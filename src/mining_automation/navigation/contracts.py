"""Typed contracts for deterministic, fixed-route checkpoint navigation.

The contracts in this module deliberately contain no route geometry and no
input capability.  They describe one ordered route, the evidence needed to
localize on it, and immutable route-local progress.  Production route truth is
expected to arrive later through separately validated evidence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from math import isfinite
from typing import Literal

from ..capture.frame import PixelFormat
from ..contracts import FrameRef

__all__ = [
    "ArrivalEvidence",
    "AttemptEvidenceRole",
    "Checkpoint",
    "CheckpointDetection",
    "CheckpointDetectorIdentity",
    "CheckpointEvidence",
    "CheckpointEvidenceRole",
    "CheckpointMatchKind",
    "CheckpointObservation",
    "CheckpointProfile",
    "CheckpointProfileIdentity",
    "CheckpointRole",
    "CheckpointSourceIdentity",
    "CompletedStepAttempt",
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
    "Sha256Digest",
    "StepAttemptIdentity",
    "StepAttemptSourceIdentity",
    "SyntheticStepAttemptReceipt",
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


@dataclass(frozen=True, slots=True)
class Sha256Digest:
    """Exact lowercase SHA-256 used by navigation evidence contracts."""

    value: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.value, str)
            or len(self.value) != 64
            or any(character not in "0123456789abcdef" for character in self.value)
        ):
            raise ValueError("SHA-256 must be exactly 64 lowercase hexadecimal characters")

    @classmethod
    def from_bytes(cls, payload: bytes) -> Sha256Digest:
        if not isinstance(payload, bytes):
            raise ValueError("SHA-256 payload must be immutable bytes")
        return cls(sha256(payload).hexdigest())


class CheckpointEvidenceRole(StrEnum):
    """Closed evidence role for this offline-only architecture branch."""

    SYNTHETIC_ARCHITECTURE_TEST_ONLY = "synthetic_checkpoint_architecture_test_only"


class AttemptEvidenceRole(StrEnum):
    """Closed receipt role; this branch cannot attest to real physical input."""

    SYNTHETIC_ARCHITECTURE_TEST_ONLY = "synthetic_attempt_receipt_test_only"


@dataclass(frozen=True, slots=True)
class CheckpointDetectorIdentity:
    detector_id: str
    version: str

    def __post_init__(self) -> None:
        _require_identifier(self.detector_id, "checkpoint detector id")
        _require_identifier(self.version, "checkpoint detector version")


@dataclass(frozen=True, slots=True)
class CheckpointProfileIdentity:
    profile_id: str
    version: str
    content_sha256: Sha256Digest

    def __post_init__(self) -> None:
        _require_identifier(self.profile_id, "checkpoint profile id")
        _require_identifier(self.version, "checkpoint profile version")
        if not isinstance(self.content_sha256, Sha256Digest):
            raise ValueError("checkpoint profile content digest must be Sha256Digest")


@dataclass(frozen=True, slots=True)
class CheckpointProfile:
    """Route-independent detector profile with no geometry or input targets."""

    profile_id: str
    version: str
    evidence_role: CheckpointEvidenceRole
    frame_width: int
    frame_height: int
    pixel_format: PixelFormat
    checkpoint_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_identifier(self.profile_id, "checkpoint profile id")
        _require_identifier(self.version, "checkpoint profile version")
        if not isinstance(self.evidence_role, CheckpointEvidenceRole):
            raise ValueError("checkpoint profile evidence role must be CheckpointEvidenceRole")
        if not _is_integer(self.frame_width) or self.frame_width <= 0:
            raise ValueError("checkpoint profile frame width must be a positive integer")
        if not _is_integer(self.frame_height) or self.frame_height <= 0:
            raise ValueError("checkpoint profile frame height must be a positive integer")
        if not isinstance(self.pixel_format, PixelFormat):
            raise ValueError("checkpoint profile pixel format must be PixelFormat")
        if not isinstance(self.checkpoint_ids, tuple) or not self.checkpoint_ids:
            raise ValueError("checkpoint profile ids must be a non-empty tuple")
        for checkpoint_id in self.checkpoint_ids:
            _require_identifier(checkpoint_id, "checkpoint profile checkpoint id")
        if len(set(self.checkpoint_ids)) != len(self.checkpoint_ids):
            raise ValueError("checkpoint profile checkpoint ids must be unique")

    @property
    def identity(self) -> CheckpointProfileIdentity:
        canonical = json.dumps(
            {
                "checkpoint_ids": self.checkpoint_ids,
                "evidence_role": self.evidence_role.value,
                "frame_height": self.frame_height,
                "frame_width": self.frame_width,
                "pixel_format": self.pixel_format.value,
                "profile_id": self.profile_id,
                "version": self.version,
            },
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii") + b"\n"
        return CheckpointProfileIdentity(
            self.profile_id,
            self.version,
            Sha256Digest.from_bytes(canonical),
        )


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
    AWAITING_ATTEMPT_RECEIPT = "awaiting_attempt_receipt"
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
    ATTEMPT_RECEIPT_REQUIRED = "attempt_receipt_required"
    ATTEMPT_RECEIPT_NOT_EXPECTED = "attempt_receipt_not_expected"
    ATTEMPT_RECEIPT_SOURCE_MISMATCH = "attempt_receipt_source_mismatch"
    ATTEMPT_STEP_MISMATCH = "attempt_step_mismatch"
    ATTEMPT_ID_MISMATCH = "attempt_id_mismatch"
    ATTEMPT_PREPARATION_MISMATCH = "attempt_preparation_mismatch"
    DUPLICATE_ATTEMPT_ID = "duplicate_attempt_id"
    DUPLICATE_ATTEMPT_RECEIPT = "duplicate_attempt_receipt"
    INVALID_ATTEMPT_TIME = "invalid_attempt_time"
    ATTEMPT_NOT_AFTER_PREPARATION = "attempt_not_after_preparation"
    STALE_ATTEMPT_RECEIPT = "stale_attempt_receipt"
    OUT_OF_ORDER_EVALUATION = "out_of_order_evaluation"


class NavigationTransitionOutcome(StrEnum):
    CHECKPOINT_ACCEPTED = "checkpoint_accepted"
    STEP_PREPARED = "step_prepared"
    STEP_ATTEMPT_RECORDED = "step_attempt_recorded"
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
class StepAttemptIdentity:
    route: RouteIdentity
    step_id: str
    attempt_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.route, RouteIdentity):
            raise ValueError("step attempt route must be RouteIdentity")
        _require_identifier(self.step_id, "step attempt step id")
        _require_identifier(self.attempt_id, "step attempt id")


@dataclass(frozen=True, slots=True)
class StepAttemptSourceIdentity:
    source_id: str
    version: str
    session_id: str
    evidence_role: AttemptEvidenceRole

    def __post_init__(self) -> None:
        _require_identifier(self.source_id, "step attempt source id")
        _require_identifier(self.version, "step attempt source version")
        _require_identifier(self.session_id, "step attempt source session id")
        if not isinstance(self.evidence_role, AttemptEvidenceRole):
            raise ValueError("step attempt source evidence role must be AttemptEvidenceRole")


@dataclass(frozen=True, slots=True)
class SyntheticStepAttemptReceipt:
    """Non-authoritative record that an offline attempt event occurred."""

    identity: StepAttemptIdentity
    source: StepAttemptSourceIdentity
    prepared_monotonic_s: float
    post_attempt_monotonic_s: float
    authoritative: Literal[False] = field(default=False, init=False)
    movement_success_proven: Literal[False] = field(default=False, init=False)
    live_input_enabled: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.identity, StepAttemptIdentity):
            raise ValueError("attempt receipt identity must be StepAttemptIdentity")
        if not isinstance(self.source, StepAttemptSourceIdentity):
            raise ValueError("attempt receipt source must be StepAttemptSourceIdentity")
        if (
            not _is_finite_number(self.prepared_monotonic_s)
            or self.prepared_monotonic_s < 0
        ):
            raise ValueError("receipt preparation boundary must be finite and non-negative")
        if (
            not _is_finite_number(self.post_attempt_monotonic_s)
            or self.post_attempt_monotonic_s <= self.prepared_monotonic_s
        ):
            raise ValueError("post-attempt boundary must be finite and after preparation")


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
    """Exact route-independent detector profile and capture stream identity."""

    detector: CheckpointDetectorIdentity
    profile: CheckpointProfile
    frame_source_id: str
    capture_session_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.detector, CheckpointDetectorIdentity):
            raise ValueError("checkpoint source detector must be CheckpointDetectorIdentity")
        if not isinstance(self.profile, CheckpointProfile):
            raise ValueError("checkpoint source profile must be CheckpointProfile")
        _require_identifier(self.frame_source_id, "frame_source_id")
        _require_identifier(self.capture_session_id, "capture_session_id")

    @property
    def detector_id(self) -> str:
        return self.detector.detector_id

    @property
    def detector_version(self) -> str:
        return self.detector.version

    @property
    def profile_identity(self) -> CheckpointProfileIdentity:
        return self.profile.identity

    @property
    def evidence_role(self) -> CheckpointEvidenceRole:
        return self.profile.evidence_role

    @property
    def frame_width(self) -> int:
        return self.profile.frame_width

    @property
    def frame_height(self) -> int:
        return self.profile.frame_height

    @property
    def pixel_format(self) -> PixelFormat:
        return self.profile.pixel_format


@dataclass(frozen=True, slots=True)
class FrameProvenance:
    source: CheckpointSourceIdentity
    frame: FrameRef
    pixel_format: PixelFormat
    frame_payload_sha256: Sha256Digest

    def __post_init__(self) -> None:
        if not isinstance(self.source, CheckpointSourceIdentity):
            raise ValueError("source must be a CheckpointSourceIdentity")
        if not isinstance(self.frame, FrameRef):
            raise ValueError("frame must be a FrameRef")
        if not _is_finite_number(self.frame.captured_monotonic_s):
            raise ValueError("checkpoint frame time must be finite and representable")
        if self.frame.frame_id < 1:
            raise ValueError("checkpoint evidence requires a positive captured frame id")
        if not isinstance(self.pixel_format, PixelFormat):
            raise ValueError("checkpoint evidence pixel format must be PixelFormat")
        if not isinstance(self.frame_payload_sha256, Sha256Digest):
            raise ValueError("checkpoint frame payload digest must be Sha256Digest")
        if (self.frame.width, self.frame.height) != (
            self.source.frame_width,
            self.source.frame_height,
        ):
            raise ValueError("frame geometry must match its declared checkpoint source")
        if self.pixel_format is not self.source.pixel_format:
            raise ValueError("frame pixel format must match its declared checkpoint source")


@dataclass(frozen=True, slots=True)
class CheckpointDetection:
    """Route-free detector classification for one checkpoint profile."""

    match: CheckpointMatchKind
    candidate_checkpoint_ids: tuple[str, ...]
    confidence: float

    def __post_init__(self) -> None:
        if not isinstance(self.match, CheckpointMatchKind):
            raise ValueError("checkpoint detection match must be CheckpointMatchKind")
        if not isinstance(self.candidate_checkpoint_ids, tuple):
            raise ValueError("candidate_checkpoint_ids must be a tuple")
        for checkpoint_id in self.candidate_checkpoint_ids:
            _require_identifier(checkpoint_id, "candidate checkpoint id")
        if len(set(self.candidate_checkpoint_ids)) != len(self.candidate_checkpoint_ids):
            raise ValueError("candidate checkpoint ids must be unique")
        candidate_count = len(self.candidate_checkpoint_ids)
        if self.match is CheckpointMatchKind.UNKNOWN and candidate_count != 0:
            raise ValueError("unknown detections cannot name checkpoint candidates")
        if self.match is CheckpointMatchKind.MATCHED and candidate_count != 1:
            raise ValueError("matched detections must name exactly one checkpoint")
        if self.match is CheckpointMatchKind.AMBIGUOUS and candidate_count < 2:
            raise ValueError("ambiguous detections must name at least two checkpoints")
        if not _is_finite_number(self.confidence) or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("detection confidence must be finite and between 0 and 1")

    @property
    def matched_checkpoint_id(self) -> str | None:
        if self.match is CheckpointMatchKind.MATCHED:
            return self.candidate_checkpoint_ids[0]
        return None


@dataclass(frozen=True, slots=True)
class CheckpointEvidence:
    """Digest-bound, route-free evidence returned by the guarded detector seam."""

    provenance: FrameProvenance
    detection: CheckpointDetection

    def __post_init__(self) -> None:
        if not isinstance(self.provenance, FrameProvenance):
            raise ValueError("checkpoint evidence provenance must be FrameProvenance")
        if not isinstance(self.detection, CheckpointDetection):
            raise ValueError("checkpoint evidence detection must be CheckpointDetection")
        allowed_ids = set(self.provenance.source.profile.checkpoint_ids)
        if any(
            checkpoint_id not in allowed_ids
            for checkpoint_id in self.detection.candidate_checkpoint_ids
        ):
            raise ValueError("checkpoint detection names an id outside its profile")


@dataclass(frozen=True, slots=True)
class CheckpointObservation:
    """One route binding around detector-owned checkpoint evidence."""

    route: RouteIdentity
    evidence: CheckpointEvidence

    def __post_init__(self) -> None:
        if not isinstance(self.route, RouteIdentity):
            raise ValueError("observation route must be a RouteIdentity")
        if not isinstance(self.evidence, CheckpointEvidence):
            raise ValueError("observation evidence must be CheckpointEvidence")

    @property
    def provenance(self) -> FrameProvenance:
        return self.evidence.provenance

    @property
    def match(self) -> CheckpointMatchKind:
        return self.evidence.detection.match

    @property
    def candidate_checkpoint_ids(self) -> tuple[str, ...]:
        return self.evidence.detection.candidate_checkpoint_ids

    @property
    def confidence(self) -> float:
        return self.evidence.detection.confidence

    @property
    def matched_checkpoint_id(self) -> str | None:
        return self.evidence.detection.matched_checkpoint_id


@dataclass(frozen=True, slots=True)
class NavigationPolicy:
    max_frame_age_s: float
    minimum_confidence: float
    max_attempt_receipt_age_s: float

    def __post_init__(self) -> None:
        if not _is_finite_number(self.max_frame_age_s) or self.max_frame_age_s <= 0:
            raise ValueError("max_frame_age_s must be finite and positive")
        if not _is_finite_number(self.minimum_confidence) or not 0.0 < self.minimum_confidence <= 1.0:
            raise ValueError("minimum_confidence must be finite, positive, and at most 1")
        if (
            not _is_finite_number(self.max_attempt_receipt_age_s)
            or self.max_attempt_receipt_age_s <= 0
        ):
            raise ValueError("max_attempt_receipt_age_s must be finite and positive")


@dataclass(frozen=True, slots=True)
class RouteEvaluationContext:
    plan: RoutePlan
    expected_source: CheckpointSourceIdentity
    expected_attempt_source: StepAttemptSourceIdentity
    policy: NavigationPolicy

    def __post_init__(self) -> None:
        if not isinstance(self.plan, RoutePlan):
            raise ValueError("plan must be a RoutePlan")
        if not isinstance(self.expected_source, CheckpointSourceIdentity):
            raise ValueError("expected_source must be a CheckpointSourceIdentity")
        if not isinstance(self.expected_attempt_source, StepAttemptSourceIdentity):
            raise ValueError("expected_attempt_source must be StepAttemptSourceIdentity")
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
    attempt_identity: StepAttemptIdentity
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
        if (
            not isinstance(self.attempt_identity, StepAttemptIdentity)
            or self.attempt_identity.route != self.route
            or self.attempt_identity.step_id != self.step.step_id
        ):
            raise ValueError("step proposal attempt identity must bind its exact route and step")
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
class CompletedStepAttempt:
    """Auditable causal record for one prepared and source-receipted step."""

    proposal: OfflineStepProposal
    receipt: SyntheticStepAttemptReceipt
    recorded_monotonic_s: float

    def __post_init__(self) -> None:
        if not isinstance(self.proposal, OfflineStepProposal):
            raise ValueError("completed attempt proposal must be OfflineStepProposal")
        if not isinstance(self.receipt, SyntheticStepAttemptReceipt):
            raise ValueError("completed attempt receipt must be SyntheticStepAttemptReceipt")
        if (
            self.receipt.identity != self.proposal.attempt_identity
            or self.receipt.source != self.proposal.context.expected_attempt_source
            or self.receipt.prepared_monotonic_s
            != self.proposal.prepared_monotonic_s
        ):
            raise ValueError("completed attempt must bind one exact proposal and receipt")
        if (
            not _is_finite_number(self.recorded_monotonic_s)
            or self.recorded_monotonic_s < self.receipt.post_attempt_monotonic_s
            or self.recorded_monotonic_s
            - self.receipt.post_attempt_monotonic_s
            > self.proposal.context.policy.max_attempt_receipt_age_s
        ):
            raise ValueError("completed attempt must retain its fresh receipt evaluation time")

    @property
    def identity(self) -> StepAttemptIdentity:
        return self.receipt.identity


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
    completed_attempts: tuple[CompletedStepAttempt, ...] = ()
    pending_step_proposal: OfflineStepProposal | None = None
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
        if not isinstance(self.completed_attempts, tuple) or any(
            not isinstance(attempt, CompletedStepAttempt)
            for attempt in self.completed_attempts
        ):
            raise ValueError("completed attempts must be a tuple of causal attempt records")
        attempt_ids = tuple(
            attempt.identity.attempt_id for attempt in self.completed_attempts
        )
        if len(set(attempt_ids)) != len(attempt_ids):
            raise ValueError("completed attempt ids must be unique")
        for index, attempt in enumerate(self.completed_attempts):
            if (
                index >= len(self.context.plan.steps)
                or attempt.proposal.context != self.context
                or attempt.identity.route != self.route
                or attempt.identity.step_id != self.context.plan.steps[index].step_id
                or attempt.recorded_monotonic_s
                > self.last_transition_monotonic_s
            ):
                raise ValueError("completed attempts must follow the route's exact step order")
            if index > 0:
                previous = self.completed_attempts[index - 1]
                current_frame = attempt.proposal.checkpoint_evidence.provenance.frame
                previous_frame = previous.proposal.checkpoint_evidence.provenance.frame
                if (
                    current_frame.frame_id <= previous_frame.frame_id
                    or current_frame.captured_monotonic_s
                    <= previous.receipt.post_attempt_monotonic_s
                    or attempt.proposal.prepared_monotonic_s
                    <= previous.receipt.post_attempt_monotonic_s
                    or attempt.proposal.prepared_monotonic_s
                    < previous.recorded_monotonic_s
                ):
                    raise ValueError("completed attempts must preserve strict causal frame order")
        if self.pending_step_proposal is not None:
            pending = self.pending_step_proposal
            if (
                not isinstance(pending, OfflineStepProposal)
                or pending.context != self.context
                or pending.route != self.route
                or len(self.completed_attempts) >= len(self.context.plan.steps)
                or pending.step != self.context.plan.steps[len(self.completed_attempts)]
                or pending.attempt_identity.attempt_id in set(attempt_ids)
                or pending.prepared_monotonic_s != self.last_transition_monotonic_s
                or self.last_accepted_provenance
                != pending.checkpoint_evidence.provenance
            ):
                raise ValueError("pending proposal must be the route's exact new attempt")
            if self.completed_attempts:
                previous = self.completed_attempts[-1]
                pending_frame = pending.checkpoint_evidence.provenance.frame
                previous_frame = previous.proposal.checkpoint_evidence.provenance.frame
                if (
                    pending_frame.frame_id <= previous_frame.frame_id
                    or pending_frame.captured_monotonic_s
                    <= previous.receipt.post_attempt_monotonic_s
                    or pending.prepared_monotonic_s < previous.recorded_monotonic_s
                ):
                    raise ValueError("pending proposal must use strictly post-attempt evidence")
        if self.stop is not None and not isinstance(self.stop, NavigationStop):
            raise ValueError("stop must be a NavigationStop or None")

        if self.phase is NavigationPhase.AWAITING_CHECKPOINT:
            if (
                self.accepted_checkpoint_count >= checkpoint_count
                or len(self.completed_attempts) != self.accepted_checkpoint_count
                or self.current_checkpoint_id is not None
                or self.expected_next_checkpoint_id
                != self.context.plan.checkpoints[
                    self.accepted_checkpoint_count
                ].checkpoint_id
                or self.active_checkpoint_evidence is not None
                or self.pending_step_proposal is not None
                or self.arrival_evidence is not None
                or self.stop is not None
                or self.evidence_boundary_monotonic_s
                != (
                    self.last_transition_monotonic_s
                    if self.last_attempt_receipt is None
                    else self.last_attempt_receipt.post_attempt_monotonic_s
                )
                or (
                    self.completed_attempts
                    and self.last_accepted_provenance
                    != self.completed_attempts[-1].proposal.checkpoint_evidence.provenance
                )
            ):
                raise ValueError("awaiting progress may retain only the expected checkpoint and history")
        elif self.phase is NavigationPhase.READY_FOR_STEP:
            if (
                not 0 < self.accepted_checkpoint_count < checkpoint_count
                or len(self.completed_attempts) != self.accepted_checkpoint_count - 1
                or self.current_checkpoint_id
                != self.context.plan.checkpoints[
                    self.accepted_checkpoint_count - 1
                ].checkpoint_id
                or self.expected_next_checkpoint_id
                != self.context.plan.checkpoints[
                    self.accepted_checkpoint_count
                ].checkpoint_id
                or self.active_checkpoint_evidence is None
                or self.pending_step_proposal is not None
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
                or (
                    self.last_attempt_receipt is not None
                    and self.evidence_boundary_monotonic_s
                    != self.last_attempt_receipt.post_attempt_monotonic_s
                )
                or (
                    self.completed_attempts
                    and self.active_checkpoint_evidence.provenance.frame.frame_id
                    <= self.completed_attempts[
                        -1
                    ].proposal.checkpoint_evidence.provenance.frame.frame_id
                )
                or self.last_transition_monotonic_s
                - self.active_checkpoint_evidence.provenance.frame.captured_monotonic_s
                > self.context.policy.max_frame_age_s
            ):
                raise ValueError("ready progress requires active evidence for its current checkpoint")
        elif self.phase is NavigationPhase.AWAITING_ATTEMPT_RECEIPT:
            if (
                not 0 < self.accepted_checkpoint_count < checkpoint_count
                or len(self.completed_attempts) != self.accepted_checkpoint_count - 1
                or self.current_checkpoint_id is not None
                or self.expected_next_checkpoint_id
                != self.context.plan.checkpoints[
                    self.accepted_checkpoint_count
                ].checkpoint_id
                or self.active_checkpoint_evidence is not None
                or self.pending_step_proposal is None
                or self.pending_step_proposal.step.step_id
                != self.context.plan.steps[
                    self.accepted_checkpoint_count - 1
                ].step_id
                or self.arrival_evidence is not None
                or self.stop is not None
                or self.evidence_boundary_monotonic_s
                != self.last_transition_monotonic_s
            ):
                raise ValueError("awaiting receipt progress must retain one exact pending attempt")
        elif self.phase is NavigationPhase.ARRIVED:
            if (
                self.accepted_checkpoint_count != checkpoint_count
                or len(self.completed_attempts) != len(self.context.plan.steps)
                or self.current_checkpoint_id
                != self.context.plan.checkpoints[-1].checkpoint_id
                or self.expected_next_checkpoint_id is not None
                or self.active_checkpoint_evidence is not None
                or self.pending_step_proposal is not None
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
                or (
                    self.last_attempt_receipt is not None
                    and self.evidence_boundary_monotonic_s
                    != self.last_attempt_receipt.post_attempt_monotonic_s
                )
                or (
                    self.completed_attempts
                    and self.arrival_evidence.observation.provenance.frame.frame_id
                    <= self.completed_attempts[
                        -1
                    ].proposal.checkpoint_evidence.provenance.frame.frame_id
                )
                or self.last_transition_monotonic_s
                - self.arrival_evidence.observation.provenance.frame.captured_monotonic_s
                > self.context.policy.max_frame_age_s
            ):
                raise ValueError("arrived progress requires explicit terminal checkpoint evidence")
        elif (
            self.current_checkpoint_id is not None
            or self.expected_next_checkpoint_id is not None
            or self.active_checkpoint_evidence is not None
            or self.pending_step_proposal is not None
            or self.arrival_evidence is not None
            or self.stop is None
            or len(self.completed_attempts)
            < max(0, self.accepted_checkpoint_count - 1)
            or len(self.completed_attempts)
            > min(self.accepted_checkpoint_count, len(self.context.plan.steps))
        ):
            raise ValueError("stopped progress must clear location evidence and contain a reason")

    @property
    def route(self) -> RouteIdentity:
        return self.context.plan.identity

    @property
    def attempt_history(self) -> tuple[StepAttemptIdentity, ...]:
        return tuple(attempt.identity for attempt in self.completed_attempts)

    @property
    def last_attempt_receipt(self) -> SyntheticStepAttemptReceipt | None:
        return None if not self.completed_attempts else self.completed_attempts[-1].receipt

    @property
    def pending_attempt(self) -> StepAttemptIdentity | None:
        proposal = self.pending_step_proposal
        return None if proposal is None else proposal.attempt_identity

    @property
    def failure_reason(self) -> NavigationFailureReason | None:
        return None if self.stop is None else self.stop.reason


@dataclass(frozen=True, slots=True)
class NavigationTransition:
    outcome: NavigationTransitionOutcome
    progress: RouteProgress
    step_proposal: OfflineStepProposal | None = None
    attempt_receipt: SyntheticStepAttemptReceipt | None = None

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
                or self.progress.pending_step_proposal != self.step_proposal
            ):
                raise ValueError("step proposal must match the transition's route history")
        elif self.step_proposal is not None:
            raise ValueError("only a prepared-step transition may contain a proposal")
        if self.outcome is NavigationTransitionOutcome.STEP_ATTEMPT_RECORDED:
            if self.attempt_receipt is None:
                raise ValueError("a recorded-attempt transition requires a receipt")
            if (
                not isinstance(self.attempt_receipt, SyntheticStepAttemptReceipt)
                or self.progress.last_attempt_receipt != self.attempt_receipt
                or not self.progress.attempt_history
                or self.progress.attempt_history[-1] != self.attempt_receipt.identity
                or self.progress.evidence_boundary_monotonic_s
                != self.attempt_receipt.post_attempt_monotonic_s
            ):
                raise ValueError("attempt receipt must match the transition's route history")
        elif self.attempt_receipt is not None:
            raise ValueError("only a recorded-attempt transition may contain a receipt")
        expected_phase = {
            NavigationTransitionOutcome.CHECKPOINT_ACCEPTED: NavigationPhase.READY_FOR_STEP,
            NavigationTransitionOutcome.STEP_PREPARED: NavigationPhase.AWAITING_ATTEMPT_RECEIPT,
            NavigationTransitionOutcome.STEP_ATTEMPT_RECORDED: NavigationPhase.AWAITING_CHECKPOINT,
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
