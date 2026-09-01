from __future__ import annotations

import ast
import json
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Barrier

import pytest

import mining_automation.navigation as navigation_root
import mining_automation.navigation.durable_route_evidence as durable_module
import mining_automation.navigation.integration_boundary as integration_boundary
import mining_automation.navigation.route_evidence_loader as loader_module
from mining_automation.capture.frame import Frame, PixelFormat
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
    CheckpointProfileIdentity,
    CheckpointRole,
    CheckpointSourceIdentity,
    FrameProvenance,
    NavigationFailureReason,
    NavigationPhase,
    NavigationPolicy,
    NavigationTransitionOutcome,
    RouteDirection,
    RouteEndpoint,
    RouteEndpointRole,
    RouteEvaluationContext,
    RouteIdentity,
    RoutePlan,
    RouteStep,
    Sha256Digest,
    StepAttemptIdentity,
    StepAttemptSourceIdentity,
    SyntheticStepAttemptReceipt,
)
from mining_automation.navigation.durable_route_evidence import (
    ACQUISITION_FINALIZATION_FILENAME,
    ACQUISITION_PLAN_FILENAME,
    ACQUISITION_STOP_FILENAME,
    DURABLE_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE,
    DURABLE_WRITER_NAMESPACE_CONTRACT,
    REVIEW_FINALIZATION_FILENAME,
    REVIEW_STOP_FILENAME,
    DurableAcquisitionFilesystemExpectation,
    DurableAcquisitionPhase,
    DurableAcquisitionReceipt,
    DurableAcquisitionTransaction,
    DurableEvidenceCollisionError,
    DurableEvidenceError,
    DurableEvidenceStateError,
    DurableReviewPhase,
    DurableReviewReceipt,
    DurableReviewTransaction,
    DurableRouteEvidenceFilesystemExpectation,
    VerifiedDurableAcquisition,
    begin_durable_acquisition,
    begin_durable_review,
    load_and_verify_durable_synthetic_route_evidence,
    load_durable_acquisition,
)
from mining_automation.navigation.passive_campaign import (
    PassiveCampaignPhase,
    PassiveCaptureRequest,
    PassiveCaptureSourceIdentity,
    PassiveSourceFrame,
)
from mining_automation.navigation.replay import (
    NAVIGATION_REPLAY_SCHEMA_VERSION,
    SYNTHETIC_FIXTURE_ROLE,
    NavigationReplayManifest,
    ObserveCheckpointEvent,
    PrepareStepEvent,
    RecordStepAttemptReceiptEvent,
    ReplayExpectedState,
    run_navigation_replay,
)
from mining_automation.navigation.route_evidence import (
    RouteEvidenceCampaignPlan,
    RouteEvidenceCaptureBuildIdentity,
    RouteEvidenceCaseRole,
    RouteEvidenceCaseSpec,
    RouteEvidenceCaseTruth,
    RouteEvidenceIntegrityError,
    RouteEvidenceReviewDecision,
    canonical_route_evidence_bytes,
    parse_synthetic_detector_report,
)
from mining_automation.navigation.route_evidence_loader import (
    FINALIZED_PACKAGE_FILENAME,
    INDEPENDENT_REVIEW_FILENAME,
)


def _digest(label: str) -> Sha256Digest:
    return Sha256Digest.from_bytes(label.encode("ascii"))


def _import_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", maxsplit=1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            roots.add(node.module.split(".", maxsplit=1)[0])
    return roots


def _campaign(
    direction: RouteDirection = RouteDirection.MINE_TO_BANK,
    *,
    campaign_id: str = "synthetic-durable-campaign-a",
    capture_session_id: str = "synthetic-durable-session-a",
) -> tuple[RouteEvidenceCampaignPlan, PassiveCaptureSourceIdentity]:
    prefix = "synthetic-m2b" if direction is RouteDirection.MINE_TO_BANK else "synthetic-b2m"
    origin_role, destination_role = (
        (RouteEndpointRole.MINE, RouteEndpointRole.BANK)
        if direction is RouteDirection.MINE_TO_BANK
        else (RouteEndpointRole.BANK, RouteEndpointRole.MINE)
    )
    checkpoints = (
        Checkpoint(f"{prefix}-departure", CheckpointRole.DEPARTURE),
        Checkpoint(f"{prefix}-transit", CheckpointRole.TRANSIT),
        Checkpoint(f"{prefix}-arrival", CheckpointRole.ARRIVAL),
    )
    route = RoutePlan(
        RouteIdentity(f"{prefix}-route", "1.0.0-synthetic", direction),
        RouteEndpoint(f"{prefix}-origin", origin_role),
        RouteEndpoint(f"{prefix}-destination", destination_role),
        checkpoints,
        (
            RouteStep(
                f"{prefix}-step-1",
                checkpoints[0].checkpoint_id,
                checkpoints[1].checkpoint_id,
            ),
            RouteStep(
                f"{prefix}-step-2",
                checkpoints[1].checkpoint_id,
                checkpoints[2].checkpoint_id,
            ),
        ),
    )
    profile = CheckpointProfile(
        f"{prefix}-profile",
        "1.0.0-synthetic",
        CheckpointEvidenceRole.SYNTHETIC_ARCHITECTURE_TEST_ONLY,
        2,
        1,
        PixelFormat.BGRA8888,
        tuple(checkpoint.checkpoint_id for checkpoint in checkpoints),
    )
    checkpoint_source = CheckpointSourceIdentity(
        CheckpointDetectorIdentity(f"{prefix}-detector", "1.0.0-synthetic"),
        profile,
        f"{prefix}-source",
        capture_session_id,
    )
    capture_build = RouteEvidenceCaptureBuildIdentity(
        f"{prefix}-capture-build",
        "1.0.0-synthetic",
        _digest(f"{prefix}-capture-build-content"),
    )
    plan = RouteEvidenceCampaignPlan(
        campaign_id=campaign_id,
        route_plan=route,
        detector=checkpoint_source.detector,
        profile=profile.identity,
        capture_source_id=checkpoint_source.frame_source_id,
        capture_session_id=capture_session_id,
        capture_build=capture_build,
        frame_width=profile.frame_width,
        frame_height=profile.frame_height,
        pixel_format=profile.pixel_format,
        capture_configuration_sha256=_digest(f"{prefix}-capture-config"),
        capture_environment_sha256=_digest(f"{prefix}-environment"),
        support_envelope_sha256=_digest(f"{prefix}-support"),
        operator_id="synthetic-durable-operator",
        created_at_utc="2026-09-01T00:00:00Z",
        cases=tuple(
            RouteEvidenceCaseSpec(
                index,
                f"{prefix}-case-{index}",
                (
                    RouteEvidenceCaseRole.ROUTE_ARRIVAL
                    if checkpoint.role is CheckpointRole.ARRIVAL
                    else RouteEvidenceCaseRole.CHECKPOINT_POSITIVE
                ),
                checkpoint.checkpoint_id,
            )
            for index, checkpoint in enumerate(checkpoints, start=1)
        ),
    )
    source_identity = PassiveCaptureSourceIdentity(
        checkpoint_source,
        capture_build,
        plan.capture_configuration_sha256,
        plan.capture_environment_sha256,
        plan.support_envelope_sha256,
    )
    return plan, source_identity


class _Detector:
    def __init__(self, identity: PassiveCaptureSourceIdentity) -> None:
        self._identity = identity.checkpoint_source.detector
        self._profile = identity.checkpoint_source.profile
        self.next_detection = CheckpointDetection(CheckpointMatchKind.UNKNOWN, (), 0.0)
        self.calls = 0

    @property
    def identity(self) -> CheckpointDetectorIdentity:
        return self._identity

    @property
    def profile(self) -> CheckpointProfile:
        return self._profile

    def detect(self, frame: Frame, /) -> CheckpointDetection:
        del frame
        self.calls += 1
        return self.next_detection


class _Clock:
    live_navigation_enabled = False
    input_authority = False

    def __init__(self, value: float = 0.0) -> None:
        self.value = value
        self.calls = 0

    def now_monotonic_s(self) -> float:
        self.calls += 1
        return self.value


