"""One-step, production-gated north bootstrap for Camera Guidance V2.

The helper is development-only and accepts an injected camera-control adapter.
It can execute exactly one reviewed compass click, never a wheel or drag.  A
fresh decision, arm, and final commit observation must all remain ready,
fail-closed, structurally stable, and inside the independent arm-age limit.
Production perception remains the only scene-acceptance authority.
"""

from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
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
from .camera_guidance_v2 import (
    CameraGuidanceV2Disposition,
    CameraGuidanceV2Session,
    WorldCameraGuidanceV2,
    select_camera_guidance_v2,
)
from .camera_plan import (
    CameraControl,
    CameraInputOperation,
    CameraInputReceipt,
    CameraPlan,
    CameraPlanReceipt,
    CameraPlanRunner,
    CameraPreflightReceipt,
    CompassClick,
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
from .client_readiness import evaluate_client_input_readiness

__all__ = [
    "MAXIMUM_NORTH_BOOTSTRAP_SETTLE_SECONDS",
    "CameraNorthBootstrapInputState",
    "CameraNorthBootstrapResult",
    "CameraNorthBootstrapTerminalReason",
    "run_camera_north_bootstrap",
]

MAXIMUM_NORTH_BOOTSTRAP_SETTLE_SECONDS: Final[float] = 10.0


class CameraNorthBootstrapTerminalReason(StrEnum):
    """Stable outcome for the one-action north bootstrap."""

    PRODUCTION_PASS = "production_pass"
    BOOTSTRAP_EXECUTED = "bootstrap_executed"
    READINESS_LOST = "readiness_lost"
    PRODUCTION_REJECTION_NOT_FAIL_CLOSED = "production_rejection_not_fail_closed"
    INSUFFICIENT_GUIDANCE = "insufficient_guidance"
    NON_FRESH_OBSERVATION = "non_fresh_observation"
    WORLD_CHANGED = "world_changed"
    ARM_FRESHNESS_EXPIRED = "arm_freshness_expired"
    CLOCK_ERROR = "clock_error"
    OBSERVATION_EXCEPTION = "observation_exception"
    INPUT_EXCEPTION = "input_exception"
    SETTLE_EXCEPTION = "settle_exception"


class CameraNorthBootstrapInputState(StrEnum):
    """Honest side-effect state for the single reviewed compass request."""

    NONE = "none"
    PARTIAL_OR_UNKNOWN = "partial_or_unknown"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class CameraNorthBootstrapResult:
    """Immutable evidence for no input or exactly one compass click."""

    initial: CameraServoFrameEvidence | None
    guidance: WorldCameraGuidanceV2 | None
    plan: CameraPlan | None
    arm: CameraServoFrameEvidence | None
    arm_guard: CameraArmGuardResult | None
    commit: CameraServoFrameEvidence | None
    commit_guard: CameraArmGuardResult | None
    decision_commit_guard: CameraArmGuardResult | None
    arm_age: CameraServoArmAgeEvidence | None
    preflight: CameraPreflightReceipt | None
    receipt: CameraPlanReceipt | None
    post: CameraServoFrameEvidence | None
    post_guidance: WorldCameraGuidanceV2 | None
    terminal_reason: CameraNorthBootstrapTerminalReason
    detail: str
    input_state: CameraNorthBootstrapInputState = CameraNorthBootstrapInputState.NONE
    input_start_clock_s: float | None = None
    input_receipt_clock_s: float | None = None
    input_delivery_duration_s: float | None = None
    exception: CameraServoExceptionEvidence | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.terminal_reason, CameraNorthBootstrapTerminalReason):
            raise ValueError("north bootstrap terminal reason is invalid")
        if not isinstance(self.detail, str) or not self.detail.strip():
            raise ValueError("north bootstrap detail must not be empty")
        if not isinstance(self.input_state, CameraNorthBootstrapInputState):
            raise ValueError("north bootstrap input state is invalid")
        for name, value in (
            ("input_start_clock_s", self.input_start_clock_s),
            ("input_receipt_clock_s", self.input_receipt_clock_s),
            ("input_delivery_duration_s", self.input_delivery_duration_s),
        ):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0.0
            ):
                raise ValueError(f"{name} must be finite and non-negative")
        if self.plan is not None and (
            len(self.plan.actions) != 1
            or not isinstance(self.plan.actions[0], CompassClick)
        ):
            raise ValueError("north bootstrap plan must contain one compass click")
        if self.receipt is not None:
            if self.plan is None or self.receipt.plan != self.plan:
                raise ValueError("north bootstrap receipt must bind the exact plan")
            if self.preflight is None or self.receipt.preflight != self.preflight:
                raise ValueError("north bootstrap receipt must bind the saved preflight")
            if len(self.receipt.action_receipts) != 1:
                raise ValueError("north bootstrap receipt must contain one action")
            action = self.receipt.action_receipts[0]
            if tuple(item.operation for item in action.input_receipts) != (
                CameraInputOperation.COMPASS_CLICK,
            ):
                raise ValueError("north bootstrap requires one complete click receipt")
        if self.input_state is CameraNorthBootstrapInputState.COMPLETE:
            if self.receipt is None:
                raise ValueError("complete input state requires a complete receipt")
        elif self.receipt is not None:
            raise ValueError("a complete receipt requires complete input state")
        if self.input_state is CameraNorthBootstrapInputState.NONE:
            if any(
                item is not None
                for item in (
                    self.input_start_clock_s,
                    self.input_receipt_clock_s,
                    self.input_delivery_duration_s,
                )
            ):
                raise ValueError(
                    "evidence with input timing cannot claim that no input began"
                )
        else:
            if (
                self.arm_age is None
                or self.arm_age.final_clock_s is None
                or self.input_start_clock_s != self.arm_age.final_clock_s
            ):
                raise ValueError(
                    "attempted input must start at the exact final arm-age clock"
                )
        if self.receipt is None:
            if (
                self.input_receipt_clock_s is not None
                or self.input_delivery_duration_s is not None
            ):
                raise ValueError("receipt timing requires a complete input receipt")
        elif self.input_receipt_clock_s is None:
            if (
                self.input_delivery_duration_s is not None
                or self.terminal_reason
                is not CameraNorthBootstrapTerminalReason.CLOCK_ERROR
                or self.exception is None
            ):
                raise ValueError(
                    "a complete receipt without timing requires an explicit clock error"
                )
        else:
            assert self.input_start_clock_s is not None
            duration = self.input_receipt_clock_s - self.input_start_clock_s
            if duration < 0.0 or self.input_delivery_duration_s != duration:
                raise ValueError(
                    "input receipt timing must be monotonic and retain exact duration"
                )
        if self.input_state is CameraNorthBootstrapInputState.PARTIAL_OR_UNKNOWN:
            if (
                self.terminal_reason
                is not CameraNorthBootstrapTerminalReason.INPUT_EXCEPTION
                or self.exception is None
                or self.receipt is not None
                or self.post is not None
                or self.post_guidance is not None
            ):
                raise ValueError(
                    "partial/unknown input requires one terminal input exception"
                )
        if self.post is not None and self.receipt is None:
            raise ValueError("post-input evidence requires a complete receipt")
        if self.post_guidance is not None and self.post is None:
            raise ValueError("post guidance requires post-input evidence")
        if self.post is not None:
            if self.commit is None or not _evidence_strictly_newer(
                self.commit, self.post
            ):
                raise ValueError("post evidence must be strictly newer than commit")
        if self.post_guidance is not None:
            assert self.post is not None
            if not _guidance_binds(self.post_guidance, self.post):
                raise ValueError("post guidance must bind the exact post evidence")
        if self.terminal_reason is CameraNorthBootstrapTerminalReason.PRODUCTION_PASS:
            final = self.post or self.commit or self.arm or self.initial
            if final is None or final.production is None or not final.production.passed:
                raise ValueError("production pass requires unchanged production evidence")
        if self.terminal_reason is CameraNorthBootstrapTerminalReason.BOOTSTRAP_EXECUTED:
            if self.receipt is None or self.post is None or self.post_guidance is None:
                raise ValueError(
                    "executed bootstrap requires receipt, fresh post, and post guidance"
                )
            if self.post.production is None or not _is_fail_closed(self.post.production):
                raise ValueError("bootstrap-executed is reserved for a fail-closed post frame")
            if (
                not self.post_guidance.heading_was_normalized
                or self.post_guidance.disposition
                is CameraGuidanceV2Disposition.ACTIONABLE_BOOTSTRAP
            ):
                raise ValueError(
                    "post-bootstrap guidance must retain completed north normalization"
                )
        if self.input_state is not CameraNorthBootstrapInputState.NONE:
            if any(
                item is None
                for item in (
                    self.initial,
                    self.guidance,
                    self.plan,
                    self.arm,
                    self.arm_guard,
                    self.commit,
                    self.commit_guard,
                    self.decision_commit_guard,
                    self.arm_age,
                )
            ):
                raise ValueError("input attempt requires every pre-input evidence binding")
            assert self.initial is not None
            assert self.guidance is not None
            assert self.plan is not None
            assert self.arm is not None
            assert self.arm_guard is not None
            assert self.commit is not None
            assert self.commit_guard is not None
            assert self.decision_commit_guard is not None
            assert self.arm_age is not None
            if self.preflight is None or not self.preflight.supported:
                raise ValueError("input attempt requires the exact supported preflight")
            if self.receipt is not None and self.receipt.plan is not self.plan:
                raise ValueError("input receipt must retain the exact prebuilt plan object")
            if (
                self.guidance.disposition
                is not CameraGuidanceV2Disposition.ACTIONABLE_BOOTSTRAP
                or self.guidance.decision_frame_id != self.initial.artifact.frame_id
                or self.guidance.decision_captured_monotonic_s
                != self.initial.captured_monotonic_s
                or self.guidance.decision_raw_sha256 != self.initial.artifact.raw_sha256
                or self.plan.name != "issue31-v2-01-heading-north"
            ):
                raise ValueError("input guidance and plan must bind the initial frame")
            if any(
                _frame_terminal(item) is not None
                for item in (self.initial, self.arm, self.commit)
            ):
                raise ValueError(
                    "input requires ready, production-fail-closed pre-input evidence"
                )
            if not _guard_binds(
                self.arm_guard, before=self.initial, after=self.arm
            ) or not _guard_binds(
                self.commit_guard, before=self.arm, after=self.commit
            ) or not _guard_binds(
                self.decision_commit_guard,
                before=self.initial,
                after=self.commit,
            ):
                raise ValueError("input guards must bind the exact recorded frame chain")
            if (
                self.arm_guard.disposition is not CameraArmGuardDisposition.RETAIN
                or self.commit_guard.disposition
                is not CameraArmGuardDisposition.RETAIN
                or self.decision_commit_guard.disposition
                is not CameraArmGuardDisposition.RETAIN
                or self.arm_age.status is not CameraServoArmAgeStatus.WITHIN_LIMIT
                or self.arm_age.origin_clock_s is None
                or self.arm_age.final_clock_s is None
            ):
                raise ValueError("input requires fresh retained commit evidence")
        elif _retains_complete_input_seam(self):
            raise ValueError(
                "a complete supported input seam cannot claim that no input began"
            )

    @property
    def input_executed(self) -> bool:
        """Return whether input began and may have produced a side effect."""

        return self.input_state is not CameraNorthBootstrapInputState.NONE

    @property
    def input_attempted(self) -> bool:
        """Return whether the adapter began the reviewed input request."""

        return self.input_state is not CameraNorthBootstrapInputState.NONE

    @property
    def input_completed(self) -> bool:
        """Return whether a complete compass receipt was retained."""

        return self.input_state is CameraNorthBootstrapInputState.COMPLETE

    @property
    def passed(self) -> bool:
        """Return only the unchanged production evaluator's success verdict."""

        return self.terminal_reason is CameraNorthBootstrapTerminalReason.PRODUCTION_PASS


