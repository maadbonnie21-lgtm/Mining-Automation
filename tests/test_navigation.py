from __future__ import annotations

import copy
import inspect
import json
from dataclasses import replace
from pathlib import Path

import pytest

from mining_automation.contracts import FrameRef
from mining_automation.navigation.cli import main as navigation_cli_main
from mining_automation.navigation.contracts import (
    ArrivalEvidence,
    Checkpoint,
    CheckpointMatchKind,
    CheckpointObservation,
    CheckpointRole,
    CheckpointSourceIdentity,
    FrameProvenance,
    NavigationFailureReason,
    NavigationPhase,
    NavigationPolicy,
    NavigationTransition,
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
from mining_automation.navigation.machine import observe_checkpoint, prepare_step, start_route
from mining_automation.navigation.replay import (
    NavigationManifestError,
    ReplayMismatch,
    load_navigation_replay,
    run_navigation_replay,
)

FIXTURE_DIRECTORY = Path(__file__).parent / "fixtures" / "navigation"


def _route_identity(direction: RouteDirection = RouteDirection.MINE_TO_BANK) -> RouteIdentity:
    route_id = (
        "synthetic-mine-to-bank"
        if direction is RouteDirection.MINE_TO_BANK
        else "synthetic-bank-to-mine"
    )
    return RouteIdentity(route_id, "synthetic-v1", direction)


def _plan(direction: RouteDirection = RouteDirection.MINE_TO_BANK) -> RoutePlan:
    if direction is RouteDirection.MINE_TO_BANK:
        origin = RouteEndpoint("synthetic-mine", RouteEndpointRole.MINE)
        destination = RouteEndpoint("synthetic-bank", RouteEndpointRole.BANK)
        prefix = "m2b"
    else:
        origin = RouteEndpoint("synthetic-bank", RouteEndpointRole.BANK)
        destination = RouteEndpoint("synthetic-mine", RouteEndpointRole.MINE)
        prefix = "b2m"
    checkpoints = (
        Checkpoint(f"synthetic-{prefix}-departure", CheckpointRole.DEPARTURE),
        Checkpoint(f"synthetic-{prefix}-transit", CheckpointRole.TRANSIT),
        Checkpoint(f"synthetic-{prefix}-arrival", CheckpointRole.ARRIVAL),
    )
    return RoutePlan(
        identity=_route_identity(direction),
        origin=origin,
        destination=destination,
        checkpoints=checkpoints,
        steps=(
            RouteStep("synthetic-step-1", checkpoints[0].checkpoint_id, checkpoints[1].checkpoint_id),
            RouteStep("synthetic-step-2", checkpoints[1].checkpoint_id, checkpoints[2].checkpoint_id),
        ),
    )


def _source(session_id: str = "synthetic-session") -> CheckpointSourceIdentity:
    return CheckpointSourceIdentity(
        detector_id="synthetic-detector",
        detector_version="synthetic-v1",
        frame_source_id="synthetic-frame-source",
        capture_session_id=session_id,
        frame_width=64,
        frame_height=48,
    )


def _context(direction: RouteDirection = RouteDirection.MINE_TO_BANK) -> RouteEvaluationContext:
    return RouteEvaluationContext(
        plan=_plan(direction),
        expected_source=_source(),
        policy=NavigationPolicy(max_frame_age_s=0.5, minimum_confidence=0.9),
    )


def _observation(
    context: RouteEvaluationContext,
    checkpoint_id: str | None,
    *,
    frame_id: int = 10,
    captured_monotonic_s: float = 100.0,
    route: RouteIdentity | None = None,
    source: CheckpointSourceIdentity | None = None,
    match: CheckpointMatchKind = CheckpointMatchKind.MATCHED,
    candidates: tuple[str, ...] | None = None,
    confidence: float = 0.99,
) -> CheckpointObservation:
    if candidates is None:
        candidates = () if checkpoint_id is None else (checkpoint_id,)
    return CheckpointObservation(
        route=context.plan.identity if route is None else route,
        provenance=FrameProvenance(
            context.expected_source if source is None else source,
            FrameRef(frame_id, captured_monotonic_s, 64, 48),
        ),
        match=match,
        candidate_checkpoint_ids=candidates,
        confidence=confidence,
    )


def _prepared_after_departure(
    context: RouteEvaluationContext,
) -> tuple[RouteProgress, CheckpointObservation]:
    progress = start_route(context, started_monotonic_s=99.9)
    departure = _observation(context, context.plan.checkpoints[0].checkpoint_id)
    accepted = observe_checkpoint(
        context,
        progress,
        departure,
        evaluated_monotonic_s=100.1,
    )
    prepared = prepare_step(context, accepted.progress, evaluated_monotonic_s=100.2)
    assert prepared.step_proposal is not None
    return prepared.progress, departure


def _complete_route(context: RouteEvaluationContext) -> RouteProgress:
    progress = start_route(context, started_monotonic_s=99.9)
    captures = (100.0, 100.3, 100.6)
    evaluations = (100.1, 100.4, 100.7)
    preparations = (100.2, 100.5)
    for index, checkpoint in enumerate(context.plan.checkpoints):
        transition = observe_checkpoint(
            context,
            progress,
            _observation(
                context,
                checkpoint.checkpoint_id,
                frame_id=10 + index,
                captured_monotonic_s=captures[index],
            ),
            evaluated_monotonic_s=evaluations[index],
        )
        progress = transition.progress
        if index < len(context.plan.steps):
            transition = prepare_step(
                context,
                progress,
                evaluated_monotonic_s=preparations[index],
            )
            assert transition.step_proposal is not None
            assert transition.step_proposal.live_input_enabled is False
            progress = transition.progress
    return progress


def _write_json(tmp_path: Path, value: object) -> Path:
    path = tmp_path / "navigation.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _fixture_json(name: str = "synthetic_mine_to_bank.json") -> dict[str, object]:
    return json.loads((FIXTURE_DIRECTORY / name).read_text(encoding="utf-8"))


def test_direction_plans_are_independent_linear_contracts() -> None:
    mine_to_bank = _plan(RouteDirection.MINE_TO_BANK)
    bank_to_mine = _plan(RouteDirection.BANK_TO_MINE)

    assert mine_to_bank.identity != bank_to_mine.identity
    assert mine_to_bank.origin.role is RouteEndpointRole.MINE
    assert mine_to_bank.destination.role is RouteEndpointRole.BANK
    assert bank_to_mine.origin.role is RouteEndpointRole.BANK
    assert bank_to_mine.destination.role is RouteEndpointRole.MINE
    assert not hasattr(mine_to_bank, "reverse")
    assert all(
        step.from_checkpoint_id == mine_to_bank.checkpoints[index].checkpoint_id
        and step.to_checkpoint_id == mine_to_bank.checkpoints[index + 1].checkpoint_id
        for index, step in enumerate(mine_to_bank.steps)
    )


@pytest.mark.parametrize(
    "mutation, message",
    [
        (
            lambda plan: replace(
                plan,
                origin=RouteEndpoint("synthetic-bank", RouteEndpointRole.BANK),
                destination=RouteEndpoint("synthetic-mine", RouteEndpointRole.MINE),
            ),
            "direction",
        ),
        (lambda plan: replace(plan, checkpoints=plan.checkpoints[:1]), "departure and arrival"),
        (
            lambda plan: replace(plan, checkpoints=(plan.checkpoints[0], plan.checkpoints[0])),
            "unique",
        ),
        (
            lambda plan: replace(
                plan,
                checkpoints=(
                    plan.checkpoints[0],
                    replace(plan.checkpoints[1], role=CheckpointRole.ARRIVAL),
                    plan.checkpoints[2],
                ),
            ),
            "departure, transit",
        ),
        (lambda plan: replace(plan, steps=plan.steps[:1]), "exactly one step"),
        (
            lambda plan: replace(
                plan,
                steps=(
                    replace(plan.steps[0], to_checkpoint_id=plan.checkpoints[2].checkpoint_id),
                    plan.steps[1],
                ),
            ),
            "adjacent",
        ),
        (
            lambda plan: replace(
                plan,
                steps=(plan.steps[0], replace(plan.steps[1], step_id=plan.steps[0].step_id)),
            ),
            "unique",
        ),
    ],
)
def test_route_plan_rejects_malformed_linear_manifests(mutation: object, message: str) -> None:
    mutate = mutation
    assert callable(mutate)
    with pytest.raises(ValueError, match=message):
        mutate(_plan())


@pytest.mark.parametrize("value", ["", " route", "route ", 1, None])
def test_route_identity_rejects_noncanonical_identifiers(value: object) -> None:
    with pytest.raises(ValueError):
        RouteIdentity(value, "v1", RouteDirection.MINE_TO_BANK)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "match,candidates",
    [
        (CheckpointMatchKind.UNKNOWN, ("checkpoint",)),
        (CheckpointMatchKind.MATCHED, ()),
        (CheckpointMatchKind.MATCHED, ("one", "two")),
        (CheckpointMatchKind.AMBIGUOUS, ("one",)),
        (CheckpointMatchKind.AMBIGUOUS, ("same", "same")),
    ],
)
def test_observation_match_cardinality_is_typed(
    match: CheckpointMatchKind,
    candidates: tuple[str, ...],
) -> None:
    context = _context()
    with pytest.raises(ValueError):
        _observation(context, None, match=match, candidates=candidates)