class _Source:
    def __init__(self, identity: PassiveCaptureSourceIdentity, clock: _Clock) -> None:
        self._identity = identity
        self.clock = clock
        self.result_identity: PassiveCaptureSourceIdentity | None = None
        self.frame_id = 1
        self.frame_time = 1.0
        self.capture_id = "synthetic-durable-capture-1"
        self.captured_at_utc = "2026-09-01T00:00:01Z"
        self.payload_byte = 1
        self.calls = 0
        self.identity_calls = 0

    @property
    def identity(self) -> PassiveCaptureSourceIdentity:
        self.identity_calls += 1
        return self._identity

    def capture(self, request: PassiveCaptureRequest, /) -> PassiveSourceFrame:
        self.calls += 1
        frame = Frame(
            FrameRef(self.frame_id, self.frame_time, 2, 1),
            bytes([self.payload_byte]) * 8,
            PixelFormat.BGRA8888,
        )
        return PassiveSourceFrame(
            self.result_identity or self._identity,
            request.request_id,
            self.capture_id,
            self.captured_at_utc,
            frame,
        )


def _runtime(
    direction: RouteDirection = RouteDirection.MINE_TO_BANK,
    **plan_kwargs: str,
) -> tuple[RouteEvidenceCampaignPlan, _Source, _Detector, _Clock]:
    plan, identity = _campaign(direction, **plan_kwargs)
    clock = _Clock()
    source = _Source(identity, clock)
    detector = _Detector(identity)
    return plan, source, detector, clock


def _begin_acquisition(
    root: Path,
    direction: RouteDirection = RouteDirection.MINE_TO_BANK,
    **plan_kwargs: str,
) -> tuple[DurableAcquisitionTransaction, _Source, _Detector, _Clock]:
    plan, source, detector, clock = _runtime(direction, **plan_kwargs)
    transaction = begin_durable_acquisition(
        root,
        plan,
        source,
        detector,
        clock,
        started_monotonic_s=0.0,
    )
    return transaction, source, detector, clock


def _capture_next(
    transaction: DurableAcquisitionTransaction,
    source: _Source,
    detector: _Detector,
    clock: _Clock,
    ordinal: int,
) -> None:
    acknowledged = float(ordinal * 10)
    spec = transaction.progress.plan.cases[ordinal - 1]
    progress = transaction.request_capture(
        request_id=f"synthetic-durable-request-{ordinal}",
        operator_id=transaction.progress.plan.operator_id,
        acknowledged_monotonic_s=acknowledged,
    )
    assert progress.pending_request is not None
    source.frame_id = ordinal
    source.frame_time = acknowledged + 1.0
    source.capture_id = f"synthetic-durable-capture-{ordinal}"
    source.captured_at_utc = f"2026-09-01T00:00:{ordinal:02d}Z"
    source.payload_byte = ordinal
    detector.next_detection = CheckpointDetection(
        CheckpointMatchKind.MATCHED,
        (spec.checkpoint_id,),
        1.0,
    )
    clock.value = acknowledged + 2.0
    transaction.capture()


def _complete_acquisition(
    root: Path,
    direction: RouteDirection = RouteDirection.MINE_TO_BANK,
    **plan_kwargs: str,
) -> tuple[DurableAcquisitionReceipt, DurableAcquisitionTransaction]:
    transaction, source, detector, clock = _begin_acquisition(
        root,
        direction,
        **plan_kwargs,
    )
    assert not (root / FINALIZED_PACKAGE_FILENAME).exists()
    for ordinal in range(1, len(transaction.progress.plan.cases) + 1):
        _capture_next(transaction, source, detector, clock, ordinal)
    assert transaction.phase is DurableAcquisitionPhase.COMPLETE
    assert not (root / ACQUISITION_FINALIZATION_FILENAME).exists()
    assert not (root / FINALIZED_PACKAGE_FILENAME).exists()
    clock.value = 40.0
    receipt = transaction.finalize(finalized_at_utc="2026-09-01T00:00:10Z")
    return receipt, transaction


def _truths(acquisition: VerifiedDurableAcquisition) -> tuple[RouteEvidenceCaseTruth, ...]:
    return tuple(
        RouteEvidenceCaseTruth(
            case_id=spec.case_id,
            frame_sha256=owned.frame_artifact.sha256,
            detector_report_sha256=owned.detector_report_artifact.sha256,
            decision=RouteEvidenceReviewDecision.APPROVED,
            detection=CheckpointDetection(
                CheckpointMatchKind.MATCHED,
                (spec.checkpoint_id,),
                1.0,
            ),
        )
        for spec, owned in zip(
            acquisition.package.campaign_plan.cases,
            acquisition.package.cases,
            strict=True,
        )
    )


def _full_expectation(
    acquisition: DurableAcquisitionFilesystemExpectation,
    review: DurableReviewReceipt,
) -> DurableRouteEvidenceFilesystemExpectation:
    return DurableRouteEvidenceFilesystemExpectation(
        finalized_package_sha256=acquisition.finalized_package_sha256,
        acquisition_head_sha256=acquisition.acquisition_head_sha256,
        campaign_id=acquisition.campaign_id,
        route=acquisition.route,
        direction=acquisition.direction,
        route_plan_sha256=acquisition.route_plan_sha256,
        detector=acquisition.detector,
        profile=acquisition.profile,
        capture_source_id=acquisition.capture_source_id,
        capture_session_id=acquisition.capture_session_id,
        capture_build=acquisition.capture_build,
        frame_width=acquisition.frame_width,
        frame_height=acquisition.frame_height,
        pixel_format=acquisition.pixel_format,
        capture_configuration_sha256=acquisition.capture_configuration_sha256,
        capture_environment_sha256=acquisition.capture_environment_sha256,
        support_envelope_sha256=acquisition.support_envelope_sha256,
        independent_review_sha256=review.independent_review_sha256,
        reviewer_id=review.reviewer_id,
        acquisition_journal_head_sha256=acquisition.acquisition_journal_head_sha256,
        acquisition_finalization_sha256=acquisition.acquisition_finalization_sha256,
        review_id=review.review_id,
        review_plan_sha256=review.review_plan_sha256,
        review_journal_head_sha256=review.review_journal_head_sha256,
        review_finalization_sha256=review.review_finalization_sha256,
    )


def _begin_review(
    root: Path,
    acquisition_root: Path,
    receipt: DurableAcquisitionReceipt,
    *,
    review_id: str = "synthetic-independent-review-a",
    reviewer_id: str = "synthetic-independent-reviewer",
) -> tuple[DurableReviewTransaction, VerifiedDurableAcquisition]:
    acquisition = load_durable_acquisition(acquisition_root, receipt.expectation)
    transaction = begin_durable_review(
        root,
        acquisition_root,
        receipt.expectation,
        review_id=review_id,
        reviewer_id=reviewer_id,
        started_at_utc="2026-09-01T00:00:20Z",
    )
    return transaction, acquisition


def _complete_review(
    root: Path,
    acquisition_root: Path,
    receipt: DurableAcquisitionReceipt,
) -> tuple[DurableReviewReceipt, DurableRouteEvidenceFilesystemExpectation]:
    transaction, acquisition = _begin_review(root, acquisition_root, receipt)
    assert not (root / INDEPENDENT_REVIEW_FILENAME).exists()
    for ordinal, truth in enumerate(_truths(acquisition), start=1):
        transaction.record_case_truth(
            truth,
            recorded_at_utc=f"2026-09-01T00:00:{20 + ordinal:02d}Z",
        )
    assert transaction.phase is DurableReviewPhase.COMPLETE
    assert not (root / REVIEW_FINALIZATION_FILENAME).exists()
    assert not (root / INDEPENDENT_REVIEW_FILENAME).exists()
    review_receipt = transaction.finalize(reviewed_at_utc="2026-09-01T00:00:30Z")
    expectation = _full_expectation(receipt.expectation, review_receipt)
    return review_receipt, expectation


