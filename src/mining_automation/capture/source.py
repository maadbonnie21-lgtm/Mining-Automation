"""The consumer-facing capture boundary.

:class:`CaptureSource` wraps a platform backend and owns the guarantees the rest
of the application relies on:

* **Identity.** Frame ids are assigned here, strictly increasing, one per
  successful capture. A backend cannot duplicate or reorder them.
* **Time.** Timestamps come from one injected clock. A clock regression is an
  error, not something to clamp.
* **Validation.** Malformed frames are rejected before any consumer sees them.
* **Explicit failure.** A failed capture raises. It never yields the previous
  frame, and never a partial one.
* **Bounded failure.** Consecutive failures latch the source into a failed
  state so a caller cannot spin indefinitely against a dead surface.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any, Final, Self

from ..diagnostics import DiagnosticEvent, EventSink
from .backend import CaptureBackend, MonotonicClock, SystemMonotonicClock
from .errors import (
    CaptureBackendError,
    CaptureClosedError,
    CaptureError,
    CaptureFailureThresholdExceeded,
    NonMonotonicCaptureError,
)
from .frame import Frame, validate_identity

__all__ = ["DEFAULT_MAX_CONSECUTIVE_FAILURES", "CaptureSource", "CaptureStats"]

#: Chosen so a genuinely transient hiccup (one alt-tab, one redraw stall) does
#: not halt a session, while a persistent fault surfaces in well under a second
#: of polling instead of looping unnoticed.
DEFAULT_MAX_CONSECUTIVE_FAILURES: Final[int] = 5

_EVENT_OPENED: Final[str] = "capture.opened"
_EVENT_CLOSED: Final[str] = "capture.closed"
_EVENT_SUCCESS: Final[str] = "capture.frame"
_EVENT_FAILED: Final[str] = "capture.failed"
_EVENT_RETRY: Final[str] = "capture.retry"
_EVENT_THRESHOLD: Final[str] = "capture.failure_threshold_exceeded"
_EVENT_RESET: Final[str] = "capture.failures_reset"


class CaptureStats:
    """Running counters for diagnostics and for tests to assert against."""

    __slots__ = ("consecutive_failures", "failures", "frames", "retries", "sink_errors")

    def __init__(self) -> None:
        self.frames: int = 0
        self.failures: int = 0
        self.consecutive_failures: int = 0
        self.retries: int = 0
        #: Diagnostic sink failures. Swallowed by design, counted so they are
        #: not invisible -- a broken sink cannot report itself.
        self.sink_errors: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "frames": self.frames,
            "failures": self.failures,
            "consecutive_failures": self.consecutive_failures,
            "retries": self.retries,
            "sink_errors": self.sink_errors,
        }


class CaptureSource:
    """Wraps a :class:`CaptureBackend` and produces validated, owned frames.

    Args:
        backend: Platform implementation.
        clock: Monotonic time source. Injected so tests are deterministic.
        sink: Optional diagnostics sink.
        max_consecutive_failures: Consecutive failures tolerated before the
            source latches. Must be at least 1.
        retry_attempts: Extra attempts per :meth:`capture` call, on top of the
            first. **Defaults to 0 deliberately** -- see the note below.
        success_event_interval: Emit a success event every Nth frame. 0 disables
            success events entirely. Per-frame events at capture rate would
            drown the log, so this is sampled while failures are never sampled.

    Note:
        Retries default to off. A retry loop inside the capture layer hides how
        bad the surface actually is from the layer that has to decide about
        recovery. When retries are enabled each one emits its own event, counts
        toward the consecutive-failure total, and can therefore still trip the
        threshold -- a retry can never mask a persistent fault.
    """

    __slots__ = (
        "_backend",
        "_clock",
        "_is_open",
        "_last_close_error",
        "_last_sink_error",
        "_last_timestamp_s",
        "_latched_error",
        "_max_consecutive_failures",
        "_next_frame_id",
        "_retry_attempts",
        "_sink",
        "_stats",
        "_success_event_interval",
    )

    def __init__(
        self,
        backend: CaptureBackend,
        *,
        clock: MonotonicClock | None = None,
        sink: EventSink | None = None,
        max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES,
        retry_attempts: int = 0,
        success_event_interval: int = 0,
    ) -> None:
        if max_consecutive_failures < 1:
            raise ValueError("max_consecutive_failures must be >= 1")
        if retry_attempts < 0:
            raise ValueError("retry_attempts cannot be negative")
        if success_event_interval < 0:
            raise ValueError("success_event_interval cannot be negative")

        self._backend = backend
        self._clock: MonotonicClock = clock if clock is not None else SystemMonotonicClock()
        self._sink = sink
        self._max_consecutive_failures = max_consecutive_failures
        self._retry_attempts = retry_attempts
        self._success_event_interval = success_event_interval

        self._next_frame_id = 1
        self._last_timestamp_s: float | None = None
        self._is_open = False
        self._latched_error: CaptureFailureThresholdExceeded | None = None
        self._last_sink_error: BaseException | None = None
        self._last_close_error: CaptureError | None = None
        self._stats = CaptureStats()

    # -- introspection -----------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def is_latched(self) -> bool:
        """True when consecutive failures exceeded the limit."""
        return self._latched_error is not None

    @property
    def stats(self) -> CaptureStats:
        return self._stats

    @property
    def last_sink_error(self) -> BaseException | None:
        """Most recent diagnostic sink failure, or None.

        Sink failures are swallowed so they cannot alter capture behaviour;
        this is how a caller inspects them.
        """
        return self._last_sink_error

    @property
    def last_close_error(self) -> CaptureError | None:
        """Cleanup failure suppressed by ``__exit__`` to preserve a real error."""
        return self._last_close_error

    @property
    def backend_name(self) -> str:
        return self._backend.name

    # -- lifecycle ---------------------------------------------------------
    def open(self) -> None:
        """Open the backend. Idempotent."""
        if self._is_open:
            return
        try:
            self._backend.open()
        except CaptureError:
            raise
        except Exception as exc:
            raise CaptureBackendError(f"backend {self._backend.name!r} failed to open") from exc
        self._is_open = True
        self._emit(_EVENT_OPENED, f"capture opened via {self._backend.name}")

    def close(self) -> None:
        """Close the backend.

        Idempotent, and safe to call after a capture failure. The source is
        marked closed regardless of whether the backend teardown succeeds, so a
        failing backend cannot leave the source stuck half-open.

        A cleanup failure is surfaced, not swallowed: a leaked handle or an
        unreleased device is exactly the kind of fault that shows up later as an
        unexplained capture failure. Unrecognised backend exceptions are
        normalised to :class:`CaptureBackendError` with the original preserved
        as ``__cause__``.

        :meth:`__exit__` applies one exception to this: if an error is already
        propagating out of the ``with`` block, that error wins and the cleanup
        failure is recorded rather than raised.

        Raises:
            CaptureError: the backend failed to release its resources.
        """
        if not self._is_open:
            return
        self._is_open = False
        try:
            self._backend.close()
        except CaptureError as exc:
            self._emit(_EVENT_CLOSED, "backend raised during close", error=repr(exc))
            raise
        except Exception as exc:
            self._emit(_EVENT_CLOSED, "backend raised during close", error=repr(exc))
            raise CaptureBackendError(
                f"backend {self._backend.name!r} failed to close"
            ) from exc
        self._emit(_EVENT_CLOSED, f"capture closed via {self._backend.name}", **self._stats.as_dict())

    def __enter__(self) -> Self:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close on the way out.

        On a normal exit a cleanup failure propagates, so a leaked resource is
        not hidden by a successful-looking block.

        When the block is already unwinding, the original exception wins. That
        error is what the caller needs to diagnose; replacing it with a teardown
        failure would discard the actual cause. The cleanup failure is still
        recorded in diagnostics and kept on :attr:`last_close_error`.
        """
        try:
            self.close()
        except CaptureError as close_error:
            self._last_close_error = close_error
            if exc_type is None:
                raise

    def reset_failures(self) -> None:
        """Clear a latched failure state.

        Deliberately explicit: recovery policy decides when the surface is worth
        trusting again, not the capture layer.
        """
        if self._latched_error is None and self._stats.consecutive_failures == 0:
            return
        self._latched_error = None
        self._stats.consecutive_failures = 0
        self._emit(_EVENT_RESET, "capture failure state cleared")

    # -- capture -----------------------------------------------------------
    def capture(self) -> Frame:
        """Return the next validated frame.

        Raises:
            CaptureClosedError: the source is not open.
            CaptureFailureThresholdExceeded: too many consecutive failures; the
                source is latched until :meth:`reset_failures`.
            CaptureError: any other capture failure, always a subclass so
                callers can branch on cause.
        """
        if self._latched_error is not None:
            raise self._latched_error
        if not self._is_open:
            raise CaptureClosedError("capture source is not open")

        attempts = self._retry_attempts + 1
        last_error: CaptureError | None = None

        for attempt in range(attempts):
            try:
                frame = self._attempt_capture()
            except CaptureError as exc:
                last_error = exc
                self._record_failure(exc, attempt=attempt)
                if attempt + 1 < attempts:
                    self._stats.retries += 1
                    self._emit(
                        _EVENT_RETRY,
                        f"retrying capture after {type(exc).__name__}",
                        attempt=attempt + 1,
                        remaining=attempts - attempt - 1,
                    )
                continue
            self._record_success(frame)
            return frame

        if last_error is None:  # pragma: no cover - loop cannot exit without an error
            raise CaptureBackendError("capture loop exited without a frame or an error")
        raise last_error

    def _attempt_capture(self) -> Frame:
        try:
            raw = self._backend.grab()
        except CaptureError:
            # The backend already speaks the taxonomy (CaptureUnavailableError,
            # CaptureTimeoutError, ...). Preserve the specific type so recovery
            # policy can distinguish transient from terminal.
            raise
        except Exception as exc:
            raise CaptureBackendError(f"backend {self._backend.name!r} grab failed") from exc

        timestamp = self._clock.monotonic_s()
        # Order matters: validate finiteness first. Every comparison against NaN
        # is False, so a NaN reading would sail through the monotonic check
        # below and become a permanent, unnoticed corruption of the floor.
        validate_identity(self._next_frame_id, timestamp)
        if self._last_timestamp_s is not None and timestamp < self._last_timestamp_s:
            raise NonMonotonicCaptureError(
                f"clock moved backwards: {timestamp} < {self._last_timestamp_s}"
            )

        # Frame.from_raw validates geometry and payload, and raises
        # InvalidFrameError (a CaptureError) on any mismatch.
        frame = Frame.from_raw(
            raw,
            frame_id=self._next_frame_id,
            captured_monotonic_s=timestamp,
        )
        # Only advance identity and time once the frame is known good, so a
        # rejected frame never consumes an id or moves the monotonic floor.
        self._next_frame_id += 1
        self._last_timestamp_s = timestamp
        return frame

    # -- bookkeeping -------------------------------------------------------
    def _record_success(self, frame: Frame) -> None:
        self._stats.frames += 1
        self._stats.consecutive_failures = 0
        interval = self._success_event_interval
        if interval and self._stats.frames % interval == 0:
            self._emit(
                _EVENT_SUCCESS,
                f"captured frame {frame.frame_id}",
                frame_id=frame.frame_id,
                width=frame.width,
                height=frame.height,
                pixel_format=frame.pixel_format.name,
                captured_monotonic_s=frame.captured_monotonic_s,
            )

    def _record_failure(self, exc: CaptureError, *, attempt: int) -> None:
        self._stats.failures += 1
        self._stats.consecutive_failures += 1
        self._emit(
            _EVENT_FAILED,
            f"capture failed: {type(exc).__name__}",
            error_type=type(exc).__name__,
            error=str(exc),
            attempt=attempt,
            consecutive_failures=self._stats.consecutive_failures,
        )
        if self._stats.consecutive_failures >= self._max_consecutive_failures:
            latched = CaptureFailureThresholdExceeded(
                consecutive_failures=self._stats.consecutive_failures,
                limit=self._max_consecutive_failures,
                last_error=exc,
            )
            self._latched_error = latched
            self._emit(
                _EVENT_THRESHOLD,
                str(latched),
                consecutive_failures=self._stats.consecutive_failures,
                limit=self._max_consecutive_failures,
                last_error_type=type(exc).__name__,
            )
            raise latched from exc

    def _emit(self, event_type: str, message: str, **data: Any) -> None:
        """Emit a diagnostic event. Never raises.

        Diagnostics are observation, not control flow. A sink that fails must
        not change what capture does: it must not prevent ``open`` from
        succeeding, withhold a valid frame, replace the capture error a caller
        needs to see, stop the failure threshold from latching, or break
        ``close``. So every sink failure is swallowed here.

        Swallowed is not invisible. Failures increment
        :attr:`CaptureStats.sink_errors` and the most recent one is kept on
        :attr:`last_sink_error`, because the one thing we cannot do about a
        broken sink is report it through that sink.
        """
        if self._sink is None:
            return
        try:
            self._sink.emit(
                DiagnosticEvent(
                    event_type=event_type,
                    message=message,
                    data={"backend": self._backend.name, **data},
                )
            )
        except Exception as exc:  # noqa: BLE001 - see docstring
            self._stats.sink_errors += 1
            self._last_sink_error = exc
