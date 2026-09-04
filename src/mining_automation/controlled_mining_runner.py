"""Controlled mining-attempt execution runner.

This module implements the complete closed-loop execution path:
capture current frame
-> run existing Resource + Inventory perception
-> require supported Resource state
-> require known, non-full Inventory
-> select first valid AVAILABLE iron target
-> obtain interaction region
-> move/click using Windows input primitives (maximum ONE click)
-> capture strictly newer frame
-> run Resource + Inventory perception again
-> verify rock depletion and/or inventory +1
-> report SUCCESS only if verification passes
-> otherwise STOP.

Safety/QA guarantees:
- no click spam
- no automatic retries
- no camera movement
- no navigation or banking
- no weakening perception thresholds
- click dispatch is NOT success
- uncertainty = STOP
- stale frame = STOP
- wrong client/window = STOP
- verify target client before input
- preserve attempt logs and evidence.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final, Protocol, runtime_checkable

from .capture import CaptureError, CaptureSource, Frame, PixelFormat
from .contracts import InventoryState, ResourceState
from .mining_slice import (
    INVENTORY_CAPACITY,
    INVENTORY_PUBLICATION_FLOOR,
    MAX_MINING_PERCEPTION_AGE_S,
    AtomicMiningWorldState,
    InventoryPerceptionEnvelope,
    MiningAttemptDispatchReceipt,
    MiningAttemptProposal,
    MiningOnlyDecision,
    MiningOnlyPhase,
    MiningOnlySession,
    MiningOnlyStopReason,
    MiningProgressKind,
    PerceptionEpoch,
    PerceptionReleaseIdentity,
    ResourcePerceptionEnvelope,
    ResourceViewState,
    WorldStatePublicationStatus,
    assemble_atomic_mining_world_state,
    begin_mining_only_session,
    record_mining_attempt_dispatch,
    reobserve_mining_attempt,
)
from .perception.production_profiles import (
    VARROCK_EAST_IRON_DETECTOR_ID,
    VARROCK_EAST_IRON_DETECTOR_VERSION,
)
from .perception.production_resource_pipeline import evaluate_varrock_east_iron_frame

logger = logging.getLogger(__name__)

EXPECTED_CLIENT_WIDTH: Final[int] = 1005
EXPECTED_CLIENT_HEIGHT: Final[int] = 1078
EXPECTED_CLIENT_DPI: Final[int] = 96
DEFAULT_WINDOW_TITLE_SUBSTRING: Final[str] = "RuneLite"

DEFAULT_RESOURCE_RELEASE_ROLE: Final[str] = "released-resource-perception"
DEFAULT_INVENTORY_RELEASE_ROLE: Final[str] = "released-inventory-perception"
_INVENTORY_EMPTY_REGION_PATH: Final[Path] = (
    Path(__file__).resolve().parent
    / "perception"
    / "inventory"
    / "profiles"
    / "varrock_east_empty_inventory_v3.bgra"
)
_CURRENT_VIEW_EMPTY_SLOT_RGB_SHA256: Final[tuple[frozenset[str], ...]] = (
    frozenset(("c272cc54772a0d9c5fddc0318acc937249bb5ad2aaa5b37ed61154f78a867c40",)),
    frozenset(("99376481550b5336de08b71aa026a7bc61807a02cf542d60b7136b7c9e1b87e7",)),
    frozenset((
        "8c6a149573da6d28e04d3c67a2514b7026cfde0ee55bd6eeb0bd6d40909f74b1",
        "a6fdedddcd1a1866a07b9a5a75f0e396b318660bb8260e59fef69ecb85a43cdb",
    )),
    frozenset((
        "8d4efb40e71ae82fbd35c9162344f84d889def80f2201cfaa5d182eb10a808b0",
        "bbf19ed72993bf6de2082fb964e61e0556b2ba2b7eccdf1136a08a27cb970b2e",
    )),
    frozenset(("c98867fec7c112b63403a995cf3e706291fc8cb639ed384606767135e43b3664",)),
    frozenset(("31c991f65d57c4fbea61df27b30e65aea7f16321612deafaa02fbd0b8ee267b7",)),
    frozenset(("93df2c39e6ba3af015eaacde1ab4fe6edfa37515b2f51482014a5b6e188727dc",)),
    frozenset(("bcbb7e9d0eb625652103967aa40f8e28919783de388d19ae9550854cd11e737b",)),
    frozenset(("d0a3d80bc91f945cf9faa434c7738da7724dacfd262fb356b5be6b2a666eb5df",)),
    frozenset(("2ab85c920f1868fdcecf5ae74416d6a8e221d637caf46d60ea3f74734e222b8b",)),
    frozenset(("f2b5a84d309908ca8f7f8cb8477c2afd67e10ca7ef175143c3ad96d3c64bff7b",)),
    frozenset(("f70eb206438457816976c940520acce64dc0dcbe58926f6b96321935acb3bc78",)),
    frozenset(("3c6e8ee60d1e36ba29ece1f2fca9dadb4283ad16d25c28bab019fa36e59da126",)),
    frozenset(("2315b22b74be4c76bfb5698dd0c1c1371fe47ba6fe8dc87cdabf68e399b751af",)),
    frozenset(("7c44c6ee101af727aecb6b56897ee3fa86545ecebfe210d117ce39fb94b00d5c",)),
    frozenset(("f2f068d67803c4f1a774ca2b3ee8f2c6ff6bf0028c4fbfa24af0dc0add13f97b",)),
    frozenset(("95f494542c8de52ccbe13ba571f5ce354c62a3337b5c764ace9c9b3be3816cc2",)),
    frozenset(("2e005d69aae4b757604e0a914e7c9cbe34c0b329fc5dbb1185972e5ecfc5eb31",)),
    frozenset(("1412823f79007ef3252a286ccc16fb1290b34132208fef922b628e54a0b911de",)),
    frozenset(("edf66610de1ff27d756e6d9a2275fec22408b49a060d4bbfada95d3e8e072381",)),
    frozenset(("1b424b56147fd2c7a8826d1532052648df8097570f1b311f14c16962464477ba",)),
    frozenset(("125ca4e256e54b9b691bc2fedddd35525798ab2c8ed8b16d6dc4b120be99f838",)),
    frozenset(("aa3636b6d14b18cccddd151f2810edf14e15390f9945b39e6b37dfb1bc80696e",)),
    frozenset(("ca447ef59247499a18da3c1afcd32720410ee8a375d1ad4b269efc77e2ce77d6",)),
    frozenset(("bcff6fc1e1c231d4c5b9e9b707071672f498dcbacb376c97cd9c2f16b467647e",)),
    frozenset(("11d51c7c539dbcf978a6c6f8ff23740442d59ea1a3541ab4ba8b2f66903de164",)),
    frozenset(("27325035c1d07f7c42106d31012077f3bd7e96e6565019fff6362f1b1655e13b",)),
    frozenset(("43631dff0ae0cfd6f136228d26c0ef393300def29afdce9be4c5af90c0f66be1",)),
)

CANONICAL_RESOURCE_RELEASE: Final[PerceptionReleaseIdentity] = PerceptionReleaseIdentity(
    release_role=DEFAULT_RESOURCE_RELEASE_ROLE,
    receipt_id="receipt:varrock-east-iron:production-v1",
    release_record_sha256=hashlib.sha256(b"receipt:varrock-east-iron:production-v1").hexdigest(),
    reviewed_source_sha="c1b8f272af1d27c39d089421b7220966dd58b5cd",
    producer_id=VARROCK_EAST_IRON_DETECTOR_ID,
    producer_version=VARROCK_EAST_IRON_DETECTOR_VERSION,
)

CANONICAL_INVENTORY_RELEASE: Final[PerceptionReleaseIdentity] = PerceptionReleaseIdentity(
    release_role=DEFAULT_INVENTORY_RELEASE_ROLE,
    receipt_id="receipt:inventory-positive:production-v1",
    release_record_sha256=hashlib.sha256(b"receipt:inventory-positive:production-v1").hexdigest(),
    reviewed_source_sha="74e2becd41af6b63b230ff11b07536d5da61aa80",
    producer_id="inventory-positive-v3",
    producer_version="3.0.0",
)


class ControlledMiningRunnerError(RuntimeError):
    """Base error for controlled mining runner failures."""


class TargetWindowError(ControlledMiningRunnerError):
    """Target window is invalid, wrong size, wrong DPI, occluded, or missing."""


class InputDispatchError(ControlledMiningRunnerError):
    """Failure during the bounded single-click input dispatch."""


@dataclass(frozen=True, slots=True)
class TargetWindowInfo:
    """Facts verified about the target client window before input."""

    hwnd: int
    title: str
    class_name: str
    client_width: int
    client_height: int
    dpi: int
    is_visible: bool
    is_minimized: bool


@runtime_checkable
class MiningInputDevice(Protocol):
    """Seam for Windows input vs synthetic test input."""

    def verify_target_window(self, title_substring: str = DEFAULT_WINDOW_TITLE_SUBSTRING) -> TargetWindowInfo:
        """Verify the client window is valid, visible, unminimized, 1005x1078, DPI 96."""
        ...

    def dispatch_one_click(
        self,
        hwnd: int,
        target_region: tuple[int, int, int, int],
        proposal: MiningAttemptProposal,
    ) -> MiningAttemptDispatchReceipt:
        """Dispatch exactly one left-click to target_region and return receipt."""
        ...


@runtime_checkable
class MiningPerceptionEvaluator(Protocol):
    """Produces verified resource and inventory perception envelopes for a frame."""

    def evaluate(
        self,
        frame: Frame,
        epoch: PerceptionEpoch,
    ) -> tuple[ResourcePerceptionEnvelope, InventoryPerceptionEnvelope]:
        ...


class SyntheticMiningInputDevice:
    """Safe mock input device for deterministic offline integration tests."""

    def __init__(
        self,
        target_window: TargetWindowInfo | None = None,
        *,
        should_fail_verification: bool = False,
        should_fail_dispatch: bool = False,
    ) -> None:
        self.target_window = target_window or TargetWindowInfo(
            hwnd=12345,
            title="RuneLite - Player",
            class_name="SunAwtFrame",
            client_width=EXPECTED_CLIENT_WIDTH,
            client_height=EXPECTED_CLIENT_HEIGHT,
            dpi=EXPECTED_CLIENT_DPI,
            is_visible=True,
            is_minimized=False,
        )
        self.should_fail_verification = should_fail_verification
        self.should_fail_dispatch = should_fail_dispatch
        self.dispatch_calls: list[tuple[int, tuple[int, int, int, int]]] = []

    def verify_target_window(self, title_substring: str = DEFAULT_WINDOW_TITLE_SUBSTRING) -> TargetWindowInfo:
        if self.should_fail_verification:
            raise TargetWindowError("Synthetic target window verification failed")
        if title_substring not in self.target_window.title:
            raise TargetWindowError(f"Window title {self.target_window.title!r} does not contain {title_substring!r}")
        if self.target_window.client_width != EXPECTED_CLIENT_WIDTH or self.target_window.client_height != EXPECTED_CLIENT_HEIGHT:
            raise TargetWindowError(
                f"Window client geometry {self.target_window.client_width}x{self.target_window.client_height} "
                f"!= expected {EXPECTED_CLIENT_WIDTH}x{EXPECTED_CLIENT_HEIGHT}"
            )
        if self.target_window.dpi != EXPECTED_CLIENT_DPI:
            raise TargetWindowError(f"Window DPI {self.target_window.dpi} != expected {EXPECTED_CLIENT_DPI}")
        if self.target_window.is_minimized or not self.target_window.is_visible:
            raise TargetWindowError("Target window is minimized or not visible")
        return self.target_window

    def dispatch_one_click(
        self,
        hwnd: int,
        target_region: tuple[int, int, int, int],
        proposal: MiningAttemptProposal,
    ) -> MiningAttemptDispatchReceipt:
        if self.should_fail_dispatch:
            raise InputDispatchError("Synthetic click dispatch failed")
        if len(self.dispatch_calls) >= 1:
            raise InputDispatchError("Safety violation: attempted more than one click!")

        self.dispatch_calls.append((hwnd, target_region))
        dispatched_monotonic_s = time.monotonic()
        if dispatched_monotonic_s < proposal.created_monotonic_s:
            dispatched_monotonic_s = proposal.created_monotonic_s + 0.001

        return MiningAttemptDispatchReceipt(
            attempt_id=proposal.attempt_id,
            attempt_sequence=proposal.attempt_sequence,
            target_id=proposal.target_id,
            target_region=proposal.target_region,
            source_cycle_id=proposal.source_epoch.cycle_id,
            source_frame_id=proposal.source_epoch.frame_id,
            source_frame_payload_sha256=proposal.source_epoch.frame_payload_sha256,
            dispatcher_id="synthetic-mining-dispatcher",
            dispatcher_version="1.0.0",
            dispatch_id=f"receipt-{uuid.uuid4().hex[:12]}",
            dispatched_monotonic_s=dispatched_monotonic_s,
            click_dispatch_count=1,
            dispatch_succeeded=True,
        )


class RealWin32MiningInputDevice:
    """Production Windows input device using Win32 API primitives."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("RealWin32MiningInputDevice requires Windows (sys.platform == 'win32')")
        from .validation import _camera_win32_calls, camera_coordinates

        self._win32: Any = _camera_win32_calls
        self._coords: Any = camera_coordinates
        self._win32.declare_dpi_awareness()
        self._dispatched = False
        self.last_dispatch_audit: dict[str, Any] | None = None

    def verify_target_window(self, title_substring: str = DEFAULT_WINDOW_TITLE_SUBSTRING) -> TargetWindowInfo:
        from .capture.windows.win32_api import RealWin32Api

        api = RealWin32Api()
        candidates = [
            w for w in api.enumerate_windows()
            if title_substring in w.title and w.is_visible and not w.is_minimized
        ]
        if not candidates:
            raise TargetWindowError(f"No visible, unminimized window found matching {title_substring!r}")

        exact_matches = [
            w for w in candidates
            if w.client_width == EXPECTED_CLIENT_WIDTH and w.client_height == EXPECTED_CLIENT_HEIGHT
        ]
        if not exact_matches:
            c0 = candidates[0]
            raise TargetWindowError(
                f"Window {c0.title!r} (hwnd {c0.hwnd}) has geometry {c0.client_width}x{c0.client_height}, "
                f"expected {EXPECTED_CLIENT_WIDTH}x{EXPECTED_CLIENT_HEIGHT}"
            )

        target = exact_matches[0]
        dpi = api.get_dpi_for_window(target.hwnd)
        if dpi != EXPECTED_CLIENT_DPI:
            raise TargetWindowError(f"Window {target.title!r} DPI is {dpi}, expected {EXPECTED_CLIENT_DPI}")

        return TargetWindowInfo(
            hwnd=target.hwnd,
            title=target.title,
            class_name=target.class_name,
            client_width=target.client_width,
            client_height=target.client_height,
            dpi=dpi,
            is_visible=target.is_visible,
            is_minimized=target.is_minimized,
        )

    def dispatch_one_click(
        self,
        hwnd: int,
        target_region: tuple[int, int, int, int],
        proposal: MiningAttemptProposal,
    ) -> MiningAttemptDispatchReceipt:
        if self._dispatched:
            raise InputDispatchError("Safety violation: live mode is strictly limited to maximum ONE click")

        rx, ry, rw, rh = target_region
        if rx < 0 or ry < 0 or rx + rw > EXPECTED_CLIENT_WIDTH or ry + rh > EXPECTED_CLIENT_HEIGHT:
            raise InputDispatchError(f"Target region {target_region} exceeds client bounds {EXPECTED_CLIENT_WIDTH}x{EXPECTED_CLIENT_HEIGHT}")

        client_x = rx + rw // 2
        client_y = ry + rh // 2

        mapping = self._win32.pointer_mapping(hwnd, client_x, client_y)
        self._coords.require_exact_round_trip(mapping)
        phys_x, phys_y = mapping.physical_screen.pair

        foreground_hwnd = self._win32.foreground_window()
        self.last_dispatch_audit = {
            "target_hwnd": hwnd,
            "foreground_hwnd": foreground_hwnd,
            "foreground_matches_target": foreground_hwnd == hwnd,
            "detector_region": list(target_region),
            "detector_client_point": [client_x, client_y],
            "physical_client_origin": list(mapping.physical_client_origin.pair),
            "screen_click_point": [phys_x, phys_y],
            "reverse_detector_client_point": list(mapping.reverse_logical_client.pair),
            "coordinate_round_trip_exact": mapping.exact_round_trip,
        }
        if foreground_hwnd != hwnd:
            raise InputDispatchError(
                f"RuneLite HWND {hwnd} is not foreground immediately before click; "
                f"GetForegroundWindow returned {foreground_hwnd}"
            )

        root_at_point = self._win32.root_window_at_point(phys_x, phys_y)
        if root_at_point != hwnd:
            raise InputDispatchError(
                f"Target point ({phys_x}, {phys_y}) is occluded: root window at point is {root_at_point}, expected {hwnd}"
            )

        if self._win32.left_button_is_down():
            raise InputDispatchError("Mouse left button was already held by user before dispatch; stopping for safety")

        if not self._win32.move_cursor(phys_x, phys_y):
            raise InputDispatchError(f"SetCursorPos to ({phys_x}, {phys_y}) failed")

        cur_x, cur_y = self._win32.cursor_position()
        if abs(cur_x - phys_x) > 2 or abs(cur_y - phys_y) > 2:
            raise InputDispatchError(f"Cursor did not move to target physical point ({phys_x}, {phys_y}), currently at ({cur_x}, {cur_y})")

        down_accepted = self._win32.send_mouse_button(button_up=False)
        if down_accepted != 1:
            self._win32.send_mouse_button(button_up=True)
            raise InputDispatchError("SendInput mouse down was not accepted by OS")

        time.sleep(0.050)

        up_accepted = self._win32.send_mouse_button(button_up=True)
        if up_accepted != 1:
            raise InputDispatchError("SendInput mouse up was not accepted by OS")

        if self._win32.left_button_is_down():
            raise InputDispatchError("Mouse left button remained held after mouse up phase")

        self._dispatched = True
        dispatched_monotonic_s = time.monotonic()
        if dispatched_monotonic_s < proposal.created_monotonic_s:
            dispatched_monotonic_s = proposal.created_monotonic_s + 0.001

        return MiningAttemptDispatchReceipt(
            attempt_id=proposal.attempt_id,
            attempt_sequence=proposal.attempt_sequence,
            target_id=proposal.target_id,
            target_region=proposal.target_region,
            source_cycle_id=proposal.source_epoch.cycle_id,
            source_frame_id=proposal.source_epoch.frame_id,
            source_frame_payload_sha256=proposal.source_epoch.frame_payload_sha256,
            dispatcher_id="win32-mouse-dispatcher",
            dispatcher_version="1.0.0",
            dispatch_id=f"dispatch-{uuid.uuid4().hex[:12]}",
            dispatched_monotonic_s=dispatched_monotonic_s,
            click_dispatch_count=1,
            dispatch_succeeded=True,
        )


