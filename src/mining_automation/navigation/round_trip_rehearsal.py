"""Deterministic two-direction synthetic route rehearsal.

This terminal evaluator composes the two exact, independently bound B1 route
results.  It has no retry, recovery, controller, world-state, or input surface.
Two individually successful legs form a round trip only when the return leg's
departure evidence was captured strictly after the outbound arrival was
accepted.
"""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field, replace
from enum import StrEnum
from math import copysign, isfinite
from typing import Final, Literal

from .contracts import (
    NavigationFailureReason,
    RouteDirection,
    RouteEndpoint,
    RouteEndpointRole,
    RouteIdentity,
    Sha256Digest,
)
from .offline_route_session import (
    OfflineRouteSessionPhase,
    OfflineRouteSessionResult,
    OfflineRouteSessionStopReason,
)
from .release_decision import (
    DirectionProductionBinding,
    NavigationReleaseDecision,
    ReviewerDecisionSummary,
    _copy_direction_production_binding,
    _owned_result,
    _post_attempt_binding,
    _require_result_binding,
    _snapshot_navigation_release_decision,
)
from .route_evidence import RouteEvidenceIntegrityError, canonical_route_evidence_bytes

__all__ = [
    "SYNTHETIC_ROUND_TRIP_REHEARSAL_ROLE",
    "SyntheticEndpointArrival",
    "SyntheticEndpointHandoff",
    "SyntheticRoundTripLeg",
    "SyntheticRoundTripPhase",
    "SyntheticRoundTripRehearsalReport",
    "SyntheticRoundTripStopReason",
    "SyntheticRoundTripTimelineExpectation",
    "evaluate_synthetic_round_trip_rehearsal",
]

SYNTHETIC_ROUND_TRIP_REHEARSAL_ROLE: Final[str] = (
    "synthetic_two_direction_round_trip_rehearsal_only"
)

_LEG_SCHEMA: Final[str] = "fixed-route-synthetic-round-trip-leg-v1"
_HANDOFF_SCHEMA: Final[str] = "fixed-route-synthetic-endpoint-handoff-v1"
_ARRIVAL_SCHEMA: Final[str] = "fixed-route-synthetic-endpoint-arrival-v1"
_REPORT_SCHEMA: Final[str] = "fixed-route-synthetic-round-trip-report-v1"
_TIMELINE_SCHEMA: Final[str] = "fixed-route-synthetic-round-trip-timeline-v1"
_FACTORY_TOKEN: Final[object] = object()


def _is_nonnegative_time(value: object) -> bool:
    return bool(
        type(value) is float
        and isfinite(value)
        and value >= 0.0
        and not (value == 0.0 and copysign(1.0, value) < 0.0)
    )


def _route_json(route: RouteIdentity) -> dict[str, str]:
    return {
        "direction": route.direction.value,
        "route_id": route.route_id,
        "version": route.version,
    }


def _endpoint_json(endpoint: RouteEndpoint) -> dict[str, str]:
    return {
        "location_id": endpoint.location_id,
        "role": endpoint.role.value,
    }


def _identifier(value: object, field_name: str) -> str:
    if type(value) is not str or not value or value != value.strip() or not value.isprintable():
        raise RouteEvidenceIntegrityError(f"{field_name} is invalid")
    return value


class SyntheticRoundTripPhase(StrEnum):
    COMPLETED = "completed"
    STOPPED = "stopped"


class SyntheticRoundTripStopReason(StrEnum):
    MINE_TO_BANK_EVIDENCE_NOT_APPROVED = "mine_to_bank_evidence_not_approved"
    MINE_TO_BANK_NOT_ARRIVED = "mine_to_bank_not_arrived"
    BANK_TO_MINE_EVIDENCE_NOT_APPROVED = "bank_to_mine_evidence_not_approved"
    BANK_TO_MINE_DEPARTURE_NOT_FRESH = "bank_to_mine_departure_not_fresh"
    BANK_TO_MINE_NOT_ARRIVED = "bank_to_mine_not_arrived"


@dataclass(frozen=True, slots=True)
class SyntheticRoundTripTimelineExpectation:
    """Caller-owned assertion that both pinned results share one synthetic timeline."""

    timeline_id: str
    release_decision_sha256: Sha256Digest
    mine_to_bank_route_session_id: str
    mine_to_bank_session_result_sha256: Sha256Digest
    bank_to_mine_route_session_id: str
    bank_to_mine_session_result_sha256: Sha256Digest
    synthetic_numeric_timeline_only: Literal[True] = field(default=True, init=False)
    real_monotonic_clock_attested: Literal[False] = field(default=False, init=False)
    release_authority: Literal[False] = field(default=False, init=False)
    activation_allowed: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        _identifier(self.timeline_id, "synthetic round-trip timeline id")
        _identifier(
            self.mine_to_bank_route_session_id,
            "synthetic timeline mine_to_bank route session id",
        )
        _identifier(
            self.bank_to_mine_route_session_id,
            "synthetic timeline bank_to_mine route session id",
        )
        if self.mine_to_bank_route_session_id == self.bank_to_mine_route_session_id:
            raise RouteEvidenceIntegrityError(
                "synthetic round-trip directions require distinct route sessions"
            )
        for digest in (
            self.release_decision_sha256,
            self.mine_to_bank_session_result_sha256,
            self.bank_to_mine_session_result_sha256,
        ):
            if type(digest) is not Sha256Digest:
                raise RouteEvidenceIntegrityError("synthetic timeline digest is invalid")
        if (
            self.synthetic_numeric_timeline_only is not True
            or self.real_monotonic_clock_attested is not False
            or self.release_authority is not False
            or self.activation_allowed is not False
            or self.input_authority is not False
        ):
            raise RouteEvidenceIntegrityError("synthetic timeline cannot carry authority")

    def to_json_value(self) -> dict[str, object]:
        if _snapshot_timeline_expectation(self) != self:
            raise RouteEvidenceIntegrityError("synthetic timeline changed after construction")
        return {
            "activation_allowed": self.activation_allowed,
            "bank_to_mine_route_session_id": self.bank_to_mine_route_session_id,
            "bank_to_mine_session_result_sha256": (self.bank_to_mine_session_result_sha256.value),
            "input_authority": self.input_authority,
            "mine_to_bank_route_session_id": self.mine_to_bank_route_session_id,
            "mine_to_bank_session_result_sha256": (self.mine_to_bank_session_result_sha256.value),
            "real_monotonic_clock_attested": self.real_monotonic_clock_attested,
            "release_authority": self.release_authority,
            "release_decision_sha256": self.release_decision_sha256.value,
            "schema": _TIMELINE_SCHEMA,
            "synthetic_numeric_timeline_only": self.synthetic_numeric_timeline_only,
            "timeline_id": self.timeline_id,
        }


