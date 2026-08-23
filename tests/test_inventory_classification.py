from __future__ import annotations

from collections.abc import Callable

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.perception.inventory.classification import (
    ClassificationPolicy,
    InventoryClassificationError,
    InventoryObstructionError,
    InventorySlotClassifier,
    ReferenceInventoryClassifier,
    SlotDecision,
    SlotOccupancy,
)
from mining_automation.perception.inventory.geometry import (
    INVENTORY_CAPACITY,
    INVENTORY_COLUMNS,
    INVENTORY_ROWS,
    InventoryGridLayout,
    Region,
)

Rgb = tuple[int, int, int]


def _layout(
    *,
    profile_id: str = "synthetic-contiguous",
    column_stride: int = 32,
    row_stride: int = 32,
) -> InventoryGridLayout:
    return InventoryGridLayout(
        profile_id=profile_id,
        column_stride=column_stride,
        row_stride=row_stride,
    )


def _pixels(width: int, height: int, color: Rgb = (16, 24, 32)) -> list[Rgb]:
    return [color] * (width * height)


def _encode(
    pixels: list[Rgb],
    pixel_format: PixelFormat,
    *,
    alpha: int = 255,
) -> bytes:
    payload = bytearray()
    for red, green, blue in pixels:
        if pixel_format is PixelFormat.GRAY8:
            if not red == green == blue:
                raise ValueError("GRAY8 test pixels must have equal channels")
            payload.append(red)
        elif pixel_format is PixelFormat.RGB888:
            payload.extend((red, green, blue))
        elif pixel_format is PixelFormat.BGR888:
            payload.extend((blue, green, red))
        elif pixel_format is PixelFormat.RGBA8888:
            payload.extend((red, green, blue, alpha))
        elif pixel_format is PixelFormat.BGRA8888:
            payload.extend((blue, green, red, alpha))
        else:  # pragma: no cover - exhaustive enum guard
            raise AssertionError(pixel_format)
    return bytes(payload)


def _frame(
    pixels: list[Rgb],
    width: int,
    height: int,
    pixel_format: PixelFormat = PixelFormat.RGB888,
    *,
    frame_id: int = 1,
    alpha: int = 255,
) -> Frame:
    return Frame.from_raw(
        RawFrame(
            payload=_encode(pixels, pixel_format, alpha=alpha),
            width=width,
            height=height,
            pixel_format=pixel_format,
        ),
        frame_id=frame_id,
        captured_monotonic_s=float(frame_id - 1),
    )


def _paint_core(
    pixels: list[Rgb],
    frame_width: int,
    slot: Region,
    *,
    inset: int = 4,
    color: Rgb = (240, 80, 190),
    changed_pixels: int | None = None,
) -> None:
    coordinates = [
        (x, y)
        for y in range(slot.y + inset, slot.y + slot.height - inset)
        for x in range(slot.x + inset, slot.x + slot.width - inset)
    ]
    limit = len(coordinates) if changed_pixels is None else changed_pixels
    for x, y in coordinates[:limit]:
        pixels[y * frame_width + x] = color


def _paint_rectangle(
    pixels: list[Rgb],
    frame_width: int,
    region: Region,
    *,
    color: Rgb = (255, 255, 255),
) -> None:
    for y in range(region.y, region.y + region.height):
        for x in range(region.x, region.x + region.width):
            pixels[y * frame_width + x] = color


def _classifier(
    *,
    pixel_format: PixelFormat = PixelFormat.RGB888,
    policy: ClassificationPolicy | None = None,
    reference_color: Rgb = (16, 24, 32),
    alpha: int = 255,
    layout: InventoryGridLayout | None = None,
) -> tuple[ReferenceInventoryClassifier, Region, InventoryGridLayout]:
    selected_layout = layout if layout is not None else _layout()
    region = selected_layout.region_at(0, 0)
    reference = _frame(
        _pixels(selected_layout.width, selected_layout.height, reference_color),
        selected_layout.width,
        selected_layout.height,
        pixel_format,
        alpha=alpha,
    )
    return (
        ReferenceInventoryClassifier(reference, region, selected_layout, policy),
        region,
        selected_layout,
    )


