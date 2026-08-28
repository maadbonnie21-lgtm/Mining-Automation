"""Raw Win32 input calls for development-only camera validation.

Imported lazily by :class:`RealWindowsCameraApi` after a platform check.  The
production capture Win32 seam remains capture-only.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Any, Final

from ..capture.windows.gdi_resources import (
    GdiBitmapSurface,
    GdiResourceError,
    read_complete_scanlines,
)
from .camera_coordinates import (
    CameraCoordinateMapping,
    CameraCoordinateTransform,
    CameraDpiEnvironment,
    LogicalClientPoint,
    LogicalScreenPoint,
    PhysicalScreenPoint,
    map_logical_client_point,
    require_exact_round_trip,
)
from .windows_camera import _require_complete_window_title_snapshot


def _load_dll(name: str) -> Any:  # noqa: ANN401 - ctypes DLL handles are untyped
    return ctypes.WinDLL(name)  # type: ignore[attr-defined]


_user32 = _load_dll("user32")
_gdi32 = _load_dll("gdi32")

_INPUT_MOUSE: Final[int] = 0
_INPUT_KEYBOARD: Final[int] = 1
_MOUSEEVENTF_LEFTDOWN: Final[int] = 0x0002
_MOUSEEVENTF_LEFTUP: Final[int] = 0x0004
_MOUSEEVENTF_MIDDLEDOWN: Final[int] = 0x0020
_MOUSEEVENTF_MIDDLEUP: Final[int] = 0x0040
_MOUSEEVENTF_WHEEL: Final[int] = 0x0800
_KEYEVENTF_EXTENDEDKEY: Final[int] = 0x0001
_KEYEVENTF_KEYUP: Final[int] = 0x0002
_WHEEL_DELTA: Final[int] = 120
_VK_LBUTTON: Final[int] = 0x01
_VK_MBUTTON: Final[int] = 0x04
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2: Final[int] = -4
_DPI_AWARENESS_PER_MONITOR_AWARE: Final[int] = 2
_STANDARD_DPI: Final[int] = 96
_GA_ROOT: Final[int] = 2
_SRCCOPY: Final[int] = 0x00CC0020
_CAPTUREBLT: Final[int] = 0x40000000
_BI_RGB: Final[int] = 0
_DIB_RGB_COLORS: Final[int] = 0


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
        ("rgbBlue", ctypes.c_ubyte),
        ("rgbGreen", ctypes.c_ubyte),
        ("rgbRed", ctypes.c_ubyte),
        ("rgbReserved", ctypes.c_ubyte),
    )


class _BITMAPINFO(ctypes.Structure):
    _fields_ = (
        ("bmiHeader", _BITMAPINFOHEADER),
        ("bmiColors", _RGBQUAD * 1),
    )


_user32.IsWindow.restype = wintypes.BOOL
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.GetWindowThreadProcessId.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(wintypes.DWORD),
]
_user32.GetWindowTextLengthW.restype = ctypes.c_int
_user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_user32.GetWindowTextW.restype = ctypes.c_int
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetClassNameW.restype = ctypes.c_int
_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.SetForegroundWindow.restype = wintypes.BOOL
_user32.SetForegroundWindow.argtypes = [wintypes.HWND]
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetForegroundWindow.argtypes = []
_user32.GetClientRect.restype = wintypes.BOOL
_user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.ClientToScreen.restype = wintypes.BOOL
_user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
_user32.ScreenToClient.restype = wintypes.BOOL
_user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
_user32.LogicalToPhysicalPointForPerMonitorDPI.restype = wintypes.BOOL
_user32.LogicalToPhysicalPointForPerMonitorDPI.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(wintypes.POINT),
]
_user32.PhysicalToLogicalPointForPerMonitorDPI.restype = wintypes.BOOL
_user32.PhysicalToLogicalPointForPerMonitorDPI.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(wintypes.POINT),
]
_user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
_user32.GetThreadDpiAwarenessContext.argtypes = []
_user32.GetWindowDpiAwarenessContext.restype = ctypes.c_void_p
_user32.GetWindowDpiAwarenessContext.argtypes = [wintypes.HWND]
_user32.GetAwarenessFromDpiAwarenessContext.restype = ctypes.c_int
_user32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
_user32.GetDpiForWindow.restype = wintypes.UINT
_user32.GetDpiForWindow.argtypes = [wintypes.HWND]
_user32.SetCursorPos.restype = wintypes.BOOL
_user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
_user32.GetCursorPos.restype = wintypes.BOOL
_user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
_user32.WindowFromPoint.restype = wintypes.HWND
_user32.WindowFromPoint.argtypes = [wintypes.POINT]
_user32.GetAncestor.restype = wintypes.HWND
_user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
_user32.SendInput.restype = wintypes.UINT
_user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
_user32.GetAsyncKeyState.restype = ctypes.c_short
_user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
_user32.GetDC.restype = wintypes.HDC
_user32.GetDC.argtypes = [wintypes.HWND]
_user32.ReleaseDC.restype = ctypes.c_int
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
_gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
_gdi32.SelectObject.restype = wintypes.HANDLE
_gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
_gdi32.DeleteDC.restype = wintypes.BOOL
_gdi32.DeleteDC.argtypes = [wintypes.HDC]
_gdi32.DeleteObject.restype = wintypes.BOOL
_gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
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


class _CameraGdiOps:
    def create_compatible_dc(self, reference_dc: int) -> int | None:
        value = _gdi32.CreateCompatibleDC(reference_dc)
        return int(value) if value else None

    def create_compatible_bitmap(
        self,
        reference_dc: int,
        width: int,
        height: int,
    ) -> int | None:
        value = _gdi32.CreateCompatibleBitmap(reference_dc, width, height)
        return int(value) if value else None

    def select_object(self, dc: int, graphic_object: int) -> int | None:
        value = _gdi32.SelectObject(dc, graphic_object)
        return int(value) if value else None

    def delete_dc(self, dc: int) -> bool:
        return bool(_gdi32.DeleteDC(dc))

    def delete_object(self, graphic_object: int) -> bool:
        return bool(_gdi32.DeleteObject(graphic_object))


_camera_gdi_ops: Final[_CameraGdiOps] = _CameraGdiOps()


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


def window_identity(hwnd: int) -> tuple[int, int, str, str]:
    """Read stable owner/class/title facts for one live HWND.

    The owner is sampled on both sides of the metadata reads.  This is not an
    OS-level atomic transaction, but it rejects a handle replacement observed
    while the identity snapshot is being assembled.  The control rechecks the
    complete snapshot at every input-readiness boundary.
    """

    if not is_window(hwnd):
        raise OSError("target RuneLite window no longer exists")
    owner_before = _window_owner(hwnd)
    class_name = _window_class_name(hwnd)
    title = _window_title(hwnd)
    owner_after = _window_owner(hwnd)
    if owner_after != owner_before or not is_window(hwnd):
        raise OSError("target RuneLite window identity changed while being read")
    process_id, thread_id = owner_before
    return process_id, thread_id, class_name, title


def _window_owner(hwnd: int) -> tuple[int, int]:
    process_id = wintypes.DWORD()
    thread_id = int(
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    )
    if thread_id <= 0 or process_id.value <= 0:
        raise OSError("GetWindowThreadProcessId failed for the target RuneLite window")
    return int(process_id.value), thread_id


def _window_class_name(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    copied = int(_user32.GetClassNameW(hwnd, buffer, len(buffer)))
    if copied <= 0:
        raise OSError("GetClassNameW failed for the target RuneLite window")
    return str(buffer.value)


def _window_title(hwnd: int) -> str:
    expected_length = int(_user32.GetWindowTextLengthW(hwnd))
    if expected_length <= 0:
        raise OSError("target RuneLite window has no readable title")
    buffer = ctypes.create_unicode_buffer(expected_length + 1)
    copied = int(_user32.GetWindowTextW(hwnd, buffer, len(buffer)))
    final_length = int(_user32.GetWindowTextLengthW(hwnd))
    return _require_complete_window_title_snapshot(
        str(buffer.value),
        expected_length=expected_length,
        copied_length=copied,
        final_length=final_length,
    )


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


def capture_physical_screen_rect(
    left: int,
    top: int,
    width: int,
    height: int,
) -> bytes:
    """Capture one physical screen rectangle without moving or sending input."""

    _require_physical_caller_context()
    if width <= 0 or height <= 0:
        raise ValueError("physical screen capture requires positive dimensions")
    screen_dc_handle = _user32.GetDC(None)
    if not screen_dc_handle:
        raise OSError("GetDC(NULL) failed for physical screen capture")
    screen_dc = int(screen_dc_handle)
    primary_error: BaseException | None = None
    payload: bytes | None = None
    try:
        try:
            with GdiBitmapSurface.create(
                _camera_gdi_ops,
                screen_dc,
                width,
                height,
                label="camera pointer physical screen capture",
            ) as surface:
                if not _gdi32.BitBlt(
                    surface.dc,
                    0,
                    0,
                    width,
                    height,
                    screen_dc,
                    left,
                    top,
                    _SRCCOPY | _CAPTUREBLT,
                ):
                    raise OSError("BitBlt failed for physical screen capture")
                header = _BITMAPINFOHEADER(
                    biSize=ctypes.sizeof(_BITMAPINFOHEADER),
                    biWidth=width,
                    biHeight=-height,
                    biPlanes=1,
                    biBitCount=32,
                    biCompression=_BI_RGB,
                    biSizeImage=width * height * 4,
                )
                info = _BITMAPINFO(bmiHeader=header)
                buffer = ctypes.create_string_buffer(width * height * 4)

                def _read_scanlines(bitmap: int) -> int:
                    return int(
                        _gdi32.GetDIBits(
                            screen_dc,
                            bitmap,
                            0,
                            height,
                            buffer,
                            ctypes.byref(info),
                            _DIB_RGB_COLORS,
                        )
                    )

                read_complete_scanlines(surface, height, _read_scanlines)
                payload = bytes(buffer.raw)
        except GdiResourceError as exc:
            primary_error = OSError(str(exc))
    except BaseException as exc:
        primary_error = exc
    finally:
        if _user32.ReleaseDC(None, screen_dc) != 1 and primary_error is None:
            primary_error = OSError("could not release physical screen DC")
    if primary_error is not None:
        raise primary_error
    if payload is None:  # pragma: no cover - defensive native guard
        raise OSError("physical screen capture returned no payload")
    return payload


def _context_value(context: Any) -> int:  # noqa: ANN401 - ctypes handle is untyped
    """Normalize a pointer-sized DPI context pseudo-handle to a signed int."""

    raw = context.value if isinstance(context, ctypes.c_void_p) else context
    if raw is None:
        return 0
    value = int(raw)
    bit_count = ctypes.sizeof(ctypes.c_void_p) * 8
    if value >= 1 << (bit_count - 1):
        value -= 1 << bit_count
    return value


def _awareness_value(context: Any) -> int:  # noqa: ANN401 - ctypes handle is untyped
    return int(_user32.GetAwarenessFromDpiAwarenessContext(context))


def _awareness_name(value: int) -> str:
    return {
        -1: "invalid",
        0: "unaware",
        1: "system_aware",
        2: "per_monitor_aware",
    }.get(value, f"unknown({value})")


def _require_physical_caller_context() -> None:
    """Require device coordinates before using ClientToScreen/ScreenToClient."""

    context = _user32.GetThreadDpiAwarenessContext()
    awareness = _awareness_value(context)
    if awareness != _DPI_AWARENESS_PER_MONITOR_AWARE:
        raise OSError(
            "camera pointer mapping requires a per-monitor-aware caller so "
            "ClientToScreen and ScreenToClient use physical device coordinates; "
            f"current thread awareness is {_awareness_name(awareness)}"
        )


class _NativeCameraCoordinateTransform(CameraCoordinateTransform):
    """Typed adapter over the three native coordinate-space transitions."""

    def physical_client_origin(self, hwnd: int) -> PhysicalScreenPoint:
        _require_physical_caller_context()
        point = wintypes.POINT(0, 0)
        if not _user32.ClientToScreen(hwnd, ctypes.byref(point)):
            raise OSError("ClientToScreen failed for the target RuneLite client origin")
        return PhysicalScreenPoint(int(point.x), int(point.y))

    def physical_to_target_logical(
        self,
        hwnd: int,
        point: PhysicalScreenPoint,
    ) -> LogicalScreenPoint:
        native_point = wintypes.POINT(point.x, point.y)
        if not _user32.PhysicalToLogicalPointForPerMonitorDPI(
            hwnd,
            ctypes.byref(native_point),
        ):
            raise OSError(
                "PhysicalToLogicalPointForPerMonitorDPI failed for the target "
                "RuneLite window"
            )
        return LogicalScreenPoint(int(native_point.x), int(native_point.y))

    def target_logical_to_physical(
        self,
        hwnd: int,
        point: LogicalScreenPoint,
    ) -> PhysicalScreenPoint:
        native_point = wintypes.POINT(point.x, point.y)
        if not _user32.LogicalToPhysicalPointForPerMonitorDPI(
            hwnd,
            ctypes.byref(native_point),
        ):
            raise OSError(
                "LogicalToPhysicalPointForPerMonitorDPI failed for the target "
                "RuneLite window"
            )
        return PhysicalScreenPoint(int(native_point.x), int(native_point.y))


_coordinate_transform: Final[CameraCoordinateTransform] = _NativeCameraCoordinateTransform()


def pointer_mapping(hwnd: int, x: int, y: int) -> CameraCoordinateMapping:
    """Return every space in the safe logical-client to physical-screen mapping."""

    return map_logical_client_point(
        hwnd,
        LogicalClientPoint(x, y),
        _coordinate_transform,
    )


def client_to_screen(hwnd: int, x: int, y: int) -> tuple[int, int]:
    """Map a RuneLite logical-client point to an exact physical screen point.

    ``ClientToScreen`` is intentionally used only for client origin ``(0, 0)``
    while the caller is verified per-monitor aware.  The physical origin is
    converted into the target window's logical screen space, the reviewed
    client delta is added there, and only then is the logical screen point
    passed to ``LogicalToPhysicalPointForPerMonitorDPI``.  The reverse target
    transform must recover the original logical client point exactly.
    """

    return require_exact_round_trip(pointer_mapping(hwnd, x, y)).pair


def physical_screen_to_physical_client(hwnd: int, x: int, y: int) -> tuple[int, int]:
    """Use ScreenToClient only with a physical-screen point and physical caller."""

    _require_physical_caller_context()
    point = wintypes.POINT(x, y)
    if not _user32.ScreenToClient(hwnd, ctypes.byref(point)):
        raise OSError("ScreenToClient failed for the target RuneLite window")
    return int(point.x), int(point.y)


def mapping_candidate_comparison(hwnd: int, x: int, y: int) -> dict[str, object]:
    """Compute legacy and corrected candidates without moving or clicking.

    The two legacy values exist only to make a native audit self-contained.
    Production pointer code consumes only :func:`client_to_screen`, which uses
    the origin-based robust mapping and exact round-trip gate.
    """

    _require_physical_caller_context()
    unscaled_point = wintypes.POINT(x, y)
    if not _user32.ClientToScreen(hwnd, ctypes.byref(unscaled_point)):
        raise OSError("comparison ClientToScreen failed for the target RuneLite window")

    wrong_order_point = wintypes.POINT(x, y)
    if not _user32.LogicalToPhysicalPointForPerMonitorDPI(
        hwnd,
        ctypes.byref(wrong_order_point),
    ):
        raise OSError(
            "comparison LogicalToPhysicalPointForPerMonitorDPI failed for the "
            "target RuneLite window"
        )
    wrong_order_intermediate = (
        int(wrong_order_point.x),
        int(wrong_order_point.y),
    )
    if not _user32.ClientToScreen(hwnd, ctypes.byref(wrong_order_point)):
        raise OSError(
            "comparison wrong-order ClientToScreen failed for the target "
            "RuneLite window"
        )

    corrected = pointer_mapping(hwnd, x, y)
    return {
        "unscaled_client_to_screen": {
            "sequence": [
                "logical_client_input_misinterpreted_as_physical_client",
                "ClientToScreen",
                "physical_screen_candidate",
            ],
            "physical_screen_candidate": [
                int(unscaled_point.x),
                int(unscaled_point.y),
            ],
            "production_eligible": False,
        },
        "audited_wrong_order": {
            "sequence": [
                "logical_client_input_misinterpreted_as_logical_screen",
                "LogicalToPhysicalPointForPerMonitorDPI",
                "physical_screen_intermediate_misinterpreted_as_client",
                "ClientToScreen",
                "physical_screen_candidate",
            ],
            "physical_screen_intermediate": list(wrong_order_intermediate),
            "physical_screen_candidate": [
                int(wrong_order_point.x),
                int(wrong_order_point.y),
            ],
            "production_eligible": False,
        },
        "origin_based_round_trip_checked": {
            "sequence": [
                "physical_client_origin",
                "PhysicalToLogicalPointForPerMonitorDPI",
                "add_logical_client_delta",
                "LogicalToPhysicalPointForPerMonitorDPI",
                "physical_screen",
                "PhysicalToLogicalPointForPerMonitorDPI",
                "subtract_target_logical_screen_origin",
                "reverse_logical_client",
            ],
            "physical_screen_candidate": list(corrected.physical_screen.pair),
            "reverse_logical_client": list(corrected.reverse_logical_client.pair),
            "exact_round_trip": corrected.exact_round_trip,
            "production_eligible": corrected.exact_round_trip,
        },
    }


def dpi_environment(hwnd: int) -> CameraDpiEnvironment:
    """Collect no-input native awareness, geometry, and effective scale facts."""

    _require_physical_caller_context()
    caller_thread_context = _user32.GetThreadDpiAwarenessContext()
    caller_thread_awareness = _awareness_value(caller_thread_context)
    target_context = _user32.GetWindowDpiAwarenessContext(hwnd)
    target_awareness = _awareness_value(target_context)

    process_context_fn = getattr(_user32, "GetDpiAwarenessContextForProcess", None)
    caller_process_context: Any | None = None
    caller_process_awareness: int | None = None
    if process_context_fn is not None:
        process_context_fn.restype = ctypes.c_void_p
        process_context_fn.argtypes = [wintypes.HANDLE]
        caller_process_context = process_context_fn(None)
        caller_process_awareness = _awareness_value(caller_process_context)

    target_dpi = int(_user32.GetDpiForWindow(hwnd))
    if target_dpi <= 0:
        raise OSError("GetDpiForWindow failed for the target RuneLite window")

    origin = pointer_mapping(hwnd, 0, 0)
    x_basis = pointer_mapping(hwnd, _STANDARD_DPI, 0)
    y_basis = pointer_mapping(hwnd, 0, _STANDARD_DPI)
    require_exact_round_trip(origin)
    require_exact_round_trip(x_basis)
    require_exact_round_trip(y_basis)
    scale_x = (
        x_basis.physical_screen.x - origin.physical_screen.x
    ) / _STANDARD_DPI
    scale_y = (
        y_basis.physical_screen.y - origin.physical_screen.y
    ) / _STANDARD_DPI
    if scale_x <= 0.0 or scale_y <= 0.0:
        raise OSError(
            "target DPI mapping returned a non-positive effective scale: "
            f"x={scale_x}, y={scale_y}"
        )
    physical_size = client_size(hwnd)
    logical_size = (
        round(physical_size[0] / scale_x),
        round(physical_size[1] / scale_y),
    )
    return CameraDpiEnvironment(
        caller_thread_context=_context_value(caller_thread_context),
        caller_thread_awareness=_awareness_name(caller_thread_awareness),
        caller_process_context=(
            _context_value(caller_process_context)
            if caller_process_context is not None
            else None
        ),
        caller_process_awareness=(
            _awareness_name(caller_process_awareness)
            if caller_process_awareness is not None
            else None
        ),
        target_window_context=_context_value(target_context),
        target_window_awareness=_awareness_name(target_awareness),
        target_window_dpi=target_dpi,
        target_window_scale=target_dpi / _STANDARD_DPI,
        effective_mapping_dpi_x=scale_x * _STANDARD_DPI,
        effective_mapping_dpi_y=scale_y * _STANDARD_DPI,
        effective_mapping_scale_x=scale_x,
        effective_mapping_scale_y=scale_y,
        physical_client_size=physical_size,
        estimated_target_logical_client_size=logical_size,
    )


def move_cursor(x: int, y: int) -> bool:
    return bool(_user32.SetCursorPos(x, y))


def cursor_position() -> tuple[int, int]:
    """Return the cursor's actual physical screen coordinate."""

    point = wintypes.POINT()
    if not _user32.GetCursorPos(ctypes.byref(point)):
        raise OSError("GetCursorPos failed while verifying camera input safety")
    return int(point.x), int(point.y)


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


def send_middle_button(*, button_up: bool) -> int:
    """Send exactly one middle-button phase and return its accepted count."""

    event = _mouse_input(
        _MOUSEEVENTF_MIDDLEUP if button_up else _MOUSEEVENTF_MIDDLEDOWN
    )
    return int(_user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(_INPUT)))


def middle_button_is_down() -> bool:
    """Return whether the global middle button is currently held."""

    return bool(_user32.GetAsyncKeyState(_VK_MBUTTON) & 0x8000)


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
