from __future__ import annotations

import copy

import pytest

from mining_automation.mining_full_proof import (
    MiningFullProofError,
    validate_mining_to_full_result,
)


def _payload() -> dict[str, object]:
    hwnd = 424242
    targets = [
        "varrock-east-iron-northwest",
        "varrock-east-iron-southwest",
    ]
    dispatches = ["dispatch-1", "dispatch-2"]
    events: list[dict[str, object]] = [
        {
            "kind": "initial_clean_observation",
            "iteration": 1,
            "frame_id": 100,
            "frame_sha256": "1" * 64,
            "cycle_id": "cycle-100",
            "inventory_occupied": 26,
            "inventory_confidence": 1.0,
            "publication_status": "ready",
            "state_stop_reason": "none",
            "selected_target_id": targets[0],
            "selected_target_region": [270, 550, 20, 20],
            "window_hwnd": hwnd,
            "foreground_hwnd": hwnd,
            "client_geometry": [1005, 1078],
            "dpi": 96,
            "neutral_cursor_proven": True,
            "matched_landmarks": 6,
            "matched_zones": ["north_west", "north_east", "south_west"],
        }
    ]
    frame = 100
    for iteration, (before, target, dispatch) in enumerate(
        zip((26, 27), targets, dispatches, strict=True),
        start=1,
    ):
        frame += 1
        events.append(
            {
                "kind": "hover_proof",
                "iteration": iteration,
                "attempt_id": f"attempt-{iteration}",
                "source_frame_id": frame - 1,
                "source_frame_sha256": f"{iteration + 1}" * 64,
                "hover_frame_id": frame,
                "hover_frame_sha256": f"{iteration + 2}" * 64,
                "target_id": target,
                "target_region": [270, 550, 20, 20],
                "client_point": [280, 560],
                "screen_point": [350, 700],
                "action_text": "Mine Iron rocks",
                "interaction_proven": True,
                "window_hwnd": hwnd,
                "foreground_hwnd": hwnd,
                "root_window_hwnd": hwnd,
                "cursor_matches_target": True,
            }
        )
        events.append(
            {
                "kind": "single_click_attempt",
                "iteration": iteration,
                "attempt_id": f"attempt-{iteration}",
                "target_id": target,
                "target_region": [270, 550, 20, 20],
                "dispatch_id": dispatch,
                "dispatched_monotonic_s": 1000.0 + frame,
                "click_count": 1,
                "dispatch_succeeded": True,
                "window_hwnd": hwnd,
                "foreground_hwnd": hwnd,
                "root_window_hwnd": hwnd,
                "client_point": [280, 560],
                "screen_point": [350, 700],
                "coordinate_round_trip_exact": True,
            }
        )
        frame += 1
        events.append(
            {
                "kind": "passive_verification",
                "iteration": iteration,
                "index": 1,
                "frame_id": frame,
                "frame_sha256": f"{iteration + 3}" * 64,
                "cycle_id": f"passive-cycle-{iteration}",
                "captured_monotonic_s": 1100.0 + frame,
                "inventory_occupied": before,
                "inventory_confidence": 1.0,
                "inventory_unknown_reason": None,
                "inventory_delta": 0,
                "selected_target_available": None,
            }
        )
        frame += 1
        events.append(
            {
                "kind": "passive_verification",
                "iteration": iteration,
                "index": 2,
                "frame_id": frame,
                "frame_sha256": f"{iteration + 4}" * 64,
                "cycle_id": f"passive-cycle-{iteration}-plus-one",
                "captured_monotonic_s": 1200.0 + frame,
                "inventory_occupied": before + 1,
                "inventory_confidence": 1.0,
                "inventory_unknown_reason": None,
                "inventory_delta": 1,
                "selected_target_available": False,
            }
        )
        frame += 1
        final = iteration == 2
        events.append(
            {
                "kind": "post_progress_clean_reacquisition",
                "iteration": iteration,
                "frame_id": frame,
                "frame_sha256": f"{iteration + 5}" * 64,
                "cycle_id": f"clean-cycle-{iteration}",
                "inventory_occupied": before + 1,
                "inventory_confidence": 1.0,
                "publication_status": "full" if final else "ready",
                "state_stop_reason": "inventory_full" if final else "none",
                "selected_target_id": None if final else targets[iteration],
                "selected_target_region": None if final else [440, 545, 20, 20],
                "window_hwnd": hwnd,
                "foreground_hwnd": hwnd,
                "client_geometry": [1005, 1078],
                "dpi": 96,
                "neutral_cursor_proven": True,
                "matched_landmarks": 6,
                "matched_zones": ["north_west", "north_east", "south_west"],
            }
        )
        events.append(
            {
                "kind": "verified_progress",
                "iteration": iteration,
                "attempt_id": f"attempt-{iteration}",
                "dispatch_id": dispatch,
                "target_id": target,
                "inventory_before": before,
                "inventory_after": before + 1,
                "progress_kind": "resource_depleted_and_inventory_incremented",
                "next_phase": "complete" if final else "ready",
                "next_target_id": None if final else targets[iteration],
            }
        )

    return {
        "run_id": "mining-to-full-test",
        "git_sha": "a" * 40,
        "runelite_hwnd": hwnd,
        "expected_client_geometry": [1005, 1078],
        "expected_dpi": 96,
        "start_inventory": 26,
        "end_inventory": 28,
        "inventory_confidence": 1.0,
        "verified_ores": 2,
        "click_count": 2,
        "attempt_count": 2,
        "target_sequence": targets,
        "distinct_targets": sorted(set(targets)),
        "dispatch_ids": dispatches,
        "phase": "complete",
        "stop_reason": "inventory_full",
        "state_stop_reason": "inventory_full",
        "success": True,
        "detail": "Inventory reached exactly 28/28",
        "events": events,
        "invariants": {
            "resource_threshold": 0.12,
            "resource_landmarks": 6,
            "resource_quorum": 5,
            "resource_zones_required": 3,
            "inventory_floor": 0.8,
            "inventory_capacity": 28,
            "exact_hover_action": "Mine Iron rocks",
            "maximum_clicks_per_attempt": 1,
            "blind_retry": False,
            "navigation_started_on_full": False,
        },
        "evidence_origin": "real_client_live_run",
        "real_client_success": True,
        "raw_frames_committed": False,
    }


