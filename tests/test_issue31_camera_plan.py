from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from mining_automation.validation import (
    EXPECTED_CLIENT_HEIGHT,
    EXPECTED_CLIENT_WIDTH,
    MAX_CAMERA_ACTIONS,
    MAX_CAMERA_DRAG_PIXELS,
    MAX_CAMERA_DRAG_STEP_PIXELS,
    MAX_CAMERA_PAUSE_SECONDS,
    MAX_CAMERA_WHEEL_DETENTS,
    MAX_KEY_HOLD_SECONDS,
    MAX_RESET_ZOOM_DWELL_SECONDS,
    MAX_TOTAL_CAMERA_PAUSE_SECONDS,
    MAX_TOTAL_CAMERA_WHEEL_DETENTS,
    MAX_TOTAL_KEY_HOLD_SECONDS,
    REVIEWED_CAMERA_DRAG_OPEN_VIEWPORT,
    CameraActionReceipt,
    CameraDragAxis,
    CameraHoldKey,
    CameraInputOperation,
    CameraInputReceipt,
    CameraKeyHold,
    CameraMiddleDrag,
    CameraPause,
    CameraPlan,
    CameraPlanReceipt,
    CameraPlanRunner,
    CameraPreflightError,
    CameraPreflightReceipt,
    CameraReceiptError,
    CameraWheel,
    CompassClick,
    ResetZoomKey,
    camera_drag_path,
)


def _complete(operation: CameraInputOperation, count: int = 1) -> CameraInputReceipt:
    return CameraInputReceipt(operation, count, count)


class RecordingControl:
    def __init__(
        self,
        *,
        preflight: CameraPreflightReceipt | None = None,
        overrides: dict[CameraInputOperation, CameraInputReceipt | BaseException] | None = None,
    ) -> None:
        self.preflight_receipt = preflight or CameraPreflightReceipt(
            focused=True,
            client_width=EXPECTED_CLIENT_WIDTH,
            client_height=EXPECTED_CLIENT_HEIGHT,
        )
        self.overrides = overrides or {}
        self.calls: list[object] = []

    def _result(
        self,
        operation: CameraInputOperation,
        count: int = 1,
    ) -> CameraInputReceipt:
        result = self.overrides.get(operation, _complete(operation, count))
        if isinstance(result, BaseException):
            raise result
        return result

    def preflight(self) -> CameraPreflightReceipt:
        self.calls.append("preflight")
        return self.preflight_receipt

    def click_compass(self, x: int, y: int) -> CameraInputReceipt:
        self.calls.append(("compass", x, y))
        return self._result(CameraInputOperation.COMPASS_CLICK)

    def key_down(self, key: str) -> CameraInputReceipt:
        self.calls.append(("down", key))
        return self._result(CameraInputOperation.KEY_DOWN)

    def key_up(self, key: str) -> CameraInputReceipt:
        self.calls.append(("up", key))
        return self._result(CameraInputOperation.KEY_UP)

    def scroll_camera(self, x: int, y: int, detents: int) -> CameraInputReceipt:
        self.calls.append(("wheel", x, y, detents))
        return self._result(CameraInputOperation.CAMERA_WHEEL)

    def drag_camera(
        self,
        x: int,
        y: int,
        delta_x: int,
        delta_y: int,
    ) -> tuple[CameraInputReceipt, CameraInputReceipt, CameraInputReceipt]:
        self.calls.append(("drag", x, y, delta_x, delta_y))
        move_count = (
            abs(delta_x or delta_y) + MAX_CAMERA_DRAG_STEP_PIXELS - 1
        ) // MAX_CAMERA_DRAG_STEP_PIXELS
        return (
            self._result(CameraInputOperation.MIDDLE_DOWN),
            self._result(CameraInputOperation.CAMERA_DRAG_MOVE, move_count),
            self._result(CameraInputOperation.MIDDLE_UP),
        )


