#!/usr/bin/env python3
"""Run repeated deterministic Varrock East camera reacquisition trials.

Development-only.  This tool is the sole composition point for Windows camera
input, production capture, frozen production perception, private artifacts,
and provenance-bound reporting.  It cannot make a diagnostic match production
definitive and it never clicks a world/resource coordinate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mining_automation.capture import (  # noqa: E402
    CaptureError,
    CaptureSource,
    Frame,
    PixelFormat,
    RawFrame,
)
from mining_automation.capture.windows import (  # noqa: E402
    DEFAULT_TITLE_SUBSTRING,
    WindowsCaptureBackend,
)
from mining_automation.perception import (  # noqa: E402
    RESOURCE_PROFILE_SCHEMA_VERSION,
    VARROCK_EAST_IRON_DETECTOR_VERSION,
    VARROCK_EAST_IRON_PROFILE_ID,
    build_varrock_east_iron_detector,
    load_varrock_east_iron_profile,
    write_resource_fixture_draft,
)
from mining_automation.perception.scene_landmarks import MacroZone  # noqa: E402
from mining_automation.validation.camera_arm_guard import (  # noqa: E402
    CameraArmGuardResult,
)
from mining_automation.validation.camera_bootstrap import (  # noqa: E402
    CameraNorthBootstrapInputState,
    CameraNorthBootstrapResult,
    CameraNorthBootstrapTerminalReason,
    run_camera_north_bootstrap,
)
from mining_automation.validation.camera_bridge_authorization import (  # noqa: E402
    CAMERA_BRIDGE_AUTHORIZATION_CAMPAIGN_ID,
    CAMERA_BRIDGE_AUTHORIZATION_ID,
    CAMERA_BRIDGE_AUTHORIZATION_VERSION,
    CameraBridgeAuthorizationEvidence,
    CameraBridgeAuthorizationReservation,
    CameraBridgeCompletionEvidence,
    authenticate_camera_bridge_authorization,
    camera_bridge_authorization_consumed,
    canonical_camera_bridge_component_sha256,
    repository_worktree_git_dir,
    reserve_camera_bridge_authorization,
    seal_camera_bridge_completion,
)
from mining_automation.validation.camera_bridge_capture import (  # noqa: E402
    CAMERA_BRIDGE_CAPTURE_HOLD_SECONDS,
    CAMERA_BRIDGE_CAPTURE_ID,
    CAMERA_BRIDGE_CAPTURE_MAXIMUM_PHYSICAL_PRIMITIVES,
    CAMERA_BRIDGE_CAPTURE_SETTLE_SECONDS,
    CAMERA_BRIDGE_CAPTURE_VERSION,
    CameraBridgeCaptureResult,
    CameraBridgeCaptureTerminalReason,
    CameraBridgePostTransitionClosure,
    CameraBridgePostTransitionStatus,
    _finalize_camera_bridge_post_production,
    camera_bridge_capture_plan,
    run_fixed_camera_bridge_capture,
)
from mining_automation.validation.camera_bridge_north_state import (  # noqa: E402
    CameraBridgeExactNorthQualification,
    qualify_exact_frozen_north_registration,
)
from mining_automation.validation.camera_bridge_planner import (  # noqa: E402
    CAMERA_BRIDGE_PLANNER_ID,
    CAMERA_BRIDGE_PLANNER_VERSION,
    FROZEN_ENDPOINT_OBJECTIVE,
    FROZEN_ENDPOINT_OBJECTIVE_ID,
    FROZEN_ENDPOINT_SOURCE_SHA256,
)
from mining_automation.validation.camera_coordinates import (  # noqa: E402
    CameraCoordinateMapping,
    require_exact_round_trip,
)
from mining_automation.validation.camera_evaluation import (  # noqa: E402
    CameraEvaluation,
    evaluate_varrock_east_camera,
)
from mining_automation.validation.camera_guidance import (  # noqa: E402
    CAMERA_GUIDANCE_ID,
    CAMERA_GUIDANCE_VERSION,
    WorldCameraGuidance,
    evaluate_varrock_east_camera_guidance,
)
from mining_automation.validation.camera_guidance_v2 import (  # noqa: E402
    CAMERA_GUIDANCE_V2_DRAG_PULSE_PIXELS,
    CAMERA_GUIDANCE_V2_ID,
    CAMERA_GUIDANCE_V2_VERSION,
    WorldCameraGuidanceV2,
)
from mining_automation.validation.camera_input_lease import (  # noqa: E402
    CameraInputLeaseError,
    WindowsCameraInputLease,
)
from mining_automation.validation.camera_plan import (  # noqa: E402
    EXPECTED_CLIENT_HEIGHT,
    EXPECTED_CLIENT_WIDTH,
    MAX_CAMERA_DRAG_PIXELS,
    MAX_CAMERA_DRAG_STEP_PIXELS,
    MAX_CAMERA_WHEEL_DETENTS,
    MAX_KEY_HOLD_SECONDS,
    REVIEWED_CAMERA_DRAG_OPEN_VIEWPORT,
    REVIEWED_CAMERA_DRAG_POINT,
    REVIEWED_CAMERA_WHEEL_POINT,
    REVIEWED_COMPASS_POINT,
    CameraAction,
    CameraDragAxis,
    CameraHoldKey,
    CameraInputReceipt,
    CameraKeyHold,
    CameraMiddleDrag,
    CameraPause,
    CameraPlan,
    CameraPlanError,
    CameraPlanReceipt,
    CameraWheel,
    CompassClick,
    ResetZoomKey,
    camera_drag_path,
)
from mining_automation.validation.camera_report import (  # noqa: E402
    CameraReportProvenance,
    write_camera_validation_report,
)
from mining_automation.validation.camera_servo import (  # noqa: E402
    CameraServoArmAgeEvidence,
    CameraServoExceptionEvidence,
    CameraServoFrameEvidence,
)
from mining_automation.validation.camera_session import (  # noqa: E402
    CameraFrameArtifact,
    CameraFrameRecord,
    CameraNormalizationResult,
    CameraSessionResult,
    run_camera_validation_session,
)
from mining_automation.validation.camera_system_id import (  # noqa: E402
    CAMERA_SYSTEM_ID_DRAG_PIXELS,
    CAMERA_SYSTEM_ID_ID,
    CAMERA_SYSTEM_ID_SETTLE_SECONDS,
    CAMERA_SYSTEM_ID_VERSION,
    CameraSystemIdAxisResult,
    CameraSystemIdComparison,
    CameraSystemIdLandmarkComparison,
    CameraSystemIdObservation,
    CameraSystemIdResult,
    CameraSystemIdStepResult,
    run_fixed_camera_system_identification,
)
from mining_automation.validation.client_readiness import (  # noqa: E402
    ClientInputReadiness,
    evaluate_client_input_readiness,
)
from mining_automation.validation.robust_registration import (  # noqa: E402
    RobustRegistrationEngine,
    RobustWorldRegistration,
)
from mining_automation.validation.robust_view_graph import (  # noqa: E402
    ROBUST_VIEW_GRAPH_ID,
    ROBUST_VIEW_GRAPH_VERSION,
)
from mining_automation.validation.windows_camera import (  # noqa: E402
    CAMERA_DRAG_STEP_INTERVAL_SECONDS,
    CAMERA_KEY_RELEASE_SETTLE_SECONDS,
    CAMERA_MIDDLE_ARMING_SETTLE_SECONDS,
    CAMERA_MIDDLE_RELEASE_SETTLE_SECONDS,
    CAMERA_WHEEL_EVENT_INTERVAL_SECONDS,
    COMPASS_CLICK_DWELL_SECONDS,
    RealWindowsCameraApi,
    WindowsCameraControl,
    WindowsCameraError,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_COMPASS_POINT = REVIEWED_COMPASS_POINT
_CAMERA_WHEEL_POINT = REVIEWED_CAMERA_WHEEL_POINT


def _drag_open_viewport_dict() -> dict[str, int]:
    left, top, right, bottom = REVIEWED_CAMERA_DRAG_OPEN_VIEWPORT
    return {
        "left": left,
        "top": top,
        "right_exclusive": right,
        "bottom_exclusive": bottom,
    }


@dataclass(slots=True)
class _ReportPublicationState:
    published_by_this_invocation: bool = False
    pending_bridge_completion: _PendingBridgeCompletion | None = None


@dataclass(frozen=True, slots=True)
class _PendingBridgeCompletion:
    """Completion evidence that cannot be sealed until the lease exits cleanly."""

    git_head_sha: str
    reservation: CameraBridgeAuthorizationReservation
    evidence: CameraBridgeCompletionEvidence


@dataclass(frozen=True, slots=True)
class _BridgeAnalysisEvidence:
    """Authenticated read-only R2 analysis; never camera-input authority."""

    report_path: Path
    report_sha256: str
    r1_report_sha256: str
    planner_id: str
    planner_version: str
    objective_id: str
    source_frame: Frame
    source_raw_path: Path
    source_sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "objective_id": self.objective_id,
            "planner_id": self.planner_id,
            "planner_version": self.planner_version,
            "r1_report_sha256": self.r1_report_sha256,
            "report_path": _display_repo_path(self.report_path),
            "report_sha256": self.report_sha256,
            "source_raw_path": _display_repo_path(self.source_raw_path),
            "source_sha256": self.source_sha256,
        }


@dataclass(frozen=True, slots=True)
class _BridgeCampaignPrecursor:
    """Same-process north-state evidence preceding the fixed Right bridge."""

    mode: str
    frame: Frame
    frame_evidence: CameraServoFrameEvidence
    registration: RobustWorldRegistration
    north_qualification: CameraBridgeExactNorthQualification | None
    bootstrap: CameraNorthBootstrapResult | None
    window_hwnd: int
    window_process_id: int
    window_thread_id: int
    window_class_name: str
    window_title_sha256: str

    def __post_init__(self) -> None:
        if self.mode not in ("compass_click", "zero_click"):
            raise ValueError("R2.3 precursor mode is invalid")
        if (self.bootstrap is None) is (self.mode == "compass_click"):
            raise ValueError("R2.3 precursor mode and bootstrap evidence disagree")
        if (self.north_qualification is None) != (self.mode == "compass_click"):
            raise ValueError(
                "R2.3 zero-click mode requires exact frozen-north qualification"
            )
        payload_sha256 = hashlib.sha256(self.frame.payload).hexdigest()
        if (
            self.frame_evidence.artifact.raw_sha256 != payload_sha256
            or self.registration.target.payload_sha256 != payload_sha256
            or not self.window_class_name
            or not self.window_title_sha256
        ):
            raise ValueError("R2.3 precursor does not bind its exact frame/window")

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "physical_primitive_count": 1 if self.mode == "compass_click" else 0,
            "captured_monotonic_s": self.frame.captured_monotonic_s,
            "frame_id": self.frame.frame_id,
            "frame": _bootstrap_frame_dict(self.frame_evidence),
            "raw_sha256": hashlib.sha256(self.frame.payload).hexdigest(),
            "bootstrap": (
                None
                if self.bootstrap is None
                else _bootstrap_result_dict(
                    self.bootstrap,
                    tracked_worktree_clean=True,
                )
            ),
            "source_to_precursor_registration": self.registration.as_dict(),
            "zero_click_north_qualification": (
                None
                if self.north_qualification is None
                else self.north_qualification.as_dict()
            ),
            "embedded_same_process_and_input_lease": True,
            "external_north_report_accepted": False,
            "registration_can_authorize_input_alone": False,
            "production_remains_sole_scene_authority": True,
            "window_binding": {
                "class_name": self.window_class_name,
                "hwnd": self.window_hwnd,
                "process_id": self.window_process_id,
                "thread_id": self.window_thread_id,
                "title_sha256": self.window_title_sha256,
            },
        }


@dataclass(frozen=True, slots=True)
class _BridgeNorthHandoff:
    """Legacy standalone north report shape; never accepted for R2.3."""

    report_path: Path
    report_sha256: str
    frame: Frame
    raw_path: Path
    window_hwnd: int
    window_process_id: int
    window_thread_id: int
    window_class_name: str
    window_title_sha256: str


@dataclass(frozen=True, slots=True)
class _BridgePointerEvidence:
    mapping: CameraCoordinateMapping
    root_hwnd: int

    def as_dict(self) -> dict[str, object]:
        return {
            "exact_round_trip": self.mapping.exact_round_trip,
            "logical_client": list(self.mapping.logical_client.pair),
            "physical_screen": list(self.mapping.physical_screen.pair),
            "reverse_logical_client": list(
                self.mapping.reverse_logical_client.pair
            ),
            "root_hwnd_matches_target": self.root_hwnd == self.mapping.hwnd,
        }


_MINIMUM_SATURATION_DETENTS = 80
_DEFAULT_PITCH_HOLD_S = 3.0
_DEFAULT_PERTURB_HOLD_S = 0.75
_DEFAULT_POST_COMPASS_SETTLE_S = 0.5
_CASE_PREFIX_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SINGLE_PLAN_STRATEGY = "single-plan"
_PRODUCTION_GATED_STRATEGY_ID = "varrock-east-production-gated-search-v1"
_PRODUCTION_GATED_STRATEGY_VERSION = "1.0.0"
_PRODUCTION_GATED_ZOOM_SATURATION = 96
_PRODUCTION_GATED_ZOOM_OFFSET = -17
_PRODUCTION_GATED_CANDIDATE_OFFSETS: tuple[tuple[float, float], ...] = (
    (0.60, 0.05),
    (0.58, 0.05),
    (0.62, 0.05),
    (0.56, 0.05),
    (0.64, 0.05),
    (0.60, 0.04),
    (0.58, 0.04),
    (0.62, 0.04),
    (0.60, 0.06),
    (0.58, 0.06),
    (0.62, 0.06),
)
_NORTH_BOOTSTRAP_COMMAND = "north-bootstrap-v2"
_NORTH_BOOTSTRAP_SETTLE_SECONDS = 1.0
_NORTH_BOOTSTRAP_OUTPUT = Path("diagnostics/issue31-camera-reacquisition-v2")
_FIXED_SYSTEM_ID_COMMAND = "fixed-aba-probe-v2"
_FIXED_SYSTEM_ID_OUTPUT = Path("diagnostics/issue31-camera-system-id-v2")
_BRIDGE_CAPTURE_COMMAND = "bridge-capture-r2"
_BRIDGE_CAPTURE_OUTPUT = Path("diagnostics/issue31-camera-bridge-r2")
_BRIDGE_OBJECTIVE_ID = FROZEN_ENDPOINT_OBJECTIVE_ID
_BRIDGE_OBJECTIVE_REPORT_SHA256S = (
    FROZEN_ENDPOINT_OBJECTIVE.selection_backing_report_sha256s
)
_BRIDGE_ANALYSIS_PLAN_ID = "issue31-read-only-bridge-analysis-r2"
_BRIDGE_ANALYSIS_PLAN_VERSION = "1.1.0"
_BRIDGE_SMALLEST_ADDITIONAL_EVIDENCE = (
    "one additional exact receipt-bound north-up-p610-y043-reset endpoint "
    "whose post frame earns cycle-verified all-three-zone edges to both "
    "existing family endpoints and at least one common frozen reviewed "
    "supported anchor"
)
_BRIDGE_LIVE_INPUT_ENABLED: Final[bool] = True
_BRIDGE_NORTH_MAXIMUM_AGE_SECONDS = 30.0
_EXPECTED_DETECTOR_ID = "profiled-resource:varrock-east-iron-v1"
_EXPECTED_DETECTOR_VERSION = "2.1.0"
_EXPECTED_PROFILE_ID = "varrock-east-iron-v1"
_EXPECTED_PROFILE_SCHEMA_VERSION = 3
_EXPECTED_GUIDANCE_V2_ID = "issue31-world-only-multi-axis-guidance"
_EXPECTED_GUIDANCE_V2_VERSION = "2.0.0"
_EXPECTED_WINDOWS_CAMERA_ADAPTER = (
    "mining_automation.validation.windows_camera.WindowsCameraControl"
)


class _PrivateArtifactRecorder:
    def __init__(
        self,
        root: Path,
        *,
        case_prefix: str,
        git_head_sha: str,
        plan_id: str,
        plan_version: str,
    ) -> None:
        self._root = root
        self._case_prefix = case_prefix
        self._git_head_sha = git_head_sha
        self._plan_id = plan_id
        self._plan_version = plan_version

    def __call__(self, label: str, frame: Frame) -> CameraFrameArtifact:
        case_id = f"{self._case_prefix}-{label}"
        paths = write_resource_fixture_draft(
            frame,
            self._root,
            dataset_id="issue31-varrock-east-camera-v1",
            case_id=case_id,
            location_id="varrock-east-mine",
            tags=("real", "issue-31", "camera-validation", "unreviewed"),
            provenance={
                "git_head_sha": self._git_head_sha,
                "plan_id": self._plan_id,
                "plan_version": self._plan_version,
                "validation_tool": "validate_varrock_east_camera.py",
            },
            notes=(
                "Private unreviewed Issue #31 camera-validation evidence. "
                "Production observations are not human ground truth."
            ),
        )
        return CameraFrameArtifact(
            label=label,
            frame_id=frame.frame_id,
            width=frame.width,
            height=frame.height,
            pixel_format=frame.pixel_format.value,
            raw_sha256=hashlib.sha256(frame.payload).hexdigest(),
            files=(
                ("raw", paths.frame.relative_to(self._root).as_posix()),
                ("preview", paths.preview.relative_to(self._root).as_posix()),
                ("draft", paths.draft.relative_to(self._root).as_posix()),
            ),
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("diagnostics/issue31-camera-reacquisition"),
        help="private diagnostics root",
    )
    parser.add_argument(
        "--case-prefix",
        help="permanently single-use artifact/report prefix",
    )
    parser.add_argument(
        "--title",
        default=DEFAULT_TITLE_SUBSTRING,
        help=f"RuneLite title substring (default: {DEFAULT_TITLE_SUBSTRING!r})",
    )
    parser.add_argument(
        "--normalization-strategy",
        choices=(_SINGLE_PLAN_STRATEGY, _PRODUCTION_GATED_STRATEGY_ID),
        default=_SINGLE_PLAN_STRATEGY,
        help=(
            "single reviewed plan, or the fixed bounded production-gated "
            "Issue #31 candidate search"
        ),
    )
    parser.add_argument(
        "--pitch-endpoint",
        choices=("up", "down"),
        help="single-plan pitch saturation endpoint",
    )
    parser.add_argument(
        "--pitch-offset-hold",
        type=float,
        default=0.0,
        help="bounded opposite-direction hold back from the pitch endpoint",
    )
    parser.add_argument(
        "--yaw-offset-direction",
        choices=("left", "right"),
        help="optional bounded yaw direction applied after compass north",
    )
    parser.add_argument(
        "--yaw-offset-hold",
        type=float,
        default=0.0,
        help="bounded yaw hold in seconds; requires --yaw-offset-direction",
    )
    parser.add_argument(
        "--yaw-drag-pixels",
        type=int,
        default=0,
        help="optional signed horizontal logical-client camera refinement",
    )
    parser.add_argument(
        "--pitch-drag-pixels",
        type=int,
        default=0,
        help="optional signed vertical logical-client camera refinement",
    )
    parser.add_argument(
        "--post-compass-settle",
        type=float,
        default=_DEFAULT_POST_COMPASS_SETTLE_S,
        help="explicit no-input settle after the compass click",
    )
    zoom = parser.add_mutually_exclusive_group()
    zoom.add_argument(
        "--reset-zoom",
        action="store_true",
        help="release Control to invoke a preconfigured RuneLite exact zoom reset",
    )
    zoom.add_argument(
        "--zoom-saturate-detents",
        type=int,
        help=(
            "signed wheel detents to a known zoom endpoint; absolute value must "
            f"be {_MINIMUM_SATURATION_DETENTS}..{MAX_CAMERA_WHEEL_DETENTS}"
        ),
    )
    parser.add_argument(
        "--zoom-offset-detents",
        type=int,
        default=0,
        help="signed detents back from the saturated zoom endpoint",
    )
    parser.add_argument("--plan-id", default="varrock-east-camera-endpoint")
    parser.add_argument("--plan-version", default="0.3.0")
    parser.add_argument("--settle", type=float, default=1.0)
    parser.add_argument("--confirmations", type=int, default=2)
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help=(
            "permit a development run with worktree changes; report remains "
            "non-release evidence"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the bounded plans without capture or input",
    )
    return parser


def _build_north_bootstrap_parser() -> argparse.ArgumentParser:
    """Return the isolated parser for the one-action V2 live boundary."""

    parser = argparse.ArgumentParser(
        prog=f"{Path(__file__).name} {_NORTH_BOOTSTRAP_COMMAND}",
        description=(
            "Run one production-gated, receipt-bound compass-north bootstrap."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_NORTH_BOOTSTRAP_OUTPUT,
        help="private ignored diagnostics root",
    )
    parser.add_argument(
        "--case-prefix",
        required=True,
        help="permanently single-use artifact/report prefix",
    )
    return parser


def _build_fixed_system_id_parser() -> argparse.ArgumentParser:
    """Return the isolated parser for the fixed A/B/A live boundary."""

    parser = argparse.ArgumentParser(
        prog=f"{Path(__file__).name} {_FIXED_SYSTEM_ID_COMMAND}",
        description=(
            "Run the fixed horizontal A/B/A system-identification probe and "
            "conditionally the identical vertical probe."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_FIXED_SYSTEM_ID_OUTPUT,
        help="private ignored diagnostics root",
    )
    parser.add_argument(
        "--case-prefix",
        required=True,
        help="permanently single-use artifact/report prefix",
    )
    return parser


def _build_bridge_capture_parser() -> argparse.ArgumentParser:
    """Return the isolated parser for the fixed full-campaign R2 boundary."""

    parser = argparse.ArgumentParser(
        prog=f"{Path(__file__).name} {_BRIDGE_CAPTURE_COMMAND}",
        description=(
            "Run the fixed, receipt-backed R2.3 north-plus-Right campaign after "
            "exact-head review. This command has no caller-selectable camera control."
        ),
    )
    parser.add_argument(
        "--expected-head",
        required=True,
        help="exact reviewed 40-character Git head required before any input",
    )
    parser.add_argument(
        "--analysis-report",
        required=True,
        type=Path,
        help="reviewed exact-head R2 bridge-analysis report",
    )
    parser.add_argument(
        "--analysis-sha256",
        required=True,
        help="reviewed SHA-256 of the R2 bridge-analysis report",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_BRIDGE_CAPTURE_OUTPUT,
        help="private ignored diagnostics root",
    )
    parser.add_argument(
        "--case-prefix",
        required=True,
        help="permanently single-use artifact/report prefix",
    )
    return parser


def _default_case_prefix() -> str:
    return "issue31-camera-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def _validate_case_prefix(value: str) -> str:
    if _CASE_PREFIX_PATTERN.fullmatch(value) is None:
        raise ValueError(
            "--case-prefix must start with an alphanumeric character, contain "
            "only letters, numbers, dot, underscore, or hyphen, and be at most "
            "128 characters"
        )
    return value


def _validate_cli_text(option: str, value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{option} must be a non-empty trimmed string")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{option} must not contain control characters")
    return value


def _validate_command_argv(command_argv: tuple[str, ...]) -> None:
    for index, argument in enumerate(command_argv):
        if not argument or any(
            character in argument for character in ("\x00", "\r", "\n")
        ):
            raise ValueError(
                f"command argument {index} must be non-empty and contain no "
                "NUL or line-break characters"
            )


def _strategy_identity(args: argparse.Namespace) -> tuple[str, str]:
    if args.normalization_strategy == _PRODUCTION_GATED_STRATEGY_ID:
        return _PRODUCTION_GATED_STRATEGY_ID, _PRODUCTION_GATED_STRATEGY_VERSION
    return args.plan_id, args.plan_version


def _require_single_plan_recipe(args: argparse.Namespace) -> None:
    if args.pitch_endpoint is None:
        raise ValueError(
            "--pitch-endpoint is required with --normalization-strategy single-plan"
        )
    if not args.reset_zoom and args.zoom_saturate_detents is None:
        raise ValueError(
            "one of --reset-zoom or --zoom-saturate-detents is required with "
            "--normalization-strategy single-plan"
        )


def _build_normalization_plan(args: argparse.Namespace) -> CameraPlan:
    _require_single_plan_recipe(args)
    pitch = CameraHoldKey(args.pitch_endpoint)
    actions: list[CameraAction] = [
        CompassClick(*_COMPASS_POINT),
        CameraPause(args.post_compass_settle),
    ]
    yaw_hold = args.yaw_offset_hold
    yaw_direction = args.yaw_offset_direction
    if (
        isinstance(yaw_hold, bool)
        or not isinstance(yaw_hold, (int, float))
        or not 0.0 <= yaw_hold <= MAX_KEY_HOLD_SECONDS
    ):
        raise ValueError(
            "--yaw-offset-hold must be finite and between 0 and "
            f"{MAX_KEY_HOLD_SECONDS} seconds"
        )
    if yaw_hold and yaw_direction is None:
        raise ValueError(
            "--yaw-offset-direction is required when --yaw-offset-hold is nonzero"
        )
    if not yaw_hold and yaw_direction is not None:
        raise ValueError(
            "--yaw-offset-hold must be nonzero when --yaw-offset-direction is set"
        )
    if yaw_direction is not None:
        actions.append(
            CameraKeyHold(CameraHoldKey(yaw_direction), float(yaw_hold))
        )
    if args.yaw_drag_pixels:
        actions.append(
            CameraMiddleDrag(CameraDragAxis.HORIZONTAL, args.yaw_drag_pixels)
        )
    actions.append(
        CameraKeyHold(pitch, _DEFAULT_PITCH_HOLD_S),
    )
    pitch_offset = args.pitch_offset_hold
    if (
        isinstance(pitch_offset, bool)
        or not isinstance(pitch_offset, (int, float))
        or not 0.0 <= pitch_offset <= MAX_KEY_HOLD_SECONDS
    ):
        raise ValueError(
            "--pitch-offset-hold must be finite and between 0 and "
            f"{MAX_KEY_HOLD_SECONDS} seconds"
        )
    if pitch_offset:
        opposite = (
            CameraHoldKey.DOWN
            if pitch is CameraHoldKey.UP
            else CameraHoldKey.UP
        )
        actions.append(CameraKeyHold(opposite, float(pitch_offset)))
    if args.pitch_drag_pixels:
        actions.append(
            CameraMiddleDrag(CameraDragAxis.VERTICAL, args.pitch_drag_pixels)
        )
    if args.reset_zoom:
        if args.zoom_offset_detents != 0:
            raise ValueError("--zoom-offset-detents cannot be used with --reset-zoom")
        actions.append(ResetZoomKey("control", dwell_s=0.1))
    else:
        saturation = args.zoom_saturate_detents
        if not isinstance(saturation, int) or isinstance(saturation, bool):
            raise ValueError("--zoom-saturate-detents must be an integer")
        if not _MINIMUM_SATURATION_DETENTS <= abs(saturation) <= (
            MAX_CAMERA_WHEEL_DETENTS
        ):
            raise ValueError(
                "--zoom-saturate-detents absolute value must be between "
                f"{_MINIMUM_SATURATION_DETENTS} and {MAX_CAMERA_WHEEL_DETENTS}"
            )
        offset = args.zoom_offset_detents
        if isinstance(offset, bool) or not isinstance(offset, int):
            raise ValueError("--zoom-offset-detents must be an integer")
        if offset and (abs(offset) > MAX_CAMERA_WHEEL_DETENTS or offset * saturation >= 0):
            raise ValueError(
                "--zoom-offset-detents must be bounded and point back from the "
                "saturation direction"
            )
        actions.append(CameraWheel(*_CAMERA_WHEEL_POINT, saturation))
        if offset:
            actions.append(CameraWheel(*_CAMERA_WHEEL_POINT, offset))
    return CameraPlan(args.plan_id, tuple(actions))


def _production_gated_candidate_plan(
    index: int,
    *,
    pitch_offset_s: float,
    yaw_offset_s: float,
) -> CameraPlan:
    """Build one complete independent reset from fixed Issue #31 bounds."""

    return CameraPlan(
        f"{_PRODUCTION_GATED_STRATEGY_ID}-candidate-{index:02d}",
        (
            CompassClick(*_COMPASS_POINT),
            CameraPause(_DEFAULT_POST_COMPASS_SETTLE_S),
            CameraKeyHold(CameraHoldKey.RIGHT, yaw_offset_s),
            CameraKeyHold(CameraHoldKey.UP, _DEFAULT_PITCH_HOLD_S),
            CameraKeyHold(CameraHoldKey.DOWN, pitch_offset_s),
            CameraWheel(
                *_CAMERA_WHEEL_POINT,
                _PRODUCTION_GATED_ZOOM_SATURATION,
            ),
            CameraWheel(*_CAMERA_WHEEL_POINT, _PRODUCTION_GATED_ZOOM_OFFSET),
        ),
    )