def _event(
    payload: dict[str, object],
    kind: str,
    iteration: int,
) -> dict[str, object]:
    events = payload["events"]
    assert isinstance(events, list)
    return next(
        event
        for event in events
        if event["kind"] == kind and event["iteration"] == iteration
    )


def test_complete_exact_real_result_returns_deny_only_receipt() -> None:
    payload = _payload()
    receipt = validate_mining_to_full_result(
        payload,
        expected_git_sha="a" * 40,
        source_result_sha256="b" * 64,
    )
    assert receipt.start_inventory == 26
    assert receipt.end_inventory == 28
    assert receipt.verified_ores == receipt.click_count == 2
    assert receipt.target_sequence == (
        "varrock-east-iron-northwest",
        "varrock-east-iron-southwest",
    )
    assert receipt.real_client_proof is True
    assert receipt.input_authority is False
    assert receipt.navigation_authority is False
    assert receipt.banking_authority is False
    assert receipt.release_authority is False


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (("success",), False),
        (("real_client_success",), False),
        (("evidence_origin",), "synthetic_replay"),
        (("raw_frames_committed",), True),
        (("phase",), "ready"),
        (("stop_reason",), "none"),
        (("end_inventory",), 27),
        (("inventory_confidence",), 0.79),
        (("verified_ores",), 1),
        (("click_count",), 1),
        (("attempt_count",), 3),
        (("expected_client_geometry",), [1005, 1077]),
        (("expected_dpi",), 120),
        (("invariants", "resource_threshold"), 0.13),
        (("invariants", "resource_quorum"), 4),
        (("invariants", "resource_zones_required"), 2),
        (("invariants", "inventory_floor"), 0.79),
        (("invariants", "maximum_clicks_per_attempt"), 2),
        (("invariants", "blind_retry"), True),
        (("invariants", "navigation_started_on_full"), True),
    ),
)
def test_top_level_completion_or_invariant_drift_is_rejected(
    path: tuple[str, ...],
    value: object,
) -> None:
    payload = _payload()
    cursor: dict[str, object] = payload
    for component in path[:-1]:
        child = cursor[component]
        assert isinstance(child, dict)
        cursor = child
    cursor[path[-1]] = value
    with pytest.raises(MiningFullProofError):
        validate_mining_to_full_result(payload)


