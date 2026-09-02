from __future__ import annotations

import ast
import copy
import dataclasses
import hashlib
import inspect
import json
import pickle
from pathlib import Path

import pytest

import mining_automation.perception.inventory as inventory_package
import mining_automation.perception.inventory.inventory_release_receipt as receipt_module
from mining_automation.perception.inventory.inventory_release_receipt import (
    INVENTORY_RELEASE_RECEIPT_SCHEMA_VERSION,
    InventoryReleaseGate,
    InventoryReleaseReceipt,
    InventoryReleaseReceiptUnavailable,
    load_source_owned_inventory_release_receipt,
    require_source_owned_inventory_release_receipt,
)

_ROOT = Path(__file__).resolve().parents[1]
_SHA = {letter: letter * 64 for letter in "abcdef"}
_GIT = {number: str(number) * 40 for number in range(1, 10)}
_IDS = (
    "inventory-v3-authorization-001",
    "inventory-v3-campaign-001",
    "inventory-v3-dataset-001",
    "inventory-v3-session-001",
)


def _root(section: dict[str, object], field: str) -> None:
    section[field] = receipt_module._mapping_sha256(
        {key: value for key, value in section.items() if key != field}
    )


def _stage(
    name: str,
    *,
    actor: str,
    commit: str,
    evidence: dict[str, object],
) -> dict[str, object]:
    stage: dict[str, object] = {
        "status": receipt_module._STAGE_STATUS[name],
        "authorization_id": _IDS[0],
        "campaign_id": _IDS[1],
        "dataset_id": _IDS[2],
        "session_id": _IDS[3],
        "actor_id": actor,
        "git_commit_sha": commit,
        "evidence": evidence,
        "stage_root_sha256": "",
    }
    _root(stage, "stage_root_sha256")
    return stage


