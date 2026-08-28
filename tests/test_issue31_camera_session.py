from __future__ import annotations

import gzip
import hashlib
import inspect
from dataclasses import FrozenInstanceError, replace
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
    CameraPause,
    CameraPlan,
    CameraPlanRunner,
    CameraPreflightError,
    CameraPreflightReceipt,
    CameraReceiptError,
    CompassClick,
)
from mining_automation.validation.camera_session import (
    CameraFrameArtifact,
    CameraNormalizationResult,
    record_frame_digest,
    run_camera_validation_session,
)

FIXTURE_ROOT = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "perception"
    / "varrock-east-iron-v1"
)


def _reviewed_case_frame(case_id: str, *, frame_id: int) -> Frame:
    payload = gzip.decompress(
        (FIXTURE_ROOT / "frames" / f"{case_id}.raw.gz").read_bytes()
    )
    return Frame.from_raw(
        RawFrame(payload, 1005, 1078, PixelFormat.BGRA8888),
        frame_id=frame_id,
        captured_monotonic_s=float(frame_id),
    )


def _reviewed_frame(*, frame_id: int) -> Frame:
    return _reviewed_case_frame("available-01", frame_id=frame_id)


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


class ShortCompassControl(CompleteControl):
    def click_compass(self, x: int, y: int) -> CameraInputReceipt:
        self.calls.append(("short-click", x, y))
        return CameraInputReceipt(CameraInputOperation.COMPASS_CLICK, 2, 1)


class ShortSecondCompassControl(CompleteControl):
    def __init__(self) -> None:
        super().__init__()
        self.compass_count = 0

    def click_compass(self, x: int, y: int) -> CameraInputReceipt:
        self.compass_count += 1
        self.calls.append(("click", x, y))
        if self.compass_count == 2:
            return CameraInputReceipt(CameraInputOperation.COMPASS_CLICK, 2, 1)
        return CameraInputReceipt(CameraInputOperation.COMPASS_CLICK, 2, 2)


class UnsupportedPreflightControl(CompleteControl):
    def preflight(self) -> CameraPreflightReceipt:
        self.calls.append("unsupported-preflight")
        return CameraPreflightReceipt(False, 1005, 1078)


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


def _normalization_candidates() -> tuple[CameraPlan, CameraPlan]:
    return (
        CameraPlan("normalize-primary", (CompassClick(608, 49),)),
        CameraPlan(
            "normalize-secondary",
            (CompassClick(608, 49), CameraPause(0.01)),
        ),
    )


def _passing_sequence() -> list[Frame]:
    frames: list[Frame] = [_reviewed_frame(frame_id=1)]
    frame_id = 2
    for _trial in range(3):
        frames.append(_reviewed_frame(frame_id=frame_id))
        frames.append(_unsupported_frame(frame_id=frame_id + 1))
        frames.append(_reviewed_frame(frame_id=frame_id + 2))
        frames.append(_reviewed_frame(frame_id=frame_id + 3))
        frames.append(_reviewed_frame(frame_id=frame_id + 4))
        frame_id += 5
    return frames


def _fallback_candidate_passing_sequence() -> list[Frame]:
    frames = [
        _unsupported_frame(frame_id=1),
        _reviewed_frame(frame_id=2),
    ]
    frame_id = 3
    for _trial in range(3):
        frames.extend(
            (
                _reviewed_frame(frame_id=frame_id),
                _unsupported_frame(frame_id=frame_id + 1),
                _unsupported_frame(frame_id=frame_id + 2),
                _reviewed_frame(frame_id=frame_id + 3),
                _reviewed_frame(frame_id=frame_id + 4),
                _reviewed_frame(frame_id=frame_id + 5),
            )
        )
        frame_id += 6
    return frames


def _two_attempt_normalization_result() -> CameraNormalizationResult:
    candidates = _normalization_candidates()
    return camera_session._run_normalization_candidates(
        SequenceSource(
            [
                _unsupported_frame(frame_id=1),
                _reviewed_frame(frame_id=2),
            ]
        ),
        record_frame_digest,
        candidates,
        runner=CameraPlanRunner(CompleteControl(), lambda _seconds: None),
        sleeper=lambda _seconds: None,
        settle_s=0.1,
        label_prefix="unit-normalization",
    )


