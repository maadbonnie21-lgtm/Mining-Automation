"""Packaged production perception profiles and detector factories."""

from __future__ import annotations

from importlib.resources import as_file, files
from typing import Final

from .resource import (
    ProfiledResourceDetector,
    ResourceDetectorProfile,
    load_resource_detector_profile,
)

__all__ = [
    "VARROCK_EAST_IRON_DETECTOR_VERSION",
    "VARROCK_EAST_IRON_FIXED_UI_REGIONS",
    "VARROCK_EAST_IRON_PROFILE_ID",
    "build_varrock_east_iron_detector",
    "load_varrock_east_iron_profile",
    "varrock_east_iron_scene_excluded_regions",
]

VARROCK_EAST_IRON_PROFILE_ID: Final[str] = "varrock-east-iron-v1"
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
