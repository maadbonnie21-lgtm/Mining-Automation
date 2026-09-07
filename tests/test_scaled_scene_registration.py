from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.perception.production_profiles import load_varrock_east_iron_profile
from mining_automation.perception.resource import ProfiledResourceDetector
from mining_automation.perception.scaled_scene_registration import (
    ScaledLandmarkMatch,
    fit_scaled_landmarks,
    register_scaled_scene,
)
from mining_automation.perception.scene_landmarks import (
    MacroZone,
    SceneLandmarkProfile,
    describe_region,
    evaluate_scene,
)

REGIONS = (
    (80, 200, 48, 48),
    (300, 300, 48, 48),
    (520, 300, 48, 48),
    (680, 350, 48, 48),
    (200, 620, 48, 48),
    (200, 700, 48, 48),
)
ZONES = (
    MacroZone.NORTH_WEST,
    MacroZone.NORTH_WEST,
    MacroZone.NORTH_EAST,
    MacroZone.NORTH_EAST,
    MacroZone.SOUTH_WEST,
    MacroZone.SOUTH_WEST,
)


def _frame(pixels: np.ndarray, frame_id: int = 1) -> Frame:
    return Frame.from_raw(
        RawFrame(pixels.tobytes(), 1005, 1078, PixelFormat.BGRA8888),
        frame_id=frame_id,
        captured_monotonic_s=float(frame_id),
    )


def _synthetic() -> tuple[Frame, Frame, ProfiledResourceDetector]:
    rng = np.random.default_rng(782091)
    reference = np.zeros((1078, 1005, 4), dtype=np.uint8)
    scaled = reference.copy()
    for x, y, _, _ in REGIONS:
        cells = rng.integers(25, 225, (4, 4), dtype=np.uint8)
        source_patch = np.repeat(np.repeat(cells, 12, axis=0), 12, axis=1)
        target_patch = np.repeat(np.repeat(cells, 10, axis=0), 10, axis=1)
        reference[y : y + 48, x : x + 48, :3] = source_patch[:, :, None]
        nx = round(0.90 * (x + 24) + 42 - 20)
        ny = round(0.85 * (y + 24) + 75 - 20)
        scaled[ny : ny + 40, nx : nx + 40, :3] = target_patch[:, :, None]
    before, after = _frame(reference), _frame(scaled, 2)
    landmarks = tuple(
        SceneLandmarkProfile(str(i), reg, describe_region(before, reg), 0.12, macro_zone=zone)
        for i, (reg, zone) in enumerate(zip(REGIONS, ZONES, strict=True))
    )
    profile = replace(
        load_varrock_east_iron_profile(),
        scene_landmarks=landmarks,
        minimum_landmark_quorum=5,
        minimum_landmark_zones=3,
    )
    return before, after, ProfiledResourceDetector(profile, version="test")


def test_scale_recovery_keeps_original_descriptors_and_thresholds() -> None:
    _, after, detector = _synthetic()
    result = register_scaled_scene(after, detector)
    assert result is not None
    registered, evidence = result
    assert evidence["matched"] == 6 and len(evidence["zones"]) == 3
    for original, current in zip(
        detector.profile.scene_landmarks, registered.profile.scene_landmarks, strict=True
    ):
        assert current.reference_descriptor == original.reference_descriptor
        assert current.maximum_distance == original.maximum_distance == 0.12
        assert current.macro_zone == original.macro_zone
    verdict = evaluate_scene(
        after,
        registered.profile.scene_landmarks,
        required_quorum=5,
        required_zones=3,
        frame_width=1005,
        frame_height=1078,
    )
    assert verdict.validated


def test_featureless_and_wrong_geometry_cannot_register() -> None:
    _, _, detector = _synthetic()
    assert (
        register_scaled_scene(_frame(np.zeros((1078, 1005, 4), dtype=np.uint8)), detector) is None
    )
    wrong = Frame.from_raw(
        RawFrame(bytes(1005 * 687 * 4), 1005, 687, PixelFormat.BGRA8888),
        frame_id=3,
        captured_monotonic_s=3.0,
    )
    assert register_scaled_scene(wrong, detector) is None


