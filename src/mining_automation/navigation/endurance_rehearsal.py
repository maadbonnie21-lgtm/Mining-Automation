"""Deterministic packaging for repeated synthetic round-trip rehearsals.

This module folds already-issued, source-anchored round-trip reports.  It does
not execute routes, inject faults, resume sessions, or grant retry authority.
"""

from __future__ import annotations

import math
import re
from dataclasses import InitVar, dataclass, field, replace
from enum import StrEnum
from typing import Final, Literal

from .contracts import RouteDirection, RouteIdentity, Sha256Digest
from .offline_route_session import OfflineRouteSessionResult
from .release_decision import DirectionProductionBinding, _owned_result
from .round_trip_rehearsal import (
    SyntheticRoundTripPhase,
    SyntheticRoundTripRehearsalReport,
    SyntheticRoundTripStopReason,
    _snapshot_synthetic_round_trip_report,
    _validate_report_sources,
)
from .route_evidence import RouteEvidenceIntegrityError, canonical_route_evidence_bytes

__all__ = [
    "SYNTHETIC_ENDURANCE_REHEARSAL_ROLE",
    "SyntheticEnduranceAttemptOutcome",
    "SyntheticEnduranceExpectation",
    "SyntheticEndurancePhase",
    "SyntheticEnduranceRehearsalReport",
    "SyntheticEnduranceStopReason",
    "SyntheticTraversalAttemptExpectation",
    "evaluate_synthetic_endurance_rehearsal",
]

SYNTHETIC_ENDURANCE_REHEARSAL_ROLE: Final[str] = (
    "synthetic_repeated_round_trip_fault_endurance_packaging_only"
)

_EXPECTATION_SCHEMA: Final[str] = "fixed-route-synthetic-endurance-expectation-v1"
_ATTEMPT_EXPECTATION_SCHEMA: Final[str] = "fixed-route-synthetic-traversal-attempt-expectation-v1"
_ATTEMPT_RECORD_SCHEMA: Final[str] = "fixed-route-synthetic-traversal-attempt-record-v1"
_REPORT_SCHEMA: Final[str] = "fixed-route-synthetic-endurance-report-v1"
_IDENTIFIER_PATTERN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_FACTORY_TOKEN: Final[object] = object()
_VALIDATION_TOKEN: Final[object] = object()


def _identifier(value: object, field_name: str) -> str:
    if type(value) is not str or _IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise RouteEvidenceIntegrityError(f"{field_name} must be a portable identifier")
    return value


def _time(value: object, field_name: str) -> float:
    if (
        type(value) is not float
        or not math.isfinite(value)
        or value < 0.0
        or (value == 0.0 and math.copysign(1.0, value) < 0.0)
    ):
        raise RouteEvidenceIntegrityError(f"{field_name} must be a finite non-negative float")
    return value


def _route_json(route: RouteIdentity) -> dict[str, str]:
    return {
        "direction": route.direction.value,
        "route_id": route.route_id,
        "version": route.version,
    }


class SyntheticEnduranceAttemptOutcome(StrEnum):
    COMPLETED = "completed"
    TRAVERSAL_STOPPED = "traversal_stopped"
    CAMPAIGN_BOUNDARY_STOPPED = "campaign_boundary_stopped"


class SyntheticEndurancePhase(StrEnum):
    COMPLETED = "completed"
    STOPPED = "stopped"


class SyntheticEnduranceStopReason(StrEnum):
    UNRECOVERED_TRAVERSAL = "unrecovered_traversal"
    CROSS_TRAVERSAL_DEPARTURE_NOT_FRESH = "cross_traversal_departure_not_fresh"
    PLANNED_CYCLE_TARGET_NOT_MET = "planned_cycle_target_not_met"


@dataclass(frozen=True, slots=True)
class SyntheticTraversalAttemptExpectation:
    """One exact report slot in a caller-owned synthetic campaign manifest."""

    traversal_id: str
    cycle_number: int
    scenario_id: str
    round_trip_sha256: Sha256Digest
    expected_round_trip_phase: SyntheticRoundTripPhase
    expected_round_trip_stop_reason: SyntheticRoundTripStopReason | None
    recovery_of_traversal_id: str | None = None
    synthetic_only: Literal[True] = field(default=True, init=False)
    automatic_retry_allowed: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        _identifier(self.traversal_id, "synthetic traversal id")
        _identifier(self.scenario_id, "synthetic traversal scenario id")
        if type(self.cycle_number) is not int or self.cycle_number < 1:
            raise RouteEvidenceIntegrityError("synthetic traversal cycle number is invalid")
        if type(self.round_trip_sha256) is not Sha256Digest:
            raise RouteEvidenceIntegrityError("synthetic traversal report digest is invalid")
        if type(self.expected_round_trip_phase) is not SyntheticRoundTripPhase:
            raise RouteEvidenceIntegrityError("synthetic traversal expected phase is invalid")
        if self.expected_round_trip_phase is SyntheticRoundTripPhase.COMPLETED:
            if self.expected_round_trip_stop_reason is not None:
                raise RouteEvidenceIntegrityError(
                    "completed traversal expectation has a stop reason"
                )
        elif type(self.expected_round_trip_stop_reason) is not SyntheticRoundTripStopReason:
            raise RouteEvidenceIntegrityError("stopped traversal expectation lost its reason")
        if self.recovery_of_traversal_id is not None:
            _identifier(
                self.recovery_of_traversal_id,
                "synthetic traversal recovery predecessor",
            )
            if self.recovery_of_traversal_id == self.traversal_id:
                raise RouteEvidenceIntegrityError("synthetic traversal cannot recover itself")
        if (
            self.synthetic_only is not True
            or self.automatic_retry_allowed is not False
            or self.input_authority is not False
        ):
            raise RouteEvidenceIntegrityError(
                "synthetic traversal expectation cannot carry authority"
            )

    def to_json_value(self) -> dict[str, object]:
        if _snapshot_attempt_expectation(self) != self:
            raise RouteEvidenceIntegrityError("traversal expectation changed after construction")
        return {
            "automatic_retry_allowed": self.automatic_retry_allowed,
            "cycle_number": self.cycle_number,
            "expected_round_trip_phase": self.expected_round_trip_phase.value,
            "expected_round_trip_stop_reason": (
                None
                if self.expected_round_trip_stop_reason is None
                else self.expected_round_trip_stop_reason.value
            ),
            "input_authority": self.input_authority,
            "recovery_of_traversal_id": self.recovery_of_traversal_id,
            "round_trip_sha256": self.round_trip_sha256.value,
            "scenario_id": self.scenario_id,
            "schema": _ATTEMPT_EXPECTATION_SCHEMA,
            "synthetic_only": self.synthetic_only,
            "traversal_id": self.traversal_id,
        }


