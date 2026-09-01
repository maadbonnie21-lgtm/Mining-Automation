"""Deterministic state-machine tests for the non-input banking workflow.

Every test here operates purely on typed values -- no capture backend, no
RuneLite, no ``WorldState``. Fixtures come from
:mod:`mining_automation.banking.testing` and are synthetic by construction;
none of them may be read as real OSRS evidence.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mining_automation.banking.attempts import MAX_ATTEMPT_RECEIPT_AGE_S
from mining_automation.banking.contracts import (
    BankCheckpointIdentity,
    BankingBlocker,
    BankInterfaceState,
)
from mining_automation.banking.perception import (
    MAX_BANKING_EVIDENCE_AGE_S,
    BankDetectorMetadata,
)
from mining_automation.banking.testing import (
    SYNTHETIC_BANK_CHECKPOINT,
    SYNTHETIC_BANK_DETECTOR_METADATA,
    SYNTHETIC_BANK_PROFILE,
    build_bank_observation,
    build_deposit_attempt_receipt,
    build_open_bank_attempt_receipt,
    build_post_deposit_inventory_observation,
    build_pre_deposit_inventory_observation,
    build_provenance,
)
from mining_automation.banking.workflow import (
    BankingWorkflowContext,
    BankingWorkflowState,
    BankObservationEvidence,
    CheckpointArrivalEvidence,
    DepositAttempted,
    DepositReadiness,
    OpenBankAttempted,
    PostDepositInventoryObservationEvidence,
    PreDepositInventoryObservationEvidence,
    advance_banking_workflow,
    deposit_readiness,
    initial_banking_workflow_context,
)


class _OverloadedString(str):
    def __eq__(self, other: object) -> bool:
        return True

    def __ne__(self, other: object) -> bool:
        return False

    __hash__ = str.__hash__


class _TupleSubclass(tuple):
    pass


def _initial() -> BankingWorkflowContext:
    return initial_banking_workflow_context(
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        expected_detector=SYNTHETIC_BANK_DETECTOR_METADATA,
    )


def _arrive(context: BankingWorkflowContext, *, frame_id: int = 1) -> BankingWorkflowContext:
    provenance = build_provenance(frame_id=frame_id, captured_monotonic_s=float(frame_id))
    return advance_banking_workflow(
        context,
        CheckpointArrivalEvidence(identity=SYNTHETIC_BANK_CHECKPOINT, provenance=provenance),
        evaluated_monotonic_s=float(frame_id),
    )


def _observe_bank(
    context: BankingWorkflowContext,
    interface_state: BankInterfaceState,
    *,
    frame_id: int,
) -> BankingWorkflowContext:
    provenance = build_provenance(frame_id=frame_id, captured_monotonic_s=float(frame_id))
    observation = build_bank_observation(interface_state=interface_state, provenance=provenance)
    return advance_banking_workflow(
        context,
        BankObservationEvidence(observations=(observation,)),
        evaluated_monotonic_s=float(frame_id),
    )


def _observe_pre_deposit_inventory(
    context: BankingWorkflowContext,
    occupied_slots: int | None,
    *,
    frame_id: int,
) -> BankingWorkflowContext:
    provenance = build_provenance(frame_id=frame_id, captured_monotonic_s=float(frame_id))
    observation = build_pre_deposit_inventory_observation(
        occupied_slots=occupied_slots, provenance=provenance
    )
    return advance_banking_workflow(
        context,
        PreDepositInventoryObservationEvidence(observations=(observation,)),
        evaluated_monotonic_s=float(frame_id),
    )


def _observe_post_deposit_inventory(
    context: BankingWorkflowContext,
    occupied_slots: int | None,
    *,
    frame_id: int,
) -> BankingWorkflowContext:
    provenance = build_provenance(frame_id=frame_id, captured_monotonic_s=float(frame_id))
    observation = build_post_deposit_inventory_observation(
        occupied_slots=occupied_slots, provenance=provenance
    )
    return advance_banking_workflow(
        context,
        PostDepositInventoryObservationEvidence(observations=(observation,)),
        evaluated_monotonic_s=float(frame_id),
    )


def _to_deposit_ready(context: BankingWorkflowContext) -> BankingWorkflowContext:
    context = _arrive(context, frame_id=1)
    context = _observe_bank(context, BankInterfaceState.OPEN, frame_id=2)
    return _observe_pre_deposit_inventory(context, 28, frame_id=3)


def _record_open_attempt(
    context: BankingWorkflowContext,
    *,
    issued_monotonic_s: float,
    attempt_id: str = "synthetic-open-workflow-attempt",
) -> BankingWorkflowContext:
    assert context.last_accepted_provenance is not None
    receipt = build_open_bank_attempt_receipt(
        attempt_id=attempt_id,
        issued_monotonic_s=issued_monotonic_s,
        preceding_provenance=context.last_accepted_provenance,
    )
    return advance_banking_workflow(
        context,
        OpenBankAttempted(receipt=receipt),
        evaluated_monotonic_s=issued_monotonic_s,
    )


def _record_deposit_attempt(
    context: BankingWorkflowContext,
    *,
    issued_monotonic_s: float,
    attempt_id: str = "synthetic-deposit-workflow-attempt",
) -> BankingWorkflowContext:
    assert context.last_accepted_provenance is not None
    receipt = build_deposit_attempt_receipt(
        attempt_id=attempt_id,
        issued_monotonic_s=issued_monotonic_s,
        preceding_provenance=context.last_accepted_provenance,
    )
    return advance_banking_workflow(
        context,
        DepositAttempted(receipt=receipt),
        evaluated_monotonic_s=issued_monotonic_s,
    )


# ---------------------------------------------------------------------------
# Happy path (Part E)
# ---------------------------------------------------------------------------


def test_happy_path_reaches_banking_complete() -> None:
    context = _initial()
    context = _arrive(context, frame_id=1)
    assert context.state is BankingWorkflowState.ARRIVED_AT_BANK_CHECKPOINT
    assert context.blockers == ()

    context = _observe_bank(context, BankInterfaceState.CLOSED, frame_id=2)
    assert context.state is BankingWorkflowState.BANK_CLOSED_VERIFIED

    context = _record_open_attempt(context, issued_monotonic_s=2.0)
    assert context.state is BankingWorkflowState.BANK_OPEN_ATTEMPT_PENDING
    assert deposit_readiness(context, evaluated_monotonic_s=2.0) is DepositReadiness.NOT_READY

    context = _observe_bank(context, BankInterfaceState.OPEN, frame_id=3)
    assert context.state is BankingWorkflowState.BANK_OPEN_VERIFIED

    context = _observe_pre_deposit_inventory(context, 28, frame_id=4)
    assert context.state is BankingWorkflowState.DEPOSIT_READY_VERIFIED
    assert deposit_readiness(context, evaluated_monotonic_s=4.0) is DepositReadiness.READY

    context = _record_deposit_attempt(context, issued_monotonic_s=4.0)
    assert context.state is BankingWorkflowState.DEPOSIT_ATTEMPT_PENDING
    assert deposit_readiness(context, evaluated_monotonic_s=4.0) is DepositReadiness.NOT_READY

    context = _observe_post_deposit_inventory(context, 0, frame_id=5)
    assert context.state is BankingWorkflowState.BANKING_COMPLETE
    assert context.complete
    assert context.blockers == ()


def test_happy_path_is_deterministic_on_repeated_replay() -> None:
    """The same event sequence produces byte-identical results every time."""

    def run() -> BankingWorkflowContext:
        context = _initial()
        context = _arrive(context, frame_id=1)
        context = _observe_bank(context, BankInterfaceState.OPEN, frame_id=2)
        context = _observe_pre_deposit_inventory(context, 28, frame_id=3)
        context = _record_deposit_attempt(context, issued_monotonic_s=3.0)
        return _observe_post_deposit_inventory(context, 0, frame_id=4)

    first = run()
    second = run()
    assert first == second
    assert first.state is BankingWorkflowState.BANKING_COMPLETE


def test_terminal_state_denies_further_events() -> None:
    context = _initial()
    context = _arrive(context, frame_id=1)
    context = _observe_bank(context, BankInterfaceState.OPEN, frame_id=2)
    context = _observe_pre_deposit_inventory(context, 28, frame_id=3)
    context = _record_deposit_attempt(context, issued_monotonic_s=3.0)
    context = _observe_post_deposit_inventory(context, 0, frame_id=4)
    assert context.complete

    result = advance_banking_workflow(context, OpenBankAttempted(), evaluated_monotonic_s=4.0)
    assert result.state is BankingWorkflowState.BANKING_COMPLETE
    assert result.blockers == (BankingBlocker.UNEXPECTED_EVENT_FOR_STATE,)


# ---------------------------------------------------------------------------
# Failure paths (Part E)
# ---------------------------------------------------------------------------


def test_open_attempt_can_still_be_closed() -> None:
    context = _initial()
    context = _arrive(context, frame_id=1)
    context = _observe_bank(context, BankInterfaceState.CLOSED, frame_id=2)
    context = _record_open_attempt(context, issued_monotonic_s=2.0)
    context = _observe_bank(context, BankInterfaceState.CLOSED, frame_id=3)
    assert context.state is BankingWorkflowState.BANK_CLOSED_VERIFIED
    assert context.blockers == ()


def test_deposit_attempt_can_still_be_full() -> None:
    context = _to_deposit_ready(_initial())
    context = _record_deposit_attempt(context, issued_monotonic_s=3.0)
    result = _observe_post_deposit_inventory(context, 28, frame_id=4)
    assert result.state is BankingWorkflowState.DEPOSIT_ATTEMPT_PENDING
    assert result.blockers == (BankingBlocker.DEPOSIT_INVENTORY_STILL_NON_EMPTY,)


def test_deposit_attempt_can_yield_unknown_inventory() -> None:
    context = _to_deposit_ready(_initial())
    context = _record_deposit_attempt(context, issued_monotonic_s=3.0)
    result = _observe_post_deposit_inventory(context, None, frame_id=4)
    assert result.state is BankingWorkflowState.DEPOSIT_ATTEMPT_PENDING
    assert result.blockers == (BankingBlocker.POST_DEPOSIT_INVENTORY_UNKNOWN,)


# ---------------------------------------------------------------------------
# Adversarial / fail-closed matrix (Part D)
# ---------------------------------------------------------------------------


def test_arrival_evidence_missing() -> None:
    context = _initial()
    result = advance_banking_workflow(context, OpenBankAttempted(), evaluated_monotonic_s=0.0)
    assert result.state is BankingWorkflowState.AWAITING_CHECKPOINT_ARRIVAL
    assert result.blockers == (BankingBlocker.ARRIVAL_EVIDENCE_MISSING,)


def test_arrival_evidence_stale() -> None:
    context = _initial()
    provenance = build_provenance(frame_id=1, captured_monotonic_s=0.0)
    result = advance_banking_workflow(
        context,
        CheckpointArrivalEvidence(identity=SYNTHETIC_BANK_CHECKPOINT, provenance=provenance),
        evaluated_monotonic_s=100.0,
    )
    assert result.state is BankingWorkflowState.AWAITING_CHECKPOINT_ARRIVAL
    assert result.blockers == (BankingBlocker.ARRIVAL_EVIDENCE_STALE,)


def test_arrival_wrong_checkpoint_identity() -> None:
    context = _initial()
    provenance = build_provenance(frame_id=1, captured_monotonic_s=0.0)
    other_identity = BankCheckpointIdentity(checkpoint_id="other", location_id="other")
    result = advance_banking_workflow(
        context,
        CheckpointArrivalEvidence(identity=other_identity, provenance=provenance),
        evaluated_monotonic_s=0.0,
    )
    assert result.blockers == (BankingBlocker.CHECKPOINT_IDENTITY_MISMATCH,)


@pytest.mark.parametrize("mutation", ["identity-type", "nested-string"])
def test_forged_arrival_contract_cannot_reach_arrived_state(mutation: str) -> None:
    identity = BankCheckpointIdentity(
        checkpoint_id=SYNTHETIC_BANK_CHECKPOINT.checkpoint_id,
        location_id=SYNTHETIC_BANK_CHECKPOINT.location_id,
    )
    event = CheckpointArrivalEvidence(
        identity=identity,
        provenance=build_provenance(frame_id=1, captured_monotonic_s=1.0),
    )
    if mutation == "identity-type":
        object.__setattr__(event, "identity", object())
    else:
        object.__setattr__(identity, "checkpoint_id", _OverloadedString(identity.checkpoint_id))

    result = advance_banking_workflow(_initial(), event, evaluated_monotonic_s=1.0)

    assert result.state is BankingWorkflowState.AWAITING_CHECKPOINT_ARRIVAL
    assert result.blockers == (BankingBlocker.ARRIVAL_EVIDENCE_TYPE_INVALID,)


def test_bank_observation_missing() -> None:
    context = _arrive(_initial(), frame_id=1)
    result = advance_banking_workflow(
        context, BankObservationEvidence(observations=()), evaluated_monotonic_s=1.0
    )
    assert result.state is BankingWorkflowState.ARRIVED_AT_BANK_CHECKPOINT
    assert result.blockers == (BankingBlocker.BANK_OBSERVATION_MISSING,)


def test_bank_state_unknown_does_not_advance() -> None:
    context = _arrive(_initial(), frame_id=1)
    result = _observe_bank(context, BankInterfaceState.UNKNOWN, frame_id=2)
    assert result.state is BankingWorkflowState.ARRIVED_AT_BANK_CHECKPOINT
    assert result.blockers == (BankingBlocker.BANK_STATE_UNKNOWN,)


def test_bank_wrong_profile_version() -> None:
    context = _arrive(_initial(), frame_id=1)
    other_profile = replace(SYNTHETIC_BANK_PROFILE, profile_version="9.9.9")
    provenance = build_provenance(frame_id=2, captured_monotonic_s=2.0)
    observation = build_bank_observation(profile=other_profile, provenance=provenance)
    result = advance_banking_workflow(
        context,
        BankObservationEvidence(observations=(observation,)),
        evaluated_monotonic_s=2.0,
    )
    assert result.blockers == (BankingBlocker.BANK_PROFILE_MISMATCH,)


def test_bank_wrong_frame_geometry_resolves_unknown_and_does_not_advance() -> None:
    # The observation still claims the *expected* profile (so the profile
    # check passes) but the frame it was actually captured from is a
    # different size -- e.g. a resized/rescaled client window.
    context = _arrive(_initial(), frame_id=1)
    mismatched_geometry_provenance = build_provenance(
        frame_id=2, captured_monotonic_s=2.0, width=999, height=999
    )
    observation = build_bank_observation(
        profile=SYNTHETIC_BANK_PROFILE, provenance=mismatched_geometry_provenance
    )
    result = advance_banking_workflow(
        context,
        BankObservationEvidence(observations=(observation,)),
        evaluated_monotonic_s=2.0,
    )
    assert result.blockers == (BankingBlocker.BANK_GEOMETRY_UNSUPPORTED,)
    assert result.state is BankingWorkflowState.ARRIVED_AT_BANK_CHECKPOINT


def test_bank_target_found_but_ui_still_closed_after_attempt() -> None:
    context = _arrive(_initial(), frame_id=1)
    context = _observe_bank(context, BankInterfaceState.CLOSED, frame_id=2)
    context = _record_open_attempt(context, issued_monotonic_s=2.0)
    result = _observe_bank(context, BankInterfaceState.CLOSED, frame_id=3)
    assert result.state is BankingWorkflowState.BANK_CLOSED_VERIFIED
    assert result.blockers == ()


@pytest.mark.parametrize(
    ("detector_id", "detector_version", "expected_blocker"),
    (
        (
            "rogue-detector",
            SYNTHETIC_BANK_DETECTOR_METADATA.version,
            BankingBlocker.BANK_DETECTOR_ID_MISMATCH,
        ),
        (
            SYNTHETIC_BANK_DETECTOR_METADATA.detector_id,
            "wrong-version",
            BankingBlocker.BANK_DETECTOR_VERSION_MISMATCH,
        ),
    ),
)
def test_workflow_rejects_wrong_bank_detector_identity(
    detector_id: str,
    detector_version: str,
    expected_blocker: BankingBlocker,
) -> None:
    context = _arrive(_initial(), frame_id=1)
    observation = build_bank_observation(
        interface_state=BankInterfaceState.OPEN,
        provenance=build_provenance(frame_id=2, captured_monotonic_s=2.0),
        detector_id=detector_id,
        detector_version=detector_version,
    )

    result = advance_banking_workflow(
        context,
        BankObservationEvidence(observations=(observation,)),
        evaluated_monotonic_s=2.0,
    )

    assert result.state is BankingWorkflowState.ARRIVED_AT_BANK_CHECKPOINT
    assert result.blockers == (expected_blocker,)
    assert result.expected_detector is SYNTHETIC_BANK_DETECTOR_METADATA


def test_open_attempt_without_fresh_verification() -> None:
    context = _arrive(_initial(), frame_id=1)
    context = _observe_bank(context, BankInterfaceState.CLOSED, frame_id=2)
    context = _record_open_attempt(context, issued_monotonic_s=2.0)
    result = advance_banking_workflow(context, OpenBankAttempted(), evaluated_monotonic_s=2.0)
    assert result.state is BankingWorkflowState.BANK_OPEN_ATTEMPT_PENDING
    assert result.blockers == (BankingBlocker.OPEN_ATTEMPT_WITHOUT_VERIFICATION,)


def test_deposit_requested_with_inventory_unknown() -> None:
    context = _arrive(_initial(), frame_id=1)
    context = _observe_bank(context, BankInterfaceState.OPEN, frame_id=2)
    result = _observe_pre_deposit_inventory(context, None, frame_id=3)
    assert result.state is BankingWorkflowState.BANK_OPEN_VERIFIED
    assert BankingBlocker.INVENTORY_UNKNOWN in result.blockers


def test_deposit_requested_with_stale_inventory() -> None:
    context = _arrive(_initial(), frame_id=1)
    context = _observe_bank(context, BankInterfaceState.OPEN, frame_id=2)
    provenance = build_provenance(frame_id=3, captured_monotonic_s=2.0)
    observation = build_pre_deposit_inventory_observation(occupied_slots=28, provenance=provenance)
    result = advance_banking_workflow(
        context,
        PreDepositInventoryObservationEvidence(observations=(observation,)),
        evaluated_monotonic_s=100.0,
    )
    assert result.blockers == (BankingBlocker.INVENTORY_EVIDENCE_STALE,)


def test_old_bank_open_support_cannot_be_carried_into_fresh_inventory() -> None:
    context = _arrive(_initial(), frame_id=1)
    context = _observe_bank(context, BankInterfaceState.OPEN, frame_id=2)
    observation = build_pre_deposit_inventory_observation(
        occupied_slots=28,
        provenance=build_provenance(frame_id=3, captured_monotonic_s=100.0),
    )

    result = advance_banking_workflow(
        context,
        PreDepositInventoryObservationEvidence(observations=(observation,)),
        evaluated_monotonic_s=100.0,
    )

    assert result.state is BankingWorkflowState.BANK_OPEN_VERIFIED
    assert result.blockers == (BankingBlocker.SUPPORTING_EVIDENCE_STALE,)


def test_old_arrival_support_cannot_be_carried_into_fresh_bank_observation() -> None:
    context = _arrive(_initial(), frame_id=1)
    observation = build_bank_observation(
        interface_state=BankInterfaceState.OPEN,
        provenance=build_provenance(frame_id=2, captured_monotonic_s=100.0),
    )

    result = advance_banking_workflow(
        context,
        BankObservationEvidence(observations=(observation,)),
        evaluated_monotonic_s=100.0,
    )

    assert result.state is BankingWorkflowState.ARRIVED_AT_BANK_CHECKPOINT
    assert result.blockers == (BankingBlocker.SUPPORTING_EVIDENCE_STALE,)


def test_deposit_attempted_inventory_remains_non_empty() -> None:
    context = _to_deposit_ready(_initial())
    context = _record_deposit_attempt(context, issued_monotonic_s=3.0)
    result = _observe_post_deposit_inventory(context, 15, frame_id=4)
    assert result.blockers == (BankingBlocker.DEPOSIT_INVENTORY_STILL_NON_EMPTY,)


def test_deposit_attempted_resulting_inventory_unknown() -> None:
    context = _to_deposit_ready(_initial())
    context = _record_deposit_attempt(context, issued_monotonic_s=3.0)
    result = _observe_post_deposit_inventory(context, None, frame_id=4)
    assert result.blockers == (BankingBlocker.POST_DEPOSIT_INVENTORY_UNKNOWN,)


def test_duplicate_conflicting_bank_observations() -> None:
    context = _arrive(_initial(), frame_id=1)
    provenance = build_provenance(frame_id=2, captured_monotonic_s=2.0)
    open_reading = build_bank_observation(
        interface_state=BankInterfaceState.OPEN, provenance=provenance
    )
    closed_reading = build_bank_observation(
        interface_state=BankInterfaceState.CLOSED, provenance=provenance
    )
    result = advance_banking_workflow(
        context,
        BankObservationEvidence(observations=(open_reading, closed_reading)),
        evaluated_monotonic_s=2.0,
    )
    assert result.state is BankingWorkflowState.ARRIVED_AT_BANK_CHECKPOINT
    assert result.blockers == (BankingBlocker.DUPLICATE_CONFLICTING_BANK_OBSERVATIONS,)


def test_duplicate_agreeing_bank_observations_are_collapsed() -> None:
    context = _arrive(_initial(), frame_id=1)
    provenance = build_provenance(frame_id=2, captured_monotonic_s=2.0)
    reading = build_bank_observation(interface_state=BankInterfaceState.OPEN, provenance=provenance)
    result = advance_banking_workflow(
        context,
        BankObservationEvidence(observations=(reading, reading)),
        evaluated_monotonic_s=2.0,
    )
    assert result.state is BankingWorkflowState.BANK_OPEN_VERIFIED


def test_malformed_second_bank_observation_cannot_hide_in_agreeing_duplicate() -> None:
    context = _arrive(_initial(), frame_id=1)
    provenance = build_provenance(frame_id=2, captured_monotonic_s=2.0)
    first = build_bank_observation(interface_state=BankInterfaceState.OPEN, provenance=provenance)
    second = build_bank_observation(interface_state=BankInterfaceState.OPEN, provenance=provenance)
    event = BankObservationEvidence(observations=(first, second))
    object.__setattr__(second, "detector_id", _OverloadedString(second.detector_id))

    result = advance_banking_workflow(context, event, evaluated_monotonic_s=2.0)

    assert result.state is BankingWorkflowState.ARRIVED_AT_BANK_CHECKPOINT
    assert result.blockers == (BankingBlocker.BANK_EVIDENCE_TYPE_INVALID,)


def test_mutated_observation_tuple_subclass_is_rejected_before_resolution() -> None:
    context = _arrive(_initial(), frame_id=1)
    observation = build_bank_observation(
        interface_state=BankInterfaceState.OPEN,
        provenance=build_provenance(frame_id=2, captured_monotonic_s=2.0),
    )
    event = BankObservationEvidence(observations=(observation,))
    object.__setattr__(event, "observations", _TupleSubclass((observation,)))

    result = advance_banking_workflow(context, event, evaluated_monotonic_s=2.0)

    assert result.state is BankingWorkflowState.ARRIVED_AT_BANK_CHECKPOINT
    assert result.blockers == (BankingBlocker.BANK_EVIDENCE_TYPE_INVALID,)


def test_duplicate_conflicting_inventory_observations() -> None:
    context = _arrive(_initial(), frame_id=1)
    context = _observe_bank(context, BankInterfaceState.OPEN, frame_id=2)
    provenance = build_provenance(frame_id=3, captured_monotonic_s=3.0)
    full = build_pre_deposit_inventory_observation(occupied_slots=28, provenance=provenance)
    empty = build_pre_deposit_inventory_observation(occupied_slots=0, provenance=provenance)
    result = advance_banking_workflow(
        context,
        PreDepositInventoryObservationEvidence(observations=(full, empty)),
        evaluated_monotonic_s=3.0,
    )
    assert result.state is BankingWorkflowState.BANK_OPEN_VERIFIED
    assert result.blockers == (BankingBlocker.DUPLICATE_CONFLICTING_INVENTORY_OBSERVATIONS,)


def test_malformed_second_inventory_observation_cannot_hide_in_agreeing_duplicate() -> None:
    context = _to_deposit_ready(_initial())
    context = _record_deposit_attempt(context, issued_monotonic_s=3.0)
    retained_receipt = context.pending_attempt_receipt
    provenance = build_provenance(frame_id=4, captured_monotonic_s=4.0)
    first = build_post_deposit_inventory_observation(occupied_slots=0, provenance=provenance)
    second = build_post_deposit_inventory_observation(occupied_slots=0, provenance=provenance)
    event = PostDepositInventoryObservationEvidence(observations=(first, second))
    object.__setattr__(second, "detector_id", _OverloadedString(second.detector_id))

    result = advance_banking_workflow(context, event, evaluated_monotonic_s=4.0)

    assert result.state is BankingWorkflowState.DEPOSIT_ATTEMPT_PENDING
    assert result.blockers == (BankingBlocker.INVENTORY_EVIDENCE_TYPE_INVALID,)
    assert result.pending_attempt_receipt is retained_receipt


def test_unsupported_geometry_never_advances_to_open_or_closed() -> None:
    context = _arrive(_initial(), frame_id=1)
    unsupported_geometry_provenance = build_provenance(
        frame_id=2, captured_monotonic_s=2.0, width=1, height=1
    )
    observation = build_bank_observation(
        profile=SYNTHETIC_BANK_PROFILE,
        provenance=unsupported_geometry_provenance,
        interface_state=BankInterfaceState.OPEN,
    )
    result = advance_banking_workflow(
        context,
        BankObservationEvidence(observations=(observation,)),
        evaluated_monotonic_s=2.0,
    )
    assert result.state is BankingWorkflowState.ARRIVED_AT_BANK_CHECKPOINT
    assert result.blockers == (BankingBlocker.BANK_GEOMETRY_UNSUPPORTED,)


def test_evidence_ordering_regression() -> None:
    context = _arrive(_initial(), frame_id=5)
    result = _observe_bank(context, BankInterfaceState.OPEN, frame_id=5)
    assert result.blockers == (BankingBlocker.EVIDENCE_ORDERING_REGRESSION,)


def test_higher_frame_id_with_regressing_capture_time_is_rejected() -> None:
    context = _arrive(_initial(), frame_id=5)
    observation = build_bank_observation(
        provenance=build_provenance(frame_id=6, captured_monotonic_s=4.0)
    )
    result = advance_banking_workflow(
        context,
        BankObservationEvidence(observations=(observation,)),
        evaluated_monotonic_s=5.0,
    )
    assert result.state is BankingWorkflowState.ARRIVED_AT_BANK_CHECKPOINT
    assert result.blockers == (BankingBlocker.EVIDENCE_ORDERING_REGRESSION,)


def test_route_arrival_assumption_cannot_substitute_for_bank_observation() -> None:
    context = _arrive(_initial(), frame_id=1)
    result = advance_banking_workflow(context, OpenBankAttempted(), evaluated_monotonic_s=1.0)
    assert result.state is BankingWorkflowState.ARRIVED_AT_BANK_CHECKPOINT
    assert result.blockers == (BankingBlocker.ARRIVAL_SUBSTITUTED_FOR_OBSERVATION,)


def test_stale_pre_deposit_inventory_cannot_be_reused_after_attempt() -> None:
    context = _initial()
    context = _arrive(context, frame_id=1)
    context = _observe_bank(context, BankInterfaceState.OPEN, frame_id=2)
    pre_deposit_provenance = build_provenance(frame_id=3, captured_monotonic_s=3.0)
    pre_deposit = build_pre_deposit_inventory_observation(
        occupied_slots=28, provenance=pre_deposit_provenance
    )
    context = advance_banking_workflow(
        context,
        PreDepositInventoryObservationEvidence(observations=(pre_deposit,)),
        evaluated_monotonic_s=3.0,
    )
    assert context.state is BankingWorkflowState.DEPOSIT_READY_VERIFIED
    context = _record_deposit_attempt(context, issued_monotonic_s=3.0)

    # Reusing the exact same (now stale) pre-deposit provenance as if it were
    # a fresh post-deposit reading must be rejected, not accepted.
    reused = build_post_deposit_inventory_observation(
        occupied_slots=0, provenance=pre_deposit_provenance
    )
    result = advance_banking_workflow(
        context,
        PostDepositInventoryObservationEvidence(observations=(reused,)),
        evaluated_monotonic_s=3.0,
    )
    assert result.state is BankingWorkflowState.DEPOSIT_ATTEMPT_PENDING
    assert result.blockers == (BankingBlocker.POST_ATTEMPT_EVIDENCE_NOT_FRESH,)


def test_deposit_without_inventory_verification() -> None:
    context = _arrive(_initial(), frame_id=1)
    context = _observe_bank(context, BankInterfaceState.OPEN, frame_id=2)
    result = advance_banking_workflow(context, DepositAttempted(), evaluated_monotonic_s=2.0)
    assert result.state is BankingWorkflowState.BANK_OPEN_VERIFIED
    assert result.blockers == (BankingBlocker.DEPOSIT_WITHOUT_INVENTORY_VERIFICATION,)


def test_unexpected_event_at_bank_open_verified() -> None:
    context = _arrive(_initial(), frame_id=1)
    context = _observe_bank(context, BankInterfaceState.OPEN, frame_id=2)
    result = advance_banking_workflow(context, OpenBankAttempted(), evaluated_monotonic_s=2.0)
    assert result.state is BankingWorkflowState.BANK_OPEN_VERIFIED
    assert result.blockers == (BankingBlocker.UNEXPECTED_EVENT_FOR_STATE,)


def test_inventory_already_empty_at_bank_does_not_grant_deposit_readiness() -> None:
    context = _arrive(_initial(), frame_id=1)
    context = _observe_bank(context, BankInterfaceState.OPEN, frame_id=2)
    result = _observe_pre_deposit_inventory(context, 0, frame_id=3)
    assert result.state is BankingWorkflowState.BANK_OPEN_VERIFIED
    assert result.blockers == (BankingBlocker.INVENTORY_ALREADY_EMPTY,)


def test_mixed_frame_inventory_evidence_rejected_by_seam_used_from_workflow() -> None:
    """Exercises the perception-seam provenance guard through a workflow-shaped call.

    The workflow itself has no independent provenance source (see
    ``banking.perception`` docstrings), so this drives
    :func:`mining_automation.banking.perception.evaluate_inventory_observation`
    directly with a caller-supplied ``current_provenance`` that disagrees with
    the observation's own -- exactly the shape a future orchestrator with a
    real capture-layer source would use.
    """
    from mining_automation.banking.perception import evaluate_inventory_observation

    claimed_current = build_provenance(
        frame_id=3, captured_monotonic_s=3.0, cycle_id="bank-open-cycle"
    )
    inventory_from_elsewhere = build_provenance(
        frame_id=3, captured_monotonic_s=3.0, cycle_id="different-cycle"
    )
    result = evaluate_inventory_observation(
        build_pre_deposit_inventory_observation(
            occupied_slots=28, provenance=inventory_from_elsewhere
        ),
        evaluated_monotonic_s=3.0,
        current_provenance=claimed_current,
    )
    assert result.blockers == (BankingBlocker.EVIDENCE_PROVENANCE_MISMATCH,)


def test_pre_deposit_inventory_from_another_cycle_cannot_grant_readiness() -> None:
    context = _arrive(_initial(), frame_id=1)
    context = _observe_bank(context, BankInterfaceState.OPEN, frame_id=2)
    observation = build_pre_deposit_inventory_observation(
        occupied_slots=28,
        provenance=build_provenance(
            frame_id=3,
            captured_monotonic_s=3.0,
            cycle_id="foreign-cycle",
        ),
    )
    result = advance_banking_workflow(
        context,
        PreDepositInventoryObservationEvidence(observations=(observation,)),
        evaluated_monotonic_s=3.0,
    )
    assert result.state is BankingWorkflowState.BANK_OPEN_VERIFIED
    assert result.blockers == (BankingBlocker.EVIDENCE_PROVENANCE_MISMATCH,)


# ---------------------------------------------------------------------------
# Context construction invariants
# ---------------------------------------------------------------------------


def test_context_cannot_be_constructed_outside_the_reducer() -> None:
    with pytest.raises(TypeError, match="reducer-issued"):
        BankingWorkflowContext(
            state=BankingWorkflowState.AWAITING_CHECKPOINT_ARRIVAL,
            expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
            expected_profile=SYNTHETIC_BANK_PROFILE,
            expected_detector=SYNTHETIC_BANK_DETECTOR_METADATA,
            blockers=(),
            last_accepted_provenance=None,
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"pending_attempt_receipt": None},
        {"state": BankingWorkflowState.DEPOSIT_ATTEMPT_PENDING},
        {"state": BankingWorkflowState.BANK_CLOSED_VERIFIED},
        {"expected_detector": BankDetectorMetadata("rogue", "9")},
        {"used_attempt_receipt_ids": frozenset()},
    ],
)
def test_context_replace_cannot_rewrite_reducer_issued_history(
    changes: dict[str, object],
) -> None:
    context = _arrive(_initial(), frame_id=1)
    context = _observe_bank(context, BankInterfaceState.CLOSED, frame_id=2)
    pending = _record_open_attempt(context, issued_monotonic_s=2.0)

    with pytest.raises(TypeError, match="reducer-issued"):
        replace(pending, **changes)


def test_context_snapshot_rejects_post_construction_mutation() -> None:
    context = _initial()
    object.__setattr__(context, "expected_detector", BankDetectorMetadata("rogue", "9"))

    with pytest.raises(ValueError, match="differs from its reducer-issued snapshot"):
        deposit_readiness(context, evaluated_monotonic_s=0.0)
    with pytest.raises(ValueError, match="differs from its reducer-issued snapshot"):
        advance_banking_workflow(
            context,
            DepositAttempted(),
            evaluated_monotonic_s=0.0,
        )
    with pytest.raises(ValueError, match="differs from its reducer-issued snapshot"):
        _ = context.complete


def test_context_snapshot_flattens_nested_checkpoint_identity() -> None:
    checkpoint = BankCheckpointIdentity("original", "location")
    context = initial_banking_workflow_context(
        expected_checkpoint=checkpoint,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        expected_detector=SYNTHETIC_BANK_DETECTOR_METADATA,
    )
    object.__setattr__(checkpoint, "checkpoint_id", "tampered")
    arrival = CheckpointArrivalEvidence(
        identity=BankCheckpointIdentity("tampered", "location"),
        provenance=build_provenance(frame_id=1, captured_monotonic_s=1.0),
    )
    with pytest.raises(ValueError, match="differs from its reducer-issued snapshot"):
        advance_banking_workflow(context, arrival, evaluated_monotonic_s=1.0)


def test_context_snapshot_flattens_retained_attempt_receipt_boundary() -> None:
    context = _arrive(_initial(), frame_id=1)
    context = _observe_bank(context, BankInterfaceState.CLOSED, frame_id=2)
    context = _record_open_attempt(context, issued_monotonic_s=3.0)
    receipt = context.pending_attempt_receipt
    assert receipt is not None
    object.__setattr__(receipt, "issued_monotonic_s", 2.1)
    observation = build_bank_observation(
        provenance=build_provenance(frame_id=3, captured_monotonic_s=2.5),
    )
    with pytest.raises(ValueError, match="differs from its reducer-issued snapshot"):
        advance_banking_workflow(
            context,
            BankObservationEvidence(observations=(observation,)),
            evaluated_monotonic_s=2.5,
        )


def test_deposit_readiness_rejects_non_context() -> None:
    with pytest.raises(TypeError, match="must be an exact BankingWorkflowContext"):
        deposit_readiness("not-a-context", evaluated_monotonic_s=0.0)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("evaluated_monotonic_s", "expected"),
    [
        (3.0, DepositReadiness.READY),
        (3.0 + MAX_BANKING_EVIDENCE_AGE_S, DepositReadiness.READY),
        (3.0 + MAX_BANKING_EVIDENCE_AGE_S + 0.001, DepositReadiness.NOT_READY),
        (2.999, DepositReadiness.NOT_READY),
        (float("nan"), DepositReadiness.NOT_READY),
    ],
)
def test_deposit_readiness_is_a_time_bounded_snapshot(
    evaluated_monotonic_s: float,
    expected: DepositReadiness,
) -> None:
    context = _to_deposit_ready(_initial())
    assert deposit_readiness(context, evaluated_monotonic_s=evaluated_monotonic_s) is expected


def test_denied_attempt_cannot_leave_ready_authority() -> None:
    context = _to_deposit_ready(_initial())
    denied = advance_banking_workflow(
        context,
        DepositAttempted(),
        evaluated_monotonic_s=3.0,
    )
    assert denied.state is BankingWorkflowState.DEPOSIT_READY_VERIFIED
    assert denied.blockers == (BankingBlocker.ATTEMPT_RECEIPT_MISSING,)
    assert deposit_readiness(denied, evaluated_monotonic_s=3.0) is DepositReadiness.NOT_READY


def test_advance_banking_workflow_rejects_non_context() -> None:
    with pytest.raises(TypeError, match="must be an exact BankingWorkflowContext"):
        advance_banking_workflow(
            "not-a-context",  # type: ignore[arg-type]
            OpenBankAttempted(),
            evaluated_monotonic_s=0.0,
        )


@pytest.mark.parametrize(
    ("kwargs_override", "message"),
    [
        ({"identity": "not-an-identity"}, "identity must be an exact BankCheckpointIdentity"),
        ({"provenance": "not-a-provenance"}, "provenance must be an exact BankEvidenceProvenance"),
    ],
)
def test_checkpoint_arrival_evidence_rejects_wrong_field_types(
    kwargs_override: dict[str, object], message: str
) -> None:
    kwargs: dict[str, object] = {
        "identity": SYNTHETIC_BANK_CHECKPOINT,
        "provenance": build_provenance(),
    }
    kwargs.update(kwargs_override)
    with pytest.raises(ValueError, match=message):
        CheckpointArrivalEvidence(**kwargs)  # type: ignore[arg-type]


def test_bank_observation_evidence_rejects_wrong_element_type() -> None:
    with pytest.raises(ValueError, match="tuple of exact BankObservation values"):
        BankObservationEvidence(observations=("not-an-observation",))  # type: ignore[arg-type]


def test_pre_deposit_inventory_observation_evidence_rejects_wrong_element_type() -> None:
    with pytest.raises(ValueError, match="tuple of exact PreDepositInventoryObservation values"):
        PreDepositInventoryObservationEvidence(observations=("nope",))  # type: ignore[arg-type]


def test_post_deposit_inventory_observation_evidence_rejects_wrong_element_type() -> None:
    with pytest.raises(ValueError, match="tuple of exact PostDepositInventoryObservation values"):
        PostDepositInventoryObservationEvidence(observations=("nope",))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        (
            "expected_checkpoint",
            "not-an-identity",
            "expected_checkpoint must be an exact BankCheckpointIdentity",
        ),
        (
            "expected_profile",
            "not-a-profile",
            "expected_profile must be an exact BankProfileIdentity",
        ),
        (
            "expected_detector",
            "not-a-detector",
            "expected_detector must be an exact BankDetectorMetadata",
        ),
    ],
)
def test_initial_context_factory_rejects_wrong_identity_types(
    field_name: str, value: object, message: str
) -> None:
    kwargs: dict[str, object] = {
        "expected_checkpoint": SYNTHETIC_BANK_CHECKPOINT,
        "expected_profile": SYNTHETIC_BANK_PROFILE,
        "expected_detector": SYNTHETIC_BANK_DETECTOR_METADATA,
    }
    kwargs[field_name] = value
    with pytest.raises(ValueError, match=message):
        initial_banking_workflow_context(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("evaluated_monotonic_s", [float("nan"), True, "not-a-number"])
def test_arrival_evaluation_time_invalid(evaluated_monotonic_s: object) -> None:
    provenance = build_provenance(frame_id=1, captured_monotonic_s=0.0)
    result = advance_banking_workflow(
        _initial(),
        CheckpointArrivalEvidence(identity=SYNTHETIC_BANK_CHECKPOINT, provenance=provenance),
        evaluated_monotonic_s=evaluated_monotonic_s,
    )
    assert result.blockers == (BankingBlocker.EVALUATION_TIME_INVALID,)


def test_arrival_evidence_from_the_future() -> None:
    provenance = build_provenance(frame_id=1, captured_monotonic_s=10.0)
    result = advance_banking_workflow(
        _initial(),
        CheckpointArrivalEvidence(identity=SYNTHETIC_BANK_CHECKPOINT, provenance=provenance),
        evaluated_monotonic_s=0.0,
    )
    assert result.blockers == (BankingBlocker.EVIDENCE_FROM_FUTURE,)


def test_unexpected_event_at_arrived_at_bank_checkpoint() -> None:
    context = _arrive(_initial(), frame_id=1)
    observation = build_pre_deposit_inventory_observation(occupied_slots=28)
    result = advance_banking_workflow(
        context,
        PreDepositInventoryObservationEvidence(observations=(observation,)),
        evaluated_monotonic_s=1.0,
    )
    assert result.blockers == (BankingBlocker.UNEXPECTED_EVENT_FOR_STATE,)


def test_unexpected_event_at_bank_closed_verified() -> None:
    context = _arrive(_initial(), frame_id=1)
    context = _observe_bank(context, BankInterfaceState.CLOSED, frame_id=2)
    result = advance_banking_workflow(context, DepositAttempted(), evaluated_monotonic_s=2.0)
    assert result.state is BankingWorkflowState.BANK_CLOSED_VERIFIED
    assert result.blockers == (BankingBlocker.UNEXPECTED_EVENT_FOR_STATE,)


def test_unexpected_event_at_deposit_ready_verified() -> None:
    context = _to_deposit_ready(_initial())
    result = advance_banking_workflow(context, OpenBankAttempted(), evaluated_monotonic_s=3.0)
    assert result.state is BankingWorkflowState.DEPOSIT_READY_VERIFIED
    assert result.blockers == (BankingBlocker.UNEXPECTED_EVENT_FOR_STATE,)


def test_unexpected_event_at_deposit_attempt_pending() -> None:
    context = _to_deposit_ready(_initial())
    context = _record_deposit_attempt(context, issued_monotonic_s=3.0)
    result = advance_banking_workflow(context, DepositAttempted(), evaluated_monotonic_s=3.0)
    assert result.state is BankingWorkflowState.DEPOSIT_ATTEMPT_PENDING
    assert result.blockers == (BankingBlocker.UNEXPECTED_EVENT_FOR_STATE,)


def test_post_deposit_observation_missing() -> None:
    context = _to_deposit_ready(_initial())
    context = _record_deposit_attempt(context, issued_monotonic_s=3.0)
    result = advance_banking_workflow(
        context,
        PostDepositInventoryObservationEvidence(observations=()),
        evaluated_monotonic_s=3.0,
    )
    assert result.blockers == (BankingBlocker.INVENTORY_EVIDENCE_MISSING,)


# ---------------------------------------------------------------------------
# Attempt-receipt causality integration (Part D3)
# ---------------------------------------------------------------------------


def test_open_bank_attempt_with_valid_receipt_advances_and_records_receipt() -> None:
    context = _arrive(_initial(), frame_id=1)
    context = _observe_bank(context, BankInterfaceState.CLOSED, frame_id=2)
    receipt = build_open_bank_attempt_receipt(
        attempt_id="open-1",
        issued_monotonic_s=2.0,
        preceding_provenance=context.last_accepted_provenance,
    )
    result = advance_banking_workflow(
        context, OpenBankAttempted(receipt=receipt), evaluated_monotonic_s=2.0
    )
    assert result.state is BankingWorkflowState.BANK_OPEN_ATTEMPT_PENDING
    assert result.blockers == ()
    assert "open-1" in result.used_attempt_receipt_ids
    assert result.pending_attempt_receipt is receipt


def test_post_construction_mutated_receipt_is_a_domain_denial() -> None:
    context = _arrive(_initial(), frame_id=1)
    context = _observe_bank(context, BankInterfaceState.CLOSED, frame_id=2)
    receipt = build_open_bank_attempt_receipt(
        attempt_id="mutated-open-receipt",
        issued_monotonic_s=2.0,
        preceding_provenance=context.last_accepted_provenance,
    )
    event = OpenBankAttempted(receipt=receipt)
    object.__setattr__(receipt, "issued_monotonic_s", float("nan"))

    result = advance_banking_workflow(context, event, evaluated_monotonic_s=2.0)

    assert result.state is BankingWorkflowState.BANK_CLOSED_VERIFIED
    assert result.blockers == (BankingBlocker.ATTEMPT_RECEIPT_TYPE_INVALID,)


@pytest.mark.parametrize("captured_monotonic_s", (2.5, 3.0))
def test_open_bank_result_must_be_strictly_post_attempt_receipt(
    captured_monotonic_s: float,
) -> None:
    context = _arrive(_initial(), frame_id=1)
    context = _observe_bank(context, BankInterfaceState.CLOSED, frame_id=2)
    context = _record_open_attempt(context, issued_monotonic_s=3.0)
    retained_receipt = context.pending_attempt_receipt
    observation = build_bank_observation(
        interface_state=BankInterfaceState.OPEN,
        provenance=build_provenance(
            frame_id=3,
            captured_monotonic_s=captured_monotonic_s,
        ),
    )

    result = advance_banking_workflow(
        context,
        BankObservationEvidence(observations=(observation,)),
        evaluated_monotonic_s=3.0,
    )

    assert result.state is BankingWorkflowState.BANK_OPEN_ATTEMPT_PENDING
    assert result.blockers == (BankingBlocker.POST_ATTEMPT_EVIDENCE_NOT_FRESH,)
    assert result.pending_attempt_receipt is retained_receipt


def test_strictly_post_attempt_open_observation_may_advance() -> None:
    context = _arrive(_initial(), frame_id=1)
    context = _observe_bank(context, BankInterfaceState.CLOSED, frame_id=2)
    context = _record_open_attempt(context, issued_monotonic_s=3.0)
    observation = build_bank_observation(
        interface_state=BankInterfaceState.OPEN,
        provenance=build_provenance(frame_id=3, captured_monotonic_s=3.01),
    )

    result = advance_banking_workflow(
        context,
        BankObservationEvidence(observations=(observation,)),
        evaluated_monotonic_s=3.01,
    )

    assert result.state is BankingWorkflowState.BANK_OPEN_VERIFIED
    assert result.blockers == ()
    assert result.pending_attempt_receipt is None


@pytest.mark.parametrize(
    ("elapsed_s", "expected_state", "expected_blockers"),
    [
        (MAX_ATTEMPT_RECEIPT_AGE_S, BankingWorkflowState.BANK_OPEN_VERIFIED, ()),
        (
            MAX_ATTEMPT_RECEIPT_AGE_S + 0.001,
            BankingWorkflowState.BANK_OPEN_ATTEMPT_PENDING,
            (BankingBlocker.POST_ATTEMPT_EVIDENCE_STALE,),
        ),
    ],
)
def test_open_result_has_bounded_post_attempt_window(
    elapsed_s: float,
    expected_state: BankingWorkflowState,
    expected_blockers: tuple[BankingBlocker, ...],
) -> None:
    context = _arrive(_initial(), frame_id=1)
    context = _observe_bank(context, BankInterfaceState.CLOSED, frame_id=2)
    context = _record_open_attempt(context, issued_monotonic_s=3.0)
    retained_receipt = context.pending_attempt_receipt
    captured = 3.0 + elapsed_s
    observation = build_bank_observation(
        interface_state=BankInterfaceState.OPEN,
        provenance=build_provenance(frame_id=3, captured_monotonic_s=captured),
    )

    result = advance_banking_workflow(
        context,
        BankObservationEvidence(observations=(observation,)),
        evaluated_monotonic_s=captured,
    )

    assert result.state is expected_state
    assert result.blockers == expected_blockers
    assert result.pending_attempt_receipt is (None if not expected_blockers else retained_receipt)


def test_malformed_open_result_is_denied_before_receipt_boundary_dereference() -> None:
    context = _arrive(_initial(), frame_id=1)
    context = _observe_bank(context, BankInterfaceState.CLOSED, frame_id=2)
    context = _record_open_attempt(context, issued_monotonic_s=2.0)
    observation = build_bank_observation(
        interface_state=BankInterfaceState.OPEN,
        provenance=build_provenance(frame_id=3, captured_monotonic_s=3.0),
    )
    event = BankObservationEvidence(observations=(observation,))
    object.__setattr__(observation, "provenance", object())

    result = advance_banking_workflow(context, event, evaluated_monotonic_s=3.0)

    assert result.state is BankingWorkflowState.BANK_OPEN_ATTEMPT_PENDING
    assert result.blockers == (BankingBlocker.BANK_EVIDENCE_TYPE_INVALID,)


def test_workflow_rejects_forged_overloaded_detector_identity() -> None:
    context = _arrive(_initial(), frame_id=1)
    observation = build_bank_observation(
        provenance=build_provenance(frame_id=2, captured_monotonic_s=2.0)
    )
    event = BankObservationEvidence(observations=(observation,))
    object.__setattr__(observation, "detector_id", _OverloadedString("rogue"))
    object.__setattr__(observation, "detector_version", _OverloadedString("rogue"))
    result = advance_banking_workflow(
        context,
        event,
        evaluated_monotonic_s=2.0,
    )
    assert result.state is BankingWorkflowState.ARRIVED_AT_BANK_CHECKPOINT
    assert result.blockers == (BankingBlocker.BANK_EVIDENCE_TYPE_INVALID,)


def test_open_attempt_rejects_stale_preceding_evidence() -> None:
    context = _arrive(_initial(), frame_id=1)
    context = _observe_bank(context, BankInterfaceState.CLOSED, frame_id=2)
    issued = 2.0 + MAX_ATTEMPT_RECEIPT_AGE_S + 0.001
    receipt = build_open_bank_attempt_receipt(
        issued_monotonic_s=issued,
        preceding_provenance=context.last_accepted_provenance,
    )
    result = advance_banking_workflow(
        context,
        OpenBankAttempted(receipt=receipt),
        evaluated_monotonic_s=issued,
    )
    assert result.state is BankingWorkflowState.BANK_CLOSED_VERIFIED
    assert result.blockers == (BankingBlocker.ATTEMPT_PRECEDING_EVIDENCE_STALE,)


def test_open_bank_attempt_receipt_wrong_provenance_denied() -> None:
    context = _arrive(_initial(), frame_id=1)
    context = _observe_bank(context, BankInterfaceState.CLOSED, frame_id=2)
    receipt = build_open_bank_attempt_receipt(
        attempt_id="open-1",
        issued_monotonic_s=2.0,
        preceding_provenance=build_provenance(
            frame_id=2, captured_monotonic_s=2.0, cycle_id="wrong-cycle"
        ),
    )
    result = advance_banking_workflow(
        context, OpenBankAttempted(receipt=receipt), evaluated_monotonic_s=2.0
    )
    assert result.state is BankingWorkflowState.BANK_CLOSED_VERIFIED
    assert result.blockers == (BankingBlocker.ATTEMPT_RECEIPT_WRONG_PROVENANCE,)


def test_open_bank_attempt_stale_receipt_denied() -> None:
    context = _arrive(_initial(), frame_id=1)
    context = _observe_bank(context, BankInterfaceState.CLOSED, frame_id=2)
    receipt = build_open_bank_attempt_receipt(
        attempt_id="open-1",
        issued_monotonic_s=2.0,
        preceding_provenance=context.last_accepted_provenance,
    )
    result = advance_banking_workflow(
        context, OpenBankAttempted(receipt=receipt), evaluated_monotonic_s=200.0
    )
    assert result.state is BankingWorkflowState.BANK_CLOSED_VERIFIED
    assert result.blockers == (BankingBlocker.ATTEMPT_RECEIPT_STALE,)


def test_open_bank_attempt_duplicate_receipt_denied_after_reattempt() -> None:
    """A retried attempt after a fault (bank still closed) must not reuse an old receipt id."""
    context = _arrive(_initial(), frame_id=1)
    context = _observe_bank(context, BankInterfaceState.CLOSED, frame_id=2)
    receipt = build_open_bank_attempt_receipt(
        attempt_id="open-1",
        issued_monotonic_s=2.0,
        preceding_provenance=context.last_accepted_provenance,
    )
    context = advance_banking_workflow(
        context, OpenBankAttempted(receipt=receipt), evaluated_monotonic_s=2.0
    )
    assert context.state is BankingWorkflowState.BANK_OPEN_ATTEMPT_PENDING

    context = _observe_bank(context, BankInterfaceState.CLOSED, frame_id=3)
    assert context.state is BankingWorkflowState.BANK_CLOSED_VERIFIED

    replayed_receipt = build_open_bank_attempt_receipt(
        attempt_id="open-1",
        issued_monotonic_s=3.0,
        preceding_provenance=context.last_accepted_provenance,
    )
    result = advance_banking_workflow(
        context, OpenBankAttempted(receipt=replayed_receipt), evaluated_monotonic_s=3.0
    )
    assert result.state is BankingWorkflowState.BANK_CLOSED_VERIFIED
    assert result.blockers == (BankingBlocker.ATTEMPT_RECEIPT_DUPLICATE,)


def test_deposit_attempt_with_valid_receipt_advances_and_records_receipt() -> None:
    context = _to_deposit_ready(_initial())
    receipt = build_deposit_attempt_receipt(
        attempt_id="deposit-1",
        issued_monotonic_s=3.0,
        preceding_provenance=context.last_accepted_provenance,
    )
    result = advance_banking_workflow(
        context, DepositAttempted(receipt=receipt), evaluated_monotonic_s=3.0
    )
    assert result.state is BankingWorkflowState.DEPOSIT_ATTEMPT_PENDING
    assert result.blockers == ()
    assert "deposit-1" in result.used_attempt_receipt_ids
    assert result.pending_attempt_receipt is receipt


@pytest.mark.parametrize("captured_monotonic_s", (3.5, 4.0))
def test_empty_inventory_must_be_strictly_post_deposit_receipt(
    captured_monotonic_s: float,
) -> None:
    context = _to_deposit_ready(_initial())
    context = _record_deposit_attempt(context, issued_monotonic_s=4.0)
    retained_receipt = context.pending_attempt_receipt
    observation = build_post_deposit_inventory_observation(
        occupied_slots=0,
        provenance=build_provenance(
            frame_id=4,
            captured_monotonic_s=captured_monotonic_s,
        ),
    )

    result = advance_banking_workflow(
        context,
        PostDepositInventoryObservationEvidence(observations=(observation,)),
        evaluated_monotonic_s=4.0,
    )

    assert result.state is BankingWorkflowState.DEPOSIT_ATTEMPT_PENDING
    assert result.blockers == (BankingBlocker.POST_ATTEMPT_EVIDENCE_NOT_FRESH,)
    assert result.pending_attempt_receipt is retained_receipt


def test_strictly_post_attempt_empty_inventory_may_complete() -> None:
    context = _to_deposit_ready(_initial())
    context = _record_deposit_attempt(context, issued_monotonic_s=4.0)
    observation = build_post_deposit_inventory_observation(
        occupied_slots=0,
        provenance=build_provenance(frame_id=4, captured_monotonic_s=4.01),
    )

    result = advance_banking_workflow(
        context,
        PostDepositInventoryObservationEvidence(observations=(observation,)),
        evaluated_monotonic_s=4.01,
    )

    assert result.state is BankingWorkflowState.BANKING_COMPLETE
    assert result.blockers == ()


@pytest.mark.parametrize(
    ("elapsed_s", "expected_state", "expected_blockers"),
    [
        (MAX_ATTEMPT_RECEIPT_AGE_S, BankingWorkflowState.BANKING_COMPLETE, ()),
        (
            MAX_ATTEMPT_RECEIPT_AGE_S + 0.001,
            BankingWorkflowState.DEPOSIT_ATTEMPT_PENDING,
            (BankingBlocker.POST_ATTEMPT_EVIDENCE_STALE,),
        ),
    ],
)
def test_deposit_result_has_bounded_post_attempt_window(
    elapsed_s: float,
    expected_state: BankingWorkflowState,
    expected_blockers: tuple[BankingBlocker, ...],
) -> None:
    context = _to_deposit_ready(_initial())
    context = _record_deposit_attempt(context, issued_monotonic_s=4.0)
    retained_receipt = context.pending_attempt_receipt
    captured = 4.0 + elapsed_s
    observation = build_post_deposit_inventory_observation(
        occupied_slots=0,
        provenance=build_provenance(frame_id=4, captured_monotonic_s=captured),
    )

    result = advance_banking_workflow(
        context,
        PostDepositInventoryObservationEvidence(observations=(observation,)),
        evaluated_monotonic_s=captured,
    )

    assert result.state is expected_state
    assert result.blockers == expected_blockers
    assert result.pending_attempt_receipt is (None if not expected_blockers else retained_receipt)


def test_malformed_deposit_result_is_denied_before_receipt_boundary_dereference() -> None:
    context = _to_deposit_ready(_initial())
    context = _record_deposit_attempt(context, issued_monotonic_s=3.0)
    retained_receipt = context.pending_attempt_receipt
    observation = build_post_deposit_inventory_observation(
        occupied_slots=0,
        provenance=build_provenance(frame_id=4, captured_monotonic_s=4.0),
    )
    event = PostDepositInventoryObservationEvidence(observations=(observation,))
    object.__setattr__(observation, "provenance", object())

    result = advance_banking_workflow(context, event, evaluated_monotonic_s=4.0)

    assert result.state is BankingWorkflowState.DEPOSIT_ATTEMPT_PENDING
    assert result.blockers == (BankingBlocker.INVENTORY_EVIDENCE_TYPE_INVALID,)
    assert result.pending_attempt_receipt is retained_receipt


def test_post_deposit_inventory_from_another_cycle_cannot_complete() -> None:
    context = _to_deposit_ready(_initial())
    context = _record_deposit_attempt(context, issued_monotonic_s=4.0)
    observation = build_post_deposit_inventory_observation(
        occupied_slots=0,
        provenance=build_provenance(
            frame_id=4,
            captured_monotonic_s=4.01,
            cycle_id="foreign-cycle",
        ),
    )
    result = advance_banking_workflow(
        context,
        PostDepositInventoryObservationEvidence(observations=(observation,)),
        evaluated_monotonic_s=4.01,
    )
    assert result.state is BankingWorkflowState.DEPOSIT_ATTEMPT_PENDING
    assert result.blockers == (BankingBlocker.EVIDENCE_PROVENANCE_MISMATCH,)


def test_deposit_attempt_rejects_stale_preceding_evidence() -> None:
    context = _to_deposit_ready(_initial())
    issued = 3.0 + MAX_ATTEMPT_RECEIPT_AGE_S + 0.001
    receipt = build_deposit_attempt_receipt(
        issued_monotonic_s=issued,
        preceding_provenance=context.last_accepted_provenance,
    )
    result = advance_banking_workflow(
        context,
        DepositAttempted(receipt=receipt),
        evaluated_monotonic_s=issued,
    )
    assert result.state is BankingWorkflowState.DEPOSIT_READY_VERIFIED
    assert result.blockers == (BankingBlocker.ATTEMPT_PRECEDING_EVIDENCE_STALE,)


def test_deposit_attempt_receipt_wrong_provenance_denied() -> None:
    context = _to_deposit_ready(_initial())
    receipt = build_deposit_attempt_receipt(
        attempt_id="deposit-1",
        issued_monotonic_s=3.0,
        preceding_provenance=build_provenance(
            frame_id=3, captured_monotonic_s=3.0, cycle_id="wrong-cycle"
        ),
    )
    result = advance_banking_workflow(
        context, DepositAttempted(receipt=receipt), evaluated_monotonic_s=3.0
    )
    assert result.state is BankingWorkflowState.DEPOSIT_READY_VERIFIED
    assert result.blockers == (BankingBlocker.ATTEMPT_RECEIPT_WRONG_PROVENANCE,)


def test_deposit_attempt_stale_receipt_denied() -> None:
    context = _to_deposit_ready(_initial())
    receipt = build_deposit_attempt_receipt(
        attempt_id="deposit-1",
        issued_monotonic_s=3.0,
        preceding_provenance=context.last_accepted_provenance,
    )
    result = advance_banking_workflow(
        context, DepositAttempted(receipt=receipt), evaluated_monotonic_s=300.0
    )
    assert result.state is BankingWorkflowState.DEPOSIT_READY_VERIFIED
    assert result.blockers == (BankingBlocker.ATTEMPT_RECEIPT_STALE,)


def test_deposit_attempt_without_receipt_is_denied_without_entering_pending() -> None:
    context = _to_deposit_ready(_initial())
    result = advance_banking_workflow(context, DepositAttempted(), evaluated_monotonic_s=3.0)
    assert result.state is BankingWorkflowState.DEPOSIT_READY_VERIFIED
    assert result.blockers == (BankingBlocker.ATTEMPT_RECEIPT_MISSING,)
    assert result.used_attempt_receipt_ids == frozenset()
    assert result.pending_attempt_receipt is None


def test_open_attempt_without_receipt_is_denied_without_entering_pending() -> None:
    context = _arrive(_initial(), frame_id=1)
    context = _observe_bank(context, BankInterfaceState.CLOSED, frame_id=2)
    result = advance_banking_workflow(context, OpenBankAttempted(), evaluated_monotonic_s=2.0)
    assert result.state is BankingWorkflowState.BANK_CLOSED_VERIFIED
    assert result.blockers == (BankingBlocker.ATTEMPT_RECEIPT_MISSING,)
    assert result.used_attempt_receipt_ids == frozenset()
    assert result.pending_attempt_receipt is None


def test_open_bank_attempted_rejects_wrong_receipt_type() -> None:
    with pytest.raises(ValueError, match="receipt must be an exact OpenBankAttemptReceipt or None"):
        OpenBankAttempted(receipt="not-a-receipt")  # type: ignore[arg-type]


def test_deposit_attempted_rejects_wrong_receipt_type() -> None:
    with pytest.raises(ValueError, match="receipt must be an exact DepositAttemptReceipt or None"):
        DepositAttempted(receipt="not-a-receipt")  # type: ignore[arg-type]


def test_context_replace_cannot_erase_used_attempt_receipt_ids() -> None:
    context = _arrive(_initial(), frame_id=1)
    context = _observe_bank(context, BankInterfaceState.CLOSED, frame_id=2)
    pending = _record_open_attempt(context, issued_monotonic_s=2.0)

    with pytest.raises(TypeError, match="reducer-issued"):
        replace(pending, used_attempt_receipt_ids=frozenset())
