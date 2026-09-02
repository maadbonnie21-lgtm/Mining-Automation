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

import mining_automation.perception as perception
import mining_automation.perception.resource_release_receipt as receipt_module
from mining_automation.perception.resource_release_receipt import (
    RESOURCE_RELEASE_RECEIPT_SCHEMA_VERSION,
    ResourceReleaseGate,
    ResourceReleaseReceipt,
    ResourceReleaseReceiptUnavailable,
    load_source_owned_varrock_east_iron_release_receipt,
    require_source_owned_varrock_east_iron_release_receipt,
)

_ROOT = Path(__file__).resolve().parents[1]
_SHA_A = "a" * 64
_SHA_B = "b" * 64
_SHA_C = "c" * 64
_SHA_D = "d" * 64
_SHA_E = "e" * 64
_SHA_F = "f" * 64
_GIT_A = "1" * 40
_GIT_B = "2" * 40
_GIT_C = "3" * 40
_GIT_D = "4" * 40
_GIT_E = "5" * 40


def _future_granted_record() -> dict[str, object]:
    """Synthetic shape fixture only; it is not real evidence or an approval."""

    record: dict[str, object] = {
        "schema_version": 1,
        "receipt_id": "resource-release-receipt:varrock-east-iron-v1@1.0.0",
        "release_record_id": "resource-release-record:varrock-east-iron-v1@1.0.0",
        "source_owner": "mining-automation-perception",
        "detector": {
            "detector_id": "profiled-resource:varrock-east-iron-v1",
            "detector_version": "2.1.0",
            "profile_id": "varrock-east-iron-v1",
            "profile_schema_version": 3,
            "location_id": "varrock-east-mine",
            "resource_ids": [
                "varrock-east-iron-northwest",
                "varrock-east-iron-southwest",
                "varrock-east-iron-center",
                "varrock-east-iron-northeast",
            ],
        },
        "c1_evidence": {
            "status": "CLOSED",
            "source_owned_campaign": True,
            "independent_reviewer_truth": True,
            "production_conformance_passed": True,
            "review_package_manifest_sha256": _SHA_A,
            "release_summary_sha256": _SHA_B,
            "completion_seal_sha256": _SHA_C,
            "followup_sha256": _SHA_D,
        },
        "retained_failure_replay": {
            "status": "CLOSED",
            "permanent_adoption_complete": True,
            "unresolved_case_ids": [],
            "adoption_manifest_sha256": _SHA_E,
            "promoted_fixture_tree_sha": _GIT_A,
            "evaluator_test_tree_sha": _GIT_B,
            "permanent_replay_root_sha256": "",
        },
        "approved_envelope": {
            "status": "APPROVED",
            "envelope_root_sha256": "",
            "frame_width": 1005,
            "frame_height": 1078,
            "pixel_format": "bgra8888",
            "reported_dpi": 96,
            "window_class": "RuneLite",
            "capture_backend": "windows-runelite",
            "capture_configuration_id": (
                "resource-release-campaign:varrock-east-iron-v1@1.1.0"
            ),
            "renderer_id": "future-reviewed-renderer",
            "automatic_camera_recovery_allowed": False,
            "unsupported_or_uncertain_view_policy": "zero_targets_and_stop",
        },
        "source_bindings": {
            "status": "COMPLETE",
            "source_commit_sha": _GIT_A,
            "source_tree_sha": _GIT_B,
            "source_binding_root_sha256": "",
            "detector_source_blob_sha": _GIT_C,
            "packaged_profile_blob_sha": _GIT_D,
            "reviewed_dataset_manifest_blob_sha": _GIT_E,
        },
        "final_decision": {
            "status": "GRANTED",
            "release_eligible": True,
            "activation_allowed": False,
            "unresolved_condition_ids": [],
            "decision_root_sha256": _SHA_B,
            "lead_approval_root_sha256": _SHA_C,
        },
    }
    replay = record["retained_failure_replay"]
    envelope = record["approved_envelope"]
    bindings = record["source_bindings"]
    assert isinstance(replay, dict)
    assert isinstance(envelope, dict)
    assert isinstance(bindings, dict)
    replay["permanent_replay_root_sha256"] = receipt_module._mapping_sha256(
        {
            "adoption_manifest_sha256": replay["adoption_manifest_sha256"],
            "promoted_fixture_tree_sha": replay["promoted_fixture_tree_sha"],
            "evaluator_test_tree_sha": replay["evaluator_test_tree_sha"],
        }
    )
    envelope["envelope_root_sha256"] = receipt_module._mapping_sha256(
        {
            key: value
            for key, value in envelope.items()
            if key != "envelope_root_sha256"
        }
    )
    bindings["source_binding_root_sha256"] = receipt_module._mapping_sha256(
        {
            key: value
            for key, value in bindings.items()
            if key != "source_binding_root_sha256"
        }
    )
    return record