@dataclass(frozen=True, slots=True)
class SyntheticEnduranceExpectation:
    """Caller-owned exact attempt order and successful-cycle target."""

    campaign_id: str
    shared_timeline_id: str
    planned_cycle_count: int
    ordered_attempts: tuple[SyntheticTraversalAttemptExpectation, ...]
    synthetic_numeric_timeline_only: Literal[True] = field(default=True, init=False)
    real_monotonic_clock_attested: Literal[False] = field(default=False, init=False)
    automatic_retry_allowed: Literal[False] = field(default=False, init=False)
    report_adoption_allowed: Literal[False] = field(default=False, init=False)
    release_authority: Literal[False] = field(default=False, init=False)
    activation_allowed: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        _identifier(self.campaign_id, "synthetic endurance campaign id")
        _identifier(self.shared_timeline_id, "synthetic endurance timeline id")
        if type(self.planned_cycle_count) is not int or self.planned_cycle_count < 2:
            raise RouteEvidenceIntegrityError(
                "synthetic endurance requires at least two planned cycles"
            )
        if (
            type(self.ordered_attempts) is not tuple
            or not self.ordered_attempts
            or any(
                type(item) is not SyntheticTraversalAttemptExpectation
                for item in self.ordered_attempts
            )
        ):
            raise RouteEvidenceIntegrityError("synthetic endurance attempts are invalid")
        ids = tuple(item.traversal_id for item in self.ordered_attempts)
        digests = tuple(item.round_trip_sha256 for item in self.ordered_attempts)
        if len(set(ids)) != len(ids) or len(set(digests)) != len(digests):
            raise RouteEvidenceIntegrityError(
                "synthetic endurance attempt ids and reports must be unique"
            )
        first = self.ordered_attempts[0]
        if first.cycle_number != 1 or first.recovery_of_traversal_id is not None:
            raise RouteEvidenceIntegrityError("synthetic endurance must begin at cycle one")
        for previous, current in zip(
            self.ordered_attempts,
            self.ordered_attempts[1:],
            strict=False,
        ):
            advances = bool(
                current.cycle_number == previous.cycle_number + 1
                and current.recovery_of_traversal_id is None
            )
            recovers = bool(
                current.cycle_number == previous.cycle_number
                and current.recovery_of_traversal_id == previous.traversal_id
            )
            if not (advances or recovers):
                raise RouteEvidenceIntegrityError(
                    "synthetic endurance attempt order skips or delays recovery"
                )
        if any(item.cycle_number > self.planned_cycle_count for item in self.ordered_attempts):
            raise RouteEvidenceIntegrityError("synthetic endurance exceeds its planned cycle count")
        if (
            self.synthetic_numeric_timeline_only is not True
            or self.real_monotonic_clock_attested is not False
            or self.automatic_retry_allowed is not False
            or self.report_adoption_allowed is not False
            or self.release_authority is not False
            or self.activation_allowed is not False
            or self.input_authority is not False
        ):
            raise RouteEvidenceIntegrityError(
                "synthetic endurance expectation cannot carry authority"
            )

    def to_json_value(self) -> dict[str, object]:
        if _snapshot_endurance_expectation(self) != self:
            raise RouteEvidenceIntegrityError("endurance expectation changed after construction")
        return {
            "activation_allowed": self.activation_allowed,
            "automatic_retry_allowed": self.automatic_retry_allowed,
            "campaign_id": self.campaign_id,
            "input_authority": self.input_authority,
            "ordered_attempts": [item.to_json_value() for item in self.ordered_attempts],
            "planned_cycle_count": self.planned_cycle_count,
            "real_monotonic_clock_attested": self.real_monotonic_clock_attested,
            "release_authority": self.release_authority,
            "report_adoption_allowed": self.report_adoption_allowed,
            "schema": _EXPECTATION_SCHEMA,
            "shared_timeline_id": self.shared_timeline_id,
            "synthetic_numeric_timeline_only": self.synthetic_numeric_timeline_only,
        }


