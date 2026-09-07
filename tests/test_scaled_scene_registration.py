from __future__ import annotations

from dataclasses import replace

import numpy as np

from mining_automation.perception.scaled_scene_registration import (
    ScaledLandmarkMatch,
    fit_scaled_landmarks,
)
from mining_automation.perception.scene_landmarks import MacroZone, SceneLandmarkProfile


def _landmark(
    landmark_id: str,
    region: tuple[int, int, int, int],
    zone: MacroZone,
) -> SceneLandmarkProfile:
    return SceneLandmarkProfile(
        landmark_id=landmark_id,
        region=region,
        reference_descriptor=(0.0,) * 16,
        maximum_distance=0.12,
        grid=4,
        macro_zone=zone,
    )


def _landmarks() -> tuple[SceneLandmarkProfile, ...]:
    return (
        _landmark("nw-a", (80, 200, 48, 48), MacroZone.NORTH_WEST),
        _landmark("nw-b", (300, 300, 48, 48), MacroZone.NORTH_WEST),
        _landmark("ne-a", (520, 300, 48, 48), MacroZone.NORTH_EAST),
        _landmark("ne-b", (680, 350, 48, 48), MacroZone.NORTH_EAST),
        _landmark("sw-a", (200, 620, 48, 48), MacroZone.SOUTH_WEST),
        _landmark("sw-b", (200, 700, 48, 48), MacroZone.SOUTH_WEST),
    )


def _coherent_matches() -> tuple[ScaledLandmarkMatch, ...]:
    affine = np.asarray(
        [
            [0.90, 0.00],
            [0.00, 0.85],
            [42.0, 76.0],
        ],
        dtype=np.float64,
    )
    matches = []
    for landmark in _landmarks():
        x, y, width, height = landmark.region
        center = np.asarray(
            [x + width / 2.0, y + height / 2.0, 1.0], dtype=np.float64
        ) @ affine
        side = 44
        matches.append(
            ScaledLandmarkMatch(
                landmark=landmark,
                region=(
                    int(round(float(center[0]) - side / 2.0)),
                    int(round(float(center[1]) - side / 2.0)),
                    side,
                    side,
                ),
                distance=0.04,
            )
        )
    return tuple(matches)


def test_fit_scaled_landmarks_accepts_one_modest_coherent_transform() -> None:
    affine = fit_scaled_landmarks(_coherent_matches())
    assert affine is not None
    assert np.linalg.det(affine[:2]) > 0.0
    scales = np.linalg.svd(affine[:2], compute_uv=False)
    assert 0.75 <= float(scales.min()) <= float(scales.max()) <= 1.25


def test_fit_scaled_landmarks_keeps_original_distance_gate() -> None:
    matches = list(_coherent_matches())
    matches[0] = replace(matches[0], distance=0.13)
    matches[1] = replace(matches[1], distance=0.13)
    assert fit_scaled_landmarks(tuple(matches)) is None


def test_fit_scaled_landmarks_requires_three_frozen_zones() -> None:
    matches = tuple(
        replace(
            match,
            landmark=replace(match.landmark, macro_zone=MacroZone.NORTH_EAST),
        )
        if match.landmark.macro_zone is MacroZone.SOUTH_WEST
        else match
        for match in _coherent_matches()
    )
    assert fit_scaled_landmarks(matches) is None


def test_fit_scaled_landmarks_rejects_incoherent_independent_match() -> None:
    matches = list(_coherent_matches())
    x, y, width, height = matches[-1].region
    matches[-1] = replace(matches[-1], region=(x + 90, y - 80, width, height))
    assert fit_scaled_landmarks(tuple(matches)) is None


def test_fit_scaled_landmarks_rejects_reflection() -> None:
    landmarks = _landmarks()
    source_centers = [
        (item.region[0] + 24.0, item.region[1] + 24.0) for item in landmarks
    ]
    max_x = max(x for x, _ in source_centers)
    matches = tuple(
        ScaledLandmarkMatch(
            landmark=landmark,
            region=(
                int(round(max_x - center_x)),
                int(round(center_y)),
                48,
                48,
            ),
            distance=0.04,
        )
        for landmark, (center_x, center_y) in zip(
            landmarks, source_centers, strict=True
        )
    )
    assert fit_scaled_landmarks(matches) is None
