"""Deterministic contracts for the fixed Issue #31 R2 bridge capture."""

from __future__ import annotations

import ast
import inspect
import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.perception.resource import ResourceVisualState
from mining_automation.perception.scene_landmarks import MacroZone
from mining_automation.validation import camera_bridge_capture
from mining_automation.validation.camera_arm_guard import (
    CAMERA_ARM_GUARD_STRUCTURAL_REGIONS,
)
from mining_automation.validation.camera_bridge_capture import (
    CAMERA_BRIDGE_CAPTURE_HOLD_SECONDS,
    CAMERA_BRIDGE_CAPTURE_ID,
    CAMERA_BRIDGE_CAPTURE_MAXIMUM_PHYSICAL_PRIMITIVES,
    CAMERA_BRIDGE_CAPTURE_SETTLE_SECONDS,
    CAMERA_BRIDGE_CAPTURE_VERSION,
    CameraBridgeCaptureInputState,
    CameraBridgeCaptureTerminalReason,
    camera_bridge_action_transition,
    camera_bridge_capture_plan,
    run_fixed_camera_bridge_capture,
)
from mining_automation.validation.camera_evaluation import (
    CameraEvaluation,
    CameraResourceEvaluation,
)
from mining_automation.validation.camera_plan import (
    EXPECTED_CLIENT_HEIGHT,
    EXPECTED_CLIENT_WIDTH,
    CameraHoldKey,
    CameraInputNotAttemptedError,
    CameraInputOperation,
    CameraInputReceipt,
    CameraKeyHold,
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


def _changed_payload(value: int = 255) -> bytes:
    payload = bytearray(_BLANK)
    _landmark_id, _zone, (x, y, width, height) = (
        CAMERA_ARM_GUARD_STRUCTURAL_REGIONS[0]
    )
    pixel = bytes((value, value, value, 0))
    for row in range(y, y + height):
        start = (row * EXPECTED_CLIENT_WIDTH + x) * 4
        payload[start : start + width * 4] = pixel * width
    return bytes(payload)


def _post_payload() -> bytes:
    return bytes((1, 0, 0, 0)) + _BLANK[4:]


class SequenceSource:
    def __init__(self, frames: list[Frame]) -> None:
        self.frames = frames
        self.captures: list[int] = []

    def capture(self) -> Frame:
        if not self.frames:
            raise RuntimeError("capture exhausted")
        frame = self.frames.pop(0)
        self.captures.append(frame.frame_id)
        return frame


class CompleteControl:
    def __init__(
        self,
        *,
        focused: bool = True,
        width: int = EXPECTED_CLIENT_WIDTH,
        height: int = EXPECTED_CLIENT_HEIGHT,
        down_completed: int = 1,
        up_completed: int = 1,
        preflight_error: Exception | None = None,
    ) -> None:
        self.focused = focused
        self.width = width
        self.height = height
        self.down_completed = down_completed
        self.up_completed = up_completed
        self.preflight_error = preflight_error
        self.calls: list[object] = []

    def preflight(self) -> CameraPreflightReceipt:
        self.calls.append("preflight")
        if self.preflight_error is not None:
            raise self.preflight_error
        return CameraPreflightReceipt(self.focused, self.width, self.height)

    def key_down(self, key: str) -> CameraInputReceipt:
        self.calls.append(("key_down", key))
        return CameraInputReceipt(
            CameraInputOperation.KEY_DOWN,
            1,
            self.down_completed,
        )

    def key_up(self, key: str) -> CameraInputReceipt:
        self.calls.append(("key_up", key))
        return CameraInputReceipt(
            CameraInputOperation.KEY_UP,
            1,
            self.up_completed,
        )

    def click_compass(self, x: int, y: int) -> CameraInputReceipt:
        raise AssertionError(f"bridge must not click ({x}, {y})")

    def scroll_camera(self, x: int, y: int, detents: int) -> CameraInputReceipt:
        raise AssertionError(f"bridge must not scroll ({x}, {y}, {detents})")

    def drag_camera(
        self,
        x: int,
        y: int,
        delta_x: int,
        delta_y: int,
    ) -> tuple[CameraInputReceipt, CameraInputReceipt, CameraInputReceipt]:
        raise AssertionError(
            f"bridge must not drag ({x}, {y}, {delta_x}, {delta_y})"
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
            MacroZone.NORTH_EAST,
            MacroZone.SOUTH_WEST,
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


def _patch_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    readiness: dict[int, ClientInputReadiness] | None = None,
    production: dict[int, CameraEvaluation] | None = None,
) -> None:
    readiness = readiness or {}
    production = production or {}
    monkeypatch.setattr(
        camera_bridge_capture,
        "evaluate_client_input_readiness",
        lambda frame: readiness.get(frame.frame_id, _ready()),
    )
    monkeypatch.setattr(
        camera_bridge_capture,
        "evaluate_varrock_east_camera",
        lambda frame: production.get(frame.frame_id, _production()),
    )


def _run(
    source: SequenceSource,
    control: CompleteControl,
    *,
    sleeper: Callable[[float], None] | None = None,
    recorder: Callable[[str, Frame], CameraFrameArtifact] | None = None,
    clock: Callable[[], float] | None = None,
    pre_input_guard: Callable[..., None] | None = None,
    final_input_guard: Callable[..., None] | None = None,
):  # type: ignore[no-untyped-def]
    kwargs: dict[str, Any] = {
        "clock": clock or iter((0.0, 0.5, 0.6)).__next__,
        "final_input_guard": final_input_guard,
        "pre_input_guard": pre_input_guard,
        "sleeper": sleeper or (lambda _seconds: None),
    }
    if recorder is not None:
        kwargs["recorder"] = recorder
    return run_fixed_camera_bridge_capture(source, control, **kwargs)


def _happy_frames() -> list[Frame]:
    return [_frame(1), _frame(2), _frame(3), _frame(4, _post_payload())]


def test_fixed_public_policy_has_no_controller_or_perception_injection() -> None:
    plan = camera_bridge_capture_plan()
    signature = inspect.signature(run_fixed_camera_bridge_capture)
    source = Path(camera_bridge_capture.__file__).read_text(encoding="utf-8")
    imports = tuple(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import | ast.ImportFrom)
    )

    assert CAMERA_BRIDGE_CAPTURE_ID == "issue31-fixed-camera-bridge-capture-r2"
    assert CAMERA_BRIDGE_CAPTURE_VERSION == "1.0.0"
    assert CAMERA_BRIDGE_CAPTURE_HOLD_SECONDS == 0.043
    assert CAMERA_BRIDGE_CAPTURE_SETTLE_SECONDS == 1.0
    assert CAMERA_BRIDGE_CAPTURE_MAXIMUM_PHYSICAL_PRIMITIVES == 1
    assert plan.name == CAMERA_BRIDGE_CAPTURE_ID
    assert plan.actions == (
        CameraKeyHold(CameraHoldKey.RIGHT, CAMERA_BRIDGE_CAPTURE_HOLD_SECONDS),
    )
    assert set(signature.parameters) == {
        "source",
        "control",
        "sleeper",
        "recorder",
        "clock",
        "pre_input_guard",
        "final_input_guard",
    }
    assert all(
        "robust" not in (node.module or "")
        for node in imports
        if isinstance(node, ast.ImportFrom)
    )
    assert "robust_registration" not in source


