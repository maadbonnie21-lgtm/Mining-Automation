from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import replace
from typing import Any

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
from mining_automation.validation import camera_bootstrap, camera_guidance_v2
from mining_automation.validation.camera_arm_guard import (
    CAMERA_ARM_GUARD_STRUCTURAL_REGIONS,
)
from mining_automation.validation.camera_bootstrap import (
    CameraNorthBootstrapInputState,
    CameraNorthBootstrapTerminalReason,
    run_camera_north_bootstrap,
)
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
from mining_automation.validation.camera_servo import CameraServoArmAgeStatus
from mining_automation.validation.camera_session import CameraFrameArtifact
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


def _frame(frame_id: int, payload: bytes = _BLANK) -> Frame:
    return Frame.from_raw(
        RawFrame(
            payload,
            EXPECTED_CLIENT_WIDTH,
            EXPECTED_CLIENT_HEIGHT,
            PixelFormat.BGRA8888,
        ),
        frame_id=frame_id,
        captured_monotonic_s=float(frame_id),
    )


def _material_payload() -> bytes:
    payload = bytearray(_BLANK)
    landmark = load_varrock_east_iron_profile().scene_landmarks[0]
    x, y, width, height = landmark.region
    for row in range(y, y + height):
        start = (row * EXPECTED_CLIENT_WIDTH + x) * 4
        payload[start : start + width * 4] = bytes([255]) * (width * 4)
    return bytes(payload)


def _structural_payload(value: int) -> bytes:
    payload = bytearray(_BLANK)
    pixel = bytes((value, value, value, 0))
    for _landmark_id, _zone, (x, y, width, height) in (
        CAMERA_ARM_GUARD_STRUCTURAL_REGIONS
    ):
        for row in range(y, y + height):
            start = (row * EXPECTED_CLIENT_WIDTH + x) * 4
            payload[start : start + width * 4] = pixel * width
    return bytes(payload)


class SequenceSource:
    def __init__(self, frames: list[Frame]) -> None:
        self.frames = frames

    def capture(self) -> Frame:
        if not self.frames:
            raise AssertionError("unexpected capture")
        return self.frames.pop(0)


class CompleteControl:
    def __init__(self, *, focused: bool = True, complete: bool = True) -> None:
        self.focused = focused
        self.complete = complete
        self.calls: list[object] = []

    def preflight(self) -> CameraPreflightReceipt:
        self.calls.append("preflight")
        return CameraPreflightReceipt(
            self.focused,
            EXPECTED_CLIENT_WIDTH,
            EXPECTED_CLIENT_HEIGHT,
        )

    def click_compass(self, x: int, y: int) -> CameraInputReceipt:
        self.calls.append(("compass", x, y))
        return CameraInputReceipt(
            CameraInputOperation.COMPASS_CLICK,
            1,
            1 if self.complete else 0,
        )

    def key_down(self, key: str) -> CameraInputReceipt:
        raise AssertionError("north bootstrap must not hold keys")

    def key_up(self, key: str) -> CameraInputReceipt:
        raise AssertionError("north bootstrap must not release keys")

    def scroll_camera(self, x: int, y: int, detents: int) -> CameraInputReceipt:
        raise AssertionError("north bootstrap must not scroll")

    def drag_camera(
        self,
        x: int,
        y: int,
        delta_x: int,
        delta_y: int,
    ) -> tuple[CameraInputReceipt, CameraInputReceipt, CameraInputReceipt]:
        raise AssertionError("north bootstrap must not drag")


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
        detail="not gameplay",
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


