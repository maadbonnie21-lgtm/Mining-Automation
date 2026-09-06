from __future__ import annotations

import ast
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "validation/inventory_v3_production_shared_dependency_preparation"
CONTRACT = PKG / "contract.json"
PARENT = "c554b55023cbf10f8fd97bcd5f1fc5f0a40292ee"
P0 = "6c675125cdfa1cd91763f2e8df07cb2faae67796"
RUNNER = "src/mining_automation/controlled_mining_runner.py"
INV_PREFIX = "src/mining_automation/perception/inventory/"
INVENTORY_MODULES = (
    "adapter", "classification", "configuration", "detector", "geometry",
    "localization", "positive_classifier_v2", "positive_classifier_v3",
    "positive_v3_prototypes", "retained_iron",
)


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


def _from_imports(path: str) -> set[tuple[int, str | None, tuple[str, ...]]]:
    tree = ast.parse(_show(P0, path))
    return {
        (node.level, node.module, tuple(alias.name for alias in node.names))
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }


def test_shared_dependency_contract_matches_exact_p0_sources() -> None:
    contract = _contract()
    assert contract["parent_source_closure_head_sha"] == PARENT
    assert contract["production_build_git_commit_sha"] == P0
    entries = contract["decision_sensitive_shared_sources"]
    assert isinstance(entries, list)
    normalized = {entry["path"]: entry["git_blob"] for entry in entries}
    expected = {
        "src/mining_automation/capture/__init__.py",
        "src/mining_automation/capture/frame.py",
        "src/mining_automation/contracts.py",
        "src/mining_automation/mining_slice.py",
        "src/mining_automation/perception/detector.py",
        "src/mining_automation/perception/errors.py",
    }
    assert set(normalized) == expected
    assert list(normalized) == sorted(normalized)
    for path, blob in normalized.items():
        assert _git("rev-parse", f"{P0}:{path}") == blob


def test_runner_inventory_decision_uses_shared_frame_state_and_floor_contracts() -> None:
    imports = _from_imports(RUNNER)
    assert (1, "capture", ("Frame", "PixelFormat")) in imports
    assert (1, "contracts", ("InventoryState",)) in imports
    mining_slice = next(
        names for level, module, names in imports
        if level == 1 and module == "mining_slice"
    )
    assert "INVENTORY_CAPACITY" in mining_slice
    assert "INVENTORY_PUBLICATION_FLOOR" in mining_slice

    tree = ast.parse(_show(P0, RUNNER))
    function = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_evaluate_packaged_inventory"
    )
    function_imports = {
        (node.level, node.module, tuple(alias.name for alias in node.names))
        for node in ast.walk(function)
        if isinstance(node, ast.ImportFrom)
    }
    assert (1, "capture", ("RawFrame",)) in function_imports


def test_inventory_modules_reach_only_the_reviewed_shared_runtime_interfaces() -> None:
    found: set[tuple[int, str | None]] = set()
    for module in INVENTORY_MODULES:
        for level, imported_module, _ in _from_imports(INV_PREFIX + module + ".py"):
            if level >= 2:
                found.add((level, imported_module))
    assert found == {
        (2, "detector"),
        (2, "errors"),
        (3, "capture"),
        (3, "contracts"),
    }


def test_contract_is_hash_bound_fail_closed_and_non_authoritative() -> None:
    raw = CONTRACT.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    assert (PKG / "contract.json.sha256").read_text(encoding="ascii") == (
        f"{digest}  contract.json\n"
    )
    contract = _contract()
    assert set(contract["authority"].values()) == {False}
    assert contract["inventory_invariants"] == {
        "capacity": 28,
        "publication_floor": 0.8,
        "unknown_fail_closed": True,
    }


def test_shared_dependency_child_is_additive_over_frozen_source_closure() -> None:
    head = _git("rev-parse", "HEAD")
    subprocess.run(
        ("git", "-C", str(ROOT), "merge-base", "--is-ancestor", PARENT, head),
        check=True,
    )
    changed = set(_git("diff", "--name-only", PARENT, head).splitlines())
    assert changed == {
        "tests/test_inventory_v3_production_source_closure_preparation.py",
        "tests/test_inventory_v3_production_shared_dependency_preparation.py",
        "validation/inventory_v3_production_shared_dependency_preparation/contract.json",
        "validation/inventory_v3_production_shared_dependency_preparation/contract.json.sha256",
    }
    assert not any(
        path.startswith(("src/", "tools/", ".github/")) for path in changed
    )
