import cv2
import numpy as np
import pytest

from mining_automation.navigation.visual_route import (
    MAP_CENTRE,
    LocalizationError,
    MinimapGeometry,
    TerrainReference,
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
    from pathlib import Path

    from mining_automation.navigation.visual_route import VisualRoute

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
