from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from mining_automation.validation import (
    EXPECTED_CLIENT_HEIGHT,
    EXPECTED_CLIENT_WIDTH,
    MAX_CAMERA_ACTIONS,
    MAX_CAMERA_WHEEL_DETENTS,
    MAX_KEY_HOLD_SECONDS,
    MAX_RESET_ZOOM_DWELL_SECONDS,
    MAX_TOTAL_CAMERA_WHEEL_DETENTS,
    MAX_TOTAL_KEY_HOLD_SECONDS,
    CameraActionReceipt,
    CameraHoldKey,
    CameraInputOperation,
    CameraInputReceipt,
    CameraKeyHold,
    CameraPlan,
    CameraPlanReceipt,
    CameraPlanRunner,
    CameraPreflightError,
    CameraPreflightReceipt,
    CameraReceiptError,
    CameraWheel,
    CompassClick,
    ResetZoomKey,
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

    def _result(self, operation: CameraInputOperation) -> CameraInputReceipt:
        result = self.overrides.get(operation, _complete(operation))
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


def test_plan_executes_actions_in_declared_order_and_returns_immutable_receipts() -> None:
    plan = CameraPlan(
        name="north-pitch-zoom",
        actions=(
            CompassClick(608, 49),
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
    assert sleeps == [0.25, 0.1]
    assert receipt.plan is plan
    assert receipt.preflight.supported is True
    assert [item.action_index for item in receipt.action_receipts] == [0, 1, 2, 3]
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


def test_camera_plan_rejects_repeated_one_shot_actions() -> None:
    with pytest.raises(ValueError, match="at most one compass"):
        CameraPlan("two-compass-clicks", (CompassClick(608, 49), CompassClick(608, 49)))

    with pytest.raises(ValueError, match="at most one reset-zoom"):
        CameraPlan("two-resets", (ResetZoomKey("home"), ResetZoomKey("home")))
