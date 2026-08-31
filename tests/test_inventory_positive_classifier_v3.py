from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.perception.inventory import classification as classification_module
from mining_automation.perception.inventory.classification import (
    InventoryClassificationError,
)
from mining_automation.perception.inventory.geometry import InventoryGridLayout
from mining_automation.perception.inventory.localization import InventoryFrameProfile
from mining_automation.perception.inventory.positive_classifier_v3 import (
    INVENTORY_POSITIVE_V3_CANDIDATE_CLASSIFIER_ID,
    INVENTORY_POSITIVE_V3_CANDIDATE_CLASSIFIER_VERSION,
    INVENTORY_POSITIVE_V3_CANDIDATE_DETECTOR_ID,
    INVENTORY_POSITIVE_V3_CANDIDATE_DETECTOR_VERSION,
    InventoryPositiveV3DevelopmentAnalyzer,
    _canonical_bytes,
    _computed_model_artifact_sha256,
)
from mining_automation.perception.inventory.positive_v2_evaluation import (
    _load_campaign,
)
from mining_automation.perception.inventory.positive_v3_evaluation import (
    InventoryPositiveV3EvaluationReport,
    evaluate_inventory_positive_v3,
)
from mining_automation.perception.inventory.positive_v3_prototypes import (
    FULL_SLOT_RGB_PROTOTYPE_OCCURRENCES,
    MODEL_ARTIFACT_SHA256,
    PROTOTYPE_SOURCE_REGION_SHA256S,
)
from mining_automation.perception.inventory.sanitized_replay import (
    _reconstructed_frame,
)

_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "perception"
    / "inventory-live-candidate-safety-bb0d0e3f7ff1c73b"
)
_HEAD = "c" * 40


@pytest.fixture(scope="module")
def report() -> InventoryPositiveV3EvaluationReport:
    return evaluate_inventory_positive_v3(_FIXTURE, git_head_sha=_HEAD)


@pytest.fixture(scope="module")
def analyzer_and_reference() -> tuple[InventoryPositiveV3DevelopmentAnalyzer, Frame]:
    campaign = _load_campaign(_FIXTURE)
    reference = _reconstructed_frame(
        campaign.reference_payload,
        campaign.profile.region,
        campaign.profile.frame_width,
        campaign.profile.frame_height,
        frame_id=1,
    )
    return InventoryPositiveV3DevelopmentAnalyzer(campaign.profile, reference), reference


def test_v3_has_distinct_descriptive_candidate_identity() -> None:
    assert INVENTORY_POSITIVE_V3_CANDIDATE_CLASSIFIER_ID == (
        "inventory-full-slot-exact-rgb-v3"
    )
    assert INVENTORY_POSITIVE_V3_CANDIDATE_CLASSIFIER_VERSION == "3.0.0"
    assert INVENTORY_POSITIVE_V3_CANDIDATE_DETECTOR_ID == (
        "inventory-positive-v3-development-candidate"
    )
    assert INVENTORY_POSITIVE_V3_CANDIDATE_DETECTOR_VERSION == "3.0.0"


def test_v3_configuration_id_binds_complete_model_configuration(
    analyzer_and_reference: tuple[InventoryPositiveV3DevelopmentAnalyzer, Frame],
) -> None:
    analyzer, _ = analyzer_and_reference
    identity = {
        "baseline_configuration_id": analyzer._baseline.configuration_id,
        "model_configuration": analyzer.model_configuration,
    }

    assert analyzer.configuration_id == (
        "inventory-positive-v3-development-"
        + hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    )


@pytest.mark.parametrize(
    "weight_name",
    ["_CHANGED_FRACTION_WEIGHT", "_MEAN_COLOR_DELTA_WEIGHT"],
)
def test_v3_configuration_identity_changes_with_each_raw_score_weight(
    analyzer_and_reference: tuple[InventoryPositiveV3DevelopmentAnalyzer, Frame],
    monkeypatch: pytest.MonkeyPatch,
    weight_name: str,
) -> None:
    analyzer, reference = analyzer_and_reference
    original = getattr(classification_module, weight_name)
    monkeypatch.setattr(classification_module, weight_name, original / 2.0)

    changed = InventoryPositiveV3DevelopmentAnalyzer(analyzer._profile, reference)

    assert changed.configuration_id != analyzer.configuration_id
    assert changed.model_configuration["raw_v1_policy"] != (
        analyzer.model_configuration["raw_v1_policy"]
    )


