"""Lead-review hardening tests for Issue #18."""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.perception import (
    load_replay_dataset,
    load_varrock_east_iron_profile,
    materialize_gzip_replay_dataset,
)
from mining_automation.perception.resource import (
    ProfiledResourceDetector,
    ResourceDetectorProfile,
    ResourceVisualState,
    load_resource_detector_profile,
    save_resource_detector_profile,
)
from mining_automation.perception.scene_landmarks import (
    MacroZone,
    SceneLandmarkProfile,
    calibrate_scene_landmark,
    descriptor_distance,
    evaluate_scene,
)

FIXTURE_ROOT = (
    Path(__file__).resolve().parent / "fixtures" / "perception" / "varrock-east-iron-v1"
)
MANIFEST = FIXTURE_ROOT / "manifest.json"
HISTORICAL_V2_PROFILE = FIXTURE_ROOT / "profile-schema-v2.json"

TITLE_MASK = (0, 0, 1005, 34)
BOTTOM_MASK = (0, 850, 1005, 228)
INVENTORY_MASK = (520, 500, 485, 350)
SANITIZED_REGIONS = (TITLE_MASK, BOTTOM_MASK, INVENTORY_MASK)


def _real_frame(tmp_path: Path, case_id: str = "available-01") -> Frame:
    dataset = load_replay_dataset(materialize_gzip_replay_dataset(MANIFEST, tmp_path))
    return next(sample.frame for sample in dataset.samples if sample.case.case_id == case_id)


def _mutate_region(
    frame: Frame,
    region: tuple[int, int, int, int],
    rgb: tuple[float, float, float],
) -> Frame:
    x, y, width, height = region
    blue, green, red = int(round(rgb[2])), int(round(rgb[1])), int(round(rgb[0]))
    payload = bytearray(frame.payload)
    stride = frame.width * frame.pixel_format.bytes_per_pixel
    for row in range(y, y + height):
        base = row * stride
        for column in range(x, x + width):
            offset = base + column * 4
            payload[offset : offset + 4] = bytes((blue, green, red, 255))
    return Frame.from_raw(
        RawFrame(bytes(payload), frame.width, frame.height, frame.pixel_format),
        frame_id=frame.frame_id + 1,
        captured_monotonic_s=frame.captured_monotonic_s + 1.0,
    )


def test_historical_schema_v2_profile_still_loads_and_uses_anchor_gate(
    tmp_path: Path,
) -> None:
    profile = load_resource_detector_profile(HISTORICAL_V2_PROFILE)
    assert profile.scene_landmarks == ()
    assert profile.minimum_landmark_quorum == 0
    assert profile.minimum_landmark_zones == 0

    source = _real_frame(tmp_path)
    anchor = next(item for item in profile.anchors if item.anchor_id == "south-ground")
    drifted_rgb = (
        anchor.signature.mean_rgb[0] + 0.6 * anchor.signature.max_distance,
        anchor.signature.mean_rgb[1],
        anchor.signature.mean_rgb[2],
    )
    frame = _mutate_region(source, anchor.region, drifted_rgb)
    observations = ProfiledResourceDetector(profile, version="v2-regression").detect(frame)

    assert all(
        observation.evidence["state"] == ResourceVisualState.UNCERTAIN.value
        for observation in observations
    )
    assert all(
        str(observation.evidence["reason"]).startswith("anchor_confidence_below_floor")
        for observation in observations
    )


def test_v3_round_trip_preserves_explicit_frozen_zones(tmp_path: Path) -> None:
    profile = load_varrock_east_iron_profile()
    destination = tmp_path / "round-trip.json"
    save_resource_detector_profile(profile, destination)
    encoded = json.loads(destination.read_text(encoding="utf-8"))

    assert all("zone" in item for item in encoded["scene_landmarks"])
    reloaded = load_resource_detector_profile(destination)
    assert [item.macro_zone for item in reloaded.scene_landmarks] == [
        item.macro_zone for item in profile.scene_landmarks
    ]


def test_v3_json_rejects_missing_landmark_zone(tmp_path: Path) -> None:
    profile = load_varrock_east_iron_profile()
    destination = tmp_path / "missing-zone.json"
    save_resource_detector_profile(profile, destination)
    payload = json.loads(destination.read_text(encoding="utf-8"))
    del payload["scene_landmarks"][0]["zone"]
    destination.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="scene landmark has invalid fields"):
        load_resource_detector_profile(destination)


def test_v3_json_rejects_zone_that_disagrees_with_region(tmp_path: Path) -> None:
    profile = load_varrock_east_iron_profile()
    destination = tmp_path / "wrong-zone.json"
    save_resource_detector_profile(profile, destination)
    payload = json.loads(destination.read_text(encoding="utf-8"))
    payload["scene_landmarks"][0]["zone"] = MacroZone.NORTH_EAST.value
    destination.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="does not match region-derived zone"):
        load_resource_detector_profile(destination)


def test_profile_rejects_programmatic_zone_mismatch() -> None:
    profile = load_varrock_east_iron_profile()
    first = replace(profile.scene_landmarks[0], macro_zone=MacroZone.NORTH_EAST)
    with pytest.raises(ValueError, match="does not match region-derived zone"):
        replace(profile, scene_landmarks=(first, *profile.scene_landmarks[1:]))


@pytest.mark.parametrize("bad_value", [math.nan, math.inf, -math.inf])
def test_landmark_descriptor_rejects_non_finite_values(bad_value: float) -> None:
    descriptor = [0.0] * 16
    descriptor[3] = bad_value
    with pytest.raises(ValueError, match="finite real numbers"):
        SceneLandmarkProfile(
            landmark_id="bad",
            region=(0, 0, 48, 48),
            reference_descriptor=tuple(descriptor),
            maximum_distance=0.12,
            macro_zone=MacroZone.NORTH_WEST,
        )


