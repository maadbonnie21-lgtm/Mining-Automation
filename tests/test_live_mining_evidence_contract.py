from __future__ import annotations

import json
from pathlib import Path


def _proof() -> dict[str, object]:
    path = (
        Path(__file__).parents[1]
        / "diagnostics"
        / "three-rock-continuous-final7-20260903"
        / "result.json"
    )
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_uninterrupted_real_seven_to_ten_proof_remains_exact() -> None:
    proof = _proof()
    assert proof["success"] is True
    assert proof["stop_reason"] == "three_rock_continuous_proof_verified"

    events = proof["events"]
    assert isinstance(events, list)
    clicks = [event for event in events if event.get("kind") == "single_click"]
    hovers = [event for event in events if event.get("kind") == "hover_proof"]
    clean = [event for event in events if event.get("kind") == "clean_reacquisition"]
    passive = [
        event for event in events if event.get("kind") == "passive_verification"
    ]

    target_order = [
        "varrock-east-iron-northwest",
        "varrock-east-iron-southwest",
        "varrock-east-iron-center",
    ]
    assert [event["target_id"] for event in clicks] == target_order
    assert [event["target_id"] for event in hovers] == target_order
    assert len(clicks) == len(hovers) == 3
    assert [event["click_count"] for event in clicks] == [1, 2, 3]
    assert all(event["proven_mine_iron_rocks"] is True for event in hovers)

    for event in clicks:
        audit = event["audit"]
        assert audit["target_hwnd"] == audit["foreground_hwnd"]
        assert audit["foreground_matches_target"] is True
        assert audit["coordinate_round_trip_exact"] is True
        assert audit["detector_client_point"] == audit["reverse_detector_client_point"]

    assert [event["inventory"] for event in clean] == [7, 8, 9]
    assert all(event["inventory_confidence"] == 1.0 for event in clean)
    assert all(event["resource_view"] == "supported" for event in clean)
    assert any(event["inventory"] == 8 for event in passive)
    assert any(event["inventory"] == 9 for event in passive)
    assert any(event["inventory"] == 10 for event in passive)

    numeric_inventory = [
        event["inventory"]
        for event in events
        if isinstance(event.get("inventory"), int)
    ]
    assert min(numeric_inventory) == 7
    assert max(numeric_inventory) == 10
    assert max(numeric_inventory) - min(numeric_inventory) == len(clicks)


def test_real_proof_preserves_resource_and_input_invariants() -> None:
    proof = _proof()
    events = proof["events"]
    assert isinstance(events, list)

    clean = [event for event in events if event.get("kind") == "clean_reacquisition"]
    assert len(clean) == 3
    for event in clean:
        diagnoses = event["pose_diagnoses"]
        selected_pose = event["pose"]
        selected = diagnoses[selected_pose]
        assert selected["validated"] is True
        assert selected["matched"] == 6
        assert set(selected["zones"]) == {
            "north_west",
            "north_east",
            "south_west",
        }
        assert max(selected["distances"].values()) <= 0.12

    clicks = [event for event in events if event.get("kind") == "single_click"]
    dispatch_ids = [event["dispatch_id"] for event in clicks]
    assert len(dispatch_ids) == len(set(dispatch_ids)) == 3
    assert all(event["audit"]["target_hwnd"] > 0 for event in clicks)
