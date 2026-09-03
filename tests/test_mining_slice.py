from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from mining_automation.contracts import InventoryState, ResourceState
from mining_automation.mining_slice import (
    INVENTORY_CAPACITY,
    INVENTORY_PUBLICATION_FLOOR,
    AtomicMiningWorldState,
    InventoryPerceptionEnvelope,
    MiningAttemptDispatchReceipt,
    MiningOnlyPhase,
    MiningOnlyStopReason,
    MiningProgressKind,
    PerceptionEpoch,
    PerceptionReleaseIdentity,
    ResourcePerceptionEnvelope,
    ResourceViewState,
    WorldStatePublicationStatus,
    assemble_atomic_mining_world_state,
    begin_mining_only_session,
    record_mining_attempt_dispatch,
    reobserve_mining_attempt,
)


def _sha(label: str) -> str:
    import hashlib

    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _epoch(
    sequence: int = 1,
    *,
    frame_id: int | None = None,
    captured: float | None = None,
    session: str = "capture-session-a",
    payload: str | None = None,
) -> PerceptionEpoch:
    return PerceptionEpoch(
        capture_source_id="windows-runelite",
        capture_session_id=session,
        cycle_id=f"cycle-{sequence}",
        cycle_sequence=sequence,
        frame_id=sequence if frame_id is None else frame_id,
        captured_monotonic_s=float(sequence) if captured is None else captured,
        frame_width=1005,
        frame_height=1078,
        frame_payload_sha256=_sha(payload or f"frame-{sequence}"),
    )


def _release(role: str) -> PerceptionReleaseIdentity:
    return PerceptionReleaseIdentity(
        release_role=role,
        receipt_id=f"receipt:{role}",
        release_record_sha256=_sha(f"record:{role}"),
        reviewed_source_sha=("a" if role.startswith("released-resource") else "b") * 40,
        producer_id=(
            "profiled-resource:varrock-east-iron-v1"
            if role.startswith("released-resource")
            else "inventory-positive-v3"
        ),
        producer_version="1.0.0",
    )


RESOURCE_RELEASE = _release("released-resource-perception")
INVENTORY_RELEASE = _release("released-inventory-perception")


def _resources(
    *,
    target_available: bool | None = True,
    second_available: bool | None = True,
) -> tuple[ResourceState, ...]:
    return (
        ResourceState(
            "iron-west",
            "iron",
            target_available,
            0.91,
            (10, 20, 30, 40) if target_available is True else None,
        ),
        ResourceState(
            "iron-center",
            "iron",
            second_available,
            0.99,
            (50, 60, 30, 40) if second_available is True else None,
        ),
        ResourceState("iron-east", "iron", False, 0.95, None),
    )


def _envelopes(
    epoch: PerceptionEpoch | None = None,
    *,
    occupied: int | None = 5,
    confidence: float = 0.95,
    view: ResourceViewState = ResourceViewState.SUPPORTED,
    resources: tuple[ResourceState, ...] | None = None,
    inventory_epoch: PerceptionEpoch | None = None,
    resource_release: PerceptionReleaseIdentity = RESOURCE_RELEASE,
    inventory_release: PerceptionReleaseIdentity = INVENTORY_RELEASE,
) -> tuple[ResourcePerceptionEnvelope, InventoryPerceptionEnvelope]:
    owned = epoch or _epoch()
    return (
        ResourcePerceptionEnvelope(
            epoch=owned,
            release=resource_release,
            view=view,
            resources=_resources() if resources is None else resources,
        ),
        InventoryPerceptionEnvelope(
            epoch=inventory_epoch or owned,
            release=inventory_release,
            inventory=InventoryState(occupied, capacity=INVENTORY_CAPACITY, confidence=confidence),
            unknown_reason="detector-unknown" if occupied is None else None,
        ),
    )


def _state(
    epoch: PerceptionEpoch | None = None,
    *,
    occupied: int | None = 5,
    confidence: float = 0.95,
    view: ResourceViewState = ResourceViewState.SUPPORTED,
    resources: tuple[ResourceState, ...] | None = None,
    now: float | None = None,
    inventory_epoch: PerceptionEpoch | None = None,
    resource_release: PerceptionReleaseIdentity = RESOURCE_RELEASE,
    inventory_release: PerceptionReleaseIdentity = INVENTORY_RELEASE,
) -> AtomicMiningWorldState:
    owned = epoch or _epoch()
    resource, inventory = _envelopes(
        owned,
        occupied=occupied,
        confidence=confidence,
        view=view,
        resources=resources,
        inventory_epoch=inventory_epoch,
        resource_release=resource_release,
        inventory_release=inventory_release,
    )
    return assemble_atomic_mining_world_state(
        resource=resource,
        inventory=inventory,
        evaluated_monotonic_s=owned.captured_monotonic_s + 0.25 if now is None else now,
    )