def _trusted_refusal() -> WorldCameraGuidance:
    profile = load_varrock_east_iron_profile()
    zones = (
        MacroZone.NORTH_WEST,
        MacroZone.SOUTH_WEST,
        MacroZone.NORTH_EAST,
    )
    analysis = WideSceneRegistrationAnalysis(
        landmarks=tuple(
            WideLandmarkSearch(
                landmark_id=f"landmark-{index}",
                offset_x=0,
                offset_y=0,
                distance=0.01 if index < 3 else 0.5,
                maximum_distance=0.12,
                matched=index < 3,
                zone=zones[index % 3],
                searched_offsets=1,
            )
            for index in range(6)
        ),
        best_shared=None,
        diagnosis=WideRegistrationDiagnosis.INSUFFICIENT_REGISTRATION_EVIDENCE,
        detail="test refusal",
        search_radius=96,
        coarse_step=4,
        refinement_radius=3,
    )
    exclusions = tuple(
        dict.fromkeys(
            (
                *varrock_east_iron_scene_excluded_regions(profile),
                *(policy.region for policy in GAMEPLAY_CHROME_POLICIES),
            )
        )
    )
    return WorldCameraGuidance(
        selector_id=CAMERA_GUIDANCE_ID,
        selector_version=CAMERA_GUIDANCE_VERSION,
        disposition=CameraGuidanceDisposition.INSUFFICIENT_GUIDANCE,
        reason=CameraGuidanceReason.INSUFFICIENT_DISTRIBUTED_LANDMARKS,
        detail="three distributed landmarks are insufficient",
        axis=None,
        direction=None,
        fit=None,
        analysis=analysis,
        excluded_regions=exclusions,
    )


def _patch_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    readiness: dict[int, ClientInputReadiness] | None = None,
    production: dict[int, CameraEvaluation] | None = None,
    guidance: WorldCameraGuidance | None = None,
) -> None:
    readiness = readiness or {}
    production = production or {}
    monkeypatch.setattr(
        camera_bootstrap,
        "evaluate_client_input_readiness",
        lambda frame: readiness.get(frame.frame_id, _ready()),
    )
    monkeypatch.setattr(
        camera_bootstrap,
        "evaluate_varrock_east_camera",
        lambda frame: production.get(frame.frame_id, _production()),
    )
    monkeypatch.setattr(
        camera_guidance_v2,
        "evaluate_varrock_east_camera_guidance",
        lambda _frame: guidance or _trusted_refusal(),
    )


def _run(
    source: SequenceSource,
    control: CompleteControl,
    *,
    clock: Callable[[], float] | None = None,
    recorder: Callable[[str, Frame], CameraFrameArtifact] | None = None,
    sleeper: Callable[[float], None] | None = None,
    pre_input_guard: (
        Callable[
            [
                camera_bootstrap.CameraServoFrameEvidence,
                camera_bootstrap.CameraServoFrameEvidence,
                camera_bootstrap.CameraServoFrameEvidence,
            ],
            None,
        ]
        | None
    ) = None,
    final_input_guard: (
        Callable[
            [
                camera_bootstrap.CameraServoFrameEvidence,
                camera_bootstrap.CameraServoFrameEvidence,
                camera_bootstrap.CameraServoFrameEvidence,
            ],
            None,
        ]
        | None
    ) = None,
):  # type: ignore[no-untyped-def]
    kwargs: dict[str, Any] = {
        "sleeper": sleeper or (lambda _seconds: None),
        "settle_s": 0.1,
        "clock": clock or iter((0.0, 0.5, 0.6)).__next__,
        "pre_input_guard": pre_input_guard,
        "final_input_guard": final_input_guard,
    }
    if recorder is not None:
        kwargs["recorder"] = recorder
    return run_camera_north_bootstrap(source, control, **kwargs)


def test_exact_one_step_happy_path_records_fresh_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    control = CompleteControl()

    result = _run(
        SequenceSource([_frame(1), _frame(2), _frame(3), _frame(4)]),
        control,
    )

    assert result.terminal_reason is CameraNorthBootstrapTerminalReason.BOOTSTRAP_EXECUTED
    assert result.input_executed
    assert not result.passed
    assert control.calls == ["preflight", ("compass", 608, 49)]
    assert result.arm_age is not None
    assert result.arm_age.status is CameraServoArmAgeStatus.WITHIN_LIMIT
    assert result.initial is not None and result.initial.artifact.frame_id == 1
    assert result.arm is not None and result.arm.artifact.frame_id == 2
    assert result.commit is not None and result.commit.artifact.frame_id == 3
    assert result.post is not None and result.post.artifact.frame_id == 4
    assert result.post_guidance is not None
    assert result.post_guidance.disposition.value == "insufficient_guidance"
    assert result.input_start_clock_s == 0.5
    assert result.input_receipt_clock_s == 0.6
    assert result.input_delivery_duration_s == pytest.approx(0.1)


