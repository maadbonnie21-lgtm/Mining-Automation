"""Authenticate and verify one saved Issue #31 R2 bridge capture offline.

This command has no camera or platform-input dependency.  It accepts only
frozen reports and saved pixels, rederives their perception evidence, and
writes a canonical no-authority report without overwriting existing evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, Final

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.perception.resource import ResourceVisualState
from mining_automation.validation.camera_arm_guard import (
    CameraArmGuardResult,
    evaluate_camera_arm_guard,
)
from mining_automation.validation.camera_bridge_authorization import (
    CAMERA_BRIDGE_AUTHORIZATION_CAMPAIGN_ID,
    CameraBridgeAuthorizationEvidence,
    CameraBridgeAuthorizationReservation,
    CameraBridgeCompletionEvidence,
    authenticate_camera_bridge_authorization,
    authenticate_camera_bridge_completion,
    canonical_camera_bridge_component_sha256,
    repository_worktree_git_dir,
)
from mining_automation.validation.camera_bridge_capture import (
    CAMERA_BRIDGE_CAPTURE_HOLD_SECONDS,
    CAMERA_BRIDGE_CAPTURE_ID,
    CAMERA_BRIDGE_CAPTURE_MAXIMUM_PHYSICAL_PRIMITIVES,
    CAMERA_BRIDGE_CAPTURE_SETTLE_SECONDS,
    CAMERA_BRIDGE_CAPTURE_VERSION,
    camera_bridge_capture_plan,
)
from mining_automation.validation.camera_bridge_north_state import (
    qualify_exact_frozen_north_registration,
)
from mining_automation.validation.camera_bridge_planner import (
    CAMERA_BRIDGE_PLANNER_ID,
    CAMERA_BRIDGE_PLANNER_VERSION,
    FROZEN_ENDPOINT_OBJECTIVE,
    FROZEN_ENDPOINT_OBJECTIVE_ID,
    FROZEN_ENDPOINT_SOURCE_SHA256,
)
from mining_automation.validation.camera_bridge_verifier import (
    CAMERA_BRIDGE_VERIFIER_ID,
    CAMERA_BRIDGE_VERIFIER_VERSION,
    AuthenticatedBridgeCapture,
    BridgePostVerification,
    verify_camera_bridge_post,
)
from mining_automation.validation.camera_evaluation import (
    CameraEvaluation,
    evaluate_varrock_east_camera,
)
from mining_automation.validation.camera_plan import (
    EXPECTED_CLIENT_HEIGHT,
    EXPECTED_CLIENT_WIDTH,
    REVIEWED_CAMERA_WHEEL_POINT,
    REVIEWED_COMPASS_POINT,
    CameraActionReceipt,
    CameraInputOperation,
    CameraInputReceipt,
    CameraPlanReceipt,
    CameraPreflightReceipt,
)
from mining_automation.validation.camera_report import (
    CameraReportProvenance,
    write_camera_validation_report,
)
from mining_automation.validation.camera_servo import (
    MAXIMUM_ARM_TO_INPUT_AGE_SECONDS,
)
from mining_automation.validation.client_readiness import (
    ClientInputReadiness,
    evaluate_client_input_readiness,
)
from mining_automation.validation.robust_registration import (
    RobustRegistrationEngine,
    RobustWorldRegistration,
)
from mining_automation.validation.robust_view_graph import (
    ROBUST_VIEW_GRAPH_ID,
    ROBUST_VIEW_GRAPH_VERSION,
    ViewNodeSpec,
    ViewRole,
)


def _load_sibling_r2_analysis() -> ModuleType:
    """Load the exact source-owned sibling without trusting ``sys.path``."""

    path = Path(__file__).resolve().with_name("analyze_issue31_bridge_r2.py")
    specification = importlib.util.spec_from_file_location(
        "_mining_automation_issue31_bridge_r2_analysis",
        path,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot load source-owned R2 analysis module: {path}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    try:
        specification.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(specification.name, None)
        raise
    return module


if TYPE_CHECKING:
    from tools import analyze_issue31_bridge_r2 as r2_analysis
else:
    r2_analysis = _load_sibling_r2_analysis()

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FULL_HEAD = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_EXPECTED_DETECTOR_ID: Final[str] = "profiled-resource:varrock-east-iron-v1"
_EXPECTED_DETECTOR_VERSION: Final[str] = "2.1.0"
_EXPECTED_PROFILE_ID: Final[str] = "varrock-east-iron-v1"
_R2_PLAN_ID: Final[str] = "issue31-read-only-bridge-analysis-r2"
_R2_PLAN_VERSION: Final[str] = "1.1.0"
_CAPTURE_COMMAND: Final[str] = "bridge-capture-r2"
_NORTH_COMMAND: Final[str] = "north-bootstrap-v2"
_NORTH_PLAN_ID: Final[str] = "issue31-v2-01-heading-north"
_NORTH_GUIDANCE_ID: Final[str] = "issue31-world-only-multi-axis-guidance"
_NORTH_GUIDANCE_VERSION: Final[str] = "2.0.0"
_EXPECTED_WINDOWS_CAMERA_ADAPTER: Final[str] = (
    "mining_automation.validation.windows_camera.WindowsCameraControl"
)
_DEFAULT_BRIDGE_OUTPUT: Final[Path] = Path("diagnostics/issue31-camera-bridge-r2")
# Frozen report-contract mirror of the live composition root's private limit.
# Keeping it local avoids importing the Windows/input-bearing CLI into this
# read-only verifier.
_BRIDGE_NORTH_MAXIMUM_AGE_SECONDS: Final[float] = 30.0
# Input-free report-contract mirror of the reviewed Windows compass dwell.
_COMPASS_CLICK_DWELL_SECONDS: Final[float] = 0.100
_STAGES: Final[tuple[str, ...]] = ("decision", "arm", "commit", "post")


@dataclass(frozen=True, slots=True)
class FrozenInputs:
    """Exact R1/R2 corpus after both reports authenticate."""

    corpus: r2_analysis.BridgeCorpus
    r1_report_sha256: str
    r2_report_sha256: str
    r1_report_path: Path
    r2_report_path: Path

    @property
    def base_specs(self) -> tuple[ViewNodeSpec, ...]:
        return (
            ViewNodeSpec(
                self.corpus.current.label,
                self.corpus.current.frame,
                ViewRole.SYSTEM_IDENTIFICATION,
            ),
            ViewNodeSpec(
                self.corpus.north.named_frame.label,
                self.corpus.north.named_frame.frame,
                ViewRole.OTHER_UNSUPPORTED,
            ),
            *(
                ViewNodeSpec(
                    item.named_frame.label,
                    item.named_frame.frame,
                    ViewRole.OTHER_UNSUPPORTED,
                )
                for item in self.corpus.resets
            ),
            *(
                ViewNodeSpec(item.label, item.frame, ViewRole.REVIEWED_SUPPORTED)
                for item in self.corpus.anchors
            ),
        )


@dataclass(frozen=True, slots=True)
class _AuthenticatedCampaignPrecursor:
    """Authenticated embedded precursor for the single R2.3 campaign."""

    mode: str
    commit: Frame
    post: Frame
    frame: Frame
    input_state: str
    receipt: dict[str, Any] | None
    input_start_clock_s: float | None
    input_receipt_clock_s: float | None
    source_registration: dict[str, Any]
    zero_click_north_qualification: dict[str, Any] | None
    campaign_reservation_id: str
    reservation_completed_clock_s: float
    window_hwnd: int
    window_process_id: int
    window_thread_id: int
    window_class_name: str
    window_title_sha256: str


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify one authenticated saved R2 bridge endpoint offline."
    )
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--r1-report", required=True, type=Path)
    parser.add_argument("--r1-sha256", required=True)
    parser.add_argument("--r2-report", required=True, type=Path)
    parser.add_argument("--r2-sha256", required=True)
    parser.add_argument("--capture-report", required=True, type=Path)
    parser.add_argument("--capture-sha256", required=True)
    parser.add_argument("--completion-sha256", required=True)
    parser.add_argument("--report", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_args(argv)
    command = tuple(sys.argv if argv is None else (str(Path(__file__)), *argv))
    try:
        head = _required_head(str(arguments.expected_head))
        before_head, before_clean = _git_state(_REPO_ROOT)
        if before_head != head or not before_clean:
            raise ValueError("verifier requires the exact clean expected Git head")
        frozen = _load_frozen_inputs(
            r1_report=arguments.r1_report,
            r1_sha256=str(arguments.r1_sha256),
            r2_report=arguments.r2_report,
            r2_sha256=str(arguments.r2_sha256),
            expected_head=head,
        )
        capture = _load_authenticated_capture(
            arguments.capture_report,
            expected_sha256=str(arguments.capture_sha256),
            expected_head=head,
            expected_r1_sha256=frozen.r1_report_sha256,
            expected_r2_sha256=frozen.r2_report_sha256,
            expected_r2_report_path=frozen.r2_report_path,
            expected_completion_sha256=str(arguments.completion_sha256),
            planner_source_frame=frozen.corpus.north.named_frame.frame,
            planner_source_path=frozen.corpus.north.named_frame.path,
        )
        verification = verify_camera_bridge_post(
            capture,
            base_specs=frozen.base_specs,
            prior_endpoint_sha256s=tuple(
                item.named_frame.sha256 for item in frozen.corpus.resets
            ),
            reviewed_manifest_sha256=frozen.corpus.r1.reviewed_manifest_sha256,
            reviewed_anchor_sha256s=frozenset(
                item.sha256 for item in frozen.corpus.anchors
            ),
        )
        after_head, after_clean = _git_state(_REPO_ROOT)
        if after_head != head or not after_clean:
            raise ValueError("Git provenance changed during offline verification")
        provenance = CameraReportProvenance(
            git_head_sha=head,
            detector_id=_EXPECTED_DETECTOR_ID,
            detector_version=_EXPECTED_DETECTOR_VERSION,
            profile_id=_EXPECTED_PROFILE_ID,
            plan_id=CAMERA_BRIDGE_VERIFIER_ID,
            plan_version=CAMERA_BRIDGE_VERIFIER_VERSION,
            command_argv=command,
            tracked_worktree_clean=True,
        )
        written = write_camera_validation_report(
            _resolve_output(arguments.report),
            _report_evidence(
                verification,
                frozen=frozen,
                before_head=before_head,
                after_head=after_head,
                completion_seal_sha256=str(arguments.completion_sha256),
            ),
            provenance,
        )
        try:
            published_head, published_clean = _git_state(_REPO_ROOT)
        except (OSError, subprocess.CalledProcessError) as error:
            _retract_published_report(
                written.report_path,
                written.digest_path,
            )
            raise ValueError(
                "Cannot re-establish Git provenance after report publication; "
                "output retracted"
            ) from error
        if published_head != head or not published_clean:
            _retract_published_report(
                written.report_path,
                written.digest_path,
            )
            raise ValueError(
                "Git provenance changed during report publication; output retracted"
            )
    except (
        FileExistsError,
        OSError,
        RuntimeError,
        subprocess.CalledProcessError,
        TypeError,
        ValueError,
    ) as error:
        print(f"R2 post verification failed: {error}", file=sys.stderr)
        return 2
    print(f"R2 post verification: {'PASS' if verification.verified else 'STOP'}")
    print(f"Report: {written.report_path}")
    print(f"Report SHA-256: {written.sha256}")
    return 0 if verification.verified else 1


def _load_frozen_inputs(
    *,
    r1_report: Path,
    r1_sha256: str,
    r2_report: Path,
    r2_sha256: str,
    expected_head: str,
) -> FrozenInputs:
    _required_digest(r1_sha256, "R1 report SHA-256")
    _required_digest(r2_sha256, "R2 report SHA-256")
    r1_path = _resolve_repo_report(r1_report)
    r2_path, r2_payload = _load_report(r2_report, r2_sha256)
    corpus = r2_analysis.load_bridge_corpus(
        _REPO_ROOT,
        r1_path,
        expected_head=expected_head,
        expected_r1_sha256=r1_sha256,
    )
    _validate_r2_report(
        r2_payload,
        expected_head=expected_head,
        expected_r1_sha256=r1_sha256,
        corpus=corpus,
    )
    return FrozenInputs(corpus, r1_sha256, r2_sha256, r1_path, r2_path)


def _validate_r2_report(
    payload: dict[str, Any],
    *,
    expected_head: str,
    expected_r1_sha256: str,
    corpus: r2_analysis.BridgeCorpus,
) -> None:
    if payload.get("schema_version") != 2:
        raise ValueError("R2 report schema must be version 2")
    _require_provenance(
        _object(payload.get("provenance"), "R2 provenance"),
        expected_head=expected_head,
        plan_id=_R2_PLAN_ID,
        plan_version=_R2_PLAN_VERSION,
    )
    evidence = _object(payload.get("evidence"), "R2 evidence")
    authority = _object(evidence.get("authority"), "R2 authority")
    if authority != {
        "diagnostic_registration_can_override_production": False,
        "live_camera_input_authorized": False,
        "live_camera_input_performed": False,
        "registration_can_authorize_camera_input": False,
        "registration_can_expose_resources": False,
        "registration_can_validate_scene": False,
    }:
        raise ValueError("R2 report retained authority")
    source = _object(evidence.get("r1_source"), "R2 R1 source")
    if source.get("report_sha256") != expected_r1_sha256:
        raise ValueError("R2 report does not bind the exact R1 report")
    planner = _object(evidence.get("bridge_planner"), "R2 planner")
    planner_authority = _object(planner.get("authority"), "R2 planner authority")
    if (
        planner.get("planner_id") != CAMERA_BRIDGE_PLANNER_ID
        or planner.get("planner_version") != CAMERA_BRIDGE_PLANNER_VERSION
        or planner.get("current_sha256") != FROZEN_ENDPOINT_SOURCE_SHA256
        or planner.get("disposition") != "no_safe_endpoint_evidence"
        or planner.get("missing_experiment") is not None
        or _array(planner.get("ranked_families"), "R2 ranked families")
        or planner_authority
        != {
            "can_accept": False,
            "can_authorize_camera_input": False,
            "can_expose_resources": False,
            "can_validate_scene": False,
            "diagnostic_registration_can_override_production": False,
        }
        or _object(planner.get("matrix_policy"), "R2 matrix policy")
        != {
            "rejected_registration_matrices_used_for_control": False,
            "rejected_registration_metrics_used_for_ranking": False,
        }
    ):
        raise ValueError("R2 planner evidence is not the reviewed no-safe result")
    inventory = _object(planner.get("inventory"), "R2 inventory")
    experiments = _array(inventory.get("experiments"), "R2 experiments")
    if (
        inventory.get("inventory_id")
        != "issue31-frozen-receipt-backed-camera-primitives-r2"
        or inventory.get("inventory_version") != "2.0.0"
        or experiments != [FROZEN_ENDPOINT_OBJECTIVE.as_dict()]
    ):
        raise ValueError("R2 inventory is not the sole frozen objective")
    families = _array(planner.get("family_evaluations"), "R2 families")
    if len(families) != 1:
        raise ValueError("R2 must contain one frozen endpoint family")
    family = _object(families[0], "R2 family")
    failures = _array(family.get("failure_reasons"), "R2 family failures")
    common = _array(
        family.get("qualifying_common_anchor_sha256s"), "R2 common anchors"
    )
    anchor_evaluations = _array(
        family.get("anchor_evaluations"), "R2 anchor evaluations"
    )
    completed = sorted(
        str(_object(item, "R2 anchor evaluation").get("anchor_sha256"))
        for item in anchor_evaluations
        if _object(item, "R2 anchor evaluation").get("complete") is True
    )
    if (
        family.get("family_id") != FROZEN_ENDPOINT_OBJECTIVE.family_id
        or family.get("complete") is not False
        or len(failures) != 1
        or not str(failures[0]).startswith("repeat_edge_not_verified_all_zones:")
        or not common
        or sorted(common) != completed
    ):
        raise ValueError("R2 family is not solely blocked by its repeat cycle")
    graph = _object(evidence.get("safe_view_graph"), "R2 graph")
    graph_authority = _object(graph.get("authority"), "R2 graph authority")
    reachability = _object(graph.get("reachability"), "R2 reachability")
    if (
        graph.get("graph_id") != ROBUST_VIEW_GRAPH_ID
        or graph.get("graph_version") != ROBUST_VIEW_GRAPH_VERSION
        or graph.get("current_sha256") != FROZEN_ENDPOINT_SOURCE_SHA256
        or graph.get("reviewed_manifest_sha256")
        != corpus.r1.reviewed_manifest_sha256
        or graph_authority != planner_authority
        or _array(graph.get("action_transitions"), "R2 action transitions")
        or reachability.get("action_path_to_supported") is not None
        or reachability.get("offline_controller_path_available") is not False
    ):
        raise ValueError("R2 safe graph identity is inconsistent")
    result = _object(evidence.get("result"), "R2 result")
    if (
        result.get("conclusion") != "no safe endpoint evidence"
        or result.get("live_input_authorized") is not False
        or result.get("reacquisition_success_claimed") is not False
        or result.get("selected_experiment_id") is not None
        or not isinstance(result.get("smallest_additional_evidence"), str)
    ):
        raise ValueError("R2 result is not the frozen no-safe STOP result")


def _load_authenticated_capture(
    report: Path,
    *,
    expected_sha256: str,
    expected_head: str,
    expected_r1_sha256: str,
    expected_r2_sha256: str,
    expected_r2_report_path: Path,
    expected_completion_sha256: str,
    planner_source_frame: Frame,
    planner_source_path: str,
) -> AuthenticatedBridgeCapture:
    report_path, payload = _load_report(report, expected_sha256, diagnostics_only=True)
    if payload.get("schema_version") != 2:
        raise ValueError("capture report schema must be version 2")
    provenance = _object(payload.get("provenance"), "capture provenance")
    _require_provenance(
        provenance,
        expected_head=expected_head,
        plan_id=CAMERA_BRIDGE_CAPTURE_ID,
        plan_version=CAMERA_BRIDGE_CAPTURE_VERSION,
        expected_command=_CAPTURE_COMMAND,
    )
    evidence = _object(payload.get("evidence"), "capture evidence")
    _require_r23_campaign_schema(evidence)
    input_start, input_receipt, arm_origin = _validate_capture_envelope(
        evidence,
        expected_r1_sha256=expected_r1_sha256,
        expected_r2_sha256=expected_r2_sha256,
    )
    receipt_value = _object(evidence.get("receipt"), "capture receipt")
    receipt = _typed_receipt(receipt_value)
    frames = _object(evidence.get("frames"), "capture frames")
    loaded: dict[str, tuple[Frame, CameraEvaluation]] = {}
    for stage in _STAGES:
        loaded[stage] = _load_frame_evidence(
            report_path,
            stage,
            _object(frames.get(stage), f"capture frames.{stage}"),
        )
    ordered_frames = [loaded[stage][0] for stage in _STAGES]
    if any(
        following.frame_id != preceding.frame_id + 1
        or following.captured_monotonic_s <= preceding.captured_monotonic_s
        for preceding, following in zip(
            ordered_frames, ordered_frames[1:], strict=False
        )
    ):
        raise ValueError("capture frame IDs/times are not strict and contiguous")
    guards = _object(evidence.get("guards"), "capture guards")
    expected_guards = {
        "decision_to_arm": _arm_guard_dict(
            evaluate_camera_arm_guard(ordered_frames[0], ordered_frames[1])
        ),
        "arm_to_commit": _arm_guard_dict(
            evaluate_camera_arm_guard(ordered_frames[1], ordered_frames[2])
        ),
        "decision_to_commit": _arm_guard_dict(
            evaluate_camera_arm_guard(ordered_frames[0], ordered_frames[2])
        ),
    }
    if guards != expected_guards:
        raise ValueError("capture arm guards do not bind exact pixels")
    commit, commit_production = loaded["commit"]
    post, post_production = loaded["post"]
    closure = _object(
        evidence.get("post_transition_closure"), "post-transition closure"
    )
    commit_sha = hashlib.sha256(commit.payload).hexdigest()
    post_sha = hashlib.sha256(post.payload).hexdigest()
    source_sha = hashlib.sha256(planner_source_frame.payload).hexdigest()
    if source_sha != FROZEN_ENDPOINT_SOURCE_SHA256:
        raise ValueError("planner source pixels do not match the frozen source")
    analysis = _object(evidence.get("analysis_evidence"), "capture analysis")
    if (
        analysis.get("source_raw_path") != planner_source_path
        or analysis.get("source_sha256") != source_sha
    ):
        raise ValueError("capture analysis does not bind the frozen source pixels")
    precursor = _load_authenticated_campaign_precursor(
        _object(evidence.get("campaign_precursor"), "campaign precursor"),
        report_path=report_path,
        planner_source_frame=planner_source_frame,
    )
    _validate_capture_command_argv(
        provenance,
        expected_head=expected_head,
        expected_r2_sha256=expected_r2_sha256,
        expected_r2_report_path=expected_r2_report_path,
        capture_report_path=report_path,
    )
    planner_source_registration = _object(
        evidence.get("planner_source_registration"),
        "planner source registration",
    )
    _require_exact_registration(
        planner_source_frame,
        precursor.frame,
        planner_source_registration,
        context="planner source to campaign precursor",
    )
    if planner_source_registration != precursor.source_registration:
        raise ValueError("campaign precursor retained two source registrations")
    precursor_registration = _object(
        evidence.get("precursor_to_commit_registration"),
        "campaign precursor to commit registration",
    )
    _require_exact_registration(
        precursor.frame,
        commit,
        precursor_registration,
        context="campaign precursor to commit",
    )
    pointer_mapping = _object(
        evidence.get("pointer_mapping"), "capture pointer mapping"
    )
    _validate_capture_pointer_mapping(pointer_mapping)
    if (
        pointer_mapping.get("selected_window_class_name")
        != precursor.window_class_name
        or pointer_mapping.get("selected_window_title_sha256")
        != precursor.window_title_sha256
    ):
        raise ValueError("capture target does not match the campaign window")
    authorization = _authenticate_capture_campaign_authorization(
        evidence.get("campaign_authorization"),
        expected_head=expected_head,
        expected_r1_sha256=expected_r1_sha256,
        expected_r2_sha256=expected_r2_sha256,
        precursor=precursor,
    )
    reservation_completed = _validate_ordered_campaign_receipt(
        _object(
            evidence.get("ordered_campaign_receipt"),
            "ordered campaign receipt",
        ),
        precursor=precursor,
        authorization=authorization,
        bridge_receipt=receipt_value,
        bridge_commit=commit,
        bridge_post=post,
        bridge_input_start=input_start,
        bridge_input_receipt=input_receipt,
    )
    _validate_capture_campaign_chronology(
        precursor=precursor,
        reservation_completed_clock_s=reservation_completed,
        decision=loaded["decision"][0],
        arm=loaded["arm"][0],
        arm_origin=arm_origin,
        commit=commit,
        input_start=input_start,
        input_receipt=input_receipt,
        post=post,
    )
    _validate_closure(closure, commit_sha=commit_sha, post_sha=post_sha)
    reported_post = _object(
        evidence.get("post_transition_production_re_evaluation"),
        "post-transition production",
    )
    if reported_post != _evaluation_dict(post_production):
        raise ValueError("sealed post production does not match exact pixels")
    reported_registration = _object(
        evidence.get("post_transition_registration"),
        "post-transition registration",
    )
    _validate_registration_binding(
        reported_registration,
        commit_sha=commit_sha,
        post_sha=post_sha,
    )
    _require_recomputed_registration(commit, post, reported_registration)
    if _production_dict(commit_production) != _object(
        _object(frames.get("commit"), "commit evidence").get("production"),
        "commit production",
    ):
        raise ValueError("commit production changed during authentication")
    _authenticate_capture_completion_seal(
        evidence,
        expected_head=expected_head,
        expected_seal_sha256=expected_completion_sha256,
        capture_report_sha256=expected_sha256,
        authorization=authorization,
        commit_sha256=commit_sha,
        post_sha256=post_sha,
    )
    return AuthenticatedBridgeCapture(
        report_sha256=expected_sha256,
        objective_id=FROZEN_ENDPOINT_OBJECTIVE_ID,
        objective_source_sha256=FROZEN_ENDPOINT_SOURCE_SHA256,
        receipt=receipt,
        commit=commit,
        post=post,
        reported_post_production=post_production,
    )


def _require_r23_campaign_schema(evidence: dict[str, Any]) -> None:
    """Reject every pre-R2.3 external-north authorization envelope."""

    legacy_fields = {
        "compass_north_handoff",
        "one_shot_authorization",
    }
    retained_legacy = sorted(legacy_fields.intersection(evidence))
    if retained_legacy:
        raise ValueError(
            "capture report retained legacy generic north authority: "
            + ", ".join(retained_legacy)
        )
    for field_name in (
        "campaign_precursor",
        "campaign_authorization",
        "ordered_campaign_receipt",
    ):
        _object(evidence.get(field_name), f"capture {field_name}")


def _authenticate_capture_campaign_authorization(
    value: object,
    *,
    expected_head: str,
    expected_r1_sha256: str,
    expected_r2_sha256: str,
    precursor: _AuthenticatedCampaignPrecursor,
) -> CameraBridgeAuthorizationReservation:
    """Re-read the fixed host-global campaign sentinel and bind it exactly."""

    authorization_value = _object(value, "capture campaign authorization")
    authorization_sha256 = authorization_value.get("sentinel_sha256")
    if not isinstance(authorization_sha256, str):
        raise ValueError("capture campaign sentinel SHA-256 is missing")
    if authorization_value.get("campaign_reservation_id") != authorization_sha256:
        raise ValueError("capture campaign reservation ID is inconsistent")
    authorization_evidence = CameraBridgeAuthorizationEvidence(
        r1_report_sha256=expected_r1_sha256,
        r2_report_sha256=expected_r2_sha256,
        precursor_mode=precursor.mode,
        precursor_commit_sha256=hashlib.sha256(precursor.commit.payload).hexdigest(),
        target_hwnd=precursor.window_hwnd,
        target_process_id=precursor.window_process_id,
        target_thread_id=precursor.window_thread_id,
        target_class_name=precursor.window_class_name,
        target_title_sha256=precursor.window_title_sha256,
    )
    authorization = authenticate_camera_bridge_authorization(
        _REPO_ROOT,
        git_head_sha=expected_head,
        expected_sentinel_sha256=authorization_sha256,
        evidence=authorization_evidence,
    )
    if precursor.campaign_reservation_id != authorization.sentinel_sha256:
        raise ValueError("campaign precursor reservation ID is inconsistent")
    if authorization_value != authorization.as_dict():
        raise ValueError(
            "capture campaign authorization does not bind the fixed sentinel"
        )
    return authorization


def _authenticate_capture_completion_seal(
    evidence: dict[str, Any],
    *,
    expected_head: str,
    expected_seal_sha256: str,
    capture_report_sha256: str,
    authorization: CameraBridgeAuthorizationReservation,
    commit_sha256: str,
    post_sha256: str,
) -> None:
    """Require the source-owned seal of the complete serialized transaction."""

    _required_digest(expected_seal_sha256, "completion seal SHA-256")
    ordered_campaign_receipt = _object(
        evidence.get("ordered_campaign_receipt"),
        "ordered campaign receipt",
    )
    frames = _object(evidence.get("frames"), "capture frames")
    closure = _object(
        evidence.get("post_transition_closure"), "post-transition closure"
    )
    pointer_mapping = _object(
        evidence.get("pointer_mapping"), "capture pointer mapping"
    )
    completion_evidence = CameraBridgeCompletionEvidence(
        authorization_sentinel_sha256=authorization.sentinel_sha256,
        capture_report_sha256=capture_report_sha256,
        ordered_campaign_receipt_sha256=(
            canonical_camera_bridge_component_sha256(ordered_campaign_receipt)
        ),
        stage_chain_sha256=canonical_camera_bridge_component_sha256(
            {
                "arm_age": evidence.get("arm_age"),
                "frames": frames,
                "guards": evidence.get("guards"),
                "input": evidence.get("input"),
                "preflight": evidence.get("preflight"),
            }
        ),
        commit_sha256=commit_sha256,
        post_sha256=post_sha256,
        pointer_mapping_sha256=canonical_camera_bridge_component_sha256(
            pointer_mapping
        ),
        registrations_sha256=canonical_camera_bridge_component_sha256(
            {
                "campaign_precursor": evidence.get("campaign_precursor"),
                "precursor_to_commit_registration": evidence.get(
                    "precursor_to_commit_registration"
                ),
                "planner_source_registration": evidence.get(
                    "planner_source_registration"
                ),
                "post_transition_registration": evidence.get(
                    "post_transition_registration"
                ),
            }
        ),
        closure_sha256=canonical_camera_bridge_component_sha256(closure),
    )
    authenticate_camera_bridge_completion(
        _REPO_ROOT,
        git_head_sha=expected_head,
        expected_seal_sha256=expected_seal_sha256,
        evidence=completion_evidence,
    )


def _validate_capture_envelope(
    evidence: dict[str, Any],
    *,
    expected_r1_sha256: str,
    expected_r2_sha256: str,
) -> tuple[float, float, float]:
    authority = _object(evidence.get("authority"), "capture authority")
    if any(
        authority.get(key) is not False
        for key in (
            "can_accept",
            "can_authorize_camera_input",
            "can_expose_resources",
            "can_validate_scene",
            "diagnostic_registration_can_override_production",
            "input_receipt_is_scene_acceptance",
        )
    ) or authority.get("production_remains_sole_scene_authority") is not True:
        raise ValueError("capture report retained forbidden authority")
    analysis = _object(evidence.get("analysis_evidence"), "capture analysis")
    if (
        analysis.get("report_sha256") != expected_r2_sha256
        or analysis.get("r1_report_sha256") != expected_r1_sha256
        or analysis.get("planner_id") != CAMERA_BRIDGE_PLANNER_ID
        or analysis.get("planner_version") != CAMERA_BRIDGE_PLANNER_VERSION
        or analysis.get("objective_id") != FROZEN_ENDPOINT_OBJECTIVE_ID
        or analysis.get("source_sha256") != FROZEN_ENDPOINT_SOURCE_SHA256
    ):
        raise ValueError("capture report does not bind the exact R1/R2 objective")
    capture = _object(evidence.get("bridge_capture"), "bridge capture")
    if (
        capture.get("id") != CAMERA_BRIDGE_CAPTURE_ID
        or capture.get("version") != CAMERA_BRIDGE_CAPTURE_VERSION
        or capture.get("protocol_completed") is not True
        or capture.get("physical_capture_protocol_completed") is not True
        or capture.get("post_transition_closure_completed") is not True
        or capture.get("post_production_passed") is not False
    ):
        raise ValueError("capture protocol is incomplete")
    input_evidence = _object(evidence.get("input"), "capture input")
    if input_evidence.get("attempted") is not True or input_evidence.get(
        "completed"
    ) is not True or input_evidence.get("state") != "complete":
        raise ValueError("capture input receipt is incomplete")
    start = input_evidence.get("start_clock_s")
    received = input_evidence.get("receipt_clock_s")
    duration = input_evidence.get("delivery_duration_s")
    if any(
        isinstance(item, bool)
        or not isinstance(item, (int, float))
        or not math.isfinite(float(item))
        for item in (start, received, duration)
    ):
        raise ValueError("capture input timing is incomplete")
    assert isinstance(start, (int, float))
    assert isinstance(received, (int, float))
    assert isinstance(duration, (int, float))
    if (
        received < start
        or float(duration) != float(received) - float(start)
        or float(duration) < CAMERA_BRIDGE_CAPTURE_HOLD_SECONDS
    ):
        raise ValueError("capture input timing is inconsistent")
    if evidence.get("preflight") != {
        "client_height": EXPECTED_CLIENT_HEIGHT,
        "client_width": EXPECTED_CLIENT_WIDTH,
        "focused": True,
        "supported": True,
    }:
        raise ValueError("capture preflight is not exact")
    arm_age = _object(evidence.get("arm_age"), "capture arm age")
    origin = arm_age.get("origin_clock_s")
    final = arm_age.get("final_clock_s")
    age = arm_age.get("age_s")
    maximum = arm_age.get("maximum_age_s")
    if (
        arm_age.get("status") != "within_limit"
        or any(
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(float(item))
            for item in (origin, final, age, maximum)
        )
        or final != start
        or float(maximum) != MAXIMUM_ARM_TO_INPUT_AGE_SECONDS
        or float(age) != float(final) - float(origin)
        or not 0.0 <= float(age) < MAXIMUM_ARM_TO_INPUT_AGE_SECONDS
    ):
        raise ValueError("capture arm age does not bind the input seam")
    if (
        evidence.get("command") != _CAPTURE_COMMAND
        or evidence.get("development_only") is not True
        or evidence.get("terminal_reason") != "capture_complete"
        or evidence.get("exception") is not None
        or evidence.get("tracked_worktree_clean") is not True
        or evidence.get("plan") != _bridge_plan_dict()
        or evidence.get("transition_candidate_eligible") is not False
        or evidence.get("action_transition_emitted") is not False
        or evidence.get("authenticated_ingestion_required") is not True
        or evidence.get("same_transaction_closure_completed") is not True
    ):
        raise ValueError("capture is not a sealed report-only artifact")
    registration_execution = _object(
        evidence.get("registration_execution"), "registration execution"
    )
    if registration_execution != {
        "north_to_commit_executed_in_input_seam": True,
        "planner_source_to_north_precomputed_before_arm": True,
        "post_transition_registration_performed": True,
        "post_transition_registration_stage": (
            "same_transaction_before_production_re_evaluation_and_report_seal"
        ),
        "production_re_evaluated_after_registration": True,
    } or any(
        (
            evidence.get("new_live_input_from_robust_registration") is not False,
            evidence.get("post_capture_registration_required") is not True,
            evidence.get("production_detector_remains_sole_scene_authority")
            is not True,
            evidence.get("robust_registration_can_authorize_input_alone")
            is not False,
        )
    ):
        raise ValueError("capture registration execution is not fixed/report-only")
    objective = _object(evidence.get("bridge_objective"), "bridge objective")
    primitive = _object(objective.get("first_missing_primitive"), "primitive")
    if (
        objective.get("id") != FROZEN_ENDPOINT_OBJECTIVE_ID
        or primitive
        != {"duration_seconds": CAMERA_BRIDGE_CAPTURE_HOLD_SECONDS, "key": "right"}
    ):
        raise ValueError("capture objective is not the frozen primitive")
    fixed = _object(evidence.get("fixed_policy"), "fixed capture policy")
    if (
        fixed.get("hold_seconds") != CAMERA_BRIDGE_CAPTURE_HOLD_SECONDS
        or fixed.get("key") != "right"
        or fixed.get("maximum_physical_primitives")
        != CAMERA_BRIDGE_CAPTURE_MAXIMUM_PHYSICAL_PRIMITIVES
        or fixed.get("post_action_settle_seconds")
        != CAMERA_BRIDGE_CAPTURE_SETTLE_SECONDS
        or any(
            fixed.get(name) is not False
            for name in (
                "caller_selectable_axis",
                "caller_selectable_coordinate",
                "caller_selectable_direction",
                "caller_selectable_evaluator",
                "caller_selectable_magnitude",
                "caller_selectable_plan",
            )
        )
    ):
        raise ValueError("capture fixed policy changed")
    return float(start), float(received), float(origin)


def _typed_receipt(value: dict[str, Any]) -> CameraPlanReceipt:
    plan = camera_bridge_capture_plan()
    expected_plan = _bridge_plan_dict()
    expected_preflight = {
        "client_height": EXPECTED_CLIENT_HEIGHT,
        "client_width": EXPECTED_CLIENT_WIDTH,
        "focused": True,
        "supported": True,
    }
    expected_inputs = [
        {"complete": True, "completed_events": 1, "operation": "key_down", "requested_events": 1},
        {"complete": True, "completed_events": 1, "operation": "key_up", "requested_events": 1},
    ]
    if value.get("plan") != expected_plan or value.get("preflight") != expected_preflight:
        raise ValueError("capture receipt plan/preflight mismatch")
    actions = _array(value.get("actions"), "capture receipt actions")
    if len(actions) != 1:
        raise ValueError("capture receipt must contain one action")
    action = _object(actions[0], "capture action receipt")
    if action.get("action_index") != 0 or action.get("input_receipts") != expected_inputs:
        raise ValueError("capture receipt is partial or not the fixed action")
    preflight = CameraPreflightReceipt(True, EXPECTED_CLIENT_WIDTH, EXPECTED_CLIENT_HEIGHT)
    input_receipts = (
        CameraInputReceipt(CameraInputOperation.KEY_DOWN, 1, 1),
        CameraInputReceipt(CameraInputOperation.KEY_UP, 1, 1),
    )
    return CameraPlanReceipt(
        plan,
        preflight,
        (CameraActionReceipt(0, plan.actions[0], input_receipts),),
    )


def _bridge_plan_dict() -> dict[str, object]:
    return {
        "name": camera_bridge_capture_plan().name,
        "actions": [
            {
                "duration_s": CAMERA_BRIDGE_CAPTURE_HOLD_SECONDS,
                "key": "right",
                "kind": "key_hold",
            }
        ],
    }


def _load_embedded_bootstrap_frame(
    report_path: Path,
    value: dict[str, Any],
    *,
    expected_label: str,
    context: str,
) -> tuple[Frame, CameraEvaluation]:
    """Load one embedded canonical bootstrap frame from exact private pixels."""

    artifact = _object(value.get("artifact"), f"{context} artifact")
    files = _object(artifact.get("files"), f"{context} artifact files")
    frame_id = artifact.get("frame_id")
    captured = artifact.get("captured_monotonic_s")
    raw_sha256 = artifact.get("raw_sha256")
    raw_reference = files.get("raw")
    case_prefix = report_path.name.removesuffix(".camera.json")
    expected_raw_reference = f"frames/{case_prefix}-{expected_label}.raw"
    if (
        not report_path.name.endswith(".camera.json")
        or artifact.get("label") != expected_label
        or artifact.get("width") != EXPECTED_CLIENT_WIDTH
        or artifact.get("height") != EXPECTED_CLIENT_HEIGHT
        or artifact.get("pixel_format") != PixelFormat.BGRA8888.value
        or isinstance(frame_id, bool)
        or not isinstance(frame_id, int)
        or frame_id <= 0
        or isinstance(captured, bool)
        or not isinstance(captured, (int, float))
        or not math.isfinite(float(captured))
        or float(captured) < 0.0
        or not isinstance(raw_sha256, str)
        or not isinstance(raw_reference, str)
        or raw_reference != expected_raw_reference
    ):
        raise ValueError(f"{context} artifact identity is invalid")
    _required_digest(raw_sha256, f"{context} raw SHA-256")
    root = report_path.parent.parent.resolve()
    diagnostics_root = (_REPO_ROOT / "diagnostics").resolve()
    if root != diagnostics_root and diagnostics_root not in root.parents:
        raise ValueError(f"{context} artifact root is outside diagnostics")
    raw_path = (root / raw_reference).resolve()
    if root not in raw_path.parents or not raw_path.is_file():
        raise ValueError(f"{context} raw path escaped or is missing")
    raw_payload = raw_path.read_bytes()
    if hashlib.sha256(raw_payload).hexdigest() != raw_sha256:
        raise ValueError(f"{context} raw SHA-256 mismatch")
    frame = Frame.from_raw(
        RawFrame(
            raw_payload,
            EXPECTED_CLIENT_WIDTH,
            EXPECTED_CLIENT_HEIGHT,
            PixelFormat.BGRA8888,
        ),
        frame_id=frame_id,
        captured_monotonic_s=float(captured),
    )
    readiness = evaluate_client_input_readiness(frame)
    production = evaluate_varrock_east_camera(frame)
    if _north_readiness_dict(readiness) != _object(
        value.get("readiness"), f"{context} readiness"
    ):
        raise ValueError(f"{context} readiness does not bind exact pixels")
    if _evaluation_dict(production) != _object(
        value.get("production"), f"{context} production"
    ):
        raise ValueError(f"{context} production does not bind exact pixels")
    if not readiness.safe_to_attempt_camera_input or not _is_fail_closed(production):
        raise ValueError(f"{context} is not gameplay-ready and fail closed")
    return frame, production


def _campaign_window_binding(
    value: dict[str, Any],
) -> tuple[int, int, int, str, str]:
    expected_fields = {"class_name", "hwnd", "process_id", "thread_id", "title_sha256"}
    title_sha256 = value.get("title_sha256")
    if isinstance(title_sha256, str):
        _required_digest(title_sha256, "campaign window title SHA-256")
    integers = (value.get("hwnd"), value.get("process_id"), value.get("thread_id"))
    class_name = value.get("class_name")
    if (
        set(value) != expected_fields
        or any(
            isinstance(item, bool) or not isinstance(item, int) or item <= 0
            for item in integers
        )
        or not isinstance(class_name, str)
        or not class_name.strip()
        or not isinstance(title_sha256, str)
    ):
        raise ValueError("campaign precursor window binding is invalid")
    hwnd, process_id, thread_id = integers
    assert isinstance(hwnd, int)
    assert isinstance(process_id, int)
    assert isinstance(thread_id, int)
    return hwnd, process_id, thread_id, class_name, title_sha256


def _north_plan_dict() -> dict[str, object]:
    return {
        "actions": [
            {
                "kind": "compass_click",
                "x": REVIEWED_COMPASS_POINT[0],
                "y": REVIEWED_COMPASS_POINT[1],
            }
        ],
        "name": _NORTH_PLAN_ID,
    }


def _validate_compass_bootstrap(
    report_path: Path,
    value: dict[str, Any],
) -> tuple[Frame, Frame, dict[str, Any], float, float]:
    """Authenticate the embedded, fixed one-click compass precursor."""

    plan = _north_plan_dict()
    preflight = {
        "client_height": EXPECTED_CLIENT_HEIGHT,
        "client_width": EXPECTED_CLIENT_WIDTH,
        "focused": True,
        "supported": True,
    }
    expected_action = {
        "action": plan["actions"][0],
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
    receipt = _object(value.get("receipt"), "campaign compass receipt")
    identity = _object(value.get("identity_policy"), "campaign compass identity")
    assumptions = _object(
        value.get("camera_assumptions"), "campaign compass assumptions"
    )
    acceptance = _object(value.get("acceptance"), "campaign compass acceptance")
    combined = _object(
        value.get("combined_issue31_acceptance"),
        "campaign compass combined acceptance",
    )
    if (
        value.get("command") != _NORTH_COMMAND
        or value.get("development_only") is not True
        or value.get("terminal_reason") != "bootstrap_executed"
        or value.get("exception") is not None
        or value.get("tracked_worktree_clean") is not True
        or value.get("camera_evidence_eligible") is not False
        or not isinstance(value.get("detail"), str)
        or not str(value.get("detail")).strip()
        or value.get("plan") != plan
        or value.get("preflight") != preflight
        or receipt.get("plan") != plan
        or receipt.get("preflight") != preflight
        or _array(receipt.get("actions"), "campaign compass receipt actions")
        != [expected_action]
        or identity
        != {
            "detector_id": _EXPECTED_DETECTOR_ID,
            "detector_version": _EXPECTED_DETECTOR_VERSION,
            "profile_id": _EXPECTED_PROFILE_ID,
            "profile_schema_version": 3,
            "guidance_v2_id": _NORTH_GUIDANCE_ID,
            "guidance_v2_version": _NORTH_GUIDANCE_VERSION,
        }
        or assumptions.get("compass_point") != list(REVIEWED_COMPASS_POINT)
        or assumptions.get("post_action_settle_s")
        != CAMERA_BRIDGE_CAPTURE_SETTLE_SECONDS
        or assumptions.get("maximum_semantic_actions") != 1
        or assumptions.get("permitted_action") != "compass_click"
        or assumptions.get("diagnostics_can_override_production") is not False
        or isinstance(assumptions.get("compass_click_dwell_s"), bool)
        or not isinstance(assumptions.get("compass_click_dwell_s"), (int, float))
        or not math.isfinite(float(assumptions.get("compass_click_dwell_s")))
        or float(assumptions.get("compass_click_dwell_s"))
        != _COMPASS_CLICK_DWELL_SECONDS
        or acceptance
        != {
            "authority": "unchanged_production_evaluator_only",
            "passed": False,
            "input_receipt_is_acceptance": False,
            "capture_is_acceptance": False,
        }
        or combined
        != {
            "complete": False,
            "reviewed_live_resource_states_included": False,
            "same_head_drift_proof_included": False,
        }
    ):
        raise ValueError("campaign compass bootstrap is not fixed and complete")
    _validate_north_pointer_mapping(
        _object(value.get("pointer_mapping"), "campaign compass pointer mapping")
    )
    frames = _object(value.get("frames"), "campaign compass frames")
    loaded: dict[str, Frame] = {}
    for stage in ("initial", "arm", "commit", "post"):
        loaded[stage] = _load_embedded_bootstrap_frame(
            report_path,
            _object(frames.get(stage), f"campaign compass {stage}"),
            expected_label=f"v2-{stage}",
            context=f"campaign compass {stage}",
        )[0]
    ordered = [loaded[stage] for stage in ("initial", "arm", "commit", "post")]
    if any(
        following.frame_id <= preceding.frame_id
        or following.captured_monotonic_s <= preceding.captured_monotonic_s
        for preceding, following in zip(ordered, ordered[1:], strict=False)
    ):
        raise ValueError("campaign compass frame chronology is not strict")
    guards = _object(value.get("guards"), "campaign compass guards")
    expected_guards = {
        "decision_to_arm": _arm_guard_dict(
            evaluate_camera_arm_guard(loaded["initial"], loaded["arm"])
        ),
        "arm_to_commit": _arm_guard_dict(
            evaluate_camera_arm_guard(loaded["arm"], loaded["commit"])
        ),
        "decision_to_commit": _arm_guard_dict(
            evaluate_camera_arm_guard(loaded["initial"], loaded["commit"])
        ),
    }
    if guards != expected_guards:
        raise ValueError("campaign compass guards do not bind exact pixels")
    input_evidence = _object(value.get("input"), "campaign compass input")
    start = input_evidence.get("start_clock_s")
    received = input_evidence.get("receipt_clock_s")
    duration = input_evidence.get("delivery_duration_s")
    if (
        input_evidence.get("state") != "complete"
        or input_evidence.get("attempted") is not True
        or input_evidence.get("completed") is not True
        or any(
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(float(item))
            for item in (start, received, duration)
        )
    ):
        raise ValueError("campaign compass input timing is incomplete")
    assert isinstance(start, (int, float))
    assert isinstance(received, (int, float))
    assert isinstance(duration, (int, float))
    compass_dwell = assumptions.get("compass_click_dwell_s")
    assert isinstance(compass_dwell, (int, float))
    if (
        received < start
        or float(duration) != float(received) - float(start)
        or float(duration) < float(compass_dwell)
    ):
        raise ValueError("campaign compass input timing is inconsistent")
    arm_age = _object(value.get("arm_age"), "campaign compass arm age")
    origin = arm_age.get("origin_clock_s")
    final = arm_age.get("final_clock_s")
    age = arm_age.get("age_s")
    maximum = arm_age.get("maximum_age_s")
    if (
        arm_age.get("status") != "within_limit"
        or any(
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(float(item))
            for item in (origin, final, age, maximum)
        )
        or final != start
        or float(maximum) != MAXIMUM_ARM_TO_INPUT_AGE_SECONDS
        or float(age) != float(final) - float(origin)
        or not 0.0 <= float(age) < MAXIMUM_ARM_TO_INPUT_AGE_SECONDS
    ):
        raise ValueError("campaign compass arm age is not exact")
    assert isinstance(origin, (int, float))
    if not (
        loaded["initial"].captured_monotonic_s
        < loaded["arm"].captured_monotonic_s
        <= float(origin)
        <= loaded["commit"].captured_monotonic_s
        <= float(start)
        <= float(received)
    ):
        raise ValueError("campaign compass pre-input chronology is invalid")
    if (
        loaded["post"].captured_monotonic_s
        < float(received) + CAMERA_BRIDGE_CAPTURE_SETTLE_SECONDS
    ):
        raise ValueError("campaign compass post predates its fixed settle interval")
    for field_name, frame, normalized in (
        ("guidance", loaded["initial"], False),
        ("post_guidance", loaded["post"], True),
    ):
        guidance = _object(value.get(field_name), f"campaign compass {field_name}")
        decision = _object(
            guidance.get("decision_frame"), f"campaign compass {field_name} frame"
        )
        if (
            guidance.get("heading_was_normalized") is not normalized
            or decision
            != {
                "frame_id": frame.frame_id,
                "captured_monotonic_s": frame.captured_monotonic_s,
                "raw_sha256": hashlib.sha256(frame.payload).hexdigest(),
            }
        ):
            raise ValueError(f"campaign compass {field_name} is not frame-bound")
    return loaded["commit"], loaded["post"], receipt, float(start), float(received)


def _load_authenticated_campaign_precursor(
    value: dict[str, Any],
    *,
    report_path: Path,
    planner_source_frame: Frame,
) -> _AuthenticatedCampaignPrecursor:
    """Authenticate the report-embedded zero/click precursor and exact pixels."""

    expected_fields = {
        "mode",
        "physical_primitive_count",
        "captured_monotonic_s",
        "frame_id",
        "raw_sha256",
        "frame",
        "bootstrap",
        "source_to_precursor_registration",
        "zero_click_north_qualification",
        "campaign_reservation_id",
        "reservation_completed_clock_s",
        "registration_can_authorize_input_alone",
        "production_remains_sole_scene_authority",
        "embedded_same_process_and_input_lease",
        "external_north_report_accepted",
        "window_binding",
    }
    mode = value.get("mode")
    if mode not in ("compass_click", "zero_click") or set(value) != expected_fields:
        raise ValueError("campaign precursor schema/mode is invalid")
    reservation_id = value.get("campaign_reservation_id")
    reservation_completed = value.get("reservation_completed_clock_s")
    if not isinstance(reservation_id, str):
        raise ValueError("campaign precursor reservation ID is missing")
    _required_digest(reservation_id, "campaign precursor reservation ID")
    if (
        isinstance(reservation_completed, bool)
        or not isinstance(reservation_completed, (int, float))
        or not math.isfinite(float(reservation_completed))
        or float(reservation_completed) < 0.0
    ):
        raise ValueError("campaign precursor reservation clock is invalid")
    expected_count = 1 if mode == "compass_click" else 0
    if (
        value.get("physical_primitive_count") != expected_count
        or value.get("registration_can_authorize_input_alone") is not False
        or value.get("production_remains_sole_scene_authority") is not True
        or value.get("embedded_same_process_and_input_lease") is not True
        or value.get("external_north_report_accepted") is not False
    ):
        raise ValueError("campaign precursor retained invalid authority or count")
    frame_value = _object(value.get("frame"), "campaign precursor frame")
    expected_label = "v2-post" if mode == "compass_click" else "r2-campaign-precursor"
    frame = _load_embedded_bootstrap_frame(
        report_path,
        frame_value,
        expected_label=expected_label,
        context="campaign precursor",
    )[0]
    frame_sha256 = hashlib.sha256(frame.payload).hexdigest()
    if (
        value.get("frame_id") != frame.frame_id
        or value.get("captured_monotonic_s") != frame.captured_monotonic_s
        or value.get("raw_sha256") != frame_sha256
    ):
        raise ValueError("campaign precursor summary does not bind exact pixels")
    source_registration = _object(
        value.get("source_to_precursor_registration"),
        "source-to-precursor registration",
    )
    recomputed_source_registration = _require_exact_registration(
        planner_source_frame,
        frame,
        source_registration,
        context="planner source to campaign precursor",
    )
    window = _campaign_window_binding(
        _object(value.get("window_binding"), "campaign precursor window binding")
    )
    bootstrap_value = value.get("bootstrap")
    north_qualification_value = value.get("zero_click_north_qualification")
    if mode == "zero_click":
        if bootstrap_value is not None:
            raise ValueError("zero-click precursor cannot contain a bootstrap")
        north_qualification = _object(
            north_qualification_value,
            "zero-click exact frozen-north qualification",
        )
        recomputed_north_qualification = (
            qualify_exact_frozen_north_registration(
                recomputed_source_registration
            ).as_dict()
        )
        if north_qualification != recomputed_north_qualification:
            raise ValueError(
                "zero-click precursor does not prove exact frozen north pixels"
            )
        commit = frame
        post = frame
        receipt = {
            "kind": "zero_click_observation",
            "physical_input_attempted": False,
            "physical_input_completed": False,
            "frame_sha256": frame_sha256,
            "source_registration_sha256": (
                canonical_camera_bridge_component_sha256(source_registration)
            ),
            "north_qualification_sha256": (
                canonical_camera_bridge_component_sha256(north_qualification)
            ),
        }
        input_state = "none"
        input_start = None
        input_received = None
    else:
        if north_qualification_value is not None:
            raise ValueError(
                "compass precursor cannot contain zero-click qualification"
            )
        north_qualification = None
        bootstrap = _object(bootstrap_value, "campaign compass bootstrap")
        commit, post, receipt, input_start, input_received = (
            _validate_compass_bootstrap(report_path, bootstrap)
        )
        frames = _object(bootstrap.get("frames"), "campaign compass frames")
        if frame_value != _object(frames.get("post"), "campaign compass post"):
            raise ValueError("campaign precursor is not the embedded compass post")
        if post != frame:
            raise ValueError("campaign precursor compass post pixels are inconsistent")
        input_state = "complete"
    return _AuthenticatedCampaignPrecursor(
        mode=mode,
        commit=commit,
        post=post,
        frame=frame,
        input_state=input_state,
        receipt=receipt,
        input_start_clock_s=input_start,
        input_receipt_clock_s=input_received,
        source_registration=source_registration,
        zero_click_north_qualification=north_qualification,
        campaign_reservation_id=reservation_id,
        reservation_completed_clock_s=float(reservation_completed),
        window_hwnd=window[0],
        window_process_id=window[1],
        window_thread_id=window[2],
        window_class_name=window[3],
        window_title_sha256=window[4],
    )


def _validate_capture_campaign_chronology(
    *,
    precursor: _AuthenticatedCampaignPrecursor,
    reservation_completed_clock_s: float,
    decision: Frame,
    arm: Frame,
    arm_origin: float,
    commit: Frame,
    input_start: float,
    input_receipt: float,
    post: Frame,
) -> None:
    if not (
        precursor.frame.captured_monotonic_s
        <= decision.captured_monotonic_s
        < arm.captured_monotonic_s
        <= arm_origin
        <= commit.captured_monotonic_s
        <= input_start
        <= input_receipt
    ):
        raise ValueError(
            "capture chronology must be precursor <= decision < arm <= arm origin "
            "<= commit <= input start <= receipt"
        )
    if (
        isinstance(reservation_completed_clock_s, bool)
        or not isinstance(reservation_completed_clock_s, (int, float))
        or not math.isfinite(float(reservation_completed_clock_s))
        or reservation_completed_clock_s < 0.0
    ):
        raise ValueError("campaign reservation completion clock is invalid")
    if precursor.mode == "compass_click":
        start = precursor.input_start_clock_s
        received = precursor.input_receipt_clock_s
        if start is None or received is None or not (
            precursor.commit.captured_monotonic_s
            <= reservation_completed_clock_s
            <= start
            <= received
            <= precursor.post.captured_monotonic_s
            <= decision.captured_monotonic_s
        ):
            raise ValueError(
                "campaign reservation/compass chronology is not authenticated"
            )
    elif not (
        commit.captured_monotonic_s
        <= reservation_completed_clock_s
        <= input_start
    ):
        raise ValueError("zero-click reservation chronology is not authenticated")
    precursor_age = input_start - precursor.frame.captured_monotonic_s
    if not 0.0 <= precursor_age < _BRIDGE_NORTH_MAXIMUM_AGE_SECONDS:
        raise ValueError("campaign precursor reached its exclusive age limit")
    if (
        post.captured_monotonic_s
        < input_receipt + CAMERA_BRIDGE_CAPTURE_SETTLE_SECONDS
    ):
        raise ValueError("capture post frame predates the fixed settle interval")


def _validate_ordered_campaign_receipt(
    value: dict[str, Any],
    *,
    precursor: _AuthenticatedCampaignPrecursor,
    authorization: CameraBridgeAuthorizationReservation,
    bridge_receipt: dict[str, Any],
    bridge_commit: Frame,
    bridge_post: Frame,
    bridge_input_start: float,
    bridge_input_receipt: float,
) -> float:
    """Require the exact optional-compass then fixed-Right campaign receipt."""

    reservation_completed = value.get("reservation_completed_clock_s")
    if (
        isinstance(reservation_completed, bool)
        or not isinstance(reservation_completed, (int, float))
        or not math.isfinite(float(reservation_completed))
        or float(reservation_completed) < 0.0
    ):
        raise ValueError("ordered campaign reservation clock is invalid")
    if (
        value.get("reservation_id") != precursor.campaign_reservation_id
        or float(reservation_completed)
        != precursor.reservation_completed_clock_s
    ):
        raise ValueError("ordered campaign reservation binding is inconsistent")
    allowed_order = [
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
    ]
    precursor_commit_sha256 = hashlib.sha256(precursor.commit.payload).hexdigest()
    precursor_post_sha256 = hashlib.sha256(precursor.post.payload).hexdigest()
    expected_stages = [
        {
            "ordinal": 0,
            "stage": "north_precursor",
            "mode": precursor.mode,
            "commit_sha256": precursor_commit_sha256,
            "post_sha256": precursor_post_sha256,
            "input_state": precursor.input_state,
            "receipt": precursor.receipt,
            "start_clock_s": precursor.input_start_clock_s,
            "receipt_clock_s": precursor.input_receipt_clock_s,
        },
        {
            "ordinal": 1,
            "stage": "bridge",
            "mode": "fixed_right_hold",
            "commit_sha256": hashlib.sha256(bridge_commit.payload).hexdigest(),
            "post_sha256": hashlib.sha256(bridge_post.payload).hexdigest(),
            "input_state": "complete",
            "receipt": bridge_receipt,
            "start_clock_s": bridge_input_start,
            "receipt_clock_s": bridge_input_receipt,
        },
    ]
    expected = {
        "schema_version": 1,
        "campaign_id": CAMERA_BRIDGE_AUTHORIZATION_CAMPAIGN_ID,
        "reservation_id": authorization.sentinel_sha256,
        "reservation_completed_clock_s": reservation_completed,
        "maximum_physical_primitives": 2,
        "actual_physical_primitives": (
            2 if precursor.mode == "compass_click" else 1
        ),
        "allowed_order": allowed_order,
        "stages": expected_stages,
    }
    if value != expected:
        raise ValueError("ordered campaign receipt is not exact")
    return float(reservation_completed)


def _validate_capture_pointer_mapping(value: dict[str, Any]) -> None:
    evidence = _object(value.get("evidence"), "capture pointer evidence")
    physical = _array(evidence.get("physical_screen"), "physical pointer point")
    class_name = value.get("selected_window_class_name")
    title_sha256 = value.get("selected_window_title_sha256")
    if isinstance(title_sha256, str):
        _required_digest(title_sha256, "selected window title SHA-256")
    if (
        value.get("adapter_identity") != _EXPECTED_WINDOWS_CAMERA_ADAPTER
        or value.get("numeric_mapping_captured") is not True
        or value.get("pointer_primitive_required") is not False
        or value.get("reviewed_logical_point") != list(REVIEWED_CAMERA_WHEEL_POINT)
        or not isinstance(class_name, str)
        or not class_name.strip()
        or not isinstance(title_sha256, str)
        or evidence.get("exact_round_trip") is not True
        or evidence.get("logical_client") != list(REVIEWED_CAMERA_WHEEL_POINT)
        or evidence.get("reverse_logical_client")
        != list(REVIEWED_CAMERA_WHEEL_POINT)
        or evidence.get("root_hwnd_matches_target") is not True
        or len(physical) != 2
        or any(isinstance(item, bool) or not isinstance(item, int) for item in physical)
    ):
        raise ValueError("capture pointer mapping/ownership evidence is not exact")


def _validate_north_pointer_mapping(value: dict[str, Any]) -> None:
    policy = _object(
        value.get("receipt_backed_target_root_policy"),
        "compass north pointer root policy",
    )
    logical = _object(value.get("reviewed_logical_point"), "compass logical point")
    if (
        value.get("adapter_identity") != _EXPECTED_WINDOWS_CAMERA_ADAPTER
        or value.get("preflight")
        != {
            "client_height": EXPECTED_CLIENT_HEIGHT,
            "client_width": EXPECTED_CLIENT_WIDTH,
            "focused": True,
            "supported": True,
        }
        or logical
        != {
            "coordinate_space": "target_logical_client_pixels",
            "x": REVIEWED_COMPASS_POINT[0],
            "y": REVIEWED_COMPASS_POINT[1],
        }
        or policy.get("complete_compass_receipt") is not True
        or policy.get("discovery_identity_bound_to_control") is not True
        or policy.get("numeric_mapping_captured") is not False
        or policy.get("physical_screen_point") is not None
        or policy.get("target_root_handle_recorded") is not False
        or policy.get("target_root_rechecked_before_button_down") is not True
        or policy.get("target_root_rechecked_during_dwell_before_button_up")
        is not True
        or not isinstance(policy.get("claim"), str)
        or not str(policy.get("claim")).strip()
    ):
        raise ValueError("compass north pointer ownership evidence is not exact")


def _load_frame_evidence(
    report_path: Path,
    stage: str,
    value: dict[str, Any],
) -> tuple[Frame, CameraEvaluation]:
    artifact = _object(value.get("artifact"), f"{stage} artifact")
    expected_label = f"r2-{stage}"
    frame_id = artifact.get("frame_id")
    captured = value.get("captured_monotonic_s")
    if (
        artifact.get("label") != expected_label
        or artifact.get("width") != EXPECTED_CLIENT_WIDTH
        or artifact.get("height") != EXPECTED_CLIENT_HEIGHT
        or artifact.get("pixel_format") != PixelFormat.BGRA8888.value
        or isinstance(frame_id, bool)
        or not isinstance(frame_id, int)
        or frame_id < 0
        or isinstance(captured, bool)
        or not isinstance(captured, (int, float))
        or not math.isfinite(float(captured))
        or float(captured) < 0.0
    ):
        raise ValueError(f"{stage} artifact identity is invalid")
    digest = artifact.get("raw_sha256")
    if not isinstance(digest, str):
        raise ValueError(f"{stage} raw digest is missing")
    _required_digest(digest, f"{stage} raw SHA-256")
    files = _object(artifact.get("files"), f"{stage} artifact files")
    raw_reference = files.get("raw")
    if not isinstance(raw_reference, str) or not raw_reference.strip():
        raise ValueError(f"{stage} raw reference is invalid")
    private_root = report_path.parent.parent.resolve()
    diagnostics_root = (_REPO_ROOT / "diagnostics").resolve()
    if private_root != diagnostics_root and diagnostics_root not in private_root.parents:
        raise ValueError("capture report grandparent is outside diagnostics")
    relative = Path(raw_reference)
    report_suffix = ".camera.json"
    if not report_path.name.endswith(report_suffix):
        raise ValueError("capture report name must end in .camera.json")
    case_prefix = report_path.name.removesuffix(report_suffix)
    expected_reference = f"frames/{case_prefix}-r2-{stage}.raw"
    if raw_reference != expected_reference:
        raise ValueError(f"{stage} raw reference is not the exact fixed artifact")
    if relative.is_absolute():
        raise ValueError(f"{stage} raw reference must be relative")
    raw_path = (private_root / relative).resolve()
    if private_root not in raw_path.parents or not raw_path.is_file():
        raise ValueError(f"{stage} raw reference escaped or is missing")
    payload = raw_path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError(f"{stage} raw SHA-256 mismatch")
    frame = Frame.from_raw(
        RawFrame(payload, EXPECTED_CLIENT_WIDTH, EXPECTED_CLIENT_HEIGHT, PixelFormat.BGRA8888),
        frame_id=frame_id,
        captured_monotonic_s=float(captured),
    )
    readiness = evaluate_client_input_readiness(frame)
    if _readiness_dict(readiness) != _object(value.get("readiness"), f"{stage} readiness"):
        raise ValueError(f"{stage} readiness does not bind exact pixels")
    if not readiness.safe_to_attempt_camera_input:
        raise ValueError(f"{stage} recomputed readiness is not gameplay-safe")
    production = evaluate_varrock_east_camera(frame)
    if _production_dict(production) != _object(value.get("production"), f"{stage} production"):
        raise ValueError(f"{stage} production does not bind exact pixels")
    if not _is_fail_closed(production):
        raise ValueError(f"{stage} recomputed production is not fail closed")
    return frame, production


def _validate_closure(value: dict[str, Any], *, commit_sha: str, post_sha: str) -> None:
    binding = _object(value.get("binding"), "closure binding")
    semantics = _object(value.get("semantic_states"), "closure semantics")
    expected_semantics = {
        "ACTION_BRIDGE_RECEIPT_PROVEN": True,
        "BRIDGE_REJECTED": False,
        "PRODUCTION_SUPPORTED_ENDPOINT": False,
        "REGISTRATION_BRIDGE_OBSERVED": True,
    }
    if (
        value.get("status") != "complete"
        or value.get("completed") is not True
        or value.get("commit_sha256") != commit_sha
        or value.get("post_sha256") != post_sha
        or value.get("registration_attempted") is not True
        or value.get("registration_accepted") is not True
        or value.get("production_re_evaluated") is not True
        or value.get("production_matches_capture") is not True
        or value.get("action_transition_emitted") is not False
        or value.get("authenticated_ingestion_required") is not True
        or value.get("transition_candidate_eligible") is not False
        or any(
            value.get(name) is not None
            for name in (
                "artifact_exception",
                "registration_exception",
                "production_exception",
                "seal_exception",
            )
        )
        or semantics != expected_semantics
        or binding
        != {
            "action_id": CAMERA_BRIDGE_CAPTURE_ID,
            "action_version": CAMERA_BRIDGE_CAPTURE_VERSION,
            "objective_id": FROZEN_ENDPOINT_OBJECTIVE_ID,
            "objective_source_sha256": FROZEN_ENDPOINT_SOURCE_SHA256,
            "plan_name": camera_bridge_capture_plan().name,
        }
    ):
        raise ValueError("capture post-transition closure is not complete and exact")


def _validate_registration_binding(
    value: dict[str, Any], *, commit_sha: str, post_sha: str
) -> None:
    source = _object(value.get("source"), "registration source")
    target = _object(value.get("target"), "registration target")
    authority = _object(value.get("authority"), "registration authority")
    zones = _array(value.get("required_zones"), "registration required zones")
    expected_authority = {
        "can_accept": False,
        "can_expose_resources": False,
        "can_validate_scene": False,
        "diagnostic_registration_can_override_production": False,
    }
    if (
        value.get("accepted") is not True
        or source.get("payload_sha256") != commit_sha
        or target.get("payload_sha256") != post_sha
        or set(zones) != {"north_west", "north_east", "south_west"}
        or len(zones) != 3
        or authority != expected_authority
    ):
        raise ValueError("capture registration does not bind exact commit/post pixels")


def _require_recomputed_registration(
    commit: Frame, post: Frame, reported: dict[str, Any]
) -> None:
    recomputed = RobustRegistrationEngine().analyze(commit, post)
    if recomputed.as_dict() != reported:
        raise ValueError("sealed commit/post registration does not match exact pixels")


def _require_exact_registration(
    source: Frame,
    target: Frame,
    reported: dict[str, Any],
    *,
    context: str,
) -> RobustWorldRegistration:
    source_sha256 = hashlib.sha256(source.payload).hexdigest()
    target_sha256 = hashlib.sha256(target.payload).hexdigest()
    try:
        _validate_registration_binding(
            reported,
            commit_sha=source_sha256,
            post_sha=target_sha256,
        )
        recomputed = RobustRegistrationEngine().analyze(source, target)
        if recomputed.as_dict() != reported:
            raise ValueError("registration payload differs from recomputation")
    except (TypeError, ValueError) as error:
        raise ValueError(f"{context} registration is not exact: {error}") from error
    return recomputed


def _validate_capture_command_argv(
    provenance: dict[str, Any],
    *,
    expected_head: str,
    expected_r2_sha256: str,
    expected_r2_report_path: Path,
    capture_report_path: Path,
) -> None:
    options = _command_options(provenance, _CAPTURE_COMMAND)
    required = {
        "--analysis-report",
        "--analysis-sha256",
        "--case-prefix",
        "--expected-head",
    }
    if set(options) not in (required, {*required, "--output"}):
        raise ValueError("capture command argv does not use only the fixed options")
    case_prefix = capture_report_path.name.removesuffix(".camera.json")
    output_path = _resolve_command_path(
        options.get("--output", str(_DEFAULT_BRIDGE_OUTPUT))
    )
    if (
        not capture_report_path.name.endswith(".camera.json")
        or options["--expected-head"] != expected_head
        or options["--analysis-sha256"] != expected_r2_sha256
        or options["--case-prefix"] != case_prefix
        or _resolve_command_path(options["--analysis-report"])
        != expected_r2_report_path.resolve()
        or output_path != capture_report_path.parent.parent.resolve()
    ):
        raise ValueError("capture command argv does not bind the exact live inputs")


def _command_options(
    provenance: dict[str, Any], expected_command: str
) -> dict[str, str]:
    argv = _array(provenance.get("command_argv"), "provenance command_argv")
    if any(
        not isinstance(item, str)
        or not item
        or any(character in item for character in ("\x00", "\r", "\n"))
        for item in argv
    ):
        raise ValueError("provenance command argv contains an invalid argument")
    if argv.count(expected_command) != 1:
        raise ValueError("provenance command argv does not pin the exact subcommand")
    command_index = argv.index(expected_command)
    tail = argv[command_index + 1 :]
    expected_script = (_REPO_ROOT / "tools" / "validate_varrock_east_camera.py").resolve()
    if (
        command_index != 2
        or _resolve_command_path(str(argv[1])) != expected_script
        or len(tail) % 2 != 0
    ):
        raise ValueError("provenance command argv has an invalid fixed-option shape")
    options: dict[str, str] = {}
    for index in range(0, len(tail), 2):
        name = tail[index]
        value = tail[index + 1]
        assert isinstance(name, str) and isinstance(value, str)
        if not name.startswith("--") or name in options:
            raise ValueError("provenance command argv has a duplicate/invalid option")
        options[name] = value
    return options


def _resolve_command_path(value: str) -> Path:
    return (Path(value) if Path(value).is_absolute() else _REPO_ROOT / value).resolve()


def _require_provenance(
    value: dict[str, Any],
    *,
    expected_head: str,
    plan_id: str,
    plan_version: str,
    expected_command: str | None = None,
) -> None:
    expected = {
        "detector_id": _EXPECTED_DETECTOR_ID,
        "detector_version": _EXPECTED_DETECTOR_VERSION,
        "git_head_sha": expected_head,
        "plan_id": plan_id,
        "plan_version": plan_version,
        "profile_id": _EXPECTED_PROFILE_ID,
        "tracked_worktree_clean": True,
    }
    if any(value.get(key) != item for key, item in expected.items()):
        raise ValueError("report provenance does not bind the exact clean head")
    if expected_command is not None:
        _command_options(value, expected_command)


def _load_report(
    path: Path, expected_sha256: str, *, diagnostics_only: bool = False
) -> tuple[Path, dict[str, Any]]:
    _required_digest(expected_sha256, "report SHA-256")
    resolved = _resolve_repo_report(path)
    diagnostics_root = (_REPO_ROOT / "diagnostics").resolve()
    if diagnostics_only and diagnostics_root not in resolved.parents:
        raise ValueError("capture report must remain inside diagnostics")
    payload = resolved.read_bytes()
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError("report SHA-256 mismatch")
    sidecar = resolved.with_name(f"{resolved.name}.sha256")
    if sidecar.read_bytes() != f"{expected_sha256}\n".encode("ascii"):
        raise ValueError("report SHA-256 sidecar mismatch")
    parsed = json.loads(
        payload,
        object_pairs_hook=_reject_duplicate_keys,
        parse_float=_finite_float,
        parse_constant=_reject_nonstandard_number,
    )
    return resolved, _object(parsed, "report")


def _resolve_repo_report(path: Path) -> Path:
    resolved = (path if path.is_absolute() else _REPO_ROOT / path).resolve()
    root = _REPO_ROOT.resolve()
    if root not in resolved.parents or not resolved.is_file():
        raise ValueError("report must be an existing repository-local file")
    return resolved


def _resolve_output(path: Path) -> Path:
    resolved = (path if path.is_absolute() else _REPO_ROOT / path).resolve()
    diagnostics = (_REPO_ROOT / "diagnostics").resolve()
    if diagnostics not in resolved.parents:
        raise ValueError("output report must remain inside diagnostics")
    return resolved


def _retract_published_report(report_path: Path, digest_path: Path) -> None:
    """Attempt both removals so one failure cannot strand the other artifact."""

    errors: list[str] = []
    for path in (digest_path, report_path):
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            errors.append(f"{path}: {error}")
    if errors:
        raise OSError("; ".join(errors))


def _report_evidence(
    verification: BridgePostVerification,
    *,
    frozen: FrozenInputs,
    before_head: str,
    after_head: str,
    completion_seal_sha256: str,
) -> dict[str, object]:
    return {
        "authority": {
            "can_accept": False,
            "can_authorize_camera_input": False,
            "can_expose_resources": False,
            "can_validate_scene": False,
            "diagnostic_registration_can_override_production": False,
            "live_camera_input_performed": False,
            "second_live_action_authorized": False,
        },
        "bindings": {
            "capture_report_sha256": verification.capture_report_sha256,
            "completion_seal_sha256": completion_seal_sha256,
            "r1_report_sha256": frozen.r1_report_sha256,
            "r2_report_sha256": frozen.r2_report_sha256,
        },
        "git": {
            "before": {"head_sha": before_head, "worktree_clean": True},
            "after": {"head_sha": after_head, "worktree_clean": True},
            "exact_head_stable": before_head == after_head,
        },
        "result": {
            "conclusion": (
                "bridge evidence available"
                if verification.verified
                else "insufficient graph evidence; stop after one sample"
            ),
            "second_live_action_authorized": False,
            "stop_after_single_sample": True,
            "verified": verification.verified,
        },
        "verification": verification.as_dict(),
    }


def _readiness_dict(value: ClientInputReadiness) -> dict[str, object]:
    return {
        "anchors": [
            {
                "anchor_id": item.policy.anchor_id,
                "dark_fraction": item.dark_fraction,
                "edge_density": item.edge_density,
                "luma_stddev": item.luma_stddev,
                "matched": item.matched,
                "region": list(item.policy.region),
            }
            for item in value.anchors
        ],
        "can_accept": value.can_accept,
        "can_expose_resources": value.can_expose_resources,
        "can_validate_scene": value.can_validate_scene,
        "detail": value.detail,
        "evaluator_id": value.evaluator_id,
        "evaluator_version": value.evaluator_version,
        "reason": value.reason.value,
        "safe_to_attempt_camera_input": value.safe_to_attempt_camera_input,
    }


def _north_readiness_dict(value: ClientInputReadiness) -> dict[str, object]:
    """Serialize readiness exactly as the compass-north producer does."""

    return {
        "evaluator_id": value.evaluator_id,
        "evaluator_version": value.evaluator_version,
        "reason": value.reason.value,
        "detail": value.detail,
        "safe_to_attempt_camera_input": value.safe_to_attempt_camera_input,
        "can_accept": value.can_accept,
        "can_validate_scene": value.can_validate_scene,
        "can_expose_resources": value.can_expose_resources,
        "anchors": [
            {
                "anchor_id": item.policy.anchor_id,
                "region": list(item.policy.region),
                "thresholds": {
                    "minimum_luma_stddev": item.policy.minimum_luma_stddev,
                    "minimum_edge_density": item.policy.minimum_edge_density,
                    "maximum_dark_fraction": item.policy.maximum_dark_fraction,
                },
                "metrics": {
                    "luma_stddev": item.luma_stddev,
                    "edge_density": item.edge_density,
                    "dark_fraction": item.dark_fraction,
                },
                "matched": item.matched,
            }
            for item in value.anchors
        ],
    }


def _production_dict(value: CameraEvaluation) -> dict[str, object]:
    return {
        "definitive_target_ids": list(value.definitive_target_ids),
        "detector_id": value.detector_id,
        "detector_version": value.detector_version,
        "frame_geometry_supported": value.frame_geometry_supported,
        "landmarks": [
            {
                "distance": item.distance,
                "landmark_id": item.landmark_id,
                "matched": item.matched,
                "threshold": item.threshold,
                "zone": item.zone.value,
            }
            for item in value.landmarks
        ],
        "matched_landmark_count": value.matched_landmark_count,
        "matched_zones": [item.value for item in value.matched_zones],
        "passed": value.passed,
        "profile_frame_height": value.profile_frame_height,
        "profile_frame_width": value.profile_frame_width,
        "profile_id": value.profile_id,
        "profile_pixel_format": value.profile_pixel_format.value,
        "profile_schema_version": value.profile_schema_version,
        "required_landmark_count": value.required_landmark_count,
        "required_landmark_matches": value.required_landmark_matches,
        "required_matched_zones": value.required_matched_zones,
        "resource_states": [
            {
                "confidence": item.confidence,
                "resource_id": item.resource_id,
                "state": item.state.value,
            }
            for item in value.resource_states
        ],
        "scene_reason": value.scene_reason,
        "scene_validated": value.scene_validated,
    }


def _is_fail_closed(value: CameraEvaluation) -> bool:
    return (
        not value.passed
        and not value.scene_validated
        and value.definitive_target_ids == ()
        and bool(value.resource_states)
        and all(
            item.state is ResourceVisualState.UNCERTAIN
            for item in value.resource_states
        )
    )


def _evaluation_dict(value: CameraEvaluation) -> dict[str, object]:
    """Serialize the sealed post evaluator in the live report's nested form."""

    return {
        "detector_id": value.detector_id,
        "detector_version": value.detector_version,
        "profile_id": value.profile_id,
        "profile_schema_version": value.profile_schema_version,
        "profile_geometry": {
            "width": value.profile_frame_width,
            "height": value.profile_frame_height,
            "pixel_format": value.profile_pixel_format.value,
        },
        "frame_geometry_supported": value.frame_geometry_supported,
        "scene": {
            "validated": value.scene_validated,
            "reason": value.scene_reason,
            "matched_landmarks": value.matched_landmark_count,
            "configured_landmarks": value.required_landmark_count,
            "required_landmark_matches": value.required_landmark_matches,
            "matched_zones": [item.value for item in value.matched_zones],
            "required_zones": value.required_matched_zones,
            "landmarks": [
                {
                    "landmark_id": item.landmark_id,
                    "distance": item.distance,
                    "threshold": item.threshold,
                    "matched": item.matched,
                    "zone": item.zone.value,
                }
                for item in value.landmarks
            ],
        },
        "resources": [
            {
                "resource_id": item.resource_id,
                "state": item.state.value,
                "confidence": item.confidence,
                "definitive": item.definitive,
            }
            for item in value.resource_states
        ],
        "definitive_target_ids": list(value.definitive_target_ids),
        "passed": value.passed,
    }


