"""Narrow Windows camera-control adapter for development validation.

This module is intentionally separate from both the production capture seam
and perception.  It exposes only the bounded input operations representable
by ``camera_plan`` and keeps every actual Win32 call behind an injected protocol,
so deterministic tests run on Linux without sending input.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from typing import Any, Protocol

from .camera_coordinates import CameraCoordinateMapping, CameraDpiEnvironment
from .camera_plan import (
    EXPECTED_CLIENT_HEIGHT,
    EXPECTED_CLIENT_WIDTH,
    REVIEWED_CAMERA_WHEEL_POINT,
    REVIEWED_COMPASS_POINT,
    CameraDragAxis,
    CameraInputOperation,
    CameraInputReceipt,
    CameraMiddleDrag,
    CameraPreflightReceipt,
    Sleeper,
    camera_drag_path,
)

__all__ = [
    "RealWindowsCameraApi",
    "CAMERA_DRAG_STEP_INTERVAL_SECONDS",
    "CAMERA_KEY_RELEASE_SETTLE_SECONDS",
    "CAMERA_MIDDLE_ARMING_SETTLE_SECONDS",
    "CAMERA_MIDDLE_RELEASE_SETTLE_SECONDS",
    "CAMERA_WHEEL_EVENT_INTERVAL_SECONDS",
    "COMPASS_CLICK_DWELL_SECONDS",
    "WindowsCameraTargetIdentity",
    "WindowsCameraApi",
    "WindowsCameraControl",
    "WindowsCameraError",
]

CAMERA_WHEEL_EVENT_INTERVAL_SECONDS = 0.025
CAMERA_DRAG_STEP_INTERVAL_SECONDS = 1.000
CAMERA_KEY_RELEASE_SETTLE_SECONDS = 1.000
CAMERA_MIDDLE_ARMING_SETTLE_SECONDS = 1.000
CAMERA_MIDDLE_RELEASE_SETTLE_SECONDS = 1.000
COMPASS_CLICK_DWELL_SECONDS = 0.100

_ARROW_KEYS: dict[str, int] = {
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
}
_RESET_KEYS: dict[str, int] = {
    "control": 0x11,
    "ctrl": 0x11,
}
_CONTROLLED_VIRTUAL_KEYS = tuple(
    sorted(set(_ARROW_KEYS.values()) | set(_RESET_KEYS.values()))
)
_KEY_RELEASE_ATTEMPTS = 3
_MOUSE_RELEASE_ATTEMPTS = 3


def _require_complete_window_title_snapshot(
    value: str,
    *,
    expected_length: int,
    copied_length: int,
    final_length: int,
) -> str:
    if (
        expected_length <= 0
        or copied_length != expected_length
        or final_length != expected_length
    ):
        raise OSError("target RuneLite window title changed while being read")
    return value


class WindowsCameraError(RuntimeError):
    """The target window or Win32 input boundary is not safe to use."""


@dataclass(frozen=True, slots=True)
class WindowsCameraTargetIdentity:
    """Stable facts that distinguish the reviewed target from an HWND reuse."""

    process_id: int
    thread_id: int
    class_name: str
    title: str

    def __post_init__(self) -> None:
        if self.process_id <= 0:
            raise ValueError("window process_id must be positive")
        if self.thread_id <= 0:
            raise ValueError("window thread_id must be positive")
        if not self.class_name:
            raise ValueError("window class_name must not be empty")
        if not self.title:
            raise ValueError("window title must not be empty")


class WindowsCameraApi(Protocol):
    """OS seam used only by :class:`WindowsCameraControl`."""

    def declare_dpi_awareness(self) -> None:
        """Request physical-pixel client geometry, best effort."""

    def is_window(self, hwnd: int) -> bool:
        """Return whether ``hwnd`` still identifies a window."""

    def window_identity(self, hwnd: int) -> WindowsCameraTargetIdentity:
        """Return owner and metadata used to detect a recycled HWND."""

    def focus_window(self, hwnd: int) -> bool:
        """Try to make ``hwnd`` the foreground window."""

    def foreground_window(self) -> int | None:
        """Return the current foreground HWND, if any."""

    def client_size(self, hwnd: int) -> tuple[int, int]:
        """Return current physical client width and height."""

    def client_to_screen(self, hwnd: int, x: int, y: int) -> tuple[int, int]:
        """Convert a target-logical client point to physical screen coordinates."""

    def move_cursor(self, x: int, y: int) -> bool:
        """Move the system cursor to a physical screen point."""

    def cursor_position(self) -> tuple[int, int]:
        """Return the cursor's actual physical screen coordinate."""

    def root_window_at_point(self, x: int, y: int) -> int | None:
        """Return the root window that would receive pointer input there."""

    def send_mouse_button(self, *, button_up: bool) -> int:
        """Send one left-button phase, returning its accepted event count."""

    def left_button_is_down(self) -> bool:
        """Return whether the global left button is already held."""

    def send_middle_button(self, *, button_up: bool) -> int:
        """Send one middle-button phase, returning its accepted event count."""

    def middle_button_is_down(self) -> bool:
        """Return whether the global middle button is already held."""

    def send_key(self, virtual_key: int, *, key_up: bool, extended: bool) -> int:
        """Send one keyboard event, returning the accepted event count."""

    def send_wheel(self, detents: int) -> int:
        """Send exactly one signed detent and return its accepted count."""

    def key_is_down(self, virtual_key: int) -> bool:
        """Return whether a key is already held before a new key-down."""


