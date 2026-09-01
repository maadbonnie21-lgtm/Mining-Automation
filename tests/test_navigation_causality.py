from __future__ import annotations

import copy
import pickle
from dataclasses import replace
from threading import Event, Lock, Thread

import pytest

import mining_automation.navigation.offline_route_session as offline_route_session_module
from mining_automation.capture import PixelFormat
from mining_automation.contracts import FrameRef
from mining_automation.navigation.contracts import (
    AttemptEvidenceRole,
    Checkpoint,
    CheckpointDetection,
    CheckpointDetectorIdentity,
    CheckpointEvidence,
    CheckpointEvidenceRole,
    CheckpointMatchKind,
    CheckpointObservation,
    CheckpointProfile,
    CheckpointRole,
    CheckpointSourceIdentity,
    CompletedStepAttempt,
    FrameProvenance,
    NavigationFailureReason,
    NavigationPhase,
    NavigationPolicy,
    NavigationStop,
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
    Sha256Digest,
    StepAttemptIdentity,
    StepAttemptSourceIdentity,
    SyntheticStepAttemptReceipt,
)
from mining_automation.navigation.machine import (
    observe_checkpoint,
    prepare_step,
    record_step_attempt_receipt,
    start_route,
    stop_route,
)
from mining_automation.navigation.offline_route_session import (
    OfflineRouteSession,
    OfflineRouteSessionPhase,
    OfflineRouteSessionResult,
    OfflineRouteSessionSequencer,
    OfflineRouteSessionStopReason,
)


def _context() -> RouteEvaluationContext:
    route = RouteIdentity(
        route_id="synthetic-causal-mine-to-bank",
        version="synthetic-v1",
        direction=RouteDirection.MINE_TO_BANK,
    )
    checkpoints = (
        Checkpoint("synthetic-departure", CheckpointRole.DEPARTURE),
        Checkpoint("synthetic-transit", CheckpointRole.TRANSIT),
        Checkpoint("synthetic-arrival", CheckpointRole.ARRIVAL),
    )
    plan = RoutePlan(
        identity=route,
        origin=RouteEndpoint("synthetic-mine", RouteEndpointRole.MINE),
        destination=RouteEndpoint("synthetic-bank", RouteEndpointRole.BANK),
        checkpoints=checkpoints,
        steps=(
            RouteStep(
                "synthetic-step-1", checkpoints[0].checkpoint_id, checkpoints[1].checkpoint_id
            ),
            RouteStep(
                "synthetic-step-2", checkpoints[1].checkpoint_id, checkpoints[2].checkpoint_id
            ),
        ),
    )
    profile = CheckpointProfile(
        profile_id="synthetic-causal-profile",
        version="synthetic-v1",
        evidence_role=CheckpointEvidenceRole.SYNTHETIC_ARCHITECTURE_TEST_ONLY,
        frame_width=2,
        frame_height=1,
        pixel_format=PixelFormat.GRAY8,
        checkpoint_ids=(
            *(checkpoint.checkpoint_id for checkpoint in checkpoints),
            "synthetic-foreign-checkpoint",
        ),
    )
    return RouteEvaluationContext(
        plan=plan,
        expected_source=CheckpointSourceIdentity(
            detector=CheckpointDetectorIdentity("synthetic-checkpoint-detector", "synthetic-v1"),
            profile=profile,
            frame_source_id="synthetic-frame-source",
            capture_session_id="synthetic-capture-session",
        ),
        expected_attempt_source=StepAttemptSourceIdentity(
            source_id="synthetic-attempt-source",
            version="synthetic-v1",
            session_id="synthetic-attempt-session",
            evidence_role=AttemptEvidenceRole.SYNTHETIC_ARCHITECTURE_TEST_ONLY,
        ),
        policy=NavigationPolicy(
            max_frame_age_s=0.5,
            minimum_confidence=0.9,
            max_attempt_receipt_age_s=0.5,
        ),
    )


def _observation(
    context: RouteEvaluationContext,
    checkpoint_id: str,
    *,
    frame_id: int,
    captured_monotonic_s: float,
) -> CheckpointObservation:
    payload = bytes((frame_id % 256, (frame_id + 1) % 256))
    return CheckpointObservation(
        route=context.plan.identity,
        evidence=CheckpointEvidence(
            provenance=FrameProvenance(
                source=context.expected_source,
                frame=FrameRef(
                    frame_id=frame_id,
                    captured_monotonic_s=captured_monotonic_s,
                    width=2,
                    height=1,
                ),
                pixel_format=PixelFormat.GRAY8,
                frame_payload_sha256=Sha256Digest.from_bytes(payload),
            ),
            detection=CheckpointDetection(
                match=CheckpointMatchKind.MATCHED,
                candidate_checkpoint_ids=(checkpoint_id,),
                confidence=0.99,
            ),
        ),
    )


def _receipt(
    proposal: OfflineStepProposal,
    *,
    identity: StepAttemptIdentity | None = None,
    source: StepAttemptSourceIdentity | None = None,
    prepared_monotonic_s: float | None = None,
    post_attempt_monotonic_s: float | None = None,
) -> SyntheticStepAttemptReceipt:
    prepared = (
        proposal.prepared_monotonic_s if prepared_monotonic_s is None else prepared_monotonic_s
    )
    return SyntheticStepAttemptReceipt(
        identity=proposal.attempt_identity if identity is None else identity,
        source=proposal.context.expected_attempt_source if source is None else source,
        prepared_monotonic_s=prepared,
        post_attempt_monotonic_s=(
            prepared + 0.1 if post_attempt_monotonic_s is None else post_attempt_monotonic_s
        ),
    )


def _ready_at_departure(
    context: RouteEvaluationContext,
) -> tuple[RouteProgress, CheckpointObservation]:
    started = start_route(context, started_monotonic_s=9.9)
    departure = _observation(
        context,
        context.plan.checkpoints[0].checkpoint_id,
        frame_id=1,
        captured_monotonic_s=10.0,
    )
    accepted = observe_checkpoint(
        context,
        started,
        departure,
        evaluated_monotonic_s=10.05,
    )
    assert accepted.outcome is NavigationTransitionOutcome.CHECKPOINT_ACCEPTED
    return accepted.progress, departure


def _pending_first_attempt(
    context: RouteEvaluationContext,
    *,
    attempt_id: str = "synthetic-attempt-1",
) -> NavigationTransition:
    ready, _ = _ready_at_departure(context)
    transition = prepare_step(
        context,
        ready,
        attempt_id=attempt_id,
        evaluated_monotonic_s=10.1,
    )
    assert transition.outcome is NavigationTransitionOutcome.STEP_PREPARED
    assert transition.step_proposal is not None
    return transition


def _record_first_attempt(
    context: RouteEvaluationContext,
) -> NavigationTransition:
    pending = _pending_first_attempt(context)
    assert pending.step_proposal is not None
    transition = record_step_attempt_receipt(
        context,
        pending.progress,
        _receipt(pending.step_proposal),
        evaluated_monotonic_s=10.21,
    )
    assert transition.outcome is NavigationTransitionOutcome.STEP_ATTEMPT_RECORDED
    return transition


def _ready_at_transit(context: RouteEvaluationContext) -> RouteProgress:
    recorded = _record_first_attempt(context)
    transit = observe_checkpoint(
        context,
        recorded.progress,
        _observation(
            context,
            context.plan.checkpoints[1].checkpoint_id,
            frame_id=2,
            captured_monotonic_s=10.3,
        ),
        evaluated_monotonic_s=10.35,
    )
    assert transit.outcome is NavigationTransitionOutcome.CHECKPOINT_ACCEPTED
    return transit.progress


