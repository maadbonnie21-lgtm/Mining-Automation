from __future__ import annotations

import inspect

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.perception.inventory import (
    INVENTORY_POSITIVE_V2_CALIBRATION_SHA256,
    InventoryClassificationError,
    InventoryFrameProfile,
    InventoryGridLayout,
    InventoryObstructionError,
    InventoryPositiveDetectorV2,
    PositiveReferenceInventoryClassifierV2,
    Region,
    SlotOccupancy,
    inventory_detector_from_profile,
    inventory_positive_detector_v2_from_profile,
)


def _layout(*, row_stride: int = 36) -> InventoryGridLayout:
    return InventoryGridLayout(
        profile_id="positive-v2-synthetic",
        column_stride=36,
        row_stride=row_stride,
    )


def _frame(payload: bytes, width: int, height: int, *, frame_id: int = 1) -> Frame:
    return Frame.from_raw(
        RawFrame(
            payload=payload,
            width=width,
            height=height,
            pixel_format=PixelFormat.GRAY8,
        ),
        frame_id=frame_id,
        captured_monotonic_s=float(frame_id),
    )


def _profile_and_reference(
    *, row_stride: int = 36
) -> tuple[InventoryFrameProfile, Frame]:
    layout = _layout(row_stride=row_stride)
    region = layout.region_at(0, 0)
    profile = InventoryFrameProfile(
        profile_id=layout.profile_id,
        frame_width=layout.width,
        frame_height=layout.height,
        region=region,
        layout=layout,
    )
    return profile, _frame(bytes(layout.width * layout.height), layout.width, layout.height)


def _candidate_with_active_cells(
    profile: InventoryFrameProfile,
    active_cells: int,
    *,
    slot_index: int = 0,
) -> Frame:
    pixels = bytearray(profile.frame_width * profile.frame_height)
    slot = profile.layout.slot_region(profile.region, slot_index)
    for cell in range(active_cells):
        cell_row, cell_column = divmod(cell, 3)
        for y in range(slot.y + 4 + cell_row * 8, slot.y + 4 + (cell_row + 1) * 8):
            for x in range(
                slot.x + 4 + cell_column * 8,
                slot.x + 4 + (cell_column + 1) * 8,
            ):
                pixels[y * profile.frame_width + x] = 40
    return _frame(bytes(pixels), profile.frame_width, profile.frame_height, frame_id=2)


def _bgra_frame(
    rgb: tuple[int, int, int],
    alpha: int,
    width: int,
    height: int,
    *,
    frame_id: int,
) -> Frame:
    red, green, blue = rgb
    return Frame.from_raw(
        RawFrame(
            payload=bytes((blue, green, red, alpha)) * (width * height),
            width=width,
            height=height,
            pixel_format=PixelFormat.BGRA8888,
        ),
        frame_id=frame_id,
        captured_monotonic_s=float(frame_id),
    )


def test_v2_factory_has_fixed_distinct_identity_and_unchanged_publication_floor() -> None:
    profile, reference = _profile_and_reference()
    v1 = inventory_detector_from_profile(profile, reference)
    v2 = inventory_positive_detector_v2_from_profile(profile, reference)

    assert isinstance(v2, InventoryPositiveDetectorV2)
    assert isinstance(v2.classifier, PositiveReferenceInventoryClassifierV2)
    assert v1.metadata.detector_id == "inventory-baseline"
    assert v1.metadata.version == "1.0.0"
    assert v2.metadata.detector_id == "inventory-positive-v2"
    assert v2.metadata.version == "2.0.0"
    assert v2.configuration_id != v1.configuration_id
    assert v2.classifier.configuration_id != v1.classifier.configuration_id
    assert v2.classifier.calibration_evidence_sha256 == (
        INVENTORY_POSITIVE_V2_CALIBRATION_SHA256
    )
    assert v1.minimum_slot_confidence == v2.minimum_slot_confidence == 0.8
    assert list(inspect.signature(inventory_positive_detector_v2_from_profile).parameters) == [
        "profile",
        "empty_reference",
    ]


