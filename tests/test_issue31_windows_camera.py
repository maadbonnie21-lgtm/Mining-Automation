from __future__ import annotations

import sys
from types import ModuleType

import pytest

from mining_automation.validation import (
    EXPECTED_CLIENT_HEIGHT,
    EXPECTED_CLIENT_WIDTH,
    CameraHoldKey,
    CameraInputOperation,
    CameraKeyHold,
    CameraPlan,
    CameraPlanRunner,
    CameraWheel,
    ResetZoomKey,
)
from mining_automation.validation.windows_camera import (
    CAMERA_DRAG_STEP_INTERVAL_SECONDS,
    CAMERA_KEY_RELEASE_SETTLE_SECONDS,
    CAMERA_MIDDLE_ARMING_SETTLE_SECONDS,
    CAMERA_MIDDLE_RELEASE_SETTLE_SECONDS,
    CAMERA_WHEEL_EVENT_INTERVAL_SECONDS,
    COMPASS_CLICK_DWELL_SECONDS,
    RealWindowsCameraApi,
    WindowsCameraControl,
    WindowsCameraError,
    WindowsCameraTargetIdentity,
    _require_complete_window_title_snapshot,
)


class FakeWindowsCameraApi:
    def __init__(self) -> None:
        self.exists = True
        self.identity = WindowsCameraTargetIdentity(
            process_id=456,
            thread_id=789,
            class_name="SunAwtFrame",
            title="RuneLite - Chief Luma",
        )
        self.identity_after_focus: WindowsCameraTargetIdentity | None = None
        self.identity_after_cursor: WindowsCameraTargetIdentity | None = None
        self.identity_after_key_state: WindowsCameraTargetIdentity | None = None
        self.identity_after_root: WindowsCameraTargetIdentity | None = None
        self.identity_after_size: WindowsCameraTargetIdentity | None = None
        self.focus_result = True
        self.foreground: int | None = 123
        self.size = (EXPECTED_CLIENT_WIDTH, EXPECTED_CLIENT_HEIGHT)
        self.screen_offset = (20, 30)
        self.cursor_result = True
        self.cursor = (0, 0)
        self.cursor_after_move: tuple[int, int] | None = None
        self.cursor_position_results: list[tuple[int, int]] = []
        self.root_at_point: int | None = 123
        self.foreign_root_points: set[tuple[int, int]] = set()
        self.mapping_fail_points: set[tuple[int, int]] = set()
        self.foreground_after_cursor: int | None = None
        self.size_after_cursor: tuple[int, int] | None = None
        self.mouse_after_cursor: bool | None = None
        self.middle_after_cursor: bool | None = None
        self.foreground_after_key_state: int | None = None
        self.size_after_key_state: tuple[int, int] | None = None
        self.mouse_down_results: list[int | BaseException] = []
        self.mouse_up_results: list[int | BaseException] = []
        self.middle_down_results: list[int | BaseException] = []
        self.middle_up_results: list[int | BaseException] = []
        self.key_down_results: list[int | BaseException] = []
        self.key_up_results: list[int | BaseException] = []
        self.wheel_results: list[int | BaseException] = []
        self.down_keys: set[int] = set()
        self.defer_key_up_observation = False
        self.mouse_is_down = False
        self.middle_mouse_is_down = False
        self.defer_middle_up_observation = False
        self.calls: list[object] = []

    def declare_dpi_awareness(self) -> None:
        self.calls.append("dpi")

    def is_window(self, hwnd: int) -> bool:
        self.calls.append(("exists", hwnd))
        return self.exists

    def window_identity(self, hwnd: int) -> WindowsCameraTargetIdentity:
        self.calls.append(("identity", hwnd))
        return self.identity

    def focus_window(self, hwnd: int) -> bool:
        self.calls.append(("focus", hwnd))
        if self.focus_result:
            self.foreground = hwnd
        if self.identity_after_focus is not None:
            self.identity = self.identity_after_focus
        return self.focus_result

    def foreground_window(self) -> int | None:
        self.calls.append("foreground")
        return self.foreground

    def client_size(self, hwnd: int) -> tuple[int, int]:
        self.calls.append(("size", hwnd))
        if self.identity_after_size is not None:
            self.identity = self.identity_after_size
        return self.size

    def client_to_screen(self, hwnd: int, x: int, y: int) -> tuple[int, int]:
        self.calls.append(("to-screen", hwnd, x, y))
        if (x, y) in self.mapping_fail_points:
            raise OSError("DPI round trip failed")
        return x + self.screen_offset[0], y + self.screen_offset[1]

    def move_cursor(self, x: int, y: int) -> bool:
        self.calls.append(("cursor", x, y))
        if self.foreground_after_cursor is not None:
            self.foreground = self.foreground_after_cursor
        if self.size_after_cursor is not None:
            self.size = self.size_after_cursor
        if self.mouse_after_cursor is not None:
            self.mouse_is_down = self.mouse_after_cursor
        if self.middle_after_cursor is not None:
            self.middle_mouse_is_down = self.middle_after_cursor
        if self.identity_after_cursor is not None:
            self.identity = self.identity_after_cursor
        if self.cursor_result:
            self.cursor = (
                self.cursor_after_move
                if self.cursor_after_move is not None
                else (x, y)
            )
        return self.cursor_result

    def cursor_position(self) -> tuple[int, int]:
        self.calls.append("cursor-position")
        if self.cursor_position_results:
            return self.cursor_position_results.pop(0)
        return self.cursor

    def root_window_at_point(self, x: int, y: int) -> int | None:
        self.calls.append(("root-at-point", x, y))
        if self.identity_after_root is not None:
            self.identity = self.identity_after_root
        return 999 if (x, y) in self.foreign_root_points else self.root_at_point

    def send_mouse_button(self, *, button_up: bool) -> int:
        self.calls.append(("mouse", button_up))
        results = self.mouse_up_results if button_up else self.mouse_down_results
        result = results.pop(0) if results else 1
        if isinstance(result, BaseException):
            raise result
        if result == 1:
            self.mouse_is_down = not button_up
        return result

    def left_button_is_down(self) -> bool:
        self.calls.append("left-button-is-down")
        return self.mouse_is_down

    def send_middle_button(self, *, button_up: bool) -> int:
        self.calls.append(("middle", button_up))
        results = self.middle_up_results if button_up else self.middle_down_results
        result = results.pop(0) if results else 1
        if isinstance(result, BaseException):
            raise result
        if result == 1 and not (
            button_up and self.defer_middle_up_observation
        ):
            self.middle_mouse_is_down = not button_up
        return result

    def middle_button_is_down(self) -> bool:
        self.calls.append("middle-button-is-down")
        return self.middle_mouse_is_down

    def send_key(self, virtual_key: int, *, key_up: bool, extended: bool) -> int:
        self.calls.append(("key", virtual_key, key_up, extended))
        results = self.key_up_results if key_up else self.key_down_results
        result = results.pop(0) if results else 1
        if isinstance(result, BaseException):
            raise result
        if result == 1 and not (key_up and self.defer_key_up_observation):
            if key_up:
                self.down_keys.discard(virtual_key)
            else:
                self.down_keys.add(virtual_key)
        return result

    def send_wheel(self, detents: int) -> int:
        self.calls.append(("wheel", detents))
        result = self.wheel_results.pop(0) if self.wheel_results else 1
        if isinstance(result, BaseException):
            raise result
        return result

    def key_is_down(self, virtual_key: int) -> bool:
        self.calls.append(("is-down", virtual_key))
        result = virtual_key in self.down_keys
        if self.foreground_after_key_state is not None:
            self.foreground = self.foreground_after_key_state
        if self.size_after_key_state is not None:
            self.size = self.size_after_key_state
        if self.identity_after_key_state is not None:
            self.identity = self.identity_after_key_state
        return result