def test_empty_reference_produces_28_ordered_empty_decisions_deterministically() -> None:
    classifier, region, layout = _classifier()
    candidate = _frame(_pixels(layout.width, layout.height), layout.width, layout.height)

    first = classifier.classify(candidate, region)
    second = classifier.classify(candidate, region)

    assert first == second
    assert len(first) == INVENTORY_CAPACITY
    assert [decision.index for decision in first] == list(range(INVENTORY_CAPACITY))
    assert [(decision.row, decision.column) for decision in first] == [
        divmod(index, INVENTORY_COLUMNS) for index in range(INVENTORY_CAPACITY)
    ]
    assert tuple(decision.region for decision in first) == layout.all_slot_regions(region)
    assert all(decision.state is SlotOccupancy.EMPTY for decision in first)
    assert all(decision.confidence == 1.0 for decision in first)
    assert all(decision.score == 0.0 for decision in first)
    assert all(decision.changed_fraction == 0.0 for decision in first)


@pytest.mark.parametrize("pixel_format", list(PixelFormat))
def test_all_pixel_formats_classify_the_same_multichannel_pattern(
    pixel_format: PixelFormat,
) -> None:
    gray = pixel_format is PixelFormat.GRAY8
    reference_color = (20, 20, 20) if gray else (12, 34, 56)
    changed_color = (230, 230, 230) if gray else (210, 70, 190)
    classifier, region, layout = _classifier(
        pixel_format=pixel_format,
        reference_color=reference_color,
    )
    pixels = _pixels(layout.width, layout.height, reference_color)
    _paint_core(
        pixels,
        layout.width,
        layout.slot_region(region, 0),
        color=changed_color,
    )
    candidate = _frame(
        pixels,
        layout.width,
        layout.height,
        pixel_format,
        frame_id=2,
    )

    decisions = classifier.classify(candidate, region)

    assert decisions[0].state is SlotOccupancy.OCCUPIED
    assert decisions[0].changed_fraction == 1.0
    assert all(item.state is SlotOccupancy.EMPTY for item in decisions[1:])


@pytest.mark.parametrize(
    "pixel_format",
    [PixelFormat.RGB888, PixelFormat.BGR888, PixelFormat.RGBA8888, PixelFormat.BGRA8888],
)
def test_rgb_and_bgr_channel_orders_canonicalize_to_identical_scores(
    pixel_format: PixelFormat,
) -> None:
    classifier, region, layout = _classifier(
        pixel_format=pixel_format,
        reference_color=(11, 73, 149),
    )
    pixels = _pixels(layout.width, layout.height, (11, 73, 149))
    _paint_core(
        pixels,
        layout.width,
        layout.slot_region(region, 5),
        color=(211, 31, 97),
        changed_pixels=288,
    )
    candidate = _frame(pixels, layout.width, layout.height, pixel_format, frame_id=2)

    decision = classifier.classify(candidate, region)[5]

    assert decision.state is SlotOccupancy.OCCUPIED
    assert decision.changed_fraction == 0.5
    assert decision.score == pytest.approx(0.40764705882352936)


@pytest.mark.parametrize("pixel_format", [PixelFormat.RGBA8888, PixelFormat.BGRA8888])
def test_alpha_is_ignored(pixel_format: PixelFormat) -> None:
    classifier, region, layout = _classifier(
        pixel_format=pixel_format,
        reference_color=(20, 40, 60),
        alpha=0,
    )
    candidate = _frame(
        _pixels(layout.width, layout.height, (20, 40, 60)),
        layout.width,
        layout.height,
        pixel_format,
        frame_id=2,
        alpha=255,
    )

    decisions = classifier.classify(candidate, region)

    assert all(item.state is SlotOccupancy.EMPTY for item in decisions)
    assert all(item.score == 0.0 for item in decisions)


def test_arbitrary_item_patterns_are_not_ore_or_color_specific() -> None:
    classifier, region, layout = _classifier()
    pixels = _pixels(layout.width, layout.height)
    colors = ((220, 25, 30), (30, 220, 25), (25, 30, 220), (230, 180, 35))
    occupied_indices = (0, 9, 18, 27)
    for index, color in zip(occupied_indices, colors, strict=True):
        _paint_core(
            pixels,
            layout.width,
            layout.slot_region(region, index),
            color=color,
            changed_pixels=200 + index,
        )
    candidate = _frame(pixels, layout.width, layout.height, frame_id=2)

    decisions = classifier.classify(candidate, region)

    assert tuple(
        decision.index
        for decision in decisions
        if decision.state is SlotOccupancy.OCCUPIED
    ) == occupied_indices


