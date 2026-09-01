from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Barrier
from typing import cast

import pytest

import mining_automation.navigation as navigation_root
import mining_automation.navigation.durable_route_evidence as durable_module
import mining_automation.navigation.handle_anchored_route_evidence as anchored_module
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
from mining_automation.navigation.handle_anchored_route_evidence import (
    HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE,
    HANDLE_ANCHORED_WRITER_NAMESPACE_CONTRACT,
    HANDLE_ANCHORED_WRITER_PROCESS_INTEGRITY_REQUIRED,
    HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM,
    HandleAnchoredEvidenceCapabilityError,
    begin_handle_anchored_acquisition,
    begin_handle_anchored_review,
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
    FinalizedRouteEvidencePackage,
    RouteEvidenceCampaignPlan,
    RouteEvidenceCaptureBuildIdentity,
    RouteEvidenceCaseRole,
    RouteEvidenceCaseSpec,
    RouteEvidenceCaseTruth,
    RouteEvidenceIntegrityError,
    RouteEvidenceReviewDecision,
    RouteEvidenceVerificationReport,
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


def _begin_handle_anchored_acquisition(
    root: Path,
    direction: RouteDirection = RouteDirection.MINE_TO_BANK,
    **plan_kwargs: str,
) -> tuple[DurableAcquisitionTransaction, _Source, _Detector, _Clock]:
    plan, source, detector, clock = _runtime(direction, **plan_kwargs)
    transaction = begin_handle_anchored_acquisition(
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


def _complete_handle_anchored_acquisition(
    root: Path,
    direction: RouteDirection = RouteDirection.MINE_TO_BANK,
    **plan_kwargs: str,
) -> tuple[DurableAcquisitionReceipt, DurableAcquisitionTransaction]:
    transaction, source, detector, clock = _begin_handle_anchored_acquisition(
        root,
        direction,
        **plan_kwargs,
    )
    for ordinal in range(1, len(transaction.progress.plan.cases) + 1):
        _capture_next(transaction, source, detector, clock, ordinal)
    clock.value = 40.0
    return (
        transaction.finalize(finalized_at_utc="2026-09-01T00:00:10Z"),
        transaction,
    )


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
        acquisition_physical_identity_sha256=(acquisition.acquisition_physical_identity_sha256),
        review_id=review.review_id,
        review_plan_sha256=review.review_plan_sha256,
        review_journal_head_sha256=review.review_journal_head_sha256,
        review_finalization_sha256=review.review_finalization_sha256,
        review_physical_identity_sha256=review.review_physical_identity_sha256,
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


def _begin_handle_anchored_review(
    root: Path,
    acquisition_root: Path,
    receipt: DurableAcquisitionReceipt,
    *,
    review_id: str = "synthetic-independent-review-a",
    reviewer_id: str = "synthetic-independent-reviewer",
) -> tuple[DurableReviewTransaction, VerifiedDurableAcquisition]:
    acquisition = load_durable_acquisition(acquisition_root, receipt.expectation)
    transaction = begin_handle_anchored_review(
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


def _complete_handle_anchored_review(
    root: Path,
    acquisition_root: Path,
    receipt: DurableAcquisitionReceipt,
) -> tuple[DurableReviewReceipt, DurableRouteEvidenceFilesystemExpectation]:
    transaction, acquisition = _begin_handle_anchored_review(
        root,
        acquisition_root,
        receipt,
    )
    for ordinal, truth in enumerate(_truths(acquisition), start=1):
        transaction.record_case_truth(
            truth,
            recorded_at_utc=f"2026-09-01T00:00:{20 + ordinal:02d}Z",
        )
    review_receipt = transaction.finalize(reviewed_at_utc="2026-09-01T00:00:30Z")
    return review_receipt, _full_expectation(receipt.expectation, review_receipt)


def _relative_files(root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}


@dataclass(slots=True)
class _ParentSwapProbe:
    swapped: bool = False
    foreign_file: Path | None = None
    parked_parent: Path | None = None


@dataclass(slots=True)
class _RootCloneSwapProbe:
    swapped: bool = False
    replacement_terminal: Path | None = None
    parked_root: Path | None = None


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


def _swap_owned_root_with_complete_clone_during_path_open(
    monkeypatch: pytest.MonkeyPatch,
    *,
    target_root: Path,
    parked_root: Path,
    terminal_filename: str,
) -> _RootCloneSwapProbe:
    """Clone a complete prefix after the terminal write's last root check."""

    probe = _RootCloneSwapProbe()
    owned_root = target_root.absolute()
    parked = parked_root.absolute()
    terminal = owned_root / terminal_filename
    original_open = Path.open

    def swapping_open(path: Path, *args: object, **kwargs: object) -> object:
        candidate = path.absolute()
        if not probe.swapped and candidate == terminal:
            owned_root.rename(parked)
            probe.swapped = True
            shutil.copytree(parked, owned_root)
            probe.replacement_terminal = candidate
            probe.parked_root = parked
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
    review_plan = json.loads((review_root / durable_module.REVIEW_PLAN_FILENAME).read_bytes())
    assert review_plan["schema"] == "fixed-route-durable-review-plan-v2"
    assert (
        review_plan["acquisition_physical_identity_sha256"]
        == receipt.expectation.acquisition_physical_identity_sha256.value
    )
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


def test_acquisition_root_clone_swap_at_terminal_open_is_not_verifiable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_root = (tmp_path / "terminal-swapped-acquisition").absolute()
    transaction, source, detector, clock = _begin_acquisition(target_root)
    for ordinal in range(1, len(transaction.progress.plan.cases) + 1):
        _capture_next(transaction, source, detector, clock, ordinal)
    assert transaction.phase is DurableAcquisitionPhase.COMPLETE
    clock.value = 40.0

    captured_expectations: list[DurableAcquisitionFilesystemExpectation] = []
    original_expectation_factory = durable_module._acquisition_expectation_from_package

    def capture_expectation(
        package: FinalizedRouteEvidencePackage,
        *,
        journal_head_sha256: Sha256Digest,
        finalization_sha256: Sha256Digest,
        physical_identity_sha256: Sha256Digest,
    ) -> DurableAcquisitionFilesystemExpectation:
        expectation = original_expectation_factory(
            package,
            journal_head_sha256=journal_head_sha256,
            finalization_sha256=finalization_sha256,
            physical_identity_sha256=physical_identity_sha256,
        )
        captured_expectations.append(expectation)
        return expectation

    monkeypatch.setattr(
        durable_module,
        "_acquisition_expectation_from_package",
        capture_expectation,
    )
    probe = _swap_owned_root_with_complete_clone_during_path_open(
        monkeypatch,
        target_root=target_root,
        parked_root=tmp_path / "parked-complete-acquisition-prefix",
        terminal_filename=ACQUISITION_FINALIZATION_FILENAME,
    )

    with pytest.raises(DurableEvidenceError, match="durable transaction root was replaced"):
        transaction.finalize(finalized_at_utc="2026-09-01T00:00:10Z")

    assert captured_expectations == []
    assert probe.swapped is True
    assert probe.replacement_terminal is not None and probe.replacement_terminal.is_file()
    assert probe.parked_root is not None and probe.parked_root.is_dir()
    assert not (probe.parked_root / ACQUISITION_FINALIZATION_FILENAME).exists()
    assert transaction.phase is DurableAcquisitionPhase.STOPPED


def test_review_root_clone_swap_at_terminal_open_is_not_verifiable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acquisition_root = tmp_path / "acquisition"
    acquisition_receipt, _ = _complete_acquisition(acquisition_root)
    review_root = (tmp_path / "terminal-swapped-review").absolute()
    transaction, acquisition = _begin_review(
        review_root,
        acquisition_root,
        acquisition_receipt,
    )
    for ordinal, truth in enumerate(_truths(acquisition), start=1):
        transaction.record_case_truth(
            truth,
            recorded_at_utc=f"2026-09-01T00:00:{20 + ordinal:02d}Z",
        )
    assert transaction.phase is DurableReviewPhase.COMPLETE

    captured_receipts: list[DurableReviewReceipt] = []
    original_receipt_type = DurableReviewReceipt

    def capture_receipt(
        *,
        review_id: str,
        reviewer_id: str,
        independent_review_sha256: Sha256Digest,
        review_plan_sha256: Sha256Digest,
        review_journal_head_sha256: Sha256Digest,
        review_finalization_sha256: Sha256Digest,
        review_physical_identity_sha256: Sha256Digest,
        report: RouteEvidenceVerificationReport,
    ) -> DurableReviewReceipt:
        receipt = original_receipt_type(
            review_id=review_id,
            reviewer_id=reviewer_id,
            independent_review_sha256=independent_review_sha256,
            review_plan_sha256=review_plan_sha256,
            review_journal_head_sha256=review_journal_head_sha256,
            review_finalization_sha256=review_finalization_sha256,
            review_physical_identity_sha256=review_physical_identity_sha256,
            report=report,
        )
        captured_receipts.append(receipt)
        return receipt

    monkeypatch.setattr(durable_module, "DurableReviewReceipt", capture_receipt)
    probe = _swap_owned_root_with_complete_clone_during_path_open(
        monkeypatch,
        target_root=review_root,
        parked_root=tmp_path / "parked-complete-review-prefix",
        terminal_filename=REVIEW_FINALIZATION_FILENAME,
    )

    with pytest.raises(DurableEvidenceError, match="durable transaction root was replaced"):
        transaction.finalize(reviewed_at_utc="2026-09-01T00:00:30Z")

    assert captured_receipts == []
    assert probe.swapped is True
    assert probe.replacement_terminal is not None and probe.replacement_terminal.is_file()
    assert probe.parked_root is not None and probe.parked_root.is_dir()
    assert not (probe.parked_root / REVIEW_FINALIZATION_FILENAME).exists()
    assert transaction.phase is DurableReviewPhase.STOPPED


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

    with pytest.raises(RouteEvidenceIntegrityError, match="physical identity differs"):
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
        "acquisition-physical",
        "review-id",
        "review-plan",
        "review-journal",
        "review-finalization",
        "review-physical",
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
    elif drift == "acquisition-physical":
        foreign = replace(expectation, acquisition_physical_identity_sha256=foreign_digest)
    elif drift == "review-id":
        foreign = replace(expectation, review_id="foreign-review-id")
    elif drift == "review-plan":
        foreign = replace(expectation, review_plan_sha256=foreign_digest)
    elif drift == "review-journal":
        foreign = replace(expectation, review_journal_head_sha256=foreign_digest)
    elif drift == "review-finalization":
        foreign = replace(expectation, review_finalization_sha256=foreign_digest)
    elif drift == "review-physical":
        foreign = replace(expectation, review_physical_identity_sha256=foreign_digest)
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


def test_handle_anchored_writer_has_a_separate_platform_scoped_contract() -> None:
    assert HANDLE_ANCHORED_WRITER_NAMESPACE_CONTRACT == (
        "windows_nt_handle_relative_no_follow_fresh_directory_v1"
    )
    assert HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM == "win32"
    assert HANDLE_ANCHORED_WRITER_PROCESS_INTEGRITY_REQUIRED is True
    assert HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE is False
    assert DURABLE_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE is False
    public_names = {
        "begin_handle_anchored_acquisition",
        "begin_handle_anchored_review",
        "HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE",
        "HANDLE_ANCHORED_WRITER_PROCESS_INTEGRITY_REQUIRED",
    }
    assert public_names.isdisjoint(navigation_root.__all__)
    assert integration_boundary.__all__ == ()
    assert not hasattr(navigation_root, "begin_handle_anchored_acquisition")
    assert not hasattr(integration_boundary, "begin_handle_anchored_acquisition")


def test_handle_anchored_writer_is_mechanically_input_free() -> None:
    boundary_path = Path(anchored_module.__file__)
    native_path = boundary_path.with_name("_windows_handle_anchored.py")
    forbidden_imports = {"pyautogui", "pynput", "win32api", "win32con"}
    forbidden_native_symbols = {
        "keybd_event",
        "mouse_event",
        "sendinput",
        "setcursorpos",
        "user32",
    }

    for source_path in (boundary_path, native_path):
        source = source_path.read_text(encoding="utf-8")
        assert _import_roots(ast.parse(source)).isdisjoint(forbidden_imports)
        normalized = source.casefold()
        assert all(symbol not in normalized for symbol in forbidden_native_symbols)


def test_handle_anchored_capability_failure_precedes_all_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "unavailable-handle-root"
    plan, source, detector, clock = _runtime()

    def unavailable() -> object:
        raise HandleAnchoredEvidenceCapabilityError("synthetic missing native capability")

    def unexpected_path_resolution(*args: object, **kwargs: object) -> Path:
        del args, kwargs
        raise AssertionError("path resolution must follow the platform capability gate")

    monkeypatch.setattr(anchored_module, "_namespace_factory", unavailable)
    monkeypatch.setattr(anchored_module, "_absolute_path_once", unexpected_path_resolution)

    with pytest.raises(HandleAnchoredEvidenceCapabilityError, match="missing native"):
        begin_handle_anchored_acquisition(
            root,
            plan,
            source,
            detector,
            clock,
            started_monotonic_s=0.0,
        )

    assert not root.exists()
    assert source.identity_calls == 0
    assert source.calls == 0
    assert detector.calls == 0
    assert clock.calls == 0


def test_handle_anchored_review_capability_failure_precedes_all_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    review_root = tmp_path / "unavailable-handle-review"
    acquisition_root = tmp_path / "unresolved-handle-acquisition"
    path_resolution_calls = 0
    intake_calls = 0

    def unavailable() -> object:
        raise HandleAnchoredEvidenceCapabilityError("synthetic missing review capability")

    def unexpected_path_resolution(*args: object, **kwargs: object) -> Path:
        nonlocal path_resolution_calls
        del args, kwargs
        path_resolution_calls += 1
        raise AssertionError("review paths must follow the platform capability gate")

    def unexpected_intake(*args: object, **kwargs: object) -> DurableReviewTransaction:
        nonlocal intake_calls
        del args, kwargs
        intake_calls += 1
        raise AssertionError("acquisition intake must follow the platform capability gate")

    monkeypatch.setattr(anchored_module, "_namespace_factory", unavailable)
    monkeypatch.setattr(anchored_module, "_absolute_path_once", unexpected_path_resolution)
    monkeypatch.setattr(
        anchored_module,
        "_begin_durable_review_with_namespace_factory",
        unexpected_intake,
    )

    with pytest.raises(HandleAnchoredEvidenceCapabilityError, match="review capability"):
        begin_handle_anchored_review(
            review_root,
            acquisition_root,
            cast(DurableAcquisitionFilesystemExpectation, object()),
            review_id="synthetic-unavailable-review",
            reviewer_id="synthetic-unavailable-reviewer",
            started_at_utc="2026-09-01T00:00:20Z",
        )

    assert path_resolution_calls == 0
    assert intake_calls == 0
    assert not review_root.exists()
    assert not acquisition_root.exists()


def test_handle_anchored_native_round_trip_is_strictly_reviewable_or_unavailable(
    tmp_path: Path,
) -> None:
    acquisition_root = tmp_path / "handle-acquisition"
    if sys.platform != HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM:
        plan, source, detector, clock = _runtime()
        with pytest.raises(
            HandleAnchoredEvidenceCapabilityError,
            match="atomic fresh-directory handle claim is unavailable",
        ):
            begin_handle_anchored_acquisition(
                acquisition_root,
                plan,
                source,
                detector,
                clock,
                started_monotonic_s=0.0,
            )
        assert not acquisition_root.exists()
        assert source.identity_calls == 0
        assert source.calls == 0
        assert detector.calls == 0
        assert clock.calls == 0
        return

    review_root = tmp_path / "handle-review"
    acquisition_receipt, acquisition_transaction = _complete_handle_anchored_acquisition(
        acquisition_root
    )
    review_receipt, expectation = _complete_handle_anchored_review(
        review_root,
        acquisition_root,
        acquisition_receipt,
    )

    report = load_and_verify_durable_synthetic_route_evidence(
        acquisition_root,
        review_root,
        expectation,
    )

    assert acquisition_transaction.phase is DurableAcquisitionPhase.FINALIZED
    assert review_receipt.report.evidence_conformance_passed is True
    assert report.evidence_conformance_passed is True
    assert report.real_release_role_satisfied is False
    assert report.live_navigation_enabled is False
    assert report.activation_allowed is False
    assert report.input_authority is False
    moved_acquisition = tmp_path / "closed-handle-acquisition"
    moved_review = tmp_path / "closed-handle-review"
    acquisition_root.rename(moved_acquisition)
    review_root.rename(moved_review)
    moved_acquisition.rename(acquisition_root)
    moved_review.rename(review_root)


@pytest.mark.parametrize("target_kind", ("frame", "terminal", "case-directory"))
def test_handle_anchored_acquisition_physical_pin_rejects_exact_byte_replacement(
    tmp_path: Path,
    target_kind: str,
) -> None:
    if sys.platform != HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM:
        assert HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE is False
        return
    root = tmp_path / f"physical-acquisition-{target_kind}"
    receipt, _ = _complete_handle_anchored_acquisition(root)

    if target_kind == "case-directory":
        target_directory = next(path for path in (root / "cases").iterdir() if path.is_dir())
        original_directory_identity = (
            target_directory.stat().st_dev,
            target_directory.stat().st_ino,
        )
        original_child_identities = {
            child.name: (child.stat().st_dev, child.stat().st_ino)
            for child in target_directory.iterdir()
        }
        replacement_directory = tmp_path / "replacement-case-directory"
        parked_directory = tmp_path / "parked-case-directory"
        replacement_directory.mkdir()
        target_directory.rename(parked_directory)
        for child in parked_directory.iterdir():
            child.rename(replacement_directory / child.name)
        replacement_directory.rename(target_directory)
        assert (target_directory.stat().st_dev, target_directory.stat().st_ino) != (
            original_directory_identity
        )
        assert {
            child.name: (child.stat().st_dev, child.stat().st_ino)
            for child in target_directory.iterdir()
        } == original_child_identities
    else:
        if target_kind == "terminal":
            relative_path = ACQUISITION_FINALIZATION_FILENAME
        else:
            acquisition = load_durable_acquisition(root, receipt.expectation)
            relative_path = acquisition.package.cases[0].frame_artifact.relative_path
        target = root.joinpath(*relative_path.split("/"))
        replacement = tmp_path / f"replacement-{target_kind}.bin"
        replacement.write_bytes(target.read_bytes())
        os.replace(replacement, target)

    with pytest.raises(RouteEvidenceIntegrityError, match="physical identity differs"):
        load_durable_acquisition(root, receipt.expectation)


@pytest.mark.parametrize("target_kind", ("truth", "terminal", "truth-directory"))
def test_handle_anchored_review_physical_pin_rejects_exact_byte_replacement(
    tmp_path: Path,
    target_kind: str,
) -> None:
    if sys.platform != HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM:
        assert HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE is False
        return
    acquisition_root = tmp_path / f"physical-review-{target_kind}-acquisition"
    acquisition_receipt, _ = _complete_handle_anchored_acquisition(acquisition_root)
    review_root = tmp_path / f"physical-review-{target_kind}"
    _, expectation = _complete_handle_anchored_review(
        review_root,
        acquisition_root,
        acquisition_receipt,
    )
    if target_kind == "truth-directory":
        target_directory = review_root / "truth"
        original_directory_identity = (
            target_directory.stat().st_dev,
            target_directory.stat().st_ino,
        )
        original_child_identities = {
            child.name: (child.stat().st_dev, child.stat().st_ino)
            for child in target_directory.iterdir()
        }
        replacement_directory = tmp_path / "replacement-truth-directory"
        parked_directory = tmp_path / "parked-truth-directory"
        replacement_directory.mkdir()
        target_directory.rename(parked_directory)
        for child in parked_directory.iterdir():
            child.rename(replacement_directory / child.name)
        replacement_directory.rename(target_directory)
        assert (target_directory.stat().st_dev, target_directory.stat().st_ino) != (
            original_directory_identity
        )
        assert {
            child.name: (child.stat().st_dev, child.stat().st_ino)
            for child in target_directory.iterdir()
        } == original_child_identities
    else:
        relative_path = (
            REVIEW_FINALIZATION_FILENAME
            if target_kind == "terminal"
            else next(path for path in _relative_files(review_root) if path.startswith("truth/"))
        )
        target = review_root.joinpath(*relative_path.split("/"))
        replacement = tmp_path / f"replacement-review-{target_kind}.json"
        replacement.write_bytes(target.read_bytes())
        os.replace(replacement, target)

    with pytest.raises(RouteEvidenceIntegrityError, match="review physical identity differs"):
        load_and_verify_durable_synthetic_route_evidence(
            acquisition_root,
            review_root,
            expectation,
        )


def test_review_plan_v1_wire_shape_cannot_claim_the_physical_binding(tmp_path: Path) -> None:
    acquisition_root = tmp_path / "review-plan-v1-acquisition"
    receipt, _ = _complete_acquisition(acquisition_root)
    review_root = tmp_path / "review-plan-v1-review"
    _, expectation = _complete_review(review_root, acquisition_root, receipt)
    plan_path = review_root / durable_module.REVIEW_PLAN_FILENAME
    plan = json.loads(plan_path.read_bytes())
    assert plan.pop("acquisition_physical_identity_sha256") == (
        receipt.expectation.acquisition_physical_identity_sha256.value
    )
    plan["schema"] = "fixed-route-durable-review-plan-v1"
    plan_path.write_bytes(canonical_route_evidence_bytes(plan))

    with pytest.raises(RouteEvidenceIntegrityError, match="review plan keys differ"):
        load_and_verify_durable_synthetic_route_evidence(
            acquisition_root,
            review_root,
            expectation,
        )


def test_handle_anchored_foreign_preclaimed_root_is_never_adopted_or_removed(
    tmp_path: Path,
) -> None:
    if sys.platform != HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM:
        assert HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE is False
        return
    root = tmp_path / "foreign-handle-root"
    root.mkdir()
    sentinel = root / "foreign-sentinel.bin"
    sentinel.write_bytes(b"foreign-owner")

    with pytest.raises(DurableEvidenceCollisionError, match="already claimed"):
        _begin_handle_anchored_acquisition(root)

    assert sentinel.read_bytes() == b"foreign-owner"
    assert _relative_files(root) == {"foreign-sentinel.bin"}


def test_two_handle_anchored_writers_claim_exactly_one_fresh_root(
    tmp_path: Path,
) -> None:
    if sys.platform != HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM:
        assert HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE is False
        return
    root = tmp_path / "contended-handle-root"
    barrier = Barrier(2)

    def claim(writer_id: int) -> str:
        plan, source, detector, clock = _runtime(
            campaign_id=f"synthetic-handle-writer-{writer_id}",
            capture_session_id=f"synthetic-handle-session-{writer_id}",
        )
        barrier.wait()
        try:
            begin_handle_anchored_acquisition(
                root,
                plan,
                source,
                detector,
                clock,
                started_monotonic_s=0.0,
            )
        except DurableEvidenceCollisionError:
            return "collision"
        return "winner"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(claim, (1, 2)))

    assert sorted(outcomes) == ["collision", "winner"]
    assert (root / ACQUISITION_PLAN_FILENAME).is_file()
    assert not (root / ACQUISITION_FINALIZATION_FILENAME).exists()


def test_handle_anchored_parent_replacement_before_root_create_never_receives_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if sys.platform != HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM:
        assert HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE is False
        return
    import mining_automation.navigation._windows_handle_anchored as native_module

    parent = tmp_path / "owned-parent"
    parent.mkdir()
    root = parent / "handle-acquisition"
    parked_parent = tmp_path / "parked-owned-parent"
    replacement_sentinel = parent / "foreign-sentinel.bin"
    original_open = native_module._nt_relative_open
    swapped = False

    def swap_before_root_create(
        parent_handle: int,
        component: str,
        *,
        directory: bool,
        create: bool,
        writable_directory: bool = False,
    ) -> int:
        nonlocal swapped
        if create and directory and component == root.name and not swapped:
            parent.rename(parked_parent)
            parent.mkdir()
            replacement_sentinel.write_bytes(b"foreign-owner")
            swapped = True
        return original_open(
            parent_handle,
            component,
            directory=directory,
            create=create,
            writable_directory=writable_directory,
        )

    monkeypatch.setattr(native_module, "_nt_relative_open", swap_before_root_create)
    plan, source, detector, clock = _runtime()

    with pytest.raises(DurableEvidenceError, match="cannot inspect handle-owned path"):
        begin_handle_anchored_acquisition(
            root,
            plan,
            source,
            detector,
            clock,
            started_monotonic_s=0.0,
        )

    assert swapped is True
    assert (parked_parent / root.name).is_dir()
    assert _relative_files(parked_parent / root.name) == set()
    assert replacement_sentinel.read_bytes() == b"foreign-owner"
    assert not root.exists()
    assert source.identity_calls == 0
    assert source.calls == 0
    assert detector.calls == 0
    assert clock.calls == 0


def test_handle_anchored_intermediate_ancestor_junction_race_fails_before_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if sys.platform != HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM:
        assert HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE is False
        return
    import mining_automation.navigation._windows_handle_anchored as native_module

    outer = tmp_path / "ancestor-junction-race"
    switch = outer / "switch"
    original_parent = switch / "owned-parent"
    original_parent.mkdir(parents=True)
    parked_switch = outer / "parked-switch"
    foreign = outer / "foreign"
    foreign_parent = foreign / "owned-parent"
    foreign_parent.mkdir(parents=True)
    root = original_parent / "handle-acquisition"
    original_open = native_module._nt_relative_open
    attacked = False

    def race_after_ancestry_snapshot(
        parent_handle: int,
        component: str,
        *,
        directory: bool,
        create: bool,
        writable_directory: bool = False,
    ) -> int:
        nonlocal attacked
        if not create and directory and component == switch.name and not attacked:
            switch.rename(parked_switch)
            subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(switch), str(foreign)],
                check=True,
                capture_output=True,
                text=True,
            )
            attacked = True
        return original_open(
            parent_handle,
            component,
            directory=directory,
            create=create,
            writable_directory=writable_directory,
        )

    monkeypatch.setattr(native_module, "_nt_relative_open", race_after_ancestry_snapshot)
    plan, source, detector, clock = _runtime()

    with pytest.raises(HandleAnchoredEvidenceCapabilityError, match="reparse point"):
        begin_handle_anchored_acquisition(
            root,
            plan,
            source,
            detector,
            clock,
            started_monotonic_s=0.0,
        )

    assert attacked is True
    assert loader_module._is_link_or_reparse(switch.lstat())
    assert not (parked_switch / "owned-parent" / root.name).exists()
    assert not (foreign_parent / root.name).exists()
    assert source.identity_calls == 0
    assert source.calls == 0
    assert detector.calls == 0
    assert clock.calls == 0