class RealWindowsCameraApi:
    """Real Win32 implementation, loaded lazily and only on Windows."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError(
                "RealWindowsCameraApi requires Windows (sys.platform == 'win32'); "
                f"got {sys.platform!r}. Inject a WindowsCameraApi fake in tests."
            )
        from . import _camera_win32_calls

        self._calls: Any = _camera_win32_calls

    def declare_dpi_awareness(self) -> None:
        self._calls.declare_dpi_awareness()

    def is_window(self, hwnd: int) -> bool:
        result: bool = self._calls.is_window(hwnd)
        return result

    def window_identity(self, hwnd: int) -> WindowsCameraTargetIdentity:
        process_id, thread_id, class_name, title = self._calls.window_identity(hwnd)
        return WindowsCameraTargetIdentity(
            process_id=process_id,
            thread_id=thread_id,
            class_name=class_name,
            title=title,
        )

    def focus_window(self, hwnd: int) -> bool:
        result: bool = self._calls.focus_window(hwnd)
        return result

    def foreground_window(self) -> int | None:
        result: int | None = self._calls.foreground_window()
        return result

    def client_size(self, hwnd: int) -> tuple[int, int]:
        result: tuple[int, int] = self._calls.client_size(hwnd)
        return result

    def client_to_screen(self, hwnd: int, x: int, y: int) -> tuple[int, int]:
        result: tuple[int, int] = self._calls.client_to_screen(hwnd, x, y)
        return result

    def pointer_mapping(self, hwnd: int, x: int, y: int) -> CameraCoordinateMapping:
        """Return the no-input coordinate trace used by production mapping."""

        result: CameraCoordinateMapping = self._calls.pointer_mapping(hwnd, x, y)
        return result

    def dpi_environment(self, hwnd: int) -> CameraDpiEnvironment:
        """Return native DPI/geometry facts without sending any input."""

        result: CameraDpiEnvironment = self._calls.dpi_environment(hwnd)
        return result

    def physical_screen_to_physical_client(
        self,
        hwnd: int,
        x: int,
        y: int,
    ) -> tuple[int, int]:
        """Cross-check a physical screen point in physical client coordinates."""

        result: tuple[int, int] = self._calls.physical_screen_to_physical_client(
            hwnd,
            x,
            y,
        )
        return result

    def capture_physical_screen_rect(
        self,
        left: int,
        top: int,
        width: int,
        height: int,
    ) -> bytes:
        """Capture a physical screen rectangle without sending input."""

        result: bytes = self._calls.capture_physical_screen_rect(
            left,
            top,
            width,
            height,
        )
        return result

    def mapping_candidate_comparison(
        self,
        hwnd: int,
        x: int,
        y: int,
    ) -> dict[str, object]:
        """Return no-input legacy/corrected mapping candidates for audit only."""

        result: dict[str, object] = self._calls.mapping_candidate_comparison(
            hwnd,
            x,
            y,
        )
        return result

    def move_cursor(self, x: int, y: int) -> bool:
        result: bool = self._calls.move_cursor(x, y)
        return result

    def cursor_position(self) -> tuple[int, int]:
        result: tuple[int, int] = self._calls.cursor_position()
        return result

    def root_window_at_point(self, x: int, y: int) -> int | None:
        result: int | None = self._calls.root_window_at_point(x, y)
        return result

    def send_mouse_button(self, *, button_up: bool) -> int:
        result: int = self._calls.send_mouse_button(button_up=button_up)
        return result

    def left_button_is_down(self) -> bool:
        result: bool = self._calls.left_button_is_down()
        return result

    def send_middle_button(self, *, button_up: bool) -> int:
        result: int = self._calls.send_middle_button(button_up=button_up)
        return result

    def middle_button_is_down(self) -> bool:
        result: bool = self._calls.middle_button_is_down()
        return result

    def send_key(self, virtual_key: int, *, key_up: bool, extended: bool) -> int:
        result: int = self._calls.send_key(
            virtual_key,
            key_up=key_up,
            extended=extended,
        )
        return result

    def send_wheel(self, detents: int) -> int:
        result: int = self._calls.send_wheel(detents)
        return result

    def key_is_down(self, virtual_key: int) -> bool:
        result: bool = self._calls.key_is_down(virtual_key)
        return result


class WindowsCameraControl:
    """Fail-closed camera input for one already-discovered RuneLite HWND."""

    def __init__(
        self,
        hwnd: int,
        api: WindowsCameraApi | None = None,
        *,
        expected_class_name: str | None = None,
        expected_title: str | None = None,
        wheel_sleeper: Sleeper = time.sleep,
        click_sleeper: Sleeper = time.sleep,
        drag_sleeper: Sleeper = time.sleep,
        key_release_sleeper: Sleeper = time.sleep,
    ) -> None:
        if isinstance(hwnd, bool) or not isinstance(hwnd, int) or hwnd <= 0:
            raise ValueError("hwnd must be a positive integer")
        self._hwnd = hwnd
        self._api = api if api is not None else RealWindowsCameraApi()
        self._wheel_sleeper = wheel_sleeper
        self._click_sleeper = click_sleeper
        self._drag_sleeper = drag_sleeper
        self._key_release_sleeper = key_release_sleeper
        self._held_keys: dict[int, bool] = {}
        self._left_button_owned = False
        self._middle_button_owned = False
        self._api.declare_dpi_awareness()
        try:
            self._target_identity = self._api.window_identity(self._hwnd)
        except (OSError, ValueError) as exc:
            raise WindowsCameraError(
                "could not bind the target RuneLite window identity"
            ) from exc
        if (
            expected_class_name is not None
            and self._target_identity.class_name != expected_class_name
        ):
            raise WindowsCameraError(
                "discovery-time RuneLite window class no longer matches the "
                "target HWND identity"
            )
        if expected_title is not None and self._target_identity.title != expected_title:
            raise WindowsCameraError(
                "discovery-time RuneLite window title no longer matches the "
                "target HWND identity"
            )

    @property
    def hwnd(self) -> int:
        return self._hwnd

    def preflight(self) -> CameraPreflightReceipt:
        """Focus the exact window and report fresh physical client geometry."""

        if not self._api.is_window(self._hwnd):
            raise WindowsCameraError("target RuneLite window no longer exists")
        self._require_target_identity()
        self._require_all_control_inputs_released()
        self._require_target_identity()
        self._api.focus_window(self._hwnd)
        self._require_target_identity()
        focused = self._api.foreground_window() == self._hwnd
        width, height = self._api.client_size(self._hwnd)
        self._require_target_identity()
        self._require_all_control_inputs_released()
        return CameraPreflightReceipt(focused, width, height)

    def click_compass(self, x: int, y: int) -> CameraInputReceipt:
        self._require_reviewed_point("compass click", x, y, REVIEWED_COMPASS_POINT)
        self._require_ready()
        self._require_all_control_inputs_released(operation="compass click")
        screen_point = self._move_to_client_point(x, y)
        self._require_ready()
        self._require_target_at_screen_point("compass click", screen_point)
        # The bootstrap intentionally performs its broader, potentially
        # focusing preflight before the final world commit. Recheck every
        # controlled global input at the last seam so a user-held middle
        # button or key that appeared during commit evaluation cannot turn
        # this compass request into an unintended interaction.
        self._require_all_control_inputs_released(operation="compass click")
        completed_down = 0
        # The pre-checks proved the button was not externally held. Mark
        # provisional ownership before the OS call so an exception raised
        # after insertion still leaves a lifecycle-cleanup obligation.
        self._left_button_owned = True
        try:
            completed_down = self._api.send_mouse_button(button_up=False)
            if completed_down not in (0, 1):
                raise WindowsCameraError(
                    "Windows returned an invalid left-button-down event count"
                )
            if completed_down == 1:
                # A down/up pair acknowledged back-to-back can be coalesced
                # before RuneLite observes a semantic compass click. Hold the
                # adapter-owned button for one fixed interval, then prove the
                # unchanged point remains safe before releasing it.
                self._click_sleeper(COMPASS_CLICK_DWELL_SECONDS)
                self._require_owned_click_point_still_safe(x, y)
        finally:
            completed_up = self._release_mouse_button()
        return CameraInputReceipt(
            CameraInputOperation.COMPASS_CLICK,
            requested_events=2,
            completed_events=completed_down + completed_up,
        )

    def key_down(self, key: str) -> CameraInputReceipt:
        self._require_ready()
        virtual_key, extended = _key_code(key)
        if virtual_key in self._held_keys or self._api.key_is_down(virtual_key):
            raise WindowsCameraError(f"refusing key-down because {key!r} is already held")
        # GetAsyncKeyState and application callbacks can yield long enough for
        # focus or geometry to change. Recheck at the last seam before global
        # keyboard injection, just as pointer input rechecks after cursor move.
        self._require_ready()
        try:
            completed = self._api.send_key(
                virtual_key,
                key_up=False,
                extended=extended,
            )
        except BaseException:
            # The pre-check proved this key was not held before our call.  If
            # the platform raised after insertion, a bounded compensating up
            # is safe; unlike the plan runner, the adapter owns this ambiguity.
            self._best_effort_key_release(virtual_key, extended=extended)
            raise
        if completed not in (0, 1):
            self._best_effort_key_release(virtual_key, extended=extended)
            raise WindowsCameraError("Windows returned an invalid key-down event count")
        if completed == 1:
            self._held_keys[virtual_key] = extended
        return CameraInputReceipt(
            CameraInputOperation.KEY_DOWN,
            requested_events=1,
            completed_events=completed,
        )

    def key_up(self, key: str) -> CameraInputReceipt:
        # A release is deliberately attempted even after focus/geometry loss.
        # The plan runner calls this in ``finally``; blocking it on a second
        # preflight could leave a globally held arrow key behind.
        virtual_key, extended = _key_code(key)
        if virtual_key not in self._held_keys:
            raise WindowsCameraError(
                f"refusing key-up because this control does not own {key!r}"
            )
        completed = self._release_owned_key(
            virtual_key,
            extended=extended,
            verify_target=True,
        )
        if completed == 1:
            self._held_keys.pop(virtual_key, None)
        return CameraInputReceipt(
            CameraInputOperation.KEY_UP,
            requested_events=1,
            completed_events=completed,
        )

    def scroll_camera(self, x: int, y: int, detents: int) -> CameraInputReceipt:
        self._require_reviewed_point(
            "camera wheel", x, y, REVIEWED_CAMERA_WHEEL_POINT
        )
        direction = 1 if detents > 0 else -1
        requested_events = abs(detents)
        completed_events = 0
        for event_index in range(requested_events):
            if event_index:
                self._wheel_sleeper(CAMERA_WHEEL_EVENT_INTERVAL_SECONDS)
            # A single SendInput batch can be fully acknowledged by Windows
            # while RuneLite semantically drops/coalesces most of the wheel
            # messages. Deliver one detent at a time, and re-establish every
            # pointer safety invariant after each bounded pacing interval.
            self._require_ready()
            self._require_pointer_buttons_released("camera wheel")
            screen_point = self._move_to_client_point(x, y)
            self._require_ready()
            self._require_pointer_buttons_released("camera wheel")
            self._require_target_at_screen_point("camera wheel", screen_point)
            completed = self._api.send_wheel(direction)
            if completed not in (0, 1):
                raise WindowsCameraError(
                    "Windows returned an invalid camera-wheel event count"
                )
            completed_events += completed
            if completed == 0:
                break
        return CameraInputReceipt(
            CameraInputOperation.CAMERA_WHEEL,
            requested_events=requested_events,
            completed_events=completed_events,
        )

    def drag_camera(
        self,
        x: int,
        y: int,
        delta_x: int,
        delta_y: int,
    ) -> tuple[CameraInputReceipt, CameraInputReceipt, CameraInputReceipt]:
        """Execute one bounded, axis-aligned validation-only middle drag."""

        action = _camera_drag_action(x, y, delta_x, delta_y)
        path = camera_drag_path(action)
        self._require_ready()
        self._require_pointer_buttons_released("camera drag")
        self._preflight_drag_path(((x, y), *path))
        self._move_to_verified_drag_point(x, y, middle_must_be_down=False)
        # Recheck immediately before provisional ownership. A user-held button
        # discovered here must never be compensated by this adapter.
        self._require_pointer_buttons_released("camera drag")
        self._require_drag_start_still_safe(x, y)

        completed_down = 0
        completed_moves = 0
        self._middle_button_owned = True
        try:
            completed_down = self._api.send_middle_button(button_up=False)
            if completed_down not in (0, 1):
                raise WindowsCameraError(
                    "Windows returned an invalid middle-button-down event count"
                )
            if completed_down == 1:
                # RuneLite needs one semantic arming interval after the
                # acknowledged down before it samples camera motion.
                self._drag_sleeper(CAMERA_MIDDLE_ARMING_SETTLE_SECONDS)
                self._require_drag_point_still_safe(x, y)
                for point_x, point_y in path:
                    self._move_to_verified_drag_point(
                        point_x,
                        point_y,
                        middle_must_be_down=True,
                    )
                    completed_moves += 1
                    # Settle after every move, including the final endpoint,
                    # and revalidate before any later move or release.
                    self._drag_sleeper(CAMERA_DRAG_STEP_INTERVAL_SECONDS)
                    self._require_drag_point_still_safe(point_x, point_y)
        finally:
            completed_up = self._release_middle_button(
                verified_endpoint=(
                    path[-1]
                    if completed_down == 1 and completed_moves == len(path)
                    else None
                ),
            )

        return (
            CameraInputReceipt(
                CameraInputOperation.MIDDLE_DOWN,
                requested_events=1,
                completed_events=completed_down,
            ),
            CameraInputReceipt(
                CameraInputOperation.CAMERA_DRAG_MOVE,
                requested_events=len(path),
                completed_events=completed_moves,
            ),
            CameraInputReceipt(
                CameraInputOperation.MIDDLE_UP,
                requested_events=1,
                completed_events=completed_up,
            ),
        )

    def release_all_held_keys(self) -> tuple[CameraInputReceipt, ...]:
        """Best-effort lifecycle cleanup for every input owned by this adapter.

        The historical method name is retained for the tool API, but cleanup
        includes provisionally or definitively owned middle and left mouse
        buttons as well as every key. Every input is attempted even if another release
        fails. Remaining owned inputs cause a fail-closed error so a CLI cannot
        report successful cleanup while Windows may still consider an injected
        input held. Returned receipts describe key releases; mouse-up is a
        compensating lifecycle action rather than a complete click receipt.
        """

        receipts: list[CameraInputReceipt] = []
        failures: list[str] = []
        if self._middle_button_owned:
            try:
                completed_middle_up = self._release_middle_button()
            except BaseException as exc:
                failures.append(f"middle mouse button: {exc}")
            else:
                if completed_middle_up != 1:
                    failures.append("middle mouse button: short button-up receipt")
        if self._left_button_owned:
            try:
                completed_mouse_up = self._release_mouse_button()
            except BaseException as exc:
                failures.append(f"left mouse button: {exc}")
            else:
                if completed_mouse_up != 1:
                    failures.append("left mouse button: short button-up receipt")
        for virtual_key, extended in sorted(tuple(self._held_keys.items())):
            try:
                completed = self._release_owned_key(
                    virtual_key,
                    extended=extended,
                    verify_target=False,
                )
            except BaseException as exc:
                failures.append(f"0x{virtual_key:02x}: {exc}")
                continue
            receipt = CameraInputReceipt(
                CameraInputOperation.KEY_UP,
                requested_events=1,
                completed_events=completed,
            )
            receipts.append(receipt)
            if completed == 1:
                self._held_keys.pop(virtual_key, None)
            else:
                failures.append(f"0x{virtual_key:02x}: short key-up receipt")
        if failures:
            raise WindowsCameraError(
                "could not release every camera input owned by this control: "
                + "; ".join(failures)
            )
        return tuple(receipts)

    def _require_ready(self) -> None:
        if not self._api.is_window(self._hwnd):
            raise WindowsCameraError("target RuneLite window no longer exists")
        self._require_target_identity()
        if self._api.foreground_window() != self._hwnd:
            raise WindowsCameraError("target RuneLite window lost foreground focus")
        width, height = self._api.client_size(self._hwnd)
        if (width, height) != (EXPECTED_CLIENT_WIDTH, EXPECTED_CLIENT_HEIGHT):
            raise WindowsCameraError(
                "target RuneLite client geometry changed during the camera plan: "
                f"{width}x{height}"
            )
        # HWND values may be recycled without changing the foreground handle or
        # geometry. Re-read identity after the other readiness queries so a
        # replacement during the check cannot inherit their approval.
        self._require_target_identity()

    def _require_target_identity(self) -> None:
        try:
            current = self._api.window_identity(self._hwnd)
        except (OSError, ValueError) as exc:
            raise WindowsCameraError(
                "could not revalidate the target RuneLite window identity"
            ) from exc
        if current != self._target_identity:
            raise WindowsCameraError(
                "target RuneLite window identity changed while the HWND was reused"
            )

    def _require_all_control_inputs_released(
        self,
        *,
        operation: str | None = None,
    ) -> None:
        if self._api.left_button_is_down():
            if operation is not None:
                raise WindowsCameraError(
                    f"refusing {operation} because the left button is already held"
                )
            raise WindowsCameraError(
                "refusing camera preflight because the global left button is held"
            )
        if self._api.middle_button_is_down():
            if operation is not None:
                raise WindowsCameraError(
                    f"refusing {operation} because the middle button is already held"
                )
            raise WindowsCameraError(
                "refusing camera preflight because the global middle button is held"
            )
        held_keys = [
            virtual_key
            for virtual_key in _CONTROLLED_VIRTUAL_KEYS
            if self._api.key_is_down(virtual_key)
        ]
        if held_keys:
            rendered = ", ".join(f"0x{key:02x}" for key in held_keys)
            if operation is not None:
                raise WindowsCameraError(
                    f"refusing {operation} because validator-controlled global "
                    f"keys are held: {rendered}"
                )
            raise WindowsCameraError(
                "refusing camera preflight because validator-controlled global "
                f"keys are held: {rendered}"
            )

    def _move_to_client_point(self, x: int, y: int) -> tuple[int, int]:
        screen_x, screen_y = self._api.client_to_screen(self._hwnd, x, y)
        return self._move_to_physical_screen_point(screen_x, screen_y)

    def _move_to_physical_screen_point(
        self,
        screen_x: int,
        screen_y: int,
    ) -> tuple[int, int]:
        if not self._api.move_cursor(screen_x, screen_y):
            raise WindowsCameraError("Windows refused to move the cursor to the target")
        expected = screen_x, screen_y
        actual = self._api.cursor_position()
        if actual != expected:
            raise WindowsCameraError(
                "camera pointer did not reach the reviewed physical screen point: "
                f"expected {expected}, got {actual}"
            )
        return actual

    def _require_owned_click_point_still_safe(self, x: int, y: int) -> None:
        """Revalidate an unchanged compass point while left-down is owned."""

        self._require_ready()
        self._require_owned_left_button_down()
        expected_point = self._api.client_to_screen(self._hwnd, x, y)
        actual_point = self._api.cursor_position()
        if actual_point != expected_point:
            raise WindowsCameraError(
                "compass-click cursor moved during its bounded dwell: "
                f"expected {expected_point}, got {actual_point}"
            )
        self._require_target_at_screen_point("compass click", actual_point)

    def _move_to_verified_drag_point(
        self,
        x: int,
        y: int,
        *,
        middle_must_be_down: bool,
    ) -> tuple[int, int]:
        """Map, move, and prove ownership of one logical drag path point."""

        self._require_ready()
        self._require_left_button_released("camera drag")
        if middle_must_be_down:
            self._require_owned_middle_button_down()
        elif self._api.middle_button_is_down():
            raise WindowsCameraError(
                "refusing camera drag because the middle button is already held"
            )
        self._require_ready()
        screen_point = self._api.client_to_screen(self._hwnd, x, y)
        self._require_target_root_at_screen_point("camera drag", screen_point)
        self._require_ready()
        self._require_left_button_released("camera drag")
        if middle_must_be_down:
            self._require_owned_middle_button_down()
        elif self._api.middle_button_is_down():
            raise WindowsCameraError(
                "refusing camera drag because the middle button became held"
            )
        self._require_ready()
        self._require_target_root_at_screen_point("camera drag", screen_point)
        screen_point = self._move_to_physical_screen_point(*screen_point)
        self._require_ready()
        self._require_left_button_released("camera drag")
        if middle_must_be_down:
            self._require_owned_middle_button_down()
        elif self._api.middle_button_is_down():
            raise WindowsCameraError(
                "refusing camera drag because the middle button became held"
            )
        self._require_ready()
        self._require_target_at_screen_point("camera drag", screen_point)
        return screen_point

    def _preflight_drag_path(
        self,
        logical_points: tuple[tuple[int, int], ...],
    ) -> None:
        """Prove the complete logical corridor is target-owned before down."""

        for point_x, point_y in logical_points:
            self._require_ready()
            self._require_pointer_buttons_released("camera drag")
            self._require_ready()
            screen_point = self._api.client_to_screen(
                self._hwnd,
                point_x,
                point_y,
            )
            self._require_target_root_at_screen_point("camera drag", screen_point)
            self._require_ready()
            self._require_pointer_buttons_released("camera drag")
            self._require_ready()
            self._require_target_root_at_screen_point("camera drag", screen_point)

    def _require_drag_point_still_safe(self, x: int, y: int) -> None:
        """Revalidate an unchanged logical path point while middle-down is owned."""

        self._require_ready()
        self._require_left_button_released("camera drag")
        self._require_owned_middle_button_down()
        self._require_ready()
        expected_point = self._api.client_to_screen(self._hwnd, x, y)
        actual_point = self._api.cursor_position()
        if actual_point != expected_point:
            raise WindowsCameraError(
                "camera-drag cursor moved during its bounded settle: "
                f"expected {expected_point}, got {actual_point}"
            )
        self._require_target_at_screen_point("camera drag", actual_point)

    def _require_drag_start_still_safe(self, x: int, y: int) -> None:
        """Final start-point and button gate immediately before middle-down."""

        self._require_ready()
        self._require_pointer_buttons_released("camera drag")
        self._require_ready()
        expected_point = self._api.client_to_screen(self._hwnd, x, y)
        actual_point = self._api.cursor_position()
        if actual_point != expected_point:
            raise WindowsCameraError(
                "camera-drag cursor moved before middle-button-down: "
                f"expected {expected_point}, got {actual_point}"
            )
        self._require_target_at_screen_point("camera drag", actual_point)

    def _release_mouse_button(self) -> int:
        last_error: BaseException | None = None
        for _attempt in range(_MOUSE_RELEASE_ATTEMPTS):
            try:
                completed = self._api.send_mouse_button(button_up=True)
            except BaseException as exc:
                last_error = exc
                continue
            if completed == 1:
                self._left_button_owned = False
                return 1
            if completed != 0:
                last_error = WindowsCameraError(
                    "Windows returned an invalid left-button-up event count"
                )
        if last_error is not None:
            raise WindowsCameraError(
                f"left-button release failed after {_MOUSE_RELEASE_ATTEMPTS} attempts"
            ) from last_error
        return 0

    def _release_middle_button(
        self,
        *,
        verified_endpoint: tuple[int, int] | None = None,
    ) -> int:
        last_error: BaseException | None = None
        for _attempt in range(_MOUSE_RELEASE_ATTEMPTS):
            try:
                completed = self._api.send_middle_button(button_up=True)
            except BaseException as exc:
                last_error = exc
                continue
            if completed == 1:
                # SendInput acknowledges insertion into the global input
                # stream, not semantic consumption by RuneLite's AWT event
                # thread. Keep ownership while one fixed settle elapses, then
                # prove the up is globally observable before a later action is
                # allowed to relocate the cursor.
                self._drag_sleeper(CAMERA_MIDDLE_RELEASE_SETTLE_SECONDS)
                if verified_endpoint is not None:
                    self._require_released_drag_endpoint_still_safe(
                        *verified_endpoint,
                    )
                elif self._api.middle_button_is_down():
                    raise WindowsCameraError(
                        "middle-button release was not observable after the "
                        "fixed post-release settle"
                    )
                self._middle_button_owned = False
                return 1
            if completed != 0:
                last_error = WindowsCameraError(
                    "Windows returned an invalid middle-button-up event count"
                )
        if last_error is not None:
            raise WindowsCameraError(
                f"middle-button release failed after {_MOUSE_RELEASE_ATTEMPTS} attempts"
            ) from last_error
        return 0

    def _require_released_drag_endpoint_still_safe(self, x: int, y: int) -> None:
        """Prove semantic middle-up and unchanged endpoint after its settle."""

        operation = "camera drag post-release verification"
        self._require_ready()
        self._require_left_button_released(operation)
        self._require_observable_middle_button_release()
        self._require_ready()
        expected_point = self._api.client_to_screen(self._hwnd, x, y)
        actual_point = self._api.cursor_position()
        if actual_point != expected_point:
            raise WindowsCameraError(
                "camera-drag cursor moved during its post-release settle: "
                f"expected {expected_point}, got {actual_point}"
            )
        self._require_left_button_released(operation)
        self._require_observable_middle_button_release()
        self._require_target_at_screen_point(operation, actual_point)

    def _require_observable_middle_button_release(self) -> None:
        if self._api.middle_button_is_down():
            raise WindowsCameraError(
                "middle-button release was not observable after the fixed "
                "post-release settle"
            )

    def _require_left_button_released(self, operation: str) -> None:
        if self._api.left_button_is_down():
            raise WindowsCameraError(
                f"refusing {operation} because the left button is already held"
            )

    def _require_pointer_buttons_released(self, operation: str) -> None:
        self._require_left_button_released(operation)
        if self._api.middle_button_is_down():
            raise WindowsCameraError(
                f"refusing {operation} because the middle button is already held"
            )

    def _require_owned_left_button_down(self) -> None:
        if not self._left_button_owned or not self._api.left_button_is_down():
            raise WindowsCameraError(
                "compass click lost its owned left-button hold before completion"
            )

    def _require_owned_middle_button_down(self) -> None:
        if not self._middle_button_owned or not self._api.middle_button_is_down():
            raise WindowsCameraError(
                "camera drag lost its owned middle-button hold before completion"
            )

    def _require_target_at_screen_point(
        self,
        operation: str,
        screen_point: tuple[int, int],
    ) -> None:
        actual_point = self._api.cursor_position()
        if actual_point != screen_point:
            raise WindowsCameraError(
                f"{operation} cursor moved before final pointer ownership check: "
                f"expected {screen_point}, got {actual_point}"
            )
        self._require_target_root_at_screen_point(operation, actual_point)

    def _require_target_root_at_screen_point(
        self,
        operation: str,
        screen_point: tuple[int, int],
    ) -> None:
        """Require the target root at one already-mapped physical point."""

        root_window = self._api.root_window_at_point(*screen_point)
        if root_window != self._hwnd:
            raise WindowsCameraError(
                f"refusing {operation} because the reviewed point is covered "
                "by another top-level window"
            )
        # This is the final seam before global pointer injection. A recycled
        # handle can still own the reviewed point and retain the same geometry.
        self._require_target_identity()

    def _release_owned_key(
        self,
        virtual_key: int,
        *,
        extended: bool,
        verify_target: bool,
    ) -> int:
        last_error: BaseException | None = None
        for _attempt in range(_KEY_RELEASE_ATTEMPTS):
            try:
                completed = self._api.send_key(
                    virtual_key,
                    key_up=True,
                    extended=extended,
                )
            except BaseException as exc:
                last_error = exc
                continue
            if completed == 1:
                # SendInput proves insertion, not consumption by RuneLite's
                # AWT event thread. Retain ownership across one fixed semantic
                # boundary, prove the global key is observably up, and—during
                # a normal plan action—revalidate the exact target before any
                # later action can run. Lifecycle cleanup deliberately omits
                # target readiness so release remains possible after focus or
                # geometry loss.
                self._key_release_sleeper(CAMERA_KEY_RELEASE_SETTLE_SECONDS)
                if self._api.key_is_down(virtual_key):
                    raise WindowsCameraError(
                        "key release was not observable after the fixed "
                        "semantic client-consumption settle"
                    )
                if verify_target:
                    self._require_ready()
                return 1
            if completed != 0:
                last_error = WindowsCameraError(
                    "Windows returned an invalid key-up event count"
                )
        if last_error is not None:
            raise WindowsCameraError(
                f"key release failed after {_KEY_RELEASE_ATTEMPTS} attempts"
            ) from last_error
        return 0

    def _best_effort_key_release(self, virtual_key: int, *, extended: bool) -> None:
        # A raised key-down call is ambiguous: Windows may have inserted the
        # event before the adapter observed the exception.  The pre-check
        # established that this was not a user-held key, so retain provisional
        # ownership until a compensating up is acknowledged.
        self._held_keys[virtual_key] = extended
        try:
            completed = self._release_owned_key(
                virtual_key,
                extended=extended,
                verify_target=False,
            )
        except BaseException:
            # Preserve the primary key-down failure.  The lifecycle cleanup
            # can retry because provisional ownership remains recorded.
            pass
        else:
            if completed == 1:
                self._held_keys.pop(virtual_key, None)

    @staticmethod
    def _require_reviewed_point(
        operation: str,
        x: int,
        y: int,
        reviewed_point: tuple[int, int],
    ) -> None:
        if (x, y) != reviewed_point:
            raise WindowsCameraError(
                f"{operation} must use exact reviewed client point {reviewed_point}"
            )


def _key_code(key: str) -> tuple[int, bool]:
    normalized = key.strip().lower() if isinstance(key, str) else ""
    if normalized in _ARROW_KEYS:
        return _ARROW_KEYS[normalized], True
    if normalized in _RESET_KEYS:
        return _RESET_KEYS[normalized], False
    raise WindowsCameraError(
        "camera validation permits only arrow keys and the Control reset-zoom key"
    )


def _camera_drag_action(
    x: int,
    y: int,
    delta_x: int,
    delta_y: int,
) -> CameraMiddleDrag:
    """Validate the platform call through the shared logical drag contract."""

    if any(
        isinstance(value, bool) or not isinstance(value, int)
        for value in (x, y, delta_x, delta_y)
    ):
        raise WindowsCameraError("camera drag coordinates and deltas must be integers")
    if (delta_x == 0) == (delta_y == 0):
        raise WindowsCameraError(
            "camera drag requires exactly one nonzero logical axis delta"
        )
    axis = (
        CameraDragAxis.HORIZONTAL
        if delta_x != 0
        else CameraDragAxis.VERTICAL
    )
    pixels = delta_x if delta_x != 0 else delta_y
    try:
        return CameraMiddleDrag(axis, pixels, start_x=x, start_y=y)
    except ValueError as exc:
        raise WindowsCameraError(str(exc)) from exc
