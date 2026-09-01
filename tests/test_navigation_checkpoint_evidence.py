from __future__ import annotations

import inspect
from dataclasses import replace
from pathlib import Path

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.navigation.checkpoint_evidence import (
    CheckpointDetectorContractError,
    CheckpointDetectorExecutionError,
    bind_checkpoint_evidence,
    run_checkpoint_detector,
)
from mining_automation.navigation.contracts import (
    Checkpoint,
    CheckpointDetection,
    CheckpointDetectorIdentity,
    CheckpointEvidenceRole,
    CheckpointMatchKind,
    CheckpointProfile,
    CheckpointRole,
    CheckpointSourceIdentity,
    NavigationFailureReason,
    NavigationPhase,
    NavigationPolicy,
    RouteDirection,
    RouteEndpoint,
    RouteEndpointRole,
    RouteEvaluationContext,
    RouteIdentity,
    RoutePlan,
    RouteStep,
    Sha256Digest,
)
from mining_automation.navigation.machine import observe_checkpoint, start_route


def _plan() -> RoutePlan:
    checkpoints = (
        Checkpoint("synthetic-departure", CheckpointRole.DEPARTURE),
        Checkpoint("synthetic-transit", CheckpointRole.TRANSIT),
        Checkpoint("synthetic-arrival", CheckpointRole.ARRIVAL),
    )
    return RoutePlan(
        identity=RouteIdentity("synthetic-evidence-route", "synthetic-v1", RouteDirection.MINE_TO_BANK),
        origin=RouteEndpoint("synthetic-mine", RouteEndpointRole.MINE),
        destination=RouteEndpoint("synthetic-bank", RouteEndpointRole.BANK),
        checkpoints=checkpoints,
        steps=(
            RouteStep("synthetic-step-1", checkpoints[0].checkpoint_id, checkpoints[1].checkpoint_id),
            RouteStep("synthetic-step-2", checkpoints[1].checkpoint_id, checkpoints[2].checkpoint_id),
        ),
    )


def _profile(
    *,
    profile_id: str = "synthetic-checkpoint-profile",
    version: str = "synthetic-v1",
    width: int = 2,
    height: int = 1,
    pixel_format: PixelFormat = PixelFormat.BGRA8888,
    checkpoint_ids: tuple[str, ...] = (
        "synthetic-departure",
        "synthetic-transit",
        "synthetic-arrival",
        "synthetic-foreign",
    ),
) -> CheckpointProfile:
    return CheckpointProfile(
        profile_id=profile_id,
        version=version,
        evidence_role=CheckpointEvidenceRole.SYNTHETIC_ARCHITECTURE_TEST_ONLY,
        frame_width=width,
        frame_height=height,
        pixel_format=pixel_format,
        checkpoint_ids=checkpoint_ids,
    )


def _source(
    *,
    detector: CheckpointDetectorIdentity | None = None,
    profile: CheckpointProfile | None = None,
    session_id: str = "synthetic-session",
) -> CheckpointSourceIdentity:
    return CheckpointSourceIdentity(
        detector=detector or CheckpointDetectorIdentity("synthetic-detector", "synthetic-v1"),
        profile=profile or _profile(),
        frame_source_id="synthetic-frame-source",
        capture_session_id=session_id,
    )


def _context(*, source: CheckpointSourceIdentity | None = None) -> RouteEvaluationContext:
    return RouteEvaluationContext(
        plan=_plan(),
        expected_source=source or _source(),
        policy=NavigationPolicy(max_frame_age_s=0.5, minimum_confidence=0.9),
    )


def _frame(
    *,
    frame_id: int = 1,
    captured_monotonic_s: float = 1.0,
    payload: bytes = b"abcdefgh",
    pixel_format: PixelFormat = PixelFormat.BGRA8888,
) -> Frame:
    return Frame.from_raw(
        RawFrame(payload=payload, width=2, height=1, pixel_format=pixel_format),
        frame_id=frame_id,
        captured_monotonic_s=captured_monotonic_s,
    )


class StaticCheckpointDetector:
    def __init__(
        self,
        result: object,
        *,
        identity: CheckpointDetectorIdentity | None = None,
        profile: CheckpointProfile | None = None,
        error: Exception | None = None,
    ) -> None:
        self.identity = identity or CheckpointDetectorIdentity(
            "synthetic-detector", "synthetic-v1"
        )
        self.profile = profile or _profile()
        self.result = result
        self.error = error

    def detect(self, frame: Frame, /) -> CheckpointDetection:
        if self.error is not None:
            raise self.error
        return self.result  # type: ignore[return-value]


