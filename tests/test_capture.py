from __future__ import annotations

from itertools import pairwise

import pytest

from mining_automation.capture import (
    DEFAULT_MAX_CONSECUTIVE_FAILURES,
    CaptureBackend,
    CaptureBackendError,
    CaptureClosedError,
    CaptureError,
    CaptureFailureThresholdExceeded,
    CaptureSource,
    CaptureTimeoutError,
    CaptureUnavailableError,
    Frame,
    InvalidFrameError,
    InvalidTimestampError,
    NonMonotonicCaptureError,
    PixelFormat,
    RawFrame,
)
from mining_automation.capture.testing import (
    BrokenClock,
    FailingEventSink,
    FakeCaptureBackend,
    FlakyEventSink,
    ManualClock,
    solid_payload,
)
from mining_automation.contracts import FrameRef
from mining_automation.diagnostics import InMemoryEventSink


def make_raw(width: int = 4, height: int = 2, value: int = 0x10) -> RawFrame:
    return RawFrame(payload=solid_payload(width, height, value), width=width, height=height)


def event_types(sink: InMemoryEventSink) -> list[str]:
    return [event.event_type for event in sink.events]


# ---------------------------------------------------------------------------
# protocol conformance
# ---------------------------------------------------------------------------


def test_fake_backend_satisfies_capture_backend_protocol() -> None:
    assert isinstance(FakeCaptureBackend(), CaptureBackend)


def test_manual_clock_satisfies_monotonic_clock_protocol() -> None:
    from mining_automation.capture import MonotonicClock

    assert isinstance(ManualClock(), MonotonicClock)


# ---------------------------------------------------------------------------
# frame construction and validation
# ---------------------------------------------------------------------------


def test_frame_from_raw_populates_shared_contract() -> None:
    frame = Frame.from_raw(make_raw(), frame_id=7, captured_monotonic_s=1.5)
    assert isinstance(frame.ref, FrameRef)
    assert (frame.frame_id, frame.captured_monotonic_s) == (7, 1.5)
    assert (frame.width, frame.height) == (4, 2)
    assert frame.size_bytes == 4 * 2 * 4
    assert frame.pixel_format is PixelFormat.BGRA8888


@pytest.mark.parametrize("width,height", [(0, 2), (4, 0), (-1, 2), (4, -3)])
def test_non_positive_dimensions_are_rejected(width: int, height: int) -> None:
    raw = RawFrame(payload=b"", width=width, height=height)
    with pytest.raises(InvalidFrameError, match="non-positive"):
        Frame.from_raw(raw, frame_id=1, captured_monotonic_s=0.0)


def test_truncated_payload_is_rejected_not_padded() -> None:
    raw = RawFrame(payload=b"\x00" * 10, width=4, height=2)  # needs 32
    with pytest.raises(InvalidFrameError, match="payload size 10 bytes"):
        Frame.from_raw(raw, frame_id=1, captured_monotonic_s=0.0)


def test_oversized_payload_is_rejected() -> None:
    raw = RawFrame(payload=b"\x00" * 64, width=4, height=2)  # needs 32
    with pytest.raises(InvalidFrameError, match="payload size 64 bytes"):
        Frame.from_raw(raw, frame_id=1, captured_monotonic_s=0.0)


def test_empty_payload_is_rejected() -> None:
    raw = RawFrame(payload=b"", width=4, height=2)
    with pytest.raises(InvalidFrameError):
        Frame.from_raw(raw, frame_id=1, captured_monotonic_s=0.0)


def test_implausible_geometry_is_rejected_before_allocation() -> None:
    raw = RawFrame(payload=b"\x00", width=100_000, height=100_000)
    with pytest.raises(InvalidFrameError, match="byte guard"):
        Frame.from_raw(raw, frame_id=1, captured_monotonic_s=0.0)


@pytest.mark.parametrize(
    "fmt,expected",
    [
        (PixelFormat.GRAY8, 1),
        (PixelFormat.RGB888, 3),
        (PixelFormat.BGR888, 3),
        (PixelFormat.RGBA8888, 4),
        (PixelFormat.BGRA8888, 4),
    ],
)
def test_pixel_format_geometry(fmt: PixelFormat, expected: int) -> None:
    assert fmt.bytes_per_pixel == expected
    raw = RawFrame(payload=solid_payload(3, 3, 1, fmt), width=3, height=3, pixel_format=fmt)
    frame = Frame.from_raw(raw, frame_id=1, captured_monotonic_s=0.0)
    assert frame.size_bytes == 9 * expected