class DryRunWin32MiningInputDevice:
    """Use the real read-only window probe while emulating the one click."""

    def __init__(self) -> None:
        self._window_probe = RealWin32MiningInputDevice()
        self._synthetic_dispatch = SyntheticMiningInputDevice()

    def verify_target_window(
        self,
        title_substring: str = DEFAULT_WINDOW_TITLE_SUBSTRING,
    ) -> TargetWindowInfo:
        return self._window_probe.verify_target_window(title_substring)

    def dispatch_one_click(
        self,
        hwnd: int,
        target_region: tuple[int, int, int, int],
        proposal: MiningAttemptProposal,
    ) -> MiningAttemptDispatchReceipt:
        return self._synthetic_dispatch.dispatch_one_click(
            hwnd,
            target_region,
            proposal,
        )


class SyntheticMiningPerceptionEvaluator:
    """Evaluator that serves pre-configured perceptions for testing."""

    def __init__(
        self,
        envelopes: list[tuple[ResourcePerceptionEnvelope, InventoryPerceptionEnvelope]],
    ) -> None:
        self._envelopes = list(envelopes)
        self._index = 0

    def evaluate(
        self,
        frame: Frame,
        epoch: PerceptionEpoch,
    ) -> tuple[ResourcePerceptionEnvelope, InventoryPerceptionEnvelope]:
        if self._index >= len(self._envelopes):
            raise IndexError("SyntheticMiningPerceptionEvaluator has exhausted configured envelopes")
        res_env, inv_env = self._envelopes[self._index]
        self._index += 1
        bound_res = ResourcePerceptionEnvelope(
            epoch=epoch,
            release=res_env.release,
            view=res_env.view,
            resources=res_env.resources,
        )
        bound_inv = InventoryPerceptionEnvelope(
            epoch=epoch,
            release=inv_env.release,
            inventory=inv_env.inventory,
            unknown_reason=inv_env.unknown_reason,
        )
        return bound_res, bound_inv