def run_camera_north_bootstrap(
    source: CameraFrameSource,
    control: CameraControl,
    *,
    sleeper: Sleeper,
    settle_s: float,
    recorder: CameraArtifactRecorder = record_frame_digest,
    clock: Callable[[], float] = time.monotonic,
    pre_input_guard: (
        Callable[
            [
                CameraServoFrameEvidence,
                CameraServoFrameEvidence,
                CameraServoFrameEvidence,
            ],
            None,
        ]
        | None
    ) = None,
    final_input_guard: (
        Callable[
            [
                CameraServoFrameEvidence,
                CameraServoFrameEvidence,
                CameraServoFrameEvidence,
            ],
            None,
        ]
        | None
    ) = None,
) -> CameraNorthBootstrapResult:
    """Execute at most one compass-north normalization under fresh evidence."""

    if (
        isinstance(settle_s, bool)
        or not isinstance(settle_s, (int, float))
        or not math.isfinite(settle_s)
        or not 0.0 < settle_s <= MAXIMUM_NORTH_BOOTSTRAP_SETTLE_SECONDS
    ):
        raise ValueError(
            "settle_s must be finite and in (0, "
            f"{MAXIMUM_NORTH_BOOTSTRAP_SETTLE_SECONDS}]"
        )
    session = CameraGuidanceV2Session()

    try:
        initial_frame, initial = _capture_evidence(source, recorder, "v2-initial")
    except Exception as error:
        return _result(
            terminal_reason=CameraNorthBootstrapTerminalReason.OBSERVATION_EXCEPTION,
            detail="Initial capture, recording, or evaluation failed closed.",
            error=error,
        )
    terminal = _frame_terminal(initial)
    if terminal is not None:
        reason, detail = terminal
        return _result(initial=initial, terminal_reason=reason, detail=detail)
    assert initial.production is not None

    try:
        guidance = select_camera_guidance_v2(initial_frame, session=session)
    except Exception as error:
        return _result(
            initial=initial,
            terminal_reason=CameraNorthBootstrapTerminalReason.OBSERVATION_EXCEPTION,
            detail="World-only V2 guidance evaluation failed closed.",
            error=error,
        )
    if guidance.disposition is not CameraGuidanceV2Disposition.ACTIONABLE_BOOTSTRAP:
        return _result(
            initial=initial,
            guidance=guidance,
            terminal_reason=CameraNorthBootstrapTerminalReason.INSUFFICIENT_GUIDANCE,
            detail="The current frame did not authorize the one-time north bootstrap.",
        )
    try:
        plan = session.build_reserved_plan(guidance, initial_frame, index=1)
    except Exception as error:
        return _result(
            initial=initial,
            guidance=guidance,
            terminal_reason=CameraNorthBootstrapTerminalReason.OBSERVATION_EXCEPTION,
            detail="The session did not retain its exact bootstrap plan token.",
            error=error,
        )

    try:
        arm_frame = source.capture()
        arm_origin_clock_s = _read_clock(clock)
        arm = _evaluate_captured_frame(arm_frame, recorder, "v2-arm")
    except Exception as error:
        return _result(
            initial=initial,
            guidance=guidance,
            plan=plan,
            terminal_reason=(
                CameraNorthBootstrapTerminalReason.CLOCK_ERROR
                if isinstance(error, _ClockError)
                else CameraNorthBootstrapTerminalReason.OBSERVATION_EXCEPTION
            ),
            detail="Fresh arm capture or arm-origin clock failed closed.",
            error=error,
        )
    if not _strictly_newer(initial_frame, arm_frame):
        return _result(
            initial=initial,
            guidance=guidance,
            plan=plan,
            arm=arm,
            terminal_reason=CameraNorthBootstrapTerminalReason.NON_FRESH_OBSERVATION,
            detail="The arm frame was not strictly newer than the decision frame.",
        )
    terminal = _frame_terminal(arm)
    if terminal is not None:
        reason, detail = terminal
        return _result(
            initial=initial,
            guidance=guidance,
            plan=plan,
            arm=arm,
            terminal_reason=reason,
            detail=f"Fresh arm veto: {detail}",
        )
    try:
        arm_guard = evaluate_camera_arm_guard(initial_frame, arm_frame)
    except Exception as error:
        return _result(
            initial=initial,
            guidance=guidance,
            plan=plan,
            arm=arm,
            terminal_reason=CameraNorthBootstrapTerminalReason.OBSERVATION_EXCEPTION,
            detail="Decision-to-arm structural guard failed closed.",
            error=error,
        )
    if arm_guard.disposition is not CameraArmGuardDisposition.RETAIN:
        return _result(
            initial=initial,
            guidance=guidance,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            terminal_reason=CameraNorthBootstrapTerminalReason.WORLD_CHANGED,
            detail="World structure changed before the final commit observation.",
        )

    try:
        commit_frame, commit = _capture_evidence(source, recorder, "v2-commit")
    except Exception as error:
        return _result(
            initial=initial,
            guidance=guidance,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            terminal_reason=CameraNorthBootstrapTerminalReason.OBSERVATION_EXCEPTION,
            detail="Final commit capture, recording, or evaluation failed closed.",
            error=error,
        )
    if not _strictly_newer(arm_frame, commit_frame):
        return _result(
            initial=initial,
            guidance=guidance,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            commit=commit,
            terminal_reason=CameraNorthBootstrapTerminalReason.NON_FRESH_OBSERVATION,
            detail="The final commit frame was not strictly newer than the arm frame.",
        )
    terminal = _frame_terminal(commit)
    if terminal is not None:
        reason, detail = terminal
        return _result(
            initial=initial,
            guidance=guidance,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            commit=commit,
            terminal_reason=reason,
            detail=f"Final commit veto: {detail}",
        )
    try:
        commit_guard = evaluate_camera_arm_guard(arm_frame, commit_frame)
        decision_commit_guard = evaluate_camera_arm_guard(
            initial_frame, commit_frame
        )
    except Exception as error:
        return _result(
            initial=initial,
            guidance=guidance,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            commit=commit,
            terminal_reason=CameraNorthBootstrapTerminalReason.OBSERVATION_EXCEPTION,
            detail="Final world-only structural guard failed closed.",
            error=error,
        )
    if (
        session.north_plan is not plan
        or commit_guard.disposition is not CameraArmGuardDisposition.RETAIN
        or decision_commit_guard.disposition
        is not CameraArmGuardDisposition.RETAIN
    ):
        return _result(
            initial=initial,
            guidance=guidance,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            commit=commit,
            commit_guard=commit_guard,
            decision_commit_guard=decision_commit_guard,
            terminal_reason=CameraNorthBootstrapTerminalReason.WORLD_CHANGED,
            detail="The final commit no longer retained the prevalidated bootstrap token.",
        )

    try:
        if pre_input_guard is not None:
            pre_input_guard(initial, arm, commit)
    except Exception as error:
        return _result(
            initial=initial,
            guidance=guidance,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            commit=commit,
            commit_guard=commit_guard,
            decision_commit_guard=decision_commit_guard,
            arm_age=_arm_age_not_reached(arm_origin_clock_s),
            terminal_reason=CameraNorthBootstrapTerminalReason.OBSERVATION_EXCEPTION,
            detail="The final external pre-input guard failed closed.",
            error=error,
        )

    try:
        preflight = control.preflight()
    except Exception as error:
        return _result(
            initial=initial,
            guidance=guidance,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            commit=commit,
            commit_guard=commit_guard,
            decision_commit_guard=decision_commit_guard,
            terminal_reason=CameraNorthBootstrapTerminalReason.INPUT_EXCEPTION,
            detail="Compass preflight failed before the final arm-age sample.",
            error=error,
        )
    try:
        if final_input_guard is not None:
            final_input_guard(initial, arm, commit)
    except Exception as error:
        return _result(
            initial=initial,
            guidance=guidance,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            commit=commit,
            commit_guard=commit_guard,
            decision_commit_guard=decision_commit_guard,
            arm_age=_arm_age_not_reached(arm_origin_clock_s),
            preflight=preflight,
            terminal_reason=CameraNorthBootstrapTerminalReason.OBSERVATION_EXCEPTION,
            detail="The last-seam external input guard failed closed.",
            error=error,
        )
    try:
        final_clock_s = _read_clock(clock)
        age = _completed_age(arm_origin_clock_s, final_clock_s)
    except Exception as error:
        return _result(
            initial=initial,
            guidance=guidance,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            commit=commit,
            commit_guard=commit_guard,
            decision_commit_guard=decision_commit_guard,
            arm_age=_clock_error_age(arm_origin_clock_s),
            preflight=preflight,
            terminal_reason=CameraNorthBootstrapTerminalReason.CLOCK_ERROR,
            detail="The final arm-age clock sample failed closed.",
            error=error,
        )
    if age.status is CameraServoArmAgeStatus.EXPIRED:
        return _result(
            initial=initial,
            guidance=guidance,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            commit=commit,
            commit_guard=commit_guard,
            decision_commit_guard=decision_commit_guard,
            arm_age=age,
            preflight=preflight,
            terminal_reason=CameraNorthBootstrapTerminalReason.ARM_FRESHNESS_EXPIRED,
            detail="The independent arm-to-input age reached its exclusive limit.",
        )

    prepared_control = _PreparedCameraControl(control, preflight)
    try:
        receipt = CameraPlanRunner(prepared_control, sleeper).run(plan)
    except Exception as error:
        return _result(
            initial=initial,
            guidance=guidance,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            commit=commit,
            commit_guard=commit_guard,
            decision_commit_guard=decision_commit_guard,
            arm_age=age,
            preflight=preflight,
            input_state=(
                CameraNorthBootstrapInputState.PARTIAL_OR_UNKNOWN
                if prepared_control.input_attempted
                else CameraNorthBootstrapInputState.NONE
            ),
            input_start_clock_s=(
                final_clock_s if prepared_control.input_attempted else None
            ),
            terminal_reason=CameraNorthBootstrapTerminalReason.INPUT_EXCEPTION,
            detail="Compass preflight, input, or receipt validation failed closed.",
            error=error,
        )
    try:
        input_receipt_clock_s = _read_clock(clock)
        input_delivery_duration_s = _input_delivery_duration(
            final_clock_s,
            input_receipt_clock_s,
        )
    except Exception as error:
        return _result(
            initial=initial,
            guidance=guidance,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            commit=commit,
            commit_guard=commit_guard,
            decision_commit_guard=decision_commit_guard,
            arm_age=age,
            receipt=receipt,
            input_start_clock_s=final_clock_s,
            terminal_reason=CameraNorthBootstrapTerminalReason.CLOCK_ERROR,
            detail=(
                "The immediate post-receipt clock sample failed closed before "
                "session bookkeeping or settle."
            ),
            error=error,
        )
    try:
        session.record_north_receipt(guidance, initial_frame, receipt)
    except Exception as error:
        return _result(
            initial=initial,
            guidance=guidance,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            commit=commit,
            commit_guard=commit_guard,
            decision_commit_guard=decision_commit_guard,
            arm_age=age,
            receipt=receipt,
            input_start_clock_s=final_clock_s,
            input_receipt_clock_s=input_receipt_clock_s,
            input_delivery_duration_s=input_delivery_duration_s,
            terminal_reason=CameraNorthBootstrapTerminalReason.INPUT_EXCEPTION,
            detail="Complete compass receipt could not update session bookkeeping.",
            error=error,
        )
    try:
        sleeper(float(settle_s))
    except Exception as error:
        return _result(
            initial=initial,
            guidance=guidance,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            commit=commit,
            commit_guard=commit_guard,
            decision_commit_guard=decision_commit_guard,
            arm_age=age,
            receipt=receipt,
            input_start_clock_s=final_clock_s,
            input_receipt_clock_s=input_receipt_clock_s,
            input_delivery_duration_s=input_delivery_duration_s,
            terminal_reason=CameraNorthBootstrapTerminalReason.SETTLE_EXCEPTION,
            detail="Post-bootstrap settle failed after the acknowledged input.",
            error=error,
        )
    try:
        post_frame, post = _capture_evidence(source, recorder, "v2-post")
    except Exception as error:
        return _result(
            initial=initial,
            guidance=guidance,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            commit=commit,
            commit_guard=commit_guard,
            decision_commit_guard=decision_commit_guard,
            arm_age=age,
            receipt=receipt,
            input_start_clock_s=final_clock_s,
            input_receipt_clock_s=input_receipt_clock_s,
            input_delivery_duration_s=input_delivery_duration_s,
            terminal_reason=CameraNorthBootstrapTerminalReason.OBSERVATION_EXCEPTION,
            detail="Post-bootstrap capture, recording, or evaluation failed.",
            error=error,
        )
    if not _strictly_newer(commit_frame, post_frame):
        return _result(
            initial=initial,
            guidance=guidance,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            commit=commit,
            commit_guard=commit_guard,
            decision_commit_guard=decision_commit_guard,
            arm_age=age,
            receipt=receipt,
            post=post,
            input_start_clock_s=final_clock_s,
            input_receipt_clock_s=input_receipt_clock_s,
            input_delivery_duration_s=input_delivery_duration_s,
            terminal_reason=CameraNorthBootstrapTerminalReason.NON_FRESH_OBSERVATION,
            detail="The post-bootstrap frame was not strictly newer than commit.",
        )
    terminal = _frame_terminal(post)
    if terminal is not None:
        reason, detail = terminal
        return _result(
            initial=initial,
            guidance=guidance,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            commit=commit,
            commit_guard=commit_guard,
            decision_commit_guard=decision_commit_guard,
            arm_age=age,
            receipt=receipt,
            post=post,
            input_start_clock_s=final_clock_s,
            input_receipt_clock_s=input_receipt_clock_s,
            input_delivery_duration_s=input_delivery_duration_s,
            terminal_reason=reason,
            detail=f"Post-bootstrap observation: {detail}",
        )
    try:
        post_guidance = select_camera_guidance_v2(post_frame, session=session)
    except Exception as error:
        return _result(
            initial=initial,
            guidance=guidance,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            commit=commit,
            commit_guard=commit_guard,
            decision_commit_guard=decision_commit_guard,
            arm_age=age,
            receipt=receipt,
            post=post,
            input_start_clock_s=final_clock_s,
            input_receipt_clock_s=input_receipt_clock_s,
            input_delivery_duration_s=input_delivery_duration_s,
            terminal_reason=CameraNorthBootstrapTerminalReason.OBSERVATION_EXCEPTION,
            detail="Post-bootstrap V2 guidance failed closed.",
            error=error,
        )
    return _result(
        initial=initial,
        guidance=guidance,
        plan=plan,
        arm=arm,
        arm_guard=arm_guard,
        commit=commit,
        commit_guard=commit_guard,
        decision_commit_guard=decision_commit_guard,
        arm_age=age,
        receipt=receipt,
        post=post,
        post_guidance=post_guidance,
        input_start_clock_s=final_clock_s,
        input_receipt_clock_s=input_receipt_clock_s,
        input_delivery_duration_s=input_delivery_duration_s,
        terminal_reason=CameraNorthBootstrapTerminalReason.BOOTSTRAP_EXECUTED,
        detail="One reviewed compass-north primitive executed; recompute from the fresh post frame.",
    )


