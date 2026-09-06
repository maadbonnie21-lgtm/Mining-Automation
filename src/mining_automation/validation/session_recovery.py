"""Exact recognition for the already-authenticated RuneLite Play Now screen."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Final

from ..capture import Frame, PixelFormat

EXPECTED_WIDTH: Final[int] = 1005
EXPECTED_HEIGHT: Final[int] = 1078
PLAY_NOW_CLIENT_POINT: Final[tuple[int, int]] = (383, 289)
WELCOME_PLAY_CLIENT_POINT: Final[tuple[int, int]] = (384, 365)
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


# The welcome screen has two source-proven render states during its first second.
# Both variants were stable across the recovery trace; neither shares the
# pre-authenticated Play Now anchors or normal gameplay chrome.
WELCOME_CLICK_HERE_TO_PLAY: Final[tuple[SessionScreenFingerprint, ...]] = (
    SessionScreenFingerprint(
        fingerprint_id="welcome-click-here-to-play-initial-v1",
        anchors=(
            SessionScreenAnchor((330, 340, 115, 45), "dd51b308f9646b1e23606771b15c521b8b56ea37c52e2daf219d779461a25307"),
            SessionScreenAnchor((275, 318, 220, 15), "f01790b48f3aefc4e01cad994abd250d474124379de36d015847040744126153"),
            SessionScreenAnchor((35, 320, 215, 90), "56c0eb0246d0c980c961a0de177f42540166c6a6d857f86a1861fd4ae24b2c2a"),
            SessionScreenAnchor((520, 320, 220, 90), "284dea224a0c643df52d50fe0628332fb3fdc1a444492d7ed2dcb550b74714f9"),
        ),
    ),
    SessionScreenFingerprint(
        fingerprint_id="welcome-click-here-to-play-steady-v1",
        anchors=(
            SessionScreenAnchor((330, 340, 115, 45), "3c3c7244f72e8bfb868e2b5730b637a92445557cddad19fc78064ffeb1cb4046"),
            SessionScreenAnchor((275, 318, 220, 15), "1879d5951437b7d4a3ecfd3172068de26ed67f65470ce88006831481cd41ef3a"),
            SessionScreenAnchor((35, 320, 215, 90), "0247e71654eb24c56cb0640ea2af7ffbd379cb03e5ddb45e7e18c1f6d8cf91f3"),
            SessionScreenAnchor((520, 320, 220, 90), "f5af52195b47495011920e6e8720f5bceccd1cd861b2a09b00ead0aa7b0468c4"),
        ),
    ),
)


def matches_welcome_click_here_to_play(frame: Frame) -> bool:
    """Recognize either reviewed render state of the in-client welcome screen."""

    return any(matches_session_screen(frame, item) for item in WELCOME_CLICK_HERE_TO_PLAY)


def session_recovery_stage(frame: Frame) -> str | None:
    """Return the one reviewed recovery stage visible in this exact frame."""

    if matches_preauthenticated_play_now(frame):
        return PREAUTHENTICATED_STAGE
    if matches_welcome_click_here_to_play(frame):
        return WELCOME_PLAY_STAGE
    return None
