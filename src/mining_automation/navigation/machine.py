"""Pure deterministic transitions for constrained fixed-route navigation."""

from __future__ import annotations

from math import copysign, isfinite

from ..contracts import FrameRef
from .contracts import (
    ArrivalEvidence,
    CheckpointDetection,
    CheckpointDetectorIdentity,
    CheckpointEvidence,
    CheckpointMatchKind,
    CheckpointObservation,
    CheckpointProfile,
    CheckpointSourceIdentity,
    CompletedStepAttempt,
    FrameProvenance,
    NavigationFailureReason,
    NavigationPhase,
    NavigationStop,
    NavigationTransition,
    NavigationTransitionOutcome,
    OfflineStepProposal,
    RouteEvaluationContext,
    RouteIdentity,
    RouteProgress,
    Sha256Digest,
    StepAttemptIdentity,
    StepAttemptSourceIdentity,
    SyntheticStepAttemptReceipt,
    _snapshot_navigation_contract,
)

__all__ = [
    "observe_checkpoint",
    "prepare_step",
    "record_step_attempt_receipt",
    "start_route",
    "stop_route",
]


def _validate_evaluation_time(evaluated_monotonic_s: float) -> None:
    if (
        type(evaluated_monotonic_s) is not float
        or not isfinite(evaluated_monotonic_s)
        or evaluated_monotonic_s < 0.0
        or (evaluated_monotonic_s == 0.0 and copysign(1.0, evaluated_monotonic_s) < 0.0)
    ):
        raise ValueError("evaluated_monotonic_s must be an exact finite non-negative float")


def _snapshot_route_identity(identity: RouteIdentity) -> RouteIdentity:
    if type(identity) is not RouteIdentity:
        raise ValueError("route identity must use the exact navigation contract")
    return RouteIdentity(identity.route_id, identity.version, identity.direction)


def _snapshot_checkpoint_source(source: CheckpointSourceIdentity) -> CheckpointSourceIdentity:
    if type(source) is not CheckpointSourceIdentity:
        raise ValueError("checkpoint source must use the exact navigation contract")
    detector = source.detector
    profile = source.profile
    if type(detector) is not CheckpointDetectorIdentity or type(profile) is not CheckpointProfile:
        raise ValueError("checkpoint source contains a replaced detector contract")
    return CheckpointSourceIdentity(
        detector=CheckpointDetectorIdentity(detector.detector_id, detector.version),
        profile=CheckpointProfile(
            profile_id=profile.profile_id,
            version=profile.version,
            evidence_role=profile.evidence_role,
            frame_width=profile.frame_width,
            frame_height=profile.frame_height,
            pixel_format=profile.pixel_format,
            checkpoint_ids=tuple(profile.checkpoint_ids),
        ),
        frame_source_id=source.frame_source_id,
        capture_session_id=source.capture_session_id,
    )


def _snapshot_observation(observation: CheckpointObservation) -> CheckpointObservation:
    if type(observation) is not CheckpointObservation:
        raise ValueError("observation must use the exact CheckpointObservation contract")
    evidence = observation.evidence
    if type(evidence) is not CheckpointEvidence:
        raise ValueError("observation contains replaced checkpoint evidence")
    provenance = evidence.provenance
    detection = evidence.detection
    if type(provenance) is not FrameProvenance or type(detection) is not CheckpointDetection:
        raise ValueError("observation contains replaced provenance or detection")
    frame = provenance.frame
    digest = provenance.frame_payload_sha256
    if type(frame) is not FrameRef or type(digest) is not Sha256Digest:
        raise ValueError("observation contains replaced frame provenance")
    return CheckpointObservation(
        route=_snapshot_route_identity(observation.route),
        evidence=CheckpointEvidence(
            provenance=FrameProvenance(
                source=_snapshot_checkpoint_source(provenance.source),
                frame=FrameRef(
                    frame_id=frame.frame_id,
                    captured_monotonic_s=frame.captured_monotonic_s,
                    width=frame.width,
                    height=frame.height,
                ),
                pixel_format=provenance.pixel_format,
                frame_payload_sha256=Sha256Digest(digest.value),
            ),
            detection=CheckpointDetection(
                match=detection.match,
                candidate_checkpoint_ids=tuple(detection.candidate_checkpoint_ids),
                confidence=detection.confidence,
            ),
        ),
    )