def test_receipt_clock_is_sampled_before_session_bookkeeping_and_settle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    events: list[str] = []
    values = iter((0.0, 0.25, 0.4))

    class OrderedControl(CompleteControl):
        def preflight(self) -> CameraPreflightReceipt:
            events.append("preflight")
            return super().preflight()

        def click_compass(self, x: int, y: int) -> CameraInputReceipt:
            events.append("input")
            return super().click_compass(x, y)

    def clock() -> float:
        events.append("clock")
        return next(values)

    original_record = camera_guidance_v2.CameraGuidanceV2Session.record_north_receipt

    def record_receipt(self: object, *args: object, **kwargs: object) -> None:
        events.append("session")
        original_record(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        camera_guidance_v2.CameraGuidanceV2Session,
        "record_north_receipt",
        record_receipt,
    )
    result = _run(
        SequenceSource([_frame(1), _frame(2), _frame(3), _frame(4)]),
        OrderedControl(),
        clock=clock,
        sleeper=lambda _seconds: events.append("settle"),
    )

    assert result.input_start_clock_s == 0.25
    assert result.input_receipt_clock_s == 0.4
    assert result.input_delivery_duration_s == pytest.approx(0.15)
    assert events == [
        "clock",
        "preflight",
        "clock",
        "input",
        "clock",
        "session",
        "settle",
    ]


@pytest.mark.parametrize("receipt_clock", [0.4, float("nan"), float("inf"), -1.0])
def test_invalid_immediate_receipt_clock_retains_complete_input_honestly(
    monkeypatch: pytest.MonkeyPatch,
    receipt_clock: float,
) -> None:
    _patch_pipeline(monkeypatch)
    control = CompleteControl()

    result = _run(
        SequenceSource([_frame(1), _frame(2), _frame(3)]),
        control,
        clock=iter((0.0, 0.5, receipt_clock)).__next__,
    )

    assert result.terminal_reason is CameraNorthBootstrapTerminalReason.CLOCK_ERROR
    assert result.input_state is CameraNorthBootstrapInputState.COMPLETE
    assert result.input_start_clock_s == 0.5
    assert result.input_receipt_clock_s is None
    assert result.input_delivery_duration_s is None
    assert result.receipt is not None
    assert result.exception is not None
    assert result.post is None
    assert control.calls == ["preflight", ("compass", 608, 49)]


def test_receipt_clock_exception_retains_complete_input_honestly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    values: list[object] = [0.0, 0.5, OSError("clock unavailable")]

    def clock() -> float:
        value = values.pop(0)
        if isinstance(value, BaseException):
            raise value
        assert isinstance(value, float)
        return value

    result = _run(
        SequenceSource([_frame(1), _frame(2), _frame(3)]),
        CompleteControl(),
        clock=clock,
    )

    assert result.terminal_reason is CameraNorthBootstrapTerminalReason.CLOCK_ERROR
    assert result.input_state is CameraNorthBootstrapInputState.COMPLETE
    assert result.input_start_clock_s == 0.5
    assert result.input_receipt_clock_s is None
    assert result.input_delivery_duration_s is None
    assert result.exception is not None


@pytest.mark.parametrize(
    ("readiness", "production", "reason"),
    [
        ({1: _not_ready()}, {}, CameraNorthBootstrapTerminalReason.READINESS_LOST),
        (
            {},
            {1: _production(passed=True)},
            CameraNorthBootstrapTerminalReason.PRODUCTION_PASS,
        ),
        (
            {},
            {1: _production(fail_closed=False)},
            CameraNorthBootstrapTerminalReason.PRODUCTION_REJECTION_NOT_FAIL_CLOSED,
        ),
    ],
)
def test_initial_vetoes_prove_zero_preflight_and_zero_input(
    monkeypatch: pytest.MonkeyPatch,
    readiness: dict[int, ClientInputReadiness],
    production: dict[int, CameraEvaluation],
    reason: CameraNorthBootstrapTerminalReason,
) -> None:
    _patch_pipeline(monkeypatch, readiness=readiness, production=production)
    control = CompleteControl()

    result = _run(SequenceSource([_frame(1)]), control)

    assert result.terminal_reason is reason
    assert not result.input_executed
    assert control.calls == []


