"""Deterministic safety proof for the bounded Issue #31 camera servo."""

from __future__ import annotations

import hashlib
import inspect
from dataclasses import FrozenInstanceError, replace
from typing import Any, cast

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.perception.production_profiles import (
    load_varrock_east_iron_profile,
    varrock_east_iron_scene_excluded_regions,
)
from mining_automation.perception.resource import ResourceVisualState
from mining_automation.perception.scene_landmarks import MacroZone
from mining_automation.perception.wide_scene_registration import (
    WideLandmarkSearch,
    WideRegistrationDiagnosis,
    WideSceneRegistrationAnalysis,
)
from mining_automation.validation import camera_servo
from mining_automation.validation.camera_arm_guard import CameraArmGuardReason
from mining_automation.validation.camera_evaluation import (
    CameraEvaluation,
    CameraResourceEvaluation,
)
from mining_automation.validation.camera_guidance import (
    CAMERA_GUIDANCE_ID,
    CAMERA_GUIDANCE_VERSION,
    CameraGuidanceDirection,
    CameraGuidanceDisposition,
    CameraGuidanceReason,
    WorldCameraGuidance,
    _guidance_from_analysis,
)
from mining_automation.validation.camera_plan import (
    CameraInputOperation,
    CameraInputReceipt,
    CameraPreflightReceipt,
)
from mining_automation.validation.camera_servo import (
    ABSOLUTE_MAX_SERVO_ARM_ATTEMPTS,
    ABSOLUTE_MAX_SERVO_PRIMITIVES,
    DEFAULT_MAX_SERVO_PRIMITIVES,
    MAXIMUM_ARM_TO_INPUT_AGE_SECONDS,
    WORLD_EFFECT_DESCRIPTOR_EPSILON,
    WORLD_EFFECT_REQUIRED_LANDMARKS,
    WORLD_EFFECT_REQUIRED_ZONES,
    CameraServoArmAgeStatus,
    CameraServoArmOutcome,
    CameraServoCommitOutcome,
    CameraServoLimits,
    CameraServoProgressStatus,
    CameraServoTerminalReason,
    WorldLandmarkEffect,
    WorldLandmarkEffectItem,
    measure_world_landmark_effect,
    run_bounded_camera_servo,
)
from mining_automation.validation.camera_session import (
    CameraFrameArtifact,
    record_frame_digest,
)
from mining_automation.validation.client_readiness import (
    CLIENT_INPUT_READINESS_ID,
    CLIENT_INPUT_READINESS_VERSION,
    GAMEPLAY_CHROME_POLICIES,
    ClientInputReadiness,
    ClientReadinessAnchorEvaluation,
    ClientReadinessReason,
)

_WIDTH = 1005
_HEIGHT = 1078
_FRAME_BYTES = _WIDTH * _HEIGHT * 4
_BLANK_PAYLOAD = bytes(_FRAME_BYTES)
_ARM_FRAME_OFFSET = 1_000_000
_COMMIT_FRAME_OFFSET = 2_000_000


def _frame(frame_id: int, payload: bytes = _BLANK_PAYLOAD) -> Frame:
    return Frame.from_raw(
        RawFrame(payload, _WIDTH, _HEIGHT, PixelFormat.BGRA8888),
        frame_id=frame_id,
        captured_monotonic_s=float(frame_id),
    )


def _frame_at(frame_id: int, timestamp: float, payload: bytes = _BLANK_PAYLOAD) -> Frame:
    return Frame.from_raw(
        RawFrame(payload, _WIDTH, _HEIGHT, PixelFormat.BGRA8888),
        frame_id=frame_id,
        captured_monotonic_s=timestamp,
    )


def _material_world_payload(base: bytes = _BLANK_PAYLOAD) -> bytes:
    payload = bytearray(base)
    landmark = load_varrock_east_iron_profile().scene_landmarks[0]
    x, y, width, height = landmark.region
    for row in range(y, y + height):
        start = (row * _WIDTH + x) * 4
        payload[start : start + width * 4] = bytes([255]) * (width * 4)
    return bytes(payload)


class SequenceSource:
    def __init__(
        self,
        frames: list[Frame],
        events: list[str] | None = None,
        *,
        expand_arm_frames: bool = True,
    ) -> None:
        self.frames = (
            [
                item
                for frame in frames
                for item in (frame, _arm_frame(frame), _commit_frame(frame))
            ]
            if expand_arm_frames
            else frames
        )
        self.events = events

    def capture(self) -> Frame:
        if self.events is not None:
            self.events.append("capture")
        if not self.frames:
            raise AssertionError("unexpected capture")
        return self.frames.pop(0)


def _arm_frame(decision: Frame) -> Frame:
    return Frame.from_raw(
        RawFrame(
            bytes(decision.payload),
            decision.width,
            decision.height,
            decision.pixel_format,
        ),
        frame_id=_ARM_FRAME_OFFSET + decision.frame_id,
        captured_monotonic_s=decision.captured_monotonic_s + 0.5,
    )


def _commit_frame(decision: Frame) -> Frame:
    return Frame.from_raw(
        RawFrame(
            bytes(decision.payload),
            decision.width,
            decision.height,
            decision.pixel_format,
        ),
        frame_id=_COMMIT_FRAME_OFFSET + decision.frame_id,
        captured_monotonic_s=decision.captured_monotonic_s + 0.75,
    )


def _logical_frame_id(frame: Frame) -> int:
    if frame.frame_id >= _COMMIT_FRAME_OFFSET:
        return frame.frame_id - _COMMIT_FRAME_OFFSET
    if frame.frame_id >= _ARM_FRAME_OFFSET:
        return frame.frame_id - _ARM_FRAME_OFFSET
    return frame.frame_id


class CompleteControl:
    def __init__(self, events: list[str] | None = None) -> None:
        self.calls: list[object] = []
        self.events = events

    def preflight(self) -> CameraPreflightReceipt:
        self.calls.append("preflight")
        if self.events is not None:
            self.events.append("preflight")
        return CameraPreflightReceipt(True, _WIDTH, _HEIGHT)

    def click_compass(self, x: int, y: int) -> CameraInputReceipt:
        raise AssertionError("servo must not click the compass")

    def key_down(self, key: str) -> CameraInputReceipt:
        raise AssertionError("servo must not press keys")

    def key_up(self, key: str) -> CameraInputReceipt:
        raise AssertionError("servo must not release keys")

    def scroll_camera(self, x: int, y: int, detents: int) -> CameraInputReceipt:
        self.calls.append(("wheel", x, y, detents))
        if self.events is not None:
            self.events.append(f"wheel:{detents}")
        return CameraInputReceipt(CameraInputOperation.CAMERA_WHEEL, 1, 1)

    def drag_camera(
        self, x: int, y: int, delta_x: int, delta_y: int
    ) -> tuple[CameraInputReceipt, CameraInputReceipt, CameraInputReceipt]:
        raise AssertionError("servo must not drag")


class UnsafeControl(CompleteControl):
    def preflight(self) -> CameraPreflightReceipt:
        self.calls.append("preflight")
        return CameraPreflightReceipt(False, _WIDTH, _HEIGHT)


class ShortReceiptControl(CompleteControl):
    def scroll_camera(self, x: int, y: int, detents: int) -> CameraInputReceipt:
        self.calls.append(("short-wheel", x, y, detents))
        return CameraInputReceipt(CameraInputOperation.CAMERA_WHEEL, 1, 0)


class ExcessReceiptControl(CompleteControl):
    def scroll_camera(self, x: int, y: int, detents: int) -> CameraInputReceipt:
        self.calls.append(("excess-wheel", x, y, detents))
        return CameraInputReceipt(CameraInputOperation.CAMERA_WHEEL, 2, 2)


