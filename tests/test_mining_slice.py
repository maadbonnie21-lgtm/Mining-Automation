from __future__ import annotations

from dataclasses import replace

import pytest

import mining_automation.mining_slice as mining_slice
from mining_automation.contracts import InventoryState, ResourceState
from mining_automation.mining_slice import (
    AssemblyResult,
    AttemptProgress,
    EvidenceRole,
    MiningAttemptProposal,
    MiningAttemptReceipt,
    MiningOnlyPhase,
    MiningOnlySession,
    MiningStopReason,
    OneAttemptExecutor,
    PerceptionEpoch,
    ReleaseComponent,
    ReleaseReceipt,
    assemble_released_mining_observation,
    make_synthetic_release_receipt,
    verify_attempt_progress,
)


def _digest(char: str) -> str:
    return char * 64


def _git(char: str) -> str:
    return char * 40


def _epoch(
    frame_id: int = 10,
    captured: float = 10.0,
    *,
    cycle_id: str | None = None,
    payload: str | None = None,
    source_id: str = "runelite-source",
    session_id: str = "session-1",
) -> PerceptionEpoch:
    return PerceptionEpoch(
        source_id=source_id,
        session_id=session_id,
        cycle_id=cycle_id or f"cycle-{frame_id}",
        frame_id=frame_id,
        captured_monotonic_s=captured,
        frame_payload_sha256=payload or _digest(str(frame_id % 10)),
    )


def _receipts() -> tuple[ReleaseReceipt, ReleaseReceipt]:
    return (
        make_synthetic_release_receipt(
            ReleaseComponent.RESOURCE,
            release_commit_sha=_git("a"),
            receipt_sha256=_digest("b"),
            producer_id="resource-producer",
            release_id="resource-release-1",
        ),
        make_synthetic_release_receipt(
            ReleaseComponent.INVENTORY,
            release_commit_sha=_git("c"),
            receipt_sha256=_digest("d"),
            producer_id="inventory-producer",
            release_id="inventory-release-1",
        ),
    )


def _resources(
    *,
    first_available: bool | None = True,
    second_available: bool | None = True,
) -> tuple[ResourceState, ...]:
    return (
        ResourceState("iron-northwest", "iron", first_available, 0.95, (10, 20, 30, 40)),
        ResourceState("copper-decoy", "copper", True, 0.99, (50, 60, 20, 20)),
        ResourceState("iron-center", "iron", second_available, 0.99, (80, 90, 30, 30)),
    )


def _assemble(
    *,
    epoch: PerceptionEpoch | None = None,
    inventory_epoch: PerceptionEpoch | None = None,
    resources: tuple[ResourceState, ...] | None = None,
    occupied: int | None = 5,
    confidence: float = 0.95,
    capacity: int = 28,
    supported: bool | None = True,
    now: float = 10.5,
    receipts: tuple[ReleaseReceipt, ReleaseReceipt] | None = None,
) -> AssemblyResult:
    resource_epoch = epoch or _epoch()
    inventory_source = inventory_epoch or resource_epoch
    resource_receipt, inventory_receipt = receipts or _receipts()
    return assemble_released_mining_observation(
        resource_epoch=resource_epoch,
        inventory_epoch=inventory_source,
        resource_receipt=resource_receipt,
        inventory_receipt=inventory_receipt,
        resources=resources if resources is not None else _resources(),
        inventory=InventoryState(occupied_slots=occupied, capacity=capacity, confidence=confidence),
        supported_resource_view=supported,
        now_monotonic_s=now,
    )


def _observation(**kwargs: object) -> mining_slice.ReleasedMiningObservation:
    result = _assemble(**kwargs)  # type: ignore[arg-type]
    assert result.reason is MiningStopReason.NONE
    assert result.observation is not None
    return result.observation


