"""Banking foundation: typed contracts, a perception seam, and a non-input workflow.

This package is the constrained-v1 banking foundation. It is offline and
architectural: it defines what a future, reviewed bank detector must deliver
and how that evidence composes into a verified deposit workflow, but it does
not itself perform any RuneLite interaction, capture, navigation, or
``WorldState``/:class:`~mining_automation.controller.MiningController`
activation.

* :mod:`mining_automation.banking.contracts` -- typed identity, evidence, and
  fail-closed result value objects.
* :mod:`mining_automation.banking.perception` -- the bank detector protocol
  and the pure evaluators that turn raw evidence into a trustworthy reading.
* :mod:`mining_automation.banking.workflow` -- the deterministic, non-input
  state machine that composes bank and inventory evidence into a verified
  deposit outcome. See its module docstring for the full transition table.

See ``docs/BANKING.md`` for the future real-evidence specification this
foundation is built to receive.
"""

from __future__ import annotations

from .contracts import (
    INVENTORY_CAPACITY,
    BankCheckpointIdentity,
    BankEvidenceProvenance,
    BankingBlocker,
    BankingVerificationResult,
    BankInterfaceState,
    BankObservation,
    BankProfileIdentity,
    DepositReadiness,
    PostDepositInventoryObservation,
    PreDepositInventoryObservation,
)
from .errors import BankDetectorContractError, BankDetectorExecutionError, BankingError
from .perception import (
    INVENTORY_PUBLICATION_CONFIDENCE_FLOOR,
    MAX_BANKING_EVIDENCE_AGE_S,
    BankDetector,
    BankDetectorMetadata,
    BankPerceptionResult,
    InventoryPerceptionResult,
    evaluate_bank_observation,
    evaluate_inventory_observation,
    run_bank_detector,
    validate_bank_detector,
)
from .workflow import (
    INITIAL_BANKING_WORKFLOW_STATE,
    BankingWorkflowContext,
    BankingWorkflowState,
    BankObservationEvidence,
    CheckpointArrivalEvidence,
    DepositAttempted,
    OpenBankAttempted,
    PostDepositInventoryObservationEvidence,
    PreDepositInventoryObservationEvidence,
    advance_banking_workflow,
    deposit_readiness,
    initial_banking_workflow_context,
)

__all__ = [
    "INITIAL_BANKING_WORKFLOW_STATE",
    "INVENTORY_CAPACITY",
    "INVENTORY_PUBLICATION_CONFIDENCE_FLOOR",
    "MAX_BANKING_EVIDENCE_AGE_S",
    "BankCheckpointIdentity",
    "BankDetector",
    "BankDetectorContractError",
    "BankDetectorExecutionError",
    "BankDetectorMetadata",
    "BankEvidenceProvenance",
    "BankInterfaceState",
    "BankObservation",
    "BankObservationEvidence",
    "BankPerceptionResult",
    "BankProfileIdentity",
    "BankingBlocker",
    "BankingError",
    "BankingVerificationResult",
    "BankingWorkflowContext",
    "BankingWorkflowState",
    "CheckpointArrivalEvidence",
    "DepositAttempted",
    "DepositReadiness",
    "InventoryPerceptionResult",
    "OpenBankAttempted",
    "PostDepositInventoryObservation",
    "PostDepositInventoryObservationEvidence",
    "PreDepositInventoryObservation",
    "PreDepositInventoryObservationEvidence",
    "advance_banking_workflow",
    "deposit_readiness",
    "evaluate_bank_observation",
    "evaluate_inventory_observation",
    "initial_banking_workflow_context",
    "run_bank_detector",
    "validate_bank_detector",
]
