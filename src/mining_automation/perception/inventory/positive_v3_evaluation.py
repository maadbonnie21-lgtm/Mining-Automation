"""Canonical self-fit development evaluation for Inventory Positive V3."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from ...capture import Frame, PixelFormat, RawFrame
from .localization import InventoryFrameProfile
from .positive_classifier_v3 import (
    INVENTORY_POSITIVE_V3_CANDIDATE_CLASSIFIER_ID,
    INVENTORY_POSITIVE_V3_CANDIDATE_CLASSIFIER_VERSION,
    INVENTORY_POSITIVE_V3_CANDIDATE_DETECTOR_ID,
    INVENTORY_POSITIVE_V3_CANDIDATE_DETECTOR_VERSION,
    INVENTORY_POSITIVE_V3_VALIDATION_STATUS,
    InventoryPositiveV3DevelopmentAnalyzer,
    InventoryPositiveV3DevelopmentResult,
    _canonical_bytes,
    _full_slot_rgb,
)
from .positive_v2_evaluation import (
    InventoryPositiveV2EvaluationError,
    _Campaign,
    _canonical_json,
    _load_campaign,
    evaluate_inventory_positive_v2,
)
from .positive_v3_prototypes import (
    DEVELOPMENT_DATASET_ID,
    DEVELOPMENT_MANIFEST_SHA256,
    FULL_SLOT_RGB_PROTOTYPE_OCCURRENCES,
    MODEL_ARTIFACT_SHA256,
    PROTOTYPE_SOURCE_REGION_SHA256S,
)
from .sanitized_replay import _reconstructed_frame

INVENTORY_POSITIVE_V3_DEVELOPMENT_DATASET_ID: Final[str] = DEVELOPMENT_DATASET_ID
INVENTORY_POSITIVE_V3_DEVELOPMENT_MANIFEST_SHA256: Final[str] = (
    DEVELOPMENT_MANIFEST_SHA256
)
_REPORT_SCHEMA_VERSION: Final[int] = 1
_PINNED_V1_RESULTS_SHA256: Final[str] = (
    "b830278ec55c75f8518db36aa24f4b1a519a47d5988dd846c33efba303d3cf24"
)
_PINNED_V2_RESULTS_SHA256: Final[str] = (
    "1db9b5f1408d1fb036ff5922ab8cd12f521af44b6858750ed4e4be289028abf9"
)
_EXPECTED_ADVERSARIAL_CASE_IDS: Final[tuple[str, ...]] = (
    "exact-reviewed-prototype",
    "checkerboard",
    "four-pixel-lattice",
    "shifted-five-pixel-cross",
    "sixteen-blob-four-tendrils",
    "ring-center-spokes",
    "rectangle-three-detached",
    "nine-pixel-boundary-ring",
    "centered-twelve-by-twenty-four-stripe",
    "dense-blob-sparse-seeds",
    "thick-diagonal",
    "edge-pressure",
    "highlight-border",
    "distributed-subthreshold-low-noise",
    "one-subthreshold-core-pixel",
    "one-subthreshold-perimeter-pixel",
    "one-slot-perimeter-only",
    "all-slot-perimeters",
    "known-prototype-altered-perimeter",
    "known-prototype-all-slot-perimeters",
    "exact-prototype-mask-color-swap",
    "neighbor-bleed-wide-boundary",
    "synthetic-clean-sprite-like",
)


class InventoryPositiveV3EvaluationError(RuntimeError):
    """Pinned development evidence or V3 derivation was invalid."""


@dataclass(frozen=True, slots=True)
class InventoryPositiveV3CaseResult:
    case_id: str
    original_review_split: str
    reviewer_truth: Mapping[str, object]
    expected: Mapping[str, object]
    v1_actual: Mapping[str, object]
    v2_actual: Mapping[str, object]
    v3_development_actual: Mapping[str, object]
    passed: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "development_regression_only": True,
            "expected": dict(self.expected),
            "original_review_split": self.original_review_split,
            "passed": self.passed,
            "reviewer_truth": dict(self.reviewer_truth),
            "v1_actual": dict(self.v1_actual),
            "v2_actual": dict(self.v2_actual),
            "v3_development_actual": dict(self.v3_development_actual),
        }


@dataclass(frozen=True, slots=True)
class InventoryPositiveV3AdversarialResult:
    case_id: str
    expected_occupied_slots: int | None
    actual: Mapping[str, object]
    passed: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "actual": dict(self.actual),
            "case_id": self.case_id,
            "development_regression_only": True,
            "expected_occupied_slots": self.expected_occupied_slots,
            "passed": self.passed,
        }


@dataclass(frozen=True, slots=True)
class InventoryPositiveV3EvaluationReport:
    git_head_sha: str
    fixture_generator_head_sha: str | None
    model_configuration: Mapping[str, object]
    model_configuration_sha256: str
    configuration_id: str
    v1_detector: Mapping[str, object]
    v2_detector: Mapping[str, object]
    v1_results_sha256: str
    v2_results_sha256: str
    cases: tuple[InventoryPositiveV3CaseResult, ...]
    adversarial_matrix: tuple[InventoryPositiveV3AdversarialResult, ...]
    prototype_coverage: Mapping[str, object]

    @property
    def activation_allowed(self) -> bool:
        return False

    @property
    def validation_status(self) -> str:
        return INVENTORY_POSITIVE_V3_VALIDATION_STATUS

    @property
    def development_regressions_passed(self) -> bool:
        return (
            len(self.cases) == 16
            and all(item.passed for item in self.cases)
            and tuple(item.case_id for item in self.adversarial_matrix)
            == _EXPECTED_ADVERSARIAL_CASE_IDS
            and all(item.passed for item in self.adversarial_matrix)
        )

    def to_dict(self) -> dict[str, object]:
        outcome = "passes" if self.development_regressions_passed else "fails"
        return {
            "activation_allowed": False,
            "adversarial_matrix": {
                "cases": [item.to_dict() for item in self.adversarial_matrix],
                "passed": all(item.passed for item in self.adversarial_matrix),
                "source_profile_and_reference_only": True,
            },
            "candidate_identity": {
                "classifier_id": INVENTORY_POSITIVE_V3_CANDIDATE_CLASSIFIER_ID,
                "classifier_version": (
                    INVENTORY_POSITIVE_V3_CANDIDATE_CLASSIFIER_VERSION
                ),
                "configuration_id": self.configuration_id,
                "detector_id": INVENTORY_POSITIVE_V3_CANDIDATE_DETECTOR_ID,
                "detector_version": INVENTORY_POSITIVE_V3_CANDIDATE_DETECTOR_VERSION,
                "implements_production_detector_protocol": False,
                "implements_production_slot_classifier_protocol": False,
            },
            "cases": [item.to_dict() for item in self.cases],
            "development_conclusion": (
                f"The exact full-slot RGB development allowlist {outcome} its "
                "self-fit corpus and adversarial regressions. This is not evidence "
                "of generalization: both full cases have substantial unique "
                "prototype dependence. An independent campaign is required."
            ),
            "development_corpus": {
                "dataset_id": DEVELOPMENT_DATASET_ID,
                "generator_head_sha": self.fixture_generator_head_sha,
                "manifest_sha256": DEVELOPMENT_MANIFEST_SHA256,
                "role": "development-regression-and-prototype-source",
                "prototype_source_and_evaluation_overlap": True,
            },
            "development_regressions_passed": self.development_regressions_passed,
            "generalization_unproven": True,
            "git_head_sha": self.git_head_sha,
            "model": {
                "artifact_sha256": MODEL_ARTIFACT_SHA256,
                "configuration": dict(self.model_configuration),
                "configuration_sha256": self.model_configuration_sha256,
            },
            "independent_validation_case_count": 0,
            "prototype_coverage": dict(self.prototype_coverage),
            "report_schema_version": _REPORT_SCHEMA_VERSION,
            "v1_detector": dict(self.v1_detector),
            "v1_results_sha256": self.v1_results_sha256,
            "v2_detector": dict(self.v2_detector),
            "v2_results_sha256": self.v2_results_sha256,
            "validation_status": INVENTORY_POSITIVE_V3_VALIDATION_STATUS,
            "validation_case_ids": [],
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())


def evaluate_inventory_positive_v3(
    fixture_directory: Path,
    *,
    git_head_sha: str,
) -> InventoryPositiveV3EvaluationReport:
    _validate_git_sha(git_head_sha)
    try:
        v2_report = evaluate_inventory_positive_v2(
            fixture_directory,
            git_head_sha=git_head_sha,
        )
        campaign = _load_campaign(fixture_directory)
    except InventoryPositiveV2EvaluationError as exc:
        raise InventoryPositiveV3EvaluationError(
            f"V1/V2 fixture integrity or replay failed: {exc}"
        ) from exc
    if (
        campaign.v1_report.dataset_id != DEVELOPMENT_DATASET_ID
        or campaign.v1_report.fixture_manifest_sha256 != DEVELOPMENT_MANIFEST_SHA256
        or len(campaign.cases) != 16
    ):
        raise InventoryPositiveV3EvaluationError(
            "V3 requires the exact pinned 16-case development corpus"
        )
    reference = _reconstructed_frame(
        campaign.reference_payload,
        campaign.profile.region,
        campaign.profile.frame_width,
        campaign.profile.frame_height,
        frame_id=1,
    )
    _verify_prototype_derivation(campaign)
    analyzer = InventoryPositiveV3DevelopmentAnalyzer(campaign.profile, reference)
    v2_by_id = {item.case_id: item for item in v2_report.cases}
    v1_hash = hashlib.sha256(
        _canonical_bytes([dict(item.v1_actual) for item in v2_report.cases])
    ).hexdigest()
    v2_hash = hashlib.sha256(
        _canonical_bytes([dict(item.v2_actual) for item in v2_report.cases])
    ).hexdigest()
    if v1_hash != _PINNED_V1_RESULTS_SHA256 or v2_hash != _PINNED_V2_RESULTS_SHA256:
        raise InventoryPositiveV3EvaluationError(
            "V1 or frozen V2 behavior differs from the compatibility pin"
        )
    results: list[InventoryPositiveV3CaseResult] = []
    for frame_id, item in enumerate(campaign.cases, start=1):
        frame = _reconstructed_frame(
            item.payload,
            campaign.profile.region,
            campaign.profile.frame_width,
            campaign.profile.frame_height,
            frame_id=frame_id,
        )
        actual = analyzer.analyze(frame)
        expected = _expected(item.reviewer_truth)
        v2_case = v2_by_id.get(item.case_id)
        if v2_case is None:
            raise InventoryPositiveV3EvaluationError(
                f"V2 omitted pinned case: {item.case_id}"
            )
        split = item.reviewer_truth.get("validation_split")
        if not isinstance(split, str):
            raise InventoryPositiveV3EvaluationError("review split is missing")
        results.append(
            InventoryPositiveV3CaseResult(
                case_id=item.case_id,
                original_review_split=split,
                reviewer_truth=item.reviewer_truth,
                expected=expected,
                v1_actual=v2_case.v1_actual,
                v2_actual=v2_case.v2_actual,
                v3_development_actual=actual.to_dict(),
                passed=_matches(actual, expected),
            )
        )
    model = analyzer.model_configuration
    return InventoryPositiveV3EvaluationReport(
        git_head_sha=git_head_sha,
        fixture_generator_head_sha=campaign.v1_report.generator_head_sha,
        model_configuration=model,
        model_configuration_sha256=hashlib.sha256(_canonical_bytes(model)).hexdigest(),
        configuration_id=analyzer.configuration_id,
        v1_detector=v2_report.v1_detector,
        v2_detector=v2_report.v2_detector,
        v1_results_sha256=v1_hash,
        v2_results_sha256=v2_hash,
        cases=tuple(results),
        adversarial_matrix=_adversarial_matrix(
            analyzer,
            campaign.profile,
            reference,
            _reconstructed_frame(
                campaign.cases[2].payload,
                campaign.profile.region,
                campaign.profile.frame_width,
                campaign.profile.frame_height,
                frame_id=100,
            ),
        ),
        prototype_coverage=_prototype_coverage(),
    )


def _verify_prototype_derivation(campaign: _Campaign) -> None:
    cases = campaign.cases
    profile = campaign.profile
    source_hashes = dict(PROTOTYPE_SOURCE_REGION_SHA256S)
    derived: list[tuple[int, str, str]] = []
    for frame_id, item in enumerate(cases, start=1):
        source_sha = source_hashes.get(item.case_id)
        if source_sha is None:
            continue
        if item.payload_sha256 != source_sha:
            raise InventoryPositiveV3EvaluationError(
                f"prototype source payload changed: {item.case_id}"
            )
        truth = item.reviewer_truth
        occupied = truth.get("occupied_slots")
        if (
            not _is_clean(truth)
            or not isinstance(occupied, int)
            or isinstance(occupied, bool)
        ):
            raise InventoryPositiveV3EvaluationError(
                f"prototype source is not reviewer-approved clean positive: {item.case_id}"
            )
        frame = _reconstructed_frame(
            item.payload,
            profile.region,
            profile.frame_width,
            profile.frame_height,
            frame_id=frame_id,
        )
        slots = profile.layout.all_slot_regions(profile.region)
        derived.extend(
            (
                index,
                hashlib.sha256(_full_slot_rgb(frame, slots[index])).hexdigest(),
                item.case_id,
            )
            for index in range(occupied)
        )
    if tuple(derived) != FULL_SLOT_RGB_PROTOTYPE_OCCURRENCES:
        raise InventoryPositiveV3EvaluationError(
            "source-owned full-slot RGB prototypes differ from clean reviewed evidence"
        )


def _prototype_coverage() -> dict[str, object]:
    occurrences: dict[tuple[int, str], list[str]] = {}
    for slot_index, digest, source_case_id in FULL_SLOT_RGB_PROTOTYPE_OCCURRENCES:
        occurrences.setdefault((slot_index, digest), []).append(source_case_id)
    recurring = {key: values for key, values in occurrences.items() if len(values) > 1}
    cross_session = {
        key: values
        for key, values in recurring.items()
        if len({value.split("/", 1)[0] for value in values}) > 1
    }
    source_cases = tuple(dict.fromkeys(source for _, _, source in FULL_SLOT_RGB_PROTOTYPE_OCCURRENCES))
    leave_one: dict[str, str] = {}
    labels = ("first_partial", "first_full", "second_partial", "second_full")
    for label, source in zip(labels, source_cases, strict=True):
        source_occurrences = tuple(
            (slot, digest)
            for slot, digest, case_id in FULL_SLOT_RGB_PROTOTYPE_OCCURRENCES
            if case_id == source
        )
        covered = sum(
            any(case_id != source for case_id in occurrences[item])
            for item in source_occurrences
        )
        leave_one[label] = f"{covered}/{len(source_occurrences)}"
    result: dict[str, object] = {
        "clean_positive_slot_occurrences": len(FULL_SLOT_RGB_PROTOTYPE_OCCURRENCES),
        "cross_session_recurring_occurrences": sum(map(len, cross_session.values())),
        "cross_session_recurring_signatures": len(cross_session),
        "leave_one_case_exact_coverage": leave_one,
        "recurring_occurrences": sum(map(len, recurring.values())),
        "recurring_signatures": len(recurring),
        "self_fit": True,
        "unique_prototypes": len(occurrences),
    }
    expected = {
        "clean_positive_slot_occurrences": 62,
        "cross_session_recurring_occurrences": 20,
        "cross_session_recurring_signatures": 10,
        "leave_one_case_exact_coverage": {
            "first_full": "11/28",
            "first_partial": "1/1",
            "second_full": "15/28",
            "second_partial": "5/5",
        },
        "recurring_occurrences": 32,
        "recurring_signatures": 16,
        "self_fit": True,
        "unique_prototypes": 46,
    }
    if result != expected:
        raise InventoryPositiveV3EvaluationError(
            "prototype recurrence or leave-one-case evidence changed"
        )
    return result


def _expected(truth: Mapping[str, object]) -> dict[str, object]:
    occupied = truth.get("occupied_slots")
    if _is_clean(truth) and isinstance(occupied, int) and not isinstance(occupied, bool):
        return {
            "label": "empty" if occupied == 0 else "full" if occupied == 28 else "partial",
            "minimum_confidence": 0.8,
            "occupied_slots": occupied,
        }
    return {"confidence": 0.0, "label": "unknown", "occupied_slots": None}


def _is_clean(truth: Mapping[str, object]) -> bool:
    return truth.get("visibility") == "inventory-visible" and all(
        truth.get(name) is False
        for name in (
            "drag_visible",
            "hover_visible",
            "quantity_text_visible",
            "selected_item_visible",
        )
    )


def _matches(
    result: InventoryPositiveV3DevelopmentResult,
    expected: Mapping[str, object],
) -> bool:
    if expected.get("occupied_slots") is None:
        return (
            result.occupied_slots is None
            and result.label == "unknown"
            and result.confidence == 0.0
        )
    return (
        result.occupied_slots == expected.get("occupied_slots")
        and result.label == expected.get("label")
        and result.confidence >= 0.8
    )


def _validate_git_sha(value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise InventoryPositiveV3EvaluationError(
            "git_head_sha must be an exact lowercase 40-character Git SHA"
        )


def _adversarial_matrix(
    analyzer: InventoryPositiveV3DevelopmentAnalyzer,
    profile: InventoryFrameProfile,
    reference: Frame,
    exact_prototype: Frame,
) -> tuple[InventoryPositiveV3AdversarialResult, ...]:
    perimeter = {
        (x, y)
        for y in range(32)
        for x in range(32)
        if x < 4 or x >= 28 or y < 4 or y >= 28
    }
    patterns: list[tuple[str, Frame, int | None]] = [
        ("exact-reviewed-prototype", exact_prototype, 1),
        ("checkerboard", _mutated(reference, profile, {(x, y) for y in range(32) for x in range(32) if (x + y) % 2 == 0}), None),
        ("four-pixel-lattice", _mutated(reference, profile, {(x, y) for y in range(0, 32, 4) for x in range(32)} | {(x, y) for x in range(0, 32, 4) for y in range(32)}), None),
        ("shifted-five-pixel-cross", _mutated(reference, profile, {(x, y) for y in range(32) for x in range(32) if 7 <= x <= 11 or 14 <= y <= 18}), None),
        ("sixteen-blob-four-tendrils", _mutated(reference, profile, _blob_tendrils()), None),
        ("ring-center-spokes", _mutated(reference, profile, _ring_spokes()), None),
        ("rectangle-three-detached", _mutated(reference, profile, _rectangle(8, 4, 16, 24) | {(1, 1), (30, 1), (1, 30)}), None),
        ("nine-pixel-boundary-ring", _mutated(reference, profile, {(x, y) for y in range(32) for x in range(32) if x < 9 or x >= 23 or y < 9 or y >= 23}), None),
        ("centered-twelve-by-twenty-four-stripe", _mutated(reference, profile, _rectangle(10, 4, 12, 24)), None),
        ("dense-blob-sparse-seeds", _mutated(reference, profile, _rectangle(10, 10, 12, 12) | {(0, 0), (31, 0), (0, 31), (31, 31)}), None),
        ("thick-diagonal", _mutated(reference, profile, {(x, y) for y in range(32) for x in range(32) if abs(x - y) <= 3}), None),
        ("edge-pressure", _mutated(reference, profile, _rectangle(0, 0, 6, 32)), None),
        ("highlight-border", _mutated(reference, profile, perimeter), None),
        ("distributed-subthreshold-low-noise", _mutated_subthreshold(reference, profile, {(x, y) for y in range(0, 32, 2) for x in range(0, 32, 2)}), None),
        ("one-subthreshold-core-pixel", _mutated_subthreshold(reference, profile, {(16, 16)}), None),
        ("one-subthreshold-perimeter-pixel", _mutated_subthreshold(reference, profile, {(0, 0)}), None),
        ("one-slot-perimeter-only", _mutated(reference, profile, perimeter), None),
        ("all-slot-perimeters", _all_perimeters(reference, profile), None),
        ("known-prototype-altered-perimeter", _mutated(exact_prototype, profile, {(0, 0)}), None),
        ("known-prototype-all-slot-perimeters", _all_perimeters(exact_prototype, profile), None),
        ("exact-prototype-mask-color-swap", _color_swapped(exact_prototype, reference, profile), None),
        ("neighbor-bleed-wide-boundary", _external_pressure(exact_prototype, profile), None),
        ("synthetic-clean-sprite-like", _mutated(reference, profile, _rectangle(8, 4, 16, 24)), None),
    ]
    results: list[InventoryPositiveV3AdversarialResult] = []
    for case_id, frame, expected in patterns:
        actual = analyzer.analyze(frame)
        passed = (
            actual.occupied_slots == expected
            and (
                expected == 1
                and actual.label == "partial"
                and actual.confidence == 1.0
                or expected is None
                and actual.label == "unknown"
                and actual.confidence == 0.0
            )
        )
        results.append(
            InventoryPositiveV3AdversarialResult(
                case_id=case_id,
                expected_occupied_slots=expected,
                actual=actual.to_dict(),
                passed=passed,
            )
        )
    return tuple(results)


def _mutated(
    base: Frame,
    profile: InventoryFrameProfile,
    points: Iterable[tuple[int, int]],
) -> Frame:
    payload = bytearray(base.payload)
    slot = profile.layout.slot_region(profile.region, 0)
    for x, y in points:
        _invert_pixel(payload, base.width, slot.x + x, slot.y + y)
    return _frame(bytes(payload), base, 200)


def _all_perimeters(base: Frame, profile: InventoryFrameProfile) -> Frame:
    payload = bytearray(base.payload)
    for slot in profile.layout.all_slot_regions(profile.region):
        for x, y in _perimeter_points():
            _invert_pixel(payload, base.width, slot.x + x, slot.y + y)
    return _frame(bytes(payload), base, 201)


def _mutated_subthreshold(
    base: Frame,
    profile: InventoryFrameProfile,
    points: Iterable[tuple[int, int]],
) -> Frame:
    payload = bytearray(base.payload)
    slot = profile.layout.slot_region(profile.region, 0)
    for x, y in points:
        offset = ((slot.y + y) * base.width + slot.x + x) * 4
        value = payload[offset]
        payload[offset] = value + 10 if value <= 245 else value - 10
    return _frame(bytes(payload), base, 204)


def _external_pressure(base: Frame, profile: InventoryFrameProfile) -> Frame:
    payload = bytearray(base.payload)
    slot = profile.layout.slot_region(profile.region, 0)
    # One continuous band spans slot 0's right owned perimeter, the entire
    # ten-pixel inter-slot gutter, and slot 1's left owned perimeter.
    for y in range(slot.y, slot.y + 32):
        for x in range(slot.x + 28, slot.x + 46):
            _invert_pixel(payload, base.width, x, y)
    return _frame(bytes(payload), base, 202)


def _color_swapped(
    candidate: Frame,
    reference: Frame,
    profile: InventoryFrameProfile,
) -> Frame:
    before_mask = _slot_changed_mask(candidate, reference, profile)
    payload = bytearray(candidate.payload)
    slot = profile.layout.slot_region(profile.region, 0)
    for y in range(slot.y, slot.y + 32):
        for x in range(slot.x, slot.x + 32):
            offset = (y * candidate.width + x) * 4
            candidate_rgb = bytes(payload[offset : offset + 3])
            reference_rgb = reference.payload[offset : offset + 3]
            if (
                max(
                    abs(a - b)
                    for a, b in zip(candidate_rgb, reference_rgb, strict=True)
                )
                >= 24
            ):
                for replacement in (
                    bytes((0, 0, 0)),
                    bytes((255, 255, 255)),
                    bytes((255, 0, 255)),
                    bytes((0, 255, 0)),
                ):
                    if replacement == candidate_rgb:
                        continue
                    if max(
                        abs(a - b)
                        for a, b in zip(replacement, reference_rgb, strict=True)
                    ) < 24:
                        continue
                    payload[offset : offset + 3] = replacement
                    result = _frame(bytes(payload), candidate, 203)
                    if _slot_changed_mask(result, reference, profile) == before_mask:
                        return result
                    payload[offset : offset + 3] = candidate_rgb
    raise InventoryPositiveV3EvaluationError("prototype has no changed pixel")


def _slot_changed_mask(
    candidate: Frame,
    reference: Frame,
    profile: InventoryFrameProfile,
) -> tuple[bool, ...]:
    slot = profile.layout.slot_region(profile.region, 0)
    return tuple(
        max(
            abs(
                candidate.payload[(y * candidate.width + x) * 4 + channel]
                - reference.payload[(y * reference.width + x) * 4 + channel]
            )
            for channel in range(3)
        )
        >= 24
        for y in range(slot.y, slot.y + 32)
        for x in range(slot.x, slot.x + 32)
    )


def _invert_pixel(payload: bytearray, width: int, x: int, y: int) -> None:
    offset = (y * width + x) * 4
    for channel in range(3):
        payload[offset + channel] = 255 - payload[offset + channel]


def _frame(payload: bytes, template: Frame, frame_id: int) -> Frame:
    return Frame.from_raw(
        RawFrame(
            payload=payload,
            width=template.width,
            height=template.height,
            pixel_format=PixelFormat.BGRA8888,
        ),
        frame_id=frame_id,
        captured_monotonic_s=float(frame_id),
    )


def _perimeter_points() -> set[tuple[int, int]]:
    return {
        (x, y)
        for y in range(32)
        for x in range(32)
        if x < 4 or x >= 28 or y < 4 or y >= 28
    }


def _rectangle(x: int, y: int, width: int, height: int) -> set[tuple[int, int]]:
    return {
        (left, top)
        for top in range(y, y + height)
        for left in range(x, x + width)
    }


def _blob_tendrils() -> set[tuple[int, int]]:
    return (
        _rectangle(8, 8, 16, 16)
        | _rectangle(14, 0, 4, 8)
        | _rectangle(14, 24, 4, 8)
        | _rectangle(0, 14, 8, 4)
        | _rectangle(24, 14, 8, 4)
    )


def _ring_spokes() -> set[tuple[int, int]]:
    return (
        {(x, y) for y in range(32) for x in range(32) if x < 4 or x >= 28 or y < 4 or y >= 28}
        | _rectangle(14, 4, 4, 24)
        | _rectangle(4, 14, 24, 4)
        | _rectangle(12, 12, 8, 8)
    )
