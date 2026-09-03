from mining_automation.contracts import (
    InventoryState,
    ResourceState,
    SessionState,
    WorldState,
)
from mining_automation.controller import MiningController


def test_mining_controller_selects_first_available_resource_in_state_order() -> None:
    state = WorldState(session_state=SessionState.MINING)
    state.inventory = InventoryState(occupied_slots=5, confidence=1.0)
    state.resources = {
        "a": ResourceState("a", "copper", True, 0.82, (10, 10, 20, 20)),
        "b": ResourceState("b", "copper", True, 0.95, (30, 30, 20, 20)),
        "c": ResourceState("c", "copper", False, 0.99, (50, 50, 20, 20)),
    }

    intent = MiningController().decide(state)

    assert intent is not None
    assert intent.kind == "interact_resource"
    assert intent.target_id == "a"
    assert intent.interaction_region == (10, 10, 20, 20)


def test_unknown_inventory_reacquires_before_resource_interaction() -> None:
    state = WorldState(session_state=SessionState.MINING)
    state.inventory = InventoryState(occupied_slots=None, confidence=0.0)
    state.resources = {
        "a": ResourceState("a", "copper", True, 0.95, (10, 10, 20, 20)),
    }

    intent = MiningController().decide(state)

    assert intent is not None
    assert intent.kind == "reacquire_inventory"
    assert intent.target_id is None
    assert intent.interaction_region is None
    assert intent.expected_observation_kinds == ("inventory_state",)


def test_full_inventory_transitions_toward_bank_workflow() -> None:
    state = WorldState(session_state=SessionState.MINING)
    state.inventory = InventoryState(occupied_slots=28, confidence=1.0)

    intent = MiningController().decide(state)

    assert intent is not None
    assert intent.kind == "begin_navigation_to_bank"


def test_break_state_produces_no_action() -> None:
    state = WorldState(session_state=SessionState.BREAK)
    assert MiningController().decide(state) is None
