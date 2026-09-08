from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from mining_automation.navigation.runtime import (
    RouteFrame,
    RouteLimits,
    point_segment_distance,
    run_route,
)
from mining_automation.navigation.visual_route import LocalizationError, Registration


@dataclass
class Geometry:
    x: float = 100.0
    y: float = 100.0
    radius: float = 100.0
    anchor_score: float = 0.0

    def screen_point(self, point):
        return tuple(round(p) for p in point)

    def contains(self, point):
        return ((point[0] - 100) ** 2 + (point[1] - 100) ** 2) ** 0.5 < 80


class Backend:
    def __init__(self):
        self.position = 0.0
        self.frame_id = 0
        self.t = 1.0
        self.clicks = []
        self.window = {"hwnd": 42}
        self.stale = False
        self.miss_click = False
        self.cancel = False

    def now(self):
        return self.t

    def check_cancelled(self):
        if self.cancel:
            raise RuntimeError("owner_stop")

    def wait(self, seconds):
        self.t += seconds

    def capture(self, label):
        self.frame_id += 0 if self.stale else 1
        self.t += 0.05
        return RouteFrame(self.frame_id, self.t, self.position, self.window.copy(), label + ".png")

    def click(self, frame, point, geometry):
        self.clicks.append(point)
        if not self.miss_click:
            self.position += (point[0] - 100) / 10


class Route:
    centre = (100.0, 100.0)
    localization_error = LocalizationError

    def __init__(self):
        self.waypoints = [
            SimpleNamespace(name=n, image_key=str(i), tolerance=3.0)
            for i, n in enumerate(["mine_start", "road_join", "bank_counter"])
        ]
        self.health = True
        self.counter = True
        self.unknown = False

    def observe(self, image, waypoint):
        if self.unknown:
            raise LocalizationError("no_current_terrain")
        dx = (int(waypoint.image_key) - image) * 10
        return Geometry(), Registration((100 + dx, 100.0), abs(dx), 30, 1.0, 0.1, 0.95, 1.0, 0.0)

    def verify_endpoint(self, image, geometry):
        return {"accepted": self.counter}

    def verify_health(self, image, geometry):
        return self.health


def test_complete_route_requires_program_clicks_and_verified_endpoint():
    b = Backend()
    r = run_route(b, Route())
    assert r.success and r.status == "PASS"
    assert r.completed_checkpoints == ["mine_start", "road_join", "bank_counter"]
    assert len(b.clicks) == r.click_count == 2
    assert r.item_actions == 0 and not r.bank_interface_opened


def test_stage_pass_is_not_full_route_proof():
    b = Backend()
    r = run_route(b, Route(), stop_after=1)
    assert r.status == "STAGE_PASS" and not r.success
    assert len(b.clicks) == 1


def test_current_mine_start_cannot_attach_to_interior_waypoint():
    b = Backend()
    b.position = 3
    r = run_route(b, Route())
    assert not r.success and "not_at_recorded_mine_start" in r.stop_reason
    assert not b.clicks


def test_unknown_is_bounded_passive_reacquisition_not_a_click():
    b = Backend()
    route = Route()
    route.unknown = True
    r = run_route(b, route)
    assert not r.success and "localization_unproven" in r.stop_reason
    assert len(r.events) == 4 and not b.clicks


def test_stale_frame_stops_before_input():
    b = Backend()
    b.stale = True
    r = run_route(b, Route())
    assert "stale_or_replayed_frame" in r.stop_reason and not b.clicks


def test_missed_click_is_bounded_not_infinite_retry():
    b = Backend()
    b.miss_click = True
    r = run_route(b, Route())
    assert "repeated_no_progress" in r.stop_reason
    assert len(b.clicks) == 2 and not r.success


def test_bad_counter_visual_cannot_be_arrival():
    b = Backend()
    route = Route()
    route.counter = False
    r = run_route(b, route)
    assert not r.success and "bank_counter_not_visually_confirmed" in r.stop_reason


def test_health_and_owner_stop_have_zero_input():
    b = Backend()
    route = Route()
    route.health = False
    assert "healthy_display_unproven" in run_route(b, route).stop_reason
    b.cancel = True
    assert "owner_stop" in run_route(b, Route()).stop_reason
    assert not b.clicks


def test_geometry_change_does_not_get_normalized():
    class Changed(Backend):
        def capture(self, label):
            f = super().capture(label)
            if self.frame_id > 1:
                self.window["client_size"] = [800, 600]
            return f

    b = Changed()
    result = run_route(b, Route())
    assert "window_identity_or_geometry_changed" in result.stop_reason and not b.clicks


def test_segment_distance_and_stage_validation():
    assert point_segment_distance((5.0, 3.0), (0.0, 0.0), (10.0, 0.0)) == 3.0
    assert point_segment_distance((12.0, 0.0), (0.0, 0.0), (10.0, 0.0)) == 2.0
    with pytest.raises(ValueError):
        run_route(Backend(), Route(), stop_after=0)


def test_first_stage_pilot_never_issues_a_corrective_second_click():
    backend = Backend()
    backend.miss_click = True
    result = run_route(backend, Route(), stop_after=1)
    assert not result.success
    assert len(backend.clicks) == 1
    assert "single_click_pilot_arrival_not_proven" in result.stop_reason


def test_observed_departure_sampling_noise_still_requires_all_route_checkpoints():
    backend = Backend()
    backend.position = -1.0004510011058667
    result = run_route(backend, Route())
    assert result.success
    assert result.completed_checkpoints == ["mine_start", "road_join", "bank_counter"]
    assert result.click_count == len(backend.clicks) == 2


def test_start_sampling_allowance_is_bounded_to_a_quarter_pixel():
    backend = Backend()
    backend.position = -1.026
    result = run_route(backend, Route())
    assert not result.success
    assert "not_at_recorded_mine_start" in result.stop_reason
    assert not backend.clicks


def test_explicit_return_start_tolerance_is_not_widened():
    backend = Backend()
    backend.position = -0.405
    result = run_route(backend, Route(), limits=RouteLimits(start_tolerance=4.0))
    assert not result.success
    assert not backend.clicks