def _snapshot_attempt_receipt(
    receipt: SyntheticStepAttemptReceipt,
) -> SyntheticStepAttemptReceipt:
    if type(receipt) is not SyntheticStepAttemptReceipt:
        raise ValueError("receipt must use the exact SyntheticStepAttemptReceipt contract")
    if (
        receipt.authoritative is not False
        or receipt.movement_success_proven is not False
        or receipt.live_input_enabled is not False
    ):
        raise ValueError("synthetic attempt receipt authority fields were mutated")
    identity = receipt.identity
    source = receipt.source
    if type(identity) is not StepAttemptIdentity or type(source) is not StepAttemptSourceIdentity:
        raise ValueError("synthetic attempt receipt contains replaced identity contracts")
    return SyntheticStepAttemptReceipt(
        identity=StepAttemptIdentity(
            route=_snapshot_route_identity(identity.route),
            step_id=identity.step_id,
            attempt_id=identity.attempt_id,
        ),
        source=StepAttemptSourceIdentity(
            source_id=source.source_id,
            version=source.version,
            session_id=source.session_id,
            evidence_role=source.evidence_role,
        ),
        prepared_monotonic_s=receipt.prepared_monotonic_s,
        post_attempt_monotonic_s=receipt.post_attempt_monotonic_s,
    )


def start_route(
    context: RouteEvaluationContext,
    *,
    started_monotonic_s: float,
) -> RouteProgress:
    """Create route-local progress with only the departure checkpoint expected."""

    _validate_evaluation_time(started_monotonic_s)
    context = _snapshot_navigation_contract(context)
    return RouteProgress(
        context=context,
        phase=NavigationPhase.AWAITING_CHECKPOINT,
        current_checkpoint_id=None,
        expected_next_checkpoint_id=context.plan.checkpoints[0].checkpoint_id,
        accepted_checkpoint_count=0,
        evidence_boundary_monotonic_s=started_monotonic_s,
        last_transition_monotonic_s=started_monotonic_s,
    )


def _stop(
    progress: RouteProgress,
    reason: NavigationFailureReason,
    evaluated_monotonic_s: float,
) -> NavigationTransition:
    stopped = RouteProgress(
        context=progress.context,
        phase=NavigationPhase.STOPPED,
        current_checkpoint_id=None,
        expected_next_checkpoint_id=None,
        accepted_checkpoint_count=progress.accepted_checkpoint_count,
        evidence_boundary_monotonic_s=progress.evidence_boundary_monotonic_s,
        last_transition_monotonic_s=max(
            progress.last_transition_monotonic_s,
            evaluated_monotonic_s,
        ),
        last_accepted_provenance=progress.last_accepted_provenance,
        completed_attempts=progress.completed_attempts,
        stop=NavigationStop(reason),
    )
    return NavigationTransition(NavigationTransitionOutcome.STOPPED, stopped)


def _terminal(progress: RouteProgress) -> NavigationTransition:
    return NavigationTransition(NavigationTransitionOutcome.TERMINAL_NO_CHANGE, progress)


def stop_route(
    progress: RouteProgress,
    reason: NavigationFailureReason,
    *,
    evaluated_monotonic_s: float,
) -> NavigationTransition:
    """Fail-stop a route from any non-stopped phase.

    This is the only core transition used for caller-owned interruption and
    timeout events.  It deliberately clears actionable checkpoint, proposal,
    and arrival evidence so a stopped outer session cannot be advanced by
    calling the pure route reducers directly.
    """

    if type(progress) is not RouteProgress:
        raise ValueError("progress must be RouteProgress")
    if not isinstance(reason, NavigationFailureReason):
        raise ValueError("stop reason must be NavigationFailureReason")
    _validate_evaluation_time(evaluated_monotonic_s)
    progress = _snapshot_navigation_contract(progress)
    if progress.phase is NavigationPhase.STOPPED:
        return _terminal(progress)
    if evaluated_monotonic_s < progress.last_transition_monotonic_s:
        reason = NavigationFailureReason.OUT_OF_ORDER_EVALUATION
    return _stop(progress, reason, evaluated_monotonic_s)


