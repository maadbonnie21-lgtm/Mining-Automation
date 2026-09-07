"""Scale-aware fallback for the retained distributed scene landmarks.

Keeps the descriptor threshold, quorum, zones, and original descriptors intact.
Only used after the existing exact and rigid-registration paths fail.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import numpy as np
from numpy.typing import NDArray

from ..capture import Frame, PixelFormat
from .resource import ProfiledResourceDetector
from .scene_landmarks import SceneLandmarkProfile, evaluate_scene

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]
Region = tuple[int, int, int, int]


@dataclass(frozen=True)
class ScaledLandmarkMatch:
    landmark: SceneLandmarkProfile
    region: Region
    distance: float


def _regions_valid(
    xs: IntArray, ys: IntArray, side: int, landmark: SceneLandmarkProfile, width: int, height: int
) -> NDArray[np.bool_]:
    zone = landmark.macro_zone.value
    result = (xs >= 0) & (ys >= 34) & (xs + side <= 767) & (ys + side <= 850)
    result &= (xs + side / 2 < width / 2) if "west" in zone else (xs + side / 2 >= width / 2)
    result &= (ys + side / 2 < height / 2) if "north" in zone else (ys + side / 2 >= height / 2)
    return result


def _distances(
    ii: FloatArray, xs: IntArray, ys: IntArray, side: int, reference: tuple[float, ...]
) -> FloatArray:
    step = side // 4
    cells = []
    for row in range(4):
        for column in range(4):
            x1 = xs + column * step
            y1 = ys + row * step
            x2 = x1 + step
            y2 = y1 + step
            cells.append((ii[y2, x2] - ii[y1, x2] - ii[y2, x1] + ii[y1, x1]) / (step * step))
    values = np.stack(cells, axis=1)
    values -= values.mean(axis=1, keepdims=True)
    scale = np.abs(values).max(axis=1, keepdims=True)
    normalized = np.divide(values, scale, out=np.zeros_like(values), where=scale > 1e-9)
    return np.asarray(np.abs(normalized - np.asarray(reference)).mean(axis=1), dtype=np.float64)


def _search(
    ii: FloatArray, landmark: SceneLandmarkProfile, width: int, height: int
) -> ScaledLandmarkMatch | None:
    x, y, w, h = landmark.region
    if (w, h, landmark.grid) != (48, 48, 4):
        return None
    best: tuple[float, int, int, int, int, int] | None = None
    dx, dy = np.meshgrid(np.arange(-160, 161, 4), np.arange(-160, 161, 4))
    for side in (40, 44, 48, 52, 56):
        xs = np.asarray(x + dx.ravel(), dtype=np.int64)
        ys = np.asarray(y + dy.ravel(), dtype=np.int64)
        valid = _regions_valid(xs, ys, side, landmark, width, height)
        xs, ys = xs[valid], ys[valid]
        if not len(xs):
            continue
        scores = _distances(ii, xs, ys, side, landmark.reference_descriptor)
        index = int(np.lexsort((np.abs(xs - x) + np.abs(ys - y), scores))[0])
        fx, fy = np.meshgrid(
            np.arange(xs[index] - 4, xs[index] + 5), np.arange(ys[index] - 4, ys[index] + 5)
        )
        xs, ys = np.asarray(fx.ravel(), dtype=np.int64), np.asarray(fy.ravel(), dtype=np.int64)
        valid = _regions_valid(xs, ys, side, landmark, width, height)
        xs, ys = xs[valid], ys[valid]
        if not len(xs):
            continue
        scores = _distances(ii, xs, ys, side, landmark.reference_descriptor)
        index = int(np.lexsort((np.abs(xs - x) + np.abs(ys - y), scores))[0])
        xx, yy = int(xs[index]), int(ys[index])
        candidate = (float(scores[index]), abs(xx - x) + abs(yy - y), abs(side - w), xx, yy, side)
        if best is None or candidate < best:
            best = candidate
    if best is None:
        return None
    distance, _, _, x, y, side = best
    return ScaledLandmarkMatch(landmark, (x, y, side, side), distance)


def fit_scaled_landmarks(matches: tuple[ScaledLandmarkMatch, ...]) -> FloatArray | None:
    """Fit one spatially coherent transform; never loosen the image-match gate."""
    accepted = tuple(m for m in matches if m.distance <= m.landmark.maximum_distance)
    if len(accepted) < 5 or len({m.landmark.macro_zone for m in accepted}) < 3:
        return None
    source = np.asarray(
        [[m.landmark.region[0] + 24.0, m.landmark.region[1] + 24.0, 1.0] for m in accepted]
    )
    target = np.asarray(
        [[m.region[0] + m.region[2] / 2, m.region[1] + m.region[3] / 2] for m in accepted]
    )
    affine, _, rank, _ = np.linalg.lstsq(source, target, rcond=None)
    if rank != 3 or not np.isfinite(affine).all():
        return None
    # The fallback searches only modest changes to the 48px patch. Reject
    # an unrelated/reflected geometry or independent matches that do not fit.
    scales = np.linalg.svd(affine[:2], compute_uv=False)
    if np.linalg.det(affine[:2]) <= 0 or scales.min() < 0.75 or scales.max() > 1.25:
        return None
    if float(np.linalg.norm(source @ affine - target, axis=1).max()) > 12.0:
        return None
    return np.asarray(affine, dtype=np.float64)


def register_scaled_scene(
    frame: Frame, detector: ProfiledResourceDetector
) -> tuple[ProfiledResourceDetector, dict[str, Any]] | None:
    profile = detector.profile
    if (
        (frame.width, frame.height) != (1005, 1078)
        or frame.pixel_format is not PixelFormat.BGRA8888
        or len(profile.scene_landmarks) != 6
    ):
        return None
    pixels = np.frombuffer(frame.payload, dtype=np.uint8).reshape(frame.height, frame.width, 4)
    luma = pixels[:, :, 2] * 0.299 + pixels[:, :, 1] * 0.587 + pixels[:, :, 0] * 0.114
    ii = np.pad(luma.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    found = tuple(_search(ii, lm, frame.width, frame.height) for lm in profile.scene_landmarks)
    if any(item is None for item in found):
        return None
    matches = tuple(item for item in found if item is not None)
    affine = fit_scaled_landmarks(matches)
    if affine is None:
        return None
    # Keep all six original descriptors and their exact thresholds. Each
    # chosen region is independently re-evaluated by the normal scene gate.
    landmarks = tuple(replace(m.landmark, region=m.region) for m in matches)
    verdict = evaluate_scene(
        frame,
        landmarks,
        required_quorum=5,
        required_zones=3,
        frame_width=frame.width,
        frame_height=frame.height,
    )
    if not verdict.validated:
        return None
    candidates = []
    for candidate in profile.candidates:
        x, y, w, h = candidate.region
        center = np.asarray([x + w / 2, y + h / 2, 1.0]) @ affine
        region = (int(round(center[0] - w / 2)), int(round(center[1] - h / 2)), w, h)
        if region[0] < 0 or region[1] < 34 or region[0] + w > 767 or region[1] + h > 850:
            return None
        candidates.append(replace(candidate, region=region))
    registered = replace(
        profile,
        profile_id=f"{profile.profile_id}-scaled",
        scene_landmarks=landmarks,
        candidates=tuple(candidates),
    )
    evidence = {
        "kind": "distributed_scaled_affine_registration",
        "matched": verdict.matched_count,
        "zones": sorted(z.value for z in verdict.matched_zones),
        "affine": affine.tolist(),
        "landmarks": {
            m.landmark.landmark_id: {"distance": round(m.distance, 6), "region": list(m.region)}
            for m in matches
        },
    }
    return ProfiledResourceDetector(registered, version=detector.metadata.version), evidence
