from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum, auto
from math import isfinite
from typing import Any


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_finite_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and isfinite(value)


def _validate_confidence(confidence: float) -> None:
    if not _is_finite_number(confidence) or not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence must be finite and between 0.0 and 1.0 inclusive")


def _validate_interaction_region(
    interaction_region: tuple[int, int, int, int] | None,
) -> None:
    if interaction_region is None:
        return
    if (
        not isinstance(interaction_region, tuple)
        or len(interaction_region) != 4
        or any(not _is_integer(component) for component in interaction_region)
    ):
        raise ValueError("interaction_region must be a tuple of four integers")
    if interaction_region[2] <= 0 or interaction_region[3] <= 0:
        raise ValueError("interaction_region width and height must be positive")


class ConfidenceBand(Enum):
    UNKNOWN = auto()
    LOW = auto()
    MEDIUM = auto()
    HIGH = auto()


class SessionState(Enum):
    IDLE = auto()
    ACQUIRING = auto()
    MINING = auto()
    NAVIGATING_TO_BANK = auto()
    BANKING = auto()
    NAVIGATING_TO_MINE = auto()
    BREAK = auto()
    RECOVERING = auto()
    PAUSED = auto()
    STOPPING = auto()
    STOPPED = auto()
    ERROR = auto()


class RoutineKind(Enum):
    ACTIVE = auto()
    INACTIVE = auto()


class ActionStatus(Enum):
    PLANNED = auto()
    ATTEMPTED = auto()
    VERIFIED_SUCCESS = auto()
    VERIFIED_FAILURE = auto()
    TIMED_OUT = auto()


@dataclass(frozen=True, slots=True)
class FrameRef:
    frame_id: int
    captured_monotonic_s: float
    width: int
    height: int

    def __post_init__(self) -> None:
        if not _is_integer(self.frame_id) or self.frame_id < 0:
            raise ValueError("frame_id must be a non-negative integer")
        if not _is_finite_number(self.captured_monotonic_s) or self.captured_monotonic_s < 0:
            raise ValueError("captured_monotonic_s must be finite and non-negative")
        if not _is_integer(self.width) or self.width <= 0:
            raise ValueError("frame width must be a positive integer")
        if not _is_integer(self.height) or self.height <= 0:
            raise ValueError("frame height must be a positive integer")


@dataclass(frozen=True, slots=True)
class Observation:
    kind: str
    frame: FrameRef
    confidence: float
    evidence: Mapping[str, Any] = field(default_factory=dict)
    detector_version: str = "unknown"

    def __post_init__(self) -> None:
        _validate_confidence(self.confidence)


@dataclass(frozen=True, slots=True)
class ResourceState:
    resource_id: str
    resource_type: str
    available: bool | None
    confidence: float
    interaction_region: tuple[int, int, int, int] | None = None

    def __post_init__(self) -> None:
        _validate_confidence(self.confidence)
        _validate_interaction_region(self.interaction_region)


@dataclass(frozen=True, slots=True)
class InventoryState:
    occupied_slots: int | None
    capacity: int = 28
    confidence: float = 0.0

    def __post_init__(self) -> None:
        if not _is_integer(self.capacity) or self.capacity <= 0:
            raise ValueError("inventory capacity must be a positive integer")
        if not _is_integer(self.occupied_slots) and self.occupied_slots is not None:
            raise ValueError("occupied_slots must be an integer or None")
        if self.occupied_slots is not None and not 0 <= self.occupied_slots <= self.capacity:
            raise ValueError("occupied_slots must be between 0 and capacity inclusive")
        _validate_confidence(self.confidence)

    @property
    def is_full(self) -> bool | None:
        if self.occupied_slots is None:
            return None
        return self.occupied_slots >= self.capacity


@dataclass(frozen=True, slots=True)
class LocationEstimate:
    location_id: str | None
    checkpoint_id: str | None = None
    confidence: float = 0.0

    def __post_init__(self) -> None:
        _validate_confidence(self.confidence)


@dataclass(frozen=True, slots=True)
class RoutineSegment:
    kind: RoutineKind
    duration_s: float
    label: str = ""

    def __post_init__(self) -> None:
        if not _is_finite_number(self.duration_s) or self.duration_s <= 0:
            raise ValueError("routine segment duration must be finite and positive")


@dataclass(frozen=True, slots=True)
class SessionConfig:
    ore_id: str
    mine_id: str
    world: int
    routine: Sequence[RoutineSegment]

    def __post_init__(self) -> None:
        if not isinstance(self.ore_id, str) or not self.ore_id.strip():
            raise ValueError("ore_id must be a non-empty string")
        if not isinstance(self.mine_id, str) or not self.mine_id.strip():
            raise ValueError("mine_id must be a non-empty string")
        if not _is_integer(self.world) or not 301 <= self.world <= 999:
            raise ValueError("world must be an integer between 301 and 999 inclusive")
        if not self.routine:
            raise ValueError("routine must contain at least one segment")


@dataclass(frozen=True, slots=True)
class ActionIntent:
    action_id: str
    kind: str
    target_id: str | None
    interaction_region: tuple[int, int, int, int] | None
    timeout_s: float
    expected_observation_kinds: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_interaction_region(self.interaction_region)
        if not _is_finite_number(self.timeout_s) or self.timeout_s <= 0:
            raise ValueError("action timeout must be finite and positive")


@dataclass(frozen=True, slots=True)
class ActionResult:
    action_id: str
    status: ActionStatus
    reason: str
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WorldState:
    session_state: SessionState = SessionState.IDLE
    config: SessionConfig | None = None
    location: LocationEstimate = field(default_factory=lambda: LocationEstimate(None))
    inventory: InventoryState = field(default_factory=lambda: InventoryState(None))
    resources: dict[str, ResourceState] = field(default_factory=dict)
    current_objective: str | None = None
    expected_event: str | None = None
    recovery_reason: str | None = None
    last_observation_monotonic_s: float | None = None

    def available_resources(self, *, min_confidence: float = 0.8) -> list[ResourceState]:
        return [
            resource
            for resource in self.resources.values()
            if resource.available is True and resource.confidence >= min_confidence
        ]