def _ready() -> ClientInputReadiness:
    anchors = tuple(
        ClientReadinessAnchorEvaluation(
            policy=policy,
            luma_stddev=100.0,
            edge_density=1.0,
            dark_fraction=0.0,
            matched=True,
        )
        for policy in GAMEPLAY_CHROME_POLICIES
    )
    return ClientInputReadiness(
        evaluator_id=CLIENT_INPUT_READINESS_ID,
        evaluator_version=CLIENT_INPUT_READINESS_VERSION,
        reason=ClientReadinessReason.READY,
        detail="ready",
        anchors=anchors,
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
    *, passed: bool = False, fail_closed: bool = True
) -> CameraEvaluation:
    states = (
        (ResourceVisualState.AVAILABLE,) * 4
        if passed
        else (ResourceVisualState.UNCERTAIN,) * 4
    )
    resources = tuple(
        CameraResourceEvaluation(f"rock-{index}", state, 1.0 if passed else 0.0)
        for index, state in enumerate(states)
    )
    definitive_ids: tuple[str, ...] = ()
    scene_validated = passed
    if not passed and not fail_closed:
        resources = (
            CameraResourceEvaluation("rock-0", ResourceVisualState.AVAILABLE, 1.0),
            *resources[1:],
        )
        definitive_ids = ("rock-0",)
    return CameraEvaluation(
        detector_id="production",
        detector_version="1.0.0",
        profile_id="profile",
        profile_schema_version=3,
        profile_frame_width=_WIDTH,
        profile_frame_height=_HEIGHT,
        profile_pixel_format=PixelFormat.BGRA8888,
        frame_geometry_supported=True,
        landmarks=(),
        matched_landmark_count=6 if passed else 0,
        required_landmark_count=6,
        required_landmark_matches=5,
        matched_zones=(
            MacroZone.NORTH_WEST,
            MacroZone.NORTH_EAST,
            MacroZone.SOUTH_WEST,
        )
        if passed
        else (),
        required_matched_zones=3,
        scene_reason="scene_validated" if passed else "insufficient_landmark_quorum",
        scene_validated=scene_validated,
        resource_states=resources,
        definitive_target_ids=definitive_ids or (
            tuple(item.resource_id for item in resources) if passed else ()
        ),
        passed=passed,
    )


def _guidance(
    direction: CameraGuidanceDirection = CameraGuidanceDirection.POSITIVE,
    *,
    scale: float | None = None,
) -> WorldCameraGuidance:
    profile = load_varrock_east_iron_profile()
    selected_scale = scale
    if selected_scale is None:
        selected_scale = (
            0.90 if direction is CameraGuidanceDirection.POSITIVE else 1.10
        )
    centre_x = profile.frame_width / 2.0
    centre_y = profile.frame_height / 2.0
    searches = tuple(
        WideLandmarkSearch(
            landmark_id=landmark.landmark_id,
            offset_x=round(
                (selected_scale - 1.0)
                * (landmark.region[0] + landmark.region[2] / 2.0 - centre_x)
            ),
            offset_y=round(
                (selected_scale - 1.0)
                * (landmark.region[1] + landmark.region[3] / 2.0 - centre_y)
            ),
            distance=landmark.maximum_distance / 4.0,
            maximum_distance=landmark.maximum_distance,
            matched=True,
            zone=landmark.macro_zone,
            searched_offsets=1,
        )
        for landmark in profile.scene_landmarks
    )
    analysis = WideSceneRegistrationAnalysis(
        landmarks=searches,
        best_shared=None,
        diagnosis=WideRegistrationDiagnosis.CAMERA_TRANSFORM_NOT_TRANSLATION,
        detail="synthetic scale-dominant transform",
        search_radius=96,
        coarse_step=4,
        refinement_radius=3,
    )
    result = _guidance_from_analysis(
        analysis,
        profile,
        excluded_regions=varrock_east_iron_scene_excluded_regions(profile),
    )
    assert result.disposition is CameraGuidanceDisposition.ACTIONABLE
    assert result.direction is direction
    return result


def _refusal() -> WorldCameraGuidance:
    excluded = varrock_east_iron_scene_excluded_regions(
        load_varrock_east_iron_profile()
    )
    return WorldCameraGuidance(
        selector_id=CAMERA_GUIDANCE_ID,
        selector_version=CAMERA_GUIDANCE_VERSION,
        disposition=CameraGuidanceDisposition.INSUFFICIENT_GUIDANCE,
        reason=CameraGuidanceReason.INSUFFICIENT_DISTRIBUTED_LANDMARKS,
        detail="not enough world evidence",
        axis=None,
        direction=None,
        fit=None,
        analysis=None,
        excluded_regions=excluded,
    )


def _effect(observed: bool) -> WorldLandmarkEffect:
    profile = load_varrock_east_iron_profile()
    changed_ids: set[str] = set()
    if observed:
        for zone in MacroZone:
            landmark = next(
                (item for item in profile.scene_landmarks if item.macro_zone is zone),
                None,
            )
            if landmark is not None:
                changed_ids.add(landmark.landmark_id)
    changed_count = len(changed_ids)
    items = tuple(
        WorldLandmarkEffectItem(
            landmark_id=landmark.landmark_id,
            zone=landmark.macro_zone,
            descriptor_distance=0.1 if landmark.landmark_id in changed_ids else 0.0,
            changed=landmark.landmark_id in changed_ids,
        )
        for landmark in profile.scene_landmarks
    )
    changed_zones = tuple(
        zone for zone in MacroZone if any(item.changed and item.zone is zone for item in items)
    )
    return WorldLandmarkEffect(
        landmarks=items,
        mean_descriptor_distance=sum(item.descriptor_distance for item in items) / len(items),
        maximum_descriptor_distance=max(item.descriptor_distance for item in items),
        changed_landmark_count=changed_count,
        changed_zones=changed_zones,
        effect_observed=observed,
    )


def _patch_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    readiness: dict[int, ClientInputReadiness] | None = None,
    production: dict[int, CameraEvaluation] | None = None,
    guidance: dict[int, WorldCameraGuidance] | None = None,
    effects: dict[tuple[int, int], WorldLandmarkEffect] | None = None,
    events: list[str] | None = None,
) -> None:
    readiness = readiness or {}
    production = production or {}
    guidance = guidance or {}
    effects = effects or {}

    def readiness_evaluator(frame: Frame) -> ClientInputReadiness:
        if events is not None:
            events.append(f"readiness:{frame.frame_id}")
        return readiness.get(frame.frame_id, _ready())

    def production_evaluator(frame: Frame) -> CameraEvaluation:
        if events is not None:
            events.append(f"production:{frame.frame_id}")
        return production.get(frame.frame_id, _production())

    def guidance_evaluator(frame: Frame) -> WorldCameraGuidance:
        if events is not None:
            events.append(f"guidance:{frame.frame_id}")
        return guidance.get(frame.frame_id, _guidance())

    def effect_evaluator(before: Frame, after: Frame) -> WorldLandmarkEffect:
        if events is not None:
            events.append(f"effect:{before.frame_id}->{after.frame_id}")
        return effects.get(
            (_logical_frame_id(before), _logical_frame_id(after)), _effect(True)
        )

    monkeypatch.setattr(camera_servo, "evaluate_client_input_readiness", readiness_evaluator)
    monkeypatch.setattr(camera_servo, "evaluate_varrock_east_camera", production_evaluator)
    monkeypatch.setattr(
        camera_servo, "evaluate_varrock_east_camera_guidance", guidance_evaluator
    )
    monkeypatch.setattr(camera_servo, "measure_world_landmark_effect", effect_evaluator)
    monkeypatch.setattr(
        camera_servo,
        "_world_state_digest",
        lambda frame: hashlib.sha256(str(frame.frame_id).encode()).hexdigest(),
    )


def _run(
    source: SequenceSource,
    control: CompleteControl,
    **kwargs: Any,
):  # type: ignore[no-untyped-def]
    return run_bounded_camera_servo(
        source,
        control,
        sleeper=kwargs.pop("sleeper", lambda _seconds: None),
        settle_s=kwargs.pop("settle_s", 0.1),
        clock=kwargs.pop("clock", lambda: 0.0),
        **kwargs,
    )


def test_default_and_absolute_primitive_bounds_are_frozen() -> None:
    assert CameraServoLimits().max_primitives == DEFAULT_MAX_SERVO_PRIMITIVES == 8
    assert CameraServoLimits(max_primitives=ABSOLUTE_MAX_SERVO_PRIMITIVES)
    for invalid in (0, True, ABSOLUTE_MAX_SERVO_PRIMITIVES + 1):
        with pytest.raises(ValueError, match="max_primitives"):
            CameraServoLimits(max_primitives=cast(Any, invalid))
    for invalid in (0.0, float("inf"), 121.0, True):
        with pytest.raises(ValueError, match="max_elapsed_s"):
            CameraServoLimits(max_elapsed_s=cast(Any, invalid))


