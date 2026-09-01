"""Typed contracts for deterministic, fixed-route checkpoint navigation.

The contracts in this module deliberately contain no route geometry and no
input capability.  They describe one ordered route, the evidence needed to
localize on it, and immutable route-local progress.  Production route truth is
expected to arrive later through separately validated evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from typing import Literal

from ..contracts import FrameRef

__all__ = [
    "ArrivalEvidence",
    "Checkpoint",
    "CheckpointMatchKind",
    "CheckpointObservation",
    "CheckpointRole",
    "CheckpointSourceIdentity",
    "FrameProvenance",
    "NavigationFailureReason",
    "NavigationPhase",
    "NavigationPolicy",
    "NavigationStop",
    "NavigationTransition",
    "NavigationTransitionOutcome",
    "OfflineStepProposal",
    "RouteDirection",
    "RouteEndpoint",
    "RouteEndpointRole",
    "RouteEvaluationContext",
    "RouteIdentity",
    "RoutePlan",
    "RouteProgress",
    "RouteStep",
]


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return isfinite(float(value))
    except OverflowError:
        return False


def _require_identifier(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or not value.isprintable()
    ):
        raise ValueError(f"{field_name} must be a non-empty, trimmed, printable string")
    return value


class RouteDirection(StrEnum):
    """The two independent fixed-route directions modeled for constrained v1."""

    MINE_TO_BANK = "mine_to_bank"
    BANK_TO_MINE = "bank_to_mine"


class RouteEndpointRole(StrEnum):
    MINE = "mine"
    BANK = "bank"


class CheckpointRole(StrEnum):
    DEPARTURE = "departure"
    TRANSIT = "transit"
    ARRIVAL = "arrival"


class CheckpointMatchKind(StrEnum):
    UNKNOWN = "unknown"
    MATCHED = "matched"
    AMBIGUOUS = "ambiguous"


class NavigationPhase(StrEnum):
    AWAITING_CHECKPOINT = "awaiting_checkpoint"
    READY_FOR_STEP = "ready_for_step"
    ARRIVED = "arrived"
    STOPPED = "stopped"


class NavigationFailureReason(StrEnum):
    """Stable fail-closed reasons emitted by the route reducer."""

    CONTEXT_MISMATCH = "context_mismatch"
    PLAN_MISMATCH = "plan_mismatch"
    ROUTE_ID_MISMATCH = "route_id_mismatch"
    ROUTE_VERSION_MISMATCH = "route_version_mismatch"
    DIRECTION_MISMATCH = "direction_mismatch"
    PROVENANCE_MISMATCH = "provenance_mismatch"
    INVALID_FRAME_TIME = "invalid_frame_time"
    EVIDENCE_NOT_AFTER_BOUNDARY = "evidence_not_after_boundary"
    STALE_FRAME = "stale_frame"
    REPEATED_FRAME = "repeated_frame"
    OUT_OF_ORDER_FRAME = "out_of_order_frame"
    UNKNOWN_CHECKPOINT = "unknown_checkpoint"
    AMBIGUOUS_CHECKPOINT = "ambiguous_checkpoint"
    LOW_CONFIDENCE = "low_confidence"
    SKIPPED_CHECKPOINT = "skipped_checkpoint"
    OUT_OF_ORDER_CHECKPOINT = "out_of_order_checkpoint"
    UNEXPECTED_CHECKPOINT = "unexpected_checkpoint"
    STEP_NOT_READY = "step_not_ready"
    STEP_EVIDENCE_NOT_CONSUMED = "step_evidence_not_consumed"
    OUT_OF_ORDER_EVALUATION = "out_of_order_evaluation"


class NavigationTransitionOutcome(StrEnum):
    CHECKPOINT_ACCEPTED = "checkpoint_accepted"
    STEP_PREPARED = "step_prepared"
    ARRIVAL_CONFIRMED = "arrival_confirmed"
    STOPPED = "stopped"
    TERMINAL_NO_CHANGE = "terminal_no_change"


@dataclass(frozen=True, slots=True)
class RouteIdentity:
    route_id: str
    version: str
    direction: RouteDirection

    def __post_init__(self) -> None:
        _require_identifier(self.route_id, "route_id")
        _require_identifier(self.version, "route version")
        if not isinstance(self.direction, RouteDirection):
            raise ValueError("route direction must be a RouteDirection")


@dataclass(frozen=True, slots=True)
class RouteEndpoint:
    location_id: str
    role: RouteEndpointRole

    def __post_init__(self) -> None:
        _require_identifier(self.location_id, "location_id")
        if not isinstance(self.role, RouteEndpointRole):
            raise ValueError("endpoint role must be a RouteEndpointRole")


@dataclass(frozen=True, slots=True)
class Checkpoint:
    checkpoint_id: str
    role: CheckpointRole

    def __post_init__(self) -> None:
        _require_identifier(self.checkpoint_id, "checkpoint_id")
        if not isinstance(self.role, CheckpointRole):
            raise ValueError("checkpoint role must be a CheckpointRole")


@dataclass(frozen=True, slots=True)
class RouteStep:
    step_id: str
    from_checkpoint_id: str
    to_checkpoint_id: str

    def __post_init__(self) -> None:
        _require_identifier(self.step_id, "step_id")
        _require_identifier(self.from_checkpoint_id, "from_checkpoint_id")
        _require_identifier(self.to_checkpoint_id, "to_checkpoint_id")
        if self.from_checkpoint_id == self.to_checkpoint_id:
            raise ValueError("a route step must advance to a different checkpoint")


@dataclass(frozen=True, slots=True)
class RoutePlan:
    """One strictly linear, direction-specific fixed route.

    No reverse helper is provided.  The return direction must be represented by
    a separately versioned and separately validated plan.
    """

    identity: RouteIdentity
    origin: RouteEndpoint
    destination: RouteEndpoint
    checkpoints: tuple[Checkpoint, ...]
    steps: tuple[RouteStep, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.identity, RouteIdentity):
            raise ValueError("route identity must be a RouteIdentity")
        if not isinstance(self.origin, RouteEndpoint) or not isinstance(
            self.destination, RouteEndpoint
        ):
            raise ValueError("route origin and destination must be RouteEndpoint values")
        if self.origin.location_id == self.destination.location_id:
            raise ValueError("route origin and destination must be different locations")

        expected_roles = {
            RouteDirection.MINE_TO_BANK: (RouteEndpointRole.MINE, RouteEndpointRole.BANK),
            RouteDirection.BANK_TO_MINE: (RouteEndpointRole.BANK, RouteEndpointRole.MINE),
        }
        if (self.origin.role, self.destination.role) != expected_roles[self.identity.direction]:
            raise ValueError("route endpoints do not match the declared direction")

        if not isinstance(self.checkpoints, tuple) or len(self.checkpoints) < 2:
            raise ValueError("route checkpoints must be a tuple with departure and arrival")
        if any(not isinstance(checkpoint, Checkpoint) for checkpoint in self.checkpoints):
            raise ValueError("route checkpoints must contain Checkpoint values")
        checkpoint_ids = tuple(checkpoint.checkpoint_id for checkpoint in self.checkpoints)
        if len(set(checkpoint_ids)) != len(checkpoint_ids):
            raise ValueError("route checkpoint ids must be unique")
        expected_checkpoint_roles = (
            CheckpointRole.DEPARTURE,
            *(CheckpointRole.TRANSIT for _ in self.checkpoints[1:-1]),
            CheckpointRole.ARRIVAL,
        )
        if tuple(checkpoint.role for checkpoint in self.checkpoints) != expected_checkpoint_roles:
            raise ValueError("route checkpoints must be departure, transit..., arrival")

        if not isinstance(self.steps, tuple) or any(
            not isinstance(step, RouteStep) for step in self.steps
        ):
            raise ValueError("route steps must be a tuple of RouteStep values")
        if len(self.steps) != len(self.checkpoints) - 1:
            raise ValueError("route must contain exactly one step per adjacent checkpoint pair")
        if len({step.step_id for step in self.steps}) != len(self.steps):
            raise ValueError("route step ids must be unique")
        for index, step in enumerate(self.steps):
            if (
                step.from_checkpoint_id != checkpoint_ids[index]
                or step.to_checkpoint_id != checkpoint_ids[index + 1]
            ):
                raise ValueError("route steps must connect each adjacent checkpoint in order")

    def checkpoint_index(self, checkpoint_id: str) -> int | None:
        for index, checkpoint in enumerate(self.checkpoints):
            if checkpoint.checkpoint_id == checkpoint_id:
                return index
        return None

    def step_from(self, checkpoint_id: str) -> RouteStep | None:
        for step in self.steps:
            if step.from_checkpoint_id == checkpoint_id:
                return step
        return None


@dataclass(frozen=True, slots=True)
class CheckpointSourceIdentity:
    """Exact detector and capture stream expected for one navigation run."""

    detector_id: str
    detector_version: str
    frame_source_id: str
    capture_session_id: str
    frame_width: int
    frame_height: int

    def __post_init__(self) -> None:
        _require_identifier(self.detector_id, "detector_id")
        _require_identifier(self.detector_version, "detector_version")
        _require_identifier(self.frame_source_id, "frame_source_id")
        _require_identifier(self.capture_session_id, "capture_session_id")
        if not _is_integer(self.frame_width) or self.frame_width <= 0:
            raise ValueError("frame_width must be a positive integer")
        if not _is_integer(self.frame_height) or self.frame_height <= 0:
            raise ValueError("frame_height must be a positive integer")


@dataclass(frozen=True, slots=True)
class FrameProvenance:
    source: CheckpointSourceIdentity
    frame: FrameRef

    def __post_init__(self) -> None:
        if not isinstance(self.source, CheckpointSourceIdentity):
            raise ValueError("source must be a CheckpointSourceIdentity")
        if not isinstance(self.frame, FrameRef):
            raise ValueError("frame must be a FrameRef")
        if not _is_finite_number(self.frame.captured_monotonic_s):
            raise ValueError("checkpoint frame time must be finite and representable")
        if self.frame.frame_id < 1:
            raise ValueError("checkpoint evidence requires a positive captured frame id")
        if (self.frame.width, self.frame.height) != (
            self.source.frame_width,
            self.source.frame_height,
        ):
            raise ValueError("frame geometry must match its declared checkpoint source")


@dataclass(frozen=True, slots=True)
class CheckpointObservation:
    route: RouteIdentity
    provenance: FrameProvenance
    match: CheckpointMatchKind
    candidate_checkpoint_ids: tuple[str, ...]
    confidence: float

    def __post_init__(self) -> None:
        if not isinstance(self.route, RouteIdentity):
            raise ValueError("observation route must be a RouteIdentity")
        if not isinstance(self.provenance, FrameProvenance):
            raise ValueError("observation provenance must be FrameProvenance")
        if not isinstance(self.match, CheckpointMatchKind):
            raise ValueError("observation match must be a CheckpointMatchKind")
        if not isinstance(self.candidate_checkpoint_ids, tuple):
            raise ValueError("candidate_checkpoint_ids must be a tuple")
        for checkpoint_id in self.candidate_checkpoint_ids:
            _require_identifier(checkpoint_id, "candidate checkpoint id")
        if len(set(self.candidate_checkpoint_ids)) != len(self.candidate_checkpoint_ids):
            raise ValueError("candidate checkpoint ids must be unique")
        candidate_count = len(self.candidate_checkpoint_ids)
        if self.match is CheckpointMatchKind.UNKNOWN and candidate_count != 0:
            raise ValueError("unknown observations cannot name checkpoint candidates")
        if self.match is CheckpointMatchKind.MATCHED and candidate_count != 1:
            raise ValueError("matched observations must name exactly one checkpoint")
        if self.match is CheckpointMatchKind.AMBIGUOUS and candidate_count < 2:
            raise ValueError("ambiguous observations must name at least two checkpoints")
        if not _is_finite_number(self.confidence) or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("observation confidence must be finite and between 0 and 1")

    @property
    def matched_checkpoint_id(self) -> str | None:
        if self.match is CheckpointMatchKind.MATCHED:
            return self.candidate_checkpoint_ids[0]
        return None


@dataclass(frozen=True, slots=True)
class NavigationPolicy:
    max_frame_age_s: float
    minimum_confidence: float

    def __post_init__(self) -> None:
        if not _is_finite_number(self.max_frame_age_s) or self.max_frame_age_s <= 0:
            raise ValueError("max_frame_age_s must be finite and positive")
        if not _is_finite_number(self.minimum_confidence) or not 0.0 < self.minimum_confidence <= 1.0:
            raise ValueError("minimum_confidence must be finite, positive, and at most 1")


@dataclass(frozen=True, slots=True)
class RouteEvaluationContext:
    plan: RoutePlan
    expected_source: CheckpointSourceIdentity
    policy: NavigationPolicy

    def __post_init__(self) -> None:
        if not isinstance(self.plan, RoutePlan):
            raise ValueError("plan must be a RoutePlan")
        if not isinstance(self.expected_source, CheckpointSourceIdentity):
            raise ValueError("expected_source must be a CheckpointSourceIdentity")
        if not isinstance(self.policy, NavigationPolicy):
            raise ValueError("policy must be a NavigationPolicy")


@dataclass(frozen=True, slots=True)
class NavigationStop:
    reason: NavigationFailureReason

    def __post_init__(self) -> None:
        if not isinstance(self.reason, NavigationFailureReason):
            raise ValueError("stop reason must be a NavigationFailureReason")


@dataclass(frozen=True, slots=True)
class ArrivalEvidence:
    """Fresh terminal-checkpoint evidence, and nothing beyond it."""

    context: RouteEvaluationContext
    checkpoint: Checkpoint
    observation: CheckpointObservation
    supported_mining_view_proven: Literal[False] = field(default=False, init=False)
    bank_interface_open_proven: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.context, RouteEvaluationContext)
            or not isinstance(self.observation, CheckpointObservation)
            or self.observation.route != self.context.plan.identity
            or self.observation.provenance.source != self.context.expected_source
            or self.observation.confidence < self.context.policy.minimum_confidence
        ):
            raise ValueError("arrival evidence must match one evaluation context")
        if (
            not isinstance(self.checkpoint, Checkpoint)
            or self.checkpoint != self.context.plan.checkpoints[-1]
            or self.checkpoint.role is not CheckpointRole.ARRIVAL
        ):
            raise ValueError("arrival evidence requires the plan's terminal checkpoint")
        if self.observation.matched_checkpoint_id != self.checkpoint.checkpoint_id:
            raise ValueError("arrival observation must match the arrival checkpoint")

    @property
    def route(self) -> RouteIdentity:
        return self.context.plan.identity

    @property
    def destination(self) -> RouteEndpoint:
        return self.context.plan.destination


@dataclass(frozen=True, slots=True)
class OfflineStepProposal:
    """Data-only future execution seam with live input mechanically disabled."""

    context: RouteEvaluationContext
    step: RouteStep
    checkpoint_evidence: CheckpointObservation
    prepared_monotonic_s: float
    live_input_enabled: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.context, RouteEvaluationContext)
            or not isinstance(self.checkpoint_evidence, CheckpointObservation)
            or self.checkpoint_evidence.route != self.context.plan.identity
            or self.checkpoint_evidence.provenance.source != self.context.expected_source
            or self.checkpoint_evidence.confidence < self.context.policy.minimum_confidence
        ):
            raise ValueError("step proposal must match one evaluation context")
        if not isinstance(self.step, RouteStep) or self.step not in self.context.plan.steps:
            raise ValueError("step proposal must contain a step from its plan")
        if self.checkpoint_evidence.matched_checkpoint_id != self.step.from_checkpoint_id:
            raise ValueError("step proposal evidence must match the step departure checkpoint")
        if (
            not _is_finite_number(self.prepared_monotonic_s)
            or self.prepared_monotonic_s
            < self.checkpoint_evidence.provenance.frame.captured_monotonic_s
        ):
            raise ValueError("step preparation time must be finite and no earlier than its evidence")
        if (
            self.prepared_monotonic_s
            - self.checkpoint_evidence.provenance.frame.captured_monotonic_s
            > self.context.policy.max_frame_age_s
        ):
            raise ValueError("step proposal evidence must be fresh at preparation time")

    @property
    def route(self) -> RouteIdentity:
        return self.context.plan.identity


@dataclass(frozen=True, slots=True)
class RouteProgress:
    context: RouteEvaluationContext
    phase: NavigationPhase
    current_checkpoint_id: str | None
    expected_next_checkpoint_id: str | None
    accepted_checkpoint_count: int
    evidence_boundary_monotonic_s: float
    last_transition_monotonic_s: float
    last_accepted_provenance: FrameProvenance | None = None
    active_checkpoint_evidence: CheckpointObservation | None = None
    arrival_evidence: ArrivalEvidence | None = None
    stop: NavigationStop | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.context, RouteEvaluationContext):
            raise ValueError("progress context must be a RouteEvaluationContext")
        if not isinstance(self.phase, NavigationPhase):
            raise ValueError("progress phase must be a NavigationPhase")
        checkpoint_count = len(self.context.plan.checkpoints)
        if (
            not _is_integer(self.accepted_checkpoint_count)
            or not 0 <= self.accepted_checkpoint_count <= checkpoint_count
        ):
            raise ValueError("accepted checkpoint count must fit the route plan")
        if (
            not _is_finite_number(self.evidence_boundary_monotonic_s)
            or self.evidence_boundary_monotonic_s < 0
        ):
            raise ValueError("evidence boundary must be finite and non-negative")
        if (
            not _is_finite_number(self.last_transition_monotonic_s)
            or self.last_transition_monotonic_s < self.evidence_boundary_monotonic_s
        ):
            raise ValueError("last transition time must be finite and not precede the evidence boundary")
        for value, name in (
            (self.current_checkpoint_id, "current_checkpoint_id"),
            (self.expected_next_checkpoint_id, "expected_next_checkpoint_id"),
        ):
            if value is not None:
                _require_identifier(value, name)
        if self.last_accepted_provenance is not None and not isinstance(
            self.last_accepted_provenance, FrameProvenance
        ):
            raise ValueError("last accepted provenance must be FrameProvenance or None")
        if (self.accepted_checkpoint_count == 0) != (self.last_accepted_provenance is None):
            raise ValueError("accepted checkpoint count must agree with frame history")
        if self.last_accepted_provenance is not None and (
            self.last_accepted_provenance.source != self.context.expected_source
            or self.last_accepted_provenance.frame.captured_monotonic_s
            > self.last_transition_monotonic_s
        ):
            raise ValueError("accepted frame history must match the context and transition time")
        if self.stop is not None and not isinstance(self.stop, NavigationStop):
            raise ValueError("stop must be a NavigationStop or None")

        if self.phase is NavigationPhase.AWAITING_CHECKPOINT:
            if (
                self.accepted_checkpoint_count >= checkpoint_count
                or self.current_checkpoint_id is not None
                or self.expected_next_checkpoint_id
                != self.context.plan.checkpoints[
                    self.accepted_checkpoint_count
                ].checkpoint_id
                or self.active_checkpoint_evidence is not None
                or self.arrival_evidence is not None
                or self.stop is not None
                or self.evidence_boundary_monotonic_s
                != self.last_transition_monotonic_s
            ):
                raise ValueError("awaiting progress may retain only the expected checkpoint and history")
        elif self.phase is NavigationPhase.READY_FOR_STEP:
            if (
                not 0 < self.accepted_checkpoint_count < checkpoint_count
                or self.current_checkpoint_id
                != self.context.plan.checkpoints[
                    self.accepted_checkpoint_count - 1
                ].checkpoint_id
                or self.expected_next_checkpoint_id
                != self.context.plan.checkpoints[
                    self.accepted_checkpoint_count
                ].checkpoint_id
                or self.active_checkpoint_evidence is None
                or not isinstance(self.active_checkpoint_evidence, CheckpointObservation)
                or self.arrival_evidence is not None
                or self.stop is not None
                or self.active_checkpoint_evidence.matched_checkpoint_id
                != self.current_checkpoint_id
                or self.active_checkpoint_evidence.route != self.route
                or self.last_accepted_provenance
                != self.active_checkpoint_evidence.provenance
                or self.active_checkpoint_evidence.confidence
                < self.context.policy.minimum_confidence
                or self.active_checkpoint_evidence.provenance.frame.captured_monotonic_s
                <= self.evidence_boundary_monotonic_s
                or self.last_transition_monotonic_s
                - self.active_checkpoint_evidence.provenance.frame.captured_monotonic_s
                > self.context.policy.max_frame_age_s
            ):
                raise ValueError("ready progress requires active evidence for its current checkpoint")
        elif self.phase is NavigationPhase.ARRIVED:
            if (
                self.accepted_checkpoint_count != checkpoint_count
                or self.current_checkpoint_id
                != self.context.plan.checkpoints[-1].checkpoint_id
                or self.expected_next_checkpoint_id is not None
                or self.active_checkpoint_evidence is not None
                or self.arrival_evidence is None
                or not isinstance(self.arrival_evidence, ArrivalEvidence)
                or self.stop is not None
                or self.current_checkpoint_id
                != self.arrival_evidence.checkpoint.checkpoint_id
                or self.arrival_evidence.route != self.route
                or self.arrival_evidence.context != self.context
                or self.last_accepted_provenance
                != self.arrival_evidence.observation.provenance
                or self.arrival_evidence.observation.provenance.frame.captured_monotonic_s
                <= self.evidence_boundary_monotonic_s
                or self.last_transition_monotonic_s
                - self.arrival_evidence.observation.provenance.frame.captured_monotonic_s
                > self.context.policy.max_frame_age_s
            ):
                raise ValueError("arrived progress requires explicit terminal checkpoint evidence")
        elif (
            self.current_checkpoint_id is not None
            or self.expected_next_checkpoint_id is not None
            or self.active_checkpoint_evidence is not None
            or self.arrival_evidence is not None
            or self.stop is None
        ):
            raise ValueError("stopped progress must clear location evidence and contain a reason")

    @property
    def route(self) -> RouteIdentity:
        return self.context.plan.identity

    @property
    def failure_reason(self) -> NavigationFailureReason | None:
        return None if self.stop is None else self.stop.reason


@dataclass(frozen=True, slots=True)
class NavigationTransition:
    outcome: NavigationTransitionOutcome
    progress: RouteProgress
    step_proposal: OfflineStepProposal | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, NavigationTransitionOutcome):
            raise ValueError("transition outcome must be a NavigationTransitionOutcome")
        if not isinstance(self.progress, RouteProgress):
            raise ValueError("transition progress must be RouteProgress")
        if self.outcome is NavigationTransitionOutcome.STEP_PREPARED:
            if self.step_proposal is None:
                raise ValueError("a prepared-step transition requires a proposal")
            if (
                not isinstance(self.step_proposal, OfflineStepProposal)
                or self.step_proposal.context != self.progress.context
                or self.step_proposal.checkpoint_evidence.provenance
                != self.progress.last_accepted_provenance
                or self.step_proposal.step.to_checkpoint_id
                != self.progress.expected_next_checkpoint_id
                or self.step_proposal.prepared_monotonic_s
                != self.progress.evidence_boundary_monotonic_s
            ):
                raise ValueError("step proposal must match the transition's route history")
        elif self.step_proposal is not None:
            raise ValueError("only a prepared-step transition may contain a proposal")
        expected_phase = {
            NavigationTransitionOutcome.CHECKPOINT_ACCEPTED: NavigationPhase.READY_FOR_STEP,
            NavigationTransitionOutcome.STEP_PREPARED: NavigationPhase.AWAITING_CHECKPOINT,
            NavigationTransitionOutcome.ARRIVAL_CONFIRMED: NavigationPhase.ARRIVED,
            NavigationTransitionOutcome.STOPPED: NavigationPhase.STOPPED,
        }.get(self.outcome)
        if expected_phase is not None and self.progress.phase is not expected_phase:
            raise ValueError("transition outcome does not match progress phase")
        if (
            self.outcome is NavigationTransitionOutcome.TERMINAL_NO_CHANGE
            and self.progress.phase not in {NavigationPhase.ARRIVED, NavigationPhase.STOPPED}
        ):
            raise ValueError("terminal no-change requires terminal progress")