@pytest.mark.parametrize(
    "failure_context",
    ("transaction drive root", "transaction parent", "new transaction root"),
)
def test_handle_anchored_constructor_failure_closes_each_untransferred_handle_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_context: str,
) -> None:
    if sys.platform != HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM:
        assert HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE is False
        return
    import mining_automation.navigation._windows_handle_anchored as native_module

    root = tmp_path / f"constructor-failure-{failure_context.replace(' ', '-')}"
    original_existing_open = native_module._open_existing_directory
    original_relative_open = native_module._nt_relative_open
    original_native_info = native_module._native_info
    original_quiet_close = native_module._close_handle_quietly
    anchor_handles: list[int] = []
    parent_handles: list[int] = []
    root_handles: list[int] = []
    quiet_closes: list[int] = []

    def tracked_existing_open(path: Path, *, writable: bool) -> int:
        handle = original_existing_open(path, writable=writable)
        anchor_handles.append(handle)
        return handle

    def tracked_relative_open(
        parent_handle: int,
        component: str,
        *,
        directory: bool,
        create: bool,
        writable_directory: bool = False,
    ) -> int:
        handle = original_relative_open(
            parent_handle,
            component,
            directory=directory,
            create=create,
            writable_directory=writable_directory,
        )
        if create and directory and component == root.name:
            root_handles.append(handle)
        if not create and directory and writable_directory:
            parent_handles.append(handle)
        return handle

    def injected_info_failure(handle: int, context: str) -> object:
        if context == failure_context:
            raise HandleAnchoredEvidenceCapabilityError(
                f"synthetic {failure_context} identity failure"
            )
        return original_native_info(handle, context)

    def tracked_quiet_close(handle: int) -> None:
        quiet_closes.append(handle)
        original_quiet_close(handle)

    monkeypatch.setattr(native_module, "_open_existing_directory", tracked_existing_open)
    monkeypatch.setattr(native_module, "_nt_relative_open", tracked_relative_open)
    monkeypatch.setattr(native_module, "_native_info", injected_info_failure)
    monkeypatch.setattr(native_module, "_close_handle_quietly", tracked_quiet_close)
    plan, source, detector, clock = _runtime()

    with pytest.raises(HandleAnchoredEvidenceCapabilityError, match="identity failure"):
        begin_handle_anchored_acquisition(
            root,
            plan,
            source,
            detector,
            clock,
            started_monotonic_s=0.0,
        )

    assert len(anchor_handles) == 1
    if failure_context == "transaction drive root":
        assert parent_handles == []
        assert root_handles == []
        assert quiet_closes.count(anchor_handles[0]) == 1
        assert not root.exists()
    elif failure_context == "transaction parent":
        assert len(parent_handles) == 1
        assert root_handles == []
        assert quiet_closes.count(parent_handles[0]) == 1
        assert not root.exists()
    else:
        assert len(parent_handles) == 1
        assert len(root_handles) == 1
        assert quiet_closes.count(root_handles[0]) == 1
        assert root.is_dir()
        assert _relative_files(root) == set()
    assert source.identity_calls == 0
    assert source.calls == 0
    assert detector.calls == 0
    assert clock.calls == 0