def _context_mismatch(
    context: RouteEvaluationContext,
    progress: RouteProgress,
) -> NavigationFailureReason | None:
    if context != progress.context:
        return NavigationFailureReason.CONTEXT_MISMATCH
    return None


def _route_mismatch(
    context: RouteEvaluationContext,
    observation: CheckpointObservation,
) -> NavigationFailureReason | None:
    expected = context.plan.identity
    observed = observation.route
    if observed.route_id != expected.route_id:
        return NavigationFailureReason.ROUTE_ID_MISMATCH
    if observed.version != expected.version:
        return NavigationFailureReason.ROUTE_VERSION_MISMATCH
    if observed.direction is not expected.direction:
        return NavigationFailureReason.DIRECTION_MISMATCH
    return None


def _attempt_route_mismatch(
    context: RouteEvaluationContext,
    receipt: SyntheticStepAttemptReceipt,
) -> NavigationFailureReason | None:
    expected = context.plan.identity
    observed = receipt.identity.route
    if observed.route_id != expected.route_id:
        return NavigationFailureReason.ROUTE_ID_MISMATCH
    if observed.version != expected.version:
        return NavigationFailureReason.ROUTE_VERSION_MISMATCH
    if observed.direction is not expected.direction:
        return NavigationFailureReason.DIRECTION_MISMATCH
    return None


def _temporal_failure(
    context: RouteEvaluationContext,
    progress: RouteProgress,
    observation: CheckpointObservation,
    evaluated_monotonic_s: float,
    *,
    compare_with_last_frame: bool = True,
) -> NavigationFailureReason | None:
    frame = observation.provenance.frame
    if frame.captured_monotonic_s > evaluated_monotonic_s:
        return NavigationFailureReason.INVALID_FRAME_TIME
    previous = progress.last_accepted_provenance
    if compare_with_last_frame and previous is not None:
        previous_frame = previous.frame
        if frame.frame_id == previous_frame.frame_id:
            return NavigationFailureReason.REPEATED_FRAME
        if (
            frame.frame_id < previous_frame.frame_id
            or frame.captured_monotonic_s < previous_frame.captured_monotonic_s
        ):
            return NavigationFailureReason.OUT_OF_ORDER_FRAME
    if frame.captured_monotonic_s <= progress.evidence_boundary_monotonic_s:
        return NavigationFailureReason.EVIDENCE_NOT_AFTER_BOUNDARY
    if evaluated_monotonic_s - frame.captured_monotonic_s > context.policy.max_frame_age_s:
        return NavigationFailureReason.STALE_FRAME
    return None


