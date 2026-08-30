"""One fixed, development-only camera bridge capture for Issue #31 R2.

The runner can execute exactly one reviewed right-arrow hold.  The action is
compile-time policy, never an observation-derived controller decision.  Fresh
readiness, unchanged production fail-closed behavior, three structural guard
links, platform preflight, independent age evidence, and complete input
receipts are required before a post-action frame can be sealed as report-only
evidence for later authenticated offline ingestion.

This module deliberately does not import robust registration.  It cannot
accept a scene, expose resources, authorize a diagnostic controller, or turn
capture/input success into production perception success.
"""

from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Final

from ..capture import Frame
from ..perception.resource import ResourceVisualState
from .camera_arm_guard import (
    CameraArmGuardDisposition,
    CameraArmGuardResult,
    evaluate_camera_arm_guard,
)
from .camera_evaluation import CameraEvaluation, evaluate_varrock_east_camera
from .camera_plan import (
    EXPECTED_CLIENT_HEIGHT,
    EXPECTED_CLIENT_WIDTH,
    CameraControl,
    CameraHoldKey,
    CameraInputNotAttemptedError,
    CameraInputReceipt,
    CameraKeyHold,
    CameraPlan,
    CameraPlanReceipt,
    CameraPlanRunner,
    CameraPreflightReceipt,
    Sleeper,
)
from .camera_servo import (
    MAXIMUM_ARM_TO_INPUT_AGE_SECONDS,
    CameraServoArmAgeEvidence,
    CameraServoArmAgeStatus,
    CameraServoExceptionEvidence,
    CameraServoFrameEvidence,
)
from .camera_session import (
    CameraArtifactRecorder,
    CameraFrameArtifact,
    CameraFrameSource,
    record_frame_digest,
)
from .client_readiness import ClientInputReadiness, evaluate_client_input_readiness

__all__ = [
    "CAMERA_BRIDGE_CAPTURE_HOLD_SECONDS",
    "CAMERA_BRIDGE_CAPTURE_ID",
    "CAMERA_BRIDGE_CAPTURE_MAXIMUM_PHYSICAL_PRIMITIVES",
    "CAMERA_BRIDGE_CAPTURE_SETTLE_SECONDS",
    "CAMERA_BRIDGE_CAPTURE_VERSION",
    "CameraBridgeCaptureInputState",
    "CameraBridgePostTransitionClosure",
    "CameraBridgePostTransitionStatus",
    "CameraBridgeCaptureResult",
    "CameraBridgeCaptureTerminalReason",
    "camera_bridge_capture_plan",
    "run_fixed_camera_bridge_capture",
]

CAMERA_BRIDGE_CAPTURE_ID: Final[str] = "issue31-fixed-camera-bridge-capture-r2"
CAMERA_BRIDGE_CAPTURE_VERSION: Final[str] = "1.1.0"
CAMERA_BRIDGE_CAPTURE_HOLD_SECONDS: Final[float] = 0.043
CAMERA_BRIDGE_CAPTURE_SETTLE_SECONDS: Final[float] = 1.0
CAMERA_BRIDGE_CAPTURE_MAXIMUM_PHYSICAL_PRIMITIVES: Final[int] = 1

_FIXED_PLAN: Final[CameraPlan] = CameraPlan(
    CAMERA_BRIDGE_CAPTURE_ID,
    (
        CameraKeyHold(
            CameraHoldKey.RIGHT,
            CAMERA_BRIDGE_CAPTURE_HOLD_SECONDS,
        ),
    ),
)

type CameraBridgeInputGuard = Callable[
    [CameraServoFrameEvidence, CameraServoFrameEvidence, CameraServoFrameEvidence],
    None,
]


class CameraBridgeCaptureInputState(StrEnum):
    """Honest physical side-effect state for the frozen primitive."""

    NONE = "none"
    PARTIAL_OR_UNKNOWN = "partial_or_unknown"
    COMPLETE = "complete"