def test_handle_anchored_nonfixed_volume_fails_before_root_or_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if sys.platform != HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM:
        assert HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE is False
        return
    import mining_automation.navigation._windows_handle_anchored as native_module

    root = tmp_path / "unsupported-volume-acquisition"

    def remote_drive(_root_path: str) -> int:
        return 4

    monkeypatch.setattr(native_module._kernel32, "GetDriveTypeW", remote_drive)
    plan, source, detector, clock = _runtime()

    with pytest.raises(HandleAnchoredEvidenceCapabilityError, match="fixed local drive"):
        begin_handle_anchored_acquisition(
            root,
            plan,
            source,
            detector,
            clock,
            started_monotonic_s=0.0,
        )

    assert not root.exists()
    assert source.identity_calls == 0
    assert source.calls == 0
    assert detector.calls == 0
    assert clock.calls == 0


def test_handle_anchored_close_protection_failure_precedes_root_and_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if sys.platform != HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM:
        assert HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE is False
        return
    import mining_automation.navigation._windows_handle_anchored as native_module

    root = tmp_path / "unprotected-handle-acquisition"
    monkeypatch.setattr(native_module._kernel32, "SetHandleInformation", lambda *args: False)
    plan, source, detector, clock = _runtime()

    with pytest.raises(
        HandleAnchoredEvidenceCapabilityError,
        match="cannot protect .* from close/reuse",
    ):
        begin_handle_anchored_acquisition(
            root,
            plan,
            source,
            detector,
            clock,
            started_monotonic_s=0.0,
        )

    assert not root.exists()
    assert source.identity_calls == 0
    assert source.calls == 0
    assert detector.calls == 0
    assert clock.calls == 0


