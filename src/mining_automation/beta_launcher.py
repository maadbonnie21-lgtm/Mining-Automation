"""Desktop beta controls. Opening the launcher never starts game input."""

from __future__ import annotations

import json
import math
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .beta_backend import PhaseBackend
from .beta_hotkeys import Hotkeys
from .beta_process import InstanceLease
from .beta_session import BetaSession, RunBreakRow, SessionControls, SessionSettings, atomic_json

REQUIRED_FEATURES = {"launcher", "canonical_finish", "emergency_stop"}


def check_handover(root: Path, sha: str, hwnd: int, settings: SessionSettings) -> dict[str, Any]:
    path = root / "outputs/beta-live-handover.json"
    if not path.is_file():
        raise RuntimeError(
            "Awaiting exclusive issue #94 live-test handover for this build. No game input."
        )
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        value.get("issue") != 94
        or type(value.get("comment_id")) is not int
        or value["comment_id"] <= 0
        or value.get("git_sha") != sha
        or value.get("hwnd") != hwnd
        or value.get("input_owner_released") is not True
        or not isinstance(value.get("expires_at_unix"), (int, float))
        or not math.isfinite(value["expires_at_unix"])
        or value["expires_at_unix"] <= time.time()
    ):
        raise RuntimeError(
            "The exclusive live handover is missing, stale, or for another build/window."
        )
    required = REQUIRED_FEATURES | {settings.mode}
    if settings.smooth_cursor:
        required.add("smooth_cursor")
    if settings.varied_rock_points:
        required.add("varied_rock_points")
    if not required.issubset(set(value.get("features", []))):
        raise RuntimeError("This handover does not authorize the selected feature test.")
    return value