class CameraBridgePostTransitionStatus(StrEnum):
    """Same-transaction diagnostic closure of an exact commit/post pair."""

    NOT_REQUIRED = "not_required"
    PHYSICAL_CAPTURE_INCOMPLETE = "physical_capture_incomplete"
    ARTIFACT_ERROR = "artifact_error"
    NO_DISTINCT_ENDPOINT = "no_distinct_endpoint"
    POST_READINESS_REJECTED = "post_readiness_rejected"
    REGISTRATION_REJECTED = "registration_rejected"
    REGISTRATION_EXCEPTION = "registration_exception"
    PRODUCTION_EXCEPTION = "production_exception"
    PRODUCTION_REJECTED = "production_rejected"
    SEAL_REVALIDATION_ERROR = "seal_revalidation_error"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class CameraBridgePostTransitionClosure:
    """No-authority evidence that one exact post transition was fully closed.

    Robust-registration details remain owned by the validation composition
    layer.  This type records only the fail-closed transaction facts needed to
    prevent a physical receipt from becoming a graph transition before the
    exact commit/post pixels have been registered and production has then been
    re-evaluated on the exact post payload.
    """

    status: CameraBridgePostTransitionStatus
    detail: str
    objective_id: str
    objective_source_sha256: str
    commit_sha256: str | None = None
    post_sha256: str | None = None
    action_bridge_receipt_proven: bool = False
    registration_attempted: bool = False
    registration_accepted: bool = False
    registration_bridge_observed: bool = False
    production_re_evaluated: bool = False
    production_matches_capture: bool = False
    production_supported_endpoint: bool = False
    bridge_rejected: bool = False
    artifact_exception: CameraServoExceptionEvidence | None = None
    registration_exception: CameraServoExceptionEvidence | None = None
    production_exception: CameraServoExceptionEvidence | None = None
    seal_exception: CameraServoExceptionEvidence | None = None
    can_accept: bool = field(default=False, init=False)
    can_validate_scene: bool = field(default=False, init=False)
    can_expose_resources: bool = field(default=False, init=False)
    can_authorize_camera_input: bool = field(default=False, init=False)
    diagnostic_registration_can_override_production: bool = field(
        default=False,
        init=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.status, CameraBridgePostTransitionStatus):
            raise TypeError("post-transition status is invalid")
        if not isinstance(self.detail, str) or not self.detail.strip():
            raise ValueError("post-transition detail must be a non-empty string")
        if (
            not isinstance(self.objective_id, str)
            or not self.objective_id
            or self.objective_id != self.objective_id.strip()
        ):
            raise ValueError("post-transition objective_id must be non-empty")
        _require_digest(
            self.objective_source_sha256,
            "post-transition objective_source_sha256",
        )
        for value, name in (
            (self.action_bridge_receipt_proven, "action_bridge_receipt_proven"),
            (self.registration_attempted, "registration_attempted"),
            (self.registration_accepted, "registration_accepted"),
            (self.registration_bridge_observed, "registration_bridge_observed"),
            (self.production_re_evaluated, "production_re_evaluated"),
            (self.production_matches_capture, "production_matches_capture"),
            (self.production_supported_endpoint, "production_supported_endpoint"),
            (self.bridge_rejected, "bridge_rejected"),
        ):
            if not isinstance(value, bool):
                raise TypeError(f"{name} must be a boolean")
        if (self.commit_sha256 is None) is not (self.post_sha256 is None):
            raise ValueError("commit/post closure digests must be present together")
        if self.commit_sha256 is not None:
            _require_digest(self.commit_sha256, "commit_sha256")
            assert self.post_sha256 is not None
            _require_digest(self.post_sha256, "post_sha256")
        if self.registration_attempted and self.commit_sha256 is None:
            raise ValueError("registration attempt requires exact commit/post digests")
        if self.registration_accepted and not self.registration_attempted:
            raise ValueError("accepted registration requires an attempted registration")
        if self.registration_bridge_observed != self.registration_accepted:
            raise ValueError("registration bridge observation must equal acceptance")
        if self.production_re_evaluated and not self.registration_attempted:
            raise ValueError("production re-evaluation must follow registration evaluation")
        if self.production_matches_capture and not self.production_re_evaluated:
            raise ValueError("production match requires production re-evaluation")
        if self.production_supported_endpoint and not self.production_re_evaluated:
            raise ValueError("supported endpoint requires production re-evaluation")
        complete = self.status is CameraBridgePostTransitionStatus.COMPLETE
        if complete != (
            self.commit_sha256 is not None
            and self.commit_sha256 != self.post_sha256
            and self.action_bridge_receipt_proven
            and self.registration_attempted
            and self.registration_accepted
            and self.registration_bridge_observed
            and self.production_re_evaluated
            and self.production_matches_capture
            and not self.bridge_rejected
            and self.artifact_exception is None
            and self.registration_exception is None
            and self.production_exception is None
            and self.seal_exception is None
        ):
            raise ValueError("complete post-transition status requires the full closure chain")
        if self.status is CameraBridgePostTransitionStatus.NOT_REQUIRED and any(
            (
                self.commit_sha256 is not None,
                self.action_bridge_receipt_proven,
                self.registration_attempted,
                self.registration_accepted,
                self.registration_bridge_observed,
                self.production_re_evaluated,
                self.production_matches_capture,
                self.production_supported_endpoint,
                self.bridge_rejected,
                self.artifact_exception is not None,
                self.registration_exception is not None,
                self.production_exception is not None,
                self.seal_exception is not None,
            )
        ):
            raise ValueError("not-required closure cannot retain transition evidence")
        if self.status not in (
            CameraBridgePostTransitionStatus.NOT_REQUIRED,
            CameraBridgePostTransitionStatus.COMPLETE,
        ) and not self.bridge_rejected:
            raise ValueError("every incomplete post-transition closure must reject bridge")
        if (
            self.status is CameraBridgePostTransitionStatus.REGISTRATION_EXCEPTION
            and (
                self.registration_exception is None
                or self.registration_accepted
            )
        ):
            raise ValueError(
                "registration-exception status must bind its exception"
            )
        if (
            self.status is CameraBridgePostTransitionStatus.PRODUCTION_EXCEPTION
        ) != (self.production_exception is not None):
            raise ValueError(
                "production-exception status must exactly bind its exception"
            )
        if (
            self.status is CameraBridgePostTransitionStatus.ARTIFACT_ERROR
        ) != (self.artifact_exception is not None):
            raise ValueError("artifact-error status must exactly bind its exception")
        if (
            self.status
            is CameraBridgePostTransitionStatus.SEAL_REVALIDATION_ERROR
            and self.seal_exception is None
        ):
            raise ValueError(
                "seal-revalidation-error status must bind its seal exception"
            )
        if self.seal_exception is not None and self.status not in (
            CameraBridgePostTransitionStatus.ARTIFACT_ERROR,
            CameraBridgePostTransitionStatus.SEAL_REVALIDATION_ERROR,
        ):
            raise ValueError(
                "seal exceptions require artifact or seal-revalidation status"
            )
        if self.status is CameraBridgePostTransitionStatus.REGISTRATION_REJECTED and (
            self.registration_exception is None
            or not self.registration_attempted
            or self.registration_accepted
            or not self.production_re_evaluated
        ):
            raise ValueError(
                "registration-rejected status requires rejection plus final production"
            )
        if self.status is CameraBridgePostTransitionStatus.NO_DISTINCT_ENDPOINT and (
            self.commit_sha256 is None
            or self.commit_sha256 != self.post_sha256
            or not self.registration_accepted
            or not self.production_re_evaluated
        ):
            raise ValueError(
                "no-distinct-endpoint status requires measured identical endpoints"
            )
        if self.status is CameraBridgePostTransitionStatus.PRODUCTION_REJECTED and (
            not self.registration_accepted
            or not self.production_re_evaluated
            or self.production_supported_endpoint
        ):
            raise ValueError(
                "production-rejected status requires registered unsupported evidence"
            )

    @property
    def completed(self) -> bool:
        return self.status is CameraBridgePostTransitionStatus.COMPLETE

    def as_dict(self) -> dict[str, object]:
        return {
            "authority": {
                "can_accept": self.can_accept,
                "can_authorize_camera_input": self.can_authorize_camera_input,
                "can_expose_resources": self.can_expose_resources,
                "can_validate_scene": self.can_validate_scene,
                "diagnostic_registration_can_override_production": (
                    self.diagnostic_registration_can_override_production
                ),
            },
            "binding": {
                "action_id": CAMERA_BRIDGE_CAPTURE_ID,
                "action_version": CAMERA_BRIDGE_CAPTURE_VERSION,
                "objective_id": self.objective_id,
                "objective_source_sha256": self.objective_source_sha256,
                "plan_name": _FIXED_PLAN.name,
            },
            "artifact_exception": (
                None
                if self.artifact_exception is None
                else {
                    "message": self.artifact_exception.detail,
                    "type": self.artifact_exception.exception_type,
                }
            ),
            "commit_sha256": self.commit_sha256,
            "completed": self.completed,
            "detail": self.detail,
            "production_exception": (
                None
                if self.production_exception is None
                else {
                    "message": self.production_exception.detail,
                    "type": self.production_exception.exception_type,
                }
            ),
            "registration_exception": (
                None
                if self.registration_exception is None
                else {
                    "message": self.registration_exception.detail,
                    "type": self.registration_exception.exception_type,
                }
            ),
            "seal_exception": (
                None
                if self.seal_exception is None
                else {
                    "message": self.seal_exception.detail,
                    "type": self.seal_exception.exception_type,
                }
            ),
            "post_sha256": self.post_sha256,
            "production_matches_capture": self.production_matches_capture,
            "production_re_evaluated": self.production_re_evaluated,
            "semantic_states": {
                "ACTION_BRIDGE_RECEIPT_PROVEN": (
                    self.action_bridge_receipt_proven
                ),
                "BRIDGE_REJECTED": self.bridge_rejected,
                "PRODUCTION_SUPPORTED_ENDPOINT": (
                    self.production_supported_endpoint
                ),
                "REGISTRATION_BRIDGE_OBSERVED": (
                    self.registration_bridge_observed
                ),
            },
            "registration_accepted": self.registration_accepted,
            "registration_attempted": self.registration_attempted,
            "status": self.status.value,
            "action_transition_emitted": False,
            "authenticated_ingestion_required": True,
            "same_transaction_closure_completed": self.completed,
            "transition_candidate_eligible": False,
        }


class CameraBridgeCaptureTerminalReason(StrEnum):
    """Stable terminal outcome for one R2 bridge-capture invocation."""

    CAPTURE_COMPLETE = "capture_complete"
    POST_CAPTURE_PENDING_CLOSURE = "post_capture_pending_closure"
    PRODUCTION_PASS = "production_pass"
    READINESS_LOST = "readiness_lost"
    PRODUCTION_REJECTION_NOT_FAIL_CLOSED = "production_rejection_not_fail_closed"
    PREFLIGHT_REJECTED = "preflight_rejected"
    NON_FRESH_OBSERVATION = "non_fresh_observation"
    WORLD_CHANGED = "world_changed"
    ARM_FRESHNESS_EXPIRED = "arm_freshness_expired"
    CLOCK_ERROR = "clock_error"
    OBSERVATION_EXCEPTION = "observation_exception"
    INPUT_EXCEPTION = "input_exception"
    SETTLE_EXCEPTION = "settle_exception"