def _complete_route(context: RouteEvaluationContext) -> RouteProgress:
    ready_transit = _ready_at_transit(context)
    prepared_second = prepare_step(
        context,
        ready_transit,
        attempt_id="synthetic-attempt-2",
        evaluated_monotonic_s=10.4,
    )
    assert prepared_second.step_proposal is not None
    recorded_second = record_step_attempt_receipt(
        context,
        prepared_second.progress,
        _receipt(prepared_second.step_proposal),
        evaluated_monotonic_s=10.51,
    )
    arrival = observe_checkpoint(
        context,
        recorded_second.progress,
        _observation(
            context,
            context.plan.checkpoints[2].checkpoint_id,
            frame_id=3,
            captured_monotonic_s=10.6,
        ),
        evaluated_monotonic_s=10.65,
    )
    assert arrival.outcome is NavigationTransitionOutcome.ARRIVAL_CONFIRMED
    return arrival.progress


def _assert_stopped(
    transition: NavigationTransition,
    reason: NavigationFailureReason,
) -> None:
    assert transition.outcome is NavigationTransitionOutcome.STOPPED
    assert transition.progress.phase is NavigationPhase.STOPPED
    assert transition.progress.failure_reason is reason


def test_full_route_requires_one_source_receipt_per_step_and_retains_causal_history() -> None:
    context = _context()
    progress = _complete_route(context)

    assert progress.phase is NavigationPhase.ARRIVED
    assert progress.accepted_checkpoint_count == len(context.plan.checkpoints)
    assert len(progress.completed_attempts) == len(context.plan.steps) == 2
    assert progress.attempt_history == tuple(
        completed.proposal.attempt_identity for completed in progress.completed_attempts
    )
    assert tuple(
        completed.proposal.checkpoint_evidence.matched_checkpoint_id
        for completed in progress.completed_attempts
    ) == tuple(step.from_checkpoint_id for step in context.plan.steps)
    assert tuple(
        completed.recorded_monotonic_s for completed in progress.completed_attempts
    ) == pytest.approx((10.21, 10.51))

    for completed in progress.completed_attempts:
        assert completed.proposal.context == context
        assert completed.proposal.context is not context
        assert completed.receipt.identity == completed.proposal.attempt_identity
        assert completed.receipt.source == context.expected_attempt_source
        assert completed.receipt.prepared_monotonic_s == completed.proposal.prepared_monotonic_s
        assert completed.receipt.post_attempt_monotonic_s > completed.receipt.prepared_monotonic_s
        assert completed.proposal.live_input_enabled is False
        assert completed.receipt.authoritative is False
        assert completed.receipt.movement_success_proven is False
        assert completed.receipt.live_input_enabled is False


def test_missing_receipt_stops_instead_of_advancing_to_checkpoint() -> None:
    context = _context()
    pending = _pending_first_attempt(context)

    stopped = record_step_attempt_receipt(
        context,
        pending.progress,
        None,
        evaluated_monotonic_s=10.2,
    )

    _assert_stopped(stopped, NavigationFailureReason.ATTEMPT_RECEIPT_REQUIRED)
    assert stopped.progress.accepted_checkpoint_count == 1
    assert stopped.progress.completed_attempts == ()


def test_checkpoint_observation_cannot_bypass_pending_receipt() -> None:
    context = _context()
    pending = _pending_first_attempt(context)

    stopped = observe_checkpoint(
        context,
        pending.progress,
        _observation(
            context,
            context.plan.checkpoints[1].checkpoint_id,
            frame_id=2,
            captured_monotonic_s=10.2,
        ),
        evaluated_monotonic_s=10.25,
    )

    _assert_stopped(stopped, NavigationFailureReason.ATTEMPT_RECEIPT_REQUIRED)


def test_receipt_before_step_preparation_is_not_expected() -> None:
    context = _context()
    ready, _ = _ready_at_departure(context)
    identity = StepAttemptIdentity(
        context.plan.identity,
        context.plan.steps[0].step_id,
        "synthetic-unprepared-attempt",
    )
    receipt = SyntheticStepAttemptReceipt(
        identity,
        context.expected_attempt_source,
        prepared_monotonic_s=10.1,
        post_attempt_monotonic_s=10.2,
    )

    stopped = record_step_attempt_receipt(
        context,
        ready,
        receipt,
        evaluated_monotonic_s=10.25,
    )

    _assert_stopped(stopped, NavigationFailureReason.ATTEMPT_RECEIPT_NOT_EXPECTED)


@pytest.mark.parametrize(
    ("route", "reason"),
    [
        (
            RouteIdentity("synthetic-other-route", "synthetic-v1", RouteDirection.MINE_TO_BANK),
            NavigationFailureReason.ROUTE_ID_MISMATCH,
        ),
        (
            RouteIdentity(
                "synthetic-causal-mine-to-bank",
                "synthetic-v2",
                RouteDirection.MINE_TO_BANK,
            ),
            NavigationFailureReason.ROUTE_VERSION_MISMATCH,
        ),
        (
            RouteIdentity(
                "synthetic-causal-mine-to-bank",
                "synthetic-v1",
                RouteDirection.BANK_TO_MINE,
            ),
            NavigationFailureReason.DIRECTION_MISMATCH,
        ),
    ],
)
def test_receipt_route_identity_mismatch_stops(
    route: RouteIdentity,
    reason: NavigationFailureReason,
) -> None:
    context = _context()
    pending = _pending_first_attempt(context)
    assert pending.step_proposal is not None
    expected = pending.step_proposal.attempt_identity
    wrong_identity = StepAttemptIdentity(route, expected.step_id, expected.attempt_id)

    stopped = record_step_attempt_receipt(
        context,
        pending.progress,
        _receipt(pending.step_proposal, identity=wrong_identity),
        evaluated_monotonic_s=10.21,
    )

    _assert_stopped(stopped, reason)


def test_receipt_for_wrong_step_stops() -> None:
    context = _context()
    pending = _pending_first_attempt(context)
    assert pending.step_proposal is not None
    expected = pending.step_proposal.attempt_identity
    wrong_identity = StepAttemptIdentity(
        expected.route,
        context.plan.steps[1].step_id,
        expected.attempt_id,
    )

    stopped = record_step_attempt_receipt(
        context,
        pending.progress,
        _receipt(pending.step_proposal, identity=wrong_identity),
        evaluated_monotonic_s=10.21,
    )

    _assert_stopped(stopped, NavigationFailureReason.ATTEMPT_STEP_MISMATCH)


def test_receipt_for_wrong_attempt_id_stops() -> None:
    context = _context()
    pending = _pending_first_attempt(context)
    assert pending.step_proposal is not None
    expected = pending.step_proposal.attempt_identity
    wrong_identity = StepAttemptIdentity(
        expected.route, expected.step_id, "synthetic-other-attempt"
    )

    stopped = record_step_attempt_receipt(
        context,
        pending.progress,
        _receipt(pending.step_proposal, identity=wrong_identity),
        evaluated_monotonic_s=10.21,
    )

    _assert_stopped(stopped, NavigationFailureReason.ATTEMPT_ID_MISMATCH)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_id", "synthetic-other-source"),
        ("version", "synthetic-v2"),
        ("session_id", "synthetic-other-session"),
    ],
)
def test_receipt_source_identity_mismatch_stops(field: str, value: str) -> None:
    context = _context()
    pending = _pending_first_attempt(context)
    assert pending.step_proposal is not None
    if field == "source_id":
        wrong_source = replace(context.expected_attempt_source, source_id=value)
    elif field == "version":
        wrong_source = replace(context.expected_attempt_source, version=value)
    else:
        assert field == "session_id"
        wrong_source = replace(context.expected_attempt_source, session_id=value)

    stopped = record_step_attempt_receipt(
        context,
        pending.progress,
        _receipt(pending.step_proposal, source=wrong_source),
        evaluated_monotonic_s=10.21,
    )

    _assert_stopped(stopped, NavigationFailureReason.ATTEMPT_RECEIPT_SOURCE_MISMATCH)


