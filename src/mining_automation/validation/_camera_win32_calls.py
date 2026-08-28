"""Raw Win32 input calls for development-only camera validation.

Imported lazily by :class:`RealWindowsCameraApi` after a platform check.  The
production capture Win32 seam remains capture-only.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Any, Final


def _load_dll(name: str) -> Any:  # noqa: ANN401 - ctypes DLL handles are untyped
    return ctypes.WinDLL(name)  # type: ignore[attr-defined]


_user32 = _load_dll("user32")

_INPUT_MOUSE: Final[int] = 0
_INPUT_KEYBOARD: Final[int] = 1
_MOUSEEVENTF_LEFTDOWN: Final[int] = 0x0002
_MOUSEEVENTF_LEFTUP: Final[int] = 0x0004
_MOUSEEVENTF_WHEEL: Final[int] = 0x0800
_KEYEVENTF_EXTENDEDKEY: Final[int] = 0x0001
_KEYEVENTF_KEYUP: Final[int] = 0x0002
_WHEEL_DELTA: Final[int] = 120
_VK_LBUTTON: Final[int] = 0x01
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2: Final[int] = -4
_GA_ROOT: Final[int] = 2


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    )


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    )


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = (
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    )


class _INPUTUNION(ctypes.Union):
    _fields_ = (
        ("mi", _MOUSEINPUT),
        ("ki", _KEYBDINPUT),
        ("hi", _HARDWAREINPUT),
    )


class _INPUT(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = (("type", wintypes.DWORD), ("data", _INPUTUNION))


_user32.IsWindow.restype = wintypes.BOOL
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.SetForegroundWindow.restype = wintypes.BOOL
_user32.SetForegroundWindow.argtypes = [wintypes.HWND]
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetForegroundWindow.argtypes = []
_user32.GetClientRect.restype = wintypes.BOOL
_user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.ClientToScreen.restype = wintypes.BOOL
_user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
_user32.LogicalToPhysicalPointForPerMonitorDPI.restype = wintypes.BOOL
_user32.LogicalToPhysicalPointForPerMonitorDPI.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(wintypes.POINT),
]
_user32.SetCursorPos.restype = wintypes.BOOL
_user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
_user32.WindowFromPoint.restype = wintypes.HWND
_user32.WindowFromPoint.argtypes = [wintypes.POINT]
_user32.GetAncestor.restype = wintypes.HWND
_user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
_user32.SendInput.restype = wintypes.UINT
_user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
_user32.GetAsyncKeyState.restype = ctypes.c_short
_user32.GetAsyncKeyState.argtypes = [ctypes.c_int]


def declare_dpi_awareness() -> None:
    """Best-effort physical-pixel awareness; an existing declaration wins."""

    try:
        set_context = getattr(_user32, "SetProcessDpiAwarenessContext", None)
        if set_context is not None:
            set_context.restype = wintypes.BOOL
            set_context.argtypes = [ctypes.c_void_p]
            set_context(ctypes.c_void_p(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2))
    except Exception:  # noqa: BLE001 - explicitly best effort
        pass


def is_window(hwnd: int) -> bool:
    return bool(_user32.IsWindow(hwnd))


def focus_window(hwnd: int) -> bool:
    return bool(_user32.SetForegroundWindow(hwnd))


def foreground_window() -> int | None:
    value = _user32.GetForegroundWindow()
    return int(value) if value else None


def client_size(hwnd: int) -> tuple[int, int]:
    rectangle = wintypes.RECT()
    if not _user32.GetClientRect(hwnd, ctypes.byref(rectangle)):
        raise OSError("GetClientRect failed for the target RuneLite window")
    width = int(rectangle.right - rectangle.left)
    height = int(rectangle.bottom - rectangle.top)
    if width <= 0 or height <= 0:
        raise OSError("target RuneLite window has an empty client area")
    return width, height


def client_to_screen(hwnd: int, x: int, y: int) -> tuple[int, int]:
    """Map RuneLite's logical capture point to a physical screen point.

    RuneLite is DPI-unaware on the reviewed client, while this input process
    is per-monitor aware. The production capture/profile coordinates therefore
    remain in RuneLite's logical client space even when Windows presents a
    scaled physical window. The per-monitor conversion must happen before
    ``ClientToScreen`` or pointer input lands left/up of the reviewed target.
    """

    point = wintypes.POINT(x, y)
    if not _user32.LogicalToPhysicalPointForPerMonitorDPI(
        hwnd,
        ctypes.byref(point),
    ):
        raise OSError(
            "LogicalToPhysicalPointForPerMonitorDPI failed for the target "
            "RuneLite window"
        )
    if not _user32.ClientToScreen(hwnd, ctypes.byref(point)):
        raise OSError("ClientToScreen failed for the target RuneLite window")
    return int(point.x), int(point.y)


def move_cursor(x: int, y: int) -> bool:
    return bool(_user32.SetCursorPos(x, y))


def root_window_at_point(x: int, y: int) -> int | None:
    """Return the top-level window that would receive pointer input there."""

    window = _user32.WindowFromPoint(wintypes.POINT(x, y))
    if not window:
        return None
    root = _user32.GetAncestor(window, _GA_ROOT)
    return int(root) if root else None


def send_mouse_button(*, button_up: bool) -> int:
    """Send exactly one left-button phase and return its accepted count.

    Down and up are deliberately separate calls.  The adapter can therefore
    guarantee a compensating up attempt if the down succeeds but a later call
    is short or raises; batching both phases would make a one-of-two SendInput
    result leave the global mouse button held with no cleanup seam.
    """

    event = _mouse_input(_MOUSEEVENTF_LEFTUP if button_up else _MOUSEEVENTF_LEFTDOWN)
    return int(_user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(_INPUT)))


def left_button_is_down() -> bool:
    """Return whether the global left button was already held by the user."""

    return bool(_user32.GetAsyncKeyState(_VK_LBUTTON) & 0x8000)


def send_key(virtual_key: int, *, key_up: bool, extended: bool) -> int:
    flags = 0
    if extended:
        flags |= _KEYEVENTF_EXTENDEDKEY
    if key_up:
        flags |= _KEYEVENTF_KEYUP
    event = _INPUT(
        type=_INPUT_KEYBOARD,
        ki=_KEYBDINPUT(
            wVk=virtual_key,
            wScan=0,
            dwFlags=flags,
            time=0,
            dwExtraInfo=0,
        ),
    )
    return int(_user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(_INPUT)))


def send_wheel(detents: int) -> int:
    """Send exactly one signed wheel detent.

    RuneLite can coalesce a batch of acknowledged wheel events, so the
    validation adapter owns pacing and calls this boundary once per detent.
    """

    if detents not in (-1, 1):
        raise ValueError("send_wheel requires exactly one signed detent")
    event = _mouse_input(
        _MOUSEEVENTF_WHEEL,
        mouse_data=detents * _WHEEL_DELTA,
    )
    return int(_user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(_INPUT)))


def key_is_down(virtual_key: int) -> bool:
    return bool(_user32.GetAsyncKeyState(virtual_key) & 0x8000)


def _mouse_input(flags: int, *, mouse_data: int = 0) -> _INPUT:
    return _INPUT(
        type=_INPUT_MOUSE,
        mi=_MOUSEINPUT(
            dx=0,
            dy=0,
            mouseData=mouse_data & 0xFFFFFFFF,
            dwFlags=flags,
            time=0,
            dwExtraInfo=0,
        ),
    )
