"""Bounded, production-gated camera servo for Issue #31 validation.

This development-only state machine is intentionally narrower than a general
camera controller.  The first reviewed phase can emit only one wheel detent at
a time, and only when world-only diagnostics coherently identify zoom as the
single actionable axis.  Diagnostics never establish success: the unchanged
production camera evaluator is the sole acceptance authority.
"""

from __future__ import annotations

import hashlib
import math
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from ..capture import Frame
from ..perception.production_profiles import load_varrock_east_iron_profile
from ..perception.resource import ResourceVisualState
from ..perception.scene_landmarks import MacroZone, describe_region, descriptor_distance
from .camera_arm_guard import (
    CameraArmGuardDisposition,
    CameraArmGuardReason,
    CameraArmGuardResult,
    evaluate_camera_arm_guard,
)
from .camera_evaluation import CameraEvaluation, evaluate_varrock_east_camera
from .camera_guidance import (
    CameraGuidanceAxis,
    CameraGuidanceDirection,
    CameraGuidanceDisposition,
    WorldCameraGuidance,
    evaluate_varrock_east_camera_guidance,
)
from .camera_plan import (
    REVIEWED_CAMERA_WHEEL_POINT,
    CameraControl,
    CameraInputReceipt,
    CameraPlan,
    CameraPlanReceipt,
    CameraPlanRunner,
    CameraPreflightReceipt,
    CameraWheel,
    Sleeper,
)
from .camera_session import (
    CameraArtifactRecorder,
    CameraFrameArtifact,
    CameraFrameSource,
    record_frame_digest,
)
from .client_readiness import ClientInputReadiness, evaluate_client_input_readiness

__all__ = [
    "ABSOLUTE_MAX_SERVO_ARM_ATTEMPTS",
    "ABSOLUTE_MAX_SERVO_ELAPSED_SECONDS",
    "ABSOLUTE_MAX_SERVO_PRIMITIVES",
    "DEFAULT_MAX_SERVO_PRIMITIVES",
    "MAXIMUM_ARM_TO_INPUT_AGE_SECONDS",
    "CameraServoArmAgeEvidence",
    "CameraServoArmAgeStatus",
    "CameraServoExceptionEvidence",
    "CameraServoArmEvidence",
    "CameraServoArmOutcome",
    "CameraServoCommitEvidence",
    "CameraServoCommitOutcome",
    "CameraServoFrameEvidence",
    "CameraServoLimits",
    "CameraServoProgress",
    "CameraServoProgressStatus",
    "CameraServoResult",
    "CameraServoStep",
    "CameraServoTerminalReason",
    "WorldLandmarkEffect",
    "WorldLandmarkEffectItem",
    "measure_world_landmark_effect",
    "run_bounded_camera_servo",
]

DEFAULT_MAX_SERVO_PRIMITIVES: Final[int] = 8
ABSOLUTE_MAX_SERVO_PRIMITIVES: Final[int] = 8
DEFAULT_MAX_SERVO_ELAPSED_SECONDS: Final[float] = 30.0
ABSOLUTE_MAX_SERVO_ELAPSED_SECONDS: Final[float] = 120.0
ABSOLUTE_MAX_SERVO_ARM_ATTEMPTS: Final[int] = 16
MAXIMUM_SERVO_SETTLE_SECONDS: Final[float] = 10.0
WORLD_EFFECT_DESCRIPTOR_EPSILON: Final[float] = 0.001
WORLD_EFFECT_REQUIRED_LANDMARKS: Final[int] = 3
WORLD_EFFECT_REQUIRED_ZONES: Final[int] = 3
ZOOM_ERROR_PROGRESS_TOLERANCE: Final[float] = 0.0005
MAXIMUM_CONSECUTIVE_STAGNANT_STEPS: Final[int] = 2
MAXIMUM_ARM_TO_INPUT_AGE_SECONDS: Final[float] = 1.0


class CameraServoTerminalReason(StrEnum):
    """Stable terminal reason for a bounded servo attempt."""

    PRODUCTION_PASS = "production_pass"
    READINESS_LOST = "readiness_lost"
    PRODUCTION_REJECTION_NOT_FAIL_CLOSED = "production_rejection_not_fail_closed"
    INSUFFICIENT_GUIDANCE = "insufficient_guidance"
    UNSAFE_GUIDANCE = "unsafe_guidance"
    INPUT_EXCEPTION = "input_exception"
    SETTLE_EXCEPTION = "settle_exception"
    OBSERVATION_EXCEPTION = "observation_exception"
    NO_EFFECT_STAGNATION = "no_effect_stagnation"
    WORSENING_GUIDANCE = "worsening_guidance"
    REPEATED_STATE = "repeated_state"
    OSCILLATION = "oscillation"
    PRIMITIVE_BUDGET_EXHAUSTED = "primitive_budget_exhausted"
    TIME_BUDGET_EXHAUSTED = "time_budget_exhausted"
    CLOCK_ERROR = "clock_error"
    ARM_ATTEMPT_BUDGET_EXHAUSTED = "arm_attempt_budget_exhausted"
    ARM_FRESHNESS_EXPIRED = "arm_freshness_expired"
    COMMIT_OBSERVATION_REJECTED = "commit_observation_rejected"


class CameraServoArmOutcome(StrEnum):
    """Recorded outcome of the dedicated, zero-authority pre-input seam."""

    RETAINED = "retained"
    STALE_DISCARDED_RESTART = "stale_discarded_restart"
    NON_FRESH_STOP = "non_fresh_stop"
    READINESS_LOST = "readiness_lost"
    PRODUCTION_PASS = "production_pass"
    PRODUCTION_REJECTION_NOT_FAIL_CLOSED = "production_rejection_not_fail_closed"
    DEADLINE_EXHAUSTED = "deadline_exhausted"
    CLOCK_ERROR = "clock_error"
    EVALUATION_ERROR = "evaluation_error"
    GUARD_ERROR = "guard_error"
    REPEATED_STATE_STOP = "repeated_state_stop"
    ARM_FRESHNESS_EXPIRED = "arm_freshness_expired"
    COMMIT_STOP = "commit_stop"


class CameraServoArmAgeStatus(StrEnum):
    """Independent injected-clock status from arm recording to input."""

    NOT_REACHED = "not_reached"
    WITHIN_LIMIT = "within_limit"
    EXPIRED = "expired"
    ORIGIN_CLOCK_ERROR = "origin_clock_error"
    FINAL_CLOCK_ERROR = "final_clock_error"


class CameraServoCommitOutcome(StrEnum):
    """Zero-authority verdict for the final visual commit observation."""

    RETAINED = "retained"
    NON_FRESH_STOP = "non_fresh_stop"
    READINESS_LOST = "readiness_lost"
    GUARD_REJECTED = "guard_rejected"
    EVALUATION_ERROR = "evaluation_error"
    GUARD_ERROR = "guard_error"


@dataclass(frozen=True, slots=True)
class CameraServoLimits:
    """Per-run limits nested inside hard, non-configurable absolute bounds."""

    max_primitives: int = DEFAULT_MAX_SERVO_PRIMITIVES
    max_elapsed_s: float = DEFAULT_MAX_SERVO_ELAPSED_SECONDS

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_primitives, bool)
            or not isinstance(self.max_primitives, int)
            or not 1 <= self.max_primitives <= ABSOLUTE_MAX_SERVO_PRIMITIVES
        ):
            raise ValueError(
                "max_primitives must be an integer between 1 and "
                f"{ABSOLUTE_MAX_SERVO_PRIMITIVES}"
            )
        if (
            isinstance(self.max_elapsed_s, bool)
            or not isinstance(self.max_elapsed_s, (int, float))
            or not math.isfinite(self.max_elapsed_s)
            or not 0.0 < self.max_elapsed_s <= ABSOLUTE_MAX_SERVO_ELAPSED_SECONDS
        ):
            raise ValueError(
                "max_elapsed_s must be finite and in (0, "
                f"{ABSOLUTE_MAX_SERVO_ELAPSED_SECONDS}]"
            )


DEFAULT_CAMERA_SERVO_LIMITS: Final[CameraServoLimits] = CameraServoLimits()


class CameraServoProgressStatus(StrEnum):
    """Whether one pulse materially reduced the frozen zoom-error score."""

    IMPROVED = "improved"
    STAGNANT = "stagnant"
    WORSENED = "worsened"


