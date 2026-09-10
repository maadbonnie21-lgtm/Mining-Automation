"""Counter appearance regressions; not a substitute for live route proof."""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from mining_automation.navigation.visual_route import VisualRoute


def route():
    return VisualRoute(
        Path(__file__).resolve().parents[1]
        / "src/mining_automation/navigation/profiles/varrock_east/route.json"
    )


@pytest.mark.parametrize("key", ["counter", "counter_verified_bank"])
def test_both_preserved_counter_appearances_are_recognized(key):
    r = route()
    im = np.zeros((1078, 1005, 3), np.uint8)
    patch = r.images[key]
    h, w = patch.shape[:2]
    im[540 : 540 + h, 80 : 80 + w] = patch
    result = r.verify_endpoint(im, SimpleNamespace(radius=r.config["counter_reference_radius"]))
    assert result["accepted"] and result["counter_score"] > 0.98
    assert result["counter_template"] == key
    assert not result["bank_interface_opened"]


def test_counter_detection_does_not_search_inventory_or_chat():
    r = route()
    im = np.zeros((1078, 1005, 3), np.uint8)
    patch = r.images["counter_verified_bank"]
    h, w = patch.shape[:2]
    im[960 : 960 + h, 80 : 80 + w] = patch
    assert not r.verify_endpoint(im, SimpleNamespace(radius=r.config["counter_reference_radius"]))[
        "accepted"
    ]


def test_counter_detection_checks_observed_intermediate_scale(monkeypatch):
    r = route()
    im = np.zeros((1078, 1005, 3), np.uint8)

    def score_only_observed_scale(_world, patch, _method):
        score = 0.9 if patch.shape[:2] == (96, 321) else 0.0
        return np.array([[score]], dtype=np.float32)

    monkeypatch.setattr(
        "mining_automation.navigation.visual_route.cv2.matchTemplate",
        score_only_observed_scale,
    )

    result = r.verify_endpoint(
        im, SimpleNamespace(radius=r.config["counter_reference_radius"])
    )
    assert result["accepted"]
    assert result["counter_score"] == pytest.approx(0.9)
    assert result["counter_template"] == "counter_verified_bank"