def test_v2_empty_reference_remains_exact_and_deterministic() -> None:
    profile, reference = _profile_and_reference()
    detector = inventory_positive_detector_v2_from_profile(profile, reference)

    first = detector.detect(reference)[0]
    second = detector.detect(reference)[0]

    assert first == second
    assert first.evidence["label"] == "empty"
    assert first.evidence["occupied_slots"] == 0
    assert first.confidence == 1.0
    assert all(item["confidence"] == 1.0 for item in first.evidence["slots"])


@pytest.mark.parametrize(
    ("active_cells", "expected_confidence", "known"),
    [
        (3, 0.5726495726495726, False),
        (6, 0.7863247863247863, False),
        (7, 0.8575498575498576, True),
        (8, 0.9287749287749287, True),
        (9, 1.0, True),
    ],
)
def test_v2_requires_distributed_spatial_support_above_the_unchanged_floor(
    active_cells: int,
    expected_confidence: float,
    known: bool,
) -> None:
    profile, reference = _profile_and_reference()
    detector = inventory_positive_detector_v2_from_profile(profile, reference)
    candidate = _candidate_with_active_cells(profile, active_cells)
    classifier = detector.classifier
    assert isinstance(classifier, PositiveReferenceInventoryClassifierV2)

    first = classifier.analyze(candidate, profile.region)[0]
    observation = detector.detect(candidate)[0]

    assert first.v1_state is SlotOccupancy.OCCUPIED
    assert first.v2_state is (
        SlotOccupancy.OCCUPIED if known else SlotOccupancy.UNCERTAIN
    )
    assert first.active_spatial_cells == active_cells
    assert first.spatial_cell_changed_pixels == tuple(
        64 if index < active_cells else 0 for index in range(9)
    )
    assert first.v2_confidence == pytest.approx(expected_confidence)
    if known:
        assert observation.evidence["occupied_slots"] == 1
        assert observation.confidence >= 0.8
    else:
        assert observation.evidence["occupied_slots"] is None
        assert observation.confidence == 0.0
        assert "uncertain_slots" in str(observation.evidence["reason"])


def test_v2_three_cell_diagonal_remains_below_publication_floor() -> None:
    profile, reference = _profile_and_reference()
    detector = inventory_positive_detector_v2_from_profile(profile, reference)
    pixels = bytearray(profile.frame_width * profile.frame_height)
    slot = profile.layout.slot_region(profile.region, 0)
    for cell in (0, 4, 8):
        cell_row, cell_column = divmod(cell, 3)
        for y in range(slot.y + 4 + cell_row * 8, slot.y + 12 + cell_row * 8):
            for x in range(
                slot.x + 4 + cell_column * 8,
                slot.x + 12 + cell_column * 8,
            ):
                pixels[y * profile.frame_width + x] = 40
    candidate = _frame(
        bytes(pixels), profile.frame_width, profile.frame_height, frame_id=2
    )
    classifier = detector.classifier
    assert isinstance(classifier, PositiveReferenceInventoryClassifierV2)

    feature = classifier.analyze(candidate, profile.region)[0]
    observation = detector.detect(candidate)[0]

    assert feature.v1_state is SlotOccupancy.OCCUPIED
    assert feature.active_coarse_rows == (True, True, True)
    assert feature.active_coarse_columns == (True, True, True)
    assert feature.distributed_support is True
    assert feature.active_spatial_cells == 3
    assert feature.v2_confidence == pytest.approx(0.5726495726495726)
    assert observation.evidence["occupied_slots"] is None
    assert observation.confidence == 0.0


def test_v2_does_not_promote_raw_ambiguous_evidence() -> None:
    profile, reference = _profile_and_reference()
    detector = inventory_positive_detector_v2_from_profile(profile, reference)
    pixels = bytearray(profile.frame_width * profile.frame_height)
    slot = profile.layout.slot_region(profile.region, 0)
    # A distributed but small 108-pixel change falls inside the unchanged raw
    # 0.08..0.22 uncertainty band.
    for cell in range(9):
        row, column = divmod(cell, 3)
        for offset in range(12):
            y = slot.y + 4 + row * 8 + offset // 4
            x = slot.x + 4 + column * 8 + offset % 4
            pixels[y * profile.frame_width + x] = 40
    candidate = _frame(bytes(pixels), profile.frame_width, profile.frame_height, frame_id=2)
    classifier = detector.classifier
    assert isinstance(classifier, PositiveReferenceInventoryClassifierV2)

    feature = classifier.analyze(candidate, profile.region)[0]
    observation = detector.detect(candidate)[0]

    assert 0.08 < feature.raw_score < 0.22
    assert feature.active_spatial_cells == 9
    assert feature.v2_state is SlotOccupancy.UNCERTAIN
    assert observation.evidence["occupied_slots"] is None
    assert observation.confidence == 0.0
    assert "uncertain_slots" in str(observation.evidence["reason"])


