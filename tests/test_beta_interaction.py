from __future__ import annotations

import random

import pytest

from mining_automation.beta_interaction import (
    InputExpired,
    click_at_proven_point,
    require_interior,
    sample_interior,
    smooth_move,
)


class Clock:
    def __init__(self):
        self.now = 0.0
        self.after_sleep = lambda: None

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        self.after_sleep()


class Pointer:
    def __init__(self):
        self.point = (10, 20)
        self.moves = []
        self.downs = self.ups = 0
        self.left = self.middle = self.right = False
        self.root = 7
        self.after_down = lambda: None

    def cursor_position(self):
        return self.point

    def move_cursor(self, x, y):
        self.point = (x, y)
        self.moves.append(self.point)
        return True

    def left_button_is_down(self):
        return self.left

    def middle_button_is_down(self):
        return self.middle

    def key_is_down(self, key):
        return self.right

    def root_window_at_point(self, x, y):
        return self.root

    def send_mouse_button(self, *, button_up):
        if button_up:
            self.ups += 1
            self.left = False
        else:
            self.downs += 1
            self.left = True
            self.after_down()
        return 1


def test_sampler_varies_only_in_current_inward_region():
    previous = None
    rng = random.Random(31)
    points = set()
    for _ in range(1000):
        point = sample_interior((100, 200, 12, 10), previous=previous, rng=rng)
        require_interior(point, (100, 200, 12, 10))
        assert point != previous
        previous = point
        points.add(point)
    assert len(points) > 20


@pytest.mark.parametrize("region", [(0, 0, 4, 4), (-1, 0, 10, 10), (0, 0, 1, 1), (True, 1, 8, 8)])
def test_impossible_surface_has_no_fallback_click(region):
    with pytest.raises(ValueError):
        sample_interior(region)


def test_one_proven_pixel_can_repeat_only_without_alternative():
    assert sample_interior((0, 0, 5, 5), previous=(2, 2)) == (2, 2)


@pytest.mark.parametrize("point", [(100, 202), (101, 205), (110, 205), (105, 200), (105, 209)])
def test_edge_points_are_denied(point):
    with pytest.raises(ValueError):
        require_interior(point, (100, 200, 12, 10))


def test_smooth_motion_has_observed_intermediate_points_and_exact_end():
    api, clock = Pointer(), Clock()
    smooth_move(api, (800, 500), check=lambda: None, clock=clock, sleep=clock.sleep)
    assert len(api.moves) > 50
    assert api.point == (800, 500)
    assert clock.now == pytest.approx(0.18)
    assert api.downs == 0
    assert all(abs(a[0] - b[0]) <= 20 for a, b in zip(api.moves, api.moves[1:], strict=False))


@pytest.mark.parametrize("call", [1, 2, 10, 25])
def test_cancellation_during_motion_never_clicks(call):
    api, clock = Pointer(), Clock()
    checks = 0

    def check():
        nonlocal checks
        checks += 1
        if checks == call:
            raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        smooth_move(api, (800, 500), check=check, clock=clock, sleep=clock.sleep)
    assert api.downs == 0


def test_source_expiry_during_motion_is_zero_click():
    api, clock = Pointer(), Clock()
    with pytest.raises(InputExpired):
        smooth_move(
            api, (800, 500), check=lambda: None, deadline=0.05, clock=clock, sleep=clock.sleep
        )
    assert api.point != (800, 500)
    assert api.downs == 0


def test_human_motion_is_not_overwritten_on_the_next_step():
    api, clock = Pointer(), Clock()

    def human():
        api.point = (900, 900)

    clock.after_sleep = human
    with pytest.raises(RuntimeError, match="human_cursor_displacement"):
        smooth_move(api, (800, 500), check=lambda: None, clock=clock, sleep=clock.sleep)
    assert api.point == (900, 900)
    assert len(api.moves) == 1


@pytest.mark.parametrize("button", ["left", "middle", "right"])
def test_held_human_button_denies_motion_and_click(button):
    api, clock = Pointer(), Clock()
    setattr(api, button, True)
    with pytest.raises(RuntimeError, match="human_mouse_button"):
        smooth_move(api, (500, 400), check=lambda: None, clock=clock, sleep=clock.sleep)
    with pytest.raises(RuntimeError, match="human_mouse_button"):
        click_at_proven_point(
            api, api.point, hwnd=7, check=lambda: None, wait=clock.sleep, deadline=1, clock=clock
        )
    assert api.downs == api.ups == 0
    assert api.moves == []


def test_click_never_recenters_and_records_real_point():
    api, clock = Pointer(), Clock()
    api.point = (37, 29)
    result = click_at_proven_point(
        api, (37, 29), hwnd=7, check=lambda: None, wait=clock.sleep, deadline=1, clock=clock
    )
    assert result["screen_point"] == [37, 29]
    assert result["down"] == result["up"] == 1
    assert result["released"]
    assert api.moves == []


@pytest.mark.parametrize("change", ["point", "root", "deadline"])
def test_mismatch_or_expired_proof_denies_down(change):
    api, clock = Pointer(), Clock()
    target = api.point
    if change == "point":
        api.point = (20, 20)
    elif change == "root":
        api.root = 99
    else:
        clock.now = 1
    with pytest.raises(RuntimeError):
        click_at_proven_point(
            api, target, hwnd=7, check=lambda: None, wait=clock.sleep, deadline=1, clock=clock
        )
    assert api.downs == api.ups == 0


def test_cancellation_during_owned_down_always_releases():
    api, clock = Pointer(), Clock()
    audit = {}

    def cancelled_wait(seconds):
        raise RuntimeError("emergency")

    with pytest.raises(RuntimeError, match="emergency"):
        click_at_proven_point(
            api,
            api.point,
            hwnd=7,
            check=lambda: None,
            wait=cancelled_wait,
            deadline=1,
            clock=clock,
            receipt=audit,
        )
    assert api.downs == api.ups == 1
    assert not api.left
    assert audit["released"]
    assert audit["down"] == 1


def test_exception_in_native_down_still_releases():
    api, clock = Pointer(), Clock()
    api.after_down = lambda: (_ for _ in ()).throw(OSError("native_down_error"))
    with pytest.raises(OSError):
        click_at_proven_point(
            api, api.point, hwnd=7, check=lambda: None, wait=clock.sleep, deadline=1, clock=clock
        )
    assert api.ups == 1
    assert not api.left


def test_motion_during_native_down_is_not_reported_as_a_proven_click():
    api, clock = Pointer(), Clock()
    audit = {}
    api.after_down = lambda: setattr(api, "point", (111, 222))
    with pytest.raises(RuntimeError, match="cursor_changed_at_mouse_down"):
        click_at_proven_point(
            api,
            (10, 20),
            hwnd=7,
            check=lambda: None,
            wait=clock.sleep,
            deadline=1,
            clock=clock,
            receipt=audit,
        )
    assert audit["down"] == 1
    assert audit["after_down_screen_point"] == [111, 222]
    assert audit["released"]


def test_already_at_target_has_no_cosmetic_delay():
    api, clock = Pointer(), Clock()
    smooth_move(api, api.point, check=lambda: None, clock=clock, sleep=clock.sleep)
    assert api.moves == []
    assert clock.now == 0
