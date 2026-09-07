# ruff: noqa: E402
# Source-checkout bootstrap must precede package imports.
"""Thin source-checkout wrapper for the integrated navigation entrypoint."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if "--live" not in sys.argv:
    sys.path.insert(0, str(ROOT / ".route-deps"))
from mining_automation.navigation.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
