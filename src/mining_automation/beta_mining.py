"""Beta-only input adapter around the existing proven mining/reacquisition backend."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import replace
from functools import wraps
from typing import Any

from .beta_input import GuardedCameraApi, InputPolicy
from .beta_interaction import (
    InputExpired,
    click_at_proven_point,
    require_fresh,
    require_interior,
    sample_interior,
)
from .mining_loop_runtime import MiningDispatchResult, MiningHoverProof, MiningLoopStopReason
from .mining_slice import MiningAttemptDispatchReceipt


def zero_click_expiry(method: Any) -> Any:
    @wraps(method)
    def wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
        self._zero_click_expired = False
        try:
            return method(self, *args, **kwargs)
        except InputExpired:
            # This exception is raised only at pre-down gates, never after native down.
            self._zero_click_expired = True
            self._selected = None
            self.active_registration = {"pose": None, "detector": None}
            raise

    return wrapped


def run_with_fresh_expiry(backend: Any, config: Any, run_existing_safe_loop: Any) -> Any:
    """Only pre-down expiry can re-enter existing fresh acquisition; other failures stop."""
    results: list[Any] = []
    reacquisitions: list[dict[str, object]] = []
    misses = 0
    while True:
        result = run_existing_safe_loop(backend, config)
        results.append(result)
        if result.verified_ores > 0:
            misses = 0
        expired = (
            getattr(backend, "_zero_click_expired", False)
            and result.stop_reason is MiningLoopStopReason.BACKEND_ERROR
            and result.detail.startswith("backend raised InputExpired:")
        )
        if not expired or misses >= 2:
            return replace(
                result,
                start_inventory=results[0].start_inventory,
                verified_ores=sum(item.verified_ores for item in results),
                click_count=sum(item.click_count for item in results),
                attempt_count=sum(item.attempt_count for item in results),
                target_sequence=tuple(t for item in results for t in item.target_sequence),
                dispatch_ids=tuple(t for item in results for t in item.dispatch_ids),
                events=tuple(e for item in results for e in item.events) + tuple(reacquisitions),
                detail=result.detail + f"; zero_click_expiry_reacquisitions={len(reacquisitions)}",
            )
        misses += 1
        backend._zero_click_expired = False
        reacquisitions.append(
            dict(
                kind="zero_click_source_expiry_reacquire",
                index=len(reacquisitions) + 1,
                consecutive=misses,
                action="discard_geometry_and_call_existing_fresh_acquisition",
                input_count=0,
            )
        )
        print(
            f"[REACQUIRE] Source expired before mouse-down; fresh geometry required ({misses}/2)",
            flush=True,
        )


def beta_mining_backend(base: type, policy: InputPolicy, *, varied_points: bool) -> type:
    # The legacy backend is selected at runtime; this adapter preserves its implementation.
    class BetaMiningBackend(base):  # type: ignore[misc]
        passive_interval_s: float

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.api = GuardedCameraApi(policy)
            self._previous_points: dict[str, tuple[int, int]] = {}
            self._selected: tuple[str, tuple[int, int], tuple[int, int, int, int]] | None = None

        def open(self) -> None:
            self._zero_click_expired = False
            policy.check()
            self.active_registration = {"pose": None, "detector": None}
            self._selected = None
            super().open()
            policy.check()

        def _verify_window(self) -> Any:
            policy.check()
            result = super()._verify_window()
            policy.check()
            return result

        def _capture(self, label: str) -> Any:
            policy.check()
            result = super()._capture(label)
            policy.check()
            return result

        def _neutralize_cursor(self) -> None:
            self._selected = None
            mapping = policy.api.pointer_mapping(self.expected_hwnd, 100, 100)
            if not mapping.exact_round_trip:
                raise RuntimeError("neutral_cursor_coordinate_round_trip_failed")
            policy.move(mapping.physical_screen.pair)
            policy.wait(self.neutral_settle_s)

        def observe_passive(self, *args: Any, **kwargs: Any) -> Any:
            interval = self.passive_interval_s
            policy.wait(interval)
            self.passive_interval_s = 0
            try:
                return super().observe_passive(*args, **kwargs)
            finally:
                self.passive_interval_s = interval

        @zero_click_expiry
        def prove_hover(self, proposal: Any, *, iteration: int) -> MiningHoverProof:
            _, window = self._verify_window()
            region = proposal.target_region
            if varied_points:
                point = sample_interior(
                    region, previous=self._previous_points.get(proposal.target_id)
                )
            else:
                x, y, w, h = region
                point = (x + w // 2, y + h // 2)
            require_interior(point, region, margin=2 if varied_points else 0)
            self._selected = (proposal.attempt_id, point, region)
            mapping = policy.api.pointer_mapping(self.expected_hwnd, *point)
            if not mapping.exact_round_trip:
                raise RuntimeError("selected_rock_coordinate_round_trip_failed")
            screen = mapping.physical_screen.pair
            deadline = proposal.source_epoch.captured_monotonic_s + 1.0
            require_fresh(deadline, time.monotonic())
            policy.move(screen, deadline=deadline)
            policy.wait(self.hover_settle_s)
            require_fresh(deadline, time.monotonic())
            frame, _ = self._capture(f"iteration-{iteration:02d}-beta-hover")
            epoch = self._epoch(frame, f"iteration-{iteration}-beta-hover")
            signature = self._mine_hover_signature(frame.payload, frame.width)
            proven = signature.get("proven_mine_iron_rocks") is True
            require_fresh(deadline, time.monotonic())
            _, window = self._verify_window()
            return MiningHoverProof(
                proposal_source_epoch=proposal.source_epoch,
                hover_epoch=epoch,
                attempt_id=proposal.attempt_id,
                target_id=proposal.target_id,
                target_region=region,
                action_text="Mine Iron rocks" if proven else "UNPROVEN",
                interaction_proven=proven,
                window=window,
                root_window_hwnd=policy.api.root_window_at_point(*screen),
                client_point=point,
                screen_point=screen,
                cursor_matches_target=policy.api.cursor_position() == screen,
            )

        @zero_click_expiry
        def dispatch_one_click(
            self, proposal: Any, proof: MiningHoverProof, *, iteration: int
        ) -> MiningDispatchResult:
            _, window = self._verify_window()
            if (
                self._selected != (proposal.attempt_id, proof.client_point, proposal.target_region)
                or proof.proposal_source_epoch != proposal.source_epoch
                or proof.target_region != proposal.target_region
                or proof.target_id != proposal.target_id
                or proof.action_text != "Mine Iron rocks"
                or not proof.interaction_proven
                or not proof.hover_epoch.strictly_newer_than(proposal.source_epoch)
            ):
                raise RuntimeError("frozen_sample_hover_mismatch; zero_click")
            require_interior(
                proof.client_point, proposal.target_region, margin=2 if varied_points else 0
            )
            mapping = policy.api.pointer_mapping(self.expected_hwnd, *proof.client_point)
            if not mapping.exact_round_trip or mapping.physical_screen.pair != proof.screen_point:
                raise RuntimeError("hover_dispatch_mapping_changed; zero_click")
            audit: dict[str, Any] = dict(
                attempt_id=proposal.attempt_id,
                iteration=iteration,
                target_id=proposal.target_id,
                source_frame_id=proposal.source_epoch.frame_id,
                source_sha256=proposal.source_epoch.frame_payload_sha256,
                hover_frame_id=proof.hover_epoch.frame_id,
                client_point=list(proof.client_point),
                target_region=list(proposal.target_region),
                down=0,
                up=0,
            )
            try:
                click_at_proven_point(
                    policy.api,
                    proof.screen_point,
                    hwnd=self.expected_hwnd,
                    check=policy.check,
                    wait=policy.wait,
                    deadline=proposal.source_epoch.captured_monotonic_s + 1.0,
                    receipt=audit,
                )
            finally:
                self._selected = None  # A proposal can never be dispatched twice.
                with (self.output / "beta-input.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(audit) + "\n")
            self._previous_points[proposal.target_id] = proof.client_point
            receipt = MiningAttemptDispatchReceipt(
                attempt_id=proposal.attempt_id,
                attempt_sequence=proposal.attempt_sequence,
                target_id=proposal.target_id,
                target_region=proposal.target_region,
                source_cycle_id=proposal.source_epoch.cycle_id,
                source_frame_id=proposal.source_epoch.frame_id,
                source_frame_payload_sha256=proposal.source_epoch.frame_payload_sha256,
                dispatcher_id="beta-proven-point",
                dispatcher_version="1.0.0",
                dispatch_id=f"beta-{uuid.uuid4().hex}",
                dispatched_monotonic_s=audit["down_monotonic_s"],
                click_dispatch_count=1,
                dispatch_succeeded=True,
            )
            return MiningDispatchResult(
                receipt=receipt,
                window=window,
                root_window_hwnd=policy.api.root_window_at_point(*proof.screen_point),
                client_point=tuple(audit["client_point"]),
                screen_point=tuple(audit["screen_point"]),
                cursor_matches_target=policy.api.cursor_position() == proof.screen_point,
                coordinate_round_trip_exact=mapping.exact_round_trip,
            )

    return BetaMiningBackend
