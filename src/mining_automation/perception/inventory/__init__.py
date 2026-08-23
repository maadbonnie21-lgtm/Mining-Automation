"""Platform-neutral OSRS inventory perception and state adaptation.

Live support is profile-driven: no RuneLite anchor is assumed until a reviewed
frame profile and matching empty-inventory reference are supplied.
"""

from __future__ import annotations

from .adapter import (
    InventoryObservationError,
    inventory_detection_from_observation,
    inventory_state_from_observation,
)
from .classification import (
    ClassificationPolicy,
    InventoryClassificationError,
    InventoryObstructionError,
    InventorySlotClassifier,
    ReferenceInventoryClassifier,
    SlotDecision,
    SlotOccupancy,
)
from .configuration import inventory_detector_from_profile
from .detector import (
    INVENTORY_EVIDENCE_SCHEMA_VERSION,
    INVENTORY_OBSERVATION_KIND,
    InventoryDetection,
    InventoryDetector,
    InventoryDetectorError,
)
from .fixture_preparation import (
    InventoryFixturePreparationError,
    PreparedInventoryFrame,
    extract_capture_bmp,
)
from .geometry import (
    INVENTORY_CAPACITY,
    INVENTORY_COLUMNS,
    INVENTORY_ROWS,
    INVENTORY_SLOT_SIZE,
    InventoryGridLayout,
    Region,
)
from .localization import (
    ExactProfileInventoryLocator,
    InventoryFrameProfile,
    InventoryLocalization,
    InventoryRegionLocator,
)

__all__ = [
    "INVENTORY_CAPACITY",
    "INVENTORY_COLUMNS",
    "INVENTORY_EVIDENCE_SCHEMA_VERSION",
    "INVENTORY_OBSERVATION_KIND",
    "INVENTORY_ROWS",
    "INVENTORY_SLOT_SIZE",
    "ClassificationPolicy",
    "ExactProfileInventoryLocator",
    "InventoryClassificationError",
    "InventoryDetection",
    "InventoryDetector",
    "InventoryDetectorError",
    "InventoryFrameProfile",
    "InventoryFixturePreparationError",
    "InventoryGridLayout",
    "InventoryLocalization",
    "InventoryObstructionError",
    "InventoryObservationError",
    "InventoryRegionLocator",
    "InventorySlotClassifier",
    "PreparedInventoryFrame",
    "ReferenceInventoryClassifier",
    "Region",
    "SlotDecision",
    "SlotOccupancy",
    "extract_capture_bmp",
    "inventory_detection_from_observation",
    "inventory_detector_from_profile",
    "inventory_state_from_observation",
]
