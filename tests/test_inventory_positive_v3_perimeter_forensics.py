from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from mining_automation.perception.inventory.positive_v3_perimeter_forensics import (
    InventoryPositiveV3PerimeterForensicError,
    SignalClassification,
    analyze_inventory_positive_v3_perimeter,
)
from mining_automation.perception.inventory.positive_v3_perimeter_forensics_cli import (
    main,
)

_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "perception"
    / "inventory-live-candidate-safety-bb0d0e3f7ff1c73b"
)
_HEAD = "8664e80996548ee718ad7c79cdddc4cfd8823279"


@pytest.fixture(scope="module")
def report():  # type: ignore[no-untyped-def]
    return analyze_inventory_positive_v3_perimeter(
        _FIXTURE,
        git_head_sha=_HEAD,
    )


def test_forensic_maps_the_exact_four_slot_owned_failure_pixels(report) -> None:  # type: ignore[no-untyped-def]
    assert report.target_slot_index == 1
    assert report.slot_size == 32
    assert report.core_inset == 4
    assert report.failure_threshold == 61
    assert [
        (
            item.slot_local_x,
            item.slot_local_y,
            item.reference_rgb,
            item.candidate_rgb,
            item.max_channel_delta,
        )
        for item in report.target_pixels
    ] == [
        (28, 10, (62, 53, 41), (1, 1, 2), 61),
        (29, 12, (62, 53, 41), (1, 1, 2), 61),
        (29, 14, (62, 53, 41), (1, 1, 2), 61),
        (28, 26, (62, 53, 41), (1, 1, 2), 61),
    ]
    assert all(item.inside_full_32x32_slot for item in report.target_pixels)
    assert all(item.inside_4px_perimeter for item in report.target_pixels)
    assert all(not item.inside_24x24_core for item in report.target_pixels)


def test_recurrent_owned_slot_cohort_justifies_artwork_not_ui(
    report,  # type: ignore[no-untyped-def]
) -> None:
    assert report.conclusion is SignalClassification.ARTWORK
    assert report.target_full_slot_sha256 == (
        "7ce2e7770362bbeec32b2a7132b95428fb3b3b1e89f1ee707fa28bde9bd4c3a7"
    )
    assert report.target_core_sha256 == (
        "da542468650b524261a060189f15a591805c48852e2fe4d51901c84fb436e0b4"
    )
    assert report.target_perimeter_sha256 == (
        "8797b6e94f029a99b0db4fad6d6e8a32cfdd99718cc1358285444693ffdeafb6"
    )
    assert len(report.target_panel_sha256s) == 5
    assert len(set(report.target_panel_sha256s)) == 5
    assert report.target_full_slot_sha256 != report.reference_full_slot_sha256
    assert report.target_core_sha256 != report.reference_core_sha256
    assert report.target_perimeter_sha256 != report.reference_perimeter_sha256
    assert SignalClassification.UI not in {
        item.signal_classification for item in report.cases
    }


def test_second_campaign_presentation_changes_preserve_the_exact_slot_bytes(
    report,  # type: ignore[no-untyped-def]
) -> None:
    target = {item.case_id.rsplit("/", 1)[1]: item for item in report.cases[8:]}
    expected_artwork_cases = {
        "20260830T223251.020375Z-partial",
        "20260830T223429.224578Z-full",
        "20260830T223457.929771Z-obstructed",
        "20260830T223534.039055Z-hover-drag",
        "20260830T223630.280156Z-quantity-text",
    }

    assert set(name for name, item in target.items() if item.target_full_slot_match) == (
        expected_artwork_cases
    )
    for name in expected_artwork_cases:
        item = target[name]
        assert item.target_signature_match
        assert item.target_core_match
        assert item.target_perimeter_match
        assert item.signal_classification is SignalClassification.ARTWORK

    assert target["20260830T223457.929771Z-obstructed"].visibility == (
        "inventory-obstructed"
    )
    assert target["20260830T223534.039055Z-hover-drag"].hover_visible
    assert target["20260830T223630.280156Z-quantity-text"].quantity_text_visible