def test_plan_executes_actions_in_declared_order_and_returns_immutable_receipts() -> None:
    plan = CameraPlan(
        name="north-pitch-zoom",
        actions=(
            CompassClick(608, 49),
            CameraPause(0.15),
            CameraKeyHold(CameraHoldKey.UP, 0.25),
            CameraWheel(400, 50, -7),
            ResetZoomKey("home"),
        ),
    )
    control = RecordingControl()
    sleeps: list[float] = []

    receipt = CameraPlanRunner(control, sleeps.append).run(plan)

    assert control.calls == [
        "preflight",
        ("compass", 608, 49),
        ("down", "up"),
        ("up", "up"),
        ("wheel", 400, 50, -7),
        ("down", "home"),
        ("up", "home"),
    ]
    assert sleeps == [0.15, 0.25, 0.1]
    assert receipt.plan is plan
    assert receipt.preflight.supported is True
    assert [item.action_index for item in receipt.action_receipts] == [0, 1, 2, 3, 4]
    assert receipt.action_receipts[1].action == CameraPause(0.15)
    assert receipt.action_receipts[1].input_receipts == ()
    with pytest.raises(FrozenInstanceError):
        receipt.preflight.focused = False  # type: ignore[misc]


@pytest.mark.parametrize(
    "preflight",
    [
        CameraPreflightReceipt(False, EXPECTED_CLIENT_WIDTH, EXPECTED_CLIENT_HEIGHT),
        CameraPreflightReceipt(True, EXPECTED_CLIENT_WIDTH - 1, EXPECTED_CLIENT_HEIGHT),
        CameraPreflightReceipt(True, EXPECTED_CLIENT_WIDTH, EXPECTED_CLIENT_HEIGHT + 1),
    ],
)
def test_preflight_fails_closed_before_any_input(preflight: CameraPreflightReceipt) -> None:
    control = RecordingControl(preflight=preflight)
    plan = CameraPlan("safe", (CompassClick(608, 49),))

    with pytest.raises(CameraPreflightError):
        CameraPlanRunner(control, lambda _seconds: None).run(plan)

    assert control.calls == ["preflight"]


def test_key_is_released_when_sleep_raises() -> None:
    control = RecordingControl()
    plan = CameraPlan("raise-during-hold", (CameraKeyHold(CameraHoldKey.DOWN, 0.5),))

    def fail_sleep(_duration_s: float) -> None:
        raise OSError("sleep failed")

    with pytest.raises(OSError, match="sleep failed"):
        CameraPlanRunner(control, fail_sleep).run(plan)

    assert control.calls == [
        "preflight",
        ("down", "down"),
        ("up", "down"),
    ]


def test_pause_sleep_failure_stops_before_later_input() -> None:
    control = RecordingControl()
    plan = CameraPlan(
        "pause-failure",
        (
            CompassClick(608, 49),
            CameraPause(0.5),
            CameraKeyHold(CameraHoldKey.DOWN, 0.25),
        ),
    )

    def fail_sleep(_duration_s: float) -> None:
        raise OSError("pause failed")

    with pytest.raises(OSError, match="pause failed"):
        CameraPlanRunner(control, fail_sleep).run(plan)

    assert control.calls == ["preflight", ("compass", 608, 49)]


def test_key_down_exception_does_not_release_a_key_the_runner_never_acquired() -> None:
    control = RecordingControl(
        overrides={CameraInputOperation.KEY_DOWN: OSError("key down failed")}
    )
    plan = CameraPlan("raise-on-down", (CameraKeyHold(CameraHoldKey.LEFT, 0.5),))

    with pytest.raises(OSError, match="key down failed"):
        CameraPlanRunner(control, lambda _seconds: None).run(plan)

    assert control.calls == [
        "preflight",
        ("down", "left"),
    ]


def test_key_is_released_after_partial_key_down_receipt() -> None:
    control = RecordingControl(
        overrides={
            CameraInputOperation.KEY_DOWN: CameraInputReceipt(
                CameraInputOperation.KEY_DOWN, 2, 1
            )
        }
    )
    plan = CameraPlan("partial-down", (CameraKeyHold(CameraHoldKey.RIGHT, 0.5),))

    with pytest.raises(CameraReceiptError, match="partial or short"):
        CameraPlanRunner(control, lambda _seconds: None).run(plan)

    assert control.calls == [
        "preflight",
        ("down", "right"),
        ("up", "right"),
    ]


