"""Frame-local geometry for the OSRS inventory grid.

The inventory has 28 logical slots in a four-column, seven-row grid.  A slot's
authoritative area is 32 by 32 pixels even when a rendered item sprite extends
outside that area.  Keeping that geometry here lets classifiers sample and
count each logical slot exactly once.

Coordinates are always relative to the captured client frame.  This module has
no desktop, window-position, or platform concepts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

__all__ = [
    "INVENTORY_CAPACITY",
    "INVENTORY_COLUMNS",
    "INVENTORY_ROWS",
    "INVENTORY_SLOT_SIZE",
    "InventoryGridLayout",
    "Region",
]

INVENTORY_COLUMNS: Final = 4
INVENTORY_ROWS: Final = 7
INVENTORY_CAPACITY: Final = INVENTORY_COLUMNS * INVENTORY_ROWS
INVENTORY_SLOT_SIZE: Final = 32


@dataclass(frozen=True, slots=True)
class Region:
    """A validated frame-local ``(x, y, width, height)`` rectangle."""

    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        _require_non_negative_int("x", self.x)
        _require_non_negative_int("y", self.y)
        _require_positive_int("width", self.width)
        _require_positive_int("height", self.height)

    def as_tuple(self) -> tuple[int, int, int, int]:
        """Return the canonical regression-evidence representation."""
        return (self.x, self.y, self.width, self.height)

    def fits(self, width: int, height: int) -> bool:
        """Whether this region is wholly inside a container at origin ``(0, 0)``.

        Container dimensions are validated rather than relying on Python's
        permissive integer arithmetic (where ``bool`` is an ``int`` subclass).
        """
        _require_positive_int("container width", width)
        _require_positive_int("container height", height)
        return self.x + self.width <= width and self.y + self.height <= height


@dataclass(frozen=True, slots=True)
class InventoryGridLayout:
    """Reviewed spacing profile for one 4-by-7 inventory presentation."""

    profile_id: str
    column_stride: int
    row_stride: int

    def __post_init__(self) -> None:
        if not isinstance(self.profile_id, str) or not self.profile_id.strip():
            raise ValueError("profile_id must be a non-empty string")
        _require_strict_int("column_stride", self.column_stride)
        _require_strict_int("row_stride", self.row_stride)
        if self.column_stride < INVENTORY_SLOT_SIZE:
            raise ValueError(
                f"column_stride must be >= {INVENTORY_SLOT_SIZE}, got {self.column_stride}"
            )
        if self.row_stride < INVENTORY_SLOT_SIZE:
            raise ValueError(
                f"row_stride must be >= {INVENTORY_SLOT_SIZE}, got {self.row_stride}"
            )

    @property
    def width(self) -> int:
        """Width from the first slot's left edge to the last slot's right edge."""
        return (INVENTORY_COLUMNS - 1) * self.column_stride + INVENTORY_SLOT_SIZE

    @property
    def height(self) -> int:
        """Height from the first slot's top edge to the last slot's bottom edge."""
        return (INVENTORY_ROWS - 1) * self.row_stride + INVENTORY_SLOT_SIZE

    def region_at(self, x: int, y: int) -> Region:
        """Create an inventory-sized region at a frame-local origin."""
        return Region(x=x, y=y, width=self.width, height=self.height)

    def slot_region(self, inventory_region: Region, index: int) -> Region:
        """Return one authoritative 32-by-32 slot, indexed in row-major order."""
        self._validate_inventory_region(inventory_region)
        _require_strict_int("slot index", index)
        if not 0 <= index < INVENTORY_CAPACITY:
            raise IndexError(
                f"slot index must be in [0, {INVENTORY_CAPACITY - 1}], got {index}"
            )

        row, column = divmod(index, INVENTORY_COLUMNS)
        slot = Region(
            x=inventory_region.x + column * self.column_stride,
            y=inventory_region.y + row * self.row_stride,
            width=INVENTORY_SLOT_SIZE,
            height=INVENTORY_SLOT_SIZE,
        )
        if not _contains(inventory_region, slot):  # pragma: no cover - invariant guard
            raise ValueError(
                f"generated slot {index} {slot.as_tuple()} falls outside inventory region "
                f"{inventory_region.as_tuple()}"
            )
        return slot

    def all_slot_regions(self, inventory_region: Region) -> tuple[Region, ...]:
        """Return all 28 authoritative slot regions in row-major order."""
        self._validate_inventory_region(inventory_region)
        return tuple(
            self.slot_region(inventory_region, index)
            for index in range(INVENTORY_CAPACITY)
        )

    def _validate_inventory_region(self, inventory_region: Region) -> None:
        if not isinstance(inventory_region, Region):
            raise TypeError(
                "inventory_region must be Region, "
                f"got {type(inventory_region).__name__}"
            )
        if (inventory_region.width, inventory_region.height) != (self.width, self.height):
            raise ValueError(
                "inventory region dimensions must match layout dimensions: "
                f"got {inventory_region.width}x{inventory_region.height}, "
                f"expected {self.width}x{self.height}"
            )


def _contains(container: Region, child: Region) -> bool:
    return (
        child.x >= container.x
        and child.y >= container.y
        and child.x + child.width <= container.x + container.width
        and child.y + child.height <= container.y + container.height
    )


def _require_strict_int(name: str, value: object) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an integer, got {type(value).__name__}")


def _require_non_negative_int(name: str, value: int) -> None:
    _require_strict_int(name, value)
    if value < 0:
        raise ValueError(f"{name} must be >= 0, got {value}")


def _require_positive_int(name: str, value: int) -> None:
    _require_strict_int(name, value)
    if value <= 0:
        raise ValueError(f"{name} must be > 0, got {value}")