def _assert_atomic_blocked(state: AtomicMiningWorldState, reason: MiningOnlyStopReason) -> None:
    assert state.status is WorldStatePublicationStatus.BLOCKED
    assert state.stop_reason is reason
    assert state.epoch is None
    assert state.resource_release is None
    assert state.inventory_release is None
    assert state.resources == ()
    assert state.inventory == InventoryState(None, 28, 0.0)
    assert state.selected_target is None
    assert state.input_authority is False
    assert state.navigation_authority is False
    assert state.banking_authority is False


def test_atomic_same_epoch_publication_selects_first_valid_source_order_not_confidence() -> None:
    state = _state()

    assert state.status is WorldStatePublicationStatus.READY
    assert state.stop_reason is MiningOnlyStopReason.NONE
    assert state.epoch == _epoch()
    assert state.resource_release is RESOURCE_RELEASE
    assert state.inventory_release is INVENTORY_RELEASE
    assert state.inventory == InventoryState(5, 28, 0.95)
    assert state.selected_target == _resources()[0]
    assert state.selected_target.confidence < state.resources[1].confidence
    assert state.blockers == ()
    assert state.input_authority is False


@pytest.mark.parametrize(
    ("case", "expected"),
    (
        ("resource-type", MiningOnlyStopReason.PUBLICATION_BLOCKED),
        ("inventory-type", MiningOnlyStopReason.PUBLICATION_BLOCKED),
        ("mixed-frame", MiningOnlyStopReason.MIXED_PERCEPTION_EPOCH),
        ("unsupported", MiningOnlyStopReason.RESOURCE_VIEW_NOT_SUPPORTED),
        ("unknown-view", MiningOnlyStopReason.RESOURCE_VIEW_NOT_SUPPORTED),
        ("resource-unknown", MiningOnlyStopReason.RESOURCE_UNKNOWN),
        ("inventory-unknown", MiningOnlyStopReason.INVENTORY_UNKNOWN),
        ("inventory-floor", MiningOnlyStopReason.INVENTORY_CONFIDENCE_BELOW_FLOOR),
        ("stale", MiningOnlyStopReason.STALE_PERCEPTION),
        ("future", MiningOnlyStopReason.STALE_PERCEPTION),
        ("no-target", MiningOnlyStopReason.NO_AVAILABLE_IRON),
    ),
)
def test_publication_failures_clear_both_perceptions_atomically(
    case: str,
    expected: MiningOnlyStopReason,
) -> None:
    epoch = _epoch()
    resource, inventory = _envelopes(epoch)
    now = 1.25
    resource_input: object = resource
    inventory_input: object = inventory
    if case == "resource-type":
        resource_input = object()
    elif case == "inventory-type":
        inventory_input = object()
    elif case == "mixed-frame":
        inventory_input = replace(inventory, epoch=_epoch(2))
    elif case == "unsupported":
        resource_input = replace(resource, view=ResourceViewState.UNSUPPORTED)
    elif case == "unknown-view":
        resource_input = replace(resource, view=ResourceViewState.UNKNOWN)
    elif case == "resource-unknown":
        resource_input = replace(resource, resources=_resources(target_available=None))
    elif case == "inventory-unknown":
        inventory_input = InventoryPerceptionEnvelope(
            epoch=epoch,
            release=INVENTORY_RELEASE,
            inventory=InventoryState(None, 28, 0.0),
            unknown_reason="wrong-tab",
        )
    elif case == "inventory-floor":
        inventory_input = replace(
            inventory,
            inventory=InventoryState(5, 28, INVENTORY_PUBLICATION_FLOOR - 0.01),
        )
    elif case == "stale":
        now = epoch.captured_monotonic_s + 1.01
    elif case == "future":
        now = epoch.captured_monotonic_s - 0.01
    elif case == "no-target":
        resource_input = replace(
            resource, resources=_resources(target_available=False, second_available=False)
        )

    result = assemble_atomic_mining_world_state(
        resource=resource_input,
        inventory=inventory_input,
        evaluated_monotonic_s=now,
    )
    _assert_atomic_blocked(result, expected)


