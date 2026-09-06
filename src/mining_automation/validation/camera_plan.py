"""Bounded camera-plan execution for real-client development validation.

The module deliberately knows nothing about Windows, RuneLite, capture, or
perception.  A development tool supplies a narrow :class:`CameraControl`
adapter and receives immutable receipts that can be attached to a validation
report.  Only the bounded camera operations needed by the Issue #31 experiment
are representable; this is not a general input-automation API.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

EXPECTED_CLIENT_WIDTH = 1005
EXPECTED_CLIENT_HEIGHT = 1078
REVIEWED_COMPASS_POINT = (608, 49)
REVIEWED_CAMERA_WHEEL_POINT = (400, 50)
REVIEWED_CAMERA_DRAG_POINT = (200, 600)
# Logical-client bounds reviewed as unobstructed world viewport for the drag
# primitive: left/top inclusive, right/bottom exclusive.  This is an input
# safety boundary, not a camera-normalization strategy parameter.
REVIEWED_CAMERA_DRAG_OPEN_VIEWPORT = (0, 34, 520, 850)

MAX_CAMERA_ACTIONS = 16
# RuneLite's reviewed -400..1400 zoom range spans 72 default 25-unit
# detents.  Ninety-six permits one endpoint-saturation action from any start;
# the total permits one saturation action plus one bounded offset action.
MAX_CAMERA_WHEEL_DETENTS = 96
MAX_TOTAL_CAMERA_WHEEL_DETENTS = 192
MAX_CAMERA_DRAG_PIXELS = 256
MAX_CAMERA_DRAG_STEP_PIXELS = 4
MAX_TOTAL_CAMERA_DRAG_PIXELS = 512
MAX_KEY_HOLD_SECONDS = 5.0
MAX_TOTAL_KEY_HOLD_SECONDS = 15.0
MAX_CAMERA_PAUSE_SECONDS = 2.0
MAX_TOTAL_CAMERA_PAUSE_SECONDS = 4.0
MAX_RESET_ZOOM_DWELL_SECONDS = 1.0
MAX_RESET_ZOOM_KEY_LENGTH = 32


class CameraPlanError(RuntimeError):
    """Base error raised while validating or executing a camera plan."""


class CameraInputNotAttemptedError(CameraPlanError):
    """A platform veto proved that no physical input call was attempted.

    Adapters may raise this marker only before crossing their operating-system
    input boundary.  Exceptions from an input API, including ambiguous calls
    that may have inserted an event before raising, must not use this type.
    """


class CameraPreflightError(CameraPlanError):
    """The target window is not safe to operate."""


class CameraReceiptError(CameraPlanError):
    """The platform adapter did not acknowledge a complete input operation."""


class CameraHoldKey(StrEnum):
    """The only keys that a camera plan may hold."""

    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"


class CameraDragAxis(StrEnum):
    """The only axes along which a validation camera drag may move."""

    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"


class CameraInputOperation(StrEnum):
    """Narrow logical operations acknowledged by a platform adapter."""

    COMPASS_CLICK = "compass_click"
    PLAY_NOW_CLICK = "play_now_click"
    WELCOME_PLAY_CLICK = "welcome_play_click"
    KEY_DOWN = "key_down"
    KEY_UP = "key_up"
    CAMERA_WHEEL = "camera_wheel"
    MIDDLE_DOWN = "middle_down"
    CAMERA_DRAG_MOVE = "camera_drag_move"
    MIDDLE_UP = "middle_up"


@dataclass(frozen=True, slots=True)
class CameraPreflightReceipt:
    """Observed target-window state before a plan starts."""

    focused: bool
    client_width: int
    client_height: int

    def __post_init__(self) -> None:
        if not isinstance(self.focused, bool):
            raise ValueError("focused must be a boolean")
        if (
            isinstance(self.client_width, bool)
            or not isinstance(self.client_width, int)
            or self.client_width <= 0
        ):
            raise ValueError("client_width must be a positive integer")
        if (
            isinstance(self.client_height, bool)
            or not isinstance(self.client_height, int)
            or self.client_height <= 0
        ):
            raise ValueError("client_height must be a positive integer")

    @property
    def supported(self) -> bool:
        return (
            self.focused
            and self.client_width == EXPECTED_CLIENT_WIDTH
            and self.client_height == EXPECTED_CLIENT_HEIGHT
        )


@dataclass(frozen=True, slots=True)
class CameraInputReceipt:
    """Counts reported by one narrow platform input operation.

    ``requested_events`` and ``completed_events`` intentionally remain
    separate.  Platform APIs commonly report how many low-level events they
    accepted, and treating a short count as success would make a deterministic
    camera sequence non-deterministic in practice.
    """

    operation: CameraInputOperation
    requested_events: int
    completed_events: int

    def __post_init__(self) -> None:
        if not isinstance(self.operation, CameraInputOperation):
            raise ValueError("operation must be a CameraInputOperation")
        for name, value in (
            ("requested_events", self.requested_events),
            ("completed_events", self.completed_events),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.completed_events > self.requested_events:
            raise ValueError("completed_events cannot exceed requested_events")

    @property
    def complete(self) -> bool:
        return self.requested_events > 0 and self.completed_events == self.requested_events


@dataclass(frozen=True, slots=True)
class CompassClick:
    """One click at a reviewed compass coordinate in the expected client."""

    x: int
    y: int

    def __post_init__(self) -> None:
        if isinstance(self.x, bool) or not isinstance(self.x, int):
            raise ValueError("compass x must be an integer")
        if isinstance(self.y, bool) or not isinstance(self.y, int):
            raise ValueError("compass y must be an integer")
        if (self.x, self.y) != REVIEWED_COMPASS_POINT:
            raise ValueError(
                "compass click must use the exact reviewed client point "
                f"{REVIEWED_COMPASS_POINT}"
            )


@dataclass(frozen=True, slots=True)
class CameraKeyHold:
    """A bounded hold of one camera arrow key."""

    key: CameraHoldKey
    duration_s: float

    def __post_init__(self) -> None:
        if not isinstance(self.key, CameraHoldKey):
            raise ValueError("camera hold key must be a CameraHoldKey")
        if (
            isinstance(self.duration_s, bool)
            or not isinstance(self.duration_s, (int, float))
            or not math.isfinite(self.duration_s)
            or self.duration_s <= 0.0
            or self.duration_s > MAX_KEY_HOLD_SECONDS
        ):
            raise ValueError(
                f"camera hold duration must be finite and in (0, {MAX_KEY_HOLD_SECONDS}]"
            )


@dataclass(frozen=True, slots=True)
class CameraPause:
    """A bounded, explicit no-input settle inside a camera plan."""

    duration_s: float

    def __post_init__(self) -> None:
        if (
            isinstance(self.duration_s, bool)
            or not isinstance(self.duration_s, (int, float))
            or not math.isfinite(self.duration_s)
            or self.duration_s <= 0.0
            or self.duration_s > MAX_CAMERA_PAUSE_SECONDS
        ):
            raise ValueError(
                "camera pause duration must be finite and in "
                f"(0, {MAX_CAMERA_PAUSE_SECONDS}]"
            )


@dataclass(frozen=True, slots=True)
class CameraMiddleDrag:
    """One bounded, axis-aligned middle-button camera drag.

    The exact reviewed open-viewport start and generated logical path keep
    this a validation-only camera primitive rather than a generic pointer API.
    """

    axis: CameraDragAxis
    pixels: int
    start_x: int = REVIEWED_CAMERA_DRAG_POINT[0]
    start_y: int = REVIEWED_CAMERA_DRAG_POINT[1]

    def __post_init__(self) -> None:
        if not isinstance(self.axis, CameraDragAxis):
            raise ValueError("camera drag axis must be a CameraDragAxis")
        if (
            isinstance(self.start_x, bool)
            or not isinstance(self.start_x, int)
            or isinstance(self.start_y, bool)
            or not isinstance(self.start_y, int)
        ):
            raise ValueError("camera drag start coordinates must be integers")
        if (self.start_x, self.start_y) != REVIEWED_CAMERA_DRAG_POINT:
            raise ValueError(
                "camera drag must use the exact reviewed open-viewport client "
                f"point {REVIEWED_CAMERA_DRAG_POINT}"
            )
        if (
            isinstance(self.pixels, bool)
            or not isinstance(self.pixels, int)
            or self.pixels == 0
            or abs(self.pixels) > MAX_CAMERA_DRAG_PIXELS
        ):
            raise ValueError(
                "camera drag pixels must be a nonzero integer with absolute value "
                f"at most {MAX_CAMERA_DRAG_PIXELS}"
            )
        end_x = self.start_x + self.delta_x
        end_y = self.start_y + self.delta_y
        viewport_left, viewport_top, viewport_right, viewport_bottom = (
            REVIEWED_CAMERA_DRAG_OPEN_VIEWPORT
        )
        if not (
            viewport_left <= end_x < viewport_right
            and viewport_top <= end_y < viewport_bottom
        ):
            raise ValueError(
                "camera drag endpoint must remain inside the reviewed open viewport"
            )

    @property
    def delta_x(self) -> int:
        """Signed horizontal logical-client delta."""

        return self.pixels if self.axis is CameraDragAxis.HORIZONTAL else 0

    @property
    def delta_y(self) -> int:
        """Signed vertical logical-client delta."""

        return self.pixels if self.axis is CameraDragAxis.VERTICAL else 0

    @property
    def step_count(self) -> int:
        """Number of deterministic logical move points after the start."""

        return (abs(self.pixels) + MAX_CAMERA_DRAG_STEP_PIXELS - 1) // (
            MAX_CAMERA_DRAG_STEP_PIXELS
        )


def camera_drag_path(action: CameraMiddleDrag) -> tuple[tuple[int, int], ...]:
    """Return logical move points after the start, including the endpoint."""

    direction = 1 if action.pixels > 0 else -1
    distance = abs(action.pixels)
    return tuple(
        (
            action.start_x
            + (
                direction * min(step * MAX_CAMERA_DRAG_STEP_PIXELS, distance)
                if action.axis is CameraDragAxis.HORIZONTAL
                else 0
            ),
            action.start_y
            + (
                direction * min(step * MAX_CAMERA_DRAG_STEP_PIXELS, distance)
                if action.axis is CameraDragAxis.VERTICAL
                else 0
            ),
        )
        for step in range(1, action.step_count + 1)
    )


@dataclass(frozen=True, slots=True)
class ResetZoomKey:
    """One configured RuneLite reset-zoom key press with release dwell."""

    key: str
    dwell_s: float = 0.1

    def __post_init__(self) -> None:
        if (
            not isinstance(self.key, str)
            or not self.key.strip()
            or self.key != self.key.strip()
            or len(self.key) > MAX_RESET_ZOOM_KEY_LENGTH
        ):
            raise ValueError(
                "reset-zoom key must be a non-empty trimmed string of at most "
                f"{MAX_RESET_ZOOM_KEY_LENGTH} characters"
            )
        if (
            isinstance(self.dwell_s, bool)
            or not isinstance(self.dwell_s, (int, float))
            or not math.isfinite(self.dwell_s)
            or self.dwell_s <= 0.0
            or self.dwell_s > MAX_RESET_ZOOM_DWELL_SECONDS
        ):
            raise ValueError(
                "reset-zoom key dwell must be finite and in "
                f"(0, {MAX_RESET_ZOOM_DWELL_SECONDS}]"
            )


@dataclass(frozen=True, slots=True)
class CameraWheel:
    """A bounded wheel action at a reviewed world-viewport coordinate."""

    x: int
    y: int
    detents: int

    def __post_init__(self) -> None:
        if isinstance(self.x, bool) or not isinstance(self.x, int):
            raise ValueError("camera wheel x must be an integer")
        if isinstance(self.y, bool) or not isinstance(self.y, int):
            raise ValueError("camera wheel y must be an integer")
        if (self.x, self.y) != REVIEWED_CAMERA_WHEEL_POINT:
            raise ValueError(
                "camera wheel must use the exact reviewed client point "
                f"{REVIEWED_CAMERA_WHEEL_POINT}"
            )
        if (
            isinstance(self.detents, bool)
            or not isinstance(self.detents, int)
            or self.detents == 0
            or abs(self.detents) > MAX_CAMERA_WHEEL_DETENTS
        ):
            raise ValueError(
                "camera wheel detents must be a nonzero integer with absolute value at most "
                f"{MAX_CAMERA_WHEEL_DETENTS}"
            )


type CameraAction = (
    CompassClick
    | CameraKeyHold
    | CameraMiddleDrag
    | CameraPause
    | ResetZoomKey
    | CameraWheel
)


@dataclass(frozen=True, slots=True)
class CameraPlan:
    """An immutable, bounded, deterministically ordered camera sequence."""

    name: str
    actions: tuple[CameraAction, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip() or self.name != self.name.strip():
            raise ValueError("camera plan name must be a non-empty trimmed string")
        if not isinstance(self.actions, tuple):
            raise ValueError("camera plan actions must be a tuple")
        if not self.actions:
            raise ValueError("camera plan must contain at least one action")
        if len(self.actions) > MAX_CAMERA_ACTIONS:
            raise ValueError(f"camera plan cannot contain more than {MAX_CAMERA_ACTIONS} actions")

        total_hold_s = 0.0
        total_pause_s = 0.0
        total_wheel_detents = 0
        total_drag_pixels = 0
        compass_clicks = 0
        reset_zoom_keys = 0
        drag_axes: set[CameraDragAxis] = set()
        for action in self.actions:
            if isinstance(action, CompassClick):
                compass_clicks += 1
            elif isinstance(action, CameraKeyHold):
                total_hold_s += action.duration_s
            elif isinstance(action, CameraPause):
                total_pause_s += action.duration_s
            elif isinstance(action, CameraMiddleDrag):
                if action.axis in drag_axes:
                    raise ValueError("camera plan may contain at most one drag per axis")
                drag_axes.add(action.axis)
                total_drag_pixels += abs(action.pixels)
            elif isinstance(action, ResetZoomKey):
                reset_zoom_keys += 1
                total_hold_s += action.dwell_s
            elif isinstance(action, CameraWheel):
                total_wheel_detents += abs(action.detents)
            else:
                raise ValueError("camera plan contains an unsupported action")

        if compass_clicks > 1:
            raise ValueError("camera plan may contain at most one compass click")
        if reset_zoom_keys > 1:
            raise ValueError("camera plan may contain at most one reset-zoom key action")
        if total_hold_s > MAX_TOTAL_KEY_HOLD_SECONDS:
            raise ValueError(
                "camera plan total key-hold duration cannot exceed "
                f"{MAX_TOTAL_KEY_HOLD_SECONDS} seconds"
            )
        if total_pause_s > MAX_TOTAL_CAMERA_PAUSE_SECONDS:
            raise ValueError(
                "camera plan total pause duration cannot exceed "
                f"{MAX_TOTAL_CAMERA_PAUSE_SECONDS} seconds"
            )
        if total_wheel_detents > MAX_TOTAL_CAMERA_WHEEL_DETENTS:
            raise ValueError(
                "camera plan total wheel movement cannot exceed "
                f"{MAX_TOTAL_CAMERA_WHEEL_DETENTS} detents"
            )
        if total_drag_pixels > MAX_TOTAL_CAMERA_DRAG_PIXELS:
            raise ValueError(
                "camera plan total drag movement cannot exceed "
                f"{MAX_TOTAL_CAMERA_DRAG_PIXELS} logical pixels"
            )


def _expected_operations(action: CameraAction) -> tuple[CameraInputOperation, ...]:
    if isinstance(action, CameraPause):
        return ()
    if isinstance(action, CompassClick):
        return (CameraInputOperation.COMPASS_CLICK,)
    if isinstance(action, CameraKeyHold):
        return (CameraInputOperation.KEY_DOWN, CameraInputOperation.KEY_UP)
    if isinstance(action, CameraMiddleDrag):
        return (
            CameraInputOperation.MIDDLE_DOWN,
            CameraInputOperation.CAMERA_DRAG_MOVE,
            CameraInputOperation.MIDDLE_UP,
        )
    if isinstance(action, ResetZoomKey):
        return (CameraInputOperation.KEY_DOWN, CameraInputOperation.KEY_UP)
    return (CameraInputOperation.CAMERA_WHEEL,)


@dataclass(frozen=True, slots=True)
class CameraActionReceipt:
    """Complete input acknowledgements for one plan action."""

    action_index: int
    action: CameraAction
    input_receipts: tuple[CameraInputReceipt, ...]

    def __post_init__(self) -> None:
        if (
            isinstance(self.action_index, bool)
            or not isinstance(self.action_index, int)
            or self.action_index < 0
        ):
            raise ValueError("action_index must be a non-negative integer")
        if not isinstance(self.input_receipts, tuple):
            raise ValueError("input_receipts must be a tuple")

        expected_operations = _expected_operations(self.action)
        if len(self.input_receipts) != len(expected_operations):
            raise ValueError("action receipt is missing required input acknowledgements")
        for receipt, expected_operation in zip(
            self.input_receipts, expected_operations, strict=True
        ):
            if receipt.operation is not expected_operation:
                raise ValueError("action receipt contains an unexpected input operation")
            if not receipt.complete:
                raise ValueError("action receipt contains a partial or short acknowledgement")
        if isinstance(self.action, CameraMiddleDrag):
            expected_event_counts = (1, self.action.step_count, 1)
            if tuple(
                receipt.requested_events for receipt in self.input_receipts
            ) != expected_event_counts:
                raise ValueError("camera drag receipt contains an unexpected event count")


@dataclass(frozen=True, slots=True)
class CameraPlanReceipt:
    """A complete receipt tied to the exact plan and preflight state."""

    plan: CameraPlan
    preflight: CameraPreflightReceipt
    action_receipts: tuple[CameraActionReceipt, ...]

    def __post_init__(self) -> None:
        if not self.preflight.supported:
            raise ValueError("plan receipt requires a supported preflight")
        if not isinstance(self.action_receipts, tuple):
            raise ValueError("action_receipts must be a tuple")
        if len(self.action_receipts) != len(self.plan.actions):
            raise ValueError("plan receipt does not cover every plan action")
        for index, (action, receipt) in enumerate(
            zip(self.plan.actions, self.action_receipts, strict=True)
        ):
            if receipt.action_index != index or receipt.action != action:
                raise ValueError("plan receipt action order does not match the plan")


class CameraControl(Protocol):
    """Platform adapter injected by a development-only validation tool."""

    def preflight(self) -> CameraPreflightReceipt:
        """Inspect focus and client geometry without sending input."""

    def click_compass(self, x: int, y: int) -> CameraInputReceipt:
        """Click one reviewed compass coordinate."""

    def key_down(self, key: str) -> CameraInputReceipt:
        """Press one allowed camera key without releasing it.

        An implementation must either return a receipt that says whether an
        event was inserted or clean up any possibly inserted event before it
        raises.  The runner never releases a key that it cannot prove this
        plan acquired, because it may have been held by the user beforehand.
        """

    def key_up(self, key: str) -> CameraInputReceipt:
        """Release one allowed camera key."""

    def scroll_camera(self, x: int, y: int, detents: int) -> CameraInputReceipt:
        """Scroll bounded detents at a reviewed client-local coordinate."""

    def drag_camera(
        self,
        x: int,
        y: int,
        delta_x: int,
        delta_y: int,
    ) -> tuple[CameraInputReceipt, CameraInputReceipt, CameraInputReceipt]:
        """Execute one reviewed, bounded middle-button drag atomically."""


class Sleeper(Protocol):
    """Injected monotonic delay used by bounded key holds and pauses."""

    def __call__(self, duration_s: float, /) -> None: ...


def _require_complete_receipt(
    receipt: CameraInputReceipt,
    expected_operation: CameraInputOperation,
) -> CameraInputReceipt:
    if receipt.operation is not expected_operation:
        raise CameraReceiptError(
            f"expected {expected_operation.value} receipt, got {receipt.operation.value}"
        )
    if not receipt.complete:
        raise CameraReceiptError(
            f"{expected_operation.value} input was partial or short: "
            f"{receipt.completed_events}/{receipt.requested_events} events completed"
        )
    return receipt


class CameraPlanRunner:
    """Execute one bounded plan after an exact fail-closed preflight."""

    def __init__(self, control: CameraControl, sleeper: Sleeper) -> None:
        self._control = control
        self._sleeper = sleeper

    def run(self, plan: CameraPlan) -> CameraPlanReceipt:
        preflight = self._control.preflight()
        self._require_supported_preflight(preflight)

        action_receipts = tuple(
            self._run_action(index, action) for index, action in enumerate(plan.actions)
        )
        return CameraPlanReceipt(
            plan=plan,
            preflight=preflight,
            action_receipts=action_receipts,
        )

    @staticmethod
    def _require_supported_preflight(preflight: CameraPreflightReceipt) -> None:
        if not preflight.focused:
            raise CameraPreflightError("target window must be focused before camera input")
        if (
            preflight.client_width != EXPECTED_CLIENT_WIDTH
            or preflight.client_height != EXPECTED_CLIENT_HEIGHT
        ):
            raise CameraPreflightError(
                "target client geometry must be exactly "
                f"{EXPECTED_CLIENT_WIDTH}x{EXPECTED_CLIENT_HEIGHT}; got "
                f"{preflight.client_width}x{preflight.client_height}"
            )

    def _run_action(self, index: int, action: CameraAction) -> CameraActionReceipt:
        if isinstance(action, CameraPause):
            self._sleeper(action.duration_s)
            return CameraActionReceipt(index, action, ())

        if isinstance(action, CompassClick):
            click_receipt = _require_complete_receipt(
                self._control.click_compass(action.x, action.y),
                CameraInputOperation.COMPASS_CLICK,
            )
            return CameraActionReceipt(index, action, (click_receipt,))

        if isinstance(action, CameraWheel):
            wheel_receipt = _require_complete_receipt(
                self._control.scroll_camera(action.x, action.y, action.detents),
                CameraInputOperation.CAMERA_WHEEL,
            )
            return CameraActionReceipt(index, action, (wheel_receipt,))

        if isinstance(action, CameraMiddleDrag):
            drag_receipts = self._control.drag_camera(
                action.start_x,
                action.start_y,
                action.delta_x,
                action.delta_y,
            )
            expected_operations = (
                CameraInputOperation.MIDDLE_DOWN,
                CameraInputOperation.CAMERA_DRAG_MOVE,
                CameraInputOperation.MIDDLE_UP,
            )
            complete_receipts = tuple(
                _require_complete_receipt(receipt, expected)
                for receipt, expected in zip(
                    drag_receipts,
                    expected_operations,
                    strict=True,
                )
            )
            return CameraActionReceipt(index, action, complete_receipts)

        if isinstance(action, ResetZoomKey):
            return self._run_key_action(index, action, action.key, action.dwell_s)

        return self._run_key_action(index, action, action.key.value, action.duration_s)

    def _run_key_action(
        self,
        index: int,
        action: CameraKeyHold | ResetZoomKey,
        key: str,
        duration_s: float,
    ) -> CameraActionReceipt:
        down_receipt: CameraInputReceipt | None = None
        up_receipt: CameraInputReceipt | None = None
        owns_key = False
        try:
            candidate_down_receipt = self._control.key_down(key)
            owns_key = candidate_down_receipt.completed_events > 0
            down_receipt = _require_complete_receipt(
                candidate_down_receipt,
                CameraInputOperation.KEY_DOWN,
            )
            self._sleeper(duration_s)
        finally:
            if owns_key:
                up_receipt = _require_complete_receipt(
                    self._control.key_up(key),
                    CameraInputOperation.KEY_UP,
                )
        if down_receipt is None or up_receipt is None:  # pragma: no cover - defensive
            raise CameraReceiptError("key action completed without owned down/up receipts")
        return CameraActionReceipt(index, action, (down_receipt, up_receipt))