def _relative_files(root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}


@dataclass(slots=True)
class _ParentSwapProbe:
    swapped: bool = False
    foreign_file: Path | None = None
    parked_parent: Path | None = None


def _swap_owned_parent_during_path_open(
    monkeypatch: pytest.MonkeyPatch,
    *,
    target_root: Path,
    parked_parent: Path,
    matches: Callable[[Path], bool],
) -> _ParentSwapProbe:
    """Replace an owned parent in the exact check-to-path-open interval."""

    probe = _ParentSwapProbe()
    owned_root = target_root.absolute()
    parked = parked_parent.absolute()
    original_open = Path.open

    def swapping_open(path: Path, *args: object, **kwargs: object) -> object:
        candidate = path.absolute()
        if not probe.swapped and owned_root in candidate.parents and matches(candidate):
            candidate.parent.rename(parked)
            candidate.parent.mkdir()
            probe.swapped = True
            probe.foreign_file = candidate
            probe.parked_parent = parked
        return original_open(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", swapping_open)
    return probe


class _AlternatingPath:
    def __init__(self, first: Path, second: Path) -> None:
        self._values = (str(first), str(second))
        self.calls = 0

    def __fspath__(self) -> str:
        value = self._values[min(self.calls, 1)]
        self.calls += 1
        return value


def _expected(
    outcome: NavigationTransitionOutcome,
    phase: NavigationPhase,
    current_checkpoint_id: str | None,
    expected_next_checkpoint_id: str | None,
    *,
    failure_reason: NavigationFailureReason | None = None,
    proposed_step_id: str | None = None,
    proposed_attempt_id: str | None = None,
    proposed_prepared_monotonic_s: float | None = None,
    recorded_step_id: str | None = None,
    recorded_attempt_id: str | None = None,
    recorded_prepared_monotonic_s: float | None = None,
    recorded_post_attempt_monotonic_s: float | None = None,
) -> ReplayExpectedState:
    return ReplayExpectedState(
        outcome=outcome,
        phase=phase,
        current_checkpoint_id=current_checkpoint_id,
        expected_next_checkpoint_id=expected_next_checkpoint_id,
        failure_reason=failure_reason,
        proposed_step_id=proposed_step_id,
        proposed_attempt_id=proposed_attempt_id,
        proposed_prepared_monotonic_s=proposed_prepared_monotonic_s,
        recorded_step_id=recorded_step_id,
        recorded_attempt_id=recorded_attempt_id,
        recorded_prepared_monotonic_s=recorded_prepared_monotonic_s,
        recorded_post_attempt_monotonic_s=recorded_post_attempt_monotonic_s,
    )


def _replay_context(acquisition: VerifiedDurableAcquisition) -> RouteEvaluationContext:
    plan = acquisition.package.campaign_plan
    profile = CheckpointProfile(
        profile_id=plan.profile.profile_id,
        version=plan.profile.version,
        evidence_role=CheckpointEvidenceRole.SYNTHETIC_ARCHITECTURE_TEST_ONLY,
        frame_width=plan.frame_width,
        frame_height=plan.frame_height,
        pixel_format=plan.pixel_format,
        checkpoint_ids=tuple(
            checkpoint.checkpoint_id for checkpoint in plan.route_plan.checkpoints
        ),
    )
    assert profile.identity == plan.profile
    return RouteEvaluationContext(
        plan=plan.route_plan,
        expected_source=CheckpointSourceIdentity(
            detector=plan.detector,
            profile=profile,
            frame_source_id=plan.capture_source_id,
            capture_session_id=plan.capture_session_id,
        ),
        expected_attempt_source=StepAttemptSourceIdentity(
            source_id="synthetic-durable-replay-attempt-source",
            version="synthetic-v1",
            session_id=f"{plan.campaign_id}-attempt-source",
            evidence_role=AttemptEvidenceRole.SYNTHETIC_ARCHITECTURE_TEST_ONLY,
        ),
        policy=NavigationPolicy(
            max_frame_age_s=0.5,
            minimum_confidence=0.9,
            max_attempt_receipt_age_s=0.5,
        ),
    )


def _persisted_observations(
    acquisition: VerifiedDurableAcquisition,
    context: RouteEvaluationContext,
) -> tuple[CheckpointObservation, ...]:
    observations: list[CheckpointObservation] = []
    for owned in acquisition.package.cases:
        frame_payload = acquisition.artifacts[owned.frame_artifact.relative_path]
        report_payload = acquisition.artifacts[owned.detector_report_artifact.relative_path]
        detector_report = parse_synthetic_detector_report(report_payload)
        assert Sha256Digest.from_bytes(frame_payload) == owned.frame_artifact.sha256
        assert Sha256Digest.from_bytes(report_payload) == owned.detector_report_artifact.sha256
        assert detector_report.frame_sha256 == owned.frame_artifact.sha256
        assert detector_report.detection.candidate_checkpoint_ids == (
            acquisition.package.campaign_plan.cases[owned.sequence_index - 1].checkpoint_id,
        )
        observations.append(
            CheckpointObservation(
                route=acquisition.package.route,
                evidence=CheckpointEvidence(
                    provenance=FrameProvenance(
                        source=context.expected_source,
                        frame=detector_report.frame_ref,
                        pixel_format=detector_report.pixel_format,
                        frame_payload_sha256=owned.frame_artifact.sha256,
                    ),
                    detection=detector_report.detection,
                ),
            )
        )
    return tuple(observations)


def _passing_replay_manifest(
    acquisition: VerifiedDurableAcquisition,
) -> NavigationReplayManifest:
    context = _replay_context(acquisition)
    observations = _persisted_observations(acquisition, context)
    departure, transit, arrival = context.plan.checkpoints
    first_step, second_step = context.plan.steps
    first_attempt = "synthetic-durable-replay-attempt-1"
    second_attempt = "synthetic-durable-replay-attempt-2"
    first_prepared = 11.2
    second_prepared = 21.2
    first_receipt = SyntheticStepAttemptReceipt(
        identity=StepAttemptIdentity(context.plan.identity, first_step.step_id, first_attempt),
        source=context.expected_attempt_source,
        prepared_monotonic_s=first_prepared,
        post_attempt_monotonic_s=11.25,
    )
    second_receipt = SyntheticStepAttemptReceipt(
        identity=StepAttemptIdentity(context.plan.identity, second_step.step_id, second_attempt),
        source=context.expected_attempt_source,
        prepared_monotonic_s=second_prepared,
        post_attempt_monotonic_s=21.25,
    )
    return NavigationReplayManifest(
        schema_version=NAVIGATION_REPLAY_SCHEMA_VERSION,
        fixture_role=SYNTHETIC_FIXTURE_ROLE,
        case_id=f"{acquisition.package.campaign_plan.campaign_id}-causal-replay",
        started_monotonic_s=10.9,
        context=context,
        events=(
            ObserveCheckpointEvent(
                evaluated_monotonic_s=11.1,
                observation=observations[0],
                expected=_expected(
                    NavigationTransitionOutcome.CHECKPOINT_ACCEPTED,
                    NavigationPhase.READY_FOR_STEP,
                    departure.checkpoint_id,
                    transit.checkpoint_id,
                ),
            ),
            PrepareStepEvent(
                evaluated_monotonic_s=first_prepared,
                attempt_id=first_attempt,
                expected=_expected(
                    NavigationTransitionOutcome.STEP_PREPARED,
                    NavigationPhase.AWAITING_ATTEMPT_RECEIPT,
                    None,
                    transit.checkpoint_id,
                    proposed_step_id=first_step.step_id,
                    proposed_attempt_id=first_attempt,
                    proposed_prepared_monotonic_s=first_prepared,
                ),
            ),
            RecordStepAttemptReceiptEvent(
                evaluated_monotonic_s=11.3,
                receipt=first_receipt,
                expected=_expected(
                    NavigationTransitionOutcome.STEP_ATTEMPT_RECORDED,
                    NavigationPhase.AWAITING_CHECKPOINT,
                    None,
                    transit.checkpoint_id,
                    recorded_step_id=first_step.step_id,
                    recorded_attempt_id=first_attempt,
                    recorded_prepared_monotonic_s=first_prepared,
                    recorded_post_attempt_monotonic_s=first_receipt.post_attempt_monotonic_s,
                ),
            ),
            ObserveCheckpointEvent(
                evaluated_monotonic_s=21.1,
                observation=observations[1],
                expected=_expected(
                    NavigationTransitionOutcome.CHECKPOINT_ACCEPTED,
                    NavigationPhase.READY_FOR_STEP,
                    transit.checkpoint_id,
                    arrival.checkpoint_id,
                ),
            ),
            PrepareStepEvent(
                evaluated_monotonic_s=second_prepared,
                attempt_id=second_attempt,
                expected=_expected(
                    NavigationTransitionOutcome.STEP_PREPARED,
                    NavigationPhase.AWAITING_ATTEMPT_RECEIPT,
                    None,
                    arrival.checkpoint_id,
                    proposed_step_id=second_step.step_id,
                    proposed_attempt_id=second_attempt,
                    proposed_prepared_monotonic_s=second_prepared,
                ),
            ),
            RecordStepAttemptReceiptEvent(
                evaluated_monotonic_s=21.3,
                receipt=second_receipt,
                expected=_expected(
                    NavigationTransitionOutcome.STEP_ATTEMPT_RECORDED,
                    NavigationPhase.AWAITING_CHECKPOINT,
                    None,
                    arrival.checkpoint_id,
                    recorded_step_id=second_step.step_id,
                    recorded_attempt_id=second_attempt,
                    recorded_prepared_monotonic_s=second_prepared,
                    recorded_post_attempt_monotonic_s=second_receipt.post_attempt_monotonic_s,
                ),
            ),
            ObserveCheckpointEvent(
                evaluated_monotonic_s=31.1,
                observation=observations[2],
                expected=_expected(
                    NavigationTransitionOutcome.ARRIVAL_CONFIRMED,
                    NavigationPhase.ARRIVED,
                    arrival.checkpoint_id,
                    None,
                ),
            ),
        ),
        expected_final_phase=NavigationPhase.ARRIVED,
    )


@pytest.mark.parametrize("direction", tuple(RouteDirection))
def test_durable_acquisition_and_separate_review_pass_for_each_direction(
    tmp_path: Path,
    direction: RouteDirection,
) -> None:
    acquisition_root = tmp_path / f"acquisition-{direction.value}"
    review_root = tmp_path / f"review-{direction.value}"
    receipt, transaction = _complete_acquisition(acquisition_root, direction)
    review_receipt, expectation = _complete_review(
        review_root,
        acquisition_root,
        receipt,
    )

    report = load_and_verify_durable_synthetic_route_evidence(
        acquisition_root,
        review_root,
        expectation,
    )

    assert transaction.phase is DurableAcquisitionPhase.FINALIZED
    assert report.evidence_conformance_passed is True
    assert report.route.direction is direction
    assert report.endpoint.route_arrival_verified is True
    assert report.endpoint.supported_mining_view_proven is False
    assert report.endpoint.bank_interface_open_proven is False
    assert receipt.activation_allowed is False and receipt.input_authority is False
    assert review_receipt.activation_allowed is False and review_receipt.input_authority is False
    assert report.real_release_role_satisfied is False
    assert report.live_navigation_enabled is False
    assert report.activation_allowed is False and report.input_authority is False
    assert INDEPENDENT_REVIEW_FILENAME not in _relative_files(acquisition_root)
    assert FINALIZED_PACKAGE_FILENAME not in _relative_files(review_root)


def test_verified_persisted_artifacts_drive_deterministic_post_attempt_replay(
    tmp_path: Path,
) -> None:
    acquisition_root = tmp_path / "acquisition"
    review_root = tmp_path / "review"
    receipt, _ = _complete_acquisition(acquisition_root)
    _, expectation = _complete_review(review_root, acquisition_root, receipt)
    evidence_report = load_and_verify_durable_synthetic_route_evidence(
        acquisition_root,
        review_root,
        expectation,
    )
    assert evidence_report.evidence_conformance_passed is True
    acquisition = load_durable_acquisition(acquisition_root, receipt.expectation)
    manifest = _passing_replay_manifest(acquisition)

    first = run_navigation_replay(manifest)
    second = run_navigation_replay(manifest)

    assert first == second
    assert first.passed is True
    assert first.final_progress.phase is NavigationPhase.ARRIVED
    assert first.live_navigation_enabled is False
    assert all(proposal.live_input_enabled is False for proposal in first.step_proposals)
    assert all(
        attempt.receipt.authoritative is False
        and attempt.receipt.movement_success_proven is False
        and attempt.receipt.live_input_enabled is False
        for attempt in first.completed_attempts
    )
    assert tuple(
        attempt.proposal.checkpoint_evidence.provenance.frame_payload_sha256
        for attempt in first.completed_attempts
    ) == tuple(owned.frame_artifact.sha256 for owned in acquisition.package.cases[:2])
    assert first.final_progress.arrival_evidence is not None
    assert (
        first.final_progress.arrival_evidence.observation.provenance.frame_payload_sha256
        == acquisition.package.cases[-1].frame_artifact.sha256
    )


@pytest.mark.parametrize(
    ("failure", "reason"),
    (
        ("missing-receipt", NavigationFailureReason.ATTEMPT_RECEIPT_REQUIRED),
        ("foreign-receipt", NavigationFailureReason.ATTEMPT_ID_MISMATCH),
        ("frame-at-receipt-boundary", NavigationFailureReason.EVIDENCE_NOT_AFTER_BOUNDARY),
    ),
)
def test_persisted_causality_replay_stops_for_missing_foreign_or_nonfresh_evidence(
    tmp_path: Path,
    failure: str,
    reason: NavigationFailureReason,
) -> None:
    acquisition_root = tmp_path / "acquisition"
    review_root = tmp_path / "review"
    receipt, _ = _complete_acquisition(acquisition_root)
    _, expectation = _complete_review(review_root, acquisition_root, receipt)
    verified = load_and_verify_durable_synthetic_route_evidence(
        acquisition_root,
        review_root,
        expectation,
    )
    assert verified.evidence_conformance_passed is True
    acquisition = load_durable_acquisition(acquisition_root, receipt.expectation)
    passing = _passing_replay_manifest(acquisition)
    record = passing.events[2]
    assert isinstance(record, RecordStepAttemptReceiptEvent)
    assert record.receipt is not None

    if failure in {"missing-receipt", "foreign-receipt"}:
        failed_receipt = None
        if failure == "foreign-receipt":
            failed_receipt = replace(
                record.receipt,
                identity=replace(
                    record.receipt.identity,
                    attempt_id="synthetic-foreign-attempt",
                ),
            )
        failed_event = RecordStepAttemptReceiptEvent(
            evaluated_monotonic_s=record.evaluated_monotonic_s,
            receipt=failed_receipt,
            expected=_expected(
                NavigationTransitionOutcome.STOPPED,
                NavigationPhase.STOPPED,
                None,
                None,
                failure_reason=reason,
            ),
        )
        events = (*passing.events[:2], failed_event)
    else:
        boundary_receipt = replace(record.receipt, post_attempt_monotonic_s=21.0)
        recorded = RecordStepAttemptReceiptEvent(
            evaluated_monotonic_s=21.1,
            receipt=boundary_receipt,
            expected=_expected(
                NavigationTransitionOutcome.STEP_ATTEMPT_RECORDED,
                NavigationPhase.AWAITING_CHECKPOINT,
                None,
                passing.context.plan.checkpoints[1].checkpoint_id,
                recorded_step_id=boundary_receipt.identity.step_id,
                recorded_attempt_id=boundary_receipt.identity.attempt_id,
                recorded_prepared_monotonic_s=boundary_receipt.prepared_monotonic_s,
                recorded_post_attempt_monotonic_s=boundary_receipt.post_attempt_monotonic_s,
            ),
        )
        persisted_transit = passing.events[3]
        assert isinstance(persisted_transit, ObserveCheckpointEvent)
        assert (
            persisted_transit.observation.provenance.frame.captured_monotonic_s
            == boundary_receipt.post_attempt_monotonic_s
        )
        rejected = ObserveCheckpointEvent(
            evaluated_monotonic_s=21.2,
            observation=persisted_transit.observation,
            expected=_expected(
                NavigationTransitionOutcome.STOPPED,
                NavigationPhase.STOPPED,
                None,
                None,
                failure_reason=reason,
            ),
        )
        events = (*passing.events[:2], recorded, rejected)

    failed_manifest = replace(
        passing,
        case_id=f"{passing.case_id}-{failure}",
        events=events,
        expected_final_phase=NavigationPhase.STOPPED,
    )
    report = run_navigation_replay(failed_manifest)

    assert report.passed is True
    assert report.final_progress.phase is NavigationPhase.STOPPED
    assert report.final_progress.failure_reason is reason
    assert report.live_navigation_enabled is False
    assert all(proposal.live_input_enabled is False for proposal in report.step_proposals)
    assert all(
        attempt.receipt.authoritative is False
        and attempt.receipt.movement_success_proven is False
        and attempt.receipt.live_input_enabled is False
        for attempt in report.completed_attempts
    )


def test_review_cannot_begin_before_acquisition_finalization(tmp_path: Path) -> None:
    reference_receipt, _ = _complete_acquisition(tmp_path / "reference")
    partial_root = tmp_path / "partial-acquisition"
    _begin_acquisition(partial_root)
    review_root = tmp_path / "premature-review"

    with pytest.raises(RouteEvidenceIntegrityError):
        begin_durable_review(
            review_root,
            partial_root,
            reference_receipt.expectation,
            review_id="synthetic-premature-review",
            reviewer_id="synthetic-independent-reviewer",
            started_at_utc="2026-09-01T00:00:20Z",
        )

    assert not review_root.exists()
    assert (partial_root / ACQUISITION_PLAN_FILENAME).is_file()
    assert not (partial_root / FINALIZED_PACKAGE_FILENAME).exists()


def test_operator_cannot_be_the_independent_reviewer(tmp_path: Path) -> None:
    acquisition_root = tmp_path / "acquisition"
    receipt, _ = _complete_acquisition(acquisition_root)
    review_root = tmp_path / "operator-review"

    with pytest.raises(RouteEvidenceIntegrityError, match="reviewer must differ"):
        begin_durable_review(
            review_root,
            acquisition_root,
            receipt.expectation,
            review_id="synthetic-operator-review",
            reviewer_id="SYNTHETIC-DURABLE-OPERATOR",
            started_at_utc="2026-09-01T00:00:20Z",
        )

    assert not review_root.exists()


@pytest.mark.parametrize("failure", ("out-of-order", "repeated-truth", "repeated-time"))
def test_review_truth_must_be_exactly_ordered_fresh_and_single_use(
    tmp_path: Path,
    failure: str,
) -> None:
    acquisition_root = tmp_path / "acquisition"
    receipt, _ = _complete_acquisition(acquisition_root)
    review_root = tmp_path / f"review-{failure}"
    transaction, acquisition = _begin_review(review_root, acquisition_root, receipt)
    truths = _truths(acquisition)

    if failure == "out-of-order":
        with pytest.raises(ValueError, match="exact next case"):
            transaction.record_case_truth(
                truths[1],
                recorded_at_utc="2026-09-01T00:00:21Z",
            )
    else:
        transaction.record_case_truth(
            truths[0],
            recorded_at_utc="2026-09-01T00:00:21Z",
        )
        if failure == "repeated-truth":
            with pytest.raises(ValueError, match="exact next case"):
                transaction.record_case_truth(
                    truths[0],
                    recorded_at_utc="2026-09-01T00:00:22Z",
                )
        else:
            with pytest.raises(ValueError, match="chronology"):
                transaction.record_case_truth(
                    truths[1],
                    recorded_at_utc="2026-09-01T00:00:21Z",
                )

    assert transaction.phase is DurableReviewPhase.STOPPED
    assert (review_root / REVIEW_STOP_FILENAME).is_file()
    assert not (review_root / REVIEW_FINALIZATION_FILENAME).exists()
    assert not (review_root / INDEPENDENT_REVIEW_FILENAME).exists()
    with pytest.raises(DurableEvidenceStateError, match="terminal"):
        transaction.finalize(reviewed_at_utc="2026-09-01T00:00:30Z")


def test_late_capture_write_failure_retains_nonreviewable_partial_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acquisition_root = tmp_path / "partial-acquisition"
    transaction, source, detector, clock = _begin_acquisition(acquisition_root)
    transaction.request_capture(
        request_id="synthetic-durable-request-1",
        operator_id=transaction.progress.plan.operator_id,
        acknowledged_monotonic_s=10.0,
    )
    spec = transaction.progress.plan.cases[0]
    source.frame_time = 11.0
    source.captured_at_utc = "2026-09-01T00:00:01Z"
    detector.next_detection = CheckpointDetection(
        CheckpointMatchKind.MATCHED,
        (spec.checkpoint_id,),
        1.0,
    )
    clock.value = 12.0
    original_write = durable_module._ExclusiveNamespace.write

    def fail_detector_report(
        namespace: durable_module._ExclusiveNamespace,
        relative_path: str,
        payload: bytes,
        *,
        max_bytes: int = durable_module._MAX_MANIFEST_BYTES,
    ) -> Sha256Digest:
        if relative_path.endswith("/detector-report.json"):
            raise OSError("synthetic late detector-report write failure")
        return original_write(
            namespace,
            relative_path,
            payload,
            max_bytes=max_bytes,
        )

    monkeypatch.setattr(durable_module._ExclusiveNamespace, "write", fail_detector_report)

    with pytest.raises(OSError, match="late detector-report"):
        transaction.capture()

    files = _relative_files(acquisition_root)
    assert transaction.phase is DurableAcquisitionPhase.STOPPED
    assert ACQUISITION_PLAN_FILENAME in files
    assert ACQUISITION_STOP_FILENAME in files
    assert any(path.endswith("/frame.bin") for path in files)
    assert not any(path.endswith("/detector-report.json") for path in files)
    assert FINALIZED_PACKAGE_FILENAME not in files
    assert ACQUISITION_FINALIZATION_FILENAME not in files
    with pytest.raises(DurableEvidenceStateError, match="terminal"):
        transaction.capture()


def test_two_concurrent_writers_claim_exactly_one_fresh_root(tmp_path: Path) -> None:
    root = tmp_path / "contended-acquisition"
    barrier = Barrier(2)

    def claim_writer(writer_id: int) -> tuple[str, int, int]:
        plan, source, detector, clock = _runtime(
            campaign_id=f"synthetic-writer-{writer_id}",
            capture_session_id=f"synthetic-writer-session-{writer_id}",
        )
        barrier.wait()
        try:
            begin_durable_acquisition(
                root,
                plan,
                source,
                detector,
                clock,
                started_monotonic_s=0.0,
            )
        except DurableEvidenceCollisionError:
            return "collision", source.identity_calls, clock.calls
        return "winner", source.identity_calls, clock.calls

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(claim_writer, (1, 2)))

    assert sorted(item[0] for item in outcomes) == ["collision", "winner"]
    loser = next(item for item in outcomes if item[0] == "collision")
    winner = next(item for item in outcomes if item[0] == "winner")
    assert loser[1:] == (0, 0)
    assert winner[1] > 0 and winner[2] > 0
    assert (root / ACQUISITION_PLAN_FILENAME).is_file()
    assert not (root / FINALIZED_PACKAGE_FILENAME).exists()