def _matched(checkpoint_id: str = "synthetic-departure") -> CheckpointDetection:
    return CheckpointDetection(
        CheckpointMatchKind.MATCHED,
        (checkpoint_id,),
        0.99,
    )


def test_detector_evidence_binds_exact_owned_frame_then_advances_reducer() -> None:
    frame = _frame()
    context = _context()
    evidence = run_checkpoint_detector(
        StaticCheckpointDetector(_matched()),
        frame,
        expected_source=context.expected_source,
    )

    assert evidence.provenance.frame == frame.ref
    assert evidence.provenance.frame_payload_sha256 == Sha256Digest.from_bytes(frame.payload)
    assert evidence.provenance.pixel_format is frame.pixel_format
    observation = bind_checkpoint_evidence(context, evidence, current_frame=frame)
    assert observation.route == context.plan.identity
    assert observation.matched_checkpoint_id == "synthetic-departure"

    transition = observe_checkpoint(
        context,
        start_route(context, started_monotonic_s=0.9),
        observation,
        evaluated_monotonic_s=1.1,
    )
    assert transition.progress.phase is NavigationPhase.READY_FOR_STEP


@pytest.mark.parametrize(
    "detection,reason",
    [
        (
            CheckpointDetection(CheckpointMatchKind.UNKNOWN, (), 0.0),
            NavigationFailureReason.UNKNOWN_CHECKPOINT,
        ),
        (
            CheckpointDetection(
                CheckpointMatchKind.AMBIGUOUS,
                ("synthetic-departure", "synthetic-transit"),
                0.99,
            ),
            NavigationFailureReason.AMBIGUOUS_CHECKPOINT,
        ),
    ],
)
def test_unknown_and_ambiguous_are_valid_evidence_but_never_progress(
    detection: CheckpointDetection,
    reason: NavigationFailureReason,
) -> None:
    context = _context()
    frame = _frame()
    evidence = run_checkpoint_detector(
        StaticCheckpointDetector(detection),
        frame,
        expected_source=context.expected_source,
    )
    observation = bind_checkpoint_evidence(context, evidence, current_frame=frame)
    transition = observe_checkpoint(
        context,
        start_route(context, started_monotonic_s=0.9),
        observation,
        evaluated_monotonic_s=1.1,
    )
    assert transition.progress.phase is NavigationPhase.STOPPED
    assert transition.progress.failure_reason is reason


@pytest.mark.parametrize(
    "detector,source,message",
    [
        (
            StaticCheckpointDetector(
                _matched(),
                identity=CheckpointDetectorIdentity("other-detector", "synthetic-v1"),
            ),
            _source(),
            "identity",
        ),
        (
            StaticCheckpointDetector(_matched(), profile=_profile(profile_id="other-profile")),
            _source(),
            "profile",
        ),
        (
            StaticCheckpointDetector(_matched(), profile=_profile(version="synthetic-v2")),
            _source(),
            "profile",
        ),
    ],
)
def test_detector_and_profile_drift_are_rejected_before_evidence_exists(
    detector: StaticCheckpointDetector,
    source: CheckpointSourceIdentity,
    message: str,
) -> None:
    with pytest.raises(CheckpointDetectorContractError, match=message):
        run_checkpoint_detector(detector, _frame(), expected_source=source)


def test_profile_content_digest_changes_with_every_binding_field() -> None:
    original = _profile()
    mutations = (
        replace(original, frame_width=3),
        replace(original, pixel_format=PixelFormat.RGBA8888),
        replace(original, checkpoint_ids=original.checkpoint_ids[::-1]),
    )
    assert len({original.identity.content_sha256, *(item.identity.content_sha256 for item in mutations)}) == 4


def test_frame_geometry_format_and_payload_shape_are_checked_before_detection() -> None:
    detector = StaticCheckpointDetector(_matched())
    source = _source()
    with pytest.raises(CheckpointDetectorContractError, match="geometry"):
        run_checkpoint_detector(detector, replace(_frame(), ref=replace(_frame().ref, width=3)), expected_source=source)
    with pytest.raises(CheckpointDetectorContractError, match="pixel format"):
        run_checkpoint_detector(
            detector,
            replace(_frame(), pixel_format=PixelFormat.RGBA8888),
            expected_source=source,
        )
    with pytest.raises(CheckpointDetectorContractError, match="payload has"):
        run_checkpoint_detector(
            detector,
            replace(_frame(), payload=b"short"),
            expected_source=source,
        )