@dataclass(frozen=True, slots=True)
class CameraServoProgress:
    """Immutable monotonic-progress evidence between actionable guidance fits."""

    before_absolute_log_scale_error: float
    after_absolute_log_scale_error: float
    error_decrease: float
    tolerance: float
    status: CameraServoProgressStatus

    def __post_init__(self) -> None:
        for name, value in (
            ("before_absolute_log_scale_error", self.before_absolute_log_scale_error),
            ("after_absolute_log_scale_error", self.after_absolute_log_scale_error),
        ):
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and non-negative")
        if not math.isclose(
            self.error_decrease,
            self.before_absolute_log_scale_error - self.after_absolute_log_scale_error,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError("progress decrease must match before/after evidence")
        if self.tolerance != ZOOM_ERROR_PROGRESS_TOLERANCE:
            raise ValueError("progress must record the frozen zoom-error tolerance")
        expected = _progress_status(self.error_decrease)
        if self.status is not expected:
            raise ValueError("progress status must match the frozen tolerance policy")


@dataclass(frozen=True, slots=True)
class CameraServoExceptionEvidence:
    """Serializable exception identity without retaining a traceback or frame."""

    exception_type: str
    detail: str

    def __post_init__(self) -> None:
        if not self.exception_type:
            raise ValueError("exception_type must not be empty")


@dataclass(frozen=True, slots=True)
class CameraServoFrameEvidence:
    """Recorded frame identity followed by veto and production evidence."""

    artifact: CameraFrameArtifact
    captured_monotonic_s: float
    readiness: ClientInputReadiness
    production: CameraEvaluation | None

    def __post_init__(self) -> None:
        if (
            isinstance(self.captured_monotonic_s, bool)
            or not isinstance(self.captured_monotonic_s, (int, float))
            or not math.isfinite(self.captured_monotonic_s)
            or self.captured_monotonic_s < 0.0
        ):
            raise ValueError("captured_monotonic_s must be finite and non-negative")
        if self.readiness.safe_to_attempt_camera_input is (self.production is None):
            raise ValueError(
                "production evidence is required exactly when readiness permits evaluation"
            )


@dataclass(frozen=True, slots=True)
class CameraServoArmAgeEvidence:
    """Independent injected-clock freshness evidence for one arm attempt."""

    origin_clock_s: float | None
    final_clock_s: float | None
    age_s: float | None
    maximum_age_s: float
    status: CameraServoArmAgeStatus

    def __post_init__(self) -> None:
        if self.maximum_age_s != MAXIMUM_ARM_TO_INPUT_AGE_SECONDS:
            raise ValueError("arm age evidence must use the frozen maximum")
        for name, value in (
            ("origin_clock_s", self.origin_clock_s),
            ("final_clock_s", self.final_clock_s),
            ("age_s", self.age_s),
        ):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0.0
            ):
                raise ValueError(f"{name} must be finite and non-negative or None")
        if self.status is CameraServoArmAgeStatus.ORIGIN_CLOCK_ERROR:
            if any(
                value is not None
                for value in (self.origin_clock_s, self.final_clock_s, self.age_s)
            ):
                raise ValueError("origin clock error cannot retain numeric age evidence")
            return
        if self.origin_clock_s is None:
            raise ValueError("arm age evidence requires a valid origin clock")
        if self.status in (
            CameraServoArmAgeStatus.NOT_REACHED,
            CameraServoArmAgeStatus.FINAL_CLOCK_ERROR,
        ):
            if self.final_clock_s is not None or self.age_s is not None:
                raise ValueError("unfinished arm age evidence cannot retain a final sample")
            return
        if self.final_clock_s is None or self.age_s is None:
            raise ValueError("completed arm age evidence requires both clock samples")
        if not math.isclose(
            self.age_s,
            self.final_clock_s - self.origin_clock_s,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError("arm age must match the exact injected-clock samples")
        expected = (
            CameraServoArmAgeStatus.EXPIRED
            if self.age_s >= self.maximum_age_s
            else CameraServoArmAgeStatus.WITHIN_LIMIT
        )
        if self.status is not expected:
            raise ValueError("arm age status must match the exclusive freshness limit")


@dataclass(frozen=True, slots=True)
class CameraServoCommitEvidence:
    """Fresh no-production observation committed immediately before input."""

    accepted_arm_artifact: CameraFrameArtifact
    accepted_arm_captured_monotonic_s: float
    artifact: CameraFrameArtifact
    captured_monotonic_s: float
    readiness: ClientInputReadiness | None
    guard: CameraArmGuardResult | None
    outcome: CameraServoCommitOutcome
    exception: CameraServoExceptionEvidence | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("accepted_arm_captured_monotonic_s", self.accepted_arm_captured_monotonic_s),
            ("captured_monotonic_s", self.captured_monotonic_s),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0.0
            ):
                raise ValueError(f"commit {name} must be finite and non-negative")
        fresh = (
            self.artifact.frame_id > self.accepted_arm_artifact.frame_id
            and self.captured_monotonic_s
            > self.accepted_arm_captured_monotonic_s
        )
        recorded_nonfresh = (
            self.outcome is CameraServoCommitOutcome.NON_FRESH_STOP
            and self.guard is not None
            and self.guard.reason is CameraArmGuardReason.NON_FRESH_ARM_FRAME
        )
        recorded_nonfresh_guard_error = (
            self.outcome is CameraServoCommitOutcome.GUARD_ERROR
            and self.readiness is None
            and self.guard is None
            and self.exception is not None
        )
        if not fresh and not (
            recorded_nonfresh or recorded_nonfresh_guard_error
        ):
            raise ValueError("non-fresh commit requires the guard's non-fresh stop")
        error_outcomes = (
            CameraServoCommitOutcome.EVALUATION_ERROR,
            CameraServoCommitOutcome.GUARD_ERROR,
        )
        if (self.outcome in error_outcomes) is (self.exception is None):
            raise ValueError("commit exception must exactly match an error outcome")
        if self.guard is not None:
            if (
                self.guard.decision_frame_id != self.accepted_arm_artifact.frame_id
                or self.guard.decision_captured_monotonic_s
                != self.accepted_arm_captured_monotonic_s
                or self.guard.decision_payload_sha256
                != self.accepted_arm_artifact.raw_sha256
                or self.guard.arm_frame_id != self.artifact.frame_id
                or self.guard.arm_captured_monotonic_s != self.captured_monotonic_s
                or self.guard.arm_payload_sha256 != self.artifact.raw_sha256
            ):
                raise ValueError("commit guard must bind the exact arm/commit frame pair")
        if self.readiness is None:
            if self.outcome not in (
                CameraServoCommitOutcome.NON_FRESH_STOP,
                CameraServoCommitOutcome.EVALUATION_ERROR,
                CameraServoCommitOutcome.GUARD_ERROR,
            ):
                raise ValueError("missing commit readiness requires an early stop")
            return
        if not self.readiness.safe_to_attempt_camera_input:
            if self.outcome is not CameraServoCommitOutcome.READINESS_LOST:
                raise ValueError("commit readiness veto requires readiness-lost outcome")
            if self.guard is not None:
                raise ValueError("commit readiness veto must stop before its guard")
            return
        if self.outcome is CameraServoCommitOutcome.GUARD_ERROR:
            if self.guard is not None:
                raise ValueError("commit guard error cannot contain a guard result")
            return
        if self.guard is None:
            raise ValueError("ready commit evidence requires a guard result")
        if self.outcome is CameraServoCommitOutcome.RETAINED:
            if self.guard.disposition is not CameraArmGuardDisposition.RETAIN:
                raise ValueError("retained commit requires an unchanged-world guard")
        elif self.outcome is CameraServoCommitOutcome.GUARD_REJECTED:
            if self.guard.disposition is not CameraArmGuardDisposition.DISCARD_RESTART:
                raise ValueError("rejected commit requires a guard discard")
        elif self.outcome is not CameraServoCommitOutcome.NON_FRESH_STOP:
            raise ValueError("ready commit outcome does not match its guard evidence")

    @property
    def accepted_arm_raw_sha256(self) -> str:
        """Return the exact accepted-arm payload identity."""

        return self.accepted_arm_artifact.raw_sha256

    @property
    def raw_sha256(self) -> str:
        """Return the exact final commit payload identity."""

        return self.artifact.raw_sha256


@dataclass(frozen=True, slots=True)
class CameraServoArmEvidence:
    """Immutable decision-to-arm evidence immediately preceding possible input."""

    cycle_index: int
    pending_primitive_index: int
    decision: CameraServoFrameEvidence
    guidance: WorldCameraGuidance
    pending_primitive: CameraPlan
    arm_artifact: CameraFrameArtifact
    arm_captured_monotonic_s: float
    readiness: ClientInputReadiness | None
    production: CameraEvaluation | None
    guard: CameraArmGuardResult | None
    age: CameraServoArmAgeEvidence
    commit: CameraServoCommitEvidence | None
    outcome: CameraServoArmOutcome
    exception: CameraServoExceptionEvidence | None = None

    def __post_init__(self) -> None:
        for name, index in (
            ("cycle_index", self.cycle_index),
            ("pending_primitive_index", self.pending_primitive_index),
        ):
            if isinstance(index, bool) or not isinstance(index, int) or index <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if len(self.pending_primitive.actions) != 1 or not isinstance(
            self.pending_primitive.actions[0], CameraWheel
        ):
            raise ValueError("arm evidence must bind exactly one pending wheel primitive")
        wheel = self.pending_primitive.actions[0]
        if abs(wheel.detents) != 1:
            raise ValueError("arm evidence pending wheel must contain one detent")
        if self.guidance.direction is None:
            raise ValueError("arm evidence requires an exact pending guidance sign")
        expected_detents = (
            1
            if self.guidance.direction is CameraGuidanceDirection.POSITIVE
            else -1
        )
        if wheel.detents != expected_detents:
            raise ValueError("pending primitive must match the recorded guidance sign")
        expected_name = (
            f"issue31-servo-{self.pending_primitive_index:02d}-zoom-"
            f"{self.guidance.direction.value}"
        )
        if self.pending_primitive.name != expected_name:
            raise ValueError("pending primitive name must match its recorded index and sign")
        if (
            isinstance(self.arm_captured_monotonic_s, bool)
            or not isinstance(self.arm_captured_monotonic_s, (int, float))
            or not math.isfinite(self.arm_captured_monotonic_s)
            or self.arm_captured_monotonic_s < 0.0
        ):
            raise ValueError("arm_captured_monotonic_s must be finite")
        fresh = (
            self.arm_artifact.frame_id > self.decision.artifact.frame_id
            and self.arm_captured_monotonic_s > self.decision.captured_monotonic_s
        )
        recorded_nonfresh_discard = (
            self.outcome is CameraServoArmOutcome.NON_FRESH_STOP
            and self.guard is not None
            and self.guard.reason is CameraArmGuardReason.NON_FRESH_ARM_FRAME
        )
        recorded_origin_clock_stop = (
            self.outcome is CameraServoArmOutcome.CLOCK_ERROR
            and self.age.status is CameraServoArmAgeStatus.ORIGIN_CLOCK_ERROR
            and self.guard is None
        )
        if not fresh and not (
            recorded_nonfresh_discard or recorded_origin_clock_stop
        ):
            raise ValueError(
                "non-fresh arm capture requires the guard's non-fresh discard"
            )
        error_outcomes = (
            CameraServoArmOutcome.CLOCK_ERROR,
            CameraServoArmOutcome.EVALUATION_ERROR,
            CameraServoArmOutcome.GUARD_ERROR,
        )
        if (self.outcome in error_outcomes) is (self.exception is None):
            raise ValueError("arm exception evidence must exactly match an error outcome")
        if self.age.status in (
            CameraServoArmAgeStatus.ORIGIN_CLOCK_ERROR,
            CameraServoArmAgeStatus.FINAL_CLOCK_ERROR,
        ):
            if self.outcome is not CameraServoArmOutcome.CLOCK_ERROR:
                raise ValueError("arm clock error requires a typed clock-error outcome")
        elif self.age.status is CameraServoArmAgeStatus.EXPIRED:
            if self.outcome is not CameraServoArmOutcome.ARM_FRESHNESS_EXPIRED:
                raise ValueError("expired arm age requires a typed expiry outcome")
        elif self.outcome is CameraServoArmOutcome.ARM_FRESHNESS_EXPIRED:
            raise ValueError("arm expiry outcome requires expired age evidence")
        if self.outcome is CameraServoArmOutcome.RETAINED:
            if self.age.status is not CameraServoArmAgeStatus.WITHIN_LIMIT:
                raise ValueError("retained arm requires a fresh injected-clock age")
            if (
                self.commit is None
                or self.commit.outcome is not CameraServoCommitOutcome.RETAINED
            ):
                raise ValueError("retained arm requires a retained final commit")
        if self.outcome is CameraServoArmOutcome.COMMIT_STOP:
            if (
                self.commit is None
                or self.commit.outcome is CameraServoCommitOutcome.RETAINED
            ):
                raise ValueError("commit stop requires rejected commit evidence")
        elif self.commit is not None and (
            self.commit.outcome is not CameraServoCommitOutcome.RETAINED
        ):
            raise ValueError("a rejected commit can only produce a commit stop")
        if self.commit is not None and (
            self.commit.accepted_arm_artifact != self.arm_artifact
            or self.commit.accepted_arm_captured_monotonic_s
            != self.arm_captured_monotonic_s
        ):
            raise ValueError("commit evidence must bind this exact accepted arm")

        guard_outcome = self.outcome in (
            CameraServoArmOutcome.RETAINED,
            CameraServoArmOutcome.STALE_DISCARDED_RESTART,
            CameraServoArmOutcome.NON_FRESH_STOP,
            CameraServoArmOutcome.REPEATED_STATE_STOP,
            CameraServoArmOutcome.ARM_FRESHNESS_EXPIRED,
            CameraServoArmOutcome.COMMIT_STOP,
        )
        if self.guard is None and guard_outcome:
            raise ValueError("guard evidence is required for a post-guard outcome")
        if self.guard is not None and self.outcome not in (
            CameraServoArmOutcome.RETAINED,
            CameraServoArmOutcome.STALE_DISCARDED_RESTART,
            CameraServoArmOutcome.NON_FRESH_STOP,
            CameraServoArmOutcome.DEADLINE_EXHAUSTED,
            CameraServoArmOutcome.CLOCK_ERROR,
            CameraServoArmOutcome.EVALUATION_ERROR,
            CameraServoArmOutcome.REPEATED_STATE_STOP,
            CameraServoArmOutcome.ARM_FRESHNESS_EXPIRED,
            CameraServoArmOutcome.COMMIT_STOP,
        ):
            raise ValueError("guard evidence is forbidden before the guard stage")
        if self.guard is not None:
            if (
                self.guard.decision_frame_id != self.decision.artifact.frame_id
                or self.guard.decision_captured_monotonic_s
                != self.decision.captured_monotonic_s
                or self.guard.decision_payload_sha256
                != self.decision.artifact.raw_sha256
                or self.guard.arm_frame_id != self.arm_artifact.frame_id
                or self.guard.arm_captured_monotonic_s
                != self.arm_captured_monotonic_s
                or self.guard.arm_payload_sha256 != self.arm_artifact.raw_sha256
            ):
                raise ValueError("arm guard must bind the exact decision/arm frame pair")
            expected_outcomes = (
                (
                    CameraServoArmOutcome.RETAINED,
                    CameraServoArmOutcome.DEADLINE_EXHAUSTED,
                    CameraServoArmOutcome.CLOCK_ERROR,
                    CameraServoArmOutcome.EVALUATION_ERROR,
                    CameraServoArmOutcome.REPEATED_STATE_STOP,
                    CameraServoArmOutcome.ARM_FRESHNESS_EXPIRED,
                    CameraServoArmOutcome.COMMIT_STOP,
                )
                if self.guard.disposition is CameraArmGuardDisposition.RETAIN
                else (
                    CameraServoArmOutcome.STALE_DISCARDED_RESTART,
                    CameraServoArmOutcome.NON_FRESH_STOP,
                    CameraServoArmOutcome.DEADLINE_EXHAUSTED,
                    CameraServoArmOutcome.CLOCK_ERROR,
                    CameraServoArmOutcome.EVALUATION_ERROR,
                    CameraServoArmOutcome.REPEATED_STATE_STOP,
                )
            )
            if self.outcome not in expected_outcomes:
                raise ValueError("arm outcome must match the guard disposition")
            if self.guard.safe_to_retain_guidance is (
                self.guard.disposition is not CameraArmGuardDisposition.RETAIN
            ):
                raise ValueError("arm retention must match the guard safety verdict")

        if self.readiness is None:
            allowed_without_readiness = (
                CameraServoArmOutcome.EVALUATION_ERROR,
                CameraServoArmOutcome.GUARD_ERROR,
                CameraServoArmOutcome.STALE_DISCARDED_RESTART,
                CameraServoArmOutcome.NON_FRESH_STOP,
                CameraServoArmOutcome.CLOCK_ERROR,
            )
            if self.outcome not in allowed_without_readiness:
                raise ValueError("missing readiness requires a pre-readiness stop outcome")
            if self.production is not None:
                raise ValueError("production cannot exist without readiness evidence")
            if (
                self.outcome is CameraServoArmOutcome.NON_FRESH_STOP
            ) is (self.exception is not None):
                raise ValueError("pre-readiness stale discard cannot carry an exception")
            return

        ready = self.readiness.safe_to_attempt_camera_input
        if self.outcome is CameraServoArmOutcome.READINESS_LOST and ready:
            raise ValueError("readiness-lost arm evidence must contain a readiness veto")
        if not ready and self.outcome is not CameraServoArmOutcome.READINESS_LOST:
            raise ValueError("a readiness-vetoed arm frame cannot have another outcome")
        if ready:
            production = self.production
            if production is None:
                if (
                    self.outcome is not CameraServoArmOutcome.EVALUATION_ERROR
                    or self.exception is None
                ):
                    raise ValueError("missing production requires retained error evidence")
                return
            if self.outcome is CameraServoArmOutcome.PRODUCTION_PASS:
                if not production.passed:
                    raise ValueError("production-pass arm evidence must contain a pass")
            elif production.passed and self.outcome not in (
                CameraServoArmOutcome.DEADLINE_EXHAUSTED,
                CameraServoArmOutcome.CLOCK_ERROR,
            ):
                raise ValueError("a passing arm frame cannot retain or discard guidance")
            if self.outcome is CameraServoArmOutcome.PRODUCTION_REJECTION_NOT_FAIL_CLOSED:
                if _is_fail_closed_production_rejection(production):
                    raise ValueError("unsafe production arm outcome requires unsafe evidence")
            elif (
                self.outcome
                not in (
                    CameraServoArmOutcome.PRODUCTION_PASS,
                    CameraServoArmOutcome.GUARD_ERROR,
                    CameraServoArmOutcome.DEADLINE_EXHAUSTED,
                    CameraServoArmOutcome.CLOCK_ERROR,
                )
                and not _is_fail_closed_production_rejection(production)
            ):
                raise ValueError("guidance may continue only from fail-closed production")

    @property
    def decision_raw_sha256(self) -> str:
        """Return the exact recorded decision-frame payload identity."""

        return self.decision.artifact.raw_sha256

    @property
    def decision_captured_monotonic_s(self) -> float:
        """Return the source timestamp attached to the decision frame."""

        return self.decision.captured_monotonic_s

    @property
    def arm_raw_sha256(self) -> str:
        """Return the exact recorded arm-frame payload identity."""

        return self.arm_artifact.raw_sha256


