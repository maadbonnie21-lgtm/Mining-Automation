from __future__ import annotations

import math
from collections.abc import Sequence

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.perception.inventory.geometry import (
    INVENTORY_CAPACITY,
    INVENTORY_COLUMNS,
    INVENTORY_ROWS,
    INVENTORY_SLOT_SIZE,
    InventoryGridLayout,
    Region,
)
from mining_automation.perception.inventory.localization import (
    ExactProfileInventoryLocator,
    InventoryFrameProfile,
    InventoryLocalization,
    InventoryRegionLocator,
)


def _layout(
    *,
    profile_id: str = "synthetic-fixed",
    column_stride: int = 40,
    row_stride: int = 36,
) -> InventoryGridLayout:
    return InventoryGridLayout(
        profile_id=profile_id,
        column_stride=column_stride,
        row_stride=row_stride,
    )


def _profile(
    *,
    profile_id: str = "synthetic-fixed",
    frame_width: int = 800,
    frame_height: int = 600,
    x: int = 600,
    y: int = 300,
) -> InventoryFrameProfile:
    layout = _layout(profile_id=profile_id)
    return InventoryFrameProfile(
        profile_id=profile_id,
        frame_width=frame_width,
        frame_height=frame_height,
        region=layout.region_at(x, y),
        layout=layout,
    )


def _frame(width: int = 800, height: int = 600) -> Frame:
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


def test_inventory_constants_describe_the_osrs_grid() -> None:
    assert (INVENTORY_COLUMNS, INVENTORY_ROWS) == (4, 7)
    assert INVENTORY_CAPACITY == 28
    assert INVENTORY_SLOT_SIZE == 32


def test_region_tuple_and_exact_fit() -> None:
    region = Region(2, 3, 5, 7)

    assert region.as_tuple() == (2, 3, 5, 7)
    assert region.fits(7, 10)
    assert not region.fits(6, 10)
    assert not region.fits(7, 9)