def _proposal(
    observation: mining_slice.ReleasedMiningObservation | None = None,
    *,
    attempt_id: str = "attempt-1",
    ordinal: int = 1,
) -> MiningAttemptProposal:
    owned = observation or _observation()
    return MiningAttemptProposal(
        attempt_id=attempt_id,
        attempt_ordinal=ordinal,
        source_observation=owned,
        target=owned.resources[0],
        reviewed_execution_sha=_git("e"),
    )


def _receipt(
    proposal: MiningAttemptProposal,
    *,
    accepted: bool = True,
    dispatched: float = 10.6,
) -> MiningAttemptReceipt:
    region = proposal.target.interaction_region
    assert region is not None
    return MiningAttemptReceipt(
        attempt_id=proposal.attempt_id,
        attempt_ordinal=proposal.attempt_ordinal,
        source_epoch=proposal.source_observation.epoch,
        target_id=proposal.target.resource_id,
        target_region=region,
        reviewed_execution_sha=proposal.reviewed_execution_sha,
        dispatched_monotonic_s=dispatched,
        dispatch_count=1,
        dispatch_accepted=accepted,
        role=proposal.source_observation.role,
        receipt_sha256=_digest("f"),
    )


def _newer(
    proposal: MiningAttemptProposal,
    *,
    occupied: int = 6,
    first_available: bool | None = False,
    frame_id: int = 11,
    captured: float = 11.0,
    source_id: str = "runelite-source",
    session_id: str = "session-1",
) -> mining_slice.ReleasedMiningObservation:
    epoch = _epoch(
        frame_id,
        captured,
        source_id=source_id,
        session_id=session_id,
    )
    receipts = (
        proposal.source_observation.resource_receipt,
        proposal.source_observation.inventory_receipt,
    )
    return _observation(
        epoch=epoch,
        resources=_resources(first_available=first_available),
        occupied=occupied,
        now=captured + 0.1,
        receipts=receipts,
    )


def test_synthetic_receipts_are_factory_owned_and_never_live_authority() -> None:
    resource_receipt, inventory_receipt = _receipts()
    assert resource_receipt.role is EvidenceRole.SYNTHETIC_ARCHITECTURE_TEST_ONLY
    assert inventory_receipt.role is EvidenceRole.SYNTHETIC_ARCHITECTURE_TEST_ONLY
    assert resource_receipt.live_input_eligible is False
    assert inventory_receipt.live_input_eligible is False
    with pytest.raises(ValueError, match="source-owned factory"):
        ReleaseReceipt(
            ReleaseComponent.RESOURCE,
            EvidenceRole.RELEASED_REAL_CLIENT,
            _git("a"),
            _digest("b"),
            "producer",
            "release",
        )


@pytest.mark.parametrize(
    ("inventory_epoch", "expected"),
    (
        (_epoch(source_id="other"), MiningStopReason.MIXED_SOURCE),
        (_epoch(session_id="other"), MiningStopReason.MIXED_SESSION),
        (_epoch(cycle_id="other"), MiningStopReason.MIXED_CYCLE),
        (
            _epoch(frame_id=11, cycle_id="cycle-10", payload=_digest("0")),
            MiningStopReason.MIXED_FRAME,
        ),
        (_epoch(payload=_digest("9")), MiningStopReason.MIXED_PAYLOAD),
        (_epoch(captured=10.1), MiningStopReason.MIXED_FRAME),
    ),
)
def test_same_frame_cycle_source_session_and_payload_are_atomic(
    inventory_epoch: PerceptionEpoch,
    expected: MiningStopReason,
) -> None:
    result = _assemble(inventory_epoch=inventory_epoch)
    assert result.observation is None
    assert result.reason is expected


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    (
        ({"now": float("nan")}, MiningStopReason.INVALID_CURRENT_TIME),
        ({"now": 9.9}, MiningStopReason.FUTURE_EVIDENCE),
        ({"now": 11.01}, MiningStopReason.STALE_EVIDENCE),
        ({"supported": None}, MiningStopReason.RESOURCE_UNKNOWN),
        ({"supported": False}, MiningStopReason.UNSUPPORTED_RESOURCE_VIEW),
        ({"resources": ()}, MiningStopReason.RESOURCE_UNKNOWN),
        (
            {"resources": _resources(first_available=None)},
            MiningStopReason.RESOURCE_UNKNOWN,
        ),
        ({"occupied": None}, MiningStopReason.INVENTORY_UNKNOWN),
        (
            {"confidence": 0.799999},
            MiningStopReason.INVENTORY_CONFIDENCE_BELOW_FLOOR,
        ),
        ({"capacity": 27}, MiningStopReason.INVENTORY_CAPACITY_MISMATCH),
    ),
)
def test_assembly_fails_closed_without_publishing_partial_state(
    kwargs: dict[str, object],
    expected: MiningStopReason,
) -> None:
    result = _assemble(**kwargs)  # type: ignore[arg-type]
    assert result.observation is None
    assert result.reason is expected


