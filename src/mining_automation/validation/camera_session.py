"""Deterministic orchestration for repeated real-client camera validation.

The session runner composes injected capture, camera control, sleeping, and
artifact recording.  It contains no Windows calls and has no diagnostic
registration fallback: every frame is judged by ``camera_evaluation`` alone.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Protocol

from ..capture import Frame
from ..perception.resource import ResourceVisualState
from .camera_evaluation import CameraEvaluation, evaluate_varrock_east_camera
from .camera_plan import (
    CameraControl,
    CameraPlan,
    CameraPlanReceipt,
    CameraPlanRunner,
    Sleeper,
)

__all__ = [
    "MINIMUM_REACQUISITION_TRIALS",
    "MINIMUM_CONFIRMATION_FRAMES",
    "MAXIMUM_NORMALIZATION_CANDIDATES",
    "CameraArtifactRecorder",
    "CameraFrameArtifact",
    "CameraFrameRecord",
    "CameraFrameSource",
    "CameraNormalizationAttempt",
    "CameraNormalizationResult",
    "CameraSessionResult",
    "CameraTrialResult",
    "record_frame_digest",
    "run_camera_validation_session",
]

MINIMUM_REACQUISITION_TRIALS = 3
MINIMUM_CONFIRMATION_FRAMES = 2
MAXIMUM_REACQUISITION_TRIALS = 12
MAXIMUM_CONFIRMATION_FRAMES = 5
MAXIMUM_NORMALIZATION_CANDIDATES = 12
MAXIMUM_SETTLE_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class CameraFrameArtifact:
    """Private-frame identity retained without retaining its pixel payload."""

    label: str
    frame_id: int
    width: int
    height: int
    pixel_format: str
    raw_sha256: str
    files: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("artifact label must be a non-empty string")
        if len(self.raw_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.raw_sha256
        ):
            raise ValueError("raw_sha256 must be a lowercase SHA-256 digest")
        if not isinstance(self.files, tuple):
            raise ValueError("artifact files must be a tuple")


@dataclass(frozen=True, slots=True)
class CameraFrameRecord:
    """One captured artifact and its unchanged production evaluation."""

    artifact: CameraFrameArtifact
    evaluation: CameraEvaluation


@dataclass(frozen=True, slots=True)
class CameraNormalizationAttempt:
    """One independently executed normalization candidate and production frame."""

    index: int
    plan: CameraPlan
    receipt: CameraPlanReceipt
    frame: CameraFrameRecord

    def __post_init__(self) -> None:
        if (
            isinstance(self.index, bool)
            or not isinstance(self.index, int)
            or self.index <= 0
        ):
            raise ValueError("normalization attempt index must be a positive integer")
        if self.receipt.plan != self.plan:
            raise ValueError("normalization attempt receipt must match its exact plan")

    @property
    def passed(self) -> bool:
        """Return the unchanged production camera verdict for this candidate."""

        return self.frame.evaluation.passed


@dataclass(frozen=True, slots=True)
class CameraNormalizationResult:
    """Ordered, production-gated result of one bounded candidate search.

    Candidate indexes are one-based for direct use in human-readable evidence.
    A selected candidate is always the final recorded attempt because search
    stops at the first unchanged production pass.  ``None`` selection fields
    therefore mean every bounded candidate was tried and failed closed.
    """

    attempts: tuple[CameraNormalizationAttempt, ...]
    selected_candidate_index_1_based: int | None
    selected_identity: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.attempts, tuple):
            raise ValueError("normalization attempts must be a tuple")
        if not 1 <= len(self.attempts) <= MAXIMUM_NORMALIZATION_CANDIDATES:
            raise ValueError(
                "normalization result requires between 1 and "
                f"{MAXIMUM_NORMALIZATION_CANDIDATES} attempts"
            )
        if tuple(attempt.index for attempt in self.attempts) != tuple(
            range(1, len(self.attempts) + 1)
        ):
            raise ValueError("normalization attempts must have contiguous ordered indexes")
        if len({attempt.plan.name for attempt in self.attempts}) != len(self.attempts):
            raise ValueError("normalization attempt identities must be unique")

        selected_index = self.selected_candidate_index_1_based
        if selected_index is None:
            if self.selected_identity is not None:
                raise ValueError(
                    "selected_identity must be None when no candidate was selected"
                )
            if any(attempt.passed for attempt in self.attempts):
                raise ValueError("a passing attempt must be selected")
            return

        if isinstance(selected_index, bool) or not isinstance(selected_index, int):
            raise ValueError("selected candidate index must be an integer or None")
        if selected_index != len(self.attempts):
            raise ValueError("selected candidate must be the final recorded attempt")
        selected = self.attempts[-1]
        if self.selected_identity != selected.plan.name:
            raise ValueError("selected identity must match the selected candidate plan")
        if not selected.passed:
            raise ValueError("selected normalization candidate must pass production evaluation")
        if any(attempt.passed for attempt in self.attempts[:-1]):
            raise ValueError("all normalization attempts before selection must fail")

    @property
    def passed(self) -> bool:
        """Whether bounded search selected an unchanged production pass."""

        return self.selected_candidate_index_1_based is not None


@dataclass(frozen=True, slots=True)
class CameraTrialResult:
    """Before, perturbed, and normalized evidence for one fixed trial."""

    trial_index: int
    before: CameraFrameRecord
    perturbation_plan: CameraPlan
    perturbation_receipt: CameraPlanReceipt
    perturbed: CameraFrameRecord
    perturbation_fail_closed: bool
    normalization: CameraNormalizationResult
    confirmations: tuple[CameraFrameRecord, ...]

    @property
    def expected_resource_state_vector(
        self,
    ) -> tuple[tuple[str, ResourceVisualState], ...]:
        """Return the ordered production states captured before perturbation."""

        return _resource_state_vector(self.before.evaluation)

    @property
    def confirmation_state_matches(self) -> tuple[bool, ...]:
        """Whether each confirmation exactly preserves the baseline states."""

        expected = self.expected_resource_state_vector
        return tuple(
            _resource_state_vector(confirmation.evaluation) == expected
            for confirmation in self.confirmations
        )

    @property
    def passed(self) -> bool:
        return (
            self.before.evaluation.passed
            and self.perturbation_fail_closed
            and self.normalization.passed
            and all(
                confirmation.evaluation.passed
                for confirmation in self.confirmations
            )
            and all(self.confirmation_state_matches)
        )


@dataclass(frozen=True, slots=True)
class CameraSessionResult:
    """Aggregate repeated-reacquisition result."""

    normalization_candidates: tuple[CameraPlan, ...]
    initial_normalization: CameraNormalizationResult
    trials: tuple[CameraTrialResult, ...]
    required_trials: int
    required_confirmations: int
    pre_perturbation_failure: CameraFrameRecord | None = None
    pre_perturbation_failure_trial_index_1_based: int | None = None

    def __post_init__(self) -> None:
        failure = self.pre_perturbation_failure
        failure_index = self.pre_perturbation_failure_trial_index_1_based
        if (failure is None) != (failure_index is None):
            raise ValueError(
                "pre-perturbation failure frame and trial index must be set together"
            )
        if failure is None:
            return
        if failure.evaluation.passed:
            raise ValueError("pre-perturbation failure frame cannot be a production pass")
        if failure_index != len(self.trials) + 1:
            raise ValueError(
                "pre-perturbation failure index must immediately follow completed trials"
            )

    @property
    def passed(self) -> bool:
        return (
            _normalization_result_matches_candidates(
                self.initial_normalization,
                self.normalization_candidates,
            )
            and all(
                _normalization_result_matches_candidates(
                    trial.normalization,
                    self.normalization_candidates,
                )
                for trial in self.trials
            )
            and self.initial_normalization.passed
            and self.pre_perturbation_failure is None
            and len(self.trials) >= self.required_trials
            and all(
                trial.passed
                and len(trial.confirmations) >= self.required_confirmations
                for trial in self.trials
            )
        )


class CameraFrameSource(Protocol):
    """Injected source of owned live frames."""

    def capture(self) -> Frame:
        """Capture one fresh frame."""


class CameraArtifactRecorder(Protocol):
    """Persist or identify one private frame before its payload is released."""

    def __call__(self, label: str, frame: Frame) -> CameraFrameArtifact: ...


def record_frame_digest(label: str, frame: Frame) -> CameraFrameArtifact:
    """Create in-memory provenance when no on-disk recorder is required."""

    return CameraFrameArtifact(
        label=label,
        frame_id=frame.frame_id,
        width=frame.width,
        height=frame.height,
        pixel_format=frame.pixel_format.value,
        raw_sha256=hashlib.sha256(frame.payload).hexdigest(),
    )


def run_camera_validation_session(
    source: CameraFrameSource,
    control: CameraControl,
    *,
    normalization_candidates: tuple[CameraPlan, ...],
    perturbation_plans: tuple[CameraPlan, ...],
    sleeper: Sleeper,
    settle_s: float,
    confirmation_frames: int,
    recorder: CameraArtifactRecorder = record_frame_digest,
) -> CameraSessionResult:
    """Run repeated perturb-and-reacquire trials with no diagnostic override."""

    _validate_session_inputs(
        normalization_candidates,
        perturbation_plans,
        settle_s=settle_s,
        confirmation_frames=confirmation_frames,
    )
    runner = CameraPlanRunner(control, sleeper)
    trials: list[CameraTrialResult] = []

    # Establish the exact same supported-view recipe used after every
    # perturbation before collecting the first baseline. This makes the
    # session independent of a manually prepared starting camera pose.
    initial_normalization = _run_normalization_candidates(
        source,
        recorder,
        normalization_candidates,
        runner=runner,
        sleeper=sleeper,
        settle_s=settle_s,
        label_prefix="initial-normalization",
    )
    if not initial_normalization.passed:
        return CameraSessionResult(
            normalization_candidates=normalization_candidates,
            initial_normalization=initial_normalization,
            trials=(),
            required_trials=MINIMUM_REACQUISITION_TRIALS,
            required_confirmations=confirmation_frames,
        )

    for trial_index, perturbation_plan in enumerate(perturbation_plans, start=1):
        before = _capture_record(
            source,
            recorder,
            f"trial-{trial_index:02d}-before",
        )
        if not before.evaluation.passed:
            # A fresh supported baseline is a prerequisite for deliberately
            # moving the camera.  Preserve the failed evidence and stop before
            # sending any perturbation input.
            return CameraSessionResult(
                normalization_candidates=normalization_candidates,
                initial_normalization=initial_normalization,
                trials=tuple(trials),
                required_trials=MINIMUM_REACQUISITION_TRIALS,
                required_confirmations=confirmation_frames,
                pre_perturbation_failure=before,
                pre_perturbation_failure_trial_index_1_based=trial_index,
            )

        # Any safety, focus, receipt, settle, capture, recording, or production
        # evaluation exception aborts immediately.  CameraPlanRunner and the
        # platform adapter own release-only cleanup; the session never sends a
        # new normalization sequence after an exceptional boundary.
        perturbation_receipt = runner.run(perturbation_plan)
        sleeper(settle_s)
        perturbed = _capture_record(
            source,
            recorder,
            f"trial-{trial_index:02d}-perturbed",
        )
        normalization = _run_normalization_candidates(
            source,
            recorder,
            normalization_candidates,
            runner=runner,
            sleeper=sleeper,
            settle_s=settle_s,
            label_prefix=f"trial-{trial_index:02d}-normalization",
        )

        confirmations: list[CameraFrameRecord] = []
        if normalization.passed:
            # The candidate-success frame is provisional evidence only.  The
            # required confirmations below are always fresh later captures.
            for confirmation_index in range(1, confirmation_frames + 1):
                sleeper(settle_s)
                confirmations.append(
                    _capture_record(
                        source,
                        recorder,
                        f"trial-{trial_index:02d}-after-{confirmation_index:02d}",
                    )
                )

        trials.append(
            CameraTrialResult(
                trial_index=trial_index,
                before=before,
                perturbation_plan=perturbation_plan,
                perturbation_receipt=perturbation_receipt,
                perturbed=perturbed,
                perturbation_fail_closed=_is_fail_closed_perturbation(
                    perturbed.evaluation
                ),
                normalization=normalization,
                confirmations=tuple(confirmations),
            )
        )
        if not normalization.passed:
            break

    return CameraSessionResult(
        normalization_candidates=normalization_candidates,
        initial_normalization=initial_normalization,
        trials=tuple(trials),
        required_trials=MINIMUM_REACQUISITION_TRIALS,
        required_confirmations=confirmation_frames,
    )


def _run_normalization_candidates(
    source: CameraFrameSource,
    recorder: CameraArtifactRecorder,
    candidates: tuple[CameraPlan, ...],
    *,
    runner: CameraPlanRunner,
    sleeper: Sleeper,
    settle_s: float,
    label_prefix: str,
) -> CameraNormalizationResult:
    """Execute independent candidates until unchanged production evaluation passes."""

    attempts: list[CameraNormalizationAttempt] = []
    for index, plan in enumerate(candidates, start=1):
        receipt = runner.run(plan)
        sleeper(settle_s)
        frame = _capture_record(
            source,
            recorder,
            f"{label_prefix}-candidate-{index:02d}",
        )
        attempt = CameraNormalizationAttempt(
            index=index,
            plan=plan,
            receipt=receipt,
            frame=frame,
        )
        attempts.append(attempt)
        if attempt.passed:
            return CameraNormalizationResult(
                attempts=tuple(attempts),
                selected_candidate_index_1_based=index,
                selected_identity=plan.name,
            )

    return CameraNormalizationResult(
        attempts=tuple(attempts),
        selected_candidate_index_1_based=None,
        selected_identity=None,
    )


def _capture_record(
    source: CameraFrameSource,
    recorder: CameraArtifactRecorder,
    label: str,
) -> CameraFrameRecord:
    frame = source.capture()
    artifact = recorder(label, frame)
    evaluation = evaluate_varrock_east_camera(frame)
    return CameraFrameRecord(artifact=artifact, evaluation=evaluation)


def _is_fail_closed_perturbation(evaluation: CameraEvaluation) -> bool:
    return (
        not evaluation.scene_validated
        and not evaluation.passed
        and not evaluation.definitive_target_ids
        and all(
            resource.state is ResourceVisualState.UNCERTAIN
            for resource in evaluation.resource_states
        )
    )


def _resource_state_vector(
    evaluation: CameraEvaluation,
) -> tuple[tuple[str, ResourceVisualState], ...]:
    """Project one evaluation to its exact ordered resource-state vector."""

    return tuple(
        (resource.resource_id, resource.state)
        for resource in evaluation.resource_states
    )


def _normalization_result_matches_candidates(
    result: CameraNormalizationResult,
    candidates: tuple[CameraPlan, ...],
) -> bool:
    attempted_plans = tuple(attempt.plan for attempt in result.attempts)
    if attempted_plans != candidates[: len(attempted_plans)]:
        return False
    return result.passed or len(attempted_plans) == len(candidates)


def _validate_session_inputs(
    normalization_candidates: tuple[CameraPlan, ...],
    perturbation_plans: tuple[CameraPlan, ...],
    *,
    settle_s: float,
    confirmation_frames: int,
) -> None:
    if not isinstance(normalization_candidates, tuple):
        raise ValueError("normalization_candidates must be a tuple")
    if not 1 <= len(normalization_candidates) <= MAXIMUM_NORMALIZATION_CANDIDATES:
        raise ValueError(
            "camera validation requires between 1 and "
            f"{MAXIMUM_NORMALIZATION_CANDIDATES} normalization candidates"
        )
    if len({plan.name for plan in normalization_candidates}) != len(
        normalization_candidates
    ):
        raise ValueError("normalization candidate names must be unique")
    if len({plan.actions for plan in normalization_candidates}) != len(
        normalization_candidates
    ):
        raise ValueError("normalization candidate action sequences must be distinct")
    if not isinstance(perturbation_plans, tuple):
        raise ValueError("perturbation_plans must be a tuple")
    if not MINIMUM_REACQUISITION_TRIALS <= len(perturbation_plans) <= (
        MAXIMUM_REACQUISITION_TRIALS
    ):
        raise ValueError(
            "camera validation requires between "
            f"{MINIMUM_REACQUISITION_TRIALS} and {MAXIMUM_REACQUISITION_TRIALS} "
            "perturbation trials"
        )
    if len({plan.name for plan in perturbation_plans}) != len(perturbation_plans):
        raise ValueError("perturbation plan names must be unique")
    if len({plan.actions for plan in perturbation_plans}) != len(perturbation_plans):
        raise ValueError("perturbation plan action sequences must be distinct")
    if (
        isinstance(settle_s, bool)
        or not isinstance(settle_s, (int, float))
        or not math.isfinite(settle_s)
        or not 0.0 < settle_s <= MAXIMUM_SETTLE_SECONDS
    ):
        raise ValueError(
            f"settle_s must be finite and in (0, {MAXIMUM_SETTLE_SECONDS}]"
        )
    if (
        isinstance(confirmation_frames, bool)
        or not isinstance(confirmation_frames, int)
        or not MINIMUM_CONFIRMATION_FRAMES
        <= confirmation_frames
        <= MAXIMUM_CONFIRMATION_FRAMES
    ):
        raise ValueError(
            "confirmation_frames must be between "
            f"{MINIMUM_CONFIRMATION_FRAMES} and {MAXIMUM_CONFIRMATION_FRAMES}"
        )
