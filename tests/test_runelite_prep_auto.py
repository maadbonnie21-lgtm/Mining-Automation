from __future__ import annotations

import sys
from pathlib import Path

from mining_automation.validation.camera_plan import (
    REVIEWED_CAMERA_WHEEL_POINT,
    REVIEWED_COMPASS_POINT,
    CameraHoldKey,
    CameraKeyHold,
    CameraPause,
    CameraWheel,
    CompassClick,
)
from mining_automation.validation.runelite_prep import (
    PREP_CONFIRMATION,
    PrepActionReceipt,
    PrepMode,
    PrepPoseReferenceReceipt,
    PrepSceneObservation,
    PrepStopReason,
    PrepWindowIdentity,
    PrepWindowSnapshot,
    run_runelite_prep,
)

TOOLS_ROOT = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_ROOT))

from runelite_prep_auto import (  # noqa: E402
    AUTO_CAMERA_SEARCH_STEPS,
    CODEX_CAMERA_CANDIDATE_OFFSETS,
    CODEX_CAMERA_PLANS,
    CODEX_PITCH_ENDPOINT_HOLD_S,
    CODEX_ZOOM_OFFSET_DETENTS,
    CODEX_ZOOM_SATURATION_DETENTS,
    AutoCameraStep,
)

GIT_SHA = "a" * 40
FRAME_SHA = "b" * 64
REF_SHA = "c" * 64


def _window() -> PrepWindowSnapshot:
    return PrepWindowSnapshot(
        hwnd=42,
        identity=PrepWindowIdentity(100, 200, "SunAwtFrame", "RuneLite - Test"),
        visible=True,
        minimized=False,
        foreground=True,
        client_width=1005,
        client_height=1078,
        dpi=96,
    )


def _refs() -> tuple[PrepPoseReferenceReceipt, ...]:
    return tuple(
        PrepPoseReferenceReceipt(
            pose_id=f"pose-{index}",
            relative_path=f"diagnostics/pose-{index}.bgra",
            sha256=REF_SHA,
            byte_count=1005 * 1078 * 4,
            width=1005,
            height=1078,
        )
        for index in range(3)
    )


def _observation(*, ready: bool, frame_id: int) -> PrepSceneObservation:
    matched = 6 if ready else 0
    zones = ("north_west", "north_east", "south_west") if ready else ()
    return PrepSceneObservation(
        frame_id=frame_id,
        frame_sha256=FRAME_SHA,
        gameplay_ready=True,
        gameplay_reason="ready",
        inventory_occupied=0,
        inventory_confidence=1.0,
        inventory_unknown_reason=None,
        resource_supported=ready,
        resource_view="supported" if ready else "unsupported",
        accepted_pose_id="retained-pose" if ready else None,
        software_registration_identity=None,
        matched_landmarks=matched,
        matched_zones=zones,
        landmark_distances=tuple(
            (f"landmark-{index}", 0.01 if ready else 0.5) for index in range(6)
        ),
    )


class _FakeBackend:
    def __init__(self, observations: list[PrepSceneObservation]) -> None:
        self.observations = observations
        self.observe_index = 0
        self.camera_calls: list[object] = []
        self.cleanup_calls = 0
        self.short_camera_receipt = False

    def snapshot(self) -> PrepWindowSnapshot:
        return _window()

    def verify_pose_references(self) -> tuple[PrepPoseReferenceReceipt, ...]:
        return _refs()

    def restore_window(self) -> PrepActionReceipt:
        return PrepActionReceipt("restore_window", 0, 0)

    def resize_client(self, width: int, height: int) -> PrepActionReceipt:
        assert (width, height) == (1005, 1078)
        return PrepActionReceipt("resize_client_area", 0, 0)

    def focus_window(self) -> PrepActionReceipt:
        return PrepActionReceipt("foreground_window", 0, 0)

    def neutralize_cursor(self) -> PrepActionReceipt:
        return PrepActionReceipt("neutral_cursor", 0, 0)

    def observe(self) -> PrepSceneObservation:
        index = min(self.observe_index, len(self.observations) - 1)
        self.observe_index += 1
        return self.observations[index]

    def camera_action(self, step: object) -> tuple[PrepActionReceipt, ...]:
        self.camera_calls.append(step)
        completed = 0 if self.short_camera_receipt else 1
        value = getattr(step, "value", str(step))
        return (PrepActionReceipt(str(value), 1, completed),)

    def cleanup(self) -> tuple[PrepActionReceipt, ...]:
        self.cleanup_calls += 1
        return ()