def test_handle_anchored_createfile_invalid_handle_is_normalized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if sys.platform != HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM:
        assert HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE is False
        return
    import mining_automation.navigation._windows_handle_anchored as native_module

    def invalid_handle(*args: object) -> int:
        del args
        return native_module._INVALID_HANDLE_VALUE

    monkeypatch.setattr(native_module._kernel32, "CreateFileW", invalid_handle)

    with pytest.raises(
        HandleAnchoredEvidenceCapabilityError,
        match="cannot open existing directory",
    ):
        native_module._open_existing_directory(tmp_path, writable=False)


def test_handle_anchored_namespace_close_releases_exact_owned_handle_ledger_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if sys.platform != HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM:
        assert HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE is False
        return
    import mining_automation.navigation._windows_handle_anchored as native_module

    root = tmp_path / "close-ledger-acquisition"
    transaction, _, _, _ = _begin_handle_anchored_acquisition(root)
    namespace = transaction._namespace
    owned_handles = [
        *(owned.handle for owned in namespace._files.values()),
        *(owned.handle for owned in namespace._directories.values()),
        namespace._root.handle,
        *(owned.handle for owned in namespace._ancestors),
    ]
    assert len(set(owned_handles)) == len(owned_handles)
    assert all(
        native_module._handle_flags(handle, "test retained handle")
        & native_module._HANDLE_FLAG_PROTECT_FROM_CLOSE
        for handle in owned_handles
    )
    foreign_handle = native_module._duplicate_handle(namespace._root.handle)
    native_module._protect_handle(foreign_handle, "test foreign duplicate")
    original_close = native_module._kernel32.CloseHandle
    closed: list[int] = []

    def tracked_close(handle: object) -> object:
        closed.append(native_module._handle_value(handle))
        return original_close(handle)

    monkeypatch.setattr(native_module._kernel32, "CloseHandle", tracked_close)
    try:
        namespace.close()
        namespace.close()

        assert all(closed.count(handle) == 1 for handle in owned_handles)
        assert foreign_handle not in closed
        foreign_info = native_module._native_info(foreign_handle, "foreign duplicate")
        assert foreign_info.identity == namespace._root.native_identity
    finally:
        native_module._close_handle(foreign_handle)


