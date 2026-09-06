"""Actual retained-region replay plus explicitly synthetic fault/full-count tests."""
from __future__ import annotations

import base64
import hashlib
import json
import lzma
import zlib
from pathlib import Path

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.controlled_mining_runner import ProductionMiningPerceptionEvaluator
from mining_automation.perception.inventory.positive_v3_prototypes import (
    SUPPORTED_COLUMN_STRIDE,
    SUPPORTED_FRAME_HEIGHT,
    SUPPORTED_FRAME_WIDTH,
    SUPPORTED_REGION,
    SUPPORTED_ROW_STRIDE,
)
from mining_automation.perception.inventory.retained_iron import _BACKGROUND_MASK

FIXTURE = Path(__file__).parent / "fixtures" / "retained_iron_inventory_regions.json"


def _region(name: str) -> bytes:
    corpus = json.loads(FIXTURE.read_text(encoding="utf-8"))
    index = next(i for i, item in enumerate(corpus["records"]) if item["name"] == name)
    record = corpus["records"][index]
    regions = lzma.decompress(base64.b64decode(corpus["region_rgb_lzma_base64"]))
    size = SUPPORTED_REGION[2] * SUPPORTED_REGION[3] * 3
    assert len(regions) == len(corpus["records"]) * size
    rgb = regions[index * size:(index + 1) * size]
    assert hashlib.sha256(rgb).hexdigest() == record["region_rgb_sha256"]
    assert len(rgb) == SUPPORTED_REGION[2] * SUPPORTED_REGION[3] * 3
    return rgb


def _frame(rgb: bytes, *, frame_id: int = 1) -> Frame:
    x, y, w, h = SUPPORTED_REGION
    data = bytearray(SUPPORTED_FRAME_WIDTH * SUPPORTED_FRAME_HEIGHT * 4)
    for row in range(h):
        for col in range(w):
            source = (row * w + col) * 3
            dest = ((y + row) * SUPPORTED_FRAME_WIDTH + x + col) * 4
            red, green, blue = rgb[source:source + 3]
            data[dest:dest + 4] = bytes((blue, green, red, 255))
    return Frame.from_raw(
        RawFrame(bytes(data), SUPPORTED_FRAME_WIDTH, SUPPORTED_FRAME_HEIGHT,
                 PixelFormat.BGRA8888),
        frame_id=frame_id, captured_monotonic_s=float(frame_id),
    )


def _set_pixel(rgb: bytes, x: int, y: int, color: tuple[int, int, int]) -> bytes:
    data = bytearray(rgb)
    offset = (y * SUPPORTED_REGION[2] + x) * 3
    data[offset:offset + 3] = bytes(color)
    return bytes(data)


