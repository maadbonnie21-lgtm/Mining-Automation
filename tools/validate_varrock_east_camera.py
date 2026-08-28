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
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mining_automation.capture import CaptureError, CaptureSource, Frame  # noqa: E402
from mining_automation.capture.windows import (  # noqa: E402
    DEFAULT_TITLE_SUBSTRING,
    WindowsCaptureBackend,
)
from mining_automation.perception import write_resource_fixture_draft  # noqa: E402
from mining_automation.validation.camera_evaluation import CameraEvaluation  # noqa: E402
from mining_automation.validation.camera_input_lease import (  # noqa: E402
    CameraInputLeaseError,
    WindowsCameraInputLease,
)
from mining_automation.validation.camera_plan import (  # noqa: E402
    MAX_CAMERA_WHEEL_DETENTS,
    MAX_KEY_HOLD_SECONDS,
    REVIEWED_CAMERA_WHEEL_POINT,
    REVIEWED_COMPASS_POINT,
    CameraAction,
    CameraHoldKey,
    CameraInputReceipt,
    CameraKeyHold,
    CameraPause,
    CameraPlan,
    CameraPlanError,
    CameraPlanReceipt,
    CameraWheel,
    CompassClick,
    ResetZoomKey,
)
from mining_automation.validation.camera_report import (  # noqa: E402
    CameraReportProvenance,
    write_camera_validation_report,
)
from mining_automation.validation.camera_session import (  # noqa: E402
    CameraFrameArtifact,
    CameraFrameRecord,
    CameraNormalizationResult,
    CameraSessionResult,
    run_camera_validation_session,
)
from mining_automation.validation.windows_camera import (  # noqa: E402
    CAMERA_WHEEL_EVENT_INTERVAL_SECONDS,
    COMPASS_CLICK_DWELL_SECONDS,
    WindowsCameraControl,
    WindowsCameraError,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_COMPASS_POINT = REVIEWED_COMPASS_POINT
_CAMERA_WHEEL_POINT = REVIEWED_CAMERA_WHEEL_POINT


@dataclass(slots=True)
class _ReportPublicationState:
    published_by_this_invocation: bool = False


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
    parser.add_argument("--plan-version", default="0.2.0")
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
        or args.post_compass_settle != _DEFAULT_POST_COMPASS_SETTLE_S
        or args.reset_zoom
        or args.zoom_saturate_detents is not None
        or args.zoom_offset_detents != 0
    )
    if overrides:
        raise ValueError(
            f"--normalization-strategy {_PRODUCTION_GATED_STRATEGY_ID} uses a "
            "frozen candidate ladder and cannot be combined with single-plan "
            "pitch, yaw, settle, or zoom options"
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


def _git_state() -> tuple[str, bool]:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return head, not status.strip()


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
        ["git", "check-ignore", "--quiet", "--", str(root)],
        cwd=_REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if ignored.returncode != 0:
        raise ValueError("--output must be excluded by the repository ignore rules")
    return root


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
        return {"kind": "key_hold", "key": action.key.value, "duration_s": action.duration_s}
    if isinstance(action, CameraPause):
        return {"kind": "pause", "duration_s": action.duration_s}
    if isinstance(action, ResetZoomKey):
        return {"kind": "reset_zoom_key", "key": action.key, "dwell_s": action.dwell_s}
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
            "wheel_point": list(_CAMERA_WHEEL_POINT),
            "pointer_coordinate_space": "runelite_target_logical_client",
            "compass_click_dwell_s": COMPASS_CLICK_DWELL_SECONDS,
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