@dataclass(frozen=True, slots=True)
class WorldLandmarkEffectItem:
    """Observed structural change inside one frozen world landmark region."""

    landmark_id: str
    zone: MacroZone
    descriptor_distance: float
    changed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.landmark_id, str) or not self.landmark_id.strip():
            raise ValueError("effect landmark_id must not be empty")
        if not isinstance(self.zone, MacroZone):
            raise ValueError("effect landmark zone must be MacroZone")
        if (
            isinstance(self.descriptor_distance, bool)
            or not isinstance(self.descriptor_distance, (int, float))
            or not math.isfinite(self.descriptor_distance)
            or self.descriptor_distance < 0.0
        ):
            raise ValueError("effect descriptor distance must be finite and non-negative")
        if self.changed is not (
            self.descriptor_distance >= WORLD_EFFECT_DESCRIPTOR_EPSILON
        ):
            raise ValueError("effect item verdict must match the descriptor epsilon")


@dataclass(frozen=True, slots=True)
class WorldLandmarkEffect:
    """Resource-independent observed-effect evidence from world regions only."""

    landmarks: tuple[WorldLandmarkEffectItem, ...]
    mean_descriptor_distance: float
    maximum_descriptor_distance: float
    changed_landmark_count: int
    changed_zones: tuple[MacroZone, ...]
    effect_observed: bool

    def __post_init__(self) -> None:
        if not self.landmarks:
            raise ValueError("world effect requires frozen landmark evidence")
        if self.changed_landmark_count != sum(item.changed for item in self.landmarks):
            raise ValueError("changed landmark count must match item evidence")
        expected_zones = tuple(
            zone
            for zone in MacroZone
            if any(item.changed and item.zone is zone for item in self.landmarks)
        )
        if self.changed_zones != expected_zones:
            raise ValueError("changed zones must match item evidence")
        distances = tuple(item.descriptor_distance for item in self.landmarks)
        expected_mean = sum(distances) / len(distances)
        if not math.isclose(
            self.mean_descriptor_distance, expected_mean, rel_tol=0.0, abs_tol=1e-12
        ):
            raise ValueError("mean descriptor distance must match item evidence")
        if not math.isclose(
            self.maximum_descriptor_distance,
            max(distances),
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError("maximum descriptor distance must match item evidence")
        expected_effect = (
            self.changed_landmark_count >= WORLD_EFFECT_REQUIRED_LANDMARKS
            and len(self.changed_zones) >= WORLD_EFFECT_REQUIRED_ZONES
        )
        if self.effect_observed is not expected_effect:
            raise ValueError("effect verdict must match the frozen landmark policy")


@dataclass(frozen=True, slots=True)
class CameraServoStep:
    """Immutable evidence for exactly one bounded primitive attempt."""

    index: int
    pre: CameraServoFrameEvidence
    arm: CameraServoArmEvidence
    guidance: WorldCameraGuidance
    primitive: CameraPlan
    receipt: CameraPlanReceipt | None
    post: CameraServoFrameEvidence | None
    post_guidance: WorldCameraGuidance | None
    effect: WorldLandmarkEffect | None
    elapsed_s: float
    pre_world_state_digest: str
    post_world_state_digest: str | None
    stagnant_steps_before: int
    stagnant_steps_after: int
    direction_reversed: bool
    progress: CameraServoProgress | None = None
    exception: CameraServoExceptionEvidence | None = None

    def __post_init__(self) -> None:
        if isinstance(self.index, bool) or not isinstance(self.index, int) or self.index <= 0:
            raise ValueError("servo step index must be a positive integer")
        if self.arm.outcome is not CameraServoArmOutcome.RETAINED:
            raise ValueError("a servo step requires retained pre-input arm evidence")
        if self.arm.pending_primitive_index != self.index:
            raise ValueError("servo step index must match its arm evidence")
        if self.arm.pending_primitive != self.primitive:
            raise ValueError("servo step primitive must match its armed primitive")
        if self.arm.guidance != self.guidance:
            raise ValueError("servo step guidance must match its armed guidance")
        if self.arm.arm_artifact != self.pre.artifact:
            raise ValueError("servo step pre evidence must be its exact arm capture")
        if self.arm.arm_captured_monotonic_s != self.pre.captured_monotonic_s:
            raise ValueError("servo step pre timestamp must be its exact arm capture")
        if len(self.primitive.actions) != 1 or not isinstance(
            self.primitive.actions[0], CameraWheel
        ):
            raise ValueError("a servo step may contain exactly one wheel primitive")
        action = self.primitive.actions[0]
        if abs(action.detents) != 1:
            raise ValueError("a servo wheel primitive must contain exactly one detent")
        if self.receipt is not None and self.receipt.plan != self.primitive:
            raise ValueError("servo receipt must match the exact primitive")
        if self.effect is not None and self.post is None:
            raise ValueError("observed effect requires post-action frame evidence")
        if self.effect is not None and self.post_world_state_digest is None:
            raise ValueError("observed effect requires a post-action world-state digest")
        if self.post_guidance is not None and (
            self.post is None or self.post.production is None
        ):
            raise ValueError("post guidance requires production-evaluated frame evidence")
        if self.progress is not None:
            if self.post_guidance is None:
                raise ValueError("progress requires post-action guidance evidence")
            if self.effect is None:
                raise ValueError("progress requires separate world-effect evidence")
            if self.guidance.direction is not self.post_guidance.direction:
                raise ValueError("progress cannot legitimize a direction reversal")
            before_error = _zoom_error(self.guidance)
            after_error = _zoom_error(self.post_guidance)
            if not math.isclose(
                self.progress.before_absolute_log_scale_error,
                before_error,
                rel_tol=0.0,
                abs_tol=1e-12,
            ) or not math.isclose(
                self.progress.after_absolute_log_scale_error,
                after_error,
                rel_tol=0.0,
                abs_tol=1e-12,
            ):
                raise ValueError("progress must match its exact guidance fits")
        if not math.isfinite(self.elapsed_s) or self.elapsed_s < 0.0:
            raise ValueError("step elapsed_s must be finite and non-negative")
        for name, digest in (
            ("pre_world_state_digest", self.pre_world_state_digest),
            ("post_world_state_digest", self.post_world_state_digest),
        ):
            if digest is not None and (
                len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest or None")
        for name, count in (
            ("stagnant_steps_before", self.stagnant_steps_before),
            ("stagnant_steps_after", self.stagnant_steps_after),
        ):
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.stagnant_steps_after > MAXIMUM_CONSECUTIVE_STAGNANT_STEPS:
            raise ValueError("stagnation evidence exceeds the hard stop count")
        if self.stagnant_steps_before >= MAXIMUM_CONSECUTIVE_STAGNANT_STEPS:
            raise ValueError("a servo step cannot start after the stagnation hard stop")
        if not isinstance(self.direction_reversed, bool):
            raise ValueError("direction_reversed must be a boolean")
        expected_reversal = (
            self.post_guidance is not None
            and self.post_guidance.disposition is CameraGuidanceDisposition.ACTIONABLE
            and self.post_guidance.direction is not self.guidance.direction
        )
        if self.direction_reversed is not expected_reversal:
            raise ValueError("direction reversal flag must exactly match guidance signs")
        if self.progress is not None:
            assert self.effect is not None
            stagnant = (
                not self.effect.effect_observed
                or self.progress.status is CameraServoProgressStatus.STAGNANT
            )
            if self.progress.status is CameraServoProgressStatus.WORSENED:
                expected_after = self.stagnant_steps_before
            elif stagnant:
                expected_after = self.stagnant_steps_before + 1
            else:
                expected_after = 0
            if self.stagnant_steps_after != expected_after:
                raise ValueError("stagnation counter must match effect and progress evidence")
        elif self.stagnant_steps_after != self.stagnant_steps_before:
                raise ValueError("a step without progress cannot change the stagnation counter")


@dataclass(frozen=True, slots=True)
class _CameraServoArmContext:
    """Loop-local fixed inputs used to assemble one arm-attempt record."""

    cycle_index: int
    pending_primitive_index: int
    decision: CameraServoFrameEvidence
    guidance: WorldCameraGuidance
    pending_primitive: CameraPlan
    arm_frame: Frame
    arm_artifact: CameraFrameArtifact

    def evidence(
        self,
        outcome: CameraServoArmOutcome,
        *,
        readiness: ClientInputReadiness | None,
        production: CameraEvaluation | None,
        guard: CameraArmGuardResult | None = None,
        age: CameraServoArmAgeEvidence,
        commit: CameraServoCommitEvidence | None = None,
        error: Exception | None = None,
    ) -> CameraServoArmEvidence:
        return CameraServoArmEvidence(
            cycle_index=self.cycle_index,
            pending_primitive_index=self.pending_primitive_index,
            decision=self.decision,
            guidance=self.guidance,
            pending_primitive=self.pending_primitive,
            arm_artifact=self.arm_artifact,
            arm_captured_monotonic_s=self.arm_frame.captured_monotonic_s,
            readiness=readiness,
            production=production,
            guard=guard,
            age=age,
            commit=commit,
            outcome=outcome,
            exception=_exception_evidence(error) if error is not None else None,
        )


@dataclass(frozen=True, slots=True)
class _CameraServoCommitContext:
    """Loop-local fixed inputs used to assemble one final commit record."""

    accepted_arm_artifact: CameraFrameArtifact
    accepted_arm_captured_monotonic_s: float
    frame: Frame
    artifact: CameraFrameArtifact

    def evidence(
        self,
        outcome: CameraServoCommitOutcome,
        *,
        readiness: ClientInputReadiness | None,
        guard: CameraArmGuardResult | None = None,
        error: Exception | None = None,
    ) -> CameraServoCommitEvidence:
        return CameraServoCommitEvidence(
            accepted_arm_artifact=self.accepted_arm_artifact,
            accepted_arm_captured_monotonic_s=(
                self.accepted_arm_captured_monotonic_s
            ),
            artifact=self.artifact,
            captured_monotonic_s=self.frame.captured_monotonic_s,
            readiness=readiness,
            guard=guard,
            outcome=outcome,
            exception=_exception_evidence(error) if error is not None else None,
        )


@dataclass(slots=True)
class _CameraServoStepBuilder:
    """Mutable loop-local assembly that emits only immutable public evidence."""

    index: int
    pre: CameraServoFrameEvidence
    arm: CameraServoArmEvidence
    guidance: WorldCameraGuidance
    primitive: CameraPlan
    receipt: CameraPlanReceipt | None
    post: CameraServoFrameEvidence
    elapsed_s: float
    pre_world_state_digest: str
    stagnant_steps_before: int
    post_guidance: WorldCameraGuidance | None = None
    effect: WorldLandmarkEffect | None = None
    post_world_state_digest: str | None = None
    progress: CameraServoProgress | None = None

    def build(
        self,
        *,
        stagnant_after: int | None = None,
        reversed_direction: bool = False,
        error: Exception | None = None,
    ) -> CameraServoStep:
        return CameraServoStep(
            index=self.index,
            pre=self.pre,
            arm=self.arm,
            guidance=self.guidance,
            primitive=self.primitive,
            receipt=self.receipt,
            post=self.post,
            post_guidance=self.post_guidance,
            effect=self.effect,
            elapsed_s=self.elapsed_s,
            pre_world_state_digest=self.pre_world_state_digest,
            post_world_state_digest=self.post_world_state_digest,
            stagnant_steps_before=self.stagnant_steps_before,
            stagnant_steps_after=(
                self.stagnant_steps_before
                if stagnant_after is None
                else stagnant_after
            ),
            direction_reversed=reversed_direction,
            progress=self.progress,
            exception=_exception_evidence(error) if error is not None else None,
        )


class _CameraServoSteps(list[CameraServoStep]):
    """Loop-local trace carrying zero-input arm attempts beside input steps."""

    def __init__(self) -> None:
        super().__init__()
        self.arm_attempts: list[CameraServoArmEvidence] = []


@dataclass(frozen=True, slots=True)
class CameraServoResult:
    """Terminal bounded-servo evidence; only a production pass can pass."""

    limits: CameraServoLimits
    settle_s: float
    initial: CameraServoFrameEvidence | None
    arm_attempts: tuple[CameraServoArmEvidence, ...]
    steps: tuple[CameraServoStep, ...]
    final: CameraServoFrameEvidence | None
    final_guidance: WorldCameraGuidance | None
    terminal_reason: CameraServoTerminalReason
    detail: str
    elapsed_s: float
    exception: CameraServoExceptionEvidence | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.steps, tuple):
            raise ValueError("servo steps must be a tuple")
        if not isinstance(self.arm_attempts, tuple):
            raise ValueError("servo arm attempts must be a tuple")
        if len(self.arm_attempts) > ABSOLUTE_MAX_SERVO_ARM_ATTEMPTS:
            raise ValueError("servo evidence exceeds the absolute arm-attempt bound")
        if len(self.steps) > self.limits.max_primitives:
            raise ValueError("servo evidence exceeds the configured primitive budget")
        retained = tuple(
            attempt
            for attempt in self.arm_attempts
            if attempt.outcome is CameraServoArmOutcome.RETAINED
        )
        if tuple(step.arm for step in self.steps) != retained:
            raise ValueError("servo steps must bind retained arm attempts in order")
        if tuple(attempt.cycle_index for attempt in self.arm_attempts) != tuple(
            range(1, len(self.arm_attempts) + 1)
        ):
            raise ValueError("servo arm attempts must have contiguous cycle indexes")
        if not math.isfinite(self.settle_s) or not 0.0 < self.settle_s <= (
            MAXIMUM_SERVO_SETTLE_SECONDS
        ):
            raise ValueError("settle_s is outside the hard servo bound")
        if not math.isfinite(self.elapsed_s) or self.elapsed_s < 0.0:
            raise ValueError("elapsed_s must be finite and non-negative")
        if self.terminal_reason is CameraServoTerminalReason.PRODUCTION_PASS:
            if self.final is None or self.final.production is None:
                raise ValueError("production pass requires final production evidence")
            if not self.final.production.passed:
                raise ValueError("diagnostics cannot create a passing servo result")

    @property
    def passed(self) -> bool:
        """Return only the unchanged final production evaluator's verdict."""

        return (
            self.terminal_reason is CameraServoTerminalReason.PRODUCTION_PASS
            and self.final is not None
            and self.final.production is not None
            and self.final.production.passed
        )


def measure_world_landmark_effect(before: Frame, after: Frame) -> WorldLandmarkEffect:
    """Compare only packaged frozen world-landmark regions across two frames."""

    profile = load_varrock_east_iron_profile()
    for label, frame in (("before", before), ("after", after)):
        if (
            frame.width != profile.frame_width
            or frame.height != profile.frame_height
            or frame.pixel_format is not profile.pixel_format
        ):
            raise ValueError(f"{label} frame does not match the packaged profile geometry")

    items = tuple(
        WorldLandmarkEffectItem(
            landmark_id=landmark.landmark_id,
            zone=landmark.macro_zone,
            descriptor_distance=(
                distance := descriptor_distance(
                    describe_region(before, landmark.region, grid=landmark.grid),
                    describe_region(after, landmark.region, grid=landmark.grid),
                )
            ),
            changed=distance >= WORLD_EFFECT_DESCRIPTOR_EPSILON,
        )
        for landmark in profile.scene_landmarks
    )
    distances = tuple(item.descriptor_distance for item in items)
    changed_zones = tuple(
        zone for zone in MacroZone if any(item.changed and item.zone is zone for item in items)
    )
    changed_count = sum(item.changed for item in items)
    return WorldLandmarkEffect(
        landmarks=items,
        mean_descriptor_distance=sum(distances) / len(distances),
        maximum_descriptor_distance=max(distances),
        changed_landmark_count=changed_count,
        changed_zones=changed_zones,
        effect_observed=(
            changed_count >= WORLD_EFFECT_REQUIRED_LANDMARKS
            and len(changed_zones) >= WORLD_EFFECT_REQUIRED_ZONES
        ),
    )


def run_bounded_camera_servo(
    source: CameraFrameSource,
    control: CameraControl,
    *,
    sleeper: Sleeper,
    settle_s: float,
    recorder: CameraArtifactRecorder = record_frame_digest,
    clock: Callable[[], float] = time.monotonic,
    limits: CameraServoLimits = DEFAULT_CAMERA_SERVO_LIMITS,
) -> CameraServoResult:
    """Run a refusal-oriented zoom servo under immutable, absolute bounds."""

    if (
        isinstance(settle_s, bool)
        or not isinstance(settle_s, (int, float))
        or not math.isfinite(settle_s)
        or not 0.0 < settle_s <= MAXIMUM_SERVO_SETTLE_SECONDS
    ):
        raise ValueError(
            f"settle_s must be finite and in (0, {MAXIMUM_SERVO_SETTLE_SECONDS}]"
        )
    if not isinstance(limits, CameraServoLimits):
        raise ValueError("limits must be CameraServoLimits")

    try:
        start = _read_clock(clock)
    except Exception as error:
        return _result_from_elapsed(
            limits,
            settle_s,
            None,
            [],
            None,
            None,
            CameraServoTerminalReason.CLOCK_ERROR,
            "The monotonic clock returned an invalid initial value.",
            0.0,
            error=error,
        )
    steps = _CameraServoSteps()
    seen_states: set[str] = set()
    consecutive_stagnant = 0
    previous_direction: CameraGuidanceDirection | None = None
    current_guidance: WorldCameraGuidance | None = None

    try:
        current_frame, current = _capture_evidence(source, recorder, "servo-initial")
    except Exception as error:
        return _result(
            limits,
            settle_s,
            None,
            steps,
            None,
            None,
            CameraServoTerminalReason.OBSERVATION_EXCEPTION,
            "Initial capture, recording, or evaluation failed closed.",
            start,
            clock,
            error=error,
        )
    initial = current

    while True:
        elapsed, clock_error = _safe_elapsed(start, clock)
        if clock_error is not None:
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                current,
                None,
                CameraServoTerminalReason.CLOCK_ERROR,
                "The monotonic clock regressed or returned an invalid value.",
                elapsed,
                error=clock_error,
            )
        if elapsed >= limits.max_elapsed_s:
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                current,
                None,
                CameraServoTerminalReason.TIME_BUDGET_EXHAUSTED,
                "The current captured evaluation exceeded the elapsed-time budget.",
                elapsed,
            )
        readiness_terminal = _readiness_terminal(current)
        if readiness_terminal is not None:
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                current,
                None,
                *readiness_terminal,
                elapsed,
            )
        production = current.production
        assert production is not None
        if production.passed:
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                current,
                None,
                CameraServoTerminalReason.PRODUCTION_PASS,
                "The unchanged production camera evaluator passed.",
                elapsed,
            )
        if not _is_fail_closed_production_rejection(production):
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                current,
                None,
                CameraServoTerminalReason.PRODUCTION_REJECTION_NOT_FAIL_CLOSED,
                "Production rejected without the required zero-target fail-closed state.",
                elapsed,
            )
        guidance = current_guidance
        if guidance is None:
            try:
                guidance = evaluate_varrock_east_camera_guidance(current_frame)
            except Exception as error:
                return _result_from_elapsed(
                    limits,
                    settle_s,
                    initial,
                    steps,
                    current,
                    None,
                    CameraServoTerminalReason.OBSERVATION_EXCEPTION,
                    "World-only guidance evaluation failed closed.",
                    elapsed,
                    error=error,
                )
        guidance_terminal = _guidance_terminal(guidance)
        if guidance_terminal is not None:
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                current,
                guidance,
                *guidance_terminal,
                elapsed,
            )
        assert guidance.direction is not None
        if previous_direction is not None and guidance.direction is not previous_direction:
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                current,
                guidance,
                CameraServoTerminalReason.OSCILLATION,
                "Guidance reversed the preceding zoom direction; stop before more input.",
                elapsed,
            )

        if len(steps) >= limits.max_primitives:
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                current,
                guidance,
                CameraServoTerminalReason.PRIMITIVE_BUDGET_EXHAUSTED,
                "The configured primitive budget was exhausted.",
                elapsed,
            )
        if len(steps.arm_attempts) >= ABSOLUTE_MAX_SERVO_ARM_ATTEMPTS:
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                current,
                guidance,
                CameraServoTerminalReason.ARM_ATTEMPT_BUDGET_EXHAUSTED,
                "The absolute pre-input arm-attempt bound was exhausted.",
                elapsed,
            )

        pre_input_elapsed, clock_error = _safe_elapsed(start, clock)
        if clock_error is not None:
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                current,
                guidance,
                CameraServoTerminalReason.CLOCK_ERROR,
                "The monotonic clock regressed or returned an invalid value.",
                pre_input_elapsed,
                error=clock_error,
            )
        if pre_input_elapsed >= limits.max_elapsed_s:
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                current,
                guidance,
                CameraServoTerminalReason.TIME_BUDGET_EXHAUSTED,
                "Guidance consumed the elapsed-time budget; stop before input.",
                pre_input_elapsed,
            )

        index = len(steps) + 1
        primitive = _zoom_primitive(index, guidance.direction)
        cycle_index = len(steps.arm_attempts) + 1

        try:
            arm_frame = source.capture()
            arm_artifact = _record_verified_frame(
                recorder, f"servo-arm-{cycle_index:02d}", arm_frame
            )
        except Exception as error:
            return _result(
                limits,
                settle_s,
                initial,
                steps,
                current,
                guidance,
                CameraServoTerminalReason.OBSERVATION_EXCEPTION,
                "Dedicated pre-input arm capture or recording failed closed.",
                start,
                clock,
                error=error,
            )

        try:
            arm_origin_clock_s = _read_clock(clock)
            if arm_origin_clock_s < start:
                raise ValueError("arm freshness clock regressed before the run start")
        except Exception as error:
            arm_context = _CameraServoArmContext(
                cycle_index=cycle_index,
                pending_primitive_index=index,
                decision=current,
                guidance=guidance,
                pending_primitive=primitive,
                arm_frame=arm_frame,
                arm_artifact=arm_artifact,
            )
            steps.arm_attempts.append(
                arm_context.evidence(
                    CameraServoArmOutcome.CLOCK_ERROR,
                    readiness=None,
                    production=None,
                    age=_arm_age_origin_error(),
                    error=error,
                )
            )
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                current,
                guidance,
                CameraServoTerminalReason.CLOCK_ERROR,
                "The arm-origin freshness clock sample was invalid.",
                0.0,
                error=error,
            )

        arm_context = _CameraServoArmContext(
            cycle_index=cycle_index,
            pending_primitive_index=index,
            decision=current,
            guidance=guidance,
            pending_primitive=primitive,
            arm_frame=arm_frame,
            arm_artifact=arm_artifact,
        )
        pending_arm_age = _arm_age_not_reached(arm_origin_clock_s)
        arm_is_fresh = (
            arm_frame.frame_id > current_frame.frame_id
            and arm_frame.captured_monotonic_s
            > current_frame.captured_monotonic_s
        )
        if not arm_is_fresh:
            try:
                nonfresh_guard = evaluate_camera_arm_guard(current_frame, arm_frame)
            except Exception as error:
                return _result(
                    limits,
                    settle_s,
                    initial,
                    steps,
                    current,
                    guidance,
                    CameraServoTerminalReason.OBSERVATION_EXCEPTION,
                    "Non-fresh pre-input arm guard failed closed.",
                    start,
                    clock,
                    error=error,
                )
            steps.arm_attempts.append(
                arm_context.evidence(
                    CameraServoArmOutcome.NON_FRESH_STOP,
                    readiness=None,
                    production=None,
                    guard=nonfresh_guard,
                    age=pending_arm_age,
                )
            )
            return _result(
                limits,
                settle_s,
                initial,
                steps,
                current,
                guidance,
                CameraServoTerminalReason.OBSERVATION_EXCEPTION,
                "The dedicated arm capture was not strictly newer; input was vetoed.",
                start,
                clock,
            )

        try:
            arm_readiness = evaluate_client_input_readiness(arm_frame)
        except Exception as error:
            steps.arm_attempts.append(
                arm_context.evidence(
                    CameraServoArmOutcome.EVALUATION_ERROR,
                    readiness=None,
                    production=None,
                    age=pending_arm_age,
                    error=error,
                )
            )
            return _result(
                limits,
                settle_s,
                initial,
                steps,
                current,
                guidance,
                CameraServoTerminalReason.OBSERVATION_EXCEPTION,
                "Pre-input arm readiness evaluation failed closed.",
                start,
                clock,
                error=error,
            )
        if not arm_readiness.safe_to_attempt_camera_input:
            armed = arm_context.evidence(
                CameraServoArmOutcome.READINESS_LOST,
                readiness=arm_readiness,
                production=None,
                age=pending_arm_age,
            )
            steps.arm_attempts.append(armed)
            arm_current = CameraServoFrameEvidence(
                artifact=arm_artifact,
                captured_monotonic_s=arm_frame.captured_monotonic_s,
                readiness=arm_readiness,
                production=None,
            )
            return _result(
                limits,
                settle_s,
                initial,
                steps,
                arm_current,
                guidance,
                CameraServoTerminalReason.READINESS_LOST,
                "Fresh pre-input gameplay readiness vetoed camera input.",
                start,
                clock,
            )

        try:
            arm_production = evaluate_varrock_east_camera(arm_frame)
        except Exception as error:
            steps.arm_attempts.append(
                arm_context.evidence(
                    CameraServoArmOutcome.EVALUATION_ERROR,
                    readiness=arm_readiness,
                    production=None,
                    age=pending_arm_age,
                    error=error,
                )
            )
            return _result(
                limits,
                settle_s,
                initial,
                steps,
                current,
                guidance,
                CameraServoTerminalReason.OBSERVATION_EXCEPTION,
                "Pre-input arm production evaluation failed closed.",
                start,
                clock,
                error=error,
            )
        arm_current = CameraServoFrameEvidence(
            artifact=arm_artifact,
            captured_monotonic_s=arm_frame.captured_monotonic_s,
            readiness=arm_readiness,
            production=arm_production,
        )
        arm_elapsed, clock_error = _safe_elapsed(start, clock)
        if clock_error is not None:
            steps.arm_attempts.append(
                arm_context.evidence(
                    CameraServoArmOutcome.CLOCK_ERROR,
                    readiness=arm_readiness,
                    production=arm_production,
                    age=pending_arm_age,
                    error=clock_error,
                )
            )
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                arm_current,
                guidance,
                CameraServoTerminalReason.CLOCK_ERROR,
                "The post-arm evaluation clock sample was invalid.",
                arm_elapsed,
                error=clock_error,
            )
        if arm_elapsed >= limits.max_elapsed_s:
            steps.arm_attempts.append(
                arm_context.evidence(
                    CameraServoArmOutcome.DEADLINE_EXHAUSTED,
                    readiness=arm_readiness,
                    production=arm_production,
                    age=pending_arm_age,
                )
            )
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                arm_current,
                guidance,
                CameraServoTerminalReason.TIME_BUDGET_EXHAUSTED,
                "The fresh arm evaluations exhausted the elapsed-time budget.",
                arm_elapsed,
            )
        if arm_production.passed:
            steps.arm_attempts.append(
                arm_context.evidence(
                    CameraServoArmOutcome.PRODUCTION_PASS,
                    readiness=arm_readiness,
                    production=arm_production,
                    age=pending_arm_age,
                )
            )
            return _result(
                limits,
                settle_s,
                initial,
                steps,
                arm_current,
                None,
                CameraServoTerminalReason.PRODUCTION_PASS,
                "The fresh arm frame passed the unchanged production evaluator.",
                start,
                clock,
            )
        if not _is_fail_closed_production_rejection(arm_production):
            steps.arm_attempts.append(
                arm_context.evidence(
                    CameraServoArmOutcome.PRODUCTION_REJECTION_NOT_FAIL_CLOSED,
                    readiness=arm_readiness,
                    production=arm_production,
                    age=pending_arm_age,
                )
            )
            return _result(
                limits,
                settle_s,
                initial,
                steps,
                arm_current,
                guidance,
                CameraServoTerminalReason.PRODUCTION_REJECTION_NOT_FAIL_CLOSED,
                "Fresh arm production was not the required zero-target fail-closed state.",
                start,
                clock,
            )

        try:
            arm_guard = evaluate_camera_arm_guard(current_frame, arm_frame)
        except Exception as error:
            steps.arm_attempts.append(
                arm_context.evidence(
                    CameraServoArmOutcome.GUARD_ERROR,
                    readiness=arm_readiness,
                    production=arm_production,
                    age=pending_arm_age,
                    error=error,
                )
            )
            return _result(
                limits,
                settle_s,
                initial,
                steps,
                arm_current,
                guidance,
                CameraServoTerminalReason.OBSERVATION_EXCEPTION,
                "Pre-input stale-guidance guard failed closed.",
                start,
                clock,
                error=error,
            )

        seam_elapsed, clock_error = _safe_elapsed(start, clock)
        if clock_error is not None:
            steps.arm_attempts.append(
                arm_context.evidence(
                    CameraServoArmOutcome.CLOCK_ERROR,
                    readiness=arm_readiness,
                    production=arm_production,
                    guard=arm_guard,
                    age=pending_arm_age,
                    error=clock_error,
                )
            )
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                arm_current,
                guidance,
                CameraServoTerminalReason.CLOCK_ERROR,
                "The post-arm monotonic clock sample was invalid.",
                seam_elapsed,
                error=clock_error,
            )
        if seam_elapsed >= limits.max_elapsed_s:
            steps.arm_attempts.append(
                arm_context.evidence(
                    CameraServoArmOutcome.DEADLINE_EXHAUSTED,
                    readiness=arm_readiness,
                    production=arm_production,
                    guard=arm_guard,
                    age=pending_arm_age,
                )
            )
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                arm_current,
                guidance,
                CameraServoTerminalReason.TIME_BUDGET_EXHAUSTED,
                "The dedicated pre-input arm seam exhausted the elapsed-time budget.",
                seam_elapsed,
            )

        if arm_guard.disposition is CameraArmGuardDisposition.DISCARD_RESTART:
            steps.arm_attempts.append(
                arm_context.evidence(
                    CameraServoArmOutcome.STALE_DISCARDED_RESTART,
                    readiness=arm_readiness,
                    production=arm_production,
                    guard=arm_guard,
                    age=pending_arm_age,
                )
            )
            current_frame = arm_frame
            current = arm_current
            current_guidance = None
            continue

        try:
            commit_frame = source.capture()
            commit_artifact = _record_verified_frame(
                recorder, f"servo-commit-{cycle_index:02d}", commit_frame
            )
        except Exception as error:
            steps.arm_attempts.append(
                arm_context.evidence(
                    CameraServoArmOutcome.EVALUATION_ERROR,
                    readiness=arm_readiness,
                    production=arm_production,
                    guard=arm_guard,
                    age=pending_arm_age,
                    error=error,
                )
            )
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                arm_current,
                guidance,
                CameraServoTerminalReason.OBSERVATION_EXCEPTION,
                "Final commit capture or recording failed closed.",
                seam_elapsed,
                error=error,
            )

        commit_context = _CameraServoCommitContext(
            accepted_arm_artifact=arm_artifact,
            accepted_arm_captured_monotonic_s=arm_frame.captured_monotonic_s,
            frame=commit_frame,
            artifact=commit_artifact,
        )
        commit_is_fresh = (
            commit_frame.frame_id > arm_frame.frame_id
            and commit_frame.captured_monotonic_s
            > arm_frame.captured_monotonic_s
        )
        if not commit_is_fresh:
            try:
                commit_guard = evaluate_camera_arm_guard(arm_frame, commit_frame)
            except Exception as error:
                commit = commit_context.evidence(
                    CameraServoCommitOutcome.GUARD_ERROR,
                    readiness=None,
                    error=error,
                )
                steps.arm_attempts.append(
                    arm_context.evidence(
                        CameraServoArmOutcome.COMMIT_STOP,
                        readiness=arm_readiness,
                        production=arm_production,
                        guard=arm_guard,
                        age=pending_arm_age,
                        commit=commit,
                    )
                )
                return _result_from_elapsed(
                    limits,
                    settle_s,
                    initial,
                    steps,
                    arm_current,
                    guidance,
                    CameraServoTerminalReason.OBSERVATION_EXCEPTION,
                    "Non-fresh final commit guard failed closed.",
                    seam_elapsed,
                    error=error,
                )
            commit = commit_context.evidence(
                CameraServoCommitOutcome.NON_FRESH_STOP,
                readiness=None,
                guard=commit_guard,
            )
            steps.arm_attempts.append(
                arm_context.evidence(
                    CameraServoArmOutcome.COMMIT_STOP,
                    readiness=arm_readiness,
                    production=arm_production,
                    guard=arm_guard,
                    age=pending_arm_age,
                    commit=commit,
                )
            )
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                arm_current,
                guidance,
                CameraServoTerminalReason.COMMIT_OBSERVATION_REJECTED,
                "The final commit capture was not strictly newer than its arm.",
                seam_elapsed,
            )

        try:
            commit_readiness = evaluate_client_input_readiness(commit_frame)
        except Exception as error:
            commit = commit_context.evidence(
                CameraServoCommitOutcome.EVALUATION_ERROR,
                readiness=None,
                error=error,
            )
            steps.arm_attempts.append(
                arm_context.evidence(
                    CameraServoArmOutcome.COMMIT_STOP,
                    readiness=arm_readiness,
                    production=arm_production,
                    guard=arm_guard,
                    age=pending_arm_age,
                    commit=commit,
                )
            )
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                arm_current,
                guidance,
                CameraServoTerminalReason.OBSERVATION_EXCEPTION,
                "Final commit readiness evaluation failed closed.",
                seam_elapsed,
                error=error,
            )
        if not commit_readiness.safe_to_attempt_camera_input:
            commit = commit_context.evidence(
                CameraServoCommitOutcome.READINESS_LOST,
                readiness=commit_readiness,
            )
            steps.arm_attempts.append(
                arm_context.evidence(
                    CameraServoArmOutcome.COMMIT_STOP,
                    readiness=arm_readiness,
                    production=arm_production,
                    guard=arm_guard,
                    age=pending_arm_age,
                    commit=commit,
                )
            )
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                arm_current,
                guidance,
                CameraServoTerminalReason.READINESS_LOST,
                "Final commit gameplay readiness vetoed camera input.",
                seam_elapsed,
            )

        try:
            commit_guard = evaluate_camera_arm_guard(arm_frame, commit_frame)
        except Exception as error:
            commit = commit_context.evidence(
                CameraServoCommitOutcome.GUARD_ERROR,
                readiness=commit_readiness,
                error=error,
            )
            steps.arm_attempts.append(
                arm_context.evidence(
                    CameraServoArmOutcome.COMMIT_STOP,
                    readiness=arm_readiness,
                    production=arm_production,
                    guard=arm_guard,
                    age=pending_arm_age,
                    commit=commit,
                )
            )
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                arm_current,
                guidance,
                CameraServoTerminalReason.OBSERVATION_EXCEPTION,
                "Final world-only commit guard failed closed.",
                seam_elapsed,
                error=error,
            )
        commit_outcome = (
            CameraServoCommitOutcome.RETAINED
            if commit_guard.disposition is CameraArmGuardDisposition.RETAIN
            else CameraServoCommitOutcome.GUARD_REJECTED
        )
        commit = commit_context.evidence(
            commit_outcome,
            readiness=commit_readiness,
            guard=commit_guard,
        )
        if commit_outcome is CameraServoCommitOutcome.GUARD_REJECTED:
            steps.arm_attempts.append(
                arm_context.evidence(
                    CameraServoArmOutcome.COMMIT_STOP,
                    readiness=arm_readiness,
                    production=arm_production,
                    guard=arm_guard,
                    age=pending_arm_age,
                    commit=commit,
                )
            )
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                arm_current,
                guidance,
                CameraServoTerminalReason.COMMIT_OBSERVATION_REJECTED,
                "The final world-only commit guard rejected the accepted arm.",
                seam_elapsed,
            )

        current_frame = commit_frame
        current = arm_current
        try:
            state_digest = _world_state_digest(current_frame)
        except Exception as error:
            steps.arm_attempts.append(
                arm_context.evidence(
                    CameraServoArmOutcome.EVALUATION_ERROR,
                    readiness=arm_readiness,
                    production=arm_production,
                    guard=arm_guard,
                    age=pending_arm_age,
                    commit=commit,
                    error=error,
                )
            )
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                current,
                guidance,
                CameraServoTerminalReason.OBSERVATION_EXCEPTION,
                "Armed world-only repeated-state measurement failed closed.",
                seam_elapsed,
                error=error,
            )
        if state_digest in seen_states:
            steps.arm_attempts.append(
                arm_context.evidence(
                    CameraServoArmOutcome.REPEATED_STATE_STOP,
                    readiness=arm_readiness,
                    production=arm_production,
                    guard=arm_guard,
                    age=pending_arm_age,
                    commit=commit,
                )
            )
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                current,
                guidance,
                CameraServoTerminalReason.REPEATED_STATE,
                "A previously armed world-landmark state recurred.",
                seam_elapsed,
            )
        seen_states.add(state_digest)

        preflight: CameraPreflightReceipt | None = None
        preflight_error: Exception | None = None
        try:
            preflight = control.preflight()
        except Exception as error:
            preflight_error = error

        try:
            arm_final_clock_s = _read_clock(clock)
            if arm_final_clock_s < start:
                raise ValueError("monotonic clock regressed before the run start")
            completed_arm_age = _completed_arm_age(
                arm_origin_clock_s, arm_final_clock_s
            )
        except Exception as error:
            steps.arm_attempts.append(
                arm_context.evidence(
                    CameraServoArmOutcome.CLOCK_ERROR,
                    readiness=arm_readiness,
                    production=arm_production,
                    guard=arm_guard,
                    age=_arm_age_final_error(arm_origin_clock_s),
                    commit=commit,
                    error=error,
                )
            )
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                arm_current,
                guidance,
                CameraServoTerminalReason.CLOCK_ERROR,
                "The final arm-freshness clock sample was invalid.",
                seam_elapsed,
                error=error,
            )
        immediate_elapsed = arm_final_clock_s - start
        if completed_arm_age.status is CameraServoArmAgeStatus.EXPIRED:
            steps.arm_attempts.append(
                arm_context.evidence(
                    CameraServoArmOutcome.ARM_FRESHNESS_EXPIRED,
                    readiness=arm_readiness,
                    production=arm_production,
                    guard=arm_guard,
                    age=completed_arm_age,
                    commit=commit,
                )
            )
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                arm_current,
                guidance,
                CameraServoTerminalReason.ARM_FRESHNESS_EXPIRED,
                "The validation-only arm-to-input freshness ceiling expired.",
                immediate_elapsed,
            )
        if immediate_elapsed >= limits.max_elapsed_s:
            steps.arm_attempts.append(
                arm_context.evidence(
                    CameraServoArmOutcome.DEADLINE_EXHAUSTED,
                    readiness=arm_readiness,
                    production=arm_production,
                    guard=arm_guard,
                    age=completed_arm_age,
                    commit=commit,
                )
            )
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                arm_current,
                guidance,
                CameraServoTerminalReason.TIME_BUDGET_EXHAUSTED,
                "The immediate pre-input deadline check exhausted the budget.",
                immediate_elapsed,
            )

        armed = arm_context.evidence(
            CameraServoArmOutcome.RETAINED,
            readiness=arm_readiness,
            production=arm_production,
            guard=arm_guard,
            age=completed_arm_age,
            commit=commit,
        )
        steps.arm_attempts.append(armed)

        receipt: CameraPlanReceipt | None = None
        try:
            if preflight_error is not None:
                raise preflight_error
            assert preflight is not None
            prepared_control = _PreparedServoCameraControl(control, preflight)
            receipt = CameraPlanRunner(prepared_control, sleeper).run(primitive)
            _require_one_detent_receipt(receipt)
        except Exception as error:
            terminal_elapsed, terminal_clock_error = _safe_elapsed(start, clock)
            steps.append(
                CameraServoStep(
                    index=index,
                    pre=current,
                    arm=armed,
                    guidance=guidance,
                    primitive=primitive,
                    receipt=receipt,
                    post=None,
                    post_guidance=None,
                    effect=None,
                    elapsed_s=terminal_elapsed,
                    pre_world_state_digest=state_digest,
                    post_world_state_digest=None,
                    stagnant_steps_before=consecutive_stagnant,
                    stagnant_steps_after=consecutive_stagnant,
                    direction_reversed=False,
                    exception=_exception_evidence(error),
                )
            )
            if terminal_clock_error is not None:
                return _result_from_elapsed(
                    limits,
                    settle_s,
                    initial,
                    steps,
                    current,
                    guidance,
                    CameraServoTerminalReason.CLOCK_ERROR,
                    "The terminal clock sample was invalid after input failure.",
                    terminal_elapsed,
                    error=terminal_clock_error,
                )
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                current,
                guidance,
                CameraServoTerminalReason.INPUT_EXCEPTION,
                "Camera preflight, safety, or receipt validation failed closed.",
                terminal_elapsed,
                error=error,
            )

        try:
            sleeper(float(settle_s))
        except Exception as error:
            terminal_elapsed, terminal_clock_error = _safe_elapsed(start, clock)
            steps.append(
                CameraServoStep(
                    index=index,
                    pre=current,
                    arm=armed,
                    guidance=guidance,
                    primitive=primitive,
                    receipt=receipt,
                    post=None,
                    post_guidance=None,
                    effect=None,
                    elapsed_s=terminal_elapsed,
                    pre_world_state_digest=state_digest,
                    post_world_state_digest=None,
                    stagnant_steps_before=consecutive_stagnant,
                    stagnant_steps_after=consecutive_stagnant,
                    direction_reversed=False,
                    exception=_exception_evidence(error),
                )
            )
            if terminal_clock_error is not None:
                return _result_from_elapsed(
                    limits,
                    settle_s,
                    initial,
                    steps,
                    current,
                    guidance,
                    CameraServoTerminalReason.CLOCK_ERROR,
                    "The terminal clock sample was invalid after settle failure.",
                    terminal_elapsed,
                    error=terminal_clock_error,
                )
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                current,
                guidance,
                CameraServoTerminalReason.SETTLE_EXCEPTION,
                "Post-primitive settle failed closed.",
                terminal_elapsed,
                error=error,
            )

        try:
            post_frame, post = _capture_evidence(
                source, recorder, f"servo-step-{index:02d}-post"
            )
        except Exception as error:
            terminal_elapsed, terminal_clock_error = _safe_elapsed(start, clock)
            steps.append(
                CameraServoStep(
                    index=index,
                    pre=current,
                    arm=armed,
                    guidance=guidance,
                    primitive=primitive,
                    receipt=receipt,
                    post=None,
                    post_guidance=None,
                    effect=None,
                    elapsed_s=terminal_elapsed,
                    pre_world_state_digest=state_digest,
                    post_world_state_digest=None,
                    stagnant_steps_before=consecutive_stagnant,
                    stagnant_steps_after=consecutive_stagnant,
                    direction_reversed=False,
                    exception=_exception_evidence(error),
                )
            )
            if terminal_clock_error is not None:
                return _result_from_elapsed(
                    limits,
                    settle_s,
                    initial,
                    steps,
                    current,
                    guidance,
                    CameraServoTerminalReason.CLOCK_ERROR,
                    "The terminal clock sample was invalid after observation failure.",
                    terminal_elapsed,
                    error=terminal_clock_error,
                )
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                current,
                guidance,
                CameraServoTerminalReason.OBSERVATION_EXCEPTION,
                "Post-primitive capture, recording, or evaluation failed closed.",
                terminal_elapsed,
                error=error,
            )

        post_elapsed, clock_error = _safe_elapsed(start, clock)
        effect: WorldLandmarkEffect | None = None
        post_guidance: WorldCameraGuidance | None = None
        post_state_digest: str | None = None
        progress: CameraServoProgress | None = None
        step_builder = _CameraServoStepBuilder(
            index=index,
            pre=current,
            arm=armed,
            guidance=guidance,
            primitive=primitive,
            receipt=receipt,
            post=post,
            elapsed_s=post_elapsed,
            pre_world_state_digest=state_digest,
            stagnant_steps_before=consecutive_stagnant,
        )

        if clock_error is not None:
            steps.append(step_builder.build(error=clock_error))
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                post,
                None,
                CameraServoTerminalReason.CLOCK_ERROR,
                "The monotonic clock regressed or returned an invalid value.",
                post_elapsed,
                error=clock_error,
            )
        if post_elapsed >= limits.max_elapsed_s:
            steps.append(step_builder.build())
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                post,
                None,
                CameraServoTerminalReason.TIME_BUDGET_EXHAUSTED,
                "The configured elapsed-time budget was exhausted after capture.",
                post_elapsed,
            )
        readiness_terminal = _readiness_terminal(post)
        if readiness_terminal is not None:
            steps.append(step_builder.build())
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                post,
                None,
                *readiness_terminal,
                post_elapsed,
            )

        try:
            effect = measure_world_landmark_effect(current_frame, post_frame)
            post_state_digest = _world_state_digest(post_frame)
            step_builder.effect = effect
            step_builder.post_world_state_digest = post_state_digest
        except Exception as error:
            steps.append(step_builder.build(error=error))
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                post,
                None,
                CameraServoTerminalReason.OBSERVATION_EXCEPTION,
                "Post-primitive world-only effect measurement failed closed.",
                post_elapsed,
                error=error,
            )

        post_elapsed, clock_error = _safe_elapsed(start, clock)
        step_builder.elapsed_s = post_elapsed
        if clock_error is not None:
            steps.append(step_builder.build(error=clock_error))
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                post,
                None,
                CameraServoTerminalReason.CLOCK_ERROR,
                "The monotonic clock regressed or returned an invalid value.",
                post_elapsed,
                error=clock_error,
            )
        if post_elapsed >= limits.max_elapsed_s:
            steps.append(step_builder.build())
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                post,
                None,
                CameraServoTerminalReason.TIME_BUDGET_EXHAUSTED,
                "The configured elapsed-time budget was exhausted after observation.",
                post_elapsed,
            )

        post_production = post.production
        assert post_production is not None
        if post_production.passed:
            steps.append(step_builder.build())
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                post,
                None,
                CameraServoTerminalReason.PRODUCTION_PASS,
                "The unchanged production camera evaluator passed.",
                post_elapsed,
            )
        if not _is_fail_closed_production_rejection(post_production):
            steps.append(step_builder.build())
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                post,
                None,
                CameraServoTerminalReason.PRODUCTION_REJECTION_NOT_FAIL_CLOSED,
                "Production rejected without the required zero-target fail-closed state.",
                post_elapsed,
            )
        if post_state_digest in seen_states:
            steps.append(step_builder.build())
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                post,
                None,
                CameraServoTerminalReason.REPEATED_STATE,
                "The primitive returned to a previously observed world-landmark state.",
                post_elapsed,
            )

        try:
            post_guidance = evaluate_varrock_east_camera_guidance(post_frame)
            step_builder.post_guidance = post_guidance
        except Exception as error:
            steps.append(step_builder.build(error=error))
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                post,
                None,
                CameraServoTerminalReason.OBSERVATION_EXCEPTION,
                "Post-primitive guidance evaluation failed closed.",
                post_elapsed,
                error=error,
            )

        post_elapsed, clock_error = _safe_elapsed(start, clock)
        step_builder.elapsed_s = post_elapsed
        if clock_error is not None:
            steps.append(step_builder.build(error=clock_error))
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                post,
                post_guidance,
                CameraServoTerminalReason.CLOCK_ERROR,
                "The monotonic clock regressed or returned an invalid value.",
                post_elapsed,
                error=clock_error,
            )
        if post_elapsed >= limits.max_elapsed_s:
            steps.append(step_builder.build())
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                post,
                post_guidance,
                CameraServoTerminalReason.TIME_BUDGET_EXHAUSTED,
                "Post-action guidance consumed the elapsed-time budget.",
                post_elapsed,
            )

        guidance_terminal = _guidance_terminal(post_guidance)
        if guidance_terminal is not None:
            steps.append(step_builder.build())
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                post,
                post_guidance,
                *guidance_terminal,
                post_elapsed,
            )
        reversed_direction = post_guidance.direction is not guidance.direction
        if reversed_direction:
            steps.append(step_builder.build(reversed_direction=True))
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                post,
                post_guidance,
                CameraServoTerminalReason.OSCILLATION,
                "Guidance reversed after one zoom detent; stop before more input.",
                post_elapsed,
            )

        try:
            progress = _measure_progress(guidance, post_guidance)
            step_builder.progress = progress
        except Exception as error:
            steps.append(step_builder.build(error=error))
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                post,
                post_guidance,
                CameraServoTerminalReason.OBSERVATION_EXCEPTION,
                "Zoom-error progress measurement failed closed.",
                post_elapsed,
                error=error,
            )
        if progress.status is CameraServoProgressStatus.WORSENED:
            steps.append(step_builder.build())
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                post,
                post_guidance,
                CameraServoTerminalReason.WORSENING_GUIDANCE,
                "The zoom-error score increased beyond the frozen tolerance.",
                post_elapsed,
            )

        stagnant_step = (
            not effect.effect_observed
            or progress.status is CameraServoProgressStatus.STAGNANT
        )
        stagnant_after = consecutive_stagnant + 1 if stagnant_step else 0
        steps.append(step_builder.build(stagnant_after=stagnant_after))
        if stagnant_after >= MAXIMUM_CONSECUTIVE_STAGNANT_STEPS:
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                post,
                post_guidance,
                CameraServoTerminalReason.NO_EFFECT_STAGNATION,
                "Two consecutive primitives lacked useful monotonic world progress.",
                post_elapsed,
            )

        previous_direction = guidance.direction
        consecutive_stagnant = stagnant_after
        current_frame = post_frame
        current = post
        current_guidance = post_guidance