def _composed_prefix(count: int) -> bytes:
    """Synthetic placement of an observed iron sprite into known empty slots.

    This tests counting/termination plumbing, NOT real 5..28 capture evidence.
    """
    source = _region("four")
    data = bytearray(_region("empty"))
    w = SUPPORTED_REGION[2]
    for i in range(count):
        dx = i % 4 * SUPPORTED_COLUMN_STRIDE
        dy = i // 4 * SUPPORTED_ROW_STRIDE
        for y in range(32):
            for x in range(32):
                bit = y * 32 + x
                if _BACKGROUND_MASK[bit // 8] & (1 << (bit % 8)):
                    continue
                src = (y * w + x) * 3
                dest = ((dy + y) * w + dx + x) * 3
                data[dest:dest + 3] = source[src:src + 3]
    return bytes(data)


@pytest.mark.parametrize(("name", "count"), [
    ("empty", 0), ("one", 1), ("two", 2), ("three", 3), ("four", 4),
])
def test_real_retained_region_count(name: str, count: int) -> None:
    evaluator = ProductionMiningPerceptionEvaluator()
    state, reason = evaluator._evaluate_packaged_inventory(_frame(_region(name)))
    assert state.occupied_slots == count
    assert state.confidence >= 0.8
    assert reason is None


def test_real_empty_then_one_two_three_four_session() -> None:
    evaluator = ProductionMiningPerceptionEvaluator()
    for seq, name in enumerate(("empty", "one", "two", "three", "four"), start=1):
        state, reason = evaluator._evaluate_packaged_inventory(_frame(_region(name), frame_id=seq))
        assert state.occupied_slots == seq - 1
        assert state.confidence >= 0.8
        assert reason is None


@pytest.mark.parametrize("count", range(29))
def test_synthetic_known_iron_prefix_counts_through_28(count: int) -> None:
    state, reason = ProductionMiningPerceptionEvaluator()._evaluate_packaged_inventory(
        _frame(_composed_prefix(count))
    )
    assert state.occupied_slots == count
    assert reason is None


@pytest.mark.parametrize("fault", [
    "unfamiliar_core", "selection_border", "unknown_item", "tooltip",
    "gutter_change", "non_prefix", "unfamiliar_background", "missing_sprite_pixel",
])
def test_faults_remain_unknown(fault: str) -> None:
    rgb = _region("three")
    w = SUPPORTED_REGION[2]
    if fault == "unfamiliar_core":
        rgb = _set_pixel(rgb, 15, 15, (255, 0, 255))
    elif fault == "selection_border":
        rgb = _set_pixel(rgb, 0, 0, (255, 255, 0))
    elif fault == "unknown_item":
        for y in range(4, 28):
            for x in range(4, 28):
                rgb = _set_pixel(rgb, x, y, (110, 100, 90))
    elif fault == "tooltip":
        for y in range(8, 24):
            for x in range(8, 55):
                rgb = _set_pixel(rgb, x, y, (0, 0, 0))
    elif fault == "gutter_change":
        rgb = _set_pixel(rgb, 34, 10, (255, 255, 255))
    elif fault == "non_prefix":
        data = bytearray(rgb)
        empty = _region("empty")
        for row in range(32):
            i = (row * w + 42) * 3
            data[i:i + 32 * 3] = empty[i:i + 32 * 3]
        rgb = bytes(data)
    elif fault == "unfamiliar_background":
        rgb = _set_pixel(rgb, 0, 0, (65, 56, 46))
    elif fault == "missing_sprite_pixel":
        rgb = _set_pixel(rgb, 15, 15, (62, 53, 41))
    evaluator = ProductionMiningPerceptionEvaluator()
    # Test after normal empty startup too; a session must not bypass a rejection.
    evaluator._evaluate_packaged_inventory(_frame(_region("empty")))
    state, reason = evaluator._evaluate_packaged_inventory(_frame(rgb, frame_id=2))
    assert state.occupied_slots is None, fault
    assert state.confidence == 0.0
    assert reason is not None


def test_one_corrupted_slot_does_not_create_full_inventory() -> None:
    rgb = _set_pixel(_composed_prefix(28), 15, 15, (255, 0, 255))
    state, _ = ProductionMiningPerceptionEvaluator()._evaluate_packaged_inventory(_frame(rgb))
    assert state.occupied_slots is None


def test_real_fifth_iron_after_relogin_is_counted_without_empty_startup() -> None:
    fixture = json.loads((FIXTURE.parent / "retained_iron_five_region.json").read_text())
    rgb = zlib.decompress(base64.b64decode(fixture["rgb_zlib_base64"]))
    assert hashlib.sha256(rgb).hexdigest() == fixture["region_rgb_sha256"]
    state, reason = ProductionMiningPerceptionEvaluator()._evaluate_packaged_inventory(
        _frame(rgb)
    )
    assert state.occupied_slots == 5
    assert state.confidence >= 0.8
    assert reason is None


@pytest.mark.parametrize("fault", ["core", "border", "gutter"])
def test_real_fifth_ore_still_rejects_corruption(fault: str) -> None:
    fixture = json.loads((FIXTURE.parent / "retained_iron_five_region.json").read_text())
    rgb = zlib.decompress(base64.b64decode(fixture["rgb_zlib_base64"]))
    x, y = {"core": (15, 51), "border": (0, 36), "gutter": (34, 46)}[fault]
    rgb = _set_pixel(rgb, x, y, (255, 0, 255))
    state, reason = ProductionMiningPerceptionEvaluator()._evaluate_packaged_inventory(
        _frame(rgb)
    )
    assert state.occupied_slots is None
    assert reason is not None
