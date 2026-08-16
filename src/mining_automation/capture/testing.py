"""Deterministic test doubles for the capture layer.

These ship inside the package rather than under ``tools/`` because they are part
of the capture contract: any future platform backend should be exercised against
the same behavioural expectations these doubles encode, and downstream
subsystems (perception, state, controller) need a display-free capture source to
test against.

They are test doubles, not a platform backend, and are never used by production
code paths.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from .errors import CaptureUnavailableError
from .frame import PixelFormat, RawFrame

__all__ = ["FakeCaptureBackend", "ManualClock", "solid_payload"]


class ManualClock:
    """A monotonic clock advanced explicitly by the test.

    Supports deliberate regression via :meth:`set` so the non-monotonic path can
    be exercised.
    """

    __slots__ = ("_now",)

    def __init__(self, start_s: float = 0.0) -> None:
        self._now = float(start_s)

    def monotonic_s(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds

    def set(self, seconds: float) -> None:
        """Set the clock to an arbitrary value, including backwards."""
        self._now = float(seconds)


def solid_payload(
    width: int,
    height: int,
    value: int = 0,
    pixel_format: PixelFormat = PixelFormat.BGRA8888,
) -> bytes:
    """Build a correctly sized payload of a single byte value."""
    return bytes([value & 0xFF]) * (width * height * pixel_format.bytes_per_pixel)


class FakeCaptureBackend:
    """Scriptable backend.

    Each entry in ``script`` is either a :class:`RawFrame` to return or an
    exception instance to raise. The last entry repeats once exhausted, which
    makes "permanently broken surface" easy to express.

    Args:
        script: Sequence of frames and/or exceptions. Defaults to a single
            valid 4x2 frame.
        name: Backend name recorded in diagnostics.
        reuse_buffer: When True, returned payloads alias one mutable buffer that
            is overwritten on each grab -- reproducing the aliasing behaviour of
            real platform APIs so ownership transfer can be tested.
    """

    def __init__(
        self,
        script: Iterable[RawFrame | BaseException] | None = None,
        *,
        name: str = "fake",
        reuse_buffer: bool = False,
    ) -> None:
        default: Sequence[RawFrame | BaseException] = (
            RawFrame(payload=solid_payload(4, 2), width=4, height=2),
        )
        self._script: list[RawFrame | BaseException] = list(script) if script is not None else list(default)
        if not self._script:
            raise ValueError("script must contain at least one entry")
        self._name = name
        self._reuse_buffer = reuse_buffer
        self._index = 0
        self._shared_buffer: bytearray | None = None

        self.open_calls = 0
        self.close_calls = 0
        self.grab_calls = 0
        self.is_open = False

    @property
    def name(self) -> str:
        return self._name

    def open(self) -> None:
        self.open_calls += 1
        self.is_open = True

    def close(self) -> None:
        self.close_calls += 1
        self.is_open = False

    def grab(self) -> RawFrame:
        self.grab_calls += 1
        if not self.is_open:
            raise CaptureUnavailableError("fake backend is not open")

        entry = self._script[min(self._index, len(self._script) - 1)]
        self._index += 1

        if isinstance(entry, BaseException):
            raise entry

        if not self._reuse_buffer:
            return entry

        payload = bytes(entry.payload)
        if self._shared_buffer is None or len(self._shared_buffer) != len(payload):
            self._shared_buffer = bytearray(len(payload))
        self._shared_buffer[:] = payload
        return RawFrame(
            payload=self._shared_buffer,
            width=entry.width,
            height=entry.height,
            pixel_format=entry.pixel_format,
        )

    def scribble_buffer(self, value: int = 0xFF) -> None:
        """Overwrite the shared buffer, simulating the backend reusing it.

        Only meaningful when ``reuse_buffer=True``. Used to prove that a
        previously returned :class:`~mining_automation.capture.frame.Frame` does
        not change.
        """
        if self._shared_buffer is not None:
            for i in range(len(self._shared_buffer)):
                self._shared_buffer[i] = value & 0xFF