class _ClockError(ValueError):
    pass


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


def _input_delivery_duration(start: float, receipt: float) -> float:
    if receipt < start:
        raise _ClockError("monotonic clock regressed during input delivery")
    return receipt - start


def _clock_error_age(origin: float) -> CameraServoArmAgeEvidence:
    return CameraServoArmAgeEvidence(
        origin_clock_s=origin,
        final_clock_s=None,
        age_s=None,
        maximum_age_s=MAXIMUM_ARM_TO_INPUT_AGE_SECONDS,
        status=CameraServoArmAgeStatus.FINAL_CLOCK_ERROR,
    )


def _arm_age_not_reached(origin: float) -> CameraServoArmAgeEvidence:
    return CameraServoArmAgeEvidence(
        origin_clock_s=origin,
        final_clock_s=None,
        age_s=None,
        maximum_age_s=MAXIMUM_ARM_TO_INPUT_AGE_SECONDS,
        status=CameraServoArmAgeStatus.NOT_REACHED,
    )


def _strictly_newer(before: Frame, after: Frame) -> bool:
    return (
        after.frame_id > before.frame_id
        and after.captured_monotonic_s > before.captured_monotonic_s
    )


def _capture_evidence(
    source: CameraFrameSource,
    recorder: CameraArtifactRecorder,
    label: str,
) -> tuple[Frame, CameraServoFrameEvidence]:
    frame = source.capture()
    return frame, _evaluate_captured_frame(frame, recorder, label)


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


