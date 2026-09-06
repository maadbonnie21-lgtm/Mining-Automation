from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

TOOLS_ROOT = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_ROOT))

import run_three_rock_continuous_proof as three_rock  # noqa: E402
from run_three_rock_continuous_proof import registered_landmark_region_preserves_zone  # noqa: E402

from mining_automation.capture import Frame, PixelFormat, RawFrame  # noqa: E402
from mining_automation.perception.production_profiles import (  # noqa: E402
    VARROCK_EAST_IRON_DETECTOR_VERSION,
    load_varrock_east_iron_profile,
)
from mining_automation.perception.resource import ProfiledResourceDetector  # noqa: E402
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
    assert registered_landmark_region_preserves_zone(
        landmark, (205, 625, 48, 48)
    )
    assert not registered_landmark_region_preserves_zone(
        landmark, (205, 500, 48, 48)
    )


def test_unmatched_boundary_outlier_cannot_veto_valid_five_of_six_quorum(
    monkeypatch,
) -> None:
    specs = (
        ("nw-a", (80, 100, 48, 48), MacroZone.NORTH_WEST, (0, 80)),
        ("nw-b", (200, 180, 48, 48), MacroZone.NORTH_WEST, (0, 80)),
        ("ne-a", (560, 100, 48, 48), MacroZone.NORTH_EAST, (0, 80)),
        ("ne-b", (680, 180, 48, 48), MacroZone.NORTH_EAST, (0, 80)),
        ("sw-a", (80, 620, 48, 48), MacroZone.SOUTH_WEST, (0, 80)),
        ("sw-outlier", (200, 770, 48, 48), MacroZone.SOUTH_WEST, (0, -148)),
    )
    landmarks = tuple(
        SceneLandmarkProfile(
            landmark_id=name,
            region=region,
            reference_descriptor=(float(index),) * 16,
            maximum_distance=0.12,
            grid=4,
            macro_zone=zone,
        )
        for index, (name, region, zone, _) in enumerate(specs, start=1)
    )
    base = load_varrock_east_iron_profile()
    profile = replace(
        base,
        scene_landmarks=landmarks,
        minimum_landmark_quorum=5,
        minimum_landmark_zones=3,
    )
    detector = ProfiledResourceDetector(
        profile, version=VARROCK_EAST_IRON_DETECTOR_VERSION
    )
    descriptors = {
        (region[0] + shift[0], region[1] + shift[1], region[2], region[3]):
            np.asarray(landmark.reference_descriptor)
        for landmark, (_, region, _, shift) in zip(landmarks, specs, strict=True)
    }

    def fake_descriptor(_integral, region):
        return descriptors.get(tuple(region), np.zeros(16))

    monkeypatch.setattr(three_rock, "_fast_descriptor", fake_descriptor)
    frame = Frame.from_raw(
        RawFrame(
            bytes(three_rock.WIDTH * three_rock.HEIGHT * 4),
            three_rock.WIDTH,
            three_rock.HEIGHT,
            PixelFormat.BGRA8888,
        ),
        frame_id=1,
        captured_monotonic_s=0.0,
    )

    registration = three_rock.register_translation(frame, detector)
    assert registration is not None
    shifted_detector, diagnosis = registration
    assert diagnosis["matched"] == 5
    assert set(diagnosis["zones"]) == {"north_west", "north_east", "south_west"}
    shifted_ids = {item.landmark_id for item in shifted_detector.profile.scene_landmarks}
    assert shifted_ids == {"nw-a", "nw-b", "ne-a", "ne-b", "sw-a"}
