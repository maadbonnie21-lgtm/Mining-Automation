#!/usr/bin/env python3
"""PREP RuneLite to READY, then stop for separate mining authorization.

This legacy convenience entry point no longer starts mining automatically.  It owns
only one exact-SHA/exact-HWND PREP attempt:

1. camera-free PREP may normalize the exact authorized RuneLite HWND;
2. PREP must return a genuine fresh READY receipt with Inventory exactly 0/28;
3. the full frozen Resource/Inventory/window/authority contract is revalidated;
4. PREP authority is relinquished and this process stops;
5. mining requires a new operator action using the separate mining-only command.

No READY result means zero mining clicks. A READY result also means zero mining clicks
from this process.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

TOOLS_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = TOOLS_ROOT.parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

import run_mining_to_full as mining  # noqa: E402
import runelite_prep_live as prep_live  # noqa: E402

OWNER_CONFIRMATION = "AUTO_PREP_TO_READY_ONLY"


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
        help=f"PREP run requires exact token {OWNER_CONFIRMATION!r}",
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


def _require_exact_number(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"PREP {field} is not numeric")
    return float(value)


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
    if payload.get("prep_authority_relinquished") is not True:
        raise RuntimeError("PREP did not relinquish setup authority")

    for authority_field in (
        "mining_input_authority",
        "navigation_authority",
        "banking_authority",
        "inventory_release_authority",
        "resource_release_authority",
    ):
        if payload.get(authority_field) is not False:
            raise RuntimeError(f"PREP receipt illegally grants {authority_field}")

    frozen_exact = {
        "resource_threshold": 0.12,
        "resource_landmark_count": 6,
        "resource_landmark_quorum": 5,
        "resource_required_zone_count": 3,
        "inventory_floor": 0.8,
        "inventory_capacity": 28,
    }
    for field, expected in frozen_exact.items():
        if payload.get(field) != expected:
            raise RuntimeError(f"PREP receipt changed frozen invariant {field}")

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
    if final_observation.get("gameplay_ready") is not True:
        raise RuntimeError("PREP final gameplay chrome is not ready")
    if final_observation.get("inventory_occupied") != 0:
        raise RuntimeError("official 0->28 attempt requires Inventory exactly 0/28")
    confidence = _require_exact_number(
        final_observation.get("inventory_confidence"),
        field="Inventory confidence",
    )
    if confidence < 0.8:
        raise RuntimeError("PREP Inventory confidence is below 0.8")
    if final_observation.get("resource_supported") is not True:
        raise RuntimeError("PREP final Resource view is not supported")

    matched = final_observation.get("matched_landmarks")
    if isinstance(matched, bool) or not isinstance(matched, int) or matched < 5:
        raise RuntimeError("PREP final Resource landmarks are below 5/6")
    zones = final_observation.get("matched_zones")
    required_zones = {"north_west", "north_east", "south_west"}
    if not isinstance(zones, list) or len(zones) != 3 or set(zones) != required_zones:
        raise RuntimeError("PREP final Resource zones are not the exact required set")

    distances = final_observation.get("landmark_distances")
    if not isinstance(distances, list) or len(distances) != 6:
        raise RuntimeError("PREP final Resource landmark distance set is not exactly 6")
    names: set[str] = set()
    within_threshold = 0
    for record in distances:
        if not isinstance(record, list) or len(record) != 2:
            raise RuntimeError("PREP Resource landmark distance record is malformed")
        name, distance_value = record
        if not isinstance(name, str) or not name or name in names:
            raise RuntimeError("PREP Resource landmark names are missing or duplicated")
        names.add(name)
        distance = _require_exact_number(distance_value, field="Resource landmark distance")
        if distance <= 0.12:
            within_threshold += 1
    if within_threshold < 5:
        raise RuntimeError("PREP Resource landmark distances fail the unchanged 0.12 / 5-of-6 gate")


def _separate_mining_command(
    *,
    head: str,
    hwnd: int,
    title: str,
    max_passive: int,
) -> list[str]:
    return [
        sys.executable,
        str(TOOLS_ROOT / "run_mining_to_full_safe.py"),
        "--live",
        "--hwnd",
        str(hwnd),
        "--authorize-execution-sha",
        head,
        "--confirm",
        mining.EXPECTED_CONFIRMATION,
        "--title",
        title,
        "--max-passive",
        str(max_passive),
    ]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if not args.live:
        print(
            json.dumps(
                {
                    "mode": "read_only_plan",
                    "live_input_performed": False,
                    "sequence": [
                        "camera_free_exact_hwnd_prep",
                        "fresh_ready_receipt",
                        "inventory_exactly_0_of_28",
                        "relinquish_prep_authority",
                        "stop_before_mining",
                        "separate_external_mining_authorization",
                    ],
                    "maximum_clicks_per_attempt": 0,
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

    prep_id = f"prep-28-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    prep_output = REPOSITORY_ROOT / "diagnostics" / prep_id
    print("=== PREP: EXACT-SHA/HWND CAMERA-FREE RUNE LITE PREP ===")
    prep_rc = prep_live.main(
        [
            "--apply",
            "--authorize-execution-sha",
            head,
            "--hwnd",
            str(args.hwnd),
            "--confirm",
            prep_live.PREP_CONFIRMATION,
            "--title",
            args.title,
            "--output",
            str(prep_output),
        ]
    )
    receipt_path = _prep_receipt_path(prep_output)
    if prep_rc != 0 or not receipt_path.is_file():
        print("STOP: PREP did not reach READY; mining was not started")
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

    next_command = _separate_mining_command(
        head=head,
        hwnd=args.hwnd,
        title=args.title,
        max_passive=args.max_passive,
    )
    print("=== PREP COMPLETE: MINING NOT STARTED ===")
    print("PREP authority is relinquished. A new mining-only authorization is required.")
    print("Separate mining command:")
    print(subprocess.list2cmdline(next_command))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
