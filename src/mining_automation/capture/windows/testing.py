"""Deterministic test double for :class:`~.win32_api.Win32Api`.

Lets the whole selection/capture/error-mapping path be exercised without
Windows, ctypes, or a real RuneLite process. Every test in
``tests/test_windows_capture.py`` drives :class:`~.backend.WindowsCaptureBackend`
through this fake rather than :class:`~.win32_api.RealWin32Api`.
"""

from __future__ import annotations

from collections.abc import Iterable

from .win32_api import CapturedPixels, Win32WindowUnavailable, WindowInfo

__all__ = ["FakeWin32Api", "solid_pixels"]


def solid_pixels(width: int, height: int, value: int = 0) -> bytes:
    """A correctly sized BGRA payload filled with one byte value."""
    return bytes([value & 0xFF]) * (width * height * 4)


class FakeWin32Api:
    """Scriptable :class:`~.win32_api.Win32Api`.

    ``windows`` is returned verbatim from :meth:`enumerate_windows` on every
    call, so a test can mutate ``self.windows`` between calls to simulate the
    desktop changing (a window closing, a new one appearing).

    ``captures`` maps an ``hwnd`` to either a :class:`CapturedPixels` or an
    exception instance to raise -- or to a callable returning either, for
    tests that need capture results to change between successive calls (for
    example, simulating a live resize).
    """

    def __init__(
        self,
        windows: Iterable[WindowInfo] | None = None,
        captures: dict[int, object] | None = None,
        dpi_by_hwnd: dict[int, int] | None = None,
    ) -> None:
        self.windows: list[WindowInfo] = list(windows) if windows is not None else []
        self.captures: dict[int, object] = dict(captures) if captures is not None else {}
        self.dpi_by_hwnd: dict[int, int] = dict(dpi_by_hwnd) if dpi_by_hwnd is not None else {}

        self.dpi_awareness_declared = 0
        self.enumerate_calls = 0
        self.capture_calls: list[int] = []

    def declare_dpi_awareness(self) -> None:
        self.dpi_awareness_declared += 1

    def enumerate_windows(self) -> list[WindowInfo]:
        self.enumerate_calls += 1
        return list(self.windows)

    def get_dpi_for_window(self, hwnd: int) -> int:
        return self.dpi_by_hwnd.get(hwnd, 96)

    def capture_client_area(self, hwnd: int) -> CapturedPixels:
        self.capture_calls.append(hwnd)
        if hwnd not in self.captures:
            raise Win32WindowUnavailable(f"no scripted capture for hwnd {hwnd}")

        entry = self.captures[hwnd]
        if callable(entry):
            entry = entry()
        if isinstance(entry, BaseException):
            raise entry
        if isinstance(entry, CapturedPixels):
            return entry
        raise TypeError(f"unexpected scripted capture entry: {entry!r}")
