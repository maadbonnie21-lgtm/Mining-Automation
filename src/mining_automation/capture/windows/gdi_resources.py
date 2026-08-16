"""Testable ownership rules for Win32 GDI bitmap surfaces."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from struct import calcsize
from types import TracebackType
from typing import Protocol, Self

__all__ = [
    "GdiBitmapSurface",
    "GdiOps",
    "GdiResourceError",
    "read_complete_scanlines",
]

_POINTER_ALL_BITS_SET = (1 << (calcsize("P") * 8)) - 1


class GdiResourceError(RuntimeError):
    """A GDI resource could not be created, selected, restored, or released."""


class GdiOps(Protocol):
    """Small injectable subset of GDI used to own bitmap/DC lifetimes."""

    def create_compatible_dc(self, reference_dc: int) -> int | None: ...

    def create_compatible_bitmap(
        self,
        reference_dc: int,
        width: int,
        height: int,
    ) -> int | None: ...

    def select_object(self, dc: int, graphic_object: int) -> int | None: ...

    def delete_dc(self, dc: int) -> bool: ...

    def delete_object(self, graphic_object: int) -> bool: ...


def _invalid_handle(handle: int | None) -> bool:
    return handle in (None, 0, -1, _POINTER_ALL_BITS_SET)


def _cleanup_suffix(*, dc_deleted: bool, bitmap_deleted: bool) -> str:
    failures: list[str] = []
    if not dc_deleted:
        failures.append("device context cleanup failed")
    if not bitmap_deleted:
        failures.append("bitmap cleanup failed")
    return "" if not failures else f" ({'; '.join(failures)})"


@dataclass(slots=True)
class GdiBitmapSurface:
    """One compatible memory DC with one owned bitmap selected into it.

    Creation is transactional: every successfully created resource is released
    if a later creation or selection step fails. Normal close restores the
    original bitmap, deletes the DC, then deletes the owned bitmap. Deleting the
    DC before the bitmap also releases the selection if restoring the original
    object unexpectedly fails, giving ``DeleteObject`` a final chance to avoid a
    leak.
    """

    ops: GdiOps
    dc: int
    bitmap: int
    previous_object: int
    label: str
    selected: bool = True
    closed: bool = False
    last_close_error: GdiResourceError | None = field(default=None, init=False)

    @classmethod
    def create(
        cls,
        ops: GdiOps,
        reference_dc: int,
        width: int,
        height: int,
        *,
        label: str,
    ) -> Self:
        """Create and select a compatible bitmap without leaking partial state."""
        dc = ops.create_compatible_dc(reference_dc)
        if _invalid_handle(dc):
            raise GdiResourceError(f"could not allocate {label} device context")
        assert dc is not None

        bitmap = ops.create_compatible_bitmap(reference_dc, width, height)
        if _invalid_handle(bitmap):
            dc_deleted = ops.delete_dc(dc)
            raise GdiResourceError(
                f"could not allocate {label} bitmap"
                + _cleanup_suffix(dc_deleted=dc_deleted, bitmap_deleted=True)
            )
        assert bitmap is not None

        previous = ops.select_object(dc, bitmap)
        if _invalid_handle(previous):
            # Delete the DC first so the bitmap cannot remain selected into it.
            dc_deleted = ops.delete_dc(dc)
            bitmap_deleted = ops.delete_object(bitmap)
            raise GdiResourceError(
                f"could not select {label} bitmap"
                + _cleanup_suffix(
                    dc_deleted=dc_deleted,
                    bitmap_deleted=bitmap_deleted,
                )
            )
        assert previous is not None
        return cls(
            ops=ops,
            dc=dc,
            bitmap=bitmap,
            previous_object=previous,
            label=label,
        )

    def deselect(self) -> None:
        """Restore the original bitmap so this bitmap can be read or deleted."""
        if self.closed:
            raise GdiResourceError(f"{self.label} surface is already closed")
        if not self.selected:
            return
        replaced = self.ops.select_object(self.dc, self.previous_object)
        if _invalid_handle(replaced):
            raise GdiResourceError(f"could not restore original {self.label} bitmap")
        self.selected = False

    def close(self) -> None:
        """Restore and release every owned handle; surface failures explicitly."""
        if self.closed:
            return

        restore_error: GdiResourceError | None = None
        if self.selected:
            try:
                self.deselect()
            except GdiResourceError as exc:
                restore_error = exc

        # DC first: if restoration failed, deleting it releases the bitmap
        # selection before DeleteObject is attempted.
        dc_deleted = self.ops.delete_dc(self.dc)
        bitmap_deleted = self.ops.delete_object(self.bitmap)
        self.closed = True

        failures: list[str] = []
        if restore_error is not None:
            failures.append(str(restore_error))
        if not dc_deleted:
            failures.append(f"could not delete {self.label} device context")
        if not bitmap_deleted:
            failures.append(f"could not delete {self.label} bitmap")
        if failures:
            error = GdiResourceError("; ".join(failures))
            self.last_close_error = error
            raise error

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            self.close()
        except GdiResourceError:
            if exc_type is None:
                raise


def read_complete_scanlines(
    surface: GdiBitmapSurface,
    expected_scanlines: int,
    reader: Callable[[int], int],
) -> None:
    """Deselect ``surface.bitmap``, read it, and require every scanline.

    ``GetDIBits`` requires that the bitmap not be selected into a device context.
    A positive but partial return is still a failed frame and must not be passed
    to perception as complete pixels.
    """
    if expected_scanlines <= 0:
        raise ValueError("expected_scanlines must be positive")
    surface.deselect()
    actual_scanlines = reader(surface.bitmap)
    if actual_scanlines != expected_scanlines:
        raise GdiResourceError(
            "reading captured pixels returned "
            f"{actual_scanlines} of {expected_scanlines} scanlines"
        )