def _capture_evidence(
    source: CameraFrameSource,
    recorder: CameraArtifactRecorder,
    label: str,
) -> tuple[Frame, CameraServoFrameEvidence]:
    frame = source.capture()
    artifact = _record_verified_frame(recorder, label, frame)
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


def _record_verified_frame(
    recorder: CameraArtifactRecorder,
    label: str,
    frame: Frame,
) -> CameraFrameArtifact:
    """Record and immediately bind artifact identity to the captured frame."""

    artifact = recorder(label, frame)
    expected_sha256 = hashlib.sha256(frame.payload).hexdigest()
    if (
        artifact.label != label
        or artifact.frame_id != frame.frame_id
        or artifact.width != frame.width
        or artifact.height != frame.height
        or artifact.pixel_format != frame.pixel_format.value
        or artifact.raw_sha256 != expected_sha256
    ):
        raise ValueError("recorder artifact does not bind the exact captured frame")
    return artifact


def _readiness_terminal(
    evidence: CameraServoFrameEvidence,
) -> tuple[CameraServoTerminalReason, str] | None:
    if evidence.readiness.safe_to_attempt_camera_input:
        return None
    return (
        CameraServoTerminalReason.READINESS_LOST,
        "Fixed gameplay-chrome readiness vetoed camera input.",
    )