@pytest.mark.parametrize("confidence", [-0.1, 1.1, float("nan"), float("inf"), True])
def test_observation_rejects_invalid_confidence(confidence: float) -> None:
    with pytest.raises(ValueError):
        _observation(_context(), "checkpoint", confidence=confidence)


def test_frame_provenance_requires_positive_frame_identity_and_exact_geometry() -> None:
    with pytest.raises(ValueError, match="positive captured frame"):
        FrameProvenance(_source(), FrameRef(0, 1.0, 64, 48))
    with pytest.raises(ValueError, match="geometry"):
        FrameProvenance(_source(), FrameRef(1, 1.0, 63, 48))


def test_public_nested_contracts_fail_with_value_error() -> None:
    context = _context()
    arrival = context.plan.checkpoints[-1]
    with pytest.raises(ValueError):
        ArrivalEvidence(context, arrival, object())  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        OfflineStepProposal(context, context.plan.steps[0], object(), 1.0)  # type: ignore[arg-type]


def test_start_route_has_no_implicit_location_evidence() -> None:
    context = _context()
    progress = start_route(context, started_monotonic_s=100.0)

    assert progress.phase is NavigationPhase.AWAITING_CHECKPOINT
    assert progress.current_checkpoint_id is None
    assert progress.expected_next_checkpoint_id == context.plan.checkpoints[0].checkpoint_id
    assert progress.active_checkpoint_evidence is None
    assert progress.last_accepted_provenance is None
    assert progress.accepted_checkpoint_count == 0
    assert progress.evidence_boundary_monotonic_s == 100.0

    with pytest.raises(ValueError, match="finite"):
        start_route(context, started_monotonic_s=10**400)