def _replacement_identity() -> WindowsCameraTargetIdentity:
    return WindowsCameraTargetIdentity(
        process_id=654,
        thread_id=987,
        class_name="ReplacementWindow",
        title="Not RuneLite",
    )


def test_preflight_focuses_exact_window_and_reports_fresh_geometry() -> None:
    api = FakeWindowsCameraApi()
    control = WindowsCameraControl(123, api)

    receipt = control.preflight()

    assert receipt.supported
    assert api.calls == [
        "dpi",
        ("identity", 123),
        ("exists", 123),
        ("identity", 123),
        "left-button-is-down",
        "middle-button-is-down",
        ("is-down", 0x11),
        ("is-down", 0x25),
        ("is-down", 0x26),
        ("is-down", 0x27),
        ("is-down", 0x28),
        ("identity", 123),
        ("focus", 123),
        ("identity", 123),
        "foreground",
        ("size", 123),
        ("identity", 123),
        "left-button-is-down",
        "middle-button-is-down",
        ("is-down", 0x11),
        ("is-down", 0x25),
        ("is-down", 0x26),
        ("is-down", 0x27),
        ("is-down", 0x28),
    ]


def test_preflight_fails_closed_when_focus_cannot_be_verified() -> None:
    api = FakeWindowsCameraApi()
    api.focus_result = False
    api.foreground = 999

    receipt = WindowsCameraControl(123, api).preflight()

    assert not receipt.focused
    assert not receipt.supported


@pytest.mark.parametrize(
    ("expected_class_name", "expected_title", "message"),
    [
        ("ReplacementWindow", "RuneLite - Chief Luma", "class no longer matches"),
        ("SunAwtFrame", "Not RuneLite", "title no longer matches"),
    ],
)
def test_control_rejects_replacement_between_discovery_and_identity_binding(
    expected_class_name: str,
    expected_title: str,
    message: str,
) -> None:
    api = FakeWindowsCameraApi()

    with pytest.raises(WindowsCameraError, match=message):
        WindowsCameraControl(
            123,
            api,
            expected_class_name=expected_class_name,
            expected_title=expected_title,
        )

    assert not any(
        isinstance(item, tuple) and item[0] in {"focus", "mouse", "key", "wheel"}
        for item in api.calls
    )


@pytest.mark.parametrize("held_key", [None, 0x11, 0x25, 0x26, 0x27, 0x28])
def test_preflight_proves_every_controlled_global_input_is_released_before_focus(
    held_key: int | None,
) -> None:
    api = FakeWindowsCameraApi()
    control = WindowsCameraControl(123, api)
    api.calls.clear()
    if held_key is None:
        api.mouse_is_down = True
    else:
        api.down_keys.add(held_key)

    with pytest.raises(WindowsCameraError, match="global left button|global keys"):
        control.preflight()

    assert ("focus", 123) not in api.calls
    assert not any(
        isinstance(item, tuple) and item[0] in {"mouse", "key", "wheel"}
        for item in api.calls
    )


def test_preflight_rechecks_identity_after_global_input_scan_before_focus() -> None:
    api = FakeWindowsCameraApi()
    api.identity_after_key_state = _replacement_identity()
    control = WindowsCameraControl(123, api)

    with pytest.raises(WindowsCameraError, match="window identity changed"):
        control.preflight()

    assert ("focus", 123) not in api.calls


def test_complete_title_snapshot_rejects_same_prefix_growth_race() -> None:
    with pytest.raises(OSError, match="title changed"):
        _require_complete_window_title_snapshot(
            "RuneLite - Chief Luma",
            expected_length=21,
            copied_length=21,
            final_length=27,
        )

    assert (
        _require_complete_window_title_snapshot(
            "RuneLite - Chief Luma",
            expected_length=21,
            copied_length=21,
            final_length=21,
        )
        == "RuneLite - Chief Luma"
    )


def test_preflight_rejects_hwnd_reuse_observed_while_focusing() -> None:
    api = FakeWindowsCameraApi()
    api.identity_after_focus = _replacement_identity()
    control = WindowsCameraControl(123, api)

    with pytest.raises(WindowsCameraError, match="window identity changed"):
        control.preflight()

    assert not any(
        isinstance(item, tuple) and item[0] in {"mouse", "key", "wheel"}
        for item in api.calls
    )


@pytest.mark.parametrize(
    "replacement",
    [
        WindowsCameraTargetIdentity(
            457, 789, "SunAwtFrame", "RuneLite - Chief Luma"
        ),
        WindowsCameraTargetIdentity(
            456, 790, "SunAwtFrame", "RuneLite - Chief Luma"
        ),
        WindowsCameraTargetIdentity(
            456, 789, "ReplacementWindow", "RuneLite - Chief Luma"
        ),
        WindowsCameraTargetIdentity(456, 789, "SunAwtFrame", "Not RuneLite"),
    ],
    ids=["process", "thread", "class", "title"],
)
@pytest.mark.parametrize("method", ["click", "key", "wheel"])
def test_each_input_rejects_reused_hwnd_with_unchanged_focus_and_geometry(
    replacement: WindowsCameraTargetIdentity,
    method: str,
) -> None:
    api = FakeWindowsCameraApi()
    control = WindowsCameraControl(123, api)
    api.identity = replacement

    with pytest.raises(WindowsCameraError, match="window identity changed"):
        if method == "click":
            control.click_compass(608, 49)
        elif method == "key":
            control.key_down("right")
        else:
            control.scroll_camera(400, 50, 1)

    assert api.exists
    assert api.foreground == 123
    assert api.size == (EXPECTED_CLIENT_WIDTH, EXPECTED_CLIENT_HEIGHT)
    assert not any(
        isinstance(item, tuple) and item[0] in {"mouse", "key", "wheel"}
        for item in api.calls
    )


@pytest.mark.parametrize("method", ["click", "key", "wheel"])
def test_each_input_rejects_hwnd_reuse_during_unchanged_geometry_check(
    method: str,
) -> None:
    api = FakeWindowsCameraApi()
    api.identity_after_size = _replacement_identity()
    control = WindowsCameraControl(123, api)

    with pytest.raises(WindowsCameraError, match="window identity changed"):
        if method == "click":
            control.click_compass(608, 49)
        elif method == "key":
            control.key_down("right")
        else:
            control.scroll_camera(400, 50, 1)

    assert api.size == (EXPECTED_CLIENT_WIDTH, EXPECTED_CLIENT_HEIGHT)
    assert not any(
        isinstance(item, tuple) and item[0] in {"mouse", "key", "wheel", "cursor"}
        for item in api.calls
    )


@pytest.mark.parametrize("method", ["click", "wheel"])
def test_pointer_input_rejects_hwnd_reuse_after_cursor_move_without_send_input(
    method: str,
) -> None:
    api = FakeWindowsCameraApi()
    api.identity_after_cursor = _replacement_identity()
    control = WindowsCameraControl(123, api)

    with pytest.raises(WindowsCameraError, match="window identity changed"):
        if method == "click":
            control.click_compass(608, 49)
        else:
            control.scroll_camera(400, 50, 1)

    assert any(
        isinstance(item, tuple) and item[0] == "cursor" for item in api.calls
    )
    assert not any(
        isinstance(item, tuple) and item[0] in {"mouse", "key", "wheel"}
        for item in api.calls
    )


def test_key_input_rejects_hwnd_reuse_after_key_state_query_without_send_input() -> None:
    api = FakeWindowsCameraApi()
    api.identity_after_key_state = _replacement_identity()
    control = WindowsCameraControl(123, api)

    with pytest.raises(WindowsCameraError, match="window identity changed"):
        control.key_down("right")

    assert ("is-down", 0x27) in api.calls
    assert not any(
        isinstance(item, tuple) and item[0] == "key" for item in api.calls
    )