def test_handle_anchored_close_never_closes_a_simulated_reused_foreign_handle(
    tmp_path: Path,
) -> None:
    if sys.platform != HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM:
        assert HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE is False
        return
    import mining_automation.navigation._windows_handle_anchored as native_module

    root = tmp_path / "reused-handle-acquisition"
    transaction, _, _, _ = _begin_handle_anchored_acquisition(root)
    namespace = transaction._namespace
    stale_owned = namespace._files[ACQUISITION_PLAN_FILENAME]
    native_module._close_handle(stale_owned.handle)
    create_event = native_module._kernel32.CreateEventW
    create_event.restype = native_module.wintypes.HANDLE
    create_event.argtypes = [
        native_module.ctypes.c_void_p,
        native_module.wintypes.BOOL,
        native_module.wintypes.BOOL,
        native_module.wintypes.LPCWSTR,
    ]
    get_handle_information = native_module._kernel32.GetHandleInformation
    get_handle_information.restype = native_module.wintypes.BOOL
    get_handle_information.argtypes = [
        native_module.wintypes.HANDLE,
        native_module.ctypes.POINTER(native_module.wintypes.DWORD),
    ]
    foreign_event = native_module._handle_value(create_event(None, True, False, None))
    stale_owned.handle = foreign_event
    try:
        with pytest.raises(DurableEvidenceError, match="could not close 1 owned Windows handle"):
            namespace.close()

        flags = native_module.wintypes.DWORD()
        assert get_handle_information(
            native_module.wintypes.HANDLE(foreign_event),
            native_module.ctypes.byref(flags),
        )
        moved = tmp_path / "reused-handle-acquisition-moved"
        root.rename(moved)
        moved.rename(root)
    finally:
        native_module._raw_close_handle_quietly(foreign_event)


def test_handle_anchored_close_refuses_reused_same_file_handle(tmp_path: Path) -> None:
    if sys.platform != HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM:
        assert HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE is False
        return
    import mining_automation.navigation._windows_handle_anchored as native_module

    root = tmp_path / "same-file-reused-handle-acquisition"
    transaction, _, _, _ = _begin_handle_anchored_acquisition(root)
    namespace = transaction._namespace
    stale_owned = namespace._files[ACQUISITION_PLAN_FILENAME]
    native_module._close_handle(stale_owned.handle)
    foreign_raw = native_module._kernel32.CreateFileW(
        str(root / ACQUISITION_PLAN_FILENAME),
        native_module._GENERIC_READ,
        native_module._DIRECTORY_SHARE,
        None,
        native_module._OPEN_EXISTING,
        native_module._FILE_ATTRIBUTE_NORMAL,
        None,
    )
    foreign_handle = native_module._handle_value(foreign_raw)
    assert foreign_handle not in {0, native_module._INVALID_HANDLE_VALUE}
    stale_owned.handle = foreign_handle

    try:
        with pytest.raises(DurableEvidenceError, match="could not close 1 owned Windows handle"):
            namespace.close()

        flags = native_module.wintypes.DWORD()
        assert native_module._kernel32.GetHandleInformation(
            native_module.wintypes.HANDLE(foreign_handle),
            native_module.ctypes.byref(flags),
        )
        assert not flags.value & native_module._HANDLE_FLAG_PROTECT_FROM_CLOSE
    finally:
        native_module._raw_close_handle_quietly(foreign_handle)


@pytest.mark.parametrize("failure", ("import-error", "exception", "negative-descriptor"))
def test_handle_anchored_descriptor_conversion_failure_closes_unprotected_duplicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    if sys.platform != HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM:
        assert HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE is False
        return
    import mining_automation.navigation._windows_handle_anchored as native_module

    root = tmp_path / f"descriptor-failure-{failure}"
    transaction, _, _, _ = _begin_handle_anchored_acquisition(root)
    namespace = transaction._namespace
    owned_handle = namespace._files[ACQUISITION_PLAN_FILENAME].handle
    original_duplicate = native_module._duplicate_handle
    original_raw_close = native_module._raw_close_handle_quietly
    duplicates: list[int] = []
    raw_closes: list[int] = []

    def tracked_duplicate(handle: int) -> int:
        duplicate = original_duplicate(handle)
        duplicates.append(duplicate)
        return duplicate

    def tracked_raw_close(handle: int) -> None:
        raw_closes.append(handle)
        original_raw_close(handle)

    class FailingMsvcrt:
        @staticmethod
        def open_osfhandle(handle: int, flags: int) -> int:
            del handle, flags
            if failure == "exception":
                raise OSError("synthetic descriptor conversion failure")
            return -1

    def import_failing_msvcrt(name: str) -> type[FailingMsvcrt]:
        assert name == "msvcrt"
        if failure == "import-error":
            raise ImportError("synthetic msvcrt import failure")
        return FailingMsvcrt

    monkeypatch.setattr(native_module, "_duplicate_handle", tracked_duplicate)
    monkeypatch.setattr(native_module, "_raw_close_handle_quietly", tracked_raw_close)
    monkeypatch.setattr(native_module.importlib, "import_module", import_failing_msvcrt)
    expected_error = {
        "import-error": ImportError,
        "exception": OSError,
        "negative-descriptor": DurableEvidenceError,
    }[failure]

    try:
        with pytest.raises(expected_error):
            native_module._descriptor_from_duplicate(owned_handle)

        if failure == "import-error":
            assert duplicates == []
            assert raw_closes == []
        else:
            assert len(duplicates) == 1
            assert raw_closes == duplicates
            flags = native_module.wintypes.DWORD()
            assert not native_module._kernel32.GetHandleInformation(
                native_module.wintypes.HANDLE(duplicates[0]),
                native_module.ctypes.byref(flags),
            )
    finally:
        namespace.close()


