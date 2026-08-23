"""Structural scene landmarks.

Issue #18. Replaces mean-RGB terrain anchors as the *gating* scene-validation
mechanism.

A single mean RGB over a patch of grass is both too weak and too strong. Too
weak, because grass looks like grass everywhere in the scene, so one patch
matching proves almost nothing about *this* camera view. Too strong, because
under the Issue #13 per-anchor floor a single patch holds veto power, so one
lighting shift, one bit of foliage, or one player standing on it blocks the
whole scene with nothing able to outvote it -- fail-closed hard enough that a
genuinely restored camera never reacquires.

The replacement changes both halves:

* **Structure, not colour.** A landmark is described by the *relative*
  luminance pattern across a grid of sub-cells, mean-centred and normalised.
  That encodes internal structure -- edges, boundaries, contrast layout -- and
  is invariant to a uniform brightness shift. A flat terrain patch has almost
  no pattern to match, and is rejected at calibration time rather than silently
  used (:data:`MINIMUM_STRUCTURAL_VARIANCE`).

* **Distributed quorum, not per-anchor veto.** The scene validates when enough
  landmarks match *and* the matching ones span enough distinct macro zones. One
  strong local or repeated-terrain match cannot carry the scene; one obstructed
  landmark cannot sink it.

Pure Python over the same ``memoryview`` access the rest of the perception
package uses. No numpy, no OpenCV: the project ships ``dependencies = []`` and
a 4x4 reduction over six small regions does not justify changing that.
"""

from __future__ import annotations

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
    "describe_region",
    "descriptor_distance",
    "evaluate_scene",
    "macro_zone_for_region",
    "structural_variance",
]

#: Sub-cells per axis. 4x4 = 16 cells over a 48x48 landmark gives 12x12 pixel
#: cells: large enough to average out per-pixel noise, small enough to retain
#: the edge layout that makes a landmark distinguishable from flat terrain.
DEFAULT_DESCRIPTOR_GRID: Final[int] = 4

#: Minimum spread of cell luminances (in 0-255 units) for a landmark to be
#: considered discriminative at calibration time. Enforces "no generic
#: grass/dirt patches" mechanically instead of by convention -- all four legacy
#: v2 anchors fall below this.
MINIMUM_STRUCTURAL_VARIANCE: Final[float] = 8.0

_LUMA_R: Final[float] = 0.299
_LUMA_G: Final[float] = 0.587
_LUMA_B: Final[float] = 0.114


class MacroZone(Enum):
    """Coarse spatial region of the frame.

    Deliberately coarse. The point is only to prove that surviving evidence is
    *spread out*, so a cluster of matches in one corner cannot validate a
    scene. Four zones is the smallest split that makes "at least 3 zones" a
    meaningful distribution requirement.
    """

    NORTH_WEST = "north_west"
    NORTH_EAST = "north_east"
    SOUTH_WEST = "south_west"
    SOUTH_EAST = "south_east"


class SceneVerdictReason(Enum):
    """Why scene validation reached its conclusion.

    Every failure is a distinct, specific reason so recovery policy and
    diagnostics can branch on cause rather than parse a message.
    """

    VALIDATED = "scene_validated"
    INSUFFICIENT_LANDMARK_QUORUM = "insufficient_landmark_quorum"
    INSUFFICIENT_SPATIAL_SPREAD = "insufficient_spatial_spread"
    MALFORMED_SCENE_EVIDENCE = "malformed_scene_evidence"
    NO_LANDMARKS_CONFIGURED = "no_landmarks_configured"


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


def describe_region(
    frame: Frame,
    region: tuple[int, int, int, int],
    *,
    grid: int = DEFAULT_DESCRIPTOR_GRID,
) -> tuple[float, ...]:
    """Structural descriptor of ``region``: normalised relative cell luminances.

    The region is split into ``grid x grid`` equal cells. Each cell's mean
    luminance is computed, the set is mean-centred, then divided by its largest
    absolute deviation. The result describes *how luminance is arranged* inside
    the region, independent of overall brightness -- so a uniform lighting
    change leaves it unchanged while a different piece of scenery does not.

    A perfectly flat region yields all zeros, which
    :func:`structural_variance` is used to reject before it can be calibrated.

    Raises:
        ValueError: the grid is not positive, the region does not divide evenly
            into it, or the region does not fit inside the frame.
    """
    if grid < 1:
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
                        payload, row_offset + column * pixel_stride, frame.pixel_format
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
    """Spread of raw cell luminances, in 0-255 units.

    Measures how much internal structure a region actually has, *before*
    normalisation flattens the scale away. Used at calibration time to reject
    featureless terrain that would normalise into a meaningless descriptor.
    """
    if grid < 1:
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
                        payload, row_offset + column * pixel_stride, frame.pixel_format
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
    """Mean absolute difference between two descriptors.

    Raises:
        ValueError: the descriptors have different lengths, which means they
            were produced with different grids and are not comparable.
    """
    if len(left) != len(right):
        raise ValueError(
            f"descriptor length mismatch: {len(left)} vs {len(right)}; "
            "descriptors from different grids are not comparable"
        )
    if not left:
        raise ValueError("descriptors must not be empty")
    return sum(abs(a - b) for a, b in zip(left, right, strict=True)) / len(left)