@pytest.mark.parametrize("direction", list(RouteDirection))
def test_happy_path_requires_every_checkpoint_and_consumes_each_step_evidence(
    direction: RouteDirection,
) -> None:
    progress = _complete_route(_context(direction))

    assert progress.phase is NavigationPhase.ARRIVED
    assert progress.expected_next_checkpoint_id is None
    assert progress.arrival_evidence is not None
    assert progress.current_checkpoint_id == progress.arrival_evidence.checkpoint.checkpoint_id
    assert progress.arrival_evidence.supported_mining_view_proven is False
    assert progress.arrival_evidence.bank_interface_open_proven is False


def test_prepared_step_is_data_only_and_clears_current_location_evidence() -> None:
    context = _context()
    progress, departure = _prepared_after_departure(context)

    accepted = observe_checkpoint(
        context,
        start_route(context, started_monotonic_s=99.9),
        departure,
        evaluated_monotonic_s=100.1,
    )
    prepared = prepare_step(context, accepted.progress, evaluated_monotonic_s=100.2)
    proposal = prepared.step_proposal
    assert proposal is not None
    assert proposal.checkpoint_evidence is departure
    assert proposal.prepared_monotonic_s == 100.2
    assert proposal.live_input_enabled is False
    assert progress.current_checkpoint_id is None
    assert progress.active_checkpoint_evidence is None
    assert progress.expected_next_checkpoint_id == context.plan.checkpoints[1].checkpoint_id
    assert progress.accepted_checkpoint_count == 1
    assert progress.evidence_boundary_monotonic_s == 100.2
    parameter_names = set(inspect.signature(OfflineStepProposal).parameters)
    assert parameter_names == {"context", "step", "checkpoint_evidence", "prepared_monotonic_s"}


@pytest.mark.parametrize(
    "route_change,expected_reason",
    [
        ({"route_id": "other-route"}, NavigationFailureReason.ROUTE_ID_MISMATCH),
        ({"version": "synthetic-v2"}, NavigationFailureReason.ROUTE_VERSION_MISMATCH),
        (
            {"direction": RouteDirection.BANK_TO_MINE},
            NavigationFailureReason.DIRECTION_MISMATCH,
        ),
    ],
)
def test_wrong_route_identity_stops(
    route_change: dict[str, object],
    expected_reason: NavigationFailureReason,
) -> None:
    context = _context()
    wrong_route = replace(context.plan.identity, **route_change)
    observation = _observation(
        context,
        context.plan.checkpoints[0].checkpoint_id,
        route=wrong_route,
    )
    transition = observe_checkpoint(
        context,
        start_route(context, started_monotonic_s=100.0),
        observation,
        evaluated_monotonic_s=100.1,
    )

    assert transition.progress.failure_reason is expected_reason
    assert transition.progress.current_checkpoint_id is None


def test_provenance_mismatch_stops_before_checkpoint_interpretation() -> None:
    context = _context()
    observation = _observation(
        context,
        context.plan.checkpoints[1].checkpoint_id,
        source=_source("different-session"),
    )
    transition = observe_checkpoint(
        context,
        start_route(context, started_monotonic_s=100.0),
        observation,
        evaluated_monotonic_s=100.1,
    )
    assert transition.progress.failure_reason is NavigationFailureReason.PROVENANCE_MISMATCH


@pytest.mark.parametrize(
    "observation,evaluated,expected_reason",
    [
        ({"captured_monotonic_s": 100.2}, 100.1, NavigationFailureReason.INVALID_FRAME_TIME),
        ({"captured_monotonic_s": 99.9}, 100.0, NavigationFailureReason.EVIDENCE_NOT_AFTER_BOUNDARY),
        ({"captured_monotonic_s": 100.1}, 100.7, NavigationFailureReason.STALE_FRAME),
    ],
)
def test_initial_checkpoint_requires_fresh_post_start_frame(
    observation: dict[str, float],
    evaluated: float,
    expected_reason: NavigationFailureReason,
) -> None:
    context = _context()
    candidate = _observation(
        context,
        context.plan.checkpoints[0].checkpoint_id,
        captured_monotonic_s=observation["captured_monotonic_s"],
    )
    transition = observe_checkpoint(
        context,
        start_route(context, started_monotonic_s=100.0),
        candidate,
        evaluated_monotonic_s=evaluated,
    )
    assert transition.progress.failure_reason is expected_reason


@pytest.mark.parametrize(
    "frame_id,captured,expected_reason",
    [
        (10, 100.3, NavigationFailureReason.REPEATED_FRAME),
        (9, 100.3, NavigationFailureReason.OUT_OF_ORDER_FRAME),
        (11, 99.9, NavigationFailureReason.OUT_OF_ORDER_FRAME),
        (11, 100.1, NavigationFailureReason.EVIDENCE_NOT_AFTER_BOUNDARY),
        (11, 100.3, NavigationFailureReason.STALE_FRAME),
    ],
)
def test_next_checkpoint_rejects_repeated_out_of_order_queued_and_stale_frames(
    frame_id: int,
    captured: float,
    expected_reason: NavigationFailureReason,
) -> None:
    context = _context()
    progress, _ = _prepared_after_departure(context)
    observation = _observation(
        context,
        context.plan.checkpoints[1].checkpoint_id,
        frame_id=frame_id,
        captured_monotonic_s=captured,
    )
    evaluated = 101.0 if expected_reason is NavigationFailureReason.STALE_FRAME else 100.4
    transition = observe_checkpoint(
        context,
        progress,
        observation,
        evaluated_monotonic_s=evaluated,
    )
    assert transition.progress.failure_reason is expected_reason


def test_higher_frame_id_at_exact_step_boundary_is_not_causal_proof() -> None:
    context = _context()
    progress, _ = _prepared_after_departure(context)
    transition = observe_checkpoint(
        context,
        progress,
        _observation(
            context,
            context.plan.checkpoints[1].checkpoint_id,
            frame_id=11,
            captured_monotonic_s=100.2,
        ),
        evaluated_monotonic_s=100.2,
    )
    assert transition.progress.failure_reason is NavigationFailureReason.EVIDENCE_NOT_AFTER_BOUNDARY