def test_initial_production_pass_is_only_success_and_sends_no_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch, production={1: _production(passed=True)})
    control = CompleteControl()

    result = _run(SequenceSource([_frame(1)]), control)

    assert result.passed
    assert result.terminal_reason is CameraServoTerminalReason.PRODUCTION_PASS
    assert result.steps == ()
    assert result.final is not None and result.final.production is not None
    assert result.final.production.passed
    assert control.calls == []


def test_slow_initial_production_pass_fails_the_absolute_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch, production={1: _production(passed=True)})
    times = iter((0.0, 2.0))

    result = _run(
        SequenceSource([_frame(1)]),
        CompleteControl(),
        clock=lambda: next(times),
        limits=CameraServoLimits(max_elapsed_s=1.0),
    )

    assert not result.passed
    assert result.terminal_reason is CameraServoTerminalReason.TIME_BUDGET_EXHAUSTED
    assert result.final is not None and result.final.production is not None
    assert result.final.production.passed
    assert result.elapsed_s == 2.0
    assert result.steps == ()


def test_one_step_exact_order_records_before_each_evaluation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    _patch_pipeline(
        monkeypatch,
        production={2: _production(passed=True)},
        events=events,
    )

    def recorder(label: str, frame: Frame) -> CameraFrameArtifact:
        events.append(f"record:{frame.frame_id}")
        return record_frame_digest(label, frame)

    def sleeper(_seconds: float) -> None:
        events.append("settle")

    result = _run(
        SequenceSource([_frame(1), _frame(2)], events),
        CompleteControl(events),
        recorder=recorder,
        sleeper=sleeper,
    )

    assert result.passed
    assert events == [
        "capture",
        "record:1",
        "readiness:1",
        "production:1",
        "guidance:1",
        "capture",
        f"record:{_ARM_FRAME_OFFSET + 1}",
        f"readiness:{_ARM_FRAME_OFFSET + 1}",
        f"production:{_ARM_FRAME_OFFSET + 1}",
        "capture",
        f"record:{_COMMIT_FRAME_OFFSET + 1}",
        f"readiness:{_COMMIT_FRAME_OFFSET + 1}",
        "preflight",
        "wheel:1",
        "settle",
        "capture",
        "record:2",
        "readiness:2",
        "production:2",
        f"effect:{_COMMIT_FRAME_OFFSET + 1}->2",
    ]
    assert len(result.steps) == 1
    assert result.steps[0].receipt is not None
    assert result.steps[0].effect is not None


@pytest.mark.parametrize(
    ("direction", "detents"),
    [
        (CameraGuidanceDirection.POSITIVE, 1),
        (CameraGuidanceDirection.NEGATIVE, -1),
    ],
)
def test_guidance_can_emit_only_one_signed_wheel_detent(
    monkeypatch: pytest.MonkeyPatch,
    direction: CameraGuidanceDirection,
    detents: int,
) -> None:
    _patch_pipeline(
        monkeypatch,
        production={2: _production(passed=True)},
        guidance={1: _guidance(direction)},
    )
    control = CompleteControl()

    result = _run(SequenceSource([_frame(1), _frame(2)]), control)

    assert result.passed
    assert control.calls == ["preflight", ("wheel", 400, 50, detents)]
    action = result.steps[0].primitive.actions[0]
    assert action.detents == detents  # type: ignore[union-attr]
    assert len(result.arm_attempts) == 1
    assert result.arm_attempts[0].outcome is CameraServoArmOutcome.RETAINED
    assert result.steps[0].arm is result.arm_attempts[0]
    assert result.steps[0].pre.artifact.frame_id == _ARM_FRAME_OFFSET + 1


def test_guidance_exception_stops_before_arm_capture_or_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)

    def fail_guidance(_frame: Frame) -> WorldCameraGuidance:
        raise RuntimeError("guidance failed")

    monkeypatch.setattr(
        camera_servo, "evaluate_varrock_east_camera_guidance", fail_guidance
    )
    source = SequenceSource([_frame(1)])
    control = CompleteControl()

    result = _run(source, control)

    assert result.terminal_reason is CameraServoTerminalReason.OBSERVATION_EXCEPTION
    assert result.arm_attempts == ()
    assert result.steps == ()
    assert control.calls == []
    assert len(source.frames) == 2


def test_arm_capture_disconnect_after_guidance_stops_before_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    control = CompleteControl()

    result = _run(
        SequenceSource([_frame(1)], expand_arm_frames=False),
        control,
    )

    assert result.terminal_reason is CameraServoTerminalReason.OBSERVATION_EXCEPTION
    assert result.arm_attempts == ()
    assert result.steps == ()
    assert control.calls == []


def test_arm_recorder_exception_after_guidance_stops_before_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)

    def recorder(label: str, frame: Frame) -> CameraFrameArtifact:
        if label.startswith("servo-arm-"):
            raise OSError("arm artifact unavailable")
        return record_frame_digest(label, frame)

    control = CompleteControl()
    result = _run(
        SequenceSource([_frame(1)]),
        control,
        recorder=recorder,
    )

    assert result.terminal_reason is CameraServoTerminalReason.OBSERVATION_EXCEPTION
    assert result.arm_attempts == ()
    assert result.steps == ()
    assert control.calls == []


def test_fresh_arm_readiness_veto_stops_with_recorded_zero_input_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arm_id = _ARM_FRAME_OFFSET + 1
    _patch_pipeline(monkeypatch, readiness={arm_id: _not_ready()})
    control = CompleteControl()

    result = _run(SequenceSource([_frame(1)]), control)

    assert result.terminal_reason is CameraServoTerminalReason.READINESS_LOST
    assert result.steps == ()
    assert control.calls == []
    assert len(result.arm_attempts) == 1
    arm = result.arm_attempts[0]
    assert arm.outcome is CameraServoArmOutcome.READINESS_LOST
    assert arm.arm_artifact.frame_id == arm_id
    assert arm.production is None


def test_fresh_arm_production_pass_succeeds_without_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arm_id = _ARM_FRAME_OFFSET + 1
    _patch_pipeline(monkeypatch, production={arm_id: _production(passed=True)})
    control = CompleteControl()

    result = _run(SequenceSource([_frame(1)]), control)

    assert result.passed
    assert result.terminal_reason is CameraServoTerminalReason.PRODUCTION_PASS
    assert result.steps == ()
    assert control.calls == []
    assert result.arm_attempts[0].outcome is CameraServoArmOutcome.PRODUCTION_PASS
    assert result.final is not None and result.final.artifact.frame_id == arm_id


def test_fresh_arm_unsafe_production_rejection_stops_without_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arm_id = _ARM_FRAME_OFFSET + 1
    _patch_pipeline(
        monkeypatch,
        production={arm_id: _production(fail_closed=False)},
    )
    control = CompleteControl()

    result = _run(SequenceSource([_frame(1)]), control)

    assert (
        result.terminal_reason
        is CameraServoTerminalReason.PRODUCTION_REJECTION_NOT_FAIL_CLOSED
    )
    assert result.steps == ()
    assert control.calls == []
    assert (
        result.arm_attempts[0].outcome
        is CameraServoArmOutcome.PRODUCTION_REJECTION_NOT_FAIL_CLOSED
    )


