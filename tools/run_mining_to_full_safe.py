#!/usr/bin/env python3
"""Separate mining-only entry point with post-capture window revalidation.

This command deliberately does not PREP RuneLite. It delegates to the existing
fail-closed mining-to-full CLI, so live use still requires the exact checkout SHA,
exact HWND, explicit mining confirmation, and a clean checkout. The adapter also
adds one conservative Resource fallback after exact-pose and rigid-translation
reacquisition both fail: the original frozen landmark descriptors may be matched
at a modest nearby scale, without changing their distance/quorum/zone gates.
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
    """Rebind evidence to fresh window facts and recover modest scene scale drift."""

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
        return replace(observation, window=final_window)

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


def main(argv: list[str] | None = None) -> int:
    mining.WindowsMiningToFullBackend = SafeWindowsMiningToFullBackend
    return mining.main(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())
