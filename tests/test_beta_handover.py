from __future__ import annotations

import json
import time

import pytest

from mining_automation.beta_launcher import check_handover
from mining_automation.beta_panel import place_panel
from mining_automation.beta_session import SessionSettings


def permit(tmp_path, **changes):
    value = dict(
        issue=94,
        comment_id=123,
        git_sha="a" * 40,
        hwnd=99,
        input_owner_released=True,
        expires_at_unix=time.time() + 30,
        features=["launcher", "canonical_finish", "emergency_stop", "continuous"],
    )
    value.update(changes)
    path = tmp_path / "outputs/beta-live-handover.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_no_handover_never_authorizes_start(tmp_path):
    with pytest.raises(RuntimeError, match="Awaiting exclusive"):
        check_handover(tmp_path, "a" * 40, 99, SessionSettings())


@pytest.mark.parametrize(
    "changes",
    [
        dict(issue=119),
        dict(comment_id=0),
        dict(comment_id="123"),
        dict(git_sha="b" * 40),
        dict(hwnd=98),
        dict(input_owner_released=False),
        dict(expires_at_unix=0),
        dict(expires_at_unix=float("nan")),
        dict(expires_at_unix=float("inf")),
        dict(features=["continuous"]),
    ],
)
def test_handover_is_build_window_time_and_feature_bound(tmp_path, changes):
    permit(tmp_path, **changes)
    with pytest.raises(RuntimeError):
        check_handover(tmp_path, "a" * 40, 99, SessionSettings())


def test_old_handover_cannot_silently_enable_motion_or_variation(tmp_path):
    permit(tmp_path)
    assert check_handover(tmp_path, "a" * 40, 99, SessionSettings())["input_owner_released"]
    for switch in ("smooth_cursor", "varied_rock_points"):
        with pytest.raises(RuntimeError, match="selected feature"):
            check_handover(tmp_path, "a" * 40, 99, SessionSettings(**{switch: True}))


@pytest.mark.parametrize(
    "work,game",
    [
        ((0, 0, 1920, 1080), (0, 0, 1000, 1000)),
        ((0, 0, 1920, 1080), (900, 0, 1900, 1000)),
        ((-1920, 0, 0, 1080), (-1920, 0, -900, 1080)),
        ((0, 0, 1920, 1080), (0, 0, 1920, 500)),
    ],
)
def test_status_panel_cannot_overlay_game_or_leave_work_area(work, game):
    x, y = place_panel(work, game, 340, 340)
    assert work[0] <= x and work[1] <= y and x + 340 <= work[2] and y + 340 <= work[3]
    assert x + 340 < game[0] or x > game[2] or y + 340 < game[1] or y > game[3]


def test_panel_placement_fails_instead_of_resizing_runelite():
    with pytest.raises(RuntimeError, match="No unobscured space"):
        place_panel((0, 0, 1920, 1080), (0, 0, 1920, 1080), 340, 340)