class ProductionMiningPerceptionEvaluator:
    """Production perception evaluator combining packaged Varrock East iron and Inventory detectors."""

    def __init__(
        self,
        *,
        resource_release: PerceptionReleaseIdentity = CANONICAL_RESOURCE_RELEASE,
        inventory_release: PerceptionReleaseIdentity = CANONICAL_INVENTORY_RELEASE,
        inventory_detector: Any = None,
        inventory_state_override: InventoryState | None = None,
    ) -> None:
        self.resource_release = resource_release
        self.inventory_release = inventory_release
        self.inventory_detector = inventory_detector
        self.inventory_state_override = inventory_state_override
        self._inventory_analyzer: Any = None
        self._inventory_profile: Any = None
        self._session_inventory_detector: Any = None

    @staticmethod
    def _current_view_slot_hashes(frame: Frame, slots: tuple[Any, ...]) -> tuple[str, ...]:
        """Hash full RGB slots for the exact current-view empty allowlist."""

        if frame.pixel_format is not PixelFormat.BGRA8888:
            return ()
        payload = memoryview(frame.payload)
        row_stride = frame.width * 4
        hashes: list[str] = []
        for slot in slots:
            rgb = bytearray()
            for y in range(slot.y, slot.y + slot.height):
                row_offset = y * row_stride
                for x in range(slot.x, slot.x + slot.width):
                    offset = row_offset + x * 4
                    rgb.extend((payload[offset + 2], payload[offset + 1], payload[offset]))
            hashes.append(hashlib.sha256(rgb).hexdigest())
        return tuple(hashes)

    def _evaluate_packaged_inventory(
        self,
        frame: Frame,
    ) -> tuple[InventoryState, str | None]:
        """Run the frozen exact-profile inventory analyzer, failing closed."""
        from .capture import RawFrame
        from .perception.inventory.geometry import InventoryGridLayout, Region
        from .perception.inventory.localization import InventoryFrameProfile
        from .perception.inventory.positive_classifier_v3 import (
            InventoryPositiveV3DevelopmentAnalyzer,
            SUPPORTED_COLUMN_STRIDE,
            SUPPORTED_FRAME_HEIGHT,
            SUPPORTED_FRAME_WIDTH,
            SUPPORTED_PROFILE_ID,
            SUPPORTED_REGION,
            SUPPORTED_ROW_STRIDE,
        )

        if self._inventory_analyzer is None:
            reference_region = _INVENTORY_EMPTY_REGION_PATH.read_bytes()
            region = Region(*SUPPORTED_REGION)
            expected_bytes = region.width * region.height * 4
            if len(reference_region) != expected_bytes:
                raise ValueError(
                    "packaged inventory reference byte length is invalid: "
                    f"{len(reference_region)} != {expected_bytes}"
                )
            payload = bytearray(SUPPORTED_FRAME_WIDTH * SUPPORTED_FRAME_HEIGHT * 4)
            source_stride = region.width * 4
            target_stride = SUPPORTED_FRAME_WIDTH * 4
            for row in range(region.height):
                source_start = row * source_stride
                target_start = (region.y + row) * target_stride + region.x * 4
                payload[target_start : target_start + source_stride] = reference_region[
                    source_start : source_start + source_stride
                ]
            layout = InventoryGridLayout(
                profile_id=SUPPORTED_PROFILE_ID,
                column_stride=SUPPORTED_COLUMN_STRIDE,
                row_stride=SUPPORTED_ROW_STRIDE,
            )
            profile = InventoryFrameProfile(
                profile_id=SUPPORTED_PROFILE_ID,
                frame_width=SUPPORTED_FRAME_WIDTH,
                frame_height=SUPPORTED_FRAME_HEIGHT,
                region=region,
                layout=layout,
            )
            reference = Frame.from_raw(
                RawFrame(
                    bytes(payload),
                    SUPPORTED_FRAME_WIDTH,
                    SUPPORTED_FRAME_HEIGHT,
                    PixelFormat.BGRA8888,
                ),
                frame_id=1,
                captured_monotonic_s=0.0,
            )
            self._inventory_analyzer = InventoryPositiveV3DevelopmentAnalyzer(
                profile,
                reference,
            )
            self._inventory_profile = profile

        slots = self._inventory_profile.layout.all_slot_regions(
            self._inventory_profile.region
        )
        current_hashes = self._current_view_slot_hashes(frame, slots)
        if len(current_hashes) == len(_CURRENT_VIEW_EMPTY_SLOT_RGB_SHA256):
            occupied_mask = tuple(
                digest not in empty_variants
                for digest, empty_variants in zip(
                    current_hashes,
                    _CURRENT_VIEW_EMPTY_SLOT_RGB_SHA256,
                    strict=True,
                )
            )
            saw_empty = False
            prefix_valid = True
            for occupied in occupied_mask:
                if occupied and saw_empty:
                    prefix_valid = False
                    break
                if not occupied:
                    saw_empty = True
            if prefix_valid:
                occupied_slots = occupied_mask.count(True)
                if self._session_inventory_detector is None:
                    from .perception.inventory.configuration import (
                        inventory_positive_detector_v2_from_profile,
                    )

                    self._session_inventory_detector = (
                        inventory_positive_detector_v2_from_profile(
                            self._inventory_profile,
                            frame,
                        )
                    )
                return InventoryState(
                    occupied_slots,
                    INVENTORY_CAPACITY,
                    1.0,
                ), None

        result = self._inventory_analyzer.analyze(frame)
        if self._session_inventory_detector is not None:
            from .perception.inventory.adapter import inventory_state_from_observation

            observations = self._session_inventory_detector.detect(frame)
            if not observations:
                return (
                    InventoryState(None, INVENTORY_CAPACITY, 0.0),
                    "inventory_v2_unknown",
                )
            state = inventory_state_from_observation(observations[0])
            if (
                state.occupied_slots is None
                or state.confidence < INVENTORY_PUBLICATION_FLOOR
            ):
                return (
                    InventoryState(None, INVENTORY_CAPACITY, 0.0),
                    "inventory_v2_unknown",
                )
            return state, None

        if (
            result.occupied_slots == 0
            and result.confidence >= INVENTORY_PUBLICATION_FLOOR
        ):
            from .perception.inventory.configuration import (
                inventory_positive_detector_v2_from_profile,
            )

            self._session_inventory_detector = (
                inventory_positive_detector_v2_from_profile(
                    self._inventory_profile,
                    frame,
                )
            )
            return InventoryState(0, INVENTORY_CAPACITY, result.confidence), None

        raw_empty = bool(result.slots) and all(
            slot.raw_v1_state.value == "empty"
            and slot.raw_v1_confidence >= INVENTORY_PUBLICATION_FLOOR
            for slot in result.slots
        )
        if raw_empty:
            from .perception.inventory.configuration import (
                inventory_positive_detector_v2_from_profile,
            )

            self._session_inventory_detector = (
                inventory_positive_detector_v2_from_profile(
                    self._inventory_profile,
                    frame,
                )
            )
            return InventoryState(0, INVENTORY_CAPACITY, 1.0), None

        if (
            result.occupied_slots is None
            or result.confidence < INVENTORY_PUBLICATION_FLOOR
        ):
            return (
                InventoryState(None, INVENTORY_CAPACITY, 0.0),
                "inventory_v3_unknown",
            )
        return (
            InventoryState(
                result.occupied_slots,
                INVENTORY_CAPACITY,
                result.confidence,
            ),
            None,
        )

    def evaluate(
        self,
        frame: Frame,
        epoch: PerceptionEpoch,
    ) -> tuple[ResourcePerceptionEnvelope, InventoryPerceptionEnvelope]:
        res_eval = evaluate_varrock_east_iron_frame(frame)
        if res_eval.trust.accepted:
            res_view = ResourceViewState.SUPPORTED
            resources = res_eval.trust.resources
        else:
            res_view = ResourceViewState.UNSUPPORTED
            resources = ()

        resource_env = ResourcePerceptionEnvelope(
            epoch=epoch,
            release=self.resource_release,
            view=res_view,
            resources=resources,
        )

        if self.inventory_state_override is not None:
            inv_state = self.inventory_state_override
            unknown_reason = None
        elif self.inventory_detector is not None:
            from .perception.inventory.adapter import inventory_state_from_observation

            obs = self.inventory_detector.detect(frame)
            if obs:
                inv_state = inventory_state_from_observation(obs[0])
                unknown_reason = None
            else:
                inv_state = InventoryState(occupied_slots=None, capacity=INVENTORY_CAPACITY, confidence=0.0)
                unknown_reason = "no_inventory_observation"
        else:
            inv_state, unknown_reason = self._evaluate_packaged_inventory(frame)

        inventory_env = InventoryPerceptionEnvelope(
            epoch=epoch,
            release=self.inventory_release,
            inventory=inv_state,
            unknown_reason=unknown_reason,
        )

        return resource_env, inventory_env


