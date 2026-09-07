"""Bind a fresh mine-start frame to a retained successful 0->28 start frame."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any, Final

import cv2
import numpy as np

from ..capture import Frame, PixelFormat, RawFrame
from .resource import ProfiledResourceDetector, resource_state_from_observation
from .scaled_scene_registration import register_scaled_scene

WIDTH: Final = 1005
HEIGHT: Final = 1078
REFERENCE_SHA256: Final = "a8f9aaab00d7c07c40cec6e4e7165a85439012a96deb9b0b93bd7b8e6cc8a7f0"
MIN_GOOD_MATCHES: Final = 500
MIN_INLIERS: Final = 400
MIN_INLIER_RATIO: Final = 0.85
MAX_SCALE_ERROR: Final = 0.03
MAX_ROTATION_DEGREES: Final = 1.0
MAX_TRANSLATION_PX: Final = 10.0


def _image(frame: Frame) -> np.ndarray:
    return np.frombuffer(frame.payload, np.uint8).reshape(HEIGHT, WIDTH, 4)[:, :, :3]


def load_reference(path: Path) -> Frame:
    payload = path.read_bytes()
    if len(payload) != WIDTH * HEIGHT * 4:
        raise ValueError("successful start reference has wrong byte count")
    if hashlib.sha256(payload).hexdigest() != REFERENCE_SHA256:
        raise ValueError("successful start reference SHA-256 changed")
    return Frame.from_raw(
        RawFrame(payload, WIDTH, HEIGHT, PixelFormat.BGRA8888),
        frame_id=1,
        captured_monotonic_s=1.0,
    )


def _equivalence(reference: Frame, current: Frame) -> dict[str, Any] | None:
    mask = np.zeros((HEIGHT, WIDTH), np.uint8)
    mask[34:850, :767] = 255
    orb = cv2.ORB.create(nfeatures=4000)
    k1, d1 = orb.detectAndCompute(cv2.cvtColor(_image(reference), cv2.COLOR_BGR2GRAY), mask)
    k2, d2 = orb.detectAndCompute(cv2.cvtColor(_image(current), cv2.COLOR_BGR2GRAY), mask)
    if d1 is None or d2 is None or not k1 or not k2:
        return None
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    good = [m for m, n in matcher.knnMatch(d1, d2, k=2) if m.distance < 0.70 * n.distance]
    if len(good) < MIN_GOOD_MATCHES:
        return None
    source = np.asarray([k1[item.queryIdx].pt for item in good], dtype=np.float32)
    target = np.asarray([k2[item.trainIdx].pt for item in good], dtype=np.float32)
    affine, inlier_mask = cv2.estimateAffinePartial2D(
        source,
        target,
        None,
        method=cv2.RANSAC,
        ransacReprojThreshold=2.0,
        maxIters=5000,
        confidence=0.999,
    )
    if affine is None or inlier_mask is None:
        return None
    inliers = int(inlier_mask.sum())
    ratio = inliers / len(good)
    scale = math.hypot(float(affine[0, 0]), float(affine[0, 1]))
    rotation = math.degrees(math.atan2(float(affine[1, 0]), float(affine[0, 0])))
    translation = math.hypot(float(affine[0, 2]), float(affine[1, 2]))
    if (
        inliers < MIN_INLIERS
        or ratio < MIN_INLIER_RATIO
        or abs(scale - 1.0) > MAX_SCALE_ERROR
        or abs(rotation) > MAX_ROTATION_DEGREES
        or translation > MAX_TRANSLATION_PX
    ):
        return None
    return {
        "good_matches": len(good),
        "inliers": inliers,
        "inlier_ratio": ratio,
        "scale": scale,
        "rotation_degrees": rotation,
        "translation_px": translation,
    }


def classify_equivalent_start(
    *,
    current: Frame,
    reference: Frame,
    original_start_detector: ProfiledResourceDetector,
) -> tuple[tuple[Any, ...], dict[str, Any]] | None:
    evidence = _equivalence(reference, current)
    if evidence is None:
        return None
    registered = register_scaled_scene(reference, original_start_detector)
    if registered is None:
        raise ValueError("retained successful start cannot rebuild its proven registered detector")
    detector, registration_evidence = registered
    states = []
    for candidate in detector.profile.candidates:
        observation = detector._classify_candidate(  # noqa: SLF001 - external scene proof owns gate
            current,
            candidate,
            scene_confidence=1.0,
            anchor_confidences={},
        )
        states.append(resource_state_from_observation(observation))
    if not any(
        state.available is True and state.interaction_region is not None for state in states
    ):
        return None
    return (
        tuple(states),
        {
            "kind": "successful_start_frame_equivalence",
            "reference_sha256": REFERENCE_SHA256,
            "equivalence": evidence,
            "registered_start": registration_evidence,
        },
    )
