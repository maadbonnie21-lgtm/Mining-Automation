"""Exact recognition for the already-authenticated RuneLite Play Now screen."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Final

from ..capture import Frame, PixelFormat

EXPECTED_WIDTH: Final[int] = 1005
EXPECTED_HEIGHT: Final[int] = 1078
PLAY_NOW_CLIENT_POINT: Final[tuple[int, int]] = (383, 289)


@dataclass(frozen=True, slots=True)
class SessionScreenAnchor:
    region: tuple[int, int, int, int]
    sha256: str


@dataclass(frozen=True, slots=True)
class SessionScreenFingerprint:
    fingerprint_id: str
    anchors: tuple[SessionScreenAnchor, ...]


def _region_sha256(frame: Frame, region: tuple[int, int, int, int]) -> str:
    x, y, width, height = region
    stride = frame.width * 4
    payload = frame.payload
    cropped = bytearray()
    for row in range(y, y + height):
        start = row * stride + x * 4
        cropped.extend(payload[start : start + width * 4])
    return hashlib.sha256(cropped).hexdigest()


def matches_session_screen(
    frame: Frame,
    fingerprint: SessionScreenFingerprint,
) -> bool:
    if (
        frame.width != EXPECTED_WIDTH
        or frame.height != EXPECTED_HEIGHT
        or frame.pixel_format is not PixelFormat.BGRA8888
    ):
        return False
    return all(
        _region_sha256(frame, anchor.region) == anchor.sha256
        for anchor in fingerprint.anchors
    )


PREAUTHENTICATED_PLAY_NOW: Final[SessionScreenFingerprint] = SessionScreenFingerprint(
    fingerprint_id="preauthenticated-play-now-v1",
    anchors=(
        SessionScreenAnchor(
            (335, 260, 100, 30),
            "2d0d50ad72e1953c7d7fb0a070c7e74749297e49d62d61f76e3d3248ce96d112",
        ),
        SessionScreenAnchor(
            (340, 294, 90, 24),
            "81f9d08c146c59da6bc329d99b98e9a70da19fe606bc657e60a706e1b7c7cf71",
        ),
        SessionScreenAnchor(
            (300, 250, 170, 78),
            "ef769474ba24584b7cd0380c6845295f557ba83efa12e26a757ada39786c571c",
        ),
        SessionScreenAnchor(
            (260, 210, 250, 190),
            "55fc5190dae6a7edd24eed3dd3a462dd0281e3a00a1dc2146c49cabaa9cb4186",
        ),
    ),
)


def matches_preauthenticated_play_now(frame: Frame) -> bool:
    """Require every source-proven anchor before permitting one re-entry click."""

    return matches_session_screen(frame, PREAUTHENTICATED_PLAY_NOW)


def is_pre_authenticated_play_now(frame: Frame) -> bool:
    """Compatibility name used by PREP; same exact fingerprint gate."""

    return matches_preauthenticated_play_now(frame)