def _arm_guard_dict(value: CameraArmGuardResult) -> dict[str, object]:
    return {
        "arm_frame": {
            "captured_monotonic_s": value.arm_captured_monotonic_s,
            "frame_id": value.arm_frame_id,
            "raw_sha256": value.arm_payload_sha256,
        },
        "can_accept": value.can_accept,
        "can_expose_resources": value.can_expose_resources,
        "can_validate_scene": value.can_validate_scene,
        "decision_frame": {
            "captured_monotonic_s": value.decision_captured_monotonic_s,
            "frame_id": value.decision_frame_id,
            "raw_sha256": value.decision_payload_sha256,
        },
        "detail": value.detail,
        "disposition": value.disposition.value,
        "evaluated_zones": [item.value for item in value.evaluated_zones],
        "guard_id": value.guard_id,
        "guard_version": value.guard_version,
        "reason": value.reason.value,
        "stable_landmark_count": value.stable_landmark_count,
        "stable_zones": [item.value for item in value.stable_zones],
    }


def _required_head(value: str) -> str:
    if _FULL_HEAD.fullmatch(value) is None:
        raise ValueError("--expected-head must be a lowercase 40-character SHA")
    return value


def _display_repo_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(_REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _required_digest(value: str, name: str) -> None:
    if _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def _object(value: object, context: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{context} must be a JSON object")
    return value


def _array(value: object, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be a JSON array")
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"non-finite JSON number: {value}")
    return result


def _reject_nonstandard_number(value: str) -> object:
    raise ValueError(f"non-standard JSON number: {value}")


def _git_state(repo_root: Path) -> tuple[str, bool]:
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
    candidates = [Path("/usr/bin/git"), Path("/usr/local/bin/git")]
    if os.name == "nt":
        drive = Path(sys.executable).drive or "C:"
        candidates.insert(0, Path(drive + "/Program Files/Git/cmd/git.exe"))
    executable = next(
        (str(path.resolve()) for path in candidates if path.is_file()),
        None,
    )
    if executable is None:
        raise RuntimeError("cannot locate an approved absolute Git executable")
    prefix = (
        executable,
        f"--git-dir={repository_worktree_git_dir(repo_root)}",
        f"--work-tree={repo_root.resolve()}",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-c",
        "core.autocrlf=input",
        "-c",
        "core.excludesFile=",
    )
    head = subprocess.run(
        (*prefix, "rev-parse", "HEAD"),
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    ).stdout.strip()
    status = subprocess.run(
        (*prefix, "status", "--porcelain=v1", "--untracked-files=all"),
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    ).stdout
    return head, not status.strip()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
