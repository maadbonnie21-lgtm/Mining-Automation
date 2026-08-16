from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Mapping, Sequence


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


@dataclass(frozen=True, slots=True)
class Observation:
    kind: str
    frame: FrameRef
    confidence: float
    evidence: Mapping[str, Any] = field(default_factory=dict)
    detector_version: str = "unknown"

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")


@dataclass(frozen=True, slots=True)
class ResourceState:
    resource_id: str
    resource_type: str
    available: bool | None
    confidence: float
    interaction_region: tuple[int, int, int, int] | None = None


@dataclass(frozen=True, slots=True)
class InventoryState:
    occupied_slots: int | None
    capacity: int = 28
    confidence: float = 0.0

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


@dataclass(frozen=True, slots=True)
class RoutineSegment:
    kind: RoutineKind
    duration_s: float
    label: str = ""

    def __post_init__(self) -> None:
        if self.duration_s <= 0:
            raise ValueError("routine segment duration must be positive")


@dataclass(frozen=True, slots=True)
class SessionConfig:
    ore_id: str
    mine_id: str
    world: int
    routine: Sequence[RoutineSegment]


@dataclass(frozen=True, slots=True)
class ActionIntent:
    action_id: str
    kind: str
    target_id: str | None
    interaction_region: tuple[int, int, int, int] | None
    timeout_s: float
    expected_observation_kinds: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


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