def test_runner_rejects_partial_key_release_receipt() -> None:
    control = RecordingControl(
        overrides={
            CameraInputOperation.KEY_UP: CameraInputReceipt(CameraInputOperation.KEY_UP, 2, 1)
        }
    )
    plan = CameraPlan("partial-up", (CameraKeyHold(CameraHoldKey.RIGHT, 0.5),))

    with pytest.raises(CameraReceiptError, match="partial or short"):
        CameraPlanRunner(control, lambda _seconds: None).run(plan)

    assert control.calls[-1] == ("up", "right")


@pytest.mark.parametrize(
    "receipt",
    [
        CameraInputReceipt(CameraInputOperation.COMPASS_CLICK, 2, 1),
        CameraInputReceipt(CameraInputOperation.COMPASS_CLICK, 0, 0),
    ],
)
def test_runner_rejects_partial_and_short_input_receipts(
    receipt: CameraInputReceipt,
) -> None:
    control = RecordingControl(overrides={CameraInputOperation.COMPASS_CLICK: receipt})

    with pytest.raises(CameraReceiptError, match="partial or short"):
        CameraPlanRunner(control, lambda _seconds: None).run(
            CameraPlan("short-click", (CompassClick(608, 49),))
        )


def test_runner_rejects_receipt_for_wrong_operation() -> None:
    control = RecordingControl(
        overrides={
            CameraInputOperation.COMPASS_CLICK: _complete(CameraInputOperation.KEY_UP)
        }
    )

    with pytest.raises(CameraReceiptError, match="expected compass_click"):
        CameraPlanRunner(control, lambda _seconds: None).run(
            CameraPlan("wrong-operation", (CompassClick(608, 49),))
        )


def test_action_receipt_rejects_missing_key_release_acknowledgement() -> None:
    action = CameraKeyHold(CameraHoldKey.UP, 0.25)

    with pytest.raises(ValueError, match="missing required input acknowledgements"):
        CameraActionReceipt(
            action_index=0,
            action=action,
            input_receipts=(_complete(CameraInputOperation.KEY_DOWN),),
        )


def test_pause_receipt_requires_exactly_zero_input_acknowledgements() -> None:
    action = CameraPause(0.5)

    assert CameraActionReceipt(0, action, ()).input_receipts == ()
    with pytest.raises(ValueError, match="missing required input acknowledgements"):
        CameraActionReceipt(
            0,
            action,
            (_complete(CameraInputOperation.KEY_DOWN),),
        )


def test_plan_receipt_rejects_partial_action_coverage() -> None:
    plan = CameraPlan("two-actions", (CompassClick(608, 49), ResetZoomKey("home")))
    preflight = CameraPreflightReceipt(True, EXPECTED_CLIENT_WIDTH, EXPECTED_CLIENT_HEIGHT)
    first_receipt = CameraActionReceipt(
        0,
        plan.actions[0],
        (_complete(CameraInputOperation.COMPASS_CLICK),),
    )

    with pytest.raises(ValueError, match="does not cover every plan action"):
        CameraPlanReceipt(plan, preflight, (first_receipt,))


@pytest.mark.parametrize(
    ("x", "y"),
    [
        (-1, 0),
        (EXPECTED_CLIENT_WIDTH, 0),
        (0, -1),
        (0, EXPECTED_CLIENT_HEIGHT),
        (607, 49),
        (608, 50),
    ],
)
def test_compass_click_is_restricted_to_exact_reviewed_point(x: int, y: int) -> None:
    with pytest.raises(ValueError, match="exact reviewed client point"):
        CompassClick(x, y)


