from __future__ import annotations

from collections.abc import Callable

import pytest

from mining_automation.contracts import (
    ActionIntent,
    FrameRef,
    InventoryState,
    LocationEstimate,
    Observation,
    ResourceState,
    RoutineKind,
    RoutineSegment,
    SessionConfig,
)

ContractFactory = Callable[[object], object]


def _frame() -> FrameRef:
    return FrameRef(frame_id=0, captured_monotonic_s=0.0, width=1, height=1)


def _segment(duration_s: float = 60.0) -> RoutineSegment:
    return RoutineSegment(kind=RoutineKind.ACTIVE, duration_s=duration_s)


def _intent(
    *,
    interaction_region: tuple[int, int, int, int] | None = None,
    timeout_s: float = 1.0,
) -> ActionIntent:
    return ActionIntent(
        action_id="action-1",
        kind="click",
        target_id="rock-1",
        interaction_region=interaction_region,
        timeout_s=timeout_s,
    )


@pytest.mark.parametrize(
    ("frame_id", "timestamp", "width", "height"),
    [
        (0, 0.0, 1, 1),
        (1, 10, 2, 2),
        (42, 1234.5, 1920, 1080),
    ],
)
def test_frame_ref_accepts_valid_boundaries(
    frame_id: int,
    timestamp: float,
    width: int,
    height: int,
) -> None:
    frame = FrameRef(
        frame_id=frame_id,
        captured_monotonic_s=timestamp,
        width=width,
        height=height,
    )

    assert frame.frame_id == frame_id
    assert frame.captured_monotonic_s == timestamp
    assert frame.width == width
    assert frame.height == height


@pytest.mark.parametrize("frame_id", [-1, True, 1.5])
def test_frame_ref_rejects_invalid_ids(frame_id: object) -> None:
    with pytest.raises(ValueError, match="frame_id must be a non-negative integer"):
        FrameRef(frame_id=frame_id, captured_monotonic_s=0.0, width=1, height=1)  # type: ignore[arg-type]


@pytest.mark.parametrize("timestamp", [-1.0, float("nan"), float("inf"), float("-inf")])
def test_frame_ref_rejects_invalid_timestamps(timestamp: float) -> None:
    with pytest.raises(
        ValueError,
        match="captured_monotonic_s must be finite and non-negative",
    ):
        FrameRef(frame_id=0, captured_monotonic_s=timestamp, width=1, height=1)


@pytest.mark.parametrize("width", [0, -1, True, 1.5])
def test_frame_ref_rejects_invalid_widths(width: object) -> None:
    with pytest.raises(ValueError, match="frame width must be a positive integer"):
        FrameRef(frame_id=0, captured_monotonic_s=0.0, width=width, height=1)  # type: ignore[arg-type]


@pytest.mark.parametrize("height", [0, -1, True, 1.5])
def test_frame_ref_rejects_invalid_heights(height: object) -> None:
    with pytest.raises(ValueError, match="frame height must be a positive integer"):
        FrameRef(frame_id=0, captured_monotonic_s=0.0, width=1, height=height)  # type: ignore[arg-type]


@pytest.mark.parametrize("confidence", [0.0, 0.5, 1.0])
@pytest.mark.parametrize(
    "factory",
    [
        lambda confidence: Observation("resource", _frame(), confidence),
        lambda confidence: ResourceState("rock-1", "iron", True, confidence),
        lambda confidence: InventoryState(None, confidence=confidence),
        lambda confidence: LocationEstimate(None, confidence=confidence),
    ],
    ids=["observation", "resource", "inventory", "location"],
)
def test_confidence_contracts_accept_inclusive_bounds(
    factory: ContractFactory,
    confidence: float,
) -> None:
    factory(confidence)


@pytest.mark.parametrize(
    "confidence",
    [-0.0001, 1.0001, float("nan"), float("inf"), float("-inf"), True],
)
@pytest.mark.parametrize(
    "factory",
    [
        lambda confidence: Observation("resource", _frame(), confidence),
        lambda confidence: ResourceState("rock-1", "iron", True, confidence),
        lambda confidence: InventoryState(None, confidence=confidence),
        lambda confidence: LocationEstimate(None, confidence=confidence),
    ],
    ids=["observation", "resource", "inventory", "location"],
)
def test_confidence_contracts_reject_invalid_values(
    factory: ContractFactory,
    confidence: object,
) -> None:
    with pytest.raises(
        ValueError,
        match="confidence must be finite and between 0.0 and 1.0 inclusive",
    ):
        factory(confidence)


@pytest.mark.parametrize(
    "region",
    [None, (0, 0, 1, 1), (30, 30, 20, 20), (-1920, 0, 1920, 1080)],
)
@pytest.mark.parametrize(
    "factory",
    [
        lambda region: ResourceState("rock-1", "iron", True, 1.0, region),
        lambda region: _intent(interaction_region=region),
    ],
    ids=["resource", "action"],
)
def test_interaction_regions_accept_valid_rectangles(
    factory: ContractFactory,
    region: object,
) -> None:
    factory(region)


@pytest.mark.parametrize(
    "region",
    [
        (),
        (0, 0, 1),
        (0, 0, 1, 1, 1),
        [0, 0, 1, 1],
        (0.0, 0, 1, 1),
        (False, 0, 1, 1),
    ],
)
@pytest.mark.parametrize(
    "factory",
    [
        lambda region: ResourceState("rock-1", "iron", True, 1.0, region),
        lambda region: _intent(interaction_region=region),
    ],
    ids=["resource", "action"],
)
def test_interaction_regions_reject_malformed_values(
    factory: ContractFactory,
    region: object,
) -> None:
    with pytest.raises(ValueError, match="interaction_region must be a tuple of four integers"):
        factory(region)


