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

from ..capture import CaptureSource
from .detector import DetectorMetadata, run_detector
from .production_profiles import (
    VARROCK_EAST_IRON_DETECTOR_ID,
    VARROCK_EAST_IRON_DETECTOR_VERSION,
    ProductionResourceTrustResult,
    build_varrock_east_iron_detector,
    trust_varrock_east_iron_observations,
)

__all__ = ["capture_detect_trust_varrock_east_iron"]

_EXPECTED_METADATA = DetectorMetadata(
    detector_id=VARROCK_EAST_IRON_DETECTOR_ID,
    version=VARROCK_EAST_IRON_DETECTOR_VERSION,
)


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

    if not isinstance(source, CaptureSource):
        raise TypeError("source must be CaptureSource")

    frame = source.capture()
    detector = build_varrock_east_iron_detector()
    observations = run_detector(
        detector,
        frame,
        expected_metadata=_EXPECTED_METADATA,
    )
    return trust_varrock_east_iron_observations(
        observations,
        current_frame=frame.ref,
    )