@pytest.mark.parametrize(
    "duration_s",
    [0.0, -1.0, float("nan"), float("inf"), MAX_KEY_HOLD_SECONDS + 0.001],
)
def test_camera_key_hold_is_strictly_bounded(duration_s: float) -> None:
    with pytest.raises(ValueError, match="camera hold duration"):
        CameraKeyHold(CameraHoldKey.UP, duration_s)


@pytest.mark.parametrize(
    "duration_s",
    [
        0.0,
        -1.0,
        True,
        float("nan"),
        float("inf"),
        MAX_CAMERA_PAUSE_SECONDS + 0.001,
    ],
)
def test_camera_pause_is_strictly_bounded(duration_s: float) -> None:
    with pytest.raises(ValueError, match="camera pause duration"):
        CameraPause(duration_s)


@pytest.mark.parametrize(
    "detents",
    [0, -MAX_CAMERA_WHEEL_DETENTS - 1, MAX_CAMERA_WHEEL_DETENTS + 1],
)
def test_camera_wheel_is_strictly_bounded(detents: int) -> None:
    with pytest.raises(ValueError, match="camera wheel detents"):
        CameraWheel(400, 50, detents)


@pytest.mark.parametrize(
    ("x", "y"),
    [
        (-1, 0),
        (EXPECTED_CLIENT_WIDTH, 0),
        (0, -1),
        (0, EXPECTED_CLIENT_HEIGHT),
        (399, 50),
        (400, 51),
    ],
)
def test_camera_wheel_is_restricted_to_exact_reviewed_point(x: int, y: int) -> None:
    with pytest.raises(ValueError, match="exact reviewed client point"):
        CameraWheel(x, y, 1)


def test_camera_drag_axis_values_are_stable_for_reports() -> None:
    assert CameraDragAxis.HORIZONTAL.value == "horizontal"
    assert CameraDragAxis.VERTICAL.value == "vertical"


@pytest.mark.parametrize(
    ("axis", "pixels", "expected"),
    [
        (CameraDragAxis.HORIZONTAL, 9, ((204, 600), (208, 600), (209, 600))),
        (CameraDragAxis.HORIZONTAL, -5, ((196, 600), (195, 600))),
        (CameraDragAxis.VERTICAL, 9, ((200, 604), (200, 608), (200, 609))),
        (CameraDragAxis.VERTICAL, -5, ((200, 596), (200, 595))),
    ],
)
def test_camera_drag_path_excludes_start_and_includes_exact_endpoint(
    axis: CameraDragAxis,
    pixels: int,
    expected: tuple[tuple[int, int], ...],
) -> None:
    action = CameraMiddleDrag(axis, pixels)

    assert camera_drag_path(action) == expected
    assert action.step_count == len(expected)


@pytest.mark.parametrize(
    ("axis", "pixels"),
    [
        (CameraDragAxis.HORIZONTAL, MAX_CAMERA_DRAG_PIXELS),
        (CameraDragAxis.HORIZONTAL, -200),
        (CameraDragAxis.VERTICAL, 249),
        (CameraDragAxis.VERTICAL, -MAX_CAMERA_DRAG_PIXELS),
    ],
)
def test_camera_drag_path_is_monotonic_axis_aligned_and_step_bounded(
    axis: CameraDragAxis,
    pixels: int,
) -> None:
    action = CameraMiddleDrag(axis, pixels)
    points = ((action.start_x, action.start_y), *camera_drag_path(action))

    assert len(points) == action.step_count + 1
    for before, after in zip(points, points[1:], strict=False):
        delta_x = after[0] - before[0]
        delta_y = after[1] - before[1]
        assert (delta_x == 0) != (delta_y == 0)
        assert max(abs(delta_x), abs(delta_y)) <= MAX_CAMERA_DRAG_STEP_PIXELS
    assert points[-1] == (
        action.start_x + action.delta_x,
        action.start_y + action.delta_y,
    )