def test_receipt_preparation_boundary_must_match_pending_proposal() -> None:
    context = _context()
    pending = _pending_first_attempt(context)
    assert pending.step_proposal is not None

    stopped = record_step_attempt_receipt(
        context,
        pending.progress,
        _receipt(
            pending.step_proposal,
            prepared_monotonic_s=10.11,
            post_attempt_monotonic_s=10.2,
        ),
        evaluated_monotonic_s=10.21,
    )

    _assert_stopped(stopped, NavigationFailureReason.ATTEMPT_PREPARATION_MISMATCH)


def test_future_receipt_boundary_stops() -> None:
    context = _context()
    pending = _pending_first_attempt(context)
    assert pending.step_proposal is not None

    stopped = record_step_attempt_receipt(
        context,
        pending.progress,
        _receipt(pending.step_proposal, post_attempt_monotonic_s=10.3),
        evaluated_monotonic_s=10.25,
    )

    _assert_stopped(stopped, NavigationFailureReason.INVALID_ATTEMPT_TIME)


def test_delayed_receipt_stops_as_stale() -> None:
    context = _context()
    pending = _pending_first_attempt(context)
    assert pending.step_proposal is not None

    stopped = record_step_attempt_receipt(
        context,
        pending.progress,
        _receipt(pending.step_proposal, post_attempt_monotonic_s=10.2),
        evaluated_monotonic_s=10.71,
    )

    _assert_stopped(stopped, NavigationFailureReason.STALE_ATTEMPT_RECEIPT)


def test_duplicate_receipt_identity_stops_even_when_timestamp_changes() -> None:
    context = _context()
    recorded = _record_first_attempt(context)
    assert recorded.attempt_receipt is not None
    first_receipt = recorded.attempt_receipt
    changed_timestamp = SyntheticStepAttemptReceipt(
        first_receipt.identity,
        first_receipt.source,
        first_receipt.prepared_monotonic_s,
        first_receipt.post_attempt_monotonic_s + 0.01,
    )

    stopped = record_step_attempt_receipt(
        context,
        recorded.progress,
        changed_timestamp,
        evaluated_monotonic_s=10.22,
    )

    _assert_stopped(stopped, NavigationFailureReason.DUPLICATE_ATTEMPT_RECEIPT)
    assert stopped.progress.completed_attempts == recorded.progress.completed_attempts


def test_duplicate_attempt_id_on_later_step_stops() -> None:
    context = _context()
    ready_transit = _ready_at_transit(context)

    stopped = prepare_step(
        context,
        ready_transit,
        attempt_id="synthetic-attempt-1",
        evaluated_monotonic_s=10.4,
    )

    _assert_stopped(stopped, NavigationFailureReason.DUPLICATE_ATTEMPT_ID)
    assert len(stopped.progress.completed_attempts) == 1


def test_checkpoint_frame_captured_before_receipt_boundary_cannot_advance() -> None:
    context = _context()
    recorded = _record_first_attempt(context)
    assert recorded.attempt_receipt is not None

    stopped = observe_checkpoint(
        context,
        recorded.progress,
        _observation(
            context,
            context.plan.checkpoints[1].checkpoint_id,
            frame_id=2,
            captured_monotonic_s=recorded.attempt_receipt.post_attempt_monotonic_s - 0.01,
        ),
        evaluated_monotonic_s=10.25,
    )

    _assert_stopped(stopped, NavigationFailureReason.EVIDENCE_NOT_AFTER_BOUNDARY)


def test_unexpected_checkpoint_after_attempt_stops() -> None:
    context = _context()
    recorded = _record_first_attempt(context)

    stopped = observe_checkpoint(
        context,
        recorded.progress,
        _observation(
            context,
            "synthetic-foreign-checkpoint",
            frame_id=2,
            captured_monotonic_s=10.3,
        ),
        evaluated_monotonic_s=10.35,
    )

    _assert_stopped(stopped, NavigationFailureReason.UNEXPECTED_CHECKPOINT)


def test_interruption_stops_and_is_absorbing_for_all_causal_operations() -> None:
    context = _context()
    pending = _pending_first_attempt(context)
    assert pending.step_proposal is not None
    stopped = record_step_attempt_receipt(
        context,
        pending.progress,
        None,
        evaluated_monotonic_s=10.2,
    )
    _assert_stopped(stopped, NavigationFailureReason.ATTEMPT_RECEIPT_REQUIRED)

    valid_receipt = _receipt(pending.step_proposal)
    followups = (
        record_step_attempt_receipt(
            context,
            stopped.progress,
            valid_receipt,
            evaluated_monotonic_s=10.21,
        ),
        observe_checkpoint(
            context,
            stopped.progress,
            _observation(
                context,
                context.plan.checkpoints[1].checkpoint_id,
                frame_id=2,
                captured_monotonic_s=10.3,
            ),
            evaluated_monotonic_s=10.35,
        ),
        prepare_step(
            context,
            stopped.progress,
            attempt_id="synthetic-after-stop",
            evaluated_monotonic_s=10.4,
        ),
    )
    for followup in followups:
        assert followup.outcome is NavigationTransitionOutcome.TERMINAL_NO_CHANGE
        assert followup.progress == stopped.progress
        assert followup.progress is not stopped.progress
        assert followup.progress.failure_reason is NavigationFailureReason.ATTEMPT_RECEIPT_REQUIRED


def test_completed_attempt_rejects_receipt_for_different_proposal() -> None:
    context = _context()
    pending = _pending_first_attempt(context)
    assert pending.step_proposal is not None
    wrong_identity = replace(
        pending.step_proposal.attempt_identity,
        attempt_id="synthetic-forged-attempt",
    )

    with pytest.raises(ValueError, match="exact proposal and receipt"):
        CompletedStepAttempt(
            pending.step_proposal,
            _receipt(pending.step_proposal, identity=wrong_identity),
            recorded_monotonic_s=10.21,
        )


def test_direct_construction_rejects_missing_completed_attempt_history() -> None:
    context = _context()
    recorded = _record_first_attempt(context)

    with pytest.raises(ValueError, match="awaiting progress"):
        replace(recorded.progress, completed_attempts=())


def test_direct_construction_rejects_unrelated_last_provenance() -> None:
    context = _context()
    recorded = _record_first_attempt(context)
    unrelated = _observation(
        context,
        context.plan.checkpoints[1].checkpoint_id,
        frame_id=99,
        captured_monotonic_s=10.19,
    ).provenance

    with pytest.raises(ValueError, match="awaiting progress"):
        replace(recorded.progress, last_accepted_provenance=unrelated)


def test_direct_construction_rejects_awaiting_receipt_boundary_mismatch() -> None:
    context = _context()
    recorded = _record_first_attempt(context)

    with pytest.raises(ValueError, match="awaiting progress"):
        replace(
            recorded.progress,
            evidence_boundary_monotonic_s=recorded.progress.evidence_boundary_monotonic_s - 0.01,
        )


