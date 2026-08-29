from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.perception import (
    WideLandmarkSearch,
    WideRegistrationDiagnosis,
    WideSceneRegistrationAnalysis,
    load_varrock_east_iron_profile,
    varrock_east_iron_scene_excluded_regions,
)
from mining_automation.perception.resource import ResourceVisualState
from mining_automation.perception.scene_landmarks import MacroZone
from mining_automation.validation import camera_system_id
from mining_automation.validation.camera_evaluation import (
    CameraEvaluation,
    CameraResourceEvaluation,
)
from mining_automation.validation.camera_guidance import (
    CAMERA_GUIDANCE_ID,
    CAMERA_GUIDANCE_VERSION,
    CameraGuidanceDisposition,
    CameraGuidanceReason,
    WorldCameraGuidance,
)
from mining_automation.validation.camera_plan import (
    EXPECTED_CLIENT_HEIGHT,
    EXPECTED_CLIENT_WIDTH,
    CameraInputOperation,
    CameraInputReceipt,
    CameraPreflightReceipt,
)
from mining_automation.validation.camera_system_id import (
    CameraSystemIdConclusion,
    CameraSystemIdInputState,
    CameraSystemIdStepTerminalReason,
    run_fixed_camera_system_identification,
)
from mining_automation.validation.client_readiness import (
    CLIENT_INPUT_READINESS_ID,
    CLIENT_INPUT_READINESS_VERSION,
    GAMEPLAY_CHROME_POLICIES,
    ClientInputReadiness,
    ClientReadinessAnchorEvaluation,
    ClientReadinessReason,
)

_FRAME_BYTES = EXPECTED_CLIENT_WIDTH * EXPECTED_CLIENT_HEIGHT * 4
_BLANK = bytes(_FRAME_BYTES)
_PROFILE = load_varrock_east_iron_profile()
_PRIMARY_IDS = {
    _PROFILE.scene_landmarks[0].landmark_id,
    _PROFILE.scene_landmarks[2].landmark_id,
    _PROFILE.scene_landmarks[4].landmark_id,
}


def _frame(frame_id: int, *, captured: float | None = None) -> Frame:
    return Frame.from_raw(
        RawFrame(
            _BLANK,
            EXPECTED_CLIENT_WIDTH,
            EXPECTED_CLIENT_HEIGHT,
            PixelFormat.BGRA8888,
        ),
        frame_id=frame_id,
        captured_monotonic_s=float(frame_id) if captured is None else captured,
    )


class SequenceSource:
    def __init__(self, frames: list[Frame], events: list[str] | None = None) -> None:
        self.frames = frames
        self.events = events

    def capture(self) -> Frame:
        if not self.frames:
            raise AssertionError("unexpected capture")
        frame = self.frames.pop(0)
        if self.events is not None:
            self.events.append(f"capture-{frame.frame_id}")
        return frame


class CompleteDragControl:
    def __init__(self, *, partial_call: int | None = None) -> None:
        self.calls: list[object] = []
        self.partial_call = partial_call
        self.drag_count = 0

    def preflight(self) -> CameraPreflightReceipt:
        self.calls.append("preflight")
        return CameraPreflightReceipt(
            True,
            EXPECTED_CLIENT_WIDTH,
            EXPECTED_CLIENT_HEIGHT,
        )

    def click_compass(self, x: int, y: int) -> CameraInputReceipt:
        raise AssertionError("fixed system identification must not click compass")

    def key_down(self, key: str) -> CameraInputReceipt:
        raise AssertionError("fixed system identification must not hold keys")

    def key_up(self, key: str) -> CameraInputReceipt:
        raise AssertionError("fixed system identification must not release keys")

    def scroll_camera(self, x: int, y: int, detents: int) -> CameraInputReceipt:
        raise AssertionError("fixed system identification must not scroll")

    def drag_camera(
        self,
        x: int,
        y: int,
        delta_x: int,
        delta_y: int,
    ) -> tuple[CameraInputReceipt, CameraInputReceipt, CameraInputReceipt]:
        self.drag_count += 1
        self.calls.append(("drag", x, y, delta_x, delta_y))
        completed_move = 0 if self.partial_call == self.drag_count else 1
        return (
            CameraInputReceipt(CameraInputOperation.MIDDLE_DOWN, 1, 1),
            CameraInputReceipt(
                CameraInputOperation.CAMERA_DRAG_MOVE,
                1,
                completed_move,
            ),
            CameraInputReceipt(CameraInputOperation.MIDDLE_UP, 1, 1),
        )


