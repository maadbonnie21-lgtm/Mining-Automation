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
import re
import subprocess
import sys
import time
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
from mining_automation.validation.camera_plan import (  # noqa: E402
    MAX_CAMERA_WHEEL_DETENTS,
    REVIEWED_CAMERA_WHEEL_POINT,
    REVIEWED_COMPASS_POINT,
    CameraAction,
    CameraHoldKey,
    CameraInputReceipt,
    CameraKeyHold,
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
    CameraSessionResult,
    run_camera_validation_session,
)
from mining_automation.validation.windows_camera import (  # noqa: E402
    WindowsCameraControl,
    WindowsCameraError,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_COMPASS_POINT = REVIEWED_COMPASS_POINT
_CAMERA_WHEEL_POINT = REVIEWED_CAMERA_WHEEL_POINT
_MINIMUM_SATURATION_DETENTS = 80
_DEFAULT_PITCH_HOLD_S = 3.0
_DEFAULT_PERTURB_HOLD_S = 0.75
_CASE_PREFIX_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")

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
    parser.add_argument("--case-prefix", help="unique artifact/report prefix")
    parser.add_argument(
        "--title",
        default=DEFAULT_TITLE_SUBSTRING,
        help=f"RuneLite title substring (default: {DEFAULT_TITLE_SUBSTRING!r})",
    )
    parser.add_argument(
        "--pitch-endpoint",
        choices=("up", "down"),
        required=True,
        help="deterministic pitch saturation endpoint",
    )
    zoom = parser.add_mutually_exclusive_group(required=True)
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
    parser.add_argument("--plan-version", default="0.1.0")
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


def _build_normalization_plan(args: argparse.Namespace) -> CameraPlan:
    pitch = CameraHoldKey(args.pitch_endpoint)
    actions: list[CameraAction] = [
        CompassClick(*_COMPASS_POINT),
        CameraKeyHold(pitch, _DEFAULT_PITCH_HOLD_S),
    ]
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


def _build_perturbation_plans(args: argparse.Namespace) -> tuple[CameraPlan, ...]:
    endpoint = CameraHoldKey(args.pitch_endpoint)
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


def _preflight_report_targets(report_path: Path, digest_path: Path) -> None:
    """Reject known report collisions before capture or global input."""

    if report_path.exists():
        raise FileExistsError(report_path)
    if digest_path.exists():
        raise FileExistsError(digest_path)


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


def _frame_record_dict(record: CameraFrameRecord) -> dict[str, Any]:
    return {
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


def _session_dict(
    result: CameraSessionResult,
    args: argparse.Namespace,
    *,
    tracked_worktree_clean: bool,
) -> dict[str, Any]:
    camera_evidence_eligible = result.passed and tracked_worktree_clean
    return {
        "camera_assumptions": {
            "compass_point": list(_COMPASS_POINT),
            "wheel_point": list(_CAMERA_WHEEL_POINT),
            "pitch_endpoint": args.pitch_endpoint,
            "pitch_hold_s": _DEFAULT_PITCH_HOLD_S,
            "zoom_mode": "reset_key" if args.reset_zoom else "wheel_endpoint",
            "zoom_saturate_detents": args.zoom_saturate_detents,
            "zoom_offset_detents": args.zoom_offset_detents,
            "diagnostics_can_override_production": False,
        },
        "normalization_plan": _plan_dict(result.normalization_plan),
        "initial_normalization_receipt": _plan_receipt_dict(
            result.initial_normalization_receipt
        ),
        "required_trials": result.required_trials,
        "required_confirmations": result.required_confirmations,
        "trials": [
            {
                "trial_index": trial.trial_index,
                "before": _frame_record_dict(trial.before),
                "perturbation_plan": _plan_dict(trial.perturbation_plan),
                "perturbation_receipt": _plan_receipt_dict(trial.perturbation_receipt),
                "perturbed": _frame_record_dict(trial.perturbed),
                "perturbation_fail_closed": trial.perturbation_fail_closed,
                "normalization_receipt": _plan_receipt_dict(
                    trial.normalization_receipt
                ),
                "confirmations": [
                    _frame_record_dict(confirmation)
                    for confirmation in trial.confirmations
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
    for trial in result.trials:
        confirmations = ", ".join(
            f"{item.evaluation.matched_landmark_count}/6"
            for item in trial.confirmations
        )
        print(
            f"  trial {trial.trial_index}: perturb fail-closed="
            f"{trial.perturbation_fail_closed}; confirmations={confirmations}; "
            f"pass={trial.passed}"
        )
    print(f"Report: {report_path}")
    print(f"Report SHA-256: {report_sha256}")


def main(argv: list[str] | None = None) -> int:
    command_args = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(command_args)
    try:
        _validate_cli_text("--title", args.title)
        _validate_cli_text("--plan-id", args.plan_id)
        _validate_cli_text("--plan-version", args.plan_version)
        command_argv = _exact_command_argv(command_args)
        _validate_command_argv(command_argv)
        normalization_plan = _build_normalization_plan(args)
        perturbation_plans = _build_perturbation_plans(args)
    except (ValueError, CameraPlanError) as exc:
        print(f"Invalid camera plan: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(
            json.dumps(
                {
                    "normalization": _plan_dict(normalization_plan),
                    "perturbations": [_plan_dict(plan) for plan in perturbation_plans],
                },
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
        _preflight_report_targets(report_path, digest_path)
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

    backend: WindowsCaptureBackend | None = None
    source: CaptureSource | None = None
    control: WindowsCameraControl | None = None
    result: CameraSessionResult | None = None
    handled_error: Exception | None = None
    unhandled_error: BaseException | None = None
    cleanup_errors: list[str] = []
    try:
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
            plan_id=args.plan_id,
            plan_version=args.plan_version,
        )
        control = WindowsCameraControl(selected.hwnd)
        result = run_camera_validation_session(
            source,
            control,
            normalization_plan=normalization_plan,
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

    final_evaluation = result.trials[-1].confirmations[-1].evaluation
    tracked_worktree_clean = tracked_clean_before and tracked_clean_after
    camera_evidence_eligible = result.passed and tracked_worktree_clean
    provenance = CameraReportProvenance(
        git_head_sha=git_head_before,
        detector_id=final_evaluation.detector_id,
        detector_version=final_evaluation.detector_version,
        profile_id=final_evaluation.profile_id,
        plan_id=args.plan_id,
        plan_version=args.plan_version,
        command_argv=command_argv,
        tracked_worktree_clean=tracked_worktree_clean,
    )
    try:
        written = write_camera_validation_report(
            report_path,
            _session_dict(
                result,
                args,
                tracked_worktree_clean=tracked_worktree_clean,
            ),
            provenance,
        )
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


if __name__ == "__main__":
    raise SystemExit(main())
