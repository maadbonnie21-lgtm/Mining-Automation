"""Deterministic state-machine tests for the non-input banking workflow.

Every test here operates purely on typed values -- no capture backend, no
RuneLite, no ``WorldState``. Fixtures come from
:mod:`mining_automation.banking.testing` and are synthetic by construction;
none of them may be read as real OSRS evidence.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mining_automation.banking.contracts import (
    BankCheckpointIdentity,
    BankingBlocker,
    BankInterfaceState,
)
from mining_automation.banking.testing import (
    SYNTHETIC_BANK_CHECKPOINT,
    SYNTHETIC_BANK_PROFILE,
    build_bank_observation,
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


def _initial() -> BankingWorkflowContext:
    return initial_banking_workflow_context(
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
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

    context = advance_banking_workflow(context, OpenBankAttempted(), evaluated_monotonic_s=2.0)
    assert context.state is BankingWorkflowState.BANK_OPEN_ATTEMPT_PENDING
    assert deposit_readiness(context) is DepositReadiness.NOT_READY

    context = _observe_bank(context, BankInterfaceState.OPEN, frame_id=3)
    assert context.state is BankingWorkflowState.BANK_OPEN_VERIFIED

    context = _observe_pre_deposit_inventory(context, 28, frame_id=4)
    assert context.state is BankingWorkflowState.DEPOSIT_READY_VERIFIED
    assert deposit_readiness(context) is DepositReadiness.READY

    context = advance_banking_workflow(context, DepositAttempted(), evaluated_monotonic_s=4.0)
    assert context.state is BankingWorkflowState.DEPOSIT_ATTEMPT_PENDING
    assert deposit_readiness(context) is DepositReadiness.NOT_READY

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
        context = advance_banking_workflow(context, DepositAttempted(), evaluated_monotonic_s=3.0)
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
    context = advance_banking_workflow(context, DepositAttempted(), evaluated_monotonic_s=3.0)
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
    context = advance_banking_workflow(context, OpenBankAttempted(), evaluated_monotonic_s=2.0)
    context = _observe_bank(context, BankInterfaceState.CLOSED, frame_id=3)
    assert context.state is BankingWorkflowState.BANK_CLOSED_VERIFIED
    assert context.blockers == ()


def test_deposit_attempt_can_still_be_full() -> None:
    context = _to_deposit_ready(_initial())
    context = advance_banking_workflow(context, DepositAttempted(), evaluated_monotonic_s=3.0)
    result = _observe_post_deposit_inventory(context, 28, frame_id=4)
    assert result.state is BankingWorkflowState.DEPOSIT_ATTEMPT_PENDING
    assert result.blockers == (BankingBlocker.DEPOSIT_INVENTORY_STILL_NON_EMPTY,)


def test_deposit_attempt_can_yield_unknown_inventory() -> None:
    context = _to_deposit_ready(_initial())
    context = advance_banking_workflow(context, DepositAttempted(), evaluated_monotonic_s=3.0)
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
    context = advance_banking_workflow(context, OpenBankAttempted(), evaluated_monotonic_s=2.0)
    result = _observe_bank(context, BankInterfaceState.CLOSED, frame_id=3)
    assert result.state is BankingWorkflowState.BANK_CLOSED_VERIFIED
    assert result.blockers == ()


def test_open_attempt_without_fresh_verification() -> None:
    context = _arrive(_initial(), frame_id=1)
    context = _observe_bank(context, BankInterfaceState.CLOSED, frame_id=2)
    context = advance_banking_workflow(context, OpenBankAttempted(), evaluated_monotonic_s=2.0)
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


def test_deposit_attempted_inventory_remains_non_empty() -> None:
    context = _to_deposit_ready(_initial())
    context = advance_banking_workflow(context, DepositAttempted(), evaluated_monotonic_s=3.0)
    result = _observe_post_deposit_inventory(context, 15, frame_id=4)
    assert result.blockers == (BankingBlocker.DEPOSIT_INVENTORY_STILL_NON_EMPTY,)


def test_deposit_attempted_resulting_inventory_unknown() -> None:
    context = _to_deposit_ready(_initial())
    context = advance_banking_workflow(context, DepositAttempted(), evaluated_monotonic_s=3.0)
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
    context = advance_banking_workflow(context, DepositAttempted(), evaluated_monotonic_s=3.0)

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
    assert result.blockers == (BankingBlocker.EVIDENCE_ORDERING_REGRESSION,)


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


# ---------------------------------------------------------------------------
# Context construction invariants
# ---------------------------------------------------------------------------


def test_context_rejects_state_without_provenance() -> None:
    with pytest.raises(ValueError, match="requires accepted provenance"):
        BankingWorkflowContext(
            state=BankingWorkflowState.BANK_OPEN_VERIFIED,
            expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
            expected_profile=SYNTHETIC_BANK_PROFILE,
            blockers=(),
            last_accepted_provenance=None,
        )


def test_deposit_readiness_rejects_non_context() -> None:
    with pytest.raises(TypeError, match="must be an exact BankingWorkflowContext"):
        deposit_readiness("not-a-context")  # type: ignore[arg-type]


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
        ("state", "not-a-state", "state must be an exact BankingWorkflowState"),
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
        ("blockers", ("not-a-blocker",), "blockers must be a tuple of exact BankingBlocker"),
        (
            "blockers",
            (BankingBlocker.BANK_STATE_UNKNOWN, BankingBlocker.BANK_STATE_UNKNOWN),
            "blockers must be unique",
        ),
        (
            "last_accepted_provenance",
            "not-a-provenance",
            "last_accepted_provenance must be an exact BankEvidenceProvenance",
        ),
    ],
)
def test_context_rejects_wrong_field_types(
    field_name: str, value: object, message: str
) -> None:
    kwargs: dict[str, object] = {
        "state": BankingWorkflowState.AWAITING_CHECKPOINT_ARRIVAL,
        "expected_checkpoint": SYNTHETIC_BANK_CHECKPOINT,
        "expected_profile": SYNTHETIC_BANK_PROFILE,
        "blockers": (),
        "last_accepted_provenance": None,
    }
    kwargs[field_name] = value
    with pytest.raises(ValueError, match=message):
        BankingWorkflowContext(**kwargs)  # type: ignore[arg-type]


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
    context = advance_banking_workflow(context, DepositAttempted(), evaluated_monotonic_s=3.0)
    result = advance_banking_workflow(context, DepositAttempted(), evaluated_monotonic_s=3.0)
    assert result.state is BankingWorkflowState.DEPOSIT_ATTEMPT_PENDING
    assert result.blockers == (BankingBlocker.UNEXPECTED_EVENT_FOR_STATE,)


def test_post_deposit_observation_missing() -> None:
    context = _to_deposit_ready(_initial())
    context = advance_banking_workflow(context, DepositAttempted(), evaluated_monotonic_s=3.0)
    result = advance_banking_workflow(
        context,
        PostDepositInventoryObservationEvidence(observations=()),
        evaluated_monotonic_s=3.0,
    )
    assert result.blockers == (BankingBlocker.INVENTORY_EVIDENCE_MISSING,)