def _run(backend: _FakeBackend):
    return run_runelite_prep(
        backend,  # type: ignore[arg-type]
        mode=PrepMode.APPLY,
        git_sha=GIT_SHA,
        prep_session_id="prep-auto-test",
        confirm=PREP_CONFIRMATION,
        camera_steps=AUTO_CAMERA_SEARCH_STEPS,  # type: ignore[arg-type]
    )


def test_auto_sequence_is_exact_preserved_codex_candidate_ladder() -> None:
    assert CODEX_CAMERA_CANDIDATE_OFFSETS == (
        (0.60, 0.05),
        (0.58, 0.05),
        (0.62, 0.05),
        (0.56, 0.05),
        (0.64, 0.05),
        (0.60, 0.04),
        (0.58, 0.04),
        (0.62, 0.04),
        (0.60, 0.06),
        (0.58, 0.06),
        (0.62, 0.06),
    )
    assert len(CODEX_CAMERA_PLANS) == 11
    assert AUTO_CAMERA_SEARCH_STEPS == (
        AutoCameraStep.CODEX_CANDIDATE,
    ) * 11

    first = CODEX_CAMERA_PLANS[0]
    assert len(first.actions) == 7
    assert isinstance(first.actions[0], CompassClick)
    assert (first.actions[0].x, first.actions[0].y) == REVIEWED_COMPASS_POINT
    assert isinstance(first.actions[1], CameraPause)
    assert first.actions[1].duration_s == 0.5
    assert isinstance(first.actions[2], CameraKeyHold)
    assert first.actions[2].key is CameraHoldKey.RIGHT
    assert first.actions[2].duration_s == 0.05
    assert isinstance(first.actions[3], CameraKeyHold)
    assert first.actions[3].key is CameraHoldKey.UP
    assert first.actions[3].duration_s == CODEX_PITCH_ENDPOINT_HOLD_S
    assert isinstance(first.actions[4], CameraKeyHold)
    assert first.actions[4].key is CameraHoldKey.DOWN
    assert first.actions[4].duration_s == 0.60
    assert isinstance(first.actions[5], CameraWheel)
    assert (first.actions[5].x, first.actions[5].y) == REVIEWED_CAMERA_WHEEL_POINT
    assert first.actions[5].detents == CODEX_ZOOM_SATURATION_DETENTS
    assert isinstance(first.actions[6], CameraWheel)
    assert first.actions[6].detents == CODEX_ZOOM_OFFSET_DETENTS


def test_already_ready_sends_zero_camera_input() -> None:
    backend = _FakeBackend([_observation(ready=True, frame_id=1)])
    result = _run(backend)
    assert result.ready_for_mining is True
    assert backend.camera_calls == []
    assert backend.cleanup_calls == 1


def test_closed_loop_stops_on_first_candidate_that_passes_frozen_gate() -> None:
    backend = _FakeBackend(
        [
            _observation(ready=False, frame_id=1),
            _observation(ready=False, frame_id=2),
            _observation(ready=True, frame_id=3),
        ]
    )
    result = _run(backend)
    assert result.ready_for_mining is True
    assert backend.camera_calls == list(AUTO_CAMERA_SEARCH_STEPS[:2])
    assert backend.cleanup_calls == 1


def test_bounded_exhaustion_stops_without_extra_candidate() -> None:
    backend = _FakeBackend([_observation(ready=False, frame_id=1)])
    result = _run(backend)
    assert result.ready_for_mining is False
    assert result.stop_reason is PrepStopReason.CAMERA_SEARCH_EXHAUSTED
    assert backend.camera_calls == list(AUTO_CAMERA_SEARCH_STEPS)
    assert backend.cleanup_calls == 1


def test_short_camera_receipt_stops_after_first_candidate() -> None:
    backend = _FakeBackend([_observation(ready=False, frame_id=1)])
    backend.short_camera_receipt = True
    result = _run(backend)
    assert result.ready_for_mining is False
    assert result.stop_reason is PrepStopReason.CAMERA_RECEIPT_INCOMPLETE
    assert backend.camera_calls == [AUTO_CAMERA_SEARCH_STEPS[0]]
    assert backend.cleanup_calls == 1