def _future_record() -> dict[str, object]:
    """Synthetic shape fixture only; it grants no real authority."""

    lineage: dict[str, object] = {
        "protocol_v2_lock_commit_sha": (
            "66c7e9536539979bc60e17f02f026eb64ebf0768"
        ),
        "protocol_v2_lock_sha256": (
            "60ff2c511e46be3b87df4e0d9e4f705d897a4181f9152f2729ee90f6c45f8cf5"
        ),
        "c3_rehearsal_head_sha": "76d47af4213a9990054b3beb5ccb0285e3138b79",
        "c4_preparation_head_sha": "74e2becd41af6b63b230ff11b07536d5da61aa80",
        "live_authorization_parent_sha": (
            "66c7e9536539979bc60e17f02f026eb64ebf0768"
        ),
        "live_authorization_commit_sha": _GIT[1],
        "capture_execution_head_sha": _GIT[1],
        "lineage_root_sha256": "",
    }
    _root(lineage, "lineage_root_sha256")

    contract: dict[str, object] = {
        "capacity": 28,
        "publication_floor": 0.8,
        "wrong_tab_outcome": "UNKNOWN",
        "row_obstruction_outcome": "UNKNOWN",
        "unknown_occupied_slots": None,
        "unknown_reason_preserved": True,
        "unknown_grants_action_authority": False,
        "unknown_grants_bank_transition_authority": False,
        "input_automation_allowed": False,
        "contract_root_sha256": "",
    }
    _root(contract, "contract_root_sha256")

    stages = {
        "authorization": _stage(
            "authorization",
            actor="source-owner",
            commit=_GIT[1],
            evidence={
                "legacy_registry_git_blob": _GIT[2],
                "protocol_v2_registry_git_blob": _GIT[3],
                "protocol_v2_registry_sidecar_git_blob": _GIT[4],
                "opaque_receipt_id": "opaque-authorization-receipt-001",
            },
        ),
        "campaign": _stage(
            "campaign",
            actor="operator-001",
            commit=_GIT[1],
            evidence={
                "campaign_manifest_sha256": _SHA["a"],
                "acquisition_record_sha256": _SHA["b"],
                "acquisition_package_tree_sha256": _SHA["c"],
                "completion_seal_sha256": _SHA["d"],
                "session_report_sha256": _SHA["e"],
                "capture_environment_sha256": _SHA["f"],
                "producer_identity_sha256": _SHA["a"],
            },
        ),
        "review": _stage(
            "review",
            actor="reviewer-001",
            commit=_GIT[2],
            evidence={
                "reviewed_package_tree_sha256": _SHA["b"],
                "reviewer_truth_sha256": _SHA["c"],
                "validation_package_sha256": _SHA["d"],
                "review_submission_sha256": _SHA["e"],
            },
        ),
        "terminal_evaluation": _stage(
            "terminal_evaluation",
            actor="evaluator-001",
            commit=_GIT[3],
            evidence={
                "terminal_result_sha256": _SHA["c"],
                "result_package_tree_sha256": _SHA["d"],
                "frozen_evaluator_report_sha256": _SHA["e"],
                "outcome": "PASS",
                "one_shot_terminal": True,
                "retry_allowed": False,
            },
        ),
        "source_approval": _stage(
            "source_approval",
            actor="source-approver-001",
            commit=_GIT[5],
            evidence={
                "approval_request_sha256": _SHA["d"],
                "approval_registry_sha256": _SHA["e"],
                "approval_registry_git_blob": _GIT[6],
                "approval_registry_sidecar_git_blob": _GIT[7],
                "approval_id": "source-approval-001",
            },
        ),
        "production_identity_approval": _stage(
            "production_identity_approval",
            actor="identity-approver-001",
            commit=_GIT[8],
            evidence={
                "identity_proposal_sha256": _SHA["e"],
                "record_sha256": _SHA["f"],
                "record_sidecar_sha256": _SHA["a"],
                "record_git_blob": _GIT[2],
                "record_sidecar_git_blob": _GIT[3],
                "approval_id": "identity-approval-001",
            },
        ),
        "production_binding": _stage(
            "production_binding",
            actor="source-owner",
            commit=_GIT[9],
            evidence={
                "record_sha256": _SHA["a"],
                "record_sidecar_sha256": _SHA["b"],
                "build_identity_sha256": _SHA["c"],
                "capture_environment_identity_sha256": _SHA["d"],
                "detector_identity_sha256": _SHA["e"],
                "inventory_configuration_identity_sha256": _SHA["f"],
                "observation_adapter_identity_sha256": _SHA["a"],
                "profile_identity_sha256": _SHA["b"],
                "record_git_blob": _GIT[4],
                "record_sidecar_git_blob": _GIT[6],
                "build_git_commit_sha": _GIT[7],
                "binding_id": "production-binding-001",
                "source_approval_id": "source-approval-001",
                "production_identity_approval_id": "identity-approval-001",
            },
        ),
    }

    resource_release: dict[str, object] = {
        "status": "RELEASED",
        "receipt_id": "resource-release-receipt:varrock-east-iron-v1@1.0.0",
        "release_record_sha256": _SHA["c"],
        "source_commit_sha": _GIT[7],
        "source_binding_root_sha256": _SHA["d"],
        "resource_release_root_sha256": "",
    }
    _root(resource_release, "resource_release_root_sha256")

    decision: dict[str, object] = {
        "status": "GRANTED",
        "release_eligible": True,
        "activation_allowed": False,
        "world_state_authority": False,
        "controller_authority": False,
        "input_authority": False,
        "unresolved_condition_ids": [],
        "lead_approval_root_sha256": _SHA["f"],
        "protocol_lineage_root_sha256": lineage["lineage_root_sha256"],
        "inventory_contract_root_sha256": contract["contract_root_sha256"],
        "stage_roots": {
            name: stage["stage_root_sha256"] for name, stage in stages.items()
        },
        "resource_release_root_sha256": resource_release[
            "resource_release_root_sha256"
        ],
        "decision_root_sha256": "",
    }
    _root(decision, "decision_root_sha256")

    return {
        "schema_version": 1,
        "receipt_id": "inventory-release-receipt:inventory-positive-v3@1.0.0",
        "release_record_id": "inventory-release-record:inventory-positive-v3@1.0.0",
        "source_owner": "mining-automation-perception-inventory",
        "protocol_lineage": lineage,
        "inventory_contract": contract,
        "stages": stages,
        "resource_release": resource_release,
        "final_decision": decision,
    }


def _digest(record: dict[str, object]) -> str:
    return hashlib.sha256(receipt_module._canonical_record_bytes(record)).hexdigest()


def _configure(
    monkeypatch: pytest.MonkeyPatch,
    record: dict[str, object] | None = None,
) -> InventoryReleaseReceipt:
    configured = _future_record() if record is None else record
    monkeypatch.setattr(receipt_module, "_OPEN_INVENTORY_RELEASE_GATES", ())
    monkeypatch.setattr(receipt_module, "_RECEIPT_ISSUANCE_ALLOWED", True)
    monkeypatch.setattr(receipt_module, "_SOURCE_OWNED_RELEASE_RECORD", configured)
    monkeypatch.setattr(receipt_module, "_APPROVED_RELEASE_RECORD_SHA256", _digest(configured))
    result = receipt_module._configured_source_receipt()
    assert result is not None
    return result


