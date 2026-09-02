from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from validation.inventory_v3_protocol_v2.package_tree import (
    PackageTreeError,
    verify_package_tree,
)
from validation.inventory_v3_protocol_v2.protocol import verify_protocol_v2_repository

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "validation" / "inventory_v3_release_binding_preparation"
PROPOSAL_PATH = PACKAGE_ROOT / "proposal-input.json"
TREE_PATH = PACKAGE_ROOT / "package-tree.json"

FROZEN_V3 = "5975532b472a74d93f010e04ca44b2efa2a3ffd7"
PROTOCOL_V1_SOURCE = "b3b141e0d9ca15d729eaa98c795f6c855bff68cf"
PROTOCOL_V1_LOCK = "32764bfd82afb46d4e99292bab7d162be536e2d7"
PROTOCOL_V1_LOCK_SHA256 = "64ab45f8b0294f733c4517ad46ebb01e722f3fbf3d14d52feb79649b5a3649f1"
PROTOCOL_V2_SOURCE = "0aa2647cd3382f217212377c7218848c3f322739"
PROTOCOL_V2_LOCK = "66c7e9536539979bc60e17f02f026eb64ebf0768"
PROTOCOL_V2_LOCK_SHA256 = "60ff2c511e46be3b87df4e0d9e4f705d897a4181f9152f2729ee90f6c45f8cf5"
PROTOCOL_V2_PREREGISTRATION_SHA256 = (
    "debecab3c90b71dbb7746c0fbe40abdb2212651ed495358a4c10ce712971d509"
)
FROZEN_C3 = "76d47af4213a9990054b3beb5ccb0285e3138b79"

EXPECTED_BLOCKERS = {
    "downstream_release": [
        "INVENTORY_RELEASE_RECEIPT_MISSING",
        "ISSUE_14_WORLDSTATE_FUSION_NOT_IMPLEMENTED",
        "PRODUCTION_CONSUMER_BINDING_UNRESOLVED",
        "RESOURCE_PERCEPTION_RELEASE_GATE_OPEN",
    ],
    "production_binding_prerequisites": [
        "CAMPAIGN_PACKAGE_ROOT_UNRESOLVED",
        "INDEPENDENT_REVIEW_MISSING",
        "INDEPENDENT_REVIEW_ROOTS_UNRESOLVED",
        "LIVE_CAMPAIGN_AUTHORIZATION_MISSING",
        "LIVE_CAMPAIGN_PACKAGE_MISSING",
        "PRODUCTION_BINDING_APPROVAL_MISSING",
        "PRODUCTION_BINDING_IDENTITY_PROPOSAL_UNRESOLVED",
        "PRODUCTION_BINDING_RECORD_MISSING",
        "PRODUCTION_BUILD_GIT_COMMIT_UNRESOLVED",
        "PRODUCTION_BUILD_IDENTITY_UNRESOLVED",
        "PRODUCTION_CAPTURE_ENVIRONMENT_IDENTITY_UNRESOLVED",
        "PRODUCTION_DETECTOR_IDENTITY_UNRESOLVED",
        "PRODUCTION_INVENTORY_CONFIGURATION_IDENTITY_UNRESOLVED",
        "PRODUCTION_OBSERVATION_ADAPTER_IDENTITY_UNRESOLVED",
        "PRODUCTION_PROFILE_IDENTITY_UNRESOLVED",
        "SOURCE_APPROVAL_MISSING",
        "SOURCE_APPROVAL_PROPOSAL_MISSING",
        "SOURCE_APPROVAL_REQUEST_ROOT_UNRESOLVED",
        "TERMINAL_CONFORMANCE_PASS_MISSING",
        "TERMINAL_EVALUATOR_RESULT_ROOT_UNRESOLVED",
    ],
}

PRODUCTION_RECORD_PATH = "validation/inventory_v3_production_binding/production-binding.json"
PRODUCTION_SIDECAR_PATH = PRODUCTION_RECORD_PATH + ".sha256"
PRODUCTION_APPROVAL_RECORD_PATH = (
    "validation/inventory_v3_production_binding/production-binding-approval.json"
)
PRODUCTION_APPROVAL_SIDECAR_PATH = PRODUCTION_APPROVAL_RECORD_PATH + ".sha256"
EXPECTED_CONTRACT_SECTION_SHA256 = {
    "external_artifact_sources": (
        "7a8f150976ec664eb6c0ead4452229561d3f04cc9a11cb0343fe376d3802b016"
    ),
    "input_derivations": ("14e9d450b27d20ec412a4e227e534c09f2212f50059494a1735ecc88e95ee89e"),
    "production_binding_action_contract": (
        "99fe01f51ae4dce548d36e2563a02a5fb3d013568fa859fda0525ad6e1c89999"
    ),
    "production_binding_approval_action_contract": (
        "46ba7e6b805470ca2164f9bd5bd29d384f2619e540a61ecd857eef84e4985a78"
    ),
    "production_identity_contracts": (
        "d52a4ec560ccd6c01b633420dbe32a39869bf366d9e716139b25ca7f062bc102"
    ),
    "required_cross_bindings": ("a831fc019c76797d6b61588b1cfbc92f677759cbc808f121fc8b18376fc95018"),
    "required_source_artifact_fields": (
        "b769b93275962a6a58a85a941ffabfb58ea9f267f74fdf7eb5f555749721ff6a"
    ),
    "required_value_bindings": ("3b6bf6e488971ae53ecdcf9852294e3a8d128bc6e8973f1b3fc2189d6b617375"),
    "source_approval_action_contract": (
        "345fac322e7288c7291a2b50e27bfe5f561af6bf8698a1f5e3b6c9ef6ef6ff69"
    ),
}

EXPECTED_INPUT_KEYS = {
    "authorization": {
        "authorization_id",
        "capture_execution_head_sha",
        "git_commit_sha",
        "legacy_registry_git_blob",
        "opaque_receipt_id",
        "protocol_v2_registry_git_blob",
        "protocol_v2_registry_sidecar_git_blob",
    },
    "campaign": {
        "acquisition_package_tree_sha256",
        "acquisition_record_sha256",
        "campaign_id",
        "campaign_manifest_sha256",
        "dataset_id",
        "finalized_at_utc",
        "host_reservation_sha256",
        "operator",
        "original_source_completion_seal_sha256",
        "original_source_session_report_sha256",
        "producer_attestation_sha256",
        "session_id",
        "source_campaign_id",
        "source_completion_seal_sha256",
        "source_session_report_sha256",
    },
    "environment": {
        "capture_environment_sha256",
        "producer_identity_sha256",
    },
    "evaluation": {
        "evaluated_at_utc",
        "frozen_evaluator_report_sha256",
        "result_package_tree_sha256",
        "terminal_result_sha256",
    },
    "production_binding": {
        "binding_id",
        "build_git_commit_sha",
        "build_identity_sha256",
        "capture_environment_identity_sha256",
        "detector_identity_sha256",
        "git_commit_sha",
        "inventory_configuration_identity_sha256",
        "observation_adapter_identity_sha256",
        "profile_identity_sha256",
        "record_git_blob",
        "record_sha256",
        "record_sidecar_git_blob",
        "record_sidecar_sha256",
    },
    "production_binding_approval": {
        "approval_id",
        "approved_at_utc",
        "approver",
        "git_commit_sha",
        "identity_proposal_sha256",
        "record_git_blob",
        "record_sha256",
        "record_sidecar_git_blob",
        "record_sidecar_sha256",
    },
    "review": {
        "review_intake_package_tree_sha256",
        "review_submission_package_tree_sha256",
        "review_submission_sha256",
        "reviewed_at_utc",
        "reviewed_package_tree_sha256",
        "reviewer",
        "reviewer_truth_sha256",
        "validation_package_sha256",
    },
    "source_approval": {
        "approval_id",
        "approved_at_utc",
        "approver",
        "git_commit_sha",
        "registry_git_blob",
        "registry_sha256",
        "registry_sidecar_git_blob",
        "registry_sidecar_sha256",
    },
    "source_approval_proposal": {
        "approval_id",
        "approval_request_sha256",
        "approval_request_tree_sha256",
        "proposed_approved_at_utc",
        "proposed_approver",
        "proposed_registry_sha256",
    },
}


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
    ).encode()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_mapping(path: Path) -> dict[str, object]:
    value = json.loads(path.read_bytes())
    assert isinstance(value, dict)
    return value


def _mapping(value: object) -> Mapping[str, object]:
    assert isinstance(value, Mapping)
    return value


def _sequence(value: object) -> Sequence[object]:
    assert isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
    return value


def _null_paths(value: object, prefix: str = "") -> list[str]:
    if value is None:
        return [prefix]
    if isinstance(value, Mapping):
        paths: list[str] = []
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            paths.extend(_null_paths(child, child_prefix))
        return paths
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        paths = []
        for index, child in enumerate(value):
            paths.extend(_null_paths(child, f"{prefix}[{index}]"))
        return paths
    return []


def _resolve_json_pointer(document: object, pointer: str) -> object:
    assert pointer.startswith("/")
    value = document
    for raw_token in pointer[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, Mapping):
            value = value[token]
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            value = value[int(token)]
        else:
            raise AssertionError(f"cannot resolve {pointer!r}")
    return value


