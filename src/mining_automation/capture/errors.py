"""Capture failure taxonomy.

Every failure mode is a distinct type so consumers and recovery policy can
branch on *why* capture failed rather than parsing a message. Nothing in this
package returns a sentinel or a stale frame on failure: capture either yields a
valid owned frame or raises.
"""

from __future__ import annotations

__all__ = [
    "CaptureBackendError",
    "CaptureClosedError",
    "CaptureError",
    "CaptureFailureThresholdExceeded",
    "CaptureTimeoutError",
    "CaptureUnavailableError",
    "InvalidFrameError",
    "InvalidTimestampError",
    "NonMonotonicCaptureError",
]


class CaptureError(Exception):
    """Base class for every capture failure."""


class CaptureClosedError(CaptureError):
    """A capture operation was requested on a source that is not open."""


class CaptureUnavailableError(CaptureError):
    """The capture surface exists but cannot be read right now.

    Typical causes: the target window is minimised, occluded by a secure
    surface, on a disconnected display, or the session is locked. Distinct from
    :class:`CaptureBackendError` because it is usually transient and recoverable
    by waiting, whereas a backend error usually is not.
    """


class CaptureTimeoutError(CaptureError):
    """The backend did not produce a frame inside the allotted time."""


class CaptureBackendError(CaptureError):
    """The platform backend failed in a way the capture layer cannot interpret.

    The originating exception is preserved as ``__cause__``.
    """


class InvalidFrameError(CaptureError):
    """A backend returned a frame that failed validation.

    Raised for non-positive dimensions, an empty payload, or a payload whose
    length disagrees with ``width * height * bytes_per_pixel``. A truncated
    payload is treated as a hard failure rather than being padded or cropped,
    because silently reshaping a malformed frame would feed corrupt pixels to
    perception.
    """


class InvalidTimestampError(CaptureError):
    """A frame carried a timestamp that cannot describe a real capture.

    Covers NaN, positive and negative infinity, and negative monotonic
    readings, plus a non-positive frame id.

    NaN is the reason this is a hard error rather than a clamp. Every
    comparison against NaN is False, so a NaN timestamp passes an ordinary
    monotonicity check unchallenged and then silently corrupts every age,
    staleness, and frame-rate calculation derived from it.
    """


class NonMonotonicCaptureError(CaptureError):
    """The injected clock moved backwards between two captures.

    Frame ordering is load-bearing for staleness checks and for expected-vs-
    observed reasoning, so a clock regression is surfaced instead of being
    clamped.
    """


class CaptureFailureThresholdExceeded(CaptureError):
    """Consecutive capture failures reached the configured limit.

    The source latches into a failed state and refuses further captures until
    :meth:`~mining_automation.capture.source.CaptureSource.reset_failures` is
    called explicitly. This is the guard against a retry loop that spins
    forever while the surface is gone.
    """

    def __init__(self, consecutive_failures: int, limit: int, last_error: BaseException | None):
        super().__init__(
            f"capture failed {consecutive_failures} consecutive times "
            f"(limit {limit}); last error: {last_error!r}"
        )
        self.consecutive_failures = consecutive_failures
        self.limit = limit
        self.last_error = last_error