def test_threshold_boundaries_are_classified_at_half_confidence() -> None:
    policy = ClassificationPolicy(
        core_inset=4,
        pixel_difference_threshold=1,
        empty_max_score=0.125,
        occupied_min_score=0.25,
        minimum_slot_confidence=0.5,
    )
    classifier, region, layout = _classifier(
        policy=policy,
        reference_color=(0, 0, 0),
    )
    pixels = _pixels(layout.width, layout.height, (0, 0, 0))
    _paint_core(
        pixels,
        layout.width,
        layout.slot_region(region, 0),
        color=(255, 255, 255),
        changed_pixels=72,
    )
    _paint_core(
        pixels,
        layout.width,
        layout.slot_region(region, 1),
        color=(255, 255, 255),
        changed_pixels=144,
    )
    _paint_core(
        pixels,
        layout.width,
        layout.slot_region(region, 2),
        color=(255, 255, 255),
        changed_pixels=108,
    )
    candidate = _frame(pixels, layout.width, layout.height, frame_id=2)

    decisions = classifier.classify(candidate, region)

    assert decisions[0].score == 0.125
    assert decisions[0].state is SlotOccupancy.EMPTY
    assert decisions[0].confidence == 0.5
    assert decisions[1].score == 0.25
    assert decisions[1].state is SlotOccupancy.OCCUPIED
    assert decisions[1].confidence == 0.5
    assert decisions[2].score == pytest.approx(0.1875)
    assert decisions[2].state is SlotOccupancy.UNCERTAIN
    assert decisions[2].confidence == pytest.approx(0.0, abs=1e-12)


def test_minimum_confidence_can_conservatively_demote_a_threshold_state() -> None:
    policy = ClassificationPolicy(
        pixel_difference_threshold=1,
        empty_max_score=0.125,
        occupied_min_score=0.25,
        minimum_slot_confidence=0.75,
    )
    classifier, region, layout = _classifier(
        policy=policy,
        reference_color=(0, 0, 0),
    )
    pixels = _pixels(layout.width, layout.height, (0, 0, 0))
    _paint_core(
        pixels,
        layout.width,
        layout.slot_region(region, 0),
        color=(255, 255, 255),
        changed_pixels=144,
    )

    decision = classifier.classify(
        _frame(pixels, layout.width, layout.height, frame_id=2),
        region,
    )[0]

    assert decision.score == 0.25
    assert decision.confidence == 0.5
    assert decision.state is SlotOccupancy.UNCERTAIN


@pytest.mark.parametrize(
    ("policy", "changed_pixels", "expected_state"),
    [
        (
            ClassificationPolicy(
                pixel_difference_threshold=1,
                empty_max_score=0.0,
                occupied_min_score=0.5,
            ),
            0,
            SlotOccupancy.EMPTY,
        ),
        (
            ClassificationPolicy(
                pixel_difference_threshold=1,
                empty_max_score=0.5,
                occupied_min_score=1.0,
            ),
            576,
            SlotOccupancy.OCCUPIED,
        ),
    ],
)
def test_endpoint_score_thresholds_also_have_half_confidence(
    policy: ClassificationPolicy,
    changed_pixels: int,
    expected_state: SlotOccupancy,
) -> None:
    classifier, region, layout = _classifier(
        policy=policy,
        reference_color=(0, 0, 0),
    )
    pixels = _pixels(layout.width, layout.height, (0, 0, 0))
    _paint_core(
        pixels,
        layout.width,
        layout.slot_region(region, 0),
        color=(255, 255, 255),
        changed_pixels=changed_pixels,
    )

    decision = classifier.classify(
        _frame(pixels, layout.width, layout.height, frame_id=2),
        region,
    )[0]

    assert decision.state is expected_state
    assert decision.confidence == 0.5


def test_one_changed_pixel_cannot_turn_a_slot_occupied() -> None:
    classifier, region, layout = _classifier(reference_color=(0, 0, 0))
    pixels = _pixels(layout.width, layout.height, (0, 0, 0))
    _paint_core(
        pixels,
        layout.width,
        layout.slot_region(region, 0),
        color=(255, 255, 255),
        changed_pixels=1,
    )

    decision = classifier.classify(
        _frame(pixels, layout.width, layout.height, frame_id=2),
        region,
    )[0]

    assert decision.changed_fraction == pytest.approx(1 / 576)
    assert decision.state is SlotOccupancy.EMPTY


