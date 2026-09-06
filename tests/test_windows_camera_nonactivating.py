from __future__ import annotations

import pytest

from mining_automation.validation.camera_plan import CameraInputOperation
from mining_automation.validation.session_recovery import PLAY_NOW_CLIENT_POINT
from mining_automation.validation.windows_camera import (
    WindowsCameraControl,
    WindowsCameraPreInputError,
    WindowsCameraTargetIdentity,
)


class _NoActivationCameraApi:
    def __init__(self, *, foreground: int | None = 42) -> None:
        self.foreground = foreground
        self.focus_calls = 0
        self.send_key_calls = 0
        self.cursor = (0, 0)
        self.mouse_events: list[bool] = []
        self.left_down = False

    def declare_dpi_awareness(self) -> None:
        pass

    def is_window(self, hwnd: int) -> bool:
        return hwnd == 42

    def window_identity(self, hwnd: int) -> WindowsCameraTargetIdentity:
        assert hwnd == 42
        return WindowsCameraTargetIdentity(100, 200, "SunAwtFrame", "RuneLite - Chief Luma")

    def focus_window(self, hwnd: int) -> bool:
        self.focus_calls += 1
        raise AssertionError("camera harness must not activate RuneLite")

    def foreground_window(self) -> int | None:
        return self.foreground

    def client_size(self, hwnd: int) -> tuple[int, int]:
        assert hwnd == 42
        return (1005, 1078)


    def client_to_screen(self, hwnd: int, x: int, y: int) -> tuple[int, int]:
        assert hwnd == 42
        return (x + 10, y + 20)

    def move_cursor(self, x: int, y: int) -> bool:
        self.cursor = (x, y)
        return True

    def cursor_position(self) -> tuple[int, int]:
        return self.cursor

    def root_window_at_point(self, x: int, y: int) -> int | None:
        return 42

    def send_mouse_button(self, *, button_up: bool) -> int:
        self.mouse_events.append(button_up)
        self.left_down = not button_up
        return 1

    def left_button_is_down(self) -> bool:
        return self.left_down

    def middle_button_is_down(self) -> bool:
        return False

    def key_is_down(self, virtual_key: int) -> bool:
        return False

    def send_key(self, virtual_key: int, *, key_up: bool, extended: bool) -> int:
        self.send_key_calls += 1
        return 1


def test_preflight_never_activates_target_window() -> None:
    api = _NoActivationCameraApi()
    control = WindowsCameraControl(42, api=api)

    receipt = control.preflight()

    assert receipt.focused is True
    assert (receipt.client_width, receipt.client_height) == (1005, 1078)
    assert api.focus_calls == 0


def test_key_input_fails_closed_without_foreground_and_never_activates() -> None:
    api = _NoActivationCameraApi(foreground=99)
    control = WindowsCameraControl(42, api=api)

    with pytest.raises(WindowsCameraPreInputError, match="lost foreground focus"):
        control.key_down("down")

    assert api.focus_calls == 0
    assert api.send_key_calls == 0


def test_play_now_click_uses_only_reviewed_point_and_complete_pair() -> None:
    api = _NoActivationCameraApi()
    control = WindowsCameraControl(42, api=api, click_sleeper=lambda _: None)

    receipt = control.click_play_now(*PLAY_NOW_CLIENT_POINT)

    assert receipt.operation is CameraInputOperation.PLAY_NOW_CLICK
    assert receipt.requested_events == 2
    assert receipt.completed_events == 2
    assert api.mouse_events == [False, True]
    assert api.cursor == (PLAY_NOW_CLIENT_POINT[0] + 10, PLAY_NOW_CLIENT_POINT[1] + 20)
