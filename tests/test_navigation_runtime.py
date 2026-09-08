from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from mining_automation.navigation.runtime import (
    RouteFrame,
    RouteLimits,
    point_segment_distance,
    run_route,
)
from mining_automation.navigation.visual_route import (
    MAP_CENTRE,
    LocalizationError,
    Registration,
    VisualRoute,
)


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


class ConnectorGeometry(Geometry):
    x: float = MAP_CENTRE
    y: float = MAP_CENTRE

    def screen_point(self, point):
        return tuple(round(value) for value in point)

    def contains(self, point):
        return ((point[0] - MAP_CENTRE) ** 2 + (point[1] - MAP_CENTRE) ** 2) ** 0.5 < 80


class ConnectorBackend(Backend):
    def __init__(self, position=(-10.00010335957663, -5.00036695352385)):
        super().__init__()
        self.position = position
        self.connector_outcome = "arrive"

    def click(self, frame, point, geometry):
        self.clicks.append(point)
        if len(self.clicks) == 1 and self.connector_outcome == "miss":
            return
        if len(self.clicks) == 1 and self.connector_outcome == "partial":
            self.position = (-4.0, 0.0)
            return
        self.position = (
            self.position[0] + point[0] - MAP_CENTRE,
            self.position[1] + point[1] - MAP_CENTRE,
        )


class JitteringConnectorBackend(ConnectorBackend):
    def __init__(self):
        super().__init__()
        self.preclick_positions = [
            (-10.8, -5.0),
            (-9.2, -5.0),
            (-10.8, -5.0),
            (-10.0, -5.0),
            (-10.0, -5.0),
        ]
        self.first_click_frame_id = None

    def capture(self, label):
        if not self.clicks and self.preclick_positions:
            self.position = self.preclick_positions.pop(0)
        return super().capture(label)

    def click(self, frame, point, geometry):
        if self.first_click_frame_id is None:
            self.first_click_frame_id = frame.frame_id
        super().click(frame, point, geometry)


class ConnectorRoute:
    centre = (MAP_CENTRE, MAP_CENTRE)
    localization_error = LocalizationError

    def __init__(self, backend):
        self.backend = backend
        self.waypoints = [
            SimpleNamespace(name=name, image_key=str(index), tolerance=3.0)
            for index, name in enumerate(("mine_start", "road_join", "bank_counter"))
        ]
        profile = (
            Path(__file__).resolve().parents[1]
            / "src/mining_automation/navigation/profiles/varrock_east/route.json"
        )
        self.policy = VisualRoute(profile)
        self.health = True

    def observe(self, image, waypoint):
        goals = ((0.0, 0.0), (30.0, 0.0), (50.0, 0.0))
        goal = goals[int(waypoint.image_key)]
        target = (
            MAP_CENTRE + goal[0] - image[0],
            MAP_CENTRE + goal[1] - image[1],
        )
        distance = ((target[0] - MAP_CENTRE) ** 2 + (target[1] - MAP_CENTRE) ** 2) ** 0.5
        return ConnectorGeometry(), Registration(target, distance, 244, 0.89, 0.1, 0.99, 1.0, 0.0)

    def match_start_connector(self, waypoint, registration):
        return self.policy.match_start_connector(waypoint, registration)

    def verify_endpoint(self, image, geometry):
        return {"accepted": True}

    def verify_health(self, image, geometry):
        return self.health


def test_observed_post_third_departure_connects_to_canonical_start_then_route():
    backend = ConnectorBackend()
    result = run_route(backend, ConnectorRoute(backend))
    assert result.success
    assert result.completed_checkpoints == ["mine_start", "road_join", "bank_counter"]
    assert result.click_count == len(backend.clicks) == 3
    assert backend.clicks[0] == (106, 101)
    assert [event["kind"] for event in result.events].count("start_connector_dispatched") == 1
    assert [event["kind"] for event in result.events].count("start_connector_arrival_verified") == 1


def test_same_radius_wrong_bearing_is_not_an_authorized_departure():
    backend = ConnectorBackend(position=(10.00010335957663, -5.00036695352385))
    result = run_route(backend, ConnectorRoute(backend))
    assert "not_at_recorded_mine_start" in result.stop_reason
    assert not backend.clicks


def test_start_connector_residual_outside_profile_bound_has_zero_input():
    backend = ConnectorBackend(position=(-11.05, -4.5))
    result = run_route(backend, ConnectorRoute(backend))
    assert "not_at_recorded_mine_start" in result.stop_reason
    assert not backend.clicks


@pytest.mark.parametrize(
    ("outcome", "reason"),
    (
        ("miss", "start_connector_arrival_not_proven"),
        ("partial", "start_connector_arrival_not_proven"),
    ),
)
def test_start_connector_never_retries_failed_or_partial_movement(outcome, reason):
    backend = ConnectorBackend()
    backend.connector_outcome = outcome
    result = run_route(backend, ConnectorRoute(backend))
    assert reason in result.stop_reason
    assert result.click_count == len(backend.clicks) == 1


def test_single_click_pilot_stops_after_verified_connector_arrival():
    backend = ConnectorBackend()
    result = run_route(backend, ConnectorRoute(backend), stop_after=1)
    assert result.status == "STAGE_PASS" and not result.success
    assert result.stop_reason == "departure_connector_complete_full_route_not_tested"
    assert result.completed_checkpoints == ["mine_start"]
    assert result.click_count == len(backend.clicks) == 1


def test_two_transition_pilot_counts_connector_as_first_transition():
    backend = ConnectorBackend()
    result = run_route(backend, ConnectorRoute(backend), stop_after=2)
    assert result.status == "STAGE_PASS" and not result.success
    assert result.stop_reason == "requested_stage_complete_full_route_not_tested"
    assert result.completed_checkpoints == ["mine_start", "road_join"]
    assert result.click_count == len(backend.clicks) == 2


def test_start_connector_requires_the_normal_stationarity_sample_count():
    backend = JitteringConnectorBackend()
    result = run_route(backend, ConnectorRoute(backend), stop_after=1)
    assert result.status == "STAGE_PASS"
    assert backend.first_click_frame_id == 5
    assert result.click_count == len(backend.clicks) == 1


def test_connector_path_preserves_health_and_stale_frame_zero_input_guards():
    unhealthy = ConnectorBackend()
    route = ConnectorRoute(unhealthy)
    route.health = False
    assert "healthy_display_unproven" in run_route(unhealthy, route).stop_reason
    assert not unhealthy.clicks

    stale = ConnectorBackend()
    stale.stale = True
    assert "stale_or_replayed_frame" in run_route(stale, ConnectorRoute(stale)).stop_reason
    assert not stale.clicks
