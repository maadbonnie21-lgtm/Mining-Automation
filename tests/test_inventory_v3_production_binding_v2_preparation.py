from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
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

PACKAGE_ROOT = ROOT / "validation" / "inventory_v3_production_binding_v2_preparation"
CONTRACT_PATH = PACKAGE_ROOT / "contract.json"
SIDECAR_PATH = PACKAGE_ROOT / "contract.json.sha256"

FROZEN_L2 = "66c7e9536539979bc60e17f02f026eb64ebf0768"
PROTOCOL_V2_LOCK_SHA256 = "60ff2c511e46be3b87df4e0d9e4f705d897a4181f9152f2729ee90f6c45f8cf5"
FROZEN_C4 = "74e2becd41af6b63b230ff11b07536d5da61aa80"
CORRECTED_C5 = "7a4529e6ce34494ddd53c76882e0fbb8a76bfb4a"
FROZEN_C7 = "861613a3830ebfa9249ef8e89f94a0188e03eadb"
P0_BUILD = "6c675125cdfa1cd91763f2e8df07cb2faae67796"
PUBLICATION_FLOOR = 0.8

EXPECTED_P0_BLOBS = {
    "src/mining_automation/controlled_mining_runner.py": "734d1dd65d1b4882f69bbd30127645a155e8f122",
    "src/mining_automation/perception/inventory/adapter.py": "e0e731df2ca663313eb2414fac0e227cff5ac04e",
    "src/mining_automation/perception/inventory/detector.py": "33d6792edb61b307d90aa435cf729b299caade3a",
    "src/mining_automation/perception/inventory/positive_classifier_v3.py": "18849334bbb34c7b1073820ab1fc2a29223662d2",
    "src/mining_automation/perception/inventory/positive_v3_prototypes.py": "fb357ce3ad477be25fbd1653457f6b367f34ab40",
    "src/mining_automation/perception/inventory/profiles/varrock_east_empty_inventory_v3.bgra": "ff42202d6f6034f1c4abbee71bbb5018336163bc",
    "src/mining_automation/perception/inventory/retained_iron.py": "94f26ead8ebe28a601a37749b323a2f551d9d11a",
}

EXPECTED_CHANGED_PATHS = {
    "tests/test_inventory_v3_production_binding_v2_preparation.py",
    "validation/inventory_v3_production_binding_v2_preparation/contract.json",
    "validation/inventory_v3_production_binding_v2_preparation/contract.json.sha256",
}


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


def _sequence(value: object) -> Sequence[object]:
    assert isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
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


def test_c8b_contract_is_canonical_hash_bound_and_non_authoritative() -> None:
    raw = CONTRACT_PATH.read_bytes()
    contract = _contract()
    assert raw == _canonical_json_bytes(contract)

    digest = hashlib.sha256(raw).hexdigest()
    assert SIDECAR_PATH.read_text(encoding="ascii") == f"{digest}  contract.json\n"

    assert contract["schema"] == "inventory-positive-v3-production-binding-preparation-v2"
    assert contract["status"] == "PREPARATION_ONLY_RELEASE_BLOCKED"

    authority = _mapping(contract["authority"])
    assert authority
    assert set(authority.values()) == {False}

    boundary = _mapping(contract["preparation_boundary"])
    assert boundary
    assert set(boundary.values()) == {False}

    invariants = _mapping(contract["inventory_invariants"])
    assert invariants == {
        "capacity": 28,
        "publication_floor": PUBLICATION_FLOOR,
        "unknown_fail_closed": True,
        "unknown_occupied_slots": None,
    }