def _is_fail_closed_production_rejection(evaluation: CameraEvaluation) -> bool:
    return (
        not evaluation.passed
        and not evaluation.scene_validated
        and not evaluation.definitive_target_ids
        and bool(evaluation.resource_states)
        and all(
            resource.state is ResourceVisualState.UNCERTAIN
            for resource in evaluation.resource_states
        )
    )


def _guidance_terminal(
    guidance: WorldCameraGuidance,
) -> tuple[CameraServoTerminalReason, str] | None:
    if guidance.disposition is not CameraGuidanceDisposition.ACTIONABLE:
        return (
            CameraServoTerminalReason.INSUFFICIENT_GUIDANCE,
            f"World-only diagnostics refused a camera action: {guidance.reason.value}.",
        )
    if (
        guidance.axis is not CameraGuidanceAxis.ZOOM
        or guidance.direction is None
        or guidance.can_accept
        or guidance.can_validate_scene
        or guidance.can_expose_resources
    ):
        return (
            CameraServoTerminalReason.UNSAFE_GUIDANCE,
            "Guidance did not authorize exactly one diagnostic-only zoom sign.",
        )
    return None


def _zoom_error(guidance: WorldCameraGuidance) -> float:
    if (
        guidance.disposition is not CameraGuidanceDisposition.ACTIONABLE
        or guidance.axis is not CameraGuidanceAxis.ZOOM
        or guidance.fit is None
        or not math.isfinite(guidance.fit.scale)
        or guidance.fit.scale <= 0.0
    ):
        raise ValueError("zoom progress requires an actionable positive finite scale fit")
    return abs(math.log(guidance.fit.scale))


