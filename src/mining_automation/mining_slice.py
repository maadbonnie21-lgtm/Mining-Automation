"""Fail-closed offline state machine for the first mining-only vertical slice.

This module deliberately stops before desktop input.  It binds independently
released Resource and Inventory observations from one owned perception epoch,
selects one source-ordered iron target, records at most one externally supplied
click-dispatch receipt, and requires a strictly newer atomic observation before
progress can be accepted.  No function in this module moves the mouse, captures
a frame, authorizes RuneLite, enters navigation, or treats dispatch as success.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Literal, final

from .contracts import InventoryState, ResourceState

__all__ = [
    "INVENTORY_CAPACITY",
    "INVENTORY_PUBLICATION_FLOOR",
    "MAX_MINING_PERCEPTION_AGE_S",
    "AtomicMiningWorldState",
    "InventoryPerceptionEnvelope",
    "MiningAttemptDispatchReceipt",
    "MiningAttemptProposal",
    "MiningOnlyDecision",
    "MiningOnlyPhase",
    "MiningOnlySession",
    "MiningOnlyStopReason",
    "MiningProgressKind",
    "PerceptionEpoch",
    "PerceptionReleaseIdentity",
    "ResourcePerceptionEnvelope",
    "ResourceViewState",
    "WorldStatePublicationStatus",
    "assemble_atomic_mining_world_state",
    "begin_mining_only_session",
    "record_mining_attempt_dispatch",
    "reobserve_mining_attempt",
]

INVENTORY_CAPACITY: Final[int] = 28
INVENTORY_PUBLICATION_FLOOR: Final[float] = 0.8
MAX_MINING_PERCEPTION_AGE_S: Final[float] = 1.0
_RESOURCE_RELEASE_ROLE: Final[str] = "released-resource-perception"
_INVENTORY_RELEASE_ROLE: Final[str] = "released-inventory-perception"
_SHA256_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}\Z")
_GIT_SHA_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{40}\Z")
_IDENTIFIER_PATTERN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,199}\Z")


def _exact_identifier(value: object, label: str) -> str:
    if type(value) is not str or not _IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(f"{label} must be a canonical identifier")
    return value


def _exact_sha256(value: object, label: str) -> str:
    if type(value) is not str or not _SHA256_PATTERN.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _exact_git_sha(value: object, label: str) -> str:
    if type(value) is not str or not _GIT_SHA_PATTERN.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase 40-character Git SHA")
    return value


def _exact_non_negative_int(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a non-negative exact integer")
    return value


def _exact_positive_int(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{label} must be a positive exact integer")
    return value


def _exact_finite_float(value: object, label: str) -> float:
    if type(value) is not float or not math.isfinite(value) or value < 0.0:
        raise ValueError(f"{label} must be a finite non-negative exact float")
    return value


def _valid_region(value: object) -> bool:
    return (
        type(value) is tuple
        and len(value) == 4
        and all(type(component) is int for component in value)
        and value[2] > 0
        and value[3] > 0
    )


@final
@dataclass(frozen=True, slots=True)
class PerceptionEpoch:
    """Exact identity of one owned capture/cycle used by both perceptions."""

    capture_source_id: str
    capture_session_id: str
    cycle_id: str
    cycle_sequence: int
    frame_id: int
    captured_monotonic_s: float
    frame_width: int
    frame_height: int
    frame_payload_sha256: str
    pixel_format: Literal["bgra8888"] = "bgra8888"

    def __init_subclass__(cls) -> None:
        raise TypeError("PerceptionEpoch is sealed")

    def __post_init__(self) -> None:
        _exact_identifier(self.capture_source_id, "capture_source_id")
        _exact_identifier(self.capture_session_id, "capture_session_id")
        _exact_identifier(self.cycle_id, "cycle_id")
        _exact_non_negative_int(self.cycle_sequence, "cycle_sequence")
        _exact_non_negative_int(self.frame_id, "frame_id")
        _exact_finite_float(self.captured_monotonic_s, "captured_monotonic_s")
        _exact_positive_int(self.frame_width, "frame_width")
        _exact_positive_int(self.frame_height, "frame_height")
        _exact_sha256(self.frame_payload_sha256, "frame_payload_sha256")
        if self.pixel_format != "bgra8888":
            raise ValueError("pixel_format must be exact bgra8888")

    def strictly_newer_than(self, prior: PerceptionEpoch) -> bool:
        if type(prior) is not PerceptionEpoch:
            return False
        return (
            self.capture_source_id == prior.capture_source_id
            and self.capture_session_id == prior.capture_session_id
            and self.cycle_sequence > prior.cycle_sequence
            and self.frame_id > prior.frame_id
            and self.captured_monotonic_s > prior.captured_monotonic_s
            and self.cycle_id != prior.cycle_id
            and self.frame_payload_sha256 != prior.frame_payload_sha256
            and self.frame_width == prior.frame_width
            and self.frame_height == prior.frame_height
            and self.pixel_format == prior.pixel_format
        )


@final
@dataclass(frozen=True, slots=True)
class PerceptionReleaseIdentity:
    """Exact released producer identity retained in every atomic state.

    This is metadata binding only.  It grants no input authority.  The final
    live adapter must construct it from the source-owned release receipt rather
    than from caller assertions.
    """

    release_role: str
    receipt_id: str
    release_record_sha256: str
    reviewed_source_sha: str
    producer_id: str
    producer_version: str

    def __init_subclass__(cls) -> None:
        raise TypeError("PerceptionReleaseIdentity is sealed")

    def __post_init__(self) -> None:
        if self.release_role not in {_RESOURCE_RELEASE_ROLE, _INVENTORY_RELEASE_ROLE}:
            raise ValueError("release_role is not a supported released perception role")
        _exact_identifier(self.receipt_id, "receipt_id")
        _exact_sha256(self.release_record_sha256, "release_record_sha256")
        _exact_git_sha(self.reviewed_source_sha, "reviewed_source_sha")
        _exact_identifier(self.producer_id, "producer_id")
        _exact_identifier(self.producer_version, "producer_version")


class ResourceViewState(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


@final
@dataclass(frozen=True, slots=True)
class ResourcePerceptionEnvelope:
    epoch: PerceptionEpoch
    release: PerceptionReleaseIdentity
    view: ResourceViewState
    resources: tuple[ResourceState, ...]

    def __init_subclass__(cls) -> None:
        raise TypeError("ResourcePerceptionEnvelope is sealed")

    def __post_init__(self) -> None:
        if type(self.epoch) is not PerceptionEpoch:
            raise ValueError("resource epoch must be exact PerceptionEpoch")
        if type(self.release) is not PerceptionReleaseIdentity:
            raise ValueError("resource release must be exact PerceptionReleaseIdentity")
        if self.release.release_role != _RESOURCE_RELEASE_ROLE:
            raise ValueError("resource release role mismatch")
        if type(self.view) is not ResourceViewState:
            raise ValueError("resource view must be exact ResourceViewState")
        if type(self.resources) is not tuple or any(
            type(resource) is not ResourceState for resource in self.resources
        ):
            raise ValueError("resources must be an exact tuple of exact ResourceState values")


@final
@dataclass(frozen=True, slots=True)
class InventoryPerceptionEnvelope:
    epoch: PerceptionEpoch
    release: PerceptionReleaseIdentity
    inventory: InventoryState
    unknown_reason: str | None = None

    def __init_subclass__(cls) -> None:
        raise TypeError("InventoryPerceptionEnvelope is sealed")

    def __post_init__(self) -> None:
        if type(self.epoch) is not PerceptionEpoch:
            raise ValueError("inventory epoch must be exact PerceptionEpoch")
        if type(self.release) is not PerceptionReleaseIdentity:
            raise ValueError("inventory release must be exact PerceptionReleaseIdentity")
        if self.release.release_role != _INVENTORY_RELEASE_ROLE:
            raise ValueError("inventory release role mismatch")
        if type(self.inventory) is not InventoryState:
            raise ValueError("inventory must be exact InventoryState")
        if self.unknown_reason is not None:
            _exact_identifier(self.unknown_reason, "unknown_reason")
        if self.inventory.occupied_slots is None and self.unknown_reason is None:
            raise ValueError("UNKNOWN inventory requires an exact non-empty reason")
        if self.inventory.occupied_slots is not None and self.unknown_reason is not None:
            raise ValueError("definitive inventory cannot carry an UNKNOWN reason")


class WorldStatePublicationStatus(StrEnum):
    BLOCKED = "blocked"
    READY = "ready"
    FULL = "full"


class MiningOnlyStopReason(StrEnum):
    NONE = "none"
    PUBLICATION_BLOCKED = "publication_blocked"
    RESOURCE_VIEW_NOT_SUPPORTED = "resource_view_not_supported"
    RESOURCE_ENSEMBLE_INVALID = "resource_ensemble_invalid"
    RESOURCE_UNKNOWN = "resource_unknown"
    INVENTORY_UNKNOWN = "inventory_unknown"
    INVENTORY_LAYOUT_INVALID = "inventory_layout_invalid"
    INVENTORY_CONFIDENCE_BELOW_FLOOR = "inventory_confidence_below_floor"
    MIXED_PERCEPTION_EPOCH = "mixed_perception_epoch"
    STALE_PERCEPTION = "stale_perception"
    INVENTORY_FULL = "inventory_full"
    NO_AVAILABLE_IRON = "no_available_iron"
    ATTEMPT_RECEIPT_INVALID = "attempt_receipt_invalid"
    ATTEMPT_RECEIPT_REPLAYED = "attempt_receipt_replayed"
    NEWER_OBSERVATION_REQUIRED = "newer_observation_required"
    PERCEPTION_LINEAGE_CHANGED = "perception_lineage_changed"
    TARGET_IDENTITY_CHANGED = "target_identity_changed"
    AMBIGUOUS_PROGRESS = "ambiguous_progress"
    NO_OBSERVED_PROGRESS = "no_observed_progress"


@final
@dataclass(frozen=True, slots=True)
class AtomicMiningWorldState:
    """One immutable Resource+Inventory publication or an atomic denial."""

    status: WorldStatePublicationStatus
    stop_reason: MiningOnlyStopReason
    epoch: PerceptionEpoch | None
    resource_release: PerceptionReleaseIdentity | None
    inventory_release: PerceptionReleaseIdentity | None
    resources: tuple[ResourceState, ...]
    inventory: InventoryState
    selected_target: ResourceState | None
    blockers: tuple[MiningOnlyStopReason, ...]
    input_authority: Literal[False] = field(default=False, init=False)
    navigation_authority: Literal[False] = field(default=False, init=False)
    banking_authority: Literal[False] = field(default=False, init=False)

    def __init_subclass__(cls) -> None:
        raise TypeError("AtomicMiningWorldState is sealed")

    def __post_init__(self) -> None:
        if type(self.status) is not WorldStatePublicationStatus:
            raise ValueError("status must be exact WorldStatePublicationStatus")
        if type(self.stop_reason) is not MiningOnlyStopReason:
            raise ValueError("stop_reason must be exact MiningOnlyStopReason")
        if type(self.inventory) is not InventoryState:
            raise ValueError("inventory must be exact InventoryState")
        if type(self.resources) is not tuple or any(
            type(resource) is not ResourceState for resource in self.resources
        ):
            raise ValueError("resources must be exact ResourceState tuple")
        if type(self.blockers) is not tuple or any(
            type(reason) is not MiningOnlyStopReason for reason in self.blockers
        ):
            raise ValueError("blockers must be exact stop-reason tuple")
        if len(set(self.blockers)) != len(self.blockers):
            raise ValueError("blockers must be unique")
        if self.status is WorldStatePublicationStatus.BLOCKED:
            if (
                self.epoch is not None
                or self.resource_release is not None
                or self.inventory_release is not None
                or self.resources
                or self.inventory.occupied_slots is not None
                or self.selected_target is not None
                or not self.blockers
                or self.stop_reason is MiningOnlyStopReason.NONE
            ):
                raise ValueError("blocked publication must be atomically cleared")
        else:
            if (
                type(self.epoch) is not PerceptionEpoch
                or type(self.resource_release) is not PerceptionReleaseIdentity
                or type(self.inventory_release) is not PerceptionReleaseIdentity
                or not self.resources
                or self.inventory.occupied_slots is None
                or self.blockers
            ):
                raise ValueError("published world state lacks exact released evidence")
            if self.status is WorldStatePublicationStatus.FULL:
                if self.inventory.is_full is not True or self.selected_target is not None:
                    raise ValueError("FULL state must end the mining-only slice")
            elif self.selected_target is None:
                raise ValueError("READY state requires one source-ordered target")
        if (
            self.input_authority is not False
            or self.navigation_authority is not False
            or self.banking_authority is not False
        ):
            raise ValueError("offline mining world state cannot carry authority")


_BLOCKED_INVENTORY: Final[InventoryState] = InventoryState(
    occupied_slots=None,
    capacity=INVENTORY_CAPACITY,
    confidence=0.0,
)


def _blocked(*reasons: MiningOnlyStopReason) -> AtomicMiningWorldState:
    unique = tuple(dict.fromkeys(reasons))
    if not unique:
        unique = (MiningOnlyStopReason.PUBLICATION_BLOCKED,)
    return AtomicMiningWorldState(
        status=WorldStatePublicationStatus.BLOCKED,
        stop_reason=unique[0],
        epoch=None,
        resource_release=None,
        inventory_release=None,
        resources=(),
        inventory=_BLOCKED_INVENTORY,
        selected_target=None,
        blockers=unique,
    )


def _resource_ensemble_issues(
    envelope: ResourcePerceptionEnvelope,
) -> tuple[MiningOnlyStopReason, ...]:
    resources = envelope.resources
    if len(resources) == 0:
        return (MiningOnlyStopReason.RESOURCE_ENSEMBLE_INVALID,)
    seen: set[str] = set()
    issues: list[MiningOnlyStopReason] = []
    for resource in resources:
        if (
            not resource.resource_id
            or resource.resource_id in seen
            or resource.resource_type != "iron"
            or type(resource.confidence) is not float
            or not math.isfinite(resource.confidence)
            or not 0.0 <= resource.confidence <= 1.0
        ):
            if MiningOnlyStopReason.RESOURCE_ENSEMBLE_INVALID not in issues:
                issues.append(MiningOnlyStopReason.RESOURCE_ENSEMBLE_INVALID)
        seen.add(resource.resource_id)
        if resource.available is None:
            if MiningOnlyStopReason.RESOURCE_UNKNOWN not in issues:
                issues.append(MiningOnlyStopReason.RESOURCE_UNKNOWN)
        elif resource.available is True:
            if not _valid_region(resource.interaction_region):
                if MiningOnlyStopReason.RESOURCE_ENSEMBLE_INVALID not in issues:
                    issues.append(MiningOnlyStopReason.RESOURCE_ENSEMBLE_INVALID)
        elif resource.interaction_region is not None:
            if MiningOnlyStopReason.RESOURCE_ENSEMBLE_INVALID not in issues:
                issues.append(MiningOnlyStopReason.RESOURCE_ENSEMBLE_INVALID)
    return tuple(issues)


def assemble_atomic_mining_world_state(
    *,
    resource: object,
    inventory: object,
    evaluated_monotonic_s: object,
) -> AtomicMiningWorldState:
    """Publish one exact same-epoch state or clear everything fail closed."""

    reasons: list[MiningOnlyStopReason] = []
    if type(resource) is not ResourcePerceptionEnvelope:
        reasons.append(MiningOnlyStopReason.PUBLICATION_BLOCKED)
    if type(inventory) is not InventoryPerceptionEnvelope:
        reasons.append(MiningOnlyStopReason.PUBLICATION_BLOCKED)
    if reasons:
        return _blocked(*reasons)
    assert isinstance(resource, ResourcePerceptionEnvelope)
    assert isinstance(inventory, InventoryPerceptionEnvelope)

    if resource.epoch != inventory.epoch:
        reasons.append(MiningOnlyStopReason.MIXED_PERCEPTION_EPOCH)
    if resource.view is not ResourceViewState.SUPPORTED:
        reasons.append(MiningOnlyStopReason.RESOURCE_VIEW_NOT_SUPPORTED)
    reasons.extend(_resource_ensemble_issues(resource))

    state = inventory.inventory
    if state.capacity != INVENTORY_CAPACITY:
        reasons.append(MiningOnlyStopReason.INVENTORY_LAYOUT_INVALID)
    if state.occupied_slots is None:
        reasons.append(MiningOnlyStopReason.INVENTORY_UNKNOWN)
    elif type(state.occupied_slots) is not int or not 0 <= state.occupied_slots <= state.capacity:
        reasons.append(MiningOnlyStopReason.INVENTORY_LAYOUT_INVALID)
    if (
        type(state.confidence) is not float
        or not math.isfinite(state.confidence)
        or state.confidence < INVENTORY_PUBLICATION_FLOOR
        or state.confidence > 1.0
    ):
        reasons.append(MiningOnlyStopReason.INVENTORY_CONFIDENCE_BELOW_FLOOR)

    try:
        evaluated = _exact_finite_float(evaluated_monotonic_s, "evaluated_monotonic_s")
    except ValueError:
        reasons.append(MiningOnlyStopReason.STALE_PERCEPTION)
    else:
        age = evaluated - resource.epoch.captured_monotonic_s
        if age < 0.0 or age > MAX_MINING_PERCEPTION_AGE_S:
            reasons.append(MiningOnlyStopReason.STALE_PERCEPTION)

    if reasons:
        return _blocked(*tuple(dict.fromkeys(reasons)))

    assert state.occupied_slots is not None
    if state.is_full is True:
        return AtomicMiningWorldState(
            status=WorldStatePublicationStatus.FULL,
            stop_reason=MiningOnlyStopReason.INVENTORY_FULL,
            epoch=resource.epoch,
            resource_release=resource.release,
            inventory_release=inventory.release,
            resources=resource.resources,
            inventory=state,
            selected_target=None,
            blockers=(),
        )

    selected = next(
        (
            candidate
            for candidate in resource.resources
            if candidate.available is True
            and candidate.resource_type == "iron"
            and _valid_region(candidate.interaction_region)
        ),
        None,
    )
    if selected is None:
        return _blocked(MiningOnlyStopReason.NO_AVAILABLE_IRON)
    return AtomicMiningWorldState(
        status=WorldStatePublicationStatus.READY,
        stop_reason=MiningOnlyStopReason.NONE,
        epoch=resource.epoch,
        resource_release=resource.release,
        inventory_release=inventory.release,
        resources=resource.resources,
        inventory=state,
        selected_target=selected,
        blockers=(),
    )


class MiningOnlyPhase(StrEnum):
    READY = "ready"
    AWAITING_NEWER_OBSERVATION = "awaiting_newer_observation"
    COMPLETE = "complete"
    STOPPED = "stopped"


class MiningProgressKind(StrEnum):
    NONE = "none"
    RESOURCE_DEPLETED = "resource_depleted"
    INVENTORY_INCREMENTED = "inventory_incremented"
    RESOURCE_DEPLETED_AND_INVENTORY_INCREMENTED = "resource_depleted_and_inventory_incremented"


@final
@dataclass(frozen=True, slots=True)
class MiningAttemptProposal:
    attempt_id: str
    attempt_sequence: int
    target_id: str
    target_region: tuple[int, int, int, int]
    target_resource_type: Literal["iron"]
    source_epoch: PerceptionEpoch
    resource_release: PerceptionReleaseIdentity
    inventory_release: PerceptionReleaseIdentity
    inventory_occupied_before: int
    created_monotonic_s: float
    max_click_dispatches: Literal[1] = field(default=1, init=False)
    input_authority: Literal[False] = field(default=False, init=False)

    def __init_subclass__(cls) -> None:
        raise TypeError("MiningAttemptProposal is sealed")

    def __post_init__(self) -> None:
        _exact_identifier(self.attempt_id, "attempt_id")
        _exact_positive_int(self.attempt_sequence, "attempt_sequence")
        _exact_identifier(self.target_id, "target_id")
        if not _valid_region(self.target_region):
            raise ValueError("target_region must be one exact positive rectangle")
        if self.target_resource_type != "iron":
            raise ValueError("target_resource_type must be iron")
        if type(self.source_epoch) is not PerceptionEpoch:
            raise ValueError("source_epoch must be exact PerceptionEpoch")
        if type(self.resource_release) is not PerceptionReleaseIdentity:
            raise ValueError("resource_release must be exact identity")
        if type(self.inventory_release) is not PerceptionReleaseIdentity:
            raise ValueError("inventory_release must be exact identity")
        if (
            type(self.inventory_occupied_before) is not int
            or not 0 <= self.inventory_occupied_before < INVENTORY_CAPACITY
        ):
            raise ValueError("inventory_occupied_before must be known and non-full")
        _exact_finite_float(self.created_monotonic_s, "created_monotonic_s")
        if self.max_click_dispatches != 1 or self.input_authority is not False:
            raise ValueError("proposal cannot grant input or multiple clicks")


@final
@dataclass(frozen=True, slots=True)
class MiningAttemptDispatchReceipt:
    """Immutable receipt supplied by a future reviewed one-click executor."""

    attempt_id: str
    attempt_sequence: int
    target_id: str
    target_region: tuple[int, int, int, int]
    source_cycle_id: str
    source_frame_id: int
    source_frame_payload_sha256: str
    dispatcher_id: str
    dispatcher_version: str
    dispatch_id: str
    dispatched_monotonic_s: float
    click_dispatch_count: int
    dispatch_succeeded: bool
    input_authority: Literal[False] = field(default=False, init=False)

    def __init_subclass__(cls) -> None:
        raise TypeError("MiningAttemptDispatchReceipt is sealed")

    def __post_init__(self) -> None:
        _exact_identifier(self.attempt_id, "attempt_id")
        _exact_positive_int(self.attempt_sequence, "attempt_sequence")
        _exact_identifier(self.target_id, "target_id")
        if not _valid_region(self.target_region):
            raise ValueError("target_region must be exact positive rectangle")
        _exact_identifier(self.source_cycle_id, "source_cycle_id")
        _exact_non_negative_int(self.source_frame_id, "source_frame_id")
        _exact_sha256(self.source_frame_payload_sha256, "source_frame_payload_sha256")
        _exact_identifier(self.dispatcher_id, "dispatcher_id")
        _exact_identifier(self.dispatcher_version, "dispatcher_version")
        _exact_identifier(self.dispatch_id, "dispatch_id")
        _exact_finite_float(self.dispatched_monotonic_s, "dispatched_monotonic_s")
        if type(self.click_dispatch_count) is not int or self.click_dispatch_count not in {0, 1}:
            raise ValueError("click_dispatch_count must be exact 0 or 1")
        if type(self.dispatch_succeeded) is not bool:
            raise ValueError("dispatch_succeeded must be exact bool")
        if self.dispatch_succeeded is not (self.click_dispatch_count == 1):
            raise ValueError("dispatch status and click count contradict")
        if self.input_authority is not False:
            raise ValueError("receipt records an attempt but grants no authority")


@final
@dataclass(frozen=True, slots=True)
class MiningOnlySession:
    session_id: str
    phase: MiningOnlyPhase
    current_state: AtomicMiningWorldState
    next_attempt_sequence: int
    pending_proposal: MiningAttemptProposal | None
    pending_receipt: MiningAttemptDispatchReceipt | None
    spent_attempt_ids: tuple[str, ...]
    spent_dispatch_ids: tuple[str, ...]
    stop_reason: MiningOnlyStopReason
    last_progress: MiningProgressKind
    input_authority: Literal[False] = field(default=False, init=False)
    navigation_authority: Literal[False] = field(default=False, init=False)
    banking_authority: Literal[False] = field(default=False, init=False)

    def __init_subclass__(cls) -> None:
        raise TypeError("MiningOnlySession is sealed")

    def __post_init__(self) -> None:
        _exact_identifier(self.session_id, "session_id")
        if type(self.phase) is not MiningOnlyPhase:
            raise ValueError("phase must be exact MiningOnlyPhase")
        if type(self.current_state) is not AtomicMiningWorldState:
            raise ValueError("current_state must be exact AtomicMiningWorldState")
        _exact_positive_int(self.next_attempt_sequence, "next_attempt_sequence")
        if (
            self.pending_proposal is not None
            and type(self.pending_proposal) is not MiningAttemptProposal
        ):
            raise ValueError("pending_proposal must be exact proposal or None")
        if (
            self.pending_receipt is not None
            and type(self.pending_receipt) is not MiningAttemptDispatchReceipt
        ):
            raise ValueError("pending_receipt must be exact receipt or None")
        if type(self.spent_attempt_ids) is not tuple or any(
            type(value) is not str for value in self.spent_attempt_ids
        ):
            raise ValueError("spent_attempt_ids must be exact tuple[str]")
        if type(self.spent_dispatch_ids) is not tuple or any(
            type(value) is not str for value in self.spent_dispatch_ids
        ):
            raise ValueError("spent_dispatch_ids must be exact tuple[str]")
        if len(set(self.spent_attempt_ids)) != len(self.spent_attempt_ids):
            raise ValueError("spent attempt IDs must be unique")
        if len(set(self.spent_dispatch_ids)) != len(self.spent_dispatch_ids):
            raise ValueError("spent dispatch IDs must be unique")
        if type(self.stop_reason) is not MiningOnlyStopReason:
            raise ValueError("stop_reason must be exact MiningOnlyStopReason")
        if type(self.last_progress) is not MiningProgressKind:
            raise ValueError("last_progress must be exact MiningProgressKind")
        if self.phase is MiningOnlyPhase.READY:
            if (
                self.current_state.status is not WorldStatePublicationStatus.READY
                or self.pending_proposal is not None
                or self.pending_receipt is not None
                or self.stop_reason is not MiningOnlyStopReason.NONE
            ):
                raise ValueError("READY session shape invalid")
        elif self.phase is MiningOnlyPhase.AWAITING_NEWER_OBSERVATION:
            if self.pending_proposal is None or self.pending_receipt is None:
                raise ValueError("awaiting session requires proposal and receipt")
        elif self.phase is MiningOnlyPhase.COMPLETE:
            if (
                self.current_state.status is not WorldStatePublicationStatus.FULL
                or self.stop_reason is not MiningOnlyStopReason.INVENTORY_FULL
                or self.pending_proposal is not None
                or self.pending_receipt is not None
            ):
                raise ValueError("COMPLETE session shape invalid")
        elif self.stop_reason in {MiningOnlyStopReason.NONE, MiningOnlyStopReason.INVENTORY_FULL}:
            raise ValueError("STOPPED session requires a failure stop reason")
        if (
            self.input_authority is not False
            or self.navigation_authority is not False
            or self.banking_authority is not False
        ):
            raise ValueError("mining-only session cannot carry downstream authority")


@final
@dataclass(frozen=True, slots=True)
class MiningOnlyDecision:
    session: MiningOnlySession
    proposal: MiningAttemptProposal | None
    progress: MiningProgressKind
    stop_reason: MiningOnlyStopReason

    def __post_init__(self) -> None:
        if type(self.session) is not MiningOnlySession:
            raise ValueError("session must be exact MiningOnlySession")
        if self.proposal is not None and type(self.proposal) is not MiningAttemptProposal:
            raise ValueError("proposal must be exact MiningAttemptProposal or None")
        if type(self.progress) is not MiningProgressKind:
            raise ValueError("progress must be exact MiningProgressKind")
        if type(self.stop_reason) is not MiningOnlyStopReason:
            raise ValueError("stop_reason must be exact MiningOnlyStopReason")


def _attempt_id(session_id: str, sequence: int, state: AtomicMiningWorldState) -> str:
    assert state.epoch is not None
    assert state.selected_target is not None
    return f"{session_id}:attempt-{sequence}:{state.epoch.cycle_id}:{state.selected_target.resource_id}"


def _proposal_from_state(
    session_id: str,
    sequence: int,
    state: AtomicMiningWorldState,
    created_monotonic_s: float,
) -> MiningAttemptProposal:
    assert state.status is WorldStatePublicationStatus.READY
    assert state.epoch is not None
    assert state.resource_release is not None
    assert state.inventory_release is not None
    assert state.selected_target is not None
    assert state.selected_target.interaction_region is not None
    assert state.inventory.occupied_slots is not None
    return MiningAttemptProposal(
        attempt_id=_attempt_id(session_id, sequence, state),
        attempt_sequence=sequence,
        target_id=state.selected_target.resource_id,
        target_region=state.selected_target.interaction_region,
        target_resource_type="iron",
        source_epoch=state.epoch,
        resource_release=state.resource_release,
        inventory_release=state.inventory_release,
        inventory_occupied_before=state.inventory.occupied_slots,
        created_monotonic_s=created_monotonic_s,
    )


def begin_mining_only_session(
    *,
    session_id: str,
    state: AtomicMiningWorldState,
    now_monotonic_s: object,
) -> MiningOnlyDecision:
    """Begin from one atomic state; FULL is completion, never navigation."""

    _exact_identifier(session_id, "session_id")
    if type(state) is not AtomicMiningWorldState:
        state = _blocked(MiningOnlyStopReason.PUBLICATION_BLOCKED)
    if state.status is WorldStatePublicationStatus.FULL:
        session = MiningOnlySession(
            session_id=session_id,
            phase=MiningOnlyPhase.COMPLETE,
            current_state=state,
            next_attempt_sequence=1,
            pending_proposal=None,
            pending_receipt=None,
            spent_attempt_ids=(),
            spent_dispatch_ids=(),
            stop_reason=MiningOnlyStopReason.INVENTORY_FULL,
            last_progress=MiningProgressKind.NONE,
        )
        return MiningOnlyDecision(session, None, MiningProgressKind.NONE, session.stop_reason)
    if state.status is not WorldStatePublicationStatus.READY:
        reason = state.stop_reason
        session = MiningOnlySession(
            session_id=session_id,
            phase=MiningOnlyPhase.STOPPED,
            current_state=state,
            next_attempt_sequence=1,
            pending_proposal=None,
            pending_receipt=None,
            spent_attempt_ids=(),
            spent_dispatch_ids=(),
            stop_reason=reason,
            last_progress=MiningProgressKind.NONE,
        )
        return MiningOnlyDecision(session, None, MiningProgressKind.NONE, reason)
    try:
        now = _exact_finite_float(now_monotonic_s, "now_monotonic_s")
    except ValueError:
        blocked = _blocked(MiningOnlyStopReason.STALE_PERCEPTION)
        return begin_mining_only_session(
            session_id=session_id,
            state=blocked,
            now_monotonic_s=0.0,
        )
    assert state.epoch is not None
    if (
        now < state.epoch.captured_monotonic_s
        or now - state.epoch.captured_monotonic_s > MAX_MINING_PERCEPTION_AGE_S
    ):
        return begin_mining_only_session(
            session_id=session_id,
            state=_blocked(MiningOnlyStopReason.STALE_PERCEPTION),
            now_monotonic_s=0.0,
        )
    proposal = _proposal_from_state(session_id, 1, state, now)
    session = MiningOnlySession(
        session_id=session_id,
        phase=MiningOnlyPhase.READY,
        current_state=state,
        next_attempt_sequence=1,
        pending_proposal=None,
        pending_receipt=None,
        spent_attempt_ids=(),
        spent_dispatch_ids=(),
        stop_reason=MiningOnlyStopReason.NONE,
        last_progress=MiningProgressKind.NONE,
    )
    return MiningOnlyDecision(session, proposal, MiningProgressKind.NONE, MiningOnlyStopReason.NONE)


def _stop_session(
    session: MiningOnlySession,
    reason: MiningOnlyStopReason,
    *,
    state: AtomicMiningWorldState | None = None,
    spent_attempt_ids: tuple[str, ...] | None = None,
    spent_dispatch_ids: tuple[str, ...] | None = None,
) -> MiningOnlyDecision:
    stopped = MiningOnlySession(
        session_id=session.session_id,
        phase=MiningOnlyPhase.STOPPED,
        current_state=state or session.current_state,
        next_attempt_sequence=session.next_attempt_sequence,
        pending_proposal=None,
        pending_receipt=None,
        spent_attempt_ids=(
            session.spent_attempt_ids if spent_attempt_ids is None else spent_attempt_ids
        ),
        spent_dispatch_ids=(
            session.spent_dispatch_ids if spent_dispatch_ids is None else spent_dispatch_ids
        ),
        stop_reason=reason,
        last_progress=session.last_progress,
    )
    return MiningOnlyDecision(stopped, None, stopped.last_progress, reason)


def record_mining_attempt_dispatch(
    session: MiningOnlySession,
    proposal: object,
    receipt: object,
) -> MiningOnlyDecision:
    """Consume one exact receipt.  The dispatch remains only an attempt."""

    if type(session) is not MiningOnlySession or session.phase is not MiningOnlyPhase.READY:
        if type(session) is MiningOnlySession:
            return _stop_session(session, MiningOnlyStopReason.ATTEMPT_RECEIPT_INVALID)
        raise TypeError("session must be an exact READY MiningOnlySession")
    if (
        type(proposal) is not MiningAttemptProposal
        or type(receipt) is not MiningAttemptDispatchReceipt
    ):
        return _stop_session(session, MiningOnlyStopReason.ATTEMPT_RECEIPT_INVALID)
    assert isinstance(proposal, MiningAttemptProposal)
    assert isinstance(receipt, MiningAttemptDispatchReceipt)
    if (
        proposal.attempt_id in session.spent_attempt_ids
        or receipt.dispatch_id in session.spent_dispatch_ids
    ):
        return _stop_session(session, MiningOnlyStopReason.ATTEMPT_RECEIPT_REPLAYED)
    expected = _proposal_from_state(
        session.session_id,
        session.next_attempt_sequence,
        session.current_state,
        proposal.created_monotonic_s,
    )
    if proposal != expected:
        return _stop_session(session, MiningOnlyStopReason.ATTEMPT_RECEIPT_INVALID)
    matches = (
        receipt.attempt_id == proposal.attempt_id
        and receipt.attempt_sequence == proposal.attempt_sequence
        and receipt.target_id == proposal.target_id
        and receipt.target_region == proposal.target_region
        and receipt.source_cycle_id == proposal.source_epoch.cycle_id
        and receipt.source_frame_id == proposal.source_epoch.frame_id
        and receipt.source_frame_payload_sha256 == proposal.source_epoch.frame_payload_sha256
        and receipt.dispatched_monotonic_s >= proposal.created_monotonic_s
        and receipt.click_dispatch_count == 1
        and receipt.dispatch_succeeded is True
    )
    if not matches:
        return _stop_session(session, MiningOnlyStopReason.ATTEMPT_RECEIPT_INVALID)
    awaiting = MiningOnlySession(
        session_id=session.session_id,
        phase=MiningOnlyPhase.AWAITING_NEWER_OBSERVATION,
        current_state=session.current_state,
        next_attempt_sequence=session.next_attempt_sequence,
        pending_proposal=proposal,
        pending_receipt=receipt,
        spent_attempt_ids=(*session.spent_attempt_ids, proposal.attempt_id),
        spent_dispatch_ids=(*session.spent_dispatch_ids, receipt.dispatch_id),
        stop_reason=MiningOnlyStopReason.NONE,
        last_progress=MiningProgressKind.NONE,
    )
    return MiningOnlyDecision(awaiting, None, MiningProgressKind.NONE, MiningOnlyStopReason.NONE)


def _resource_by_id(state: AtomicMiningWorldState, resource_id: str) -> ResourceState | None:
    return next((item for item in state.resources if item.resource_id == resource_id), None)


def reobserve_mining_attempt(
    session: MiningOnlySession,
    newer_state: object,
    *,
    now_monotonic_s: object,
) -> MiningOnlyDecision:
    """Accept progress only from one strictly newer released atomic state."""

    if (
        type(session) is not MiningOnlySession
        or session.phase is not MiningOnlyPhase.AWAITING_NEWER_OBSERVATION
    ):
        if type(session) is MiningOnlySession:
            return _stop_session(session, MiningOnlyStopReason.NEWER_OBSERVATION_REQUIRED)
        raise TypeError("session must be an exact awaiting MiningOnlySession")
    assert session.pending_proposal is not None
    assert session.pending_receipt is not None
    proposal = session.pending_proposal
    receipt = session.pending_receipt
    if type(newer_state) is not AtomicMiningWorldState:
        return _stop_session(session, MiningOnlyStopReason.PUBLICATION_BLOCKED)
    assert isinstance(newer_state, AtomicMiningWorldState)
    if newer_state.status is WorldStatePublicationStatus.BLOCKED:
        return _stop_session(session, newer_state.stop_reason, state=newer_state)
    assert newer_state.epoch is not None
    if not newer_state.epoch.strictly_newer_than(proposal.source_epoch):
        return _stop_session(
            session, MiningOnlyStopReason.NEWER_OBSERVATION_REQUIRED, state=newer_state
        )
    if (
        newer_state.resource_release != proposal.resource_release
        or newer_state.inventory_release != proposal.inventory_release
    ):
        return _stop_session(
            session, MiningOnlyStopReason.PERCEPTION_LINEAGE_CHANGED, state=newer_state
        )
    try:
        now = _exact_finite_float(now_monotonic_s, "now_monotonic_s")
    except ValueError:
        return _stop_session(session, MiningOnlyStopReason.STALE_PERCEPTION, state=newer_state)
    if (
        now < newer_state.epoch.captured_monotonic_s
        or now - newer_state.epoch.captured_monotonic_s > MAX_MINING_PERCEPTION_AGE_S
    ):
        return _stop_session(session, MiningOnlyStopReason.STALE_PERCEPTION, state=newer_state)
    if newer_state.epoch.captured_monotonic_s <= receipt.dispatched_monotonic_s:
        return _stop_session(
            session, MiningOnlyStopReason.NEWER_OBSERVATION_REQUIRED, state=newer_state
        )

    before_target = _resource_by_id(session.current_state, proposal.target_id)
    after_target = _resource_by_id(newer_state, proposal.target_id)
    if before_target is None or after_target is None or after_target.resource_type != "iron":
        return _stop_session(
            session, MiningOnlyStopReason.TARGET_IDENTITY_CHANGED, state=newer_state
        )
    depleted = before_target.available is True and after_target.available is False

    occupied_after = newer_state.inventory.occupied_slots
    assert occupied_after is not None
    inventory_delta = occupied_after - proposal.inventory_occupied_before
    if inventory_delta not in {0, 1}:
        return _stop_session(session, MiningOnlyStopReason.AMBIGUOUS_PROGRESS, state=newer_state)
    incremented = inventory_delta == 1
    if depleted and incremented:
        progress = MiningProgressKind.RESOURCE_DEPLETED_AND_INVENTORY_INCREMENTED
    elif depleted:
        progress = MiningProgressKind.RESOURCE_DEPLETED
    elif incremented:
        progress = MiningProgressKind.INVENTORY_INCREMENTED
    else:
        return _stop_session(session, MiningOnlyStopReason.NO_OBSERVED_PROGRESS, state=newer_state)

    if newer_state.status is WorldStatePublicationStatus.FULL:
        completed = MiningOnlySession(
            session_id=session.session_id,
            phase=MiningOnlyPhase.COMPLETE,
            current_state=newer_state,
            next_attempt_sequence=session.next_attempt_sequence + 1,
            pending_proposal=None,
            pending_receipt=None,
            spent_attempt_ids=session.spent_attempt_ids,
            spent_dispatch_ids=session.spent_dispatch_ids,
            stop_reason=MiningOnlyStopReason.INVENTORY_FULL,
            last_progress=progress,
        )
        return MiningOnlyDecision(completed, None, progress, completed.stop_reason)

    assert newer_state.status is WorldStatePublicationStatus.READY
    next_sequence = session.next_attempt_sequence + 1
    next_proposal = _proposal_from_state(session.session_id, next_sequence, newer_state, now)
    ready = MiningOnlySession(
        session_id=session.session_id,
        phase=MiningOnlyPhase.READY,
        current_state=newer_state,
        next_attempt_sequence=next_sequence,
        pending_proposal=None,
        pending_receipt=None,
        spent_attempt_ids=session.spent_attempt_ids,
        spent_dispatch_ids=session.spent_dispatch_ids,
        stop_reason=MiningOnlyStopReason.NONE,
        last_progress=progress,
    )
    return MiningOnlyDecision(ready, next_proposal, progress, MiningOnlyStopReason.NONE)