def _ready() -> ClientInputReadiness:
    return ClientInputReadiness(
        evaluator_id=CLIENT_INPUT_READINESS_ID,
        evaluator_version=CLIENT_INPUT_READINESS_VERSION,
        reason=ClientReadinessReason.READY,
        detail="ready",
        anchors=tuple(
            ClientReadinessAnchorEvaluation(
                policy=policy,
                luma_stddev=100.0,
                edge_density=1.0,
                dark_fraction=0.0,
                matched=True,
            )
            for policy in GAMEPLAY_CHROME_POLICIES
        ),
        safe_to_attempt_camera_input=True,
    )


def _not_ready() -> ClientInputReadiness:
    return ClientInputReadiness(
        evaluator_id=CLIENT_INPUT_READINESS_ID,
        evaluator_version=CLIENT_INPUT_READINESS_VERSION,
        reason=ClientReadinessReason.GAMEPLAY_CHROME_MISMATCH,
        detail="not ready",
        anchors=(),
        safe_to_attempt_camera_input=False,
    )


def _production(
    *,
    passed: bool = False,
    fail_closed: bool = True,
) -> CameraEvaluation:
    resources = tuple(
        CameraResourceEvaluation(
            f"rock-{index}",
            ResourceVisualState.AVAILABLE
            if passed
            else ResourceVisualState.UNCERTAIN,
            1.0 if passed else 0.0,
        )
        for index in range(4)
    )
    definitive = tuple(item.resource_id for item in resources) if passed else ()
    if not passed and not fail_closed:
        resources = (
            CameraResourceEvaluation("rock-0", ResourceVisualState.AVAILABLE, 1.0),
            *resources[1:],
        )
        definitive = ("rock-0",)
    return CameraEvaluation(
        detector_id="profiled-resource:varrock-east-iron-v1",
        detector_version="2.1.0",
        profile_id="varrock-east-iron-v1",
        profile_schema_version=3,
        profile_frame_width=EXPECTED_CLIENT_WIDTH,
        profile_frame_height=EXPECTED_CLIENT_HEIGHT,
        profile_pixel_format=PixelFormat.BGRA8888,
        frame_geometry_supported=True,
        landmarks=(),
        matched_landmark_count=6 if passed else 0,
        required_landmark_count=6,
        required_landmark_matches=5,
        matched_zones=(
            MacroZone.NORTH_WEST,
            MacroZone.SOUTH_WEST,
            MacroZone.NORTH_EAST,
        )
        if passed
        else (),
        required_matched_zones=3,
        scene_reason="scene_validated" if passed else "insufficient_landmark_quorum",
        scene_validated=passed,
        resource_states=resources,
        definitive_target_ids=definitive,
        passed=passed,
    )


def _offsets(
    axis: str,
    phase: str,
    *,
    missing_zone: MacroZone | None = None,
    equal_jitter: bool = False,
    incoherent: bool = False,
) -> dict[str, tuple[int, int]]:
    values: dict[str, tuple[int, int]] = {}
    for landmark in _PROFILE.scene_landmarks:
        zone = landmark.zone(EXPECTED_CLIENT_WIDTH, EXPECTED_CLIENT_HEIGHT)
        base_x = 10 + list(MacroZone).index(zone) * 5
        base_y = 20 + list(MacroZone).index(zone) * 5
        if phase == "baseline-one":
            value = (base_x, base_y)
        elif phase == "baseline-two":
            jitter = 2 if equal_jitter else 0
            value = (
                base_x + (jitter if axis == "horizontal" else 0),
                base_y + (jitter if axis == "vertical" else 0),
            )
        elif phase == "positive":
            delta = 2 if equal_jitter else 3
            if incoherent and zone is MacroZone.NORTH_EAST:
                delta = -delta
            value = (
                base_x + (delta if axis == "horizontal" else 0),
                base_y + (delta if axis == "vertical" else 0),
            )
        elif phase == "returned":
            jitter = 2 if equal_jitter else 0
            value = (
                base_x + (jitter if axis == "horizontal" else 0),
                base_y + (jitter if axis == "vertical" else 0),
            )
        else:  # pragma: no cover - test helper guard
            raise AssertionError(phase)
        if zone is missing_zone:
            value = (96, 96)
        values[landmark.landmark_id] = value
    return values