def test_foreign_preclaimed_root_and_sentinel_are_never_adopted_or_removed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "foreign-acquisition"
    root.mkdir()
    sentinel = root / "foreign-sentinel.bin"
    sentinel.write_bytes(b"foreign-owner")
    before = _relative_files(root)
    plan, source, detector, clock = _runtime()

    with pytest.raises(DurableEvidenceCollisionError, match="already claimed"):
        begin_durable_acquisition(
            root,
            plan,
            source,
            detector,
            clock,
            started_monotonic_s=0.0,
        )

    assert _relative_files(root) == before
    assert sentinel.read_bytes() == b"foreign-owner"


def test_acquisition_manifest_collision_preserves_foreign_bytes_and_stops(
    tmp_path: Path,
) -> None:
    root = tmp_path / "acquisition"
    transaction, source, detector, clock = _begin_acquisition(root)
    for ordinal in range(1, len(transaction.progress.plan.cases) + 1):
        _capture_next(transaction, source, detector, clock, ordinal)
    sentinel = root / ACQUISITION_FINALIZATION_FILENAME
    sentinel.write_bytes(b"foreign-acquisition-finalization")
    clock.value = 40.0

    with pytest.raises(RouteEvidenceIntegrityError, match="foreign_files"):
        transaction.finalize(finalized_at_utc="2026-09-01T00:00:10Z")

    assert transaction.phase is DurableAcquisitionPhase.STOPPED
    assert sentinel.read_bytes() == b"foreign-acquisition-finalization"
    assert (root / FINALIZED_PACKAGE_FILENAME).is_file()
    assert (root / ACQUISITION_STOP_FILENAME).is_file()


