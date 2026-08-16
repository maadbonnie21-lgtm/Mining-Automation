"""Capture layer: acquire frames from a supported surface.

This package owns the boundary between platform-specific screen acquisition and
the rest of the application. Consumers depend on :class:`CaptureSource` and
:class:`Frame`; they never import a platform backend.

Typical use::

    from mining_automation.capture import CaptureSource
    from mining_automation.capture.testing import FakeCaptureBackend

    with CaptureSource(FakeCaptureBackend()) as source:
        frame = source.capture()

See ``docs/CAPTURE.md`` for supported behaviour, guarantees, and limitations.
"""

from __future__ import annotations

from .backend import CaptureBackend, MonotonicClock, SystemMonotonicClock
from .errors import (
    CaptureBackendError,
    CaptureClosedError,
    CaptureError,
    CaptureFailureThresholdExceeded,
    CaptureTimeoutError,
    CaptureUnavailableError,
    InvalidFrameError,
    InvalidTimestampError,
    NonMonotonicCaptureError,
)
from .frame import Frame, PixelFormat, RawFrame
from .source import DEFAULT_MAX_CONSECUTIVE_FAILURES, CaptureSource, CaptureStats

__all__ = [
    "DEFAULT_MAX_CONSECUTIVE_FAILURES",
    "CaptureBackend",
    "CaptureBackendError",
    "CaptureClosedError",
    "CaptureError",
    "CaptureFailureThresholdExceeded",
    "CaptureSource",
    "CaptureStats",
    "CaptureTimeoutError",
    "CaptureUnavailableError",
    "Frame",
    "InvalidFrameError",
    "InvalidTimestampError",
    "MonotonicClock",
    "NonMonotonicCaptureError",
    "PixelFormat",
    "RawFrame",
    "SystemMonotonicClock",
]