def _guidance(
    offsets: dict[str, tuple[int, int]],
    *,
    missing_zone: MacroZone | None = None,
    primary_distance: float = 0.01,
) -> WorldCameraGuidance:
    landmarks = tuple(
        WideLandmarkSearch(
            landmark_id=landmark.landmark_id,
            offset_x=offsets[landmark.landmark_id][0],
            offset_y=offsets[landmark.landmark_id][1],
            distance=(
                primary_distance
                if landmark.landmark_id in _PRIMARY_IDS
                and landmark.zone(EXPECTED_CLIENT_WIDTH, EXPECTED_CLIENT_HEIGHT)
                is not missing_zone
                else 0.5
            ),
            maximum_distance=0.12,
            matched=(
                landmark.landmark_id in _PRIMARY_IDS
                and landmark.zone(EXPECTED_CLIENT_WIDTH, EXPECTED_CLIENT_HEIGHT)
                is not missing_zone
            ),
            zone=landmark.zone(EXPECTED_CLIENT_WIDTH, EXPECTED_CLIENT_HEIGHT),
            searched_offsets=100,
        )
        for landmark in _PROFILE.scene_landmarks
    )
    analysis = WideSceneRegistrationAnalysis(
        landmarks=landmarks,
        best_shared=None,
        diagnosis=WideRegistrationDiagnosis.INSUFFICIENT_REGISTRATION_EVIDENCE,
        detail="test diagnostic",
        search_radius=96,
        coarse_step=4,
        refinement_radius=3,
    )
    exclusions = tuple(
        dict.fromkeys(
            (
                *varrock_east_iron_scene_excluded_regions(_PROFILE),
                *(policy.region for policy in GAMEPLAY_CHROME_POLICIES),
            )
        )
    )
    return WorldCameraGuidance(
        selector_id=CAMERA_GUIDANCE_ID,
        selector_version=CAMERA_GUIDANCE_VERSION,
        disposition=CameraGuidanceDisposition.INSUFFICIENT_GUIDANCE,
        reason=CameraGuidanceReason.INSUFFICIENT_DISTRIBUTED_LANDMARKS,
        detail="diagnostic only",
        axis=None,
        direction=None,
        fit=None,
        analysis=analysis,
        excluded_regions=exclusions,
    )


def _phase(frame_id: int) -> tuple[str, str]:
    within = (frame_id - 1) % 8 + 1
    axis = "horizontal" if frame_id <= 8 else "vertical"
    phase = {
        1: "baseline-one",
        2: "baseline-two",
        3: "baseline-two",
        4: "baseline-two",
        5: "positive",
        6: "positive",
        7: "positive",
        8: "returned",
    }[within]
    return axis, phase


def _shifted_primary_offsets(delta_x: int, delta_y: int) -> dict[str, tuple[int, int]]:
    baseline = _offsets("horizontal", "baseline-one")
    return {
        landmark_id: (offset_x + delta_x, offset_y + delta_y)
        for landmark_id, (offset_x, offset_y) in baseline.items()
    }


