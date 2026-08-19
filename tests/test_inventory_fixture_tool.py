from __future__ import annotations

import os
import struct
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from mining_automation.capture.windows.bmp import write_bgra_bmp
from mining_automation.perception.inventory import (
    InventoryFixturePreparationError,
    extract_capture_bmp,
)

_TOOL = Path(__file__).resolve().parents[1] / "tools" / "prepare_inventory_fixture.py"


def _bmp_bytes(tmp_path: Path) -> tuple[bytes, bytes]:
    payload = bytes(
        (
            1,
            2,
            3,
            255,
            4,
            5,
            6,
            255,
            7,
            8,
            9,
            255,
            10,
            11,
            12,
            255,
        )
    )
    path = tmp_path / "capture.bmp"
    write_bgra_bmp(path, width=2, height=2, bgra_payload=payload)
    return path.read_bytes(), payload


def test_extract_capture_bmp_preserves_exact_top_down_bgra_payload(
    tmp_path: Path,
) -> None:
    data, payload = _bmp_bytes(tmp_path)

    prepared = extract_capture_bmp(data)

    assert (prepared.width, prepared.height) == (2, 2)
    assert prepared.pixel_format == "bgra8888"
    assert prepared.payload == payload


def test_extract_capture_bmp_requires_owned_bytes(tmp_path: Path) -> None:
    data, _ = _bmp_bytes(tmp_path)

    with pytest.raises(TypeError, match="data must be bytes"):
        extract_capture_bmp(bytearray(data))  # type: ignore[arg-type]


def _pack(offset: int, format_string: str, value: object) -> Callable[[bytearray], None]:
    def mutate(data: bytearray) -> None:
        struct.pack_into(format_string, data, offset, value)

    return mutate


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (_pack(0, "<2s", b"ZZ"), "signature"),
        (_pack(2, "<I", 0), "file size"),
        (_pack(6, "<H", 1), "reserved"),
        (_pack(10, "<I", 55), "pixel offset"),
        (_pack(14, "<I", 41), "information header"),
        (_pack(18, "<i", 0), "width"),
        (_pack(22, "<i", 2), "negative height"),
        (_pack(26, "<H", 2), "one plane"),
        (_pack(28, "<H", 24), "32-bit"),
        (_pack(30, "<I", 1), "uncompressed"),
        (_pack(34, "<I", 0), "image size"),
        (_pack(46, "<I", 1), "colour table"),
    ],
    ids=[
        "signature",
        "file-size",
        "reserved",
        "pixel-offset",
        "information-header",
        "width",
        "top-down",
        "planes",
        "depth",
        "compression",
        "image-size",
        "colour-table",
    ],
)
def test_extract_capture_bmp_rejects_non_harness_encodings(
    tmp_path: Path,
    mutate: Callable[[bytearray], None],
    message: str,
) -> None:
    original, _ = _bmp_bytes(tmp_path)
    malformed = bytearray(original)
    mutate(malformed)

    with pytest.raises(InventoryFixturePreparationError, match=message):
        extract_capture_bmp(bytes(malformed))


def test_extract_capture_bmp_rejects_truncated_payload(tmp_path: Path) -> None:
    original, _ = _bmp_bytes(tmp_path)
    truncated = bytearray(original[:-1])
    struct.pack_into("<I", truncated, 2, len(truncated))

    with pytest.raises(InventoryFixturePreparationError, match="payload length"):
        extract_capture_bmp(bytes(truncated))


def test_cli_writes_raw_payload_and_refuses_unapproved_overwrite(
    tmp_path: Path,
) -> None:
    data, payload = _bmp_bytes(tmp_path)
    input_path = tmp_path / "input.bmp"
    input_path.write_bytes(data)
    output_path = tmp_path / "frames" / "empty-reference.bgra"

    first = _run_tool(input_path, output_path)
    assert first.returncode == 0
    assert output_path.read_bytes() == payload
    assert "2x2 bgra8888" in first.stdout

    refused = _run_tool(input_path, output_path)
    assert refused.returncode == 2
    assert "pass --force" in refused.stderr

    forced = _run_tool(input_path, output_path, force=True)
    assert forced.returncode == 0
    assert output_path.read_bytes() == payload


def test_cli_refuses_hard_link_alias_even_with_force(
    tmp_path: Path,
) -> None:
    data, _ = _bmp_bytes(tmp_path)
    input_path = tmp_path / "reviewed-input.bmp"
    input_path.write_bytes(data)
    output_alias = tmp_path / "empty-reference.bgra"
    os.link(input_path, output_alias)

    result = _run_tool(input_path, output_alias, force=True)
    assert result.returncode == 2
    assert "same file" in result.stderr
    assert input_path.read_bytes() == data
    assert output_alias.read_bytes() == data


def _run_tool(
    input_path: Path,
    output_path: Path,
    *,
    force: bool = False,
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(_TOOL), str(input_path), str(output_path)]
    if force:
        command.append("--force")
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