def test_publication_floor_capacity_and_synthetic_nonauthority_are_preserved() -> None:
    observation = _observation(confidence=0.8, capacity=28)
    assert observation.inventory.capacity == 28
    assert observation.inventory.confidence == 0.8
    assert observation.input_authority is False
    assert observation.role is EvidenceRole.SYNTHETIC_ARCHITECTURE_TEST_ONLY


def test_full_inventory_finishes_mining_only_without_navigation() -> None:
    observation = _observation(occupied=28)
    session = MiningOnlySession.start(observation)
    assert session.phase is MiningOnlyPhase.COMPLETE_FULL
    assert session.stop_reason is MiningStopReason.NONE
    assert session.pending_proposal is None


def test_plan_selects_first_valid_available_iron_in_source_order() -> None:
    observation = _observation()
    session = MiningOnlySession.start(observation).plan(
        attempt_id="attempt-1",
        reviewed_execution_sha=_git("e"),
    )
    assert session.phase is MiningOnlyPhase.AWAITING_ATTEMPT_RECEIPT
    assert session.pending_proposal is not None
    assert session.pending_proposal.target.resource_id == "iron-northwest"
    assert session.pending_proposal.input_authority is False


def test_plan_does_not_confidence_rerank_or_select_decoy_resource() -> None:
    resources = (
        ResourceState("iron-first", "iron", True, 0.81, (1, 1, 10, 10)),
        ResourceState("copper-high", "copper", True, 1.0, (2, 2, 10, 10)),
        ResourceState("iron-second", "iron", True, 1.0, (3, 3, 10, 10)),
    )
    session = MiningOnlySession.start(_observation(resources=resources)).plan(
        attempt_id="attempt-order",
        reviewed_execution_sha=_git("e"),
    )
    assert session.pending_proposal is not None
    assert session.pending_proposal.target.resource_id == "iron-first"


def test_no_available_iron_stops_without_retry_or_hidden_camera_action() -> None:
    resources = _resources(first_available=False, second_available=False)
    session = MiningOnlySession.start(_observation(resources=resources)).plan(
        attempt_id="attempt-none",
        reviewed_execution_sha=_git("e"),
    )
    assert session.phase is MiningOnlyPhase.STOPPED
    assert session.stop_reason is MiningStopReason.NO_AVAILABLE_IRON
    assert session.pending_proposal is None


def test_live_executor_rejects_synthetic_proposal_before_callback() -> None:
    proposal = _proposal()
    callback_count = 0

    def click_once(_region: tuple[int, int, int, int]) -> bool:
        nonlocal callback_count
        callback_count += 1
        return True

    with pytest.raises(ValueError, match=MiningStopReason.INPUT_AUTHORITY_MISSING.value):
        OneAttemptExecutor().dispatch(
            proposal,
            object(),  # type: ignore[arg-type]
            click_once,
            now_monotonic_s=10.6,
            receipt_sha256=_digest("f"),
        )
    assert callback_count == 0


def test_attempt_receipt_never_claims_success_from_dispatch() -> None:
    receipt = _receipt(_proposal())
    assert receipt.dispatch_count == 1
    assert receipt.dispatch_accepted is True
    assert receipt.success_observed is False