@dataclass(frozen=True, slots=True)
class SyntheticRoundTripLeg:
    direction: RouteDirection
    route: RouteIdentity
    route_plan_sha256: Sha256Digest
    route_session_id: str
    session_result_sha256: Sha256Digest
    terminal_phase: OfflineRouteSessionPhase
    session_stop_reason: OfflineRouteSessionStopReason | None
    navigation_failure_reason: NavigationFailureReason | None
    durable_evidence_conformance_passed: bool
    reviewer_approved: bool
    durable_endpoint_arrival_verified: bool
    explicit_terminal_arrival_bound: bool
    synthetic_causality_conforms: bool
    _source_binding: DirectionProductionBinding = field(repr=False)
    _source_result: OfflineRouteSessionResult = field(repr=False)
    _factory_token: InitVar[object | None] = None
    automatic_retry_enabled: Literal[False] = field(default=False, init=False)
    live_navigation_enabled: Literal[False] = field(default=False, init=False)
    activation_allowed: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _FACTORY_TOKEN:
            raise RouteEvidenceIntegrityError("round-trip legs are evaluator-owned")
        if type(self.direction) is not RouteDirection:
            raise RouteEvidenceIntegrityError("round-trip leg direction is invalid")
        if type(self.route) is not RouteIdentity or self.route.direction is not self.direction:
            raise RouteEvidenceIntegrityError("round-trip leg route is invalid")
        if (
            type(self.route_plan_sha256) is not Sha256Digest
            or type(self.session_result_sha256) is not Sha256Digest
            or type(self.route_session_id) is not str
            or not self.route_session_id
        ):
            raise RouteEvidenceIntegrityError("round-trip leg identity is invalid")
        if type(self.terminal_phase) is not OfflineRouteSessionPhase:
            raise RouteEvidenceIntegrityError("round-trip leg phase is invalid")
        if self.terminal_phase is OfflineRouteSessionPhase.STOPPED:
            if (
                type(self.session_stop_reason) is not OfflineRouteSessionStopReason
                or type(self.navigation_failure_reason) is not NavigationFailureReason
            ):
                raise RouteEvidenceIntegrityError("stopped round-trip leg lost its reason")
        elif self.session_stop_reason is not None or self.navigation_failure_reason is not None:
            raise RouteEvidenceIntegrityError("non-stopped round-trip leg carries a stop reason")
        if (
            type(self.durable_evidence_conformance_passed) is not bool
            or type(self.reviewer_approved) is not bool
            or type(self.durable_endpoint_arrival_verified) is not bool
            or self.durable_endpoint_arrival_verified
            is not self.durable_evidence_conformance_passed
            or (self.durable_evidence_conformance_passed and not self.reviewer_approved)
        ):
            raise RouteEvidenceIntegrityError("round-trip durable evidence summary is invalid")
        if (
            type(self.explicit_terminal_arrival_bound) is not bool
            or type(self.synthetic_causality_conforms) is not bool
        ):
            raise RouteEvidenceIntegrityError("round-trip leg summaries must be booleans")
        expected_conformance = bool(
            self.terminal_phase is OfflineRouteSessionPhase.ARRIVED
            and self.session_stop_reason is None
            and self.navigation_failure_reason is None
            and self.explicit_terminal_arrival_bound
        )
        if self.synthetic_causality_conforms is not expected_conformance:
            raise RouteEvidenceIntegrityError("round-trip leg conformance is inconsistent")
        if (
            self.automatic_retry_enabled is not False
            or self.live_navigation_enabled is not False
            or self.activation_allowed is not False
            or self.input_authority is not False
        ):
            raise RouteEvidenceIntegrityError("round-trip legs cannot carry authority")
        _validate_leg_sources(self)

    @property
    def durable_evidence_accepted(self) -> bool:
        return bool(
            self.durable_evidence_conformance_passed
            and self.reviewer_approved
            and self.durable_endpoint_arrival_verified
        )

    @property
    def accepted_for_round_trip(self) -> bool:
        return bool(self.durable_evidence_accepted and self.synthetic_causality_conforms)

    def to_json_value(self) -> dict[str, object]:
        if replace(self, _factory_token=_FACTORY_TOKEN) != self:
            raise RouteEvidenceIntegrityError("round-trip leg changed after evaluation")
        _validate_leg_sources(self)
        return {
            "activation_allowed": self.activation_allowed,
            "automatic_retry_enabled": self.automatic_retry_enabled,
            "direction": self.direction.value,
            "durable_endpoint_arrival_verified": self.durable_endpoint_arrival_verified,
            "durable_evidence_conformance_passed": (self.durable_evidence_conformance_passed),
            "durable_evidence_accepted": self.durable_evidence_accepted,
            "explicit_terminal_arrival_bound": self.explicit_terminal_arrival_bound,
            "input_authority": self.input_authority,
            "live_navigation_enabled": self.live_navigation_enabled,
            "navigation_failure_reason": (
                None
                if self.navigation_failure_reason is None
                else self.navigation_failure_reason.value
            ),
            "route": _route_json(self.route),
            "route_plan_sha256": self.route_plan_sha256.value,
            "route_session_id": self.route_session_id,
            "round_trip_leg_accepted": self.accepted_for_round_trip,
            "schema": _LEG_SCHEMA,
            "session_result_sha256": self.session_result_sha256.value,
            "session_stop_reason": (
                None if self.session_stop_reason is None else self.session_stop_reason.value
            ),
            "reviewer_approved": self.reviewer_approved,
            "synthetic_causality_conforms": self.synthetic_causality_conforms,
            "terminal_phase": self.terminal_phase.value,
        }


