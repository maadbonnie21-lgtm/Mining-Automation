"""Read-only robust world registration for Issue #31 validation.

This module deliberately lives outside production perception.  Its results can
explain geometric relationships between saved frames, but they can never
validate a scene, expose a resource, or override the packaged detector.

NumPy and headless OpenCV are optional validation dependencies.  Production
package imports do not load this module.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final, cast

import cv2
import numpy as np
import numpy.typing as npt

from ..capture import Frame, PixelFormat
from ..perception import (
    ResourceDetectorProfile,
    load_varrock_east_iron_profile,
    varrock_east_iron_scene_excluded_regions,
)
from ..perception.scene_landmarks import MacroZone
from .client_readiness import GAMEPLAY_CHROME_POLICIES

__all__ = [
    "ROBUST_WORLD_REGISTRATION_ID",
    "ROBUST_WORLD_REGISTRATION_VERSION",
    "DEFAULT_ROBUST_REGISTRATION_POLICY",
    "CorrespondenceEvidence",
    "DistortionEvidence",
    "EndpointEvidence",
    "ModelEvidence",
    "ModelFamily",
    "RegistrationDisposition",
    "RegistrationPolicy",
    "RobustRegistrationEngine",
    "RobustWorldRegistration",
    "analyze_robust_world_registration",
    "robust_registration_algorithm_settings",
    "robust_registration_environment",
    "trusted_robust_registration_exclusions",
]

ROBUST_WORLD_REGISTRATION_ID: Final[str] = "issue31-robust-world-registration-r1"
ROBUST_WORLD_REGISTRATION_VERSION: Final[str] = "1.0.0"

_OPENCV_RNG_SEED: Final[int] = 0x0310_2026
_FEATURE_LIMIT: Final[int] = 6000
_SIFT_CONTRAST_THRESHOLD: Final[float] = 0.02
_SIFT_EDGE_THRESHOLD: Final[float] = 12.0
_SIFT_SIGMA: Final[float] = 1.6
_FEATURE_SUPPORT_MARGIN: Final[int] = 24
_FEATURE_SUPPORT_SIZE_MULTIPLIER: Final[float] = 6.0
_MATCHES_PER_SPATIAL_CELL: Final[int] = 24
_SPATIAL_CELL_SIZE: Final[int] = 64
_CV_LOCK: Final[threading.Lock] = threading.Lock()
_REQUIRED_ZONES: Final[tuple[MacroZone, ...]] = (
    MacroZone.NORTH_WEST,
    MacroZone.NORTH_EAST,
    MacroZone.SOUTH_WEST,
)

type FloatArray = npt.NDArray[np.float64]
type ByteArray = npt.NDArray[np.uint8]
type Matrix3 = tuple[
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
]


def _is_finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


class ModelFamily(StrEnum):
    """Increasing-complexity transform families evaluated by R1."""

    TRANSLATION = "translation"
    SIMILARITY = "similarity"
    AFFINE = "affine"
    HOMOGRAPHY = "homography"


_MODEL_ORDER: Final[tuple[ModelFamily, ...]] = (
    ModelFamily.TRANSLATION,
    ModelFamily.SIMILARITY,
    ModelFamily.AFFINE,
    ModelFamily.HOMOGRAPHY,
)


class RegistrationDisposition(StrEnum):
    """Why an edge was accepted or rejected."""

    ACCEPTED = "accepted"
    UNSUPPORTED_FRAME = "unsupported_frame"
    INSUFFICIENT_FEATURES = "insufficient_features"
    INSUFFICIENT_MUTUAL_MATCHES = "insufficient_mutual_matches"
    INSUFFICIENT_DISTRIBUTION = "insufficient_distribution"
    GLOBAL_MODEL_INADEQUATE = "global_model_inadequate"


@dataclass(frozen=True, slots=True)
class RegistrationPolicy:
    """Frozen R1 matching, fitting, and edge-acceptance policy."""

    ratio_threshold: float = 0.72
    reprojection_inlier_threshold_px: float = 1.5
    minimum_mutual_matches: int = 50
    minimum_inliers: int = 50
    minimum_inliers_per_zone: int = 10
    minimum_spatial_cells_per_zone: int = 4
    minimum_inlier_ratio: float = 0.65
    maximum_median_residual_px: float = 0.75
    maximum_p90_residual_px: float = 1.0
    maximum_homography_p90_residual_px: float = 1.25
    maximum_cycle_median_px: float = 0.50
    maximum_cycle_p90_px: float = 1.0
    minimum_overlap_fraction: float = 0.25
    minimum_local_scale: float = 0.75
    maximum_local_scale: float = 1.35
    maximum_local_scale_ratio: float = 1.10
    maximum_local_condition: float = 1.25
    maximum_perspective_span: float = 0.05

    def __post_init__(self) -> None:
        for name in (
            "ratio_threshold",
            "minimum_inlier_ratio",
            "minimum_overlap_fraction",
        ):
            value = getattr(self, name)
            if not _is_finite_number(value) or not 0.0 < float(value) <= 1.0:
                raise ValueError(f"{name} must be finite and in (0, 1]")
        for name in (
            "reprojection_inlier_threshold_px",
            "maximum_median_residual_px",
            "maximum_p90_residual_px",
            "maximum_homography_p90_residual_px",
            "maximum_cycle_median_px",
            "maximum_cycle_p90_px",
            "minimum_local_scale",
            "maximum_local_scale",
            "maximum_local_scale_ratio",
            "maximum_local_condition",
            "maximum_perspective_span",
        ):
            value = getattr(self, name)
            if not _is_finite_number(value) or float(value) <= 0.0:
                raise ValueError(f"{name} must be finite and positive")
        for name in (
            "minimum_mutual_matches",
            "minimum_inliers",
            "minimum_inliers_per_zone",
            "minimum_spatial_cells_per_zone",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.minimum_inliers > self.minimum_mutual_matches:
            raise ValueError("minimum_inliers cannot exceed minimum_mutual_matches")
        if self.minimum_local_scale >= self.maximum_local_scale:
            raise ValueError("minimum_local_scale must be below maximum_local_scale")

    def as_dict(self) -> dict[str, object]:
        """Return canonical report evidence."""

        return {
            "maximum_cycle_median_px": self.maximum_cycle_median_px,
            "maximum_cycle_p90_px": self.maximum_cycle_p90_px,
            "maximum_homography_p90_residual_px": (
                self.maximum_homography_p90_residual_px
            ),
            "maximum_local_condition": self.maximum_local_condition,
            "maximum_local_scale": self.maximum_local_scale,
            "maximum_local_scale_ratio": self.maximum_local_scale_ratio,
            "maximum_median_residual_px": self.maximum_median_residual_px,
            "maximum_p90_residual_px": self.maximum_p90_residual_px,
            "maximum_perspective_span": self.maximum_perspective_span,
            "minimum_inlier_ratio": self.minimum_inlier_ratio,
            "minimum_inliers": self.minimum_inliers,
            "minimum_inliers_per_zone": self.minimum_inliers_per_zone,
            "minimum_local_scale": self.minimum_local_scale,
            "minimum_mutual_matches": self.minimum_mutual_matches,
            "minimum_overlap_fraction": self.minimum_overlap_fraction,
            "minimum_spatial_cells_per_zone": self.minimum_spatial_cells_per_zone,
            "ratio_threshold": self.ratio_threshold,
            "reprojection_inlier_threshold_px": (
                self.reprojection_inlier_threshold_px
            ),
        }


DEFAULT_ROBUST_REGISTRATION_POLICY: Final[RegistrationPolicy] = RegistrationPolicy()


@dataclass(frozen=True, slots=True)
class CorrespondenceEvidence:
    """Deterministic feature and bidirectional matching counts."""

    source_features: int
    target_features: int
    total_forward_matches: int
    total_reverse_matches: int
    forward_ratio_matches: int
    reverse_ratio_matches: int
    mutual_matches: int
    balanced_matches: int
    per_zone_mutual_matches: tuple[tuple[MacroZone, int], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "balanced_matches": self.balanced_matches,
            "forward_ratio_matches": self.forward_ratio_matches,
            "mutual_matches": self.mutual_matches,
            "per_zone_mutual_matches": {
                zone.value: count for zone, count in self.per_zone_mutual_matches
            },
            "reverse_ratio_matches": self.reverse_ratio_matches,
            "source_features": self.source_features,
            "target_features": self.target_features,
            "total_forward_matches": self.total_forward_matches,
            "total_reverse_matches": self.total_reverse_matches,
        }


@dataclass(frozen=True, slots=True)
class EndpointEvidence:
    """Exact immutable frame identity bound to a registration endpoint."""

    payload_sha256: str
    payload_bytes: int
    width: int
    height: int
    pixel_format: str

    def __post_init__(self) -> None:
        if len(self.payload_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.payload_sha256
        ):
            raise ValueError("payload_sha256 must be a lowercase SHA-256 digest")
        for name, value in (
            ("payload_bytes", self.payload_bytes),
            ("width", self.width),
            ("height", self.height),
        ):
            minimum = 0 if name == "payload_bytes" else 1
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                requirement = "non-negative" if minimum == 0 else "positive"
                raise ValueError(f"{name} must be a {requirement} integer")
        if not self.pixel_format:
            raise ValueError("pixel_format must be non-empty")

    def as_dict(self) -> dict[str, object]:
        return {
            "height": self.height,
            "payload_bytes": self.payload_bytes,
            "payload_sha256": self.payload_sha256,
            "pixel_format": self.pixel_format,
            "width": self.width,
        }


@dataclass(frozen=True, slots=True)
class DistortionEvidence:
    """Conditioning and footprint checks for a transform and its inverse."""

    finite: bool
    orientation_preserved: bool
    minimum_local_scale: float
    maximum_local_scale: float
    maximum_local_scale_ratio: float
    maximum_local_condition: float
    overlap_fraction: float
    perspective_span: float
    passed: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "finite": self.finite,
            "maximum_local_condition": _rounded_finite_or_none(
                self.maximum_local_condition
            ),
            "maximum_local_scale": _rounded_finite_or_none(self.maximum_local_scale),
            "maximum_local_scale_ratio": _rounded_finite_or_none(
                self.maximum_local_scale_ratio
            ),
            "minimum_local_scale": _rounded_finite_or_none(self.minimum_local_scale),
            "orientation_preserved": self.orientation_preserved,
            "overlap_fraction": _rounded_finite_or_none(self.overlap_fraction),
            "passed": self.passed,
            "perspective_span": _rounded_finite_or_none(self.perspective_span),
        }


@dataclass(frozen=True, slots=True)
class ModelEvidence:
    """Forward/reverse evidence for one candidate model family."""

    family: ModelFamily
    forward_matrix: Matrix3 | None
    reverse_matrix: Matrix3 | None
    inliers: int
    inlier_ratio: float
    source_zone_inliers: tuple[tuple[MacroZone, int], ...]
    target_zone_inliers: tuple[tuple[MacroZone, int], ...]
    source_zone_cells: tuple[tuple[MacroZone, int], ...]
    target_zone_cells: tuple[tuple[MacroZone, int], ...]
    median_residual_px: float | None
    p90_residual_px: float | None
    cycle_median_px: float | None
    cycle_p90_px: float | None
    distortion: DistortionEvidence | None
    adequate: bool
    rejection_reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "adequate": self.adequate,
            "cycle_median_px": _rounded_optional(self.cycle_median_px),
            "cycle_p90_px": _rounded_optional(self.cycle_p90_px),
            "distortion": None if self.distortion is None else self.distortion.as_dict(),
            "family": self.family.value,
            "forward_matrix": _matrix_dict(self.forward_matrix),
            "inlier_ratio": _rounded(self.inlier_ratio),
            "inlier_ratio_denominator": "correspondence.balanced_matches",
            "inliers": self.inliers,
            "median_residual_px": _rounded_optional(self.median_residual_px),
            "p90_residual_px": _rounded_optional(self.p90_residual_px),
            "rejection_reasons": list(self.rejection_reasons),
            "reverse_matrix": _matrix_dict(self.reverse_matrix),
            "source_zone_cells": {
                zone.value: count for zone, count in self.source_zone_cells
            },
            "source_zone_inliers": {
                zone.value: count for zone, count in self.source_zone_inliers
            },
            "target_zone_cells": {
                zone.value: count for zone, count in self.target_zone_cells
            },
            "target_zone_inliers": {
                zone.value: count for zone, count in self.target_zone_inliers
            },
        }


@dataclass(frozen=True, slots=True)
class RobustWorldRegistration:
    """Diagnostic-only edge result; never a production scene verdict."""

    registration_id: str
    registration_version: str
    source: EndpointEvidence
    target: EndpointEvidence
    profile_id: str
    profile_fingerprint_sha256: str
    exclusion_fingerprint_sha256: str
    algorithm_fingerprint_sha256: str
    policy_fingerprint_sha256: str
    disposition: RegistrationDisposition
    detail: str
    correspondence: CorrespondenceEvidence
    required_zones: tuple[MacroZone, ...]
    excluded_regions: tuple[tuple[int, int, int, int], ...]
    models: tuple[ModelEvidence, ...]
    selected_family: ModelFamily | None
    policy: RegistrationPolicy
    can_accept: bool = field(default=False, init=False)
    can_validate_scene: bool = field(default=False, init=False)
    can_expose_resources: bool = field(default=False, init=False)
    diagnostic_registration_can_override_production: bool = field(
        default=False, init=False
    )

    def __post_init__(self) -> None:
        if self.accepted is not (self.selected_family is not None):
            raise ValueError("accepted registration must select exactly one model")
        if self.selected_family is not None:
            selected = self.selected_model
            if selected is None or not selected.adequate:
                raise ValueError("selected registration model must be adequate")
        for name, digest in (
            ("profile_fingerprint_sha256", self.profile_fingerprint_sha256),
            ("exclusion_fingerprint_sha256", self.exclusion_fingerprint_sha256),
            ("algorithm_fingerprint_sha256", self.algorithm_fingerprint_sha256),
            ("policy_fingerprint_sha256", self.policy_fingerprint_sha256),
        ):
            if len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest
            ):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")

    @property
    def accepted(self) -> bool:
        return self.disposition is RegistrationDisposition.ACCEPTED

    @property
    def selected_model(self) -> ModelEvidence | None:
        if self.selected_family is None:
            return None
        return next(model for model in self.models if model.family is self.selected_family)

    def as_dict(self) -> dict[str, object]:
        return {
            "accepted": self.accepted,
            "authority": {
                "can_accept": self.can_accept,
                "can_expose_resources": self.can_expose_resources,
                "can_validate_scene": self.can_validate_scene,
                "diagnostic_registration_can_override_production": (
                    self.diagnostic_registration_can_override_production
                ),
            },
            "correspondence": self.correspondence.as_dict(),
            "detail": self.detail,
            "disposition": self.disposition.value,
            "excluded_regions": [list(region) for region in self.excluded_regions],
            "models": [model.as_dict() for model in self.models],
            "policy": self.policy.as_dict(),
            "registration_id": self.registration_id,
            "registration_version": self.registration_version,
            "source": self.source.as_dict(),
            "target": self.target.as_dict(),
            "profile_id": self.profile_id,
            "profile_fingerprint_sha256": self.profile_fingerprint_sha256,
            "exclusion_fingerprint_sha256": self.exclusion_fingerprint_sha256,
            "algorithm_fingerprint_sha256": self.algorithm_fingerprint_sha256,
            "algorithm_settings": robust_registration_algorithm_settings(),
            "policy_fingerprint_sha256": self.policy_fingerprint_sha256,
            "required_zones": [zone.value for zone in self.required_zones],
            "selected_family": (
                None if self.selected_family is None else self.selected_family.value
            ),
        }


@dataclass(frozen=True, slots=True)
class _Features:
    keypoints: tuple[Any, ...]
    descriptors: npt.NDArray[np.float32]


@dataclass(frozen=True, slots=True)
class _MatchedPoints:
    source: FloatArray
    target: FloatArray
    evidence: CorrespondenceEvidence


def robust_registration_environment() -> dict[str, object]:
    """Return the actual deterministic backend identity and settings."""

    with _CV_LOCK:
        _configure_opencv_determinism()
        return {
            "numpy_distribution_version": importlib.metadata.version("numpy"),
            "numpy_version": np.__version__,
            "opencv_opencl_enabled": bool(cv2.ocl.useOpenCL()),
            "opencv_distribution": "opencv-python-headless",
            "opencv_distribution_version": importlib.metadata.version(
                "opencv-python-headless"
            ),
            "opencv_threads": int(cv2.getNumThreads()),
            "opencv_version": cv2.__version__,
            "rng_seed": _OPENCV_RNG_SEED,
        }


def robust_registration_algorithm_settings() -> dict[str, object]:
    """Return every frozen R1 setting outside the scalar edge policy."""

    return {
        "feature_extractor": "opencv-sift",
        "feature_limit": _FEATURE_LIMIT,
        "feature_support_margin_px": _FEATURE_SUPPORT_MARGIN,
        "feature_support_size_multiplier": _FEATURE_SUPPORT_SIZE_MULTIPLIER,
        "matcher": "bidirectional-bruteforce-l2-lowe-mutual",
        "matches_per_spatial_cell": _MATCHES_PER_SPATIAL_CELL,
        "model_order": [family.value for family in _MODEL_ORDER],
        "opencv_rng_seed": _OPENCV_RNG_SEED,
        "sift_contrast_threshold": _SIFT_CONTRAST_THRESHOLD,
        "sift_edge_threshold": _SIFT_EDGE_THRESHOLD,
        "sift_sigma": _SIFT_SIGMA,
        "spatial_cell_size_px": _SPATIAL_CELL_SIZE,
    }


def trusted_robust_registration_exclusions() -> tuple[tuple[int, int, int, int], ...]:
    """Return candidate, fixed-UI, and readiness exclusions in stable order."""

    active_profile = load_varrock_east_iron_profile()
    combined = (
        *varrock_east_iron_scene_excluded_regions(active_profile),
        *(policy.region for policy in GAMEPLAY_CHROME_POLICIES),
    )
    unique: list[tuple[int, int, int, int]] = []
    for region in combined:
        if region not in unique:
            unique.append(region)
    return tuple(unique)


class RobustRegistrationEngine:
    """Cache exact-frame features while recomputing every edge from pixels."""

    def __init__(
        self,
        *,
        policy: RegistrationPolicy = DEFAULT_ROBUST_REGISTRATION_POLICY,
    ) -> None:
        if not isinstance(policy, RegistrationPolicy):
            raise TypeError("policy must be RegistrationPolicy")
        self._policy = policy
        self._feature_cache: dict[tuple[str, str, str, str], _Features] = {}

    @property
    def policy(self) -> RegistrationPolicy:
        return self._policy

    def analyze(self, source: Frame, target: Frame) -> RobustWorldRegistration:
        """Recompute one pairwise result, caching only SHA-bound features."""

        return _analyze_robust_world_registration(
            source,
            target,
            policy=self._policy,
            feature_cache=self._feature_cache,
        )


def analyze_robust_world_registration(
    source: Frame,
    target: Frame,
    *,
    policy: RegistrationPolicy = DEFAULT_ROBUST_REGISTRATION_POLICY,
) -> RobustWorldRegistration:
    """Fit the lowest-complexity adequate world-only transform.

    The operation is read-only and serializes OpenCV's process-global
    determinism settings.  Production perception is neither called nor
    modified here.
    """

    return RobustRegistrationEngine(policy=policy).analyze(source, target)


def _analyze_robust_world_registration(
    source: Frame,
    target: Frame,
    *,
    policy: RegistrationPolicy,
    feature_cache: dict[tuple[str, str, str, str], _Features],
) -> RobustWorldRegistration:
    if not isinstance(source, Frame) or not isinstance(target, Frame):
        raise TypeError("source and target must be Frame instances")
    active_profile = load_varrock_east_iron_profile()
    required_zones = _REQUIRED_ZONES
    if {
        landmark.zone(active_profile.frame_width, active_profile.frame_height)
        for landmark in active_profile.scene_landmarks
    } != set(required_zones):
        raise RuntimeError("packaged profile no longer spans the frozen R1 macro zones")
    exclusions = trusted_robust_registration_exclusions()
    source_endpoint = _endpoint_evidence(source)
    target_endpoint = _endpoint_evidence(target)
    profile_fingerprint = _profile_fingerprint(active_profile)
    exclusion_fingerprint = _canonical_digest([list(region) for region in exclusions])
    algorithm_fingerprint = _canonical_digest(
        robust_registration_algorithm_settings()
    )
    policy_fingerprint = _canonical_digest(policy.as_dict())
    empty_correspondence = _empty_correspondence(required_zones)
    if not _frame_is_supported(source, active_profile) or not _frame_is_supported(
        target, active_profile
    ):
        return RobustWorldRegistration(
            registration_id=ROBUST_WORLD_REGISTRATION_ID,
            registration_version=ROBUST_WORLD_REGISTRATION_VERSION,
            source=source_endpoint,
            target=target_endpoint,
            profile_id=active_profile.profile_id,
            profile_fingerprint_sha256=profile_fingerprint,
            exclusion_fingerprint_sha256=exclusion_fingerprint,
            algorithm_fingerprint_sha256=algorithm_fingerprint,
            policy_fingerprint_sha256=policy_fingerprint,
            disposition=RegistrationDisposition.UNSUPPORTED_FRAME,
            detail=(
                "Robust registration requires two exact packaged-profile BGRA8888 "
                "frames; no diagnostic edge was created."
            ),
            correspondence=empty_correspondence,
            required_zones=required_zones,
            excluded_regions=exclusions,
            models=(),
            selected_family=None,
            policy=policy,
        )

    with _CV_LOCK:
        _configure_opencv_determinism()
        mask = _feature_mask(
            active_profile.frame_width,
            active_profile.frame_height,
            exclusions,
            required_zones,
        )
        source_cache_key = (
            source_endpoint.payload_sha256,
            profile_fingerprint,
            exclusion_fingerprint,
            algorithm_fingerprint,
        )
        target_cache_key = (
            target_endpoint.payload_sha256,
            profile_fingerprint,
            exclusion_fingerprint,
            algorithm_fingerprint,
        )
        source_features = feature_cache.get(source_cache_key)
        if source_features is None:
            source_features = _extract_features(
                source, mask, exclusions, required_zones
            )
            feature_cache[source_cache_key] = source_features
        target_features = feature_cache.get(target_cache_key)
        if target_features is None:
            target_features = _extract_features(
                target, mask, exclusions, required_zones
            )
            feature_cache[target_cache_key] = target_features
        matches = _match_features(
            source_features,
            target_features,
            width=active_profile.frame_width,
            height=active_profile.frame_height,
            required_zones=required_zones,
            ratio_threshold=policy.ratio_threshold,
        )
        models = tuple(
            _evaluate_model(
                family,
                matches.source,
                matches.target,
                width=active_profile.frame_width,
                height=active_profile.frame_height,
                required_zones=required_zones,
                mutual_matches=matches.evidence.balanced_matches,
                policy=policy,
            )
            for family in _MODEL_ORDER
        )

    selected = next((model for model in models if model.adequate), None)
    if selected is not None:
        disposition = RegistrationDisposition.ACCEPTED
        detail = (
            f"{selected.family.value} is the lowest-complexity model satisfying "
            "mutual-match, all-zone, residual, cycle, and distortion gates; this "
            "diagnostic edge has no production authority."
        )
    elif min(
        matches.evidence.source_features, matches.evidence.target_features
    ) < 2:
        disposition = RegistrationDisposition.INSUFFICIENT_FEATURES
        detail = (
            f"Only {matches.evidence.source_features}/"
            f"{matches.evidence.target_features} trusted "
            "world features were available; no edge was created."
        )
    elif matches.evidence.balanced_matches < policy.minimum_mutual_matches:
        disposition = RegistrationDisposition.INSUFFICIENT_MUTUAL_MATCHES
        detail = (
            f"Only {matches.evidence.balanced_matches} balanced mutual matches remained; "
            f"{policy.minimum_mutual_matches} are required."
        )
    elif any(
        dict(matches.evidence.per_zone_mutual_matches).get(zone, 0)
        < policy.minimum_inliers_per_zone
        for zone in required_zones
    ):
        disposition = RegistrationDisposition.INSUFFICIENT_DISTRIBUTION
        detail = (
            "Mutual matches did not cover every frozen macro zone before fitting; "
            "local similarity cannot create a graph edge."
        )
    elif any(
        reason.startswith(("source_zone_", "target_zone_"))
        for reason in max(models, key=lambda model: model.inliers).rejection_reasons
    ):
        disposition = RegistrationDisposition.INSUFFICIENT_DISTRIBUTION
        detail = (
            "The strongest fitted model lacks the required inlier count or spatial "
            "cell coverage at one or both endpoints in every frozen macro zone; "
            "aggregate local similarity cannot create a graph edge."
        )
    else:
        disposition = RegistrationDisposition.GLOBAL_MODEL_INADEQUATE
        detail = (
            "Distributed correspondences exist, but translation, similarity, affine, "
            "and homography all fail at least one residual, cycle, conditioning, or "
            "coverage gate; parallax/non-planarity or a false local match is likely."
        )
    return RobustWorldRegistration(
        registration_id=ROBUST_WORLD_REGISTRATION_ID,
        registration_version=ROBUST_WORLD_REGISTRATION_VERSION,
        source=source_endpoint,
        target=target_endpoint,
        profile_id=active_profile.profile_id,
        profile_fingerprint_sha256=profile_fingerprint,
        exclusion_fingerprint_sha256=exclusion_fingerprint,
        algorithm_fingerprint_sha256=algorithm_fingerprint,
        policy_fingerprint_sha256=policy_fingerprint,
        disposition=disposition,
        detail=detail,
        correspondence=matches.evidence,
        required_zones=required_zones,
        excluded_regions=exclusions,
        models=models,
        selected_family=None if selected is None else selected.family,
        policy=policy,
    )


def _configure_opencv_determinism() -> None:
    cv2.setNumThreads(1)
    cv2.ocl.setUseOpenCL(False)
    cv2.setRNGSeed(_OPENCV_RNG_SEED)


def _frame_is_supported(frame: Frame, profile: ResourceDetectorProfile) -> bool:
    return (
        frame.width == profile.frame_width
        and frame.height == profile.frame_height
        and frame.pixel_format is PixelFormat.BGRA8888
        and len(frame.payload)
        == frame.width * frame.height * frame.pixel_format.bytes_per_pixel
    )


def _endpoint_evidence(frame: Frame) -> EndpointEvidence:
    return EndpointEvidence(
        payload_sha256=hashlib.sha256(frame.payload).hexdigest(),
        payload_bytes=len(frame.payload),
        width=frame.width,
        height=frame.height,
        pixel_format=frame.pixel_format.value,
    )


def _profile_fingerprint(profile: ResourceDetectorProfile) -> str:
    evidence: dict[str, object] = {
        "candidates": [
            {
                "region": list(candidate.region),
                "resource_id": candidate.resource_id,
            }
            for candidate in profile.candidates
        ],
        "frame_height": profile.frame_height,
        "frame_width": profile.frame_width,
        "location_id": profile.location_id,
        "minimum_landmark_quorum": profile.minimum_landmark_quorum,
        "minimum_landmark_zones": profile.minimum_landmark_zones,
        "pixel_format": profile.pixel_format.value,
        "profile_id": profile.profile_id,
        "scene_landmarks": [
            {
                "grid": landmark.grid,
                "landmark_id": landmark.landmark_id,
                "macro_zone": landmark.macro_zone.value,
                "maximum_distance": landmark.maximum_distance,
                "reference_descriptor": list(landmark.reference_descriptor),
                "region": list(landmark.region),
            }
            for landmark in profile.scene_landmarks
        ],
    }
    return _canonical_digest(evidence)


def _canonical_digest(value: object) -> str:
    payload = json.dumps(
        value,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _frame_gray(frame: Frame) -> ByteArray:
    pixels = np.frombuffer(frame.payload, dtype=np.uint8).reshape(
        frame.height, frame.width, 4
    )
    return cast(ByteArray, cv2.cvtColor(pixels, cv2.COLOR_BGRA2GRAY))


def _feature_mask(
    width: int,
    height: int,
    exclusions: tuple[tuple[int, int, int, int], ...],
    required_zones: tuple[MacroZone, ...],
) -> ByteArray:
    mask = np.zeros((height, width), dtype=np.uint8)
    west_end = math.ceil(width / 2.0)
    north_end = math.ceil(height / 2.0)
    zone_slices = {
        MacroZone.NORTH_WEST: (slice(0, north_end), slice(0, west_end)),
        MacroZone.NORTH_EAST: (slice(0, north_end), slice(west_end, width)),
        MacroZone.SOUTH_WEST: (slice(north_end, height), slice(0, west_end)),
        MacroZone.SOUTH_EAST: (slice(north_end, height), slice(west_end, width)),
    }
    for zone in required_zones:
        rows, columns = zone_slices[zone]
        mask[rows, columns] = 255
    for x, y, region_width, region_height in exclusions:
        left = max(0, x)
        top = max(0, y)
        right = min(width, x + region_width)
        bottom = min(height, y + region_height)
        mask[top:bottom, left:right] = 0
    return mask


def _extract_features(
    frame: Frame,
    mask: ByteArray,
    exclusions: tuple[tuple[int, int, int, int], ...],
    required_zones: tuple[MacroZone, ...],
) -> _Features:
    detector = cv2.SIFT.create(
        nfeatures=_FEATURE_LIMIT,
        nOctaveLayers=3,
        contrastThreshold=_SIFT_CONTRAST_THRESHOLD,
        edgeThreshold=_SIFT_EDGE_THRESHOLD,
        sigma=_SIFT_SIGMA,
    )
    raw_keypoints, raw_descriptors = detector.detectAndCompute(_frame_gray(frame), mask)
    if raw_descriptors is None or not raw_keypoints:
        return _Features((), np.empty((0, 128), dtype=np.float32))
    retained = [
        index
        for index, keypoint in enumerate(raw_keypoints)
        if _keypoint_support_allowed(
            keypoint,
            width=frame.width,
            height=frame.height,
            mask=mask,
            exclusions=exclusions,
            required_zones=required_zones,
        )
    ]
    retained.sort(key=lambda index: _keypoint_key(raw_keypoints[index], index))
    keypoints = tuple(raw_keypoints[index] for index in retained)
    descriptors = np.asarray(raw_descriptors[retained], dtype=np.float32)
    return _Features(keypoints, descriptors)


def _keypoint_key(keypoint: Any, original_index: int) -> tuple[float, ...]:
    return (
        round(float(keypoint.pt[1]), 6),
        round(float(keypoint.pt[0]), 6),
        round(float(keypoint.size), 6),
        round(float(keypoint.angle), 6),
        round(float(keypoint.response), 9),
        float(keypoint.octave),
        float(original_index),
    )


def _keypoint_support_allowed(
    keypoint: Any,
    *,
    width: int,
    height: int,
    mask: ByteArray,
    exclusions: tuple[tuple[int, int, int, int], ...],
    required_zones: tuple[MacroZone, ...],
) -> bool:
    x = float(keypoint.pt[0])
    y = float(keypoint.pt[1])
    if _zone_for_point(x, y, width, height) not in required_zones:
        return False
    radius = max(
        _FEATURE_SUPPORT_MARGIN,
        int(math.ceil(float(keypoint.size) * _FEATURE_SUPPORT_SIZE_MULTIPLIER)),
    )
    support = (
        int(math.floor(x)) - radius,
        int(math.floor(y)) - radius,
        radius * 2 + 1,
        radius * 2 + 1,
    )
    support_x, support_y, support_width, support_height = support
    if (
        support_x < 0
        or support_y < 0
        or support_x + support_width > width
        or support_y + support_height > height
    ):
        return False
    support_mask = mask[
        support_y : support_y + support_height,
        support_x : support_x + support_width,
    ]
    if not bool(np.all(support_mask)):
        return False
    return not any(_regions_overlap(support, excluded) for excluded in exclusions)


def _match_features(
    source: _Features,
    target: _Features,
    *,
    width: int,
    height: int,
    required_zones: tuple[MacroZone, ...],
    ratio_threshold: float,
) -> _MatchedPoints:
    if len(source.keypoints) < 2 or len(target.keypoints) < 2:
        return _MatchedPoints(
            np.empty((0, 2), dtype=np.float64),
            np.empty((0, 2), dtype=np.float64),
            CorrespondenceEvidence(
                source_features=len(source.keypoints),
                target_features=len(target.keypoints),
                total_forward_matches=len(source.keypoints),
                total_reverse_matches=len(target.keypoints),
                forward_ratio_matches=0,
                reverse_ratio_matches=0,
                mutual_matches=0,
                balanced_matches=0,
                per_zone_mutual_matches=tuple((zone, 0) for zone in required_zones),
            ),
        )
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    forward_pairs = cast(
        list[list[Any]], matcher.knnMatch(source.descriptors, target.descriptors, k=2)
    )
    reverse_pairs = cast(
        list[list[Any]], matcher.knnMatch(target.descriptors, source.descriptors, k=2)
    )
    forward = _ratio_matches(forward_pairs, ratio_threshold)
    reverse = _ratio_matches(reverse_pairs, ratio_threshold)
    mutual = [
        match
        for query_index, match in sorted(forward.items())
        if (reverse_match := reverse.get(int(match.trainIdx))) is not None
        and int(reverse_match.trainIdx) == query_index
    ]
    mutual.sort(
        key=lambda match: (
            round(float(match.distance), 9),
            int(match.queryIdx),
            int(match.trainIdx),
        )
    )
    zone_counts: dict[MacroZone, int] = defaultdict(int)
    balanced: list[Any] = []
    source_cell_counts: dict[tuple[MacroZone, int, int], int] = defaultdict(int)
    target_cell_counts: dict[tuple[MacroZone, int, int], int] = defaultdict(int)
    for match in mutual:
        point = source.keypoints[int(match.queryIdx)].pt
        zone = _zone_for_point(float(point[0]), float(point[1]), width, height)
        zone_counts[zone] += 1
        source_cell = (
            zone,
            int(float(point[0]) // _SPATIAL_CELL_SIZE),
            int(float(point[1]) // _SPATIAL_CELL_SIZE),
        )
        target_point = target.keypoints[int(match.trainIdx)].pt
        target_zone = _zone_for_point(
            float(target_point[0]), float(target_point[1]), width, height
        )
        target_cell = (
            target_zone,
            int(float(target_point[0]) // _SPATIAL_CELL_SIZE),
            int(float(target_point[1]) // _SPATIAL_CELL_SIZE),
        )
        if (
            source_cell_counts[source_cell] >= _MATCHES_PER_SPATIAL_CELL
            or target_cell_counts[target_cell] >= _MATCHES_PER_SPATIAL_CELL
        ):
            continue
        source_cell_counts[source_cell] += 1
        target_cell_counts[target_cell] += 1
        balanced.append(match)
    balanced.sort(key=lambda match: (int(match.queryIdx), int(match.trainIdx)))
    source_points = np.asarray(
        [source.keypoints[int(match.queryIdx)].pt for match in balanced],
        dtype=np.float64,
    ).reshape(-1, 2)
    target_points = np.asarray(
        [target.keypoints[int(match.trainIdx)].pt for match in balanced],
        dtype=np.float64,
    ).reshape(-1, 2)
    evidence = CorrespondenceEvidence(
        source_features=len(source.keypoints),
        target_features=len(target.keypoints),
        total_forward_matches=len(forward_pairs),
        total_reverse_matches=len(reverse_pairs),
        forward_ratio_matches=len(forward),
        reverse_ratio_matches=len(reverse),
        mutual_matches=len(mutual),
        balanced_matches=len(balanced),
        per_zone_mutual_matches=tuple(
            (zone, zone_counts.get(zone, 0)) for zone in required_zones
        ),
    )
    return _MatchedPoints(source_points, target_points, evidence)


def _ratio_matches(pairs: list[list[Any]], threshold: float) -> dict[int, Any]:
    accepted: dict[int, Any] = {}
    for pair in pairs:
        if len(pair) != 2:
            continue
        first, second = pair
        if float(first.distance) < threshold * float(second.distance):
            accepted[int(first.queryIdx)] = first
    return accepted


def _evaluate_model(
    family: ModelFamily,
    source: FloatArray,
    target: FloatArray,
    *,
    width: int,
    height: int,
    required_zones: tuple[MacroZone, ...],
    mutual_matches: int,
    policy: RegistrationPolicy,
) -> ModelEvidence:
    forward = _fit_model(family, source, target, policy)
    reverse = _fit_model(family, target, source, policy)
    if forward is None or reverse is None:
        return ModelEvidence(
            family=family,
            forward_matrix=None,
            reverse_matrix=None,
            inliers=0,
            inlier_ratio=0.0,
            source_zone_inliers=tuple((zone, 0) for zone in required_zones),
            target_zone_inliers=tuple((zone, 0) for zone in required_zones),
            source_zone_cells=tuple((zone, 0) for zone in required_zones),
            target_zone_cells=tuple((zone, 0) for zone in required_zones),
            median_residual_px=None,
            p90_residual_px=None,
            cycle_median_px=None,
            cycle_p90_px=None,
            distortion=None,
            adequate=False,
            rejection_reasons=("model_fit_unavailable",),
        )
    forward_projected = _transform_points(forward, source)
    reverse_projected = _transform_points(reverse, target)
    forward_residuals = np.linalg.norm(forward_projected - target, axis=1)
    reverse_residuals = np.linalg.norm(reverse_projected - source, axis=1)
    symmetric_residuals = np.maximum(forward_residuals, reverse_residuals)
    inlier_mask = symmetric_residuals <= policy.reprojection_inlier_threshold_px
    inliers = int(np.count_nonzero(inlier_mask))
    ratio = inliers / mutual_matches if mutual_matches else 0.0
    source_inliers = source[inlier_mask]
    target_inliers = target[inlier_mask]
    source_zone_counts = _zone_counts(source_inliers, width, height, required_zones)
    target_zone_counts = _zone_counts(target_inliers, width, height, required_zones)
    source_zone_cells = _zone_cell_counts(source_inliers, width, height, required_zones)
    target_zone_cells = _zone_cell_counts(target_inliers, width, height, required_zones)

    if inliers:
        selected_residuals = symmetric_residuals[inlier_mask]
        median_residual = float(np.median(selected_residuals))
        p90_residual = float(np.percentile(selected_residuals, 90))
        forward_cycle = _transform_points(
            reverse, _transform_points(forward, source_inliers)
        )
        reverse_cycle = _transform_points(
            forward, _transform_points(reverse, target_inliers)
        )
        cycle_errors = np.concatenate(
            (
                np.linalg.norm(forward_cycle - source_inliers, axis=1),
                np.linalg.norm(reverse_cycle - target_inliers, axis=1),
            )
        )
        if bool(np.all(np.isfinite(cycle_errors))):
            cycle_median = float(np.median(cycle_errors))
            cycle_p90 = float(np.percentile(cycle_errors, 90))
        else:
            cycle_median = None
            cycle_p90 = None
    else:
        median_residual = None
        p90_residual = None
        cycle_median = None
        cycle_p90 = None

    forward_distortion = _distortion_evidence(forward, width, height, policy)
    reverse_distortion = _distortion_evidence(reverse, width, height, policy)
    distortion = _combine_distortion(forward_distortion, reverse_distortion)
    reasons: list[str] = []
    if mutual_matches < policy.minimum_mutual_matches:
        reasons.append("insufficient_mutual_matches")
    if inliers < policy.minimum_inliers:
        reasons.append("insufficient_inliers")
    if ratio < policy.minimum_inlier_ratio:
        reasons.append("inlier_ratio_below_minimum")
    for zone in required_zones:
        if dict(source_zone_counts)[zone] < policy.minimum_inliers_per_zone:
            reasons.append(f"source_zone_{zone.value}_underrepresented")
        if dict(target_zone_counts)[zone] < policy.minimum_inliers_per_zone:
            reasons.append(f"target_zone_{zone.value}_underrepresented")
        if dict(source_zone_cells)[zone] < policy.minimum_spatial_cells_per_zone:
            reasons.append(f"source_zone_{zone.value}_not_spatially_distributed")
        if dict(target_zone_cells)[zone] < policy.minimum_spatial_cells_per_zone:
            reasons.append(f"target_zone_{zone.value}_not_spatially_distributed")
    maximum_p90 = (
        policy.maximum_homography_p90_residual_px
        if family is ModelFamily.HOMOGRAPHY
        else policy.maximum_p90_residual_px
    )
    if median_residual is None or median_residual > policy.maximum_median_residual_px:
        reasons.append("median_residual_above_maximum")
    if p90_residual is None or p90_residual > maximum_p90:
        reasons.append("p90_residual_above_maximum")
    if cycle_median is None or cycle_median > policy.maximum_cycle_median_px:
        reasons.append("cycle_median_above_maximum")
    if cycle_p90 is None or cycle_p90 > policy.maximum_cycle_p90_px:
        reasons.append("cycle_p90_above_maximum")
    if not distortion.passed:
        reasons.append("conditioning_or_distortion_rejected")
    return ModelEvidence(
        family=family,
        forward_matrix=_matrix_tuple(forward),
        reverse_matrix=_matrix_tuple(reverse),
        inliers=inliers,
        inlier_ratio=ratio,
        source_zone_inliers=source_zone_counts,
        target_zone_inliers=target_zone_counts,
        source_zone_cells=source_zone_cells,
        target_zone_cells=target_zone_cells,
        median_residual_px=median_residual,
        p90_residual_px=p90_residual,
        cycle_median_px=cycle_median,
        cycle_p90_px=cycle_p90,
        distortion=distortion,
        adequate=not reasons,
        rejection_reasons=tuple(reasons),
    )


def _fit_model(
    family: ModelFamily,
    source: FloatArray,
    target: FloatArray,
    policy: RegistrationPolicy,
) -> FloatArray | None:
    minimum_points = {
        ModelFamily.TRANSLATION: 1,
        ModelFamily.SIMILARITY: 2,
        ModelFamily.AFFINE: 3,
        ModelFamily.HOMOGRAPHY: 4,
    }[family]
    if len(source) < minimum_points:
        return None
    cv2.setRNGSeed(_OPENCV_RNG_SEED)
    if family is ModelFamily.TRANSLATION:
        deltas = target - source
        translation = np.median(deltas, axis=0)
        residuals = np.linalg.norm(deltas - translation, axis=1)
        inliers = residuals <= policy.reprojection_inlier_threshold_px
        if not np.any(inliers):
            return None
        translation = np.median(deltas[inliers], axis=0)
        return np.asarray(
            (
                (1.0, 0.0, float(translation[0])),
                (0.0, 1.0, float(translation[1])),
                (0.0, 0.0, 1.0),
            ),
            dtype=np.float64,
        )
    source_cv = np.asarray(source, dtype=np.float64)
    target_cv = np.asarray(target, dtype=np.float64)
    if family is ModelFamily.SIMILARITY:
        matrix, _ = cv2.estimateAffinePartial2D(
            source_cv,
            target_cv,
            method=cv2.RANSAC,
            ransacReprojThreshold=policy.reprojection_inlier_threshold_px,
            maxIters=10000,
            confidence=0.999,
            refineIters=50,
        )
        return _homogeneous_affine(matrix)
    if family is ModelFamily.AFFINE:
        matrix, _ = cv2.estimateAffine2D(
            source_cv,
            target_cv,
            method=cv2.RANSAC,
            ransacReprojThreshold=policy.reprojection_inlier_threshold_px,
            maxIters=10000,
            confidence=0.999,
            refineIters=50,
        )
        return _homogeneous_affine(matrix)
    matrix, _ = cv2.findHomography(
        source_cv,
        target_cv,
        cv2.RANSAC,
        policy.reprojection_inlier_threshold_px,
        maxIters=10000,
        confidence=0.999,
    )
    if matrix is None:
        return None
    homogeneous = np.asarray(matrix, dtype=np.float64)
    if (
        homogeneous.shape != (3, 3)
        or not bool(np.all(np.isfinite(homogeneous)))
        or abs(float(homogeneous[2, 2])) <= 1e-12
    ):
        return None
    return np.asarray(homogeneous / homogeneous[2, 2], dtype=np.float64)


def _homogeneous_affine(matrix: Any) -> FloatArray | None:
    if matrix is None:
        return None
    affine = np.asarray(matrix, dtype=np.float64)
    if affine.shape != (2, 3) or not bool(np.all(np.isfinite(affine))):
        return None
    return np.vstack((affine, np.asarray((0.0, 0.0, 1.0), dtype=np.float64)))


def _transform_points(matrix: FloatArray, points: FloatArray) -> FloatArray:
    if not len(points):
        return np.empty((0, 2), dtype=np.float64)
    homogeneous = np.column_stack((points, np.ones(len(points), dtype=np.float64)))
    projected = homogeneous @ matrix.T
    denominators = projected[:, 2:3]
    result = np.full((len(points), 2), np.inf, dtype=np.float64)
    valid = np.abs(denominators[:, 0]) > 1e-12
    result[valid] = projected[valid, :2] / denominators[valid]
    return result


def _distortion_evidence(
    matrix: FloatArray,
    width: int,
    height: int,
    policy: RegistrationPolicy,
) -> DistortionEvidence:
    finite = bool(np.all(np.isfinite(matrix)))
    sample_x = (0.0, width / 2.0, float(width - 1))
    sample_y = (0.0, height / 2.0, float(height - 1))
    minimum_scale = math.inf
    maximum_scale = 0.0
    maximum_condition = 0.0
    orientation_preserved = True
    if finite:
        for y in sample_y:
            for x in sample_x:
                basis = np.asarray(((x, y), (x + 1.0, y), (x, y + 1.0)), dtype=np.float64)
                transformed = _transform_points(matrix, basis)
                if not np.all(np.isfinite(transformed)):
                    finite = False
                    break
                jacobian = np.column_stack(
                    (transformed[1] - transformed[0], transformed[2] - transformed[0])
                )
                singular = np.linalg.svd(jacobian, compute_uv=False)
                minimum_scale = min(minimum_scale, float(np.min(singular)))
                maximum_scale = max(maximum_scale, float(np.max(singular)))
                if float(np.min(singular)) <= 1e-12:
                    maximum_condition = math.inf
                else:
                    maximum_condition = max(
                        maximum_condition,
                        float(np.max(singular) / np.min(singular)),
                    )
                orientation_preserved = orientation_preserved and float(
                    np.linalg.det(jacobian)
                ) > 0.0
            if not finite:
                break
    if not math.isfinite(minimum_scale):
        minimum_scale = 0.0
    scale_ratio = (
        maximum_scale / minimum_scale if minimum_scale > 1e-12 else math.inf
    )
    perspective_span = abs(float(matrix[2, 0])) * width + abs(
        float(matrix[2, 1])
    ) * height
    overlap = _overlap_fraction(matrix, width, height) if finite else 0.0
    passed = (
        finite
        and orientation_preserved
        and minimum_scale >= policy.minimum_local_scale
        and maximum_scale <= policy.maximum_local_scale
        and scale_ratio <= policy.maximum_local_scale_ratio
        and maximum_condition <= policy.maximum_local_condition
        and overlap >= policy.minimum_overlap_fraction
        and perspective_span <= policy.maximum_perspective_span
    )
    return DistortionEvidence(
        finite=finite,
        orientation_preserved=orientation_preserved,
        minimum_local_scale=minimum_scale,
        maximum_local_scale=maximum_scale,
        maximum_local_scale_ratio=scale_ratio,
        maximum_local_condition=maximum_condition,
        overlap_fraction=overlap,
        perspective_span=perspective_span,
        passed=passed,
    )


def _overlap_fraction(matrix: FloatArray, width: int, height: int) -> float:
    corners = np.asarray(
        ((0.0, 0.0), (width - 1.0, 0.0), (width - 1.0, height - 1.0), (0.0, height - 1.0)),
        dtype=np.float64,
    )
    transformed = _transform_points(matrix, corners)
    if not np.all(np.isfinite(transformed)):
        return 0.0
    polygon = cv2.convexHull(np.asarray(transformed, dtype=np.float32))
    target = np.asarray(
        (((0.0, 0.0),), ((width - 1.0, 0.0),), ((width - 1.0, height - 1.0),), ((0.0, height - 1.0),)),
        dtype=np.float32,
    )
    try:
        intersection, _ = cv2.intersectConvexConvex(polygon, target)
    except cv2.error:
        return 0.0
    return max(0.0, min(1.0, float(intersection) / ((width - 1) * (height - 1))))


def _combine_distortion(
    forward: DistortionEvidence, reverse: DistortionEvidence
) -> DistortionEvidence:
    return DistortionEvidence(
        finite=forward.finite and reverse.finite,
        orientation_preserved=(
            forward.orientation_preserved and reverse.orientation_preserved
        ),
        minimum_local_scale=min(
            forward.minimum_local_scale, reverse.minimum_local_scale
        ),
        maximum_local_scale=max(
            forward.maximum_local_scale, reverse.maximum_local_scale
        ),
        maximum_local_scale_ratio=max(
            forward.maximum_local_scale_ratio,
            reverse.maximum_local_scale_ratio,
        ),
        maximum_local_condition=max(
            forward.maximum_local_condition, reverse.maximum_local_condition
        ),
        overlap_fraction=min(forward.overlap_fraction, reverse.overlap_fraction),
        perspective_span=max(forward.perspective_span, reverse.perspective_span),
        passed=forward.passed and reverse.passed,
    )


def _zone_counts(
    points: FloatArray,
    width: int,
    height: int,
    required_zones: tuple[MacroZone, ...],
) -> tuple[tuple[MacroZone, int], ...]:
    counts: dict[MacroZone, int] = defaultdict(int)
    for x, y in points:
        counts[_zone_for_point(float(x), float(y), width, height)] += 1
    return tuple((zone, counts.get(zone, 0)) for zone in required_zones)


def _zone_cell_counts(
    points: FloatArray,
    width: int,
    height: int,
    required_zones: tuple[MacroZone, ...],
) -> tuple[tuple[MacroZone, int], ...]:
    cells: dict[MacroZone, set[tuple[int, int]]] = defaultdict(set)
    for x, y in points:
        zone = _zone_for_point(float(x), float(y), width, height)
        cells[zone].add(
            (int(float(x) // _SPATIAL_CELL_SIZE), int(float(y) // _SPATIAL_CELL_SIZE))
        )
    return tuple((zone, len(cells.get(zone, set()))) for zone in required_zones)


def _zone_for_point(x: float, y: float, width: int, height: int) -> MacroZone:
    northern = y < height / 2.0
    western = x < width / 2.0
    if northern:
        return MacroZone.NORTH_WEST if western else MacroZone.NORTH_EAST
    return MacroZone.SOUTH_WEST if western else MacroZone.SOUTH_EAST


def _empty_correspondence(
    required_zones: tuple[MacroZone, ...],
) -> CorrespondenceEvidence:
    return CorrespondenceEvidence(
        source_features=0,
        target_features=0,
        total_forward_matches=0,
        total_reverse_matches=0,
        forward_ratio_matches=0,
        reverse_ratio_matches=0,
        mutual_matches=0,
        balanced_matches=0,
        per_zone_mutual_matches=tuple((zone, 0) for zone in required_zones),
    )


def _regions_overlap(
    first: tuple[int, int, int, int], second: tuple[int, int, int, int]
) -> bool:
    first_x, first_y, first_width, first_height = first
    second_x, second_y, second_width, second_height = second
    return (
        first_x < second_x + second_width
        and second_x < first_x + first_width
        and first_y < second_y + second_height
        and second_y < first_y + first_height
    )


def _matrix_tuple(matrix: FloatArray) -> Matrix3:
    return cast(
        Matrix3,
        tuple(
            tuple(_rounded(float(matrix[row, column])) for column in range(3))
            for row in range(3)
        ),
    )


def _matrix_dict(matrix: Matrix3 | None) -> object:
    return None if matrix is None else [list(row) for row in matrix]


def _rounded(value: float) -> float:
    if not math.isfinite(value):
        return value
    return round(value, 12)


def _rounded_optional(value: float | None) -> float | None:
    return None if value is None or not math.isfinite(value) else _rounded(value)


def _rounded_finite_or_none(value: float) -> float | None:
    return _rounded(value) if math.isfinite(value) else None
