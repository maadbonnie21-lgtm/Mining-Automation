"""The Windows ``CaptureBackend`` implementation.

Wires :mod:`.window_selector` (deterministic candidate selection) to a
:class:`~.win32_api.Win32Api` (real or fake) and translates its one failure
signal, :class:`~.win32_api.Win32WindowUnavailable`, into the shared
:class:`~mining_automation.capture.errors.CaptureUnavailableError` taxonomy
from Issue #1. Any other exception the API raises is left to propagate --
:class:`~mining_automation.capture.source.CaptureSource` already normalises an
unrecognised backend exception into
:class:`~mining_automation.capture.errors.CaptureBackendError`, so that
mapping is not duplicated here.
"""

from __future__ import annotations

from ..errors import CaptureUnavailableError
from ..frame import PixelFormat, RawFrame
from .win32_api import RealWin32Api, Win32Api, Win32WindowUnavailable, WindowInfo
from .window_selector import DEFAULT_TITLE_SUBSTRING, select_window

__all__ = ["WindowsCaptureBackend"]


class WindowsCaptureBackend:
    """Captures a RuneLite client window's content area on Windows.

    Satisfies :class:`~mining_automation.capture.backend.CaptureBackend`.

    Args:
        win32_api: Injected OS seam. Defaults to :class:`RealWin32Api`, which
            requires Windows; pass
            :class:`~.testing.FakeWin32Api` for tests.
        title_substring: Case-insensitive substring the target window's title
            must contain. Not a fixed window, not a coordinate -- the window
            is re-resolved by this criterion whenever discovery runs.

    The window handle is cached across successful captures rather than
    re-resolved every frame, since the same handle stays valid across window
    movement and resizing -- geometry is re-read fresh on every capture
    regardless. Any capture failure invalidates the cached handle, so the
    next attempt re-runs discovery rather than retrying a handle that may no
    longer point at anything.
    """

    def __init__(
        self,
        win32_api: Win32Api | None = None,
        *,
        title_substring: str = DEFAULT_TITLE_SUBSTRING,
    ) -> None:
        self._api: Win32Api = win32_api if win32_api is not None else RealWin32Api()
        self._title_substring = title_substring
        self._window: WindowInfo | None = None
        self._is_open = False

    @property
    def name(self) -> str:
        return "windows-runelite"

    @property
    def selected_window(self) -> WindowInfo | None:
        """The window currently targeted, or ``None`` if none is resolved.

        Reflects the state as of the last discovery, not necessarily the
        outcome of the most recent capture attempt -- a failed capture clears
        this immediately so it never reports a window that just proved
        unreachable.
        """
        return self._window

    @property
    def current_dpi(self) -> int | None:
        """DPI Windows currently reports for the selected window, or ``None``
        if no window is currently resolved. 96 means 100% scaling."""
        if self._window is None:
            return None
        return self._api.get_dpi_for_window(self._window.hwnd)

    def open(self) -> None:
        if self._is_open:
            return
        self._api.declare_dpi_awareness()
        self._is_open = True

    def close(self) -> None:
        # No OS handle is held across grab() calls -- each capture creates and
        # releases its own GDI resources -- so closing only needs to drop the
        # cached window, which also ensures a later open() re-resolves the
        # target rather than trusting one cached before the pause.
        self._is_open = False
        self._window = None

    def grab(self) -> RawFrame:
        if not self._is_open:
            raise CaptureUnavailableError(f"{self.name} backend is not open")

        if self._window is None:
            self._window = self._discover_window()

        try:
            pixels = self._api.capture_client_area(self._window.hwnd)
        except Win32WindowUnavailable as exc:
            self._window = None
            raise CaptureUnavailableError(str(exc)) from exc

        return RawFrame(
            payload=pixels.payload,
            width=pixels.width,
            height=pixels.height,
            pixel_format=PixelFormat.BGRA8888,
        )

    def _discover_window(self) -> WindowInfo:
        windows = self._api.enumerate_windows()
        selected = select_window(windows, self._title_substring)
        if selected is None:
            raise CaptureUnavailableError(
                f"no window found matching {self._title_substring!r}"
            )
        return selected
