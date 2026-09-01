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
* :mod:`mining_automation.banking.attempts` -- future attempt-receipt
  causality contracts: data-only bookkeeping that a receipt for an
  open-bank/deposit attempt is not a duplicate, matches the evidence the
  workflow currently holds, and is fresh. Never proof of an attempt's outcome.
* :mod:`mining_automation.banking.evidence_intake` -- the immutable future
  bank-evidence intake/reviewer-package design: operator intent vs. reviewer
  truth, cryptographically bound to a finalized package. No pixels collected.
* :mod:`mining_automation.banking.integration_boundary` -- type/test-level
  adapters for a future Codex B navigation-arrival source and a future
  Codex C approved-inventory result, with zero coupling to their branches.

See ``docs/BANKING.md`` for the full architecture, transition matrix,
adversarial fail-closed matrix, and future real-evidence specification this
foundation is built to receive.
"""

from __future__ import annotations

from .attempts import (
    MAX_ATTEMPT_RECEIPT_AGE_S,
    AttemptCausalityResult,
    DepositAttemptReceipt,
    OpenBankAttemptReceipt,
    evaluate_attempt_receipt_causality,
)
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
from .errors import (
    BankDetectorContractError,
    BankDetectorExecutionError,
    BankingError,
    IntegrationBoundaryContractError,
)
from .evidence_intake import (
    MAX_EVIDENCE_PACKAGE_AGE_S,
    REQUIRED_BANK_EVIDENCE_CASES,
    BankEvidenceCase,
    DepositResultEvidenceRecord,
    FinalizedBankEvidencePackage,
    OperatorIntentLabel,
    ReviewedBankEvidenceCase,
    ReviewerVerdict,
    validate_evidence_case_batch,
)
from .integration_boundary import (
    ExternalApprovedInventoryResult,
    ExternalCheckpointArrivalSource,
    adapt_checkpoint_arrival,
    adapt_post_deposit_inventory,
    adapt_pre_deposit_inventory,
)
from .perception import (
    BANK_PUBLICATION_CONFIDENCE_FLOOR,
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
    "BANK_PUBLICATION_CONFIDENCE_FLOOR",
    "INITIAL_BANKING_WORKFLOW_STATE",
    "INVENTORY_CAPACITY",
    "INVENTORY_PUBLICATION_CONFIDENCE_FLOOR",
    "MAX_ATTEMPT_RECEIPT_AGE_S",
    "MAX_BANKING_EVIDENCE_AGE_S",
    "MAX_EVIDENCE_PACKAGE_AGE_S",
    "REQUIRED_BANK_EVIDENCE_CASES",
    "AttemptCausalityResult",
    "BankCheckpointIdentity",
    "BankDetector",
    "BankDetectorContractError",
    "BankDetectorExecutionError",
    "BankDetectorMetadata",
    "BankEvidenceCase",
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
    "DepositAttemptReceipt",
    "DepositReadiness",
    "DepositResultEvidenceRecord",
    "ExternalApprovedInventoryResult",
    "ExternalCheckpointArrivalSource",
    "FinalizedBankEvidencePackage",
    "IntegrationBoundaryContractError",
    "InventoryPerceptionResult",
    "OpenBankAttempted",
    "OpenBankAttemptReceipt",
    "OperatorIntentLabel",
    "PostDepositInventoryObservation",
    "PostDepositInventoryObservationEvidence",
    "PreDepositInventoryObservation",
    "PreDepositInventoryObservationEvidence",
    "ReviewedBankEvidenceCase",
    "ReviewerVerdict",
    "adapt_checkpoint_arrival",
    "adapt_post_deposit_inventory",
    "adapt_pre_deposit_inventory",
    "advance_banking_workflow",
    "deposit_readiness",
    "evaluate_attempt_receipt_causality",
    "evaluate_bank_observation",
    "evaluate_inventory_observation",
    "initial_banking_workflow_context",
    "run_bank_detector",
    "validate_bank_detector",
    "validate_evidence_case_batch",
]
