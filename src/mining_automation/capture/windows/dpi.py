"""DPI scaling arithmetic.

Correctness against real Windows scaling depends primarily on declaring
per-monitor DPI awareness once at backend ``open()`` time
(:meth:`~.win32_api.Win32Api.declare_dpi_awareness`) -- with awareness
declared, every geometry query already returns physical pixels, so capture
dimensions need no further conversion.

What lives here is the remainder: a pure conversion from a DPI value to a
scale factor, used for diagnostics and for anything downstream that wants to
reason about a window's current scaling. Kept separate from
:mod:`._win32_calls` so it is testable without any OS call at all.
"""

from __future__ import annotations

__all__ = ["STANDARD_DPI", "scale_factor_for_dpi"]

#: Windows' 100%-scaling reference point.
STANDARD_DPI = 96


def scale_factor_for_dpi(dpi: int) -> float:
    """Convert a DPI value to a scale factor (96 DPI -> 1.0, 144 -> 1.5, ...).

    Raises:
        ValueError: ``dpi`` is not a positive integer.
    """
    if dpi <= 0:
        raise ValueError(f"dpi must be positive, got {dpi}")
    return dpi / STANDARD_DPI