@pytest.mark.parametrize(
    ("newer_kwargs", "expected"),
    (
        (
            {"occupied": 6, "first_available": False},
            AttemptProgress.RESOURCE_DEPLETED_AND_INVENTORY_INCREMENTED,
        ),
        ({"occupied": 5, "first_available": False}, AttemptProgress.RESOURCE_DEPLETED),
        ({"occupied": 6, "first_available": True}, AttemptProgress.INVENTORY_INCREMENTED),
    ),
)
def test_progress_requires_observed_depletion_and_or_inventory_plus_one(
    newer_kwargs: dict[str, object],
    expected: AttemptProgress,
) -> None:
    proposal = _proposal()
    newer = _newer(proposal, **newer_kwargs)  # type: ignore[arg-type]
    result = verify_attempt_progress(proposal, _receipt(proposal), newer)
    assert result.progress is expected
    assert result.reason is MiningStopReason.NONE


@pytest.mark.parametrize(
    ("newer_kwargs", "expected"),
    (
        ({"frame_id": 10}, MiningStopReason.NEWER_OBSERVATION_REQUIRED),
        ({"captured": 10.6}, MiningStopReason.NEWER_OBSERVATION_REQUIRED),
        ({"source_id": "other"}, MiningStopReason.PROVENANCE_CHANGED),
        ({"session_id": "other"}, MiningStopReason.PROVENANCE_CHANGED),
        ({"occupied": 4}, MiningStopReason.INVENTORY_REGRESSED),
        ({"occupied": 7}, MiningStopReason.AMBIGUOUS_CAUSALITY),
        (
            {"occupied": 5, "first_available": True},
            MiningStopReason.NO_OBSERVED_PROGRESS,
        ),
    ),
)
def test_uncertain_or_ambiguous_reobservation_stops(
    newer_kwargs: dict[str, object],
    expected: MiningStopReason,
) -> None:
    proposal = _proposal()
    newer = _newer(proposal, **newer_kwargs)  # type: ignore[arg-type]
    result = verify_attempt_progress(proposal, _receipt(proposal), newer)
    assert result.progress is None
    assert result.reason is expected



def test_failed_atomic_reobservation_clears_state_and_stops() -> None:
    planned = MiningOnlySession.start(_observation()).plan(
        attempt_id="attempt-atomic-stop",
        reviewed_execution_sha=_git("e"),
    )
    assert planned.pending_proposal is not None
    waiting = planned.accept_attempt_receipt(_receipt(planned.pending_proposal))
    failed = _assemble(
        epoch=_epoch(11, 11.0),
        resources=_resources(first_available=None),
        now=11.1,
        receipts=(
            planned.pending_proposal.source_observation.resource_receipt,
            planned.pending_proposal.source_observation.inventory_receipt,
        ),
    )
    assert failed.observation is None
    stopped = waiting.reobserve(failed)
    assert stopped.phase is MiningOnlyPhase.STOPPED
    assert stopped.observation is None
    assert stopped.stop_reason is MiningStopReason.RESOURCE_UNKNOWN

def test_replayed_or_foreign_attempt_receipt_is_rejected() -> None:
    proposal = _proposal()
    receipt = replace(_receipt(proposal), attempt_id="foreign")
    result = verify_attempt_progress(proposal, receipt, _newer(proposal))
    assert result.progress is None
    assert result.reason is MiningStopReason.ATTEMPT_RECEIPT_INVALID


