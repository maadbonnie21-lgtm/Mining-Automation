"""Packaged production perception profiles and detector factories."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib.resources import as_file, files
from typing import Final

from ..contracts import FrameRef, Observation, ResourceState
from .resource import (
    RESOURCE_PROFILE_SCHEMA_VERSION,
    ProfiledResourceDetector,
    ResourceDetectorProfile,
    load_resource_detector_profile,
    resource_state_from_observation,
)

__all__ = [
    "VARROCK_EAST_IRON_DETECTOR_VERSION",
    "VARROCK_EAST_IRON_DETECTOR_ID",
    "VARROCK_EAST_IRON_FIXED_UI_REGIONS",
    "VARROCK_EAST_IRON_PROFILE_ID",
    "VARROCK_EAST_IRON_RESOURCE_IDS",
    "ProductionResourceTrustResult",
    "build_varrock_east_iron_detector",
    "load_varrock_east_iron_profile",
    "trust_varrock_east_iron_observations",
    "varrock_east_iron_scene_excluded_regions",
]

VARROCK_EAST_IRON_PROFILE_ID: Final[str] = "varrock-east-iron-v1"
VARROCK_EAST_IRON_DETECTOR_ID: Final[str] = (
    f"profiled-resource:{VARROCK_EAST_IRON_PROFILE_ID}"
)
VARROCK_EAST_IRON_RESOURCE_IDS: Final[tuple[str, ...]] = (
    "varrock-east-iron-northwest",
    "varrock-east-iron-southwest",
    "varrock-east-iron-center",
    "varrock-east-iron-northeast",
)
# These rectangles are fixed UI or non-rendered padding in the reviewed
# 1005x1078 RuneLite layout. They may be useful to diagnostics as layout
# evidence, but pixels inside them must never establish resource-scene identity.
VARROCK_EAST_IRON_FIXED_UI_REGIONS: Final[
    tuple[tuple[int, int, int, int], ...]
] = (
    (0, 0, 1005, 34),
    (545, 34, 222, 220),
    (767, 34, 238, 816),
    (520, 500, 485, 350),
    (0, 850, 1005, 228),
)

# Issue #18 changed the production scene-validation decision boundary. The
# follow-up replaces a minimap-contaminated landmark with world-only evidence,
# so provenance must distinguish observations from the earlier v2.0 profile.
VARROCK_EAST_IRON_DETECTOR_VERSION: Final[str] = "2.1.0"


@dataclass(frozen=True, slots=True)
class ProductionResourceTrustResult:
    """Fail-closed result of the source-owned Varrock East trust boundary.

    A rejected ensemble deliberately contains no resource states and therefore
    no interaction targets. An accepted ensemble retains all four canonical
    states, while ``actionable_targets`` exposes only validated AVAILABLE
    states with their exact packaged frame-local candidate regions.
    """

    accepted: bool
    reason: str
    frame: FrameRef | None = None
    resources: tuple[ResourceState, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.accepted, bool):
            raise ValueError("accepted must be a boolean")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason must be a non-empty string")
        if self.frame is not None and not isinstance(self.frame, FrameRef):
            raise ValueError("frame must be FrameRef or None")
        if not isinstance(self.resources, tuple) or any(
            not isinstance(resource, ResourceState) for resource in self.resources
        ):
            raise ValueError("resources must be a tuple of ResourceState values")
        if self.accepted:
            if self.frame is None or len(self.resources) != len(
                VARROCK_EAST_IRON_RESOURCE_IDS
            ):
                raise ValueError(
                    "accepted production resource trust requires one complete frame"
                )
        elif self.frame is not None or self.resources:
            raise ValueError("rejected production resource trust must expose zero targets")

    @property
    def actionable_targets(self) -> tuple[ResourceState, ...]:
        if not self.accepted:
            return ()
        return tuple(
            resource
            for resource in self.resources
            if resource.available is True and resource.interaction_region is not None
        )


def _reject_resource_ensemble(reason: str) -> ProductionResourceTrustResult:
    return ProductionResourceTrustResult(accepted=False, reason=reason)


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


def varrock_east_iron_scene_excluded_regions(
    profile: ResourceDetectorProfile,
) -> tuple[tuple[int, int, int, int], ...]:
    """Return reviewed non-scene regions, rejecting an unsafe packaged profile."""

    if not isinstance(profile, ResourceDetectorProfile):
        raise TypeError("profile must be ResourceDetectorProfile")
    for region in VARROCK_EAST_IRON_FIXED_UI_REGIONS:
        x, y, width, height = region
        if x + width > profile.frame_width or y + height > profile.frame_height:
            raise ValueError(
                f"fixed UI exclusion {region} is outside the packaged profile frame"
            )
        for landmark in profile.scene_landmarks:
            if _regions_overlap(landmark.region, region):
                raise ValueError(
                    f"packaged scene landmark {landmark.landmark_id!r} overlaps "
                    f"fixed UI exclusion {region}"
                )
    return (
        *(candidate.region for candidate in profile.candidates),
        *VARROCK_EAST_IRON_FIXED_UI_REGIONS,
    )


def load_varrock_east_iron_profile() -> ResourceDetectorProfile:
    """Load the packaged, reviewed Varrock East iron profile."""

    resource = files("mining_automation.perception").joinpath(
        "profiles/varrock_east_iron_v1.json"
    )
    with as_file(resource) as profile_path:
        profile = load_resource_detector_profile(profile_path)
    if profile.profile_id != VARROCK_EAST_IRON_PROFILE_ID:
        raise ValueError(
            "packaged Varrock East profile id mismatch: "
            f"{profile.profile_id!r} != {VARROCK_EAST_IRON_PROFILE_ID!r}"
        )
    varrock_east_iron_scene_excluded_regions(profile)
    return profile


def build_varrock_east_iron_detector() -> ProfiledResourceDetector:
    """Build the packaged production resource detector."""

    return ProfiledResourceDetector(
        load_varrock_east_iron_profile(),
        version=VARROCK_EAST_IRON_DETECTOR_VERSION,
    )


def trust_varrock_east_iron_observations(
    observations: Sequence[Observation],
) -> ProductionResourceTrustResult:
    """Validate one complete production detector ensemble without overrides.

    The generic resource adapters intentionally remain useful for diagnostics.
    This separate boundary is the controller-preparation contract: it accepts
    only the exact source-owned detector/profile/schema/location/resource
    identity, one complete four-resource ensemble, and one identical frame.
    Any counterexample fails closed to zero states and zero actionable targets.
    """

    if not isinstance(observations, Sequence) or isinstance(
        observations, (str, bytes, bytearray)
    ):
        return _reject_resource_ensemble("observations_not_a_sequence")
    items = tuple(observations)
    if len(items) != len(VARROCK_EAST_IRON_RESOURCE_IDS):
        return _reject_resource_ensemble("incomplete_resource_ensemble")
    if any(not isinstance(observation, Observation) for observation in items):
        return _reject_resource_ensemble("malformed_observation")

    typed_items = tuple(items)
    first_frame = typed_items[0].frame
    if not isinstance(first_frame, FrameRef):
        return _reject_resource_ensemble("malformed_frame_ref")

    by_resource_id: dict[str, Observation] = {}
    for observation in typed_items:
        if not isinstance(observation.frame, FrameRef):
            return _reject_resource_ensemble("malformed_frame_ref")
        if observation.frame != first_frame:
            return _reject_resource_ensemble("mixed_frame_ensemble")
        if not isinstance(observation.evidence, Mapping):
            return _reject_resource_ensemble("malformed_evidence")
        evidence = observation.evidence
        if evidence.get("detector_id") != VARROCK_EAST_IRON_DETECTOR_ID:
            return _reject_resource_ensemble("detector_identity_mismatch")
        if observation.detector_version != VARROCK_EAST_IRON_DETECTOR_VERSION:
            return _reject_resource_ensemble("detector_version_mismatch")
        if evidence.get("profile_id") != VARROCK_EAST_IRON_PROFILE_ID:
            return _reject_resource_ensemble("profile_identity_mismatch")
        if evidence.get("location_id") != "varrock-east-mine":
            return _reject_resource_ensemble("location_identity_mismatch")
        schema_version = evidence.get("profile_schema_version")
        if (
            not isinstance(schema_version, int)
            or isinstance(schema_version, bool)
            or schema_version != RESOURCE_PROFILE_SCHEMA_VERSION
        ):
            return _reject_resource_ensemble("profile_schema_mismatch")
        if evidence.get("label") != "iron":
            return _reject_resource_ensemble("resource_type_mismatch")
        resource_id = evidence.get("resource_id")
        if not isinstance(resource_id, str) or resource_id not in (
            VARROCK_EAST_IRON_RESOURCE_IDS
        ):
            return _reject_resource_ensemble("unexpected_resource_id")
        if resource_id in by_resource_id:
            return _reject_resource_ensemble("duplicate_resource_id")
        by_resource_id[resource_id] = observation

    if set(by_resource_id) != set(VARROCK_EAST_IRON_RESOURCE_IDS):
        return _reject_resource_ensemble("incomplete_resource_identity_set")

    try:
        profile = load_varrock_east_iron_profile()
    except (OSError, ValueError):
        return _reject_resource_ensemble("packaged_profile_unavailable")
    if (
        profile.schema_version != RESOURCE_PROFILE_SCHEMA_VERSION
        or profile.location_id != "varrock-east-mine"
        or tuple(candidate.resource_id for candidate in profile.candidates)
        != VARROCK_EAST_IRON_RESOURCE_IDS
    ):
        return _reject_resource_ensemble("packaged_profile_identity_mismatch")
    if first_frame.width != profile.frame_width or first_frame.height != profile.frame_height:
        return _reject_resource_ensemble("frame_geometry_mismatch")
    expected_regions = {
        candidate.resource_id: candidate.region for candidate in profile.candidates
    }

    resources: list[ResourceState] = []
    for resource_id in VARROCK_EAST_IRON_RESOURCE_IDS:
        observation = by_resource_id[resource_id]
        try:
            state = resource_state_from_observation(observation)
        except (TypeError, ValueError):
            return _reject_resource_ensemble("malformed_resource_observation")
        expected_region = expected_regions[resource_id]
        evidence_region = observation.evidence.get("region")
        if evidence_region is not None:
            if (
                not isinstance(evidence_region, (tuple, list))
                or tuple(evidence_region) != expected_region
            ):
                return _reject_resource_ensemble("candidate_region_mismatch")
        if state.available is True:
            if state.interaction_region != expected_region:
                return _reject_resource_ensemble("available_region_missing_or_invalid")
        elif state.interaction_region is not None:
            return _reject_resource_ensemble("non_available_region_exposed")
        resources.append(state)

    return ProductionResourceTrustResult(
        accepted=True,
        reason="trusted_complete_production_ensemble",
        frame=first_frame,
        resources=tuple(resources),
    )