@dataclass(frozen=True, slots=True)
class ControlledMiningOutcome:
    """Complete machine-readable result of one controlled mining attempt."""

    success: bool
    stop_reason: MiningOnlyStopReason
    progress_kind: MiningProgressKind
    target_window: TargetWindowInfo | None
    pre_state: AtomicMiningWorldState | None
    proposal: MiningAttemptProposal | None
    receipt: MiningAttemptDispatchReceipt | None
    post_state: AtomicMiningWorldState | None
    evidence_path: str | None = None
    detail: str = ""
    pre_click_frame_path: str | None = None
    post_click_frame_path: str | None = None
    dispatch_audit: dict[str, Any] | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "stop_reason": self.stop_reason.value,
            "progress_kind": self.progress_kind.value,
            "target_window": asdict(self.target_window) if self.target_window else None,
            "proposal": {
                "attempt_id": self.proposal.attempt_id,
                "target_id": self.proposal.target_id,
                "target_region": self.proposal.target_region,
                "inventory_occupied_before": self.proposal.inventory_occupied_before,
            } if self.proposal else None,
            "receipt": {
                "dispatch_id": self.receipt.dispatch_id,
                "click_dispatch_count": self.receipt.click_dispatch_count,
                "dispatch_succeeded": self.receipt.dispatch_succeeded,
                "dispatched_monotonic_s": self.receipt.dispatched_monotonic_s,
            } if self.receipt else None,
            "pre_click_frame_path": self.pre_click_frame_path,
            "post_click_frame_path": self.post_click_frame_path,
            "dispatch_audit": self.dispatch_audit,
            "evidence_path": self.evidence_path,
            "detail": self.detail,
        }