def test_material_arm_change_discards_sign_then_requires_second_fresh_arm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    changed = _material_world_payload()
    events: list[str] = []
    _patch_pipeline(
        monkeypatch,
        production={5: _production(passed=True)},
        guidance={
            1: _guidance(CameraGuidanceDirection.POSITIVE),
            2: _guidance(CameraGuidanceDirection.NEGATIVE),
        },
        events=events,
    )
    source = SequenceSource(
        [
            _frame(1),
            _frame(2, changed),
            _frame(3, changed),
            _frame(4, changed),
            _frame(5, changed),
        ],
        events,
        expand_arm_frames=False,
    )
    control = CompleteControl(events)

    result = _run(source, control)

    assert result.passed
    assert [attempt.outcome for attempt in result.arm_attempts] == [
        CameraServoArmOutcome.STALE_DISCARDED_RESTART,
        CameraServoArmOutcome.RETAINED,
    ]
    assert result.arm_attempts[0].guard is not None
    assert (
        result.arm_attempts[0].guard.reason
        is CameraArmGuardReason.MATERIAL_WORLD_CHANGE
    )
    assert [event for event in events if event.startswith("guidance:")] == [
        "guidance:1",
        "guidance:2",
    ]
    assert events.index("capture", events.index("guidance:2") + 1) < events.index(
        "wheel:-1"
    )
    assert control.calls == ["preflight", ("wheel", 400, 50, -1)]
    assert len(result.steps) == 1
    assert result.steps[0].pre.artifact.frame_id == 3
    assert result.steps[0].pre_world_state_digest == hashlib.sha256(b"4").hexdigest()


@pytest.mark.parametrize(
    "arm_frame",
    [
        _frame_at(1, 1.0),
        _frame_at(2, 1.0),
        _frame_at(2, 0.5),
    ],
)
def test_nonfresh_arm_is_recorded_terminal_veto_even_if_arm_would_pass(
    monkeypatch: pytest.MonkeyPatch,
    arm_frame: Frame,
) -> None:
    events: list[str] = []
    _patch_pipeline(monkeypatch, events=events)

    def arm_would_pass(frame: Frame) -> CameraEvaluation:
        events.append(f"production:{frame.frame_id}")
        return _production(passed=frame is arm_frame)

    monkeypatch.setattr(camera_servo, "evaluate_varrock_east_camera", arm_would_pass)
    control = CompleteControl()
    source = SequenceSource(
        [_frame_at(1, 1.0), arm_frame, _frame(9)],
        expand_arm_frames=False,
    )

    result = _run(source, control)

    assert not result.passed
    assert result.terminal_reason is CameraServoTerminalReason.OBSERVATION_EXCEPTION
    assert result.steps == ()
    assert control.calls == []
    assert len(result.arm_attempts) == 1
    arm = result.arm_attempts[0]
    assert arm.outcome is CameraServoArmOutcome.NON_FRESH_STOP
    assert arm.guard is not None
    assert arm.guard.reason is CameraArmGuardReason.NON_FRESH_ARM_FRAME
    assert arm.readiness is None and arm.production is None
    assert len(source.frames) == 1
    assert not any(
        event == f"readiness:{arm_frame.frame_id}" for event in events[3:]
    )


def test_absolute_arm_attempt_bound_stops_frozen_clock_discard_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    changed = _material_world_payload()
    frames = [
        _frame(index + 1, changed if index % 2 else _BLANK_PAYLOAD)
        for index in range(ABSOLUTE_MAX_SERVO_ARM_ATTEMPTS + 1)
    ]
    control = CompleteControl()

    result = _run(
        SequenceSource(frames, expand_arm_frames=False),
        control,
        clock=lambda: 0.0,
    )

    assert (
        result.terminal_reason
        is CameraServoTerminalReason.ARM_ATTEMPT_BUDGET_EXHAUSTED
    )
    assert len(result.arm_attempts) == ABSOLUTE_MAX_SERVO_ARM_ATTEMPTS
    assert all(
        attempt.outcome is CameraServoArmOutcome.STALE_DISCARDED_RESTART
        for attempt in result.arm_attempts
    )
    assert result.steps == ()
    assert control.calls == []


@pytest.mark.parametrize("failure_stage", ["readiness", "production", "guard"])
def test_arm_evaluator_exception_is_recorded_and_sends_zero_input(
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    _patch_pipeline(monkeypatch)
    arm_id = _ARM_FRAME_OFFSET + 1
    if failure_stage == "readiness":
        def readiness(frame: Frame) -> ClientInputReadiness:
            if frame.frame_id == arm_id:
                raise RuntimeError("arm readiness failed")
            return _ready()

        monkeypatch.setattr(camera_servo, "evaluate_client_input_readiness", readiness)
    elif failure_stage == "production":
        def production(frame: Frame) -> CameraEvaluation:
            if frame.frame_id == arm_id:
                raise RuntimeError("arm production failed")
            return _production()

        monkeypatch.setattr(camera_servo, "evaluate_varrock_east_camera", production)
    else:
        def guard(_decision: Frame, _arm: Frame) -> Any:
            raise RuntimeError("arm guard failed")

        monkeypatch.setattr(camera_servo, "evaluate_camera_arm_guard", guard)
    control = CompleteControl()

    result = _run(SequenceSource([_frame(1)]), control)

    assert result.terminal_reason is CameraServoTerminalReason.OBSERVATION_EXCEPTION
    assert result.steps == ()
    assert control.calls == []
    assert len(result.arm_attempts) == 1
    attempt = result.arm_attempts[0]
    assert attempt.outcome is (
        CameraServoArmOutcome.GUARD_ERROR
        if failure_stage == "guard"
        else CameraServoArmOutcome.EVALUATION_ERROR
    )
    assert attempt.exception is not None


@pytest.mark.parametrize(
    "times",
    [
        (0.0, 0.0, 0.0, 0.0, 1.0),
        (0.0, 0.0, 0.0, 0.0, 0.0, 1.0),
        (0.0, 0.0, 0.0, 0.0, 0.5),
        (0.0, 0.0, 0.0, 0.0, 0.0, 0.5),
    ],
)
def test_deadline_is_rechecked_after_arm_and_immediately_before_runner(
    monkeypatch: pytest.MonkeyPatch,
    times: tuple[float, ...],
) -> None:
    _patch_pipeline(monkeypatch)
    clock_values = iter(times)
    control = CompleteControl()

    result = _run(
        SequenceSource([_frame(1)]),
        control,
        clock=lambda: next(clock_values),
        limits=CameraServoLimits(max_elapsed_s=0.5),
    )

    assert result.terminal_reason is CameraServoTerminalReason.TIME_BUDGET_EXHAUSTED
    assert result.steps == ()
    assert control.calls == []
    assert len(result.arm_attempts) == 1
    assert (
        result.arm_attempts[0].outcome
        is CameraServoArmOutcome.DEADLINE_EXHAUSTED
    )
    assert (result.arm_attempts[0].guard is not None) is (len(times) == 6)


@pytest.mark.parametrize(
    "times",
    [
        (0.0, 0.0, 0.0, 0.0, float("nan")),
        (0.0, 0.0, 0.0, 0.0, 0.0, float("nan")),
        (1.0, 1.0, 1.0, 1.0, 0.5),
        (1.0, 1.0, 1.0, 1.0, 1.0, 0.5),
    ],
)
def test_invalid_clock_at_either_post_arm_sample_vetoes_input(
    monkeypatch: pytest.MonkeyPatch,
    times: tuple[float, ...],
) -> None:
    _patch_pipeline(monkeypatch)
    clock_values = iter(times)
    control = CompleteControl()

    result = _run(
        SequenceSource([_frame(1)]),
        control,
        clock=lambda: next(clock_values),
    )

    assert result.terminal_reason is CameraServoTerminalReason.CLOCK_ERROR
    assert result.steps == ()
    assert control.calls == []
    assert result.arm_attempts[0].outcome is CameraServoArmOutcome.CLOCK_ERROR
    assert result.arm_attempts[0].exception is not None


def test_arm_recorder_provenance_mismatch_fails_before_evaluators_or_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    _patch_pipeline(monkeypatch, events=events)

    def mismatching_recorder(label: str, frame: Frame) -> CameraFrameArtifact:
        artifact = record_frame_digest(label, frame)
        if label.startswith("servo-arm-"):
            return replace(artifact, raw_sha256="0" * 64)
        return artifact

    control = CompleteControl()

    result = _run(
        SequenceSource([_frame(1)], events),
        control,
        recorder=mismatching_recorder,
    )

    assert result.terminal_reason is CameraServoTerminalReason.OBSERVATION_EXCEPTION
    assert result.arm_attempts == ()
    assert result.steps == ()
    assert control.calls == []
    assert events[-1] == "capture"


def test_arm_evidence_binds_hashes_timestamps_and_is_immutable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch, production={2: _production(passed=True)})
    result = _run(SequenceSource([_frame(1), _frame(2)]), CompleteControl())
    attempt = result.arm_attempts[0]

    assert attempt.decision_raw_sha256 == hashlib.sha256(_BLANK_PAYLOAD).hexdigest()
    assert attempt.arm_raw_sha256 == hashlib.sha256(_BLANK_PAYLOAD).hexdigest()
    assert attempt.decision_captured_monotonic_s == 1.0
    assert attempt.arm_captured_monotonic_s == 1.5
    assert attempt.guard is not None
    assert attempt.guard.decision_payload_sha256 == attempt.decision_raw_sha256
    assert attempt.guard.arm_payload_sha256 == attempt.arm_raw_sha256
    with pytest.raises(FrozenInstanceError):
        cast(Any, attempt).outcome = CameraServoArmOutcome.NON_FRESH_STOP
    with pytest.raises(ValueError, match="captured_monotonic_s"):
        replace(attempt.decision, captured_monotonic_s=cast(Any, True))
    with pytest.raises(ValueError, match="arm_captured_monotonic_s"):
        replace(attempt, arm_captured_monotonic_s=cast(Any, True))
    with pytest.raises(ValueError, match="bind retained arm attempts"):
        replace(result, arm_attempts=(*result.arm_attempts, attempt))


