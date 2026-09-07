"""Offline regressions; these synthetic fixtures are not live-client evidence."""

from types import SimpleNamespace

import numpy as np
import pytest

from mining_automation.bank_runtime import BankRegion, BankRunner
from mining_automation.bank_vision import BankUnproven, BankVision, Match


def inventory_image(ores):
    vision = BankVision()
    image = np.zeros((862, 804, 3), np.uint8)
    image[564:823, 550:741] = vision.assets["empty_open"]
    for index in range(ores):
        row, col = divmod(index, 4)
        x = 565 + 42 * col
        y = 569 + 36 * row
        image[y : y + 31, x : x + 37] = vision.assets["ore"]
    return vision, image


@pytest.mark.parametrize("ore_count", [0, 1, 27, 28])
def test_counts_real_sprite_in_synthetic_inventory(ore_count):
    vision, image = inventory_image(ore_count)
    result = vision.inventory(image, (550, 564))
    assert result["ore_count"] == ore_count
    assert result["empty_count"] == 28 - ore_count
    assert result["unknown_count"] == 0


def test_unknown_slot_is_neither_ore_nor_empty():
    vision, image = inventory_image(27)
    image[783:817, 689:729] = (255, 0, 255)
    result = vision.inventory(image, (550, 564))
    assert result["unknown_count"] == 1 and result["ore_count"] == 27


def fake_runner(close_works=True, initial_ores=28):
    state = {"ore": initial_ores, "open": True, "clicks": [], "frame": 0}
    runner = BankRunner.__new__(BankRunner)
    runner.backend = SimpleNamespace(wait=lambda seconds: None)
    runner.hover = lambda point: None
    runner.record = lambda *a, **kw: None
    image = SimpleNamespace(shape=(862, 804, 3))

    def observe(label):
        state["frame"] += 1
        return SimpleNamespace(frame_id=state["frame"], evidence_path=label), image

    def inventory(image):
        return {
            "ore_count": state["ore"],
            "empty_count": 28 - state["ore"],
            "unknown_count": 0,
            "ore_slots": [(565, 567, 605, 601)] * state["ore"],
        }

    def controls(image):
        return (
            (Match(170, 39, 135, 15, 1.0), Match(478, 34, 21, 22, 1.0)) if state["open"] else None
        )

    runner.observe = observe
    runner.vision = SimpleNamespace(
        inventory=inventory,
        bank_controls=controls,
        match=lambda *a, **kw: Match(260, 650, 39, 37, 1.0),
    )

    def click(frame, point, action):
        state["clicks"].append(action)
        if action == "DEPOSIT_ALL_IRON_ORE_ONLY":
            state["ore"] = 0
        if action == "CLOSE_BANK_X" and close_works:
            state["open"] = False

    runner.click = click
    return runner, state


def test_deposit_is_followed_by_X_and_verified_closed():
    runner, state = fake_runner()
    result = runner.run()
    assert result["success"] and result["bank_closed_verified"]
    assert state["clicks"] == ["DEPOSIT_ALL_IRON_ORE_ONLY", "CLOSE_BANK_X"]


def test_deposit_without_bank_close_is_not_success():
    runner, state = fake_runner(close_works=False)
    with pytest.raises(BankUnproven, match="bank_close_not_verified"):
        runner.run()
    assert state["ore"] == 0 and state["open"]


def test_initial_empty_inventory_never_claims_a_deposit():
    runner, state = fake_runner(initial_ores=0)
    with pytest.raises(BankUnproven, match="requires_verified_28_iron_ore"):
        runner.run()
    assert state["clicks"] == []


def test_bank_target_region_is_bounded():
    region = BankRegion(10, 20, 30, 40)
    assert region.contains((20, 30))
    assert not region.contains((30, 40))
