"""Frozen positive-capable inventory classifier candidate.

Version 2 preserves the baseline reference score, state thresholds, geometry,
and obstruction guards. A raw occupied decision becomes publishable only when
changed pixels are distributed across every coarse row and column of the owned
slot core. First-campaign-frozen presentation guards add strict fail-closed
paths for selection, quantity text, non-prefix occupancy, and out-of-slot
changes. The candidate is not wired into the approved factory.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Final

from ...capture import Frame
from ..detector import DetectorMetadata
from .classification import (
    ClassificationPolicy,
    CorePixels,
    InventoryClassificationError,
    InventoryObstructionError,
    ReferenceInventoryClassifier,
    SlotDecision,
    SlotOccupancy,
    _decision_from_score,
    _read_core,
    _read_rgb,
)
from .detector import InventoryDetector
from .geometry import (
    INVENTORY_COLUMNS,
    INVENTORY_SLOT_SIZE,
    InventoryGridLayout,
    Region,
)

__all__ = [
    "INVENTORY_POSITIVE_V2_CALIBRATION_SHA256",
    "INVENTORY_POSITIVE_V2_CLASSIFIER_ID",
    "INVENTORY_POSITIVE_V2_CLASSIFIER_VERSION",
    "INVENTORY_POSITIVE_V2_DETECTOR_METADATA",
    "InventoryPositiveDetectorV2",
    "PositiveReferenceInventoryClassifierV2",
    "PositiveSlotFeaturesV2",
    "inventory_positive_v2_algorithm_configuration",
]


INVENTORY_POSITIVE_V2_CLASSIFIER_ID: Final[str] = "reference-distributed-core-v2"
INVENTORY_POSITIVE_V2_CLASSIFIER_VERSION: Final[str] = "2.0.0"
INVENTORY_POSITIVE_V2_DETECTOR_METADATA: Final[DetectorMetadata] = DetectorMetadata(
    detector_id="inventory-positive-v2",
    version="2.0.0",
)

# Canonical digest of only the first reviewed campaign, its truth, the empty
# reference, and every algorithm constant. Held-out bytes are excluded. This
# placeholder is replaced exactly once after the calibration-only reader and
# constants are frozen.
INVENTORY_POSITIVE_V2_CALIBRATION_SHA256: Final[str] = (
    "91d12e3a30824da09f98b766bde8659460121d463e3df7ae8755f617d8abf2c9"
)

_CELL_ROWS: Final[int] = 3
_CELL_COLUMNS: Final[int] = 3
_CELL_SIZE: Final[int] = 8
_MIN_CHANGED_PIXELS_PER_CELL: Final[int] = 1
_MAX_STRONG_NON_SLOT_CHANGED_PIXELS: Final[int] = 0
_STRONG_NON_SLOT_PIXEL_THRESHOLD: Final[int] = 24
_MAX_SLOT_PERIMETER_CHANGED_PIXELS: Final[int] = 0
_SLOT_PERIMETER_PIXEL_THRESHOLD: Final[int] = 61
_REQUIRE_PREFIX_OCCUPANCY: Final[bool] = True
_CHANGED_FRACTION_WEIGHT: Final[float] = 0.7
_MEAN_COLOR_DELTA_WEIGHT: Final[float] = 0.3
_V2_LOCALIZATION_THRESHOLD: Final[float] = 0.9
_V2_PUBLICATION_THRESHOLD: Final[float] = 0.8
_MODEL_SCHEMA: Final[str] = "inventory-reference-distributed-core-v2"
_DETECTOR_CONFIGURATION_SCHEMA: Final[str] = "inventory-positive-detector-v2"


def inventory_positive_v2_algorithm_configuration() -> dict[str, object]:
    """Return every frozen algorithm constant, excluding corpus identity."""
    policy = ClassificationPolicy()
    return {
        "cell_columns": _CELL_COLUMNS,
        "cell_rows": _CELL_ROWS,
        "cell_size": _CELL_SIZE,
        "classifier_id": INVENTORY_POSITIVE_V2_CLASSIFIER_ID,
        "classifier_version": INVENTORY_POSITIVE_V2_CLASSIFIER_VERSION,
        "confidence_feature": "occupied-distributed-core-axes-v1",
        "confidence_mapping": {
            "empty": "unchanged-baseline-raw-score-v1",
            "occupied": (
                "unchanged decision_from_score(spatial_support) after the raw "
                "occupied decision and support in every coarse row and column; "
                "otherwise uncertain"
            ),
            "uncertain": "unchanged-baseline-raw-score-v1",
        },
        "detector_thresholds": {
            "localization": _V2_LOCALIZATION_THRESHOLD,
            "slot_publication": _V2_PUBLICATION_THRESHOLD,
        },
        "minimum_changed_pixels_per_cell": _MIN_CHANGED_PIXELS_PER_CELL,
        "raw_score": {
            "changed_fraction_weight": _CHANGED_FRACTION_WEIGHT,
            "mean_color_delta_weight": _MEAN_COLOR_DELTA_WEIGHT,
        },
        "reference_policy": {
            "core_inset": policy.core_inset,
            "empty_max_score": policy.empty_max_score,
            "max_guard_changed_fraction": policy.max_guard_changed_fraction,
            "max_row_guard_changed_fraction": (
                policy.max_row_guard_changed_fraction
            ),
            "minimum_slot_confidence": policy.minimum_slot_confidence,
            "occupied_min_score": policy.occupied_min_score,
            "pixel_difference_threshold": policy.pixel_difference_threshold,
        },
        "presentation_guards": {
            "max_slot_perimeter_changed_pixels": (
                _MAX_SLOT_PERIMETER_CHANGED_PIXELS
            ),
            "max_strong_non_slot_changed_pixels": (
                _MAX_STRONG_NON_SLOT_CHANGED_PIXELS
            ),
            "require_row_major_prefix_occupancy": _REQUIRE_PREFIX_OCCUPANCY,
            "slot_perimeter_pixel_threshold": _SLOT_PERIMETER_PIXEL_THRESHOLD,
            "strong_non_slot_pixel_threshold": (
                _STRONG_NON_SLOT_PIXEL_THRESHOLD
            ),
        },
        "schema": _MODEL_SCHEMA,
        "spatial_eligibility": {
            "require_each_coarse_column": True,
            "require_each_coarse_row": True,
        },
    }


def _model_data() -> dict[str, object]:
    return {
        "algorithm": inventory_positive_v2_algorithm_configuration(),
        "calibration_evidence_sha256": INVENTORY_POSITIVE_V2_CALIBRATION_SHA256,
    }


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


@dataclass(frozen=True, slots=True)
class PositiveSlotFeaturesV2:
    """Read-only feature vector used by the V2 calibration report."""

    index: int
    row: int
    column: int
    region: Region
    raw_score: float
    changed_fraction: float
    changed_fraction_component: float
    mean_normalized_l1_delta: float
    mean_color_component: float
    active_spatial_cells: int
    spatial_cell_active: tuple[bool, ...]
    spatial_cell_changed_pixels: tuple[int, ...]
    spatial_support: float
    active_coarse_rows: tuple[bool, ...]
    active_coarse_columns: tuple[bool, ...]
    distributed_support: bool
    v1_state: SlotOccupancy
    v1_confidence: float
    v2_state: SlotOccupancy
    v2_confidence: float


class PositiveReferenceInventoryClassifierV2(ReferenceInventoryClassifier):
    """Reference classifier with first-campaign-frozen distributed support."""

    def __init__(
        self,
        reference_frame: Frame,
        reference_region: Region,
        layout: InventoryGridLayout,
    ) -> None:
        # Keep the public runtime policy source-owned and non-overridable.
        super().__init__(
            reference_frame,
            reference_region,
            layout,
            ClassificationPolicy(),
        )
        core_width = INVENTORY_SLOT_SIZE - 2 * self.policy.core_inset
        if core_width != _CELL_COLUMNS * _CELL_SIZE:
            raise InventoryClassificationError(
                "V2 coarse cells require the frozen 24x24 ownership core"
            )
        identity = {
            "base_classifier_configuration_id": super().configuration_id,
            "model": _model_data(),
        }
        self._v2_configuration_id = (
            "inventory-reference-distributed-core-v2-"
            + hashlib.sha256(_canonical_bytes(identity)).hexdigest()
        )
        reference_slots = self.layout.all_slot_regions(reference_region)
        self._reference_slot_perimeters = tuple(
            _read_slot_perimeter(reference_frame, slot, self.policy.core_inset)
            for slot in reference_slots
        )

    @property
    def classifier_id(self) -> str:
        return INVENTORY_POSITIVE_V2_CLASSIFIER_ID

    @property
    def classifier_version(self) -> str:
        return INVENTORY_POSITIVE_V2_CLASSIFIER_VERSION

    @property
    def calibration_evidence_sha256(self) -> str:
        return INVENTORY_POSITIVE_V2_CALIBRATION_SHA256

    @property
    def configuration_id(self) -> str:
        return self._v2_configuration_id

    @property
    def model_configuration(self) -> dict[str, object]:
        """Return a detached JSON-friendly copy of the frozen model constants."""
        return dict(_model_data())

    def analyze(
        self,
        frame: Frame,
        inventory_region: Region,
        /,
    ) -> tuple[PositiveSlotFeaturesV2, ...]:
        """Return deterministic V1/V2 slot features after normal guard checks."""
        # Base validation and obstruction behavior are deliberately shared.
        if not isinstance(frame, Frame):
            raise InventoryClassificationError("frame must be a Frame")
        slots = self._validated_slot_regions(frame, inventory_region, context="candidate")
        self._check_obstruction(frame, inventory_region)
        self._check_v2_presentation_guards(frame, inventory_region, slots)
        features: list[PositiveSlotFeaturesV2] = []
        for index, (slot_region, reference_core) in enumerate(
            zip(slots, self._reference_cores, strict=True)
        ):
            candidate_core = _read_core(frame, slot_region, self.policy.core_inset)
            item = _slot_features(index, slot_region, reference_core, candidate_core, self.policy)
            features.append(item)
        if _REQUIRE_PREFIX_OCCUPANCY and _has_non_prefix_occupancy(features):
            raise InventoryObstructionError(
                "inventory V2 unsupported presentation: occupied mask falls "
                "outside the frozen row-major-prefix calibration envelope"
            )
        return tuple(features)

    def _check_v2_presentation_guards(
        self,
        frame: Frame,
        inventory_region: Region,
        slots: tuple[Region, ...],
    ) -> None:
        strong_non_slot_changes = _changed_pixel_count(
            tuple(
                _read_rgb(
                    frame,
                    inventory_region.x + offset_x,
                    inventory_region.y + offset_y,
                )
                for offset_x, offset_y in self._guard_offsets
            ),
            self._reference_guard,
            threshold=_STRONG_NON_SLOT_PIXEL_THRESHOLD,
        )
        if strong_non_slot_changes > _MAX_STRONG_NON_SLOT_CHANGED_PIXELS:
            raise InventoryObstructionError(
                "inventory V2 unsupported presentation: "
                f"{strong_non_slot_changes} strong non-slot guard pixels changed"
            )

        for index, (slot, reference_perimeter) in enumerate(
            zip(slots, self._reference_slot_perimeters, strict=True)
        ):
            changed = _changed_pixel_count(
                _read_slot_perimeter(frame, slot, self.policy.core_inset),
                reference_perimeter,
                threshold=_SLOT_PERIMETER_PIXEL_THRESHOLD,
            )
            if changed > _MAX_SLOT_PERIMETER_CHANGED_PIXELS:
                raise InventoryObstructionError(
                    "inventory V2 unsupported presentation: slot "
                    f"{index} perimeter has {changed} pixels at or above "
                    f"D={_SLOT_PERIMETER_PIXEL_THRESHOLD}"
                )

    def classify(
        self,
        frame: Frame,
        inventory_region: Region,
        /,
    ) -> tuple[SlotDecision, ...]:
        return tuple(
            SlotDecision(
                index=item.index,
                row=item.row,
                column=item.column,
                region=item.region,
                state=item.v2_state,
                confidence=item.v2_confidence,
                score=item.raw_score,
                changed_fraction=item.changed_fraction,
            )
            for item in self.analyze(frame, inventory_region)
        )


class InventoryPositiveDetectorV2(InventoryDetector):
    """Inventory detector permanently bound to the V2 classifier identity."""

    __slots__ = ()

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.classifier, PositiveReferenceInventoryClassifierV2):
            raise TypeError(
                "InventoryPositiveDetectorV2 requires "
                "PositiveReferenceInventoryClassifierV2"
            )
        if self.localization_threshold != _V2_LOCALIZATION_THRESHOLD:
            raise ValueError(
                "InventoryPositiveDetectorV2 localization_threshold is frozen "
                f"at {_V2_LOCALIZATION_THRESHOLD}"
            )
        if self.minimum_slot_confidence != _V2_PUBLICATION_THRESHOLD:
            raise ValueError(
                "InventoryPositiveDetectorV2 minimum_slot_confidence is frozen "
                f"at {_V2_PUBLICATION_THRESHOLD}"
            )

    @property
    def metadata(self) -> DetectorMetadata:
        return INVENTORY_POSITIVE_V2_DETECTOR_METADATA

    @property
    def configuration_id(self) -> str:
        identity = {
            "base_detector_configuration_id": super().configuration_id,
            "detector_id": self.metadata.detector_id,
            "detector_version": self.metadata.version,
            "schema": _DETECTOR_CONFIGURATION_SCHEMA,
        }
        return "inventory-positive-v2-config-" + hashlib.sha256(
            _canonical_bytes(identity)
        ).hexdigest()


def _slot_features(
    index: int,
    region: Region,
    reference: CorePixels,
    candidate: CorePixels,
    policy: ClassificationPolicy,
) -> PositiveSlotFeaturesV2:
    if len(reference) != len(candidate) or len(reference) != (_CELL_SIZE * 3) ** 2:
        raise InventoryClassificationError(
            "V2 candidate and reference cores must both contain 576 pixels"
        )
    changed = 0
    total_l1_delta = 0
    active_cells = [0] * (_CELL_ROWS * _CELL_COLUMNS)
    for pixel_index, (reference_pixel, candidate_pixel) in enumerate(
        zip(reference, candidate, strict=True)
    ):
        deltas = tuple(
            abs(candidate_channel - reference_channel)
            for reference_channel, candidate_channel in zip(
                reference_pixel, candidate_pixel, strict=True
            )
        )
        if max(deltas) >= policy.pixel_difference_threshold:
            changed += 1
            y, x = divmod(pixel_index, _CELL_COLUMNS * _CELL_SIZE)
            cell_index = (y // _CELL_SIZE) * _CELL_COLUMNS + (x // _CELL_SIZE)
            active_cells[cell_index] += 1
        total_l1_delta += sum(deltas)

    pixel_count = len(reference)
    changed_fraction = changed / pixel_count
    mean_normalized_l1_delta = total_l1_delta / (pixel_count * 3 * 255)
    changed_component = _CHANGED_FRACTION_WEIGHT * changed_fraction
    mean_component = _MEAN_COLOR_DELTA_WEIGHT * mean_normalized_l1_delta
    raw_score = min(1.0, max(0.0, changed_component + mean_component))
    support = (
        sum(count >= _MIN_CHANGED_PIXELS_PER_CELL for count in active_cells)
        / len(active_cells)
    )
    active_mask = tuple(
        count >= _MIN_CHANGED_PIXELS_PER_CELL for count in active_cells
    )
    active_rows = tuple(
        any(active_mask[row * _CELL_COLUMNS : (row + 1) * _CELL_COLUMNS])
        for row in range(_CELL_ROWS)
    )
    active_columns = tuple(
        any(
            active_mask[row * _CELL_COLUMNS + column]
            for row in range(_CELL_ROWS)
        )
        for column in range(_CELL_COLUMNS)
    )
    active_count = sum(active_mask)
    distributed = all(active_rows) and all(active_columns)
    v1_state, v1_confidence = _decision_from_score(raw_score, policy)
    v2_state = v1_state
    v2_confidence = v1_confidence
    if v1_state is SlotOccupancy.OCCUPIED:
        support_state, support_confidence = _decision_from_score(support, policy)
        if distributed and support_state is SlotOccupancy.OCCUPIED:
            v2_confidence = support_confidence
        else:
            v2_state = SlotOccupancy.UNCERTAIN
            v2_confidence = support_confidence
    row, column = divmod(index, INVENTORY_COLUMNS)
    return PositiveSlotFeaturesV2(
        index=index,
        row=row,
        column=column,
        region=region,
        raw_score=raw_score,
        changed_fraction=changed_fraction,
        changed_fraction_component=changed_component,
        mean_normalized_l1_delta=mean_normalized_l1_delta,
        mean_color_component=mean_component,
        active_spatial_cells=active_count,
        spatial_cell_active=active_mask,
        spatial_cell_changed_pixels=tuple(active_cells),
        spatial_support=support,
        active_coarse_rows=active_rows,
        active_coarse_columns=active_columns,
        distributed_support=distributed,
        v1_state=v1_state,
        v1_confidence=v1_confidence,
        v2_state=v2_state,
        v2_confidence=v2_confidence,
    )


def _read_slot_perimeter(
    frame: Frame,
    slot: Region,
    inset: int,
) -> CorePixels:
    right = slot.x + slot.width
    bottom = slot.y + slot.height
    return tuple(
        _read_rgb(frame, x, y)
        for y in range(slot.y, bottom)
        for x in range(slot.x, right)
        if (
            x < slot.x + inset
            or x >= right - inset
            or y < slot.y + inset
            or y >= bottom - inset
        )
    )


def _changed_pixel_count(
    candidate: CorePixels,
    reference: CorePixels,
    *,
    threshold: int,
) -> int:
    if len(candidate) != len(reference):
        raise InventoryClassificationError(
            "V2 presentation guard candidate/reference lengths differ"
        )
    return sum(
        max(
            abs(candidate_channel - reference_channel)
            for candidate_channel, reference_channel in zip(
                candidate_pixel, reference_pixel, strict=True
            )
        )
        >= threshold
        for candidate_pixel, reference_pixel in zip(candidate, reference, strict=True)
    )


def _has_non_prefix_occupancy(
    features: list[PositiveSlotFeaturesV2],
) -> bool:
    saw_empty = False
    for item in features:
        if item.v2_state is SlotOccupancy.EMPTY:
            saw_empty = True
        elif item.v2_state is SlotOccupancy.OCCUPIED and saw_empty:
            return True
    return False
