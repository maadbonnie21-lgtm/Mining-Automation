import hashlib
from types import SimpleNamespace

import numpy as np
import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.contracts import InventoryState
from mining_automation.controlled_mining_runner import CANONICAL_INVENTORY_RELEASE
from mining_automation.mining_slice import InventoryPerceptionEnvelope, PerceptionEpoch
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
    b.initial = {
        "client_size": [1005, 1078],
        "client_origin": [10, 20],
        "identity": {
            "title": "RuneLite - Chief Luma",
            "class_name": "SunAwtFrame",
        },
        "dpi": 96,
    }
    b.expected_title = "RuneLite - Chief Luma"
    b.focus_existing = False
    b._foreground_recovery_used = False
    b.guard = lambda: b.initial.copy()
    b.now = lambda: 2.0
    b.wait = lambda s: None
    return b


def test_focus_loss_before_click_invalidates_old_frame_until_fresh_capture(tmp_path):
    b = backend(tmp_path)
    b.guard = NativeRouteBackend.guard.__get__(b)
    b.check_cancelled = lambda: None
    b.snapshot = lambda: b.initial.copy()
    b.focus_existing = True
    b.api.foreground = 99
    b.api.foreground_window = lambda: b.api.foreground
    b.api.focus_calls = []

    def focus_window(hwnd):
        b.api.focus_calls.append(hwnd)
        b.api.foreground = hwnd
        return True

    b.api.focus_window = focus_window
    waits = []
    b.wait = waits.append
    old = RouteFrame(1, 1.0, None, b.initial, "old.png")
    with pytest.raises(RuntimeError, match="RuneLite_not_foreground"):
        b.click(old, (800, 140), SimpleNamespace(contains=lambda p: True))
    assert b.api.focus_calls == []
    assert b.api.events == []

    b._screen_pixels = lambda snapshot: np.zeros((1, 1, 3), dtype=np.uint8)
    new = b.capture("fresh-after-refocus")
    assert b.api.focus_calls == [42]
    assert waits == [0.20]
    assert new.frame_id == 2
    with pytest.raises(RuntimeError, match="stale_native_input_proposal"):
        b.click(old, (800, 140), SimpleNamespace(contains=lambda p: True))
    assert b.api.events == []

    b.click(new, (800, 140), SimpleNamespace(contains=lambda p: True))
    assert b.api.events == ["down", "up"]


def test_capture_refocus_is_bounded_and_rejects_geometry_change(tmp_path):
    b = backend(tmp_path)
    b.guard = NativeRouteBackend.guard.__get__(b)
    b.check_cancelled = lambda: None
    b.snapshot = lambda: b.initial.copy()
    b.focus_existing = True
    b.api.foreground = 99
    b.api.foreground_window = lambda: b.api.foreground
    b.api.focus_calls = []

    def focus_window(hwnd):
        b.api.focus_calls.append(hwnd)
        b.api.foreground = hwnd
        return True

    b.api.focus_window = focus_window
    b.wait = lambda _: None
    b._screen_pixels = lambda snapshot: np.zeros((1, 1, 3), dtype=np.uint8)
    b.capture("one-recovery")
    assert b.api.focus_calls == [42]

    b.api.foreground = 99
    with pytest.raises(RuntimeError, match="RuneLite_not_foreground"):
        b.capture("second-recovery-rejected")
    assert b.api.focus_calls == [42]

    b._foreground_recovery_used = False
    b.api.foreground = 99
    b.snapshot = lambda: {**b.initial, "client_size": [1004, 1078]}
    with pytest.raises(RuntimeError, match="window_identity_or_geometry_changed"):
        b.capture("geometry-changed")
    assert b.api.focus_calls == [42]


def test_capture_refocus_checks_cancellation_before_window_or_focus(tmp_path):
    b = backend(tmp_path)
    b.focus_existing = True
    b.check_cancelled = lambda: (_ for _ in ()).throw(RuntimeError("owner_stop"))
    b.snapshot = lambda: pytest.fail("snapshot after cancellation")
    b.api.focus_window = lambda hwnd: pytest.fail(f"focus after cancellation for {hwnd}")

    with pytest.raises(RuntimeError, match="owner_stop"):
        b._recover_foreground_before_capture()


def test_guard_does_not_refocus_without_explicit_focus_existing(tmp_path):
    b = backend(tmp_path)
    b.guard = NativeRouteBackend.guard.__get__(b)
    b.check_cancelled = lambda: None
    b.snapshot = lambda: b.initial.copy()
    b.api.foreground_window = lambda: 99
    b.api.focus_window = lambda hwnd: pytest.fail(f"unexpected focus request for {hwnd}")

    with pytest.raises(RuntimeError, match="RuneLite_not_foreground_no_automatic_restore"):
        b.guard()


