from __future__ import annotations

import ast
import json
import os
from dataclasses import dataclass, fields, replace
from pathlib import Path

import pytest

import mining_automation.navigation as navigation_root
import mining_automation.navigation.endurance_rehearsal as endurance_module
import mining_automation.navigation.release_decision as release_module
import mining_automation.navigation.round_trip_rehearsal as round_trip_module
from mining_automation.capture.frame import Frame, PixelFormat
from mining_automation.contracts import FrameRef
from mining_automation.navigation import integration_boundary
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
    FrameProvenance,
    NavigationFailureReason,
    NavigationPolicy,
    OfflineStepProposal,
    RouteDirection,
    RouteEndpoint,
    RouteEndpointRole,
    RouteEvaluationContext,
    RouteIdentity,
    RoutePlan,
    RouteStep,
    Sha256Digest,
    StepAttemptSourceIdentity,
    SyntheticStepAttemptReceipt,
)
from mining_automation.navigation.durable_route_evidence import (
    ACQUISITION_PLAN_FILENAME,
    DURABLE_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE,
    DURABLE_WRITER_NAMESPACE_CONTRACT,
    DurableAcquisitionReceipt,
    DurableAcquisitionTransaction,
    DurableReviewReceipt,
    DurableRouteEvidenceFilesystemExpectation,
    begin_durable_acquisition,
    begin_durable_review,
    load_durable_acquisition,
)
from mining_automation.navigation.endurance_rehearsal import (
    SyntheticEnduranceAttemptOutcome,
    SyntheticEnduranceExpectation,
    SyntheticEndurancePhase,
    SyntheticEnduranceStopReason,
    SyntheticTraversalAttemptExpectation,
    evaluate_synthetic_endurance_rehearsal,
)
from mining_automation.navigation.offline_route_session import (
    OfflineRouteSession,
    OfflineRouteSessionPhase,
    OfflineRouteSessionResult,
    OfflineRouteSessionSequencer,
    OfflineRouteSessionStopReason,
)
from mining_automation.navigation.passive_campaign import (
    PassiveCaptureRequest,
    PassiveCaptureSourceIdentity,
    PassiveSourceFrame,
)
from mining_automation.navigation.release_decision import (
    REQUIRED_NAVIGATION_HOST_THREAT_MODEL,
    REQUIRED_NAVIGATION_TARGET_PLATFORM,
    DirectionReleaseRequirement,
    DownstreamEvidenceRequirement,
    NavigationReleaseDecision,
    PairReleaseRequirement,
    PostAttemptCausalityExpectation,
    ReleaseCheckStatus,
    ReviewerDecisionSummary,
    evaluate_navigation_release_readiness,
)
from mining_automation.navigation.round_trip_rehearsal import (
    SyntheticRoundTripPhase,
    SyntheticRoundTripStopReason,
    SyntheticRoundTripTimelineExpectation,
    evaluate_synthetic_round_trip_rehearsal,
)
from mining_automation.navigation.route_evidence import (
    RouteEvidenceCampaignPlan,
    RouteEvidenceCaptureBuildIdentity,
    RouteEvidenceCaseRole,
    RouteEvidenceCaseSpec,
    RouteEvidenceCaseTruth,
    RouteEvidenceIntegrityError,
    RouteEvidenceReviewDecision,
)


def _digest(label: str) -> Sha256Digest:
    return Sha256Digest.from_bytes(label.encode("ascii"))


class _Clock:
    live_navigation_enabled = False
    input_authority = False

    def __init__(self) -> None:
        self.value = 0.0

    def now_monotonic_s(self) -> float:
        return self.value


class _Detector:
    def __init__(self, identity: PassiveCaptureSourceIdentity) -> None:
        self._identity = identity.checkpoint_source.detector
        self._profile = identity.checkpoint_source.profile
        self.next_detection = CheckpointDetection(CheckpointMatchKind.UNKNOWN, (), 0.0)

    @property
    def identity(self) -> CheckpointDetectorIdentity:
        return self._identity

    @property
    def profile(self) -> CheckpointProfile:
        return self._profile

    def detect(self, frame: Frame, /) -> CheckpointDetection:
        del frame
        return self.next_detection


class _Source:
    def __init__(self, identity: PassiveCaptureSourceIdentity) -> None:
        self._identity = identity
        self.frame_id = 1
        self.frame_time = 1.0
        self.capture_id = "synthetic-release-capture-1"
        self.captured_at_utc = "2026-09-01T00:00:01Z"
        self.payload_byte = 1

    @property
    def identity(self) -> PassiveCaptureSourceIdentity:
        return self._identity

    def capture(self, request: PassiveCaptureRequest, /) -> PassiveSourceFrame:
        return PassiveSourceFrame(
            self._identity,
            request.request_id,
            self.capture_id,
            self.captured_at_utc,
            Frame(
                FrameRef(self.frame_id, self.frame_time, 2, 1),
                bytes([self.payload_byte]) * 8,
                PixelFormat.BGRA8888,
            ),
        )


@dataclass(frozen=True, slots=True)
class _DirectionEvidence:
    acquisition_root: Path
    review_root: Path
    expectation: DurableRouteEvidenceFilesystemExpectation
    route_plan: RoutePlan
    causality_expectation: PostAttemptCausalityExpectation
    result: OfflineRouteSessionResult


@dataclass(frozen=True, slots=True)
class _PairEvidence:
    mine_to_bank: _DirectionEvidence
    bank_to_mine: _DirectionEvidence


def _campaign(
    direction: RouteDirection,
    *,
    campaign_id: str,
    capture_session_id: str,
    operator_id: str,
    mine_location_id: str,
    bank_location_id: str,
    route_id: str | None = None,
    route_version: str = "1.0.0-synthetic",
) -> tuple[RouteEvidenceCampaignPlan, PassiveCaptureSourceIdentity]:
    prefix = (
        "synthetic-release-m2b"
        if direction is RouteDirection.MINE_TO_BANK
        else ("synthetic-release-b2m")
    )
    origin, destination = (
        (
            RouteEndpoint(mine_location_id, RouteEndpointRole.MINE),
            RouteEndpoint(bank_location_id, RouteEndpointRole.BANK),
        )
        if direction is RouteDirection.MINE_TO_BANK
        else (
            RouteEndpoint(bank_location_id, RouteEndpointRole.BANK),
            RouteEndpoint(mine_location_id, RouteEndpointRole.MINE),
        )
    )
    checkpoints = (
        Checkpoint(f"{prefix}-departure", CheckpointRole.DEPARTURE),
        Checkpoint(f"{prefix}-transit", CheckpointRole.TRANSIT),
        Checkpoint(f"{prefix}-arrival", CheckpointRole.ARRIVAL),
    )
    route_plan = RoutePlan(
        identity=RouteIdentity(
            f"{prefix}-route" if route_id is None else route_id,
            route_version,
            direction,
        ),
        origin=origin,
        destination=destination,
        checkpoints=checkpoints,
        steps=tuple(
            RouteStep(
                f"{prefix}-step-{index + 1}",
                checkpoints[index].checkpoint_id,
                checkpoints[index + 1].checkpoint_id,
            )
            for index in range(len(checkpoints) - 1)
        ),
    )
    profile = CheckpointProfile(
        profile_id=f"{prefix}-profile",
        version="1.0.0-synthetic",
        evidence_role=CheckpointEvidenceRole.SYNTHETIC_ARCHITECTURE_TEST_ONLY,
        frame_width=2,
        frame_height=1,
        pixel_format=PixelFormat.BGRA8888,
        checkpoint_ids=tuple(item.checkpoint_id for item in checkpoints),
    )
    checkpoint_source = CheckpointSourceIdentity(
        detector=CheckpointDetectorIdentity(f"{prefix}-detector", "1.0.0-synthetic"),
        profile=profile,
        frame_source_id=f"{prefix}-source",
        capture_session_id=capture_session_id,
    )
    capture_build = RouteEvidenceCaptureBuildIdentity(
        build_id=f"{prefix}-capture-build",
        version="1.0.0-synthetic",
        content_sha256=_digest(f"{prefix}-capture-build-content"),
    )
    plan = RouteEvidenceCampaignPlan(
        campaign_id=campaign_id,
        route_plan=route_plan,
        detector=checkpoint_source.detector,
        profile=profile.identity,
        capture_source_id=checkpoint_source.frame_source_id,
        capture_session_id=capture_session_id,
        capture_build=capture_build,
        frame_width=profile.frame_width,
        frame_height=profile.frame_height,
        pixel_format=profile.pixel_format,
        capture_configuration_sha256=_digest(f"{prefix}-capture-configuration"),
        capture_environment_sha256=_digest(f"{prefix}-capture-environment"),
        support_envelope_sha256=_digest(f"{prefix}-support-envelope"),
        operator_id=operator_id,
        created_at_utc="2026-09-01T00:00:00Z",
        cases=tuple(
            RouteEvidenceCaseSpec(
                ordinal,
                f"{prefix}-case-{ordinal}",
                (
                    RouteEvidenceCaseRole.ROUTE_ARRIVAL
                    if checkpoint.role is CheckpointRole.ARRIVAL
                    else RouteEvidenceCaseRole.CHECKPOINT_POSITIVE
                ),
                checkpoint.checkpoint_id,
            )
            for ordinal, checkpoint in enumerate(checkpoints, start=1)
        ),
    )
    return plan, PassiveCaptureSourceIdentity(
        checkpoint_source=checkpoint_source,
        capture_build=capture_build,
        capture_configuration_sha256=plan.capture_configuration_sha256,
        capture_environment_sha256=plan.capture_environment_sha256,
        support_envelope_sha256=plan.support_envelope_sha256,
    )


def _capture_next(
    transaction: DurableAcquisitionTransaction,
    source: _Source,
    detector: _Detector,
    clock: _Clock,
    ordinal: int,
) -> None:
    acknowledged = float(ordinal * 10)
    spec = transaction.progress.plan.cases[ordinal - 1]
    transaction.request_capture(
        request_id=f"{transaction.progress.plan.campaign_id}-request-{ordinal}",
        operator_id=transaction.progress.plan.operator_id,
        acknowledged_monotonic_s=acknowledged,
    )
    source.frame_id = ordinal
    source.frame_time = acknowledged + 1.0
    source.capture_id = f"{transaction.progress.plan.campaign_id}-capture-{ordinal}"
    source.captured_at_utc = f"2026-09-01T00:00:{ordinal:02d}Z"
    source.payload_byte = ordinal
    detector.next_detection = CheckpointDetection(
        CheckpointMatchKind.MATCHED,
        (spec.checkpoint_id,),
        1.0,
    )
    clock.value = acknowledged + 2.0
    transaction.capture()


def _full_expectation(
    acquisition: DurableAcquisitionReceipt,
    review: DurableReviewReceipt,
) -> DurableRouteEvidenceFilesystemExpectation:
    pins = acquisition.expectation
    return DurableRouteEvidenceFilesystemExpectation(
        finalized_package_sha256=pins.finalized_package_sha256,
        acquisition_head_sha256=pins.acquisition_head_sha256,
        campaign_id=pins.campaign_id,
        route=pins.route,
        direction=pins.direction,
        route_plan_sha256=pins.route_plan_sha256,
        detector=pins.detector,
        profile=pins.profile,
        capture_source_id=pins.capture_source_id,
        capture_session_id=pins.capture_session_id,
        capture_build=pins.capture_build,
        frame_width=pins.frame_width,
        frame_height=pins.frame_height,
        pixel_format=pins.pixel_format,
        capture_configuration_sha256=pins.capture_configuration_sha256,
        capture_environment_sha256=pins.capture_environment_sha256,
        support_envelope_sha256=pins.support_envelope_sha256,
        independent_review_sha256=review.independent_review_sha256,
        reviewer_id=review.reviewer_id,
        acquisition_journal_head_sha256=pins.acquisition_journal_head_sha256,
        acquisition_finalization_sha256=pins.acquisition_finalization_sha256,
        review_id=review.review_id,
        review_plan_sha256=review.review_plan_sha256,
        review_journal_head_sha256=review.review_journal_head_sha256,
        review_finalization_sha256=review.review_finalization_sha256,
    )


