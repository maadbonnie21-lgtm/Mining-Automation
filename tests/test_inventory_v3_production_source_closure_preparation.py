from __future__ import annotations

import ast
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "validation/inventory_v3_production_source_closure_preparation"
CONTRACT = PKG / "contract.json"
PARENT = "519059e910bd196a9fdbeb8dcf5334c6ef74742c"
P0 = "6c675125cdfa1cd91763f2e8df07cb2faae67796"
RUNNER = "src/mining_automation/controlled_mining_runner.py"
INV_PREFIX = "src/mining_automation/perception/inventory/"
PROFILE = INV_PREFIX + "profiles/varrock_east_empty_inventory_v3.bgra"


def _git(*args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(ROOT), *args), check=True,
        capture_output=True, text=True,
    ).stdout.strip()


def _show(ref: str, path: str) -> str:
    return _git("show", f"{ref}:{path}")


def _contract() -> dict[str, object]:
    value = json.loads(CONTRACT.read_text(encoding="ascii"))
    assert isinstance(value, dict)
    return value
def _runtime_inventory_roots() -> set[str]:
    tree = ast.parse(_show(P0, RUNNER))
    function = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_evaluate_packaged_inventory"
    )
    roots: set[str] = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        module = node.module
        if module.startswith("perception.inventory."):
            roots.add(module.rsplit(".", 1)[-1])
    return roots


def _module_dependencies(name: str) -> set[str]:
    source = _show(P0, f"{INV_PREFIX}{name}.py")
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        if node.level == 1:
            candidate = node.module.split(".", 1)[0]
        elif node.module.startswith("mining_automation.perception.inventory."):
            candidate = node.module.rsplit(".", 1)[-1]
        else:
            continue
        try:
            _show(P0, f"{INV_PREFIX}{candidate}.py")
        except subprocess.CalledProcessError:
            continue
        found.add(candidate)
    return found
def _transitive_closure(roots: set[str]) -> set[str]:
    seen: set[str] = set()
    pending = sorted(roots)
    while pending:
        name = pending.pop(0)
        if name in seen:
            continue
        seen.add(name)
        for child in sorted(_module_dependencies(name)):
            if child not in seen:
                pending.append(child)
    return seen


def test_contract_matches_exact_runtime_inventory_source_closure() -> None:
    contract = _contract()
    roots = _runtime_inventory_roots()
    assert roots == {
        "adapter", "configuration", "geometry", "localization",
        "positive_classifier_v3", "positive_v3_prototypes", "retained_iron",
    }
    assert contract["direct_inventory_module_roots"] == sorted(roots)
    closure = _transitive_closure(roots)
    assert closure == {
        "adapter", "classification", "configuration", "detector", "geometry",
        "localization", "positive_classifier_v2", "positive_classifier_v3",
        "positive_v3_prototypes", "retained_iron",
    }
    assert contract["inventory_package_transitive_modules"] == sorted(closure)
def test_contract_pins_every_closure_blob_at_exact_p0_build() -> None:
    contract = _contract()
    entries = contract["source_git_blobs"]
    assert isinstance(entries, list)
    normalized = {entry["path"]: entry["git_blob"] for entry in entries}
    expected_paths = {RUNNER, PROFILE}
    expected_paths.update(
        f"{INV_PREFIX}{name}.py"
        for name in contract["inventory_package_transitive_modules"]
    )
    assert set(normalized) == expected_paths
    assert list(normalized) == sorted(normalized)
    for path, expected_blob in normalized.items():
        assert _git("rev-parse", f"{P0}:{path}") == expected_blob


def test_contract_is_hash_bound_fail_closed_and_non_authoritative() -> None:
    raw = CONTRACT.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    assert (PKG / "contract.json.sha256").read_text(encoding="ascii") == (
        f"{digest}  contract.json\n"
    )
    contract = _contract()
    assert contract["parent_c8b_head_sha"] == PARENT
    assert contract["production_build_git_commit_sha"] == P0
    assert set(contract["authority"].values()) == {False}
    assert contract["inventory_invariants"] == {
        "capacity": 28,
        "publication_floor": 0.8,
        "unknown_can_grant_action_readiness": False,
        "unknown_fail_closed": True,
    }
def test_source_closure_child_is_additive_over_frozen_c8b() -> None:
    head = _git("rev-parse", "HEAD")
    subprocess.run(
        ("git", "-C", str(ROOT), "merge-base", "--is-ancestor", PARENT, head),
        check=True,
    )
    changed = set(_git("diff", "--name-only", PARENT, head).splitlines())
    assert changed == {
        "tests/test_inventory_v3_production_binding_v2_preparation.py",
        "tests/test_inventory_v3_production_source_closure_preparation.py",
        "validation/inventory_v3_production_source_closure_preparation/contract.json",
        "validation/inventory_v3_production_source_closure_preparation/contract.json.sha256",
    }
    assert not any(path.startswith(("src/", "tools/", ".github/")) for path in changed)