@dataclass(frozen=True, slots=True)
class _CameraBridgePendingPostEvidence:
    """Exact post artifact/readiness before the sole production evaluation."""

    artifact: CameraFrameArtifact
    captured_monotonic_s: float
    readiness: ClientInputReadiness

    def __post_init__(self) -> None:
        if (
            isinstance(self.captured_monotonic_s, bool)
            or not isinstance(self.captured_monotonic_s, (int, float))
            or not math.isfinite(float(self.captured_monotonic_s))
            or float(self.captured_monotonic_s) < 0.0
        ):
            raise ValueError("pending post captured_monotonic_s is invalid")

    @property
    def production(self) -> None:
        """Pending post evidence intentionally has no pre-registration production."""

        return None


@dataclass(frozen=True, slots=True)
class CameraBridgeCaptureResult:
    """Immutable evidence for zero input or one frozen bridge primitive."""

    plan: CameraPlan
    decision: CameraServoFrameEvidence | None
    arm: CameraServoFrameEvidence | None
    arm_guard: CameraArmGuardResult | None
    preflight: CameraPreflightReceipt | None
    commit: CameraServoFrameEvidence | None
    commit_guard: CameraArmGuardResult | None
    decision_commit_guard: CameraArmGuardResult | None
    arm_age: CameraServoArmAgeEvidence | None
    receipt: CameraPlanReceipt | None
    post: CameraServoFrameEvidence | _CameraBridgePendingPostEvidence | None
    terminal_reason: CameraBridgeCaptureTerminalReason
    detail: str
    input_state: CameraBridgeCaptureInputState = CameraBridgeCaptureInputState.NONE
    input_start_clock_s: float | None = None
    input_receipt_clock_s: float | None = None
    input_delivery_duration_s: float | None = None
    exception: CameraServoExceptionEvidence | None = None
    can_accept: bool = field(default=False, init=False)
    can_validate_scene: bool = field(default=False, init=False)
    can_expose_resources: bool = field(default=False, init=False)
    can_authorize_camera_input: bool = field(default=False, init=False)
    diagnostic_registration_can_override_production: bool = field(
        default=False,
        init=False,
    )

    def __post_init__(self) -> None:
        if self.plan is not _FIXED_PLAN:
            raise ValueError("bridge capture must bind the exact frozen plan object")
        if not isinstance(self.terminal_reason, CameraBridgeCaptureTerminalReason):
            raise ValueError("bridge terminal reason is invalid")
        if not isinstance(self.detail, str) or not self.detail.strip():
            raise ValueError("bridge result detail must be a non-empty string")
        if not isinstance(self.input_state, CameraBridgeCaptureInputState):
            raise ValueError("bridge input state is invalid")
        _validate_timing(self)
        _validate_receipt_state(self)
        _validate_frame_chronology(self)
        _validate_attempted_input_seam(self)
        _validate_terminal_result(self)

    @property
    def input_attempted(self) -> bool:
        return self.input_state is not CameraBridgeCaptureInputState.NONE

    @property
    def input_completed(self) -> bool:
        return self.input_state is CameraBridgeCaptureInputState.COMPLETE

    @property
    def protocol_completed(self) -> bool:
        return self.input_completed and self.terminal_reason in (
            CameraBridgeCaptureTerminalReason.CAPTURE_COMPLETE,
            CameraBridgeCaptureTerminalReason.PRODUCTION_PASS,
        )

    @property
    def post_production_passed(self) -> bool:
        return (
            isinstance(self.post, CameraServoFrameEvidence)
            and self.post.production is not None
            and self.post.production.passed
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "authority": {
                "can_accept": self.can_accept,
                "can_authorize_camera_input": self.can_authorize_camera_input,
                "can_expose_resources": self.can_expose_resources,
                "can_validate_scene": self.can_validate_scene,
                "diagnostic_registration_can_override_production": (
                    self.diagnostic_registration_can_override_production
                ),
                "input_receipt_is_scene_acceptance": False,
                "production_remains_sole_scene_authority": True,
            },
            "bridge_capture": {
                "id": CAMERA_BRIDGE_CAPTURE_ID,
                "version": CAMERA_BRIDGE_CAPTURE_VERSION,
                "protocol_completed": self.protocol_completed,
                "post_production_passed": self.post_production_passed,
            },
            "detail": self.detail,
            "exception": (
                None
                if self.exception is None
                else {
                    "type": self.exception.exception_type,
                    "message": self.exception.detail,
                }
            ),
            "fixed_policy": {
                "caller_selectable_axis": False,
                "caller_selectable_coordinate": False,
                "caller_selectable_direction": False,
                "caller_selectable_evaluator": False,
                "caller_selectable_magnitude": False,
                "caller_selectable_plan": False,
                "hold_seconds": CAMERA_BRIDGE_CAPTURE_HOLD_SECONDS,
                "key": CameraHoldKey.RIGHT.value,
                "maximum_physical_primitives": (
                    CAMERA_BRIDGE_CAPTURE_MAXIMUM_PHYSICAL_PRIMITIVES
                ),
                "post_action_settle_seconds": CAMERA_BRIDGE_CAPTURE_SETTLE_SECONDS,
            },
            "frames": {
                "arm": _frame_evidence_dict(self.arm),
                "commit": _frame_evidence_dict(self.commit),
                "decision": _frame_evidence_dict(self.decision),
                "post": _frame_evidence_dict(self.post),
            },
            "guards": {
                "arm_to_commit": _arm_guard_dict(self.commit_guard),
                "decision_to_arm": _arm_guard_dict(self.arm_guard),
                "decision_to_commit": _arm_guard_dict(
                    self.decision_commit_guard
                ),
            },
            "input": {
                "attempted": self.input_attempted,
                "completed": self.input_completed,
                "delivery_duration_s": self.input_delivery_duration_s,
                "receipt_clock_s": self.input_receipt_clock_s,
                "start_clock_s": self.input_start_clock_s,
                "state": self.input_state.value,
            },
            "plan": _plan_dict(self.plan),
            "preflight": _preflight_dict(self.preflight),
            "receipt": _receipt_dict(self.receipt),
            "arm_age": _arm_age_dict(self.arm_age),
            "terminal_reason": self.terminal_reason.value,
            # Physical capture completion alone is deliberately insufficient.
            # The composition layer must robust-register the exact commit/post
            # pair and then re-evaluate production before a transition exists.
            "transition_candidate_eligible": False,
        }


def camera_bridge_capture_plan() -> CameraPlan:
    """Return the immutable, compile-time R2 plan."""

    return _FIXED_PLAN


