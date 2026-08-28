"""Fail-closed production evaluation for Varrock East camera validation.

The evaluator intentionally has one input: an owned :class:`Frame`.  It loads
the packaged, reviewed production profile, runs the packaged detector through
the detector contract, and independently records the frozen-coordinate scene
verdict.  Development diagnostics and registration results are neither inputs
nor fallbacks, so they cannot turn an unsupported view into a production pass.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..capture import Frame, PixelFormat
from ..contracts import Observation
from ..perception.detector import run_detector
from ..perception.production_profiles import build_varrock_east_iron_detector
from ..perception.resource import (
    RESOURCE_PROFILE_SCHEMA_VERSION,
    ResourceVisualState,
    RockCandidateProfile,
    observation_kind_for_state,
)
from ..perception.scene_landmarks import MacroZone, evaluate_scene

__all__ = [
    "ISSUE31_REQUIRED_LANDMARK_COUNT",
    "ISSUE31_REQUIRED_LANDMARK_MATCHES",
    "ISSUE31_REQUIRED_MATCHED_ZONES",
    "CameraEvaluation",
    "CameraLandmarkEvaluation",
    "CameraResourceEvaluation",
    "evaluate_varrock_east_camera",
]

ISSUE31_REQUIRED_LANDMARK_COUNT = 6
ISSUE31_REQUIRED_LANDMARK_MATCHES = 5
ISSUE31_REQUIRED_MATCHED_ZONES = 3


@dataclass(frozen=True, slots=True)
class CameraLandmarkEvaluation:
    """One frozen production landmark measurement."""

    landmark_id: str
    distance: float | None
    threshold: float
    matched: bool
    zone: MacroZone


@dataclass(frozen=True, slots=True)
class CameraResourceEvaluation:
    """One production resource observation in packaged candidate order."""

    resource_id: str
    state: ResourceVisualState
    confidence: float

    @property
    def definitive(self) -> bool:
        """Whether production classified this target without uncertainty."""

        return self.state is not ResourceVisualState.UNCERTAIN


@dataclass(frozen=True, slots=True)
class CameraEvaluation:
    """Immutable evidence for one production camera-gate decision."""

    detector_id: str
    detector_version: str
    profile_id: str
    profile_schema_version: int
    profile_frame_width: int
    profile_frame_height: int
    profile_pixel_format: PixelFormat
    frame_geometry_supported: bool
    landmarks: tuple[CameraLandmarkEvaluation, ...]
    matched_landmark_count: int
    required_landmark_count: int
    required_landmark_matches: int
    matched_zones: tuple[MacroZone, ...]
    required_matched_zones: int
    scene_reason: str
    scene_validated: bool
    resource_states: tuple[CameraResourceEvaluation, ...]
    definitive_target_ids: tuple[str, ...]
    passed: bool


def evaluate_varrock_east_camera(frame: Frame, /) -> CameraEvaluation:
    """Evaluate one frame against only the packaged production decision path.

    ``passed`` is deliberately stricter than merely receiving detector output.
    The frozen production scene must validate with at least five of exactly six
    landmarks across at least three macro zones, and every profiled resource
    must receive an available/depleted (never uncertain) observation.
    """

    detector = build_varrock_east_iron_detector()
    profile = detector.profile
    metadata = detector.metadata
    observations = run_detector(detector, frame, expected_metadata=metadata)
    resources = _ordered_resource_evaluations(observations, profile.candidates)

    geometry_supported = (
        frame.width == profile.frame_width
        and frame.height == profile.frame_height
        and frame.pixel_format is profile.pixel_format
    )
    if geometry_supported:
        verdict = evaluate_scene(
            frame,
            profile.scene_landmarks,
            required_quorum=profile.minimum_landmark_quorum,
            required_zones=profile.minimum_landmark_zones,
            frame_width=profile.frame_width,
            frame_height=profile.frame_height,
        )
        profile_landmarks = {
            landmark.landmark_id: landmark for landmark in profile.scene_landmarks
        }
        landmark_results = tuple(
            CameraLandmarkEvaluation(
                landmark_id=match.landmark_id,
                distance=match.distance,
                threshold=profile_landmarks[match.landmark_id].maximum_distance,
                matched=match.matched,
                zone=match.zone,
            )
            for match in verdict.matches
        )
        matched_count = verdict.matched_count
        matched_zones = verdict.matched_zones
        scene_reason = verdict.reason.value
        scene_validated = verdict.validated
    else:
        landmark_results = tuple(
            CameraLandmarkEvaluation(
                landmark_id=landmark.landmark_id,
                distance=None,
                threshold=landmark.maximum_distance,
                matched=False,
                zone=landmark.macro_zone,
            )
            for landmark in profile.scene_landmarks
        )
        matched_count = 0
        matched_zones = ()
        scene_reason = "frame_geometry_mismatch"
        scene_validated = False

    definitive_target_ids = tuple(
        resource.resource_id for resource in resources if resource.definitive
    )
    all_resources_definitive = (
        len(resources) == len(profile.candidates)
        and all(resource.definitive for resource in resources)
    )
    passed = (
        geometry_supported
        and scene_validated
        and len(landmark_results) == ISSUE31_REQUIRED_LANDMARK_COUNT
        and matched_count >= ISSUE31_REQUIRED_LANDMARK_MATCHES
        and len(matched_zones) >= ISSUE31_REQUIRED_MATCHED_ZONES
        and all_resources_definitive
    )

    return CameraEvaluation(
        detector_id=metadata.detector_id,
        detector_version=metadata.version,
        profile_id=profile.profile_id,
        profile_schema_version=RESOURCE_PROFILE_SCHEMA_VERSION,
        profile_frame_width=profile.frame_width,
        profile_frame_height=profile.frame_height,
        profile_pixel_format=profile.pixel_format,
        frame_geometry_supported=geometry_supported,
        landmarks=landmark_results,
        matched_landmark_count=matched_count,
        required_landmark_count=ISSUE31_REQUIRED_LANDMARK_COUNT,
        required_landmark_matches=ISSUE31_REQUIRED_LANDMARK_MATCHES,
        matched_zones=matched_zones,
        required_matched_zones=ISSUE31_REQUIRED_MATCHED_ZONES,
        scene_reason=scene_reason,
        scene_validated=scene_validated,
        resource_states=resources,
        definitive_target_ids=definitive_target_ids,
        passed=passed,
    )


def _ordered_resource_evaluations(
    observations: tuple[Observation, ...],
    candidates: tuple[RockCandidateProfile, ...],
) -> tuple[CameraResourceEvaluation, ...]:
    """Extract strict detector states in packaged candidate order."""

    typed_observations: dict[str, Observation] = {}
    for observation in observations:
        resource_id = observation.evidence.get("resource_id")
        raw_state = observation.evidence.get("state")
        if not isinstance(resource_id, str) or not resource_id:
            raise ValueError("production resource observation has no resource_id")
        if resource_id in typed_observations:
            raise ValueError(f"duplicate production resource observation: {resource_id!r}")
        if not isinstance(raw_state, str):
            raise ValueError(f"resource {resource_id!r} has no typed visual state")
        try:
            state = ResourceVisualState(raw_state)
        except ValueError as exc:
            raise ValueError(
                f"resource {resource_id!r} has unsupported visual state {raw_state!r}"
            ) from exc
        if observation.kind != observation_kind_for_state(state):
            raise ValueError(f"resource {resource_id!r} observation kind/state disagree")
        typed_observations[resource_id] = observation

    results: list[CameraResourceEvaluation] = []
    for candidate in candidates:
        if candidate.resource_id not in typed_observations:
            raise ValueError(
                f"production detector omitted resource {candidate.resource_id!r}"
            )
        observation = typed_observations.pop(candidate.resource_id)
        results.append(
            CameraResourceEvaluation(
                resource_id=candidate.resource_id,
                state=ResourceVisualState(str(observation.evidence["state"])),
                confidence=observation.confidence,
            )
        )
    if typed_observations:
        raise ValueError(
            "production detector returned unexpected resources: "
            + ", ".join(sorted(typed_observations))
        )
    return tuple(results)