def test_three_distinct_perturbations_reacquire_with_two_confirmations() -> None:
    normalization, perturbations = _plans()
    control = CompleteControl()
    sleeps: list[float] = []

    result = run_camera_validation_session(
        SequenceSource(_passing_sequence()),
        control,
        normalization_candidates=(normalization,),
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
    assert result.normalization_candidates == (normalization,)
    assert result.initial_normalization.passed
    assert result.initial_normalization.selected_candidate_index_1_based == 1
    assert result.initial_normalization.selected_identity == "normalize"
    assert result.initial_normalization.attempts[0].plan is normalization
    assert result.initial_normalization.attempts[0].receipt.preflight.supported
    assert all(trial.normalization.passed for trial in result.trials)
    assert control.calls.count("preflight") == 7
    assert sleeps.count(0.25) == 13

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
        normalization_candidates=(normalization,),
        perturbation_plans=perturbations,
        sleeper=sleeper,
        settle_s=0.25,
        confirmation_frames=2,
        recorder=recorder,
    )

    assert result.passed
    assert result.initial_normalization.attempts[0].plan is normalization
    assert control.calls[:5] == [
        "preflight",
        ("click", 608, 49),
        ("sleep", 0.25),
        ("record", "initial-normalization-candidate-01"),
        ("record", "trial-01-before"),
    ]


def test_each_normalization_boundary_uses_ordered_production_gated_fallback() -> None:
    candidates = _normalization_candidates()
    _, perturbations = _plans()
    control = CompleteControl()
    labels: list[str] = []

    def recorder(label: str, frame: Frame) -> CameraFrameArtifact:
        labels.append(label)
        return record_frame_digest(label, frame)

    result = run_camera_validation_session(
        SequenceSource(_fallback_candidate_passing_sequence()),
        control,
        normalization_candidates=candidates,
        perturbation_plans=perturbations,
        sleeper=lambda _seconds: None,
        settle_s=0.1,
        confirmation_frames=2,
        recorder=recorder,
    )

    normalizations = (
        result.initial_normalization,
        *(trial.normalization for trial in result.trials),
    )
    assert result.passed
    assert len(normalizations) == 4
    assert all(
        tuple(attempt.passed for attempt in normalization.attempts)
        == (False, True)
        for normalization in normalizations
    )
    assert all(
        normalization.selected_candidate_index_1_based == 2
        and normalization.selected_identity == "normalize-secondary"
        for normalization in normalizations
    )
    assert control.calls.count("preflight") == 11
    assert labels[:3] == [
        "initial-normalization-candidate-01",
        "initial-normalization-candidate-02",
        "trial-01-before",
    ]
    assert labels[4:8] == [
        "trial-01-normalization-candidate-01",
        "trial-01-normalization-candidate-02",
        "trial-01-after-01",
        "trial-01-after-02",
    ]
    assert all(
        trial.normalization.attempts[-1].frame.artifact.frame_id
        < trial.confirmations[0].artifact.frame_id
        < trial.confirmations[1].artifact.frame_id
        for trial in result.trials
    )


def test_first_passing_candidate_stops_without_executing_later_candidate() -> None:
    candidates = _normalization_candidates()
    _, perturbations = _plans()
    sleeps: list[float] = []

    result = run_camera_validation_session(
        SequenceSource(_passing_sequence()),
        CompleteControl(),
        normalization_candidates=candidates,
        perturbation_plans=perturbations,
        sleeper=sleeps.append,
        settle_s=0.1,
        confirmation_frames=2,
    )

    normalizations = (
        result.initial_normalization,
        *(trial.normalization for trial in result.trials),
    )
    assert result.passed
    assert all(len(normalization.attempts) == 1 for normalization in normalizations)
    assert all(
        normalization.selected_candidate_index_1_based == 1
        and normalization.selected_identity == "normalize-primary"
        for normalization in normalizations
    )
    assert 0.01 not in sleeps


