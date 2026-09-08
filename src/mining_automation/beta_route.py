"""Preserve native connector authority through beta cursor travel, not just before it."""

from __future__ import annotations

import math
from typing import Any

from .beta_input import InputPolicy
from .mining_slice import MAX_MINING_PERCEPTION_AGE_S


def beta_route_backend(base: type, policy: InputPolicy) -> type:
    # Runtime composition of the retained native backend; no route decision changes.
    class BetaRouteBackend(base):  # type: ignore[misc]
        _beta_connector_deadline: float | None = None

        def verify_start_connector_authority(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            receipt: dict[str, Any] = super().verify_start_connector_authority(*args, **kwargs)
            self._beta_connector_deadline = None
            if receipt.get("accepted") is True:
                captured = receipt.get("native_captured_monotonic_s")
                if type(captured) is not float or not math.isfinite(captured):
                    raise RuntimeError("native_connector_capture_time_unproven")
                self._beta_connector_deadline = captured + MAX_MINING_PERCEPTION_AGE_S
            return receipt

        def click(self, *args: Any, **kwargs: Any) -> None:
            previous = policy.dispatch_deadline
            deadline = self._beta_connector_deadline
            if deadline is not None:
                policy.dispatch_deadline = deadline if previous is None else min(previous, deadline)
            try:
                policy.check()
                # The existing native click calls policy checks while moving and immediately
                # before mouse-down. Its separate terrain/frame-age proof stays unchanged.
                super().click(*args, **kwargs)
            finally:
                policy.dispatch_deadline = previous
                self._beta_connector_deadline = None

    return BetaRouteBackend