@pytest.mark.parametrize("method", ["click", "wheel"])
def test_pointer_input_rejects_hwnd_reuse_after_root_check_without_send_input(
    method: str,
) -> None:
    api = FakeWindowsCameraApi()
    api.identity_after_root = _replacement_identity()
    control = WindowsCameraControl(123, api)

    with pytest.raises(WindowsCameraError, match="window identity changed"):
        if method == "click":
            control.click_compass(608, 49)
        else:
            control.scroll_camera(400, 50, 1)

    assert any(
        isinstance(item, tuple) and item[0] == "root-at-point"
        for item in api.calls
    )
    assert not any(
        isinstance(item, tuple) and item[0] in {"mouse", "wheel"}
        for item in api.calls
    )


def test_compass_click_uses_client_to_screen_and_exact_event_counts() -> None:
    api = FakeWindowsCameraApi()
    sleeps: list[float] = []

    def record_sleep(duration: float) -> None:
        sleeps.append(duration)
        api.calls.append(("click-sleep", duration))

    control = WindowsCameraControl(123, api, click_sleeper=record_sleep)

    receipt = control.click_compass(608, 49)

    assert receipt.operation is CameraInputOperation.COMPASS_CLICK
    assert receipt.requested_events == 2
    assert receipt.completed_events == 2
    assert ("to-screen", 123, 608, 49) in api.calls
    assert ("cursor", 628, 79) in api.calls
    assert [
        item
        for item in api.calls
        if item == "cursor-position"
        or (
            isinstance(item, tuple)
            and item[0] in {"cursor", "mouse", "click-sleep"}
        )
    ] == [
        ("cursor", 628, 79),
        "cursor-position",
        "cursor-position",
        ("mouse", False),
        ("click-sleep", COMPASS_CLICK_DWELL_SECONDS),
        "cursor-position",
        "cursor-position",
        ("mouse", True),
    ]
    assert sleeps == [COMPASS_CLICK_DWELL_SECONDS]
    assert not api.mouse_is_down


def test_compass_click_never_releases_a_preexisting_user_held_left_button() -> None:
    api = FakeWindowsCameraApi()
    api.mouse_is_down = True
    control = WindowsCameraControl(123, api)

    with pytest.raises(WindowsCameraError, match="left button is already held"):
        control.click_compass(608, 49)

    assert api.mouse_is_down
    assert "left-button-is-down" in api.calls
    assert not any(
        isinstance(item, tuple) and item[0] in {"cursor", "mouse"}
        for item in api.calls
    )


@pytest.mark.parametrize(
    ("held_input", "message"),
    [
        ("middle", "middle button is already held"),
        ("right", "global keys are held: 0x27"),
    ],
)
def test_compass_rechecks_all_inputs_changed_after_preflight_before_any_input(
    held_input: str,
    message: str,
) -> None:
    api = FakeWindowsCameraApi()
    control = WindowsCameraControl(123, api)
    assert control.preflight().supported
    api.calls.clear()
    if held_input == "middle":
        api.middle_mouse_is_down = True
    else:
        api.down_keys.add(0x27)

    with pytest.raises(WindowsCameraError, match=message):
        control.click_compass(608, 49)

    assert not any(
        isinstance(item, tuple) and item[0] in {"cursor", "mouse"}
        for item in api.calls
    )
    assert api.middle_mouse_is_down is (held_input == "middle")
    assert (0x27 in api.down_keys) is (held_input == "right")


def test_arrow_keys_are_extended_and_control_is_not() -> None:
    api = FakeWindowsCameraApi()
    sleeps: list[float] = []
    control = WindowsCameraControl(123, api, key_release_sleeper=sleeps.append)

    control.key_down("up")
    control.key_up("up")
    control.key_down("control")
    control.key_up("control")

    key_calls = [item for item in api.calls if isinstance(item, tuple) and item[0] == "key"]
    assert key_calls == [
        ("key", 0x26, False, True),
        ("key", 0x26, True, True),
        ("key", 0x11, False, False),
        ("key", 0x11, True, False),
    ]
    assert sleeps == [CAMERA_KEY_RELEASE_SETTLE_SECONDS] * 2


def test_key_down_rejects_preexisting_key_state_without_sending_input() -> None:
    api = FakeWindowsCameraApi()
    api.down_keys.add(0x28)
    control = WindowsCameraControl(123, api)

    with pytest.raises(WindowsCameraError, match="already held"):
        control.key_down("down")

    assert not any(
        isinstance(item, tuple) and item[0] == "key" for item in api.calls
    )


def test_plan_runner_never_releases_preexisting_control_key() -> None:
    api = FakeWindowsCameraApi()
    api.down_keys.add(0x11)
    control = WindowsCameraControl(123, api)
    plan = CameraPlan("reset", (ResetZoomKey("control"),))

    with pytest.raises(WindowsCameraError, match="global keys are held"):
        CameraPlanRunner(control, lambda _seconds: None).run(plan)

    assert 0x11 in api.down_keys
    assert not any(
        isinstance(item, tuple) and item[0] == "key" for item in api.calls
    )


def test_key_up_is_sent_but_fails_closed_after_target_loses_focus() -> None:
    api = FakeWindowsCameraApi()
    control = WindowsCameraControl(
        123,
        api,
        key_release_sleeper=lambda _duration: None,
    )
    control.key_down("left")
    api.foreground = 999

    with pytest.raises(WindowsCameraError, match="lost foreground focus"):
        control.key_up("left")

    assert ("key", 0x25, True, True) in api.calls
    assert 0x25 not in api.down_keys
    # Lifecycle cleanup must remain release-capable without restoring focus.
    cleanup = control.release_all_held_keys()
    assert len(cleanup) == 1 and cleanup[0].complete
    assert [
        call
        for call in api.calls
        if isinstance(call, tuple) and call[:3] == ("key", 0x25, True)
    ] == [("key", 0x25, True, True)] * 2


def test_key_up_retries_a_short_release_before_reporting_complete() -> None:
    api = FakeWindowsCameraApi()
    api.key_up_results = [0, 1]
    sleeps: list[float] = []
    control = WindowsCameraControl(123, api, key_release_sleeper=sleeps.append)
    control.key_down("up")

    receipt = control.key_up("up")

    assert receipt.complete
    assert [
        item
        for item in api.calls
        if isinstance(item, tuple) and item[:3] == ("key", 0x26, True)
    ] == [
        ("key", 0x26, True, True),
        ("key", 0x26, True, True),
    ]
    assert sleeps == [CAMERA_KEY_RELEASE_SETTLE_SECONDS]
    assert control.release_all_held_keys() == ()


def test_lifecycle_cleanup_retries_a_key_left_owned_after_short_release() -> None:
    api = FakeWindowsCameraApi()
    api.key_up_results = [0, 0, 0]
    control = WindowsCameraControl(
        123,
        api,
        key_release_sleeper=lambda _duration: None,
    )
    control.key_down("down")

    receipt = control.key_up("down")
    assert not receipt.complete
    assert 0x28 in api.down_keys

    api.key_up_results = [1]
    cleanup_receipts = control.release_all_held_keys()

    assert len(cleanup_receipts) == 1
    assert cleanup_receipts[0].complete
    assert 0x28 not in api.down_keys
    assert control.release_all_held_keys() == ()


