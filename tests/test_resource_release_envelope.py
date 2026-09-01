from __future__ import annotations

import json
from pathlib import Path

from mining_automation.perception import (
    RESOURCE_PROFILE_SCHEMA_VERSION,
    VARROCK_EAST_IRON_DETECTOR_VERSION,
    VARROCK_EAST_IRON_PROFILE_ID,
    VARROCK_EAST_IRON_RESOURCE_IDS,
    load_varrock_east_iron_profile,
)

_ROOT = Path(__file__).resolve().parents[1]
_RELEASE_RECORD = _ROOT / "knowledge" / "mining" / "varrock_east_iron_profile_v1.json"


def test_resource_release_record_matches_the_packaged_production_profile() -> None:
    record = json.loads(_RELEASE_RECORD.read_text(encoding="utf-8"))
    profile = load_varrock_east_iron_profile()

    assert record["profile_id"] == VARROCK_EAST_IRON_PROFILE_ID == profile.profile_id
    assert record["detector_version"] == VARROCK_EAST_IRON_DETECTOR_VERSION
    assert record["profile_schema_version"] == RESOURCE_PROFILE_SCHEMA_VERSION
    assert record["location_id"] == profile.location_id == "varrock-east-mine"
    assert record["ore_label"] == profile.ore_label == "iron"
    assert record["capture_envelope"] == {
        "automatic_camera_recovery_allowed": False,
        "camera": "fixed-reviewed-camera-v1",
        "frame_height": profile.frame_height,
        "frame_width": profile.frame_width,
        "pixel_format": profile.pixel_format.value,
        "reported_dpi": 96,
        "unsupported_or_uncertain_view_policy": "zero_targets_and_stop",
    }
    assert tuple(
        (item["resource_id"], tuple(item["region"]))
        for item in record["candidate_regions"]
    ) == tuple(
        (candidate.resource_id, candidate.region) for candidate in profile.candidates
    )
    assert tuple(item["resource_id"] for item in record["candidate_regions"]) == (
        VARROCK_EAST_IRON_RESOURCE_IDS
    )


def test_resource_release_record_contains_only_current_fresh_evidence_gates() -> None:
    record = json.loads(_RELEASE_RECORD.read_text(encoding="utf-8"))

    assert record["remaining_release_gates"] == [
        "real north-west depletion and respawn",
        "real center depletion and respawn",
        "real north-east depletion and respawn",
        "real unsupported-location fixture",
        "real neighboring copper/tin/terrain negative fixtures",
        "real sample-patch obstruction fixture",
        "fresh current-client positive capture in the exact reviewed supported view",
        "final constrained-v1 supported-envelope review",
    ]
    assert all(
        "reacquisition" not in gate and "restart" not in gate
        for gate in record["remaining_release_gates"]
    )
