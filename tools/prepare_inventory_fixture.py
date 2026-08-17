#!/usr/bin/env python3
"""Convert a reviewed capture-harness BMP into replay-v1 BGRA bytes.

This development tool accepts only the exact top-down, uncompressed 32-bit
BMP shape written by ``windows_capture_check.py``. It deliberately is not a
general image decoder: rejecting any other encoding keeps replay pixel order
and colour semantics explicit.
"""

from __future__ import annotations

import argparse
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "InventoryFixturePreparationError",
    "PreparedInventoryFrame",
    "extract_capture_bmp",
    "main",
]

_FILE_HEADER = struct.Struct("<2sIHHI")
_INFO_HEADER = struct.Struct("<IiiHHIIiiII")
_PIXEL_OFFSET = _FILE_HEADER.size + _INFO_HEADER.size
_MAX_PAYLOAD_BYTES = 512 * 1024 * 1024


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


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("input_bmp", type=Path, help="reviewed capture-harness BMP")
    parser.add_argument("output_raw", type=Path, help="headerless .bgra replay payload")
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing output file",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        input_path = args.input_bmp.resolve(strict=True)
        output_path = args.output_raw.resolve(strict=False)
        if input_path == output_path:
            raise InventoryFixturePreparationError("input and output paths must differ")
        if output_path.exists() and input_path.samefile(output_path):
            raise InventoryFixturePreparationError(
                "input and output paths must not refer to the same file"
            )
        if output_path.exists() and not args.force:
            raise InventoryFixturePreparationError(
                f"output already exists: {output_path}; pass --force to replace it"
            )
        if input_path.stat().st_size > _PIXEL_OFFSET + _MAX_PAYLOAD_BYTES:
            raise InventoryFixturePreparationError("BMP exceeds the replay safety limit")

        prepared = extract_capture_bmp(input_path.read_bytes())
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_mode = "wb" if args.force else "xb"
        with output_path.open(output_mode) as output_file:
            output_file.write(prepared.payload)
    except (OSError, InventoryFixturePreparationError) as exc:
        print(f"inventory fixture preparation failed: {exc}", file=sys.stderr)
        return 2

    print(
        f"wrote {len(prepared.payload)} bytes to {output_path} "
        f"({prepared.width}x{prepared.height} {prepared.pixel_format})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
