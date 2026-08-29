"""Offline safety proof for Issue #31 world-only camera guidance."""

from __future__ import annotations

import gzip
import math
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any, cast

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.perception import (
    WideLandmarkSearch,
    WideRegistrationDiagnosis,
    WideSceneRegistrationAnalysis,
    load_varrock_east_iron_profile,
    varrock_east_iron_scene_excluded_regions,
)
from mining_automation.perception.production_profiles import (
    VARROCK_EAST_IRON_FIXED_UI_REGIONS,
)
from mining_automation.validation.camera_guidance import (
    CAMERA_GUIDANCE_ID,
    CAMERA_GUIDANCE_VERSION,
    CameraGuidanceAxis,
    CameraGuidanceDirection,
    CameraGuidanceDisposition,
    CameraGuidanceReason,
    _guidance_from_analysis,
    evaluate_varrock_east_camera_guidance,
)
from mining_automation.validation.client_readiness import GAMEPLAY_CHROME_POLICIES

_FIXTURE_ROOT = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "perception"
    / "varrock-east-iron-v1"
)


def _reviewed_frame(*, frame_id: int = 1) -> Frame:
    payload = gzip.decompress(
        (_FIXTURE_ROOT / "frames" / "available-01.raw.gz").read_bytes()
    )
    return Frame.from_raw(
        RawFrame(payload, 1005, 1078, PixelFormat.BGRA8888),
        frame_id=frame_id,
        captured_monotonic_s=float(frame_id),
    )


def _nearest_scaled_frame(frame: Frame, *, scale: float, frame_id: int) -> Frame:
    """Return a deterministic centre-scale raster without a test-only CV dependency."""

    centre_x = frame.width / 2.0
    centre_y = frame.height / 2.0
    source_x = tuple(
        math.floor((x - centre_x) / scale + centre_x + 0.5)
        for x in range(frame.width)
    )
    output = bytearray(len(frame.payload))
    for y in range(frame.height):
        source_y = math.floor((y - centre_y) / scale + centre_y + 0.5)
        if not 0 <= source_y < frame.height:
            continue
        source_row = source_y * frame.width * 4
        output_row = y * frame.width * 4
        for x, sampled_x in enumerate(source_x):
            if 0 <= sampled_x < frame.width:
                source_offset = source_row + sampled_x * 4
                output_offset = output_row + x * 4
                output[output_offset : output_offset + 4] = frame.payload[
                    source_offset : source_offset + 4
                ]
    return Frame.from_raw(
        RawFrame(bytes(output), frame.width, frame.height, frame.pixel_format),
        frame_id=frame_id,
        captured_monotonic_s=float(frame_id),
    )


def _transform_analysis(
    *,
    scale: float = 1.0,
    rotation_degrees: float = 0.0,
    shift_x: float = 0.0,
    shift_y: float = 0.0,
    keep_indexes: tuple[int, ...] = (0, 1, 2, 3, 4, 5),
    corrupt_index: int | None = None,
) -> WideSceneRegistrationAnalysis:
    profile = load_varrock_east_iron_profile()
    angle = math.radians(rotation_degrees)
    coefficient_a = scale * math.cos(angle)
    coefficient_b = scale * math.sin(angle)
    centre_x = profile.frame_width / 2.0
    centre_y = profile.frame_height / 2.0
    searches = []
    for index, landmark in enumerate(profile.scene_landmarks):
        if index not in keep_indexes:
            continue
        x, y, width, height = landmark.region
        reference_x = x + width / 2.0
        reference_y = y + height / 2.0
        relative_x = reference_x - centre_x
        relative_y = reference_y - centre_y
        observed_x = (
            centre_x
            + coefficient_a * relative_x
            - coefficient_b * relative_y
            + shift_x
        )
        observed_y = (
            centre_y
            + coefficient_b * relative_x
            + coefficient_a * relative_y
            + shift_y
        )
        offset_x = round(observed_x - reference_x)
        offset_y = round(observed_y - reference_y)
        if corrupt_index == index:
            offset_x += 24
            offset_y -= 20
        searches.append(
            WideLandmarkSearch(
                landmark_id=landmark.landmark_id,
                offset_x=offset_x,
                offset_y=offset_y,
                distance=landmark.maximum_distance / 4.0,
                maximum_distance=landmark.maximum_distance,
                matched=True,
                zone=landmark.zone(profile.frame_width, profile.frame_height),
                searched_offsets=1,
            )
        )
    return WideSceneRegistrationAnalysis(
        landmarks=tuple(searches),
        best_shared=None,
        diagnosis=WideRegistrationDiagnosis.CAMERA_TRANSFORM_NOT_TRANSLATION,
        detail="synthetic distributed transform",
        search_radius=96,
        coarse_step=4,
        refinement_radius=3,
    )


