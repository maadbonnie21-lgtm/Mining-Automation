from __future__ import annotations

import ast
import json
import os
from dataclasses import dataclass, fields, replace
from pathlib import Path

import pytest

import mining_automation.navigation as navigation_root
import mining_automation.navigation.release_decision as release_module
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
from mining_automation.navigation.offline_route_session import (
    OfflineRouteSession,
    OfflineRouteSessionPhase,
    OfflineRouteSessionResult,
    OfflineRouteSessionSequencer,
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
            session_id=f"{prefix}-attempt-session",
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


def _complete_result(context: RouteEvaluationContext) -> OfflineRouteSessionResult:
    session = _session(context)
    sequencer = OfflineRouteSessionSequencer.begin(session, started_monotonic_s=10.0)
    last_result: OfflineRouteSessionResult | None = None
    for index, checkpoint in enumerate(context.plan.checkpoints):
        captured = 10.1 + (index * 0.3)
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
            attempt_id=f"{session.session_id}-attempt-{index + 1}",
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
    context = _execution_context(plan.route_plan, identity)
    result = _complete_result(context)
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
    bank_to_mine_reject_arrival: bool = False,
    shared_route_id: str | None = None,
    shared_route_version: str = "1.0.0-synthetic",
    shared_reviewer_id: str | None = None,
) -> _PairEvidence:
    return _PairEvidence(
        mine_to_bank=_build_direction(
            root / "mine-to-bank",
            RouteDirection.MINE_TO_BANK,
            campaign_id="synthetic-release-m2b-campaign",
            capture_session_id="synthetic-release-m2b-session",
            operator_id="synthetic-release-m2b-operator",
            review_id="synthetic-release-m2b-review",
            reviewer_id=shared_reviewer_id or "synthetic-release-m2b-reviewer",
            mine_location_id=mine_location_id,
            bank_location_id=bank_location_id,
            route_id=shared_route_id,
            route_version=shared_route_version,
        ),
        bank_to_mine=_build_direction(
            root / "bank-to-mine",
            RouteDirection.BANK_TO_MINE,
            campaign_id="synthetic-release-b2m-campaign",
            capture_session_id="synthetic-release-b2m-session",
            operator_id="synthetic-release-b2m-operator",
            review_id="synthetic-release-b2m-review",
            reviewer_id=shared_reviewer_id or "synthetic-release-b2m-reviewer",
            mine_location_id=(
                mine_location_id
                if bank_to_mine_mine_location_id is None
                else bank_to_mine_mine_location_id
            ),
            bank_location_id=bank_location_id,
            reject_arrival=bank_to_mine_reject_arrival,
            route_id=shared_route_id,
            route_version=shared_route_version,
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


@pytest.fixture(scope="module")
def pair(tmp_path_factory: pytest.TempPathFactory) -> _PairEvidence:
    return _build_pair(tmp_path_factory.mktemp("release-decision-pair"))


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


def test_release_boundary_is_not_root_exported_or_input_capable() -> None:
    exported = set(navigation_root.__all__)
    integration_exports = set(integration_boundary.__all__)
    assert "NavigationReleaseDecision" not in exported
    assert "evaluate_navigation_release_readiness" not in exported
    assert "NavigationReleaseDecision" not in integration_exports
    assert "evaluate_navigation_release_readiness" not in integration_exports

    source_path = Path(release_module.__file__ or "")
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_modules, imported_names = _imports(tree)
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
    assert all(
        forbidden not in module.lstrip(".").split(".")
        for module in imported_modules
        for forbidden in forbidden_module_parts
    )
    assert {"MiningController", "WorldState"}.isdisjoint(imported_names)
