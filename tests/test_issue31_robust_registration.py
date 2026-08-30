"""Issue #31 read-only robust world-registration regressions."""

from __future__ import annotations

import json
import math
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.perception import (
    load_replay_dataset,
    materialize_gzip_replay_dataset,
)
from mining_automation.perception.scene_landmarks import MacroZone
from mining_automation.validation import robust_registration as registration
from mining_automation.validation.robust_registration import (
    DistortionEvidence,
    ModelFamily,
    RegistrationDisposition,
    RobustRegistrationEngine,
    analyze_robust_world_registration,
    trusted_robust_registration_exclusions,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE_MANIFEST = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "perception"
    / "varrock-east-iron-v1"
    / "manifest.json"
)
_WIDTH = 1005
_HEIGHT = 1078
_REQUIRED_ZONES = (
    MacroZone.NORTH_WEST,
    MacroZone.NORTH_EAST,
    MacroZone.SOUTH_WEST,
)


@pytest.fixture(scope="module")
def reviewed_frame(tmp_path_factory: pytest.TempPathFactory) -> Frame:
    destination = tmp_path_factory.mktemp("issue31-robust-registration")
    dataset = load_replay_dataset(
        materialize_gzip_replay_dataset(_FIXTURE_MANIFEST, destination)
    )
    return next(
        sample.frame for sample in dataset.samples if sample.case.case_id == "available-01"
    )


def _distributed_points() -> np.ndarray:
    points: list[tuple[float, float]] = []
    for base_x, base_y in ((80, 80), (600, 80), (80, 650)):
        for row in range(4):
            for column in range(5):
                points.append(
                    (
                        float(base_x + column * 70 + row * 3),
                        float(base_y + row * 85 + column * 2),
                    )
                )
    return np.asarray(points, dtype=np.float64)