def _select(analysis: WideSceneRegistrationAnalysis):  # type: ignore[no-untyped-def]
    profile = load_varrock_east_iron_profile()
    return _guidance_from_analysis(
        analysis,
        profile,
        excluded_regions=varrock_east_iron_scene_excluded_regions(profile),
    )


@pytest.mark.parametrize(
    ("scale", "direction", "reason"),
    [
        (1.08, CameraGuidanceDirection.NEGATIVE, CameraGuidanceReason.ZOOM_SCALE_HIGH),
        (0.92, CameraGuidanceDirection.POSITIVE, CameraGuidanceReason.ZOOM_SCALE_LOW),
    ],
)
def test_distributed_scale_dominant_fit_selects_one_zoom_sign_only(
    scale: float,
    direction: CameraGuidanceDirection,
    reason: CameraGuidanceReason,
) -> None:
    result = _select(_transform_analysis(scale=scale))

    assert result.selector_id == CAMERA_GUIDANCE_ID
    assert result.selector_version == CAMERA_GUIDANCE_VERSION
    assert result.disposition is CameraGuidanceDisposition.ACTIONABLE
    assert result.reason is reason
    assert result.axis is CameraGuidanceAxis.ZOOM
    assert result.direction is direction
    assert result.fit is not None
    assert result.fit.landmark_count == 6
    assert len(result.fit.matched_zones) == 3
    assert not result.can_accept
    assert not result.can_validate_scene
    assert not result.can_expose_resources


@pytest.mark.parametrize(
    ("scale", "direction"),
    [
        (0.92, CameraGuidanceDirection.POSITIVE),
        (1.02, CameraGuidanceDirection.NEGATIVE),
    ],
)
def test_end_to_end_scaled_reviewed_pixels_recover_one_zoom_sign(
    scale: float,
    direction: CameraGuidanceDirection,
) -> None:
    transformed = _nearest_scaled_frame(_reviewed_frame(), scale=scale, frame_id=2)

    result = evaluate_varrock_east_camera_guidance(transformed)

    assert result.disposition is CameraGuidanceDisposition.ACTIONABLE
    assert result.axis is CameraGuidanceAxis.ZOOM
    assert result.direction is direction
    assert result.fit is not None
    assert result.fit.landmark_count >= 5
    assert len(result.fit.matched_zones) == 3
    assert not result.can_accept


def test_four_landmarks_or_two_zones_cannot_authorize_a_primitive() -> None:
    result = _select(
        _transform_analysis(scale=1.08, keep_indexes=(0, 1, 2, 3))
    )

    assert result.disposition is CameraGuidanceDisposition.INSUFFICIENT_GUIDANCE
    assert result.reason is CameraGuidanceReason.INSUFFICIENT_DISTRIBUTED_LANDMARKS
    assert result.axis is None
    assert result.direction is None


def test_incoherent_independent_minimum_cannot_authorize_direction() -> None:
    result = _select(_transform_analysis(scale=1.08, corrupt_index=5))

    assert result.disposition is CameraGuidanceDisposition.INSUFFICIENT_GUIDANCE
    assert result.reason is CameraGuidanceReason.INCOHERENT_TRANSFORM
    assert result.fit is not None
    assert result.fit.rms_residual_px > 3.0


def test_uncalibrated_rotation_axis_refuses_even_with_perfect_fit() -> None:
    result = _select(_transform_analysis(rotation_degrees=2.0))

    assert result.disposition is CameraGuidanceDisposition.INSUFFICIENT_GUIDANCE
    assert result.reason is CameraGuidanceReason.UNCALIBRATED_AXIS
    assert result.axis is None
    assert result.direction is None
    assert result.fit is not None
    assert abs(result.fit.rotation_degrees) > 1.5