def test_landmark_distance_rejects_non_finite_inputs() -> None:
    with pytest.raises(ValueError, match="finite real numbers"):
        descriptor_distance((0.0, math.nan), (0.0, 0.0))


def test_direct_scene_evaluation_validates_quorum_zone_and_geometry_arguments() -> None:
    frame = Frame.from_raw(
        RawFrame(bytes(64 * 64 * 4), 64, 64, PixelFormat.BGRA8888),
        frame_id=1,
        captured_monotonic_s=0.0,
    )
    landmark = SceneLandmarkProfile(
        landmark_id="nw",
        region=(0, 0, 32, 32),
        reference_descriptor=tuple(0.0 for _ in range(16)),
        maximum_distance=0.12,
        macro_zone=MacroZone.NORTH_WEST,
    )

    with pytest.raises(ValueError, match="required_quorum"):
        evaluate_scene(
            frame,
            (landmark,),
            required_quorum=0,
            required_zones=1,
            frame_width=64,
            frame_height=64,
        )
    with pytest.raises(ValueError, match="landmark count"):
        evaluate_scene(
            frame,
            (landmark,),
            required_quorum=2,
            required_zones=1,
            frame_width=64,
            frame_height=64,
        )
    with pytest.raises(ValueError, match="required_zones"):
        evaluate_scene(
            frame,
            (landmark,),
            required_quorum=1,
            required_zones=0,
            frame_width=64,
            frame_height=64,
        )
    with pytest.raises(ValueError, match="profile dimensions"):
        evaluate_scene(
            frame,
            (landmark,),
            required_quorum=1,
            required_zones=1,
            frame_width=65,
            frame_height=64,
        )


def test_calibration_rejects_featureless_landmark() -> None:
    frame = Frame.from_raw(
        RawFrame(bytes(100 * 100 * 4), 100, 100, PixelFormat.BGRA8888),
        frame_id=1,
        captured_monotonic_s=0.0,
    )
    with pytest.raises(ValueError, match="structural variance"):
        calibrate_scene_landmark(
            frame,
            landmark_id="flat",
            region=(0, 0, 48, 48),
            macro_zone=MacroZone.NORTH_WEST,
            maximum_distance=0.12,
        )


def test_calibration_rejects_candidate_margin_overlap(tmp_path: Path) -> None:
    frame = _real_frame(tmp_path)
    profile = load_varrock_east_iron_profile()
    with pytest.raises(ValueError, match="candidate region or its margin"):
        calibrate_scene_landmark(
            frame,
            landmark_id="too-close-to-rock",
            region=(215, 409, 40, 40),
            macro_zone=MacroZone.NORTH_WEST,
            maximum_distance=0.12,
            candidate_regions=tuple(item.region for item in profile.candidates),
            candidate_margin=10,
        )


def test_calibration_actively_rejects_lower_right_privacy_mask_edge(
    tmp_path: Path,
) -> None:
    frame = _real_frame(tmp_path)
    with pytest.raises(ValueError, match="excluded/sanitized"):
        calibrate_scene_landmark(
            frame,
            landmark_id="privacy-mask-edge",
            region=(500, 520, 48, 48),
            macro_zone=MacroZone.SOUTH_EAST,
            maximum_distance=0.12,
            excluded_regions=SANITIZED_REGIONS,
        )


def test_calibration_helper_reproduces_reviewed_landmark(tmp_path: Path) -> None:
    frame = _real_frame(tmp_path)
    profile = load_varrock_east_iron_profile()
    reviewed = next(item for item in profile.scene_landmarks if item.landmark_id == "west-ridge")
    calibrated = calibrate_scene_landmark(
        frame,
        landmark_id=reviewed.landmark_id,
        region=reviewed.region,
        macro_zone=reviewed.macro_zone,
        maximum_distance=reviewed.maximum_distance,
        grid=reviewed.grid,
        excluded_regions=SANITIZED_REGIONS,
        candidate_regions=tuple(item.region for item in profile.candidates),
        candidate_margin=8,
    )

    assert calibrated.macro_zone is reviewed.macro_zone
    assert descriptor_distance(
        calibrated.reference_descriptor, reviewed.reference_descriptor
    ) < 1e-6


def test_packaged_landmarks_are_balanced_two_per_usable_zone() -> None:
    profile = load_varrock_east_iron_profile()
    counts = Counter(item.macro_zone for item in profile.scene_landmarks)
    assert counts == Counter(
        {
            MacroZone.NORTH_WEST: 2,
            MacroZone.SOUTH_WEST: 2,
            MacroZone.NORTH_EAST: 2,
        }
    )


def test_resource_profile_rejects_non_tuple_scene_landmarks() -> None:
    profile = load_varrock_east_iron_profile()
    with pytest.raises(ValueError, match="scene_landmarks must be a tuple"):
        ResourceDetectorProfile(
            profile_id=profile.profile_id,
            location_id=profile.location_id,
            ore_label=profile.ore_label,
            frame_width=profile.frame_width,
            frame_height=profile.frame_height,
            pixel_format=profile.pixel_format,
            anchors=profile.anchors,
            candidates=profile.candidates,
            scene_landmarks=list(profile.scene_landmarks),  # type: ignore[arg-type]
            minimum_landmark_quorum=5,
            minimum_landmark_zones=3,
        )
