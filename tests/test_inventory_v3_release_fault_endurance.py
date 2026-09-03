from __future__ import annotations

import copy
import hashlib
import json
import math
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "validation" / "inventory_v3_release_fault_endurance"
CONTRACT_PATH = PACKAGE_ROOT / "fault-endurance-contract.json"
SIDECAR_PATH = PACKAGE_ROOT / "fault-endurance-contract.json.sha256"
RECEIPT_CONTRACT_PATH = (
    ROOT
    / "validation"
    / "inventory_v3_release_receipt_preparation"
    / "receipt-contract.json"
)
CONSUMER_CONTRACT_PATH = (
    ROOT
    / "validation"
    / "inventory_v3_same_frame_consumer_preparation"
    / "consumer-contract.json"
)

C7_HEAD = "861613a3830ebfa9249ef8e89f94a0188e03eadb"
CORRECTED_C5 = "7a4529e6ce34494ddd53c76882e0fbb8a76bfb4a"
RECEIPT_CONTRACT_SHA256 = (
    "2f45527c5ea4d74893f9de55b95fbf77efe6cb60ab3a20f879d0655c4e378b35"
)
CONSUMER_CONTRACT_SHA256 = (
    "9d54fd305be4be7a2bfdf09a9fb74c4e8d7d4c2b306e2b5585481d6803f6bda2"
)
PUBLICATION_FLOOR = 0.8
CAPACITY = 28

EXPECTED_CHANGED_PATHS = {
    "tests/test_inventory_v3_release_fault_endurance.py",
    "validation/inventory_v3_release_fault_endurance/fault-endurance-contract.json",
    "validation/inventory_v3_release_fault_endurance/fault-endurance-contract.json.sha256",
}


def _git(*args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(ROOT), *args),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


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


def _load_mapping(path: Path) -> dict[str, object]:
    value = json.loads(path.read_bytes())
    assert type(value) is dict
    return cast(dict[str, object], value)


def _mapping(value: object) -> dict[str, object]:
    assert type(value) is dict
    return cast(dict[str, object], value)


def _sequence(value: object) -> list[object]:
    assert type(value) is list
    return cast(list[object], value)


def _deep_merge(
    base: Mapping[str, object],
    override: Mapping[str, object],
) -> dict[str, object]:
    merged = copy.deepcopy(dict(base))
    for key, value in override.items():
        current = merged.get(key)
        if type(current) is dict and type(value) is dict:
            merged[key] = _deep_merge(
                cast(dict[str, object], current),
                cast(dict[str, object], value),
            )
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _false_authority() -> dict[str, bool]:
    return {
        "activation_allowed": False,
        "bank_transition_authority": False,
        "controller_authority": False,
        "input_authority": False,
        "mining_authority": False,
        "world_state_publication_allowed": False,
    }


def _result(
    projection_state: str,
    *,
    occupied_slots: int | None,
    confidence: float | None,
    reason: str | None,
) -> dict[str, object]:
    return {
        "projection_state": projection_state,
        "occupied_slots": occupied_slots,
        "confidence": confidence,
        "reason": reason,
        "authority": _false_authority(),
        "evidence_role": "synthetic-contract-rehearsal-only",
    }


def _unknown(reason: str) -> dict[str, object]:
    assert reason
    return _result(
        "UNKNOWN",
        occupied_slots=None,
        confidence=0.0,
        reason=reason,
    )


