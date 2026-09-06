"""Strict first-live PREP boundary for one explicitly authorized RuneLite HWND.

This module is deliberately a narrow wrapper around the reviewed PREP backend.  It
adds the two guarantees required before a real PREP run:

* mutating PREP operations are bound to an externally supplied exact HWND; and
* every perception observation is followed by a fresh window snapshot, so READY
  can only be published when the exact observation frame is causally paired with
  a still-valid RuneLite window.

It does not mine, navigate, bank, move items, authorize perception release, or
create mining authority.
"""

from __future__ import annotations

from dataclasses import replace
from typing import final

from .runelite_prep import (
    EXPECTED_CLIENT_DPI,
    PrepActionReceipt,
    PrepBackend,
    PrepCameraStep,
    PrepOperationError,
    PrepPoseReferenceReceipt,
    PrepSceneObservation,
    PrepStopReason,
    PrepWindowSnapshot,
    RunelitePrepResult,
)


@final
class ExactHwndPrepBackend(PrepBackend):
    """Decorate PREP with exact-HWND and post-observation window binding."""

    def __init__(self, inner: PrepBackend, *, expected_hwnd: int | None) -> None:
        if expected_hwnd is not None and (
            isinstance(expected_hwnd, bool)
            or not isinstance(expected_hwnd, int)
            or expected_hwnd <= 0
        ):
            raise ValueError("expected_hwnd must be a positive integer when supplied")
        self._inner = inner
        self._expected_hwnd = expected_hwnd
        self.last_observation_window: PrepWindowSnapshot | None = None

    def _require_expected_hwnd(self, snapshot: PrepWindowSnapshot) -> None:
        if self._expected_hwnd is not None and snapshot.hwnd != self._expected_hwnd:
            raise PrepOperationError(
                PrepStopReason.WINDOW_IDENTITY_CHANGED,
                "PREP resolved RuneLite HWND "
                f"{snapshot.hwnd}, but this session is authorized only for exact "
                f"HWND {self._expected_hwnd}.",
            )

    @staticmethod
    def _require_same_window(
        before: PrepWindowSnapshot,
        after: PrepWindowSnapshot,
    ) -> None:
        if after.hwnd != before.hwnd or after.identity != before.identity:
            raise PrepOperationError(
                PrepStopReason.WINDOW_IDENTITY_CHANGED,
                "RuneLite HWND/process/thread/class/title identity changed while the "
                "READY observation was being captured.",
            )

    @staticmethod
    def _require_observation_window_ready(snapshot: PrepWindowSnapshot) -> None:
        if not snapshot.visible:
            raise PrepOperationError(
                PrepStopReason.WINDOW_NOT_VISIBLE,
                "RuneLite became hidden while the READY observation was captured.",
            )
        if snapshot.minimized:
            raise PrepOperationError(
                PrepStopReason.WINDOW_MINIMIZED,
                "RuneLite became minimized while the READY observation was captured.",
            )
        if not snapshot.foreground:
            raise PrepOperationError(
                PrepStopReason.WINDOW_NOT_FOREGROUND,
                "RuneLite lost foreground while the READY observation was captured.",
            )
        if snapshot.dpi != EXPECTED_CLIENT_DPI:
            raise PrepOperationError(
                PrepStopReason.DPI_MISMATCH,
                f"RuneLite DPI changed to {snapshot.dpi} during READY observation; "
                f"expected exact {EXPECTED_CLIENT_DPI}.",
            )
        if not snapshot.exact_geometry:
            raise PrepOperationError(
                PrepStopReason.CLIENT_GEOMETRY_MISMATCH,
                "RuneLite client geometry changed while the READY observation was "
                f"captured: {snapshot.client_width}x{snapshot.client_height}.",
            )

    def _before_mutation(self) -> None:
        snapshot = self.snapshot()
        if self._expected_hwnd is None:
            raise PrepOperationError(
                PrepStopReason.WINDOW_IDENTITY_CHANGED,
                "Mutating PREP requires an externally authorized exact RuneLite HWND.",
            )
        self._require_expected_hwnd(snapshot)

    def snapshot(self) -> PrepWindowSnapshot:
        snapshot = self._inner.snapshot()
        self._require_expected_hwnd(snapshot)
        return snapshot

    def verify_pose_references(self) -> tuple[PrepPoseReferenceReceipt, ...]:
        # Bind discovery before any later setup mutation, even though reference
        # verification itself is read-only.
        self.snapshot()
        return self._inner.verify_pose_references()

    def restore_window(self) -> PrepActionReceipt:
        self._before_mutation()
        return self._inner.restore_window()

    def resize_client(self, width: int, height: int) -> PrepActionReceipt:
        self._before_mutation()
        return self._inner.resize_client(width, height)

    def focus_window(self) -> PrepActionReceipt:
        self._before_mutation()
        return self._inner.focus_window()

    def neutralize_cursor(self) -> PrepActionReceipt:
        self._before_mutation()
        return self._inner.neutralize_cursor()

    def observe(self) -> PrepSceneObservation:
        before = self.snapshot()
        observation = self._inner.observe()
        after = self.snapshot()
        self._require_expected_hwnd(after)
        self._require_same_window(before, after)
        self._require_observation_window_ready(after)
        # This snapshot is causally downstream of the exact observation capture and
        # is therefore the only window snapshot allowed to support a READY receipt.
        self.last_observation_window = after
        return observation

    def camera_action(self, step: PrepCameraStep) -> tuple[PrepActionReceipt, ...]:
        self._before_mutation()
        return self._inner.camera_action(step)

    def cleanup(self) -> tuple[PrepActionReceipt, ...]:
        # Cleanup can only relinquish input already owned by PREP.  It is not allowed
        # to manufacture a new READY window snapshot.
        return self._inner.cleanup()


def bind_ready_receipt_to_observation_window(
    result: RunelitePrepResult,
    backend: ExactHwndPrepBackend,
) -> RunelitePrepResult:
    """Replace terminal/cleanup window evidence with the READY-frame-bound snapshot."""

    if not result.ready_for_mining:
        return result
    ready_window = backend.last_observation_window
    if ready_window is None:
        return replace(
            result,
            ready_for_mining=False,
            stop_reason=PrepStopReason.BACKEND_ERROR,
            detail="READY was withheld because no post-observation window snapshot exists.",
        )
    return replace(result, final_window=ready_window)