@pytest.mark.parametrize(
    ("final_clock_s", "expected_status"),
    [
        (10.999999, CameraServoArmAgeStatus.WITHIN_LIMIT),
        (11.0, CameraServoArmAgeStatus.EXPIRED),
        (11.000001, CameraServoArmAgeStatus.EXPIRED),
    ],
)
def test_arm_age_boundary_is_exclusive_after_preflight_and_vetoes_input(
    monkeypatch: pytest.MonkeyPatch,
    final_clock_s: float,
    expected_status: CameraServoArmAgeStatus,
) -> None:
    _patch_pipeline(monkeypatch)
    times = iter((0.0, 0.0, 0.0, 10.0, 10.0, 10.0, final_clock_s, 12.0))
    control = CompleteControl()

    result = _run(
        SequenceSource([_frame(1)]), control, clock=lambda: next(times)
    )

    attempt = result.arm_attempts[0]
    assert attempt.age.status is expected_status
    assert attempt.age.maximum_age_s == MAXIMUM_ARM_TO_INPUT_AGE_SECONDS == 1.0
    if expected_status is CameraServoArmAgeStatus.WITHIN_LIMIT:
        assert control.calls == ["preflight", ("wheel", 400, 50, 1)]
        assert attempt.outcome is CameraServoArmOutcome.RETAINED
        assert len(result.steps) == 1
    else:
        assert result.terminal_reason is CameraServoTerminalReason.ARM_FRESHNESS_EXPIRED
        assert attempt.outcome is CameraServoArmOutcome.ARM_FRESHNESS_EXPIRED
        assert result.steps == ()
        assert control.calls == ["preflight"]


def test_slow_preflight_expires_arm_and_vetoes_physical_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    now = [0.0]

    class SlowPreflightControl(CompleteControl):
        def preflight(self) -> CameraPreflightReceipt:
            receipt = super().preflight()
            now[0] = MAXIMUM_ARM_TO_INPUT_AGE_SECONDS
            return receipt

    control = SlowPreflightControl()
    result = _run(
        SequenceSource([_frame(1)]),
        control,
        clock=lambda: now[0],
    )

    assert result.terminal_reason is CameraServoTerminalReason.ARM_FRESHNESS_EXPIRED
    assert result.steps == ()
    assert control.calls == ["preflight"]
    attempt = result.arm_attempts[0]
    assert attempt.outcome is CameraServoArmOutcome.ARM_FRESHNESS_EXPIRED
    assert attempt.age.status is CameraServoArmAgeStatus.EXPIRED
    assert attempt.age.age_s == MAXIMUM_ARM_TO_INPUT_AGE_SECONDS


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_invalid_arm_origin_clock_vetoes_with_typed_zero_input_evidence(
    monkeypatch: pytest.MonkeyPatch,
    invalid: float,
) -> None:
    _patch_pipeline(monkeypatch)
    times = iter((0.0, 0.0, 0.0, invalid))
    control = CompleteControl()

    result = _run(
        SequenceSource([_frame(1)]), control, clock=lambda: next(times)
    )

    assert result.terminal_reason is CameraServoTerminalReason.CLOCK_ERROR
    assert result.steps == ()
    assert control.calls == []
    attempt = result.arm_attempts[0]
    assert attempt.outcome is CameraServoArmOutcome.CLOCK_ERROR
    assert attempt.age.status is CameraServoArmAgeStatus.ORIGIN_CLOCK_ERROR
    assert attempt.exception is not None


def test_regressing_arm_origin_clock_vetoes_before_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    times = iter((5.0, 5.0, 5.0, 4.999))
    control = CompleteControl()

    result = _run(
        SequenceSource([_frame(1)]), control, clock=lambda: next(times)
    )

    assert result.terminal_reason is CameraServoTerminalReason.CLOCK_ERROR
    assert result.arm_attempts[0].age.status is CameraServoArmAgeStatus.ORIGIN_CLOCK_ERROR
    assert control.calls == []


@pytest.mark.parametrize("final", [float("nan"), float("inf"), 9.999])
def test_invalid_or_regressing_final_arm_clock_vetoes_after_read_only_preflight(
    monkeypatch: pytest.MonkeyPatch,
    final: float,
) -> None:
    _patch_pipeline(monkeypatch)
    times = iter((0.0, 0.0, 0.0, 10.0, 10.0, 10.0, final))
    control = CompleteControl()

    result = _run(
        SequenceSource([_frame(1)]), control, clock=lambda: next(times)
    )

    assert result.terminal_reason is CameraServoTerminalReason.CLOCK_ERROR
    assert result.steps == ()
    assert control.calls == ["preflight"]
    attempt = result.arm_attempts[0]
    assert attempt.outcome is CameraServoArmOutcome.CLOCK_ERROR
    assert attempt.age.status is CameraServoArmAgeStatus.FINAL_CLOCK_ERROR
    assert attempt.exception is not None


@pytest.mark.parametrize(
    "commit_frame",
    [_frame_at(2, 3.0), _frame_at(3, 2.0), _frame_at(3, 1.5)],
)
def test_final_commit_must_be_strictly_newer_than_the_accepted_arm(
    monkeypatch: pytest.MonkeyPatch,
    commit_frame: Frame,
) -> None:
    _patch_pipeline(monkeypatch)
    control = CompleteControl()
    source = SequenceSource(
        [_frame_at(1, 1.0), _frame_at(2, 2.0), commit_frame],
        expand_arm_frames=False,
    )

    result = _run(source, control)

    assert result.terminal_reason is CameraServoTerminalReason.COMMIT_OBSERVATION_REJECTED
    assert result.steps == ()
    assert control.calls == []
    attempt = result.arm_attempts[0]
    assert attempt.outcome is CameraServoArmOutcome.COMMIT_STOP
    assert attempt.commit is not None
    assert attempt.commit.outcome is CameraServoCommitOutcome.NON_FRESH_STOP
    assert attempt.commit.guard is not None
    assert attempt.commit.guard.reason is CameraArmGuardReason.NON_FRESH_ARM_FRAME


def test_final_commit_readiness_vetoes_without_production_or_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    commit_id = _COMMIT_FRAME_OFFSET + 1
    _patch_pipeline(monkeypatch, readiness={commit_id: _not_ready()}, events=events)
    control = CompleteControl()

    result = _run(SequenceSource([_frame(1)]), control)

    assert result.terminal_reason is CameraServoTerminalReason.READINESS_LOST
    assert result.steps == ()
    assert control.calls == []
    assert f"production:{commit_id}" not in events
    attempt = result.arm_attempts[0]
    assert attempt.outcome is CameraServoArmOutcome.COMMIT_STOP
    assert attempt.commit is not None
    assert attempt.commit.outcome is CameraServoCommitOutcome.READINESS_LOST
    assert attempt.commit.guard is None


