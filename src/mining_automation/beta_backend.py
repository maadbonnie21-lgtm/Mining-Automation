"""Session-to-existing-phase adapter; does not implement mining, routes, or banking."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .beta_process import ChildJob
from .beta_session import SessionControls, SessionSettings, SessionUnproven, atomic_json
from .full_cycle import _require_banking, _require_mining, _require_return, _require_route_to_bank

PHASES = (
    (
        "mine",
        "run_mining_to_full_safe.py",
        "MINE_TO_FULL_28_FAIL_CLOSED",
        "--authorize-execution-sha",
    ),
    ("outbound", "run_mine_to_bank.py", "RUN_MINE_TO_BANK_NO_RESIZE", "--authorize-execution-sha"),
    ("bank", "run_bank_iron.py", "DEPOSIT_IRON_AND_CLOSE_BANK", "--sha"),
    ("return", "run_bank_to_mine.py", "RUN_BANK_TO_MINE_NO_RESIZE", "--authorize-execution-sha"),
)


class PhaseBackend:
    def __init__(
        self,
        root: Path,
        output: Path,
        hwnd: int,
        title: str,
        sha: str,
        settings: SessionSettings,
        controls: SessionControls,
    ) -> None:
        self.root, self.output = root.resolve(), output.resolve()
        self.hwnd, self.title, self.sha = hwnd, title, sha
        self.settings, self.controls = settings, controls
        self.child: subprocess.Popen[Any] | None = None
        self.job: ChildJob | None = None
        self.sequence = 0
        self.ore_deposited = 0
        self.gems_deposited = 0
        self.status_sink: Callable[[], None] | None = None
        self.control_hwnd = 0
        self.initial_window: dict[str, Any] | None = None
        self.cancel_file = output / "EMERGENCY_STOP"
        self.auth_cancel_file = output / "CANCEL_LOGIN"

    def bind_status_sink(self, sink: Callable[[], None]) -> None:
        self.status_sink = sink

    def refresh_latches(self) -> None:
        if self.cancel_file.exists():
            self.controls.request_emergency()
        if self.auth_cancel_file.exists():
            self.controls.request_stop()

    def _clean_build(self) -> None:
        def git(*args: str) -> str:
            return subprocess.run(
                ["git", "-C", str(self.root), *args], check=True, capture_output=True, text=True
            ).stdout.strip()

        if git("rev-parse", "HEAD") != self.sha or git(
            "status", "--porcelain", "--untracked-files=no"
        ):
            raise SessionUnproven("source_changed_since_Start; no child authorized")

    def request_emergency(self) -> None:
        self.controls.request_emergency()
        if self.output.is_dir():
            self.cancel_file.touch()

    def request_stop(self) -> None:
        self.controls.request_stop()
        if self.output.is_dir():
            self.auth_cancel_file.touch()

    def cancel(self) -> None:
        self.cancel_file.touch()
        self.auth_cancel_file.touch()
        try:
            if self.child is not None and self.child.poll() is None:
                try:
                    self.child.wait(timeout=0.25)
                except subprocess.TimeoutExpired as exc:
                    if self.job is None:
                        raise SessionUnproven("child_ownership_missing") from exc
                    self.job.terminate()
        finally:
            if self.job is not None:
                self.job.close()  # Kill-on-close remains the backstop if termination raised.
                self.job = None
            if self.child is not None:
                self.child.wait(timeout=5)
            self.child = None

    def _run(
        self,
        kind: str,
        output: Path,
        heartbeat: Callable[[str], None] | None = None,
        *,
        fresh_rocks: bool = False,
    ) -> dict[str, Any]:
        self.refresh_latches()
        self.controls.check(authentication=kind in ("login", "logout"))
        self._clean_build()
        if shutil.disk_usage(self.root).free < 4 * 1024**3:
            raise SessionUnproven("low_disk_reserve; no_new_phase_or_input; evidence_preserved")
        self.sequence += 1
        output.parent.mkdir(parents=True, exist_ok=True)
        gate = self.output / f"child-{self.sequence:04d}.ready"
        request = self.output / f"child-{self.sequence:04d}.json"
        atomic_json(
            request,
            dict(
                kind=kind,
                output=str(output),
                hwnd=self.hwnd,
                title=self.title,
                sha=self.sha,
                initial_window=self.initial_window,
                gate=str(gate),
                cancel=str(self.cancel_file),
                auth_cancel=str(self.auth_cancel_file),
                fresh_rocks=fresh_rocks,
                smooth_cursor=self.settings.smooth_cursor,
                varied_rock_points=self.settings.varied_rock_points,
                control_hwnd=self.control_hwnd,
            ),
        )
        log = self.output / f"child-{self.sequence:04d}-{kind}.log"
        self.job = ChildJob()
        command = [
            sys.executable,
            "-I",
            "-X",
            "utf8",
            "-u",
            str(self.root / "tools/run_beta_phase.py"),
            str(request),
        ]
        flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            with log.open("w", encoding="utf-8") as stream:
                self.child = subprocess.Popen(
                    command,
                    cwd=self.root,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    creationflags=flags,
                )
                self.job.assign(self.child)
                self.controls.check(authentication=kind in ("login", "logout"))
                atomic_json(
                    self.output / "child.json",
                    dict(pid=self.child.pid, phase=kind, request=str(request), git_sha=self.sha),
                )
                gate.touch(exist_ok=False)
                deadline = time.monotonic() + (2400 if kind == "mine" else 360)
                while self.child.poll() is None:
                    self.refresh_latches()
                    self.controls.check(authentication=kind in ("login", "logout"))
                    if heartbeat:
                        heartbeat(kind)
                    elif self.status_sink is not None:
                        self.status_sink()
                    if time.monotonic() > deadline:
                        raise SessionUnproven(f"bounded_phase_timeout:{kind}")
                    time.sleep(0.05)
                self.refresh_latches()
                self.controls.check(authentication=kind in ("login", "logout"))
                result_path = output / "result.json"
                result: dict[str, Any] = json.loads(result_path.read_text(encoding="utf-8"))
                if not isinstance(result, dict):
                    raise SessionUnproven("phase_receipt_must_be_an_object")
                if self.child.returncode or result.get("success") is not True:
                    raise SessionUnproven(
                        f"{kind}:{result.get('stop_reason') or result.get('reason')}"
                    )
                if kind in ("mine", "outbound", "bank", "return"):
                    boundary = json.loads((output / "beta-phase.json").read_text(encoding="utf-8"))
                    if (
                        boundary.get("success") is not True
                        or boundary.get("git_sha") != self.sha
                        or boundary.get("phase_result_sha256")
                        != hashlib.sha256(result_path.read_bytes()).hexdigest()
                        or boundary.get("start_window") != self.initial_window
                        or boundary.get("end_window") != self.initial_window
                    ):
                        raise SessionUnproven("beta_phase_boundary_unproven")
                if result.get("git_sha") != self.sha:
                    raise SessionUnproven("phase_build_mismatch")
                for key in ("start_window", "end_window"):
                    window = result.get(key)
                    if (
                        window is not None
                        and self.initial_window is not None
                        and window != self.initial_window
                    ):
                        raise SessionUnproven("phase_window_changed")
                result["receipt_path"] = str(result_path)
                result["receipt_sha256"] = hashlib.sha256(result_path.read_bytes()).hexdigest()
                return result
        finally:
            if self.child is not None and self.child.poll() is None:
                self.cancel()
            if self.job is not None:
                self.job.close()
                self.job = None
            self.child = None
            atomic_json(self.output / "child.json", dict(pid=None, phase=kind, git_sha=self.sha))

    def cycle(self, number: int, heartbeat: Callable[[str], None]) -> dict[str, Any]:
        receipts = []
        mining_payload: dict[str, Any] = {}
        bank_payload: dict[str, Any] = {}
        for index, (kind, _tool, _confirm, _sha_arg) in enumerate(PHASES, 1):
            heartbeat(kind)
            result = self._run(
                kind, self.output / f"cycle-{number:06d}" / f"{index:02d}-{kind}", heartbeat
            )
            if kind == "mine":
                _require_mining(result, label=f"cycle_{number}_mining")
                mining_payload = result
            elif kind == "outbound":
                _require_route_to_bank(result)
            elif kind == "bank":
                _require_banking(result, mining_payload=mining_payload)
                bank_payload = result
                self.ore_deposited += result["deposited_ore_count"]
                self.gems_deposited += result["deposited_gem_count"]
            else:
                _require_return(result)
            receipts.append(
                dict(phase=kind, path=result["receipt_path"], sha256=result["receipt_sha256"])
            )
        return dict(
            success=True,
            cycle=number,
            deposited_ore=bank_payload["deposited_ore_count"],
            deposited_gems=bank_payload["deposited_gem_count"],
            deposited_gem_item_ids=bank_payload["deposited_gem_item_ids"],
            phase_receipts=receipts,
        )

    def verify_home(self, *, fresh_rocks: bool) -> dict[str, Any]:
        result = self._run(
            "home", self.output / f"home-{self.sequence + 1:04d}", fresh_rocks=fresh_rocks
        )
        if self.initial_window is None:
            self.initial_window = result["start_window"]
        return result

    def logout(self) -> dict[str, Any]:
        return self._run("logout", self.output / f"logout-{self.sequence + 1:04d}")

    def login(self) -> dict[str, Any]:
        return self._run("login", self.output / f"login-{self.sequence + 1:04d}")
