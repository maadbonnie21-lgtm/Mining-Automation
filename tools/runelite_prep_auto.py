#!/usr/bin/env python3
"""Run PREP with the bounded evidence-backed RuneLite camera correction sequence.

The ordinary PREP entry point remains useful for read-only diagnosis.  This entry
point restores the owner workflow requested for the first 0->28 experiment: when
``--apply`` is explicitly authorized, PREP itself performs the small retained
2026-09-03 zoom/pitch sequence and re-observes after every step.  It stops as soon
as the unchanged Resource gate passes, or fails closed when the bounded sequence
is exhausted.

This is not a blind camera random walk and it grants no mining, navigation,
banking, Resource-release, or Inventory-release authority.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import uuid
from dataclasses import replace
from pathlib import Path

TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import runelite_prep as base  # noqa: E402

from mining_automation.validation.runelite_prep import (  # noqa: E402
    PREP_CONFIRMATION,
    PrepBackend,
    PrepCameraStep,
    PrepMode,
    PrepStopReason,
    run_runelite_prep,
)

# Retained real-client camera evidence from 2026-09-03. This is a bounded first
# automatic correction path, not a claim that one universal deterministic camera
# recipe has already been proven. PREP captures and reevaluates after every step,
# stops immediately when the frozen gate passes, and otherwise stops on exhaustion.
AUTO_CAMERA_SEARCH_STEPS: tuple[PrepCameraStep, ...] = (
    PrepCameraStep.WHEEL_POSITIVE_1,
    PrepCameraStep.WHEEL_POSITIVE_1,
    PrepCameraStep.WHEEL_POSITIVE_1,
    PrepCameraStep.WHEEL_POSITIVE_1,
    PrepCameraStep.PITCH_DOWN_100MS,
    PrepCameraStep.PITCH_DOWN_100MS,
    PrepCameraStep.PITCH_UP_50MS,
)


def _split_expected_hwnd(argv: list[str]) -> tuple[int | None, list[str]]:
    """Extract the exact optional HWND without widening the base PREP CLI."""

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--hwnd", type=int)
    known, remaining = parser.parse_known_args(argv)
    return known.hwnd, remaining


def _run_auto_apply(argv: list[str]) -> int:
    expected_hwnd, base_argv = _split_expected_hwnd(argv)
    args = base._parse_args(base_argv)
    if expected_hwnd is not None and expected_hwnd <= 0:
        print("STOP: --hwnd must be a positive exact RuneLite HWND", file=sys.stderr)
        return 2
    if not args.apply:
        # Preserve the original zero-input read-only behavior when --apply is absent.
        return base.main(base_argv)
    if args.confirm != PREP_CONFIRMATION:
        # Let the controller produce the canonical confirmation STOP receipt.
        pass

    prep_session_id = f"prep-auto-{uuid.uuid4().hex[:12]}"
    output = args.output or (
        base.REPOSITORY_ROOT / "diagnostics" / f"runelite-prep-{prep_session_id}"
    )
    if output.exists():
        print(f"STOP: output path already exists: {output}", file=sys.stderr)
        return 2

    dirty_checkout = False
    try:
        git_sha = base._exact_git_sha()
        checkout_clean = base._checkout_clean()
    except (OSError, subprocess.CalledProcessError) as exc:
        git_sha = "0" * 40
        backend: PrepBackend = base._ConstructionFailureBackend(
            f"Could not read exact Git checkout state: {exc}"
        )
    else:
        if not checkout_clean:
            dirty_checkout = True
            backend = base._ConstructionFailureBackend(
                "PREP requires a clean Git checkout before diagnosis or apply; "
                "commit/stash unrelated changes first."
            )
        else:
            try:
                real_backend = base.RealPrepBackend(
                    title_substring=args.title,
                    output=output,
                    prep_session_id=prep_session_id,
                )
                # Discovery is read-only. Bind the explicitly authorized HWND before
                # any PREP camera/window action can run.
                discovered_hwnd = real_backend.hwnd
                if expected_hwnd is not None and discovered_hwnd != expected_hwnd:
                    backend = base._ConstructionFailureBackend(
                        "Explicit PREP HWND does not match the uniquely discovered "
                        f"RuneLite HWND: expected {expected_hwnd}, got {discovered_hwnd}."
                    )
                else:
                    backend = real_backend
            except Exception as exc:  # noqa: BLE001 - emit one fail-closed receipt
                backend = base._ConstructionFailureBackend(
                    "Could not construct/bind real Windows PREP backend: "
                    f"{type(exc).__name__}: {exc}"
                )

    output.mkdir(parents=True)
    print(
        "AUTO CAMERA PREP: enabled - "
        f"maximum {len(AUTO_CAMERA_SEARCH_STEPS)} measured correction steps"
    )
    result = run_runelite_prep(
        backend,
        mode=PrepMode.APPLY,
        git_sha=git_sha,
        prep_session_id=prep_session_id,
        confirm=args.confirm,
        camera_steps=AUTO_CAMERA_SEARCH_STEPS,
    )
    if dirty_checkout and isinstance(backend, base._ConstructionFailureBackend):
        result = replace(
            result,
            ready_for_mining=False,
            stop_reason=PrepStopReason.DIRTY_CHECKOUT,
            detail=backend.detail,
        )
    if result.ready_for_mining and not base._checkout_clean():
        result = replace(
            result,
            ready_for_mining=False,
            stop_reason=PrepStopReason.DIRTY_CHECKOUT,
            detail=(
                "Checkout became dirty during PREP; READY is withheld until the "
                "exact mining checkout is clean."
            ),
        )
    receipt = base._write_result(output, result)
    base._print_owner_summary(result, receipt)
    return 0 if result.ready_for_mining else 2


def main(argv: list[str] | None = None) -> int:
    return _run_auto_apply(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())