@pytest.mark.parametrize("field", ["x", "y", "width", "height"])
@pytest.mark.parametrize("value", [True, 1.5, "1", None])
def test_region_rejects_non_integer_components(field: str, value: object) -> None:
    values: dict[str, object] = {"x": 0, "y": 0, "width": 1, "height": 1}
    values[field] = value

    with pytest.raises(TypeError, match=field):
        Region(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ((-1, 0, 1, 1), "x must be >= 0"),
        ((0, -1, 1, 1), "y must be >= 0"),
        ((0, 0, 0, 1), "width must be > 0"),
        ((0, 0, 1, -1), "height must be > 0"),
    ],
)
def test_region_rejects_invalid_ranges(
    values: tuple[int, int, int, int], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        Region(*values)


@pytest.mark.parametrize(
    ("width", "height", "error"),
    [
        (True, 10, TypeError),
        (10, False, TypeError),
        (1.0, 10, TypeError),
        (10, "10", TypeError),
        (0, 10, ValueError),
        (10, -1, ValueError),
    ],
)
def test_region_fits_rejects_invalid_container_dimensions(
    width: object, height: object, error: type[Exception]
) -> None:
    with pytest.raises(error):
        Region(0, 0, 1, 1).fits(width, height)  # type: ignore[arg-type]


def test_layout_dimensions_include_four_columns_and_seven_rows() -> None:
    layout = _layout(column_stride=40, row_stride=36)

    assert layout.width == 3 * 40 + 32
    assert layout.height == 6 * 36 + 32
    assert layout.region_at(10, 20) == Region(10, 20, 152, 248)


@pytest.mark.parametrize("profile_id", ["", " ", "\t"])
def test_layout_requires_non_empty_profile_id(profile_id: str) -> None:
    with pytest.raises(ValueError, match="profile_id"):
        _layout(profile_id=profile_id)


@pytest.mark.parametrize("field", ["column_stride", "row_stride"])
@pytest.mark.parametrize("value", [True, 32.5, "32", None])
def test_layout_rejects_non_integer_strides(field: str, value: object) -> None:
    values: dict[str, object] = {
        "profile_id": "synthetic-fixed",
        "column_stride": 32,
        "row_stride": 32,
    }
    values[field] = value

    with pytest.raises(TypeError, match=field):
        InventoryGridLayout(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["column_stride", "row_stride"])
def test_layout_rejects_stride_smaller_than_slot(field: str) -> None:
    values = {"column_stride": 32, "row_stride": 32}
    values[field] = 31

    with pytest.raises(ValueError, match=field):
        _layout(**values)


def test_slot_regions_are_row_major_and_frame_local() -> None:
    layout = _layout()
    inventory = layout.region_at(10, 20)

    assert layout.slot_region(inventory, 0) == Region(10, 20, 32, 32)
    assert layout.slot_region(inventory, 3) == Region(130, 20, 32, 32)
    assert layout.slot_region(inventory, 4) == Region(10, 56, 32, 32)
    assert layout.slot_region(inventory, 27) == Region(130, 236, 32, 32)


def test_all_slot_regions_are_unique_ordered_and_contained() -> None:
    layout = _layout()
    inventory = layout.region_at(125, 75)

    slots = layout.all_slot_regions(inventory)

    assert len(slots) == INVENTORY_CAPACITY
    assert len(set(slots)) == INVENTORY_CAPACITY
    assert slots == tuple(layout.slot_region(inventory, index) for index in range(28))
    assert all(slot.width == slot.height == INVENTORY_SLOT_SIZE for slot in slots)
    assert all(slot.x >= inventory.x and slot.y >= inventory.y for slot in slots)
    assert all(
        slot.x + slot.width <= inventory.x + inventory.width
        and slot.y + slot.height <= inventory.y + inventory.height
        for slot in slots
    )


@pytest.mark.parametrize("index", [-1, 28, 999])
def test_slot_region_rejects_out_of_range_index(index: int) -> None:
    layout = _layout()

    with pytest.raises(IndexError, match="slot index"):
        layout.slot_region(layout.region_at(0, 0), index)


@pytest.mark.parametrize("index", [True, 1.0, "1", None])
def test_slot_region_rejects_non_integer_index(index: object) -> None:
    layout = _layout()

    with pytest.raises(TypeError, match="slot index"):
        layout.slot_region(layout.region_at(0, 0), index)  # type: ignore[arg-type]


@pytest.mark.parametrize("operation", ["one", "all"])
def test_layout_rejects_wrong_size_inventory_region(operation: str) -> None:
    layout = _layout()
    malformed = Region(0, 0, layout.width + 1, layout.height)

    with pytest.raises(ValueError, match="must match layout dimensions"):
        if operation == "one":
            layout.slot_region(malformed, 0)
        else:
            layout.all_slot_regions(malformed)


def test_layout_rejects_non_region_inventory_value() -> None:
    with pytest.raises(TypeError, match="inventory_region must be Region"):
        _layout().slot_region((0, 0, 152, 248), 0)  # type: ignore[arg-type]


def test_region_at_reuses_strict_region_origin_validation() -> None:
    with pytest.raises(ValueError, match="x must be >= 0"):
        _layout().region_at(-1, 0)


def test_localization_accepts_known_and_unknown_results() -> None:
    region = _layout().region_at(0, 0)

    known = InventoryLocalization(region=region, confidence=1, reason="reviewed profile")
    identified = InventoryLocalization(
        region=region,
        confidence=1.0,
        reason="reviewed profile",
        profile_id="synthetic-fixed",
    )
    unknown = InventoryLocalization(region=None, confidence=0.0, reason="unsupported geometry")

    assert known.confidence == 1.0
    assert isinstance(known.confidence, float)
    assert known.profile_id is None
    assert identified.profile_id == "synthetic-fixed"
    assert unknown.region is None
    assert unknown.profile_id is None


@pytest.mark.parametrize(
    "confidence",
    [True, -0.1, 1.1, math.nan, math.inf, 10**400, "0.5"],
)
def test_localization_rejects_invalid_confidence(confidence: object) -> None:
    with pytest.raises(ValueError, match="confidence"):
        InventoryLocalization(
            region=None,
            confidence=confidence,  # type: ignore[arg-type]
            reason="invalid",
        )


@pytest.mark.parametrize("reason", ["", " ", "\t", None])
def test_localization_requires_a_reason(reason: object) -> None:
    with pytest.raises(ValueError, match="reason"):
        InventoryLocalization(
            region=None,
            confidence=0.0,
            reason=reason,  # type: ignore[arg-type]
        )


def test_localization_rejects_mismatched_region_and_confidence() -> None:
    region = _layout().region_at(0, 0)

    with pytest.raises(ValueError, match="unknown localization"):
        InventoryLocalization(region=None, confidence=0.5, reason="contradiction")
    with pytest.raises(ValueError, match="localized region"):
        InventoryLocalization(region=region, confidence=0.0, reason="contradiction")


def test_localization_rejects_non_region() -> None:
    with pytest.raises(TypeError, match="region must be Region or None"):
        InventoryLocalization(
            region=(0, 0, 1, 1),  # type: ignore[arg-type]
            confidence=1.0,
            reason="invalid",
        )


@pytest.mark.parametrize("profile_id", ["", " ", "\t", 1, True])
def test_localization_rejects_invalid_profile_id(profile_id: object) -> None:
    with pytest.raises(ValueError, match="profile_id"):
        InventoryLocalization(
            region=_layout().region_at(0, 0),
            confidence=1.0,
            reason="reviewed profile",
            profile_id=profile_id,  # type: ignore[arg-type]
        )


def test_unknown_localization_cannot_claim_a_profile() -> None:
    with pytest.raises(ValueError, match="cannot identify a profile"):
        InventoryLocalization(
            region=None,
            confidence=0.0,
            reason="unsupported geometry",
            profile_id="synthetic-fixed",
        )


def test_frame_profile_validates_region_layout_and_frame() -> None:
    profile = _profile()

    assert profile.region.fits(profile.frame_width, profile.frame_height)
    assert profile.region.width == profile.layout.width
    assert profile.region.height == profile.layout.height


@pytest.mark.parametrize("profile_id", ["", " ", "\n"])
def test_frame_profile_requires_non_empty_id(profile_id: str) -> None:
    layout = _layout(profile_id="valid")
    with pytest.raises(ValueError, match="profile_id"):
        InventoryFrameProfile(profile_id, 800, 600, layout.region_at(0, 0), layout)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("frame_width", True, TypeError),
        ("frame_height", 1.5, TypeError),
        ("frame_width", 0, ValueError),
        ("frame_height", -1, ValueError),
    ],
)
def test_frame_profile_rejects_invalid_frame_dimensions(
    field: str, value: object, error: type[Exception]
) -> None:
    layout = _layout()
    values: dict[str, object] = {
        "profile_id": layout.profile_id,
        "frame_width": 800,
        "frame_height": 600,
        "region": layout.region_at(0, 0),
        "layout": layout,
    }
    values[field] = value

    with pytest.raises(error, match=field):
        InventoryFrameProfile(**values)  # type: ignore[arg-type]