def test_key_down_rechecks_focus_and_geometry_after_key_state_query() -> None:
    for change in ("focus", "geometry"):
        api = FakeWindowsCameraApi()
        if change == "focus":
            api.foreground_after_key_state = 999
            error = "lost foreground focus"
        else:
            api.size_after_key_state = (
                EXPECTED_CLIENT_WIDTH - 1,
                EXPECTED_CLIENT_HEIGHT,
            )
            error = "geometry changed"
        control = WindowsCameraControl(123, api)

        with pytest.raises(WindowsCameraError, match=error):
            control.key_down("up")

        assert not any(
            isinstance(item, tuple) and item[0] == "key" for item in api.calls
        )


def test_unowned_key_up_is_refused_without_releasing_external_state() -> None:
    api = FakeWindowsCameraApi()
    api.down_keys.add(0x27)
    control = WindowsCameraControl(123, api)

    with pytest.raises(WindowsCameraError, match="does not own"):
        control.key_up("right")

    assert 0x27 in api.down_keys
    assert not any(
        isinstance(item, tuple) and item[0] == "key" for item in api.calls
    )


def test_key_down_exception_attempts_owned_cleanup_and_preserves_primary_error() -> None:
    api = FakeWindowsCameraApi()
    api.key_down_results = [OSError("down failed")]
    control = WindowsCameraControl(
        123,
        api,
        key_release_sleeper=lambda _duration: None,
    )

    with pytest.raises(OSError, match="down failed"):
        control.key_down("left")

    assert ("key", 0x25, True, True) in api.calls
    assert control.release_all_held_keys() == ()


def test_camera_plan_starts_no_later_action_before_key_release_settle() -> None:
    api = FakeWindowsCameraApi()
    release_sleeps: list[float] = []

    def inspect_release_boundary(duration_s: float) -> None:
        release_sleeps.append(duration_s)
        assert duration_s == CAMERA_KEY_RELEASE_SETTLE_SECONDS
        assert ("key", 0x26, True, True) in api.calls
        assert not any(
            isinstance(call, tuple) and call[0] in {"cursor", "wheel"}
            for call in api.calls
        )

    control = WindowsCameraControl(
        123,
        api,
        key_release_sleeper=inspect_release_boundary,
        wheel_sleeper=lambda _duration: None,
    )
    plan = CameraPlan(
        "key-release-before-wheel",
        (
            CameraKeyHold(CameraHoldKey.UP, 0.1),
            CameraWheel(400, 50, 1),
        ),
    )

    receipt = CameraPlanRunner(control, lambda _duration: None).run(plan)

    assert len(receipt.action_receipts) == 2
    assert release_sleeps == [CAMERA_KEY_RELEASE_SETTLE_SECONDS]
    assert ("wheel", 1) in api.calls


def test_key_release_accepts_delayed_observable_up_after_semantic_settle() -> None:
    api = FakeWindowsCameraApi()
    api.defer_key_up_observation = True
    sleeps: list[float] = []

    def observe_release(duration_s: float) -> None:
        sleeps.append(duration_s)
        api.down_keys.discard(0x26)

    control = WindowsCameraControl(
        123,
        api,
        key_release_sleeper=observe_release,
    )
    control.key_down("up")

    receipt = control.key_up("up")

    assert receipt.complete
    assert sleeps == [CAMERA_KEY_RELEASE_SETTLE_SECONDS]
    assert control.release_all_held_keys() == ()
    assert [
        call
        for call in api.calls
        if isinstance(call, tuple) and call[:3] == ("key", 0x26, True)
    ] == [("key", 0x26, True, True)]


def test_unobservable_key_release_fails_closed_and_retains_ownership() -> None:
    api = FakeWindowsCameraApi()
    api.defer_key_up_observation = True
    control = WindowsCameraControl(
        123,
        api,
        key_release_sleeper=lambda _duration: None,
    )
    control.key_down("down")

    with pytest.raises(WindowsCameraError, match="release was not observable"):
        control.key_up("down")

    assert 0x28 in api.down_keys
    api.defer_key_up_observation = False
    cleanup = control.release_all_held_keys()
    assert len(cleanup) == 1 and cleanup[0].complete
    assert 0x28 not in api.down_keys
    assert [
        call
        for call in api.calls
        if isinstance(call, tuple) and call[:3] == ("key", 0x28, True)
    ] == [("key", 0x28, True, True)] * 2


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("focus", "lost foreground focus"),
        ("geometry", "geometry changed"),
        ("identity", "window identity changed"),
    ],
)
def test_key_release_revalidates_target_after_semantic_settle(
    mutation: str,
    message: str,
) -> None:
    api = FakeWindowsCameraApi()
    mutated = False

    def mutate_target(_duration_s: float) -> None:
        nonlocal mutated
        if mutated:
            return
        mutated = True
        if mutation == "focus":
            api.foreground = 999
        elif mutation == "geometry":
            api.size = (EXPECTED_CLIENT_WIDTH - 1, EXPECTED_CLIENT_HEIGHT)
        else:
            api.identity = _replacement_identity()

    control = WindowsCameraControl(
        123,
        api,
        key_release_sleeper=mutate_target,
    )
    control.key_down("right")

    with pytest.raises(WindowsCameraError, match=message):
        control.key_up("right")

    assert 0x27 not in api.down_keys
    api.foreground = 123
    api.size = (EXPECTED_CLIENT_WIDTH, EXPECTED_CLIENT_HEIGHT)
    api.identity = WindowsCameraTargetIdentity(
        process_id=456,
        thread_id=789,
        class_name="SunAwtFrame",
        title="RuneLite - Chief Luma",
    )
    cleanup = control.release_all_held_keys()
    assert len(cleanup) == 1 and cleanup[0].complete
    assert [
        call
        for call in api.calls
        if isinstance(call, tuple) and call[:3] == ("key", 0x27, True)
    ] == [("key", 0x27, True, True)] * 2


def test_wheel_moves_to_reviewed_client_point_and_preserves_direction() -> None:
    api = FakeWindowsCameraApi()
    sleeps: list[float] = []
    control = WindowsCameraControl(123, api, wheel_sleeper=sleeps.append)

    receipt = control.scroll_camera(400, 50, -12)

    assert receipt.operation is CameraInputOperation.CAMERA_WHEEL
    assert receipt.requested_events == 12
    assert receipt.completed_events == 12
    assert api.calls.count(("to-screen", 123, 400, 50)) == 12
    assert api.calls.count(("cursor", 420, 80)) == 12
    assert [call for call in api.calls if call == ("wheel", -1)] == [
        ("wheel", -1)
    ] * 12
    assert sleeps == [CAMERA_WHEEL_EVENT_INTERVAL_SECONDS] * 11


def test_wheel_stops_after_short_individual_event_and_reports_aggregate() -> None:
    api = FakeWindowsCameraApi()
    api.wheel_results = [1, 1, 0, 1]
    sleeps: list[float] = []
    control = WindowsCameraControl(123, api, wheel_sleeper=sleeps.append)

    receipt = control.scroll_camera(400, 50, 4)

    assert receipt.requested_events == 4
    assert receipt.completed_events == 2
    assert [call for call in api.calls if call == ("wheel", 1)] == [
        ("wheel", 1),
        ("wheel", 1),
        ("wheel", 1),
    ]
    assert sleeps == [CAMERA_WHEEL_EVENT_INTERVAL_SECONDS] * 2


