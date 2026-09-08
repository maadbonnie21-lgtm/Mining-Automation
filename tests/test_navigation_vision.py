import math
from pathlib import Path

import cv2
import numpy as np
import pytest

from mining_automation.navigation.visual_route import (
    MAP_CENTRE,
    LocalizationError,
    MinimapGeometry,
    Registration,
    TerrainReference,
    VisualRoute,
)


def textured_map(seed=10):
    rng = np.random.default_rng(seed)
    image = rng.integers(35, 150, (192, 192, 3), dtype=np.uint8)
    image = cv2.GaussianBlur(image, (3, 3), 0)
    for _ in range(70):
        p = tuple(int(x) for x in rng.integers(18, 172, 2))
        cv2.circle(image, p, 3, tuple(int(c) for c in rng.integers(30, 180, 3)), -1)
    return image


def test_registered_target_tracks_terrain_translation_not_old_click():
    image = textured_map()
    matrix = np.float32([[1, 0, 17], [0, 1, -12]])
    current = cv2.warpAffine(image, matrix, (192, 192))
    r = TerrainReference(image).register(current)
    assert np.allclose(r.target, (MAP_CENTRE + 17, MAP_CENTRE - 12), atol=1.0)
    assert r.agreement > 0.9 and r.inliers >= 8


def test_unknown_or_blank_map_is_not_a_target():
    ref = TerrainReference(textured_map())
    for current in (np.zeros((192, 192, 3), np.uint8), textured_map(99)):
        with pytest.raises(LocalizationError):
            ref.register(current)


def test_unsupported_rotation_does_not_trigger_camera_reset():
    image = textured_map()
    matrix = cv2.getRotationMatrix2D((MAP_CENTRE, MAP_CENTRE), 25, 1.0)
    current = cv2.warpAffine(image, matrix, (192, 192))
    with pytest.raises(LocalizationError):
        TerrainReference(image).register(current)


def test_coordinate_mapping_uses_current_minimap():
    a = MinimapGeometry(800, 140, 95.5, 0.0)
    b = MinimapGeometry(1000, 200, 47.75, 0.0)
    target = (MAP_CENTRE + 20, MAP_CENTRE - 10)
    assert a.screen_point(target) == (820, 130)
    assert b.screen_point(target) == (1010, 195)
    assert a.contains((820, 130)) and not a.contains((900, 140))


def test_same_scale_duplicate_compass_is_not_a_unique_minimap():
    path = (
        Path(__file__).resolve().parents[1]
        / "src/mining_automation/navigation/profiles/varrock_east/route.json"
    )
    route = VisualRoute(path)
    anchor = route.images["chrome"]
    h, w = anchor.shape[:2]
    frame = np.zeros((1080, 1200, 3), dtype=np.uint8)
    frame[50 : 50 + h, 700 : 700 + w] = anchor
    frame[50 : 50 + h, 900 : 900 + w] = anchor
    with pytest.raises(LocalizationError, match="ambiguous"):
        route.locator.locate(frame)


def test_profile_connector_binds_exact_observed_departure_not_a_larger_radius():
    path = (
        Path(__file__).resolve().parents[1]
        / "src/mining_automation/navigation/profiles/varrock_east/route.json"
    )
    route = VisualRoute(path)
    observed = Registration(
        target=(105.50010335957663, 100.50036695352385),
        distance=11.18059644527559,
        inliers=244,
        ratio=0.8905109489051095,
        error=0.10136834532022476,
        agreement=0.9993889086927739,
        scale=1.0004775584387702,
        rotation_degrees=0.015382927586891748,
    )
    match = route.match_start_connector(route.waypoints[0], observed)
    assert match is not None
    assert match["connector_id"] == "post_third_returned_to_canonical_mine_start"
    assert match["offset_residual"] < 0.01
    assert (
        match["mining_terminal_frame_sha256"]
        == "d4904cca0de4b3b18c07ee2f0be99a182486fe201e535679d626e60a05810ea8"
    )
    assert (
        match["route_observation_frame_sha256"]
        == "1b03fc9cdef9aa9865e371a031094d9d3125244fb8ebe14f33159e29abd4d444"
    )
    assert (
        match["pose_reference_sha256"]
        == "1b5ce3847f76e7e9a6a9fed50d6cbe10b6afcf6030e389277566e6cfdf4f17c6"
    )

    wrong_bearing = Registration(
        target=(MAP_CENTRE - 10.00010335957663, MAP_CENTRE + 5.00036695352385),
        distance=observed.distance,
        inliers=observed.inliers,
        ratio=observed.ratio,
        error=observed.error,
        agreement=observed.agreement,
        scale=1.0,
        rotation_degrees=0.0,
    )
    assert route.match_start_connector(route.waypoints[0], wrong_bearing) is None

    outside_residual = Registration(
        target=(MAP_CENTRE + 11.05, MAP_CENTRE + 4.5),
        distance=math.hypot(11.05, 4.5),
        inliers=observed.inliers,
        ratio=observed.ratio,
        error=observed.error,
        agreement=observed.agreement,
        scale=1.0,
        rotation_degrees=0.0,
    )
    assert route.match_start_connector(route.waypoints[0], outside_residual) is None
    assert route.match_start_connector(route.waypoints[-1], observed) is None
