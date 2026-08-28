"""Raw Win32 named-mutex calls for the camera-validation input lease."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Any, Final, cast


def _load_dll(name: str) -> Any:  # noqa: ANN401 - ctypes DLL handles are untyped
    return ctypes.WinDLL(name, use_last_error=True)  # type: ignore[attr-defined]


_kernel32 = _load_dll("kernel32")

_WAIT_OBJECT_0: Final[int] = 0x00000000
_WAIT_ABANDONED: Final[int] = 0x00000080
_WAIT_TIMEOUT: Final[int] = 0x00000102
_WAIT_FAILED: Final[int] = 0xFFFFFFFF

_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.CreateMutexW.argtypes = [
    ctypes.c_void_p,
    wintypes.BOOL,
    wintypes.LPCWSTR,
]
_kernel32.WaitForSingleObject.restype = wintypes.DWORD
_kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel32.ReleaseMutex.restype = wintypes.BOOL
_kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def _last_win32_error() -> OSError:
    return cast(
        OSError,
        ctypes.WinError(ctypes.get_last_error()),  # type: ignore[attr-defined]
    )


def create_named_mutex(name: str) -> int:
    """Create or open an unowned named mutex."""

    handle = _kernel32.CreateMutexW(None, False, name)
    if not handle:
        raise _last_win32_error()
    return int(handle)


def try_acquire(handle: int) -> bool | None:
    """Acquire without waiting; ``None`` reports unsafe abandoned ownership."""

    result = int(_kernel32.WaitForSingleObject(handle, 0))
    if result == _WAIT_OBJECT_0:
        return True
    if result == _WAIT_ABANDONED:
        return None
    if result == _WAIT_TIMEOUT:
        return False
    if result == _WAIT_FAILED:
        raise _last_win32_error()
    raise OSError(f"WaitForSingleObject returned unexpected status 0x{result:08x}")


def release_mutex(handle: int) -> None:
    """Release a named mutex owned by the current thread."""

    if not _kernel32.ReleaseMutex(handle):
        raise _last_win32_error()


def close_handle(handle: int) -> None:
    """Close a process-local mutex handle."""

    if not _kernel32.CloseHandle(handle):
        raise _last_win32_error()
