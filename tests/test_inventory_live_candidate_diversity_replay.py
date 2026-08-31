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
    / "inventory-live-candidate-safety-bb0d0e3f7ff1c73b"
)
_GENERATOR_HEAD = "15c841aa04185f127072cbaf4d7ff36ad2a01ade"


def test_diverse_reviewed_real_inventory_regions_replay_production_safely() -> None:
    report = replay_inventory_sanitized_fixture(_FIXTURE)

    assert report.passed
    assert report.failed_case_ids == ()
    assert report.dataset_id == "inventory-live-candidate-safety-bb0d0e3f7ff1c73b"
    assert report.fixture_schema_version == 2
    assert report.generator_head_sha == _GENERATOR_HEAD
    assert report.fixture_manifest_sha256 == (
        "2e518ce81dd291f8b7d055afad9ddc12acbc66e0e967845f8f2e548fe1644479"
    )
    assert report.detector_id == "inventory-baseline"
    assert report.detector_version == "1.0.0"
    assert report.profile_id == "candidate-live-inventory-348867800b28a54e"
    assert report.configuration_id == (
        "inventory-detector-config-"
        "5991ca1c46bb5b6e5bb31330730c668ede3463740c1e8f26a4d433c1c2753c4e"
    )
    assert len(report.cases) == 16
    assert [item.actual["occupied_slots"] for item in report.cases] == [
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


def test_diverse_fixture_preserves_reviewer_truth_and_release_failures() -> None:
    manifest = json.loads((_FIXTURE / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["activation_allowed"] is False
    candidate = manifest["candidate"]
    assert candidate["activation_allowed"] is False
    assert candidate["derivation"] == {
        "mode": "imported-reviewed-sanitized-fixture",
        "source_fixture": {
            "dataset_id": "inventory-live-candidate-safety-v1",
            "generator_head_sha": None,
            "manifest_sha256": (
                "f6f2655405231995096568e9d4be39b51b37492fc6b24120f885428e098f2bcd"
            ),
            "schema_version": 1,
        },
    }

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
        0,
        0,
        5,
        28,
        None,
        None,
        27,
        28,
    ]
    for index in (2, 3, 10, 11):
        assert cases[index]["review_truth"]["visibility"] == "inventory-visible"
        expectation = cases[index]["current_safety_expectation"]
        assert expectation["label"] == "unknown"
        assert expectation["occupied_slots"] is None
        assert expectation["confidence"] == 0.0
        assert expectation["reason"]

    for index in (4, 12):
        assert cases[index]["review_truth"]["visibility"] == "wrong-tab-visible"
    for index in (5, 13):
        assert cases[index]["review_truth"]["visibility"] == "inventory-obstructed"
    assert cases[6]["review_truth"]["selected_item_visible"] is True
    assert cases[14]["review_truth"]["hover_visible"] is True
    assert cases[14]["review_truth"]["drag_visible"] is False
    assert cases[7]["review_truth"]["quantity_text_visible"] is True
    assert cases[15]["review_truth"]["quantity_text_visible"] is True


def test_diverse_fixture_contains_only_owned_inventory_region_pixels() -> None:
    manifest = json.loads((_FIXTURE / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["frame_reconstruction"] == {
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