def test_higher_frame_id_with_same_capture_timestamp_cannot_cross_boundary() -> None:
    context = _context()
    progress = start_route(context, started_monotonic_s=99.9)
    accepted = observe_checkpoint(
        context,
        progress,
        _observation(context, context.plan.checkpoints[0].checkpoint_id),
        evaluated_monotonic_s=100.0,
    )
    prepared = prepare_step(context, accepted.progress, evaluated_monotonic_s=100.0)
    transition = observe_checkpoint(
        context,
        prepared.progress,
        _observation(
            context,
            context.plan.checkpoints[1].checkpoint_id,
            frame_id=11,
            captured_monotonic_s=100.0,
        ),
        evaluated_monotonic_s=100.0,
    )
    assert transition.progress.failure_reason is NavigationFailureReason.EVIDENCE_NOT_AFTER_BOUNDARY


@pytest.mark.parametrize(
    "match,candidates,confidence,expected_reason",
    [
        (CheckpointMatchKind.UNKNOWN, (), 0.0, NavigationFailureReason.UNKNOWN_CHECKPOINT),
        (
            CheckpointMatchKind.AMBIGUOUS,
            ("synthetic-m2b-departure", "synthetic-m2b-transit"),
            0.99,
            NavigationFailureReason.AMBIGUOUS_CHECKPOINT,
        ),
        (CheckpointMatchKind.MATCHED, None, 0.89, NavigationFailureReason.LOW_CONFIDENCE),
    ],
)
def test_unknown_ambiguous_and_low_confidence_never_continue(
    match: CheckpointMatchKind,
    candidates: tuple[str, ...] | None,
    confidence: float,
    expected_reason: NavigationFailureReason,
) -> None:
    context = _context()
    checkpoint_id = None if match is CheckpointMatchKind.UNKNOWN else context.plan.checkpoints[0].checkpoint_id
    observation = _observation(
        context,
        checkpoint_id,
        match=match,
        candidates=candidates,
        confidence=confidence,
    )
    transition = observe_checkpoint(
        context,
        start_route(context, started_monotonic_s=99.9),
        observation,
        evaluated_monotonic_s=100.1,
    )
    assert transition.progress.phase is NavigationPhase.STOPPED
    assert transition.progress.failure_reason is expected_reason


@pytest.mark.parametrize(
    "checkpoint_id,expected_reason",
    [
        ("synthetic-m2b-transit", NavigationFailureReason.SKIPPED_CHECKPOINT),
        ("synthetic-foreign", NavigationFailureReason.UNEXPECTED_CHECKPOINT),
    ],
)
def test_skipped_and_foreign_checkpoints_stop(
    checkpoint_id: str,
    expected_reason: NavigationFailureReason,
) -> None:
    context = _context()
    transition = observe_checkpoint(
        context,
        start_route(context, started_monotonic_s=99.9),
        _observation(context, checkpoint_id),
        evaluated_monotonic_s=100.1,
    )
    assert transition.progress.failure_reason is expected_reason


def test_prior_checkpoint_after_step_is_out_of_order() -> None:
    context = _context()
    progress, _ = _prepared_after_departure(context)
    transition = observe_checkpoint(
        context,
        progress,
        _observation(
            context,
            context.plan.checkpoints[0].checkpoint_id,
            frame_id=11,
            captured_monotonic_s=100.3,
        ),
        evaluated_monotonic_s=100.4,
    )
    assert transition.progress.failure_reason is NavigationFailureReason.OUT_OF_ORDER_CHECKPOINT


def test_checkpoint_cannot_advance_before_step_evidence_is_consumed() -> None:
    context = _context()
    started = start_route(context, started_monotonic_s=99.9)
    accepted = observe_checkpoint(
        context,
        started,
        _observation(context, context.plan.checkpoints[0].checkpoint_id),
        evaluated_monotonic_s=100.1,
    )
    transition = observe_checkpoint(
        context,
        accepted.progress,
        _observation(
            context,
            context.plan.checkpoints[1].checkpoint_id,
            frame_id=11,
            captured_monotonic_s=100.2,
        ),
        evaluated_monotonic_s=100.2,
    )
    assert transition.progress.failure_reason is NavigationFailureReason.STEP_EVIDENCE_NOT_CONSUMED


def test_step_preparation_requires_current_fresh_evidence_and_is_single_use() -> None:
    context = _context()
    started = start_route(context, started_monotonic_s=99.9)
    premature = prepare_step(context, started, evaluated_monotonic_s=100.0)
    assert premature.progress.failure_reason is NavigationFailureReason.STEP_NOT_READY

    accepted = observe_checkpoint(
        context,
        started,
        _observation(context, context.plan.checkpoints[0].checkpoint_id),
        evaluated_monotonic_s=100.1,
    )
    stale = prepare_step(context, accepted.progress, evaluated_monotonic_s=100.6)
    assert stale.progress.failure_reason is NavigationFailureReason.STALE_FRAME

    prepared = prepare_step(context, accepted.progress, evaluated_monotonic_s=100.2)
    repeated = prepare_step(context, prepared.progress, evaluated_monotonic_s=100.2)
    assert repeated.progress.failure_reason is NavigationFailureReason.STEP_NOT_READY


