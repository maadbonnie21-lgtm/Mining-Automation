#!/usr/bin/env python3
"""Convert a reviewed capture-harness BMP into replay-v1 BGRA bytes.

This development tool accepts only the exact top-down, uncompressed 32-bit
BMP shape written by ``windows_capture_check.py``. It deliberately is not a
general image decoder: rejecting any other encoding keeps replay pixel order
and colour semantics explicit.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mining_automation.perception.inventory.fixture_preparation import (
    MAX_CAPTURE_BMP_FILE_BYTES,
    InventoryFixturePreparationError,
    PreparedInventoryFrame,
    extract_capture_bmp,
)

__all__ = [
    "InventoryFixturePreparationError",
    "PreparedInventoryFrame",
    "extract_capture_bmp",
    "main",
]


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
        if input_path.stat().st_size > MAX_CAPTURE_BMP_FILE_BYTES:
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
