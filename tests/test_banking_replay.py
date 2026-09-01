"""Deterministic replay/simulation of full banking-visit scenarios.

Each scenario is a fixed, named sequence of workflow events replayed through
:func:`advance_banking_workflow` from a fresh context. This is architecture
simulation only: every event is built from
:mod:`mining_automation.banking.testing` synthetic fixtures, none of it is
real OSRS evidence, and none of it drives RuneLite, capture, or any input
surface.

Complements the transition-by-transition adversarial matrix in
``test_banking_workflow.py`` with whole-visit, end-to-end replay -- the shape
Part E of the constrained-v1 banking foundation asks for: a happy path, named
failure paths, and proof that replaying the same scenario twice always
produces the same result.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from mining_automation.banking.attempts import OpenBankAttemptReceipt
from mining_automation.banking.contracts import BankingBlocker, BankInterfaceState
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
    BankingWorkflowEvent,
    BankingWorkflowState,
    BankObservationEvidence,
    CheckpointArrivalEvidence,
    DepositAttempted,
    OpenBankAttempted,
    PostDepositInventoryObservationEvidence,
    PreDepositInventoryObservationEvidence,
    advance_banking_workflow,
    initial_banking_workflow_context,
)


@dataclass(frozen=True, slots=True)
class WorkflowStep:
    event: BankingWorkflowEvent
    evaluated_monotonic_s: float


@dataclass(frozen=True, slots=True)
class WorkflowScenario:
    scenario_id: str
    steps: tuple[WorkflowStep, ...]
    expected_final_state: BankingWorkflowState
    expected_final_blockers: tuple[BankingBlocker, ...] = ()


def _arrive_step(frame_id: int) -> WorkflowStep:
    provenance = build_provenance(frame_id=frame_id, captured_monotonic_s=float(frame_id))
    return WorkflowStep(
        CheckpointArrivalEvidence(identity=SYNTHETIC_BANK_CHECKPOINT, provenance=provenance),
        evaluated_monotonic_s=float(frame_id),
    )


def _bank_observed_step(
    interface_state: BankInterfaceState,
    frame_id: int,
    *,
    evaluated_monotonic_s: float | None = None,
    profile=SYNTHETIC_BANK_PROFILE,
    width: int | None = None,
    height: int | None = None,
    captured_monotonic_s: float | None = None,
) -> WorkflowStep:
    provenance = build_provenance(
        frame_id=frame_id,
        captured_monotonic_s=(
            captured_monotonic_s if captured_monotonic_s is not None else float(frame_id)
        ),
        width=width if width is not None else profile.frame_width,
        height=height if height is not None else profile.frame_height,
    )
    observation = build_bank_observation(
        interface_state=interface_state, provenance=provenance, profile=profile
    )
    return WorkflowStep(
        BankObservationEvidence(observations=(observation,)),
        evaluated_monotonic_s=(
            evaluated_monotonic_s if evaluated_monotonic_s is not None else float(frame_id)
        ),
    )


def _open_attempt_step(evaluated_monotonic_s: float) -> WorkflowStep:
    frame_id = int(evaluated_monotonic_s)
    receipt = build_open_bank_attempt_receipt(
        attempt_id="synthetic-replay-open-attempt",
        issued_monotonic_s=evaluated_monotonic_s,
        preceding_provenance=build_provenance(
            frame_id=frame_id,
            captured_monotonic_s=float(frame_id),
        ),
    )
    return WorkflowStep(
        OpenBankAttempted(receipt=receipt),
        evaluated_monotonic_s=evaluated_monotonic_s,
    )


def _open_attempt_with_receipt_step(
    receipt: OpenBankAttemptReceipt, evaluated_monotonic_s: float
) -> WorkflowStep:
    return WorkflowStep(
        OpenBankAttempted(receipt=receipt), evaluated_monotonic_s=evaluated_monotonic_s
    )


def _deposit_attempt_step(evaluated_monotonic_s: float) -> WorkflowStep:
    frame_id = int(evaluated_monotonic_s)
    receipt = build_deposit_attempt_receipt(
        attempt_id="synthetic-replay-deposit-attempt",
        issued_monotonic_s=evaluated_monotonic_s,
        preceding_provenance=build_provenance(
            frame_id=frame_id,
            captured_monotonic_s=float(frame_id),
        ),
    )
    return WorkflowStep(
        DepositAttempted(receipt=receipt),
        evaluated_monotonic_s=evaluated_monotonic_s,
    )


def _pre_deposit_inventory_step(
    occupied_slots: int | None,
    frame_id: int,
    *,
    captured_monotonic_s: float | None = None,
    evaluated_monotonic_s: float | None = None,
) -> WorkflowStep:
    captured = captured_monotonic_s if captured_monotonic_s is not None else float(frame_id)
    provenance = build_provenance(frame_id=frame_id, captured_monotonic_s=captured)
    observation = build_pre_deposit_inventory_observation(
        occupied_slots=occupied_slots, provenance=provenance
    )
    return WorkflowStep(
        PreDepositInventoryObservationEvidence(observations=(observation,)),
        evaluated_monotonic_s=(
            evaluated_monotonic_s if evaluated_monotonic_s is not None else float(frame_id)
        ),
    )


def _post_deposit_inventory_step(
    occupied_slots: int | None,
    frame_id: int,
    *,
    provenance=None,
    evaluated_monotonic_s: float | None = None,
) -> WorkflowStep:
    used_provenance = (
        provenance
        if provenance is not None
        else build_provenance(frame_id=frame_id, captured_monotonic_s=float(frame_id))
    )
    observation = build_post_deposit_inventory_observation(
        occupied_slots=occupied_slots, provenance=used_provenance
    )
    return WorkflowStep(
        PostDepositInventoryObservationEvidence(observations=(observation,)),
        evaluated_monotonic_s=(
            evaluated_monotonic_s if evaluated_monotonic_s is not None else float(frame_id)
        ),
    )


HAPPY_PATH = WorkflowScenario(
    scenario_id="happy-path-closed-open-deposit-complete",
    steps=(
        _arrive_step(1),
        _bank_observed_step(BankInterfaceState.CLOSED, 2),
        _open_attempt_step(2.0),
        _bank_observed_step(BankInterfaceState.OPEN, 3),
        _pre_deposit_inventory_step(28, 4),
        _deposit_attempt_step(4.0),
        _post_deposit_inventory_step(0, 5),
    ),
    expected_final_state=BankingWorkflowState.BANKING_COMPLETE,
)

STILL_CLOSED_AFTER_OPEN_ATTEMPT = WorkflowScenario(
    scenario_id="closed-open-attempted-still-closed",
    steps=(
        _arrive_step(1),
        _bank_observed_step(BankInterfaceState.CLOSED, 2),
        _open_attempt_step(2.0),
        _bank_observed_step(BankInterfaceState.CLOSED, 3),
    ),
    expected_final_state=BankingWorkflowState.BANK_CLOSED_VERIFIED,
)

STILL_FULL_AFTER_DEPOSIT_ATTEMPT = WorkflowScenario(
    scenario_id="open-deposit-attempted-still-full",
    steps=(
        _arrive_step(1),
        _bank_observed_step(BankInterfaceState.OPEN, 2),
        _pre_deposit_inventory_step(28, 3),
        _deposit_attempt_step(3.0),
        _post_deposit_inventory_step(28, 4),
    ),
    expected_final_state=BankingWorkflowState.DEPOSIT_ATTEMPT_PENDING,
    expected_final_blockers=(BankingBlocker.DEPOSIT_INVENTORY_STILL_NON_EMPTY,),
)

UNKNOWN_INVENTORY_AFTER_DEPOSIT_ATTEMPT = WorkflowScenario(
    scenario_id="open-deposit-attempted-unknown-inventory",
    steps=(
        _arrive_step(1),
        _bank_observed_step(BankInterfaceState.OPEN, 2),
        _pre_deposit_inventory_step(28, 3),
        _deposit_attempt_step(3.0),
        _post_deposit_inventory_step(None, 4),
    ),
    expected_final_state=BankingWorkflowState.DEPOSIT_ATTEMPT_PENDING,
    expected_final_blockers=(BankingBlocker.POST_DEPOSIT_INVENTORY_UNKNOWN,),
)

STALE_EVIDENCE = WorkflowScenario(
    scenario_id="stale-bank-observation-after-arrival",
    steps=(
        _arrive_step(1),
        _bank_observed_step(BankInterfaceState.OPEN, 2, evaluated_monotonic_s=100.0),
    ),
    expected_final_state=BankingWorkflowState.ARRIVED_AT_BANK_CHECKPOINT,
    expected_final_blockers=(BankingBlocker.BANK_EVIDENCE_STALE,),
)

FRAME_GEOMETRY_MISMATCH = WorkflowScenario(
    scenario_id="bank-observation-wrong-frame-geometry",
    steps=(
        _arrive_step(1),
        _bank_observed_step(BankInterfaceState.OPEN, 2, width=1, height=1),
    ),
    expected_final_state=BankingWorkflowState.ARRIVED_AT_BANK_CHECKPOINT,
    expected_final_blockers=(BankingBlocker.BANK_GEOMETRY_UNSUPPORTED,),
)

ORDERING_ERROR = WorkflowScenario(
    scenario_id="bank-observation-replays-arrival-frame",
    steps=(
        _arrive_step(5),
        _bank_observed_step(BankInterfaceState.OPEN, 5),
    ),
    expected_final_state=BankingWorkflowState.ARRIVED_AT_BANK_CHECKPOINT,
    expected_final_blockers=(BankingBlocker.EVIDENCE_ORDERING_REGRESSION,),
)

PROFILE_CHANGE = WorkflowScenario(
    scenario_id="bank-observation-declares-a-different-profile-version",
    steps=(
        _arrive_step(1),
        _bank_observed_step(
            BankInterfaceState.OPEN,
            2,
            profile=replace(SYNTHETIC_BANK_PROFILE, profile_version="9.9.9"),
        ),
    ),
    expected_final_state=BankingWorkflowState.ARRIVED_AT_BANK_CHECKPOINT,
    expected_final_blockers=(BankingBlocker.BANK_PROFILE_MISMATCH,),
)

BANK_CLOSES_UNEXPECTEDLY_AFTER_OPEN_VERIFIED = WorkflowScenario(
    scenario_id="bank-closes-unexpectedly-after-open-verified",
    steps=(
        _arrive_step(1),
        _bank_observed_step(BankInterfaceState.OPEN, 2),
        # A stray re-observation once OPEN is already verified -- e.g. the
        # bank interface unexpectedly closed mid-visit. The workflow does not
        # re-verify bank state from BANK_OPEN_VERIFIED; it must deny this,
        # never silently treat it as a new/different accepted state.
        _bank_observed_step(BankInterfaceState.CLOSED, 3),
    ),
    expected_final_state=BankingWorkflowState.BANK_OPEN_VERIFIED,
    expected_final_blockers=(BankingBlocker.UNEXPECTED_EVENT_FOR_STATE,),
)

INTERRUPTED_TRANSACTION_STRAY_ARRIVAL_DURING_DEPOSIT_PENDING = WorkflowScenario(
    scenario_id="interrupted-transaction-stray-arrival-during-deposit-pending",
    steps=(
        _arrive_step(1),
        _bank_observed_step(BankInterfaceState.OPEN, 2),
        _pre_deposit_inventory_step(28, 3),
        _deposit_attempt_step(3.0),
        # Simulates a session interruption replaying an old arrival event
        # while a deposit is still pending -- must not be mistaken for a
        # fresh post-deposit inventory reading or a restart.
        _arrive_step(4),
    ),
    expected_final_state=BankingWorkflowState.DEPOSIT_ATTEMPT_PENDING,
    expected_final_blockers=(BankingBlocker.UNEXPECTED_EVENT_FOR_STATE,),
)

_DUPLICATE_RECEIPT_AFTER_FAULT_ATTEMPT_ID = "replay-open-attempt-1"

DUPLICATE_RECEIPT_AFTER_FAULT = WorkflowScenario(
    scenario_id="duplicate-attempt-receipt-reused-after-open-fails",
    steps=(
        _arrive_step(1),
        _bank_observed_step(BankInterfaceState.CLOSED, 2),
        _open_attempt_with_receipt_step(
            OpenBankAttemptReceipt(
                attempt_id=_DUPLICATE_RECEIPT_AFTER_FAULT_ATTEMPT_ID,
                issued_monotonic_s=2.0,
                preceding_provenance=build_provenance(frame_id=2, captured_monotonic_s=2.0),
            ),
            evaluated_monotonic_s=2.0,
        ),
        # Open fails -- the bank is still closed on the next observation.
        _bank_observed_step(BankInterfaceState.CLOSED, 3),
        # A buggy fault-recovery retry reuses the exact same attempt id.
        _open_attempt_with_receipt_step(
            OpenBankAttemptReceipt(
                attempt_id=_DUPLICATE_RECEIPT_AFTER_FAULT_ATTEMPT_ID,
                issued_monotonic_s=3.0,
                preceding_provenance=build_provenance(frame_id=3, captured_monotonic_s=3.0),
            ),
            evaluated_monotonic_s=3.0,
        ),
    ),
    expected_final_state=BankingWorkflowState.BANK_CLOSED_VERIFIED,
    expected_final_blockers=(BankingBlocker.ATTEMPT_RECEIPT_DUPLICATE,),
)

PRE_ATTEMPT_OPEN_OBSERVATION_DELIVERED_AFTER_RECEIPT = WorkflowScenario(
    scenario_id="pre-attempt-open-observation-delivered-after-receipt",
    steps=(
        _arrive_step(1),
        _bank_observed_step(BankInterfaceState.CLOSED, 2),
        _open_attempt_with_receipt_step(
            OpenBankAttemptReceipt(
                attempt_id="delayed-pre-attempt-open",
                issued_monotonic_s=3.0,
                preceding_provenance=build_provenance(
                    frame_id=2,
                    captured_monotonic_s=2.0,
                ),
            ),
            evaluated_monotonic_s=3.0,
        ),
        _bank_observed_step(
            BankInterfaceState.OPEN,
            3,
            captured_monotonic_s=2.5,
            evaluated_monotonic_s=3.0,
        ),
    ),
    expected_final_state=BankingWorkflowState.BANK_OPEN_ATTEMPT_PENDING,
    expected_final_blockers=(BankingBlocker.POST_ATTEMPT_EVIDENCE_NOT_FRESH,),
)

PRE_ATTEMPT_EMPTY_INVENTORY_DELIVERED_AFTER_RECEIPT = WorkflowScenario(
    scenario_id="pre-attempt-empty-inventory-delivered-after-receipt",
    steps=(
        _arrive_step(1),
        _bank_observed_step(BankInterfaceState.OPEN, 2),
        _pre_deposit_inventory_step(28, 3),
        WorkflowStep(
            DepositAttempted(
                receipt=build_deposit_attempt_receipt(
                    attempt_id="delayed-pre-attempt-deposit",
                    issued_monotonic_s=4.0,
                    preceding_provenance=build_provenance(
                        frame_id=3,
                        captured_monotonic_s=3.0,
                    ),
                )
            ),
            evaluated_monotonic_s=4.0,
        ),
        _post_deposit_inventory_step(
            0,
            4,
            provenance=build_provenance(
                frame_id=4,
                captured_monotonic_s=3.5,
            ),
            evaluated_monotonic_s=4.0,
        ),
    ),
    expected_final_state=BankingWorkflowState.DEPOSIT_ATTEMPT_PENDING,
    expected_final_blockers=(BankingBlocker.POST_ATTEMPT_EVIDENCE_NOT_FRESH,),
)

DELAYED_OPEN_OUTCOME_AFTER_RECEIPT = WorkflowScenario(
    scenario_id="delayed-open-outcome-after-receipt",
    steps=(
        _arrive_step(1),
        _bank_observed_step(BankInterfaceState.CLOSED, 2),
        _open_attempt_step(2.0),
        _bank_observed_step(
            BankInterfaceState.OPEN,
            3,
            captured_monotonic_s=100.0,
            evaluated_monotonic_s=100.0,
        ),
    ),
    expected_final_state=BankingWorkflowState.BANK_OPEN_ATTEMPT_PENDING,
    expected_final_blockers=(BankingBlocker.POST_ATTEMPT_EVIDENCE_STALE,),
)

DELAYED_EMPTY_INVENTORY_AFTER_RECEIPT = WorkflowScenario(
    scenario_id="delayed-empty-inventory-after-receipt",
    steps=(
        _arrive_step(1),
        _bank_observed_step(BankInterfaceState.OPEN, 2),
        _pre_deposit_inventory_step(28, 3),
        _deposit_attempt_step(3.0),
        _post_deposit_inventory_step(
            0,
            4,
            provenance=build_provenance(frame_id=4, captured_monotonic_s=100.0),
            evaluated_monotonic_s=100.0,
        ),
    ),
    expected_final_state=BankingWorkflowState.DEPOSIT_ATTEMPT_PENDING,
    expected_final_blockers=(BankingBlocker.POST_ATTEMPT_EVIDENCE_STALE,),
)

OLD_BANK_OPEN_SUPPORT_WITH_FRESH_INVENTORY = WorkflowScenario(
    scenario_id="old-bank-open-support-with-fresh-inventory",
    steps=(
        _arrive_step(1),
        _bank_observed_step(BankInterfaceState.OPEN, 2),
        _pre_deposit_inventory_step(
            28,
            3,
            captured_monotonic_s=100.0,
            evaluated_monotonic_s=100.0,
        ),
    ),
    expected_final_state=BankingWorkflowState.BANK_OPEN_VERIFIED,
    expected_final_blockers=(BankingBlocker.SUPPORTING_EVIDENCE_STALE,),
)

ALL_SCENARIOS = (
    HAPPY_PATH,
    STILL_CLOSED_AFTER_OPEN_ATTEMPT,
    STILL_FULL_AFTER_DEPOSIT_ATTEMPT,
    UNKNOWN_INVENTORY_AFTER_DEPOSIT_ATTEMPT,
    STALE_EVIDENCE,
    FRAME_GEOMETRY_MISMATCH,
    ORDERING_ERROR,
    PROFILE_CHANGE,
    BANK_CLOSES_UNEXPECTEDLY_AFTER_OPEN_VERIFIED,
    INTERRUPTED_TRANSACTION_STRAY_ARRIVAL_DURING_DEPOSIT_PENDING,
    DUPLICATE_RECEIPT_AFTER_FAULT,
    PRE_ATTEMPT_OPEN_OBSERVATION_DELIVERED_AFTER_RECEIPT,
    PRE_ATTEMPT_EMPTY_INVENTORY_DELIVERED_AFTER_RECEIPT,
    DELAYED_OPEN_OUTCOME_AFTER_RECEIPT,
    DELAYED_EMPTY_INVENTORY_AFTER_RECEIPT,
    OLD_BANK_OPEN_SUPPORT_WITH_FRESH_INVENTORY,
)


def _replay(scenario: WorkflowScenario) -> BankingWorkflowContext:
    context = initial_banking_workflow_context(
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        expected_detector=SYNTHETIC_BANK_DETECTOR_METADATA,
    )
    for step in scenario.steps:
        context = advance_banking_workflow(
            context, step.event, evaluated_monotonic_s=step.evaluated_monotonic_s
        )
    return context


@pytest.mark.parametrize("scenario", ALL_SCENARIOS, ids=lambda s: s.scenario_id)
def test_scenario_reaches_expected_final_state(scenario: WorkflowScenario) -> None:
    result = _replay(scenario)
    assert result.state is scenario.expected_final_state
    assert result.blockers == scenario.expected_final_blockers


@pytest.mark.parametrize("scenario", ALL_SCENARIOS, ids=lambda s: s.scenario_id)
def test_scenario_replay_is_deterministic(scenario: WorkflowScenario) -> None:
    first = _replay(scenario)
    second = _replay(scenario)
    assert first == second


def test_happy_path_never_treats_an_attempt_as_success() -> None:
    """No prefix of the happy path that stops right after an attempt event
    is ever in a verified state -- only the observation that follows one is.
    """
    attempt_indices = [
        index
        for index, step in enumerate(HAPPY_PATH.steps)
        if isinstance(step.event, (OpenBankAttempted, DepositAttempted))
    ]
    assert attempt_indices, "scenario must contain at least one attempt event"

    context = initial_banking_workflow_context(
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        expected_detector=SYNTHETIC_BANK_DETECTOR_METADATA,
    )
    verified_states = {
        BankingWorkflowState.BANK_OPEN_VERIFIED,
        BankingWorkflowState.BANK_CLOSED_VERIFIED,
        BankingWorkflowState.DEPOSIT_READY_VERIFIED,
        BankingWorkflowState.BANKING_COMPLETE,
    }
    for index, step in enumerate(HAPPY_PATH.steps):
        context = advance_banking_workflow(
            context, step.event, evaluated_monotonic_s=step.evaluated_monotonic_s
        )
        if index in attempt_indices:
            assert context.state not in verified_states