def test_direct_construction_rejects_ready_boundary_before_latest_receipt() -> None:
    context = _context()
    ready = _ready_at_transit(context)

    with pytest.raises(ValueError, match="ready progress"):
        replace(ready, evidence_boundary_monotonic_s=9.9)


def test_direct_construction_rejects_arrival_boundary_before_latest_receipt() -> None:
    context = _context()
    arrived = _complete_route(context)

    with pytest.raises(ValueError, match="arrived progress"):
        replace(arrived, evidence_boundary_monotonic_s=10.2)


def test_direct_construction_rejects_pending_proposal_with_pre_receipt_frame() -> None:
    context = _context()
    ready = _ready_at_transit(context)
    prepared = prepare_step(
        context,
        ready,
        attempt_id="synthetic-attempt-2",
        evaluated_monotonic_s=10.4,
    )
    assert prepared.step_proposal is not None
    pre_receipt_evidence = _observation(
        context,
        context.plan.checkpoints[1].checkpoint_id,
        frame_id=2,
        captured_monotonic_s=10.19,
    )
    forged_proposal = replace(
        prepared.step_proposal,
        checkpoint_evidence=pre_receipt_evidence,
    )

    with pytest.raises(ValueError, match="pending proposal"):
        replace(
            prepared.progress,
            last_accepted_provenance=pre_receipt_evidence.provenance,
            pending_step_proposal=forged_proposal,
        )


def test_direct_construction_rejects_stopped_state_with_future_attempt_history() -> None:
    context = _context()
    arrived = _complete_route(context)
    started = start_route(context, started_monotonic_s=9.9)

    with pytest.raises(ValueError, match="stopped progress"):
        RouteProgress(
            context=context,
            phase=NavigationPhase.STOPPED,
            current_checkpoint_id=None,
            expected_next_checkpoint_id=None,
            accepted_checkpoint_count=0,
            evidence_boundary_monotonic_s=9.9,
            last_transition_monotonic_s=10.7,
            completed_attempts=arrived.completed_attempts,
            stop=NavigationStop(NavigationFailureReason.PLAN_MISMATCH),
        )
    assert started.completed_attempts == ()


def _offline_session(
    context: RouteEvaluationContext,
    session_id: str = "synthetic-route-session-1",
) -> OfflineRouteSession:
    return OfflineRouteSession(
        session_id=session_id,
        context=context,
        direction=context.plan.identity.direction,
    )


def _fresh_offline_session(
    context: RouteEvaluationContext,
    *,
    route_session_id: str,
    capture_session_id: str,
    attempt_session_id: str,
) -> OfflineRouteSession:
    fresh_context = replace(
        context,
        expected_source=replace(
            context.expected_source,
            capture_session_id=capture_session_id,
        ),
        expected_attempt_source=replace(
            context.expected_attempt_source,
            session_id=attempt_session_id,
        ),
    )
    return _offline_session(fresh_context, route_session_id)


def _offline_after_first_receipt() -> tuple[
    OfflineRouteSession,
    OfflineRouteSessionSequencer,
]:
    context = _context()
    session = _offline_session(context)
    sequencer = OfflineRouteSessionSequencer.begin(session, started_monotonic_s=9.9)
    sequencer.observe(
        session,
        _observation(
            context,
            context.plan.checkpoints[0].checkpoint_id,
            frame_id=1,
            captured_monotonic_s=10.0,
        ),
        evaluated_monotonic_s=10.05,
    )
    prepared = sequencer.prepare_step(
        session,
        attempt_id="synthetic-attempt-1",
        evaluated_monotonic_s=10.1,
    )
    assert prepared.navigation_transition is not None
    proposal = prepared.navigation_transition.step_proposal
    assert proposal is not None
    recorded = sequencer.record_attempt(
        session,
        _receipt(proposal),
        evaluated_monotonic_s=10.21,
    )
    assert recorded.progress.phase is OfflineRouteSessionPhase.ACTIVE
    return session, sequencer


def _complete_offline_route(
    session: OfflineRouteSession,
    sequencer: OfflineRouteSessionSequencer,
) -> None:
    sequencer.observe(
        session,
        _observation(
            session.context,
            session.context.plan.checkpoints[1].checkpoint_id,
            frame_id=2,
            captured_monotonic_s=10.3,
        ),
        evaluated_monotonic_s=10.35,
    )
    prepared = sequencer.prepare_step(
        session,
        attempt_id="synthetic-attempt-2",
        evaluated_monotonic_s=10.4,
    )
    assert prepared.navigation_transition is not None
    proposal = prepared.navigation_transition.step_proposal
    assert proposal is not None
    sequencer.record_attempt(
        session,
        _receipt(proposal),
        evaluated_monotonic_s=10.51,
    )
    arrived = sequencer.observe(
        session,
        _observation(
            session.context,
            session.context.plan.checkpoints[2].checkpoint_id,
            frame_id=3,
            captured_monotonic_s=10.6,
        ),
        evaluated_monotonic_s=10.65,
    )
    assert arrived.progress.phase is OfflineRouteSessionPhase.ARRIVED


@pytest.mark.parametrize("captured_monotonic_s", (10.19, 10.2))
def test_offline_session_rehearsal_rejects_pre_or_equal_boundary_frame_delivered_later(
    captured_monotonic_s: float,
) -> None:
    session, sequencer = _offline_after_first_receipt()
    result = sequencer.observe(
        session,
        _observation(
            session.context,
            session.context.plan.checkpoints[1].checkpoint_id,
            frame_id=2,
            captured_monotonic_s=captured_monotonic_s,
        ),
        evaluated_monotonic_s=10.4,
    )

    assert result.progress.phase is OfflineRouteSessionPhase.STOPPED
    assert result.progress.navigation.phase is NavigationPhase.STOPPED
    assert result.progress.stop_reason is OfflineRouteSessionStopReason.NAVIGATION_FAILURE
    assert (
        result.progress.navigation.failure_reason
        is NavigationFailureReason.EVIDENCE_NOT_AFTER_BOUNDARY
    )


def test_offline_session_timeout_reasons_are_phase_specific_and_stop_inner_progress() -> None:
    context = _context()
    session = _offline_session(context)
    awaiting = OfflineRouteSessionSequencer.begin(session, started_monotonic_s=9.9)
    checkpoint_timeout = awaiting.timeout(session, evaluated_monotonic_s=10.0)
    assert (
        checkpoint_timeout.progress.stop_reason is OfflineRouteSessionStopReason.CHECKPOINT_TIMEOUT
    )
    assert (
        checkpoint_timeout.progress.navigation.failure_reason
        is NavigationFailureReason.CHECKPOINT_TIMEOUT
    )

    ready = OfflineRouteSessionSequencer.begin(session, started_monotonic_s=9.9)
    ready.observe(
        session,
        _observation(
            context,
            context.plan.checkpoints[0].checkpoint_id,
            frame_id=1,
            captured_monotonic_s=10.0,
        ),
        evaluated_monotonic_s=10.05,
    )
    step_timeout = ready.timeout(session, evaluated_monotonic_s=10.1)
    assert step_timeout.progress.stop_reason is OfflineRouteSessionStopReason.STEP_TIMEOUT
    assert step_timeout.progress.navigation.failure_reason is NavigationFailureReason.STEP_TIMEOUT

    pending = OfflineRouteSessionSequencer.begin(session, started_monotonic_s=9.9)
    pending.observe(
        session,
        _observation(
            context,
            context.plan.checkpoints[0].checkpoint_id,
            frame_id=1,
            captured_monotonic_s=10.0,
        ),
        evaluated_monotonic_s=10.05,
    )
    pending.prepare_step(
        session,
        attempt_id="synthetic-attempt-1",
        evaluated_monotonic_s=10.1,
    )
    attempt_timeout = pending.timeout(session, evaluated_monotonic_s=10.7)
    assert attempt_timeout.progress.stop_reason is OfflineRouteSessionStopReason.ATTEMPT_TIMEOUT
    assert (
        attempt_timeout.progress.navigation.failure_reason
        is NavigationFailureReason.ATTEMPT_TIMEOUT
    )