def test_full_inventory_is_mining_only_completion_and_never_navigation() -> None:
    state = _state(occupied=28)
    assert state.status is WorldStatePublicationStatus.FULL
    assert state.stop_reason is MiningOnlyStopReason.INVENTORY_FULL
    assert state.selected_target is None
    assert state.navigation_authority is False

    decision = begin_mining_only_session(
        session_id="mining-session-a",
        state=state,
        now_monotonic_s=1.25,
    )
    assert decision.session.phase is MiningOnlyPhase.COMPLETE
    assert decision.proposal is None
    assert decision.stop_reason is MiningOnlyStopReason.INVENTORY_FULL
    assert decision.session.navigation_authority is False


def _receipt(proposal, *, dispatch_id: str = "dispatch-1", count: int = 1, success: bool = True):
    return MiningAttemptDispatchReceipt(
        attempt_id=proposal.attempt_id,
        attempt_sequence=proposal.attempt_sequence,
        target_id=proposal.target_id,
        target_region=proposal.target_region,
        source_cycle_id=proposal.source_epoch.cycle_id,
        source_frame_id=proposal.source_epoch.frame_id,
        source_frame_payload_sha256=proposal.source_epoch.frame_payload_sha256,
        dispatcher_id="reviewed-mining-click-boundary",
        dispatcher_version="1.0.0",
        dispatch_id=dispatch_id,
        dispatched_monotonic_s=proposal.created_monotonic_s + 0.1,
        click_dispatch_count=count,
        dispatch_succeeded=success,
    )


def _begin():
    state = _state()
    decision = begin_mining_only_session(
        session_id="mining-session-a",
        state=state,
        now_monotonic_s=1.25,
    )
    assert decision.proposal is not None
    return decision.session, decision.proposal


def test_dispatch_receipt_is_only_an_attempt_and_requires_newer_observation() -> None:
    session, proposal = _begin()
    decision = record_mining_attempt_dispatch(session, proposal, _receipt(proposal))

    assert decision.session.phase is MiningOnlyPhase.AWAITING_NEWER_OBSERVATION
    assert decision.progress is MiningProgressKind.NONE
    assert decision.stop_reason is MiningOnlyStopReason.NONE
    assert decision.proposal is None
    assert decision.session.pending_receipt is not None
    assert decision.session.input_authority is False


@pytest.mark.parametrize(
    "mutation",
    (
        "proposal-target",
        "receipt-attempt",
        "receipt-target",
        "receipt-region",
        "receipt-cycle",
        "receipt-frame",
        "receipt-payload",
        "receipt-before-proposal",
        "no-click",
    ),
)
def test_wrong_or_zero_click_receipt_stops_without_retry(mutation: str) -> None:
    session, proposal = _begin()
    receipt = _receipt(proposal)
    if mutation == "proposal-target":
        proposal = replace(proposal, target_id="iron-center")
    elif mutation == "receipt-attempt":
        receipt = replace(receipt, attempt_id="foreign-attempt")
    elif mutation == "receipt-target":
        receipt = replace(receipt, target_id="iron-center")
    elif mutation == "receipt-region":
        receipt = replace(receipt, target_region=(99, 99, 1, 1))
    elif mutation == "receipt-cycle":
        receipt = replace(receipt, source_cycle_id="cycle-foreign")
    elif mutation == "receipt-frame":
        receipt = replace(receipt, source_frame_id=999)
    elif mutation == "receipt-payload":
        receipt = replace(receipt, source_frame_payload_sha256=_sha("foreign"))
    elif mutation == "receipt-before-proposal":
        receipt = replace(receipt, dispatched_monotonic_s=proposal.created_monotonic_s - 0.01)
    elif mutation == "no-click":
        receipt = replace(receipt, click_dispatch_count=0, dispatch_succeeded=False)

    decision = record_mining_attempt_dispatch(session, proposal, receipt)
    assert decision.session.phase is MiningOnlyPhase.STOPPED
    assert decision.stop_reason is MiningOnlyStopReason.ATTEMPT_RECEIPT_INVALID
    assert decision.proposal is None


def test_receipt_constructor_rejects_more_than_one_click() -> None:
    _, proposal = _begin()
    with pytest.raises(ValueError, match="click_dispatch_count"):
        _receipt(proposal, count=2)


