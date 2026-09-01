"""Synthetic test doubles for the banking foundation.

Everything in this module is architecture-test scaffolding only. None of it
was captured from a real OSRS client, none of it is wired to RuneLite or the
capture layer, and none of it may be described as real evidence in a PR,
issue, or report. It exists so :mod:`mining_automation.banking.workflow` and
:mod:`mining_automation.banking.perception` can be exercised deterministically
without a live game.
"""

from __future__ import annotations

from ..contracts import FrameRef, InventoryState
from .contracts import (
    INVENTORY_CAPACITY,
    BankCheckpointIdentity,
    BankEvidenceProvenance,
    BankInterfaceState,
    BankObservation,
    BankProfileIdentity,
    PostDepositInventoryObservation,
    PreDepositInventoryObservation,
)

__all__ = [
    "SYNTHETIC_BANK_CHECKPOINT",
    "SYNTHETIC_BANK_PROFILE",
    "SYNTHETIC_DETECTOR_ID",
    "SYNTHETIC_DETECTOR_VERSION",
    "build_bank_observation",
    "build_post_deposit_inventory_observation",
    "build_pre_deposit_inventory_observation",
    "build_provenance",
]

SYNTHETIC_DETECTOR_ID = "synthetic-bank-detector"
SYNTHETIC_DETECTOR_VERSION = "0.0.0-synthetic"

SYNTHETIC_BANK_CHECKPOINT = BankCheckpointIdentity(
    checkpoint_id="synthetic-checkpoint",
    location_id="synthetic-location",
)

SYNTHETIC_BANK_PROFILE = BankProfileIdentity(
    profile_id="synthetic-bank-profile",
    profile_version="0.0.0-synthetic",
    schema_version=1,
    frame_width=64,
    frame_height=48,
)


def build_provenance(
    *,
    frame_id: int = 1,
    captured_monotonic_s: float = 0.0,
    width: int = SYNTHETIC_BANK_PROFILE.frame_width,
    height: int = SYNTHETIC_BANK_PROFILE.frame_height,
    cycle_id: str = "synthetic-cycle-1",
    frame_sha256: str | None = None,
) -> BankEvidenceProvenance:
    """Build a synthetic, internally-consistent evidence provenance value."""
    digest = frame_sha256 if frame_sha256 is not None else format(frame_id, "064x")
    return BankEvidenceProvenance(
        frame=FrameRef(
            frame_id=frame_id,
            captured_monotonic_s=captured_monotonic_s,
            width=width,
            height=height,
        ),
        cycle_id=cycle_id,
        frame_sha256=digest,
    )


def build_bank_observation(
    *,
    interface_state: BankInterfaceState = BankInterfaceState.OPEN,
    provenance: BankEvidenceProvenance | None = None,
    identity: BankCheckpointIdentity = SYNTHETIC_BANK_CHECKPOINT,
    profile: BankProfileIdentity = SYNTHETIC_BANK_PROFILE,
    confidence: float = 0.99,
    detector_id: str = SYNTHETIC_DETECTOR_ID,
    detector_version: str = SYNTHETIC_DETECTOR_VERSION,
) -> BankObservation:
    """Build a synthetic bank observation. Never real OSRS evidence."""
    return BankObservation(
        identity=identity,
        profile=profile,
        provenance=provenance if provenance is not None else build_provenance(),
        interface_state=interface_state,
        confidence=confidence,
        detector_id=detector_id,
        detector_version=detector_version,
    )


def build_pre_deposit_inventory_observation(
    *,
    occupied_slots: int | None,
    provenance: BankEvidenceProvenance | None = None,
    capacity: int = INVENTORY_CAPACITY,
    confidence: float = 0.95,
    detector_id: str = "synthetic-inventory-detector",
    detector_version: str = "0.0.0-synthetic",
) -> PreDepositInventoryObservation:
    """Build a synthetic pre-deposit inventory observation."""
    return PreDepositInventoryObservation(
        state=InventoryState(
            occupied_slots=occupied_slots,
            capacity=capacity,
            confidence=confidence if occupied_slots is not None else 0.0,
        ),
        provenance=provenance if provenance is not None else build_provenance(),
        detector_id=detector_id,
        detector_version=detector_version,
    )


def build_post_deposit_inventory_observation(
    *,
    occupied_slots: int | None,
    provenance: BankEvidenceProvenance | None = None,
    capacity: int = INVENTORY_CAPACITY,
    confidence: float = 0.95,
    detector_id: str = "synthetic-inventory-detector",
    detector_version: str = "0.0.0-synthetic",
) -> PostDepositInventoryObservation:
    """Build a synthetic post-deposit inventory observation."""
    return PostDepositInventoryObservation(
        state=InventoryState(
            occupied_slots=occupied_slots,
            capacity=capacity,
            confidence=confidence if occupied_slots is not None else 0.0,
        ),
        provenance=provenance if provenance is not None else build_provenance(),
        detector_id=detector_id,
        detector_version=detector_version,
    )