def test_stop_and_arrival_are_absorbing() -> None:
    context = _context()
    stopped = observe_checkpoint(
        context,
        start_route(context, started_monotonic_s=99.9),
        _observation(context, "synthetic-foreign"),
        evaluated_monotonic_s=100.1,
    ).progress
    after_stop = observe_checkpoint(
        context,
        stopped,
        _observation(context, context.plan.checkpoints[0].checkpoint_id),
        evaluated_monotonic_s=100.2,
    )
    assert after_stop.outcome is NavigationTransitionOutcome.TERMINAL_NO_CHANGE
    assert after_stop.progress is stopped

    arrived = _complete_route(context)
    after_arrival = prepare_step(context, arrived, evaluated_monotonic_s=101.0)
    assert after_arrival.outcome is NavigationTransitionOutcome.TERMINAL_NO_CHANGE
    assert after_arrival.progress is arrived


def test_progress_rejects_mid_run_context_replacement() -> None:
    context = _context()
    progress, _ = _prepared_after_departure(context)
    changed_context = replace(context, expected_source=_source("replacement-session"))
    transition = observe_checkpoint(
        changed_context,
        progress,
        _observation(
            changed_context,
            changed_context.plan.checkpoints[1].checkpoint_id,
            frame_id=11,
            captured_monotonic_s=100.3,
        ),
        evaluated_monotonic_s=100.4,
    )
    assert transition.progress.failure_reason is NavigationFailureReason.CONTEXT_MISMATCH


def test_evaluation_time_cannot_move_backwards() -> None:
    context = _context()
    started = start_route(context, started_monotonic_s=99.9)
    accepted = observe_checkpoint(
        context,
        started,
        _observation(context, context.plan.checkpoints[0].checkpoint_id),
        evaluated_monotonic_s=100.5,
    )
    transition = prepare_step(context, accepted.progress, evaluated_monotonic_s=100.1)
    assert transition.progress.failure_reason is NavigationFailureReason.OUT_OF_ORDER_EVALUATION
    assert transition.progress.last_transition_monotonic_s == 100.5


def test_zero_confidence_policy_cannot_be_configured() -> None:
    with pytest.raises(ValueError, match="positive"):
        NavigationPolicy(max_frame_age_s=0.5, minimum_confidence=0.0)


def test_route_is_incomplete_without_explicit_terminal_observation() -> None:
    context = _context()
    progress, _ = _prepared_after_departure(context)
    transit = observe_checkpoint(
        context,
        progress,
        _observation(
            context,
            context.plan.checkpoints[1].checkpoint_id,
            frame_id=11,
            captured_monotonic_s=100.3,
        ),
        evaluated_monotonic_s=100.4,
    )
    prepared = prepare_step(context, transit.progress, evaluated_monotonic_s=100.5)
    assert prepared.progress.phase is NavigationPhase.AWAITING_CHECKPOINT
    assert prepared.progress.expected_next_checkpoint_id == context.plan.checkpoints[-1].checkpoint_id
    assert prepared.progress.arrival_evidence is None


def test_invalid_public_progress_and_transition_construction_is_rejected() -> None:
    context = _context()
    with pytest.raises(ValueError):
        RouteProgress(
            context=context,
            phase=NavigationPhase.READY_FOR_STEP,
            current_checkpoint_id="synthetic-m2b-departure",
            expected_next_checkpoint_id="synthetic-m2b-transit",
            accepted_checkpoint_count=1,
            evidence_boundary_monotonic_s=100.0,
            last_transition_monotonic_s=100.0,
            active_checkpoint_evidence=object(),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="NavigationStop"):
        RouteProgress(
            context=context,
            phase=NavigationPhase.STOPPED,
            current_checkpoint_id=None,
            expected_next_checkpoint_id=None,
            accepted_checkpoint_count=0,
            evidence_boundary_monotonic_s=100.0,
            last_transition_monotonic_s=100.0,
            stop=object(),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="terminal no-change"):
        NavigationTransition(
            NavigationTransitionOutcome.TERMINAL_NO_CHANGE,
            start_route(context, started_monotonic_s=100.0),
        )
    with pytest.raises(ValueError, match="expected checkpoint"):
        RouteProgress(
            context=context,
            phase=NavigationPhase.AWAITING_CHECKPOINT,
            current_checkpoint_id=None,
            expected_next_checkpoint_id=context.plan.checkpoints[-1].checkpoint_id,
            accepted_checkpoint_count=0,
            evidence_boundary_monotonic_s=100.0,
            last_transition_monotonic_s=100.0,
        )


def test_prepared_transition_binds_step_target_and_time_to_resulting_progress() -> None:
    context = _context()
    started = start_route(context, started_monotonic_s=99.9)
    accepted = observe_checkpoint(
        context,
        started,
        _observation(context, context.plan.checkpoints[0].checkpoint_id),
        evaluated_monotonic_s=100.1,
    )
    prepared = prepare_step(context, accepted.progress, evaluated_monotonic_s=100.2)
    assert prepared.step_proposal is not None
    inconsistent = replace(prepared.step_proposal, prepared_monotonic_s=100.3)
    with pytest.raises(ValueError, match="route history"):
        NavigationTransition(
            NavigationTransitionOutcome.STEP_PREPARED,
            prepared.progress,
            inconsistent,
        )


def test_terminal_progress_still_rejects_context_replacement() -> None:
    context = _context()
    arrived = _complete_route(context)
    changed_context = replace(context, policy=NavigationPolicy(0.4, 0.9))
    transition = prepare_step(changed_context, arrived, evaluated_monotonic_s=101.0)
    assert transition.outcome is NavigationTransitionOutcome.STOPPED
    assert transition.progress.failure_reason is NavigationFailureReason.CONTEXT_MISMATCH


