from __future__ import annotations

import time
from dataclasses import replace
from hashlib import sha256

import pytest

from mining_automation.contracts import InventoryState, ResourceState
from mining_automation.mining_loop_runtime import (
    CleanMiningObservation,
    MiningDispatchResult,
    MiningHoverProof,
    MiningLoopConfig,
    MiningLoopStopReason,
    MiningWindowSnapshot,
    PassiveMiningObservation,
    run_mining_until_full,
)
from mining_automation.mining_slice import (
    INVENTORY_CAPACITY,
    AtomicMiningWorldState,
    InventoryPerceptionEnvelope,
    MiningAttemptDispatchReceipt,
    MiningAttemptProposal,
    MiningOnlyPhase,
    PerceptionEpoch,
    PerceptionReleaseIdentity,
    ResourcePerceptionEnvelope,
    ResourceViewState,
    assemble_atomic_mining_world_state,
)

_BASE_TIME = time.monotonic() + 1000.0
_RESOURCE_RELEASE = PerceptionReleaseIdentity(
    release_role="released-resource-perception",
    receipt_id="receipt:resource-live-test",
    release_record_sha256=sha256(b"resource-live-test").hexdigest(),
    reviewed_source_sha="a" * 40,
    producer_id="resource-live-test",
    producer_version="1.0.0",
)
_INVENTORY_RELEASE = PerceptionReleaseIdentity(
    release_role="released-inventory-perception",
    receipt_id="receipt:inventory-live-test",
    release_record_sha256=sha256(b"inventory-live-test").hexdigest(),
    reviewed_source_sha="b" * 40,
    producer_id="inventory-live-test",
    producer_version="1.0.0",
)


def _digest(label: str) -> str:
    return sha256(label.encode("ascii")).hexdigest()


def _epoch(sequence: int, *, label: str | None = None) -> PerceptionEpoch:
    token = label or f"frame-{sequence}"
    return PerceptionEpoch(
        capture_source_id="windows-runelite",
        capture_session_id="runtime-test-session",
        cycle_id=f"cycle-{sequence}-{token}",
        cycle_sequence=sequence,
        frame_id=sequence,
        captured_monotonic_s=_BASE_TIME + float(sequence),
        frame_width=1005,
        frame_height=1078,
        frame_payload_sha256=_digest(token),
    )


def _resources(
    target_order: tuple[str, ...] = ("northwest", "southwest", "center"),
    *,
    available: frozenset[str] | None = None,
) -> tuple[ResourceState, ...]:
    available_ids = available if available is not None else frozenset(target_order)
    regions = {
        "northwest": (270, 550, 20, 20),
        "southwest": (440, 545, 20, 20),
        "center": (270, 640, 20, 20),
    }
    return tuple(
        ResourceState(
            resource_id=f"varrock-east-iron-{target_id}",
            resource_type="iron",
            available=target_id in available_ids,
            confidence=0.95,
            interaction_region=(regions[target_id] if target_id in available_ids else None),
        )
        for target_id in target_order
    )


def _state(
    sequence: int,
    occupied: int | None,
    *,
    target_order: tuple[str, ...] = ("northwest", "southwest", "center"),
    available: frozenset[str] | None = None,
    inventory_confidence: float = 1.0,
    resource_view: ResourceViewState = ResourceViewState.SUPPORTED,
) -> AtomicMiningWorldState:
    epoch = _epoch(sequence)
    resources = (
        ()
        if resource_view is not ResourceViewState.SUPPORTED
        else _resources(target_order, available=available)
    )
    return assemble_atomic_mining_world_state(
        resource=ResourcePerceptionEnvelope(
            epoch=epoch,
            release=_RESOURCE_RELEASE,
            view=resource_view,
            resources=resources,
        ),
        inventory=InventoryPerceptionEnvelope(
            epoch=epoch,
            release=_INVENTORY_RELEASE,
            inventory=InventoryState(
                occupied_slots=occupied,
                capacity=INVENTORY_CAPACITY,
                confidence=inventory_confidence,
            ),
            unknown_reason="tooltip_occlusion" if occupied is None else None,
        ),
        evaluated_monotonic_s=epoch.captured_monotonic_s + 0.01,
    )


def _window(
    *,
    hwnd: int = 42,
    foreground: int = 42,
    width: int = 1005,
    height: int = 1078,
    dpi: int = 96,
    visible: bool = True,
    minimized: bool = False,
) -> MiningWindowSnapshot:
    return MiningWindowSnapshot(
        hwnd=hwnd,
        foreground_hwnd=foreground,
        client_width=width,
        client_height=height,
        dpi=dpi,
        is_visible=visible,
        is_minimized=minimized,
    )


