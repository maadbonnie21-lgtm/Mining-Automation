"""A non-match to an empty hash is not positive evidence of an item.

The uploaded 2026-09-05 PNG round-trips to BGRA SHA256
c65922ee277593bcc33a340da3c5b9c0f54db21e58a93b1031ac2cb3ee1a5955.
Its visibly empty inventory produced 28 mismatches and the old fast path
published FULL with confidence 1.0. Only non-reconstructive crop digests
are retained below; no private client pixels or new approved prototypes.
The classifier seams in these tests are synthetic, not live proof.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.contracts import InventoryState
from mining_automation.controlled_mining_runner import (
    _CURRENT_VIEW_EMPTY_SLOT_RGB_SHA256,
    ProductionMiningPerceptionEvaluator,
)
from mining_automation.perception.inventory import adapter, configuration
from mining_automation.perception.inventory.geometry import InventoryGridLayout, Region
from mining_automation.perception.inventory.localization import InventoryFrameProfile
from mining_automation.perception.inventory.positive_v3_prototypes import (
    SUPPORTED_COLUMN_STRIDE,
    SUPPORTED_FRAME_HEIGHT,
    SUPPORTED_FRAME_WIDTH,
    SUPPORTED_PROFILE_ID,
    SUPPORTED_REGION,
    SUPPORTED_ROW_STRIDE,
)

REPORTED_EMPTY_FRAME_SLOT_HASHES = (
    "10c14c0255bb45dc036df6d0a13972fafc93057cfd79e911c825aae2e63ac041",
    "f04010111b6b68a280c291bbc8dfef2d8c512c33300e83336f5595fbe038ae4a",
    "a820ed9c600021acfb5dc1d0e65c2fc1894190d65b151ce2697c62a6db7e2eac",
    "8673b27edd6a3e2c527f204524d7e83ee931302896165dd6d5b217a1b07bcff6",
    "60006979509efdbc7da24deb18277d849f2491640f9f24c7f692b5575c301d07",
    "2b8d596a31093f912ceb31b299e2ce178dd21ed67df0fd4258b79a9addb69a9f",
    "2abce1ffb9a0155b47da46e49863478df4e21f38a74831048abd53b85a9c63b1",
    "ebae1c8a9bee236bb63e4ed8531d02d8906f64484646a9ba6fd36be8faeb7c9d",
    "72f3241bbde0c3b136012d10540c5fab5c5a69fdedca33afc3b6202b17715232",
    "492678c5d23a9a6b51667f732ba76ea464e212d1ed2b0a80b3372a59bd465c62",
    "6d97c530bb7691b49ad6a1feb29a629b719723479fdf96408fa779c45cad43ee",
    "de9972cf2011b4296ffed7962f12dc89a230bd971521fbe4279d5cd0c82ad8a6",
    "5ba158fddbeb4927965ac734f442fe239a31f5a1aa8d512740d94f069059c364",
    "7bad9677b1464572fda484870c2668a9f32c148a658e282d00411e6ad496759e",
    "610c02e6a098df199560a32a2c9bd1556fb7bc6867b2ec9c164118dfed4ac88e",
    "8e5b1c39f84c77cb7dee9173b9cb9c68439f525921bd86c3588ef771fb157944",
    "7cc44087f8f5030530db68bbc5ad04d0fd0dcb1427099018cfd2ca23fcdd563d",
    "9a6e1689e09c415d29fc6aca2e5f86ced29d3785071d566cf3f7cddcb515dbf3",
    "6f8fb5b31be7463e2c62684f58b0b0ce40ac6a2f88016ae55a578e79e9811ce8",
    "8d047822ef25b4f3497ca19688f7fa8e4852dc724c4b7f82fa4c557549939e99",
    "045571384fb1edb731ca6c1841fe069dd3341c3237944d781360eb5d5ca31051",
    "bbb2478d467bb023716654e2d58870195b2aea4977d6c43545ce6f05e80b5aff",
    "c230137d09b19deb42526ac5282def78f25dae07dc9a872f7f478758b78d330c",
    "f7fc11023cd79576ccee5aafc44fe3b6c51775b7a37c2ed92c6927cd872a34e7",
    "f840bb61c6fc723e1d8afe74d1cc23f7084d78a91b3f41a3083ee494020f152f",
    "0b1d20aff5372d4f173d4da7cc002a9a3ffdc68074907f832200adf063d6361b",
    "5822c1fb7408b8df041c0c5d68de3de093612915e415ea5de746a7774f71ca7d",
    "9a29839557ca3904029986e01b193515bd15ce1c3626b982b226937e1b83bd2b",
)


def _frame() -> Frame:
    return Frame.from_raw(
        RawFrame(bytes(SUPPORTED_FRAME_WIDTH * SUPPORTED_FRAME_HEIGHT * 4),
                 SUPPORTED_FRAME_WIDTH, SUPPORTED_FRAME_HEIGHT, PixelFormat.BGRA8888),
        frame_id=1, captured_monotonic_s=1.0,
    )


def _evaluator(monkeypatch, hashes, occupied=None, confidence=0.0):
    evaluator = ProductionMiningPerceptionEvaluator()
    evaluator._inventory_profile = InventoryFrameProfile(
        profile_id=SUPPORTED_PROFILE_ID,
        frame_width=SUPPORTED_FRAME_WIDTH,
        frame_height=SUPPORTED_FRAME_HEIGHT,
        region=Region(*SUPPORTED_REGION),
        layout=InventoryGridLayout(
            profile_id=SUPPORTED_PROFILE_ID,
            column_stride=SUPPORTED_COLUMN_STRIDE,
            row_stride=SUPPORTED_ROW_STRIDE,
        ),
    )
    analyzer = Mock()
    analyzer.analyze.return_value = SimpleNamespace(
        occupied_slots=occupied, confidence=confidence, slots=(),
    )
    evaluator._inventory_analyzer = analyzer
    monkeypatch.setattr(evaluator, "_current_view_slot_hashes", lambda *args: hashes)
    bootstrap = Mock(return_value=object())
    monkeypatch.setattr(configuration, "inventory_positive_detector_v2_from_profile", bootstrap)
    return evaluator, analyzer, bootstrap


def test_reported_empty_panel_cannot_be_published_as_full(monkeypatch):
    hashes = REPORTED_EMPTY_FRAME_SLOT_HASHES
    assert all(digest not in allowed for digest, allowed in
               zip(hashes, _CURRENT_VIEW_EMPTY_SLOT_RGB_SHA256, strict=True))
    evaluator, analyzer, bootstrap = _evaluator(monkeypatch, hashes)
    frame = _frame()
    state, reason = evaluator._evaluate_packaged_inventory(frame)
    assert state.occupied_slots is None
    assert state.confidence == 0.0
    assert reason == "inventory_v3_unknown"
    analyzer.analyze.assert_called_once_with(frame)
    bootstrap.assert_not_called()
    assert evaluator._session_inventory_detector is None


@pytest.mark.parametrize("prefix", [1, 3, 7, 10, 27, 28])
def test_unrecognized_prefix_is_not_positive_occupancy(monkeypatch, prefix):
    hashes = tuple(
        "0" * 64 if index < prefix else next(iter(allowed))
        for index, allowed in enumerate(_CURRENT_VIEW_EMPTY_SLOT_RGB_SHA256)
    )
    evaluator, analyzer, bootstrap = _evaluator(monkeypatch, hashes)
    state, reason = evaluator._evaluate_packaged_inventory(_frame())
    assert state.occupied_slots is None
    assert reason == "inventory_v3_unknown"
    analyzer.analyze.assert_called_once()
    bootstrap.assert_not_called()


def test_exact_known_empty_still_bootstraps_empty_session(monkeypatch):
    hashes = tuple(next(iter(allowed)) for allowed in _CURRENT_VIEW_EMPTY_SLOT_RGB_SHA256)
    evaluator, analyzer, bootstrap = _evaluator(monkeypatch, hashes)
    state, reason = evaluator._evaluate_packaged_inventory(_frame())
    assert state.occupied_slots == 0
    assert state.confidence == 1.0
    assert reason is None
    analyzer.analyze.assert_not_called()
    bootstrap.assert_called_once()
    assert evaluator._session_inventory_detector is bootstrap.return_value


@pytest.mark.parametrize("occupied", [1, 7, 10, 27, 28])
def test_positive_classifier_result_not_replaced_by_hash_complement(monkeypatch, occupied):
    evaluator, analyzer, bootstrap = _evaluator(
        monkeypatch, REPORTED_EMPTY_FRAME_SLOT_HASHES, occupied=occupied, confidence=0.9,
    )
    state, reason = evaluator._evaluate_packaged_inventory(_frame())
    assert state.occupied_slots == occupied
    assert state.confidence == 0.9
    assert reason is None
    analyzer.analyze.assert_called_once()
    bootstrap.assert_not_called()


def test_existing_session_detector_unknown_cannot_be_overridden_as_full(monkeypatch):
    evaluator, _, bootstrap = _evaluator(monkeypatch, REPORTED_EMPTY_FRAME_SLOT_HASHES)
    detector = Mock()
    detector.detect.return_value = []
    evaluator._session_inventory_detector = detector
    state, reason = evaluator._evaluate_packaged_inventory(_frame())
    assert state.occupied_slots is None
    assert state.confidence == 0.0
    assert reason == "inventory_v2_unknown"
    detector.detect.assert_called_once()
    bootstrap.assert_not_called()


def test_existing_session_classifier_controls_ore_count(monkeypatch):
    evaluator, _, bootstrap = _evaluator(monkeypatch, REPORTED_EMPTY_FRAME_SLOT_HASHES)
    detector = Mock()
    observation = object()
    detector.detect.return_value = [observation]
    evaluator._session_inventory_detector = detector
    monkeypatch.setattr(adapter, "inventory_state_from_observation",
                        lambda obs: InventoryState(8, 28, 0.95))
    state, reason = evaluator._evaluate_packaged_inventory(_frame())
    assert state.occupied_slots == 8
    assert state.confidence == 0.95
    assert reason is None
    bootstrap.assert_not_called()
