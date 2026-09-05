#!/usr/bin/env python3
"""Read-only startup resolver for the retained successful RuneLite mining poses.

The prior automatic camera action ladders are intentionally retired from the P0
path.  GitHub Issue #31 did not prove repeatable arbitrary-camera recovery, and
both attempted ladders failed on the real client on 2026-09-04.

This entry point now performs no camera input.  It binds the exact RuneLite HWND,
normalizes only the reviewed window geometry/focus state through the base PREP
backend, then evaluates the current frame against the retained September 3 pose
references and their existing software-registration path.  READY is possible
only when a fresh frame passes the unchanged Resource 0.12 / 5-of-6 / all-three-
zone gate and Inventory is known at or above 0.8.

Mining, navigation, banking, and Resource/Inventory release authority remain
absent.  A later software-normalization implementation must land behind new
replay/negative evidence; this module will not invent another open-loop camera
sequence.
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

from mining_automation.safe_live_inventory import (  # noqa: E402
    SafeEmptyStartMiningPerceptionEvaluator,
)
from mining_automation.validation.runelite_prep import (  # noqa: E402
    PrepBackend,
    PrepMode,
    PrepStopReason,
    run_runelite_prep,
)

PREP_CONFIRMATION = base.PREP_CONFIRMATION
AUTO_CAMERA_SEARCH_STEPS: tuple[()] = ()


class RetainedPosePrepBackend(base.RealPrepBackend):
    """Use safe empty-start Inventory with the retained pose registration path."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.inventory_evaluator = SafeEmptyStartMiningPerceptionEvaluator()


def _split_expected_hwnd(argv: list[str]) -> tuple[int | None, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--hwnd", type=int)
    known, remaining = parser.parse_known_args(argv)
    return known.hwnd, remaining


def _run_apply(argv: list[str]) -> int:
    expected_hwnd, base_argv = _split_expected_hwnd(argv)
    args = base._parse_args(base_argv)
    if expected_hwnd is not None and expected_hwnd <= 0:
        print("STOP: --hwnd must be a positive exact RuneLite HWND", file=sys.stderr)
        return 2
    if not args.apply:
        return base.main(base_argv)

    prep_session_id = f"prep-retained-pose-{uuid.uuid4().hex[:12]}"
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
                "PREP requires a clean tracked Git checkout before diagnosis or apply."
            )
        else:
            try:
                real_backend = RetainedPosePrepBackend(
                    title_substring=args.title,
                    output=output,
                    prep_session_id=prep_session_id,
                )
                discovered_hwnd = real_backend.hwnd
                if expected_hwnd is not None and discovered_hwnd != expected_hwnd:
                    backend = base._ConstructionFailureBackend(
                        "Explicit PREP HWND does not match the uniquely discovered "
                        f"RuneLite HWND: expected {expected_hwnd}, got {discovered_hwnd}."
                    )
                else:
                    backend = real_backend
            except Exception as exc:  # noqa: BLE001 - one fail-closed receipt
                backend = base._ConstructionFailureBackend(
                    "Could not construct/bind real Windows PREP backend: "
                    f"{type(exc).__name__}: {exc}"
                )

    output.mkdir(parents=True)
    print(
        "STARTUP RESOLVER: retained September 3 poses + software registration; "
        "camera input disabled"
    )
    result = run_runelite_prep(
        backend,
        mode=PrepMode.APPLY,
        git_sha=git_sha,
        prep_session_id=prep_session_id,
        confirm=args.confirm,
        camera_steps=(),
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
    return _run_apply(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())