def test_exact_happy_path_records_one_receipted_primitive_and_stable_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    source = SequenceSource(_happy_frames())
    control = CompleteControl()
    sleeps: list[float] = []

    result = _run(source, control, sleeper=sleeps.append)

    assert result.terminal_reason is CameraBridgeCaptureTerminalReason.CAPTURE_COMPLETE
    assert result.protocol_completed
    assert result.input_state is CameraBridgeCaptureInputState.COMPLETE
    assert source.captures == [1, 2, 3, 4]
    assert control.calls == [
        "preflight",
        ("key_down", "right"),
        ("key_up", "right"),
    ]
    assert sleeps == [0.043, 1.0]
    assert result.receipt is not None
    assert result.receipt.plan is camera_bridge_capture_plan()
    action = result.receipt.action_receipts[0]
    assert [item.operation for item in action.input_receipts] == [
        CameraInputOperation.KEY_DOWN,
        CameraInputOperation.KEY_UP,
    ]
    assert all(item.complete for item in action.input_receipts)
    payload = result.as_dict()
    assert payload["fixed_policy"] == {
        "caller_selectable_axis": False,
        "caller_selectable_coordinate": False,
        "caller_selectable_direction": False,
        "caller_selectable_evaluator": False,
        "caller_selectable_magnitude": False,
        "caller_selectable_plan": False,
        "hold_seconds": 0.043,
        "key": "right",
        "maximum_physical_primitives": 1,
        "post_action_settle_seconds": 1.0,
    }
    assert payload["authority"] == {
        "can_accept": False,
        "can_authorize_camera_input": False,
        "can_expose_resources": False,
        "can_validate_scene": False,
        "diagnostic_registration_can_override_production": False,
        "input_receipt_is_scene_acceptance": False,
        "production_remains_sole_scene_authority": True,
    }
    assert set(payload["frames"]) == {"decision", "arm", "commit", "post"}
    json.dumps(payload, allow_nan=False, sort_keys=True)


