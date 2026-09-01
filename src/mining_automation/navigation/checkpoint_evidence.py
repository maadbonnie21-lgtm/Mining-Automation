"""Guarded, route-agnostic checkpoint detection over one owned frame."""

from __future__ import annotations

import math
from typing import Protocol, cast, runtime_checkable

from ..capture import Frame
from ..contracts import FrameRef
from .contracts import (
    CheckpointDetection,
    CheckpointDetectorIdentity,
    CheckpointEvidence,
    CheckpointObservation,
    CheckpointProfile,
    CheckpointSourceIdentity,
    FrameProvenance,
    RouteEvaluationContext,
    Sha256Digest,
)

__all__ = [
    "CheckpointDetector",
    "CheckpointDetectorContractError",
    "CheckpointDetectorExecutionError",
    "CheckpointEvidenceError",
    "bind_checkpoint_evidence",
    "run_checkpoint_detector",
]


class CheckpointEvidenceError(RuntimeError):
    """Base error for the offline checkpoint evidence seam."""


class CheckpointDetectorContractError(CheckpointEvidenceError):
    """A detector, profile, frame, or result violates the typed seam."""


class CheckpointDetectorExecutionError(CheckpointEvidenceError):
    """A checkpoint detector failed while inspecting one frame."""


@runtime_checkable
class CheckpointDetector(Protocol):
    """Produce one route-free checkpoint classification for one owned frame."""

    @property
    def identity(self) -> CheckpointDetectorIdentity: ...

    @property
    def profile(self) -> CheckpointProfile: ...

    def detect(self, frame: Frame, /) -> CheckpointDetection: ...


def run_checkpoint_detector(
    detector: CheckpointDetector,
    frame: Frame,
    *,
    expected_source: CheckpointSourceIdentity,
) -> CheckpointEvidence:
    """Run and bind one detector result to the exact immutable input frame.

    The detector never receives a route, expected checkpoint, caller label, or
    navigation state. UNKNOWN and AMBIGUOUS are successful detector outputs;
    the navigation reducer decides that they cannot advance a route.
    """

    source = _snapshot_source_identity(expected_source)
    _validate_frame(frame, source)
    input_frame_ref = FrameRef(
        frame_id=frame.frame_id,
        captured_monotonic_s=frame.captured_monotonic_s,
        width=frame.width,
        height=frame.height,
    )
    input_payload = frame.payload
    input_pixel_format = frame.pixel_format
    identity, profile = _snapshot_detector_contract(*_read_detector_contract(detector))
    _require_expected_detector(identity, profile, source)
    frame_digest = Sha256Digest.from_bytes(input_payload)

    try:
        result = detector.detect(frame)
    except Exception as exc:
        raise CheckpointDetectorExecutionError(
            f"checkpoint detector {identity.detector_id!r}@{identity.version!r} "
            f"failed on frame {frame.frame_id}: {type(exc).__name__}: {exc}"
        ) from exc
    detection = _snapshot_detection(result)

    final_identity, final_profile = _snapshot_detector_contract(*_read_detector_contract(detector))
    final_source = _snapshot_source_identity(expected_source)
    if final_identity != identity or final_profile != profile:
        raise CheckpointDetectorContractError(
            "checkpoint detector identity or profile changed during one-frame execution"
        )
    if final_source != source:
        raise CheckpointDetectorContractError(
            "expected checkpoint source changed during one-frame execution"
        )
    _validate_frame(frame, source)
    if (
        frame.ref != input_frame_ref
        or frame.payload != input_payload
        or frame.pixel_format is not input_pixel_format
        or Sha256Digest.from_bytes(frame.payload) != frame_digest
    ):
        raise CheckpointDetectorContractError(
            "checkpoint frame identity, payload, or pixel format changed during detection"
        )
    if (
        result.match is not detection.match
        or result.candidate_checkpoint_ids != detection.candidate_checkpoint_ids
        or result.confidence != detection.confidence
    ):
        raise CheckpointDetectorContractError(
            "checkpoint detector result changed after its exact output was snapshotted"
        )
    try:
        return CheckpointEvidence(
            provenance=FrameProvenance(
                source=source,
                frame=input_frame_ref,
                pixel_format=input_pixel_format,
                frame_payload_sha256=frame_digest,
            ),
            detection=detection,
        )
    except ValueError as exc:
        raise CheckpointDetectorContractError(
            f"checkpoint detector returned evidence outside profile {profile.profile_id!r}: {exc}"
        ) from exc


