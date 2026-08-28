"""Issue #31 production camera-gate evaluation regressions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, cast

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.perception import (
    VARROCK_EAST_IRON_FIXED_UI_REGIONS,
    ResourceVisualState,
    load_replay_dataset,
    load_varrock_east_iron_profile,
    materialize_gzip_replay_dataset,
)
from mining_automation.validation.camera_evaluation import (
    CameraEvaluation,
    evaluate_varrock_east_camera,
)

FIXTURE_ROOT = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "perception"
    / "varrock-east-iron-v1"
)
MANIFEST = FIXTURE_ROOT / "manifest.json"

NORTHWEST = "varrock-east-iron-northwest"
SOUTHWEST = "varrock-east-iron-southwest"
CENTER = "varrock-east-iron-center"
NORTHEAST = "varrock-east-iron-northeast"
ALL_RESOURCES = (NORTHWEST, SOUTHWEST, CENTER, NORTHEAST)

EXPECTED_STATES = {
    "available-01": (
        ResourceVisualState.AVAILABLE,
        ResourceVisualState.AVAILABLE,
        ResourceVisualState.AVAILABLE,
        ResourceVisualState.AVAILABLE,
    ),
    "lower-left-full-cycle-019": (
        ResourceVisualState.AVAILABLE,
        ResourceVisualState.AVAILABLE,
        ResourceVisualState.AVAILABLE,
        ResourceVisualState.AVAILABLE,
    ),
    "lower-left-full-cycle-020": (
        ResourceVisualState.AVAILABLE,
        ResourceVisualState.DEPLETED,
        ResourceVisualState.AVAILABLE,
        ResourceVisualState.AVAILABLE,
    ),
    "lower-left-full-cycle-028": (
        ResourceVisualState.AVAILABLE,
        ResourceVisualState.DEPLETED,
        ResourceVisualState.AVAILABLE,
        ResourceVisualState.AVAILABLE,
    ),
    "lower-left-full-cycle-029": (
        ResourceVisualState.AVAILABLE,
        ResourceVisualState.AVAILABLE,
        ResourceVisualState.AVAILABLE,
        ResourceVisualState.AVAILABLE,
    ),
}


def _dataset(tmp_path: Path):
    materialized = materialize_gzip_replay_dataset(MANIFEST, tmp_path / "materialized")
    return load_replay_dataset(materialized)


def _blank_like(frame: Frame, *, frame_id: int) -> Frame:
    return Frame.from_raw(
        RawFrame(
            payload=bytes(frame.width * frame.height * frame.pixel_format.bytes_per_pixel),
            width=frame.width,
            height=frame.height,
            pixel_format=frame.pixel_format,
        ),
        frame_id=frame_id,
        captured_monotonic_s=frame.captured_monotonic_s + 1.0,
    )


def _fill_region(
    frame: Frame,
    region: tuple[int, int, int, int],
    rgb: tuple[int, int, int],
    *,
    frame_id: int,
) -> Frame:
    payload = bytearray(frame.payload)
    x, y, width, height = region
    red, green, blue = rgb
    stride = frame.width * frame.pixel_format.bytes_per_pixel
    for pixel_y in range(y, y + height):
        for pixel_x in range(x, x + width):
            offset = pixel_y * stride + pixel_x * 4
            payload[offset : offset + 4] = bytes((blue, green, red, 255))
    return Frame.from_raw(
        RawFrame(
            payload=payload,
            width=frame.width,
            height=frame.height,
            pixel_format=frame.pixel_format,
        ),
        frame_id=frame_id,
        captured_monotonic_s=frame.captured_monotonic_s + 1.0,
    )


def _copy_regions(
    source: Frame,
    destination: Frame,
    regions: tuple[tuple[int, int, int, int], ...],
    *,
    frame_id: int,
) -> Frame:
    payload = bytearray(destination.payload)
    stride = source.width * source.pixel_format.bytes_per_pixel
    for x, y, width, height in regions:
        for pixel_y in range(y, y + height):
            start = pixel_y * stride + x * 4
            end = start + width * 4
            payload[start:end] = source.payload[start:end]
    return Frame.from_raw(
        RawFrame(
            payload=payload,
            width=destination.width,
            height=destination.height,
            pixel_format=destination.pixel_format,
        ),
        frame_id=frame_id,
        captured_monotonic_s=destination.captured_monotonic_s + 1.0,
    )


def test_all_reviewed_replays_pass_with_exact_ordered_resource_states(
    tmp_path: Path,
) -> None:
    dataset = _dataset(tmp_path)

    for sample in dataset.samples:
        result = evaluate_varrock_east_camera(sample.frame)

        assert result.passed
        assert result.detector_id == "profiled-resource:varrock-east-iron-v1"
        assert result.detector_version == "2.1.0"
        assert result.profile_id == "varrock-east-iron-v1"
        assert result.profile_schema_version == 3
        assert (
            result.profile_frame_width,
            result.profile_frame_height,
            result.profile_pixel_format,
        ) == (1005, 1078, PixelFormat.BGRA8888)
        assert result.frame_geometry_supported
        assert result.scene_validated
        assert result.scene_reason == "scene_validated"
        assert result.matched_landmark_count >= 5
        assert result.required_landmark_count == 6
        assert result.required_landmark_matches == 5
        assert len(result.matched_zones) >= 3
        assert result.required_matched_zones == 3
        assert len(result.landmarks) == 6
        assert all(item.distance is not None for item in result.landmarks)
        assert all(
            item.matched == (cast(float, item.distance) <= item.threshold)
            for item in result.landmarks
        )
        assert tuple(item.resource_id for item in result.resource_states) == ALL_RESOURCES
        assert tuple(item.state for item in result.resource_states) == EXPECTED_STATES[
            sample.case.case_id
        ]
        assert result.definitive_target_ids == ALL_RESOURCES


def test_wrong_scene_fails_with_landmark_evidence_and_no_definitive_targets(
    tmp_path: Path,
) -> None:
    source = _dataset(tmp_path).samples[0].frame
    result = evaluate_varrock_east_camera(_blank_like(source, frame_id=9001))

    assert not result.passed
    assert result.frame_geometry_supported
    assert not result.scene_validated
    assert result.scene_reason == "insufficient_landmark_quorum"
    assert result.matched_landmark_count == 0
    assert result.matched_zones == ()
    assert result.definitive_target_ids == ()
    assert all(item.distance is not None for item in result.landmarks)
    assert all(not item.matched for item in result.landmarks)
    assert all(
        item.state is ResourceVisualState.UNCERTAIN for item in result.resource_states
    )


def test_candidate_and_fixed_ui_pixels_cannot_establish_scene_identity(
    tmp_path: Path,
) -> None:
    supported = _dataset(tmp_path).samples[0].frame
    wrong_scene = _blank_like(supported, frame_id=9005)
    profile = load_varrock_east_iron_profile()
    preserved_regions = (
        *(candidate.region for candidate in profile.candidates),
        *VARROCK_EAST_IRON_FIXED_UI_REGIONS,
    )
    candidate_and_ui_only = _copy_regions(
        supported,
        wrong_scene,
        preserved_regions,
        frame_id=9006,
    )

    result = evaluate_varrock_east_camera(candidate_and_ui_only)

    assert not result.passed
    assert not result.scene_validated
    assert result.definitive_target_ids == ()
    assert all(not landmark.matched for landmark in result.landmarks)
    assert all(
        resource.state is ResourceVisualState.UNCERTAIN
        for resource in result.resource_states
    )


def test_uncertain_resource_blocks_pass_even_when_the_scene_is_production_validated(
    tmp_path: Path,
) -> None:
    source = _dataset(tmp_path).samples[0].frame
    # The reviewed south-west candidate is 20x20 at (295, 490). Candidate
    # pixels are outside every world-only scene landmark by profile contract.
    obstructed = _fill_region(
        source,
        (295, 490, 20, 20),
        (255, 0, 255),
        frame_id=9004,
    )

    result = evaluate_varrock_east_camera(obstructed)

    assert result.scene_validated
    assert result.matched_landmark_count == 6
    assert not result.passed
    assert tuple(item.state for item in result.resource_states) == (
        ResourceVisualState.AVAILABLE,
        ResourceVisualState.UNCERTAIN,
        ResourceVisualState.AVAILABLE,
        ResourceVisualState.AVAILABLE,
    )
    assert result.definitive_target_ids == (NORTHWEST, CENTER, NORTHEAST)


def test_unsupported_frame_geometry_fails_closed_without_definitive_targets() -> None:
    unsupported = Frame.from_raw(
        RawFrame(
            payload=bytes(32 * 32 * 4),
            width=32,
            height=32,
            pixel_format=PixelFormat.BGRA8888,
        ),
        frame_id=9002,
        captured_monotonic_s=0.0,
    )

    result = evaluate_varrock_east_camera(unsupported)

    assert not result.passed
    assert not result.frame_geometry_supported
    assert not result.scene_validated
    assert result.scene_reason == "frame_geometry_mismatch"
    assert result.matched_landmark_count == 0
    assert result.matched_zones == ()
    assert all(item.distance is None and not item.matched for item in result.landmarks)
    assert result.definitive_target_ids == ()
    assert all(
        item.state is ResourceVisualState.UNCERTAIN for item in result.resource_states
    )


def test_diagnostic_results_cannot_be_injected_into_the_production_gate(
    tmp_path: Path,
) -> None:
    wrong_scene = _blank_like(_dataset(tmp_path).samples[0].frame, frame_id=9003)
    baseline = evaluate_varrock_east_camera(wrong_scene)
    unsafe_call = cast(Callable[..., CameraEvaluation], evaluate_varrock_east_camera)

    with pytest.raises(TypeError):
        unsafe_call(
            wrong_scene,
            diagnostic_data={
                "validated": True,
                "matched_landmarks": 6,
                "matched_zones": 4,
            },
        )

    assert not baseline.passed
    assert baseline.definitive_target_ids == ()


def test_camera_evaluation_is_immutable(tmp_path: Path) -> None:
    result = evaluate_varrock_east_camera(_dataset(tmp_path).samples[0].frame)

    with pytest.raises(FrozenInstanceError):
        cast(Any, result).passed = False