def test_material_final_commit_change_vetoes_without_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    source = SequenceSource(
        [
            _frame_at(1, 1.0),
            _frame_at(2, 2.0),
            _frame_at(3, 3.0, _material_world_payload()),
        ],
        expand_arm_frames=False,
    )
    control = CompleteControl()

    result = _run(source, control)

    assert result.terminal_reason is CameraServoTerminalReason.COMMIT_OBSERVATION_REJECTED
    assert result.steps == ()
    assert control.calls == []
    commit = result.arm_attempts[0].commit
    assert commit is not None
    assert commit.outcome is CameraServoCommitOutcome.GUARD_REJECTED
    assert commit.guard is not None
    assert commit.guard.reason is CameraArmGuardReason.MATERIAL_WORLD_CHANGE


def test_final_commit_recorder_mismatch_vetoes_without_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)

    def recorder(label: str, frame: Frame) -> CameraFrameArtifact:
        artifact = record_frame_digest(label, frame)
        if label.startswith("servo-commit-"):
            return replace(artifact, raw_sha256="0" * 64)
        return artifact

    control = CompleteControl()
    result = _run(SequenceSource([_frame(1)]), control, recorder=recorder)

    assert result.terminal_reason is CameraServoTerminalReason.OBSERVATION_EXCEPTION
    assert result.steps == ()
    assert control.calls == []
    attempt = result.arm_attempts[0]
    assert attempt.outcome is CameraServoArmOutcome.EVALUATION_ERROR
    assert attempt.commit is None
    assert attempt.exception is not None


@pytest.mark.parametrize("failure_stage", ["readiness", "guard", "recorder"])
def test_final_commit_exceptions_veto_without_input(
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    _patch_pipeline(monkeypatch)
    commit_id = _COMMIT_FRAME_OFFSET + 1
    recorder = record_frame_digest
    if failure_stage == "readiness":
        original_readiness = camera_servo.evaluate_client_input_readiness

        def readiness(frame: Frame) -> ClientInputReadiness:
            if frame.frame_id == commit_id:
                raise RuntimeError("commit readiness failed")
            return original_readiness(frame)

        monkeypatch.setattr(camera_servo, "evaluate_client_input_readiness", readiness)
    elif failure_stage == "guard":
        original_guard = camera_servo.evaluate_camera_arm_guard
        guard_calls = 0

        def guard(decision: Frame, arm: Frame) -> Any:
            nonlocal guard_calls
            guard_calls += 1
            if guard_calls == 2:
                raise RuntimeError("commit guard failed")
            return original_guard(decision, arm)

        monkeypatch.setattr(camera_servo, "evaluate_camera_arm_guard", guard)
    else:
        def recorder(label: str, frame: Frame) -> CameraFrameArtifact:
            if label.startswith("servo-commit-"):
                raise OSError("commit recording failed")
            return record_frame_digest(label, frame)

    control = CompleteControl()
    result = _run(SequenceSource([_frame(1)]), control, recorder=recorder)

    assert result.terminal_reason is CameraServoTerminalReason.OBSERVATION_EXCEPTION
    assert result.steps == ()
    assert control.calls == []
    attempt = result.arm_attempts[0]
    if failure_stage == "guard":
        assert attempt.outcome is CameraServoArmOutcome.COMMIT_STOP
        assert attempt.commit is not None
        assert attempt.commit.outcome is CameraServoCommitOutcome.GUARD_ERROR
    elif failure_stage == "readiness":
        assert attempt.outcome is CameraServoArmOutcome.COMMIT_STOP
        assert attempt.commit is not None
        assert attempt.commit.outcome is CameraServoCommitOutcome.EVALUATION_ERROR
    else:
        assert attempt.outcome is CameraServoArmOutcome.EVALUATION_ERROR
        assert attempt.commit is None


def test_retained_arm_evidence_binds_final_commit_and_independent_timing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    _patch_pipeline(monkeypatch, events=events)
    clock_values = iter((0.0, 0.0, 0.0, 10.0, 10.0, 10.0, 10.5, 12.0))
    control = CompleteControl(events)

    result = _run(
        SequenceSource([_frame(1)], events),
        control,
        clock=lambda: next(clock_values),
    )

    assert control.calls == ["preflight", ("wheel", 400, 50, 1)]
    attempt = result.arm_attempts[0]
    assert attempt.outcome is CameraServoArmOutcome.RETAINED
    assert attempt.commit is not None
    assert attempt.commit.outcome is CameraServoCommitOutcome.RETAINED
    assert attempt.commit.accepted_arm_artifact == attempt.arm_artifact
    assert attempt.commit.accepted_arm_captured_monotonic_s == 1.5
    assert attempt.commit.artifact.frame_id == _COMMIT_FRAME_OFFSET + 1
    assert attempt.commit.captured_monotonic_s == 1.75
    assert attempt.commit.guard is not None
    assert attempt.commit.guard.decision_payload_sha256 == attempt.arm_raw_sha256
    assert attempt.commit.guard.arm_payload_sha256 == attempt.commit.raw_sha256
    assert attempt.age.origin_clock_s == 10.0
    assert attempt.age.final_clock_s == 10.5
    assert attempt.age.age_s == 0.5
    assert attempt.age.status is CameraServoArmAgeStatus.WITHIN_LIMIT
    assert len(result.steps) == 1 and result.steps[0].arm is attempt
    assert f"production:{_COMMIT_FRAME_OFFSET + 1}" not in events
    with pytest.raises(FrozenInstanceError):
        cast(Any, attempt.commit).outcome = CameraServoCommitOutcome.GUARD_REJECTED
    with pytest.raises(FrozenInstanceError):
        cast(Any, attempt.age).age_s = 0.0


def test_readiness_veto_stops_before_production_guidance_or_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    _patch_pipeline(monkeypatch, readiness={1: _not_ready()}, events=events)

    result = _run(SequenceSource([_frame(1)]), CompleteControl())

    assert not result.passed
    assert result.terminal_reason is CameraServoTerminalReason.READINESS_LOST
    assert events == ["readiness:1"]
    assert result.steps == ()


def test_post_action_readiness_loss_stops_without_post_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    _patch_pipeline(monkeypatch, readiness={2: _not_ready()}, events=events)
    control = CompleteControl()

    result = _run(SequenceSource([_frame(1), _frame(2)]), control)

    assert result.terminal_reason is CameraServoTerminalReason.READINESS_LOST
    assert "production:2" not in events
    assert len(result.steps) == 1
    assert control.calls.count("preflight") == 1


@pytest.mark.parametrize("frame_id", [1, 2])
def test_non_fail_closed_production_rejection_never_authorizes_more_input(
    monkeypatch: pytest.MonkeyPatch,
    frame_id: int,
) -> None:
    _patch_pipeline(
        monkeypatch,
        production={frame_id: _production(fail_closed=False)},
    )
    frames = [_frame(1)] if frame_id == 1 else [_frame(1), _frame(2)]
    control = CompleteControl()

    result = _run(SequenceSource(frames), control)

    assert not result.passed
    assert (
        result.terminal_reason
        is CameraServoTerminalReason.PRODUCTION_REJECTION_NOT_FAIL_CLOSED
    )
    assert control.calls.count("preflight") == (0 if frame_id == 1 else 1)


def test_insufficient_guidance_stops_before_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch, guidance={1: _refusal()})
    control = CompleteControl()

    result = _run(SequenceSource([_frame(1)]), control)

    assert result.terminal_reason is CameraServoTerminalReason.INSUFFICIENT_GUIDANCE
    assert result.final_guidance is not None
    assert not result.passed
    assert control.calls == []


@pytest.mark.parametrize(
    "control", [UnsafeControl(), ShortReceiptControl(), ExcessReceiptControl()]
)
def test_safety_or_receipt_exception_is_terminal_without_post_capture(
    monkeypatch: pytest.MonkeyPatch,
    control: CompleteControl,
) -> None:
    _patch_pipeline(monkeypatch)
    source = SequenceSource([_frame(1), _frame(2)])

    result = _run(source, control)

    assert result.terminal_reason is CameraServoTerminalReason.INPUT_EXCEPTION
    assert not result.passed
    assert len(result.steps) == 1
    assert result.steps[0].post is None
    assert result.steps[0].exception is not None
    if isinstance(control, ExcessReceiptControl):
        assert result.steps[0].receipt is not None
    assert len(source.frames) == 3