def test_v3_model_artifact_is_frozen_and_preserves_all_source_occurrences() -> None:
    assert _computed_model_artifact_sha256() == MODEL_ARTIFACT_SHA256
    assert len(FULL_SLOT_RGB_PROTOTYPE_OCCURRENCES) == 62
    assert len({(slot, digest) for slot, digest, _ in FULL_SLOT_RGB_PROTOTYPE_OCCURRENCES}) == 46
    assert len(PROTOTYPE_SOURCE_REGION_SHA256S) == 4
    assert {source for _, _, source in FULL_SLOT_RGB_PROTOTYPE_OCCURRENCES} == {
        source for source, _ in PROTOTYPE_SOURCE_REGION_SHA256S
    }


def test_v3_self_fit_real_corpus_has_exact_required_outcomes(
    report: InventoryPositiveV3EvaluationReport,
) -> None:
    assert report.development_regressions_passed
    assert [item.v3_development_actual["occupied_slots"] for item in report.cases] == [
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
        5,
        28,
        None,
        None,
        None,
        None,
    ]
    assert all(item.passed for item in report.cases)
    for item in report.cases:
        occupied_slots = item.expected["occupied_slots"]
        if not isinstance(occupied_slots, int):
            continue
        slots = item.v3_development_actual["slots"]
        assert isinstance(slots, list)
        for slot in slots[:occupied_slots]:
            assert slot["development_state"] == "occupied"
            assert slot["development_confidence"] == 1.0


def test_v3_unsafe_real_cases_each_have_an_unknown_path(
    report: InventoryPositiveV3EvaluationReport,
) -> None:
    for index in (4, 5, 6, 7, 12, 13, 14, 15):
        actual = report.cases[index].v3_development_actual
        assert actual["occupied_slots"] is None
        assert actual["label"] == "unknown"
        assert actual["confidence"] == 0.0
        assert actual["reason"]


def test_v3_every_adversarial_false_positive_is_a_permanent_regression(
    report: InventoryPositiveV3EvaluationReport,
) -> None:
    by_id = {item.case_id: item for item in report.adversarial_matrix}
    required = {
        "all-slot-perimeters",
        "checkerboard",
        "centered-twelve-by-twenty-four-stripe",
        "dense-blob-sparse-seeds",
        "distributed-subthreshold-low-noise",
        "edge-pressure",
        "exact-prototype-mask-color-swap",
        "shifted-five-pixel-cross",
        "four-pixel-lattice",
        "highlight-border",
        "known-prototype-all-slot-perimeters",
        "known-prototype-altered-perimeter",
        "neighbor-bleed-wide-boundary",
        "nine-pixel-boundary-ring",
        "one-slot-perimeter-only",
        "one-subthreshold-core-pixel",
        "one-subthreshold-perimeter-pixel",
        "rectangle-three-detached",
        "ring-center-spokes",
        "sixteen-blob-four-tendrils",
        "synthetic-clean-sprite-like",
        "thick-diagonal",
    }
    assert required <= set(by_id)
    assert all(by_id[name].passed for name in required)
    assert all(by_id[name].actual["occupied_slots"] is None for name in required)
    assert by_id["exact-reviewed-prototype"].actual["occupied_slots"] == 1
    assert by_id["exact-reviewed-prototype"].passed
    assert len(report.adversarial_matrix) == 23


def test_v3_perimeter_only_changes_cannot_publish_empty(
    report: InventoryPositiveV3EvaluationReport,
) -> None:
    by_id = {item.case_id: item for item in report.adversarial_matrix}
    for name in (
        "one-subthreshold-perimeter-pixel",
        "one-slot-perimeter-only",
        "all-slot-perimeters",
    ):
        actual = by_id[name].actual
        assert actual["label"] == "unknown"
        assert actual["occupied_slots"] is None
        if actual["slots"]:
            assert actual["slots"][0]["authoritative_pixels_accounted"] == 1024


