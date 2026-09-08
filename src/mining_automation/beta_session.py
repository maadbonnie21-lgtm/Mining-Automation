"""Beta session policy. No pixels, input, credentials, or phase reimplementation here."""

from __future__ import annotations

import json
import math
import os
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol


class SessionStopped(RuntimeError):
    pass


class SessionUnproven(RuntimeError):
    pass


@dataclass(frozen=True)
class RunBreakRow:
    active_s: float = 3600
    break_s: float = 600

    def __post_init__(self) -> None:
        for value in (self.active_s, self.break_s):
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError("Run and break durations must be finite and positive")


@dataclass(frozen=True)
class SessionSettings:
    mode: str = "continuous"
    cycles: int = 3
    routine: tuple[RunBreakRow, ...] = (RunBreakRow(),)
    repeat: bool = True
    smooth_cursor: bool = False
    varied_rock_points: bool = False

    def __post_init__(self) -> None:
        if self.mode not in ("continuous", "finite", "routine"):
            raise ValueError("Unknown session mode")
        if type(self.cycles) is not int or self.cycles < 1:
            raise ValueError("Cycle limit must be a positive integer")
        if not self.routine or not all(isinstance(row, RunBreakRow) for row in self.routine):
            raise ValueError("At least one valid run/break row is required")
        if any(
            type(value) is not bool
            for value in (self.repeat, self.smooth_cursor, self.varied_rock_points)
        ):
            raise ValueError("Settings switches must be booleans")

    @classmethod
    def load(cls, path: Path) -> SessionSettings:
        if not path.exists():
            return cls()
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.pop("version", None) != 1:
            raise ValueError("Unsupported settings format")
        value["routine"] = tuple(RunBreakRow(**row) for row in value.get("routine", []))
        return cls(**value)

    def save(self, path: Path) -> None:
        atomic_json(path, {"version": 1, **asdict(self)})


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


class SessionControls:
    """Stop drains work; Emergency forbids all further input. Neither latch is reset."""

    def __init__(self) -> None:
        self.stop = threading.Event()
        self.emergency = threading.Event()

    def request_stop(self) -> None:
        self.stop.set()

    def request_emergency(self) -> None:
        self.emergency.set()
        self.stop.set()

    def check(self, *, authentication: bool = False) -> None:
        if self.emergency.is_set():
            raise SessionStopped("emergency_stop; return_not_completed")
        if authentication and self.stop.is_set():
            raise SessionStopped("owner_stop_cancelled_login_or_logout")


class SessionBackend(Protocol):
    def cycle(self, number: int, heartbeat: Callable[[str], None]) -> dict[str, Any]: ...
    def verify_home(self, *, fresh_rocks: bool) -> dict[str, Any]: ...
    def logout(self) -> dict[str, Any]: ...
    def login(self) -> dict[str, Any]: ...
    def cancel(self) -> None: ...


def require_home(proof: dict[str, Any]) -> None:
    if not (
        proof.get("mine_arrival_verified") is True
        and proof.get("inventory_empty_verified") is True
        and proof.get("bank_closed_verified") is True
        and proof.get("window_unchanged") is True
        and proof.get("stationary_verified") is True
        and proof.get("fresh") is True
    ):
        raise SessionUnproven("return_not_completed: fresh exact mine endpoint unproven")