def run_fixed_camera_bridge_capture(
    source: CameraFrameSource,
    control: CameraControl,
    *,
    sleeper: Sleeper,
    recorder: CameraArtifactRecorder = record_frame_digest,
    clock: Callable[[], float] = time.monotonic,
    pre_input_guard: CameraBridgeInputGuard | None = None,
    final_input_guard: CameraBridgeInputGuard | None = None,
) -> CameraBridgeCaptureResult:
    """Capture one fixed receipt-backed R2 bridge sample, or fail closed."""

    plan = _FIXED_PLAN
    try:
        decision_frame, decision = _capture_evidence(
            source,
            recorder,
            "r2-decision",
        )
    except Exception as error:
        return _result(
            plan=plan,
            terminal_reason=CameraBridgeCaptureTerminalReason.OBSERVATION_EXCEPTION,
            detail="Decision capture, recording, or evaluation failed closed.",
            error=error,
        )
    terminal = _frame_terminal(decision)
    if terminal is not None:
        return _result(
            plan=plan,
            decision=decision,
            terminal_reason=terminal[0],
            detail=f"Decision veto: {terminal[1]}",
        )

    try:
        arm_frame = source.capture()
        arm_origin_clock_s = _read_clock(clock)
        arm = _evaluate_captured_frame(arm_frame, recorder, "r2-arm")
    except Exception as error:
        return _result(
            plan=plan,
            decision=decision,
            terminal_reason=(
                CameraBridgeCaptureTerminalReason.CLOCK_ERROR
                if isinstance(error, _ClockError)
                else CameraBridgeCaptureTerminalReason.OBSERVATION_EXCEPTION
            ),
            detail="Fresh arm capture, recording, evaluation, or clock failed closed.",
            error=error,
        )
    if not _strictly_newer(decision_frame, arm_frame):
        return _result(
            plan=plan,
            decision=decision,
            arm=arm,
            terminal_reason=CameraBridgeCaptureTerminalReason.NON_FRESH_OBSERVATION,
            detail="The arm frame was not strictly newer than decision.",
        )
    terminal = _frame_terminal(arm)
    if terminal is not None:
        return _result(
            plan=plan,
            decision=decision,
            arm=arm,
            terminal_reason=terminal[0],
            detail=f"Arm veto: {terminal[1]}",
        )
    try:
        arm_guard = evaluate_camera_arm_guard(decision_frame, arm_frame)
    except Exception as error:
        return _result(
            plan=plan,
            decision=decision,
            arm=arm,
            terminal_reason=CameraBridgeCaptureTerminalReason.OBSERVATION_EXCEPTION,
            detail="Decision-to-arm structural guard failed closed.",
            error=error,
        )
    if arm_guard.disposition is not CameraArmGuardDisposition.RETAIN:
        return _result(
            plan=plan,
            decision=decision,
            arm=arm,
            arm_guard=arm_guard,
            terminal_reason=CameraBridgeCaptureTerminalReason.WORLD_CHANGED,
            detail="World structure changed before platform preflight.",
        )

    try:
        preflight = control.preflight()
    except Exception as error:
        return _result(
            plan=plan,
            decision=decision,
            arm=arm,
            arm_guard=arm_guard,
            terminal_reason=CameraBridgeCaptureTerminalReason.INPUT_EXCEPTION,
            detail="No-input platform preflight failed closed.",
            error=error,
        )
    if not preflight.supported:
        return _result(
            plan=plan,
            decision=decision,
            arm=arm,
            arm_guard=arm_guard,
            preflight=preflight,
            terminal_reason=CameraBridgeCaptureTerminalReason.PREFLIGHT_REJECTED,
            detail=(
                "Platform preflight did not prove focused exact "
                f"{EXPECTED_CLIENT_WIDTH}x{EXPECTED_CLIENT_HEIGHT} geometry."
            ),
        )

    try:
        commit_frame, commit = _capture_evidence(
            source,
            recorder,
            "r2-commit",
        )
    except Exception as error:
        return _result(
            plan=plan,
            decision=decision,
            arm=arm,
            arm_guard=arm_guard,
            preflight=preflight,
            terminal_reason=CameraBridgeCaptureTerminalReason.OBSERVATION_EXCEPTION,
            detail="Final commit capture, recording, or evaluation failed closed.",
            error=error,
        )
    if not _strictly_newer(arm_frame, commit_frame):
        return _result(
            plan=plan,
            decision=decision,
            arm=arm,
            arm_guard=arm_guard,
            preflight=preflight,
            commit=commit,
            terminal_reason=CameraBridgeCaptureTerminalReason.NON_FRESH_OBSERVATION,
            detail="The final commit was not strictly newer than arm.",
        )
    terminal = _frame_terminal(commit)
    if terminal is not None:
        return _result(
            plan=plan,
            decision=decision,
            arm=arm,
            arm_guard=arm_guard,
            preflight=preflight,
            commit=commit,
            terminal_reason=terminal[0],
            detail=f"Commit veto: {terminal[1]}",
        )
    try:
        commit_guard = evaluate_camera_arm_guard(arm_frame, commit_frame)
        decision_commit_guard = evaluate_camera_arm_guard(
            decision_frame,
            commit_frame,
        )
    except Exception as error:
        return _result(
            plan=plan,
            decision=decision,
            arm=arm,
            arm_guard=arm_guard,
            preflight=preflight,
            commit=commit,
            terminal_reason=CameraBridgeCaptureTerminalReason.OBSERVATION_EXCEPTION,
            detail="Final structural guard chain failed closed.",
            error=error,
        )
    if (
        not _guard_binds(arm_guard, before=decision, after=arm)
        or not _guard_binds(commit_guard, before=arm, after=commit)
        or not _guard_binds(
            decision_commit_guard,
            before=decision,
            after=commit,
        )
        or commit_guard.disposition is not CameraArmGuardDisposition.RETAIN
        or decision_commit_guard.disposition
        is not CameraArmGuardDisposition.RETAIN
    ):
        return _result(
            plan=plan,
            decision=decision,
            arm=arm,
            arm_guard=arm_guard,
            preflight=preflight,
            commit=commit,
            commit_guard=commit_guard,
            decision_commit_guard=decision_commit_guard,
            terminal_reason=CameraBridgeCaptureTerminalReason.WORLD_CHANGED,
            detail="The commit did not retain the exact guarded world chain.",
        )

    for guard_name, guard in (
        ("external provenance/identity", pre_input_guard),
        ("last-seam external input", final_input_guard),
    ):
        try:
            if guard is not None:
                guard(decision, arm, commit)
        except Exception as error:
            return _result(
                plan=plan,
                decision=decision,
                arm=arm,
                arm_guard=arm_guard,
                preflight=preflight,
                commit=commit,
                commit_guard=commit_guard,
                decision_commit_guard=decision_commit_guard,
                arm_age=_arm_age_not_reached(arm_origin_clock_s),
                terminal_reason=CameraBridgeCaptureTerminalReason.OBSERVATION_EXCEPTION,
                detail=f"The {guard_name} guard failed closed.",
                error=error,
            )
    try:
        input_start_clock_s = _read_clock(clock)
        arm_age = _completed_age(arm_origin_clock_s, input_start_clock_s)
    except Exception as error:
        return _result(
            plan=plan,
            decision=decision,
            arm=arm,
            arm_guard=arm_guard,
            preflight=preflight,
            commit=commit,
            commit_guard=commit_guard,
            decision_commit_guard=decision_commit_guard,
            arm_age=_clock_error_age(arm_origin_clock_s),
            terminal_reason=CameraBridgeCaptureTerminalReason.CLOCK_ERROR,
            detail="The final arm-age clock sample failed closed.",
            error=error,
        )
    if arm_age.status is CameraServoArmAgeStatus.EXPIRED:
        return _result(
            plan=plan,
            decision=decision,
            arm=arm,
            arm_guard=arm_guard,
            preflight=preflight,
            commit=commit,
            commit_guard=commit_guard,
            decision_commit_guard=decision_commit_guard,
            arm_age=arm_age,
            terminal_reason=CameraBridgeCaptureTerminalReason.ARM_FRESHNESS_EXPIRED,
            detail="The independent arm-to-input age reached its exclusive limit.",
        )

    prepared_control = _PreparedCameraControl(control, preflight)
    try:
        receipt = CameraPlanRunner(prepared_control, sleeper).run(plan)
    except Exception as error:
        return _result(
            plan=plan,
            decision=decision,
            arm=arm,
            arm_guard=arm_guard,
            preflight=preflight,
            commit=commit,
            commit_guard=commit_guard,
            decision_commit_guard=decision_commit_guard,
            arm_age=arm_age,
            input_state=(
                CameraBridgeCaptureInputState.PARTIAL_OR_UNKNOWN
                if prepared_control.input_attempted
                else CameraBridgeCaptureInputState.NONE
            ),
            input_start_clock_s=(
                input_start_clock_s if prepared_control.input_attempted else None
            ),
            terminal_reason=CameraBridgeCaptureTerminalReason.INPUT_EXCEPTION,
            detail="Fixed bridge input or receipt validation failed closed.",
            error=error,
        )
    try:
        receipt_clock_s = _read_clock(clock)
        delivery_duration_s = _input_delivery_duration(
            input_start_clock_s,
            receipt_clock_s,
        )
    except Exception as error:
        return _result(
            plan=plan,
            decision=decision,
            arm=arm,
            arm_guard=arm_guard,
            preflight=preflight,
            commit=commit,
            commit_guard=commit_guard,
            decision_commit_guard=decision_commit_guard,
            arm_age=arm_age,
            receipt=receipt,
            input_start_clock_s=input_start_clock_s,
            terminal_reason=CameraBridgeCaptureTerminalReason.CLOCK_ERROR,
            detail="The immediate post-receipt clock sample failed closed.",
            error=error,
        )
    try:
        sleeper(CAMERA_BRIDGE_CAPTURE_SETTLE_SECONDS)
    except Exception as error:
        return _result(
            plan=plan,
            decision=decision,
            arm=arm,
            arm_guard=arm_guard,
            preflight=preflight,
            commit=commit,
            commit_guard=commit_guard,
            decision_commit_guard=decision_commit_guard,
            arm_age=arm_age,
            receipt=receipt,
            input_start_clock_s=input_start_clock_s,
            input_receipt_clock_s=receipt_clock_s,
            input_delivery_duration_s=delivery_duration_s,
            terminal_reason=CameraBridgeCaptureTerminalReason.SETTLE_EXCEPTION,
            detail="Post-action settle failed after acknowledged input.",
            error=error,
        )
    try:
        post_frame, post = _capture_evidence_without_production(
            source,
            recorder,
            "r2-post",
        )
    except Exception as error:
        return _result(
            plan=plan,
            decision=decision,
            arm=arm,
            arm_guard=arm_guard,
            preflight=preflight,
            commit=commit,
            commit_guard=commit_guard,
            decision_commit_guard=decision_commit_guard,
            arm_age=arm_age,
            receipt=receipt,
            input_start_clock_s=input_start_clock_s,
            input_receipt_clock_s=receipt_clock_s,
            input_delivery_duration_s=delivery_duration_s,
            terminal_reason=CameraBridgeCaptureTerminalReason.OBSERVATION_EXCEPTION,
            detail="Post-action capture, recording, or evaluation failed.",
            error=error,
        )
    if not _strictly_newer(commit_frame, post_frame):
        return _result(
            plan=plan,
            decision=decision,
            arm=arm,
            arm_guard=arm_guard,
            preflight=preflight,
            commit=commit,
            commit_guard=commit_guard,
            decision_commit_guard=decision_commit_guard,
            arm_age=arm_age,
            receipt=receipt,
            post=post,
            input_start_clock_s=input_start_clock_s,
            input_receipt_clock_s=receipt_clock_s,
            input_delivery_duration_s=delivery_duration_s,
            terminal_reason=CameraBridgeCaptureTerminalReason.NON_FRESH_OBSERVATION,
            detail="The post-action frame was not strictly newer than commit.",
        )
    return _result(
        plan=plan,
        decision=decision,
        arm=arm,
        arm_guard=arm_guard,
        preflight=preflight,
        commit=commit,
        commit_guard=commit_guard,
        decision_commit_guard=decision_commit_guard,
        arm_age=arm_age,
        receipt=receipt,
        post=post,
        input_start_clock_s=input_start_clock_s,
        input_receipt_clock_s=receipt_clock_s,
        input_delivery_duration_s=delivery_duration_s,
        terminal_reason=(
            CameraBridgeCaptureTerminalReason.POST_CAPTURE_PENDING_CLOSURE
        ),
        detail=(
            "One fixed right-arrow bridge primitive completed with an exact "
            "receipt and fresh recorded post frame; production awaits the "
            "same-transaction post-registration closure."
        ),
    )


