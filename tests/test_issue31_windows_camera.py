from __future__ import annotations

import sys

import pytest

from mining_automation.validation import (
    EXPECTED_CLIENT_HEIGHT,
    EXPECTED_CLIENT_WIDTH,
    CameraInputOperation,
    CameraPlan,
    CameraPlanRunner,
    ResetZoomKey,
)
from mining_automation.validation.windows_camera import (
    RealWindowsCameraApi,
    WindowsCameraControl,
    WindowsCameraError,
)


class FakeWindowsCameraApi:
    def __init__(self) -> None:
        self.exists = True
        self.focus_result = True
        self.foreground: int | None = 123
        self.size = (EXPECTED_CLIENT_WIDTH, EXPECTED_CLIENT_HEIGHT)
        self.screen_offset = (20, 30)
        self.cursor_result = True
        self.root_at_point: int | None = 123
        self.foreground_after_cursor: int | None = None
        self.size_after_cursor: tuple[int, int] | None = None
        self.mouse_after_cursor: bool | None = None
        self.foreground_after_key_state: int | None = None
        self.size_after_key_state: tuple[int, int] | None = None
        self.mouse_down_results: list[int | BaseException] = []
        self.mouse_up_results: list[int | BaseException] = []
        self.key_down_results: list[int | BaseException] = []
        self.key_up_results: list[int | BaseException] = []
        self.wheel_count: int | None = None
        self.down_keys: set[int] = set()
        self.mouse_is_down = False
        self.calls: list[object] = []

    def declare_dpi_awareness(self) -> None:
        self.calls.append("dpi")

    def is_window(self, hwnd: int) -> bool:
        self.calls.append(("exists", hwnd))
        return self.exists

    def focus_window(self, hwnd: int) -> bool:
        self.calls.append(("focus", hwnd))
        if self.focus_result:
            self.foreground = hwnd
        return self.focus_result

    def foreground_window(self) -> int | None:
        self.calls.append("foreground")
        return self.foreground

    def client_size(self, hwnd: int) -> tuple[int, int]:
        self.calls.append(("size", hwnd))
        return self.size

    def client_to_screen(self, hwnd: int, x: int, y: int) -> tuple[int, int]:
        self.calls.append(("to-screen", hwnd, x, y))
        return x + self.screen_offset[0], y + self.screen_offset[1]

    def move_cursor(self, x: int, y: int) -> bool:
        self.calls.append(("cursor", x, y))
        if self.foreground_after_cursor is not None:
            self.foreground = self.foreground_after_cursor
        if self.size_after_cursor is not None:
            self.size = self.size_after_cursor
        if self.mouse_after_cursor is not None:
            self.mouse_is_down = self.mouse_after_cursor
        return self.cursor_result

    def root_window_at_point(self, x: int, y: int) -> int | None:
        self.calls.append(("root-at-point", x, y))
        return self.root_at_point

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

    def send_key(self, virtual_key: int, *, key_up: bool, extended: bool) -> int:
        self.calls.append(("key", virtual_key, key_up, extended))
        results = self.key_up_results if key_up else self.key_down_results
        result = results.pop(0) if results else 1
        if isinstance(result, BaseException):
            raise result
        if result == 1:
            if key_up:
                self.down_keys.discard(virtual_key)
            else:
                self.down_keys.add(virtual_key)
        return result

    def send_wheel(self, detents: int) -> int:
        self.calls.append(("wheel", detents))
        return abs(detents) if self.wheel_count is None else self.wheel_count

    def key_is_down(self, virtual_key: int) -> bool:
        self.calls.append(("is-down", virtual_key))
        result = virtual_key in self.down_keys
        if self.foreground_after_key_state is not None:
            self.foreground = self.foreground_after_key_state
        if self.size_after_key_state is not None:
            self.size = self.size_after_key_state
        return result


def test_preflight_focuses_exact_window_and_reports_fresh_geometry() -> None:
    api = FakeWindowsCameraApi()
    control = WindowsCameraControl(123, api)

    receipt = control.preflight()

    assert receipt.supported
    assert api.calls == [
        "dpi",
        ("exists", 123),
        ("focus", 123),
        "foreground",
        ("size", 123),
    ]