def test_pixel_delta_exactly_at_threshold_counts_as_changed() -> None:
    policy = ClassificationPolicy(pixel_difference_threshold=24)
    classifier, region, layout = _classifier(
        policy=policy,
        reference_color=(0, 0, 0),
    )
    pixels = _pixels(layout.width, layout.height, (0, 0, 0))
    _paint_core(
        pixels,
        layout.width,
        layout.slot_region(region, 0),
        color=(24, 0, 0),
    )

    decision = classifier.classify(
        _frame(pixels, layout.width, layout.height, frame_id=2),
        region,
    )[0]

    assert decision.changed_fraction == 1.0
    assert decision.state is SlotOccupancy.OCCUPIED


def test_sprite_spill_inside_neighbor_edge_does_not_double_count() -> None:
    guarded_layout = _layout(column_stride=36, row_stride=36)
    classifier, region, layout = _classifier(
        reference_color=(0, 0, 0),
        layout=guarded_layout,
    )
    pixels = _pixels(layout.width, layout.height, (0, 0, 0))
    owner = layout.slot_region(region, 0)
    neighbor = layout.slot_region(region, 1)
    _paint_core(pixels, layout.width, owner, color=(255, 120, 40))
    for y in range(owner.y + 8, owner.y + 24):
        for x in range(owner.x + 30, neighbor.x + classifier.policy.core_inset):
            pixels[y * layout.width + x] = (255, 120, 40)
    candidate = _frame(pixels, layout.width, layout.height, frame_id=2)

    decisions = classifier.classify(candidate, region)

    assert classifier.has_obstruction_guard
    assert classifier.guard_pixel_count > 0
    assert decisions[0].state is SlotOccupancy.OCCUPIED
    assert decisions[1].state is SlotOccupancy.EMPTY
    assert decisions[1].score == 0.0


def test_full_horizontal_sprite_overflow_across_every_slot_is_allowed() -> None:
    guarded_layout = _layout(column_stride=36, row_stride=36)
    classifier, region, layout = _classifier(
        reference_color=(0, 0, 0),
        layout=guarded_layout,
    )
    pixels = _pixels(layout.width, layout.height, (0, 0, 0))
    for slot in layout.all_slot_regions(region):
        _paint_rectangle(
            pixels,
            layout.width,
            Region(
                slot.x,
                slot.y,
                min(36, layout.width - slot.x),
                slot.height,
            ),
        )

    decisions = classifier.classify(
        _frame(pixels, layout.width, layout.height, frame_id=2),
        region,
    )

    assert len(decisions) == INVENTORY_CAPACITY
    assert all(decision.state is SlotOccupancy.OCCUPIED for decision in decisions)


def test_opaque_whole_region_obstruction_is_rejected() -> None:
    guarded_layout = _layout(column_stride=36, row_stride=36)
    classifier, region, layout = _classifier(
        reference_color=(0, 0, 0),
        layout=guarded_layout,
    )
    obstructed = _frame(
        _pixels(layout.width, layout.height, (255, 255, 255)),
        layout.width,
        layout.height,
        frame_id=2,
    )

    with pytest.raises(
        InventoryObstructionError,
        match=r"guard changed fraction 1\.000000 exceeds configured maximum 0\.500000",
    ):
        classifier.classify(obstructed, region)