def _external_references(proposal: Mapping[str, object]) -> list[str]:
    references = [
        str(member)
        for binding in _sequence(proposal["required_cross_bindings"])
        for member in _sequence(_mapping(binding)["members"])
        if not str(member).startswith("input#")
    ]
    references.extend(
        str(_mapping(binding)["pointer"])
        for binding in _sequence(proposal["required_value_bindings"])
    )
    return references


def _is_json_field_locator(locator: str) -> bool:
    return locator.startswith("#/") and "#" not in locator[1:]


def _strict_json_loads(payload: bytes) -> object:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate object key: {key}")
            result[key] = value
        return result

    return json.loads(payload, object_pairs_hook=reject_duplicates)


def _derive_prefixed_id(record: Mapping[str, object], spec: Mapping[str, object]) -> str:
    assert spec["digest_algorithm"] == "sha256"
    assert spec["formula"] == (
        "output_prefix-plus-digest-hex-slice-of-canonical-source-with-omitted-members"
    )
    omitted = {str(member) for member in _sequence(spec["omitted_top_level_members"])}
    projection = {key: value for key, value in record.items() if key not in omitted}
    full_digest = _sha256(_canonical_json_bytes(projection))
    digest_slice = list(_sequence(spec["digest_hex_slice"]))
    assert len(digest_slice) == 2
    start, end = (int(value) for value in digest_slice)
    return str(spec["output_prefix"]) + full_digest[start:end]


