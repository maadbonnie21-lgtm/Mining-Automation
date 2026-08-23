"""Structural scene landmarks and calibration guards.

Issue #18 replaces generic mean-RGB terrain anchors as the gating scene-
validation mechanism with spatially distributed structural landmarks.

Landmarks describe the relative luminance structure inside a small frame-local
region. Runtime validation requires both a landmark quorum and spatial spread.
Calibration is deliberately stricter than runtime: featureless regions,
candidate overlap, and caller-supplied excluded/sanitized regions are rejected
before a descriptor can be frozen into a profile.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Final

from ..capture import Frame, PixelFormat

__all__ = [
    "DEFAULT_DESCRIPTOR_GRID",
    "MINIMUM_STRUCTURAL_VARIANCE",
    "LandmarkMatch",
    "MacroZone",
    "SceneLandmarkProfile",
    "SceneVerdict",
    "SceneVerdictReason",
    "calibrate_scene_landmark",
    "describe_region",
    "descriptor_distance",
    "evaluate_scene",
    "macro_zone_for_region",
    "structural_variance",
]

DEFAULT_DESCRIPTOR_GRID: Final[int] = 4
MINIMUM_STRUCTURAL_VARIANCE: Final[float] = 8.0

_LUMA_R: Final[float] = 0.299
_LUMA_G: Final[float] = 0.587
_LUMA_B: Final[float] = 0.114
_REGION_COMPONENTS: Final[int] = 4


class MacroZone(Enum):
    """Coarse spatial region used to require distributed scene evidence."""

    NORTH_WEST = "north_west"
    NORTH_EAST = "north_east"
    SOUTH_WEST = "south_west"
    SOUTH_EAST = "south_east"


class SceneVerdictReason(Enum):
    """Typed reason for a scene-validation decision."""

    VALIDATED = "scene_validated"
    INSUFFICIENT_LANDMARK_QUORUM = "insufficient_landmark_quorum"
    INSUFFICIENT_SPATIAL_SPREAD = "insufficient_spatial_spread"
    MALFORMED_SCENE_EVIDENCE = "malformed_scene_evidence"
    NO_LANDMARKS_CONFIGURED = "no_landmarks_configured"


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _luma(red: int, green: int, blue: int) -> float:
    return _LUMA_R * red + _LUMA_G * green + _LUMA_B * blue


def _read_rgb(
    payload: memoryview, offset: int, pixel_format: PixelFormat
) -> tuple[int, int, int]:
    if pixel_format is PixelFormat.RGB888 or pixel_format is PixelFormat.RGBA8888:
        return int(payload[offset]), int(payload[offset + 1]), int(payload[offset + 2])
    if pixel_format is PixelFormat.BGR888 or pixel_format is PixelFormat.BGRA8888:
        return int(payload[offset + 2]), int(payload[offset + 1]), int(payload[offset])
    if pixel_format is PixelFormat.GRAY8:
        value = int(payload[offset])
        return value, value, value
    raise ValueError(f"unsupported pixel format: {pixel_format}")


def _validate_region_shape(region: tuple[int, int, int, int]) -> None:
    if (
        not isinstance(region, tuple)
        or len(region) != _REGION_COMPONENTS
        or any(not _is_integer(component) for component in region)
    ):
        raise ValueError("region must be a tuple of four integers")
    x, y, width, height = region
    if x < 0 or y < 0:
        raise ValueError("region origin must be non-negative and frame-local")
    if width <= 0 or height <= 0:
        raise ValueError("region width and height must be positive")


def _validated_region(
    region: tuple[int, int, int, int], frame_width: int, frame_height: int
) -> tuple[int, int, int, int]:
    _validate_region_shape(region)
    if not _is_integer(frame_width) or frame_width <= 0:
        raise ValueError("frame_width must be a positive integer")
    if not _is_integer(frame_height) or frame_height <= 0:
        raise ValueError("frame_height must be a positive integer")
    x, y, width, height = region
    if x + width > frame_width or y + height > frame_height:
        raise ValueError(
            f"region {region} does not fit inside frame {frame_width}x{frame_height}"
        )
    return region


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


def _region_with_margin(
    region: tuple[int, int, int, int],
    margin: int,
    *,
    frame_width: int,
    frame_height: int,
) -> tuple[int, int, int, int]:
    _validated_region(region, frame_width, frame_height)
    if not _is_integer(margin) or margin < 0:
        raise ValueError("candidate_margin must be a non-negative integer")
    x, y, width, height = region
    left = max(0, x - margin)
    top = max(0, y - margin)
    right = min(frame_width, x + width + margin)
    bottom = min(frame_height, y + height + margin)
    return left, top, right - left, bottom - top


def describe_region(
    frame: Frame,
    region: tuple[int, int, int, int],
    *,
    grid: int = DEFAULT_DESCRIPTOR_GRID,
) -> tuple[float, ...]:
    """Return a normalized grid-luminance structural descriptor."""

    if not _is_integer(grid) or grid < 1:
        raise ValueError("descriptor grid must be a positive integer")
    x, y, width, height = _validated_region(region, frame.width, frame.height)
    if width % grid or height % grid:
        raise ValueError(
            f"landmark region {width}x{height} must divide evenly by grid {grid}"
        )

    cell_width = width // grid
    cell_height = height // grid
    payload = memoryview(frame.payload)
    row_stride = frame.width * frame.pixel_format.bytes_per_pixel
    pixel_stride = frame.pixel_format.bytes_per_pixel

    cells: list[float] = []
    for cell_row in range(grid):
        for cell_column in range(grid):
            total = 0.0
            count = 0
            base_y = y + cell_row * cell_height
            base_x = x + cell_column * cell_width
            for row in range(base_y, base_y + cell_height):
                row_offset = row * row_stride
                for column in range(base_x, base_x + cell_width):
                    red, green, blue = _read_rgb(
                        payload,
                        row_offset + column * pixel_stride,
                        frame.pixel_format,
                    )
                    total += _luma(red, green, blue)
                    count += 1
            cells.append(total / count)

    mean = sum(cells) / len(cells)
    centred = [value - mean for value in cells]
    scale = max(abs(value) for value in centred)
    if scale <= 1e-9:
        return tuple(0.0 for _ in centred)
    return tuple(value / scale for value in centred)


def structural_variance(
    frame: Frame,
    region: tuple[int, int, int, int],
    *,
    grid: int = DEFAULT_DESCRIPTOR_GRID,
) -> float:
    """Return standard deviation of raw grid-cell luminances in 0..255 units."""

    if not _is_integer(grid) or grid < 1:
        raise ValueError("descriptor grid must be a positive integer")
    x, y, width, height = _validated_region(region, frame.width, frame.height)
    if width % grid or height % grid:
        raise ValueError(
            f"landmark region {width}x{height} must divide evenly by grid {grid}"
        )

    cell_width = width // grid
    cell_height = height // grid
    payload = memoryview(frame.payload)
    row_stride = frame.width * frame.pixel_format.bytes_per_pixel
    pixel_stride = frame.pixel_format.bytes_per_pixel

    cells: list[float] = []
    for cell_row in range(grid):
        for cell_column in range(grid):
            total = 0.0
            count = 0
            base_y = y + cell_row * cell_height
            base_x = x + cell_column * cell_width
            for row in range(base_y, base_y + cell_height):
                row_offset = row * row_stride
                for column in range(base_x, base_x + cell_width):
                    red, green, blue = _read_rgb(
                        payload,
                        row_offset + column * pixel_stride,
                        frame.pixel_format,
                    )
                    total += _luma(red, green, blue)
                    count += 1
            cells.append(total / count)

    mean = sum(cells) / len(cells)
    variance = sum((value - mean) ** 2 for value in cells) / len(cells)
    return float(variance**0.5)


def descriptor_distance(
    left: tuple[float, ...], right: tuple[float, ...]
) -> float:
    """Return mean absolute difference between comparable descriptors."""

    if len(left) != len(right):
        raise ValueError(
            f"descriptor length mismatch: {len(left)} vs {len(right)}; "
            "descriptors from different grids are not comparable"
        )
    if not left:
        raise ValueError("descriptors must not be empty")
    if any(not _is_finite_number(value) for value in (*left, *right)):
        raise ValueError("descriptors must contain only finite real numbers")
    return sum(abs(a - b) for a, b in zip(left, right, strict=True)) / len(left)


def macro_zone_for_region(
    region: tuple[int, int, int, int], frame_width: int, frame_height: int
) -> MacroZone:
    """Return the macro zone containing the region center."""

    _validated_region(region, frame_width, frame_height)
    x, y, width, height = region
    centre_x = x + width / 2
    centre_y = y + height / 2
    northern = centre_y < frame_height / 2
    western = centre_x < frame_width / 2
    if northern:
        return MacroZone.NORTH_WEST if western else MacroZone.NORTH_EAST
    return MacroZone.SOUTH_WEST if western else MacroZone.SOUTH_EAST


@dataclass(frozen=True, slots=True)
class SceneLandmarkProfile:
    """One calibrated structural landmark with frozen spatial identity."""

    landmark_id: str
    region: tuple[int, int, int, int]
    reference_descriptor: tuple[float, ...]
    maximum_distance: float
    grid: int = DEFAULT_DESCRIPTOR_GRID
    macro_zone: MacroZone = MacroZone.NORTH_WEST

    def __post_init__(self) -> None:
        if not isinstance(self.landmark_id, str) or not self.landmark_id.strip():
            raise ValueError("landmark_id must be a non-empty string")
        if not _is_integer(self.grid) or self.grid < 1:
            raise ValueError("landmark grid must be a positive integer")
        if not isinstance(self.macro_zone, MacroZone):
            raise ValueError("landmark macro_zone must be MacroZone")
        _validate_region_shape(self.region)
        _, _, width, height = self.region
        if width % self.grid or height % self.grid:
            raise ValueError(
                f"landmark {self.landmark_id!r} region {width}x{height} "
                f"must divide evenly by grid {self.grid}"
            )
        expected_cells = self.grid * self.grid
        if len(self.reference_descriptor) != expected_cells:
            raise ValueError(
                f"landmark {self.landmark_id!r} descriptor has "
                f"{len(self.reference_descriptor)} values, expected {expected_cells}"
            )
        if any(not _is_finite_number(value) for value in self.reference_descriptor):
            raise ValueError(
                f"landmark {self.landmark_id!r} descriptor must contain finite real numbers"
            )
        if (
            not _is_finite_number(self.maximum_distance)
            or not 0.0 < float(self.maximum_distance) <= 2.0
        ):
            raise ValueError(
                f"landmark {self.landmark_id!r} maximum_distance must be finite "
                "and in (0.0, 2.0]"
            )

    def zone(self, frame_width: int, frame_height: int) -> MacroZone:
        """Return the frozen zone, rejecting geometry/zone drift in configuration."""

        derived = macro_zone_for_region(self.region, frame_width, frame_height)
        if derived is not self.macro_zone:
            raise ValueError(
                f"landmark {self.landmark_id!r} zone {self.macro_zone.value!r} "
                f"does not match region-derived zone {derived.value!r}"
            )
        return self.macro_zone


@dataclass(frozen=True, slots=True)
class LandmarkMatch:
    """Per-landmark runtime outcome retained as deterministic evidence."""

    landmark_id: str
    matched: bool
    distance: float
    zone: MacroZone


@dataclass(frozen=True, slots=True)
class SceneVerdict:
    """Result of distributed scene validation."""

    validated: bool
    reason: SceneVerdictReason
    matches: tuple[LandmarkMatch, ...]
    matched_count: int
    required_quorum: int
    matched_zones: tuple[MacroZone, ...]
    required_zones: int

    @property
    def detail(self) -> str:
        zones = ",".join(zone.value for zone in self.matched_zones)
        return (
            f"{self.reason.value}: {self.matched_count}/{len(self.matches)} landmarks "
            f"(quorum {self.required_quorum}) across {len(self.matched_zones)} zones "
            f"(required {self.required_zones})"
            + (f" [{zones}]" if zones else "")
        )


def calibrate_scene_landmark(
    frame: Frame,
    *,
    landmark_id: str,
    region: tuple[int, int, int, int],
    macro_zone: MacroZone,
    maximum_distance: float,
    grid: int = DEFAULT_DESCRIPTOR_GRID,
    minimum_structural_variance: float = MINIMUM_STRUCTURAL_VARIANCE,
    excluded_regions: tuple[tuple[int, int, int, int], ...] = (),
    candidate_regions: tuple[tuple[int, int, int, int], ...] = (),
    candidate_margin: int = 0,
) -> SceneLandmarkProfile:
    """Create a frozen landmark only after calibration-time safety checks.

    ``excluded_regions`` is intended for privacy-sanitized fixture masks and
    other coordinates that are stable in a fixture but not stable world pixels
    on a live client. ``candidate_regions`` plus ``candidate_margin`` prevents
    resource-state pixels from leaking into scene identity.
    """

    _validated_region(region, frame.width, frame.height)
    if not isinstance(macro_zone, MacroZone):
        raise ValueError("macro_zone must be MacroZone")
    derived_zone = macro_zone_for_region(region, frame.width, frame.height)
    if derived_zone is not macro_zone:
        raise ValueError(
            f"supplied macro_zone {macro_zone.value!r} does not match "
            f"region-derived zone {derived_zone.value!r}"
        )
    if (
        not _is_finite_number(minimum_structural_variance)
        or float(minimum_structural_variance) <= 0.0
    ):
        raise ValueError("minimum_structural_variance must be finite and positive")
    if not _is_integer(candidate_margin) or candidate_margin < 0:
        raise ValueError("candidate_margin must be a non-negative integer")

    for excluded in excluded_regions:
        _validated_region(excluded, frame.width, frame.height)
        if _regions_overlap(region, excluded):
            raise ValueError(
                f"landmark {landmark_id!r} overlaps an excluded/sanitized region"
            )

    for candidate in candidate_regions:
        expanded = _region_with_margin(
            candidate,
            candidate_margin,
            frame_width=frame.width,
            frame_height=frame.height,
        )
        if _regions_overlap(region, expanded):
            raise ValueError(
                f"landmark {landmark_id!r} overlaps a candidate region or its margin"
            )

    variance = structural_variance(frame, region, grid=grid)
    if variance < float(minimum_structural_variance):
        raise ValueError(
            f"landmark {landmark_id!r} structural variance {variance:.6f} is below "
            f"minimum {float(minimum_structural_variance):.6f}"
        )

    return SceneLandmarkProfile(
        landmark_id=landmark_id,
        region=region,
        reference_descriptor=describe_region(frame, region, grid=grid),
        maximum_distance=maximum_distance,
        grid=grid,
        macro_zone=macro_zone,
    )


def evaluate_scene(
    frame: Frame,
    landmarks: tuple[SceneLandmarkProfile, ...],
    *,
    required_quorum: int,
    required_zones: int,
    frame_width: int,
    frame_height: int,
) -> SceneVerdict:
    """Validate a scene using landmark quorum plus frozen-zone spatial spread."""

    if not _is_integer(required_quorum) or required_quorum < 1:
        raise ValueError("required_quorum must be a positive integer")
    if not _is_integer(required_zones) or not 1 <= required_zones <= len(MacroZone):
        raise ValueError(
            f"required_zones must be an integer between 1 and {len(MacroZone)}"
        )
    if not _is_integer(frame_width) or frame_width <= 0:
        raise ValueError("frame_width must be a positive integer")
    if not _is_integer(frame_height) or frame_height <= 0:
        raise ValueError("frame_height must be a positive integer")
    if frame.width != frame_width or frame.height != frame_height:
        raise ValueError(
            "evaluate_scene frame dimensions must match the supplied profile dimensions"
        )
    if not landmarks:
        return SceneVerdict(
            validated=False,
            reason=SceneVerdictReason.NO_LANDMARKS_CONFIGURED,
            matches=(),
            matched_count=0,
            required_quorum=required_quorum,
            matched_zones=(),
            required_zones=required_zones,
        )
    if required_quorum > len(landmarks):
        raise ValueError("required_quorum must not exceed the landmark count")

    matches: list[LandmarkMatch] = []
    for landmark in landmarks:
        if not isinstance(landmark, SceneLandmarkProfile):
            return SceneVerdict(
                validated=False,
                reason=SceneVerdictReason.MALFORMED_SCENE_EVIDENCE,
                matches=tuple(matches),
                matched_count=0,
                required_quorum=required_quorum,
                matched_zones=(),
                required_zones=required_zones,
            )
        try:
            zone = landmark.zone(frame_width, frame_height)
            observed = describe_region(frame, landmark.region, grid=landmark.grid)
            distance = descriptor_distance(observed, landmark.reference_descriptor)
        except ValueError:
            return SceneVerdict(
                validated=False,
                reason=SceneVerdictReason.MALFORMED_SCENE_EVIDENCE,
                matches=tuple(matches),
                matched_count=0,
                required_quorum=required_quorum,
                matched_zones=(),
                required_zones=required_zones,
            )
        matches.append(
            LandmarkMatch(
                landmark_id=landmark.landmark_id,
                matched=distance <= landmark.maximum_distance,
                distance=distance,
                zone=zone,
            )
        )

    matched = [match for match in matches if match.matched]
    matched_count = len(matched)
    matched_zones = tuple(
        zone for zone in MacroZone if any(match.zone is zone for match in matched)
    )

    if matched_count < required_quorum:
        reason = SceneVerdictReason.INSUFFICIENT_LANDMARK_QUORUM
    elif len(matched_zones) < required_zones:
        reason = SceneVerdictReason.INSUFFICIENT_SPATIAL_SPREAD
    else:
        reason = SceneVerdictReason.VALIDATED

    return SceneVerdict(
        validated=reason is SceneVerdictReason.VALIDATED,
        reason=reason,
        matches=tuple(matches),
        matched_count=matched_count,
        required_quorum=required_quorum,
        matched_zones=matched_zones,
        required_zones=required_zones,
    )
