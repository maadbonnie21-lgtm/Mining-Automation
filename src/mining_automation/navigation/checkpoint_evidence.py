"""Guarded, route-agnostic checkpoint detection over one owned frame."""

from __future__ import annotations

from typing import Protocol, cast, runtime_checkable

from ..capture import Frame
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
    def identity(self) -> CheckpointDetectorIdentity:
        ...

    @property
    def profile(self) -> CheckpointProfile:
        ...

    def detect(self, frame: Frame, /) -> CheckpointDetection:
        ...


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

    if not isinstance(expected_source, CheckpointSourceIdentity):
        raise CheckpointDetectorContractError(
            "expected checkpoint source must be CheckpointSourceIdentity"
        )
    _validate_frame(frame, expected_source)
    identity, profile = _read_detector_contract(detector)
    _require_expected_detector(identity, profile, expected_source)
    frame_digest = Sha256Digest.from_bytes(frame.payload)

    try:
        result = detector.detect(frame)
    except Exception as exc:
        raise CheckpointDetectorExecutionError(
            f"checkpoint detector {identity.detector_id!r}@{identity.version!r} "
            f"failed on frame {frame.frame_id}: {type(exc).__name__}: {exc}"
        ) from exc

    final_identity, final_profile = _read_detector_contract(detector)
    if final_identity != identity or final_profile != profile:
        raise CheckpointDetectorContractError(
            "checkpoint detector identity or profile changed during one-frame execution"
        )
    if not isinstance(result, CheckpointDetection):
        raise CheckpointDetectorContractError(
            "checkpoint detector must return exactly one CheckpointDetection, "
            f"got {type(result).__name__}"
        )
    try:
        return CheckpointEvidence(
            provenance=FrameProvenance(
                source=expected_source,
                frame=frame.ref,
                pixel_format=frame.pixel_format,
                frame_payload_sha256=frame_digest,
            ),
            detection=result,
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

    if not isinstance(context, RouteEvaluationContext):
        raise CheckpointDetectorContractError("binding context must be RouteEvaluationContext")
    if not isinstance(evidence, CheckpointEvidence):
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
    if not isinstance(identity, CheckpointDetectorIdentity):
        raise CheckpointDetectorContractError(
            "checkpoint detector identity must be CheckpointDetectorIdentity"
        )
    if not isinstance(profile, CheckpointProfile):
        raise CheckpointDetectorContractError(
            "checkpoint detector profile must be CheckpointProfile"
        )
    return identity, profile


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
    if not isinstance(frame, Frame):
        raise CheckpointDetectorContractError(
            f"checkpoint detector input must be Frame, got {type(frame).__name__}"
        )
    if not isinstance(frame.payload, bytes):
        raise CheckpointDetectorContractError("checkpoint frame payload must be immutable bytes")
    if frame.frame_id < 1:
        raise CheckpointDetectorContractError(
            "checkpoint detector requires a positive captured frame id"
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