def _patch_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    readiness: dict[int, ClientInputReadiness] | None = None,
    production: dict[int, CameraEvaluation] | None = None,
    missing_zone: MacroZone | None = None,
    equal_jitter: bool = False,
    incoherent: bool = False,
) -> None:
    readiness = readiness or {}
    production = production or {}
    monkeypatch.setattr(
        camera_system_id,
        "evaluate_client_input_readiness",
        lambda frame: readiness.get(frame.frame_id, _ready()),
    )
    monkeypatch.setattr(
        camera_system_id,
        "evaluate_varrock_east_camera",
        lambda frame: production.get(frame.frame_id, _production()),
    )

    def guidance(frame: Frame) -> WorldCameraGuidance:
        axis, phase = _phase(frame.frame_id)
        return _guidance(
            _offsets(
                axis,
                phase,
                missing_zone=missing_zone,
                equal_jitter=equal_jitter,
                incoherent=incoherent,
            ),
            missing_zone=missing_zone,
        )

    monkeypatch.setattr(
        camera_system_id,
        "evaluate_varrock_east_camera_guidance",
        guidance,
    )


def _clock() -> Callable[[], float]:
    values = iter(index / 10.0 for index in range(100))
    return values.__next__


def _run(
    source: SequenceSource,
    control: CompleteDragControl,
    *,
    clock: Callable[[], float] | None = None,
    pre_input_guard: Callable[..., None] | None = None,
):  # type: ignore[no-untyped-def]
    return run_fixed_camera_system_identification(
        source,
        control,
        sleeper=lambda _seconds: None,
        clock=_clock() if clock is None else clock,
        pre_input_guard=pre_input_guard,
        final_input_guard=pre_input_guard,
    )


def test_clean_distributed_horizontal_and_vertical_response_is_usable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    control = CompleteDragControl()

    result = _run(SequenceSource([_frame(index) for index in range(1, 17)]), control)

    assert result.conclusion is CameraSystemIdConclusion.CONTROL_DERIVATIVE_USABLE
    assert result.horizontal.complete
    assert result.vertical is not None and result.vertical.complete
    assert control.calls == [
        "preflight",
        ("drag", 200, 600, 4, 0),
        "preflight",
        ("drag", 200, 600, -4, 0),
        "preflight",
        ("drag", 200, 600, 0, 4),
        "preflight",
        ("drag", 200, 600, 0, -4),
    ]
    for axis in (result.horizontal, result.vertical):
        assert axis is not None and axis.comparison is not None
        assert axis.comparison.derivative_usable
        assert len(axis.comparison.qualified_landmark_ids) == 3
        assert set(axis.comparison.qualified_zones) == {
            MacroZone.NORTH_WEST,
            MacroZone.SOUTH_WEST,
            MacroZone.NORTH_EAST,
        }
    assert result.horizontal.comparison is not None
    with pytest.raises(ValueError, match="derivative verdict"):
        replace(result.horizontal.comparison, derivative_usable=False)
    with pytest.raises(ValueError, match="conclusion"):
        replace(
            result,
            conclusion=CameraSystemIdConclusion.LANDMARK_CONTROLLER_RETIRED,
        )


def test_result_model_rejects_identity_spoofs_and_cross_frame_comparisons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    result = _run(
        SequenceSource([_frame(index) for index in range(1, 17)]),
        CompleteDragControl(),
    )
    comparison = result.horizontal.comparison
    assert comparison is not None
    first = comparison.landmarks[0]

    with pytest.raises(ValueError, match="macro zone"):
        replace(first, zone=MacroZone.SOUTH_EAST)
    with pytest.raises(ValueError, match="identity"):
        replace(
            first,
            baseline_one=replace(first.baseline_one, maximum_distance=0.13),
        )
    with pytest.raises(ValueError, match="duplicate"):
        replace(
            comparison,
            landmarks=(first, first, *comparison.landmarks[2:]),
        )
    altered_first = replace(
        first,
        positive=replace(first.positive, offset_x=first.positive.offset_x + 1),
    )
    altered_comparison = replace(
        comparison,
        landmarks=(altered_first, *comparison.landmarks[1:]),
    )
    with pytest.raises(ValueError, match="exact A/B/A frames"):
        replace(result.horizontal, comparison=altered_comparison)
    assert result.horizontal.positive_step is not None
    with pytest.raises(ValueError, match="exact fixed drag"):
        replace(
            result.horizontal.positive_step,
            plan=replace(
                result.horizontal.positive_step.plan,
                name="forged-system-id-plan",
            ),
        )
    with pytest.raises(ValueError, match="direction"):
        replace(result.horizontal.positive_step, direction=True)
    assert result.horizontal.baseline_guard is not None
    with pytest.raises(ValueError, match="exact baselines"):
        replace(
            result.horizontal,
            baseline_guard=replace(
                result.horizontal.baseline_guard,
                decision_payload_sha256="f" * 64,
            ),
        )