def test_offline_duplicate_interruption_and_mid_route_skip_all_stop() -> None:
    session, sequencer = _offline_after_first_receipt()
    proposal = sequencer.progress.navigation.completed_attempts[0].proposal
    duplicate = sequencer.record_attempt(
        session,
        _receipt(proposal),
        evaluated_monotonic_s=10.3,
    )
    assert (
        duplicate.progress.navigation.failure_reason
        is NavigationFailureReason.DUPLICATE_ATTEMPT_RECEIPT
    )

    session, sequencer = _offline_after_first_receipt()
    interrupted = sequencer.interrupt(session, evaluated_monotonic_s=10.3)
    assert interrupted.progress.stop_reason is OfflineRouteSessionStopReason.INTERRUPTED
    assert (
        interrupted.progress.navigation.failure_reason
        is NavigationFailureReason.SESSION_INTERRUPTED
    )
    absorbed = sequencer.observe(
        session,
        _observation(
            session.context,
            session.context.plan.checkpoints[1].checkpoint_id,
            frame_id=2,
            captured_monotonic_s=10.4,
        ),
        evaluated_monotonic_s=10.45,
    ).progress
    assert absorbed == interrupted.progress
    assert absorbed is not interrupted.progress

    session, sequencer = _offline_after_first_receipt()
    skipped = sequencer.observe(
        session,
        _observation(
            session.context,
            session.context.plan.checkpoints[-1].checkpoint_id,
            frame_id=2,
            captured_monotonic_s=10.3,
        ),
        evaluated_monotonic_s=10.35,
    )
    assert skipped.progress.navigation.failure_reason is NavigationFailureReason.SKIPPED_CHECKPOINT


def test_external_stop_converts_inner_progress_to_absorbing_core_stop() -> None:
    session, sequencer = _offline_after_first_receipt()
    interrupted = sequencer.interrupt(session, evaluated_monotonic_s=10.3)
    navigation = interrupted.progress.navigation
    assert navigation.phase is NavigationPhase.STOPPED

    direct_prepare = prepare_step(
        session.context,
        navigation,
        attempt_id="synthetic-after-stop",
        evaluated_monotonic_s=10.4,
    )
    direct_observe = observe_checkpoint(
        session.context,
        navigation,
        _observation(
            session.context,
            session.context.plan.checkpoints[1].checkpoint_id,
            frame_id=2,
            captured_monotonic_s=10.35,
        ),
        evaluated_monotonic_s=10.4,
    )
    direct_receipt = record_step_attempt_receipt(
        session.context,
        navigation,
        navigation.completed_attempts[-1].receipt,
        evaluated_monotonic_s=10.4,
    )
    for transition in (direct_prepare, direct_observe, direct_receipt):
        assert transition.outcome is NavigationTransitionOutcome.TERMINAL_NO_CHANGE
        assert transition.progress == navigation
        assert transition.progress is not navigation


def test_offline_route_recovery_enforces_complete_global_lineage_without_aba() -> None:
    context = _context()
    session = _offline_session(context)
    sequencer = OfflineRouteSessionSequencer.begin(session, started_monotonic_s=0.0)
    stopped = sequencer.interrupt(session, evaluated_monotonic_s=1.0).progress

    with pytest.raises(ValueError, match="globally new route session_id"):
        sequencer.restart(session, started_monotonic_s=2.0)
    same_sources = _offline_session(context, "synthetic-route-session-2")
    with pytest.raises(ValueError, match="globally fresh checkpoint source session"):
        sequencer.restart(same_sources, started_monotonic_s=2.0)

    second = _fresh_offline_session(
        context,
        route_session_id="synthetic-route-session-2",
        capture_session_id="synthetic-capture-session-2",
        attempt_session_id="synthetic-attempt-session-2",
    )
    restarted = sequencer.restart(second, started_monotonic_s=2.0)
    assert restarted.phase is OfflineRouteSessionPhase.ACTIVE
    assert restarted.navigation.accepted_checkpoint_count == 0
    sequencer.interrupt(second, evaluated_monotonic_s=3.0)

    aba_route = _fresh_offline_session(
        context,
        route_session_id=session.session_id,
        capture_session_id="synthetic-capture-session-3",
        attempt_session_id="synthetic-attempt-session-3",
    )
    with pytest.raises(ValueError, match="globally new route session_id"):
        sequencer.restart(aba_route, started_monotonic_s=4.0)

    aba_capture = _fresh_offline_session(
        context,
        route_session_id="synthetic-route-session-3",
        capture_session_id=context.expected_source.capture_session_id,
        attempt_session_id="synthetic-attempt-session-3",
    )
    with pytest.raises(ValueError, match="globally fresh checkpoint source session"):
        sequencer.restart(aba_capture, started_monotonic_s=4.0)

    aba_attempt = _fresh_offline_session(
        context,
        route_session_id="synthetic-route-session-3",
        capture_session_id="synthetic-capture-session-3",
        attempt_session_id=context.expected_attempt_source.session_id,
    )
    with pytest.raises(ValueError, match="globally fresh attempt source session"):
        sequencer.restart(aba_attempt, started_monotonic_s=4.0)

    third = _fresh_offline_session(
        context,
        route_session_id="synthetic-route-session-3",
        capture_session_id="synthetic-capture-session-3",
        attempt_session_id="synthetic-attempt-session-3",
    )
    published_third = sequencer.restart(third, started_monotonic_s=4.0).session
    assert published_third == third
    assert published_third is not third
    assert stopped.phase is OfflineRouteSessionPhase.STOPPED


def test_offline_route_restart_requires_strict_chronology_and_one_current_head() -> None:
    context = _context()
    session = _offline_session(context)
    sequencer = OfflineRouteSessionSequencer.begin(session, started_monotonic_s=0.0)
    stale_snapshot = sequencer.interrupt(session, evaluated_monotonic_s=1.0).progress
    replacement = _fresh_offline_session(
        context,
        route_session_id="synthetic-route-session-2",
        capture_session_id="synthetic-capture-session-2",
        attempt_session_id="synthetic-attempt-session-2",
    )

    with pytest.raises(ValueError, match="strictly after"):
        sequencer.restart(replacement, started_monotonic_s=1.0)
    assert sequencer.progress == stale_snapshot
    assert sequencer.progress is not stale_snapshot
    sequencer.restart(replacement, started_monotonic_s=2.0)
    with pytest.raises(ValueError, match="only a stopped"):
        sequencer.restart(
            _fresh_offline_session(
                context,
                route_session_id="synthetic-route-session-3",
                capture_session_id="synthetic-capture-session-3",
                attempt_session_id="synthetic-attempt-session-3",
            ),
            started_monotonic_s=3.0,
        )
    assert stale_snapshot.phase is OfflineRouteSessionPhase.STOPPED