def test_strictly_newer_depletion_and_inventory_plus_one_continue_with_next_attempt() -> None:
    session, proposal = _begin()
    attempted = record_mining_attempt_dispatch(session, proposal, _receipt(proposal)).session
    next_epoch = _epoch(2, captured=1.5)
    next_state = _state(
        next_epoch,
        occupied=6,
        resources=_resources(target_available=False, second_available=True),
        now=1.6,
    )

    decision = reobserve_mining_attempt(attempted, next_state, now_monotonic_s=1.6)

    assert decision.session.phase is MiningOnlyPhase.READY
    assert decision.progress is MiningProgressKind.RESOURCE_DEPLETED_AND_INVENTORY_INCREMENTED
    assert decision.stop_reason is MiningOnlyStopReason.NONE
    assert decision.proposal is not None
    assert decision.proposal.attempt_sequence == 2
    assert decision.proposal.target_id == "iron-center"
    assert decision.session.spent_attempt_ids == (proposal.attempt_id,)
    assert decision.session.spent_dispatch_ids == ("dispatch-1",)


@pytest.mark.parametrize(
    ("occupied", "resources", "expected"),
    (
        (
            5,
            _resources(target_available=False, second_available=True),
            MiningProgressKind.RESOURCE_DEPLETED,
        ),
        (
            6,
            _resources(target_available=True, second_available=True),
            MiningProgressKind.INVENTORY_INCREMENTED,
        ),
    ),
)
def test_either_independent_observed_progress_signal_can_continue(
    occupied: int,
    resources: tuple[ResourceState, ...],
    expected: MiningProgressKind,
) -> None:
    session, proposal = _begin()
    attempted = record_mining_attempt_dispatch(session, proposal, _receipt(proposal)).session
    next_state = _state(_epoch(2, captured=1.5), occupied=occupied, resources=resources, now=1.6)
    decision = reobserve_mining_attempt(attempted, next_state, now_monotonic_s=1.6)
    assert decision.session.phase is MiningOnlyPhase.READY
    assert decision.progress is expected


@pytest.mark.parametrize(
    ("epoch", "occupied", "resources", "expected"),
    (
        (
            _epoch(1),
            6,
            _resources(target_available=False, second_available=True),
            MiningOnlyStopReason.NEWER_OBSERVATION_REQUIRED,
        ),
        (
            _epoch(2, frame_id=1, captured=1.5),
            6,
            _resources(target_available=False, second_available=True),
            MiningOnlyStopReason.NEWER_OBSERVATION_REQUIRED,
        ),
        (
            _epoch(2, captured=1.5, payload="frame-1"),
            6,
            _resources(target_available=False, second_available=True),
            MiningOnlyStopReason.NEWER_OBSERVATION_REQUIRED,
        ),
        (
            _epoch(2, captured=1.5, session="foreign-session"),
            6,
            _resources(target_available=False, second_available=True),
            MiningOnlyStopReason.NEWER_OBSERVATION_REQUIRED,
        ),
        (
            _epoch(2, captured=1.3),
            6,
            _resources(target_available=False, second_available=True),
            MiningOnlyStopReason.NEWER_OBSERVATION_REQUIRED,
        ),
        (
            _epoch(2, captured=1.5),
            5,
            _resources(target_available=True, second_available=True),
            MiningOnlyStopReason.NO_OBSERVED_PROGRESS,
        ),
        (
            _epoch(2, captured=1.5),
            7,
            _resources(target_available=False, second_available=True),
            MiningOnlyStopReason.AMBIGUOUS_PROGRESS,
        ),
        (
            _epoch(2, captured=1.5),
            4,
            _resources(target_available=False, second_available=True),
            MiningOnlyStopReason.AMBIGUOUS_PROGRESS,
        ),
    ),
)
def test_reobservation_uncertainty_stops_absorbingly(
    epoch: PerceptionEpoch,
    occupied: int,
    resources: tuple[ResourceState, ...],
    expected: MiningOnlyStopReason,
) -> None:
    session, proposal = _begin()
    attempted = record_mining_attempt_dispatch(session, proposal, _receipt(proposal)).session
    newer = _state(
        epoch, occupied=occupied, resources=resources, now=max(1.6, epoch.captured_monotonic_s)
    )
    decision = reobserve_mining_attempt(
        attempted, newer, now_monotonic_s=max(1.6, epoch.captured_monotonic_s)
    )
    assert decision.session.phase is MiningOnlyPhase.STOPPED
    assert decision.stop_reason is expected
    assert decision.proposal is None


