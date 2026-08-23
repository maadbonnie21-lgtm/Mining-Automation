from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from mining_automation.perception import (
    CorruptFixtureError,
    MissingFixtureError,
    load_replay_dataset,
    materialize_gzip_replay_dataset,
)


def _write_source(
    root: Path,
    *,
    payload: bytes | None = b"\x01\x02\x03\x04\x05\x06",
    compressed_bytes: bytes | None = None,
) -> Path:
    frames = root / "frames"
    frames.mkdir(parents=True)
    if compressed_bytes is not None:
        (frames / "case.raw.gz").write_bytes(compressed_bytes)
    elif payload is not None:
        (frames / "case.raw.gz").write_bytes(
            gzip.compress(payload, compresslevel=9, mtime=0)
        )

    manifest = {
        "schema_version": 1,
        "dataset_id": "compressed-test",
        "cases": [
            {
                "case_id": "case",
                "frame": {
                    "path": "frames/case.raw",
                    "width": 2,
                    "height": 1,
                    "pixel_format": "rgb888",
                },
                "expected_observations": [],
                "tags": ["synthetic"],
                "provenance": {"source": "unit-test"},
                "notes": "",
            }
        ],
    }
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_materializes_exact_bytes_for_ordinary_replay_loader(tmp_path: Path) -> None:
    payload = b"\x01\x02\x03\x04\x05\x06"
    source_manifest = _write_source(tmp_path / "source", payload=payload)

    manifest = materialize_gzip_replay_dataset(
        source_manifest,
        tmp_path / "materialized",
    )
    dataset = load_replay_dataset(manifest)

    assert (manifest.parent / "frames" / "case.raw").read_bytes() == payload
    assert dataset.samples[0].frame.payload == payload


def test_missing_compressed_payload_is_typed_and_leaves_no_manifest(
    tmp_path: Path,
) -> None:
    source_manifest = _write_source(tmp_path / "source", payload=None)
    destination = tmp_path / "materialized"

    with pytest.raises(MissingFixtureError, match="compressed fixture is missing"):
        materialize_gzip_replay_dataset(source_manifest, destination)

    assert not (destination / "manifest.json").exists()


def test_corrupt_gzip_is_typed_and_partial_output_is_removed(tmp_path: Path) -> None:
    source_manifest = _write_source(
        tmp_path / "source",
        compressed_bytes=b"not-gzip",
    )
    destination = tmp_path / "materialized"

    with pytest.raises(CorruptFixtureError, match="cannot be decoded"):
        materialize_gzip_replay_dataset(source_manifest, destination)

    assert not (destination / "frames" / "case.raw").exists()
    assert not (destination / "manifest.json").exists()


def test_oversized_decompressed_payload_is_rejected(tmp_path: Path) -> None:
    source_manifest = _write_source(
        tmp_path / "source",
        payload=b"\x00" * 7,
    )
    destination = tmp_path / "materialized"

    with pytest.raises(CorruptFixtureError, match="expands beyond"):
        materialize_gzip_replay_dataset(source_manifest, destination)

    assert not (destination / "frames" / "case.raw").exists()


def test_existing_materialized_target_is_not_overwritten(tmp_path: Path) -> None:
    source_manifest = _write_source(tmp_path / "source")
    destination = tmp_path / "materialized"
    target = destination / "frames" / "case.raw"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"keep-me")

    with pytest.raises(CorruptFixtureError, match="already exists"):
        materialize_gzip_replay_dataset(source_manifest, destination)

    assert target.read_bytes() == b"keep-me"
    assert not (destination / "manifest.json").exists()