def test_initial_candidate_exhaustion_returns_reportable_failure_without_trial() -> None:
    candidates = _normalization_candidates()
    _, perturbations = _plans()
    source = SequenceSource(
        [
            _unsupported_frame(frame_id=1),
            _unsupported_frame(frame_id=2),
        ]
    )
    control = CompleteControl()

    result = run_camera_validation_session(
        source,
        control,
        normalization_candidates=candidates,
        perturbation_plans=perturbations,
        sleeper=lambda _seconds: None,
        settle_s=0.1,
        confirmation_frames=2,
    )

    assert not result.passed
    assert result.trials == ()
    assert not result.initial_normalization.passed
    assert result.initial_normalization.selected_candidate_index_1_based is None
    assert result.initial_normalization.selected_identity is None
    assert tuple(
        attempt.passed for attempt in result.initial_normalization.attempts
    ) == (False, False)
    assert control.calls.count("preflight") == 2
    assert source.frames == []


def test_per_trial_candidate_exhaustion_stops_before_confirmation_or_next_trial() -> None:
    candidates = _normalization_candidates()
    _, perturbations = _plans()
    source = SequenceSource(
        [
            _reviewed_frame(frame_id=1),
            _reviewed_frame(frame_id=2),
            _unsupported_frame(frame_id=3),
            _unsupported_frame(frame_id=4),
            _unsupported_frame(frame_id=5),
        ]
    )
    control = CompleteControl()

    result = run_camera_validation_session(
        source,
        control,
        normalization_candidates=candidates,
        perturbation_plans=perturbations,
        sleeper=lambda _seconds: None,
        settle_s=0.1,
        confirmation_frames=2,
    )

    assert not result.passed
    assert len(result.trials) == 1
    trial = result.trials[0]
    assert trial.perturbation_fail_closed
    assert not trial.normalization.passed
    assert tuple(attempt.passed for attempt in trial.normalization.attempts) == (
        False,
        False,
    )
    assert trial.confirmations == ()
    assert control.calls.count("preflight") == 4
    assert source.frames == []


def test_normalization_attempt_and_result_are_immutable_production_evidence() -> None:
    result = _two_attempt_normalization_result()
    first, selected = result.attempts

    assert first.index == 1
    assert not first.passed
    assert first.passed is first.frame.evaluation.passed
    assert selected.index == 2
    assert selected.passed
    assert selected.passed is selected.frame.evaluation.passed
    assert result.passed

    with pytest.raises(FrozenInstanceError):
        cast(Any, first).index = 7
    with pytest.raises(FrozenInstanceError):
        cast(Any, result).selected_identity = "changed"


def test_normalization_attempt_rejects_bad_index_or_mismatched_receipt() -> None:
    result = _two_attempt_normalization_result()
    first, selected = result.attempts

    with pytest.raises(ValueError, match="positive integer"):
        replace(first, index=cast(Any, True))
    with pytest.raises(ValueError, match="receipt must match"):
        replace(first, plan=selected.plan)


def test_normalization_result_rejects_unordered_or_inconsistent_selection() -> None:
    result = _two_attempt_normalization_result()
    first, selected = result.attempts

    with pytest.raises(ValueError, match="must be a tuple"):
        CameraNormalizationResult(
            attempts=cast(Any, list(result.attempts)),
            selected_candidate_index_1_based=2,
            selected_identity=selected.plan.name,
        )
    with pytest.raises(ValueError, match="contiguous ordered indexes"):
        replace(result, attempts=(replace(first, index=2), selected))
    with pytest.raises(ValueError, match="final recorded attempt"):
        replace(result, selected_candidate_index_1_based=1)
    with pytest.raises(ValueError, match="identity must match"):
        replace(result, selected_identity="not-the-selected-plan")
    with pytest.raises(ValueError, match="passing attempt must be selected"):
        replace(
            result,
            selected_candidate_index_1_based=None,
            selected_identity=None,
        )
    with pytest.raises(ValueError, match="before selection must fail"):
        replace(
            result,
            attempts=(replace(first, frame=selected.frame), selected),
        )
    with pytest.raises(ValueError, match="selected.*must pass"):
        CameraNormalizationResult(
            attempts=(first,),
            selected_candidate_index_1_based=1,
            selected_identity=first.plan.name,
        )
    with pytest.raises(ValueError, match="must be None"):
        CameraNormalizationResult(
            attempts=(first,),
            selected_candidate_index_1_based=None,
            selected_identity=first.plan.name,
        )