class _ClockError(ValueError):
    pass


class _PreparedCameraControl:
    """Replay one completed preflight without repeating its focusing side effect."""

    __slots__ = ("_control", "_input_attempted", "_preflight", "_served")

    def __init__(
        self,
        control: CameraControl,
        preflight: CameraPreflightReceipt,
    ) -> None:
        self._control = control
        self._preflight = preflight
        self._served = False
        self._input_attempted = False

    @property
    def input_attempted(self) -> bool:
        return self._input_attempted

    def preflight(self) -> CameraPreflightReceipt:
        if self._served:
            raise RuntimeError("prepared preflight is single-use")
        self._served = True
        return self._preflight

    def click_compass(self, x: int, y: int) -> CameraInputReceipt:
        self._input_attempted = True
        return self._control.click_compass(x, y)

    def key_down(self, key: str) -> CameraInputReceipt:
        previous_state = self._input_attempted
        self._input_attempted = True
        try:
            return self._control.key_down(key)
        except CameraInputNotAttemptedError:
            self._input_attempted = previous_state
            raise

    def key_up(self, key: str) -> CameraInputReceipt:
        self._input_attempted = True
        return self._control.key_up(key)

    def scroll_camera(self, x: int, y: int, detents: int) -> CameraInputReceipt:
        self._input_attempted = True
        return self._control.scroll_camera(x, y, detents)

    def drag_camera(
        self,
        x: int,
        y: int,
        delta_x: int,
        delta_y: int,
    ) -> tuple[CameraInputReceipt, CameraInputReceipt, CameraInputReceipt]:
        self._input_attempted = True
        return self._control.drag_camera(x, y, delta_x, delta_y)


def _capture_evidence(
    source: CameraFrameSource,
    recorder: CameraArtifactRecorder,
    label: str,
) -> tuple[Frame, CameraServoFrameEvidence]:
    frame = source.capture()
    return frame, _evaluate_captured_frame(frame, recorder, label)


def _capture_evidence_without_production(
    source: CameraFrameSource,
    recorder: CameraArtifactRecorder,
    label: str,
) -> tuple[Frame, _CameraBridgePendingPostEvidence]:
    """Record post pixels/readiness while deferring sole production evaluation."""

    frame = source.capture()
    artifact = _record_verified_frame(recorder, label, frame)
    return frame, _CameraBridgePendingPostEvidence(
        artifact=artifact,
        captured_monotonic_s=frame.captured_monotonic_s,
        readiness=evaluate_client_input_readiness(frame),
    )