@pytest.mark.parametrize("stage_frame", [2, 3])
def test_readiness_loss_at_arm_or_commit_sends_zero_input(
    monkeypatch: pytest.MonkeyPatch,
    stage_frame: int,
) -> None:
    _patch_pipeline(monkeypatch, readiness={stage_frame: _not_ready()})
    control = CompleteControl()

    result = _run(SequenceSource([_frame(1), _frame(2), _frame(3)]), control)

    assert result.terminal_reason is CameraNorthBootstrapTerminalReason.READINESS_LOST
    assert not result.input_executed
    assert control.calls == []


def test_nonfresh_arm_or_commit_sends_zero_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    for frames in (
        [_frame(1), _frame(1)],
        [_frame(1), _frame(2), _frame(2)],
    ):
        control = CompleteControl()
        result = _run(SequenceSource(frames), control)
        assert (
            result.terminal_reason
            is CameraNorthBootstrapTerminalReason.NON_FRESH_OBSERVATION
        )
        assert not result.input_executed
        assert control.calls == []


def test_material_world_change_at_arm_or_commit_sends_zero_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    changed = _material_payload()
    for frames in (
        [_frame(1), _frame(2, changed)],
        [_frame(1), _frame(2), _frame(3, changed)],
    ):
        control = CompleteControl()
        result = _run(SequenceSource(frames), control)
        assert result.terminal_reason is CameraNorthBootstrapTerminalReason.WORLD_CHANGED
        assert not result.input_executed
        assert control.calls == []


@pytest.mark.parametrize("age", [1.0, 1.001])
def test_equal_or_over_arm_age_limit_sends_zero_input(
    monkeypatch: pytest.MonkeyPatch,
    age: float,
) -> None:
    _patch_pipeline(monkeypatch)
    control = CompleteControl()

    result = _run(
        SequenceSource([_frame(1), _frame(2), _frame(3)]),
        control,
        clock=iter((0.0, age)).__next__,
    )

    assert (
        result.terminal_reason
        is CameraNorthBootstrapTerminalReason.ARM_FRESHNESS_EXPIRED
    )
    assert not result.input_executed
    assert control.calls == ["preflight"]


def test_just_under_arm_age_limit_can_reach_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    control = CompleteControl()
    input_start = math.nextafter(1.0, 0.0)
    result = _run(
        SequenceSource([_frame(1), _frame(2), _frame(3), _frame(4)]),
        control,
        clock=iter((0.0, input_start, input_start)).__next__,
    )

    assert result.input_executed
    assert control.calls[0] == "preflight"


@pytest.mark.parametrize("final", [float("nan"), float("inf"), -1.0])
def test_invalid_final_clock_sends_zero_input(
    monkeypatch: pytest.MonkeyPatch,
    final: float,
) -> None:
    _patch_pipeline(monkeypatch)
    control = CompleteControl()

    result = _run(
        SequenceSource([_frame(1), _frame(2), _frame(3)]),
        control,
        clock=iter((0.5, final)).__next__,
    )

    assert result.terminal_reason is CameraNorthBootstrapTerminalReason.CLOCK_ERROR
    assert not result.input_executed
    assert control.calls == ["preflight"]


def test_recorder_mismatch_and_exception_send_zero_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)

    def bad_recorder(label: str, frame: Frame) -> CameraFrameArtifact:
        return CameraFrameArtifact(
            label=f"{label}-wrong",
            frame_id=frame.frame_id,
            raw_sha256="0" * 64,
            width=frame.width,
            height=frame.height,
            pixel_format=frame.pixel_format.value,
        )

    control = CompleteControl()
    result = _run(SequenceSource([_frame(1)]), control, recorder=bad_recorder)
    assert result.terminal_reason is CameraNorthBootstrapTerminalReason.OBSERVATION_EXCEPTION
    assert not result.input_executed
    assert control.calls == []


def test_preflight_and_short_receipt_fail_without_false_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    for control, input_state in (
        (CompleteControl(focused=False), CameraNorthBootstrapInputState.NONE),
        (
            CompleteControl(complete=False),
            CameraNorthBootstrapInputState.PARTIAL_OR_UNKNOWN,
        ),
    ):
        result = _run(
            SequenceSource([_frame(1), _frame(2), _frame(3)]),
            control,
        )
        assert result.terminal_reason is CameraNorthBootstrapTerminalReason.INPUT_EXCEPTION
        assert result.input_state is input_state
        assert result.input_executed is (
            input_state is CameraNorthBootstrapInputState.PARTIAL_OR_UNKNOWN
        )
        assert "preflight" in control.calls


