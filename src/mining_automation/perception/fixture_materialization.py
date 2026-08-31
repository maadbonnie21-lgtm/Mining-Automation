"""Losslessly materialize gzip-compressed replay fixtures for evaluation."""

from __future__ import annotations

import gzip
import hashlib
from pathlib import Path

from .errors import CorruptFixtureError, MissingFixtureError
from .replay import load_fixture_manifest

__all__ = ["materialize_gzip_replay_dataset"]

_CHUNK_BYTES = 1024 * 1024


def materialize_gzip_replay_dataset(
    source_manifest: Path,
    destination_root: Path,
) -> Path:
    """Expand an exact gzip-at-rest fixture set into replay-schema-v1 bytes.

    The committed manifest remains an ordinary replay schema v1 manifest whose
    frame paths end in ``.raw``. At rest, each source payload is stored beside
    it as ``<path>.gz``. This function verifies safe paths, exact decompressed
    byte counts, and exact SHA-256 equality with the reviewed
    ``provenance.sanitized_sha256``. It writes every target exclusively and
    installs the manifest only after all payloads succeed. The returned manifest
    can be passed directly to :func:`load_replay_dataset`.

    Raises:
        MissingFixtureError: a required gzip payload is absent.
        CorruptFixtureError: gzip data is invalid, expands beyond the declared
            geometry, has the wrong reviewed digest, is truncated, escapes its
            dataset root, or targets an already-existing materialized file.
    """

    source_manifest = Path(source_manifest)
    destination_root = Path(destination_root)
    manifest = load_fixture_manifest(source_manifest)
    source_root = source_manifest.parent.resolve()
    destination_root.mkdir(parents=True, exist_ok=True)
    destination_root_resolved = destination_root.resolve()
    destination_manifest = destination_root / source_manifest.name

    created: list[Path] = []
    seen_paths: set[str] = set()
    try:
        for case in manifest.cases:
            relative_path = case.frame.path
            if relative_path in seen_paths:
                raise CorruptFixtureError(
                    f"duplicate compressed fixture path: {relative_path}"
                )
            seen_paths.add(relative_path)

            compressed = source_manifest.parent / f"{relative_path}.gz"
            compressed_resolved = compressed.resolve()
            if not compressed_resolved.is_relative_to(source_root):
                raise CorruptFixtureError(
                    f"compressed fixture escapes dataset root: {relative_path}.gz"
                )
            if not compressed.is_file():
                raise MissingFixtureError(
                    f"compressed fixture is missing: {relative_path}.gz"
                )

            target = destination_root / relative_path
            target_resolved = target.resolve()
            if not target_resolved.is_relative_to(destination_root_resolved):
                raise CorruptFixtureError(
                    f"materialized fixture escapes destination root: {relative_path}"
                )
            if target.exists():
                raise CorruptFixtureError(
                    f"materialized fixture already exists: {relative_path}"
                )

            expected = (
                case.frame.width
                * case.frame.height
                * case.frame.pixel_format.bytes_per_pixel
            )
            expected_sha256 = case.provenance.get("sanitized_sha256")
            if (
                not isinstance(expected_sha256, str)
                or len(expected_sha256) != 64
                or any(character not in "0123456789abcdef" for character in expected_sha256)
            ):
                raise CorruptFixtureError(
                    f"compressed fixture {relative_path!r} requires a canonical "
                    "provenance.sanitized_sha256"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            digest = hashlib.sha256()
            try:
                with gzip.open(compressed, "rb") as source, target.open("xb") as output:
                    while chunk := source.read(_CHUNK_BYTES):
                        written += len(chunk)
                        if written > expected:
                            raise CorruptFixtureError(
                                f"compressed fixture {relative_path!r} expands beyond "
                                f"the declared {expected} bytes"
                            )
                        digest.update(chunk)
                        output.write(chunk)
            except (OSError, EOFError) as exc:
                target.unlink(missing_ok=True)
                raise CorruptFixtureError(
                    f"compressed fixture cannot be decoded: {relative_path}.gz"
                ) from exc
            except Exception:
                target.unlink(missing_ok=True)
                raise

            if written != expected:
                target.unlink(missing_ok=True)
                raise CorruptFixtureError(
                    f"compressed fixture {relative_path!r} expands to {written} bytes; "
                    f"expected {expected}"
                )
            actual_sha256 = digest.hexdigest()
            if actual_sha256 != expected_sha256:
                target.unlink(missing_ok=True)
                raise CorruptFixtureError(
                    f"compressed fixture {relative_path!r} SHA-256 mismatch: "
                    f"expected {expected_sha256}, got {actual_sha256}"
                )
            created.append(target)

        if destination_manifest.exists():
            raise CorruptFixtureError(
                f"materialized manifest already exists: {destination_manifest}"
            )
        manifest_bytes = source_manifest.read_bytes()
        try:
            with destination_manifest.open("xb") as output:
                output.write(manifest_bytes)
        except Exception:
            destination_manifest.unlink(missing_ok=True)
            raise
        created.append(destination_manifest)
    except Exception:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise

    return destination_manifest