@dataclass(frozen=True, slots=True)
class SyntheticEndpointHandoff:
    endpoint: RouteEndpoint
    mine_to_bank_route: RouteIdentity
    bank_to_mine_route: RouteIdentity
    mine_to_bank_route_session_id: str
    bank_to_mine_route_session_id: str
    mine_to_bank_finalized_package_sha256: Sha256Digest
    mine_to_bank_reviewer_truth_sha256: Sha256Digest
    bank_to_mine_finalized_package_sha256: Sha256Digest
    bank_to_mine_reviewer_truth_sha256: Sha256Digest
    arrival_checkpoint_id: str
    arrival_frame_id: int
    arrival_captured_monotonic_s: float
    arrival_accepted_monotonic_s: float
    arrival_frame_payload_sha256: Sha256Digest
    departure_checkpoint_id: str
    departure_frame_id: int
    departure_captured_monotonic_s: float
    departure_frame_payload_sha256: Sha256Digest
    _source_mine_to_bank_leg: SyntheticRoundTripLeg = field(repr=False)
    _source_bank_to_mine_leg: SyntheticRoundTripLeg = field(repr=False)
    _factory_token: InitVar[object | None] = None
    bank_interface_open_proven: Literal[False] = field(default=False, init=False)
    downstream_handoff_eligible: Literal[False] = field(default=False, init=False)
    activation_allowed: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _FACTORY_TOKEN:
            raise RouteEvidenceIntegrityError("endpoint handoffs are evaluator-owned")
        if (
            type(self.endpoint) is not RouteEndpoint
            or self.endpoint.role is not RouteEndpointRole.BANK
        ):
            raise RouteEvidenceIntegrityError("round-trip handoff requires the shared bank")
        if (
            type(self.mine_to_bank_route) is not RouteIdentity
            or self.mine_to_bank_route.direction is not RouteDirection.MINE_TO_BANK
            or type(self.bank_to_mine_route) is not RouteIdentity
            or self.bank_to_mine_route.direction is not RouteDirection.BANK_TO_MINE
        ):
            raise RouteEvidenceIntegrityError("round-trip handoff routes are invalid")
        for value in (
            self.mine_to_bank_route_session_id,
            self.bank_to_mine_route_session_id,
            self.arrival_checkpoint_id,
            self.departure_checkpoint_id,
        ):
            if type(value) is not str or not value:
                raise RouteEvidenceIntegrityError("round-trip handoff identity is invalid")
        for digest in (
            self.mine_to_bank_finalized_package_sha256,
            self.mine_to_bank_reviewer_truth_sha256,
            self.bank_to_mine_finalized_package_sha256,
            self.bank_to_mine_reviewer_truth_sha256,
            self.arrival_frame_payload_sha256,
            self.departure_frame_payload_sha256,
        ):
            if type(digest) is not Sha256Digest:
                raise RouteEvidenceIntegrityError("round-trip handoff digest is invalid")
        if (
            type(self.arrival_frame_id) is not int
            or self.arrival_frame_id < 1
            or type(self.departure_frame_id) is not int
            or self.departure_frame_id < 1
        ):
            raise RouteEvidenceIntegrityError("round-trip handoff frame id is invalid")
        if (
            not _is_nonnegative_time(self.arrival_captured_monotonic_s)
            or not _is_nonnegative_time(self.arrival_accepted_monotonic_s)
            or not _is_nonnegative_time(self.departure_captured_monotonic_s)
            or self.arrival_captured_monotonic_s > self.arrival_accepted_monotonic_s
            or self.departure_captured_monotonic_s <= self.arrival_accepted_monotonic_s
        ):
            raise RouteEvidenceIntegrityError("round-trip handoff is not fresh and ordered")
        if (
            self.bank_interface_open_proven is not False
            or self.downstream_handoff_eligible is not False
            or self.activation_allowed is not False
            or self.input_authority is not False
        ):
            raise RouteEvidenceIntegrityError("bank handoff cannot carry downstream authority")
        _validate_handoff_sources(self)

    def to_json_value(self) -> dict[str, object]:
        if replace(self, _factory_token=_FACTORY_TOKEN) != self:
            raise RouteEvidenceIntegrityError("endpoint handoff changed after evaluation")
        _validate_handoff_sources(self)
        return {
            "activation_allowed": self.activation_allowed,
            "arrival": {
                "accepted_monotonic_s": self.arrival_accepted_monotonic_s,
                "captured_monotonic_s": self.arrival_captured_monotonic_s,
                "checkpoint_id": self.arrival_checkpoint_id,
                "frame_id": self.arrival_frame_id,
                "frame_payload_sha256": self.arrival_frame_payload_sha256.value,
                "route": _route_json(self.mine_to_bank_route),
                "route_session_id": self.mine_to_bank_route_session_id,
            },
            "bank_interface_open_proven": self.bank_interface_open_proven,
            "departure": {
                "captured_monotonic_s": self.departure_captured_monotonic_s,
                "checkpoint_id": self.departure_checkpoint_id,
                "frame_id": self.departure_frame_id,
                "frame_payload_sha256": self.departure_frame_payload_sha256.value,
                "route": _route_json(self.bank_to_mine_route),
                "route_session_id": self.bank_to_mine_route_session_id,
            },
            "downstream_handoff_eligible": self.downstream_handoff_eligible,
            "endpoint": _endpoint_json(self.endpoint),
            "evidence_lineage": {
                "bank_to_mine_finalized_package_sha256": (
                    self.bank_to_mine_finalized_package_sha256.value
                ),
                "bank_to_mine_reviewer_truth_sha256": (
                    self.bank_to_mine_reviewer_truth_sha256.value
                ),
                "mine_to_bank_finalized_package_sha256": (
                    self.mine_to_bank_finalized_package_sha256.value
                ),
                "mine_to_bank_reviewer_truth_sha256": (
                    self.mine_to_bank_reviewer_truth_sha256.value
                ),
            },
            "input_authority": self.input_authority,
            "schema": _HANDOFF_SCHEMA,
        }


@dataclass(frozen=True, slots=True)
class SyntheticEndpointArrival:
    endpoint: RouteEndpoint
    route: RouteIdentity
    route_session_id: str
    finalized_package_sha256: Sha256Digest
    reviewer_truth_sha256: Sha256Digest
    checkpoint_id: str
    frame_id: int
    captured_monotonic_s: float
    accepted_monotonic_s: float
    frame_payload_sha256: Sha256Digest
    _source_bank_to_mine_leg: SyntheticRoundTripLeg = field(repr=False)
    _factory_token: InitVar[object | None] = None
    explicit_route_arrival_bound: Literal[True] = field(default=True, init=False)
    supported_mining_view_proven: Literal[False] = field(default=False, init=False)
    downstream_handoff_eligible: Literal[False] = field(default=False, init=False)
    activation_allowed: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _FACTORY_TOKEN:
            raise RouteEvidenceIntegrityError("endpoint arrivals are evaluator-owned")
        if (
            type(self.endpoint) is not RouteEndpoint
            or self.endpoint.role is not RouteEndpointRole.MINE
        ):
            raise RouteEvidenceIntegrityError("round-trip completion requires the shared mine")
        if (
            type(self.route) is not RouteIdentity
            or self.route.direction is not RouteDirection.BANK_TO_MINE
        ):
            raise RouteEvidenceIntegrityError("round-trip mine arrival route is invalid")
        if (
            type(self.route_session_id) is not str
            or not self.route_session_id
            or type(self.checkpoint_id) is not str
            or not self.checkpoint_id
            or type(self.frame_id) is not int
            or self.frame_id < 1
            or type(self.finalized_package_sha256) is not Sha256Digest
            or type(self.reviewer_truth_sha256) is not Sha256Digest
            or type(self.frame_payload_sha256) is not Sha256Digest
        ):
            raise RouteEvidenceIntegrityError("round-trip mine arrival identity is invalid")
        if (
            not _is_nonnegative_time(self.captured_monotonic_s)
            or not _is_nonnegative_time(self.accepted_monotonic_s)
            or self.captured_monotonic_s > self.accepted_monotonic_s
        ):
            raise RouteEvidenceIntegrityError("round-trip mine arrival chronology is invalid")
        if (
            self.explicit_route_arrival_bound is not True
            or self.supported_mining_view_proven is not False
            or self.downstream_handoff_eligible is not False
            or self.activation_allowed is not False
            or self.input_authority is not False
        ):
            raise RouteEvidenceIntegrityError("mine arrival cannot carry downstream authority")
        _validate_arrival_sources(self)

    def to_json_value(self) -> dict[str, object]:
        if replace(self, _factory_token=_FACTORY_TOKEN) != self:
            raise RouteEvidenceIntegrityError("mine arrival changed after evaluation")
        _validate_arrival_sources(self)
        return {
            "accepted_monotonic_s": self.accepted_monotonic_s,
            "activation_allowed": self.activation_allowed,
            "captured_monotonic_s": self.captured_monotonic_s,
            "checkpoint_id": self.checkpoint_id,
            "downstream_handoff_eligible": self.downstream_handoff_eligible,
            "endpoint": _endpoint_json(self.endpoint),
            "explicit_route_arrival_bound": self.explicit_route_arrival_bound,
            "finalized_package_sha256": self.finalized_package_sha256.value,
            "frame_id": self.frame_id,
            "frame_payload_sha256": self.frame_payload_sha256.value,
            "input_authority": self.input_authority,
            "reviewer_truth_sha256": self.reviewer_truth_sha256.value,
            "route": _route_json(self.route),
            "route_session_id": self.route_session_id,
            "schema": _ARRIVAL_SCHEMA,
            "supported_mining_view_proven": self.supported_mining_view_proven,
        }


