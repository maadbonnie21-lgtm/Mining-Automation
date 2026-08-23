"""Deterministic regression coverage for Issue #22 diagnostics."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

import mining_automation.perception.scene_diagnostics as scene_diagnostics
from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.perception import (
    ReacquisitionDiagnosis,
    ResourceVisualState,
    analyze_scene_reacquisition,
    build_varrock_east_iron_detector,
    classify_reacquisition,
    compare_scene_frames,
    load_replay_dataset,
    load_varrock_east_iron_profile,
    materialize_gzip_replay_dataset,
)
from mining_automation.perception.scene_landmarks import (
    LandmarkMatch,
    MacroZone,
    SceneLandmarkProfile,
    SceneVerdict,
    SceneVerdictReason,
    describe_region,
    macro_zone_for_region,
)

FIXTURE_ROOT = (
    Path(__file__).resolve().parent / "fixtures" / "perception" / "varrock-east-iron-v1"
)
MANIFEST = FIXTURE_ROOT / "manifest.json"


def _load_validator_module() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "tools" / "validate_varrock_east_drift.py"
    specification = importlib.util.spec_from_file_location("issue22_validator", path)
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


validator = _load_validator_module()


@pytest.fixture(scope="module")
def reviewed_frame(tmp_path_factory: pytest.TempPathFactory) -> Frame:
    destination = tmp_path_factory.mktemp("issue22-reviewed-frame")
    dataset = load_replay_dataset(materialize_gzip_replay_dataset(MANIFEST, destination))
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


def _analyze(frame: Frame, *, radius: int = 4):  # type: ignore[no-untyped-def]
    profile = load_varrock_east_iron_profile()
    return analyze_scene_reacquisition(
        frame,
        profile.scene_landmarks,
        required_quorum=profile.minimum_landmark_quorum,
        required_zones=profile.minimum_landmark_zones,
        frame_width=profile.frame_width,
        frame_height=profile.frame_height,
        search_radius=radius,
        excluded_regions=tuple(candidate.region for candidate in profile.candidates),
    )


def _states(frame: Frame) -> set[str]:
    return {
        str(observation.evidence["state"])
        for observation in build_varrock_east_iron_detector().detect(frame)
    }


def _mutate_candidates(frame: Frame) -> Frame:
    profile = load_varrock_east_iron_profile()
    payload = bytearray(frame.payload)
    stride = frame.width * 4
    for candidate in profile.candidates:
        x, y, width, height = candidate.region
        for row in range(y, y + height):
            for column in range(x, x + width):
                offset = row * stride + column * 4
                payload[offset : offset + 4] = bytes((241, 3, 197, 255))
    return Frame.from_raw(
        RawFrame(bytes(payload), frame.width, frame.height, frame.pixel_format),
        frame_id=frame.frame_id + 1,
        captured_monotonic_s=frame.captured_monotonic_s + 1.0,
    )


def _synthetic_inconsistent_offsets() -> tuple[
    Frame, tuple[SceneLandmarkProfile, ...]
]:
    width = 240
    height = 240
    regions = (
        (20, 20, 32, 32),
        (72, 62, 32, 32),
        (146, 20, 32, 32),
        (190, 64, 32, 32),
        (20, 146, 32, 32),
        (74, 190, 32, 32),
    )
    offsets = ((-4, -4), (4, 4), (-4, 4), (4, -4), (0, 4), (4, 0))
    reference_payload = bytearray(bytes((0, 0, 0, 255)) * width * height)
    for index, (x, y, region_width, region_height) in enumerate(regions):
        cell_width = region_width // 4
        cell_height = region_height // 4
        for row in range(region_height):
            for column in range(region_width):
                cell = (row // cell_height) * 4 + column // cell_width
                gray = ((cell * (index * 2 + 3) + index * 5) % 17) * 15
                offset = ((y + row) * width + x + column) * 4
                reference_payload[offset : offset + 4] = bytes((gray, gray, gray, 255))
    reference = Frame.from_raw(
        RawFrame(bytes(reference_payload), width, height, PixelFormat.BGRA8888),
        frame_id=1,
        captured_monotonic_s=0.0,
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

    observed_payload = bytearray(bytes((0, 0, 0, 255)) * width * height)
    for region, (offset_x, offset_y) in zip(regions, offsets, strict=True):
        x, y, region_width, region_height = region
        for row in range(region_height):
            source_start = ((y + row) * width + x) * 4
            destination_start = (
                (y + offset_y + row) * width + x + offset_x
            ) * 4
            observed_payload[
                destination_start : destination_start + region_width * 4
            ] = reference.payload[source_start : source_start + region_width * 4]
    observed = Frame.from_raw(
        RawFrame(bytes(observed_payload), width, height, PixelFormat.BGRA8888),
        frame_id=2,
        captured_monotonic_s=1.0,
    )
    return observed, landmarks


def test_supported_view_validates_at_frozen_coordinates(reviewed_frame: Frame) -> None:
    analysis = _analyze(reviewed_frame)
    conclusion = classify_reacquisition(analysis)

    assert analysis.frozen.verdict.validated
    assert analysis.frozen.verdict.matched_count == 6
    assert len(analysis.frozen.verdict.matched_zones) == 3
    assert analysis.best_coherent.offset_x == 0
    assert analysis.best_coherent.offset_y == 0
    assert conclusion.diagnosis is ReacquisitionDiagnosis.SUPPORTED_VIEW


def test_small_coherent_offset_is_diagnosed_without_changing_production(
    reviewed_frame: Frame,
) -> None:
    shifted = _translate(reviewed_frame, 2, 0)
    analysis = _analyze(shifted)
    conclusion = classify_reacquisition(analysis)

    assert not analysis.frozen.verdict.validated
    assert analysis.best_coherent.verdict.validated
    assert (analysis.best_coherent.offset_x, analysis.best_coherent.offset_y) == (2, 0)
    assert conclusion.diagnosis is ReacquisitionDiagnosis.FROZEN_LANDMARKS_TOO_BRITTLE
    assert _states(shifted) == {ResourceVisualState.UNCERTAIN.value}


def test_conflicting_coherent_and_known_drift_evidence_is_inconclusive(
    reviewed_frame: Frame,
) -> None:
    shifted = _translate(reviewed_frame, 2, 0)
    profile = load_varrock_east_iron_profile()
    comparison = compare_scene_frames(
        shifted,
        shifted,
        profile.scene_landmarks,
        required_quorum=profile.minimum_landmark_quorum,
        required_zones=profile.minimum_landmark_zones,
        frame_width=profile.frame_width,
        frame_height=profile.frame_height,
    )

    conclusion = classify_reacquisition(
        _analyze(shifted),
        matching_drift=comparison,
        matching_drift_label="conflicting-drift.raw",
    )

    assert conclusion.diagnosis is ReacquisitionDiagnosis.INCONCLUSIVE
    assert "Conflicting structural evidence" in conclusion.detail


def test_coherent_offset_is_not_called_brittle_when_it_would_accept_drift(
    reviewed_frame: Frame,
) -> None:
    shifted = _translate(reviewed_frame, 2, 0)

    conclusion = classify_reacquisition(
        _analyze(shifted),
        bounded_drift_false_support_count=1,
    )

    assert conclusion.diagnosis is ReacquisitionDiagnosis.INCONCLUSIVE
    assert "registration is not safe evidence" in conclusion.detail


def test_coherent_offset_is_inconclusive_with_an_incomplete_drift_set(
    reviewed_frame: Frame,
) -> None:
    shifted = _translate(reviewed_frame, 2, 0)

    conclusion = classify_reacquisition(
        _analyze(shifted),
        bounded_drift_set_complete=False,
    )

    assert conclusion.diagnosis is ReacquisitionDiagnosis.INCONCLUSIVE
    assert "drift safety set is incomplete" in conclusion.detail


def test_known_drift_match_proves_camera_was_not_restored(
    reviewed_frame: Frame,
) -> None:
    drift = _translate(reviewed_frame, 16, 8)
    profile = load_varrock_east_iron_profile()
    analysis = _analyze(drift)
    comparison = compare_scene_frames(
        drift,
        drift,
        profile.scene_landmarks,
        required_quorum=profile.minimum_landmark_quorum,
        required_zones=profile.minimum_landmark_zones,
        frame_width=profile.frame_width,
        frame_height=profile.frame_height,
    )
    conclusion = classify_reacquisition(
        analysis,
        matching_drift=comparison,
        matching_drift_label="known-drift.raw",
    )

    assert not analysis.frozen.verdict.validated
    assert not analysis.best_coherent.verdict.validated
    assert comparison.verdict.validated
    assert comparison.verdict.matched_count == 6
    assert conclusion.diagnosis is ReacquisitionDiagnosis.CAMERA_NOT_ACTUALLY_RESTORED
    assert "known-drift.raw" in conclusion.detail
    assert _states(drift) == {ResourceVisualState.UNCERTAIN.value}


def test_independent_local_matches_never_form_a_scene_verdict() -> None:
    observed, landmarks = _synthetic_inconsistent_offsets()
    analysis = analyze_scene_reacquisition(
        observed,
        landmarks,
        required_quorum=5,
        required_zones=3,
        frame_width=observed.width,
        frame_height=observed.height,
        search_radius=4,
    )

    assert analysis.local_matched_count == 6
    assert len(analysis.local_matched_zones) == 3
    assert not analysis.best_coherent.verdict.validated
    assert analysis.best_coherent.verdict.matched_count < 5
    assert classify_reacquisition(analysis).diagnosis is ReacquisitionDiagnosis.INCONCLUSIVE


def test_candidate_pixels_cannot_change_scene_diagnosis(reviewed_frame: Frame) -> None:
    profile = load_varrock_east_iron_profile()
    mutated = _mutate_candidates(reviewed_frame)
    comparison = compare_scene_frames(
        reviewed_frame,
        mutated,
        profile.scene_landmarks,
        required_quorum=profile.minimum_landmark_quorum,
        required_zones=profile.minimum_landmark_zones,
        frame_width=profile.frame_width,
        frame_height=profile.frame_height,
    )

    assert comparison.verdict.validated
    assert comparison.verdict.matched_count == 6
    assert _analyze(mutated).frozen.verdict.validated


def test_search_envelope_rejects_candidate_or_sanitized_overlap(
    reviewed_frame: Frame,
) -> None:
    profile = load_varrock_east_iron_profile()
    landmark = profile.scene_landmarks[0]

    with pytest.raises(ValueError, match="search envelope overlaps excluded"):
        analyze_scene_reacquisition(
            reviewed_frame,
            profile.scene_landmarks,
            required_quorum=profile.minimum_landmark_quorum,
            required_zones=profile.minimum_landmark_zones,
            frame_width=profile.frame_width,
            frame_height=profile.frame_height,
            excluded_regions=(landmark.region,),
        )


def test_search_envelope_may_not_cross_a_frozen_macro_zone() -> None:
    frame = Frame.from_raw(
        RawFrame(bytes(100 * 100 * 4), 100, 100, PixelFormat.BGRA8888),
        frame_id=1,
        captured_monotonic_s=0.0,
    )
    landmark = SceneLandmarkProfile(
        landmark_id="near-zone-edge",
        region=(33, 10, 32, 32),
        reference_descriptor=tuple(0.0 for _ in range(16)),
        maximum_distance=0.12,
        macro_zone=MacroZone.NORTH_WEST,
    )

    with pytest.raises(ValueError, match="crosses a macro-zone boundary"):
        analyze_scene_reacquisition(
            frame,
            (landmark,),
            required_quorum=1,
            required_zones=1,
            frame_width=frame.width,
            frame_height=frame.height,
            search_radius=4,
        )


def test_valid_scene_evidence_outranks_a_larger_clustered_match_count() -> None:
    profile = load_varrock_east_iron_profile()
    landmarks_by_id = {item.landmark_id: item for item in profile.scene_landmarks}
    valid_ids = {
        profile.scene_landmarks[index].landmark_id for index in (0, 2, 4, 5)
    }
    valid_matches = tuple(
        LandmarkMatch(
            landmark_id=item.landmark_id,
            matched=item.landmark_id in valid_ids,
            distance=0.0 if item.landmark_id in valid_ids else 1.0,
            zone=item.macro_zone,
        )
        for item in profile.scene_landmarks
    )
    invalid_matches = tuple(
        LandmarkMatch(
            landmark_id=item.landmark_id,
            matched=index < 5,
            distance=0.0 if index < 5 else 1.0,
            zone=MacroZone.NORTH_WEST,
        )
        for index, item in enumerate(profile.scene_landmarks)
    )
    valid = scene_diagnostics.SceneOffsetEvaluation(
        1,
        0,
        SceneVerdict(
            validated=True,
            reason=SceneVerdictReason.VALIDATED,
            matches=valid_matches,
            matched_count=4,
            required_quorum=4,
            matched_zones=(
                MacroZone.NORTH_WEST,
                MacroZone.NORTH_EAST,
                MacroZone.SOUTH_WEST,
            ),
            required_zones=3,
        ),
    )
    invalid = scene_diagnostics.SceneOffsetEvaluation(
        0,
        0,
        SceneVerdict(
            validated=False,
            reason=SceneVerdictReason.INSUFFICIENT_SPATIAL_SPREAD,
            matches=invalid_matches,
            matched_count=5,
            required_quorum=4,
            matched_zones=(MacroZone.NORTH_WEST,),
            required_zones=3,
        ),
    )

    selected = min(
        (invalid, valid),
        key=lambda item: scene_diagnostics._evaluation_key(item, landmarks_by_id),
    )
    assert selected is valid
    nearest = validator._nearest_comparison(
        [
            validator.NamedComparison(
                Path("invalid.raw"),
                scene_diagnostics.SceneFrameComparison(invalid.verdict, 0.0),
            ),
            validator.NamedComparison(
                Path("valid.raw"),
                scene_diagnostics.SceneFrameComparison(valid.verdict, 1.0),
            ),
        ]
    )
    assert nearest.path == Path("valid.raw")


def test_analysis_is_deterministic_and_radius_is_bounded(reviewed_frame: Frame) -> None:
    assert _analyze(reviewed_frame) == _analyze(reviewed_frame)
    with pytest.raises(ValueError, match="search_radius"):
        _analyze(reviewed_frame, radius=17)


def _write_raw(path: Path, frame: Frame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(frame.payload)


def test_combined_cli_passes_realistic_drift_and_restored_case(
    reviewed_frame: Frame,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    drift_directory = tmp_path / "drift"
    drift_path = drift_directory / "drift.raw"
    restored_path = tmp_path / "restored.raw"
    report_path = tmp_path / "report.json"
    _write_raw(drift_path, _translate(reviewed_frame, 16, 8))
    _write_raw(restored_path, reviewed_frame)
    monkeypatch.setattr(validator, "_EXPECTED_DRIFT_FRAME_COUNT", 1)
    monkeypatch.setattr(validator, "DEFAULT_DIAGNOSTIC_SEARCH_RADIUS", 0)

    exit_code = validator.main(
        [
            "--drift-frames",
            str(drift_directory),
            "--restored-frame",
            str(restored_path),
            "--report",
            str(report_path),
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "DRIFT-SET RESULT: PASS" in output
    assert "RESTORED-FRAME RESULT: PASS" in output
    assert "distance  threshold  status" in output
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["drift_set"]["false_definitive_targets"] == 0
    assert report["restored_frame"]["passed"] is True
    assert report["overall_passed"] is True
    frozen_scene = report["restored_frame"]["scene"]["frozen_coordinate_scene"]
    assert frozen_scene["matched_zones"] == [
        "north_west",
        "north_east",
        "south_west",
    ]
    assert frozen_scene["reason"] == "scene_validated"
    assert all(
        {"landmark_id", "zone", "distance", "threshold", "matched"} <= set(item)
        for item in frozen_scene["landmarks"]
    )

    count_report = tmp_path / "count-mismatch.json"
    monkeypatch.setattr(validator, "_EXPECTED_DRIFT_FRAME_COUNT", 2)
    assert (
        validator.main(
            [
                "--drift-frames",
                str(drift_directory),
                "--restored-frame",
                str(restored_path),
                "--report",
                str(count_report),
            ]
        )
        == 1
    )
    count_payload = json.loads(count_report.read_text(encoding="utf-8"))
    assert count_payload["drift_set"]["frame_count_matches"] is False
    assert count_payload["drift_set"]["passed"] is False
    assert count_payload["overall_passed"] is False
    capsys.readouterr()


def test_combined_cli_identifies_stored_drift_view_as_not_restored(
    reviewed_frame: Frame,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    unsupported = _translate(reviewed_frame, 16, 8)
    drift_directory = tmp_path / "drift"
    drift_path = drift_directory / "known-drift.raw"
    restored_path = tmp_path / "claimed-restored.raw"
    _write_raw(drift_path, unsupported)
    _write_raw(restored_path, unsupported)
    monkeypatch.setattr(validator, "_EXPECTED_DRIFT_FRAME_COUNT", 1)
    monkeypatch.setattr(validator, "DEFAULT_DIAGNOSTIC_SEARCH_RADIUS", 0)

    exit_code = validator.main(
        [
            "--drift-frames",
            str(drift_directory),
            "--restored-frame",
            str(restored_path),
        ]
    )

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "DIAGNOSIS: camera_not_actually_restored" in output
    assert "NEAREST KNOWN DRIFT VIEW (known-drift.raw)" in output
    assert "restored-vs-drift structural matches: 1/1" in output
    assert "RESTORED-FRAME RESULT: FAIL" in output
    assert "NEXT EVIDENCE: capture one fresh frame" in output


def test_validator_rejects_malformed_frames_and_unsafe_shortcuts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    frame_directory = tmp_path / "frames"
    frame_directory.mkdir()
    (frame_directory / "bad.raw").write_bytes(b"not-a-frame")

    assert validator.main(["--frames", str(frame_directory)]) == 2
    assert "payload is" in capsys.readouterr().err
    assert (
        validator.main(
            [
                "--frames",
                str(frame_directory),
                "--restored-frame",
                str(frame_directory / "bad.raw"),
                "--limit",
                "1",
            ]
        )
        == 2
    )
    assert "all 36 drift frames are required" in capsys.readouterr().err


def test_validator_requires_the_complete_resource_observation_set() -> None:
    with pytest.raises(ValueError, match="omitted expected resources"):
        validator._extract_states((), ("expected-resource",))

    duplicate = (
        SimpleNamespace(evidence={"resource_id": "rock", "state": "uncertain"}),
        SimpleNamespace(evidence={"resource_id": "rock", "state": "uncertain"}),
    )
    with pytest.raises(ValueError, match="duplicate resource_id"):
        validator._extract_states(duplicate, ("rock",))


def test_legacy_frames_alias_remains_available() -> None:
    args = validator.build_parser().parse_args(["--frames", "diagnostics/example"])
    assert args.drift_frames == Path("diagnostics/example")
    assert args.restored_frame is None


def test_legacy_report_preserves_original_fields(
    reviewed_frame: Frame,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    frame_directory = tmp_path / "frames"
    _write_raw(frame_directory / "supported.raw", reviewed_frame)
    report_path = tmp_path / "legacy-report.json"
    monkeypatch.setattr(validator, "DEFAULT_DIAGNOSTIC_SEARCH_RADIUS", 0)

    assert (
        validator.main(
            [
                "--frames",
                str(frame_directory),
                "--expect",
                "definitive",
                "--limit",
                "1",
                "--report",
                str(report_path),
            ]
        )
        == 0
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert {
        "profile_id",
        "schema_version",
        "expectation",
        "frames_total",
        "frames_passed",
        "false_definitive_targets",
        "results",
    } <= set(report)
    assert {
        "frame",
        "ok",
        "states",
        "definitive_targets",
        "landmarks_matched",
        "reason",
        "landmark_distances",
    } <= set(report["results"][0])
    assert report["results"][0]["reason"] == "available_signature_matched"
    assert set(report["results"][0]["landmark_distances"]) == {
        item.landmark_id for item in load_varrock_east_iron_profile().scene_landmarks
    }
    assert "scene verdict: VALIDATED" in capsys.readouterr().out
