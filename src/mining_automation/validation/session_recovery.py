"""Exact recognition for the already-authenticated RuneLite Play Now screen."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Final

from ..capture import Frame, PixelFormat

EXPECTED_WIDTH: Final[int] = 1005
EXPECTED_HEIGHT: Final[int] = 1078
DISCONNECTED_OK_CLIENT_POINT: Final[tuple[int, int]] = (384, 329)
PLAY_NOW_CLIENT_POINT: Final[tuple[int, int]] = (383, 289)
WELCOME_PLAY_CLIENT_POINT: Final[tuple[int, int]] = (384, 365)
DISCONNECTED_STAGE: Final[str] = "disconnected_ok"
PREAUTHENTICATED_STAGE: Final[str] = "preauthenticated_play_now"
WELCOME_PLAY_STAGE: Final[str] = "welcome_click_here_to_play"


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


DISCONNECTED_SCREEN: Final[SessionScreenFingerprint] = SessionScreenFingerprint(
    fingerprint_id="disconnected-ok-v1",
    anchors=(
        SessionScreenAnchor(
            (235, 240, 305, 40),
            "d25add6731eb9f7e16c5fbfeae577703739c2b1b890f6347c7ccf84158403b6f",
        ),
        SessionScreenAnchor(
            (305, 305, 160, 50),
            "bfc2dcb1d7a8e2d99cb1911d1a81e02039be0501465f3b4bd0e103c4c5553887",
        ),
        SessionScreenAnchor(
            (205, 198, 360, 200),
            "ad23f019afea83c999bc9f9aabf89219cbb4dc42587de4fd115cbb20f0548c7e",
        ),
    ),
)


def matches_disconnected_screen(frame: Frame) -> bool:
    """Recognize the source-proven normal disconnect dialog."""

    return matches_session_screen(frame, DISCONNECTED_SCREEN)


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


# The real 2026-09-05 recovery trace proved the actual welcome screen only
# after the transient Connecting/Loading canvases.  These three disjoint
# anchors are stable across frames 006/008/024 and differ from Play Now,
# Connecting, Loading, and retained gameplay frames.
WELCOME_CLICK_HERE_TO_PLAY: Final[tuple[SessionScreenFingerprint, ...]] = (
    SessionScreenFingerprint(
        fingerprint_id="welcome-click-here-to-play-v2",
        anchors=(
            SessionScreenAnchor(
                (95, 125, 390, 125),
                "964a08d9dd1946756d8487402ccea3785a3f13e6a4bf8de493ca746bffe562bd",
            ),
            SessionScreenAnchor(
                (510, 105, 225, 190),
                "3889a76d7b0da34a3bff4c959e6f70532d5aef654a10c280b660dae63f8ec0dd",
            ),
            SessionScreenAnchor(
                (335, 340, 155, 45),
                "6f9c624c4df9e13c416dcb78d8cd54433d59fb277a0ddeb0b6c1c5e1078bdb6a",
            ),
        ),
    ),
)

def matches_welcome_click_here_to_play(frame: Frame) -> bool:
    """Recognize either reviewed render state of the in-client welcome screen."""

    return any(matches_session_screen(frame, item) for item in WELCOME_CLICK_HERE_TO_PLAY)


def session_recovery_stage(frame: Frame) -> str | None:
    """Return the one reviewed recovery stage visible in this exact frame."""

    if matches_disconnected_screen(frame):
        return DISCONNECTED_STAGE
    if matches_preauthenticated_play_now(frame):
        return PREAUTHENTICATED_STAGE
    if matches_welcome_click_here_to_play(frame):
        return WELCOME_PLAY_STAGE
    return None