@pytest.mark.parametrize(
    "region",
    [(0, 0, 0, 1), (0, 0, -1, 1), (0, 0, 1, 0), (0, 0, 1, -1)],
)
@pytest.mark.parametrize(
    "factory",
    [
        lambda region: ResourceState("rock-1", "iron", True, 1.0, region),
        lambda region: _intent(interaction_region=region),
    ],
    ids=["resource", "action"],
)
def test_interaction_regions_require_positive_extents(
    factory: ContractFactory,
    region: object,
) -> None:
    with pytest.raises(
        ValueError,
        match="interaction_region width and height must be positive",
    ):
        factory(region)


@pytest.mark.parametrize(
    ("occupied_slots", "capacity", "expected_full"),
    [(None, 28, None), (0, 1, False), (27, 28, False), (28, 28, True)],
)
def test_inventory_accepts_valid_slot_boundaries(
    occupied_slots: int | None,
    capacity: int,
    expected_full: bool | None,
) -> None:
    inventory = InventoryState(occupied_slots, capacity=capacity, confidence=1.0)

    assert inventory.is_full is expected_full


@pytest.mark.parametrize("capacity", [0, -1, True, 28.0])
def test_inventory_rejects_invalid_capacity(capacity: object) -> None:
    with pytest.raises(ValueError, match="inventory capacity must be a positive integer"):
        InventoryState(None, capacity=capacity)  # type: ignore[arg-type]


@pytest.mark.parametrize("occupied_slots", [True, 1.5])
def test_inventory_rejects_non_integer_slot_counts(occupied_slots: object) -> None:
    with pytest.raises(ValueError, match="occupied_slots must be an integer or None"):
        InventoryState(occupied_slots)  # type: ignore[arg-type]


@pytest.mark.parametrize("occupied_slots", [-1, 29])
def test_inventory_rejects_out_of_range_slot_counts(occupied_slots: int) -> None:
    with pytest.raises(ValueError, match="occupied_slots must be between 0 and capacity inclusive"):
        InventoryState(occupied_slots)


@pytest.mark.parametrize("duration_s", [0.000001, 1.0, 3600.0])
def test_routine_segment_accepts_positive_finite_duration(duration_s: float) -> None:
    assert _segment(duration_s).duration_s == duration_s


@pytest.mark.parametrize(
    "duration_s",
    [0.0, -0.0, -1.0, float("nan"), float("inf"), float("-inf")],
)
def test_routine_segment_rejects_invalid_duration(duration_s: float) -> None:
    with pytest.raises(ValueError, match="routine segment duration must be finite and positive"):
        _segment(duration_s)


@pytest.mark.parametrize("world", [301, 638, 999])
@pytest.mark.parametrize("routine", [(_segment(),), [_segment()]])
def test_session_config_accepts_broad_world_range_and_nonempty_routine(
    world: int,
    routine: object,
) -> None:
    config = SessionConfig(" iron ", " varrock-west ", world, routine)  # type: ignore[arg-type]

    assert config.ore_id == " iron "
    assert config.mine_id == " varrock-west "
    assert config.routine is routine


@pytest.mark.parametrize("ore_id", ["", " ", "\t\n", None, b"iron", 123])
def test_session_config_rejects_invalid_ore_id(ore_id: object) -> None:
    with pytest.raises(ValueError, match="ore_id must be a non-empty string"):
        SessionConfig(ore_id, "varrock-west", 301, (_segment(),))  # type: ignore[arg-type]


@pytest.mark.parametrize("mine_id", ["", " ", "\t\n", None, b"varrock-west", 123])
def test_session_config_rejects_invalid_mine_id(mine_id: object) -> None:
    with pytest.raises(ValueError, match="mine_id must be a non-empty string"):
        SessionConfig("iron", mine_id, 301, (_segment(),))  # type: ignore[arg-type]


@pytest.mark.parametrize("world", [300, 1000, -1, True, 301.0])
def test_session_config_rejects_invalid_worlds(world: object) -> None:
    with pytest.raises(ValueError, match="world must be an integer between 301 and 999 inclusive"):
        SessionConfig("iron", "varrock-west", world, (_segment(),))  # type: ignore[arg-type]


@pytest.mark.parametrize("routine", [(), []])
def test_session_config_rejects_empty_routine(routine: object) -> None:
    with pytest.raises(ValueError, match="routine must contain at least one segment"):
        SessionConfig("iron", "varrock-west", 301, routine)  # type: ignore[arg-type]


@pytest.mark.parametrize("timeout_s", [0.000001, 1.0, 30.0])
def test_action_intent_accepts_positive_finite_timeout(timeout_s: float) -> None:
    assert _intent(timeout_s=timeout_s).timeout_s == timeout_s


@pytest.mark.parametrize(
    "timeout_s",
    [0.0, -0.0, -1.0, float("nan"), float("inf"), float("-inf")],
)
def test_action_intent_rejects_invalid_timeout(timeout_s: float) -> None:
    with pytest.raises(ValueError, match="action timeout must be finite and positive"):
        _intent(timeout_s=timeout_s)