def test_unknown_newer_observation_stops_with_atomic_clearing() -> None:
    session, proposal = _begin()
    attempted = record_mining_attempt_dispatch(session, proposal, _receipt(proposal)).session
    blocked = _state(_epoch(2, captured=1.5), occupied=None, confidence=0.0, now=1.6)
    decision = reobserve_mining_attempt(attempted, blocked, now_monotonic_s=1.6)
    assert decision.session.phase is MiningOnlyPhase.STOPPED
    assert decision.stop_reason is MiningOnlyStopReason.INVENTORY_UNKNOWN
    _assert_atomic_blocked(decision.session.current_state, MiningOnlyStopReason.INVENTORY_UNKNOWN)


def test_release_lineage_change_after_attempt_stops() -> None:
    session, proposal = _begin()
    attempted = record_mining_attempt_dispatch(session, proposal, _receipt(proposal)).session
    changed = replace(RESOURCE_RELEASE, release_record_sha256=_sha("changed-resource-release"))
    newer = _state(
        _epoch(2, captured=1.5),
        occupied=6,
        resources=_resources(target_available=False, second_available=True),
        now=1.6,
        resource_release=changed,
    )
    decision = reobserve_mining_attempt(attempted, newer, now_monotonic_s=1.6)
    assert decision.session.phase is MiningOnlyPhase.STOPPED
    assert decision.stop_reason is MiningOnlyStopReason.PERCEPTION_LINEAGE_CHANGED


def test_full_after_verified_progress_completes_without_banking_transition() -> None:
    initial = _state(occupied=27)
    started = begin_mining_only_session(
        session_id="mining-session-full",
        state=initial,
        now_monotonic_s=1.25,
    )
    assert started.proposal is not None
    attempted = record_mining_attempt_dispatch(
        started.session,
        started.proposal,
        _receipt(started.proposal),
    ).session
    full = _state(
        _epoch(2, captured=1.5),
        occupied=28,
        resources=_resources(target_available=False, second_available=True),
        now=1.6,
    )
    decision = reobserve_mining_attempt(attempted, full, now_monotonic_s=1.6)
    assert decision.session.phase is MiningOnlyPhase.COMPLETE
    assert decision.progress is MiningProgressKind.RESOURCE_DEPLETED_AND_INVENTORY_INCREMENTED
    assert decision.stop_reason is MiningOnlyStopReason.INVENTORY_FULL
    assert decision.proposal is None
    assert decision.session.navigation_authority is False
    assert decision.session.banking_authority is False


def test_dispatch_replay_is_rejected_and_spent_receipts_are_retained() -> None:
    session, proposal = _begin()
    receipt = _receipt(proposal)
    attempted = record_mining_attempt_dispatch(session, proposal, receipt).session
    # A caller cannot use the already-consumed proposal/receipt against a READY projection.
    forged_ready = replace(
        attempted,
        phase=MiningOnlyPhase.READY,
        pending_proposal=None,
        pending_receipt=None,
        stop_reason=MiningOnlyStopReason.NONE,
    )
    decision = record_mining_attempt_dispatch(forged_ready, proposal, receipt)
    assert decision.session.phase is MiningOnlyPhase.STOPPED
    assert decision.stop_reason is MiningOnlyStopReason.ATTEMPT_RECEIPT_REPLAYED
    assert decision.session.spent_attempt_ids == (proposal.attempt_id,)
    assert decision.session.spent_dispatch_ids == (receipt.dispatch_id,)


def test_core_carriers_are_frozen_sealed_and_authority_false() -> None:
    state = _state()
    decision = begin_mining_only_session(
        session_id="mining-session-a",
        state=state,
        now_monotonic_s=1.25,
    )
    assert decision.proposal is not None
    with pytest.raises(FrozenInstanceError):
        state.status = WorldStatePublicationStatus.FULL  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        decision.proposal.max_click_dispatches = 2  # type: ignore[misc]
    with pytest.raises(TypeError):
        type("ForgedEpoch", (PerceptionEpoch,), {})
    with pytest.raises(TypeError):
        type("ForgedState", (AtomicMiningWorldState,), {})
    assert decision.proposal.input_authority is False
    assert decision.session.input_authority is False