def other_phase_processes() -> list[int]:
    script = (
        r"$x=Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^pythonw?\.exe$' "
        "-and $_.CommandLine -match 'run_(mining_to_full|mine_to_bank|bank_to_mine|bank_iron|five_cycles|full_cycle|beta_phase|28_auto|proven_mining)' }; "
        "@($x | Select-Object -ExpandProperty ProcessId) | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    value = json.loads(result.stdout) if result.stdout.strip() else []
    return [value] if isinstance(value, int) else list(value or [])


def git_head(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    dirty = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
        check=True,
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if dirty.stdout.strip():
        raise RuntimeError("Source has uncommitted changes. No live start is allowed.")
    return result.stdout.strip()


class Launcher:
    def __init__(self, root: Path) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk, self.ttk, self.root_path = tk, ttk, root
        self.data = Path(os.environ.get("LOCALAPPDATA", root / "outputs")) / "MiningAutomationBeta"
        self.lease = InstanceLease(self.data / "launcher.lock")
        self.lease.acquire()
        self.window = tk.Tk()
        self.window.title("Mining Automation â€” Beta Controls")
        self.window.geometry("580x740")
        self.window.minsize(500, 600)
        self.worker: threading.Thread | None = None
        self.backend: PhaseBackend | None = None
        self.session_output: Path | None = None
        self.close_requested = False
        self.windows: dict[str, Any] = {}
        self.hotkeys: Hotkeys | None = None
        self.panel: Any = None
        self.settings_path = self.data / "settings.json"
        self.message = tk.StringVar(
            value="Development candidate â€” live acceptance not yet passed."
        )
        self.activity = tk.StringVar(value="IDLE â€” no game input")
        self.metrics = tk.StringVar(value="Cycles 0   |   Ore deposited 0   |   Runtime 00:00:00")
        self.timing = tk.StringVar(value="No break scheduled")
        try:
            settings = SessionSettings.load(self.settings_path)
        except (ValueError, TypeError, OSError) as exc:
            settings = SessionSettings()
            self.message.set(f"Saved settings rejected: {exc}")
        self.mode = tk.StringVar(value=settings.mode)
        self.cycle_limit = tk.StringVar(value=str(settings.cycles))
        self.repeat = tk.BooleanVar(value=settings.repeat)
        self.smooth = tk.BooleanVar(value=settings.smooth_cursor)
        self.varied = tk.BooleanVar(value=settings.varied_rock_points)
        self.selected_window = tk.StringVar()
        self.run_minutes, self.break_minutes = tk.StringVar(value="60"), tk.StringVar(value="10")
        pad = {"padx": 14, "pady": 5}
        self.form = ttk.Frame(self.window)
        self.form.pack(fill="both", expand=True)
        ttk.Label(self.form, text="Varrock East â€¢ Iron", font=("Segoe UI", 18, "bold")).pack(
            anchor="w", **pad
        )
        ttk.Label(
            self.form, text="Controls candidate â€¢ saved settings â€¢ one input-owning session"
        ).pack(anchor="w", **pad)
        self.combo = ttk.Combobox(self.form, textvariable=self.selected_window, state="readonly")
        self.combo.pack(fill="x", **pad)
        ttk.Button(self.form, text="Refresh RuneLite windows", command=self.refresh_windows).pack(
            anchor="w", **pad
        )
        row = ttk.Frame(self.form)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="Mode").pack(side="left")
        ttk.Combobox(
            row,
            textvariable=self.mode,
            values=("continuous", "finite", "routine"),
            state="readonly",
            width=15,
        ).pack(side="left", padx=8)
        ttk.Label(row, text="Cycle limit").pack(side="left", padx=8)
        ttk.Entry(row, textvariable=self.cycle_limit, width=7).pack(side="left")
        ttk.Label(self.form, text="Routine rows â€” minutes active / minutes logged out").pack(
            anchor="w", **pad
        )
        self.rows = ttk.Treeview(
            self.form, columns=("run", "break"), show="headings", height=5, selectmode="browse"
        )
        self.rows.heading("run", text="Run (minutes)")
        self.rows.heading("break", text="Break (minutes)")
        self.rows.column("run", width=230)
        self.rows.column("break", width=230)
        self.rows.pack(fill="x", **pad)
        for item in settings.routine:
            self.rows.insert(
                "", "end", values=(f"{item.active_s / 60:g}", f"{item.break_s / 60:g}")
            )
        self.rows.bind("<<TreeviewSelect>>", self.selected_row)
        row = ttk.Frame(self.form)
        row.pack(fill="x", **pad)
        ttk.Entry(row, textvariable=self.run_minutes, width=10).pack(side="left")
        ttk.Entry(row, textvariable=self.break_minutes, width=10).pack(side="left", padx=8)
        for text, command in [
            ("Add", self.add_row),
            ("Update", self.update_row),
            ("Copy", self.copy_row),
            ("Remove", self.remove_row),
        ]:
            ttk.Button(row, text=text, command=command, width=8).pack(side="left", padx=2)
        row = ttk.Frame(self.form)
        row.pack(fill="x", **pad)
        ttk.Button(row, text="Move up", command=lambda: self.move_row(-1)).pack(side="left")
        ttk.Button(row, text="Move down", command=lambda: self.move_row(1)).pack(
            side="left", padx=8
        )
        ttk.Checkbutton(row, text="Repeat routine", variable=self.repeat).pack(side="left", padx=8)
        ttk.Checkbutton(
            self.form, text="Smooth cursor (separate live handover required)", variable=self.smooth
        ).pack(anchor="w", **pad)
        ttk.Checkbutton(
            self.form,
            text="Varied safe rock points (separate live handover required)",
            variable=self.varied,
        ).pack(anchor="w", **pad)
        row = ttk.Frame(self.form)
        row.pack(fill="x", **pad)
        ttk.Button(row, text="Save settings", command=self.save).pack(side="left")
        self.start_button = ttk.Button(row, text="Start", command=self.start)
        self.start_button.pack(side="right")
        self.status_frame = ttk.Frame(self.window)
        self.status_frame.pack(fill="x")
        ttk.Separator(self.status_frame).pack(fill="x", **pad)
        ttk.Label(
            self.status_frame,
            textvariable=self.activity,
            font=("Segoe UI", 12, "bold"),
            wraplength=540,
        ).pack(anchor="w", **pad)
        ttk.Label(self.status_frame, textvariable=self.metrics, wraplength=540).pack(
            anchor="w", **pad
        )
        ttk.Label(self.status_frame, textvariable=self.timing, wraplength=540).pack(
            anchor="w", **pad
        )
        ttk.Label(self.status_frame, textvariable=self.message, wraplength=540).pack(
            anchor="w", **pad
        )
        row = ttk.Frame(self.status_frame)
        row.pack(fill="x", **pad)
        ttk.Button(row, text="Stop / return (F8)", command=self.stop).pack(side="left")
        ttk.Button(row, text="Emergency Stop (F9)", command=self.emergency).pack(side="right")
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        try:
            self.hotkeys = Hotkeys()
        except (RuntimeError, OSError) as exc:
            self.start_button.configure(state="disabled")
            self.message.set(str(exc))
        self.refresh_windows()
        self.poll()

    def refresh_windows(self) -> None:
        try:
            from .capture.windows.win32_api import RealWin32Api

            self.windows = {
                f"{w.title}  [HWND {w.hwnd}]": w
                for w in RealWin32Api().enumerate_windows()
                if w.title.startswith("RuneLite - ") and w.is_visible and not w.is_minimized
            }
            self.combo["values"] = tuple(self.windows)
            if self.selected_window.get() not in self.windows:
                self.selected_window.set(next(iter(self.windows), ""))
        except Exception as exc:
            self.message.set(f"Window discovery failed: {exc}")

    def selected_row(self, event: Any = None) -> None:
        selection = self.rows.selection()
        if selection:
            run, pause = self.rows.item(selection[0], "values")
            self.run_minutes.set(run)
            self.break_minutes.set(pause)

    def row_values(self) -> tuple[str, str]:
        run, pause = float(self.run_minutes.get()), float(self.break_minutes.get())
        RunBreakRow(run * 60, pause * 60)
        return f"{run:g}", f"{pause:g}"

    def add_row(self) -> None:
        try:
            self.rows.insert("", "end", values=self.row_values())
        except ValueError as exc:
            self.message.set(str(exc))

    def update_row(self) -> None:
        try:
            selection = self.rows.selection()
            if selection:
                self.rows.item(selection[0], values=self.row_values())
        except ValueError as exc:
            self.message.set(str(exc))

    def copy_row(self) -> None:
        selection = self.rows.selection()
        if selection:
            self.rows.insert(
                "", self.rows.index(selection[0]) + 1, values=self.rows.item(selection[0], "values")
            )

    def remove_row(self) -> None:
        selection = self.rows.selection()
        if selection:
            self.rows.delete(selection[0])

    def move_row(self, offset: int) -> None:
        selection = self.rows.selection()
        if selection:
            index = max(
                0, min(len(self.rows.get_children()) - 1, self.rows.index(selection[0]) + offset)
            )
            self.rows.move(selection[0], "", index)

    def settings(self) -> SessionSettings:
        rows = tuple(
            RunBreakRow(
                float(self.rows.item(item, "values")[0]) * 60,
                float(self.rows.item(item, "values")[1]) * 60,
            )
            for item in self.rows.get_children()
        )
        return SessionSettings(
            mode=self.mode.get(),
            cycles=int(self.cycle_limit.get()),
            routine=rows,
            repeat=self.repeat.get(),
            smooth_cursor=self.smooth.get(),
            varied_rock_points=self.varied.get(),
        )

    def save(self) -> None:
        try:
            self.settings().save(self.settings_path)
            self.message.set("Settings saved. Edits never restart an active session.")
        except (ValueError, OSError) as exc:
            self.message.set(f"Settings not saved: {exc}")

    def start(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            self.message.set("A session already owns input. Stop it before a new Start.")
            return
        try:
            settings = self.settings()
            target = self.windows.get(self.selected_window.get())
            if target is None:
                raise RuntimeError(
                    "Select the existing account window. No window/camera setup is inferred."
                )
            sha = git_head(self.root_path)
            check_handover(self.root_path, sha, target.hwnd, settings)
            if other_phase_processes():
                raise RuntimeError(
                    "Another mining/navigation/banking worker is running. No concurrent controller started."
                )
            if settings.mode == "routine":
                from .beta_auth import load_logout_profile

                load_logout_profile(self.root_path)
            settings.save(self.settings_path)
            controls = SessionControls()
            self.session_output = (
                self.root_path
                / "outputs"
                / f"beta-session-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
            )
            self.backend = PhaseBackend(
                self.root_path,
                self.session_output,
                target.hwnd,
                target.title,
                sha,
                settings,
                controls,
            )
            session = BetaSession(settings, controls, self.backend, self.session_output, sha=sha)

            def run() -> None:
                try:
                    session.run()
                except Exception as exc:
                    atomic_json(
                        self.session_output / "launcher-error.json",
                        dict(state="ERROR", reason=str(exc), return_not_completed=True),
                    )

            self.worker = threading.Thread(target=run, name="beta-session-owner", daemon=False)
            self.start_button.configure(state="disabled")
            self.message.set(
                "F8 finishes the cycle and returns. F9 cancels immediately. Live proof remains pending."
            )
            from .beta_panel import StatusPanel

            self.panel = StatusPanel(
                self.tk,
                self.ttk,
                self.window,
                target.hwnd,
                (self.activity, self.metrics, self.timing, self.message),
                self.stop,
                self.emergency,
            )
            self.backend.control_hwnd = self.panel.hwnd
            self.window.iconify()
            self.worker.start()
        except Exception as exc:
            if self.panel is not None:
                self.panel.close()
                self.panel = None
            self.start_button.configure(state="normal" if self.hotkeys else "disabled")
            self.message.set(str(exc))

    def stop(self) -> None:
        if self.backend is not None and self.worker is not None and self.worker.is_alive():
            self.backend.request_stop()
            self.message.set(
                "Stop latched: finishing the safe cycle / returning to mine. No new cycle or login."
            )

    def emergency(self) -> None:
        if self.backend is not None and self.worker is not None and self.worker.is_alive():
            self.backend.request_emergency()
            self.message.set(
                "Emergency latched. No walking, banking, logout or login will be started."
            )

    def poll(self) -> None:
        if self.hotkeys is not None:
            for key in self.hotkeys.poll():
                if key == 0x4D08:
                    self.stop()
                elif key == 0x4D09:
                    self.emergency()
        if self.session_output is not None:
            try:
                status = json.loads(
                    (self.session_output / "status.json").read_text(encoding="utf-8")
                )
                self.activity.set(f"{status['state']} â€¢ {status['phase']}")
                elapsed = int(status["elapsed_s"])
                self.metrics.set(
                    f"Cycles {status['cycles_completed']} | Ore deposited {status['ore_deposited']} | "
                    f"Runtime {elapsed // 3600:02d}:{elapsed // 60 % 60:02d}:{elapsed % 60:02d}"
                )
                if status["break_remaining_s"] is not None:
                    self.timing.set(
                        f"Logged-out break remaining: {status['break_remaining_s']:.0f} seconds"
                    )
                elif status["active_remaining_s"] is not None:
                    self.timing.set(
                        f"Active remaining: {status['active_remaining_s']:.0f}s | "
                        f"Wind-down overrun: {status['wind_down_overrun_s']:.0f}s"
                    )
                else:
                    self.timing.set("Continuous / finite mode â€” no automatic breaks")
                self.message.set(status["reason"])
            except (OSError, ValueError, KeyError):
                pass
        if self.worker is not None and not self.worker.is_alive():
            self.worker = None
            if self.panel is not None:
                self.panel.close()
                self.panel = None
            self.start_button.configure(state="normal" if self.hotkeys else "disabled")
            self.window.deiconify()
            if self.close_requested:
                self.close()
                return
        self.window.after(100, self.poll)

    def close(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            self.close_requested = True
            self.stop()
            return
        if self.panel is not None:
            self.panel.close()
            self.panel = None
        if self.hotkeys:
            self.hotkeys.close()
        self.lease.close()
        self.window.destroy()

    def run(self) -> None:
        self.window.mainloop()
