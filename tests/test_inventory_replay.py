from __future__ import annotations

import json
from pathlib import Path

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.perception import evaluate_dataset, load_replay_dataset
from mining_automation.perception.errors import CorruptFixtureError
from mining_automation.perception.inventory.adapter import (
    inventory_state_from_observation,
)
from mining_automation.perception.inventory.classification import (
    ReferenceInventoryClassifier,
)
from mining_automation.perception.inventory.detector import InventoryDetector
from mining_automation.perception.inventory.geometry import InventoryGridLayout, Region
from mining_automation.perception.inventory.localization import (
    ExactProfileInventoryLocator,
    InventoryFrameProfile,
)

_BACKGROUND = (24, 27, 31)
_LAYOUT = InventoryGridLayout(
    profile_id="synthetic-replay-4x7",
    column_stride=36,
    row_stride=36,
)
_REGION = _LAYOUT.region_at(2, 2)
_FRAME_WIDTH = _REGION.x + _REGION.width + 2
_FRAME_HEIGHT = _REGION.y + _REGION.height + 2


def _rgb_payload() -> bytearray:
    return bytearray(_BACKGROUND * (_FRAME_WIDTH * _FRAME_HEIGHT))


def _paint_rectangle(
    payload: bytearray,
    region: Region,
    colour: tuple[int, int, int],
) -> None:
    for y in range(region.y, region.y + region.height):
        for x in range(region.x, region.x + region.width):
            offset = (y * _FRAME_WIDTH + x) * 3
            payload[offset : offset + 3] = bytes(colour)


def _paint_occupied(payload: bytearray, index: int) -> None:
    slot = _LAYOUT.slot_region(_REGION, index)
    # Fill the complete 24x24 ownership core. Colours vary by slot and are not
    # tied to an ore or item identity.
    colour = (
        180 + index % 70,
        90 + (index * 7) % 100,
        55 + (index * 11) % 110,
    )
    _paint_rectangle(
        payload,
        Region(slot.x + 4, slot.y + 4, 24, 24),
        colour,
    )


def _paint_uncertain(payload: bytearray, index: int) -> None:
    slot = _LAYOUT.slot_region(_REGION, index)
    # Four changed rows produce a deterministic score inside the policy's
    # uncertainty band: the evidence is retained but no count is published.
    _paint_rectangle(
        payload,
        Region(slot.x + 4, slot.y + 4, 24, 4),
        (220, 150, 80),
    )


