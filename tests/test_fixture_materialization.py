from __future__ import annotations

import gzip
import hashlib
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
    sanitized_sha256: str | None = None,
    include_sanitized_sha256: bool = True,
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
                "provenance": {
                    "source": "unit-test",
                    **(
                        {
                            "sanitized_sha256": sanitized_sha256
                            if sanitized_sha256 is not None
                            else hashlib.sha256(payload).hexdigest()
                        }
                        if payload is not None and include_sanitized_sha256
                        else {}
                    ),
                },
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


def test_same_length_one_byte_corruption_is_rejected_and_removed(tmp_path: Path) -> None:
    reviewed = b"\x01\x02\x03\x04\x05\x06"
    corrupted = b"\x01\x02\x03\x04\x05\x07"
    source_manifest = _write_source(
        tmp_path / "source",
        payload=corrupted,
        sanitized_sha256=hashlib.sha256(reviewed).hexdigest(),
    )
    destination = tmp_path / "materialized"

    with pytest.raises(CorruptFixtureError, match="SHA-256 mismatch"):
        materialize_gzip_replay_dataset(source_manifest, destination)

    assert not (destination / "frames" / "case.raw").exists()
    assert not (destination / "manifest.json").exists()


def test_late_hash_mismatch_removes_earlier_materialized_case(tmp_path: Path) -> None:
    payload = b"\x01\x02\x03\x04\x05\x06"
    source_manifest = _write_source(tmp_path / "source", payload=payload)
    manifest = json.loads(source_manifest.read_text(encoding="utf-8"))
    second_case = dict(manifest["cases"][0])
    second_case["case_id"] = "case-2"
    second_case["frame"] = {**second_case["frame"], "path": "frames/case-2.raw"}
    second_case["provenance"] = {
        **second_case["provenance"],
        "sanitized_sha256": hashlib.sha256(b"reviewed").hexdigest(),
    }
    manifest["cases"].append(second_case)
    source_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    (source_manifest.parent / "frames" / "case-2.raw.gz").write_bytes(
        gzip.compress(payload, compresslevel=9, mtime=0)
    )
    destination = tmp_path / "materialized"

    with pytest.raises(CorruptFixtureError, match="SHA-256 mismatch"):
        materialize_gzip_replay_dataset(source_manifest, destination)

    assert not (destination / "frames" / "case.raw").exists()
    assert not (destination / "frames" / "case-2.raw").exists()
    assert not (destination / "manifest.json").exists()


@pytest.mark.parametrize(
    "sanitized_sha256",
    ["0" * 63, "0" * 65, "G" * 64, "A" * 64],
)
def test_noncanonical_sanitized_hash_is_rejected(
    tmp_path: Path,
    sanitized_sha256: str,
) -> None:
    source_manifest = _write_source(
        tmp_path / "source",
        sanitized_sha256=sanitized_sha256,
    )
    destination = tmp_path / "materialized"

    with pytest.raises(CorruptFixtureError, match="sanitized_sha256"):
        materialize_gzip_replay_dataset(source_manifest, destination)

    assert not (destination / "frames" / "case.raw").exists()
    assert not (destination / "manifest.json").exists()


def test_missing_sanitized_hash_is_rejected(tmp_path: Path) -> None:
    source_manifest = _write_source(
        tmp_path / "source",
        include_sanitized_sha256=False,
    )
    destination = tmp_path / "materialized"

    with pytest.raises(CorruptFixtureError, match="sanitized_sha256"):
        materialize_gzip_replay_dataset(source_manifest, destination)

    assert not (destination / "frames" / "case.raw").exists()
    assert not (destination / "manifest.json").exists()
