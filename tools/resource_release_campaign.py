#!/usr/bin/env python3
"""Run the fixed passive Varrock East resource release campaign workflow."""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from mining_automation.perception.resource_release_campaign_cli import (  # noqa: E402
    main,
)

if __name__ == "__main__":
    raise SystemExit(main(repository_root=REPOSITORY_ROOT))