class BetaSession:
    """A single immutable command, retaining its input lease through logged-out breaks."""

    def __init__(
        self,
        settings: SessionSettings,
        controls: SessionControls,
        backend: SessionBackend,
        output: Path,
        *,
        sha: str,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings, self.controls, self.backend = settings, controls, backend
        self.output, self.sha, self.clock, self.sleep = output, sha, clock, sleep
        self.state = "IDLE"
        self.phase = "not_started"
        self.reason = "Press Start"
        self.completed = 0
        self.deposited = 0
        self.row = 0
        self.started = clock()
        self.active_deadline: float | None = None
        self.break_started: float | None = None
        self.break_deadline: float | None = None
        self.home: dict[str, Any] | None = None
        self.logout_proof: dict[str, Any] | None = None
        self.receipts: list[dict[str, Any]] = []
        self.interrupted = False

    def snapshot(self) -> dict[str, Any]:
        now = self.clock()
        return {
            "state": self.state,
            "phase": self.phase,
            "reason": self.reason,
            "git_sha": self.sha,
            "settings": asdict(self.settings),
            "cycles_completed": self.completed,
            "ore_deposited": self.deposited,
            "elapsed_s": max(0, now - self.started),
            "routine_row": self.row,
            "active_remaining_s": (
                None if self.active_deadline is None else max(0, self.active_deadline - now)
            ),
            "wind_down_overrun_s": (
                0 if self.active_deadline is None else max(0, now - self.active_deadline)
            ),
            "break_remaining_s": (
                None if self.break_deadline is None else max(0, self.break_deadline - now)
            ),
            "break_started_monotonic": self.break_started,
            "normal_stop_requested": self.controls.stop.is_set(),
            "emergency_stop_requested": self.controls.emergency.is_set(),
            "login_permitted": not self.controls.stop.is_set(),
            "home_proof": self.home,
            "logout_proof": self.logout_proof,
            "phase_receipts": self.receipts,
            "interrupted": self.interrupted,
            "live_acceptance": "NOT_ASSESSED_BY_SESSION",
            "updated_at_unix": time.time(),
        }

    def publish(self, state: str | None = None, reason: str | None = None) -> None:
        if state is not None:
            self.state = state
        if reason is not None:
            self.reason = reason
        atomic_json(self.output / "status.json", self.snapshot())

    def heartbeat(self, phase: str) -> None:
        self.controls.check()
        self.phase = phase
        expired = self.active_deadline is not None and self.clock() >= self.active_deadline
        if self.controls.stop.is_set() or expired:
            self.publish("DRAINING", "Finishing cycle / returning to mine; break has not started")
        else:
            self.publish("ACTIVE", phase)

    def _home(self, *, rocks: bool = False) -> None:
        self.controls.check()
        proof = self.backend.verify_home(fresh_rocks=rocks)
        require_home(proof)
        if rocks and proof.get("fresh_rocks_verified") is not True:
            raise SessionUnproven("fresh_rocks_unproven")
        self.home = proof

    def _active(self) -> None:
        self.active_deadline = (
            self.clock() + self.settings.routine[self.row].active_s
            if self.settings.mode == "routine"
            else None
        )
        self.break_started = self.break_deadline = None
        self.logout_proof = None
        self.publish("ACTIVE", "Mining with fresh observations")

    def _break(self) -> bool:
        self.controls.check(authentication=True)
        self.publish("LOGGING_OUT", "At verified mine start; verifying ordinary logout")
        proof = self.backend.logout()
        self.controls.check()
        if proof.get("logout_verified") is not True or proof.get("deliberate") is not True:
            raise SessionUnproven("deliberate_logout_unproven; break_not_started")
        self.logout_proof = proof
        # The requested inactive duration begins at acknowledgement, never at active expiry.
        self.break_started = self.clock()
        self.break_deadline = self.break_started + self.settings.routine[self.row].break_s
        self.active_deadline = None
        self.publish("BREAK", "Logged out; no game input during break")
        while self.clock() < self.break_deadline:
            self.controls.check()
            if self.controls.stop.is_set():
                return False
            self.publish()
            self.sleep(min(0.1, self.break_deadline - self.clock()))
        if self.controls.stop.is_set():
            return False
        last_row = self.row == len(self.settings.routine) - 1
        if last_row and not self.settings.repeat:
            return False  # Routine finishes logged out at the verified mine start.
        self.publish(
            "RECONNECTING", "Ordinary existing-account login; fresh reacquisition required"
        )
        self.controls.check(authentication=True)
        login = self.backend.login()
        self.controls.check(authentication=True)
        if login.get("login_verified") is not True:
            raise SessionUnproven("login_unproven; owner_attention_required")
        self._home(rocks=True)
        self.row = (self.row + 1) % len(self.settings.routine)
        self.interrupted = True  # Deliberate break cannot count as an uninterrupted streak.
        self._active()
        return True

    def run(self) -> dict[str, Any]:
        self.output.mkdir(parents=True, exist_ok=False)
        self.started = self.clock()
        try:
            self.publish(
                "ACQUIRING", "Verifying account/window, exact mine start and empty inventory"
            )
            self._home(rocks=True)
            self._active()
            while True:
                self.controls.check()
                if self.controls.stop.is_set():
                    break
                if self.settings.mode == "finite" and self.completed >= self.settings.cycles:
                    break
                if self.active_deadline is not None and self.clock() >= self.active_deadline:
                    self.publish(
                        "DRAINING", "Verifying mine start before logout; active time may overrun"
                    )
                    self._home()
                    if not self._break():
                        break
                    continue
                self.home = None  # No stale home claim while a new cycle owns the character.
                receipt = self.backend.cycle(self.completed + 1, self.heartbeat)
                self.controls.check()
                if receipt.get("success") is not True or receipt.get("deposited_ore") != 28:
                    raise SessionUnproven("full_cycle_receipt_unproven")
                self.deposited += receipt["deposited_ore"]
                self._home()
                self.receipts.append(receipt)
                self.completed += 1
                self.publish(reason="Cycle complete; empty inventory and exact mine start verified")
            if self.logout_proof is None:
                self._home()
            self.publish(
                "STOPPED",
                "Stopped at verified mine start"
                + ("; logged out; pending login cancelled" if self.logout_proof else ""),
            )
        except SessionStopped as exc:
            self.interrupted = True
            self.backend.cancel()
            self.publish(
                "EMERGENCY_STOPPED" if self.controls.emergency.is_set() else "PAUSED", str(exc)
            )
        except Exception as exc:
            self.interrupted = True
            self.backend.cancel()
            self.publish("ERROR", f"{type(exc).__name__}:{exc}; return_not_completed")
        finally:
            result = self.snapshot()
            result["success"] = self.state == "STOPPED"
            atomic_json(self.output / "result.json", result)
        return result