def test_calibration_selection_is_alternate_artwork_and_obstruction_stays_ambiguous(
    report,  # type: ignore[no-untyped-def]
) -> None:
    calibration = {
        item.case_id.rsplit("/", 1)[1]: item for item in report.cases[:8]
    }
    full = calibration["20260830T184604.267640Z-full"]
    selected = calibration["20260830T184642.926662Z-hover-drag"]
    obstructed = calibration["20260830T184628.891977Z-obstructed"]
    quantity = calibration["20260830T185539.015871Z-quantity-text"]

    assert selected.selected_item_visible
    assert full.slot_full_sha256 == selected.slot_full_sha256
    assert full.slot_core_sha256 == selected.slot_core_sha256
    assert full.slot_perimeter_sha256 == selected.slot_perimeter_sha256
    assert [item.max_channel_delta for item in full.pixels] == [47, 39, 47, 0]
    assert [item.max_channel_delta for item in selected.pixels] == [47, 39, 47, 0]
    assert [item.max_channel_delta for item in obstructed.pixels] == [47, 39, 47, 62]
    assert [item.max_channel_delta for item in quantity.pixels] == [0, 0, 0, 0]
    assert full.signal_classification is SignalClassification.ARTWORK
    assert selected.signal_classification is SignalClassification.ARTWORK
    assert obstructed.signal_classification is SignalClassification.AMBIGUOUS
    assert quantity.signal_classification is SignalClassification.AMBIGUOUS
    assert obstructed.nearest_artwork_cohort_full_slot_sha256 == (
        full.slot_full_sha256
    )
    assert obstructed.target_position_differences_from_nearest_artwork == (
        (28, 26),
    )


def test_wrong_tab_pixels_are_reported_but_not_semantically_overclassified(
    report,  # type: ignore[no-untyped-def]
) -> None:
    wrong_tabs = [
        item for item in report.cases if item.visibility == "wrong-tab-visible"
    ]
    assert len(wrong_tabs) == 2
    assert wrong_tabs[0].slot_full_sha256 == wrong_tabs[1].slot_full_sha256
    assert [item.max_channel_delta for item in wrong_tabs[0].pixels] == [
        117,
        61,
        38,
        32,
    ]
    assert all(
        item.signal_classification is SignalClassification.AMBIGUOUS
        for item in wrong_tabs
    )


def test_report_is_canonical_hashed_and_contains_no_operator_artwork_tags(
    report,  # type: ignore[no-untyped-def]
) -> None:
    first = report.to_json()
    second = analyze_inventory_positive_v3_perimeter(
        _FIXTURE,
        git_head_sha=_HEAD,
    ).to_json()

    assert first == second
    assert first.endswith("\n")
    assert report.report_sha256 == hashlib.sha256(first.encode("utf-8")).hexdigest()
    decoded = json.loads(first)
    assert decoded["classification"]["value"] == "artwork"
    assert decoded["classification"]["classification_uses_case_names"] is False
    assert (
        decoded["classification"]["classification_uses_operator_selected_labels"]
        is False
    )
    assert "artwork_tags" not in first
    assert decoded["activation_allowed"] is False
    assert decoded["production_authority"] is False
    assert decoded["validation_status"] == "independent-campaign-required"
    assert decoded["validation_case_ids"] == []
    assert decoded["independent_validation_case_count"] == 0
    assert decoded["generalization_unproven"] is True
    assert (
        decoded["classification"]["prefix_establishes_presentation_legitimacy"]
        is False
    )


def test_forensic_reuses_fixture_integrity_before_reading_pixels(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(_FIXTURE, fixture)
    manifest = json.loads((fixture / "manifest.json").read_text(encoding="utf-8"))
    artifact = fixture / manifest["cases"][10]["frame_region"]["path"]
    payload = bytearray(artifact.read_bytes())
    payload[-1] ^= 1
    artifact.write_bytes(payload)

    with pytest.raises(
        InventoryPositiveV3PerimeterForensicError,
        match="sanitized fixture integrity/replay failed",
    ):
        analyze_inventory_positive_v3_perimeter(fixture, git_head_sha=_HEAD)


@pytest.mark.parametrize("head", ["", "A" * 40, "0" * 39, "g" * 40])
def test_forensic_rejects_invalid_git_provenance(head: str) -> None:
    with pytest.raises(
        InventoryPositiveV3PerimeterForensicError,
        match="git_head_sha",
    ):
        analyze_inventory_positive_v3_perimeter(_FIXTURE, git_head_sha=head)


def test_cli_writes_canonical_json_and_sha256_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mining_automation.perception.inventory import (
        positive_v3_perimeter_forensics_cli as cli,
    )

    monkeypatch.setattr(cli, "_verify_clean_head", lambda _expected: _HEAD)
    output = tmp_path / "report"

    assert (
        main(
            [
                "--fixture",
                str(_FIXTURE),
                "--output",
                str(output),
                "--expected-head",
                _HEAD,
            ]
        )
        == 0
    )
    report_path = output / "inventory-positive-v3-perimeter-forensics.json"
    digest = hashlib.sha256(report_path.read_bytes()).hexdigest()
    assert (
        output / "inventory-positive-v3-perimeter-forensics.sha256"
    ).read_text(encoding="ascii") == f"{digest}  {report_path.name}\n"
