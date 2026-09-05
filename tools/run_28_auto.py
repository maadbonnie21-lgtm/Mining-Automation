#!/usr/bin/env python3
"""Automatically PREP RuneLite, then run one fail-closed 0->28 mining attempt.

This is the owner-facing first-endurance entry point. It keeps the two authority
boundaries explicit while removing manual camera setup from the operator workflow:

1. bounded PREP owns only window/camera normalization and perception readiness;
2. PREP must return a genuine fresh READY receipt with Inventory exactly 0/28;
3. PREP authority is relinquished;
4. the separate mining gate is then invoked for the same exact SHA + HWND;
5. mining stops at 28/28 or the first uncertainty, with no navigation/banking.

No READY result means zero mining clicks.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid
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
import runelite_prep_auto as prep_auto  # noqa: E402

from mining_automation.mining_loop_runtime import (  # noqa: E402
    CleanMiningObservation,
    MiningHoverProof,
)
from mining_automation.safe_live_inventory import (  # noqa: E402
    SafeEmptyStartMiningPerceptionEvaluator,
)

OWNER_CONFIRMATION = "AUTO_PREP_THEN_MINE_TO_28"


class SafeWindowsMiningToFullBackend(mining.WindowsMiningToFullBackend):
    """Live backend with post-capture window checks and proven-empty Inventory."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # The official run is explicitly 0->28. Until a genuine empty frame is
        # proven, nonzero Inventory is UNKNOWN instead of inferred from missing
        # empty-slot hashes. Once empty is proven this evaluator calibrates the
        # existing session detector and tracks subsequent +1 observations.
        self.inventory_evaluator = SafeEmptyStartMiningPerceptionEvaluator()

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
        # The original #84 adapter sampled window facts before neutralize/capture.
        # Recheck after the final clean/registered perception work so stale window
        # facts can never authorize the later proposal/click path.
        _, final_window = self._verify_window()
        return replace(observation, window=final_window)

    def prove_hover(
        self,
        proposal: Any,
        *,
        iteration: int,
    ) -> MiningHoverProof:
        proof = super().prove_hover(proposal, iteration=iteration)
        # Recheck after the hover frame and tooltip signature are complete. Also
        # re-prove ownership/cursor at that same terminal boundary. dispatch_one_click
        # independently rechecks all three again immediately before SendInput.
        _, final_window = self._verify_window()
        root = self.api.root_window_at_point(*proof.screen_point)
        if root != self.expected_hwnd:
            raise RuntimeError("target became occluded during hover evidence capture")
        cursor_matches = self.api.cursor_position() == proof.screen_point
        if not cursor_matches:
            raise RuntimeError("cursor moved during hover evidence capture")
        return replace(
            proof,
            window=final_window,
            root_window_hwnd=root,
            cursor_matches_target=True,
        )


