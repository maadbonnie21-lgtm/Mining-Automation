from __future__ import annotations

import hashlib

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.validation.session_recovery import (
    PLAY_NOW_CLIENT_POINT,
    SessionScreenAnchor,
    SessionScreenFingerprint,
    matches_session_screen,
)


def _frame(width: int = 1005, height: int = 1078) -> Frame:
    raw = RawFrame(bytes(width * height * 4), width, height, PixelFormat.BGRA8888)
    return Frame.from_raw(raw, frame_id=1, captured_monotonic_s=1.0)


def test_exact_fingerprint_engine_requires_every_anchor() -> None:
    zero_pixel = hashlib.sha256(bytes(4)).hexdigest()
    good = SessionScreenFingerprint("synthetic", (SessionScreenAnchor((1, 1, 1, 1), zero_pixel),))
    bad = SessionScreenFingerprint("synthetic-bad", (SessionScreenAnchor((1, 1, 1, 1), "f" * 64),))
    frame = _frame()
    assert matches_session_screen(frame, good) is True
    assert matches_session_screen(frame, bad) is False


def test_fingerprint_engine_rejects_wrong_geometry() -> None:
    zero_pixel = hashlib.sha256(bytes(4)).hexdigest()
    fingerprint = SessionScreenFingerprint("synthetic", (SessionScreenAnchor((1, 1, 1, 1), zero_pixel),))
    assert matches_session_screen(_frame(width=1004), fingerprint) is False


def test_reviewed_play_now_point_is_inside_client() -> None:
    x, y = PLAY_NOW_CLIENT_POINT
    assert 0 <= x < 1005
    assert 0 <= y < 1078