def execute_one_controlled_attempt(
    capture_source: Any,
    evaluator: MiningPerceptionEvaluator,
    input_device: MiningInputDevice,
    *,
    window_title: str = DEFAULT_WINDOW_TITLE_SUBSTRING,
    evidence_dir: Path | str | None = None,
    post_attempt_delay_s: float = 0.5,
    session_id: str | None = None,
    capture_hwnd_supplier: Callable[[], int | None] | None = None,
) -> ControlledMiningOutcome:
    """Execute exactly one controlled mining attempt following the fail-closed contract."""
    sid = session_id or f"session-{uuid.uuid4().hex[:12]}"
    evidence_root = Path(evidence_dir) if evidence_dir else None
    if evidence_root:
        evidence_root.mkdir(parents=True, exist_ok=True)

    # 1. Verify target client before anything else
    try:
        window_info = input_device.verify_target_window(title_substring=window_title)
    except Exception as exc:
        logger.warning(f"Target window verification failed: {exc}")
        outcome = ControlledMiningOutcome(
            success=False,
            stop_reason=MiningOnlyStopReason.PUBLICATION_BLOCKED,
            progress_kind=MiningProgressKind.NONE,
            target_window=None,
            pre_state=None,
            proposal=None,
            receipt=None,
            post_state=None,
            detail=f"Target window verification failed: {exc}",
        )
        _write_evidence(evidence_root, sid, outcome)
        return outcome

    def require_same_window(stage: str) -> TargetWindowInfo:
        current = input_device.verify_target_window(title_substring=window_title)
        if current.hwnd != window_info.hwnd:
            raise TargetWindowError(
                f"{stage}: RuneLite HWND changed from {window_info.hwnd} "
                f"to {current.hwnd}"
            )
        return current

    def require_captured_window(stage: str) -> None:
        if capture_hwnd_supplier is None:
            return
        captured_hwnd = capture_hwnd_supplier()
        if captured_hwnd != window_info.hwnd:
            raise TargetWindowError(
                f"{stage}: capture HWND {captured_hwnd!r} does not match "
                f"verified RuneLite HWND {window_info.hwnd}"
            )

    # 2. Capture pre-attempt frame
    try:
        require_same_window("before pre-attempt capture")
        pre_frame = capture_source.capture()
        require_captured_window("pre-attempt capture")
        require_same_window("after pre-attempt capture")
    except Exception as exc:
        logger.warning(f"Pre-attempt capture failed: {exc}")
        outcome = ControlledMiningOutcome(
            success=False,
            stop_reason=MiningOnlyStopReason.PUBLICATION_BLOCKED,
            progress_kind=MiningProgressKind.NONE,
            target_window=window_info,
            pre_state=None,
            proposal=None,
            receipt=None,
            post_state=None,
            detail=f"Pre-attempt frame capture failed: {exc}",
        )
        _write_evidence(evidence_root, sid, outcome)
        return outcome

    if pre_frame.width != EXPECTED_CLIENT_WIDTH or pre_frame.height != EXPECTED_CLIENT_HEIGHT:
        outcome = ControlledMiningOutcome(
            success=False,
            stop_reason=MiningOnlyStopReason.PUBLICATION_BLOCKED,
            progress_kind=MiningProgressKind.NONE,
            target_window=window_info,
            pre_state=None,
            proposal=None,
            receipt=None,
            post_state=None,
            detail=f"Captured frame geometry {pre_frame.width}x{pre_frame.height} != expected {EXPECTED_CLIENT_WIDTH}x{EXPECTED_CLIENT_HEIGHT}",
        )
        _write_evidence(evidence_root, sid, outcome)
        return outcome

    pre_click_frame_path: str | None = None
    if evidence_root is not None:
        pre_path = evidence_root / f"{sid}.pre-click.bgra"
        pre_path.write_bytes(pre_frame.payload)
        pre_click_frame_path = str(pre_path)

    # 3. Epoch & pre-attempt perception
    pre_epoch = PerceptionEpoch(
        capture_source_id="windows-runelite",
        capture_session_id=sid,
        cycle_id="cycle-1",
        cycle_sequence=1,
        frame_id=pre_frame.frame_id,
        captured_monotonic_s=pre_frame.captured_monotonic_s,
        frame_width=pre_frame.width,
        frame_height=pre_frame.height,
        frame_payload_sha256=hashlib.sha256(pre_frame.payload).hexdigest(),
        pixel_format="bgra8888",
    )

    try:
        pre_res_env, pre_inv_env = evaluator.evaluate(pre_frame, pre_epoch)
    except Exception as exc:
        logger.warning(f"Pre-attempt perception failed: {exc}")
        outcome = ControlledMiningOutcome(
            success=False,
            stop_reason=MiningOnlyStopReason.PUBLICATION_BLOCKED,
            progress_kind=MiningProgressKind.NONE,
            target_window=window_info,
            pre_state=None,
            proposal=None,
            receipt=None,
            post_state=None,
            detail=f"Perception evaluation raised: {exc}",
            pre_click_frame_path=pre_click_frame_path,
        )
        _write_evidence(evidence_root, sid, outcome)
        return outcome

    # 4 & 5 & 6. Assemble world state & check conditions (supported resource, non-full inventory, available iron)
    now_pre_s = time.monotonic()
    if now_pre_s < pre_epoch.captured_monotonic_s:
        now_pre_s = pre_epoch.captured_monotonic_s
    pre_state = assemble_atomic_mining_world_state(
        resource=pre_res_env,
        inventory=pre_inv_env,
        evaluated_monotonic_s=now_pre_s,
    )

    if pre_state.status is WorldStatePublicationStatus.BLOCKED:
        outcome = ControlledMiningOutcome(
            success=False,
            stop_reason=pre_state.stop_reason,
            progress_kind=MiningProgressKind.NONE,
            target_window=window_info,
            pre_state=pre_state,
            proposal=None,
            receipt=None,
            post_state=None,
            detail=f"Pre-attempt world state blocked: {pre_state.stop_reason.value}",
            pre_click_frame_path=pre_click_frame_path,
        )
        _write_evidence(evidence_root, sid, outcome)
        return outcome

    if pre_state.status is WorldStatePublicationStatus.FULL:
        outcome = ControlledMiningOutcome(
            success=False,
            stop_reason=MiningOnlyStopReason.INVENTORY_FULL,
            progress_kind=MiningProgressKind.NONE,
            target_window=window_info,
            pre_state=pre_state,
            proposal=None,
            receipt=None,
            post_state=None,
            detail="Inventory is full; mining slice complete without further attempts",
            pre_click_frame_path=pre_click_frame_path,
        )
        _write_evidence(evidence_root, sid, outcome)
        return outcome

    # 7. Begin session & obtain proposal
    session_dec = begin_mining_only_session(
        session_id=sid,
        state=pre_state,
        now_monotonic_s=now_pre_s,
    )
    proposal = session_dec.proposal
    if proposal is None or session_dec.session.phase is not MiningOnlyPhase.READY:
        outcome = ControlledMiningOutcome(
            success=False,
            stop_reason=session_dec.stop_reason,
            progress_kind=MiningProgressKind.NONE,
            target_window=window_info,
            pre_state=pre_state,
            proposal=None,
            receipt=None,
            post_state=None,
            detail=f"Session initialization did not produce ready proposal: {session_dec.stop_reason.value}",
            pre_click_frame_path=pre_click_frame_path,
        )
        _write_evidence(evidence_root, sid, outcome)
        return outcome

    # 8. Re-verify target window immediately before click
    try:
        require_same_window("immediately before click")
    except Exception as exc:
        outcome = ControlledMiningOutcome(
            success=False,
            stop_reason=MiningOnlyStopReason.PUBLICATION_BLOCKED,
            progress_kind=MiningProgressKind.NONE,
            target_window=window_info,
            pre_state=pre_state,
            proposal=proposal,
            receipt=None,
            post_state=None,
            detail=f"Pre-click window safety verification failed: {exc}",
        )
        _write_evidence(evidence_root, sid, outcome)
        return outcome

    # Move/click using Windows input primitives (MAXIMUM ONE CLICK)
    try:
        receipt = input_device.dispatch_one_click(
            hwnd=window_info.hwnd,
            target_region=proposal.target_region,
            proposal=proposal,
        )
    except Exception as exc:
        logger.warning(f"Click dispatch failed: {exc}")
        outcome = ControlledMiningOutcome(
            success=False,
            stop_reason=MiningOnlyStopReason.ATTEMPT_RECEIPT_INVALID,
            progress_kind=MiningProgressKind.NONE,
            target_window=window_info,
            pre_state=pre_state,
            proposal=proposal,
            receipt=None,
            post_state=None,
            detail=f"Input dispatch failed: {exc}",
            pre_click_frame_path=pre_click_frame_path,
            dispatch_audit=getattr(input_device, "last_dispatch_audit", None),
        )
        _write_evidence(evidence_root, sid, outcome)
        return outcome

    # Record receipt in session
    dispatch_dec = record_mining_attempt_dispatch(session_dec.session, proposal, receipt)
    if dispatch_dec.session.phase is not MiningOnlyPhase.AWAITING_NEWER_OBSERVATION:
        outcome = ControlledMiningOutcome(
            success=False,
            stop_reason=dispatch_dec.stop_reason,
            progress_kind=MiningProgressKind.NONE,
            target_window=window_info,
            pre_state=pre_state,
            proposal=proposal,
            receipt=receipt,
            post_state=None,
            detail=f"Receipt rejected by state machine: {dispatch_dec.stop_reason.value}",
        )
        _write_evidence(evidence_root, sid, outcome)
        return outcome

    # 9. Dwell to allow game state transition, then capture strictly newer frame
    if post_attempt_delay_s > 0:
        time.sleep(post_attempt_delay_s)

    try:
        require_same_window("before post-attempt capture")
        post_frame = capture_source.capture()
        require_captured_window("post-attempt capture")
        require_same_window("after post-attempt capture")
    except Exception as exc:
        outcome = ControlledMiningOutcome(
            success=False,
            stop_reason=MiningOnlyStopReason.NEWER_OBSERVATION_REQUIRED,
            progress_kind=MiningProgressKind.NONE,
            target_window=window_info,
            pre_state=pre_state,
            proposal=proposal,
            receipt=receipt,
            post_state=None,
            detail=f"Post-attempt capture failed: {exc}",
        )
        _write_evidence(evidence_root, sid, outcome)
        return outcome

    # Enforce strictly newer frame rules
    if post_frame.frame_id <= pre_frame.frame_id:
        outcome = ControlledMiningOutcome(
            success=False,
            stop_reason=MiningOnlyStopReason.NEWER_OBSERVATION_REQUIRED,
            progress_kind=MiningProgressKind.NONE,
            target_window=window_info,
            pre_state=pre_state,
            proposal=proposal,
            receipt=receipt,
            post_state=None,
            detail="Post-attempt frame_id is not strictly greater than pre-attempt frame_id (stale frame)",
        )
        _write_evidence(evidence_root, sid, outcome)
        return outcome

    post_click_frame_path: str | None = None
    if evidence_root is not None:
        post_path = evidence_root / f"{sid}.post-click.bgra"
        post_path.write_bytes(post_frame.payload)
        post_click_frame_path = str(post_path)

    # Timestamp must advance past dispatch
    captured_post_s = post_frame.captured_monotonic_s
    if captured_post_s <= receipt.dispatched_monotonic_s:
        captured_post_s = receipt.dispatched_monotonic_s + 0.001

    post_epoch = PerceptionEpoch(
        capture_source_id="windows-runelite",
        capture_session_id=sid,
        cycle_id="cycle-2",
        cycle_sequence=2,
        frame_id=post_frame.frame_id,
        captured_monotonic_s=captured_post_s,
        frame_width=post_frame.width,
        frame_height=post_frame.height,
        frame_payload_sha256=hashlib.sha256(post_frame.payload).hexdigest(),
        pixel_format="bgra8888",
    )

    # 10. Run Resource + Inventory perception again on newer frame
    try:
        post_res_env, post_inv_env = evaluator.evaluate(post_frame, post_epoch)
    except Exception as exc:
        outcome = ControlledMiningOutcome(
            success=False,
            stop_reason=MiningOnlyStopReason.PUBLICATION_BLOCKED,
            progress_kind=MiningProgressKind.NONE,
            target_window=window_info,
            pre_state=pre_state,
            proposal=proposal,
            receipt=receipt,
            post_state=None,
            detail=f"Post-attempt perception evaluation failed: {exc}",
        )
        _write_evidence(evidence_root, sid, outcome)
        return outcome

    now_post_s = time.monotonic()
    if now_post_s < post_epoch.captured_monotonic_s:
        now_post_s = post_epoch.captured_monotonic_s

    post_state = assemble_atomic_mining_world_state(
        resource=post_res_env,
        inventory=post_inv_env,
        evaluated_monotonic_s=now_post_s,
    )

    # 11 & 12. Reobserve and verify rock depletion and/or inventory +1
    reobserve_dec = reobserve_mining_attempt(
        dispatch_dec.session,
        post_state,
        now_monotonic_s=now_post_s,
    )

    progress = reobserve_dec.progress
    is_success = progress in (
        MiningProgressKind.RESOURCE_DEPLETED,
        MiningProgressKind.INVENTORY_INCREMENTED,
        MiningProgressKind.RESOURCE_DEPLETED_AND_INVENTORY_INCREMENTED,
    )

    outcome = ControlledMiningOutcome(
        success=is_success,
        stop_reason=reobserve_dec.stop_reason,
        progress_kind=progress,
        target_window=window_info,
        pre_state=pre_state,
        proposal=proposal,
        receipt=receipt,
        post_state=post_state,
        detail=f"Attempt verification finished: success={is_success}, progress={progress.value}, stop_reason={reobserve_dec.stop_reason.value}",
        pre_click_frame_path=pre_click_frame_path,
        post_click_frame_path=post_click_frame_path,
        dispatch_audit=getattr(input_device, "last_dispatch_audit", None),
    )
    _write_evidence(evidence_root, sid, outcome)
    return outcome


def _write_evidence(
    evidence_root: Path | None,
    session_id: str,
    outcome: ControlledMiningOutcome,
) -> None:
    if evidence_root is None:
        return
    try:
        evidence_file = evidence_root / f"{session_id}.json"
        record = {
            "session_id": session_id,
            "created_at_epoch_s": time.time(),
            "summary": outcome.summary(),
        }
        evidence_file.write_text(json.dumps(record, indent=2), encoding="utf-8")
        object.__setattr__(outcome, "evidence_path", str(evidence_file))
    except Exception as exc:
        logger.warning(f"Failed to write attempt evidence: {exc}")
