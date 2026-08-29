"""Deterministic safety proof for the Issue #31 stale-guidance arm guard."""

from __future__ import annotations

import gzip
import hashlib
from dataclasses import FrozenInstanceError, replace
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.perception.scene_landmarks import MacroZone
from mining_automation.validation.camera_arm_guard import (
    CAMERA_ARM_GUARD_EXCLUDED_REGIONS,
    CAMERA_ARM_GUARD_ID,
    CAMERA_ARM_GUARD_LANDMARK_IDS,
    CAMERA_ARM_GUARD_STRUCTURAL_REGIONS,
    CAMERA_ARM_GUARD_VERSION,
    CAMERA_ARM_MAXIMUM_CHANGED_PIXEL_FRACTION,
    CAMERA_ARM_MAXIMUM_MEAN_CHANNEL_DELTA,
    CAMERA_ARM_MINIMUM_REGION_COVERAGE,
    CAMERA_ARM_REQUIRED_REGION_COUNT,
    CAMERA_ARM_REQUIRED_ZONE_COUNT,
    CameraArmGuardDisposition,
    CameraArmGuardReason,
    CameraArmGuardRegionMetric,
    evaluate_camera_arm_guard,
)

_FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "perception"
    / "varrock-east-iron-v1"
    / "frames"
    / "available-01.raw.gz"
)


@lru_cache(maxsize=1)
def _reviewed_payload() -> bytes:
    return gzip.decompress(_FIXTURE.read_bytes())


def _frame(
    payload: bytes | None = None,
    *,
    frame_id: int,
    captured_monotonic_s: float,
    width: int = 1005,
    height: int = 1078,
    pixel_format: PixelFormat = PixelFormat.BGRA8888,
) -> Frame:
    return Frame.from_raw(
        RawFrame(
            _reviewed_payload() if payload is None else payload,
            width,
            height,
            pixel_format,
        ),
        frame_id=frame_id,
        captured_monotonic_s=captured_monotonic_s,
    )


def _paint_regions(
    payload: bytes,
    regions: tuple[tuple[int, int, int, int], ...],
    *,
    colour: tuple[int, int, int, int] = (255, 0, 255, 255),
) -> bytes:
    changed = bytearray(payload)
    row_value_by_width: dict[int, bytes] = {}
    for x, y, width, height in regions:
        row_value = row_value_by_width.setdefault(width, bytes(colour) * width)
        for row in range(y, y + height):
            start = (row * 1005 + x) * 4
            changed[start : start + width * 4] = row_value
    return bytes(changed)


def _offset_region_exactly(
    payload: bytes,
    region: tuple[int, int, int, int],
    *,
    delta: int,
) -> bytes:
    changed = bytearray(payload)
    x, y, width, height = region
    for row in range(y, y + height):
        for column in range(x, x + width):
            offset = (row * 1005 + column) * 4
            for channel in range(3):
                value = changed[offset + channel]
                changed[offset + channel] = value + delta if value <= 255 - delta else value - delta
    return bytes(changed)


def _shift_raster_right(payload: bytes, pixels: int) -> bytes:
    """Translate a reviewed BGRA raster without image-library variability."""

    row_bytes = 1005 * 4
    shift_bytes = pixels * 4
    changed = bytearray(len(payload))
    for row in range(1078):
        source = row * row_bytes
        destination = source + shift_bytes
        changed[destination : source + row_bytes] = payload[
            source : source + row_bytes - shift_bytes
        ]
    return bytes(changed)