def test_direct_progress_history_must_use_the_bound_source() -> None:
    context = _context()
    with pytest.raises(ValueError, match="frame history"):
        RouteProgress(
            context=context,
            phase=NavigationPhase.AWAITING_CHECKPOINT,
            current_checkpoint_id=None,
            expected_next_checkpoint_id=context.plan.checkpoints[1].checkpoint_id,
            accepted_checkpoint_count=1,
            evidence_boundary_monotonic_s=100.2,
            last_transition_monotonic_s=100.2,
            last_accepted_provenance=FrameProvenance(
                _source("foreign-session"),
                FrameRef(10, 100.0, 64, 48),
            ),
        )


def test_direct_ready_proposal_and_arrival_require_confident_fresh_evidence() -> None:
    context = _context()
    departure = context.plan.checkpoints[0]
    low_departure = _observation(
        context,
        departure.checkpoint_id,
        confidence=0.1,
    )
    with pytest.raises(ValueError, match="active evidence"):
        RouteProgress(
            context=context,
            phase=NavigationPhase.READY_FOR_STEP,
            current_checkpoint_id=departure.checkpoint_id,
            expected_next_checkpoint_id=context.plan.checkpoints[1].checkpoint_id,
            accepted_checkpoint_count=1,
            evidence_boundary_monotonic_s=100.0,
            last_transition_monotonic_s=100.1,
            last_accepted_provenance=low_departure.provenance,
            active_checkpoint_evidence=low_departure,
        )
    with pytest.raises(ValueError, match="evaluation context"):
        OfflineStepProposal(context, context.plan.steps[0], low_departure, 100.1)

    confident_departure = _observation(context, departure.checkpoint_id)
    with pytest.raises(ValueError, match="fresh"):
        OfflineStepProposal(context, context.plan.steps[0], confident_departure, 100.6)

    arrival_checkpoint = context.plan.checkpoints[-1]
    low_arrival = _observation(
        context,
        arrival_checkpoint.checkpoint_id,
        confidence=0.1,
    )
    with pytest.raises(ValueError, match="evaluation context"):
        ArrivalEvidence(context, arrival_checkpoint, low_arrival)

    arrival_observation = _observation(context, arrival_checkpoint.checkpoint_id)
    arrival = ArrivalEvidence(context, arrival_checkpoint, arrival_observation)
    with pytest.raises(ValueError, match="explicit terminal"):
        RouteProgress(
            context=context,
            phase=NavigationPhase.ARRIVED,
            current_checkpoint_id=arrival_checkpoint.checkpoint_id,
            expected_next_checkpoint_id=None,
            accepted_checkpoint_count=len(context.plan.checkpoints),
            evidence_boundary_monotonic_s=100.0,
            last_transition_monotonic_s=100.6,
            last_accepted_provenance=arrival_observation.provenance,
            arrival_evidence=arrival,
        )


@pytest.mark.parametrize(
    "fixture_name",
    ["synthetic_mine_to_bank.json", "synthetic_bank_to_mine.json"],
)
def test_committed_synthetic_replays_pass_deterministically(fixture_name: str) -> None:
    manifest = load_navigation_replay(FIXTURE_DIRECTORY / fixture_name)

    first = run_navigation_replay(manifest)
    second = run_navigation_replay(manifest)

    assert first.passed
    assert first == second
    assert first.to_json() == second.to_json()
    assert first.final_progress.phase is NavigationPhase.ARRIVED
    assert len(first.step_proposals) == 2
    assert all(proposal.live_input_enabled is False for proposal in first.step_proposals)
    assert first.fixture_role == "synthetic_navigation_architecture_test_only"
    assert first.live_navigation_enabled is False
    assert '"live_navigation_enabled": false' in first.to_json()

    with pytest.raises(ValueError, match="synthetic fixture role"):
        replace(first, fixture_role="production_route_evidence")
    with pytest.raises(ValueError, match="route"):
        replace(
            first,
            route=RouteIdentity("different-synthetic-route", "synthetic-v1", first.route.direction),
        )
    with pytest.raises(ValueError, match="outcome"):
        replace(first.trace[0], outcome=NavigationTransitionOutcome.ARRIVAL_CONFIRMED)

    forged_last = replace(
        first.trace[-1],
        current_checkpoint_id="synthetic-forged-arrival",
    )
    with pytest.raises(ValueError, match="final trace"):
        replace(first, trace=first.trace[:-1] + (forged_last,))

    forged_first = replace(
        first.trace[0],
        current_checkpoint_id=first.trace[0].expected_next_checkpoint_id,
        expected_next_checkpoint_id=first.trace[-1].current_checkpoint_id,
    )
    with pytest.raises(ValueError, match="route sequence"):
        replace(first, trace=(forged_first,) + first.trace[1:])

    with pytest.raises(ValueError, match="non-negative integer"):
        ReplayMismatch(-1, "field", None, None)
    with pytest.raises(ValueError, match="printable"):
        ReplayMismatch(0, "field\nspoofed-output", None, None)
    with pytest.raises(ValueError, match="string or None"):
        ReplayMismatch(0, "field", object(), None)  # type: ignore[arg-type]


def test_replay_report_rejects_proposals_from_another_provenance_context(tmp_path: Path) -> None:
    original_manifest = load_navigation_replay(FIXTURE_DIRECTORY / "synthetic_mine_to_bank.json")
    original_report = run_navigation_replay(original_manifest)
    other_data = _fixture_json()
    other_data["context"]["expected_source"]["capture_session_id"] = "other-session"  # type: ignore[index]
    for event in other_data["events"]:  # type: ignore[union-attr]
        if "observation" in event:
            event["observation"]["provenance"]["source"]["capture_session_id"] = (  # type: ignore[index]
                "other-session"
            )
    other_manifest = load_navigation_replay(_write_json(tmp_path, other_data))
    other_report = run_navigation_replay(other_manifest)

    assert original_report.route == other_report.route
    with pytest.raises(ValueError, match="exact evaluation context"):
        replace(original_report, step_proposals=other_report.step_proposals)


