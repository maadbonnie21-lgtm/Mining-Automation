"""Type/test-level integration boundary with future navigation and inventory evidence.

This module defines the minimal shape a future Codex B fixed-route/checkpoint
arrival result and a future Codex C approved-inventory result must satisfy to
be accepted as banking evidence, plus pure adapters that convert a conforming
external value into this package's own typed evidence. It does not import,
depend on, merge, or otherwise couple to Codex B's or Codex C's branches or
implementations -- conformance is checked purely structurally (``Protocol``),
exactly the way :class:`~mining_automation.banking.perception.BankDetector`
already lets an unrelated detector implementation plug into this package
without a shared base class.

Composing all three evidence sources (navigation arrival, bank perception,
inventory perception) into one end-to-end "safe to resume mining" decision is
explicitly **not** implemented here -- see ``docs/BANKING.md`` Part G. This
module only proves, at the type/test level, that such a future composition is
*possible* without inventing authority: each adapter validates and repackages
one source's evidence into this package's existing event/observation types,
and nothing more. It does not implement ``WorldState`` and does not decide
when banking is complete.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..contracts import InventoryState
from .contracts import (
    BankCheckpointIdentity,
    BankEvidenceProvenance,
    PostDepositInventoryObservation,
    PreDepositInventoryObservation,
)
from .errors import IntegrationBoundaryContractError
from .workflow import (
    CheckpointArrivalEvidence,
    PostDepositInventoryObservationEvidence,
    PreDepositInventoryObservationEvidence,
)

__all__ = [
    "ExternalApprovedInventoryResult",
    "ExternalCheckpointArrivalSource",
    "adapt_checkpoint_arrival",
    "adapt_post_deposit_inventory",
    "adapt_pre_deposit_inventory",
]


@runtime_checkable
class ExternalCheckpointArrivalSource(Protocol):
    """Minimal shape a future navigation-arrival result (Codex B) must expose."""

    @property
    def identity(self) -> BankCheckpointIdentity: ...

    @property
    def provenance(self) -> BankEvidenceProvenance: ...


@runtime_checkable
class ExternalApprovedInventoryResult(Protocol):
    """Minimal shape a future approved inventory result (Codex C) must expose.

    Matches the duck-typed shape
    :func:`mining_automation.banking.perception.evaluate_inventory_observation`
    already consumes for
    :class:`~mining_automation.banking.contracts.PreDepositInventoryObservation`/
    :class:`~mining_automation.banking.contracts.PostDepositInventoryObservation`.
    """

    @property
    def state(self) -> InventoryState: ...

    @property
    def provenance(self) -> BankEvidenceProvenance: ...

    @property
    def detector_id(self) -> str: ...

    @property
    def detector_version(self) -> str: ...


def adapt_checkpoint_arrival(source: object) -> CheckpointArrivalEvidence:
    """Validate and adapt an external arrival result into a workflow event.

    Raises :class:`IntegrationBoundaryContractError` if ``source`` does not
    conform -- it never guesses or repairs a malformed source.
    """
    if not isinstance(source, ExternalCheckpointArrivalSource):
        raise IntegrationBoundaryContractError(
            "external checkpoint-arrival source must satisfy "
            f"ExternalCheckpointArrivalSource protocol, got {type(source).__name__}"
        )
    identity = source.identity
    provenance = source.provenance
    if type(identity) is not BankCheckpointIdentity:
        raise IntegrationBoundaryContractError(
            f"external arrival source identity must be BankCheckpointIdentity, "
            f"got {type(identity).__name__}"
        )
    if type(provenance) is not BankEvidenceProvenance:
        raise IntegrationBoundaryContractError(
            f"external arrival source provenance must be BankEvidenceProvenance, "
            f"got {type(provenance).__name__}"
        )
    return CheckpointArrivalEvidence(identity=identity, provenance=provenance)


def _validate_external_inventory_result(source: object) -> ExternalApprovedInventoryResult:
    if not isinstance(source, ExternalApprovedInventoryResult):
        raise IntegrationBoundaryContractError(
            "external inventory result must satisfy ExternalApprovedInventoryResult "
            f"protocol, got {type(source).__name__}"
        )
    if type(source.state) is not InventoryState:
        raise IntegrationBoundaryContractError(
            f"external inventory result state must be InventoryState, got {type(source.state).__name__}"
        )
    if type(source.provenance) is not BankEvidenceProvenance:
        raise IntegrationBoundaryContractError(
            "external inventory result provenance must be BankEvidenceProvenance, "
            f"got {type(source.provenance).__name__}"
        )
    if not isinstance(source.detector_id, str) or not source.detector_id.strip():
        raise IntegrationBoundaryContractError(
            "external inventory result detector_id must be a non-empty string"
        )
    if not isinstance(source.detector_version, str) or not source.detector_version.strip():
        raise IntegrationBoundaryContractError(
            "external inventory result detector_version must be a non-empty string"
        )
    return source


def adapt_pre_deposit_inventory(source: object) -> PreDepositInventoryObservationEvidence:
    """Validate and adapt an external inventory result into a pre-deposit workflow event."""
    validated = _validate_external_inventory_result(source)
    observation = PreDepositInventoryObservation(
        state=validated.state,
        provenance=validated.provenance,
        detector_id=validated.detector_id,
        detector_version=validated.detector_version,
    )
    return PreDepositInventoryObservationEvidence(observations=(observation,))


def adapt_post_deposit_inventory(source: object) -> PostDepositInventoryObservationEvidence:
    """Validate and adapt an external inventory result into a post-deposit workflow event."""
    validated = _validate_external_inventory_result(source)
    observation = PostDepositInventoryObservation(
        state=validated.state,
        provenance=validated.provenance,
        detector_id=validated.detector_id,
        detector_version=validated.detector_version,
    )
    return PostDepositInventoryObservationEvidence(observations=(observation,))