def bind_checkpoint_evidence(
    context: RouteEvaluationContext,
    evidence: CheckpointEvidence,
    *,
    current_frame: Frame,
) -> CheckpointObservation:
    """Bind route-free detector evidence to a route after rechecking its frame.

    The route comes only from ``context``. No checkpoint label is accepted by
    this API, and the exact current frame bytes are rehashed before binding.
    """

    if type(context) is not RouteEvaluationContext:
        raise CheckpointDetectorContractError("binding context must be RouteEvaluationContext")
    if type(evidence) is not CheckpointEvidence:
        raise CheckpointDetectorContractError("binding evidence must be CheckpointEvidence")
    if evidence.provenance.source != context.expected_source:
        raise CheckpointDetectorContractError(
            "checkpoint evidence source does not match the route evaluation context"
        )
    expected_checkpoint_ids = tuple(
        checkpoint.checkpoint_id for checkpoint in context.plan.checkpoints
    )
    expected_checkpoint_set = set(expected_checkpoint_ids)
    profile_route_order = tuple(
        checkpoint_id
        for checkpoint_id in context.expected_source.profile.checkpoint_ids
        if checkpoint_id in expected_checkpoint_set
    )
    if profile_route_order != expected_checkpoint_ids:
        raise CheckpointDetectorContractError(
            "checkpoint profile does not contain the route checkpoints in exact order"
        )
    _validate_frame(current_frame, context.expected_source)
    if evidence.provenance.frame != current_frame.ref:
        raise CheckpointDetectorContractError(
            "checkpoint evidence FrameRef does not match the current owned frame"
        )
    if evidence.provenance.pixel_format is not current_frame.pixel_format:
        raise CheckpointDetectorContractError(
            "checkpoint evidence pixel format does not match the current owned frame"
        )
    current_digest = Sha256Digest.from_bytes(current_frame.payload)
    if evidence.provenance.frame_payload_sha256 != current_digest:
        raise CheckpointDetectorContractError(
            "checkpoint evidence payload digest does not match the current owned frame"
        )
    return CheckpointObservation(route=context.plan.identity, evidence=evidence)


def _read_detector_contract(
    detector: object,
) -> tuple[CheckpointDetectorIdentity, CheckpointProfile]:
    try:
        conforms = isinstance(detector, CheckpointDetector)
    except Exception as exc:
        raise CheckpointDetectorContractError(
            "checkpoint detector protocol could not be inspected"
        ) from exc
    if not conforms:
        raise CheckpointDetectorContractError(
            f"checkpoint detector must satisfy CheckpointDetector, got {type(detector).__name__}"
        )
    typed_detector = cast(CheckpointDetector, detector)
    try:
        identity = typed_detector.identity
        profile = typed_detector.profile
    except Exception as exc:
        raise CheckpointDetectorContractError(
            "checkpoint detector identity or profile could not be read"
        ) from exc
    if type(identity) is not CheckpointDetectorIdentity:
        raise CheckpointDetectorContractError(
            "checkpoint detector identity must be CheckpointDetectorIdentity"
        )
    if type(profile) is not CheckpointProfile:
        raise CheckpointDetectorContractError(
            "checkpoint detector profile must be CheckpointProfile"
        )
    return identity, profile


def _snapshot_detector_contract(
    identity: CheckpointDetectorIdentity,
    profile: CheckpointProfile,
) -> tuple[CheckpointDetectorIdentity, CheckpointProfile]:
    return (
        CheckpointDetectorIdentity(identity.detector_id, identity.version),
        CheckpointProfile(
            profile_id=profile.profile_id,
            version=profile.version,
            evidence_role=profile.evidence_role,
            frame_width=profile.frame_width,
            frame_height=profile.frame_height,
            pixel_format=profile.pixel_format,
            checkpoint_ids=tuple(profile.checkpoint_ids),
        ),
    )


