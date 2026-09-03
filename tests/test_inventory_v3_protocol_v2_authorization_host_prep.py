from __future__ import annotations

import json
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "tools" / "prepare_inventory_v3_protocol_v2_authorization.ps1"
_PLAN = (
    _ROOT
    / "validation"
    / "inventory_v3_protocol_v2"
    / "authorization_child_plan.json"
)
_L2 = "66c7e9536539979bc60e17f02f026eb64ebf0768"
_LOCK = "60ff2c511e46be3b87df4e0d9e4f705d897a4181f9152f2729ee90f6c45f8cf5"
_ALLOWLIST = [
    "validation/inventory_v3/live-campaign-authorizations.json",
    "validation/inventory_v3_protocol_v2/live-campaign-authorizations.json",
    "validation/inventory_v3_protocol_v2/live-campaign-authorizations.json.sha256",
]


def test_host_proposal_plan_is_exact_non_authorizing_and_three_file_only() -> None:
    plan = json.loads(_PLAN.read_text(encoding="utf-8"))
    assert plan["exact_starting_git_sha"] == _L2
    assert plan["protocol_lock_sha256"] == _LOCK
    assert plan["source_change_allowlist"] == _ALLOWLIST
    assert plan["proposal_status"] == "host-proposal-only-not-authorized"
    assert plan["live_pixels_allowed_during_proposal"] is False
    assert plan["campaign_execution_allowed"] is False
    assert plan["activation_allowed"] is False
    assert plan["input_authority"] is False
    assert plan["proposal_expected_fields"] == {
        "activation_allowed": False,
        "promotion_allowed": False,
        "source_registry_modified": False,
        "status": "proposal-only-not-authorized",
    }
    assert len(plan["review_checklist"]) >= 9
    assert len(plan["authorization_child_ci_matrix"]) >= 9


def test_windows_command_uses_exact_detached_l2_and_nonwriting_proposal_api() -> None:
    source = _SCRIPT.read_text(encoding="utf-8")
    assert f"$StartingSha = '{_L2}'" in source
    assert f"$LockSha256 = '{_LOCK}'" in source
    assert "worktree add --detach $DetachedCheckout $StartingSha" in source
    assert "build_live_authorization_proposal" in source
    assert "expected_lock_head=os.environ[\"C8A_EXPECTED_HEAD\"]" in source
    assert "opaque_receipt_id=os.environ[\"C8A_OPAQUE_RECEIPT_ID\"]" in source
    assert "attempt_base=attempt_base" in source
    assert "assert not attempt_base.exists()" in source
    assert "git -C $DetachedCheckout status --porcelain=v1 --untracked-files=all" in source
    assert "proposal-only-not-authorized" in source
    assert "live_pixels_captured = $false" in source
    assert "campaign_authorized = $false" in source
    assert "approval_self_granted = $false" in source
    assert "repository_files_changed = @()" in source
    assert "attempt_state_created = $false" in source
    assert "capture-passive-campaign" not in source
    assert "run_protocol_v2" not in source
    assert "mouse" not in source.lower()
    assert "sendkeys" not in source.lower()
    assert "click" not in source.lower()


def test_windows_command_freezes_uuidv4_and_exact_proposal_file_order() -> None:
    source = _SCRIPT.read_text(encoding="utf-8")
    uuid_pattern = (
        r"\^\[0-9a-f\]\{8\}-\[0-9a-f\]\{4\}-4\[0-9a-f\]\{3\}"
        r"-\[89ab\]\[0-9a-f\]\{3\}-\[0-9a-f\]\{12\}\$"
    )
    assert re.search(uuid_pattern, source)
    positions = [source.index(path) for path in _ALLOWLIST]
    assert positions == sorted(positions)
    # The command checks this once in the embedded proposal builder and once
    # again after PowerShell reloads the written proposal. The detached L2
    # worktree status checks independently prove that no source write occurred.
    assert source.count("source_registry_modified") == 2
    assert source.count("activation_allowed") >= 4
    assert source.count("promotion_allowed") >= 4