def test_transition_is_exact_commit_to_post_and_has_no_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    result = _run(SequenceSource(_happy_frames()), CompleteControl())

    transition = camera_bridge_action_transition(
        result,
        evidence_report_sha256="f" * 64,
    )

    assert transition is not None
    assert result.commit is not None and result.post is not None
    assert transition.source_sha256 == result.commit.artifact.raw_sha256
    assert transition.target_sha256 == result.post.artifact.raw_sha256
    assert transition.receipt_verified
    assert transition.as_dict()["authority"] == {
        "can_accept": False,
        "can_authorize_camera_input": False,
        "can_expose_resources": False,
        "can_validate_scene": False,
    }


@pytest.mark.parametrize("frame_id", [1, 2, 3])
@pytest.mark.parametrize("mode", ["readiness", "production_pass", "fail_open"])
def test_every_preinput_safety_veto_sends_zero_physical_input(
    monkeypatch: pytest.MonkeyPatch,
    frame_id: int,
    mode: str,
) -> None:
    readiness = {frame_id: _not_ready()} if mode == "readiness" else {}
    production = (
        {frame_id: _production(passed=True)}
        if mode == "production_pass"
        else {frame_id: _production(fail_closed=False)}
        if mode == "fail_open"
        else {}
    )
    _patch_pipeline(monkeypatch, readiness=readiness, production=production)
    control = CompleteControl()

    result = _run(
        SequenceSource([_frame(index) for index in range(1, 5)]),
        control,
    )

    assert result.input_state is CameraBridgeCaptureInputState.NONE
    assert not any(isinstance(call, tuple) for call in control.calls)
    assert control.calls == ([] if frame_id < 3 else ["preflight"])
    expected = {
        "readiness": CameraBridgeCaptureTerminalReason.READINESS_LOST,
        "production_pass": CameraBridgeCaptureTerminalReason.PRODUCTION_PASS,
        "fail_open": (
            CameraBridgeCaptureTerminalReason.PRODUCTION_REJECTION_NOT_FAIL_CLOSED
        ),
    }
    assert result.terminal_reason is expected[mode]


@pytest.mark.parametrize(
    "frames",
    [
        [_frame(1), _frame(1)],
        [_frame(1), _frame(2), _frame(2)],
    ],
)
def test_nonfresh_arm_or_commit_sends_zero_input(
    monkeypatch: pytest.MonkeyPatch,
    frames: list[Frame],
) -> None:
    _patch_pipeline(monkeypatch)
    control = CompleteControl()

    result = _run(SequenceSource(frames), control)

    assert result.terminal_reason is CameraBridgeCaptureTerminalReason.NON_FRESH_OBSERVATION
    assert result.input_state is CameraBridgeCaptureInputState.NONE
    assert not any(isinstance(call, tuple) for call in control.calls)


def test_world_change_before_or_during_preflight_sends_zero_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    changed = _changed_payload()
    before_preflight = CompleteControl()
    during_preflight = CompleteControl()

    first = _run(
        SequenceSource([_frame(1), _frame(2, changed)]),
        before_preflight,
    )
    second = _run(
        SequenceSource([_frame(1), _frame(2), _frame(3, changed)]),
        during_preflight,
    )

    assert first.terminal_reason is CameraBridgeCaptureTerminalReason.WORLD_CHANGED
    assert before_preflight.calls == []
    assert second.terminal_reason is CameraBridgeCaptureTerminalReason.WORLD_CHANGED
    assert during_preflight.calls == ["preflight"]
    assert first.input_state is second.input_state is CameraBridgeCaptureInputState.NONE


@pytest.mark.parametrize(
    ("focused", "width", "height"),
    [
        (False, EXPECTED_CLIENT_WIDTH, EXPECTED_CLIENT_HEIGHT),
        (True, EXPECTED_CLIENT_WIDTH - 1, EXPECTED_CLIENT_HEIGHT),
        (True, EXPECTED_CLIENT_WIDTH, EXPECTED_CLIENT_HEIGHT - 1),
    ],
)
def test_unsupported_focus_or_geometry_stops_before_commit_and_input(
    monkeypatch: pytest.MonkeyPatch,
    focused: bool,
    width: int,
    height: int,
) -> None:
    _patch_pipeline(monkeypatch)
    source = SequenceSource(_happy_frames())
    control = CompleteControl(focused=focused, width=width, height=height)

    result = _run(source, control)

    assert result.terminal_reason is CameraBridgeCaptureTerminalReason.PREFLIGHT_REJECTED
    assert source.captures == [1, 2]
    assert control.calls == ["preflight"]
    assert result.input_state is CameraBridgeCaptureInputState.NONE


