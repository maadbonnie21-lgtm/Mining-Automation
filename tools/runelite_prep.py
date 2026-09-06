#!/usr/bin/env python3
"""Diagnose or explicitly PREP RuneLite for the supported mining-only run.

Default mode is strictly read-only.  ``--apply`` authorizes only bounded
window/camera preparation and never authorizes a mining click, navigation,
banking, item movement, or perception release approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from mining_automation.capture import CaptureSource, Frame  # noqa: E402
from mining_automation.capture.windows import WindowsCaptureBackend  # noqa: E402
from mining_automation.capture.windows.win32_api import RealWin32Api, WindowInfo  # noqa: E402
from mining_automation.capture.windows.window_selector import matches  # noqa: E402
from mining_automation.controlled_mining_runner import (  # noqa: E402
    ProductionMiningPerceptionEvaluator,
)
from mining_automation.mining_slice import PerceptionEpoch, ResourceViewState  # noqa: E402
from mining_automation.perception.live_pose_references import (  # noqa: E402
    verify_local_pose_references,
)
from mining_automation.validation import _runelite_prep_win32  # noqa: E402
from mining_automation.validation.camera_plan import (  # noqa: E402
    REVIEWED_CAMERA_WHEEL_POINT,
)
from mining_automation.validation.client_readiness import (  # noqa: E402
    evaluate_client_input_readiness,
)
from mining_automation.validation.runelite_prep import (  # noqa: E402
    EXPECTED_CLIENT_DPI,
    EXPECTED_CLIENT_HEIGHT,
    EXPECTED_CLIENT_WIDTH,
    PREP_CONFIRMATION,
    PrepActionReceipt,
    PrepBackend,
    PrepCameraStep,
    PrepMode,
    PrepOperationError,
    PrepPoseReferenceReceipt,
    PrepSceneObservation,
    PrepStopReason,
    PrepWindowIdentity,
    PrepWindowSnapshot,
    RunelitePrepResult,
    run_runelite_prep,
)
from mining_automation.validation.session_recovery import (  # noqa: E402
    PLAY_NOW_CLIENT_POINT,
    PREAUTHENTICATED_STAGE,
    WELCOME_PLAY_CLIENT_POINT,
    WELCOME_PLAY_STAGE,
    session_recovery_stage,
)
from mining_automation.validation.windows_camera import (  # noqa: E402
    RealWindowsCameraApi,
    WindowsCameraControl,
    WindowsCameraError,
)

NEUTRAL_CLIENT_POINT = (100, 100)
TOOLTIP_SETTLE_SECONDS = 1.0
WINDOW_SETTLE_SECONDS = 0.20
PITCH_DOWN_SECONDS = 0.100
PITCH_UP_SECONDS = 0.050


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _exact_git_sha() -> str:
    return _git("rev-parse", "HEAD")


def _checkout_clean() -> bool:
    return _git("status", "--porcelain", "--untracked-files=no") == ""


def _matching_windows(windows: list[WindowInfo], title: str) -> list[WindowInfo]:
    return [
        item
        for item in windows
        if item.is_visible
        and matches(item, title)
        and (
            item.is_minimized
            or (item.client_width > 0 and item.client_height > 0)
        )
    ]


class RealPrepBackend(PrepBackend):
    """Windows adapter for the narrow PREP controller."""

    def __init__(
        self,
        *,
        title_substring: str,
        output: Path,
        prep_session_id: str,
    ) -> None:
        self.title_substring = title_substring
        self.output = output
        self.prep_session_id = prep_session_id
        self.win32 = RealWin32Api()
        self.camera_api = RealWindowsCameraApi()
        self.win32.declare_dpi_awareness()
        self.camera_api.declare_dpi_awareness()
        self.bound_window: WindowInfo | None = None
        self.bound_identity: PrepWindowIdentity | None = None
        self.capture_backend: WindowsCaptureBackend | None = None
        self.capture_source: CaptureSource | None = None
        self.camera_control: WindowsCameraControl | None = None
        self.inventory_evaluator = ProductionMiningPerceptionEvaluator()
        self.pose_detectors: dict[str, Any] | None = None
        self.active_registration: dict[str, Any] = {"pose": None, "detector": None}
        self.evaluate_resource: Any = None
        self.capture_sequence = 0
        self.epoch_sequence = 0

    def _discover_once(self) -> None:
        if self.bound_window is not None:
            return
        candidates = _matching_windows(
            self.win32.enumerate_windows(),
            self.title_substring,
        )
        if not candidates:
            raise PrepOperationError(
                PrepStopReason.WINDOW_NOT_FOUND,
                f"No visible RuneLite window matches {self.title_substring!r}.",
            )
        if len(candidates) != 1:
            rendered = ", ".join(
                f"hwnd={item.hwnd} title={item.title!r}" for item in candidates
            )
            raise PrepOperationError(
                PrepStopReason.WINDOW_AMBIGUOUS,
                "Multiple visible RuneLite windows match; PREP will not guess: "
                + rendered,
            )
        selected = candidates[0]
        raw_identity = self.camera_api.window_identity(selected.hwnd)
        self.bound_window = selected
        self.bound_identity = PrepWindowIdentity(
            process_id=raw_identity.process_id,
            thread_id=raw_identity.thread_id,
            class_name=raw_identity.class_name,
            title=raw_identity.title,
        )

    @property
    def hwnd(self) -> int:
        self._discover_once()
        assert self.bound_window is not None
        return self.bound_window.hwnd

    @property
    def identity(self) -> PrepWindowIdentity:
        self._discover_once()
        assert self.bound_identity is not None
        return self.bound_identity

    def _fresh_window_info(self) -> WindowInfo:
        self._discover_once()
        assert self.bound_window is not None
        for item in self.win32.enumerate_windows():
            if item.hwnd == self.bound_window.hwnd:
                return item
        raise PrepOperationError(
            PrepStopReason.WINDOW_IDENTITY_CHANGED,
            "The bound RuneLite HWND disappeared during PREP.",
        )

    def _fresh_identity(self) -> PrepWindowIdentity:
        raw = self.camera_api.window_identity(self.hwnd)
        current = PrepWindowIdentity(
            process_id=raw.process_id,
            thread_id=raw.thread_id,
            class_name=raw.class_name,
            title=raw.title,
        )
        if current != self.identity:
            raise PrepOperationError(
                PrepStopReason.WINDOW_IDENTITY_CHANGED,
                "RuneLite HWND was reused or its process/thread/class/title identity changed.",
            )
        return current

    def snapshot(self) -> PrepWindowSnapshot:
        info = self._fresh_window_info()
        identity = self._fresh_identity()
        width = info.client_width
        height = info.client_height
        if not info.is_minimized:
            # This is the physical client-area measurement used by the camera/input
            # boundary, not an assumed outer-window rectangle.
            width, height = self.camera_api.client_size(info.hwnd)
        dpi = self.win32.get_dpi_for_window(info.hwnd)
        return PrepWindowSnapshot(
            hwnd=info.hwnd,
            identity=identity,
            visible=info.is_visible,
            minimized=info.is_minimized,
            foreground=self.camera_api.foreground_window() == info.hwnd,
            client_width=width,
            client_height=height,
            dpi=dpi,
        )

    def verify_pose_references(self) -> tuple[PrepPoseReferenceReceipt, ...]:
        try:
            manifest = verify_local_pose_references(REPOSITORY_ROOT)
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise PrepOperationError(
                PrepStopReason.POSE_REFERENCES_INVALID,
                str(exc),
            ) from exc

        # The exact successful pose detector/registration implementation remains the
        # evidence adapter used by #84 today. PREP consumes it read-only; it does not
        # make that historical CLI a release authority.
        try:
            from run_three_rock_continuous_proof import (  # type: ignore[import-not-found]
                build_pose_detectors,
                evaluate_resource,
            )
        except (ImportError, ModuleNotFoundError) as exc:
            raise PrepOperationError(
                PrepStopReason.POSE_REFERENCES_INVALID,
                f"Could not load preserved pose adapter: {exc}",
            ) from exc
        try:
            self.pose_detectors = build_pose_detectors()
        except Exception as exc:  # noqa: BLE001 - local evidence verification boundary
            raise PrepOperationError(
                PrepStopReason.POSE_REFERENCES_INVALID,
                f"Could not build preserved pose detectors: {type(exc).__name__}: {exc}",
            ) from exc
        self.evaluate_resource = evaluate_resource
        return tuple(
            PrepPoseReferenceReceipt(
                pose_id=item.pose_id,
                relative_path=item.relative_path,
                sha256=item.sha256,
                byte_count=item.byte_count,
                width=item.width,
                height=item.height,
            )
            for item in manifest.receipts
        )

    def restore_window(self) -> PrepActionReceipt:
        ok = _runelite_prep_win32.restore_window(self.hwnd)
        time.sleep(WINDOW_SETTLE_SECONDS)
        self._fresh_identity()
        return PrepActionReceipt(
            action="restore_window",
            requested_events=1,
            completed_events=1 if ok else 0,
            detail="ShowWindow(SW_RESTORE) followed by fresh visibility/minimized measurement.",
        )

    def resize_client(self, width: int, height: int) -> PrepActionReceipt:
        result = _runelite_prep_win32.resize_client_area(
            self.hwnd,
            width,
            height,
        )
        self._fresh_identity()
        requested = max(1, result.attempts)
        completed = requested if result.success else max(0, requested - 1)
        return PrepActionReceipt(
            action="resize_client_area",
            requested_events=requested,
            completed_events=completed,
            detail=(
                f"bounded SetWindowPos attempts={result.attempts}; measured final "
                f"client={result.final_width}x{result.final_height}"
            ),
        )

    def focus_window(self) -> PrepActionReceipt:
        self._fresh_identity()
        attempted = self.camera_api.focus_window(self.hwnd)
        time.sleep(WINDOW_SETTLE_SECONDS)
        self._fresh_identity()
        return PrepActionReceipt(
            action="foreground_window",
            requested_events=1,
            completed_events=1 if attempted else 0,
            detail="Explicit PREP-only SetForegroundWindow request.",
        )

    def _control(self) -> WindowsCameraControl:
        if self.camera_control is None:
            self.camera_control = WindowsCameraControl(
                self.hwnd,
                api=self.camera_api,
                expected_class_name=self.identity.class_name,
                expected_title=self.identity.title,
            )
        return self.camera_control

    def neutralize_cursor(self) -> PrepActionReceipt:
        control = self._control()
        control.preflight()  # includes exact identity/geometry/foreground + held-input veto
        mapping = self.camera_api.pointer_mapping(self.hwnd, *NEUTRAL_CLIENT_POINT)
        if not mapping.exact_round_trip:
            raise PrepOperationError(
                PrepStopReason.NEUTRAL_CURSOR_FAILED,
                "Neutral cursor coordinate round trip was not exact.",
            )
        screen = mapping.physical_screen.pair
        if self.camera_api.root_window_at_point(*screen) != self.hwnd:
            raise PrepOperationError(
                PrepStopReason.NEUTRAL_CURSOR_FAILED,
                "Neutral cursor point is not owned by the exact RuneLite root window.",
            )
        if self.camera_api.cursor_position() == screen:
            time.sleep(TOOLTIP_SETTLE_SECONDS)
            control.preflight()
            return PrepActionReceipt(
                action="neutral_cursor",
                requested_events=0,
                completed_events=0,
                detail="Cursor already at the reviewed neutral client point; settle only.",
            )
        if not self.camera_api.move_cursor(*screen):
            raise PrepOperationError(
                PrepStopReason.NEUTRAL_CURSOR_FAILED,
                "Windows refused the PREP neutral-cursor move.",
            )
        if self.camera_api.cursor_position() != screen:
            raise PrepOperationError(
                PrepStopReason.NEUTRAL_CURSOR_FAILED,
                "Cursor did not remain at the reviewed neutral client point.",
            )
        time.sleep(TOOLTIP_SETTLE_SECONDS)
        control.preflight()
        if self.camera_api.root_window_at_point(*screen) != self.hwnd:
            raise PrepOperationError(
                PrepStopReason.NEUTRAL_CURSOR_FAILED,
                "Neutral point lost RuneLite ownership during tooltip settle.",
            )
        return PrepActionReceipt(
            action="neutral_cursor",
            requested_events=1,
            completed_events=1,
            detail="Moved to reviewed neutral client point and waited for tooltip clearance.",
        )

    def recover_session(self, stage: str) -> PrepActionReceipt:
        """Perform one exact click for the exact freshly re-proven recovery stage."""

        frame, _ = self._capture(f"session-recovery-commit-{stage}")
        fresh_stage = session_recovery_stage(frame)
        if fresh_stage != stage:
            raise PrepOperationError(
                PrepStopReason.SESSION_RECOVERY_FAILED,
                "Fresh recovery commit frame no longer matches the requested "
                f"reviewed stage: requested {stage!r}, observed {fresh_stage!r}.",
            )
        try:
            if stage == PREAUTHENTICATED_STAGE:
                receipt = self._control().click_play_now(*PLAY_NOW_CLIENT_POINT)
            elif stage == WELCOME_PLAY_STAGE:
                receipt = self._control().click_welcome_play(*WELCOME_PLAY_CLIENT_POINT)
            else:
                raise PrepOperationError(
                    PrepStopReason.SESSION_RECOVERY_FAILED,
                    f"No reviewed recovery input exists for stage {stage!r}.",
                )
        except WindowsCameraError as exc:
            raise PrepOperationError(
                PrepStopReason.SESSION_RECOVERY_FAILED,
                str(exc),
            ) from exc
        return self._convert_camera_receipt(receipt, detail=stage)

    def _ensure_capture(self) -> CaptureSource:
        if self.capture_source is None:
            self.capture_backend = WindowsCaptureBackend(
                title_substring=self.title_substring
            )
            self.capture_source = CaptureSource(
                self.capture_backend,
                max_consecutive_failures=2,
            )
            self.capture_source.open()
        return self.capture_source

    def _capture(self, label: str) -> tuple[Frame, str]:
        source = self._ensure_capture()
        frame = source.capture()
        assert self.capture_backend is not None
        selected = self.capture_backend.selected_window
        if selected is None or selected.hwnd != self.hwnd:
            raise PrepOperationError(
                PrepStopReason.WINDOW_IDENTITY_CHANGED,
                "Capture backend selected a different RuneLite HWND during PREP.",
            )
        self._fresh_identity()
        self.capture_sequence += 1
        path = self.output / f"{self.capture_sequence:03d}-{label}.bgra"
        path.write_bytes(frame.payload)
        return frame, str(path)

    def _epoch(self, frame: Frame, label: str) -> PerceptionEpoch:
        self.epoch_sequence += 1
        return PerceptionEpoch(
            capture_source_id="windows-runelite-prep",
            capture_session_id=self.prep_session_id,
            cycle_id=f"{self.prep_session_id}:{self.epoch_sequence}:{label}",
            cycle_sequence=self.epoch_sequence,
            frame_id=frame.frame_id,
            captured_monotonic_s=frame.captured_monotonic_s,
            frame_width=frame.width,
            frame_height=frame.height,
            frame_payload_sha256=hashlib.sha256(frame.payload).hexdigest(),
            pixel_format="bgra8888",
        )

    @staticmethod
    def _diagnosis_summary(
        pose: str | None,
        diagnoses: dict[str, Any],
        *,
        registration_identity: str | None,
    ) -> tuple[int, tuple[str, ...], tuple[tuple[str, float], ...], float | None]:
        records: list[tuple[str, dict[str, Any]]] = []
        for key, value in diagnoses.items():
            if isinstance(value, dict) and isinstance(value.get("matched"), int):
                records.append((str(key), value))
        chosen: dict[str, Any] | None = None
        if pose is not None and isinstance(diagnoses.get(pose), dict):
            candidate = diagnoses[pose]
            if isinstance(candidate.get("matched"), int):
                chosen = candidate
        if chosen is None and records:
            _, chosen = max(
                records,
                key=lambda item: (
                    int(item[1].get("matched", 0)),
                    len(item[1].get("zones", []))
                    if isinstance(item[1].get("zones"), list)
                    else 0,
                ),
            )
        if chosen is None:
            return 0, (), (), None
        matched = int(chosen.get("matched", 0))
        zones_raw = chosen.get("zones", [])
        zones = (
            tuple(str(item) for item in zones_raw)
            if isinstance(zones_raw, list)
            else ()
        )
        distances_raw = chosen.get("distances", {})
        distances: tuple[tuple[str, float], ...] = ()
        if isinstance(distances_raw, dict):
            pairs: list[tuple[str, float]] = []
            for key, value in distances_raw.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    pairs.append((str(key), float(value)))
            distances = tuple(sorted(pairs))
        score = None
        if distances:
            score = sum(value for _, value in distances) / len(distances)
        if registration_identity is not None and score is None:
            score = 0.0
        return matched, zones, distances, score

    def observe(self) -> PrepSceneObservation:
        if self.pose_detectors is None or self.evaluate_resource is None:
            raise PrepOperationError(
                PrepStopReason.POSE_REFERENCES_INVALID,
                "Pose references were not verified before scene observation.",
            )
        frame, frame_path = self._capture("clean")
        epoch = self._epoch(frame, "clean")
        resource, pose, diagnoses = self.evaluate_resource(
            frame,
            epoch,
            self.pose_detectors,
            frozenset(),
            self.active_registration,
        )
        registration_identity: str | None = None
        if "software_registration" in diagnoses:
            registration = diagnoses.get("software_registration")
            registration_identity = (
                f"{pose or 'candidate'}:distributed_affine_registration"
            )
            frame, frame_path = self._capture("clean-registered")
            epoch = self._epoch(frame, "clean-registered")
            resource, pose, diagnoses = self.evaluate_resource(
                frame,
                epoch,
                self.pose_detectors,
                frozenset(),
                self.active_registration,
            )
            if isinstance(registration, dict):
                # Retain the identity only; the final fresh frame must satisfy the
                # actual Resource gate independently. Never reuse landmark evidence
                # from the prior registration-capture frame.
                registration_identity = (
                    f"{pose or 'candidate'}:"
                    f"{registration.get('kind', 'distributed_affine_registration')}"
                )

        gameplay = evaluate_client_input_readiness(frame)
        _, inventory = self.inventory_evaluator.evaluate(frame, epoch)
        matched, zones, distances, score = self._diagnosis_summary(
            pose,
            diagnoses,
            registration_identity=registration_identity,
        )
        recovery_stage = session_recovery_stage(frame)
        return PrepSceneObservation(
            frame_id=frame.frame_id,
            frame_sha256=hashlib.sha256(frame.payload).hexdigest(),
            gameplay_ready=gameplay.safe_to_attempt_camera_input,
            gameplay_reason=gameplay.detail,
            inventory_occupied=inventory.inventory.occupied_slots,
            inventory_confidence=inventory.inventory.confidence,
            inventory_unknown_reason=inventory.unknown_reason,
            resource_supported=resource.view is ResourceViewState.SUPPORTED,
            resource_view=resource.view.value,
            accepted_pose_id=pose,
            software_registration_identity=registration_identity,
            matched_landmarks=matched,
            matched_zones=zones,
            landmark_distances=distances,
            diagnostic_score=score,
            frame_path=frame_path,
            session_recovery_ready=recovery_stage is not None,
            session_recovery_stage=recovery_stage,
        )

    @staticmethod
    def _convert_camera_receipt(receipt: Any, *, detail: str = "") -> PrepActionReceipt:
        operation = getattr(receipt, "operation", "camera_input")
        action = getattr(operation, "value", str(operation))
        return PrepActionReceipt(
            action=str(action),
            requested_events=int(receipt.requested_events),
            completed_events=int(receipt.completed_events),
            detail=detail,
        )

    def camera_action(self, step: PrepCameraStep) -> tuple[PrepActionReceipt, ...]:
        control = self._control()
        try:
            if step is PrepCameraStep.PITCH_DOWN_100MS:
                down = control.key_down("down")
                receipts = [self._convert_camera_receipt(down, detail=step.value)]
                if down.complete:
                    try:
                        time.sleep(PITCH_DOWN_SECONDS)
                    finally:
                        up = control.key_up("down")
                        receipts.append(self._convert_camera_receipt(up, detail=step.value))
                return tuple(receipts)
            if step is PrepCameraStep.PITCH_UP_50MS:
                down = control.key_down("up")
                receipts = [self._convert_camera_receipt(down, detail=step.value)]
                if down.complete:
                    try:
                        time.sleep(PITCH_UP_SECONDS)
                    finally:
                        up = control.key_up("up")
                        receipts.append(self._convert_camera_receipt(up, detail=step.value))
                return tuple(receipts)
            if step in {
                PrepCameraStep.WHEEL_POSITIVE_1,
                PrepCameraStep.WHEEL_NEGATIVE_1,
            }:
                detents = 1 if step is PrepCameraStep.WHEEL_POSITIVE_1 else -1
                receipt = control.scroll_camera(
                    *REVIEWED_CAMERA_WHEEL_POINT,
                    detents,
                )
                return (self._convert_camera_receipt(receipt, detail=step.value),)
        except WindowsCameraError as exc:
            raise PrepOperationError(
                PrepStopReason.CAMERA_INPUT_REJECTED,
                str(exc),
            ) from exc
        raise PrepOperationError(
            PrepStopReason.CAMERA_INPUT_REJECTED,
            f"Unsupported PREP camera step {step!r}.",
        )

    def cleanup(self) -> tuple[PrepActionReceipt, ...]:
        receipts: list[PrepActionReceipt] = []
        failure: BaseException | None = None
        if self.camera_control is not None:
            try:
                released = self.camera_control.release_all_held_keys()
            except BaseException as exc:  # keep closing capture before surfacing
                failure = exc
            else:
                receipts.extend(
                    self._convert_camera_receipt(item, detail="PREP lifecycle cleanup")
                    for item in released
                )
        if self.capture_source is not None:
            try:
                self.capture_source.close()
            except BaseException as exc:
                if failure is None:
                    failure = exc
        if failure is not None:
            raise RuntimeError(str(failure)) from failure
        return tuple(receipts)


class _ConstructionFailureBackend(PrepBackend):
    """Allow even platform/construction failures to produce one JSON receipt."""

    def __init__(self, detail: str) -> None:
        self.detail = detail

    def _raise(self) -> None:
        raise PrepOperationError(PrepStopReason.BACKEND_ERROR, self.detail)

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

    def recover_session(self, stage: str) -> PrepActionReceipt:
        del stage
        self._raise()
        raise AssertionError

    def camera_action(self, step: PrepCameraStep) -> tuple[PrepActionReceipt, ...]:
        del step
        self._raise()
        return ()

    def cleanup(self) -> tuple[PrepActionReceipt, ...]:
        return ()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="explicitly permit bounded PREP-only window/camera correction",
    )
    parser.add_argument(
        "--confirm",
        help=f"apply mode requires exact token {PREP_CONFIRMATION!r}",
    )
    parser.add_argument("--title", default="RuneLite")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="new local evidence directory; default is diagnostics/runelite-prep-<id>",
    )
    return parser.parse_args(argv)


def _result_payload(result: RunelitePrepResult) -> dict[str, object]:
    payload = asdict(result)
    payload["generated_at_utc"] = datetime.now(UTC).isoformat()
    return payload


def _write_result(output: Path, result: RunelitePrepResult) -> Path:
    path = output / "result.json"
    path.write_text(
        json.dumps(_result_payload(result), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path


def _print_owner_summary(result: RunelitePrepResult, receipt: Path) -> None:
    initial = result.initial_window
    final = result.final_window
    observation = result.observations[-1] if result.observations else None
    print("RUNE LITE PREP DIAGNOSTIC\n")
    if initial is None:
        print("✗ RuneLite not safely bound")
    else:
        print(f"✓ RuneLite found — HWND {initial.hwnd}")
        print(
            "✓ HWND identity bound — "
            f"PID {initial.identity.process_id}, TID {initial.identity.thread_id}, "
            f"class {initial.identity.class_name!r}"
        )
        window = final or initial
        geometry_mark = "✓" if (
            window.client_width == EXPECTED_CLIENT_WIDTH
            and window.client_height == EXPECTED_CLIENT_HEIGHT
        ) else "✗"
        print(
            f"{geometry_mark} Client {window.client_width} x {window.client_height} "
            f"— expected {EXPECTED_CLIENT_WIDTH} x {EXPECTED_CLIENT_HEIGHT}"
        )
        dpi_mark = "✓" if window.dpi == EXPECTED_CLIENT_DPI else "✗"
        print(f"{dpi_mark} DPI {window.dpi} — expected {EXPECTED_CLIENT_DPI}")
        print(
            f"{'✓' if window.foreground else '✗'} Foreground "
            f"{'yes' if window.foreground else 'no'}"
        )
    if result.pose_references:
        print(f"✓ Local pose references {len(result.pose_references)}/3 verified")
    else:
        print("✗ Local pose references not verified")
    if observation is not None:
        print(
            f"{'✓' if observation.gameplay_ready else '✗'} Gameplay chrome "
            f"{'ready' if observation.gameplay_ready else 'not ready'}"
        )
        inventory_ready = (
            observation.inventory_occupied is not None
            and observation.inventory_confidence >= 0.8
        )
        rendered_inventory = (
            str(observation.inventory_occupied)
            if observation.inventory_occupied is not None
            else "UNKNOWN"
        )
        print(
            f"{'✓' if inventory_ready else '✗'} Inventory {rendered_inventory}/28 "
            f"confidence {observation.inventory_confidence:.3f}"
        )
        print(
            f"{'✓' if observation.frozen_resource_gate_passed else '✗'} Resource "
            f"{observation.matched_landmarks}/6 landmarks across "
            f"{len(observation.matched_zones)}/3 zones"
        )
    else:
        print("? Scene/Inventory not evaluated")
    print()
    if result.ready_for_mining:
        print("READY FOR MINING")
    else:
        print(f"NOT READY: {result.stop_reason.value} — {result.detail}")
    if final is not None:
        print(f"HWND: {final.hwnd}")
    print(f"READY receipt: {receipt}")
    print("PREP authority: RELINQUISHED")
    print("Mining input authority: FALSE")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    mode = PrepMode.APPLY if args.apply else PrepMode.READ_ONLY
    prep_session_id = f"prep-{uuid.uuid4().hex[:12]}"
    output = args.output or (
        REPOSITORY_ROOT / "diagnostics" / f"runelite-prep-{prep_session_id}"
    )
    if output.exists():
        print(f"STOP: output path already exists: {output}", file=sys.stderr)
        return 2

    dirty_checkout = False
    try:
        git_sha = _exact_git_sha()
        checkout_clean = _checkout_clean()
    except (OSError, subprocess.CalledProcessError) as exc:
        git_sha = "0" * 40
        backend: PrepBackend = _ConstructionFailureBackend(
            f"Could not read exact Git checkout state: {exc}"
        )
    else:
        if not checkout_clean:
            dirty_checkout = True
            backend = _ConstructionFailureBackend(
                "PREP requires a clean Git checkout before diagnosis or apply; "
                "commit/stash unrelated changes first."
            )
        else:
            try:
                backend = RealPrepBackend(
                    title_substring=args.title,
                    output=output,
                    prep_session_id=prep_session_id,
                )
            except Exception as exc:  # noqa: BLE001 - still emit machine receipt
                backend = _ConstructionFailureBackend(
                    f"Could not construct real Windows PREP backend: "
                    f"{type(exc).__name__}: {exc}"
                )

    # The default diagnostics path is repository-ignored. Create it only after the
    # exact checkout has been measured so PREP cannot make its own preflight dirty.
    output.mkdir(parents=True)

    result = run_runelite_prep(
        backend,
        mode=mode,
        git_sha=git_sha,
        prep_session_id=prep_session_id,
        confirm=args.confirm,
    )
    if dirty_checkout and isinstance(backend, _ConstructionFailureBackend):
        result = replace(
            result,
            ready_for_mining=False,
            stop_reason=PrepStopReason.DIRTY_CHECKOUT,
            detail=backend.detail,
        )
    # A custom evidence path or an external process must not leave a READY receipt
    # that the separately authorized miner would immediately reject as dirty.
    if result.ready_for_mining and not _checkout_clean():
        result = replace(
            result,
            ready_for_mining=False,
            stop_reason=PrepStopReason.DIRTY_CHECKOUT,
            detail=(
                "Checkout became dirty during PREP; READY is withheld until the "
                "exact mining checkout is clean."
            ),
        )
    receipt = _write_result(output, result)
    _print_owner_summary(result, receipt)
    return 0 if result.ready_for_mining else 2


if __name__ == "__main__":
    raise SystemExit(main())