def _progress_status(error_decrease: float) -> CameraServoProgressStatus:
    if error_decrease > ZOOM_ERROR_PROGRESS_TOLERANCE:
        return CameraServoProgressStatus.IMPROVED
    if error_decrease < -ZOOM_ERROR_PROGRESS_TOLERANCE:
        return CameraServoProgressStatus.WORSENED
    return CameraServoProgressStatus.STAGNANT


def _measure_progress(
    before: WorldCameraGuidance,
    after: WorldCameraGuidance,
) -> CameraServoProgress:
    before_error = _zoom_error(before)
    after_error = _zoom_error(after)
    decrease = before_error - after_error
    return CameraServoProgress(
        before_absolute_log_scale_error=before_error,
        after_absolute_log_scale_error=after_error,
        error_decrease=decrease,
        tolerance=ZOOM_ERROR_PROGRESS_TOLERANCE,
        status=_progress_status(decrease),
    )


def _zoom_primitive(index: int, direction: CameraGuidanceDirection) -> CameraPlan:
    detents = 1 if direction is CameraGuidanceDirection.POSITIVE else -1
    x, y = REVIEWED_CAMERA_WHEEL_POINT
    return CameraPlan(
        name=f"issue31-servo-{index:02d}-zoom-{direction.value}",
        actions=(CameraWheel(x=x, y=y, detents=detents),),
    )


