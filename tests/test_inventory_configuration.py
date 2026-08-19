from __future__ import annotations

import json
from pathlib import Path

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.perception import load_replay_dataset
from mining_automation.perception.inventory import (
    ClassificationPolicy,
    ExactProfileInventoryLocator,
    InventoryClassificationError,
    InventoryDetector,
    InventoryFrameProfile,
    InventoryGridLayout,
    ReferenceInventoryClassifier,
    inventory_detector_from_profile,
)

_LAYOUT = InventoryGridLayout(
    profile_id="reviewed-live-test",
    column_stride=36,
    row_stride=36,
)
_REGION = _LAYOUT.region_at(2, 2)
_FRAME_WIDTH = _REGION.x + _REGION.width + 2
_FRAME_HEIGHT = _REGION.y + _REGION.height + 2


def _profile() -> InventoryFrameProfile:
    return InventoryFrameProfile(
        profile_id=_LAYOUT.profile_id,
        frame_width=_FRAME_WIDTH,
        frame_height=_FRAME_HEIGHT,
        region=_REGION,
        layout=_LAYOUT,
    )


def _frame(
    *,
    width: int = _FRAME_WIDTH,
    height: int = _FRAME_HEIGHT,
) -> Frame:
    return Frame.from_raw(
        RawFrame(
            payload=bytes(width * height),
            width=width,
            height=height,
            pixel_format=PixelFormat.GRAY8,
        ),
        frame_id=1,
        captured_monotonic_s=0.0,
    )


def test_factory_builds_detector_from_profile_as_single_source_of_truth() -> None:
    profile = _profile()
    reference = _frame()

    detector = inventory_detector_from_profile(profile, reference)

    assert isinstance(detector, InventoryDetector)
    assert isinstance(detector.locator, ExactProfileInventoryLocator)
    assert detector.locator.profiles == (profile,)
    assert isinstance(detector.classifier, ReferenceInventoryClassifier)
    assert detector.classifier.layout == profile.layout
    assert detector.classifier.profile_id == profile.profile_id
    observation = detector.detect(reference)[0]
    assert observation.evidence["occupied_slots"] == 0
    assert observation.evidence["profile_id"] == profile.profile_id


def test_factory_accepts_an_owned_frame_from_the_replay_loader(tmp_path: Path) -> None:
    payload_path = tmp_path / "frames" / "empty.gray"
    payload_path.parent.mkdir(parents=True)
    payload_path.write_bytes(bytes(_FRAME_WIDTH * _FRAME_HEIGHT))
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dataset_id": "reviewed-inventory-reference",
                "cases": [
                    {
                        "case_id": "empty-reference",
                        "frame": {
                            "path": "frames/empty.gray",
                            "width": _FRAME_WIDTH,
                            "height": _FRAME_HEIGHT,
                            "pixel_format": "gray8",
                        },
                        "expected_observations": [],
                        "tags": ["inventory", "empty-reference"],
                        "provenance": {"source": "reviewed-test-fixture"},
                        "notes": "Synthetic owned-frame compatibility check.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    replay_reference = load_replay_dataset(manifest_path)[0].frame

    detector = inventory_detector_from_profile(_profile(), replay_reference)

    assert detector.detect(replay_reference)[0].evidence["occupied_slots"] == 0


@pytest.mark.parametrize(
    ("width", "height"),
    [
        (_FRAME_WIDTH + 1, _FRAME_HEIGHT),
        (_FRAME_WIDTH, _FRAME_HEIGHT + 1),
    ],
)
def test_factory_rejects_reference_geometry_that_does_not_match_profile(
    width: int,
    height: int,
) -> None:
    with pytest.raises(InventoryClassificationError, match="geometry must match"):
        inventory_detector_from_profile(_profile(), _frame(width=width, height=height))


def test_factory_rejects_wrong_profile_type() -> None:
    with pytest.raises(TypeError, match="InventoryFrameProfile"):
        inventory_detector_from_profile(object(), _frame())  # type: ignore[arg-type]


def test_factory_rejects_wrong_reference_type() -> None:
    with pytest.raises(InventoryClassificationError, match="empty_reference must be a Frame"):
        inventory_detector_from_profile(_profile(), object())  # type: ignore[arg-type]


def test_factory_forwards_explicit_policy_and_detector_thresholds() -> None:
    policy = ClassificationPolicy(
        core_inset=5,
        pixel_difference_threshold=30,
        empty_max_score=0.07,
        occupied_min_score=0.25,
        minimum_slot_confidence=0.6,
        max_guard_changed_fraction=0.4,
        max_row_guard_changed_fraction=0.01,
    )

    detector = inventory_detector_from_profile(
        _profile(),
        _frame(),
        policy=policy,
        localization_threshold=0.95,
        minimum_slot_confidence=0.85,
    )

    assert isinstance(detector.classifier, ReferenceInventoryClassifier)
    assert detector.classifier.policy is policy
    assert detector.localization_threshold == 0.95
    assert detector.minimum_slot_confidence == 0.85


def test_detector_configuration_identity_pins_the_reviewed_inventory_anchor() -> None:
    reference = _frame()
    baseline = inventory_detector_from_profile(_profile(), reference)
    shifted_profile = InventoryFrameProfile(
        profile_id=_LAYOUT.profile_id,
        frame_width=_FRAME_WIDTH,
        frame_height=_FRAME_HEIGHT,
        region=_LAYOUT.region_at(_REGION.x + 1, _REGION.y),
        layout=_LAYOUT,
    )
    shifted = inventory_detector_from_profile(shifted_profile, reference)

    assert isinstance(baseline.classifier, ReferenceInventoryClassifier)
    assert isinstance(shifted.classifier, ReferenceInventoryClassifier)
    assert baseline.classifier.configuration_id == shifted.classifier.configuration_id
    assert baseline.configuration_id != shifted.configuration_id