def test_wheel_rejects_invalid_individual_event_count() -> None:
    api = FakeWindowsCameraApi()
    api.wheel_results = [2]

    with pytest.raises(WindowsCameraError, match="invalid camera-wheel event count"):
        WindowsCameraControl(123, api, wheel_sleeper=lambda _duration: None).scroll_camera(
            400, 50, 2
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("focus", "lost foreground focus"),
        ("geometry", "geometry changed"),
        ("button", "left button is already held"),
        ("middle", "middle button is already held"),
        ("overlay", "covered by another top-level window"),
    ],
)
def test_wheel_revalidates_safety_between_paced_detents(
    mutation: str,
    message: str,
) -> None:
    api = FakeWindowsCameraApi()

    def mutate(_duration: float) -> None:
        if mutation == "focus":
            api.foreground = 999
        elif mutation == "geometry":
            api.size = (EXPECTED_CLIENT_WIDTH - 1, EXPECTED_CLIENT_HEIGHT)
        elif mutation == "button":
            api.mouse_is_down = True
        elif mutation == "middle":
            api.middle_mouse_is_down = True
        else:
            api.root_at_point = 999

    control = WindowsCameraControl(123, api, wheel_sleeper=mutate)

    with pytest.raises(WindowsCameraError, match=message):
        control.scroll_camera(400, 50, 2)

    assert [call for call in api.calls if call == ("wheel", 1)] == [("wheel", 1)]


def test_wheel_recomputes_screen_point_after_each_pacing_interval() -> None:
    api = FakeWindowsCameraApi()

    def move_window(_duration: float) -> None:
        api.screen_offset = (120, 230)

    control = WindowsCameraControl(123, api, wheel_sleeper=move_window)

    receipt = control.scroll_camera(400, 50, 2)

    assert receipt.complete
    assert ("cursor", 420, 80) in api.calls
    assert ("cursor", 520, 280) in api.calls


def test_wheel_never_relocates_cursor_while_middle_button_is_down() -> None:
    api = FakeWindowsCameraApi()
    api.middle_mouse_is_down = True
    control = WindowsCameraControl(123, api, wheel_sleeper=lambda _duration: None)

    with pytest.raises(WindowsCameraError, match="middle button is already held"):
        control.scroll_camera(400, 50, 1)

    assert not any(
        isinstance(call, tuple) and call[0] in {"cursor", "wheel"}
        for call in api.calls
    )


def test_wheel_rechecks_middle_button_after_cursor_relocation() -> None:
    api = FakeWindowsCameraApi()
    api.middle_after_cursor = True
    control = WindowsCameraControl(123, api, wheel_sleeper=lambda _duration: None)

    with pytest.raises(WindowsCameraError, match="middle button is already held"):
        control.scroll_camera(400, 50, 1)

    assert ("cursor", 420, 80) in api.calls
    assert ("wheel", 1) not in api.calls


def test_middle_drag_preflights_and_executes_exact_logical_path() -> None:
    api = FakeWindowsCameraApi()
    sleeps: list[float] = []
    control = WindowsCameraControl(123, api, drag_sleeper=sleeps.append)

    receipts = control.drag_camera(200, 600, 9, 0)

    assert tuple(receipt.operation for receipt in receipts) == (
        CameraInputOperation.MIDDLE_DOWN,
        CameraInputOperation.CAMERA_DRAG_MOVE,
        CameraInputOperation.MIDDLE_UP,
    )
    assert tuple(receipt.requested_events for receipt in receipts) == (1, 3, 1)
    assert all(receipt.complete for receipt in receipts)
    assert [
        call for call in api.calls if isinstance(call, tuple) and call[0] == "cursor"
    ] == [
        ("cursor", 220, 630),
        ("cursor", 224, 630),
        ("cursor", 228, 630),
        ("cursor", 229, 630),
    ]
    root_points = {
        (call[1], call[2])
        for call in api.calls
        if isinstance(call, tuple) and call[0] == "root-at-point"
    }
    assert {(220, 630), (224, 630), (228, 630), (229, 630)} <= root_points
    assert [call for call in api.calls if isinstance(call, tuple) and call[0] == "middle"] == [
        ("middle", False),
        ("middle", True),
    ]
    assert sleeps == [
        CAMERA_MIDDLE_ARMING_SETTLE_SECONDS,
        CAMERA_DRAG_STEP_INTERVAL_SECONDS,
        CAMERA_DRAG_STEP_INTERVAL_SECONDS,
        CAMERA_DRAG_STEP_INTERVAL_SECONDS,
        CAMERA_MIDDLE_RELEASE_SETTLE_SECONDS,
    ]
    assert not api.middle_mouse_is_down


def test_middle_drag_preflights_every_path_mapping_before_button_down() -> None:
    api = FakeWindowsCameraApi()
    api.mapping_fail_points.add((208, 600))
    control = WindowsCameraControl(123, api, drag_sleeper=lambda _duration: None)

    with pytest.raises(OSError, match="DPI round trip failed"):
        control.drag_camera(200, 600, 9, 0)

    assert not any(
        isinstance(call, tuple) and call[0] in {"cursor", "middle"}
        for call in api.calls
    )


def test_middle_drag_sends_no_move_before_full_arming_settle() -> None:
    api = FakeWindowsCameraApi()
    sleep_calls = 0

    def inspect_before_arming_returns(duration_s: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls != 1:
            return
        assert duration_s == CAMERA_MIDDLE_ARMING_SETTLE_SECONDS
        assert [
            call
            for call in api.calls
            if isinstance(call, tuple) and call[0] == "cursor"
        ] == [("cursor", 220, 630)]
        assert api.calls[-1] == ("middle", False)

    control = WindowsCameraControl(
        123,
        api,
        drag_sleeper=inspect_before_arming_returns,
    )

    receipts = control.drag_camera(200, 600, 4, 0)

    assert all(receipt.complete for receipt in receipts)
    assert sleep_calls == 3


def test_middle_drag_completes_each_step_settle_before_the_next_move() -> None:
    api = FakeWindowsCameraApi()
    settle_cursor_paths: list[list[tuple[object, ...]]] = []

    def inspect_before_each_settle_returns(duration_s: float) -> None:
        assert duration_s == CAMERA_DRAG_STEP_INTERVAL_SECONDS
        settle_cursor_paths.append(
            [
                call
                for call in api.calls
                if isinstance(call, tuple) and call[0] == "cursor"
            ]
        )

    control = WindowsCameraControl(
        123,
        api,
        drag_sleeper=inspect_before_each_settle_returns,
    )

    receipts = control.drag_camera(200, 600, 8, 0)

    assert all(receipt.complete for receipt in receipts)
    assert settle_cursor_paths == [
        [("cursor", 220, 630)],
        [("cursor", 220, 630), ("cursor", 224, 630)],
        [
            ("cursor", 220, 630),
            ("cursor", 224, 630),
            ("cursor", 228, 630),
        ],
        [
            ("cursor", 220, 630),
            ("cursor", 224, 630),
            ("cursor", 228, 630),
        ],
    ]


@pytest.mark.parametrize("blocked_logical_point", [(200, 600), (204, 600), (209, 600)])
def test_middle_drag_preflights_start_path_and_endpoint_root_ownership(
    blocked_logical_point: tuple[int, int],
) -> None:
    api = FakeWindowsCameraApi()
    api.foreign_root_points.add(
        (
            blocked_logical_point[0] + api.screen_offset[0],
            blocked_logical_point[1] + api.screen_offset[1],
        )
    )
    control = WindowsCameraControl(123, api, drag_sleeper=lambda _duration: None)

    with pytest.raises(WindowsCameraError, match="covered by another top-level window"):
        control.drag_camera(200, 600, 9, 0)

    assert not any(
        isinstance(call, tuple) and call[0] in {"cursor", "middle"}
        for call in api.calls
    )


def test_middle_drag_rechecks_destination_root_before_held_cursor_move() -> None:
    api = FakeWindowsCameraApi()
    sleep_count = 0

    def cover_first_path_point(_duration: float) -> None:
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count == 1:
            api.foreign_root_points.add((224, 630))

    control = WindowsCameraControl(123, api, drag_sleeper=cover_first_path_point)

    with pytest.raises(WindowsCameraError, match="covered by another top-level window"):
        control.drag_camera(200, 600, 8, 0)

    assert [
        call for call in api.calls if isinstance(call, tuple) and call[0] == "cursor"
    ] == [("cursor", 220, 630)]
    assert ("middle", False) in api.calls
    assert [
        call
        for call in api.calls
        if isinstance(call, tuple) and call[0] == "middle"
    ][-1] == ("middle", True)
    assert not api.middle_mouse_is_down


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("focus", "lost foreground focus"),
        ("geometry", "geometry changed"),
        ("identity", "window identity changed"),
        ("middle", "lost its owned middle-button hold"),
        ("left", "left button is already held"),
        ("cursor", "cursor moved during its bounded settle"),
        ("overlay", "covered by another top-level window"),
    ],
)
def test_middle_drag_revalidates_safety_after_arming_settle(
    mutation: str,
    message: str,
) -> None:
    api = FakeWindowsCameraApi()

    def mutate(_duration: float) -> None:
        if mutation == "focus":
            api.foreground = 999
        elif mutation == "geometry":
            api.size = (EXPECTED_CLIENT_WIDTH - 1, EXPECTED_CLIENT_HEIGHT)
        elif mutation == "identity":
            api.identity = _replacement_identity()
        elif mutation == "middle":
            api.middle_mouse_is_down = False
        elif mutation == "left":
            api.mouse_is_down = True
        elif mutation == "cursor":
            api.cursor = (221, 630)
        else:
            api.foreign_root_points.add((220, 630))

    control = WindowsCameraControl(123, api, drag_sleeper=mutate)

    with pytest.raises(WindowsCameraError, match=message):
        control.drag_camera(200, 600, 8, 0)

    assert ("middle", False) in api.calls
    assert [
        call
        for call in api.calls
        if isinstance(call, tuple) and call[0] == "middle"
    ][-1] == ("middle", True)
    assert not api.middle_mouse_is_down