def _require_one_detent_receipt(receipt: CameraPlanReceipt) -> None:
    input_receipts = receipt.action_receipts[0].input_receipts
    if len(input_receipts) != 1:
        raise ValueError("servo wheel receipt must contain one input acknowledgement")
    wheel = input_receipts[0]
    if wheel.requested_events != 1 or wheel.completed_events != 1:
        raise ValueError("servo wheel receipt must prove exactly one completed detent")


def _world_state_digest(frame: Frame) -> str:
    profile = load_varrock_east_iron_profile()
    digest = hashlib.sha256()
    for landmark in profile.scene_landmarks:
        digest.update(landmark.landmark_id.encode("utf-8"))
        for value in describe_region(frame, landmark.region, grid=landmark.grid):
            digest.update(struct.pack("!d", value))
    return digest.hexdigest()


def _read_clock(clock: Callable[[], float]) -> float:
    value = clock()
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("clock must return a finite real number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("clock must return a finite real number")
    return result


def _arm_age_not_reached(origin_clock_s: float) -> CameraServoArmAgeEvidence:
    return CameraServoArmAgeEvidence(
        origin_clock_s=origin_clock_s,
        final_clock_s=None,
        age_s=None,
        maximum_age_s=MAXIMUM_ARM_TO_INPUT_AGE_SECONDS,
        status=CameraServoArmAgeStatus.NOT_REACHED,
    )


def _arm_age_origin_error() -> CameraServoArmAgeEvidence:
    return CameraServoArmAgeEvidence(
        origin_clock_s=None,
        final_clock_s=None,
        age_s=None,
        maximum_age_s=MAXIMUM_ARM_TO_INPUT_AGE_SECONDS,
        status=CameraServoArmAgeStatus.ORIGIN_CLOCK_ERROR,
    )


def _arm_age_final_error(origin_clock_s: float) -> CameraServoArmAgeEvidence:
    return CameraServoArmAgeEvidence(
        origin_clock_s=origin_clock_s,
        final_clock_s=None,
        age_s=None,
        maximum_age_s=MAXIMUM_ARM_TO_INPUT_AGE_SECONDS,
        status=CameraServoArmAgeStatus.FINAL_CLOCK_ERROR,
    )


class _PreparedServoCameraControl:
    """Replay one completed preflight immediately before a servo primitive."""

    __slots__ = ("_control", "_preflight", "_served")

    def __init__(
        self,
        control: CameraControl,
        preflight: CameraPreflightReceipt,
    ) -> None:
        self._control = control
        self._preflight = preflight
        self._served = False

    def preflight(self) -> CameraPreflightReceipt:
        if self._served:
            raise RuntimeError("prepared servo preflight is single-use")
        self._served = True
        return self._preflight

    def click_compass(self, x: int, y: int) -> CameraInputReceipt:
        return self._control.click_compass(x, y)

    def key_down(self, key: str) -> CameraInputReceipt:
        return self._control.key_down(key)

    def key_up(self, key: str) -> CameraInputReceipt:
        return self._control.key_up(key)

    def scroll_camera(self, x: int, y: int, detents: int) -> CameraInputReceipt:
        return self._control.scroll_camera(x, y, detents)

    def drag_camera(
        self,
        x: int,
        y: int,
        delta_x: int,
        delta_y: int,
    ) -> tuple[CameraInputReceipt, CameraInputReceipt, CameraInputReceipt]:
        return self._control.drag_camera(x, y, delta_x, delta_y)


def _completed_arm_age(
    origin_clock_s: float,
    final_clock_s: float,
) -> CameraServoArmAgeEvidence:
    age_s = final_clock_s - origin_clock_s
    if age_s < 0.0:
        raise ValueError("arm freshness clock regressed")
    status = (
        CameraServoArmAgeStatus.EXPIRED
        if age_s >= MAXIMUM_ARM_TO_INPUT_AGE_SECONDS
        else CameraServoArmAgeStatus.WITHIN_LIMIT
    )
    return CameraServoArmAgeEvidence(
        origin_clock_s=origin_clock_s,
        final_clock_s=final_clock_s,
        age_s=age_s,
        maximum_age_s=MAXIMUM_ARM_TO_INPUT_AGE_SECONDS,
        status=status,
    )


def _safe_elapsed(
    start: float, clock: Callable[[], float]
) -> tuple[float, Exception | None]:
    try:
        elapsed = _read_clock(clock) - start
    except Exception as error:
        return 0.0, error
    if elapsed < 0.0:
        return 0.0, ValueError("monotonic clock regressed")
    return elapsed, None


def _exception_evidence(error: Exception) -> CameraServoExceptionEvidence:
    return CameraServoExceptionEvidence(type(error).__name__, str(error))


def _result(
    limits: CameraServoLimits,
    settle_s: float,
    initial: CameraServoFrameEvidence | None,
    steps: list[CameraServoStep],
    final: CameraServoFrameEvidence | None,
    final_guidance: WorldCameraGuidance | None,
    reason: CameraServoTerminalReason,
    detail: str,
    start: float,
    clock: Callable[[], float],
    *,
    error: Exception | None = None,
) -> CameraServoResult:
    elapsed, clock_error = _safe_elapsed(start, clock)
    if clock_error is not None and reason is not CameraServoTerminalReason.CLOCK_ERROR:
        reason = CameraServoTerminalReason.CLOCK_ERROR
        detail = "The monotonic clock regressed or returned an invalid value."
        error = clock_error
    return _result_from_elapsed(
        limits,
        settle_s,
        initial,
        steps,
        final,
        final_guidance,
        reason,
        detail,
        elapsed,
        error=error,
    )


def _result_from_elapsed(
    limits: CameraServoLimits,
    settle_s: float,
    initial: CameraServoFrameEvidence | None,
    steps: list[CameraServoStep],
    final: CameraServoFrameEvidence | None,
    final_guidance: WorldCameraGuidance | None,
    reason: CameraServoTerminalReason,
    detail: str,
    elapsed_s: float,
    *,
    error: Exception | None = None,
) -> CameraServoResult:
    return CameraServoResult(
        limits=limits,
        settle_s=float(settle_s),
        initial=initial,
        arm_attempts=tuple(
            steps.arm_attempts if isinstance(steps, _CameraServoSteps) else ()
        ),
        steps=tuple(steps),
        final=final,
        final_guidance=final_guidance,
        terminal_reason=reason,
        detail=detail,
        elapsed_s=elapsed_s,
        exception=_exception_evidence(error) if error is not None else None,
    )