def observe_checkpoint(
    context: RouteEvaluationContext,
    progress: RouteProgress,
    observation: CheckpointObservation,
    *,
    evaluated_monotonic_s: float,
) -> NavigationTransition:
    """Evaluate exactly one explicit checkpoint observation.

    Any runtime mismatch is absorbing and clears active location evidence.  The
    function uses only the supplied monotonic time; it never reads a clock.
    """

    _validate_evaluation_time(evaluated_monotonic_s)
    context = _snapshot_navigation_contract(context)
    progress = _snapshot_navigation_contract(progress)
    if progress.phase is NavigationPhase.STOPPED:
        return _terminal(progress)
    observation = _snapshot_observation(observation)
    mismatch = _context_mismatch(context, progress)
    if mismatch is not None:
        return _stop(progress, mismatch, evaluated_monotonic_s)
    if progress.phase is NavigationPhase.ARRIVED:
        return _terminal(progress)
    if evaluated_monotonic_s < progress.last_transition_monotonic_s:
        return _stop(
            progress,
            NavigationFailureReason.OUT_OF_ORDER_EVALUATION,
            evaluated_monotonic_s,
        )
    if progress.phase is NavigationPhase.READY_FOR_STEP:
        return _stop(
            progress,
            NavigationFailureReason.STEP_EVIDENCE_NOT_CONSUMED,
            evaluated_monotonic_s,
        )
    if progress.phase is NavigationPhase.AWAITING_ATTEMPT_RECEIPT:
        return _stop(
            progress,
            NavigationFailureReason.ATTEMPT_RECEIPT_REQUIRED,
            evaluated_monotonic_s,
        )

    mismatch = _route_mismatch(context, observation)
    if mismatch is not None:
        return _stop(progress, mismatch, evaluated_monotonic_s)
    if observation.provenance.source != context.expected_source:
        return _stop(
            progress,
            NavigationFailureReason.PROVENANCE_MISMATCH,
            evaluated_monotonic_s,
        )
    temporal_failure = _temporal_failure(
        context,
        progress,
        observation,
        evaluated_monotonic_s,
    )
    if temporal_failure is not None:
        return _stop(progress, temporal_failure, evaluated_monotonic_s)
    if observation.match is CheckpointMatchKind.UNKNOWN:
        return _stop(
            progress,
            NavigationFailureReason.UNKNOWN_CHECKPOINT,
            evaluated_monotonic_s,
        )
    if observation.match is CheckpointMatchKind.AMBIGUOUS:
        return _stop(
            progress,
            NavigationFailureReason.AMBIGUOUS_CHECKPOINT,
            evaluated_monotonic_s,
        )
    if observation.confidence < context.policy.minimum_confidence:
        return _stop(progress, NavigationFailureReason.LOW_CONFIDENCE, evaluated_monotonic_s)

    observed_checkpoint_id = observation.matched_checkpoint_id
    expected_checkpoint_id = progress.expected_next_checkpoint_id
    if observed_checkpoint_id is None or expected_checkpoint_id is None:  # pragma: no cover
        raise AssertionError("validated navigation state lost its expected checkpoint")
    observed_index = context.plan.checkpoint_index(observed_checkpoint_id)
    expected_index = context.plan.checkpoint_index(expected_checkpoint_id)
    if observed_index is None:
        return _stop(
            progress,
            NavigationFailureReason.UNEXPECTED_CHECKPOINT,
            evaluated_monotonic_s,
        )
    if expected_index is None:  # pragma: no cover - RouteProgress is constructed from the plan
        return _stop(progress, NavigationFailureReason.PLAN_MISMATCH, evaluated_monotonic_s)
    if observed_index > expected_index:
        return _stop(progress, NavigationFailureReason.SKIPPED_CHECKPOINT, evaluated_monotonic_s)
    if observed_index < expected_index:
        return _stop(
            progress,
            NavigationFailureReason.OUT_OF_ORDER_CHECKPOINT,
            evaluated_monotonic_s,
        )

    checkpoint = context.plan.checkpoints[observed_index]
    if observed_index == len(context.plan.checkpoints) - 1:
        arrival = ArrivalEvidence(
            context=context,
            checkpoint=checkpoint,
            observation=observation,
        )
        arrived = RouteProgress(
            context=progress.context,
            phase=NavigationPhase.ARRIVED,
            current_checkpoint_id=checkpoint.checkpoint_id,
            expected_next_checkpoint_id=None,
            accepted_checkpoint_count=len(context.plan.checkpoints),
            evidence_boundary_monotonic_s=progress.evidence_boundary_monotonic_s,
            last_transition_monotonic_s=evaluated_monotonic_s,
            last_accepted_provenance=observation.provenance,
            completed_attempts=progress.completed_attempts,
            arrival_evidence=arrival,
        )
        return NavigationTransition(NavigationTransitionOutcome.ARRIVAL_CONFIRMED, arrived)

    ready = RouteProgress(
        context=progress.context,
        phase=NavigationPhase.READY_FOR_STEP,
        current_checkpoint_id=checkpoint.checkpoint_id,
        expected_next_checkpoint_id=context.plan.checkpoints[observed_index + 1].checkpoint_id,
        accepted_checkpoint_count=observed_index + 1,
        evidence_boundary_monotonic_s=progress.evidence_boundary_monotonic_s,
        last_transition_monotonic_s=evaluated_monotonic_s,
        last_accepted_provenance=observation.provenance,
        completed_attempts=progress.completed_attempts,
        active_checkpoint_evidence=observation,
    )
    return NavigationTransition(NavigationTransitionOutcome.CHECKPOINT_ACCEPTED, ready)


