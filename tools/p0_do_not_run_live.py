#!/usr/bin/env python3
"""P0 sentinel explaining why another live mining command is not yet authorized."""

from __future__ import annotations

import json


def main() -> int:
    print(
        json.dumps(
            {
                "live_authorized": False,
                "reason": "software_startup_resolver_not_yet_offline_accepted",
                "failed_live_heads": [
                    "5a0b1033be88e0073a2c272fb203708bcf378ee6",
                    "cc6e8e0d7e3c14ed2ac2fd36b26fc64f44746148",
                ],
                "next_allowed_action": "read_only_startup_diagnosis_after_exact_head_audit",
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
