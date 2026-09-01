from __future__ import annotations

import ast
import copy
import pickle
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

import mining_automation.navigation as navigation_root
import mining_automation.navigation.integration_boundary as integration_boundary
import mining_automation.navigation.offline_route_session as offline_session_module
import mining_automation.navigation.passive_campaign as passive_module
from mining_automation.capture.frame import Frame, PixelFormat
from mining_automation.contracts import FrameRef
from mining_automation.navigation.contracts import (
    Checkpoint,
    CheckpointDetection,
    CheckpointDetectorIdentity,
    CheckpointEvidenceRole,
    CheckpointMatchKind,
    CheckpointProfile,
    CheckpointRole,
    CheckpointSourceIdentity,
    RouteDirection,
    RouteEndpoint,
    RouteEndpointRole,
    RouteIdentity,
    RoutePlan,
    RouteStep,
    Sha256Digest,
)
from mining_automation.navigation.passive_campaign import (
    PASSIVE_CAPTURE_REQUEST_TIMEOUT_S,
    PassiveCampaignFailureReason,
    PassiveCampaignFinalizationError,
    PassiveCampaignPhase,
    PassiveCampaignSequencer,
    PassiveCaptureRequest,
    PassiveCaptureSourceIdentity,
    PassiveSourceFrame,
)
from mining_automation.navigation.route_evidence import (
    RouteEvidenceCampaignPlan,
    RouteEvidenceCaptureBuildIdentity,
    RouteEvidenceCaseRole,
    RouteEvidenceCaseSpec,
    RouteEvidenceCaseTruth,
    RouteEvidenceLoadExpectation,
    RouteEvidenceReview,
    RouteEvidenceReviewDecision,
    verify_synthetic_route_evidence,
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
    campaign_id: str = "synthetic-passive-campaign-a",
    capture_session_id: str = "synthetic-passive-session-a",
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
        operator_id="synthetic-passive-operator",
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
        self.raises = False
        self.interrupts = False
        self.on_detect: Callable[[], None] | None = None
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
        if self.on_detect is not None:
            self.on_detect()
        if self.interrupts:
            raise KeyboardInterrupt
        if self.raises:
            raise RuntimeError("synthetic detector failure")
        return self.next_detection


class _Clock:
    live_navigation_enabled = False
    input_authority = False

    def __init__(self, value: float = 0.0) -> None:
        self.value = value
        self.on_read: Callable[[], None] | None = None

    def now_monotonic_s(self) -> float:
        if self.on_read is not None:
            self.on_read()
        return self.value


class _Source:
    def __init__(
        self,
        identity: PassiveCaptureSourceIdentity,
        clock: _Clock | None = None,
    ) -> None:
        self._identity = identity
        self.clock = clock or _Clock()
        self.replacement_after_capture: PassiveCaptureSourceIdentity | None = None
        self.result_identity: PassiveCaptureSourceIdentity | None = None
        self.frame_id = 1
        self.frame_time = 1.0
        self.capture_id = "synthetic-capture-1"
        self.captured_at_utc = "2026-09-01T00:00:01Z"
        self.payload_byte = 1
        self.calls = 0
        self.raises = False
        self.interrupts = False
        self.malformed = False
        self.on_capture: Callable[[], None] | None = None
        self.on_identity: Callable[[], None] | None = None
        self.last_result: PassiveSourceFrame | None = None
        self.last_request: PassiveCaptureRequest | None = None

    @property
    def identity(self) -> PassiveCaptureSourceIdentity:
        if self.on_identity is not None:
            self.on_identity()
        if self.calls and self.replacement_after_capture is not None:
            return self.replacement_after_capture
        return self._identity

    def capture(self, request: PassiveCaptureRequest, /) -> PassiveSourceFrame:
        self.calls += 1
        self.last_request = request
        if self.on_capture is not None:
            self.on_capture()
        if self.interrupts:
            raise KeyboardInterrupt
        if self.raises:
            raise RuntimeError("synthetic source failure")
        if self.malformed:
            return object()  # type: ignore[return-value]
        frame = Frame(
            FrameRef(self.frame_id, self.frame_time, 2, 1),
            bytes([self.payload_byte]) * 8,
            PixelFormat.BGRA8888,
        )
        result = PassiveSourceFrame(
            self.result_identity or self._identity,
            request.request_id,
            self.capture_id,
            self.captured_at_utc,
            frame,
        )
        self.last_result = result
        return result


def _runtime(
    direction: RouteDirection = RouteDirection.MINE_TO_BANK,
    **plan_kwargs: str,
) -> tuple[PassiveCampaignSequencer, _Source, _Detector]:
    plan, identity = _campaign(direction, **plan_kwargs)
    clock = _Clock()
    source = _Source(identity, clock)
    detector = _Detector(identity)
    return (
        PassiveCampaignSequencer(
            plan,
            source,
            detector,
            clock,
            started_monotonic_s=0.0,
        ),
        source,
        detector,
    )


def _capture_next(
    sequencer: PassiveCampaignSequencer,
    source: _Source,
    detector: _Detector,
    ordinal: int,
    *,
    detection: CheckpointDetection | None = None,
) -> None:
    acknowledged = float(ordinal * 10)
    spec = sequencer.progress.plan.cases[ordinal - 1]
    sequencer.request_capture(
        request_id=f"synthetic-request-{ordinal}",
        operator_id=sequencer.progress.plan.operator_id,
        acknowledged_monotonic_s=acknowledged,
    )
    source.frame_id = ordinal
    source.frame_time = acknowledged + 1.0
    source.capture_id = f"synthetic-capture-{ordinal}"
    source.captured_at_utc = f"2026-09-01T00:00:0{ordinal}Z"
    source.payload_byte = ordinal
    detector.next_detection = detection or CheckpointDetection(
        CheckpointMatchKind.MATCHED,
        (spec.checkpoint_id,),
        1.0,
    )
    source.clock.value = acknowledged + 2.0
    sequencer.capture()


def _complete(
    direction: RouteDirection = RouteDirection.MINE_TO_BANK,
) -> tuple[PassiveCampaignSequencer, _Source, _Detector]:
    sequencer, source, detector = _runtime(direction)
    for ordinal in range(1, len(sequencer.progress.plan.cases) + 1):
        _capture_next(sequencer, source, detector, ordinal)
    assert sequencer.progress.phase is PassiveCampaignPhase.COMPLETE
    return sequencer, source, detector


@pytest.mark.parametrize("direction", tuple(RouteDirection))
def test_direction_session_finalizes_exact_source_frames_without_input(
    direction: RouteDirection,
) -> None:
    sequencer, source, detector = _complete(direction)
    assert sequencer.progress.review_eligible is False
    source.clock.value = 40.0
    finalization = sequencer.finalize(
        finalized_at_utc="2026-09-01T00:00:10Z",
    )

    assert source.calls == len(finalization.package.cases)
    assert detector.calls == len(finalization.package.cases)
    assert finalization.progress.review_eligible is True
    assert finalization.package.route.direction is direction
    assert finalization.package.acquisition_head_sha256 == (
        finalization.package.cases[-1].content_sha256
    )
    assert finalization.review_created is False
    assert finalization.activation_allowed is False
    assert finalization.input_authority is False
    assert all(
        not case.acquisition.mouse_input_enabled
        and not case.acquisition.keyboard_input_enabled
        and not case.acquisition.navigation_automation_enabled
        for case in finalization.package.cases
    )


def test_operator_acknowledgement_only_requests_the_exact_next_capture() -> None:
    sequencer, _, _ = _runtime()
    progress = sequencer.request_capture(
        request_id="synthetic-request",
        operator_id=sequencer.progress.plan.operator_id,
        acknowledged_monotonic_s=1.0,
    )
    request = progress.pending_request
    assert request is not None
    assert request.case_id == progress.plan.cases[0].case_id
    assert request.operator_acknowledgement_is_reviewer_truth is False
    assert request.checkpoint_truth_asserted is False
    assert request.expires_monotonic_s == 1.0 + PASSIVE_CAPTURE_REQUEST_TIMEOUT_S
    with pytest.raises(ValueError, match="only be issued"):
        replace(request, case_id=progress.plan.cases[1].case_id)


@pytest.mark.parametrize(
    "detection",
    (
        CheckpointDetection(CheckpointMatchKind.UNKNOWN, (), 0.0),
        CheckpointDetection(
            CheckpointMatchKind.AMBIGUOUS,
            ("synthetic-m2b-departure", "synthetic-m2b-transit"),
            0.5,
        ),
    ),
)
def test_nonmatch_detector_output_is_owned_without_retry_or_truth(
    detection: CheckpointDetection,
) -> None:
    sequencer, source, detector = _runtime()
    _capture_next(sequencer, source, detector, 1, detection=detection)
    assert sequencer.progress.phase is PassiveCampaignPhase.READY_FOR_REQUEST
    report = sequencer.progress.captures[0].detector_report_payload
    assert f'"match":"{detection.match.value}"'.encode() in report
    assert sequencer.progress.captures[0].detector_output_is_reviewer_truth is False


def test_single_writer_latches_failure_and_stale_snapshot_cannot_fork() -> None:
    sequencer, source, detector = _runtime()
    stale = sequencer.progress
    stopped = sequencer.request_capture(
        request_id="synthetic-request",
        operator_id="foreign-operator",
        acknowledged_monotonic_s=1.0,
    )
    assert stopped.phase is PassiveCampaignPhase.STOPPED
    assert stopped.failure is not None
    assert stopped.failure.reason is PassiveCampaignFailureReason.OPERATOR_MISMATCH
    assert stale.phase is PassiveCampaignPhase.READY_FOR_REQUEST

    result = sequencer.capture()
    assert result == stopped
    assert source.calls == 0
    with pytest.raises(PassiveCampaignFinalizationError):
        sequencer.finalize(
            finalized_at_utc="2026-09-01T00:00:10Z",
        )


def test_one_request_invokes_source_once_and_exception_cannot_retry() -> None:
    sequencer, source, detector = _runtime()
    sequencer.request_capture(
        request_id="synthetic-request",
        operator_id=sequencer.progress.plan.operator_id,
        acknowledged_monotonic_s=1.0,
    )
    source.raises = True
    source.clock.value = 2.0
    first = sequencer.capture()
    source.clock.value = 3.0
    second = sequencer.capture()
    assert first == second
    assert first.failure is not None
    assert first.failure.reason is PassiveCampaignFailureReason.CAPTURE_FAILED
    assert source.calls == 1


@pytest.mark.parametrize("malformed", (True, False))
def test_malformed_source_or_detector_failure_latches_stop(malformed: bool) -> None:
    sequencer, source, detector = _runtime()
    sequencer.request_capture(
        request_id="synthetic-request",
        operator_id=sequencer.progress.plan.operator_id,
        acknowledged_monotonic_s=1.0,
    )
    source.frame_time = 2.0
    source.malformed = malformed
    detector.raises = not malformed
    source.clock.value = 3.0
    stopped = sequencer.capture()
    assert stopped.phase is PassiveCampaignPhase.STOPPED
    assert stopped.failure is not None
    expected = (
        PassiveCampaignFailureReason.CAPTURE_CONTRACT_MISMATCH
        if malformed
        else PassiveCampaignFailureReason.CAPTURE_FAILED
    )
    assert stopped.failure.reason is expected


def test_source_identity_toctou_and_foreign_result_stop() -> None:
    sequencer, source, detector = _runtime()
    _, replacement = _campaign(
        campaign_id="synthetic-replacement",
        capture_session_id="synthetic-replacement-session",
    )
    sequencer.request_capture(
        request_id="synthetic-request",
        operator_id=sequencer.progress.plan.operator_id,
        acknowledged_monotonic_s=1.0,
    )
    source.frame_time = 2.0
    source.replacement_after_capture = replacement
    source.clock.value = 3.0
    stopped = sequencer.capture()
    assert stopped.failure is not None
    assert stopped.failure.reason is PassiveCampaignFailureReason.SOURCE_REPLACED

    sequencer2, source2, detector2 = _runtime()
    sequencer2.request_capture(
        request_id="synthetic-request",
        operator_id=sequencer2.progress.plan.operator_id,
        acknowledged_monotonic_s=1.0,
    )
    source2.frame_time = 2.0
    source2.result_identity = replacement
    source2.clock.value = 3.0
    stopped2 = sequencer2.capture()
    assert stopped2.failure is not None
    assert stopped2.failure.reason is PassiveCampaignFailureReason.FRAME_PROVENANCE_MISMATCH


@pytest.mark.parametrize(
    ("frame_offset", "evaluation_offset", "reason"),
    (
        (0.0, 2.0, PassiveCampaignFailureReason.FRAME_NOT_AFTER_REQUEST),
        (3.0, 2.0, PassiveCampaignFailureReason.FRAME_FROM_FUTURE),
        (1.0, 31.0, PassiveCampaignFailureReason.CAPTURE_TIMEOUT),
    ),
)
def test_stale_equal_future_and_timeout_capture_events_fail_closed(
    frame_offset: float,
    evaluation_offset: float,
    reason: PassiveCampaignFailureReason,
) -> None:
    sequencer, source, detector = _runtime()
    sequencer.request_capture(
        request_id="synthetic-request",
        operator_id=sequencer.progress.plan.operator_id,
        acknowledged_monotonic_s=10.0,
    )
    source.frame_time = 10.0 + frame_offset
    source.clock.value = 10.0 + evaluation_offset
    stopped = sequencer.capture()
    assert stopped.failure is not None
    assert stopped.failure.reason is reason


def test_bound_clock_records_completion_and_detects_late_callbacks() -> None:
    sequencer, source, detector = _runtime()
    sequencer.request_capture(
        request_id="synthetic-clocked-request",
        operator_id=sequencer.progress.plan.operator_id,
        acknowledged_monotonic_s=1.0,
    )
    source.frame_time = 2.0
    source.clock.value = 1.5
    source.on_capture = lambda: setattr(source.clock, "value", 2.5)
    detector.on_detect = lambda: setattr(source.clock, "value", 3.0)

    captured = sequencer.capture()

    assert captured.failure is None
    assert captured.captures[0].recorded_monotonic_s == 3.0
    assert captured.captures[0].owned_case.acquisition.recorded_monotonic_s == 3.0

    late, late_source, late_detector = _runtime()
    late.request_capture(
        request_id="synthetic-late-request",
        operator_id=late.progress.plan.operator_id,
        acknowledged_monotonic_s=1.0,
    )
    late_source.frame_time = 2.0
    late_source.clock.value = 2.0
    late_source.on_capture = lambda: setattr(late_source.clock, "value", 32.0)

    stopped = late.capture()

    assert stopped.failure is not None
    assert stopped.failure.reason is PassiveCampaignFailureReason.CAPTURE_TIMEOUT
    assert late_source.calls == 1
    assert late_detector.calls == 0


def test_detector_delay_past_expiry_cannot_use_an_earlier_clock_read() -> None:
    sequencer, source, detector = _runtime()
    sequencer.request_capture(
        request_id="synthetic-late-detector-request",
        operator_id=sequencer.progress.plan.operator_id,
        acknowledged_monotonic_s=1.0,
    )
    source.frame_time = 2.0
    source.clock.value = 2.0
    detector.on_detect = lambda: setattr(source.clock, "value", 32.0)

    stopped = sequencer.capture()

    assert stopped.failure is not None
    assert stopped.failure.reason is PassiveCampaignFailureReason.CAPTURE_TIMEOUT
    assert stopped.captures == ()


def test_public_snapshots_cannot_rewrite_internal_capture_or_finalization() -> None:
    sequencer, source, detector = _runtime()
    _capture_next(sequencer, source, detector, 1)
    published = sequencer.progress
    original_payload = published.captures[0].frame_payload

    object.__setattr__(published.captures[0].owned_case, "activation_allowed", True)
    object.__setattr__(published.captures[0], "frame_payload", b"tampered")

    fresh = sequencer.progress
    assert fresh.captures[0].owned_case.activation_allowed is False
    assert fresh.captures[0].frame_payload == original_payload
    for ordinal in (2, 3):
        _capture_next(sequencer, source, detector, ordinal)
    source.clock.value = 40.0
    finalization = sequencer.finalize(finalized_at_utc="2026-09-01T00:00:10Z")

    object.__setattr__(finalization.progress, "input_authority", True)
    object.__setattr__(finalization.package, "activation_allowed", True)
    fresh_finalization = sequencer.finalization
    assert fresh_finalization is not None
    assert fresh_finalization.progress.input_authority is False
    assert fresh_finalization.package.activation_allowed is False


def test_public_and_source_request_mutation_cannot_change_issued_request() -> None:
    sequencer, source, detector = _runtime()
    requested = sequencer.request_capture(
        request_id="synthetic-request-snapshot",
        operator_id=sequencer.progress.plan.operator_id,
        acknowledged_monotonic_s=1.0,
    )
    assert requested.pending_request is not None
    object.__setattr__(requested.pending_request, "case_id", "forged-public-case")
    source.frame_time = 2.0
    source.clock.value = 3.0
    accepted = sequencer.capture()
    assert accepted.failure is None

    forged, forged_source, _ = _runtime()
    forged.request_capture(
        request_id="synthetic-source-request-mutation",
        operator_id=forged.progress.plan.operator_id,
        acknowledged_monotonic_s=1.0,
    )
    forged_source.frame_time = 2.0
    forged_source.clock.value = 3.0

    def mutate_source_request() -> None:
        assert forged_source.last_request is not None
        object.__setattr__(forged_source.last_request, "case_id", "forged-source-case")

    forged_source.on_capture = mutate_source_request
    stopped = forged.capture()
    assert stopped.failure is not None
    assert stopped.failure.reason is PassiveCampaignFailureReason.FRAME_PROVENANCE_MISMATCH


def test_duplicate_capture_id_stops_without_dropping_first_capture() -> None:
    sequencer, source, detector = _runtime()
    _capture_next(sequencer, source, detector, 1)
    first = sequencer.progress.captures[0]
    sequencer.request_capture(
        request_id="synthetic-request-2",
        operator_id=sequencer.progress.plan.operator_id,
        acknowledged_monotonic_s=20.0,
    )
    source.frame_id = 2
    source.frame_time = 21.0
    source.capture_id = first.owned_case.capture_id
    source.captured_at_utc = "2026-09-01T00:00:02Z"
    source.payload_byte = 2
    source.clock.value = 22.0
    stopped = sequencer.capture()
    assert stopped.failure is not None
    assert stopped.failure.reason is PassiveCampaignFailureReason.DUPLICATE_CAPTURE
    assert stopped.captures == (first,)


def test_failure_after_complete_prevents_finalization_and_double_finalization() -> None:
    sequencer, source, _ = _complete()
    stopped = sequencer.fail(
        PassiveCampaignFailureReason.INTERRUPTED,
        evaluated_monotonic_s=33.0,
    )
    assert stopped.phase is PassiveCampaignPhase.STOPPED
    with pytest.raises(PassiveCampaignFinalizationError):
        source.clock.value = 40.0
        sequencer.finalize(
            finalized_at_utc="2026-09-01T00:00:10Z",
        )

    complete, complete_source, _ = _complete()
    complete_source.clock.value = 40.0
    finalization = complete.finalize(
        finalized_at_utc="2026-09-01T00:00:10Z",
    )
    with pytest.raises(PassiveCampaignFinalizationError):
        complete_source.clock.value = 41.0
        complete.finalize(
            finalized_at_utc="2026-09-01T00:00:11Z",
        )
    assert complete.finalization == finalization


def test_backdated_finalization_latches_stop() -> None:
    sequencer, source, _ = _complete()
    source.clock.value = 32.0
    with pytest.raises(PassiveCampaignFinalizationError, match="follow"):
        sequencer.finalize(
            finalized_at_utc="2026-09-01T00:00:10Z",
        )
    assert sequencer.progress.phase is PassiveCampaignPhase.STOPPED


def test_malformed_finalization_latches_stop_and_cannot_retry() -> None:
    sequencer, source, _ = _complete()
    source.clock.value = 40.0

    with pytest.raises(PassiveCampaignFinalizationError, match="malformed"):
        sequencer.finalize(finalized_at_utc="not-utc")

    stopped = sequencer.progress
    assert stopped.phase is PassiveCampaignPhase.STOPPED
    assert stopped.failure is not None
    assert stopped.failure.reason is PassiveCampaignFailureReason.FINALIZATION_FAILED
    with pytest.raises(PassiveCampaignFinalizationError, match="complete"):
        sequencer.finalize(finalized_at_utc="2026-09-01T00:00:10Z")
    assert sequencer.finalization is None


def test_equal_metadata_adapter_replacement_stops_finalization() -> None:
    sequencer, source, _ = _complete()
    replacement = _Source(sequencer.progress.source, source.clock)
    source.clock.value = 40.0

    def replace_bound_source() -> None:
        source.on_identity = None
        object.__setattr__(sequencer, "_source", replacement)

    source.on_identity = replace_bound_source
    with pytest.raises(PassiveCampaignFinalizationError, match="changed"):
        sequencer.finalize(finalized_at_utc="2026-09-01T00:00:10Z")

    stopped = sequencer.progress
    assert stopped.phase is PassiveCampaignPhase.STOPPED
    assert stopped.failure is not None
    assert stopped.failure.reason is PassiveCampaignFailureReason.SOURCE_REPLACED


def test_detector_equality_spoof_cannot_satisfy_exact_finalization_contract() -> None:
    class _EqualitySpoof:
        def __eq__(self, other: object) -> bool:
            del other
            return True

        def __ne__(self, other: object) -> bool:
            del other
            return False

    sequencer, source, detector = _complete()
    detector._identity = _EqualitySpoof()  # type: ignore[assignment]
    detector._profile = _EqualitySpoof()  # type: ignore[assignment]
    source.clock.value = 40.0

    with pytest.raises(PassiveCampaignFinalizationError, match="changed before finalization"):
        sequencer.finalize(finalized_at_utc="2026-09-01T00:00:10Z")

    stopped = sequencer.progress
    assert stopped.failure is not None
    assert stopped.failure.reason is PassiveCampaignFailureReason.SOURCE_REPLACED


def test_nonincreasing_capture_utc_stops_before_detector_and_cannot_retry() -> None:
    sequencer, source, detector = _runtime()
    _capture_next(sequencer, source, detector, 1)
    sequencer.request_capture(
        request_id="synthetic-request-2",
        operator_id=sequencer.progress.plan.operator_id,
        acknowledged_monotonic_s=20.0,
    )
    source.frame_id = 2
    source.frame_time = 21.0
    source.capture_id = "synthetic-capture-2"
    source.payload_byte = 2
    source.captured_at_utc = "2026-09-01T00:00:01Z"

    source.clock.value = 22.0
    stopped = sequencer.capture()
    source.clock.value = 23.0
    repeated = sequencer.capture()

    assert stopped == repeated
    assert stopped.failure is not None
    assert stopped.failure.reason is PassiveCampaignFailureReason.FRAME_PROVENANCE_MISMATCH
    assert source.calls == 2
    assert detector.calls == 1
    assert len(stopped.captures) == 1


def test_equal_time_next_acknowledgement_stops_before_source_invocation() -> None:
    sequencer, source, detector = _runtime()
    _capture_next(sequencer, source, detector, 1)

    stopped = sequencer.request_capture(
        request_id="synthetic-request-2",
        operator_id=sequencer.progress.plan.operator_id,
        acknowledged_monotonic_s=12.0,
    )
    repeated = sequencer.capture()

    assert stopped == repeated
    assert stopped.failure is not None
    assert stopped.failure.reason is PassiveCampaignFailureReason.OUT_OF_ORDER_EVENT
    assert source.calls == 1
    assert detector.calls == 1


@pytest.mark.parametrize("interrupt_stage", ("source", "detector"))
def test_base_exception_latches_interruption_before_propagation(
    interrupt_stage: str,
) -> None:
    sequencer, source, detector = _runtime()
    sequencer.request_capture(
        request_id="synthetic-request",
        operator_id=sequencer.progress.plan.operator_id,
        acknowledged_monotonic_s=1.0,
    )
    source.frame_time = 2.0
    if interrupt_stage == "source":
        source.interrupts = True
    else:
        detector.interrupts = True

    source.clock.value = 3.0
    with pytest.raises(KeyboardInterrupt):
        sequencer.capture()
    stopped = sequencer.progress
    source.clock.value = 4.0
    repeated = sequencer.capture()

    assert stopped == repeated
    assert stopped.failure is not None
    assert stopped.failure.reason is PassiveCampaignFailureReason.INTERRUPTED
    assert source.calls == 1
    assert detector.calls == (0 if interrupt_stage == "source" else 1)


@pytest.mark.parametrize("callback", ("stop", "reenter"))
def test_source_callback_cannot_reenter_or_resurrect_campaign(callback: str) -> None:
    sequencer, source, detector = _runtime()
    sequencer.request_capture(
        request_id="synthetic-request",
        operator_id=sequencer.progress.plan.operator_id,
        acknowledged_monotonic_s=1.0,
    )
    source.frame_time = 2.0
    if callback == "stop":
        source.on_capture = lambda: sequencer.fail(
            PassiveCampaignFailureReason.INTERRUPTED,
            evaluated_monotonic_s=2.0,
        )
        expected = PassiveCampaignFailureReason.INTERRUPTED
    else:
        source.on_capture = lambda: sequencer.capture()
        expected = PassiveCampaignFailureReason.CAPTURE_REENTRANCY

    source.clock.value = 3.0
    stopped = sequencer.capture()

    assert stopped.phase is PassiveCampaignPhase.STOPPED
    assert stopped.failure is not None
    assert stopped.failure.reason is expected
    assert stopped.captures == ()
    assert source.calls == 1
    assert detector.calls == 0


def test_detector_cannot_replace_bound_source_after_initial_capture_check() -> None:
    sequencer, source, detector = _runtime()
    _, replacement = _campaign(
        campaign_id="synthetic-replacement",
        capture_session_id="synthetic-replacement-session",
    )
    sequencer.request_capture(
        request_id="synthetic-request",
        operator_id=sequencer.progress.plan.operator_id,
        acknowledged_monotonic_s=1.0,
    )
    source.frame_time = 2.0
    detector.on_detect = lambda: setattr(source, "replacement_after_capture", replacement)

    source.clock.value = 3.0
    stopped = sequencer.capture()

    assert stopped.failure is not None
    assert stopped.failure.reason is PassiveCampaignFailureReason.SOURCE_REPLACED
    assert stopped.captures == ()
    assert source.calls == 1
    assert detector.calls == 1


def test_detector_cannot_relabel_source_result_after_capture() -> None:
    sequencer, source, detector = _runtime()
    sequencer.request_capture(
        request_id="synthetic-request",
        operator_id=sequencer.progress.plan.operator_id,
        acknowledged_monotonic_s=1.0,
    )
    source.frame_time = 2.0

    def forge_capture_id() -> None:
        assert source.last_result is not None
        object.__setattr__(source.last_result, "capture_id", "forged-capture-id")

    detector.on_detect = forge_capture_id
    source.clock.value = 3.0
    stopped = sequencer.capture()

    assert stopped.failure is not None
    assert stopped.failure.reason is PassiveCampaignFailureReason.FRAME_PROVENANCE_MISMATCH
    assert stopped.captures == ()
    assert source.calls == 1
    assert detector.calls == 1


def test_explicit_restart_requires_fresh_lineage_and_blocks_aba() -> None:
    sequencer, _, _ = _runtime()
    sequencer.fail(
        PassiveCampaignFailureReason.INTERRUPTED,
        evaluated_monotonic_s=1.0,
    )
    replacement_plan, replacement_identity = _campaign(
        campaign_id="synthetic-passive-campaign-b",
        capture_session_id="synthetic-passive-session-b",
    )
    replacement_source = _Source(replacement_identity)
    replacement_detector = _Detector(replacement_identity)
    replacement_source.clock.value = 2.0
    restarted = sequencer.restart(
        replacement_plan,
        replacement_source,
        replacement_detector,
        replacement_source.clock,
        started_monotonic_s=2.0,
    )
    assert restarted.phase is PassiveCampaignPhase.READY_FOR_REQUEST
    sequencer.fail(
        PassiveCampaignFailureReason.INTERRUPTED,
        evaluated_monotonic_s=3.0,
    )
    original_plan, original_identity = _campaign()
    with pytest.raises(ValueError, match="reuse any campaign id"):
        original_source = _Source(original_identity)
        original_source.clock.value = 4.0
        sequencer.restart(
            original_plan,
            original_source,
            _Detector(original_identity),
            original_source.clock,
            started_monotonic_s=4.0,
        )


def test_restart_clears_old_pending_request_and_retains_retired_audit() -> None:
    sequencer, _, _ = _runtime()
    sequencer.request_capture(
        request_id="synthetic-retired-request",
        operator_id=sequencer.progress.plan.operator_id,
        acknowledged_monotonic_s=1.0,
    )
    stopped = sequencer.fail(
        PassiveCampaignFailureReason.INTERRUPTED,
        evaluated_monotonic_s=2.0,
    )
    replacement_plan, replacement_identity = _campaign(
        campaign_id="synthetic-fresh-campaign",
        capture_session_id="synthetic-fresh-session",
    )
    replacement_source = _Source(replacement_identity)
    replacement_source.clock.value = 3.0
    sequencer.restart(
        replacement_plan,
        replacement_source,
        _Detector(replacement_identity),
        replacement_source.clock,
        started_monotonic_s=3.0,
    )

    fresh_stop = sequencer.fail(
        PassiveCampaignFailureReason.INTERRUPTED,
        evaluated_monotonic_s=4.0,
    )

    assert fresh_stop.failure is not None
    assert fresh_stop.failure.request is None
    assert len(sequencer.retired_sessions) == 1
    retired = sequencer.retired_sessions[0]
    assert retired == stopped
    assert retired.failure is not None
    assert retired.failure.request is not None
    assert retired.failure.request.request_id == "synthetic-retired-request"


def test_restart_callback_cannot_fork_or_overwrite_the_stopped_head() -> None:
    sequencer, _, _ = _runtime()
    stopped = sequencer.fail(
        PassiveCampaignFailureReason.INTERRUPTED,
        evaluated_monotonic_s=1.0,
    )
    outer_plan, outer_identity = _campaign(
        campaign_id="synthetic-outer-campaign",
        capture_session_id="synthetic-outer-session",
    )
    inner_plan, inner_identity = _campaign(
        campaign_id="synthetic-inner-campaign",
        capture_session_id="synthetic-inner-session",
    )
    outer_source = _Source(outer_identity)
    outer_detector = _Detector(outer_identity)
    outer_source.clock.value = 2.0

    def attempt_nested_restart() -> None:
        outer_source.on_identity = None
        with pytest.raises(ValueError, match="reenter"):
            inner_source = _Source(inner_identity)
            inner_source.clock.value = 2.0
            sequencer.restart(
                inner_plan,
                inner_source,
                _Detector(inner_identity),
                inner_source.clock,
                started_monotonic_s=2.0,
            )

    outer_source.on_identity = attempt_nested_restart
    with pytest.raises(ValueError, match="invalidated"):
        sequencer.restart(
            outer_plan,
            outer_source,
            outer_detector,
            outer_source.clock,
            started_monotonic_s=2.0,
        )
    assert sequencer.progress == stopped

    restarted = sequencer.restart(
        outer_plan,
        outer_source,
        outer_detector,
        outer_source.clock,
        started_monotonic_s=2.0,
    )
    assert restarted.plan == outer_plan
    assert restarted.plan is not outer_plan


def test_restart_rejects_backdating_same_session_and_direction_reversal() -> None:
    sequencer, _, _ = _runtime()
    sequencer.fail(
        PassiveCampaignFailureReason.INTERRUPTED,
        evaluated_monotonic_s=5.0,
    )
    same_session_plan, same_session_identity = _campaign(
        campaign_id="synthetic-new-campaign",
    )
    same_session_source = _Source(same_session_identity)
    same_session_detector = _Detector(same_session_identity)
    same_session_source.clock.value = 6.0
    with pytest.raises(ValueError, match="after"):
        sequencer.restart(
            same_session_plan,
            same_session_source,
            same_session_detector,
            same_session_source.clock,
            started_monotonic_s=5.0,
        )
    with pytest.raises(ValueError, match="never-used capture session"):
        sequencer.restart(
            same_session_plan,
            same_session_source,
            same_session_detector,
            same_session_source.clock,
            started_monotonic_s=6.0,
        )
    reversed_plan, reversed_identity = _campaign(
        RouteDirection.BANK_TO_MINE,
        campaign_id="synthetic-reversed-campaign",
        capture_session_id="synthetic-reversed-session",
    )
    reversed_source = _Source(reversed_identity)
    reversed_source.clock.value = 6.0
    with pytest.raises(ValueError, match="reverse direction"):
        sequencer.restart(
            reversed_plan,
            reversed_source,
            _Detector(reversed_identity),
            reversed_source.clock,
            started_monotonic_s=6.0,
        )


def test_sequencer_cannot_be_copied_deepcopied_or_pickled() -> None:
    sequencer, _, _ = _runtime()
    with pytest.raises(TypeError, match="cannot be copied"):
        copy.copy(sequencer)
    with pytest.raises(TypeError, match="cannot be copied"):
        copy.deepcopy(sequencer)
    with pytest.raises(TypeError, match="cannot be pickled"):
        pickle.dumps(sequencer)


def test_monotonic_inputs_require_exact_floats_without_rounding_or_subclasses() -> None:
    class _EvilFloat(float):
        def __sub__(self, other: object) -> float:
            del other
            return 0.0

        def __lt__(self, other: object) -> bool:
            del other
            return False

    plan, identity = _campaign()
    source = _Source(identity)
    detector = _Detector(identity)
    invalid_clock = _Clock(-0.0)
    with pytest.raises(ValueError, match="exact finite"):
        PassiveCampaignSequencer(
            plan,
            source,
            detector,
            invalid_clock,
            started_monotonic_s=0.0,
        )
    authority_clock = _Clock()
    authority_clock.input_authority = True  # type: ignore[assignment]
    with pytest.raises(ValueError, match="cannot carry"):
        PassiveCampaignSequencer(
            plan,
            source,
            detector,
            authority_clock,
            started_monotonic_s=0.0,
        )
    with pytest.raises(ValueError, match="exact finite"):
        PassiveCampaignSequencer(
            plan,
            source,
            detector,
            source.clock,
            started_monotonic_s=0,  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="exact finite"):
        PassiveCampaignSequencer(
            plan,
            source,
            detector,
            source.clock,
            started_monotonic_s=_EvilFloat(-1.0),
        )

    sequencer = PassiveCampaignSequencer(
        plan,
        source,
        detector,
        source.clock,
        started_monotonic_s=0.0,
    )
    original = sequencer.progress
    with pytest.raises(ValueError, match="exact finite"):
        sequencer.request_capture(
            request_id="synthetic-request",
            operator_id=plan.operator_id,
            acknowledged_monotonic_s=2**53 + 1,  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="exact finite"):
        sequencer.request_capture(
            request_id="synthetic-negative-zero-request",
            operator_id=plan.operator_id,
            acknowledged_monotonic_s=-0.0,
        )
    with pytest.raises(ValueError, match="fixed passive capture timeout"):
        sequencer.request_capture(
            request_id="synthetic-large-float-request",
            operator_id=plan.operator_id,
            acknowledged_monotonic_s=float(2**57),
        )
    assert sequencer.progress == original


def test_acquisition_transcript_is_hash_chained_into_reviewed_package() -> None:
    sequencer, source, _ = _complete()
    source.clock.value = 40.0
    finalization = sequencer.finalize(
        finalized_at_utc="2026-09-01T00:00:10Z",
    )
    package = finalization.package
    previous = package.campaign_plan.content_sha256
    for owned in package.cases:
        assert owned.acquisition.previous_acquisition_sha256 == previous
        assert owned.acquisition.capture_source_identity_sha256 == (
            package.campaign_plan.capture_source_identity_sha256
        )
        previous = owned.content_sha256
    assert previous == package.acquisition_head_sha256
    changed = replace(package.cases[0].acquisition, request_id="different-request")
    assert changed.content_sha256 != package.cases[0].acquisition.content_sha256


def test_independent_review_uses_separate_fixture_truth_and_disagreement_fails() -> None:
    sequencer, source, _ = _complete()
    source.clock.value = 40.0
    finalization = sequencer.finalize(
        finalized_at_utc="2026-09-01T00:00:10Z",
    )
    package = finalization.package
    plan = package.campaign_plan
    fixture_truth = tuple(
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
        for spec, owned in zip(plan.cases, package.cases, strict=True)
    )
    review = RouteEvidenceReview(
        package.content_sha256,
        plan.campaign_id,
        plan.route,
        plan.route_plan_sha256,
        "synthetic-independent-reviewer",
        "2026-09-01T00:00:11Z",
        fixture_truth,
    )
    expectation = RouteEvidenceLoadExpectation(
        package.content_sha256,
        package.acquisition_head_sha256,
        plan.campaign_id,
        plan.route,
        plan.route.direction,
        plan.route_plan_sha256,
        plan.detector,
        plan.profile,
        plan.capture_source_id,
        plan.capture_session_id,
        plan.capture_build,
        plan.frame_width,
        plan.frame_height,
        plan.pixel_format,
        plan.capture_configuration_sha256,
        plan.capture_environment_sha256,
        plan.support_envelope_sha256,
    )
    report = verify_synthetic_route_evidence(
        package,
        review,
        finalization.artifacts,
        expectation,
    )
    assert report.evidence_conformance_passed is True

    disagreeing_truth = replace(
        fixture_truth[0],
        detection=CheckpointDetection(CheckpointMatchKind.UNKNOWN, (), 0.0),
    )
    disagreement = replace(review, cases=(disagreeing_truth, *fixture_truth[1:]))
    failed = verify_synthetic_route_evidence(
        package,
        disagreement,
        finalization.artifacts,
        expectation,
    )
    assert failed.evidence_conformance_passed is False
    assert any("disagrees" in reason for reason in failed.failure_reasons)


def test_new_campaign_surfaces_are_not_root_exported_or_input_capable() -> None:
    assert integration_boundary.__all__ == ()
    assert "PassiveCampaignSequencer" not in navigation_root.__all__
    assert "PassiveMonotonicClock" not in navigation_root.__all__
    assert "OfflineRouteSessionSequencer" not in navigation_root.__all__
    assert not hasattr(navigation_root, "start_passive_campaign")
    forbidden = {"pyautogui", "pynput", "win32api", "win32con"}
    for module in (passive_module, offline_session_module, integration_boundary):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert _import_roots(ast.parse(source)).isdisjoint(forbidden)
    assert "pyautogui" in _import_roots(ast.parse("from pyautogui import click"))
