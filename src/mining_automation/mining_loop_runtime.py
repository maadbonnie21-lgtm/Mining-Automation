"""Fail-closed repeated mining-only orchestration.

The existing :mod:`mining_automation.mining_slice` module owns atomic
Resource+Inventory publication, source-order target choice, one-attempt
receipts, strictly-newer reobservation, progress, and FULL.  This module owns
only the repeated live order around that state machine:

``clean -> freeze -> hover prove -> one click -> passive +1 -> clean reacquire``

It contains no Windows capture or input implementation and never starts
navigation or banking.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Literal, Protocol, final

from .contracts import InventoryState
from .mining_slice import (
    INVENTORY_CAPACITY,
    INVENTORY_PUBLICATION_FLOOR,
    AtomicMiningWorldState,
    MiningAttemptDispatchReceipt,
    MiningAttemptProposal,
    MiningOnlyPhase,
    MiningOnlyStopReason,
    MiningProgressKind,
    PerceptionEpoch,
    PerceptionReleaseIdentity,
    WorldStatePublicationStatus,
    begin_mining_only_session,
    record_mining_attempt_dispatch,
    reobserve_mining_attempt,
)

EXPECTED_CLIENT_WIDTH: Final[int] = 1005
EXPECTED_CLIENT_HEIGHT: Final[int] = 1078
EXPECTED_CLIENT_DPI: Final[int] = 96
EXPECTED_PRIMARY_ACTION: Final[str] = "Mine Iron rocks"
DEFAULT_MAX_PASSIVE_OBSERVATIONS: Final[int] = 30
ATTEMPTS_PER_REQUIRED_ORE: Final[int] = 8


class MiningLoopStopReason(StrEnum):
    NONE = "none"
    INVENTORY_FULL = "inventory_full"
    BACKEND_ERROR = "backend_error"
    INITIAL_STATE_BLOCKED = "initial_state_blocked"
    CLEAN_WINDOW_MISMATCH = "clean_window_mismatch"
    CLEAN_CURSOR_NOT_NEUTRAL = "clean_cursor_not_neutral"
    SESSION_NOT_READY = "session_not_ready"
    HOVER_PROOF_MISMATCH = "hover_proof_mismatch"
    HOVER_ACTION_UNPROVEN = "hover_action_unproven"
    DISPATCH_WINDOW_MISMATCH = "dispatch_window_mismatch"
    DISPATCH_RECEIPT_INVALID = "dispatch_receipt_invalid"
    DISPATCH_RECEIPT_REPLAYED = "dispatch_receipt_replayed"
    PASSIVE_OBSERVATION_NOT_NEWER = "passive_observation_not_newer"
    PASSIVE_INVENTORY_LINEAGE_CHANGED = "passive_inventory_lineage_changed"
    PASSIVE_INVENTORY_UNKNOWN = "passive_inventory_unknown"
    PASSIVE_INVENTORY_CONFIDENCE_BELOW_FLOOR = (
        "passive_inventory_confidence_below_floor"
    )
    PASSIVE_INVENTORY_DELTA_AMBIGUOUS = "passive_inventory_delta_ambiguous"
    PASSIVE_PROGRESS_TIMEOUT = "passive_progress_timeout"
    REACQUISITION_NOT_NEWER = "reacquisition_not_newer"
    REACQUISITION_BLOCKED = "reacquisition_blocked"
    STATE_MACHINE_STOPPED = "state_machine_stopped"
    ATTEMPT_LIMIT_EXCEEDED = "attempt_limit_exceeded"


@final
@dataclass(frozen=True, slots=True)
class MiningWindowSnapshot:
    hwnd: int
    foreground_hwnd: int
    client_width: int
    client_height: int
    dpi: int
    is_visible: bool
    is_minimized: bool

    def __post_init__(self) -> None:
        ints = (
            self.hwnd,
            self.foreground_hwnd,
            self.client_width,
            self.client_height,
            self.dpi,
        )
        if any(type(value) is not int or value <= 0 for value in ints):
            raise ValueError("window identity/geometry values must be positive exact ints")
        if type(self.is_visible) is not bool or type(self.is_minimized) is not bool:
            raise ValueError("window visibility fields must be exact bools")


@final
@dataclass(frozen=True, slots=True)
class CleanMiningObservation:
    state: AtomicMiningWorldState
    window: MiningWindowSnapshot
    neutral_cursor_proven: bool
    frame_path: str | None = None
    pose_id: str | None = None
    registration_kind: str | None = None
    matched_landmarks: int | None = None
    matched_zones: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.state) is not AtomicMiningWorldState:
            raise ValueError("clean state must be exact AtomicMiningWorldState")
        if type(self.window) is not MiningWindowSnapshot:
            raise ValueError("clean window must be exact MiningWindowSnapshot")
        if type(self.neutral_cursor_proven) is not bool:
            raise ValueError("neutral_cursor_proven must be exact bool")


@final
@dataclass(frozen=True, slots=True)
class MiningHoverProof:
    proposal_source_epoch: PerceptionEpoch
    hover_epoch: PerceptionEpoch
    attempt_id: str
    target_id: str
    target_region: tuple[int, int, int, int]
    action_text: str | None
    interaction_proven: bool
    window: MiningWindowSnapshot
    root_window_hwnd: int
    client_point: tuple[int, int]
    screen_point: tuple[int, int]
    cursor_matches_target: bool
    input_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        if type(self.proposal_source_epoch) is not PerceptionEpoch:
            raise ValueError("proposal source epoch must be exact")
        if type(self.hover_epoch) is not PerceptionEpoch:
            raise ValueError("hover epoch must be exact")
        if type(self.window) is not MiningWindowSnapshot:
            raise ValueError("hover window must be exact")
        if self.input_authority is not False:
            raise ValueError("hover proof cannot grant input")


@final
@dataclass(frozen=True, slots=True)
class MiningDispatchResult:
    receipt: MiningAttemptDispatchReceipt
    window: MiningWindowSnapshot
    root_window_hwnd: int
    client_point: tuple[int, int]
    screen_point: tuple[int, int]
    cursor_matches_target: bool
    coordinate_round_trip_exact: bool
    input_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        if type(self.receipt) is not MiningAttemptDispatchReceipt:
            raise ValueError("dispatch receipt must be exact")
        if type(self.window) is not MiningWindowSnapshot:
            raise ValueError("dispatch window must be exact")
        if self.input_authority is not False:
            raise ValueError("dispatch evidence cannot grant input")


@final
@dataclass(frozen=True, slots=True)
class PassiveMiningObservation:
    epoch: PerceptionEpoch
    inventory_release: PerceptionReleaseIdentity
    inventory: InventoryState
    unknown_reason: str | None
    selected_target_available: bool | None
    frame_path: str | None = None

    def __post_init__(self) -> None:
        if type(self.epoch) is not PerceptionEpoch:
            raise ValueError("passive epoch must be exact")
        if type(self.inventory_release) is not PerceptionReleaseIdentity:
            raise ValueError("passive Inventory release must be exact")
        if self.inventory_release.release_role != "released-inventory-perception":
            raise ValueError("passive evidence must use the Inventory release role")
        if type(self.inventory) is not InventoryState:
            raise ValueError("passive Inventory must be exact")
        if self.inventory.occupied_slots is None and self.unknown_reason is None:
            raise ValueError("UNKNOWN passive Inventory requires a reason")
        if self.inventory.occupied_slots is not None and self.unknown_reason is not None:
            raise ValueError("known passive Inventory cannot carry an UNKNOWN reason")


@final
@dataclass(frozen=True, slots=True)
class MiningLoopConfig:
    session_id: str
    expected_hwnd: int
    expected_action_text: str = EXPECTED_PRIMARY_ACTION
    max_passive_observations: int = DEFAULT_MAX_PASSIVE_OBSERVATIONS
    client_width: int = EXPECTED_CLIENT_WIDTH
    client_height: int = EXPECTED_CLIENT_HEIGHT
    dpi: int = EXPECTED_CLIENT_DPI

    def __post_init__(self) -> None:
        if type(self.session_id) is not str or not self.session_id:
            raise ValueError("session_id must be a non-empty exact string")
        if type(self.expected_action_text) is not str or not self.expected_action_text:
            raise ValueError("expected_action_text must be a non-empty exact string")
        ints = (
            self.expected_hwnd,
            self.max_passive_observations,
            self.client_width,
            self.client_height,
            self.dpi,
        )
        if any(type(value) is not int or value <= 0 for value in ints):
            raise ValueError("loop limits/window values must be positive exact ints")


@final
@dataclass(frozen=True, slots=True)
class MiningLoopResult:
    success: bool
    phase: MiningOnlyPhase
    stop_reason: MiningLoopStopReason
    state_stop_reason: MiningOnlyStopReason
    start_inventory: int | None
    end_inventory: int | None
    verified_ores: int
    click_count: int
    attempt_count: int
    target_sequence: tuple[str, ...]
    dispatch_ids: tuple[str, ...]
    events: tuple[dict[str, object], ...]
    final_state: AtomicMiningWorldState | None
    detail: str
    input_authority: Literal[False] = field(default=False, init=False)
    navigation_authority: Literal[False] = field(default=False, init=False)
    banking_authority: Literal[False] = field(default=False, init=False)


class MiningLoopBackend(Protocol):
    def open(self) -> None: ...

    def close(self) -> None: ...

    def acquire_clean_observation(
        self, *, session_id: str, iteration: int
    ) -> CleanMiningObservation: ...

    def prove_hover(
        self, proposal: MiningAttemptProposal, *, iteration: int
    ) -> MiningHoverProof: ...

    def dispatch_one_click(
        self,
        proposal: MiningAttemptProposal,
        proof: MiningHoverProof,
        *,
        iteration: int,
    ) -> MiningDispatchResult: ...

    def observe_passive(
        self,
        proposal: MiningAttemptProposal,
        receipt: MiningAttemptDispatchReceipt,
        *,
        iteration: int,
        passive_index: int,
    ) -> PassiveMiningObservation: ...


def _window_ok(config: MiningLoopConfig, window: MiningWindowSnapshot) -> bool:
    return (
        window.hwnd == config.expected_hwnd
        and window.foreground_hwnd == config.expected_hwnd
        and window.client_width == config.client_width
        and window.client_height == config.client_height
        and window.dpi == config.dpi
        and window.is_visible is True
        and window.is_minimized is False
    )


def _clean_event(kind: str, iteration: int, clean: CleanMiningObservation) -> dict[str, object]:
    state = clean.state
    epoch = state.epoch
    target = state.selected_target
    return {
        "kind": kind,
        "iteration": iteration,
        "frame_path": clean.frame_path,
        "frame_id": None if epoch is None else epoch.frame_id,
        "frame_sha256": None if epoch is None else epoch.frame_payload_sha256,
        "cycle_id": None if epoch is None else epoch.cycle_id,
        "inventory_occupied": state.inventory.occupied_slots,
        "inventory_confidence": state.inventory.confidence,
        "publication_status": state.status.value,
        "state_stop_reason": state.stop_reason.value,
        "selected_target_id": None if target is None else target.resource_id,
        "selected_target_region": None if target is None else target.interaction_region,
        "window_hwnd": clean.window.hwnd,
        "foreground_hwnd": clean.window.foreground_hwnd,
        "client_geometry": [clean.window.client_width, clean.window.client_height],
        "dpi": clean.window.dpi,
        "neutral_cursor_proven": clean.neutral_cursor_proven,
        "pose_id": clean.pose_id,
        "registration_kind": clean.registration_kind,
        "matched_landmarks": clean.matched_landmarks,
        "matched_zones": list(clean.matched_zones),
    }


def run_mining_until_full(
    backend: MiningLoopBackend,
    config: MiningLoopConfig,
) -> MiningLoopResult:
    """Mine until exact 28/28 or stop on the first uncertainty.

    Resource may be temporarily unavailable for decision during passive walking
    frames.  Inventory must stay known at confidence >= 0.8.  A completely fresh
    neutral-cursor atomic state is mandatory before every next target.
    """

    events: list[dict[str, object]] = []
    targets: list[str] = []
    dispatch_ids: list[str] = []
    start_inventory: int | None = None
    state: AtomicMiningWorldState | None = None
    verified_ores = 0
    click_count = 0
    attempt_count = 0
    opened = False

    def finish(
        success: bool,
        phase: MiningOnlyPhase,
        reason: MiningLoopStopReason,
        state_reason: MiningOnlyStopReason,
        detail: str,
    ) -> MiningLoopResult:
        return MiningLoopResult(
            success=success,
            phase=phase,
            stop_reason=reason,
            state_stop_reason=state_reason,
            start_inventory=start_inventory,
            end_inventory=None if state is None else state.inventory.occupied_slots,
            verified_ores=verified_ores,
            click_count=click_count,
            attempt_count=attempt_count,
            target_sequence=tuple(targets),
            dispatch_ids=tuple(dispatch_ids),
            events=tuple(events),
            final_state=state,
            detail=detail,
        )

    try:
        backend.open()
        opened = True
        clean = backend.acquire_clean_observation(
            session_id=config.session_id,
            iteration=1,
        )
        state = clean.state
        events.append(_clean_event("initial_clean_observation", 1, clean))
        if not _window_ok(config, clean.window):
            return finish(
                False,
                MiningOnlyPhase.STOPPED,
                MiningLoopStopReason.CLEAN_WINDOW_MISMATCH,
                MiningOnlyStopReason.PUBLICATION_BLOCKED,
                "initial clean observation lost exact visible foreground client identity",
            )
        if clean.neutral_cursor_proven is not True:
            return finish(
                False,
                MiningOnlyPhase.STOPPED,
                MiningLoopStopReason.CLEAN_CURSOR_NOT_NEUTRAL,
                MiningOnlyStopReason.PUBLICATION_BLOCKED,
                "clean perception was not captured after neutral-cursor proof",
            )
        recoverable_initial = {
            MiningOnlyStopReason.RESOURCE_UNKNOWN,
            MiningOnlyStopReason.RESOURCE_VIEW_NOT_SUPPORTED,
            MiningOnlyStopReason.NO_AVAILABLE_IRON,
        }
        if (
            state.status is WorldStatePublicationStatus.BLOCKED
            and state.stop_reason in recoverable_initial
        ):
            for wait_index in range(1, config.max_passive_observations + 1):
                clean = backend.acquire_clean_observation(
                    session_id=config.session_id,
                    iteration=1,
                )
                state = clean.state
                events.append(_clean_event("initial_settle_reacquisition", wait_index, clean))
                if not _window_ok(config, clean.window):
                    return finish(
                        False,
                        MiningOnlyPhase.STOPPED,
                        MiningLoopStopReason.CLEAN_WINDOW_MISMATCH,
                        MiningOnlyStopReason.PUBLICATION_BLOCKED,
                        "initial settle lost exact visible foreground client identity",
                    )
                if clean.neutral_cursor_proven is not True:
                    return finish(
                        False,
                        MiningOnlyPhase.STOPPED,
                        MiningLoopStopReason.CLEAN_CURSOR_NOT_NEUTRAL,
                        MiningOnlyStopReason.PUBLICATION_BLOCKED,
                        "initial settle was not neutral-cursor clean",
                    )
                if (
                    state.status is not WorldStatePublicationStatus.BLOCKED
                    or state.stop_reason not in recoverable_initial
                ):
                    break
        if state.status is WorldStatePublicationStatus.BLOCKED:
            return finish(
                False,
                MiningOnlyPhase.STOPPED,
                MiningLoopStopReason.INITIAL_STATE_BLOCKED,
                state.stop_reason,
                f"initial atomic state blocked after settle: {state.stop_reason.value}",
            )
        start_inventory = state.inventory.occupied_slots
        if state.status is WorldStatePublicationStatus.FULL:
            return finish(
                True,
                MiningOnlyPhase.COMPLETE,
                MiningLoopStopReason.INVENTORY_FULL,
                MiningOnlyStopReason.INVENTORY_FULL,
                "Inventory was already 28/28; mining-only ended without input",
            )
        assert state.epoch is not None
        assert start_inventory is not None
        remaining_ores = INVENTORY_CAPACITY - start_inventory
        maximum_attempts = max(remaining_ores, remaining_ores * ATTEMPTS_PER_REQUIRED_ORE)
        decision = begin_mining_only_session(
            session_id=config.session_id,
            state=state,
            now_monotonic_s=max(time.monotonic(), state.epoch.captured_monotonic_s),
        )

        iteration = 0
        while True:
            iteration += 1
            if attempt_count >= maximum_attempts:
                return finish(
                    False,
                    MiningOnlyPhase.STOPPED,
                    MiningLoopStopReason.ATTEMPT_LIMIT_EXCEEDED,
                    MiningOnlyStopReason.AMBIGUOUS_PROGRESS,
                    "non-full state remained after the maximum possible exact +1 attempts",
                )
            proposal = decision.proposal
            if proposal is None or decision.session.phase is not MiningOnlyPhase.READY:
                return finish(
                    False,
                    MiningOnlyPhase.STOPPED,
                    MiningLoopStopReason.SESSION_NOT_READY,
                    decision.stop_reason,
                    "mining state machine did not expose one exact READY proposal",
                )

            proof = backend.prove_hover(proposal, iteration=iteration)
            events.append(
                {
                    "kind": "hover_proof",
                    "iteration": iteration,
                    "attempt_id": proposal.attempt_id,
                    "source_frame_id": proposal.source_epoch.frame_id,
                    "source_frame_sha256": proposal.source_epoch.frame_payload_sha256,
                    "hover_frame_id": proof.hover_epoch.frame_id,
                    "hover_frame_sha256": proof.hover_epoch.frame_payload_sha256,
                    "target_id": proof.target_id,
                    "target_region": proof.target_region,
                    "client_point": proof.client_point,
                    "screen_point": proof.screen_point,
                    "action_text": proof.action_text,
                    "interaction_proven": proof.interaction_proven,
                    "window_hwnd": proof.window.hwnd,
                    "foreground_hwnd": proof.window.foreground_hwnd,
                    "root_window_hwnd": proof.root_window_hwnd,
                    "cursor_matches_target": proof.cursor_matches_target,
                }
            )
            if not (
                proof.proposal_source_epoch == proposal.source_epoch
                and proof.attempt_id == proposal.attempt_id
                and proof.target_id == proposal.target_id
                and proof.target_region == proposal.target_region
                and proof.hover_epoch.strictly_newer_than(proposal.source_epoch)
                and _window_ok(config, proof.window)
                and proof.root_window_hwnd == config.expected_hwnd
                and proof.cursor_matches_target is True
            ):
                return finish(
                    False,
                    MiningOnlyPhase.STOPPED,
                    MiningLoopStopReason.HOVER_PROOF_MISMATCH,
                    MiningOnlyStopReason.ATTEMPT_RECEIPT_INVALID,
                    "hover proof was stale, foreign, wrong-window, or not bound to the clean proposal",
                )
            if (
                proof.interaction_proven is not True
                or proof.action_text != config.expected_action_text
            ):
                return finish(
                    False,
                    MiningOnlyPhase.STOPPED,
                    MiningLoopStopReason.HOVER_ACTION_UNPROVEN,
                    MiningOnlyStopReason.ATTEMPT_RECEIPT_INVALID,
                    f"primary hover action was not exact {config.expected_action_text!r}",
                )

            dispatched = backend.dispatch_one_click(
                proposal,
                proof,
                iteration=iteration,
            )
            attempt_count += 1
            events.append(
                {
                    "kind": "single_click_attempt",
                    "iteration": iteration,
                    "attempt_id": proposal.attempt_id,
                    "target_id": proposal.target_id,
                    "target_region": proposal.target_region,
                    "dispatch_id": dispatched.receipt.dispatch_id,
                    "dispatched_monotonic_s": dispatched.receipt.dispatched_monotonic_s,
                    "click_count": dispatched.receipt.click_dispatch_count,
                    "dispatch_succeeded": dispatched.receipt.dispatch_succeeded,
                    "window_hwnd": dispatched.window.hwnd,
                    "foreground_hwnd": dispatched.window.foreground_hwnd,
                    "root_window_hwnd": dispatched.root_window_hwnd,
                    "client_point": dispatched.client_point,
                    "screen_point": dispatched.screen_point,
                    "coordinate_round_trip_exact": dispatched.coordinate_round_trip_exact,
                }
            )
            if not (
                _window_ok(config, dispatched.window)
                and dispatched.root_window_hwnd == config.expected_hwnd
                and dispatched.client_point == proof.client_point
                and dispatched.screen_point == proof.screen_point
                and dispatched.cursor_matches_target is True
                and dispatched.coordinate_round_trip_exact is True
                and dispatched.receipt.dispatched_monotonic_s
                > proof.hover_epoch.captured_monotonic_s
            ):
                return finish(
                    False,
                    MiningOnlyPhase.STOPPED,
                    MiningLoopStopReason.DISPATCH_WINDOW_MISMATCH,
                    MiningOnlyStopReason.ATTEMPT_RECEIPT_INVALID,
                    "dispatch lost exact foreground/window/coordinate identity",
                )
            if dispatched.receipt.dispatch_id in dispatch_ids:
                return finish(
                    False,
                    MiningOnlyPhase.STOPPED,
                    MiningLoopStopReason.DISPATCH_RECEIPT_REPLAYED,
                    MiningOnlyStopReason.ATTEMPT_RECEIPT_REPLAYED,
                    "dispatch ID was reused",
                )
            attempted = record_mining_attempt_dispatch(
                decision.session,
                proposal,
                dispatched.receipt,
            )
            if attempted.session.phase is not MiningOnlyPhase.AWAITING_NEWER_OBSERVATION:
                return finish(
                    False,
                    MiningOnlyPhase.STOPPED,
                    MiningLoopStopReason.DISPATCH_RECEIPT_INVALID,
                    attempted.stop_reason,
                    f"one-click receipt rejected: {attempted.stop_reason.value}",
                )

            click_count += 1
            targets.append(proposal.target_id)
            dispatch_ids.append(dispatched.receipt.dispatch_id)
            before = proposal.inventory_occupied_before
            last_epoch = proof.hover_epoch
            witness: PassiveMiningObservation | None = None
            progress_hint: str | None = None

            for passive_index in range(1, config.max_passive_observations + 1):
                passive = backend.observe_passive(
                    proposal,
                    dispatched.receipt,
                    iteration=iteration,
                    passive_index=passive_index,
                )
                occupied = passive.inventory.occupied_slots
                delta = None if occupied is None else occupied - before
                events.append(
                    {
                        "kind": "passive_verification",
                        "iteration": iteration,
                        "index": passive_index,
                        "frame_path": passive.frame_path,
                        "frame_id": passive.epoch.frame_id,
                        "frame_sha256": passive.epoch.frame_payload_sha256,
                        "cycle_id": passive.epoch.cycle_id,
                        "captured_monotonic_s": passive.epoch.captured_monotonic_s,
                        "inventory_occupied": occupied,
                        "inventory_confidence": passive.inventory.confidence,
                        "inventory_unknown_reason": passive.unknown_reason,
                        "inventory_delta": delta,
                        "selected_target_available": passive.selected_target_available,
                    }
                )
                if (
                    not passive.epoch.strictly_newer_than(last_epoch)
                    or passive.epoch.captured_monotonic_s
                    <= dispatched.receipt.dispatched_monotonic_s
                ):
                    return finish(
                        False,
                        MiningOnlyPhase.STOPPED,
                        MiningLoopStopReason.PASSIVE_OBSERVATION_NOT_NEWER,
                        MiningOnlyStopReason.NEWER_OBSERVATION_REQUIRED,
                        "passive verification was stale, replayed, or predates dispatch",
                    )
                last_epoch = passive.epoch
                if passive.inventory_release != proposal.inventory_release:
                    return finish(
                        False,
                        MiningOnlyPhase.STOPPED,
                        MiningLoopStopReason.PASSIVE_INVENTORY_LINEAGE_CHANGED,
                        MiningOnlyStopReason.PERCEPTION_LINEAGE_CHANGED,
                        "passive Inventory release identity changed",
                    )
                if occupied is None:
                    # A single animation/occlusion frame can make Inventory unreadable.
                    # Do not infer progress and do not click again; simply wait for a
                    # later passive frame within the same bounded observation window.
                    continue
                confidence = passive.inventory.confidence
                if (
                    passive.inventory.capacity != INVENTORY_CAPACITY
                    or type(confidence) is not float
                    or not math.isfinite(confidence)
                    or not INVENTORY_PUBLICATION_FLOOR <= confidence <= 1.0
                ):
                    return finish(
                        False,
                        MiningOnlyPhase.STOPPED,
                        MiningLoopStopReason.PASSIVE_INVENTORY_CONFIDENCE_BELOW_FLOOR,
                        MiningOnlyStopReason.INVENTORY_CONFIDENCE_BELOW_FLOOR,
                        "passive Inventory did not satisfy capacity/floor contract",
                    )
                assert delta is not None
                if delta == 1:
                    witness = passive
                    progress_hint = "inventory_incremented"
                    break
                if delta == 0 and passive.selected_target_available is False:
                    witness = passive
                    progress_hint = "target_depleted"
                    break
                if delta != 0:
                    return finish(
                        False,
                        MiningOnlyPhase.STOPPED,
                        MiningLoopStopReason.PASSIVE_INVENTORY_DELTA_AMBIGUOUS,
                        MiningOnlyStopReason.AMBIGUOUS_PROGRESS,
                        f"passive Inventory delta was {delta}, expected only 0 or exact +1",
                    )

            if witness is None:
                return finish(
                    False,
                    MiningOnlyPhase.STOPPED,
                    MiningLoopStopReason.PASSIVE_PROGRESS_TIMEOUT,
                    MiningOnlyStopReason.NO_OBSERVED_PROGRESS,
                    "bounded passive window ended without inventory progress or target depletion",
                )

            clean = backend.acquire_clean_observation(
                session_id=config.session_id,
                iteration=iteration + 1,
            )
            state = clean.state
            events.append(_clean_event("post_attempt_clean_reacquisition", iteration, clean))
            if not _window_ok(config, clean.window):
                return finish(
                    False,
                    MiningOnlyPhase.STOPPED,
                    MiningLoopStopReason.CLEAN_WINDOW_MISMATCH,
                    MiningOnlyStopReason.PUBLICATION_BLOCKED,
                    "post-movement clean reacquisition lost exact client identity",
                )
            if clean.neutral_cursor_proven is not True:
                return finish(
                    False,
                    MiningOnlyPhase.STOPPED,
                    MiningLoopStopReason.CLEAN_CURSOR_NOT_NEUTRAL,
                    MiningOnlyStopReason.PUBLICATION_BLOCKED,
                    "post-movement perception was not neutral-cursor clean",
                )
            recoverable_reacquisition = {
                MiningOnlyStopReason.RESOURCE_UNKNOWN,
                MiningOnlyStopReason.RESOURCE_VIEW_NOT_SUPPORTED,
                MiningOnlyStopReason.NO_AVAILABLE_IRON,
            }
            if (
                state.status is WorldStatePublicationStatus.BLOCKED
                and state.stop_reason in recoverable_reacquisition
            ):
                for wait_index in range(1, config.max_passive_observations + 1):
                    events.append({
                        "kind": (
                            "wait_for_iron_respawn"
                            if state.stop_reason is MiningOnlyStopReason.NO_AVAILABLE_IRON
                            else "wait_for_resource_reacquisition"
                        ),
                        "iteration": iteration,
                        "index": wait_index,
                    })
                    clean = backend.acquire_clean_observation(
                        session_id=config.session_id,
                        iteration=iteration + 1,
                    )
                    state = clean.state
                    events.append(_clean_event("settled_reacquisition", iteration, clean))
                    if (
                        state.status is not WorldStatePublicationStatus.BLOCKED
                        or state.stop_reason not in recoverable_reacquisition
                    ):
                        break
            if state.status is WorldStatePublicationStatus.BLOCKED:
                return finish(
                    False,
                    MiningOnlyPhase.STOPPED,
                    MiningLoopStopReason.REACQUISITION_BLOCKED,
                    state.stop_reason,
                    f"fresh post-attempt state blocked: {state.stop_reason.value}",
                )
            assert state.epoch is not None
            if not state.epoch.strictly_newer_than(witness.epoch):
                return finish(
                    False,
                    MiningOnlyPhase.STOPPED,
                    MiningLoopStopReason.REACQUISITION_NOT_NEWER,
                    MiningOnlyStopReason.NEWER_OBSERVATION_REQUIRED,
                    "post-movement clean state was not newer than the +1 witness",
                )
            decision = reobserve_mining_attempt(
                attempted.session,
                state,
                now_monotonic_s=max(time.monotonic(), state.epoch.captured_monotonic_s),
            )
            if decision.session.phase not in {
                MiningOnlyPhase.READY,
                MiningOnlyPhase.COMPLETE,
            }:
                return finish(
                    False,
                    MiningOnlyPhase.STOPPED,
                    MiningLoopStopReason.STATE_MACHINE_STOPPED,
                    decision.stop_reason,
                    f"fresh +1 state was rejected: {decision.stop_reason.value}",
                )
            ore_gained = decision.progress in {
                MiningProgressKind.INVENTORY_INCREMENTED,
                MiningProgressKind.RESOURCE_DEPLETED_AND_INVENTORY_INCREMENTED,
            }
            if ore_gained:
                verified_ores += 1
            events.append(
                {
                    "kind": "verified_progress" if ore_gained else "lost_race_reacquired",
                    "iteration": iteration,
                    "attempt_id": proposal.attempt_id,
                    "dispatch_id": dispatched.receipt.dispatch_id,
                    "target_id": proposal.target_id,
                    "inventory_before": before,
                    "inventory_after": state.inventory.occupied_slots,
                    "progress_kind": decision.progress.value,
                    "progress_hint": progress_hint,
                    "next_phase": decision.session.phase.value,
                    "next_target_id": (
                        None if decision.proposal is None else decision.proposal.target_id
                    ),
                }
            )
            if decision.session.phase is MiningOnlyPhase.COMPLETE:
                end = state.inventory.occupied_slots
                if (
                    end != INVENTORY_CAPACITY
                    or verified_ores > click_count
                    or verified_ores != end - start_inventory
                    or decision.proposal is not None
                ):
                    return finish(
                        False,
                        MiningOnlyPhase.STOPPED,
                        MiningLoopStopReason.STATE_MACHINE_STOPPED,
                        MiningOnlyStopReason.AMBIGUOUS_PROGRESS,
                        "FULL terminal accounting was inconsistent",
                    )
                return finish(
                    True,
                    MiningOnlyPhase.COMPLETE,
                    MiningLoopStopReason.INVENTORY_FULL,
                    MiningOnlyStopReason.INVENTORY_FULL,
                    "Inventory reached exactly 28/28; stopped before navigation",
                )
    except Exception as exc:
        return finish(
            False,
            MiningOnlyPhase.STOPPED,
            MiningLoopStopReason.BACKEND_ERROR,
            MiningOnlyStopReason.PUBLICATION_BLOCKED,
            f"backend raised {type(exc).__name__}: {exc}",
        )
    finally:
        if opened:
            try:
                backend.close()
            except Exception:
                pass
