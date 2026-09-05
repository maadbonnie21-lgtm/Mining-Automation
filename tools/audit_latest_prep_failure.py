#!/usr/bin/env python3
"""Summarize the newest local PREP receipt and its saved observations.

This tool is read-only. It sends no RuneLite input and exists so the next
real-client diagnosis produces one concise machine/owner report instead of
requiring Tyler to inspect raw JSON.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIAGNOSTICS = REPOSITORY_ROOT / "diagnostics"


def _candidate_receipts(root: Path) -> list[Path]:
    return sorted(
        (path for path in root.glob("**/result.json") if "prep" in path.parent.name),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("receipt root must be an object")
    return payload


def _summary(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    observations = payload.get("observations")
    items = observations if isinstance(observations, list) else []
    concise_observations = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        concise_observations.append(
            {
                "index": index,
                "frame_id": item.get("frame_id"),
                "frame_sha256": item.get("frame_sha256"),
                "frame_path": item.get("frame_path"),
                "inventory_occupied": item.get("inventory_occupied"),
                "inventory_confidence": item.get("inventory_confidence"),
                "inventory_unknown_reason": item.get("inventory_unknown_reason"),
                "resource_supported": item.get("resource_supported"),
                "accepted_pose_id": item.get("accepted_pose_id"),
                "software_registration_identity": item.get(
                    "software_registration_identity"
                ),
                "matched_landmarks": item.get("matched_landmarks"),
                "matched_zones": item.get("matched_zones"),
                "landmark_distances": item.get("landmark_distances"),
                "diagnostic_score": item.get("diagnostic_score"),
            }
        )
    return {
        "receipt": str(path),
        "git_sha": payload.get("git_sha"),
        "prep_session_id": payload.get("prep_session_id"),
        "ready_for_mining": payload.get("ready_for_mining"),
        "stop_reason": payload.get("stop_reason"),
        "detail": payload.get("detail"),
        "initial_window": payload.get("initial_window"),
        "final_window": payload.get("final_window"),
        "action_count": len(payload.get("actions", []))
        if isinstance(payload.get("actions"), list)
        else None,
        "observations": concise_observations,
        "mining_input_authority": payload.get("mining_input_authority"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--diagnostics", type=Path, default=DEFAULT_DIAGNOSTICS)
    args = parser.parse_args()

    path = args.receipt
    if path is None:
        candidates = _candidate_receipts(args.diagnostics)
        if not candidates:
            raise SystemExit("STOP: no PREP result.json found")
        path = candidates[0]
    if not path.is_file():
        raise SystemExit(f"STOP: receipt does not exist: {path}")

    print(json.dumps(_summary(path, _load(path)), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
