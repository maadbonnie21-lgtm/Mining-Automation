from __future__ import annotations

from dataclasses import replace

from mining_automation.validation.runelite_prep import (
    PREP_CONFIRMATION,
    PrepActionReceipt,
    PrepCameraStep,
    PrepMode,
    PrepPoseReferenceReceipt,
    PrepSceneObservation,
    PrepStopReason,
    PrepWindowIdentity,
    PrepWindowSnapshot,
    run_runelite_prep,
)
from mining_automation.validation.runelite_prep_live_boundary import (
    ExactHwndPrepBackend,
    bind_ready_receipt_to_observation_window,
)

GIT_SHA = "a" * 40
FRAME_SHA = "b" * 64
REF_SHA = "c" * 64


def _window(**changes: object) -> PrepWindowSnapshot:
    base = PrepWindowSnapshot(
        hwnd=42,
        identity=PrepWindowIdentity(100, 200, "SunAwtFrame", "RuneLite - Test"),
        visible=True,
        minimized=False,
        foreground=True,
        client_width=1005,
        client_height=1078,
        dpi=96,
    )
    return replace(base, **changes)


def _observation() -> PrepSceneObservation:
    return PrepSceneObservation(
        frame_id=10,
        frame_sha256=FRAME_SHA,
        gameplay_ready=True,
        gameplay_reason="ready",
        inventory_occupied=0,
        inventory_confidence=1.0,
        inventory_unknown_reason=None,
        resource_supported=True,
        resource_view="supported",
        accepted_pose_id="pose-test",
        software_registration_identity=None,
        matched_landmarks=6,
        matched_zones=("north_west", "north_east", "south_west"),
        landmark_distances=tuple((f"landmark-{index}", 0.01) for index in range(6)),
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


class InnerBackend:
    def __init__(self) -> None:
        self.window = _window()
        self.post_observe_window = self.window
        self.cleanup_window = self.window
        self.mutations: list[str] = []

    def snapshot(self) -> PrepWindowSnapshot:
        return self.window

    def verify_pose_references(self) -> tuple[PrepPoseReferenceReceipt, ...]:
        return _refs()

    def restore_window(self) -> PrepActionReceipt:
        self.mutations.append("restore")
        return PrepActionReceipt("restore", 1, 1)

    def resize_client(self, width: int, height: int) -> PrepActionReceipt:
        self.mutations.append("resize")
        self.window = replace(self.window, client_width=width, client_height=height)
        return PrepActionReceipt("resize", 1, 1)

    def focus_window(self) -> PrepActionReceipt:
        self.mutations.append("focus")
        self.window = replace(self.window, foreground=True)
        return PrepActionReceipt("focus", 1, 1)

    def neutralize_cursor(self) -> PrepActionReceipt:
        self.mutations.append("neutral")
        return PrepActionReceipt("neutral", 0, 0)

    def recover_session(self, stage: str) -> PrepActionReceipt:
        del stage
        return PrepActionReceipt("play_now_click", 2, 2)

    def observe(self) -> PrepSceneObservation:
        # Simulate a window change that occurs after the frame has been captured but
        # before the strict boundary performs its post-observation snapshot.
        self.window = self.post_observe_window
        return _observation()

    def camera_action(self, step: PrepCameraStep) -> tuple[PrepActionReceipt, ...]:
        self.mutations.append(f"camera:{step.value}")
        return (PrepActionReceipt(step.value, 1, 1),)

    def cleanup(self) -> tuple[PrepActionReceipt, ...]:
        self.window = self.cleanup_window
        return ()


def _run(inner: InnerBackend, *, expected_hwnd: int = 42):
    backend = ExactHwndPrepBackend(inner, expected_hwnd=expected_hwnd)
    result = run_runelite_prep(
        backend,
        mode=PrepMode.APPLY,
        git_sha=GIT_SHA,
        prep_session_id="prep-live-test",
        confirm=PREP_CONFIRMATION,
        camera_steps=(),
    )
    return bind_ready_receipt_to_observation_window(result, backend), backend


def test_wrong_authorized_hwnd_stops_before_any_mutation() -> None:
    inner = InnerBackend()
    result, _ = _run(inner, expected_hwnd=999)
    assert result.ready_for_mining is False
    assert result.stop_reason is PrepStopReason.WINDOW_IDENTITY_CHANGED
    assert inner.mutations == []


def test_ready_frame_foreground_drift_stops_even_if_cleanup_restores_perfect_window() -> None:
    inner = InnerBackend()
    inner.post_observe_window = _window(foreground=False)
    inner.cleanup_window = _window()
    result, _ = _run(inner)
    assert result.ready_for_mining is False
    assert result.stop_reason is PrepStopReason.WINDOW_NOT_FOREGROUND
    assert result.final_window == _window()


def test_ready_frame_geometry_drift_stops_even_if_cleanup_restores_perfect_window() -> None:
    inner = InnerBackend()
    inner.post_observe_window = _window(client_height=687)
    inner.cleanup_window = _window()
    result, _ = _run(inner)
    assert result.ready_for_mining is False
    assert result.stop_reason is PrepStopReason.CLIENT_GEOMETRY_MISMATCH


def test_ready_frame_dpi_drift_stops() -> None:
    inner = InnerBackend()
    inner.post_observe_window = _window(dpi=120)
    result, _ = _run(inner)
    assert result.ready_for_mining is False
    assert result.stop_reason is PrepStopReason.DPI_MISMATCH


def test_success_receipt_is_bound_to_post_observation_window() -> None:
    inner = InnerBackend()
    result, backend = _run(inner)
    assert result.ready_for_mining is True
    assert backend.last_observation_window is not None
    assert result.final_window == backend.last_observation_window
    assert result.final_window.hwnd == 42
    assert result.mining_input_authority is False
    assert result.navigation_authority is False
    assert result.banking_authority is False
