#!/usr/bin/env python3
"""Solve current-view landmark descriptors and iron candidate coordinates."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mining_automation.capture import Frame, RawFrame  # noqa: E402
from mining_automation.perception import load_varrock_east_iron_profile  # noqa: E402
from mining_automation.perception.scene_landmarks import describe_region  # noqa: E402

_FRAME = Path(
    "diagnostics/live-proof-resource/frames/post-up-hold-50ms-20260903.raw"
)
_OFFSETS = {
    "west-ridge": (-5, -16),
    "west-lower-ridge": (18, -60),
    "south-path": (59, 6),
    "south-central-edge": (19, 4),
    "north-east-wall": (-44, -11),
    "east-bank-edge": (-17, -24),
}


def _mean(integral: np.ndarray, x: int, y: int, width: int, height: int) -> np.ndarray:
    x2, y2 = x + width, y + height
    total = integral[y2, x2] - integral[y, x2] - integral[y2, x] + integral[y, x]
    return total / float(width * height)


def _similarity(prototype: tuple[float, ...], actual: np.ndarray, maximum: float) -> float:
    distance = float(np.linalg.norm(np.asarray(prototype) - actual))
    return max(0.0, min(1.0, 1.0 - distance / maximum))


def main() -> int:
    profile = load_varrock_east_iron_profile()
    payload = _FRAME.read_bytes()
    frame = Frame.from_raw(
        RawFrame(payload, profile.frame_width, profile.frame_height, profile.pixel_format),
        frame_id=1,
        captured_monotonic_s=0.0,
    )
    bgra = np.frombuffer(payload, dtype=np.uint8).reshape(
        profile.frame_height, profile.frame_width, 4
    )
    rgb = bgra[:, :, [2, 1, 0]].astype(np.float64)
    integral = np.pad(rgb.cumsum(axis=0).cumsum(axis=1), ((1, 0), (1, 0), (0, 0)))

    source_centers = np.asarray(
        [[x + w / 2, y + h / 2] for x, y, w, h in (landmark.region for landmark in profile.scene_landmarks)]
    )
    target_centers = np.asarray(
        [source_centers[i] + _OFFSETS[landmark.landmark_id] for i, landmark in enumerate(profile.scene_landmarks)]
    )
    affine = np.linalg.lstsq(
        np.column_stack((source_centers, np.ones(len(source_centers)))),
        target_centers,
        rcond=None,
    )[0]

    landmarks = []
    for landmark in profile.scene_landmarks:
        dx, dy = _OFFSETS[landmark.landmark_id]
        x, y, width, height = landmark.region
        region = (x + dx, y + dy, width, height)
        landmarks.append(
            {
                "landmark_id": landmark.landmark_id,
                "region": list(region),
                "zone": landmark.macro_zone.value,
                "maximum_distance": landmark.maximum_distance,
                "grid": landmark.grid,
                "reference_descriptor": [
                    round(value, 9)
                    for value in describe_region(frame, region, grid=landmark.grid)
                ],
            }
        )

    candidates = []
    for candidate in profile.candidates:
        x, y, width, height = candidate.region
        mapped_center = np.asarray([x + width / 2, y + height / 2, 1.0]) @ affine
        predicted_x = int(round(mapped_center[0] - width / 2))
        predicted_y = int(round(mapped_center[1] - height / 2))
        solutions = []
        for search_y in range(max(34, predicted_y - 60), min(820 - height, predicted_y + 61)):
            for search_x in range(max(0, predicted_x - 60), min(767 - width, predicted_x + 61)):
                regions = (
                    (search_x, search_y, 10, 10),
                    (search_x + 10, search_y, 10, 10),
                    (search_x, search_y + 10, 10, 10),
                    (search_x + 10, search_y + 10, 10, 10),
                )
                available_scores = []
                margins = []
                whole_mean = _mean(integral, search_x, search_y, width, height)
                for cell in regions:
                    actual = _mean(integral, *cell)
                    available = _similarity(
                        tuple(float(value) for value in whole_mean),
                        actual,
                        candidate.available_signature.max_distance,
                    )
                    depleted = _similarity(
                        candidate.depleted_signature.mean_rgb,
                        actual,
                        candidate.depleted_signature.max_distance,
                    )
                    available_scores.append(available)
                    margins.append(available - depleted)
                if not (
                    55.0 <= whole_mean[0] <= 115.0
                    and 30.0 <= whole_mean[1] <= 80.0
                    and 15.0 <= whole_mean[2] <= 65.0
                    and whole_mean[0] > whole_mean[1] + 12.0
                    and whole_mean[1] > whole_mean[2] + 5.0
                ):
                    continue
                distance = abs(search_x - predicted_x) + abs(search_y - predicted_y)
                score = (
                    min(available_scores)
                    + 0.5 * min(margins)
                    + 0.2 * sum(available_scores) / 4
                    - 0.0002 * distance
                )
                solutions.append((score, search_x, search_y, min(available_scores), min(margins)))
        solutions.sort(reverse=True)
        top = solutions[:10]
        candidates.append(
            {
                "resource_id": candidate.resource_id,
                "predicted_region": [predicted_x, predicted_y, width, height],
                "solutions": [
                    {
                        "region": [sx, sy, width, height],
                        "score": round(score, 6),
                        "minimum_available_similarity": round(minimum, 6),
                        "minimum_margin": round(margin, 6),
                        "mean_rgb": [
                            round(float(value), 3)
                            for value in _mean(integral, sx, sy, width, height)
                        ],
                    }
                    for score, sx, sy, minimum, margin in top
                ],
            }
        )

    print(json.dumps({"affine": affine.tolist(), "landmarks": landmarks, "candidates": candidates}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
