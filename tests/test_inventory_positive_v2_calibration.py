from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from mining_automation.perception.inventory import (
    INVENTORY_POSITIVE_V2_CALIBRATION_SHA256,
    InventoryPositiveV2CalibrationError,
    compute_inventory_positive_v2_calibration_sha256,
)

_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "perception"
    / "inventory-live-candidate-safety-bb0d0e3f7ff1c73b"
)


def _manifest(fixture: Path) -> dict[str, object]:
    value = json.loads((fixture / "manifest.json").read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _case_path(fixture: Path, case: object) -> Path:
    assert isinstance(case, dict)
    artifact = case["frame_region"]
    assert isinstance(artifact, dict)
    relative = artifact["path"]
    assert isinstance(relative, str)
    return fixture / relative


def test_calibration_digest_matches_the_frozen_first_campaign() -> None:
    assert compute_inventory_positive_v2_calibration_sha256(_FIXTURE) == (
        INVENTORY_POSITIVE_V2_CALIBRATION_SHA256
    )


def test_calibration_does_not_open_held_out_artifacts(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(_FIXTURE, fixture)
    cases = _manifest(fixture)["cases"]
    assert isinstance(cases, list)
    for case in cases[8:]:
        _case_path(fixture, case).unlink()

    assert compute_inventory_positive_v2_calibration_sha256(fixture) == (
        INVENTORY_POSITIVE_V2_CALIBRATION_SHA256
    )


def test_calibration_digest_ignores_changed_held_out_pixels(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(_FIXTURE, fixture)
    cases = _manifest(fixture)["cases"]
    assert isinstance(cases, list)
    held_out = _case_path(fixture, cases[8])
    payload = bytearray(held_out.read_bytes())
    payload[-1] ^= 1
    held_out.write_bytes(payload)

    assert compute_inventory_positive_v2_calibration_sha256(fixture) == (
        INVENTORY_POSITIVE_V2_CALIBRATION_SHA256
    )


def test_calibration_rejects_changed_calibration_pixels(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(_FIXTURE, fixture)
    cases = _manifest(fixture)["cases"]
    assert isinstance(cases, list)
    calibration = _case_path(fixture, cases[0])
    payload = bytearray(calibration.read_bytes())
    payload[-1] ^= 1
    calibration.write_bytes(payload)

    with pytest.raises(
        InventoryPositiveV2CalibrationError,
        match="calibration frame region SHA-256 mismatch",
    ):
        compute_inventory_positive_v2_calibration_sha256(fixture)