def _record_digest(record: dict[str, object]) -> str:
    return hashlib.sha256(receipt_module._canonical_record_bytes(record)).hexdigest()


def _configure_complete_source(
    monkeypatch: pytest.MonkeyPatch,
    record: dict[str, object] | None = None,
) -> ResourceReleaseReceipt:
    configured = _future_granted_record() if record is None else record
    monkeypatch.setattr(receipt_module, "_OPEN_RESOURCE_RELEASE_GATES", ())
    monkeypatch.setattr(receipt_module, "_RECEIPT_ISSUANCE_ALLOWED", True)
    monkeypatch.setattr(receipt_module, "_SOURCE_OWNED_RELEASE_RECORD", configured)
    monkeypatch.setattr(
        receipt_module,
        "_APPROVED_RECEIPT_RECORD_SHA256",
        _record_digest(configured),
    )
    result = receipt_module._configured_source_receipt()
    assert result is not None
    return result


def _set_path(record: dict[str, object], path: str, value: object) -> None:
    parts = path.split(".")
    current: dict[str, object] = record
    for part in parts[:-1]:
        nested = current[part]
        assert isinstance(nested, dict)
        current = nested
    current[parts[-1]] = value


def test_current_source_configuration_has_every_gate_open_and_no_record() -> None:
    assert RESOURCE_RELEASE_RECEIPT_SCHEMA_VERSION == 1
    assert receipt_module._OPEN_RESOURCE_RELEASE_GATES == tuple(ResourceReleaseGate)
    assert receipt_module._RECEIPT_ISSUANCE_ALLOWED is False
    assert receipt_module._SOURCE_OWNED_RELEASE_RECORD is None
    assert receipt_module._APPROVED_RECEIPT_RECORD_SHA256 is None
    assert receipt_module._SOURCE_OWNED_RECEIPT_SINGLETON is None
    assert receipt_module._configured_source_receipt() is None


def test_current_loader_is_no_argument_and_always_unavailable() -> None:
    assert list(inspect.signature(load_source_owned_varrock_east_iron_release_receipt).parameters) == []
    with pytest.raises(ResourceReleaseReceiptUnavailable, match="gates remain open"):
        load_source_owned_varrock_east_iron_release_receipt()
    with pytest.raises(ResourceReleaseReceiptUnavailable, match="gates remain open"):
        require_source_owned_varrock_east_iron_release_receipt(object())


def test_receipt_has_no_public_constructor_or_subclass_seam() -> None:
    with pytest.raises(TypeError, match="no public constructor"):
        ResourceReleaseReceipt()
    with pytest.raises(TypeError):
        ResourceReleaseReceipt(approved=True)  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="sealed"):

        class ForgedReceipt(ResourceReleaseReceipt):
            pass