def _execution_context(
    plan: RoutePlan,
    source: PassiveCaptureSourceIdentity,
    *,
    attempt_session_suffix: str = "",
) -> RouteEvaluationContext:
    prefix = (
        "synthetic-release-m2b"
        if plan.identity.direction is RouteDirection.MINE_TO_BANK
        else ("synthetic-release-b2m")
    )
    return RouteEvaluationContext(
        plan=plan,
        expected_source=source.checkpoint_source,
        expected_attempt_source=StepAttemptSourceIdentity(
            source_id=f"{prefix}-attempt-source",
            version="1.0.0-synthetic",
            session_id=(
                f"{prefix}-attempt-session"
                if not attempt_session_suffix
                else f"{prefix}-attempt-session-{attempt_session_suffix}"
            ),
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
    return CheckpointObservation(
        route=context.plan.identity,
        evidence=CheckpointEvidence(
            provenance=FrameProvenance(
                source=context.expected_source,
                frame=FrameRef(
                    frame_id=frame_id,
                    captured_monotonic_s=captured_monotonic_s,
                    width=context.expected_source.frame_width,
                    height=context.expected_source.frame_height,
                ),
                pixel_format=context.expected_source.pixel_format,
                frame_payload_sha256=_digest(
                    f"{context.plan.identity.direction.value}:{frame_id}:"
                    f"{captured_monotonic_s}:{checkpoint_id}"
                ),
            ),
            detection=CheckpointDetection(
                CheckpointMatchKind.MATCHED,
                (checkpoint_id,),
                0.99,
            ),
        ),
    )


def _receipt(
    proposal: OfflineStepProposal,
    *,
    post_attempt_monotonic_s: float,
) -> SyntheticStepAttemptReceipt:
    return SyntheticStepAttemptReceipt(
        identity=proposal.attempt_identity,
        source=proposal.context.expected_attempt_source,
        prepared_monotonic_s=proposal.prepared_monotonic_s,
        post_attempt_monotonic_s=post_attempt_monotonic_s,
    )


def _session(context: RouteEvaluationContext, suffix: str = "complete") -> OfflineRouteSession:
    prefix = "m2b" if context.plan.identity.direction is RouteDirection.MINE_TO_BANK else "b2m"
    return OfflineRouteSession(
        session_id=f"synthetic-release-{prefix}-route-session-{suffix}",
        context=context,
        direction=context.plan.identity.direction,
    )


def _complete_result(
    context: RouteEvaluationContext,
    *,
    started_monotonic_s: float = 10.0,
    first_frame_monotonic_s: float | None = None,
    session_suffix: str = "complete",
    attempt_id_prefix: str | None = None,
) -> OfflineRouteSessionResult:
    session = _session(context, session_suffix)
    sequencer = OfflineRouteSessionSequencer.begin(
        session,
        started_monotonic_s=started_monotonic_s,
    )
    first_frame = (
        started_monotonic_s + 0.1 if first_frame_monotonic_s is None else first_frame_monotonic_s
    )
    last_result: OfflineRouteSessionResult | None = None
    for index, checkpoint in enumerate(context.plan.checkpoints):
        captured = first_frame + (index * 0.3)
        last_result = sequencer.observe(
            session,
            _observation(
                context,
                checkpoint.checkpoint_id,
                frame_id=index + 1,
                captured_monotonic_s=captured,
            ),
            evaluated_monotonic_s=captured + 0.05,
        )
        if checkpoint.role is CheckpointRole.ARRIVAL:
            break
        prepared = sequencer.prepare_step(
            session,
            attempt_id=(
                f"{session.session_id}-attempt-{index + 1}"
                if attempt_id_prefix is None
                else f"{attempt_id_prefix}-attempt-{index + 1}"
            ),
            evaluated_monotonic_s=captured + 0.1,
        )
        assert prepared.navigation_transition is not None
        proposal = prepared.navigation_transition.step_proposal
        assert proposal is not None
        last_result = sequencer.record_attempt(
            session,
            _receipt(proposal, post_attempt_monotonic_s=captured + 0.2),
            evaluated_monotonic_s=captured + 0.25,
        )
    assert last_result is not None
    assert last_result.progress.phase is OfflineRouteSessionPhase.ARRIVED
    return last_result


def _causality_expectation(
    route_plan_sha256: Sha256Digest,
    result: OfflineRouteSessionResult,
) -> PostAttemptCausalityExpectation:
    session = result.progress.session
    return PostAttemptCausalityExpectation(
        route=session.context.plan.identity,
        route_plan_sha256=route_plan_sha256,
        route_session_id=session.session_id,
        attempt_source=session.context.expected_attempt_source,
        policy=session.context.policy,
    )


def _interrupted_result(context: RouteEvaluationContext) -> OfflineRouteSessionResult:
    session = _session(context, "interrupted")
    sequencer = OfflineRouteSessionSequencer.begin(session, started_monotonic_s=20.0)
    return sequencer.interrupt(session, evaluated_monotonic_s=20.1)


def _equal_boundary_result(context: RouteEvaluationContext) -> OfflineRouteSessionResult:
    session = _session(context, "equal-boundary")
    sequencer = OfflineRouteSessionSequencer.begin(session, started_monotonic_s=30.0)
    sequencer.observe(
        session,
        _observation(
            context,
            context.plan.checkpoints[0].checkpoint_id,
            frame_id=101,
            captured_monotonic_s=30.1,
        ),
        evaluated_monotonic_s=30.15,
    )
    prepared = sequencer.prepare_step(
        session,
        attempt_id=f"{session.session_id}-attempt-1",
        evaluated_monotonic_s=30.2,
    )
    assert prepared.navigation_transition is not None
    proposal = prepared.navigation_transition.step_proposal
    assert proposal is not None
    sequencer.record_attempt(
        session,
        _receipt(proposal, post_attempt_monotonic_s=30.3),
        evaluated_monotonic_s=30.35,
    )
    return sequencer.observe(
        session,
        _observation(
            context,
            context.plan.checkpoints[1].checkpoint_id,
            frame_id=102,
            captured_monotonic_s=30.3,
        ),
        evaluated_monotonic_s=30.4,
    )


def _build_direction(
    root: Path,
    direction: RouteDirection,
    *,
    campaign_id: str,
    capture_session_id: str,
    operator_id: str,
    review_id: str,
    reviewer_id: str,
    mine_location_id: str,
    bank_location_id: str,
    reject_arrival: bool = False,
    route_id: str | None = None,
    route_version: str = "1.0.0-synthetic",
    result_session_suffix: str = "complete",
    attempt_session_suffix: str = "",
) -> _DirectionEvidence:
    root.mkdir(parents=True)
    acquisition_root = root / "acquisition"
    review_root = root / "review"
    plan, identity = _campaign(
        direction,
        campaign_id=campaign_id,
        capture_session_id=capture_session_id,
        operator_id=operator_id,
        mine_location_id=mine_location_id,
        bank_location_id=bank_location_id,
        route_id=route_id,
        route_version=route_version,
    )
    clock = _Clock()
    source = _Source(identity)
    detector = _Detector(identity)
    acquisition = begin_durable_acquisition(
        acquisition_root,
        plan,
        source,
        detector,
        clock,
        started_monotonic_s=0.0,
    )
    for ordinal in range(1, len(plan.cases) + 1):
        _capture_next(acquisition, source, detector, clock, ordinal)
    clock.value = 40.0
    acquisition_receipt = acquisition.finalize(finalized_at_utc="2026-09-01T00:00:10Z")
    verified_acquisition = load_durable_acquisition(
        acquisition_root,
        acquisition_receipt.expectation,
    )
    review = begin_durable_review(
        review_root,
        acquisition_root,
        acquisition_receipt.expectation,
        review_id=review_id,
        reviewer_id=reviewer_id,
        started_at_utc="2026-09-01T00:00:20Z",
    )
    for ordinal, (spec, owned) in enumerate(
        zip(
            verified_acquisition.package.campaign_plan.cases,
            verified_acquisition.package.cases,
            strict=True,
        ),
        start=1,
    ):
        review.record_case_truth(
            RouteEvidenceCaseTruth(
                case_id=spec.case_id,
                frame_sha256=owned.frame_artifact.sha256,
                detector_report_sha256=owned.detector_report_artifact.sha256,
                decision=(
                    RouteEvidenceReviewDecision.REJECTED
                    if reject_arrival and spec.role is RouteEvidenceCaseRole.ROUTE_ARRIVAL
                    else RouteEvidenceReviewDecision.APPROVED
                ),
                detection=CheckpointDetection(
                    CheckpointMatchKind.MATCHED,
                    (spec.checkpoint_id,),
                    1.0,
                ),
            ),
            recorded_at_utc=f"2026-09-01T00:00:{20 + ordinal:02d}Z",
        )
    review_receipt = review.finalize(reviewed_at_utc="2026-09-01T00:00:30Z")
    context = _execution_context(
        plan.route_plan,
        identity,
        attempt_session_suffix=attempt_session_suffix,
    )
    result = _complete_result(context, session_suffix=result_session_suffix)
    return _DirectionEvidence(
        acquisition_root=acquisition_root,
        review_root=review_root,
        expectation=_full_expectation(acquisition_receipt, review_receipt),
        route_plan=plan.route_plan,
        causality_expectation=_causality_expectation(plan.route_plan_sha256, result),
        result=result,
    )


def _build_pair(
    root: Path,
    *,
    mine_location_id: str = "synthetic-shared-mine",
    bank_location_id: str = "synthetic-shared-bank",
    bank_to_mine_mine_location_id: str | None = None,
    mine_to_bank_reject_arrival: bool = False,
    bank_to_mine_reject_arrival: bool = False,
    shared_route_id: str | None = None,
    shared_route_version: str = "1.0.0-synthetic",
    shared_reviewer_id: str | None = None,
    identity_suffix: str = "",
    attempt_session_suffix: str | None = None,
) -> _PairEvidence:
    identity_token = "" if not identity_suffix else f"-{identity_suffix}"
    result_session_suffix = "complete" if not identity_suffix else f"complete-{identity_suffix}"
    effective_attempt_session_suffix = (
        identity_suffix if attempt_session_suffix is None else attempt_session_suffix
    )
    return _PairEvidence(
        mine_to_bank=_build_direction(
            root / "mine-to-bank",
            RouteDirection.MINE_TO_BANK,
            campaign_id=f"synthetic-release-m2b{identity_token}-campaign",
            capture_session_id=f"synthetic-release-m2b{identity_token}-session",
            operator_id=f"synthetic-release-m2b{identity_token}-operator",
            review_id=f"synthetic-release-m2b{identity_token}-review",
            reviewer_id=(shared_reviewer_id or f"synthetic-release-m2b{identity_token}-reviewer"),
            mine_location_id=mine_location_id,
            bank_location_id=bank_location_id,
            route_id=shared_route_id,
            route_version=shared_route_version,
            reject_arrival=mine_to_bank_reject_arrival,
            result_session_suffix=result_session_suffix,
            attempt_session_suffix=effective_attempt_session_suffix,
        ),
        bank_to_mine=_build_direction(
            root / "bank-to-mine",
            RouteDirection.BANK_TO_MINE,
            campaign_id=f"synthetic-release-b2m{identity_token}-campaign",
            capture_session_id=f"synthetic-release-b2m{identity_token}-session",
            operator_id=f"synthetic-release-b2m{identity_token}-operator",
            review_id=f"synthetic-release-b2m{identity_token}-review",
            reviewer_id=(shared_reviewer_id or f"synthetic-release-b2m{identity_token}-reviewer"),
            mine_location_id=(
                mine_location_id
                if bank_to_mine_mine_location_id is None
                else bank_to_mine_mine_location_id
            ),
            bank_location_id=bank_location_id,
            reject_arrival=bank_to_mine_reject_arrival,
            route_id=shared_route_id,
            route_version=shared_route_version,
            result_session_suffix=result_session_suffix,
            attempt_session_suffix=effective_attempt_session_suffix,
        ),
    )


def _evaluate(pair: _PairEvidence, **overrides: object) -> NavigationReleaseDecision:
    arguments: dict[str, object] = {
        "mine_to_bank_acquisition_root": pair.mine_to_bank.acquisition_root,
        "mine_to_bank_review_root": pair.mine_to_bank.review_root,
        "mine_to_bank_expectation": pair.mine_to_bank.expectation,
        "mine_to_bank_post_attempt_expectation": (pair.mine_to_bank.causality_expectation),
        "mine_to_bank_post_attempt_result": pair.mine_to_bank.result,
        "bank_to_mine_acquisition_root": pair.bank_to_mine.acquisition_root,
        "bank_to_mine_review_root": pair.bank_to_mine.review_root,
        "bank_to_mine_expectation": pair.bank_to_mine.expectation,
        "bank_to_mine_post_attempt_expectation": (pair.bank_to_mine.causality_expectation),
        "bank_to_mine_post_attempt_result": pair.bank_to_mine.result,
    }
    arguments.update(overrides)
    return evaluate_navigation_release_readiness(**arguments)  # type: ignore[arg-type]


def _pair_with_result(
    pair: _PairEvidence,
    direction: RouteDirection,
    result: OfflineRouteSessionResult,
) -> _PairEvidence:
    source = pair.mine_to_bank if direction is RouteDirection.MINE_TO_BANK else pair.bank_to_mine
    updated = replace(
        source,
        causality_expectation=_causality_expectation(
            source.expectation.route_plan_sha256,
            result,
        ),
        result=result,
    )
    return (
        replace(pair, mine_to_bank=updated)
        if direction is RouteDirection.MINE_TO_BANK
        else replace(pair, bank_to_mine=updated)
    )


def _sequential_return_result(
    pair: _PairEvidence,
    *,
    first_frame_monotonic_s: float = 20.1,
    session_suffix: str = "round-trip-return",
) -> OfflineRouteSessionResult:
    return _complete_result(
        pair.bank_to_mine.result.progress.session.context,
        started_monotonic_s=first_frame_monotonic_s - 0.1,
        first_frame_monotonic_s=first_frame_monotonic_s,
        session_suffix=session_suffix,
    )


def _timeline_expectation(
    decision: NavigationReleaseDecision,
    mine_to_bank_result: OfflineRouteSessionResult,
    bank_to_mine_result: OfflineRouteSessionResult,
    *,
    timeline_id: str = "synthetic-round-trip-shared-timeline",
) -> SyntheticRoundTripTimelineExpectation:
    return SyntheticRoundTripTimelineExpectation(
        timeline_id=timeline_id,
        release_decision_sha256=decision.content_sha256,
        mine_to_bank_route_session_id=(mine_to_bank_result.progress.session.session_id),
        mine_to_bank_session_result_sha256=(
            decision.mine_to_bank.post_attempt_causality.session_result_sha256
        ),
        bank_to_mine_route_session_id=(bank_to_mine_result.progress.session.session_id),
        bank_to_mine_session_result_sha256=(
            decision.bank_to_mine.post_attempt_causality.session_result_sha256
        ),
    )


def _round_trip(
    decision: NavigationReleaseDecision,
    mine_to_bank_result: OfflineRouteSessionResult,
    bank_to_mine_result: OfflineRouteSessionResult,
) -> round_trip_module.SyntheticRoundTripRehearsalReport:
    return evaluate_synthetic_round_trip_rehearsal(
        decision,
        timeline_expectation=_timeline_expectation(
            decision,
            mine_to_bank_result,
            bank_to_mine_result,
        ),
        mine_to_bank_result=mine_to_bank_result,
        bank_to_mine_result=bank_to_mine_result,
    )


def _completed_round_trip(
    pair: _PairEvidence,
) -> tuple[
    _PairEvidence,
    NavigationReleaseDecision,
    OfflineRouteSessionResult,
    round_trip_module.SyntheticRoundTripRehearsalReport,
]:
    return_result = _sequential_return_result(pair)
    evidence = _pair_with_result(pair, RouteDirection.BANK_TO_MINE, return_result)
    decision = _evaluate(evidence)
    return (
        evidence,
        decision,
        return_result,
        _round_trip(decision, evidence.mine_to_bank.result, return_result),
    )


def _completed_round_trip_at(
    pair: _PairEvidence,
    *,
    traversal_id: str,
    outbound_started_monotonic_s: float,
    outbound_first_frame_monotonic_s: float | None = None,
    outbound_attempt_id_prefix: str | None = None,
) -> round_trip_module.SyntheticRoundTripRehearsalReport:
    outbound = _complete_result(
        pair.mine_to_bank.result.progress.session.context,
        started_monotonic_s=outbound_started_monotonic_s,
        first_frame_monotonic_s=outbound_first_frame_monotonic_s,
        session_suffix=f"{traversal_id}-outbound",
        attempt_id_prefix=outbound_attempt_id_prefix,
    )
    return_started = max(
        outbound.progress.last_event_monotonic_s + 1.0,
        outbound_started_monotonic_s + 10.0,
    )
    return_result = _complete_result(
        pair.bank_to_mine.result.progress.session.context,
        started_monotonic_s=return_started,
        session_suffix=f"{traversal_id}-return",
    )
    evidence = _pair_with_result(pair, RouteDirection.MINE_TO_BANK, outbound)
    evidence = _pair_with_result(evidence, RouteDirection.BANK_TO_MINE, return_result)
    decision = _evaluate(evidence)
    return _round_trip(decision, outbound, return_result)


def _stopped_round_trip(
    pair: _PairEvidence,
    *,
    direction: RouteDirection,
    case: str,
) -> tuple[OfflineRouteSessionResult, round_trip_module.SyntheticRoundTripRehearsalReport]:
    source = pair.mine_to_bank if direction is RouteDirection.MINE_TO_BANK else pair.bank_to_mine
    stopped = _stopped_result(source.result.progress.session.context, case)
    evidence = _pair_with_result(pair, direction, stopped)
    decision = _evaluate(evidence)
    return stopped, _round_trip(
        decision,
        evidence.mine_to_bank.result,
        evidence.bank_to_mine.result,
    )


def _endurance_expectation(
    reports: tuple[round_trip_module.SyntheticRoundTripRehearsalReport, ...],
    *,
    planned_cycle_count: int,
    cycle_numbers: tuple[int, ...] | None = None,
    recovery_links: tuple[str | None, ...] | None = None,
    campaign_id: str = "synthetic-repeated-round-trip-campaign",
) -> SyntheticEnduranceExpectation:
    cycles = tuple(range(1, len(reports) + 1)) if cycle_numbers is None else cycle_numbers
    recoveries = (None,) * len(reports) if recovery_links is None else recovery_links
    assert len(cycles) == len(reports)
    assert len(recoveries) == len(reports)
    return SyntheticEnduranceExpectation(
        campaign_id=campaign_id,
        shared_timeline_id="synthetic-round-trip-shared-timeline",
        planned_cycle_count=planned_cycle_count,
        ordered_attempts=tuple(
            SyntheticTraversalAttemptExpectation(
                traversal_id=f"synthetic-endurance-traversal-{index}",
                cycle_number=cycle_number,
                scenario_id=(
                    "nominal-round-trip"
                    if report.phase is SyntheticRoundTripPhase.COMPLETED
                    else f"retained-{report.stop_reason.value}"
                ),
                round_trip_sha256=report.content_sha256,
                expected_round_trip_phase=report.phase,
                expected_round_trip_stop_reason=report.stop_reason,
                recovery_of_traversal_id=recovery,
            )
            for index, (report, cycle_number, recovery) in enumerate(
                zip(reports, cycles, recoveries, strict=True),
                start=1,
            )
        ),
    )


def _stopped_result(
    context: RouteEvaluationContext,
    case: str,
) -> OfflineRouteSessionResult:
    session = _session(context, f"round-trip-{case}")
    sequencer = OfflineRouteSessionSequencer.begin(session, started_monotonic_s=30.0)
    if case == "interrupted":
        return sequencer.interrupt(session, evaluated_monotonic_s=30.1)
    if case == "checkpoint-timeout":
        return sequencer.timeout(session, evaluated_monotonic_s=30.1)
    departure_id = context.plan.checkpoints[0].checkpoint_id
    observation = _observation(
        context,
        departure_id,
        frame_id=301,
        captured_monotonic_s=30.1,
    )
    if case == "wrong-direction":
        wrong_direction = (
            RouteDirection.BANK_TO_MINE
            if context.plan.identity.direction is RouteDirection.MINE_TO_BANK
            else RouteDirection.MINE_TO_BANK
        )
        observation = replace(
            observation,
            route=RouteIdentity(
                context.plan.identity.route_id,
                context.plan.identity.version,
                wrong_direction,
            ),
        )
        return sequencer.observe(session, observation, evaluated_monotonic_s=30.15)
    if case == "wrong-route":
        observation = replace(
            observation,
            route=RouteIdentity(
                "foreign-synthetic-route",
                context.plan.identity.version,
                context.plan.identity.direction,
            ),
        )
        return sequencer.observe(session, observation, evaluated_monotonic_s=30.15)
    if case == "wrong-version":
        observation = replace(
            observation,
            route=RouteIdentity(
                context.plan.identity.route_id,
                "foreign-route-version",
                context.plan.identity.direction,
            ),
        )
        return sequencer.observe(session, observation, evaluated_monotonic_s=30.15)
    if case == "wrong-checkpoint":
        return sequencer.observe(
            session,
            _observation(
                context,
                context.plan.checkpoints[1].checkpoint_id,
                frame_id=301,
                captured_monotonic_s=30.1,
            ),
            evaluated_monotonic_s=30.15,
        )
    if case == "stale":
        return sequencer.observe(session, observation, evaluated_monotonic_s=31.0)
    if case == "mixed-session":
        foreign_source = replace(
            observation.provenance.source,
            capture_session_id="synthetic-round-trip-foreign-capture-session",
        )
        observation = replace(
            observation,
            evidence=replace(
                observation.evidence,
                provenance=replace(observation.provenance, source=foreign_source),
            ),
        )
        return sequencer.observe(session, observation, evaluated_monotonic_s=30.15)
    if case == "unknown":
        observation = replace(
            observation,
            evidence=replace(
                observation.evidence,
                detection=CheckpointDetection(CheckpointMatchKind.UNKNOWN, (), 0.0),
            ),
        )
        return sequencer.observe(session, observation, evaluated_monotonic_s=30.15)
    if case == "ambiguous":
        observation = replace(
            observation,
            evidence=replace(
                observation.evidence,
                detection=CheckpointDetection(
                    CheckpointMatchKind.AMBIGUOUS,
                    (
                        departure_id,
                        context.plan.checkpoints[1].checkpoint_id,
                    ),
                    0.99,
                ),
            ),
        )
        return sequencer.observe(session, observation, evaluated_monotonic_s=30.15)

    sequencer.observe(session, observation, evaluated_monotonic_s=30.15)
    if case == "step-timeout":
        return sequencer.timeout(session, evaluated_monotonic_s=30.2)
    if case == "duplicate-checkpoint":
        return sequencer.observe(
            session,
            _observation(
                context,
                departure_id,
                frame_id=302,
                captured_monotonic_s=30.2,
            ),
            evaluated_monotonic_s=30.25,
        )
    prepared = sequencer.prepare_step(
        session,
        attempt_id=f"{session.session_id}-attempt-1",
        evaluated_monotonic_s=30.2,
    )
    assert prepared.navigation_transition is not None
    proposal = prepared.navigation_transition.step_proposal
    assert proposal is not None
    if case == "attempt-timeout":
        return sequencer.timeout(session, evaluated_monotonic_s=30.8)
    if case == "late-receipt":
        return sequencer.record_attempt(
            session,
            _receipt(proposal, post_attempt_monotonic_s=30.3),
            evaluated_monotonic_s=31.0,
        )
    receipt = _receipt(proposal, post_attempt_monotonic_s=30.3)
    sequencer.record_attempt(
        session,
        receipt,
        evaluated_monotonic_s=30.35,
    )
    if case == "duplicate-attempt-receipt":
        return sequencer.record_attempt(
            session,
            receipt,
            evaluated_monotonic_s=30.4,
        )
    if case == "non-post-receipt-evidence":
        return sequencer.observe(
            session,
            _observation(
                context,
                context.plan.checkpoints[1].checkpoint_id,
                frame_id=302,
                captured_monotonic_s=receipt.post_attempt_monotonic_s,
            ),
            evaluated_monotonic_s=30.4,
        )
    if case == "duplicate-attempt-id":
        sequencer.observe(
            session,
            _observation(
                context,
                context.plan.checkpoints[1].checkpoint_id,
                frame_id=302,
                captured_monotonic_s=30.4,
            ),
            evaluated_monotonic_s=30.45,
        )
        return sequencer.prepare_step(
            session,
            attempt_id=f"{session.session_id}-attempt-1",
            evaluated_monotonic_s=30.5,
        )
    if case == "reordered-checkpoint":
        return sequencer.observe(
            session,
            _observation(
                context,
                departure_id,
                frame_id=302,
                captured_monotonic_s=30.4,
            ),
            evaluated_monotonic_s=30.45,
        )
    if case == "mid-route-skip":
        return sequencer.observe(
            session,
            _observation(
                context,
                context.plan.checkpoints[-1].checkpoint_id,
                frame_id=302,
                captured_monotonic_s=30.4,
            ),
            evaluated_monotonic_s=30.45,
        )
    raise AssertionError(f"unknown stopped-result case: {case}")


@pytest.fixture(scope="module")
def pair(tmp_path_factory: pytest.TempPathFactory) -> _PairEvidence:
    return _build_pair(tmp_path_factory.mktemp("release-decision-pair"))


@pytest.fixture(scope="module")
def endurance_pairs(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[_PairEvidence, _PairEvidence, _PairEvidence]:
    root = tmp_path_factory.mktemp("release-decision-endurance-pairs")
    return (
        _build_pair(root / "pair-1", identity_suffix="endurance-1"),
        _build_pair(root / "pair-2", identity_suffix="endurance-2"),
        _build_pair(root / "pair-3", identity_suffix="endurance-3"),
    )


@pytest.fixture(scope="module")
def endurance_nominal_round_trips(
    endurance_pairs: tuple[_PairEvidence, _PairEvidence, _PairEvidence],
) -> tuple[
    round_trip_module.SyntheticRoundTripRehearsalReport,
    round_trip_module.SyntheticRoundTripRehearsalReport,
]:
    return (
        _completed_round_trip_at(
            endurance_pairs[1],
            traversal_id="fault-recovery",
            outbound_started_monotonic_s=100.0,
        ),
        _completed_round_trip_at(
            endurance_pairs[2],
            traversal_id="post-recovery-cycle",
            outbound_started_monotonic_s=200.0,
        ),
    )


def _status(
    direction: _DirectionEvidence,
    decision: NavigationReleaseDecision,
    requirement: DirectionReleaseRequirement,
) -> ReleaseCheckStatus:
    binding = (
        decision.mine_to_bank
        if direction.route_plan.identity.direction is RouteDirection.MINE_TO_BANK
        else decision.bank_to_mine
    )
    return next(item.status for item in binding.checks if item.requirement is requirement)


def _tree_payloads(root: Path) -> tuple[tuple[str, bool, bytes | None], ...]:
    return tuple(
        (
            path.relative_to(root).as_posix(),
            path.is_dir(),
            path.read_bytes() if path.is_file() else None,
        )
        for path in sorted(root.rglob("*"))
    )


def _imports(tree: ast.AST) -> tuple[set[str], set[str]]:
    modules: set[str] = set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(f"{'.' * node.level}{node.module or ''}")
            names.update(alias.name for alias in node.names)
    return modules, names


def _assert_fixed_false_fields(value: object, expected_keys: set[str]) -> None:
    observed: set[str] = set()

    def walk(item: object) -> None:
        if isinstance(item, dict):
            for key, nested in item.items():
                if key in expected_keys:
                    observed.add(key)
                    assert nested is False, key
                walk(nested)
        elif isinstance(item, list):
            for nested in item:
                walk(nested)

    walk(value)
    assert observed == expected_keys


def test_exact_pair_is_deterministic_bound_and_permanently_denied(
    pair: _PairEvidence,
) -> None:
    first = _evaluate(pair)
    second = _evaluate(pair)

    assert first == second
    assert first.to_json_value() == second.to_json_value()
    assert first.canonical_bytes == second.canonical_bytes
    assert first.content_sha256 == second.content_sha256
    assert json.loads(first.canonical_bytes) == first.to_json_value()
    assert tuple(item.requirement for item in first.mine_to_bank.checks) == tuple(
        DirectionReleaseRequirement
    )
    assert tuple(item.requirement for item in first.pair_checks) == tuple(PairReleaseRequirement)
    assert first.required_target_platform == REQUIRED_NAVIGATION_TARGET_PLATFORM == "win32"
    assert first.required_host_threat_model == REQUIRED_NAVIGATION_HOST_THREAT_MODEL
    assert first.required_namespace_contract == DURABLE_WRITER_NAMESPACE_CONTRACT
    assert first.writer_future_real_evidence_eligible is False
    assert DURABLE_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE is False
    assert first.supported_host_namespace_attested is False
    assert first.real_release_role_satisfied is False
    assert first.release_eligible is False
    assert first.live_navigation_enabled is False
    assert first.world_state_activation_allowed is False
    assert first.controller_activation_allowed is False
    assert first.activation_allowed is False
    assert first.input_authority is False
    direction_denials = (
        DirectionReleaseRequirement.PRODUCTION_NAVIGATION_POLICY_ATTESTATION,
        DirectionReleaseRequirement.REAL_POST_ATTEMPT_CAUSALITY,
        DirectionReleaseRequirement.REAL_ROUTE_EVIDENCE,
        DirectionReleaseRequirement.DOWNSTREAM_ENDPOINT_EVIDENCE,
    )
    pair_denials = (
        PairReleaseRequirement.HOST_NAMESPACE_ATTESTATION,
        PairReleaseRequirement.WRITER_FUTURE_REAL_ELIGIBILITY,
        PairReleaseRequirement.FINAL_RELEASE_DECISION,
    )
    for binding in (first.mine_to_bank, first.bank_to_mine):
        assert (
            tuple(
                item.requirement
                for item in binding.checks
                if item.status is ReleaseCheckStatus.NOT_SATISFIED
            )
            == direction_denials
        )
    assert (
        tuple(
            item.requirement
            for item in first.pair_checks
            if item.status is ReleaseCheckStatus.NOT_SATISFIED
        )
        == pair_denials
    )
    assert first.release_blocker_labels == tuple(
        f"{direction.value}:{requirement.value}"
        for direction in (RouteDirection.MINE_TO_BANK, RouteDirection.BANK_TO_MINE)
        for requirement in direction_denials
    ) + tuple(f"pair:{requirement.value}" for requirement in pair_denials)

    for source, binding in (
        (pair.mine_to_bank, first.mine_to_bank),
        (pair.bank_to_mine, first.bank_to_mine),
    ):
        pins = source.expectation
        assert binding.expectation == pins
        assert binding.route_plan == source.route_plan
        assert binding.direction is pins.direction
        assert binding.reviewer_decision is ReviewerDecisionSummary.APPROVED
        assert binding.evidence_conformance_passed is True
        assert binding.post_attempt_causality.synthetic_causality_conforms is True
        assert binding.post_attempt_causality.max_frame_age_s == 0.5
        assert binding.post_attempt_causality.minimum_confidence == 0.9
        assert binding.post_attempt_causality.max_attempt_receipt_age_s == 0.5
        assert binding.post_attempt_causality.real_post_attempt_causality_satisfied is False
        assert len(binding.post_attempt_causality.steps) == len(source.route_plan.steps)
        assert all(
            item.post_attempt_checkpoint_bound
            and item.next_captured_monotonic_s is not None
            and item.next_captured_monotonic_s > item.post_attempt_monotonic_s
            for item in binding.post_attempt_causality.steps
        )
        assert binding.real_route_evidence_satisfied is False
        assert binding.release_eligible is False
        assert binding.activation_allowed is False
        assert binding.input_authority is False
        assert _status(source, first, DirectionReleaseRequirement.REAL_ROUTE_EVIDENCE) is (
            ReleaseCheckStatus.NOT_SATISFIED
        )
        assert (
            _status(source, first, DirectionReleaseRequirement.REAL_POST_ATTEMPT_CAUSALITY)
            is ReleaseCheckStatus.NOT_SATISFIED
        )

    assert first.mine_to_bank.endpoint.required_downstream_evidence is (
        DownstreamEvidenceRequirement.FRESH_BANK_INTERFACE_OPEN
    )
    assert first.bank_to_mine.endpoint.required_downstream_evidence is (
        DownstreamEvidenceRequirement.FRESH_SUPPORTED_MINING_VIEW
    )
    for endpoint in (first.mine_to_bank.endpoint, first.bank_to_mine.endpoint):
        assert endpoint.synthetic_route_arrival_verified is True
        assert endpoint.fresh_downstream_evidence_present is False
        assert endpoint.bank_interface_open_proven is False
        assert endpoint.supported_mining_view_proven is False
        assert endpoint.downstream_handoff_eligible is False
        assert endpoint.activation_allowed is False
        assert endpoint.input_authority is False

    _assert_fixed_false_fields(
        first.to_json_value(),
        {
            "activation_allowed",
            "attempt_authoritative",
            "automatic_retry_enabled",
            "bank_interface_open_proven",
            "controller_activation_allowed",
            "downstream_handoff_eligible",
            "fresh_downstream_evidence_present",
            "input_authority",
            "live_input_enabled",
            "live_navigation_enabled",
            "movement_success_proven_by_receipt",
            "path_is_evidence_identity",
            "real_post_attempt_causality_satisfied",
            "real_release_role_satisfied",
            "real_route_evidence_satisfied",
            "release_authority",
            "release_eligible",
            "support_attested",
            "supported_host_namespace_attested",
            "supported_mining_view_proven",
            "world_state_activation_allowed",
            "writer_future_real_evidence_eligible",
        },
    )


def test_valid_reviewer_rejection_returns_deterministic_denial(
    tmp_path: Path,
) -> None:
    evidence = _build_pair(tmp_path, bank_to_mine_reject_arrival=True)
    decision = _evaluate(evidence)

    assert decision.bank_to_mine.reviewer_decision is ReviewerDecisionSummary.REJECTED
    assert decision.bank_to_mine.evidence_conformance_passed is False
    assert decision.bank_to_mine.endpoint.synthetic_route_arrival_verified is False
    assert (
        _status(
            evidence.bank_to_mine,
            decision,
            DirectionReleaseRequirement.REVIEWER_DECISION_BOUND,
        )
        is ReleaseCheckStatus.BOUND_OFFLINE
    )
    assert (
        _status(
            evidence.bank_to_mine,
            decision,
            DirectionReleaseRequirement.SYNTHETIC_EVIDENCE_CONFORMANCE,
        )
        is ReleaseCheckStatus.NOT_SATISFIED
    )
    assert (
        _status(
            evidence.bank_to_mine,
            decision,
            DirectionReleaseRequirement.SYNTHETIC_ROUTE_ARRIVAL,
        )
        is ReleaseCheckStatus.NOT_SATISFIED
    )
    assert decision.release_eligible is False


def test_typed_causal_graph_rejects_truncation_reuse_and_provenance_splices(
    pair: _PairEvidence,
) -> None:
    direction = _evaluate(pair).mine_to_bank
    causal = direction.post_attempt_causality
    assert len(causal.steps) >= 2
    first, second = causal.steps[:2]
    factory_token = release_module._FACTORY_TOKEN

    with pytest.raises(RouteEvidenceIntegrityError, match="expected step ids are invalid"):
        replace(
            causal,
            expected_step_ids=(),
            steps=(),
            _factory_token=factory_token,
        )

    truncated = replace(
        causal,
        expected_step_ids=(first.step_id,),
        steps=(first,),
        _factory_token=factory_token,
    )
    with pytest.raises(RouteEvidenceIntegrityError, match="exact offline session result"):
        replace(
            direction,
            post_attempt_causality=truncated,
            _factory_token=factory_token,
        )

    repeated_attempt = replace(
        second,
        attempt_id=first.attempt_id,
        _factory_token=factory_token,
    )
    with pytest.raises(RouteEvidenceIntegrityError, match="attempt ids must be unique"):
        replace(
            causal,
            steps=(first, repeated_attempt, *causal.steps[2:]),
            _factory_token=factory_token,
        )

    disconnected = replace(
        second,
        departure_frame_payload_sha256=_digest("foreign-adjacent-departure"),
        _factory_token=factory_token,
    )
    with pytest.raises(RouteEvidenceIntegrityError, match="checkpoint evidence is disconnected"):
        replace(
            causal,
            steps=(first, disconnected, *causal.steps[2:]),
            _factory_token=factory_token,
        )

    late_prior_receipt = replace(
        first,
        receipt_recorded_monotonic_s=second.prepared_monotonic_s + 0.01,
        _factory_token=factory_token,
    )
    with pytest.raises(RouteEvidenceIntegrityError, match="checkpoint evidence is disconnected"):
        replace(
            causal,
            steps=(late_prior_receipt, second, *causal.steps[2:]),
            _factory_token=factory_token,
        )

    source_unbound_causal_splices = (
        replace(
            causal,
            steps=(
                replace(
                    first,
                    departure_frame_payload_sha256=_digest("foreign-first-departure"),
                    _factory_token=factory_token,
                ),
                *causal.steps[1:],
            ),
            _factory_token=factory_token,
        ),
        replace(
            causal,
            steps=(
                *causal.steps[:-1],
                replace(
                    causal.steps[-1],
                    next_frame_payload_sha256=_digest("foreign-terminal-arrival"),
                    _factory_token=factory_token,
                ),
            ),
            _factory_token=factory_token,
        ),
        replace(
            causal,
            session_result_sha256=_digest("foreign-session-result"),
            _factory_token=factory_token,
        ),
    )
    for causal_splice in source_unbound_causal_splices:
        with pytest.raises(RouteEvidenceIntegrityError, match="exact offline session result"):
            replace(
                direction,
                post_attempt_causality=causal_splice,
                _factory_token=factory_token,
            )


def test_typed_release_graph_rejects_nonbool_endpoint_reviewer_and_matrix_splices(
    pair: _PairEvidence,
) -> None:
    decision = _evaluate(pair)
    direction = decision.mine_to_bank
    causal = direction.post_attempt_causality
    first_step = causal.steps[0]
    factory_token = release_module._FACTORY_TOKEN

    with pytest.raises(RouteEvidenceIntegrityError, match="exact boolean"):
        replace(
            first_step,
            post_attempt_checkpoint_bound=1,
            _factory_token=factory_token,
        )
    with pytest.raises(RouteEvidenceIntegrityError, match="exact booleans"):
        replace(
            causal,
            explicit_terminal_arrival_bound=1,
            _factory_token=factory_token,
        )
    with pytest.raises(RouteEvidenceIntegrityError, match="exact boolean"):
        replace(
            direction.endpoint,
            synthetic_route_arrival_verified=1,
            _factory_token=factory_token,
        )

    foreign_endpoint = replace(
        direction.endpoint,
        terminal=RouteEndpoint(
            "synthetic-foreign-bank",
            direction.endpoint.terminal.role,
        ),
        _factory_token=factory_token,
    )
    with pytest.raises(RouteEvidenceIntegrityError, match="endpoint evidence binding differs"):
        replace(direction, endpoint=foreign_endpoint, _factory_token=factory_token)

    with pytest.raises(RouteEvidenceIntegrityError, match="reviewer cases are invalid"):
        replace(
            direction,
            reviewer_cases=(),
            reviewer_decision=ReviewerDecisionSummary.APPROVED,
            _factory_token=factory_token,
        )
    foreign_case = replace(
        direction.reviewer_cases[0],
        candidate_checkpoint_ids=("synthetic-foreign-checkpoint",),
        _factory_token=factory_token,
    )
    with pytest.raises(RouteEvidenceIntegrityError, match="foreign checkpoint"):
        replace(
            direction,
            reviewer_cases=(foreign_case, *direction.reviewer_cases[1:]),
            _factory_token=factory_token,
        )

    approved_case_splices = (
        replace(
            direction.reviewer_cases[0],
            case_id="synthetic-foreign-review-case",
            _factory_token=factory_token,
        ),
        replace(
            direction.reviewer_cases[0],
            frame_sha256=_digest("foreign-reviewed-frame"),
            _factory_token=factory_token,
        ),
        replace(
            direction.reviewer_cases[0],
            detector_report_sha256=_digest("foreign-reviewed-detector-report"),
            _factory_token=factory_token,
        ),
    )
    for approved_case_splice in approved_case_splices:
        with pytest.raises(RouteEvidenceIntegrityError, match="pinned reviewer truth"):
            replace(
                direction,
                reviewer_cases=(approved_case_splice, *direction.reviewer_cases[1:]),
                _factory_token=factory_token,
            )
    with pytest.raises(RouteEvidenceIntegrityError, match="pinned reviewer truth"):
        replace(
            direction,
            reviewer_cases=direction.reviewer_cases[:-1],
            _factory_token=factory_token,
        )

    unverified_endpoint = replace(
        direction.endpoint,
        synthetic_route_arrival_verified=False,
        _factory_token=factory_token,
    )
    unverified_arrival_checks = release_module._direction_checks(
        direction.direction,
        direction.evidence_conformance_passed,
        False,
        causal.synthetic_causality_conforms,
    )
    with pytest.raises(RouteEvidenceIntegrityError, match="strict verification report"):
        replace(
            direction,
            endpoint=unverified_endpoint,
            checks=unverified_arrival_checks,
            _factory_token=factory_token,
        )

    rejected_case = replace(
        direction.reviewer_cases[0],
        decision=RouteEvidenceReviewDecision.REJECTED,
        _factory_token=factory_token,
    )
    with pytest.raises(RouteEvidenceIntegrityError, match="pinned reviewer truth"):
        replace(
            direction,
            reviewer_cases=(rejected_case, *direction.reviewer_cases[1:]),
            reviewer_decision=ReviewerDecisionSummary.REJECTED,
            _factory_token=factory_token,
        )

    with pytest.raises(RouteEvidenceIntegrityError, match="strict durable intake"):
        replace(
            direction,
            operator_id="synthetic-foreign-operator",
            _factory_token=factory_token,
        )
    forged_acquisition_root = replace(
        direction.acquisition_root,
        stable_tree_identity_sha256=_digest("foreign-tree-identity"),
        _factory_token=factory_token,
    )
    with pytest.raises(RouteEvidenceIntegrityError, match="strict intake identities"):
        replace(
            direction,
            acquisition_root=forged_acquisition_root,
            _factory_token=factory_token,
        )
    root = direction.acquisition_root

    class _AuthoritySerializingRoot(release_module.DurableRootBinding):
        def to_json_value(self) -> dict[str, object]:
            value = super().to_json_value()
            value["release_authority"] = True
            return value

    authority_serializing_root = _AuthoritySerializingRoot(
        direction=root.direction,
        role=root.role,
        storage_path=root.storage_path,
        physical_root_identity=root.physical_root_identity,
        stable_tree_identity_sha256=root.stable_tree_identity_sha256,
        _factory_token=factory_token,
    )
    with pytest.raises(RouteEvidenceIntegrityError, match="root bindings are invalid"):
        replace(
            direction,
            acquisition_root=authority_serializing_root,
            _factory_token=factory_token,
        )

    class _AuthoritySerializingCausality(release_module.PostAttemptCausalityBinding):
        def __eq__(self, other: object) -> bool:
            del other
            return True

        def to_json_value(self) -> dict[str, object]:
            value = super().to_json_value()
            value["release_authority"] = True
            return value

    causal_arguments = {
        item.name: getattr(causal, item.name) for item in fields(causal) if item.init
    }
    authority_serializing_causality = _AuthoritySerializingCausality(
        **causal_arguments,
        _factory_token=factory_token,
    )
    with pytest.raises(RouteEvidenceIntegrityError, match="causality is invalid"):
        replace(
            direction,
            post_attempt_causality=authority_serializing_causality,
            _factory_token=factory_token,
        )

    class _AuthoritySerializingEndpoint(release_module.EndpointReleaseBinding):
        def __eq__(self, other: object) -> bool:
            del other
            return True

        def to_json_value(self) -> dict[str, object]:
            value = super().to_json_value()
            value["activation_allowed"] = True
            return value

    endpoint_arguments = {
        item.name: getattr(direction.endpoint, item.name)
        for item in fields(direction.endpoint)
        if item.init
    }
    authority_serializing_endpoint = _AuthoritySerializingEndpoint(
        **endpoint_arguments,
        _factory_token=factory_token,
    )
    with pytest.raises(RouteEvidenceIntegrityError, match="endpoint binding is invalid"):
        replace(
            direction,
            endpoint=authority_serializing_endpoint,
            _factory_token=factory_token,
        )

    class _AuthoritySerializingDirectionCheck(release_module.DirectionReleaseCheck):
        def __eq__(self, other: object) -> bool:
            del other
            return True

        def to_json_value(self) -> dict[str, object]:
            value = super().to_json_value()
            value["release_authority"] = True
            return value

    first_direction_check = direction.checks[0]
    authority_serializing_direction_check = _AuthoritySerializingDirectionCheck(
        direction=first_direction_check.direction,
        requirement=first_direction_check.requirement,
        status=first_direction_check.status,
        _factory_token=factory_token,
    )
    with pytest.raises(RouteEvidenceIntegrityError, match="matrix types are invalid"):
        replace(
            direction,
            checks=(authority_serializing_direction_check, *direction.checks[1:]),
            _factory_token=factory_token,
        )

    class _AuthoritySerializingPairCheck(release_module.PairReleaseCheck):
        def __eq__(self, other: object) -> bool:
            del other
            return True

        def to_json_value(self) -> dict[str, object]:
            value = super().to_json_value()
            value["release_authority"] = True
            return value

    first_pair_check = decision.pair_checks[0]
    authority_serializing_pair_check = _AuthoritySerializingPairCheck(
        requirement=first_pair_check.requirement,
        status=first_pair_check.status,
        _factory_token=factory_token,
    )
    with pytest.raises(RouteEvidenceIntegrityError, match="matrix types are invalid"):
        replace(
            decision,
            pair_checks=(authority_serializing_pair_check, *decision.pair_checks[1:]),
            _factory_token=factory_token,
        )
    with pytest.raises(RouteEvidenceIntegrityError, match="strict durable intake"):
        replace(
            direction,
            evidence_conformance_passed=False,
            endpoint=unverified_endpoint,
            checks=unverified_arrival_checks,
            _factory_token=factory_token,
        )

    policy_check_index = next(
        index
        for index, item in enumerate(direction.checks)
        if item.requirement is DirectionReleaseRequirement.PRODUCTION_NAVIGATION_POLICY_ATTESTATION
    )
    forged_direction_check = replace(
        direction.checks[policy_check_index],
        status=ReleaseCheckStatus.BOUND_OFFLINE,
        _factory_token=factory_token,
    )
    forged_direction_checks = list(direction.checks)
    forged_direction_checks[policy_check_index] = forged_direction_check
    with pytest.raises(RouteEvidenceIntegrityError, match="matrix differs"):
        replace(
            direction,
            checks=tuple(forged_direction_checks),
            _factory_token=factory_token,
        )

    forged_pair_check = replace(
        decision.pair_checks[-1],
        status=ReleaseCheckStatus.BOUND_OFFLINE,
        _factory_token=factory_token,
    )
    with pytest.raises(RouteEvidenceIntegrityError, match="matrix differs"):
        replace(
            decision,
            pair_checks=(*decision.pair_checks[:-1], forged_pair_check),
            _factory_token=factory_token,
        )


@pytest.mark.parametrize("case", ("interrupted", "equal-boundary"))
def test_incomplete_or_nonfresh_post_attempt_chain_is_retained_as_denial(
    pair: _PairEvidence,
    case: str,
) -> None:
    context = pair.mine_to_bank.result.progress.session.context
    result = (
        _interrupted_result(context) if case == "interrupted" else _equal_boundary_result(context)
    )
    decision = _evaluate(
        pair,
        mine_to_bank_post_attempt_expectation=_causality_expectation(
            pair.mine_to_bank.expectation.route_plan_sha256,
            result,
        ),
        mine_to_bank_post_attempt_result=result,
    )
    binding = decision.mine_to_bank.post_attempt_causality

    assert binding.synthetic_causality_conforms is False
    assert binding.real_post_attempt_causality_satisfied is False
    assert (
        _status(
            pair.mine_to_bank,
            decision,
            DirectionReleaseRequirement.OFFLINE_POST_ATTEMPT_CAUSALITY,
        )
        is ReleaseCheckStatus.NOT_SATISFIED
    )
    if case == "interrupted":
        assert binding.failure_reason is NavigationFailureReason.SESSION_INTERRUPTED.value
        assert binding.steps == ()
    else:
        assert binding.failure_reason is NavigationFailureReason.EVIDENCE_NOT_AFTER_BOUNDARY.value
        assert len(binding.steps) == 1
        assert binding.steps[0].post_attempt_checkpoint_bound is False
    assert decision.release_eligible is False


def test_direction_result_or_slot_cannot_cross_satisfy(pair: _PairEvidence) -> None:
    with pytest.raises(RouteEvidenceIntegrityError, match="post-attempt result differs"):
        _evaluate(
            pair,
            mine_to_bank_post_attempt_result=pair.bank_to_mine.result,
        )

    with pytest.raises(RouteEvidenceIntegrityError, match="wrong direction"):
        _evaluate(
            pair,
            mine_to_bank_expectation=pair.bank_to_mine.expectation,
            bank_to_mine_expectation=pair.mine_to_bank.expectation,
        )


def test_source_session_or_route_plan_drift_fails_closed(pair: _PairEvidence) -> None:
    original_context = pair.mine_to_bank.result.progress.session.context
    foreign_source_context = replace(
        original_context,
        expected_source=replace(
            original_context.expected_source,
            capture_session_id="synthetic-foreign-capture-session",
        ),
    )
    foreign_result = _interrupted_result(foreign_source_context)
    with pytest.raises(RouteEvidenceIntegrityError, match="route/source binding"):
        _evaluate(pair, mine_to_bank_post_attempt_result=foreign_result)

    permissive_context = replace(
        original_context,
        policy=NavigationPolicy(
            max_frame_age_s=60.0,
            minimum_confidence=0.01,
            max_attempt_receipt_age_s=60.0,
        ),
    )
    permissive_result = _complete_result(permissive_context)
    with pytest.raises(RouteEvidenceIntegrityError, match="route/source binding"):
        _evaluate(pair, mine_to_bank_post_attempt_result=permissive_result)

    other = pair.bank_to_mine.result.progress.session.context
    foreign_plan_context = replace(original_context, plan=other.plan)
    foreign_plan_session = OfflineRouteSession(
        "synthetic-foreign-plan-session",
        foreign_plan_context,
        other.plan.identity.direction,
    )
    foreign_plan_result = OfflineRouteSessionSequencer.begin(
        foreign_plan_session,
        started_monotonic_s=50.0,
    ).interrupt(foreign_plan_session, evaluated_monotonic_s=50.1)
    with pytest.raises(RouteEvidenceIntegrityError, match="route/source binding"):
        _evaluate(pair, mine_to_bank_post_attempt_result=foreign_plan_result)


@pytest.mark.parametrize(
    ("field_name", "error_label"),
    (
        ("campaign_id", "campaign id"),
        ("capture_session_id", "capture session"),
        ("route_plan_sha256", "route plan"),
        ("finalized_package_sha256", "finalized package"),
        ("acquisition_head_sha256", "acquisition head"),
        ("acquisition_journal_head_sha256", "acquisition journal"),
        ("acquisition_finalization_sha256", "acquisition finalization"),
        ("review_id", "review id"),
        ("independent_review_sha256", "independent review"),
        ("review_plan_sha256", "review plan"),
        ("review_journal_head_sha256", "review journal"),
        ("review_finalization_sha256", "review finalization"),
    ),
)
def test_every_direction_lineage_pin_must_be_independent(
    pair: _PairEvidence,
    field_name: str,
    error_label: str,
) -> None:
    duplicate_lineage = replace(
        pair.bank_to_mine.expectation,
        **{field_name: getattr(pair.mine_to_bank.expectation, field_name)},
    )
    with pytest.raises(RouteEvidenceIntegrityError, match=rf"{error_label} lineage"):
        _evaluate(pair, bank_to_mine_expectation=duplicate_lineage)


def test_every_transaction_root_must_be_independent(pair: _PairEvidence) -> None:

    roots = (
        pair.mine_to_bank.acquisition_root,
        pair.mine_to_bank.review_root,
        pair.bank_to_mine.acquisition_root,
        pair.bank_to_mine.review_root,
    )
    override_names = (
        "mine_to_bank_acquisition_root",
        "mine_to_bank_review_root",
        "bank_to_mine_acquisition_root",
        "bank_to_mine_review_root",
    )
    for first in range(len(roots)):
        for second in range(first + 1, len(roots)):
            with pytest.raises(RouteEvidenceIntegrityError, match="physically disjoint"):
                _evaluate(pair, **{override_names[second]: roots[first]})


def test_asymmetric_endpoint_contract_is_rejected(tmp_path: Path) -> None:
    evidence = _build_pair(
        tmp_path,
        bank_to_mine_mine_location_id="synthetic-other-mine",
    )
    with pytest.raises(RouteEvidenceIntegrityError, match="endpoint contracts"):
        _evaluate(evidence)


def test_same_opaque_route_name_version_and_reviewer_are_allowed(tmp_path: Path) -> None:
    evidence = _build_pair(
        tmp_path,
        shared_route_id="synthetic-shared-route-name",
        shared_route_version="synthetic-shared-version",
        shared_reviewer_id="synthetic-shared-independent-reviewer",
    )
    decision = _evaluate(evidence)

    assert decision.mine_to_bank.route_plan.identity.route_id == (
        decision.bank_to_mine.route_plan.identity.route_id
    )
    assert decision.mine_to_bank.route_plan.identity.version == (
        decision.bank_to_mine.route_plan.identity.version
    )
    assert decision.mine_to_bank.route_plan.identity.direction is RouteDirection.MINE_TO_BANK
    assert decision.bank_to_mine.route_plan.identity.direction is RouteDirection.BANK_TO_MINE
    assert decision.mine_to_bank.expectation.route_plan_sha256 != (
        decision.bank_to_mine.expectation.route_plan_sha256
    )
    assert decision.mine_to_bank.reviewer_id == decision.bank_to_mine.reviewer_id
    assert decision.release_eligible is False


@dataclass(slots=True)
class _AlternatingPath:
    first: Path
    second: Path
    calls: int = 0

    def __fspath__(self) -> str:
        value = self.first if self.calls == 0 else self.second
        self.calls += 1
        return str(value)


def test_each_stateful_pathlike_is_evaluated_once(pair: _PairEvidence) -> None:
    paths = (
        _AlternatingPath(pair.mine_to_bank.acquisition_root, pair.bank_to_mine.acquisition_root),
        _AlternatingPath(pair.mine_to_bank.review_root, pair.bank_to_mine.review_root),
        _AlternatingPath(pair.bank_to_mine.acquisition_root, pair.mine_to_bank.acquisition_root),
        _AlternatingPath(pair.bank_to_mine.review_root, pair.mine_to_bank.review_root),
    )
    decision = _evaluate(
        pair,
        mine_to_bank_acquisition_root=paths[0],
        mine_to_bank_review_root=paths[1],
        bank_to_mine_acquisition_root=paths[2],
        bank_to_mine_review_root=paths[3],
    )

    assert decision.release_eligible is False
    assert tuple(item.calls for item in paths) == (1, 1, 1, 1)


def test_repeated_pair_intake_rejects_metadata_change_between_snapshots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = _build_pair(tmp_path)
    original_loader = release_module._load_and_verify_durable_synthetic_route_evidence_detailed
    call_count = 0

    def racing_loader(
        acquisition_root: str | os.PathLike[str],
        review_root: str | os.PathLike[str],
        expectation: DurableRouteEvidenceFilesystemExpectation,
    ) -> object:
        nonlocal call_count
        call_count += 1
        if call_count == 3:
            plan_path = evidence.mine_to_bank.acquisition_root / ACQUISITION_PLAN_FILENAME
            current = plan_path.stat()
            os.utime(
                plan_path,
                ns=(current.st_atime_ns, current.st_mtime_ns + 10_000_000),
            )
        return original_loader(acquisition_root, review_root, expectation)

    monkeypatch.setattr(
        release_module,
        "_load_and_verify_durable_synthetic_route_evidence_detailed",
        racing_loader,
    )
    with pytest.raises(
        RouteEvidenceIntegrityError,
        match="changed between repeated intake snapshots",
    ):
        _evaluate(evidence)
    assert call_count == 4


def test_evaluator_is_read_only_and_returned_mutation_cannot_change_fresh_result(
    pair: _PairEvidence,
) -> None:
    roots = (
        pair.mine_to_bank.acquisition_root,
        pair.mine_to_bank.review_root,
        pair.bank_to_mine.acquisition_root,
        pair.bank_to_mine.review_root,
    )
    before = tuple(_tree_payloads(root) for root in roots)
    first = _evaluate(pair)
    object.__setattr__(first.mine_to_bank.expectation, "activation_allowed", True)
    second = _evaluate(pair)
    after = tuple(_tree_payloads(root) for root in roots)

    assert before == after
    assert second.mine_to_bank.expectation.activation_allowed is False
    assert second.release_eligible is False
    with pytest.raises(RouteEvidenceIntegrityError, match="evaluator-owned"):
        replace(second)


def test_mutated_fixed_authority_input_is_rejected(pair: _PairEvidence) -> None:
    expectation = replace(pair.mine_to_bank.expectation)
    object.__setattr__(expectation, "input_authority", True)
    with pytest.raises(RouteEvidenceIntegrityError, match="expectation is malformed"):
        _evaluate(pair, mine_to_bank_expectation=expectation)


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("activation_allowed", True),
        ("input_authority", True),
        ("evidence_role", "foreign-route-evidence-role"),
    ),
)
def test_mutated_fixed_authority_causal_expectation_is_rejected(
    pair: _PairEvidence,
    field_name: str,
    value: object,
) -> None:
    expectation = replace(pair.mine_to_bank.causality_expectation)
    object.__setattr__(expectation, field_name, value)
    with pytest.raises(RouteEvidenceIntegrityError, match="post-attempt expectation is malformed"):
        _evaluate(pair, mine_to_bank_post_attempt_expectation=expectation)

    direction = _evaluate(pair).mine_to_bank
    with pytest.raises(RouteEvidenceIntegrityError, match="post-attempt expectation differs"):
        replace(
            direction,
            post_attempt_expectation=expectation,
            _factory_token=release_module._FACTORY_TOKEN,
        )

    object.__setattr__(direction.expectation, field_name, value)
    with pytest.raises(RouteEvidenceIntegrityError, match="strict durable intake"):
        replace(direction, _factory_token=release_module._FACTORY_TOKEN)


def test_exact_round_trip_rehearsal_is_deterministic_ordered_and_narrow(
    pair: _PairEvidence,
) -> None:
    return_result = _sequential_return_result(pair)
    sequential_pair = _pair_with_result(pair, RouteDirection.BANK_TO_MINE, return_result)
    decision = _evaluate(sequential_pair)

    first = _round_trip(
        decision,
        sequential_pair.mine_to_bank.result,
        return_result,
    )
    second = _round_trip(
        decision,
        sequential_pair.mine_to_bank.result,
        return_result,
    )

    assert first == second
    assert first.phase is SyntheticRoundTripPhase.COMPLETED
    assert first.stop_reason is None
    assert first.evaluated_leg_order == (
        RouteDirection.MINE_TO_BANK,
        RouteDirection.BANK_TO_MINE,
    )
    assert first.to_json_value() == second.to_json_value()
    assert first.canonical_bytes == second.canonical_bytes
    assert first.content_sha256 == second.content_sha256
    assert json.loads(first.canonical_bytes) == first.to_json_value()
    assert first.release_decision_sha256 == decision.content_sha256
    assert first.bank_handoff is not None
    assert first.bank_handoff.endpoint == decision.mine_to_bank.route_plan.destination
    assert first.bank_handoff.endpoint == decision.bank_to_mine.route_plan.origin
    assert first.bank_handoff.arrival_checkpoint_id != first.bank_handoff.departure_checkpoint_id
    assert (
        first.bank_handoff.departure_captured_monotonic_s
        > first.bank_handoff.arrival_accepted_monotonic_s
    )
    assert first.bank_handoff.bank_interface_open_proven is False
    assert first.bank_handoff.downstream_handoff_eligible is False
    assert first.mine_arrival is not None
    assert first.mine_arrival.endpoint == decision.bank_to_mine.route_plan.destination
    assert first.mine_arrival.endpoint == decision.mine_to_bank.route_plan.origin
    assert first.mine_arrival.explicit_route_arrival_bound is True
    assert first.mine_arrival.supported_mining_view_proven is False
    assert first.mine_arrival.downstream_handoff_eligible is False
    assert first.retry_count == 0
    assert first.automatic_retry_enabled is False
    assert first.release_eligible is False
    assert first.live_navigation_enabled is False
    assert first.world_state_activation_allowed is False
    assert first.controller_activation_allowed is False
    assert first.activation_allowed is False
    assert first.input_authority is False
    _assert_fixed_false_fields(
        first.to_json_value(),
        {
            "activation_allowed",
            "automatic_retry_enabled",
            "bank_interface_open_proven",
            "controller_activation_allowed",
            "downstream_handoff_eligible",
            "input_authority",
            "live_navigation_enabled",
            "release_eligible",
            "supported_mining_view_proven",
            "world_state_activation_allowed",
        },
    )


@pytest.mark.parametrize("boundary_delta", (-0.01, 0.0))
def test_two_independent_arrivals_do_not_form_a_round_trip_without_fresh_handoff(
    pair: _PairEvidence,
    boundary_delta: float,
) -> None:
    accepted_arrival = pair.mine_to_bank.result.progress.last_event_monotonic_s
    return_result = _sequential_return_result(
        pair,
        first_frame_monotonic_s=accepted_arrival + boundary_delta,
        session_suffix=f"nonfresh-return-{boundary_delta}",
    )
    assert return_result.progress.phase is OfflineRouteSessionPhase.ARRIVED
    evidence = _pair_with_result(pair, RouteDirection.BANK_TO_MINE, return_result)
    decision = _evaluate(evidence)

    report = _round_trip(
        decision,
        evidence.mine_to_bank.result,
        return_result,
    )

    assert report.phase is SyntheticRoundTripPhase.STOPPED
    assert report.stop_reason is (SyntheticRoundTripStopReason.BANK_TO_MINE_DEPARTURE_NOT_FRESH)
    assert report.mine_to_bank.synthetic_causality_conforms is True
    assert report.bank_to_mine is not None
    assert report.bank_to_mine.synthetic_causality_conforms is True
    assert report.bank_handoff is None
    assert report.mine_arrival is None
    assert report.automatic_retry_enabled is False


def test_crossed_foreign_or_cross_slotted_round_trip_results_have_no_report(
    pair: _PairEvidence,
) -> None:
    return_result = _sequential_return_result(pair)
    evidence = _pair_with_result(pair, RouteDirection.BANK_TO_MINE, return_result)
    decision = _evaluate(evidence)

    with pytest.raises(RouteEvidenceIntegrityError, match="malformed or cross-bound"):
        evaluate_synthetic_round_trip_rehearsal(
            decision,
            timeline_expectation=_timeline_expectation(
                decision,
                return_result,
                evidence.mine_to_bank.result,
            ),
            mine_to_bank_result=return_result,
            bank_to_mine_result=evidence.mine_to_bank.result,
        )

    foreign_outbound = _complete_result(
        evidence.mine_to_bank.result.progress.session.context,
        started_monotonic_s=40.0,
        session_suffix="foreign-outbound-session",
    )
    with pytest.raises(RouteEvidenceIntegrityError, match="malformed or cross-bound"):
        evaluate_synthetic_round_trip_rehearsal(
            decision,
            timeline_expectation=_timeline_expectation(
                decision,
                foreign_outbound,
                return_result,
            ),
            mine_to_bank_result=foreign_outbound,
            bank_to_mine_result=return_result,
        )

    crossed_decision = _evaluate(evidence)
    object.__setattr__(
        crossed_decision,
        "mine_to_bank",
        crossed_decision.bank_to_mine,
    )
    with pytest.raises(RouteEvidenceIntegrityError, match="decision is malformed"):
        evaluate_synthetic_round_trip_rehearsal(
            crossed_decision,
            timeline_expectation=_timeline_expectation(
                decision,
                evidence.mine_to_bank.result,
                return_result,
            ),
            mine_to_bank_result=evidence.mine_to_bank.result,
            bank_to_mine_result=return_result,
        )


@pytest.mark.parametrize(
    ("case", "failure_reason"),
    (
        ("wrong-direction", NavigationFailureReason.DIRECTION_MISMATCH),
        ("wrong-version", NavigationFailureReason.ROUTE_VERSION_MISMATCH),
        ("wrong-checkpoint", NavigationFailureReason.SKIPPED_CHECKPOINT),
        ("mid-route-skip", NavigationFailureReason.SKIPPED_CHECKPOINT),
        ("duplicate-checkpoint", NavigationFailureReason.STEP_EVIDENCE_NOT_CONSUMED),
        ("reordered-checkpoint", NavigationFailureReason.OUT_OF_ORDER_CHECKPOINT),
        ("stale", NavigationFailureReason.STALE_FRAME),
        ("mixed-session", NavigationFailureReason.PROVENANCE_MISMATCH),
        ("late-receipt", NavigationFailureReason.STALE_ATTEMPT_RECEIPT),
    ),
)
def test_exact_failed_checkpoint_or_late_outcome_reason_is_retained_and_stops(
    pair: _PairEvidence,
    case: str,
    failure_reason: NavigationFailureReason,
) -> None:
    result = _stopped_result(pair.mine_to_bank.result.progress.session.context, case)
    assert result.progress.phase is OfflineRouteSessionPhase.STOPPED
    assert result.progress.navigation.failure_reason is failure_reason
    evidence = _pair_with_result(pair, RouteDirection.MINE_TO_BANK, result)
    decision = _evaluate(evidence)

    report = _round_trip(
        decision,
        result,
        evidence.bank_to_mine.result,
    )

    assert report.phase is SyntheticRoundTripPhase.STOPPED
    assert report.stop_reason is SyntheticRoundTripStopReason.MINE_TO_BANK_NOT_ARRIVED
    assert report.mine_to_bank.terminal_phase is OfflineRouteSessionPhase.STOPPED
    assert report.mine_to_bank.session_stop_reason is not None
    assert report.mine_to_bank.navigation_failure_reason is failure_reason
    assert report.bank_to_mine is None
    assert report.evaluated_leg_order == (RouteDirection.MINE_TO_BANK,)
    assert report.bank_handoff is None
    assert report.mine_arrival is None
    assert report.automatic_retry_enabled is False


def test_interrupted_leg_stops_without_consuming_or_resurrecting_the_other_leg(
    pair: _PairEvidence,
) -> None:
    interrupted_outbound = _interrupted_result(pair.mine_to_bank.result.progress.session.context)
    outbound_evidence = _pair_with_result(
        pair,
        RouteDirection.MINE_TO_BANK,
        interrupted_outbound,
    )
    outbound_decision = _evaluate(outbound_evidence)
    outbound_report = _round_trip(
        outbound_decision,
        interrupted_outbound,
        outbound_evidence.bank_to_mine.result,
    )
    assert outbound_report.stop_reason is (SyntheticRoundTripStopReason.MINE_TO_BANK_NOT_ARRIVED)
    assert outbound_report.mine_to_bank.session_stop_reason is (
        OfflineRouteSessionStopReason.INTERRUPTED
    )
    assert outbound_report.bank_to_mine is None

    interrupted_return = _interrupted_result(pair.bank_to_mine.result.progress.session.context)
    return_evidence = _pair_with_result(
        pair,
        RouteDirection.BANK_TO_MINE,
        interrupted_return,
    )
    return_decision = _evaluate(return_evidence)
    return_report = _round_trip(
        return_decision,
        return_evidence.mine_to_bank.result,
        interrupted_return,
    )
    assert return_report.stop_reason is SyntheticRoundTripStopReason.BANK_TO_MINE_NOT_ARRIVED
    assert return_report.bank_to_mine is not None
    assert return_report.bank_to_mine.session_stop_reason is (
        OfflineRouteSessionStopReason.INTERRUPTED
    )
    assert return_report.bank_handoff is None
    assert return_report.mine_arrival is None


def test_stopped_round_trip_has_no_retry_surface_and_requires_a_fresh_b1_decision(
    pair: _PairEvidence,
) -> None:
    interrupted = _interrupted_result(pair.mine_to_bank.result.progress.session.context)
    evidence = _pair_with_result(pair, RouteDirection.MINE_TO_BANK, interrupted)
    decision = _evaluate(evidence)
    report = _round_trip(
        decision,
        interrupted,
        evidence.bank_to_mine.result,
    )

    assert report.phase is SyntheticRoundTripPhase.STOPPED
    assert report.retry_count == 0
    assert report.automatic_retry_enabled is False
    assert {"retry", "restart", "resume"}.isdisjoint(dir(report))
    assert {"retry", "restart", "resume"}.isdisjoint(round_trip_module.__all__)
    with pytest.raises(RouteEvidenceIntegrityError, match="malformed or cross-bound"):
        evaluate_synthetic_round_trip_rehearsal(
            decision,
            timeline_expectation=_timeline_expectation(
                decision,
                pair.mine_to_bank.result,
                evidence.bank_to_mine.result,
            ),
            mine_to_bank_result=pair.mine_to_bank.result,
            bank_to_mine_result=evidence.bank_to_mine.result,
        )


@pytest.mark.parametrize(
    ("rejected_direction", "expected_reason", "invalid_relabel"),
    (
        (
            RouteDirection.MINE_TO_BANK,
            SyntheticRoundTripStopReason.MINE_TO_BANK_EVIDENCE_NOT_APPROVED,
            SyntheticRoundTripStopReason.MINE_TO_BANK_NOT_ARRIVED,
        ),
        (
            RouteDirection.BANK_TO_MINE,
            SyntheticRoundTripStopReason.BANK_TO_MINE_EVIDENCE_NOT_APPROVED,
            SyntheticRoundTripStopReason.BANK_TO_MINE_NOT_ARRIVED,
        ),
    ),
)
def test_rejected_durable_evidence_stops_before_causality_and_cannot_be_relabelled(
    tmp_path: Path,
    rejected_direction: RouteDirection,
    expected_reason: SyntheticRoundTripStopReason,
    invalid_relabel: SyntheticRoundTripStopReason,
) -> None:
    evidence = _build_pair(
        tmp_path / rejected_direction.value,
        mine_to_bank_reject_arrival=(rejected_direction is RouteDirection.MINE_TO_BANK),
        bank_to_mine_reject_arrival=(rejected_direction is RouteDirection.BANK_TO_MINE),
    )
    rejected_source = (
        evidence.mine_to_bank
        if rejected_direction is RouteDirection.MINE_TO_BANK
        else evidence.bank_to_mine
    )
    rejected_result = _interrupted_result(rejected_source.result.progress.session.context)
    evidence = _pair_with_result(evidence, rejected_direction, rejected_result)
    decision = _evaluate(evidence)
    report = _round_trip(
        decision,
        evidence.mine_to_bank.result,
        evidence.bank_to_mine.result,
    )

    assert report.phase is SyntheticRoundTripPhase.STOPPED
    assert report.stop_reason is expected_reason
    if rejected_direction is RouteDirection.MINE_TO_BANK:
        assert report.mine_to_bank.durable_evidence_accepted is False
        assert report.mine_to_bank.synthetic_causality_conforms is False
        assert report.bank_to_mine is None
    else:
        assert report.mine_to_bank.accepted_for_round_trip is True
        assert report.bank_to_mine is not None
        assert report.bank_to_mine.durable_evidence_accepted is False
        assert report.bank_to_mine.synthetic_causality_conforms is False
    assert report.bank_handoff is None
    assert report.mine_arrival is None
    with pytest.raises(RouteEvidenceIntegrityError):
        replace(
            report,
            stop_reason=invalid_relabel,
            _factory_token=round_trip_module._FACTORY_TOKEN,
        )


def test_round_trip_timeline_pins_both_exact_results_and_has_no_authority(
    pair: _PairEvidence,
) -> None:
    evidence, decision, return_result, _ = _completed_round_trip(pair)
    timeline = _timeline_expectation(
        decision,
        evidence.mine_to_bank.result,
        return_result,
    )
    mismatches = (
        replace(timeline, release_decision_sha256=_digest("foreign-release-decision")),
        replace(
            timeline,
            mine_to_bank_route_session_id="synthetic-foreign-outbound-route-session",
        ),
        replace(
            timeline,
            mine_to_bank_session_result_sha256=_digest("foreign-outbound-result"),
        ),
        replace(
            timeline,
            bank_to_mine_route_session_id="synthetic-foreign-return-route-session",
        ),
        replace(
            timeline,
            bank_to_mine_session_result_sha256=_digest("foreign-return-result"),
        ),
        replace(
            timeline,
            mine_to_bank_route_session_id=timeline.bank_to_mine_route_session_id,
            bank_to_mine_route_session_id=timeline.mine_to_bank_route_session_id,
        ),
    )
    for mismatch in mismatches:
        with pytest.raises(RouteEvidenceIntegrityError, match="timeline differs"):
            evaluate_synthetic_round_trip_rehearsal(
                decision,
                timeline_expectation=mismatch,
                mine_to_bank_result=evidence.mine_to_bank.result,
                bank_to_mine_result=return_result,
            )

    with pytest.raises(RouteEvidenceIntegrityError, match="distinct route sessions"):
        replace(
            timeline,
            bank_to_mine_route_session_id=timeline.mine_to_bank_route_session_id,
        )

    object.__setattr__(timeline, "release_authority", True)
    with pytest.raises(RouteEvidenceIntegrityError, match="timeline expectation is malformed"):
        evaluate_synthetic_round_trip_rehearsal(
            decision,
            timeline_expectation=timeline,
            mine_to_bank_result=evidence.mine_to_bank.result,
            bank_to_mine_result=return_result,
        )


def test_mutated_returned_timeline_cannot_be_serialized(
    pair: _PairEvidence,
) -> None:
    _, _, _, report = _completed_round_trip(pair)

    object.__setattr__(report.timeline, "timeline_id", "mutated-after-evaluation")

    with pytest.raises(RouteEvidenceIntegrityError, match="timeline differs from its source"):
        report.to_json_value()


@pytest.mark.parametrize("root_name", ("acquisition_root", "review_root"))
def test_round_trip_rejects_post_construction_b1_storage_path_reauthoring(
    pair: _PairEvidence,
    tmp_path: Path,
    root_name: str,
) -> None:
    return_result = _sequential_return_result(pair)
    evidence = _pair_with_result(pair, RouteDirection.BANK_TO_MINE, return_result)
    decision = _evaluate(evidence)
    root = getattr(decision.mine_to_bank, root_name)
    object.__setattr__(
        root,
        "storage_path",
        str((tmp_path / f"never-evaluated-{root_name}").resolve()),
    )
    timeline = _timeline_expectation(
        decision,
        evidence.mine_to_bank.result,
        return_result,
    )

    with pytest.raises(RouteEvidenceIntegrityError, match="decision differs from its sources"):
        evaluate_synthetic_round_trip_rehearsal(
            decision,
            timeline_expectation=timeline,
            mine_to_bank_result=evidence.mine_to_bank.result,
            bank_to_mine_result=return_result,
        )


def test_stopped_outbound_still_requires_the_exact_named_return_result(
    pair: _PairEvidence,
) -> None:
    interrupted = _interrupted_result(pair.mine_to_bank.result.progress.session.context)
    evidence = _pair_with_result(pair, RouteDirection.MINE_TO_BANK, interrupted)
    decision = _evaluate(evidence)
    timeline = _timeline_expectation(
        decision,
        interrupted,
        evidence.bank_to_mine.result,
    )

    with pytest.raises(RouteEvidenceIntegrityError, match="malformed or cross-bound"):
        evaluate_synthetic_round_trip_rehearsal(
            decision,
            timeline_expectation=timeline,
            mine_to_bank_result=interrupted,
            bank_to_mine_result=interrupted,
        )


def test_evaluator_owned_projections_cannot_be_forged_with_the_internal_token(
    pair: _PairEvidence,
) -> None:
    _, _, _, report = _completed_round_trip(pair)
    assert report.bank_handoff is not None
    assert report.mine_arrival is not None

    with pytest.raises(RouteEvidenceIntegrityError):
        replace(
            report,
            release_decision_sha256=_digest("forged-round-trip-decision"),
            _factory_token=round_trip_module._FACTORY_TOKEN,
        )
    with pytest.raises(RouteEvidenceIntegrityError):
        replace(
            report,
            _source_timeline=replace(report.timeline, timeline_id="forged-source-timeline"),
            _factory_token=round_trip_module._FACTORY_TOKEN,
        )
    assert report.bank_to_mine is not None
    with pytest.raises(RouteEvidenceIntegrityError):
        replace(
            report.mine_to_bank,
            _source_result=report.bank_to_mine._source_result,
            _factory_token=round_trip_module._FACTORY_TOKEN,
        )

    handoff_mutations: tuple[dict[str, object], ...] = (
        {"mine_to_bank_finalized_package_sha256": _digest("forged-package")},
        {"bank_to_mine_route_session_id": "forged-return-route-session"},
        {"arrival_checkpoint_id": "forged-bank-arrival-checkpoint"},
        {"departure_checkpoint_id": "forged-bank-departure-checkpoint"},
    )
    for mutation in handoff_mutations:
        with pytest.raises(RouteEvidenceIntegrityError):
            replace(
                report.bank_handoff,
                **mutation,
                _factory_token=round_trip_module._FACTORY_TOKEN,
            )
    with pytest.raises(RouteEvidenceIntegrityError):
        replace(
            report.bank_handoff,
            _source_bank_to_mine_leg=report.mine_to_bank,
            _factory_token=round_trip_module._FACTORY_TOKEN,
        )

    arrival_mutations: tuple[dict[str, object], ...] = (
        {"finalized_package_sha256": _digest("forged-return-package")},
        {"route_session_id": "forged-return-arrival-session"},
        {"checkpoint_id": "forged-mine-arrival-checkpoint"},
    )
    for mutation in arrival_mutations:
        with pytest.raises(RouteEvidenceIntegrityError):
            replace(
                report.mine_arrival,
                **mutation,
                _factory_token=round_trip_module._FACTORY_TOKEN,
            )
    with pytest.raises(RouteEvidenceIntegrityError):
        replace(
            report.mine_arrival,
            _source_bank_to_mine_leg=report.mine_to_bank,
            _factory_token=round_trip_module._FACTORY_TOKEN,
        )


def test_standalone_handoff_sources_must_share_the_same_bank_endpoint(
    tmp_path: Path,
) -> None:
    first_pair = _build_pair(tmp_path / "first", bank_location_id="synthetic-bank-first")
    second_pair = _build_pair(tmp_path / "second", bank_location_id="synthetic-bank-second")
    _, _, _, first_report = _completed_round_trip(first_pair)
    _, _, _, second_report = _completed_round_trip(second_pair)
    first_handoff = first_report.bank_handoff
    second_handoff = second_report.bank_handoff
    second_return_leg = second_report.bank_to_mine
    assert first_handoff is not None
    assert second_handoff is not None
    assert second_return_leg is not None

    with pytest.raises(RouteEvidenceIntegrityError, match="lacks exact accepted source evidence"):
        replace(
            first_handoff,
            bank_to_mine_route=second_handoff.bank_to_mine_route,
            bank_to_mine_route_session_id=(second_handoff.bank_to_mine_route_session_id),
            bank_to_mine_finalized_package_sha256=(
                second_handoff.bank_to_mine_finalized_package_sha256
            ),
            bank_to_mine_reviewer_truth_sha256=(second_handoff.bank_to_mine_reviewer_truth_sha256),
            departure_checkpoint_id=second_handoff.departure_checkpoint_id,
            departure_frame_id=second_handoff.departure_frame_id,
            departure_captured_monotonic_s=(second_handoff.departure_captured_monotonic_s),
            departure_frame_payload_sha256=(second_handoff.departure_frame_payload_sha256),
            _source_bank_to_mine_leg=second_return_leg,
            _factory_token=round_trip_module._FACTORY_TOKEN,
        )


@pytest.mark.parametrize(
    ("field_name", "mutated_value"),
    (
        ("retry_count", 1),
        ("input_authority", True),
        ("release_eligible", True),
        ("phase", SyntheticRoundTripPhase.STOPPED),
        (
            "evaluated_leg_order",
            (RouteDirection.BANK_TO_MINE, RouteDirection.MINE_TO_BANK),
        ),
    ),
)
def test_post_construction_report_mutation_cannot_be_serialized(
    pair: _PairEvidence,
    field_name: str,
    mutated_value: object,
) -> None:
    _, _, _, report = _completed_round_trip(pair)
    object.__setattr__(report, field_name, mutated_value)

    with pytest.raises(RouteEvidenceIntegrityError):
        report.to_json_value()


@pytest.mark.parametrize(
    ("projection_name", "field_name", "mutated_value"),
    (
        ("mine_to_bank", "route_session_id", "mutated-outbound-session"),
        ("bank_handoff", "input_authority", True),
        ("mine_arrival", "checkpoint_id", "mutated-mine-arrival"),
    ),
)
def test_post_construction_nested_projection_mutation_cannot_be_serialized(
    pair: _PairEvidence,
    projection_name: str,
    field_name: str,
    mutated_value: object,
) -> None:
    _, _, _, report = _completed_round_trip(pair)
    projection = getattr(report, projection_name)
    assert projection is not None
    object.__setattr__(projection, field_name, mutated_value)

    with pytest.raises(RouteEvidenceIntegrityError):
        report.to_json_value()


def test_return_stop_reason_and_handoff_shape_are_recomputed_from_source_time(
    pair: _PairEvidence,
) -> None:
    late_outbound = _complete_result(
        pair.mine_to_bank.result.progress.session.context,
        started_monotonic_s=40.0,
        session_suffix="late-outbound-for-precedence",
    )
    early_failed_return = _stopped_result(
        pair.bank_to_mine.result.progress.session.context,
        "mid-route-skip",
    )
    nonfresh_evidence = _pair_with_result(pair, RouteDirection.MINE_TO_BANK, late_outbound)
    nonfresh_evidence = _pair_with_result(
        nonfresh_evidence,
        RouteDirection.BANK_TO_MINE,
        early_failed_return,
    )
    nonfresh_decision = _evaluate(nonfresh_evidence)
    nonfresh_report = _round_trip(
        nonfresh_decision,
        late_outbound,
        early_failed_return,
    )
    assert nonfresh_report.stop_reason is (
        SyntheticRoundTripStopReason.BANK_TO_MINE_DEPARTURE_NOT_FRESH
    )
    with pytest.raises(RouteEvidenceIntegrityError, match="bypassed nonfresh precedence"):
        replace(
            nonfresh_report,
            stop_reason=SyntheticRoundTripStopReason.BANK_TO_MINE_NOT_ARRIVED,
            _factory_token=round_trip_module._FACTORY_TOKEN,
        )

    fresh_failed_return = _stopped_result(
        pair.bank_to_mine.result.progress.session.context,
        "mid-route-skip",
    )
    fresh_evidence = _pair_with_result(
        pair,
        RouteDirection.BANK_TO_MINE,
        fresh_failed_return,
    )
    fresh_decision = _evaluate(fresh_evidence)
    fresh_report = _round_trip(
        fresh_decision,
        fresh_evidence.mine_to_bank.result,
        fresh_failed_return,
    )
    assert fresh_report.stop_reason is SyntheticRoundTripStopReason.BANK_TO_MINE_NOT_ARRIVED
    assert fresh_report.bank_handoff is not None
    with pytest.raises(RouteEvidenceIntegrityError, match="omitted its bank handoff"):
        replace(
            fresh_report,
            bank_handoff=None,
            _factory_token=round_trip_module._FACTORY_TOKEN,
        )


def test_three_cycle_endurance_package_is_deterministic_unique_and_narrow(
    endurance_pairs: tuple[_PairEvidence, _PairEvidence, _PairEvidence],
) -> None:
    round_trips = tuple(
        _completed_round_trip_at(
            pair,
            traversal_id=f"nominal-{index}",
            outbound_started_monotonic_s=float(index * 100),
        )
        for index, pair in enumerate(endurance_pairs, start=1)
    )
    expectation = _endurance_expectation(
        round_trips,
        planned_cycle_count=3,
    )

    first = evaluate_synthetic_endurance_rehearsal(
        expectation,
        round_trips=round_trips,
    )
    second = evaluate_synthetic_endurance_rehearsal(
        expectation,
        round_trips=round_trips,
    )

    assert first == second
    assert first.phase is SyntheticEndurancePhase.COMPLETED
    assert first.stop_reason is None
    assert first.completed_cycle_count == 3
    assert first.retained_failure_count == 0
    assert first.explicit_recovery_count == 0
    assert first.successful_recovery_count == 0
    assert (
        tuple(item.outcome for item in first.attempts)
        == (SyntheticEnduranceAttemptOutcome.COMPLETED,) * 3
    )
    assert first.attempts[0].cross_traversal_departure_fresh is None
    assert all(item.cross_traversal_departure_fresh is True for item in first.attempts[1:])
    first_bytes = first.canonical_bytes
    second_bytes = second.canonical_bytes
    assert first_bytes == second_bytes
    assert first.content_sha256 == Sha256Digest.from_bytes(first_bytes)
    payload = json.loads(first_bytes)
    histories = [
        history
        for attempt in payload["attempts"]
        for history in attempt["named_direction_histories"]
    ]
    assert len(histories) == 6
    assert len({item["route_session_id"] for item in histories}) == 6
    assert len({item["capture_source"]["capture_session_id"] for item in histories}) == 6
    assert len({item["attempt_source"]["session_id"] for item in histories}) == 6
    assert len({item["durable_lineage"]["campaign_id"] for item in histories}) == 6
    assert len({item["durable_lineage"]["review_id"] for item in histories}) == 6
    assert len({item["durable_lineage"]["finalized_package_sha256"] for item in histories}) == len(
        histories
    )
    assert len({item["durable_lineage"]["independent_review_sha256"] for item in histories}) == len(
        histories
    )
    root_identities = [
        tuple(item["durable_lineage"][root_field])
        for item in histories
        for root_field in ("acquisition_root_identity", "review_root_identity")
    ]
    assert len(set(root_identities)) == len(root_identities)
    attempt_ids = [
        completed["attempt_id"]
        for history in histories
        for completed in history["completed_attempts"]
    ]
    assert len(set(attempt_ids)) == len(attempt_ids)
    assert first.automatic_retry_count == 0
    assert first.report_adoption_count == 0
    assert first.real_endurance_satisfied is False
    assert first.bank_interface_open_proven is False
    assert first.supported_mining_view_proven is False
    assert first.release_eligible is False
    assert first.live_navigation_enabled is False
    assert first.world_state_activation_allowed is False
    assert first.controller_activation_allowed is False
    assert first.activation_allowed is False
    assert first.input_authority is False


@pytest.mark.parametrize("boundary_delta", (-0.01, 0.0))
def test_endurance_cross_traversal_departure_must_be_strictly_fresh(
    endurance_pairs: tuple[_PairEvidence, _PairEvidence, _PairEvidence],
    boundary_delta: float,
) -> None:
    first = _completed_round_trip_at(
        endurance_pairs[0],
        traversal_id=f"boundary-first-{boundary_delta}",
        outbound_started_monotonic_s=10.0,
    )
    prior_terminal = first._source_decision.bank_to_mine._source_post_attempt_result.progress.last_event_monotonic_s
    second = _completed_round_trip_at(
        endurance_pairs[1],
        traversal_id=f"boundary-second-{boundary_delta}",
        outbound_started_monotonic_s=prior_terminal + boundary_delta - 0.1,
        outbound_first_frame_monotonic_s=prior_terminal + boundary_delta,
    )
    round_trips = (first, second)
    expectation = _endurance_expectation(round_trips, planned_cycle_count=2)

    report = evaluate_synthetic_endurance_rehearsal(
        expectation,
        round_trips=round_trips,
    )

    assert report.phase is SyntheticEndurancePhase.STOPPED
    assert report.stop_reason is (SyntheticEnduranceStopReason.CROSS_TRAVERSAL_DEPARTURE_NOT_FRESH)
    assert report.completed_cycle_count == 1
    assert report.retained_failure_count == 1
    assert report.attempts[-1].outcome is (
        SyntheticEnduranceAttemptOutcome.CAMPAIGN_BOUNDARY_STOPPED
    )
    assert report.attempts[-1].round_trip.phase is SyntheticRoundTripPhase.COMPLETED
    assert report.attempts[-1].cross_traversal_departure_fresh is False
    assert report.automatic_retry_count == 0


def test_endurance_boundary_stop_requires_explicit_same_cycle_fresh_recovery(
    endurance_pairs: tuple[_PairEvidence, _PairEvidence, _PairEvidence],
) -> None:
    first = _completed_round_trip_at(
        endurance_pairs[0],
        traversal_id="boundary-recovery-first",
        outbound_started_monotonic_s=10.0,
    )
    prior_terminal = first._source_decision.bank_to_mine._source_post_attempt_result.progress.last_event_monotonic_s
    nonfresh = _completed_round_trip_at(
        endurance_pairs[1],
        traversal_id="boundary-recovery-nonfresh",
        outbound_started_monotonic_s=prior_terminal - 0.1,
        outbound_first_frame_monotonic_s=prior_terminal,
    )
    recovery = _completed_round_trip_at(
        endurance_pairs[2],
        traversal_id="boundary-recovery-fresh",
        outbound_started_monotonic_s=100.0,
    )
    round_trips = (first, nonfresh, recovery)
    expectation = _endurance_expectation(
        round_trips,
        planned_cycle_count=2,
        cycle_numbers=(1, 2, 2),
        recovery_links=(None, None, "synthetic-endurance-traversal-2"),
    )

    report = evaluate_synthetic_endurance_rehearsal(
        expectation,
        round_trips=round_trips,
    )

    assert report.phase is SyntheticEndurancePhase.COMPLETED
    assert report.completed_cycle_count == 2
    assert report.retained_failure_count == 1
    assert report.explicit_recovery_count == 1
    assert report.successful_recovery_count == 1
    assert report.attempts[1].round_trip.phase is SyntheticRoundTripPhase.COMPLETED
    assert report.attempts[1].outcome is (
        SyntheticEnduranceAttemptOutcome.CAMPAIGN_BOUNDARY_STOPPED
    )
    assert report.attempts[1].boundary_stop_reason is (
        SyntheticEnduranceStopReason.CROSS_TRAVERSAL_DEPARTURE_NOT_FRESH
    )
    assert report.attempts[2].explicit_recovery is True
    assert report.attempts[2].recovery_fresh_departure_proven is True
    assert report.automatic_retry_count == 0


def test_endurance_rejects_exact_report_replay_and_cross_cycle_source_session_reuse(
    pair: _PairEvidence,
) -> None:
    first = _completed_round_trip_at(
        pair,
        traversal_id="identity-first",
        outbound_started_monotonic_s=10.0,
    )
    with pytest.raises(RouteEvidenceIntegrityError, match="reports must be unique"):
        _endurance_expectation((first, first), planned_cycle_count=2)

    second = _completed_round_trip_at(
        pair,
        traversal_id="identity-second",
        outbound_started_monotonic_s=100.0,
    )
    round_trips = (first, second)
    expectation = _endurance_expectation(round_trips, planned_cycle_count=2)
    with pytest.raises(RouteEvidenceIntegrityError, match="capture session id"):
        evaluate_synthetic_endurance_rehearsal(
            expectation,
            round_trips=round_trips,
        )


def test_endurance_rejects_route_session_and_completed_attempt_identity_reuse(
    tmp_path: Path,
    endurance_pairs: tuple[_PairEvidence, _PairEvidence, _PairEvidence],
    endurance_nominal_round_trips: tuple[
        round_trip_module.SyntheticRoundTripRehearsalReport,
        round_trip_module.SyntheticRoundTripRehearsalReport,
    ],
) -> None:
    first = endurance_nominal_round_trips[0]
    route_session_reuse = _completed_round_trip_at(
        endurance_pairs[2],
        traversal_id="fault-recovery",
        outbound_started_monotonic_s=200.0,
    )
    route_session_reports = (first, route_session_reuse)
    with pytest.raises(RouteEvidenceIntegrityError, match="route session id"):
        evaluate_synthetic_endurance_rehearsal(
            _endurance_expectation(route_session_reports, planned_cycle_count=2),
            round_trips=route_session_reports,
        )

    first_attempt_id = first._source_decision.mine_to_bank._source_post_attempt_result.progress.navigation.completed_attempts[
        0
    ].identity.attempt_id
    duplicate_prefix = first_attempt_id.removesuffix("-attempt-1")
    attempt_reuse = _completed_round_trip_at(
        endurance_pairs[2],
        traversal_id="distinct-route-session",
        outbound_started_monotonic_s=200.0,
        outbound_attempt_id_prefix=duplicate_prefix,
    )
    attempt_reports = (first, attempt_reuse)
    with pytest.raises(RouteEvidenceIntegrityError, match="completed attempt id"):
        evaluate_synthetic_endurance_rehearsal(
            _endurance_expectation(attempt_reports, planned_cycle_count=2),
            round_trips=attempt_reports,
        )

    attempt_source_reuse_pair = _build_pair(
        tmp_path / "attempt-source-reuse",
        identity_suffix="attempt-source-reuse",
        attempt_session_suffix="endurance-2",
    )
    attempt_source_reuse = _completed_round_trip_at(
        attempt_source_reuse_pair,
        traversal_id="attempt-source-reuse",
        outbound_started_monotonic_s=200.0,
    )
    attempt_source_reports = (first, attempt_source_reuse)
    with pytest.raises(RouteEvidenceIntegrityError, match="attempt-source session id"):
        evaluate_synthetic_endurance_rehearsal(
            _endurance_expectation(attempt_source_reports, planned_cycle_count=2),
            round_trips=attempt_source_reports,
        )


def test_endurance_rejects_recovery_after_success_and_malformed_order(
    endurance_nominal_round_trips: tuple[
        round_trip_module.SyntheticRoundTripRehearsalReport,
        round_trip_module.SyntheticRoundTripRehearsalReport,
    ],
) -> None:
    first, second = endurance_nominal_round_trips
    invalid_recovery = _endurance_expectation(
        (first, second),
        planned_cycle_count=2,
        cycle_numbers=(1, 1),
        recovery_links=(None, "synthetic-endurance-traversal-1"),
    )
    with pytest.raises(RouteEvidenceIntegrityError, match="recovered after success"):
        evaluate_synthetic_endurance_rehearsal(
            invalid_recovery,
            round_trips=(first, second),
        )

    first_attempt = SyntheticTraversalAttemptExpectation(
        traversal_id="manifest-first",
        cycle_number=1,
        scenario_id="nominal",
        round_trip_sha256=_digest("manifest-first-report"),
        expected_round_trip_phase=SyntheticRoundTripPhase.COMPLETED,
        expected_round_trip_stop_reason=None,
    )
    skipped_attempt = SyntheticTraversalAttemptExpectation(
        traversal_id="manifest-skipped",
        cycle_number=3,
        scenario_id="nominal",
        round_trip_sha256=_digest("manifest-skipped-report"),
        expected_round_trip_phase=SyntheticRoundTripPhase.COMPLETED,
        expected_round_trip_stop_reason=None,
    )
    with pytest.raises(RouteEvidenceIntegrityError, match="skips or delays"):
        SyntheticEnduranceExpectation(
            campaign_id="malformed-order-campaign",
            shared_timeline_id="synthetic-round-trip-shared-timeline",
            planned_cycle_count=3,
            ordered_attempts=(first_attempt, skipped_attempt),
        )


def test_endurance_terminal_stop_reasons_distinguish_failure_from_incomplete_target(
    endurance_pairs: tuple[_PairEvidence, _PairEvidence, _PairEvidence],
    endurance_nominal_round_trips: tuple[
        round_trip_module.SyntheticRoundTripRehearsalReport,
        round_trip_module.SyntheticRoundTripRehearsalReport,
    ],
) -> None:
    _, failed = _stopped_round_trip(
        endurance_pairs[0],
        direction=RouteDirection.MINE_TO_BANK,
        case="interrupted",
    )
    failed_expectation = _endurance_expectation((failed,), planned_cycle_count=2)
    failed_report = evaluate_synthetic_endurance_rehearsal(
        failed_expectation,
        round_trips=(failed,),
    )
    assert failed_report.phase is SyntheticEndurancePhase.STOPPED
    assert failed_report.stop_reason is SyntheticEnduranceStopReason.UNRECOVERED_TRAVERSAL
    assert failed_report.completed_cycle_count == 0
    assert failed_report.retained_failure_count == 1

    implicit_advance = _endurance_expectation(
        (failed, endurance_nominal_round_trips[0]),
        planned_cycle_count=2,
        cycle_numbers=(1, 2),
    )
    with pytest.raises(RouteEvidenceIntegrityError, match="failure was not recovered exactly"):
        evaluate_synthetic_endurance_rehearsal(
            implicit_advance,
            round_trips=(failed, endurance_nominal_round_trips[0]),
        )

    nominal = endurance_nominal_round_trips[0]
    incomplete_expectation = _endurance_expectation((nominal,), planned_cycle_count=2)
    incomplete_report = evaluate_synthetic_endurance_rehearsal(
        incomplete_expectation,
        round_trips=(nominal,),
    )
    assert incomplete_report.phase is SyntheticEndurancePhase.STOPPED
    assert incomplete_report.stop_reason is (
        SyntheticEnduranceStopReason.PLANNED_CYCLE_TARGET_NOT_MET
    )
    assert incomplete_report.completed_cycle_count == 1
    assert incomplete_report.retained_failure_count == 0


def test_endurance_rejects_route_contract_drift(
    tmp_path: Path,
    endurance_nominal_round_trips: tuple[
        round_trip_module.SyntheticRoundTripRehearsalReport,
        round_trip_module.SyntheticRoundTripRehearsalReport,
    ],
) -> None:
    stable = endurance_nominal_round_trips[0]
    changed_pair = _build_pair(
        tmp_path / "changed-route-version",
        identity_suffix="changed-route-version",
        shared_route_version="2.0.0-synthetic",
    )
    changed = _completed_round_trip_at(
        changed_pair,
        traversal_id="changed-route-version",
        outbound_started_monotonic_s=200.0,
    )
    round_trips = (stable, changed)
    expectation = _endurance_expectation(round_trips, planned_cycle_count=2)

    with pytest.raises(RouteEvidenceIntegrityError, match="contract changed"):
        evaluate_synthetic_endurance_rehearsal(
            expectation,
            round_trips=round_trips,
        )


def test_endurance_history_and_authority_mutation_cannot_be_serialized(
    endurance_nominal_round_trips: tuple[
        round_trip_module.SyntheticRoundTripRehearsalReport,
        round_trip_module.SyntheticRoundTripRehearsalReport,
    ],
) -> None:
    expectation = _endurance_expectation(
        endurance_nominal_round_trips,
        planned_cycle_count=2,
    )
    report = evaluate_synthetic_endurance_rehearsal(
        expectation,
        round_trips=endurance_nominal_round_trips,
    )

    with pytest.raises(RouteEvidenceIntegrityError, match="history length changed"):
        replace(
            report,
            attempts=report.attempts[:-1],
            _factory_token=endurance_module._VALIDATION_TOKEN,
        )
    with pytest.raises(RouteEvidenceIntegrityError, match="outcome differs"):
        replace(
            report.attempts[0],
            outcome=SyntheticEnduranceAttemptOutcome.TRAVERSAL_STOPPED,
            _factory_token=endurance_module._VALIDATION_TOKEN,
        )
    second = report.attempts[1]
    assert second.first_outbound_departure_monotonic_s is not None
    forged_boundary = replace(
        second,
        outcome=SyntheticEnduranceAttemptOutcome.CAMPAIGN_BOUNDARY_STOPPED,
        boundary_stop_reason=(SyntheticEnduranceStopReason.CROSS_TRAVERSAL_DEPARTURE_NOT_FRESH),
        cross_traversal_departure_fresh=False,
        _source_prior_terminal_monotonic_s=(second.first_outbound_departure_monotonic_s),
        _factory_token=endurance_module._FACTORY_TOKEN,
    )
    assert "SyntheticTraversalAttemptRecord" not in endurance_module.__all__
    assert not hasattr(forged_boundary, "to_json_value")
    with pytest.raises(RouteEvidenceIntegrityError, match="differs from exact source fold"):
        replace(
            report,
            attempts=(report.attempts[0], forged_boundary),
            _factory_token=endurance_module._VALIDATION_TOKEN,
        )

    object.__setattr__(report, "input_authority", True)
    with pytest.raises(RouteEvidenceIntegrityError, match="changed after evaluation"):
        report.to_json_value()


@pytest.mark.parametrize(
    ("direction", "case", "outer_reason", "inner_reason"),
    (
        (
            RouteDirection.MINE_TO_BANK,
            "interrupted",
            OfflineRouteSessionStopReason.INTERRUPTED,
            NavigationFailureReason.SESSION_INTERRUPTED,
        ),
        (
            RouteDirection.MINE_TO_BANK,
            "mid-route-skip",
            OfflineRouteSessionStopReason.NAVIGATION_FAILURE,
            NavigationFailureReason.SKIPPED_CHECKPOINT,
        ),
        (
            RouteDirection.MINE_TO_BANK,
            "stale",
            OfflineRouteSessionStopReason.NAVIGATION_FAILURE,
            NavigationFailureReason.STALE_FRAME,
        ),
        (
            RouteDirection.MINE_TO_BANK,
            "late-receipt",
            OfflineRouteSessionStopReason.NAVIGATION_FAILURE,
            NavigationFailureReason.STALE_ATTEMPT_RECEIPT,
        ),
        (
            RouteDirection.MINE_TO_BANK,
            "non-post-receipt-evidence",
            OfflineRouteSessionStopReason.NAVIGATION_FAILURE,
            NavigationFailureReason.EVIDENCE_NOT_AFTER_BOUNDARY,
        ),
        (
            RouteDirection.MINE_TO_BANK,
            "duplicate-attempt-receipt",
            OfflineRouteSessionStopReason.NAVIGATION_FAILURE,
            NavigationFailureReason.DUPLICATE_ATTEMPT_RECEIPT,
        ),
        (
            RouteDirection.MINE_TO_BANK,
            "wrong-direction",
            OfflineRouteSessionStopReason.NAVIGATION_FAILURE,
            NavigationFailureReason.DIRECTION_MISMATCH,
        ),
        (
            RouteDirection.MINE_TO_BANK,
            "wrong-route",
            OfflineRouteSessionStopReason.NAVIGATION_FAILURE,
            NavigationFailureReason.ROUTE_ID_MISMATCH,
        ),
        (
            RouteDirection.MINE_TO_BANK,
            "mixed-session",
            OfflineRouteSessionStopReason.NAVIGATION_FAILURE,
            NavigationFailureReason.PROVENANCE_MISMATCH,
        ),
        (
            RouteDirection.MINE_TO_BANK,
            "unknown",
            OfflineRouteSessionStopReason.NAVIGATION_FAILURE,
            NavigationFailureReason.UNKNOWN_CHECKPOINT,
        ),
        (
            RouteDirection.MINE_TO_BANK,
            "ambiguous",
            OfflineRouteSessionStopReason.NAVIGATION_FAILURE,
            NavigationFailureReason.AMBIGUOUS_CHECKPOINT,
        ),
        (
            RouteDirection.BANK_TO_MINE,
            "duplicate-attempt-id",
            OfflineRouteSessionStopReason.NAVIGATION_FAILURE,
            NavigationFailureReason.DUPLICATE_ATTEMPT_ID,
        ),
        (
            RouteDirection.BANK_TO_MINE,
            "checkpoint-timeout",
            OfflineRouteSessionStopReason.CHECKPOINT_TIMEOUT,
            NavigationFailureReason.CHECKPOINT_TIMEOUT,
        ),
        (
            RouteDirection.BANK_TO_MINE,
            "step-timeout",
            OfflineRouteSessionStopReason.STEP_TIMEOUT,
            NavigationFailureReason.STEP_TIMEOUT,
        ),
        (
            RouteDirection.BANK_TO_MINE,
            "attempt-timeout",
            OfflineRouteSessionStopReason.ATTEMPT_TIMEOUT,
            NavigationFailureReason.ATTEMPT_TIMEOUT,
        ),
    ),
)
def test_endurance_retains_fault_and_requires_explicit_fresh_recovery(
    endurance_pairs: tuple[_PairEvidence, _PairEvidence, _PairEvidence],
    endurance_nominal_round_trips: tuple[
        round_trip_module.SyntheticRoundTripRehearsalReport,
        round_trip_module.SyntheticRoundTripRehearsalReport,
    ],
    direction: RouteDirection,
    case: str,
    outer_reason: OfflineRouteSessionStopReason,
    inner_reason: NavigationFailureReason,
) -> None:
    failed_result, failed_round_trip = _stopped_round_trip(
        endurance_pairs[0],
        direction=direction,
        case=case,
    )
    round_trips = (failed_round_trip, *endurance_nominal_round_trips)
    failed_traversal_id = "synthetic-endurance-traversal-1"
    expectation = _endurance_expectation(
        round_trips,
        planned_cycle_count=2,
        cycle_numbers=(1, 1, 2),
        recovery_links=(None, failed_traversal_id, None),
    )

    report = evaluate_synthetic_endurance_rehearsal(
        expectation,
        round_trips=round_trips,
    )

    assert failed_result.progress.stop_reason is outer_reason
    assert failed_result.progress.navigation.failure_reason is inner_reason
    assert report.phase is SyntheticEndurancePhase.COMPLETED
    assert report.stop_reason is None
    assert report.completed_cycle_count == 2
    assert report.retained_failure_count == 1
    assert report.explicit_recovery_count == 1
    assert report.successful_recovery_count == 1
    failed, recovery, next_cycle = report.attempts
    assert failed.ordinal == 1
    assert failed.expectation.scenario_id == (f"retained-{failed_round_trip.stop_reason.value}")
    assert failed.outcome is SyntheticEnduranceAttemptOutcome.TRAVERSAL_STOPPED
    assert failed.round_trip is not endurance_nominal_round_trips[0]
    assert failed.round_trip.stop_reason is (
        SyntheticRoundTripStopReason.MINE_TO_BANK_NOT_ARRIVED
        if direction is RouteDirection.MINE_TO_BANK
        else SyntheticRoundTripStopReason.BANK_TO_MINE_NOT_ARRIVED
    )
    failed_leg = (
        failed.round_trip.mine_to_bank
        if direction is RouteDirection.MINE_TO_BANK
        else failed.round_trip.bank_to_mine
    )
    assert failed_leg is not None
    assert failed_leg.session_stop_reason is outer_reason
    assert failed_leg.navigation_failure_reason is inner_reason
    failed_binding = (
        failed.round_trip._source_decision.mine_to_bank
        if direction is RouteDirection.MINE_TO_BANK
        else failed.round_trip._source_decision.bank_to_mine
    )
    assert (
        failed_leg.session_result_sha256
        == failed_binding.post_attempt_causality.session_result_sha256
    )
    if direction is RouteDirection.MINE_TO_BANK:
        assert failed.round_trip.evaluated_leg_order == (RouteDirection.MINE_TO_BANK,)
        assert failed.round_trip.bank_to_mine is None
    else:
        assert failed.round_trip.evaluated_leg_order == (
            RouteDirection.MINE_TO_BANK,
            RouteDirection.BANK_TO_MINE,
        )
    assert recovery.expectation.recovery_of_traversal_id == failed_traversal_id
    assert recovery.explicit_recovery is True
    assert recovery.recovery_fresh_departure_proven is True
    assert recovery.outcome is SyntheticEnduranceAttemptOutcome.COMPLETED
    assert next_cycle.expectation.cycle_number == 2
    assert next_cycle.explicit_recovery is False
    assert next_cycle.outcome is SyntheticEnduranceAttemptOutcome.COMPLETED
    assert report.automatic_retry_count == 0
    assert report.report_adoption_count == 0
    assert {"retry", "restart", "resume", "adopt"}.isdisjoint(dir(report))


@pytest.mark.parametrize(
    ("rejected_direction", "expected_reason"),
    (
        (
            RouteDirection.MINE_TO_BANK,
            SyntheticRoundTripStopReason.MINE_TO_BANK_EVIDENCE_NOT_APPROVED,
        ),
        (
            RouteDirection.BANK_TO_MINE,
            SyntheticRoundTripStopReason.BANK_TO_MINE_EVIDENCE_NOT_APPROVED,
        ),
    ),
)
def test_endurance_retains_endpoint_denial_until_a_fresh_b1_lineage_recovers(
    tmp_path: Path,
    endurance_nominal_round_trips: tuple[
        round_trip_module.SyntheticRoundTripRehearsalReport,
        round_trip_module.SyntheticRoundTripRehearsalReport,
    ],
    rejected_direction: RouteDirection,
    expected_reason: SyntheticRoundTripStopReason,
) -> None:
    rejected_pair = _build_pair(
        tmp_path / rejected_direction.value,
        identity_suffix=f"endpoint-denial-{rejected_direction.value}",
        mine_to_bank_reject_arrival=(rejected_direction is RouteDirection.MINE_TO_BANK),
        bank_to_mine_reject_arrival=(rejected_direction is RouteDirection.BANK_TO_MINE),
    )
    rejected_decision = _evaluate(rejected_pair)
    rejected_round_trip = _round_trip(
        rejected_decision,
        rejected_pair.mine_to_bank.result,
        rejected_pair.bank_to_mine.result,
    )
    round_trips = (rejected_round_trip, *endurance_nominal_round_trips)
    expectation = _endurance_expectation(
        round_trips,
        planned_cycle_count=2,
        cycle_numbers=(1, 1, 2),
        recovery_links=(None, "synthetic-endurance-traversal-1", None),
    )

    report = evaluate_synthetic_endurance_rehearsal(
        expectation,
        round_trips=round_trips,
    )

    assert report.phase is SyntheticEndurancePhase.COMPLETED
    assert report.retained_failure_count == 1
    assert report.attempts[0].round_trip.stop_reason is expected_reason
    assert report.attempts[0].outcome is SyntheticEnduranceAttemptOutcome.TRAVERSAL_STOPPED
    assert report.attempts[1].explicit_recovery is True
    assert report.attempts[1].recovery_fresh_departure_proven is True
    assert (
        report.attempts[0].round_trip.release_decision_sha256
        != report.attempts[1].round_trip.release_decision_sha256
    )
    assert report.bank_interface_open_proven is False
    assert report.supported_mining_view_proven is False
    assert report.release_eligible is False
    assert report.input_authority is False


def test_release_boundary_is_not_root_exported_or_input_capable() -> None:
    exported = set(navigation_root.__all__)
    integration_exports = set(integration_boundary.__all__)
    assert "NavigationReleaseDecision" not in exported
    assert "evaluate_navigation_release_readiness" not in exported
    assert "NavigationReleaseDecision" not in integration_exports
    assert "evaluate_navigation_release_readiness" not in integration_exports
    assert "SyntheticRoundTripRehearsalReport" not in exported
    assert "evaluate_synthetic_round_trip_rehearsal" not in exported
    assert "SyntheticRoundTripRehearsalReport" not in integration_exports
    assert "evaluate_synthetic_round_trip_rehearsal" not in integration_exports
    assert "SyntheticEnduranceRehearsalReport" not in exported
    assert "evaluate_synthetic_endurance_rehearsal" not in exported
    assert "SyntheticEnduranceRehearsalReport" not in integration_exports
    assert "evaluate_synthetic_endurance_rehearsal" not in integration_exports

    forbidden_module_parts = {
        "pyautogui",
        "pynput",
        "keyboard",
        "mouse",
        "subprocess",
        "ctypes",
        "controller",
        "world_state",
        "banking",
        "input",
    }
    for module_under_test in (release_module, round_trip_module, endurance_module):
        source_path = Path(module_under_test.__file__ or "")
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported_modules, imported_names = _imports(tree)
        assert all(
            forbidden not in module.lstrip(".").split(".")
            for module in imported_modules
            for forbidden in forbidden_module_parts
        )
        assert {"MiningController", "WorldState"}.isdisjoint(imported_names)
