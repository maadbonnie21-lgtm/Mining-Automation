#!/usr/bin/env python3
"""Run PREP with the preserved Codex camera normalization candidate ladder.

This owner-facing PREP path reuses the exact bounded full-reset candidate family
preserved on ``codex/31-deterministic-camera-reacquisition`` instead of the
failed seven-step camera guess used in the first 2026-09-04 live attempt.

Each candidate independently resets yaw with the reviewed compass point,
saturates pitch, applies the preserved pitch/yaw offset, saturates zoom, applies
the preserved zoom offset, then returns to the unchanged production Resource
and Inventory gates. PREP stops on the first genuine READY observation or fails
closed after the bounded candidate list.

The Codex candidate family is experimental real-client preparation, not a claim
of release acceptance. It cannot mine, navigate, bank, weaken perception gates,
or convert diagnostic evidence into authority.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import uuid
from dataclasses import replace
from enum import StrEnum
from pathlib import Path

TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import runelite_prep as base  # noqa: E402

from mining_automation.safe_live_inventory import (  # noqa: E402
    SafeEmptyStartMiningPerceptionEvaluator,
)
from mining_automation.validation.camera_plan import (  # noqa: E402
    REVIEWED_CAMERA_WHEEL_POINT,
    REVIEWED_COMPASS_POINT,
    CameraHoldKey,
    CameraKeyHold,
    CameraPause,
    CameraPlan,
    CameraPlanError,
    CameraPlanRunner,
    CameraWheel,
    CompassClick,
)
from mining_automation.validation.runelite_prep import (  # noqa: E402
    PREP_CONFIRMATION,
    PrepActionReceipt,
    PrepBackend,
    PrepCameraStep,
    PrepMode,
    PrepOperationError,
    PrepStopReason,
    run_runelite_prep,
)
from mining_automation.validation.windows_camera import WindowsCameraError  # noqa: E402


class AutoCameraStep(StrEnum):
    """One whole preserved Codex full-reset candidate."""

    CODEX_CANDIDATE = "codex_full_reset_candidate"


# Exact frozen candidate ladder copied from Codex Issue #31 camera work.
# Tuple entries are (down-pitch seconds from UP endpoint, right-yaw seconds).
CODEX_CAMERA_CANDIDATE_OFFSETS: tuple[tuple[float, float], ...] = (
    (0.60, 0.05),
    (0.58, 0.05),
    (0.62, 0.05),
    (0.56, 0.05),
    (0.64, 0.05),
    (0.60, 0.04),
    (0.58, 0.04),
    (0.62, 0.04),
    (0.60, 0.06),
    (0.58, 0.06),
    (0.62, 0.06),
)
CODEX_PITCH_ENDPOINT_HOLD_S = 3.0
CODEX_POST_COMPASS_SETTLE_S = 0.5
CODEX_ZOOM_SATURATION_DETENTS = 96
CODEX_ZOOM_OFFSET_DETENTS = -17


def _build_codex_camera_candidates() -> tuple[CameraPlan, ...]:
    return tuple(
        CameraPlan(
            f"codex-issue31-production-gated-candidate-{index:02d}",
            (
                CompassClick(*REVIEWED_COMPASS_POINT),
                CameraPause(CODEX_POST_COMPASS_SETTLE_S),
                CameraKeyHold(CameraHoldKey.RIGHT, yaw_offset_s),
                CameraKeyHold(CameraHoldKey.UP, CODEX_PITCH_ENDPOINT_HOLD_S),
                CameraKeyHold(CameraHoldKey.DOWN, pitch_offset_s),
                CameraWheel(
                    *REVIEWED_CAMERA_WHEEL_POINT,
                    CODEX_ZOOM_SATURATION_DETENTS,
                ),
                CameraWheel(
                    *REVIEWED_CAMERA_WHEEL_POINT,
                    CODEX_ZOOM_OFFSET_DETENTS,
                ),
            ),
        )
        for index, (pitch_offset_s, yaw_offset_s) in enumerate(
            CODEX_CAMERA_CANDIDATE_OFFSETS,
            start=1,
        )
    )


CODEX_CAMERA_PLANS = _build_codex_camera_candidates()
AUTO_CAMERA_SEARCH_STEPS: tuple[AutoCameraStep, ...] = tuple(
    AutoCameraStep.CODEX_CANDIDATE for _ in CODEX_CAMERA_PLANS
)


class CodexCameraPrepBackend(base.RealPrepBackend):
    """PREP backend using safe empty-start Inventory and Codex camera plans."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.inventory_evaluator = SafeEmptyStartMiningPerceptionEvaluator()
        self._codex_candidate_index = 0

    def camera_action(
        self,
        step: PrepCameraStep | AutoCameraStep,
    ) -> tuple[PrepActionReceipt, ...]:
        if step is not AutoCameraStep.CODEX_CANDIDATE:
            if not isinstance(step, PrepCameraStep):
                raise PrepOperationError(
                    PrepStopReason.CAMERA_INPUT_REJECTED,
                    f"Unsupported automatic camera step {step!r}.",
                )
            return super().camera_action(step)

        if self._codex_candidate_index >= len(CODEX_CAMERA_PLANS):
            raise PrepOperationError(
                PrepStopReason.CAMERA_SEARCH_EXHAUSTED,
                "Codex camera candidate ladder was requested beyond its frozen bound.",
            )
        plan = CODEX_CAMERA_PLANS[self._codex_candidate_index]
        self._codex_candidate_index += 1

        try:
            receipt = CameraPlanRunner(self._control(), time.sleep).run(plan)
        except (CameraPlanError, WindowsCameraError, OSError, ValueError) as exc:
            raise PrepOperationError(
                PrepStopReason.CAMERA_INPUT_REJECTED,
                f"Codex camera candidate {plan.name!r} failed closed: {exc}",
            ) from exc

        converted: list[PrepActionReceipt] = []
        for action_receipt in receipt.action_receipts:
            if not action_receipt.input_receipts:
                converted.append(
                    PrepActionReceipt(
                        action=f"{plan.name}:pause",
                        requested_events=0,
                        completed_events=0,
                        detail=(
                            f"candidate={self._codex_candidate_index}; "
                            f"action_index={action_receipt.action_index}; no-input settle"
                        ),
                    )
                )
                continue
            for input_receipt in action_receipt.input_receipts:
                converted.append(
                    self._convert_camera_receipt(
                        input_receipt,
                        detail=(
                            f"candidate={self._codex_candidate_index}; "
                            f"plan={plan.name}; action_index={action_receipt.action_index}"
                        ),
                    )
                )
        return tuple(converted)


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
        return base.main(base_argv)

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
                "PREP requires a clean tracked Git checkout before diagnosis or apply."
            )
        else:
            try:
                real_backend = CodexCameraPrepBackend(
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
        "AUTO CAMERA PREP: Codex Issue #31 full-reset ladder enabled — "
        f"maximum {len(CODEX_CAMERA_PLANS)} candidates"
    )
    result = run_runelite_prep(
        backend,
        mode=PrepMode.APPLY,
        git_sha=git_sha,
        prep_session_id=prep_session_id,
        confirm=args.confirm,
        camera_steps=AUTO_CAMERA_SEARCH_STEPS,  # type: ignore[arg-type]
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