@pytest.mark.parametrize("held_button", ["left", "middle"])
def test_middle_drag_never_releases_a_preexisting_user_button(
    held_button: str,
) -> None:
    api = FakeWindowsCameraApi()
    if held_button == "left":
        api.mouse_is_down = True
    else:
        api.middle_mouse_is_down = True
    control = WindowsCameraControl(123, api)

    with pytest.raises(WindowsCameraError, match=f"{held_button} button is already held"):
        control.drag_camera(200, 600, 8, 0)

    assert api.mouse_is_down is (held_button == "left")
    assert api.middle_mouse_is_down is (held_button == "middle")
    assert not any(
        isinstance(call, tuple) and call[0] == "middle" for call in api.calls
    )


def test_preflight_rejects_preheld_middle_without_releasing_it() -> None:
    api = FakeWindowsCameraApi()
    api.middle_mouse_is_down = True
    control = WindowsCameraControl(123, api)

    with pytest.raises(WindowsCameraError, match="global middle button is held"):
        control.preflight()

    assert api.middle_mouse_is_down
    assert not any(
        isinstance(call, tuple) and call[0] == "middle" for call in api.calls
    )


@pytest.mark.parametrize(
    ("x", "y", "delta_x", "delta_y"),
    [
        (199, 600, 8, 0),
        (200, 600, 0, 0),
        (200, 600, 8, 8),
        (200, 600, True, 0),
        (200, 600, 257, 0),
        (200, 600, 0, 250),
    ],
)
def test_middle_drag_rejects_unreviewed_or_unbounded_calls_before_input(
    x: int,
    y: int,
    delta_x: int,
    delta_y: int,
) -> None:
    api = FakeWindowsCameraApi()
    control = WindowsCameraControl(123, api)

    with pytest.raises(WindowsCameraError):
        control.drag_camera(x, y, delta_x, delta_y)

    assert not any(
        isinstance(call, tuple) and call[0] in {"cursor", "middle"}
        for call in api.calls
    )


def test_middle_drag_start_cursor_readback_failure_aborts_before_down() -> None:
    api = FakeWindowsCameraApi()
    api.cursor_after_move = (0, 0)
    control = WindowsCameraControl(123, api)

    with pytest.raises(WindowsCameraError, match="did not reach"):
        control.drag_camera(200, 600, 8, 0)

    assert not any(
        isinstance(call, tuple) and call[0] == "middle" for call in api.calls
    )


def test_middle_drag_path_cursor_readback_failure_releases_middle() -> None:
    api = FakeWindowsCameraApi()
    sleep_count = 0

    def make_next_move_misland(_duration: float) -> None:
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count == 1:
            api.cursor_after_move = (0, 0)

    control = WindowsCameraControl(123, api, drag_sleeper=make_next_move_misland)

    with pytest.raises(WindowsCameraError, match="did not reach"):
        control.drag_camera(200, 600, 8, 0)

    assert ("middle", False) in api.calls
    assert [
        call
        for call in api.calls
        if isinstance(call, tuple) and call[0] == "middle"
    ][-1] == ("middle", True)
    assert not api.middle_mouse_is_down


def test_middle_drag_short_down_skips_motion_and_returns_fail_closed_receipt() -> None:
    api = FakeWindowsCameraApi()
    api.middle_down_results = [0]
    sleeps: list[float] = []
    control = WindowsCameraControl(123, api, drag_sleeper=sleeps.append)

    receipts = control.drag_camera(200, 600, 8, 0)

    assert tuple(receipt.completed_events for receipt in receipts) == (0, 0, 1)
    assert sleeps == [CAMERA_MIDDLE_RELEASE_SETTLE_SECONDS]
    assert [
        call for call in api.calls if isinstance(call, tuple) and call[0] == "cursor"
    ] == [("cursor", 220, 630)]
    assert not api.middle_mouse_is_down


def test_middle_drag_down_exception_still_attempts_compensating_up() -> None:
    api = FakeWindowsCameraApi()
    api.middle_down_results = [OSError("middle down failed")]
    control = WindowsCameraControl(123, api)

    with pytest.raises(OSError, match="middle down failed"):
        control.drag_camera(200, 600, 8, 0)

    assert [
        call
        for call in api.calls
        if isinstance(call, tuple) and call[0] == "middle"
    ] == [("middle", False), ("middle", True)]
    assert not api.middle_mouse_is_down


def test_middle_drag_sleeper_failure_still_releases_middle() -> None:
    api = FakeWindowsCameraApi()

    def fail_sleep(_duration: float) -> None:
        raise OSError("drag sleep failed")

    control = WindowsCameraControl(123, api, drag_sleeper=fail_sleep)

    with pytest.raises(OSError, match="drag sleep failed"):
        control.drag_camera(200, 600, 8, 0)

    assert api.calls[-1] == ("middle", True)
    assert not api.middle_mouse_is_down


def test_middle_drag_short_up_remains_owned_for_lifecycle_cleanup() -> None:
    api = FakeWindowsCameraApi()
    api.middle_up_results = [0, 0, 0]
    control = WindowsCameraControl(123, api, drag_sleeper=lambda _duration: None)

    receipts = control.drag_camera(200, 600, 4, 0)

    assert not receipts[-1].complete
    assert api.middle_mouse_is_down
    api.middle_up_results = [1]
    assert control.release_all_held_keys() == ()
    assert not api.middle_mouse_is_down