@dataclass(frozen=True, slots=True)
class SyntheticRoundTripRehearsalReport:
    release_decision_sha256: Sha256Digest
    timeline: SyntheticRoundTripTimelineExpectation
    phase: SyntheticRoundTripPhase
    stop_reason: SyntheticRoundTripStopReason | None
    mine_to_bank: SyntheticRoundTripLeg
    bank_to_mine: SyntheticRoundTripLeg | None
    evaluated_leg_order: tuple[RouteDirection, ...]
    bank_handoff: SyntheticEndpointHandoff | None
    mine_arrival: SyntheticEndpointArrival | None
    _source_decision: NavigationReleaseDecision = field(repr=False)
    _source_timeline: SyntheticRoundTripTimelineExpectation = field(repr=False)
    _factory_token: InitVar[object | None] = None
    retry_count: Literal[0] = field(default=0, init=False)
    automatic_retry_enabled: Literal[False] = field(default=False, init=False)
    release_eligible: Literal[False] = field(default=False, init=False)
    live_navigation_enabled: Literal[False] = field(default=False, init=False)
    world_state_activation_allowed: Literal[False] = field(default=False, init=False)
    controller_activation_allowed: Literal[False] = field(default=False, init=False)
    activation_allowed: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _FACTORY_TOKEN:
            raise RouteEvidenceIntegrityError("round-trip reports are evaluator-owned")
        if type(self.release_decision_sha256) is not Sha256Digest:
            raise RouteEvidenceIntegrityError("round-trip decision digest is invalid")
        if (
            type(self.timeline) is not SyntheticRoundTripTimelineExpectation
            or self.timeline.release_decision_sha256 != self.release_decision_sha256
        ):
            raise RouteEvidenceIntegrityError("round-trip timeline binding is invalid")
        if type(self.phase) is not SyntheticRoundTripPhase:
            raise RouteEvidenceIntegrityError("round-trip report phase is invalid")
        if (
            type(self.mine_to_bank) is not SyntheticRoundTripLeg
            or self.mine_to_bank.direction is not RouteDirection.MINE_TO_BANK
        ):
            raise RouteEvidenceIntegrityError("round-trip outbound leg is invalid")
        if self.bank_to_mine is not None and (
            type(self.bank_to_mine) is not SyntheticRoundTripLeg
            or self.bank_to_mine.direction is not RouteDirection.BANK_TO_MINE
        ):
            raise RouteEvidenceIntegrityError("round-trip return leg is invalid")
        expected_order = (
            (RouteDirection.MINE_TO_BANK,)
            if self.bank_to_mine is None
            else (RouteDirection.MINE_TO_BANK, RouteDirection.BANK_TO_MINE)
        )
        if self.evaluated_leg_order != expected_order:
            raise RouteEvidenceIntegrityError("round-trip leg evaluation order is invalid")
        if self.phase is SyntheticRoundTripPhase.COMPLETED:
            valid = bool(
                self.stop_reason is None
                and self.mine_to_bank.accepted_for_round_trip
                and self.bank_to_mine is not None
                and self.bank_to_mine.accepted_for_round_trip
                and type(self.bank_handoff) is SyntheticEndpointHandoff
                and type(self.mine_arrival) is SyntheticEndpointArrival
            )
        else:
            if type(self.stop_reason) is not SyntheticRoundTripStopReason:
                raise RouteEvidenceIntegrityError("stopped round trip lost its reason")
            valid = self.mine_arrival is None
            if self.stop_reason is (
                SyntheticRoundTripStopReason.MINE_TO_BANK_EVIDENCE_NOT_APPROVED
            ):
                valid = bool(
                    valid
                    and not self.mine_to_bank.durable_evidence_accepted
                    and self.bank_to_mine is None
                    and self.bank_handoff is None
                )
            elif self.stop_reason is SyntheticRoundTripStopReason.MINE_TO_BANK_NOT_ARRIVED:
                valid = bool(
                    valid
                    and self.mine_to_bank.durable_evidence_accepted
                    and not self.mine_to_bank.synthetic_causality_conforms
                    and self.bank_to_mine is None
                    and self.bank_handoff is None
                )
            elif self.stop_reason is (
                SyntheticRoundTripStopReason.BANK_TO_MINE_EVIDENCE_NOT_APPROVED
            ):
                valid = bool(
                    valid
                    and self.mine_to_bank.accepted_for_round_trip
                    and self.bank_to_mine is not None
                    and not self.bank_to_mine.durable_evidence_accepted
                    and self.bank_handoff is None
                )
            elif self.stop_reason is SyntheticRoundTripStopReason.BANK_TO_MINE_DEPARTURE_NOT_FRESH:
                valid = bool(
                    valid
                    and self.mine_to_bank.accepted_for_round_trip
                    and self.bank_to_mine is not None
                    and self.bank_to_mine.durable_evidence_accepted
                    and self.bank_handoff is None
                )
            elif self.stop_reason is SyntheticRoundTripStopReason.BANK_TO_MINE_NOT_ARRIVED:
                valid = bool(
                    valid
                    and self.mine_to_bank.accepted_for_round_trip
                    and self.bank_to_mine is not None
                    and self.bank_to_mine.durable_evidence_accepted
                    and not self.bank_to_mine.synthetic_causality_conforms
                )
        if not valid:
            raise RouteEvidenceIntegrityError("round-trip report state is inconsistent")
        if (
            self.retry_count != 0
            or self.automatic_retry_enabled is not False
            or self.release_eligible is not False
            or self.live_navigation_enabled is not False
            or self.world_state_activation_allowed is not False
            or self.controller_activation_allowed is not False
            or self.activation_allowed is not False
            or self.input_authority is not False
        ):
            raise RouteEvidenceIntegrityError("round-trip reports cannot carry authority")
        _validate_report_sources(self)

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_route_evidence_bytes(self.to_json_value())

    @property
    def content_sha256(self) -> Sha256Digest:
        return Sha256Digest.from_bytes(self.canonical_bytes)

    def to_json_value(self) -> dict[str, object]:
        if replace(self, _factory_token=_FACTORY_TOKEN) != self:
            raise RouteEvidenceIntegrityError("round-trip report changed after evaluation")
        return {
            "activation_allowed": self.activation_allowed,
            "automatic_retry_enabled": self.automatic_retry_enabled,
            "bank_handoff": (
                None if self.bank_handoff is None else self.bank_handoff.to_json_value()
            ),
            "bank_to_mine": (
                None if self.bank_to_mine is None else self.bank_to_mine.to_json_value()
            ),
            "controller_activation_allowed": self.controller_activation_allowed,
            "evaluated_leg_order": [item.value for item in self.evaluated_leg_order],
            "input_authority": self.input_authority,
            "live_navigation_enabled": self.live_navigation_enabled,
            "mine_arrival": (
                None if self.mine_arrival is None else self.mine_arrival.to_json_value()
            ),
            "mine_to_bank": self.mine_to_bank.to_json_value(),
            "phase": self.phase.value,
            "rehearsal_role": SYNTHETIC_ROUND_TRIP_REHEARSAL_ROLE,
            "release_decision_sha256": self.release_decision_sha256.value,
            "release_eligible": self.release_eligible,
            "retry_count": self.retry_count,
            "schema": _REPORT_SCHEMA,
            "stop_reason": None if self.stop_reason is None else self.stop_reason.value,
            "timeline": self.timeline.to_json_value(),
            "world_state_activation_allowed": self.world_state_activation_allowed,
        }


