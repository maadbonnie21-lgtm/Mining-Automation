"""Raw Win32 calls.

Every actual Windows API call in this project lives in this one file. It is
imported exactly once, lazily, from
:meth:`~mining_automation.capture.windows.win32_api.RealWin32Api.__init__`,
and only after that constructor has already confirmed ``sys.platform ==
"win32"``. Nothing else in the codebase imports this module, and this module
is never exercised at runtime on the Linux CI this project builds on.

typeshed defines :data:`ctypes.WinDLL` and :func:`ctypes.WINFUNCTYPE` only
under ``sys.platform == "win32"``, so mypy running with its default (Linux)
platform cannot resolve them. Both are isolated to the two loader functions
immediately below, each carrying one explicit, justified suppression, so the
rest of this file -- and everywhere else in the project -- stays fully typed.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Any, Final

from .gdi_resources import (
    GdiBitmapSurface,
    GdiOps,
    GdiResourceError,
    read_complete_scanlines,
)
from .geometry import client_offset_within_window

__all__ = [
    "capture_client_area",
    "declare_dpi_awareness",
    "enumerate_windows",
    "get_dpi_for_window",
]


def _load_dll(name: str) -> Any:  # noqa: ANN401 - ctypes DLL handles are inherently untyped
    return ctypes.WinDLL(name)  # type: ignore[attr-defined]


def _make_enum_windows_proc(callback: Any) -> Any:  # noqa: ANN401
    proc_type = ctypes.WINFUNCTYPE(  # type: ignore[attr-defined]
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )
    return proc_type(callback)


_user32 = _load_dll("user32")
_gdi32 = _load_dll("gdi32")

# -- constants ---------------------------------------------------------------

# DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2. Pointer-sized pseudo-handle
# constant; Windows 10 version 1703 and later.
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2: Final[int] = -4
# PROCESS_PER_MONITOR_DPI_AWARE, the Windows 8.1 shcore.dll fallback value.
_PROCESS_PER_MONITOR_DPI_AWARE: Final[int] = 2
# PW_RENDERFULLCONTENT asks PrintWindow for the fullest rendering Windows can
# provide. It does not guarantee every hardware-accelerated surface is
# rasterized; RuneLite still requires real-machine validation.
_PW_RENDERFULLCONTENT: Final[int] = 0x00000002
_BI_RGB: Final[int] = 0
_DIB_RGB_COLORS: Final[int] = 0
_SRCCOPY: Final[int] = 0x00CC0020
# Windows' own default when no DPI information is available.
_DEFAULT_DPI: Final[int] = 96


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = (
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    )


class _RGBQUAD(ctypes.Structure):
    _fields_ = (
        ("rgbBlue", ctypes.c_byte),
        ("rgbGreen", ctypes.c_byte),
        ("rgbRed", ctypes.c_byte),
        ("rgbReserved", ctypes.c_byte),
    )


class _BITMAPINFO(ctypes.Structure):
    # BI_RGB at 32bpp does not use a colour table; GetDIBits still expects the
    # struct to exist, so one placeholder entry is enough.
    _fields_ = (("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", _RGBQUAD * 1))


# -- signatures ---------------------------------------------------------------
# Handle-returning functions default to a 32-bit ctypes.c_int return type when
# .restype is left unset, which truncates 64-bit handles on Windows x64. Every
# function used below has an explicit restype/argtypes for exactly that
# reason.

_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsIconic.restype = wintypes.BOOL
_user32.IsIconic.argtypes = [wintypes.HWND]
_user32.IsWindow.restype = wintypes.BOOL
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.GetWindowTextW.restype = ctypes.c_int
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetClassNameW.restype = ctypes.c_int
_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetClientRect.restype = wintypes.BOOL
_user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.ClientToScreen.restype = wintypes.BOOL
_user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
_user32.GetDC.restype = wintypes.HDC
_user32.GetDC.argtypes = [wintypes.HWND]
_user32.ReleaseDC.restype = ctypes.c_int
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32.PrintWindow.restype = wintypes.BOOL
_user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
_user32.EnumWindows.restype = wintypes.BOOL
_user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]

_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
_gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
_gdi32.SelectObject.restype = wintypes.HGDIOBJ
_gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_gdi32.DeleteDC.restype = wintypes.BOOL
_gdi32.DeleteDC.argtypes = [wintypes.HDC]
_gdi32.DeleteObject.restype = wintypes.BOOL
_gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_gdi32.BitBlt.restype = wintypes.BOOL
_gdi32.BitBlt.argtypes = [
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.DWORD,
]
_gdi32.GetDIBits.restype = ctypes.c_int
_gdi32.GetDIBits.argtypes = [
    wintypes.HDC,
    wintypes.HBITMAP,
    wintypes.UINT,
    wintypes.UINT,
    ctypes.c_void_p,
    ctypes.POINTER(_BITMAPINFO),
    wintypes.UINT,
]


def _handle_value(value: Any) -> int | None:
    """Normalize a ctypes handle return to an integer or ``None``."""
    if not value:
        return None
    return int(value)


class _CtypesGdiOps:
    """Adapter from raw ctypes calls to the tested GDI lifecycle seam."""

    def create_compatible_dc(self, reference_dc: int) -> int | None:
        return _handle_value(_gdi32.CreateCompatibleDC(reference_dc))

    def create_compatible_bitmap(
        self,
        reference_dc: int,
        width: int,
        height: int,
    ) -> int | None:
        return _handle_value(
            _gdi32.CreateCompatibleBitmap(reference_dc, width, height)
        )

    def select_object(self, dc: int, graphic_object: int) -> int | None:
        return _handle_value(_gdi32.SelectObject(dc, graphic_object))

    def delete_dc(self, dc: int) -> bool:
        return bool(_gdi32.DeleteDC(dc))

    def delete_object(self, graphic_object: int) -> bool:
        return bool(_gdi32.DeleteObject(graphic_object))


_gdi_ops: Final[GdiOps] = _CtypesGdiOps()


def _get_window_text(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(512)
    length = _user32.GetWindowTextW(hwnd, buffer, 512)
    return buffer.value if length > 0 else ""


def _get_class_name(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    length = _user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value if length > 0 else ""


def declare_dpi_awareness() -> None:
    """Request per-monitor DPI awareness. Best-effort; never raises.

    Tries the modern per-monitor-v2 API (Windows 10 1703+), then the
    Windows 8.1 shcore API, then the Vista+ legacy API, in that order,
    stopping at the first one that succeeds. A failure at any step -- commonly
    because awareness was already declared elsewhere, such as by an
    application manifest -- is not an error.
    """
    try:
        set_context = getattr(_user32, "SetProcessDpiAwarenessContext", None)
        if set_context is not None:
            set_context.restype = wintypes.BOOL
            set_context.argtypes = [ctypes.c_void_p]
            if set_context(ctypes.c_void_p(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)):
                return

        shcore = _load_dll("shcore")
        set_awareness = getattr(shcore, "SetProcessDpiAwareness", None)
        if set_awareness is not None and set_awareness(_PROCESS_PER_MONITOR_DPI_AWARE) == 0:
            return

        legacy = getattr(_user32, "SetProcessDPIAware", None)
        if legacy is not None:
            legacy()
    except Exception:  # noqa: BLE001 - declared best-effort by the Win32Api contract
        pass


def get_dpi_for_window(hwnd: int) -> int:
    """The DPI Windows currently associates with ``hwnd``, or 96 if unknown."""
    try:
        fn = getattr(_user32, "GetDpiForWindow", None)
        if fn is not None:
            fn.restype = wintypes.UINT
            fn.argtypes = [wintypes.HWND]
            dpi = int(fn(hwnd))
            if dpi > 0:
                return dpi
    except OSError:
        pass
    return _DEFAULT_DPI


def enumerate_windows() -> list[Any]:
    """Snapshot every visible top-level window.

    Returns plain ``WindowInfo``-shaped data (imported locally to avoid a
    module-level import cycle with :mod:`.win32_api`).
    """
    from .win32_api import WindowInfo

    windows: list[WindowInfo] = []

    def _callback(hwnd: int, _lparam: int) -> bool:
        if not _user32.IsWindowVisible(hwnd):
            return True
        rect = wintypes.RECT()
        if _user32.GetClientRect(hwnd, ctypes.byref(rect)):
            client_w, client_h = rect.right - rect.left, rect.bottom - rect.top
        else:
            client_w, client_h = 0, 0
        windows.append(
            WindowInfo(
                hwnd=int(hwnd),
                title=_get_window_text(hwnd),
                class_name=_get_class_name(hwnd),
                is_visible=True,
                is_minimized=bool(_user32.IsIconic(hwnd)),
                client_width=client_w,
                client_height=client_h,
            )
        )
        return True

    proc = _make_enum_windows_proc(_callback)
    _user32.EnumWindows(proc, 0)
    return windows


def capture_client_area(hwnd: int) -> Any:
    """Capture ``hwnd``'s client area as top-down BGRA pixels.

    Returns a ``CapturedPixels`` (imported locally, see :func:`enumerate_windows`).

    Raises:
        Win32WindowUnavailable: the window is gone, minimized, or has a
            zero-size window or client rect, or a GDI step failed in a way
            consistent with the window having disappeared mid-capture.
    """
    from .win32_api import CapturedPixels, Win32WindowUnavailable

    if not _user32.IsWindow(hwnd):
        raise Win32WindowUnavailable("window no longer exists")
    if _user32.IsIconic(hwnd):
        raise Win32WindowUnavailable("window is minimized")

    window_rect = wintypes.RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(window_rect)):
        raise Win32WindowUnavailable("could not read window bounds")
    window_w = window_rect.right - window_rect.left
    window_h = window_rect.bottom - window_rect.top
    if window_w <= 0 or window_h <= 0:
        raise Win32WindowUnavailable("window has zero-size bounds")

    client_rect = wintypes.RECT()
    if not _user32.GetClientRect(hwnd, ctypes.byref(client_rect)):
        raise Win32WindowUnavailable("could not read client rect")
    client_w = client_rect.right - client_rect.left
    client_h = client_rect.bottom - client_rect.top
    if client_w <= 0 or client_h <= 0:
        raise Win32WindowUnavailable("window has zero-size client area")

    origin = wintypes.POINT(0, 0)
    if not _user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        raise Win32WindowUnavailable("could not resolve client origin")
    offset_x, offset_y = client_offset_within_window(
        (window_rect.left, window_rect.top, window_rect.right, window_rect.bottom),
        (origin.x, origin.y),
    )

    screen_dc_handle = _user32.GetDC(None)
    if not screen_dc_handle:
        raise Win32WindowUnavailable("could not acquire a reference device context")
    screen_dc = int(screen_dc_handle)
    try:
        try:
            with GdiBitmapSurface.create(
                _gdi_ops,
                screen_dc,
                window_w,
                window_h,
                label="window capture",
            ) as window_surface:
                if not _user32.PrintWindow(
                    hwnd,
                    window_surface.dc,
                    _PW_RENDERFULLCONTENT,
                ):
                    raise Win32WindowUnavailable("PrintWindow failed")

                with GdiBitmapSurface.create(
                    _gdi_ops,
                    screen_dc,
                    client_w,
                    client_h,
                    label="client crop",
                ) as client_surface:
                    if not _gdi32.BitBlt(
                        client_surface.dc,
                        0,
                        0,
                        client_w,
                        client_h,
                        window_surface.dc,
                        offset_x,
                        offset_y,
                        _SRCCOPY,
                    ):
                        raise Win32WindowUnavailable("cropping the client area failed")

                    header = _BITMAPINFOHEADER(
                        biSize=ctypes.sizeof(_BITMAPINFOHEADER),
                        biWidth=client_w,
                        biHeight=-client_h,
                        biPlanes=1,
                        biBitCount=32,
                        biCompression=_BI_RGB,
                    )
                    info = _BITMAPINFO(bmiHeader=header)
                    buffer = ctypes.create_string_buffer(client_w * client_h * 4)

                    def _read_scanlines(bitmap: int) -> int:
                        return int(
                            _gdi32.GetDIBits(
                                screen_dc,
                                bitmap,
                                0,
                                client_h,
                                buffer,
                                ctypes.byref(info),
                                _DIB_RGB_COLORS,
                            )
                        )

                    read_complete_scanlines(
                        client_surface,
                        client_h,
                        _read_scanlines,
                    )
                    return CapturedPixels(
                        payload=bytes(buffer.raw),
                        width=client_w,
                        height=client_h,
                    )
        except GdiResourceError as exc:
            raise Win32WindowUnavailable(str(exc)) from exc
    finally:
        released = bool(_user32.ReleaseDC(None, screen_dc))
        if not released and sys.exc_info()[0] is None:
            raise Win32WindowUnavailable(
                "could not release reference device context"
            )