def _git_head() -> str:
    return subprocess.run(
        ["git", "-C", str(REPOSITORY_ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _tracked_checkout_clean() -> bool:
    return (
        subprocess.run(
            [
                "git",
                "-C",
                str(REPOSITORY_ROOT),
                "status",
                "--porcelain",
                "--untracked-files=no",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == ""
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--hwnd", type=int, required=True)
    parser.add_argument("--authorize-execution-sha", required=True)
    parser.add_argument(
        "--confirm",
        help=f"live run requires exact token {OWNER_CONFIRMATION!r}",
    )
    parser.add_argument("--title", default="RuneLite")
    parser.add_argument("--max-passive", type=int, default=30)
    return parser.parse_args(argv)


def _prep_receipt_path(output: Path) -> Path:
    return output / "result.json"


def _load_ready_receipt(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("PREP receipt root is not an object")
    return payload


def _require_ready_for_zero_to_28(
    payload: dict[str, Any],
    *,
    expected_sha: str,
    expected_hwnd: int,
) -> None:
    if payload.get("ready_for_mining") is not True:
        raise RuntimeError("PREP did not publish READY FOR MINING")
    if payload.get("git_sha") != expected_sha:
        raise RuntimeError("PREP READY receipt SHA does not match authorized execution SHA")
    if payload.get("mining_input_authority") is not False:
        raise RuntimeError("PREP receipt illegally contains mining input authority")
    if payload.get("prep_authority_relinquished") is not True:
        raise RuntimeError("PREP did not relinquish setup authority")

    final_window = payload.get("final_window")
    if not isinstance(final_window, dict) or final_window.get("hwnd") != expected_hwnd:
        raise RuntimeError("PREP READY receipt HWND does not match authorized HWND")
    if (
        final_window.get("client_width") != 1005
        or final_window.get("client_height") != 1078
        or final_window.get("dpi") != 96
        or final_window.get("foreground") is not True
        or final_window.get("visible") is not True
        or final_window.get("minimized") is not False
    ):
        raise RuntimeError("PREP READY receipt window facts are not exact")

    observations = payload.get("observations")
    if not isinstance(observations, list) or not observations:
        raise RuntimeError("PREP READY receipt has no final scene observation")
    final_observation = observations[-1]
    if not isinstance(final_observation, dict):
        raise RuntimeError("PREP final observation is malformed")
    if final_observation.get("inventory_occupied") != 0:
        raise RuntimeError("official 0->28 attempt requires Inventory exactly 0/28")
    confidence = final_observation.get("inventory_confidence")
    if not isinstance(confidence, (int, float)) or confidence < 0.8:
        raise RuntimeError("PREP Inventory confidence is below 0.8")
    if final_observation.get("resource_supported") is not True:
        raise RuntimeError("PREP final Resource view is not supported")
    if final_observation.get("matched_landmarks", 0) < 5:
        raise RuntimeError("PREP final Resource landmarks are below 5/6")
    zones = final_observation.get("matched_zones")
    if set(zones if isinstance(zones, list) else ()) != {
        "north_west",
        "north_east",
        "south_west",
    }:
        raise RuntimeError("PREP final Resource zones are not the exact required set")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if not args.live:
        print(
            json.dumps(
                {
                    "mode": "read_only_plan",
                    "live_input_performed": False,
                    "sequence": [
                        "codex_full_reset_camera_prep",
                        "fresh_ready_receipt",
                        "inventory_exactly_0_of_28",
                        "separate_mining_gate",
                        "mine_until_28_or_first_stop",
                    ],
                    "inventory_bootstrap": "proven_empty_required",
                    "maximum_clicks_per_attempt": 1,
                    "navigation_started_on_full": False,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.hwnd <= 0:
        print("STOP: --hwnd must be positive", file=sys.stderr)
        return 2
    if args.confirm != OWNER_CONFIRMATION:
        print(f"STOP: --confirm must equal {OWNER_CONFIRMATION}", file=sys.stderr)
        return 2
    try:
        head = _git_head()
        clean = _tracked_checkout_clean()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"STOP: unable to verify exact checkout: {exc}", file=sys.stderr)
        return 2
    if not clean:
        print("STOP: tracked checkout is dirty", file=sys.stderr)
        return 2
    if args.authorize_execution_sha != head:
        print(
            f"STOP: authorized SHA must equal exact HEAD {head}",
            file=sys.stderr,
        )
        return 2

    prep_id = f"prep-auto-28-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    prep_output = REPOSITORY_ROOT / "diagnostics" / prep_id
    print("=== PHASE 1/2: AUTOMATIC RUNE LITE PREP ===")
    prep_rc = prep_auto.main(
        [
            "--apply",
            "--hwnd",
            str(args.hwnd),
            "--confirm",
            prep_auto.PREP_CONFIRMATION,
            "--title",
            args.title,
            "--output",
            str(prep_output),
        ]
    )
    receipt_path = _prep_receipt_path(prep_output)
    if prep_rc != 0 or not receipt_path.is_file():
        print("STOP: automatic PREP did not reach READY; mining was not started")
        return 2
    try:
        ready = _load_ready_receipt(receipt_path)
        _require_ready_for_zero_to_28(
            ready,
            expected_sha=head,
            expected_hwnd=args.hwnd,
        )
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"STOP: PREP READY handoff rejected: {exc}", file=sys.stderr)
        return 2

    print("=== PHASE 2/2: CONTROLLED 0->28 MINING ===")
    mining.WindowsMiningToFullBackend = SafeWindowsMiningToFullBackend
    return mining.main(
        [
            "--live",
            "--hwnd",
            str(args.hwnd),
            "--authorize-execution-sha",
            head,
            "--confirm",
            mining.EXPECTED_CONFIRMATION,
            "--title",
            args.title,
            "--max-passive",
            str(args.max_passive),
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
