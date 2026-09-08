"""Observe/act/verify runtime for the evidence-derived outbound route.

This module cannot resize a window or interact with inventory. A stage pass is
not full-route success. The backend owns cancellation and native input guards.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class RouteFrame:
    frame_id: int
    captured_monotonic_s: float
    image: Any
    window: dict[str, Any]
    evidence_path: str


class RouteBackend(Protocol):
    def capture(self, label: str) -> RouteFrame: ...
    def click(self, frame: RouteFrame, point: tuple[int, int], geometry: Any) -> None: ...
    def wait(self, seconds: float) -> None: ...
    def now(self) -> float: ...
    def check_cancelled(self) -> None: ...


@dataclass(frozen=True)
class RouteLimits:
    timeout_s: float = 300.0
    waypoint_timeout_s: float = 30.0
    observation_interval_s: float = 0.35
    # Live full-inventory departure measured10.0045 with0.1075px fit residual.
    # Bound only startup sampling noise; later waypoint tolerances stay exact.
    start_tolerance: float = 10.25
    corridor_tolerance: float = 12.0
    stationary_tolerance: float = 1.3
    stable_observations: int = 2
    localization_rechecks: int = 3
    max_clicks_per_waypoint: int = 3
    maximum_target_distance: float = 72.0

    def __post_init__(self) -> None:
        for value in asdict(self).values():
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError("Route limits must be finite positive numbers")
        if (
            self.stable_observations < 2
            or self.max_clicks_per_waypoint > 3
            or self.maximum_target_distance > 72
        ):
            raise ValueError("Route limits exceed the reviewed safety envelope")


@dataclass
class RouteResult:
    status: str = "STOP"
    success: bool = False
    stop_reason: str = "not_started"
    completed_checkpoints: list[str] = field(default_factory=list)
    click_count: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)
    start_window: dict[str, Any] | None = None
    end_window: dict[str, Any] | None = None
    bank_interface_opened: bool = False
    item_actions: int = 0


def point_segment_distance(
    point: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    if length2 == 0:
        return math.dist(point, a)
    t = max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length2))
    return math.dist(point, (a[0] + t * dx, a[1] + t * dy))


def run_route(
    backend: RouteBackend,
    route: Any,
    *,
    limits: RouteLimits | None = None,
    stop_after: int | None = None,
) -> RouteResult:
    """Run only this route in order, from a freshly localized mine start.

    ``stop_after=1`` is the first program-owned movement pilot. It never returns
    ``success=True``. Small corrections require a fresh, precise localization,
    the observed route corridor, stationarity, and the same native window.
    """
    limits = limits or RouteLimits()
    if stop_after is not None and (
        isinstance(stop_after, bool) or not isinstance(stop_after, int) or stop_after < 1
    ):
        raise ValueError("stop_after must be a positive transition count")
    result = RouteResult()
    begun = backend.now()
    last_frame_id = 0
    last_timestamp = -1.0
    previous_target: tuple[float, float] | None = None
    last_frame: RouteFrame | None = None
    try:
        for index, waypoint in enumerate(route.waypoints):
            waypoint_begun = backend.now()
            stable = arrived = misses = attempts = 0
            previous_target = None
            previous_map_geometry = None
            last_click_distance: float | None = None
            for _ in range(300):
                backend.check_cancelled()
                now = backend.now()
                if now - begun > limits.timeout_s:
                    raise RuntimeError("route_timeout")
                if now - waypoint_begun > limits.waypoint_timeout_s:
                    raise RuntimeError(f"waypoint_timeout:{waypoint.name}")
                frame = backend.capture(f"{index:02d}-{waypoint.name}")
                last_frame = frame
                if frame.frame_id <= last_frame_id or frame.captured_monotonic_s <= last_timestamp:
                    raise RuntimeError("stale_or_replayed_frame")
                last_frame_id, last_timestamp = frame.frame_id, frame.captured_monotonic_s
                result.start_window = result.start_window or frame.window
                result.end_window = frame.window
                if result.start_window != frame.window:
                    raise RuntimeError("window_identity_or_geometry_changed")
                try:
                    geometry, registration = route.observe(frame.image, waypoint)
                except route.localization_error as exc:
                    result.events.append(
                        {
                            "kind": "localization_recheck",
                            "waypoint": waypoint.name,
                            "frame_id": frame.frame_id,
                            "reason": str(exc),
                        }
                    )
                    misses += 1
                    stable = arrived = 0
                    previous_target = None
                    if misses >= limits.localization_rechecks:
                        raise RuntimeError(f"localization_unproven:{waypoint.name}:{exc}") from exc
                    backend.wait(limits.observation_interval_s)
                    continue
                misses = 0
                if not route.verify_health(frame.image, geometry):
                    raise RuntimeError("healthy_display_unproven")
                if not math.isfinite(registration.distance):
                    raise RuntimeError("nonfinite_localization")
                current_geometry = (geometry.x, geometry.y, geometry.radius)
                if (
                    previous_map_geometry is not None
                    and math.dist(current_geometry, previous_map_geometry) > 2.0
                ):
                    raise RuntimeError("minimap_layout_changed")
                previous_map_geometry = current_geometry
                stationary = (
                    previous_target is not None
                    and math.dist(previous_target, registration.target)
                    <= limits.stationary_tolerance
                )
                stable = stable + 1 if stationary else 0
                previous_target = registration.target
                tolerance = limits.start_tolerance if index == 0 else waypoint.tolerance
                result.events.append(
                    {
                        "kind": "observation",
                        "waypoint": waypoint.name,
                        "frame_id": frame.frame_id,
                        "image": frame.evidence_path,
                        "registration": asdict(registration),
                        "minimap": asdict(geometry),
                        "stationary": stationary,
                    }
                )
                if index == 0 and registration.distance > limits.start_tolerance:
                    raise RuntimeError("not_at_recorded_mine_start")
                if registration.distance <= tolerance:
                    arrived += 1
                    if arrived >= limits.stable_observations and stationary:
                        if index == len(route.waypoints) - 1:
                            proof = route.verify_endpoint(frame.image, geometry)
                            result.events.append(
                                {
                                    "kind": "bank_counter_visual_proof",
                                    "proof": proof,
                                    "frame_id": frame.frame_id,
                                }
                            )
                            if not proof.get("accepted", False):
                                raise RuntimeError("bank_counter_not_visually_confirmed")
                        result.completed_checkpoints.append(waypoint.name)
                        result.events.append(
                            {
                                "kind": "checkpoint_verified",
                                "waypoint": waypoint.name,
                                "frame_id": frame.frame_id,
                            }
                        )
                        break
                else:
                    arrived = 0
                if (
                    index != 0
                    and registration.distance > tolerance
                    and stable >= limits.stable_observations
                ):
                    if stop_after == 1 and result.click_count >= 1:
                        raise RuntimeError("single_click_pilot_arrival_not_proven")
                    # The previous recorded place and next recorded place bound
                    # the route corridor. Off-route wandering is not recovery.
                    _, origin = route.observe(frame.image, route.waypoints[index - 1])
                    corridor = point_segment_distance(
                        route.centre, origin.target, registration.target
                    )
                    if corridor > limits.corridor_tolerance:
                        raise RuntimeError(f"outside_recorded_route_corridor:{corridor:.2f}")
                    if registration.distance > limits.maximum_target_distance:
                        raise RuntimeError("next_waypoint_outside_bounded_minimap_reach")
                    if attempts >= limits.max_clicks_per_waypoint:
                        raise RuntimeError("bounded_correction_exhausted")
                    if (
                        attempts > 1
                        and last_click_distance is not None
                        and last_click_distance - registration.distance < 1.5
                    ):
                        raise RuntimeError("repeated_no_progress")
                    point = geometry.screen_point(registration.target)
                    if not geometry.contains(point):
                        raise RuntimeError("target_outside_safe_minimap")
                    backend.click(frame, point, geometry)
                    result.click_count += 1
                    result.events.append(
                        {
                            "kind": "walking_click_dispatched",
                            "waypoint": waypoint.name,
                            "frame_id": frame.frame_id,
                            "point": list(point),
                            "attempt": attempts + 1,
                            "expected_distance": registration.distance,
                            "corridor_distance": corridor,
                        }
                    )
                    last_click_distance = registration.distance
                    attempts += 1
                    stable = 0
                    previous_target = None
                backend.wait(limits.observation_interval_s)
            else:
                raise RuntimeError("observation_budget_exhausted")
            if stop_after is not None and index >= stop_after and index < len(route.waypoints) - 1:
                result.status = "STAGE_PASS"
                result.stop_reason = "requested_stage_complete_full_route_not_tested"
                return result
        result.success = True
        result.status = "PASS"
        result.stop_reason = "bank_counter_arrival_verified"
    except Exception as exc:
        result.click_count = max(result.click_count, getattr(backend, "delivered_click_count", 0))
        receipt = getattr(backend, "last_dispatch_receipt", None)
        if receipt is not None:
            result.events.append({"kind": "last_input_receipt", "receipt": receipt})
        result.stop_reason = f"{type(exc).__name__}:{exc}"
        result.events.append(
            {
                "kind": "stop",
                "reason": result.stop_reason,
                "last_image": None if last_frame is None else last_frame.evidence_path,
            }
        )
    return result