def _transform(points: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    homogeneous = np.column_stack((points, np.ones(len(points), dtype=np.float64)))
    projected = homogeneous @ matrix.T
    return np.asarray(projected[:, :2] / projected[:, 2:3], dtype=np.float64)


def _candidate_models(
    source: np.ndarray, target: np.ndarray
) -> tuple[registration.ModelEvidence, ...]:
    return tuple(
        registration._evaluate_model(
            family,
            source,
            target,
            width=_WIDTH,
            height=_HEIGHT,
            required_zones=_REQUIRED_ZONES,
            mutual_matches=len(source),
            policy=registration.DEFAULT_ROBUST_REGISTRATION_POLICY,
        )
        for family in (
            ModelFamily.TRANSLATION,
            ModelFamily.SIMILARITY,
            ModelFamily.AFFINE,
            ModelFamily.HOMOGRAPHY,
        )
    )


@pytest.mark.parametrize(
    ("expected", "matrix"),
    (
        (
            ModelFamily.TRANSLATION,
            np.asarray(((1.0, 0.0, 4.0), (0.0, 1.0, -3.0), (0.0, 0.0, 1.0))),
        ),
        (
            ModelFamily.SIMILARITY,
            np.asarray(
                (
                    (1.0099446, -0.0105765, 4.0),
                    (0.0105765, 1.0099446, -3.0),
                    (0.0, 0.0, 1.0),
                )
            ),
        ),
        (
            ModelFamily.AFFINE,
            np.asarray(((1.01, 0.015, 2.0), (-0.01, 0.99, -2.0), (0.0, 0.0, 1.0))),
        ),
        (
            ModelFamily.HOMOGRAPHY,
            np.asarray(
                ((1.0, 0.004, 2.0), (-0.003, 1.0, -2.0), (0.00002, -0.00001, 1.0))
            ),
        ),
    ),
)
def test_lowest_adequate_model_is_selected_from_distributed_synthetic_points(
    expected: ModelFamily,
    matrix: np.ndarray,
) -> None:
    source = _distributed_points()
    models = _candidate_models(source, _transform(source, matrix))

    selected = next(model for model in models if model.adequate)

    assert selected.family is expected
    assert selected.inliers == 60
    assert selected.rejection_reasons == ()
    assert dict(selected.source_zone_inliers) == {
        MacroZone.NORTH_WEST: 20,
        MacroZone.NORTH_EAST: 20,
        MacroZone.SOUTH_WEST: 20,
    }
    assert dict(selected.target_zone_inliers) == dict(selected.source_zone_inliers)
    assert all(not model.adequate for model in models[: models.index(selected)])


def test_missing_south_west_evidence_rejects_otherwise_exact_transform() -> None:
    source = _distributed_points()[:40]
    target = source + np.asarray((4.0, -3.0))

    model = _candidate_models(source, target)[0]

    assert not model.adequate
    assert dict(model.source_zone_inliers) == {
        MacroZone.NORTH_WEST: 20,
        MacroZone.NORTH_EAST: 20,
        MacroZone.SOUTH_WEST: 0,
    }
    assert "source_zone_south_west_underrepresented" in model.rejection_reasons
    assert "target_zone_south_west_underrepresented" in model.rejection_reasons


def test_committed_reviewed_frame_selects_translation_and_has_no_authority(
    reviewed_frame: Frame,
) -> None:
    result = analyze_robust_world_registration(reviewed_frame, reviewed_frame)

    assert result.disposition is RegistrationDisposition.ACCEPTED
    assert result.selected_family is ModelFamily.TRANSLATION
    assert result.required_zones == _REQUIRED_ZONES
    assert result.correspondence.mutual_matches >= 50
    assert all(dict(result.correspondence.per_zone_mutual_matches)[zone] >= 10 for zone in _REQUIRED_ZONES)
    assert result.accepted
    assert not result.can_accept
    assert not result.can_validate_scene
    assert not result.can_expose_resources
    assert not result.diagnostic_registration_can_override_production
    assert result.as_dict()["authority"] == {
        "can_accept": False,
        "can_expose_resources": False,
        "can_validate_scene": False,
        "diagnostic_registration_can_override_production": False,
    }


def test_changes_inside_every_excluded_region_cannot_change_registration_evidence(
    reviewed_frame: Frame,
) -> None:
    pixels = np.frombuffer(reviewed_frame.payload, dtype=np.uint8).copy().reshape(
        reviewed_frame.height, reviewed_frame.width, 4
    )
    for index, (x, y, width, height) in enumerate(
        trusted_robust_registration_exclusions()
    ):
        pixels[y : y + height, x : x + width] = np.asarray(
            (
                (19 * index + 7) % 256,
                (43 * index + 11) % 256,
                (71 * index + 13) % 256,
                255,
            ),
            dtype=np.uint8,
        )
    changed = Frame.from_raw(
        RawFrame(
            pixels.tobytes(),
            reviewed_frame.width,
            reviewed_frame.height,
            reviewed_frame.pixel_format,
        ),
        frame_id=reviewed_frame.frame_id + 100,
        captured_monotonic_s=reviewed_frame.captured_monotonic_s + 100.0,
    )
    engine = RobustRegistrationEngine()

    unchanged_result = engine.analyze(reviewed_frame, reviewed_frame)
    changed_result = engine.analyze(reviewed_frame, changed)

    assert unchanged_result.correspondence == changed_result.correspondence
    assert unchanged_result.models == changed_result.models
    assert unchanged_result.selected_family is changed_result.selected_family
    assert unchanged_result.exclusion_fingerprint_sha256 == (
        changed_result.exclusion_fingerprint_sha256
    )
    assert unchanged_result.target.payload_sha256 != changed_result.target.payload_sha256


def test_malformed_payload_and_pixel_format_fail_closed_without_an_edge(
    reviewed_frame: Frame,
) -> None:
    malformed = replace(reviewed_frame, payload=b"\x00")
    wrong_format = replace(reviewed_frame, pixel_format=PixelFormat.RGBA8888)

    for observed in (malformed, wrong_format):
        result = analyze_robust_world_registration(reviewed_frame, observed)

        assert result.disposition is RegistrationDisposition.UNSUPPORTED_FRAME
        assert not result.accepted
        assert result.selected_family is None
        assert result.models == ()
        assert result.correspondence.balanced_matches == 0
        assert not result.can_validate_scene
        assert not result.can_expose_resources


def test_degenerate_and_nonfinite_evidence_remains_strict_json() -> None:
    repeated = np.repeat(np.asarray(((100.0, 100.0),)), 60, axis=0)
    degenerate = registration._evaluate_model(
        ModelFamily.HOMOGRAPHY,
        repeated,
        repeated,
        width=_WIDTH,
        height=_HEIGHT,
        required_zones=_REQUIRED_ZONES,
        mutual_matches=len(repeated),
        policy=registration.DEFAULT_ROBUST_REGISTRATION_POLICY,
    )
    nonfinite_distortion = DistortionEvidence(
        finite=False,
        orientation_preserved=False,
        minimum_local_scale=math.nan,
        maximum_local_scale=math.inf,
        maximum_local_scale_ratio=math.inf,
        maximum_local_condition=math.inf,
        overlap_fraction=math.nan,
        perspective_span=math.inf,
        passed=False,
    )

    assert not degenerate.adequate
    assert degenerate.forward_matrix is None
    assert degenerate.reverse_matrix is None
    json.dumps(degenerate.as_dict(), allow_nan=False, sort_keys=True)
    encoded = json.dumps(nonfinite_distortion.as_dict(), allow_nan=False, sort_keys=True)
    assert "NaN" not in encoded
    assert "Infinity" not in encoded
    assert all(
        value is None
        for key, value in nonfinite_distortion.as_dict().items()
        if key
        in {
            "minimum_local_scale",
            "maximum_local_scale",
            "maximum_local_scale_ratio",
            "maximum_local_condition",
            "overlap_fraction",
            "perspective_span",
        }
    )


def _feature_set(descriptors: np.ndarray) -> registration._Features:
    keypoints = (
        cv2.KeyPoint(100.0, 100.0, 8.0),
        cv2.KeyPoint(230.0, 200.0, 8.0),
        cv2.KeyPoint(620.0, 100.0, 8.0),
        cv2.KeyPoint(760.0, 220.0, 8.0),
        cv2.KeyPoint(100.0, 700.0, 8.0),
        cv2.KeyPoint(300.0, 850.0, 8.0),
    )
    return registration._Features(keypoints, np.asarray(descriptors, dtype=np.float32))


def test_matching_is_bidirectional_and_repeated_descriptors_create_no_edge() -> None:
    unique = np.zeros((6, 128), dtype=np.float32)
    for index in range(6):
        unique[index, index] = 1.0
    distinct = _feature_set(unique)

    mutual = registration._match_features(
        distinct,
        distinct,
        width=_WIDTH,
        height=_HEIGHT,
        required_zones=_REQUIRED_ZONES,
        ratio_threshold=0.72,
    )
    ambiguous_features = _feature_set(np.zeros((6, 128), dtype=np.float32))
    ambiguous = registration._match_features(
        ambiguous_features,
        ambiguous_features,
        width=_WIDTH,
        height=_HEIGHT,
        required_zones=_REQUIRED_ZONES,
        ratio_threshold=0.72,
    )

    assert mutual.evidence.forward_ratio_matches == 6
    assert mutual.evidence.reverse_ratio_matches == 6
    assert mutual.evidence.mutual_matches == 6
    assert mutual.evidence.balanced_matches == 6
    assert dict(mutual.evidence.per_zone_mutual_matches) == {
        MacroZone.NORTH_WEST: 2,
        MacroZone.NORTH_EAST: 2,
        MacroZone.SOUTH_WEST: 2,
    }
    assert ambiguous.evidence.forward_ratio_matches == 0
    assert ambiguous.evidence.reverse_ratio_matches == 0
    assert ambiguous.evidence.mutual_matches == 0
    assert ambiguous.evidence.balanced_matches == 0


def test_engine_cache_and_evidence_are_deterministic(
    reviewed_frame: Frame,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extraction_calls = 0
    original = registration._extract_features

    def counted_extract(
        frame: Frame,
        mask: np.ndarray,
        exclusions: tuple[tuple[int, int, int, int], ...],
        required_zones: tuple[MacroZone, ...],
    ) -> registration._Features:
        nonlocal extraction_calls
        extraction_calls += 1
        return original(frame, mask, exclusions, required_zones)

    monkeypatch.setattr(registration, "_extract_features", counted_extract)
    engine = RobustRegistrationEngine()

    first = engine.analyze(reviewed_frame, reviewed_frame)
    second = engine.analyze(reviewed_frame, reviewed_frame)

    assert extraction_calls == 1
    assert first == second
    assert first.as_dict() == second.as_dict()
    json.dumps(first.as_dict(), allow_nan=False, sort_keys=True)
    environment = registration.robust_registration_environment()
    assert environment["opencv_threads"] == 1
    assert environment["opencv_opencl_enabled"] is False


def test_production_imports_do_not_load_optional_vision_dependencies() -> None:
    script = """
import sys
import mining_automation
import mining_automation.capture
import mining_automation.perception
import mining_automation.validation
import mining_automation.validation.camera_evaluation
assert 'cv2' not in sys.modules
assert 'numpy' not in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=_REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