def test_preflight_fails_closed_when_focus_cannot_be_verified() -> None:
    api = FakeWindowsCameraApi()
    api.focus_result = False
    api.foreground = 999

    receipt = WindowsCameraControl(123, api).preflight()

    assert not receipt.focused
    assert not receipt.supported


def test_compass_click_uses_client_to_screen_and_exact_event_counts() -> None:
    api = FakeWindowsCameraApi()
    control = WindowsCameraControl(123, api)

    receipt = control.click_compass(608, 49)

    assert receipt.operation is CameraInputOperation.COMPASS_CLICK
    assert receipt.requested_events == 2
    assert receipt.completed_events == 2
    assert ("to-screen", 123, 608, 49) in api.calls
    assert ("cursor", 628, 79) in api.calls
    assert api.calls[-2:] == [("mouse", False), ("mouse", True)]
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


def test_arrow_keys_are_extended_and_control_is_not() -> None:
    api = FakeWindowsCameraApi()
    control = WindowsCameraControl(123, api)

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

    with pytest.raises(WindowsCameraError, match="already held"):
        CameraPlanRunner(control, lambda _seconds: None).run(plan)

    assert 0x11 in api.down_keys
    assert not any(
        isinstance(item, tuple) and item[0] == "key" for item in api.calls
    )


def test_key_up_is_still_sent_after_target_loses_focus() -> None:
    api = FakeWindowsCameraApi()
    control = WindowsCameraControl(123, api)
    control.key_down("left")
    api.foreground = 999

    receipt = control.key_up("left")

    assert receipt.complete
    assert api.calls[-1] == ("key", 0x25, True, True)


def test_key_up_retries_a_short_release_before_reporting_complete() -> None:
    api = FakeWindowsCameraApi()
    api.key_up_results = [0, 1]
    control = WindowsCameraControl(123, api)
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
    assert control.release_all_held_keys() == ()


def test_lifecycle_cleanup_retries_a_key_left_owned_after_short_release() -> None:
    api = FakeWindowsCameraApi()
    api.key_up_results = [0, 0, 0]
    control = WindowsCameraControl(123, api)
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
    control = WindowsCameraControl(123, api)

    with pytest.raises(OSError, match="down failed"):
        control.key_down("left")

    assert ("key", 0x25, True, True) in api.calls
    assert control.release_all_held_keys() == ()


def test_wheel_moves_to_reviewed_client_point_and_preserves_direction() -> None:
    api = FakeWindowsCameraApi()
    control = WindowsCameraControl(123, api)

    receipt = control.scroll_camera(400, 50, -12)

    assert receipt.operation is CameraInputOperation.CAMERA_WHEEL
    assert receipt.requested_events == 12
    assert receipt.completed_events == 12
    assert ("cursor", 420, 80) in api.calls
    assert api.calls[-1] == ("wheel", -12)


def test_partial_mouse_click_retries_button_up_until_released() -> None:
    api = FakeWindowsCameraApi()
    api.mouse_up_results = [0, 1]
    control = WindowsCameraControl(123, api)

    receipt = control.click_compass(608, 49)

    assert receipt.complete
    assert api.calls[-3:] == [
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
    assert api.calls[-3:] == [
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

    assert api.calls == ["dpi"]


def test_cursor_move_failure_aborts_before_click() -> None:
    api = FakeWindowsCameraApi()
    api.cursor_result = False

    with pytest.raises(WindowsCameraError, match="refused to move"):
        WindowsCameraControl(123, api).click_compass(608, 49)

    assert not any(
        isinstance(item, tuple) and item[0] == "mouse" for item in api.calls
    )


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


@pytest.mark.parametrize("hwnd", [0, -1, True, 1.5])
def test_control_requires_positive_integer_hwnd(hwnd: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        WindowsCameraControl(hwnd, FakeWindowsCameraApi())  # type: ignore[arg-type]