def _finalize_camera_bridge_post_production(
    result: CameraBridgeCaptureResult,
    post_frame: Frame,
) -> tuple[CameraBridgeCaptureResult, CameraEvaluation | None]:
    """Run and bind the fixed post evaluator at the ordered composition seam."""

    if not isinstance(result, CameraBridgeCaptureResult):
        raise TypeError("result must be CameraBridgeCaptureResult")
    if (
        result.terminal_reason
        is not CameraBridgeCaptureTerminalReason.POST_CAPTURE_PENDING_CLOSURE
        or not result.input_completed
        or not isinstance(result.post, _CameraBridgePendingPostEvidence)
    ):
        raise ValueError("only a pending exact post capture can be finalized")
    if not isinstance(post_frame, Frame):
        raise TypeError("post_frame must be a Frame")
    if (
        hashlib.sha256(post_frame.payload).hexdigest()
        != result.post.artifact.raw_sha256
        or post_frame.frame_id != result.post.artifact.frame_id
        or post_frame.width != result.post.artifact.width
        or post_frame.height != result.post.artifact.height
        or post_frame.pixel_format.value != result.post.artifact.pixel_format
        or post_frame.captured_monotonic_s != result.post.captured_monotonic_s
    ):
        raise ValueError("post_frame does not bind the exact pending post artifact")
    readiness = evaluate_client_input_readiness(post_frame)
    if readiness != result.post.readiness:
        raise ValueError("post_frame readiness changed before production finalization")
    if not result.post.readiness.safe_to_attempt_camera_input:
        production = None
        terminal = CameraBridgeCaptureTerminalReason.READINESS_LOST
        detail = "Post-action readiness vetoed the fixed production evaluation."
    else:
        production = evaluate_varrock_east_camera(post_frame)
        terminal_detail = _frame_terminal(
            CameraServoFrameEvidence(
                artifact=result.post.artifact,
                captured_monotonic_s=result.post.captured_monotonic_s,
                readiness=result.post.readiness,
                production=production,
            )
        )
        if terminal_detail is None:
            terminal = CameraBridgeCaptureTerminalReason.CAPTURE_COMPLETE
            detail = (
                "The sole unchanged production evaluation bound the exact post "
                "frame and remained fail closed."
            )
        else:
            terminal, reason = terminal_detail
            detail = f"Post-action production observation: {reason}"
    post = CameraServoFrameEvidence(
        artifact=result.post.artifact,
        captured_monotonic_s=result.post.captured_monotonic_s,
        readiness=result.post.readiness,
        production=production,
    )
    return (
        replace(
            result,
            post=post,
            terminal_reason=terminal,
            detail=detail,
        ),
        production,
    )


def _evaluate_captured_frame(
    frame: Frame,
    recorder: CameraArtifactRecorder,
    label: str,
) -> CameraServoFrameEvidence:
    artifact = _record_verified_frame(recorder, label, frame)
    readiness = evaluate_client_input_readiness(frame)
    production = (
        evaluate_varrock_east_camera(frame)
        if readiness.safe_to_attempt_camera_input
        else None
    )
    return CameraServoFrameEvidence(
        artifact=artifact,
        captured_monotonic_s=frame.captured_monotonic_s,
        readiness=readiness,
        production=production,
    )


def _record_verified_frame(
    recorder: CameraArtifactRecorder,
    label: str,
    frame: Frame,
) -> CameraFrameArtifact:
    artifact = recorder(label, frame)
    if (
        artifact.label != label
        or artifact.frame_id != frame.frame_id
        or artifact.width != frame.width
        or artifact.height != frame.height
        or artifact.pixel_format != frame.pixel_format.value
        or artifact.raw_sha256 != hashlib.sha256(frame.payload).hexdigest()
    ):
        raise ValueError("recorder artifact does not bind the exact captured frame")
    return artifact


def _frame_terminal(
    evidence: CameraServoFrameEvidence,
) -> tuple[CameraBridgeCaptureTerminalReason, str] | None:
    if not evidence.readiness.safe_to_attempt_camera_input:
        return (
            CameraBridgeCaptureTerminalReason.READINESS_LOST,
            "Gameplay readiness vetoed camera input.",
        )
    production = evidence.production
    assert production is not None
    if production.passed:
        return (
            CameraBridgeCaptureTerminalReason.PRODUCTION_PASS,
            "The unchanged production evaluator passed; no bridge input is needed.",
        )
    if not _is_fail_closed(production):
        return (
            CameraBridgeCaptureTerminalReason.PRODUCTION_REJECTION_NOT_FAIL_CLOSED,
            "Production rejection did not hide every definitive resource target.",
        )
    return None


def _is_fail_closed(evaluation: CameraEvaluation) -> bool:
    return (
        not evaluation.passed
        and not evaluation.scene_validated
        and evaluation.definitive_target_ids == ()
        and bool(evaluation.resource_states)
        and all(
            item.state is ResourceVisualState.UNCERTAIN
            for item in evaluation.resource_states
        )
    )


def _strictly_newer(before: Frame, after: Frame) -> bool:
    return (
        after.frame_id > before.frame_id
        and after.captured_monotonic_s > before.captured_monotonic_s
    )


def _evidence_strictly_newer(
    before: CameraServoFrameEvidence | _CameraBridgePendingPostEvidence,
    after: CameraServoFrameEvidence | _CameraBridgePendingPostEvidence,
) -> bool:
    return (
        after.artifact.frame_id > before.artifact.frame_id
        and after.captured_monotonic_s > before.captured_monotonic_s
    )


def _guard_binds(
    guard: CameraArmGuardResult,
    *,
    before: CameraServoFrameEvidence,
    after: CameraServoFrameEvidence,
) -> bool:
    return (
        guard.decision_frame_id == before.artifact.frame_id
        and guard.decision_captured_monotonic_s == before.captured_monotonic_s
        and guard.decision_payload_sha256 == before.artifact.raw_sha256
        and guard.arm_frame_id == after.artifact.frame_id
        and guard.arm_captured_monotonic_s == after.captured_monotonic_s
        and guard.arm_payload_sha256 == after.artifact.raw_sha256
    )


def _read_clock(clock: Callable[[], float]) -> float:
    try:
        value = clock()
    except Exception as error:
        raise _ClockError("monotonic clock raised") from error
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0.0
    ):
        raise _ClockError("monotonic clock must return a finite non-negative value")
    return float(value)


def _completed_age(origin: float, final: float) -> CameraServoArmAgeEvidence:
    if final < origin:
        raise _ClockError("monotonic clock regressed during the arm seam")
    age = final - origin
    return CameraServoArmAgeEvidence(
        origin_clock_s=origin,
        final_clock_s=final,
        age_s=age,
        maximum_age_s=MAXIMUM_ARM_TO_INPUT_AGE_SECONDS,
        status=(
            CameraServoArmAgeStatus.EXPIRED
            if age >= MAXIMUM_ARM_TO_INPUT_AGE_SECONDS
            else CameraServoArmAgeStatus.WITHIN_LIMIT
        ),
    )


def _arm_age_not_reached(origin: float) -> CameraServoArmAgeEvidence:
    return CameraServoArmAgeEvidence(
        origin_clock_s=origin,
        final_clock_s=None,
        age_s=None,
        maximum_age_s=MAXIMUM_ARM_TO_INPUT_AGE_SECONDS,
        status=CameraServoArmAgeStatus.NOT_REACHED,
    )


def _clock_error_age(origin: float) -> CameraServoArmAgeEvidence:
    return CameraServoArmAgeEvidence(
        origin_clock_s=origin,
        final_clock_s=None,
        age_s=None,
        maximum_age_s=MAXIMUM_ARM_TO_INPUT_AGE_SECONDS,
        status=CameraServoArmAgeStatus.FINAL_CLOCK_ERROR,
    )