def test_session_pass_requires_results_to_match_declared_candidate_order() -> None:
    candidates = _normalization_candidates()
    _, perturbations = _plans()
    result = run_camera_validation_session(
        SequenceSource(_fallback_candidate_passing_sequence()),
        CompleteControl(),
        normalization_candidates=candidates,
        perturbation_plans=perturbations,
        sleeper=lambda _seconds: None,
        settle_s=0.1,
        confirmation_frames=2,
    )

    assert result.passed
    assert not replace(
        result,
        normalization_candidates=tuple(reversed(candidates)),
    ).passed


def test_definitive_perturbed_frame_cannot_count_as_a_valid_trial() -> None:
    normalization, perturbations = _plans()
    frames = _passing_sequence()
    frames[2] = _reviewed_frame(frame_id=3)

    result = run_camera_validation_session(
        SequenceSource(frames),
        CompleteControl(),
        normalization_candidates=(normalization,),
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
    frames[5] = _unsupported_frame(frame_id=6)

    result = run_camera_validation_session(
        SequenceSource(frames),
        CompleteControl(),
        normalization_candidates=(normalization,),
        perturbation_plans=perturbations,
        sleeper=lambda _seconds: None,
        settle_s=0.1,
        confirmation_frames=2,
    )

    assert not result.passed
    assert not result.trials[0].passed
    assert not result.trials[0].confirmations[1].evaluation.passed


def test_definitive_confirmation_with_changed_state_vector_fails_session() -> None:
    normalization, perturbations = _plans()
    frames = _passing_sequence()
    frames[4] = _reviewed_case_frame(
        "lower-left-full-cycle-020",
        frame_id=5,
    )

    result = run_camera_validation_session(
        SequenceSource(frames),
        CompleteControl(),
        normalization_candidates=(normalization,),
        perturbation_plans=perturbations,
        sleeper=lambda _seconds: None,
        settle_s=0.1,
        confirmation_frames=2,
    )

    first_trial = result.trials[0]
    assert first_trial.before.evaluation.passed
    assert all(
        confirmation.evaluation.passed
        for confirmation in first_trial.confirmations
    )
    assert first_trial.confirmation_state_matches == (False, True)
    assert sum(
        before_state != confirmation_state
        for (_, before_state), (_, confirmation_state) in zip(
            first_trial.expected_resource_state_vector,
            (
                (item.resource_id, item.state)
                for item in first_trial.confirmations[0].evaluation.resource_states
            ),
            strict=True,
        )
    ) == 1
    assert not first_trial.passed
    assert not result.passed


def test_unsupported_before_frame_cannot_be_overridden_by_reacquisition() -> None:
    normalization, perturbations = _plans()
    frames = _passing_sequence()
    frames[1] = _unsupported_frame(frame_id=2)
    control = CompleteControl()

    result = run_camera_validation_session(
        SequenceSource(frames),
        control,
        normalization_candidates=(normalization,),
        perturbation_plans=perturbations,
        sleeper=lambda _seconds: None,
        settle_s=0.1,
        confirmation_frames=2,
    )

    assert not result.passed
    assert result.trials == ()
    assert result.pre_perturbation_failure is not None
    assert not result.pre_perturbation_failure.evaluation.passed
    assert result.pre_perturbation_failure_trial_index_1_based == 1
    assert control.calls == ["preflight", ("click", 608, 49)]


def test_pre_perturbation_failure_evidence_validates_as_an_atomic_pair() -> None:
    normalization, perturbations = _plans()
    frames = _passing_sequence()
    frames[1] = _unsupported_frame(frame_id=2)
    result = run_camera_validation_session(
        SequenceSource(frames),
        CompleteControl(),
        normalization_candidates=(normalization,),
        perturbation_plans=perturbations,
        sleeper=lambda _seconds: None,
        settle_s=0.1,
        confirmation_frames=2,
    )

    with pytest.raises(ValueError, match="must be set together"):
        replace(result, pre_perturbation_failure=None)
    with pytest.raises(ValueError, match="cannot be a production pass"):
        replace(
            result,
            pre_perturbation_failure=result.initial_normalization.attempts[0].frame,
        )
    with pytest.raises(ValueError, match="immediately follow completed trials"):
        replace(result, pre_perturbation_failure_trial_index_1_based=2)


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
        normalization_candidates=(normalization,),
        perturbation_plans=perturbations,
        sleeper=lambda _seconds: None,
        settle_s=0.1,
        confirmation_frames=2,
        recorder=recorder,
    )

    assert result.passed
    assert len(events) == 32
    assert all(
        events[index].startswith("record:") and events[index + 1].startswith("evaluate:")
        for index in range(0, len(events), 2)
    )