def _source_git_blobs_violations(
    repository: Path,
    anchor_commit: str,
    raw_entries: object,
    *,
    approved_entries: object | None = None,
) -> list[str]:
    violations: list[str] = []
    if re.fullmatch(r"[0-9a-f]{40}", anchor_commit) is None:
        violations.append("anchor-commit-format")
        return violations
    if not isinstance(raw_entries, Sequence) or isinstance(raw_entries, (str, bytes, bytearray)):
        return ["entries-not-array"]
    entries = list(raw_entries)
    if not entries:
        return ["entries-empty"]
    normalized: list[tuple[str, str]] = []
    for index, raw_entry in enumerate(entries):
        if not isinstance(raw_entry, Mapping):
            violations.append(f"entry-{index}-not-object")
            continue
        if set(raw_entry) != {"git_blob", "path"}:
            violations.append(f"entry-{index}-members")
            continue
        path = raw_entry["path"]
        git_blob = raw_entry["git_blob"]
        if not isinstance(path, str) or not path:
            violations.append(f"entry-{index}-path")
            continue
        parsed = PurePosixPath(path)
        if (
            "\\" in path
            or parsed.is_absolute()
            or path != parsed.as_posix()
            or any(part in {"", ".", ".."} for part in parsed.parts)
        ):
            violations.append(f"entry-{index}-path")
            continue
        if not isinstance(git_blob, str) or re.fullmatch(r"[0-9a-f]{40}", git_blob) is None:
            violations.append(f"entry-{index}-git-blob-format")
            continue
        normalized.append((path, git_blob))

    paths = [path for path, _ in normalized]
    if paths != sorted(paths):
        violations.append("entries-not-sorted")
    if len(paths) != len(set(paths)):
        violations.append("duplicate-path")
    if approved_entries is not None and raw_entries != approved_entries:
        violations.append("approved-set-mismatch")

    for path, git_blob in normalized:
        object_name = f"{anchor_commit}:{path}"
        object_type = subprocess.run(
            ("git", "-C", str(repository), "cat-file", "-t", object_name),
            check=False,
            capture_output=True,
            text=True,
        )
        if object_type.returncode != 0:
            violations.append(f"{path}:missing")
            continue
        if object_type.stdout.strip() != "blob":
            violations.append(f"{path}:not-blob")
            continue
        actual = subprocess.run(
            ("git", "-C", str(repository), "rev-parse", object_name),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if actual != git_blob:
            violations.append(f"{path}:blob-mismatch")
    return violations


def _contract_violations(proposal: Mapping[str, object], values: Mapping[str, object]) -> list[str]:
    violations: list[str] = []
    for raw_binding in _sequence(proposal["required_cross_bindings"]):
        binding = _mapping(raw_binding)
        members = [str(member) for member in _sequence(binding["members"])]
        if any(member not in values for member in members):
            violations.append(f"{binding['id']}:missing")
            continue
        first = values[members[0]]
        if any(values[member] != first for member in members[1:]):
            violations.append(f"{binding['id']}:mismatch")
    for raw_binding in _sequence(proposal["required_value_bindings"]):
        binding = _mapping(raw_binding)
        pointer = str(binding["pointer"])
        if pointer not in values:
            violations.append(f"{pointer}:missing")
        elif values[pointer] != binding["value"]:
            violations.append(f"{pointer}:mismatch")
    source_fields = _mapping(proposal["required_source_artifact_fields"])
    for alias in ("production-binding-approval", "production-binding-record"):
        expected = {
            alias + str(locator)
            for locator in _sequence(source_fields[alias])
            if _is_json_field_locator(str(locator))
        }
        actual = {
            pointer
            for pointer in values
            if pointer.startswith(alias + "#") and _is_json_field_locator(pointer[len(alias) :])
        }
        for missing in sorted(expected - actual):
            violations.append(f"{missing}:missing-declared-member")
        for extra in sorted(actual - expected):
            violations.append(f"{extra}:undeclared-member")
    return violations


def _synthetic_contract_values(proposal: Mapping[str, object]) -> dict[str, object]:
    parent: dict[str, str] = {}

    def find(member: str) -> str:
        parent.setdefault(member, member)
        if parent[member] != member:
            parent[member] = find(parent[member])
        return parent[member]

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    bindings = [_mapping(item) for item in _sequence(proposal["required_cross_bindings"])]
    for binding in bindings:
        members = [str(member) for member in _sequence(binding["members"])]
        for member in members[1:]:
            union(members[0], member)

    components: dict[str, list[str]] = {}
    for member in parent:
        components.setdefault(find(member), []).append(member)

    values: dict[str, object] = {}
    for root, members in components.items():
        fixed_values = []
        for member in members:
            if member.startswith("input#/"):
                resolved = _resolve_json_pointer(proposal, member[len("input#") :])
                if resolved is not None:
                    fixed_values.append(resolved)
        assert not fixed_values or all(value == fixed_values[0] for value in fixed_values)
        shared_value = fixed_values[0] if fixed_values else f"synthetic-{root}"
        for member in members:
            values[member] = shared_value
    for raw_binding in _sequence(proposal["required_value_bindings"]):
        binding = _mapping(raw_binding)
        pointer = str(binding["pointer"])
        prior = values.setdefault(pointer, binding["value"])
        assert prior == binding["value"]
    source_fields = _mapping(proposal["required_source_artifact_fields"])
    for alias in ("production-binding-approval", "production-binding-record"):
        for raw_locator in _sequence(source_fields[alias]):
            locator = str(raw_locator)
            if _is_json_field_locator(locator):
                values.setdefault(alias + locator, f"synthetic-{alias + locator}")
    return values


def _git(*arguments: str) -> str:
    return subprocess.run(
        ("git", "-C", str(ROOT), *arguments),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _git_bytes(*arguments: str) -> bytes:
    return subprocess.run(
        ("git", "-C", str(ROOT), *arguments),
        check=True,
        capture_output=True,
    ).stdout


def test_c4_preparation_package_is_canonical_hash_bound_and_closed() -> None:
    proposal_payload = PROPOSAL_PATH.read_bytes()
    proposal = _read_mapping(PROPOSAL_PATH)
    assert _strict_json_loads(proposal_payload) == proposal
    assert proposal_payload == _canonical_json_bytes(proposal)
    proposal_sha = _sha256(proposal_payload)
    assert (PACKAGE_ROOT / "proposal-input.json.sha256").read_bytes() == (
        f"{proposal_sha}  proposal-input.json\n".encode("ascii")
    )

    tree_payload = TREE_PATH.read_bytes()
    tree = _read_mapping(TREE_PATH)
    assert tree_payload == _canonical_json_bytes(tree)
    tree_sha = _sha256(tree_payload)
    assert (PACKAGE_ROOT / "package-tree.json.sha256").read_bytes() == (
        f"{tree_sha}  package-tree.json\n".encode("ascii")
    )
    snapshot = verify_package_tree(
        PACKAGE_ROOT,
        tree,
        reserved_paths=("package-tree.json", "package-tree.json.sha256"),
    )
    snapshot.recheck()

    with pytest.raises(ValueError, match="duplicate object key: approval_id"):
        _strict_json_loads(b'{"approval_id":"one","approval_id":"two"}\n')


def test_c4_closed_package_rejects_mutation_and_extra_files(tmp_path: Path) -> None:
    tree = _read_mapping(TREE_PATH)
    mutated_root = tmp_path / "mutated"
    shutil.copytree(PACKAGE_ROOT, mutated_root)
    proposal_path = mutated_root / "proposal-input.json"
    proposal_path.write_bytes(
        proposal_path.read_bytes().replace(
            b'"status":"preparation-only-release-blocked"',
            b'"status":"preparation-only-release-ready"',
            1,
        )
    )
    with pytest.raises(PackageTreeError, match="size or SHA-256 differs"):
        verify_package_tree(
            mutated_root,
            tree,
            reserved_paths=("package-tree.json", "package-tree.json.sha256"),
        )

    extra_root = tmp_path / "extra"
    shutil.copytree(PACKAGE_ROOT, extra_root)
    (extra_root / "unreviewed-extra.json").write_text("{}\n", encoding="ascii")
    with pytest.raises(PackageTreeError, match="physical tree differs"):
        verify_package_tree(
            extra_root,
            tree,
            reserved_paths=("package-tree.json", "package-tree.json.sha256"),
        )


def test_c4_all_live_and_production_inputs_are_explicitly_unresolved() -> None:
    proposal = _read_mapping(PROPOSAL_PATH)
    assert set(proposal) == {
        "artifact_contracts",
        "authority",
        "canonicalization_contract",
        "capture_contract",
        "chronology_contract",
        "current_inputs",
        "current_release_state",
        "external_artifact_sources",
        "external_locator_contract",
        "fixed_contract_digest_sources",
        "fixed_contract_digests",
        "fixed_root_templates",
        "frozen_bindings",
        "input_derivations",
        "model_firewall",
        "preparation_lineage",
        "privacy",
        "production_binding_action_contract",
        "production_binding_approval_action_contract",
        "production_identity_contracts",
        "release_blockers",
        "required_cross_bindings",
        "required_pass_contract",
        "required_source_approval_proposal_contract",
        "required_source_artifact_fields",
        "required_value_bindings",
        "role_contract",
        "schema",
        "source_approval_action_contract",
        "status",
        "unknown_policy",
        "unresolved_field_paths",
    }
    assert proposal["schema"] == (
        "inventory-positive-v3-source-approval-production-binding-input-preparation-v1"
    )
    assert proposal["status"] == "preparation-only-release-blocked"

    inputs = _mapping(proposal["current_inputs"])
    assert set(inputs) == set(EXPECTED_INPUT_KEYS)
    for group, expected_keys in EXPECTED_INPUT_KEYS.items():
        values = _mapping(inputs[group])
        assert set(values) == expected_keys
        assert all(value is None for value in values.values())

    unresolved = list(_sequence(proposal["unresolved_field_paths"]))
    assert unresolved == sorted(unresolved)
    assert unresolved == sorted(_null_paths(proposal))
    assert len(unresolved) == len(set(unresolved)) == 72
    assert proposal["release_blockers"] == EXPECTED_BLOCKERS


def test_c4_canonicalization_and_fixed_contract_digests_are_exact() -> None:
    proposal = _read_mapping(PROPOSAL_PATH)
    for section, expected_sha256 in EXPECTED_CONTRACT_SECTION_SHA256.items():
        assert _sha256(_canonical_json_bytes(proposal[section])) == expected_sha256
    assert proposal["canonicalization_contract"] == {
        "canonical_json": {
            "allow_nan": False,
            "duplicate_object_keys_allowed": False,
            "encoding": "utf-8",
            "ensure_ascii": True,
            "exact_document_byte_equality_required": True,
            "separators": [",", ":"],
            "sort_keys": True,
            "trailing_lf_count": 1,
        },
        "sidecar": {
            "encoding": "ascii",
            "lowercase_sha256_required": True,
            "template": "{sha256}  {basename}\n",
            "trailing_lf_count": 1,
        },
        "subobject_extraction": "resolve-rfc6901-pointer-before-canonicalization",
        "subobject_sha256_formula": (
            "sha256(canonical-json(exact-json-pointer-value)-with-one-trailing-lf)"
        ),
    }
    assert _canonical_json_bytes({"z": "Windows™", "a": 1}) == (b'{"a":1,"z":"Windows\\u2122"}\n')
    with pytest.raises(ValueError, match="Out of range float values"):
        _canonical_json_bytes({"invalid": float("nan")})

    sources = _mapping(proposal["fixed_contract_digest_sources"])
    assert sources == {
        "capture_contract_sha256": "#/capture_contract",
        "frozen_candidate_sha256": "#/frozen_bindings/candidate",
        "frozen_evaluator_sha256": "#/frozen_bindings/evaluator",
        "model_firewall_sha256": "#/model_firewall",
        "unknown_policy_sha256": "#/unknown_policy",
    }
    digests = _mapping(proposal["fixed_contract_digests"])
    assert set(digests) == set(sources)
    for name, pointer in sources.items():
        value = _resolve_json_pointer(proposal, str(pointer)[1:])
        assert digests[name] == _sha256(_canonical_json_bytes(value))


def test_c4_frozen_lineage_capture_and_non_authority_are_exact() -> None:
    proposal = _read_mapping(PROPOSAL_PATH)
    protocol = _mapping(_mapping(proposal["frozen_bindings"])["protocol"])
    assert protocol == {
        "frozen_v3_head_sha": FROZEN_V3,
        "protocol_v1_lock_git_commit_sha": PROTOCOL_V1_LOCK,
        "protocol_v1_lock_sha256": PROTOCOL_V1_LOCK_SHA256,
        "protocol_v1_source_git_commit_sha": PROTOCOL_V1_SOURCE,
        "protocol_v2_lock_git_commit_sha": PROTOCOL_V2_LOCK,
        "protocol_v2_lock_sha256": PROTOCOL_V2_LOCK_SHA256,
        "protocol_v2_preregistration_sha256": PROTOCOL_V2_PREREGISTRATION_SHA256,
        "protocol_v2_source_git_commit_sha": PROTOCOL_V2_SOURCE,
        "source_identity_bridge": "frozen-capture-lf-to-frozen-evaluator-data-bytes-v1",
    }

    capture = _mapping(proposal["capture_contract"])
    assert capture["approved_capture_build_sha"] == PROTOCOL_V1_SOURCE
    assert capture["capture_configuration_id"] == (
        "inventory-positive-v3-independent-passive-natural-fill-v1"
    )
    assert capture["selection_policy"] == (
        "all-owned-captures-in-source-order-no-drop-no-replacement"
    )
    assert capture["source_order"] == [
        "empty",
        "early-partial",
        "mid-partial",
        "near-full",
        "full",
        "wrong-tab",
        "row-obstruction",
    ]
    assert capture["frame"] == {
        "height": 1078,
        "inventory_region": [567, 569, 158, 248],
        "pixel_format": "bgra8888",
        "profile_id": "candidate-live-inventory-348867800b28a54e",
        "width": 1005,
    }
    assert capture["natural_iron_fill_required"] is True
    assert capture["arbitrary_inventory_rearrangement_allowed"] is False
    assert capture["bank_or_item_fixture_manipulation_allowed"] is False
    assert capture["input_automation_allowed"] is False
    assert set(_mapping(capture["detector_control"]).values()) == {False}

    candidate = _mapping(_mapping(proposal["frozen_bindings"])["candidate"])
    assert candidate["identity_role"] == "development-frozen-not-production-authority"
    assert candidate["publication_floor"] == 0.8
    assert isinstance(candidate["publication_floor"], float)
    assert candidate["development_detector_id"] == ("inventory-positive-v3-development-candidate")

    v1_preregistration = _read_mapping(
        ROOT / "validation" / "inventory-positive-v3" / "preregistration.json"
    )
    v2_preregistration = _read_mapping(
        ROOT / "validation" / "inventory_v3_protocol_v2" / "preregistration.json"
    )
    v1_candidate = _mapping(v1_preregistration["candidate"])
    v2_candidate = _mapping(v2_preregistration["candidate"])
    assert candidate == {
        "analyzer_id": _mapping(v1_candidate["analyzer"])["id"],
        "analyzer_version": _mapping(v1_candidate["analyzer"])["version"],
        "classifier_id": _mapping(v1_candidate["classifier"])["id"],
        "classifier_version": _mapping(v1_candidate["classifier"])["version"],
        "development_configuration_id": v2_candidate["configuration_id"],
        "development_detector_id": _mapping(v1_candidate["detector"])["id"],
        "development_detector_version": _mapping(v1_candidate["detector"])["version"],
        "identity_role": "development-frozen-not-production-authority",
        "model_artifact_sha256": v2_candidate["model_artifact_sha256"],
        "model_configuration_sha256": v2_candidate["model_configuration_sha256"],
        "prototype_occurrences_sha256": v2_candidate["prototype_occurrences_sha256"],
        "prototype_source_set_sha256": v2_candidate["prototype_source_set_sha256"],
        "publication_floor": v2_candidate["publication_floor"],
        "reference_region_file_sha256": v2_candidate["reference_region_file_sha256"],
        "reference_rgb_sha256": v2_candidate["reference_rgb_sha256"],
    }
    assert _mapping(_mapping(proposal["frozen_bindings"])["evaluator"]) == {
        "id": _mapping(v1_preregistration["evaluator"])["id"],
        "identity_role": "frozen-conformance-only-not-production-authority",
        "version": _mapping(v1_preregistration["evaluator"])["version"],
    }
    v2_capture = _mapping(v2_preregistration["capture"])
    assert capture["approved_capture_build_sha"] == v2_capture["approved_build_sha"]
    assert capture["capture_configuration_id"] == v2_capture["capture_configuration_id"]
    assert capture["selection_policy"] == v2_capture["selection_policy"]
    assert capture["source_order"] == v2_capture["stages"]
    v2_environment = _mapping(v2_preregistration["environment_provenance"])
    assert capture["environment_provenance"] == {
        "assertions_grant_support_authority": v2_environment["assertions_grant_support_authority"],
        "observed_field_names": v2_environment["observed_fields"],
        "operator_asserted_field_names": v2_environment["asserted_fields"],
        "producer_scope": v2_environment["producer_scope"],
        "required_observed_field_names": v2_environment["required_observed_fields"],
        "windows_legacy_path_budget_required": v2_environment[
            "windows_legacy_path_budget_required"
        ],
    }

    authority = _mapping(proposal["authority"])
    release_state = _mapping(proposal["current_release_state"])
    assert set(authority) == {
        "activation_allowed",
        "controller_authority_allowed",
        "input_authority_allowed",
        "live_capture_allowed",
        "promotion_allowed",
        "source_approval_granted",
        "source_write_allowed",
        "support_authority_granted",
        "world_state_authority_allowed",
    }
    assert set(release_state) == {
        "inventory_release_receipt_present",
        "live_campaign_authorized",
        "live_campaign_executed",
        "production_binding_present",
        "production_binding_approval_present",
        "review_present",
        "source_approval_present",
        "source_approval_proposal_present",
        "terminal_evaluation_executed",
    }
    assert authority and all(value is False for value in authority.values())
    assert release_state and all(value is False for value in release_state.values())
    firewall = _mapping(proposal["model_firewall"])
    assert set(firewall) == {
        "calibration_allowed",
        "model_mutation_allowed",
        "post_campaign_tuning_allowed",
        "prototype_learning_allowed",
        "training_allowed",
        "validation_case_export_to_model_allowed",
    }
    privacy = _mapping(proposal["privacy"])
    assert set(privacy) == {
        "absolute_host_paths_allowed",
        "live_pixels_included",
        "private_evaluator_report_included",
        "raw_producer_identity_included",
        "resolved_package_may_publish_private_metadata",
        "reviewer_truth_included",
    }
    assert all(value is False for value in firewall.values())
    assert all(value is False for value in privacy.values())


def test_c4_empty_authorization_and_approval_baselines_match_exact_l2() -> None:
    proposal = _read_mapping(PROPOSAL_PATH)
    frozen = _mapping(proposal["frozen_bindings"])
    approval = _mapping(frozen["approval_registry_baseline"])
    approval_payload = _git_bytes("show", f"{PROTOCOL_V2_LOCK}:{approval['path']}")
    approval_sidecar = _git_bytes("show", f"{PROTOCOL_V2_LOCK}:{approval['sidecar_path']}")
    assert json.loads(approval_payload) == {
        "activation_allowed": False,
        "entries": [],
        "promotion_allowed": False,
        "schema": "inventory-positive-v3-independent-validation-approval-registry-v1",
    }
    assert approval["git_blob"] == _git("rev-parse", f"{PROTOCOL_V2_LOCK}:{approval['path']}")
    assert approval["sha256"] == _sha256(approval_payload)
    assert approval["sidecar_git_blob"] == _git(
        "rev-parse", f"{PROTOCOL_V2_LOCK}:{approval['sidecar_path']}"
    )
    assert approval["sidecar_sha256"] == _sha256(approval_sidecar)
    assert approval_sidecar == f"{approval['sha256']}  approved-campaigns.json\n".encode("ascii")
    assert approval["state"] == "canonical-empty-source-approval-absent"

    live = _mapping(frozen["live_authorization_baselines"])
    legacy = _mapping(live["legacy"])
    v2 = _mapping(live["protocol_v2"])
    legacy_payload = _git_bytes("show", f"{PROTOCOL_V2_LOCK}:{legacy['path']}")
    v2_payload = _git_bytes("show", f"{PROTOCOL_V2_LOCK}:{v2['path']}")
    v2_sidecar = _git_bytes("show", f"{PROTOCOL_V2_LOCK}:{v2['sidecar_path']}")
    assert json.loads(legacy_payload)["authorizations"] == []
    assert json.loads(v2_payload)["authorizations"] == []
    for binding, payload in ((legacy, legacy_payload), (v2, v2_payload)):
        assert binding["git_blob"] == _git("rev-parse", f"{PROTOCOL_V2_LOCK}:{binding['path']}")
        assert binding["sha256"] == _sha256(payload)
    assert v2["sidecar_git_blob"] == _git("rev-parse", f"{PROTOCOL_V2_LOCK}:{v2['sidecar_path']}")
    assert v2["sidecar_sha256"] == _sha256(v2_sidecar)
    assert v2_sidecar == f"{v2['sha256']}  live-campaign-authorizations.json\n".encode("ascii")
    assert live["state"] == "canonical-empty-live-authorization-absent"


def test_c4_unknown_pass_and_future_source_boundaries_remain_fail_closed() -> None:
    proposal = _read_mapping(PROPOSAL_PATH)
    unknown = _mapping(proposal["unknown_policy"])
    assert unknown == {
        "operator_labels_are_reviewer_truth": False,
        "publication_floor": 0.8,
        "required_negative_outcomes": {
            "row-obstruction": {
                "occupied_slots": "MUST-BE-NULL",
                "outcome": "UNKNOWN",
            },
            "wrong-tab": {
                "occupied_slots": "MUST-BE-NULL",
                "outcome": "UNKNOWN",
            },
        },
        "reviewer_truth_only_after_finalization": True,
        "unknown_can_grant_action_readiness": False,
        "unknown_can_grant_bank_transition_readiness": False,
        "unknown_can_grant_full": False,
        "unknown_can_grant_known_non_full": False,
        "validation_failure_remains_failure": True,
    }
    assert proposal["required_pass_contract"] == {
        "activation_allowed": False,
        "approval_required": True,
        "contract_id": "CONFORMANCE_PASSED_APPROVAL_REQUIRED",
        "detector_conformance_passed": True,
        "promotion_allowed": False,
        "retry_allowed": False,
        "terminal_status": "conformance-passed-source-approval-required",
    }
    assert proposal["required_source_approval_proposal_contract"] == {
        "approval_registry_modified": False,
        "source_action_required": True,
        "status": "request-only-not-approved",
    }
    assert proposal["role_contract"] == {
        "pairwise_distinct_required": True,
        "roles": ["operator", "reviewer", "approver"],
    }

    preparation = _mapping(proposal["preparation_lineage"])
    assert preparation == {
        "future_live_authorization_base_git_commit_sha": PROTOCOL_V2_LOCK,
        "future_live_authorization_changed_paths": [
            "validation/inventory-positive-v3/live-campaign-authorizations.json",
            "validation/inventory_v3_protocol_v2/live-campaign-authorizations.json",
            "validation/inventory_v3_protocol_v2/live-campaign-authorizations.json.sha256",
        ],
        "future_live_authorization_direct_child_required": True,
        "future_live_authorization_exact_changed_path_set_required": True,
        "parent_git_commit_sha": FROZEN_C3,
        "parent_role": "locked-synthetic-rehearsal-only",
        "preparation_commits_grant_live_authority": False,
    }


def test_c4_binding_graph_prevents_foreign_or_rebound_roots() -> None:
    proposal = _read_mapping(PROPOSAL_PATH)
    assert proposal["fixed_root_templates"] == {
        "acquisition_package": "diagnostics/iv3v2/{authorization_id}/a",
        "approval_request_package": "diagnostics/iv3v2/{authorization_id}/ar",
        "result_package": "diagnostics/iv3v2r/{authorization_id}",
        "review_intake_package": "diagnostics/iv3v2/{authorization_id}/ri/package",
        "review_submission_package": "diagnostics/iv3v2/{authorization_id}/ri/submission",
        "reviewed_package": "diagnostics/iv3v2/{authorization_id}/rp",
        "source_campaign": (
            "diagnostics/inventory-positive-v3-independent-source/{authorization_id}"
        ),
    }
    raw_bindings = _sequence(proposal["required_cross_bindings"])
    bindings = {
        str(_mapping(item)["id"]): list(_sequence(_mapping(item)["members"]))
        for item in raw_bindings
    }
    expected_ids = {
        "acquisition-record",
        "acquisition-tree",
        "approval-id",
        "approval-registry",
        "approval-registry-sidecar",
        "approved-at",
        "approver",
        "authorization-capture-execution-head",
        "authorization-id",
        "authorization-legacy-registry-git-blob",
        "authorization-protocol-v2-registry-git-blob",
        "authorization-protocol-v2-registry-sidecar-git-blob",
        "campaign-id",
        "campaign-manifest",
        "capture-build",
        "capture-configuration",
        "capture-contract",
        "capture-environment-evidence",
        "dataset-id",
        "evaluated-at",
        "evaluator-report",
        "finalized-at",
        "frozen-candidate",
        "frozen-candidate-configuration",
        "frozen-candidate-head",
        "frozen-evaluator",
        "host-reservation",
        "model-firewall",
        "opaque-receipt",
        "operator",
        "predecessor-lock-commit",
        "predecessor-lock-digest",
        "predecessor-protocol-source-commit",
        "producer-attestation",
        "producer-identity",
        "production-binding-approval-commit",
        "production-binding-approval-id",
        "production-binding-approval-record",
        "production-binding-approval-record-git-blob",
        "production-binding-approval-record-sidecar",
        "production-binding-approval-record-sidecar-git-blob",
        "production-binding-approved-at",
        "production-binding-commit",
        "production-binding-id",
        "production-build-git-commit",
        "production-build-identity",
        "production-capture-environment-identity",
        "production-detector-identity",
        "production-identity-proposal",
        "production-inventory-configuration-identity",
        "production-observation-adapter-identity",
        "production-profile-identity",
        "production-record",
        "production-record-git-blob",
        "production-record-sidecar",
        "production-record-sidecar-git-blob",
        "protocol-lock-commit",
        "protocol-lock-digest",
        "protocol-source-commit",
        "review-intake-tree",
        "review-submission",
        "review-submission-tree",
        "reviewed-at",
        "reviewed-tree",
        "reviewer",
        "reviewer-truth",
        "result-tree",
        "session-id",
        "source-approval-git-commit",
        "source-approval-registry-git-blob",
        "source-approval-registry-sidecar-git-blob",
        "source-approval-request",
        "source-approval-request-tree",
        "source-campaign-id",
        "source-completion-seal",
        "source-identity-bridge",
        "source-original-completion-seal",
        "source-original-session-report",
        "source-session-report",
        "terminal-result",
        "unknown-policy",
        "validation-package",
    }
    assert set(bindings) == expected_ids
    assert list(bindings) == sorted(bindings)
    assert len(bindings) == len(raw_bindings)
    assert "approval-request#/result_binding/authorization_id" in bindings["authorization-id"]
    assert "approval-request#/result_binding/terminal_result_sha256" in bindings["terminal-result"]
    assert "approval-request#/result_binding/result_package_tree_sha256" in bindings["result-tree"]
    assert (
        "approval-request#/result_binding/reviewed_package_tree_sha256" in bindings["reviewed-tree"]
    )
    assert (
        "approval-request#/result_binding/frozen_evaluator_report_sha256"
        in bindings["evaluator-report"]
    )
    assert "production-binding-record#/source_approval_id" in bindings["approval-id"]
    assert "source-approval-entry#/reviewer_truth_sha256" in bindings["reviewer-truth"]
    assert {
        "input#/current_inputs/authorization/git_commit_sha",
        "input#/current_inputs/authorization/capture_execution_head_sha",
    }.issubset(bindings["authorization-capture-execution-head"])
    assert (
        "production-binding-record#/source_approval_git_commit_sha"
        in bindings["source-approval-git-commit"]
    )
    assert (
        "production-binding-record#/frozen_candidate_head_sha" in bindings["frozen-candidate-head"]
    )

    artifact_contracts = _mapping(proposal["artifact_contracts"])
    assert set(artifact_contracts) == {
        "acquisition_package",
        "approval_request_package",
        "result_package",
        "review_intake_package",
        "review_submission_package",
        "reviewed_package",
    }
    serialized_contracts = json.dumps(artifact_contracts, sort_keys=True)
    assert ".bgra" not in serialized_contracts
    assert "reviewer-truth.json" not in serialized_contracts
    assert (
        _mapping(artifact_contracts["reviewed_package"])["reviewer_truth_content_read_allowed"]
        is False
    )
    assert (
        _mapping(artifact_contracts["result_package"])["private_evaluator_report_read_allowed"]
        is False
    )
    source_fields = _mapping(proposal["required_source_artifact_fields"])
    external_sources = _mapping(proposal["external_artifact_sources"])
    assert set(source_fields) == set(external_sources)
    expected_locators: dict[str, set[str]] = {}
    for reference in _external_references(proposal):
        alias, locator = reference.split("#", 1)
        expected_locators.setdefault(alias, set()).add("#" + locator)
    for raw_contract in _mapping(proposal["production_identity_contracts"]).values():
        contract = _mapping(raw_contract)
        base_pointer = str(contract["canonical_record_pointer"])
        for field in _sequence(contract["required_fields"]):
            expected_locators.setdefault("production-binding-record", set()).add(
                "#" + base_pointer + str(field)
            )
            expected_locators.setdefault("production-binding-approval", set()).add(
                "#/production_identity_proposal" + base_pointer + str(field)
            )
    assert source_fields == {
        alias: sorted(locators) for alias, locators in sorted(expected_locators.items())
    }
    assert external_sources["production-binding-record"] == PRODUCTION_RECORD_PATH
    assert external_sources["production-binding-sidecar"] == PRODUCTION_SIDECAR_PATH
    assert external_sources["production-binding-approval"] == (PRODUCTION_APPROVAL_RECORD_PATH)
    assert external_sources["production-binding-approval-sidecar"] == (
        PRODUCTION_APPROVAL_SIDECAR_PATH
    )

    input_members = {
        str(member)
        for binding in _sequence(proposal["required_cross_bindings"])
        for member in _sequence(_mapping(binding)["members"])
        if str(member).startswith("input#/")
    }
    for member in input_members:
        _resolve_json_pointer(proposal, member[len("input#") :])

    derivations = _mapping(proposal["input_derivations"])
    assert set(derivations) == {
        "current_inputs.authorization.legacy_registry_git_blob",
        "current_inputs.authorization.protocol_v2_registry_git_blob",
        "current_inputs.authorization.protocol_v2_registry_sidecar_git_blob",
        "current_inputs.campaign.acquisition_record_sha256",
        "current_inputs.environment.capture_environment_sha256",
        "current_inputs.environment.producer_identity_sha256",
        "current_inputs.evaluation.terminal_result_sha256",
        "current_inputs.production_binding.binding_id",
        "current_inputs.production_binding.build_identity_sha256",
        "current_inputs.production_binding.capture_environment_identity_sha256",
        "current_inputs.production_binding.detector_identity_sha256",
        "current_inputs.production_binding.inventory_configuration_identity_sha256",
        "current_inputs.production_binding.observation_adapter_identity_sha256",
        "current_inputs.production_binding.profile_identity_sha256",
        "current_inputs.production_binding.record_git_blob",
        "current_inputs.production_binding.record_sha256",
        "current_inputs.production_binding.record_sidecar_git_blob",
        "current_inputs.production_binding.record_sidecar_sha256",
        "current_inputs.production_binding_approval.approval_id",
        "current_inputs.production_binding_approval.identity_proposal_sha256",
        "current_inputs.production_binding_approval.record_git_blob",
        "current_inputs.production_binding_approval.record_sha256",
        "current_inputs.production_binding_approval.record_sidecar_git_blob",
        "current_inputs.production_binding_approval.record_sidecar_sha256",
        "current_inputs.source_approval.registry_git_blob",
        "current_inputs.source_approval.registry_sha256",
        "current_inputs.source_approval.registry_sidecar_git_blob",
        "current_inputs.source_approval.registry_sidecar_sha256",
        "current_inputs.source_approval_proposal.approval_id",
        "current_inputs.source_approval_proposal.approval_request_sha256",
    }
    for derivation in derivations.values():
        assert {"formula", "source"} <= set(_mapping(derivation))
    assert all(
        "current_inputs.production_binding.record_path" not in str(_mapping(derivation)["source"])
        and "current_inputs.production_binding.record_sidecar_path"
        not in str(_mapping(derivation)["source"])
        for derivation in derivations.values()
    )

    covered_inputs = {member[len("input#/") :].replace("/", ".") for member in input_members} | set(
        derivations
    )
    unresolved = set(_sequence(proposal["unresolved_field_paths"]))
    assert unresolved <= covered_inputs

    value_bindings = [
        _mapping(binding) for binding in _sequence(proposal["required_value_bindings"])
    ]
    value_pointers = [str(binding["pointer"]) for binding in value_bindings]
    assert value_pointers == sorted(value_pointers)
    assert len(value_pointers) == len(set(value_pointers))


def test_c4_literal_contract_and_rebinding_attacks_fail_closed() -> None:
    proposal = _read_mapping(PROPOSAL_PATH)
    required_values = {
        str(binding["pointer"]): binding["value"]
        for item in _sequence(proposal["required_value_bindings"])
        if (binding := _mapping(item))
    }
    pass_contract = _mapping(proposal["required_pass_contract"])
    terminal_projection = {
        "terminal-result#/activation_allowed": pass_contract["activation_allowed"],
        "terminal-result#/approval_required": pass_contract["approval_required"],
        "terminal-result#/contract_id": pass_contract["contract_id"],
        "terminal-result#/detector_conformance_passed": pass_contract[
            "detector_conformance_passed"
        ],
        "terminal-result#/promotion_allowed": pass_contract["promotion_allowed"],
        "terminal-result#/retry_allowed": pass_contract["retry_allowed"],
        "terminal-result#/terminal_status": pass_contract["terminal_status"],
    }
    assert {key: required_values[key] for key in terminal_projection} == terminal_projection
    proposal_contract = _mapping(proposal["required_source_approval_proposal_contract"])
    assert (
        required_values["approval-request#/approval_registry_modified"]
        is (proposal_contract["approval_registry_modified"])
    )
    assert (
        required_values["approval-request#/source_action_required"]
        is (proposal_contract["source_action_required"])
    )
    assert required_values["approval-request#/status"] == proposal_contract["status"]
    assert required_values["source-approval-entry#/status"] == (
        "approved-for-independent-validation-conformance"
    )
    assert required_values["production-binding-approval#/schema"] == (
        "inventory-positive-v3-production-binding-approval-v1"
    )
    assert required_values["production-binding-approval#/status"] == (
        "approved-for-source-owned-production-binding"
    )
    assert (
        required_values["production-binding-approval#/production_identity_proposal/schema"]
        == "inventory-positive-v3-production-identity-proposal-v1"
    )
    assert (
        required_values["production-binding-approval#/production_binding_write_authorized"] is True
    )
    assert required_values["production-binding-approval#/identity_proposal_finalized"] is True
    for pointer in (
        "production-binding-approval#/activation_allowed",
        "production-binding-approval#/controller_authority_allowed",
        "production-binding-approval#/input_authority_allowed",
        "production-binding-approval#/inventory_release_receipt_issued",
        "production-binding-approval#/promotion_allowed",
        "production-binding-approval#/support_authority_granted",
        "production-binding-approval#/world_state_authority_allowed",
    ):
        assert required_values[pointer] is False
    assert required_values["production-binding-record#/publication_floor"] == 0.8
    for pointer in (
        "production-binding-record#/activation_allowed",
        "production-binding-record#/controller_authority_allowed",
        "production-binding-record#/input_authority_allowed",
        "production-binding-record#/promotion_allowed",
        "production-binding-record#/support_authority_granted",
        "production-binding-record#/unknown_can_grant_action_readiness",
        "production-binding-record#/unknown_can_grant_bank_transition_readiness",
        "production-binding-record#/unknown_can_grant_full",
        "production-binding-record#/unknown_can_grant_known_non_full",
        "production-binding-record#/world_state_authority_allowed",
    ):
        assert required_values[pointer] is False
    assert required_values["production-binding-record#/validation_failure_remains_failure"] is True

    values = _synthetic_contract_values(proposal)
    assert _contract_violations(proposal, values) == []
    mutations: tuple[tuple[str, object], ...] = (
        ("terminal-result#/terminal_status", "conformance-failed-permanent"),
        ("terminal-result#/detector_conformance_passed", False),
        ("approval-request#/approval_registry_modified", True),
        ("source-approval-entry#/status", "rejected"),
        ("production-binding-approval#/activation_allowed", True),
        ("production-binding-approval#/schema", "foreign-schema"),
        (
            "production-binding-approval#/production_identity_proposal/detector_identity/status",
            "unreviewed",
        ),
        ("production-binding-record#/activation_allowed", True),
        ("production-binding-record#/unknown_can_grant_full", True),
        ("production-binding-record#/source_approval_git_commit_sha", "f" * 40),
        ("production-binding-record#/reviewer_truth_sha256", "f" * 64),
        ("production-binding-record#/frozen_candidate_sha256", "f" * 64),
        ("production-binding-record#/build_identity_sha256", "f" * 64),
        ("production-binding-record#/profile_identity_sha256", "f" * 64),
        ("input#/current_inputs/authorization/capture_execution_head_sha", "f" * 40),
    )
    for pointer, replacement in mutations:
        mutated = dict(values)
        mutated[pointer] = replacement
        assert _contract_violations(proposal, mutated), pointer

    extra = dict(values)
    extra["production-binding-approval#/undeclared_authority"] = True
    assert "production-binding-approval#/undeclared_authority:undeclared-member" in (
        _contract_violations(proposal, extra)
    )
    missing = dict(values)
    missing.pop("production-binding-approval#/production_identity_proposal/build_identity/build_id")
    assert _contract_violations(proposal, missing)

    coherent_rebind = dict(values)
    for binding_name in (
        "production-build-identity",
        "production-capture-environment-identity",
        "production-detector-identity",
        "production-inventory-configuration-identity",
        "production-observation-adapter-identity",
        "production-profile-identity",
    ):
        binding = next(
            _mapping(item)
            for item in _sequence(proposal["required_cross_bindings"])
            if _mapping(item)["id"] == binding_name
        )
        for member in _sequence(binding["members"]):
            member_text = str(member)
            if member_text.startswith("input#/current_inputs/production_binding/") or (
                member_text.startswith("production-binding-record#")
            ):
                coherent_rebind[member_text] = f"coherent-rebound-{binding_name}"
    violations = _contract_violations(proposal, coherent_rebind)
    assert any(
        violation.startswith("production-") and violation.endswith(":mismatch")
        for violation in violations
    )


def test_c4_production_identities_are_canonical_source_reviewed_documents() -> None:
    proposal = _read_mapping(PROPOSAL_PATH)
    contracts = _mapping(proposal["production_identity_contracts"])
    assert set(contracts) == {
        "build_identity_sha256",
        "capture_environment_identity_sha256",
        "detector_identity_sha256",
        "inventory_configuration_identity_sha256",
        "observation_adapter_identity_sha256",
        "profile_identity_sha256",
    }
    for raw_contract in contracts.values():
        contract = _mapping(raw_contract)
        assert contract["hash_formula"] == ("sha256-canonical-json-under-canonicalization_contract")
        assert contract["exact_required_field_set"] is True
        assert contract["unknown_members_allowed"] is False
        assert contract["source_state"] == ("unresolved-until-source-reviewed-production-binding")
        fields = list(_sequence(contract["required_fields"]))
        assert fields == sorted(fields)
        assert len(fields) == len(set(fields))
        assert "/schema" in fields
        assert "/status" in fields
        assert "/source_approval_git_commit_sha" in fields

    source_blob_contract = {
        "anchor_commit_binding_id": "production-build-git-commit",
        "blob_resolution": ("git-rev-parse-anchor-commit-colon-path-must-equal-git_blob"),
        "entry_exact_keys": ["git_blob", "path"],
        "git_blob_format": "lowercase-40-hex",
        "locked_paths_must_match_protocol_v2_lock": True,
        "path_contract": ("normalized-repository-relative-posix-no-absolute-no-dotdot"),
        "set_contract": ("nonempty-sorted-by-path-no-duplicate-path-no-unreviewed-add-or-drop"),
    }
    for name in (
        "build_identity_sha256",
        "detector_identity_sha256",
        "observation_adapter_identity_sha256",
    ):
        assert _mapping(contracts[name])["source_git_blobs_contract"] == (source_blob_contract)
    assert (
        "/build_git_commit_sha" in _mapping(contracts["build_identity_sha256"])["required_fields"]
    )
    for name in ("detector_identity_sha256", "observation_adapter_identity_sha256"):
        assert "/implementation_git_commit_sha" in _mapping(contracts[name])["required_fields"]

    bindings = {
        str(_mapping(item)["id"]): _mapping(item)
        for item in _sequence(proposal["required_cross_bindings"])
    }
    assert (
        sum(
            binding_id == source_blob_contract["anchor_commit_binding_id"]
            for binding_id in bindings
        )
        == 1
    )
    build_commit_members = set(_sequence(bindings["production-build-git-commit"]["members"]))
    assert {
        "input#/current_inputs/production_binding/build_git_commit_sha",
        "input#/current_inputs/source_approval/git_commit_sha",
        "source-approval-registry#git-commit-sha",
        "production-binding-approval#/production_build_git_commit_sha",
        "production-binding-approval#/production_identity_proposal/build_identity/build_git_commit_sha",
        "production-binding-approval#/production_identity_proposal/detector_identity/implementation_git_commit_sha",
        "production-binding-approval#/production_identity_proposal/observation_adapter_identity/implementation_git_commit_sha",
        "production-binding-record#/build_identity/build_git_commit_sha",
        "production-binding-record#/detector_identity/implementation_git_commit_sha",
        "production-binding-record#/observation_adapter_identity/implementation_git_commit_sha",
    } <= build_commit_members

    binding_id_spec = _mapping(proposal["input_derivations"])[
        "current_inputs.production_binding.binding_id"
    ]
    assert binding_id_spec == {
        "digest_algorithm": "sha256",
        "digest_hex_slice": [0, 24],
        "formula": ("output_prefix-plus-digest-hex-slice-of-canonical-source-with-omitted-members"),
        "omitted_top_level_members": ["binding_id"],
        "output_prefix": "inventory-v3-production-binding-",
        "source": PRODUCTION_RECORD_PATH + "#",
    }
    approval_id_spec = _mapping(proposal["input_derivations"])[
        "current_inputs.production_binding_approval.approval_id"
    ]
    assert approval_id_spec == {
        "digest_algorithm": "sha256",
        "digest_hex_slice": [0, 24],
        "formula": ("output_prefix-plus-digest-hex-slice-of-canonical-source-with-omitted-members"),
        "omitted_top_level_members": ["approval_id"],
        "output_prefix": "inventory-v3-production-binding-approval-",
        "source": PRODUCTION_APPROVAL_RECORD_PATH + "#",
    }

    binding_one = {
        "binding_id": "ignored",
        "schema": "test-v1",
        "value": "one",
    }
    approval_one = {
        "approval_id": "ignored",
        "schema": "test-v1",
        "value": "one",
    }
    full_one = _sha256(_canonical_json_bytes({"schema": "test-v1", "value": "one"}))
    assert full_one == "aaf4b421609ae468efd76e9e2ac09ed1c52bd9ea9460b36fdbb47ac6f2e67479"
    expected_binding = "inventory-v3-production-binding-aaf4b421609ae468efd76e9e"
    expected_approval = "inventory-v3-production-binding-approval-aaf4b421609ae468efd76e9e"
    assert _derive_prefixed_id(binding_one, binding_id_spec) == expected_binding
    assert _derive_prefixed_id(approval_one, approval_id_spec) == expected_approval

    binding_two = dict(binding_one, binding_id=expected_binding, value="two")
    full_two = _sha256(_canonical_json_bytes({"schema": "test-v1", "value": "two"}))
    assert full_two == "2af3e95daadca835fe2d45c8d589641956af7d72dcfa0496c2012a4ca3142ebd"
    assert _derive_prefixed_id(binding_two, binding_id_spec) == (
        "inventory-v3-production-binding-2af3e95daadca835fe2d45c8"
    )
    assert binding_two["binding_id"] != _derive_prefixed_id(binding_two, binding_id_spec)
    assert expected_binding != expected_approval
    assert not expected_approval.startswith("inventory-v3-production-binding-aaf4")

    malformed_ids = (
        expected_approval,
        "foreign-" + expected_binding,
        expected_binding.upper(),
        expected_binding[:-1],
        expected_binding + "0",
    )
    assert all(
        candidate != _derive_prefixed_id(binding_one, binding_id_spec)
        for candidate in malformed_ids
    )
    nested_one = dict(binding_one, nested={"binding_id": "one"})
    nested_two = dict(binding_one, nested={"binding_id": "two"})
    assert _derive_prefixed_id(nested_one, binding_id_spec) != _derive_prefixed_id(
        nested_two, binding_id_spec
    )
    supplied_id_changed = dict(binding_one, binding_id="different-top-level-id")
    assert _derive_prefixed_id(supplied_id_changed, binding_id_spec) == expected_binding


def test_c4_source_git_blobs_are_exact_commit_path_bound(tmp_path: Path) -> None:
    repository = tmp_path / "source-repository"
    repository.mkdir()

    def git(*arguments: str) -> str:
        return subprocess.run(
            ("git", "-C", str(repository), *arguments),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init")
    git("config", "user.name", "Inventory C4 Test")
    git("config", "user.email", "inventory-c4@example.invalid")
    (repository / "a").mkdir()
    (repository / "b").mkdir()
    (repository / "a" / "one.py").write_text("same bytes\n", encoding="ascii")
    (repository / "b" / "same.py").write_text("same bytes\n", encoding="ascii")
    (repository / "b" / "two.py").write_text("two\n", encoding="ascii")
    git("add", "a/one.py", "b/same.py", "b/two.py")
    git("commit", "-m", "first")
    commit_a = git("rev-parse", "HEAD")
    approved = [
        {"git_blob": git("rev-parse", f"{commit_a}:a/one.py"), "path": "a/one.py"},
        {"git_blob": git("rev-parse", f"{commit_a}:b/two.py"), "path": "b/two.py"},
    ]
    assert (
        _source_git_blobs_violations(repository, commit_a, approved, approved_entries=approved)
        == []
    )

    rebound_same_bytes = [
        {"git_blob": approved[0]["git_blob"], "path": "b/same.py"},
        approved[1],
    ]
    assert _source_git_blobs_violations(repository, commit_a, rebound_same_bytes) == []
    assert "approved-set-mismatch" in _source_git_blobs_violations(
        repository,
        commit_a,
        rebound_same_bytes,
        approved_entries=approved,
    )
    assert _sha256(_canonical_json_bytes({"source_git_blobs": approved})) != _sha256(
        _canonical_json_bytes({"source_git_blobs": rebound_same_bytes})
    )

    (repository / "a" / "one.py").write_text("changed\n", encoding="ascii")
    git("add", "a/one.py")
    git("commit", "-m", "second")
    commit_b = git("rev-parse", "HEAD")
    assert "a/one.py:blob-mismatch" in _source_git_blobs_violations(repository, commit_b, approved)

    invalid_cases: tuple[object, ...] = (
        [],
        list(reversed(approved)),
        [approved[0], approved[0]],
        [{"git_blob": str(approved[0]["git_blob"]).upper(), "path": "a/one.py"}],
        [{"git_blob": "f" * 39, "path": "a/one.py"}],
        [{"git_blob": approved[0]["git_blob"], "path": "/a/one.py"}],
        [{"git_blob": approved[0]["git_blob"], "path": "a\\one.py"}],
        [{"git_blob": approved[0]["git_blob"], "path": "a/../a/one.py"}],
        [{"git_blob": approved[0]["git_blob"], "path": "missing.py"}],
        [{"git_blob": approved[0]["git_blob"], "path": "a/one.py", "extra": True}],
        [{"git_blob": approved[0]["git_blob"]}],
    )
    for entries in invalid_cases:
        assert _source_git_blobs_violations(repository, commit_a, entries), entries

    tree_entry = [{"git_blob": git("rev-parse", f"{commit_a}:a"), "path": "a"}]
    assert "a:not-blob" in _source_git_blobs_violations(repository, commit_a, tree_entry)
    assert _source_git_blobs_violations(repository, "A" * 40, approved) == ["anchor-commit-format"]


def test_c4_approval_commitment_rejects_coherent_identity_transplants() -> None:
    proposal = _read_mapping(PROPOSAL_PATH)
    derivations = _mapping(proposal["input_derivations"])
    approval_id_spec = _mapping(
        derivations["current_inputs.production_binding_approval.approval_id"]
    )
    binding_id_spec = _mapping(derivations["current_inputs.production_binding.binding_id"])
    identity_names = (
        "build_identity",
        "capture_environment_identity",
        "detector_identity",
        "inventory_configuration_identity",
        "observation_adapter_identity",
        "profile_identity",
    )

    def identity_proposal(label: str) -> dict[str, object]:
        identities = {
            name: {
                "label": label,
                "schema": f"synthetic-{name}-v1",
                "source_approval_git_commit_sha": "a" * 40,
            }
            for name in identity_names
        }
        hashes = {
            f"{name}_sha256": _sha256(_canonical_json_bytes(identity))
            for name, identity in identities.items()
        }
        return {
            **identities,
            **hashes,
            "production_build_git_commit_sha": "a" * 40,
            "schema": "inventory-positive-v3-production-identity-proposal-v1",
        }

    def approval(label: str) -> tuple[dict[str, object], str]:
        identity_bundle = identity_proposal(label)
        record: dict[str, object] = {
            "approval_id": "pending",
            "production_identity_proposal": identity_bundle,
            "production_identity_proposal_sha256": _sha256(_canonical_json_bytes(identity_bundle)),
            "schema": "inventory-positive-v3-production-binding-approval-v1",
            "source_approval_git_commit_sha": "a" * 40,
            "status": "approved-for-source-owned-production-binding",
        }
        record["approval_id"] = _derive_prefixed_id(record, approval_id_spec)
        return record, _sha256(_canonical_json_bytes(record))

    def binding(
        approved_record: Mapping[str, object],
        approved_record_sha256: str,
        identity_label: str,
    ) -> dict[str, object]:
        identity_bundle = identity_proposal(identity_label)
        record: dict[str, object] = {
            "binding_id": "pending",
            "production_binding_approval_id": approved_record["approval_id"],
            "production_binding_approval_record_sha256": approved_record_sha256,
            "production_identity_proposal_sha256": _sha256(_canonical_json_bytes(identity_bundle)),
            "schema": "inventory-positive-v3-production-binding-v1",
            "status": ("source-approved-production-binding-release-receipt-required-nonactivating"),
            **{name: identity_bundle[name] for name in identity_names},
            **{f"{name}_sha256": identity_bundle[f"{name}_sha256"] for name in identity_names},
        }
        record["binding_id"] = _derive_prefixed_id(record, binding_id_spec)
        return record

    def pair_violations(
        approved_record: Mapping[str, object],
        approved_record_sha256: str,
        binding_record: Mapping[str, object],
    ) -> list[str]:
        violations: list[str] = []
        identity_bundle = _mapping(approved_record["production_identity_proposal"])
        identity_digest = _sha256(_canonical_json_bytes(identity_bundle))
        if approved_record["production_identity_proposal_sha256"] != identity_digest:
            violations.append("approval-identity-proposal-digest")
        if approved_record["approval_id"] != _derive_prefixed_id(approved_record, approval_id_spec):
            violations.append("approval-id")
        if approved_record_sha256 != _sha256(_canonical_json_bytes(approved_record)):
            violations.append("approval-record-digest")
        if binding_record["production_binding_approval_id"] != approved_record["approval_id"]:
            violations.append("approval-id-binding")
        if binding_record["production_binding_approval_record_sha256"] != (approved_record_sha256):
            violations.append("approval-record-binding")
        if binding_record["production_identity_proposal_sha256"] != identity_digest:
            violations.append("identity-proposal-binding")
        if binding_record["binding_id"] != _derive_prefixed_id(binding_record, binding_id_spec):
            violations.append("binding-id")
        return violations

    approval_a, approval_a_sha = approval("A")
    binding_a = binding(approval_a, approval_a_sha, "A")
    assert pair_violations(approval_a, approval_a_sha, binding_a) == []

    binding_b_transplanted_onto_approval_a = binding(approval_a, approval_a_sha, "B")
    assert pair_violations(
        approval_a,
        approval_a_sha,
        binding_b_transplanted_onto_approval_a,
    ) == ["identity-proposal-binding"]

    approval_b, approval_b_sha = approval("B")
    assert pair_violations(approval_b, approval_b_sha, binding_a) == [
        "approval-id-binding",
        "approval-record-binding",
        "identity-proposal-binding",
    ]

    mutated_approval = json.loads(json.dumps(approval_a))
    assert isinstance(mutated_approval, dict)
    mutated_bundle = mutated_approval["production_identity_proposal"]
    assert isinstance(mutated_bundle, dict)
    mutated_build = mutated_bundle["build_identity"]
    assert isinstance(mutated_build, dict)
    mutated_build["label"] = "mutated-after-approval"
    assert pair_violations(mutated_approval, approval_a_sha, binding_a)


def test_c4_source_actions_are_separate_non_authorizing_commits() -> None:
    proposal = _read_mapping(PROPOSAL_PATH)
    assert proposal["chronology_contract"] == [
        "live-authorization-commit-strictly-after-exact-l2",
        "capture-execution-head-exactly-live-authorization-commit",
        "campaign-start-strictly-after-live-authorization-commit",
        "acquisition-finalization-strictly-after-source-completion",
        "independent-review-strictly-after-acquisition-finalization",
        "terminal-evaluation-strictly-after-independent-review",
        "proposed-approval-strictly-after-review-and-terminal-evaluation",
        "source-approval-commit-strictly-after-proposed-approval",
        "production-binding-approval-commit-strictly-after-source-approval-commit",
        "production-binding-commit-strictly-after-production-binding-approval-commit",
    ]
    assert proposal["source_approval_action_contract"] == {
        "canonical_json_contract_source": "canonicalization_contract",
        "changed_paths": [
            "validation/inventory-positive-v3/approved-campaigns.json",
            "validation/inventory-positive-v3/approved-campaigns.json.sha256",
        ],
        "direct_child_of_capture_execution_head_required": True,
        "exact_changed_path_set_required": True,
        "ordinary_non_merge_commit_required": True,
        "parent_git_commit_sha_source": "current_inputs.authorization.git_commit_sha",
        "production_binding_may_share_commit": False,
        "proposed_source_files_must_match_byte_for_byte": True,
        "registry_entry_must_equal_proposed_approval_byte_projection": True,
        "sidecar_ascii_template": "{registry_sha256}  approved-campaigns.json\n",
        "source_write_performed_by_this_package": False,
    }
    assert proposal["production_binding_action_contract"] == {
        "activation_allowed": False,
        "binding_id_excluded_record_members": ["binding_id"],
        "binding_id_formula_source": (
            "input_derivations.current_inputs.production_binding.binding_id"
        ),
        "canonical_json_contract_source": "canonicalization_contract",
        "changed_paths": [
            PRODUCTION_RECORD_PATH,
            PRODUCTION_SIDECAR_PATH,
        ],
        "changed_paths_are_literal_not_input_derived": True,
        "controller_authority_allowed": False,
        "direct_child_of_production_binding_approval_commit_required": True,
        "exact_changed_path_set_required": True,
        "input_authority_allowed": False,
        "later_path_touch_allowed": False,
        "new_paths_required": True,
        "ordinary_non_merge_commit_required": True,
        "parent_git_commit_sha_source": (
            "current_inputs.production_binding_approval.git_commit_sha"
        ),
        "promotion_allowed": False,
        "record_exact_member_set_required": True,
        "record_git_blobs_must_match_committed_bytes": True,
        "record_path": PRODUCTION_RECORD_PATH,
        "record_required_locators_source": (
            "required_source_artifact_fields.production-binding-record"
        ),
        "record_schema": "inventory-positive-v3-production-binding-v1",
        "record_sidecar_path": PRODUCTION_SIDECAR_PATH,
        "record_status": (
            "source-approved-production-binding-release-receipt-required-nonactivating"
        ),
        "record_unknown_members_allowed": False,
        "separate_source_commit_required": True,
        "sidecar_ascii_template": "{record_sha256}  production-binding.json\n",
        "sidecar_must_hash_exact_record_bytes": True,
        "sidecar_path_is_record_path_plus_dot_sha256": True,
        "source_approval_and_production_binding_same_commit_allowed": False,
        "source_write_performed_by_this_package": False,
        "unique_introduction_required": True,
        "world_state_authority_allowed": False,
    }
    assert proposal["production_binding_approval_action_contract"] == {
        "activation_allowed": False,
        "approval_id_formula_source": (
            "input_derivations.current_inputs.production_binding_approval.approval_id"
        ),
        "build_git_commit_sha_source": ("current_inputs.production_binding.build_git_commit_sha"),
        "canonical_json_contract_source": "canonicalization_contract",
        "changed_paths": [
            PRODUCTION_APPROVAL_RECORD_PATH,
            PRODUCTION_APPROVAL_SIDECAR_PATH,
        ],
        "changed_paths_are_literal_not_input_derived": True,
        "controller_authority_allowed": False,
        "direct_child_of_source_approval_commit_required": True,
        "exact_changed_path_set_required": True,
        "input_authority_allowed": False,
        "later_path_touch_allowed": False,
        "new_paths_required": True,
        "ordinary_non_merge_commit_required": True,
        "parent_git_commit_sha_source": "current_inputs.source_approval.git_commit_sha",
        "production_identity_proposal_exact_member_sets_required": True,
        "production_identity_proposal_unknown_members_allowed": False,
        "promotion_allowed": False,
        "protocol_v2_repository_verification_at_build_commit_required": True,
        "record_exact_member_set_required": True,
        "record_git_blobs_must_match_committed_bytes": True,
        "record_path": PRODUCTION_APPROVAL_RECORD_PATH,
        "record_required_locators_source": (
            "required_source_artifact_fields.production-binding-approval"
        ),
        "record_schema": "inventory-positive-v3-production-binding-approval-v1",
        "record_sidecar_path": PRODUCTION_APPROVAL_SIDECAR_PATH,
        "record_status": "approved-for-source-owned-production-binding",
        "record_unknown_members_allowed": False,
        "sidecar_ascii_template": ("{record_sha256}  production-binding-approval.json\n"),
        "sidecar_must_hash_exact_record_bytes": True,
        "sidecar_path_is_record_path_plus_dot_sha256": True,
        "source_write_performed_by_this_package": False,
        "unique_introduction_required": True,
        "world_state_authority_allowed": False,
    }
    production_inputs = _mapping(_mapping(proposal["current_inputs"])["production_binding"])
    assert "inventory_release_receipt_id" not in production_inputs
    assert "issue_14_consumer_contract_sha256" not in production_inputs
    assert "activation_record_id" not in production_inputs
    assert set(_mapping(_mapping(proposal["current_inputs"])["environment"])) == {
        "capture_environment_sha256",
        "producer_identity_sha256",
    }


def test_c4_descendant_preserves_the_locked_v2_repository() -> None:
    head = _git("rev-parse", "HEAD")
    binding = verify_protocol_v2_repository(ROOT, expected_head=head)
    assert binding.source_commit_sha == PROTOCOL_V2_SOURCE
    assert binding.lock_commit_sha == PROTOCOL_V2_LOCK
    assert binding.lock_sha256 == PROTOCOL_V2_LOCK_SHA256
    assert _git("show", "-s", "--format=%P", FROZEN_C3) == PROTOCOL_V2_LOCK

    introduction_commits = _git(
        "log",
        "--full-history",
        "--diff-filter=A",
        "--format=%H",
        head,
        "--",
        "validation/inventory_v3_release_binding_preparation/proposal-input.json",
    ).splitlines()
    assert len(introduction_commits) == 1
    introduction = introduction_commits[0]
    assert _git("show", "-s", "--format=%P", introduction) == FROZEN_C3
    changed_paths = set(
        _git(
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            introduction,
        ).splitlines()
    )
    assert changed_paths == {
        "tests/test_inventory_v3_release_binding_preparation.py",
        "validation/inventory_v3_release_binding_preparation/package-tree.json",
        "validation/inventory_v3_release_binding_preparation/package-tree.json.sha256",
        "validation/inventory_v3_release_binding_preparation/proposal-input.json",
        "validation/inventory_v3_release_binding_preparation/proposal-input.json.sha256",
    }
    later_touches = _git(
        "log",
        "--format=%H",
        f"{introduction}..{head}",
        "--",
        *sorted(changed_paths),
    )
    assert later_touches == ""
    subprocess.run(
        ("git", "-C", str(ROOT), "merge-base", "--is-ancestor", FROZEN_C3, head),
        check=True,
        capture_output=True,
    )