def test_session_repeats_only_after_verified_progress_and_stops_at_full() -> None:
    first = _observation(occupied=26)
    planned = MiningOnlySession.start(first).plan(
        attempt_id="attempt-1",
        reviewed_execution_sha=_git("e"),
    )
    assert planned.pending_proposal is not None
    proposal_1 = planned.pending_proposal
    waiting = planned.accept_attempt_receipt(_receipt(proposal_1))
    assert waiting.phase is MiningOnlyPhase.AWAITING_NEWER_OBSERVATION
    second = _newer(proposal_1, occupied=27)
    ready = waiting.reobserve(AssemblyResult(second, MiningStopReason.NONE))
    assert ready.phase is MiningOnlyPhase.READY
    assert ready.attempt_count == 1

    planned_2 = ready.plan(attempt_id="attempt-2", reviewed_execution_sha=_git("e"))
    assert planned_2.pending_proposal is not None
    proposal_2 = planned_2.pending_proposal
    waiting_2 = planned_2.accept_attempt_receipt(
        MiningAttemptReceipt(
            attempt_id=proposal_2.attempt_id,
            attempt_ordinal=proposal_2.attempt_ordinal,
            source_epoch=proposal_2.source_observation.epoch,
            target_id=proposal_2.target.resource_id,
            target_region=proposal_2.target.interaction_region,  # type: ignore[arg-type]
            reviewed_execution_sha=proposal_2.reviewed_execution_sha,
            dispatched_monotonic_s=11.1,
            dispatch_count=1,
            dispatch_accepted=True,
            role=proposal_2.source_observation.role,
            receipt_sha256=_digest("7"),
        )
    )
    full = _observation(
        epoch=_epoch(12, 12.0),
        resources=_resources(first_available=False),
        occupied=28,
        now=12.1,
        receipts=(second.resource_receipt, second.inventory_receipt),
    )
    complete = waiting_2.reobserve(AssemblyResult(full, MiningStopReason.NONE))
    assert complete.phase is MiningOnlyPhase.COMPLETE_FULL
    assert complete.attempt_count == 2
    assert complete.stop_reason is MiningStopReason.NONE


def test_session_stops_permanently_when_progress_is_not_observed() -> None:
    planned = MiningOnlySession.start(_observation()).plan(
        attempt_id="attempt-1",
        reviewed_execution_sha=_git("e"),
    )
    assert planned.pending_proposal is not None
    proposal = planned.pending_proposal
    waiting = planned.accept_attempt_receipt(_receipt(proposal))
    stopped = waiting.reobserve(
        AssemblyResult(
            _newer(proposal, occupied=5, first_available=True),
            MiningStopReason.NONE,
        )
    )
    assert stopped.phase is MiningOnlyPhase.STOPPED
    assert stopped.stop_reason is MiningStopReason.NO_OBSERVED_PROGRESS
    again = stopped.plan(attempt_id="retry", reviewed_execution_sha=_git("e"))
    assert again.phase is MiningOnlyPhase.STOPPED
    assert again.stop_reason is MiningStopReason.ATTEMPT_RECEIPT_INVALID


def test_attempt_ceiling_is_literal_28_and_fails_closed() -> None:
    observation = _observation()
    session = MiningOnlySession(
        MiningOnlyPhase.READY,
        observation,
        28,
    ).plan(attempt_id="attempt-29", reviewed_execution_sha=_git("e"))
    assert session.phase is MiningOnlyPhase.STOPPED
    assert session.stop_reason is MiningStopReason.ATTEMPT_LIMIT_REACHED


def test_mutated_fixed_authority_fields_do_not_create_live_input() -> None:
    observation = _observation()
    object.__setattr__(observation, "input_authority", True)
    proposal = _proposal(observation)
    assert proposal.input_authority is True
    with pytest.raises(ValueError, match=MiningStopReason.INPUT_AUTHORITY_MISSING.value):
        OneAttemptExecutor().dispatch(
            proposal,
            object(),  # type: ignore[arg-type]
            lambda _region: True,
            now_monotonic_s=10.6,
            receipt_sha256=_digest("f"),
        )


def test_assembly_result_shape_rejects_partial_success_claims() -> None:
    with pytest.raises(ValueError, match="successful assembly"):
        AssemblyResult(None, MiningStopReason.NONE)
    with pytest.raises(ValueError, match="successful assembly"):
        AssemblyResult(_observation(), MiningStopReason.RESOURCE_UNKNOWN)
