"""Offline regressions; these synthetic fixtures are not live-client evidence."""

import base64
import zlib
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
        x = 567 + 42 * col
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
    mask = (
        np.unpackbits(
            np.frombuffer(bytes.fromhex(_DEPOSIT_ALL_PREFIX_MASK_HEX), dtype=np.uint8),
            bitorder="big",
            count=18 * 92,
        )
        .reshape((18, 92))
        .astype(bool)
    )
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
            {"item_id": "uncut_ruby", "slot": (649, 747, 689, 781)} for _ in range(state["gems"])
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


def test_ruby_bank_crop_matches_original_native_slot_22_alignment():
    # The source-identified ruby occupies native C slot (651, 749), not (649, 749).
    vision, image = item_inventory_image(set(range(24)) - {22}, set())
    ruby = np.frombuffer(source_verified_uncut_ruby_slot_rgb(), dtype=np.uint8).reshape(32, 32, 3)
    image[749:781, 651:683] = ruby[:, :, ::-1]
    result = vision.inventory(image, (550, 564))
    assert result["ore_count"] == 23
    assert result["gem_count"] == 1
    assert result["gems"][0]["slot_index"] == 22
    assert result["empty_count"] == 4
    assert result["unknown_count"] == 0


def test_current_logical_bank_ruby_presentation_is_exact_and_rejects_mutation():
    encoded = (
        "eNq1lVtz01YQgB/KtMi6naOLddeRJVmW7RDfM4SYuH3oQ4cOl+lDmKQECCFOAIMhxaGEQtq0/7trHdsxJL4kgZlvNGNb/nbP"
        "rna12sivnsXNetys5VaqEVwB+Lg64c6LQeUjaJQLeFqNwkw55Wsd4ZvKz/R/xfpPT36letlAp/3jQsdQKoXMZfxgu17Oeioy"
        "BFZPMMZoyVyEWE3oU8l710tZypyHgtuWFoOcqdzT0W+acFvlf1W5W2NsGsIthW1JzCpmQol3JIFSzXvN2uwQkHloKHd1/MFX"
        "PvnpD776ysXPAQfvOQjYddC2JW4Y/H2d+1lO/SgxrQQii/PUrbEY2Jjfs+V/QzVBOwrSH/30YUbtOFLbQsCOibZM8ZEpPjTE"
        "dZ1f0zlASzFxFDQbxfn9x6EC/v9C/TjQDj3lvSe9tHHb7Pu3TfQk4TEEMvoYLJOLwpXGwrn8x6FK/QdEfkvw4TDEkzG2TARN"
        "sdmroe8t1wrn92vg7xGp66C3Ln5PpI6NHxnC1tAP8nWdK3JXTCxUiv45/f0QIz/Qc/E7gp+aAlQGqgTteGjw4K/y3ympq3EU"
        "Tm/BWX7lnyAN/n0XQX32XQynOCB4z0K7NtRf2NA58P+iMA6fykXZ6S2g/l0bj+TA34Fy4El/etKnjPyHi1+7CK4dGxotPkiS"
        "B+6l2Uhks37mxtQWgN/CfNtGp/xyj8gHnrJP5DdE7rrSjtWvPE0eWNO4n2SWSGLtWnghP5gHvHaVttX3Qwt+1wf5A7fVFDxF"
        "hThqNiYeYakUEgVtmuJR8Jn/XWYg77py28I7dn+Wt2mIYYmoP47jZr04ZXmWc24OsV3yWQs+BkrPo5ljyHw32RVPrcGI0RDz"
        "+IHlcpZnGfj76RLRslD5yD8IcTl/cgR5tOW+8AOwju6q7Dx+WNG1BX9B4kZdPgrgycFvCH7poI6DnjvoWQLUf8sc8NhEd1Te"
        "Fbh8Pj/dTyGysGaIf/kyNLpHcNdFIKd0aBQYLgOGd8B9TSwjPnTdSrk0c4sC0OWahtYN4QXsfEt8YZ/4AfgSlsOmjh4MuaMK"
        "GVGIc9FyffFmozD7LVyPIYSHWOuHKyHz/ZrGn8jtE/mGDiPGAxGfCgmB5Geu6LFnFRoRwLzE2dCXscsxFIdjLJYxhxgJUeBX"
        "y6Ub9Wvnfd1DPrDVSwU/l/WjMJhEtbII8nkqM/E4jSLEmgT8ehn5t6O1VBxw0fT+BwbhR9s="
    )
    raw = zlib.decompress(base64.b64decode(encoded))
    item = BankVision._logical_bank_byproduct(full_slot_rgb=raw, slot_index=22)
    assert item is not None
    assert item["item_id"] == "uncut_ruby"
    assert item["presentation_slot_rgb_sha256"] == (
        "52f4b427fadbaec40936200ec8a5a51fa4f264f0f6f75fbb5647d77c6d6e919e"
    )
    changed = bytearray(raw)
    for offset in range(0, len(changed), 3):
        red, green, blue = changed[offset : offset + 3]
        if red > 60 and red - green > 30 and red - blue > 35:
            changed[offset : offset + 3] = b"\x00\x00\x00"
            break
    assert BankVision._logical_bank_byproduct(full_slot_rgb=bytes(changed), slot_index=22) is None