def _require_no_single_plan_overrides(args: argparse.Namespace) -> None:
    overrides = (
        args.pitch_endpoint is not None
        or args.pitch_offset_hold != 0.0
        or args.yaw_offset_direction is not None
        or args.yaw_offset_hold != 0.0
        or args.yaw_drag_pixels != 0
        or args.pitch_drag_pixels != 0
        or args.post_compass_settle != _DEFAULT_POST_COMPASS_SETTLE_S
        or args.reset_zoom
        or args.zoom_saturate_detents is not None
        or args.zoom_offset_detents != 0
    )
    if overrides:
        raise ValueError(
            f"--normalization-strategy {_PRODUCTION_GATED_STRATEGY_ID} uses a "
            "frozen candidate ladder and cannot be combined with single-plan "
            "pitch, yaw, drag, settle, or zoom options"
        )


def _build_normalization_candidates(
    args: argparse.Namespace,
) -> tuple[CameraPlan, ...]:
    if args.normalization_strategy == _SINGLE_PLAN_STRATEGY:
        return (_build_normalization_plan(args),)
    _require_no_single_plan_overrides(args)
    return tuple(
        _production_gated_candidate_plan(
            index,
            pitch_offset_s=pitch_offset_s,
            yaw_offset_s=yaw_offset_s,
        )
        for index, (pitch_offset_s, yaw_offset_s) in enumerate(
            _PRODUCTION_GATED_CANDIDATE_OFFSETS,
            start=1,
        )
    )


def _build_perturbation_plans(args: argparse.Namespace) -> tuple[CameraPlan, ...]:
    endpoint = (
        CameraHoldKey.UP
        if args.normalization_strategy == _PRODUCTION_GATED_STRATEGY_ID
        else CameraHoldKey(args.pitch_endpoint)
    )
    opposite = CameraHoldKey.DOWN if endpoint is CameraHoldKey.UP else CameraHoldKey.UP
    return (
        CameraPlan(
            "perturb-yaw-right",
            (CameraKeyHold(CameraHoldKey.RIGHT, _DEFAULT_PERTURB_HOLD_S),),
        ),
        CameraPlan(
            "perturb-opposite-pitch-endpoint",
            (CameraKeyHold(opposite, _DEFAULT_PITCH_HOLD_S),),
        ),
        CameraPlan(
            "perturb-yaw-left-and-zoom",
            (
                CameraKeyHold(CameraHoldKey.LEFT, _DEFAULT_PERTURB_HOLD_S),
                CameraWheel(*_CAMERA_WHEEL_POINT, 12),
            ),
        ),
    )


def _trusted_git_environment() -> dict[str, str]:
    """Build a minimal environment with caller Git configuration disabled."""

    environment = {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "LANG": "C",
        "LC_ALL": "C",
    }
    for name in ("COMSPEC", "SYSTEMROOT", "TEMP", "TMP", "WINDIR"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment


def _trusted_git_executable() -> str:
    candidates = [Path("/usr/bin/git"), Path("/usr/local/bin/git")]
    if os.name == "nt":
        drive = Path(sys.executable).drive or "C:"
        candidates.insert(0, Path(drive + "/Program Files/Git/cmd/git.exe"))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())
    raise RuntimeError("cannot locate an approved absolute Git executable")


def _trusted_git_command(*arguments: str) -> list[str]:
    return [
        _trusted_git_executable(),
        f"--git-dir={repository_worktree_git_dir(_REPO_ROOT)}",
        f"--work-tree={_REPO_ROOT.resolve()}",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-c",
        "core.autocrlf=input",
        "-c",
        "core.excludesFile=",
        *arguments,
    ]


def _git_state() -> tuple[str, bool]:
    environment = _trusted_git_environment()
    head = subprocess.run(
        _trusted_git_command("rev-parse", "HEAD"),
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    ).stdout.strip()
    status = subprocess.run(
        _trusted_git_command("status", "--porcelain", "--untracked-files=all"),
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    ).stdout
    return head, not status.strip()


def _require_north_bootstrap_runtime_identities() -> None:
    """Pin the live command to the reviewed production and V2 identities."""

    profile = load_varrock_east_iron_profile()
    detector = build_varrock_east_iron_detector()
    observed = {
        "detector_id": detector.metadata.detector_id,
        "detector_version": detector.metadata.version,
        "detector_version_constant": VARROCK_EAST_IRON_DETECTOR_VERSION,
        "profile_id": profile.profile_id,
        "profile_id_constant": VARROCK_EAST_IRON_PROFILE_ID,
        "profile_schema_version": RESOURCE_PROFILE_SCHEMA_VERSION,
        "guidance_v2_id": CAMERA_GUIDANCE_V2_ID,
        "guidance_v2_version": CAMERA_GUIDANCE_V2_VERSION,
    }
    expected: dict[str, object] = {
        "detector_id": _EXPECTED_DETECTOR_ID,
        "detector_version": _EXPECTED_DETECTOR_VERSION,
        "detector_version_constant": _EXPECTED_DETECTOR_VERSION,
        "profile_id": _EXPECTED_PROFILE_ID,
        "profile_id_constant": _EXPECTED_PROFILE_ID,
        "profile_schema_version": _EXPECTED_PROFILE_SCHEMA_VERSION,
        "guidance_v2_id": _EXPECTED_GUIDANCE_V2_ID,
        "guidance_v2_version": _EXPECTED_GUIDANCE_V2_VERSION,
    }
    mismatches = [
        f"{name}={observed[name]!r} (expected {expected_value!r})"
        for name, expected_value in expected.items()
        if observed[name] != expected_value
    ]
    if mismatches:
        raise RuntimeError(
            "north-bootstrap runtime identity mismatch: " + "; ".join(mismatches)
        )


def _require_fixed_system_id_runtime_identities() -> None:
    """Pin the fixed live probe to reviewed production/control identities."""

    _require_north_bootstrap_runtime_identities()
    observed: dict[str, object] = {
        "system_id": CAMERA_SYSTEM_ID_ID,
        "system_id_version": CAMERA_SYSTEM_ID_VERSION,
        "system_id_drag_pixels": CAMERA_SYSTEM_ID_DRAG_PIXELS,
        "v2_drag_pixels": CAMERA_GUIDANCE_V2_DRAG_PULSE_PIXELS,
        "drag_point": REVIEWED_CAMERA_DRAG_POINT,
        "settle_s": CAMERA_SYSTEM_ID_SETTLE_SECONDS,
    }
    expected: dict[str, object] = {
        "system_id": "issue31-fixed-camera-system-identification",
        "system_id_version": "1.0.0",
        "system_id_drag_pixels": 4,
        "v2_drag_pixels": 4,
        "drag_point": (200, 600),
        "settle_s": 1.0,
    }
    mismatches = [
        f"{name}={observed[name]!r} (expected {expected_value!r})"
        for name, expected_value in expected.items()
        if observed[name] != expected_value
    ]
    if mismatches:
        raise RuntimeError(
            "fixed-system-id runtime identity mismatch: " + "; ".join(mismatches)
        )


def _require_bridge_capture_runtime_identities() -> None:
    """Pin R2.3 and its immutable one-Right bridge stage."""

    profile = load_varrock_east_iron_profile()
    detector = build_varrock_east_iron_detector()
    plan = camera_bridge_capture_plan()
    observed: dict[str, object] = {
        "authorization_campaign_id": CAMERA_BRIDGE_AUTHORIZATION_CAMPAIGN_ID,
        "authorization_id": CAMERA_BRIDGE_AUTHORIZATION_ID,
        "authorization_version": CAMERA_BRIDGE_AUTHORIZATION_VERSION,
        "bridge_id": CAMERA_BRIDGE_CAPTURE_ID,
        "bridge_version": CAMERA_BRIDGE_CAPTURE_VERSION,
        "detector_id": detector.metadata.detector_id,
        "detector_version": detector.metadata.version,
        "detector_version_constant": VARROCK_EAST_IRON_DETECTOR_VERSION,
        "hold_seconds": CAMERA_BRIDGE_CAPTURE_HOLD_SECONDS,
        "maximum_physical_primitives": (
            CAMERA_BRIDGE_CAPTURE_MAXIMUM_PHYSICAL_PRIMITIVES
        ),
        "planner_id": CAMERA_BRIDGE_PLANNER_ID,
        "planner_version": CAMERA_BRIDGE_PLANNER_VERSION,
        "objective_action_id": FROZEN_ENDPOINT_OBJECTIVE.action_id,
        "objective_duration_seconds": FROZEN_ENDPOINT_OBJECTIVE.duration_s,
        "objective_family_id": FROZEN_ENDPOINT_OBJECTIVE.family_id,
        "objective_id": FROZEN_ENDPOINT_OBJECTIVE.experiment_id,
        "objective_key": FROZEN_ENDPOINT_OBJECTIVE.key.value,
        "objective_receipts": tuple(
            sorted(FROZEN_ENDPOINT_OBJECTIVE.selection_backing_report_sha256s)
        ),
        "objective_source_sha256": FROZEN_ENDPOINT_SOURCE_SHA256,
        "plan": _plan_dict(plan),
        "profile_id": profile.profile_id,
        "profile_id_constant": VARROCK_EAST_IRON_PROFILE_ID,
        "profile_schema_version": RESOURCE_PROFILE_SCHEMA_VERSION,
        "settle_seconds": CAMERA_BRIDGE_CAPTURE_SETTLE_SECONDS,
    }
    expected: dict[str, object] = {
        "authorization_campaign_id": (
            "issue31-r2-3-full-campaign-north-right-0043-v1"
        ),
        "authorization_id": "issue31-r2-3-full-campaign-authorization",
        "authorization_version": "2.3.0",
        "bridge_id": "issue31-fixed-camera-bridge-capture-r2",
        "bridge_version": "1.1.0",
        "detector_id": _EXPECTED_DETECTOR_ID,
        "detector_version": _EXPECTED_DETECTOR_VERSION,
        "detector_version_constant": _EXPECTED_DETECTOR_VERSION,
        "hold_seconds": 0.043,
        "maximum_physical_primitives": 1,
        "planner_id": "issue31-read-only-camera-bridge-planner-r2",
        "planner_version": "2.1.0",
        "objective_action_id": "issue31-fixed-camera-bridge-capture-r2",
        "objective_duration_seconds": 0.043,
        "objective_family_id": "north-up-p610-y043-reset",
        "objective_id": "north-up-p610-y043-reset:right-key-hold-0.043s",
        "objective_key": "right",
        "objective_receipts": tuple(sorted((
            "1925996eb4f431f44a71abc6a33d5198707fc6173f0c81ec91ee4b350241547f",
            "a9a75ac611789b9f4d900261c63ad03210764b6db34d55ac883d700546de1dc5",
        ))),
        "objective_source_sha256": (
            "c1cb6fe144600ce153b1ceb2e90d6e375d42babea1eda6a08120efbc7ed2a4cd"
        ),
        "plan": {
            "name": "issue31-fixed-camera-bridge-capture-r2",
            "actions": [
                {
                    "kind": "key_hold",
                    "key": "right",
                    "duration_s": 0.043,
                    "post_release_settle_s": CAMERA_KEY_RELEASE_SETTLE_SECONDS,
                    "post_release_verification": (
                        "semantic_client_consumption_wait_then_observable_key_up_and_"
                        "target_focus_identity_geometry"
                    ),
                }
            ],
        },
        "profile_id": _EXPECTED_PROFILE_ID,
        "profile_id_constant": _EXPECTED_PROFILE_ID,
        "profile_schema_version": _EXPECTED_PROFILE_SCHEMA_VERSION,
        "settle_seconds": 1.0,
    }
    mismatches = [
        f"{name}={observed[name]!r} (expected {expected_value!r})"
        for name, expected_value in expected.items()
        if observed[name] != expected_value
    ]
    if mismatches:
        raise RuntimeError(
            "bridge-capture runtime identity mismatch: " + "; ".join(mismatches)
        )


def _require_north_bootstrap_result_identities(
    result: CameraNorthBootstrapResult,
) -> None:
    """Reject evidence produced by anything outside the pinned live policy."""

    for stage, frame in (
        ("initial", result.initial),
        ("arm", result.arm),
        ("commit", result.commit),
        ("post", result.post),
    ):
        if frame is None or frame.production is None:
            continue
        _require_north_bootstrap_production_identity(stage, frame.production)
    for stage, guidance in (
        ("decision", result.guidance),
        ("post", result.post_guidance),
    ):
        if guidance is None:
            continue
        if (
            guidance.selector_id != _EXPECTED_GUIDANCE_V2_ID
            or guidance.selector_version != _EXPECTED_GUIDANCE_V2_VERSION
        ):
            raise RuntimeError(f"{stage} guidance does not use the pinned V2 selector")
    if result.plan is not None and (
        result.plan.name != "issue31-v2-01-heading-north"
        or result.plan.actions != (CompassClick(*REVIEWED_COMPASS_POINT),)
    ):
        raise RuntimeError("north-bootstrap result retained an unexpected camera plan")


def _require_north_bootstrap_production_identity(
    stage: str,
    production: CameraEvaluation,
) -> None:
    if (
        production.detector_id != _EXPECTED_DETECTOR_ID
        or production.detector_version != _EXPECTED_DETECTOR_VERSION
        or production.profile_id != _EXPECTED_PROFILE_ID
        or production.profile_schema_version != _EXPECTED_PROFILE_SCHEMA_VERSION
        or production.profile_frame_width != EXPECTED_CLIENT_WIDTH
        or production.profile_frame_height != EXPECTED_CLIENT_HEIGHT
        or production.profile_pixel_format.value != "bgra8888"
        or production.required_landmark_count != 6
        or production.required_landmark_matches != 5
        or production.required_matched_zones != 3
    ):
        raise RuntimeError(
            f"{stage} production evidence does not use the pinned detector/profile"
        )


def _require_fixed_system_id_result_identities(
    result: CameraSystemIdResult,
) -> None:
    """Re-evaluate every retained payload before canonical publication."""

    axes = (result.horizontal,) + (
        () if result.vertical is None else (result.vertical,)
    )
    unique_observations: dict[tuple[int, float, str], CameraSystemIdObservation] = {}
    frame_hashes: dict[tuple[int, float], str] = {}
    for axis in axes:
        observations = [axis.baseline_one, axis.baseline_two]
        frames: list[CameraServoFrameEvidence | None] = []
        for step in (axis.positive_step, axis.return_step):
            if step is None:
                continue
            observations.extend(
                (
                    step.decision,
                    step.arm_observation,
                    step.commit_observation,
                    step.post,
                )
            )
            frames.extend((step.arm, step.commit))
        for index, observation in enumerate(observations):
            if observation is None:
                continue
            guidance = observation.guidance
            if (
                guidance.selector_id != CAMERA_GUIDANCE_ID
                or guidance.selector_version != CAMERA_GUIDANCE_VERSION
                or guidance.analysis is None
                or guidance.can_accept
                or guidance.can_validate_scene
                or guidance.can_expose_resources
            ):
                raise RuntimeError(
                    f"{axis.axis.value} observation {index} does not use the "
                    "pinned world-only diagnostic"
                )
            frames.append(observation.evidence)
            key = (
                observation.frame.frame_id,
                observation.frame.captured_monotonic_s,
                observation.evidence.artifact.raw_sha256,
            )
            frame_key = key[:2]
            existing_hash = frame_hashes.setdefault(
                frame_key,
                observation.evidence.artifact.raw_sha256,
            )
            if existing_hash != observation.evidence.artifact.raw_sha256:
                raise RuntimeError("one frame identity retained conflicting payloads")
            existing = unique_observations.setdefault(key, observation)
            if existing != observation:
                raise RuntimeError("one retained frame has conflicting observations")
        for index, evidence in enumerate(frames):
            if evidence is None or evidence.production is None:
                continue
            _require_north_bootstrap_production_identity(
                f"{axis.axis.value}-frame-{index}",
                evidence.production,
            )
    for index, observation in enumerate(unique_observations.values()):
        expected_readiness = evaluate_client_input_readiness(observation.frame)
        if observation.evidence.readiness != expected_readiness:
            raise RuntimeError(
                f"observation {index} readiness does not bind its exact payload"
            )
        expected_production = (
            evaluate_varrock_east_camera(observation.frame)
            if expected_readiness.safe_to_attempt_camera_input
            else None
        )
        if observation.evidence.production != expected_production:
            raise RuntimeError(
                f"observation {index} production does not bind its exact payload"
            )
        expected_guidance = evaluate_varrock_east_camera_guidance(observation.frame)
        if observation.guidance != expected_guidance:
            raise RuntimeError(
                f"observation {index} guidance does not bind its exact payload"
            )


def _require_bridge_capture_result_identities(
    result: CameraBridgeCaptureResult,
    *,
    output_root: Path,
    sealed_post_production: CameraEvaluation | None = None,
    post_production_already_bound: bool = False,
) -> None:
    """Re-evaluate every exact private R2 payload before publication."""

    if result.plan is not camera_bridge_capture_plan():
        raise RuntimeError("bridge result does not retain the frozen plan")
    if any(
        (
            result.can_accept,
            result.can_authorize_camera_input,
            result.can_expose_resources,
            result.can_validate_scene,
            result.diagnostic_registration_can_override_production,
        )
    ):
        raise RuntimeError("bridge result retained forbidden diagnostic authority")
    expected_labels = {
        "decision": "r2-decision",
        "arm": "r2-arm",
        "commit": "r2-commit",
        "post": "r2-post",
    }
    resolved_root = output_root.resolve()
    for stage, evidence in (
        ("decision", result.decision),
        ("arm", result.arm),
        ("commit", result.commit),
        ("post", result.post),
    ):
        if evidence is None:
            continue
        artifact = evidence.artifact
        if artifact.label != expected_labels[stage]:
            raise RuntimeError(f"{stage} artifact retained an unexpected label")
        files = dict(artifact.files)
        raw_relative = files.get("raw")
        if raw_relative is None:
            raise RuntimeError(f"{stage} artifact is missing its private raw path")
        raw_path = (output_root / raw_relative).resolve()
        if raw_path != resolved_root and resolved_root not in raw_path.parents:
            raise RuntimeError(f"{stage} raw path escaped the private output root")
        payload = raw_path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != artifact.raw_sha256:
            raise RuntimeError(f"{stage} raw payload SHA-256 does not match artifact")
        frame = Frame.from_raw(
            RawFrame(
                payload=payload,
                width=artifact.width,
                height=artifact.height,
                pixel_format=PixelFormat(artifact.pixel_format),
            ),
            frame_id=artifact.frame_id,
            captured_monotonic_s=evidence.captured_monotonic_s,
        )
        readiness = evaluate_client_input_readiness(frame)
        if readiness != evidence.readiness:
            raise RuntimeError(f"{stage} readiness does not bind its exact payload")
        if not isinstance(evidence, CameraServoFrameEvidence):
            if (
                stage != "post"
                or result.terminal_reason
                is not CameraBridgeCaptureTerminalReason.POST_CAPTURE_PENDING_CLOSURE
            ):
                raise RuntimeError(
                    f"{stage} retained pending evidence outside the closure seam"
                )
            continue
        if stage == "post" and post_production_already_bound:
            # The exact post payload/readiness/time/identity was bound by
            # _finalize_camera_bridge_post_production after registration.  A
            # second detector call here would violate the required O3 order
            # (capture -> registration -> production -> seal), so sealing is
            # a comparison against that already-bound result only.
            production = sealed_post_production
        else:
            production = (
                evaluate_varrock_east_camera(frame)
                if readiness.safe_to_attempt_camera_input
                else None
            )
        if production != evidence.production:
            raise RuntimeError(f"{stage} production does not bind its exact payload")
        if production is not None:
            _require_north_bootstrap_production_identity(stage, production)


def _resolve_private_output_root(output: Path) -> Path:
    """Resolve and verify the ignored, repository-local evidence boundary."""

    root = (output if output.is_absolute() else _REPO_ROOT / output).resolve()
    diagnostics_root = (_REPO_ROOT / "diagnostics").resolve()
    if root != diagnostics_root and diagnostics_root not in root.parents:
        raise ValueError(
            "--output must be diagnostics/ or one of its descendants so live "
            "pixels remain private"
        )
    ignored = subprocess.run(
        _trusted_git_command("check-ignore", "--quiet", "--", str(root)),
        cwd=_REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=_trusted_git_environment(),
    )
    if ignored.returncode != 0:
        raise ValueError("--output must be excluded by the repository ignore rules")
    return root


