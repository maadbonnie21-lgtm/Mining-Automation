"""Deterministic diagnostics for structural scene reacquisition.

The production resource detector deliberately evaluates landmarks only at
their reviewed coordinates.  This module explains failures without changing
that decision boundary: it reports a small coherent-offset search, independent
per-landmark local minima, and structural similarity to known scene frames.

Only a single coherent offset may form a scene verdict.  Independent local
minima are diagnostic evidence and are never combined into a passing view.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Final

from ..capture import Frame
from .scene_landmarks import (
    LandmarkMatch,
    MacroZone,
    SceneLandmarkProfile,
    SceneVerdict,
    SceneVerdictReason,
    describe_region,
    descriptor_distance,
    evaluate_scene,
    macro_zone_for_region,
)

__all__ = [
    "DEFAULT_DIAGNOSTIC_SEARCH_RADIUS",
    "MAXIMUM_DIAGNOSTIC_SEARCH_RADIUS",
    "LandmarkLocalSearch",
    "ReacquisitionConclusion",
    "ReacquisitionDiagnosis",
    "SceneFrameComparison",
    "SceneOffsetEvaluation",
    "SceneReacquisitionAnalysis",
    "analyze_scene_reacquisition",
    "classify_reacquisition",
    "compare_scene_frames",
]

DEFAULT_DIAGNOSTIC_SEARCH_RADIUS: Final[int] = 4
MAXIMUM_DIAGNOSTIC_SEARCH_RADIUS: Final[int] = 16
_REGION_COMPONENTS: Final[int] = 4


class ReacquisitionDiagnosis(Enum):
    """Evidence-based explanation of one attempted scene reacquisition."""

    SUPPORTED_VIEW = "supported_view"
    CAMERA_NOT_ACTUALLY_RESTORED = "camera_not_actually_restored"
    FROZEN_LANDMARKS_TOO_BRITTLE = "frozen_landmarks_too_brittle"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True, slots=True)
class SceneOffsetEvaluation:
    """Scene verdict obtained from one shared landmark offset."""

    offset_x: int
    offset_y: int
    verdict: SceneVerdict


@dataclass(frozen=True, slots=True)
class LandmarkLocalSearch:
    """Best bounded offset for one landmark, retained as diagnostics only."""

    landmark_id: str
    offset_x: int
    offset_y: int
    distance: float
    maximum_distance: float
    matched: bool
    zone: MacroZone


@dataclass(frozen=True, slots=True)
class SceneReacquisitionAnalysis:
    """Frozen, coherent, and independent evidence for one frame."""

    frozen: SceneOffsetEvaluation
    best_coherent: SceneOffsetEvaluation
    local_best: tuple[LandmarkLocalSearch, ...]
    search_radius: int

    @property
    def local_matched_count(self) -> int:
        return sum(item.matched for item in self.local_best)

    @property
    def local_matched_zones(self) -> tuple[MacroZone, ...]:
        return tuple(
            zone
            for zone in MacroZone
            if any(item.matched and item.zone is zone for item in self.local_best)
        )


@dataclass(frozen=True, slots=True)
class SceneFrameComparison:
    """Structural comparison between two frames at the frozen landmarks."""

    verdict: SceneVerdict
    normalized_distance: float


@dataclass(frozen=True, slots=True)
class ReacquisitionConclusion:
    """Typed diagnosis plus a human-readable evidence statement."""

    diagnosis: ReacquisitionDiagnosis
    detail: str


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_search_radius(search_radius: int) -> None:
    if not _is_integer(search_radius) or not (
        0 <= search_radius <= MAXIMUM_DIAGNOSTIC_SEARCH_RADIUS
    ):
        raise ValueError(
            "search_radius must be an integer between 0 and "
            f"{MAXIMUM_DIAGNOSTIC_SEARCH_RADIUS}"
        )


def _validate_region(region: tuple[int, int, int, int]) -> None:
    if (
        not isinstance(region, tuple)
        or len(region) != _REGION_COMPONENTS
        or any(not _is_integer(component) for component in region)
    ):
        raise ValueError("excluded regions must be tuples of four integers")
    x, y, width, height = region
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        raise ValueError("excluded regions must have non-negative origins and positive sizes")


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


def _validate_search_envelopes(
    landmarks: tuple[SceneLandmarkProfile, ...],
    *,
    search_radius: int,
    frame_width: int,
    frame_height: int,
    excluded_regions: tuple[tuple[int, int, int, int], ...],
) -> None:
    for excluded in excluded_regions:
        _validate_region(excluded)
        x, y, width, height = excluded
        if x + width > frame_width or y + height > frame_height:
            raise ValueError(f"excluded region {excluded} is outside the profile frame")

    for landmark in landmarks:
        x, y, width, height = landmark.region
        frozen_zone = landmark.zone(frame_width, frame_height)
        envelope = (
            x - search_radius,
            y - search_radius,
            width + 2 * search_radius,
            height + 2 * search_radius,
        )
        envelope_x, envelope_y, envelope_width, envelope_height = envelope
        if (
            envelope_x < 0
            or envelope_y < 0
            or envelope_x + envelope_width > frame_width
            or envelope_y + envelope_height > frame_height
        ):
            raise ValueError(
                f"landmark {landmark.landmark_id!r} search envelope leaves the frame"
            )
        for offset_x in (-search_radius, search_radius):
            for offset_y in (-search_radius, search_radius):
                shifted = (x + offset_x, y + offset_y, width, height)
                if macro_zone_for_region(shifted, frame_width, frame_height) is not frozen_zone:
                    raise ValueError(
                        f"landmark {landmark.landmark_id!r} search envelope crosses "
                        "a macro-zone boundary"
                    )
        for excluded in excluded_regions:
            if _regions_overlap(envelope, excluded):
                raise ValueError(
                    f"landmark {landmark.landmark_id!r} search envelope overlaps "
                    f"excluded region {excluded}"
                )


def _verdict_from_matches(
    matches: tuple[LandmarkMatch, ...], *, required_quorum: int, required_zones: int
) -> SceneVerdict:
    matched = tuple(item for item in matches if item.matched)
    matched_zones = tuple(
        zone for zone in MacroZone if any(item.zone is zone for item in matched)
    )
    if len(matched) < required_quorum:
        reason = SceneVerdictReason.INSUFFICIENT_LANDMARK_QUORUM
    elif len(matched_zones) < required_zones:
        reason = SceneVerdictReason.INSUFFICIENT_SPATIAL_SPREAD
    else:
        reason = SceneVerdictReason.VALIDATED
    return SceneVerdict(
        validated=reason is SceneVerdictReason.VALIDATED,
        reason=reason,
        matches=matches,
        matched_count=len(matched),
        required_quorum=required_quorum,
        matched_zones=matched_zones,
        required_zones=required_zones,
    )


def _evaluation_key(
    evaluation: SceneOffsetEvaluation,
    landmarks_by_id: dict[str, SceneLandmarkProfile],
) -> tuple[int, int, int, float, int, int, int]:
    normalized_distance = sum(
        match.distance / landmarks_by_id[match.landmark_id].maximum_distance
        for match in evaluation.verdict.matches
    )
    return (
        -int(evaluation.verdict.validated),
        -evaluation.verdict.matched_count,
        -len(evaluation.verdict.matched_zones),
        normalized_distance,
        abs(evaluation.offset_x) + abs(evaluation.offset_y),
        evaluation.offset_y,
        evaluation.offset_x,
    )


def analyze_scene_reacquisition(
    frame: Frame,
    landmarks: tuple[SceneLandmarkProfile, ...],
    *,
    required_quorum: int,
    required_zones: int,
    frame_width: int,
    frame_height: int,
    search_radius: int = DEFAULT_DIAGNOSTIC_SEARCH_RADIUS,
    excluded_regions: tuple[tuple[int, int, int, int], ...] = (),
) -> SceneReacquisitionAnalysis:
    """Analyze frozen and bounded offsets without changing production state.

    ``excluded_regions`` protects candidate or sanitized pixels from entering
    even the diagnostic search envelope.  The caller should pass every region
    that must remain independent of scene identity.
    """

    _validate_search_radius(search_radius)
    if not landmarks:
        raise ValueError("at least one structural landmark is required")

    frozen_verdict = evaluate_scene(
        frame,
        landmarks,
        required_quorum=required_quorum,
        required_zones=required_zones,
        frame_width=frame_width,
        frame_height=frame_height,
    )
    if frozen_verdict.reason in {
        SceneVerdictReason.MALFORMED_SCENE_EVIDENCE,
        SceneVerdictReason.NO_LANDMARKS_CONFIGURED,
    }:
        raise ValueError(f"cannot diagnose malformed scene evidence: {frozen_verdict.detail}")

    _validate_search_envelopes(
        landmarks,
        search_radius=search_radius,
        frame_width=frame_width,
        frame_height=frame_height,
        excluded_regions=excluded_regions,
    )

    distances: dict[tuple[int, int, int], float] = {}

    def distance_at(index: int, offset_x: int, offset_y: int) -> float:
        key = index, offset_x, offset_y
        cached = distances.get(key)
        if cached is not None:
            return cached
        landmark = landmarks[index]
        x, y, width, height = landmark.region
        observed = describe_region(
            frame,
            (x + offset_x, y + offset_y, width, height),
            grid=landmark.grid,
        )
        measured = descriptor_distance(observed, landmark.reference_descriptor)
        distances[key] = measured
        return measured

    evaluations: list[SceneOffsetEvaluation] = []
    for offset_y in range(-search_radius, search_radius + 1):
        for offset_x in range(-search_radius, search_radius + 1):
            matches = tuple(
                LandmarkMatch(
                    landmark_id=landmark.landmark_id,
                    matched=(measured := distance_at(index, offset_x, offset_y))
                    <= landmark.maximum_distance,
                    distance=measured,
                    zone=landmark.zone(frame_width, frame_height),
                )
                for index, landmark in enumerate(landmarks)
            )
            evaluations.append(
                SceneOffsetEvaluation(
                    offset_x=offset_x,
                    offset_y=offset_y,
                    verdict=_verdict_from_matches(
                        matches,
                        required_quorum=required_quorum,
                        required_zones=required_zones,
                    ),
                )
            )

    landmarks_by_id = {item.landmark_id: item for item in landmarks}
    best_coherent = min(
        evaluations,
        key=lambda item: _evaluation_key(item, landmarks_by_id),
    )
    local_best = tuple(
        min(
            (
                LandmarkLocalSearch(
                    landmark_id=landmark.landmark_id,
                    offset_x=offset_x,
                    offset_y=offset_y,
                    distance=distance_at(index, offset_x, offset_y),
                    maximum_distance=landmark.maximum_distance,
                    matched=(
                        distance_at(index, offset_x, offset_y)
                        <= landmark.maximum_distance
                    ),
                    zone=landmark.zone(frame_width, frame_height),
                )
                for offset_y in range(-search_radius, search_radius + 1)
                for offset_x in range(-search_radius, search_radius + 1)
            ),
            key=lambda item: (
                item.distance / item.maximum_distance,
                abs(item.offset_x) + abs(item.offset_y),
                item.offset_y,
                item.offset_x,
            ),
        )
        for index, landmark in enumerate(landmarks)
    )

    return SceneReacquisitionAnalysis(
        frozen=SceneOffsetEvaluation(0, 0, frozen_verdict),
        best_coherent=best_coherent,
        local_best=local_best,
        search_radius=search_radius,
    )


def compare_scene_frames(
    reference: Frame,
    observed: Frame,
    landmarks: tuple[SceneLandmarkProfile, ...],
    *,
    required_quorum: int,
    required_zones: int,
    frame_width: int,
    frame_height: int,
) -> SceneFrameComparison:
    """Compare two frames using the existing landmark thresholds and spread."""

    if not landmarks:
        raise ValueError("at least one structural landmark is required")
    if (
        reference.width != observed.width
        or reference.height != observed.height
        or reference.pixel_format is not observed.pixel_format
    ):
        raise ValueError("scene comparison frames must have identical geometry and format")
    if reference.width != frame_width or reference.height != frame_height:
        raise ValueError("scene comparison frames must match the profile dimensions")

    comparison_landmarks = tuple(
        replace(
            landmark,
            reference_descriptor=describe_region(
                reference,
                landmark.region,
                grid=landmark.grid,
            ),
        )
        for landmark in landmarks
    )
    verdict = evaluate_scene(
        observed,
        comparison_landmarks,
        required_quorum=required_quorum,
        required_zones=required_zones,
        frame_width=frame_width,
        frame_height=frame_height,
    )
    maximum_by_id = {item.landmark_id: item.maximum_distance for item in landmarks}
    normalized_distance = sum(
        match.distance / maximum_by_id[match.landmark_id] for match in verdict.matches
    ) / len(verdict.matches)
    return SceneFrameComparison(verdict=verdict, normalized_distance=normalized_distance)


def classify_reacquisition(
    analysis: SceneReacquisitionAnalysis,
    *,
    matching_drift: SceneFrameComparison | None = None,
    matching_drift_label: str | None = None,
    bounded_drift_false_support_count: int = 0,
    bounded_drift_set_complete: bool = True,
) -> ReacquisitionConclusion:
    """Explain a reacquisition result without changing its production verdict."""

    if (
        not _is_integer(bounded_drift_false_support_count)
        or bounded_drift_false_support_count < 0
    ):
        raise ValueError("bounded_drift_false_support_count must be non-negative")
    if not isinstance(bounded_drift_set_complete, bool):
        raise ValueError("bounded_drift_set_complete must be a boolean")

    frozen = analysis.frozen.verdict
    coherent = analysis.best_coherent
    if frozen.validated:
        return ReacquisitionConclusion(
            diagnosis=ReacquisitionDiagnosis.SUPPORTED_VIEW,
            detail=(
                "Frozen calibration coordinates validate the supported view at "
                f"{frozen.matched_count}/{len(frozen.matches)} landmarks across "
                f"{len(frozen.matched_zones)} zones."
            ),
        )

    coherent_validated = coherent.verdict.validated and bool(
        coherent.offset_x or coherent.offset_y
    )
    drift_validated = matching_drift is not None and matching_drift.verdict.validated
    if coherent_validated and drift_validated:
        label = matching_drift_label or "known drift frame"
        return ReacquisitionConclusion(
            diagnosis=ReacquisitionDiagnosis.INCONCLUSIVE,
            detail=(
                "Conflicting structural evidence: one coherent bounded calibration "
                f"offset ({coherent.offset_x:+d},{coherent.offset_y:+d}) validates, "
                f"but the frame also matches unsupported drift evidence {label!r}; "
                "neither diagnosis may safely override the production rejection."
            ),
        )

    if coherent_validated and bounded_drift_false_support_count:
        return ReacquisitionConclusion(
            diagnosis=ReacquisitionDiagnosis.INCONCLUSIVE,
            detail=(
                "A coherent bounded calibration offset validates this frame, but the "
                f"same diagnostic search also validates {bounded_drift_false_support_count} "
                "known drift frame(s); registration is not safe evidence for "
                "reacquisition."
            ),
        )

    if coherent_validated and not bounded_drift_set_complete:
        return ReacquisitionConclusion(
            diagnosis=ReacquisitionDiagnosis.INCONCLUSIVE,
            detail=(
                "A coherent bounded calibration offset validates this frame, but the "
                "known drift safety set is incomplete; absence of a false support is "
                "not sufficient evidence for a brittleness diagnosis."
            ),
        )

    if coherent_validated:
        return ReacquisitionConclusion(
            diagnosis=ReacquisitionDiagnosis.FROZEN_LANDMARKS_TOO_BRITTLE,
            detail=(
                "Frozen coordinates fail, but one coherent bounded offset "
                f"({coherent.offset_x:+d},{coherent.offset_y:+d}) validates unchanged "
                f"quorum and zone requirements at {coherent.verdict.matched_count}/"
                f"{len(coherent.verdict.matches)} landmarks; the frozen-coordinate "
                "strategy is too brittle for this small displacement."
            ),
        )

    if drift_validated:
        assert matching_drift is not None
        label = matching_drift_label or "known drift frame"
        drift_verdict = matching_drift.verdict
        return ReacquisitionConclusion(
            diagnosis=ReacquisitionDiagnosis.CAMERA_NOT_ACTUALLY_RESTORED,
            detail=(
                f"The frame matches unsupported drift evidence {label!r} at "
                f"{drift_verdict.matched_count}/{len(drift_verdict.matches)} landmarks "
                f"across {len(drift_verdict.matched_zones)} zones, while calibration "
                f"matches only {frozen.matched_count}/{len(frozen.matches)}; the camera "
                "was not actually restored."
            ),
        )

    return ReacquisitionConclusion(
        diagnosis=ReacquisitionDiagnosis.INCONCLUSIVE,
        detail=(
            f"Neither frozen coordinates nor a coherent +/-{analysis.search_radius}px "
            f"offset validates the scene; independent local search finds "
            f"{analysis.local_matched_count}/{len(analysis.local_best)} matches across "
            f"{len(analysis.local_matched_zones)} zones and is diagnostic-only."
        ),
    )
