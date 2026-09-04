"""Narrow Win32 window-normalization seam used only by RuneLite PREP.

This module is intentionally not part of capture or mining input.  It exposes
only the two window mutations explicitly authorized for PREP: restore a
minimized top-level RuneLite window and resize its *client area* to the exact
reviewed geometry.  Callers must bind/revalidate HWND identity separately.
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any, Final

__all__ = [
    "ClientResizeResult",
    "restore_window",
    "resize_client_area",
]

_SW_RESTORE: Final[int] = 9
_GWL_STYLE: Final[int] = -16
_GWL_EXSTYLE: Final[int] = -20
_SWP_NOZORDER: Final[int] = 0x0004
_SWP_NOACTIVATE: Final[int] = 0x0010
_SWP_NOOWNERZORDER: Final[int] = 0x0200
_DEFAULT_SETTLE_S: Final[float] = 0.15
_MAX_CORRECTION_DELTA: Final[int] = 4096


@dataclass(frozen=True, slots=True)
class ClientResizeResult:
    success: bool
    attempts: int
    final_width: int
    final_height: int

    def __post_init__(self) -> None:
        if self.attempts < 0:
            raise ValueError("attempts must be non-negative")
        if self.final_width < 0 or self.final_height < 0:
            raise ValueError("final client dimensions must be non-negative")


def _user32() -> Any:  # noqa: ANN401 - ctypes DLL handles are untyped
    if sys.platform != "win32":
        raise RuntimeError("RuneLite PREP window mutation requires Windows")
    return ctypes.WinDLL("user32", use_last_error=True)


def _require_window(user32: Any, hwnd: int) -> None:  # noqa: ANN401
    user32.IsWindow.restype = wintypes.BOOL
    user32.IsWindow.argtypes = [wintypes.HWND]
    if not user32.IsWindow(hwnd):
        raise OSError("target RuneLite HWND no longer exists")


def _client_size(user32: Any, hwnd: int) -> tuple[int, int]:  # noqa: ANN401
    rect = wintypes.RECT()
    user32.GetClientRect.restype = wintypes.BOOL
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        raise OSError("GetClientRect failed for RuneLite PREP")
    width = int(rect.right - rect.left)
    height = int(rect.bottom - rect.top)
    if width < 0 or height < 0:
        raise OSError("RuneLite client rectangle is invalid")
    return width, height


def restore_window(hwnd: int, *, settle_s: float = _DEFAULT_SETTLE_S) -> bool:
    """Restore one exact HWND and verify it is visible and no longer iconic.

    ``ShowWindow``'s return value reports the *previous* visibility state, not
    whether the requested transition succeeded, so success is established only
    from fresh ``IsIconic``/``IsWindowVisible`` measurements after the settle.
    """

    if isinstance(hwnd, bool) or not isinstance(hwnd, int) or hwnd <= 0:
        raise ValueError("hwnd must be a positive integer")
    if settle_s < 0.0:
        raise ValueError("settle_s must be non-negative")
    user32 = _user32()
    _require_window(user32, hwnd)
    user32.ShowWindow.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.IsIconic.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.ShowWindow(hwnd, _SW_RESTORE)
    if settle_s:
        time.sleep(settle_s)
    _require_window(user32, hwnd)
    return bool(user32.IsWindowVisible(hwnd)) and not bool(user32.IsIconic(hwnd))


def _window_rect(user32: Any, hwnd: int) -> tuple[int, int, int, int]:  # noqa: ANN401
    rect = wintypes.RECT()
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise OSError("GetWindowRect failed for RuneLite PREP")
    return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)


def _get_window_long(user32: Any, hwnd: int, index: int) -> int:  # noqa: ANN401
    getter = getattr(user32, "GetWindowLongPtrW", None)
    if getter is not None:
        getter.restype = ctypes.c_ssize_t
        getter.argtypes = [wintypes.HWND, ctypes.c_int]
        value = int(getter(hwnd, index))
    else:
        getter32 = user32.GetWindowLongW
        getter32.restype = wintypes.LONG
        getter32.argtypes = [wintypes.HWND, ctypes.c_int]
        value = int(getter32(hwnd, index))
    if value == 0:
        raise OSError("GetWindowLong returned zero for RuneLite PREP")
    return value


def _outer_size_for_client(
    user32: Any,  # noqa: ANN401
    hwnd: int,
    client_width: int,
    client_height: int,
) -> tuple[int, int]:
    style = _get_window_long(user32, hwnd, _GWL_STYLE)
    ex_style = _get_window_long(user32, hwnd, _GWL_EXSTYLE)
    rect = wintypes.RECT(0, 0, client_width, client_height)

    user32.GetDpiForWindow.restype = wintypes.UINT
    user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    dpi = int(user32.GetDpiForWindow(hwnd))
    if dpi <= 0:
        raise OSError("GetDpiForWindow failed for RuneLite PREP")

    adjust_for_dpi = getattr(user32, "AdjustWindowRectExForDpi", None)
    if adjust_for_dpi is not None:
        adjust_for_dpi.restype = wintypes.BOOL
        adjust_for_dpi.argtypes = [
            ctypes.POINTER(wintypes.RECT),
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
            wintypes.UINT,
        ]
        ok = bool(
            adjust_for_dpi(
                ctypes.byref(rect),
                wintypes.DWORD(style),
                False,
                wintypes.DWORD(ex_style),
                wintypes.UINT(dpi),
            )
        )
    else:
        user32.AdjustWindowRectEx.restype = wintypes.BOOL
        user32.AdjustWindowRectEx.argtypes = [
            ctypes.POINTER(wintypes.RECT),
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        ok = bool(
            user32.AdjustWindowRectEx(
                ctypes.byref(rect),
                wintypes.DWORD(style),
                False,
                wintypes.DWORD(ex_style),
            )
        )
    if not ok:
        raise OSError("AdjustWindowRectEx failed for RuneLite PREP")
    width = int(rect.right - rect.left)
    height = int(rect.bottom - rect.top)
    if width <= 0 or height <= 0:
        raise OSError("computed RuneLite outer dimensions are invalid")
    return width, height


def _set_outer_size(
    user32: Any,  # noqa: ANN401
    hwnd: int,
    width: int,
    height: int,
) -> None:
    if width <= 0 or height <= 0:
        raise ValueError("outer window dimensions must be positive")
    left, top, _, _ = _window_rect(user32, hwnd)
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    flags = _SWP_NOZORDER | _SWP_NOACTIVATE | _SWP_NOOWNERZORDER
    if not user32.SetWindowPos(hwnd, 0, left, top, width, height, flags):
        raise OSError("SetWindowPos failed for RuneLite PREP")


def resize_client_area(
    hwnd: int,
    target_width: int,
    target_height: int,
    *,
    max_attempts: int = 4,
    settle_s: float = _DEFAULT_SETTLE_S,
) -> ClientResizeResult:
    """Boundedly resize one top-level window until its measured client is exact.

    The first attempt converts the desired client rectangle through the live
    window style/DPI.  Later attempts correct only the measured client error.
    No desktop position, z-order, activation state, or global scaling is
    intentionally changed.  Success is based solely on a fresh client-area
    measurement after each mutation.
    """

    if isinstance(hwnd, bool) or not isinstance(hwnd, int) or hwnd <= 0:
        raise ValueError("hwnd must be a positive integer")
    if target_width <= 0 or target_height <= 0:
        raise ValueError("target client dimensions must be positive")
    if max_attempts <= 0 or max_attempts > 8:
        raise ValueError("max_attempts must be in 1..8")
    if settle_s < 0.0:
        raise ValueError("settle_s must be non-negative")

    user32 = _user32()
    _require_window(user32, hwnd)
    current_width, current_height = _client_size(user32, hwnd)
    if (current_width, current_height) == (target_width, target_height):
        return ClientResizeResult(True, 0, current_width, current_height)

    outer_width, outer_height = _outer_size_for_client(
        user32,
        hwnd,
        target_width,
        target_height,
    )
    attempts = 0
    for attempt in range(max_attempts):
        attempts += 1
        _require_window(user32, hwnd)
        if attempt:
            left, top, right, bottom = _window_rect(user32, hwnd)
            live_outer_width = right - left
            live_outer_height = bottom - top
            delta_width = target_width - current_width
            delta_height = target_height - current_height
            if (
                abs(delta_width) > _MAX_CORRECTION_DELTA
                or abs(delta_height) > _MAX_CORRECTION_DELTA
            ):
                raise OSError("client resize correction exceeded bounded delta")
            outer_width = live_outer_width + delta_width
            outer_height = live_outer_height + delta_height
        _set_outer_size(user32, hwnd, outer_width, outer_height)
        if settle_s:
            time.sleep(settle_s)
        _require_window(user32, hwnd)
        current_width, current_height = _client_size(user32, hwnd)
        if (current_width, current_height) == (target_width, target_height):
            return ClientResizeResult(True, attempts, current_width, current_height)

    return ClientResizeResult(False, attempts, current_width, current_height)