def test_handle_anchored_post_validation_close_cannot_redirect_child_create(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if sys.platform != HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM:
        assert HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE is False
        return
    import mining_automation.navigation._windows_handle_anchored as native_module

    root = tmp_path / "protected-parent-acquisition"
    foreign = tmp_path / "foreign-reuse-target"
    foreign.mkdir()
    transaction, _, _, _ = _begin_handle_anchored_acquisition(root)
    namespace = transaction._namespace
    audit_handle = namespace._directories["audit"].handle
    foreign_handle = native_module._open_existing_directory(foreign, writable=True)
    original_open = native_module._nt_relative_open
    close_results: list[bool] = []

    def attempt_close_after_validation(
        parent_handle: int,
        component: str,
        *,
        directory: bool,
        create: bool,
        writable_directory: bool = False,
    ) -> int:
        if create and not directory and component.endswith("-request.json"):
            assert parent_handle == audit_handle
            close_results.append(
                bool(
                    native_module._kernel32.CloseHandle(
                        native_module.wintypes.HANDLE(parent_handle)
                    )
                )
            )
            assert foreign_handle != parent_handle
        return original_open(
            parent_handle,
            component,
            directory=directory,
            create=create,
            writable_directory=writable_directory,
        )

    monkeypatch.setattr(native_module, "_nt_relative_open", attempt_close_after_validation)
    try:
        progress = transaction.request_capture(
            request_id="synthetic-protected-parent-request",
            operator_id=transaction.progress.plan.operator_id,
            acknowledged_monotonic_s=10.0,
        )

        assert progress.phase is PassiveCampaignPhase.AWAITING_CAPTURE
        assert close_results == [False]
        assert any(path.name.endswith("-request.json") for path in (root / "audit").iterdir())
        assert list(foreign.iterdir()) == []
    finally:
        namespace.close()
        native_module._close_handle(foreign_handle)


@pytest.mark.parametrize("target", ("parent", "root"))
@pytest.mark.parametrize("stage", ("frame", "case-record", "final-manifest"))
def test_handle_anchored_mid_transaction_namespace_replacement_is_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    stage: str,
) -> None:
    if sys.platform != HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM:
        assert HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE is False
        return
    import mining_automation.navigation._windows_handle_anchored as native_module

    parent = tmp_path / f"{stage}-{target}-parent"
    parent.mkdir()
    root = parent / "handle-acquisition"
    transaction, source, detector, clock = _begin_handle_anchored_acquisition(root)
    if stage == "final-manifest":
        for ordinal in range(1, len(transaction.progress.plan.cases) + 1):
            _capture_next(transaction, source, detector, clock, ordinal)
        clock.value = 40.0

    original_open = native_module._nt_relative_open
    attempted = False
    blocked_error: OSError | None = None
    parked = tmp_path / f"parked-{stage}-{target}"

    def stage_matches(component: str, directory: bool, create: bool) -> bool:
        if not create or directory:
            return False
        if stage == "frame":
            return component == "frame.bin"
        if stage == "case-record":
            return component.endswith("-owned.json")
        return component == ACQUISITION_FINALIZATION_FILENAME

    def attack_before_create(
        parent_handle: int,
        component: str,
        *,
        directory: bool,
        create: bool,
        writable_directory: bool = False,
    ) -> int:
        nonlocal attempted, blocked_error
        if not attempted and stage_matches(component, directory, create):
            attempted = True
            candidate = parent if target == "parent" else root
            try:
                candidate.rename(parked)
            except OSError as exc:
                blocked_error = exc
        return original_open(
            parent_handle,
            component,
            directory=directory,
            create=create,
            writable_directory=writable_directory,
        )

    monkeypatch.setattr(native_module, "_nt_relative_open", attack_before_create)

    if stage == "final-manifest":
        receipt = transaction.finalize(finalized_at_utc="2026-09-01T00:00:10Z")
        assert receipt.expectation.campaign_id == transaction.progress.plan.campaign_id
        assert transaction.phase is DurableAcquisitionPhase.FINALIZED
    else:
        _capture_next(transaction, source, detector, clock, 1)
        assert transaction.phase is DurableAcquisitionPhase.READY_FOR_REQUEST

    assert attempted is True
    assert blocked_error is not None
    assert not parked.exists()
    assert root.is_dir()
    assert not any(path.name == "foreign-sentinel.bin" for path in root.rglob("*"))


def test_handle_anchored_complete_acquisition_clone_cannot_replace_or_verify(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if sys.platform != HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM:
        assert HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE is False
        return
    import mining_automation.navigation._windows_handle_anchored as native_module

    root = tmp_path / "complete-clone-acquisition"
    clone = tmp_path / "complete-clone-acquisition-replacement"
    parked = tmp_path / "complete-clone-acquisition-parked"
    transaction, source, detector, clock = _begin_handle_anchored_acquisition(root)
    for ordinal in range(1, len(transaction.progress.plan.cases) + 1):
        _capture_next(transaction, source, detector, clock, ordinal)
    clock.value = 40.0
    original_open = native_module._nt_relative_open
    attacked = False
    blocked_error: OSError | None = None

    def attack_with_complete_clone(
        parent_handle: int,
        component: str,
        *,
        directory: bool,
        create: bool,
        writable_directory: bool = False,
    ) -> int:
        nonlocal attacked, blocked_error
        if create and not directory and component == ACQUISITION_FINALIZATION_FILENAME:
            attacked = True
            shutil.copytree(root, clone)
            try:
                root.rename(parked)
                clone.rename(root)
            except OSError as exc:
                blocked_error = exc
        return original_open(
            parent_handle,
            component,
            directory=directory,
            create=create,
            writable_directory=writable_directory,
        )

    monkeypatch.setattr(native_module, "_nt_relative_open", attack_with_complete_clone)
    receipt = transaction.finalize(finalized_at_utc="2026-09-01T00:00:10Z")

    assert attacked is True
    assert blocked_error is not None
    assert not parked.exists()
    assert transaction.phase is DurableAcquisitionPhase.FINALIZED
    assert not (clone / ACQUISITION_FINALIZATION_FILENAME).exists()
    shutil.copy2(
        root / ACQUISITION_FINALIZATION_FILENAME,
        clone / ACQUISITION_FINALIZATION_FILENAME,
    )
    assert _relative_files(clone) == _relative_files(root)
    with pytest.raises(RouteEvidenceIntegrityError, match="transaction root identity differs"):
        load_durable_acquisition(clone, receipt.expectation)


def test_handle_anchored_replaced_empty_case_directory_never_receives_frame_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if sys.platform != HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM:
        assert HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE is False
        return
    import mining_automation.navigation._windows_handle_anchored as native_module

    root = tmp_path / "replaced-case-acquisition"
    parked = tmp_path / "parked-owned-case"
    transaction, source, detector, clock = _begin_handle_anchored_acquisition(root)
    original_open = native_module._nt_relative_open
    replacement: Path | None = None

    def replace_case_before_frame(
        parent_handle: int,
        component: str,
        *,
        directory: bool,
        create: bool,
        writable_directory: bool = False,
    ) -> int:
        nonlocal replacement
        if create and not directory and component == "frame.bin" and replacement is None:
            case_directories = tuple((root / "cases").iterdir())
            assert len(case_directories) == 1
            replacement = case_directories[0]
            replacement.rename(parked)
            replacement.mkdir()
            (replacement / "foreign-sentinel.bin").write_bytes(b"foreign-owner")
        return original_open(
            parent_handle,
            component,
            directory=directory,
            create=create,
            writable_directory=writable_directory,
        )

    monkeypatch.setattr(native_module, "_nt_relative_open", replace_case_before_frame)

    with pytest.raises(DurableEvidenceError, match="cannot inspect handle-owned path"):
        _capture_next(transaction, source, detector, clock, 1)

    assert replacement is not None
    assert transaction.phase is DurableAcquisitionPhase.STOPPED
    assert (parked / "frame.bin").is_file()
    assert (replacement / "foreign-sentinel.bin").read_bytes() == b"foreign-owner"
    assert not (replacement / "frame.bin").exists()
    assert not (root / ACQUISITION_FINALIZATION_FILENAME).exists()


def test_handle_anchored_case_junction_substitution_never_receives_frame_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if sys.platform != HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM:
        assert HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE is False
        return
    import mining_automation.navigation._windows_handle_anchored as native_module

    root = tmp_path / "junction-case-acquisition"
    parked = tmp_path / "parked-junction-owned-case"
    foreign_target = tmp_path / "foreign-junction-target"
    foreign_target.mkdir()
    (foreign_target / "foreign-sentinel.bin").write_bytes(b"foreign-junction-owner")
    transaction, source, detector, clock = _begin_handle_anchored_acquisition(root)
    original_open = native_module._nt_relative_open
    junction: Path | None = None

    def install_junction_before_frame(
        parent_handle: int,
        component: str,
        *,
        directory: bool,
        create: bool,
        writable_directory: bool = False,
    ) -> int:
        nonlocal junction
        if create and not directory and component == "frame.bin" and junction is None:
            case_directories = tuple((root / "cases").iterdir())
            assert len(case_directories) == 1
            junction = case_directories[0]
            junction.rename(parked)
            subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(foreign_target)],
                check=True,
                capture_output=True,
                text=True,
            )
        return original_open(
            parent_handle,
            component,
            directory=directory,
            create=create,
            writable_directory=writable_directory,
        )

    monkeypatch.setattr(native_module, "_nt_relative_open", install_junction_before_frame)

    with pytest.raises(DurableEvidenceError, match="cannot inspect handle-owned path"):
        _capture_next(transaction, source, detector, clock, 1)

    assert junction is not None
    assert transaction.phase is DurableAcquisitionPhase.STOPPED
    assert loader_module._is_link_or_reparse(junction.lstat())
    assert (parked / "frame.bin").is_file()
    assert (foreign_target / "foreign-sentinel.bin").read_bytes() == b"foreign-junction-owner"
    assert not (foreign_target / "frame.bin").exists()
    assert not (root / ACQUISITION_FINALIZATION_FILENAME).exists()


