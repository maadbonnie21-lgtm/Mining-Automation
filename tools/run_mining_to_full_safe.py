#!/usr/bin/env python3
"""Separate mining-only entry point with post-capture window revalidation.

This command deliberately does not PREP RuneLite. It delegates to the existing
fail-closed mining-to-full CLI, so live use still requires the exact checkout SHA,
exact HWND, explicit mining confirmation, and a clean checkout. The only adapter
change is to bind clean and hover evidence to fresh window/ownership facts after
their final captures, while dispatch still rechecks immediately before SendInput.
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


class SafeWindowsMiningToFullBackend(mining.WindowsMiningToFullBackend):
    """Rebind clean/hover evidence to fresh post-capture window facts."""

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
