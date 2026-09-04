from __future__ import annotations

import pytest

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

    def left_button_is_down(self) -> bool:
        return False

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