def _case_payload(case_id: str) -> bytes:
    payload = _rgb_payload()
    if case_id == "partial":
        for index in (0, 13, 27):
            _paint_occupied(payload, index)
    elif case_id == "full":
        for index in range(28):
            _paint_occupied(payload, index)
    elif case_id == "uncertain":
        _paint_occupied(payload, 0)
        _paint_uncertain(payload, 5)
    elif case_id == "boundary-spill":
        _paint_occupied(payload, 0)
        first = _LAYOUT.slot_region(_REGION, 0)
        # Simulate a 36-pixel-wide icon spilling through the gutter and into
        # the border of slot 1. Slot 1's inset ownership core begins at x=42,
        # so it must remain empty.
        _paint_rectangle(
            payload,
            Region(first.x + 30, first.y + 8, 10, 16),
            (245, 210, 30),
        )
    elif case_id == "obstructed":
        # A same-size opaque panel, tooltip, or wrong tab must not look like 28
        # occupied slots merely because every slot differs from the reference.
        _paint_rectangle(payload, _REGION, (235, 235, 235))
    elif case_id == "partial-obstruction":
        # Cover exactly half of the panel. A simple aggregate guard with a 50%
        # maximum would accept this boundary and fabricate 14 occupied slots;
        # row-gutter validation must fail it closed instead.
        _paint_rectangle(
            payload,
            Region(_REGION.x, _REGION.y, _REGION.width // 2, _REGION.height),
            (235, 235, 235),
        )
    elif case_id != "empty":  # pragma: no cover - test helper guard
        raise AssertionError(f"unknown synthetic case {case_id}")
    return bytes(payload)


def _owned_frame(payload: bytes, *, frame_id: int = 100) -> Frame:
    return Frame.from_raw(
        RawFrame(
            payload=payload,
            width=_FRAME_WIDTH,
            height=_FRAME_HEIGHT,
            pixel_format=PixelFormat.RGB888,
        ),
        frame_id=frame_id,
        captured_monotonic_s=float(frame_id),
    )


def _detector() -> InventoryDetector:
    reference = _owned_frame(_case_payload("empty"))
    profile = InventoryFrameProfile(
        profile_id=_LAYOUT.profile_id,
        frame_width=_FRAME_WIDTH,
        frame_height=_FRAME_HEIGHT,
        region=_REGION,
        layout=_LAYOUT,
    )
    return InventoryDetector(
        locator=ExactProfileInventoryLocator((profile,)),
        classifier=ReferenceInventoryClassifier(reference, _REGION, _LAYOUT),
    )


def _manifest_case(case_id: str, label: str, *, confidence_min: float) -> dict[str, object]:
    confidence_max = 0.0 if label == "unknown" else 1.0
    return {
        "case_id": case_id,
        "frame": {
            "path": f"frames/{case_id}.rgb",
            "width": _FRAME_WIDTH,
            "height": _FRAME_HEIGHT,
            "pixel_format": "rgb888",
        },
        "expected_observations": [
            {
                "kind": "inventory_state",
                "label": label,
                "region": list(_REGION.as_tuple()),
                "confidence": {"min": confidence_min, "max": confidence_max},
            }
        ],
        "tags": ["inventory", "synthetic", label],
        "provenance": {"source": "generated-unit-test", "issue": "9"},
        "notes": "No live-client screenshot; deterministic generated RGB pixels.",
    }


def _write_dataset(tmp_path: Path) -> Path:
    specifications = (
        ("empty", "empty", 0.8),
        ("partial", "partial", 0.8),
        ("full", "full", 0.8),
        ("uncertain", "unknown", 0.0),
        ("boundary-spill", "partial", 0.8),
        ("obstructed", "unknown", 0.0),
        ("partial-obstruction", "unknown", 0.0),
    )
    cases = []
    for case_id, label, minimum in specifications:
        case = _manifest_case(case_id, label, confidence_min=minimum)
        cases.append(case)
        target = tmp_path / "frames" / f"{case_id}.rgb"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_case_payload(case_id))

    manifest = {
        "schema_version": 1,
        "dataset_id": "inventory-synthetic-regressions",
        "cases": cases,
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_inventory_replay_evaluates_known_unknown_and_boundary_cases(
    tmp_path: Path,
) -> None:
    dataset = load_replay_dataset(_write_dataset(tmp_path))
    detector = _detector()

    report = evaluate_dataset(dataset, [detector])

    assert report.passed
    assert report.cases_run == 7
    assert report.cases_failed == 0
    states = [
        inventory_state_from_observation(detector.detect(sample.frame)[0])
        for sample in dataset
    ]
    assert [state.occupied_slots for state in states] == [
        0,
        3,
        28,
        None,
        1,
        None,
        None,
    ]
    assert [state.is_full for state in states] == [
        False,
        False,
        True,
        None,
        False,
        None,
        None,
    ]
    assert [state.confidence for state in states if state.occupied_slots is None] == [
        0.0,
        0.0,
        0.0,
    ]


def test_inventory_replay_report_and_observations_are_repeatable(tmp_path: Path) -> None:
    dataset = load_replay_dataset(_write_dataset(tmp_path))
    detector = _detector()

    first_report = evaluate_dataset(dataset, [detector]).to_json()
    second_report = evaluate_dataset(dataset, [detector]).to_json()
    first_observations = tuple(detector.detect(sample.frame) for sample in dataset)
    second_observations = tuple(detector.detect(sample.frame) for sample in dataset)

    assert first_report == second_report
    assert first_observations == second_observations


def test_inventory_replay_rejects_a_wrong_size_payload(tmp_path: Path) -> None:
    manifest_path = _write_dataset(tmp_path)
    (tmp_path / "frames" / "partial.rgb").write_bytes(b"too short")

    with pytest.raises(CorruptFixtureError, match="payload size"):
        load_replay_dataset(manifest_path)