def test_middle_drag_waits_for_observable_release_before_returning() -> None:
    api = FakeWindowsCameraApi()
    api.defer_middle_up_observation = True
    sleeps: list[float] = []

    def observe_release_after_settle(duration_s: float) -> None:
        sleeps.append(duration_s)
        if api.calls[-1] == ("middle", True):
            api.middle_mouse_is_down = False

    control = WindowsCameraControl(
        123,
        api,
        drag_sleeper=observe_release_after_settle,
    )

    receipts = control.drag_camera(200, 600, 4, 0)

    assert all(receipt.complete for receipt in receipts)
    assert sleeps[-1] == CAMERA_MIDDLE_RELEASE_SETTLE_SECONDS
    assert not api.middle_mouse_is_down
    assert control.release_all_held_keys() == ()
    assert [
        call
        for call in api.calls
        if isinstance(call, tuple) and call[0] == "middle"
    ] == [("middle", False), ("middle", True)]


def test_unobservable_middle_release_fails_closed_and_retains_ownership() -> None:
    api = FakeWindowsCameraApi()
    api.defer_middle_up_observation = True
    sleeps: list[float] = []
    control = WindowsCameraControl(123, api, drag_sleeper=sleeps.append)

    with pytest.raises(WindowsCameraError, match="release was not observable"):
        control.drag_camera(200, 600, 4, 0)

    assert sleeps[-1] == CAMERA_MIDDLE_RELEASE_SETTLE_SECONDS
    assert api.middle_mouse_is_down
    api.defer_middle_up_observation = False
    assert control.release_all_held_keys() == ()
    assert not api.middle_mouse_is_down
    assert [
        call
        for call in api.calls
        if isinstance(call, tuple) and call[0] == "middle"
    ][-2:] == [("middle", True), ("middle", True)]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("focus", "lost foreground focus"),
        ("geometry", "geometry changed"),
        ("cursor", "cursor moved during its post-release settle"),
        ("overlay", "covered by another top-level window"),
    ],
)
def test_middle_drag_revalidates_final_endpoint_after_release_settle(
    mutation: str,
    message: str,
) -> None:
    api = FakeWindowsCameraApi()
    mutated = False

    def mutate_after_release(_duration_s: float) -> None:
        nonlocal mutated
        if api.calls[-1] != ("middle", True) or mutated:
            return
        mutated = True
        if mutation == "focus":
            api.foreground = 999
        elif mutation == "geometry":
            api.size = (EXPECTED_CLIENT_WIDTH - 1, EXPECTED_CLIENT_HEIGHT)
        elif mutation == "cursor":
            api.cursor = (0, 0)
        else:
            api.foreign_root_points.add((224, 630))

    control = WindowsCameraControl(123, api, drag_sleeper=mutate_after_release)

    with pytest.raises(WindowsCameraError, match=message):
        control.drag_camera(200, 600, 4, 0)

    assert api.calls.count(("middle", True)) == 1
    assert not api.middle_mouse_is_down
    api.foreground = 123
    api.size = (EXPECTED_CLIENT_WIDTH, EXPECTED_CLIENT_HEIGHT)
    api.cursor = (224, 630)
    api.foreign_root_points.clear()
    assert control.release_all_held_keys() == ()
    assert api.calls.count(("middle", True)) == 2


def test_partial_mouse_click_retries_button_up_until_released() -> None:
    api = FakeWindowsCameraApi()
    api.mouse_up_results = [0, 1]
    control = WindowsCameraControl(123, api)

    receipt = control.click_compass(608, 49)

    assert receipt.complete
    assert [
        item
        for item in api.calls
        if isinstance(item, tuple) and item[0] == "mouse"
    ] == [
        ("mouse", False),
        ("mouse", True),
        ("mouse", True),
    ]
    assert not api.mouse_is_down


def test_mouse_up_exception_is_retried_until_the_button_is_released() -> None:
    api = FakeWindowsCameraApi()
    api.mouse_up_results = [OSError("up failed"), 1]
    control = WindowsCameraControl(123, api)

    receipt = control.click_compass(608, 49)

    assert receipt.complete
    assert [
        item
        for item in api.calls
        if isinstance(item, tuple) and item[0] == "mouse"
    ] == [
        ("mouse", False),
        ("mouse", True),
        ("mouse", True),
    ]
    assert not api.mouse_is_down


def test_mouse_down_exception_still_attempts_compensating_button_up() -> None:
    api = FakeWindowsCameraApi()
    api.mouse_down_results = [OSError("mouse down failed")]
    control = WindowsCameraControl(123, api)

    with pytest.raises(OSError, match="mouse down failed"):
        control.click_compass(608, 49)

    assert api.calls[-2:] == [("mouse", False), ("mouse", True)]
    assert not api.mouse_is_down


def test_unreleased_partial_mouse_click_is_reported_after_bounded_up_attempts() -> None:
    api = FakeWindowsCameraApi()
    api.mouse_up_results = [0, 0, 0]
    control = WindowsCameraControl(123, api)

    receipt = control.click_compass(608, 49)

    assert not receipt.complete
    assert receipt.completed_events == 1
    assert api.calls[-3:] == [("mouse", True), ("mouse", True), ("mouse", True)]
    assert api.mouse_is_down

    api.mouse_up_results = [1]
    assert control.release_all_held_keys() == ()
    assert not api.mouse_is_down


def test_lifecycle_cleanup_attempts_mouse_and_every_key_after_one_failure() -> None:
    api = FakeWindowsCameraApi()
    api.mouse_up_results = [0, 0, 0]
    control = WindowsCameraControl(123, api)
    partial_click = control.click_compass(608, 49)
    assert not partial_click.complete
    control.key_down("right")

    api.mouse_up_results = [0, 0, 0]
    with pytest.raises(WindowsCameraError, match="left mouse button"):
        control.release_all_held_keys()

    assert 0x27 not in api.down_keys
    assert api.mouse_is_down

    api.mouse_up_results = [1]
    assert control.release_all_held_keys() == ()
    assert not api.mouse_is_down


@pytest.mark.parametrize("method", ["click", "key", "wheel"])
def test_each_input_rechecks_focus_and_geometry(method: str) -> None:
    api = FakeWindowsCameraApi()
    api.size = (EXPECTED_CLIENT_WIDTH, EXPECTED_CLIENT_HEIGHT - 1)
    control = WindowsCameraControl(123, api)

    with pytest.raises(WindowsCameraError, match="geometry changed"):
        if method == "click":
            control.click_compass(608, 49)
        elif method == "key":
            control.key_down("right")
        else:
            control.scroll_camera(400, 50, 1)

    assert not any(
        isinstance(item, tuple) and item[0] == "mouse" for item in api.calls
    )
    assert not any(
        isinstance(item, tuple) and item[0] in {"key", "wheel"} for item in api.calls
    )


@pytest.mark.parametrize("method", ["click", "wheel"])
def test_pointer_input_rechecks_focus_after_cursor_move(method: str) -> None:
    api = FakeWindowsCameraApi()
    api.foreground_after_cursor = 999
    control = WindowsCameraControl(123, api)

    with pytest.raises(WindowsCameraError, match="lost foreground focus"):
        if method == "click":
            control.click_compass(608, 49)
        else:
            control.scroll_camera(400, 50, 1)

    assert not any(
        isinstance(item, tuple) and item[0] in {"mouse", "wheel"}
        for item in api.calls
    )


@pytest.mark.parametrize("method", ["click", "wheel"])
def test_pointer_input_rechecks_geometry_after_cursor_move(method: str) -> None:
    api = FakeWindowsCameraApi()
    api.size_after_cursor = (EXPECTED_CLIENT_WIDTH - 1, EXPECTED_CLIENT_HEIGHT)
    control = WindowsCameraControl(123, api)

    with pytest.raises(WindowsCameraError, match="geometry changed"):
        if method == "click":
            control.click_compass(608, 49)
        else:
            control.scroll_camera(400, 50, 1)

    assert not any(
        isinstance(item, tuple) and item[0] in {"mouse", "wheel"}
        for item in api.calls
    )


