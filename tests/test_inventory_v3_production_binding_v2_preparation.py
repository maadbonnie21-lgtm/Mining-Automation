from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "validation/inventory_v3_release_binding_preparation/proposal-input.json"
PKG = ROOT / "validation/inventory_v3_production_binding_v2_preparation"
CONTRACT = PKG / "contract.json"
C7 = "861613a3830ebfa9249ef8e89f94a0188e03eadb"
P0 = "6c675125cdfa1cd91763f2e8df07cb2faae67796"


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
def test_v1_bug_is_reproduced_and_v2_separates_commits() -> None:
    base = _read(BASE)
    contract = _read(CONTRACT)
    binding = next(
        item for item in base["required_cross_bindings"]
        if item["id"] == "production-build-git-commit"
    )
    members = list(binding["members"])
    removed = contract["v1_corrections"]["production_build_git_commit_remove_members"]
    assert "input#/current_inputs/source_approval/git_commit_sha" in members
    assert "source-approval-registry#git-commit-sha" in members
    assert contract["effective_production_build_members"] == [
        member for member in members if member not in removed
    ]
    assert all(member not in contract["effective_production_build_members"] for member in removed)
    separation = contract["identity_separation_contract"]
    assert separation["independent_exact_values_required"] is True
    assert separation["equality_implied_by_contract"] is False


def test_source_approval_binding_remains_independent() -> None:
    base = _read(BASE)
    binding = next(
        item for item in base["required_cross_bindings"]
        if item["id"] == "source-approval-git-commit"
    )
    members = set(binding["members"])
    assert "input#/current_inputs/source_approval/git_commit_sha" in members
    assert "source-approval-registry#git-commit-sha" in members
def test_current_p0_candidate_blobs_resolve_exactly() -> None:
    contract = _read(CONTRACT)
    candidate = contract["current_p0_candidate"]
    assert candidate["git_commit_sha"] == P0
    assert candidate["status"] == "candidate-not-approved"
    assert re.fullmatch(r"[0-9a-f]{40}", P0)
    entries = candidate["source_git_blobs"]
    paths = [entry["path"] for entry in entries]
    assert paths == sorted(paths)
    assert len(paths) == len(set(paths))
    for entry in entries:
        actual = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", f"{P0}:{entry['path']}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert actual == entry["git_blob"]


def test_protocol_verification_moves_off_the_executable_lineage() -> None:
    base = _read(BASE)
    contract = _read(CONTRACT)
    action = base["production_binding_approval_action_contract"]
    assert action["protocol_v2_repository_verification_at_build_commit_required"] is True
    correction = contract["v1_corrections"]
    assert correction["protocol_v2_repository_verification_at_build_commit_required"] is False
    assert correction["protocol_v2_repository_verification_at_source_approval_commit_required"] is True
    assert correction["production_build_exact_source_blob_verification_required"] is True
def test_v2_preserves_fail_closed_inventory_and_zero_authority() -> None:
    base = _read(BASE)
    contract = _read(CONTRACT)
    assert set(contract["authority"].values()) == {False}
    frozen = contract["frozen_inventory_rules"]
    assert frozen == {
        "capacity": 28,
        "publication_floor": 0.8,
        "unknown_can_grant_action_readiness": False,
        "unknown_can_grant_full": False,
        "unknown_is_fail_closed": True,
    }
    unknown = base["unknown_policy"]
    assert unknown["publication_floor"] == 0.8
    assert unknown["unknown_can_grant_action_readiness"] is False
    assert unknown["unknown_can_grant_full"] is False
    assert unknown["validation_failure_remains_failure"] is True


def test_future_materialization_requires_both_exact_identities() -> None:
    contract = _read(CONTRACT)
    future = contract["future_materialization"]
    assert future["production_binding_approval_schema"].endswith("-v2")
    assert future["production_binding_record_schema"].endswith("-v2")
    assert future["source_approval_commit_must_be_explicit"] is True
    assert future["production_build_commit_must_be_explicit"] is True
    assert future["protocol_v2_verification_anchor"] == "source-approval-lineage"
    assert future["production_blob_verification_anchor"] == "production-build-git-commit"
    assert future["wrong_source_approval_commit_denies"] is True
    assert future["wrong_production_build_commit_denies"] is True
    assert future["wrong_production_blob_set_denies"] is True
    assert future["mixed_identity_denies"] is True
def test_package_hashes_are_exact() -> None:
    contract_bytes = CONTRACT.read_bytes()
    contract_digest = _sha256(contract_bytes)
    assert (PKG / "contract.json.sha256").read_text(encoding="ascii") == (
        f"{contract_digest}  contract.json\n"
    )
    tree_path = PKG / "package-tree.json"
    tree = _read(tree_path)
    assert tree["schema"] == "inventory-positive-v3-independent-package-tree-v1"
    for entry in tree["entries"]:
        payload = (PKG / entry["path"]).read_bytes()
        assert entry["sha256"] == _sha256(payload)
        assert entry["size_bytes"] == len(payload)
    tree_digest = _sha256(tree_path.read_bytes())
    assert (PKG / "package-tree.json.sha256").read_text(encoding="ascii") == (
        f"{tree_digest}  package-tree.json\n"
    )


def test_c8b_is_additive_over_frozen_c7() -> None:
    changed = subprocess.run(
        ["git", "-C", str(ROOT), "diff", "--name-only", C7],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    expected = {
        "tests/test_inventory_v3_production_binding_v2_preparation.py",
        "validation/inventory_v3_production_binding_v2_preparation/contract.json",
        "validation/inventory_v3_production_binding_v2_preparation/contract.json.sha256",
        "validation/inventory_v3_production_binding_v2_preparation/package-tree.json",
        "validation/inventory_v3_production_binding_v2_preparation/package-tree.json.sha256",
    }
    assert set(changed) == expected