def test_settle_exception_is_terminal_without_post_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    source = SequenceSource([_frame(1), _frame(2)])

    def fail_settle(_seconds: float) -> None:
        raise OSError("settle failed")

    result = _run(source, CompleteControl(), sleeper=fail_settle)

    assert result.terminal_reason is CameraServoTerminalReason.SETTLE_EXCEPTION
    assert result.steps[0].receipt is not None
    assert result.steps[0].post is None
    assert len(source.frames) == 3


def test_recording_failure_stops_before_any_evaluator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    _patch_pipeline(monkeypatch, events=events)

    def fail_recorder(_label: str, _frame: Frame) -> CameraFrameArtifact:
        events.append("record-failed")
        raise OSError("private artifact failed")

    result = _run(
        SequenceSource([_frame(1)]),
        CompleteControl(),
        recorder=fail_recorder,
    )

    assert result.terminal_reason is CameraServoTerminalReason.OBSERVATION_EXCEPTION
    assert events == ["record-failed"]
    assert result.initial is None
    assert result.steps == ()


def test_post_capture_failure_stops_after_one_receipted_primitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    control = CompleteControl()

    result = _run(SequenceSource([_frame(1)]), control)

    assert result.terminal_reason is CameraServoTerminalReason.OBSERVATION_EXCEPTION
    assert len(result.steps) == 1
    assert result.steps[0].receipt is not None
    assert result.steps[0].post is None
    assert control.calls.count("preflight") == 1


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        ("input", CameraServoTerminalReason.INPUT_EXCEPTION),
        ("settle", CameraServoTerminalReason.SETTLE_EXCEPTION),
        ("capture", CameraServoTerminalReason.OBSERVATION_EXCEPTION),
    ],
)
def test_exception_steps_record_fresh_terminal_elapsed_time(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    reason: CameraServoTerminalReason,
) -> None:
    _patch_pipeline(monkeypatch)
    source = SequenceSource([_frame(1), _frame(2)])
    control: CompleteControl = CompleteControl()

    def sleeper(_seconds: float) -> None:
        return None

    if failure == "input":
        control = UnsafeControl()
    elif failure == "settle":
        def fail_sleep(_seconds: float) -> None:
            raise OSError("terminal settle failure")

        sleeper = fail_sleep
    else:
        source = SequenceSource([_frame(1)])
    times = iter((0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.5, 9.0))

    result = _run(
        source,
        control,
        sleeper=sleeper,
        clock=lambda: next(times),
    )

    assert result.terminal_reason is reason
    assert result.elapsed_s == 9.0
    assert result.steps[0].elapsed_s == 9.0
    assert result.steps[0].exception is not None


def test_terminal_clock_error_takes_precedence_over_input_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    times = iter((0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.5, float("nan")))

    result = _run(
        SequenceSource([_frame(1)]),
        UnsafeControl(),
        clock=lambda: next(times),
    )

    assert result.terminal_reason is CameraServoTerminalReason.CLOCK_ERROR
    assert result.exception is not None
    assert result.exception.exception_type == "ValueError"
    assert result.steps[0].exception is not None
    assert result.steps[0].exception.exception_type == "CameraPreflightError"


def test_two_consecutive_no_effect_steps_stop_as_stagnation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(
        monkeypatch,
        effects={(1, 2): _effect(False), (2, 3): _effect(False)},
        guidance={
            1: _guidance(scale=0.70),
            2: _guidance(scale=0.75),
            3: _guidance(scale=0.80),
        },
    )
    control = CompleteControl()

    result = _run(SequenceSource([_frame(1), _frame(2), _frame(3)]), control)

    assert result.terminal_reason is CameraServoTerminalReason.NO_EFFECT_STAGNATION
    assert len(result.steps) == 2
    assert [step.effect.effect_observed for step in result.steps if step.effect] == [
        False,
        False,
    ]
    assert [step.progress.status for step in result.steps if step.progress] == [
        CameraServoProgressStatus.IMPROVED,
        CameraServoProgressStatus.IMPROVED,
    ]
    assert [step.stagnant_steps_after for step in result.steps] == [1, 2]
    assert control.calls.count("preflight") == 2


def test_same_sign_worsening_zoom_error_stops_after_one_primitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(
        monkeypatch,
        guidance={
            1: _guidance(scale=0.80),
            2: _guidance(scale=0.75),
        },
    )
    control = CompleteControl()

    result = _run(SequenceSource([_frame(1), _frame(2)]), control)

    assert result.terminal_reason is CameraServoTerminalReason.WORSENING_GUIDANCE
    assert len(result.steps) == 1
    progress = result.steps[0].progress
    assert progress is not None
    assert progress.status is CameraServoProgressStatus.WORSENED
    assert progress.after_absolute_log_scale_error > (
        progress.before_absolute_log_scale_error
    )
    with pytest.raises(ValueError, match="status must match"):
        replace(progress, status=CameraServoProgressStatus.IMPROVED)
    with pytest.raises(ValueError, match="stagnation counter"):
        replace(result.steps[0], stagnant_steps_after=1)
    assert control.calls.count("preflight") == 1


def test_unchanged_zoom_error_twice_stops_as_no_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(
        monkeypatch,
        guidance={index: _guidance(scale=0.80) for index in (1, 2, 3)},
    )

    result = _run(
        SequenceSource([_frame(1), _frame(2), _frame(3)]), CompleteControl()
    )

    assert result.terminal_reason is CameraServoTerminalReason.NO_EFFECT_STAGNATION
    assert [step.progress.status for step in result.steps if step.progress] == [
        CameraServoProgressStatus.STAGNANT,
        CameraServoProgressStatus.STAGNANT,
    ]
    assert [step.stagnant_steps_after for step in result.steps] == [1, 2]


def test_helpful_same_sign_decrease_resets_prior_stagnation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(
        monkeypatch,
        effects={(1, 2): _effect(False), (2, 3): _effect(True)},
        guidance={
            1: _guidance(scale=0.70),
            2: _guidance(scale=0.75),
            3: _guidance(scale=0.80),
        },
    )

    result = _run(
        SequenceSource([_frame(1), _frame(2), _frame(3)]),
        CompleteControl(),
        limits=CameraServoLimits(max_primitives=2),
    )

    assert result.terminal_reason is CameraServoTerminalReason.PRIMITIVE_BUDGET_EXHAUSTED
    assert [step.progress.status for step in result.steps if step.progress] == [
        CameraServoProgressStatus.IMPROVED,
        CameraServoProgressStatus.IMPROVED,
    ]
    assert [step.stagnant_steps_after for step in result.steps] == [1, 0]


def test_exact_post_action_repeat_stops_after_first_semantic_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch, effects={(1, 2): _effect(False)})
    repeated_digest = "a" * 64
    monkeypatch.setattr(camera_servo, "_world_state_digest", lambda _frame: repeated_digest)
    control = CompleteControl()

    result = _run(SequenceSource([_frame(1), _frame(2)]), control)

    assert result.terminal_reason is CameraServoTerminalReason.REPEATED_STATE
    assert len(result.steps) == 1
    assert result.steps[0].pre_world_state_digest == repeated_digest
    assert result.steps[0].post_world_state_digest == repeated_digest
    assert result.steps[0].progress is None
    assert control.calls.count("preflight") == 1


def test_post_action_ambiguous_guidance_refuses_without_second_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch, guidance={2: _refusal()})
    control = CompleteControl()

    result = _run(SequenceSource([_frame(1), _frame(2)]), control)

    assert result.terminal_reason is CameraServoTerminalReason.INSUFFICIENT_GUIDANCE
    assert len(result.steps) == 1
    assert result.steps[0].post_guidance is not None
    assert result.steps[0].progress is None
    assert control.calls.count("preflight") == 1


def test_guidance_reversal_stops_as_oscillation_after_one_primitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(
        monkeypatch,
        guidance={
            1: _guidance(CameraGuidanceDirection.POSITIVE),
            2: _guidance(CameraGuidanceDirection.NEGATIVE),
        },
    )
    control = CompleteControl()

    result = _run(SequenceSource([_frame(1), _frame(2)]), control)

    assert result.terminal_reason is CameraServoTerminalReason.OSCILLATION
    assert len(result.steps) == 1
    assert result.steps[0].post_guidance is not None
    assert result.steps[0].direction_reversed
    assert result.steps[0].progress is None
    with pytest.raises(ValueError, match="exactly match"):
        replace(result.steps[0], direction_reversed=False)
    assert control.calls.count("preflight") == 1