def test_handle_anchored_unexpected_native_create_result_stops_without_adoption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if sys.platform != HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM:
        assert HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE is False
        return
    import mining_automation.navigation._windows_handle_anchored as native_module

    root = tmp_path / "unexpected-create-result"
    transaction, source, detector, clock = _begin_handle_anchored_acquisition(root)
    original_create = native_module._ntdll.NtCreateFile
    injected = False

    def unexpected_information(*args: object) -> int:
        nonlocal injected
        status = int(original_create(*args))
        disposition = int(cast(int, args[7]))
        if status >= 0 and disposition == native_module._FILE_CREATE and not injected:
            io_status = native_module.ctypes.cast(
                args[3],
                native_module.ctypes.POINTER(native_module._IO_STATUS_BLOCK),
            ).contents
            io_status.Information = native_module._FILE_OPENED
            injected = True
        return status

    monkeypatch.setattr(native_module._ntdll, "NtCreateFile", unexpected_information)

    with pytest.raises(DurableEvidenceError, match="unexpected create result"):
        _capture_next(transaction, source, detector, clock, 1)

    assert injected is True
    assert transaction.phase is DurableAcquisitionPhase.STOPPED
    assert not (root / ACQUISITION_FINALIZATION_FILENAME).exists()


def test_handle_anchored_parent_reparse_capability_failure_precedes_root_create(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if sys.platform != HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM:
        assert HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE is False
        return
    import mining_automation.navigation._windows_handle_anchored as native_module

    root = tmp_path / "simulated-reparse-root"
    original_info = native_module._native_info

    def simulated_reparse(handle: int, context: str) -> object:
        if context == "transaction parent":
            raise HandleAnchoredEvidenceCapabilityError("transaction parent is a reparse point")
        return original_info(handle, context)

    monkeypatch.setattr(native_module, "_native_info", simulated_reparse)
    plan, source, detector, clock = _runtime()

    with pytest.raises(HandleAnchoredEvidenceCapabilityError, match="reparse point"):
        begin_handle_anchored_acquisition(
            root,
            plan,
            source,
            detector,
            clock,
            started_monotonic_s=0.0,
        )

    assert not root.exists()
    assert source.identity_calls == 0
    assert detector.calls == 0
    assert clock.calls == 0


def test_handle_anchored_hard_link_alias_stops_before_later_evidence(
    tmp_path: Path,
) -> None:
    if sys.platform != HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM:
        assert HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE is False
        return
    root = tmp_path / "hard-link-acquisition"
    transaction, source, detector, clock = _begin_handle_anchored_acquisition(root)
    _capture_next(transaction, source, detector, clock, 1)
    first = transaction.progress.captures[0].owned_case.frame_artifact.relative_path
    target = root.joinpath(*first.split("/"))
    alias = tmp_path / "foreign-hard-link.bgra"
    os.link(target, alias)

    with pytest.raises(DurableEvidenceError, match="handle-anchored file changed"):
        transaction.request_capture(
            request_id="synthetic-durable-request-2",
            operator_id=transaction.progress.plan.operator_id,
            acknowledged_monotonic_s=20.0,
        )

    assert transaction.phase is DurableAcquisitionPhase.STOPPED
    assert alias.is_file()
    assert alias.read_bytes() == target.read_bytes()
    assert not (root / ACQUISITION_FINALIZATION_FILENAME).exists()


def test_handle_anchored_hard_link_race_after_create_stops_without_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if sys.platform != HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM:
        assert HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE is False
        return
    import mining_automation.navigation._windows_handle_anchored as native_module

    root = tmp_path / "hard-link-race-acquisition"
    alias = tmp_path / "foreign-plan-alias.json"
    original_write = native_module._write_and_readback
    linked = False

    def link_after_exclusive_create(
        handle: int,
        payload: bytes,
        relative_path: str,
    ) -> os.stat_result:
        nonlocal linked
        if relative_path == ACQUISITION_PLAN_FILENAME and not linked:
            os.link(root / relative_path, alias)
            linked = True
        return original_write(handle, payload, relative_path)

    monkeypatch.setattr(native_module, "_write_and_readback", link_after_exclusive_create)
    plan, source, detector, clock = _runtime()

    with pytest.raises(DurableEvidenceError, match="changed during write"):
        begin_handle_anchored_acquisition(
            root,
            plan,
            source,
            detector,
            clock,
            started_monotonic_s=0.0,
        )

    assert linked is True
    assert alias.read_bytes() == (root / ACQUISITION_PLAN_FILENAME).read_bytes()
    assert not (root / ACQUISITION_FINALIZATION_FILENAME).exists()


def test_handle_anchored_invalidated_root_handle_stops_without_path_fallback(
    tmp_path: Path,
) -> None:
    if sys.platform != HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM:
        assert HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE is False
        return
    import mining_automation.navigation._windows_handle_anchored as native_module

    root = tmp_path / "invalid-handle-acquisition"
    transaction, _, _, _ = _begin_handle_anchored_acquisition(root)
    namespace = transaction._namespace
    native_module._close_handle(namespace._root.handle)

    with pytest.raises(HandleAnchoredEvidenceCapabilityError, match="not a disk handle"):
        transaction.request_capture(
            request_id="synthetic-invalid-handle-request",
            operator_id=transaction.progress.plan.operator_id,
            acknowledged_monotonic_s=10.0,
        )

    assert transaction.phase is DurableAcquisitionPhase.STOPPED
    assert not (root / ACQUISITION_STOP_FILENAME).exists()
    assert not (root / ACQUISITION_FINALIZATION_FILENAME).exists()
    assert (root / ACQUISITION_PLAN_FILENAME).is_file()
    moved = tmp_path / "invalid-handle-acquisition-moved"
    root.rename(moved)
    moved.rename(root)


def test_handle_anchored_partial_write_failure_retains_only_owned_audit_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if sys.platform != HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM:
        assert HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE is False
        return
    import mining_automation.navigation._windows_handle_anchored as native_module

    root = tmp_path / "partial-handle-acquisition"
    transaction, source, detector, clock = _begin_handle_anchored_acquisition(root)
    original_write = native_module._write_and_readback

    def fail_frame(handle: int, payload: bytes, relative_path: str) -> os.stat_result:
        if relative_path.endswith("/frame.bin"):
            raise OSError("synthetic handle write failure")
        return original_write(handle, payload, relative_path)

    monkeypatch.setattr(native_module, "_write_and_readback", fail_frame)
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

    with pytest.raises(OSError, match="synthetic handle write failure"):
        transaction.capture()

    assert transaction.phase is DurableAcquisitionPhase.STOPPED
    assert (root / ACQUISITION_PLAN_FILENAME).is_file()
    assert any(path.name.endswith("-request.json") for path in root.rglob("*.json"))
    assert not (root / FINALIZED_PACKAGE_FILENAME).exists()
    assert not (root / ACQUISITION_FINALIZATION_FILENAME).exists()


def test_handle_anchored_foreign_file_survives_finalization_failure(
    tmp_path: Path,
) -> None:
    if sys.platform != HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM:
        assert HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE is False
        return
    root = tmp_path / "foreign-prefix-acquisition"
    transaction, source, detector, clock = _begin_handle_anchored_acquisition(root)
    for ordinal in range(1, len(transaction.progress.plan.cases) + 1):
        _capture_next(transaction, source, detector, clock, ordinal)
    sentinel = root / "foreign-sentinel.bin"
    sentinel.write_bytes(b"foreign-owner")
    clock.value = 40.0

    with pytest.raises(RouteEvidenceIntegrityError, match="foreign_files"):
        transaction.finalize(finalized_at_utc="2026-09-01T00:00:10Z")

    assert transaction.phase is DurableAcquisitionPhase.STOPPED
    assert sentinel.read_bytes() == b"foreign-owner"
    assert not (root / ACQUISITION_FINALIZATION_FILENAME).exists()


def test_handle_anchored_review_parent_replacement_before_root_create_is_isolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if sys.platform != HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM:
        assert HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE is False
        return
    import mining_automation.navigation._windows_handle_anchored as native_module

    acquisition_root = tmp_path / "review-swap-acquisition"
    acquisition_receipt, _ = _complete_handle_anchored_acquisition(acquisition_root)
    parent = tmp_path / "owned-review-parent"
    parent.mkdir()
    review_root = parent / "handle-review"
    parked_parent = tmp_path / "parked-owned-review-parent"
    replacement_sentinel = parent / "foreign-sentinel.bin"
    original_open = native_module._nt_relative_open
    swapped = False

    def swap_before_review_root_create(
        parent_handle: int,
        component: str,
        *,
        directory: bool,
        create: bool,
        writable_directory: bool = False,
    ) -> int:
        nonlocal swapped
        if create and directory and component == review_root.name and not swapped:
            parent.rename(parked_parent)
            parent.mkdir()
            replacement_sentinel.write_bytes(b"foreign-review-owner")
            swapped = True
        return original_open(
            parent_handle,
            component,
            directory=directory,
            create=create,
            writable_directory=writable_directory,
        )

    monkeypatch.setattr(native_module, "_nt_relative_open", swap_before_review_root_create)

    with pytest.raises(DurableEvidenceError, match="cannot inspect handle-owned path"):
        begin_handle_anchored_review(
            review_root,
            acquisition_root,
            acquisition_receipt.expectation,
            review_id="synthetic-swapped-review",
            reviewer_id="synthetic-swapped-reviewer",
            started_at_utc="2026-09-01T00:00:20Z",
        )

    assert swapped is True
    assert (parked_parent / review_root.name).is_dir()
    assert _relative_files(parked_parent / review_root.name) == set()
    assert replacement_sentinel.read_bytes() == b"foreign-review-owner"
    assert not review_root.exists()


@pytest.mark.parametrize("target", ("parent", "root"))
@pytest.mark.parametrize("stage", ("truth", "review-manifest", "final-manifest"))
def test_handle_anchored_review_mid_transaction_replacement_is_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    stage: str,
) -> None:
    if sys.platform != HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM:
        assert HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE is False
        return
    import mining_automation.navigation._windows_handle_anchored as native_module

    acquisition_root = tmp_path / f"{stage}-{target}-acquisition"
    acquisition_receipt, _ = _complete_handle_anchored_acquisition(acquisition_root)
    parent = tmp_path / f"{stage}-{target}-review-parent"
    parent.mkdir()
    review_root = parent / "handle-review"
    transaction, acquisition = _begin_handle_anchored_review(
        review_root,
        acquisition_root,
        acquisition_receipt,
    )
    truths = _truths(acquisition)
    if stage != "truth":
        for ordinal, truth in enumerate(truths, start=1):
            transaction.record_case_truth(
                truth,
                recorded_at_utc=f"2026-09-01T00:00:{20 + ordinal:02d}Z",
            )

    original_open = native_module._nt_relative_open
    attempted = False
    blocked_error: OSError | None = None
    parked = tmp_path / f"parked-review-{stage}-{target}"

    def stage_matches(component: str, directory: bool, create: bool) -> bool:
        if not create or directory:
            return False
        if stage == "truth":
            return component.startswith("001-") and component.endswith(".json")
        if stage == "review-manifest":
            return component == INDEPENDENT_REVIEW_FILENAME
        return component == REVIEW_FINALIZATION_FILENAME

    def attack_before_review_create(
        parent_handle: int,
        component: str,
        *,
        directory: bool,
        create: bool,
        writable_directory: bool = False,
    ) -> int:
        nonlocal attempted, blocked_error
        if not attempted and stage_matches(component, directory, create):
            attempted = True
            candidate = parent if target == "parent" else review_root
            try:
                candidate.rename(parked)
            except OSError as exc:
                blocked_error = exc
        return original_open(
            parent_handle,
            component,
            directory=directory,
            create=create,
            writable_directory=writable_directory,
        )

    monkeypatch.setattr(native_module, "_nt_relative_open", attack_before_review_create)

    if stage == "truth":
        phase = transaction.record_case_truth(
            truths[0],
            recorded_at_utc="2026-09-01T00:00:21Z",
        )
        assert phase is DurableReviewPhase.READY_FOR_TRUTH
    else:
        receipt = transaction.finalize(reviewed_at_utc="2026-09-01T00:00:30Z")
        assert receipt.report.evidence_conformance_passed is True
        assert transaction.phase is DurableReviewPhase.FINALIZED

    assert attempted is True
    assert blocked_error is not None
    assert not parked.exists()
    assert review_root.is_dir()


