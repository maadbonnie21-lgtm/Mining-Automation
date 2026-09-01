"""Strict, display-free replay manifests for the fixed-route reducer."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from pathlib import Path
from typing import Final, Literal, cast

from ..contracts import FrameRef
from .contracts import (
    Checkpoint,
    CheckpointMatchKind,
    CheckpointObservation,
    CheckpointRole,
    CheckpointSourceIdentity,
    FrameProvenance,
    NavigationFailureReason,
    NavigationPhase,
    NavigationPolicy,
    NavigationTransitionOutcome,
    OfflineStepProposal,
    RouteDirection,
    RouteEndpoint,
    RouteEndpointRole,
    RouteEvaluationContext,
    RouteIdentity,
    RoutePlan,
    RouteProgress,
    RouteStep,
)
from .machine import observe_checkpoint, prepare_step, start_route

__all__ = [
    "NAVIGATION_REPLAY_SCHEMA_VERSION",
    "SYNTHETIC_FIXTURE_ROLE",
    "NavigationManifestError",
    "NavigationReplayManifest",
    "NavigationReplayReport",
    "ObserveCheckpointEvent",
    "PrepareStepEvent",
    "ReplayExpectedState",
    "ReplayMismatch",
    "ReplayTraceEntry",
    "load_navigation_replay",
    "run_navigation_replay",
]

NAVIGATION_REPLAY_SCHEMA_VERSION: Final[int] = 1
SYNTHETIC_FIXTURE_ROLE: Final[str] = "synthetic_navigation_architecture_test_only"


class NavigationManifestError(ValueError):
    """A navigation replay manifest is malformed or outside offline scope."""


class _DuplicateKeyError(ValueError):
    pass


class ReplayEventKind(StrEnum):
    OBSERVE_CHECKPOINT = "observe_checkpoint"
    PREPARE_STEP = "prepare_step"


@dataclass(frozen=True, slots=True)
class ReplayExpectedState:
    outcome: NavigationTransitionOutcome
    phase: NavigationPhase
    current_checkpoint_id: str | None
    expected_next_checkpoint_id: str | None
    failure_reason: NavigationFailureReason | None
    proposed_step_id: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, NavigationTransitionOutcome):
            raise ValueError("expected outcome must be a NavigationTransitionOutcome")
        if not isinstance(self.phase, NavigationPhase):
            raise ValueError("expected phase must be a NavigationPhase")
        if self.current_checkpoint_id is not None:
            _require_identifier(self.current_checkpoint_id, "expected current checkpoint")
        if self.expected_next_checkpoint_id is not None:
            _require_identifier(self.expected_next_checkpoint_id, "expected next checkpoint")
        if self.failure_reason is not None and not isinstance(
            self.failure_reason, NavigationFailureReason
        ):
            raise ValueError("expected failure reason must be a NavigationFailureReason or None")
        if self.proposed_step_id is not None:
            _require_identifier(self.proposed_step_id, "expected proposed step")


@dataclass(frozen=True, slots=True)
class ObserveCheckpointEvent:
    evaluated_monotonic_s: float
    observation: CheckpointObservation
    expected: ReplayExpectedState

    def __post_init__(self) -> None:
        _require_nonnegative_time(self.evaluated_monotonic_s, "event evaluation time")
        if not isinstance(self.observation, CheckpointObservation):
            raise ValueError("observe event requires a CheckpointObservation")
        if not isinstance(self.expected, ReplayExpectedState):
            raise ValueError("observe event requires ReplayExpectedState")


@dataclass(frozen=True, slots=True)
class PrepareStepEvent:
    evaluated_monotonic_s: float
    expected: ReplayExpectedState

    def __post_init__(self) -> None:
        _require_nonnegative_time(self.evaluated_monotonic_s, "event evaluation time")
        if not isinstance(self.expected, ReplayExpectedState):
            raise ValueError("prepare event requires ReplayExpectedState")


ReplayEvent = ObserveCheckpointEvent | PrepareStepEvent


@dataclass(frozen=True, slots=True)
class NavigationReplayManifest:
    schema_version: int
    fixture_role: str
    case_id: str
    started_monotonic_s: float
    context: RouteEvaluationContext
    events: tuple[ReplayEvent, ...]
    expected_final_phase: NavigationPhase

    def __post_init__(self) -> None:
        if (
            not _is_integer(self.schema_version)
            or self.schema_version != NAVIGATION_REPLAY_SCHEMA_VERSION
        ):
            raise ValueError(f"unsupported navigation replay schema {self.schema_version!r}")
        if self.fixture_role != SYNTHETIC_FIXTURE_ROLE:
            raise ValueError("navigation replay manifests must be explicitly synthetic-only")
        _require_identifier(self.case_id, "case_id")
        _require_nonnegative_time(self.started_monotonic_s, "route start time")
        if not isinstance(self.context, RouteEvaluationContext):
            raise ValueError("replay context must be a RouteEvaluationContext")
        if not isinstance(self.events, tuple) or not self.events:
            raise ValueError("replay events must be a non-empty tuple")
        if any(not isinstance(event, (ObserveCheckpointEvent, PrepareStepEvent)) for event in self.events):
            raise ValueError("replay events contain an unsupported event value")
        event_times = tuple(event.evaluated_monotonic_s for event in self.events)
        if event_times[0] < self.started_monotonic_s or any(
            later < earlier for earlier, later in zip(event_times, event_times[1:], strict=False)
        ):
            raise ValueError("replay event times must be nondecreasing from the route start")
        if self.expected_final_phase not in {NavigationPhase.ARRIVED, NavigationPhase.STOPPED}:
            raise ValueError("expected_final_phase must be arrived or stopped")


@dataclass(frozen=True, slots=True)
class ReplayMismatch:
    event_index: int
    field: str
    expected: str | None
    actual: str | None

    def __post_init__(self) -> None:
        if not _is_integer(self.event_index) or self.event_index < 0:
            raise ValueError("mismatch event index must be a non-negative integer")
        _require_identifier(self.field, "mismatch field")
        if self.expected is not None and not isinstance(self.expected, str):
            raise ValueError("mismatch expected value must be a string or None")
        if self.actual is not None and not isinstance(self.actual, str):
            raise ValueError("mismatch actual value must be a string or None")


@dataclass(frozen=True, slots=True)
class ReplayTraceEntry:
    event_index: int
    outcome: NavigationTransitionOutcome
    phase: NavigationPhase
    current_checkpoint_id: str | None
    expected_next_checkpoint_id: str | None
    failure_reason: NavigationFailureReason | None
    proposed_step_id: str | None

    def __post_init__(self) -> None:
        if not _is_integer(self.event_index) or self.event_index < 0:
            raise ValueError("trace event index must be a non-negative integer")
        if not isinstance(self.outcome, NavigationTransitionOutcome):
            raise ValueError("trace outcome must be a NavigationTransitionOutcome")
        if not isinstance(self.phase, NavigationPhase):
            raise ValueError("trace phase must be a NavigationPhase")
        if self.current_checkpoint_id is not None:
            _require_identifier(self.current_checkpoint_id, "trace current checkpoint")
        if self.expected_next_checkpoint_id is not None:
            _require_identifier(self.expected_next_checkpoint_id, "trace next checkpoint")
        if self.failure_reason is not None and not isinstance(
            self.failure_reason, NavigationFailureReason
        ):
            raise ValueError("trace failure reason must be a NavigationFailureReason or None")
        if self.proposed_step_id is not None:
            _require_identifier(self.proposed_step_id, "trace proposed step")

        expected_phase = {
            NavigationTransitionOutcome.CHECKPOINT_ACCEPTED: NavigationPhase.READY_FOR_STEP,
            NavigationTransitionOutcome.STEP_PREPARED: NavigationPhase.AWAITING_CHECKPOINT,
            NavigationTransitionOutcome.ARRIVAL_CONFIRMED: NavigationPhase.ARRIVED,
            NavigationTransitionOutcome.STOPPED: NavigationPhase.STOPPED,
        }.get(self.outcome)
        if expected_phase is not None and self.phase is not expected_phase:
            raise ValueError("trace outcome does not match its phase")
        if (
            self.outcome is NavigationTransitionOutcome.TERMINAL_NO_CHANGE
            and self.phase not in {NavigationPhase.ARRIVED, NavigationPhase.STOPPED}
        ):
            raise ValueError("trace terminal no-change requires a terminal phase")
        if (self.outcome is NavigationTransitionOutcome.STEP_PREPARED) is (
            self.proposed_step_id is None
        ):
            raise ValueError("only a prepared-step trace may contain a proposed step")
        if (self.phase is NavigationPhase.STOPPED) is (self.failure_reason is None):
            raise ValueError("only a stopped trace must contain a failure reason")

        location_shape = {
            NavigationPhase.AWAITING_CHECKPOINT: (False, True),
            NavigationPhase.READY_FOR_STEP: (True, True),
            NavigationPhase.ARRIVED: (True, False),
            NavigationPhase.STOPPED: (False, False),
        }[self.phase]
        actual_location_shape = (
            self.current_checkpoint_id is not None,
            self.expected_next_checkpoint_id is not None,
        )
        if actual_location_shape != location_shape:
            raise ValueError("trace checkpoint fields do not match its phase")


@dataclass(frozen=True, slots=True)
class NavigationReplayReport:
    case_id: str
    fixture_role: str
    route: RouteIdentity
    trace: tuple[ReplayTraceEntry, ...]
    step_proposals: tuple[OfflineStepProposal, ...]
    final_progress: RouteProgress
    mismatches: tuple[ReplayMismatch, ...]
    live_navigation_enabled: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        _require_identifier(self.case_id, "report case_id")
        if self.fixture_role != SYNTHETIC_FIXTURE_ROLE:
            raise ValueError("navigation replay reports must retain the synthetic fixture role")
        if not isinstance(self.route, RouteIdentity):
            raise ValueError("report route must be a RouteIdentity")
        if not isinstance(self.trace, tuple) or not self.trace or any(
            not isinstance(entry, ReplayTraceEntry) for entry in self.trace
        ):
            raise ValueError("report trace must be a non-empty tuple of ReplayTraceEntry values")
        if tuple(entry.event_index for entry in self.trace) != tuple(range(len(self.trace))):
            raise ValueError("report trace event indexes must be contiguous from zero")
        if not isinstance(self.final_progress, RouteProgress) or self.final_progress.route != self.route:
            raise ValueError("report final progress must belong to its route")
        if not isinstance(self.step_proposals, tuple) or any(
            not isinstance(proposal, OfflineStepProposal)
            or proposal.context != self.final_progress.context
            for proposal in self.step_proposals
        ):
            raise ValueError("report proposals must belong to its exact evaluation context")
        traced_step_ids = tuple(
            entry.proposed_step_id
            for entry in self.trace
            if entry.outcome is NavigationTransitionOutcome.STEP_PREPARED
        )
        proposal_step_ids = tuple(proposal.step.step_id for proposal in self.step_proposals)
        if traced_step_ids != proposal_step_ids:
            raise ValueError("report proposals must exactly match its prepared-step trace")
        if not isinstance(self.mismatches, tuple) or any(
            not isinstance(mismatch, ReplayMismatch) for mismatch in self.mismatches
        ):
            raise ValueError("report mismatches must be a tuple of ReplayMismatch values")
        last_entry = self.trace[-1]
        if (
            last_entry.phase is not self.final_progress.phase
            or last_entry.current_checkpoint_id != self.final_progress.current_checkpoint_id
            or last_entry.expected_next_checkpoint_id
            != self.final_progress.expected_next_checkpoint_id
            or last_entry.failure_reason is not self.final_progress.failure_reason
        ):
            raise ValueError("report final trace must match its final progress")
        _validate_trace_route_sequence(self.trace, self.final_progress)
        if not self.mismatches and self.final_progress.phase not in {
            NavigationPhase.ARRIVED,
            NavigationPhase.STOPPED,
        }:
            raise ValueError("a passing report requires a traced terminal final state")

    @property
    def passed(self) -> bool:
        return not self.mismatches

    def to_json(self) -> str:
        payload = {
            "case_id": self.case_id,
            "fixture_role": self.fixture_role,
            "route": {
                "route_id": self.route.route_id,
                "version": self.route.version,
                "direction": self.route.direction.value,
            },
            "live_navigation_enabled": self.live_navigation_enabled,
            "passed": self.passed,
            "final_phase": self.final_progress.phase.value,
            "mismatches": [
                {
                    "event_index": mismatch.event_index,
                    "field": mismatch.field,
                    "expected": mismatch.expected,
                    "actual": mismatch.actual,
                }
                for mismatch in self.mismatches
            ],
            "trace": [
                {
                    "event_index": entry.event_index,
                    "outcome": entry.outcome.value,
                    "phase": entry.phase.value,
                    "current_checkpoint_id": entry.current_checkpoint_id,
                    "expected_next_checkpoint_id": entry.expected_next_checkpoint_id,
                    "failure_reason": _enum_value(entry.failure_reason),
                    "proposed_step_id": entry.proposed_step_id,
                }
                for entry in self.trace
            ],
        }
        return json.dumps(payload, indent=2, sort_keys=True) + "\n"

    def render_text(self) -> str:
        result = "PASS" if self.passed else "FAIL"
        lines = [
            f"navigation replay {result}: {self.case_id}",
            f"fixture role: {self.fixture_role}",
            (
                f"route: {self.route.route_id}@{self.route.version} "
                f"({self.route.direction.value})"
            ),
            "live navigation: disabled",
            f"final phase: {self.final_progress.phase.value}",
            f"events: {len(self.trace)}",
            f"offline step proposals: {len(self.step_proposals)}",
        ]
        for mismatch in self.mismatches:
            lines.append(
                f"event {mismatch.event_index} {mismatch.field}: "
                f"expected {mismatch.expected!r}, got {mismatch.actual!r}"
            )
        return "\n".join(lines) + "\n"


def _validate_trace_route_sequence(
    trace: tuple[ReplayTraceEntry, ...],
    final_progress: RouteProgress,
) -> None:
    checkpoints = final_progress.context.plan.checkpoints
    steps = final_progress.context.plan.steps
    phase = NavigationPhase.AWAITING_CHECKPOINT
    accepted_checkpoint_count = 0
    terminal_snapshot: tuple[
        NavigationPhase,
        str | None,
        str | None,
        NavigationFailureReason | None,
    ] | None = None

    for entry in trace:
        snapshot = (
            entry.phase,
            entry.current_checkpoint_id,
            entry.expected_next_checkpoint_id,
            entry.failure_reason,
        )
        if terminal_snapshot is not None:
            if (
                entry.outcome is not NavigationTransitionOutcome.TERMINAL_NO_CHANGE
                or snapshot != terminal_snapshot
            ):
                raise ValueError("report trace must remain unchanged after a terminal state")
            continue

        if entry.outcome is NavigationTransitionOutcome.CHECKPOINT_ACCEPTED:
            if (
                phase is not NavigationPhase.AWAITING_CHECKPOINT
                or accepted_checkpoint_count >= len(checkpoints) - 1
                or entry.current_checkpoint_id
                != checkpoints[accepted_checkpoint_count].checkpoint_id
                or entry.expected_next_checkpoint_id
                != checkpoints[accepted_checkpoint_count + 1].checkpoint_id
            ):
                raise ValueError("report trace checkpoint acceptance violates route sequence")
            accepted_checkpoint_count += 1
            phase = NavigationPhase.READY_FOR_STEP
        elif entry.outcome is NavigationTransitionOutcome.STEP_PREPARED:
            if (
                phase is not NavigationPhase.READY_FOR_STEP
                or accepted_checkpoint_count == 0
                or accepted_checkpoint_count >= len(checkpoints)
                or entry.expected_next_checkpoint_id
                != checkpoints[accepted_checkpoint_count].checkpoint_id
                or entry.proposed_step_id != steps[accepted_checkpoint_count - 1].step_id
            ):
                raise ValueError("report prepared-step trace violates route sequence")
            phase = NavigationPhase.AWAITING_CHECKPOINT
        elif entry.outcome is NavigationTransitionOutcome.ARRIVAL_CONFIRMED:
            if (
                phase is not NavigationPhase.AWAITING_CHECKPOINT
                or accepted_checkpoint_count != len(checkpoints) - 1
                or entry.current_checkpoint_id != checkpoints[-1].checkpoint_id
            ):
                raise ValueError("report arrival trace violates route sequence")
            accepted_checkpoint_count += 1
            phase = NavigationPhase.ARRIVED
            terminal_snapshot = snapshot
        elif entry.outcome is NavigationTransitionOutcome.STOPPED:
            phase = NavigationPhase.STOPPED
            terminal_snapshot = snapshot
        else:
            raise ValueError("report terminal no-change cannot precede a terminal state")

    if (
        phase is not final_progress.phase
        or accepted_checkpoint_count != final_progress.accepted_checkpoint_count
    ):
        raise ValueError("report trace route history must match its final progress")


def run_navigation_replay(manifest: NavigationReplayManifest) -> NavigationReplayReport:
    """Run a replay without a clock, display, capture backend, or input adapter."""

    progress = start_route(
        manifest.context,
        started_monotonic_s=manifest.started_monotonic_s,
    )
    trace: list[ReplayTraceEntry] = []
    proposals: list[OfflineStepProposal] = []
    mismatches: list[ReplayMismatch] = []
    for event_index, event in enumerate(manifest.events):
        if isinstance(event, ObserveCheckpointEvent):
            transition = observe_checkpoint(
                manifest.context,
                progress,
                event.observation,
                evaluated_monotonic_s=event.evaluated_monotonic_s,
            )
        else:
            transition = prepare_step(
                manifest.context,
                progress,
                evaluated_monotonic_s=event.evaluated_monotonic_s,
            )
        progress = transition.progress
        proposal = transition.step_proposal
        if proposal is not None:
            proposals.append(proposal)
        entry = ReplayTraceEntry(
            event_index=event_index,
            outcome=transition.outcome,
            phase=progress.phase,
            current_checkpoint_id=progress.current_checkpoint_id,
            expected_next_checkpoint_id=progress.expected_next_checkpoint_id,
            failure_reason=progress.failure_reason,
            proposed_step_id=None if proposal is None else proposal.step.step_id,
        )
        trace.append(entry)
        mismatches.extend(_compare_expected(event_index, event.expected, entry))

    if progress.phase is not manifest.expected_final_phase:
        mismatches.append(
            ReplayMismatch(
                event_index=len(manifest.events),
                field="final_phase",
                expected=manifest.expected_final_phase.value,
                actual=progress.phase.value,
            )
        )
    return NavigationReplayReport(
        case_id=manifest.case_id,
        fixture_role=manifest.fixture_role,
        route=manifest.context.plan.identity,
        trace=tuple(trace),
        step_proposals=tuple(proposals),
        final_progress=progress,
        mismatches=tuple(mismatches),
    )


def _compare_expected(
    event_index: int,
    expected: ReplayExpectedState,
    actual: ReplayTraceEntry,
) -> list[ReplayMismatch]:
    pairs: tuple[tuple[str, str | None, str | None], ...] = (
        ("outcome", expected.outcome.value, actual.outcome.value),
        ("phase", expected.phase.value, actual.phase.value),
        ("current_checkpoint_id", expected.current_checkpoint_id, actual.current_checkpoint_id),
        (
            "expected_next_checkpoint_id",
            expected.expected_next_checkpoint_id,
            actual.expected_next_checkpoint_id,
        ),
        ("failure_reason", _enum_value(expected.failure_reason), _enum_value(actual.failure_reason)),
        ("proposed_step_id", expected.proposed_step_id, actual.proposed_step_id),
    )
    return [
        ReplayMismatch(event_index, field, wanted, observed)
        for field, wanted, observed in pairs
        if wanted != observed
    ]


def _enum_value(value: StrEnum | None) -> str | None:
    return None if value is None else value.value


def load_navigation_replay(path: Path) -> NavigationReplayManifest:
    """Load one strict UTF-8 JSON navigation replay manifest."""

    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise NavigationManifestError(f"could not read navigation replay {path}: {exc}") from exc
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise NavigationManifestError("navigation replay must be valid UTF-8") from exc
    try:
        raw: object = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_number,
        )
    except NavigationManifestError:
        raise
    except (ValueError, RecursionError) as exc:
        raise NavigationManifestError(f"invalid navigation replay JSON: {exc}") from exc
    try:
        return _parse_manifest(raw)
    except (TypeError, ValueError) as exc:
        if isinstance(exc, NavigationManifestError):
            raise
        raise NavigationManifestError(f"invalid navigation replay manifest: {exc}") from exc


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _reject_nonstandard_number(value: str) -> object:
    raise NavigationManifestError(f"non-standard JSON number {value!r} is not allowed")


def _parse_manifest(raw: object) -> NavigationReplayManifest:
    root = _mapping(raw, "manifest")
    _exact_keys(
        root,
        {
            "schema_version",
            "fixture_role",
            "case_id",
            "started_monotonic_s",
            "context",
            "events",
            "expected_final_phase",
        },
        "manifest",
    )
    schema_version = _integer(root["schema_version"], "schema_version")
    if schema_version != NAVIGATION_REPLAY_SCHEMA_VERSION:
        raise NavigationManifestError(f"unsupported navigation replay schema {schema_version}")
    fixture_role = _string(root["fixture_role"], "fixture_role")
    if fixture_role != SYNTHETIC_FIXTURE_ROLE:
        raise NavigationManifestError("only explicitly synthetic architecture fixtures are accepted")
    events_raw = _sequence(root["events"], "events")
    return NavigationReplayManifest(
        schema_version=schema_version,
        fixture_role=fixture_role,
        case_id=_string(root["case_id"], "case_id"),
        started_monotonic_s=_number(root["started_monotonic_s"], "started_monotonic_s"),
        context=_parse_context(root["context"]),
        events=tuple(_parse_event(event, index) for index, event in enumerate(events_raw)),
        expected_final_phase=_enum(
            NavigationPhase,
            root["expected_final_phase"],
            "expected_final_phase",
        ),
    )


def _parse_context(raw: object) -> RouteEvaluationContext:
    mapping = _mapping(raw, "context")
    _exact_keys(mapping, {"plan", "expected_source", "policy"}, "context")
    return RouteEvaluationContext(
        plan=_parse_plan(mapping["plan"]),
        expected_source=_parse_source(mapping["expected_source"], "context.expected_source"),
        policy=_parse_policy(mapping["policy"]),
    )


def _parse_plan(raw: object) -> RoutePlan:
    mapping = _mapping(raw, "context.plan")
    _exact_keys(
        mapping,
        {"identity", "origin", "destination", "checkpoints", "steps"},
        "context.plan",
    )
    checkpoints = _sequence(mapping["checkpoints"], "context.plan.checkpoints")
    steps = _sequence(mapping["steps"], "context.plan.steps")
    return RoutePlan(
        identity=_parse_route_identity(mapping["identity"], "context.plan.identity"),
        origin=_parse_endpoint(mapping["origin"], "context.plan.origin"),
        destination=_parse_endpoint(mapping["destination"], "context.plan.destination"),
        checkpoints=tuple(
            _parse_checkpoint(checkpoint, f"context.plan.checkpoints[{index}]")
            for index, checkpoint in enumerate(checkpoints)
        ),
        steps=tuple(
            _parse_step(step, f"context.plan.steps[{index}]")
            for index, step in enumerate(steps)
        ),
    )


def _parse_route_identity(raw: object, path: str) -> RouteIdentity:
    mapping = _mapping(raw, path)
    _exact_keys(mapping, {"route_id", "version", "direction"}, path)
    return RouteIdentity(
        route_id=_string(mapping["route_id"], f"{path}.route_id"),
        version=_string(mapping["version"], f"{path}.version"),
        direction=_enum(RouteDirection, mapping["direction"], f"{path}.direction"),
    )


def _parse_endpoint(raw: object, path: str) -> RouteEndpoint:
    mapping = _mapping(raw, path)
    _exact_keys(mapping, {"location_id", "role"}, path)
    return RouteEndpoint(
        location_id=_string(mapping["location_id"], f"{path}.location_id"),
        role=_enum(RouteEndpointRole, mapping["role"], f"{path}.role"),
    )


def _parse_checkpoint(raw: object, path: str) -> Checkpoint:
    mapping = _mapping(raw, path)
    _exact_keys(mapping, {"checkpoint_id", "role"}, path)
    return Checkpoint(
        checkpoint_id=_string(mapping["checkpoint_id"], f"{path}.checkpoint_id"),
        role=_enum(CheckpointRole, mapping["role"], f"{path}.role"),
    )


def _parse_step(raw: object, path: str) -> RouteStep:
    mapping = _mapping(raw, path)
    _exact_keys(mapping, {"step_id", "from_checkpoint_id", "to_checkpoint_id"}, path)
    return RouteStep(
        step_id=_string(mapping["step_id"], f"{path}.step_id"),
        from_checkpoint_id=_string(mapping["from_checkpoint_id"], f"{path}.from_checkpoint_id"),
        to_checkpoint_id=_string(mapping["to_checkpoint_id"], f"{path}.to_checkpoint_id"),
    )


def _parse_source(raw: object, path: str) -> CheckpointSourceIdentity:
    mapping = _mapping(raw, path)
    _exact_keys(
        mapping,
        {
            "detector_id",
            "detector_version",
            "frame_source_id",
            "capture_session_id",
            "frame_width",
            "frame_height",
        },
        path,
    )
    return CheckpointSourceIdentity(
        detector_id=_string(mapping["detector_id"], f"{path}.detector_id"),
        detector_version=_string(mapping["detector_version"], f"{path}.detector_version"),
        frame_source_id=_string(mapping["frame_source_id"], f"{path}.frame_source_id"),
        capture_session_id=_string(mapping["capture_session_id"], f"{path}.capture_session_id"),
        frame_width=_integer(mapping["frame_width"], f"{path}.frame_width"),
        frame_height=_integer(mapping["frame_height"], f"{path}.frame_height"),
    )


def _parse_policy(raw: object) -> NavigationPolicy:
    path = "context.policy"
    mapping = _mapping(raw, path)
    _exact_keys(mapping, {"max_frame_age_s", "minimum_confidence"}, path)
    return NavigationPolicy(
        max_frame_age_s=_number(mapping["max_frame_age_s"], f"{path}.max_frame_age_s"),
        minimum_confidence=_number(mapping["minimum_confidence"], f"{path}.minimum_confidence"),
    )


def _parse_event(raw: object, index: int) -> ReplayEvent:
    path = f"events[{index}]"
    mapping = _mapping(raw, path)
    kind = _enum(ReplayEventKind, mapping.get("kind"), f"{path}.kind")
    if kind is ReplayEventKind.OBSERVE_CHECKPOINT:
        _exact_keys(mapping, {"kind", "evaluated_monotonic_s", "observation", "expected"}, path)
        return ObserveCheckpointEvent(
            evaluated_monotonic_s=_number(
                mapping["evaluated_monotonic_s"], f"{path}.evaluated_monotonic_s"
            ),
            observation=_parse_observation(mapping["observation"], f"{path}.observation"),
            expected=_parse_expected(mapping["expected"], f"{path}.expected"),
        )
    _exact_keys(mapping, {"kind", "evaluated_monotonic_s", "expected"}, path)
    return PrepareStepEvent(
        evaluated_monotonic_s=_number(
            mapping["evaluated_monotonic_s"], f"{path}.evaluated_monotonic_s"
        ),
        expected=_parse_expected(mapping["expected"], f"{path}.expected"),
    )


def _parse_observation(raw: object, path: str) -> CheckpointObservation:
    mapping = _mapping(raw, path)
    _exact_keys(
        mapping,
        {"route", "provenance", "match", "candidate_checkpoint_ids", "confidence"},
        path,
    )
    candidates = _sequence(mapping["candidate_checkpoint_ids"], f"{path}.candidate_checkpoint_ids")
    return CheckpointObservation(
        route=_parse_route_identity(mapping["route"], f"{path}.route"),
        provenance=_parse_provenance(mapping["provenance"], f"{path}.provenance"),
        match=_enum(CheckpointMatchKind, mapping["match"], f"{path}.match"),
        candidate_checkpoint_ids=tuple(
            _string(candidate, f"{path}.candidate_checkpoint_ids[{index}]")
            for index, candidate in enumerate(candidates)
        ),
        confidence=_number(mapping["confidence"], f"{path}.confidence"),
    )


def _parse_provenance(raw: object, path: str) -> FrameProvenance:
    mapping = _mapping(raw, path)
    _exact_keys(mapping, {"source", "frame"}, path)
    frame_path = f"{path}.frame"
    frame_mapping = _mapping(mapping["frame"], frame_path)
    _exact_keys(
        frame_mapping,
        {"frame_id", "captured_monotonic_s", "width", "height"},
        frame_path,
    )
    return FrameProvenance(
        source=_parse_source(mapping["source"], f"{path}.source"),
        frame=FrameRef(
            frame_id=_integer(frame_mapping["frame_id"], f"{frame_path}.frame_id"),
            captured_monotonic_s=_number(
                frame_mapping["captured_monotonic_s"], f"{frame_path}.captured_monotonic_s"
            ),
            width=_integer(frame_mapping["width"], f"{frame_path}.width"),
            height=_integer(frame_mapping["height"], f"{frame_path}.height"),
        ),
    )


def _parse_expected(raw: object, path: str) -> ReplayExpectedState:
    mapping = _mapping(raw, path)
    _exact_keys(
        mapping,
        {
            "outcome",
            "phase",
            "current_checkpoint_id",
            "expected_next_checkpoint_id",
            "failure_reason",
            "proposed_step_id",
        },
        path,
    )
    return ReplayExpectedState(
        outcome=_enum(NavigationTransitionOutcome, mapping["outcome"], f"{path}.outcome"),
        phase=_enum(NavigationPhase, mapping["phase"], f"{path}.phase"),
        current_checkpoint_id=_optional_string(
            mapping["current_checkpoint_id"], f"{path}.current_checkpoint_id"
        ),
        expected_next_checkpoint_id=_optional_string(
            mapping["expected_next_checkpoint_id"], f"{path}.expected_next_checkpoint_id"
        ),
        failure_reason=_optional_enum(
            NavigationFailureReason,
            mapping["failure_reason"],
            f"{path}.failure_reason",
        ),
        proposed_step_id=_optional_string(
            mapping["proposed_step_id"], f"{path}.proposed_step_id"
        ),
    )


def _mapping(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise NavigationManifestError(f"{path} must be a JSON object")
    return cast(Mapping[str, object], value)


def _sequence(value: object, path: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise NavigationManifestError(f"{path} must be a JSON array")
    return cast(Sequence[object], value)


def _exact_keys(mapping: Mapping[str, object], expected: set[str], path: str) -> None:
    actual = set(mapping)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise NavigationManifestError(f"{path} keys mismatch; missing={missing}, extra={extra}")


def _string(value: object, path: str) -> str:
    try:
        return _require_identifier(value, path)
    except ValueError as exc:
        raise NavigationManifestError(str(exc)) from exc


def _optional_string(value: object, path: str) -> str | None:
    return None if value is None else _string(value, path)


def _integer(value: object, path: str) -> int:
    if not _is_integer(value):
        raise NavigationManifestError(f"{path} must be an integer")
    return cast(int, value)


def _number(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NavigationManifestError(f"{path} must be a finite JSON number")
    try:
        converted = float(value)
    except (OverflowError, ValueError) as exc:
        raise NavigationManifestError(f"{path} must be a finite JSON number") from exc
    if not isfinite(converted):
        raise NavigationManifestError(f"{path} must be a finite JSON number")
    return converted


def _enum[EnumT: StrEnum](enum_type: type[EnumT], value: object, path: str) -> EnumT:
    if not isinstance(value, str):
        raise NavigationManifestError(f"{path} must be a string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise NavigationManifestError(f"{path} has unsupported value {value!r}") from exc


def _optional_enum[EnumT: StrEnum](
    enum_type: type[EnumT], value: object, path: str
) -> EnumT | None:
    return None if value is None else _enum(enum_type, value, path)


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require_identifier(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or not value.isprintable()
    ):
        raise ValueError(f"{field_name} must be a non-empty, trimmed, printable string")
    return value


def _require_nonnegative_time(value: object, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be finite and non-negative")
    try:
        finite = isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        finite = False
    if not finite or value < 0:
        raise ValueError(f"{field_name} must be finite and non-negative")
