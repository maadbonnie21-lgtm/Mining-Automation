"""Offline-only exact full-slot Inventory Positive V3 development analyzer.

This module deliberately exposes no Detector, InventorySlotClassifier, or
Observation implementation. Its results cannot enter the production adapter.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from ...capture import Frame, PixelFormat
from . import classification as _classification
from .classification import (
    ClassificationPolicy,
    CorePixels,
    InventoryClassificationError,
    InventoryObstructionError,
    ReferenceInventoryClassifier,
    SlotOccupancy,
    _read_rgb,
)
from .geometry import (
    INVENTORY_COLUMNS,
    INVENTORY_ROWS,
    INVENTORY_SLOT_SIZE,
    Region,
)
from .localization import InventoryFrameProfile
from .positive_v3_prototypes import (
    DEVELOPMENT_DATASET_ID,
    DEVELOPMENT_MANIFEST_SHA256,
    FULL_SLOT_RGB_PROTOTYPE_OCCURRENCES,
    MODEL_ARTIFACT_SHA256,
    PROTOTYPE_GENERATION_ALGORITHM,
    PROTOTYPE_SCHEMA,
    PROTOTYPE_SOURCE_REGION_SHA256S,
    SUPPORTED_COLUMN_STRIDE,
    SUPPORTED_COLUMNS,
    SUPPORTED_FRAME_HEIGHT,
    SUPPORTED_FRAME_WIDTH,
    SUPPORTED_PIXEL_FORMAT,
    SUPPORTED_PROFILE_ID,
    SUPPORTED_REFERENCE_RGB_SHA256,
    SUPPORTED_REGION,
    SUPPORTED_ROW_STRIDE,
    SUPPORTED_ROWS,
    SUPPORTED_SLOT_SIZE,
)

INVENTORY_POSITIVE_V3_CANDIDATE_CLASSIFIER_ID: Final[str] = (
    "inventory-full-slot-exact-rgb-v3"
)
INVENTORY_POSITIVE_V3_CANDIDATE_CLASSIFIER_VERSION: Final[str] = "3.0.0"
INVENTORY_POSITIVE_V3_CANDIDATE_DETECTOR_ID: Final[str] = (
    "inventory-positive-v3-development-candidate"
)
INVENTORY_POSITIVE_V3_CANDIDATE_DETECTOR_VERSION: Final[str] = "3.0.0"
INVENTORY_POSITIVE_V3_ANALYZER_ID: Final[str] = (
    "inventory-positive-v3-full-slot-exact-development"
)
INVENTORY_POSITIVE_V3_ANALYZER_VERSION: Final[str] = "3.0.0"
INVENTORY_POSITIVE_V3_VALIDATION_STATUS: Final[str] = (
    "independent-campaign-required"
)

_ACTIVATION_ALLOWED: Final[bool] = False
_PUBLICATION_FLOOR: Final[float] = 0.8
_STRONG_EXTERNAL_PIXEL_THRESHOLD: Final[int] = 24
_MAX_STRONG_EXTERNAL_CHANGED_PIXELS: Final[int] = 0
_EXACT_PROTOTYPE_CONFIDENCE: Final[float] = 1.0
_UNKNOWN_CONFIDENCE: Final[float] = 0.0
_MODEL_SCHEMA: Final[str] = "inventory-positive-v3-development-analyzer-v1"


def inventory_positive_v3_model_configuration() -> dict[str, object]:
    """Return the source-owned non-activating model contract."""
    policy = ClassificationPolicy()
    return {
        "activation_allowed": False,
        "analyzer_id": INVENTORY_POSITIVE_V3_ANALYZER_ID,
        "analyzer_version": INVENTORY_POSITIVE_V3_ANALYZER_VERSION,
        "candidate_classifier_id": INVENTORY_POSITIVE_V3_CANDIDATE_CLASSIFIER_ID,
        "candidate_classifier_version": (
            INVENTORY_POSITIVE_V3_CANDIDATE_CLASSIFIER_VERSION
        ),
        "candidate_detector_id": INVENTORY_POSITIVE_V3_CANDIDATE_DETECTOR_ID,
        "candidate_detector_version": INVENTORY_POSITIVE_V3_CANDIDATE_DETECTOR_VERSION,
        "development_evidence": {
            "dataset_id": DEVELOPMENT_DATASET_ID,
            "manifest_sha256": DEVELOPMENT_MANIFEST_SHA256,
            "self_fit_only": True,
        },
        "external_guard_policy": {
            "maximum_strong_changed_pixels": _MAX_STRONG_EXTERNAL_CHANGED_PIXELS,
            "owned_slot_pixels_included": False,
            "pixel_difference_threshold": _STRONG_EXTERNAL_PIXEL_THRESHOLD,
            "scope": "non-slot-and-row-gutter-pixels",
        },
        "full_slot_policy": {
            "authoritative_pixels_per_slot": 1024,
            "empty_requires_exact_reference_rgb": True,
            "exact_prototype_confidence": _EXACT_PROTOTYPE_CONFIDENCE,
            "occupied_requires_exact_prototype_rgb": True,
            "prototype_distance": "exact SHA-256 equality (distance 0 only)",
            "prototype_generation_algorithm": PROTOTYPE_GENERATION_ALGORITHM,
            "prototype_occurrences": [
                {
                    "full_slot_rgb_sha256": digest,
                    "slot_index": slot_index,
                    "source_case_id": source_case_id,
                }
                for slot_index, digest, source_case_id in (
                    FULL_SLOT_RGB_PROTOTYPE_OCCURRENCES
                )
            ],
            "prototype_schema": PROTOTYPE_SCHEMA,
            "unknown_confidence": _UNKNOWN_CONFIDENCE,
        },
        "model_artifact_sha256": MODEL_ARTIFACT_SHA256,
        "prototype_source_artifacts": [
            {
                "case_id": case_id,
                "sanitized_region_sha256": digest,
            }
            for case_id, digest in PROTOTYPE_SOURCE_REGION_SHA256S
        ],
        "profile_binding": {
            "profile_id": SUPPORTED_PROFILE_ID,
            "reference_region_rgb_sha256": SUPPORTED_REFERENCE_RGB_SHA256,
            "frame": {
                "height": SUPPORTED_FRAME_HEIGHT,
                "pixel_format": SUPPORTED_PIXEL_FORMAT,
                "width": SUPPORTED_FRAME_WIDTH,
            },
            "grid": {
                "column_stride": SUPPORTED_COLUMN_STRIDE,
                "columns": SUPPORTED_COLUMNS,
                "region": list(SUPPORTED_REGION),
                "row_stride": SUPPORTED_ROW_STRIDE,
                "rows": SUPPORTED_ROWS,
                "slot_size": SUPPORTED_SLOT_SIZE,
            },
        },
        "publication_floor": _PUBLICATION_FLOOR,
        "raw_v1_policy": {
            "classification_policy": _classification._policy_data(policy),
            "score_weights": {
                "changed_fraction": _classification._CHANGED_FRACTION_WEIGHT,
                "mean_color_delta": _classification._MEAN_COLOR_DELTA_WEIGHT,
            },
        },
        "schema": _MODEL_SCHEMA,
        "slot_ensemble_policy": {
            "gapped_exact_occupancy": "explicit-unknown",
            "prefix_is_scene_or_presentation_authority": False,
            "rationale": (
                "the reviewed development corpus provides counts but not independent "
                "per-slot truth; a non-prefix exact ensemble cannot be safely counted"
            ),
        },
        "validation_status": INVENTORY_POSITIVE_V3_VALIDATION_STATUS,
    }


@dataclass(frozen=True, slots=True)
class InventoryPositiveV3SlotDevelopmentResult:
    """Non-production evidence for one complete authoritative slot."""

    index: int
    raw_v1_state: SlotOccupancy
    raw_v1_confidence: float
    raw_v1_score: float
    raw_v1_changed_fraction: float
    full_slot_rgb_sha256: str
    reference_full_slot_rgb_sha256: str
    full_slot_changed_pixels: int
    exact_reference_match: bool
    exact_prototype_match: bool
    matched_prototype_source_case_ids: tuple[str, ...]
    development_state: SlotOccupancy
    development_confidence: float
    reason: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "authoritative_pixels_accounted": 1024,
            "development_confidence": self.development_confidence,
            "development_state": self.development_state.value,
            "exact_prototype_match": self.exact_prototype_match,
            "exact_reference_match": self.exact_reference_match,
            "full_slot_changed_pixels": self.full_slot_changed_pixels,
            "full_slot_rgb_sha256": self.full_slot_rgb_sha256,
            "index": self.index,
            "matched_prototype_source_case_ids": list(
                self.matched_prototype_source_case_ids
            ),
            "prototype_distance": 0 if self.exact_prototype_match else None,
            "prototype_maximum_accepted_distance": 0,
            "raw_v1_changed_fraction": self.raw_v1_changed_fraction,
            "raw_v1_confidence": self.raw_v1_confidence,
            "raw_v1_score": self.raw_v1_score,
            "raw_v1_state": self.raw_v1_state.value,
            "reason": self.reason,
            "reference_full_slot_rgb_sha256": self.reference_full_slot_rgb_sha256,
        }


@dataclass(frozen=True, slots=True)
class InventoryPositiveV3DevelopmentResult:
    """Plain development result, intentionally incompatible with observations."""

    configuration_id: str
    occupied_slots: int | None
    label: str
    confidence: float
    reason: str | None
    slots: tuple[InventoryPositiveV3SlotDevelopmentResult, ...]

    @property
    def activation_allowed(self) -> bool:
        return False

    @property
    def validation_status(self) -> str:
        return INVENTORY_POSITIVE_V3_VALIDATION_STATUS

    def to_dict(self) -> dict[str, object]:
        return {
            "activation_allowed": False,
            "analyzer_id": INVENTORY_POSITIVE_V3_ANALYZER_ID,
            "analyzer_version": INVENTORY_POSITIVE_V3_ANALYZER_VERSION,
            "candidate_classifier_id": INVENTORY_POSITIVE_V3_CANDIDATE_CLASSIFIER_ID,
            "candidate_classifier_version": (
                INVENTORY_POSITIVE_V3_CANDIDATE_CLASSIFIER_VERSION
            ),
            "candidate_detector_id": INVENTORY_POSITIVE_V3_CANDIDATE_DETECTOR_ID,
            "candidate_detector_version": INVENTORY_POSITIVE_V3_CANDIDATE_DETECTOR_VERSION,
            "confidence": self.confidence,
            "configuration_id": self.configuration_id,
            "development_result_only": True,
            "label": self.label,
            "occupied_slots": self.occupied_slots,
            "reason": self.reason,
            "slots": [item.to_dict() for item in self.slots],
            "validation_status": INVENTORY_POSITIVE_V3_VALIDATION_STATUS,
        }


class InventoryPositiveV3DevelopmentAnalyzer:
    """Exact full-slot RGB allowlist analyzer for one pinned development profile."""

    def __init__(self, profile: InventoryFrameProfile, empty_reference: Frame) -> None:
        if not isinstance(profile, InventoryFrameProfile):
            raise TypeError("profile must be InventoryFrameProfile")
        if not isinstance(empty_reference, Frame):
            raise InventoryClassificationError("empty_reference must be a Frame")
        if (
            profile.profile_id != SUPPORTED_PROFILE_ID
            or profile.frame_width != SUPPORTED_FRAME_WIDTH
            or profile.frame_height != SUPPORTED_FRAME_HEIGHT
            or profile.region.as_tuple() != SUPPORTED_REGION
            or profile.layout.profile_id != SUPPORTED_PROFILE_ID
            or profile.layout.column_stride != SUPPORTED_COLUMN_STRIDE
            or profile.layout.row_stride != SUPPORTED_ROW_STRIDE
            or INVENTORY_COLUMNS != SUPPORTED_COLUMNS
            or INVENTORY_ROWS != SUPPORTED_ROWS
            or INVENTORY_SLOT_SIZE != SUPPORTED_SLOT_SIZE
        ):
            raise InventoryClassificationError(
                "V3 development analyzer requires its complete source-owned profile"
            )
        if (empty_reference.width, empty_reference.height) != (
            profile.frame_width,
            profile.frame_height,
        ):
            raise InventoryClassificationError(
                "V3 reference geometry differs from its source-owned profile"
            )
        if empty_reference.pixel_format.value != SUPPORTED_PIXEL_FORMAT:
            raise InventoryClassificationError(
                "V3 reference pixel format differs from its source-owned profile"
            )
        reference_region_rgb = _region_rgb(empty_reference, profile.region)
        reference_digest = hashlib.sha256(reference_region_rgb).hexdigest()
        if reference_digest != SUPPORTED_REFERENCE_RGB_SHA256:
            raise InventoryClassificationError(
                "V3 reference RGB differs from its source-owned reviewed reference"
            )
        model_artifact = _computed_model_artifact_sha256()
        if model_artifact != MODEL_ARTIFACT_SHA256:
            raise InventoryClassificationError(
                "V3 prototype artifact differs from its frozen model identity"
            )
        self._profile = profile
        self._reference = empty_reference
        self._baseline = ReferenceInventoryClassifier(
            empty_reference,
            profile.region,
            profile.layout,
            ClassificationPolicy(),
        )
        slots = profile.layout.all_slot_regions(profile.region)
        self._reference_slot_rgb = tuple(
            _full_slot_rgb(empty_reference, slot) for slot in slots
        )
        prototypes: dict[int, dict[str, set[str]]] = {}
        for slot_index, digest, source_case_id in FULL_SLOT_RGB_PROTOTYPE_OCCURRENCES:
            prototypes.setdefault(slot_index, {}).setdefault(digest, set()).add(
                source_case_id
            )
        self._prototype_sources = {
            index: {
                digest: tuple(sorted(source_ids))
                for digest, source_ids in by_digest.items()
            }
            for index, by_digest in prototypes.items()
        }
        self._model_configuration = inventory_positive_v3_model_configuration()
        identity: dict[str, object] = {
            "baseline_configuration_id": self._baseline.configuration_id,
            "model_configuration": self._model_configuration,
        }
        self._configuration_id = "inventory-positive-v3-development-" + hashlib.sha256(
            _canonical_bytes(identity)
        ).hexdigest()

    @property
    def configuration_id(self) -> str:
        return self._configuration_id

    @property
    def model_configuration(self) -> Mapping[str, object]:
        return copy.deepcopy(self._model_configuration)

    def analyze(self, frame: Frame) -> InventoryPositiveV3DevelopmentResult:
        """Analyze one frame without creating a Detector or Observation."""
        if not isinstance(frame, Frame):
            raise TypeError("frame must be a Frame")
        if (frame.width, frame.height) != (
            SUPPORTED_FRAME_WIDTH,
            SUPPORTED_FRAME_HEIGHT,
        ):
            return self._unknown("candidate_frame_geometry_not_source_owned")
        if frame.pixel_format is not PixelFormat.BGRA8888:
            return self._unknown("candidate_pixel_format_not_source_owned")
        try:
            baseline = self._baseline.classify(frame, self._profile.region)
            self._check_strong_external_guard(frame)
        except (InventoryClassificationError, InventoryObstructionError) as exc:
            return self._unknown(f"baseline_or_external_guard: {exc}")
        slots = self._profile.layout.all_slot_regions(self._profile.region)
        results: list[InventoryPositiveV3SlotDevelopmentResult] = []
        for decision, slot, reference_rgb in zip(
            baseline,
            slots,
            self._reference_slot_rgb,
            strict=True,
        ):
            candidate_rgb = _full_slot_rgb(frame, slot)
            digest = hashlib.sha256(candidate_rgb).hexdigest()
            reference_digest = hashlib.sha256(reference_rgb).hexdigest()
            changed = _changed_pixel_count(
                candidate_rgb,
                reference_rgb,
                threshold=self._baseline.policy.pixel_difference_threshold,
            )
            exact_reference = candidate_rgb == reference_rgb
            sources = self._prototype_sources.get(decision.index, {}).get(digest, ())
            exact_prototype = bool(sources)
            state = decision.state
            confidence = decision.confidence
            reason: str | None = None
            if decision.state is SlotOccupancy.EMPTY:
                if not exact_reference:
                    state = SlotOccupancy.UNCERTAIN
                    confidence = 0.0
                    reason = "full_slot_not_exact_reference"
            elif decision.state is SlotOccupancy.OCCUPIED:
                if exact_prototype:
                    confidence = _EXACT_PROTOTYPE_CONFIDENCE
                else:
                    state = SlotOccupancy.UNCERTAIN
                    confidence = _UNKNOWN_CONFIDENCE
                    reason = "full_slot_rgb_not_in_exact_development_prototypes"
            else:
                confidence = _UNKNOWN_CONFIDENCE
                reason = "raw_v1_uncertain"
            results.append(
                InventoryPositiveV3SlotDevelopmentResult(
                    index=decision.index,
                    raw_v1_state=decision.state,
                    raw_v1_confidence=decision.confidence,
                    raw_v1_score=decision.score,
                    raw_v1_changed_fraction=decision.changed_fraction,
                    full_slot_rgb_sha256=digest,
                    reference_full_slot_rgb_sha256=reference_digest,
                    full_slot_changed_pixels=changed,
                    exact_reference_match=exact_reference,
                    exact_prototype_match=exact_prototype,
                    matched_prototype_source_case_ids=sources,
                    development_state=state,
                    development_confidence=confidence,
                    reason=reason,
                )
            )
        if any(item.development_state is SlotOccupancy.UNCERTAIN for item in results):
            uncertain = ",".join(
                str(item.index)
                for item in results
                if item.development_state is SlotOccupancy.UNCERTAIN
            )
            return self._unknown(f"uncertain_full_slots: {uncertain}", tuple(results))
        occupied_mask = tuple(
            item.development_state is SlotOccupancy.OCCUPIED for item in results
        )
        if _is_non_prefix(occupied_mask):
            return self._unknown("occupied_mask_not_row_major_prefix", tuple(results))
        if any(item.development_confidence < _PUBLICATION_FLOOR for item in results):
            return self._unknown("slot_confidence_below_0.8", tuple(results))
        occupied = sum(occupied_mask)
        return InventoryPositiveV3DevelopmentResult(
            configuration_id=self.configuration_id,
            occupied_slots=occupied,
            label="empty" if occupied == 0 else "full" if occupied == 28 else "partial",
            confidence=min(item.development_confidence for item in results),
            reason=None,
            slots=tuple(results),
        )

    def _unknown(
        self,
        reason: str,
        slots: tuple[InventoryPositiveV3SlotDevelopmentResult, ...] = (),
    ) -> InventoryPositiveV3DevelopmentResult:
        return InventoryPositiveV3DevelopmentResult(
            configuration_id=self.configuration_id,
            occupied_slots=None,
            label="unknown",
            confidence=_UNKNOWN_CONFIDENCE,
            reason=reason,
            slots=slots,
        )

    def _check_strong_external_guard(self, frame: Frame) -> None:
        candidate: CorePixels = tuple(
            _read_rgb(
                frame,
                self._profile.region.x + offset_x,
                self._profile.region.y + offset_y,
            )
            for offset_x, offset_y in self._baseline._guard_offsets
        )
        changed = sum(
            max(abs(a - b) for a, b in zip(left, right, strict=True))
            >= _STRONG_EXTERNAL_PIXEL_THRESHOLD
            for left, right in zip(
                candidate,
                self._baseline._reference_guard,
                strict=True,
            )
        )
        if changed > _MAX_STRONG_EXTERNAL_CHANGED_PIXELS:
            raise InventoryObstructionError(
                f"{changed} strong external guard pixels changed"
            )


def _computed_model_artifact_sha256() -> str:
    value = {
        "dataset_id": DEVELOPMENT_DATASET_ID,
        "generation_algorithm": PROTOTYPE_GENERATION_ALGORITHM,
        "manifest_sha256": DEVELOPMENT_MANIFEST_SHA256,
        "occurrences": list(FULL_SLOT_RGB_PROTOTYPE_OCCURRENCES),
        "prototype_source_region_sha256s": list(
            PROTOTYPE_SOURCE_REGION_SHA256S
        ),
        "source_profile": {
            "column_stride": SUPPORTED_COLUMN_STRIDE,
            "columns": SUPPORTED_COLUMNS,
            "frame_height": SUPPORTED_FRAME_HEIGHT,
            "frame_width": SUPPORTED_FRAME_WIDTH,
            "pixel_format": SUPPORTED_PIXEL_FORMAT,
            "region": list(SUPPORTED_REGION),
            "row_stride": SUPPORTED_ROW_STRIDE,
            "rows": SUPPORTED_ROWS,
            "slot_size": SUPPORTED_SLOT_SIZE,
        },
        "profile_id": SUPPORTED_PROFILE_ID,
        "reference_region_rgb_sha256": SUPPORTED_REFERENCE_RGB_SHA256,
        "schema": PROTOTYPE_SCHEMA,
    }
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _region_rgb(frame: Frame, region: Region) -> bytes:
    return bytes(
        channel
        for y in range(region.y, region.y + region.height)
        for x in range(region.x, region.x + region.width)
        for channel in _read_rgb(frame, x, y)
    )


def _full_slot_rgb(frame: Frame, slot: Region) -> bytes:
    return _region_rgb(frame, slot)


def _changed_pixel_count(candidate: bytes, reference: bytes, *, threshold: int) -> int:
    if len(candidate) != 32 * 32 * 3 or len(reference) != len(candidate):
        raise InventoryClassificationError("full-slot RGB descriptor must contain 3072 bytes")
    return sum(
        max(
            abs(candidate[index + channel] - reference[index + channel])
            for channel in range(3)
        )
        >= threshold
        for index in range(0, len(candidate), 3)
    )


def _is_non_prefix(mask: tuple[bool, ...]) -> bool:
    saw_empty = False
    for occupied in mask:
        if not occupied:
            saw_empty = True
        elif saw_empty:
            return True
    return False