def test_preflight_exception_is_zero_input_and_retains_no_attempt_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)

    class RaisingPreflightControl(CompleteControl):
        def preflight(self) -> CameraPreflightReceipt:
            self.calls.append("preflight")
            raise OSError("preflight unavailable")

    control = RaisingPreflightControl()
    result = _run(
        SequenceSource([_frame(1), _frame(2), _frame(3)]), control
    )

    assert result.terminal_reason is CameraNorthBootstrapTerminalReason.INPUT_EXCEPTION
    assert result.input_state is CameraNorthBootstrapInputState.NONE
    assert result.receipt is None
    assert control.calls == ["preflight"]


def test_slow_selector_is_outside_arm_age_but_slow_arm_evaluation_vetoes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    now = [0.0]
    selector_calls = 0

    def slow_selector(_frame: Frame) -> WorldCameraGuidance:
        nonlocal selector_calls
        selector_calls += 1
        now[0] += 9.69
        return _trusted_refusal()

    monkeypatch.setattr(
        camera_guidance_v2, "evaluate_varrock_east_camera_guidance", slow_selector
    )
    control = CompleteControl()
    result = _run(
        SequenceSource([_frame(1), _frame(2), _frame(3), _frame(4)]),
        control,
        clock=lambda: now[0],
    )
    assert result.input_completed
    assert control.calls == ["preflight", ("compass", 608, 49)]
    assert selector_calls == 2  # Initial authorization and post-input reporting only.

    _patch_pipeline(monkeypatch)
    now[0] = 0.0

    def slow_arm_production(frame: Frame) -> CameraEvaluation:
        if frame.frame_id == 2:
            now[0] += 1.0
        return _production()

    monkeypatch.setattr(
        camera_bootstrap, "evaluate_varrock_east_camera", slow_arm_production
    )
    control = CompleteControl()
    result = _run(
        SequenceSource([_frame(1), _frame(2), _frame(3)]),
        control,
        clock=lambda: now[0],
    )
    assert result.terminal_reason is CameraNorthBootstrapTerminalReason.ARM_FRESHNESS_EXPIRED
    assert result.input_state is CameraNorthBootstrapInputState.NONE
    assert control.calls == ["preflight"]


def test_slow_preflight_is_inside_arm_age_and_vetoes_at_equal_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    now = [0.0]

    class SlowPreflightControl(CompleteControl):
        def preflight(self) -> CameraPreflightReceipt:
            now[0] += 1.0
            return super().preflight()

    control = SlowPreflightControl()
    result = _run(
        SequenceSource([_frame(1), _frame(2), _frame(3)]),
        control,
        clock=lambda: now[0],
    )

    assert result.terminal_reason is CameraNorthBootstrapTerminalReason.ARM_FRESHNESS_EXPIRED
    assert result.input_state is CameraNorthBootstrapInputState.NONE
    assert control.calls == ["preflight"]


def test_cumulative_initial_to_commit_world_change_vetoes_zero_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    control = CompleteControl()
    result = _run(
        SequenceSource(
            [
                _frame(1),
                _frame(2, _structural_payload(3)),
                _frame(3, _structural_payload(6)),
            ]
        ),
        control,
    )

    assert result.terminal_reason is CameraNorthBootstrapTerminalReason.WORLD_CHANGED
    assert result.commit_guard is not None
    assert result.commit_guard.safe_to_retain_guidance
    assert result.decision_commit_guard is not None
    assert not result.decision_commit_guard.safe_to_retain_guidance
    assert result.input_state is CameraNorthBootstrapInputState.NONE
    assert control.calls == []


def test_partial_real_shaped_receipt_is_not_reported_as_zero_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)

    class PartialControl(CompleteControl):
        def click_compass(self, x: int, y: int) -> CameraInputReceipt:
            self.calls.append(("compass", x, y))
            return CameraInputReceipt(CameraInputOperation.COMPASS_CLICK, 2, 1)

    control = PartialControl()
    result = _run(
        SequenceSource([_frame(1), _frame(2), _frame(3)]), control
    )

    assert result.terminal_reason is CameraNorthBootstrapTerminalReason.INPUT_EXCEPTION
    assert result.input_state is CameraNorthBootstrapInputState.PARTIAL_OR_UNKNOWN
    assert result.input_attempted and not result.input_completed
    assert result.preflight is not None and result.preflight.supported
    assert result.receipt is None
    assert result.input_start_clock_s == result.arm_age.final_clock_s
    assert result.input_receipt_clock_s is None
    assert result.input_delivery_duration_s is None
    assert control.calls == ["preflight", ("compass", 608, 49)]


