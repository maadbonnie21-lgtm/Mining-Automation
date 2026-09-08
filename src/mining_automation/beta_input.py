"""Opt-in beta input policy. Existing standalone phase behavior is unchanged by default."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .beta_interaction import require_fresh, smooth_move

_CURRENT: InputPolicy | None = None


def current_policy() -> InputPolicy | None:
    return _CURRENT


def install_policy(policy: InputPolicy) -> None:
    global _CURRENT
    if _CURRENT is not None:
        raise RuntimeError("A beta input policy is already installed")
    _CURRENT = policy


class InputPolicy:
    def __init__(
        self,
        native: Any,
        cancel: Path,
        auth_cancel: Path,
        *,
        smooth: bool,
        authentication: bool = False,
    ) -> None:
        self.native, self.api = native, native.api
        self.initial = native.initial
        self.hwnd = native.hwnd
        self.cancel, self.auth_cancel = cancel, auth_cancel
        self.smooth, self.authentication = smooth, authentication
        self.last_pointer: tuple[int, int] | None = None

    def check(self) -> None:
        if self.cancel.exists() or self.api.key_is_down(0x1B) or self.api.key_is_down(0x78):
            # Escape or F9 emergency is propagated to the parent latch.
            self.cancel.touch()
            raise RuntimeError("emergency_stop")
        if self.authentication and self.auth_cancel.exists():
            raise RuntimeError("normal_stop_cancelled_authentication")
        if self.native.snapshot() != self.initial:
            raise RuntimeError("beta_window_identity_geometry_or_DPI_changed")
        if self.api.foreground_window() != self.hwnd:
            raise RuntimeError("beta_focus_lost; no_automatic_refocus")

    def wait(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.check()
            time.sleep(min(0.02, max(0, end - time.monotonic())))
        self.check()

    def move(self, screen: tuple[int, int], *, deadline: float | None = None) -> None:
        self.check()
        if self.api.root_window_at_point(*screen) != self.hwnd:
            raise RuntimeError("beta_target_occluded")
        if self.last_pointer is not None and self.api.cursor_position() != self.last_pointer:
            raise RuntimeError("human_cursor_displacement; input_ownership_lost")
        if self.smooth:
            smooth_move(self.api, screen, check=self.check, deadline=deadline, sleep=self.wait)
        else:
            if deadline is not None:
                require_fresh(deadline, time.monotonic())
            if (
                self.api.left_button_is_down()
                or self.api.middle_button_is_down()
                or self.api.key_is_down(0x02)
            ):
                raise RuntimeError("human_mouse_button_held")
            if not self.api.move_cursor(*screen) or self.api.cursor_position() != screen:
                raise RuntimeError("beta_cursor_delivery_failed")
        self.last_pointer = screen
        self.check()


class GuardedCameraApi:
    """Delegates coordinate conversions unchanged, but never fights for lost focus."""

    def __init__(self, policy: InputPolicy) -> None:
        self.policy = policy

    def __getattr__(self, name: str) -> Any:
        return getattr(self.policy.api, name)

    def focus_window(self, hwnd: int) -> bool:
        self.policy.check()
        return hwnd == self.policy.hwnd and self.policy.api.foreground_window() == hwnd

    def move_cursor(self, x: int, y: int) -> bool:
        self.policy.move((x, y))
        return True