def test_frame_age_is_floored_at_zero() -> None:
    frame = Frame.from_raw(make_raw(), frame_id=1, captured_monotonic_s=10.0)
    assert frame.age_s(12.5) == pytest.approx(2.5)
    assert frame.age_s(9.0) == 0.0


# ---------------------------------------------------------------------------
# payload ownership
# ---------------------------------------------------------------------------


def test_frame_payload_is_immutable_bytes() -> None:
    raw = RawFrame(payload=bytearray(solid_payload(4, 2)), width=4, height=2)
    frame = Frame.from_raw(raw, frame_id=1, captured_monotonic_s=0.0)
    assert isinstance(frame.payload, bytes)


def test_frame_survives_backend_reusing_its_buffer() -> None:
    """The ownership guarantee: a retained frame must not change underneath us."""
    backend = FakeCaptureBackend([make_raw(value=0x11)], reuse_buffer=True)
    with CaptureSource(backend, clock=ManualClock()) as source:
        first = source.capture()
        snapshot = first.payload
        backend.scribble_buffer(0xFF)
        assert first.payload == snapshot
        assert set(first.payload) == {0x11}


def test_successive_frames_do_not_alias_each_other() -> None:
    backend = FakeCaptureBackend(
        [make_raw(value=0x01), make_raw(value=0x02)], reuse_buffer=True
    )
    with CaptureSource(backend, clock=ManualClock()) as source:
        first = source.capture()
        second = source.capture()
    assert set(first.payload) == {0x01}
    assert set(second.payload) == {0x02}


# ---------------------------------------------------------------------------
# identity and time
# ---------------------------------------------------------------------------


def test_frame_ids_are_unique_and_strictly_increasing() -> None:
    backend = FakeCaptureBackend([make_raw()])
    with CaptureSource(backend, clock=ManualClock()) as source:
        ids = [source.capture().frame_id for _ in range(10)]
    assert ids == list(range(1, 11))
    assert len(set(ids)) == 10


def test_timestamps_are_monotonic_and_come_from_the_injected_clock() -> None:
    clock = ManualClock(start_s=100.0)
    backend = FakeCaptureBackend([make_raw()])
    stamps: list[float] = []
    with CaptureSource(backend, clock=clock) as source:
        for _ in range(5):
            stamps.append(source.capture().captured_monotonic_s)
            clock.advance(0.25)
    assert stamps == [100.0, 100.25, 100.5, 100.75, 101.0]
    assert all(b > a for a, b in pairwise(stamps))


def test_equal_timestamps_are_allowed_but_backwards_is_an_error() -> None:
    clock = ManualClock(start_s=5.0)
    with CaptureSource(FakeCaptureBackend([make_raw()]), clock=clock) as source:
        source.capture()
        source.capture()  # same instant is tolerated
        clock.set(4.0)
        with pytest.raises(NonMonotonicCaptureError, match="moved backwards"):
            source.capture()


def test_rejected_frame_does_not_consume_a_frame_id() -> None:
    backend = FakeCaptureBackend(
        [RawFrame(payload=b"\x00" * 4, width=4, height=2), make_raw()]
    )
    with CaptureSource(backend, clock=ManualClock()) as source:
        with pytest.raises(InvalidFrameError):
            source.capture()
        assert source.capture().frame_id == 1


def test_rejected_frame_does_not_move_the_monotonic_floor() -> None:
    clock = ManualClock(start_s=10.0)
    backend = FakeCaptureBackend(
        [make_raw(), RawFrame(payload=b"\x00", width=4, height=2), make_raw()]
    )
    with CaptureSource(backend, clock=clock) as source:
        source.capture()
        clock.set(20.0)
        with pytest.raises(InvalidFrameError):
            source.capture()
        clock.set(15.0)  # still ahead of the last *successful* capture
        assert source.capture().captured_monotonic_s == 15.0


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------


def test_capture_before_open_raises() -> None:
    source = CaptureSource(FakeCaptureBackend(), clock=ManualClock())
    with pytest.raises(CaptureClosedError):
        source.capture()


