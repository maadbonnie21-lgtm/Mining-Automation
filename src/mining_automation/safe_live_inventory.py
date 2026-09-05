"""Fail-closed Inventory evaluator for the official empty-start live mining run.

The historical integration used an exact empty-slot hash allowlist as a shortcut.
Any slot hash that was not in the allowlist was treated as occupied.  When a
legitimate empty RuneLite view drifted away from every saved empty hash, all 28
slots therefore became "occupied" with confidence 1.0.  That is fail-open.

This evaluator keeps the existing packaged analyzer and 0.8 publication floor,
but changes the live-session bootstrap contract:

* before the session has a proven empty baseline, only a positively proven
  0/28 state may publish;
* absence from an empty hash allowlist is never positive occupancy evidence;
* once 0/28 is proven, the existing session detector is calibrated from that
  exact frame and owns subsequent occupancy observations;
* any nonzero/ambiguous pre-calibration result is UNKNOWN.

This is intentionally narrow for the controlled 0->28 experiment.  It does not
change Resource perception or any frozen threshold.
"""

from __future__ import annotations

from typing import Any

from .capture import Frame, PixelFormat, RawFrame
from .contracts import InventoryState
from .controlled_mining_runner import (
    _CURRENT_VIEW_EMPTY_SLOT_RGB_SHA256,
    _INVENTORY_EMPTY_REGION_PATH,
    ProductionMiningPerceptionEvaluator,
)
from .mining_slice import INVENTORY_CAPACITY, INVENTORY_PUBLICATION_FLOOR
from .perception.inventory.geometry import InventoryGridLayout, Region
from .perception.inventory.localization import InventoryFrameProfile
from .perception.inventory.positive_classifier_v3 import (
    InventoryPositiveV3DevelopmentAnalyzer,
)
from .perception.inventory.positive_v3_prototypes import (
    SUPPORTED_COLUMN_STRIDE,
    SUPPORTED_FRAME_HEIGHT,
    SUPPORTED_FRAME_WIDTH,
    SUPPORTED_PROFILE_ID,
    SUPPORTED_REGION,
    SUPPORTED_ROW_STRIDE,
)


class SafeEmptyStartMiningPerceptionEvaluator(ProductionMiningPerceptionEvaluator):
    """Inventory integration that must prove 0/28 before publishing occupancy."""

    def _ensure_inventory_analyzer(self) -> None:
        if self._inventory_analyzer is not None:
            return

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

    def _calibrate_session_detector(self, frame: Frame) -> None:
        from .perception.inventory.configuration import (
            inventory_positive_detector_v2_from_profile,
        )

        if self._inventory_profile is None:
            raise RuntimeError("Inventory profile is unavailable for session calibration")
        self._session_inventory_detector = inventory_positive_detector_v2_from_profile(
            self._inventory_profile,
            frame,
        )

    def _evaluate_calibrated_session(
        self,
        frame: Frame,
    ) -> tuple[InventoryState, str | None]:
        from .perception.inventory.adapter import inventory_state_from_observation

        if self._session_inventory_detector is None:
            raise RuntimeError("Inventory session detector is not calibrated")
        observations = self._session_inventory_detector.detect(frame)
        if not observations:
            return (
                InventoryState(None, INVENTORY_CAPACITY, 0.0),
                "inventory_session_unknown",
            )
        state = inventory_state_from_observation(observations[0])
        if (
            state.occupied_slots is None
            or state.confidence < INVENTORY_PUBLICATION_FLOOR
        ):
            return (
                InventoryState(None, INVENTORY_CAPACITY, 0.0),
                "inventory_session_unknown",
            )
        return state, None

    def _evaluate_packaged_inventory(
        self,
        frame: Frame,
    ) -> tuple[InventoryState, str | None]:
        self._ensure_inventory_analyzer()
        if self._inventory_profile is None or self._inventory_analyzer is None:
            raise RuntimeError("Inventory analyzer bootstrap did not complete")

        if self._session_inventory_detector is not None:
            return self._evaluate_calibrated_session(frame)

        slots = self._inventory_profile.layout.all_slot_regions(
            self._inventory_profile.region
        )
        current_hashes = self._current_view_slot_hashes(frame, slots)

        # Exact hashes are positive evidence only for EMPTY.  A hash that is not
        # in an empty allowlist is unknown, not positive evidence for OCCUPIED.
        exact_empty = (
            len(current_hashes) == len(_CURRENT_VIEW_EMPTY_SLOT_RGB_SHA256)
            and all(
                digest in empty_variants
                for digest, empty_variants in zip(
                    current_hashes,
                    _CURRENT_VIEW_EMPTY_SLOT_RGB_SHA256,
                    strict=True,
                )
            )
        )
        if exact_empty:
            self._calibrate_session_detector(frame)
            return InventoryState(0, INVENTORY_CAPACITY, 1.0), None

        result: Any = self._inventory_analyzer.analyze(frame)
        if (
            result.occupied_slots == 0
            and result.confidence >= INVENTORY_PUBLICATION_FLOOR
        ):
            self._calibrate_session_detector(frame)
            return InventoryState(0, INVENTORY_CAPACITY, result.confidence), None

        raw_empty = bool(result.slots) and all(
            slot.raw_v1_state.value == "empty"
            and slot.raw_v1_confidence >= INVENTORY_PUBLICATION_FLOOR
            for slot in result.slots
        )
        if raw_empty:
            self._calibrate_session_detector(frame)
            return InventoryState(0, INVENTORY_CAPACITY, 1.0), None

        # The official experiment starts empty.  Until that exact empty baseline
        # has been proven and the session detector is calibrated, publishing any
        # nonzero count could recreate the 0/28 -> 28/28 fail-open defect.
        return (
            InventoryState(None, INVENTORY_CAPACITY, 0.0),
            "inventory_requires_proven_empty_baseline",
        )
