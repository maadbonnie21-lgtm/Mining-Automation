"""Narrow Windows camera-control adapter for development validation.

This module is intentionally separate from both the production capture seam
and perception.  It exposes only the four input operations representable by
``camera_plan`` and keeps every actual Win32 call behind an injected protocol,
so deterministic tests run on Linux without sending input.
"""

from __future__ import annotations

import sys
from typing import Any, Protocol

from .camera_plan import (
    EXPECTED_CLIENT_HEIGHT,
    EXPECTED_CLIENT_WIDTH,
    REVIEWED_CAMERA_WHEEL_POINT,
    REVIEWED_COMPASS_POINT,
    CameraInputOperation,
    CameraInputReceipt,
    CameraPreflightReceipt,
)

__all__ = [
    "RealWindowsCameraApi",
    "WindowsCameraApi",
    "WindowsCameraControl",
    "WindowsCameraError",
]

_ARROW_KEYS: dict[str, int] = {
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
}
_RESET_KEYS: dict[str, int] = {
    "control": 0x11,
    "ctrl": 0x11,
}
_KEY_RELEASE_ATTEMPTS = 3
_MOUSE_RELEASE_ATTEMPTS = 3


class WindowsCameraError(RuntimeError):
    """The target window or Win32 input boundary is not safe to use."""


class WindowsCameraApi(Protocol):
    """OS seam used only by :class:`WindowsCameraControl`."""

    def declare_dpi_awareness(self) -> None:
        """Request physical-pixel client geometry, best effort."""

    def is_window(self, hwnd: int) -> bool:
        """Return whether ``hwnd`` still identifies a window."""

    def focus_window(self, hwnd: int) -> bool:
        """Try to make ``hwnd`` the foreground window."""

    def foreground_window(self) -> int | None:
        """Return the current foreground HWND, if any."""

    def client_size(self, hwnd: int) -> tuple[int, int]:
        """Return current physical client width and height."""

    def client_to_screen(self, hwnd: int, x: int, y: int) -> tuple[int, int]:
        """Convert a client-local point to physical screen coordinates."""

    def move_cursor(self, x: int, y: int) -> bool:
        """Move the system cursor to a physical screen point."""

    def root_window_at_point(self, x: int, y: int) -> int | None:
        """Return the root window that would receive pointer input there."""

    def send_mouse_button(self, *, button_up: bool) -> int:
        """Send one left-button phase, returning its accepted event count."""

    def left_button_is_down(self) -> bool:
        """Return whether the global left button is already held."""

    def send_key(self, virtual_key: int, *, key_up: bool, extended: bool) -> int:
        """Send one keyboard event, returning the accepted event count."""

    def send_wheel(self, detents: int) -> int:
        """Send one event per wheel detent and return the accepted count."""

    def key_is_down(self, virtual_key: int) -> bool:
        """Return whether a key is already held before a new key-down."""


