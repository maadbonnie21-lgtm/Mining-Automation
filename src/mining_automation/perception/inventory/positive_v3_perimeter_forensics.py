"""Read-only pixel forensics for the inventory-positive V2 perimeter failure.

This module deliberately does not implement, configure, or activate a V3
classifier.  It explains the exact four slot-1 pixels that rejected the
second reviewed campaign. Signal classification is derived from pixel/hash
recurrence plus independently reviewed row-major-prefix occupancy. Operator
action/presentation labels are retained solely as non-authoritative comparison
context.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from .classification import ClassificationPolicy
from .sanitized_replay import (
    InventorySanitizedReplayError,
    replay_inventory_sanitized_fixture,
)

__all__ = [
    "InventoryPositiveV3PerimeterForensicError",
    "InventoryPositiveV3PerimeterForensicReport",
    "SignalClassification",
    "analyze_inventory_positive_v3_perimeter",
]


_REPORT_SCHEMA_VERSION: Final[int] = 1
_EXPECTED_DATASET_ID: Final[str] = (
    "inventory-live-candidate-safety-bb0d0e3f7ff1c73b"
)
_EXPECTED_MANIFEST_SHA256: Final[str] = (
    "2e518ce81dd291f8b7d055afad9ddc12acbc66e0e967845f8f2e548fe1644479"
)
_EXPECTED_CASE_COUNT: Final[int] = 16
_EXPECTED_SESSION_COUNT: Final[int] = 2
_TARGET_SLOT_INDEX: Final[int] = 1
_FAILURE_THRESHOLD: Final[int] = 61
_EXPECTED_FAILURE_PIXEL_COUNT: Final[int] = 4
_MIN_ARTWORK_COHORT_SIZE: Final[int] = 2
_MIN_DISTINCT_PANEL_HASHES: Final[int] = 2
_VALIDATION_STATUS: Final[str] = "independent-campaign-required"


class InventoryPositiveV3PerimeterForensicError(RuntimeError):
    """The frozen corpus could not support the requested forensic conclusion."""


class SignalClassification(StrEnum):
    """Pixel-evidence classifications available to the forensic report."""

    ARTWORK = "artwork"
    UI = "ui"
    AMBIGUOUS = "ambiguous"


RGB = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class PerimeterPixelEvidence:
    """One exact slot-local pixel implicated by the V2 failure."""

    slot_local_x: int
    slot_local_y: int
    reference_rgb: RGB
    candidate_rgb: RGB
    max_channel_delta: int
    inside_full_32x32_slot: bool
    inside_24x24_core: bool
    inside_4px_perimeter: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_rgb": list(self.candidate_rgb),
            "inside_24x24_core": self.inside_24x24_core,
            "inside_4px_perimeter": self.inside_4px_perimeter,
            "inside_full_32x32_slot": self.inside_full_32x32_slot,
            "max_channel_delta": self.max_channel_delta,
            "reference_rgb": list(self.reference_rgb),
            "slot_local_x": self.slot_local_x,
            "slot_local_y": self.slot_local_y,
        }


@dataclass(frozen=True, slots=True)
class PerimeterCaseComparison:
    """The four exact positions compared against one corpus case."""

    case_id: str
    session_ordinal: int
    panel_sha256: str
    slot_full_sha256: str
    slot_core_sha256: str
    slot_perimeter_sha256: str
    visibility: str
    selected_item_visible: bool
    hover_visible: bool
    drag_visible: bool
    quantity_text_visible: bool
    reviewed_occupied_slots: int | None
    pixels: tuple[PerimeterPixelEvidence, ...]
    target_signature_match: bool
    target_full_slot_match: bool
    target_core_match: bool
    target_perimeter_match: bool
    signal_classification: SignalClassification
    nearest_artwork_cohort_full_slot_sha256: str | None
    target_position_differences_from_nearest_artwork: tuple[tuple[int, int], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "classification": self.signal_classification.value,
            "classification_uses_operator_selected_labels": False,
            "classification_uses_reviewed_prefix_occupied_count": True,
            "panel_sha256": self.panel_sha256,
            "nearest_artwork_cohort": {
                "full_32x32_sha256": (
                    self.nearest_artwork_cohort_full_slot_sha256
                ),
                "target_position_differences": [
                    [x, y]
                    for x, y in self.target_position_differences_from_nearest_artwork
                ],
            },
            "pixels": [item.to_dict() for item in self.pixels],
            "review_context": {
                "drag_visible": self.drag_visible,
                "hover_visible": self.hover_visible,
                "occupied_slots": self.reviewed_occupied_slots,
                "quantity_text_visible": self.quantity_text_visible,
                "selected_item_visible": self.selected_item_visible,
                "visibility": self.visibility,
            },
            "session_ordinal": self.session_ordinal,
            "slot_hashes": {
                "core_24x24_sha256": self.slot_core_sha256,
                "full_32x32_sha256": self.slot_full_sha256,
                "perimeter_4px_sha256": self.slot_perimeter_sha256,
            },
            "target_matches": {
                "core_24x24": self.target_core_match,
                "four_pixel_signature": self.target_signature_match,
                "full_32x32_slot": self.target_full_slot_match,
                "perimeter_4px": self.target_perimeter_match,
            },
        }


@dataclass(frozen=True, slots=True)
class SlotContentCohortEvidence:
    """A byte-identical full-slot cohort classified without case-name labels."""

    full_slot_sha256: str
    core_sha256: str
    perimeter_sha256: str
    case_ids: tuple[str, ...]
    distinct_panel_sha256s: tuple[str, ...]
    reviewed_prefix_occupied_support_count: int
    classification: SignalClassification

    def to_dict(self) -> dict[str, object]:
        return {
            "case_ids": list(self.case_ids),
            "classification": self.classification.value,
            "distinct_panel_sha256s": list(self.distinct_panel_sha256s),
            "reviewed_prefix_occupied_support_count": (
                self.reviewed_prefix_occupied_support_count
            ),
            "slot_hashes": {
                "core_24x24_sha256": self.core_sha256,
                "full_32x32_sha256": self.full_slot_sha256,
                "perimeter_4px_sha256": self.perimeter_sha256,
            },
        }


@dataclass(frozen=True, slots=True)
class InventoryPositiveV3PerimeterForensicReport:
    """Canonical, read-only evidence for the slot-1 perimeter root cause."""

    git_head_sha: str
    fixture_dataset_id: str
    fixture_manifest_sha256: str
    reference_case_id: str
    target_session_ordinal: int
    target_slot_index: int
    slot_size: int
    core_inset: int
    failure_threshold: int
    target_cohort_case_ids: tuple[str, ...]
    target_panel_sha256s: tuple[str, ...]
    reference_full_slot_sha256: str
    reference_core_sha256: str
    reference_perimeter_sha256: str
    target_full_slot_sha256: str
    target_core_sha256: str
    target_perimeter_sha256: str
    target_pixels: tuple[PerimeterPixelEvidence, ...]
    slot_content_cohorts: tuple[SlotContentCohortEvidence, ...]
    cases: tuple[PerimeterCaseComparison, ...]
    conclusion: SignalClassification

    @property
    def report_sha256(self) -> str:
        return _sha256(self.to_json().encode("utf-8"))

    def to_dict(self) -> dict[str, object]:
        return {
            "activation_allowed": False,
            "cases": [item.to_dict() for item in self.cases],
            "classification": {
                "basis": [
                    "largest recurrent non-reference full-slot byte cohort in "
                    "the second campaign",
                    "identical full/core/perimeter slot hashes across the cohort",
                    "identical four-pixel RGB signature across the cohort",
                    "multiple distinct whole-panel hashes with one unchanged slot",
                    "independently reviewed row-major-prefix occupied count places "
                    "slot 1 inside the occupied prefix in multiple cohort cases; "
                    "this associates owned content with occupancy but does not "
                    "establish whole-presentation legitimacy",
                    "target full/core/perimeter hashes all differ from the empty "
                    "reference",
                ],
                "classification_uses_case_names": False,
                "classification_uses_operator_selected_labels": False,
                "classification_uses_reviewed_prefix_occupied_count": True,
                "prefix_establishes_presentation_legitimacy": False,
                "meaning_of_artwork": (
                    "stable authoritative 32x32 slot content, including owned "
                    "perimeter pixels, recurrent with independently reviewed "
                    "row-major-prefix occupancy; not a claim about item identity "
                    "or whole-presentation legitimacy"
                ),
                "nonmatching_case_policy": SignalClassification.AMBIGUOUS.value,
                "ui_is_not_inferred_from_four_pixels_alone": True,
                "value": self.conclusion.value,
            },
            "fixture": {
                "dataset_id": self.fixture_dataset_id,
                "manifest_sha256": self.fixture_manifest_sha256,
            },
            "git_head_sha": self.git_head_sha,
            "generalization_unproven": True,
            "independent_validation_case_count": 0,
            "pixel_ownership": {
                "core_inset": self.core_inset,
                "core_size": self.slot_size - 2 * self.core_inset,
                "slot_size": self.slot_size,
            },
            "production_authority": False,
            "reference": {
                "case_id": self.reference_case_id,
                "slot_hashes": {
                    "core_24x24_sha256": self.reference_core_sha256,
                    "full_32x32_sha256": self.reference_full_slot_sha256,
                    "perimeter_4px_sha256": self.reference_perimeter_sha256,
                },
            },
            "report_schema_version": _REPORT_SCHEMA_VERSION,
            "slot_content_cohorts": [
                item.to_dict() for item in self.slot_content_cohorts
            ],
            "target": {
                "cohort_case_ids": list(self.target_cohort_case_ids),
                "distinct_panel_sha256s": list(self.target_panel_sha256s),
                "failure_threshold_max_channel_delta": self.failure_threshold,
                "pixels": [item.to_dict() for item in self.target_pixels],
                "session_ordinal": self.target_session_ordinal,
                "slot_hashes": {
                    "core_24x24_sha256": self.target_core_sha256,
                    "full_32x32_sha256": self.target_full_slot_sha256,
                    "perimeter_4px_sha256": self.target_perimeter_sha256,
                },
                "slot_index": self.target_slot_index,
            },
            "tool_kind": "inventory-positive-v3-slot-perimeter-forensics",
            "validation_case_ids": [],
            "validation_status": _VALIDATION_STATUS,
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())


@dataclass(frozen=True, slots=True)
class _Case:
    case_id: str
    session_id: str
    payload: bytes
    panel_sha256: str
    visibility: str
    selected_item_visible: bool
    hover_visible: bool
    drag_visible: bool
    quantity_text_visible: bool
    occupied_slots: int | None


def analyze_inventory_positive_v3_perimeter(
    fixture_directory: Path,
    *,
    git_head_sha: str,
) -> InventoryPositiveV3PerimeterForensicReport:
    """Explain the four V2 perimeter failures without changing any detector."""
    _validate_git_head(git_head_sha)
    manifest_path = fixture_directory / "manifest.json"
    manifest_before = _read_bytes(manifest_path, "fixture manifest")
    try:
        replay = replay_inventory_sanitized_fixture(fixture_directory)
    except InventorySanitizedReplayError as exc:
        raise InventoryPositiveV3PerimeterForensicError(
            f"sanitized fixture integrity/replay failed: {exc}"
        ) from exc
    if _read_bytes(manifest_path, "fixture manifest") != manifest_before:
        raise InventoryPositiveV3PerimeterForensicError(
            "fixture manifest changed during forensic analysis"
        )
    if replay.fixture_manifest_sha256 != _sha256(manifest_before):
        raise InventoryPositiveV3PerimeterForensicError(
            "verified replay and forensic manifest identities differ"
        )
    if (
        replay.dataset_id != _EXPECTED_DATASET_ID
        or replay.fixture_manifest_sha256 != _EXPECTED_MANIFEST_SHA256
    ):
        raise InventoryPositiveV3PerimeterForensicError(
            "forensic analysis is pinned to the exact reviewed 16-case dataset "
            "and manifest"
        )

    manifest = _json_object(manifest_before, "fixture manifest")
    cases, sessions = _load_cases(fixture_directory, manifest)
    if _read_bytes(manifest_path, "fixture manifest") != manifest_before:
        raise InventoryPositiveV3PerimeterForensicError(
            "fixture manifest changed while forensic case bytes were loaded"
        )
    if len(cases) != _EXPECTED_CASE_COUNT:
        raise InventoryPositiveV3PerimeterForensicError(
            f"forensic corpus requires exactly {_EXPECTED_CASE_COUNT} cases"
        )
    if len(sessions) != _EXPECTED_SESSION_COUNT:
        raise InventoryPositiveV3PerimeterForensicError(
            f"forensic corpus requires exactly {_EXPECTED_SESSION_COUNT} sessions"
        )

    candidate = _required_object(manifest, "candidate")
    evidence = _required_object(candidate, "evidence")
    reference_case_id = (
        _required_text(evidence, "reference_session_id")
        + "/"
        + _required_text(evidence, "reference_capture_id")
    )
    references = tuple(item for item in cases if item.case_id == reference_case_id)
    if len(references) != 1:
        raise InventoryPositiveV3PerimeterForensicError(
            "forensic corpus cannot resolve the exact empty reference case"
        )
    reference = references[0]

    profile = _required_object(candidate, "profile")
    slot_size = _required_positive_int(profile, "slot_size")
    columns = _required_positive_int(profile, "columns")
    column_stride = _required_positive_int(profile, "column_stride")
    row_stride = _required_positive_int(profile, "row_stride")
    reconstruction = _required_object(manifest, "frame_reconstruction")
    region_value = reconstruction.get("region")
    if (
        not isinstance(region_value, list)
        or len(region_value) != 4
        or any(not isinstance(item, int) or isinstance(item, bool) for item in region_value)
    ):
        raise InventoryPositiveV3PerimeterForensicError(
            "frame reconstruction region must contain four integers"
        )
    region_width = region_value[2]
    region_height = region_value[3]
    if region_width <= 0 or region_height <= 0:
        raise InventoryPositiveV3PerimeterForensicError(
            "frame reconstruction region must be positive"
        )
    expected_length = region_width * region_height * 4
    if any(len(item.payload) != expected_length for item in cases):
        raise InventoryPositiveV3PerimeterForensicError(
            "forensic case payload length differs from declared region"
        )

    core_inset = ClassificationPolicy().core_inset
    slot_x, slot_y = _slot_origin(
        _TARGET_SLOT_INDEX,
        columns=columns,
        column_stride=column_stride,
        row_stride=row_stride,
    )
    reference_hashes = _slot_hashes(
        reference.payload,
        panel_width=region_width,
        slot_x=slot_x,
        slot_y=slot_y,
        slot_size=slot_size,
        core_inset=core_inset,
    )

    second_session_cases = tuple(
        item for item in cases if item.session_id == sessions[1]
    )
    cohort = _largest_non_reference_slot_cohort(
        second_session_cases,
        reference_full_hash=reference_hashes[0],
        panel_width=region_width,
        slot_x=slot_x,
        slot_y=slot_y,
        slot_size=slot_size,
        core_inset=core_inset,
    )
    target_hashes = _slot_hashes(
        cohort[0].payload,
        panel_width=region_width,
        slot_x=slot_x,
        slot_y=slot_y,
        slot_size=slot_size,
        core_inset=core_inset,
    )
    target_pixels = _failure_pixels(
        reference.payload,
        cohort[0].payload,
        panel_width=region_width,
        slot_x=slot_x,
        slot_y=slot_y,
        slot_size=slot_size,
        core_inset=core_inset,
        threshold=_FAILURE_THRESHOLD,
    )
    if len(target_pixels) != _EXPECTED_FAILURE_PIXEL_COUNT:
        raise InventoryPositiveV3PerimeterForensicError(
            "slot-1 recurrent cohort must contain exactly four D>=61 perimeter pixels; "
            f"found {len(target_pixels)}"
        )
    target_signature = tuple(item.candidate_rgb for item in target_pixels)
    target_coordinates = tuple(
        (item.slot_local_x, item.slot_local_y) for item in target_pixels
    )
    panel_hashes = tuple(sorted({item.panel_sha256 for item in cohort}))
    reviewed_occupied_support = sum(
        item.occupied_slots is not None
        and item.occupied_slots > _TARGET_SLOT_INDEX
        for item in cohort
    )
    conclusion = _classify_target_signal(
        cohort_size=len(cohort),
        distinct_panel_hash_count=len(panel_hashes),
        reviewed_prefix_occupied_support_count=reviewed_occupied_support,
        reference_hashes=reference_hashes,
        target_hashes=target_hashes,
        target_pixels=target_pixels,
    )
    if conclusion is not SignalClassification.ARTWORK:
        raise InventoryPositiveV3PerimeterForensicError(
            "pixel-only evidence did not justify the required artwork conclusion"
        )

    slot_content_cohorts = _slot_content_cohorts(
        cases,
        reference_hashes=reference_hashes,
        panel_width=region_width,
        slot_x=slot_x,
        slot_y=slot_y,
        slot_size=slot_size,
        core_inset=core_inset,
    )
    artwork_signatures = {
        item.full_slot_sha256: _coordinate_signature(
            next(
                case
                for case in cases
                if _slot_hashes(
                    case.payload,
                    panel_width=region_width,
                    slot_x=slot_x,
                    slot_y=slot_y,
                    slot_size=slot_size,
                    core_inset=core_inset,
                )[0]
                == item.full_slot_sha256
            ).payload,
            panel_width=region_width,
            slot_x=slot_x,
            slot_y=slot_y,
            coordinates=target_coordinates,
        )
        for item in slot_content_cohorts
        if item.classification is SignalClassification.ARTWORK
    }
    comparisons = tuple(
        _case_comparison(
            item,
            session_ordinal=sessions.index(item.session_id) + 1,
            reference=reference,
            panel_width=region_width,
            slot_x=slot_x,
            slot_y=slot_y,
            slot_size=slot_size,
            core_inset=core_inset,
            target_coordinates=target_coordinates,
            target_signature=target_signature,
            target_hashes=target_hashes,
            artwork_signatures=artwork_signatures,
        )
        for item in cases
    )
    return InventoryPositiveV3PerimeterForensicReport(
        git_head_sha=git_head_sha,
        fixture_dataset_id=replay.dataset_id,
        fixture_manifest_sha256=replay.fixture_manifest_sha256,
        reference_case_id=reference_case_id,
        target_session_ordinal=2,
        target_slot_index=_TARGET_SLOT_INDEX,
        slot_size=slot_size,
        core_inset=core_inset,
        failure_threshold=_FAILURE_THRESHOLD,
        target_cohort_case_ids=tuple(item.case_id for item in cohort),
        target_panel_sha256s=panel_hashes,
        reference_full_slot_sha256=reference_hashes[0],
        reference_core_sha256=reference_hashes[1],
        reference_perimeter_sha256=reference_hashes[2],
        target_full_slot_sha256=target_hashes[0],
        target_core_sha256=target_hashes[1],
        target_perimeter_sha256=target_hashes[2],
        target_pixels=target_pixels,
        slot_content_cohorts=slot_content_cohorts,
        cases=comparisons,
        conclusion=conclusion,
    )


def _classify_target_signal(
    *,
    cohort_size: int,
    distinct_panel_hash_count: int,
    reviewed_prefix_occupied_support_count: int,
    reference_hashes: tuple[str, str, str],
    target_hashes: tuple[str, str, str],
    target_pixels: tuple[PerimeterPixelEvidence, ...],
) -> SignalClassification:
    """Classify from slot/panel bytes only; no review labels are accepted."""
    if (
        cohort_size >= _MIN_ARTWORK_COHORT_SIZE
        and distinct_panel_hash_count >= _MIN_DISTINCT_PANEL_HASHES
        and reviewed_prefix_occupied_support_count >= _MIN_ARTWORK_COHORT_SIZE
        and all(target != reference for target, reference in zip(target_hashes, reference_hashes, strict=True))
        and target_pixels
        and all(
            item.inside_full_32x32_slot
            and item.inside_4px_perimeter
            and not item.inside_24x24_core
            and item.max_channel_delta >= _FAILURE_THRESHOLD
            for item in target_pixels
        )
    ):
        return SignalClassification.ARTWORK
    return SignalClassification.AMBIGUOUS


def _case_comparison(
    item: _Case,
    *,
    session_ordinal: int,
    reference: _Case,
    panel_width: int,
    slot_x: int,
    slot_y: int,
    slot_size: int,
    core_inset: int,
    target_coordinates: tuple[tuple[int, int], ...],
    target_signature: tuple[RGB, ...],
    target_hashes: tuple[str, str, str],
    artwork_signatures: Mapping[str, tuple[RGB, ...]],
) -> PerimeterCaseComparison:
    hashes = _slot_hashes(
        item.payload,
        panel_width=panel_width,
        slot_x=slot_x,
        slot_y=slot_y,
        slot_size=slot_size,
        core_inset=core_inset,
    )
    pixels = tuple(
        _pixel_evidence(
            reference.payload,
            item.payload,
            panel_width=panel_width,
            slot_x=slot_x,
            slot_y=slot_y,
            slot_size=slot_size,
            core_inset=core_inset,
            local_x=x,
            local_y=y,
        )
        for x, y in target_coordinates
    )
    signature_match = tuple(pixel.candidate_rgb for pixel in pixels) == target_signature
    case_signature = tuple(pixel.candidate_rgb for pixel in pixels)
    nearest_hash: str | None = None
    nearest_differences: tuple[tuple[int, int], ...] = ()
    if artwork_signatures:
        ranked = sorted(
            (
                sum(
                    candidate != artwork
                    for candidate, artwork in zip(
                        case_signature, signature, strict=True
                    )
                ),
                full_hash,
                tuple(
                    coordinate
                    for coordinate, candidate, artwork in zip(
                        target_coordinates,
                        case_signature,
                        signature,
                        strict=True,
                    )
                    if candidate != artwork
                ),
            )
            for full_hash, signature in artwork_signatures.items()
        )
        _difference_count, nearest_hash, nearest_differences = ranked[0]
    classification = (
        SignalClassification.ARTWORK
        if hashes[0] in artwork_signatures
        else SignalClassification.AMBIGUOUS
    )
    return PerimeterCaseComparison(
        case_id=item.case_id,
        session_ordinal=session_ordinal,
        panel_sha256=item.panel_sha256,
        slot_full_sha256=hashes[0],
        slot_core_sha256=hashes[1],
        slot_perimeter_sha256=hashes[2],
        visibility=item.visibility,
        selected_item_visible=item.selected_item_visible,
        hover_visible=item.hover_visible,
        drag_visible=item.drag_visible,
        quantity_text_visible=item.quantity_text_visible,
        reviewed_occupied_slots=item.occupied_slots,
        pixels=pixels,
        target_signature_match=signature_match,
        target_full_slot_match=hashes[0] == target_hashes[0],
        target_core_match=hashes[1] == target_hashes[1],
        target_perimeter_match=hashes[2] == target_hashes[2],
        signal_classification=classification,
        nearest_artwork_cohort_full_slot_sha256=nearest_hash,
        target_position_differences_from_nearest_artwork=nearest_differences,
    )


def _slot_content_cohorts(
    cases: tuple[_Case, ...],
    *,
    reference_hashes: tuple[str, str, str],
    panel_width: int,
    slot_x: int,
    slot_y: int,
    slot_size: int,
    core_inset: int,
) -> tuple[SlotContentCohortEvidence, ...]:
    grouped: defaultdict[tuple[str, str, str], list[_Case]] = defaultdict(list)
    for item in cases:
        hashes = _slot_hashes(
            item.payload,
            panel_width=panel_width,
            slot_x=slot_x,
            slot_y=slot_y,
            slot_size=slot_size,
            core_inset=core_inset,
        )
        grouped[hashes].append(item)
    cohorts: list[SlotContentCohortEvidence] = []
    for hashes, items in sorted(grouped.items()):
        panels = tuple(sorted({item.panel_sha256 for item in items}))
        reviewed_support = sum(
            item.occupied_slots is not None
            and item.occupied_slots > _TARGET_SLOT_INDEX
            for item in items
        )
        classification = (
            SignalClassification.ARTWORK
            if (
                hashes != reference_hashes
                and len(items) >= _MIN_ARTWORK_COHORT_SIZE
                and len(panels) >= _MIN_DISTINCT_PANEL_HASHES
                and reviewed_support >= _MIN_ARTWORK_COHORT_SIZE
            )
            else SignalClassification.AMBIGUOUS
        )
        cohorts.append(
            SlotContentCohortEvidence(
                full_slot_sha256=hashes[0],
                core_sha256=hashes[1],
                perimeter_sha256=hashes[2],
                case_ids=tuple(item.case_id for item in items),
                distinct_panel_sha256s=panels,
                reviewed_prefix_occupied_support_count=reviewed_support,
                classification=classification,
            )
        )
    return tuple(cohorts)


def _coordinate_signature(
    payload: bytes,
    *,
    panel_width: int,
    slot_x: int,
    slot_y: int,
    coordinates: tuple[tuple[int, int], ...],
) -> tuple[RGB, ...]:
    return tuple(
        _read_rgb(
            payload,
            panel_width=panel_width,
            x=slot_x + local_x,
            y=slot_y + local_y,
        )
        for local_x, local_y in coordinates
    )


def _largest_non_reference_slot_cohort(
    cases: tuple[_Case, ...],
    *,
    reference_full_hash: str,
    panel_width: int,
    slot_x: int,
    slot_y: int,
    slot_size: int,
    core_inset: int,
) -> tuple[_Case, ...]:
    groups: defaultdict[str, list[_Case]] = defaultdict(list)
    for item in cases:
        full_hash = _slot_hashes(
            item.payload,
            panel_width=panel_width,
            slot_x=slot_x,
            slot_y=slot_y,
            slot_size=slot_size,
            core_inset=core_inset,
        )[0]
        if full_hash != reference_full_hash:
            groups[full_hash].append(item)
    if not groups:
        raise InventoryPositiveV3PerimeterForensicError(
            "second campaign contains no recurrent non-reference slot content"
        )
    largest_size = max(len(items) for items in groups.values())
    largest = tuple(items for items in groups.values() if len(items) == largest_size)
    if len(largest) != 1 or largest_size < _MIN_ARTWORK_COHORT_SIZE:
        raise InventoryPositiveV3PerimeterForensicError(
            "second-campaign recurrent slot cohort is absent or tied"
        )
    return tuple(largest[0])


def _failure_pixels(
    reference: bytes,
    candidate: bytes,
    *,
    panel_width: int,
    slot_x: int,
    slot_y: int,
    slot_size: int,
    core_inset: int,
    threshold: int,
) -> tuple[PerimeterPixelEvidence, ...]:
    pixels: list[PerimeterPixelEvidence] = []
    for local_y in range(slot_size):
        for local_x in range(slot_size):
            inside_core = _inside_core(
                local_x,
                local_y,
                slot_size=slot_size,
                core_inset=core_inset,
            )
            if inside_core:
                continue
            evidence = _pixel_evidence(
                reference,
                candidate,
                panel_width=panel_width,
                slot_x=slot_x,
                slot_y=slot_y,
                slot_size=slot_size,
                core_inset=core_inset,
                local_x=local_x,
                local_y=local_y,
            )
            if evidence.max_channel_delta >= threshold:
                pixels.append(evidence)
    return tuple(pixels)


def _pixel_evidence(
    reference: bytes,
    candidate: bytes,
    *,
    panel_width: int,
    slot_x: int,
    slot_y: int,
    slot_size: int,
    core_inset: int,
    local_x: int,
    local_y: int,
) -> PerimeterPixelEvidence:
    reference_rgb = _read_rgb(
        reference,
        panel_width=panel_width,
        x=slot_x + local_x,
        y=slot_y + local_y,
    )
    candidate_rgb = _read_rgb(
        candidate,
        panel_width=panel_width,
        x=slot_x + local_x,
        y=slot_y + local_y,
    )
    inside_core = _inside_core(
        local_x,
        local_y,
        slot_size=slot_size,
        core_inset=core_inset,
    )
    return PerimeterPixelEvidence(
        slot_local_x=local_x,
        slot_local_y=local_y,
        reference_rgb=reference_rgb,
        candidate_rgb=candidate_rgb,
        max_channel_delta=max(
            abs(candidate_channel - reference_channel)
            for candidate_channel, reference_channel in zip(
                candidate_rgb, reference_rgb, strict=True
            )
        ),
        inside_full_32x32_slot=(
            0 <= local_x < slot_size and 0 <= local_y < slot_size
        ),
        inside_24x24_core=inside_core,
        inside_4px_perimeter=not inside_core,
    )


def _slot_hashes(
    payload: bytes,
    *,
    panel_width: int,
    slot_x: int,
    slot_y: int,
    slot_size: int,
    core_inset: int,
) -> tuple[str, str, str]:
    full = bytearray()
    core = bytearray()
    perimeter = bytearray()
    for local_y in range(slot_size):
        for local_x in range(slot_size):
            offset = ((slot_y + local_y) * panel_width + slot_x + local_x) * 4
            pixel = payload[offset : offset + 4]
            if len(pixel) != 4:
                raise InventoryPositiveV3PerimeterForensicError(
                    "slot pixel is outside the sanitized region"
                )
            full.extend(pixel)
            if _inside_core(
                local_x,
                local_y,
                slot_size=slot_size,
                core_inset=core_inset,
            ):
                core.extend(pixel)
            else:
                perimeter.extend(pixel)
    return _sha256(bytes(full)), _sha256(bytes(core)), _sha256(bytes(perimeter))


def _inside_core(
    local_x: int,
    local_y: int,
    *,
    slot_size: int,
    core_inset: int,
) -> bool:
    return (
        core_inset <= local_x < slot_size - core_inset
        and core_inset <= local_y < slot_size - core_inset
    )


def _read_rgb(payload: bytes, *, panel_width: int, x: int, y: int) -> RGB:
    offset = (y * panel_width + x) * 4
    pixel = payload[offset : offset + 4]
    if len(pixel) != 4:
        raise InventoryPositiveV3PerimeterForensicError(
            "forensic pixel is outside the sanitized region"
        )
    blue, green, red, _alpha = pixel
    return red, green, blue


def _slot_origin(
    index: int,
    *,
    columns: int,
    column_stride: int,
    row_stride: int,
) -> tuple[int, int]:
    row, column = divmod(index, columns)
    return column * column_stride, row * row_stride


def _load_cases(
    fixture_directory: Path,
    manifest: Mapping[str, object],
) -> tuple[tuple[_Case, ...], tuple[str, ...]]:
    values = manifest.get("cases")
    if not isinstance(values, list):
        raise InventoryPositiveV3PerimeterForensicError("cases must be an array")
    cases: list[_Case] = []
    sessions: list[str] = []
    for value in values:
        raw = _object_value(value, "case")
        case_id = _required_text(raw, "case_id")
        artifact = _required_object(raw, "frame_region")
        relative = _required_text(artifact, "path")
        payload_path = _owned_path(fixture_directory, relative)
        payload = _read_bytes(payload_path, "sanitized frame region")
        expected_sha = _required_sha256(artifact, "sha256")
        if _sha256(payload) != expected_sha:
            raise InventoryPositiveV3PerimeterForensicError(
                f"sanitized frame region SHA-256 mismatch: {case_id}"
            )
        truth = _required_object(raw, "review_truth")
        session_id = _required_text(truth, "session_id")
        capture_id = _required_text(truth, "capture_id")
        if case_id != f"{session_id}/{capture_id}":
            raise InventoryPositiveV3PerimeterForensicError(
                f"case and review identities differ: {case_id}"
            )
        if session_id not in sessions:
            sessions.append(session_id)
        occupied = truth.get("occupied_slots")
        if occupied is not None and (
            not isinstance(occupied, int) or isinstance(occupied, bool)
        ):
            raise InventoryPositiveV3PerimeterForensicError(
                f"reviewed occupied_slots is invalid: {case_id}"
            )
        cases.append(
            _Case(
                case_id=case_id,
                session_id=session_id,
                payload=payload,
                panel_sha256=expected_sha,
                visibility=_required_text(truth, "visibility"),
                selected_item_visible=_required_bool(
                    truth, "selected_item_visible"
                ),
                hover_visible=_required_bool(truth, "hover_visible"),
                drag_visible=_required_bool(truth, "drag_visible"),
                quantity_text_visible=_required_bool(
                    truth, "quantity_text_visible"
                ),
                occupied_slots=occupied,
            )
        )
    return tuple(cases), tuple(sessions)


def _validate_git_head(value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise InventoryPositiveV3PerimeterForensicError(
            "git_head_sha must be an exact lowercase 40-character Git SHA"
        )


def _owned_path(root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or "\\" in relative or any(
        part in ("", ".", "..") for part in candidate.parts
    ):
        raise InventoryPositiveV3PerimeterForensicError(
            "sanitized frame path must be portable and relative"
        )
    root_resolved = root.resolve()
    path = (root / candidate).resolve()
    try:
        path.relative_to(root_resolved)
    except ValueError as exc:
        raise InventoryPositiveV3PerimeterForensicError(
            "sanitized frame path escapes fixture root"
        ) from exc
    return path


def _json_object(data: bytes, label: str) -> dict[str, object]:
    try:
        value: object = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InventoryPositiveV3PerimeterForensicError(
            f"{label} is not strict UTF-8 JSON"
        ) from exc
    return _object_value(value, label)


def _object_value(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise InventoryPositiveV3PerimeterForensicError(
            f"{label} must be an object with string keys"
        )
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _required_object(mapping: Mapping[str, object], key: str) -> dict[str, object]:
    return _object_value(mapping.get(key), key)


def _required_text(mapping: Mapping[str, object], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise InventoryPositiveV3PerimeterForensicError(
            f"{key} must be a non-empty string"
        )
    return value


def _required_positive_int(mapping: Mapping[str, object], key: str) -> int:
    value = mapping.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise InventoryPositiveV3PerimeterForensicError(
            f"{key} must be a positive integer"
        )
    return value


def _required_bool(mapping: Mapping[str, object], key: str) -> bool:
    value = mapping.get(key)
    if not isinstance(value, bool):
        raise InventoryPositiveV3PerimeterForensicError(
            f"{key} must be a boolean"
        )
    return value


def _required_sha256(mapping: Mapping[str, object], key: str) -> str:
    value = _required_text(mapping, key)
    if len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise InventoryPositiveV3PerimeterForensicError(
            f"{key} must be a lowercase hexadecimal SHA-256"
        )
    return value


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise InventoryPositiveV3PerimeterForensicError(
            f"cannot read {label}: {path}: {exc}"
        ) from exc


def _canonical_json(value: object) -> str:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