def test_offline_sequencer_cannot_be_directly_constructed_copied_or_pickled() -> None:
    session = _offline_session(_context())
    with pytest.raises(ValueError, match="begin"):
        OfflineRouteSessionSequencer(session, started_monotonic_s=0.0)
    sequencer = OfflineRouteSessionSequencer.begin(session, started_monotonic_s=0.0)
    with pytest.raises(TypeError, match="cannot be copied"):
        copy.copy(sequencer)
    with pytest.raises(TypeError, match="cannot be copied"):
        copy.deepcopy(sequencer)
    with pytest.raises(TypeError, match="cannot be pickled"):
        pickle.dumps(sequencer)


def test_offline_module_has_no_stateless_start_or_restart_bypass() -> None:
    assert not hasattr(offline_route_session_module, "start_offline_route_session")
    assert not hasattr(offline_route_session_module, "restart_offline_route_session")


@pytest.mark.parametrize("operation", ("interrupt", "timeout"))
def test_offline_events_cannot_be_backdated(operation: str) -> None:
    context = _context()
    session = _offline_session(context)
    sequencer = OfflineRouteSessionSequencer.begin(session, started_monotonic_s=1.0)
    sequencer.observe(
        session,
        _observation(
            context,
            context.plan.checkpoints[0].checkpoint_id,
            frame_id=1,
            captured_monotonic_s=1.1,
        ),
        evaluated_monotonic_s=1.2,
    )
    if operation == "interrupt":
        result = sequencer.interrupt(session, evaluated_monotonic_s=1.1)
    else:
        result = sequencer.timeout(session, evaluated_monotonic_s=1.1)
    assert result.progress.phase is OfflineRouteSessionPhase.STOPPED
    assert (
        result.progress.navigation.failure_reason is NavigationFailureReason.OUT_OF_ORDER_EVALUATION
    )
    assert result.progress.last_event_monotonic_s == pytest.approx(1.2)


def test_offline_session_times_reject_lossy_integer_conversion() -> None:
    context = _context()
    session = _offline_session(context)
    with pytest.raises(ValueError, match="float"):
        OfflineRouteSessionSequencer.begin(session, started_monotonic_s=0)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="float"):
        OfflineRouteSessionSequencer.begin(session, started_monotonic_s=-0.0)

    start = float(2**53) - 2.0
    sequencer = OfflineRouteSessionSequencer.begin(
        session,
        started_monotonic_s=start,
    )
    initial = sequencer.progress
    with pytest.raises(ValueError, match="float"):
        sequencer.observe(
            session,
            _observation(
                context,
                context.plan.checkpoints[0].checkpoint_id,
                frame_id=1,
                captured_monotonic_s=float(2**53),
            ),
            evaluated_monotonic_s=2**53 + 1,  # type: ignore[arg-type]
        )
    assert sequencer.progress == initial
    assert sequencer.progress is not initial

    stopped = sequencer.interrupt(
        session,
        evaluated_monotonic_s=float(2**53) + 2.0,
    ).progress
    replacement = _fresh_offline_session(
        context,
        route_session_id="synthetic-route-session-2",
        capture_session_id="synthetic-capture-session-2",
        attempt_session_id="synthetic-attempt-session-2",
    )
    with pytest.raises(ValueError, match="float"):
        sequencer.restart(
            replacement,
            started_monotonic_s=2**53 + 4,  # type: ignore[arg-type]
        )
    assert sequencer.progress == stopped
    assert sequencer.progress is not stopped


def test_offline_session_times_reject_float_subclass_operator_overrides() -> None:
    class AdversarialFloat(float):
        def __lt__(self, other: object) -> bool:
            del other
            return False

        def __sub__(self, other: object) -> float:
            del other
            return 0.0

    context = _context()
    session = _offline_session(context)
    with pytest.raises(ValueError, match="float"):
        OfflineRouteSessionSequencer.begin(
            session,
            started_monotonic_s=AdversarialFloat(-1.0),
        )

    sequencer = OfflineRouteSessionSequencer.begin(session, started_monotonic_s=0.0)
    initial = sequencer.progress
    with pytest.raises(ValueError, match="float"):
        sequencer.observe(
            session,
            _observation(
                context,
                context.plan.checkpoints[0].checkpoint_id,
                frame_id=1,
                captured_monotonic_s=0.1,
            ),
            evaluated_monotonic_s=AdversarialFloat(100.0),
        )
    assert sequencer.progress == initial
    assert sequencer.progress is not initial


def test_navigation_causality_contracts_reject_primitive_subclass_operators() -> None:
    class AdversarialFloat(float):
        def __lt__(self, other: object) -> bool:
            del other
            return False

        def __le__(self, other: object) -> bool:
            del other
            return False

        def __sub__(self, other: object) -> float:
            del other
            return 0.0

    class AdversarialInt(int):
        def __eq__(self, other: object) -> bool:
            del other
            return False

        def __lt__(self, other: object) -> bool:
            del other
            return False

        __hash__ = int.__hash__

    class AdversarialStr(str):
        def __eq__(self, other: object) -> bool:
            del other
            return True

        def __ne__(self, other: object) -> bool:
            del other
            return False

        __hash__ = str.__hash__

    context = _context()
    with pytest.raises(ValueError, match="route_id"):
        RouteIdentity(
            route_id=AdversarialStr("foreign-route"),
            version="synthetic-v1",
            direction=RouteDirection.MINE_TO_BANK,
        )
    with pytest.raises(ValueError, match="frame time"):
        FrameProvenance(
            source=context.expected_source,
            frame=FrameRef(1, AdversarialFloat(0.0), 2, 1),
            pixel_format=PixelFormat.GRAY8,
            frame_payload_sha256=Sha256Digest.from_bytes(b"ab"),
        )
    with pytest.raises(ValueError, match="positive captured frame id"):
        FrameProvenance(
            source=context.expected_source,
            frame=FrameRef(AdversarialInt(1), 0.1, 2, 1),
            pixel_format=PixelFormat.GRAY8,
            frame_payload_sha256=Sha256Digest.from_bytes(b"ab"),
        )
    with pytest.raises(ValueError, match="confidence"):
        CheckpointDetection(
            CheckpointMatchKind.MATCHED,
            (context.plan.checkpoints[0].checkpoint_id,),
            AdversarialFloat(0.0),
        )
    with pytest.raises(ValueError, match="post-attempt boundary"):
        SyntheticStepAttemptReceipt(
            identity=StepAttemptIdentity(
                context.plan.identity,
                context.plan.steps[0].step_id,
                "synthetic-attempt",
            ),
            source=context.expected_attempt_source,
            prepared_monotonic_s=0.1,
            post_attempt_monotonic_s=AdversarialFloat(0.0),
        )
    with pytest.raises(ValueError, match="session_id"):
        _offline_session(context, AdversarialStr("synthetic-route-session-1"))