def test_complete_receipt_survives_session_bookkeeping_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)

    def fail_record(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("session bookkeeping failed")

    monkeypatch.setattr(
        camera_guidance_v2.CameraGuidanceV2Session,
        "record_north_receipt",
        fail_record,
    )
    result = _run(
        SequenceSource([_frame(1), _frame(2), _frame(3)]), CompleteControl()
    )

    assert result.terminal_reason is CameraNorthBootstrapTerminalReason.INPUT_EXCEPTION
    assert result.input_state is CameraNorthBootstrapInputState.COMPLETE
    assert result.input_completed
    assert result.receipt is not None


@pytest.mark.parametrize(
    ("frame_id", "production", "reason"),
    [
        (2, _production(passed=True), CameraNorthBootstrapTerminalReason.PRODUCTION_PASS),
        (
            2,
            _production(fail_closed=False),
            CameraNorthBootstrapTerminalReason.PRODUCTION_REJECTION_NOT_FAIL_CLOSED,
        ),
        (3, _production(passed=True), CameraNorthBootstrapTerminalReason.PRODUCTION_PASS),
        (
            3,
            _production(fail_closed=False),
            CameraNorthBootstrapTerminalReason.PRODUCTION_REJECTION_NOT_FAIL_CLOSED,
        ),
    ],
)
def test_arm_or_commit_production_vetoes_before_preflight(
    monkeypatch: pytest.MonkeyPatch,
    frame_id: int,
    production: CameraEvaluation,
    reason: CameraNorthBootstrapTerminalReason,
) -> None:
    _patch_pipeline(monkeypatch, production={frame_id: production})
    control = CompleteControl()
    result = _run(
        SequenceSource([_frame(1), _frame(2), _frame(3)]), control
    )

    assert result.terminal_reason is reason
    assert result.input_state is CameraNorthBootstrapInputState.NONE
    assert control.calls == []


@pytest.mark.parametrize(
    "stage",
    [
        "guidance",
        "arm_capture",
        "arm_record",
        "arm_evaluate",
        "arm_guard",
        "commit_capture",
        "commit_record",
        "commit_evaluate",
        "commit_guard",
        "cumulative_guard",
    ],
)
def test_every_preinput_exception_stage_stops_before_preflight(
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    _patch_pipeline(monkeypatch)
    frames = [_frame(1), _frame(2), _frame(3)]
    recorder: Callable[[str, Frame], CameraFrameArtifact] | None = None
    if stage == "guidance":
        def fail_guidance(*_args: object, **_kwargs: object) -> object:
            raise RuntimeError("guidance failed")

        monkeypatch.setattr(
            camera_bootstrap, "select_camera_guidance_v2", fail_guidance
        )
    elif stage == "arm_capture":
        frames = frames[:1]
    elif stage == "commit_capture":
        frames = frames[:2]
    elif stage in ("arm_record", "commit_record"):
        failed_label = "v2-arm" if stage == "arm_record" else "v2-commit"

        def recorder(label: str, frame: Frame) -> CameraFrameArtifact:
            if label == failed_label:
                raise OSError("recording failed")
            return camera_bootstrap.record_frame_digest(label, frame)
    elif stage in ("arm_evaluate", "commit_evaluate"):
        failed_id = 2 if stage == "arm_evaluate" else 3

        def production(frame: Frame) -> CameraEvaluation:
            if frame.frame_id == failed_id:
                raise RuntimeError("production failed")
            return _production()

        monkeypatch.setattr(
            camera_bootstrap, "evaluate_varrock_east_camera", production
        )
    else:
        failed_call = {"arm_guard": 1, "commit_guard": 2, "cumulative_guard": 3}[stage]
        original_guard = camera_bootstrap.evaluate_camera_arm_guard
        calls = 0

        def guard(before: Frame, after: Frame) -> object:
            nonlocal calls
            calls += 1
            if calls == failed_call:
                raise RuntimeError("guard failed")
            return original_guard(before, after)

        monkeypatch.setattr(camera_bootstrap, "evaluate_camera_arm_guard", guard)

    control = CompleteControl()
    result = _run(SequenceSource(frames), control, recorder=recorder)

    assert result.terminal_reason is CameraNorthBootstrapTerminalReason.OBSERVATION_EXCEPTION
    assert result.input_state is CameraNorthBootstrapInputState.NONE
    assert control.calls == []


def test_nonactionable_initial_guidance_and_origin_clock_stop_zero_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unsupported = replace(
        _trusted_refusal(), reason=CameraGuidanceReason.UNSUPPORTED_FRAME
    )
    _patch_pipeline(monkeypatch, guidance=unsupported)
    control = CompleteControl()
    result = _run(SequenceSource([_frame(1)]), control)
    assert result.terminal_reason is CameraNorthBootstrapTerminalReason.INSUFFICIENT_GUIDANCE
    assert control.calls == []

    _patch_pipeline(monkeypatch)
    control = CompleteControl()
    result = _run(
        SequenceSource([_frame(1), _frame(2)]),
        control,
        clock=lambda: float("nan"),
    )
    assert result.terminal_reason is CameraNorthBootstrapTerminalReason.CLOCK_ERROR
    assert result.input_state is CameraNorthBootstrapInputState.NONE
    assert control.calls == []


def test_nonnegative_final_clock_regression_stops_after_read_only_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    control = CompleteControl()
    result = _run(
        SequenceSource([_frame(1), _frame(2), _frame(3)]),
        control,
        clock=iter((0.5, 0.4)).__next__,
    )
    assert result.terminal_reason is CameraNorthBootstrapTerminalReason.CLOCK_ERROR
    assert result.input_state is CameraNorthBootstrapInputState.NONE
    assert control.calls == ["preflight"]


def test_external_pre_input_guard_failure_sends_zero_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    control = CompleteControl()

    def reject(
        _initial: object,
        _arm: object,
        _commit: object,
    ) -> None:
        raise RuntimeError("provenance changed")

    result = _run(
        SequenceSource([_frame(1), _frame(2), _frame(3)]),
        control,
        pre_input_guard=reject,
    )

    assert result.terminal_reason is CameraNorthBootstrapTerminalReason.OBSERVATION_EXCEPTION
    assert result.input_state is CameraNorthBootstrapInputState.NONE
    assert result.arm_age is not None
    assert result.arm_age.status is CameraServoArmAgeStatus.NOT_REACHED
    assert control.calls == []


def test_external_last_seam_guard_failure_stops_after_read_only_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    control = CompleteControl()

    def reject(
        _initial: object,
        _arm: object,
        _commit: object,
    ) -> None:
        raise RuntimeError("provenance changed during preflight")

    result = _run(
        SequenceSource([_frame(1), _frame(2), _frame(3)]),
        control,
        final_input_guard=reject,
    )

    assert result.terminal_reason is CameraNorthBootstrapTerminalReason.OBSERVATION_EXCEPTION
    assert result.input_state is CameraNorthBootstrapInputState.NONE
    assert result.arm_age is not None
    assert result.arm_age.status is CameraServoArmAgeStatus.NOT_REACHED
    assert control.calls == ["preflight"]


def test_result_rejects_missing_unsafe_or_misbound_executed_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    result = _run(
        SequenceSource([_frame(1), _frame(2), _frame(3), _frame(4)]),
        CompleteControl(),
    )
    assert result.terminal_reason is CameraNorthBootstrapTerminalReason.BOOTSTRAP_EXECUTED
    for field_name in (
        "initial",
        "guidance",
        "plan",
        "arm",
        "arm_guard",
        "commit",
        "commit_guard",
        "decision_commit_guard",
        "arm_age",
    ):
        with pytest.raises(ValueError):
            replace(result, **{field_name: None})

    assert result.arm is not None and result.commit is not None
    unsafe_arm = replace(result.arm, readiness=_not_ready(), production=None)
    with pytest.raises(ValueError, match="production-fail-closed"):
        replace(result, arm=unsafe_arm)
    fail_open_commit = replace(
        result.commit, production=_production(fail_closed=False)
    )
    with pytest.raises(ValueError, match="production-fail-closed"):
        replace(result, commit=fail_open_commit)

    assert result.post is not None and result.post_guidance is not None
    unsafe_post = replace(result.post, production=_production(fail_closed=False))
    with pytest.raises(ValueError, match="fail-closed post"):
        replace(result, post=unsafe_post)
    stale_post = replace(
        result.post,
        artifact=replace(
            result.post.artifact,
            frame_id=result.commit.artifact.frame_id,
        ),
        captured_monotonic_s=result.commit.captured_monotonic_s,
    )
    with pytest.raises(ValueError, match="strictly newer"):
        replace(result, post=stale_post)
    wrong_post_guidance = replace(
        result.post_guidance,
        decision_frame_id=result.post_guidance.decision_frame_id + 100,
    )
    with pytest.raises(ValueError, match="exact post evidence"):
        replace(result, post_guidance=wrong_post_guidance)
    forged_bootstrap = replace(
        result.post_guidance,
        disposition=camera_guidance_v2.CameraGuidanceV2Disposition.ACTIONABLE_BOOTSTRAP,
        reason=camera_guidance_v2.CameraGuidanceV2Reason.DETERMINISTIC_NORTH_BOOTSTRAP,
        heading_was_normalized=False,
        axis=camera_guidance_v2.CameraPrimitiveAxis.HEADING,
        direction=None,
    )
    with pytest.raises(ValueError, match="completed north normalization"):
        replace(result, post_guidance=forged_bootstrap)


def test_partial_result_cannot_be_replaced_into_success_or_unsafe_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)

    class PartialControl(CompleteControl):
        def click_compass(self, x: int, y: int) -> CameraInputReceipt:
            self.calls.append(("compass", x, y))
            return CameraInputReceipt(CameraInputOperation.COMPASS_CLICK, 2, 1)

    result = _run(
        SequenceSource([_frame(1), _frame(2), _frame(3)]), PartialControl()
    )
    assert result.commit is not None
    with pytest.raises(ValueError, match="terminal input exception"):
        replace(
            result,
            terminal_reason=CameraNorthBootstrapTerminalReason.PRODUCTION_PASS,
        )
    with pytest.raises(ValueError, match="production-fail-closed"):
        replace(
            result,
            commit=replace(
                result.commit,
                production=_production(fail_closed=False),
            ),
        )
    with pytest.raises(ValueError, match="cannot claim that no input began"):
        replace(result, input_state=CameraNorthBootstrapInputState.NONE)


def test_complete_failure_cannot_strip_receipt_and_relabel_input_as_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    result = _run(
        SequenceSource([_frame(1), _frame(2), _frame(3), _frame(4)]),
        CompleteControl(),
    )
    assert result.input_state is CameraNorthBootstrapInputState.COMPLETE

    with pytest.raises(ValueError, match="cannot claim that no input began"):
        replace(
            result,
            input_state=CameraNorthBootstrapInputState.NONE,
            receipt=None,
            post=None,
            post_guidance=None,
            terminal_reason=CameraNorthBootstrapTerminalReason.INPUT_EXCEPTION,
            detail="Forged receipt-stripped input failure.",
        )


def test_complete_receipt_timing_cannot_be_detached_or_forged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    result = _run(
        SequenceSource([_frame(1), _frame(2), _frame(3), _frame(4)]),
        CompleteControl(),
    )

    with pytest.raises(ValueError, match="exact final arm-age clock"):
        replace(result, input_start_clock_s=0.4)
    with pytest.raises(ValueError, match="retain exact duration"):
        replace(result, input_delivery_duration_s=0.2)
    with pytest.raises(ValueError, match="explicit clock error"):
        replace(
            result,
            input_receipt_clock_s=None,
            input_delivery_duration_s=None,
        )


def test_fresh_post_production_pass_is_the_only_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch, production={4: _production(passed=True)})
    result = _run(
        SequenceSource([_frame(1), _frame(2), _frame(3), _frame(4)]),
        CompleteControl(),
    )

    assert result.terminal_reason is CameraNorthBootstrapTerminalReason.PRODUCTION_PASS
    assert result.input_executed
    assert result.passed
    assert result.post is not None and result.post.production is not None
    assert result.post.production.passed
    assert result.input_state is CameraNorthBootstrapInputState.COMPLETE
    assert result.input_attempted and result.input_completed
    assert "no input was sent" not in result.detail
