from __future__ import annotations

import uuid

from .contracts import ActionIntent, SessionState, WorldState


class ControllerDecisionError(RuntimeError):
    pass


class MiningController:
    """High-level closed-loop decision scaffold.

    This controller intentionally plans only from structured WorldState. Execution and
    verification live behind separate boundaries so an attempted action can never be
    mistaken for success.
    """

    def decide(self, state: WorldState) -> ActionIntent | None:
        if state.session_state in {
            SessionState.IDLE,
            SessionState.PAUSED,
            SessionState.BREAK,
            SessionState.STOPPING,
            SessionState.STOPPED,
            SessionState.ERROR,
        }:
            return None

        if state.session_state is SessionState.ACQUIRING:
            return self._intent(
                "reacquire",
                timeout_s=10.0,
                expected=("location", "inventory"),
            )

        if state.session_state is SessionState.MINING:
            if state.inventory.is_full is None:
                return self._intent(
                    "reacquire_inventory",
                    timeout_s=5.0,
                    expected=("inventory_state",),
                )

            if state.inventory.is_full is True:
                return self._intent(
                    "begin_navigation_to_bank",
                    timeout_s=5.0,
                    expected=("navigation_started",),
                )

            candidates = state.available_resources()
            if not candidates:
                return self._intent(
                    "reacquire_resources",
                    timeout_s=5.0,
                    expected=("resource_state",),
                )

            target = candidates[0]
            if target.interaction_region is None:
                raise ControllerDecisionError(
                    "validated resource has no interaction region"
                )

            return ActionIntent(
                action_id=str(uuid.uuid4()),
                kind="interact_resource",
                target_id=target.resource_id,
                interaction_region=target.interaction_region,
                timeout_s=12.0,
                expected_observation_kinds=(
                    "activity_state",
                    "resource_state",
                    "inventory_state",
                ),
                metadata={"resource_type": target.resource_type},
            )

        if state.session_state is SessionState.NAVIGATING_TO_BANK:
            return self._intent(
                "navigation_step_to_bank",
                8.0,
                ("location", "checkpoint"),
            )

        if state.session_state is SessionState.BANKING:
            return self._intent(
                "banking_step",
                8.0,
                ("bank_state", "inventory_state"),
            )

        if state.session_state is SessionState.NAVIGATING_TO_MINE:
            return self._intent(
                "navigation_step_to_mine",
                8.0,
                ("location", "checkpoint"),
            )

        if state.session_state is SessionState.RECOVERING:
            return self._intent(
                "recovery_step",
                10.0,
                ("recovery_evidence",),
            )

        raise ControllerDecisionError(
            f"unhandled session state: {state.session_state}"
        )

    @staticmethod
    def _intent(
        kind: str,
        timeout_s: float,
        expected: tuple[str, ...],
    ) -> ActionIntent:
        return ActionIntent(
            action_id=str(uuid.uuid4()),
            kind=kind,
            target_id=None,
            interaction_region=None,
            timeout_s=timeout_s,
            expected_observation_kinds=expected,
        )