class RealWindowsCameraApi:
    """Real Win32 implementation, loaded lazily and only on Windows."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError(
                "RealWindowsCameraApi requires Windows (sys.platform == 'win32'); "
                f"got {sys.platform!r}. Inject a WindowsCameraApi fake in tests."
            )
        from . import _camera_win32_calls

        self._calls: Any = _camera_win32_calls

    def declare_dpi_awareness(self) -> None:
        self._calls.declare_dpi_awareness()

    def is_window(self, hwnd: int) -> bool:
        result: bool = self._calls.is_window(hwnd)
        return result

    def focus_window(self, hwnd: int) -> bool:
        result: bool = self._calls.focus_window(hwnd)
        return result

    def foreground_window(self) -> int | None:
        result: int | None = self._calls.foreground_window()
        return result

    def client_size(self, hwnd: int) -> tuple[int, int]:
        result: tuple[int, int] = self._calls.client_size(hwnd)
        return result

    def client_to_screen(self, hwnd: int, x: int, y: int) -> tuple[int, int]:
        result: tuple[int, int] = self._calls.client_to_screen(hwnd, x, y)
        return result

    def move_cursor(self, x: int, y: int) -> bool:
        result: bool = self._calls.move_cursor(x, y)
        return result

    def root_window_at_point(self, x: int, y: int) -> int | None:
        result: int | None = self._calls.root_window_at_point(x, y)
        return result

    def send_mouse_button(self, *, button_up: bool) -> int:
        result: int = self._calls.send_mouse_button(button_up=button_up)
        return result

    def left_button_is_down(self) -> bool:
        result: bool = self._calls.left_button_is_down()
        return result

    def send_key(self, virtual_key: int, *, key_up: bool, extended: bool) -> int:
        result: int = self._calls.send_key(
            virtual_key,
            key_up=key_up,
            extended=extended,
        )
        return result

    def send_wheel(self, detents: int) -> int:
        result: int = self._calls.send_wheel(detents)
        return result

    def key_is_down(self, virtual_key: int) -> bool:
        result: bool = self._calls.key_is_down(virtual_key)
        return result


class WindowsCameraControl:
    """Fail-closed camera input for one already-discovered RuneLite HWND."""

    def __init__(self, hwnd: int, api: WindowsCameraApi | None = None) -> None:
        if isinstance(hwnd, bool) or not isinstance(hwnd, int) or hwnd <= 0:
            raise ValueError("hwnd must be a positive integer")
        self._hwnd = hwnd
        self._api = api if api is not None else RealWindowsCameraApi()
        self._held_keys: dict[int, bool] = {}
        self._left_button_owned = False
        self._api.declare_dpi_awareness()

    @property
    def hwnd(self) -> int:
        return self._hwnd

    def preflight(self) -> CameraPreflightReceipt:
        """Focus the exact window and report fresh physical client geometry."""

        if not self._api.is_window(self._hwnd):
            raise WindowsCameraError("target RuneLite window no longer exists")
        self._api.focus_window(self._hwnd)
        focused = self._api.foreground_window() == self._hwnd
        width, height = self._api.client_size(self._hwnd)
        return CameraPreflightReceipt(focused, width, height)

    def click_compass(self, x: int, y: int) -> CameraInputReceipt:
        self._require_reviewed_point("compass click", x, y, REVIEWED_COMPASS_POINT)
        self._require_ready()
        self._require_left_button_released("compass click")
        screen_point = self._move_to_client_point(x, y)
        self._require_ready()
        self._require_left_button_released("compass click")
        self._require_target_at_screen_point("compass click", screen_point)
        completed_down = 0
        # The pre-checks proved the button was not externally held. Mark
        # provisional ownership before the OS call so an exception raised
        # after insertion still leaves a lifecycle-cleanup obligation.
        self._left_button_owned = True
        try:
            completed_down = self._api.send_mouse_button(button_up=False)
            if completed_down not in (0, 1):
                raise WindowsCameraError(
                    "Windows returned an invalid left-button-down event count"
                )
        finally:
            completed_up = self._release_mouse_button()
        return CameraInputReceipt(
            CameraInputOperation.COMPASS_CLICK,
            requested_events=2,
            completed_events=completed_down + completed_up,
        )

    def key_down(self, key: str) -> CameraInputReceipt:
        self._require_ready()
        virtual_key, extended = _key_code(key)
        if virtual_key in self._held_keys or self._api.key_is_down(virtual_key):
            raise WindowsCameraError(f"refusing key-down because {key!r} is already held")
        # GetAsyncKeyState and application callbacks can yield long enough for
        # focus or geometry to change. Recheck at the last seam before global
        # keyboard injection, just as pointer input rechecks after cursor move.
        self._require_ready()
        try:
            completed = self._api.send_key(
                virtual_key,
                key_up=False,
                extended=extended,
            )
        except BaseException:
            # The pre-check proved this key was not held before our call.  If
            # the platform raised after insertion, a bounded compensating up
            # is safe; unlike the plan runner, the adapter owns this ambiguity.
            self._best_effort_key_release(virtual_key, extended=extended)
            raise
        if completed not in (0, 1):
            self._best_effort_key_release(virtual_key, extended=extended)
            raise WindowsCameraError("Windows returned an invalid key-down event count")
        if completed == 1:
            self._held_keys[virtual_key] = extended
        return CameraInputReceipt(
            CameraInputOperation.KEY_DOWN,
            requested_events=1,
            completed_events=completed,
        )

    def key_up(self, key: str) -> CameraInputReceipt:
        # A release is deliberately attempted even after focus/geometry loss.
        # The plan runner calls this in ``finally``; blocking it on a second
        # preflight could leave a globally held arrow key behind.
        virtual_key, extended = _key_code(key)
        if virtual_key not in self._held_keys:
            raise WindowsCameraError(
                f"refusing key-up because this control does not own {key!r}"
            )
        completed = self._release_owned_key(virtual_key, extended=extended)
        if completed == 1:
            self._held_keys.pop(virtual_key, None)
        return CameraInputReceipt(
            CameraInputOperation.KEY_UP,
            requested_events=1,
            completed_events=completed,
        )

    def scroll_camera(self, x: int, y: int, detents: int) -> CameraInputReceipt:
        self._require_reviewed_point(
            "camera wheel", x, y, REVIEWED_CAMERA_WHEEL_POINT
        )
        self._require_ready()
        self._require_left_button_released("camera wheel")
        screen_point = self._move_to_client_point(x, y)
        self._require_ready()
        self._require_left_button_released("camera wheel")
        self._require_target_at_screen_point("camera wheel", screen_point)
        return CameraInputReceipt(
            CameraInputOperation.CAMERA_WHEEL,
            requested_events=abs(detents),
            completed_events=self._api.send_wheel(detents),
        )

    def release_all_held_keys(self) -> tuple[CameraInputReceipt, ...]:
        """Best-effort lifecycle cleanup for every input owned by this adapter.

        The historical method name is retained for the tool API, but cleanup
        includes a provisionally or definitively owned left mouse button as
        well as every key. Every input is attempted even if another release
        fails. Remaining owned inputs cause a fail-closed error so a CLI cannot
        report successful cleanup while Windows may still consider an injected
        input held. Returned receipts describe key releases; mouse-up is a
        compensating lifecycle action rather than a complete click receipt.
        """

        receipts: list[CameraInputReceipt] = []
        failures: list[str] = []
        if self._left_button_owned:
            try:
                completed_mouse_up = self._release_mouse_button()
            except BaseException as exc:
                failures.append(f"left mouse button: {exc}")
            else:
                if completed_mouse_up != 1:
                    failures.append("left mouse button: short button-up receipt")
        for virtual_key, extended in sorted(tuple(self._held_keys.items())):
            try:
                completed = self._release_owned_key(
                    virtual_key,
                    extended=extended,
                )
            except BaseException as exc:
                failures.append(f"0x{virtual_key:02x}: {exc}")
                continue
            receipt = CameraInputReceipt(
                CameraInputOperation.KEY_UP,
                requested_events=1,
                completed_events=completed,
            )
            receipts.append(receipt)
            if completed == 1:
                self._held_keys.pop(virtual_key, None)
            else:
                failures.append(f"0x{virtual_key:02x}: short key-up receipt")
        if failures:
            raise WindowsCameraError(
                "could not release every camera input owned by this control: "
                + "; ".join(failures)
            )
        return tuple(receipts)

    def _require_ready(self) -> None:
        if not self._api.is_window(self._hwnd):
            raise WindowsCameraError("target RuneLite window no longer exists")
        if self._api.foreground_window() != self._hwnd:
            raise WindowsCameraError("target RuneLite window lost foreground focus")
        width, height = self._api.client_size(self._hwnd)
        if (width, height) != (EXPECTED_CLIENT_WIDTH, EXPECTED_CLIENT_HEIGHT):
            raise WindowsCameraError(
                "target RuneLite client geometry changed during the camera plan: "
                f"{width}x{height}"
            )

    def _move_to_client_point(self, x: int, y: int) -> tuple[int, int]:
        screen_x, screen_y = self._api.client_to_screen(self._hwnd, x, y)
        if not self._api.move_cursor(screen_x, screen_y):
            raise WindowsCameraError("Windows refused to move the cursor to the target")
        return screen_x, screen_y

    def _release_mouse_button(self) -> int:
        last_error: BaseException | None = None
        for _attempt in range(_MOUSE_RELEASE_ATTEMPTS):
            try:
                completed = self._api.send_mouse_button(button_up=True)
            except BaseException as exc:
                last_error = exc
                continue
            if completed == 1:
                self._left_button_owned = False
                return 1
            if completed != 0:
                last_error = WindowsCameraError(
                    "Windows returned an invalid left-button-up event count"
                )
        if last_error is not None:
            raise WindowsCameraError(
                f"left-button release failed after {_MOUSE_RELEASE_ATTEMPTS} attempts"
            ) from last_error
        return 0

    def _require_left_button_released(self, operation: str) -> None:
        if self._api.left_button_is_down():
            raise WindowsCameraError(
                f"refusing {operation} because the left button is already held"
            )

    def _require_target_at_screen_point(
        self,
        operation: str,
        screen_point: tuple[int, int],
    ) -> None:
        root_window = self._api.root_window_at_point(*screen_point)
        if root_window != self._hwnd:
            raise WindowsCameraError(
                f"refusing {operation} because the reviewed point is covered "
                "by another top-level window"
            )

    def _release_owned_key(self, virtual_key: int, *, extended: bool) -> int:
        last_error: BaseException | None = None
        for _attempt in range(_KEY_RELEASE_ATTEMPTS):
            try:
                completed = self._api.send_key(
                    virtual_key,
                    key_up=True,
                    extended=extended,
                )
            except BaseException as exc:
                last_error = exc
                continue
            if completed == 1:
                return 1
            if completed != 0:
                last_error = WindowsCameraError(
                    "Windows returned an invalid key-up event count"
                )
        if last_error is not None:
            raise WindowsCameraError(
                f"key release failed after {_KEY_RELEASE_ATTEMPTS} attempts"
            ) from last_error
        return 0

    def _best_effort_key_release(self, virtual_key: int, *, extended: bool) -> None:
        # A raised key-down call is ambiguous: Windows may have inserted the
        # event before the adapter observed the exception.  The pre-check
        # established that this was not a user-held key, so retain provisional
        # ownership until a compensating up is acknowledged.
        self._held_keys[virtual_key] = extended
        try:
            completed = self._release_owned_key(virtual_key, extended=extended)
        except BaseException:
            # Preserve the primary key-down failure.  The lifecycle cleanup
            # can retry because provisional ownership remains recorded.
            pass
        else:
            if completed == 1:
                self._held_keys.pop(virtual_key, None)

    @staticmethod
    def _require_reviewed_point(
        operation: str,
        x: int,
        y: int,
        reviewed_point: tuple[int, int],
    ) -> None:
        if (x, y) != reviewed_point:
            raise WindowsCameraError(
                f"{operation} must use exact reviewed client point {reviewed_point}"
            )


def _key_code(key: str) -> tuple[int, bool]:
    normalized = key.strip().lower() if isinstance(key, str) else ""
    if normalized in _ARROW_KEYS:
        return _ARROW_KEYS[normalized], True
    if normalized in _RESET_KEYS:
        return _RESET_KEYS[normalized], False
    raise WindowsCameraError(
        "camera validation permits only arrow keys and the Control reset-zoom key"
    )
