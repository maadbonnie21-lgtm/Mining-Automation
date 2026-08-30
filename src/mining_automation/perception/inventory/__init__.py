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
from .live_validation_session import (
    DEFAULT_INVENTORY_VALIDATION_CASES,
    OPTIONAL_INVENTORY_VALIDATION_CASES,
    InventoryValidationSessionError,
    InventoryValidationSessionPaused,
    InventoryValidationSessionRecord,
    InventoryValidationSessionReport,
    InventoryValidationSessionStatus,
    load_inventory_validation_session,
    run_inventory_validation_session,
)
from .localization import (
    ExactProfileInventoryLocator,
    InventoryFrameProfile,
    InventoryLocalization,
    InventoryRegionLocator,
)
from .review_gate import (
    CandidateInventoryProfile,
    InventoryCaseReview,
    InventoryEvidenceVisibility,
    InventoryReviewDecision,
    InventoryReviewGateError,
    InventoryReviewPackage,
    InventoryReviewPackageCase,
    InventoryReviewRecord,
    InventoryReviewReplayReport,
    InventoryReviewSourceSession,
    InventoryValidationSplit,
    load_inventory_review_package,
    load_inventory_review_record,
    prepare_inventory_review_package,
    run_inventory_review_replay_gate,
)
from .sanitized_replay import (
    InventorySanitizedReplayCaseResult,
    InventorySanitizedReplayError,
    InventorySanitizedReplayReport,
    replay_inventory_sanitized_fixture,
)

__all__ = [
    "DEFAULT_INVENTORY_VALIDATION_CASES",
    "INVENTORY_CAPACITY",
    "INVENTORY_COLUMNS",
    "INVENTORY_EVIDENCE_SCHEMA_VERSION",
    "INVENTORY_OBSERVATION_KIND",
    "INVENTORY_ROWS",
    "INVENTORY_SLOT_SIZE",
    "OPTIONAL_INVENTORY_VALIDATION_CASES",
    "ClassificationPolicy",
    "CandidateInventoryProfile",
    "ExactProfileInventoryLocator",
    "InventoryClassificationError",
    "InventoryCaseReview",
    "InventoryDetection",
    "InventoryDetector",
    "InventoryDetectorError",
    "InventoryEvidenceVisibility",
    "InventoryFrameProfile",
    "InventoryFixturePreparationError",
    "InventoryGridLayout",
    "InventoryLocalization",
    "InventoryObstructionError",
    "InventoryObservationError",
    "InventoryRegionLocator",
    "InventoryReviewDecision",
    "InventoryReviewGateError",
    "InventoryReviewPackage",
    "InventoryReviewPackageCase",
    "InventoryReviewRecord",
    "InventoryReviewReplayReport",
    "InventoryReviewSourceSession",
    "InventorySanitizedReplayCaseResult",
    "InventorySanitizedReplayError",
    "InventorySanitizedReplayReport",
    "InventorySlotClassifier",
    "InventoryValidationSessionError",
    "InventoryValidationSessionPaused",
    "InventoryValidationSessionRecord",
    "InventoryValidationSessionReport",
    "InventoryValidationSessionStatus",
    "InventoryValidationSplit",
    "PreparedInventoryFrame",
    "ReferenceInventoryClassifier",
    "Region",
    "SlotDecision",
    "SlotOccupancy",
    "extract_capture_bmp",
    "inventory_detection_from_observation",
    "inventory_detector_from_profile",
    "inventory_state_from_observation",
    "load_inventory_validation_session",
    "load_inventory_review_package",
    "load_inventory_review_record",
    "prepare_inventory_review_package",
    "replay_inventory_sanitized_fixture",
    "run_inventory_review_replay_gate",
    "run_inventory_validation_session",
]
