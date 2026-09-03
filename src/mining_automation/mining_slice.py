"""Fail-closed first mining-only slice runtime.

This module implements the smallest release-independent closed loop needed after
Resource and Inventory have each supplied an exact source-owned release receipt.
The only public receipt factory is synthetic and can never authorize live input.
A later reviewed adapter may bind genuine release receipts through the private
real-receipt boundary without changing the mining state machine.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import InitVar, dataclass, field
from enum import StrEnum
from math import isfinite
from threading import Lock
from typing import Final, Literal, final

from .contracts import InventoryState, ResourceState

__all__ = [
    "INVENTORY_CAPACITY",
    "INVENTORY_PUBLICATION_FLOOR",
    "MAX_MINING_ATTEMPTS",
    "MAX_PERCEPTION_AGE_S",
    "AssemblyResult",
    "AttemptProgress",
    "AttemptProgressResult",
    "EvidenceRole",
    "MiningAttemptProposal",
    "MiningAttemptReceipt",
    "MiningOnlyPhase",
    "MiningOnlySession",
    "MiningStopReason",
    "OneAttemptExecutor",
    "PerceptionEpoch",
    "ReleasedMiningObservation",
    "ReleaseComponent",
    "ReleaseReceipt",
    "assemble_released_mining_observation",
    "make_synthetic_release_receipt",
    "verify_attempt_progress",
]

INVENTORY_CAPACITY: Final[int] = 28
INVENTORY_PUBLICATION_FLOOR: Final[float] = 0.8
MAX_PERCEPTION_AGE_S: Final[float] = 1.0
MAX_MINING_ATTEMPTS: Final[int] = 28

_SHA256_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}\Z")
_GIT_SHA_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{40}\Z")
_SYNTHETIC_RECEIPT_TOKEN: Final[object] = object()
_REAL_RECEIPT_TOKEN: Final[object] = object()
_REAL_INPUT_TOKEN: Final[object] = object()
_OBSERVATION_TOKEN: Final[object] = object()


def _identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _git_sha(value: object, name: str) -> str:
    if not isinstance(value, str) or _GIT_SHA_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase 40-character Git SHA")
    return value


def _finite_nonnegative(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a finite non-negative number")
    converted = float(value)
    if not isfinite(converted) or converted < 0.0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return converted


class EvidenceRole(StrEnum):
    SYNTHETIC_ARCHITECTURE_TEST_ONLY = "synthetic-architecture-test-only"
    RELEASED_REAL_CLIENT = "released-real-client"


class ReleaseComponent(StrEnum):
    RESOURCE = "resource"
    INVENTORY = "inventory"


@final
@dataclass(frozen=True, slots=True)
class ReleaseReceipt:
    """Exact release identity; real issuance is intentionally not public."""

    component: ReleaseComponent
    role: EvidenceRole
    release_commit_sha: str
    receipt_sha256: str
    producer_id: str
    release_id: str
    _factory_token: InitVar[object | None] = None

    def __init_subclass__(cls) -> None:
        raise TypeError("ReleaseReceipt is sealed")

    def __post_init__(self, _factory_token: object | None) -> None:
        if type(self.component) is not ReleaseComponent:
            raise ValueError("release component must be exact")
        if type(self.role) is not EvidenceRole:
            raise ValueError("release evidence role must be exact")
        _git_sha(self.release_commit_sha, "release_commit_sha")
        _sha256(self.receipt_sha256, "receipt_sha256")
        _identifier(self.producer_id, "producer_id")
        _identifier(self.release_id, "release_id")
        expected_token = (
            _SYNTHETIC_RECEIPT_TOKEN
            if self.role is EvidenceRole.SYNTHETIC_ARCHITECTURE_TEST_ONLY
            else _REAL_RECEIPT_TOKEN
        )
        if _factory_token is not expected_token:
            raise ValueError("release receipt requires its source-owned factory")

    @property
    def live_input_eligible(self) -> bool:
        return self.role is EvidenceRole.RELEASED_REAL_CLIENT


def make_synthetic_release_receipt(
    component: ReleaseComponent,
    *,
    release_commit_sha: str,
    receipt_sha256: str,
    producer_id: str,
    release_id: str,
) -> ReleaseReceipt:
    """Create architecture-test evidence that can never authorize live input."""

    return ReleaseReceipt(
        component=component,
        role=EvidenceRole.SYNTHETIC_ARCHITECTURE_TEST_ONLY,
        release_commit_sha=release_commit_sha,
        receipt_sha256=receipt_sha256,
        producer_id=producer_id,
        release_id=release_id,
        _factory_token=_SYNTHETIC_RECEIPT_TOKEN,
    )


def _bind_real_release_receipt(
    component: ReleaseComponent,
    *,
    release_commit_sha: str,
    receipt_sha256: str,
    producer_id: str,
    release_id: str,
    _source_token: object,
) -> ReleaseReceipt:
    """Private adapter seam for a later exact source-owned receipt verifier."""

    if _source_token is not _REAL_RECEIPT_TOKEN:
        raise ValueError("real release receipt requires the source-owned verifier")
    return ReleaseReceipt(
        component=component,
        role=EvidenceRole.RELEASED_REAL_CLIENT,
        release_commit_sha=release_commit_sha,
        receipt_sha256=receipt_sha256,
        producer_id=producer_id,
        release_id=release_id,
        _factory_token=_REAL_RECEIPT_TOKEN,
    )


@final
@dataclass(frozen=True, slots=True)
class PerceptionEpoch:
    """One owned frame/cycle and exact capture-source provenance."""

    source_id: str
    session_id: str
    cycle_id: str
    frame_id: int
    captured_monotonic_s: float
    frame_payload_sha256: str

    def __init_subclass__(cls) -> None:
        raise TypeError("PerceptionEpoch is sealed")

    def __post_init__(self) -> None:
        _identifier(self.source_id, "source_id")
        _identifier(self.session_id, "session_id")
        _identifier(self.cycle_id, "cycle_id")
        if not isinstance(self.frame_id, int) or isinstance(self.frame_id, bool):
            raise ValueError("frame_id must be an integer")
        if self.frame_id < 0:
            raise ValueError("frame_id must be non-negative")
        _finite_nonnegative(self.captured_monotonic_s, "captured_monotonic_s")
        _sha256(self.frame_payload_sha256, "frame_payload_sha256")


class MiningStopReason(StrEnum):
    NONE = "none"
    INVALID_CURRENT_TIME = "invalid-current-time"
    MIXED_FRAME = "mixed-frame"
    MIXED_CYCLE = "mixed-cycle"
    MIXED_SOURCE = "mixed-source"
    MIXED_SESSION = "mixed-session"
    MIXED_PAYLOAD = "mixed-payload"
    STALE_EVIDENCE = "stale-evidence"
    FUTURE_EVIDENCE = "future-evidence"
    RESOURCE_RECEIPT_INVALID = "resource-receipt-invalid"
    INVENTORY_RECEIPT_INVALID = "inventory-receipt-invalid"
    MIXED_EVIDENCE_ROLE = "mixed-evidence-role"
    RESOURCE_UNKNOWN = "resource-unknown"
    UNSUPPORTED_RESOURCE_VIEW = "unsupported-resource-view"
    INVENTORY_UNKNOWN = "inventory-unknown"
    INVENTORY_CONFIDENCE_BELOW_FLOOR = "inventory-confidence-below-floor"
    INVENTORY_CAPACITY_MISMATCH = "inventory-capacity-mismatch"
    INVENTORY_FULL = "inventory-full"
    NO_AVAILABLE_IRON = "no-available-iron"
    TARGET_REGION_INVALID = "target-region-invalid"
    ATTEMPT_LIMIT_REACHED = "attempt-limit-reached"
    ATTEMPT_ALREADY_CONSUMED = "attempt-already-consumed"
    INPUT_AUTHORITY_MISSING = "input-authority-missing"
    ATTEMPT_RECEIPT_INVALID = "attempt-receipt-invalid"
    ATTEMPT_DISPATCH_FAILED = "attempt-dispatch-failed"
    NEWER_OBSERVATION_REQUIRED = "newer-observation-required"
    PROVENANCE_CHANGED = "provenance-changed"
    TARGET_IDENTITY_CHANGED = "target-identity-changed"
    INVENTORY_REGRESSED = "inventory-regressed"
    AMBIGUOUS_CAUSALITY = "ambiguous-causality"
    NO_OBSERVED_PROGRESS = "no-observed-progress"


@final
@dataclass(frozen=True, slots=True)
class ReleasedMiningObservation:
    """Atomic Resource + Inventory state from one exact released epoch."""

    epoch: PerceptionEpoch
    resource_receipt: ReleaseReceipt
    inventory_receipt: ReleaseReceipt
    resources: tuple[ResourceState, ...]
    inventory: InventoryState
    supported_resource_view: bool
    input_authority: bool = field(init=False)
    _factory_token: InitVar[object | None] = None

    def __init_subclass__(cls) -> None:
        raise TypeError("ReleasedMiningObservation is sealed")

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _OBSERVATION_TOKEN:
            raise ValueError("released observation requires atomic assembler")
        if type(self.epoch) is not PerceptionEpoch:
            raise ValueError("observation epoch must be exact")
        if type(self.resource_receipt) is not ReleaseReceipt:
            raise ValueError("resource receipt must be exact")
        if type(self.inventory_receipt) is not ReleaseReceipt:
            raise ValueError("inventory receipt must be exact")
        if self.resource_receipt.component is not ReleaseComponent.RESOURCE:
            raise ValueError("resource receipt component mismatch")
        if self.inventory_receipt.component is not ReleaseComponent.INVENTORY:
            raise ValueError("inventory receipt component mismatch")
        if not isinstance(self.resources, tuple) or any(
            type(resource) is not ResourceState for resource in self.resources
        ):
            raise ValueError("resources must be a tuple of exact ResourceState values")
        if type(self.inventory) is not InventoryState:
            raise ValueError("inventory must be an exact InventoryState")
        if not isinstance(self.supported_resource_view, bool):
            raise ValueError("supported_resource_view must be boolean")
        roles = {self.resource_receipt.role, self.inventory_receipt.role}
        if len(roles) != 1:
            raise ValueError("resource and inventory evidence roles must match")
        object.__setattr__(
            self,
            "input_authority",
            roles == {EvidenceRole.RELEASED_REAL_CLIENT},
        )

    @property
    def role(self) -> EvidenceRole:
        return self.resource_receipt.role

    @property
    def is_full(self) -> bool:
        return self.inventory.occupied_slots == INVENTORY_CAPACITY


@final
@dataclass(frozen=True, slots=True)
class AssemblyResult:
    observation: ReleasedMiningObservation | None
    reason: MiningStopReason

    def __post_init__(self) -> None:
        if self.observation is not None and type(self.observation) is not ReleasedMiningObservation:
            raise ValueError("assembly observation must be exact or None")
        if type(self.reason) is not MiningStopReason:
            raise ValueError("assembly reason must be exact")
        if (self.observation is None) == (self.reason is MiningStopReason.NONE):
            raise ValueError("successful assembly requires NONE and failure requires a reason")


def _epoch_mismatch(
    resource_epoch: PerceptionEpoch,
    inventory_epoch: PerceptionEpoch,
) -> MiningStopReason | None:
    if resource_epoch.source_id != inventory_epoch.source_id:
        return MiningStopReason.MIXED_SOURCE
    if resource_epoch.session_id != inventory_epoch.session_id:
        return MiningStopReason.MIXED_SESSION
    if resource_epoch.cycle_id != inventory_epoch.cycle_id:
        return MiningStopReason.MIXED_CYCLE
    if resource_epoch.frame_id != inventory_epoch.frame_id:
        return MiningStopReason.MIXED_FRAME
    if resource_epoch.frame_payload_sha256 != inventory_epoch.frame_payload_sha256:
        return MiningStopReason.MIXED_PAYLOAD
    if resource_epoch.captured_monotonic_s != inventory_epoch.captured_monotonic_s:
        return MiningStopReason.MIXED_FRAME
    return None


def assemble_released_mining_observation(
    *,
    resource_epoch: PerceptionEpoch,
    inventory_epoch: PerceptionEpoch,
    resource_receipt: ReleaseReceipt,
    inventory_receipt: ReleaseReceipt,
    resources: tuple[ResourceState, ...],
    inventory: InventoryState,
    supported_resource_view: bool | None,
    now_monotonic_s: float,
) -> AssemblyResult:
    """Publish one immutable mining observation or fail closed with no state."""

    if type(resource_epoch) is not PerceptionEpoch or type(inventory_epoch) is not PerceptionEpoch:
        return AssemblyResult(None, MiningStopReason.MIXED_FRAME)
    mismatch = _epoch_mismatch(resource_epoch, inventory_epoch)
    if mismatch is not None:
        return AssemblyResult(None, mismatch)

    try:
        now = _finite_nonnegative(now_monotonic_s, "now_monotonic_s")
    except ValueError:
        return AssemblyResult(None, MiningStopReason.INVALID_CURRENT_TIME)
    age = now - resource_epoch.captured_monotonic_s
    if age < 0.0:
        return AssemblyResult(None, MiningStopReason.FUTURE_EVIDENCE)
    if age > MAX_PERCEPTION_AGE_S:
        return AssemblyResult(None, MiningStopReason.STALE_EVIDENCE)

    if type(resource_receipt) is not ReleaseReceipt or (
        resource_receipt.component is not ReleaseComponent.RESOURCE
    ):
        return AssemblyResult(None, MiningStopReason.RESOURCE_RECEIPT_INVALID)
    if type(inventory_receipt) is not ReleaseReceipt or (
        inventory_receipt.component is not ReleaseComponent.INVENTORY
    ):
        return AssemblyResult(None, MiningStopReason.INVENTORY_RECEIPT_INVALID)
    if resource_receipt.role is not inventory_receipt.role:
        return AssemblyResult(None, MiningStopReason.MIXED_EVIDENCE_ROLE)

    if supported_resource_view is None:
        return AssemblyResult(None, MiningStopReason.RESOURCE_UNKNOWN)
    if supported_resource_view is not True:
        return AssemblyResult(None, MiningStopReason.UNSUPPORTED_RESOURCE_VIEW)
    if not isinstance(resources, tuple) or not resources:
        return AssemblyResult(None, MiningStopReason.RESOURCE_UNKNOWN)
    if any(type(resource) is not ResourceState for resource in resources):
        return AssemblyResult(None, MiningStopReason.RESOURCE_UNKNOWN)
    if any(resource.available is None for resource in resources):
        return AssemblyResult(None, MiningStopReason.RESOURCE_UNKNOWN)

    if type(inventory) is not InventoryState or inventory.occupied_slots is None:
        return AssemblyResult(None, MiningStopReason.INVENTORY_UNKNOWN)
    if inventory.capacity != INVENTORY_CAPACITY:
        return AssemblyResult(None, MiningStopReason.INVENTORY_CAPACITY_MISMATCH)
    if inventory.confidence < INVENTORY_PUBLICATION_FLOOR:
        return AssemblyResult(None, MiningStopReason.INVENTORY_CONFIDENCE_BELOW_FLOOR)

    try:
        observation = ReleasedMiningObservation(
            epoch=resource_epoch,
            resource_receipt=resource_receipt,
            inventory_receipt=inventory_receipt,
            resources=resources,
            inventory=inventory,
            supported_resource_view=True,
            _factory_token=_OBSERVATION_TOKEN,
        )
    except ValueError:
        return AssemblyResult(None, MiningStopReason.MIXED_EVIDENCE_ROLE)
    return AssemblyResult(observation, MiningStopReason.NONE)


@final
@dataclass(frozen=True, slots=True)
class MiningAttemptProposal:
    attempt_id: str
    attempt_ordinal: int
    source_observation: ReleasedMiningObservation
    target: ResourceState
    reviewed_execution_sha: str
    input_authority: bool = field(init=False)

    def __init_subclass__(cls) -> None:
        raise TypeError("MiningAttemptProposal is sealed")

    def __post_init__(self) -> None:
        _identifier(self.attempt_id, "attempt_id")
        if (
            not isinstance(self.attempt_ordinal, int)
            or isinstance(self.attempt_ordinal, bool)
            or not 1 <= self.attempt_ordinal <= MAX_MINING_ATTEMPTS
        ):
            raise ValueError("attempt_ordinal must be between 1 and 28")
        if type(self.source_observation) is not ReleasedMiningObservation:
            raise ValueError("source_observation must be exact")
        if type(self.target) is not ResourceState:
            raise ValueError("target must be exact")
        _git_sha(self.reviewed_execution_sha, "reviewed_execution_sha")
        if self.target.resource_type != "iron" or self.target.available is not True:
            raise ValueError("proposal target must be available iron")
        if self.target.interaction_region is None:
            raise ValueError("proposal target requires an interaction region")
        if self.target not in self.source_observation.resources:
            raise ValueError("proposal target must come from the source observation")
        object.__setattr__(self, "input_authority", self.source_observation.input_authority)


@final
@dataclass(frozen=True, slots=True)
class _RealInputCapability:
    epoch: PerceptionEpoch
    reviewed_execution_sha: str
    _factory_token: InitVar[object | None] = None

    def __init_subclass__(cls) -> None:
        raise TypeError("_RealInputCapability is sealed")

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _REAL_INPUT_TOKEN:
            raise ValueError("input capability requires exact released receipt binding")
        if type(self.epoch) is not PerceptionEpoch:
            raise ValueError("input capability epoch must be exact")
        _git_sha(self.reviewed_execution_sha, "reviewed_execution_sha")


def _bind_real_input_capability(
    observation: ReleasedMiningObservation,
    reviewed_execution_sha: str,
    *,
    _source_token: object,
) -> _RealInputCapability:
    if _source_token is not _REAL_INPUT_TOKEN or not observation.input_authority:
        raise ValueError("real input capability requires genuine released evidence")
    return _RealInputCapability(
        observation.epoch,
        reviewed_execution_sha,
        _factory_token=_REAL_INPUT_TOKEN,
    )


@final
@dataclass(frozen=True, slots=True)
class MiningAttemptReceipt:
    attempt_id: str
    attempt_ordinal: int
    source_epoch: PerceptionEpoch
    target_id: str
    target_region: tuple[int, int, int, int]
    reviewed_execution_sha: str
    dispatched_monotonic_s: float
    dispatch_count: Literal[1]
    dispatch_accepted: bool
    role: EvidenceRole
    receipt_sha256: str
    success_observed: Literal[False] = field(default=False, init=False)

    def __init_subclass__(cls) -> None:
        raise TypeError("MiningAttemptReceipt is sealed")

    def __post_init__(self) -> None:
        _identifier(self.attempt_id, "attempt_id")
        if (
            not isinstance(self.attempt_ordinal, int)
            or isinstance(self.attempt_ordinal, bool)
            or not 1 <= self.attempt_ordinal <= MAX_MINING_ATTEMPTS
        ):
            raise ValueError("attempt_ordinal must be between 1 and 28")
        if type(self.source_epoch) is not PerceptionEpoch:
            raise ValueError("source_epoch must be exact")
        _identifier(self.target_id, "target_id")
        if (
            not isinstance(self.target_region, tuple)
            or len(self.target_region) != 4
            or any(
                not isinstance(value, int) or isinstance(value, bool)
                for value in self.target_region
            )
            or self.target_region[2] <= 0
            or self.target_region[3] <= 0
        ):
            raise ValueError("target_region must be a positive integer rectangle")
        _git_sha(self.reviewed_execution_sha, "reviewed_execution_sha")
        _finite_nonnegative(self.dispatched_monotonic_s, "dispatched_monotonic_s")
        if self.dispatch_count != 1:
            raise ValueError("one attempt receipt must record exactly one dispatch")
        if not isinstance(self.dispatch_accepted, bool):
            raise ValueError("dispatch_accepted must be boolean")
        if type(self.role) is not EvidenceRole:
            raise ValueError("receipt role must be exact")
        _sha256(self.receipt_sha256, "receipt_sha256")
        if self.success_observed is not False:
            raise ValueError("dispatch receipt cannot claim mining success")


ClickOnce = Callable[[tuple[int, int, int, int]], bool]


class OneAttemptExecutor:
    """Thread-safe exactly-once dispatch boundary for reviewed proposals."""

    __slots__ = ("_consumed_attempt_ids", "_lock")

    def __init__(self) -> None:
        self._consumed_attempt_ids: set[str] = set()
        self._lock = Lock()

    def dispatch(
        self,
        proposal: MiningAttemptProposal,
        capability: _RealInputCapability,
        click_once: ClickOnce,
        *,
        now_monotonic_s: float,
        receipt_sha256: str,
    ) -> MiningAttemptReceipt:
        if type(proposal) is not MiningAttemptProposal:
            raise ValueError(MiningStopReason.ATTEMPT_RECEIPT_INVALID)
        if type(capability) is not _RealInputCapability:
            raise ValueError(MiningStopReason.INPUT_AUTHORITY_MISSING)
        if not proposal.input_authority:
            raise ValueError(MiningStopReason.INPUT_AUTHORITY_MISSING)
        if capability.epoch != proposal.source_observation.epoch:
            raise ValueError(MiningStopReason.PROVENANCE_CHANGED)
        if capability.reviewed_execution_sha != proposal.reviewed_execution_sha:
            raise ValueError(MiningStopReason.INPUT_AUTHORITY_MISSING)
        now = _finite_nonnegative(now_monotonic_s, "now_monotonic_s")
        if now < proposal.source_observation.epoch.captured_monotonic_s:
            raise ValueError(MiningStopReason.FUTURE_EVIDENCE)
        _sha256(receipt_sha256, "receipt_sha256")

        region = proposal.target.interaction_region
        if region is None:  # Defensive: constructor already rejects this.
            raise ValueError(MiningStopReason.TARGET_REGION_INVALID)
        with self._lock:
            if proposal.attempt_id in self._consumed_attempt_ids:
                raise ValueError(MiningStopReason.ATTEMPT_ALREADY_CONSUMED)
            self._consumed_attempt_ids.add(proposal.attempt_id)
            accepted = bool(click_once(region))

        return MiningAttemptReceipt(
            attempt_id=proposal.attempt_id,
            attempt_ordinal=proposal.attempt_ordinal,
            source_epoch=proposal.source_observation.epoch,
            target_id=proposal.target.resource_id,
            target_region=region,
            reviewed_execution_sha=proposal.reviewed_execution_sha,
            dispatched_monotonic_s=now,
            dispatch_count=1,
            dispatch_accepted=accepted,
            role=proposal.source_observation.role,
            receipt_sha256=receipt_sha256,
        )


class AttemptProgress(StrEnum):
    RESOURCE_DEPLETED = "resource-depleted"
    INVENTORY_INCREMENTED = "inventory-incremented"
    RESOURCE_DEPLETED_AND_INVENTORY_INCREMENTED = (
        "resource-depleted-and-inventory-incremented"
    )


@final
@dataclass(frozen=True, slots=True)
class AttemptProgressResult:
    progress: AttemptProgress | None
    reason: MiningStopReason

    def __post_init__(self) -> None:
        if self.progress is not None and type(self.progress) is not AttemptProgress:
            raise ValueError("progress must be exact or None")
        if type(self.reason) is not MiningStopReason:
            raise ValueError("progress reason must be exact")
        if (self.progress is None) == (self.reason is MiningStopReason.NONE):
            raise ValueError("observed progress requires NONE and failure requires a reason")


def _resource_by_id(
    observation: ReleasedMiningObservation,
    resource_id: str,
) -> ResourceState | None:
    return next(
        (resource for resource in observation.resources if resource.resource_id == resource_id),
        None,
    )


def verify_attempt_progress(
    proposal: MiningAttemptProposal,
    receipt: MiningAttemptReceipt,
    newer_observation: ReleasedMiningObservation,
) -> AttemptProgressResult:
    """Require fresh perception and observed depletion and/or Inventory +1."""

    if type(proposal) is not MiningAttemptProposal or type(receipt) is not MiningAttemptReceipt:
        return AttemptProgressResult(None, MiningStopReason.ATTEMPT_RECEIPT_INVALID)
    if type(newer_observation) is not ReleasedMiningObservation:
        return AttemptProgressResult(None, MiningStopReason.ATTEMPT_RECEIPT_INVALID)
    if (
        receipt.attempt_id != proposal.attempt_id
        or receipt.attempt_ordinal != proposal.attempt_ordinal
        or receipt.target_id != proposal.target.resource_id
        or receipt.target_region != proposal.target.interaction_region
        or receipt.reviewed_execution_sha != proposal.reviewed_execution_sha
        or receipt.source_epoch != proposal.source_observation.epoch
        or receipt.role is not proposal.source_observation.role
    ):
        return AttemptProgressResult(None, MiningStopReason.ATTEMPT_RECEIPT_INVALID)
    if not receipt.dispatch_accepted:
        return AttemptProgressResult(None, MiningStopReason.ATTEMPT_DISPATCH_FAILED)

    old_epoch = proposal.source_observation.epoch
    new_epoch = newer_observation.epoch
    if new_epoch.source_id != old_epoch.source_id or new_epoch.session_id != old_epoch.session_id:
        return AttemptProgressResult(None, MiningStopReason.PROVENANCE_CHANGED)
    if (
        newer_observation.resource_receipt != proposal.source_observation.resource_receipt
        or newer_observation.inventory_receipt != proposal.source_observation.inventory_receipt
    ):
        return AttemptProgressResult(None, MiningStopReason.PROVENANCE_CHANGED)
    if (
        new_epoch.frame_id <= old_epoch.frame_id
        or new_epoch.captured_monotonic_s <= receipt.dispatched_monotonic_s
        or new_epoch.cycle_id == old_epoch.cycle_id
        or new_epoch.frame_payload_sha256 == old_epoch.frame_payload_sha256
    ):
        return AttemptProgressResult(None, MiningStopReason.NEWER_OBSERVATION_REQUIRED)

    old_occupied = proposal.source_observation.inventory.occupied_slots
    new_occupied = newer_observation.inventory.occupied_slots
    if old_occupied is None or new_occupied is None:
        return AttemptProgressResult(None, MiningStopReason.INVENTORY_UNKNOWN)
    inventory_delta = new_occupied - old_occupied
    if inventory_delta < 0:
        return AttemptProgressResult(None, MiningStopReason.INVENTORY_REGRESSED)
    if inventory_delta > 1:
        return AttemptProgressResult(None, MiningStopReason.AMBIGUOUS_CAUSALITY)

    old_target = _resource_by_id(proposal.source_observation, proposal.target.resource_id)
    new_target = _resource_by_id(newer_observation, proposal.target.resource_id)
    if old_target is None or new_target is None:
        return AttemptProgressResult(None, MiningStopReason.TARGET_IDENTITY_CHANGED)
    if new_target.interaction_region != old_target.interaction_region:
        return AttemptProgressResult(None, MiningStopReason.TARGET_IDENTITY_CHANGED)
    if new_target.available is None:
        return AttemptProgressResult(None, MiningStopReason.RESOURCE_UNKNOWN)
    depleted = old_target.available is True and new_target.available is False
    incremented = inventory_delta == 1

    if depleted and incremented:
        return AttemptProgressResult(
            AttemptProgress.RESOURCE_DEPLETED_AND_INVENTORY_INCREMENTED,
            MiningStopReason.NONE,
        )
    if depleted:
        return AttemptProgressResult(AttemptProgress.RESOURCE_DEPLETED, MiningStopReason.NONE)
    if incremented:
        return AttemptProgressResult(AttemptProgress.INVENTORY_INCREMENTED, MiningStopReason.NONE)
    return AttemptProgressResult(None, MiningStopReason.NO_OBSERVED_PROGRESS)


class MiningOnlyPhase(StrEnum):
    READY = "ready"
    AWAITING_ATTEMPT_RECEIPT = "awaiting-attempt-receipt"
    AWAITING_NEWER_OBSERVATION = "awaiting-newer-observation"
    COMPLETE_FULL = "complete-full"
    STOPPED = "stopped"


@final
@dataclass(frozen=True, slots=True)
class MiningOnlySession:
    phase: MiningOnlyPhase
    observation: ReleasedMiningObservation | None
    attempt_count: int
    pending_proposal: MiningAttemptProposal | None = None
    pending_receipt: MiningAttemptReceipt | None = None
    stop_reason: MiningStopReason = MiningStopReason.NONE

    def __init_subclass__(cls) -> None:
        raise TypeError("MiningOnlySession is sealed")

    def __post_init__(self) -> None:
        if type(self.phase) is not MiningOnlyPhase:
            raise ValueError("phase must be exact")
        if self.observation is not None and type(self.observation) is not ReleasedMiningObservation:
            raise ValueError("observation must be exact or None")
        if (
            not isinstance(self.attempt_count, int)
            or isinstance(self.attempt_count, bool)
            or not 0 <= self.attempt_count <= MAX_MINING_ATTEMPTS
        ):
            raise ValueError("attempt_count must be between 0 and 28")
        if (
            self.pending_proposal is not None
            and type(self.pending_proposal) is not MiningAttemptProposal
        ):
            raise ValueError("pending_proposal must be exact or None")
        if (
            self.pending_receipt is not None
            and type(self.pending_receipt) is not MiningAttemptReceipt
        ):
            raise ValueError("pending_receipt must be exact or None")
        if type(self.stop_reason) is not MiningStopReason:
            raise ValueError("stop_reason must be exact")
        if self.phase is MiningOnlyPhase.STOPPED and self.stop_reason is MiningStopReason.NONE:
            raise ValueError("stopped session requires a reason")
        if (
            self.phase is not MiningOnlyPhase.STOPPED
            and self.stop_reason is not MiningStopReason.NONE
        ):
            raise ValueError("non-stopped session cannot carry a stop reason")

    @classmethod
    def start(cls, observation: ReleasedMiningObservation) -> MiningOnlySession:
        if type(observation) is not ReleasedMiningObservation:
            raise ValueError("observation must be exact")
        if observation.is_full:
            return cls(MiningOnlyPhase.COMPLETE_FULL, observation, 0)
        return cls(MiningOnlyPhase.READY, observation, 0)

    @classmethod
    def stopped(cls, reason: MiningStopReason, attempt_count: int = 0) -> MiningOnlySession:
        if reason is MiningStopReason.NONE:
            raise ValueError("stopped session requires a reason")
        return cls(MiningOnlyPhase.STOPPED, None, attempt_count, stop_reason=reason)

    def plan(self, *, attempt_id: str, reviewed_execution_sha: str) -> MiningOnlySession:
        if self.phase is not MiningOnlyPhase.READY or self.observation is None:
            return MiningOnlySession.stopped(
                MiningStopReason.ATTEMPT_RECEIPT_INVALID,
                self.attempt_count,
            )
        if self.attempt_count >= MAX_MINING_ATTEMPTS:
            return MiningOnlySession.stopped(
                MiningStopReason.ATTEMPT_LIMIT_REACHED,
                self.attempt_count,
            )
        if self.observation.is_full:
            return MiningOnlySession(
                MiningOnlyPhase.COMPLETE_FULL,
                self.observation,
                self.attempt_count,
            )

        target = next(
            (
                resource
                for resource in self.observation.resources
                if resource.resource_type == "iron"
                and resource.available is True
                and resource.interaction_region is not None
            ),
            None,
        )
        if target is None:
            return MiningOnlySession.stopped(MiningStopReason.NO_AVAILABLE_IRON, self.attempt_count)
        proposal = MiningAttemptProposal(
            attempt_id=attempt_id,
            attempt_ordinal=self.attempt_count + 1,
            source_observation=self.observation,
            target=target,
            reviewed_execution_sha=reviewed_execution_sha,
        )
        return MiningOnlySession(
            MiningOnlyPhase.AWAITING_ATTEMPT_RECEIPT,
            self.observation,
            self.attempt_count,
            pending_proposal=proposal,
        )

    def accept_attempt_receipt(self, receipt: MiningAttemptReceipt) -> MiningOnlySession:
        if (
            self.phase is not MiningOnlyPhase.AWAITING_ATTEMPT_RECEIPT
            or self.observation is None
            or self.pending_proposal is None
            or type(receipt) is not MiningAttemptReceipt
        ):
            return MiningOnlySession.stopped(
                MiningStopReason.ATTEMPT_RECEIPT_INVALID,
                self.attempt_count,
            )
        proposal = self.pending_proposal
        if (
            receipt.attempt_id != proposal.attempt_id
            or receipt.attempt_ordinal != proposal.attempt_ordinal
            or receipt.source_epoch != proposal.source_observation.epoch
            or receipt.target_id != proposal.target.resource_id
            or receipt.target_region != proposal.target.interaction_region
            or receipt.reviewed_execution_sha != proposal.reviewed_execution_sha
            or receipt.role is not proposal.source_observation.role
        ):
            return MiningOnlySession.stopped(
                MiningStopReason.ATTEMPT_RECEIPT_INVALID,
                self.attempt_count,
            )
        if not receipt.dispatch_accepted:
            return MiningOnlySession.stopped(
                MiningStopReason.ATTEMPT_DISPATCH_FAILED,
                self.attempt_count + 1,
            )
        return MiningOnlySession(
            MiningOnlyPhase.AWAITING_NEWER_OBSERVATION,
            self.observation,
            self.attempt_count + 1,
            pending_proposal=proposal,
            pending_receipt=receipt,
        )

    def reobserve(self, newer_result: AssemblyResult) -> MiningOnlySession:
        if (
            self.phase is not MiningOnlyPhase.AWAITING_NEWER_OBSERVATION
            or self.pending_proposal is None
            or self.pending_receipt is None
            or type(newer_result) is not AssemblyResult
        ):
            return MiningOnlySession.stopped(
                MiningStopReason.NEWER_OBSERVATION_REQUIRED,
                self.attempt_count,
            )
        if newer_result.observation is None:
            return MiningOnlySession.stopped(newer_result.reason, self.attempt_count)
        newer_observation = newer_result.observation
        result = verify_attempt_progress(
            self.pending_proposal,
            self.pending_receipt,
            newer_observation,
        )
        if result.progress is None:
            return MiningOnlySession.stopped(result.reason, self.attempt_count)
        if newer_observation.is_full:
            return MiningOnlySession(
                MiningOnlyPhase.COMPLETE_FULL,
                newer_observation,
                self.attempt_count,
            )
        return MiningOnlySession(
            MiningOnlyPhase.READY,
            newer_observation,
            self.attempt_count,
        )
