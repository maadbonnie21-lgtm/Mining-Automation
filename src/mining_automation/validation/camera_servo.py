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
    CameraPlan,
    CameraPlanReceipt,
    CameraPlanRunner,
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
    "ABSOLUTE_MAX_SERVO_ELAPSED_SECONDS",
    "ABSOLUTE_MAX_SERVO_PRIMITIVES",
    "DEFAULT_MAX_SERVO_PRIMITIVES",
    "CameraServoExceptionEvidence",
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
MAXIMUM_SERVO_SETTLE_SECONDS: Final[float] = 10.0
WORLD_EFFECT_DESCRIPTOR_EPSILON: Final[float] = 0.001
WORLD_EFFECT_REQUIRED_LANDMARKS: Final[int] = 3
WORLD_EFFECT_REQUIRED_ZONES: Final[int] = 3
ZOOM_ERROR_PROGRESS_TOLERANCE: Final[float] = 0.0005
MAXIMUM_CONSECUTIVE_STAGNANT_STEPS: Final[int] = 2


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
    readiness: ClientInputReadiness
    production: CameraEvaluation | None

    def __post_init__(self) -> None:
        if self.readiness.safe_to_attempt_camera_input is (self.production is None):
            raise ValueError(
                "production evidence is required exactly when readiness permits evaluation"
            )


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


@dataclass(slots=True)
class _CameraServoStepBuilder:
    """Mutable loop-local assembly that emits only immutable public evidence."""

    index: int
    pre: CameraServoFrameEvidence
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


@dataclass(frozen=True, slots=True)
class CameraServoResult:
    """Terminal bounded-servo evidence; only a production pass can pass."""

    limits: CameraServoLimits
    settle_s: float
    initial: CameraServoFrameEvidence | None
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
        if len(self.steps) > self.limits.max_primitives:
            raise ValueError("servo evidence exceeds the configured primitive budget")
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
    steps: list[CameraServoStep] = []
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

        try:
            state_digest = _world_state_digest(current_frame)
        except Exception as error:
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                current,
                guidance,
                CameraServoTerminalReason.OBSERVATION_EXCEPTION,
                "World-only repeated-state measurement failed closed.",
                elapsed,
                error=error,
            )
        if state_digest in seen_states:
            return _result_from_elapsed(
                limits,
                settle_s,
                initial,
                steps,
                current,
                guidance,
                CameraServoTerminalReason.REPEATED_STATE,
                "A previously observed world-landmark state recurred.",
                elapsed,
            )
        seen_states.add(state_digest)

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
        runner = CameraPlanRunner(control, sleeper)
        receipt: CameraPlanReceipt | None = None
        try:
            receipt = runner.run(primitive)
            _require_one_detent_receipt(receipt)
        except Exception as error:
            terminal_elapsed, terminal_clock_error = _safe_elapsed(start, clock)
            steps.append(
                CameraServoStep(
                    index=index,
                    pre=current,
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
    artifact = recorder(label, frame)
    readiness = evaluate_client_input_readiness(frame)
    production = (
        evaluate_varrock_east_camera(frame)
        if readiness.safe_to_attempt_camera_input
        else None
    )
    return frame, CameraServoFrameEvidence(
        artifact=artifact,
        readiness=readiness,
        production=production,
    )


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
        steps=tuple(steps),
        final=final,
        final_guidance=final_guidance,
        terminal_reason=reason,
        detail=detail,
        elapsed_s=elapsed_s,
        exception=_exception_evidence(error) if error is not None else None,
    )
