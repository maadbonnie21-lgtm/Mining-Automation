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


def test_resource_release_record_binds_packaged_profile_identity_and_geometry() -> None:
    record = json.loads(_RELEASE_RECORD.read_text(encoding="utf-8"))
    profile = load_varrock_east_iron_profile()

    assert record["profile_id"] == VARROCK_EAST_IRON_PROFILE_ID == profile.profile_id
    assert record["detector_version"] == VARROCK_EAST_IRON_DETECTOR_VERSION
    assert record["profile_schema_version"] == RESOURCE_PROFILE_SCHEMA_VERSION
    assert record["location_id"] == profile.location_id == "varrock-east-mine"
    assert record["ore_label"] == profile.ore_label == "iron"
    envelope = record["capture_envelope"]
    assert envelope["frame_height"] == profile.frame_height
    assert envelope["frame_width"] == profile.frame_width
    assert envelope["pixel_format"] == profile.pixel_format.value
    assert envelope["camera"] == "fixed-reviewed-camera-v1"
    assert envelope["automatic_camera_recovery_allowed"] is False
    assert envelope["unsupported_or_uncertain_view_policy"] == (
        "zero_targets_and_stop"
    )
    assert "reported_dpi" not in record["capture_envelope"]
    assert tuple(
        (item["resource_id"], tuple(item["region"]))
        for item in record["candidate_regions"]
    ) == tuple(
        (candidate.resource_id, candidate.region) for candidate in profile.candidates
    )
    assert tuple(item["resource_id"] for item in record["candidate_regions"]) == (
        VARROCK_EAST_IRON_RESOURCE_IDS
    )


def test_resource_release_record_marks_candidate_dpi_as_pending_review() -> None:
    record = json.loads(_RELEASE_RECORD.read_text(encoding="utf-8"))

    assert record["capture_envelope"]["reported_dpi_requirement"] == {
        "candidate_value": 96,
        "evidence_role": (
            "required_release_envelope_constraint_not_packaged_profile_identity"
        ),
        "status": "pending_fresh_supported_envelope_review",
    }
    assert "reported_dpi" not in record["capture_envelope"]
    assert record["release_eligible"] is False


def test_resource_release_record_separates_c1_empirical_from_c2_review_gates() -> None:
    record = json.loads(_RELEASE_RECORD.read_text(encoding="utf-8"))

    categories = record["release_gate_categories"]
    assert categories["c1_fresh_empirical_evidence"] == {
        "status": "OPEN",
        "gates": [
            "real north-west depletion and respawn",
            "real center depletion and respawn",
            "real north-east depletion and respawn",
            "real unsupported-location fixture",
            "real neighboring copper/tin/terrain negative fixtures",
            "real sample-patch obstruction fixture",
            "fresh current-client positive capture in the exact reviewed supported view",
            "independent reviewer truth for every empirical case",
        ],
    }
    assert categories["c2_evidence_contingent_source_review"] == {
        "status": "OPEN",
        "gates": [
            (
                "reported DPI 96 and exact client/renderer/profile envelope "
                "independently reviewed"
            ),
            "every retained failure promoted to a privacy-safe replay regression",
            "source-owned constrained-v1 resource release and promotion record",
        ],
    }
    all_gates = [
        gate for category in categories.values() for gate in category["gates"]
    ]
    assert set(categories["c1_fresh_empirical_evidence"]["gates"]).isdisjoint(
        categories["c2_evidence_contingent_source_review"]["gates"]
    )
    assert all(
        "reacquisition" not in gate and "restart" not in gate
        for gate in all_gates
    )


def test_resource_release_record_cannot_self_promote_with_open_b_c1_or_c2() -> None:
    record = json.loads(_RELEASE_RECORD.read_text(encoding="utf-8"))

    assert record["schema_version"] == 2
    assert record["release_ready"] is False
    assert record["release_eligible"] is False
    assert record["release_status"] == (
        "pending_b_boundary_c1_empirical_and_c2_source_review_gates"
    )
    assert record["release_evidence_boundary"] == {
        "reason": (
            "generic_development_capture_and_annotation_are_not_release_authority"
        ),
        "required_capability": (
            "source_owned_immutable_passive_campaign_and_independent_review"
        ),
        "status": "OPEN",
    }
    assert {
        category["status"]
        for category in record["release_gate_categories"].values()
    } == {"OPEN"}
