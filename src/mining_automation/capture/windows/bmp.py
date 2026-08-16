"""Minimal BMP encoder for saving a diagnostic frame to disk.

Standard library only -- no image dependency added just for an opt-in dev
diagnostic. A captured :class:`~mining_automation.capture.frame.Frame`
payload is already BGRA, 4 bytes per pixel, top-down row order, which is
byte-for-byte what a 32bpp top-down BMP wants, so no pixel conversion is
needed at all -- only two small headers.
"""

from __future__ import annotations

import struct
from pathlib import Path

__all__ = ["write_bgra_bmp"]

_FILE_HEADER_SIZE = 14
_INFO_HEADER_SIZE = 40


def write_bgra_bmp(path: Path, *, width: int, height: int, bgra_payload: bytes) -> None:
    """Write a top-down BGRA payload as a 32bpp BMP file.

    Raises:
        ValueError: ``bgra_payload`` is not exactly ``width * height * 4`` bytes.
    """
    expected = width * height * 4
    if len(bgra_payload) != expected:
        raise ValueError(
            f"payload size {len(bgra_payload)} != expected {expected} for {width}x{height}"
        )

    file_header = struct.pack(
        "<2sIHHI",
        b"BM",
        _FILE_HEADER_SIZE + _INFO_HEADER_SIZE + len(bgra_payload),
        0,
        0,
        _FILE_HEADER_SIZE + _INFO_HEADER_SIZE,
    )
    # Negative height requests top-down row order, matching the payload
    # exactly -- Windows GDI has documented this BMP variant since Windows
    # 3.0, and every viewer this tool's output is likely opened with (modern
    # Windows, Paint, browsers) supports it.
    info_header = struct.pack(
        "<IiiHHIIiiII",
        _INFO_HEADER_SIZE,
        width,
        -height,
        1,
        32,
        0,
        len(bgra_payload),
        0,
        0,
        0,
        0,
    )
    path.write_bytes(file_header + info_header + bgra_payload)