def test_frame_profile_requires_matching_profile_ids() -> None:
    layout = _layout(profile_id="layout-id")
    with pytest.raises(ValueError, match="must match"):
        InventoryFrameProfile("profile-id", 800, 600, layout.region_at(0, 0), layout)


def test_frame_profile_rejects_wrong_size_inventory_region() -> None:
    layout = _layout()
    with pytest.raises(ValueError, match="dimensions must match"):
        InventoryFrameProfile(
            layout.profile_id,
            800,
            600,
            Region(0, 0, layout.width - 1, layout.height),
            layout,
        )


def test_frame_profile_accepts_region_exactly_on_frame_boundary() -> None:
    layout = _layout()
    profile = InventoryFrameProfile(
        layout.profile_id,
        layout.width,
        layout.height,
        layout.region_at(0, 0),
        layout,
    )

    assert profile.region.fits(profile.frame_width, profile.frame_height)


def test_frame_profile_rejects_region_outside_frame() -> None:
    layout = _layout()
    with pytest.raises(ValueError, match="does not fit frame"):
        InventoryFrameProfile(
            layout.profile_id,
            layout.width,
            layout.height,
            layout.region_at(1, 0),
            layout,
        )


def test_frame_profile_rejects_wrong_runtime_types() -> None:
    layout = _layout()
    with pytest.raises(TypeError, match="region must be Region"):
        InventoryFrameProfile(
            layout.profile_id,
            800,
            600,
            (0, 0, layout.width, layout.height),  # type: ignore[arg-type]
            layout,
        )
    with pytest.raises(TypeError, match="layout must be InventoryGridLayout"):
        InventoryFrameProfile(
            layout.profile_id,
            800,
            600,
            layout.region_at(0, 0),
            object(),  # type: ignore[arg-type]
        )


