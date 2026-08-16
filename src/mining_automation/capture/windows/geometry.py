"""Pure geometry arithmetic for the Windows backend.

No :mod:`ctypes`, no OS calls, nothing platform-specific. Kept separate from
:mod:`._win32_calls` specifically so it can be imported and unit tested on any
platform -- putting it in the same file as the DLL loading would have made it
inherit that file's Windows-only importability for no reason.
"""

from __future__ import annotations

__all__ = ["client_offset_within_window"]


def client_offset_within_window(
    window_rect: tuple[int, int, int, int],
    client_origin_screen: tuple[int, int],
) -> tuple[int, int]:
    """Offset of the client area's top-left corner within the full window.

    Used to crop a full-window capture down to the client area, since
    combining ``PW_CLIENTONLY`` with ``PW_RENDERFULLCONTENT`` is documented as
    unreliable across Windows versions -- the window is captured whole via the
    DWM-aware path and cropped in memory instead.
    """
    window_left, window_top, _, _ = window_rect
    client_left, client_top = client_origin_screen
    return (client_left - window_left, client_top - window_top)
