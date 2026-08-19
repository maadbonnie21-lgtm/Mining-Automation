"""Deterministic, platform-neutral inventory slot occupancy classification.

The baseline classifier compares the owned pixels in each slot with a reviewed
empty-inventory reference.  It deliberately works on an inset, multi-pixel
ownership core: a wide sprite may cross a slot boundary visually, but pixels
confined to the neighbouring slot's edge cannot make that neighbour occupied.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import Final, Protocol, runtime_checkable

from ...capture import Frame, PixelFormat
from .geometry import (
    INVENTORY_CAPACITY,
    INVENTORY_COLUMNS,
    INVENTORY_ROWS,
    INVENTORY_SLOT_SIZE,
    InventoryGridLayout,
    Region,
)

__all__ = [
    "ClassificationPolicy",
    "InventoryClassificationError",
    "InventoryObstructionError",
    "InventorySlotClassifier",
    "ReferenceInventoryClassifier",
    "SlotDecision",
    "SlotOccupancy",
]


_CHANGED_FRACTION_WEIGHT: Final[float] = 0.7
_MEAN_COLOR_DELTA_WEIGHT: Final[float] = 0.3


class InventoryClassificationError(ValueError):
    """Inventory pixels or geometry cannot be classified safely."""


class InventoryObstructionError(InventoryClassificationError):
    """Non-slot guard pixels indicate that the inventory UI is obstructed."""


class SlotOccupancy(StrEnum):
    """One slot's internal occupancy result."""

    EMPTY = "empty"
    OCCUPIED = "occupied"
    UNCERTAIN = "uncertain"


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_unit_interval(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return 0 <= value <= 1
    return isinstance(value, float) and isfinite(value) and 0.0 <= value <= 1.0


@dataclass(frozen=True, slots=True)
class SlotDecision:
    """One deterministic row-major inventory-slot decision."""

    index: int
    row: int
    column: int
    region: Region
    state: SlotOccupancy
    confidence: float
    score: float
    changed_fraction: float

    def __post_init__(self) -> None:
        if not _is_integer(self.index) or not 0 <= self.index < INVENTORY_CAPACITY:
            raise InventoryClassificationError(
                f"slot index must be an integer in [0, {INVENTORY_CAPACITY - 1}]"
            )
        if not _is_integer(self.row) or not 0 <= self.row < INVENTORY_ROWS:
            raise InventoryClassificationError(
                f"slot row must be an integer in [0, {INVENTORY_ROWS - 1}]"
            )
        if not _is_integer(self.column) or not 0 <= self.column < INVENTORY_COLUMNS:
            raise InventoryClassificationError(
                f"slot column must be an integer in [0, {INVENTORY_COLUMNS - 1}]"
            )
        if self.index != self.row * INVENTORY_COLUMNS + self.column:
            raise InventoryClassificationError(
                "slot index must match its row-major row and column"
            )
        if not isinstance(self.region, Region):
            raise InventoryClassificationError("slot region must be a Region")
        if not isinstance(self.state, SlotOccupancy):
            raise InventoryClassificationError("slot state must be a SlotOccupancy")
        for field_name, value in (
            ("confidence", self.confidence),
            ("score", self.score),
            ("changed_fraction", self.changed_fraction),
        ):
            if not _is_unit_interval(value):
                raise InventoryClassificationError(
                    f"slot {field_name} must be finite and between 0.0 and 1.0"
                )


@dataclass(frozen=True, slots=True)
class ClassificationPolicy:
    """Reviewed thresholds for reference-based slot classification.

    Scores at either band boundary are classified (with confidence ``0.5``).
    Scores strictly inside the band remain uncertain.  Raising
    ``minimum_slot_confidence`` conservatively widens the effective uncertain
    area without changing the objective score.
    """

    core_inset: int = 4
    pixel_difference_threshold: int = 24
    empty_max_score: float = 0.08
    occupied_min_score: float = 0.22
    minimum_slot_confidence: float = 0.5
    max_guard_changed_fraction: float = 0.5
    max_row_guard_changed_fraction: float = 0.0

    def __post_init__(self) -> None:
        if (
            not _is_integer(self.core_inset)
            or not 0 <= self.core_inset < INVENTORY_SLOT_SIZE // 2
        ):
            raise InventoryClassificationError(
                f"core_inset must be an integer in [0, {INVENTORY_SLOT_SIZE // 2 - 1}]"
            )
        if (
            not _is_integer(self.pixel_difference_threshold)
            or not 1 <= self.pixel_difference_threshold <= 255
        ):
            raise InventoryClassificationError(
                "pixel_difference_threshold must be an integer in [1, 255]"
            )
        if not _is_unit_interval(self.empty_max_score):
            raise InventoryClassificationError(
                "empty_max_score must be finite and between 0.0 and 1.0"
            )
        if not _is_unit_interval(self.occupied_min_score):
            raise InventoryClassificationError(
                "occupied_min_score must be finite and between 0.0 and 1.0"
            )
        if self.empty_max_score >= self.occupied_min_score:
            raise InventoryClassificationError(
                "empty_max_score must be lower than occupied_min_score"
            )
        if not _is_unit_interval(self.minimum_slot_confidence):
            raise InventoryClassificationError(
                "minimum_slot_confidence must be finite and between 0.0 and 1.0"
            )
        if self.minimum_slot_confidence < 0.5:
            raise InventoryClassificationError(
                "minimum_slot_confidence must be at least 0.5"
            )
        if (
            not _is_unit_interval(self.max_guard_changed_fraction)
            or self.max_guard_changed_fraction >= 1.0
        ):
            raise InventoryClassificationError(
                "max_guard_changed_fraction must be finite and in [0.0, 1.0)"
            )
        if (
            not _is_unit_interval(self.max_row_guard_changed_fraction)
            or self.max_row_guard_changed_fraction >= 1.0
        ):
            raise InventoryClassificationError(
                "max_row_guard_changed_fraction must be finite and in [0.0, 1.0)"
            )


@runtime_checkable
class InventorySlotClassifier(Protocol):
    """Classify all 28 slots in one localized inventory region."""

    def classify(
        self,
        frame: Frame,
        inventory_region: Region,
        /,
    ) -> tuple[SlotDecision, ...]:
        """Return exactly 28 row-major slot decisions."""
        ...


RgbPixel = tuple[int, int, int]
CorePixels = tuple[RgbPixel, ...]


class ReferenceInventoryClassifier:
    """Compare each slot core with the same slot in an empty reference frame."""

    def __init__(
        self,
        reference_frame: Frame,
        reference_region: Region,
        layout: InventoryGridLayout,
        policy: ClassificationPolicy | None = None,
    ) -> None:
        if not isinstance(reference_frame, Frame):
            raise InventoryClassificationError("reference_frame must be a Frame")
        if not isinstance(layout, InventoryGridLayout):
            raise InventoryClassificationError("layout must be an InventoryGridLayout")
        self._layout = layout
        self._policy = policy if policy is not None else ClassificationPolicy()
        if not isinstance(self._policy, ClassificationPolicy):
            raise InventoryClassificationError("policy must be a ClassificationPolicy")

        reference_slots = self._validated_slot_regions(
            reference_frame,
            reference_region,
            context="reference",
        )
        self._reference_cores = tuple(
            _read_core(reference_frame, region, self._policy.core_inset)
            for region in reference_slots
        )
        self._guard_offsets = _guard_offsets(reference_region, reference_slots)
        self._reference_guard = tuple(
            _read_rgb(
                reference_frame,
                reference_region.x + offset_x,
                reference_region.y + offset_y,
            )
            for offset_x, offset_y in self._guard_offsets
        )
        self._row_guard_offsets = _row_guard_offsets(self._layout)
        self._reference_row_guard = tuple(
            _read_rgb(
                reference_frame,
                reference_region.x + offset_x,
                reference_region.y + offset_y,
            )
            for offset_x, offset_y in self._row_guard_offsets
        )
        self._reference_sha256 = _reference_fingerprint(
            self._layout,
            self._policy,
            self._reference_cores,
            self._reference_guard,
            self._reference_row_guard,
        )
        policy_sha256 = hashlib.sha256(_policy_bytes(self._policy)).hexdigest()
        safe_profile = _safe_identifier_component(self._layout.profile_id)
        self._configuration_id = (
            f"inventory-{safe_profile}-ref-{self._reference_sha256}-"
            f"policy-{policy_sha256}"
        )

    @property
    def layout(self) -> InventoryGridLayout:
        return self._layout

    @property
    def policy(self) -> ClassificationPolicy:
        return self._policy

    @property
    def profile_id(self) -> str:
        """The named layout profile this reviewed reference belongs to."""
        return self._layout.profile_id

    @property
    def reference_sha256(self) -> str:
        """Stable hash of canonical reference pixels and classification config."""
        return self._reference_sha256

    @property
    def configuration_id(self) -> str:
        """Filesystem/report-safe identity for this profile, reference, and policy."""
        return self._configuration_id

    @property
    def has_obstruction_guard(self) -> bool:
        """Whether horizontal row gutters provide a fail-closed obstruction guard."""
        return bool(self._row_guard_offsets)

    @property
    def guard_pixel_count(self) -> int:
        """Number of deterministic non-slot pixels checked for obstruction."""
        return len(self._guard_offsets)

    @property
    def row_guard_pixel_count(self) -> int:
        """Number of horizontal-gutter pixels in the fail-closed guard."""
        return len(self._row_guard_offsets)

    def classify(
        self,
        frame: Frame,
        inventory_region: Region,
        /,
    ) -> tuple[SlotDecision, ...]:
        """Classify ``inventory_region`` against the reviewed empty reference."""
        if not isinstance(frame, Frame):
            raise InventoryClassificationError("frame must be a Frame")
        slots = self._validated_slot_regions(frame, inventory_region, context="candidate")
        self._check_obstruction(frame, inventory_region)
        decisions: list[SlotDecision] = []
        for index, (slot_region, reference_core) in enumerate(
            zip(slots, self._reference_cores, strict=True)
        ):
            candidate_core = _read_core(frame, slot_region, self._policy.core_inset)
            score, changed_fraction = _compare_cores(
                reference_core,
                candidate_core,
                pixel_difference_threshold=self._policy.pixel_difference_threshold,
            )
            state, confidence = _decision_from_score(score, self._policy)
            row, column = divmod(index, INVENTORY_COLUMNS)
            decisions.append(
                SlotDecision(
                    index=index,
                    row=row,
                    column=column,
                    region=slot_region,
                    state=state,
                    confidence=confidence,
                    score=score,
                    changed_fraction=changed_fraction,
                )
            )
        return tuple(decisions)

    def _check_obstruction(self, frame: Frame, inventory_region: Region) -> None:
        if self._guard_offsets:
            changed_fraction = _guard_changed_fraction(
                frame,
                inventory_region,
                self._guard_offsets,
                self._reference_guard,
                pixel_difference_threshold=self._policy.pixel_difference_threshold,
            )
            if changed_fraction > self._policy.max_guard_changed_fraction:
                raise InventoryObstructionError(
                    "inventory guard changed fraction "
                    f"{changed_fraction:.6f} exceeds configured maximum "
                    f"{self._policy.max_guard_changed_fraction:.6f}"
                )

        if self._row_guard_offsets:
            row_changed_fraction = _guard_changed_fraction(
                frame,
                inventory_region,
                self._row_guard_offsets,
                self._reference_row_guard,
                pixel_difference_threshold=self._policy.pixel_difference_threshold,
            )
            if row_changed_fraction > self._policy.max_row_guard_changed_fraction:
                raise InventoryObstructionError(
                    "inventory row guard changed fraction "
                    f"{row_changed_fraction:.6f} exceeds configured maximum "
                    f"{self._policy.max_row_guard_changed_fraction:.6f}"
                )

    def _validated_slot_regions(
        self,
        frame: Frame,
        inventory_region: Region,
        *,
        context: str,
    ) -> tuple[Region, ...]:
        if not isinstance(inventory_region, Region):
            raise InventoryClassificationError(f"{context} inventory region must be a Region")
        if (
            inventory_region.width != self._layout.width
            or inventory_region.height != self._layout.height
        ):
            raise InventoryClassificationError(
                f"{context} inventory region must be exactly "
                f"{self._layout.width}x{self._layout.height}, got "
                f"{inventory_region.width}x{inventory_region.height}"
            )
        if not inventory_region.fits(frame.width, frame.height):
            raise InventoryClassificationError(
                f"{context} inventory region {inventory_region.as_tuple()} "
                f"does not fit frame {frame.width}x{frame.height}"
            )

        slots = self._layout.all_slot_regions(inventory_region)
        if not isinstance(slots, Sequence) or len(slots) != INVENTORY_CAPACITY:
            raise InventoryClassificationError(
                f"layout must produce exactly {INVENTORY_CAPACITY} slot regions"
            )
        result = tuple(slots)
        for index, region in enumerate(result):
            if not isinstance(region, Region):
                raise InventoryClassificationError(
                    f"layout slot {index} must be a Region"
                )
            if not region.fits(frame.width, frame.height):
                raise InventoryClassificationError(
                    f"layout slot {index} {region.as_tuple()} does not fit the frame"
                )
            if region.width <= self._policy.core_inset * 2 or region.height <= (
                self._policy.core_inset * 2
            ):
                raise InventoryClassificationError(
                    f"core_inset {self._policy.core_inset} leaves slot {index} with no pixels"
                )
        return result


def _decision_from_score(
    score: float,
    policy: ClassificationPolicy,
) -> tuple[SlotOccupancy, float]:
    if score <= policy.empty_max_score:
        if policy.empty_max_score == 0.0:
            confidence = 0.5
        else:
            confidence = 0.5 + 0.5 * (
                (policy.empty_max_score - score) / policy.empty_max_score
            )
        state = SlotOccupancy.EMPTY
    elif score >= policy.occupied_min_score:
        if policy.occupied_min_score == 1.0:
            confidence = 0.5
        else:
            confidence = 0.5 + 0.5 * (
                (score - policy.occupied_min_score)
                / (1.0 - policy.occupied_min_score)
            )
        state = SlotOccupancy.OCCUPIED
    else:
        midpoint = (policy.empty_max_score + policy.occupied_min_score) / 2.0
        half_band = (policy.occupied_min_score - policy.empty_max_score) / 2.0
        confidence = 0.5 * abs(score - midpoint) / half_band
        state = SlotOccupancy.UNCERTAIN

    confidence = min(1.0, max(0.0, confidence))
    if state is not SlotOccupancy.UNCERTAIN and (
        confidence < policy.minimum_slot_confidence
    ):
        state = SlotOccupancy.UNCERTAIN
    return state, confidence


def _compare_cores(
    reference: CorePixels,
    candidate: CorePixels,
    *,
    pixel_difference_threshold: int,
) -> tuple[float, float]:
    if len(reference) != len(candidate) or not reference:
        raise InventoryClassificationError(
            "candidate and reference slot cores must have the same non-zero size"
        )

    changed = 0
    total_l1_delta = 0
    for reference_pixel, candidate_pixel in zip(reference, candidate, strict=True):
        deltas = tuple(
            abs(candidate_channel - reference_channel)
            for reference_channel, candidate_channel in zip(
                reference_pixel,
                candidate_pixel,
                strict=True,
            )
        )
        if max(deltas) >= pixel_difference_threshold:
            changed += 1
        total_l1_delta += sum(deltas)

    pixel_count = len(reference)
    changed_fraction = changed / pixel_count
    mean_normalized_l1_delta = total_l1_delta / (pixel_count * 3 * 255)
    score = (
        _CHANGED_FRACTION_WEIGHT * changed_fraction
        + _MEAN_COLOR_DELTA_WEIGHT * mean_normalized_l1_delta
    )
    return min(1.0, max(0.0, score)), changed_fraction


def _read_core(frame: Frame, region: Region, inset: int) -> CorePixels:
    left = region.x + inset
    top = region.y + inset
    right = region.x + region.width - inset
    bottom = region.y + region.height - inset
    if left >= right or top >= bottom:
        raise InventoryClassificationError(
            f"core_inset {inset} leaves region {region.as_tuple()} with no pixels"
        )
    return tuple(
        _read_rgb(frame, x, y)
        for y in range(top, bottom)
        for x in range(left, right)
    )


def _read_rgb(frame: Frame, x: int, y: int) -> RgbPixel:
    bytes_per_pixel = frame.pixel_format.bytes_per_pixel
    offset = (y * frame.width + x) * bytes_per_pixel
    payload = frame.payload
    if frame.pixel_format is PixelFormat.GRAY8:
        value = payload[offset]
        return value, value, value
    if frame.pixel_format is PixelFormat.RGB888:
        return payload[offset], payload[offset + 1], payload[offset + 2]
    if frame.pixel_format is PixelFormat.BGR888:
        return payload[offset + 2], payload[offset + 1], payload[offset]
    if frame.pixel_format is PixelFormat.RGBA8888:
        return payload[offset], payload[offset + 1], payload[offset + 2]
    if frame.pixel_format is PixelFormat.BGRA8888:
        return payload[offset + 2], payload[offset + 1], payload[offset]
    raise InventoryClassificationError(  # pragma: no cover - exhaustive enum guard
        f"unsupported pixel format: {frame.pixel_format!r}"
    )


def _guard_offsets(
    inventory_region: Region,
    slots: tuple[Region, ...],
) -> tuple[tuple[int, int], ...]:
    """Return row-major offsets not owned by any authoritative slot rectangle."""
    owned = bytearray(inventory_region.width * inventory_region.height)
    for slot in slots:
        relative_x = slot.x - inventory_region.x
        relative_y = slot.y - inventory_region.y
        for y in range(relative_y, relative_y + slot.height):
            row_start = y * inventory_region.width
            for x in range(relative_x, relative_x + slot.width):
                owned[row_start + x] = 1
    return tuple(
        (x, y)
        for y in range(inventory_region.height)
        for x in range(inventory_region.width)
        if not owned[y * inventory_region.width + x]
    )


def _row_guard_offsets(layout: InventoryGridLayout) -> tuple[tuple[int, int], ...]:
    """Return every pixel in the horizontal gaps between authoritative rows."""
    return tuple(
        (x, y)
        for row in range(INVENTORY_ROWS - 1)
        for y in range(
            row * layout.row_stride + INVENTORY_SLOT_SIZE,
            (row + 1) * layout.row_stride,
        )
        for x in range(layout.width)
    )


def _guard_changed_fraction(
    frame: Frame,
    inventory_region: Region,
    offsets: tuple[tuple[int, int], ...],
    reference_pixels: CorePixels,
    *,
    pixel_difference_threshold: int,
) -> float:
    if len(offsets) != len(reference_pixels) or not offsets:
        raise InventoryClassificationError(
            "candidate and reference obstruction guards must have the same non-zero size"
        )

    changed = 0
    for (offset_x, offset_y), reference_pixel in zip(
        offsets,
        reference_pixels,
        strict=True,
    ):
        candidate_pixel = _read_rgb(
            frame,
            inventory_region.x + offset_x,
            inventory_region.y + offset_y,
        )
        if max(
            abs(candidate_channel - reference_channel)
            for candidate_channel, reference_channel in zip(
                candidate_pixel,
                reference_pixel,
                strict=True,
            )
        ) >= pixel_difference_threshold:
            changed += 1
    return changed / len(offsets)


def _policy_data(policy: ClassificationPolicy) -> dict[str, int | float]:
    return {
        "core_inset": policy.core_inset,
        "pixel_difference_threshold": policy.pixel_difference_threshold,
        "empty_max_score": policy.empty_max_score,
        "occupied_min_score": policy.occupied_min_score,
        "minimum_slot_confidence": policy.minimum_slot_confidence,
        "max_guard_changed_fraction": policy.max_guard_changed_fraction,
        "max_row_guard_changed_fraction": policy.max_row_guard_changed_fraction,
    }


def _policy_bytes(policy: ClassificationPolicy) -> bytes:
    return json.dumps(
        _policy_data(policy),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _reference_fingerprint(
    layout: InventoryGridLayout,
    policy: ClassificationPolicy,
    reference_cores: tuple[CorePixels, ...],
    reference_guard: CorePixels,
    reference_row_guard: CorePixels,
) -> str:
    header = {
        "schema": "inventory-reference-v1",
        "layout": {
            "profile_id": layout.profile_id,
            "column_stride": layout.column_stride,
            "row_stride": layout.row_stride,
            "width": layout.width,
            "height": layout.height,
        },
        "policy": _policy_data(policy),
        "core_count": len(reference_cores),
        "core_pixel_count": sum(len(core) for core in reference_cores),
        "guard_pixel_count": len(reference_guard),
        "row_guard_pixel_count": len(reference_row_guard),
    }
    digest = hashlib.sha256()
    digest.update(
        json.dumps(
            header,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    )
    digest.update(b"\x00cores\x00")
    for core in reference_cores:
        for pixel in core:
            digest.update(bytes(pixel))
    digest.update(b"\x00guard\x00")
    for pixel in reference_guard:
        digest.update(bytes(pixel))
    digest.update(b"\x00row-guard\x00")
    for pixel in reference_row_guard:
        digest.update(bytes(pixel))
    return digest.hexdigest()


def _safe_identifier_component(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9._-]+", "-", value.casefold())
    normalized = re.sub(r"-+", "-", normalized).strip("-._")
    return normalized or "profile"