def _input_delivery_duration(start: float, receipt: float) -> float:
    if receipt < start:
        raise _ClockError("monotonic clock regressed during input delivery")
    return receipt - start


def _result(
    *,
    plan: CameraPlan,
    terminal_reason: CameraBridgeCaptureTerminalReason,
    detail: str,
    decision: CameraServoFrameEvidence | None = None,
    arm: CameraServoFrameEvidence | None = None,
    arm_guard: CameraArmGuardResult | None = None,
    preflight: CameraPreflightReceipt | None = None,
    commit: CameraServoFrameEvidence | None = None,
    commit_guard: CameraArmGuardResult | None = None,
    decision_commit_guard: CameraArmGuardResult | None = None,
    arm_age: CameraServoArmAgeEvidence | None = None,
    receipt: CameraPlanReceipt | None = None,
    post: CameraServoFrameEvidence | _CameraBridgePendingPostEvidence | None = None,
    input_state: CameraBridgeCaptureInputState | None = None,
    input_start_clock_s: float | None = None,
    input_receipt_clock_s: float | None = None,
    input_delivery_duration_s: float | None = None,
    error: Exception | None = None,
) -> CameraBridgeCaptureResult:
    resolved_state = (
        CameraBridgeCaptureInputState.COMPLETE
        if receipt is not None
        else CameraBridgeCaptureInputState.NONE
        if input_state is None
        else input_state
    )
    return CameraBridgeCaptureResult(
        plan=plan,
        decision=decision,
        arm=arm,
        arm_guard=arm_guard,
        preflight=receipt.preflight if receipt is not None else preflight,
        commit=commit,
        commit_guard=commit_guard,
        decision_commit_guard=decision_commit_guard,
        arm_age=arm_age,
        receipt=receipt,
        post=post,
        terminal_reason=terminal_reason,
        detail=detail,
        input_state=resolved_state,
        input_start_clock_s=input_start_clock_s,
        input_receipt_clock_s=input_receipt_clock_s,
        input_delivery_duration_s=input_delivery_duration_s,
        exception=(
            None
            if error is None
            else CameraServoExceptionEvidence(type(error).__name__, str(error))
        ),
    )


def _validate_timing(result: CameraBridgeCaptureResult) -> None:
    for name, value in (
        ("input_start_clock_s", result.input_start_clock_s),
        ("input_receipt_clock_s", result.input_receipt_clock_s),
        ("input_delivery_duration_s", result.input_delivery_duration_s),
    ):
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0.0
        ):
            raise ValueError(f"{name} must be finite and non-negative")
    if result.input_state is CameraBridgeCaptureInputState.NONE and any(
        value is not None
        for value in (
            result.input_start_clock_s,
            result.input_receipt_clock_s,
            result.input_delivery_duration_s,
        )
    ):
        raise ValueError("zero-input evidence cannot retain input timing")
    if result.input_state is not CameraBridgeCaptureInputState.NONE:
        if (
            result.arm_age is None
            or result.arm_age.final_clock_s is None
            or result.input_start_clock_s != result.arm_age.final_clock_s
        ):
            raise ValueError("attempted input must bind the final arm-age clock")


def _validate_receipt_state(result: CameraBridgeCaptureResult) -> None:
    receipt = result.receipt
    if receipt is not None:
        if (
            receipt.plan is not _FIXED_PLAN
            or result.preflight is None
            or receipt.preflight != result.preflight
            or len(receipt.action_receipts) != 1
        ):
            raise ValueError("bridge receipt must bind the exact plan and preflight")
    if result.input_state is CameraBridgeCaptureInputState.COMPLETE:
        if receipt is None:
            raise ValueError("complete bridge input requires a complete receipt")
    elif receipt is not None:
        raise ValueError("a complete receipt requires complete input state")
    if result.input_state is CameraBridgeCaptureInputState.PARTIAL_OR_UNKNOWN:
        if (
            result.terminal_reason
            is not CameraBridgeCaptureTerminalReason.INPUT_EXCEPTION
            or result.exception is None
            or receipt is not None
            or result.post is not None
        ):
            raise ValueError("partial bridge input requires one terminal input exception")
    if receipt is None:
        if (
            result.input_receipt_clock_s is not None
            or result.input_delivery_duration_s is not None
        ):
            raise ValueError("receipt timing requires a complete receipt")
    elif result.input_receipt_clock_s is None:
        if (
            result.input_delivery_duration_s is not None
            or result.terminal_reason
            is not CameraBridgeCaptureTerminalReason.CLOCK_ERROR
            or result.exception is None
        ):
            raise ValueError("untimed complete receipt requires an explicit clock error")
    else:
        assert result.input_start_clock_s is not None
        duration = result.input_receipt_clock_s - result.input_start_clock_s
        if duration < 0.0 or result.input_delivery_duration_s != duration:
            raise ValueError("receipt timing must retain its exact monotonic duration")


def _validate_frame_chronology(result: CameraBridgeCaptureResult) -> None:
    stages = (
        ("r2-decision", result.decision),
        ("r2-arm", result.arm),
        ("r2-commit", result.commit),
        ("r2-post", result.post),
    )
    seen_missing = False
    chain: list[CameraServoFrameEvidence | _CameraBridgePendingPostEvidence] = []
    for expected_label, evidence in stages:
        if evidence is None:
            seen_missing = True
            continue
        if seen_missing:
            raise ValueError("retained bridge frame stages must be contiguous")
        if evidence.artifact.label != expected_label:
            raise ValueError("retained bridge frame has an unexpected stage label")
        chain.append(evidence)

    nonfresh_pairs = tuple(
        (before, after)
        for before, after in zip(chain, chain[1:], strict=False)
        if not _evidence_strictly_newer(before, after)
    )
    if nonfresh_pairs:
        if (
            result.terminal_reason
            is not CameraBridgeCaptureTerminalReason.NON_FRESH_OBSERVATION
            or len(nonfresh_pairs) != 1
            or nonfresh_pairs[0][1] is not chain[-1]
        ):
            raise ValueError("retained bridge frames must be strictly chronological")
    elif (
        result.terminal_reason
        is CameraBridgeCaptureTerminalReason.NON_FRESH_OBSERVATION
    ):
        raise ValueError("non-fresh terminal evidence requires a stale final stage")
    if result.post is not None and (result.receipt is None or result.commit is None):
        raise ValueError("post evidence requires a complete receipt and commit")


def _validate_attempted_input_seam(result: CameraBridgeCaptureResult) -> None:
    if result.input_state is CameraBridgeCaptureInputState.NONE:
        return
    required = (
        result.decision,
        result.arm,
        result.arm_guard,
        result.preflight,
        result.commit,
        result.commit_guard,
        result.decision_commit_guard,
        result.arm_age,
    )
    if any(item is None for item in required):
        raise ValueError("attempted input requires every pre-input evidence binding")
    assert result.decision is not None
    assert result.arm is not None
    assert result.arm_guard is not None
    assert result.preflight is not None
    assert result.commit is not None
    assert result.commit_guard is not None
    assert result.decision_commit_guard is not None
    assert result.arm_age is not None
    if not result.preflight.supported:
        raise ValueError("attempted input requires a supported preflight")
    if any(
        _frame_terminal(item) is not None
        for item in (result.decision, result.arm, result.commit)
    ):
        raise ValueError("attempted input requires production-fail-closed frames")
    if (
        result.arm_guard.disposition is not CameraArmGuardDisposition.RETAIN
        or result.commit_guard.disposition is not CameraArmGuardDisposition.RETAIN
        or result.decision_commit_guard.disposition
        is not CameraArmGuardDisposition.RETAIN
        or not _guard_binds(
            result.arm_guard,
            before=result.decision,
            after=result.arm,
        )
        or not _guard_binds(
            result.commit_guard,
            before=result.arm,
            after=result.commit,
        )
        or not _guard_binds(
            result.decision_commit_guard,
            before=result.decision,
            after=result.commit,
        )
        or result.arm_age.status is not CameraServoArmAgeStatus.WITHIN_LIMIT
    ):
        raise ValueError("attempted input requires an exact retained fresh guard chain")