def test_detector_output_must_be_typed_and_inside_its_profile() -> None:
    source = _source()
    with pytest.raises(CheckpointDetectorContractError, match="exactly one"):
        run_checkpoint_detector(StaticCheckpointDetector(object()), _frame(), expected_source=source)
    with pytest.raises(CheckpointDetectorContractError, match="outside profile"):
        run_checkpoint_detector(
            StaticCheckpointDetector(_matched("not-in-profile")),
            _frame(),
            expected_source=source,
        )


def test_detector_exception_is_typed_and_preserves_cause() -> None:
    cause = RuntimeError("synthetic detector failure")
    with pytest.raises(CheckpointDetectorExecutionError) as caught:
        run_checkpoint_detector(
            StaticCheckpointDetector(_matched(), error=cause),
            _frame(),
            expected_source=_source(),
        )
    assert caught.value.__cause__ is cause


def test_detector_metadata_change_during_execution_is_rejected() -> None:
    detector = StaticCheckpointDetector(_matched())

    def mutate_identity(frame: Frame) -> CheckpointDetection:
        detector.identity = CheckpointDetectorIdentity("changed-detector", "synthetic-v2")
        return _matched()

    detector.detect = mutate_identity  # type: ignore[method-assign]
    with pytest.raises(CheckpointDetectorContractError, match="changed during"):
        run_checkpoint_detector(detector, _frame(), expected_source=_source())


def test_binder_rejects_same_ref_with_different_bytes_and_same_bytes_with_different_ref() -> None:
    context = _context()
    frame = _frame()
    evidence = run_checkpoint_detector(
        StaticCheckpointDetector(_matched()),
        frame,
        expected_source=context.expected_source,
    )
    changed_bytes = replace(frame, payload=b"abcdEfgh")
    changed_ref = replace(frame, ref=replace(frame.ref, frame_id=2))

    with pytest.raises(CheckpointDetectorContractError, match="payload digest"):
        bind_checkpoint_evidence(context, evidence, current_frame=changed_bytes)
    with pytest.raises(CheckpointDetectorContractError, match="FrameRef"):
        bind_checkpoint_evidence(context, evidence, current_frame=changed_ref)


def test_binder_rejects_foreign_source_and_profile_route_binding_without_mutating_progress() -> None:
    context = _context()
    progress = start_route(context, started_monotonic_s=0.9)
    frame = _frame()
    foreign_source = _source(session_id="foreign-session")
    foreign_evidence = run_checkpoint_detector(
        StaticCheckpointDetector(_matched()),
        frame,
        expected_source=foreign_source,
    )
    with pytest.raises(CheckpointDetectorContractError, match="source"):
        bind_checkpoint_evidence(context, foreign_evidence, current_frame=frame)
    assert progress == start_route(context, started_monotonic_s=0.9)

    incomplete_profile = _profile(
        checkpoint_ids=("synthetic-departure", "synthetic-arrival")
    )
    incomplete_source = _source(profile=incomplete_profile)
    incomplete_context = _context(source=incomplete_source)
    incomplete_evidence = run_checkpoint_detector(
        StaticCheckpointDetector(_matched(), profile=incomplete_profile),
        frame,
        expected_source=incomplete_source,
    )
    with pytest.raises(CheckpointDetectorContractError, match="route checkpoints"):
        bind_checkpoint_evidence(
            incomplete_context,
            incomplete_evidence,
            current_frame=frame,
        )


def test_checkpoint_evidence_api_has_no_caller_checkpoint_label_or_live_dependency() -> None:
    runner_parameters = inspect.signature(run_checkpoint_detector).parameters
    binder_parameters = inspect.signature(bind_checkpoint_evidence).parameters
    forbidden_parameters = {
        "checkpoint_id",
        "expected_checkpoint_id",
        "label",
        "route",
    }
    assert forbidden_parameters.isdisjoint(runner_parameters)
    assert forbidden_parameters.isdisjoint(binder_parameters)

    source_text = (
        Path(__file__).parents[1]
        / "src"
        / "mining_automation"
        / "navigation"
        / "checkpoint_evidence.py"
    ).read_text(encoding="utf-8")
    for forbidden in ("WorldState", "controller", "mouse", "keyboard", "win32", "RuneLite"):
        assert forbidden not in source_text