def test_replay_cli_emits_stable_machine_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    report_path = tmp_path / "report.json"
    exit_code = navigation_cli_main(
        [
            "--manifest",
            str(FIXTURE_DIRECTORY / "synthetic_mine_to_bank.json"),
            "--json-report",
            str(report_path),
        ]
    )
    output = capsys.readouterr()
    assert exit_code == 0
    assert "navigation replay PASS" in output.out
    assert "fixture role: synthetic_navigation_architecture_test_only" in output.out
    assert "live navigation: disabled" in output.out
    assert output.err == ""
    assert json.loads(report_path.read_text(encoding="utf-8"))["passed"] is True


@pytest.mark.parametrize(
    "case",
    [
        "extra_root_key",
        "wrong_schema",
        "non_synthetic_role",
        "boolean_frame_id",
        "reversed_endpoints",
        "reversed_steps",
        "duplicate_checkpoint",
        "missing_events",
        "event_before_start",
        "decreasing_event_time",
        "nonterminal_final_phase",
        "unexpected_event_key",
    ],
)
def test_manifest_rejects_malformed_or_scope_claiming_data(tmp_path: Path, case: str) -> None:
    data = _fixture_json()
    if case == "extra_root_key":
        data["real_route_claim"] = True
    elif case == "wrong_schema":
        data["schema_version"] = 2
    elif case == "non_synthetic_role":
        data["fixture_role"] = "production_varrock_route"
    elif case == "boolean_frame_id":
        data["events"][0]["observation"]["provenance"]["frame"]["frame_id"] = True  # type: ignore[index]
    elif case == "reversed_endpoints":
        plan = data["context"]["plan"]  # type: ignore[index]
        plan["origin"], plan["destination"] = plan["destination"], plan["origin"]
    elif case == "reversed_steps":
        data["context"]["plan"]["steps"].reverse()  # type: ignore[index,union-attr]
    elif case == "duplicate_checkpoint":
        checkpoints = data["context"]["plan"]["checkpoints"]  # type: ignore[index]
        checkpoints[1]["checkpoint_id"] = checkpoints[0]["checkpoint_id"]
    elif case == "missing_events":
        del data["events"]
    elif case == "event_before_start":
        data["events"][0]["evaluated_monotonic_s"] = 9.8  # type: ignore[index]
    elif case == "decreasing_event_time":
        data["events"][1]["evaluated_monotonic_s"] = 10.0  # type: ignore[index]
    elif case == "nonterminal_final_phase":
        data["expected_final_phase"] = "awaiting_checkpoint"
    else:
        data["events"][0]["unexpected"] = "value"  # type: ignore[index]

    with pytest.raises(NavigationManifestError):
        load_navigation_replay(_write_json(tmp_path, data))


@pytest.mark.parametrize(
    "raw",
    [
        '{"schema_version": 1, "schema_version": 1}',
        '{"schema_version": 1,',
    ],
)
def test_manifest_rejects_duplicate_keys_and_malformed_json(tmp_path: Path, raw: str) -> None:
    path = tmp_path / "navigation.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(NavigationManifestError):
        load_navigation_replay(path)


def test_manifest_rejects_nonstandard_numbers(tmp_path: Path) -> None:
    raw = (FIXTURE_DIRECTORY / "synthetic_mine_to_bank.json").read_text(encoding="utf-8")
    path = tmp_path / "navigation.json"
    path.write_text(raw.replace("0.99", "NaN", 1), encoding="utf-8")
    with pytest.raises(NavigationManifestError, match="non-standard"):
        load_navigation_replay(path)


def test_navigation_identifiers_reject_control_characters(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="printable"):
        RouteIdentity("synthetic-route\nspoofed-output", "synthetic-v1", RouteDirection.MINE_TO_BANK)

    data = _fixture_json()
    data["case_id"] = (
        "synthetic-case\nlive navigation: enabled\nfixture role: production_route_evidence"
    )
    with pytest.raises(NavigationManifestError, match="printable"):
        load_navigation_replay(_write_json(tmp_path, data))


def test_manifest_rejects_unrepresentable_large_number(tmp_path: Path) -> None:
    raw = (FIXTURE_DIRECTORY / "synthetic_mine_to_bank.json").read_text(encoding="utf-8")
    path = tmp_path / "navigation.json"
    path.write_text(
        raw.replace('"started_monotonic_s": 9.9', '"started_monotonic_s": ' + "1" + "0" * 400),
        encoding="utf-8",
    )
    with pytest.raises(NavigationManifestError, match="finite JSON number"):
        load_navigation_replay(path)


def test_manifest_normalizes_json_integer_conversion_failures(tmp_path: Path) -> None:
    path = tmp_path / "navigation.json"
    path.write_text(
        '{"schema_version": ' + "1" * 5_000 + "}",
        encoding="utf-8",
    )

    with pytest.raises(NavigationManifestError, match="invalid navigation replay JSON"):
        load_navigation_replay(path)


def test_manifest_normalizes_json_recursion_failures(tmp_path: Path) -> None:
    path = tmp_path / "navigation.json"
    nesting_depth = 20_000
    path.write_text(
        "[" * nesting_depth + "0" + "]" * nesting_depth,
        encoding="utf-8",
    )

    with pytest.raises(NavigationManifestError):
        load_navigation_replay(path)


