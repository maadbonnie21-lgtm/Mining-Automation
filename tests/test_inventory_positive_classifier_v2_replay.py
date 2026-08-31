from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from mining_automation.perception.inventory import (
    INVENTORY_POSITIVE_V2_CALIBRATION_SHA256,
    INVENTORY_POSITIVE_V2_MODEL_FREEZE_GIT_SHA,
    InventoryPositiveV2EvaluationError,
    evaluate_inventory_positive_v2,
    positive_v2_evaluation,
)

_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "perception"
    / "inventory-live-candidate-safety-bb0d0e3f7ff1c73b"
)
_HEAD = "9985c9ad2522ef869efff6fe2dfd4979c69d1c79"


@pytest.fixture(scope="module")
def report():  # type: ignore[no-untyped-def]
    return evaluate_inventory_positive_v2(_FIXTURE, git_head_sha=_HEAD)


def test_frozen_v2_records_the_one_shot_held_out_failure(report) -> None:  # type: ignore[no-untyped-def]
    assert not report.passed
    assert len(report.calibration_cases) == 8
    assert len(report.held_out_cases) == 8
    assert report.calibration_evidence_sha256 == (
        INVENTORY_POSITIVE_V2_CALIBRATION_SHA256
    )
    assert report.to_dict()["activation_allowed"] is False
    assert report.model_freeze_git_sha == INVENTORY_POSITIVE_V2_MODEL_FREEZE_GIT_SHA
    assert report.to_dict()["retrospective_validation"][  # type: ignore[index]
        "evaluated_model_freeze_git_sha"
    ] == (
        INVENTORY_POSITIVE_V2_MODEL_FREEZE_GIT_SHA
    )
    assert [item.v2_actual["occupied_slots"] for item in report.cases] == [
        0,
        0,
        1,
        28,
        None,
        None,
        None,
        None,
        0,
        0,
        None,
        None,
        None,
        None,
        None,
        None,
    ]
    assert [item.case_id for item in report.cases if not item.passed] == [
        (
            "20260830T222938.820219Z-inventory-session/"
            "20260830T223251.020375Z-partial"
        ),
        (
            "20260830T222938.820219Z-inventory-session/"
            "20260830T223429.224578Z-full"
        ),
    ]


def test_v2_clean_root_cause_rows_explain_the_v1_publication_failure(report) -> None:  # type: ignore[no-untyped-def]
    clean = [item for item in report.cases if item.slot_root_cause]
    assert len(clean) == 8
    assert all(len(item.slot_root_cause) == 28 for item in clean)
    calibration_partial = report.cases[2].slot_root_cause[0]

    assert calibration_partial[
        "expected_slot_state_from_reviewed_count_and_prefix_policy"
    ] == "occupied"
    assert calibration_partial["raw_score"] == pytest.approx(0.42702410130718954)
    assert calibration_partial["changed_fraction"] == pytest.approx(
        0.5746527777777778
    )
    assert calibration_partial["mean_normalized_l1_delta"] == pytest.approx(
        0.08255718954248366
    )
    assert calibration_partial["mean_color_component"] == pytest.approx(
        0.0247671568627451
    )
    assert calibration_partial["v1_state"] == "occupied"
    assert calibration_partial["v1_confidence"] == pytest.approx(
        0.6327077572481984
    )
    assert calibration_partial["v1_meets_publication_floor"] is False
    assert calibration_partial["active_spatial_cells"] == 9
    cell_counts = calibration_partial["spatial_cell_changed_pixels"]
    assert isinstance(cell_counts, list)
    assert len(cell_counts) == 9
    assert sum(cell_counts) == 331
    assert all(isinstance(value, int) and value > 0 for value in cell_counts)
    assert calibration_partial["distributed_support"] is True
    assert calibration_partial["v2_confidence"] == 1.0
    assert calibration_partial["v2_meets_publication_floor"] is True


def test_v2_held_out_clean_feature_succeeds_but_guard_fails_closed(report) -> None:  # type: ignore[no-untyped-def]
    held_out_partial = report.cases[10].v2_actual
    held_out_full = report.cases[11].v2_actual

    for actual in (held_out_partial, held_out_full):
        assert actual["occupied_slots"] is None
        assert actual["confidence"] == 0.0
        assert "slot 1 perimeter has 4 pixels at or above D=61" in str(
            actual["reason"]
        )
    assert report.cases[10].v2_analysis_failure_reason == (
        "inventory V2 unsupported presentation: slot 1 perimeter has 4 pixels "
        "at or above D=61"
    )
    assert report.cases[11].v2_analysis_failure_reason == (
        report.cases[10].v2_analysis_failure_reason
    )

    partial_occupied = report.cases[10].slot_root_cause[:5]
    full_occupied = report.cases[11].slot_root_cause
    assert all(item["v2_meets_publication_floor"] is True for item in partial_occupied)
    assert all(item["v2_meets_publication_floor"] is True for item in full_occupied)
    assert min(item["v2_confidence"] for item in partial_occupied) == pytest.approx(
        0.9287749287749287
    )
    assert min(item["v2_confidence"] for item in full_occupied) == pytest.approx(
        0.8575498575498576
    )


def test_v2_wrong_tab_and_obstruction_remain_fail_closed(report) -> None:  # type: ignore[no-untyped-def]
    for index in (4, 5, 12, 13):
        actual = report.cases[index].v2_actual
        assert actual["label"] == "unknown"
        assert actual["occupied_slots"] is None
        assert actual["confidence"] == 0.0
        assert actual["reason"]