def test_v2_tiny_local_change_never_becomes_a_false_occupied_slot() -> None:
    profile, reference = _profile_and_reference()
    detector = inventory_positive_detector_v2_from_profile(profile, reference)
    pixels = bytearray(profile.frame_width * profile.frame_height)
    slot = profile.layout.slot_region(profile.region, 0)
    pixels[(slot.y + 4) * profile.frame_width + slot.x + 4] = 255
    candidate = _frame(bytes(pixels), profile.frame_width, profile.frame_height, frame_id=2)

    observation = detector.detect(candidate)[0]

    assert observation.evidence["occupied_slots"] == 0
    assert observation.evidence["label"] == "empty"


def test_v2_wide_sprite_spill_fails_closed_without_double_counting() -> None:
    profile, reference = _profile_and_reference()
    detector = inventory_positive_detector_v2_from_profile(profile, reference)
    pixels = bytearray(profile.frame_width * profile.frame_height)
    owner = profile.layout.slot_region(profile.region, 0)
    neighbor = profile.layout.slot_region(profile.region, 1)
    # Seven owned core cells make slot 0 definitive. Spill crosses its border,
    # the column gutter, and four pixels into slot 1, but never reaches slot
    # 1's inset ownership core.
    owned = _candidate_with_active_cells(profile, 7)
    pixels[:] = owned.payload
    for y in range(owner.y + 8, owner.y + 24):
        for x in range(owner.x + 28, neighbor.x + 4):
            pixels[y * profile.frame_width + x] = 255
    candidate = _frame(bytes(pixels), profile.frame_width, profile.frame_height, frame_id=3)

    observation = detector.detect(candidate)[0]

    assert observation.evidence["occupied_slots"] is None
    assert observation.confidence == 0.0
    assert "strong non-slot guard pixels changed" in str(observation.evidence["reason"])


@pytest.mark.parametrize(
    ("edge", "offset"),
    [
        ("top", (16, 0)),
        ("right", (31, 16)),
        ("bottom", (16, 31)),
        ("left", (0, 16)),
    ],
)
def test_v2_slot_perimeter_boundary_fails_closed_on_every_edge(
    edge: str,
    offset: tuple[int, int],
) -> None:
    del edge
    profile, reference = _profile_and_reference()
    detector = inventory_positive_detector_v2_from_profile(profile, reference)
    owned = _candidate_with_active_cells(profile, 7)
    slot = profile.layout.slot_region(profile.region, 0)
    x_offset, y_offset = offset

    below = bytearray(owned.payload)
    below[(slot.y + y_offset) * profile.frame_width + slot.x + x_offset] = 60
    below_observation = detector.detect(
        _frame(bytes(below), profile.frame_width, profile.frame_height, frame_id=3)
    )[0]
    assert below_observation.evidence["occupied_slots"] == 1

    boundary = bytearray(owned.payload)
    boundary[(slot.y + y_offset) * profile.frame_width + slot.x + x_offset] = 61
    boundary_observation = detector.detect(
        _frame(bytes(boundary), profile.frame_width, profile.frame_height, frame_id=4)
    )[0]
    assert boundary_observation.evidence["occupied_slots"] is None
    assert boundary_observation.confidence == 0.0
    assert "perimeter" in str(boundary_observation.evidence["reason"])


def test_v2_non_prefix_occupied_mask_fails_closed() -> None:
    profile, reference = _profile_and_reference()
    detector = inventory_positive_detector_v2_from_profile(profile, reference)
    candidate = _candidate_with_active_cells(profile, 7, slot_index=1)

    observation = detector.detect(candidate)[0]

    assert observation.evidence["occupied_slots"] is None
    assert observation.confidence == 0.0
    assert "row-major-prefix" in str(observation.evidence["reason"])