@dataclass(frozen=True, slots=True)
class SyntheticTraversalAttemptRecord:
    """Evaluator-owned nested record; meaningful only inside its campaign report."""

    ordinal: int
    expectation: SyntheticTraversalAttemptExpectation
    round_trip: SyntheticRoundTripRehearsalReport
    outcome: SyntheticEnduranceAttemptOutcome
    boundary_stop_reason: SyntheticEnduranceStopReason | None
    effective_terminal_monotonic_s: float
    first_outbound_departure_monotonic_s: float | None
    cross_traversal_departure_fresh: bool | None
    explicit_recovery: bool
    recovery_fresh_departure_proven: bool
    _source_expectation: SyntheticTraversalAttemptExpectation = field(repr=False)
    _source_round_trip: SyntheticRoundTripRehearsalReport = field(repr=False)
    _source_ordinal: int = field(repr=False)
    _source_prior_terminal_monotonic_s: float | None = field(repr=False)
    _factory_token: InitVar[object | None] = None
    automatic_retry_count: Literal[0] = field(default=0, init=False)
    report_adopted: Literal[False] = field(default=False, init=False)
    live_navigation_enabled: Literal[False] = field(default=False, init=False)
    activation_allowed: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _FACTORY_TOKEN and _factory_token is not _VALIDATION_TOKEN:
            raise RouteEvidenceIntegrityError("endurance attempt records are evaluator-owned")
        if type(self.ordinal) is not int or self.ordinal < 1:
            raise RouteEvidenceIntegrityError("endurance attempt ordinal is invalid")
        if type(self.expectation) is not SyntheticTraversalAttemptExpectation:
            raise RouteEvidenceIntegrityError("endurance attempt expectation is invalid")
        if type(self.round_trip) is not SyntheticRoundTripRehearsalReport:
            raise RouteEvidenceIntegrityError("endurance attempt report is invalid")
        if type(self.outcome) is not SyntheticEnduranceAttemptOutcome:
            raise RouteEvidenceIntegrityError("endurance attempt outcome is invalid")
        if self.outcome is SyntheticEnduranceAttemptOutcome.CAMPAIGN_BOUNDARY_STOPPED:
            if self.boundary_stop_reason is not (
                SyntheticEnduranceStopReason.CROSS_TRAVERSAL_DEPARTURE_NOT_FRESH
            ):
                raise RouteEvidenceIntegrityError("boundary-stopped attempt lost its reason")
        elif self.boundary_stop_reason is not None:
            raise RouteEvidenceIntegrityError("non-boundary attempt carries a boundary reason")
        _time(self.effective_terminal_monotonic_s, "endurance attempt terminal time")
        if self.first_outbound_departure_monotonic_s is not None:
            _time(
                self.first_outbound_departure_monotonic_s,
                "endurance outbound departure time",
            )
        if (
            self.cross_traversal_departure_fresh is not None
            and type(self.cross_traversal_departure_fresh) is not bool
        ):
            raise RouteEvidenceIntegrityError("endurance departure freshness is invalid")
        if type(self._source_ordinal) is not int or self._source_ordinal < 1:
            raise RouteEvidenceIntegrityError("endurance source ordinal is invalid")
        if self.ordinal != self._source_ordinal:
            raise RouteEvidenceIntegrityError("endurance attempt ordinal differs from source")
        if self._source_prior_terminal_monotonic_s is None:
            if self._source_ordinal != 1:
                raise RouteEvidenceIntegrityError("later endurance attempt lost its prior terminal")
        else:
            _time(
                self._source_prior_terminal_monotonic_s,
                "endurance source prior terminal time",
            )
            if self._source_ordinal == 1:
                raise RouteEvidenceIntegrityError("first endurance attempt has a prior terminal")
        expected_departure = _first_outbound_departure(self.round_trip)
        expected_terminal = _effective_terminal(self.round_trip)
        expected_departure_fresh = (
            None
            if self._source_prior_terminal_monotonic_s is None
            else bool(
                expected_departure is not None
                and expected_departure > self._source_prior_terminal_monotonic_s
            )
        )
        if self.cross_traversal_departure_fresh is not expected_departure_fresh:
            raise RouteEvidenceIntegrityError(
                "endurance attempt departure freshness differs from source chronology"
            )
        expected_explicit_recovery = self.expectation.recovery_of_traversal_id is not None
        expected_recovery_fresh = bool(
            expected_explicit_recovery and expected_departure_fresh is True
        )
        if (
            type(self.explicit_recovery) is not bool
            or type(self.recovery_fresh_departure_proven) is not bool
            or self.explicit_recovery is not expected_explicit_recovery
            or self.recovery_fresh_departure_proven is not expected_recovery_fresh
        ):
            raise RouteEvidenceIntegrityError("endurance recovery summary is inconsistent")
        if (
            self.round_trip.phase is not self.expectation.expected_round_trip_phase
            or self.round_trip.stop_reason is not self.expectation.expected_round_trip_stop_reason
        ):
            raise RouteEvidenceIntegrityError(
                "endurance attempt terminal state differs from its manifest"
            )
        if (
            self.effective_terminal_monotonic_s != expected_terminal
            or self.first_outbound_departure_monotonic_s != expected_departure
        ):
            raise RouteEvidenceIntegrityError("endurance attempt timing differs from its report")
        if self.round_trip.phase is SyntheticRoundTripPhase.COMPLETED:
            expected_outcome = (
                SyntheticEnduranceAttemptOutcome.CAMPAIGN_BOUNDARY_STOPPED
                if expected_departure_fresh is False
                else SyntheticEnduranceAttemptOutcome.COMPLETED
            )
        elif expected_departure_fresh is False and expected_departure is not None:
            expected_outcome = SyntheticEnduranceAttemptOutcome.CAMPAIGN_BOUNDARY_STOPPED
        else:
            expected_outcome = SyntheticEnduranceAttemptOutcome.TRAVERSAL_STOPPED
        if self.outcome is not expected_outcome:
            raise RouteEvidenceIntegrityError("endurance attempt outcome differs from its report")
        if (
            self.automatic_retry_count != 0
            or self.report_adopted is not False
            or self.live_navigation_enabled is not False
            or self.activation_allowed is not False
            or self.input_authority is not False
        ):
            raise RouteEvidenceIntegrityError("endurance attempt cannot carry authority")
        if _factory_token is _VALIDATION_TOKEN:
            if _snapshot_attempt_expectation(self._source_expectation) != self.expectation:
                raise RouteEvidenceIntegrityError(
                    "endurance attempt expectation differs from source"
                )
            if type(self._source_round_trip) is not SyntheticRoundTripRehearsalReport:
                raise RouteEvidenceIntegrityError("endurance attempt report source is invalid")
            _validate_report_sources(self._source_round_trip)
            if self.round_trip is not self._source_round_trip:
                _validate_report_sources(self.round_trip)
            if self._source_round_trip != self.round_trip:
                raise RouteEvidenceIntegrityError("endurance attempt report differs from source")