@pytest.mark.parametrize(
    "pixels",
    [0, True, 1.0, MAX_CAMERA_DRAG_PIXELS + 1, -MAX_CAMERA_DRAG_PIXELS - 1],
)
def test_camera_drag_pixels_are_strictly_bounded(pixels: object) -> None:
    with pytest.raises(ValueError, match="camera drag pixels"):
        CameraMiddleDrag(CameraDragAxis.VERTICAL, pixels)  # type: ignore[arg-type]


def test_camera_drag_requires_enum_axis() -> None:
    with pytest.raises(ValueError, match="CameraDragAxis"):
        CameraMiddleDrag("horizontal", 4)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("start_x", "start_y"),
    [(199, 600), (200, 599), (True, 600), (200, 600.0)],
)
def test_camera_drag_requires_exact_reviewed_open_viewport_start(
    start_x: object,
    start_y: object,
) -> None:
    with pytest.raises(ValueError, match="start coordinates|open-viewport"):
        CameraMiddleDrag(  # type: ignore[arg-type]
            CameraDragAxis.HORIZONTAL,
            4,
            start_x=start_x,
            start_y=start_y,
        )


def test_camera_drag_rejects_endpoint_outside_reviewed_open_viewport() -> None:
    with pytest.raises(ValueError, match="reviewed open viewport"):
        CameraMiddleDrag(CameraDragAxis.HORIZONTAL, -MAX_CAMERA_DRAG_PIXELS)


def test_camera_drag_rejects_bottom_fixed_ui_boundary() -> None:
    safe = CameraMiddleDrag(CameraDragAxis.VERTICAL, 249)
    left, top, right, bottom = REVIEWED_CAMERA_DRAG_OPEN_VIEWPORT

    assert camera_drag_path(safe)[-1] == (200, bottom - 1)
    assert all(
        left <= x < right and top <= y < bottom
        for x, y in ((safe.start_x, safe.start_y), *camera_drag_path(safe))
    )
    with pytest.raises(ValueError, match="reviewed open viewport"):
        CameraMiddleDrag(CameraDragAxis.VERTICAL, 250)


def test_camera_plan_allows_at_most_one_drag_per_axis() -> None:
    with pytest.raises(ValueError, match="at most one drag per axis"):
        CameraPlan(
            "duplicate-horizontal",
            (
                CameraMiddleDrag(CameraDragAxis.HORIZONTAL, 4),
                CameraMiddleDrag(CameraDragAxis.HORIZONTAL, -4),
            ),
        )

    CameraPlan(
        "one-per-axis",
        (
            CameraMiddleDrag(CameraDragAxis.HORIZONTAL, 4),
            CameraMiddleDrag(CameraDragAxis.VERTICAL, -4),
        ),
    )


def test_camera_drag_runner_calls_atomic_control_and_records_exact_receipts() -> None:
    action = CameraMiddleDrag(CameraDragAxis.HORIZONTAL, 9)
    control = RecordingControl()

    receipt = CameraPlanRunner(control, lambda _seconds: None).run(
        CameraPlan("drag", (action,))
    )

    assert control.calls == ["preflight", ("drag", 200, 600, 9, 0)]
    assert tuple(
        item.operation for item in receipt.action_receipts[0].input_receipts
    ) == (
        CameraInputOperation.MIDDLE_DOWN,
        CameraInputOperation.CAMERA_DRAG_MOVE,
        CameraInputOperation.MIDDLE_UP,
    )
    assert tuple(
        item.requested_events for item in receipt.action_receipts[0].input_receipts
    ) == (1, 3, 1)


@pytest.mark.parametrize(
    ("operation", "receipt"),
    [
        (
            CameraInputOperation.MIDDLE_DOWN,
            CameraInputReceipt(CameraInputOperation.MIDDLE_DOWN, 1, 0),
        ),
        (
            CameraInputOperation.CAMERA_DRAG_MOVE,
            CameraInputReceipt(CameraInputOperation.CAMERA_DRAG_MOVE, 2, 1),
        ),
        (
            CameraInputOperation.MIDDLE_UP,
            CameraInputReceipt(CameraInputOperation.MIDDLE_UP, 1, 0),
        ),
    ],
)
def test_camera_drag_runner_rejects_any_short_atomic_receipt(
    operation: CameraInputOperation,
    receipt: CameraInputReceipt,
) -> None:
    control = RecordingControl(overrides={operation: receipt})

    with pytest.raises(CameraReceiptError, match="partial or short"):
        CameraPlanRunner(control, lambda _seconds: None).run(
            CameraPlan(
                "short-drag",
                (CameraMiddleDrag(CameraDragAxis.VERTICAL, 8),),
            )
        )


