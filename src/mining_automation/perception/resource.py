"""Profile-driven resource perception and shared-state adaptation.

The first production detector is deliberately narrow: one reviewed site profile
represents one supported mine, ore, client geometry, and camera envelope. The
profile contains frame-local scene evidence and candidate rock regions. It never
contains desktop coordinates and it never guesses when the frame does not match
that supported envelope.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from ..capture import Frame, PixelFormat
from ..contracts import Observation, ResourceState
from .detector import DetectorMetadata
from .scene_landmarks import MacroZone, SceneLandmarkProfile, evaluate_scene

__all__ = [
    "RESOURCE_OBSERVATION_PREFIX",
    "RESOURCE_PROFILE_SCHEMA_VERSION",
    "ColorSignature",
    "ProfiledResourceDetector",
    "ResourceDetectorProfile",
    "ResourceVisualState",
    "RockCandidateProfile",
    "SceneAnchorProfile",
    "load_resource_detector_profile",
    "measure_region_mean_rgb",
    "observation_kind_for_state",
    "resource_state_from_observation",
    "resource_states_from_observations",
    "save_resource_detector_profile",
]

RESOURCE_OBSERVATION_PREFIX: Final[str] = "resource."
RESOURCE_PROFILE_SCHEMA_VERSION: Final[int] = 3
_LEGACY_RESOURCE_PROFILE_SCHEMA_VERSION: Final[int] = 2
_MACRO_ZONE_COUNT: Final[int] = len(MacroZone)
_REGION_COMPONENTS: Final[int] = 4
_MAX_RGB_DISTANCE: Final[float] = math.sqrt(3.0 * 255.0 * 255.0)


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _validate_unit_interval(value: float, field_name: str) -> None:
    if not _is_finite_number(value) or not 0.0 <= float(value) <= 1.0:
        raise ValueError(f"{field_name} must be finite and between 0 and 1")


def _validate_region(
    region: tuple[int, int, int, int],
    *,
    frame_width: int | None = None,
    frame_height: int | None = None,
) -> None:
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
    if frame_width is not None and x + width > frame_width:
        raise ValueError("region must fit inside the profile frame width")
    if frame_height is not None and y + height > frame_height:
        raise ValueError("region must fit inside the profile frame height")


def _regions_overlap(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> bool:
    """Whether two frame-local rectangles have positive-area intersection."""

    first_x, first_y, first_width, first_height = first
    second_x, second_y, second_width, second_height = second
    return (
        first_x < second_x + second_width
        and second_x < first_x + first_width
        and first_y < second_y + second_height
        and second_y < first_y + first_height
    )


class ResourceVisualState(StrEnum):
    """Visually verified state of one profiled resource target."""

    AVAILABLE = "available"
    DEPLETED = "depleted"
    UNCERTAIN = "uncertain"


def observation_kind_for_state(state: ResourceVisualState) -> str:
    """Return the stable, replay-testable observation kind for ``state``."""

    if not isinstance(state, ResourceVisualState):
        raise TypeError("state must be ResourceVisualState")
    return f"{RESOURCE_OBSERVATION_PREFIX}{state.value}"


@dataclass(frozen=True, slots=True)
class ColorSignature:
    """Mean RGB prototype with a maximum useful Euclidean distance."""

    mean_rgb: tuple[float, float, float]
    max_distance: float

    def __post_init__(self) -> None:
        if (
            not isinstance(self.mean_rgb, tuple)
            or len(self.mean_rgb) != 3
            or any(
                not _is_finite_number(channel) or not 0.0 <= float(channel) <= 255.0
                for channel in self.mean_rgb
            )
        ):
            raise ValueError("mean_rgb must contain three finite channels between 0 and 255")
        if (
            not _is_finite_number(self.max_distance)
            or not 0.0 < float(self.max_distance) <= _MAX_RGB_DISTANCE
        ):
            raise ValueError("max_distance must be finite and within the RGB distance range")

    def similarity(self, actual_rgb: tuple[float, float, float]) -> float:
        distance = math.dist(self.mean_rgb, actual_rgb)
        return max(0.0, min(1.0, 1.0 - distance / float(self.max_distance)))


@dataclass(frozen=True, slots=True)
class SceneAnchorProfile:
    """A frame-local mean-RGB patch used by legacy schema-v2 scene gating."""

    anchor_id: str
    region: tuple[int, int, int, int]
    signature: ColorSignature
    weight: float = 1.0

    def __post_init__(self) -> None:
        if not isinstance(self.anchor_id, str) or not self.anchor_id.strip():
            raise ValueError("anchor_id must be a non-empty string")
        _validate_region(self.region)
        if not isinstance(self.signature, ColorSignature):
            raise ValueError("anchor signature must be ColorSignature")
        if not _is_finite_number(self.weight) or float(self.weight) <= 0.0:
            raise ValueError("anchor weight must be finite and positive")


@dataclass(frozen=True, slots=True)
class RockCandidateProfile:
    """One known rock position and its available/depleted colour prototypes."""

    resource_id: str
    region: tuple[int, int, int, int]
    available_signature: ColorSignature
    depleted_signature: ColorSignature
    minimum_similarity: float = 0.55
    minimum_margin: float = 0.12
    occlusion_grid_columns: int = 1
    occlusion_grid_rows: int = 1
    minimum_occlusion_agreement: float = 1.0

    def __post_init__(self) -> None:
        if not isinstance(self.resource_id, str) or not self.resource_id.strip():
            raise ValueError("resource_id must be a non-empty string")
        _validate_region(self.region)
        if not isinstance(self.available_signature, ColorSignature):
            raise ValueError("available_signature must be ColorSignature")
        if not isinstance(self.depleted_signature, ColorSignature):
            raise ValueError("depleted_signature must be ColorSignature")
        _validate_unit_interval(self.minimum_similarity, "minimum_similarity")
        _validate_unit_interval(self.minimum_margin, "minimum_margin")
        if not _is_integer(self.occlusion_grid_columns) or self.occlusion_grid_columns < 1:
            raise ValueError("occlusion_grid_columns must be a positive integer")
        if not _is_integer(self.occlusion_grid_rows) or self.occlusion_grid_rows < 1:
            raise ValueError("occlusion_grid_rows must be a positive integer")
        _validate_unit_interval(self.minimum_occlusion_agreement, "minimum_occlusion_agreement")
        if self.minimum_occlusion_agreement <= 0.0:
            raise ValueError("minimum_occlusion_agreement must be greater than 0.0")
        _, _, width, height = self.region
        if width % self.occlusion_grid_columns != 0:
            raise ValueError(
                f"region width {width} must divide evenly by "
                f"occlusion_grid_columns {self.occlusion_grid_columns}"
            )
        if height % self.occlusion_grid_rows != 0:
            raise ValueError(
                f"region height {height} must divide evenly by "
                f"occlusion_grid_rows {self.occlusion_grid_rows}"
            )

    @property
    def occlusion_cell_count(self) -> int:
        return self.occlusion_grid_columns * self.occlusion_grid_rows

    def occlusion_cell_regions(self) -> tuple[tuple[int, int, int, int], ...]:
        x, y, width, height = self.region
        cell_width = width // self.occlusion_grid_columns
        cell_height = height // self.occlusion_grid_rows
        return tuple(
            (x + column * cell_width, y + row * cell_height, cell_width, cell_height)
            for row in range(self.occlusion_grid_rows)
            for column in range(self.occlusion_grid_columns)
        )


@dataclass(frozen=True, slots=True)
class ResourceDetectorProfile:
    """Reviewed support envelope for one mine/ore/camera configuration.

    Profiles without ``scene_landmarks`` use the exact schema-v2 anchor gating
    semantics. Migrated schema-v3 profiles use structural landmarks as the
    gating scene evidence while retaining legacy anchor measurements only for
    diagnostics.
    """

    profile_id: str
    location_id: str
    ore_label: str
    frame_width: int
    frame_height: int
    pixel_format: PixelFormat
    anchors: tuple[SceneAnchorProfile, ...]
    candidates: tuple[RockCandidateProfile, ...]
    minimum_scene_confidence: float = 0.7
    minimum_anchor_confidence: float = 0.0
    sample_step: int = 2
    scene_landmarks: tuple[SceneLandmarkProfile, ...] = ()
    minimum_landmark_quorum: int = 0
    minimum_landmark_zones: int = 0

    def __post_init__(self) -> None:
        for field_name, value in (
            ("profile_id", self.profile_id),
            ("location_id", self.location_id),
            ("ore_label", self.ore_label),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if not _is_integer(self.frame_width) or self.frame_width <= 0:
            raise ValueError("frame_width must be a positive integer")
        if not _is_integer(self.frame_height) or self.frame_height <= 0:
            raise ValueError("frame_height must be a positive integer")
        if not isinstance(self.pixel_format, PixelFormat):
            raise ValueError("pixel_format must be PixelFormat")
        if (
            not isinstance(self.anchors, tuple)
            or not self.anchors
            or any(not isinstance(anchor, SceneAnchorProfile) for anchor in self.anchors)
        ):
            raise ValueError("anchors must be a non-empty tuple of SceneAnchorProfile values")
        if (
            not isinstance(self.candidates, tuple)
            or not self.candidates
            or any(not isinstance(candidate, RockCandidateProfile) for candidate in self.candidates)
        ):
            raise ValueError("candidates must be a non-empty tuple of RockCandidateProfile values")
        if not isinstance(self.scene_landmarks, tuple) or any(
            not isinstance(landmark, SceneLandmarkProfile) for landmark in self.scene_landmarks
        ):
            raise ValueError("scene_landmarks must be a tuple of SceneLandmarkProfile values")
        _validate_unit_interval(self.minimum_scene_confidence, "minimum_scene_confidence")
        _validate_unit_interval(self.minimum_anchor_confidence, "minimum_anchor_confidence")
        if not _is_integer(self.sample_step) or self.sample_step <= 0:
            raise ValueError("sample_step must be a positive integer")

        anchor_ids = [anchor.anchor_id for anchor in self.anchors]
        if len(set(anchor_ids)) != len(anchor_ids):
            raise ValueError("anchor ids must be unique")
        resource_ids = [candidate.resource_id for candidate in self.candidates]
        if len(set(resource_ids)) != len(resource_ids):
            raise ValueError("candidate resource ids must be unique")
        for anchor in self.anchors:
            _validate_region(
                anchor.region,
                frame_width=self.frame_width,
                frame_height=self.frame_height,
            )
        for candidate in self.candidates:
            _validate_region(
                candidate.region,
                frame_width=self.frame_width,
                frame_height=self.frame_height,
            )

        for first_index, first_candidate in enumerate(self.candidates):
            for second_candidate in self.candidates[first_index + 1 :]:
                if _regions_overlap(first_candidate.region, second_candidate.region):
                    raise ValueError(
                        "candidate regions must not overlap: "
                        f"{first_candidate.resource_id!r} and "
                        f"{second_candidate.resource_id!r}"
                    )
            for anchor in self.anchors:
                if _regions_overlap(first_candidate.region, anchor.region):
                    raise ValueError(
                        "a candidate region must not overlap a scene anchor: "
                        f"{first_candidate.resource_id!r} and anchor {anchor.anchor_id!r}"
                    )

        if self.scene_landmarks:
            landmark_ids = [item.landmark_id for item in self.scene_landmarks]
            if len(set(landmark_ids)) != len(landmark_ids):
                raise ValueError("scene landmark ids must be unique")
            if (
                not _is_integer(self.minimum_landmark_quorum)
                or not 1 <= self.minimum_landmark_quorum <= len(self.scene_landmarks)
            ):
                raise ValueError(
                    "minimum_landmark_quorum must be between 1 and the landmark count"
                )
            if (
                not _is_integer(self.minimum_landmark_zones)
                or not 1 <= self.minimum_landmark_zones <= _MACRO_ZONE_COUNT
            ):
                raise ValueError(
                    f"minimum_landmark_zones must be between 1 and {_MACRO_ZONE_COUNT}"
                )
            available_zones = {
                landmark.zone(self.frame_width, self.frame_height)
                for landmark in self.scene_landmarks
            }
            if len(available_zones) < self.minimum_landmark_zones:
                raise ValueError(
                    f"landmarks span only {len(available_zones)} macro zones but "
                    f"minimum_landmark_zones is {self.minimum_landmark_zones}; the "
                    "spatial-spread requirement could never be satisfied"
                )
            for landmark in self.scene_landmarks:
                _validate_region(
                    landmark.region,
                    frame_width=self.frame_width,
                    frame_height=self.frame_height,
                )
                for candidate in self.candidates:
                    if _regions_overlap(landmark.region, candidate.region):
                        raise ValueError(
                            "a scene landmark must not overlap a candidate region: "
                            f"{landmark.landmark_id!r} and {candidate.resource_id!r}"
                        )
        elif self.minimum_landmark_quorum or self.minimum_landmark_zones:
            raise ValueError(
                "landmark quorum/zone requirements set without any scene_landmarks"
            )


class ProfiledResourceDetector:
    """Classify known rock candidates only after verifying the profiled scene."""

    def __init__(self, profile: ResourceDetectorProfile, *, version: str) -> None:
        if not isinstance(profile, ResourceDetectorProfile):
            raise TypeError("profile must be ResourceDetectorProfile")
        if not isinstance(version, str) or not version.strip():
            raise ValueError("version must be a non-empty string")
        self._profile = profile
        self._metadata = DetectorMetadata(
            detector_id=f"profiled-resource:{profile.profile_id}",
            version=version,
        )

    @property
    def metadata(self) -> DetectorMetadata:
        return self._metadata

    @property
    def profile(self) -> ResourceDetectorProfile:
        return self._profile

    def detect(self, frame: Frame) -> tuple[Observation, ...]:
        mismatch = self._frame_mismatch_reason(frame)
        if mismatch is not None:
            return tuple(
                self._uncertain_observation(
                    frame,
                    candidate,
                    reason=mismatch,
                    scene_confidence=0.0,
                    region_is_valid=False,
                )
                for candidate in self._profile.candidates
            )

        anchor_evidence: dict[str, float] = {}
        weighted_total = 0.0
        total_weight = 0.0
        for anchor in self._profile.anchors:
            mean_rgb = _mean_rgb(frame, anchor.region, sample_step=self._profile.sample_step)
            confidence = anchor.signature.similarity(mean_rgb)
            anchor_evidence[anchor.anchor_id] = confidence
            weighted_total += confidence * float(anchor.weight)
            total_weight += float(anchor.weight)
        legacy_scene_confidence = weighted_total / total_weight if total_weight else 0.0

        if self._profile.scene_landmarks:
            verdict = evaluate_scene(
                frame,
                self._profile.scene_landmarks,
                required_quorum=self._profile.minimum_landmark_quorum,
                required_zones=self._profile.minimum_landmark_zones,
                frame_width=self._profile.frame_width,
                frame_height=self._profile.frame_height,
            )
            landmark_evidence = {
                match.landmark_id: round(match.distance, 6) for match in verdict.matches
            }
            scene_confidence = verdict.matched_count / len(verdict.matches)
            if not verdict.validated:
                return tuple(
                    self._uncertain_observation(
                        frame,
                        candidate,
                        reason=verdict.detail,
                        scene_confidence=scene_confidence,
                        region_is_valid=True,
                        anchor_confidences=anchor_evidence,
                        landmark_distances=landmark_evidence,
                    )
                    for candidate in self._profile.candidates
                )
            return tuple(
                self._classify_candidate(
                    frame,
                    candidate,
                    scene_confidence=scene_confidence,
                    anchor_confidences=anchor_evidence,
                    landmark_distances=landmark_evidence,
                )
                for candidate in self._profile.candidates
            )

        # Exact schema-v2 semantics: every legacy anchor must clear its floor,
        # then the weighted scene average must clear minimum_scene_confidence.
        scene_confidence = legacy_scene_confidence
        weakest_anchor_id = ""
        weakest_anchor_confidence = 1.0
        for anchor_id, confidence in anchor_evidence.items():
            if confidence < weakest_anchor_confidence:
                weakest_anchor_confidence = confidence
                weakest_anchor_id = anchor_id

        if weakest_anchor_confidence < self._profile.minimum_anchor_confidence:
            return tuple(
                self._uncertain_observation(
                    frame,
                    candidate,
                    reason=(
                        f"anchor_confidence_below_floor: {weakest_anchor_id!r} "
                        f"at {weakest_anchor_confidence:.6f}, "
                        f"floor {self._profile.minimum_anchor_confidence:.6f}"
                    ),
                    scene_confidence=scene_confidence,
                    region_is_valid=True,
                    anchor_confidences=anchor_evidence,
                )
                for candidate in self._profile.candidates
            )

        if scene_confidence < self._profile.minimum_scene_confidence:
            return tuple(
                self._uncertain_observation(
                    frame,
                    candidate,
                    reason="scene_not_recognized",
                    scene_confidence=scene_confidence,
                    region_is_valid=True,
                    anchor_confidences=anchor_evidence,
                )
                for candidate in self._profile.candidates
            )

        return tuple(
            self._classify_candidate(
                frame,
                candidate,
                scene_confidence=scene_confidence,
                anchor_confidences=anchor_evidence,
            )
            for candidate in self._profile.candidates
        )

    def _frame_mismatch_reason(self, frame: Frame) -> str | None:
        if frame.width != self._profile.frame_width or frame.height != self._profile.frame_height:
            return (
                "frame_geometry_mismatch: "
                f"expected {self._profile.frame_width}x{self._profile.frame_height}, "
                f"got {frame.width}x{frame.height}"
            )
        if frame.pixel_format is not self._profile.pixel_format:
            return (
                "frame_pixel_format_mismatch: "
                f"expected {self._profile.pixel_format.value}, got {frame.pixel_format.value}"
            )
        return None

    def _classify_candidate(
        self,
        frame: Frame,
        candidate: RockCandidateProfile,
        *,
        scene_confidence: float,
        anchor_confidences: dict[str, float],
        landmark_distances: dict[str, float] | None = None,
    ) -> Observation:
        if candidate.occlusion_cell_count == 1:
            return self._classify_whole_region(
                frame,
                candidate,
                scene_confidence=scene_confidence,
                anchor_confidences=anchor_confidences,
                landmark_distances=landmark_distances,
            )
        return self._classify_with_occlusion_grid(
            frame,
            candidate,
            scene_confidence=scene_confidence,
            anchor_confidences=anchor_confidences,
            landmark_distances=landmark_distances,
        )

    def _classify_whole_region(
        self,
        frame: Frame,
        candidate: RockCandidateProfile,
        *,
        scene_confidence: float,
        anchor_confidences: dict[str, float],
        landmark_distances: dict[str, float] | None = None,
    ) -> Observation:
        mean_rgb = _mean_rgb(
            frame,
            candidate.region,
            sample_step=self._profile.sample_step,
        )
        available_similarity = candidate.available_signature.similarity(mean_rgb)
        depleted_similarity = candidate.depleted_signature.similarity(mean_rgb)
        best_similarity = max(available_similarity, depleted_similarity)
        margin = abs(available_similarity - depleted_similarity)

        if best_similarity < candidate.minimum_similarity or margin < candidate.minimum_margin:
            state = ResourceVisualState.UNCERTAIN
            confidence = min(
                scene_confidence,
                max(0.0, 1.0 - best_similarity + (candidate.minimum_margin - margin)),
            )
            reason = "candidate_colour_ambiguous"
        elif available_similarity > depleted_similarity:
            state = ResourceVisualState.AVAILABLE
            confidence = min(scene_confidence, best_similarity)
            reason = "available_signature_matched"
        else:
            state = ResourceVisualState.DEPLETED
            confidence = min(scene_confidence, best_similarity)
            reason = "depleted_signature_matched"

        evidence: dict[str, object] = {
            "label": self._profile.ore_label,
            "location_id": self._profile.location_id,
            "profile_id": self._profile.profile_id,
            "resource_id": candidate.resource_id,
            "state": state.value,
            "region": candidate.region,
            "mean_rgb": tuple(round(channel, 3) for channel in mean_rgb),
            "available_similarity": round(available_similarity, 6),
            "depleted_similarity": round(depleted_similarity, 6),
            "scene_confidence": round(scene_confidence, 6),
            "anchor_confidences": dict(sorted(anchor_confidences.items())),
            "reason": reason,
        }
        if landmark_distances is not None:
            evidence["landmark_distances"] = dict(sorted(landmark_distances.items()))
        return Observation(
            kind=observation_kind_for_state(state),
            frame=frame.ref,
            confidence=max(0.0, min(1.0, confidence)),
            evidence=evidence,
            detector_version=self._metadata.version,
        )

    def _classify_with_occlusion_grid(
        self,
        frame: Frame,
        candidate: RockCandidateProfile,
        *,
        scene_confidence: float,
        anchor_confidences: dict[str, float],
        landmark_distances: dict[str, float] | None = None,
    ) -> Observation:
        whole_mean_rgb = _mean_rgb(
            frame, candidate.region, sample_step=self._profile.sample_step
        )
        whole_available_similarity = candidate.available_signature.similarity(whole_mean_rgb)
        whole_depleted_similarity = candidate.depleted_signature.similarity(whole_mean_rgb)

        cell_states: list[ResourceVisualState] = []
        cell_winning_similarities: list[float] = []
        for cell_region in candidate.occlusion_cell_regions():
            cell_mean_rgb = _mean_rgb(
                frame, cell_region, sample_step=self._profile.sample_step
            )
            cell_available = candidate.available_signature.similarity(cell_mean_rgb)
            cell_depleted = candidate.depleted_signature.similarity(cell_mean_rgb)
            cell_best = max(cell_available, cell_depleted)
            cell_margin = abs(cell_available - cell_depleted)
            if cell_best < candidate.minimum_similarity or cell_margin < candidate.minimum_margin:
                cell_states.append(ResourceVisualState.UNCERTAIN)
                cell_winning_similarities.append(cell_best)
            elif cell_available > cell_depleted:
                cell_states.append(ResourceVisualState.AVAILABLE)
                cell_winning_similarities.append(cell_available)
            else:
                cell_states.append(ResourceVisualState.DEPLETED)
                cell_winning_similarities.append(cell_depleted)

        cell_count = len(cell_states)
        available_votes = cell_states.count(ResourceVisualState.AVAILABLE)
        depleted_votes = cell_states.count(ResourceVisualState.DEPLETED)
        if available_votes >= depleted_votes:
            leading_state, leading_votes = ResourceVisualState.AVAILABLE, available_votes
        else:
            leading_state, leading_votes = ResourceVisualState.DEPLETED, depleted_votes
        agreement_fraction = leading_votes / cell_count

        if leading_votes == 0 or agreement_fraction < candidate.minimum_occlusion_agreement:
            state = ResourceVisualState.UNCERTAIN
            confidence = min(scene_confidence, agreement_fraction)
            reason = "partial_occlusion_suspected"
        else:
            state = leading_state
            agreeing_similarities = [
                similarity
                for similarity, cell_state in zip(
                    cell_winning_similarities, cell_states, strict=True
                )
                if cell_state is leading_state
            ]
            confidence = min(scene_confidence, min(agreeing_similarities))
            reason = (
                "available_signature_matched"
                if state is ResourceVisualState.AVAILABLE
                else "depleted_signature_matched"
            )

        evidence: dict[str, object] = {
            "label": self._profile.ore_label,
            "location_id": self._profile.location_id,
            "profile_id": self._profile.profile_id,
            "resource_id": candidate.resource_id,
            "state": state.value,
            "region": candidate.region,
            "mean_rgb": tuple(round(channel, 3) for channel in whole_mean_rgb),
            "available_similarity": round(whole_available_similarity, 6),
            "depleted_similarity": round(whole_depleted_similarity, 6),
            "scene_confidence": round(scene_confidence, 6),
            "anchor_confidences": dict(sorted(anchor_confidences.items())),
            "reason": reason,
            "occlusion_cell_states": [cell_state.value for cell_state in cell_states],
            "occlusion_agreement_fraction": round(agreement_fraction, 6),
        }
        if landmark_distances is not None:
            evidence["landmark_distances"] = dict(sorted(landmark_distances.items()))
        return Observation(
            kind=observation_kind_for_state(state),
            frame=frame.ref,
            confidence=max(0.0, min(1.0, confidence)),
            evidence=evidence,
            detector_version=self._metadata.version,
        )

    def _uncertain_observation(
        self,
        frame: Frame,
        candidate: RockCandidateProfile,
        *,
        reason: str,
        scene_confidence: float,
        region_is_valid: bool,
        anchor_confidences: dict[str, float] | None = None,
        landmark_distances: dict[str, float] | None = None,
    ) -> Observation:
        evidence: dict[str, object] = {
            "label": self._profile.ore_label,
            "location_id": self._profile.location_id,
            "profile_id": self._profile.profile_id,
            "resource_id": candidate.resource_id,
            "state": ResourceVisualState.UNCERTAIN.value,
            "scene_confidence": round(scene_confidence, 6),
            "reason": reason,
        }
        if region_is_valid:
            evidence["region"] = candidate.region
        if anchor_confidences is not None:
            evidence["anchor_confidences"] = dict(sorted(anchor_confidences.items()))
        if landmark_distances is not None:
            evidence["landmark_distances"] = dict(sorted(landmark_distances.items()))
        return Observation(
            kind=observation_kind_for_state(ResourceVisualState.UNCERTAIN),
            frame=frame.ref,
            confidence=max(0.0, min(1.0, 1.0 - scene_confidence)),
            evidence=evidence,
            detector_version=self._metadata.version,
        )


def resource_state_from_observation(observation: Observation) -> ResourceState:
    """Convert one validated resource observation to the shared contract."""

    if not isinstance(observation, Observation):
        raise TypeError("observation must be Observation")
    if not observation.kind.startswith(RESOURCE_OBSERVATION_PREFIX):
        raise ValueError("observation is not a resource state observation")
    raw_state = observation.kind.removeprefix(RESOURCE_OBSERVATION_PREFIX)
    try:
        state = ResourceVisualState(raw_state)
    except ValueError as exc:
        raise ValueError(f"unsupported resource observation state: {raw_state!r}") from exc

    evidence_state = observation.evidence.get("state")
    if evidence_state != state.value:
        raise ValueError("resource observation kind and evidence state disagree")
    resource_id = observation.evidence.get("resource_id")
    if not isinstance(resource_id, str) or not resource_id.strip():
        raise ValueError("resource observation requires a non-empty resource_id")
    label = observation.evidence.get("label")
    if not isinstance(label, str) or not label.strip():
        raise ValueError("resource observation requires a non-empty label")

    region = _evidence_region(observation.evidence.get("region"))
    availability = {
        ResourceVisualState.AVAILABLE: True,
        ResourceVisualState.DEPLETED: False,
        ResourceVisualState.UNCERTAIN: None,
    }[state]
    return ResourceState(
        resource_id=resource_id,
        resource_type=label,
        available=availability,
        confidence=observation.confidence,
        interaction_region=region if availability is True else None,
    )


def resource_states_from_observations(
    observations: tuple[Observation, ...] | list[Observation],
) -> dict[str, ResourceState]:
    result: dict[str, ResourceState] = {}
    for observation in observations:
        state = resource_state_from_observation(observation)
        if state.resource_id in result:
            raise ValueError(f"duplicate resource observation: {state.resource_id}")
        result[state.resource_id] = state
    return result


def _evidence_region(value: object) -> tuple[int, int, int, int] | None:
    if value is None:
        return None
    if not isinstance(value, (tuple, list)) or len(value) != _REGION_COMPONENTS:
        raise ValueError("resource observation region must contain four integers")
    region = tuple(value)
    if any(not _is_integer(component) for component in region):
        raise ValueError("resource observation region must contain four integers")
    typed_region = (region[0], region[1], region[2], region[3])
    _validate_region(typed_region)
    return typed_region


def measure_region_mean_rgb(
    frame: Frame,
    region: tuple[int, int, int, int],
    *,
    sample_step: int = 1,
) -> tuple[float, float, float]:
    if not _is_integer(sample_step) or sample_step <= 0:
        raise ValueError("sample_step must be a positive integer")
    return _mean_rgb(frame, region, sample_step=sample_step)


def load_resource_detector_profile(path: Path) -> ResourceDetectorProfile:
    """Load a strict schema-v2 or schema-v3 resource detector profile.

    Schema v2 is kept as a real compatibility path rather than being inferred
    from missing v3 fields. Schema v3 requires the explicit landmark fields and
    frozen per-landmark ``zone`` identity.
    """

    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"resource profile cannot be read: {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("resource profile root must be an object")

    schema_version = raw.get("schema_version")
    common_keys = {
        "schema_version",
        "profile_id",
        "location_id",
        "ore_label",
        "frame",
        "minimum_scene_confidence",
        "minimum_anchor_confidence",
        "sample_step",
        "anchors",
        "candidates",
    }
    if schema_version == _LEGACY_RESOURCE_PROFILE_SCHEMA_VERSION:
        expected_keys = common_keys
    elif schema_version == RESOURCE_PROFILE_SCHEMA_VERSION:
        expected_keys = common_keys | {
            "scene_landmarks",
            "minimum_landmark_quorum",
            "minimum_landmark_zones",
        }
    else:
        raise ValueError(f"unsupported resource profile schema version {schema_version}")
    if set(raw) != expected_keys:
        raise ValueError("resource profile has missing or unknown root fields")

    frame_raw = raw["frame"]
    if not isinstance(frame_raw, dict) or set(frame_raw) != {
        "width",
        "height",
        "pixel_format",
    }:
        raise ValueError("resource profile frame object has invalid fields")
    anchors_raw = raw["anchors"]
    candidates_raw = raw["candidates"]
    if not isinstance(anchors_raw, list) or not isinstance(candidates_raw, list):
        raise ValueError("resource profile anchors and candidates must be arrays")

    try:
        anchors = tuple(_scene_anchor_from_json(item) for item in anchors_raw)
        candidates = tuple(_candidate_from_json(item) for item in candidates_raw)
        if schema_version == RESOURCE_PROFILE_SCHEMA_VERSION:
            landmarks_raw = raw["scene_landmarks"]
            if not isinstance(landmarks_raw, list):
                raise ValueError("resource profile scene_landmarks must be an array")
            scene_landmarks = tuple(_landmark_from_json(item) for item in landmarks_raw)
            minimum_landmark_quorum = raw["minimum_landmark_quorum"]
            minimum_landmark_zones = raw["minimum_landmark_zones"]
        else:
            scene_landmarks = ()
            minimum_landmark_quorum = 0
            minimum_landmark_zones = 0

        return ResourceDetectorProfile(
            profile_id=raw["profile_id"],
            location_id=raw["location_id"],
            ore_label=raw["ore_label"],
            frame_width=frame_raw["width"],
            frame_height=frame_raw["height"],
            pixel_format=PixelFormat(frame_raw["pixel_format"]),
            anchors=anchors,
            candidates=candidates,
            minimum_scene_confidence=raw["minimum_scene_confidence"],
            minimum_anchor_confidence=raw["minimum_anchor_confidence"],
            sample_step=raw["sample_step"],
            scene_landmarks=scene_landmarks,
            minimum_landmark_quorum=minimum_landmark_quorum,
            minimum_landmark_zones=minimum_landmark_zones,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid resource profile: {exc}") from exc


def save_resource_detector_profile(profile: ResourceDetectorProfile, path: Path) -> None:
    """Atomically write a current schema-v3 detector profile."""

    if not isinstance(profile, ResourceDetectorProfile):
        raise TypeError("profile must be ResourceDetectorProfile")
    payload = {
        "schema_version": RESOURCE_PROFILE_SCHEMA_VERSION,
        "profile_id": profile.profile_id,
        "location_id": profile.location_id,
        "ore_label": profile.ore_label,
        "frame": {
            "width": profile.frame_width,
            "height": profile.frame_height,
            "pixel_format": profile.pixel_format.value,
        },
        "minimum_scene_confidence": profile.minimum_scene_confidence,
        "minimum_anchor_confidence": profile.minimum_anchor_confidence,
        "sample_step": profile.sample_step,
        "minimum_landmark_quorum": profile.minimum_landmark_quorum,
        "minimum_landmark_zones": profile.minimum_landmark_zones,
        "scene_landmarks": [
            {
                "landmark_id": landmark.landmark_id,
                "region": list(landmark.region),
                "reference_descriptor": list(landmark.reference_descriptor),
                "maximum_distance": landmark.maximum_distance,
                "grid": landmark.grid,
                "zone": landmark.macro_zone.value,
            }
            for landmark in profile.scene_landmarks
        ],
        "anchors": [
            {
                "anchor_id": anchor.anchor_id,
                "region": list(anchor.region),
                "signature": _signature_to_json(anchor.signature),
                "weight": anchor.weight,
            }
            for anchor in profile.anchors
        ],
        "candidates": [
            {
                "resource_id": candidate.resource_id,
                "region": list(candidate.region),
                "available_signature": _signature_to_json(candidate.available_signature),
                "depleted_signature": _signature_to_json(candidate.depleted_signature),
                "minimum_similarity": candidate.minimum_similarity,
                "minimum_margin": candidate.minimum_margin,
                "occlusion_grid_columns": candidate.occlusion_grid_columns,
                "occlusion_grid_rows": candidate.occlusion_grid_rows,
                "minimum_occlusion_agreement": candidate.minimum_occlusion_agreement,
            }
            for candidate in profile.candidates
        ],
    }
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _signature_to_json(signature: ColorSignature) -> dict[str, object]:
    return {
        "mean_rgb": list(signature.mean_rgb),
        "max_distance": signature.max_distance,
    }


def _signature_from_json(value: object) -> ColorSignature:
    if not isinstance(value, dict) or set(value) != {"mean_rgb", "max_distance"}:
        raise ValueError("colour signature has invalid fields")
    mean_rgb = value["mean_rgb"]
    if not isinstance(mean_rgb, list) or len(mean_rgb) != 3:
        raise ValueError("colour signature mean_rgb must contain three values")
    return ColorSignature(
        mean_rgb=(mean_rgb[0], mean_rgb[1], mean_rgb[2]),
        max_distance=value["max_distance"],
    )


def _region_from_json(value: object) -> tuple[int, int, int, int]:
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError("profile region must contain four values")
    return value[0], value[1], value[2], value[3]


def _scene_anchor_from_json(value: object) -> SceneAnchorProfile:
    if not isinstance(value, dict) or set(value) != {
        "anchor_id",
        "region",
        "signature",
        "weight",
    }:
        raise ValueError("scene anchor has invalid fields")
    return SceneAnchorProfile(
        anchor_id=value["anchor_id"],
        region=_region_from_json(value["region"]),
        signature=_signature_from_json(value["signature"]),
        weight=value["weight"],
    )


def _landmark_from_json(value: object) -> SceneLandmarkProfile:
    if not isinstance(value, dict) or set(value) != {
        "landmark_id",
        "region",
        "reference_descriptor",
        "maximum_distance",
        "grid",
        "zone",
    }:
        raise ValueError("scene landmark has invalid fields")
    descriptor = value["reference_descriptor"]
    if not isinstance(descriptor, list):
        raise ValueError("scene landmark reference_descriptor must be an array")
    try:
        macro_zone = MacroZone(value["zone"])
    except (TypeError, ValueError) as exc:
        raise ValueError("scene landmark zone must be a valid macro-zone value") from exc
    return SceneLandmarkProfile(
        landmark_id=value["landmark_id"],
        region=_region_from_json(value["region"]),
        reference_descriptor=tuple(descriptor),
        maximum_distance=value["maximum_distance"],
        grid=value["grid"],
        macro_zone=macro_zone,
    )


def _candidate_from_json(value: object) -> RockCandidateProfile:
    if not isinstance(value, dict) or set(value) != {
        "resource_id",
        "region",
        "available_signature",
        "depleted_signature",
        "minimum_similarity",
        "minimum_margin",
        "occlusion_grid_columns",
        "occlusion_grid_rows",
        "minimum_occlusion_agreement",
    }:
        raise ValueError("rock candidate has invalid fields")
    return RockCandidateProfile(
        resource_id=value["resource_id"],
        region=_region_from_json(value["region"]),
        available_signature=_signature_from_json(value["available_signature"]),
        depleted_signature=_signature_from_json(value["depleted_signature"]),
        minimum_similarity=value["minimum_similarity"],
        minimum_margin=value["minimum_margin"],
        occlusion_grid_columns=value["occlusion_grid_columns"],
        occlusion_grid_rows=value["occlusion_grid_rows"],
        minimum_occlusion_agreement=value["minimum_occlusion_agreement"],
    )


def _mean_rgb(
    frame: Frame,
    region: tuple[int, int, int, int],
    *,
    sample_step: int,
) -> tuple[float, float, float]:
    _validate_region(region, frame_width=frame.width, frame_height=frame.height)
    x, y, width, height = region
    bytes_per_pixel = frame.pixel_format.bytes_per_pixel
    row_stride = frame.width * bytes_per_pixel
    payload = memoryview(frame.payload).cast("B")
    red_total = 0
    green_total = 0
    blue_total = 0
    sample_count = 0

    for sample_y in range(y, y + height, sample_step):
        row_start = sample_y * row_stride
        for sample_x in range(x, x + width, sample_step):
            offset = row_start + sample_x * bytes_per_pixel
            red, green, blue = _read_rgb(payload, offset, frame.pixel_format)
            red_total += red
            green_total += green
            blue_total += blue
            sample_count += 1

    if sample_count == 0:  # pragma: no cover - validated positive geometry
        raise ValueError("region produced no samples")
    return (
        red_total / sample_count,
        green_total / sample_count,
        blue_total / sample_count,
    )


def _read_rgb(
    payload: memoryview,
    offset: int,
    pixel_format: PixelFormat,
) -> tuple[int, int, int]:
    if pixel_format is PixelFormat.RGB888 or pixel_format is PixelFormat.RGBA8888:
        return int(payload[offset]), int(payload[offset + 1]), int(payload[offset + 2])
    if pixel_format is PixelFormat.BGR888 or pixel_format is PixelFormat.BGRA8888:
        return int(payload[offset + 2]), int(payload[offset + 1]), int(payload[offset])
    if pixel_format is PixelFormat.GRAY8:
        value = int(payload[offset])
        return value, value, value
    raise ValueError(f"unsupported pixel format: {pixel_format}")
