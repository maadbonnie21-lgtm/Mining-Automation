from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from mining_automation.capture.windows.gdi_resources import (
    GdiBitmapSurface,
    GdiResourceError,
    read_complete_scanlines,
)


@dataclass
class FakeOps:
    dc: int | None = 11
    bitmap: int | None = 22
    previous: int | None = 33
    restore_result: int | None = 22
    delete_dc_result: bool = True
    delete_object_result: bool = True
    events: list[tuple[object, ...]] = field(default_factory=list)
    select_calls: int = 0

    def create_compatible_dc(self, reference_dc: int) -> int | None:
        self.events.append(("create_dc", reference_dc))
        return self.dc

    def create_compatible_bitmap(
        self,
        reference_dc: int,
        width: int,
        height: int,
    ) -> int | None:
        self.events.append(("create_bitmap", reference_dc, width, height))
        return self.bitmap

    def select_object(self, dc: int, graphic_object: int) -> int | None:
        self.events.append(("select", dc, graphic_object))
        self.select_calls += 1
        return self.previous if self.select_calls == 1 else self.restore_result

    def delete_dc(self, dc: int) -> bool:
        self.events.append(("delete_dc", dc))
        return self.delete_dc_result

    def delete_object(self, graphic_object: int) -> bool:
        self.events.append(("delete_object", graphic_object))
        return self.delete_object_result


def make_surface(ops: FakeOps | None = None) -> tuple[FakeOps, GdiBitmapSurface]:
    actual = ops or FakeOps()
    return actual, GdiBitmapSurface.create(actual, 1, 4, 3, label="test")


def test_successful_surface_creation_selects_bitmap() -> None:
    ops, surface = make_surface()
    assert surface.selected
    assert ops.events == [
        ("create_dc", 1),
        ("create_bitmap", 1, 4, 3),
        ("select", 11, 22),
    ]


def test_bitmap_creation_failure_releases_created_dc() -> None:
    ops = FakeOps(bitmap=None)
    with pytest.raises(GdiResourceError, match="allocate test bitmap"):
        GdiBitmapSurface.create(ops, 1, 4, 3, label="test")
    assert ops.events[-1] == ("delete_dc", 11)


def test_select_failure_releases_dc_then_bitmap() -> None:
    ops = FakeOps(previous=None)
    with pytest.raises(GdiResourceError, match="select test bitmap"):
        GdiBitmapSurface.create(ops, 1, 4, 3, label="test")
    assert ops.events[-2:] == [("delete_dc", 11), ("delete_object", 22)]


def test_read_deselects_before_reader_runs() -> None:
    ops, surface = make_surface()

    def reader(bitmap: int) -> int:
        ops.events.append(("read", bitmap, surface.selected))
        return 3

    read_complete_scanlines(surface, 3, reader)
    assert not surface.selected
    assert ops.events[-2:] == [("select", 11, 33), ("read", 22, False)]


def test_partial_scanline_read_is_failure() -> None:
    _, surface = make_surface()
    with pytest.raises(GdiResourceError, match="2 of 3"):
        read_complete_scanlines(surface, 3, lambda bitmap: 2)


def test_close_restores_then_deletes_dc_then_bitmap() -> None:
    ops, surface = make_surface()
    surface.close()
    assert ops.events[-3:] == [
        ("select", 11, 33),
        ("delete_dc", 11),
        ("delete_object", 22),
    ]
    assert surface.closed


def test_normal_close_surfaces_cleanup_failure() -> None:
    ops, surface = make_surface(FakeOps(delete_object_result=False))
    with pytest.raises(GdiResourceError, match="delete test bitmap"):
        surface.close()
    assert surface.last_close_error is not None
    assert ops.events[-2:] == [("delete_dc", 11), ("delete_object", 22)]


def test_active_body_exception_is_not_replaced_by_cleanup_failure() -> None:
    ops = FakeOps(
        restore_result=None,
        delete_dc_result=False,
        delete_object_result=False,
    )
    surface = GdiBitmapSurface.create(ops, 1, 4, 3, label="test")
    with pytest.raises(ValueError, match="body failed"):
        with surface:
            raise ValueError("body failed")
    assert surface.last_close_error is not None


def test_close_is_idempotent() -> None:
    ops, surface = make_surface()
    surface.close()
    count = len(ops.events)
    surface.close()
    assert len(ops.events) == count


def test_invalid_expected_scanline_count_is_rejected() -> None:
    _, surface = make_surface()
    with pytest.raises(ValueError, match="positive"):
        read_complete_scanlines(surface, 0, lambda bitmap: 0)
