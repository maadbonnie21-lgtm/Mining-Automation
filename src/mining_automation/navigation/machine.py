"""Pure deterministic transitions for constrained fixed-route navigation."""

from __future__ import annotations

from math import isfinite

from .contracts import (
    ArrivalEvidence,
    CheckpointMatchKind,
    CheckpointObservation,
    NavigationFailureReason,
    NavigationPhase,
    NavigationStop,
    NavigationTransition,
    NavigationTransitionOutcome,
    OfflineStepProposal,
    RouteEvaluationContext,
    RouteProgress,
)

__all__ = ["observe_checkpoint", "prepare_step", "start_route"]


def _validate_evaluation_time(evaluated_monotonic_s: float) -> None:
    if isinstance(evaluated_monotonic_s, bool) or not isinstance(
        evaluated_monotonic_s, (int, float)
    ):
        raise ValueError("evaluated_monotonic_s must be finite and non-negative")
    try:
        finite = isfinite(float(evaluated_monotonic_s))
    except (OverflowError, TypeError, ValueError):
        finite = False
    if not finite or evaluated_monotonic_s < 0:
        raise ValueError("evaluated_monotonic_s must be finite and non-negative")


def start_route(
    context: RouteEvaluationContext,
    *,
    started_monotonic_s: float,
) -> RouteProgress:
    """Create route-local progress with only the departure checkpoint expected."""

    _validate_evaluation_time(started_monotonic_s)
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
        stop=NavigationStop(reason),
    )
    return NavigationTransition(NavigationTransitionOutcome.STOPPED, stopped)


def _terminal(progress: RouteProgress) -> NavigationTransition:
    return NavigationTransition(NavigationTransitionOutcome.TERMINAL_NO_CHANGE, progress)


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
    mismatch = _context_mismatch(context, progress)
    if mismatch is not None:
        return _stop(progress, mismatch, evaluated_monotonic_s)
    if progress.phase in {NavigationPhase.ARRIVED, NavigationPhase.STOPPED}:
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
        active_checkpoint_evidence=observation,
    )
    return NavigationTransition(NavigationTransitionOutcome.CHECKPOINT_ACCEPTED, ready)


def prepare_step(
    context: RouteEvaluationContext,
    progress: RouteProgress,
    *,
    evaluated_monotonic_s: float,
) -> NavigationTransition:
    """Consume one fresh checkpoint observation into an input-disabled proposal."""

    _validate_evaluation_time(evaluated_monotonic_s)
    mismatch = _context_mismatch(context, progress)
    if mismatch is not None:
        return _stop(progress, mismatch, evaluated_monotonic_s)
    if progress.phase in {NavigationPhase.ARRIVED, NavigationPhase.STOPPED}:
        return _terminal(progress)
    if evaluated_monotonic_s < progress.last_transition_monotonic_s:
        return _stop(
            progress,
            NavigationFailureReason.OUT_OF_ORDER_EVALUATION,
            evaluated_monotonic_s,
        )
    if progress.phase is not NavigationPhase.READY_FOR_STEP:
        return _stop(progress, NavigationFailureReason.STEP_NOT_READY, evaluated_monotonic_s)
    evidence = progress.active_checkpoint_evidence
    current_checkpoint_id = progress.current_checkpoint_id
    expected_checkpoint_id = progress.expected_next_checkpoint_id
    if (
        evidence is None
        or current_checkpoint_id is None
        or expected_checkpoint_id is None
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
    proposal = OfflineStepProposal(
        context=context,
        step=step,
        checkpoint_evidence=evidence,
        prepared_monotonic_s=evaluated_monotonic_s,
    )
    awaiting = RouteProgress(
        context=progress.context,
        phase=NavigationPhase.AWAITING_CHECKPOINT,
        current_checkpoint_id=None,
        expected_next_checkpoint_id=expected_checkpoint_id,
        accepted_checkpoint_count=progress.accepted_checkpoint_count,
        evidence_boundary_monotonic_s=evaluated_monotonic_s,
        last_transition_monotonic_s=evaluated_monotonic_s,
        last_accepted_provenance=progress.last_accepted_provenance,
    )
    return NavigationTransition(
        NavigationTransitionOutcome.STEP_PREPARED,
        awaiting,
        proposal,
    )