@pytest.mark.parametrize(
    "case,expected_reason",
    [
        ("wrong_direction", NavigationFailureReason.DIRECTION_MISMATCH),
        ("wrong_initial_version", NavigationFailureReason.ROUTE_VERSION_MISMATCH),
        ("provenance_mismatch", NavigationFailureReason.PROVENANCE_MISMATCH),
        ("unknown", NavigationFailureReason.UNKNOWN_CHECKPOINT),
        ("ambiguous", NavigationFailureReason.AMBIGUOUS_CHECKPOINT),
        ("skipped", NavigationFailureReason.SKIPPED_CHECKPOINT),
        ("foreign", NavigationFailureReason.UNEXPECTED_CHECKPOINT),
        ("stale", NavigationFailureReason.STALE_FRAME),
    ],
)
def test_replay_harness_accepts_expected_fail_closed_scenarios(
    tmp_path: Path,
    case: str,
    expected_reason: NavigationFailureReason,
) -> None:
    data = _fixture_json()
    first_event = copy.deepcopy(data["events"][0])  # type: ignore[index]
    observation = first_event["observation"]
    if case == "wrong_direction":
        observation["route"]["direction"] = "bank_to_mine"
    elif case == "wrong_initial_version":
        observation["route"]["version"] = "synthetic-v2"
    elif case == "provenance_mismatch":
        observation["provenance"]["source"]["capture_session_id"] = "other-session"
    elif case == "unknown":
        observation["match"] = "unknown"
        observation["candidate_checkpoint_ids"] = []
    elif case == "ambiguous":
        observation["match"] = "ambiguous"
        observation["candidate_checkpoint_ids"] = [
            "synthetic-mine-departure",
            "synthetic-m2b-transit",
        ]
    elif case == "skipped":
        observation["candidate_checkpoint_ids"] = ["synthetic-m2b-transit"]
    elif case == "foreign":
        observation["candidate_checkpoint_ids"] = ["synthetic-foreign"]
    else:
        first_event["evaluated_monotonic_s"] = 10.6
    first_event["expected"] = {
        "outcome": "stopped",
        "phase": "stopped",
        "current_checkpoint_id": None,
        "expected_next_checkpoint_id": None,
        "failure_reason": expected_reason.value,
        "proposed_step_id": None,
    }
    data["events"] = [first_event]
    data["expected_final_phase"] = "stopped"

    report = run_navigation_replay(load_navigation_replay(_write_json(tmp_path, data)))
    assert report.passed
    assert report.final_progress.failure_reason is expected_reason


@pytest.mark.parametrize(
    "case,expected_reason",
    [
        ("repeated", NavigationFailureReason.REPEATED_FRAME),
        ("out_of_order", NavigationFailureReason.OUT_OF_ORDER_FRAME),
        ("pre_step_queue", NavigationFailureReason.EVIDENCE_NOT_AFTER_BOUNDARY),
        ("prior_checkpoint", NavigationFailureReason.OUT_OF_ORDER_CHECKPOINT),
        ("route_version_change", NavigationFailureReason.ROUTE_VERSION_MISMATCH),
    ],
)
def test_replay_harness_preserves_adversarial_frame_identity(
    tmp_path: Path,
    case: str,
    expected_reason: NavigationFailureReason,
) -> None:
    data = _fixture_json()
    events = copy.deepcopy(data["events"][:3])  # type: ignore[index]
    first_frame = events[0]["observation"]["provenance"]["frame"]
    candidate_frame = events[2]["observation"]["provenance"]["frame"]
    if case == "repeated":
        candidate_frame["frame_id"] = first_frame["frame_id"]
        candidate_frame["captured_monotonic_s"] = first_frame["captured_monotonic_s"]
    elif case == "out_of_order":
        first_frame["frame_id"] = 5
        candidate_frame["frame_id"] = 4
    elif case == "prior_checkpoint":
        events[2]["observation"]["candidate_checkpoint_ids"] = [
            "synthetic-mine-departure"
        ]
    elif case == "route_version_change":
        events[2]["observation"]["route"]["version"] = "synthetic-v2"
    else:
        candidate_frame["captured_monotonic_s"] = 10.1
    events[2]["expected"] = {
        "outcome": "stopped",
        "phase": "stopped",
        "current_checkpoint_id": None,
        "expected_next_checkpoint_id": None,
        "failure_reason": expected_reason.value,
        "proposed_step_id": None,
    }
    data["events"] = events
    data["expected_final_phase"] = "stopped"

    report = run_navigation_replay(load_navigation_replay(_write_json(tmp_path, data)))
    assert report.passed
    assert report.final_progress.failure_reason is expected_reason


def test_replay_reports_incomplete_route_and_expectation_mismatch(tmp_path: Path) -> None:
    data = _fixture_json()
    data["events"] = data["events"][:-1]  # type: ignore[index]
    report = run_navigation_replay(load_navigation_replay(_write_json(tmp_path, data)))
    assert not report.passed
    assert report.final_progress.phase is NavigationPhase.AWAITING_CHECKPOINT
    assert any(mismatch.field == "final_phase" for mismatch in report.mismatches)

    data = _fixture_json()
    data["events"][0]["expected"]["current_checkpoint_id"] = "wrong"  # type: ignore[index]
    report = run_navigation_replay(load_navigation_replay(_write_json(tmp_path, data)))
    assert not report.passed
    assert report.mismatches[0].field == "current_checkpoint_id"


def test_navigation_package_has_no_controller_worldstate_or_input_dependency() -> None:
    package = Path(__file__).parents[1] / "src" / "mining_automation" / "navigation"
    combined = "\n".join(path.read_text(encoding="utf-8") for path in sorted(package.glob("*.py")))
    for forbidden in (
        "MiningController",
        "WorldState",
        "pyautogui",
        "pynput",
        "win32api",
        "SendInput",
    ):
        assert forbidden not in combined
