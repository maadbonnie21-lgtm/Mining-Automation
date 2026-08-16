# Capture Layer

Milestone: **M1 — Foundation and reliable capture**
Package: `src/mining_automation/capture/`

The boundary between platform-specific screen acquisition and everything above
it. Consumers depend on `CaptureSource` and `Frame`. No consumer imports a
platform backend, which is what allows perception, state, and controller work to
be developed and tested without a display.

## Public API

| Symbol | Role |
|---|---|
| `CaptureSource` | Consumer entry point. Owns identity, timing, validation, failure policy, diagnostics. |
| `Frame` | Validated frame with an owned immutable payload. Wraps the shared `contracts.FrameRef`. |
| `RawFrame` | What a backend returns. Payload may alias a reusable buffer. No identity, no timestamp. |
| `PixelFormat` | Byte layout. `GRAY8`, `RGB888`, `BGR888`, `RGBA8888`, `BGRA8888`. |
| `CaptureBackend` | Runtime-checkable protocol a platform implementation satisfies. |
| `MonotonicClock` / `SystemMonotonicClock` | Injected time source. |
| `CaptureStats` | Frame/failure/retry counters. |
| `CaptureError` and subclasses | Failure taxonomy. |

Test doubles live in `capture.testing`: `FakeCaptureBackend`, `ManualClock`,
`solid_payload`.

```python
from mining_automation.capture import CaptureSource
from mining_automation.capture.testing import FakeCaptureBackend

with CaptureSource(FakeCaptureBackend()) as source:
    frame = source.capture()
    frame.frame_id, frame.width, frame.height, frame.captured_monotonic_s
```

## Guarantees

**Frame identity.** Ids are assigned by `CaptureSource`, start at 1, and
increase by exactly one per *successful* capture. A rejected frame does not
consume an id. Backends cannot influence identity.

**Monotonic time.** Timestamps come from one injected clock. Equal consecutive
timestamps are allowed (a fast loop inside clock resolution); a backwards clock
raises `NonMonotonicCaptureError` rather than being clamped. A rejected frame
does not advance the monotonic floor.

**Payload ownership.** `Frame.payload` is `bytes` — immutable and owned. A
backend may return a `RawFrame` aliasing a buffer it overwrites on the next
grab; `Frame.from_raw` copies. A retained `Frame` therefore never changes
underneath a consumer. This is covered by
`test_frame_survives_backend_reusing_its_buffer`.

**Explicit failure.** Every failure raises a `CaptureError` subclass. Capture
never returns `None`, never returns a sentinel, and never returns the previous
frame.

**Validation before delivery.** Non-positive dimensions, empty payloads, and any
payload whose length disagrees with `width * height * bytes_per_pixel` are
rejected. Truncated payloads are *not* padded and oversized payloads are *not*
cropped — reshaping a malformed frame would hand corrupt pixels to perception.

**Bounded failure.** After `max_consecutive_failures` (default 5) consecutive
failures the source latches: it raises `CaptureFailureThresholdExceeded` and
stops calling the backend entirely until `reset_failures()` is called. One
success resets the counter.

## Failure taxonomy

| Exception | Meaning | Typically |
|---|---|---|
| `CaptureClosedError` | Source is not open. | Programming error |
| `CaptureUnavailableError` | Surface temporarily unreadable — minimised, locked session, sleeping display. | Transient |
| `CaptureTimeoutError` | Backend produced no frame in time. | Transient |
| `CaptureBackendError` | Uninterpretable platform failure. Original preserved as `__cause__`. | Terminal |
| `InvalidFrameError` | Frame failed validation. | Terminal |
| `NonMonotonicCaptureError` | Clock moved backwards. | Terminal |
| `CaptureFailureThresholdExceeded` | Consecutive limit reached; source latched. | Escalate |

Recovery policy branches on type; nothing needs to parse a message.

## Retry policy

`retry_attempts` **defaults to 0**, on purpose. A retry loop inside the capture
layer hides how bad the surface actually is from the layer responsible for
deciding about recovery, and AGENTS.md requires that unknown state is not
converted into progress.

When retries are enabled, they cannot mask a persistent fault: every attempt
emits its own `capture.failed` event, counts toward the consecutive total, and
the threshold check runs on each one. A source with `retry_attempts=10` and
`max_consecutive_failures=3` calls the backend three times and then latches —
verified by `test_retries_cannot_hide_a_persistent_failure`.

## Diagnostics

Events go to any `diagnostics.EventSink`. Every event carries `backend`.

| Event | When |
|---|---|
| `capture.opened` | Backend opened |
| `capture.closed` | Backend closed, or raised during close |
| `capture.frame` | Sampled success (see below) |
| `capture.failed` | Every failed attempt, with `error_type`, `attempt`, `consecutive_failures` |
| `capture.retry` | Each retry, with attempts remaining |
| `capture.failure_threshold_exceeded` | Source latched |
| `capture.failures_reset` | `reset_failures()` cleared a real failure state |

Success events are **sampled** via `success_event_interval` and disabled by
default; at capture rate, per-frame events would bury the failure events that
matter. Failures are never sampled.

## Supported and unsupported

**Supported now**

- Any backend satisfying the `CaptureBackend` protocol
- The five pixel formats above
- Deterministic testing with no display, via `capture.testing`
- Injected clocks
- Synchronous, single-threaded capture

**Not implemented in M1** — deliberately out of scope per issue #1

- Any real platform backend (Windows/X11/Wayland/macOS). The protocol is
  defined; no OS implementation ships yet.
- Window discovery, targeting, focus handling, DPI/scaling awareness
- Region-of-interest cropping and format conversion
- Frame buffering, ring buffers, async or threaded capture
- Frame persistence to disk and replay
- Any perception, detection, navigation, banking, or GUI behaviour

**Known limitations**

- `CaptureSource` is not thread-safe. One source per thread, or add external
  synchronisation.
- `Frame.from_raw` copies when a backend returns a mutable buffer. At 4K BGRA
  that is roughly 33 MB per frame. Correctness is prioritised over that copy in
  M1; if profiling later shows it matters, the fix is a pooled-buffer variant
  behind the same ownership guarantee, not a relaxation of the guarantee.
- Validation checks payload *length*, not pixel plausibility. A correctly sized
  buffer of garbage passes. Content validation belongs to perception.
- `MAX_REASONABLE_PAYLOAD_BYTES` (512 MB) guards absurd geometry. Displays
  beyond that would need it raised.

## Dependencies

None beyond the standard library. The capture package imports only
`mining_automation.contracts` and `mining_automation.diagnostics` internally.

A real platform backend will need a third-party dependency (`mss`,
`python-xlib`, `pywin32`, or similar). That decision belongs to the backend
issue, not here — which is precisely why the protocol seam exists.

## Tests

`tests/test_capture.py` — 61 tests, 100% statement coverage of the capture
package.

Groups: protocol conformance, frame validation, payload ownership, identity and
monotonicity, lifecycle and idempotency, failure paths, latch and reset
behaviour, retry policy, configuration validation, diagnostics, consumer
decoupling.
