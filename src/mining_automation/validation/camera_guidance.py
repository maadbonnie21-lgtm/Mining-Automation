"""World-only, refusal-oriented guidance for Issue #31 camera validation.

Guidance is deliberately separate from production perception.  It may select
one bounded camera axis/sign only when distributed world landmarks support one
coherent transform.  It can never accept a scene, expose a resource, or turn a
production rejection into success.

The first reviewed policy is intentionally narrow: it can recommend only a
zoom sign from a low-residual, scale-dominant similarity fit.  Yaw and pitch
remain ``INSUFFICIENT_GUIDANCE`` until their image-to-input calibration is
proved with real replay evidence.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from ..capture import Frame, PixelFormat
from ..perception.production_profiles import (
    load_varrock_east_iron_profile,
    varrock_east_iron_scene_excluded_regions,
)
from ..perception.resource import ResourceDetectorProfile
from ..perception.scene_landmarks import MacroZone, SceneLandmarkProfile
from ..perception.wide_scene_registration import (
    MAXIMUM_WIDE_REGISTRATION_RADIUS,
    WideLandmarkSearch,
    WideSceneRegistrationAnalysis,
    analyze_wide_scene_registration,
)
from .client_readiness import GAMEPLAY_CHROME_POLICIES

__all__ = [
    "CAMERA_GUIDANCE_ID",
    "CAMERA_GUIDANCE_VERSION",
    "CameraGuidanceAxis",
    "CameraGuidanceDirection",
    "CameraGuidanceDisposition",
    "CameraGuidanceReason",
    "CameraSimilarityFit",
    "WorldCameraGuidance",
    "evaluate_varrock_east_camera_guidance",
]

CAMERA_GUIDANCE_ID: Final[str] = "issue31-world-only-zoom-guidance"
CAMERA_GUIDANCE_VERSION: Final[str] = "1.0.0"
_REQUIRED_LANDMARKS: Final[int] = 5
_REQUIRED_ZONES: Final[int] = 3
_MAXIMUM_RMS_RESIDUAL_PX: Final[float] = 3.0
_MAXIMUM_POINT_RESIDUAL_PX: Final[float] = 5.0
_MINIMUM_LOG_SCALE_ERROR: Final[float] = 0.01
_SCALE_DOMINANCE_RATIO: Final[float] = 1.5
_ROTATION_SCORE_UNIT_DEGREES: Final[float] = 0.25
_TRANSLATION_SCORE_UNIT_PX: Final[float] = 4.0


class CameraGuidanceDisposition(StrEnum):
    """Whether diagnostics justify exactly one bounded axis/sign."""

    ACTIONABLE = "actionable"
    INSUFFICIENT_GUIDANCE = "insufficient_guidance"


class CameraGuidanceReason(StrEnum):
    """Stable refusal/action reason for immutable servo evidence."""

    ZOOM_SCALE_HIGH = "zoom_scale_high"
    ZOOM_SCALE_LOW = "zoom_scale_low"
    UNSUPPORTED_FRAME = "unsupported_frame"
    INSUFFICIENT_DISTRIBUTED_LANDMARKS = "insufficient_distributed_landmarks"
    INCOHERENT_TRANSFORM = "incoherent_transform"
    WITHIN_DEADBAND = "within_deadband"
    AMBIGUOUS_AXIS = "ambiguous_axis"
    UNCALIBRATED_AXIS = "uncalibrated_axis"


class CameraGuidanceAxis(StrEnum):
    """Validation camera axes; v1 authorizes only ``ZOOM``."""

    YAW = "yaw"
    PITCH = "pitch"
    ZOOM = "zoom"


class CameraGuidanceDirection(StrEnum):
    """Signed direction interpreted by the bounded primitive catalog."""

    NEGATIVE = "negative"
    POSITIVE = "positive"


@dataclass(frozen=True, slots=True)
class CameraSimilarityFit:
    """Distributed diagnostic fit from frozen to independently found centres."""

    scale: float
    rotation_degrees: float
    centre_shift_x: float
    centre_shift_y: float
    rms_residual_px: float
    maximum_residual_px: float
    landmark_count: int
    matched_zones: tuple[MacroZone, ...]

    def __post_init__(self) -> None:
        for name, value in (
            ("scale", self.scale),
            ("rotation_degrees", self.rotation_degrees),
            ("centre_shift_x", self.centre_shift_x),
            ("centre_shift_y", self.centre_shift_y),
            ("rms_residual_px", self.rms_residual_px),
            ("maximum_residual_px", self.maximum_residual_px),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(f"similarity {name} must be finite")
        if self.scale <= 0.0:
            raise ValueError("similarity scale must be positive")
        if self.rms_residual_px < 0.0 or self.maximum_residual_px < 0.0:
            raise ValueError("similarity residuals must be non-negative")
        if (
            isinstance(self.landmark_count, bool)
            or not isinstance(self.landmark_count, int)
            or self.landmark_count <= 0
        ):
            raise ValueError("similarity landmark_count must be positive")
        if not isinstance(self.matched_zones, tuple) or not self.matched_zones:
            raise ValueError("similarity matched_zones must be a non-empty tuple")
        if any(not isinstance(zone, MacroZone) for zone in self.matched_zones):
            raise ValueError("similarity matched_zones must contain only MacroZone values")
        if len(set(self.matched_zones)) != len(self.matched_zones):
            raise ValueError("similarity matched_zones must be unique")


@dataclass(frozen=True, slots=True)
class WorldCameraGuidance:
    """Diagnostic-only camera direction or an explicit safe refusal."""

    selector_id: str
    selector_version: str
    disposition: CameraGuidanceDisposition
    reason: CameraGuidanceReason
    detail: str
    axis: CameraGuidanceAxis | None
    direction: CameraGuidanceDirection | None
    fit: CameraSimilarityFit | None
    analysis: WideSceneRegistrationAnalysis | None
    excluded_regions: tuple[tuple[int, int, int, int], ...]
    can_accept: bool = field(default=False, init=False)
    can_validate_scene: bool = field(default=False, init=False)
    can_expose_resources: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.selector_id, str)
            or not self.selector_id
            or self.selector_id != self.selector_id.strip()
        ):
            raise ValueError("guidance selector_id must be a non-empty trimmed string")
        if (
            not isinstance(self.selector_version, str)
            or not self.selector_version
            or self.selector_version != self.selector_version.strip()
        ):
            raise ValueError("guidance selector_version must be a non-empty trimmed string")
        if not isinstance(self.disposition, CameraGuidanceDisposition):
            raise ValueError("guidance disposition is invalid")
        if not isinstance(self.reason, CameraGuidanceReason):
            raise ValueError("guidance reason is invalid")
        if not isinstance(self.detail, str) or not self.detail.strip():
            raise ValueError("guidance detail must be non-empty")
        if not isinstance(self.excluded_regions, tuple) or not self.excluded_regions:
            raise ValueError("guidance requires frozen candidate/UI exclusions")
        if any(
            not isinstance(region, tuple)
            or len(region) != 4
            or any(isinstance(value, bool) or not isinstance(value, int) for value in region)
            for region in self.excluded_regions
        ):
            raise ValueError("guidance excluded regions must contain four-integer tuples")
        actionable = self.disposition is CameraGuidanceDisposition.ACTIONABLE
        if actionable:
            if self.axis is None or self.direction is None:
                raise ValueError("actionable guidance must provide exactly one axis/sign")
            if self.axis is not CameraGuidanceAxis.ZOOM:
                raise ValueError("guidance v1 may authorize only the calibrated zoom axis")
            if self.fit is None or self.analysis is None:
                raise ValueError("actionable guidance requires distributed transform evidence")
            if (
                self.fit.landmark_count < _REQUIRED_LANDMARKS
                or len(self.fit.matched_zones) < _REQUIRED_ZONES
                or self.analysis.matched_count < _REQUIRED_LANDMARKS
                or len(self.analysis.matched_zones) < _REQUIRED_ZONES
            ):
                raise ValueError(
                    "actionable guidance requires the frozen distributed-evidence gate"
                )
            if (
                self.fit.landmark_count != self.analysis.matched_count
                or self.fit.matched_zones != self.analysis.matched_zones
            ):
                raise ValueError(
                    "actionable fit and distributed landmark evidence must agree"
                )
            if (
                self.fit.rms_residual_px > _MAXIMUM_RMS_RESIDUAL_PX
                or self.fit.maximum_residual_px > _MAXIMUM_POINT_RESIDUAL_PX
            ):
                raise ValueError("actionable guidance requires a coherent transform fit")
            scale_score = abs(math.log(self.fit.scale)) / _MINIMUM_LOG_SCALE_ERROR
            rotation_score = (
                abs(self.fit.rotation_degrees) / _ROTATION_SCORE_UNIT_DEGREES
            )
            translation_score = math.hypot(
                self.fit.centre_shift_x, self.fit.centre_shift_y
            ) / _TRANSLATION_SCORE_UNIT_PX
            competing_score = math.hypot(
                rotation_score,
                translation_score,
            )
            if (
                scale_score < 1.0
                or scale_score < competing_score * _SCALE_DOMINANCE_RATIO
            ):
                raise ValueError(
                    "actionable guidance requires one scale-dominant transform"
                )
            expected = (
                (CameraGuidanceReason.ZOOM_SCALE_HIGH, CameraGuidanceDirection.NEGATIVE)
                if self.fit.scale > 1.0
                else (CameraGuidanceReason.ZOOM_SCALE_LOW, CameraGuidanceDirection.POSITIVE)
            )
            if (self.reason, self.direction) != expected:
                raise ValueError("actionable zoom reason/sign must agree with fitted scale")
        elif self.axis is not None or self.direction is not None:
            raise ValueError("insufficient guidance cannot provide an axis or sign")


def evaluate_varrock_east_camera_guidance(frame: Frame) -> WorldCameraGuidance:
    """Select one safe world-only correction sign or refuse.

    The profile-bound wrapper always supplies every reviewed resource-candidate
    and fixed-UI exclusion.  Callers cannot omit those exclusions.
    """

    profile = load_varrock_east_iron_profile()
    excluded = tuple(
        dict.fromkeys(
            (
                *varrock_east_iron_scene_excluded_regions(profile),
                *(policy.region for policy in GAMEPLAY_CHROME_POLICIES),
            )
        )
    )
    if (
        frame.width != profile.frame_width
        or frame.height != profile.frame_height
        or frame.pixel_format is not PixelFormat.BGRA8888
    ):
        return _refusal(
            CameraGuidanceReason.UNSUPPORTED_FRAME,
            "Guidance requires the exact packaged BGRA8888 profile geometry.",
            excluded_regions=excluded,
        )
    analysis = analyze_wide_scene_registration(
        frame,
        profile.scene_landmarks,
        required_quorum=profile.minimum_landmark_quorum,
        required_zones=profile.minimum_landmark_zones,
        frame_width=profile.frame_width,
        frame_height=profile.frame_height,
        search_radius=MAXIMUM_WIDE_REGISTRATION_RADIUS,
        excluded_regions=excluded,
    )
    return _guidance_from_analysis(analysis, profile, excluded_regions=excluded)


def _guidance_from_analysis(
    analysis: WideSceneRegistrationAnalysis,
    profile: ResourceDetectorProfile,
    *,
    excluded_regions: tuple[tuple[int, int, int, int], ...],
) -> WorldCameraGuidance:
    matched = tuple(item for item in analysis.landmarks if item.matched)
    matched_zones = tuple(
        zone for zone in MacroZone if any(item.zone is zone for item in matched)
    )
    if len(matched) < _REQUIRED_LANDMARKS or len(matched_zones) < _REQUIRED_ZONES:
        return _refusal(
            CameraGuidanceReason.INSUFFICIENT_DISTRIBUTED_LANDMARKS,
            (
                f"Only {len(matched)}/{len(profile.scene_landmarks)} world landmarks "
                f"across {len(matched_zones)} zones are independently recoverable; "
                "no camera direction is authorized."
            ),
            analysis=analysis,
            excluded_regions=excluded_regions,
        )

    fit = _fit_similarity(
        matched,
        profile.scene_landmarks,
        frame_width=profile.frame_width,
        frame_height=profile.frame_height,
    )
    if (
        fit.rms_residual_px > _MAXIMUM_RMS_RESIDUAL_PX
        or fit.maximum_residual_px > _MAXIMUM_POINT_RESIDUAL_PX
    ):
        return _refusal(
            CameraGuidanceReason.INCOHERENT_TRANSFORM,
            (
                "Distributed landmark minima do not support a low-residual "
                f"similarity transform (rms={fit.rms_residual_px:.3f}px, "
                f"max={fit.maximum_residual_px:.3f}px)."
            ),
            fit=fit,
            analysis=analysis,
            excluded_regions=excluded_regions,
        )

    log_scale_error = math.log(fit.scale)
    scale_score = abs(log_scale_error) / _MINIMUM_LOG_SCALE_ERROR
    rotation_score = abs(fit.rotation_degrees) / _ROTATION_SCORE_UNIT_DEGREES
    translation_score = math.hypot(
        fit.centre_shift_x, fit.centre_shift_y
    ) / _TRANSLATION_SCORE_UNIT_PX
    competing_score = math.hypot(rotation_score, translation_score)

    if scale_score < 1.0 and competing_score < 1.0:
        return _refusal(
            CameraGuidanceReason.WITHIN_DEADBAND,
            "The coherent world transform is inside every reviewed motion deadband.",
            fit=fit,
            analysis=analysis,
            excluded_regions=excluded_regions,
        )
    if scale_score < competing_score * _SCALE_DOMINANCE_RATIO:
        reason = (
            CameraGuidanceReason.UNCALIBRATED_AXIS
            if competing_score > scale_score * _SCALE_DOMINANCE_RATIO
            else CameraGuidanceReason.AMBIGUOUS_AXIS
        )
        return _refusal(
            reason,
            (
                "The world transform does not isolate the calibrated zoom axis "
                f"(scale={scale_score:.3f}, rotation={rotation_score:.3f}, "
                f"translation={translation_score:.3f})."
            ),
            fit=fit,
            analysis=analysis,
            excluded_regions=excluded_regions,
        )

    if log_scale_error > 0.0:
        direction = CameraGuidanceDirection.NEGATIVE
        reason = CameraGuidanceReason.ZOOM_SCALE_HIGH
        detail = "Observed world scale is high; one bounded negative zoom pulse is permitted."
    else:
        direction = CameraGuidanceDirection.POSITIVE
        reason = CameraGuidanceReason.ZOOM_SCALE_LOW
        detail = "Observed world scale is low; one bounded positive zoom pulse is permitted."
    return WorldCameraGuidance(
        selector_id=CAMERA_GUIDANCE_ID,
        selector_version=CAMERA_GUIDANCE_VERSION,
        disposition=CameraGuidanceDisposition.ACTIONABLE,
        reason=reason,
        detail=detail,
        axis=CameraGuidanceAxis.ZOOM,
        direction=direction,
        fit=fit,
        analysis=analysis,
        excluded_regions=excluded_regions,
    )


def _fit_similarity(
    searches: tuple[WideLandmarkSearch, ...],
    landmarks: tuple[SceneLandmarkProfile, ...],
    *,
    frame_width: int,
    frame_height: int,
) -> CameraSimilarityFit:
    landmarks_by_id = {landmark.landmark_id: landmark for landmark in landmarks}
    points: list[tuple[float, float, float, float, MacroZone]] = []
    for search in searches:
        landmark = landmarks_by_id[search.landmark_id]
        x, y, width, height = landmark.region
        reference_x = x + width / 2.0
        reference_y = y + height / 2.0
        points.append(
            (
                reference_x,
                reference_y,
                reference_x + search.offset_x,
                reference_y + search.offset_y,
                search.zone,
            )
        )
    count = len(points)
    reference_mean_x = sum(item[0] for item in points) / count
    reference_mean_y = sum(item[1] for item in points) / count
    observed_mean_x = sum(item[2] for item in points) / count
    observed_mean_y = sum(item[3] for item in points) / count
    denominator = sum(
        (item[0] - reference_mean_x) ** 2 + (item[1] - reference_mean_y) ** 2
        for item in points
    )
    if denominator <= 0.0:
        raise ValueError("distributed landmark centres require nonzero extent")
    coefficient_a = sum(
        (item[0] - reference_mean_x) * (item[2] - observed_mean_x)
        + (item[1] - reference_mean_y) * (item[3] - observed_mean_y)
        for item in points
    ) / denominator
    coefficient_b = sum(
        (item[0] - reference_mean_x) * (item[3] - observed_mean_y)
        - (item[1] - reference_mean_y) * (item[2] - observed_mean_x)
        for item in points
    ) / denominator
    translation_x = (
        observed_mean_x
        - coefficient_a * reference_mean_x
        + coefficient_b * reference_mean_y
    )
    translation_y = (
        observed_mean_y
        - coefficient_b * reference_mean_x
        - coefficient_a * reference_mean_y
    )
    residuals = tuple(
        math.hypot(
            coefficient_a * reference_x
            - coefficient_b * reference_y
            + translation_x
            - observed_x,
            coefficient_b * reference_x
            + coefficient_a * reference_y
            + translation_y
            - observed_y,
        )
        for reference_x, reference_y, observed_x, observed_y, _ in points
    )
    centre_x = frame_width / 2.0
    centre_y = frame_height / 2.0
    transformed_centre_x = (
        coefficient_a * centre_x - coefficient_b * centre_y + translation_x
    )
    transformed_centre_y = (
        coefficient_b * centre_x + coefficient_a * centre_y + translation_y
    )
    return CameraSimilarityFit(
        scale=math.hypot(coefficient_a, coefficient_b),
        rotation_degrees=math.degrees(math.atan2(coefficient_b, coefficient_a)),
        centre_shift_x=transformed_centre_x - centre_x,
        centre_shift_y=transformed_centre_y - centre_y,
        rms_residual_px=math.sqrt(sum(value * value for value in residuals) / count),
        maximum_residual_px=max(residuals),
        landmark_count=count,
        matched_zones=tuple(
            zone for zone in MacroZone if any(item[4] is zone for item in points)
        ),
    )


def _refusal(
    reason: CameraGuidanceReason,
    detail: str,
    *,
    excluded_regions: tuple[tuple[int, int, int, int], ...],
    fit: CameraSimilarityFit | None = None,
    analysis: WideSceneRegistrationAnalysis | None = None,
) -> WorldCameraGuidance:
    return WorldCameraGuidance(
        selector_id=CAMERA_GUIDANCE_ID,
        selector_version=CAMERA_GUIDANCE_VERSION,
        disposition=CameraGuidanceDisposition.INSUFFICIENT_GUIDANCE,
        reason=reason,
        detail=detail,
        axis=None,
        direction=None,
        fit=fit,
        analysis=analysis,
        excluded_regions=excluded_regions,
    )
