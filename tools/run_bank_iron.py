"""Bank-only development entry point; requires explicit exact-build authorization."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from mining_automation.bank_runtime import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
