#!/usr/bin/env python3
"""Launch beta controls, or check imports without opening UI or accessing RuneLite."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools")]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Imports only; no UI, capture, focus or game input"
    )
    args = parser.parse_args()
    if args.check:
        for name in (
            "tkinter",
            "numpy",
            "cv2",
            "mining_automation.beta_session",
            "mining_automation.beta_launcher",
            "mining_automation.beta_backend",
            "mining_automation.beta_home",
            "mining_automation.beta_auth",
            "mining_automation.beta_mining",
            "mining_automation.beta_panel",
            "mining_automation.beta_route",
            "run_mining_to_full_safe",
        ):
            importlib.import_module(name)
        print(
            json.dumps(
                dict(
                    status="IMPORT_CHECK_PASS",
                    live_input=False,
                    gui_opened=False,
                    live_acceptance="NOT_RUN",
                )
            )
        )
        return 0
    if sys.platform != "win32":
        parser.error("This beta launcher requires the supported Windows/RuneLite environment")
    from mining_automation.beta_launcher import Launcher

    try:
        Launcher(ROOT).run()
        return 0
    except Exception as exc:
        (ROOT / "outputs").mkdir(exist_ok=True)
        (ROOT / "outputs/beta-launch-error.txt").write_text(
            f"{type(exc).__name__}: {exc}", encoding="utf-8"
        )
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, str(exc), "Mining Automation - cannot start", 0x10)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
