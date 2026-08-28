from __future__ import annotations

import gzip
import hashlib
import inspect
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.validation import camera_session
from mining_automation.validation.camera_plan import (
    CameraHoldKey,
    CameraInputOperation,
    CameraInputReceipt,
    CameraKeyHold,
    CameraPlan,
    CameraPreflightReceipt,
    CompassClick,
)
from mining_automation.validation.camera_session import (
    CameraFrameArtifact,
    record_frame_digest,
    run_camera_validation_session,
)

FIXTURE_ROOT = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "perception"
    / "varrock-east-iron-v1"
)


def _reviewed_frame(*, frame_id: int) -> Frame:
    payload = gzip.decompress((FIXTURE_ROOT / "frames" / "available-01.raw.gz").read_bytes())
    return Frame.from_raw(
        RawFrame(payload, 1005, 1078, PixelFormat.BGRA8888),
        frame_id=frame_id,
        captured_monotonic_s=float(frame_id),
    )


def _unsupported_frame(*, frame_id: int) -> Frame:
    source = _reviewed_frame(frame_id=frame_id)
    return Frame.from_raw(
        RawFrame(bytes(len(source.payload)), 1005, 1078, PixelFormat.BGRA8888),
        frame_id=frame_id,
        captured_monotonic_s=float(frame_id),
    )


class SequenceSource:
    def __init__(self, frames: list[Frame]) -> None:
        self.frames = frames

    def capture(self) -> Frame:
        if not self.frames:
            raise AssertionError("unexpected capture")
        return self.frames.pop(0)


class CompleteControl:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def preflight(self) -> CameraPreflightReceipt:
        self.calls.append("preflight")
        return CameraPreflightReceipt(True, 1005, 1078)

    def click_compass(self, x: int, y: int) -> CameraInputReceipt:
        self.calls.append(("click", x, y))
        return CameraInputReceipt(CameraInputOperation.COMPASS_CLICK, 2, 2)

    def key_down(self, key: str) -> CameraInputReceipt:
        self.calls.append(("down", key))
        return CameraInputReceipt(CameraInputOperation.KEY_DOWN, 1, 1)

    def key_up(self, key: str) -> CameraInputReceipt:
        self.calls.append(("up", key))
        return CameraInputReceipt(CameraInputOperation.KEY_UP, 1, 1)

    def scroll_camera(self, x: int, y: int, detents: int) -> CameraInputReceipt:
        self.calls.append(("wheel", x, y, detents))
        return CameraInputReceipt(CameraInputOperation.CAMERA_WHEEL, abs(detents), abs(detents))


class FailSecondKeyDownControl(CompleteControl):
    def __init__(self) -> None:
        super().__init__()
        self.key_down_count = 0

    def key_down(self, key: str) -> CameraInputReceipt:
        self.key_down_count += 1
        if self.key_down_count == 2:
            self.calls.append(("down-failed", key))
            raise OSError("second perturbation action failed")
        return super().key_down(key)


def _plans() -> tuple[CameraPlan, tuple[CameraPlan, ...]]:
    normalization = CameraPlan("normalize", (CompassClick(608, 49),))
    perturbations = tuple(
        CameraPlan(
            f"perturb-{index}",
            (CameraKeyHold(CameraHoldKey.RIGHT, 0.1 * index),),
        )
        for index in range(1, 4)
    )
    return normalization, perturbations


def _passing_sequence() -> list[Frame]:
    frames: list[Frame] = []
    frame_id = 1
    for _trial in range(3):
        frames.append(_reviewed_frame(frame_id=frame_id))
        frames.append(_unsupported_frame(frame_id=frame_id + 1))
        frames.append(_reviewed_frame(frame_id=frame_id + 2))
        frames.append(_reviewed_frame(frame_id=frame_id + 3))
        frame_id += 4
    return frames


def test_three_distinct_perturbations_reacquire_with_two_confirmations() -> None:
    normalization, perturbations = _plans()
    control = CompleteControl()
    sleeps: list[float] = []

    result = run_camera_validation_session(
        SequenceSource(_passing_sequence()),
        control,
        normalization_plan=normalization,
        perturbation_plans=perturbations,
        sleeper=sleeps.append,
        settle_s=0.25,
        confirmation_frames=2,
    )

    assert result.passed
    assert len(result.trials) == 3
    assert all(trial.perturbation_fail_closed for trial in result.trials)
    assert all(len(trial.confirmations) == 2 for trial in result.trials)
    assert all(
        confirmation.evaluation.passed
        for trial in result.trials
        for confirmation in trial.confirmations
    )
    assert result.initial_normalization_receipt.plan is normalization
    assert result.initial_normalization_receipt.preflight.supported
    assert control.calls.count("preflight") == 7
    assert sleeps.count(0.25) == 10

    shortened = replace(
        result,
        trials=(
            replace(result.trials[0], confirmations=result.trials[0].confirmations[:1]),
            *result.trials[1:],
        ),
    )
    assert not shortened.passed


