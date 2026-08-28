"""Wide, diagnostic-only structural scene registration.

This module exists to explain a rejected supported-view candidate without
changing the production detector.  It searches each frozen structural landmark
across a larger bounded window, then asks whether the independently recovered
landmarks support one shared translation.  Independent minima are never
promoted into a production scene verdict.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

from ..capture import Frame
from .scene_landmarks import (
    MacroZone,
    SceneLandmarkProfile,
    describe_region,
    descriptor_distance,
    macro_zone_for_region,
)

__all__ = [
    "DEFAULT_WIDE_COARSE_STEP",
    "DEFAULT_WIDE_REFINEMENT_RADIUS",
    "DEFAULT_WIDE_REGISTRATION_RADIUS",
    "MAXIMUM_WIDE_REGISTRATION_RADIUS",
    "WideLandmarkSearch",
    "WideRegistrationDiagnosis",
    "WideSceneRegistrationAnalysis",
    "WideSharedOffsetEvaluation",
    "analyze_wide_scene_registration",
]

DEFAULT_WIDE_REGISTRATION_RADIUS: Final[int] = 64
MAXIMUM_WIDE_REGISTRATION_RADIUS: Final[int] = 96
DEFAULT_WIDE_COARSE_STEP: Final[int] = 4
DEFAULT_WIDE_REFINEMENT_RADIUS: Final[int] = 3
_DEFAULT_REFINEMENT_SEEDS: Final[int] = 4
_DEFAULT_TRANSFORM_EVIDENCE_LANDMARKS: Final[int] = 4
_DEFAULT_TRANSFORM_EVIDENCE_ZONES: Final[int] = 2
_REGION_COMPONENTS: Final[int] = 4
_INVALID_DISTANCE_PENALTY: Final[float] = 1_000.0


class WideRegistrationDiagnosis(Enum):
    """Diagnostic explanation for a scene rejected by the narrow gate."""

    LARGER_COHERENT_TRANSLATION = "larger_coherent_translation"
    CAMERA_TRANSFORM_NOT_TRANSLATION = "camera_transform_not_translation"
    INSUFFICIENT_REGISTRATION_EVIDENCE = "insufficient_registration_evidence"


@dataclass(frozen=True, slots=True)
class WideLandmarkSearch:
    """Best valid wide-search location for one frozen landmark."""

    landmark_id: str
    offset_x: int
    offset_y: int
    distance: float
    maximum_distance: float
    matched: bool
    zone: MacroZone
    searched_offsets: int

    @property
    def normalized_distance(self) -> float:
        return self.distance / self.maximum_distance


@dataclass(frozen=True, slots=True)
class WideSharedOffsetEvaluation:
    """Evidence produced by applying one shared offset to every landmark."""

    offset_x: int
    offset_y: int
    matched_count: int
    matched_zones: tuple[MacroZone, ...]
    required_quorum: int
    required_zones: int
    valid_landmark_count: int
    normalized_distance_sum: float

    @property
    def validated(self) -> bool:
        return (
            self.matched_count >= self.required_quorum
            and len(self.matched_zones) >= self.required_zones
        )


@dataclass(frozen=True, slots=True)
class WideSceneRegistrationAnalysis:
    """Wide local-search evidence and its safest shared-offset interpretation."""

    landmarks: tuple[WideLandmarkSearch, ...]
    best_shared: WideSharedOffsetEvaluation | None
    diagnosis: WideRegistrationDiagnosis
    detail: str
    search_radius: int
    coarse_step: int
    refinement_radius: int

    @property
    def matched_count(self) -> int:
        return sum(item.matched for item in self.landmarks)

    @property
    def matched_zones(self) -> tuple[MacroZone, ...]:
        return tuple(
            zone
            for zone in MacroZone
            if any(item.matched and item.zone is zone for item in self.landmarks)
        )


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_positive_int(name: str, value: int) -> None:
    if not _is_integer(value) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _validate_configuration(
    *,
    search_radius: int,
    coarse_step: int,
    refinement_radius: int,
    required_quorum: int,
    required_zones: int,
    landmark_count: int,
) -> None:
    if not _is_integer(search_radius) or not (
        1 <= search_radius <= MAXIMUM_WIDE_REGISTRATION_RADIUS
    ):
        raise ValueError(
            "search_radius must be an integer between 1 and "
            f"{MAXIMUM_WIDE_REGISTRATION_RADIUS}"
        )
    _validate_positive_int("coarse_step", coarse_step)
    if coarse_step > search_radius:
        raise ValueError("coarse_step must not exceed search_radius")
    if not _is_integer(refinement_radius) or refinement_radius < 0:
        raise ValueError("refinement_radius must be a non-negative integer")
    _validate_positive_int("required_quorum", required_quorum)
    _validate_positive_int("required_zones", required_zones)
    if required_quorum > landmark_count:
        raise ValueError("required_quorum cannot exceed landmark count")
    if required_zones > len(MacroZone):
        raise ValueError("required_zones cannot exceed macro-zone count")


def _validate_region(
    region: tuple[int, int, int, int],
    *,
    frame_width: int,
    frame_height: int,
) -> None:
    if (
        not isinstance(region, tuple)
        or len(region) != _REGION_COMPONENTS
        or any(not _is_integer(component) for component in region)
    ):
        raise ValueError("excluded regions must be tuples of four integers")
    x, y, width, height = region
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        raise ValueError("excluded regions require non-negative origins and positive sizes")
    if x + width > frame_width or y + height > frame_height:
        raise ValueError(f"excluded region {region} is outside the profile frame")


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


def _shifted_region(
    landmark: SceneLandmarkProfile,
    *,
    offset_x: int,
    offset_y: int,
    frame_width: int,
    frame_height: int,
    excluded_regions: tuple[tuple[int, int, int, int], ...],
) -> tuple[int, int, int, int] | None:
    x, y, width, height = landmark.region
    shifted = (x + offset_x, y + offset_y, width, height)
    shifted_x, shifted_y, shifted_width, shifted_height = shifted
    if (
        shifted_x < 0
        or shifted_y < 0
        or shifted_x + shifted_width > frame_width
        or shifted_y + shifted_height > frame_height
    ):
        return None
    if macro_zone_for_region(shifted, frame_width, frame_height) is not landmark.zone(
        frame_width, frame_height
    ):
        return None
    if any(_regions_overlap(shifted, excluded) for excluded in excluded_regions):
        return None
    return shifted


def _axis_offsets(radius: int, step: int) -> tuple[int, ...]:
    values = set(range(-radius, radius + 1, step))
    values.update((-radius, 0, radius))
    return tuple(sorted(values))


def _local_key(
    distance: float,
    maximum_distance: float,
    offset_x: int,
    offset_y: int,
) -> tuple[float, int, int, int]:
    return (
        distance / maximum_distance,
        abs(offset_x) + abs(offset_y),
        offset_y,
        offset_x,
    )


def _shared_key(
    value: WideSharedOffsetEvaluation,
) -> tuple[int, int, int, float, int, int, int]:
    return (
        -int(value.validated),
        -value.matched_count,
        -len(value.matched_zones),
        value.normalized_distance_sum,
        abs(value.offset_x) + abs(value.offset_y),
        value.offset_y,
        value.offset_x,
    )


def analyze_wide_scene_registration(
    frame: Frame,
    landmarks: tuple[SceneLandmarkProfile, ...],
    *,
    required_quorum: int,
    required_zones: int,
    frame_width: int,
    frame_height: int,
    search_radius: int = DEFAULT_WIDE_REGISTRATION_RADIUS,
    coarse_step: int = DEFAULT_WIDE_COARSE_STEP,
    refinement_radius: int = DEFAULT_WIDE_REFINEMENT_RADIUS,
    excluded_regions: tuple[tuple[int, int, int, int], ...] = (),
) -> WideSceneRegistrationAnalysis:
    """Search wide landmark offsets and diagnose translation versus transform.

    The result is diagnostic-only.  A validated ``best_shared`` value proves
    that the existing landmark thresholds/quorum/zones are satisfied at one
    larger shared displacement; it does not alter production state.
    """

    if not isinstance(frame, Frame):
        raise TypeError("frame must be Frame")
    if not landmarks:
        raise ValueError("at least one structural landmark is required")
    if frame.width != frame_width or frame.height != frame_height:
        raise ValueError("wide registration frame must match profile dimensions")
    _validate_configuration(
        search_radius=search_radius,
        coarse_step=coarse_step,
        refinement_radius=refinement_radius,
        required_quorum=required_quorum,
        required_zones=required_zones,
        landmark_count=len(landmarks),
    )
    for excluded in excluded_regions:
        _validate_region(
            excluded,
            frame_width=frame_width,
            frame_height=frame_height,
        )
    for landmark in landmarks:
        for excluded in excluded_regions:
            if _regions_overlap(landmark.region, excluded):
                raise ValueError(
                    f"landmark {landmark.landmark_id!r} frozen region overlaps "
                    f"excluded region {excluded}"
                )

    distance_cache: dict[tuple[int, int, int], float | None] = {}

    def distance_at(index: int, offset_x: int, offset_y: int) -> float | None:
        key = (index, offset_x, offset_y)
        if key in distance_cache:
            return distance_cache[key]
        landmark = landmarks[index]
        region = _shifted_region(
            landmark,
            offset_x=offset_x,
            offset_y=offset_y,
            frame_width=frame_width,
            frame_height=frame_height,
            excluded_regions=excluded_regions,
        )
        if region is None:
            distance_cache[key] = None
            return None
        distance = descriptor_distance(
            describe_region(frame, region, grid=landmark.grid),
            landmark.reference_descriptor,
        )
        distance_cache[key] = distance
        return distance

    coarse_axis = _axis_offsets(search_radius, coarse_step)
    searches: list[WideLandmarkSearch] = []
    for index, landmark in enumerate(landmarks):
        coarse_candidates: list[tuple[float, int, int]] = []
        for offset_y in coarse_axis:
            for offset_x in coarse_axis:
                distance = distance_at(index, offset_x, offset_y)
                if distance is not None:
                    coarse_candidates.append((distance, offset_x, offset_y))
        if not coarse_candidates:
            raise ValueError(
                f"landmark {landmark.landmark_id!r} has no valid wide-search positions"
            )
        coarse_candidates.sort(
            key=lambda item: _local_key(
                item[0], landmark.maximum_distance, item[1], item[2]
            )
        )
        seed_candidates = coarse_candidates[:_DEFAULT_REFINEMENT_SEEDS]
        refine_offsets: set[tuple[int, int]] = {
            (offset_x, offset_y)
            for _, offset_x, offset_y in coarse_candidates
        }
        for _, seed_x, seed_y in seed_candidates:
            for delta_y in range(-refinement_radius, refinement_radius + 1):
                for delta_x in range(-refinement_radius, refinement_radius + 1):
                    offset_x = seed_x + delta_x
                    offset_y = seed_y + delta_y
                    if (
                        -search_radius <= offset_x <= search_radius
                        and -search_radius <= offset_y <= search_radius
                    ):
                        refine_offsets.add((offset_x, offset_y))

        evaluated: list[tuple[float, int, int]] = []
        for offset_x, offset_y in sorted(refine_offsets, key=lambda item: (item[1], item[0])):
            distance = distance_at(index, offset_x, offset_y)
            if distance is not None:
                evaluated.append((distance, offset_x, offset_y))
        best_distance, best_x, best_y = min(
            evaluated,
            key=lambda item: _local_key(
                item[0], landmark.maximum_distance, item[1], item[2]
            ),
        )
        searches.append(
            WideLandmarkSearch(
                landmark_id=landmark.landmark_id,
                offset_x=best_x,
                offset_y=best_y,
                distance=best_distance,
                maximum_distance=landmark.maximum_distance,
                matched=best_distance <= landmark.maximum_distance,
                zone=landmark.zone(frame_width, frame_height),
                searched_offsets=len(evaluated),
            )
        )

    matched_searches = tuple(item for item in searches if item.matched)
    candidate_centres: set[tuple[int, int]] = {
        (item.offset_x, item.offset_y) for item in matched_searches
    }
    shared_candidates: list[WideSharedOffsetEvaluation] = []

    for centre_x, centre_y in sorted(candidate_centres, key=lambda item: (item[1], item[0])):
        for delta_y in range(-refinement_radius, refinement_radius + 1):
            for delta_x in range(-refinement_radius, refinement_radius + 1):
                offset_x = centre_x + delta_x
                offset_y = centre_y + delta_y
                if not (
                    -search_radius <= offset_x <= search_radius
                    and -search_radius <= offset_y <= search_radius
                ):
                    continue
                matches = 0
                valid_count = 0
                normalized_sum = 0.0
                shared_matched_zones: set[MacroZone] = set()
                for index, landmark in enumerate(landmarks):
                    distance = distance_at(index, offset_x, offset_y)
                    if distance is None:
                        normalized_sum += _INVALID_DISTANCE_PENALTY
                        continue
                    valid_count += 1
                    normalized_sum += distance / landmark.maximum_distance
                    if distance <= landmark.maximum_distance:
                        matches += 1
                        shared_matched_zones.add(
                            landmark.zone(frame_width, frame_height)
                        )
                shared_candidates.append(
                    WideSharedOffsetEvaluation(
                        offset_x=offset_x,
                        offset_y=offset_y,
                        matched_count=matches,
                        matched_zones=tuple(
                            zone for zone in MacroZone if zone in shared_matched_zones
                        ),
                        required_quorum=required_quorum,
                        required_zones=required_zones,
                        valid_landmark_count=valid_count,
                        normalized_distance_sum=normalized_sum,
                    )
                )

    best_shared = min(shared_candidates, key=_shared_key) if shared_candidates else None
    matched_zones = tuple(
        zone
        for zone in MacroZone
        if any(item.matched and item.zone is zone for item in searches)
    )

    if best_shared is not None and best_shared.validated:
        diagnosis = WideRegistrationDiagnosis.LARGER_COHERENT_TRANSLATION
        detail = (
            "Wide search recovers one shared displacement "
            f"({best_shared.offset_x:+d},{best_shared.offset_y:+d}) that preserves the "
            f"existing {required_quorum}-of-{len(landmarks)} landmark quorum across "
            f"{len(best_shared.matched_zones)} zones; production remains unchanged."
        )
    elif (
        len(matched_searches) >= min(_DEFAULT_TRANSFORM_EVIDENCE_LANDMARKS, len(landmarks))
        and len(matched_zones) >= _DEFAULT_TRANSFORM_EVIDENCE_ZONES
    ):
        diagnosis = WideRegistrationDiagnosis.CAMERA_TRANSFORM_NOT_TRANSLATION
        offsets = ", ".join(
            f"{item.landmark_id}=({item.offset_x:+d},{item.offset_y:+d})"
            for item in matched_searches
        )
        detail = (
            f"{len(matched_searches)}/{len(landmarks)} landmarks are individually "
            f"recoverable across {len(matched_zones)} zones, but no single shared "
            f"offset satisfies the existing quorum/zone gate. Best offsets diverge: {offsets}."
        )
    else:
        diagnosis = WideRegistrationDiagnosis.INSUFFICIENT_REGISTRATION_EVIDENCE
        detail = (
            f"Only {len(matched_searches)}/{len(landmarks)} landmarks are recoverable "
            f"within +/-{search_radius}px across {len(matched_zones)} zones; there is "
            "not enough distributed evidence to infer a safe translation or a "
            "non-translation camera transform."
        )

    return WideSceneRegistrationAnalysis(
        landmarks=tuple(searches),
        best_shared=best_shared,
        diagnosis=diagnosis,
        detail=detail,
        search_radius=search_radius,
        coarse_step=coarse_step,
        refinement_radius=refinement_radius,
    )
