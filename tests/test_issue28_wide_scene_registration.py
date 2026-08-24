"""Deterministic regression coverage for Issue #28 wide scene registration."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.perception import (
    WideRegistrationDiagnosis,
    analyze_wide_scene_registration,
    load_replay_dataset,
    load_varrock_east_iron_profile,
    materialize_gzip_replay_dataset,
)
from mining_automation.perception.scene_landmarks import (
    MacroZone,
    SceneLandmarkProfile,
    describe_region,
    macro_zone_for_region,
)

_FIXTURE_ROOT = (
    Path(__file__).resolve().parent / "fixtures" / "perception" / "varrock-east-iron-v1"
)
_MANIFEST = _FIXTURE_ROOT / "manifest.json"


def _load_tool_module() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "tools" / "diagnose_varrock_east_wide.py"
    specification = importlib.util.spec_from_file_location("issue28_wide_tool", path)
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


_tool = _load_tool_module()


@pytest.fixture(scope="module")
def reviewed_frame(tmp_path_factory: pytest.TempPathFactory) -> Frame:
    destination = tmp_path_factory.mktemp("issue28-reviewed-frame")
    dataset = load_replay_dataset(materialize_gzip_replay_dataset(_MANIFEST, destination))
    return next(
        sample.frame for sample in dataset.samples if sample.case.case_id == "available-01"
    )


def _translate(frame: Frame, offset_x: int, offset_y: int) -> Frame:
    stride = frame.width * frame.pixel_format.bytes_per_pixel
    source = frame.payload
    translated = bytearray(len(source))
    for y in range(frame.height):
        source_y = (y - offset_y) % frame.height
        for x in range(frame.width):
            source_x = (x - offset_x) % frame.width
            destination_offset = y * stride + x * 4
            source_offset = source_y * stride + source_x * 4
            translated[destination_offset : destination_offset + 4] = source[
                source_offset : source_offset + 4
            ]
    return Frame.from_raw(
        RawFrame(bytes(translated), frame.width, frame.height, frame.pixel_format),
        frame_id=frame.frame_id + 1,
        captured_monotonic_s=frame.captured_monotonic_s + 1.0,
    )


def _blank_frame(width: int, height: int, *, frame_id: int = 1) -> Frame:
    payload = bytes((0, 0, 0, 255)) * width * height
    return Frame.from_raw(
        RawFrame(payload, width, height, PixelFormat.BGRA8888),
        frame_id=frame_id,
        captured_monotonic_s=float(frame_id),
    )


def _paint_pattern(
    payload: bytearray,
    *,
    frame_width: int,
    region: tuple[int, int, int, int],
    seed: int,
) -> None:
    x, y, width, height = region
    cell_width = width // 4
    cell_height = height // 4
    for row in range(height):
        for column in range(width):
            cell = (row // cell_height) * 4 + column // cell_width
            gray = ((cell * (seed * 2 + 3) + seed * 7 + 1) % 17) * 15
            offset = ((y + row) * frame_width + x + column) * 4
            payload[offset : offset + 4] = bytes((gray, gray, gray, 255))


def _copy_region(
    source: Frame,
    target: bytearray,
    *,
    region: tuple[int, int, int, int],
    offset_x: int,
    offset_y: int,
) -> None:
    x, y, width, height = region
    for row in range(height):
        source_start = ((y + row) * source.width + x) * 4
        target_start = (
            (y + offset_y + row) * source.width + x + offset_x
        ) * 4
        target[target_start : target_start + width * 4] = source.payload[
            source_start : source_start + width * 4
        ]


def _synthetic_landmarks() -> tuple[Frame, tuple[SceneLandmarkProfile, ...]]:
    width = 320
    height = 320
    regions = (
        (40, 40, 32, 32),
        (94, 96, 32, 32),
        (208, 40, 32, 32),
        (248, 102, 32, 32),
        (40, 208, 32, 32),
        (104, 248, 32, 32),
    )
    payload = bytearray(_blank_frame(width, height).payload)
    for index, region in enumerate(regions, start=1):
        _paint_pattern(
            payload,
            frame_width=width,
            region=region,
            seed=index,
        )
    reference = Frame.from_raw(
        RawFrame(bytes(payload), width, height, PixelFormat.BGRA8888),
        frame_id=10,
        captured_monotonic_s=10.0,
    )
    landmarks = tuple(
        SceneLandmarkProfile(
            landmark_id=f"synthetic-{index}",
            region=region,
            reference_descriptor=describe_region(reference, region),
            maximum_distance=0.000001,
            macro_zone=macro_zone_for_region(region, width, height),
        )
        for index, region in enumerate(regions)
    )
    return reference, landmarks


def _observed_with_offsets(
    reference: Frame,
    landmarks: tuple[SceneLandmarkProfile, ...],
    offsets: tuple[tuple[int, int], ...],
) -> Frame:
    payload = bytearray(_blank_frame(reference.width, reference.height).payload)
    for landmark, (offset_x, offset_y) in zip(landmarks, offsets, strict=True):
        _copy_region(
            reference,
            payload,
            region=landmark.region,
            offset_x=offset_x,
            offset_y=offset_y,
        )
    return Frame.from_raw(
        RawFrame(
            bytes(payload),
            reference.width,
            reference.height,
            reference.pixel_format,
        ),
        frame_id=reference.frame_id + 1,
        captured_monotonic_s=reference.captured_monotonic_s + 1.0,
    )


def test_wide_search_recovers_translation_beyond_narrow_radius(
    reviewed_frame: Frame,
) -> None:
    profile = load_varrock_east_iron_profile()
    shifted = _translate(reviewed_frame, 12, 8)

    analysis = analyze_wide_scene_registration(
        shifted,
        profile.scene_landmarks,
        required_quorum=profile.minimum_landmark_quorum,
        required_zones=profile.minimum_landmark_zones,
        frame_width=profile.frame_width,
        frame_height=profile.frame_height,
        search_radius=16,
        coarse_step=4,
        refinement_radius=3,
        excluded_regions=tuple(candidate.region for candidate in profile.candidates),
    )

    assert analysis.diagnosis is WideRegistrationDiagnosis.LARGER_COHERENT_TRANSLATION
    assert analysis.best_shared is not None
    assert analysis.best_shared.validated
    assert (analysis.best_shared.offset_x, analysis.best_shared.offset_y) == (12, 8)
    assert analysis.best_shared.matched_count >= profile.minimum_landmark_quorum
    assert len(analysis.best_shared.matched_zones) >= profile.minimum_landmark_zones


def test_inconsistent_landmark_offsets_are_not_promoted_to_translation() -> None:
    reference, landmarks = _synthetic_landmarks()
    observed = _observed_with_offsets(
        reference,
        landmarks,
        ((-8, -4), (8, 4), (-8, 8), (8, -8), (0, 8), (8, 0)),
    )

    analysis = analyze_wide_scene_registration(
        observed,
        landmarks,
        required_quorum=5,
        required_zones=3,
        frame_width=observed.width,
        frame_height=observed.height,
        search_radius=12,
        coarse_step=4,
        refinement_radius=3,
    )

    assert analysis.matched_count == 6
    assert len(analysis.matched_zones) == 3
    assert analysis.best_shared is not None
    assert not analysis.best_shared.validated
    assert analysis.diagnosis is WideRegistrationDiagnosis.CAMERA_TRANSFORM_NOT_TRANSLATION


def test_too_few_recoverable_landmarks_stays_insufficient() -> None:
    reference, landmarks = _synthetic_landmarks()
    observed = _observed_with_offsets(
        reference,
        landmarks[:2],
        ((8, 0), (8, 0)),
    )

    analysis = analyze_wide_scene_registration(
        observed,
        landmarks,
        required_quorum=5,
        required_zones=3,
        frame_width=observed.width,
        frame_height=observed.height,
        search_radius=12,
        coarse_step=4,
        refinement_radius=3,
    )

    assert analysis.matched_count == 2
    assert analysis.diagnosis is WideRegistrationDiagnosis.INSUFFICIENT_REGISTRATION_EVIDENCE


def test_excluded_shifted_region_cannot_be_used_as_registration_evidence() -> None:
    reference, landmarks = _synthetic_landmarks()
    landmark = landmarks[0]
    observed = _observed_with_offsets(reference, (landmark,), ((12, 0),))
    x, y, width, height = landmark.region
    forbidden = (x + 12, y, width, height)

    analysis = analyze_wide_scene_registration(
        observed,
        (landmark,),
        required_quorum=1,
        required_zones=1,
        frame_width=observed.width,
        frame_height=observed.height,
        search_radius=16,
        coarse_step=4,
        refinement_radius=3,
        excluded_regions=(forbidden,),
    )

    assert not analysis.landmarks[0].matched
    assert (analysis.landmarks[0].offset_x, analysis.landmarks[0].offset_y) != (12, 0)


def test_zone_crossing_exact_match_is_rejected() -> None:
    width = 200
    height = 200
    region = (80, 40, 32, 32)
    payload = bytearray(_blank_frame(width, height).payload)
    _paint_pattern(payload, frame_width=width, region=region, seed=3)
    reference = Frame.from_raw(
        RawFrame(bytes(payload), width, height, PixelFormat.BGRA8888),
        frame_id=1,
        captured_monotonic_s=1.0,
    )
    landmark = SceneLandmarkProfile(
        landmark_id="near-zone-edge",
        region=region,
        reference_descriptor=describe_region(reference, region),
        maximum_distance=0.000001,
        macro_zone=MacroZone.NORTH_WEST,
    )
    observed = _observed_with_offsets(reference, (landmark,), ((12, 0),))

    analysis = analyze_wide_scene_registration(
        observed,
        (landmark,),
        required_quorum=1,
        required_zones=1,
        frame_width=width,
        frame_height=height,
        search_radius=16,
        coarse_step=4,
        refinement_radius=3,
    )

    assert not analysis.landmarks[0].matched


def test_equal_matches_use_deterministic_offset_tie_break() -> None:
    width = 240
    height = 180
    region = (100, 60, 32, 32)
    payload = bytearray(_blank_frame(width, height).payload)
    _paint_pattern(payload, frame_width=width, region=region, seed=5)
    reference = Frame.from_raw(
        RawFrame(bytes(payload), width, height, PixelFormat.BGRA8888),
        frame_id=1,
        captured_monotonic_s=1.0,
    )
    landmark = SceneLandmarkProfile(
        landmark_id="tie-break",
        region=region,
        reference_descriptor=describe_region(reference, region),
        maximum_distance=0.000001,
        macro_zone=macro_zone_for_region(region, width, height),
    )
    observed_payload = bytearray(_blank_frame(width, height).payload)
    _copy_region(reference, observed_payload, region=region, offset_x=-20, offset_y=0)
    _copy_region(reference, observed_payload, region=region, offset_x=20, offset_y=0)
    observed = Frame.from_raw(
        RawFrame(bytes(observed_payload), width, height, PixelFormat.BGRA8888),
        frame_id=2,
        captured_monotonic_s=2.0,
    )

    analysis = analyze_wide_scene_registration(
        observed,
        (landmark,),
        required_quorum=1,
        required_zones=1,
        frame_width=width,
        frame_height=height,
        search_radius=24,
        coarse_step=4,
        refinement_radius=3,
    )

    assert analysis.landmarks[0].matched
    assert (analysis.landmarks[0].offset_x, analysis.landmarks[0].offset_y) == (-20, 0)
    assert analysis == analyze_wide_scene_registration(
        observed,
        (landmark,),
        required_quorum=1,
        required_zones=1,
        frame_width=width,
        frame_height=height,
        search_radius=24,
        coarse_step=4,
        refinement_radius=3,
    )


def test_one_command_tool_writes_deterministic_report(
    reviewed_frame: Frame,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    shifted = _translate(reviewed_frame, 12, 8)
    frame_path = tmp_path / "fresh.raw"
    report_path = tmp_path / "wide-report.json"
    frame_path.write_bytes(shifted.payload)

    exit_code = _tool.main(
        [
            "--frame",
            str(frame_path),
            "--radius",
            "16",
            "--report",
            str(report_path),
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "WIDE SCENE REGISTRATION -- DIAGNOSTIC ONLY" in output
    assert "WIDE DIAGNOSIS: larger_coherent_translation" in output
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["diagnosis"]["code"] == "larger_coherent_translation"
    assert payload["best_shared_offset"]["validated"] is True
    assert payload["production_decision_unchanged"] is True