def test_handle_anchored_complete_review_clone_cannot_replace_or_verify(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if sys.platform != HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM:
        assert HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE is False
        return
    import mining_automation.navigation._windows_handle_anchored as native_module

    acquisition_root = tmp_path / "complete-clone-review-acquisition"
    acquisition_receipt, _ = _complete_handle_anchored_acquisition(acquisition_root)
    review_root = tmp_path / "complete-clone-review"
    clone = tmp_path / "complete-clone-review-replacement"
    parked = tmp_path / "complete-clone-review-parked"
    transaction, acquisition = _begin_handle_anchored_review(
        review_root,
        acquisition_root,
        acquisition_receipt,
    )
    for ordinal, truth in enumerate(_truths(acquisition), start=1):
        transaction.record_case_truth(
            truth,
            recorded_at_utc=f"2026-09-01T00:00:{20 + ordinal:02d}Z",
        )
    original_open = native_module._nt_relative_open
    attacked = False
    blocked_error: OSError | None = None

    def attack_with_complete_clone(
        parent_handle: int,
        component: str,
        *,
        directory: bool,
        create: bool,
        writable_directory: bool = False,
    ) -> int:
        nonlocal attacked, blocked_error
        if create and not directory and component == REVIEW_FINALIZATION_FILENAME:
            attacked = True
            shutil.copytree(review_root, clone)
            try:
                review_root.rename(parked)
                clone.rename(review_root)
            except OSError as exc:
                blocked_error = exc
        return original_open(
            parent_handle,
            component,
            directory=directory,
            create=create,
            writable_directory=writable_directory,
        )

    monkeypatch.setattr(native_module, "_nt_relative_open", attack_with_complete_clone)
    review_receipt = transaction.finalize(reviewed_at_utc="2026-09-01T00:00:30Z")
    expectation = _full_expectation(acquisition_receipt.expectation, review_receipt)

    assert attacked is True
    assert blocked_error is not None
    assert not parked.exists()
    assert transaction.phase is DurableReviewPhase.FINALIZED
    assert not (clone / REVIEW_FINALIZATION_FILENAME).exists()
    shutil.copy2(
        review_root / REVIEW_FINALIZATION_FILENAME,
        clone / REVIEW_FINALIZATION_FILENAME,
    )
    assert _relative_files(clone) == _relative_files(review_root)
    with pytest.raises(RouteEvidenceIntegrityError, match="transaction root identity differs"):
        load_and_verify_durable_synthetic_route_evidence(
            acquisition_root,
            clone,
            expectation,
        )


def test_two_handle_anchored_review_writers_claim_exactly_one_fresh_root(
    tmp_path: Path,
) -> None:
    if sys.platform != HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM:
        assert HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE is False
        return
    acquisition_root = tmp_path / "review-contention-acquisition"
    review_root = tmp_path / "contended-handle-review"
    acquisition_receipt, _ = _complete_handle_anchored_acquisition(acquisition_root)
    barrier = Barrier(2)

    def claim(writer_id: int) -> str:
        barrier.wait()
        try:
            begin_handle_anchored_review(
                review_root,
                acquisition_root,
                acquisition_receipt.expectation,
                review_id=f"synthetic-handle-review-{writer_id}",
                reviewer_id=f"synthetic-handle-reviewer-{writer_id}",
                started_at_utc="2026-09-01T00:00:20Z",
            )
        except DurableEvidenceCollisionError:
            return "collision"
        return "winner"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(claim, (1, 2)))

    assert sorted(outcomes) == ["collision", "winner"]
    assert (review_root / durable_module.REVIEW_PLAN_FILENAME).is_file()
    assert not (review_root / REVIEW_FINALIZATION_FILENAME).exists()