def test_public_session_signature_cannot_inject_an_evaluator() -> None:
    parameters = inspect.signature(run_camera_validation_session).parameters
    assert "evaluator" not in parameters
    assert "normalization_candidates" in parameters
    assert "normalization_plan" not in parameters

    normalization, perturbations = _plans()
    unsafe_call = cast(Any, run_camera_validation_session)
    with pytest.raises(TypeError, match="unexpected keyword argument 'evaluator'"):
        unsafe_call(
            SequenceSource([]),
            CompleteControl(),
            normalization_candidates=(normalization,),
            perturbation_plans=perturbations,
            sleeper=lambda _seconds: None,
            settle_s=0.1,
            confirmation_frames=2,
            evaluator=lambda _frame: None,
        )


@pytest.mark.parametrize("candidate_count", [0, 13])
def test_session_rejects_empty_or_unbounded_candidate_search(
    candidate_count: int,
) -> None:
    _, perturbations = _plans()
    candidates = tuple(
        CameraPlan(
            f"candidate-{index}",
            (CameraKeyHold(CameraHoldKey.UP, 0.01 * (index + 1)),),
        )
        for index in range(candidate_count)
    )

    with pytest.raises(ValueError, match="between 1 and 12"):
        run_camera_validation_session(
            SequenceSource([]),
            CompleteControl(),
            normalization_candidates=candidates,
            perturbation_plans=perturbations,
            sleeper=lambda _seconds: None,
            settle_s=0.1,
            confirmation_frames=2,
        )


def test_session_requires_frozen_candidate_tuple() -> None:
    normalization, perturbations = _plans()
    unsafe_call = cast(Any, run_camera_validation_session)

    with pytest.raises(ValueError, match="normalization_candidates must be a tuple"):
        unsafe_call(
            SequenceSource([]),
            CompleteControl(),
            normalization_candidates=[normalization],
            perturbation_plans=perturbations,
            sleeper=lambda _seconds: None,
            settle_s=0.1,
            confirmation_frames=2,
        )


def test_session_requires_unique_candidate_names_and_actions() -> None:
    _, perturbations = _plans()
    click = CompassClick(608, 49)

    with pytest.raises(ValueError, match="candidate names must be unique"):
        run_camera_validation_session(
            SequenceSource([]),
            CompleteControl(),
            normalization_candidates=(
                CameraPlan("same", (click,)),
                CameraPlan("same", (click, CameraPause(0.1))),
            ),
            perturbation_plans=perturbations,
            sleeper=lambda _seconds: None,
            settle_s=0.1,
            confirmation_frames=2,
        )

    with pytest.raises(ValueError, match="action sequences must be distinct"):
        run_camera_validation_session(
            SequenceSource([]),
            CompleteControl(),
            normalization_candidates=(
                CameraPlan("first", (click,)),
                CameraPlan("second", (click,)),
            ),
            perturbation_plans=perturbations,
            sleeper=lambda _seconds: None,
            settle_s=0.1,
            confirmation_frames=2,
        )


