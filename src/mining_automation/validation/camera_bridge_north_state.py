"""Exact frozen-north qualification for the Issue #31 R2.3 campaign.

Robust registration is intentionally broad enough to relate different camera
poses.  That makes ordinary accepted registration unsuitable for deciding
that a compass click is redundant.  R2.3 may omit that physical primitive
only when the fresh pixels are byte-identical to the frozen, receipt-proven
north frame and registration independently resolves to the identity
translation model.  This predicate is a campaign precondition only; it does
not validate a scene, expose resources, or grant input authority by itself.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

from ..perception.scene_landmarks import MacroZone
from .camera_bridge_planner import FROZEN_ENDPOINT_SOURCE_SHA256
from .robust_registration import ModelFamily, RobustWorldRegistration

__all__ = [
    "CAMERA_BRIDGE_EXACT_NORTH_QUALIFICATION_ID",
    "CAMERA_BRIDGE_EXACT_NORTH_QUALIFICATION_VERSION",
    "CameraBridgeExactNorthQualification",
    "CameraBridgeNorthStateError",
    "qualify_exact_frozen_north_registration",
]

CAMERA_BRIDGE_EXACT_NORTH_QUALIFICATION_ID: Final[str] = (
    "issue31-r2-3-exact-frozen-north-state"
)
CAMERA_BRIDGE_EXACT_NORTH_QUALIFICATION_VERSION: Final[str] = "1.0.0"

_MATRIX_TOLERANCE: Final[float] = 1.0e-9
_MINIMUM_OVERLAP_FRACTION: Final[float] = 0.99
_REQUIRED_ZONES: Final[tuple[MacroZone, ...]] = (
    MacroZone.NORTH_WEST,
    MacroZone.NORTH_EAST,
    MacroZone.SOUTH_WEST,
)
_IDENTITY_MATRIX: Final[tuple[tuple[float, float, float], ...]] = (
    (1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, 0.0, 1.0),
)


class CameraBridgeNorthStateError(ValueError):
    """Raised when fresh pixels do not prove the exact frozen north state."""


@dataclass(frozen=True, slots=True)
class CameraBridgeExactNorthQualification:
    """Canonical evidence that the zero-click precursor is exactly frozen north."""

    payload_sha256: str
    overlap_fraction: float

    def __post_init__(self) -> None:
        if self.payload_sha256 != FROZEN_ENDPOINT_SOURCE_SHA256:
            raise CameraBridgeNorthStateError(
                "north qualification must bind the frozen source digest"
            )
        if (
            not math.isfinite(self.overlap_fraction)
            or self.overlap_fraction < _MINIMUM_OVERLAP_FRACTION
            or self.overlap_fraction > 1.0
        ):
            raise CameraBridgeNorthStateError(
                "north qualification overlap is outside the exact-state policy"
            )

    def as_dict(self) -> dict[str, object]:
        return {
            "accepted": True,
            "exact_frozen_pixel_identity": True,
            "frozen_source_sha256": FROZEN_ENDPOINT_SOURCE_SHA256,
            "id": CAMERA_BRIDGE_EXACT_NORTH_QUALIFICATION_ID,
            "identity_matrix_tolerance": _MATRIX_TOLERANCE,
            "method": "exact_pixels_and_identity_translation_registration",
            "minimum_overlap_fraction": _MINIMUM_OVERLAP_FRACTION,
            "observed_overlap_fraction": round(self.overlap_fraction, 12),
            "payload_sha256": self.payload_sha256,
            "production_scene_authority": False,
            "registration_input_authority": False,
            "required_zones": [zone.value for zone in _REQUIRED_ZONES],
            "selected_model_family": ModelFamily.TRANSLATION.value,
            "version": CAMERA_BRIDGE_EXACT_NORTH_QUALIFICATION_VERSION,
        }


def _is_identity_matrix(
    matrix: tuple[tuple[float, float, float], ...] | None,
) -> bool:
    if matrix is None or len(matrix) != 3 or any(len(row) != 3 for row in matrix):
        return False
    return all(
        math.isfinite(float(observed))
        and abs(float(observed) - expected) <= _MATRIX_TOLERANCE
        for observed_row, expected_row in zip(matrix, _IDENTITY_MATRIX, strict=True)
        for observed, expected in zip(observed_row, expected_row, strict=True)
    )


def qualify_exact_frozen_north_registration(
    registration: RobustWorldRegistration,
) -> CameraBridgeExactNorthQualification:
    """Require exact frozen pixels plus an all-zone identity registration.

    The frozen digest is source-owned and cannot be supplied by a caller.  A
    visually different frame is rejected even when R1 can robustly register
    it, because such an edge may encode yaw, pitch, zoom, or translation.
    """

    model = registration.selected_model
    required_zones = frozenset(_REQUIRED_ZONES)
    if (
        not registration.accepted
        or registration.source.payload_sha256 != FROZEN_ENDPOINT_SOURCE_SHA256
        or registration.target.payload_sha256 != FROZEN_ENDPOINT_SOURCE_SHA256
        or registration.source != registration.target
        or model is None
        or model.family is not ModelFamily.TRANSLATION
        or not model.adequate
        or not required_zones.issubset(registration.required_zones)
        or registration.can_accept
        or registration.can_validate_scene
        or registration.can_expose_resources
        or registration.diagnostic_registration_can_override_production
    ):
        raise CameraBridgeNorthStateError(
            "zero-click requires exact frozen north pixels and no-authority "
            "identity registration"
        )
    source_inliers = dict(model.source_zone_inliers)
    target_inliers = dict(model.target_zone_inliers)
    source_cells = dict(model.source_zone_cells)
    target_cells = dict(model.target_zone_cells)
    if any(
        source_inliers.get(zone, 0) < registration.policy.minimum_inliers_per_zone
        or target_inliers.get(zone, 0)
        < registration.policy.minimum_inliers_per_zone
        or source_cells.get(zone, 0)
        < registration.policy.minimum_spatial_cells_per_zone
        or target_cells.get(zone, 0)
        < registration.policy.minimum_spatial_cells_per_zone
        for zone in _REQUIRED_ZONES
    ):
        raise CameraBridgeNorthStateError(
            "zero-click identity registration lacks distributed all-zone evidence"
        )
    distortion = model.distortion
    if (
        not _is_identity_matrix(model.forward_matrix)
        or not _is_identity_matrix(model.reverse_matrix)
        or distortion is None
        or not distortion.passed
        or not distortion.finite
        or not distortion.orientation_preserved
        or not math.isfinite(distortion.overlap_fraction)
        or distortion.overlap_fraction < _MINIMUM_OVERLAP_FRACTION
    ):
        raise CameraBridgeNorthStateError(
            "zero-click registration is not a high-overlap identity transform"
        )
    return CameraBridgeExactNorthQualification(
        payload_sha256=registration.target.payload_sha256,
        overlap_fraction=distortion.overlap_fraction,
    )