def authority_backend(
    tmp_path,
    *,
    inventory_count=28,
    inventory_capacity=28,
    inventory_confidence=None,
    mixed_epoch=False,
    exact_envelope=True,
    evaluated_monotonic_s=1.8,
):
    b = backend(tmp_path)
    b.frame_id = 3
    b.now = lambda: evaluated_monotonic_s
    native = Frame.from_raw(
        RawFrame(
            bytes(1005 * 1078 * 4),
            1005,
            1078,
            PixelFormat.BGRA8888,
        ),
        frame_id=1,
        captured_monotonic_s=1.2,
    )
    native_window = {
        "hwnd": 42,
        "title": "RuneLite - Chief Luma",
        "class_name": "SunAwtFrame",
        "is_visible": True,
        "is_minimized": False,
        "client_width": 1005,
        "client_height": 1078,
    }
    b._capture_start_connector_native_frame = lambda: (
        native,
        native_window,
        96,
        str(tmp_path / "native.bgra"),
    )

    unknown_reason = "inventory_v3_unknown" if inventory_count is None else None
    if inventory_confidence is None:
        confidence = 0.0 if inventory_count is None else 1.0
    else:
        confidence = inventory_confidence

    def evaluate_inventory(frame):
        if not exact_envelope:
            return object()
        inventory = InventoryState(inventory_count, inventory_capacity, confidence)
        if mixed_epoch:
            return InventoryPerceptionEnvelope(
                epoch=PerceptionEpoch(
                    capture_source_id="windows-runelite-native",
                    capture_session_id="route-start-connector:42",
                    cycle_id="route-start-connector:42:mixed",
                    cycle_sequence=frame.frame_id,
                    frame_id=frame.frame_id,
                    captured_monotonic_s=frame.captured_monotonic_s,
                    frame_width=frame.width,
                    frame_height=frame.height,
                    frame_payload_sha256=hashlib.sha256(frame.payload).hexdigest(),
                    pixel_format="bgra8888",
                ),
                release=CANONICAL_INVENTORY_RELEASE,
                inventory=inventory,
                unknown_reason=unknown_reason,
            )
        return (
            inventory,
            unknown_reason,
        )

    def resource_evaluator(*args, **kwargs):
        del args, kwargs
        pytest.fail("resource evaluator must not run for navigation authority")

    def composite_evaluator(*args, **kwargs):
        del args, kwargs
        pytest.fail("composite mining evaluator must not run for navigation authority")

    b.prepare_start_connector_authority(
        pose_detectors={"post_third_returned": object()},
        resource_evaluator=resource_evaluator,
        inventory_evaluator=SimpleNamespace(
            evaluate=composite_evaluator,
            _evaluate_packaged_inventory=evaluate_inventory,
            inventory_release=CANONICAL_INVENTORY_RELEASE,
        ),
    )
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


@pytest.mark.parametrize(
    ("overrides", "accepted", "reason"),
    (
        ({}, True, "accepted"),
        ({"inventory_count": 0}, False, "inventory_not_full"),
        ({"inventory_count": None}, False, "inventory_unknown"),
        ({"inventory_confidence": 0.79}, False, "inventory_confidence_below_floor"),
        ({"inventory_confidence": 1}, False, "inventory_confidence_below_floor"),
        (
            {"inventory_count": 27, "inventory_capacity": 27},
            False,
            "inventory_layout_invalid",
        ),
        ({"mixed_epoch": True}, False, "inventory_epoch_mismatch"),
        ({"exact_envelope": False}, False, "inventory_envelope_invalid"),
        ({"evaluated_monotonic_s": 2.21}, False, "inventory_perception_stale"),
    ),
)
def test_native_connector_authority_requires_exact_fresh_full_inventory_epoch(
    tmp_path,
    overrides,
    accepted,
    reason,
):
    b = authority_backend(tmp_path, **overrides)
    route_frame = RouteFrame(
        3,
        1.0,
        np.zeros((1078, 1005, 3), dtype=np.uint8),
        b.initial,
        "route.png",
    )
    receipt = b.verify_start_connector_authority(
        route_frame,
        {"source_pose_id": "post_third_returned"},
    )
    assert receipt["accepted"] is accepted
    assert receipt["reason"] == reason
    assert receipt["route_source_frame_id"] == 3
    assert receipt["native_frame_id"] == 1
    assert receipt["native_captured_monotonic_s"] == 1.2


def test_native_connector_authority_does_not_require_mining_pose_or_resource_state(tmp_path):
    b = authority_backend(tmp_path)
    route_frame = RouteFrame(
        3, 1.0, np.zeros((1078, 1005, 3), dtype=np.uint8), b.initial, "route.png"
    )
    receipt = b.verify_start_connector_authority(
        route_frame, {"source_pose_id": "post_third_returned"}
    )
    assert receipt["accepted"] is True
    assert receipt["reason"] == "accepted"
    assert receipt["authority_kind"] == "navigation_full_inventory"
    assert receipt["expected_pose_id"] == "post_third_returned"
    assert receipt["resource_evaluated"] is False
    assert receipt["mining_pose_evaluated"] is False
    assert receipt["mining_world_state_evaluated"] is False
    assert "pose_id" not in receipt
    assert "resource_view" not in receipt
    assert "world_state" not in receipt


def test_native_connector_authority_rejects_window_change_after_inventory_evaluation(
    tmp_path,
):
    b = authority_backend(tmp_path)
    guards = 0

    def guard():
        nonlocal guards
        guards += 1
        return b.initial.copy() if guards == 1 else {**b.initial, "dpi": 120}

    b.guard = guard
    route_frame = RouteFrame(
        3, 1.0, np.zeros((1078, 1005, 3), dtype=np.uint8), b.initial, "route.png"
    )
    with pytest.raises(RuntimeError, match="window_changed_during_start_connector_authority"):
        b.verify_start_connector_authority(
            route_frame, {"source_pose_id": "post_third_returned"}
        )
    assert b.api.events == []