def test_receipt_is_not_package_level_api_and_has_no_action_surface() -> None:
    assert not hasattr(perception, "ResourceReleaseReceipt")
    forbidden = {
        "approved",
        "resources",
        "inventory",
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
    assert forbidden.isdisjoint(dir(ResourceReleaseReceipt))


@pytest.mark.parametrize("gate", tuple(ResourceReleaseGate))
def test_each_individual_open_gate_prevents_receipt_issuance(
    monkeypatch: pytest.MonkeyPatch,
    gate: ResourceReleaseGate,
) -> None:
    record = _future_granted_record()
    monkeypatch.setattr(receipt_module, "_OPEN_RESOURCE_RELEASE_GATES", (gate,))
    monkeypatch.setattr(receipt_module, "_RECEIPT_ISSUANCE_ALLOWED", True)
    monkeypatch.setattr(receipt_module, "_SOURCE_OWNED_RELEASE_RECORD", record)
    monkeypatch.setattr(
        receipt_module,
        "_APPROVED_RECEIPT_RECORD_SHA256",
        _record_digest(record),
    )
    with pytest.raises(ResourceReleaseReceiptUnavailable, match="remain open"):
        receipt_module._configured_source_receipt()


def test_empty_gates_alone_cannot_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(receipt_module, "_OPEN_RESOURCE_RELEASE_GATES", ())
    with pytest.raises(ResourceReleaseReceiptUnavailable, match="issuance is disabled"):
        receipt_module._configured_source_receipt()


def test_record_or_digest_alone_cannot_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _future_granted_record()
    monkeypatch.setattr(receipt_module, "_SOURCE_OWNED_RELEASE_RECORD", record)
    monkeypatch.setattr(
        receipt_module,
        "_APPROVED_RECEIPT_RECORD_SHA256",
        _record_digest(record),
    )
    with pytest.raises(ResourceReleaseReceiptUnavailable, match="remain open"):
        receipt_module._configured_source_receipt()


def test_complete_runtime_rebinding_cannot_replace_import_time_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issued = _configure_complete_source(monkeypatch)
    monkeypatch.setattr(receipt_module, "_SOURCE_OWNED_RECEIPT_SINGLETON", issued)
    monkeypatch.setattr(receipt_module, "_RECEIPT_ISSUER", object())
    with pytest.raises(ResourceReleaseReceiptUnavailable, match="gates remain open"):
        load_source_owned_varrock_east_iron_release_receipt()


def test_future_complete_source_shape_builds_one_narrow_immutable_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _future_granted_record()
    result = _configure_complete_source(monkeypatch, record)

    assert result.receipt_id == record["receipt_id"]
    assert result.release_record_sha256 == _record_digest(record)
    assert result.detector_id == "profiled-resource:varrock-east-iron-v1"
    assert result.detector_version == "2.1.0"
    assert result.profile_id == "varrock-east-iron-v1"
    assert result.profile_schema_version == 3
    assert result.location_id == "varrock-east-mine"
    assert result.resource_ids == (
        "varrock-east-iron-northwest",
        "varrock-east-iron-southwest",
        "varrock-east-iron-center",
        "varrock-east-iron-northeast",
    )
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


def test_exact_singleton_consumer_rejects_duck_and_second_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issued = _configure_complete_source(monkeypatch)
    load_receipt, require_receipt = receipt_module._receipt_accessors(issued, ())

    assert load_receipt() is issued
    assert require_receipt(issued) is issued

    class DuckReceipt:
        receipt_id = issued.receipt_id

    with pytest.raises(ResourceReleaseReceiptUnavailable, match="not the source-owned"):
        require_receipt(DuckReceipt())
    forged = tuple.__new__(ResourceReleaseReceipt, tuple(issued))
    with pytest.raises(ResourceReleaseReceiptUnavailable, match="not the source-owned"):
        require_receipt(forged)


def test_future_accessors_ignore_every_rebound_module_global(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issued = _configure_complete_source(monkeypatch)
    load_receipt, require_receipt = receipt_module._receipt_accessors(issued, ())
    forged = tuple.__new__(ResourceReleaseReceipt, tuple(issued))

    monkeypatch.setattr(
        receipt_module,
        "_OPEN_RESOURCE_RELEASE_GATES",
        tuple(ResourceReleaseGate),
    )
    monkeypatch.setattr(receipt_module, "_RECEIPT_ISSUANCE_ALLOWED", False)
    monkeypatch.setattr(receipt_module, "_SOURCE_OWNED_RELEASE_RECORD", None)
    monkeypatch.setattr(receipt_module, "_APPROVED_RECEIPT_RECORD_SHA256", None)
    monkeypatch.setattr(receipt_module, "_RECEIPT_ISSUER", object())
    monkeypatch.setattr(receipt_module, "_SOURCE_OWNED_RECEIPT_SINGLETON", forged)

    assert load_receipt() is issued
    assert require_receipt(issued) is issued
    with pytest.raises(ResourceReleaseReceiptUnavailable, match="not the source-owned"):
        require_receipt(forged)


def test_record_snapshot_is_immune_to_mutation_after_serialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _future_granted_record()
    expected_payload = receipt_module._canonical_record_bytes(record)
    real_canonical = receipt_module._canonical_record_bytes

    def serialize_then_mutate(value: dict[str, object]) -> bytes:
        payload = real_canonical(value)
        if value is record:
            detector = record["detector"]
            assert isinstance(detector, dict)
            detector["detector_id"] = "mutated-after-snapshot"
        return payload

    monkeypatch.setattr(receipt_module, "_OPEN_RESOURCE_RELEASE_GATES", ())
    monkeypatch.setattr(receipt_module, "_RECEIPT_ISSUANCE_ALLOWED", True)
    monkeypatch.setattr(receipt_module, "_SOURCE_OWNED_RELEASE_RECORD", record)
    monkeypatch.setattr(
        receipt_module,
        "_APPROVED_RECEIPT_RECORD_SHA256",
        hashlib.sha256(expected_payload).hexdigest(),
    )
    monkeypatch.setattr(
        receipt_module,
        "_canonical_record_bytes",
        serialize_then_mutate,
    )

    result = receipt_module._configured_source_receipt()
    assert result is not None
    assert result.detector_id == "profiled-resource:varrock-east-iron-v1"
    assert record["detector"] != json.loads(expected_payload)["detector"]


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        ("schema_version", True),
        ("receipt_id", "foreign-receipt"),
        ("release_record_id", "foreign-release-record"),
        ("source_owner", "caller"),
        ("detector.detector_id", "foreign-detector"),
        ("detector.detector_version", "2.0.0"),
        ("detector.profile_id", "foreign-profile"),
        ("detector.profile_schema_version", True),
        ("detector.location_id", "foreign-location"),
        (
            "detector.resource_ids",
            [
                "varrock-east-iron-northeast",
                "varrock-east-iron-center",
                "varrock-east-iron-southwest",
                "varrock-east-iron-northwest",
            ],
        ),
        ("c1_evidence.status", "OPEN"),
        ("c1_evidence.source_owned_campaign", 1),
        ("c1_evidence.independent_reviewer_truth", False),
        ("c1_evidence.production_conformance_passed", False),
        ("c1_evidence.followup_sha256", "0" * 63),
        ("retained_failure_replay.status", "PENDING"),
        ("retained_failure_replay.permanent_adoption_complete", False),
        ("retained_failure_replay.unresolved_case_ids", ["case-1"]),
        ("retained_failure_replay.permanent_replay_root_sha256", _SHA_D),
        ("approved_envelope.status", "CANDIDATE"),
        ("approved_envelope.envelope_root_sha256", _SHA_D),
        ("approved_envelope.frame_width", 1004),
        ("approved_envelope.frame_height", 1077),
        ("approved_envelope.pixel_format", "rgba8888"),
        ("approved_envelope.reported_dpi", True),
        ("approved_envelope.window_class", ""),
        ("approved_envelope.capture_backend", "test-injected"),
        ("approved_envelope.capture_configuration_id", "caller-config"),
        ("approved_envelope.renderer_id", "  "),
        ("approved_envelope.automatic_camera_recovery_allowed", True),
        ("approved_envelope.unsupported_or_uncertain_view_policy", "retry"),
        ("source_bindings.status", "PENDING"),
        ("source_bindings.source_commit_sha", "0" * 39),
        ("source_bindings.source_binding_root_sha256", _SHA_D),
        ("final_decision.status", "NOT_GRANTED"),
        ("final_decision.release_eligible", 1),
        ("final_decision.activation_allowed", True),
        ("final_decision.unresolved_condition_ids", ["renderer-review"]),
        ("final_decision.lead_approval_root_sha256", "not-a-hash"),
    ],
)
def test_future_record_rejects_every_incomplete_or_foreign_binding(
    path: str,
    replacement: object,
) -> None:
    record = _future_granted_record()
    _set_path(record, path, replacement)
    with pytest.raises(ResourceReleaseReceiptUnavailable):
        receipt_module._validate_source_owned_record(record)