@dataclass(frozen=True, slots=True)
class SyntheticEnduranceRehearsalReport:
    expectation: SyntheticEnduranceExpectation
    attempts: tuple[SyntheticTraversalAttemptRecord, ...]
    phase: SyntheticEndurancePhase
    stop_reason: SyntheticEnduranceStopReason | None
    mine_to_bank_contract_sha256: Sha256Digest
    bank_to_mine_contract_sha256: Sha256Digest
    completed_cycle_count: int
    retained_failure_count: int
    explicit_recovery_count: int
    successful_recovery_count: int
    _source_expectation: SyntheticEnduranceExpectation = field(repr=False)
    _source_round_trips: tuple[SyntheticRoundTripRehearsalReport, ...] = field(repr=False)
    _factory_token: InitVar[object | None] = None
    all_planned_attempts_packaged: Literal[True] = field(default=True, init=False)
    automatic_retry_count: Literal[0] = field(default=0, init=False)
    report_adoption_count: Literal[0] = field(default=0, init=False)
    real_endurance_satisfied: Literal[False] = field(default=False, init=False)
    bank_interface_open_proven: Literal[False] = field(default=False, init=False)
    supported_mining_view_proven: Literal[False] = field(default=False, init=False)
    release_eligible: Literal[False] = field(default=False, init=False)
    live_navigation_enabled: Literal[False] = field(default=False, init=False)
    world_state_activation_allowed: Literal[False] = field(default=False, init=False)
    controller_activation_allowed: Literal[False] = field(default=False, init=False)
    activation_allowed: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _FACTORY_TOKEN and _factory_token is not _VALIDATION_TOKEN:
            raise RouteEvidenceIntegrityError("endurance reports are evaluator-owned")
        if type(self.expectation) is not SyntheticEnduranceExpectation:
            raise RouteEvidenceIntegrityError("endurance report expectation is invalid")
        if (
            type(self.attempts) is not tuple
            or not self.attempts
            or any(type(item) is not SyntheticTraversalAttemptRecord for item in self.attempts)
        ):
            raise RouteEvidenceIntegrityError("endurance report attempts are invalid")
        if type(self.phase) is not SyntheticEndurancePhase:
            raise RouteEvidenceIntegrityError("endurance report phase is invalid")
        if self.phase is SyntheticEndurancePhase.COMPLETED:
            if self.stop_reason is not None:
                raise RouteEvidenceIntegrityError("completed endurance report has a stop reason")
        elif type(self.stop_reason) is not SyntheticEnduranceStopReason:
            raise RouteEvidenceIntegrityError("stopped endurance report lost its reason")
        if (
            type(self.mine_to_bank_contract_sha256) is not Sha256Digest
            or type(self.bank_to_mine_contract_sha256) is not Sha256Digest
        ):
            raise RouteEvidenceIntegrityError("endurance route contract digests are invalid")
        for count in (
            self.completed_cycle_count,
            self.retained_failure_count,
            self.explicit_recovery_count,
            self.successful_recovery_count,
        ):
            if type(count) is not int or count < 0:
                raise RouteEvidenceIntegrityError("endurance report count is invalid")
        if (
            self.all_planned_attempts_packaged is not True
            or self.automatic_retry_count != 0
            or self.report_adoption_count != 0
            or self.real_endurance_satisfied is not False
            or self.bank_interface_open_proven is not False
            or self.supported_mining_view_proven is not False
            or self.release_eligible is not False
            or self.live_navigation_enabled is not False
            or self.world_state_activation_allowed is not False
            or self.controller_activation_allowed is not False
            or self.activation_allowed is not False
            or self.input_authority is not False
        ):
            raise RouteEvidenceIntegrityError("endurance report cannot carry authority")
        if _factory_token is _VALIDATION_TOKEN:
            _validate_endurance_report_sources(self)

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_route_evidence_bytes(self.to_json_value())

    @property
    def content_sha256(self) -> Sha256Digest:
        return Sha256Digest.from_bytes(self.canonical_bytes)

    def to_json_value(self) -> dict[str, object]:
        if replace(self, _factory_token=_VALIDATION_TOKEN) != self:
            raise RouteEvidenceIntegrityError("endurance report changed after evaluation")
        return {
            "activation_allowed": self.activation_allowed,
            "all_planned_attempts_packaged": self.all_planned_attempts_packaged,
            "attempts": [_attempt_record_json(item) for item in self.attempts],
            "automatic_retry_count": self.automatic_retry_count,
            "bank_interface_open_proven": self.bank_interface_open_proven,
            "bank_to_mine_contract_sha256": self.bank_to_mine_contract_sha256.value,
            "completed_cycle_count": self.completed_cycle_count,
            "controller_activation_allowed": self.controller_activation_allowed,
            "endurance_role": SYNTHETIC_ENDURANCE_REHEARSAL_ROLE,
            "expectation": self.expectation.to_json_value(),
            "explicit_recovery_count": self.explicit_recovery_count,
            "input_authority": self.input_authority,
            "live_navigation_enabled": self.live_navigation_enabled,
            "mine_to_bank_contract_sha256": self.mine_to_bank_contract_sha256.value,
            "phase": self.phase.value,
            "real_endurance_satisfied": self.real_endurance_satisfied,
            "release_eligible": self.release_eligible,
            "report_adoption_count": self.report_adoption_count,
            "retained_failure_count": self.retained_failure_count,
            "schema": _REPORT_SCHEMA,
            "stop_reason": None if self.stop_reason is None else self.stop_reason.value,
            "successful_recovery_count": self.successful_recovery_count,
            "supported_mining_view_proven": self.supported_mining_view_proven,
            "world_state_activation_allowed": self.world_state_activation_allowed,
        }


