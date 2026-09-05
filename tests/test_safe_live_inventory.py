from __future__ import annotations

from pathlib import Path

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.controlled_mining_runner import (
    EXPECTED_CLIENT_HEIGHT,
    EXPECTED_CLIENT_WIDTH,
)
from mining_automation.perception.inventory.geometry import Region
from mining_automation.perception.inventory.positive_v3_prototypes import SUPPORTED_REGION
from mining_automation.safe_live_inventory import (
    SafeEmptyStartMiningPerceptionEvaluator,
)


def _frame_with_inventory_region(region_bytes: bytes) -> Frame:
    region = Region(*SUPPORTED_REGION)
    payload = bytearray(EXPECTED_CLIENT_WIDTH * EXPECTED_CLIENT_HEIGHT * 4)
    source_stride = region.width * 4
    target_stride = EXPECTED_CLIENT_WIDTH * 4
    for row in range(region.height):
        source_start = row * source_stride
        target_start = (region.y + row) * target_stride + region.x * 4
        payload[target_start : target_start + source_stride] = region_bytes[
            source_start : source_start + source_stride
        ]
    return Frame.from_raw(
        RawFrame(
            bytes(payload),
            EXPECTED_CLIENT_WIDTH,
            EXPECTED_CLIENT_HEIGHT,
            PixelFormat.BGRA8888,
        ),
        frame_id=1,
        captured_monotonic_s=1.0,
    )


def _packaged_empty_region() -> bytes:
    return (
        Path(__file__).parents[1]
        / "src"
        / "mining_automation"
        / "perception"
        / "inventory"
        / "profiles"
        / "varrock_east_empty_inventory_v3.bgra"
    ).read_bytes()


def test_proven_empty_bootstraps_zero_and_session_detector() -> None:
    evaluator = SafeEmptyStartMiningPerceptionEvaluator()
    state, reason = evaluator._evaluate_packaged_inventory(
        _frame_with_inventory_region(_packaged_empty_region())
    )

    assert state.occupied_slots == 0
    assert state.confidence >= 0.8
    assert reason is None
    assert evaluator._session_inventory_detector is not None


def test_unrecognized_precalibration_inventory_can_never_publish_false_full() -> None:
    region = Region(*SUPPORTED_REGION)
    unknown_region = bytes([255]) * (region.width * region.height * 4)
    evaluator = SafeEmptyStartMiningPerceptionEvaluator()

    state, reason = evaluator._evaluate_packaged_inventory(
        _frame_with_inventory_region(unknown_region)
    )

    assert state.occupied_slots is None
    assert state.confidence == 0.0
    assert reason == "inventory_requires_proven_empty_baseline"
    assert evaluator._session_inventory_detector is None
