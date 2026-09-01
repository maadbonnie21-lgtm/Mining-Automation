"""Deterministic, non-input verified banking workflow.

This module is a pure state machine. It never touches RuneLite, the capture
layer, ``WorldState``, or :class:`~mining_automation.controller.MiningController`
-- it only reduces (state, event) pairs to a new state plus an explicit list
of reasons when nothing advanced. Every public function here is a plain
function over immutable values; there is no hidden mutable session object.

The central invariant, enforced structurally rather than by convention, is:

    an attempted input action never proves its own success.

Concretely:

* :class:`OpenBankAttempted` and :class:`DepositAttempted` carry a causal
  receipt, never outcome evidence, and cannot by themselves move the workflow
  into a verified state. Only a *following* fresh observation event can.
* Arrival at a checkpoint (:class:`CheckpointArrivalEvidence`) can only ever
  produce :attr:`BankingWorkflowState.ARRIVED_AT_BANK_CHECKPOINT`. It is
  structurally incapable of producing a bank-open or inventory result --
  those states are only reachable through :class:`BankObservationEvidence`
  and the inventory evidence events.
* Every non-advancing call returns at least one
  :class:`~mining_automation.banking.contracts.BankingBlocker`, so a caller
  can never mistake "nothing happened" for a silent success.
* ``OpenBankAttempted``/``DepositAttempted`` must carry a
  :mod:`mining_automation.banking.attempts` receipt before either pending state
  can be entered. The pending state retains that exact receipt as an exclusive
  post-attempt evidence boundary. A rejected or missing receipt denies the
  transition; an accepted one still never proves the attempt's outcome.

Transition summary::

    AWAITING_CHECKPOINT_ARRIVAL
        --CheckpointArrivalEvidence-->            ARRIVED_AT_BANK_CHECKPOINT

    ARRIVED_AT_BANK_CHECKPOINT
        --BankObservationEvidence(OPEN)-->         BANK_OPEN_VERIFIED
        --BankObservationEvidence(CLOSED)-->       BANK_CLOSED_VERIFIED
        --BankObservationEvidence(UNKNOWN)-->      (unchanged, blocked)

    BANK_CLOSED_VERIFIED
        --OpenBankAttempted-->                     BANK_OPEN_ATTEMPT_PENDING

    BANK_OPEN_ATTEMPT_PENDING
        --BankObservationEvidence(OPEN)-->         BANK_OPEN_VERIFIED
        --BankObservationEvidence(CLOSED)-->       BANK_CLOSED_VERIFIED
        --BankObservationEvidence(UNKNOWN)-->      (unchanged, blocked)

    BANK_OPEN_VERIFIED
        --PreDepositInventoryObservationEvidence(known non-empty)-->
                                                    DEPOSIT_READY_VERIFIED

    DEPOSIT_READY_VERIFIED
        --DepositAttempted-->                      DEPOSIT_ATTEMPT_PENDING

    DEPOSIT_ATTEMPT_PENDING
        --PostDepositInventoryObservationEvidence(known empty)-->
                                                    BANKING_COMPLETE
        --PostDepositInventoryObservationEvidence(known non-empty)-->
                                                    (unchanged, blocked)

    BANK_CLOSED_VERIFIED / BANK_OPEN_VERIFIED / DEPOSIT_READY_VERIFIED /
    DEPOSIT_ATTEMPT_PENDING
        --fresh BankObservationEvidence(OPEN)-->    BANK_OPEN_VERIFIED
        --fresh BankObservationEvidence(CLOSED)-->  BANK_CLOSED_VERIFIED
        --fresh BankObservationEvidence(UNKNOWN)--> ARRIVED_AT_BANK_CHECKPOINT,
                                                    blocked

    BANKING_COMPLETE is terminal; start a new context for the next visit.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from typing import Final

from .attempts import (
    MAX_ATTEMPT_RECEIPT_AGE_S,
    DepositAttemptReceipt,
    OpenBankAttemptReceipt,
    evaluate_attempt_receipt_causality,
)
from .contracts import (
    BankCheckpointIdentity,
    BankEvidenceProvenance,
    BankingBlocker,
    BankInterfaceState,
    BankObservation,
    BankProfileIdentity,
    DepositReadiness,
    PostDepositInventoryObservation,
    PreDepositInventoryObservation,
)
from .perception import (
    MAX_BANKING_EVIDENCE_AGE_S,
    BankDetectorMetadata,
    evaluate_bank_observation,
    evaluate_inventory_observation,
)

__all__ = [
    "INITIAL_BANKING_WORKFLOW_STATE",
    "BankObservationEvidence",
    "BankingWorkflowContext",
    "BankingWorkflowState",
    "CheckpointArrivalEvidence",
    "DepositAttempted",
    "OpenBankAttempted",
    "PostDepositInventoryObservationEvidence",
    "PreDepositInventoryObservationEvidence",
    "advance_banking_workflow",
    "deposit_readiness",
    "initial_banking_workflow_context",
]


def _finite_float(value: object) -> float | None:
    if type(value) is int:
        try:
            return float(value)
        except OverflowError:
            return None
    if type(value) is float:
        converted = value
        return converted if isfinite(converted) else None
    return None


def _post_attempt_freshness_blocker(
    receipt: OpenBankAttemptReceipt | DepositAttemptReceipt,
    provenance: BankEvidenceProvenance,
) -> BankingBlocker | None:
    """Require a capture from the bounded interval immediately after an attempt."""
    captured = _finite_float(provenance.frame.captured_monotonic_s)
    issued = _finite_float(receipt.issued_monotonic_s)
    if captured is None or issued is None or captured <= issued:
        return BankingBlocker.POST_ATTEMPT_EVIDENCE_NOT_FRESH
    if captured - issued > MAX_ATTEMPT_RECEIPT_AGE_S:
        return BankingBlocker.POST_ATTEMPT_EVIDENCE_STALE
    return None


def _supporting_evidence_freshness_blocker(
    provenance: BankEvidenceProvenance,
    *,
    evaluated_monotonic_s: object,
) -> BankingBlocker | None:
    """Prevent cached prerequisite evidence from becoming durable authority."""
    evaluated = _finite_float(evaluated_monotonic_s)
    captured = _finite_float(provenance.frame.captured_monotonic_s)
    if evaluated is None:
        return BankingBlocker.EVALUATION_TIME_INVALID
    if captured is None:
        return BankingBlocker.EVIDENCE_TIMESTAMP_INVALID
    age_s = evaluated - captured
    if age_s < 0.0:
        return BankingBlocker.EVIDENCE_FROM_FUTURE
    if age_s > MAX_BANKING_EVIDENCE_AGE_S:
        return BankingBlocker.SUPPORTING_EVIDENCE_STALE
    return None


class BankingWorkflowState(StrEnum):
    """Deterministic states of one bank visit."""

    AWAITING_CHECKPOINT_ARRIVAL = "awaiting_checkpoint_arrival"
    ARRIVED_AT_BANK_CHECKPOINT = "arrived_at_bank_checkpoint"
    BANK_CLOSED_VERIFIED = "bank_closed_verified"
    BANK_OPEN_ATTEMPT_PENDING = "bank_open_attempt_pending"
    BANK_OPEN_VERIFIED = "bank_open_verified"
    DEPOSIT_READY_VERIFIED = "deposit_ready_verified"
    DEPOSIT_ATTEMPT_PENDING = "deposit_attempt_pending"
    BANKING_COMPLETE = "banking_complete"


INITIAL_BANKING_WORKFLOW_STATE: Final[BankingWorkflowState] = (
    BankingWorkflowState.AWAITING_CHECKPOINT_ARRIVAL
)


@dataclass(frozen=True, slots=True)
class CheckpointArrivalEvidence:
    """Claim that navigation delivered the agent to one bank checkpoint.

    This proves arrival only -- it can never be substituted for a bank
    observation. The reducer only ever consumes it from
    :attr:`BankingWorkflowState.AWAITING_CHECKPOINT_ARRIVAL`.
    """

    identity: BankCheckpointIdentity
    provenance: BankEvidenceProvenance

    def __post_init__(self) -> None:
        if type(self.identity) is not BankCheckpointIdentity:
            raise ValueError("identity must be an exact BankCheckpointIdentity")
        if type(self.provenance) is not BankEvidenceProvenance:
            raise ValueError("provenance must be an exact BankEvidenceProvenance")
        BankCheckpointIdentity.__post_init__(self.identity)
        BankEvidenceProvenance.__post_init__(self.provenance)


@dataclass(frozen=True, slots=True)
class BankObservationEvidence:
    """One or more same-step bank-interface readings to resolve together.

    More than one entry is only meaningful for exercising the
    duplicate/conflicting-observation guard; a well-behaved caller supplies
    exactly one.
    """

    observations: tuple[BankObservation, ...]

    def __post_init__(self) -> None:
        if type(self.observations) is not tuple or any(
            type(item) is not BankObservation for item in self.observations
        ):
            raise ValueError("observations must be a tuple of exact BankObservation values")
        for observation in self.observations:
            BankObservation.__post_init__(observation)


@dataclass(frozen=True, slots=True)
class OpenBankAttempted:
    """A marker that an open-bank interaction was attempted.

    Carries no evidence -- ``receipt`` is causality bookkeeping only (see
    :mod:`mining_automation.banking.attempts`), never proof of the attempt's
    outcome. Cannot advance the workflow past a "pending" state on its own.
    A missing, duplicate, wrong-provenance, or stale ``receipt`` denies the
    transition outright (see :func:`advance_banking_workflow`).
    """

    receipt: OpenBankAttemptReceipt | None = None

    def __post_init__(self) -> None:
        if self.receipt is not None and type(self.receipt) is not OpenBankAttemptReceipt:
            raise ValueError("receipt must be an exact OpenBankAttemptReceipt or None")
        if self.receipt is not None:
            OpenBankAttemptReceipt.__post_init__(self.receipt)


@dataclass(frozen=True, slots=True)
class PreDepositInventoryObservationEvidence:
    """One or more same-step pre-deposit inventory readings to resolve together."""

    observations: tuple[PreDepositInventoryObservation, ...]

    def __post_init__(self) -> None:
        if type(self.observations) is not tuple or any(
            type(item) is not PreDepositInventoryObservation for item in self.observations
        ):
            raise ValueError(
                "observations must be a tuple of exact PreDepositInventoryObservation values"
            )
        for observation in self.observations:
            PreDepositInventoryObservation.__post_init__(observation)


@dataclass(frozen=True, slots=True)
class DepositAttempted:
    """A marker that a deposit interaction was attempted.

    Carries no evidence -- ``receipt`` is causality bookkeeping only, never
    proof of the attempt's outcome. See :class:`OpenBankAttempted` for the
    same ``receipt`` contract.
    """

    receipt: DepositAttemptReceipt | None = None

    def __post_init__(self) -> None:
        if self.receipt is not None and type(self.receipt) is not DepositAttemptReceipt:
            raise ValueError("receipt must be an exact DepositAttemptReceipt or None")
        if self.receipt is not None:
            DepositAttemptReceipt.__post_init__(self.receipt)


@dataclass(frozen=True, slots=True)
class PostDepositInventoryObservationEvidence:
    """One or more same-step post-deposit inventory readings to resolve together."""

    observations: tuple[PostDepositInventoryObservation, ...]

    def __post_init__(self) -> None:
        if type(self.observations) is not tuple or any(
            type(item) is not PostDepositInventoryObservation for item in self.observations
        ):
            raise ValueError(
                "observations must be a tuple of exact PostDepositInventoryObservation values"
            )
        for observation in self.observations:
            PostDepositInventoryObservation.__post_init__(observation)


BankingWorkflowEvent = (
    CheckpointArrivalEvidence
    | BankObservationEvidence
    | OpenBankAttempted
    | PreDepositInventoryObservationEvidence
    | DepositAttempted
    | PostDepositInventoryObservationEvidence
)


_CONTEXT_ISSUER = object()


@dataclass(frozen=True, slots=True, init=False)
class BankingWorkflowContext:
    """Immutable workflow state plus the fixed identity of the current visit.

    ``blockers`` explains why the *previous* call to
    :func:`advance_banking_workflow` could not grant or retain authority. A
    denial either preserves state or revokes it to a safer boundary; blockers
    are empty after accepted evidence advances or safely reclassifies state,
    and after construction via :func:`initial_banking_workflow_context`.
    """

    state: BankingWorkflowState
    expected_checkpoint: BankCheckpointIdentity
    expected_profile: BankProfileIdentity
    expected_detector: BankDetectorMetadata
    blockers: tuple[BankingBlocker, ...]
    last_accepted_provenance: BankEvidenceProvenance | None
    used_attempt_receipt_ids: frozenset[str] = field(default_factory=frozenset)
    pending_attempt_receipt: OpenBankAttemptReceipt | DepositAttemptReceipt | None = None
    _issued_snapshot: tuple[object, ...] = field(init=False, repr=False, compare=False)

    def __init__(
        self,
        *,
        state: BankingWorkflowState,
        expected_checkpoint: BankCheckpointIdentity,
        expected_profile: BankProfileIdentity,
        expected_detector: BankDetectorMetadata,
        blockers: tuple[BankingBlocker, ...],
        last_accepted_provenance: BankEvidenceProvenance | None,
        used_attempt_receipt_ids: frozenset[str] = frozenset(),
        pending_attempt_receipt: OpenBankAttemptReceipt | DepositAttemptReceipt | None = None,
        _issuer: object = None,
    ) -> None:
        if _issuer is not _CONTEXT_ISSUER:
            raise TypeError(
                "BankingWorkflowContext is reducer-issued; use "
                "initial_banking_workflow_context and advance_banking_workflow"
            )
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "expected_checkpoint", expected_checkpoint)
        object.__setattr__(self, "expected_profile", expected_profile)
        object.__setattr__(self, "expected_detector", expected_detector)
        object.__setattr__(self, "blockers", blockers)
        object.__setattr__(self, "last_accepted_provenance", last_accepted_provenance)
        object.__setattr__(self, "used_attempt_receipt_ids", used_attempt_receipt_ids)
        object.__setattr__(self, "pending_attempt_receipt", pending_attempt_receipt)
        self.__post_init__()
        object.__setattr__(self, "_issued_snapshot", self._snapshot())

    def _snapshot(self) -> tuple[object, ...]:
        def provenance_snapshot(
            provenance: BankEvidenceProvenance | None,
        ) -> tuple[object, ...] | None:
            if provenance is None:
                return None
            return (
                provenance.frame.frame_id,
                provenance.frame.captured_monotonic_s,
                provenance.frame.width,
                provenance.frame.height,
                provenance.cycle_id,
                provenance.frame_sha256,
            )

        receipt = self.pending_attempt_receipt
        receipt_snapshot: tuple[object, ...] | None = None
        if receipt is not None:
            receipt_snapshot = (
                type(receipt).__name__,
                receipt.attempt_id,
                receipt.issued_monotonic_s,
                provenance_snapshot(receipt.preceding_provenance),
            )
        return (
            self.state.value,
            (
                self.expected_checkpoint.checkpoint_id,
                self.expected_checkpoint.location_id,
            ),
            (
                self.expected_profile.profile_id,
                self.expected_profile.profile_version,
                self.expected_profile.schema_version,
                self.expected_profile.frame_width,
                self.expected_profile.frame_height,
            ),
            (self.expected_detector.detector_id, self.expected_detector.version),
            tuple(blocker.value for blocker in self.blockers),
            provenance_snapshot(self.last_accepted_provenance),
            tuple(sorted(self.used_attempt_receipt_ids)),
            receipt_snapshot,
        )

    def __post_init__(self) -> None:
        if type(self.state) is not BankingWorkflowState:
            raise ValueError("state must be an exact BankingWorkflowState")
        if type(self.expected_checkpoint) is not BankCheckpointIdentity:
            raise ValueError("expected_checkpoint must be an exact BankCheckpointIdentity")
        if type(self.expected_profile) is not BankProfileIdentity:
            raise ValueError("expected_profile must be an exact BankProfileIdentity")
        if type(self.expected_detector) is not BankDetectorMetadata:
            raise ValueError("expected_detector must be an exact BankDetectorMetadata")
        BankCheckpointIdentity.__post_init__(self.expected_checkpoint)
        BankProfileIdentity.__post_init__(self.expected_profile)
        BankDetectorMetadata.__post_init__(self.expected_detector)
        if not isinstance(self.blockers, tuple) or any(
            type(blocker) is not BankingBlocker for blocker in self.blockers
        ):
            raise ValueError("blockers must be a tuple of exact BankingBlocker values")
        if len(set(self.blockers)) != len(self.blockers):
            raise ValueError("blockers must be unique")
        if (
            self.last_accepted_provenance is not None
            and type(self.last_accepted_provenance) is not BankEvidenceProvenance
        ):
            raise ValueError("last_accepted_provenance must be an exact BankEvidenceProvenance")
        if self.last_accepted_provenance is not None:
            BankEvidenceProvenance.__post_init__(self.last_accepted_provenance)
        if (
            self.state is not BankingWorkflowState.AWAITING_CHECKPOINT_ARRIVAL
            and self.last_accepted_provenance is None
        ):
            raise ValueError(
                "every state past AWAITING_CHECKPOINT_ARRIVAL requires accepted provenance"
            )
        if type(self.used_attempt_receipt_ids) is not frozenset or any(
            type(item) is not str or not item.strip() for item in self.used_attempt_receipt_ids
        ):
            raise ValueError("used_attempt_receipt_ids must be a frozenset of non-empty str")
        if self.state is BankingWorkflowState.BANK_OPEN_ATTEMPT_PENDING:
            if type(self.pending_attempt_receipt) is not OpenBankAttemptReceipt:
                raise ValueError("bank-open pending state requires an exact open attempt receipt")
        elif self.state is BankingWorkflowState.DEPOSIT_ATTEMPT_PENDING:
            if type(self.pending_attempt_receipt) is not DepositAttemptReceipt:
                raise ValueError("deposit pending state requires an exact deposit attempt receipt")
        elif self.pending_attempt_receipt is not None:
            raise ValueError("only an attempt-pending state may retain an attempt receipt")
        if self.pending_attempt_receipt is not None and (
            self.pending_attempt_receipt.attempt_id not in self.used_attempt_receipt_ids
            or self.pending_attempt_receipt.preceding_provenance != self.last_accepted_provenance
        ):
            raise ValueError(
                "pending attempt receipt must be used and bind the last accepted provenance"
            )
        if type(self.pending_attempt_receipt) is OpenBankAttemptReceipt:
            OpenBankAttemptReceipt.__post_init__(self.pending_attempt_receipt)
        elif type(self.pending_attempt_receipt) is DepositAttemptReceipt:
            DepositAttemptReceipt.__post_init__(self.pending_attempt_receipt)
        if hasattr(self, "_issued_snapshot") and self._issued_snapshot != self._snapshot():
            raise ValueError("banking workflow context differs from its reducer-issued snapshot")

    @property
    def complete(self) -> bool:
        self.__post_init__()
        return self.state is BankingWorkflowState.BANKING_COMPLETE


def initial_banking_workflow_context(
    *,
    expected_checkpoint: BankCheckpointIdentity,
    expected_profile: BankProfileIdentity,
    expected_detector: BankDetectorMetadata,
) -> BankingWorkflowContext:
    """Return the fresh starting context for one bank visit."""
    return BankingWorkflowContext(
        state=INITIAL_BANKING_WORKFLOW_STATE,
        expected_checkpoint=expected_checkpoint,
        expected_profile=expected_profile,
        expected_detector=expected_detector,
        blockers=(),
        last_accepted_provenance=None,
        used_attempt_receipt_ids=frozenset(),
        pending_attempt_receipt=None,
        _issuer=_CONTEXT_ISSUER,
    )


def deposit_readiness(
    context: BankingWorkflowContext,
    *,
    evaluated_monotonic_s: object,
) -> DepositReadiness:
    """Whether ``context`` currently permits a deposit attempt.

    ``READY`` only when the workflow has jointly verified, in the current
    visit, that the bank is open and that inventory is known non-empty --
    never from an attempt alone, a denied transition, or a stale reading.
    Readiness is a time-bounded snapshot, not durable input authority.
    """
    if type(context) is not BankingWorkflowContext:
        raise TypeError("context must be an exact BankingWorkflowContext")
    BankingWorkflowContext.__post_init__(context)
    if context.state is not BankingWorkflowState.DEPOSIT_READY_VERIFIED or context.blockers:
        return DepositReadiness.NOT_READY
    assert context.last_accepted_provenance is not None
    freshness_blocker = _supporting_evidence_freshness_blocker(
        context.last_accepted_provenance,
        evaluated_monotonic_s=evaluated_monotonic_s,
    )
    return DepositReadiness.READY if freshness_blocker is None else DepositReadiness.NOT_READY


def _denied(
    context: BankingWorkflowContext, blockers: tuple[BankingBlocker, ...]
) -> BankingWorkflowContext:
    if not blockers:  # pragma: no cover - caller invariant, every call site passes >=1 blocker
        raise ValueError("a denied transition must carry at least one blocker")
    return BankingWorkflowContext(
        state=context.state,
        expected_checkpoint=context.expected_checkpoint,
        expected_profile=context.expected_profile,
        expected_detector=context.expected_detector,
        blockers=blockers,
        last_accepted_provenance=context.last_accepted_provenance,
        used_attempt_receipt_ids=context.used_attempt_receipt_ids,
        pending_attempt_receipt=context.pending_attempt_receipt,
        _issuer=_CONTEXT_ISSUER,
    )


def _advanced(
    context: BankingWorkflowContext,
    new_state: BankingWorkflowState,
    *,
    provenance: BankEvidenceProvenance,
    used_attempt_receipt_ids: frozenset[str] | None = None,
    pending_attempt_receipt: OpenBankAttemptReceipt | DepositAttemptReceipt | None = None,
) -> BankingWorkflowContext:
    return BankingWorkflowContext(
        state=new_state,
        expected_checkpoint=context.expected_checkpoint,
        expected_profile=context.expected_profile,
        expected_detector=context.expected_detector,
        blockers=(),
        last_accepted_provenance=provenance,
        used_attempt_receipt_ids=(
            context.used_attempt_receipt_ids
            if used_attempt_receipt_ids is None
            else used_attempt_receipt_ids
        ),
        pending_attempt_receipt=pending_attempt_receipt,
        _issuer=_CONTEXT_ISSUER,
    )


def _resolve_single(
    observations: tuple[object, ...],
    *,
    missing_blocker: BankingBlocker,
    conflict_blocker: BankingBlocker,
) -> tuple[object | None, BankingBlocker | None]:
    if not observations:
        return None, missing_blocker
    first = observations[0]
    if any(item != first for item in observations[1:]):
        return None, conflict_blocker
    return first, None


def _invalidate_bank_authority(
    context: BankingWorkflowContext,
    blockers: tuple[BankingBlocker, ...],
    *,
    provenance: BankEvidenceProvenance | None = None,
) -> BankingWorkflowContext:
    """Drop established bank/readiness/deposit-pending authority safely."""
    retained_provenance = provenance or context.last_accepted_provenance
    assert retained_provenance is not None
    invalidated = _advanced(
        context,
        BankingWorkflowState.ARRIVED_AT_BANK_CHECKPOINT,
        provenance=retained_provenance,
    )
    return _denied(invalidated, blockers)


def _handle_bank_authority_reobservation(
    context: BankingWorkflowContext,
    event: BankObservationEvidence,
    evaluated_monotonic_s: object,
) -> BankingWorkflowContext:
    """Consume a newer bank reading without carrying older OPEN authority through it."""
    try:
        BankObservationEvidence.__post_init__(event)
    except ValueError:
        return _invalidate_bank_authority(context, (BankingBlocker.BANK_EVIDENCE_TYPE_INVALID,))

    observation, resolve_blocker = _resolve_single(
        event.observations,
        missing_blocker=BankingBlocker.BANK_OBSERVATION_MISSING,
        conflict_blocker=BankingBlocker.DUPLICATE_CONFLICTING_BANK_OBSERVATIONS,
    )
    if resolve_blocker is not None:
        return _invalidate_bank_authority(context, (resolve_blocker,))
    assert type(observation) is BankObservation

    result = evaluate_bank_observation(
        observation,
        expected_checkpoint=context.expected_checkpoint,
        expected_profile=context.expected_profile,
        expected_detector=context.expected_detector,
        evaluated_monotonic_s=evaluated_monotonic_s,
        previous_provenance=context.last_accepted_provenance,
    )
    if not result.accepted:
        return _invalidate_bank_authority(context, result.blockers)
    if result.interface_state is BankInterfaceState.OPEN:
        return _advanced(
            context,
            BankingWorkflowState.BANK_OPEN_VERIFIED,
            provenance=observation.provenance,
        )
    if result.interface_state is BankInterfaceState.CLOSED:
        return _advanced(
            context,
            BankingWorkflowState.BANK_CLOSED_VERIFIED,
            provenance=observation.provenance,
        )
    return _invalidate_bank_authority(
        context,
        (BankingBlocker.BANK_STATE_UNKNOWN,),
        provenance=observation.provenance,
    )


def advance_banking_workflow(
    context: BankingWorkflowContext,
    event: BankingWorkflowEvent,
    *,
    evaluated_monotonic_s: object,
) -> BankingWorkflowContext:
    """Reduce one (context, event) pair to the next context.

    Never raises for a domain-level denial -- every fail-closed outcome is
    returned with a populated ``blockers`` tuple and can only preserve or
    revoke authority, never increase it. A :class:`TypeError`/:class:`ValueError`
    only signals a genuine caller bug (wrong argument types).
    """
    if type(context) is not BankingWorkflowContext:
        raise TypeError("context must be an exact BankingWorkflowContext")
    BankingWorkflowContext.__post_init__(context)

    handler = _STATE_HANDLERS.get(context.state)
    if handler is None:  # pragma: no cover - defensive, all states are mapped
        raise ValueError(f"no handler registered for state {context.state}")
    return handler(context, event, evaluated_monotonic_s)


def _handle_awaiting_arrival(
    context: BankingWorkflowContext,
    event: BankingWorkflowEvent,
    evaluated_monotonic_s: object,
) -> BankingWorkflowContext:
    if type(event) is not CheckpointArrivalEvidence:
        return _denied(context, (BankingBlocker.ARRIVAL_EVIDENCE_MISSING,))
    try:
        CheckpointArrivalEvidence.__post_init__(event)
        BankCheckpointIdentity.__post_init__(event.identity)
        BankEvidenceProvenance.__post_init__(event.provenance)
    except ValueError:
        return _denied(context, (BankingBlocker.ARRIVAL_EVIDENCE_TYPE_INVALID,))
    if event.identity != context.expected_checkpoint:
        return _denied(context, (BankingBlocker.CHECKPOINT_IDENTITY_MISMATCH,))

    evaluated = _finite_float(evaluated_monotonic_s)
    if evaluated is None:
        return _denied(context, (BankingBlocker.EVALUATION_TIME_INVALID,))
    captured = _finite_float(event.provenance.frame.captured_monotonic_s)
    if captured is None:  # pragma: no cover - FrameRef already guarantees this is finite
        return _denied(context, (BankingBlocker.EVIDENCE_TIMESTAMP_INVALID,))
    age_s = evaluated - captured
    if age_s < 0.0:
        return _denied(context, (BankingBlocker.EVIDENCE_FROM_FUTURE,))
    if age_s > MAX_BANKING_EVIDENCE_AGE_S:
        return _denied(context, (BankingBlocker.ARRIVAL_EVIDENCE_STALE,))

    return _advanced(
        context, BankingWorkflowState.ARRIVED_AT_BANK_CHECKPOINT, provenance=event.provenance
    )


def _handle_awaiting_bank_observation(
    context: BankingWorkflowContext,
    event: BankingWorkflowEvent,
    evaluated_monotonic_s: object,
) -> BankingWorkflowContext:
    if type(event) is OpenBankAttempted or type(event) is DepositAttempted:
        # From ARRIVED_AT_BANK_CHECKPOINT this is arrival evidence (or nothing)
        # being used as if it proved a bank state. From BANK_OPEN_ATTEMPT_PENDING
        # it is a second attempt substituting for the fresh observation the
        # first attempt still requires. Both are the same underlying mistake
        # -- treating an attempt as its own proof -- named for their context.
        if context.state is BankingWorkflowState.BANK_OPEN_ATTEMPT_PENDING:
            return _denied(context, (BankingBlocker.OPEN_ATTEMPT_WITHOUT_VERIFICATION,))
        return _denied(context, (BankingBlocker.ARRIVAL_SUBSTITUTED_FOR_OBSERVATION,))
    if type(event) is not BankObservationEvidence:
        return _denied(context, (BankingBlocker.UNEXPECTED_EVENT_FOR_STATE,))
    try:
        BankObservationEvidence.__post_init__(event)
    except ValueError:
        return _denied(context, (BankingBlocker.BANK_EVIDENCE_TYPE_INVALID,))

    observation, resolve_blocker = _resolve_single(
        event.observations,
        missing_blocker=BankingBlocker.BANK_OBSERVATION_MISSING,
        conflict_blocker=BankingBlocker.DUPLICATE_CONFLICTING_BANK_OBSERVATIONS,
    )
    if resolve_blocker is not None:
        return _denied(context, (resolve_blocker,))
    assert type(observation) is BankObservation  # event contract guarantees this

    # No current_provenance: this reducer has no independent capture-layer
    # source to check the observation's provenance against, only the
    # observation itself and the previous step's accepted provenance.
    is_pending_attempt = context.state is BankingWorkflowState.BANK_OPEN_ATTEMPT_PENDING
    result = evaluate_bank_observation(
        observation,
        expected_checkpoint=context.expected_checkpoint,
        expected_profile=context.expected_profile,
        expected_detector=context.expected_detector,
        evaluated_monotonic_s=evaluated_monotonic_s,
        previous_provenance=None if is_pending_attempt else context.last_accepted_provenance,
    )
    if not result.accepted:
        return _denied(context, result.blockers)
    if is_pending_attempt:
        receipt = context.pending_attempt_receipt
        assert type(receipt) is OpenBankAttemptReceipt
        freshness_blocker = _post_attempt_freshness_blocker(receipt, observation.provenance)
        if freshness_blocker is not None:
            return _denied(context, (freshness_blocker,))
        result = evaluate_bank_observation(
            observation,
            expected_checkpoint=context.expected_checkpoint,
            expected_profile=context.expected_profile,
            expected_detector=context.expected_detector,
            evaluated_monotonic_s=evaluated_monotonic_s,
            previous_provenance=context.last_accepted_provenance,
        )
        if not result.accepted:
            return _denied(context, result.blockers)
    else:
        assert context.state is BankingWorkflowState.ARRIVED_AT_BANK_CHECKPOINT
        assert context.last_accepted_provenance is not None
        support_blocker = _supporting_evidence_freshness_blocker(
            context.last_accepted_provenance,
            evaluated_monotonic_s=evaluated_monotonic_s,
        )
        if support_blocker is not None:
            return _denied(context, (support_blocker,))
    if result.interface_state is BankInterfaceState.OPEN:
        return _advanced(
            context, BankingWorkflowState.BANK_OPEN_VERIFIED, provenance=observation.provenance
        )
    if result.interface_state is BankInterfaceState.CLOSED:
        return _advanced(
            context, BankingWorkflowState.BANK_CLOSED_VERIFIED, provenance=observation.provenance
        )
    return _denied(context, (BankingBlocker.BANK_STATE_UNKNOWN,))


def _handle_bank_closed_verified(
    context: BankingWorkflowContext,
    event: BankingWorkflowEvent,
    evaluated_monotonic_s: object,
) -> BankingWorkflowContext:
    if type(event) is BankObservationEvidence:
        return _handle_bank_authority_reobservation(context, event, evaluated_monotonic_s)
    if type(event) is not OpenBankAttempted:
        return _denied(context, (BankingBlocker.UNEXPECTED_EVENT_FOR_STATE,))
    if context.blockers:
        return _denied(context, context.blockers)
    try:
        OpenBankAttempted.__post_init__(event)
    except ValueError:
        return _denied(context, (BankingBlocker.ATTEMPT_RECEIPT_TYPE_INVALID,))
    assert (
        context.last_accepted_provenance is not None
    )  # invariant of this state, see __post_init__

    if event.receipt is None:
        return _denied(context, (BankingBlocker.ATTEMPT_RECEIPT_MISSING,))

    causality = evaluate_attempt_receipt_causality(
        event.receipt,
        expected_preceding_provenance=context.last_accepted_provenance,
        used_attempt_ids=context.used_attempt_receipt_ids,
        evaluated_monotonic_s=evaluated_monotonic_s,
    )
    if not causality.accepted:
        return _denied(context, causality.blockers)
    return _advanced(
        context,
        BankingWorkflowState.BANK_OPEN_ATTEMPT_PENDING,
        provenance=context.last_accepted_provenance,
        used_attempt_receipt_ids=context.used_attempt_receipt_ids | {event.receipt.attempt_id},
        pending_attempt_receipt=event.receipt,
    )


def _handle_bank_open_verified(
    context: BankingWorkflowContext,
    event: BankingWorkflowEvent,
    evaluated_monotonic_s: object,
) -> BankingWorkflowContext:
    if type(event) is BankObservationEvidence:
        return _handle_bank_authority_reobservation(context, event, evaluated_monotonic_s)
    if type(event) is DepositAttempted:
        return _denied(context, (BankingBlocker.DEPOSIT_WITHOUT_INVENTORY_VERIFICATION,))
    if type(event) is not PreDepositInventoryObservationEvidence:
        return _denied(context, (BankingBlocker.UNEXPECTED_EVENT_FOR_STATE,))
    if context.blockers:
        return _denied(context, context.blockers)
    try:
        PreDepositInventoryObservationEvidence.__post_init__(event)
    except ValueError:
        return _denied(context, (BankingBlocker.INVENTORY_EVIDENCE_TYPE_INVALID,))

    observation, resolve_blocker = _resolve_single(
        event.observations,
        missing_blocker=BankingBlocker.INVENTORY_EVIDENCE_MISSING,
        conflict_blocker=BankingBlocker.DUPLICATE_CONFLICTING_INVENTORY_OBSERVATIONS,
    )
    if resolve_blocker is not None:
        return _denied(context, (resolve_blocker,))
    assert type(observation) is PreDepositInventoryObservation

    result = evaluate_inventory_observation(
        observation,
        evaluated_monotonic_s=evaluated_monotonic_s,
        previous_provenance=context.last_accepted_provenance,
    )
    if not result.accepted:
        return _denied(context, result.blockers)
    assert context.last_accepted_provenance is not None
    support_blocker = _supporting_evidence_freshness_blocker(
        context.last_accepted_provenance,
        evaluated_monotonic_s=evaluated_monotonic_s,
    )
    if support_blocker is not None:
        return _denied(context, (support_blocker,))
    if result.state.occupied_slots == 0:
        return _denied(context, (BankingBlocker.INVENTORY_ALREADY_EMPTY,))
    return _advanced(
        context, BankingWorkflowState.DEPOSIT_READY_VERIFIED, provenance=observation.provenance
    )


def _handle_deposit_ready_verified(
    context: BankingWorkflowContext,
    event: BankingWorkflowEvent,
    evaluated_monotonic_s: object,
) -> BankingWorkflowContext:
    if type(event) is BankObservationEvidence:
        return _handle_bank_authority_reobservation(context, event, evaluated_monotonic_s)
    if type(event) is not DepositAttempted:
        return _denied(context, (BankingBlocker.UNEXPECTED_EVENT_FOR_STATE,))
    if context.blockers:
        return _denied(context, context.blockers)
    try:
        DepositAttempted.__post_init__(event)
    except ValueError:
        return _denied(context, (BankingBlocker.ATTEMPT_RECEIPT_TYPE_INVALID,))
    assert (
        context.last_accepted_provenance is not None
    )  # invariant of this state, see __post_init__

    if event.receipt is None:
        return _denied(context, (BankingBlocker.ATTEMPT_RECEIPT_MISSING,))

    causality = evaluate_attempt_receipt_causality(
        event.receipt,
        expected_preceding_provenance=context.last_accepted_provenance,
        used_attempt_ids=context.used_attempt_receipt_ids,
        evaluated_monotonic_s=evaluated_monotonic_s,
    )
    if not causality.accepted:
        return _denied(context, causality.blockers)
    return _advanced(
        context,
        BankingWorkflowState.DEPOSIT_ATTEMPT_PENDING,
        provenance=context.last_accepted_provenance,
        used_attempt_receipt_ids=context.used_attempt_receipt_ids | {event.receipt.attempt_id},
        pending_attempt_receipt=event.receipt,
    )


def _handle_deposit_attempt_pending(
    context: BankingWorkflowContext,
    event: BankingWorkflowEvent,
    evaluated_monotonic_s: object,
) -> BankingWorkflowContext:
    if type(event) is BankObservationEvidence:
        return _handle_bank_authority_reobservation(context, event, evaluated_monotonic_s)
    if type(event) is not PostDepositInventoryObservationEvidence:
        return _denied(context, (BankingBlocker.UNEXPECTED_EVENT_FOR_STATE,))
    try:
        PostDepositInventoryObservationEvidence.__post_init__(event)
    except ValueError:
        return _denied(context, (BankingBlocker.INVENTORY_EVIDENCE_TYPE_INVALID,))

    observation, resolve_blocker = _resolve_single(
        event.observations,
        missing_blocker=BankingBlocker.INVENTORY_EVIDENCE_MISSING,
        conflict_blocker=BankingBlocker.DUPLICATE_CONFLICTING_INVENTORY_OBSERVATIONS,
    )
    if resolve_blocker is not None:
        return _denied(context, (resolve_blocker,))
    assert type(observation) is PostDepositInventoryObservation

    result = evaluate_inventory_observation(
        observation,
        evaluated_monotonic_s=evaluated_monotonic_s,
        previous_provenance=None,
    )
    if not result.accepted:
        # INVENTORY_UNKNOWN is generic across pre/post-deposit evaluation;
        # sharpen it here since "deposit attempted and the result is unknown"
        # is a distinct, explicitly-required diagnosable case.
        blockers = tuple(
            BankingBlocker.POST_DEPOSIT_INVENTORY_UNKNOWN
            if blocker is BankingBlocker.INVENTORY_UNKNOWN
            else blocker
            for blocker in result.blockers
        )
        return _denied(context, blockers)
    receipt = context.pending_attempt_receipt
    assert type(receipt) is DepositAttemptReceipt
    freshness_blocker = _post_attempt_freshness_blocker(receipt, observation.provenance)
    if freshness_blocker is not None:
        return _denied(context, (freshness_blocker,))
    result = evaluate_inventory_observation(
        observation,
        evaluated_monotonic_s=evaluated_monotonic_s,
        previous_provenance=context.last_accepted_provenance,
    )
    if not result.accepted:
        blockers = tuple(
            BankingBlocker.POST_DEPOSIT_INVENTORY_UNKNOWN
            if blocker is BankingBlocker.INVENTORY_UNKNOWN
            else blocker
            for blocker in result.blockers
        )
        return _denied(context, blockers)
    if result.state.occupied_slots != 0:
        return _denied(context, (BankingBlocker.DEPOSIT_INVENTORY_STILL_NON_EMPTY,))
    return _advanced(
        context, BankingWorkflowState.BANKING_COMPLETE, provenance=observation.provenance
    )


def _handle_terminal(
    context: BankingWorkflowContext,
    event: BankingWorkflowEvent,
    evaluated_monotonic_s: object,
) -> BankingWorkflowContext:
    del event, evaluated_monotonic_s
    return _denied(context, (BankingBlocker.UNEXPECTED_EVENT_FOR_STATE,))


_StateHandler = Callable[
    [BankingWorkflowContext, BankingWorkflowEvent, object], BankingWorkflowContext
]

_STATE_HANDLERS: Final[dict[BankingWorkflowState, _StateHandler]] = {
    BankingWorkflowState.AWAITING_CHECKPOINT_ARRIVAL: _handle_awaiting_arrival,
    BankingWorkflowState.ARRIVED_AT_BANK_CHECKPOINT: _handle_awaiting_bank_observation,
    BankingWorkflowState.BANK_CLOSED_VERIFIED: _handle_bank_closed_verified,
    BankingWorkflowState.BANK_OPEN_ATTEMPT_PENDING: _handle_awaiting_bank_observation,
    BankingWorkflowState.BANK_OPEN_VERIFIED: _handle_bank_open_verified,
    BankingWorkflowState.DEPOSIT_READY_VERIFIED: _handle_deposit_ready_verified,
    BankingWorkflowState.DEPOSIT_ATTEMPT_PENDING: _handle_deposit_attempt_pending,
    BankingWorkflowState.BANKING_COMPLETE: _handle_terminal,
}