def test_camera_drag_action_receipt_rejects_wrong_move_count() -> None:
    action = CameraMiddleDrag(CameraDragAxis.VERTICAL, 8)

    with pytest.raises(ValueError, match="unexpected event count"):
        CameraActionReceipt(
            0,
            action,
            (
                _complete(CameraInputOperation.MIDDLE_DOWN),
                _complete(CameraInputOperation.CAMERA_DRAG_MOVE),
                _complete(CameraInputOperation.MIDDLE_UP),
            ),
        )


def test_reset_zoom_key_is_released_when_sleep_raises() -> None:
    control = RecordingControl()
    plan = CameraPlan("reset-release", (ResetZoomKey("home", dwell_s=0.2),))

    def fail_sleep(_duration_s: float) -> None:
        raise OSError("reset dwell failed")

    with pytest.raises(OSError, match="reset dwell failed"):
        CameraPlanRunner(control, fail_sleep).run(plan)

    assert control.calls == ["preflight", ("down", "home"), ("up", "home")]


@pytest.mark.parametrize(
    "dwell_s",
    [0.0, -1.0, float("nan"), float("inf"), MAX_RESET_ZOOM_DWELL_SECONDS + 0.001],
)
def test_reset_zoom_key_dwell_is_strictly_bounded(dwell_s: float) -> None:
    with pytest.raises(ValueError, match="reset-zoom key dwell"):
        ResetZoomKey("home", dwell_s=dwell_s)


def test_camera_plan_limits_action_count_and_total_hold_duration() -> None:
    with pytest.raises(ValueError, match=f"more than {MAX_CAMERA_ACTIONS}"):
        CameraPlan(
            "too-many",
            tuple(CameraKeyHold(CameraHoldKey.UP, 0.1) for _ in range(MAX_CAMERA_ACTIONS + 1)),
        )

    hold_count = int(MAX_TOTAL_KEY_HOLD_SECONDS / MAX_KEY_HOLD_SECONDS) + 1
    with pytest.raises(ValueError, match="total key-hold duration"):
        CameraPlan(
            "too-long",
            tuple(
                CameraKeyHold(CameraHoldKey.UP, MAX_KEY_HOLD_SECONDS)
                for _ in range(hold_count)
            ),
        )

    wheel_count = int(MAX_TOTAL_CAMERA_WHEEL_DETENTS / MAX_CAMERA_WHEEL_DETENTS) + 1
    with pytest.raises(ValueError, match="total wheel movement"):
        CameraPlan(
            "too-much-wheel",
            tuple(
                CameraWheel(400, 50, MAX_CAMERA_WHEEL_DETENTS)
                for _ in range(wheel_count)
            ),
        )

    pause_count = int(MAX_TOTAL_CAMERA_PAUSE_SECONDS / MAX_CAMERA_PAUSE_SECONDS) + 1
    with pytest.raises(ValueError, match="total pause duration"):
        CameraPlan(
            "too-much-pause",
            tuple(CameraPause(MAX_CAMERA_PAUSE_SECONDS) for _ in range(pause_count)),
        )


def test_camera_plan_rejects_repeated_one_shot_actions() -> None:
    with pytest.raises(ValueError, match="at most one compass"):
        CameraPlan("two-compass-clicks", (CompassClick(608, 49), CompassClick(608, 49)))

    with pytest.raises(ValueError, match="at most one reset-zoom"):
        CameraPlan("two-resets", (ResetZoomKey("home"), ResetZoomKey("home")))