def test_future_record_rejects_missing_and_extra_fields() -> None:
    missing = _future_granted_record()
    del missing["final_decision"]
    with pytest.raises(ResourceReleaseReceiptUnavailable, match="fields changed"):
        receipt_module._validate_source_owned_record(missing)

    extra = _future_granted_record()
    extra["caller_approved"] = True
    with pytest.raises(ResourceReleaseReceiptUnavailable, match="fields changed"):
        receipt_module._validate_source_owned_record(extra)


def test_fixed_string_subclass_is_rejected_without_running_hostile_equality() -> None:
    class HostileString(str):
        def __eq__(self, other: object) -> bool:
            del other
            raise AssertionError("subclass equality must not run")

    record = _future_granted_record()
    record["receipt_id"] = HostileString(
        "resource-release-receipt:varrock-east-iron-v1@1.0.0"
    )
    with pytest.raises(ResourceReleaseReceiptUnavailable, match="receipt ID changed"):
        receipt_module._validate_source_owned_record(record)


def test_rehashed_or_tampered_record_cannot_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _future_granted_record()
    monkeypatch.setattr(receipt_module, "_OPEN_RESOURCE_RELEASE_GATES", ())
    monkeypatch.setattr(receipt_module, "_RECEIPT_ISSUANCE_ALLOWED", True)
    monkeypatch.setattr(receipt_module, "_SOURCE_OWNED_RELEASE_RECORD", record)
    monkeypatch.setattr(receipt_module, "_APPROVED_RECEIPT_RECORD_SHA256", "0" * 64)
    with pytest.raises(ResourceReleaseReceiptUnavailable, match="digest changed"):
        receipt_module._configured_source_receipt()


