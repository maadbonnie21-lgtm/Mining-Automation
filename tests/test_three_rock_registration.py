from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

TOOLS_ROOT = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_ROOT))

from run_three_rock_continuous_proof import (  # noqa: E402
    registered_landmark_region_preserves_zone,
    validate_hash_bound_reference_payload,
)

from mining_automation.perception.scene_landmarks import (  # noqa: E402
    MacroZone,
    SceneLandmarkProfile,
)


def _landmark() -> SceneLandmarkProfile:
    return SceneLandmarkProfile(
        landmark_id="test-south-west",
        region=(200, 620, 48, 48),
        reference_descriptor=(0.0,) * 16,
        maximum_distance=0.12,
        grid=4,
        macro_zone=MacroZone.SOUTH_WEST,
    )


def test_registered_landmark_must_preserve_frozen_macro_zone() -> None:
    landmark = _landmark()
    assert registered_landmark_region_preserves_zone(landmark, (205, 625, 48, 48))
    assert not registered_landmark_region_preserves_zone(landmark, (205, 500, 48, 48))


def test_hash_bound_pose_reference_rejects_changed_pixels() -> None:
    payload = b"frozen returned-mine pixels"

    assert (
        validate_hash_bound_reference_payload(
            payload,
            hashlib.sha256(payload).hexdigest(),
        )
        == payload
    )

    with pytest.raises(ValueError, match="pose reference hash mismatch"):
        validate_hash_bound_reference_payload(payload, "0" * 64)
