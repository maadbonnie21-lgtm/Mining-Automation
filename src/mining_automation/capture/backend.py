"""The platform seam.

A backend does exactly one job: hand back the current pixels of a surface. It
does not assign frame identity, does not timestamp, does not retry, does not
decide whether a frame is valid, and does not know what a mine is.

Everything above this seam depends on :class:`CaptureBackend`, never on a
concrete platform implementation, which is what lets the rest of the
application be tested without a display.
"""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable

from .frame import RawFrame

__all__ = ["CaptureBackend", "MonotonicClock", "SystemMonotonicClock"]


@runtime_checkable
class MonotonicClock(Protocol):
    """Injected time source. Tests substitute a deterministic implementation."""

    def monotonic_s(self) -> float: ...


class SystemMonotonicClock:
    """Production clock. Wraps :func:`time.monotonic`."""

    __slots__ = ()

    def monotonic_s(self) -> float:
        return time.monotonic()


@runtime_checkable
class CaptureBackend(Protocol):
    """Platform-specific frame acquisition.

    Implementations must honour these rules:

    * ``grab`` raises on failure. It never returns a previously captured frame,
      a partially written buffer, or ``None``.
    * ``grab`` raises
      :class:`~mining_automation.capture.errors.CaptureUnavailableError` when the
      surface is temporarily unreadable (minimised, locked session, display
      asleep) and lets other exceptions propagate for the source to wrap.
    * ``open`` is idempotent, and so is ``close``.
    * The payload of a returned :class:`~mining_automation.capture.frame.RawFrame`
      may alias a reusable internal buffer; the source copies it.
    """

    @property
    def name(self) -> str:
        """Stable identifier recorded in diagnostics, e.g. ``"fake"``."""
        ...

    def open(self) -> None:
        """Acquire platform resources. Idempotent."""
        ...

    def grab(self) -> RawFrame:
        """Return the current contents of the surface, or raise."""
        ...

    def close(self) -> None:
        """Release platform resources. Idempotent."""
        ...
