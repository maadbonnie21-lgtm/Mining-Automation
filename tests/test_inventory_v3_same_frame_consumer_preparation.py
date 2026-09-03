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

PACKAGE_ROOT = ROOT / "validation" / "inventory_v3_same_frame_consumer_preparation"
CONTRACT_PATH = PACKAGE_ROOT / "consumer-contract.json"
SIDECAR_PATH = PACKAGE_ROOT / "consumer-contract.json.sha256"

CORRECTED_C5 = "7a4529e6ce34494ddd53c76882e0fbb8a76bfb4a"
FROZEN_C4 = "74e2becd41af6b63b230ff11b07536d5da61aa80"
REJECTED_C5 = "2aad6ff304d8af20ea360e43cfcd56a54910814e"
PUBLICATION_FLOOR = 0.8

EXPECTED_CHANGED_PATHS = {
    "tests/test_inventory_v3_same_frame_consumer_preparation.py",
    "validation/inventory_v3_same_frame_consumer_preparation/consumer-contract.json",
    "validation/inventory_v3_same_frame_consumer_preparation/consumer-contract.json.sha256",
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


def test_c7_contract_is_canonical_hash_bound_and_currently_non_authoritative() -> None:
    raw = CONTRACT_PATH.read_bytes()
    contract = _contract()
    assert raw == _canonical_json_bytes(contract)

    digest = hashlib.sha256(raw).hexdigest()
    assert SIDECAR_PATH.read_text(encoding="ascii") == (
        f"{digest}  consumer-contract.json\n"
    )

    assert contract["schema"] == "inventory-v3-same-frame-consumer-preparation-v1"
    assert contract["current_status"] == "PREPARATION_ONLY_NO_RUNTIME_CONSUMER"
    assert contract["inventory_publication_floor"] == PUBLICATION_FLOOR

    lineage = _mapping(contract["lineage"])
    assert lineage == {
        "c5_corrected_head_sha": CORRECTED_C5,
        "frozen_c4_head_sha": FROZEN_C4,
        "future_receipt_not_yet_issued": True,
        "rejected_c5_audit_head_sha": REJECTED_C5,
    }

    authority = _mapping(contract["authority"])
    assert authority
    assert set(authority.values()) == {False}

    boundary = _mapping(contract["preparation_boundary"])
    assert boundary
    assert set(boundary.values()) == {False}


def test_c7_freezes_same_frame_inventory_publication_contract() -> None:
    contract = _contract()
    future_input = _mapping(contract["future_consumer_input_contract"])
    cycle = _mapping(future_input["cycle"])
    frame = _mapping(future_input["frame"])
    observation = _mapping(future_input["inventory_observation"])
    provenance = _mapping(future_input["provenance"])
    receipt = _mapping(future_input["release_receipt"])

    assert cycle == {
        "cycle_id": "required-nonempty-source-owned-identity",
        "single_owned_cycle_required": True,
    }
    assert frame["frame_id"] == "required-positive-integer"
    assert frame["strictly_current_for_cycle"] is True
    assert frame["captured_monotonic_s"] == "required-finite-nonnegative"
    assert observation["capacity"] == 28
    assert observation["occupied_slots"] == "integer-0-to-28-or-null"
    assert observation["reason"] == "null-for-definitive-nonempty-for-unknown"
    assert provenance == {
        "capture_source_identity": "required-source-owned",
        "frame_content_sha256": "required-lowercase-64hex",
        "repository_head_sha": "required-lowercase-40hex",
    }
    assert receipt == {
        "receipt_id": "inventory-release-receipt:inventory-positive-v3@1.0.0",
        "required": True,
        "source_owned_immutable_singleton_required": True,
    }

    output = _mapping(contract["future_output_contract"])
    definitive = _mapping(output["definitive"])
    unknown = _mapping(output["unknown"])
    assert definitive == {
        "capacity": 28,
        "confidence_at_or_above": PUBLICATION_FLOOR,
        "occupied_slots": "integer-0-to-28",
        "reason": None,
        "requires_exact_current_frame_cycle_provenance": True,
        "requires_exact_released_inventory_receipt": True,
    }
    assert unknown == {
        "occupied_slots": None,
        "reason": "required-nonempty",
        "world_state_publication_allowed": False,
    }


def test_c7_unknown_stale_mixed_or_unreleased_inputs_remain_fail_closed() -> None:
    rules = _mapping(_contract()["fail_closed_rules"])
    assert rules == {
        "confidence_below_publication_floor": "UNKNOWN",
        "missing_or_invalid_receipt": "UNKNOWN",
        "mixed_frame_cycle_or_provenance": "UNKNOWN",
        "stale_observation": "UNKNOWN",
        "unknown_occupied_slots": None,
        "unknown_requires_reason": True,
        "unknown_yields_action_authority": False,
    }


def test_c7_does_not_add_a_runtime_consumer_or_receipt_surface() -> None:
    assert not (PACKAGE_ROOT / "__init__.py").exists()
    assert not tuple(PACKAGE_ROOT.glob("*.py"))
    assert importlib.util.find_spec(
        "mining_automation.perception.inventory.same_frame_consumer"
    ) is None
    assert not (
        ROOT
        / "src"
        / "mining_automation"
        / "perception"
        / "inventory"
        / "same_frame_consumer.py"
    ).exists()


def test_c7_preserves_frozen_inventory_repository_verifiers() -> None:
    head = _git("rev-parse", "HEAD")
    capture._verify_capture_repository(ROOT)
    assert v1_validation._verify_repository_state(ROOT, head) == ROOT.resolve()
    binding = verify_protocol_v2_repository(ROOT, expected_head=head)
    assert binding.source_commit_sha == "0aa2647cd3382f217212377c7218848c3f322739"
    assert binding.lock_commit_sha == "66c7e9536539979bc60e17f02f026eb64ebf0768"


def test_c7_is_one_clean_child_of_exact_green_c5() -> None:
    head = _git("rev-parse", "HEAD")
    introduction_commits = _git(
        "log",
        "--full-history",
        "--diff-filter=A",
        "--format=%H",
        head,
        "--",
        "validation/inventory_v3_same_frame_consumer_preparation/consumer-contract.json",
    ).splitlines()
    assert len(introduction_commits) == 1
    introduction = introduction_commits[0]

    assert _git("show", "-s", "--format=%P", introduction) == CORRECTED_C5
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