def _overlap(
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


def test_exact_unchanged_fresh_frame_retains_non_authoritative_guidance() -> None:
    decision = _frame(frame_id=11, captured_monotonic_s=10.0)
    arm = _frame(frame_id=12, captured_monotonic_s=10.1)

    result = evaluate_camera_arm_guard(decision, arm)

    assert result.guard_id == CAMERA_ARM_GUARD_ID
    assert result.guard_version == CAMERA_ARM_GUARD_VERSION
    assert result.disposition is CameraArmGuardDisposition.RETAIN
    assert result.reason is CameraArmGuardReason.UNCHANGED_WORLD
    assert result.safe_to_retain_guidance
    assert result.stable_landmark_count == CAMERA_ARM_REQUIRED_REGION_COUNT == 6
    assert len(result.stable_zones) == CAMERA_ARM_REQUIRED_ZONE_COUNT == 3
    assert tuple(metric.landmark_id for metric in result.regions) == (
        CAMERA_ARM_GUARD_LANDMARK_IDS
    )
    assert all(metric.distance == 0.0 and metric.stable for metric in result.regions)
    assert result.mean_landmark_distance == 0.0
    assert result.maximum_landmark_distance == 0.0
    assert result.decision_frame_id == 11
    assert result.arm_frame_id == 12
    expected_sha256 = hashlib.sha256(_reviewed_payload()).hexdigest()
    assert result.decision_payload_sha256 == expected_sha256
    assert result.arm_payload_sha256 == expected_sha256
    assert not result.can_accept
    assert not result.can_validate_scene
    assert not result.can_expose_resources


def test_frozen_policy_records_world_regions_and_all_exclusions_without_overlap() -> None:
    result = evaluate_camera_arm_guard(
        _frame(frame_id=1, captured_monotonic_s=1.0),
        _frame(frame_id=2, captured_monotonic_s=2.0),
    )

    assert result.excluded_regions == CAMERA_ARM_GUARD_EXCLUDED_REGIONS
    assert tuple(
        (metric.landmark_id, metric.zone, metric.region) for metric in result.regions
    ) == CAMERA_ARM_GUARD_STRUCTURAL_REGIONS
    assert all(
        not _overlap(structural_region, exclusion)
        for _, _, structural_region in CAMERA_ARM_GUARD_STRUCTURAL_REGIONS
        for exclusion in CAMERA_ARM_GUARD_EXCLUDED_REGIONS
    )
    assert CAMERA_ARM_MINIMUM_REGION_COVERAGE == 0.75
    assert all(
        metric.compared_pixel_count / metric.total_pixel_count
        >= CAMERA_ARM_MINIMUM_REGION_COVERAGE
        for metric in result.regions
    )


def test_candidate_and_every_fixed_ui_readiness_mutation_are_not_world_evidence() -> None:
    decision = _frame(frame_id=1, captured_monotonic_s=1.0)
    changed_payload = _paint_regions(
        decision.payload,
        CAMERA_ARM_GUARD_EXCLUDED_REGIONS,
    )
    arm = _frame(changed_payload, frame_id=2, captured_monotonic_s=2.0)

    result = evaluate_camera_arm_guard(decision, arm)

    assert changed_payload != decision.payload
    assert result.arm_payload_sha256 != result.decision_payload_sha256
    assert result.disposition is CameraArmGuardDisposition.RETAIN
    assert all(metric.distance == 0.0 for metric in result.regions)
    assert not result.can_accept
    assert not result.can_validate_scene
    assert not result.can_expose_resources


@pytest.mark.parametrize("landmark_index", [0, 2, 4])
def test_one_material_outlier_in_each_macro_zone_discards_even_with_five_stable(
    landmark_index: int,
) -> None:
    decision = _frame(frame_id=1, captured_monotonic_s=1.0)
    region = CAMERA_ARM_GUARD_STRUCTURAL_REGIONS[landmark_index][2]
    arm = _frame(
        _paint_regions(decision.payload, (region,)),
        frame_id=2,
        captured_monotonic_s=2.0,
    )

    result = evaluate_camera_arm_guard(decision, arm)

    assert result.disposition is CameraArmGuardDisposition.DISCARD_RESTART
    assert result.reason is CameraArmGuardReason.MATERIAL_WORLD_CHANGE
    assert result.stable_landmark_count == 5
    assert len(result.stable_zones) == 3
    assert not result.regions[landmark_index].stable
    assert result.maximum_landmark_distance >= result.regions[landmark_index].distance
    assert not result.safe_to_retain_guidance
    assert not result.can_accept
    assert not result.can_expose_resources


def test_two_material_outliers_leave_four_of_six_and_discard() -> None:
    decision = _frame(frame_id=1, captured_monotonic_s=1.0)
    regions = tuple(CAMERA_ARM_GUARD_STRUCTURAL_REGIONS[index][2] for index in (0, 4))
    arm = _frame(
        _paint_regions(decision.payload, regions),
        frame_id=2,
        captured_monotonic_s=2.0,
    )

    result = evaluate_camera_arm_guard(decision, arm)

    assert result.disposition is CameraArmGuardDisposition.DISCARD_RESTART
    assert result.reason is CameraArmGuardReason.MATERIAL_WORLD_CHANGE
    assert result.stable_landmark_count == 4


def test_coherent_two_pixel_world_translation_discards_stale_guidance() -> None:
    decision = _frame(frame_id=1, captured_monotonic_s=1.0)
    arm = _frame(
        _shift_raster_right(decision.payload, 2),
        frame_id=2,
        captured_monotonic_s=2.0,
    )

    result = evaluate_camera_arm_guard(decision, arm)

    assert result.disposition is CameraArmGuardDisposition.DISCARD_RESTART
    assert result.reason is CameraArmGuardReason.MATERIAL_WORLD_CHANGE
    assert any(not metric.stable for metric in result.regions)
    assert result.maximum_landmark_distance > CAMERA_ARM_MAXIMUM_MEAN_CHANNEL_DELTA
    assert not result.safe_to_retain_guidance


def test_exact_mean_distance_threshold_is_an_outlier_and_discards() -> None:
    decision = _frame(frame_id=1, captured_monotonic_s=1.0)
    changed = _offset_region_exactly(
        decision.payload,
        CAMERA_ARM_GUARD_STRUCTURAL_REGIONS[0][2],
        delta=int(CAMERA_ARM_MAXIMUM_MEAN_CHANNEL_DELTA),
    )

    result = evaluate_camera_arm_guard(
        decision,
        _frame(changed, frame_id=2, captured_monotonic_s=2.0),
    )

    metric = result.regions[0]
    assert metric.distance == CAMERA_ARM_MAXIMUM_MEAN_CHANNEL_DELTA
    assert metric.changed_pixel_fraction == 0.0
    assert not metric.stable
    assert result.reason is CameraArmGuardReason.MATERIAL_WORLD_CHANGE
    assert result.disposition is CameraArmGuardDisposition.DISCARD_RESTART


def test_exact_changed_fraction_threshold_is_exclusive_in_metric_contract() -> None:
    _, zone, region = CAMERA_ARM_GUARD_STRUCTURAL_REGIONS[0]

    metric = CameraArmGuardRegionMetric(
        landmark_id=CAMERA_ARM_GUARD_LANDMARK_IDS[0],
        zone=zone,
        region=region,
        compared_pixel_count=2304,
        total_pixel_count=2304,
        mean_absolute_channel_delta=0.0,
        changed_pixel_fraction=CAMERA_ARM_MAXIMUM_CHANGED_PIXEL_FRACTION,
        within_limit=False,
    )

    assert not metric.stable
    assert metric.changed_fraction_threshold == CAMERA_ARM_MAXIMUM_CHANGED_PIXEL_FRACTION
    with pytest.raises(ValueError, match="within_limit"):
        replace(metric, within_limit=True)


def test_forged_duplicate_or_two_zone_retain_evidence_is_rejected() -> None:
    result = evaluate_camera_arm_guard(
        _frame(frame_id=1, captured_monotonic_s=1.0),
        _frame(frame_id=2, captured_monotonic_s=2.0),
    )
    duplicate = (result.regions[0], *result.regions[1:5], result.regions[0])
    two_zone = tuple(
        replace(metric, zone=MacroZone.NORTH_WEST)
        if metric.zone is MacroZone.NORTH_EAST
        else metric
        for metric in result.regions
    )

    with pytest.raises(ValueError, match="frozen structural regions"):
        replace(result, regions=duplicate)
    with pytest.raises(ValueError, match="evaluated_zones|macro zones|frozen structural"):
        replace(result, regions=two_zone)


@pytest.mark.parametrize(
    ("decision_id", "decision_time", "arm_id", "arm_time"),
    [
        (1, 1.0, 1, 2.0),
        (1, 1.0, 2, 1.0),
        (2, 1.0, 1, 2.0),
        (2, 2.0, 3, 1.0),
    ],
)
def test_same_replayed_or_non_strict_identity_timestamp_discards(
    decision_id: int,
    decision_time: float,
    arm_id: int,
    arm_time: float,
) -> None:
    result = evaluate_camera_arm_guard(
        _frame(frame_id=decision_id, captured_monotonic_s=decision_time),
        _frame(frame_id=arm_id, captured_monotonic_s=arm_time),
    )

    assert result.disposition is CameraArmGuardDisposition.DISCARD_RESTART
    assert result.reason is CameraArmGuardReason.NON_FRESH_ARM_FRAME
    assert not result.safe_to_retain_guidance
    assert result.regions == ()


def test_same_frame_object_is_not_a_fresh_arm_capture() -> None:
    frame = _frame(frame_id=1, captured_monotonic_s=1.0)

    result = evaluate_camera_arm_guard(frame, frame)

    assert result.reason is CameraArmGuardReason.NON_FRESH_ARM_FRAME
    assert result.decision_payload_sha256 == result.arm_payload_sha256


def test_unsupported_decision_and_arm_geometry_or_format_discard() -> None:
    reviewed = _reviewed_payload()
    narrow = _frame(
        reviewed[: 1004 * 1078 * 4],
        frame_id=1,
        captured_monotonic_s=1.0,
        width=1004,
    )
    rgba = _frame(
        reviewed,
        frame_id=2,
        captured_monotonic_s=2.0,
        pixel_format=PixelFormat.RGBA8888,
    )
    supported_decision = _frame(frame_id=1, captured_monotonic_s=1.0)
    supported_arm = _frame(frame_id=2, captured_monotonic_s=2.0)

    decision_result = evaluate_camera_arm_guard(narrow, supported_arm)
    arm_result = evaluate_camera_arm_guard(supported_decision, rgba)

    assert decision_result.reason is CameraArmGuardReason.UNSUPPORTED_DECISION_FRAME
    assert arm_result.reason is CameraArmGuardReason.UNSUPPORTED_ARM_FRAME
    assert decision_result.disposition is CameraArmGuardDisposition.DISCARD_RESTART
    assert arm_result.disposition is CameraArmGuardDisposition.DISCARD_RESTART
    assert decision_result.regions == ()
    assert arm_result.regions == ()


def test_non_frame_type_boundary_rejects_programmer_error() -> None:
    frame = _frame(frame_id=1, captured_monotonic_s=1.0)

    with pytest.raises(TypeError, match="decision_frame"):
        evaluate_camera_arm_guard(cast(Any, None), frame)
    with pytest.raises(TypeError, match="arm_frame"):
        evaluate_camera_arm_guard(frame, cast(Any, object()))


def test_result_and_region_evidence_are_immutable_and_non_authoritative() -> None:
    result = evaluate_camera_arm_guard(
        _frame(frame_id=1, captured_monotonic_s=1.0),
        _frame(frame_id=2, captured_monotonic_s=2.0),
    )

    with pytest.raises(FrozenInstanceError):
        cast(Any, result).can_accept = True
    with pytest.raises(FrozenInstanceError):
        cast(Any, result.regions[0]).within_limit = False
    with pytest.raises(ValueError, match="frozen policy"):
        replace(result, excluded_regions=result.excluded_regions[:-1])
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        replace(result, arm_payload_sha256="0" * 63)
