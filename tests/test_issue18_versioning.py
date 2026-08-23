"""Issue #18 production provenance/versioning regression."""

from mining_automation.perception import build_varrock_east_iron_detector
from mining_automation.perception.production_profiles import (
    VARROCK_EAST_IRON_DETECTOR_VERSION,
)


def test_scene_decision_boundary_bumps_detector_version() -> None:
    assert VARROCK_EAST_IRON_DETECTOR_VERSION == "2.0.0"
    assert build_varrock_east_iron_detector().metadata.version == "2.0.0"
