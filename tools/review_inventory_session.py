"""Development entry point for inventory evidence review and replay."""

from __future__ import annotations

from mining_automation.perception.inventory.review_gate_cli import main

if __name__ == "__main__":  # pragma: no cover - exercised through CLI main
    raise SystemExit(main())