def macro_zone_for_region(
    region: tuple[int, int, int, int], frame_width: int, frame_height: int
) -> MacroZone:
    """Which macro zone a region's centre falls in."""
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
    """One calibrated structural landmark.

    ``reference_descriptor`` is frozen into the versioned profile at
    calibration time, so the same frame and the same profile always produce the
    same verdict.
    """

    landmark_id: str
    region: tuple[int, int, int, int]
    reference_descriptor: tuple[float, ...]
    maximum_distance: float
    grid: int = DEFAULT_DESCRIPTOR_GRID

    def __post_init__(self) -> None:
        if not isinstance(self.landmark_id, str) or not self.landmark_id.strip():
            raise ValueError("landmark_id must be a non-empty string")
        if self.grid < 1:
            raise ValueError("landmark grid must be a positive integer")
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
        if any(
            not isinstance(value, (int, float)) or isinstance(value, bool)
            for value in self.reference_descriptor
        ):
            raise ValueError(
                f"landmark {self.landmark_id!r} descriptor must be real numbers"
            )
        if not 0.0 < self.maximum_distance <= 2.0:
            raise ValueError(
                f"landmark {self.landmark_id!r} maximum_distance must be in (0.0, 2.0]"
            )

    def zone(self, frame_width: int, frame_height: int) -> MacroZone:
        return macro_zone_for_region(self.region, frame_width, frame_height)


@dataclass(frozen=True, slots=True)
class LandmarkMatch:
    """Per-landmark outcome, retained as evidence whether or not it matched."""

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
        """Human-readable summary for diagnostics and evidence."""
        zones = ",".join(zone.value for zone in self.matched_zones)
        return (
            f"{self.reason.value}: {self.matched_count}/{len(self.matches)} landmarks "
            f"(quorum {self.required_quorum}) across {len(self.matched_zones)} zones "
            f"(required {self.required_zones})"
            + (f" [{zones}]" if zones else "")
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
    """Validate the scene from spatially distributed structural evidence.

    The scene is validated only when **both** conditions hold:

    * at least ``required_quorum`` landmarks match their frozen reference, and
    * the matching landmarks span at least ``required_zones`` macro zones.

    The zone requirement is what stops a cluster of matches in one corner --
    or one strong repeated-terrain match -- from carrying the whole scene. The
    quorum is what lets a single obstructed or altered landmark degrade safely
    instead of vetoing a view that distributed evidence still proves.

    Any malformed landmark (region off-frame, undivisible geometry, descriptor
    length mismatch) fails the whole scene closed with
    :attr:`SceneVerdictReason.MALFORMED_SCENE_EVIDENCE` rather than being
    skipped -- silently ignoring broken evidence would shrink the quorum
    denominator and make validation easier, which is backwards.
    """
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

    matches: list[LandmarkMatch] = []
    for landmark in landmarks:
        try:
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
                zone=landmark.zone(frame_width, frame_height),
            )
        )

    matched = [match for match in matches if match.matched]
    matched_count = len(matched)
    # Ordered by enum definition, not by iteration order, so the evidence is
    # byte-identical for the same frame and profile on every run.
    zone_order = list(MacroZone)
    matched_zones = tuple(
        zone for zone in zone_order if any(match.zone is zone for match in matched)
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


def _validate_region_shape(region: tuple[int, int, int, int]) -> None:
    if (
        not isinstance(region, tuple)
        or len(region) != 4
        or any(
            not isinstance(component, int) or isinstance(component, bool)
            for component in region
        )
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
    x, y, width, height = region
    if x + width > frame_width or y + height > frame_height:
        raise ValueError(
            f"region {region} does not fit inside frame {frame_width}x{frame_height}"
        )
    return region
