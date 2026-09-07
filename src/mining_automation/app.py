from __future__ import annotations

import sys

from . import __version__


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "route":
        from .navigation.cli import main as route_main

        raise SystemExit(route_main(sys.argv[2:]))
    print(f"Mining Automation {__version__}")
    print("Foundation build: no production mining workflow is declared supported yet.")


if __name__ == "__main__":
    main()