def test_v2_selected_hover_and_quantity_variants_are_fail_closed(report) -> None:  # type: ignore[no-untyped-def]
    for index in (6, 7, 14, 15):
        actual = report.cases[index].v2_actual
        assert actual["occupied_slots"] is None
        assert actual["label"] == "unknown"
        assert actual["confidence"] == 0.0
        assert actual["reason"]


def test_v1_safety_replay_identity_and_results_remain_unchanged(report) -> None:  # type: ignore[no-untyped-def]
    assert report.v1_detector == {
        "configuration_id": (
            "inventory-detector-config-"
            "5991ca1c46bb5b6e5bb31330730c668ede3463740c1e8f26a4d433c1c2753c4e"
        ),
        "detector_id": "inventory-baseline",
        "detector_version": "1.0.0",
    }
    assert [item.v1_actual["occupied_slots"] for item in report.cases] == [
        0,
        0,
        None,
        None,
        None,
        None,
        None,
        None,
        0,
        0,
        None,
        None,
        None,
        None,
        None,
        None,
    ]


def test_v2_report_is_canonical_and_deterministic(report) -> None:  # type: ignore[no-untyped-def]
    first = report.to_json()
    second = evaluate_inventory_positive_v2(_FIXTURE, git_head_sha=_HEAD).to_json()

    assert first == second
    assert first.endswith("\n")
    decoded = json.loads(first)
    assert decoded["passed"] is False
    assert decoded["report_schema_version"] == 2
    assert decoded["git_provenance"] == {
        "head_sha": _HEAD,
        "verified_by_clean_head_cli": False,
    }
    repository_evidence = decoded["calibration"]["repository_evidence"]
    assert repository_evidence["model_commit_precedes_formal_evaluator_commit"] is None
    assert repository_evidence["unseen_data_chronology_established"] is False
    assert decoded["fixture"]["campaign_partition_schema"] == (
        "inventory-positive-v2-campaign-partition-v1"
    )
    assert (
        decoded["retrospective_validation"][
            "unseen_data_chronology_established"
        ]
        is False
    )
    assert decoded["model"]["configuration_sha256"] == (
        "cd916de0f7b7201ecfd25646b34e09855bc4641f5a041be6f6665837f719acc2"
    )
    assert decoded["v2_detector"]["detector_id"] == "inventory-positive-v2"
    assert decoded["v2_detector"]["detector_version"] == "2.0.0"
    assert decoded["v2_detector"]["minimum_slot_confidence"] == 0.8


def test_v2_model_identity_contains_no_held_out_or_full_fixture_identity(report) -> None:  # type: ignore[no-untyped-def]
    model = json.dumps(report.model_configuration, sort_keys=True)

    assert "20260830T222938.820219Z-inventory-session" not in model
    assert report.fixture_dataset_id not in model
    assert report.fixture_manifest_sha256 not in model
    assert report.calibration_evidence_sha256 in model


@pytest.mark.parametrize(
    ("field", "replacement_value", "message"),
    [
        (
            "dataset_id",
            "inventory-live-candidate-safety-0000000000000000",
            "pinned canonical dataset",
        ),
        ("fixture_manifest_sha256", "0" * 64, "pinned canonical manifest"),
    ],
)
def test_v2_evaluator_pins_exact_dataset_and_manifest_identity(
    monkeypatch,
    field: str,
    replacement_value: str,
    message: str,
) -> None:  # type: ignore[no-untyped-def]
    original = positive_v2_evaluation.replay_inventory_sanitized_fixture(_FIXTURE)
    altered = replace(original, **{field: replacement_value})
    monkeypatch.setattr(
        positive_v2_evaluation,
        "replay_inventory_sanitized_fixture",
        lambda fixture: altered,
    )

    with pytest.raises(InventoryPositiveV2EvaluationError, match=message):
        evaluate_inventory_positive_v2(_FIXTURE, git_head_sha=_HEAD)


def test_v2_evaluator_pins_exact_held_out_case_identities(report) -> None:  # type: ignore[no-untyped-def]
    case_ids = tuple(item.case_id for item in report.cases)
    altered = (*case_ids[:8], case_ids[9], case_ids[8], *case_ids[10:])

    with pytest.raises(
        InventoryPositiveV2EvaluationError,
        match="held-out case identities or order",
    ):
        positive_v2_evaluation._require_canonical_case_identities(altered)


@pytest.mark.parametrize("case_index", [0, 15])
def test_v2_reuses_strict_v1_fixture_integrity_before_evaluation(
    tmp_path: Path,
    case_index: int,
) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(_FIXTURE, fixture)
    manifest = json.loads((fixture / "manifest.json").read_text(encoding="utf-8"))
    payload_path = fixture / manifest["cases"][case_index]["frame_region"]["path"]
    payload = bytearray(payload_path.read_bytes())
    payload[-1] ^= 1
    payload_path.write_bytes(payload)

    with pytest.raises(
        InventoryPositiveV2EvaluationError,
        match="V1 fixture integrity/replay failed",
    ):
        evaluate_inventory_positive_v2(fixture, git_head_sha=_HEAD)


@pytest.mark.parametrize(
    "git_head_sha",
    ["", "A" * 40, "0" * 39, "g" * 40],
)
def test_v2_evaluator_rejects_invalid_provenance_head(git_head_sha: str) -> None:
    with pytest.raises(InventoryPositiveV2EvaluationError, match="git_head_sha"):
        evaluate_inventory_positive_v2(_FIXTURE, git_head_sha=git_head_sha)