@pytest.mark.parametrize("guard_name", ["pre", "final"])
def test_external_guard_failure_occurs_after_commit_and_before_input(
    monkeypatch: pytest.MonkeyPatch,
    guard_name: str,
) -> None:
    _patch_pipeline(monkeypatch)
    control = CompleteControl()
    observed: list[tuple[int, int, int]] = []

    def reject(*evidence: object) -> None:
        observed.append(
            tuple(item.artifact.frame_id for item in evidence)  # type: ignore[attr-defined]
        )
        raise RuntimeError("provenance changed")

    result = _run(
        SequenceSource(_happy_frames()),
        control,
        pre_input_guard=reject if guard_name == "pre" else None,
        final_input_guard=reject if guard_name == "final" else None,
    )

    assert observed == [(1, 2, 3)]
    assert control.calls == ["preflight"]
    assert result.input_state is CameraBridgeCaptureInputState.NONE
    assert result.arm_age is not None
    assert result.arm_age.status is CameraServoArmAgeStatus.NOT_REACHED


@pytest.mark.parametrize(
    "clock_values",
    [
        (0.0, 1.0),
        (0.5, 0.4),
        (0.0, float("nan")),
    ],
)
def test_expired_invalid_or_regressing_arm_clock_sends_zero_input(
    monkeypatch: pytest.MonkeyPatch,
    clock_values: tuple[float, float],
) -> None:
    _patch_pipeline(monkeypatch)
    control = CompleteControl()

    result = _run(
        SequenceSource(_happy_frames()),
        control,
        clock=iter(clock_values).__next__,
    )

    assert result.input_state is CameraBridgeCaptureInputState.NONE
    assert control.calls == ["preflight"]
    assert result.terminal_reason in (
        CameraBridgeCaptureTerminalReason.ARM_FRESHNESS_EXPIRED,
        CameraBridgeCaptureTerminalReason.CLOCK_ERROR,
    )


def test_short_receipt_is_partial_unknown_and_never_captures_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    source = SequenceSource(_happy_frames())
    control = CompleteControl(down_completed=0)

    result = _run(source, control)

    assert result.terminal_reason is CameraBridgeCaptureTerminalReason.INPUT_EXCEPTION
    assert result.input_state is CameraBridgeCaptureInputState.PARTIAL_OR_UNKNOWN
    assert result.receipt is None and result.post is None
    assert source.captures == [1, 2, 3]
    assert control.calls == ["preflight", ("key_down", "right")]


def test_definite_preinput_key_veto_is_recorded_as_zero_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    source = SequenceSource(_happy_frames())

    class PreInputVetoControl(CompleteControl):
        def key_down(self, key: str) -> CameraInputReceipt:
            self.calls.append(("key_down", key))
            raise CameraInputNotAttemptedError("focus changed before send")

    control = PreInputVetoControl()
    result = _run(source, control)

    assert result.terminal_reason is CameraBridgeCaptureTerminalReason.INPUT_EXCEPTION
    assert result.input_state is CameraBridgeCaptureInputState.NONE
    assert result.input_attempted is False
    assert result.input_start_clock_s is None
    assert result.input_receipt_clock_s is None
    assert result.receipt is None and result.post is None
    assert source.captures == [1, 2, 3]
    assert control.calls == ["preflight", ("key_down", "right")]


def test_ambiguous_key_down_exception_remains_partial_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    source = SequenceSource(_happy_frames())

    class AmbiguousControl(CompleteControl):
        def key_down(self, key: str) -> CameraInputReceipt:
            self.calls.append(("key_down", key))
            raise RuntimeError("send may have raised after insertion")

    control = AmbiguousControl()
    result = _run(source, control)

    assert result.terminal_reason is CameraBridgeCaptureTerminalReason.INPUT_EXCEPTION
    assert result.input_state is CameraBridgeCaptureInputState.PARTIAL_OR_UNKNOWN
    assert result.input_attempted is True
    assert result.input_start_clock_s is not None
    assert result.receipt is None and result.post is None


def test_hold_exception_attempts_key_cleanup_and_never_captures_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    source = SequenceSource(_happy_frames())
    control = CompleteControl()

    def fail_hold(duration: float) -> None:
        assert duration == CAMERA_BRIDGE_CAPTURE_HOLD_SECONDS
        raise RuntimeError("hold failed")

    result = _run(source, control, sleeper=fail_hold)

    assert result.input_state is CameraBridgeCaptureInputState.PARTIAL_OR_UNKNOWN
    assert control.calls == [
        "preflight",
        ("key_down", "right"),
        ("key_up", "right"),
    ]
    assert source.captures == [1, 2, 3]


