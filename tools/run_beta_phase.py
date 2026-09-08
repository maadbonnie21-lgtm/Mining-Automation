#!/usr/bin/env python3
"""Owned-child entrypoint. A parent job lease and exact frozen build precede live I/O."""

from __future__ import annotations

import hashlib
import json
import runpy
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools")]

from mining_automation.beta_backend import PHASES  # noqa: E402
from mining_automation.beta_process import wait_for_parent_gate  # noqa: E402
from mining_automation.beta_session import atomic_json  # noqa: E402


def validate_request(path: Path) -> dict:
    path = path.resolve()
    if not path.is_relative_to((ROOT / "outputs").resolve()):
        raise ValueError("Child requests must be inside this checkout outputs")
    request = json.loads(path.read_text(encoding="utf-8"))
    for field in ("output", "gate", "cancel", "auth_cancel"):
        target = Path(request[field]).resolve()
        if not target.is_relative_to(path.parent):
            raise ValueError("Child paths must remain inside their owned session directory")
    if type(request["hwnd"]) is not int or request["hwnd"] <= 0:
        raise ValueError("Exact positive HWND required")
    if not request["title"].startswith("RuneLite - "):
        raise ValueError("Exact account window title required")
    if request["kind"] not in ("home", "mine", "outbound", "bank", "return", "logout", "login"):
        raise ValueError("Unknown beta phase")
    return request


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        raise ValueError("Exactly one parent request file is required")
    request = validate_request(Path(args[0]))
    output = Path(request["output"])
    try:
        if output.exists():
            raise ValueError("Phase evidence directory must be new")
        wait_for_parent_gate(Path(request["gate"]), Path(request["cancel"]))

        def git(*a: str) -> str:
            return subprocess.run(
                ["git", "-C", str(ROOT), *a], check=True, capture_output=True, text=True
            ).stdout.strip()

        if git("rev-parse", "HEAD") != request["sha"] or git(
            "status", "--porcelain", "--untracked-files=no"
        ):
            raise RuntimeError("Exact clean committed child build required")
        from mining_automation.beta_input import InputPolicy, install_policy
        from mining_automation.navigation.windows import NativeRouteBackend

        native_output = (
            output
            if request["kind"] in ("home", "logout", "login")
            else output.with_name(output.name + "-guard")
        )
        # Only the explicit first Start may focus the existing non-minimized window once.
        native = NativeRouteBackend(
            request["hwnd"],
            native_output,
            expected_title=request["title"],
            focus_existing=request["initial_window"] is None,
            stop_file=Path(request["cancel"]),
        )
        if request["initial_window"] is not None and native.initial != request["initial_window"]:
            raise RuntimeError("Pinned session HWND/PID/title/window/DPI changed")
        policy = InputPolicy(
            native,
            Path(request["cancel"]),
            Path(request["auth_cancel"]),
            smooth=request["smooth_cursor"],
            authentication=request["kind"] in ("login", "logout"),
        )
        install_policy(policy)
        policy.check()
        kind = request["kind"]
        if kind == "home":
            from mining_automation.beta_home import verify_home

            result = verify_home(native, policy, ROOT, fresh_rocks=request["fresh_rocks"])
        elif kind in ("login", "logout"):
            from mining_automation import beta_auth

            if kind == "logout":
                beta_auth.load_logout_profile(ROOT)
                from mining_automation.beta_home import verify_home

                verify_home(native, policy, ROOT, fresh_rocks=False)
            result = getattr(beta_auth, kind)(policy, ROOT)
        else:
            _, tool, confirm, sha_arg = next(item for item in PHASES if item[0] == kind)
            phase_args = [
                "--live",
                "--hwnd",
                str(request["hwnd"]),
                "--title",
                request["title"],
                sha_arg,
                request["sha"],
                "--confirm",
                confirm,
                "--output",
                str(output),
            ]
            if kind == "mine":
                sys.argv = [str(ROOT / "tools" / tool), *phase_args]
                import run_mining_to_full_safe as safe

                from mining_automation.beta_mining import beta_mining_backend

                original_run = safe.mining.run_mining_until_full
                safe.mining.WindowsMiningToFullBackend = beta_mining_backend(
                    safe.SafeWindowsMiningToFullBackend,
                    policy,
                    varied_points=request["varied_rock_points"],
                )
                safe.mining.run_mining_until_full = lambda backend, config: (
                    safe._run_with_hover_recovery(backend, config, original_run)
                )
                code = safe.mining.main(phase_args)
            else:
                sys.argv = [str(ROOT / "tools" / tool), *phase_args]
                try:
                    runpy.run_path(str(ROOT / "tools" / tool), run_name="__main__")
                    code = 0
                except SystemExit as exc:
                    code = int(exc.code or 0)
            result = json.loads((output / "result.json").read_text(encoding="utf-8"))
            if code != 0:
                return code
        policy.check()
        boundary = dict(
            git_sha=request["sha"],
            start_window=native.initial,
            end_window=native.snapshot(),
            success=result.get("success") is True,
        )
        if kind in ("mine", "outbound", "bank", "return"):
            boundary["phase_result_sha256"] = hashlib.sha256(
                (output / "result.json").read_bytes()
            ).hexdigest()
            atomic_json(output / "beta-phase.json", boundary)
        else:
            result.update(boundary)
            atomic_json(output / "result.json", result)
        return 0 if result.get("success") is True else 2
    except Exception as exc:
        # Preserve phase receipts even when parent-level validation fails afterwards.
        target = output / "result.json"
        if target.exists():
            atomic_json(
                output / "beta-boundary-error.json", dict(reason=f"{type(exc).__name__}:{exc}")
            )
        else:
            atomic_json(
                target,
                dict(
                    success=False,
                    status="STOP",
                    git_sha=request["sha"],
                    stop_reason=f"{type(exc).__name__}:{exc}",
                ),
            )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
