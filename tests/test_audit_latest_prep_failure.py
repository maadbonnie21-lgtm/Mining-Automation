from __future__ import annotations

import json
from pathlib import Path

from tools import audit_latest_prep_failure as audit


def test_summary_keeps_only_owner_relevant_prep_fields(tmp_path: Path) -> None:
    receipt = tmp_path / "diagnostics" / "prep-auto-test" / "result.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(
        json.dumps(
            {
                "git_sha": "a" * 40,
                "prep_session_id": "prep-test",
                "ready_for_mining": False,
                "stop_reason": "camera_search_exhausted",
                "detail": "failed safely",
                "initial_window": {"hwnd": 42},
                "final_window": {"hwnd": 42},
                "actions": [{"action": "camera"}],
                "observations": [
                    {
                        "frame_id": 7,
                        "frame_sha256": "b" * 64,
                        "frame_path": "frame.bgra",
                        "inventory_occupied": 0,
                        "inventory_confidence": 1.0,
                        "resource_supported": False,
                        "matched_landmarks": 0,
                        "matched_zones": [],
                        "landmark_distances": [["one", 0.4]],
                    }
                ],
                "mining_input_authority": False,
                "unrelated": "drop me",
            }
        ),
        encoding="utf-8",
    )

    summary = audit._summary(receipt, audit._load(receipt))
    assert summary["receipt"] == str(receipt)
    assert summary["action_count"] == 1
    assert summary["observations"][0]["inventory_occupied"] == 0
    assert summary["observations"][0]["matched_landmarks"] == 0
    assert "unrelated" not in summary


def test_candidate_receipts_returns_newest_first(tmp_path: Path) -> None:
    old = tmp_path / "prep-old" / "result.json"
    new = tmp_path / "prep-new" / "result.json"
    old.parent.mkdir()
    new.parent.mkdir()
    old.write_text("{}", encoding="utf-8")
    new.write_text("{}", encoding="utf-8")
    old.touch()
    new.touch()
    old_stat = old.stat()
    new_stat = new.stat()
    assert new_stat.st_mtime_ns >= old_stat.st_mtime_ns
    assert audit._candidate_receipts(tmp_path)[0] == new
