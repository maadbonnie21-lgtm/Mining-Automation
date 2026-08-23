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
    "VARROCK_EAST_IRON_PROFILE_ID",
    "build_varrock_east_iron_detector",
    "load_varrock_east_iron_profile",
]

VARROCK_EAST_IRON_PROFILE_ID: Final[str] = "varrock-east-iron-v1"
# Issue #18 changes the production scene-validation decision boundary. Bump the
# detector version so WorldState/provenance code can force reacquisition rather
# than treating v2-anchor and v3-landmark evidence as semantically identical.
VARROCK_EAST_IRON_DETECTOR_VERSION: Final[str] = "2.0.0"


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
    return profile


def build_varrock_east_iron_detector() -> ProfiledResourceDetector:
    """Build the packaged production resource detector."""

    return ProfiledResourceDetector(
        load_varrock_east_iron_profile(),
        version=VARROCK_EAST_IRON_DETECTOR_VERSION,
    )