def test_v3_gapped_exact_prototypes_fail_explicitly_instead_of_miscounting(
    analyzer_and_reference: tuple[InventoryPositiveV3DevelopmentAnalyzer, Frame],
) -> None:
    analyzer, reference = analyzer_and_reference
    campaign = _load_campaign(_FIXTURE)
    full = _reconstructed_frame(
        campaign.cases[3].payload,
        campaign.profile.region,
        campaign.profile.frame_width,
        campaign.profile.frame_height,
        frame_id=102,
    )
    payload = bytearray(full.payload)
    slot_zero = campaign.profile.layout.slot_region(campaign.profile.region, 0)
    for y in range(slot_zero.y, slot_zero.y + slot_zero.height):
        for x in range(slot_zero.x, slot_zero.x + slot_zero.width):
            offset = (y * full.width + x) * 4
            payload[offset : offset + 4] = reference.payload[offset : offset + 4]
    gapped = Frame.from_raw(
        RawFrame(
            payload=bytes(payload),
            width=full.width,
            height=full.height,
            pixel_format=full.pixel_format,
        ),
        frame_id=103,
        captured_monotonic_s=103.0,
    )

    result = analyzer.analyze(gapped)

    assert result.occupied_slots is None
    assert result.label == "unknown"
    assert result.reason == "occupied_mask_not_row_major_prefix"
    assert result.slots[0].development_state.value == "empty"
    assert all(item.development_state.value == "occupied" for item in result.slots[1:])


def test_v3_external_guards_are_disjoint_from_every_owned_slot_pixel(
    analyzer_and_reference: tuple[InventoryPositiveV3DevelopmentAnalyzer, Frame],
) -> None:
    analyzer, _ = analyzer_and_reference
    profile = analyzer._profile
    owned_offsets = {
        (x - profile.region.x, y - profile.region.y)
        for slot in profile.layout.all_slot_regions(profile.region)
        for y in range(slot.y, slot.y + slot.height)
        for x in range(slot.x, slot.x + slot.width)
    }
    guard_offsets = set(analyzer._baseline._guard_offsets)
    row_guard_offsets = set(analyzer._baseline._row_guard_offsets)

    assert len(owned_offsets) == 28 * 32 * 32
    assert guard_offsets
    assert row_guard_offsets
    assert owned_offsets.isdisjoint(guard_offsets)
    assert owned_offsets.isdisjoint(row_guard_offsets)


def test_v3_model_identity_binds_external_guard_and_gapped_policy(
    analyzer_and_reference: tuple[InventoryPositiveV3DevelopmentAnalyzer, Frame],
) -> None:
    analyzer, _ = analyzer_and_reference
    assert analyzer.model_configuration["external_guard_policy"] == {
        "maximum_strong_changed_pixels": 0,
        "owned_slot_pixels_included": False,
        "pixel_difference_threshold": 24,
        "scope": "non-slot-and-row-gutter-pixels",
    }
    assert analyzer.model_configuration["slot_ensemble_policy"] == {
        "gapped_exact_occupancy": "explicit-unknown",
        "prefix_is_scene_or_presentation_authority": False,
        "rationale": (
            "the reviewed development corpus provides counts but not independent "
            "per-slot truth; a non-prefix exact ensemble cannot be safely counted"
        ),
    }
    assert analyzer.model_configuration["raw_v1_policy"] == {
        "classification_policy": {
            "core_inset": 4,
            "empty_max_score": 0.08,
            "max_guard_changed_fraction": 0.5,
            "max_row_guard_changed_fraction": 0.0,
            "minimum_slot_confidence": 0.5,
            "occupied_min_score": 0.22,
            "pixel_difference_threshold": 24,
        },
        "score_weights": {
            "changed_fraction": 0.7,
            "mean_color_delta": 0.3,
        },
    }
    full_slot_policy = analyzer.model_configuration["full_slot_policy"]
    assert isinstance(full_slot_policy, dict)
    assert full_slot_policy["exact_prototype_confidence"] == 1.0
    assert full_slot_policy["unknown_confidence"] == 0.0


def test_v3_report_discloses_zero_independent_validation(
    report: InventoryPositiveV3EvaluationReport,
) -> None:
    decoded = report.to_dict()
    assert decoded["activation_allowed"] is False
    assert decoded["validation_status"] == "independent-campaign-required"
    assert decoded["validation_case_ids"] == []
    assert decoded["independent_validation_case_count"] == 0
    assert decoded["generalization_unproven"] is True
    assert decoded["candidate_identity"] == {
        "classifier_id": "inventory-full-slot-exact-rgb-v3",
        "classifier_version": "3.0.0",
        "configuration_id": report.configuration_id,
        "detector_id": "inventory-positive-v3-development-candidate",
        "detector_version": "3.0.0",
        "implements_production_detector_protocol": False,
        "implements_production_slot_classifier_protocol": False,
    }
    assert decoded["development_corpus"]["prototype_source_and_evaluation_overlap"] is True  # type: ignore[index]
    assert decoded["prototype_coverage"]["unique_prototypes"] == 46  # type: ignore[index]
    assert decoded["prototype_coverage"]["leave_one_case_exact_coverage"] == {  # type: ignore[index]
        "first_full": "11/28",
        "first_partial": "1/1",
        "second_full": "15/28",
        "second_partial": "5/5",
    }