def test_repeated_world_state_stops_before_a_third_primitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    digests = {1: "a" * 64, 2: "b" * 64, 3: "a" * 64}
    monkeypatch.setattr(
        camera_servo,
        "_world_state_digest",
        lambda frame: digests[_logical_frame_id(frame)],
    )
    control = CompleteControl()

    result = _run(SequenceSource([_frame(1), _frame(2), _frame(3)]), control)

    assert result.terminal_reason is CameraServoTerminalReason.REPEATED_STATE
    assert len(result.steps) == 2
    assert control.calls.count("preflight") == 2


def test_primitive_budget_exhaustion_is_terminal_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(
        monkeypatch,
        guidance={
            1: _guidance(scale=0.70),
            2: _guidance(scale=0.75),
            3: _guidance(scale=0.80),
        },
    )
    control = CompleteControl()

    result = _run(
        SequenceSource([_frame(1), _frame(2), _frame(3)]),
        control,
        limits=CameraServoLimits(max_primitives=2),
    )

    assert result.terminal_reason is CameraServoTerminalReason.PRIMITIVE_BUDGET_EXHAUSTED
    assert len(result.steps) == 2
    assert control.calls.count("preflight") == 2


def test_time_budget_exhaustion_cannot_be_overridden_by_post_production_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch, production={2: _production(passed=True)})
    times = iter((0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.5, 2.0))

    result = _run(
        SequenceSource([_frame(1), _frame(2)]),
        CompleteControl(),
        clock=lambda: next(times),
        limits=CameraServoLimits(max_elapsed_s=1.0),
    )

    assert not result.passed
    assert result.terminal_reason is CameraServoTerminalReason.TIME_BUDGET_EXHAUSTED
    assert result.final is not None and result.final.production is not None
    assert result.final.production.passed


def test_slow_guidance_exhausts_time_budget_before_any_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    times = iter((0.0, 0.0, 2.0))
    control = CompleteControl()
    source = SequenceSource([_frame(1), _frame(2)])

    result = _run(
        source,
        control,
        clock=lambda: next(times),
        limits=CameraServoLimits(max_elapsed_s=1.0),
    )

    assert result.terminal_reason is CameraServoTerminalReason.TIME_BUDGET_EXHAUSTED
    assert result.steps == ()
    assert control.calls == []
    assert len(source.frames) == 5


def test_invalid_initial_clock_fails_before_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    source = SequenceSource([_frame(1)])

    result = _run(source, CompleteControl(), clock=lambda: float("nan"))

    assert result.terminal_reason is CameraServoTerminalReason.CLOCK_ERROR
    assert result.initial is None
    assert len(source.frames) == 3


@pytest.mark.parametrize("settle_s", [0.0, 11.0, float("inf"), True])
def test_settle_interval_has_a_hard_absolute_bound(settle_s: float) -> None:
    with pytest.raises(ValueError, match="settle_s"):
        _run(
            SequenceSource([]),
            CompleteControl(),
            settle_s=cast(Any, settle_s),
        )


def test_guidance_never_passes_when_production_remains_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)

    result = _run(
        SequenceSource([_frame(1), _frame(2)]),
        CompleteControl(),
        limits=CameraServoLimits(max_primitives=1),
    )

    assert not result.passed
    assert result.terminal_reason is CameraServoTerminalReason.PRIMITIVE_BUDGET_EXHAUSTED
    assert result.final_guidance is not None
    assert not result.final_guidance.can_accept


def test_world_effect_ignores_candidate_and_ui_pixels_but_detects_world_changes() -> None:
    profile = load_varrock_east_iron_profile()
    excluded_only = bytearray(_BLANK_PAYLOAD)
    excluded_regions = tuple(
        dict.fromkeys(
            (
                *varrock_east_iron_scene_excluded_regions(profile),
                *(policy.region for policy in GAMEPLAY_CHROME_POLICIES),
            )
        )
    )
    for x, y, width, height in excluded_regions:
        for row in range(y, y + height):
            start = (row * _WIDTH + x) * 4
            excluded_only[start : start + width * 4] = bytes([255]) * (width * 4)
    excluded_effect = measure_world_landmark_effect(
        _frame(1), _frame(2, bytes(excluded_only))
    )
    assert not excluded_effect.effect_observed
    assert excluded_effect.changed_landmark_count == 0

    world = bytearray(_BLANK_PAYLOAD)
    changed_landmarks = tuple(
        next(item for item in profile.scene_landmarks if item.macro_zone is zone)
        for zone in (
            MacroZone.NORTH_WEST,
            MacroZone.NORTH_EAST,
            MacroZone.SOUTH_WEST,
        )
    )
    for landmark in changed_landmarks:
        x, y, width, height = landmark.region
        for row in range(y, y + height // 2):
            start = (row * _WIDTH + x) * 4
            world[start : start + width * 4] = bytes([255]) * (width * 4)
    world_effect = measure_world_landmark_effect(_frame(3), _frame(4, bytes(world)))
    assert world_effect.effect_observed
    assert world_effect.changed_landmark_count == 3
    assert {item.landmark_id for item in world_effect.landmarks if item.changed} == {
        item.landmark_id for item in changed_landmarks
    }


def test_world_effect_threshold_quorum_and_zone_boundaries_are_frozen() -> None:
    profile = load_varrock_east_iron_profile()
    assert WORLD_EFFECT_REQUIRED_LANDMARKS == 3
    assert WORLD_EFFECT_REQUIRED_ZONES == 3

    below = WorldLandmarkEffectItem(
        landmark_id=profile.scene_landmarks[0].landmark_id,
        zone=profile.scene_landmarks[0].macro_zone,
        descriptor_distance=WORLD_EFFECT_DESCRIPTOR_EPSILON - 1e-12,
        changed=False,
    )
    at = WorldLandmarkEffectItem(
        landmark_id=profile.scene_landmarks[0].landmark_id,
        zone=profile.scene_landmarks[0].macro_zone,
        descriptor_distance=WORLD_EFFECT_DESCRIPTOR_EPSILON,
        changed=True,
    )
    assert not below.changed
    assert at.changed

    def evidence(changed_indexes: set[int]) -> WorldLandmarkEffect:
        items = tuple(
            WorldLandmarkEffectItem(
                landmark_id=landmark.landmark_id,
                zone=landmark.macro_zone,
                descriptor_distance=(
                    WORLD_EFFECT_DESCRIPTOR_EPSILON if index in changed_indexes else 0.0
                ),
                changed=index in changed_indexes,
            )
            for index, landmark in enumerate(profile.scene_landmarks)
        )
        distances = tuple(item.descriptor_distance for item in items)
        zones = tuple(
            zone
            for zone in MacroZone
            if any(item.changed and item.zone is zone for item in items)
        )
        return WorldLandmarkEffect(
            landmarks=items,
            mean_descriptor_distance=sum(distances) / len(distances),
            maximum_descriptor_distance=max(distances),
            changed_landmark_count=len(changed_indexes),
            changed_zones=zones,
            effect_observed=(len(changed_indexes) >= 3 and len(zones) >= 3),
        )

    assert not evidence({0, 1, 2}).effect_observed
    assert evidence({0, 2, 4}).effect_observed


def test_step_and_result_evidence_are_immutable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch, production={2: _production(passed=True)})
    result = _run(SequenceSource([_frame(1), _frame(2)]), CompleteControl())

    with pytest.raises(FrozenInstanceError):
        cast(Any, result).terminal_reason = CameraServoTerminalReason.INSUFFICIENT_GUIDANCE
    with pytest.raises(FrozenInstanceError):
        cast(Any, result.steps[0]).index = 9


def test_public_runner_does_not_allow_evaluator_or_guidance_injection() -> None:
    parameters = inspect.signature(run_bounded_camera_servo).parameters
    assert "evaluator" not in parameters
    assert "readiness_evaluator" not in parameters
    assert "guidance_evaluator" not in parameters
    assert "effect_evaluator" not in parameters