@pytest.mark.parametrize(
    ("control", "error"),
    [
        (UnsupportedPreflightControl(), CameraPreflightError),
        (ShortCompassControl(), CameraReceiptError),
    ],
)
def test_candidate_safety_or_receipt_error_aborts_without_fallback(
    control: CompleteControl,
    error: type[Exception],
) -> None:
    candidates = _normalization_candidates()
    _, perturbations = _plans()
    source = SequenceSource([_reviewed_frame(frame_id=1)])

    with pytest.raises(error):
        run_camera_validation_session(
            source,
            control,
            normalization_candidates=candidates,
            perturbation_plans=perturbations,
            sleeper=lambda _seconds: None,
            settle_s=0.1,
            confirmation_frames=2,
        )

    assert control.calls.count("preflight") + control.calls.count(
        "unsupported-preflight"
    ) == 1
    assert source.frames != []


def test_per_trial_normalization_receipt_error_aborts_without_fallback() -> None:
    candidates = _normalization_candidates()
    _, perturbations = _plans()
    source = SequenceSource(
        [
            _reviewed_frame(frame_id=1),
            _reviewed_frame(frame_id=2),
            _unsupported_frame(frame_id=3),
            _reviewed_frame(frame_id=4),
        ]
    )
    control = ShortSecondCompassControl()

    with pytest.raises(CameraReceiptError):
        run_camera_validation_session(
            source,
            control,
            normalization_candidates=candidates,
            perturbation_plans=perturbations,
            sleeper=lambda _seconds: None,
            settle_s=0.1,
            confirmation_frames=2,
        )

    # Initial candidate 1 passed.  The first per-trial candidate then returned
    # a short receipt, so candidate 2 was never sent and no candidate frame or
    # confirmation was captured after the exceptional boundary.
    assert control.compass_count == 2
    assert control.calls.count("preflight") == 3
    assert len(source.frames) == 1


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
            normalization_candidates=(normalization,),
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
            normalization_candidates=(normalization,),
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
            normalization_candidates=(normalization,),
            perturbation_plans=duplicate_perturbations,
            sleeper=lambda _seconds: None,
            settle_s=0.1,
            confirmation_frames=2,
        )


def test_capture_failure_after_perturbation_aborts_without_more_input() -> None:
    normalization, perturbations = _plans()
    source = SequenceSource(
        [
            _reviewed_frame(frame_id=1),
            _reviewed_frame(frame_id=2),
        ]
    )
    control = CompleteControl()

    with pytest.raises(AssertionError, match="unexpected capture"):
        run_camera_validation_session(
            source,
            control,
            normalization_candidates=(normalization,),
            perturbation_plans=perturbations,
            sleeper=lambda _seconds: None,
            settle_s=0.1,
            confirmation_frames=2,
        )

    assert control.calls.count(("click", 608, 49)) == 1
    assert control.calls.count("preflight") == 2


def test_failed_second_perturbation_action_aborts_without_normalization() -> None:
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
            SequenceSource(
                [
                    _reviewed_frame(frame_id=1),
                    _reviewed_frame(frame_id=2),
                    _reviewed_frame(frame_id=3),
                ]
            ),
            control,
            normalization_candidates=(normalization,),
            perturbation_plans=plans,
            sleeper=lambda _seconds: None,
            settle_s=0.1,
            confirmation_frames=2,
        )

    # A failed key-down does not establish ownership of that key, so the
    # runner must not release a key that could have been held by the user.
    assert ("up", "left") not in control.calls
    assert control.calls.count(("click", 608, 49)) == 1
    assert control.calls.count("preflight") == 2


def test_artifact_digest_is_exact_and_frame_payload_is_not_retained() -> None:
    frame = _reviewed_frame(frame_id=11)

    artifact = record_frame_digest("sample", frame)

    assert artifact.raw_sha256 == hashlib.sha256(frame.payload).hexdigest()
    assert not hasattr(artifact, "payload")