class _ProtocolLocator:
    def locate(self, frame: Frame, /) -> InventoryLocalization:
        return InventoryLocalization(None, 0.0, f"synthetic frame {frame.frame_id}")


def test_inventory_locator_protocol_is_runtime_checkable() -> None:
    assert isinstance(_ProtocolLocator(), InventoryRegionLocator)
    assert isinstance(ExactProfileInventoryLocator([_profile()]), InventoryRegionLocator)


@pytest.mark.parametrize("profiles", [[], (), "not-profiles", b"not-profiles"])
def test_exact_locator_requires_non_empty_profile_sequence(profiles: object) -> None:
    error = ValueError if isinstance(profiles, Sequence) and not profiles else TypeError
    with pytest.raises(error):
        ExactProfileInventoryLocator(profiles)  # type: ignore[arg-type]


def test_exact_locator_rejects_invalid_profile_member() -> None:
    with pytest.raises(TypeError, match=r"profiles\[0\]"):
        ExactProfileInventoryLocator([object()])  # type: ignore[list-item]


def test_exact_locator_rejects_duplicate_frame_geometry() -> None:
    first = _profile(profile_id="first")
    second = _profile(profile_id="second", x=400)

    with pytest.raises(ValueError, match="duplicate 800x600"):
        ExactProfileInventoryLocator([first, second])


def test_exact_locator_allows_one_profile_id_for_identical_layouts() -> None:
    first = _profile(profile_id="shared")
    second = _profile(
        profile_id="shared",
        frame_width=1000,
        frame_height=800,
        x=800,
        y=500,
    )

    locator = ExactProfileInventoryLocator([first, second])

    assert locator.profiles == (first, second)


def test_exact_locator_rejects_one_profile_id_with_conflicting_layouts() -> None:
    first = _profile(profile_id="shared")
    conflicting_layout = InventoryGridLayout(
        profile_id="shared",
        column_stride=42,
        row_stride=38,
    )
    second = InventoryFrameProfile(
        profile_id="shared",
        frame_width=1000,
        frame_height=800,
        region=conflicting_layout.region_at(800, 500),
        layout=conflicting_layout,
    )

    with pytest.raises(ValueError, match="must use an identical inventory grid layout"):
        ExactProfileInventoryLocator([first, second])


def test_exact_locator_retains_immutable_caller_order() -> None:
    profiles = [_profile(profile_id="small", frame_width=800, frame_height=600)]
    profiles.append(
        _profile(
            profile_id="large",
            frame_width=1000,
            frame_height=800,
            x=800,
            y=500,
        )
    )

    locator = ExactProfileInventoryLocator(profiles)
    profiles.reverse()

    assert tuple(profile.profile_id for profile in locator.profiles) == ("small", "large")


def test_exact_locator_returns_reviewed_region_for_exact_frame_dimensions() -> None:
    profile = _profile()
    result = ExactProfileInventoryLocator([profile]).locate(_frame())

    assert result.region == profile.region
    assert result.confidence == 1.0
    assert result.profile_id == profile.profile_id
    assert profile.profile_id in result.reason


@pytest.mark.parametrize(("width", "height"), [(801, 600), (800, 599), (600, 800)])
def test_exact_locator_returns_unknown_for_wrong_frame_dimensions(
    width: int, height: int
) -> None:
    result = ExactProfileInventoryLocator([_profile()]).locate(_frame(width, height))

    assert result.region is None
    assert result.confidence == 0.0
    assert result.profile_id is None
    assert f"{width}x{height}" in result.reason


def test_exact_locator_selects_between_multiple_reviewed_profiles() -> None:
    small = _profile(profile_id="small")
    large = _profile(
        profile_id="large",
        frame_width=1000,
        frame_height=800,
        x=800,
        y=500,
    )
    locator = ExactProfileInventoryLocator([small, large])

    result = locator.locate(_frame(1000, 800))

    assert result.region == large.region
    assert "large" in result.reason


def test_exact_locator_rejects_non_frame_input() -> None:
    with pytest.raises(TypeError, match="frame must be Frame"):
        ExactProfileInventoryLocator([_profile()]).locate(object())  # type: ignore[arg-type]
