from __future__ import annotations

from types import SimpleNamespace

import pytest

from mining_automation.beta_interaction import require_fresh
from mining_automation.beta_route import beta_route_backend


@pytest.mark.parametrize("extra_delay", [0, 0.2, 0.99])
def test_connector_keeps_native_receipt_and_deadline_through_movement(extra_delay):
    clock = SimpleNamespace(now=50.0)
    policy = SimpleNamespace(dispatch_deadline=None)

    def check():
        if policy.dispatch_deadline is not None:
            require_fresh(policy.dispatch_deadline, clock.now)

    policy.check = check
    calls = []
    receipt = dict(accepted=True, native_captured_monotonic_s=50.0, source="actual_native")

    class Native:
        def verify_start_connector_authority(self, frame, connector):
            return receipt

        def click(self, frame, point, geometry):
            calls.append(("destination", point))
            clock.now += extra_delay
            policy.check()
            calls.append("down")

    backend = beta_route_backend(Native, policy)()
    assert backend.verify_start_connector_authority(None, None) is receipt
    backend.click(None, (100, 200), None)
    assert calls == [("destination", (100, 200)), "down"]
    assert policy.dispatch_deadline is None
    assert backend._beta_connector_deadline is None


def test_motion_expiry_after_core_authority_check_is_zero_down():
    clock = SimpleNamespace(now=50.0)
    policy = SimpleNamespace(dispatch_deadline=None)

    def check():
        if policy.dispatch_deadline is not None:
            require_fresh(policy.dispatch_deadline, clock.now)

    policy.check = check
    calls = []

    class Native:
        def verify_start_connector_authority(self, *args):
            return dict(accepted=True, native_captured_monotonic_s=50.0)

        def click(self, *args):
            calls.append("same_terrain_destination")
            clock.now = 51.01
            policy.check()
            calls.append("down")

    backend = beta_route_backend(Native, policy)()
    backend.verify_start_connector_authority(None, None)
    with pytest.raises(RuntimeError, match="source_expired_before_down"):
        backend.click(None, (100, 200), None)
    assert calls == ["same_terrain_destination"]
    assert policy.dispatch_deadline is None


def test_ordinary_waypoint_does_not_invent_resource_authority():
    calls = []
    policy = SimpleNamespace(dispatch_deadline=None, check=lambda: None)

    class Native:
        def click(self, *args):
            calls.append(args)

    backend = beta_route_backend(Native, policy)()
    backend.click("current_frame", (100, 200), "verified_geometry")
    assert calls == [("current_frame", (100, 200), "verified_geometry")]
    assert policy.dispatch_deadline is None


@pytest.mark.parametrize("timestamp", [None, True, 1, float("nan"), float("inf")])
def test_invalid_native_source_time_never_becomes_input_authority(timestamp):
    policy = SimpleNamespace(dispatch_deadline=None, check=lambda: None)

    class Native:
        def verify_start_connector_authority(self, *args):
            return dict(accepted=True, native_captured_monotonic_s=timestamp)

    backend = beta_route_backend(Native, policy)()
    with pytest.raises(RuntimeError, match="capture_time_unproven"):
        backend.verify_start_connector_authority(None, None)
    assert backend._beta_connector_deadline is None