def test_c8b_preserves_history_but_separates_source_approval_and_executable_build() -> None:
    contract = _contract()
    lineage = _mapping(contract["lineage"])
    assert lineage == {
        "c4_v1_head_sha": FROZEN_C4,
        "c5_corrected_head_sha": CORRECTED_C5,
        "c7_same_frame_head_sha": FROZEN_C7,
        "frozen_l2_sha": FROZEN_L2,
        "protocol_v2_lock_sha256": PROTOCOL_V2_LOCK_SHA256,
        "v1_history_remains_immutable": True,
    }

    separation = _mapping(contract["identity_separation_contract"])
    assert separation["both_exact_identities_required"] is True
    assert separation["different_git_lineages_allowed"] is True

    source_approval = _mapping(separation["source_approval_git_commit_sha"])
    production_build = _mapping(separation["production_build_git_commit_sha"])
    assert source_approval["must_equal_production_build_git_commit_sha"] is False
    assert source_approval["source_approval_registry_blobs_anchor_here"] is True
    assert production_build["must_equal_source_approval_git_commit_sha"] is False
    assert production_build["source_git_blobs_anchor_here"] is True

    registry = _mapping(contract["source_approval_registry_contract"])
    assert registry == {
        "anchor": "source_approval_git_commit_sha",
        "paths": [
            "validation/inventory-positive-v3/approved-campaigns.json",
            "validation/inventory-positive-v3/approved-campaigns.json.sha256",
        ],
    }

    review = _mapping(contract["production_review_contract"])
    assert review["production_build_may_diverge_from_protocol_v2_validation_lineage"] is True
    assert review["protocol_v2_repository_verification_anchor"] == (
        "source-approval-validation-lineage-not-production-executable-lineage"
    )
    assert review["unreviewed_source_blob_add_or_drop_allowed"] is False


def test_c8b_current_p0_source_anchors_resolve_exactly() -> None:
    current = _mapping(_contract()["current_p0_candidate"])
    assert current["git_commit_sha"] == P0_BUILD
    assert current["role"] == "offline-green-executable-candidate-not-inventory-release-authority"

    entries = list(_sequence(current["source_git_blobs"]))
    normalized: dict[str, str] = {}
    paths: list[str] = []
    for raw_entry in entries:
        entry = _mapping(raw_entry)
        assert set(entry) == {"git_blob", "path"}
        path = str(entry["path"])
        git_blob = str(entry["git_blob"])
        assert path not in normalized
        normalized[path] = git_blob
        paths.append(path)
        assert _git("cat-file", "-t", f"{P0_BUILD}:{path}") == "blob"
        assert _git("rev-parse", f"{P0_BUILD}:{path}") == git_blob

    assert paths == sorted(paths)
    assert normalized == EXPECTED_P0_BLOBS


def test_c8b_denies_the_v1_equal_sha_topology_and_other_release_splices() -> None:
    rules = _mapping(_contract()["fail_closed_release_rules"])
    assert rules == {
        "equal_source_approval_and_production_build_sha": "DENY",
        "inventory_confidence_below_floor": "DENY",
        "missing_production_build_identity": "DENY",
        "missing_source_approval_identity": "DENY",
        "production_blob_mismatch": "DENY",
        "production_build_not_independently_reviewed": "DENY",
        "source_approval_registry_blob_mismatch": "DENY",
        "stale_or_replayed_binding": "DENY",
        "synthetic_evidence_as_real": "DENY",
        "unknown_inventory": "DENY",
    }

    review = _mapping(_contract()["production_review_contract"])
    required = set(str(value) for value in _sequence(review["production_build_requires_invariant_evidence"]))
    assert required == {
        "capacity-28-preserved",
        "inventory-floor-0.8-preserved",
        "retained/full-sprite-positive-recognition-reviewed",
        "same-cycle-consumer-compatible",
        "unknown-remains-fail-closed",
    }


def test_c8b_is_preparation_only_and_adds_no_runtime_surface() -> None:
    assert not (PACKAGE_ROOT / "__init__.py").exists()
    assert not tuple(PACKAGE_ROOT.glob("*.py"))
    assert importlib.util.find_spec(
        "mining_automation.perception.inventory.production_binding_v2"
    ) is None


def test_c8b_preserves_frozen_inventory_repository_verifiers() -> None:
    head = _git("rev-parse", "HEAD")
    capture._verify_capture_repository(ROOT)
    assert v1_validation._verify_repository_state(ROOT, head) == ROOT.resolve()
    binding = verify_protocol_v2_repository(ROOT, expected_head=head)
    assert binding.source_commit_sha == "0aa2647cd3382f217212377c7218848c3f322739"
    assert binding.lock_commit_sha == FROZEN_L2


def test_c8b_is_validation_test_only_descendant_of_frozen_c7() -> None:
    head = _git("rev-parse", "HEAD")
    subprocess.run(
        ("git", "-C", str(ROOT), "merge-base", "--is-ancestor", FROZEN_C7, head),
        check=True,
    )
    changed_paths = set(_git("diff", "--name-only", FROZEN_C7, head).splitlines())
    assert changed_paths == EXPECTED_CHANGED_PATHS
    assert not any(
        path.startswith(("src/", "tools/", ".github/")) for path in changed_paths
    )