def test_capture_after_close_raises() -> None:
    source = CaptureSource(FakeCaptureBackend(), clock=ManualClock())
    source.open()
    source.capture()
    source.close()
    with pytest.raises(CaptureClosedError):
        source.capture()


def test_open_and_close_are_idempotent() -> None:
    backend = FakeCaptureBackend()
    source = CaptureSource(backend, clock=ManualClock())
    source.open()
    source.open()
    source.close()
    source.close()
    assert backend.open_calls == 1
    assert backend.close_calls == 1


def test_context_manager_closes_on_exception() -> None:
    backend = FakeCaptureBackend()
    with pytest.raises(RuntimeError), CaptureSource(backend, clock=ManualClock()):
        raise RuntimeError("consumer blew up")
    assert backend.close_calls == 1
    assert backend.is_open is False


def test_backend_open_failure_is_normalised() -> None:
    class BadOpen:
        name = "bad-open"

        def open(self) -> None:
            raise OSError("no display")

        def grab(self) -> RawFrame:
            raise AssertionError("unreachable")

        def close(self) -> None:
            return None

    source = CaptureSource(BadOpen(), clock=ManualClock())
    with pytest.raises(CaptureBackendError, match="failed to open"):
        source.open()
    assert source.is_open is False


def test_close_error_is_reported_and_diagnosed() -> None:
    """Superseded by review finding P3: a cleanup failure must not be silent."""

    class BadClose(FakeCaptureBackend):
        def close(self) -> None:
            self.close_calls += 1
            self.is_open = False
            raise OSError("handle already gone")

    sink = InMemoryEventSink()
    source = CaptureSource(BadClose(), clock=ManualClock(), sink=sink)
    source.open()
    with pytest.raises(CaptureBackendError, match="failed to close"):
        source.close()
    assert source.is_open is False
    assert "capture.closed" in event_types(sink)


# ---------------------------------------------------------------------------
# failure paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        CaptureUnavailableError("window minimised"),
        CaptureTimeoutError("no frame in time"),
    ],
)
def test_known_capture_errors_propagate_unchanged(error: Exception) -> None:
    backend = FakeCaptureBackend([error, make_raw()])
    with CaptureSource(backend, clock=ManualClock()) as source, pytest.raises(type(error)):
        source.capture()


def test_unexpected_backend_exception_is_wrapped_and_preserves_cause() -> None:
    backend = FakeCaptureBackend([ValueError("driver exploded")])
    with (
        CaptureSource(backend, clock=ManualClock()) as source,
        pytest.raises(CaptureBackendError) as info,
    ):
        source.capture()
    assert isinstance(info.value.__cause__, ValueError)


def test_failure_never_yields_a_stale_frame() -> None:
    backend = FakeCaptureBackend([make_raw(value=0x22), CaptureUnavailableError("gone")])
    with CaptureSource(backend, clock=ManualClock()) as source:
        good = source.capture()
        with pytest.raises(CaptureUnavailableError):
            source.capture()
        assert good.frame_id == 1
        assert source.stats.frames == 1


def test_consecutive_failures_latch_the_source() -> None:
    backend = FakeCaptureBackend([CaptureUnavailableError("gone")])
    source = CaptureSource(backend, clock=ManualClock(), max_consecutive_failures=3)
    source.open()

    for _ in range(2):
        with pytest.raises(CaptureUnavailableError):
            source.capture()
    assert source.is_latched is False

    with pytest.raises(CaptureFailureThresholdExceeded) as info:
        source.capture()
    assert info.value.consecutive_failures == 3
    assert info.value.limit == 3
    assert isinstance(info.value.last_error, CaptureUnavailableError)
    assert source.is_latched is True


def test_latched_source_refuses_further_captures_without_calling_backend() -> None:
    backend = FakeCaptureBackend([CaptureUnavailableError("gone")])
    source = CaptureSource(backend, clock=ManualClock(), max_consecutive_failures=1)
    source.open()
    with pytest.raises(CaptureFailureThresholdExceeded):
        source.capture()

    grabs_at_latch = backend.grab_calls
    for _ in range(5):
        with pytest.raises(CaptureFailureThresholdExceeded):
            source.capture()
    assert backend.grab_calls == grabs_at_latch, "latched source must not hammer the backend"