def _matches() -> tuple[ScaledLandmarkMatch, ...]:
    _, _, detector = _synthetic()
    return tuple(
        ScaledLandmarkMatch(
            lm,
            (
                round(0.9 * (lm.region[0] + 24) + 42 - 20),
                round(0.85 * (lm.region[1] + 24) + 75 - 20),
                40,
                40,
            ),
            0.04,
        )
        for lm in detector.profile.scene_landmarks
    )


def test_five_of_six_all_zones_still_works() -> None:
    matches = _matches()
    assert fit_scaled_landmarks(matches) is not None
    assert fit_scaled_landmarks(matches[1:]) is not None
    assert fit_scaled_landmarks(matches[:4]) is None
    assert fit_scaled_landmarks(tuple(replace(m, distance=0.121) for m in matches)) is None


def test_inconsistent_and_reflected_geometry_rejected() -> None:
    matches = _matches()
    bad = replace(matches[-1], region=(360, 800, 40, 40))
    assert fit_scaled_landmarks((*matches[:-1], bad)) is None
    reflected = tuple(replace(m, region=(700 - m.region[0], m.region[1], 40, 40)) for m in matches)
    assert fit_scaled_landmarks(reflected) is None


def test_no_zone_spread_rejected() -> None:
    matches = tuple(
        replace(m, landmark=replace(m.landmark, macro_zone=MacroZone.NORTH_WEST))
        for m in _matches()
    )
    assert fit_scaled_landmarks(matches) is None


def test_original_three_ore_failure_replayed_without_changing_pixels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = os.environ.get("MINING_REAL_REPLAY_ROOT")
    if not path:
        pytest.skip("private original live evidence stays local; set MINING_REAL_REPLAY_ROOT")
    root = Path(path)
    monkeypatch.chdir(root)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    from run_three_rock_continuous_proof import (
        build_pose_detectors,
        evaluate_resource,
        frame_from_path,
        make_epoch,
    )

    saved = (
        root / "diagnostics/mining-to-full-20260906-162731-70a27099/00056-iteration-04-clean.bgra"
    )
    frame = frame_from_path(saved, 56)
    assert (
        hashlib.sha256(frame.payload).hexdigest()
        == "b095291d95f550893b28eb2069a374176a1cae6f00ae71740818d8e713441bc2"
    )
    detectors = build_pose_detectors()
    resource, pose, diagnosis = evaluate_resource(
        frame,
        make_epoch(frame, 1, "original-replay"),
        detectors,
        frozenset(),
        {"pose": None, "detector": None},
    )
    assert resource.view.value == "supported" and pose == "at_center"
    assert diagnosis["software_registration"]["matched"] == 6
    assert len(diagnosis["software_registration"]["zones"]) == 3
    assert any(item.available is True for item in resource.resources)


def test_existing_exact_pose_does_not_call_scaled_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    from run_three_rock_continuous_proof import evaluate_resource, make_epoch

    from mining_automation.perception import scaled_scene_registration

    before, _, detector = _synthetic()

    def forbidden(*args: object) -> None:
        raise AssertionError("working exact pose must not enter new fallback")

    monkeypatch.setattr(scaled_scene_registration, "register_scaled_scene", forbidden)
    resource, pose, _ = evaluate_resource(
        before,
        make_epoch(before, 1, "exact"),
        {"existing": detector},
        frozenset(),
        {"pose": None, "detector": None},
    )
    assert resource.view.value == "supported" and pose == "existing"


def test_two_valid_exact_poses_remain_ambiguous(monkeypatch: pytest.MonkeyPatch) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    from run_three_rock_continuous_proof import evaluate_resource, make_epoch

    before, _, detector = _synthetic()
    resource, pose, _ = evaluate_resource(
        before,
        make_epoch(before, 1, "ambiguous"),
        {"a": detector, "b": detector},
        frozenset(),
        {"pose": None, "detector": None},
    )
    assert resource.view.value == "unsupported" and pose is None
    assert not resource.resources
