#!/usr/bin/env python3
"""Run the guided, resumable real-client inventory validation session."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mining_automation.perception.inventory.live_validation_session_cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