def prepare_step(
    context: RouteEvaluationContext,
    progress: RouteProgress,
    *,
    attempt_id: str,
    evaluated_monotonic_s: float,
) -> NavigationTransition:
    """Consume one fresh checkpoint observation into an input-disabled proposal."""

    _validate_evaluation_time(evaluated_monotonic_s)
    context = _snapshot_navigation_contract(context)
    progress = _snapshot_navigation_contract(progress)
    if progress.phase is NavigationPhase.STOPPED:
        return _terminal(progress)
    mismatch = _context_mismatch(context, progress)
    if mismatch is not None:
        return _stop(progress, mismatch, evaluated_monotonic_s)
    if progress.phase is NavigationPhase.ARRIVED:
        return _terminal(progress)
    if evaluated_monotonic_s < progress.last_transition_monotonic_s:
        return _stop(
            progress,
            NavigationFailureReason.OUT_OF_ORDER_EVALUATION,
            evaluated_monotonic_s,
        )
    if progress.phase is NavigationPhase.AWAITING_ATTEMPT_RECEIPT:
        return _stop(
            progress,
            NavigationFailureReason.ATTEMPT_RECEIPT_REQUIRED,
            evaluated_monotonic_s,
        )
    if progress.phase is not NavigationPhase.READY_FOR_STEP:
        return _stop(progress, NavigationFailureReason.STEP_NOT_READY, evaluated_monotonic_s)
    evidence = progress.active_checkpoint_evidence
    current_checkpoint_id = progress.current_checkpoint_id
    expected_checkpoint_id = progress.expected_next_checkpoint_id
    if (
        evidence is None or current_checkpoint_id is None or expected_checkpoint_id is None
    ):  # pragma: no cover - RouteProgress validates this invariant
        raise AssertionError("ready progress lost its active checkpoint evidence")
    if evidence.provenance.source != context.expected_source:
        return _stop(
            progress,
            NavigationFailureReason.PROVENANCE_MISMATCH,
            evaluated_monotonic_s,
        )
    temporal_failure = _temporal_failure(
        context,
        progress,
        evidence,
        evaluated_monotonic_s,
        compare_with_last_frame=False,
    )
    if temporal_failure is not None:
        return _stop(progress, temporal_failure, evaluated_monotonic_s)

    step = context.plan.step_from(current_checkpoint_id)
    if step is None or step.to_checkpoint_id != expected_checkpoint_id:  # pragma: no cover
        return _stop(progress, NavigationFailureReason.PLAN_MISMATCH, evaluated_monotonic_s)
    attempt_identity = StepAttemptIdentity(context.plan.identity, step.step_id, attempt_id)
    if any(
        previous.attempt_id == attempt_identity.attempt_id for previous in progress.attempt_history
    ):
        return _stop(
            progress,
            NavigationFailureReason.DUPLICATE_ATTEMPT_ID,
            evaluated_monotonic_s,
        )
    proposal = OfflineStepProposal(
        context=context,
        step=step,
        attempt_identity=attempt_identity,
        checkpoint_evidence=evidence,
        prepared_monotonic_s=evaluated_monotonic_s,
    )
    awaiting = RouteProgress(
        context=progress.context,
        phase=NavigationPhase.AWAITING_ATTEMPT_RECEIPT,
        current_checkpoint_id=None,
        expected_next_checkpoint_id=expected_checkpoint_id,
        accepted_checkpoint_count=progress.accepted_checkpoint_count,
        evidence_boundary_monotonic_s=evaluated_monotonic_s,
        last_transition_monotonic_s=evaluated_monotonic_s,
        last_accepted_provenance=progress.last_accepted_provenance,
        completed_attempts=progress.completed_attempts,
        pending_step_proposal=proposal,
    )
    return NavigationTransition(
        NavigationTransitionOutcome.STEP_PREPARED,
        awaiting,
        proposal,
    )