@pytest.mark.parametrize(
    ("kind", "iteration", "field", "value"),
    (
        ("hover_proof", 1, "action_text", "Mine Copper rocks"),
        ("hover_proof", 1, "interaction_proven", False),
        ("hover_proof", 1, "foreground_hwnd", 99),
        ("hover_proof", 1, "root_window_hwnd", 99),
        ("hover_proof", 1, "cursor_matches_target", False),
        ("single_click_attempt", 1, "click_count", 2),
        ("single_click_attempt", 1, "dispatch_succeeded", False),
        ("single_click_attempt", 1, "foreground_hwnd", 99),
        ("single_click_attempt", 1, "coordinate_round_trip_exact", False),
        ("verified_progress", 1, "inventory_after", 28),
        ("verified_progress", 1, "progress_kind", "none"),
        ("verified_progress", 1, "next_phase", "complete"),
        ("verified_progress", 2, "next_target_id", "29th-target"),
    ),
)
def test_crossed_hover_click_or_progress_evidence_is_rejected(
    kind: str,
    iteration: int,
    field: str,
    value: object,
) -> None:
    payload = _payload()
    _event(payload, kind, iteration)[field] = value
    with pytest.raises(MiningFullProofError):
        validate_mining_to_full_result(payload)


def test_replayed_dispatch_id_is_rejected() -> None:
    payload = _payload()
    payload["dispatch_ids"] = ["dispatch-1", "dispatch-1"]
    _event(payload, "single_click_attempt", 2)["dispatch_id"] = "dispatch-1"
    _event(payload, "verified_progress", 2)["dispatch_id"] = "dispatch-1"
    with pytest.raises(MiningFullProofError, match="dispatch IDs"):
        validate_mining_to_full_result(payload)


def test_missing_iteration_event_is_rejected() -> None:
    payload = _payload()
    events = payload["events"]
    assert isinstance(events, list)
    events[:] = [
        event
        for event in events
        if not (event["kind"] == "hover_proof" and event["iteration"] == 2)
    ]
    with pytest.raises(MiningFullProofError, match="hover_proof event count"):
        validate_mining_to_full_result(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("inventory_occupied", None),
        ("inventory_delta", 2),
        ("inventory_confidence", 0.79),
        ("frame_id", 101),
    ),
)
def test_unknown_ambiguous_low_confidence_or_replayed_passive_evidence_is_rejected(
    field: str,
    value: object,
) -> None:
    payload = _payload()
    events = payload["events"]
    assert isinstance(events, list)
    second_passive = [
        event
        for event in events
        if event["kind"] == "passive_verification" and event["iteration"] == 1
    ][1]
    second_passive[field] = value
    with pytest.raises(MiningFullProofError):
        validate_mining_to_full_result(payload)


def test_stale_or_inconsistent_clean_reacquisition_is_rejected() -> None:
    payload = _payload()
    clean = _event(payload, "post_progress_clean_reacquisition", 1)
    clean["inventory_occupied"] = 26
    with pytest.raises(MiningFullProofError, match="clean reacquisition"):
        validate_mining_to_full_result(payload)


def test_final_clean_state_cannot_expose_a_29th_target() -> None:
    payload = _payload()
    clean = _event(payload, "post_progress_clean_reacquisition", 2)
    clean["selected_target_id"] = "varrock-east-iron-center"
    with pytest.raises(MiningFullProofError, match="final 28/28"):
        validate_mining_to_full_result(payload)


def test_expected_exact_git_head_is_mandatory_when_supplied() -> None:
    with pytest.raises(MiningFullProofError, match="expected exact head"):
        validate_mining_to_full_result(_payload(), expected_git_sha="f" * 40)


def test_post_validation_source_mutation_cannot_change_frozen_receipt() -> None:
    payload = _payload()
    receipt = validate_mining_to_full_result(payload)
    mutated = copy.deepcopy(payload)
    targets = mutated["target_sequence"]
    assert isinstance(targets, list)
    targets[0] = "foreign-target"
    assert receipt.target_sequence[0] == "varrock-east-iron-northwest"
