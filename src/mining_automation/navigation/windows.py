"""Native navigation I/O with a pinned, unchanged RuneLite window.

Uses physical screen pixels, avoiding PrintWindow's possible DPI-resampled
rendering. The client must be unobscured. No resize, restore, minimize,
maximize, reposition, camera, inventory or keyboard-input operations exist.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import math
import sys
import time
from ctypes import wintypes
from dataclasses import asdict
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .runtime import RouteFrame


class NativeRouteBackend:
    def __init__(
        self,
        hwnd: int,
        output: Path,
        *,
        expected_title: str,
        focus_existing: bool = False,
        stop_file: Path | None = None,
        max_frame_age_s: float = 4.0,
    ) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Native route execution requires Windows")
        if isinstance(hwnd, bool) or hwnd <= 0:
            raise ValueError("A positive exact RuneLite HWND is required")
        from ..capture.windows.win32_api import RealWin32Api
        from ..validation.windows_camera import RealWindowsCameraApi

        self.capture_api = RealWin32Api()
        self.api = RealWindowsCameraApi()
        self.api.declare_dpi_awareness()
        self.hwnd, self.output, self.stop_file = hwnd, output, stop_file
        self.expected_title = expected_title
        self.max_frame_age_s = max_frame_age_s
        self.frame_id = 0
        self.delivered_click_count = 0
        self.last_dispatch_receipt: dict[str, Any] | None = None
        self._start_connector_pose_detectors: dict[str, Any] | None = None
        self._start_connector_resource_evaluator: Any = None
        self._start_connector_inventory_evaluator: Any = None
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        for name, result, arguments in [
            ("IsIconic", wintypes.BOOL, [wintypes.HWND]),
            ("IsZoomed", wintypes.BOOL, [wintypes.HWND]),
            ("IsWindowVisible", wintypes.BOOL, [wintypes.HWND]),
            ("GetWindowRect", wintypes.BOOL, [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]),
            ("SetForegroundWindow", wintypes.BOOL, [wintypes.HWND]),
        ]:
            fn = getattr(self.user32, name)
            fn.restype = result
            fn.argtypes = arguments
        self.initial = self.snapshot()
        if self.initial["identity"]["title"] != expected_title or not expected_title.startswith(
            "RuneLite - "
        ):
            raise RuntimeError("Wrong RuneLite account window")
        if self.initial["identity"]["class_name"] != "SunAwtFrame":
            raise RuntimeError("Unexpected RuneLite native window class")
        if focus_existing and self.api.foreground_window() != self.hwnd:
            # This only requests focus; it does not restore an iconic window
            # or normalize its geometry. No focus hacks or repeated attempts.
            self.user32.SetForegroundWindow(self.hwnd)
            time.sleep(0.15)
            self.guard()
        self.output.mkdir(parents=True, exist_ok=False)
        (self.output / "window.json").write_text(
            json.dumps(self.initial, indent=2), encoding="utf-8"
        )

    def now(self) -> float:
        return time.monotonic()

    def check_cancelled(self) -> None:
        if self.api.key_is_down(0x1B) or (self.stop_file and self.stop_file.exists()):
            raise RuntimeError("owner_stop")

    def wait(self, seconds: float) -> None:
        end = self.now() + seconds
        while self.now() < end:
            self.check_cancelled()
            time.sleep(min(0.05, max(0, end - self.now())))

    def snapshot(self) -> dict[str, Any]:
        if not self.api.is_window(self.hwnd) or self.user32.IsIconic(self.hwnd):
            raise RuntimeError("RuneLite_missing_or_minimized")
        if not self.user32.IsWindowVisible(self.hwnd):
            raise RuntimeError("RuneLite_hidden")
        rect = wintypes.RECT()
        if not self.user32.GetWindowRect(self.hwnd, ctypes.byref(rect)):
            raise OSError("GetWindowRect_failed")
        width, height = self.api.client_size(self.hwnd)
        return {
            "hwnd": self.hwnd,
            "identity": asdict(self.api.window_identity(self.hwnd)),
            "client_size": [width, height],
            "client_origin": list(self.api.client_to_screen(self.hwnd, 0, 0)),
            "window_rect": [rect.left, rect.top, rect.right, rect.bottom],
            "maximized": bool(self.user32.IsZoomed(self.hwnd)),
            "dpi": self.capture_api.get_dpi_for_window(self.hwnd),
            "coordinate_space": "physical_client_pixels",
            "dpi_environment": asdict(self.api.dpi_environment(self.hwnd)),
        }

    def guard(self) -> dict[str, Any]:
        self.check_cancelled()
        current = self.snapshot()
        if current != self.initial:
            raise RuntimeError("window_identity_or_geometry_changed")
        if self.api.foreground_window() != self.hwnd:
            raise RuntimeError("RuneLite_not_foreground_no_automatic_restore")
        return current

    def _screen_pixels(self, snapshot: dict[str, Any]) -> np.ndarray:
        """Read a physical client rectangle using the existing GDI lifecycle."""
        width, height = snapshot["client_size"]
        x, y = snapshot["client_origin"]
        # An always-on-top overlay or another app is not valid client evidence.
        for iy in range(1, 8):
            for ix in range(1, 8):
                if (
                    self.api.root_window_at_point(x + width * ix // 8, y + height * iy // 8)
                    != self.hwnd
                ):
                    raise RuntimeError("RuneLite_capture_occluded")
        payload = self.api.capture_physical_screen_rect(x, y, width, height)
        if len(payload) != width * height * 4:
            raise RuntimeError("incomplete_physical_capture")
        return np.frombuffer(payload, np.uint8).reshape(height, width, 4)[:, :, :3].copy()

    def capture(self, label: str) -> RouteFrame:
        before = self.guard()
        timestamp = self.now()
        image = self._screen_pixels(before)
        if self.guard() != before:
            raise RuntimeError("window_changed_during_capture")
        self.frame_id += 1
        safe = "".join(c for c in label if c.isalnum() or c in "-_")[:70]
        path = self.output / f"{self.frame_id:05d}-{safe}.png"
        if not cv2.imwrite(str(path), image):
            raise OSError("capture_evidence_write_failed")
        return RouteFrame(self.frame_id, timestamp, image, before, str(path))

    def prepare_start_connector_authority(
        self,
        *,
        pose_detectors: dict[str, Any],
        resource_evaluator: Any,
        inventory_evaluator: Any,
    ) -> None:
        """Prepare preserved mining evaluators before any authority frame exists."""

        if not pose_detectors or not callable(resource_evaluator):
            raise RuntimeError("start_connector_mining_evaluators_unavailable")
        if not callable(getattr(inventory_evaluator, "evaluate", None)):
            raise RuntimeError("start_connector_inventory_evaluator_unavailable")
        self._start_connector_pose_detectors = pose_detectors
        self._start_connector_resource_evaluator = resource_evaluator
        self._start_connector_inventory_evaluator = inventory_evaluator

    def verify_start_connector_authority(
        self,
        frame: RouteFrame,
        connector: dict[str, Any],
    ) -> dict[str, Any]:
        """Require one native BGRA Resource+Inventory epoch before route input."""

        from ..mining_slice import (
            INVENTORY_CAPACITY,
            PerceptionEpoch,
            ResourceViewState,
            WorldStatePublicationStatus,
            assemble_atomic_mining_world_state,
        )
        from ..perception.live_pose_references import POSE_FRAME_HEIGHT, POSE_FRAME_WIDTH

        before = self.guard()
        if frame.window != before or frame.frame_id != self.frame_id:
            raise RuntimeError("stale_start_connector_authority_frame")
        expected_pose = connector.get("source_pose_id")
        detectors = self._start_connector_pose_detectors
        resource_evaluator = self._start_connector_resource_evaluator
        inventory_evaluator = self._start_connector_inventory_evaluator
        if (
            type(expected_pose) is not str
            or detectors is None
            or expected_pose not in detectors
            or not callable(resource_evaluator)
            or not callable(getattr(inventory_evaluator, "evaluate", None))
        ):
            return {
                "accepted": False,
                "reason": "source_pose_evaluator_unavailable",
                "route_source_frame_id": frame.frame_id,
                "route_source_captured_monotonic_s": frame.captured_monotonic_s,
                "window": before,
                "expected_pose_id": expected_pose,
            }
        if before.get("client_size") != [POSE_FRAME_WIDTH, POSE_FRAME_HEIGHT]:
            return {
                "accepted": False,
                "reason": "current_frame_geometry_unproven",
                "route_source_frame_id": frame.frame_id,
                "route_source_captured_monotonic_s": frame.captured_monotonic_s,
                "window": before,
                "expected_pose_id": expected_pose,
            }
        mining_frame, native_window, native_dpi, native_path = (
            self._capture_start_connector_native_frame()
        )
        if (
            native_window.get("hwnd") != self.hwnd
            or native_window.get("title") != self.expected_title
            or native_window.get("class_name") != before["identity"]["class_name"]
            or native_window.get("is_visible") is not True
            or native_window.get("is_minimized") is not False
            or native_window.get("client_width") != POSE_FRAME_WIDTH
            or native_window.get("client_height") != POSE_FRAME_HEIGHT
            or native_dpi != before["dpi"]
            or mining_frame.width != POSE_FRAME_WIDTH
            or mining_frame.height != POSE_FRAME_HEIGHT
        ):
            raise RuntimeError("native_authority_window_or_geometry_mismatch")
        payload_sha256 = hashlib.sha256(mining_frame.payload).hexdigest()
        epoch = PerceptionEpoch(
            capture_source_id="windows-runelite-native",
            capture_session_id=f"route-start-connector:{self.hwnd}",
            cycle_id=f"route-start-connector:{self.hwnd}:{mining_frame.frame_id}",
            cycle_sequence=mining_frame.frame_id,
            frame_id=mining_frame.frame_id,
            captured_monotonic_s=mining_frame.captured_monotonic_s,
            frame_width=mining_frame.width,
            frame_height=mining_frame.height,
            frame_payload_sha256=payload_sha256,
            pixel_format="bgra8888",
        )
        resource, pose, _ = resource_evaluator(
            mining_frame,
            epoch,
            detectors,
            frozenset(),
            {"pose": None, "detector": None},
        )
        _, inventory = inventory_evaluator.evaluate(mining_frame, epoch)
        evaluated = self.now()
        state = assemble_atomic_mining_world_state(
            resource=resource,
            inventory=inventory,
            evaluated_monotonic_s=evaluated,
        )
        after = self.guard()
        if after != before or frame.frame_id != self.frame_id:
            raise RuntimeError("window_changed_during_start_connector_authority")
        inventory_state = inventory.inventory
        accepted = (
            pose == expected_pose
            and resource.view is ResourceViewState.SUPPORTED
            and inventory.unknown_reason is None
            and inventory_state.occupied_slots == INVENTORY_CAPACITY
            and state.status is WorldStatePublicationStatus.FULL
        )
        if pose != expected_pose:
            reason = "expected_mining_pose_not_supported"
        elif resource.view is not ResourceViewState.SUPPORTED:
            reason = "resource_view_not_supported"
        elif inventory.unknown_reason is not None:
            reason = "inventory_unknown"
        elif inventory_state.occupied_slots != INVENTORY_CAPACITY:
            reason = "inventory_not_full"
        elif state.status is not WorldStatePublicationStatus.FULL:
            reason = state.stop_reason.value
        else:
            reason = "accepted"
        return {
            "accepted": accepted,
            "reason": reason,
            "route_source_frame_id": frame.frame_id,
            "route_source_captured_monotonic_s": frame.captured_monotonic_s,
            "native_frame_id": mining_frame.frame_id,
            "native_captured_monotonic_s": mining_frame.captured_monotonic_s,
            "native_frame_payload_sha256": payload_sha256,
            "native_frame_path": native_path,
            "native_window": native_window,
            "native_dpi": native_dpi,
            "window": before,
            "expected_pose_id": expected_pose,
            "pose_id": pose,
            "resource_view": resource.view.value,
            "inventory_occupied_slots": inventory_state.occupied_slots,
            "inventory_capacity": inventory_state.capacity,
            "inventory_confidence": inventory_state.confidence,
            "inventory_unknown_reason": inventory.unknown_reason,
            "world_state": state.status.value,
            "world_state_stop_reason": state.stop_reason.value,
            "perception_age_s": evaluated - mining_frame.captured_monotonic_s,
        }

    def _capture_start_connector_native_frame(
        self,
    ) -> tuple[Any, dict[str, Any], int | None, str]:
        """Capture unscaled BGRA pixels through the existing client backend."""

        from ..capture import CaptureSource
        from ..capture.windows import WindowsCaptureBackend

        backend = WindowsCaptureBackend(title_substring=self.expected_title)
        source = CaptureSource(backend, max_consecutive_failures=1)
        source.open()
        try:
            native_frame = source.capture()
            selected = backend.selected_window
            native_dpi = backend.current_dpi
            if selected is None:
                raise RuntimeError("native_authority_window_not_selected")
            native_window = asdict(selected)
        finally:
            source.close()
        path = self.output / (f"{self.frame_id:05d}-start-connector-native-authority.bgra")
        path.write_bytes(native_frame.payload)
        return native_frame, native_window, native_dpi, str(path)

    def click(self, frame: RouteFrame, point: tuple[int, int], geometry: Any) -> None:
        before = self.guard()
        if frame.window != before or frame.frame_id != self.frame_id:
            raise RuntimeError("stale_native_input_proposal")
        age = self.now() - frame.captured_monotonic_s
        if not math.isfinite(age) or age < 0 or age > self.max_frame_age_s:
            raise RuntimeError("input_frame_expired")
        if not geometry.contains(point):
            raise RuntimeError("outside_minimap")
        x, y = point
        width, height = before["client_size"]
        if not 0 <= x < width or not 0 <= y < height:
            raise RuntimeError("point_outside_client")
        # These pixels are already physical; do NOT apply the logical-client 1.25 mapping again.
        ox, oy = before["client_origin"]
        screen = (ox + x, oy + y)
        reverse = self.api.physical_screen_to_physical_client(self.hwnd, *screen)
        # Windows rounds this unaware-AWT boundary by about one physical pixel.
        # The capture and click share the same measured physical origin; permit
        # only that measured +/-2 px conversion noise, never a scale mismatch.
        if abs(reverse[0] - x) > 2 or abs(reverse[1] - y) > 2:
            raise RuntimeError("physical_coordinate_round_trip_failed")
        if self.api.root_window_at_point(*screen) != self.hwnd or self.api.left_button_is_down():
            raise RuntimeError("target_occluded_or_human_mouse_down")
        if not self.api.move_cursor(*screen) or self.api.cursor_position() != screen:
            raise RuntimeError("cursor_delivery_failed")
        self.guard()
        if self.api.root_window_at_point(*screen) != self.hwnd:
            raise RuntimeError("target_became_occluded")
        # Preserve actual input evidence even if cancellation/window drift follows.
        down = up = 0
        receipt = {
            "frame_id": frame.frame_id,
            "client_point": point,
            "screen_point": screen,
            "phase": "intent",
            "down": 0,
            "up": 0,
        }
        with (self.output / "clicks.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(receipt) + "\n")
        try:
            down = self.api.send_mouse_button(button_up=False)
            if down != 1:
                raise RuntimeError("mouse_down_not_delivered")
            self.delivered_click_count = getattr(self, "delivered_click_count", 0) + 1
            self.wait(0.05)
        finally:
            for _ in range(3):
                up = self.api.send_mouse_button(button_up=True)
                if up == 1 and not self.api.left_button_is_down():
                    break
            receipt.update(
                {
                    "phase": "input_result",
                    "down": down,
                    "up": up,
                    "button_released": not self.api.left_button_is_down(),
                    "post_input_window_guard": "not_yet_checked",
                }
            )
            self.last_dispatch_receipt = receipt.copy()
            with (self.output / "clicks.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(receipt) + "\n")
        if down != 1 or up != 1 or self.api.left_button_is_down():
            raise RuntimeError("mouse_release_unconfirmed")
        self.guard()
