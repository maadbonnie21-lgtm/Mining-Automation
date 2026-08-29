"""Cheap, world-only veto against stale Issue #31 camera guidance.

The camera guidance selector can be intentionally expensive.  A fresh frame
must therefore be captured immediately before input and compared with the
frame that produced the pending guidance.  This module retains that pending
guidance only when every frozen structural world region remains materially
unchanged.

This is a veto boundary, not perception.  It cannot accept a scene, expose a
resource, or turn any production rejection into success.  Candidate pixels,
fixed UI, and gameplay-readiness chrome are always excluded from its evidence.
Although the capture contract permits equal monotonic timestamps, this arming
seam deliberately requires both identity and timestamp to increase strictly;
equality cannot prove that a new pre-input capture occurred.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from ..capture import Frame, PixelFormat
from ..perception.production_profiles import (
    load_varrock_east_iron_profile,
    varrock_east_iron_scene_excluded_regions,
)
from ..perception.scene_landmarks import MacroZone, SceneLandmarkProfile
from .client_readiness import GAMEPLAY_CHROME_POLICIES

__all__ = [
    "CAMERA_ARM_GUARD_ID",
    "CAMERA_ARM_GUARD_LANDMARK_IDS",
    "CAMERA_ARM_GUARD_VERSION",
    "CAMERA_ARM_GUARD_EXCLUDED_REGIONS",
    "CAMERA_ARM_GUARD_STRUCTURAL_REGIONS",
    "CAMERA_ARM_MATERIAL_CHANNEL_DELTA",
    "CAMERA_ARM_MAXIMUM_CHANGED_PIXEL_FRACTION",
    "CAMERA_ARM_MAXIMUM_MEAN_CHANNEL_DELTA",
    "CAMERA_ARM_MINIMUM_REGION_COVERAGE",
    "CAMERA_ARM_REQUIRED_STABLE_LANDMARKS",
    "CAMERA_ARM_REQUIRED_STABLE_ZONES",
    "CAMERA_ARM_REQUIRED_REGION_COUNT",
    "CAMERA_ARM_REQUIRED_ZONE_COUNT",
    "CameraArmGuardDisposition",
    "CameraArmGuardReason",
    "CameraArmGuardRegionMetric",
    "CameraArmGuardResult",
    "evaluate_camera_arm_guard",
]

CAMERA_ARM_GUARD_ID: Final[str] = "issue31-world-only-arm-guard"
CAMERA_ARM_GUARD_VERSION: Final[str] = "1.0.0"
CAMERA_ARM_GUARD_LANDMARK_IDS: Final[tuple[str, ...]] = (
    "west-ridge",
    "west-lower-ridge",
    "south-path",
    "south-central-edge",
    "north-east-wall",
    "east-bank-edge",
)
CAMERA_ARM_GUARD_STRUCTURAL_REGIONS: Final[
    tuple[tuple[str, MacroZone, tuple[int, int, int, int]], ...]
] = (
    ("west-ridge", MacroZone.NORTH_WEST, (6, 376, 48, 48)),
    ("west-lower-ridge", MacroZone.NORTH_WEST, (6, 448, 48, 48)),
    ("south-path", MacroZone.SOUTH_WEST, (258, 784, 48, 48)),
    ("south-central-edge", MacroZone.SOUTH_WEST, (426, 736, 48, 48)),
    ("north-east-wall", MacroZone.NORTH_EAST, (689, 299, 48, 48)),
    ("east-bank-edge", MacroZone.NORTH_EAST, (678, 448, 48, 48)),
)
CAMERA_ARM_GUARD_EXCLUDED_REGIONS: Final[
    tuple[tuple[int, int, int, int], ...]
] = (
    (263, 409, 20, 20),
    (295, 490, 20, 20),
    (405, 424, 20, 20),
    (590, 365, 20, 20),
    (0, 0, 1005, 34),
    (545, 34, 222, 220),
    (767, 34, 238, 816),
    (520, 500, 485, 350),
    (0, 850, 1005, 228),
    (588, 34, 40, 40),
    (628, 74, 139, 180),
    (0, 834, 520, 16),
)

# Frozen, conservative v1 comparison policy.  The mean gate catches diffuse
# changes below the per-pixel material threshold; the fraction gate catches a
# concentrated structural change.  Both operate on colour channels only.
CAMERA_ARM_MATERIAL_CHANNEL_DELTA: Final[int] = 24
CAMERA_ARM_MAXIMUM_MEAN_CHANNEL_DELTA: Final[float] = 4.0
CAMERA_ARM_MAXIMUM_CHANGED_PIXEL_FRACTION: Final[float] = 0.08
CAMERA_ARM_REQUIRED_REGION_COUNT: Final[int] = 6
CAMERA_ARM_REQUIRED_ZONE_COUNT: Final[int] = 3
CAMERA_ARM_REQUIRED_STABLE_LANDMARKS: Final[int] = CAMERA_ARM_REQUIRED_REGION_COUNT
CAMERA_ARM_REQUIRED_STABLE_ZONES: Final[int] = CAMERA_ARM_REQUIRED_ZONE_COUNT
CAMERA_ARM_MINIMUM_REGION_COVERAGE: Final[float] = 0.75


class CameraArmGuardDisposition(StrEnum):
    """Whether pending guidance may survive the fresh arming capture."""

    RETAIN = "retain"
    DISCARD_RESTART = "discard_restart"


class CameraArmGuardReason(StrEnum):
    """Stable reason for retaining or discarding pending guidance."""

    UNCHANGED_WORLD = "unchanged_world"
    UNSUPPORTED_DECISION_FRAME = "unsupported_decision_frame"
    UNSUPPORTED_ARM_FRAME = "unsupported_arm_frame"
    NON_FRESH_ARM_FRAME = "non_fresh_arm_frame"
    AMBIGUOUS_WORLD_EVIDENCE = "ambiguous_world_evidence"
    MATERIAL_WORLD_CHANGE = "material_world_change"


@dataclass(frozen=True, slots=True)
class CameraArmGuardRegionMetric:
    """Deterministic colour-change evidence for one frozen world landmark."""

    landmark_id: str
    zone: MacroZone
    region: tuple[int, int, int, int]
    compared_pixel_count: int
    total_pixel_count: int
    mean_absolute_channel_delta: float
    changed_pixel_fraction: float
    within_limit: bool

    def __post_init__(self) -> None:
        if (
            not isinstance(self.landmark_id, str)
            or not self.landmark_id
            or self.landmark_id != self.landmark_id.strip()
        ):
            raise ValueError("arm-guard landmark_id must be a non-empty trimmed string")
        if not isinstance(self.zone, MacroZone):
            raise ValueError("arm-guard region zone must be MacroZone")
        if (
            not isinstance(self.region, tuple)
            or len(self.region) != 4
            or any(isinstance(value, bool) or not isinstance(value, int) for value in self.region)
        ):
            raise ValueError("arm-guard region must contain four integers")
        x, y, width, height = self.region
        if x < 0 or y < 0 or width <= 0 or height <= 0:
            raise ValueError("arm-guard region must have positive frame-local area")
        for count_name, count_value in (
            ("compared_pixel_count", self.compared_pixel_count),
            ("total_pixel_count", self.total_pixel_count),
        ):
            if (
                isinstance(count_value, bool)
                or not isinstance(count_value, int)
                or count_value <= 0
            ):
                raise ValueError(f"arm-guard {count_name} must be a positive integer")
        if self.compared_pixel_count > self.total_pixel_count:
            raise ValueError("arm-guard compared pixels cannot exceed total pixels")
        if (
            isinstance(self.mean_absolute_channel_delta, bool)
            or not isinstance(self.mean_absolute_channel_delta, (int, float))
            or not math.isfinite(self.mean_absolute_channel_delta)
            or not 0.0 <= self.mean_absolute_channel_delta <= 255.0
        ):
            raise ValueError("arm-guard mean channel delta must be finite and in [0, 255]")
        if (
            isinstance(self.changed_pixel_fraction, bool)
            or not isinstance(self.changed_pixel_fraction, (int, float))
            or not math.isfinite(self.changed_pixel_fraction)
            or not 0.0 <= self.changed_pixel_fraction <= 1.0
        ):
            raise ValueError("arm-guard changed fraction must be finite and in [0, 1]")
        expected_within_limit = (
            self.mean_absolute_channel_delta
            < CAMERA_ARM_MAXIMUM_MEAN_CHANNEL_DELTA
            and self.changed_pixel_fraction
            < CAMERA_ARM_MAXIMUM_CHANGED_PIXEL_FRACTION
        )
        if not isinstance(self.within_limit, bool) or (
            self.within_limit is not expected_within_limit
        ):
            raise ValueError("arm-guard within_limit must agree with frozen thresholds")

    @property
    def distance(self) -> float:
        """Frozen mean absolute BGR-channel distance for this landmark."""

        return self.mean_absolute_channel_delta

    @property
    def distance_threshold(self) -> float:
        """Exclusive per-landmark distance threshold."""

        return CAMERA_ARM_MAXIMUM_MEAN_CHANNEL_DELTA

    @property
    def changed_fraction_threshold(self) -> float:
        """Exclusive concentrated-change threshold."""

        return CAMERA_ARM_MAXIMUM_CHANGED_PIXEL_FRACTION

    @property
    def stable(self) -> bool:
        """Whether this landmark is strictly inside both frozen limits."""

        return self.within_limit


@dataclass(frozen=True, slots=True)
class CameraArmGuardResult:
    """Immutable, permanently non-authoritative arming decision."""

    guard_id: str
    guard_version: str
    disposition: CameraArmGuardDisposition
    reason: CameraArmGuardReason
    detail: str
    decision_frame_id: int
    decision_captured_monotonic_s: float
    decision_payload_sha256: str
    arm_frame_id: int
    arm_captured_monotonic_s: float
    arm_payload_sha256: str
    regions: tuple[CameraArmGuardRegionMetric, ...]
    evaluated_zones: tuple[MacroZone, ...]
    excluded_regions: tuple[tuple[int, int, int, int], ...]
    compared_pixel_count: int
    mean_absolute_channel_delta: float
    changed_pixel_fraction: float
    safe_to_retain_guidance: bool
    can_accept: bool = field(default=False, init=False)
    can_validate_scene: bool = field(default=False, init=False)
    can_expose_resources: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        for text_name, text_value in (
            ("guard_id", self.guard_id),
            ("guard_version", self.guard_version),
            ("detail", self.detail),
        ):
            if (
                not isinstance(text_value, str)
                or not text_value
                or text_value != text_value.strip()
            ):
                raise ValueError(
                    f"arm-guard {text_name} must be a non-empty trimmed string"
                )
        if not isinstance(self.disposition, CameraArmGuardDisposition):
            raise ValueError("arm-guard disposition is invalid")
        if not isinstance(self.reason, CameraArmGuardReason):
            raise ValueError("arm-guard reason is invalid")
        for frame_id_name, frame_id_value in (
            ("decision_frame_id", self.decision_frame_id),
            ("arm_frame_id", self.arm_frame_id),
        ):
            if (
                isinstance(frame_id_value, bool)
                or not isinstance(frame_id_value, int)
                or frame_id_value <= 0
            ):
                raise ValueError(
                    f"arm-guard {frame_id_name} must be a positive integer"
                )
        for timestamp_name, timestamp_value in (
            ("decision_captured_monotonic_s", self.decision_captured_monotonic_s),
            ("arm_captured_monotonic_s", self.arm_captured_monotonic_s),
        ):
            if (
                isinstance(timestamp_value, bool)
                or not isinstance(timestamp_value, (int, float))
                or not math.isfinite(timestamp_value)
                or timestamp_value < 0.0
            ):
                raise ValueError(
                    f"arm-guard {timestamp_name} must be finite and non-negative"
                )
        for digest_name, digest_value in (
            ("decision_payload_sha256", self.decision_payload_sha256),
            ("arm_payload_sha256", self.arm_payload_sha256),
        ):
            if (
                not isinstance(digest_value, str)
                or len(digest_value) != 64
                or digest_value != digest_value.lower()
                or any(
                    character not in "0123456789abcdef" for character in digest_value
                )
            ):
                raise ValueError(f"arm-guard {digest_name} must be lowercase SHA-256")
        if not isinstance(self.regions, tuple) or any(
            not isinstance(metric, CameraArmGuardRegionMetric) for metric in self.regions
        ):
            raise ValueError("arm-guard regions must be a metric tuple")
        if not isinstance(self.evaluated_zones, tuple) or any(
            not isinstance(zone, MacroZone) for zone in self.evaluated_zones
        ):
            raise ValueError("arm-guard evaluated_zones must contain MacroZone values")
        if len(set(self.evaluated_zones)) != len(self.evaluated_zones):
            raise ValueError("arm-guard evaluated_zones must be unique")
        expected_zones = tuple(
            zone for zone in MacroZone if any(metric.zone is zone for metric in self.regions)
        )
        if self.evaluated_zones != expected_zones:
            raise ValueError("arm-guard evaluated_zones must match region evidence")
        if not isinstance(self.excluded_regions, tuple) or not self.excluded_regions:
            raise ValueError("arm guard requires frozen candidate/UI exclusions")
        if any(
            not isinstance(region, tuple)
            or len(region) != 4
            or any(isinstance(value, bool) or not isinstance(value, int) for value in region)
            for region in self.excluded_regions
        ):
            raise ValueError("arm-guard exclusions must contain four-integer tuples")
        if self.excluded_regions != CAMERA_ARM_GUARD_EXCLUDED_REGIONS:
            raise ValueError("arm-guard exclusions must match the frozen policy")
        if (
            isinstance(self.compared_pixel_count, bool)
            or not isinstance(self.compared_pixel_count, int)
            or self.compared_pixel_count < 0
        ):
            raise ValueError("arm-guard compared_pixel_count must be non-negative")
        expected_count = sum(metric.compared_pixel_count for metric in self.regions)
        if self.compared_pixel_count != expected_count:
            raise ValueError("arm-guard compared count must equal region evidence")
        for metric_name, metric_value, maximum in (
            ("mean_absolute_channel_delta", self.mean_absolute_channel_delta, 255.0),
            ("changed_pixel_fraction", self.changed_pixel_fraction, 1.0),
        ):
            if (
                isinstance(metric_value, bool)
                or not isinstance(metric_value, (int, float))
                or not math.isfinite(metric_value)
                or not 0.0 <= metric_value <= maximum
            ):
                raise ValueError(f"arm-guard {metric_name} must be finite and in range")
        if self.regions:
            expected_mean = sum(
                metric.mean_absolute_channel_delta * metric.compared_pixel_count
                for metric in self.regions
            ) / expected_count
            expected_fraction = sum(
                metric.changed_pixel_fraction * metric.compared_pixel_count
                for metric in self.regions
            ) / expected_count
            if not math.isclose(
                self.mean_absolute_channel_delta, expected_mean, abs_tol=1e-12
            ):
                raise ValueError("arm-guard mean must equal weighted region evidence")
            if not math.isclose(
                self.changed_pixel_fraction, expected_fraction, abs_tol=1e-12
            ):
                raise ValueError("arm-guard changed fraction must equal region evidence")
        elif self.mean_absolute_channel_delta != 0.0 or self.changed_pixel_fraction != 0.0:
            raise ValueError("arm-guard empty evidence must use zero metrics")

        retained = self.disposition is CameraArmGuardDisposition.RETAIN
        if not isinstance(self.safe_to_retain_guidance, bool) or (
            self.safe_to_retain_guidance is not retained
        ):
            raise ValueError("arm-guard retain boolean must agree with disposition")
        if retained:
            if (
                self.arm_frame_id <= self.decision_frame_id
                or self.arm_captured_monotonic_s
                <= self.decision_captured_monotonic_s
            ):
                raise ValueError("arm-guard retain requires a strictly fresh arm frame")
            if self.reason is not CameraArmGuardReason.UNCHANGED_WORLD:
                raise ValueError("arm-guard retain requires unchanged-world reason")
            if len(self.regions) != CAMERA_ARM_REQUIRED_REGION_COUNT:
                raise ValueError("arm-guard retain requires every frozen world region")
            if self.stable_landmark_count < CAMERA_ARM_REQUIRED_STABLE_LANDMARKS:
                raise ValueError("arm-guard retain requires the stable landmark floor")
            if len(self.stable_zones) < CAMERA_ARM_REQUIRED_STABLE_ZONES:
                raise ValueError("arm-guard retain requires distributed macro zones")
            if not all(metric.within_limit for metric in self.regions):
                raise ValueError("arm-guard retain cannot ignore a material outlier")
        elif self.reason is CameraArmGuardReason.UNCHANGED_WORLD:
            raise ValueError("arm-guard discard cannot claim unchanged world")
        if (
            self.reason is CameraArmGuardReason.MATERIAL_WORLD_CHANGE
            and self.regions
            and all(metric.within_limit for metric in self.regions)
        ):
            raise ValueError("material-change discard requires an unstable landmark")
        if self.regions:
            observed_policy = tuple(
                (metric.landmark_id, metric.zone, metric.region) for metric in self.regions
            )
            if observed_policy != CAMERA_ARM_GUARD_STRUCTURAL_REGIONS:
                raise ValueError("arm-guard metrics must match frozen structural regions")

    @property
    def stable_landmark_count(self) -> int:
        """Number of frozen landmarks strictly inside both change limits."""

        return sum(metric.stable for metric in self.regions)

    @property
    def stable_zones(self) -> tuple[MacroZone, ...]:
        """Macro zones represented by stable structural evidence."""

        return tuple(
            zone
            for zone in MacroZone
            if any(metric.zone is zone and metric.stable for metric in self.regions)
        )

    @property
    def maximum_landmark_distance(self) -> float:
        """Largest recorded per-landmark colour distance."""

        return max((metric.distance for metric in self.regions), default=0.0)

    @property
    def mean_landmark_distance(self) -> float:
        """Unweighted mean of the six frozen per-landmark distances."""

        return (
            sum(metric.distance for metric in self.regions) / len(self.regions)
            if self.regions
            else 0.0
        )


def evaluate_camera_arm_guard(
    decision_frame: Frame,
    arm_frame: Frame,
) -> CameraArmGuardResult:
    """Retain pending guidance only across one fresh, unchanged world capture.

    Unsupported input and incomplete structural evidence are represented as a
    deterministic discard/restart result.  Non-``Frame`` values are programmer
    errors and are rejected before any profile or pixel work.
    """

    if not isinstance(decision_frame, Frame):
        raise TypeError("decision_frame must be Frame")
    if not isinstance(arm_frame, Frame):
        raise TypeError("arm_frame must be Frame")

    profile = load_varrock_east_iron_profile()
    observed_exclusions = tuple(
        dict.fromkeys(
            (
                *varrock_east_iron_scene_excluded_regions(profile),
                *(policy.region for policy in GAMEPLAY_CHROME_POLICIES),
            )
        )
    )
    excluded = CAMERA_ARM_GUARD_EXCLUDED_REGIONS
    if observed_exclusions != excluded:
        return _discard(
            CameraArmGuardReason.AMBIGUOUS_WORLD_EVIDENCE,
            "The packaged candidate/UI exclusions do not match the frozen arm policy.",
            excluded,
            decision_frame,
            arm_frame,
        )
    observed_landmarks = tuple(
        (
            landmark.landmark_id,
            landmark.zone(profile.frame_width, profile.frame_height),
            landmark.region,
        )
        for landmark in profile.scene_landmarks
    )
    if observed_landmarks != CAMERA_ARM_GUARD_STRUCTURAL_REGIONS or any(
        _regions_overlap(structural[2], exclusion)
        for structural in CAMERA_ARM_GUARD_STRUCTURAL_REGIONS
        for exclusion in excluded
    ):
        return _discard(
            CameraArmGuardReason.AMBIGUOUS_WORLD_EVIDENCE,
            "The packaged structural regions do not match the frozen world-only policy.",
            excluded,
            decision_frame,
            arm_frame,
        )
    if not _supported(decision_frame, profile.frame_width, profile.frame_height):
        return _discard(
            CameraArmGuardReason.UNSUPPORTED_DECISION_FRAME,
            "The guidance-decision frame is outside the exact reviewed BGRA geometry.",
            excluded,
            decision_frame,
            arm_frame,
        )
    if not _supported(arm_frame, profile.frame_width, profile.frame_height):
        return _discard(
            CameraArmGuardReason.UNSUPPORTED_ARM_FRAME,
            "The pre-input arm frame is outside the exact reviewed BGRA geometry.",
            excluded,
            decision_frame,
            arm_frame,
        )
    if (
        arm_frame.frame_id <= decision_frame.frame_id
        or arm_frame.captured_monotonic_s <= decision_frame.captured_monotonic_s
    ):
        return _discard(
            CameraArmGuardReason.NON_FRESH_ARM_FRAME,
            "The pre-input arm capture is not strictly newer; discard pending guidance.",
            excluded,
            decision_frame,
            arm_frame,
        )

    try:
        metrics = tuple(
            _measure_region(decision_frame, arm_frame, landmark, excluded)
            for landmark in profile.scene_landmarks
        )
    except ValueError:
        return _discard(
            CameraArmGuardReason.AMBIGUOUS_WORLD_EVIDENCE,
            "A frozen structural landmark has no unexcluded world evidence.",
            excluded,
            decision_frame,
            arm_frame,
        )
    zones = tuple(
        zone for zone in MacroZone if any(metric.zone is zone for metric in metrics)
    )
    sufficiently_covered = (
        len(metrics) == CAMERA_ARM_REQUIRED_REGION_COUNT
        and len(zones) >= CAMERA_ARM_REQUIRED_ZONE_COUNT
        and all(
            metric.compared_pixel_count / metric.total_pixel_count
            >= CAMERA_ARM_MINIMUM_REGION_COVERAGE
            for metric in metrics
        )
    )
    if not sufficiently_covered:
        return _discard(
            CameraArmGuardReason.AMBIGUOUS_WORLD_EVIDENCE,
            "Frozen structural evidence is incomplete or insufficiently distributed.",
            excluded,
            decision_frame,
            arm_frame,
            metrics=metrics,
        )
    # Unlike production scene recognition's 5/6 landmark quorum, this
    # false-negative-biased arming seam cannot excuse one measured material
    # outlier.  Every frozen region and all three zones must remain stable.
    stable_count = sum(metric.stable for metric in metrics)
    stable_zones = tuple(
        zone
        for zone in MacroZone
        if any(metric.zone is zone and metric.stable for metric in metrics)
    )
    if (
        stable_count < CAMERA_ARM_REQUIRED_STABLE_LANDMARKS
        or len(stable_zones) < CAMERA_ARM_REQUIRED_STABLE_ZONES
        or not all(metric.within_limit for metric in metrics)
    ):
        changed = ", ".join(
            metric.landmark_id for metric in metrics if not metric.within_limit
        )
        return _discard(
            CameraArmGuardReason.MATERIAL_WORLD_CHANGE,
            f"Structural world change detected at: {changed}; restart from the arm frame.",
            excluded,
            decision_frame,
            arm_frame,
            metrics=metrics,
        )
    return _result(
        disposition=CameraArmGuardDisposition.RETAIN,
        reason=CameraArmGuardReason.UNCHANGED_WORLD,
        detail=(
            "Every frozen structural world region remains within the stale-guidance "
            "veto limits; pending direction/sign may be retained."
        ),
        excluded_regions=excluded,
        metrics=metrics,
        decision_frame=decision_frame,
        arm_frame=arm_frame,
    )


def _supported(frame: Frame, width: int, height: int) -> bool:
    return (
        frame.width == width
        and frame.height == height
        and frame.pixel_format is PixelFormat.BGRA8888
    )


def _regions_overlap(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> bool:
    first_x, first_y, first_width, first_height = first
    second_x, second_y, second_width, second_height = second
    return (
        first_x < second_x + second_width
        and second_x < first_x + first_width
        and first_y < second_y + second_height
        and second_y < first_y + first_height
    )


def _measure_region(
    decision_frame: Frame,
    arm_frame: Frame,
    landmark: SceneLandmarkProfile,
    excluded_regions: tuple[tuple[int, int, int, int], ...],
) -> CameraArmGuardRegionMetric:
    x, y, width, height = landmark.region
    total_pixel_count = width * height
    compared_pixel_count = 0
    channel_delta_sum = 0
    changed_pixel_count = 0
    stride = decision_frame.width * 4
    for row in range(y, y + height):
        for column in range(x, x + width):
            if _point_is_excluded(column, row, excluded_regions):
                continue
            offset = row * stride + column * 4
            channel_deltas = (
                abs(decision_frame.payload[offset] - arm_frame.payload[offset]),
                abs(decision_frame.payload[offset + 1] - arm_frame.payload[offset + 1]),
                abs(decision_frame.payload[offset + 2] - arm_frame.payload[offset + 2]),
            )
            compared_pixel_count += 1
            channel_delta_sum += sum(channel_deltas)
            if max(channel_deltas) >= CAMERA_ARM_MATERIAL_CHANNEL_DELTA:
                changed_pixel_count += 1
    if compared_pixel_count <= 0:
        # Packaged v1 evidence cannot reach this branch.  Keep an explicit,
        # deterministic metric shape if a future unsafe profile is supplied.
        raise ValueError(f"arm-guard landmark {landmark.landmark_id!r} has no world pixels")
    mean_delta = channel_delta_sum / (compared_pixel_count * 3)
    changed_fraction = changed_pixel_count / compared_pixel_count
    within_limit = (
        mean_delta < CAMERA_ARM_MAXIMUM_MEAN_CHANNEL_DELTA
        and changed_fraction < CAMERA_ARM_MAXIMUM_CHANGED_PIXEL_FRACTION
    )
    return CameraArmGuardRegionMetric(
        landmark_id=landmark.landmark_id,
        zone=landmark.zone(decision_frame.width, decision_frame.height),
        region=landmark.region,
        compared_pixel_count=compared_pixel_count,
        total_pixel_count=total_pixel_count,
        mean_absolute_channel_delta=mean_delta,
        changed_pixel_fraction=changed_fraction,
        within_limit=within_limit,
    )


def _point_is_excluded(
    x: int,
    y: int,
    excluded_regions: tuple[tuple[int, int, int, int], ...],
) -> bool:
    return any(
        region_x <= x < region_x + width and region_y <= y < region_y + height
        for region_x, region_y, width, height in excluded_regions
    )


def _discard(
    reason: CameraArmGuardReason,
    detail: str,
    excluded_regions: tuple[tuple[int, int, int, int], ...],
    decision_frame: Frame,
    arm_frame: Frame,
    *,
    metrics: tuple[CameraArmGuardRegionMetric, ...] = (),
) -> CameraArmGuardResult:
    return _result(
        disposition=CameraArmGuardDisposition.DISCARD_RESTART,
        reason=reason,
        detail=detail,
        excluded_regions=excluded_regions,
        metrics=metrics,
        decision_frame=decision_frame,
        arm_frame=arm_frame,
    )


def _result(
    *,
    disposition: CameraArmGuardDisposition,
    reason: CameraArmGuardReason,
    detail: str,
    excluded_regions: tuple[tuple[int, int, int, int], ...],
    metrics: tuple[CameraArmGuardRegionMetric, ...],
    decision_frame: Frame,
    arm_frame: Frame,
) -> CameraArmGuardResult:
    compared_count = sum(metric.compared_pixel_count for metric in metrics)
    mean_delta = (
        sum(
            metric.mean_absolute_channel_delta * metric.compared_pixel_count
            for metric in metrics
        )
        / compared_count
        if compared_count
        else 0.0
    )
    changed_fraction = (
        sum(
            metric.changed_pixel_fraction * metric.compared_pixel_count
            for metric in metrics
        )
        / compared_count
        if compared_count
        else 0.0
    )
    zones = tuple(
        zone for zone in MacroZone if any(metric.zone is zone for metric in metrics)
    )
    return CameraArmGuardResult(
        guard_id=CAMERA_ARM_GUARD_ID,
        guard_version=CAMERA_ARM_GUARD_VERSION,
        disposition=disposition,
        reason=reason,
        detail=detail,
        decision_frame_id=decision_frame.frame_id,
        decision_captured_monotonic_s=decision_frame.captured_monotonic_s,
        decision_payload_sha256=hashlib.sha256(decision_frame.payload).hexdigest(),
        arm_frame_id=arm_frame.frame_id,
        arm_captured_monotonic_s=arm_frame.captured_monotonic_s,
        arm_payload_sha256=hashlib.sha256(arm_frame.payload).hexdigest(),
        regions=metrics,
        evaluated_zones=zones,
        excluded_regions=excluded_regions,
        compared_pixel_count=compared_count,
        mean_absolute_channel_delta=mean_delta,
        changed_pixel_fraction=changed_fraction,
        safe_to_retain_guidance=(disposition is CameraArmGuardDisposition.RETAIN),
    )
