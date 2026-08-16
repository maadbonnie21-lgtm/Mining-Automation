"""Windows capture backend for a real RuneLite client window.

Nothing in :mod:`mining_automation.capture` imports this package -- it is an
optional, Windows-specific implementation of the platform-independent
:class:`~mining_automation.capture.backend.CaptureBackend` protocol from
Issue #1, kept separate so that generic capture consumers, and the rest of
that package, never depend on Windows.

The only third-party dependency is none: everything here is built on
:mod:`ctypes`, part of the standard library. See ``docs/CAPTURE_WINDOWS.md``
for the rationale, DPI handling, supported assumptions, and local validation
steps.

    from mining_automation.capture import CaptureSource
    from mining_automation.capture.windows import WindowsCaptureBackend

    with CaptureSource(WindowsCaptureBackend()) as source:
        frame = source.capture()

Test doubles live in :mod:`.testing`; production Win32 calls live in the
private :mod:`._win32_calls`, imported only by :class:`~.win32_api.RealWin32Api`.
"""

from __future__ import annotations

from .backend import WindowsCaptureBackend
from .win32_api import CapturedPixels, RealWin32Api, Win32Api, Win32WindowUnavailable, WindowInfo
from .window_selector import DEFAULT_TITLE_SUBSTRING, select_window

__all__ = [
    "DEFAULT_TITLE_SUBSTRING",
    "CapturedPixels",
    "RealWin32Api",
    "Win32Api",
    "Win32WindowUnavailable",
    "WindowInfo",
    "WindowsCaptureBackend",
    "select_window",
]