def _display_repo_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(_REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _load_private_bound_report(
    report: Path,
    *,
    expected_sha256: str,
) -> tuple[Path, dict[str, Any]]:
    """Load one ignored canonical report through its exact reviewed digest."""

    if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        raise ValueError("reviewed report SHA-256 must be lowercase hexadecimal")
    resolved = (report if report.is_absolute() else _REPO_ROOT / report).resolve()
    diagnostics_root = (_REPO_ROOT / "diagnostics").resolve()
    if diagnostics_root not in resolved.parents or not resolved.is_file():
        raise ValueError("reviewed report must be an existing diagnostics/ file")
    ignored = subprocess.run(
        _trusted_git_command("check-ignore", "--quiet", "--", str(resolved)),
        cwd=_REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=_trusted_git_environment(),
    )
    if ignored.returncode != 0:
        raise ValueError("reviewed report must remain private under ignore rules")
    payload = resolved.read_bytes()
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if actual_sha256 != expected_sha256:
        raise ValueError("reviewed report SHA-256 does not match exact bytes")
    sidecar = resolved.with_name(f"{resolved.name}.sha256")
    if sidecar.read_bytes() != f"{actual_sha256}\n".encode("ascii"):
        raise ValueError("reviewed report SHA-256 sidecar does not match")
    parsed = json.loads(
        payload,
        object_pairs_hook=_reject_duplicate_json_keys,
        parse_float=_finite_json_float,
        parse_constant=_reject_nonstandard_json_number,
    )
    return resolved, _json_object(parsed, "reviewed report")


def _require_north_handoff_command_argv(
    value: object,
    *,
    report_path: Path,
) -> None:
    """Authenticate the exact fixed north command and its artifact namespace."""

    argv = _json_list(value, "north provenance command_argv")
    if (
        len(argv) < 5
        or any(not isinstance(argument, str) or not argument for argument in argv)
        or argv[0] != str(Path(sys.executable).resolve())
        or argv[1] != str(Path(__file__).resolve())
        or argv[2] != _NORTH_BOOTSTRAP_COMMAND
    ):
        raise ValueError("north handoff command_argv is not the fixed launcher")
    options: dict[str, str] = {}
    index = 3
    while index < len(argv):
        token = argv[index]
        assert isinstance(token, str)
        if token in ("--case-prefix", "--output"):
            if token in options or index + 1 >= len(argv):
                raise ValueError("north handoff command_argv has invalid options")
            option_value = argv[index + 1]
            if not isinstance(option_value, str) or not option_value:
                raise ValueError("north handoff command_argv has invalid options")
            options[token] = option_value
            index += 2
            continue
        matching = next(
            (
                option
                for option in ("--case-prefix", "--output")
                if token.startswith(f"{option}=")
            ),
            None,
        )
        if matching is None or matching in options:
            raise ValueError("north handoff command_argv contains an override")
        options[matching] = token.removeprefix(f"{matching}=")
        if not options[matching]:
            raise ValueError("north handoff command_argv has an empty option")
        index += 1
    expected_prefix = report_path.name.removesuffix(".camera.json")
    if options.get("--case-prefix") != expected_prefix:
        raise ValueError("north handoff command does not bind its report prefix")
    output_value = options.get("--output")
    output_root = (
        (_REPO_ROOT / _NORTH_BOOTSTRAP_OUTPUT).resolve()
        if output_value is None
        else (
            Path(output_value)
            if Path(output_value).is_absolute()
            else _REPO_ROOT / output_value
        ).resolve()
    )
    if output_root != report_path.parent.parent.resolve():
        raise ValueError("north handoff command does not bind its output root")


def _bridge_north_window_binding(
    evidence: dict[str, Any],
) -> tuple[int, int, int, str, str]:
    """Authenticate the exact selected-window identity recorded by north."""

    window_binding = _json_object(
        evidence.get("selected_window_binding"),
        "north selected-window binding",
    )
    window_hwnd = window_binding.get("hwnd")
    window_process_id = window_binding.get("process_id")
    window_thread_id = window_binding.get("thread_id")
    window_class_name = window_binding.get("class_name")
    window_title_sha256 = window_binding.get("title_sha256")
    if (
        any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in (window_hwnd, window_process_id, window_thread_id)
        )
        or not isinstance(window_class_name, str)
        or not window_class_name
        or window_class_name != window_class_name.strip()
        or not isinstance(window_title_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", window_title_sha256) is None
        or window_binding.get("adapter_identity")
        != _EXPECTED_WINDOWS_CAMERA_ADAPTER
        or window_binding.get("title_substring") != DEFAULT_TITLE_SUBSTRING
    ):
        raise ValueError("north handoff lacks an exact selected-window binding")
    assert isinstance(window_hwnd, int)
    assert isinstance(window_process_id, int)
    assert isinstance(window_thread_id, int)
    return (
        window_hwnd,
        window_process_id,
        window_thread_id,
        window_class_name,
        window_title_sha256,
    )


def _load_bridge_north_handoff(
    report: Path,
    *,
    expected_sha256: str,
    expected_head: str,
) -> _BridgeNorthHandoff:
    """Reject legacy standalone north evidence for the R2.3 campaign."""

    raise ValueError(
        "generic or externally produced north-bootstrap reports cannot satisfy "
        "the integrated R2.3 campaign precursor"
    )

    report_path, payload = _load_private_bound_report(
        report,
        expected_sha256=expected_sha256,
    )
    if payload.get("schema_version") != 2:
        raise ValueError("north handoff report schema must be version 2")
    provenance = _json_object(payload.get("provenance"), "north provenance")
    required_provenance = {
        "detector_id": _EXPECTED_DETECTOR_ID,
        "detector_version": _EXPECTED_DETECTOR_VERSION,
        "git_head_sha": expected_head,
        "plan_id": _EXPECTED_GUIDANCE_V2_ID,
        "plan_version": _EXPECTED_GUIDANCE_V2_VERSION,
        "profile_id": _EXPECTED_PROFILE_ID,
        "tracked_worktree_clean": True,
    }
    if any(
        provenance.get(field) != value
        for field, value in required_provenance.items()
    ):
        raise ValueError("north handoff provenance does not match reviewed head")
    _require_north_handoff_command_argv(
        provenance.get("command_argv"),
        report_path=report_path,
    )
    evidence = _json_object(payload.get("evidence"), "north evidence")
    if (
        evidence.get("command") != _NORTH_BOOTSTRAP_COMMAND
        or evidence.get("development_only") is not True
        or evidence.get("terminal_reason") != "bootstrap_executed"
    ):
        raise ValueError("north handoff is not a completed fixed bootstrap")
    expected_plan = {
        "actions": [
            {
                "kind": "compass_click",
                "x": REVIEWED_COMPASS_POINT[0],
                "y": REVIEWED_COMPASS_POINT[1],
            }
        ],
        "name": "issue31-v2-01-heading-north",
    }
    plan = _json_object(evidence.get("plan"), "north plan")
    if plan != expected_plan:
        raise ValueError("north handoff plan is not the frozen compass primitive")
    receipt = _json_object(evidence.get("receipt"), "north receipt")
    if receipt.get("plan") != expected_plan:
        raise ValueError("north handoff receipt does not bind the frozen plan")
    if receipt.get("preflight") != {
        "client_height": EXPECTED_CLIENT_HEIGHT,
        "client_width": EXPECTED_CLIENT_WIDTH,
        "focused": True,
        "supported": True,
    }:
        raise ValueError("north handoff receipt lacks exact focused geometry")
    actions = _json_list(receipt.get("actions"), "north receipt actions")
    expected_action_receipt = {
        "action": expected_plan["actions"][0],
        "action_index": 0,
        "input_receipts": [
            {
                "complete": True,
                "completed_events": 2,
                "operation": "compass_click",
                "requested_events": 2,
            }
        ],
    }
    if actions != [expected_action_receipt]:
        raise ValueError("north handoff input receipt is incomplete or unexpected")
    frames = _json_object(evidence.get("frames"), "north frames")
    post = _json_object(frames.get("post"), "north post")
    artifact = _json_object(post.get("artifact"), "north post artifact")
    files = _json_object(artifact.get("files"), "north post files")
    raw_relative = files.get("raw")
    if not isinstance(raw_relative, str) or not raw_relative:
        raise ValueError("north post artifact is missing its private raw path")
    root = report_path.parent.parent.resolve()
    raw_path = (root / raw_relative).resolve()
    if root not in raw_path.parents or not raw_path.is_file():
        raise ValueError("north post raw path escaped or is missing")
    raw_payload = raw_path.read_bytes()
    raw_sha256 = hashlib.sha256(raw_payload).hexdigest()
    if artifact.get("raw_sha256") != raw_sha256:
        raise ValueError("north post raw payload does not match its artifact")
    if (
        artifact.get("width") != EXPECTED_CLIENT_WIDTH
        or artifact.get("height") != EXPECTED_CLIENT_HEIGHT
        or artifact.get("pixel_format") != PixelFormat.BGRA8888.value
    ):
        raise ValueError("north post raw geometry is not the reviewed geometry")
    frame_id = artifact.get("frame_id")
    captured = artifact.get("captured_monotonic_s")
    if (
        isinstance(frame_id, bool)
        or not isinstance(frame_id, int)
        or frame_id <= 0
        or isinstance(captured, bool)
        or not isinstance(captured, (int, float))
        or not math.isfinite(float(captured))
        or float(captured) < 0.0
    ):
        raise ValueError("north post artifact retained invalid frame identity")
    frame = Frame.from_raw(
        RawFrame(
            payload=raw_payload,
            width=EXPECTED_CLIENT_WIDTH,
            height=EXPECTED_CLIENT_HEIGHT,
            pixel_format=PixelFormat.BGRA8888,
        ),
        frame_id=frame_id,
        captured_monotonic_s=float(captured),
    )
    readiness = evaluate_client_input_readiness(frame)
    production = evaluate_varrock_east_camera(frame)
    if _readiness_dict(readiness) != post.get("readiness"):
        raise ValueError("north readiness does not bind the exact post payload")
    if _evaluation_dict(production) != post.get("production"):
        raise ValueError("north production does not bind the exact post payload")
    _require_north_bootstrap_production_identity("north-handoff", production)
    if readiness.safe_to_attempt_camera_input is not True or production.passed:
        raise ValueError("north handoff must be ready and production-rejected")
    (
        window_hwnd,
        window_process_id,
        window_thread_id,
        window_class_name,
        window_title_sha256,
    ) = _bridge_north_window_binding(evidence)
    return _BridgeNorthHandoff(
        report_path=report_path,
        report_sha256=expected_sha256,
        frame=frame,
        raw_path=raw_path,
        window_hwnd=window_hwnd,
        window_process_id=window_process_id,
        window_thread_id=window_thread_id,
        window_class_name=window_class_name,
        window_title_sha256=window_title_sha256,
    )


def _load_bridge_analysis_evidence(
    report: Path,
    *,
    expected_sha256: str,
    expected_head: str,
) -> _BridgeAnalysisEvidence:
    """Load exact read-only planner evidence without granting input authority."""

    report_path, payload = _load_private_bound_report(
        report,
        expected_sha256=expected_sha256,
    )
    if payload.get("schema_version") != 2:
        raise ValueError("R2 analysis report schema must be version 2")
    provenance = _json_object(payload.get("provenance"), "R2 provenance")
    required_provenance = {
        "detector_id": _EXPECTED_DETECTOR_ID,
        "detector_version": _EXPECTED_DETECTOR_VERSION,
        "git_head_sha": expected_head,
        "plan_id": _BRIDGE_ANALYSIS_PLAN_ID,
        "plan_version": _BRIDGE_ANALYSIS_PLAN_VERSION,
        "profile_id": _EXPECTED_PROFILE_ID,
        "tracked_worktree_clean": True,
    }
    if any(
        provenance.get(field) != value
        for field, value in required_provenance.items()
    ):
        raise ValueError("R2 analysis provenance does not bind the reviewed head")
    evidence = _json_object(payload.get("evidence"), "R2 analysis evidence")
    authority = _json_object(evidence.get("authority"), "R2 authority")
    if authority != {
        "diagnostic_registration_can_override_production": False,
        "live_camera_input_authorized": False,
        "live_camera_input_performed": False,
        "registration_can_authorize_camera_input": False,
        "registration_can_expose_resources": False,
        "registration_can_validate_scene": False,
    }:
        raise ValueError("R2 analysis retained input or production authority")
    safe_graph = _json_object(evidence.get("safe_view_graph"), "R2 safe graph")
    if (
        _json_object(safe_graph.get("authority"), "R2 safe graph authority")
        != {
            "can_accept": False,
            "can_authorize_camera_input": False,
            "can_expose_resources": False,
            "can_validate_scene": False,
            "diagnostic_registration_can_override_production": False,
        }
        or safe_graph.get("graph_id") != ROBUST_VIEW_GRAPH_ID
        or safe_graph.get("graph_version") != ROBUST_VIEW_GRAPH_VERSION
    ):
        raise ValueError("R2 safe graph identity/authority is not canonical")
    planner = _json_object(evidence.get("bridge_planner"), "R2 bridge planner")
    if (
        planner.get("planner_id")
        != "issue31-read-only-camera-bridge-planner-r2"
        or planner.get("planner_version") != CAMERA_BRIDGE_PLANNER_VERSION
        or planner.get("disposition") != "no_safe_endpoint_evidence"
        or _json_object(planner.get("authority"), "R2 planner authority")
        != {
            "can_accept": False,
            "can_authorize_camera_input": False,
            "can_expose_resources": False,
            "can_validate_scene": False,
            "diagnostic_registration_can_override_production": False,
        }
    ):
        raise ValueError("R2 bridge planner identity/disposition is not reviewed")
    matrix_policy = _json_object(planner.get("matrix_policy"), "R2 matrix policy")
    if matrix_policy != {
        "rejected_registration_matrices_used_for_control": False,
        "rejected_registration_metrics_used_for_ranking": False,
    }:
        raise ValueError("R2 planner used a rejected registration matrix")
    if planner.get("missing_experiment") is not None:
        raise ValueError("R2 no-safe evidence must not invent a missing experiment")
    if _json_list(planner.get("ranked_families"), "R2 ranked families"):
        raise ValueError("R2 no-safe evidence must not rank an endpoint family")
    inventory = _json_object(planner.get("inventory"), "R2 inventory")
    if (
        inventory.get("inventory_id")
        != "issue31-frozen-receipt-backed-camera-primitives-r2"
        or inventory.get("inventory_version") != "2.0.0"
    ):
        raise ValueError("R2 inventory identity is not canonical")
    experiments = _json_list(inventory.get("experiments"), "R2 experiments")
    if len(experiments) != 1:
        raise ValueError("R2 inventory must retain exactly one frozen objective")
    objective = _json_object(experiments[0], "R2 frozen objective")
    source_sha256 = objective.get("required_source_sha256")
    if (
        objective.get("experiment_id") != _BRIDGE_OBJECTIVE_ID
        or objective.get("action_id") != CAMERA_BRIDGE_CAPTURE_ID
        or objective.get("family_id") != FROZEN_ENDPOINT_OBJECTIVE.family_id
        or objective.get("key") != "right"
        or objective.get("duration_s") != CAMERA_BRIDGE_CAPTURE_HOLD_SECONDS
        or objective.get("minimum_distinct_receipt_endpoints") != 2
        or objective.get("ordinal") != 1
        or not isinstance(source_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", source_sha256) is None
        or source_sha256 != FROZEN_ENDPOINT_SOURCE_SHA256
    ):
        raise ValueError("R2 inventory does not retain the frozen bridge objective")
    if objective.get("selection_backing_report_sha256s") != list(
        sorted(_BRIDGE_OBJECTIVE_REPORT_SHA256S)
    ):
        raise ValueError("R2 inventory objective lacks the frozen selection receipts")
    if (
        planner.get("current_sha256") != source_sha256
        or safe_graph.get("current_sha256") != source_sha256
    ):
        raise ValueError("R2 planner source does not bind the safe-graph origin")
    family_evaluations = _json_list(
        planner.get("family_evaluations"),
        "R2 family evaluations",
    )
    selected_family = next(
        (
            _json_object(item, "R2 family evaluation")
            for item in family_evaluations
            if _json_object(item, "R2 family evaluation").get("family_id")
            == FROZEN_ENDPOINT_OBJECTIVE.family_id
        ),
        None,
    )
    if (
        selected_family is None
        or selected_family.get("complete") is not False
        or selected_family.get("distinct_receipt_report_sha256s")
        != list(sorted(_BRIDGE_OBJECTIVE_REPORT_SHA256S))
    ):
        raise ValueError("R2 planner does not retain the incomplete frozen family")
    family_failures = _json_list(
        selected_family.get("failure_reasons"),
        "R2 family failures",
    )
    if len(family_failures) != 1 or not str(family_failures[0]).startswith(
        "repeat_edge_not_verified_all_zones:"
    ):
        raise ValueError("R2 frozen family failure is not the reviewed repeat edge")
    common_anchors = _json_list(
        selected_family.get("qualifying_common_anchor_sha256s"),
        "R2 qualifying common anchors",
    )
    frozen_anchors = _json_list(
        selected_family.get("frozen_anchor_sha256s"),
        "R2 frozen anchors",
    )
    endpoint_sha256s = _json_list(
        selected_family.get("distinct_endpoint_sha256s"),
        "R2 distinct endpoints",
    )
    if (
        len(endpoint_sha256s) != 2
        or any(
            not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            for digest in endpoint_sha256s
        )
    ):
        raise ValueError("R2 frozen family endpoint membership is not canonical")
    anchor_evaluations = _json_list(
        selected_family.get("anchor_evaluations"),
        "R2 anchor evaluations",
    )
    completed_anchor_sha256s: list[str] = []
    for raw_anchor_evaluation in anchor_evaluations:
        anchor_evaluation = _json_object(
            raw_anchor_evaluation,
            "R2 anchor evaluation",
        )
        anchor_sha256 = anchor_evaluation.get("anchor_sha256")
        verified_edges = _json_list(
            anchor_evaluation.get("verified_edge_ids"),
            "R2 verified anchor edges",
        )
        missing_edges = _json_list(
            anchor_evaluation.get("missing_edge_ids"),
            "R2 missing anchor edges",
        )
        if anchor_sha256 not in frozen_anchors:
            raise ValueError("R2 anchor evaluation is not frozen")
        if anchor_evaluation.get("complete") is True:
            if missing_edges or len(verified_edges) != len(endpoint_sha256s):
                raise ValueError("R2 complete common anchor is internally inconsistent")
            assert isinstance(anchor_sha256, str)
            completed_anchor_sha256s.append(anchor_sha256)
        elif anchor_evaluation.get("complete") is not False:
            raise ValueError("R2 anchor completion must be boolean")
    if (
        not common_anchors
        or any(anchor not in frozen_anchors for anchor in common_anchors)
        or not all(
            isinstance(anchor, str)
            and re.fullmatch(r"[0-9a-f]{64}", anchor) is not None
            for anchor in common_anchors
        )
        or sorted(common_anchors) != sorted(completed_anchor_sha256s)
    ):
        raise ValueError("R2 frozen family lacks a reviewed common-anchor set")
    result = _json_object(evidence.get("result"), "R2 result")
    if (
        result.get("reacquisition_success_claimed") is not False
        or result.get("live_input_authorized") is not False
        or result.get("selected_experiment_id") is not None
        or result.get("conclusion") != "no safe endpoint evidence"
        or result.get("smallest_additional_evidence")
        != _BRIDGE_SMALLEST_ADDITIONAL_EVIDENCE
    ):
        raise ValueError("R2 result is not the reviewed no-safe replication need")
    r1_source = _json_object(evidence.get("r1_source"), "R2 R1 source")
    r1_report_sha256 = r1_source.get("report_sha256")
    negative = _json_object(
        r1_source.get("negative_corpus"),
        "R2 negative corpus",
    )
    if (
        not isinstance(r1_report_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", r1_report_sha256) is None
        or negative.get("policy_roles")
        != ["disconnected", "risky_state_change"]
        or negative.get("supported_path_count") != 0
    ):
        raise ValueError("R2 analysis does not retain the quarantined R1 corpus")
    source_frame, source_raw_path = _load_bridge_analysis_source(
        evidence,
        expected_sha256=source_sha256,
    )
    return _BridgeAnalysisEvidence(
        report_path=report_path,
        report_sha256=expected_sha256,
        r1_report_sha256=r1_report_sha256,
        planner_id="issue31-read-only-camera-bridge-planner-r2",
        planner_version=CAMERA_BRIDGE_PLANNER_VERSION,
        objective_id=_BRIDGE_OBJECTIVE_ID,
        source_frame=source_frame,
        source_raw_path=source_raw_path,
        source_sha256=source_sha256,
    )


def _load_bridge_analysis_source(
    evidence: dict[str, Any],
    *,
    expected_sha256: str,
) -> tuple[Frame, Path]:
    """Load the exact planner-origin pixels bound by the R2 safe graph."""

    corpus = _json_object(evidence.get("corpus"), "R2 corpus")
    north = _json_object(corpus.get("north"), "R2 corpus north")
    frame_evidence = _json_object(north.get("frame"), "R2 corpus north frame")
    if (
        frame_evidence.get("label") != "north:corrected-compass-post"
        or frame_evidence.get("raw_sha256") != expected_sha256
        or frame_evidence.get("width") != EXPECTED_CLIENT_WIDTH
        or frame_evidence.get("height") != EXPECTED_CLIENT_HEIGHT
        or frame_evidence.get("pixel_format") != PixelFormat.BGRA8888.value
    ):
        raise ValueError("R2 planner source is not the authenticated north frame")
    raw_reference = frame_evidence.get("path")
    if not isinstance(raw_reference, str) or not raw_reference:
        raise ValueError("R2 planner source is missing its exact raw path")
    raw_path = (_REPO_ROOT / raw_reference).resolve()
    diagnostics_root = (_REPO_ROOT / "diagnostics").resolve()
    if diagnostics_root not in raw_path.parents or not raw_path.is_file():
        raise ValueError("R2 planner source raw path escaped or is missing")
    ignored = subprocess.run(
        _trusted_git_command("check-ignore", "--quiet", "--", str(raw_path)),
        cwd=_REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=_trusted_git_environment(),
    )
    if ignored.returncode != 0:
        raise ValueError("R2 planner source raw pixels must remain private")
    payload = raw_path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError("R2 planner source raw SHA-256 mismatch")
    frame = Frame.from_raw(
        RawFrame(
            payload=payload,
            width=EXPECTED_CLIENT_WIDTH,
            height=EXPECTED_CLIENT_HEIGHT,
            pixel_format=PixelFormat.BGRA8888,
        ),
        frame_id=1,
        captured_monotonic_s=0.0,
    )
    readiness = evaluate_client_input_readiness(frame)
    production = evaluate_varrock_east_camera(frame)
    if not readiness.safe_to_attempt_camera_input or production.passed:
        raise ValueError("R2 planner source must be ready and production-rejected")
    return frame, raw_path


def _json_object(value: object, context: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{context} must be a JSON object")
    return value


def _json_list(value: object, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be a JSON array")
    return value


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonstandard_json_number(value: str) -> object:
    raise ValueError(f"non-standard JSON number: {value}")


def _finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"non-finite JSON number: {value}")
    return parsed


def _bridge_evidence_frame(
    output_root: Path,
    evidence: CameraServoFrameEvidence,
) -> Frame:
    files = dict(evidence.artifact.files)
    raw_relative = files.get("raw")
    if raw_relative is None:
        raise RuntimeError("bridge evidence is missing its private raw payload")
    root = output_root.resolve()
    raw_path = (root / raw_relative).resolve()
    if root not in raw_path.parents:
        raise RuntimeError("bridge evidence raw payload escaped diagnostics root")
    payload = raw_path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != evidence.artifact.raw_sha256:
        raise RuntimeError("bridge evidence raw payload SHA-256 mismatch")
    return Frame.from_raw(
        RawFrame(
            payload=payload,
            width=evidence.artifact.width,
            height=evidence.artifact.height,
            pixel_format=PixelFormat(evidence.artifact.pixel_format),
        ),
        frame_id=evidence.artifact.frame_id,
        captured_monotonic_s=evidence.captured_monotonic_s,
    )


def _require_bridge_starting_registration(
    registration: RobustWorldRegistration,
    *,
    expected_source_sha256: str,
    expected_target_sha256: str,
    context: str,
) -> None:
    """Use reviewed registration only as a veto on the fixed experiment."""

    model = registration.selected_model
    required_zones = frozenset(
        (MacroZone.NORTH_WEST, MacroZone.NORTH_EAST, MacroZone.SOUTH_WEST)
    )
    if (
        not registration.accepted
        or model is None
        or registration.source.payload_sha256 != expected_source_sha256
        or registration.target.payload_sha256 != expected_target_sha256
        or not required_zones.issubset(registration.required_zones)
        or registration.can_accept
        or registration.can_validate_scene
        or registration.can_expose_resources
        or registration.diagnostic_registration_can_override_production
    ):
        raise RuntimeError(
            f"{context} is not bound by accepted no-authority registration"
        )
    source_inliers = dict(model.source_zone_inliers)
    target_inliers = dict(model.target_zone_inliers)
    source_cells = dict(model.source_zone_cells)
    target_cells = dict(model.target_zone_cells)
    if any(
        source_inliers.get(zone, 0)
        < registration.policy.minimum_inliers_per_zone
        or target_inliers.get(zone, 0)
        < registration.policy.minimum_inliers_per_zone
        or source_cells.get(zone, 0)
        < registration.policy.minimum_spatial_cells_per_zone
        or target_cells.get(zone, 0)
        < registration.policy.minimum_spatial_cells_per_zone
        for zone in required_zones
    ):
        raise RuntimeError(
            f"{context} lacks distributed all-zone registration"
        )


def _require_bridge_pointer_ownership(
    api: RealWindowsCameraApi,
    *,
    hwnd: int,
) -> _BridgePointerEvidence:
    """Prove one frozen open-world point is still owned by the target root."""

    mapping = api.pointer_mapping(hwnd, *REVIEWED_CAMERA_WHEEL_POINT)
    physical = require_exact_round_trip(mapping)
    root_hwnd = api.root_window_at_point(*physical.pair)
    if root_hwnd != hwnd:
        raise RuntimeError(
            "reviewed open-world pointer point is owned by another root window"
        )
    return _BridgePointerEvidence(mapping=mapping, root_hwnd=root_hwnd)


def _report_paths(output_root: Path, case_prefix: str) -> tuple[Path, Path]:
    report = output_root / "reports" / f"{case_prefix}.camera.json"
    return report, report.with_name(f"{report.name}.sha256")


def _case_reservation_path(output_root: Path, case_prefix: str) -> Path:
    return (
        output_root
        / "reservations"
        / f"{case_prefix}.camera-reservation.json"
    )


def _case_namespace_artifacts(
    output_root: Path,
    case_prefix: str,
    report_path: Path,
    digest_path: Path,
) -> tuple[Path, ...]:
    """Return every existing path owned by a case-prefix namespace."""

    candidates = [
        report_path,
        digest_path,
        _case_reservation_path(output_root, case_prefix),
    ]
    patterns = (
        ("frames", f"{case_prefix}-*.raw"),
        ("previews", f"{case_prefix}-*.bmp"),
        ("drafts", f"{case_prefix}-*.json"),
    )
    for directory, pattern in patterns:
        candidates.extend(sorted((output_root / directory).glob(pattern)))
    return tuple(path for path in candidates if path.exists())


def _preflight_case_namespace(
    output_root: Path,
    case_prefix: str,
    report_path: Path,
    digest_path: Path,
) -> None:
    """Reject any previously used case-prefix before capture or input."""

    existing = _case_namespace_artifacts(
        output_root,
        case_prefix,
        report_path,
        digest_path,
    )
    if existing:
        raise FileExistsError(existing[0])


def _reserve_case_namespace(
    output_root: Path,
    case_prefix: str,
    report_path: Path,
    digest_path: Path,
    *,
    git_head_sha: str,
) -> Path:
    """Durably make a case prefix single-use while the input lease is held."""

    _preflight_case_namespace(
        output_root,
        case_prefix,
        report_path,
        digest_path,
    )
    reservation_path = _case_reservation_path(output_root, case_prefix)
    reservation_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "case_prefix": case_prefix,
        "git_head_sha": git_head_sha,
        "owner": "validate_varrock_east_camera.py",
        "schema_version": 1,
    }
    with reservation_path.open("xb") as reservation:
        reservation.write(
            (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
        )
        reservation.flush()
        os.fsync(reservation.fileno())
    return reservation_path


def _retract_report_targets_after_lease_failure(
    report_path: Path,
    digest_path: Path,
) -> tuple[str, ...]:
    """Remove only this run's newly published canonical report artifacts."""

    errors: list[str] = []
    for path in (digest_path, report_path):
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            errors.append(f"could not retract {path}: {exc}")
    return tuple(errors)


def _exact_command_argv(command_args: list[str]) -> tuple[str, ...]:
    return (
        str(Path(sys.executable).resolve()),
        str(Path(__file__).resolve()),
        *command_args,
    )


def _evaluation_dict(evaluation: CameraEvaluation) -> dict[str, Any]:
    return {
        "detector_id": evaluation.detector_id,
        "detector_version": evaluation.detector_version,
        "profile_id": evaluation.profile_id,
        "profile_schema_version": evaluation.profile_schema_version,
        "profile_geometry": {
            "width": evaluation.profile_frame_width,
            "height": evaluation.profile_frame_height,
            "pixel_format": evaluation.profile_pixel_format.value,
        },
        "frame_geometry_supported": evaluation.frame_geometry_supported,
        "scene": {
            "validated": evaluation.scene_validated,
            "reason": evaluation.scene_reason,
            "matched_landmarks": evaluation.matched_landmark_count,
            "configured_landmarks": evaluation.required_landmark_count,
            "required_landmark_matches": evaluation.required_landmark_matches,
            "matched_zones": [zone.value for zone in evaluation.matched_zones],
            "required_zones": evaluation.required_matched_zones,
            "landmarks": [
                {
                    "landmark_id": item.landmark_id,
                    "distance": item.distance,
                    "threshold": item.threshold,
                    "matched": item.matched,
                    "zone": item.zone.value,
                }
                for item in evaluation.landmarks
            ],
        },
        "resources": [
            {
                "resource_id": item.resource_id,
                "state": item.state.value,
                "confidence": item.confidence,
                "definitive": item.definitive,
            }
            for item in evaluation.resource_states
        ],
        "definitive_target_ids": list(evaluation.definitive_target_ids),
        "passed": evaluation.passed,
    }


def _action_dict(action: CameraAction) -> dict[str, Any]:
    if isinstance(action, CompassClick):
        return {"kind": "compass_click", "x": action.x, "y": action.y}
    if isinstance(action, CameraKeyHold):
        return {
            "kind": "key_hold",
            "key": action.key.value,
            "duration_s": action.duration_s,
            "post_release_settle_s": CAMERA_KEY_RELEASE_SETTLE_SECONDS,
            "post_release_verification": (
                "semantic_client_consumption_wait_then_observable_key_up_and_"
                "target_focus_identity_geometry"
            ),
        }
    if isinstance(action, CameraPause):
        return {"kind": "pause", "duration_s": action.duration_s}
    if isinstance(action, CameraMiddleDrag):
        path = camera_drag_path(action)
        return {
            "kind": "camera_middle_drag",
            "axis": action.axis.value,
            "pixels": action.pixels,
            "coordinate_space": "runelite_target_logical_client",
            "start": [action.start_x, action.start_y],
            "reviewed_open_viewport": _drag_open_viewport_dict(),
            "path": [[x, y] for x, y in path],
            "step_count": action.step_count,
            "max_step_pixels": MAX_CAMERA_DRAG_STEP_PIXELS,
            "arming_settle_s": CAMERA_MIDDLE_ARMING_SETTLE_SECONDS,
            "post_move_settle_s": CAMERA_DRAG_STEP_INTERVAL_SECONDS,
            "final_move_settle_included": True,
            "post_release_settle_s": CAMERA_MIDDLE_RELEASE_SETTLE_SECONDS,
            "post_release_verification": (
                "middle_up_focus_geometry_cursor_and_target_root"
            ),
        }
    if isinstance(action, ResetZoomKey):
        return {
            "kind": "reset_zoom_key",
            "key": action.key,
            "dwell_s": action.dwell_s,
            "post_release_settle_s": CAMERA_KEY_RELEASE_SETTLE_SECONDS,
            "post_release_verification": (
                "semantic_client_consumption_wait_then_observable_key_up_and_"
                "target_focus_identity_geometry"
            ),
        }
    return {
        "kind": "camera_wheel",
        "x": action.x,
        "y": action.y,
        "detents": action.detents,
    }


def _plan_dict(plan: CameraPlan) -> dict[str, Any]:
    return {"name": plan.name, "actions": [_action_dict(action) for action in plan.actions]}


def _plan_input_event_count(plan: CameraPlan) -> int:
    total = 0
    for action in plan.actions:
        if isinstance(action, CompassClick | CameraKeyHold | ResetZoomKey):
            total += 2
        elif isinstance(action, CameraMiddleDrag):
            total += 2 + action.step_count
        elif isinstance(action, CameraWheel):
            total += abs(action.detents)
    return total


def _worst_case_bounds(
    candidates: tuple[CameraPlan, ...],
    perturbations: tuple[CameraPlan, ...],
    *,
    confirmations: int,
) -> dict[str, int]:
    normalization_boundaries = 1 + len(perturbations)
    normalization_plan_executions = len(candidates) * normalization_boundaries
    normalization_input_events = (
        sum(_plan_input_event_count(plan) for plan in candidates)
        * normalization_boundaries
    )
    perturbation_input_events = sum(
        _plan_input_event_count(plan) for plan in perturbations
    )
    return {
        "normalization_candidates_per_boundary": len(candidates),
        "normalization_boundaries": normalization_boundaries,
        "normalization_plan_executions": normalization_plan_executions,
        "normalization_input_events": normalization_input_events,
        "perturbation_input_events": perturbation_input_events,
        "total_input_events": normalization_input_events
        + perturbation_input_events,
        "candidate_evaluation_frames": normalization_plan_executions,
        "required_confirmation_frames": len(perturbations) * confirmations,
        "maximum_protocol_frames": normalization_plan_executions
        + len(perturbations) * (2 + confirmations),
    }


def _input_receipt_dict(receipt: CameraInputReceipt) -> dict[str, Any]:
    return {
        "operation": receipt.operation.value,
        "requested_events": receipt.requested_events,
        "completed_events": receipt.completed_events,
        "complete": receipt.complete,
    }


def _plan_receipt_dict(receipt: CameraPlanReceipt) -> dict[str, Any]:
    return {
        "plan": _plan_dict(receipt.plan),
        "preflight": {
            "focused": receipt.preflight.focused,
            "client_width": receipt.preflight.client_width,
            "client_height": receipt.preflight.client_height,
            "supported": receipt.preflight.supported,
        },
        "actions": [
            {
                "action_index": item.action_index,
                "action": _action_dict(item.action),
                "input_receipts": [
                    _input_receipt_dict(input_receipt)
                    for input_receipt in item.input_receipts
                ],
            }
            for item in receipt.action_receipts
        ],
    }


def _frame_record_dict(
    record: CameraFrameRecord,
    *,
    resource_states_match_expected: bool | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "artifact": {
            "label": record.artifact.label,
            "frame_id": record.artifact.frame_id,
            "width": record.artifact.width,
            "height": record.artifact.height,
            "pixel_format": record.artifact.pixel_format,
            "raw_sha256": record.artifact.raw_sha256,
            "files": dict(record.artifact.files),
        },
        "production": _evaluation_dict(record.evaluation),
    }
    if resource_states_match_expected is not None:
        payload["resource_states_match_expected"] = (
            resource_states_match_expected
        )
    return payload


def _readiness_dict(readiness: ClientInputReadiness) -> dict[str, Any]:
    return {
        "evaluator_id": readiness.evaluator_id,
        "evaluator_version": readiness.evaluator_version,
        "reason": readiness.reason.value,
        "detail": readiness.detail,
        "safe_to_attempt_camera_input": readiness.safe_to_attempt_camera_input,
        "can_accept": readiness.can_accept,
        "can_validate_scene": readiness.can_validate_scene,
        "can_expose_resources": readiness.can_expose_resources,
        "anchors": [
            {
                "anchor_id": anchor.policy.anchor_id,
                "region": list(anchor.policy.region),
                "thresholds": {
                    "minimum_luma_stddev": anchor.policy.minimum_luma_stddev,
                    "minimum_edge_density": anchor.policy.minimum_edge_density,
                    "maximum_dark_fraction": anchor.policy.maximum_dark_fraction,
                },
                "metrics": {
                    "luma_stddev": anchor.luma_stddev,
                    "edge_density": anchor.edge_density,
                    "dark_fraction": anchor.dark_fraction,
                },
                "matched": anchor.matched,
            }
            for anchor in readiness.anchors
        ],
    }


def _bootstrap_frame_dict(frame: CameraServoFrameEvidence) -> dict[str, Any]:
    return {
        "artifact": {
            "label": frame.artifact.label,
            "frame_id": frame.artifact.frame_id,
            "captured_monotonic_s": frame.captured_monotonic_s,
            "width": frame.artifact.width,
            "height": frame.artifact.height,
            "pixel_format": frame.artifact.pixel_format,
            "raw_sha256": frame.artifact.raw_sha256,
            "files": dict(frame.artifact.files),
        },
        "readiness": _readiness_dict(frame.readiness),
        "production": (
            None if frame.production is None else _evaluation_dict(frame.production)
        ),
    }


def _guidance_v2_dict(guidance: WorldCameraGuidanceV2) -> dict[str, Any]:
    base = guidance.base_guidance
    fit = base.fit
    analysis = base.analysis
    shared = None if analysis is None else analysis.best_shared
    transform = guidance.transform_error
    return {
        "selector_id": guidance.selector_id,
        "selector_version": guidance.selector_version,
        "disposition": guidance.disposition.value,
        "reason": guidance.reason.value,
        "detail": guidance.detail,
        "decision_frame": {
            "frame_id": guidance.decision_frame_id,
            "captured_monotonic_s": guidance.decision_captured_monotonic_s,
            "raw_sha256": guidance.decision_raw_sha256,
        },
        "heading_was_normalized": guidance.heading_was_normalized,
        "axis": None if guidance.axis is None else guidance.axis.value,
        "direction": None if guidance.direction is None else guidance.direction.value,
        "transform_error": (
            None
            if transform is None
            else {
                "log_scale": transform.log_scale,
                "rotation": transform.rotation,
                "horizontal_shift": transform.horizontal_shift,
                "vertical_shift": transform.vertical_shift,
                "norm": transform.norm,
            }
        ),
        "can_accept": guidance.can_accept,
        "can_validate_scene": guidance.can_validate_scene,
        "can_expose_resources": guidance.can_expose_resources,
        "base_guidance": {
            "selector_id": base.selector_id,
            "selector_version": base.selector_version,
            "disposition": base.disposition.value,
            "reason": base.reason.value,
            "detail": base.detail,
            "axis": None if base.axis is None else base.axis.value,
            "direction": None if base.direction is None else base.direction.value,
            "fit": (
                None
                if fit is None
                else {
                    "scale": fit.scale,
                    "rotation_degrees": fit.rotation_degrees,
                    "centre_shift_x": fit.centre_shift_x,
                    "centre_shift_y": fit.centre_shift_y,
                    "rms_residual_px": fit.rms_residual_px,
                    "maximum_residual_px": fit.maximum_residual_px,
                    "landmark_count": fit.landmark_count,
                    "matched_zones": [zone.value for zone in fit.matched_zones],
                }
            ),
            "analysis": (
                None
                if analysis is None
                else {
                    "diagnosis": analysis.diagnosis.value,
                    "detail": analysis.detail,
                    "search_radius": analysis.search_radius,
                    "coarse_step": analysis.coarse_step,
                    "refinement_radius": analysis.refinement_radius,
                    "matched_count": analysis.matched_count,
                    "matched_zones": [
                        zone.value for zone in analysis.matched_zones
                    ],
                    "landmarks": [
                        {
                            "landmark_id": landmark.landmark_id,
                            "offset_x": landmark.offset_x,
                            "offset_y": landmark.offset_y,
                            "distance": landmark.distance,
                            "maximum_distance": landmark.maximum_distance,
                            "normalized_distance": landmark.normalized_distance,
                            "matched": landmark.matched,
                            "zone": landmark.zone.value,
                            "searched_offsets": landmark.searched_offsets,
                        }
                        for landmark in analysis.landmarks
                    ],
                    "best_shared": (
                        None
                        if shared is None
                        else {
                            "offset_x": shared.offset_x,
                            "offset_y": shared.offset_y,
                            "matched_count": shared.matched_count,
                            "matched_zones": [
                                zone.value for zone in shared.matched_zones
                            ],
                            "required_quorum": shared.required_quorum,
                            "required_zones": shared.required_zones,
                            "valid_landmark_count": shared.valid_landmark_count,
                            "normalized_distance_sum": (
                                shared.normalized_distance_sum
                            ),
                            "validated_diagnostic_only": shared.validated,
                        }
                    ),
                }
            ),
            "excluded_regions": [list(region) for region in base.excluded_regions],
            "can_accept": base.can_accept,
            "can_validate_scene": base.can_validate_scene,
            "can_expose_resources": base.can_expose_resources,
        },
    }


def _world_guidance_dict(guidance: WorldCameraGuidance) -> dict[str, Any]:
    analysis = guidance.analysis
    shared = None if analysis is None else analysis.best_shared
    fit = guidance.fit
    return {
        "selector_id": guidance.selector_id,
        "selector_version": guidance.selector_version,
        "disposition": guidance.disposition.value,
        "reason": guidance.reason.value,
        "detail": guidance.detail,
        "axis": None if guidance.axis is None else guidance.axis.value,
        "direction": None if guidance.direction is None else guidance.direction.value,
        "fit": (
            None
            if fit is None
            else {
                "scale": fit.scale,
                "rotation_degrees": fit.rotation_degrees,
                "centre_shift_x": fit.centre_shift_x,
                "centre_shift_y": fit.centre_shift_y,
                "rms_residual_px": fit.rms_residual_px,
                "maximum_residual_px": fit.maximum_residual_px,
                "landmark_count": fit.landmark_count,
                "matched_zones": [zone.value for zone in fit.matched_zones],
            }
        ),
        "analysis": (
            None
            if analysis is None
            else {
                "diagnosis": analysis.diagnosis.value,
                "detail": analysis.detail,
                "search_radius": analysis.search_radius,
                "coarse_step": analysis.coarse_step,
                "refinement_radius": analysis.refinement_radius,
                "matched_count": analysis.matched_count,
                "matched_zones": [zone.value for zone in analysis.matched_zones],
                "landmarks": [
                    {
                        "landmark_id": landmark.landmark_id,
                        "offset_x": landmark.offset_x,
                        "offset_y": landmark.offset_y,
                        "distance": landmark.distance,
                        "maximum_distance": landmark.maximum_distance,
                        "normalized_distance": landmark.normalized_distance,
                        "matched": landmark.matched,
                        "zone": landmark.zone.value,
                        "searched_offsets": landmark.searched_offsets,
                    }
                    for landmark in analysis.landmarks
                ],
                "best_shared": (
                    None
                    if shared is None
                    else {
                        "offset_x": shared.offset_x,
                        "offset_y": shared.offset_y,
                        "matched_count": shared.matched_count,
                        "matched_zones": [
                            zone.value for zone in shared.matched_zones
                        ],
                        "required_quorum": shared.required_quorum,
                        "required_zones": shared.required_zones,
                        "valid_landmark_count": shared.valid_landmark_count,
                        "normalized_distance_sum": shared.normalized_distance_sum,
                        "validated_diagnostic_only": shared.validated,
                    }
                ),
            }
        ),
        "excluded_regions": [list(region) for region in guidance.excluded_regions],
        "can_accept": guidance.can_accept,
        "can_validate_scene": guidance.can_validate_scene,
        "can_expose_resources": guidance.can_expose_resources,
    }


def _system_id_observation_dict(
    observation: CameraSystemIdObservation,
) -> dict[str, Any]:
    return {
        "frame": _bootstrap_frame_dict(observation.evidence),
        "world_only_diagnostic": _world_guidance_dict(observation.guidance),
    }


def _system_id_landmark_comparison_dict(
    item: CameraSystemIdLandmarkComparison,
) -> dict[str, Any]:
    def search(value: Any) -> dict[str, Any]:
        return {
            "offset_x": value.offset_x,
            "offset_y": value.offset_y,
            "distance": value.distance,
            "maximum_distance": value.maximum_distance,
            "normalized_distance": value.normalized_distance,
            "matched": value.matched,
            "searched_offsets": value.searched_offsets,
        }

    return {
        "landmark_id": item.landmark_id,
        "zone": item.zone.value,
        "strict_matches": {
            "baseline_one": search(item.baseline_one),
            "baseline_two": search(item.baseline_two),
            "positive_arm": search(item.positive_arm),
            "positive_commit": search(item.positive_commit),
            "positive_post": search(item.positive),
            "return_arm": search(item.return_arm),
            "return_commit": search(item.return_commit),
            "return_post": search(item.returned),
        },
        "vectors": {
            "baseline_one_to_two": [
                item.baseline_delta_x,
                item.baseline_delta_y,
            ],
            "no_input_same_pose_samples": [
                [delta_x, delta_y] for delta_x, delta_y in item.no_input_deltas
            ],
            "positive_commit_to_post": [
                item.positive_delta_x,
                item.positive_delta_y,
            ],
            "return_commit_to_post": [item.return_delta_x, item.return_delta_y],
            "positive_commit_to_final_residual": [
                item.return_residual_x,
                item.return_residual_y,
            ],
        },
        "magnitudes_px": {
            "maximum_natural_offset_jitter": item.baseline_jitter_px,
            "positive_commit_to_post": item.positive_magnitude_px,
            "return_commit_to_post": item.return_magnitude_px,
            "final_residual": item.return_residual_px,
        },
        "tested_axis": {
            "maximum_natural_jitter": item.tested_axis_baseline_jitter,
            "positive_commit_to_post": item.tested_axis_positive_delta,
            "return_commit_to_post": item.tested_axis_return_delta,
        },
        "descriptor_stability": {
            "same_pose_distance_deltas": list(
                item.no_input_descriptor_deltas
            ),
            "maximum_natural_distance_jitter": item.descriptor_jitter,
            "minimum_threshold_margin": item.minimum_descriptor_margin,
            "stable": item.descriptor_stable,
        },
        "strictly_matched_in_all_frames": item.strictly_matched,
        "above_baseline_jitter": item.above_baseline_jitter,
        "opposite_return": item.opposite_return,
        "opposite_vector_return": item.opposite_vector_return,
        "closed_inside_baseline_envelope": item.closed_inside_baseline_envelope,
        "qualified": item.qualified,
    }


def _system_id_comparison_dict(
    comparison: CameraSystemIdComparison,
) -> dict[str, Any]:
    return {
        "axis": comparison.axis.value,
        "required_landmarks": comparison.required_landmarks,
        "required_zones": comparison.required_zones,
        "common_matched_zones": [
            zone.value for zone in comparison.common_matched_zones
        ],
        "qualified_landmark_ids": list(comparison.qualified_landmark_ids),
        "qualified_zones": [zone.value for zone in comparison.qualified_zones],
        "coherent_forward_sign": comparison.coherent_forward_sign,
        "derivative_usable": comparison.derivative_usable,
        "detail": comparison.detail,
        "landmarks": [
            _system_id_landmark_comparison_dict(item)
            for item in comparison.landmarks
        ],
    }


def _arm_guard_dict(guard: CameraArmGuardResult) -> dict[str, Any]:
    return {
        "guard_id": guard.guard_id,
        "guard_version": guard.guard_version,
        "disposition": guard.disposition.value,
        "reason": guard.reason.value,
        "detail": guard.detail,
        "decision_frame": {
            "frame_id": guard.decision_frame_id,
            "captured_monotonic_s": guard.decision_captured_monotonic_s,
            "raw_sha256": guard.decision_payload_sha256,
        },
        "arm_frame": {
            "frame_id": guard.arm_frame_id,
            "captured_monotonic_s": guard.arm_captured_monotonic_s,
            "raw_sha256": guard.arm_payload_sha256,
        },
        "regions": [
            {
                "landmark_id": region.landmark_id,
                "zone": region.zone.value,
                "region": list(region.region),
                "compared_pixel_count": region.compared_pixel_count,
                "total_pixel_count": region.total_pixel_count,
                "mean_absolute_channel_delta": region.mean_absolute_channel_delta,
                "changed_pixel_fraction": region.changed_pixel_fraction,
                "within_limit": region.within_limit,
            }
            for region in guard.regions
        ],
        "evaluated_zones": [zone.value for zone in guard.evaluated_zones],
        "stable_landmark_count": guard.stable_landmark_count,
        "stable_zones": [zone.value for zone in guard.stable_zones],
        "excluded_regions": [list(region) for region in guard.excluded_regions],
        "compared_pixel_count": guard.compared_pixel_count,
        "mean_absolute_channel_delta": guard.mean_absolute_channel_delta,
        "changed_pixel_fraction": guard.changed_pixel_fraction,
        "safe_to_retain_guidance": guard.safe_to_retain_guidance,
        "can_accept": guard.can_accept,
        "can_validate_scene": guard.can_validate_scene,
        "can_expose_resources": guard.can_expose_resources,
    }


def _arm_age_dict(age: CameraServoArmAgeEvidence) -> dict[str, Any]:
    return {
        "origin_clock_s": age.origin_clock_s,
        "final_clock_s": age.final_clock_s,
        "age_s": age.age_s,
        "maximum_age_s": age.maximum_age_s,
        "status": age.status.value,
    }


def _system_id_step_dict(step: CameraSystemIdStepResult) -> dict[str, Any]:
    return {
        "axis": step.axis.value,
        "direction": step.direction,
        "terminal_reason": step.terminal_reason.value,
        "detail": step.detail,
        "input_state": step.input_state.value,
        "decision": _system_id_observation_dict(step.decision),
        "arm": None if step.arm is None else _bootstrap_frame_dict(step.arm),
        "arm_world_observation": (
            None
            if step.arm_observation is None
            else _system_id_observation_dict(step.arm_observation)
        ),
        "commit": (
            None if step.commit is None else _bootstrap_frame_dict(step.commit)
        ),
        "commit_world_observation": (
            None
            if step.commit_observation is None
            else _system_id_observation_dict(step.commit_observation)
        ),
        "post": (
            None if step.post is None else _system_id_observation_dict(step.post)
        ),
        "plan": _plan_dict(step.plan),
        "preflight": (
            None
            if step.preflight is None
            else {
                "focused": step.preflight.focused,
                "client_width": step.preflight.client_width,
                "client_height": step.preflight.client_height,
                "supported": step.preflight.supported,
            }
        ),
        "guards": {
            "decision_to_arm": (
                None
                if step.arm_guard is None
                else _arm_guard_dict(step.arm_guard)
            ),
            "arm_to_commit": (
                None
                if step.commit_guard is None
                else _arm_guard_dict(step.commit_guard)
            ),
            "decision_to_commit": (
                None
                if step.decision_commit_guard is None
                else _arm_guard_dict(step.decision_commit_guard)
            ),
        },
        "arm_age": None if step.arm_age is None else _arm_age_dict(step.arm_age),
        "receipt": (
            None if step.receipt is None else _plan_receipt_dict(step.receipt)
        ),
        "timing": {
            "input_start_clock_s": step.input_start_clock_s,
            "input_receipt_clock_s": step.input_receipt_clock_s,
            "input_delivery_duration_s": step.input_delivery_duration_s,
        },
        "exception": (
            None
            if step.exception is None
            else {
                "type": step.exception.exception_type,
                "message": step.exception.detail,
            }
        ),
    }


def _system_id_axis_dict(axis: CameraSystemIdAxisResult) -> dict[str, Any]:
    return {
        "axis": axis.axis.value,
        "detail": axis.detail,
        "complete": axis.complete,
        "baseline_one": (
            None
            if axis.baseline_one is None
            else _system_id_observation_dict(axis.baseline_one)
        ),
        "baseline_two": (
            None
            if axis.baseline_two is None
            else _system_id_observation_dict(axis.baseline_two)
        ),
        "baseline_guard": (
            None
            if axis.baseline_guard is None
            else _arm_guard_dict(axis.baseline_guard)
        ),
        "positive_step": (
            None
            if axis.positive_step is None
            else _system_id_step_dict(axis.positive_step)
        ),
        "return_step": (
            None
            if axis.return_step is None
            else _system_id_step_dict(axis.return_step)
        ),
        "comparison": (
            None
            if axis.comparison is None
            else _system_id_comparison_dict(axis.comparison)
        ),
    }


def _system_id_result_dict(
    result: CameraSystemIdResult,
    *,
    tracked_worktree_clean: bool,
    adapter_identity: str,
) -> dict[str, Any]:
    steps = tuple(
        step
        for axis in (result.horizontal, result.vertical)
        if axis is not None
        for step in (axis.positive_step, axis.return_step)
        if step is not None
    )
    return {
        "command": _FIXED_SYSTEM_ID_COMMAND,
        "development_only": True,
        "system_identification": {
            "id": CAMERA_SYSTEM_ID_ID,
            "version": CAMERA_SYSTEM_ID_VERSION,
            "conclusive": result.conclusive,
            "conclusion": (
                None if result.conclusion is None else result.conclusion.value
            ),
            "detail": result.detail,
        },
        "fixed_policy": {
            "drag_point": list(REVIEWED_CAMERA_DRAG_POINT),
            "logical_pixels": CAMERA_SYSTEM_ID_DRAG_PIXELS,
            "order": [
                "horizontal_positive",
                "horizontal_return",
                "vertical_positive_if_horizontal_usable",
                "vertical_return_if_horizontal_usable",
            ],
            "post_action_settle_s": CAMERA_SYSTEM_ID_SETTLE_SECONDS,
            "maximum_physical_primitives": 4,
            "caller_selectable_axis": False,
            "caller_selectable_direction": False,
            "caller_selectable_magnitude": False,
            "caller_selectable_coordinate": False,
        },
        "pointer_mapping": {
            "adapter_identity": adapter_identity,
            "expected_adapter_identity": _EXPECTED_WINDOWS_CAMERA_ADAPTER,
            "coordinate_space": "target_logical_client_pixels",
            "reviewed_start": list(REVIEWED_CAMERA_DRAG_POINT),
            "reviewed_open_viewport": _drag_open_viewport_dict(),
            "numeric_physical_mapping_captured": False,
            "adapter_contract": {
                "unique_target_root_required": True,
                "logical_to_physical_mapping_rechecked": True,
                "target_root_ownership_rechecked_before_middle_down": True,
                "target_root_ownership_rechecked_for_every_held_move": True,
                "target_root_ownership_rechecked_before_and_after_middle_up": True,
                "exact_geometry_and_focus_required": True,
                "middle_release_observable": True,
            },
            "steps": [
                {
                    "axis": step.axis.value,
                    "direction": step.direction,
                    "plan": step.plan.name,
                    "complete_receipt": step.receipt is not None,
                    "middle_release_acknowledged": (
                        step.receipt is not None
                        and step.receipt.action_receipts[-1].input_receipts[-1].complete
                    ),
                }
                for step in steps
            ],
        },
        "authority": {
            "scene_acceptance": "unchanged_production_camera_evaluation_only",
            "diagnostic_registration_can_override_production": False,
            "calibration_can_expose_resources": False,
        },
        "identity_policy": {
            "detector_id": _EXPECTED_DETECTOR_ID,
            "detector_version": _EXPECTED_DETECTOR_VERSION,
            "profile_id": _EXPECTED_PROFILE_ID,
            "profile_schema_version": _EXPECTED_PROFILE_SCHEMA_VERSION,
            "guidance_id": CAMERA_GUIDANCE_ID,
            "guidance_version": CAMERA_GUIDANCE_VERSION,
        },
        "horizontal": _system_id_axis_dict(result.horizontal),
        "vertical": (
            None if result.vertical is None else _system_id_axis_dict(result.vertical)
        ),
        "tracked_worktree_clean": tracked_worktree_clean,
        "issue31_acceptance_claimed": False,
    }


def _bootstrap_result_dict(
    result: CameraNorthBootstrapResult,
    *,
    tracked_worktree_clean: bool,
) -> dict[str, Any]:
    return {
        "command": _NORTH_BOOTSTRAP_COMMAND,
        "development_only": True,
        "identity_policy": {
            "detector_id": _EXPECTED_DETECTOR_ID,
            "detector_version": _EXPECTED_DETECTOR_VERSION,
            "profile_id": _EXPECTED_PROFILE_ID,
            "profile_schema_version": _EXPECTED_PROFILE_SCHEMA_VERSION,
            "guidance_v2_id": _EXPECTED_GUIDANCE_V2_ID,
            "guidance_v2_version": _EXPECTED_GUIDANCE_V2_VERSION,
        },
        "camera_assumptions": {
            "compass_point": list(REVIEWED_COMPASS_POINT),
            "compass_click_dwell_s": COMPASS_CLICK_DWELL_SECONDS,
            "post_action_settle_s": _NORTH_BOOTSTRAP_SETTLE_SECONDS,
            "maximum_semantic_actions": 1,
            "permitted_action": "compass_click",
            "diagnostics_can_override_production": False,
        },
        "frames": {
            "initial": (
                None if result.initial is None else _bootstrap_frame_dict(result.initial)
            ),
            "arm": None if result.arm is None else _bootstrap_frame_dict(result.arm),
            "commit": (
                None if result.commit is None else _bootstrap_frame_dict(result.commit)
            ),
            "post": None if result.post is None else _bootstrap_frame_dict(result.post),
        },
        "guidance": (
            None if result.guidance is None else _guidance_v2_dict(result.guidance)
        ),
        "post_guidance": (
            None
            if result.post_guidance is None
            else _guidance_v2_dict(result.post_guidance)
        ),
        "plan": None if result.plan is None else _plan_dict(result.plan),
        "guards": {
            "decision_to_arm": (
                None
                if result.arm_guard is None
                else _arm_guard_dict(result.arm_guard)
            ),
            "arm_to_commit": (
                None
                if result.commit_guard is None
                else _arm_guard_dict(result.commit_guard)
            ),
            "decision_to_commit": (
                None
                if result.decision_commit_guard is None
                else _arm_guard_dict(result.decision_commit_guard)
            ),
        },
        "arm_age": (
            None if result.arm_age is None else _arm_age_dict(result.arm_age)
        ),
        "preflight": (
            None
            if result.preflight is None
            else {
                "focused": result.preflight.focused,
                "client_width": result.preflight.client_width,
                "client_height": result.preflight.client_height,
                "supported": result.preflight.supported,
            }
        ),
        "receipt": (
            None if result.receipt is None else _plan_receipt_dict(result.receipt)
        ),
        "input": {
            "state": result.input_state.value,
            "attempted": result.input_attempted,
            "completed": result.input_completed,
            "start_clock_s": result.input_start_clock_s,
            "receipt_clock_s": result.input_receipt_clock_s,
            "delivery_duration_s": result.input_delivery_duration_s,
        },
        "pointer_mapping": {
            "adapter_identity": (
                "mining_automation.validation.windows_camera.WindowsCameraControl"
            ),
            "reviewed_logical_point": {
                "x": REVIEWED_COMPASS_POINT[0],
                "y": REVIEWED_COMPASS_POINT[1],
                "coordinate_space": "target_logical_client_pixels",
            },
            "preflight": (
                None
                if result.preflight is None
                else {
                    "focused": result.preflight.focused,
                    "client_width": result.preflight.client_width,
                    "client_height": result.preflight.client_height,
                    "supported": result.preflight.supported,
                }
            ),
            "receipt_backed_target_root_policy": {
                "complete_compass_receipt": result.receipt is not None,
                "discovery_identity_bound_to_control": True,
                "target_root_rechecked_before_button_down": (
                    result.receipt is not None
                ),
                "target_root_rechecked_during_dwell_before_button_up": (
                    result.receipt is not None
                ),
                "numeric_mapping_captured": False,
                "physical_screen_point": None,
                "target_root_handle_recorded": False,
                "claim": (
                    "A complete receipt proves the live WindowsCameraControl "
                    "completed its target-root and identity rechecks; this report "
                    "does not capture or infer a numeric logical-to-physical mapping."
                ),
            },
        },
        "exception": (
            None
            if result.exception is None
            else {
                "type": result.exception.exception_type,
                "message": result.exception.detail,
            }
        ),
        "terminal_reason": result.terminal_reason.value,
        "detail": result.detail,
        "acceptance": {
            "authority": "unchanged_production_evaluator_only",
            "passed": result.passed,
            "input_receipt_is_acceptance": False,
            "capture_is_acceptance": False,
        },
        "tracked_worktree_clean": tracked_worktree_clean,
        "camera_evidence_eligible": result.passed and tracked_worktree_clean,
        "combined_issue31_acceptance": {
            "complete": False,
            "reviewed_live_resource_states_included": False,
            "same_head_drift_proof_included": False,
        },
    }


def _normalization_result_dict(
    result: CameraNormalizationResult,
) -> dict[str, Any]:
    return {
        "attempts": [
            {
                "index_1_based": attempt.index,
                "identity": attempt.plan.name,
                "plan": _plan_dict(attempt.plan),
                "receipt": _plan_receipt_dict(attempt.receipt),
                "candidate_frame": _frame_record_dict(attempt.frame),
                "production_gate_passed": attempt.passed,
                "counts_as_confirmation": False,
            }
            for attempt in result.attempts
        ],
        "selected_candidate_index_1_based": (
            result.selected_candidate_index_1_based
        ),
        "selected_identity": result.selected_identity,
        "production_gate_passed": result.passed,
    }


def _session_dict(
    result: CameraSessionResult,
    args: argparse.Namespace,
    *,
    strategy_id: str,
    strategy_version: str,
    tracked_worktree_clean: bool,
) -> dict[str, Any]:
    camera_evidence_eligible = result.passed and tracked_worktree_clean
    production_gated_search = (
        args.normalization_strategy == _PRODUCTION_GATED_STRATEGY_ID
    )
    return {
        "camera_assumptions": {
            "compass_point": list(_COMPASS_POINT),
            "drag_point": list(REVIEWED_CAMERA_DRAG_POINT),
            "wheel_point": list(_CAMERA_WHEEL_POINT),
            "pointer_coordinate_space": "runelite_target_logical_client",
            "compass_click_dwell_s": COMPASS_CLICK_DWELL_SECONDS,
            "key_release_settle_s": CAMERA_KEY_RELEASE_SETTLE_SECONDS,
            "key_release_verification": (
                "semantic_client_consumption_wait_then_observable_key_up_and_"
                "target_focus_identity_geometry"
            ),
            "pitch_endpoint": "up" if production_gated_search else args.pitch_endpoint,
            "pitch_hold_s": _DEFAULT_PITCH_HOLD_S,
            "pitch_offset_hold_s": (
                "candidate_specific"
                if production_gated_search
                else args.pitch_offset_hold
            ),
            "yaw_offset_direction": (
                "right" if production_gated_search else args.yaw_offset_direction
            ),
            "yaw_offset_hold_s": (
                "candidate_specific"
                if production_gated_search
                else args.yaw_offset_hold
            ),
            "yaw_drag_pixels": 0 if production_gated_search else args.yaw_drag_pixels,
            "pitch_drag_pixels": (
                0 if production_gated_search else args.pitch_drag_pixels
            ),
            "drag_delivery": (
                "preflight_complete_logical_corridor_then_middle_down_arming_"
                "and_post_move_settle_including_final"
            ),
            "drag_coordinate_space": "runelite_target_logical_client",
            "drag_open_viewport": _drag_open_viewport_dict(),
            "drag_max_pixels": MAX_CAMERA_DRAG_PIXELS,
            "drag_max_step_pixels": MAX_CAMERA_DRAG_STEP_PIXELS,
            "drag_path_excludes_start": True,
            "drag_arming_settle_s": CAMERA_MIDDLE_ARMING_SETTLE_SECONDS,
            "drag_post_move_settle_s": CAMERA_DRAG_STEP_INTERVAL_SECONDS,
            "drag_final_move_settle_included": True,
            "drag_post_release_settle_s": CAMERA_MIDDLE_RELEASE_SETTLE_SECONDS,
            "drag_post_release_verification": (
                "middle_up_focus_geometry_cursor_and_target_root"
            ),
            "post_compass_settle_s": (
                _DEFAULT_POST_COMPASS_SETTLE_S
                if production_gated_search
                else args.post_compass_settle
            ),
            "zoom_mode": (
                "wheel_endpoint"
                if production_gated_search or not args.reset_zoom
                else "reset_key"
            ),
            "zoom_saturate_detents": (
                _PRODUCTION_GATED_ZOOM_SATURATION
                if production_gated_search
                else args.zoom_saturate_detents
            ),
            "zoom_offset_detents": (
                _PRODUCTION_GATED_ZOOM_OFFSET
                if production_gated_search
                else args.zoom_offset_detents
            ),
            "wheel_delivery": "paced_individual_detents",
            "wheel_pointer_button_gate": (
                "left_and_middle_before_and_after_relocation"
            ),
            "wheel_event_interval_s": CAMERA_WHEEL_EVENT_INTERVAL_SECONDS,
            "diagnostics_can_override_production": False,
        },
        "normalization_strategy": {
            "id": strategy_id,
            "version": strategy_version,
            "selection_authority": "unchanged_production_camera_evaluation",
            "diagnostic_registration_used": False,
            "candidates": [
                {
                    "index_1_based": index,
                    **_plan_dict(plan),
                }
                for index, plan in enumerate(
                    result.normalization_candidates,
                    start=1,
                )
            ],
            "worst_case_bounds": _worst_case_bounds(
                result.normalization_candidates,
                tuple(trial.perturbation_plan for trial in result.trials)
                if len(result.trials) == result.required_trials
                else _build_perturbation_plans(args),
                confirmations=result.required_confirmations,
            ),
        },
        "initial_normalization": _normalization_result_dict(
            result.initial_normalization
        ),
        "required_trials": result.required_trials,
        "required_confirmations": result.required_confirmations,
        "pre_perturbation_failure": (
            {
                "trial_index_1_based": (
                    result.pre_perturbation_failure_trial_index_1_based
                ),
                "frame": _frame_record_dict(result.pre_perturbation_failure),
                "further_input_sent": False,
            }
            if result.pre_perturbation_failure is not None
            else None
        ),
        "trials": [
            {
                "trial_index": trial.trial_index,
                "before": _frame_record_dict(trial.before),
                "expected_resource_state_vector": [
                    {
                        "resource_id": resource_id,
                        "state": state.value,
                    }
                    for resource_id, state in trial.expected_resource_state_vector
                ],
                "perturbation_plan": _plan_dict(trial.perturbation_plan),
                "perturbation_receipt": _plan_receipt_dict(trial.perturbation_receipt),
                "perturbed": _frame_record_dict(trial.perturbed),
                "perturbation_fail_closed": trial.perturbation_fail_closed,
                "normalization": _normalization_result_dict(trial.normalization),
                "confirmations": [
                    _frame_record_dict(
                        confirmation,
                        resource_states_match_expected=states_match,
                    )
                    for confirmation, states_match in zip(
                        trial.confirmations,
                        trial.confirmation_state_matches,
                        strict=True,
                    )
                ],
                "passed": trial.passed,
            }
            for trial in result.trials
        ],
        "camera_protocol_passed": result.passed,
        "tracked_worktree_clean": tracked_worktree_clean,
        "camera_evidence_eligible": camera_evidence_eligible,
        "combined_issue31_acceptance": {
            "complete": False,
            "reviewed_live_resource_states_included": False,
            "same_head_drift_proof_included": False,
        },
    }


def _last_production_evaluation(result: CameraSessionResult) -> CameraEvaluation:
    """Return the latest serialized evaluation for provenance on pass or fail."""

    if result.pre_perturbation_failure is not None:
        return result.pre_perturbation_failure.evaluation
    if result.trials:
        trial = result.trials[-1]
        if trial.confirmations:
            return trial.confirmations[-1].evaluation
        return trial.normalization.attempts[-1].frame.evaluation
    return result.initial_normalization.attempts[-1].frame.evaluation


def _print_summary(
    result: CameraSessionResult,
    *,
    camera_evidence_eligible: bool,
    tracked_worktree_clean: bool,
    report_path: Path,
    report_sha256: str,
    git_head_sha: str,
) -> None:
    print(
        "CAMERA PROTOCOL EVIDENCE: "
        f"{'ELIGIBLE' if camera_evidence_eligible else 'INELIGIBLE'} "
        f"(protocol_passed={result.passed}; clean={tracked_worktree_clean})"
    )
    print(f"Head: {git_head_sha}")
    initial = result.initial_normalization
    print(
        "  initial normalization: "
        f"attempts={len(initial.attempts)}; "
        f"selected={initial.selected_candidate_index_1_based}; "
        f"pass={initial.passed}"
    )
    for trial in result.trials:
        confirmations = ", ".join(
            f"{item.evaluation.matched_landmark_count}/6"
            for item in trial.confirmations
        )
        print(
            f"  trial {trial.trial_index}: perturb fail-closed="
            f"{trial.perturbation_fail_closed}; confirmations={confirmations}; "
            "normalization="
            f"{trial.normalization.selected_candidate_index_1_based}; "
            f"pass={trial.passed}"
        )
    if result.pre_perturbation_failure is not None:
        print(
            "  pre-perturbation production failure: trial="
            f"{result.pre_perturbation_failure_trial_index_1_based}; "
            "no further input sent"
        )
    print(f"Report: {report_path}")
    print(f"Report SHA-256: {report_sha256}")


def _print_north_bootstrap_summary(
    result: CameraNorthBootstrapResult,
    *,
    report_path: Path,
    report_sha256: str,
    git_head_sha: str,
) -> None:
    print(
        "UNCHANGED PRODUCTION CAMERA EVIDENCE: "
        f"{'PASSED' if result.passed else 'NOT PASSED'}"
    )
    print(f"Head: {git_head_sha}")
    print(f"Terminal reason: {result.terminal_reason.value}")
    print(f"Input state: {result.input_state.value}")
    print(f"Report: {report_path}")
    print(f"Report SHA-256: {report_sha256}")


def _run_live_north_bootstrap(
    *,
    output_root: Path,
    report_path: Path,
    digest_path: Path,
    case_prefix: str,
    git_head_before: str,
    command_argv: tuple[str, ...],
    publication_state: _ReportPublicationState,
) -> int:
    """Run the one-action V2 boundary while the caller owns the input lease."""

    backend: WindowsCaptureBackend | None = None
    source: CaptureSource | None = None
    control: WindowsCameraControl | None = None
    result: CameraNorthBootstrapResult | None = None
    selected_hwnd: int | None = None
    selected_process_id: int | None = None
    selected_thread_id: int | None = None
    selected_class_name: str | None = None
    selected_title_sha256: str | None = None
    handled_error: Exception | None = None
    unhandled_error: BaseException | None = None
    cleanup_errors: list[str] = []
    try:
        git_head_inside, clean_inside = _git_state()
        if git_head_inside != git_head_before or not clean_inside:
            raise RuntimeError(
                "Git HEAD/worktree changed before the leased bootstrap boundary"
            )
        _reserve_case_namespace(
            output_root,
            case_prefix,
            report_path,
            digest_path,
            git_head_sha=git_head_before,
        )
        backend = WindowsCaptureBackend(title_substring=DEFAULT_TITLE_SUBSTRING)
        source = CaptureSource(backend, max_consecutive_failures=1)
        source.open()
        # Resolve and bind the exact RuneLite HWND. This discovery frame is not
        # evidence; the bootstrap runner captures and records its own decision.
        source.capture()
        selected = backend.selected_window
        if selected is None:
            raise RuntimeError("capture succeeded without a selected RuneLite window")
        selected_hwnd = selected.hwnd
        selected_class_name = selected.class_name
        selected_title_sha256 = hashlib.sha256(
            selected.title.encode("utf-8")
        ).hexdigest()
        recorder = _PrivateArtifactRecorder(
            output_root,
            case_prefix=case_prefix,
            git_head_sha=git_head_before,
            plan_id=_EXPECTED_GUIDANCE_V2_ID,
            plan_version=_EXPECTED_GUIDANCE_V2_VERSION,
        )
        control = WindowsCameraControl(
            selected.hwnd,
            expected_class_name=selected.class_name,
            expected_title=selected.title,
        )
        selected_process_id = control.target_identity.process_id
        selected_thread_id = control.target_identity.thread_id

        def require_same_clean_head_before_input(
            initial: CameraServoFrameEvidence,
            arm: CameraServoFrameEvidence,
            commit: CameraServoFrameEvidence,
        ) -> None:
            current_head, current_clean = _git_state()
            if current_head != git_head_before or not current_clean:
                raise RuntimeError(
                    "Git HEAD/worktree changed before the physical input seam"
                )
            for stage, evidence in (
                ("initial", initial),
                ("arm", arm),
                ("commit", commit),
            ):
                if evidence.production is None:
                    raise RuntimeError(
                        f"{stage} production evidence is missing at the input seam"
                    )
                _require_north_bootstrap_production_identity(
                    stage,
                    evidence.production,
                )

        result = run_camera_north_bootstrap(
            source,
            control,
            sleeper=time.sleep,
            settle_s=_NORTH_BOOTSTRAP_SETTLE_SECONDS,
            recorder=recorder,
            pre_input_guard=require_same_clean_head_before_input,
            final_input_guard=require_same_clean_head_before_input,
        )
    except (
        CaptureError,
        FileExistsError,
        OSError,
        RuntimeError,
        ValueError,
        WindowsCameraError,
        subprocess.CalledProcessError,
    ) as exc:
        handled_error = exc
    except BaseException as exc:
        unhandled_error = exc
    finally:
        if control is not None:
            try:
                control.release_all_held_keys()
            except (OSError, RuntimeError, WindowsCameraError) as exc:
                cleanup_errors.append(f"camera input cleanup failed: {exc}")
        if source is not None:
            try:
                source.close()
            except (CaptureError, OSError, RuntimeError) as exc:
                cleanup_errors.append(f"capture cleanup failed: {exc}")

    if handled_error is not None:
        print(f"North bootstrap failed: {handled_error}", file=sys.stderr)
        if cleanup_errors:
            print("; ".join(cleanup_errors), file=sys.stderr)
        return 2
    if unhandled_error is not None:
        if cleanup_errors:
            print("; ".join(cleanup_errors), file=sys.stderr)
        raise unhandled_error
    if cleanup_errors:
        print("; ".join(cleanup_errors), file=sys.stderr)
        return 2
    if (
        result is None
        or selected_hwnd is None
        or selected_process_id is None
        or selected_thread_id is None
        or selected_class_name is None
        or selected_title_sha256 is None
    ):  # pragma: no cover - defensive composition guard
        print("North bootstrap produced no result.", file=sys.stderr)
        return 2

    try:
        git_head_after, clean_after = _git_state()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Cannot re-establish Git provenance: {exc}", file=sys.stderr)
        return 2
    if git_head_after != git_head_before or not clean_after:
        print(
            "Git HEAD/worktree changed during north bootstrap; refusing report.",
            file=sys.stderr,
        )
        return 2
    try:
        _require_north_bootstrap_result_identities(result)
        provenance = CameraReportProvenance(
            git_head_sha=git_head_before,
            detector_id=_EXPECTED_DETECTOR_ID,
            detector_version=_EXPECTED_DETECTOR_VERSION,
            profile_id=_EXPECTED_PROFILE_ID,
            plan_id=_EXPECTED_GUIDANCE_V2_ID,
            plan_version=_EXPECTED_GUIDANCE_V2_VERSION,
            command_argv=command_argv,
            tracked_worktree_clean=True,
        )
        bootstrap_evidence = _bootstrap_result_dict(
            result,
            tracked_worktree_clean=True,
        )
        bootstrap_evidence["selected_window_binding"] = {
            "adapter_identity": _EXPECTED_WINDOWS_CAMERA_ADAPTER,
            "class_name": selected_class_name,
            "hwnd": selected_hwnd,
            "process_id": selected_process_id,
            "thread_id": selected_thread_id,
            "title_sha256": selected_title_sha256,
            "title_substring": DEFAULT_TITLE_SUBSTRING,
        }
        written = write_camera_validation_report(
            report_path,
            bootstrap_evidence,
            provenance,
        )
        publication_state.published_by_this_invocation = True
    except (FileExistsError, OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"Cannot write north-bootstrap report: {exc}", file=sys.stderr)
        return 2

    _print_north_bootstrap_summary(
        result,
        report_path=written.report_path,
        report_sha256=written.sha256,
        git_head_sha=git_head_before,
    )
    return 0 if result.passed else 1


def _main_north_bootstrap(command_args: list[str]) -> int:
    """Validate and execute the isolated V2 subcommand."""

    args = _build_north_bootstrap_parser().parse_args(command_args[1:])
    try:
        case_prefix = _validate_case_prefix(args.case_prefix)
        command_argv = _exact_command_argv(command_args)
        _validate_command_argv(command_argv)
        _require_north_bootstrap_runtime_identities()
        output_root = _resolve_private_output_root(args.output)
        report_path, digest_path = _report_paths(output_root, case_prefix)
        _preflight_case_namespace(
            output_root,
            case_prefix,
            report_path,
            digest_path,
        )
        git_head_before, clean_before = _git_state()
        if re.fullmatch(r"[0-9a-f]{40}", git_head_before) is None:
            raise ValueError("Git HEAD must be a full lowercase 40-character SHA")
    except (
        FileExistsError,
        OSError,
        RuntimeError,
        subprocess.CalledProcessError,
        ValueError,
    ) as exc:
        print(f"Cannot establish north-bootstrap provenance: {exc}", file=sys.stderr)
        return 2
    if not clean_before:
        print(
            "Refusing north-bootstrap input unless the worktree is exactly clean.",
            file=sys.stderr,
        )
        return 2

    lease = WindowsCameraInputLease()
    lease_entered = False
    publication_state = _ReportPublicationState()
    try:
        with lease:
            lease_entered = True
            return _run_live_north_bootstrap(
                output_root=output_root,
                report_path=report_path,
                digest_path=digest_path,
                case_prefix=case_prefix,
                git_head_before=git_head_before,
                command_argv=command_argv,
                publication_state=publication_state,
            )
    except CameraInputLeaseError as exc:
        retraction_errors: tuple[str, ...] = ()
        if (
            lease_entered
            and lease.acquired
            and publication_state.published_by_this_invocation
        ):
            retraction_errors = _retract_report_targets_after_lease_failure(
                report_path,
                digest_path,
            )
        print(f"North-bootstrap input lease unavailable: {exc}", file=sys.stderr)
        if retraction_errors:
            print("; ".join(retraction_errors), file=sys.stderr)
        return 2
    except BaseException as exc:
        if (
            lease_entered
            and lease.acquired
            and publication_state.published_by_this_invocation
        ):
            for retraction_error in _retract_report_targets_after_lease_failure(
                report_path,
                digest_path,
            ):
                exc.add_note(retraction_error)
        raise


def _print_fixed_system_id_summary(
    result: CameraSystemIdResult,
    *,
    report_path: Path,
    report_sha256: str,
    git_head_sha: str,
) -> None:
    print(f"Fixed system identification: {'CONCLUSIVE' if result.conclusive else 'INCONCLUSIVE'}")
    print(
        "Conclusion: "
        + ("none" if result.conclusion is None else result.conclusion.value)
    )
    for axis in (result.horizontal, result.vertical):
        if axis is None:
            continue
        comparison = axis.comparison
        print(f"{axis.axis.value}: {'complete' if axis.complete else 'incomplete'}")
        if comparison is not None:
            print(
                "  strict common landmarks: "
                f"{sum(item.strictly_matched for item in comparison.landmarks)}; "
                f"qualified: {len(comparison.qualified_landmark_ids)}; "
                f"zones: {[zone.value for zone in comparison.qualified_zones]}"
            )
            for item in comparison.landmarks:
                print(
                    f"  {item.landmark_id} [{item.zone.value}] "
                    f"jitter_bound={item.baseline_jitter_px:.3f}px "
                    f"forward=({item.positive_delta_x:+d},{item.positive_delta_y:+d}) "
                    f"return=({item.return_delta_x:+d},{item.return_delta_y:+d}) "
                    f"residual=({item.return_residual_x:+d},{item.return_residual_y:+d}) "
                    f"qualified={item.qualified}"
                )
    print(f"Git HEAD: {git_head_sha}")
    print(f"Report: {report_path}")
    print(f"Report SHA-256: {report_sha256}")


def _run_live_fixed_system_id(
    *,
    output_root: Path,
    report_path: Path,
    digest_path: Path,
    case_prefix: str,
    git_head_before: str,
    command_argv: tuple[str, ...],
    publication_state: _ReportPublicationState,
) -> int:
    """Run the fixed A/B/A boundary while the caller owns the input lease."""

    backend: WindowsCaptureBackend | None = None
    source: CaptureSource | None = None
    control: WindowsCameraControl | None = None
    adapter_identity: str | None = None
    result: CameraSystemIdResult | None = None
    handled_error: Exception | None = None
    unhandled_error: BaseException | None = None
    cleanup_errors: list[str] = []
    try:
        git_head_inside, clean_inside = _git_state()
        if git_head_inside != git_head_before or not clean_inside:
            raise RuntimeError(
                "Git HEAD/worktree changed before the leased system-ID boundary"
            )
        _reserve_case_namespace(
            output_root,
            case_prefix,
            report_path,
            digest_path,
            git_head_sha=git_head_before,
        )
        backend = WindowsCaptureBackend(title_substring=DEFAULT_TITLE_SUBSTRING)
        source = CaptureSource(backend, max_consecutive_failures=1)
        source.open()
        source.capture()
        selected = backend.selected_window
        if selected is None:
            raise RuntimeError("capture succeeded without a selected RuneLite window")
        recorder = _PrivateArtifactRecorder(
            output_root,
            case_prefix=case_prefix,
            git_head_sha=git_head_before,
            plan_id=CAMERA_SYSTEM_ID_ID,
            plan_version=CAMERA_SYSTEM_ID_VERSION,
        )
        control = WindowsCameraControl(
            selected.hwnd,
            expected_class_name=selected.class_name,
            expected_title=selected.title,
        )
        adapter_identity = f"{type(control).__module__}.{type(control).__qualname__}"
        if adapter_identity != _EXPECTED_WINDOWS_CAMERA_ADAPTER:
            raise RuntimeError("fixed-system-ID camera adapter identity mismatch")

        def require_same_clean_head_before_input(
            decision: CameraServoFrameEvidence,
            arm: CameraServoFrameEvidence,
            commit: CameraServoFrameEvidence,
        ) -> None:
            current_head, current_clean = _git_state()
            if current_head != git_head_before or not current_clean:
                raise RuntimeError(
                    "Git HEAD/worktree changed before the physical input seam"
                )
            for stage, evidence in (
                ("decision", decision),
                ("arm", arm),
                ("commit", commit),
            ):
                if evidence.production is None:
                    raise RuntimeError(
                        f"{stage} production evidence is missing at the input seam"
                    )
                _require_north_bootstrap_production_identity(
                    stage,
                    evidence.production,
                )

        result = run_fixed_camera_system_identification(
            source,
            control,
            sleeper=time.sleep,
            recorder=recorder,
            pre_input_guard=require_same_clean_head_before_input,
            final_input_guard=require_same_clean_head_before_input,
        )
    except (
        CaptureError,
        FileExistsError,
        OSError,
        RuntimeError,
        ValueError,
        WindowsCameraError,
        subprocess.CalledProcessError,
    ) as exc:
        handled_error = exc
    except BaseException as exc:
        unhandled_error = exc
    finally:
        if control is not None:
            try:
                control.release_all_held_keys()
            except (OSError, RuntimeError, WindowsCameraError) as exc:
                cleanup_errors.append(f"camera input cleanup failed: {exc}")
        if source is not None:
            try:
                source.close()
            except (CaptureError, OSError, RuntimeError) as exc:
                cleanup_errors.append(f"capture cleanup failed: {exc}")

    if handled_error is not None:
        print(f"Fixed system identification failed: {handled_error}", file=sys.stderr)
        if cleanup_errors:
            print("; ".join(cleanup_errors), file=sys.stderr)
        return 2
    if unhandled_error is not None:
        if cleanup_errors:
            print("; ".join(cleanup_errors), file=sys.stderr)
        raise unhandled_error
    if cleanup_errors:
        print("; ".join(cleanup_errors), file=sys.stderr)
        return 2
    if result is None or adapter_identity is None:  # pragma: no cover
        print("Fixed system identification produced no result.", file=sys.stderr)
        return 2
    try:
        git_head_after, clean_after = _git_state()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Cannot re-establish Git provenance: {exc}", file=sys.stderr)
        return 2
    if git_head_after != git_head_before or not clean_after:
        print(
            "Git HEAD/worktree changed during fixed system identification; "
            "refusing report.",
            file=sys.stderr,
        )
        return 2
    try:
        _require_fixed_system_id_result_identities(result)
        provenance = CameraReportProvenance(
            git_head_sha=git_head_before,
            detector_id=_EXPECTED_DETECTOR_ID,
            detector_version=_EXPECTED_DETECTOR_VERSION,
            profile_id=_EXPECTED_PROFILE_ID,
            plan_id=CAMERA_SYSTEM_ID_ID,
            plan_version=CAMERA_SYSTEM_ID_VERSION,
            command_argv=command_argv,
            tracked_worktree_clean=True,
        )
        written = write_camera_validation_report(
            report_path,
            _system_id_result_dict(
                result,
                tracked_worktree_clean=True,
                adapter_identity=adapter_identity,
            ),
            provenance,
        )
        publication_state.published_by_this_invocation = True
    except (FileExistsError, OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"Cannot write fixed-system-ID report: {exc}", file=sys.stderr)
        return 2
    _print_fixed_system_id_summary(
        result,
        report_path=written.report_path,
        report_sha256=written.sha256,
        git_head_sha=git_head_before,
    )
    return 0 if result.conclusive else 1


def _main_fixed_system_id(command_args: list[str]) -> int:
    """Validate and execute the isolated fixed A/B/A subcommand."""

    args = _build_fixed_system_id_parser().parse_args(command_args[1:])
    try:
        case_prefix = _validate_case_prefix(args.case_prefix)
        command_argv = _exact_command_argv(command_args)
        _validate_command_argv(command_argv)
        _require_fixed_system_id_runtime_identities()
        output_root = _resolve_private_output_root(args.output)
        report_path, digest_path = _report_paths(output_root, case_prefix)
        _preflight_case_namespace(
            output_root,
            case_prefix,
            report_path,
            digest_path,
        )
        git_head_before, clean_before = _git_state()
        if re.fullmatch(r"[0-9a-f]{40}", git_head_before) is None:
            raise ValueError("Git HEAD must be a full lowercase 40-character SHA")
    except (
        FileExistsError,
        OSError,
        RuntimeError,
        subprocess.CalledProcessError,
        ValueError,
    ) as exc:
        print(f"Cannot establish fixed-system-ID provenance: {exc}", file=sys.stderr)
        return 2
    if not clean_before:
        print(
            "Refusing fixed-system-ID input unless the worktree is exactly clean.",
            file=sys.stderr,
        )
        return 2

    lease = WindowsCameraInputLease()
    lease_entered = False
    publication_state = _ReportPublicationState()
    try:
        with lease:
            lease_entered = True
            return _run_live_fixed_system_id(
                output_root=output_root,
                report_path=report_path,
                digest_path=digest_path,
                case_prefix=case_prefix,
                git_head_before=git_head_before,
                command_argv=command_argv,
                publication_state=publication_state,
            )
    except CameraInputLeaseError as exc:
        retraction_errors: tuple[str, ...] = ()
        if (
            lease_entered
            and lease.acquired
            and publication_state.published_by_this_invocation
        ):
            retraction_errors = _retract_report_targets_after_lease_failure(
                report_path,
                digest_path,
            )
        print(f"Fixed-system-ID input lease unavailable: {exc}", file=sys.stderr)
        if retraction_errors:
            print("; ".join(retraction_errors), file=sys.stderr)
        return 2
    except BaseException as exc:
        if (
            lease_entered
            and lease.acquired
            and publication_state.published_by_this_invocation
        ):
            for retraction_error in _retract_report_targets_after_lease_failure(
                report_path,
                digest_path,
            ):
                exc.add_note(retraction_error)
        raise


def _bridge_closure_exception(error: BaseException) -> CameraServoExceptionEvidence:
    detail = str(error).strip() or repr(error)
    return CameraServoExceptionEvidence(type(error).__name__, detail)


def _new_bridge_post_transition_closure(
    status: CameraBridgePostTransitionStatus,
    detail: str,
    **kwargs: object,
) -> CameraBridgePostTransitionClosure:
    """Bind every closure outcome to the frozen planner objective."""

    return CameraBridgePostTransitionClosure(
        status=status,
        detail=detail,
        objective_id=_BRIDGE_OBJECTIVE_ID,
        objective_source_sha256=FROZEN_ENDPOINT_SOURCE_SHA256,
        **kwargs,  # type: ignore[arg-type]
    )


def _evaluate_bridge_post_transition(
    result: CameraBridgeCaptureResult,
    *,
    output_root: Path,
    registration_engine: RobustRegistrationEngine,
) -> tuple[
    CameraBridgeCaptureResult,
    CameraBridgePostTransitionClosure,
    RobustWorldRegistration | None,
    CameraEvaluation | None,
]:
    """Close exact post evidence before sealing, without granting authority."""

    if not result.input_attempted:
        return (
            result,
            _new_bridge_post_transition_closure(
                CameraBridgePostTransitionStatus.NOT_REQUIRED,
                "No physical bridge input was attempted; no transition exists.",
            ),
            None,
            None,
        )
    receipt_proven = (
        result.input_completed
        and result.receipt is not None
        and result.receipt.plan is camera_bridge_capture_plan()
    )
    if not receipt_proven or result.commit is None or result.post is None:
        return (
            result,
            _new_bridge_post_transition_closure(
                CameraBridgePostTransitionStatus.PHYSICAL_CAPTURE_INCOMPLETE,
                (
                    "Physical input state is retained, but no complete exact "
                    "receipt/commit/post chain exists."
                ),
                action_bridge_receipt_proven=receipt_proven,
                bridge_rejected=True,
            ),
            None,
            None,
        )

    commit_sha256 = result.commit.artifact.raw_sha256
    post_sha256 = result.post.artifact.raw_sha256
    try:
        commit_frame = _bridge_evidence_frame(output_root, result.commit)
        post_frame = _bridge_evidence_frame(output_root, result.post)
    except Exception as error:
        return (
            result,
            _new_bridge_post_transition_closure(
                CameraBridgePostTransitionStatus.ARTIFACT_ERROR,
                "Exact commit/post artifact re-read failed closed.",
                commit_sha256=commit_sha256,
                post_sha256=post_sha256,
                action_bridge_receipt_proven=True,
                bridge_rejected=True,
                artifact_exception=_bridge_closure_exception(error),
            ),
            None,
            None,
        )

    registration: RobustWorldRegistration | None = None
    registration_error: BaseException | None = None
    registration_accepted = False
    try:
        registration = registration_engine.analyze(commit_frame, post_frame)
        _require_bridge_starting_registration(
            registration,
            expected_source_sha256=commit_sha256,
            expected_target_sha256=post_sha256,
            context="exact live commit to immediate post-action frame",
        )
        registration_accepted = True
    except Exception as error:
        registration_error = error

    # O3 ordering is deliberate: physical cleanup has already completed;
    # production runs only after this registration attempt. Registration
    # rejection/exception never suppresses production for a ready exact post.
    production: CameraEvaluation | None = None
    production_error: BaseException | None = None
    post_readiness = evaluate_client_input_readiness(post_frame)
    if post_readiness != result.post.readiness:
        return (
            result,
            _new_bridge_post_transition_closure(
                CameraBridgePostTransitionStatus.POST_READINESS_REJECTED,
                "Exact post readiness changed during closure re-evaluation.",
                commit_sha256=commit_sha256,
                post_sha256=post_sha256,
                action_bridge_receipt_proven=True,
                registration_attempted=True,
                registration_accepted=registration_accepted,
                registration_bridge_observed=registration_accepted,
                bridge_rejected=True,
                registration_exception=(
                    None
                    if registration_error is None
                    else _bridge_closure_exception(registration_error)
                ),
            ),
            registration,
            None,
        )
    if not post_readiness.safe_to_attempt_camera_input:
        finalized, _ = _finalize_camera_bridge_post_production(result, post_frame)
        return (
            finalized,
            _new_bridge_post_transition_closure(
                CameraBridgePostTransitionStatus.POST_READINESS_REJECTED,
                (
                    "Exact post readiness vetoed production under the established "
                    "production-evaluation policy."
                ),
                commit_sha256=commit_sha256,
                post_sha256=post_sha256,
                action_bridge_receipt_proven=True,
                registration_attempted=True,
                registration_accepted=registration_accepted,
                registration_bridge_observed=registration_accepted,
                bridge_rejected=True,
                registration_exception=(
                    None
                    if registration_error is None
                    else _bridge_closure_exception(registration_error)
                ),
            ),
            registration,
            None,
        )
    try:
        finalized, production = _finalize_camera_bridge_post_production(
            result,
            post_frame,
        )
        if production is None:  # pragma: no cover - readiness handled above
            raise RuntimeError("ready exact post produced no production evaluation")
        _require_north_bootstrap_production_identity(
            "post-transition-production-evaluation",
            production,
        )
    except Exception as error:
        production_error = error
    if production_error is not None:
        detail = (
            "Post-transition production evaluation failed closed after registration."
        )
        if registration_error is not None:
            detail += " Registration also rejected or raised."
        return (
            result,
            _new_bridge_post_transition_closure(
                CameraBridgePostTransitionStatus.PRODUCTION_EXCEPTION,
                detail,
                commit_sha256=commit_sha256,
                post_sha256=post_sha256,
                action_bridge_receipt_proven=True,
                registration_attempted=True,
                registration_accepted=registration_accepted,
                registration_bridge_observed=registration_accepted,
                bridge_rejected=True,
                registration_exception=(
                    None
                    if registration_error is None
                    else _bridge_closure_exception(registration_error)
                ),
                production_exception=_bridge_closure_exception(production_error),
            ),
            registration,
            None,
        )
    assert production is not None
    if registration_error is not None:
        status = (
            CameraBridgePostTransitionStatus.REGISTRATION_EXCEPTION
            if registration is None
            else CameraBridgePostTransitionStatus.REGISTRATION_REJECTED
        )
        return (
            finalized,
            _new_bridge_post_transition_closure(
                status,
                (
                    "Post-transition production was re-evaluated exactly, but "
                    "diagnostic registration rejected or raised."
                ),
                commit_sha256=commit_sha256,
                post_sha256=post_sha256,
                action_bridge_receipt_proven=True,
                registration_attempted=True,
                registration_accepted=False,
                registration_bridge_observed=False,
                production_re_evaluated=True,
                production_matches_capture=True,
                production_supported_endpoint=production.passed,
                bridge_rejected=True,
                registration_exception=_bridge_closure_exception(registration_error),
            ),
            registration,
            production,
        )
    if commit_sha256 == post_sha256:
        return (
            finalized,
            _new_bridge_post_transition_closure(
                CameraBridgePostTransitionStatus.NO_DISTINCT_ENDPOINT,
                (
                    "The complete receipt produced no distinct post endpoint; "
                    "registration and production evidence are retained, but the "
                    "bridge is rejected."
                ),
                commit_sha256=commit_sha256,
                post_sha256=post_sha256,
                action_bridge_receipt_proven=True,
                registration_attempted=True,
                registration_accepted=True,
                registration_bridge_observed=True,
                production_re_evaluated=True,
                production_matches_capture=True,
                production_supported_endpoint=production.passed,
                bridge_rejected=True,
            ),
            registration,
            production,
        )
    if not finalized.protocol_completed:
        return (
            finalized,
            _new_bridge_post_transition_closure(
                CameraBridgePostTransitionStatus.PRODUCTION_REJECTED,
                (
                    "The sole post production evaluation did not reach an "
                    "accepted fail-closed capture or supported endpoint; the "
                    "physical receipt is retained and the bridge is rejected."
                ),
                commit_sha256=commit_sha256,
                post_sha256=post_sha256,
                action_bridge_receipt_proven=True,
                registration_attempted=True,
                registration_accepted=True,
                registration_bridge_observed=True,
                production_re_evaluated=True,
                production_matches_capture=True,
                production_supported_endpoint=False,
                bridge_rejected=True,
            ),
            registration,
            production,
        )
    return (
        finalized,
        _new_bridge_post_transition_closure(
            CameraBridgePostTransitionStatus.COMPLETE,
            (
                "Exact commit/post registration and subsequent production "
                "re-evaluation completed before report sealing."
            ),
            commit_sha256=commit_sha256,
            post_sha256=post_sha256,
            action_bridge_receipt_proven=True,
            registration_attempted=True,
            registration_accepted=True,
            registration_bridge_observed=True,
            production_re_evaluated=True,
            production_matches_capture=True,
            production_supported_endpoint=production.passed,
            bridge_rejected=False,
        ),
        registration,
        production,
    )


def _bridge_capture_evidence(
    result: CameraBridgeCaptureResult,
    *,
    analysis_evidence: _BridgeAnalysisEvidence,
    authorization_reservation: CameraBridgeAuthorizationReservation | None,
    adapter_identity: str,
    campaign_precursor: _BridgeCampaignPrecursor,
    precursor_to_commit_registration: RobustWorldRegistration | None,
    reservation_completed_clock_s: float | None,
    planner_source_registration: RobustWorldRegistration | None,
    post_transition_closure: CameraBridgePostTransitionClosure,
    post_transition_production: CameraEvaluation | None,
    post_transition_registration: RobustWorldRegistration | None,
    pointer_evidence: _BridgePointerEvidence | None,
    selected_class_name: str,
    selected_title: str,
) -> dict[str, object]:
    evidence = result.as_dict()
    campaign_precursor_evidence = campaign_precursor.as_dict()
    campaign_precursor_evidence["campaign_reservation_id"] = (
        None
        if authorization_reservation is None
        else authorization_reservation.sentinel_sha256
    )
    campaign_precursor_evidence["reservation_completed_clock_s"] = (
        reservation_completed_clock_s
    )
    evidence["transition_candidate_eligible"] = False
    evidence["action_transition_emitted"] = False
    evidence["authenticated_ingestion_required"] = True
    evidence["same_transaction_closure_completed"] = (
        post_transition_closure.completed
    )
    bridge_capture = _json_object(
        evidence.get("bridge_capture"),
        "R2 bridge capture evidence",
    )
    bridge_capture["physical_capture_protocol_completed"] = (
        result.protocol_completed
    )
    bridge_capture["post_transition_closure_completed"] = (
        post_transition_closure.completed
    )
    evidence.update(
        {
            "bridge_objective": {
                "first_missing_primitive": {
                    "duration_seconds": CAMERA_BRIDGE_CAPTURE_HOLD_SECONDS,
                    "key": "right",
                },
                "id": _BRIDGE_OBJECTIVE_ID,
                "prior_endpoint_report_sha256s": list(
                    _BRIDGE_OBJECTIVE_REPORT_SHA256S
                ),
                "selection_rule": (
                    "smallest receipt-proven step in the only repeated endpoint "
                    "family with all-zone diagnostic links to one common frozen "
                    "supported anchor"
                ),
            },
            "command": _BRIDGE_CAPTURE_COMMAND,
            "development_only": True,
            "analysis_evidence": analysis_evidence.as_dict(),
            "campaign_authorization": (
                None
                if authorization_reservation is None
                else authorization_reservation.as_dict()
            ),
            "campaign_precursor": campaign_precursor_evidence,
            "precursor_to_commit_registration": (
                None
                if precursor_to_commit_registration is None
                else precursor_to_commit_registration.as_dict()
            ),
            "planner_source_registration": (
                None
                if planner_source_registration is None
                else planner_source_registration.as_dict()
            ),
            "post_transition_closure": post_transition_closure.as_dict(),
            "post_transition_production_re_evaluation": (
                None
                if post_transition_production is None
                else _evaluation_dict(post_transition_production)
            ),
            "post_transition_registration": (
                None
                if post_transition_registration is None
                else post_transition_registration.as_dict()
            ),
            "new_live_input_from_robust_registration": False,
            "pointer_mapping": {
                "adapter_identity": adapter_identity,
                "evidence": (
                    None if pointer_evidence is None else pointer_evidence.as_dict()
                ),
                "numeric_mapping_captured": pointer_evidence is not None,
                "pointer_primitive_required": False,
                "reviewed_logical_point": list(REVIEWED_CAMERA_WHEEL_POINT),
                "selected_window_class_name": selected_class_name,
                "selected_window_title_sha256": hashlib.sha256(
                    selected_title.encode("utf-8")
                ).hexdigest(),
            },
            "post_capture_registration_required": True,
            "registration_execution": {
                "precursor_to_commit_executed_in_input_seam": (
                    precursor_to_commit_registration is not None
                ),
                "planner_source_to_precursor_precomputed_before_arm": (
                    planner_source_registration is not None
                ),
                "post_transition_registration_performed": (
                    post_transition_closure.registration_attempted
                ),
                "post_transition_registration_stage": (
                    "same_transaction_before_production_re_evaluation_and_report_seal"
                    if post_transition_closure.registration_attempted
                    else "not_applicable_without_complete_physical_receipt"
                ),
                "production_re_evaluated_after_registration": (
                    post_transition_closure.production_re_evaluated
                ),
            },
            "production_detector_remains_sole_scene_authority": True,
            "registration_role": (
                "reviewed precondition and post-transition evidence only; fixed "
                "reviewed analysis artifact selects the experiment and unchanged "
                "production remains sole scene authority"
            ),
            "robust_registration_executed_in_input_seam": (
                precursor_to_commit_registration is not None
            ),
            "robust_registration_can_authorize_input_alone": False,
            "tracked_worktree_clean": True,
        }
    )
    if authorization_reservation is not None:
        if reservation_completed_clock_s is None:
            raise RuntimeError("R2.3 reservation is missing its completion clock")
        evidence["ordered_campaign_receipt"] = _ordered_campaign_receipt(
            campaign_precursor,
            result,
            reservation=authorization_reservation,
            reservation_completed_clock_s=reservation_completed_clock_s,
        )
    else:
        evidence["ordered_campaign_receipt"] = None
    return evidence


def _capture_campaign_precursor_frame(
    source: CaptureSource,
    recorder: _PrivateArtifactRecorder,
) -> tuple[Frame, CameraServoFrameEvidence]:
    frame = source.capture()
    artifact = recorder("r2-campaign-precursor", frame)
    readiness = evaluate_client_input_readiness(frame)
    production = (
        evaluate_varrock_east_camera(frame)
        if readiness.safe_to_attempt_camera_input
        else None
    )
    return frame, CameraServoFrameEvidence(
        artifact=artifact,
        captured_monotonic_s=frame.captured_monotonic_s,
        readiness=readiness,
        production=production,
    )


def _require_fail_closed_campaign_frame(
    evidence: CameraServoFrameEvidence,
    *,
    context: str,
) -> None:
    if not evidence.readiness.safe_to_attempt_camera_input:
        raise RuntimeError(f"{context} lost client input readiness")
    production = evidence.production
    if production is None:
        raise RuntimeError(f"{context} has no production evaluation")
    _require_north_bootstrap_production_identity(context, production)
    if (
        production.passed
        or production.scene_validated
        or production.definitive_target_ids
        or not production.resource_states
        or any(item.state.value != "uncertain" for item in production.resource_states)
    ):
        raise RuntimeError(f"{context} is not production-fail-closed")


def _ordered_campaign_receipt(
    precursor: _BridgeCampaignPrecursor,
    result: CameraBridgeCaptureResult,
    *,
    reservation: CameraBridgeAuthorizationReservation,
    reservation_completed_clock_s: float,
) -> dict[str, object]:
    """Serialize the immutable optional-compass then fixed-Right receipt."""

    if (
        isinstance(reservation_completed_clock_s, bool)
        or not isinstance(reservation_completed_clock_s, (int, float))
        or not math.isfinite(float(reservation_completed_clock_s))
        or float(reservation_completed_clock_s) < 0.0
    ):
        raise RuntimeError("R2.3 reservation completion clock is invalid")
    bootstrap = precursor.bootstrap
    if bootstrap is None:
        if precursor.north_qualification is None:
            raise RuntimeError("zero-click precursor lacks exact north qualification")
        precursor_commit_sha256 = precursor.frame_evidence.artifact.raw_sha256
        precursor_post_sha256 = precursor_commit_sha256
        precursor_receipt: dict[str, object] | None = {
            "kind": "zero_click_observation",
            "physical_input_attempted": False,
            "physical_input_completed": False,
            "frame_sha256": precursor_commit_sha256,
            "source_registration_sha256": (
                canonical_camera_bridge_component_sha256(
                    precursor.registration.as_dict()
                )
            ),
            "north_qualification_sha256": (
                canonical_camera_bridge_component_sha256(
                    precursor.north_qualification.as_dict()
                )
            ),
        }
        precursor_input_state = "none"
        precursor_input_start = None
        precursor_input_receipt = None
    else:
        if (
            bootstrap.commit is None
            or bootstrap.post is None
            or bootstrap.input_state is not CameraNorthBootstrapInputState.COMPLETE
            or bootstrap.receipt is None
            or bootstrap.input_start_clock_s is None
            or reservation_completed_clock_s > bootstrap.input_start_clock_s
        ):
            raise RuntimeError("compass precursor lacks exact commit/post evidence")
        precursor_commit_sha256 = bootstrap.commit.artifact.raw_sha256
        precursor_post_sha256 = bootstrap.post.artifact.raw_sha256
        precursor_receipt = (
            None
            if bootstrap.receipt is None
            else _plan_receipt_dict(bootstrap.receipt)
        )
        precursor_input_state = bootstrap.input_state.value
        precursor_input_start = bootstrap.input_start_clock_s
        precursor_input_receipt = bootstrap.input_receipt_clock_s
    bridge_post_sha256 = (
        None if result.post is None else result.post.artifact.raw_sha256
    )
    bridge_commit_sha256 = (
        None if result.commit is None else result.commit.artifact.raw_sha256
    )
    if (
        reservation.evidence.precursor_mode != precursor.mode
        or reservation.evidence.precursor_commit_sha256
        != precursor_commit_sha256
    ):
        raise RuntimeError("R2.3 ordered receipt does not bind its reservation")
    actual_physical_primitives = (
        (1 if precursor.mode == "compass_click" else 0)
        + (1 if result.input_attempted else 0)
    )
    if actual_physical_primitives > 2:
        raise RuntimeError("R2.3 campaign exceeded its physical primitive budget")
    if result.input_attempted and (
        result.input_start_clock_s is None
        or reservation_completed_clock_s > result.input_start_clock_s
    ):
        raise RuntimeError("R2.3 reservation did not precede Right input")
    return {
        "schema_version": 1,
        "campaign_id": CAMERA_BRIDGE_AUTHORIZATION_CAMPAIGN_ID,
        "reservation_id": reservation.sentinel_sha256,
        "reservation_completed_clock_s": reservation_completed_clock_s,
        "maximum_physical_primitives": 2,
        "actual_physical_primitives": actual_physical_primitives,
        "allowed_order": [
            {
                "ordinal": 0,
                "stage": "north_precursor",
                "kind": "compass_click",
                "logical_client_point": list(REVIEWED_COMPASS_POINT),
                "zero_click_requires_exact_frozen_north_pixels": True,
            },
            {
                "ordinal": 1,
                "stage": "bridge",
                "kind": "key_hold",
                "key": "right",
                "hold_seconds": CAMERA_BRIDGE_CAPTURE_HOLD_SECONDS,
            },
        ],
        "stages": [
            {
                "ordinal": 0,
                "stage": "north_precursor",
                "mode": precursor.mode,
                "commit_sha256": precursor_commit_sha256,
                "post_sha256": precursor_post_sha256,
                "input_state": precursor_input_state,
                "receipt": precursor_receipt,
                "start_clock_s": precursor_input_start,
                "receipt_clock_s": precursor_input_receipt,
            },
            {
                "ordinal": 1,
                "stage": "bridge",
                "mode": "fixed_right_hold",
                "commit_sha256": bridge_commit_sha256,
                "post_sha256": bridge_post_sha256,
                "input_state": result.input_state.value,
                "receipt": (
                    None
                    if result.receipt is None
                    else _plan_receipt_dict(result.receipt)
                ),
                "start_clock_s": result.input_start_clock_s,
                "receipt_clock_s": result.input_receipt_clock_s,
            },
        ],
    }


def _write_consumed_precursor_failure_report(
    *,
    result: CameraNorthBootstrapResult,
    reservation: CameraBridgeAuthorizationReservation,
    reservation_completed_clock_s: float,
    report_path: Path,
    expected_head: str,
    command_argv: tuple[str, ...],
    selected_hwnd: int,
    selected_process_id: int,
    selected_thread_id: int,
    selected_class_name: str,
    selected_title_sha256: str,
    detail: str,
) -> str:
    """Publish truthful consumed/no-Right evidence without a completion seal."""

    precursor_commit_sha256 = (
        None if result.commit is None else result.commit.artifact.raw_sha256
    )
    precursor_post_sha256 = (
        None if result.post is None else result.post.artifact.raw_sha256
    )
    ordered_receipt = {
        "schema_version": 1,
        "campaign_id": CAMERA_BRIDGE_AUTHORIZATION_CAMPAIGN_ID,
        "reservation_id": reservation.sentinel_sha256,
        "reservation_completed_clock_s": reservation_completed_clock_s,
        "maximum_physical_primitives": 2,
        "actual_physical_primitives": 1 if result.input_attempted else 0,
        "campaign_completed": False,
        "stages": [
            {
                "ordinal": 0,
                "stage": "north_precursor",
                "mode": "compass_click",
                "commit_sha256": precursor_commit_sha256,
                "post_sha256": precursor_post_sha256,
                "input_state": result.input_state.value,
                "receipt": (
                    None
                    if result.receipt is None
                    else _plan_receipt_dict(result.receipt)
                ),
                "start_clock_s": result.input_start_clock_s,
                "receipt_clock_s": result.input_receipt_clock_s,
            },
            {
                "ordinal": 1,
                "stage": "bridge",
                "mode": "fixed_right_hold",
                "status": "forbidden_after_precursor_failure",
                "receipt": None,
            },
        ],
    }
    evidence = {
        "command": _BRIDGE_CAPTURE_COMMAND,
        "development_only": True,
        "campaign_authorization": reservation.as_dict(),
        "campaign_precursor": {
            "mode": "compass_click",
            "campaign_reservation_id": reservation.sentinel_sha256,
            "reservation_completed_clock_s": reservation_completed_clock_s,
            "bootstrap": _bootstrap_result_dict(
                result,
                tracked_worktree_clean=True,
            ),
            "embedded_same_process_and_input_lease": True,
            "external_north_report_accepted": False,
            "window_binding": {
                "class_name": selected_class_name,
                "hwnd": selected_hwnd,
                "process_id": selected_process_id,
                "thread_id": selected_thread_id,
                "title_sha256": selected_title_sha256,
            },
        },
        "ordered_campaign_receipt": ordered_receipt,
        "terminal_reason": "campaign_precursor_failed",
        "detail": detail,
        "right_input_attempted": False,
        "right_input_forbidden": True,
        "completion_seal_eligible": False,
        "production_remains_sole_scene_authority": True,
        "registration_can_validate_scene": False,
        "registration_can_expose_resources": False,
        "tracked_worktree_clean": True,
    }
    provenance = CameraReportProvenance(
        git_head_sha=expected_head,
        detector_id=_EXPECTED_DETECTOR_ID,
        detector_version=_EXPECTED_DETECTOR_VERSION,
        profile_id=_EXPECTED_PROFILE_ID,
        plan_id=CAMERA_BRIDGE_CAPTURE_ID,
        plan_version=CAMERA_BRIDGE_CAPTURE_VERSION,
        command_argv=command_argv,
        tracked_worktree_clean=True,
    )
    return write_camera_validation_report(
        report_path,
        evidence,
        provenance,
    ).sha256


def _bridge_completion_evidence(
    capture_evidence: dict[str, object],
    *,
    capture_report_sha256: str,
    authorization_reservation: CameraBridgeAuthorizationReservation,
) -> CameraBridgeCompletionEvidence:
    """Bind every completed stage used by offline ActionTransition ingestion."""

    ordered_receipt = _json_object(
        capture_evidence.get("ordered_campaign_receipt"),
        "ordered R2.3 campaign receipt",
    )
    frames = _json_object(capture_evidence.get("frames"), "bridge frames")
    closure = _json_object(
        capture_evidence.get("post_transition_closure"),
        "bridge post-transition closure",
    )
    pointer_mapping = _json_object(
        capture_evidence.get("pointer_mapping"),
        "bridge pointer mapping",
    )
    commit_sha256 = closure.get("commit_sha256")
    post_sha256 = closure.get("post_sha256")
    if (
        closure.get("completed") is not True
        or not isinstance(commit_sha256, str)
        or not isinstance(post_sha256, str)
    ):
        raise RuntimeError(
            "bridge completion seal requires a complete exact commit/post closure"
        )
    stage_chain = {
        "arm_age": capture_evidence.get("arm_age"),
        "frames": frames,
        "guards": capture_evidence.get("guards"),
        "input": capture_evidence.get("input"),
        "preflight": capture_evidence.get("preflight"),
    }
    registrations = {
        "campaign_precursor": capture_evidence.get("campaign_precursor"),
        "precursor_to_commit_registration": capture_evidence.get(
            "precursor_to_commit_registration"
        ),
        "planner_source_registration": capture_evidence.get(
            "planner_source_registration"
        ),
        "post_transition_registration": capture_evidence.get(
            "post_transition_registration"
        ),
    }
    return CameraBridgeCompletionEvidence(
        authorization_sentinel_sha256=(
            authorization_reservation.sentinel_sha256
        ),
        capture_report_sha256=capture_report_sha256,
        ordered_campaign_receipt_sha256=(
            canonical_camera_bridge_component_sha256(ordered_receipt)
        ),
        stage_chain_sha256=canonical_camera_bridge_component_sha256(stage_chain),
        commit_sha256=commit_sha256,
        post_sha256=post_sha256,
        pointer_mapping_sha256=canonical_camera_bridge_component_sha256(
            pointer_mapping
        ),
        registrations_sha256=canonical_camera_bridge_component_sha256(
            registrations
        ),
        closure_sha256=canonical_camera_bridge_component_sha256(closure),
    )


def _print_bridge_capture_summary(
    result: CameraBridgeCaptureResult,
    *,
    report_path: Path,
    report_sha256: str,
    git_head_sha: str,
) -> None:
    print(f"R2 bridge capture: {result.terminal_reason.value}")
    print(f"Input state: {result.input_state.value}")
    print(f"Protocol completed: {result.protocol_completed}")
    print(f"Git HEAD: {git_head_sha}")
    print(f"Report: {report_path}")
    print(f"Report SHA-256: {report_sha256}")


def _run_live_bridge_capture(
    *,
    analysis_evidence: _BridgeAnalysisEvidence,
    output_root: Path,
    report_path: Path,
    digest_path: Path,
    case_prefix: str,
    expected_head: str,
    command_argv: tuple[str, ...],
    publication_state: _ReportPublicationState,
) -> int:
    """Run the integrated R2.3 precursor and bridge under one input lease."""

    backend: WindowsCaptureBackend | None = None
    source: CaptureSource | None = None
    control: WindowsCameraControl | None = None
    result: CameraBridgeCaptureResult | None = None
    adapter_identity: str | None = None
    selected_hwnd: int | None = None
    selected_process_id: int | None = None
    selected_thread_id: int | None = None
    selected_class_name: str | None = None
    selected_title: str | None = None
    selected_title_sha256: str | None = None
    registration_engine: RobustRegistrationEngine | None = None
    campaign_precursor: _BridgeCampaignPrecursor | None = None
    planner_source_registration: RobustWorldRegistration | None = None
    precursor_to_commit_registration: RobustWorldRegistration | None = None
    post_transition_closure: CameraBridgePostTransitionClosure | None = None
    post_transition_production: CameraEvaluation | None = None
    post_transition_registration: RobustWorldRegistration | None = None
    pointer_evidence: _BridgePointerEvidence | None = None
    authorization_reservation: CameraBridgeAuthorizationReservation | None = None
    reservation_completed_clock_s: float | None = None
    north_result: CameraNorthBootstrapResult | None = None
    handled_error: Exception | None = None
    unhandled_error: BaseException | None = None
    cleanup_errors: list[str] = []
    try:
        head_inside, clean_inside = _git_state()
        if head_inside != expected_head or not clean_inside:
            raise RuntimeError(
                "Git HEAD/worktree changed before the leased R2 boundary"
            )
        _reserve_case_namespace(
            output_root,
            case_prefix,
            report_path,
            digest_path,
            git_head_sha=expected_head,
        )
        backend = WindowsCaptureBackend(title_substring=DEFAULT_TITLE_SUBSTRING)
        source = CaptureSource(backend, max_consecutive_failures=1)
        source.open()
        source.capture()
        selected = backend.selected_window
        if selected is None:
            raise RuntimeError("capture succeeded without a selected RuneLite window")
        selected_hwnd = selected.hwnd
        selected_title_sha256 = hashlib.sha256(
            selected.title.encode("utf-8")
        ).hexdigest()
        selected_class_name = selected.class_name
        selected_title = selected.title
        recorder = _PrivateArtifactRecorder(
            output_root,
            case_prefix=case_prefix,
            git_head_sha=expected_head,
            plan_id=CAMERA_BRIDGE_CAPTURE_ID,
            plan_version=CAMERA_BRIDGE_CAPTURE_VERSION,
        )
        control = WindowsCameraControl(
            selected.hwnd,
            expected_class_name=selected.class_name,
            expected_title=selected.title,
        )
        control_identity = control.target_identity
        selected_process_id = control_identity.process_id
        selected_thread_id = control_identity.thread_id
        if (
            control_identity.class_name != selected.class_name
            or hashlib.sha256(control_identity.title.encode("utf-8")).hexdigest()
            != selected_title_sha256
        ):
            raise RuntimeError(
                "selected RuneLite window does not bind the camera-control identity"
            )
        adapter_identity = f"{type(control).__module__}.{type(control).__qualname__}"
        if adapter_identity != _EXPECTED_WINDOWS_CAMERA_ADAPTER:
            raise RuntimeError("R2 camera adapter identity mismatch")
        pointer_api = RealWindowsCameraApi()
        pointer_api.declare_dpi_awareness()
        registration_engine = RobustRegistrationEngine()
        def require_campaign_window_identity() -> None:
            current = control.target_identity
            if (
                current.process_id != control_identity.process_id
                or current.thread_id != control_identity.thread_id
                or current.class_name != control_identity.class_name
                or hashlib.sha256(current.title.encode("utf-8")).hexdigest()
                != selected_title_sha256
            ):
                raise RuntimeError(
                    "RuneLite process/thread/window identity changed inside R2.3"
                )

        def authorization_evidence(
            mode: str,
            precursor_commit_sha256: str,
        ) -> CameraBridgeAuthorizationEvidence:
            return CameraBridgeAuthorizationEvidence(
                r1_report_sha256=analysis_evidence.r1_report_sha256,
                r2_report_sha256=analysis_evidence.report_sha256,
                precursor_mode=mode,
                precursor_commit_sha256=precursor_commit_sha256,
                target_hwnd=selected.hwnd,
                target_process_id=control_identity.process_id,
                target_thread_id=control_identity.thread_id,
                target_class_name=selected.class_name,
                target_title_sha256=selected_title_sha256,
            )

        def reserve_campaign(
            mode: str,
            precursor_commit_sha256: str,
        ) -> None:
            nonlocal authorization_reservation, reservation_completed_clock_s
            if authorization_reservation is not None:
                raise RuntimeError("R2.3 campaign reservation was requested twice")
            authorization_reservation = reserve_camera_bridge_authorization(
                _REPO_ROOT,
                git_head_sha=expected_head,
                source_gate_enabled=_BRIDGE_LIVE_INPUT_ENABLED,
                evidence=authorization_evidence(mode, precursor_commit_sha256),
            )
            reservation_completed_clock_s = time.monotonic()

        # The only zero-click route is exact byte identity with the frozen,
        # receipt-proven north frame plus identity registration. Ordinary
        # accepted registration may relate a different yaw/pitch/zoom pose and
        # therefore cannot omit the compass primitive.
        precursor_frame, precursor_evidence = _capture_campaign_precursor_frame(
            source,
            recorder,
        )
        _require_fail_closed_campaign_frame(
            precursor_evidence,
            context="fresh R2.3 precursor",
        )
        direct_registration = registration_engine.analyze(
            analysis_evidence.source_frame,
            precursor_frame,
        )
        direct_north_qualification: CameraBridgeExactNorthQualification | None
        try:
            _require_bridge_starting_registration(
                direct_registration,
                expected_source_sha256=analysis_evidence.source_sha256,
                expected_target_sha256=precursor_evidence.artifact.raw_sha256,
                context="frozen planner source to zero-click precursor",
            )
            direct_north_qualification = qualify_exact_frozen_north_registration(
                direct_registration
            )
        except RuntimeError:
            direct_registration_accepted = False
            direct_north_qualification = None
        except ValueError:
            direct_registration_accepted = False
            direct_north_qualification = None
        else:
            direct_registration_accepted = True

        if direct_registration_accepted:
            if direct_north_qualification is None:  # pragma: no cover - defensive
                raise RuntimeError("zero-click route lost exact north qualification")
            planner_source_registration = direct_registration
            campaign_precursor = _BridgeCampaignPrecursor(
                mode="zero_click",
                frame=precursor_frame,
                frame_evidence=precursor_evidence,
                registration=direct_registration,
                north_qualification=direct_north_qualification,
                bootstrap=None,
                window_hwnd=selected.hwnd,
                window_process_id=control_identity.process_id,
                window_thread_id=control_identity.thread_id,
                window_class_name=selected.class_name,
                window_title_sha256=selected_title_sha256,
            )
        else:
            def require_safe_compass_seam(
                initial: CameraServoFrameEvidence,
                arm: CameraServoFrameEvidence,
                commit: CameraServoFrameEvidence,
            ) -> None:
                current_head, current_clean = _git_state()
                if current_head != expected_head or not current_clean:
                    raise RuntimeError(
                        "Git HEAD/worktree changed before the compass seam"
                    )
                require_campaign_window_identity()
                for stage, evidence in (
                    ("north-initial", initial),
                    ("north-arm", arm),
                    ("north-commit", commit),
                ):
                    _require_fail_closed_campaign_frame(evidence, context=stage)

            def reserve_before_compass(
                initial: CameraServoFrameEvidence,
                arm: CameraServoFrameEvidence,
                commit: CameraServoFrameEvidence,
            ) -> None:
                require_safe_compass_seam(initial, arm, commit)
                reserve_campaign("compass_click", commit.artifact.raw_sha256)

            north_result = run_camera_north_bootstrap(
                source,
                control,
                sleeper=time.sleep,
                settle_s=_NORTH_BOOTSTRAP_SETTLE_SECONDS,
                recorder=recorder,
                pre_input_guard=require_safe_compass_seam,
                final_input_guard=reserve_before_compass,
            )
            _require_north_bootstrap_result_identities(north_result)
            if (
                north_result.terminal_reason
                is not CameraNorthBootstrapTerminalReason.BOOTSTRAP_EXECUTED
                or north_result.input_state
                is not CameraNorthBootstrapInputState.COMPLETE
                or north_result.post is None
                or north_result.commit is None
                or authorization_reservation is None
                or reservation_completed_clock_s is None
            ):
                raise RuntimeError(
                    "R2.3 north precursor did not complete; Right is forbidden"
                )
            if (
                north_result.input_start_clock_s is None
                or reservation_completed_clock_s > north_result.input_start_clock_s
            ):
                raise RuntimeError(
                    "R2.3 reservation did not precede the compass input seam"
                )
            _require_fail_closed_campaign_frame(
                north_result.post,
                context="R2.3 compass post",
            )
            precursor_frame = _bridge_evidence_frame(
                output_root,
                north_result.post,
            )
            planner_source_registration = registration_engine.analyze(
                analysis_evidence.source_frame,
                precursor_frame,
            )
            _require_bridge_starting_registration(
                planner_source_registration,
                expected_source_sha256=analysis_evidence.source_sha256,
                expected_target_sha256=north_result.post.artifact.raw_sha256,
                context="frozen planner source to compass-post precursor",
            )
            campaign_precursor = _BridgeCampaignPrecursor(
                mode="compass_click",
                frame=precursor_frame,
                frame_evidence=north_result.post,
                registration=planner_source_registration,
                north_qualification=None,
                bootstrap=north_result,
                window_hwnd=selected.hwnd,
                window_process_id=control_identity.process_id,
                window_thread_id=control_identity.thread_id,
                window_class_name=selected.class_name,
                window_title_sha256=selected_title_sha256,
            )

        if campaign_precursor is None or planner_source_registration is None:
            raise RuntimeError("R2.3 campaign produced no authenticated precursor")
        prearm_head, prearm_clean = _git_state()
        precursor_age = time.monotonic() - campaign_precursor.frame.captured_monotonic_s
        if prearm_head != expected_head or not prearm_clean:
            raise RuntimeError(
                "Git HEAD/worktree changed during the read-only R2.3 precursor"
            )
        if (
            not math.isfinite(precursor_age)
            or precursor_age < 0.0
            or precursor_age >= _BRIDGE_NORTH_MAXIMUM_AGE_SECONDS
        ):
            raise RuntimeError("R2.3 campaign precursor expired before Right")

        def require_same_clean_head_before_input(
            decision: CameraServoFrameEvidence,
            arm: CameraServoFrameEvidence,
            commit: CameraServoFrameEvidence,
        ) -> None:
            nonlocal precursor_to_commit_registration, pointer_evidence
            current_head, current_clean = _git_state()
            if current_head != expected_head or not current_clean:
                raise RuntimeError(
                    "Git HEAD/worktree changed before the physical input seam"
                )
            for stage, evidence in (
                ("decision", decision),
                ("arm", arm),
                ("commit", commit),
            ):
                if evidence.production is None:
                    raise RuntimeError(
                        f"{stage} production evidence is missing at the input seam"
                    )
                _require_north_bootstrap_production_identity(
                    stage,
                    evidence.production,
                )
            require_campaign_window_identity()
            current_north_age = (
                time.monotonic() - campaign_precursor.frame.captured_monotonic_s
            )
            if (
                not math.isfinite(current_north_age)
                or current_north_age < 0.0
                or current_north_age >= _BRIDGE_NORTH_MAXIMUM_AGE_SECONDS
            ):
                raise RuntimeError(
                    "R2.3 precursor expired at the Right physical input seam"
                )
            fresh_north_sha256 = hashlib.sha256(
                campaign_precursor.frame.payload
            ).hexdigest()
            if planner_source_registration is None:  # pragma: no cover - defensive
                raise RuntimeError("R2 planner-source registration was not precomputed")
            _require_bridge_starting_registration(
                planner_source_registration,
                expected_source_sha256=analysis_evidence.source_sha256,
                expected_target_sha256=fresh_north_sha256,
                context="reviewed planner source to R2.3 campaign precursor",
            )
            commit_frame = _bridge_evidence_frame(output_root, commit)
            if (
                precursor_to_commit_registration is None
                or precursor_to_commit_registration.target.payload_sha256
                != commit.artifact.raw_sha256
            ):
                candidate = registration_engine.analyze(
                    campaign_precursor.frame,
                    commit_frame,
                )
                _require_bridge_starting_registration(
                    candidate,
                    expected_source_sha256=fresh_north_sha256,
                    expected_target_sha256=commit.artifact.raw_sha256,
                    context="R2.3 campaign precursor to live Right commit",
                )
                precursor_to_commit_registration = candidate
            else:
                _require_bridge_starting_registration(
                    precursor_to_commit_registration,
                    expected_source_sha256=fresh_north_sha256,
                    expected_target_sha256=commit.artifact.raw_sha256,
                    context="R2.3 campaign precursor to live Right commit",
                )
            pointer_evidence = _require_bridge_pointer_ownership(
                pointer_api,
                hwnd=selected.hwnd,
            )

        def authenticate_or_reserve_campaign_before_right(
            decision: CameraServoFrameEvidence,
            arm: CameraServoFrameEvidence,
            commit: CameraServoFrameEvidence,
        ) -> None:
            """Burn zero-click campaigns or reauthenticate compass campaigns."""

            nonlocal authorization_reservation, reservation_completed_clock_s
            require_same_clean_head_before_input(decision, arm, commit)
            precursor_commit_sha256 = (
                campaign_precursor.frame_evidence.artifact.raw_sha256
                if campaign_precursor.bootstrap is None
                else (
                    campaign_precursor.bootstrap.commit.artifact.raw_sha256
                    if campaign_precursor.bootstrap.commit is not None
                    else ""
                )
            )
            if not precursor_commit_sha256:
                raise RuntimeError("compass campaign lost its exact commit hash")
            expected_authorization = authorization_evidence(
                campaign_precursor.mode,
                precursor_commit_sha256,
            )
            if authorization_reservation is None:
                if campaign_precursor.mode != "zero_click":
                    raise RuntimeError(
                        "compass campaign reached Right without its reservation"
                    )
                reserve_campaign("zero_click", precursor_commit_sha256)
            else:
                authenticated = authenticate_camera_bridge_authorization(
                    _REPO_ROOT,
                    git_head_sha=expected_head,
                    expected_sentinel_sha256=(
                        authorization_reservation.sentinel_sha256
                    ),
                    evidence=expected_authorization,
                )
                if authenticated.sentinel_sha256 != authorization_reservation.sentinel_sha256:
                    raise RuntimeError("R2.3 campaign reservation identity changed")

        result = run_fixed_camera_bridge_capture(
            source,
            control,
            sleeper=time.sleep,
            recorder=recorder,
            pre_input_guard=require_same_clean_head_before_input,
            final_input_guard=authenticate_or_reserve_campaign_before_right,
        )
        if result.input_attempted and (
            authorization_reservation is None
            or reservation_completed_clock_s is None
            or result.input_start_clock_s is None
            or reservation_completed_clock_s > result.input_start_clock_s
        ):
            raise RuntimeError(
                "R2.3 reservation did not precede the first possible Right input"
            )
    except (
        CaptureError,
        FileExistsError,
        OSError,
        RuntimeError,
        ValueError,
        WindowsCameraError,
        subprocess.CalledProcessError,
    ) as exc:
        handled_error = exc
    except BaseException as exc:
        unhandled_error = exc
    finally:
        if control is not None:
            try:
                control.release_all_held_keys()
            except (OSError, RuntimeError, WindowsCameraError) as exc:
                cleanup_errors.append(f"camera input cleanup failed: {exc}")
        if source is not None:
            try:
                source.close()
            except (CaptureError, OSError, RuntimeError) as exc:
                cleanup_errors.append(f"capture cleanup failed: {exc}")

    if handled_error is not None:
        if (
            authorization_reservation is not None
            and reservation_completed_clock_s is not None
            and north_result is not None
            and selected_hwnd is not None
            and selected_process_id is not None
            and selected_thread_id is not None
            and selected_class_name is not None
            and selected_title_sha256 is not None
        ):
            try:
                failure_head, failure_clean = _git_state()
                if failure_head != expected_head or not failure_clean:
                    raise RuntimeError(
                        "cannot publish consumed failure evidence from a changed head"
                    )
                failure_sha256 = _write_consumed_precursor_failure_report(
                    result=north_result,
                    reservation=authorization_reservation,
                    reservation_completed_clock_s=reservation_completed_clock_s,
                    report_path=report_path,
                    expected_head=expected_head,
                    command_argv=command_argv,
                    selected_hwnd=selected_hwnd,
                    selected_process_id=selected_process_id,
                    selected_thread_id=selected_thread_id,
                    selected_class_name=selected_class_name,
                    selected_title_sha256=selected_title_sha256,
                    detail=str(handled_error),
                )
                publication_state.published_by_this_invocation = True
                print(
                    "Consumed R2.3 precursor-failure report SHA-256: "
                    f"{failure_sha256}",
                    file=sys.stderr,
                )
            except (OSError, RuntimeError, TypeError, ValueError) as report_error:
                print(
                    "Could not publish consumed R2.3 precursor-failure evidence: "
                    f"{report_error}",
                    file=sys.stderr,
                )
        print(f"R2 bridge capture failed: {handled_error}", file=sys.stderr)
        if cleanup_errors:
            print("; ".join(cleanup_errors), file=sys.stderr)
        return 2
    if unhandled_error is not None:
        if cleanup_errors:
            print("; ".join(cleanup_errors), file=sys.stderr)
        raise unhandled_error
    if cleanup_errors:
        print("; ".join(cleanup_errors), file=sys.stderr)
        return 2

    if result is not None and registration_engine is not None:
        try:
            (
                result,
                post_transition_closure,
                post_transition_registration,
                post_transition_production,
            ) = _evaluate_bridge_post_transition(
                result,
                output_root=output_root,
                registration_engine=registration_engine,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            handled_error = exc
    if handled_error is not None:
        print(f"R2 bridge capture failed: {handled_error}", file=sys.stderr)
        return 2
    if (
        result is None
        or adapter_identity is None
        or selected_class_name is None
        or selected_title is None
        or post_transition_closure is None
    ):  # pragma: no cover - defensive composition guard
        print("R2 bridge capture produced no result.", file=sys.stderr)
        return 2

    try:
        head_after, clean_after = _git_state()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Cannot re-establish Git provenance: {exc}", file=sys.stderr)
        return 2
    if head_after != expected_head or not clean_after:
        print(
            "Git HEAD/worktree changed during R2 capture; refusing report.",
            file=sys.stderr,
        )
        return 2
    try:
        try:
            _require_bridge_capture_result_identities(
                result,
                output_root=output_root,
                sealed_post_production=post_transition_production,
                post_production_already_bound=(
                    isinstance(
                        getattr(result, "post", None),
                        CameraServoFrameEvidence,
                    )
                    and result.terminal_reason
                    is not CameraBridgeCaptureTerminalReason.POST_CAPTURE_PENDING_CLOSURE
                ),
            )
        except (OSError, RuntimeError, TypeError, ValueError) as identity_error:
            if not result.input_attempted:
                raise
            identity_exception = _bridge_closure_exception(identity_error)
            if (
                post_transition_closure.status
                is CameraBridgePostTransitionStatus.ARTIFACT_ERROR
            ):
                post_transition_closure = _new_bridge_post_transition_closure(
                    CameraBridgePostTransitionStatus.ARTIFACT_ERROR,
                    (
                        f"{post_transition_closure.detail} Final artifact seal "
                        "revalidation also failed; the exact receipt is retained."
                    ),
                    commit_sha256=post_transition_closure.commit_sha256,
                    post_sha256=post_transition_closure.post_sha256,
                    action_bridge_receipt_proven=(
                        post_transition_closure.action_bridge_receipt_proven
                    ),
                    bridge_rejected=True,
                    artifact_exception=(
                        post_transition_closure.artifact_exception
                    ),
                    seal_exception=identity_exception,
                )
            else:
                post_transition_closure = _new_bridge_post_transition_closure(
                    CameraBridgePostTransitionStatus.SEAL_REVALIDATION_ERROR,
                    (
                        "Final exact-artifact/production seal revalidation failed; "
                        "the physical receipt is retained and the bridge is rejected."
                    ),
                    commit_sha256=post_transition_closure.commit_sha256,
                    post_sha256=post_transition_closure.post_sha256,
                    action_bridge_receipt_proven=(
                        post_transition_closure.action_bridge_receipt_proven
                    ),
                    registration_attempted=(
                        post_transition_closure.registration_attempted
                    ),
                    registration_accepted=(
                        post_transition_closure.registration_accepted
                    ),
                    registration_bridge_observed=(
                        post_transition_closure.registration_bridge_observed
                    ),
                    production_re_evaluated=(
                        post_transition_closure.production_re_evaluated
                    ),
                    production_matches_capture=False,
                    production_supported_endpoint=False,
                    bridge_rejected=True,
                    registration_exception=(
                        post_transition_closure.registration_exception
                    ),
                    seal_exception=identity_exception,
                )
        if result.input_attempted and (
            authorization_reservation is None
            or reservation_completed_clock_s is None
            or campaign_precursor is None
            or planner_source_registration is None
            or precursor_to_commit_registration is None
            or pointer_evidence is None
        ):
            raise RuntimeError(
                "attempted bridge input lacks full-campaign precondition evidence"
            )
        if campaign_precursor is None:
            raise RuntimeError("R2.3 report lacks its embedded campaign precursor")
        provenance = CameraReportProvenance(
            git_head_sha=expected_head,
            detector_id=_EXPECTED_DETECTOR_ID,
            detector_version=_EXPECTED_DETECTOR_VERSION,
            profile_id=_EXPECTED_PROFILE_ID,
            plan_id=CAMERA_BRIDGE_CAPTURE_ID,
            plan_version=CAMERA_BRIDGE_CAPTURE_VERSION,
            command_argv=command_argv,
            tracked_worktree_clean=True,
        )
        capture_evidence = _bridge_capture_evidence(
            result,
            analysis_evidence=analysis_evidence,
            authorization_reservation=authorization_reservation,
            adapter_identity=adapter_identity,
            campaign_precursor=campaign_precursor,
            precursor_to_commit_registration=precursor_to_commit_registration,
            reservation_completed_clock_s=reservation_completed_clock_s,
            planner_source_registration=planner_source_registration,
            post_transition_closure=post_transition_closure,
            post_transition_production=post_transition_production,
            post_transition_registration=post_transition_registration,
            pointer_evidence=pointer_evidence,
            selected_class_name=selected_class_name,
            selected_title=selected_title,
        )
        written = write_camera_validation_report(
            report_path,
            capture_evidence,
            provenance,
        )
        publication_state.published_by_this_invocation = True
        head_published, clean_published = _git_state()
        if head_published != expected_head or not clean_published:
            retraction_errors = _retract_report_targets_after_lease_failure(
                report_path,
                digest_path,
            )
            publication_state.published_by_this_invocation = False
            detail = (
                "Git HEAD/worktree changed during R2 report publication; "
                "canonical evidence was retracted."
            )
            if retraction_errors:
                detail += " " + "; ".join(retraction_errors)
            raise RuntimeError(detail)
        if post_transition_closure.completed:
            if authorization_reservation is None:
                raise RuntimeError(
                    "complete bridge transaction lacks campaign authorization"
                )
            publication_state.pending_bridge_completion = _PendingBridgeCompletion(
                git_head_sha=expected_head,
                reservation=authorization_reservation,
                evidence=_bridge_completion_evidence(
                    capture_evidence,
                    capture_report_sha256=written.sha256,
                    authorization_reservation=authorization_reservation,
                ),
            )
    except (FileExistsError, OSError, RuntimeError, TypeError, ValueError) as exc:
        if publication_state.published_by_this_invocation:
            retraction_errors = _retract_report_targets_after_lease_failure(
                report_path,
                digest_path,
            )
            publication_state.published_by_this_invocation = False
            if retraction_errors:
                exc.add_note("; ".join(retraction_errors))
        print(f"Cannot write R2 bridge report: {exc}", file=sys.stderr)
        return 2

    _print_bridge_capture_summary(
        result,
        report_path=written.report_path,
        report_sha256=written.sha256,
        git_head_sha=expected_head,
    )
    return (
        0
        if (
            post_transition_closure.completed
            or (
                not result.input_attempted
                and result.terminal_reason
                is CameraBridgeCaptureTerminalReason.PRODUCTION_PASS
            )
        )
        else 1
    )


def _main_bridge_capture(command_args: list[str]) -> int:
    """Keep the integrated R2.3 campaign inert pending lead review."""

    args = _build_bridge_capture_parser().parse_args(command_args[1:])
    try:
        expected_head = _validate_cli_text("--expected-head", args.expected_head)
        if re.fullmatch(r"[0-9a-f]{40}", expected_head) is None:
            raise ValueError("--expected-head must be a lowercase 40-character SHA")
        analysis_sha256 = _validate_cli_text(
            "--analysis-sha256",
            args.analysis_sha256,
        )
        case_prefix = _validate_case_prefix(args.case_prefix)
        command_argv = _exact_command_argv(command_args)
        _validate_command_argv(command_argv)
        _require_bridge_capture_runtime_identities()
        output_root = _resolve_private_output_root(args.output)
        report_path, digest_path = _report_paths(output_root, case_prefix)
        _preflight_case_namespace(
            output_root,
            case_prefix,
            report_path,
            digest_path,
        )
        head_before, clean_before = _git_state()
        if head_before != expected_head:
            raise RuntimeError(
                f"reviewed head {expected_head} does not match current {head_before}"
            )
    except (
        FileExistsError,
        OSError,
        RuntimeError,
        subprocess.CalledProcessError,
        ValueError,
    ) as exc:
        print(f"Cannot establish R2 bridge provenance: {exc}", file=sys.stderr)
        return 2
    if not clean_before:
        print(
            "Refusing R2 bridge input unless the worktree is exactly clean.",
            file=sys.stderr,
        )
        return 2
    if not _BRIDGE_LIVE_INPUT_ENABLED:
        print(
            "R2.3 full campaign remains input-disabled pending a future "
            "exact-head LEAD authorization; no report can grant input authority.",
            file=sys.stderr,
        )
        return 2
    try:
        if camera_bridge_authorization_consumed(_REPO_ROOT):
            raise RuntimeError(
                "the source-owned R2.3 full campaign is already consumed"
            )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Cannot establish R2.3 campaign state: {exc}", file=sys.stderr)
        return 2
    try:
        analysis_evidence = _load_bridge_analysis_evidence(
            args.analysis_report,
            expected_sha256=analysis_sha256,
            expected_head=expected_head,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Cannot authenticate reviewed R2 inputs: {exc}", file=sys.stderr)
        return 2

    lease = WindowsCameraInputLease()
    lease_entered = False
    publication_state = _ReportPublicationState()
    try:
        with lease:
            lease_entered = True
            outcome = _run_live_bridge_capture(
                analysis_evidence=analysis_evidence,
                output_root=output_root,
                report_path=report_path,
                digest_path=digest_path,
                case_prefix=case_prefix,
                expected_head=expected_head,
                command_argv=command_argv,
                publication_state=publication_state,
            )
    except CameraInputLeaseError as exc:
        retraction_errors: tuple[str, ...] = ()
        if (
            lease_entered
            and publication_state.published_by_this_invocation
        ):
            retraction_errors = _retract_report_targets_after_lease_failure(
                report_path,
                digest_path,
            )
        print(f"R2 bridge input lease unavailable: {exc}", file=sys.stderr)
        if retraction_errors:
            print("; ".join(retraction_errors), file=sys.stderr)
        return 2
    except BaseException as exc:
        if (
            lease_entered
            and publication_state.published_by_this_invocation
        ):
            for retraction_error in _retract_report_targets_after_lease_failure(
                report_path,
                digest_path,
            ):
                exc.add_note(retraction_error)
        raise
    pending = publication_state.pending_bridge_completion
    if pending is not None:
        try:
            postlease_head, postlease_clean = _git_state()
            if postlease_head != pending.git_head_sha or not postlease_clean:
                raise RuntimeError(
                    "Git HEAD/worktree changed after the input lease released"
                )
            completion = seal_camera_bridge_completion(
                _REPO_ROOT,
                git_head_sha=pending.git_head_sha,
                reservation=pending.reservation,
                evidence=pending.evidence,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            retraction_errors = _retract_report_targets_after_lease_failure(
                report_path,
                digest_path,
            )
            publication_state.published_by_this_invocation = False
            print(f"Cannot seal completed R2 bridge transaction: {exc}", file=sys.stderr)
            if retraction_errors:
                print("; ".join(retraction_errors), file=sys.stderr)
            return 2
        print(f"Completion seal SHA-256: {completion.seal_sha256}")
    return outcome


def _run_live_validation(
    args: argparse.Namespace,
    *,
    output_root: Path,
    report_path: Path,
    digest_path: Path,
    case_prefix: str,
    git_head_before: str,
    tracked_clean_before: bool,
    strategy_id: str,
    strategy_version: str,
    command_argv: tuple[str, ...],
    normalization_candidates: tuple[CameraPlan, ...],
    perturbation_plans: tuple[CameraPlan, ...],
    publication_state: _ReportPublicationState,
) -> int:
    """Run capture through report publication while the caller owns the lease."""

    backend: WindowsCaptureBackend | None = None
    source: CaptureSource | None = None
    control: WindowsCameraControl | None = None
    result: CameraSessionResult | None = None
    handled_error: Exception | None = None
    unhandled_error: BaseException | None = None
    cleanup_errors: list[str] = []
    try:
        # The first check happens before taking the machine-global lease for a
        # cheap deterministic refusal. While holding the lease, recheck every
        # legacy artifact location and durably reserve the complete prefix.
        # Reservations are permanent: even a failed attempt makes its prefix
        # single-use, so no retry can collide only after camera input begins.
        _reserve_case_namespace(
            output_root,
            case_prefix,
            report_path,
            digest_path,
            git_head_sha=git_head_before,
        )
        backend = WindowsCaptureBackend(title_substring=args.title)
        source = CaptureSource(backend, max_consecutive_failures=1)
        source.open()
        # The first capture resolves the exact window handle needed by the
        # control adapter. It is deliberately discarded: the first evidence
        # frame must be captured only after initial normalization completes.
        source.capture()
        selected = backend.selected_window
        if selected is None:
            raise RuntimeError("capture succeeded without a selected RuneLite window")
        recorder = _PrivateArtifactRecorder(
            output_root,
            case_prefix=case_prefix,
            git_head_sha=git_head_before,
            plan_id=strategy_id,
            plan_version=strategy_version,
        )
        control = WindowsCameraControl(
            selected.hwnd,
            expected_class_name=selected.class_name,
            expected_title=selected.title,
        )
        result = run_camera_validation_session(
            source,
            control,
            normalization_candidates=normalization_candidates,
            perturbation_plans=perturbation_plans,
            sleeper=time.sleep,
            settle_s=args.settle,
            confirmation_frames=args.confirmations,
            recorder=recorder,
        )
    except (
        CaptureError,
        CameraPlanError,
        FileExistsError,
        OSError,
        RuntimeError,
        ValueError,
        WindowsCameraError,
    ) as exc:
        handled_error = exc
    except BaseException as exc:
        # KeyboardInterrupt and unexpected programming errors still receive
        # lifecycle cleanup, then retain their normal propagation semantics.
        unhandled_error = exc
    finally:
        if control is not None:
            try:
                control.release_all_held_keys()
            except (OSError, RuntimeError, WindowsCameraError) as exc:
                cleanup_errors.append(f"camera input cleanup failed: {exc}")
        if source is not None:
            try:
                source.close()
            except (CaptureError, OSError, RuntimeError) as exc:
                cleanup_errors.append(f"capture cleanup failed: {exc}")

    if handled_error is not None:
        print(f"Camera validation failed: {handled_error}", file=sys.stderr)
        if cleanup_errors:
            print("; ".join(cleanup_errors), file=sys.stderr)
        return 2
    if unhandled_error is not None:
        if cleanup_errors:
            print("; ".join(cleanup_errors), file=sys.stderr)
        raise unhandled_error
    if cleanup_errors:
        print("; ".join(cleanup_errors), file=sys.stderr)
        return 2
    if result is None:  # pragma: no cover - defensive composition guard
        print("Camera validation produced no session result.", file=sys.stderr)
        return 2

    try:
        git_head_after, tracked_clean_after = _git_state()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Cannot re-establish Git provenance: {exc}", file=sys.stderr)
        return 2
    if git_head_after != git_head_before:
        print("Git HEAD changed during camera validation; refusing report.", file=sys.stderr)
        return 2

    final_evaluation = _last_production_evaluation(result)
    tracked_worktree_clean = tracked_clean_before and tracked_clean_after
    camera_evidence_eligible = result.passed and tracked_worktree_clean
    provenance = CameraReportProvenance(
        git_head_sha=git_head_before,
        detector_id=final_evaluation.detector_id,
        detector_version=final_evaluation.detector_version,
        profile_id=final_evaluation.profile_id,
        plan_id=strategy_id,
        plan_version=strategy_version,
        command_argv=command_argv,
        tracked_worktree_clean=tracked_worktree_clean,
    )
    try:
        written = write_camera_validation_report(
            report_path,
            _session_dict(
                result,
                args,
                strategy_id=strategy_id,
                strategy_version=strategy_version,
                tracked_worktree_clean=tracked_worktree_clean,
            ),
            provenance,
        )
        publication_state.published_by_this_invocation = True
    except (FileExistsError, OSError, TypeError, ValueError) as exc:
        print(f"Cannot write camera-validation report: {exc}", file=sys.stderr)
        return 2

    _print_summary(
        result,
        camera_evidence_eligible=camera_evidence_eligible,
        tracked_worktree_clean=tracked_worktree_clean,
        report_path=written.report_path,
        report_sha256=written.sha256,
        git_head_sha=git_head_before,
    )
    return 0 if camera_evidence_eligible else 1


def main(argv: list[str] | None = None) -> int:
    command_args = list(sys.argv[1:] if argv is None else argv)
    if command_args and command_args[0] == _BRIDGE_CAPTURE_COMMAND:
        return _main_bridge_capture(command_args)
    if command_args and command_args[0] == _FIXED_SYSTEM_ID_COMMAND:
        return _main_fixed_system_id(command_args)
    if command_args and command_args[0] == _NORTH_BOOTSTRAP_COMMAND:
        return _main_north_bootstrap(command_args)
    args = build_parser().parse_args(command_args)
    try:
        _validate_cli_text("--title", args.title)
        _validate_cli_text("--plan-id", args.plan_id)
        _validate_cli_text("--plan-version", args.plan_version)
        command_argv = _exact_command_argv(command_args)
        _validate_command_argv(command_argv)
        strategy_id, strategy_version = _strategy_identity(args)
        normalization_candidates = _build_normalization_candidates(args)
        perturbation_plans = _build_perturbation_plans(args)
    except (ValueError, CameraPlanError) as exc:
        print(f"Invalid camera plan: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        dry_run_payload: dict[str, Any] = {
            "normalization_strategy": {
                "id": strategy_id,
                "version": strategy_version,
                "selection_authority": (
                    "unchanged_production_camera_evaluation"
                ),
                "diagnostic_registration_used": False,
            },
            "normalization_candidates": [
                {
                    "index_1_based": index,
                    **_plan_dict(plan),
                }
                for index, plan in enumerate(
                    normalization_candidates,
                    start=1,
                )
            ],
            "perturbations": [_plan_dict(plan) for plan in perturbation_plans],
            "worst_case_bounds": _worst_case_bounds(
                normalization_candidates,
                perturbation_plans,
                confirmations=args.confirmations,
            ),
        }
        if len(normalization_candidates) == 1:
            # Compatibility alias for the original single-plan dry-run shape.
            dry_run_payload["normalization"] = _plan_dict(
                normalization_candidates[0]
            )
        print(
            json.dumps(
                dry_run_payload,
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    try:
        case_prefix = _validate_case_prefix(
            args.case_prefix if args.case_prefix is not None else _default_case_prefix()
        )
    except ValueError as exc:
        print(f"Invalid camera plan: {exc}", file=sys.stderr)
        return 2
    try:
        output_root = _resolve_private_output_root(args.output)
        report_path, digest_path = _report_paths(output_root, case_prefix)
        _preflight_case_namespace(
            output_root,
            case_prefix,
            report_path,
            digest_path,
        )
        git_head_before, tracked_clean_before = _git_state()
    except (FileExistsError, OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"Cannot establish Git provenance: {exc}", file=sys.stderr)
        return 2
    if not tracked_clean_before and not args.allow_dirty:
        print(
            "Refusing camera input with worktree changes; commit first or use "
            "--allow-dirty for non-release development evidence.",
            file=sys.stderr,
        )
        return 2

    lease = WindowsCameraInputLease()
    lease_entered = False
    publication_state = _ReportPublicationState()
    try:
        with lease:
            lease_entered = True
            return _run_live_validation(
                args,
                output_root=output_root,
                report_path=report_path,
                digest_path=digest_path,
                case_prefix=case_prefix,
                git_head_before=git_head_before,
                tracked_clean_before=tracked_clean_before,
                strategy_id=strategy_id,
                strategy_version=strategy_version,
                command_argv=command_argv,
                normalization_candidates=normalization_candidates,
                perturbation_plans=perturbation_plans,
                publication_state=publication_state,
            )
    except CameraInputLeaseError as exc:
        retraction_errors: tuple[str, ...] = ()
        if (
            lease_entered
            and lease.acquired
            and publication_state.published_by_this_invocation
        ):
            # A failed ReleaseMutex leaves ownership indeterminate. Canonical
            # evidence may have been published while the lease was still held;
            # retract it so no apparently eligible report survives that gate.
            retraction_errors = _retract_report_targets_after_lease_failure(
                report_path,
                digest_path,
            )
        print(f"Camera validation lease unavailable: {exc}", file=sys.stderr)
        if retraction_errors:
            print("; ".join(retraction_errors), file=sys.stderr)
        return 2
    except BaseException as exc:
        # Defensive fallback for an alternate/faulty context-manager boundary
        # that preserves a body exception despite failed lease release. The
        # real lease raises CameraInputLeaseError in this state, but canonical
        # evidence must never survive whenever ownership is still retained.
        if (
            lease_entered
            and lease.acquired
            and publication_state.published_by_this_invocation
        ):
            for retraction_error in _retract_report_targets_after_lease_failure(
                report_path,
                digest_path,
            ):
                exc.add_note(retraction_error)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