def test_success_resets_the_consecutive_counter() -> None:
    backend = FakeCaptureBackend(
        [CaptureUnavailableError("blip"), make_raw(), CaptureUnavailableError("blip")]
    )
    source = CaptureSource(backend, clock=ManualClock(), max_consecutive_failures=2)
    source.open()

    with pytest.raises(CaptureUnavailableError):
        source.capture()
    source.capture()
    assert source.stats.consecutive_failures == 0

    with pytest.raises(CaptureUnavailableError):
        source.capture()
    assert source.is_latched is False
    assert source.stats.failures == 2


def test_reset_failures_clears_latch_and_is_explicit() -> None:
    backend = FakeCaptureBackend([CaptureUnavailableError("gone"), make_raw()])
    source = CaptureSource(backend, clock=ManualClock(), max_consecutive_failures=1)
    source.open()
    with pytest.raises(CaptureFailureThresholdExceeded):
        source.capture()

    source.reset_failures()
    assert source.is_latched is False
    assert source.capture().frame_id == 1


def test_invalid_frames_also_count_toward_the_threshold() -> None:
    bad = RawFrame(payload=b"\x00", width=4, height=2)
    source = CaptureSource(
        FakeCaptureBackend([bad]), clock=ManualClock(), max_consecutive_failures=2
    )
    source.open()
    with pytest.raises(InvalidFrameError):
        source.capture()
    with pytest.raises(CaptureFailureThresholdExceeded):
        source.capture()


# ---------------------------------------------------------------------------
# retry policy
# ---------------------------------------------------------------------------


def test_retries_are_off_by_default() -> None:
    backend = FakeCaptureBackend([CaptureUnavailableError("gone"), make_raw()])
    with (
        CaptureSource(backend, clock=ManualClock()) as source,
        pytest.raises(CaptureUnavailableError),
    ):
        source.capture()
    assert backend.grab_calls == 1
    assert source.stats.retries == 0


def test_retry_recovers_from_a_transient_failure() -> None:
    backend = FakeCaptureBackend([CaptureUnavailableError("blip"), make_raw()])
    with CaptureSource(backend, clock=ManualClock(), retry_attempts=1) as source:
        frame = source.capture()
    assert frame.frame_id == 1
    assert source.stats.retries == 1
    assert source.stats.failures == 1


def test_retries_cannot_hide_a_persistent_failure() -> None:
    """A retry loop must still trip the threshold rather than spinning."""
    backend = FakeCaptureBackend([CaptureUnavailableError("gone")])
    source = CaptureSource(
        backend,
        clock=ManualClock(),
        retry_attempts=10,
        max_consecutive_failures=3,
    )
    source.open()
    with pytest.raises(CaptureFailureThresholdExceeded):
        source.capture()
    assert backend.grab_calls == 3, "must stop at the threshold, not exhaust all retries"
    assert source.is_latched is True


def test_every_retry_is_visible_in_diagnostics() -> None:
    sink = InMemoryEventSink()
    backend = FakeCaptureBackend(
        [CaptureUnavailableError("a"), CaptureUnavailableError("b"), make_raw()]
    )
    with CaptureSource(backend, clock=ManualClock(), retry_attempts=2, sink=sink) as source:
        source.capture()
    assert event_types(sink).count("capture.failed") == 2
    assert event_types(sink).count("capture.retry") == 2


