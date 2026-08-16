"""Deterministic RuneLite window selection.

Pure functions over :class:`~.win32_api.WindowInfo`. No OS calls, no ctypes,
nothing platform-specific -- which is what makes selection behaviour, the
multi-candidate tie-break, and the "no match" case fully testable on any
platform without mocking a single Win32 call.
"""

from __future__ import annotations

from .win32_api import WindowInfo

__all__ = ["DEFAULT_TITLE_SUBSTRING", "select_window", "matches"]

DEFAULT_TITLE_SUBSTRING = "runelite"


def matches(window: WindowInfo, title_substring: str) -> bool:
    """Whether ``window`` is a plausible RuneLite candidate.

    Matching is a case-insensitive substring test against the window title.
    RuneLite's title varies by launch mode and logged-in state (for example
    ``"RuneLite"`` versus ``"RuneLite - PlayerName"``), so an exact match
    would be fragile; a substring is the same approach the issue's own
    examples use ("target a RuneLite window ... not fixed desktop
    coordinates").
    """
    if not title_substring:
        return False
    return title_substring.lower() in window.title.lower()


def select_window(
    windows: list[WindowInfo],
    title_substring: str = DEFAULT_TITLE_SUBSTRING,
) -> WindowInfo | None:
    """Pick one window from a snapshot, or ``None`` if nothing matches.

    Candidates are filtered to visible windows whose title contains
    ``title_substring`` (case-insensitive) and whose reported client area is
    not already known to be zero-size. Among the remaining candidates,
    selection is fully deterministic so repeated runs against the same
    desktop state always choose the same window:

    1. not minimized before minimized
    2. larger client area before smaller
    3. lower ``hwnd`` before higher, as a final, arbitrary but stable
       tie-break

    A minimized candidate is not discarded outright -- a session that starts
    with RuneLite minimized should still resolve to it, so the backend can
    report a clear "minimized" failure on the first capture attempt rather
    than "no window found", which would be misleading.
    """
    candidates = [
        w
        for w in windows
        if w.is_visible and matches(w, title_substring) and _has_plausible_size(w)
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda w: (w.is_minimized, -(w.client_width * w.client_height), w.hwnd),
    )


def _has_plausible_size(window: WindowInfo) -> bool:
    # A minimized window commonly reports a zero-size client rect; that is
    # expected and does not disqualify it (see select_window). What this
    # excludes is a *visible, non-minimized* window with a degenerate rect,
    # which is not a usable capture target regardless of title match.
    if window.is_minimized:
        return True
    return window.client_width > 0 and window.client_height > 0
