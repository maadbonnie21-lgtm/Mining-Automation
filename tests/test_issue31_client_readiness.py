"""Deterministic safety coverage for the Issue #31 input-readiness veto."""

from __future__ import annotations

import gzip
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, cast

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.perception import load_varrock_east_iron_profile
from mining_automation.validation.client_readiness import (
    CLIENT_INPUT_READINESS_ID,
    CLIENT_INPUT_READINESS_VERSION,
    GAMEPLAY_CHROME_POLICIES,
    ClientReadinessReason,
    evaluate_client_input_readiness,
)

_FIXTURE_ROOT = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "perception"
    / "varrock-east-iron-v1"
)
_REVIEWED_CASES = (
    "available-01",
    "lower-left-full-cycle-019",
    "lower-left-full-cycle-020",
    "lower-left-full-cycle-028",
    "lower-left-full-cycle-029",
)


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


def _reviewed_frame(case_id: str = "available-01", *, frame_id: int = 1) -> Frame:
    payload = gzip.decompress(
        (_FIXTURE_ROOT / "frames" / f"{case_id}.raw.gz").read_bytes()
    )
    return Frame.from_raw(
        RawFrame(payload, 1005, 1078, PixelFormat.BGRA8888),
        frame_id=frame_id,
        captured_monotonic_s=float(frame_id),
    )


def _replace_regions(
    frame: Frame,
    regions: tuple[tuple[int, int, int, int], ...],
    *,
    bgra: tuple[int, int, int, int] = (0, 0, 0, 255),
) -> Frame:
    payload = bytearray(frame.payload)
    for x, y, width, height in regions:
        for row in range(y, y + height):
            for column in range(x, x + width):
                offset = (row * frame.width + column) * 4
                payload[offset : offset + 4] = bytes(bgra)
    return Frame.from_raw(
        RawFrame(bytes(payload), frame.width, frame.height, frame.pixel_format),
        frame_id=frame.frame_id + 1,
        captured_monotonic_s=frame.captured_monotonic_s + 1.0,
    )


@pytest.mark.parametrize("case_id", _REVIEWED_CASES)
def test_all_reviewed_gameplay_fixtures_pass_veto_only_readiness(case_id: str) -> None:
    result = evaluate_client_input_readiness(_reviewed_frame(case_id))

    assert result.evaluator_id == CLIENT_INPUT_READINESS_ID
    assert result.evaluator_version == CLIENT_INPUT_READINESS_VERSION
    assert result.reason is ClientReadinessReason.READY
    assert result.safe_to_attempt_camera_input
    assert len(result.anchors) == 3
    assert all(anchor.matched for anchor in result.anchors)
    assert not result.can_accept
    assert not result.can_validate_scene
    assert not result.can_expose_resources


def test_black_login_like_canvas_is_ambiguous_and_stops_input() -> None:
    payload = bytes((0, 0, 0, 255)) * 1005 * 1078
    frame = Frame.from_raw(
        RawFrame(payload, 1005, 1078, PixelFormat.BGRA8888),
        frame_id=1,
        captured_monotonic_s=1.0,
    )

    result = evaluate_client_input_readiness(frame)

    assert result.reason is ClientReadinessReason.GAMEPLAY_CHROME_MISMATCH
    assert not result.safe_to_attempt_camera_input
    assert not any(anchor.matched for anchor in result.anchors)
    assert "stop before camera input" in result.detail


@pytest.mark.parametrize(
    ("width", "height", "pixel_format"),
    [
        (1004, 1078, PixelFormat.BGRA8888),
        (1005, 1077, PixelFormat.BGRA8888),
        (1005, 1078, PixelFormat.RGBA8888),
    ],
)
def test_unsupported_frame_geometry_or_format_stops_without_anchor_evidence(
    width: int,
    height: int,
    pixel_format: PixelFormat,
) -> None:
    payload = bytes(width * height * pixel_format.bytes_per_pixel)
    frame = Frame.from_raw(
        RawFrame(payload, width, height, pixel_format),
        frame_id=1,
        captured_monotonic_s=1.0,
    )

    result = evaluate_client_input_readiness(frame)

    assert result.reason is ClientReadinessReason.UNSUPPORTED_FRAME
    assert not result.safe_to_attempt_camera_input
    assert result.anchors == ()


def test_world_landmarks_and_candidate_pixels_cannot_change_readiness() -> None:
    frame = _reviewed_frame()
    profile = load_varrock_east_iron_profile()
    protected_world_regions = tuple(
        landmark.region for landmark in profile.scene_landmarks
    ) + tuple(candidate.region for candidate in profile.candidates)

    changed = _replace_regions(frame, protected_world_regions, bgra=(255, 0, 255, 255))

    assert evaluate_client_input_readiness(changed) == evaluate_client_input_readiness(
        frame
    )


def test_readiness_anchors_are_disjoint_from_each_other_and_world_evidence() -> None:
    profile = load_varrock_east_iron_profile()
    anchor_regions = tuple(policy.region for policy in GAMEPLAY_CHROME_POLICIES)
    world_regions = tuple(
        landmark.region for landmark in profile.scene_landmarks
    ) + tuple(candidate.region for candidate in profile.candidates)

    assert all(
        not _regions_overlap(first, second)
        for index, first in enumerate(anchor_regions)
        for second in anchor_regions[index + 1 :]
    )
    assert all(
        not _regions_overlap(anchor, world)
        for anchor in anchor_regions
        for world in world_regions
    )


@pytest.mark.parametrize("anchor_index", range(len(GAMEPLAY_CHROME_POLICIES)))
def test_each_fixed_chrome_anchor_is_independently_required(anchor_index: int) -> None:
    frame = _reviewed_frame()
    changed = _replace_regions(frame, (GAMEPLAY_CHROME_POLICIES[anchor_index].region,))

    result = evaluate_client_input_readiness(changed)

    assert not result.safe_to_attempt_camera_input
    assert result.reason is ClientReadinessReason.GAMEPLAY_CHROME_MISMATCH
    assert not result.anchors[anchor_index].matched


def test_readiness_is_deterministic_immutable_and_contains_no_success_authority() -> None:
    frame = _reviewed_frame()
    first = evaluate_client_input_readiness(frame)
    second = evaluate_client_input_readiness(frame)

    assert first == second
    with pytest.raises(FrozenInstanceError):
        cast(Any, first).safe_to_attempt_camera_input = False
    with pytest.raises(FrozenInstanceError):
        cast(Any, first.anchors[0]).matched = False
