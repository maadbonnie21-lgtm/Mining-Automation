"""Conservative scale-aware fallback for frozen mining-scene landmarks.

Exact-pose and rigid-translation registration remain first. This fallback only
runs after both fail. It keeps every original landmark descriptor and 0.12
distance gate, still requires the frozen 5-of-6 quorum across all three zones,
and searches one *global* nearby landmark scale for the whole scene.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Final

import numpy as np
from numpy.typing import NDArray

from ..capture import Frame, PixelFormat
from .resource import ProfiledResourceDetector
from .scene_landmarks import SceneLandmarkProfile, evaluate_scene

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]
Region = tuple[int, int, int, int]

_SUPPORTED_WIDTH: Final[int] = 1005
_SUPPORTED_HEIGHT: Final[int] = 1078
_WORLD_RIGHT: Final[int] = 767
_WORLD_TOP: Final[int] = 34
_WORLD_BOTTOM: Final[int] = 850
_SEARCH_RADIUS: Final[int] = 160
_COARSE_STEP: Final[int] = 4
_REFINE_RADIUS: Final[int] = 4
# Rigid 48x48 matching already ran and failed before this fallback is called.
_SCALED_SIDES: Final[tuple[int, ...]] = (40, 44, 52, 56)
_MAX_AFFINE_RESIDUAL: Final[float] = 12.0
_MIN_AFFINE_SCALE: Final[float] = 0.75
_MAX_AFFINE_SCALE: Final[float] = 1.25
_MAX_SIDE_SPREAD: Final[int] = 8


@dataclass(frozen=True, slots=True)
class ScaledLandmarkMatch:
    """One frozen landmark observed at a nearby region and scale."""

    landmark: SceneLandmarkProfile
    region: Region
    distance: float


def _region_mask(
    xs: IntArray,
    ys: IntArray,
    side: int,
    landmark: SceneLandmarkProfile,
) -> NDArray[np.bool_]:
    result = (
        (xs >= 0)
        & (ys >= _WORLD_TOP)
        & (xs + side <= _WORLD_RIGHT)
        & (ys + side <= _WORLD_BOTTOM)
    )
    center_x = xs + side / 2.0
    center_y = ys + side / 2.0
    if "west" in landmark.macro_zone.value:
        result &= center_x < _SUPPORTED_WIDTH / 2.0
    else:
        result &= center_x >= _SUPPORTED_WIDTH / 2.0
    if "north" in landmark.macro_zone.value:
        result &= center_y < _SUPPORTED_HEIGHT / 2.0
    else:
        result &= center_y >= _SUPPORTED_HEIGHT / 2.0
    return result


def _descriptor_distances(
    integral: FloatArray,
    xs: IntArray,
    ys: IntArray,
    side: int,
    reference: tuple[float, ...],
) -> FloatArray:
    step = side // 4
    cells: list[FloatArray] = []
    for row in range(4):
        for column in range(4):
            x1 = xs + column * step
            y1 = ys + row * step
            x2 = x1 + step
            y2 = y1 + step
            cells.append(
                np.asarray(
                    (
                        integral[y2, x2]
                        - integral[y1, x2]
                        - integral[y2, x1]
                        + integral[y1, x1]
                    )
                    / float(step * step),
                    dtype=np.float64,
                )
            )
    values = np.stack(cells, axis=1)
    values -= values.mean(axis=1, keepdims=True)
    scale = np.abs(values).max(axis=1, keepdims=True)
    normalized = np.divide(
        values,
        scale,
        out=np.zeros_like(values),
        where=scale > 1e-9,
    )
    return np.asarray(
        np.abs(normalized - np.asarray(reference, dtype=np.float64)).mean(axis=1),
        dtype=np.float64,
    )


def _match_landmark(
    integral: FloatArray,
    landmark: SceneLandmarkProfile,
    side: int,
) -> ScaledLandmarkMatch | None:
    x, y, width, height = landmark.region
    if (width, height, landmark.grid) != (48, 48, 4):
        return None

    dx, dy = np.meshgrid(
        np.arange(-_SEARCH_RADIUS, _SEARCH_RADIUS + 1, _COARSE_STEP),
        np.arange(-_SEARCH_RADIUS, _SEARCH_RADIUS + 1, _COARSE_STEP),
    )
    xs = np.asarray(x + dx.ravel(), dtype=np.int64)
    ys = np.asarray(y + dy.ravel(), dtype=np.int64)
    mask = _region_mask(xs, ys, side, landmark)
    xs, ys = xs[mask], ys[mask]
    if len(xs) == 0:
        return None
    distances = _descriptor_distances(
        integral,
        xs,
        ys,
        side,
        landmark.reference_descriptor,
    )
    order = np.lexsort((np.abs(xs - x) + np.abs(ys - y), distances))
    index = int(order[0])
    coarse_x, coarse_y = int(xs[index]), int(ys[index])

    fx, fy = np.meshgrid(
        np.arange(coarse_x - _REFINE_RADIUS, coarse_x + _REFINE_RADIUS + 1),
        np.arange(coarse_y - _REFINE_RADIUS, coarse_y + _REFINE_RADIUS + 1),
    )
    xs = np.asarray(fx.ravel(), dtype=np.int64)
    ys = np.asarray(fy.ravel(), dtype=np.int64)
    mask = _region_mask(xs, ys, side, landmark)
    xs, ys = xs[mask], ys[mask]
    if len(xs) == 0:
        return None
    distances = _descriptor_distances(
        integral,
        xs,
        ys,
        side,
        landmark.reference_descriptor,
    )
    order = np.lexsort((np.abs(xs - x) + np.abs(ys - y), distances))
    index = int(order[0])
    return ScaledLandmarkMatch(
        landmark=landmark,
        region=(int(xs[index]), int(ys[index]), side, side),
        distance=float(distances[index]),
    )


def fit_scaled_landmarks(
    matches: tuple[ScaledLandmarkMatch, ...],
) -> FloatArray | None:
    """Fit one modest coherent affine map without loosening landmark gates."""

    accepted = tuple(
        match
        for match in matches
        if match.distance <= match.landmark.maximum_distance
    )
    if len(accepted) < 5:
        return None
    if len({match.landmark.macro_zone for match in accepted}) < 3:
        return None
    sides = tuple(match.region[2] for match in accepted)
    if max(sides) - min(sides) > _MAX_SIDE_SPREAD:
        return None

    source = np.asarray(
        [
            [
                match.landmark.region[0] + match.landmark.region[2] / 2.0,
                match.landmark.region[1] + match.landmark.region[3] / 2.0,
                1.0,
            ]
            for match in accepted
        ],
        dtype=np.float64,
    )
    target = np.asarray(
        [
            [
                match.region[0] + match.region[2] / 2.0,
                match.region[1] + match.region[3] / 2.0,
            ]
            for match in accepted
        ],
        dtype=np.float64,
    )
    affine, _, rank, _ = np.linalg.lstsq(source, target, rcond=None)
    if rank != 3 or not np.isfinite(affine).all():
        return None
    linear = affine[:2]
    scales = np.linalg.svd(linear, compute_uv=False)
    if float(np.linalg.det(linear)) <= 0.0:
        return None
    if float(scales.min()) < _MIN_AFFINE_SCALE:
        return None
    if float(scales.max()) > _MAX_AFFINE_SCALE:
        return None
    residuals = np.linalg.norm(source @ affine - target, axis=1)
    if float(residuals.max()) > _MAX_AFFINE_RESIDUAL:
        return None
    return np.asarray(affine, dtype=np.float64)


def _registration_for_side(
    frame: Frame,
    detector: ProfiledResourceDetector,
    integral: FloatArray,
    side: int,
) -> tuple[ProfiledResourceDetector, dict[str, Any], float] | None:
    profile = detector.profile
    found = tuple(
        _match_landmark(integral, landmark, side)
        for landmark in profile.scene_landmarks
    )
    if any(match is None for match in found):
        return None
    matches = tuple(match for match in found if match is not None)
    affine = fit_scaled_landmarks(matches)
    if affine is None:
        return None

    landmarks = tuple(
        replace(match.landmark, region=match.region) for match in matches
    )
    verdict = evaluate_scene(
        frame,
        landmarks,
        required_quorum=profile.minimum_landmark_quorum,
        required_zones=profile.minimum_landmark_zones,
        frame_width=frame.width,
        frame_height=frame.height,
    )
    if not verdict.validated:
        return None

    candidates = []
    for candidate in profile.candidates:
        x, y, width, height = candidate.region
        center = np.asarray(
            [x + width / 2.0, y + height / 2.0, 1.0],
            dtype=np.float64,
        ) @ affine
        region = (
            int(round(float(center[0]) - width / 2.0)),
            int(round(float(center[1]) - height / 2.0)),
            width,
            height,
        )
        if (
            region[0] < 0
            or region[1] < _WORLD_TOP
            or region[0] + width > _WORLD_RIGHT
            or region[1] + height > _WORLD_BOTTOM
        ):
            return None
        candidates.append(replace(candidate, region=region))

    try:
        registered_profile = replace(
            profile,
            profile_id=f"{profile.profile_id}-scaled-{side}",
            scene_landmarks=landmarks,
            candidates=tuple(candidates),
        )
    except ValueError:
        return None

    accepted_distances = tuple(
        match.distance
        for match in matches
        if match.distance <= match.landmark.maximum_distance
    )
    score = float(sum(accepted_distances) / len(accepted_distances))
    evidence = {
        "kind": "distributed_scaled_affine_registration",
        "landmark_side": side,
        "matched": verdict.matched_count,
        "zones": sorted(zone.value for zone in verdict.matched_zones),
        "score": round(score, 6),
        "affine": affine.tolist(),
        "landmarks": {
            match.landmark.landmark_id: {
                "distance": round(match.distance, 6),
                "region": list(match.region),
            }
            for match in matches
        },
    }
    return (
        ProfiledResourceDetector(
            registered_profile,
            version=detector.metadata.version,
        ),
        evidence,
        score,
    )


def register_scaled_scene(
    frame: Frame,
    detector: ProfiledResourceDetector,
) -> tuple[ProfiledResourceDetector, dict[str, Any]] | None:
    """Reacquire one known pose after a modest global apparent scale change."""

    profile = detector.profile
    if (
        (frame.width, frame.height) != (_SUPPORTED_WIDTH, _SUPPORTED_HEIGHT)
        or frame.pixel_format is not PixelFormat.BGRA8888
        or len(profile.scene_landmarks) != 6
    ):
        return None

    pixels = np.frombuffer(frame.payload, dtype=np.uint8).reshape(
        frame.height,
        frame.width,
        4,
    )
    luma = (
        pixels[:, :, 2].astype(np.float64) * 0.299
        + pixels[:, :, 1].astype(np.float64) * 0.587
        + pixels[:, :, 0].astype(np.float64) * 0.114
    )
    integral = np.pad(luma.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    registrations = tuple(
        result
        for side in _SCALED_SIDES
        if (result := _registration_for_side(frame, detector, integral, side))
        is not None
    )
    if not registrations:
        return None
    registered_detector, evidence, _ = min(
        registrations,
        key=lambda result: (result[2], abs(result[1]["landmark_side"] - 48)),
    )
    return registered_detector, evidence