def test_v3_preserves_pinned_v1_and_frozen_v2_results(
    report: InventoryPositiveV3EvaluationReport,
) -> None:
    assert report.v1_results_sha256 == (
        "b830278ec55c75f8518db36aa24f4b1a519a47d5988dd846c33efba303d3cf24"
    )
    assert report.v2_results_sha256 == (
        "1db9b5f1408d1fb036ff5922ab8cd12f521af44b6858750ed4e4be289028abf9"
    )
    assert report.v1_detector["detector_id"] == "inventory-baseline"
    assert report.v2_detector["detector_id"] == "inventory-positive-v2"


def test_v3_analyzer_rejects_candidate_geometry_and_pixel_format_before_pixels(
    analyzer_and_reference: tuple[InventoryPositiveV3DevelopmentAnalyzer, Frame],
) -> None:
    analyzer, reference = analyzer_and_reference
    larger = Frame.from_raw(
        RawFrame(
            payload=bytes((reference.width + 1) * reference.height * 4),
            width=reference.width + 1,
            height=reference.height,
            pixel_format=PixelFormat.BGRA8888,
        ),
        frame_id=99,
        captured_monotonic_s=99.0,
    )
    wrong_format = Frame.from_raw(
        RawFrame(
            payload=reference.payload,
            width=reference.width,
            height=reference.height,
            pixel_format=PixelFormat.RGBA8888,
        ),
        frame_id=100,
        captured_monotonic_s=100.0,
    )

    assert analyzer.analyze(larger).reason == "candidate_frame_geometry_not_source_owned"
    assert analyzer.analyze(wrong_format).reason == "candidate_pixel_format_not_source_owned"


@pytest.mark.parametrize("change", ["frame", "region", "column_stride", "row_stride"])
def test_v3_analyzer_rejects_same_id_altered_profile(
    analyzer_and_reference: tuple[InventoryPositiveV3DevelopmentAnalyzer, Frame],
    change: str,
) -> None:
    _, reference = analyzer_and_reference
    layout = InventoryGridLayout(
        profile_id="candidate-live-inventory-348867800b28a54e",
        column_stride=43 if change == "column_stride" else 42,
        row_stride=37 if change == "row_stride" else 36,
    )
    region = layout.region_at(568 if change == "region" else 567, 569)
    profile = InventoryFrameProfile(
        profile_id=layout.profile_id,
        frame_width=1006 if change == "frame" else 1005,
        frame_height=1078,
        region=region,
        layout=layout,
    )

    with pytest.raises(InventoryClassificationError, match="complete source-owned profile"):
        InventoryPositiveV3DevelopmentAnalyzer(profile, reference)


def test_v3_analyzer_rejects_altered_reference(
    analyzer_and_reference: tuple[InventoryPositiveV3DevelopmentAnalyzer, Frame],
) -> None:
    _, reference = analyzer_and_reference
    campaign = _load_campaign(_FIXTURE)
    payload = bytearray(reference.payload)
    offset = (campaign.profile.region.y * reference.width + campaign.profile.region.x) * 4
    payload[offset] ^= 1
    altered = Frame.from_raw(
        RawFrame(
            payload=bytes(payload),
            width=reference.width,
            height=reference.height,
            pixel_format=PixelFormat.BGRA8888,
        ),
        frame_id=101,
        captured_monotonic_s=101.0,
    )

    with pytest.raises(InventoryClassificationError, match="reviewed reference"):
        InventoryPositiveV3DevelopmentAnalyzer(campaign.profile, altered)


def test_v3_report_is_canonical_and_deterministic(
) -> None:
    first = evaluate_inventory_positive_v3(_FIXTURE, git_head_sha=_HEAD).to_json()
    second = evaluate_inventory_positive_v3(_FIXTURE, git_head_sha=_HEAD).to_json()

    assert first == second
    assert first.endswith("\n")