def test_review_manifest_collision_preserves_foreign_bytes_and_stops(tmp_path: Path) -> None:
    acquisition_root = tmp_path / "acquisition"
    receipt, _ = _complete_acquisition(acquisition_root)
    review_root = tmp_path / "review"
    transaction, acquisition = _begin_review(review_root, acquisition_root, receipt)
    for ordinal, truth in enumerate(_truths(acquisition), start=1):
        transaction.record_case_truth(
            truth,
            recorded_at_utc=f"2026-09-01T00:00:{20 + ordinal:02d}Z",
        )
    sentinel = review_root / REVIEW_FINALIZATION_FILENAME
    sentinel.write_bytes(b"foreign-review-finalization")

    with pytest.raises(RouteEvidenceIntegrityError, match="foreign_files"):
        transaction.finalize(reviewed_at_utc="2026-09-01T00:00:30Z")

    assert transaction.phase is DurableReviewPhase.STOPPED
    assert sentinel.read_bytes() == b"foreign-review-finalization"
    assert (review_root / INDEPENDENT_REVIEW_FILENAME).is_file()
    assert (review_root / REVIEW_STOP_FILENAME).is_file()


@pytest.mark.parametrize("drift", ("reverse-route", "source", "campaign"))
def test_loader_rejects_reversed_route_and_caller_owned_identity_drift(
    tmp_path: Path,
    drift: str,
) -> None:
    acquisition_root = tmp_path / "acquisition"
    review_root = tmp_path / "review"
    receipt, _ = _complete_acquisition(acquisition_root)
    _, expectation = _complete_review(review_root, acquisition_root, receipt)

    if drift == "reverse-route":
        reverse = RouteDirection.BANK_TO_MINE
        foreign = replace(
            expectation,
            route=RouteIdentity(
                expectation.route.route_id,
                expectation.route.version,
                reverse,
            ),
            direction=reverse,
        )
    elif drift == "source":
        foreign = replace(expectation, capture_source_id="foreign-capture-source")
    else:
        foreign = replace(expectation, campaign_id="foreign-campaign")

    with pytest.raises(RouteEvidenceIntegrityError, match="identity pins"):
        load_and_verify_durable_synthetic_route_evidence(
            acquisition_root,
            review_root,
            foreign,
        )


