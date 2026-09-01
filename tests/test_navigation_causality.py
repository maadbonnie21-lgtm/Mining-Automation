from __future__ import annotations

from dataclasses import replace

import pytest

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
        assert completed.proposal.context is context
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
        assert followup.progress is stopped.progress
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
