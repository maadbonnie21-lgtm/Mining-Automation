from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mining_automation.perception.inventory import (  # noqa: E402
    positive_v3_independent_validation as v1_validation,
)
from mining_automation.validation import inventory_v3_capture as capture  # noqa: E402
from validation.inventory_v3_protocol_v2.protocol import (  # noqa: E402
    verify_protocol_v2_repository,
)

PACKAGE_ROOT = ROOT / "validation" / "inventory_v3_release_receipt_preparation"
CONTRACT_PATH = PACKAGE_ROOT / "receipt-contract.json"
SIDECAR_PATH = PACKAGE_ROOT / "receipt-contract.json.sha256"

FROZEN_C4 = "74e2becd41af6b63b230ff11b07536d5da61aa80"
REJECTED_C5 = "2aad6ff304d8af20ea360e43cfcd56a54910814e"
PROTOCOL_V1_SOURCE = "b3b141e0d9ca15d729eaa98c795f6c855bff68cf"
PROTOCOL_V1_LOCK = "32764bfd82afb46d4e99292bab7d162be536e2d7"
PROTOCOL_V2_SOURCE = "0aa2647cd3382f217212377c7218848c3f322739"
PROTOCOL_V2_LOCK = "66c7e9536539979bc60e17f02f026eb64ebf0768"
PROTOCOL_V2_LOCK_SHA256 = "60ff2c511e46be3b87df4e0d9e4f705d897a4181f9152f2729ee90f6c45f8cf5"

EXPECTED_CHANGED_PATHS = {
    "docs/INVENTORY_RELEASE_RECEIPT.md",
    "tests/test_inventory_v3_release_receipt_preparation.py",
    "validation/inventory_v3_release_receipt_preparation/receipt-contract.json",
    "validation/inventory_v3_release_receipt_preparation/receipt-contract.json.sha256",
}

EXPECTED_GATES = [
    "live_protocol_v2_authorization",
    "live_protocol_v2_campaign_execution",
    "finalized_campaign_package",
    "independent_reviewer_truth",
    "terminal_conformance_pass",
    "source_approval",
    "production_identity_approval",
    "production_binding",
    "resource_perception_release",
]


def _git(*args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(ROOT), *args),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _mapping(value: object) -> Mapping[str, object]:
    assert isinstance(value, Mapping)
    return value


def _contract() -> dict[str, object]:
    value = json.loads(CONTRACT_PATH.read_bytes())
    assert isinstance(value, dict)
    return value


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")


def test_c5_contract_is_canonical_hash_bound_and_deny_only() -> None:
    raw = CONTRACT_PATH.read_bytes()
    contract = _contract()
    assert raw == _canonical_json_bytes(contract)

    digest = hashlib.sha256(raw).hexdigest()
    assert SIDECAR_PATH.read_text(encoding="ascii") == (
        f"{digest}  receipt-contract.json\n"
    )

    assert contract["schema"] == "inventory-positive-v3-release-receipt-preparation-v1"
    assert contract["status"] == "PREPARATION_ONLY_NO_RECEIPT_ISSUED"

    lineage = _mapping(contract["lineage"])
    assert lineage["frozen_c4_head_sha"] == FROZEN_C4
    assert lineage["rejected_c5_audit_head_sha"] == REJECTED_C5
    assert lineage["protocol_v1_source_sha"] == PROTOCOL_V1_SOURCE
    assert lineage["protocol_v1_lock_sha"] == PROTOCOL_V1_LOCK
    assert lineage["protocol_v2_source_sha"] == PROTOCOL_V2_SOURCE
    assert lineage["protocol_v2_lock_sha"] == PROTOCOL_V2_LOCK
    assert lineage["protocol_v2_lock_sha256"] == PROTOCOL_V2_LOCK_SHA256

    assert contract["required_release_gates"] == EXPECTED_GATES
    gate_state = _mapping(contract["current_gate_state"])
    assert set(gate_state) == set(EXPECTED_GATES)
    assert set(gate_state.values()) == {"OPEN"}

    authority = _mapping(contract["current_authority"])
    assert authority == {
        "activation_allowed": False,
        "bank_transition_authority": False,
        "controller_authority": False,
        "input_authority": False,
        "receipt_issuance_allowed": False,
        "runtime_receipt_packaged": False,
        "synthetic_evidence_is_release_evidence": False,
        "world_state_authority": False,
    }


