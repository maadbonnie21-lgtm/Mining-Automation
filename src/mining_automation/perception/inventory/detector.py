"""Inventory detector orchestration and typed diagnostic results.

The pixel classifier and region locator remain replaceable.  This module owns
the safety policy at their boundary: an inventory count is published only when
the region and all twenty-eight slot decisions are trustworthy.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from ...capture import Frame
from ...contracts import Observation
from ..detector import DetectorMetadata
from ..errors import DetectorError
from .classification import (
    InventoryObstructionError,
    InventorySlotClassifier,
    SlotDecision,
    SlotOccupancy,
)
from .geometry import (
    INVENTORY_CAPACITY,
    INVENTORY_COLUMNS,
    INVENTORY_ROWS,
    INVENTORY_SLOT_SIZE,
    Region,
)
from .localization import InventoryLocalization, InventoryRegionLocator

__all__ = [
    "INVENTORY_EVIDENCE_SCHEMA_VERSION",
    "INVENTORY_OBSERVATION_KIND",
    "InventoryDetection",
    "InventoryDetector",
    "InventoryDetectorError",
]

INVENTORY_OBSERVATION_KIND: Final[str] = "inventory_state"
INVENTORY_EVIDENCE_SCHEMA_VERSION: Final[int] = 1

_DETECTOR_METADATA: Final[DetectorMetadata] = DetectorMetadata(
    detector_id="inventory-baseline",
    version="1.0.0",
)
_UNIDENTIFIED_CONFIGURATION_ID: Final[str] = "unidentified-custom-classifier"
_UNIDENTIFIED_LOCATOR_ID: Final[str] = "unidentified-custom-locator"
_CONFIGURATION_SCHEMA: Final[str] = "inventory-detector-configuration-v1"


class InventoryDetectorError(DetectorError):
    """A locator or slot classifier violated the inventory detector boundary."""


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_confidence(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return 0 <= value <= 1
    return (
        isinstance(value, float)
        and math.isfinite(value)
        and 0.0 <= value <= 1.0
    )


def _expected_label(occupied_slots: int | None) -> str:
    if occupied_slots is None:
        return "unknown"
    if occupied_slots == 0:
        return "empty"
    if occupied_slots == INVENTORY_CAPACITY:
        return "full"
    return "partial"


def _contains(outer: Region, inner: Region) -> bool:
    return (
        inner.x >= outer.x
        and inner.y >= outer.y
        and inner.x + inner.width <= outer.x + outer.width
        and inner.y + inner.height <= outer.y + outer.height
    )


def _classifier_identifier(
    classifier: InventorySlotClassifier,
    attribute: str,
) -> str | None:
    """Read an optional classifier identity without accepting opaque values."""
    try:
        value = getattr(classifier, attribute, None)
    except Exception as exc:
        raise InventoryDetectorError(
            f"inventory classifier {attribute} could not be read: {exc}"
        ) from exc
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise InventoryDetectorError(
            f"inventory classifier {attribute} must be a non-empty string or None"
        )
    return value


def _detector_configuration_id(
    locator_configuration_id: str,
    classifier_configuration_id: str,
    localization_threshold: float,
    minimum_slot_confidence: float,
) -> str:
    """Identify declared components and settings that affect published state."""
    payload = json.dumps(
        {
            "classifier_configuration_id": classifier_configuration_id,
            "locator_configuration_id": locator_configuration_id,
            "localization_threshold": float(localization_threshold),
            "minimum_slot_confidence": float(minimum_slot_confidence),
            "schema": _CONFIGURATION_SCHEMA,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return f"inventory-detector-config-{hashlib.sha256(payload).hexdigest()}"


def _locator_identifier(locator: InventoryRegionLocator) -> str | None:
    """Read an optional locator identity without accepting opaque values."""
    try:
        value = getattr(locator, "configuration_id", None)
    except Exception as exc:
        raise InventoryDetectorError(
            f"inventory locator configuration_id could not be read: {exc}"
        ) from exc
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise InventoryDetectorError(
            "inventory locator configuration_id must be a non-empty string or None"
        )
    return value


def _classifier_has_obstruction_guard(
    classifier: InventorySlotClassifier,
) -> bool | None:
    """Read an optional guard capability advertised by a classifier.

    The reference classifier exposes this property. Custom classifiers may use
    a different, internally validated occlusion strategy and therefore need
    not expose it. An explicit ``False`` is never treated as production-safe.
    """
    try:
        value = getattr(classifier, "has_obstruction_guard", None)
    except Exception as exc:
        raise InventoryDetectorError(
            f"inventory classifier obstruction capability could not be read: {exc}"
        ) from exc
    if value is not None and not isinstance(value, bool):
        raise InventoryDetectorError(
            "inventory classifier has_obstruction_guard must be bool or None"
        )
    return value


def _validate_slot_sequence(slots: tuple[SlotDecision, ...], region: Region) -> None:
    if len(slots) != INVENTORY_CAPACITY:
        raise ValueError(f"slots must contain exactly {INVENTORY_CAPACITY} decisions")

    seen_regions: set[tuple[int, int, int, int]] = set()
    for expected_index, decision in enumerate(slots):
        if not isinstance(decision, SlotDecision):
            raise ValueError(
                f"slots[{expected_index}] must be SlotDecision, "
                f"got {type(decision).__name__}"
            )
        expected_row, expected_column = divmod(expected_index, INVENTORY_COLUMNS)
        if decision.index != expected_index:
            raise ValueError(
                f"slots[{expected_index}] has index {decision.index}; "
                f"expected row-major index {expected_index}"
            )
        if decision.row != expected_row or decision.column != expected_column:
            raise ValueError(
                f"slots[{expected_index}] has row/column "
                f"{decision.row}/{decision.column}; expected "
                f"{expected_row}/{expected_column}"
            )
        if decision.row >= INVENTORY_ROWS:
            raise ValueError(f"slots[{expected_index}] row is outside the inventory grid")
        if (
            decision.region.width != INVENTORY_SLOT_SIZE
            or decision.region.height != INVENTORY_SLOT_SIZE
        ):
            raise ValueError(
                f"slots[{expected_index}] region must be "
                f"{INVENTORY_SLOT_SIZE}x{INVENTORY_SLOT_SIZE}"
            )
        if not _contains(region, decision.region):
            raise ValueError(
                f"slots[{expected_index}] region is outside the localized inventory region"
            )
        region_key = decision.region.as_tuple()
        if region_key in seen_regions:
            raise ValueError(f"slots[{expected_index}] duplicates another slot region")
        seen_regions.add(region_key)

    column_origins = tuple(slots[column].region.x for column in range(INVENTORY_COLUMNS))
    row_origins = tuple(
        slots[row * INVENTORY_COLUMNS].region.y for row in range(INVENTORY_ROWS)
    )
    if column_origins[0] != region.x or (
        column_origins[-1] + INVENTORY_SLOT_SIZE != region.x + region.width
    ):
        raise ValueError("slot columns do not span the localized inventory region")
    if row_origins[0] != region.y or (
        row_origins[-1] + INVENTORY_SLOT_SIZE != region.y + region.height
    ):
        raise ValueError("slot rows do not span the localized inventory region")
    column_strides = tuple(
        right - left
        for left, right in zip(column_origins, column_origins[1:], strict=False)
    )
    row_strides = tuple(
        bottom - top for top, bottom in zip(row_origins, row_origins[1:], strict=False)
    )
    if len(set(column_strides)) != 1 or column_strides[0] < INVENTORY_SLOT_SIZE:
        raise ValueError("slot columns must use one non-overlapping stride")
    if len(set(row_strides)) != 1 or row_strides[0] < INVENTORY_SLOT_SIZE:
        raise ValueError("slot rows must use one non-overlapping stride")
    for decision in slots:
        if (
            decision.region.x != column_origins[decision.column]
            or decision.region.y != row_origins[decision.row]
        ):
            raise ValueError(
                f"slots[{decision.index}] region is inconsistent with its row and column"
            )


@dataclass(frozen=True, slots=True)
class InventoryDetection:
    """One coherent inventory result before conversion to a generic observation."""

    region: Region | None
    occupied_slots: int | None
    confidence: float
    label: str
    reason: str | None
    localization_confidence: float
    configuration_id: str
    profile_id: str | None
    slots: tuple[SlotDecision, ...] = ()

    def __post_init__(self) -> None:
        if self.region is not None and not isinstance(self.region, Region):
            raise ValueError(
                f"region must be Region or None, got {type(self.region).__name__}"
            )
        if (
            not isinstance(self.configuration_id, str)
            or not self.configuration_id.strip()
        ):
            raise ValueError("configuration_id must be a non-empty string")
        if self.profile_id is not None and (
            not isinstance(self.profile_id, str) or not self.profile_id.strip()
        ):
            raise ValueError("profile_id must be None or a non-empty string")
        if not _is_confidence(self.confidence):
            raise ValueError("confidence must be finite and between 0.0 and 1.0")
        if not _is_confidence(self.localization_confidence):
            raise ValueError(
                "localization_confidence must be finite and between 0.0 and 1.0"
            )
        if not isinstance(self.slots, tuple):
            raise ValueError("slots must be an immutable tuple")
        if self.occupied_slots is not None and (
            not _is_integer(self.occupied_slots)
            or not 0 <= self.occupied_slots <= INVENTORY_CAPACITY
        ):
            raise ValueError(
                f"occupied_slots must be None or an integer from 0 to {INVENTORY_CAPACITY}"
            )
        expected_label = _expected_label(self.occupied_slots)
        if self.label != expected_label:
            raise ValueError(
                f"label {self.label!r} is inconsistent with occupied_slots; "
                f"expected {expected_label!r}"
            )

        if self.occupied_slots is not None and self.region is None:
            raise ValueError("a known inventory must include its localized region")

        if self.region is None:
            if self.localization_confidence != 0.0:
                raise ValueError("a missing region must have zero localization confidence")
            if self.profile_id is not None:
                raise ValueError("a missing region cannot identify an inventory profile")
            if self.slots:
                raise ValueError("a detection without a region cannot contain slot decisions")
        else:
            if self.localization_confidence <= 0.0:
                raise ValueError(
                    "a localized region must have localization confidence greater than 0.0"
                )
            if self.slots:
                _validate_slot_sequence(self.slots, self.region)

        if self.occupied_slots is None:
            if self.confidence != 0.0:
                raise ValueError("an unknown inventory must have zero confidence")
            if not isinstance(self.reason, str) or not self.reason.strip():
                raise ValueError("an unknown inventory must include a non-empty reason")
            return

        if self.reason is not None:
            raise ValueError("a known inventory cannot include a failure reason")
        if self.confidence <= 0.0:
            raise ValueError("a known inventory must have confidence greater than 0.0")
        if len(self.slots) != INVENTORY_CAPACITY:
            raise ValueError("a known inventory must include all slot decisions")
        if any(decision.state is SlotOccupancy.UNCERTAIN for decision in self.slots):
            raise ValueError("a known inventory cannot contain uncertain slot decisions")
        detected_occupied = sum(
            decision.state is SlotOccupancy.OCCUPIED for decision in self.slots
        )
        if detected_occupied != self.occupied_slots:
            raise ValueError(
                f"occupied_slots {self.occupied_slots} disagrees with "
                f"{detected_occupied} occupied decisions"
            )
        expected_confidence = min(
            self.localization_confidence,
            *(decision.confidence for decision in self.slots),
        )
        if self.confidence != expected_confidence:
            raise ValueError(
                "known inventory confidence must equal the weakest localization/slot confidence"
            )

    def to_observation(self, frame: Frame, version: str) -> Observation:
        """Render a stable, JSON-friendly evidence mapping for the shared contract."""
        if not isinstance(frame, Frame):
            raise TypeError(f"frame must be Frame, got {type(frame).__name__}")
        if not isinstance(version, str) or not version.strip():
            raise ValueError("version must be a non-empty string")

        evidence: dict[str, Any] = {
            "evidence_schema_version": INVENTORY_EVIDENCE_SCHEMA_VERSION,
            "label": self.label,
            "region": None if self.region is None else self.region.as_tuple(),
            "occupied_slots": self.occupied_slots,
            "capacity": INVENTORY_CAPACITY,
            "reason": self.reason,
            "localization_confidence": self.localization_confidence,
            "configuration_id": self.configuration_id,
            "profile_id": self.profile_id,
            "slots": tuple(_slot_evidence(decision) for decision in self.slots),
        }
        return Observation(
            kind=INVENTORY_OBSERVATION_KIND,
            frame=frame.ref,
            confidence=self.confidence,
            evidence=evidence,
            detector_version=version,
        )


def _slot_evidence(decision: SlotDecision) -> Mapping[str, object]:
    return {
        "index": decision.index,
        "row": decision.row,
        "column": decision.column,
        "region": decision.region.as_tuple(),
        "state": decision.state.value,
        "confidence": decision.confidence,
        "score": decision.score,
        "changed_fraction": decision.changed_fraction,
    }


@dataclass(frozen=True, slots=True)
class InventoryDetector:
    """Compose localization and slot classification into one safe observation."""

    locator: InventoryRegionLocator
    classifier: InventorySlotClassifier
    localization_threshold: float = 0.9
    minimum_slot_confidence: float = 0.8

    def __post_init__(self) -> None:
        if not isinstance(self.locator, InventoryRegionLocator):
            raise TypeError("locator must satisfy InventoryRegionLocator")
        if not isinstance(self.classifier, InventorySlotClassifier):
            raise TypeError("classifier must satisfy InventorySlotClassifier")
        if (
            not _is_confidence(self.localization_threshold)
            or float(self.localization_threshold) <= 0.0
        ):
            raise ValueError(
                "localization_threshold must be between 0.0 (exclusive) and 1.0"
            )
        if (
            not _is_confidence(self.minimum_slot_confidence)
            or float(self.minimum_slot_confidence) <= 0.0
        ):
            raise ValueError(
                "minimum_slot_confidence must be between 0.0 (exclusive) and 1.0"
            )

    @property
    def metadata(self) -> DetectorMetadata:
        """Return the stable identity required by the generic detector contract."""
        return _DETECTOR_METADATA

    @property
    def configuration_id(self) -> str:
        """Stable identity for locator, classifier, and publication thresholds."""
        locator_configuration_id = (
            _locator_identifier(self.locator) or _UNIDENTIFIED_LOCATOR_ID
        )
        classifier_configuration_id = _classifier_identifier(
            self.classifier, "configuration_id"
        ) or _UNIDENTIFIED_CONFIGURATION_ID
        return _detector_configuration_id(
            locator_configuration_id,
            classifier_configuration_id,
            self.localization_threshold,
            self.minimum_slot_confidence,
        )

    def detect(self, frame: Frame) -> tuple[Observation, ...]:
        """Produce exactly one known or explicitly unknown inventory observation."""
        if not isinstance(frame, Frame):
            raise InventoryDetectorError(
                f"inventory detector input must be Frame, got {type(frame).__name__}"
            )
        detection = self._analyze(frame)
        return (detection.to_observation(frame, self.metadata.version),)

    def _analyze(self, frame: Frame) -> InventoryDetection:
        configuration_id = self.configuration_id
        classifier_profile_id = _classifier_identifier(self.classifier, "profile_id")
        try:
            localization = self.locator.locate(frame)
        except Exception as exc:
            raise InventoryDetectorError(
                f"inventory locator failed on frame {frame.frame_id}: {exc}"
            ) from exc
        if not isinstance(localization, InventoryLocalization):
            raise InventoryDetectorError(
                "inventory locator must return InventoryLocalization, "
                f"got {type(localization).__name__}"
            )

        region = localization.region
        profile_id = localization.profile_id
        if (
            profile_id is not None
            and classifier_profile_id is not None
            and profile_id != classifier_profile_id
        ):
            raise InventoryDetectorError(
                "inventory localization/classifier profile mismatch: "
                f"{profile_id!r} != {classifier_profile_id!r}"
            )
        if region is not None and not region.fits(frame.width, frame.height):
            raise InventoryDetectorError(
                f"localized inventory region {region.as_tuple()} is outside "
                f"frame {frame.width}x{frame.height}"
            )
        if region is None:
            return InventoryDetection(
                region=None,
                occupied_slots=None,
                confidence=0.0,
                label="unknown",
                reason=f"inventory_region_not_localized: {localization.reason}",
                localization_confidence=0.0,
                configuration_id=configuration_id,
                profile_id=None,
            )
        if localization.confidence < self.localization_threshold:
            return InventoryDetection(
                region=region,
                occupied_slots=None,
                confidence=0.0,
                label="unknown",
                reason=f"localization_below_threshold: {localization.reason}",
                localization_confidence=localization.confidence,
                configuration_id=configuration_id,
                profile_id=profile_id,
            )

        if _classifier_has_obstruction_guard(self.classifier) is False:
            return InventoryDetection(
                region=region,
                occupied_slots=None,
                confidence=0.0,
                label="unknown",
                reason=(
                    "obstruction_guard_unavailable: localized layout has no "
                    "horizontal row-gutter obstruction guard"
                ),
                localization_confidence=localization.confidence,
                configuration_id=configuration_id,
                profile_id=profile_id,
            )

        try:
            decisions = self.classifier.classify(frame, region)
        except InventoryObstructionError as exc:
            detail = str(exc).strip() or "classifier reported an obstruction"
            return InventoryDetection(
                region=region,
                occupied_slots=None,
                confidence=0.0,
                label="unknown",
                reason=f"inventory_obstructed: {detail}",
                localization_confidence=localization.confidence,
                configuration_id=configuration_id,
                profile_id=profile_id,
            )
        except Exception as exc:
            raise InventoryDetectorError(
                f"inventory slot classification failed on frame {frame.frame_id}: {exc}"
            ) from exc
        if not isinstance(decisions, tuple):
            raise InventoryDetectorError(
                "inventory classifier must return tuple[SlotDecision, ...], "
                f"got {type(decisions).__name__}"
            )
        try:
            _validate_slot_sequence(decisions, region)
        except ValueError as exc:
            raise InventoryDetectorError(f"invalid inventory slot decisions: {exc}") from exc

        uncertain = tuple(
            decision.index
            for decision in decisions
            if decision.state is SlotOccupancy.UNCERTAIN
        )
        if uncertain:
            return InventoryDetection(
                region=region,
                occupied_slots=None,
                confidence=0.0,
                label="unknown",
                reason="uncertain_slots: " + ",".join(str(index) for index in uncertain),
                localization_confidence=localization.confidence,
                configuration_id=configuration_id,
                profile_id=profile_id,
                slots=decisions,
            )

        low_confidence = tuple(
            decision.index
            for decision in decisions
            if decision.confidence < self.minimum_slot_confidence
        )
        if low_confidence:
            return InventoryDetection(
                region=region,
                occupied_slots=None,
                confidence=0.0,
                label="unknown",
                reason=(
                    "slot_confidence_below_threshold: "
                    + ",".join(str(index) for index in low_confidence)
                ),
                localization_confidence=localization.confidence,
                configuration_id=configuration_id,
                profile_id=profile_id,
                slots=decisions,
            )

        occupied_slots = sum(
            decision.state is SlotOccupancy.OCCUPIED for decision in decisions
        )
        confidence = min(
            localization.confidence,
            *(decision.confidence for decision in decisions),
        )
        return InventoryDetection(
            region=region,
            occupied_slots=occupied_slots,
            confidence=confidence,
            label=_expected_label(occupied_slots),
            reason=None,
            localization_confidence=localization.confidence,
            configuration_id=configuration_id,
            profile_id=profile_id,
            slots=decisions,
        )