def test_runtime_source_identity_drift_stops_without_persisting_case(tmp_path: Path) -> None:
    root = tmp_path / "acquisition"
    transaction, source, detector, clock = _begin_acquisition(root)
    _, foreign_identity = _campaign(
        campaign_id="synthetic-foreign-campaign",
        capture_session_id="synthetic-foreign-session",
    )
    source.result_identity = foreign_identity
    transaction.request_capture(
        request_id="synthetic-durable-request-1",
        operator_id=transaction.progress.plan.operator_id,
        acknowledged_monotonic_s=10.0,
    )
    source.frame_time = 11.0
    source.captured_at_utc = "2026-09-01T00:00:01Z"
    detector.next_detection = CheckpointDetection(
        CheckpointMatchKind.MATCHED,
        (transaction.progress.plan.cases[0].checkpoint_id,),
        1.0,
    )
    clock.value = 12.0

    progress = transaction.capture()

    assert progress.phase is PassiveCampaignPhase.STOPPED
    assert transaction.phase is DurableAcquisitionPhase.STOPPED
    assert (root / ACQUISITION_STOP_FILENAME).is_file()
    assert not any(path.startswith("cases/") for path in _relative_files(root))
    assert not (root / FINALIZED_PACKAGE_FILENAME).exists()


def test_persisted_artifact_mutation_invalidates_previously_verified_transaction(
    tmp_path: Path,
) -> None:
    acquisition_root = tmp_path / "acquisition"
    review_root = tmp_path / "review"
    receipt, _ = _complete_acquisition(acquisition_root)
    _, expectation = _complete_review(review_root, acquisition_root, receipt)
    acquisition = load_durable_acquisition(acquisition_root, receipt.expectation)
    relative = acquisition.package.cases[0].frame_artifact.relative_path
    target = acquisition_root.joinpath(*relative.split("/"))
    payload = target.read_bytes()
    target.write_bytes(bytes([payload[0] ^ 0xFF]) + payload[1:])

    with pytest.raises(RouteEvidenceIntegrityError):
        load_and_verify_durable_synthetic_route_evidence(
            acquisition_root,
            review_root,
            expectation,
        )


def test_loader_deterministically_rejects_reparse_alias_without_platform_skip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acquisition_root = tmp_path / "acquisition"
    review_root = tmp_path / "review"
    receipt, _ = _complete_acquisition(acquisition_root)
    _, expectation = _complete_review(review_root, acquisition_root, receipt)
    acquisition = load_durable_acquisition(acquisition_root, receipt.expectation)
    relative = acquisition.package.cases[0].frame_artifact.relative_path
    target = acquisition_root.joinpath(*relative.split("/")).absolute()
    original_lstat = loader_module._lstat

    def simulated_reparse(path: Path, context: str) -> os.stat_result:
        if path.absolute() == target:
            raise RouteEvidenceIntegrityError(f"{context} is a symlink or reparse point")
        return original_lstat(path, context)

    monkeypatch.setattr(loader_module, "_lstat", simulated_reparse)

    with pytest.raises(RouteEvidenceIntegrityError, match="symlink or reparse"):
        load_and_verify_durable_synthetic_route_evidence(
            acquisition_root,
            review_root,
            expectation,
        )


def test_loader_rejects_hardlink_alias_to_foreign_bytes(tmp_path: Path) -> None:
    acquisition_root = tmp_path / "acquisition"
    review_root = tmp_path / "review"
    receipt, _ = _complete_acquisition(acquisition_root)
    _, expectation = _complete_review(review_root, acquisition_root, receipt)
    acquisition = load_durable_acquisition(acquisition_root, receipt.expectation)
    relative = acquisition.package.cases[0].frame_artifact.relative_path
    target = acquisition_root.joinpath(*relative.split("/"))
    outside = tmp_path / "foreign-owned-frame.bgra"
    outside.write_bytes(target.read_bytes())
    target.unlink()
    os.link(outside, target)

    with pytest.raises(RouteEvidenceIntegrityError, match="hard-link"):
        load_and_verify_durable_synthetic_route_evidence(
            acquisition_root,
            review_root,
            expectation,
        )


def test_writer_contract_requires_trusted_parent_and_is_not_real_evidence_eligible() -> None:
    assert DURABLE_WRITER_NAMESPACE_CONTRACT == "trusted_non_hostile_dedicated_parent_namespace_v1"
    assert DURABLE_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE is False
    assert "DURABLE_WRITER_NAMESPACE_CONTRACT" not in navigation_root.__all__
    assert "DURABLE_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE" not in navigation_root.__all__