class _FakeBackend:
    def __init__(
        self,
        clean_states: list[AtomicMiningWorldState],
        passive_counts: list[list[int | None]],
        *,
        clean_windows: list[MiningWindowSnapshot] | None = None,
        hover_actions: list[str | None] | None = None,
        hover_source_override_on_attempt: int | None = None,
        dispatch_ids: list[str] | None = None,
        passive_confidence: float = 1.0,
        passive_resource_release: PerceptionReleaseIdentity = _RESOURCE_RELEASE,
        passive_release: PerceptionReleaseIdentity = _INVENTORY_RELEASE,
        passive_availability: list[list[bool | None]] | None = None,
    ) -> None:
        self.clean_states = clean_states
        self.passive_counts = passive_counts
        self.clean_windows = clean_windows or [_window()] * len(clean_states)
        self.hover_actions = hover_actions or ["Mine Iron rocks"] * len(passive_counts)
        self.hover_source_override_on_attempt = hover_source_override_on_attempt
        self.dispatch_ids = dispatch_ids or [
            f"dispatch-{index}" for index in range(1, len(passive_counts) + 1)
        ]
        self.passive_confidence = passive_confidence
        self.passive_resource_release = passive_resource_release
        self.passive_release = passive_release
        self.passive_availability = passive_availability or [
            [None] * len(row) for row in passive_counts
        ]
        self.opened = False
        self.closed = False
        self.clean_calls = 0
        self.hover_calls = 0
        self.dispatch_calls = 0
        self.passive_call_by_attempt: dict[int, int] = {}

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.closed = True

    def acquire_clean_observation(
        self, *, session_id: str, iteration: int
    ) -> CleanMiningObservation:
        del session_id, iteration
        index = self.clean_calls
        self.clean_calls += 1
        return CleanMiningObservation(
            state=self.clean_states[index],
            window=self.clean_windows[index],
            neutral_cursor_proven=True,
            frame_path=f"clean-{index + 1}.bgra",
            pose_id=f"pose-{index + 1}",
            matched_landmarks=6,
            matched_zones=("north_west", "north_east", "south_west"),
        )

    def prove_hover(
        self, proposal: MiningAttemptProposal, *, iteration: int
    ) -> MiningHoverProof:
        index = self.hover_calls
        self.hover_calls += 1
        source = proposal.source_epoch
        if self.hover_source_override_on_attempt == iteration:
            source = _epoch(1, label="stale-prior-proposal")
        rx, ry, rw, rh = proposal.target_region
        client_point = (rx + rw // 2, ry + rh // 2)
        return MiningHoverProof(
            proposal_source_epoch=source,
            hover_epoch=_epoch(
                proposal.source_epoch.cycle_sequence + 1,
                label=f"hover-{iteration}",
            ),
            attempt_id=proposal.attempt_id,
            target_id=proposal.target_id,
            target_region=proposal.target_region,
            action_text=self.hover_actions[index],
            interaction_proven=(self.hover_actions[index] == "Mine Iron rocks"),
            window=_window(),
            root_window_hwnd=42,
            client_point=client_point,
            screen_point=(client_point[0] + 70, client_point[1] + 140),
            cursor_matches_target=True,
        )

    def dispatch_one_click(
        self,
        proposal: MiningAttemptProposal,
        proof: MiningHoverProof,
        *,
        iteration: int,
    ) -> MiningDispatchResult:
        self.dispatch_calls += 1
        receipt = MiningAttemptDispatchReceipt(
            attempt_id=proposal.attempt_id,
            attempt_sequence=proposal.attempt_sequence,
            target_id=proposal.target_id,
            target_region=proposal.target_region,
            source_cycle_id=proposal.source_epoch.cycle_id,
            source_frame_id=proposal.source_epoch.frame_id,
            source_frame_payload_sha256=proposal.source_epoch.frame_payload_sha256,
            dispatcher_id="fake-reviewed-dispatcher",
            dispatcher_version="1.0.0",
            dispatch_id=self.dispatch_ids[iteration - 1],
            dispatched_monotonic_s=proof.hover_epoch.captured_monotonic_s + 0.01,
            click_dispatch_count=1,
            dispatch_succeeded=True,
        )
        return MiningDispatchResult(
            receipt=receipt,
            window=_window(),
            root_window_hwnd=42,
            client_point=proof.client_point,
            screen_point=proof.screen_point,
            cursor_matches_target=True,
            coordinate_round_trip_exact=True,
        )

    def observe_passive(
        self,
        proposal: MiningAttemptProposal,
        receipt: MiningAttemptDispatchReceipt,
        *,
        iteration: int,
        passive_index: int,
    ) -> PassiveMiningObservation:
        del receipt
        calls = self.passive_call_by_attempt.get(iteration, 0)
        self.passive_call_by_attempt[iteration] = calls + 1
        occupied = self.passive_counts[iteration - 1][calls]
        sequence = proposal.source_epoch.cycle_sequence + 1 + passive_index
        return PassiveMiningObservation(
            epoch=_epoch(sequence, label=f"passive-{iteration}-{passive_index}"),
            resource_release=self.passive_resource_release,
            inventory_release=self.passive_release,
            inventory=InventoryState(
                occupied_slots=occupied,
                capacity=INVENTORY_CAPACITY,
                confidence=self.passive_confidence if occupied is not None else 0.0,
            ),
            unknown_reason="tooltip_occlusion" if occupied is None else None,
            selected_target_available=self.passive_availability[iteration - 1][calls],
            frame_path=f"passive-{iteration}-{passive_index}.bgra",
        )


def _config(*, max_passive: int = 3) -> MiningLoopConfig:
    return MiningLoopConfig(
        session_id="runtime-test-session",
        expected_hwnd=42,
        max_passive_observations=max_passive,
    )


def test_wrong_foreground_stops_before_hover_or_click() -> None:
    backend = _FakeBackend(
        [_state(100, 0)],
        [],
        clean_windows=[_window(foreground=99)],
    )
    result = run_mining_until_full(backend, _config())
    assert result.stop_reason is MiningLoopStopReason.CLEAN_WINDOW_MISMATCH
    assert backend.hover_calls == 0
    assert backend.dispatch_calls == 0


@pytest.mark.parametrize(
    "window",
    (
        _window(width=1004),
        _window(height=1077),
        _window(dpi=120),
        _window(visible=False),
        _window(minimized=True),
    ),
)
def test_wrong_geometry_dpi_hidden_or_minimized_stops_before_click(
    window: MiningWindowSnapshot,
) -> None:
    backend = _FakeBackend([_state(100, 0)], [], clean_windows=[window])
    result = run_mining_until_full(backend, _config())
    assert result.stop_reason is MiningLoopStopReason.CLEAN_WINDOW_MISMATCH
    assert backend.dispatch_calls == 0


def test_inventory_tooltip_unknown_stops_before_click() -> None:
    backend = _FakeBackend([_state(100, None)], [])
    result = run_mining_until_full(backend, _config())
    assert result.stop_reason is MiningLoopStopReason.INITIAL_STATE_BLOCKED
    assert result.state_stop_reason.value == "inventory_unknown"
    assert backend.dispatch_calls == 0


def test_resource_unknown_stops_before_click() -> None:
    backend = _FakeBackend(
        [_state(100, 0, resource_view=ResourceViewState.UNKNOWN)],
        [],
    )
    result = run_mining_until_full(backend, _config())
    assert result.stop_reason is MiningLoopStopReason.INITIAL_STATE_BLOCKED
    assert backend.dispatch_calls == 0


@pytest.mark.parametrize("action", (None, "Walk here", "Mine Copper rocks"))
def test_wrong_or_unproven_hover_action_stops_with_zero_clicks(
    action: str | None,
) -> None:
    backend = _FakeBackend(
        [_state(100, 27)],
        [[28]],
        hover_actions=[action],
    )
    result = run_mining_until_full(backend, _config())
    assert result.stop_reason is MiningLoopStopReason.HOVER_ACTION_UNPROVEN
    assert backend.dispatch_calls == 0


def test_hover_frame_does_not_replace_frozen_clean_resource_decision() -> None:
    backend = _FakeBackend(
        [
            _state(100, 27, available=frozenset({"northwest"})),
            _state(200, 28, available=frozenset({"southwest"})),
        ],
        [[27, 28]],
    )
    result = run_mining_until_full(backend, _config())
    assert result.success is True
    assert result.target_sequence == ("varrock-east-iron-northwest",)
    hover = next(event for event in result.events if event["kind"] == "hover_proof")
    assert hover["source_frame_id"] == 100
    assert hover["hover_frame_id"] == 101
    assert backend.dispatch_calls == 1


def test_resource_unknown_during_normal_movement_is_tolerated_until_inventory_plus_one() -> None:
    backend = _FakeBackend(
        [_state(100, 27), _state(200, 28)],
        [[27, 27, 28]],
    )
    result = run_mining_until_full(backend, _config(max_passive=3))
    assert result.success is True
    passive = [event for event in result.events if event["kind"] == "passive_verification"]
    assert [event["selected_target_available"] for event in passive] == [None, None, None]
    assert backend.dispatch_calls == 1


def test_no_progress_after_bounded_passive_window_stops_without_retry() -> None:
    backend = _FakeBackend([_state(100, 5)], [[5, 5, 5]])
    result = run_mining_until_full(backend, _config(max_passive=3))
    assert result.stop_reason is MiningLoopStopReason.PASSIVE_PROGRESS_TIMEOUT
    assert result.click_count == 1
    assert result.attempt_count == 1
    assert backend.dispatch_calls == 1
    assert backend.clean_calls == 1


def test_depleted_target_without_ore_is_lost_race_then_miner_continues() -> None:
    backend = _FakeBackend(
        [
            _state(100, 26, available=frozenset({"northwest", "southwest"})),
            _state(200, 26, available=frozenset({"southwest"})),
            _state(300, 27, available=frozenset({"center"})),
            _state(400, 28, available=frozenset({"northwest"})),
        ],
        [[26], [27], [28]],
        passive_availability=[[False], [False], [False]],
    )
    result = run_mining_until_full(backend, _config())
    assert result.success is True
    assert result.start_inventory == 26
    assert result.end_inventory == 28
    assert result.click_count == 3
    assert result.attempt_count == 3
    assert result.verified_ores == 2
    lost = [event for event in result.events if event["kind"] == "lost_race_reacquired"]
    assert len(lost) == 1
    assert lost[0]["progress_kind"] == "resource_depleted"
    assert lost[0]["inventory_before"] == lost[0]["inventory_after"] == 26


def test_lost_race_survives_same_target_respawn_before_reacquisition() -> None:
    backend = _FakeBackend(
        [
            _state(100, 27, available=frozenset({"northwest"})),
            _state(200, 27, available=frozenset()),
            _state(300, 27, available=frozenset({"northwest"})),
            _state(400, 28, available=frozenset({"southwest"})),
        ],
        [[27], [28]],
        passive_availability=[[False], [False]],
    )
    result = run_mining_until_full(backend, _config())
    assert result.success is True
    assert result.end_inventory == 28
    assert result.click_count == 2
    assert result.verified_ores == 1
    assert len([event for event in result.events if event["kind"] == "lost_race_reacquired"]) == 1


def test_exact_plus_one_reacquires_then_continues_to_full() -> None:
    backend = _FakeBackend(
        [
            _state(100, 26, available=frozenset({"northwest"})),
            _state(200, 27, available=frozenset({"southwest"})),
            _state(300, 28, available=frozenset({"center"})),
        ],
        [[26, 27], [27, 28]],
    )
    result = run_mining_until_full(backend, _config())
    assert result.success is True
    assert result.start_inventory == 26
    assert result.end_inventory == 28
    assert result.verified_ores == 2
    assert result.click_count == 2
    assert result.phase is MiningOnlyPhase.COMPLETE


def test_stale_prior_position_hover_geometry_is_rejected_before_second_click() -> None:
    backend = _FakeBackend(
        [
            _state(100, 26, available=frozenset({"northwest"})),
            _state(200, 27, available=frozenset({"southwest"})),
        ],
        [[27], [28]],
        hover_source_override_on_attempt=2,
    )
    result = run_mining_until_full(backend, _config())
    assert result.stop_reason is MiningLoopStopReason.HOVER_PROOF_MISMATCH
    assert result.click_count == 1
    assert backend.dispatch_calls == 1


def test_three_distinct_current_state_targets_are_selected_dynamically() -> None:
    backend = _FakeBackend(
        [
            _state(100, 25, available=frozenset({"northwest"})),
            _state(200, 26, available=frozenset({"southwest"})),
            _state(300, 27, available=frozenset({"center"})),
            _state(400, 28, available=frozenset({"northwest"})),
        ],
        [[26], [27], [28]],
    )
    result = run_mining_until_full(backend, _config())
    assert result.success is True
    assert result.target_sequence == (
        "varrock-east-iron-northwest",
        "varrock-east-iron-southwest",
        "varrock-east-iron-center",
    )


def test_respawned_rock_can_be_selected_again_from_fresh_current_state() -> None:
    backend = _FakeBackend(
        [
            _state(100, 25, available=frozenset({"northwest"})),
            _state(200, 26, available=frozenset({"southwest"})),
            _state(300, 27, available=frozenset({"northwest"})),
            _state(400, 28, available=frozenset({"center"})),
        ],
        [[26], [27], [28]],
    )
    result = run_mining_until_full(backend, _config())
    assert result.success is True
    assert result.target_sequence == (
        "varrock-east-iron-northwest",
        "varrock-east-iron-southwest",
        "varrock-east-iron-northwest",
    )


def test_inventory_27_to_28_completes_with_no_29th_click() -> None:
    backend = _FakeBackend([_state(100, 27), _state(200, 28)], [[28]])
    result = run_mining_until_full(backend, _config())
    assert result.success is True
    assert result.end_inventory == 28
    assert result.click_count == 1
    assert result.attempt_count == 1
    assert backend.hover_calls == 1
    assert backend.dispatch_calls == 1
    assert result.phase is MiningOnlyPhase.COMPLETE


def test_passive_inventory_unknown_stops_without_second_click() -> None:
    backend = _FakeBackend([_state(100, 5)], [[None]])
    result = run_mining_until_full(backend, _config())
    assert result.stop_reason is MiningLoopStopReason.PASSIVE_INVENTORY_UNKNOWN
    assert result.click_count == 1
    assert backend.dispatch_calls == 1


@pytest.mark.parametrize("after", (4, 7))
def test_inventory_decrease_or_plus_greater_than_one_is_ambiguous(after: int) -> None:
    backend = _FakeBackend([_state(100, 5)], [[after]])
    result = run_mining_until_full(backend, _config())
    assert result.stop_reason is MiningLoopStopReason.PASSIVE_INVENTORY_DELTA_AMBIGUOUS
    assert result.click_count == 1
    assert backend.dispatch_calls == 1


def test_low_confidence_passive_inventory_stops() -> None:
    backend = _FakeBackend(
        [_state(100, 5)],
        [[5]],
        passive_confidence=0.79,
    )
    result = run_mining_until_full(backend, _config())
    assert result.stop_reason is (
        MiningLoopStopReason.PASSIVE_INVENTORY_CONFIDENCE_BELOW_FLOOR
    )
    assert backend.dispatch_calls == 1


def test_replayed_dispatch_id_stops_before_second_receipt_can_progress() -> None:
    backend = _FakeBackend(
        [
            _state(100, 26, available=frozenset({"northwest"})),
            _state(200, 27, available=frozenset({"southwest"})),
        ],
        [[27], [28]],
        dispatch_ids=["same-dispatch", "same-dispatch"],
    )
    result = run_mining_until_full(backend, _config())
    assert result.stop_reason is MiningLoopStopReason.DISPATCH_RECEIPT_REPLAYED
    assert result.click_count == 1
    assert result.attempt_count == 2


def test_post_progress_clean_reacquisition_must_be_strictly_newer() -> None:
    backend = _FakeBackend([_state(100, 27), _state(100, 28)], [[28]])
    result = run_mining_until_full(backend, _config())
    assert result.stop_reason is MiningLoopStopReason.REACQUISITION_NOT_NEWER
    assert result.click_count == 1


def test_changed_passive_resource_release_is_rejected() -> None:
    foreign_release = replace(
        _RESOURCE_RELEASE,
        release_record_sha256=_digest("foreign-resource-release"),
    )
    backend = _FakeBackend(
        [_state(100, 27)],
        [[28]],
        passive_resource_release=foreign_release,
    )
    result = run_mining_until_full(backend, _config())
    assert result.stop_reason is MiningLoopStopReason.PASSIVE_RESOURCE_LINEAGE_CHANGED
    assert result.click_count == 1


def test_changed_passive_inventory_release_is_rejected() -> None:
    foreign_release = replace(
        _INVENTORY_RELEASE,
        release_record_sha256=_digest("foreign-inventory-release"),
    )
    backend = _FakeBackend(
        [_state(100, 27)],
        [[28]],
        passive_release=foreign_release,
    )
    result = run_mining_until_full(backend, _config())
    assert result.stop_reason is MiningLoopStopReason.PASSIVE_INVENTORY_LINEAGE_CHANGED
    assert result.click_count == 1


def test_initial_full_inventory_completes_without_input() -> None:
    backend = _FakeBackend([_state(100, 28)], [])
    result = run_mining_until_full(backend, _config())
    assert result.success is True
    assert result.phase is MiningOnlyPhase.COMPLETE
    assert result.click_count == 0
    assert backend.hover_calls == 0
    assert backend.dispatch_calls == 0
    assert backend.closed is True
