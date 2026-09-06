from __future__ import annotations

import sys
from pathlib import Path

TOOLS_ROOT = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_ROOT))

from run_three_rock_continuous_proof import (  # noqa: E402
    _registered_landmark_region_is_valid,
)

from mining_automation.perception.scene_landmarks import MacroZone  # noqa: E402


def test_registered_landmark_rejects_cross_zone_affine_projection() -> None:
    assert _registered_landmark_region_is_valid(
        (200, 620, 48, 48), MacroZone.SOUTH_WEST
    )
    # Mirrors the live failure mode: the same south-west landmark projected
    # above the horizontal macro-zone boundary must reject registration.
    assert not _registered_landmark_region_is_valid(
        (200, 460, 48, 48), MacroZone.SOUTH_WEST
    )


def test_registered_landmark_rejects_world_view_bounds_drift() -> None:
    assert _registered_landmark_region_is_valid(
        (689, 299, 48, 48), MacroZone.NORTH_EAST
    )
    assert not _registered_landmark_region_is_valid(
        (740, 299, 48, 48), MacroZone.NORTH_EAST
    )


def test_register_translation_fails_closed_when_affine_crosses_frozen_zone(
    monkeypatch,
) -> None:
    from dataclasses import replace

    import numpy as np
    import run_three_rock_continuous_proof as proof

    from mining_automation.capture import Frame, PixelFormat, RawFrame
    from mining_automation.perception.resource import ProfiledResourceDetector

    zero_frame = Frame.from_raw(
        RawFrame(
            bytes(proof.WIDTH * proof.HEIGHT * 4),
            proof.WIDTH,
            proof.HEIGHT,
            PixelFormat.BGRA8888,
        ),
        frame_id=1,
        captured_monotonic_s=0.0,
    )
    monkeypatch.setattr(proof, "frame_from_path", lambda path, frame_id: zero_frame)
    detector = proof.build_pose_detectors()["at_southwest"]

    landmarks = list(detector.profile.scene_landmarks)
    crossing_index = next(
        index
        for index, landmark in enumerate(landmarks)
        if landmark.landmark_id == "at_southwest-south-west-a"
    )
    landmarks[crossing_index] = replace(
        landmarks[crossing_index],
        reference_descriptor=tuple(1.0 for _ in range(16)),
    )
    detector = ProfiledResourceDetector(
        replace(detector.profile, scene_landmarks=tuple(landmarks)),
        version=detector.metadata.version,
    )

    monkeypatch.setattr(proof, "_fast_descriptor", lambda integral, region: np.zeros(16))
    forced_affine = np.asarray(
        [
            [1.0, 0.0],
            [0.0, 1.0],
            [0.0, -200.0],
        ]
    )
    monkeypatch.setattr(
        proof.np.linalg,
        "lstsq",
        lambda source, target, rcond=None: (forced_affine, None, None, None),
    )

    # Five landmarks still match across all three frozen zones. The affine for
    # the sixth would move its SOUTH_WEST region into NORTH_WEST. This must be
    # normal unsupported registration (None), never a profile-construction crash.
    assert proof.register_translation(zero_frame, detector) is None
