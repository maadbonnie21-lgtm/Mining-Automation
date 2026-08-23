"""Validated conversion contract for reviewed inventory capture fixtures."""

from __future__ import annotations

import struct
from dataclasses import dataclass

__all__ = [
    "MAX_CAPTURE_BMP_FILE_BYTES",
    "InventoryFixturePreparationError",
    "PreparedInventoryFrame",
    "extract_capture_bmp",
]

_FILE_HEADER = struct.Struct("<2sIHHI")
_INFO_HEADER = struct.Struct("<IiiHHIIiiII")
_PIXEL_OFFSET = _FILE_HEADER.size + _INFO_HEADER.size
_MAX_PAYLOAD_BYTES = 512 * 1024 * 1024
MAX_CAPTURE_BMP_FILE_BYTES = _PIXEL_OFFSET + _MAX_PAYLOAD_BYTES


class InventoryFixturePreparationError(ValueError):
    """A supplied BMP cannot preserve the capture frame's replay semantics."""


@dataclass(frozen=True, slots=True)
class PreparedInventoryFrame:
    """Validated headerless frame data ready for replay schema v1."""

    width: int
    height: int
    payload: bytes
    pixel_format: str = "bgra8888"


def extract_capture_bmp(data: bytes) -> PreparedInventoryFrame:
    """Extract exact top-down BGRA bytes from the capture harness's BMP."""
    if not isinstance(data, bytes):
        raise TypeError(f"data must be bytes, got {type(data).__name__}")
    if len(data) < _PIXEL_OFFSET:
        raise InventoryFixturePreparationError("BMP is shorter than its required headers")

    signature, file_size, reserved_1, reserved_2, pixel_offset = _FILE_HEADER.unpack_from(
        data
    )
    if signature != b"BM":
        raise InventoryFixturePreparationError("BMP signature must be 'BM'")
    if file_size != len(data):
        raise InventoryFixturePreparationError(
            f"BMP file size {file_size} does not match actual size {len(data)}"
        )
    if reserved_1 != 0 or reserved_2 != 0:
        raise InventoryFixturePreparationError("BMP reserved fields must be zero")
    if pixel_offset != _PIXEL_OFFSET:
        raise InventoryFixturePreparationError(
            f"BMP pixel offset must be {_PIXEL_OFFSET}, got {pixel_offset}"
        )

    (
        info_size,
        width,
        stored_height,
        planes,
        bits_per_pixel,
        compression,
        image_size,
        _x_pixels_per_meter,
        _y_pixels_per_meter,
        colours_used,
        important_colours,
    ) = _INFO_HEADER.unpack_from(data, _FILE_HEADER.size)
    if info_size != _INFO_HEADER.size:
        raise InventoryFixturePreparationError(
            f"BMP information header must be {_INFO_HEADER.size} bytes"
        )
    if width <= 0:
        raise InventoryFixturePreparationError("BMP width must be positive")
    if stored_height >= 0:
        raise InventoryFixturePreparationError(
            "BMP must use a negative height for top-down row order"
        )
    if planes != 1:
        raise InventoryFixturePreparationError("BMP must contain exactly one plane")
    if bits_per_pixel != 32:
        raise InventoryFixturePreparationError("BMP must contain 32-bit BGRA pixels")
    if compression != 0:
        raise InventoryFixturePreparationError("BMP pixels must be uncompressed")
    if colours_used != 0 or important_colours != 0:
        raise InventoryFixturePreparationError("BMP must not contain a colour table")

    height = -stored_height
    expected_size = width * height * 4
    if expected_size > _MAX_PAYLOAD_BYTES:
        raise InventoryFixturePreparationError(
            f"BMP payload exceeds the {_MAX_PAYLOAD_BYTES}-byte replay safety limit"
        )
    if image_size != expected_size:
        raise InventoryFixturePreparationError(
            f"BMP image size {image_size} does not match expected {expected_size}"
        )
    if pixel_offset + expected_size != len(data):
        raise InventoryFixturePreparationError(
            "BMP pixel payload length does not match its dimensions"
        )

    return PreparedInventoryFrame(
        width=width,
        height=height,
        payload=data[pixel_offset:],
    )
