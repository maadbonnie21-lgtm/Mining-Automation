#!/usr/bin/env python3
"""CLI tool for performing one controlled mining attempt.

Safety invariants:
- By default, runs in dry-run / preflight mode (sends NO mouse or keyboard input).
- When --live is passed, performs strictly at most ONE click against the selected
  AVAILABLE iron rock, captures a strictly newer frame, and reobserves.
- Success is reported ONLY if rock depletion and/or inventory +1 is verified.
- Otherwise, immediately STOPS with diagnostic evidence preserved.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure src is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mining_automation.capture import CaptureSource
from mining_automation.capture.windows import WindowsCaptureBackend
from mining_automation.controlled_mining_runner import (
    DEFAULT_WINDOW_TITLE_SUBSTRING,
    DryRunWin32MiningInputDevice,
    ProductionMiningPerceptionEvaluator,
    RealWin32MiningInputDevice,
    execute_one_controlled_attempt,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n", 1)[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--live",
        action="store_true",
        default=False,
        help="authorize one live Win32 mouse click on the selected iron target (default: false, dry-run only)",
    )
    parser.add_argument(
        "--title",
        default=DEFAULT_WINDOW_TITLE_SUBSTRING,
        help=f"RuneLite title substring to search for (default: {DEFAULT_WINDOW_TITLE_SUBSTRING!r})",
    )
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=Path("diagnostics/mining_attempts"),
        help="directory to store structured attempt evidence JSON (default: diagnostics/mining_attempts)",
    )
    parser.add_argument(
        "--dwell",
        type=float,
        default=0.8,
        help="seconds to wait after click before capturing the post-attempt frame (default: 0.8s)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    print("=" * 60)
    print(" CONTROLLED MINING ATTEMPT RUNNER")
    print("=" * 60)
    print(f"Mode: {'LIVE (MAX 1 CLICK)' if args.live else 'DRY RUN / PREFLIGHT (NO INPUT)'}")
    print(f"Target Title Substring: {args.title!r}")
    print(f"Post-Attempt Dwell:     {args.dwell:.2f}s")
    print(f"Evidence Directory:     {args.evidence_dir}")
    print("-" * 60)

    try:
        backend = WindowsCaptureBackend(title_substring=args.title)
    except Exception as exc:
        print(f"[STOP] Failed to initialize Windows capture backend: {exc}", file=sys.stderr)
        return 1

    source = CaptureSource(backend, max_consecutive_failures=2)

    try:
        source.open()
    except Exception as exc:
        print(f"[STOP] Failed to open capture source: {exc}", file=sys.stderr)
        return 1

    evaluator = ProductionMiningPerceptionEvaluator()

    if args.live:
        print("\n[LIVE MODE CONFIRMATION]")
        print("  - Limited to maximum ONE single left click.")
        print("  - No automatic retries.")
        print("  - No camera adjustments, navigation, or banking.")
        print("  - Click dispatch is NOT success.")
        print("  - A strictly newer frame will be captured to verify depletion/inventory.")
        input_device = RealWin32MiningInputDevice()
    else:
        print("\n[DRY RUN MODE]")
        print("  - Emulating input device (no physical mouse movement or click).")
        print("  - Pass --live to authorize exactly one real click.")
        input_device = DryRunWin32MiningInputDevice()

    try:
        outcome = execute_one_controlled_attempt(
            capture_source=source,
            evaluator=evaluator,
            input_device=input_device,
            window_title=args.title,
            evidence_dir=args.evidence_dir,
            post_attempt_delay_s=args.dwell,
            capture_hwnd_supplier=lambda: (
                backend.selected_window.hwnd
                if backend.selected_window is not None
                else None
            ),
        )
    finally:
        try:
            source.close()
        except Exception:
            pass

    print("\n" + "=" * 60)
    print(" ATTEMPT OUTCOME SUMMARY")
    print("=" * 60)
    print(f"Success:          {outcome.success}")
    print(f"Progress Kind:    {outcome.progress_kind.value}")
    print(f"Stop Reason:      {outcome.stop_reason.value}")
    if outcome.target_window:
        tw = outcome.target_window
        print(f"Target Window:    HWND {tw.hwnd} | {tw.client_width}x{tw.client_height} @ DPI {tw.dpi} | {tw.title!r}")
    if outcome.proposal:
        p = outcome.proposal
        print(f"Selected Target:  {p.target_id} @ region {p.target_region}")
        print(f"Pre-Inventory:    {p.inventory_occupied_before}/28 slots occupied")
    if outcome.receipt:
        r = outcome.receipt
        print(f"Click Dispatched: count={r.click_dispatch_count}, succeeded={r.dispatch_succeeded}")
    if outcome.evidence_path:
        print(f"Evidence File:    {outcome.evidence_path}")
    if outcome.detail:
        print(f"Detail:           {outcome.detail}")
    print("=" * 60)

    if outcome.success:
        print("\n>>> RESULT: SUCCESS - Mining progress verified by reobservation.")
        return 0
    else:
        print(f"\n>>> RESULT: STOPPED - No verified progress or gate failure ({outcome.stop_reason.value}).")
        return 2


if __name__ == "__main__":
    sys.exit(main())
