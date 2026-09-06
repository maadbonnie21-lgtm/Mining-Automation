"""Fail-closed PREP -> READY orchestration for the supported RuneLite mining view.

This module owns preparation state only. It cannot mine, navigate, bank, move items,
or grant perception release authority. A platform adapter supplies bounded setup
operations and measured perception results; this controller decides only whether the
starting client is READY for a separately authorized mining-only run.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Literal, Protocol, final

EXPECTED_CLIENT_WIDTH: Final[int] = 1005
EXPECTED_CLIENT_HEIGHT: Final[int] = 1078
EXPECTED_CLIENT_DPI: Final[int] = 96
INVENTORY_CAPACITY: Final[int] = 28
INVENTORY_PUBLICATION_FLOOR: Final[float] = 0.8
RESOURCE_LANDMARK_DISTANCE_THRESHOLD: Final[float] = 0.12
RESOURCE_LANDMARK_COUNT: Final[int] = 6
RESOURCE_LANDMARK_QUORUM: Final[int] = 5
RESOURCE_REQUIRED_ZONE_COUNT: Final[int] = 3
RESOURCE_REQUIRED_ZONES: Final[frozenset[str]] = frozenset(
    ("north_west", "north_east", "south_west")
)
PREP_CONFIRMATION: Final[str] = "PREP_RUNELITE_FOR_MINING"
PREP_SCHEMA_VERSION: Final[int] = 1
SESSION_RECOVERY_POLL_SECONDS: Final[float] = 0.5
SESSION_RECOVERY_POLL_ATTEMPTS: Final[int] = 20
_GIT_SHA_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{40}\Z")
_SHA256_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}\Z")


class PrepMode(StrEnum):
    READ_ONLY = "read_only"
    APPLY = "apply"


class PrepStopReason(StrEnum):
    NONE = "none"
    WINDOW_NOT_FOUND = "window_not_found"
    WINDOW_AMBIGUOUS = "window_ambiguous"
    WINDOW_IDENTITY_CHANGED = "window_identity_changed"
    WINDOW_NOT_VISIBLE = "window_not_visible"
    WINDOW_MINIMIZED = "window_minimized"
    WINDOW_NOT_FOREGROUND = "window_not_foreground"
    CLIENT_GEOMETRY_MISMATCH = "client_geometry_mismatch"
    DPI_MISMATCH = "dpi_mismatch"
    POSE_REFERENCES_INVALID = "pose_references_invalid"
    DIRTY_CHECKOUT = "dirty_checkout"
    PREP_CONFIRMATION_REQUIRED = "prep_confirmation_required"
    WINDOW_RESTORE_FAILED = "window_restore_failed"
    CLIENT_RESIZE_FAILED = "client_resize_failed"
    WINDOW_FOCUS_FAILED = "window_focus_failed"
    NEUTRAL_CURSOR_FAILED = "neutral_cursor_failed"
    GAMEPLAY_CHROME_MISMATCH = "gameplay_chrome_mismatch"
    SESSION_RECOVERY_FAILED = "session_recovery_failed"
    INVENTORY_UNKNOWN = "inventory_unknown"
    INVENTORY_CONFIDENCE_BELOW_FLOOR = "inventory_confidence_below_floor"
    RESOURCE_SCENE_UNSUPPORTED = "resource_scene_unsupported"
    CAMERA_RECEIPT_INCOMPLETE = "camera_receipt_incomplete"
    CAMERA_INPUT_REJECTED = "camera_input_rejected"
    CAMERA_SEARCH_EXHAUSTED = "camera_search_exhausted"
    INPUT_STATE_UNSAFE = "input_state_unsafe"
    CLEANUP_FAILED = "cleanup_failed"
    BACKEND_ERROR = "backend_error"


class PrepCameraStep(StrEnum):
    """Small measured search steps retained from the 2026-09-03 live diagnosis."""

    PITCH_DOWN_100MS = "pitch_down_100ms"
    PITCH_UP_50MS = "pitch_up_50ms"
    WHEEL_POSITIVE_1 = "wheel_positive_1"
    WHEEL_NEGATIVE_1 = "wheel_negative_1"


# 2026-09-04 independent audit: none of the retained zoom/pitch/manual-restoration
# trials restored the frozen Resource gate. The working session came from retained
# current-view calibration/pose references. Therefore production PREP sends zero
# camera input today. The typed camera steps remain injectable for focused testing
# and future explicitly reviewed evidence, but they are not a default search recipe.
DEFAULT_CAMERA_SEARCH_STEPS: Final[tuple[PrepCameraStep, ...]] = ()


@final
@dataclass(frozen=True, slots=True)
class PrepWindowIdentity:
    process_id: int
    thread_id: int
    class_name: str
    title: str

    def __post_init__(self) -> None:
        if self.process_id <= 0 or self.thread_id <= 0:
            raise ValueError("window process/thread identity must be positive")
        if not self.class_name or not self.title:
            raise ValueError("window class/title identity must be non-empty")


@final
@dataclass(frozen=True, slots=True)
class PrepWindowSnapshot:
    hwnd: int
    identity: PrepWindowIdentity
    visible: bool
    minimized: bool
    foreground: bool
    client_width: int
    client_height: int
    dpi: int

    def __post_init__(self) -> None:
        if self.hwnd <= 0:
            raise ValueError("hwnd must be positive")
        if self.client_width < 0 or self.client_height < 0:
            raise ValueError("client dimensions must be non-negative")
        if self.dpi <= 0:
            raise ValueError("dpi must be positive")

    @property
    def exact_geometry(self) -> bool:
        return (
            self.client_width == EXPECTED_CLIENT_WIDTH
            and self.client_height == EXPECTED_CLIENT_HEIGHT
        )


@final
@dataclass(frozen=True, slots=True)
class PrepPoseReferenceReceipt:
    pose_id: str
    relative_path: str
    sha256: str
    byte_count: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if not self.pose_id or not self.relative_path:
            raise ValueError("pose reference identity must be non-empty")
        if _SHA256_RE.fullmatch(self.sha256) is None:
            raise ValueError("pose reference SHA-256 must be exact lowercase hex")
        if self.byte_count != EXPECTED_CLIENT_WIDTH * EXPECTED_CLIENT_HEIGHT * 4:
            raise ValueError("pose reference byte count does not match 1005x1078 BGRA")
        if (self.width, self.height) != (
            EXPECTED_CLIENT_WIDTH,
            EXPECTED_CLIENT_HEIGHT,
        ):
            raise ValueError("pose reference geometry changed")


@final
@dataclass(frozen=True, slots=True)
class PrepActionReceipt:
    action: str
    requested_events: int
    completed_events: int
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.action:
            raise ValueError("prep action name must be non-empty")
        if self.requested_events < 0 or self.completed_events < 0:
            raise ValueError("prep event counts must be non-negative")
        if self.completed_events > self.requested_events:
            raise ValueError("completed prep events cannot exceed requested events")

    @property
    def complete(self) -> bool:
        return self.completed_events == self.requested_events


@final
@dataclass(frozen=True, slots=True)
class PrepSceneObservation:
    frame_id: int
    frame_sha256: str
    gameplay_ready: bool
    gameplay_reason: str
    inventory_occupied: int | None
    inventory_confidence: float
    inventory_unknown_reason: str | None
    resource_supported: bool
    resource_view: str
    accepted_pose_id: str | None
    software_registration_identity: str | None
    matched_landmarks: int
    matched_zones: tuple[str, ...]
    landmark_distances: tuple[tuple[str, float], ...]
    diagnostic_score: float | None = None
    frame_path: str | None = None
    session_recovery_ready: bool = False
    session_recovery_stage: str | None = None

    def __post_init__(self) -> None:
        if self.frame_id < 0:
            raise ValueError("frame_id must be non-negative")
        if _SHA256_RE.fullmatch(self.frame_sha256) is None:
            raise ValueError("frame_sha256 must be exact lowercase hex")
        if self.inventory_occupied is not None and not (
            0 <= self.inventory_occupied <= INVENTORY_CAPACITY
        ):
            raise ValueError("Inventory occupancy must remain in 0..28")
        if not 0.0 <= self.inventory_confidence <= 1.0:
            raise ValueError("Inventory confidence must remain in [0, 1]")
        if not 0 <= self.matched_landmarks <= RESOURCE_LANDMARK_COUNT:
            raise ValueError("matched landmark count is outside the frozen ensemble")
        if len(set(self.matched_zones)) != len(self.matched_zones):
            raise ValueError("matched zones must be unique")
        if not isinstance(self.session_recovery_ready, bool):
            raise ValueError("session_recovery_ready must be a boolean")
        if self.session_recovery_ready != (self.session_recovery_stage is not None):
            raise ValueError(
                "session recovery readiness must agree with its exact stage identity"
            )

    @property
    def frozen_resource_gate_passed(self) -> bool:
        # READY is never delegated to a diagnostic score or an adapter assertion.
        # Re-check all six retained landmark distances at the unchanged 0.12 ceiling
        # and require the exact three macro zones used by the released Resource gate.
        if len(self.landmark_distances) != RESOURCE_LANDMARK_COUNT:
            return False
        if len({name for name, _ in self.landmark_distances}) != RESOURCE_LANDMARK_COUNT:
            return False
        within_threshold = sum(
            distance <= RESOURCE_LANDMARK_DISTANCE_THRESHOLD
            for _, distance in self.landmark_distances
        )
        return (
            self.resource_supported
            and self.matched_landmarks >= RESOURCE_LANDMARK_QUORUM
            and within_threshold >= RESOURCE_LANDMARK_QUORUM
            and frozenset(self.matched_zones) == RESOURCE_REQUIRED_ZONES
        )


class PrepOperationError(RuntimeError):
    """Typed fail-closed platform/adapter veto."""

    def __init__(self, reason: PrepStopReason, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


class PrepBackend(Protocol):
    """Narrow setup/perception seam. No mining/navigation/banking methods exist."""

    def snapshot(self) -> PrepWindowSnapshot:
        ...

    def verify_pose_references(self) -> tuple[PrepPoseReferenceReceipt, ...]:
        ...

    def restore_window(self) -> PrepActionReceipt:
        ...

    def resize_client(self, width: int, height: int) -> PrepActionReceipt:
        ...

    def focus_window(self) -> PrepActionReceipt:
        ...

    def neutralize_cursor(self) -> PrepActionReceipt:
        ...

    def recover_session(self, stage: str) -> PrepActionReceipt:
        ...

    def observe(self) -> PrepSceneObservation:
        ...

    def camera_action(self, step: PrepCameraStep) -> tuple[PrepActionReceipt, ...]:
        ...

    def cleanup(self) -> tuple[PrepActionReceipt, ...]:
        ...


@final
@dataclass(frozen=True, slots=True)
class RunelitePrepResult:
    schema_version: int
    mode: PrepMode
    git_sha: str
    prep_session_id: str
    started_monotonic_s: float
    ended_monotonic_s: float
    initial_window: PrepWindowSnapshot | None
    final_window: PrepWindowSnapshot | None
    pose_references: tuple[PrepPoseReferenceReceipt, ...]
    observations: tuple[PrepSceneObservation, ...]
    actions: tuple[PrepActionReceipt, ...]
    ready_for_mining: bool
    stop_reason: PrepStopReason
    detail: str
    resource_threshold: float = RESOURCE_LANDMARK_DISTANCE_THRESHOLD
    resource_landmark_count: int = RESOURCE_LANDMARK_COUNT
    resource_landmark_quorum: int = RESOURCE_LANDMARK_QUORUM
    resource_required_zone_count: int = RESOURCE_REQUIRED_ZONE_COUNT
    inventory_floor: float = INVENTORY_PUBLICATION_FLOOR
    inventory_capacity: int = INVENTORY_CAPACITY
    prep_authority_relinquished: bool = True
    mining_input_authority: Literal[False] = field(default=False, init=False)
    navigation_authority: Literal[False] = field(default=False, init=False)
    banking_authority: Literal[False] = field(default=False, init=False)
    inventory_release_authority: Literal[False] = field(default=False, init=False)
    resource_release_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        if self.schema_version != PREP_SCHEMA_VERSION:
            raise ValueError("unexpected PREP receipt schema version")
        if _GIT_SHA_RE.fullmatch(self.git_sha) is None:
            raise ValueError("git_sha must be an exact lowercase 40-character SHA")
        if not self.prep_session_id:
            raise ValueError("prep_session_id must be non-empty")
        if self.ended_monotonic_s < self.started_monotonic_s:
            raise ValueError("PREP end time precedes start time")
        if self.ready_for_mining is not (self.stop_reason is PrepStopReason.NONE):
            raise ValueError("READY boolean must agree with the exact stop reason")
        if self.ready_for_mining:
            if self.final_window is None or not _window_ready(self.final_window):
                raise ValueError("READY requires an exact final window snapshot")
            if not self.observations or not _observation_ready(self.observations[-1]):
                raise ValueError("READY requires a final clean perception observation")
        if (
            self.resource_threshold != RESOURCE_LANDMARK_DISTANCE_THRESHOLD
            or self.resource_landmark_count != RESOURCE_LANDMARK_COUNT
            or self.resource_landmark_quorum != RESOURCE_LANDMARK_QUORUM
            or self.resource_required_zone_count != RESOURCE_REQUIRED_ZONE_COUNT
            or self.inventory_floor != INVENTORY_PUBLICATION_FLOOR
            or self.inventory_capacity != INVENTORY_CAPACITY
        ):
            raise ValueError("PREP receipt weakens a frozen mining invariant")
        if not self.prep_authority_relinquished:
            raise ValueError("PREP receipt cannot retain setup authority after return")


def _window_ready(snapshot: PrepWindowSnapshot) -> bool:
    return (
        snapshot.visible
        and not snapshot.minimized
        and snapshot.foreground
        and snapshot.exact_geometry
        and snapshot.dpi == EXPECTED_CLIENT_DPI
    )


def _observation_ready(observation: PrepSceneObservation) -> bool:
    return (
        observation.gameplay_ready
        and observation.inventory_occupied is not None
        and observation.inventory_confidence >= INVENTORY_PUBLICATION_FLOOR
        and observation.frozen_resource_gate_passed
    )


def _validate_same_identity(
    initial: PrepWindowSnapshot,
    current: PrepWindowSnapshot,
) -> None:
    if current.hwnd != initial.hwnd or current.identity != initial.identity:
        raise PrepOperationError(
            PrepStopReason.WINDOW_IDENTITY_CHANGED,
            "RuneLite HWND/process/thread/class/title identity changed during PREP.",
        )


def _require_complete(
    receipt: PrepActionReceipt,
    reason: PrepStopReason,
) -> None:
    if not receipt.complete:
        raise PrepOperationError(
            reason,
            f"Prep action {receipt.action!r} completed "
            f"{receipt.completed_events}/{receipt.requested_events} low-level events.",
        )


def _observation_stop(observation: PrepSceneObservation) -> PrepOperationError | None:
    if not observation.gameplay_ready:
        return PrepOperationError(
            PrepStopReason.GAMEPLAY_CHROME_MISMATCH,
            observation.gameplay_reason,
        )
    if observation.inventory_occupied is None:
        detail = observation.inventory_unknown_reason or (
            "Inventory is UNKNOWN; open the Inventory tab, keep it unobstructed, "
            "and rerun PREP."
        )
        return PrepOperationError(PrepStopReason.INVENTORY_UNKNOWN, detail)
    if observation.inventory_confidence < INVENTORY_PUBLICATION_FLOOR:
        return PrepOperationError(
            PrepStopReason.INVENTORY_CONFIDENCE_BELOW_FLOOR,
            "Inventory confidence is below the unchanged 0.8 publication floor.",
        )
    return None


def run_runelite_prep(
    backend: PrepBackend,
    *,
    mode: PrepMode,
    git_sha: str,
    prep_session_id: str,
    confirm: str | None = None,
    camera_steps: tuple[PrepCameraStep, ...] = DEFAULT_CAMERA_SEARCH_STEPS,
    session_recovery_sleeper: Callable[[float], None] = time.sleep,
) -> RunelitePrepResult:
    """Diagnose or prepare one RuneLite starting state and then relinquish authority."""

    if _GIT_SHA_RE.fullmatch(git_sha) is None:
        raise ValueError("git_sha must be an exact lowercase 40-character SHA")
    if not prep_session_id:
        raise ValueError("prep_session_id must be non-empty")
    if type(camera_steps) is not tuple:
        raise TypeError("camera_steps must be an exact tuple")

    started = time.monotonic()
    initial: PrepWindowSnapshot | None = None
    final: PrepWindowSnapshot | None = None
    pose_references: tuple[PrepPoseReferenceReceipt, ...] = ()
    observations: list[PrepSceneObservation] = []
    actions: list[PrepActionReceipt] = []
    ready = False
    stop_reason = PrepStopReason.BACKEND_ERROR
    detail = "PREP did not reach a terminal verdict."

    try:
        initial = backend.snapshot()
        final = initial
        pose_references = backend.verify_pose_references()
        if len(pose_references) != 3:
            raise PrepOperationError(
                PrepStopReason.POSE_REFERENCES_INVALID,
                "PREP requires all three preserved local successful pose references.",
            )
        if initial.dpi != EXPECTED_CLIENT_DPI:
            raise PrepOperationError(
                PrepStopReason.DPI_MISMATCH,
                f"RuneLite DPI is {initial.dpi}; expected exact {EXPECTED_CLIENT_DPI}.",
            )

        if mode is PrepMode.READ_ONLY:
            if not initial.visible:
                raise PrepOperationError(
                    PrepStopReason.WINDOW_NOT_VISIBLE,
                    "RuneLite is not visible; read-only PREP sends no repair input.",
                )
            if initial.minimized:
                raise PrepOperationError(
                    PrepStopReason.WINDOW_MINIMIZED,
                    "RuneLite is minimized; rerun with explicit --apply to restore it.",
                )
            if not initial.exact_geometry:
                raise PrepOperationError(
                    PrepStopReason.CLIENT_GEOMETRY_MISMATCH,
                    f"RuneLite client is {initial.client_width}x{initial.client_height}; "
                    f"expected exact {EXPECTED_CLIENT_WIDTH}x{EXPECTED_CLIENT_HEIGHT}.",
                )
            observation = backend.observe()
            observations.append(observation)
            failure = _observation_stop(observation)
            if failure is not None:
                raise failure
            if not initial.foreground:
                raise PrepOperationError(
                    PrepStopReason.WINDOW_NOT_FOREGROUND,
                    "RuneLite is not foreground; read-only PREP will not activate it.",
                )
            if not observation.frozen_resource_gate_passed:
                raise PrepOperationError(
                    PrepStopReason.RESOURCE_SCENE_UNSUPPORTED,
                    "Current view does not satisfy unchanged Resource 0.12 / 5-of-6 / "
                    "all-3-zone readiness.",
                )
            ready = True
            stop_reason = PrepStopReason.NONE
            detail = "READY FOR MINING; read-only diagnose sent zero setup input."
        else:
            if confirm != PREP_CONFIRMATION:
                raise PrepOperationError(
                    PrepStopReason.PREP_CONFIRMATION_REQUIRED,
                    f"Apply mode requires exact confirmation {PREP_CONFIRMATION!r}.",
                )
            if not final.visible:
                raise PrepOperationError(
                    PrepStopReason.WINDOW_NOT_VISIBLE,
                    "RuneLite is not visible; PREP will not operate on a hidden window.",
                )
            if final.minimized:
                receipt = backend.restore_window()
                actions.append(receipt)
                _require_complete(receipt, PrepStopReason.WINDOW_RESTORE_FAILED)
                final = backend.snapshot()
                _validate_same_identity(initial, final)
                if final.minimized or not final.visible:
                    raise PrepOperationError(
                        PrepStopReason.WINDOW_RESTORE_FAILED,
                        "RuneLite remained minimized/hidden after the bounded restore.",
                    )

            # Focus first, then perform the final measured client-area correction.
            # Real Java/AWT evidence showed activation can change an otherwise-correct
            # 1005x1078 client to 1005x687. Resizing before focus can therefore create
            # a false terminal STOP even though one bounded post-focus correction is
            # both sufficient and within PREP authority.
            if not final.foreground:
                receipt = backend.focus_window()
                actions.append(receipt)
                _require_complete(receipt, PrepStopReason.WINDOW_FOCUS_FAILED)
                final = backend.snapshot()
                _validate_same_identity(initial, final)
                if not final.foreground:
                    raise PrepOperationError(
                        PrepStopReason.WINDOW_FOCUS_FAILED,
                        "RuneLite did not become foreground after explicit PREP focus.",
                    )

            if final.dpi != EXPECTED_CLIENT_DPI:
                raise PrepOperationError(
                    PrepStopReason.DPI_MISMATCH,
                    f"RuneLite DPI changed to {final.dpi}; expected exact "
                    f"{EXPECTED_CLIENT_DPI}.",
                )

            if not final.exact_geometry:
                receipt = backend.resize_client(
                    EXPECTED_CLIENT_WIDTH,
                    EXPECTED_CLIENT_HEIGHT,
                )
                actions.append(receipt)
                _require_complete(receipt, PrepStopReason.CLIENT_RESIZE_FAILED)
                final = backend.snapshot()
                _validate_same_identity(initial, final)
                if not final.exact_geometry:
                    raise PrepOperationError(
                        PrepStopReason.CLIENT_RESIZE_FAILED,
                        f"Client remained {final.client_width}x{final.client_height} after "
                        "bounded post-focus resize correction.",
                    )

            # Final window gate after every startup mutation. No perception is
            # evaluated until the exact rebound HWND is still foreground, 1005x1078,
            # and DPI96 after focus/restore/resize have all settled.
            final = backend.snapshot()
            _validate_same_identity(initial, final)
            if not final.foreground:
                raise PrepOperationError(
                    PrepStopReason.WINDOW_FOCUS_FAILED,
                    "RuneLite lost foreground before final PREP window verification.",
                )
            if final.dpi != EXPECTED_CLIENT_DPI:
                raise PrepOperationError(
                    PrepStopReason.DPI_MISMATCH,
                    f"RuneLite DPI changed to {final.dpi}; expected exact "
                    f"{EXPECTED_CLIENT_DPI}.",
                )
            if not final.exact_geometry:
                raise PrepOperationError(
                    PrepStopReason.CLIENT_RESIZE_FAILED,
                    "RuneLite client is not exact 1005x1078 after post-focus correction.",
                )

            neutral = backend.neutralize_cursor()
            actions.append(neutral)
            _require_complete(neutral, PrepStopReason.NEUTRAL_CURSOR_FAILED)
            observation = backend.observe()
            observations.append(observation)
            recovery_stages_seen: set[str] = set()
            while not observation.gameplay_ready and observation.session_recovery_ready:
                stage = observation.session_recovery_stage
                if stage is None or stage in recovery_stages_seen:
                    raise PrepOperationError(
                        PrepStopReason.SESSION_RECOVERY_FAILED,
                        "Session recovery refused to repeat the same reviewed stage.",
                    )
                if len(recovery_stages_seen) >= 3:
                    raise PrepOperationError(
                        PrepStopReason.SESSION_RECOVERY_FAILED,
                        "Session recovery exceeded the three reviewed re-entry stages.",
                    )
                recovery_stages_seen.add(stage)
                recovery = backend.recover_session(stage)
                actions.append(recovery)
                _require_complete(recovery, PrepStopReason.SESSION_RECOVERY_FAILED)
                next_stage: PrepSceneObservation | None = None
                recovered = False
                for _ in range(SESSION_RECOVERY_POLL_ATTEMPTS):
                    session_recovery_sleeper(SESSION_RECOVERY_POLL_SECONDS)
                    probe = backend.observe()
                    observations.append(probe)
                    if probe.gameplay_ready:
                        neutral = backend.neutralize_cursor()
                        actions.append(neutral)
                        _require_complete(
                            neutral, PrepStopReason.NEUTRAL_CURSOR_FAILED
                        )
                        observation = backend.observe()
                        observations.append(observation)
                        recovered = observation.gameplay_ready
                        break
                    if (
                        probe.session_recovery_ready
                        and probe.session_recovery_stage not in recovery_stages_seen
                    ):
                        next_stage = probe
                        break
                if recovered:
                    break
                if next_stage is not None:
                    observation = next_stage
                    continue
                raise PrepOperationError(
                    PrepStopReason.SESSION_RECOVERY_FAILED,
                    "Reviewed session-recovery input did not reach gameplay or the "
                    "next reviewed re-entry stage within the bounded passive window.",
                )
            failure = _observation_stop(observation)
            if failure is not None:
                raise failure

            if not observation.frozen_resource_gate_passed and not camera_steps:
                raise PrepOperationError(
                    PrepStopReason.RESOURCE_SCENE_UNSUPPORTED,
                    "Current view is not READY and no evidence-backed automatic camera "
                    "normalization is authorized today. Set the supported mining view "
                    "once, then rerun PREP; software registration may still validate it.",
                )

            if not observation.frozen_resource_gate_passed:
                for step in camera_steps:
                    receipts = backend.camera_action(step)
                    if not receipts:
                        raise PrepOperationError(
                            PrepStopReason.CAMERA_RECEIPT_INCOMPLETE,
                            f"Camera step {step.value!r} returned no low-level receipt.",
                        )
                    for receipt in receipts:
                        actions.append(receipt)
                        _require_complete(
                            receipt,
                            PrepStopReason.CAMERA_RECEIPT_INCOMPLETE,
                        )
                    final = backend.snapshot()
                    _validate_same_identity(initial, final)
                    if not _window_ready(final):
                        raise PrepOperationError(
                            PrepStopReason.CAMERA_INPUT_REJECTED,
                            "RuneLite window readiness changed during camera PREP.",
                        )
                    neutral = backend.neutralize_cursor()
                    actions.append(neutral)
                    _require_complete(neutral, PrepStopReason.NEUTRAL_CURSOR_FAILED)
                    observation = backend.observe()
                    observations.append(observation)
                    failure = _observation_stop(observation)
                    if failure is not None:
                        raise failure
                    if observation.frozen_resource_gate_passed:
                        break

            if not observation.frozen_resource_gate_passed:
                raise PrepOperationError(
                    PrepStopReason.CAMERA_SEARCH_EXHAUSTED,
                    "Bounded measured camera search exhausted without unchanged "
                    "0.12 / 5-of-6 / all-3-zone Resource readiness.",
                )

            final_neutral = backend.neutralize_cursor()
            actions.append(final_neutral)
            _require_complete(final_neutral, PrepStopReason.NEUTRAL_CURSOR_FAILED)
            final = backend.snapshot()
            _validate_same_identity(initial, final)
            if not _window_ready(final):
                raise PrepOperationError(
                    PrepStopReason.CAMERA_INPUT_REJECTED,
                    "Final RuneLite window state changed before READY publication.",
                )
            final_observation = backend.observe()
            observations.append(final_observation)
            failure = _observation_stop(final_observation)
            if failure is not None:
                raise failure
            if not final_observation.frozen_resource_gate_passed:
                raise PrepOperationError(
                    PrepStopReason.RESOURCE_SCENE_UNSUPPORTED,
                    "Final clean frame lost frozen Resource readiness; PREP will not "
                    "publish READY from an earlier frame.",
                )
            ready = True
            stop_reason = PrepStopReason.NONE
            detail = (
                "READY FOR MINING; PREP setup/camera authority ends with this receipt."
            )
    except PrepOperationError as exc:
        ready = False
        stop_reason = exc.reason
        detail = exc.detail
    except Exception as exc:  # noqa: BLE001 - terminal fail-closed boundary
        ready = False
        stop_reason = PrepStopReason.BACKEND_ERROR
        detail = f"PREP backend error: {type(exc).__name__}: {exc}"
    finally:
        try:
            cleanup_receipts = backend.cleanup()
            actions.extend(cleanup_receipts)
            incomplete_cleanup = next(
                (receipt for receipt in cleanup_receipts if not receipt.complete),
                None,
            )
            if incomplete_cleanup is not None:
                ready = False
                stop_reason = PrepStopReason.CLEANUP_FAILED
                detail = (
                    f"Cleanup action {incomplete_cleanup.action!r} was incomplete: "
                    f"{incomplete_cleanup.completed_events}/"
                    f"{incomplete_cleanup.requested_events}."
                )
        except Exception as exc:  # noqa: BLE001 - cleanup failure must veto READY
            ready = False
            stop_reason = PrepStopReason.CLEANUP_FAILED
            detail = f"PREP cleanup failed: {type(exc).__name__}: {exc}"
        try:
            terminal = backend.snapshot()
            if initial is not None:
                _validate_same_identity(initial, terminal)
            final = terminal
        except Exception as exc:  # noqa: BLE001 - terminal identity failure is fail-closed
            if ready:
                ready = False
                stop_reason = PrepStopReason.WINDOW_IDENTITY_CHANGED
                detail = f"Terminal RuneLite revalidation failed: {type(exc).__name__}: {exc}"

    if ready:
        stop_reason = PrepStopReason.NONE
    elif stop_reason is PrepStopReason.NONE:
        stop_reason = PrepStopReason.BACKEND_ERROR
    ended = time.monotonic()
    return RunelitePrepResult(
        schema_version=PREP_SCHEMA_VERSION,
        mode=mode,
        git_sha=git_sha,
        prep_session_id=prep_session_id,
        started_monotonic_s=started,
        ended_monotonic_s=ended,
        initial_window=initial,
        final_window=final,
        pose_references=pose_references,
        observations=tuple(observations),
        actions=tuple(actions),
        ready_for_mining=ready,
        stop_reason=stop_reason,
        detail=detail,
    )
