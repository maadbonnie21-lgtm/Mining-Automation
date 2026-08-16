"""Frame types and payload ownership.

Ownership is the reason there are two types here rather than one.

Platform capture APIs commonly hand back a buffer they will overwrite on the
next grab. A consumer that holds such a buffer across frames silently reads
different pixels than it thinks it does, which produces perception bugs that
look like detector flakiness.

So the boundary is explicit:

``RawFrame``
    What a backend returns. Its payload **may** alias a buffer the backend
    reuses. It carries no frame identity and no timestamp -- those are assigned
    centrally so monotonicity cannot be violated by a backend.

``Frame``
    What consumers receive. Its payload is ``bytes``, immutable and owned by the
    frame. Safe to retain, queue, hash, or write to a fixture.

:meth:`Frame.from_raw` performs the ownership transfer. When the backend already
produced ``bytes`` this is free; when it produced a mutable buffer it copies,
and that copy is deliberate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Final

from ..contracts import FrameRef
from .errors import InvalidFrameError, InvalidTimestampError

__all__ = ["Frame", "PixelFormat", "RawFrame", "validate_identity"]


class PixelFormat(Enum):
    """Byte layout of a frame payload.

    Members carry distinct string values rather than their pixel width. Using
    the byte count as the value would make same-width formats enum *aliases* --
    ``BGR888 is RGB888`` would be true and channel order would silently vanish,
    which is exactly the kind of bug that surfaces later as a colour-inverted
    detector. Widths live in :data:`_BYTES_PER_PIXEL` instead.
    """

    GRAY8 = "gray8"
    RGB888 = "rgb888"
    BGR888 = "bgr888"
    RGBA8888 = "rgba8888"
    BGRA8888 = "bgra8888"

    @property
    def bytes_per_pixel(self) -> int:
        return _BYTES_PER_PIXEL[self]


_BYTES_PER_PIXEL: Final[dict[PixelFormat, int]] = {
    PixelFormat.GRAY8: 1,
    PixelFormat.RGB888: 3,
    PixelFormat.BGR888: 3,
    PixelFormat.RGBA8888: 4,
    PixelFormat.BGRA8888: 4,
}


#: Guard against a bad width/height/format triple demanding an absurd payload.
MAX_REASONABLE_PAYLOAD_BYTES: Final[int] = 512 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class RawFrame:
    """A backend's output, before identity assignment and ownership transfer.

    ``payload`` may alias a reusable backend buffer. Do not retain it; pass it
    straight to :meth:`Frame.from_raw`.
    """

    payload: bytes | bytearray | memoryview
    width: int
    height: int
    pixel_format: PixelFormat = PixelFormat.BGRA8888

    @property
    def expected_payload_bytes(self) -> int:
        return self.width * self.height * self.pixel_format.bytes_per_pixel


@dataclass(frozen=True, slots=True)
class Frame:
    """A validated, owned frame.

    ``payload`` is immutable and safe to retain for the lifetime of the frame.
    """

    ref: FrameRef
    payload: bytes
    pixel_format: PixelFormat

    @property
    def frame_id(self) -> int:
        return self.ref.frame_id

    @property
    def captured_monotonic_s(self) -> float:
        return self.ref.captured_monotonic_s

    @property
    def width(self) -> int:
        return self.ref.width

    @property
    def height(self) -> int:
        return self.ref.height

    @property
    def size_bytes(self) -> int:
        return len(self.payload)

    def age_s(self, now_monotonic_s: float) -> float:
        """Seconds since capture, floored at zero."""
        return max(0.0, now_monotonic_s - self.ref.captured_monotonic_s)

    @classmethod
    def from_raw(
        cls,
        raw: RawFrame,
        *,
        frame_id: int,
        captured_monotonic_s: float,
    ) -> Frame:
        """Validate ``raw`` and take ownership of its payload."""
        _validate_geometry(raw)
        validate_identity(frame_id, captured_monotonic_s)
        actual = _payload_nbytes(raw.payload)
        expected = raw.expected_payload_bytes
        if actual != expected:
            raise InvalidFrameError(
                f"payload size {actual} bytes != expected {expected} for "
                f"{raw.width}x{raw.height} {raw.pixel_format.name}"
            )
        payload = bytes(raw.payload)
        if len(payload) != expected:
            raise InvalidFrameError(
                f"payload size changed during copy: {len(payload)} != {expected}"
            )
        return cls(
            ref=FrameRef(
                frame_id=frame_id,
                captured_monotonic_s=captured_monotonic_s,
                width=raw.width,
                height=raw.height,
            ),
            payload=payload,
            pixel_format=raw.pixel_format,
        )


def _payload_nbytes(payload: bytes | bytearray | memoryview) -> int:
    if isinstance(payload, memoryview):
        return payload.nbytes
    return len(payload)


def validate_identity(frame_id: int, captured_monotonic_s: float) -> None:
    if frame_id < 1:
        raise InvalidTimestampError(f"frame_id must be >= 1, got {frame_id}")
    if not isinstance(captured_monotonic_s, (int, float)) or isinstance(
        captured_monotonic_s, bool
    ):
        raise InvalidTimestampError(
            f"timestamp must be a real number, got {type(captured_monotonic_s).__name__}"
        )
    value = float(captured_monotonic_s)
    if math.isnan(value):
        raise InvalidTimestampError("timestamp is NaN")
    if math.isinf(value):
        raise InvalidTimestampError(f"timestamp is infinite: {value}")
    if value < 0.0:
        raise InvalidTimestampError(f"monotonic timestamp cannot be negative: {value}")


def _validate_geometry(raw: RawFrame) -> None:
    if raw.width <= 0 or raw.height <= 0:
        raise InvalidFrameError(f"non-positive frame dimensions: {raw.width}x{raw.height}")
    expected = raw.expected_payload_bytes
    if expected > MAX_REASONABLE_PAYLOAD_BYTES:
        raise InvalidFrameError(
            f"declared geometry {raw.width}x{raw.height} {raw.pixel_format.name} "
            f"requires {expected} bytes, above the {MAX_REASONABLE_PAYLOAD_BYTES} byte guard"
        )
