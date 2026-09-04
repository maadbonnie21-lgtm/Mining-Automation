"""First-campaign-only calibration identity for inventory classifier V2.

This module deliberately does not invoke the complete sanitized replay loader.
It reads and hashes only the first reviewed campaign's eight region artifacts.
The second campaign can therefore remain absent while the frozen calibration
identity is reproduced.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from .positive_classifier_v2 import inventory_positive_v2_algorithm_configuration
from .sanitized_replay import (
    InventorySanitizedReplayError,
    _json_object,
    _object_value,
    _owned_path,
    _read_bytes,
    _region_value,
    _required_list,
    _required_object,
    _required_positive_int,
    _required_relative_path,
    _required_sha256,
    _required_text,
    _sha256,
)

__all__ = [
    "INVENTORY_POSITIVE_V2_CALIBRATION_SESSION_ID",
    "INVENTORY_POSITIVE_V2_HELD_OUT_SESSION_ID",
    "InventoryPositiveV2CalibrationError",
    "compute_inventory_positive_v2_calibration_sha256",
]


INVENTORY_POSITIVE_V2_CALIBRATION_SESSION_ID: Final[str] = (
    "20260830T183057.424897Z-inventory-session"
)
INVENTORY_POSITIVE_V2_HELD_OUT_SESSION_ID: Final[str] = (
    "20260830T222938.820219Z-inventory-session"
)
_CALIBRATION_CASE_COUNT: Final[int] = 8
_CALIBRATION_SCHEMA: Final[str] = "inventory-positive-v2-calibration-evidence-v1"
_FIXTURE_KIND: Final[str] = "inventory-sanitized-region-replay"
_FIXTURE_SCHEMA_VERSION: Final[int] = 2
_PIXEL_BYTES: Final[int] = 4


class InventoryPositiveV2CalibrationError(RuntimeError):
    """The calibration-only corpus or its integrity contract was invalid."""


def compute_inventory_positive_v2_calibration_sha256(
    fixture_directory: Path,
) -> str:
    """Hash the first reviewed campaign without opening held-out artifacts."""
    if not isinstance(fixture_directory, Path):
        raise TypeError("fixture_directory must be pathlib.Path")
    try:
        return _compute_calibration_sha256(fixture_directory)
    except InventorySanitizedReplayError as exc:
        raise InventoryPositiveV2CalibrationError(
            f"V2 calibration fixture is invalid: {exc}"
        ) from exc


def _compute_calibration_sha256(fixture_directory: Path) -> str:
    manifest_path = fixture_directory / "manifest.json"
    manifest_bytes = _read_bytes(manifest_path, "fixture manifest")
    sidecar = _read_bytes(
        fixture_directory / "manifest.json.sha256",
        "fixture SHA-256 sidecar",
    )
    try:
        sidecar_digest = sidecar.decode("ascii").split()[0]
    except (UnicodeDecodeError, IndexError) as exc:
        raise InventoryPositiveV2CalibrationError(
            "fixture SHA-256 sidecar is malformed"
        ) from exc
    if sidecar_digest != _sha256(manifest_bytes):
        raise InventoryPositiveV2CalibrationError(
            "fixture manifest SHA-256 mismatch"
        )

    manifest = _json_object(manifest_bytes, "fixture manifest")
    if manifest.get("fixture_kind") != _FIXTURE_KIND:
        raise InventoryPositiveV2CalibrationError(
            "unsupported sanitized fixture kind"
        )
    if manifest.get("schema_version") != _FIXTURE_SCHEMA_VERSION:
        raise InventoryPositiveV2CalibrationError(
            "V2 calibration requires sanitized fixture schema 2"
        )
    if manifest.get("activation_allowed") is not False:
        raise InventoryPositiveV2CalibrationError(
            "sanitized fixture cannot allow activation"
        )

    candidate = _required_object(manifest, "candidate")
    if candidate.get("activation_allowed") is not False:
        raise InventoryPositiveV2CalibrationError(
            "candidate fixture cannot allow activation"
        )
    evidence = _required_object(candidate, "evidence")
    profile = _required_object(candidate, "profile")
    reconstruction = _required_object(manifest, "frame_reconstruction")
    if reconstruction.get("pixel_format") != "bgra8888":
        raise InventoryPositiveV2CalibrationError(
            "V2 calibration requires BGRA8888 region artifacts"
        )
    _required_positive_int(reconstruction, "width")
    _required_positive_int(reconstruction, "height")
    region = _region_value(
        reconstruction.get("region"), "frame reconstruction region"
    )
    expected_region_bytes = region.width * region.height * _PIXEL_BYTES

    cases = _required_list(manifest, "cases")
    if len(cases) < _CALIBRATION_CASE_COUNT:
        raise InventoryPositiveV2CalibrationError(
            "V2 calibration requires its first exact eight fixture cases"
        )
    calibration: list[dict[str, object]] = []
    seen_case_ids: set[str] = set()
    for raw in cases[:_CALIBRATION_CASE_COUNT]:
        case = _object_value(raw, "sanitized fixture calibration case")
        case_id = _required_text(case, "case_id")
        if case_id in seen_case_ids:
            raise InventoryPositiveV2CalibrationError(
                "V2 calibration case ids are not unique"
            )
        seen_case_ids.add(case_id)
        truth = _required_object(case, "review_truth")
        session_id = _required_text(truth, "session_id")
        capture_id = _required_text(truth, "capture_id")
        if session_id != INVENTORY_POSITIVE_V2_CALIBRATION_SESSION_ID:
            raise InventoryPositiveV2CalibrationError(
                "V2 calibration campaign must be the first exact eight cases"
            )
        if case_id != f"{session_id}/{capture_id}":
            raise InventoryPositiveV2CalibrationError(
                "V2 calibration case identity differs from reviewer truth"
            )
        artifact = _required_object(case, "frame_region")
        expected_sha256 = _required_sha256(artifact, "sha256")
        artifact_path = _owned_path(
            fixture_directory,
            _required_relative_path(artifact, "path"),
            "calibration frame region",
        )
        artifact_bytes = _read_bytes(artifact_path, "calibration frame region")
        if len(artifact_bytes) != expected_region_bytes:
            raise InventoryPositiveV2CalibrationError(
                f"calibration frame region has wrong byte length: {case_id}"
            )
        actual_sha256 = _sha256(artifact_bytes)
        if actual_sha256 != expected_sha256:
            raise InventoryPositiveV2CalibrationError(
                f"calibration frame region SHA-256 mismatch: {case_id}"
            )
        calibration.append(
            {
                "case_id": case_id,
                "frame_region_sha256": actual_sha256,
                "reviewer_truth": truth,
            }
        )

    payload = {
        "algorithm": inventory_positive_v2_algorithm_configuration(),
        "cases": calibration,
        "profile": profile,
        "reference_region_sha256": _required_sha256(
            evidence, "reference_region_sha256"
        ),
        "schema": _CALIBRATION_SCHEMA,
    }
    if _read_bytes(manifest_path, "fixture manifest") != manifest_bytes:
        raise InventoryPositiveV2CalibrationError(
            "fixture manifest changed during V2 calibration"
        )
    return _sha256(_canonical_bytes(payload))


def _canonical_bytes(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