def test_mixed_non_zoom_components_are_combined_conservatively() -> None:
    result = _select(
        _transform_analysis(
            scale=1.02,
            rotation_degrees=0.25,
            shift_x=4.0,
            shift_y=4.0,
        )
    )

    assert result.disposition is CameraGuidanceDisposition.INSUFFICIENT_GUIDANCE
    assert result.reason in {
        CameraGuidanceReason.AMBIGUOUS_AXIS,
        CameraGuidanceReason.UNCALIBRATED_AXIS,
    }
    assert result.axis is None
    assert result.direction is None


def test_supported_reference_is_inside_deadband_and_guidance_never_returns_pass() -> None:
    result = evaluate_varrock_east_camera_guidance(_reviewed_frame())

    assert result.disposition is CameraGuidanceDisposition.INSUFFICIENT_GUIDANCE
    assert result.reason is CameraGuidanceReason.WITHIN_DEADBAND
    assert result.axis is None
    assert result.direction is None
    assert not result.can_accept


def test_candidate_and_fixed_ui_mutations_cannot_change_guidance() -> None:
    frame = _reviewed_frame()
    baseline = evaluate_varrock_east_camera_guidance(frame)
    regions = baseline.excluded_regions
    assert tuple(policy.region for policy in GAMEPLAY_CHROME_POLICIES)[-1] in regions
    assert all(region in regions for region in VARROCK_EAST_IRON_FIXED_UI_REGIONS)
    payload = bytearray(frame.payload)
    for x, y, width, height in regions:
        for row in range(y, y + height):
            start = (row * frame.width + x) * 4
            payload[start : start + width * 4] = bytes((255, 0, 255, 255)) * width
    changed = Frame.from_raw(
        RawFrame(bytes(payload), frame.width, frame.height, frame.pixel_format),
        frame_id=2,
        captured_monotonic_s=2.0,
    )

    assert evaluate_varrock_east_camera_guidance(changed) == baseline


def test_blank_world_returns_insufficient_guidance_with_no_target_authority() -> None:
    frame = Frame.from_raw(
        RawFrame(bytes((0, 0, 0, 255)) * 1005 * 1078, 1005, 1078),
        frame_id=1,
        captured_monotonic_s=1.0,
    )

    result = evaluate_varrock_east_camera_guidance(frame)

    assert result.disposition is CameraGuidanceDisposition.INSUFFICIENT_GUIDANCE
    assert result.reason is CameraGuidanceReason.INSUFFICIENT_DISTRIBUTED_LANDMARKS
    assert result.axis is None
    assert result.direction is None
    assert not result.can_accept
    assert not result.can_validate_scene
    assert not result.can_expose_resources


def test_non_bgra_frame_refuses_before_world_search() -> None:
    frame = Frame.from_raw(
        RawFrame(bytes(1005 * 1078 * 4), 1005, 1078, PixelFormat.RGBA8888),
        frame_id=1,
        captured_monotonic_s=1.0,
    )

    result = evaluate_varrock_east_camera_guidance(frame)

    assert result.disposition is CameraGuidanceDisposition.INSUFFICIENT_GUIDANCE
    assert result.reason is CameraGuidanceReason.UNSUPPORTED_FRAME
    assert result.analysis is None


def test_guidance_evidence_is_immutable_and_rejects_non_zoom_authority() -> None:
    result = _select(_transform_analysis(scale=1.08))

    with pytest.raises(FrozenInstanceError):
        cast(Any, result).axis = CameraGuidanceAxis.YAW
    with pytest.raises(ValueError, match="only the calibrated zoom axis"):
        replace(result, axis=CameraGuidanceAxis.YAW)
    with pytest.raises(ValueError, match="cannot provide an axis or sign"):
        replace(
            result,
            disposition=CameraGuidanceDisposition.INSUFFICIENT_GUIDANCE,
        )
    with pytest.raises(ValueError, match="requires distributed transform evidence"):
        replace(result, analysis=None)
    assert result.fit is not None
    with pytest.raises(ValueError, match="scale-dominant"):
        replace(result, fit=replace(result.fit, scale=1.0))