def test_pure_reducer_owns_observation_and_receipt_ingress() -> None:
    context = _context()
    started = start_route(context, started_monotonic_s=9.9)
    caller_observation = _observation(
        context,
        context.plan.checkpoints[0].checkpoint_id,
        frame_id=1,
        captured_monotonic_s=10.0,
    )
    accepted = observe_checkpoint(
        context,
        started,
        caller_observation,
        evaluated_monotonic_s=10.05,
    )
    object.__setattr__(caller_observation.evidence.detection, "confidence", 0.0)
    assert accepted.progress.active_checkpoint_evidence is not None
    assert accepted.progress.active_checkpoint_evidence.confidence == 0.99
    assert accepted.progress.active_checkpoint_evidence is not caller_observation

    prepared = prepare_step(
        context,
        accepted.progress,
        attempt_id="synthetic-owned-reducer-attempt",
        evaluated_monotonic_s=10.1,
    )
    assert prepared.step_proposal is not None
    caller_receipt = _receipt(prepared.step_proposal)
    recorded = record_step_attempt_receipt(
        context,
        prepared.progress,
        caller_receipt,
        evaluated_monotonic_s=10.21,
    )
    assert recorded.attempt_receipt is not None
    assert recorded.attempt_receipt is not caller_receipt
    object.__setattr__(caller_receipt, "movement_success_proven", True)
    object.__setattr__(caller_receipt, "live_input_enabled", True)
    object.__setattr__(caller_receipt, "post_attempt_monotonic_s", 0.0)
    assert recorded.attempt_receipt.movement_success_proven is False
    assert recorded.attempt_receipt.live_input_enabled is False
    assert recorded.attempt_receipt.post_attempt_monotonic_s == 10.2

    mutated_before_ingress = _receipt(prepared.step_proposal)
    object.__setattr__(mutated_before_ingress, "authoritative", True)
    with pytest.raises(ValueError, match="authority fields were mutated"):
        record_step_attempt_receipt(
            context,
            prepared.progress,
            mutated_before_ingress,
            evaluated_monotonic_s=10.21,
        )


def test_offline_sequencer_owns_ingress_and_detaches_public_state() -> None:
    caller_context = _context()
    caller_session = _offline_session(caller_context)
    sequencer = OfflineRouteSessionSequencer.begin(
        caller_session,
        started_monotonic_s=0.0,
    )
    published_initial = sequencer.progress

    object.__setattr__(caller_session, "input_authority", True)
    object.__setattr__(caller_context.policy, "max_frame_age_s", 999.0)
    object.__setattr__(published_initial, "phase", OfflineRouteSessionPhase.ARRIVED)
    object.__setattr__(published_initial.navigation, "phase", NavigationPhase.ARRIVED)

    retained = sequencer.progress
    assert retained.phase is OfflineRouteSessionPhase.ACTIVE
    assert retained.navigation.phase is NavigationPhase.AWAITING_CHECKPOINT
    assert retained.session.input_authority is False
    assert retained.session.context.policy.max_frame_age_s == 0.5
    with pytest.raises(ValueError, match="authority fields were mutated"):
        sequencer.timeout(caller_session, evaluated_monotonic_s=0.1)

    valid_context = _context()
    valid_session = _offline_session(valid_context)
    caller_observation = _observation(
        valid_context,
        valid_context.plan.checkpoints[0].checkpoint_id,
        frame_id=1,
        captured_monotonic_s=0.1,
    )
    accepted = sequencer.observe(
        valid_session,
        caller_observation,
        evaluated_monotonic_s=0.2,
    )
    assert accepted.progress.navigation.phase is NavigationPhase.READY_FOR_STEP
    object.__setattr__(caller_observation.evidence.detection, "confidence", 0.0)
    object.__setattr__(accepted.progress, "phase", OfflineRouteSessionPhase.ARRIVED)
    assert accepted.navigation_transition is not None
    object.__setattr__(accepted.navigation_transition.progress, "phase", NavigationPhase.ARRIVED)

    retained = sequencer.progress
    assert retained.phase is OfflineRouteSessionPhase.ACTIVE
    assert retained.navigation.phase is NavigationPhase.READY_FOR_STEP
    assert retained.navigation.active_checkpoint_evidence is not None
    assert retained.navigation.active_checkpoint_evidence.confidence == 0.99

    prepared = sequencer.prepare_step(
        valid_session,
        attempt_id="synthetic-owned-attempt",
        evaluated_monotonic_s=0.3,
    )
    assert prepared.navigation_transition is not None
    proposal = prepared.navigation_transition.step_proposal
    assert proposal is not None
    mutated_receipt = _receipt(proposal)
    object.__setattr__(mutated_receipt, "movement_success_proven", True)
    object.__setattr__(mutated_receipt, "live_input_enabled", True)
    with pytest.raises(ValueError, match="fixed-authority"):
        sequencer.record_attempt(
            valid_session,
            mutated_receipt,
            evaluated_monotonic_s=0.5,
        )
    assert sequencer.progress.navigation.phase is NavigationPhase.AWAITING_ATTEMPT_RECEIPT


def test_offline_public_transitions_commit_one_serialized_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context()
    session = _offline_session(context)
    sequencer = OfflineRouteSessionSequencer.begin(session, started_monotonic_s=0.0)
    original_observe = offline_route_session_module.observe_checkpoint
    first_entered = Event()
    second_attempting = Event()
    second_entered = Event()
    release_first = Event()
    counter_lock = Lock()
    result_lock = Lock()
    active_calls = 0
    maximum_active_calls = 0
    total_calls = 0
    results: dict[str, OfflineRouteSessionResult] = {}
    errors: list[BaseException] = []

    def guarded_observe(
        route_context: RouteEvaluationContext,
        progress: RouteProgress,
        observation: CheckpointObservation,
        *,
        evaluated_monotonic_s: float,
    ) -> NavigationTransition:
        nonlocal active_calls, maximum_active_calls, total_calls
        with counter_lock:
            total_calls += 1
            call_number = total_calls
            active_calls += 1
            maximum_active_calls = max(maximum_active_calls, active_calls)
        if call_number == 1:
            first_entered.set()
            if not release_first.wait(timeout=2.0):
                raise AssertionError("first reducer call was not released")
        else:
            second_entered.set()
        try:
            return original_observe(
                route_context,
                progress,
                observation,
                evaluated_monotonic_s=evaluated_monotonic_s,
            )
        finally:
            with counter_lock:
                active_calls -= 1

    monkeypatch.setattr(
        offline_route_session_module,
        "observe_checkpoint",
        guarded_observe,
    )

    def run_observation(
        label: str,
        frame_id: int,
        captured: float,
        evaluated: float,
    ) -> None:
        try:
            if label == "second":
                second_attempting.set()
            result = sequencer.observe(
                session,
                _observation(
                    context,
                    context.plan.checkpoints[0].checkpoint_id,
                    frame_id=frame_id,
                    captured_monotonic_s=captured,
                ),
                evaluated_monotonic_s=evaluated,
            )
            with result_lock:
                results[label] = result
        except BaseException as exc:  # pragma: no cover - asserted below
            with result_lock:
                errors.append(exc)

    first = Thread(target=run_observation, args=("first", 1, 0.1, 0.2))
    second = Thread(target=run_observation, args=("second", 2, 0.15, 0.3))
    first.start()
    assert first_entered.wait(timeout=1.0)
    second.start()
    assert second_attempting.wait(timeout=1.0)
    assert not second_entered.wait(timeout=0.1)
    release_first.set()
    first.join(timeout=2.0)
    second.join(timeout=2.0)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert total_calls == 2
    assert maximum_active_calls == 1
    assert results["first"].progress.navigation.phase is NavigationPhase.READY_FOR_STEP
    assert results["second"].progress.navigation.phase is NavigationPhase.STOPPED
    final = sequencer.progress
    assert final.phase is OfflineRouteSessionPhase.STOPPED
    assert final.navigation.failure_reason is NavigationFailureReason.STEP_EVIDENCE_NOT_CONSUMED