def _reroot_record(record: dict[str, object]) -> None:
    lineage = record["protocol_lineage"]
    contract = record["inventory_contract"]
    stages = record["stages"]
    resource = record["resource_release"]
    decision = record["final_decision"]
    assert isinstance(lineage, dict)
    assert isinstance(contract, dict)
    assert isinstance(stages, dict)
    assert isinstance(resource, dict)
    assert isinstance(decision, dict)
    _root(lineage, "lineage_root_sha256")
    _root(contract, "contract_root_sha256")
    for stage in stages.values():
        assert isinstance(stage, dict)
        _root(stage, "stage_root_sha256")
    _root(resource, "resource_release_root_sha256")
    decision["protocol_lineage_root_sha256"] = lineage["lineage_root_sha256"]
    decision["inventory_contract_root_sha256"] = contract["contract_root_sha256"]
    decision["stage_roots"] = {
        name: stage["stage_root_sha256"]
        for name, stage in stages.items()
        if isinstance(stage, dict)
    }
    decision["resource_release_root_sha256"] = resource[
        "resource_release_root_sha256"
    ]
    _root(decision, "decision_root_sha256")


def test_current_source_is_deny_only() -> None:
    assert INVENTORY_RELEASE_RECEIPT_SCHEMA_VERSION == 1
    assert receipt_module._OPEN_INVENTORY_RELEASE_GATES == tuple(InventoryReleaseGate)
    assert receipt_module._RECEIPT_ISSUANCE_ALLOWED is False
    assert receipt_module._SOURCE_OWNED_RELEASE_RECORD is None
    assert receipt_module._APPROVED_RELEASE_RECORD_SHA256 is None
    assert receipt_module._SOURCE_OWNED_RECEIPT_SINGLETON is None
    assert receipt_module._configured_source_receipt() is None


def test_loader_is_no_argument_and_currently_unavailable() -> None:
    assert list(inspect.signature(load_source_owned_inventory_release_receipt).parameters) == []
    with pytest.raises(InventoryReleaseReceiptUnavailable, match="gates remain open"):
        load_source_owned_inventory_release_receipt()
    with pytest.raises(InventoryReleaseReceiptUnavailable, match="gates remain open"):
        require_source_owned_inventory_release_receipt(object())


def test_receipt_is_sealed_nonexported_and_has_no_action_surface() -> None:
    with pytest.raises(TypeError, match="no public constructor"):
        InventoryReleaseReceipt()
    with pytest.raises(TypeError, match="sealed"):

        class ForgedReceipt(InventoryReleaseReceipt):
            pass

    assert not hasattr(inventory_package, "InventoryReleaseReceipt")
    forbidden = {
        "inventory_state",
        "occupied_slots",
        "interaction_regions",
        "actionable_target_ids",
        "activation_allowed",
        "world_state_authority",
        "controller_authority",
        "input_authority",
        "click_authorized",
        "to_world_state",
        "to_action",
    }
    assert forbidden.isdisjoint(dir(InventoryReleaseReceipt))


@pytest.mark.parametrize("gate", tuple(InventoryReleaseGate))
def test_every_open_gate_independently_blocks_issuance(
    monkeypatch: pytest.MonkeyPatch,
    gate: InventoryReleaseGate,
) -> None:
    record = _future_record()
    monkeypatch.setattr(receipt_module, "_OPEN_INVENTORY_RELEASE_GATES", (gate,))
    monkeypatch.setattr(receipt_module, "_RECEIPT_ISSUANCE_ALLOWED", True)
    monkeypatch.setattr(receipt_module, "_SOURCE_OWNED_RELEASE_RECORD", record)
    monkeypatch.setattr(receipt_module, "_APPROVED_RELEASE_RECORD_SHA256", _digest(record))
    with pytest.raises(InventoryReleaseReceiptUnavailable, match="remain open"):
        receipt_module._configured_source_receipt()