class _PreparedCameraControl:
    """Replay one completed preflight while retaining runner execution safety."""

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
        self._input_attempted = True
        return self._control.key_down(key)

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


def _retains_complete_input_seam(result: CameraNorthBootstrapResult) -> bool:
    """Return whether evidence proves execution reached input delegation."""

    guards = (
        result.arm_guard,
        result.commit_guard,
        result.decision_commit_guard,
    )
    frames = (result.initial, result.arm, result.commit)
    return (
        result.guidance is not None
        and result.plan is not None
        and all(frame is not None for frame in frames)
        and all(guard is not None for guard in guards)
        and result.arm_age is not None
        and result.arm_age.status is CameraServoArmAgeStatus.WITHIN_LIMIT
        and result.preflight is not None
        and result.preflight.supported
        and all(
            guard is not None
            and guard.disposition is CameraArmGuardDisposition.RETAIN
            for guard in guards
        )
        and all(
            frame is not None and _frame_terminal(frame) is None
            for frame in frames
        )
    )


def _evidence_strictly_newer(
    before: CameraServoFrameEvidence,
    after: CameraServoFrameEvidence,
) -> bool:
    return (
        after.artifact.frame_id > before.artifact.frame_id
        and after.captured_monotonic_s > before.captured_monotonic_s
    )