def test_exact_half_width_opaque_obstruction_is_rejected_by_row_guard() -> None:
    guarded_layout = _layout(column_stride=36, row_stride=36)
    classifier, region, layout = _classifier(
        reference_color=(0, 0, 0),
        layout=guarded_layout,
    )
    pixels = _pixels(layout.width, layout.height, (0, 0, 0))
    _paint_rectangle(
        pixels,
        layout.width,
        Region(0, 0, layout.width // 2, layout.height),
    )

    with pytest.raises(
        InventoryObstructionError,
        match=(
            r"row guard changed fraction 0\.500000 "
            r"exceeds configured maximum 0\.000000"
        ),
    ):
        classifier.classify(
            _frame(pixels, layout.width, layout.height, frame_id=2),
            region,
        )


def test_full_inventory_core_changes_do_not_trip_obstruction_guard() -> None:
    guarded_layout = _layout(column_stride=36, row_stride=36)
    classifier, region, layout = _classifier(
        reference_color=(0, 0, 0),
        layout=guarded_layout,
    )
    pixels = _pixels(layout.width, layout.height, (0, 0, 0))
    for slot in layout.all_slot_regions(region):
        _paint_core(pixels, layout.width, slot, color=(255, 255, 255))

    decisions = classifier.classify(
        _frame(pixels, layout.width, layout.height, frame_id=2),
        region,
    )

    assert len(decisions) == INVENTORY_CAPACITY
    assert all(decision.state is SlotOccupancy.OCCUPIED for decision in decisions)


def test_contiguous_layout_is_supported_but_explicitly_unguarded() -> None:
    classifier, _, _ = _classifier()

    assert not classifier.has_obstruction_guard
    assert classifier.guard_pixel_count == 0
    assert classifier.row_guard_pixel_count == 0


def test_column_gutters_without_row_gutters_are_not_an_obstruction_guard() -> None:
    column_only_layout = _layout(column_stride=36, row_stride=32)
    classifier, _, _ = _classifier(layout=column_only_layout)

    assert classifier.guard_pixel_count > 0
    assert classifier.row_guard_pixel_count == 0
    assert not classifier.has_obstruction_guard


def test_reference_and_configuration_fingerprints_are_deterministic_and_safe() -> None:
    layout = _layout(profile_id=" Synthetic Profile / unsafe? ")
    first, _, _ = _classifier(layout=layout, pixel_format=PixelFormat.RGB888)
    second, _, _ = _classifier(layout=layout, pixel_format=PixelFormat.BGR888)
    third, _, _ = _classifier(layout=layout, pixel_format=PixelFormat.BGRA8888)

    assert first.profile_id == layout.profile_id
    assert first.reference_sha256 == second.reference_sha256 == third.reference_sha256
    assert first.configuration_id == second.configuration_id == third.configuration_id
    assert len(first.reference_sha256) == 64
    assert all(character in "0123456789abcdef" for character in first.reference_sha256)
    assert first.configuration_id.startswith("inventory-synthetic-profile-unsafe-ref-")
    assert all(
        character.isascii()
        and (character.islower() or character.isdigit() or character in ".-_")
        for character in first.configuration_id
    )


def test_fingerprint_changes_with_reference_or_policy() -> None:
    baseline, _, layout = _classifier(reference_color=(10, 20, 30))
    changed_reference, _, _ = _classifier(
        reference_color=(11, 20, 30),
        layout=layout,
    )
    changed_policy, _, _ = _classifier(
        reference_color=(10, 20, 30),
        layout=layout,
        policy=ClassificationPolicy(max_guard_changed_fraction=0.4),
    )
    changed_row_guard_policy, _, _ = _classifier(
        reference_color=(10, 20, 30),
        layout=layout,
        policy=ClassificationPolicy(max_row_guard_changed_fraction=0.1),
    )

    assert baseline.reference_sha256 != changed_reference.reference_sha256
    assert baseline.reference_sha256 != changed_policy.reference_sha256
    assert baseline.reference_sha256 != changed_row_guard_policy.reference_sha256
    assert baseline.configuration_id != changed_reference.configuration_id
    assert baseline.configuration_id != changed_policy.configuration_id
    assert baseline.configuration_id != changed_row_guard_policy.configuration_id


@pytest.mark.parametrize(
    "region_factory, message",
    [
        (lambda layout: Region(0, 0, layout.width - 1, layout.height), "exactly"),
        (lambda layout: Region(1, 0, layout.width, layout.height), "does not fit"),
    ],
)
def test_reference_region_must_have_exact_dimensions_and_fit(
    region_factory: Callable[[InventoryGridLayout], Region],
    message: str,
) -> None:
    layout = _layout()
    reference = _frame(
        _pixels(layout.width, layout.height),
        layout.width,
        layout.height,
    )

    with pytest.raises(InventoryClassificationError, match=message):
        ReferenceInventoryClassifier(reference, region_factory(layout), layout)


def test_candidate_region_must_have_exact_dimensions_and_fit() -> None:
    classifier, region, layout = _classifier()
    frame = _frame(_pixels(layout.width, layout.height), layout.width, layout.height)

    with pytest.raises(InventoryClassificationError, match="exactly"):
        classifier.classify(frame, Region(0, 0, region.width - 1, region.height))
    with pytest.raises(InventoryClassificationError, match="does not fit"):
        classifier.classify(frame, Region(1, 0, region.width, region.height))
    with pytest.raises(InventoryClassificationError, match="must be a Region"):
        classifier.classify(frame, object())  # type: ignore[arg-type]


def test_candidate_frame_may_have_a_different_origin_but_not_wrong_size() -> None:
    classifier, _, layout = _classifier()
    candidate_width = layout.width + 5
    candidate_height = layout.height + 7
    moved_region = layout.region_at(5, 7)
    candidate = _frame(
        _pixels(candidate_width, candidate_height),
        candidate_width,
        candidate_height,
    )

    decisions = classifier.classify(candidate, moved_region)

    assert decisions[0].region == Region(5, 7, 32, 32)
    assert decisions[-1].region == Region(
        5 + (INVENTORY_COLUMNS - 1) * 32,
        7 + (INVENTORY_ROWS - 1) * 32,
        32,
        32,
    )


def test_classifier_is_runtime_protocol_compatible() -> None:
    classifier, _, _ = _classifier()

    assert isinstance(classifier, InventorySlotClassifier)


@pytest.mark.parametrize(
    "values, message",
    [
        ({"core_inset": -1}, "core_inset"),
        ({"core_inset": 16}, "core_inset"),
        ({"core_inset": True}, "core_inset"),
        ({"pixel_difference_threshold": 0}, "pixel_difference_threshold"),
        ({"pixel_difference_threshold": -1}, "pixel_difference_threshold"),
        ({"pixel_difference_threshold": 256}, "pixel_difference_threshold"),
        ({"pixel_difference_threshold": True}, "pixel_difference_threshold"),
        ({"empty_max_score": float("nan")}, "empty_max_score"),
        ({"empty_max_score": -0.1}, "empty_max_score"),
        ({"occupied_min_score": float("inf")}, "occupied_min_score"),
        ({"occupied_min_score": 1.1}, "occupied_min_score"),
        ({"empty_max_score": 0.4, "occupied_min_score": 0.4}, "lower"),
        ({"empty_max_score": 0.5, "occupied_min_score": 0.4}, "lower"),
        ({"minimum_slot_confidence": 0.49}, "at least 0.5"),
        ({"minimum_slot_confidence": float("nan")}, "minimum_slot_confidence"),
        ({"minimum_slot_confidence": 1.01}, "minimum_slot_confidence"),
        ({"max_guard_changed_fraction": -0.01}, "max_guard_changed_fraction"),
        ({"max_guard_changed_fraction": 1.0}, "max_guard_changed_fraction"),
        ({"max_guard_changed_fraction": 1.01}, "max_guard_changed_fraction"),
        ({"max_guard_changed_fraction": float("nan")}, "max_guard_changed_fraction"),
        ({"max_guard_changed_fraction": 10**400}, "max_guard_changed_fraction"),
        ({"max_guard_changed_fraction": True}, "max_guard_changed_fraction"),
        ({"max_row_guard_changed_fraction": -0.01}, "max_row_guard_changed_fraction"),
        ({"max_row_guard_changed_fraction": 1.0}, "max_row_guard_changed_fraction"),
        ({"max_row_guard_changed_fraction": 1.01}, "max_row_guard_changed_fraction"),
        (
            {"max_row_guard_changed_fraction": float("nan")},
            "max_row_guard_changed_fraction",
        ),
        ({"max_row_guard_changed_fraction": True}, "max_row_guard_changed_fraction"),
    ],
)
def test_classification_policy_rejects_invalid_values(
    values: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(InventoryClassificationError, match=message):
        ClassificationPolicy(**values)  # type: ignore[arg-type]


def test_core_inset_rejects_half_or_more_of_authoritative_slot() -> None:
    with pytest.raises(InventoryClassificationError, match=r"core_inset.*\[0, 15\]"):
        ClassificationPolicy(core_inset=16)


def _valid_decision(**overrides: object) -> SlotDecision:
    values: dict[str, object] = {
        "index": 0,
        "row": 0,
        "column": 0,
        "region": Region(0, 0, 32, 32),
        "state": SlotOccupancy.EMPTY,
        "confidence": 1.0,
        "score": 0.0,
        "changed_fraction": 0.0,
    }
    values.update(overrides)
    return SlotDecision(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"index": -1}, "slot index"),
        ({"index": INVENTORY_CAPACITY}, "slot index"),
        ({"index": True}, "slot index"),
        ({"row": INVENTORY_ROWS}, "slot row"),
        ({"column": INVENTORY_COLUMNS}, "slot column"),
        ({"index": 1}, "row-major"),
        ({"region": object()}, "slot region"),
        ({"state": "empty"}, "SlotOccupancy"),
        ({"confidence": -0.1}, "confidence"),
        ({"confidence": float("nan")}, "confidence"),
        ({"score": 1.1}, "score"),
        ({"changed_fraction": True}, "changed_fraction"),
    ],
)
def test_slot_decision_strictly_validates_public_values(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(InventoryClassificationError, match=message):
        _valid_decision(**overrides)