# ---------------------------------------------------------------------------
# construction validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_consecutive_failures": 0},
        {"max_consecutive_failures": -1},
        {"retry_attempts": -1},
        {"success_event_interval": -1},
    ],
)
def test_invalid_configuration_is_rejected(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        CaptureSource(FakeCaptureBackend(), clock=ManualClock(), **kwargs)


def test_default_threshold_is_applied() -> None:
    backend = FakeCaptureBackend([CaptureUnavailableError("gone")])
    source = CaptureSource(backend, clock=ManualClock())
    source.open()
    for _ in range(DEFAULT_MAX_CONSECUTIVE_FAILURES - 1):
        with pytest.raises(CaptureUnavailableError):
            source.capture()
    with pytest.raises(CaptureFailureThresholdExceeded):
        source.capture()


# ---------------------------------------------------------------------------
# diagnostics
# ---------------------------------------------------------------------------


def test_lifecycle_and_failure_events_are_emitted() -> None:
    sink = InMemoryEventSink()
    backend = FakeCaptureBackend([make_raw(), CaptureUnavailableError("gone")])
    source = CaptureSource(backend, clock=ManualClock(), sink=sink, max_consecutive_failures=1)
    source.open()
    source.capture()
    with pytest.raises(CaptureFailureThresholdExceeded):
        source.capture()
    source.reset_failures()
    source.close()

    types = event_types(sink)
    assert types == [
        "capture.opened",
        "capture.failed",
        "capture.failure_threshold_exceeded",
        "capture.failures_reset",
        "capture.closed",
    ]


def test_success_events_are_sampled_not_per_frame() -> None:
    sink = InMemoryEventSink()
    backend = FakeCaptureBackend([make_raw()])
    with CaptureSource(
        backend, clock=ManualClock(), sink=sink, success_event_interval=5
    ) as source:
        for _ in range(12):
            source.capture()
    assert event_types(sink).count("capture.frame") == 2


def test_success_events_disabled_by_default() -> None:
    sink = InMemoryEventSink()
    with CaptureSource(FakeCaptureBackend([make_raw()]), clock=ManualClock(), sink=sink) as src:
        for _ in range(5):
            src.capture()
    assert "capture.frame" not in event_types(sink)


def test_failure_events_carry_actionable_context() -> None:
    sink = InMemoryEventSink()
    backend = FakeCaptureBackend([CaptureUnavailableError("window minimised")])
    with (
        CaptureSource(backend, clock=ManualClock(), sink=sink) as source,
        pytest.raises(CaptureUnavailableError),
    ):
        source.capture()
    failure = next(e for e in sink.events if e.event_type == "capture.failed")
    assert failure.data["error_type"] == "CaptureUnavailableError"
    assert failure.data["consecutive_failures"] == 1
    assert failure.data["backend"] == "fake"
    assert "minimised" in str(failure.data["error"])


def test_source_works_without_a_sink() -> None:
    with CaptureSource(FakeCaptureBackend([make_raw()]), clock=ManualClock()) as source:
        assert source.capture().frame_id == 1


def test_stats_track_frames_and_failures() -> None:
    backend = FakeCaptureBackend([make_raw(), CaptureUnavailableError("x"), make_raw()])
    with CaptureSource(backend, clock=ManualClock()) as source:
        source.capture()
        with pytest.raises(CaptureUnavailableError):
            source.capture()
        source.capture()
        assert source.stats.as_dict() == {
            "frames": 2,
            "failures": 1,
            "consecutive_failures": 0,
            "retries": 0,
            "sink_errors": 0,
        }
        assert source.backend_name == "fake"


# ---------------------------------------------------------------------------
# consumer decoupling
# ---------------------------------------------------------------------------


def test_consumer_needs_only_the_interface() -> None:
    """A consumer typed against CaptureSource works with any backend."""

    def mean_first_byte(source: CaptureSource, samples: int) -> float:
        return sum(source.capture().payload[0] for _ in range(samples)) / samples

    with CaptureSource(FakeCaptureBackend([make_raw(value=8)]), clock=ManualClock()) as src:
        assert mean_first_byte(src, 4) == 8.0


# ---------------------------------------------------------------------------
# remaining edge coverage
# ---------------------------------------------------------------------------


def test_backend_open_may_raise_a_capture_error_directly() -> None:
    """A backend that already speaks the taxonomy is not re-wrapped."""

    class UnavailableOnOpen:
        name = "unavailable-open"

        def open(self) -> None:
            raise CaptureUnavailableError("display asleep")

        def grab(self) -> RawFrame:
            raise AssertionError("unreachable")

        def close(self) -> None:
            return None

    source = CaptureSource(UnavailableOnOpen(), clock=ManualClock())
    with pytest.raises(CaptureUnavailableError):
        source.open()
    assert source.is_open is False


def test_reset_failures_is_a_noop_when_healthy() -> None:
    sink = InMemoryEventSink()
    with CaptureSource(FakeCaptureBackend([make_raw()]), clock=ManualClock(), sink=sink) as src:
        src.capture()
        src.reset_failures()
    assert "capture.failures_reset" not in event_types(sink)


def test_system_clock_is_monotonic() -> None:
    from mining_automation.capture import SystemMonotonicClock

    clock = SystemMonotonicClock()
    assert clock.monotonic_s() <= clock.monotonic_s()


def test_fake_backend_rejects_an_empty_script() -> None:
    with pytest.raises(ValueError, match="at least one entry"):
        FakeCaptureBackend([])


def test_fake_backend_refuses_to_grab_while_closed() -> None:
    backend = FakeCaptureBackend([make_raw()])
    with pytest.raises(CaptureUnavailableError, match="not open"):
        backend.grab()


# ===========================================================================
# Review findings — regression coverage
# ===========================================================================

# --- P1: diagnostic sink isolation -----------------------------------------
# A failing EventSink must never change capture behaviour or lifecycle state.


def test_failing_sink_does_not_prevent_open() -> None:
    sink = FailingEventSink()
    source = CaptureSource(FakeCaptureBackend(), clock=ManualClock(), sink=sink)
    source.open()
    assert source.is_open is True
    assert sink.attempts == 1
    assert source.stats.sink_errors == 1


def test_failing_sink_does_not_withhold_a_valid_frame() -> None:
    sink = FailingEventSink()
    backend = FakeCaptureBackend([make_raw(value=0x33)])
    with CaptureSource(
        backend, clock=ManualClock(), sink=sink, success_event_interval=1
    ) as source:
        frame = source.capture()
    assert frame.frame_id == 1
    assert set(frame.payload) == {0x33}
    assert source.stats.frames == 1


def test_failing_sink_does_not_replace_the_original_capture_error() -> None:
    sink = FailingEventSink()
    backend = FakeCaptureBackend([CaptureUnavailableError("window minimised")])
    with (
        CaptureSource(backend, clock=ManualClock(), sink=sink) as source,
        pytest.raises(CaptureUnavailableError, match="minimised"),
    ):
        source.capture()


def test_failing_sink_does_not_prevent_failure_threshold_latching() -> None:
    """The dangerous case: the sink dies exactly at the threshold emission."""
    sink = FlakyEventSink(fail_on="capture.failure_threshold_exceeded")
    backend = FakeCaptureBackend([CaptureUnavailableError("gone")])
    source = CaptureSource(backend, clock=ManualClock(), sink=sink, max_consecutive_failures=2)
    source.open()

    with pytest.raises(CaptureUnavailableError):
        source.capture()
    with pytest.raises(CaptureFailureThresholdExceeded):
        source.capture()

    assert source.is_latched is True
    assert sink.failures == 1
    assert source.stats.sink_errors == 1


def test_failing_sink_does_not_prevent_failure_reset() -> None:
    sink = FailingEventSink()
    backend = FakeCaptureBackend([CaptureUnavailableError("gone"), make_raw()])
    source = CaptureSource(backend, clock=ManualClock(), sink=sink, max_consecutive_failures=1)
    source.open()
    with pytest.raises(CaptureFailureThresholdExceeded):
        source.capture()

    source.reset_failures()
    assert source.is_latched is False
    assert source.capture().frame_id == 1


def test_failing_sink_does_not_break_close() -> None:
    sink = FailingEventSink()
    backend = FakeCaptureBackend()
    source = CaptureSource(backend, clock=ManualClock(), sink=sink)
    source.open()
    source.close()
    assert source.is_open is False
    assert backend.close_calls == 1


def test_failing_sink_across_a_full_lifecycle_changes_nothing() -> None:
    sink = FailingEventSink()
    working = InMemoryEventSink()
    script: list[RawFrame | BaseException] = [
        make_raw(),
        CaptureUnavailableError("blip"),
        make_raw(),
    ]

    def run(s: object) -> dict[str, int]:
        backend = FakeCaptureBackend(list(script))
        src = CaptureSource(backend, clock=ManualClock(), sink=s)  # type: ignore[arg-type]
        src.open()
        src.capture()
        with pytest.raises(CaptureUnavailableError):
            src.capture()
        src.capture()
        src.close()
        stats = src.stats.as_dict()
        stats.pop("sink_errors")
        return stats

    assert run(sink) == run(working)


def test_sink_errors_are_counted_and_the_last_one_is_retained() -> None:
    boom = RuntimeError("sink exploded")
    sink = FailingEventSink(boom)
    source = CaptureSource(FakeCaptureBackend(), clock=ManualClock(), sink=sink)
    source.open()
    source.close()
    assert source.stats.sink_errors == 2
    assert source.last_sink_error is boom


# --- P2: validate payload size before copying -------------------------------


def test_oversized_payload_is_rejected_without_copying() -> None:
    """Validation must precede the copy, not follow it."""
    huge = bytearray(4 * 1024 * 1024)
    raw = RawFrame(payload=huge, width=4, height=2)  # needs 32 bytes
    copies: list[int] = []

    class TrackedBytes(bytearray):
        def __bytes__(self) -> bytes:  # pragma: no cover - must never run
            copies.append(len(self))
            return bytes(memoryview(self))

    raw = RawFrame(payload=TrackedBytes(huge), width=4, height=2)
    with pytest.raises(InvalidFrameError, match="payload size 4194304"):
        Frame.from_raw(raw, frame_id=1, captured_monotonic_s=0.0)
    assert copies == [], "payload must be rejected before any copy"


def test_memoryview_payload_of_correct_size_is_accepted() -> None:
    buf = bytearray(solid_payload(4, 2, 0x44))
    raw = RawFrame(payload=memoryview(buf), width=4, height=2)
    frame = Frame.from_raw(raw, frame_id=1, captured_monotonic_s=0.0)
    assert frame.size_bytes == 32
    assert set(frame.payload) == {0x44}


def test_memoryview_size_uses_nbytes_not_element_count() -> None:
    """A non-byte memoryview reports elements from len(), bytes from nbytes.

    An 8-element 'I' array is 32 bytes -- exactly the expected size here. Using
    len() would see 8 and wrongly reject it.
    """
    import array

    arr = array.array("I", [0x01020304] * 8)
    view = memoryview(arr)
    assert len(view) == 8
    assert view.nbytes == 32

    raw = RawFrame(payload=view, width=4, height=2)
    frame = Frame.from_raw(raw, frame_id=1, captured_monotonic_s=0.0)
    assert frame.size_bytes == 32


def test_memoryview_with_wrong_byte_count_is_rejected() -> None:
    import array

    arr = array.array("I", [0] * 4)  # 16 bytes, needs 32
    raw = RawFrame(payload=memoryview(arr), width=4, height=2)
    with pytest.raises(InvalidFrameError, match="payload size 16 bytes"):
        Frame.from_raw(raw, frame_id=1, captured_monotonic_s=0.0)


def test_undersized_payload_still_rejected_before_copy() -> None:
    raw = RawFrame(payload=bytearray(10), width=4, height=2)
    with pytest.raises(InvalidFrameError, match="payload size 10 bytes"):
        Frame.from_raw(raw, frame_id=1, captured_monotonic_s=0.0)


# --- P3: cleanup failure behaviour ------------------------------------------


class FailingCloseBackend(FakeCaptureBackend):
    def close(self) -> None:
        self.close_calls += 1
        self.is_open = False
        raise OSError("handle already released")


def test_direct_close_failure_is_normalised_and_raised() -> None:
    source = CaptureSource(FailingCloseBackend(), clock=ManualClock())
    source.open()
    with pytest.raises(CaptureBackendError, match="failed to close") as info:
        source.close()
    assert isinstance(info.value.__cause__, OSError)
    assert source.is_open is False


def test_normal_context_exit_surfaces_a_cleanup_failure() -> None:
    backend = FailingCloseBackend()
    with (
        pytest.raises(CaptureBackendError, match="failed to close"),
        CaptureSource(backend, clock=ManualClock()) as source,
    ):
        source.capture()
    assert backend.close_calls == 1


def test_in_flight_exception_wins_over_a_cleanup_failure() -> None:
    """The original error is what the caller needs; teardown must not mask it."""
    backend = FailingCloseBackend()
    source = CaptureSource(backend, clock=ManualClock())
    with pytest.raises(ValueError, match="consumer blew up"), source:
        raise ValueError("consumer blew up")

    assert backend.close_calls == 1
    assert isinstance(source.last_close_error, CaptureBackendError)


def test_capture_error_in_flight_also_wins_over_cleanup_failure() -> None:
    backend = FailingCloseBackend([CaptureUnavailableError("surface gone")])
    source = CaptureSource(backend, clock=ManualClock())
    with pytest.raises(CaptureUnavailableError, match="surface gone"), source:
        source.capture()
    assert isinstance(source.last_close_error, CaptureBackendError)


def test_close_failure_that_is_already_a_capture_error_is_not_rewrapped() -> None:
    class UnavailableOnClose(FakeCaptureBackend):
        def close(self) -> None:
            self.close_calls += 1
            self.is_open = False
            raise CaptureUnavailableError("device detached during close")

    source = CaptureSource(UnavailableOnClose(), clock=ManualClock())
    source.open()
    with pytest.raises(CaptureUnavailableError, match="detached"):
        source.close()


def test_close_marks_source_closed_even_when_teardown_fails() -> None:
    source = CaptureSource(FailingCloseBackend(), clock=ManualClock())
    source.open()
    with pytest.raises(CaptureBackendError):
        source.close()
    source.close()  # idempotent: already closed, no second attempt, no raise


# --- P4: invalid and non-finite timestamps ----------------------------------


@pytest.mark.parametrize(
    "value,match",
    [
        (float("nan"), "NaN"),
        (float("inf"), "infinite"),
        (float("-inf"), "infinite"),
        (-0.001, "negative"),
        (-1000.0, "negative"),
    ],
)
def test_invalid_timestamps_are_rejected_by_frame(value: float, match: str) -> None:
    with pytest.raises(InvalidTimestampError, match=match):
        Frame.from_raw(make_raw(), frame_id=1, captured_monotonic_s=value)


@pytest.mark.parametrize("frame_id", [0, -1, -99])
def test_non_positive_frame_ids_are_rejected(frame_id: int) -> None:
    with pytest.raises(InvalidTimestampError, match="frame_id"):
        Frame.from_raw(make_raw(), frame_id=frame_id, captured_monotonic_s=0.0)


def test_zero_timestamp_is_valid() -> None:
    frame = Frame.from_raw(make_raw(), frame_id=1, captured_monotonic_s=0.0)
    assert frame.captured_monotonic_s == 0.0


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), -5.0])
def test_source_rejects_an_invalid_clock_reading(bad: float) -> None:
    source = CaptureSource(
        FakeCaptureBackend([make_raw()]), clock=BrokenClock([bad]), max_consecutive_failures=99
    )
    source.open()
    with pytest.raises(InvalidTimestampError):
        source.capture()