@pytest.mark.parametrize(
    ("method", "expected_screen_point"),
    [("click", (628, 79)), ("wheel", (420, 80))],
)
def test_pointer_input_rejects_no_activate_overlay_covering_reviewed_point(
    method: str,
    expected_screen_point: tuple[int, int],
) -> None:
    api = FakeWindowsCameraApi()
    api.root_at_point = 999
    control = WindowsCameraControl(123, api)

    with pytest.raises(WindowsCameraError, match="covered by another top-level window"):
        if method == "click":
            control.click_compass(608, 49)
        else:
            control.scroll_camera(400, 50, 1)

    assert api.foreground == 123
    assert ("root-at-point", *expected_screen_point) in api.calls
    assert not any(
        isinstance(item, tuple) and item[0] in {"mouse", "wheel"}
        for item in api.calls
    )


def test_wheel_refuses_preexisting_left_button_without_moving_or_releasing_it() -> None:
    api = FakeWindowsCameraApi()
    api.mouse_is_down = True
    control = WindowsCameraControl(123, api)

    with pytest.raises(WindowsCameraError, match="left button is already held"):
        control.scroll_camera(400, 50, 1)

    assert api.mouse_is_down
    assert not any(
        isinstance(item, tuple) and item[0] in {"cursor", "wheel"}
        for item in api.calls
    )


def test_wheel_rechecks_left_button_after_cursor_move_before_scrolling() -> None:
    api = FakeWindowsCameraApi()
    api.mouse_after_cursor = True
    control = WindowsCameraControl(123, api)

    with pytest.raises(WindowsCameraError, match="left button is already held"):
        control.scroll_camera(400, 50, 1)

    assert api.mouse_is_down
    assert any(
        isinstance(item, tuple) and item[0] == "cursor" for item in api.calls
    )
    assert ("wheel", 1) not in api.calls


@pytest.mark.parametrize(
    ("method", "x", "y"),
    [
        ("click", 607, 49),
        ("click", 608, 50),
        ("wheel", 399, 50),
        ("wheel", 400, 51),
    ],
)
def test_adapter_rejects_nonreviewed_pointer_coordinates_before_input(
    method: str,
    x: int,
    y: int,
) -> None:
    api = FakeWindowsCameraApi()
    control = WindowsCameraControl(123, api)

    with pytest.raises(WindowsCameraError, match="exact reviewed client point"):
        if method == "click":
            control.click_compass(x, y)
        else:
            control.scroll_camera(x, y, 1)

    assert api.calls == ["dpi", ("identity", 123)]


def test_cursor_move_failure_aborts_before_click() -> None:
    api = FakeWindowsCameraApi()
    api.cursor_result = False

    with pytest.raises(WindowsCameraError, match="refused to move"):
        WindowsCameraControl(123, api).click_compass(608, 49)

    assert not any(
        isinstance(item, tuple) and item[0] == "mouse" for item in api.calls
    )


@pytest.mark.parametrize("method", ["click", "wheel"])
def test_pointer_input_rejects_successful_but_mislanded_cursor_move(
    method: str,
) -> None:
    api = FakeWindowsCameraApi()
    api.cursor_after_move = (1, 2)
    control = WindowsCameraControl(123, api)

    with pytest.raises(WindowsCameraError, match="did not reach"):
        if method == "click":
            control.click_compass(608, 49)
        else:
            control.scroll_camera(400, 50, 1)

    assert "cursor-position" in api.calls
    assert not any(
        isinstance(item, tuple) and item[0] in {"mouse", "wheel", "root-at-point"}
        for item in api.calls
    )


@pytest.mark.parametrize("method", ["click", "wheel"])
def test_pointer_input_rejects_cursor_move_before_final_ownership_check(
    method: str,
) -> None:
    api = FakeWindowsCameraApi()
    expected = (628, 79) if method == "click" else (420, 80)
    api.cursor_position_results = [expected, (1, 2)]
    control = WindowsCameraControl(123, api)

    with pytest.raises(WindowsCameraError, match="before final pointer ownership"):
        if method == "click":
            control.click_compass(608, 49)
        else:
            control.scroll_camera(400, 50, 1)

    assert not any(
        isinstance(item, tuple) and item[0] in {"mouse", "wheel", "root-at-point"}
        for item in api.calls
    )


def test_compass_click_short_down_skips_dwell_but_compensates_up() -> None:
    api = FakeWindowsCameraApi()
    api.mouse_down_results = [0]

    def unexpected_sleep(_duration: float) -> None:
        raise AssertionError("short left-down must not dwell")

    control = WindowsCameraControl(123, api, click_sleeper=unexpected_sleep)

    receipt = control.click_compass(608, 49)

    assert not receipt.complete
    assert receipt.completed_events == 1
    assert api.calls[-2:] == [("mouse", False), ("mouse", True)]


def test_compass_click_dwell_failure_always_releases_owned_button() -> None:
    api = FakeWindowsCameraApi()

    def fail_dwell(_duration: float) -> None:
        raise OSError("click dwell failed")

    control = WindowsCameraControl(123, api, click_sleeper=fail_dwell)

    with pytest.raises(OSError, match="click dwell failed"):
        control.click_compass(608, 49)

    assert api.calls[-1] == ("mouse", True)
    assert not api.mouse_is_down


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("focus", "lost foreground focus"),
        ("geometry", "geometry changed"),
        ("button", "lost its owned left-button hold"),
        ("overlay", "covered by another top-level window"),
        ("cursor", "cursor moved during its bounded dwell"),
    ],
)
def test_compass_click_revalidates_unchanged_point_after_dwell_and_releases(
    mutation: str,
    message: str,
) -> None:
    api = FakeWindowsCameraApi()

    def mutate(_duration: float) -> None:
        if mutation == "focus":
            api.foreground = 999
        elif mutation == "geometry":
            api.size = (EXPECTED_CLIENT_WIDTH - 1, EXPECTED_CLIENT_HEIGHT)
        elif mutation == "button":
            api.mouse_is_down = False
        elif mutation == "overlay":
            api.root_at_point = 999
        else:
            api.cursor = (627, 79)

    control = WindowsCameraControl(123, api, click_sleeper=mutate)

    with pytest.raises(WindowsCameraError, match=message):
        control.click_compass(608, 49)

    assert api.calls[-1] == ("mouse", True)
    assert not api.mouse_is_down


def test_unknown_key_is_rejected_before_input() -> None:
    api = FakeWindowsCameraApi()

    with pytest.raises(WindowsCameraError, match="only arrow keys"):
        WindowsCameraControl(123, api).key_down("space")

    assert not any(
        isinstance(item, tuple) and item[0] == "key" for item in api.calls
    )


def test_real_api_rejects_non_windows_before_loading_win32(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")

    with pytest.raises(RuntimeError, match="requires Windows"):
        RealWindowsCameraApi()


def test_real_api_materializes_stable_window_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_name = "mining_automation.validation._camera_win32_calls"
    calls = ModuleType(module_name)
    calls.window_identity = lambda hwnd: (456, 789, "SunAwtFrame", f"RuneLite {hwnd}")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module_name, calls)
    monkeypatch.setattr(sys, "platform", "win32")

    identity = RealWindowsCameraApi().window_identity(123)

    assert identity == WindowsCameraTargetIdentity(
        process_id=456,
        thread_id=789,
        class_name="SunAwtFrame",
        title="RuneLite 123",
    )


@pytest.mark.parametrize("hwnd", [0, -1, True, 1.5])
def test_control_requires_positive_integer_hwnd(hwnd: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        WindowsCameraControl(hwnd, FakeWindowsCameraApi())  # type: ignore[arg-type]