def test_empty_gates_or_record_alone_cannot_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(receipt_module, "_OPEN_INVENTORY_RELEASE_GATES", ())
    with pytest.raises(InventoryReleaseReceiptUnavailable, match="issuance is disabled"):
        receipt_module._configured_source_receipt()

    record = _future_record()
    monkeypatch.setattr(
        receipt_module,
        "_OPEN_INVENTORY_RELEASE_GATES",
        tuple(InventoryReleaseGate),
    )
    monkeypatch.setattr(receipt_module, "_SOURCE_OWNED_RELEASE_RECORD", record)
    monkeypatch.setattr(receipt_module, "_APPROVED_RELEASE_RECORD_SHA256", _digest(record))
    with pytest.raises(InventoryReleaseReceiptUnavailable, match="remain open"):
        receipt_module._configured_source_receipt()


def test_future_complete_record_builds_one_immutable_metadata_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _future_record()
    result = _configure(monkeypatch, record)
    assert result.receipt_id == record["receipt_id"]
    assert result.release_record_sha256 == _digest(record)
    assert result.protocol_v2_lock_commit_sha == (
        "66c7e9536539979bc60e17f02f026eb64ebf0768"
    )
    assert result.live_authorization_commit_sha == _GIT[1]
    assert (
        result.authorization_id,
        result.campaign_id,
        result.dataset_id,
        result.session_id,
    ) == _IDS
    assert tuple(name for name, _ in result.stage_roots) == receipt_module._STAGE_ORDER
    assert copy.copy(result) is result
    assert copy.deepcopy(result) is result
    for protocol in range(pickle.HIGHEST_PROTOCOL + 1):
        with pytest.raises(TypeError, match="cannot be serialized"):
            pickle.dumps(result, protocol=protocol)
    with pytest.raises(AttributeError):
        result.receipt_id = "changed"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        object.__setattr__(result, "receipt_id", "changed")
    with pytest.raises(TypeError):
        dataclasses.replace(result)


def test_consumer_requires_exact_import_time_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issued = _configure(monkeypatch)
    load_receipt, require_receipt = receipt_module._receipt_accessors(issued, ())
    assert load_receipt() is issued
    assert require_receipt(issued) is issued

    class DuckReceipt:
        receipt_id = issued.receipt_id

    with pytest.raises(InventoryReleaseReceiptUnavailable, match="not the source-owned"):
        require_receipt(DuckReceipt())
    forged = tuple.__new__(InventoryReleaseReceipt, tuple(issued))
    with pytest.raises(InventoryReleaseReceiptUnavailable, match="not the source-owned"):
        require_receipt(forged)


def test_runtime_rebinding_cannot_replace_closure_or_public_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issued = _configure(monkeypatch)
    load_receipt, require_receipt = receipt_module._receipt_accessors(issued, ())
    forged = tuple.__new__(InventoryReleaseReceipt, tuple(issued))
    monkeypatch.setattr(
        receipt_module,
        "_OPEN_INVENTORY_RELEASE_GATES",
        tuple(InventoryReleaseGate),
    )
    monkeypatch.setattr(receipt_module, "_RECEIPT_ISSUANCE_ALLOWED", False)
    monkeypatch.setattr(receipt_module, "_SOURCE_OWNED_RELEASE_RECORD", None)
    monkeypatch.setattr(receipt_module, "_APPROVED_RELEASE_RECORD_SHA256", None)
    monkeypatch.setattr(receipt_module, "_SOURCE_OWNED_RECEIPT_SINGLETON", forged)
    assert load_receipt() is issued
    assert require_receipt(issued) is issued
    with pytest.raises(InventoryReleaseReceiptUnavailable, match="not the source-owned"):
        require_receipt(forged)
    with pytest.raises(InventoryReleaseReceiptUnavailable, match="gates remain open"):
        load_source_owned_inventory_release_receipt()


