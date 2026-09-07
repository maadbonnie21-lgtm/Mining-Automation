#!/usr/bin/env python3
"""Separate mining-only entry point with recoverable target reacquisition.

This command deliberately does not PREP RuneLite. It delegates to the existing
mining-to-full runtime and keeps every existing window, lineage, Inventory,
hover-text, and one-click gate. Two narrow recovery adapters are added:

* after exact-pose and rigid-translation Resource reacquisition both fail, the
  original frozen scene landmarks may be matched at one modest global scale;
* when a fresh target hover is not exactly ``Mine Iron rocks``, no click is
  sent. The target is deprioritized, the loop reopens from a fresh clean
  observation, and another currently proven iron target is selected.

A stale/foreign/wrong-window hover proof is still terminal. Only the ordinary
"wrong action text / unproven action" case is recoverable because it performs
zero game clicks and therefore cannot create unobserved mining progress.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

TOOLS_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = TOOLS_ROOT.parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

import run_mining_to_full as mining  # noqa: E402

from mining_automation.mining_loop_runtime import (  # noqa: E402
    CleanMiningObservation,
    MiningHoverProof,
    MiningLoopResult,
    MiningLoopStopReason,
)
from mining_automation.mining_slice import (  # noqa: E402
    ResourcePerceptionEnvelope,
    ResourceViewState,
)
from mining_automation.perception.resource import (  # noqa: E402
    resource_state_from_observation,
)
from mining_automation.perception.scaled_scene_registration import (  # noqa: E402
    register_scaled_scene,
)


class SafeWindowsMiningToFullBackend(mining.WindowsMiningToFullBackend):
    """Keep strict proof gates while recovering modest scene/target misses."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._hover_rejections: dict[str, int] = {}

    def note_hover_rejection(self, target_id: str) -> None:
        """Deprioritize one no-click target on the next fresh observation."""

        self._hover_rejections[target_id] = self._hover_rejections.get(target_id, 0) + 1

    def _deprioritize_hover_rejections(
        self,
        observation: CleanMiningObservation,
    ) -> CleanMiningObservation:
        state = observation.state
        if state.selected_target is None or not state.resources or not self._hover_rejections:
            return observation

        # Python's sort is stable, so original detector/source order is retained
        # among targets carrying the same rejection count.
        ordered = tuple(
            sorted(
                state.resources,
                key=lambda resource: self._hover_rejections.get(resource.resource_id, 0),
            )
        )
        selected = next(
            (
                resource
                for resource in ordered
                if resource.available is True
                and resource.resource_type == "iron"
                and resource.interaction_region is not None
            ),
            None,
        )
        if selected is None:
            return observation
        return replace(
            observation,
            state=replace(
                state,
                resources=ordered,
                selected_target=selected,
            ),
        )

    def open(self) -> None:
        super().open()
        original_evaluator = self._evaluate_resource
        if original_evaluator is None:
            raise RuntimeError("Resource proof adapter was not opened")

        def evaluate_with_scaled_fallback(
            frame: Any,
            epoch: Any,
            detectors: Any,
            excluded: Any,
            active: Any,
        ) -> Any:
            resource, pose, diagnoses = original_evaluator(
                frame,
                epoch,
                detectors,
                excluded,
                active,
            )
            if resource.view is not ResourceViewState.UNSUPPORTED:
                return resource, pose, diagnoses

            scaled = []
            scaled_diagnoses = []
            for pose_name, detector in detectors.items():
                registration = register_scaled_scene(frame, detector)
                if registration is None:
                    continue
                registered_detector, evidence = registration
                scaled.append((pose_name, registered_detector, evidence))
                scaled_diagnoses.append(
                    {
                        "pose": pose_name,
                        "matched": evidence["matched"],
                        "zones": evidence["zones"],
                    }
                )

            if len(scaled) != 1:
                return (
                    resource,
                    pose,
                    {
                        **diagnoses,
                        "scaled_registration_candidates": scaled_diagnoses,
                    },
                )

            pose_name, detector, registration_evidence = scaled[0]
            active["pose"] = pose_name
            active["detector"] = detector
            resources = tuple(
                resource_state_from_observation(observation)
                for observation in detector.detect(frame)
                if observation.evidence["resource_id"] not in excluded
            )
            return (
                ResourcePerceptionEnvelope(
                    epoch=epoch,
                    release=resource.release,
                    view=ResourceViewState.SUPPORTED,
                    resources=resources,
                ),
                pose_name,
                {
                    **diagnoses,
                    "software_registration": registration_evidence,
                },
            )

        self._evaluate_resource = evaluate_with_scaled_fallback

    def acquire_clean_observation(
        self,
        *,
        session_id: str,
        iteration: int,
    ) -> CleanMiningObservation:
        observation = super().acquire_clean_observation(
            session_id=session_id,
            iteration=iteration,
        )
        _, final_window = self._verify_window()
        observation = replace(observation, window=final_window)
        return self._deprioritize_hover_rejections(observation)

    def prove_hover(
        self,
        proposal: Any,
        *,
        iteration: int,
    ) -> MiningHoverProof:
        proof = super().prove_hover(proposal, iteration=iteration)
        _, final_window = self._verify_window()
        root = self.api.root_window_at_point(*proof.screen_point)
        if root != self.expected_hwnd:
            raise RuntimeError("target became occluded during hover evidence capture")
        if self.api.cursor_position() != proof.screen_point:
            raise RuntimeError("cursor moved during hover evidence capture")
        return replace(
            proof,
            window=final_window,
            root_window_hwnd=root,
            cursor_matches_target=True,
        )


