"""Source-owned production resource perception assembly.

This module deliberately exposes one narrow capture -> detect -> trust operation.
The caller supplies an open :class:`~mining_automation.capture.CaptureSource`,
but cannot supply detector output or a frame identity to bless. The exact frame
captured inside the operation is both the detector input and the current-frame
identity passed to the production trust boundary.

The result remains controller preparation only. This module does not create
WorldState, choose a target, or authorize/execute input.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..capture import CaptureSource, Frame
from ..contracts import Observation
from .detector import DetectorMetadata, run_detector
from .production_profiles import (
    VARROCK_EAST_IRON_DETECTOR_ID,
    VARROCK_EAST_IRON_DETECTOR_VERSION,
    ProductionResourceTrustResult,
    build_varrock_east_iron_detector,
    trust_varrock_east_iron_observations,
)

__all__ = [
    "ProductionResourceEvaluationResult",
    "capture_detect_trust_varrock_east_iron",
    "capture_evaluate_trust_varrock_east_iron",
    "evaluate_varrock_east_iron_frame",
]

_EXPECTED_METADATA = DetectorMetadata(
    detector_id=VARROCK_EAST_IRON_DETECTOR_ID,
    version=VARROCK_EAST_IRON_DETECTOR_VERSION,
)


@dataclass(frozen=True, slots=True)
class ProductionResourceEvaluationResult:
    """Exact owned frame, validated observations, and production trust result.

    This immutable carrier preserves the evidence used by the production
    boundary without adding a detector, identity, or policy injection seam.
    ``trust.accepted`` continues to mean that the complete ensemble contract
    passed; callers must use the resource states and interaction regions to
    distinguish a validated scene from a fail-closed uncertain scene.
    """

    frame: Frame
    observations: tuple[Observation, ...]
    trust: ProductionResourceTrustResult

    def __post_init__(self) -> None:
        if not isinstance(self.frame, Frame):
            raise TypeError("frame must be Frame")
        if not isinstance(self.observations, tuple) or any(
            not isinstance(observation, Observation)
            for observation in self.observations
        ):
            raise TypeError("observations must be a tuple of Observation values")
        if any(observation.frame != self.frame.ref for observation in self.observations):
            raise ValueError("every observation must reference the exact evaluated frame")
        if not isinstance(self.trust, ProductionResourceTrustResult):
            raise TypeError("trust must be ProductionResourceTrustResult")
        if self.trust.accepted and self.trust.frame != self.frame.ref:
            raise ValueError("accepted trust must reference the exact evaluated frame")


def evaluate_varrock_east_iron_frame(
    frame: Frame,
    /,
) -> ProductionResourceEvaluationResult:
    """Evaluate one owned frame under the fixed packaged production policy.

    The caller may retain ``frame`` before invoking this function, so a
    detector failure cannot erase successfully captured evidence. The caller
    cannot supply a detector, metadata, frame token, trust policy, profile, or
    location identity.
    """

    if not isinstance(frame, Frame):
        raise TypeError("frame must be Frame")

    detector = build_varrock_east_iron_detector()
    observations = run_detector(
        detector,
        frame,
        expected_metadata=_EXPECTED_METADATA,
    )
    trust = trust_varrock_east_iron_observations(
        observations,
        current_frame=frame.ref,
    )
    return ProductionResourceEvaluationResult(
        frame=frame,
        observations=observations,
        trust=trust,
    )


def capture_evaluate_trust_varrock_east_iron(
    source: CaptureSource,
    /,
) -> ProductionResourceEvaluationResult:
    """Capture once and retain the complete fixed-policy production evidence."""

    if not isinstance(source, CaptureSource):
        raise TypeError("source must be CaptureSource")

    return evaluate_varrock_east_iron_frame(source.capture())


def capture_detect_trust_varrock_east_iron(
    source: CaptureSource,
    /,
) -> ProductionResourceTrustResult:
    """Capture and evaluate one resource frame under source-owned identity.

    ``source`` must already be open. Capture and detector failures retain their
    existing typed exceptions and therefore never produce an accepted-looking
    result. A successfully detected frame is trusted only against its own exact
    immutable :class:`~mining_automation.contracts.FrameRef`.

    There are intentionally no detector, observation, frame, or policy
    parameters. This keeps arbitrary historical ensembles and caller-selected
    current-frame tokens outside the future live assembly boundary.
    """

    return capture_evaluate_trust_varrock_east_iron(source).trust
