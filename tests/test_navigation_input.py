from types import SimpleNamespace

import pytest

from mining_automation.navigation.runtime import RouteFrame
from mining_automation.navigation.windows import NativeRouteBackend


class Input:
    def __init__(self):
        self.cursor = (0, 0)
        self.down = False
        self.events = []
        self.reject_down = False

    def client_to_screen(self, h, x, y):
        return (x + 10, y + 20)

    def physical_screen_to_physical_client(self, h, x, y):
        return (x - 10, y - 20)

    def root_window_at_point(self, *p):
        return 42

    def left_button_is_down(self):
        return self.down

    def move_cursor(self, *p):
        self.cursor = p
        return True

    def cursor_position(self):
        return self.cursor

    def send_mouse_button(self, *, button_up):
        self.events.append("up" if button_up else "down")
        self.down = not button_up
        return 0 if self.reject_down and not button_up else 1


def backend(tmp_path):
    b = NativeRouteBackend.__new__(NativeRouteBackend)
    b.api = Input()
    b.hwnd = 42
    b.frame_id = 1
    b.output = tmp_path
    b.max_frame_age_s = 4.0
    b.initial = {"client_size": [1000, 800], "client_origin": [10, 20]}
    b.guard = lambda: b.initial.copy()
    b.now = lambda: 2.0
    b.wait = lambda s: None
    return b


def test_click_is_one_down_up_with_bound_screen_point(tmp_path):
    b = backend(tmp_path)
    frame = RouteFrame(1, 1.0, None, b.initial, "before.png")
    b.click(frame, (800, 140), SimpleNamespace(contains=lambda p: True))
    assert b.api.events == ["down", "up"] and not b.api.down
    assert b.api.cursor == (810, 160)


def test_stale_point_and_human_mouse_down_do_not_get_clicked(tmp_path):
    b = backend(tmp_path)
    frame = RouteFrame(0, 1.0, None, b.initial, "before.png")
    with pytest.raises(RuntimeError):
        b.click(frame, (800, 140), SimpleNamespace(contains=lambda p: True))
    b.api.down = True
    frame = RouteFrame(1, 1.0, None, b.initial, "before.png")
    with pytest.raises(RuntimeError):
        b.click(frame, (800, 140), SimpleNamespace(contains=lambda p: True))
    assert not b.api.events


def test_partial_delivery_and_cancel_always_release(tmp_path):
    b = backend(tmp_path)
    b.api.reject_down = True
    frame = RouteFrame(1, 1.0, None, b.initial, "before.png")
    with pytest.raises(RuntimeError):
        b.click(frame, (800, 140), SimpleNamespace(contains=lambda p: True))
    assert b.api.events == ["down", "up"] and not b.api.down
    b.api.reject_down = False

    def cancelled(s):
        raise RuntimeError("owner_stop")

    b.wait = cancelled
    with pytest.raises(RuntimeError):
        b.click(frame, (800, 140), SimpleNamespace(contains=lambda p: True))
    assert b.api.events[-1] == "up" and not b.api.down


def test_physical_capture_point_never_uses_logical_dpi_mapper(tmp_path):
    b = backend(tmp_path)

    def forbidden_logical_mapping(*args):
        raise AssertionError("Physical points must not be scaled by 1.25 again")

    b.api.client_to_screen = forbidden_logical_mapping
    frame = RouteFrame(1, 1.0, None, b.initial, "before.png")
    b.click(frame, (800, 140), SimpleNamespace(contains=lambda p: True))
    assert b.api.cursor == (810, 160)


def test_delivered_click_receipt_survives_post_input_window_failure(tmp_path):
    import json

    b = backend(tmp_path)
    calls = 0

    def guard():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("window_identity_or_geometry_changed")
        return b.initial.copy()

    b.guard = guard
    frame = RouteFrame(1, 1.0, None, b.initial, "before.png")
    with pytest.raises(RuntimeError, match="window_identity"):
        b.click(frame, (800, 140), SimpleNamespace(contains=lambda p: True))
    records = [json.loads(line) for line in (tmp_path / "clicks.jsonl").read_text().splitlines()]
    assert b.delivered_click_count == 1
    assert records[-1]["down"] == records[-1]["up"] == 1
    assert records[-1]["button_released"]


def test_physical_reverse_round_trip_allows_only_two_pixel_awt_rounding(tmp_path):
    b = backend(tmp_path)
    b.api.physical_screen_to_physical_client = lambda h, x, y: (x - 10 - 2, y - 20 - 1)
    frame = RouteFrame(1, 1.0, None, b.initial, "before.png")
    b.click(frame, (800, 140), SimpleNamespace(contains=lambda p: True))
    b = backend(tmp_path / "bad")
    b.output.mkdir()
    b.api.physical_screen_to_physical_client = lambda h, x, y: (x - 10 - 4, y - 20)
    frame = RouteFrame(1, 1.0, None, b.initial, "before.png")
    with pytest.raises(RuntimeError, match="round_trip"):
        b.click(frame, (800, 140), SimpleNamespace(contains=lambda p: True))
