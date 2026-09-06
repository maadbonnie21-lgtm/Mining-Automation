"""Exact recognition for the already-authenticated RuneLite Play Now screen."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
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
        fingerprint_id="welcome-click-here-to-play-v3",
        anchors=(
            # These small static patches were bit-identical across six real welcome
            # frames from two different login sessions, while each differed from
            # disconnect, Connecting, Loading, and retained gameplay frames.
            SessionScreenAnchor(
                (442, 76, 16, 16),
                "7806d12cc3051366d9cb78b95e93ef34991ef903afe475929a266206aa052229",
            ),
            SessionScreenAnchor(
                (310, 363, 12, 12),
                "4e72fe6b1a7c8a224456da63ffe22c92520433cac821e1e29200fe2de53bd188",
            ),
        ),
    ),
)

WELCOME_BUTTON_REGION: Final[tuple[int, int, int, int]] = (270, 320, 230, 90)
WELCOME_TEXT_REGION: Final[tuple[int, int, int, int]] = (350, 350, 140, 28)
WELCOME_MIN_RED_DOMINANT_FRACTION: Final[float] = 0.25
WELCOME_MIN_BRIGHT_TEXT_FRACTION: Final[float] = 0.08


def _region_fraction(
    frame: Frame,
    region: tuple[int, int, int, int],
    predicate: Callable[[int, int, int], bool],
) -> float:
    x, y, width, height = region
    stride = frame.width * 4
    matched = 0
    total = width * height
    payload = frame.payload
    for row in range(y, y + height):
        start = row * stride + x * 4
        for offset in range(start, start + width * 4, 4):
            blue = payload[offset]
            green = payload[offset + 1]
            red = payload[offset + 2]
            if predicate(red, green, blue):
                matched += 1
    return matched / total


def _matches_welcome_button_signature(frame: Frame) -> bool:
    if (
        frame.width != EXPECTED_WIDTH
        or frame.height != EXPECTED_HEIGHT
        or frame.pixel_format is not PixelFormat.BGRA8888
    ):
        return False
    red_fraction = _region_fraction(
        frame,
        WELCOME_BUTTON_REGION,
        lambda red, green, blue: (
            red > 90 and red * 4 > green * 5 and red * 4 > blue * 5
        ),
    )
    if red_fraction < WELCOME_MIN_RED_DOMINANT_FRACTION:
        return False
    bright_fraction = _region_fraction(
        frame,
        WELCOME_TEXT_REGION,
        lambda red, green, blue: red + green + blue > 570,
    )
    return bright_fraction >= WELCOME_MIN_BRIGHT_TEXT_FRACTION


def matches_welcome_click_here_to_play(frame: Frame) -> bool:
    """Recognize the reviewed Welcome screen across normal animation variance."""

    return any(
        matches_session_screen(frame, item) for item in WELCOME_CLICK_HERE_TO_PLAY
    ) or _matches_welcome_button_signature(frame)


def session_recovery_stage(frame: Frame) -> str | None:
    """Return the one reviewed recovery stage visible in this exact frame."""

    if matches_disconnected_screen(frame):
        return DISCONNECTED_STAGE
    if matches_preauthenticated_play_now(frame):
        return PREAUTHENTICATED_STAGE
    if matches_welcome_click_here_to_play(frame):
        return WELCOME_PLAY_STAGE
    return None
