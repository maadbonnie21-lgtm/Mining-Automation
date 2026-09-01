"""Deterministic, non-input verified banking workflow.

This module is a pure state machine. It never touches RuneLite, the capture
layer, ``WorldState``, or :class:`~mining_automation.controller.MiningController`
-- it only reduces (state, event) pairs to a new state plus an explicit list
of reasons when nothing advanced. Every public function here is a plain
function over immutable values; there is no hidden mutable session object.

The central invariant, enforced structurally rather than by convention, is:

    an attempted input action never proves its own success.

Concretely:

* :class:`OpenBankAttempted` and :class:`DepositAttempted` carry no evidence
  and cannot, by themselves, move the workflow into a verified state. Only a
  *following* fresh observation event can.
* Arrival at a checkpoint (:class:`CheckpointArrivalEvidence`) can only ever
  produce :attr:`BankingWorkflowState.ARRIVED_AT_BANK_CHECKPOINT`. It is
  structurally incapable of producing a bank-open or inventory result --
  those states are only reachable through :class:`BankObservationEvidence`
  and the inventory evidence events.
* Every non-advancing call returns at least one
  :class:`~mining_automation.banking.contracts.BankingBlocker`, so a caller
  can never mistake "nothing happened" for a silent success.

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

    BANKING_COMPLETE is terminal; start a new context for the next visit.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import Final

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
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    converted = float(value)
    return converted if isfinite(converted) else None


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


@dataclass(frozen=True, slots=True)
class BankObservationEvidence:
    """One or more same-step bank-interface readings to resolve together.

    More than one entry is only meaningful for exercising the
    duplicate/conflicting-observation guard; a well-behaved caller supplies
    exactly one.
    """

    observations: tuple[BankObservation, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.observations, tuple) or any(
            type(item) is not BankObservation for item in self.observations
        ):
            raise ValueError("observations must be a tuple of exact BankObservation values")


@dataclass(frozen=True, slots=True)
class OpenBankAttempted:
    """A marker that an open-bank interaction was attempted.

    Carries no evidence. Cannot advance the workflow past a "pending"
    state on its own.
    """


@dataclass(frozen=True, slots=True)
class PreDepositInventoryObservationEvidence:
    """One or more same-step pre-deposit inventory readings to resolve together."""

    observations: tuple[PreDepositInventoryObservation, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.observations, tuple) or any(
            type(item) is not PreDepositInventoryObservation for item in self.observations
        ):
            raise ValueError(
                "observations must be a tuple of exact PreDepositInventoryObservation values"
            )


@dataclass(frozen=True, slots=True)
class DepositAttempted:
    """A marker that a deposit interaction was attempted.

    Carries no evidence. Cannot advance the workflow past a "pending" state
    on its own.
    """


@dataclass(frozen=True, slots=True)
class PostDepositInventoryObservationEvidence:
    """One or more same-step post-deposit inventory readings to resolve together."""

    observations: tuple[PostDepositInventoryObservation, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.observations, tuple) or any(
            type(item) is not PostDepositInventoryObservation for item in self.observations
        ):
            raise ValueError(
                "observations must be a tuple of exact PostDepositInventoryObservation values"
            )


BankingWorkflowEvent = (
    CheckpointArrivalEvidence
    | BankObservationEvidence
    | OpenBankAttempted
    | PreDepositInventoryObservationEvidence
    | DepositAttempted
    | PostDepositInventoryObservationEvidence
)


@dataclass(frozen=True, slots=True)
class BankingWorkflowContext:
    """Immutable workflow state plus the fixed identity of the current visit.

    ``blockers`` explains why the *previous* call to
    :func:`advance_banking_workflow` did not change ``state`` -- it is always
    empty immediately after a successful advance and after construction via
    :func:`initial_banking_workflow_context`.
    """

    state: BankingWorkflowState
    expected_checkpoint: BankCheckpointIdentity
    expected_profile: BankProfileIdentity
    blockers: tuple[BankingBlocker, ...]
    last_accepted_provenance: BankEvidenceProvenance | None

    def __post_init__(self) -> None:
        if type(self.state) is not BankingWorkflowState:
            raise ValueError("state must be an exact BankingWorkflowState")
        if type(self.expected_checkpoint) is not BankCheckpointIdentity:
            raise ValueError("expected_checkpoint must be an exact BankCheckpointIdentity")
        if type(self.expected_profile) is not BankProfileIdentity:
            raise ValueError("expected_profile must be an exact BankProfileIdentity")
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
        if (
            self.state is not BankingWorkflowState.AWAITING_CHECKPOINT_ARRIVAL
            and self.last_accepted_provenance is None
        ):
            raise ValueError(
                "every state past AWAITING_CHECKPOINT_ARRIVAL requires accepted provenance"
            )

    @property
    def complete(self) -> bool:
        return self.state is BankingWorkflowState.BANKING_COMPLETE


def initial_banking_workflow_context(
    *,
    expected_checkpoint: BankCheckpointIdentity,
    expected_profile: BankProfileIdentity,
) -> BankingWorkflowContext:
    """Return the fresh starting context for one bank visit."""
    return BankingWorkflowContext(
        state=INITIAL_BANKING_WORKFLOW_STATE,
        expected_checkpoint=expected_checkpoint,
        expected_profile=expected_profile,
        blockers=(),
        last_accepted_provenance=None,
    )


def deposit_readiness(context: BankingWorkflowContext) -> DepositReadiness:
    """Whether ``context`` currently permits a deposit attempt.

    ``READY`` only when the workflow has jointly verified, in the current
    visit, that the bank is open and that inventory is known non-empty --
    never from an attempt alone and never from a stale reading.
    """
    if type(context) is not BankingWorkflowContext:
        raise TypeError("context must be an exact BankingWorkflowContext")
    if context.state is BankingWorkflowState.DEPOSIT_READY_VERIFIED:
        return DepositReadiness.READY
    return DepositReadiness.NOT_READY


def _denied(context: BankingWorkflowContext, blockers: tuple[BankingBlocker, ...]) -> BankingWorkflowContext:
    if not blockers:  # pragma: no cover - caller invariant, every call site passes >=1 blocker
        raise ValueError("a denied transition must carry at least one blocker")
    return BankingWorkflowContext(
        state=context.state,
        expected_checkpoint=context.expected_checkpoint,
        expected_profile=context.expected_profile,
        blockers=blockers,
        last_accepted_provenance=context.last_accepted_provenance,
    )


def _advanced(
    context: BankingWorkflowContext,
    new_state: BankingWorkflowState,
    *,
    provenance: BankEvidenceProvenance,
) -> BankingWorkflowContext:
    return BankingWorkflowContext(
        state=new_state,
        expected_checkpoint=context.expected_checkpoint,
        expected_profile=context.expected_profile,
        blockers=(),
        last_accepted_provenance=provenance,
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


def advance_banking_workflow(
    context: BankingWorkflowContext,
    event: BankingWorkflowEvent,
    *,
    evaluated_monotonic_s: object,
) -> BankingWorkflowContext:
    """Reduce one (context, event) pair to the next context.

    Never raises for a domain-level denial -- every fail-closed outcome is
    returned as an unchanged ``state`` plus a populated ``blockers`` tuple.
    A :class:`TypeError`/:class:`ValueError` only signals a genuine caller
    bug (wrong argument types).
    """
    if type(context) is not BankingWorkflowContext:
        raise TypeError("context must be an exact BankingWorkflowContext")

    handler = _STATE_HANDLERS.get(context.state)
    if handler is None:  # pragma: no cover - defensive, all states are mapped
        raise ValueError(f"no handler registered for state {context.state}")
    return handler(context, event, evaluated_monotonic_s)


def _handle_awaiting_arrival(
    context: BankingWorkflowContext,
    event: BankingWorkflowEvent,
    evaluated_monotonic_s: object,
) -> BankingWorkflowContext:
    if not isinstance(event, CheckpointArrivalEvidence):
        return _denied(context, (BankingBlocker.ARRIVAL_EVIDENCE_MISSING,))
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
    if isinstance(event, (OpenBankAttempted, DepositAttempted)):
        # From ARRIVED_AT_BANK_CHECKPOINT this is arrival evidence (or nothing)
        # being used as if it proved a bank state. From BANK_OPEN_ATTEMPT_PENDING
        # it is a second attempt substituting for the fresh observation the
        # first attempt still requires. Both are the same underlying mistake
        # -- treating an attempt as its own proof -- named for their context.
        if context.state is BankingWorkflowState.BANK_OPEN_ATTEMPT_PENDING:
            return _denied(context, (BankingBlocker.OPEN_ATTEMPT_WITHOUT_VERIFICATION,))
        return _denied(context, (BankingBlocker.ARRIVAL_SUBSTITUTED_FOR_OBSERVATION,))
    if not isinstance(event, BankObservationEvidence):
        return _denied(context, (BankingBlocker.UNEXPECTED_EVENT_FOR_STATE,))

    observation, resolve_blocker = _resolve_single(
        event.observations,
        missing_blocker=BankingBlocker.BANK_OBSERVATION_MISSING,
        conflict_blocker=BankingBlocker.DUPLICATE_CONFLICTING_BANK_OBSERVATIONS,
    )
    if resolve_blocker is not None:
        return _denied(context, (resolve_blocker,))
    assert isinstance(observation, BankObservation)  # guaranteed when resolve_blocker is None

    # No current_provenance: this reducer has no independent capture-layer
    # source to check the observation's provenance against, only the
    # observation itself and the previous step's accepted provenance.
    result = evaluate_bank_observation(
        observation,
        expected_checkpoint=context.expected_checkpoint,
        expected_profile=context.expected_profile,
        evaluated_monotonic_s=evaluated_monotonic_s,
        previous_provenance=context.last_accepted_provenance,
    )
    if not result.accepted:
        return _denied(context, result.blockers)
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
    del evaluated_monotonic_s
    if not isinstance(event, OpenBankAttempted):
        return _denied(context, (BankingBlocker.UNEXPECTED_EVENT_FOR_STATE,))
    assert context.last_accepted_provenance is not None  # invariant of this state, see __post_init__
    return _advanced(
        context,
        BankingWorkflowState.BANK_OPEN_ATTEMPT_PENDING,
        provenance=context.last_accepted_provenance,
    )


def _handle_bank_open_verified(
    context: BankingWorkflowContext,
    event: BankingWorkflowEvent,
    evaluated_monotonic_s: object,
) -> BankingWorkflowContext:
    if isinstance(event, DepositAttempted):
        return _denied(context, (BankingBlocker.DEPOSIT_WITHOUT_INVENTORY_VERIFICATION,))
    if not isinstance(event, PreDepositInventoryObservationEvidence):
        return _denied(context, (BankingBlocker.UNEXPECTED_EVENT_FOR_STATE,))

    observation, resolve_blocker = _resolve_single(
        event.observations,
        missing_blocker=BankingBlocker.INVENTORY_EVIDENCE_MISSING,
        conflict_blocker=BankingBlocker.DUPLICATE_CONFLICTING_INVENTORY_OBSERVATIONS,
    )
    if resolve_blocker is not None:
        return _denied(context, (resolve_blocker,))
    assert isinstance(observation, PreDepositInventoryObservation)

    result = evaluate_inventory_observation(
        observation,
        evaluated_monotonic_s=evaluated_monotonic_s,
        previous_provenance=context.last_accepted_provenance,
    )
    if not result.accepted:
        return _denied(context, result.blockers)
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
    del evaluated_monotonic_s
    if not isinstance(event, DepositAttempted):
        return _denied(context, (BankingBlocker.UNEXPECTED_EVENT_FOR_STATE,))
    assert context.last_accepted_provenance is not None  # invariant of this state, see __post_init__
    return _advanced(
        context,
        BankingWorkflowState.DEPOSIT_ATTEMPT_PENDING,
        provenance=context.last_accepted_provenance,
    )


def _handle_deposit_attempt_pending(
    context: BankingWorkflowContext,
    event: BankingWorkflowEvent,
    evaluated_monotonic_s: object,
) -> BankingWorkflowContext:
    if not isinstance(event, PostDepositInventoryObservationEvidence):
        return _denied(context, (BankingBlocker.UNEXPECTED_EVENT_FOR_STATE,))

    observation, resolve_blocker = _resolve_single(
        event.observations,
        missing_blocker=BankingBlocker.INVENTORY_EVIDENCE_MISSING,
        conflict_blocker=BankingBlocker.DUPLICATE_CONFLICTING_INVENTORY_OBSERVATIONS,
    )
    if resolve_blocker is not None:
        return _denied(context, (resolve_blocker,))
    assert isinstance(observation, PostDepositInventoryObservation)

    result = evaluate_inventory_observation(
        observation,
        evaluated_monotonic_s=evaluated_monotonic_s,
        previous_provenance=context.last_accepted_provenance,
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
