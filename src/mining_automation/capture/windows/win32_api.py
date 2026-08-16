"""The Win32 seam.

Everything that actually talks to Windows lives behind :class:`Win32Api`. The
backend in :mod:`.backend` depends only on this protocol, never on
:mod:`ctypes` directly, which is what lets ``WindowsCaptureBackend`` be
constructed, protocol-checked, and exercised through every failure path on any
platform including the Linux CI runner this project builds on. Only
:class:`RealWin32Api` in this module touches the actual Windows API, and only
inside methods -- never at import time -- so importing this module is safe
everywhere even though *using* :class:`RealWin32Api` is not.

Mirrors the dependency-injection shape the capture layer already uses for
:class:`~mining_automation.capture.backend.MonotonicClock`.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "CapturedPixels",
    "RealWin32Api",
    "Win32Api",
    "Win32WindowUnavailable",
    "WindowInfo",
]


class Win32WindowUnavailable(Exception):
    """Raised by a :class:`Win32Api` implementation when a window cannot be
    read right now: minimized, closed since discovery, zero-size client area,
    or otherwise unreadable.

    Deliberately not part of the shared
    :mod:`mining_automation.capture.errors` taxonomy. This is a platform-layer
    signal; :class:`~.backend.WindowsCaptureBackend` is the single place that
    translates it into
    :class:`~mining_automation.capture.errors.CaptureUnavailableError`, per
    "keep Win32/platform code isolated from the generic capture consumer API."
    """


@dataclass(frozen=True, slots=True)
class WindowInfo:
    """A candidate top-level window, as plain data.

    Deliberately holds no OS handle beyond the raw integer HWND value, and no
    behaviour. Window *selection* (:mod:`.window_selector`) operates purely on
    lists of this type, which is what makes selection logic testable without
    mocking a single OS call.
    """

    hwnd: int
    title: str
    class_name: str
    is_visible: bool
    is_minimized: bool
    client_width: int
    client_height: int


@dataclass(frozen=True, slots=True)
class CapturedPixels:
    """Raw pixels captured from a window's client area.

    ``width``/``height`` are measured at capture time, independently of
    whatever :class:`WindowInfo` reported at discovery time -- the window may
    have resized in between. ``payload`` is BGRA, 4 bytes per pixel, top-down
    row order (row 0 is the top of the image).
    """

    payload: bytes
    width: int
    height: int


@runtime_checkable
class Win32Api(Protocol):
    """Everything the Windows backend needs from the operating system.

    Implementations must honour:

    * ``enumerate_windows`` never raises for an empty result; zero candidates
      is a valid, non-exceptional answer.
    * ``capture_client_area`` raises :class:`Win32WindowUnavailable` for a
      minimized, closed, or zero-size window, and lets any other failure
      propagate as whatever exception is natural to the implementation --
      the backend normalises it.
    * ``declare_dpi_awareness`` is best-effort and never raises. Windows may
      reject a redundant request if awareness was already declared elsewhere
      (for example by an application manifest); that is not a failure.
    """

    def declare_dpi_awareness(self) -> None:
        """Request per-monitor DPI awareness for this process.

        Called once, from the backend's ``open()``. With awareness declared,
        every subsequent geometry query returns physical pixels directly, so
        capture dimensions need no further scaling.
        """
        ...

    def enumerate_windows(self) -> list[WindowInfo]:
        """Snapshot every top-level window currently on the desktop."""
        ...

    def get_dpi_for_window(self, hwnd: int) -> int:
        """The DPI Windows currently associates with ``hwnd`` (96 = 100%)."""
        ...

    def capture_client_area(self, hwnd: int) -> CapturedPixels:
        """Capture the current pixels of ``hwnd``'s client area.

        Raises:
            Win32WindowUnavailable: the window is minimized, has been closed,
                or its client area is currently zero-size.
        """
        ...


class RealWin32Api:
    """Production :class:`Win32Api`, implemented with :mod:`ctypes`.

    Construction raises immediately on any platform other than Windows,
    before any Windows API is touched, so a misuse surfaces as one clear
    error rather than a confusing failure deep inside the first real call.
    """

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError(
                "RealWin32Api requires Windows (sys.platform == 'win32'); "
                f"got {sys.platform!r}. Use capture.windows.testing.FakeWin32Api "
                "for tests on other platforms."
            )
        # Imported lazily, inside __init__, so this module remains importable
        # (and WindowsCaptureBackend remains constructible and
        # protocol-checkable) on non-Windows platforms such as Linux CI.
        from . import _win32_calls

        self._calls: Any = _win32_calls

    def declare_dpi_awareness(self) -> None:
        self._calls.declare_dpi_awareness()

    def enumerate_windows(self) -> list[WindowInfo]:
        result: list[WindowInfo] = self._calls.enumerate_windows()
        return result

    def get_dpi_for_window(self, hwnd: int) -> int:
        result: int = self._calls.get_dpi_for_window(hwnd)
        return result

    def capture_client_area(self, hwnd: int) -> CapturedPixels:
        result: CapturedPixels = self._calls.capture_client_area(hwnd)
        return result