def test_acquisition_parent_swap_at_path_open_cannot_finalize_or_verify(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control_root = tmp_path / "control-acquisition"
    control_receipt, _ = _complete_acquisition(control_root)
    target_root = (tmp_path / "swapped-acquisition").absolute()
    transaction, source, detector, clock = _begin_acquisition(target_root)
    probe = _swap_owned_parent_during_path_open(
        monkeypatch,
        target_root=target_root,
        parked_parent=tmp_path / "parked-acquisition-case-parent",
        matches=lambda path: path.name == "frame.bin",
    )

    with pytest.raises(DurableEvidenceError, match="owned durable directory was replaced"):
        _capture_next(transaction, source, detector, clock, 1)

    assert probe.swapped is True
    assert probe.foreign_file is not None and probe.foreign_file.is_file()
    assert probe.parked_parent is not None and probe.parked_parent.is_dir()
    assert transaction.phase is DurableAcquisitionPhase.STOPPED
    assert not (target_root / FINALIZED_PACKAGE_FILENAME).exists()
    assert not (target_root / ACQUISITION_FINALIZATION_FILENAME).exists()
    with pytest.raises(DurableEvidenceStateError, match="terminal"):
        transaction.finalize(finalized_at_utc="2026-09-01T00:00:10Z")
    with pytest.raises(RouteEvidenceIntegrityError):
        load_durable_acquisition(target_root, control_receipt.expectation)


def test_review_parent_swap_at_path_open_cannot_finalize_or_verify(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acquisition_root = tmp_path / "acquisition"
    receipt, _ = _complete_acquisition(acquisition_root)
    _, control_expectation = _complete_review(
        tmp_path / "control-review",
        acquisition_root,
        receipt,
    )
    review_root = (tmp_path / "swapped-review").absolute()
    transaction, acquisition = _begin_review(review_root, acquisition_root, receipt)
    probe = _swap_owned_parent_during_path_open(
        monkeypatch,
        target_root=review_root,
        parked_parent=tmp_path / "parked-review-truth-parent",
        matches=lambda path: path.parent.name == "truth" and path.suffix == ".json",
    )

    with pytest.raises(DurableEvidenceError, match="owned durable directory was replaced"):
        transaction.record_case_truth(
            _truths(acquisition)[0],
            recorded_at_utc="2026-09-01T00:00:21Z",
        )

    assert probe.swapped is True
    assert probe.foreign_file is not None and probe.foreign_file.is_file()
    assert probe.parked_parent is not None and probe.parked_parent.is_dir()
    assert transaction.phase is DurableReviewPhase.STOPPED
    assert not (review_root / INDEPENDENT_REVIEW_FILENAME).exists()
    assert not (review_root / REVIEW_FINALIZATION_FILENAME).exists()
    with pytest.raises(DurableEvidenceStateError, match="terminal"):
        transaction.finalize(reviewed_at_utc="2026-09-01T00:00:30Z")
    with pytest.raises(RouteEvidenceIntegrityError):
        load_and_verify_durable_synthetic_route_evidence(
            acquisition_root,
            review_root,
            control_expectation,
        )


def test_terminal_manifests_are_the_last_successful_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: dict[Path, list[str]] = {}
    original_write = durable_module._ExclusiveNamespace.write

    def record_write(
        namespace: durable_module._ExclusiveNamespace,
        relative_path: str,
        payload: bytes,
        *,
        max_bytes: int = durable_module._MAX_MANIFEST_BYTES,
    ) -> Sha256Digest:
        result = original_write(
            namespace,
            relative_path,
            payload,
            max_bytes=max_bytes,
        )
        writes.setdefault(namespace.root, []).append(relative_path)
        return result

    monkeypatch.setattr(durable_module._ExclusiveNamespace, "write", record_write)
    acquisition_root = (tmp_path / "acquisition").absolute()
    review_root = (tmp_path / "review").absolute()
    acquisition_receipt, _ = _complete_acquisition(acquisition_root)
    _complete_review(review_root, acquisition_root, acquisition_receipt)

    assert writes[acquisition_root][-2:] == [
        FINALIZED_PACKAGE_FILENAME,
        ACQUISITION_FINALIZATION_FILENAME,
    ]
    assert writes[review_root][-2:] == [
        INDEPENDENT_REVIEW_FILENAME,
        REVIEW_FINALIZATION_FILENAME,
    ]


def test_exception_after_terminal_manifest_never_appends_a_stop_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "acquisition"
    transaction, source, detector, clock = _begin_acquisition(root)
    for ordinal in range(1, len(transaction.progress.plan.cases) + 1):
        _capture_next(transaction, source, detector, clock, ordinal)
    clock.value = 40.0
    original_write = durable_module._ExclusiveNamespace.write
    writes: list[str] = []

    def fail_after_terminal_write(
        namespace: durable_module._ExclusiveNamespace,
        relative_path: str,
        payload: bytes,
        *,
        max_bytes: int = durable_module._MAX_MANIFEST_BYTES,
    ) -> Sha256Digest:
        result = original_write(
            namespace,
            relative_path,
            payload,
            max_bytes=max_bytes,
        )
        writes.append(relative_path)
        if relative_path == ACQUISITION_FINALIZATION_FILENAME:
            raise OSError("synthetic post-terminal acknowledgement loss")
        return result

    monkeypatch.setattr(
        durable_module._ExclusiveNamespace,
        "write",
        fail_after_terminal_write,
    )

    with pytest.raises(OSError, match="acknowledgement loss"):
        transaction.finalize(finalized_at_utc="2026-09-01T00:00:10Z")

    assert transaction.phase is DurableAcquisitionPhase.STOPPED
    assert writes[-1] == ACQUISITION_FINALIZATION_FILENAME
    assert (root / ACQUISITION_FINALIZATION_FILENAME).is_file()
    assert not (root / ACQUISITION_STOP_FILENAME).exists()


def test_same_content_replacement_before_finalization_is_not_adopted(tmp_path: Path) -> None:
    root = tmp_path / "acquisition"
    transaction, source, detector, clock = _begin_acquisition(root)
    for ordinal in range(1, len(transaction.progress.plan.cases) + 1):
        _capture_next(transaction, source, detector, clock, ordinal)
    first = transaction.progress.captures[0].owned_case.frame_artifact.relative_path
    target = root.joinpath(*first.split("/"))
    identical = target.read_bytes()
    target.unlink()
    target.write_bytes(identical)
    clock.value = 40.0

    with pytest.raises(DurableEvidenceError, match="owned durable file was replaced"):
        transaction.finalize(finalized_at_utc="2026-09-01T00:00:10Z")

    assert transaction.phase is DurableAcquisitionPhase.STOPPED
    assert not (root / ACQUISITION_FINALIZATION_FILENAME).exists()
    assert not (root / FINALIZED_PACKAGE_FILENAME).exists()


def test_same_content_acquisition_replacement_after_review_begin_invalidates_review(
    tmp_path: Path,
) -> None:
    acquisition_root = tmp_path / "acquisition"
    review_root = tmp_path / "review"
    receipt, _ = _complete_acquisition(acquisition_root)
    transaction, acquisition = _begin_review(review_root, acquisition_root, receipt)
    first = acquisition.package.cases[0].frame_artifact.relative_path
    target = acquisition_root.joinpath(*first.split("/"))
    identical = target.read_bytes()
    target.unlink()
    target.write_bytes(identical)
    for ordinal, truth in enumerate(_truths(acquisition), start=1):
        transaction.record_case_truth(
            truth,
            recorded_at_utc=f"2026-09-01T00:00:{20 + ordinal:02d}Z",
        )

    with pytest.raises(RouteEvidenceIntegrityError, match="filesystem identity changed"):
        transaction.finalize(reviewed_at_utc="2026-09-01T00:00:30Z")

    assert transaction.phase is DurableReviewPhase.STOPPED
    assert (review_root / REVIEW_STOP_FILENAME).is_file()
    assert not (review_root / REVIEW_FINALIZATION_FILENAME).exists()


def test_stateful_pathlike_values_are_snapshotted_once_per_public_operation(
    tmp_path: Path,
) -> None:
    acquisition_root = tmp_path / "acquisition"
    review_root = tmp_path / "review"
    receipt, _ = _complete_acquisition(acquisition_root)
    acquisition = load_durable_acquisition(acquisition_root, receipt.expectation)
    alternating_acquisition = _AlternatingPath(
        acquisition_root,
        tmp_path / "foreign-acquisition",
    )
    transaction = begin_durable_review(
        review_root,
        alternating_acquisition,
        receipt.expectation,
        review_id="synthetic-stateful-review",
        reviewer_id="synthetic-stateful-reviewer",
        started_at_utc="2026-09-01T00:00:20Z",
    )
    assert alternating_acquisition.calls == 1
    for ordinal, truth in enumerate(_truths(acquisition), start=1):
        transaction.record_case_truth(
            truth,
            recorded_at_utc=f"2026-09-01T00:00:{20 + ordinal:02d}Z",
        )
    review_receipt = transaction.finalize(reviewed_at_utc="2026-09-01T00:00:30Z")
    assert alternating_acquisition.calls == 1
    expectation = _full_expectation(receipt.expectation, review_receipt)
    intake_acquisition = _AlternatingPath(acquisition_root, tmp_path / "foreign-a")
    intake_review = _AlternatingPath(review_root, tmp_path / "foreign-r")

    report = load_and_verify_durable_synthetic_route_evidence(
        intake_acquisition,
        intake_review,
        expectation,
    )

    assert report.evidence_conformance_passed is True
    assert intake_acquisition.calls == 1
    assert intake_review.calls == 1


def test_nested_review_root_is_rejected_before_reservation(tmp_path: Path) -> None:
    acquisition_root = tmp_path / "acquisition"
    receipt, _ = _complete_acquisition(acquisition_root)
    nested_review = acquisition_root / "review"

    with pytest.raises(RouteEvidenceIntegrityError, match="physically disjoint"):
        begin_durable_review(
            nested_review,
            acquisition_root,
            receipt.expectation,
            review_id="synthetic-nested-review",
            reviewer_id="synthetic-independent-reviewer",
            started_at_utc="2026-09-01T00:00:20Z",
        )

    assert not nested_review.exists()


def test_two_concurrent_review_writers_claim_exactly_one_fresh_root(tmp_path: Path) -> None:
    acquisition_root = tmp_path / "acquisition"
    review_root = tmp_path / "contended-review"
    receipt, _ = _complete_acquisition(acquisition_root)
    barrier = Barrier(2)

    def claim_review(writer_id: int) -> str:
        barrier.wait()
        try:
            begin_durable_review(
                review_root,
                acquisition_root,
                receipt.expectation,
                review_id=f"synthetic-review-writer-{writer_id}",
                reviewer_id=f"synthetic-reviewer-{writer_id}",
                started_at_utc="2026-09-01T00:00:20Z",
            )
        except DurableEvidenceCollisionError:
            return "collision"
        return "winner"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(claim_review, (1, 2)))

    assert sorted(outcomes) == ["collision", "winner"]
    assert (review_root / durable_module.REVIEW_PLAN_FILENAME).is_file()
    assert not (review_root / REVIEW_FINALIZATION_FILENAME).exists()


@pytest.mark.parametrize(
    "drift",
    (
        "route-version",
        "route-plan",
        "detector",
        "profile",
        "session",
        "build",
        "width",
        "configuration",
        "environment",
        "support",
        "acquisition-journal",
        "acquisition-finalization",
        "review-id",
        "review-plan",
        "review-journal",
        "review-finalization",
        "review-digest",
        "reviewer",
    ),
)
def test_durable_loader_rejects_every_external_identity_or_lineage_drift(
    tmp_path: Path,
    drift: str,
) -> None:
    acquisition_root = tmp_path / "acquisition"
    review_root = tmp_path / "review"
    receipt, _ = _complete_acquisition(acquisition_root)
    _, expectation = _complete_review(review_root, acquisition_root, receipt)
    foreign_digest = _digest(f"foreign-{drift}")

    if drift == "route-version":
        foreign = replace(
            expectation,
            route=RouteIdentity(
                expectation.route.route_id,
                "foreign-route-version",
                expectation.direction,
            ),
        )
    elif drift == "route-plan":
        foreign = replace(expectation, route_plan_sha256=foreign_digest)
    elif drift == "detector":
        foreign = replace(
            expectation,
            detector=CheckpointDetectorIdentity("foreign-detector", "1.0.0"),
        )
    elif drift == "profile":
        foreign = replace(
            expectation,
            profile=CheckpointProfileIdentity("foreign-profile", "1.0.0", foreign_digest),
        )
    elif drift == "session":
        foreign = replace(expectation, capture_session_id="foreign-session")
    elif drift == "build":
        foreign = replace(
            expectation,
            capture_build=RouteEvidenceCaptureBuildIdentity(
                "foreign-build",
                "1.0.0",
                foreign_digest,
            ),
        )
    elif drift == "width":
        foreign = replace(expectation, frame_width=expectation.frame_width + 1)
    elif drift == "configuration":
        foreign = replace(expectation, capture_configuration_sha256=foreign_digest)
    elif drift == "environment":
        foreign = replace(expectation, capture_environment_sha256=foreign_digest)
    elif drift == "support":
        foreign = replace(expectation, support_envelope_sha256=foreign_digest)
    elif drift == "acquisition-journal":
        foreign = replace(expectation, acquisition_journal_head_sha256=foreign_digest)
    elif drift == "acquisition-finalization":
        foreign = replace(expectation, acquisition_finalization_sha256=foreign_digest)
    elif drift == "review-id":
        foreign = replace(expectation, review_id="foreign-review-id")
    elif drift == "review-plan":
        foreign = replace(expectation, review_plan_sha256=foreign_digest)
    elif drift == "review-journal":
        foreign = replace(expectation, review_journal_head_sha256=foreign_digest)
    elif drift == "review-finalization":
        foreign = replace(expectation, review_finalization_sha256=foreign_digest)
    elif drift == "review-digest":
        foreign = replace(expectation, independent_review_sha256=foreign_digest)
    else:
        foreign = replace(expectation, reviewer_id="foreign-reviewer")

    with pytest.raises(RouteEvidenceIntegrityError):
        load_and_verify_durable_synthetic_route_evidence(
            acquisition_root,
            review_root,
            foreign,
        )


def test_stale_persisted_request_predecessor_cannot_be_promoted(tmp_path: Path) -> None:
    acquisition_root = tmp_path / "acquisition"
    receipt, _ = _complete_acquisition(acquisition_root)
    request_path = next(path for path in acquisition_root.rglob("*-request.json") if path.is_file())
    record = json.loads(request_path.read_bytes())
    assert isinstance(record, dict)
    record["previous_journal_sha256"] = _digest("stale-predecessor").value
    request_path.write_bytes(canonical_route_evidence_bytes(record))

    with pytest.raises(RouteEvidenceIntegrityError, match="request journal differs"):
        load_durable_acquisition(acquisition_root, receipt.expectation)


def test_durable_surfaces_are_not_root_exported_or_input_capable() -> None:
    public_names = {
        "DurableAcquisitionTransaction",
        "DurableReviewTransaction",
        "begin_durable_acquisition",
        "begin_durable_review",
        "load_and_verify_durable_synthetic_route_evidence",
    }
    assert public_names.isdisjoint(navigation_root.__all__)
    assert integration_boundary.__all__ == ()
    assert not hasattr(navigation_root, "begin_durable_acquisition")
    assert not hasattr(integration_boundary, "begin_durable_acquisition")
    forbidden = {"pyautogui", "pynput", "win32api", "win32con"}
    source = Path(durable_module.__file__).read_text(encoding="utf-8")
    assert _import_roots(ast.parse(source)).isdisjoint(forbidden)