def test_c5_preserves_inventory_fail_closed_contract() -> None:
    contract = _mapping(_contract()["inventory_contract"])
    assert contract == {
        "capacity": 28,
        "input_automation_allowed": False,
        "publication_floor": 0.8,
        "row_obstruction_outcome": "UNKNOWN",
        "unknown_grants_action_authority": False,
        "unknown_grants_bank_transition_authority": False,
        "unknown_occupied_slots": None,
        "unknown_reason_preserved": True,
        "wrong_tab_outcome": "UNKNOWN",
    }

    future = _mapping(_contract()["future_receipt_record_contract"])
    assert future["stage_order"] == [
        "authorization",
        "campaign",
        "review",
        "terminal_evaluation",
        "source_approval",
        "production_identity_approval",
        "production_binding",
    ]
    terminal = _mapping(future["terminal_evaluation"])
    assert terminal == {
        "one_shot_terminal": True,
        "required_outcome": "PASS",
        "retry_allowed": False,
    }
    separation = _mapping(future["actor_separation"])
    assert separation["operator_reviewer_source_approver_pairwise_distinct"] is True

    decision = _mapping(future["final_decision"])
    assert decision["status"] == "GRANTED"
    assert decision["release_eligible"] is True
    assert decision["unresolved_condition_ids"] == []
    for field in (
        "activation_allowed",
        "world_state_authority",
        "controller_authority",
        "input_authority",
    ):
        assert decision[field] is False
    assert future["resource_release_required"] is True


def test_c5_is_preparation_only_and_cannot_be_a_runtime_receipt_surface() -> None:
    boundary = _mapping(_contract()["preparation_boundary"])
    for field in (
        "runtime_import_path_allowed",
        "runtime_receipt_constructor_allowed",
        "runtime_loader_allowed",
        "runtime_consumer_binding_allowed",
        "writes_source_owned_runtime_files",
        "may_capture_runelite",
        "may_mutate_live_authorization_registry",
        "may_issue_release_receipt",
        "may_grant_action_authority",
    ):
        assert boundary[field] is False

    assert not (PACKAGE_ROOT / "__init__.py").exists()
    assert not tuple(PACKAGE_ROOT.glob("*.py"))
    assert importlib.util.find_spec(
        "mining_automation.perception.inventory.inventory_release_receipt"
    ) is None
    assert not (
        ROOT
        / "src"
        / "mining_automation"
        / "perception"
        / "inventory"
        / "inventory_release_receipt.py"
    ).exists()

    future_action = _mapping(_contract()["future_source_action_contract"])
    assert future_action["performed_by_this_preparation"] is False
    assert future_action["all_release_gates_must_be_closed_first"] is True
    assert future_action["requires_separate_reviewed_source_commit"] is True
    assert future_action["runtime_receipt_must_not_expose_action_surface"] is True
    assert future_action["runtime_receipt_must_be_source_owned_immutable_singleton"] is True
    assert future_action["caller_input_must_not_select_or_mint_receipt"] is True


def test_c5_preserves_all_three_frozen_repository_verifiers() -> None:
    head = _git("rev-parse", "HEAD")

    capture._verify_capture_repository(ROOT)
    assert v1_validation._verify_repository_state(ROOT, head) == ROOT.resolve()

    binding = verify_protocol_v2_repository(ROOT, expected_head=head)
    assert binding.source_commit_sha == PROTOCOL_V2_SOURCE
    assert binding.lock_commit_sha == PROTOCOL_V2_LOCK
    assert binding.lock_sha256 == PROTOCOL_V2_LOCK_SHA256


def test_c5_is_one_clean_sibling_commit_directly_from_frozen_c4() -> None:
    head = _git("rev-parse", "HEAD")
    introduction_commits = _git(
        "log",
        "--full-history",
        "--diff-filter=A",
        "--format=%H",
        head,
        "--",
        "validation/inventory_v3_release_receipt_preparation/receipt-contract.json",
    ).splitlines()
    assert len(introduction_commits) == 1
    introduction = introduction_commits[0]

    assert _git("show", "-s", "--format=%P", introduction) == FROZEN_C4
    changed_paths = set(
        _git(
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            introduction,
        ).splitlines()
    )
    assert changed_paths == EXPECTED_CHANGED_PATHS

    later_touches = _git(
        "log",
        "--format=%H",
        f"{introduction}..{head}",
        "--",
        *sorted(EXPECTED_CHANGED_PATHS),
    )
    assert later_touches == ""

    # A direct child of frozen C4 is a sibling of rejected #66, never its descendant.
    assert REJECTED_C5 != introduction
