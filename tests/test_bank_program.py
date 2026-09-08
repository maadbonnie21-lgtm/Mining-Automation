"""Offline regressions; these synthetic fixtures are not live-client evidence."""

from types import SimpleNamespace

import numpy as np
import pytest

from mining_automation.bank_runtime import BankRegion, BankRunner
from mining_automation.bank_vision import (
    _DEPOSIT_ALL_PREFIX_MASK_HEX,
    BankUnproven,
    BankVision,
    Match,
)
from mining_automation.perception.inventory.retained_iron import (
    source_verified_uncut_ruby_slot_rgb,
)


def inventory_image(ores):
    return item_inventory_image(set(range(ores)), set())


def item_inventory_image(ore_indices, ruby_indices):
    vision = BankVision()
    image = np.zeros((862, 804, 3), np.uint8)
    image[564:823, 550:741] = vision.assets["empty_open"]
    for index in ore_indices:
        row, col = divmod(index, 4)
        x = 565 + 42 * col
        y = 569 + 36 * row
        image[y : y + 31, x : x + 37] = vision.assets["ore"]
    ruby = np.frombuffer(source_verified_uncut_ruby_slot_rgb(), dtype=np.uint8).reshape(
        (32, 32, 3)
    )[:, :, ::-1]
    red = ruby[:, :, 2].astype(np.int16)
    green = ruby[:, :, 1].astype(np.int16)
    blue = ruby[:, :, 0].astype(np.int16)
    ruby_mask = (red > 60) & (red - green > 30) & (red - blue > 35)
    for index in ruby_indices:
        row, col = divmod(index, 4)
        x = 565 + 42 * col
        y = 569 + 36 * row
        slot = image[y : y + 32, x : x + 32]
        slot[ruby_mask] = ruby[ruby_mask]
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


@pytest.mark.parametrize("ruby_index", [0, 5, 17, 22, 27])
def test_ruby_is_source_verified_in_any_guarded_slot(ruby_index):
    iron = set(range(28)) - {ruby_index}
    vision, image = item_inventory_image(iron, {ruby_index})
    result = vision.inventory(image, (550, 564))
    assert result["occupied_count"] == 28
    assert result["ore_count"] == 27
    assert result["gem_count"] == 1
    assert result["gems"][0]["slot_index"] == ruby_index
    assert result["gems"][0]["item_id"] == "uncut_ruby"
    assert result["unknown_count"] == result["empty_count"] == 0


def test_remaining_nonprefix_ruby_is_not_masked_as_empty_after_iron_deposit():
    vision, image = item_inventory_image(set(), {22})
    result = vision.inventory(image, (550, 564))
    assert result["ore_count"] == 0
    assert result["gem_count"] == result["occupied_count"] == 1
    assert result["empty_count"] == 27
    assert result["unknown_count"] == 0


def test_ruby_signature_on_wrong_slot_background_remains_unknown():
    vision, image = item_inventory_image(set(range(27)), {27})
    image[785:788, 691:723] = (255, 0, 255)
    result = vision.inventory(image, (550, 564))
    assert result["gem_count"] == 0
    assert result["unknown_count"] == 1


def test_fresh_deposit_all_prefix_proof_is_not_present_on_a_clean_top_bar():
    image = np.zeros((862, 804, 3), np.uint8)
    assert BankVision.deposit_all_prefix_score(image) == 0.0
    mask = np.unpackbits(
        np.frombuffer(bytes.fromhex(_DEPOSIT_ALL_PREFIX_MASK_HEX), dtype=np.uint8),
        bitorder="big",
        count=18 * 92,
    ).reshape((18, 92)).astype(bool)
    crop = image[29:47, 3:95]
    crop[mask] = (255, 255, 255)
    assert BankVision.deposit_all_prefix_score(image) == 1.0


def fake_runner(close_works=True, initial_ores=28, initial_gems=0):
    state = {
        "ore": initial_ores,
        "gems": initial_gems,
        "open": True,
        "clicks": [],
        "frame": 0,
    }
    runner = BankRunner.__new__(BankRunner)
    runner.backend = SimpleNamespace(wait=lambda seconds: None)
    runner.hover = lambda point: None
    runner.record = lambda *a, **kw: None
    image = SimpleNamespace(shape=(862, 804, 3))

    def observe(label):
        state["frame"] += 1
        return SimpleNamespace(frame_id=state["frame"], evidence_path=label), image

    def inventory(image):
        gem_items = [
            {"item_id": "uncut_ruby", "slot": (649, 747, 689, 781)}
            for _ in range(state["gems"])
        ]
        return {
            "ore_count": state["ore"],
            "gem_count": state["gems"],
            "gems": gem_items,
            "gem_slots": [item["slot"] for item in gem_items],
            "occupied_count": state["ore"] + state["gems"],
            "empty_count": 28 - state["ore"] - state["gems"],
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
        deposit_all_prefix_score=lambda image: 1.0,
        deposit_all_prefix_source_sha256="8dcaf2733824e7904c0b282b83180f14df7a64b0f0f82468f2dac8c6a0b7fa14",
    )

    def click(frame, point, action):
        state["clicks"].append(action)
        if action == "DEPOSIT_ALL_IRON_ORE_ONLY":
            state["ore"] = 0
        if action == "DEPOSIT_ALL_UNCUT_RUBY_ONLY":
            state["gems"] = 0
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
    with pytest.raises(BankUnproven, match="requires_verified_full_mining_load"):
        runner.run()
    assert state["clicks"] == []


def test_mixed_load_deposits_iron_then_ruby_then_closes_bank():
    runner, state = fake_runner(initial_ores=27, initial_gems=1)
    result = runner.run()
    assert result["success"]
    assert result["before_ore_count"] == result["deposited_ore_count"] == 27
    assert result["before_gem_count"] == result["deposited_gem_count"] == 1
    assert result["deposited_gem_item_ids"] == ["uncut_ruby"]
    assert state["clicks"] == [
        "DEPOSIT_ALL_IRON_ORE_ONLY",
        "DEPOSIT_ALL_UNCUT_RUBY_ONLY",
        "CLOSE_BANK_X",
    ]


def test_bank_target_region_is_bounded():
    region = BankRegion(10, 20, 30, 40)
    assert region.contains((20, 30))
    assert not region.contains((30, 40))