@pytest.mark.parametrize(
    ("missing_zone", "equal_jitter", "incoherent"),
    [
        (MacroZone.NORTH_EAST, False, False),
        (None, True, False),
        (None, False, True),
    ],
)
def test_unproven_horizontal_response_retires_without_vertical_input(
    monkeypatch: pytest.MonkeyPatch,
    missing_zone: MacroZone | None,
    equal_jitter: bool,
    incoherent: bool,
) -> None:
    _patch_pipeline(
        monkeypatch,
        missing_zone=missing_zone,
        equal_jitter=equal_jitter,
        incoherent=incoherent,
    )
    control = CompleteDragControl()

    result = _run(SequenceSource([_frame(index) for index in range(1, 9)]), control)

    assert result.conclusion is CameraSystemIdConclusion.LANDMARK_CONTROLLER_RETIRED
    assert result.vertical is None
    assert [call for call in control.calls if isinstance(call, tuple)] == [
        ("drag", 200, 600, 4, 0),
        ("drag", 200, 600, -4, 0),
    ]


def test_precommit_no_input_drift_cannot_forge_a_control_derivative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for the stale decision-to-post comparator false positive."""

    _patch_pipeline(monkeypatch)
    offsets_by_frame = {
        1: _shifted_primary_offsets(0, 0),
        2: _shifted_primary_offsets(0, 0),
        3: _shifted_primary_offsets(3, 0),
        4: _shifted_primary_offsets(3, 0),
        5: _shifted_primary_offsets(3, 0),  # +4 pulse had no measured effect
        6: _shifted_primary_offsets(3, 0),
        7: _shifted_primary_offsets(0, 0),
        8: _shifted_primary_offsets(0, 0),  # -4 pulse had no measured effect
    }
    monkeypatch.setattr(
        camera_system_id,
        "evaluate_varrock_east_camera_guidance",
        lambda frame: _guidance(offsets_by_frame[frame.frame_id]),
    )
    control = CompleteDragControl()

    result = _run(SequenceSource([_frame(index) for index in range(1, 9)]), control)

    assert result.conclusion is CameraSystemIdConclusion.LANDMARK_CONTROLLER_RETIRED
    comparison = result.horizontal.comparison
    assert comparison is not None and not comparison.derivative_usable
    for item in comparison.landmarks:
        if item.landmark_id in _PRIMARY_IDS:
            assert item.positive_delta_x == 0
            assert item.return_delta_x == 0
            assert not item.qualified


def test_orthogonal_natural_jitter_must_be_below_vector_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    shifts = {
        1: (0, 0),
        2: (0, 4),
        3: (0, 0),
        4: (0, 0),
        5: (3, 0),
        6: (3, 0),
        7: (3, 0),
        8: (0, 0),
    }
    monkeypatch.setattr(
        camera_system_id,
        "evaluate_varrock_east_camera_guidance",
        lambda frame: _guidance(_shifted_primary_offsets(*shifts[frame.frame_id])),
    )

    result = _run(
        SequenceSource([_frame(index) for index in range(1, 9)]),
        CompleteDragControl(),
    )

    assert result.conclusion is CameraSystemIdConclusion.LANDMARK_CONTROLLER_RETIRED
    comparison = result.horizontal.comparison
    assert comparison is not None
    primary = next(
        item for item in comparison.landmarks if item.landmark_id in _PRIMARY_IDS
    )
    assert primary.tested_axis_baseline_jitter == 0
    assert primary.baseline_jitter_px == 4.0
    assert primary.positive_magnitude_px == 3.0
    assert not primary.above_baseline_jitter


def test_descriptor_jitter_must_stay_inside_strict_threshold_margin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    distances = {index: 0.01 for index in range(1, 9)}
    distances[2] = 0.11

    def guidance(frame: Frame) -> WorldCameraGuidance:
        _axis, phase = _phase(frame.frame_id)
        return _guidance(
            _offsets("horizontal", phase),
            primary_distance=distances[frame.frame_id],
        )

    monkeypatch.setattr(
        camera_system_id,
        "evaluate_varrock_east_camera_guidance",
        guidance,
    )

    result = _run(
        SequenceSource([_frame(index) for index in range(1, 9)]),
        CompleteDragControl(),
    )

    assert result.conclusion is CameraSystemIdConclusion.LANDMARK_CONTROLLER_RETIRED
    comparison = result.horizontal.comparison
    assert comparison is not None
    primary = next(
        item for item in comparison.landmarks if item.landmark_id in _PRIMARY_IDS
    )
    assert primary.descriptor_jitter == pytest.approx(0.1)
    assert primary.minimum_descriptor_margin == pytest.approx(0.01)
    assert not primary.descriptor_stable
    assert not primary.qualified


@pytest.mark.parametrize("failure", ["boundary", "unmatched"])
def test_one_phase_failure_is_reported_but_never_qualified(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    _patch_pipeline(monkeypatch)
    failed_id = _PROFILE.scene_landmarks[0].landmark_id

    def guidance(frame: Frame) -> WorldCameraGuidance:
        axis, phase = _phase(frame.frame_id)
        value = _guidance(_offsets(axis, phase))
        if frame.frame_id != 4:
            return value
        assert value.analysis is not None
        searches = tuple(
            (
                replace(item, offset_x=96)
                if item.landmark_id == failed_id and failure == "boundary"
                else replace(item, distance=0.5, matched=False)
                if item.landmark_id == failed_id
                else item
            )
            for item in value.analysis.landmarks
        )
        return replace(value, analysis=replace(value.analysis, landmarks=searches))

    monkeypatch.setattr(
        camera_system_id,
        "evaluate_varrock_east_camera_guidance",
        guidance,
    )

    result = _run(
        SequenceSource([_frame(index) for index in range(1, 9)]),
        CompleteDragControl(),
    )

    assert result.conclusion is CameraSystemIdConclusion.LANDMARK_CONTROLLER_RETIRED
    comparison = result.horizontal.comparison
    assert comparison is not None and len(comparison.landmarks) == 6
    failed = next(item for item in comparison.landmarks if item.landmark_id == failed_id)
    assert not failed.strictly_matched
    assert not failed.qualified


@pytest.mark.parametrize(
    "readiness,production",
    [
        ({1: _not_ready()}, {}),
        ({}, {1: _production(fail_closed=False)}),
    ],
)
def test_baseline_safety_veto_sends_zero_input(
    monkeypatch: pytest.MonkeyPatch,
    readiness: dict[int, ClientInputReadiness],
    production: dict[int, CameraEvaluation],
) -> None:
    _patch_pipeline(monkeypatch, readiness=readiness, production=production)
    control = CompleteDragControl()

    result = _run(SequenceSource([_frame(1)]), control)

    assert not result.conclusive
    assert control.calls == []


def test_nonfresh_second_baseline_sends_zero_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    control = CompleteDragControl()

    result = _run(SequenceSource([_frame(1), _frame(1)]), control)

    assert not result.conclusive
    assert control.calls == []


def test_provenance_guard_failure_occurs_after_commit_and_before_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    control = CompleteDragControl()

    def reject(*_args: object) -> None:
        raise RuntimeError("head changed")

    result = _run(
        SequenceSource([_frame(index) for index in range(1, 5)]),
        control,
        pre_input_guard=reject,
    )

    assert not result.conclusive
    assert control.calls == ["preflight"]
    assert result.horizontal.positive_step is not None
    assert (
        result.horizontal.positive_step.input_state
        is CameraSystemIdInputState.NONE
    )


def test_partial_positive_receipt_never_executes_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    control = CompleteDragControl(partial_call=1)

    result = _run(SequenceSource([_frame(index) for index in range(1, 5)]), control)

    assert not result.conclusive
    assert control.drag_count == 1
    step = result.horizontal.positive_step
    assert step is not None
    assert step.terminal_reason is CameraSystemIdStepTerminalReason.INPUT_EXCEPTION
    assert step.input_state is CameraSystemIdInputState.PARTIAL_OR_UNKNOWN
    assert result.horizontal.return_step is None


def test_post_positive_readiness_loss_never_executes_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch, readiness={5: _not_ready()})
    control = CompleteDragControl()

    result = _run(SequenceSource([_frame(index) for index in range(1, 6)]), control)

    assert not result.conclusive
    assert control.drag_count == 1
    step = result.horizontal.positive_step
    assert step is not None
    assert step.terminal_reason is CameraSystemIdStepTerminalReason.READINESS_LOST
    assert step.input_state is CameraSystemIdInputState.COMPLETE
    assert result.horizontal.return_step is None


@pytest.mark.parametrize(
    ("frame_id", "expected_drags"),
    [(3, 0), (4, 0), (5, 1)],
)
def test_non_fail_closed_production_rejection_stops_at_every_input_phase(
    monkeypatch: pytest.MonkeyPatch,
    frame_id: int,
    expected_drags: int,
) -> None:
    _patch_pipeline(
        monkeypatch,
        production={frame_id: _production(fail_closed=False)},
    )
    control = CompleteDragControl()

    result = _run(
        SequenceSource([_frame(index) for index in range(1, frame_id + 1)]),
        control,
    )

    assert not result.conclusive
    assert control.drag_count == expected_drags
    step = result.horizontal.positive_step
    assert step is not None
    assert (
        step.terminal_reason
        is CameraSystemIdStepTerminalReason.PRODUCTION_REJECTION_NOT_FAIL_CLOSED
    )
    assert result.horizontal.return_step is None


def test_post_positive_production_pass_remains_safe_and_executes_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch, production={5: _production(passed=True)})
    control = CompleteDragControl()

    result = _run(SequenceSource([_frame(index) for index in range(1, 9)]), control)

    assert not result.conclusive
    assert control.drag_count == 2
    step = result.horizontal.positive_step
    assert step is not None
    assert step.terminal_reason is CameraSystemIdStepTerminalReason.COMPLETE
    assert step.input_state is CameraSystemIdInputState.COMPLETE
    assert result.horizontal.return_step is not None
    assert result.horizontal.return_step.completed


@pytest.mark.parametrize("frame_id", range(1, 9))
def test_production_pass_is_safety_valid_at_every_horizontal_phase(
    monkeypatch: pytest.MonkeyPatch,
    frame_id: int,
) -> None:
    _patch_pipeline(monkeypatch, production={frame_id: _production(passed=True)})
    control = CompleteDragControl()

    result = _run(
        SequenceSource([_frame(index) for index in range(1, 17)]),
        control,
    )

    assert result.conclusion is CameraSystemIdConclusion.CONTROL_DERIVATIVE_USABLE
    assert control.drag_count == 4


def test_exact_arm_age_limit_sends_zero_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    control = CompleteDragControl()
    clock = iter((0.0, 1.0)).__next__

    result = _run(
        SequenceSource([_frame(index) for index in range(1, 5)]),
        control,
        clock=clock,
    )

    assert not result.conclusive
    assert control.calls == ["preflight"]
    step = result.horizontal.positive_step
    assert step is not None
    assert (
        step.terminal_reason
        is CameraSystemIdStepTerminalReason.ARM_FRESHNESS_EXPIRED
    )
    assert step.input_state is CameraSystemIdInputState.NONE
