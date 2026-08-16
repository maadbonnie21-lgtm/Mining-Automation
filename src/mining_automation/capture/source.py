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
    """Wraps a :class:`CaptureBackend` and produces validated, owned frames."""

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

    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def is_latched(self) -> bool:
        return self._latched_error is not None

    @property
    def stats(self) -> CaptureStats:
        return self._stats

    @property
    def last_sink_error(self) -> BaseException | None:
        return self._last_sink_error

    @property
    def last_close_error(self) -> CaptureError | None:
        return self._last_close_error

    @property
    def backend_name(self) -> str:
        return self._backend.name

    def open(self) -> None:
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
        try:
            self.close()
        except CaptureError as close_error:
            self._last_close_error = close_error
            if exc_type is None:
                raise

    def reset_failures(self) -> None:
        if self._latched_error is None and self._stats.consecutive_failures == 0:
            return
        self._latched_error = None
        self._stats.consecutive_failures = 0
        self._emit(_EVENT_RESET, "capture failure state cleared")

    def capture(self) -> Frame:
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

        if last_error is None:
            raise CaptureBackendError("capture loop exited without a frame or an error")
        raise last_error

    def _attempt_capture(self) -> Frame:
        try:
            raw = self._backend.grab()
        except CaptureError:
            raise
        except Exception as exc:
            raise CaptureBackendError(f"backend {self._backend.name!r} grab failed") from exc

        timestamp = self._clock.monotonic_s()
        validate_identity(self._next_frame_id, timestamp)
        if self._last_timestamp_s is not None and timestamp < self._last_timestamp_s:
            raise NonMonotonicCaptureError(
                f"clock moved backwards: {timestamp} < {self._last_timestamp_s}"
            )

        frame = Frame.from_raw(
            raw,
            frame_id=self._next_frame_id,
            captured_monotonic_s=timestamp,
        )
        self._next_frame_id += 1
        self._last_timestamp_s = timestamp
        return frame

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
        except Exception as exc:
            self._stats.sink_errors += 1
            self._last_sink_error = exc
