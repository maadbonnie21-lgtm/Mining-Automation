"""Program-owned Varrock East Bank -> mine route using reversed observed checkpoints."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

import cv2

from mining_automation.bank_vision import BankVision

from .runtime import RouteLimits, RouteResult, run_route
from .visual_route import VisualRoute, crop_minimap
from .windows import NativeRouteBackend


class ReturnRouteError(RuntimeError):
    """The current client cannot safely enter or complete the return route."""


def run_bank_to_mine(
    *,
    hwnd: int,
    title: str,
    output: Path,
    profile: Path,
    focus_existing: bool = False,
) -> RouteResult:
    """Return from the verified bank counter endpoint to the verified mine start."""
    route = VisualRoute(profile)
    backend = NativeRouteBackend(
        hwnd,
        output,
        expected_title=title,
        focus_existing=focus_existing,
        stop_file=output / "STOP",
    )

    start = backend.capture("bank-return-start")
    geometry, registration = route.observe(start.image, route.waypoints[-1])
    if registration.distance > 4.0:
        raise ReturnRouteError("bank_endpoint_minimap_unproven")

    dpi = backend.initial["dpi_environment"]
    logical = cv2.resize(
        start.image,
        None,
        fx=1 / dpi["effective_mapping_scale_x"],
        fy=1 / dpi["effective_mapping_scale_y"],
        interpolation=cv2.INTER_AREA,
    )
    vision = BankVision()
    inventory = vision.inventory(logical)
    if inventory["empty_count"] != 28 or inventory["unknown_count"] != 0:
        raise ReturnRouteError("return_requires_verified_empty_inventory")
    if vision.bank_controls(logical) is not None:
        raise ReturnRouteError("return_requires_verified_closed_bank_interface")

    mine = route.waypoints[0]

    def verify_mine_endpoint(image: Any, current_geometry: Any) -> dict[str, Any]:
        minimap = crop_minimap(image, current_geometry)
        result = route.references[mine.image_key].register(minimap)
        return {
            "accepted": result.distance <= 4.0,
            "mine_distance": result.distance,
            "authority": "bank_to_mine_arrival",
        }

    route.waypoints = list(reversed(route.waypoints))
    route.verify_endpoint = verify_mine_endpoint  # type: ignore[assignment]
    result = run_route(
        backend,
        route,
        limits=RouteLimits(start_tolerance=4.0),
    )
    if result.success:
        result.status = "PASS"
        result.stop_reason = "mine_arrival_verified"
        result.events.append(
            {
                "kind": "return_route_complete",
                "inventory_empty_verified_at_start": True,
                "endpoint": "mine_start",
                "window_unchanged": result.start_window == result.end_window,
            }
        )
    return result


def result_payload(result: RouteResult) -> dict[str, Any]:
    """Stable serialization helper for CLI/orchestrator."""
    payload = asdict(result)
    payload["direction"] = "bank_to_mine"
    payload["evidence_origin"] = "standalone_program"
    payload["operator_chose_walking_clicks"] = False
    return payload