def _evaluate_projection(
    candidate: Mapping[str, object],
    *,
    gate_order: tuple[str, ...],
) -> dict[str, object]:
    gates = _mapping(candidate.get("release_gates"))
    if set(gates) != set(gate_order):
        return _unknown("release-gate-set-invalid")
    for gate in gate_order:
        if gates[gate] != "CLOSED":
            return _unknown(f"release-gate-open:{gate}")

    receipt = _mapping(candidate.get("receipt"))
    if (
        receipt.get("present") is not True
        or receipt.get("valid") is not True
        or receipt.get("source_owned_immutable_singleton") is not True
    ):
        return _unknown("missing-or-invalid-receipt")

    observation = _mapping(candidate.get("observation"))
    state = observation.get("detector_state")
    reason = observation.get("reason")
    if state == "UNKNOWN":
        if type(reason) is not str or not reason:
            return _unknown("unknown-reason-required")
        return _unknown(reason)
    if state != "DEFINITIVE":
        return _unknown("invalid-inventory-observation")

    if observation.get("fresh") is not True:
        return _unknown("stale-observation")
    if (
        observation.get("same_cycle") is not True
        or observation.get("same_frame") is not True
        or observation.get("same_provenance") is not True
    ):
        return _unknown("mixed-frame-cycle-or-provenance")
    if observation.get("source_owned_capture") is not True:
        return _unknown("source-not-owned")

    capacity = observation.get("capacity")
    occupied = observation.get("occupied_slots")
    confidence = observation.get("confidence")
    if (
        type(capacity) is not int
        or capacity != CAPACITY
        or type(occupied) is not int
        or not 0 <= occupied <= CAPACITY
        or type(confidence) not in {int, float}
        or not math.isfinite(float(confidence))
        or not 0.0 <= float(confidence) <= 1.0
        or reason is not None
    ):
        return _unknown("invalid-inventory-observation")
    if float(confidence) < PUBLICATION_FLOOR:
        return _unknown("confidence-below-publication-floor")
    return _result(
        "DEFINITIVE",
        occupied_slots=occupied,
        confidence=float(confidence),
        reason=None,
    )


def _validate_contract(contract: Mapping[str, object]) -> None:
    assert contract["schema"] == "inventory-v3-release-fault-endurance-replay-v1"
    assert contract["status"] == "SYNTHETIC_PREPARATION_ONLY_NO_RELEASE_AUTHORITY"
    assert _mapping(contract["authority"]) == _false_authority()

    lineage = _mapping(contract["lineage"])
    assert lineage["c7_same_frame_consumer_head_sha"] == C7_HEAD
    assert lineage["corrected_c5_head_sha"] == CORRECTED_C5
    assert lineage["receipt_contract_sha256"] == RECEIPT_CONTRACT_SHA256
    assert lineage["consumer_contract_sha256"] == CONSUMER_CONTRACT_SHA256
    assert hashlib.sha256(RECEIPT_CONTRACT_PATH.read_bytes()).hexdigest() == (
        RECEIPT_CONTRACT_SHA256
    )
    assert hashlib.sha256(CONSUMER_CONTRACT_PATH.read_bytes()).hexdigest() == (
        CONSUMER_CONTRACT_SHA256
    )

    replay = _mapping(contract["replay_contract"])
    assert replay == {
        "automatic_retry_allowed": False,
        "case_order_fixed": True,
        "failure_retention_required": True,
        "fresh_invocation_recovery_only": True,
        "hypothetical_inputs_cannot_close_real_gates": True,
        "round_count": 3,
        "synthetic_definitive_projection_is_release_evidence": False,
    }

    cases = _sequence(contract["cases"])
    case_ids = []
    for case in cases:
        case_id = _mapping(case)["case_id"]
        assert type(case_id) is str and case_id
        case_ids.append(case_id)
    assert len(case_ids) == len(set(case_ids))


def test_c6_contract_is_canonical_hash_bound_and_source_bound() -> None:
    raw = CONTRACT_PATH.read_bytes()
    contract = _load_mapping(CONTRACT_PATH)
    assert raw == _canonical_json_bytes(contract)
    digest = hashlib.sha256(raw).hexdigest()
    assert SIDECAR_PATH.read_text(encoding="ascii") == (
        f"{digest}  fault-endurance-contract.json\n"
    )
    _validate_contract(contract)


def test_c6_current_state_remains_unreleased_and_non_authoritative() -> None:
    contract = _load_mapping(CONTRACT_PATH)
    current = _mapping(contract["current_release_state"])
    assert current == {
        "all_real_release_gates_open": True,
        "live_protocol_v2_authorized": False,
        "production_binding_complete": False,
        "real_campaign_complete": False,
        "runtime_receipt_issued": False,
        "source_approval_granted": False,
    }

    receipt_contract = _load_mapping(RECEIPT_CONTRACT_PATH)
    assert set(_mapping(receipt_contract["current_gate_state"]).values()) == {"OPEN"}
    assert set(_mapping(receipt_contract["current_authority"]).values()) == {False}

    consumer_contract = _load_mapping(CONSUMER_CONTRACT_PATH)
    assert consumer_contract["current_status"] == "PREPARATION_ONLY_NO_RUNTIME_CONSUMER"
    assert set(_mapping(consumer_contract["authority"]).values()) == {False}