def _last_hover_target(result: MiningLoopResult) -> str | None:
    for event in reversed(result.events):
        if event.get("kind") == "hover_proof":
            target_id = event.get("target_id")
            return target_id if isinstance(target_id, str) and target_id else None
    return None


def _run_with_hover_recovery(
    backend: Any,
    config: Any,
    original_run: Any,
) -> MiningLoopResult:
    """Re-enter from fresh perception after a zero-click action-text miss."""

    aggregate_events: list[dict[str, object]] = []
    aggregate_targets: list[str] = []
    aggregate_dispatch_ids: list[str] = []
    aggregate_verified_ores = 0
    aggregate_click_count = 0
    aggregate_attempt_count = 0
    first_start_inventory: int | None = None
    recovery_count = 0

    while True:
        result: MiningLoopResult = original_run(backend, config)
        if first_start_inventory is None:
            first_start_inventory = result.start_inventory
        aggregate_events.extend(result.events)
        aggregate_targets.extend(result.target_sequence)
        aggregate_dispatch_ids.extend(result.dispatch_ids)
        aggregate_verified_ores += result.verified_ores
        aggregate_click_count += result.click_count
        aggregate_attempt_count += result.attempt_count

        if result.stop_reason is not MiningLoopStopReason.HOVER_ACTION_UNPROVEN:
            detail = result.detail
            if recovery_count:
                detail = f"{detail}; recovered_hover_misses={recovery_count}"
            return replace(
                result,
                start_inventory=first_start_inventory,
                verified_ores=aggregate_verified_ores,
                click_count=aggregate_click_count,
                attempt_count=aggregate_attempt_count,
                target_sequence=tuple(aggregate_targets),
                dispatch_ids=tuple(aggregate_dispatch_ids),
                events=tuple(aggregate_events),
                detail=detail,
            )

        # The runtime reached this result only after proving the hover evidence
        # itself was fresh, same-window, cursor-bound, and tied to the current
        # clean proposal. Crucially, dispatch has not occurred yet.
        target_id = _last_hover_target(result)
        recovery_count += 1
        aggregate_events.append(
            {
                "kind": "hover_action_reacquire",
                "recovery_index": recovery_count,
                "target_id": target_id,
                "action": "zero_click_fresh_reobserve_and_deprioritize",
            }
        )
        if target_id is not None and isinstance(backend, SafeWindowsMiningToFullBackend):
            backend.note_hover_rejection(target_id)

        # Prevent an endless no-click hover loop if the scene truly cannot
        # expose any exact iron action. This uses the existing observation limit
        # rather than inventing a new aggressive stop threshold.
        if recovery_count >= config.max_passive_observations:
            return replace(
                result,
                start_inventory=first_start_inventory,
                verified_ores=aggregate_verified_ores,
                click_count=aggregate_click_count,
                attempt_count=aggregate_attempt_count,
                target_sequence=tuple(aggregate_targets),
                dispatch_ids=tuple(aggregate_dispatch_ids),
                events=tuple(aggregate_events),
                detail=(
                    f"exact hover action remained unavailable after {recovery_count} "
                    "zero-click fresh reacquisitions"
                ),
            )


def main(argv: list[str] | None = None) -> int:
    original_run = mining.run_mining_until_full

    def recovered_run(backend: Any, config: Any) -> MiningLoopResult:
        return _run_with_hover_recovery(backend, config, original_run)

    mining.WindowsMiningToFullBackend = SafeWindowsMiningToFullBackend
    mining.run_mining_until_full = recovered_run
    return mining.main(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())