def _validate_terminal_result(result: CameraBridgeCaptureResult) -> None:
    if (
        result.terminal_reason
        is CameraBridgeCaptureTerminalReason.POST_CAPTURE_PENDING_CLOSURE
    ):
        if (
            not result.input_completed
            or not isinstance(result.post, _CameraBridgePendingPostEvidence)
        ):
            raise ValueError(
                "pending post closure requires a complete receipt and raw post evidence"
            )
        return
    if result.terminal_reason is CameraBridgeCaptureTerminalReason.CAPTURE_COMPLETE:
        if (
            not result.input_completed
            or result.post is None
            or result.post.production is None
            or not _is_fail_closed(result.post.production)
        ):
            raise ValueError("capture-complete requires a fresh fail-closed post frame")
    if result.terminal_reason is CameraBridgeCaptureTerminalReason.PRODUCTION_PASS:
        final = result.post or result.commit or result.arm or result.decision
        if not isinstance(final, CameraServoFrameEvidence):
            raise ValueError("production pass cannot retain pending post evidence")
        if final is None or final.production is None or not final.production.passed:
            raise ValueError("production-pass requires unchanged production evidence")
        if result.input_completed and result.post is not final:
            raise ValueError("post-input production pass must bind the exact post frame")


def _require_digest(value: str, name: str) -> None:
    if len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def _plan_dict(plan: CameraPlan) -> dict[str, object]:
    action = plan.actions[0]
    assert isinstance(action, CameraKeyHold)
    return {
        "name": plan.name,
        "actions": [
            {
                "duration_s": action.duration_s,
                "key": action.key.value,
                "kind": "key_hold",
            }
        ],
    }


def _artifact_dict(artifact: CameraFrameArtifact) -> dict[str, object]:
    return {
        "files": dict(artifact.files),
        "frame_id": artifact.frame_id,
        "height": artifact.height,
        "label": artifact.label,
        "pixel_format": artifact.pixel_format,
        "raw_sha256": artifact.raw_sha256,
        "width": artifact.width,
    }


def _frame_evidence_dict(
    evidence: CameraServoFrameEvidence | _CameraBridgePendingPostEvidence | None,
) -> dict[str, object] | None:
    if evidence is None:
        return None
    return {
        "artifact": _artifact_dict(evidence.artifact),
        "captured_monotonic_s": evidence.captured_monotonic_s,
        "production": _production_dict(
            evidence.production
            if isinstance(evidence, CameraServoFrameEvidence)
            else None
        ),
        "readiness": _readiness_dict(evidence.readiness),
    }


def _readiness_dict(readiness: ClientInputReadiness) -> dict[str, object]:
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
            for item in readiness.anchors
        ],
        "can_accept": readiness.can_accept,
        "can_expose_resources": readiness.can_expose_resources,
        "can_validate_scene": readiness.can_validate_scene,
        "detail": readiness.detail,
        "evaluator_id": readiness.evaluator_id,
        "evaluator_version": readiness.evaluator_version,
        "reason": readiness.reason.value,
        "safe_to_attempt_camera_input": readiness.safe_to_attempt_camera_input,
    }


def _production_dict(
    production: CameraEvaluation | None,
) -> dict[str, object] | None:
    if production is None:
        return None
    return {
        "definitive_target_ids": list(production.definitive_target_ids),
        "detector_id": production.detector_id,
        "detector_version": production.detector_version,
        "frame_geometry_supported": production.frame_geometry_supported,
        "landmarks": [
            {
                "distance": item.distance,
                "landmark_id": item.landmark_id,
                "matched": item.matched,
                "threshold": item.threshold,
                "zone": item.zone.value,
            }
            for item in production.landmarks
        ],
        "matched_landmark_count": production.matched_landmark_count,
        "matched_zones": [zone.value for zone in production.matched_zones],
        "passed": production.passed,
        "profile_frame_height": production.profile_frame_height,
        "profile_frame_width": production.profile_frame_width,
        "profile_id": production.profile_id,
        "profile_pixel_format": production.profile_pixel_format.value,
        "profile_schema_version": production.profile_schema_version,
        "required_landmark_count": production.required_landmark_count,
        "required_landmark_matches": production.required_landmark_matches,
        "required_matched_zones": production.required_matched_zones,
        "resource_states": [
            {
                "confidence": item.confidence,
                "resource_id": item.resource_id,
                "state": item.state.value,
            }
            for item in production.resource_states
        ],
        "scene_reason": production.scene_reason,
        "scene_validated": production.scene_validated,
    }


def _arm_guard_dict(
    guard: CameraArmGuardResult | None,
) -> dict[str, object] | None:
    if guard is None:
        return None
    return {
        "arm_frame": {
            "captured_monotonic_s": guard.arm_captured_monotonic_s,
            "frame_id": guard.arm_frame_id,
            "raw_sha256": guard.arm_payload_sha256,
        },
        "can_accept": guard.can_accept,
        "can_expose_resources": guard.can_expose_resources,
        "can_validate_scene": guard.can_validate_scene,
        "decision_frame": {
            "captured_monotonic_s": guard.decision_captured_monotonic_s,
            "frame_id": guard.decision_frame_id,
            "raw_sha256": guard.decision_payload_sha256,
        },
        "detail": guard.detail,
        "disposition": guard.disposition.value,
        "evaluated_zones": [zone.value for zone in guard.evaluated_zones],
        "guard_id": guard.guard_id,
        "guard_version": guard.guard_version,
        "reason": guard.reason.value,
        "stable_landmark_count": guard.stable_landmark_count,
        "stable_zones": [zone.value for zone in guard.stable_zones],
    }


def _preflight_dict(
    preflight: CameraPreflightReceipt | None,
) -> dict[str, object] | None:
    if preflight is None:
        return None
    return {
        "client_height": preflight.client_height,
        "client_width": preflight.client_width,
        "focused": preflight.focused,
        "supported": preflight.supported,
    }


def _receipt_dict(receipt: CameraPlanReceipt | None) -> dict[str, object] | None:
    if receipt is None:
        return None
    return {
        "actions": [
            {
                "action_index": item.action_index,
                "input_receipts": [
                    {
                        "complete": input_receipt.complete,
                        "completed_events": input_receipt.completed_events,
                        "operation": input_receipt.operation.value,
                        "requested_events": input_receipt.requested_events,
                    }
                    for input_receipt in item.input_receipts
                ],
            }
            for item in receipt.action_receipts
        ],
        "plan": _plan_dict(receipt.plan),
        "preflight": _preflight_dict(receipt.preflight),
    }


def _arm_age_dict(
    age: CameraServoArmAgeEvidence | None,
) -> dict[str, object] | None:
    if age is None:
        return None
    return {
        "age_s": age.age_s,
        "final_clock_s": age.final_clock_s,
        "maximum_age_s": age.maximum_age_s,
        "origin_clock_s": age.origin_clock_s,
        "status": age.status.value,
    }