def test_initial_normalization_and_settle_precede_first_before_recording() -> None:
    normalization, perturbations = _plans()
    control = CompleteControl()

    def sleeper(duration_s: float) -> None:
        control.calls.append(("sleep", duration_s))

    def recorder(label: str, frame: Frame) -> CameraFrameArtifact:
        control.calls.append(("record", label))
        return record_frame_digest(label, frame)

    result = run_camera_validation_session(
        SequenceSource(_passing_sequence()),
        control,
        normalization_plan=normalization,
        perturbation_plans=perturbations,
        sleeper=sleeper,
        settle_s=0.25,
        confirmation_frames=2,
        recorder=recorder,
    )

    assert result.passed
    assert result.initial_normalization_receipt.plan is normalization
    assert control.calls[:4] == [
        "preflight",
        ("click", 608, 49),
        ("sleep", 0.25),
        ("record", "trial-01-before"),
    ]


def test_definitive_perturbed_frame_cannot_count_as_a_valid_trial() -> None:
    normalization, perturbations = _plans()
    frames = _passing_sequence()
    frames[1] = _reviewed_frame(frame_id=2)

    result = run_camera_validation_session(
        SequenceSource(frames),
        CompleteControl(),
        normalization_plan=normalization,
        perturbation_plans=perturbations,
        sleeper=lambda _seconds: None,
        settle_s=0.1,
        confirmation_frames=2,
    )

    assert not result.passed
    assert not result.trials[0].perturbation_fail_closed
    assert result.trials[0].perturbed.evaluation.passed


def test_failed_confirmation_cannot_be_overridden_by_other_passes() -> None:
    normalization, perturbations = _plans()
    frames = _passing_sequence()
    frames[3] = _unsupported_frame(frame_id=4)

    result = run_camera_validation_session(
        SequenceSource(frames),
        CompleteControl(),
        normalization_plan=normalization,
        perturbation_plans=perturbations,
        sleeper=lambda _seconds: None,
        settle_s=0.1,
        confirmation_frames=2,
    )

    assert not result.passed
    assert not result.trials[0].passed
    assert not result.trials[0].confirmations[1].evaluation.passed


def test_unsupported_before_frame_cannot_be_overridden_by_reacquisition() -> None:
    normalization, perturbations = _plans()
    frames = _passing_sequence()
    frames[0] = _unsupported_frame(frame_id=1)

    result = run_camera_validation_session(
        SequenceSource(frames),
        CompleteControl(),
        normalization_plan=normalization,
        perturbation_plans=perturbations,
        sleeper=lambda _seconds: None,
        settle_s=0.1,
        confirmation_frames=2,
    )

    assert not result.passed
    assert not result.trials[0].passed
    assert not result.trials[0].before.evaluation.passed
    assert all(
        confirmation.evaluation.passed
        for confirmation in result.trials[0].confirmations
    )


def test_recorder_runs_before_evaluator_and_receives_every_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    normalization, perturbations = _plans()
    events: list[str] = []
    production_evaluator = camera_session.evaluate_varrock_east_camera

    def recorder(label: str, frame: Frame) -> CameraFrameArtifact:
        events.append(f"record:{label}")
        return record_frame_digest(label, frame)

    def recording_evaluator(frame: Frame):  # type: ignore[no-untyped-def]
        events.append(f"evaluate:{frame.frame_id}")
        return production_evaluator(frame)

    monkeypatch.setattr(
        camera_session,
        "evaluate_varrock_east_camera",
        recording_evaluator,
    )

    result = run_camera_validation_session(
        SequenceSource(_passing_sequence()),
        CompleteControl(),
        normalization_plan=normalization,
        perturbation_plans=perturbations,
        sleeper=lambda _seconds: None,
        settle_s=0.1,
        confirmation_frames=2,
        recorder=recorder,
    )

    assert result.passed
    assert len(events) == 24
    assert all(
        events[index].startswith("record:") and events[index + 1].startswith("evaluate:")
        for index in range(0, len(events), 2)
    )


