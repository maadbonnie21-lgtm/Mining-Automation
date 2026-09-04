#!/usr/bin/env python3
"""Run the evidence-derived RuneLite mining-only loop to exact 28/28.

The default mode is read-only planning. Live input requires all of:

* ``--live``
* ``--hwnd`` bound to the already-inspected RuneLite window
* ``--authorize-execution-sha`` equal to the clean checkout's exact HEAD
* ``--confirm MINE_TO_FULL_28_FAIL_CLOSED``

The command never resizes, minimizes, activates, navigates, or banks. It stops
at the first uncertainty and stops successfully at Inventory 28/28.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mining_automation.capture import CaptureSource, Frame
from mining_automation.capture.windows import WindowsCaptureBackend
from mining_automation.controlled_mining_runner import (
    ProductionMiningPerceptionEvaluator,
    RealWin32MiningInputDevice,
)
from mining_automation.mining_loop_runtime import (
    CleanMiningObservation,
    MiningDispatchResult,
    MiningHoverProof,
    MiningLoopConfig,
    MiningWindowSnapshot,
    PassiveMiningObservation,
    run_mining_until_full,
)
from mining_automation.mining_slice import (
    MiningAttemptDispatchReceipt,
    MiningAttemptProposal,
    PerceptionEpoch,
    ResourceViewState,
    assemble_atomic_mining_world_state,
)
from mining_automation.validation.windows_camera import RealWindowsCameraApi

EXPECTED_CONFIRMATION = "MINE_TO_FULL_28_FAIL_CLOSED"
NEUTRAL_CLIENT_POINT = (100, 100)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _window_snapshot(window: Any, foreground_hwnd: int | None) -> MiningWindowSnapshot:
    if foreground_hwnd is None:
        raise RuntimeError("GetForegroundWindow returned no HWND")
    return MiningWindowSnapshot(
        hwnd=int(window.hwnd),
        foreground_hwnd=int(foreground_hwnd),
        client_width=int(window.client_width),
        client_height=int(window.client_height),
        dpi=int(window.dpi),
        is_visible=bool(window.is_visible),
        is_minimized=bool(window.is_minimized),
    )


class WindowsMiningToFullBackend:
    """Real Windows adapter for the fail-closed repeated mining runtime."""

    def __init__(
        self,
        *,
        expected_hwnd: int,
        output: Path,
        session_id: str,
        title_substring: str,
        neutral_settle_s: float,
        hover_settle_s: float,
        passive_interval_s: float,
    ) -> None:
        self.expected_hwnd = expected_hwnd
        self.output = output
        self.session_id = session_id
        self.title_substring = title_substring
        self.neutral_settle_s = neutral_settle_s
        self.hover_settle_s = hover_settle_s
        self.passive_interval_s = passive_interval_s
        self.api = RealWindowsCameraApi()
        self.capture_backend = WindowsCaptureBackend(title_substring=title_substring)
        self.capture_source = CaptureSource(
            self.capture_backend,
            max_consecutive_failures=2,
        )
        self.inventory_evaluator = ProductionMiningPerceptionEvaluator()
        self.pose_detectors: dict[str, Any] | None = None
        self._mine_hover_signature: Any = None
        self._evaluate_resource: Any = None
        self.active_registration: dict[str, Any] = {
            "pose": None,
            "detector": None,
        }
        self.epoch_sequence = 0
        self.opened = False

    def open(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("live mining-to-full requires Windows")
        self.output.mkdir(parents=True, exist_ok=False)
        try:
            from run_proven_mining_loop import mine_hover_signature
            from run_three_rock_continuous_proof import (
                build_pose_detectors,
                evaluate_resource,
            )
        except ImportError as exc:
            raise RuntimeError(
                "live proof adapters are unavailable; verify the preserved proof "
                "tools and their NumPy dependency"
            ) from exc
        self._mine_hover_signature = mine_hover_signature
        self._evaluate_resource = evaluate_resource
        self.pose_detectors = build_pose_detectors()
        self.capture_source.open()
        self.opened = True

    def close(self) -> None:
        if self.opened:
            self.capture_source.close()
            self.opened = False

    def _verify_window(self) -> tuple[Any, MiningWindowSnapshot]:
        device = RealWin32MiningInputDevice()
        window = device.verify_target_window(self.title_substring)
        if window.hwnd != self.expected_hwnd:
            raise RuntimeError(
                f"RuneLite HWND changed: expected {self.expected_hwnd}, got {window.hwnd}"
            )
        snapshot = _window_snapshot(window, self.api.foreground_window())
        if snapshot.foreground_hwnd != self.expected_hwnd:
            raise RuntimeError(
                f"RuneLite is not foreground: expected {self.expected_hwnd}, "
                f"got {snapshot.foreground_hwnd}"
            )
        return window, snapshot

    def _capture(self, label: str) -> tuple[Frame, str]:
        frame = self.capture_source.capture()
        selected = self.capture_backend.selected_window
        if selected is None or selected.hwnd != self.expected_hwnd:
            raise RuntimeError(
                "captured HWND does not match the exact verified RuneLite HWND"
            )
        path = self.output / f"{self.epoch_sequence + 1:05d}-{label}.bgra"
        path.write_bytes(frame.payload)
        return frame, str(path)

    def _epoch(self, frame: Frame, label: str) -> PerceptionEpoch:
        self.epoch_sequence += 1
        return PerceptionEpoch(
            capture_source_id="windows-runelite",
            capture_session_id=self.session_id,
            cycle_id=f"{self.session_id}:{self.epoch_sequence}:{label}",
            cycle_sequence=self.epoch_sequence,
            frame_id=frame.frame_id,
            captured_monotonic_s=frame.captured_monotonic_s,
            frame_width=frame.width,
            frame_height=frame.height,
            frame_payload_sha256=_sha256(frame.payload),
            pixel_format="bgra8888",
        )

    def _neutralize_cursor(self) -> None:
        mapping = self.api.pointer_mapping(
            self.expected_hwnd,
            *NEUTRAL_CLIENT_POINT,
        )
        if not mapping.exact_round_trip:
            raise RuntimeError("neutral cursor coordinate round trip was not exact")
        screen = mapping.physical_screen.pair
        if self.api.root_window_at_point(*screen) != self.expected_hwnd:
            raise RuntimeError("neutral cursor point is occluded by another root window")
        if not self.api.move_cursor(*screen):
            raise RuntimeError("failed to move cursor to neutral client point")
        if self.api.cursor_position() != screen:
            raise RuntimeError("cursor did not remain at the neutral client point")
        time.sleep(self.neutral_settle_s)
        if self.api.foreground_window() != self.expected_hwnd:
            raise RuntimeError("foreground changed while waiting for tooltip clearance")

    @staticmethod
    def _registration_summary(
        pose: str | None,
        diagnoses: dict[str, Any],
    ) -> tuple[str | None, int | None, tuple[str, ...]]:
        registration: dict[str, Any] | None = None
        if isinstance(diagnoses.get("software_registration"), dict):
            registration = diagnoses["software_registration"]
        capture = diagnoses.get("registration_capture")
        if isinstance(capture, dict) and isinstance(
            capture.get("software_registration"),
            dict,
        ):
            registration = capture["software_registration"]
        if registration is not None:
            matched = registration.get("matched")
            zones = registration.get("zones")
            return (
                str(registration.get("kind") or "distributed_affine_registration"),
                int(matched) if isinstance(matched, int) else None,
                tuple(str(zone) for zone in zones) if isinstance(zones, list) else (),
            )
        if pose is not None and isinstance(diagnoses.get(pose), dict):
            record = diagnoses[pose]
            matched = record.get("matched")
            zones = record.get("zones")
            return (
                "exact_pose_profile",
                int(matched) if isinstance(matched, int) else None,
                tuple(str(zone) for zone in zones) if isinstance(zones, list) else (),
            )
        return None, None, ()

    def acquire_clean_observation(
        self,
        *,
        session_id: str,
        iteration: int,
    ) -> CleanMiningObservation:
        if session_id != self.session_id:
            raise RuntimeError("runtime session identity changed")
        _, snapshot = self._verify_window()
        self._neutralize_cursor()
        frame, frame_path = self._capture(f"iteration-{iteration:02d}-clean")
        epoch = self._epoch(frame, f"iteration-{iteration}-clean")
        if self.pose_detectors is None:
            raise RuntimeError("pose detectors were not opened")
        if self._evaluate_resource is None:
            raise RuntimeError("Resource proof adapter was not opened")
        resource, pose, diagnoses = self._evaluate_resource(
            frame,
            epoch,
            self.pose_detectors,
            frozenset(),
            self.active_registration,
        )
        if "software_registration" in diagnoses:
            registration_diagnoses = diagnoses
            frame, frame_path = self._capture(
                f"iteration-{iteration:02d}-clean-registered"
            )
            epoch = self._epoch(frame, f"iteration-{iteration}-clean-registered")
            resource, pose, fresh_diagnoses = self._evaluate_resource(
                frame,
                epoch,
                self.pose_detectors,
                frozenset(),
                self.active_registration,
            )
            diagnoses = {
                "registration_capture": registration_diagnoses,
                "fresh_registered_capture": fresh_diagnoses,
            }
        _, inventory = self.inventory_evaluator.evaluate(frame, epoch)
        now = max(time.monotonic(), frame.captured_monotonic_s)
        state = assemble_atomic_mining_world_state(
            resource=resource,
            inventory=inventory,
            evaluated_monotonic_s=now,
        )
        registration_kind, matched, zones = self._registration_summary(
            pose,
            diagnoses,
        )
        return CleanMiningObservation(
            state=state,
            window=snapshot,
            neutral_cursor_proven=True,
            frame_path=frame_path,
            pose_id=pose,
            registration_kind=registration_kind,
            matched_landmarks=matched,
            matched_zones=zones,
        )

    def prove_hover(
        self,
        proposal: MiningAttemptProposal,
        *,
        iteration: int,
    ) -> MiningHoverProof:
        _, snapshot = self._verify_window()
        rx, ry, rw, rh = proposal.target_region
        client_point = (rx + rw // 2, ry + rh // 2)
        mapping = self.api.pointer_mapping(self.expected_hwnd, *client_point)
        if not mapping.exact_round_trip:
            raise RuntimeError("target coordinate round trip was not exact")
        screen_point = mapping.physical_screen.pair
        root = self.api.root_window_at_point(*screen_point)
        if root != self.expected_hwnd:
            raise RuntimeError("target point is occluded by another root window")
        if not self.api.move_cursor(*screen_point):
            raise RuntimeError("failed to move cursor to the frozen target point")
        time.sleep(self.hover_settle_s)
        cursor_matches = self.api.cursor_position() == screen_point
        hover_frame, _ = self._capture(f"iteration-{iteration:02d}-hover")
        hover_epoch = self._epoch(hover_frame, f"iteration-{iteration}-hover")
        if self._mine_hover_signature is None:
            raise RuntimeError("hover proof adapter was not opened")
        signature = self._mine_hover_signature(
            hover_frame.payload,
            hover_frame.width,
        )
        proven = signature.get("proven_mine_iron_rocks") is True
        return MiningHoverProof(
            proposal_source_epoch=proposal.source_epoch,
            hover_epoch=hover_epoch,
            attempt_id=proposal.attempt_id,
            target_id=proposal.target_id,
            target_region=proposal.target_region,
            action_text="Mine Iron rocks" if proven else "UNPROVEN",
            interaction_proven=proven,
            window=snapshot,
            root_window_hwnd=root,
            client_point=client_point,
            screen_point=screen_point,
            cursor_matches_target=cursor_matches,
        )

    def dispatch_one_click(
        self,
        proposal: MiningAttemptProposal,
        proof: MiningHoverProof,
        *,
        iteration: int,
    ) -> MiningDispatchResult:
        del iteration
        _, snapshot = self._verify_window()
        if self.api.cursor_position() != proof.screen_point:
            raise RuntimeError("cursor moved after hover proof")
        root = self.api.root_window_at_point(*proof.screen_point)
        if root != self.expected_hwnd:
            raise RuntimeError("target became occluded after hover proof")
        device = RealWin32MiningInputDevice()
        receipt = device.dispatch_one_click(
            self.expected_hwnd,
            proposal.target_region,
            proposal,
        )
        audit = device.last_dispatch_audit or {}
        return MiningDispatchResult(
            receipt=receipt,
            window=snapshot,
            root_window_hwnd=root,
            client_point=proof.client_point,
            screen_point=proof.screen_point,
            cursor_matches_target=(self.api.cursor_position() == proof.screen_point),
            coordinate_round_trip_exact=(
                audit.get("coordinate_round_trip_exact") is True
            ),
        )

    def observe_passive(
        self,
        proposal: MiningAttemptProposal,
        receipt: MiningAttemptDispatchReceipt,
        *,
        iteration: int,
        passive_index: int,
    ) -> PassiveMiningObservation:
        del receipt
        time.sleep(self.passive_interval_s)
        frame, frame_path = self._capture(
            f"iteration-{iteration:02d}-passive-{passive_index:02d}"
        )
        epoch = self._epoch(
            frame,
            f"iteration-{iteration}-passive-{passive_index}",
        )
        resource, inventory = self.inventory_evaluator.evaluate(frame, epoch)
        selected_available: bool | None = None
        if resource.view is ResourceViewState.SUPPORTED:
            selected_available = next(
                (
                    item.available
                    for item in resource.resources
                    if item.resource_id == proposal.target_id
                ),
                None,
            )
        return PassiveMiningObservation(
            epoch=epoch,
            inventory_release=inventory.release,
            inventory=inventory.inventory,
            unknown_reason=inventory.unknown_reason,
            selected_target_available=selected_available,
            frame_path=frame_path,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--live", action="store_true", help="enable reviewed live input")
    parser.add_argument("--hwnd", type=int, help="exact expected RuneLite HWND")
    parser.add_argument(
        "--authorize-execution-sha",
        help="exact clean Git HEAD authorized for this run",
    )
    parser.add_argument(
        "--confirm",
        help=f"must equal {EXPECTED_CONFIRMATION!r} in live mode",
    )
    parser.add_argument("--title", default="RuneLite")
    parser.add_argument("--max-passive", type=int, default=30)
    parser.add_argument("--neutral-settle", type=float, default=1.0)
    parser.add_argument("--hover-settle", type=float, default=0.7)
    parser.add_argument("--passive-interval", type=float, default=1.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="new evidence directory; must not already exist",
    )
    return parser


def _plan(head: str) -> dict[str, object]:
    return {
        "mode": "read_only_plan",
        "git_sha": head,
        "live_input_performed": False,
        "required_confirmation": EXPECTED_CONFIRMATION,
        "required_geometry": [1005, 1078],
        "required_dpi": 96,
        "resource_threshold": 0.12,
        "resource_landmarks": 6,
        "resource_quorum": 5,
        "resource_zones_required": 3,
        "inventory_floor": 0.8,
        "inventory_capacity": 28,
        "exact_hover_action": "Mine Iron rocks",
        "maximum_clicks_per_attempt": 1,
        "navigation_started_on_full": False,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        head = _git("-C", str(root), "rev-parse", "HEAD")
        dirty = _git("-C", str(root), "status", "--porcelain")
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"STOP: unable to identify exact Git checkout: {exc}", file=sys.stderr)
        return 2

    if not args.live:
        print(json.dumps(_plan(head), indent=2, sort_keys=True))
        return 0

    if dirty:
        print("STOP: live run requires a clean exact Git checkout", file=sys.stderr)
        return 2
    if args.hwnd is None or args.hwnd <= 0:
        print("STOP: --hwnd must provide the exact positive RuneLite HWND", file=sys.stderr)
        return 2
    if args.authorize_execution_sha != head:
        print(
            f"STOP: --authorize-execution-sha must equal exact HEAD {head}",
            file=sys.stderr,
        )
        return 2
    if args.confirm != EXPECTED_CONFIRMATION:
        print(
            f"STOP: --confirm must equal {EXPECTED_CONFIRMATION}",
            file=sys.stderr,
        )
        return 2
    if args.max_passive <= 0:
        print("STOP: --max-passive must be positive", file=sys.stderr)
        return 2
    for value, label in (
        (args.neutral_settle, "--neutral-settle"),
        (args.hover_settle, "--hover-settle"),
        (args.passive_interval, "--passive-interval"),
    ):
        if value < 0.0:
            print(f"STOP: {label} must be non-negative", file=sys.stderr)
            return 2

    run_id = f"mining-to-full-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    output = args.output or root / "diagnostics" / run_id
    if output.exists():
        print(f"STOP: evidence directory already exists: {output}", file=sys.stderr)
        return 2

    backend = WindowsMiningToFullBackend(
        expected_hwnd=args.hwnd,
        output=output,
        session_id=run_id,
        title_substring=args.title,
        neutral_settle_s=args.neutral_settle,
        hover_settle_s=args.hover_settle,
        passive_interval_s=args.passive_interval,
    )
    config = MiningLoopConfig(
        session_id=run_id,
        expected_hwnd=args.hwnd,
        max_passive_observations=args.max_passive,
    )
    result = run_mining_until_full(backend, config)
    final_confidence = (
        None if result.final_state is None else result.final_state.inventory.confidence
    )
    payload = {
        "run_id": run_id,
        "git_sha": head,
        "runelite_hwnd": args.hwnd,
        "expected_client_geometry": [1005, 1078],
        "expected_dpi": 96,
        "start_inventory": result.start_inventory,
        "end_inventory": result.end_inventory,
        "inventory_confidence": final_confidence,
        "verified_ores": result.verified_ores,
        "click_count": result.click_count,
        "attempt_count": result.attempt_count,
        "target_sequence": list(result.target_sequence),
        "distinct_targets": sorted(set(result.target_sequence)),
        "dispatch_ids": list(result.dispatch_ids),
        "phase": result.phase.value,
        "stop_reason": result.stop_reason.value,
        "state_stop_reason": result.state_stop_reason.value,
        "success": result.success,
        "detail": result.detail,
        "events": list(result.events),
        "invariants": {
            "resource_threshold": 0.12,
            "resource_landmarks": 6,
            "resource_quorum": 5,
            "resource_zones_required": 3,
            "inventory_floor": 0.8,
            "inventory_capacity": 28,
            "exact_hover_action": "Mine Iron rocks",
            "maximum_clicks_per_attempt": 1,
            "blind_retry": False,
            "navigation_started_on_full": False,
        },
        "evidence_origin": "real_client_live_run",
        "real_client_success": result.success,
        "raw_frames_committed": False,
    }
    output.mkdir(parents=True, exist_ok=True)
    result_path = output / "result.json"
    result_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    print(f"RESULT_PATH={result_path}")
    return 0 if result.success else 2


if __name__ == "__main__":
    raise SystemExit(main())
