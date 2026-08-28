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
    "CameraArtifactRecorder",
    "CameraFrameArtifact",
    "CameraFrameRecord",
    "CameraFrameSource",
    "CameraSessionResult",
    "CameraTrialResult",
    "record_frame_digest",
    "run_camera_validation_session",
]

MINIMUM_REACQUISITION_TRIALS = 3
MINIMUM_CONFIRMATION_FRAMES = 2
MAXIMUM_REACQUISITION_TRIALS = 12
MAXIMUM_CONFIRMATION_FRAMES = 5
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
class CameraTrialResult:
    """Before, perturbed, and normalized evidence for one fixed trial."""

    trial_index: int
    before: CameraFrameRecord
    perturbation_plan: CameraPlan
    perturbation_receipt: CameraPlanReceipt
    perturbed: CameraFrameRecord
    perturbation_fail_closed: bool
    normalization_receipt: CameraPlanReceipt
    confirmations: tuple[CameraFrameRecord, ...]

    @property
    def passed(self) -> bool:
        return (
            self.before.evaluation.passed
            and self.perturbation_fail_closed
            and all(
                confirmation.evaluation.passed
                for confirmation in self.confirmations
            )
        )


@dataclass(frozen=True, slots=True)
class CameraSessionResult:
    """Aggregate repeated-reacquisition result."""

    normalization_plan: CameraPlan
    initial_normalization_receipt: CameraPlanReceipt
    trials: tuple[CameraTrialResult, ...]
    required_trials: int
    required_confirmations: int

    @property
    def passed(self) -> bool:
        return (
            self.initial_normalization_receipt.plan == self.normalization_plan
            and self.initial_normalization_receipt.preflight.supported
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
    normalization_plan: CameraPlan,
    perturbation_plans: tuple[CameraPlan, ...],
    sleeper: Sleeper,
    settle_s: float,
    confirmation_frames: int,
    recorder: CameraArtifactRecorder = record_frame_digest,
) -> CameraSessionResult:
    """Run repeated perturb-and-reacquire trials with no diagnostic override."""

    _validate_session_inputs(
        perturbation_plans,
        settle_s=settle_s,
        confirmation_frames=confirmation_frames,
    )
    runner = CameraPlanRunner(control, sleeper)
    trials: list[CameraTrialResult] = []

    # Establish the exact same supported-view recipe used after every
    # perturbation before collecting the first baseline. This makes the
    # session independent of a manually prepared starting camera pose.
    initial_normalization_receipt = runner.run(normalization_plan)
    sleeper(settle_s)

    for trial_index, perturbation_plan in enumerate(perturbation_plans, start=1):
        before = _capture_record(
            source,
            recorder,
            f"trial-{trial_index:02d}-before",
        )
        # The restoration boundary begins before the first perturbation
        # action. A later action, settle, capture, or evaluation can fail after
        # earlier input already moved the camera, so every such path must still
        # attempt the same fixed normalization plan.
        try:
            perturbation_receipt = runner.run(perturbation_plan)
            sleeper(settle_s)
            perturbed = _capture_record(
                source,
                recorder,
                f"trial-{trial_index:02d}-perturbed",
            )
        finally:
            normalization_receipt = runner.run(normalization_plan)

        sleeper(settle_s)
        confirmations: list[CameraFrameRecord] = []
        for confirmation_index in range(1, confirmation_frames + 1):
            confirmations.append(
                _capture_record(
                    source,
                    recorder,
                    f"trial-{trial_index:02d}-after-{confirmation_index:02d}",
                )
            )
            if confirmation_index < confirmation_frames:
                sleeper(settle_s)

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
                normalization_receipt=normalization_receipt,
                confirmations=tuple(confirmations),
            )
        )

    return CameraSessionResult(
        normalization_plan=normalization_plan,
        initial_normalization_receipt=initial_normalization_receipt,
        trials=tuple(trials),
        required_trials=MINIMUM_REACQUISITION_TRIALS,
        required_confirmations=confirmation_frames,
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


def _validate_session_inputs(
    perturbation_plans: tuple[CameraPlan, ...],
    *,
    settle_s: float,
    confirmation_frames: int,
) -> None:
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
