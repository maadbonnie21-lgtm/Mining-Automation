#!/usr/bin/env python3
"""First-live PREP entry point with exact HWND and READY-frame binding.

Read-only mode may diagnose by title alone.  ``--apply`` requires the exact RuneLite
HWND that the operator is authorizing for this PREP session.  This command ends at
READY and never starts mining; mining requires a separate later authorization and
separate command.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

import runelite_prep as legacy_prep  # noqa: E402
from mining_automation.validation.runelite_prep import (  # noqa: E402
    PREP_CONFIRMATION,
    PrepBackend,
    PrepMode,
    PrepOperationError,
    PrepStopReason,
    PrepWindowSnapshot,
    PrepPoseReferenceReceipt,
    PrepActionReceipt,
    PrepCameraStep,
    PrepSceneObservation,
    RunelitePrepResult,
    run_runelite_prep,
)
from mining_automation.validation.runelite_prep_live_boundary import (  # noqa: E402
    ExactHwndPrepBackend,
    bind_ready_receipt_to_observation_window,
)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="explicitly permit bounded PREP-only setup correction",
    )
    parser.add_argument(
        "--confirm",
        help=f"apply mode requires exact token {PREP_CONFIRMATION!r}",
    )
    parser.add_argument(
        "--hwnd",
        type=int,
        help="exact RuneLite HWND authorized for this PREP session; required with --apply",
    )
    parser.add_argument("--title", default="RuneLite")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="new local evidence directory; default diagnostics/runelite-prep-live-<id>",
    )
    return parser.parse_args(argv)


class _ExactHwndRequiredBackend(PrepBackend):
    """Emit a typed zero-mutation receipt when apply lacks exact HWND authority."""

    @staticmethod
    def _raise() -> None:
        raise PrepOperationError(
            PrepStopReason.WINDOW_IDENTITY_CHANGED,
            "--apply requires an externally authorized exact RuneLite --hwnd; PREP sent zero input.",
        )

    def snapshot(self) -> PrepWindowSnapshot:
        self._raise()
        raise AssertionError

    def verify_pose_references(self) -> tuple[PrepPoseReferenceReceipt, ...]:
        self._raise()
        return ()

    def restore_window(self) -> PrepActionReceipt:
        self._raise()
        raise AssertionError

    def resize_client(self, width: int, height: int) -> PrepActionReceipt:
        del width, height
        self._raise()
        raise AssertionError

    def focus_window(self) -> PrepActionReceipt:
        self._raise()
        raise AssertionError

    def neutralize_cursor(self) -> PrepActionReceipt:
        self._raise()
        raise AssertionError

    def observe(self) -> PrepSceneObservation:
        self._raise()
        raise AssertionError

    def camera_action(self, step: PrepCameraStep) -> tuple[PrepActionReceipt, ...]:
        del step
        self._raise()
        return ()

    def cleanup(self) -> tuple[PrepActionReceipt, ...]:
        return ()


def _result_payload(result: RunelitePrepResult) -> dict[str, object]:
    payload = asdict(result)
    payload["generated_at_utc"] = datetime.now(UTC).isoformat()
    payload["first_live_boundary"] = "exact-hwnd-post-observation-window-v1"
    return payload


def _write_result(output: Path, result: RunelitePrepResult) -> Path:
    path = output / "result.json"
    path.write_text(
        json.dumps(_result_payload(result), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    mode = PrepMode.APPLY if args.apply else PrepMode.READ_ONLY
    prep_session_id = f"prep-live-{uuid.uuid4().hex[:12]}"
    output = args.output or (
        REPOSITORY_ROOT / "diagnostics" / f"runelite-prep-live-{prep_session_id}"
    )
    if output.exists():
        print(f"STOP: output path already exists: {output}", file=sys.stderr)
        return 2

    dirty_checkout = False
    strict_backend: ExactHwndPrepBackend | None = None
    try:
        git_sha = legacy_prep._exact_git_sha()
        checkout_clean = legacy_prep._checkout_clean()
    except (OSError, subprocess.CalledProcessError) as exc:
        git_sha = "0" * 40
        backend: PrepBackend = legacy_prep._ConstructionFailureBackend(
            f"Could not read exact Git checkout state: {exc}"
        )
    else:
        if not checkout_clean:
            dirty_checkout = True
            backend = legacy_prep._ConstructionFailureBackend(
                "PREP requires a clean Git checkout before diagnosis or apply."
            )
        elif mode is PrepMode.APPLY and args.hwnd is None:
            backend = _ExactHwndRequiredBackend()
        else:
            try:
                inner = legacy_prep.RealPrepBackend(
                    title_substring=args.title,
                    output=output,
                    prep_session_id=prep_session_id,
                )
                strict_backend = ExactHwndPrepBackend(
                    inner,
                    expected_hwnd=args.hwnd,
                )
                backend = strict_backend
            except Exception as exc:  # noqa: BLE001 - still emit a machine receipt
                backend = legacy_prep._ConstructionFailureBackend(
                    "Could not construct exact-HWND real PREP backend: "
                    f"{type(exc).__name__}: {exc}"
                )

    # The diagnostics root is ignored by Git. Create it only after measuring the
    # checkout so PREP cannot dirty its own preflight.
    output.mkdir(parents=True)

    result = run_runelite_prep(
        backend,
        mode=mode,
        git_sha=git_sha,
        prep_session_id=prep_session_id,
        confirm=args.confirm,
        # First-live PREP remains camera-free. Unsupported Resource -> STOP.
        camera_steps=(),
    )
    if strict_backend is not None:
        result = bind_ready_receipt_to_observation_window(result, strict_backend)
    if dirty_checkout and isinstance(backend, legacy_prep._ConstructionFailureBackend):
        result = replace(
            result,
            ready_for_mining=False,
            stop_reason=PrepStopReason.DIRTY_CHECKOUT,
            detail="Checkout is dirty; exact first-live PREP authority is withheld.",
        )
    if result.ready_for_mining and not legacy_prep._checkout_clean():
        result = replace(
            result,
            ready_for_mining=False,
            stop_reason=PrepStopReason.DIRTY_CHECKOUT,
            detail="Checkout became dirty during PREP; READY is withheld.",
        )

    receipt = _write_result(output, result)
    legacy_prep._print_owner_summary(result, receipt)
    if result.ready_for_mining:
        print("\nSTOP HERE: PREP is complete. Mining is NOT authorized by this receipt.")
        print("A separate mining-only authorization and separate command are required.")
    return 0 if result.ready_for_mining else 2


if __name__ == "__main__":
    raise SystemExit(main())