def test_nan_timestamp_does_not_consume_a_frame_id_or_move_the_floor() -> None:
    """NaN is the case an ordinary monotonic check cannot catch."""
    clock = BrokenClock([10.0, float("nan"), 11.0])
    backend = FakeCaptureBackend([make_raw()])
    source = CaptureSource(backend, clock=clock, max_consecutive_failures=99)
    source.open()

    first = source.capture()
    assert (first.frame_id, first.captured_monotonic_s) == (1, 10.0)

    with pytest.raises(InvalidTimestampError, match="NaN"):
        source.capture()

    second = source.capture()
    assert second.frame_id == 2, "rejected frame must not consume an id"
    assert second.captured_monotonic_s == 11.0


def test_infinite_timestamp_does_not_poison_the_monotonic_floor() -> None:
    clock = BrokenClock([5.0, float("inf"), 6.0])
    source = CaptureSource(
        FakeCaptureBackend([make_raw()]), clock=clock, max_consecutive_failures=99
    )
    source.open()
    source.capture()
    with pytest.raises(InvalidTimestampError):
        source.capture()
    assert source.capture().captured_monotonic_s == 6.0


def test_invalid_timestamps_count_toward_the_failure_threshold() -> None:
    source = CaptureSource(
        FakeCaptureBackend([make_raw()]),
        clock=BrokenClock([float("nan")]),
        max_consecutive_failures=2,
    )
    source.open()
    with pytest.raises(InvalidTimestampError):
        source.capture()
    with pytest.raises(CaptureFailureThresholdExceeded):
        source.capture()


def test_invalid_timestamp_error_is_a_capture_error() -> None:
    assert issubclass(InvalidTimestampError, CaptureError)


@pytest.mark.parametrize("bad", ["1.0", None, True, [1.0]])
def test_non_numeric_timestamps_are_rejected(bad: object) -> None:
    """bool is excluded deliberately: True would otherwise pass as 1.0."""
    with pytest.raises(InvalidTimestampError, match="real number"):
        Frame.from_raw(make_raw(), frame_id=1, captured_monotonic_s=bad)  # type: ignore[arg-type]


def test_broken_clock_rejects_an_empty_script() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        BrokenClock([])