@dataclass(frozen=True, slots=True)
class _NamedDirectionSource:
    direction: RouteDirection
    binding: DirectionProductionBinding
    result: OfflineRouteSessionResult
    evaluated_in_round_trip: bool


@dataclass(frozen=True, slots=True)
class _EnduranceFacts:
    attempts: tuple[SyntheticTraversalAttemptRecord, ...]
    phase: SyntheticEndurancePhase
    stop_reason: SyntheticEnduranceStopReason | None
    mine_to_bank_contract_sha256: Sha256Digest
    bank_to_mine_contract_sha256: Sha256Digest
    completed_cycle_count: int
    retained_failure_count: int
    explicit_recovery_count: int
    successful_recovery_count: int


def _attempt_record_json(record: SyntheticTraversalAttemptRecord) -> dict[str, object]:
    sources = _named_direction_sources(record.round_trip)
    round_trip_json = record.round_trip.to_json_value()
    round_trip_sha256 = record.expectation.round_trip_sha256
    return {
        "activation_allowed": record.activation_allowed,
        "automatic_retry_count": record.automatic_retry_count,
        "boundary_stop_reason": (
            None if record.boundary_stop_reason is None else record.boundary_stop_reason.value
        ),
        "cross_traversal_departure_fresh": record.cross_traversal_departure_fresh,
        "effective_terminal_monotonic_s": record.effective_terminal_monotonic_s,
        "explicit_recovery": record.explicit_recovery,
        "first_outbound_departure_monotonic_s": (record.first_outbound_departure_monotonic_s),
        "input_authority": record.input_authority,
        "live_navigation_enabled": record.live_navigation_enabled,
        "named_direction_histories": [_direction_history_json(source) for source in sources],
        "ordinal": record.ordinal,
        "outcome": record.outcome.value,
        "recovery_fresh_departure_proven": record.recovery_fresh_departure_proven,
        "report_adopted": record.report_adopted,
        "round_trip": round_trip_json,
        "round_trip_sha256": round_trip_sha256.value,
        "schema": _ATTEMPT_RECORD_SCHEMA,
        "traversal_expectation": record.expectation.to_json_value(),
    }