def test_a3_proposal_or_caller_approval_mapping_cannot_issue() -> None:
    for value in (
        {
            "status": "PROPOSED_NOT_GRANTED",
            "authority": {"release_eligible": False},
        },
        {"approved": True, "release_eligible": True},
    ):
        with pytest.raises(ResourceReleaseReceiptUnavailable, match="fields changed"):
            receipt_module._validate_source_owned_record(value)


def test_receipt_module_has_no_runtime_perception_or_action_dependencies() -> None:
    path = (
        _ROOT
        / "src"
        / "mining_automation"
        / "perception"
        / "resource_release_receipt.py"
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
    forbidden_fragments = {
        "constrained_v1_authority",
        "constrained_v1_same_cycle",
        "resource_release_decision",
        "resource_release_campaign",
        "capture.backend",
        "state",
        "controller",
        "navigation",
        "banking",
        "interaction",
        "application",
    }
    assert all(
        fragment not in imported
        for imported in imports
        for fragment in forbidden_fragments
    )


def test_public_module_has_no_approval_or_deserialization_seam() -> None:
    public_names = set(receipt_module.__all__)
    assert not any(
        fragment in name.lower()
        for name in public_names
        for fragment in ("approve", "promote", "from_json", "from_dict")
    )
    assert list(inspect.signature(load_source_owned_varrock_east_iron_release_receipt).parameters) == []
    assert list(
        inspect.signature(
            require_source_owned_varrock_east_iron_release_receipt
        ).parameters
    ) == ["value"]