def test_record_snapshot_ignores_mutation_after_serialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _future_record()
    expected_payload = receipt_module._canonical_record_bytes(record)
    real_canonical = receipt_module._canonical_record_bytes

    def serialize_then_mutate(value: dict[str, object]) -> bytes:
        payload = real_canonical(value)
        if value is record:
            stages = record["stages"]
            assert isinstance(stages, dict)
            campaign = stages["campaign"]
            assert isinstance(campaign, dict)
            campaign["campaign_id"] = "mutated-after-snapshot"
        return payload

    monkeypatch.setattr(receipt_module, "_OPEN_INVENTORY_RELEASE_GATES", ())
    monkeypatch.setattr(receipt_module, "_RECEIPT_ISSUANCE_ALLOWED", True)
    monkeypatch.setattr(receipt_module, "_SOURCE_OWNED_RELEASE_RECORD", record)
    monkeypatch.setattr(
        receipt_module,
        "_APPROVED_RELEASE_RECORD_SHA256",
        hashlib.sha256(expected_payload).hexdigest(),
    )
    monkeypatch.setattr(receipt_module, "_canonical_record_bytes", serialize_then_mutate)
    result = receipt_module._configured_source_receipt()
    assert result is not None
    assert result.campaign_id == _IDS[1]
    assert json.loads(expected_payload)["stages"] != record["stages"]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("authorization-is-lock", "future source-owned"),
        ("capture-head-crossed", "crossed release lineages"),
        ("capacity-bool", "production contract"),
        ("floor-int", "production contract"),
        ("unknown-count", "production contract"),
        ("unknown-reason-lost", "UNKNOWN reason retention"),
        ("campaign-id-crossed", "crossed release lineages"),
        ("reviewer-is-operator", "pairwise distinct"),
        ("terminal-fail", "terminal evaluator outcome"),
        ("terminal-retry", "terminal retry policy"),
        ("binding-source-approval-crossed", "crossed release lineages"),
        ("approval-commit-reused", "must be distinct"),
        ("resource-not-released", "resource release status"),
        ("release-eligible-false", "release eligibility"),
        ("activation-true", "activation_allowed"),
        ("unresolved-remains", "retains unresolved"),
        ("decision-stage-root-crossed", "crossed release lineages"),
    ],
)
def test_record_rejects_incomplete_mixed_replayed_or_foreign_state(
    mutation: str,
    message: str,
) -> None:
    record = _future_record()
    lineage = record["protocol_lineage"]
    contract = record["inventory_contract"]
    stages = record["stages"]
    resource = record["resource_release"]
    decision = record["final_decision"]
    assert isinstance(lineage, dict)
    assert isinstance(contract, dict)
    assert isinstance(stages, dict)
    assert isinstance(resource, dict)
    assert isinstance(decision, dict)

    if mutation == "authorization-is-lock":
        lineage["live_authorization_commit_sha"] = lineage["protocol_v2_lock_commit_sha"]
        lineage["capture_execution_head_sha"] = lineage["protocol_v2_lock_commit_sha"]
        authorization = stages["authorization"]
        campaign = stages["campaign"]
        assert isinstance(authorization, dict)
        assert isinstance(campaign, dict)
        authorization["git_commit_sha"] = lineage["protocol_v2_lock_commit_sha"]
        campaign["git_commit_sha"] = lineage["protocol_v2_lock_commit_sha"]
    elif mutation == "capture-head-crossed":
        lineage["capture_execution_head_sha"] = _GIT[2]
    elif mutation == "capacity-bool":
        contract["capacity"] = True
    elif mutation == "floor-int":
        contract["publication_floor"] = 1
    elif mutation == "unknown-count":
        contract["unknown_occupied_slots"] = 0
    elif mutation == "unknown-reason-lost":
        contract["unknown_reason_preserved"] = False
    elif mutation == "campaign-id-crossed":
        campaign = stages["campaign"]
        assert isinstance(campaign, dict)
        campaign["campaign_id"] = "foreign"
    elif mutation == "reviewer-is-operator":
        review = stages["review"]
        assert isinstance(review, dict)
        review["actor_id"] = "operator-001"
    elif mutation == "terminal-fail":
        evaluation = stages["terminal_evaluation"]
        assert isinstance(evaluation, dict)
        evidence = evaluation["evidence"]
        assert isinstance(evidence, dict)
        evidence["outcome"] = "FAIL"
    elif mutation == "terminal-retry":
        evaluation = stages["terminal_evaluation"]
        assert isinstance(evaluation, dict)
        evidence = evaluation["evidence"]
        assert isinstance(evidence, dict)
        evidence["retry_allowed"] = True
    elif mutation == "binding-source-approval-crossed":
        binding = stages["production_binding"]
        assert isinstance(binding, dict)
        evidence = binding["evidence"]
        assert isinstance(evidence, dict)
        evidence["source_approval_id"] = "foreign"
    elif mutation == "approval-commit-reused":
        approval = stages["source_approval"]
        assert isinstance(approval, dict)
        approval["git_commit_sha"] = _GIT[1]
    elif mutation == "resource-not-released":
        resource["status"] = "PENDING"
    elif mutation == "release-eligible-false":
        decision["release_eligible"] = False
    elif mutation == "activation-true":
        decision["activation_allowed"] = True
    elif mutation == "unresolved-remains":
        decision["unresolved_condition_ids"] = ["campaign"]
    elif mutation == "decision-stage-root-crossed":
        stage_roots = decision["stage_roots"]
        assert isinstance(stage_roots, dict)
        stage_roots["campaign"] = _SHA["a"]
    else:
        raise AssertionError(mutation)

    _reroot_record(record)
    if mutation == "decision-stage-root-crossed":
        stage_roots = decision["stage_roots"]
        assert isinstance(stage_roots, dict)
        stage_roots["campaign"] = _SHA["a"]
        _root(decision, "decision_root_sha256")
    with pytest.raises(InventoryReleaseReceiptUnavailable, match=message):
        receipt_module._validate_source_owned_record(record)


