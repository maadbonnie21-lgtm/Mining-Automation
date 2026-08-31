from __future__ import annotations

import gzip
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from mining_automation.perception import (
    CorruptFixtureError,
    MissingFixtureError,
    load_replay_dataset,
    materialize_gzip_replay_dataset,
)
from mining_automation.perception import fixture_materialization as materialization


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


def test_source_manifest_edit_during_materialization_cannot_rebind_destination(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_manifest = _write_source(tmp_path / "source")
    verified_snapshot = source_manifest.read_bytes()
    changed = json.loads(verified_snapshot.decode("utf-8"))
    changed["dataset_id"] = "unverified-concurrent-edit"
    changed["cases"][0]["frame"]["path"] = "frames/unverified.raw"
    changed_bytes = json.dumps(changed).encode("utf-8")

    real_gzip_open = gzip.open
    changed_source = False

    def open_after_source_edit(*args: object, **kwargs: object):
        nonlocal changed_source
        if not changed_source:
            source_manifest.write_bytes(changed_bytes)
            changed_source = True
        return real_gzip_open(*args, **kwargs)

    monkeypatch.setattr(materialization.gzip, "open", open_after_source_edit)
    destination_manifest = materialize_gzip_replay_dataset(
        source_manifest,
        tmp_path / "materialized",
    )

    assert changed_source is True
    assert source_manifest.read_bytes() == changed_bytes
    assert destination_manifest.read_bytes() == verified_snapshot
    dataset = load_replay_dataset(destination_manifest)
    assert dataset.manifest.dataset_id == "compressed-test"
    assert dataset.samples[0].case.frame.path == "frames/case.raw"


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


def test_concurrent_payload_writer_winner_is_preserved_exactly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_manifest = _write_source(tmp_path / "source")
    destination = tmp_path / "materialized"
    target = destination / "frames" / "case.raw"
    winner_payload = b"concurrent-writer-payload"
    real_open = Path.open

    def open_after_concurrent_win(
        path: Path,
        mode: str = "r",
        *args: object,
        **kwargs: object,
    ):
        if path == target and mode == "xb":
            with real_open(path, "wb") as winner:
                winner.write(winner_payload)
            raise FileExistsError(f"simulated concurrent winner: {path}")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_after_concurrent_win)

    with pytest.raises(CorruptFixtureError, match="already exists"):
        materialize_gzip_replay_dataset(source_manifest, destination)

    assert target.read_bytes() == winner_payload
    assert not (destination / "manifest.json").exists()


def test_concurrent_manifest_writer_winner_is_preserved_and_owned_payload_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_manifest = _write_source(tmp_path / "source")
    destination = tmp_path / "materialized"
    destination_manifest = destination / "manifest.json"
    materialized_payload = destination / "frames" / "case.raw"
    winner_manifest = b'{"winner":"concurrent-writer"}\n'
    real_open = Path.open

    def open_after_concurrent_win(
        path: Path,
        mode: str = "r",
        *args: object,
        **kwargs: object,
    ):
        if path == destination_manifest and mode == "xb":
            with real_open(path, "wb") as winner:
                winner.write(winner_manifest)
            raise FileExistsError(f"simulated concurrent winner: {path}")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_after_concurrent_win)

    with pytest.raises(CorruptFixtureError, match="manifest already exists"):
        materialize_gzip_replay_dataset(source_manifest, destination)

    assert destination_manifest.read_bytes() == winner_manifest
    assert not materialized_payload.exists()


def test_two_simultaneous_materializers_preserve_one_complete_winner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = b"\x01\x02\x03\x04\x05\x06"
    source_manifest = _write_source(tmp_path / "source", payload=payload)
    source_manifest_bytes = source_manifest.read_bytes()
    destination = tmp_path / "materialized"
    target = destination / "frames" / "case.raw"
    target.parent.mkdir(parents=True)
    exclusive_create_barrier = Barrier(2, timeout=10)
    real_open = Path.open

    def synchronized_open(
        path: Path,
        mode: str = "r",
        *args: object,
        **kwargs: object,
    ):
        if path == target and mode == "xb":
            exclusive_create_barrier.wait()
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", synchronized_open)

    successes: list[Path] = []
    failures: list[CorruptFixtureError] = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                materialize_gzip_replay_dataset,
                source_manifest,
                destination,
            )
            for _ in range(2)
        ]
        for future in futures:
            try:
                successes.append(future.result(timeout=15))
            except CorruptFixtureError as exc:
                failures.append(exc)

    assert successes == [destination / "manifest.json"]
    assert len(failures) == 1
    assert "materialized fixture already exists" in str(failures[0])
    assert target.read_bytes() == payload
    assert successes[0].read_bytes() == source_manifest_bytes
    dataset = load_replay_dataset(successes[0])
    assert dataset.samples[0].frame.payload == payload


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