def _snapshot_detection(result: object) -> CheckpointDetection:
    if type(result) is not CheckpointDetection:
        raise CheckpointDetectorContractError(
            "checkpoint detector must return exactly one CheckpointDetection, "
            f"got {type(result).__name__}"
        )
    typed_result = result
    try:
        detection = CheckpointDetection(
            match=typed_result.match,
            candidate_checkpoint_ids=tuple(typed_result.candidate_checkpoint_ids),
            confidence=typed_result.confidence,
        )
    except Exception as exc:
        raise CheckpointDetectorContractError(
            "checkpoint detector result could not be snapshotted"
        ) from exc
    if (
        typed_result.match is not detection.match
        or typed_result.candidate_checkpoint_ids != detection.candidate_checkpoint_ids
        or typed_result.confidence != detection.confidence
    ):
        raise CheckpointDetectorContractError(
            "checkpoint detector result changed while its exact output was snapshotted"
        )
    return detection


def _snapshot_source_identity(source: object) -> CheckpointSourceIdentity:
    if type(source) is not CheckpointSourceIdentity:
        raise CheckpointDetectorContractError(
            "expected checkpoint source must be exactly CheckpointSourceIdentity"
        )
    try:
        identity, profile = _snapshot_detector_contract(source.detector, source.profile)
        return CheckpointSourceIdentity(
            detector=identity,
            profile=profile,
            frame_source_id=source.frame_source_id,
            capture_session_id=source.capture_session_id,
        )
    except Exception as exc:
        raise CheckpointDetectorContractError(
            "expected checkpoint source could not be snapshotted"
        ) from exc


def _require_expected_detector(
    identity: CheckpointDetectorIdentity,
    profile: CheckpointProfile,
    expected_source: CheckpointSourceIdentity,
) -> None:
    if identity != expected_source.detector:
        raise CheckpointDetectorContractError(
            "checkpoint detector identity does not match the expected source"
        )
    if profile != expected_source.profile:
        raise CheckpointDetectorContractError(
            "checkpoint detector profile does not match the expected source"
        )


def _validate_frame(frame: object, source: CheckpointSourceIdentity) -> None:
    if type(frame) is not Frame:
        raise CheckpointDetectorContractError(
            f"checkpoint detector input must be Frame, got {type(frame).__name__}"
        )
    if type(frame.ref) is not FrameRef:
        raise CheckpointDetectorContractError("checkpoint frame ref must be exact FrameRef")
    if type(frame.payload) is not bytes:
        raise CheckpointDetectorContractError("checkpoint frame payload must be immutable bytes")
    if type(frame.frame_id) is not int or frame.frame_id < 1:
        raise CheckpointDetectorContractError(
            "checkpoint detector requires a positive captured frame id"
        )
    if type(frame.width) is not int or type(frame.height) is not int:
        raise CheckpointDetectorContractError("checkpoint frame geometry must use exact integers")
    captured = frame.captured_monotonic_s
    if (
        type(captured) is not float
        or not math.isfinite(captured)
        or captured < 0.0
        or (captured == 0.0 and math.copysign(1.0, captured) < 0.0)
    ):
        raise CheckpointDetectorContractError(
            "checkpoint frame time must be an exact finite non-negative float"
        )
    if (frame.width, frame.height) != (source.frame_width, source.frame_height):
        raise CheckpointDetectorContractError(
            "checkpoint frame geometry does not match the expected source profile"
        )
    if frame.pixel_format is not source.pixel_format:
        raise CheckpointDetectorContractError(
            "checkpoint frame pixel format does not match the expected source profile"
        )
    expected_size = frame.width * frame.height * frame.pixel_format.bytes_per_pixel
    if len(frame.payload) != expected_size:
        raise CheckpointDetectorContractError(
            f"checkpoint frame payload has {len(frame.payload)} bytes, expected {expected_size}"
        )