def test_public_session_signature_cannot_inject_an_evaluator() -> None:
    assert "evaluator" not in inspect.signature(run_camera_validation_session).parameters

    normalization, perturbations = _plans()
    unsafe_call = cast(Any, run_camera_validation_session)
    with pytest.raises(TypeError, match="unexpected keyword argument 'evaluator'"):
        unsafe_call(
            SequenceSource([]),
            CompleteControl(),
            normalization_plan=normalization,
            perturbation_plans=perturbations,
            sleeper=lambda _seconds: None,
            settle_s=0.1,
            confirmation_frames=2,
            evaluator=lambda _frame: None,
        )


@pytest.mark.parametrize(
    "perturbation_count",
    [0, 1, 2, 13],
)
def test_session_rejects_insufficient_or_unbounded_trial_counts(
    perturbation_count: int,
) -> None:
    normalization, perturbations = _plans()
    repeated = tuple(
        CameraPlan(f"p-{index}", (CompassClick(608, 49),))
        for index in range(perturbation_count)
    )

    with pytest.raises(ValueError, match="between 3 and 12"):
        run_camera_validation_session(
            SequenceSource([]),
            CompleteControl(),
            normalization_plan=normalization,
            perturbation_plans=repeated,
            sleeper=lambda _seconds: None,
            settle_s=0.1,
            confirmation_frames=2,
        )


@pytest.mark.parametrize("confirmations", [0, 1, 6, True])
def test_session_requires_multiple_bounded_confirmation_frames(
    confirmations: int,
) -> None:
    normalization, perturbations = _plans()

    with pytest.raises(ValueError, match="confirmation_frames"):
        run_camera_validation_session(
            SequenceSource([]),
            CompleteControl(),
            normalization_plan=normalization,
            perturbation_plans=perturbations,
            sleeper=lambda _seconds: None,
            settle_s=0.1,
            confirmation_frames=confirmations,
        )


def test_session_requires_distinct_perturbation_action_sequences() -> None:
    normalization, _ = _plans()
    duplicate_action = (CameraKeyHold(CameraHoldKey.RIGHT, 0.25),)
    duplicate_perturbations = tuple(
        CameraPlan(f"different-name-{index}", duplicate_action)
        for index in range(3)
    )

    with pytest.raises(ValueError, match="action sequences must be distinct"):
        run_camera_validation_session(
            SequenceSource([]),
            CompleteControl(),
            normalization_plan=normalization,
            perturbation_plans=duplicate_perturbations,
            sleeper=lambda _seconds: None,
            settle_s=0.1,
            confirmation_frames=2,
        )


def test_capture_failure_after_perturbation_still_runs_normalization() -> None:
    normalization, perturbations = _plans()
    source = SequenceSource([_reviewed_frame(frame_id=1)])
    control = CompleteControl()

    with pytest.raises(AssertionError, match="unexpected capture"):
        run_camera_validation_session(
            source,
            control,
            normalization_plan=normalization,
            perturbation_plans=perturbations,
            sleeper=lambda _seconds: None,
            settle_s=0.1,
            confirmation_frames=2,
        )

    assert control.calls.count(("click", 608, 49)) == 2


def test_failed_second_perturbation_action_still_runs_normalization() -> None:
    normalization, perturbations = _plans()
    two_action_failure = CameraPlan(
        "two-action-failure",
        (
            CameraKeyHold(CameraHoldKey.RIGHT, 0.1),
            CameraKeyHold(CameraHoldKey.LEFT, 0.1),
        ),
    )
    plans = (two_action_failure, perturbations[1], perturbations[2])
    control = FailSecondKeyDownControl()

    with pytest.raises(OSError, match="second perturbation action failed"):
        run_camera_validation_session(
            SequenceSource([_reviewed_frame(frame_id=1)]),
            control,
            normalization_plan=normalization,
            perturbation_plans=plans,
            sleeper=lambda _seconds: None,
            settle_s=0.1,
            confirmation_frames=2,
        )

    # A failed key-down does not establish ownership of that key, so the
    # runner must not release a key that could have been held by the user.
    assert ("up", "left") not in control.calls
    assert control.calls.count(("click", 608, 49)) == 2


def test_artifact_digest_is_exact_and_frame_payload_is_not_retained() -> None:
    frame = _reviewed_frame(frame_id=11)

    artifact = record_frame_digest("sample", frame)

    assert artifact.raw_sha256 == hashlib.sha256(frame.payload).hexdigest()
    assert not hasattr(artifact, "payload")