def test_settle_exception_retains_complete_input_and_exact_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    source = SequenceSource(_happy_frames())
    control = CompleteControl()

    def sleeper(duration: float) -> None:
        if duration == CAMERA_BRIDGE_CAPTURE_SETTLE_SECONDS:
            raise RuntimeError("settle failed")

    result = _run(source, control, sleeper=sleeper)

    assert result.terminal_reason is CameraBridgeCaptureTerminalReason.SETTLE_EXCEPTION
    assert result.input_state is CameraBridgeCaptureInputState.COMPLETE
    assert result.receipt is not None and result.post is None
    assert source.captures == [1, 2, 3]
    assert control.calls.count(("key_down", "right")) == 1
    assert control.calls.count(("key_up", "right")) == 1


@pytest.mark.parametrize("post_mode", ["stale", "readiness", "fail_open"])
def test_post_failure_is_honest_complete_input_and_never_retries(
    monkeypatch: pytest.MonkeyPatch,
    post_mode: str,
) -> None:
    readiness = {4: _not_ready()} if post_mode == "readiness" else {}
    production = (
        {4: _production(fail_closed=False)} if post_mode == "fail_open" else {}
    )
    _patch_pipeline(monkeypatch, readiness=readiness, production=production)
    frames = _happy_frames()
    if post_mode == "stale":
        frames[-1] = _frame(3, _post_payload())
    control = CompleteControl()

    result = _run(SequenceSource(frames), control)

    assert result.input_state is CameraBridgeCaptureInputState.COMPLETE
    assert not result.protocol_completed
    assert control.calls.count(("key_down", "right")) == 1
    assert control.calls.count(("key_up", "right")) == 1
    assert camera_bridge_action_transition(
        result,
        evidence_report_sha256="e" * 64,
    ) is None


def test_recorder_mismatch_and_preflight_exception_send_zero_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)

    def mismatched(label: str, frame: Frame) -> CameraFrameArtifact:
        artifact = camera_bridge_capture.record_frame_digest(label, frame)
        return replace(artifact, raw_sha256="0" * 64)

    recorder_control = CompleteControl()
    recorder_result = _run(
        SequenceSource(_happy_frames()),
        recorder_control,
        recorder=mismatched,
    )
    preflight_control = CompleteControl(preflight_error=RuntimeError("focus failed"))
    preflight_result = _run(SequenceSource(_happy_frames()), preflight_control)

    assert recorder_result.input_state is CameraBridgeCaptureInputState.NONE
    assert recorder_control.calls == []
    assert preflight_result.input_state is CameraBridgeCaptureInputState.NONE
    assert preflight_control.calls == ["preflight"]


def test_result_cannot_be_forged_into_authority_or_strip_complete_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    result = _run(SequenceSource(_happy_frames()), CompleteControl())
    assert result.receipt is not None

    with pytest.raises((TypeError, ValueError)):
        replace(result, can_validate_scene=True)
    with pytest.raises(ValueError, match="complete bridge input"):
        replace(result, receipt=None)
    with pytest.raises(ValueError, match="zero-input evidence"):
        replace(
            result,
            input_state=CameraBridgeCaptureInputState.NONE,
            receipt=None,
        )
    with pytest.raises(ValueError, match="exact frozen plan object"):
        replace(
            result,
            plan=type(result.plan)(result.plan.name, result.plan.actions),
        )


def test_identical_commit_and_post_pixels_never_form_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    result = _run(
        SequenceSource([_frame(1), _frame(2), _frame(3), _frame(4)]),
        CompleteControl(),
    )

    assert result.protocol_completed
    assert camera_bridge_action_transition(
        result,
        evidence_report_sha256="d" * 64,
    ) is None


def test_result_retains_exact_stage_labels_and_honest_freshness_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    result = _run(SequenceSource(_happy_frames()), CompleteControl())
    assert result.decision is not None
    relabeled_artifact = replace(result.decision.artifact, label="r2-post")
    relabeled_decision = replace(result.decision, artifact=relabeled_artifact)

    with pytest.raises(ValueError, match="unexpected stage label"):
        replace(result, decision=relabeled_decision)
    with pytest.raises(ValueError, match="requires a stale final stage"):
        replace(
            result,
            terminal_reason=(
                CameraBridgeCaptureTerminalReason.NON_FRESH_OBSERVATION
            ),
        )