def test_v2_still_ignores_alpha() -> None:
    layout = _layout()
    region = layout.region_at(0, 0)
    profile = InventoryFrameProfile(
        profile_id=layout.profile_id,
        frame_width=layout.width,
        frame_height=layout.height,
        region=region,
        layout=layout,
    )
    reference = _bgra_frame((20, 30, 40), 0, layout.width, layout.height, frame_id=1)
    candidate = _bgra_frame((20, 30, 40), 255, layout.width, layout.height, frame_id=2)
    detector = inventory_positive_detector_v2_from_profile(profile, reference)

    observation = detector.detect(candidate)[0]

    assert observation.evidence["occupied_slots"] == 0
    assert observation.confidence == 1.0


def test_v2_preserves_row_gutter_obstruction_rejection() -> None:
    profile, reference = _profile_and_reference()
    detector = inventory_positive_detector_v2_from_profile(profile, reference)
    classifier = detector.classifier
    assert isinstance(classifier, PositiveReferenceInventoryClassifierV2)
    pixels = bytearray(profile.frame_width * profile.frame_height)
    pixels[32 * profile.frame_width] = 255
    candidate = _frame(bytes(pixels), profile.frame_width, profile.frame_height, frame_id=2)

    with pytest.raises(InventoryObstructionError, match="row guard changed fraction"):
        classifier.classify(candidate, profile.region)
    observation = detector.detect(candidate)[0]
    assert observation.evidence["occupied_slots"] is None
    assert observation.confidence == 0.0
    assert "inventory_obstructed" in str(observation.evidence["reason"])


def test_v2_layout_without_row_gutter_cannot_publish() -> None:
    profile, reference = _profile_and_reference(row_stride=32)
    detector = inventory_positive_detector_v2_from_profile(profile, reference)

    observation = detector.detect(reference)[0]

    assert observation.evidence["occupied_slots"] is None
    assert observation.confidence == 0.0
    assert "obstruction_guard_unavailable" in str(observation.evidence["reason"])


def test_v2_rejects_wrong_candidate_region_geometry() -> None:
    profile, reference = _profile_and_reference()
    detector = inventory_positive_detector_v2_from_profile(profile, reference)
    classifier = detector.classifier
    assert isinstance(classifier, PositiveReferenceInventoryClassifierV2)

    with pytest.raises(InventoryClassificationError, match="must be exactly"):
        classifier.classify(reference, Region(0, 0, profile.region.width - 1, 248))


def test_v2_configuration_changes_with_reference_and_profile() -> None:
    profile, reference = _profile_and_reference()
    baseline = inventory_positive_detector_v2_from_profile(profile, reference)
    changed_offset = 4 * reference.width + 4
    changed_reference = _frame(
        (
            reference.payload[:changed_offset]
            + bytes([1])
            + reference.payload[changed_offset + 1 :]
        ),
        reference.width,
        reference.height,
    )
    changed = inventory_positive_detector_v2_from_profile(profile, changed_reference)
    shifted_profile = InventoryFrameProfile(
        profile_id="positive-v2-shifted",
        frame_width=profile.frame_width,
        frame_height=profile.frame_height,
        region=profile.region,
        layout=InventoryGridLayout(
            profile_id="positive-v2-shifted",
            column_stride=36,
            row_stride=36,
        ),
    )
    shifted = inventory_positive_detector_v2_from_profile(shifted_profile, reference)

    assert len(
        {
            baseline.configuration_id,
            changed.configuration_id,
            shifted.configuration_id,
        }
    ) == 3


@pytest.mark.parametrize(
    ("localization_threshold", "minimum_slot_confidence", "message"),
    [
        (0.89, 0.8, "localization_threshold is frozen"),
        (0.9, 0.79, "minimum_slot_confidence is frozen"),
    ],
)
def test_v2_direct_construction_cannot_weaken_frozen_thresholds(
    localization_threshold: float,
    minimum_slot_confidence: float,
    message: str,
) -> None:
    profile, reference = _profile_and_reference()
    valid = inventory_positive_detector_v2_from_profile(profile, reference)

    with pytest.raises(ValueError, match=message):
        InventoryPositiveDetectorV2(
            locator=valid.locator,
            classifier=valid.classifier,
            localization_threshold=localization_threshold,
            minimum_slot_confidence=minimum_slot_confidence,
        )