def _bind_named_result(
    binding: DirectionProductionBinding,
    result: OfflineRouteSessionResult,
) -> OfflineRouteSessionResult:
    try:
        owned = _owned_result(result)
        retained_source = _owned_result(binding._source_post_attempt_result)
        _require_result_binding(
            binding._source_evidence,
            binding.post_attempt_expectation,
            owned,
        )
        projected = _post_attempt_binding(binding.route_plan, binding.expectation, owned)
    except (AttributeError, TypeError, ValueError) as exc:
        raise RouteEvidenceIntegrityError(
            f"{binding.direction.value} rehearsal result is malformed or cross-bound"
        ) from exc
    if owned != retained_source or projected != binding.post_attempt_causality:
        raise RouteEvidenceIntegrityError(
            f"{binding.direction.value} rehearsal result differs from its exact B1 binding"
        )
    return owned


def _validate_leg_sources(leg: SyntheticRoundTripLeg) -> None:
    if (
        type(leg._source_binding) is not DirectionProductionBinding
        or type(leg._source_result) is not OfflineRouteSessionResult
    ):
        raise RouteEvidenceIntegrityError("round-trip leg source anchors are invalid")
    binding = _copy_direction_production_binding(leg._source_binding)
    if binding != leg._source_binding:
        raise RouteEvidenceIntegrityError("round-trip leg source binding was mutated")
    result = _bind_named_result(binding, leg._source_result)
    progress = result.progress
    causal = binding.post_attempt_causality
    expected = (
        binding.direction,
        binding.route_plan.identity,
        binding.expectation.route_plan_sha256,
        progress.session.session_id,
        causal.session_result_sha256,
        progress.phase,
        progress.stop_reason,
        progress.navigation.failure_reason,
        binding.evidence_conformance_passed,
        binding.reviewer_decision is ReviewerDecisionSummary.APPROVED,
        binding.endpoint.synthetic_route_arrival_verified,
        causal.explicit_terminal_arrival_bound,
        causal.synthetic_causality_conforms,
    )
    observed = (
        leg.direction,
        leg.route,
        leg.route_plan_sha256,
        leg.route_session_id,
        leg.session_result_sha256,
        leg.terminal_phase,
        leg.session_stop_reason,
        leg.navigation_failure_reason,
        leg.durable_evidence_conformance_passed,
        leg.reviewer_approved,
        leg.durable_endpoint_arrival_verified,
        leg.explicit_terminal_arrival_bound,
        leg.synthetic_causality_conforms,
    )
    if observed != expected:
        raise RouteEvidenceIntegrityError("round-trip leg differs from its exact source anchors")