def test_offline_restart_preserves_route_and_source_semantics() -> None:
    context = _context()
    session = _offline_session(context)
    sequencer = OfflineRouteSessionSequencer.begin(session, started_monotonic_s=0.0)
    stopped = sequencer.interrupt(session, evaluated_monotonic_s=1.0).progress
    replacement = _fresh_offline_session(
        context,
        route_session_id="synthetic-route-session-2",
        capture_session_id="synthetic-capture-session-2",
        attempt_session_id="synthetic-attempt-session-2",
    )

    changed_plan_context = replace(
        replacement.context,
        plan=replace(
            replacement.context.plan,
            identity=replace(replacement.context.plan.identity, version="synthetic-v2"),
        ),
    )
    with pytest.raises(ValueError, match="same exact route plan and version"):
        sequencer.restart(
            _offline_session(changed_plan_context, replacement.session_id),
            started_monotonic_s=2.0,
        )
    changed_policy_context = replace(
        replacement.context,
        policy=replace(replacement.context.policy, max_frame_age_s=0.6),
    )
    with pytest.raises(ValueError, match="change navigation policy"):
        sequencer.restart(
            _offline_session(changed_policy_context, replacement.session_id),
            started_monotonic_s=2.0,
        )
    changed_checkpoint_source_context = replace(
        replacement.context,
        expected_source=replace(
            replacement.context.expected_source,
            frame_source_id="synthetic-foreign-frame-source",
        ),
    )
    with pytest.raises(ValueError, match="change checkpoint source semantics"):
        sequencer.restart(
            _offline_session(changed_checkpoint_source_context, replacement.session_id),
            started_monotonic_s=2.0,
        )
    changed_attempt_source_context = replace(
        replacement.context,
        expected_attempt_source=replace(
            replacement.context.expected_attempt_source,
            version="synthetic-v2",
        ),
    )
    with pytest.raises(ValueError, match="change attempt source semantics"):
        sequencer.restart(
            _offline_session(changed_attempt_source_context, replacement.session_id),
            started_monotonic_s=2.0,
        )

    assert sequencer.progress == stopped
    assert sequencer.restart(replacement, started_monotonic_s=2.0).session == replacement


def test_attempt_ids_cannot_be_reused_after_explicit_restart() -> None:
    context = _context()
    session = _offline_session(context)
    sequencer = OfflineRouteSessionSequencer.begin(session, started_monotonic_s=0.0)
    sequencer.observe(
        session,
        _observation(
            context,
            context.plan.checkpoints[0].checkpoint_id,
            frame_id=1,
            captured_monotonic_s=0.1,
        ),
        evaluated_monotonic_s=0.2,
    )
    sequencer.prepare_step(
        session,
        attempt_id="synthetic-attempt-global",
        evaluated_monotonic_s=0.3,
    )
    sequencer.interrupt(session, evaluated_monotonic_s=0.4)
    replacement = _fresh_offline_session(
        context,
        route_session_id="synthetic-route-session-2",
        capture_session_id="synthetic-capture-session-2",
        attempt_session_id="synthetic-attempt-session-2",
    )
    sequencer.restart(replacement, started_monotonic_s=1.0)
    sequencer.observe(
        replacement,
        _observation(
            replacement.context,
            replacement.context.plan.checkpoints[0].checkpoint_id,
            frame_id=1,
            captured_monotonic_s=1.1,
        ),
        evaluated_monotonic_s=1.2,
    )
    reused = sequencer.prepare_step(
        replacement,
        attempt_id="synthetic-attempt-global",
        evaluated_monotonic_s=1.3,
    )
    assert reused.progress.navigation.failure_reason is NavigationFailureReason.DUPLICATE_ATTEMPT_ID


def test_old_checkpoint_source_cannot_enter_fresh_recovery_session() -> None:
    context = _context()
    original = _offline_session(context)
    sequencer = OfflineRouteSessionSequencer.begin(original, started_monotonic_s=0.0)
    sequencer.interrupt(original, evaluated_monotonic_s=1.0)
    replacement = _fresh_offline_session(
        context,
        route_session_id="synthetic-route-session-2",
        capture_session_id="synthetic-capture-session-2",
        attempt_session_id="synthetic-attempt-session-2",
    )
    sequencer.restart(replacement, started_monotonic_s=2.0)

    old_frame = _observation(
        context,
        context.plan.checkpoints[0].checkpoint_id,
        frame_id=1,
        captured_monotonic_s=2.1,
    )
    old_source_result = sequencer.observe(
        replacement,
        old_frame,
        evaluated_monotonic_s=2.2,
    )
    assert (
        old_source_result.progress.navigation.failure_reason
        is NavigationFailureReason.PROVENANCE_MISMATCH
    )


def test_route_session_replacement_stops_even_after_arrival_then_remains_absorbing() -> None:
    original, sequencer = _offline_after_first_receipt()
    _complete_offline_route(original, sequencer)
    assert sequencer.progress.phase is OfflineRouteSessionPhase.ARRIVED
    replacement_context = replace(
        original.context,
        plan=replace(
            original.context.plan,
            identity=replace(original.context.plan.identity, version="synthetic-v2"),
        ),
    )
    replacement = _offline_session(replacement_context, "synthetic-route-session-foreign")
    result = sequencer.observe(
        replacement,
        _observation(
            replacement_context,
            replacement_context.plan.checkpoints[0].checkpoint_id,
            frame_id=4,
            captured_monotonic_s=10.7,
        ),
        evaluated_monotonic_s=10.75,
    )
    assert result.progress.stop_reason is OfflineRouteSessionStopReason.SESSION_REPLACED
    assert (
        result.progress.navigation.failure_reason is NavigationFailureReason.ROUTE_SESSION_REPLACED
    )
    absorbed = sequencer.observe(
        original,
        _observation(
            original.context,
            original.context.plan.checkpoints[-1].checkpoint_id,
            frame_id=5,
            captured_monotonic_s=10.8,
        ),
        evaluated_monotonic_s=10.85,
    )
    assert absorbed.progress == result.progress
    assert absorbed.progress is not result.progress


def test_stop_route_stops_arrived_progress_and_is_absorbing() -> None:
    context = _context()
    arrived = _complete_route(context)
    stopped = stop_route(
        arrived,
        NavigationFailureReason.ROUTE_SESSION_REPLACED,
        evaluated_monotonic_s=10.7,
    )
    _assert_stopped(stopped, NavigationFailureReason.ROUTE_SESSION_REPLACED)
    repeated = stop_route(
        stopped.progress,
        NavigationFailureReason.SESSION_INTERRUPTED,
        evaluated_monotonic_s=10.8,
    )
    assert repeated.outcome is NavigationTransitionOutcome.TERMINAL_NO_CHANGE
    assert repeated.progress == stopped.progress
    assert repeated.progress is not stopped.progress

    changed_context = replace(
        context,
        expected_source=replace(
            context.expected_source,
            capture_session_id="synthetic-replacement-capture-session",
        ),
    )
    mismatched_followup = observe_checkpoint(
        changed_context,
        stopped.progress,
        _observation(
            changed_context,
            changed_context.plan.checkpoints[0].checkpoint_id,
            frame_id=4,
            captured_monotonic_s=10.8,
        ),
        evaluated_monotonic_s=10.9,
    )
    assert mismatched_followup.outcome is NavigationTransitionOutcome.TERMINAL_NO_CHANGE
    assert mismatched_followup.progress == stopped.progress
    assert mismatched_followup.progress is not stopped.progress


def test_shared_navigation_identifiers_reject_unicode_surrogate_code_points() -> None:
    scalar = RouteIdentity(
        route_id="synthetic-\U0001f600",
        version="synthetic-v1",
        direction=RouteDirection.MINE_TO_BANK,
    )
    assert scalar.route_id.endswith("\U0001f600")
    with pytest.raises(ValueError, match="printable"):
        RouteIdentity(
            route_id="synthetic-\ud83d\ude00",
            version="synthetic-v1",
            direction=RouteDirection.MINE_TO_BANK,
        )