def _snapshot_attempt_expectation(
    value: SyntheticTraversalAttemptExpectation,
) -> SyntheticTraversalAttemptExpectation:
    def copy_once(
        candidate: SyntheticTraversalAttemptExpectation,
    ) -> SyntheticTraversalAttemptExpectation:
        if type(candidate) is not SyntheticTraversalAttemptExpectation:
            raise TypeError("synthetic traversal expectation has the wrong type")
        if (
            candidate.synthetic_only is not True
            or candidate.automatic_retry_allowed is not False
            or candidate.input_authority is not False
        ):
            raise ValueError("synthetic traversal expectation authority was mutated")
        return SyntheticTraversalAttemptExpectation(
            traversal_id=candidate.traversal_id,
            cycle_number=candidate.cycle_number,
            scenario_id=candidate.scenario_id,
            round_trip_sha256=Sha256Digest(candidate.round_trip_sha256.value),
            expected_round_trip_phase=candidate.expected_round_trip_phase,
            expected_round_trip_stop_reason=candidate.expected_round_trip_stop_reason,
            recovery_of_traversal_id=candidate.recovery_of_traversal_id,
        )

    try:
        first = copy_once(value)
        second = copy_once(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise RouteEvidenceIntegrityError("synthetic traversal expectation is malformed") from exc
    if first != second:
        raise RouteEvidenceIntegrityError("synthetic traversal expectation changed during snapshot")
    return second


def _snapshot_endurance_expectation(
    value: SyntheticEnduranceExpectation,
) -> SyntheticEnduranceExpectation:
    def copy_once(candidate: SyntheticEnduranceExpectation) -> SyntheticEnduranceExpectation:
        if type(candidate) is not SyntheticEnduranceExpectation:
            raise TypeError("synthetic endurance expectation has the wrong type")
        if (
            candidate.synthetic_numeric_timeline_only is not True
            or candidate.real_monotonic_clock_attested is not False
            or candidate.automatic_retry_allowed is not False
            or candidate.report_adoption_allowed is not False
            or candidate.release_authority is not False
            or candidate.activation_allowed is not False
            or candidate.input_authority is not False
        ):
            raise ValueError("synthetic endurance expectation authority was mutated")
        return SyntheticEnduranceExpectation(
            campaign_id=candidate.campaign_id,
            shared_timeline_id=candidate.shared_timeline_id,
            planned_cycle_count=candidate.planned_cycle_count,
            ordered_attempts=tuple(
                _snapshot_attempt_expectation(item) for item in candidate.ordered_attempts
            ),
        )

    try:
        first = copy_once(value)
        second = copy_once(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise RouteEvidenceIntegrityError("synthetic endurance expectation is malformed") from exc
    if first != second:
        raise RouteEvidenceIntegrityError("synthetic endurance expectation changed during snapshot")
    return second


def _snapshot_round_trips(
    values: tuple[SyntheticRoundTripRehearsalReport, ...],
) -> tuple[SyntheticRoundTripRehearsalReport, ...]:
    if type(values) is not tuple or not values:
        raise RouteEvidenceIntegrityError("synthetic endurance requires an exact report tuple")
    try:
        owned = tuple(_snapshot_synthetic_round_trip_report(value) for value in values)
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise RouteEvidenceIntegrityError("synthetic endurance report tuple is malformed") from exc
    return owned


def _named_direction_sources(
    report: SyntheticRoundTripRehearsalReport,
) -> tuple[_NamedDirectionSource, _NamedDirectionSource]:
    decision = report._source_decision
    return (
        _NamedDirectionSource(
            RouteDirection.MINE_TO_BANK,
            decision.mine_to_bank,
            _owned_result(decision.mine_to_bank._source_post_attempt_result),
            True,
        ),
        _NamedDirectionSource(
            RouteDirection.BANK_TO_MINE,
            decision.bank_to_mine,
            _owned_result(decision.bank_to_mine._source_post_attempt_result),
            report.bank_to_mine is not None,
        ),
    )


def _stable_direction_contract_sha256(source: _NamedDirectionSource) -> Sha256Digest:
    binding = source.binding
    expectation = binding.expectation
    context = source.result.progress.session.context
    attempt_source = context.expected_attempt_source
    return Sha256Digest.from_bytes(
        canonical_route_evidence_bytes(
            {
                "attempt_source": {
                    "evidence_role": attempt_source.evidence_role.value,
                    "source_id": attempt_source.source_id,
                    "version": attempt_source.version,
                },
                "capture_build": expectation.capture_build.to_json_value(),
                "capture_configuration_sha256": expectation.capture_configuration_sha256.value,
                "capture_environment_sha256": expectation.capture_environment_sha256.value,
                "capture_source_id": expectation.capture_source_id,
                "detector": {
                    "detector_id": expectation.detector.detector_id,
                    "version": expectation.detector.version,
                },
                "frame": {
                    "height": expectation.frame_height,
                    "pixel_format": expectation.pixel_format.value,
                    "width": expectation.frame_width,
                },
                "navigation_policy": {
                    "max_attempt_receipt_age_s": context.policy.max_attempt_receipt_age_s,
                    "max_frame_age_s": context.policy.max_frame_age_s,
                    "minimum_confidence": context.policy.minimum_confidence,
                },
                "profile": {
                    "content_sha256": expectation.profile.content_sha256.value,
                    "profile_id": expectation.profile.profile_id,
                    "version": expectation.profile.version,
                },
                "route": _route_json(binding.route_plan.identity),
                "route_plan_sha256": expectation.route_plan_sha256.value,
                "support_envelope_sha256": expectation.support_envelope_sha256.value,
            }
        )
    )


def _direction_history_json(source: _NamedDirectionSource) -> dict[str, object]:
    binding = source.binding
    progress = source.result.progress
    context = progress.session.context
    attempts = progress.navigation.completed_attempts
    pending = progress.navigation.pending_step_proposal
    return {
        "accepted_checkpoint_count": progress.navigation.accepted_checkpoint_count,
        "attempt_source": {
            "session_id": context.expected_attempt_source.session_id,
            "source_id": context.expected_attempt_source.source_id,
            "version": context.expected_attempt_source.version,
        },
        "capture_source": {
            "capture_session_id": context.expected_source.capture_session_id,
            "frame_source_id": context.expected_source.frame_source_id,
        },
        "completed_attempts": [
            {
                "attempt_id": item.identity.attempt_id,
                "checkpoint_frame_id": (
                    item.proposal.checkpoint_evidence.provenance.frame.frame_id
                ),
                "checkpoint_frame_monotonic_s": (
                    item.proposal.checkpoint_evidence.provenance.frame.captured_monotonic_s
                ),
                "checkpoint_id": item.proposal.step.from_checkpoint_id,
                "post_attempt_monotonic_s": item.receipt.post_attempt_monotonic_s,
                "step_id": item.identity.step_id,
            }
            for item in attempts
        ],
        "direction": source.direction.value,
        "durable_lineage": {
            "acquisition_root_identity": list(binding.acquisition_root.physical_root_identity),
            "campaign_id": binding.expectation.campaign_id,
            "finalized_package_sha256": binding.expectation.finalized_package_sha256.value,
            "independent_review_sha256": binding.expectation.independent_review_sha256.value,
            "review_id": binding.expectation.review_id,
            "review_root_identity": list(binding.review_root.physical_root_identity),
        },
        "evaluated_in_round_trip": source.evaluated_in_round_trip,
        "last_event_monotonic_s": progress.last_event_monotonic_s,
        "navigation_failure_reason": (
            None
            if progress.navigation.failure_reason is None
            else progress.navigation.failure_reason.value
        ),
        "pending_attempt_id": (None if pending is None else pending.attempt_identity.attempt_id),
        "route": _route_json(binding.route_plan.identity),
        "route_session_id": progress.session.session_id,
        "session_result_sha256": binding.post_attempt_causality.session_result_sha256.value,
        "session_stop_reason": (
            None if progress.stop_reason is None else progress.stop_reason.value
        ),
        "terminal_phase": progress.phase.value,
    }


def _first_outbound_departure(report: SyntheticRoundTripRehearsalReport) -> float | None:
    source = _named_direction_sources(report)[0]
    attempts = source.result.progress.navigation.completed_attempts
    if not attempts:
        return None
    return attempts[0].proposal.checkpoint_evidence.provenance.frame.captured_monotonic_s


def _effective_terminal(report: SyntheticRoundTripRehearsalReport) -> float:
    sources = _named_direction_sources(report)
    terminal = sources[1] if report.bank_to_mine is not None else sources[0]
    return terminal.result.progress.last_event_monotonic_s


def _claim_unique(seen: set[object], value: object, field_name: str) -> None:
    if value in seen:
        raise RouteEvidenceIntegrityError(f"synthetic endurance reused {field_name}")
    seen.add(value)


def _fold_endurance(
    expectation: SyntheticEnduranceExpectation,
    reports: tuple[SyntheticRoundTripRehearsalReport, ...],
) -> _EnduranceFacts:
    if len(reports) != len(expectation.ordered_attempts):
        raise RouteEvidenceIntegrityError("synthetic endurance report count differs from manifest")

    seen_report_digests: set[object] = set()
    seen_decision_digests: set[object] = set()
    seen_route_sessions: set[object] = set()
    seen_capture_sessions: set[object] = set()
    seen_attempt_source_sessions: set[object] = set()
    seen_attempt_ids: set[object] = set()
    seen_campaign_ids: set[object] = set()
    seen_review_ids: set[object] = set()
    seen_package_digests: set[object] = set()
    seen_review_digests: set[object] = set()
    seen_root_identities: set[object] = set()
    baseline_contracts: dict[RouteDirection, Sha256Digest] = {}

    attempts: list[SyntheticTraversalAttemptRecord] = []
    completed_cycles = 0
    retained_failures = 0
    explicit_recoveries = 0
    successful_recoveries = 0
    previous: SyntheticTraversalAttemptRecord | None = None

    for ordinal, (attempt_expectation, report) in enumerate(
        zip(expectation.ordered_attempts, reports, strict=True),
        start=1,
    ):
        if report.timeline.timeline_id != expectation.shared_timeline_id:
            raise RouteEvidenceIntegrityError("synthetic endurance timeline id changed")
        if (
            report.content_sha256 != attempt_expectation.round_trip_sha256
            or report.phase is not attempt_expectation.expected_round_trip_phase
            or report.stop_reason is not attempt_expectation.expected_round_trip_stop_reason
        ):
            raise RouteEvidenceIntegrityError(
                "synthetic endurance report differs from its exact attempt manifest"
            )
        _claim_unique(
            seen_report_digests,
            report.content_sha256,
            "round-trip report digest",
        )
        _claim_unique(
            seen_decision_digests,
            report.release_decision_sha256,
            "release decision digest",
        )

        sources = _named_direction_sources(report)
        for source in sources:
            binding = source.binding
            progress = source.result.progress
            context = progress.session.context
            contract = _stable_direction_contract_sha256(source)
            baseline = baseline_contracts.setdefault(source.direction, contract)
            if contract != baseline:
                raise RouteEvidenceIntegrityError(
                    f"synthetic endurance {source.direction.value} contract changed"
                )
            _claim_unique(seen_route_sessions, progress.session.session_id, "route session id")
            _claim_unique(
                seen_capture_sessions,
                context.expected_source.capture_session_id,
                "capture session id",
            )
            _claim_unique(
                seen_attempt_source_sessions,
                context.expected_attempt_source.session_id,
                "attempt-source session id",
            )
            _claim_unique(seen_campaign_ids, binding.expectation.campaign_id, "campaign id")
            _claim_unique(seen_review_ids, binding.expectation.review_id, "review id")
            _claim_unique(
                seen_package_digests,
                binding.expectation.finalized_package_sha256,
                "finalized package digest",
            )
            _claim_unique(
                seen_review_digests,
                binding.expectation.independent_review_sha256,
                "independent review digest",
            )
            for root_identity in (
                binding.acquisition_root.physical_root_identity,
                binding.review_root.physical_root_identity,
            ):
                _claim_unique(
                    seen_root_identities,
                    root_identity,
                    "durable physical root identity",
                )
            for item in progress.navigation.completed_attempts:
                _claim_unique(
                    seen_attempt_ids,
                    item.identity.attempt_id,
                    "completed attempt id",
                )
            pending = progress.navigation.pending_step_proposal
            if pending is not None:
                _claim_unique(
                    seen_attempt_ids,
                    pending.attempt_identity.attempt_id,
                    "pending attempt id",
                )

        terminal = _effective_terminal(report)
        departure = _first_outbound_departure(report)
        prior_terminal = None if previous is None else previous.effective_terminal_monotonic_s
        if prior_terminal is not None and terminal <= prior_terminal:
            raise RouteEvidenceIntegrityError(
                "synthetic endurance terminal history is not strictly ordered"
            )
        departure_fresh = (
            None
            if prior_terminal is None
            else bool(departure is not None and departure > prior_terminal)
        )
        if previous is None:
            if (
                attempt_expectation.cycle_number != 1
                or attempt_expectation.recovery_of_traversal_id is not None
            ):
                raise RouteEvidenceIntegrityError("synthetic endurance first attempt is invalid")
        elif previous.outcome is SyntheticEnduranceAttemptOutcome.COMPLETED:
            if (
                attempt_expectation.cycle_number != previous.expectation.cycle_number + 1
                or attempt_expectation.recovery_of_traversal_id is not None
            ):
                raise RouteEvidenceIntegrityError("synthetic endurance recovered after success")
        elif (
            attempt_expectation.cycle_number != previous.expectation.cycle_number
            or attempt_expectation.recovery_of_traversal_id != previous.expectation.traversal_id
        ):
            raise RouteEvidenceIntegrityError(
                "synthetic endurance failure was not recovered exactly"
            )

        if report.phase is SyntheticRoundTripPhase.COMPLETED and departure_fresh is not False:
            outcome = SyntheticEnduranceAttemptOutcome.COMPLETED
            boundary_reason = None
        elif report.phase is SyntheticRoundTripPhase.COMPLETED:
            outcome = SyntheticEnduranceAttemptOutcome.CAMPAIGN_BOUNDARY_STOPPED
            boundary_reason = SyntheticEnduranceStopReason.CROSS_TRAVERSAL_DEPARTURE_NOT_FRESH
        elif departure_fresh is False and departure is not None:
            outcome = SyntheticEnduranceAttemptOutcome.CAMPAIGN_BOUNDARY_STOPPED
            boundary_reason = SyntheticEnduranceStopReason.CROSS_TRAVERSAL_DEPARTURE_NOT_FRESH
        else:
            outcome = SyntheticEnduranceAttemptOutcome.TRAVERSAL_STOPPED
            boundary_reason = None

        if outcome is SyntheticEnduranceAttemptOutcome.COMPLETED:
            if attempt_expectation.cycle_number != completed_cycles + 1:
                raise RouteEvidenceIntegrityError("synthetic endurance completed a wrong cycle")
            completed_cycles += 1
        else:
            retained_failures += 1
        if attempt_expectation.recovery_of_traversal_id is not None:
            explicit_recoveries += 1
            if outcome is SyntheticEnduranceAttemptOutcome.COMPLETED:
                successful_recoveries += 1

        record = SyntheticTraversalAttemptRecord(
            ordinal=ordinal,
            expectation=_snapshot_attempt_expectation(attempt_expectation),
            round_trip=report,
            outcome=outcome,
            boundary_stop_reason=boundary_reason,
            effective_terminal_monotonic_s=terminal,
            first_outbound_departure_monotonic_s=departure,
            cross_traversal_departure_fresh=departure_fresh,
            explicit_recovery=(attempt_expectation.recovery_of_traversal_id is not None),
            recovery_fresh_departure_proven=bool(
                attempt_expectation.recovery_of_traversal_id is not None and departure_fresh is True
            ),
            _source_expectation=attempt_expectation,
            _source_round_trip=report,
            _source_ordinal=ordinal,
            _source_prior_terminal_monotonic_s=prior_terminal,
            _factory_token=_FACTORY_TOKEN,
        )
        attempts.append(record)
        previous = record
        if completed_cycles == expectation.planned_cycle_count and ordinal != len(reports):
            raise RouteEvidenceIntegrityError(
                "synthetic endurance contains attempts after its planned target"
            )

    if previous is None:  # pragma: no cover - expectation validates a non-empty tuple
        raise AssertionError("synthetic endurance fold omitted every attempt")
    if (
        completed_cycles == expectation.planned_cycle_count
        and previous.outcome is SyntheticEnduranceAttemptOutcome.COMPLETED
    ):
        phase = SyntheticEndurancePhase.COMPLETED
        stop_reason = None
    else:
        phase = SyntheticEndurancePhase.STOPPED
        if previous.outcome is SyntheticEnduranceAttemptOutcome.CAMPAIGN_BOUNDARY_STOPPED:
            stop_reason = SyntheticEnduranceStopReason.CROSS_TRAVERSAL_DEPARTURE_NOT_FRESH
        elif previous.outcome is SyntheticEnduranceAttemptOutcome.TRAVERSAL_STOPPED:
            stop_reason = SyntheticEnduranceStopReason.UNRECOVERED_TRAVERSAL
        else:
            stop_reason = SyntheticEnduranceStopReason.PLANNED_CYCLE_TARGET_NOT_MET

    return _EnduranceFacts(
        attempts=tuple(attempts),
        phase=phase,
        stop_reason=stop_reason,
        mine_to_bank_contract_sha256=baseline_contracts[RouteDirection.MINE_TO_BANK],
        bank_to_mine_contract_sha256=baseline_contracts[RouteDirection.BANK_TO_MINE],
        completed_cycle_count=completed_cycles,
        retained_failure_count=retained_failures,
        explicit_recovery_count=explicit_recoveries,
        successful_recovery_count=successful_recoveries,
    )


def _validate_endurance_report_sources(report: SyntheticEnduranceRehearsalReport) -> None:
    source_expectation = _snapshot_endurance_expectation(report._source_expectation)
    public_expectation = _snapshot_endurance_expectation(report.expectation)
    if source_expectation != public_expectation or public_expectation != report.expectation:
        raise RouteEvidenceIntegrityError("endurance report expectation differs from source")
    source_reports = report._source_round_trips
    if (
        type(source_reports) is not tuple
        or not source_reports
        or any(type(item) is not SyntheticRoundTripRehearsalReport for item in source_reports)
    ):
        raise RouteEvidenceIntegrityError("endurance report source history is invalid")
    public_reports = tuple(item.round_trip for item in report.attempts)
    if len(source_reports) != len(public_reports):
        raise RouteEvidenceIntegrityError("endurance report traversal history length changed")
    if source_reports != public_reports:
        raise RouteEvidenceIntegrityError("endurance report traversal history differs from sources")
    facts = _fold_endurance(source_expectation, source_reports)
    observed = (
        report.attempts,
        report.phase,
        report.stop_reason,
        report.mine_to_bank_contract_sha256,
        report.bank_to_mine_contract_sha256,
        report.completed_cycle_count,
        report.retained_failure_count,
        report.explicit_recovery_count,
        report.successful_recovery_count,
    )
    expected = (
        facts.attempts,
        facts.phase,
        facts.stop_reason,
        facts.mine_to_bank_contract_sha256,
        facts.bank_to_mine_contract_sha256,
        facts.completed_cycle_count,
        facts.retained_failure_count,
        facts.explicit_recovery_count,
        facts.successful_recovery_count,
    )
    if observed != expected:
        raise RouteEvidenceIntegrityError("endurance report differs from exact source fold")


def evaluate_synthetic_endurance_rehearsal(
    expectation: SyntheticEnduranceExpectation,
    *,
    round_trips: tuple[SyntheticRoundTripRehearsalReport, ...],
) -> SyntheticEnduranceRehearsalReport:
    """Fold one complete, exact report tuple without retry or execution authority."""

    owned_expectation = _snapshot_endurance_expectation(expectation)
    owned_reports = _snapshot_round_trips(round_trips)
    facts = _fold_endurance(owned_expectation, owned_reports)
    return SyntheticEnduranceRehearsalReport(
        expectation=_snapshot_endurance_expectation(owned_expectation),
        attempts=facts.attempts,
        phase=facts.phase,
        stop_reason=facts.stop_reason,
        mine_to_bank_contract_sha256=facts.mine_to_bank_contract_sha256,
        bank_to_mine_contract_sha256=facts.bank_to_mine_contract_sha256,
        completed_cycle_count=facts.completed_cycle_count,
        retained_failure_count=facts.retained_failure_count,
        explicit_recovery_count=facts.explicit_recovery_count,
        successful_recovery_count=facts.successful_recovery_count,
        _source_expectation=_snapshot_endurance_expectation(owned_expectation),
        _source_round_trips=owned_reports,
        _factory_token=_FACTORY_TOKEN,
    )
