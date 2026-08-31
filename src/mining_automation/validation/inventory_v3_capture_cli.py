"""Fixed CLI for a future passive Inventory V3 independent campaign."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import cast

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Capture the frozen seven-stage Inventory V3 independent campaign. "
            "This passive command never runs a detector or sends RuneLite input."
        )
    )
    parser.add_argument("--operator", required=True)
    parser.add_argument("--runelite-build", required=True)
    parser.add_argument("--client-mode", required=True)
    parser.add_argument("--theme", required=True)
    parser.add_argument("--renderer", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    if not (
        sys.flags.isolated
        and sys.flags.no_site
        and sys.pycache_prefix is not None
    ):
        print(
            "Inventory V3 passive capture requires the locked Python -I -S "
            "launcher with an isolated source cache.",
            file=sys.stderr,
        )
        return 2
    from ..capture import CaptureError
    from .inventory_v3_capture import (
        PassiveInventoryV3CaptureError,
        PassiveInventoryV3CaptureInputs,
        run_passive_inventory_v3_capture_campaign,
    )

    arguments = build_parser().parse_args(argv)
    repository_root = Path(__file__).resolve().parents[3]
    try:
        inputs = PassiveInventoryV3CaptureInputs(
            operator=cast(str, arguments.operator),
            runelite_build=cast(str, arguments.runelite_build),
            client_mode=cast(str, arguments.client_mode),
            theme=cast(str, arguments.theme),
            renderer=cast(str, arguments.renderer),
        )

        result = run_passive_inventory_v3_capture_campaign(
            inputs=inputs,
            repository_root=repository_root,
        )
    except KeyboardInterrupt:
        print(
            "Inventory V3 source campaign aborted; retained evidence is not validation.",
            file=sys.stderr,
        )
        return 130
    except (
        CaptureError,
        OSError,
        PassiveInventoryV3CaptureError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as exc:
        print(f"Inventory V3 passive capture error: {exc}", file=sys.stderr)
        return 1

    try:
        print("CAPTURE COMPLETE — NOT REVIEWED OR VALIDATED")
        print(f"Campaign: {result.campaign_id}")
        print(f"Captures retained: {result.capture_count}")
        print(f"Source session: {result.source_session_report_path.resolve()}")
        print(f"Source session SHA-256: {result.source_session_report_sha256}")
        print(f"Capture source build: {result.capture_build_sha}")
        print(f"Capture configuration: {result.capture_configuration_id}")
        print(f"Capture execution HEAD: {result.capture_execution_head_sha}")
        print(f"Host reservation SHA-256: {result.host_reservation_sha256}")
        print(f"Protocol lock commit: {result.protocol_lock_git_commit_sha}")
        print(f"Live authorization ID: {result.live_authorization_id}")
        print(
            "Live authorization commit: "
            f"{result.live_authorization_git_commit_sha}"
        )
        print("Activation allowed: false")
        print("CAPTURE COMPLETE — NOT REVIEWED OR VALIDATED")
    except OSError:
        # The completion seal is authoritative. A closed output stream cannot
        # retroactively turn a sealed capture into a failed/ambiguous campaign.
        pass
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by subprocess tests
    raise SystemExit(main())