def record_step_attempt_receipt(
    context: RouteEvaluationContext,
    progress: RouteProgress,
    receipt: SyntheticStepAttemptReceipt | None,
    *,
    evaluated_monotonic_s: float,
) -> NavigationTransition:
    """Bind one source-issued synthetic attempt boundary to the pending step.

    A receipt proves only that the named offline attempt event occurred.  It
    cannot prove movement, arrival, or permission to send input.
    """

    _validate_evaluation_time(evaluated_monotonic_s)
    context = _snapshot_navigation_contract(context)
    progress = _snapshot_navigation_contract(progress)
    if progress.phase is NavigationPhase.STOPPED:
        return _terminal(progress)
    mismatch = _context_mismatch(context, progress)
    if mismatch is not None:
        return _stop(progress, mismatch, evaluated_monotonic_s)
    if progress.phase is NavigationPhase.ARRIVED:
        return _terminal(progress)
    if evaluated_monotonic_s < progress.last_transition_monotonic_s:
        return _stop(
            progress,
            NavigationFailureReason.OUT_OF_ORDER_EVALUATION,
            evaluated_monotonic_s,
        )
    if receipt is not None:
        receipt = _snapshot_attempt_receipt(receipt)
    if receipt is not None and receipt.identity in progress.attempt_history:
        return _stop(
            progress,
            NavigationFailureReason.DUPLICATE_ATTEMPT_RECEIPT,
            evaluated_monotonic_s,
        )
    if progress.phase is not NavigationPhase.AWAITING_ATTEMPT_RECEIPT:
        return _stop(
            progress,
            NavigationFailureReason.ATTEMPT_RECEIPT_NOT_EXPECTED,
            evaluated_monotonic_s,
        )
    if receipt is None:
        return _stop(
            progress,
            NavigationFailureReason.ATTEMPT_RECEIPT_REQUIRED,
            evaluated_monotonic_s,
        )

    mismatch = _attempt_route_mismatch(context, receipt)
    if mismatch is not None:
        return _stop(progress, mismatch, evaluated_monotonic_s)
    if receipt.source != context.expected_attempt_source:
        return _stop(
            progress,
            NavigationFailureReason.ATTEMPT_RECEIPT_SOURCE_MISMATCH,
            evaluated_monotonic_s,
        )
    pending = progress.pending_attempt
    if pending is None:  # pragma: no cover - RouteProgress validates this invariant
        raise AssertionError("awaiting-receipt progress lost its pending attempt")
    if receipt.identity.step_id != pending.step_id:
        return _stop(
            progress,
            NavigationFailureReason.ATTEMPT_STEP_MISMATCH,
            evaluated_monotonic_s,
        )
    if receipt.identity.attempt_id != pending.attempt_id:
        return _stop(
            progress,
            NavigationFailureReason.ATTEMPT_ID_MISMATCH,
            evaluated_monotonic_s,
        )
    pending_proposal = progress.pending_step_proposal
    if pending_proposal is None:  # pragma: no cover - RouteProgress validates this invariant
        raise AssertionError("awaiting-receipt progress lost its pending proposal")
    if receipt.prepared_monotonic_s != pending_proposal.prepared_monotonic_s:
        return _stop(
            progress,
            NavigationFailureReason.ATTEMPT_PREPARATION_MISMATCH,
            evaluated_monotonic_s,
        )
    if receipt.post_attempt_monotonic_s > evaluated_monotonic_s:
        return _stop(
            progress,
            NavigationFailureReason.INVALID_ATTEMPT_TIME,
            evaluated_monotonic_s,
        )
    if receipt.post_attempt_monotonic_s <= progress.evidence_boundary_monotonic_s:
        return _stop(
            progress,
            NavigationFailureReason.ATTEMPT_NOT_AFTER_PREPARATION,
            evaluated_monotonic_s,
        )
    if (
        evaluated_monotonic_s - receipt.post_attempt_monotonic_s
        > context.policy.max_attempt_receipt_age_s
    ):
        return _stop(
            progress,
            NavigationFailureReason.STALE_ATTEMPT_RECEIPT,
            evaluated_monotonic_s,
        )

    awaiting = RouteProgress(
        context=progress.context,
        phase=NavigationPhase.AWAITING_CHECKPOINT,
        current_checkpoint_id=None,
        expected_next_checkpoint_id=progress.expected_next_checkpoint_id,
        accepted_checkpoint_count=progress.accepted_checkpoint_count,
        evidence_boundary_monotonic_s=receipt.post_attempt_monotonic_s,
        last_transition_monotonic_s=evaluated_monotonic_s,
        last_accepted_provenance=progress.last_accepted_provenance,
        completed_attempts=progress.completed_attempts
        + (CompletedStepAttempt(pending_proposal, receipt, evaluated_monotonic_s),),
    )
    return NavigationTransition(
        NavigationTransitionOutcome.STEP_ATTEMPT_RECORDED,
        awaiting,
        attempt_receipt=receipt,
    )