def _guidance_binds(
    guidance: WorldCameraGuidanceV2,
    evidence: CameraServoFrameEvidence,
) -> bool:
    return (
        guidance.decision_frame_id == evidence.artifact.frame_id
        and guidance.decision_captured_monotonic_s
        == evidence.captured_monotonic_s
        and guidance.decision_raw_sha256 == evidence.artifact.raw_sha256
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
) -> tuple[CameraNorthBootstrapTerminalReason, str] | None:
    if not evidence.readiness.safe_to_attempt_camera_input:
        return (
            CameraNorthBootstrapTerminalReason.READINESS_LOST,
            "Gameplay readiness vetoed camera input.",
        )
    production = evidence.production
    assert production is not None
    if production.passed:
        return (
            CameraNorthBootstrapTerminalReason.PRODUCTION_PASS,
            "The unchanged production evaluator passed.",
        )
    if not _is_fail_closed(production):
        return (
            CameraNorthBootstrapTerminalReason.PRODUCTION_REJECTION_NOT_FAIL_CLOSED,
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


def _exception(error: Exception | None) -> CameraServoExceptionEvidence | None:
    if error is None:
        return None
    return CameraServoExceptionEvidence(type(error).__name__, str(error))


def _result(
    *,
    terminal_reason: CameraNorthBootstrapTerminalReason,
    detail: str,
    initial: CameraServoFrameEvidence | None = None,
    guidance: WorldCameraGuidanceV2 | None = None,
    plan: CameraPlan | None = None,
    arm: CameraServoFrameEvidence | None = None,
    arm_guard: CameraArmGuardResult | None = None,
    commit: CameraServoFrameEvidence | None = None,
    commit_guard: CameraArmGuardResult | None = None,
    decision_commit_guard: CameraArmGuardResult | None = None,
    arm_age: CameraServoArmAgeEvidence | None = None,
    preflight: CameraPreflightReceipt | None = None,
    receipt: CameraPlanReceipt | None = None,
    post: CameraServoFrameEvidence | None = None,
    post_guidance: WorldCameraGuidanceV2 | None = None,
    input_state: CameraNorthBootstrapInputState | None = None,
    input_start_clock_s: float | None = None,
    input_receipt_clock_s: float | None = None,
    input_delivery_duration_s: float | None = None,
    error: Exception | None = None,
) -> CameraNorthBootstrapResult:
    resolved_preflight = receipt.preflight if receipt is not None else preflight
    resolved_input_state = (
        CameraNorthBootstrapInputState.COMPLETE
        if receipt is not None
        else CameraNorthBootstrapInputState.NONE
        if input_state is None
        else input_state
    )
    return CameraNorthBootstrapResult(
        initial=initial,
        guidance=guidance,
        plan=plan,
        arm=arm,
        arm_guard=arm_guard,
        commit=commit,
        commit_guard=commit_guard,
        decision_commit_guard=decision_commit_guard,
        arm_age=arm_age,
        preflight=resolved_preflight,
        receipt=receipt,
        post=post,
        post_guidance=post_guidance,
        terminal_reason=terminal_reason,
        detail=detail,
        input_state=resolved_input_state,
        input_start_clock_s=input_start_clock_s,
        input_receipt_clock_s=input_receipt_clock_s,
        input_delivery_duration_s=input_delivery_duration_s,
        exception=_exception(error),
    )
