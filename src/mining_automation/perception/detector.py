"""Platform-independent detector contract and guarded one-frame execution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..capture import Frame
from ..contracts import FrameRef, Observation
from .errors import DetectorContractError, DetectorExecutionError

__all__ = ["Detector", "DetectorMetadata", "run_detector", "validate_detector"]


@dataclass(frozen=True, slots=True)
class DetectorMetadata:
    """Stable identity and implementation version for a detector."""

    detector_id: str
    version: str

    def __post_init__(self) -> None:
        if not isinstance(self.detector_id, str) or not self.detector_id.strip():
            raise ValueError("detector_id must be a non-empty string")
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("detector version must be a non-empty string")


@runtime_checkable
class Detector(Protocol):
    """Deterministically produce typed observations for one owned frame."""

    @property
    def metadata(self) -> DetectorMetadata:
        """Return stable identity and version metadata."""
        ...

    def detect(self, frame: Frame) -> Sequence[Observation]:
        """Inspect ``frame`` and return observations in deterministic order."""
        ...


def validate_detector(detector: object) -> DetectorMetadata:
    """Validate runtime protocol shape and return trusted detector metadata.

    Runtime-checkable protocols establish that the required attributes exist;
    this function additionally verifies the metadata value. It deliberately
    does not invoke ``detect``.
    """
    if not isinstance(detector, Detector):
        raise DetectorContractError(
            f"detector must satisfy Detector protocol, got {type(detector).__name__}"
        )

    try:
        metadata = detector.metadata
    except Exception as exc:
        raise DetectorContractError("detector metadata could not be read") from exc

    if not isinstance(metadata, DetectorMetadata):
        raise DetectorContractError(
            "detector metadata must be DetectorMetadata, "
            f"got {type(metadata).__name__}"
        )
    return metadata


def run_detector(
    detector: Detector,
    frame: Frame,
    *,
    expected_metadata: DetectorMetadata | None = None,
) -> tuple[Observation, ...]:
    """Run one detector against one frame and validate every returned value.

    Detector exceptions are normalized to :class:`DetectorExecutionError` with
    their original exception preserved as ``__cause__``. Malformed output is a
    :class:`DetectorContractError`; neither failure can be confused with an
    empty, successful observation set. When ``expected_metadata`` is supplied,
    metadata drift within a larger evaluation run is also a contract failure.
    """
    metadata = validate_detector(detector)
    if expected_metadata is not None and metadata != expected_metadata:
        raise DetectorContractError(
            "detector metadata changed during the evaluation run: "
            f"expected {expected_metadata.detector_id!r}@{expected_metadata.version!r}, "
            f"got {metadata.detector_id!r}@{metadata.version!r}"
        )
    if not isinstance(frame, Frame):
        raise DetectorContractError(
            f"detector input must be Frame, got {type(frame).__name__}"
        )

    try:
        raw_observations = detector.detect(frame)
    except Exception as exc:
        raise DetectorExecutionError(
            _failure_prefix(metadata, frame)
            + f" raised {type(exc).__name__}: {exc}"
        ) from exc

    if not isinstance(raw_observations, Sequence):
        raise DetectorContractError(
            _failure_prefix(metadata, frame)
            + " must return a Sequence[Observation], "
            f"got {type(raw_observations).__name__}"
        )

    try:
        observations = tuple(raw_observations)
    except Exception as exc:
        raise DetectorExecutionError(
            _failure_prefix(metadata, frame)
            + f" failed while materializing output: {type(exc).__name__}: {exc}"
        ) from exc

    for index, observation in enumerate(observations):
        _validate_observation(metadata, frame, observation, index=index)
    return observations


def _validate_observation(
    metadata: DetectorMetadata,
    frame: Frame,
    observation: object,
    *,
    index: int,
) -> None:
    prefix = _failure_prefix(metadata, frame) + f" output[{index}]"
    if not isinstance(observation, Observation):
        raise DetectorContractError(
            f"{prefix} must be Observation, got {type(observation).__name__}"
        )
    if not isinstance(observation.kind, str) or not observation.kind.strip():
        raise DetectorContractError(f"{prefix} kind must be a non-empty string")
    if not isinstance(observation.frame, FrameRef):
        raise DetectorContractError(
            f"{prefix} frame must be FrameRef, got {type(observation.frame).__name__}"
        )
    if observation.frame != frame.ref:
        raise DetectorContractError(
            f"{prefix} references frame {observation.frame.frame_id}, "
            f"expected input frame {frame.frame_id}"
        )
    if not isinstance(observation.evidence, Mapping):
        raise DetectorContractError(
            f"{prefix} evidence must be a Mapping, got {type(observation.evidence).__name__}"
        )
    if observation.detector_version != metadata.version:
        raise DetectorContractError(
            f"{prefix} detector_version {observation.detector_version!r} "
            f"does not match metadata version {metadata.version!r}"
        )


def _failure_prefix(metadata: DetectorMetadata, frame: Frame) -> str:
    return (
        f"detector {metadata.detector_id!r} version {metadata.version!r} "
        f"on frame {frame.frame_id}"
    )
