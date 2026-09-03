from __future__ import annotations

from hashlib import sha256

from mining_automation.contracts import InventoryState, ResourceState
from mining_automation.mining_slice import (
    INVENTORY_CAPACITY,
    AtomicMiningWorldState,
    InventoryPerceptionEnvelope,
    MiningAttemptDispatchReceipt,
    MiningOnlyPhase,
    MiningOnlyStopReason,
    PerceptionEpoch,
    PerceptionReleaseIdentity,
    ResourcePerceptionEnvelope,
    ResourceViewState,
    assemble_atomic_mining_world_state,
    begin_mining_only_session,
    record_mining_attempt_dispatch,
    reobserve_mining_attempt,
)


def _sha(label: str) -> str:
    return sha256(label.encode("ascii")).hexdigest()


_RESOURCE_RELEASE = PerceptionReleaseIdentity(
    release_role="released-resource-perception",
    receipt_id="receipt:resource",
    release_record_sha256=_sha("resource-release"),
    reviewed_source_sha="a" * 40,
    producer_id="profiled-resource:varrock-east-iron-v1",
    producer_version="1.0.0",
)
_INVENTORY_RELEASE = PerceptionReleaseIdentity(
    release_role="released-inventory-perception",
    receipt_id="receipt:inventory",
    release_record_sha256=_sha("inventory-release"),
    reviewed_source_sha="b" * 40,
    producer_id="inventory-positive-v3",
    producer_version="1.0.0",
)


def _epoch(sequence: int) -> PerceptionEpoch:
    return PerceptionEpoch(
        capture_source_id="windows-runelite",
        capture_session_id="synthetic-endurance-session",
        cycle_id=f"cycle-{sequence}",
        cycle_sequence=sequence,
        frame_id=sequence,
        captured_monotonic_s=float(sequence),
        frame_width=1005,
        frame_height=1078,
        frame_payload_sha256=_sha(f"frame-{sequence}"),
    )


def _state(sequence: int, occupied: int) -> AtomicMiningWorldState:
    epoch = _epoch(sequence)
    resources = (
        ResourceState("iron-west", "iron", True, 0.91, (10, 20, 30, 40)),
        ResourceState("iron-center", "iron", True, 0.99, (50, 60, 30, 40)),
        ResourceState("iron-east", "iron", False, 0.95, None),
    )
    return assemble_atomic_mining_world_state(
        resource=ResourcePerceptionEnvelope(
            epoch=epoch,
            release=_RESOURCE_RELEASE,
            view=ResourceViewState.SUPPORTED,
            resources=resources,
        ),
        inventory=InventoryPerceptionEnvelope(
            epoch=epoch,
            release=_INVENTORY_RELEASE,
            inventory=InventoryState(
                occupied_slots=occupied,
                capacity=INVENTORY_CAPACITY,
                confidence=0.95,
            ),
            unknown_reason=None,
        ),
        evaluated_monotonic_s=epoch.captured_monotonic_s + 0.1,
    )


def _receipt(proposal) -> MiningAttemptDispatchReceipt:
    return MiningAttemptDispatchReceipt(
        attempt_id=proposal.attempt_id,
        attempt_sequence=proposal.attempt_sequence,
        target_id=proposal.target_id,
        target_region=proposal.target_region,
        source_cycle_id=proposal.source_epoch.cycle_id,
        source_frame_id=proposal.source_epoch.frame_id,
        source_frame_payload_sha256=proposal.source_epoch.frame_payload_sha256,
        dispatcher_id="synthetic-reviewed-boundary",
        dispatcher_version="1.0.0",
        dispatch_id=f"dispatch-{proposal.attempt_sequence}",
        dispatched_monotonic_s=proposal.created_monotonic_s + 0.01,
        click_dispatch_count=1,
        dispatch_succeeded=True,
    )


def test_synthetic_zero_to_full_transcript_uses_exactly_28_single_attempts() -> None:
    started = begin_mining_only_session(
        session_id="mining-endurance-0-to-28",
        state=_state(1, 0),
        now_monotonic_s=1.1,
    )
    session = started.session
    proposal = started.proposal
    assert proposal is not None

    for occupied_after in range(1, INVENTORY_CAPACITY + 1):
        assert session.phase is MiningOnlyPhase.READY
        assert proposal.attempt_sequence == occupied_after
        assert proposal.target_id == "iron-west"

        attempted = record_mining_attempt_dispatch(
            session,
            proposal,
            _receipt(proposal),
        )
        assert attempted.session.phase is MiningOnlyPhase.AWAITING_NEWER_OBSERVATION
        decision = reobserve_mining_attempt(
            attempted.session,
            _state(occupied_after + 1, occupied_after),
            now_monotonic_s=float(occupied_after + 1) + 0.1,
        )
        session = decision.session

        if occupied_after < INVENTORY_CAPACITY:
            assert session.phase is MiningOnlyPhase.READY
            assert decision.proposal is not None
            proposal = decision.proposal
        else:
            assert session.phase is MiningOnlyPhase.COMPLETE
            assert decision.proposal is None
            assert decision.stop_reason is MiningOnlyStopReason.INVENTORY_FULL

    assert session.next_attempt_sequence == 29
    assert len(session.spent_attempt_ids) == INVENTORY_CAPACITY
    assert len(set(session.spent_attempt_ids)) == INVENTORY_CAPACITY
    assert len(session.spent_dispatch_ids) == INVENTORY_CAPACITY
    assert len(set(session.spent_dispatch_ids)) == INVENTORY_CAPACITY
    assert session.current_state.inventory.occupied_slots == INVENTORY_CAPACITY
    assert session.input_authority is False
    assert session.navigation_authority is False
    assert session.banking_authority is False


def test_midrun_no_progress_stops_without_retry_or_reused_ordinal() -> None:
    started = begin_mining_only_session(
        session_id="mining-endurance-stop",
        state=_state(1, 0),
        now_monotonic_s=1.1,
    )
    session = started.session
    proposal = started.proposal
    assert proposal is not None

    for occupied_after in range(1, 6):
        attempted = record_mining_attempt_dispatch(session, proposal, _receipt(proposal))
        advanced = reobserve_mining_attempt(
            attempted.session,
            _state(occupied_after + 1, occupied_after),
            now_monotonic_s=float(occupied_after + 1) + 0.1,
        )
        assert advanced.proposal is not None
        session = advanced.session
        proposal = advanced.proposal

    assert proposal.attempt_sequence == 6
    attempted = record_mining_attempt_dispatch(session, proposal, _receipt(proposal))
    stopped = reobserve_mining_attempt(
        attempted.session,
        _state(7, 5),
        now_monotonic_s=7.1,
    )

    assert stopped.session.phase is MiningOnlyPhase.STOPPED
    assert stopped.stop_reason is MiningOnlyStopReason.NO_OBSERVED_PROGRESS
    assert stopped.proposal is None
    assert stopped.session.next_attempt_sequence == 6
    assert len(stopped.session.spent_attempt_ids) == 6
    assert len(stopped.session.spent_dispatch_ids) == 6
    assert stopped.session.input_authority is False
    assert stopped.session.navigation_authority is False
    assert stopped.session.banking_authority is False
