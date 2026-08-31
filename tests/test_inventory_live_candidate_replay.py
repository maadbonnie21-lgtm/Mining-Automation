from __future__ import annotations

import json
from pathlib import Path

from mining_automation.perception.inventory import (
    replay_inventory_sanitized_fixture,
)

_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "perception"
    / "inventory-live-candidate-safety-v1"
)


def test_reviewed_real_inventory_regions_replay_current_production_safely() -> None:
    report = replay_inventory_sanitized_fixture(_FIXTURE)

    assert report.passed
    assert report.failed_case_ids == ()
    assert report.dataset_id == "inventory-live-candidate-safety-v1"
    assert report.fixture_schema_version == 1
    assert report.generator_head_sha is None
    assert report.detector_id == "inventory-baseline"
    assert report.detector_version == "1.0.0"
    assert len(report.cases) == 8
    assert [item.actual["occupied_slots"] for item in report.cases] == [
        0,
        0,
        None,
        None,
        None,
        None,
        None,
        None,
    ]
    assert [item.actual["label"] for item in report.cases] == [
        "empty",
        "empty",
        "unknown",
        "unknown",
        "unknown",
        "unknown",
        "unknown",
        "unknown",
    ]


def test_real_fixture_preserves_release_truth_separate_from_safety_expectations() -> None:
    manifest = json.loads((_FIXTURE / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["activation_allowed"] is False
    assert manifest["candidate"]["activation_allowed"] is False
    cases = manifest["cases"]
    assert [item["review_truth"]["occupied_slots"] for item in cases] == [
        0,
        0,
        1,
        28,
        None,
        None,
        28,
        1,
    ]
    # The permanent safety baseline must not disguise the two ordinary iron
    # release failures as passing vision results.
    for index in (2, 3):
        assert cases[index]["review_truth"]["visibility"] == "inventory-visible"
        assert cases[index]["current_safety_expectation"]["label"] == "unknown"
        assert cases[index]["current_safety_expectation"]["occupied_slots"] is None
        assert cases[index]["current_safety_expectation"]["confidence"] == 0.0
        assert cases[index]["current_safety_expectation"]["reason"]

    assert cases[4]["review_truth"]["visibility"] == "wrong-tab-visible"
    assert cases[5]["review_truth"]["visibility"] == "inventory-obstructed"
    assert cases[6]["review_truth"]["selected_item_visible"] is True
    assert cases[6]["review_truth"]["drag_visible"] is False
    assert cases[7]["review_truth"]["quantity_text_visible"] is True


def test_real_fixture_contains_only_owned_inventory_region_pixels() -> None:
    manifest = json.loads((_FIXTURE / "manifest.json").read_text(encoding="utf-8"))
    reconstruction = manifest["frame_reconstruction"]
    assert reconstruction == {
        "fill_byte": 0,
        "height": 1078,
        "pixel_format": "bgra8888",
        "region": [567, 569, 158, 248],
        "width": 1005,
    }
    expected_size = 158 * 248 * 4
    assert all(
        (_FIXTURE / item["frame_region"]["path"]).stat().st_size == expected_size
        for item in manifest["cases"]
    )
    serialized = (_FIXTURE / "manifest.json").read_text(encoding="utf-8").lower()
    assert "window_title" not in serialized
    assert "notes" not in serialized
    assert ":\\" not in serialized