def test_c6_replays_every_case_three_times_with_identical_results() -> None:
    contract = _load_mapping(CONTRACT_PATH)
    baseline = _mapping(contract["baseline_hypothetical_input"])
    receipt_contract = _load_mapping(RECEIPT_CONTRACT_PATH)
    future_record = _mapping(receipt_contract["future_receipt_record_contract"])
    assert cast(list[str], future_record["stage_order"]) == [
        "authorization",
        "campaign",
        "review",
        "terminal_evaluation",
        "source_approval",
        "production_identity_approval",
        "production_binding",
    ]
    gate_order = tuple(cast(list[str], receipt_contract["required_release_gates"]))

    cases = _sequence(contract["cases"])
    round_roots: list[str] = []
    for _ in range(3):
        round_results: list[dict[str, object]] = []
        for raw_case in cases:
            case = _mapping(raw_case)
            candidate = _deep_merge(baseline, _mapping(case["overrides"]))
            result = _evaluate_projection(candidate, gate_order=gate_order)
            assert result == _mapping(case["expected"])
            assert set(_mapping(result["authority"]).values()) == {False}
            round_results.append(
                {
                    "case_id": case["case_id"],
                    "category": case["category"],
                    "result": result,
                }
            )
        round_roots.append(hashlib.sha256(_canonical_json_bytes(round_results)).hexdigest())

    assert len(set(round_roots)) == 1


def test_c6_matrix_covers_nominal_and_each_fail_closed_boundary() -> None:
    contract = _load_mapping(CONTRACT_PATH)
    cases = [_mapping(value) for value in _sequence(contract["cases"])]
    categories = {cast(str, case["category"]) for case in cases}
    assert categories == {
        "authority",
        "freshness",
        "inventory",
        "nominal-projection",
        "provenance",
        "receipt",
        "release-gate",
    }

    receipt_contract = _load_mapping(RECEIPT_CONTRACT_PATH)
    required_gates = set(cast(list[str], receipt_contract["required_release_gates"]))
    covered_open_gates = {
        cast(str, case["case_id"]).removeprefix("open-gate-").replace("-", "_")
        for case in cases
        if case["category"] == "release-gate"
    }
    assert covered_open_gates == required_gates

    expected_states = {
        _mapping(case["expected"])["projection_state"]
        for case in cases
    }
    assert expected_states == {"DEFINITIVE", "UNKNOWN"}
    assert all(
        _mapping(_mapping(case["expected"])["authority"]) == _false_authority()
        for case in cases
    )


def test_c6_rejects_duplicate_case_ids_and_detects_reordering() -> None:
    contract = _load_mapping(CONTRACT_PATH)
    duplicate = copy.deepcopy(contract)
    duplicate_cases = _sequence(duplicate["cases"])
    _mapping(duplicate_cases[1])["case_id"] = _mapping(duplicate_cases[0])["case_id"]
    with pytest.raises(AssertionError):
        _validate_contract(duplicate)

    reordered = copy.deepcopy(contract)
    reordered_cases = _sequence(reordered["cases"])
    reordered_cases[0], reordered_cases[1] = reordered_cases[1], reordered_cases[0]
    assert _canonical_json_bytes(reordered) != CONTRACT_PATH.read_bytes()


def test_c6_has_no_runtime_receipt_consumer_capture_or_input_surface() -> None:
    assert not (PACKAGE_ROOT / "__init__.py").exists()
    assert not tuple(PACKAGE_ROOT.glob("*.py"))
    assert not (
        ROOT
        / "src"
        / "mining_automation"
        / "perception"
        / "inventory"
        / "inventory_release_fault_endurance.py"
    ).exists()
    contract = _load_mapping(CONTRACT_PATH)
    assert set(_mapping(contract["authority"]).values()) == {False}


def test_c6_is_one_clean_child_of_exact_frozen_c7() -> None:
    head = _git("rev-parse", "HEAD")
    introduction_commits = _git(
        "log",
        "--full-history",
        "--diff-filter=A",
        "--format=%H",
        head,
        "--",
        "validation/inventory_v3_release_fault_endurance/fault-endurance-contract.json",
    ).splitlines()
    assert len(introduction_commits) == 1
    introduction = introduction_commits[0]

    assert _git("show", "-s", "--format=%P", introduction) == C7_HEAD
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