def _validate_handoff_sources(handoff: SyntheticEndpointHandoff) -> None:
    outbound_leg = handoff._source_mine_to_bank_leg
    return_leg = handoff._source_bank_to_mine_leg
    if (
        type(outbound_leg) is not SyntheticRoundTripLeg
        or type(return_leg) is not SyntheticRoundTripLeg
    ):
        raise RouteEvidenceIntegrityError("endpoint handoff source legs are invalid")
    _validate_leg_sources(outbound_leg)
    _validate_leg_sources(return_leg)
    outbound_binding = outbound_leg._source_binding
    return_binding = return_leg._source_binding
    outbound_result = outbound_leg._source_result
    return_result = return_leg._source_result
    arrival = outbound_result.progress.navigation.arrival_evidence
    attempts = return_result.progress.navigation.completed_attempts
    if (
        outbound_binding.route_plan.destination != return_binding.route_plan.origin
        or outbound_result.progress.session.session_id == return_result.progress.session.session_id
        or not outbound_leg.accepted_for_round_trip
        or not return_leg.durable_evidence_conformance_passed
        or not return_leg.reviewer_approved
        or not return_leg.durable_endpoint_arrival_verified
        or arrival is None
        or not attempts
    ):
        raise RouteEvidenceIntegrityError("endpoint handoff lacks exact accepted source evidence")
    departure = attempts[0].proposal.checkpoint_evidence
    arrival_provenance = arrival.observation.provenance
    departure_provenance = departure.provenance
    expected = (
        outbound_binding.route_plan.destination,
        outbound_binding.route_plan.identity,
        return_binding.route_plan.identity,
        outbound_result.progress.session.session_id,
        return_result.progress.session.session_id,
        outbound_binding.expectation.finalized_package_sha256,
        outbound_binding.expectation.independent_review_sha256,
        return_binding.expectation.finalized_package_sha256,
        return_binding.expectation.independent_review_sha256,
        arrival.checkpoint.checkpoint_id,
        arrival_provenance.frame.frame_id,
        arrival_provenance.frame.captured_monotonic_s,
        outbound_result.progress.last_event_monotonic_s,
        arrival_provenance.frame_payload_sha256,
        return_binding.route_plan.checkpoints[0].checkpoint_id,
        departure_provenance.frame.frame_id,
        departure_provenance.frame.captured_monotonic_s,
        departure_provenance.frame_payload_sha256,
    )
    observed = (
        handoff.endpoint,
        handoff.mine_to_bank_route,
        handoff.bank_to_mine_route,
        handoff.mine_to_bank_route_session_id,
        handoff.bank_to_mine_route_session_id,
        handoff.mine_to_bank_finalized_package_sha256,
        handoff.mine_to_bank_reviewer_truth_sha256,
        handoff.bank_to_mine_finalized_package_sha256,
        handoff.bank_to_mine_reviewer_truth_sha256,
        handoff.arrival_checkpoint_id,
        handoff.arrival_frame_id,
        handoff.arrival_captured_monotonic_s,
        handoff.arrival_accepted_monotonic_s,
        handoff.arrival_frame_payload_sha256,
        handoff.departure_checkpoint_id,
        handoff.departure_frame_id,
        handoff.departure_captured_monotonic_s,
        handoff.departure_frame_payload_sha256,
    )
    if observed != expected:
        raise RouteEvidenceIntegrityError("endpoint handoff differs from its exact source legs")


def _validate_arrival_sources(arrival: SyntheticEndpointArrival) -> None:
    return_leg = arrival._source_bank_to_mine_leg
    if type(return_leg) is not SyntheticRoundTripLeg:
        raise RouteEvidenceIntegrityError("mine arrival source leg is invalid")
    _validate_leg_sources(return_leg)
    binding = return_leg._source_binding
    result = return_leg._source_result
    source_arrival = result.progress.navigation.arrival_evidence
    if not return_leg.accepted_for_round_trip or source_arrival is None:
        raise RouteEvidenceIntegrityError("mine arrival lacks exact accepted source evidence")
    provenance = source_arrival.observation.provenance
    expected = (
        binding.route_plan.destination,
        binding.route_plan.identity,
        result.progress.session.session_id,
        binding.expectation.finalized_package_sha256,
        binding.expectation.independent_review_sha256,
        source_arrival.checkpoint.checkpoint_id,
        provenance.frame.frame_id,
        provenance.frame.captured_monotonic_s,
        result.progress.last_event_monotonic_s,
        provenance.frame_payload_sha256,
    )
    observed = (
        arrival.endpoint,
        arrival.route,
        arrival.route_session_id,
        arrival.finalized_package_sha256,
        arrival.reviewer_truth_sha256,
        arrival.checkpoint_id,
        arrival.frame_id,
        arrival.captured_monotonic_s,
        arrival.accepted_monotonic_s,
        arrival.frame_payload_sha256,
    )
    if observed != expected:
        raise RouteEvidenceIntegrityError("mine arrival differs from its exact source leg")


def _validate_report_sources(report: SyntheticRoundTripRehearsalReport) -> None:
    if type(report._source_decision) is not NavigationReleaseDecision:
        raise RouteEvidenceIntegrityError("round-trip report source decision is invalid")
    decision = _snapshot_navigation_release_decision(report._source_decision)
    if report.release_decision_sha256 != decision.content_sha256:
        raise RouteEvidenceIntegrityError("round-trip report decision digest differs")
    source_timeline = _snapshot_timeline_expectation(report._source_timeline)
    timeline = _snapshot_timeline_expectation(report.timeline)
    if timeline != report.timeline or timeline != source_timeline:
        raise RouteEvidenceIntegrityError("round-trip report timeline differs from its source")
    _validate_leg_sources(report.mine_to_bank)
    if report.mine_to_bank._source_binding != decision.mine_to_bank:
        raise RouteEvidenceIntegrityError("round-trip outbound leg is cross-bound")
    return_source = _owned_result(decision.bank_to_mine._source_post_attempt_result)
    _require_timeline_binding(
        timeline,
        decision,
        report.mine_to_bank._source_result,
        return_source,
    )
    if report.bank_to_mine is not None:
        _validate_leg_sources(report.bank_to_mine)
        if report.bank_to_mine._source_binding != decision.bank_to_mine:
            raise RouteEvidenceIntegrityError("round-trip return leg is cross-bound")
    if report.bank_handoff is not None:
        _validate_handoff_sources(report.bank_handoff)
        if (
            report.bank_handoff._source_mine_to_bank_leg != report.mine_to_bank
            or report.bank_handoff._source_bank_to_mine_leg != report.bank_to_mine
        ):
            raise RouteEvidenceIntegrityError("round-trip handoff is cross-bound")
    if report.mine_arrival is not None:
        _validate_arrival_sources(report.mine_arrival)
        if report.mine_arrival._source_bank_to_mine_leg != report.bank_to_mine:
            raise RouteEvidenceIntegrityError("round-trip mine arrival is cross-bound")
    if report.stop_reason is SyntheticRoundTripStopReason.BANK_TO_MINE_DEPARTURE_NOT_FRESH:
        if report.bank_to_mine is None:
            raise RouteEvidenceIntegrityError("nonfresh handoff omitted its return leg")
        attempts = report.bank_to_mine._source_result.progress.navigation.completed_attempts
        if (
            not attempts
            or attempts[0].proposal.checkpoint_evidence.provenance.frame.captured_monotonic_s
            > report.mine_to_bank._source_result.progress.last_event_monotonic_s
        ):
            raise RouteEvidenceIntegrityError("round-trip nonfresh stop reason differs")
    if report.stop_reason is SyntheticRoundTripStopReason.BANK_TO_MINE_NOT_ARRIVED:
        if report.bank_to_mine is None:
            raise RouteEvidenceIntegrityError("return failure omitted its source leg")
        attempts = report.bank_to_mine._source_result.progress.navigation.completed_attempts
        if attempts:
            departure_time = attempts[
                0
            ].proposal.checkpoint_evidence.provenance.frame.captured_monotonic_s
            if departure_time <= report.mine_to_bank._source_result.progress.last_event_monotonic_s:
                raise RouteEvidenceIntegrityError("return failure bypassed nonfresh precedence")
            if report.bank_handoff is None:
                raise RouteEvidenceIntegrityError("fresh failed return omitted its bank handoff")
        elif report.bank_handoff is not None:
            raise RouteEvidenceIntegrityError("return failure invented a bank handoff")


