"""Tests for the type/test-level integration boundary with Codex B and Codex C (Part D6).

Every fake here is a small local class built purely to exercise the
``Protocol`` shape -- none of it imports, depends on, or represents Codex B's
fixed-route/checkpoint code or Codex C's Inventory V3 code. That is the
point: the boundary is checked structurally, with zero coupling to either
branch.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from mining_automation.banking.contracts import BankEvidenceProvenance
from mining_automation.banking.errors import IntegrationBoundaryContractError
from mining_automation.banking.integration_boundary import (
    ExternalApprovedInventoryResult,
    ExternalCheckpointArrivalSource,
    adapt_checkpoint_arrival,
    adapt_post_deposit_inventory,
    adapt_pre_deposit_inventory,
)
from mining_automation.banking.testing import (
    SYNTHETIC_BANK_CHECKPOINT,
    build_provenance,
)
from mining_automation.banking.workflow import (
    CheckpointArrivalEvidence,
    PostDepositInventoryObservationEvidence,
    PreDepositInventoryObservationEvidence,
)
from mining_automation.contracts import InventoryState

# ---------------------------------------------------------------------------
# Fakes standing in for a future Codex B arrival result / Codex C inventory
# result. Deliberately not imported from either subsystem.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _FakeCodexBArrival:
    identity = SYNTHETIC_BANK_CHECKPOINT
    provenance: BankEvidenceProvenance


@dataclass(frozen=True, slots=True)
class _FakeCodexCInventoryResult:
    state: InventoryState
    provenance: BankEvidenceProvenance
    detector_id: str = "codex-c-inventory-v3"
    detector_version: str = "3.0.0"


class _MissingAttributesArrival:
    """Does not expose identity/provenance at all."""


class _WrongTypeArrival:
    identity = "not-an-identity"
    provenance = "not-a-provenance"


@dataclass(frozen=True, slots=True)
class _WrongProvenanceTypeArrival:
    identity = SYNTHETIC_BANK_CHECKPOINT
    provenance: str = "not-a-provenance"


class _WrongTypeInventoryResult:
    state = "not-a-state"
    provenance = "not-a-provenance"
    detector_id = "d"
    detector_version = "1"


class _WrongProvenanceTypeInventoryResult:
    def __init__(self) -> None:
        self.state = InventoryState(occupied_slots=28, capacity=28, confidence=0.9)
        self.provenance = "not-a-provenance"
        self.detector_id = "d"
        self.detector_version = "1"


class _BlankDetectorIdInventoryResult:
    def __init__(self, provenance: BankEvidenceProvenance) -> None:
        self.state = InventoryState(occupied_slots=28, capacity=28, confidence=0.9)
        self.provenance = provenance
        self.detector_id = "   "
        self.detector_version = "1"


class _BlankDetectorVersionInventoryResult:
    def __init__(self, provenance: BankEvidenceProvenance) -> None:
        self.state = InventoryState(occupied_slots=28, capacity=28, confidence=0.9)
        self.provenance = provenance
        self.detector_id = "d"
        self.detector_version = "   "


# ---------------------------------------------------------------------------
# adapt_checkpoint_arrival
# ---------------------------------------------------------------------------


def test_adapt_checkpoint_arrival_accepts_conforming_source() -> None:
    provenance = build_provenance()
    source = _FakeCodexBArrival(provenance=provenance)
    assert isinstance(source, ExternalCheckpointArrivalSource)

    event = adapt_checkpoint_arrival(source)
    assert type(event) is CheckpointArrivalEvidence
    assert event.identity == SYNTHETIC_BANK_CHECKPOINT
    assert event.provenance == provenance


def test_adapt_checkpoint_arrival_rejects_missing_attributes() -> None:
    with pytest.raises(IntegrationBoundaryContractError, match="must satisfy"):
        adapt_checkpoint_arrival(_MissingAttributesArrival())


def test_adapt_checkpoint_arrival_rejects_wrong_field_types() -> None:
    with pytest.raises(IntegrationBoundaryContractError):
        adapt_checkpoint_arrival(_WrongTypeArrival())


def test_adapt_checkpoint_arrival_rejects_plain_object() -> None:
    with pytest.raises(IntegrationBoundaryContractError, match="must satisfy"):
        adapt_checkpoint_arrival(object())


def test_adapt_checkpoint_arrival_rejects_wrong_provenance_type_alone() -> None:
    with pytest.raises(
        IntegrationBoundaryContractError, match="provenance must be BankEvidenceProvenance"
    ):
        adapt_checkpoint_arrival(_WrongProvenanceTypeArrival())


# ---------------------------------------------------------------------------
# adapt_pre_deposit_inventory / adapt_post_deposit_inventory
# ---------------------------------------------------------------------------


def test_adapt_pre_deposit_inventory_accepts_conforming_source() -> None:
    provenance = build_provenance()
    state = InventoryState(occupied_slots=28, capacity=28, confidence=0.95)
    source = _FakeCodexCInventoryResult(state=state, provenance=provenance)
    assert isinstance(source, ExternalApprovedInventoryResult)

    event = adapt_pre_deposit_inventory(source)
    assert type(event) is PreDepositInventoryObservationEvidence
    assert len(event.observations) == 1
    assert event.observations[0].state == state
    assert event.observations[0].provenance == provenance
    assert event.observations[0].detector_id == "codex-c-inventory-v3"


def test_adapt_post_deposit_inventory_accepts_conforming_source() -> None:
    provenance = build_provenance()
    state = InventoryState(occupied_slots=0, capacity=28, confidence=0.95)
    source = _FakeCodexCInventoryResult(state=state, provenance=provenance)

    event = adapt_post_deposit_inventory(source)
    assert type(event) is PostDepositInventoryObservationEvidence
    assert event.observations[0].state.occupied_slots == 0


def test_adapt_pre_deposit_inventory_rejects_plain_object() -> None:
    with pytest.raises(IntegrationBoundaryContractError, match="must satisfy"):
        adapt_pre_deposit_inventory(object())


def test_adapt_pre_deposit_inventory_rejects_wrong_state_type() -> None:
    with pytest.raises(IntegrationBoundaryContractError, match="state must be InventoryState"):
        adapt_pre_deposit_inventory(_WrongTypeInventoryResult())


def test_adapt_post_deposit_inventory_rejects_blank_detector_id() -> None:
    source = _BlankDetectorIdInventoryResult(provenance=build_provenance())
    with pytest.raises(
        IntegrationBoundaryContractError, match="detector_id must be a non-empty string"
    ):
        adapt_post_deposit_inventory(source)


def test_adapt_post_deposit_inventory_rejects_blank_detector_version() -> None:
    source = _BlankDetectorVersionInventoryResult(provenance=build_provenance())
    with pytest.raises(
        IntegrationBoundaryContractError, match="detector_version must be a non-empty string"
    ):
        adapt_post_deposit_inventory(source)


def test_adapt_pre_deposit_inventory_rejects_wrong_provenance_type_alone() -> None:
    with pytest.raises(
        IntegrationBoundaryContractError, match="provenance must be BankEvidenceProvenance"
    ):
        adapt_pre_deposit_inventory(_WrongProvenanceTypeInventoryResult())


def test_adapt_pre_deposit_inventory_reads_each_field_exactly_once() -> None:
    """D0 adversarial audit finding: Protocol members are properties.

    Validating one read and then separately re-reading to build the
    observation would silently trust whatever a second, possibly different,
    read returned. Each field must be read exactly once and that exact value
    used, mirroring how ``run_bank_detector`` guards against a detector's own
    metadata drifting mid-run.
    """

    class _CountingInventoryResult:
        def __init__(self, provenance: BankEvidenceProvenance) -> None:
            self.state_reads = 0
            self.provenance_reads = 0
            self.detector_id_reads = 0
            self.detector_version_reads = 0
            self._provenance = provenance

        @property
        def state(self) -> InventoryState:
            self.state_reads += 1
            # A flaky/malicious implementation could return something
            # different on a later read; here it stays valid but the
            # adapter must still only read it once.
            return InventoryState(occupied_slots=28, capacity=28, confidence=0.95)

        @property
        def provenance(self) -> BankEvidenceProvenance:
            self.provenance_reads += 1
            return self._provenance

        @property
        def detector_id(self) -> str:
            self.detector_id_reads += 1
            return "codex-c-inventory-v3"

        @property
        def detector_version(self) -> str:
            self.detector_version_reads += 1
            return "3.0.0"

    source = _CountingInventoryResult(provenance=build_provenance())
    adapt_pre_deposit_inventory(source)
    assert source.state_reads == 1
    assert source.provenance_reads == 1
    assert source.detector_id_reads == 1
    assert source.detector_version_reads == 1


def test_adapters_never_produce_a_verified_workflow_state() -> None:
    """The adapters only produce input *events* -- never a workflow context,
    never a BankInterfaceState, never anything resembling authority."""
    provenance = build_provenance()
    arrival_event = adapt_checkpoint_arrival(_FakeCodexBArrival(provenance=provenance))
    inventory_event = adapt_pre_deposit_inventory(
        _FakeCodexCInventoryResult(
            state=InventoryState(occupied_slots=28, capacity=28, confidence=0.9),
            provenance=provenance,
        )
    )
    assert type(arrival_event) is CheckpointArrivalEvidence
    assert type(inventory_event) is PreDepositInventoryObservationEvidence
    assert not hasattr(arrival_event, "state")
    assert not hasattr(inventory_event, "interface_state")