@pytest.mark.parametrize(
    ("section", "root_field"),
    [
        ("protocol_lineage", "lineage_root_sha256"),
        ("inventory_contract", "contract_root_sha256"),
        ("resource_release", "resource_release_root_sha256"),
        ("final_decision", "decision_root_sha256"),
    ],
)
def test_top_level_section_roots_reject_tampering(section: str, root_field: str) -> None:
    record = _future_record()
    value = record[section]
    assert isinstance(value, dict)
    value[root_field] = "0" * 64
    with pytest.raises(InventoryReleaseReceiptUnavailable, match="root"):
        receipt_module._validate_source_owned_record(record)


@pytest.mark.parametrize("stage_name", receipt_module._STAGE_ORDER)
def test_every_stage_root_rejects_tampering(stage_name: str) -> None:
    record = _future_record()
    stages = record["stages"]
    assert isinstance(stages, dict)
    stage = stages[stage_name]
    assert isinstance(stage, dict)
    stage["stage_root_sha256"] = "0" * 64
    with pytest.raises(InventoryReleaseReceiptUnavailable, match="root"):
        receipt_module._validate_source_owned_record(record)


def test_record_rejects_missing_extra_wrong_digest_and_hostile_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = _future_record()
    del missing["resource_release"]
    with pytest.raises(InventoryReleaseReceiptUnavailable, match="fields changed"):
        receipt_module._validate_source_owned_record(missing)

    extra = _future_record()
    extra["caller_approved"] = True
    with pytest.raises(InventoryReleaseReceiptUnavailable, match="fields changed"):
        receipt_module._validate_source_owned_record(extra)

    record = _future_record()
    monkeypatch.setattr(receipt_module, "_OPEN_INVENTORY_RELEASE_GATES", ())
    monkeypatch.setattr(receipt_module, "_RECEIPT_ISSUANCE_ALLOWED", True)
    monkeypatch.setattr(receipt_module, "_SOURCE_OWNED_RELEASE_RECORD", record)
    monkeypatch.setattr(receipt_module, "_APPROVED_RELEASE_RECORD_SHA256", "0" * 64)
    with pytest.raises(InventoryReleaseReceiptUnavailable, match="digest changed"):
        receipt_module._configured_source_receipt()

    class HostileString(str):
        def __eq__(self, other: object) -> bool:
            del other
            raise AssertionError("hostile equality must not run")

    hostile = _future_record()
    hostile["receipt_id"] = HostileString(
        "inventory-release-receipt:inventory-positive-v3@1.0.0"
    )
    with pytest.raises(InventoryReleaseReceiptUnavailable, match="receipt ID changed"):
        receipt_module._validate_source_owned_record(hostile)


def test_module_has_no_runtime_perception_action_or_live_dependency() -> None:
    path = (
        _ROOT
        / "src"
        / "mining_automation"
        / "perception"
        / "inventory"
        / "inventory_release_receipt.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    imports.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    forbidden = {
        "detector",
        "adapter",
        "live_validation",
        "capture",
        "state",
        "controller",
        "navigation",
        "banking",
        "interaction",
        "application",
    }
    assert all(fragment not in imported for imported in imports for fragment in forbidden)


def test_public_api_has_no_approval_deserialization_or_live_seam() -> None:
    public_names = set(receipt_module.__all__)
    assert not any(
        fragment in name.lower()
        for name in public_names
        for fragment in ("approve", "promote", "authorize", "from_json", "from_dict")
    )
    assert list(inspect.signature(load_source_owned_inventory_release_receipt).parameters) == []
    assert list(
        inspect.signature(require_source_owned_inventory_release_receipt).parameters
    ) == ["value"]