def _snapshot_timeline_expectation(
    value: SyntheticRoundTripTimelineExpectation,
) -> SyntheticRoundTripTimelineExpectation:
    def copy_once(
        candidate: SyntheticRoundTripTimelineExpectation,
    ) -> SyntheticRoundTripTimelineExpectation:
        if type(candidate) is not SyntheticRoundTripTimelineExpectation:
            raise TypeError("synthetic round-trip timeline has the wrong type")
        if (
            candidate.synthetic_numeric_timeline_only is not True
            or candidate.real_monotonic_clock_attested is not False
            or candidate.release_authority is not False
            or candidate.activation_allowed is not False
            or candidate.input_authority is not False
        ):
            raise ValueError("synthetic round-trip timeline authority fields were mutated")
        return SyntheticRoundTripTimelineExpectation(
            timeline_id=candidate.timeline_id,
            release_decision_sha256=Sha256Digest(candidate.release_decision_sha256.value),
            mine_to_bank_route_session_id=candidate.mine_to_bank_route_session_id,
            mine_to_bank_session_result_sha256=Sha256Digest(
                candidate.mine_to_bank_session_result_sha256.value
            ),
            bank_to_mine_route_session_id=candidate.bank_to_mine_route_session_id,
            bank_to_mine_session_result_sha256=Sha256Digest(
                candidate.bank_to_mine_session_result_sha256.value
            ),
        )

    try:
        first = copy_once(value)
        second = copy_once(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise RouteEvidenceIntegrityError(
            "synthetic round-trip timeline expectation is malformed"
        ) from exc
    if first != second:
        raise RouteEvidenceIntegrityError("synthetic round-trip timeline changed during snapshot")
    return second


def _require_timeline_binding(
    timeline: SyntheticRoundTripTimelineExpectation,
    decision: NavigationReleaseDecision,
    mine_to_bank_result: OfflineRouteSessionResult,
    bank_to_mine_result: OfflineRouteSessionResult,
) -> None:
    if (
        timeline.release_decision_sha256 != decision.content_sha256
        or timeline.mine_to_bank_route_session_id != mine_to_bank_result.progress.session.session_id
        or timeline.mine_to_bank_session_result_sha256
        != decision.mine_to_bank.post_attempt_causality.session_result_sha256
        or timeline.bank_to_mine_route_session_id != bank_to_mine_result.progress.session.session_id
        or timeline.bank_to_mine_session_result_sha256
        != decision.bank_to_mine.post_attempt_causality.session_result_sha256
    ):
        raise RouteEvidenceIntegrityError(
            "synthetic round-trip timeline differs from the exact decision results"
        )


def _leg(
    binding: DirectionProductionBinding,
    result: OfflineRouteSessionResult,
) -> SyntheticRoundTripLeg:
    progress = result.progress
    causal = binding.post_attempt_causality
    return SyntheticRoundTripLeg(
        direction=binding.direction,
        route=binding.route_plan.identity,
        route_plan_sha256=binding.expectation.route_plan_sha256,
        route_session_id=progress.session.session_id,
        session_result_sha256=causal.session_result_sha256,
        terminal_phase=progress.phase,
        session_stop_reason=progress.stop_reason,
        navigation_failure_reason=progress.navigation.failure_reason,
        durable_evidence_conformance_passed=binding.evidence_conformance_passed,
        reviewer_approved=(binding.reviewer_decision is ReviewerDecisionSummary.APPROVED),
        durable_endpoint_arrival_verified=(binding.endpoint.synthetic_route_arrival_verified),
        explicit_terminal_arrival_bound=causal.explicit_terminal_arrival_bound,
        synthetic_causality_conforms=causal.synthetic_causality_conforms,
        _source_binding=binding,
        _source_result=result,
        _factory_token=_FACTORY_TOKEN,
    )


def _report(
    decision: NavigationReleaseDecision,
    timeline: SyntheticRoundTripTimelineExpectation,
    *,
    phase: SyntheticRoundTripPhase,
    stop_reason: SyntheticRoundTripStopReason | None,
    mine_to_bank: SyntheticRoundTripLeg,
    bank_to_mine: SyntheticRoundTripLeg | None = None,
    bank_handoff: SyntheticEndpointHandoff | None = None,
    mine_arrival: SyntheticEndpointArrival | None = None,
) -> SyntheticRoundTripRehearsalReport:
    public_timeline = _snapshot_timeline_expectation(timeline)
    source_timeline = _snapshot_timeline_expectation(timeline)
    return SyntheticRoundTripRehearsalReport(
        release_decision_sha256=decision.content_sha256,
        timeline=public_timeline,
        phase=phase,
        stop_reason=stop_reason,
        mine_to_bank=mine_to_bank,
        bank_to_mine=bank_to_mine,
        evaluated_leg_order=(
            (RouteDirection.MINE_TO_BANK,)
            if bank_to_mine is None
            else (RouteDirection.MINE_TO_BANK, RouteDirection.BANK_TO_MINE)
        ),
        bank_handoff=bank_handoff,
        mine_arrival=mine_arrival,
        _source_decision=decision,
        _source_timeline=source_timeline,
        _factory_token=_FACTORY_TOKEN,
    )


def evaluate_synthetic_round_trip_rehearsal(
    decision: NavigationReleaseDecision,
    *,
    timeline_expectation: SyntheticRoundTripTimelineExpectation,
    mine_to_bank_result: OfflineRouteSessionResult,
    bank_to_mine_result: OfflineRouteSessionResult,
) -> SyntheticRoundTripRehearsalReport:
    """Evaluate one exact synthetic outbound/return pair, once, without retry."""

    owned_decision = _snapshot_navigation_release_decision(decision)
    outbound_binding = owned_decision.mine_to_bank
    return_binding = owned_decision.bank_to_mine

    outbound_result = _bind_named_result(outbound_binding, mine_to_bank_result)
    return_result = _bind_named_result(return_binding, bank_to_mine_result)
    timeline = _snapshot_timeline_expectation(timeline_expectation)
    _require_timeline_binding(
        timeline,
        owned_decision,
        outbound_result,
        return_result,
    )
    outbound_leg = _leg(outbound_binding, outbound_result)
    if not outbound_leg.durable_evidence_accepted:
        return _report(
            owned_decision,
            timeline,
            phase=SyntheticRoundTripPhase.STOPPED,
            stop_reason=(SyntheticRoundTripStopReason.MINE_TO_BANK_EVIDENCE_NOT_APPROVED),
            mine_to_bank=outbound_leg,
        )
    if not outbound_leg.synthetic_causality_conforms:
        return _report(
            owned_decision,
            timeline,
            phase=SyntheticRoundTripPhase.STOPPED,
            stop_reason=SyntheticRoundTripStopReason.MINE_TO_BANK_NOT_ARRIVED,
            mine_to_bank=outbound_leg,
        )

    return_leg = _leg(return_binding, return_result)
    if not return_leg.durable_evidence_accepted:
        return _report(
            owned_decision,
            timeline,
            phase=SyntheticRoundTripPhase.STOPPED,
            stop_reason=(SyntheticRoundTripStopReason.BANK_TO_MINE_EVIDENCE_NOT_APPROVED),
            mine_to_bank=outbound_leg,
            bank_to_mine=return_leg,
        )
    outbound_arrival = outbound_result.progress.navigation.arrival_evidence
    if outbound_arrival is None:
        raise RouteEvidenceIntegrityError("conforming outbound leg omitted arrival evidence")
    return_attempts = return_result.progress.navigation.completed_attempts
    if not return_attempts:
        return _report(
            owned_decision,
            timeline,
            phase=SyntheticRoundTripPhase.STOPPED,
            stop_reason=SyntheticRoundTripStopReason.BANK_TO_MINE_NOT_ARRIVED,
            mine_to_bank=outbound_leg,
            bank_to_mine=return_leg,
        )

    departure = return_attempts[0].proposal.checkpoint_evidence
    arrival_provenance = outbound_arrival.observation.provenance
    departure_provenance = departure.provenance
    arrival_accepted = outbound_result.progress.last_event_monotonic_s
    if departure_provenance.frame.captured_monotonic_s <= arrival_accepted:
        return _report(
            owned_decision,
            timeline,
            phase=SyntheticRoundTripPhase.STOPPED,
            stop_reason=(SyntheticRoundTripStopReason.BANK_TO_MINE_DEPARTURE_NOT_FRESH),
            mine_to_bank=outbound_leg,
            bank_to_mine=return_leg,
        )

    bank_handoff = SyntheticEndpointHandoff(
        endpoint=outbound_binding.route_plan.destination,
        mine_to_bank_route=outbound_binding.route_plan.identity,
        bank_to_mine_route=return_binding.route_plan.identity,
        mine_to_bank_route_session_id=outbound_result.progress.session.session_id,
        bank_to_mine_route_session_id=return_result.progress.session.session_id,
        mine_to_bank_finalized_package_sha256=(
            outbound_binding.expectation.finalized_package_sha256
        ),
        mine_to_bank_reviewer_truth_sha256=(outbound_binding.expectation.independent_review_sha256),
        bank_to_mine_finalized_package_sha256=(return_binding.expectation.finalized_package_sha256),
        bank_to_mine_reviewer_truth_sha256=(return_binding.expectation.independent_review_sha256),
        arrival_checkpoint_id=outbound_arrival.checkpoint.checkpoint_id,
        arrival_frame_id=arrival_provenance.frame.frame_id,
        arrival_captured_monotonic_s=arrival_provenance.frame.captured_monotonic_s,
        arrival_accepted_monotonic_s=arrival_accepted,
        arrival_frame_payload_sha256=arrival_provenance.frame_payload_sha256,
        departure_checkpoint_id=return_binding.route_plan.checkpoints[0].checkpoint_id,
        departure_frame_id=departure_provenance.frame.frame_id,
        departure_captured_monotonic_s=departure_provenance.frame.captured_monotonic_s,
        departure_frame_payload_sha256=departure_provenance.frame_payload_sha256,
        _source_mine_to_bank_leg=outbound_leg,
        _source_bank_to_mine_leg=return_leg,
        _factory_token=_FACTORY_TOKEN,
    )
    if not return_leg.synthetic_causality_conforms:
        return _report(
            owned_decision,
            timeline,
            phase=SyntheticRoundTripPhase.STOPPED,
            stop_reason=SyntheticRoundTripStopReason.BANK_TO_MINE_NOT_ARRIVED,
            mine_to_bank=outbound_leg,
            bank_to_mine=return_leg,
            bank_handoff=bank_handoff,
        )

    return_arrival = return_result.progress.navigation.arrival_evidence
    if return_arrival is None:
        raise RouteEvidenceIntegrityError("conforming return leg omitted arrival evidence")
    return_arrival_provenance = return_arrival.observation.provenance
    mine_arrival = SyntheticEndpointArrival(
        endpoint=return_binding.route_plan.destination,
        route=return_binding.route_plan.identity,
        route_session_id=return_result.progress.session.session_id,
        finalized_package_sha256=return_binding.expectation.finalized_package_sha256,
        reviewer_truth_sha256=return_binding.expectation.independent_review_sha256,
        checkpoint_id=return_arrival.checkpoint.checkpoint_id,
        frame_id=return_arrival_provenance.frame.frame_id,
        captured_monotonic_s=return_arrival_provenance.frame.captured_monotonic_s,
        accepted_monotonic_s=return_result.progress.last_event_monotonic_s,
        frame_payload_sha256=return_arrival_provenance.frame_payload_sha256,
        _source_bank_to_mine_leg=return_leg,
        _factory_token=_FACTORY_TOKEN,
    )
    return _report(
        owned_decision,
        timeline,
        phase=SyntheticRoundTripPhase.COMPLETED,
        stop_reason=None,
        mine_to_bank=outbound_leg,
        bank_to_mine=return_leg,
        bank_handoff=bank_handoff,
        mine_arrival=mine_arrival,
    )


def _snapshot_synthetic_round_trip_report(
    value: SyntheticRoundTripRehearsalReport,
) -> SyntheticRoundTripRehearsalReport:
    """Re-evaluate one report from its retained B1 sources and detach it."""

    def copy_once(
        candidate: SyntheticRoundTripRehearsalReport,
    ) -> SyntheticRoundTripRehearsalReport:
        if type(candidate) is not SyntheticRoundTripRehearsalReport:
            raise TypeError("synthetic round-trip report has the wrong type")
        decision = _snapshot_navigation_release_decision(candidate._source_decision)
        timeline = _snapshot_timeline_expectation(candidate._source_timeline)
        return evaluate_synthetic_round_trip_rehearsal(
            decision,
            timeline_expectation=timeline,
            mine_to_bank_result=_owned_result(decision.mine_to_bank._source_post_attempt_result),
            bank_to_mine_result=_owned_result(decision.bank_to_mine._source_post_attempt_result),
        )

    try:
        first = copy_once(value)
        second = copy_once(value)
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise RouteEvidenceIntegrityError("synthetic round-trip report is malformed") from exc
    if first != second:
        raise RouteEvidenceIntegrityError("synthetic round-trip report changed during snapshot")
    if second != value:
        raise RouteEvidenceIntegrityError("synthetic round-trip report differs from its sources")
    return second
