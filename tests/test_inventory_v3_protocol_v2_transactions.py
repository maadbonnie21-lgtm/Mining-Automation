from __future__ import annotations

import ast
import hashlib
import inspect
import json
import os
import subprocess
import sys
import textwrap
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from mining_automation.perception.inventory import (
    positive_v3_independent_validation as frozen_validation,
)
from validation.inventory_v3_protocol_v2 import privacy, producer
from validation.inventory_v3_protocol_v2 import protocol as v2
from validation.inventory_v3_protocol_v2.package_tree import PackageTreeError

_AUTHORIZATION_ID = "2" * 64
_PROTOCOL_SOURCE_HEAD = "a" * 40
_PROTOCOL_LOCK_HEAD = "b" * 40
_PROTOCOL_LOCK_SHA256 = "d" * 64
_EXECUTION_HEAD = "c" * 40
_AUTHORIZATION_HEAD = "e" * 40
_OPAQUE_RECEIPT_ID = "123e4567-e89b-42d3-a456-426614174000"


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_canonical_fixture(
    path: Path,
    value: Mapping[str, object],
    *,
    sidecar: bool = True,
) -> bytes:
    payload = _canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    if sidecar:
        path.with_suffix(path.suffix + ".sha256").write_bytes(
            f"{_sha256(payload)}  {path.name}\n".encode("ascii")
        )
    return payload


def _create_dangling_reparse(
    path: Path,
    request: pytest.FixtureRequest,
    *,
    directory: bool,
) -> None:
    missing_target = path.parent / f"missing-target-for-{path.name}"
    try:
        path.symlink_to(missing_target, target_is_directory=directory)
    except (NotImplementedError, OSError) as exc:
        if os.name != "nt":
            pytest.skip(f"dangling symlink creation is unavailable: {exc}")
        junction_target = path.parent / f"temporary-target-for-{path.name}"
        junction_target.mkdir()
        completed = subprocess.run(
            (
                "cmd",
                "/d",
                "/c",
                "mklink",
                "/J",
                str(path),
                str(junction_target),
            ),
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            pytest.skip(
                "dangling symlink and junction creation are unavailable: "
                f"{completed.stderr or completed.stdout}"
            )
        junction_target.rmdir()

    def remove_reparse() -> None:
        try:
            if path.is_symlink():
                path.unlink()
            else:
                os.rmdir(path)
        except FileNotFoundError:
            pass

    request.addfinalizer(remove_reparse)
    assert path.exists() is False
    assert path.is_symlink() or v2._is_reparse(path.lstat())


def _prepare_authorization_proposal_environment(
    temporary_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path, v2.ProtocolV2LockBinding]:
    root = temporary_root / "r"
    root.mkdir()
    attempt_base = temporary_root / "a"
    protocol = replace(_protocol(root), evaluator_head_sha=_PROTOCOL_LOCK_HEAD)
    _write_canonical_fixture(
        root.joinpath(*v2._LIVE_AUTHORIZATION_PATH.parts),
        {
            "activation_allowed": False,
            "authorizations": [],
            "schema": v2.LIVE_AUTHORIZATION_SCHEMA,
        },
        sidecar=False,
    )
    _write_canonical_fixture(
        root.joinpath(*v2._V2_LIVE_AUTHORIZATION_PATH.parts),
        {
            "activation_allowed": False,
            "authorizations": [],
            "schema": v2._V2_LIVE_AUTHORIZATION_SCHEMA,
        },
    )
    monkeypatch.setattr(
        v2,
        "verify_protocol_v2_repository",
        lambda _root, *, expected_head: protocol,
    )
    monkeypatch.setattr(
        v2,
        "_verify_approval_registry_absent",
        lambda _protocol: b"synthetic absent approval registry\n",
    )
    monkeypatch.setattr(
        producer,
        "observe_windows_identity",
        lambda: producer.WindowsProducerIdentity("HOST", "user", 1),
    )
    return root, attempt_base, protocol


def _protocol(root: Path) -> v2.ProtocolV2LockBinding:
    return v2.ProtocolV2LockBinding(
        repository_root=root.resolve(strict=True),
        evaluator_head_sha=_EXECUTION_HEAD,
        source_commit_sha=_PROTOCOL_SOURCE_HEAD,
        lock_commit_sha=_PROTOCOL_LOCK_HEAD,
        lock_sha256=_PROTOCOL_LOCK_SHA256,
        locked_git_blobs=(),
    )


def _authorization() -> v2.LiveAuthorizationBinding:
    return v2.LiveAuthorizationBinding(
        authorization_id=_AUTHORIZATION_ID,
        git_commit_sha=_AUTHORIZATION_HEAD,
        legacy_registry_git_blob="f" * 40,
        protocol_v2_registry_git_blob="1" * 40,
        committed_at_utc="2098-12-31T23:59:45.000000Z",
        opaque_receipt_id=_OPAQUE_RECEIPT_ID,
    )


def _paths(root: Path, attempt_base: Path) -> v2.ProtocolV2Paths:
    return v2.ProtocolV2Paths(
        repository_root=root.resolve(strict=True),
        authorization_id=_AUTHORIZATION_ID,
        source_campaign_root=root / "source",
        workspace_root=root / "workspace",
        acquisition_root=root / "workspace" / "a",
        review_intake_root=root / "workspace" / "ri",
        reviewed_package_root=root / "workspace" / "rp",
        approval_request_root=root / "workspace" / "ar",
        result_root=root / "result",
        attempt_root=attempt_base / "attempts",
        attempt_base_root=attempt_base,
    )


def _exact_unresolved_paths(
    repository_root: Path,
    attempt_base: Path,
) -> v2.ProtocolV2Paths:
    workspace = repository_root.joinpath(*v2._V2_WORKSPACE_ROOT.parts) / _AUTHORIZATION_ID
    attempt_identity = hashlib.sha256(
        f"{_PROTOCOL_LOCK_SHA256}:{_AUTHORIZATION_ID}".encode("ascii")
    ).hexdigest()
    return v2.ProtocolV2Paths(
        repository_root=repository_root,
        authorization_id=_AUTHORIZATION_ID,
        source_campaign_root=(
            repository_root.joinpath(*v2._SOURCE_OUTPUT_ROOT.parts) / _AUTHORIZATION_ID
        ),
        workspace_root=workspace,
        acquisition_root=workspace / v2._WORKSPACE_ACQUISITION_DIR,
        review_intake_root=workspace / v2._WORKSPACE_REVIEW_INTAKE_DIR,
        reviewed_package_root=workspace / v2._WORKSPACE_REVIEWED_PACKAGE_DIR,
        approval_request_root=workspace / v2._WORKSPACE_APPROVAL_REQUEST_DIR,
        result_root=(repository_root.joinpath(*v2._RESULT_OUTPUT_ROOT.parts) / _AUTHORIZATION_ID),
        attempt_root=(attempt_base / "Mining-Automation" / "iv3v2" / attempt_identity),
        attempt_base_root=attempt_base,
    )


def _utf16_units(path: Path) -> int:
    return len(str(Path(os.path.abspath(path))).encode("utf-16-le")) // 2


def _fixed_budget_envelope(
    paths: v2.ProtocolV2Paths,
) -> tuple[set[Path], set[Path]]:
    files: set[Path] = set()
    directories: set[Path] = {
        paths.source_campaign_root,
        paths.workspace_root,
        paths.acquisition_root,
        paths.review_intake_root,
        paths.reviewed_package_root,
        paths.approval_request_root,
        paths.result_root,
        paths.attempt_root,
    }

    def add_file(path: Path, *, boundary: Path) -> None:
        files.add(path)
        current = path.parent
        while current != boundary.parent:
            directories.add(current)
            if current == boundary:
                break
            current = current.parent

    def add_tree(
        root: Path,
        roles: Mapping[str, str],
        *,
        packaged: bool,
    ) -> None:
        directories.add(root)
        for relative in roles:
            add_file(root.joinpath(*PurePosixPath(relative).parts), boundary=root)
        if packaged:
            add_file(root / v2._PACKAGE_TREE_NAME, boundary=root)
            add_file(root / f"{v2._PACKAGE_TREE_NAME}.sha256", boundary=root)

    add_tree(paths.source_campaign_root, v2._source_allowlist(), packaged=False)
    add_tree(paths.acquisition_root, v2._acquisition_roles(), packaged=True)
    add_tree(
        paths.review_intake_root / "package",
        v2._review_intake_roles(),
        packaged=True,
    )
    add_tree(
        paths.review_intake_root / "submission",
        v2._review_submission_roles(),
        packaged=True,
    )
    add_tree(
        paths.reviewed_package_root,
        v2._reviewed_package_roles(),
        packaged=True,
    )
    result_roles = v2._result_roles(conformance_passed=True)
    result_roles.update(v2._result_roles(conformance_passed=False))
    add_tree(paths.result_root, result_roles, packaged=True)
    add_tree(
        paths.approval_request_root,
        v2._approval_request_roles(),
        packaged=True,
    )
    for operation in v2._ATTEMPT_OPERATIONS:
        for suffix in (
            "reserved.json",
            "reserved.json.sha256",
            "terminal.json",
            "terminal.json.sha256",
        ):
            add_file(
                paths.attempt_root / f"{operation}-{suffix}",
                boundary=paths.attempt_root,
            )
        add_tree(
            paths.attempt_root / f"{operation}-failure",
            v2._operation_failure_roles(),
            packaged=True,
        )
    attempt_anchor = paths.attempt_base_root or paths.attempt_root.parent
    legacy_reservation_root = (
        attempt_anchor / "Mining-Automation" / "inventory-positive-v3-independent-reservations"
    )
    add_file(
        legacy_reservation_root / f"{v2.PROTOCOL_V1_LOCK_SHA256}.json",
        boundary=legacy_reservation_root,
    )
    return files, directories


def _prepare_evaluator_snapshot_harness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[v2.SourceMetadataBinding, Path]:
    root = tmp_path / "repository"
    root.mkdir()
    protocol = _protocol(root)
    authorization = _authorization()
    paths = _paths(root, tmp_path / "local-app-data")
    session_id = f"inventory-v3-independent-{_AUTHORIZATION_ID}"
    source = v2.SourceMetadataBinding(
        paths=paths,
        protocol=protocol,
        authorization=authorization,
        session={
            "campaign_id": v2._legacy_source_campaign_id(session_id),
            "operator": "operator-a",
            "session_id": session_id,
        },
        session_payload=b"synthetic private source session\n",
        completion_seal={},
        completion_payload=b"synthetic private completion seal\n",
        producer_attestation={},
        capture_reports=(),
        owned_frame_reports=(),
        source_files=(),
        source_metadata_snapshot=(),
    )
    campaign_id = v2._content_bound_campaign_id(session_id)
    (
        _,
        evaluator_session_payload,
        _,
        evaluator_seal_payload,
    ) = v2._evaluator_compatible_source_documents(source, campaign_id)
    reviewed_root = paths.reviewed_package_root
    roles = v2._reviewed_package_roles()
    for relative in roles:
        if relative.endswith(".sha256"):
            continue
        path = reviewed_root.joinpath(*PurePosixPath(relative).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"synthetic evaluator input: {relative}\n".encode("ascii"))

    manifest_cases: list[dict[str, object]] = []
    for index in range(1, len(v2.REQUIRED_STAGES) + 1):
        capture_id = f"capture-{index}"
        manifest_cases.append(
            {
                "capture_id": capture_id,
                "case_id": f"{session_id}/{capture_id}",
                "frame_region": {"sha256": f"{index:064x}"},
                "session_id": session_id,
            }
        )
    manifest: Mapping[str, object] = {
        "campaign_id": campaign_id,
        "cases": manifest_cases,
        "dataset_id": "dataset-a",
        "finalized_at_utc": "2025-01-01T00:30:00.000000Z",
        "operator": "operator-a",
        "schema": v2._CAMPAIGN_MANIFEST_SCHEMA,
        "session_id": session_id,
    }
    manifest_payload = _canonical_bytes(manifest)
    (reviewed_root / v2._CAMPAIGN_MANIFEST_NAME).write_bytes(manifest_payload)
    manifest_sha = _sha256(manifest_payload)
    acquisition_tree_payload = b"synthetic acquisition package tree\n"
    acquisition_tree_sha = _sha256(acquisition_tree_payload)
    review_intake_tree_sha = "2" * 64
    submission_cases: list[dict[str, object]] = []
    for index in range(1, len(v2.REQUIRED_STAGES) + 1):
        submission_cases.append(
            {
                "review_case_id": v2._review_case_id(manifest_sha, index),
                "truth": {
                    "decision": "approved",
                    "drag_visible": False,
                    "hover_visible": False,
                    "occupied_slots": 0,
                    "ordinary_iron_only": False,
                    "quantity_text_visible": False,
                    "review_note": None,
                    "selected_item_visible": False,
                    "visibility": "inventory-visible",
                },
            }
        )
    submission: Mapping[str, object] = {
        "acquisition_package_tree_sha256": acquisition_tree_sha,
        "activation_allowed": False,
        "campaign_manifest_sha256": manifest_sha,
        "cases": submission_cases,
        "operator_labels_used_as_truth": False,
        "promotion_allowed": False,
        "review_intake_tree_sha256": review_intake_tree_sha,
        "reviewed_at_utc": "2025-01-01T00:45:00.000000Z",
        "reviewer": "reviewer-b",
        "schema": v2._REVIEW_SUBMISSION_SCHEMA,
        "truth_source": "independent-human-review",
    }
    submission_root = paths.review_intake_root / "submission"
    submission_payload = _write_canonical_fixture(
        submission_root / v2._REVIEW_SUBMISSION_NAME,
        submission,
    )
    submission_tree_sha, _ = v2._write_tree_document(
        submission_root,
        v2._review_submission_roles(),
    )
    review = v2._project_reviewer_truth(
        manifest,
        manifest_sha,
        submission,
        expected_acquisition_tree_sha256=acquisition_tree_sha,
        expected_review_intake_tree_sha256=review_intake_tree_sha,
    )
    reviewer_truth_payload = _canonical_bytes(review)
    (reviewed_root / v2._REVIEWER_TRUTH_NAME).write_bytes(reviewer_truth_payload)
    reviewer_truth_sha = _sha256(reviewer_truth_payload)
    package: Mapping[str, object] = {
        "activation_allowed": False,
        "campaign_manifest": {
            "path": v2._CAMPAIGN_MANIFEST_NAME,
            "sha256": manifest_sha,
        },
        "dataset_role": "independent-validation-only",
        "preregistration_sha256": v2.PROTOCOL_V1_PREREGISTRATION_SHA256,
        "prototype_eligible": False,
        "reviewer_truth": {
            "path": v2._REVIEWER_TRUTH_NAME,
            "sha256": reviewer_truth_sha,
        },
        "schema": v2._VALIDATION_PACKAGE_SCHEMA,
        "training_allowed": False,
    }
    package_payload = _canonical_bytes(package)
    (reviewed_root / v2._VALIDATION_PACKAGE_NAME).write_bytes(package_payload)
    package_sha = _sha256(package_payload)
    acquisition_record: Mapping[str, object] = {
        "original_source_completion_seal_sha256": _sha256(source.completion_payload),
        "original_source_session_report_sha256": _sha256(source.session_payload),
        "schema": v2._ACQUISITION_SCHEMA,
        "source_completion_seal_sha256": _sha256(evaluator_seal_payload),
        "source_identity_bridge": v2._SOURCE_IDENTITY_BRIDGE,
        "source_session_report_sha256": _sha256(evaluator_session_payload),
    }
    (reviewed_root / "protocol-v2-acquisition.json").write_bytes(
        _canonical_bytes(acquisition_record)
    )
    reviewed_record: Mapping[str, object] = {
        "acquisition_package_tree_sha256": acquisition_tree_sha,
        "activation_allowed": False,
        "authorization_id": authorization.authorization_id,
        "campaign_id": campaign_id,
        "campaign_manifest_sha256": manifest_sha,
        "dataset_id": "dataset-a",
        "operator": "operator-a",
        "promotion_allowed": False,
        "protocol_lock_git_commit_sha": protocol.lock_commit_sha,
        "protocol_lock_sha256": protocol.lock_sha256,
        "review_intake_tree_sha256": review_intake_tree_sha,
        "review_submission_sha256": _sha256(submission_payload),
        "reviewed_at_utc": "2025-01-01T00:45:00.000000Z",
        "reviewer": "reviewer-b",
        "reviewer_truth_sha256": reviewer_truth_sha,
        "schema": v2._REVIEWED_PACKAGE_SCHEMA,
        "status": "reviewed-evaluator-ready",
        "training_allowed": False,
        "validation_package_sha256": package_sha,
    }
    (reviewed_root / "protocol-v2-reviewed-package.json").write_bytes(
        _canonical_bytes(reviewed_record)
    )
    for relative in roles:
        if not relative.endswith(".sha256"):
            continue
        sidecar = reviewed_root.joinpath(*PurePosixPath(relative).parts)
        primary = sidecar.with_suffix("")
        payload = primary.read_bytes()
        sidecar.write_bytes(f"{_sha256(payload)}  {primary.name}\n".encode("ascii"))
    reviewed_tree_sha, _ = v2._write_tree_document(reviewed_root, roles)

    evaluator_index = v2._ATTEMPT_OPERATIONS.index("evaluate-locked-candidate")
    for index, operation in enumerate(
        v2._ATTEMPT_OPERATIONS[:evaluator_index],
        start=1,
    ):
        v2._reserve_attempt(paths, protocol, operation, {})
        v2._record_attempt_terminal(
            paths,
            protocol,
            operation,
            status="passed-terminal",
            contract_id=v2._ATTEMPT_SUCCESS_CONTRACTS[operation],
            output_sha256=f"{index:x}" * 64,
        )

    monkeypatch.setattr(
        v2,
        "verify_protocol_v2_repository",
        lambda _root, *, expected_head: protocol,
    )
    monkeypatch.setattr(
        v2,
        "verify_live_authorization",
        lambda _protocol, *, access_hook=None: authorization,
    )
    monkeypatch.setattr(v2, "preflight_source_metadata", lambda *args, **kwargs: source)
    monkeypatch.setattr(v2, "_recheck_source_metadata", lambda _source: None)
    monkeypatch.setattr(v2, "_assert_workspace_children", lambda _paths, _expected: None)
    monkeypatch.setattr(
        v2,
        "_frozen_development_identity_sets",
        lambda _protocol: (frozenset(), frozenset(), frozenset()),
    )
    original_tree_preflight = v2._preflight_tree_metadata_only

    def preflight_tree(
        package_root: Path,
        expected_roles: Mapping[str, str],
    ) -> tuple[Mapping[str, object], bytes]:
        if package_root == paths.acquisition_root:
            return (
                {
                    "entries": [],
                    "schema": "inventory-positive-v3-independent-package-tree-v1",
                },
                acquisition_tree_payload,
            )
        return original_tree_preflight(package_root, expected_roles)

    monkeypatch.setattr(v2, "_preflight_tree_metadata_only", preflight_tree)
    monkeypatch.setattr(
        v2,
        "_preflight_acquisition_semantics",
        lambda _source, _root, _tree, _payload: (
            manifest,
            acquisition_record,
            manifest_payload,
        ),
    )
    monkeypatch.setattr(
        v2,
        "_require_tree_entry_subset_equal",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        v2,
        "_preflight_review_pipeline_lineage",
        lambda _source, *, require_reviewed: {
            "acquisition_package_tree_sha256": acquisition_tree_sha,
            "campaign_manifest_sha256": manifest_sha,
            "review_intake_package_tree_sha256": review_intake_tree_sha,
            "review_submission_package_tree_sha256": submission_tree_sha,
            "review_submission_sha256": _sha256(submission_payload),
            "reviewed_package_tree_sha256": reviewed_tree_sha,
        },
    )
    return source, reviewed_root / "protocol-v2-acquisition.json"


def _prepare_approval_registry_race_harness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[v2.SourceMetadataBinding, Path, bytes, bytes]:
    source, _ = _prepare_evaluator_snapshot_harness(tmp_path, monkeypatch)
    reviewed_tree_payload = (
        source.paths.reviewed_package_root / v2._PACKAGE_TREE_NAME
    ).read_bytes()
    manifest_payload = (
        source.paths.reviewed_package_root / v2._CAMPAIGN_MANIFEST_NAME
    ).read_bytes()
    package_payload = (
        source.paths.reviewed_package_root / v2._VALIDATION_PACKAGE_NAME
    ).read_bytes()
    manifest = json.loads(manifest_payload)
    result_root = source.paths.result_root
    result_root.mkdir(parents=True)
    private_report: Mapping[str, object] = {
        "detector_conformance_passed": True,
        "schema": "synthetic-frozen-evaluator-report-v1",
    }
    private_report_payload = _write_canonical_fixture(
        result_root / "frozen-evaluator-private-report.json",
        private_report,
    )
    result_record: Mapping[str, object] = {
        "activation_allowed": False,
        "approval_required": True,
        "authorization_id": source.authorization.authorization_id,
        "campaign_id": manifest["campaign_id"],
        "campaign_manifest_sha256": _sha256(manifest_payload),
        "dataset_id": manifest["dataset_id"],
        "detector_conformance_passed": True,
        "evaluated_at_utc": "2025-01-01T01:00:00.000000Z",
        "frozen_evaluator_report_sha256": _sha256(private_report_payload),
        "promotion_allowed": False,
        "protocol_lock_git_commit_sha": source.protocol.lock_commit_sha,
        "protocol_lock_sha256": source.protocol.lock_sha256,
        "retry_allowed": False,
        "reviewed_package_tree_sha256": _sha256(reviewed_tree_payload),
        "schema": "inventory-positive-v3-independent-terminal-result-v2",
        "terminal_status": "conformance-passed-source-approval-required",
        "validation_package_sha256": _sha256(package_payload),
    }
    result_record_payload = _write_canonical_fixture(
        result_root / "protocol-v2-terminal-result.json",
        result_record,
    )
    del result_record_payload
    result_tree_sha, _ = v2._write_tree_document(
        result_root,
        v2._result_roles(conformance_passed=True),
    )
    operation = "evaluate-locked-candidate"
    v2._reserve_attempt(
        source.paths,
        source.protocol,
        operation,
        {
            "campaign_manifest_sha256": _sha256(manifest_payload),
            "opaque_receipt_id": source.authorization.opaque_receipt_id,
            "reviewed_package_tree_sha256": _sha256(reviewed_tree_payload),
            "validation_package_sha256": _sha256(package_payload),
        },
    )
    v2._record_attempt_terminal(
        source.paths,
        source.protocol,
        operation,
        status="passed-terminal",
        contract_id="CONFORMANCE_PASSED_APPROVAL_REQUIRED",
        output_sha256=result_tree_sha,
    )
    registry: Mapping[str, object] = {
        "activation_allowed": False,
        "entries": [],
        "promotion_allowed": False,
        "schema": "inventory-positive-v3-independent-validation-approval-registry-v1",
    }
    registry_path = source.protocol.repository_root.joinpath(*v2._APPROVAL_REGISTRY_PATH.parts)
    registry_payload = _write_canonical_fixture(registry_path, registry)
    registry_sidecar = registry_path.with_suffix(registry_path.suffix + ".sha256")
    return source, registry_path, registry_payload, registry_sidecar.read_bytes()


def _assert_evaluator_integrity_failure(source: v2.SourceMetadataBinding) -> None:
    operation = "evaluate-locked-candidate"
    terminal = json.loads((source.paths.attempt_root / f"{operation}-terminal.json").read_bytes())
    assert terminal["status"] == "failed-terminal"
    assert terminal["contract_id"] == "ATTEMPT_INTEGRITY_FAILURE"
    assert terminal["retry_allowed"] is False
    assert terminal["activation_allowed"] is False
    assert terminal["promotion_allowed"] is False
    assert not (source.paths.result_root / "frozen-evaluator-private-report.json").exists()
    assert not (source.paths.result_root / "protocol-v2-terminal-result.json").exists()
    failure_root = source.paths.attempt_root / f"{operation}-failure"
    assert terminal["output_sha256"] == _sha256((failure_root / v2._PACKAGE_TREE_NAME).read_bytes())


def _assert_approval_integrity_failure(source: v2.SourceMetadataBinding) -> None:
    operation = "prepare-approval-request"
    terminal = json.loads((source.paths.attempt_root / f"{operation}-terminal.json").read_bytes())
    assert terminal["status"] == "failed-terminal"
    assert terminal["contract_id"] == "ATTEMPT_INTEGRITY_FAILURE"
    assert terminal["retry_allowed"] is False
    assert terminal["activation_allowed"] is False
    assert terminal["promotion_allowed"] is False
    failure_root = source.paths.attempt_root / f"{operation}-failure"
    assert terminal["output_sha256"] == _sha256((failure_root / v2._PACKAGE_TREE_NAME).read_bytes())


def _current_approval_time_after_evaluator_terminal(
    source: v2.SourceMetadataBinding,
) -> str:
    terminal = json.loads(
        (source.paths.attempt_root / "evaluate-locked-candidate-terminal.json").read_bytes()
    )
    terminal_at = v2._parse_utc(
        terminal["terminal_at_utc"],
        "synthetic evaluator terminal",
    )
    now = datetime.now(UTC)
    assert now > terminal_at
    return v2._format_utc(now)


def _atomic_replace_bytes(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f"{path.name}.coordinated-swap")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _coherently_rebind_package_member(
    root: Path,
    relative: str,
    replacement_payload: bytes,
) -> tuple[str, str]:
    tree_path = root / v2._PACKAGE_TREE_NAME
    tree_payload = tree_path.read_bytes()
    tree = json.loads(tree_payload)
    member_path = root.joinpath(*PurePosixPath(relative).parts)
    member_sidecar_relative = f"{relative}.sha256"
    member_sidecar_path = root.joinpath(*PurePosixPath(member_sidecar_relative).parts)
    member_sidecar_payload = f"{_sha256(replacement_payload)}  {member_path.name}\n".encode("ascii")
    replacements = {
        relative: replacement_payload,
        member_sidecar_relative: member_sidecar_payload,
    }
    entries = tree["entries"]
    assert isinstance(entries, list)
    replaced: set[str] = set()
    for raw_entry in entries:
        assert isinstance(raw_entry, dict)
        path = raw_entry.get("path")
        if isinstance(path, str) and path in replacements:
            payload = replacements[path]
            raw_entry["sha256"] = _sha256(payload)
            raw_entry["size_bytes"] = len(payload)
            replaced.add(path)
    assert replaced == set(replacements)
    replacement_tree_payload = _canonical_bytes(tree)
    replacement_tree_sidecar = f"{_sha256(replacement_tree_payload)}  {tree_path.name}\n".encode(
        "ascii"
    )

    _atomic_replace_bytes(member_path, replacement_payload)
    _atomic_replace_bytes(member_sidecar_path, member_sidecar_payload)
    _atomic_replace_bytes(tree_path, replacement_tree_payload)
    _atomic_replace_bytes(
        tree_path.with_suffix(tree_path.suffix + ".sha256"),
        replacement_tree_sidecar,
    )
    return _sha256(tree_payload), _sha256(replacement_tree_payload)


def test_canonical_exclusive_writer_publishes_exact_primary_and_sidecar(
    tmp_path: Path,
) -> None:
    target = tmp_path / "closed" / "record.json"
    value: Mapping[str, object] = {
        "activation_allowed": False,
        "schema": "synthetic-transaction-test-v1",
    }
    expected = _canonical_bytes(value)

    digest = v2._write_canonical_exclusive(target, value)

    assert digest == _sha256(expected)
    assert target.read_bytes() == expected
    assert target.with_suffix(".json.sha256").read_bytes() == (
        f"{digest}  record.json\n".encode("ascii")
    )
    assert {path.name for path in target.parent.iterdir()} == {
        "record.json",
        "record.json.sha256",
    }


def test_sidecar_collision_rolls_back_only_owned_primary(
    tmp_path: Path,
) -> None:
    target = tmp_path / "record.json"
    sidecar = target.with_suffix(".json.sha256")
    foreign = b"foreign pre-existing sidecar\n"
    sidecar.write_bytes(foreign)

    with pytest.raises(v2.InventoryV3ProtocolV2Error, match="already exists"):
        v2._write_canonical_exclusive(target, {"schema": "collision-v1"})

    assert not target.exists()
    assert sidecar.read_bytes() == foreign
    assert tuple(tmp_path.iterdir()) == (sidecar,)


def test_authorization_readiness_treats_dangling_legacy_reservation_as_occupied(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    temporary = TemporaryDirectory(prefix="iv3v2-occupancy-")
    request.addfinalizer(temporary.cleanup)
    temporary_root = Path(temporary.name)
    root, attempt_base, protocol = _prepare_authorization_proposal_environment(
        temporary_root,
        monkeypatch,
    )
    reservation = (
        attempt_base
        / "Mining-Automation"
        / "inventory-positive-v3-independent-reservations"
        / f"{v2.PROTOCOL_V1_LOCK_SHA256}.json"
    )
    reservation.parent.mkdir(parents=True)
    _create_dangling_reparse(reservation, request, directory=False)
    assert v2._path_is_occupied(reservation, "legacy reservation") is True

    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="already occupied|redirected or non-directory ancestor",
    ):
        v2.build_live_authorization_proposal(
            root,
            expected_lock_head=protocol.lock_commit_sha,
            opaque_receipt_id=_OPAQUE_RECEIPT_ID,
            attempt_base=attempt_base,
        )

    assert reservation.is_symlink() or v2._is_reparse(reservation.lstat())


def test_authorization_readiness_rejects_dangling_reservation_parent_reparse(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    temporary = TemporaryDirectory(prefix="iv3v2-parent-")
    request.addfinalizer(temporary.cleanup)
    temporary_root = Path(temporary.name)
    root, attempt_base, protocol = _prepare_authorization_proposal_environment(
        temporary_root,
        monkeypatch,
    )
    reservation_parent = (
        attempt_base / "Mining-Automation" / "inventory-positive-v3-independent-reservations"
    )
    reservation_parent.parent.mkdir(parents=True)
    _create_dangling_reparse(reservation_parent, request, directory=True)
    reservation = reservation_parent / f"{v2.PROTOCOL_V1_LOCK_SHA256}.json"
    assert reservation.exists() is False

    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="redirected or non-directory ancestor",
    ):
        v2.build_live_authorization_proposal(
            root,
            expected_lock_head=protocol.lock_commit_sha,
            opaque_receipt_id=_OPAQUE_RECEIPT_ID,
            attempt_base=attempt_base,
        )

    assert reservation_parent.is_symlink() or v2._is_reparse(reservation_parent.lstat())


@pytest.mark.parametrize(
    "collision",
    (
        "legacy-reservation",
        "source-campaign-root",
        "workspace-root",
        "result-root",
        "empty-attempt-root",
    ),
)
def test_post_proposal_capture_collision_never_invokes_frozen_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    collision: str,
) -> None:
    temporary = TemporaryDirectory(prefix="iv3v2-post-proposal-")
    request.addfinalizer(temporary.cleanup)
    temporary_root = Path(temporary.name)
    root, attempt_base, protocol = _prepare_authorization_proposal_environment(
        temporary_root,
        monkeypatch,
    )
    proposal = v2.build_live_authorization_proposal(
        root,
        expected_lock_head=protocol.lock_commit_sha,
        opaque_receipt_id=_OPAQUE_RECEIPT_ID,
        attempt_base=attempt_base,
    )
    authorization_id = proposal.get("authorization_id")
    assert isinstance(authorization_id, str)
    authorization = replace(
        _authorization(),
        authorization_id=authorization_id,
    )
    paths = v2.ProtocolV2Paths.for_authorization(
        root,
        authorization.authorization_id,
        protocol.lock_sha256,
        attempt_base=attempt_base,
    )
    legacy_reservation = (
        attempt_base
        / "Mining-Automation"
        / "inventory-positive-v3-independent-reservations"
        / f"{v2.PROTOCOL_V1_LOCK_SHA256}.json"
    )
    if collision == "legacy-reservation":
        legacy_reservation.parent.mkdir(parents=True)
        legacy_reservation.write_bytes(b"foreign post-proposal reservation\n")
    else:
        occupied_root = {
            "source-campaign-root": paths.source_campaign_root,
            "workspace-root": paths.workspace_root,
            "result-root": paths.result_root,
            "empty-attempt-root": paths.attempt_root,
        }[collision]
        occupied_root.mkdir(parents=True)
        assert tuple(occupied_root.iterdir()) == ()
    launcher = root / "tools" / "capture_inventory_v3_independent.py"
    launcher.parent.mkdir()
    launcher.write_bytes(b"frozen launcher sentinel\n")
    monkeypatch.setattr(
        v2,
        "verify_live_authorization",
        lambda _protocol: authorization,
    )
    subprocess_called = False

    def forbidden_subprocess(*args: object, **kwargs: object) -> object:
        nonlocal subprocess_called
        del args, kwargs
        subprocess_called = True
        raise AssertionError("frozen subprocess must not run after a root collision")

    monkeypatch.setattr(v2.subprocess, "run", forbidden_subprocess)

    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="occupied|already exists|pre-existing|non-directory ancestor",
    ):
        v2.run_passive_capture_protocol_v2(
            root,
            expected_head=protocol.evaluator_head_sha,
            operator="operator-a",
            runelite_build="asserted-build",
            client_mode="fixed",
            theme="dark",
            renderer="gpu",
            attempt_base=attempt_base,
        )

    assert subprocess_called is False


def test_injected_short_write_rolls_back_every_owned_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "record.json"
    real_write = os.write
    calls = 0

    def short_then_stop(descriptor: int, payload: bytes) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_write(descriptor, payload[:3])
        return 0

    monkeypatch.setattr(v2.os, "write", short_then_stop)

    with pytest.raises(OSError, match="short output write"):
        v2._write_canonical_exclusive(target, {"schema": "short-write-v1"})

    assert calls == 2
    assert not target.exists()
    assert not target.with_suffix(".json.sha256").exists()
    assert tuple(tmp_path.iterdir()) == ()


def test_writer_cleanup_never_deletes_foreign_replacement_on_identity_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "record.json"
    foreign = b"foreign replacement bytes\n"
    real_unlink_owned_file = v2._unlink_owned_file
    replacement_observed = False
    occupiers: list[Path] = []

    def fail_readback(path: Path, identity: tuple[int, int]) -> bytes:
        del path, identity
        raise v2.InventoryV3ProtocolV2Error("synthetic readback failure")

    def replace_before_cleanup(path: Path, identity: tuple[int, int]) -> None:
        nonlocal replacement_observed
        if path == target:
            path.unlink()
            for index in range(128):
                path.write_bytes(foreign)
                current = path.lstat()
                current_identity = (int(current.st_dev), int(current.st_ino))
                if current_identity != identity:
                    replacement_observed = True
                    break
                path.unlink()
                occupier = tmp_path / f"inode-occupier-{index}"
                occupier.write_bytes(b"occupy a recycled identity")
                occupiers.append(occupier)
            assert replacement_observed
        real_unlink_owned_file(path, identity)

    monkeypatch.setattr(v2, "_read_owned_file", fail_readback)
    monkeypatch.setattr(v2, "_unlink_owned_file", replace_before_cleanup)

    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="synthetic readback failure",
    ):
        v2._write_canonical_exclusive(target, {"schema": "replacement-race-v1"})

    assert replacement_observed is True
    assert target.read_bytes() == foreign
    assert not target.with_suffix(".json.sha256").exists()


def test_non_evaluator_failure_is_closed_private_public_and_terminal_bound(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    protocol = _protocol(root)
    authorization = _authorization()
    paths = _paths(root, tmp_path / "local-app-data")
    operation = "capture-passive-campaign"
    v2._reserve_attempt(paths, protocol, operation, {"source": "frozen"})

    v2._attempt_failed_best_effort(
        paths,
        protocol,
        authorization,
        operation,
        "CAMPAIGN_TERMINAL_FAILURE",
        error_type="SyntheticCaptureFailure",
    )

    failure_root = paths.attempt_root / f"{operation}-failure"
    assert {path.name for path in failure_root.iterdir()} == {
        "package-tree.json",
        "package-tree.json.sha256",
        "private-failure.json",
        "private-failure.json.sha256",
        "public-failure-receipt.json",
        "public-failure-receipt.json.sha256",
    }
    tree_payload = (failure_root / "package-tree.json").read_bytes()
    tree = json.loads(tree_payload)
    assert {
        entry["path"]: entry["role"] for entry in tree["entries"]
    } == v2._operation_failure_roles()

    private_record = json.loads((failure_root / "private-failure.json").read_bytes())
    assert set(private_record) == {
        "activation_allowed",
        "authorization_id",
        "contract_id",
        "error_type",
        "opaque_receipt_id",
        "operation",
        "promotion_allowed",
        "protocol_lock_git_commit_sha",
        "protocol_lock_sha256",
        "protocol_source_git_commit_sha",
        "reservation_sha256",
        "retry_allowed",
        "schema",
        "terminal_status",
    }
    assert private_record["error_type"] == "SyntheticCaptureFailure"
    assert private_record["retry_allowed"] is False
    assert private_record["activation_allowed"] is False
    assert private_record["promotion_allowed"] is False

    public_record = json.loads((failure_root / "public-failure-receipt.json").read_bytes())
    projection = privacy.parse_permanent_failure_projection(public_record)
    assert projection.contract_id is privacy.FailureContractId.CAMPAIGN_TERMINAL_FAILURE
    assert set(public_record) == {
        "activation_allowed",
        "contract_id",
        "opaque_receipt_id",
        "promotion_allowed",
        "retry_allowed",
        "schema",
        "terminal_status",
    }

    terminal = json.loads((paths.attempt_root / f"{operation}-terminal.json").read_bytes())
    assert terminal["status"] == "failed-terminal"
    assert terminal["contract_id"] == "CAMPAIGN_TERMINAL_FAILURE"
    assert terminal["retry_allowed"] is False
    assert terminal["output_sha256"] == _sha256(tree_payload)


def test_lifecycle_rejects_missing_first_predecessor(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    protocol = _protocol(root)
    paths = _paths(root, tmp_path / "local-app-data")

    with pytest.raises(v2.InventoryV3ProtocolV2Error):
        v2._reserve_attempt(paths, protocol, "finalize-acquisition", {})

    assert not (paths.attempt_root / "finalize-acquisition-reserved.json").exists()


def test_first_attempt_reservation_refuses_preexisting_empty_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    protocol = _protocol(root)
    paths = _paths(root, tmp_path / "local-app-data")
    paths.attempt_root.mkdir(parents=True)
    assert tuple(paths.attempt_root.iterdir()) == ()

    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="occupied|pre-existing|already exists",
    ):
        v2._reserve_attempt(
            paths,
            protocol,
            "capture-passive-campaign",
            {"capture_build_sha": v2.PROTOCOL_V1_SOURCE_HEAD},
        )

    assert tuple(paths.attempt_root.iterdir()) == ()


def test_lifecycle_rejects_skipped_middle_predecessors(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    protocol = _protocol(root)
    paths = _paths(root, tmp_path / "local-app-data")
    first = "capture-passive-campaign"
    v2._reserve_attempt(paths, protocol, first, {})
    v2._record_attempt_terminal(
        paths,
        protocol,
        first,
        status="passed-terminal",
        contract_id=v2._ATTEMPT_SUCCESS_CONTRACTS[first],
        output_sha256="1" * 64,
    )

    with pytest.raises(v2.InventoryV3ProtocolV2Error):
        v2._reserve_attempt(paths, protocol, "record-review-submission", {})

    assert not (paths.attempt_root / "record-review-submission-reserved.json").exists()


def test_lifecycle_rejects_failed_predecessor_without_advancement(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    protocol = _protocol(root)
    paths = _paths(root, tmp_path / "local-app-data")
    first = "capture-passive-campaign"
    v2._reserve_attempt(paths, protocol, first, {})
    v2._record_attempt_terminal(
        paths,
        protocol,
        first,
        status="failed-terminal",
        contract_id="CAMPAIGN_TERMINAL_FAILURE",
        output_sha256="1" * 64,
    )

    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="not a closed successful prior lifecycle stage",
    ):
        v2._reserve_attempt(paths, protocol, "finalize-acquisition", {})

    assert not (paths.attempt_root / "finalize-acquisition-reserved.json").exists()


def test_closed_failure_package_blocks_every_post_failure_continuation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    protocol = _protocol(root)
    authorization = _authorization()
    paths = _paths(root, tmp_path / "local-app-data")
    first = "capture-passive-campaign"
    v2._reserve_attempt(paths, protocol, first, {})
    v2._attempt_failed_best_effort(
        paths,
        protocol,
        authorization,
        first,
        "CAMPAIGN_TERMINAL_FAILURE",
        error_type="SyntheticCaptureFailure",
    )

    with pytest.raises(v2.InventoryV3ProtocolV2Error):
        v2._reserve_attempt(paths, protocol, "finalize-acquisition", {})

    assert not (paths.attempt_root / "finalize-acquisition-reserved.json").exists()
    assert (paths.attempt_root / "capture-passive-campaign-failure" / "package-tree.json").is_file()


def test_development_dataset_rejects_before_evaluator_sensitive_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, _ = _prepare_evaluator_snapshot_harness(tmp_path, monkeypatch)
    events: list[tuple[str, str]] = []

    def reject_development_dataset(_dataset_id: object) -> None:
        raise v2.InventoryV3ProtocolV2Error("synthetic development dataset reuse")

    monkeypatch.setattr(
        v2,
        "_require_non_development_dataset_identity",
        reject_development_dataset,
    )

    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="synthetic development dataset reuse",
    ):
        v2.evaluate_locked_protocol_v2(
            source.protocol.repository_root,
            expected_head=source.protocol.evaluator_head_sha,
            attempt_base=tmp_path / "local-app-data",
            access_hook=lambda phase, kind, _path: events.append((phase, kind)),
        )

    assert all(phase != "sensitive" for phase, _kind in events)
    assert not (source.paths.attempt_root / "evaluate-locked-candidate-reserved.json").exists()
    assert not source.paths.result_root.exists()


def test_development_manifest_identity_rejects_before_evaluator_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, _ = _prepare_evaluator_snapshot_harness(tmp_path, monkeypatch)
    manifest = json.loads(
        (
            source.paths.reviewed_package_root / v2._CAMPAIGN_MANIFEST_NAME
        ).read_bytes()
    )
    cases = manifest["cases"]
    assert isinstance(cases, list)
    first = cases[0]
    assert isinstance(first, dict)
    capture_id = first["capture_id"]
    assert isinstance(capture_id, str)
    monkeypatch.setattr(
        v2,
        "_frozen_development_identity_sets",
        lambda _protocol: (
            frozenset(),
            frozenset(),
            frozenset({capture_id}),
        ),
    )
    events: list[tuple[str, str]] = []

    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="campaign manifest reuses a frozen development identity",
    ):
        v2.evaluate_locked_protocol_v2(
            source.protocol.repository_root,
            expected_head=source.protocol.evaluator_head_sha,
            attempt_base=tmp_path / "local-app-data",
            access_hook=lambda phase, kind, _path: events.append((phase, kind)),
        )

    assert all(phase != "sensitive" for phase, _kind in events)
    assert not (
        source.paths.attempt_root / "evaluate-locked-candidate-reserved.json"
    ).exists()
    assert not source.paths.result_root.exists()


def test_validation_package_extra_key_rejects_before_evaluator_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, _ = _prepare_evaluator_snapshot_harness(tmp_path, monkeypatch)
    reviewed_root = source.paths.reviewed_package_root
    package_path = reviewed_root / v2._VALIDATION_PACKAGE_NAME
    package = json.loads(package_path.read_bytes())
    package["hint"] = "empty"
    _, replacement_tree_sha = _coherently_rebind_package_member(
        reviewed_root,
        v2._VALIDATION_PACKAGE_NAME,
        _canonical_bytes(package),
    )
    manifest_payload = (reviewed_root / v2._CAMPAIGN_MANIFEST_NAME).read_bytes()
    reviewed_record = json.loads(
        (reviewed_root / "protocol-v2-reviewed-package.json").read_bytes()
    )
    submission_root = source.paths.review_intake_root / "submission"
    prior_lineage = {
        "acquisition_package_tree_sha256": reviewed_record[
            "acquisition_package_tree_sha256"
        ],
        "campaign_manifest_sha256": _sha256(manifest_payload),
        "review_intake_package_tree_sha256": reviewed_record[
            "review_intake_tree_sha256"
        ],
        "review_submission_package_tree_sha256": _sha256(
            (submission_root / v2._PACKAGE_TREE_NAME).read_bytes()
        ),
        "review_submission_sha256": _sha256(
            (submission_root / v2._REVIEW_SUBMISSION_NAME).read_bytes()
        ),
        "reviewed_package_tree_sha256": replacement_tree_sha,
    }
    monkeypatch.setattr(
        v2,
        "_preflight_review_pipeline_lineage",
        lambda _source, *, require_reviewed: prior_lineage,
    )
    events: list[tuple[str, str]] = []

    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="validation package metadata keys differ",
    ):
        v2.evaluate_locked_protocol_v2(
            source.protocol.repository_root,
            expected_head=source.protocol.evaluator_head_sha,
            attempt_base=tmp_path / "local-app-data",
            access_hook=lambda phase, kind, _path: events.append((phase, kind)),
        )

    assert all(phase != "sensitive" for phase, _kind in events)
    assert not (
        source.paths.attempt_root / "evaluate-locked-candidate-reserved.json"
    ).exists()
    assert not source.paths.result_root.exists()


def test_evaluator_rejects_tampered_v2_bridge_after_irrevocable_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, bridge_path = _prepare_evaluator_snapshot_harness(tmp_path, monkeypatch)
    bridge_path.write_bytes(bridge_path.read_bytes() + b"tampered before evaluation\n")
    evaluator_called = False

    def forbidden_evaluator(*args: object, **kwargs: object) -> object:
        nonlocal evaluator_called
        del args, kwargs
        evaluator_called = True
        raise AssertionError("frozen evaluation must not see a tampered bridge")

    monkeypatch.setattr(
        frozen_validation,
        "evaluate_frozen_v3_independent_validation",
        forbidden_evaluator,
    )

    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="closed package tree differs",
    ):
        v2.evaluate_locked_protocol_v2(
            source.protocol.repository_root,
            expected_head=source.protocol.evaluator_head_sha,
            attempt_base=tmp_path / "local-app-data",
        )

    assert evaluator_called is False
    assert (source.paths.attempt_root / "evaluate-locked-candidate-reserved.json").is_file()
    _assert_evaluator_integrity_failure(source)


def test_concurrent_reviewed_tree_mutation_prevents_result_publication_and_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, bridge_path = _prepare_evaluator_snapshot_harness(tmp_path, monkeypatch)

    def mutating_evaluator(*args: object, **kwargs: object) -> object:
        del args, kwargs
        bridge_path.write_bytes(bridge_path.read_bytes() + b"mutated during evaluator\n")
        return SimpleNamespace(
            approval=None,
            cases=(),
            detector_conformance_passed=True,
            to_dict=lambda: {
                "detector_conformance_passed": True,
                "schema": "synthetic-frozen-evaluator-report-v1",
            },
        )

    monkeypatch.setattr(
        frozen_validation,
        "evaluate_frozen_v3_independent_validation",
        mutating_evaluator,
    )

    with pytest.raises(PackageTreeError, match="changed after its original snapshot"):
        v2.evaluate_locked_protocol_v2(
            source.protocol.repository_root,
            expected_head=source.protocol.evaluator_head_sha,
            attempt_base=tmp_path / "local-app-data",
        )

    _assert_evaluator_integrity_failure(source)


def test_coherent_tree_rebind_after_reservation_cannot_replace_expected_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, bridge_path = _prepare_evaluator_snapshot_harness(tmp_path, monkeypatch)
    rebound = False
    original_tree_sha = ""
    replacement_tree_sha = ""
    evaluator_called = False

    def coordinated_rebind(phase: str, kind: str, _path: Path | None) -> None:
        nonlocal rebound, original_tree_sha, replacement_tree_sha
        if not rebound and (phase, kind) == ("sensitive", "reviewer_truth_opened"):
            original_tree_sha, replacement_tree_sha = _coherently_rebind_package_member(
                source.paths.reviewed_package_root,
                "protocol-v2-acquisition.json",
                bridge_path.read_bytes() + b"coherent post-reservation replacement\n",
            )
            rebound = True

    def forbidden_evaluator(*args: object, **kwargs: object) -> object:
        nonlocal evaluator_called
        del args, kwargs
        evaluator_called = True
        raise AssertionError("reserved tree replacement must not reach evaluation")

    monkeypatch.setattr(
        frozen_validation,
        "evaluate_frozen_v3_independent_validation",
        forbidden_evaluator,
    )

    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="package tree changed after metadata reservation",
    ):
        v2.evaluate_locked_protocol_v2(
            source.protocol.repository_root,
            expected_head=source.protocol.evaluator_head_sha,
            attempt_base=tmp_path / "local-app-data",
            access_hook=coordinated_rebind,
        )

    assert rebound is True
    assert original_tree_sha != replacement_tree_sha
    assert evaluator_called is False
    replacement_snapshot = v2._read_verified_tree(
        source.paths.reviewed_package_root,
        v2._reviewed_package_roles(),
    )
    replacement_snapshot.recheck()
    _assert_evaluator_integrity_failure(source)


def test_approval_must_strictly_follow_verified_evaluator_terminal_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, _, _, _ = _prepare_approval_registry_race_harness(
        tmp_path,
        monkeypatch,
    )
    evaluator_terminal = json.loads(
        (source.paths.attempt_root / "evaluate-locked-candidate-terminal.json").read_bytes()
    )
    terminal_at_text = evaluator_terminal["terminal_at_utc"]
    result_record = json.loads(
        (source.paths.result_root / "protocol-v2-terminal-result.json").read_bytes()
    )
    assert v2._parse_utc(result_record["evaluated_at_utc"], "evaluated") < (
        v2._parse_utc(terminal_at_text, "evaluator terminal")
    )

    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="source approval must follow independent review and terminal conformance",
    ):
        v2.prepare_approval_request(
            source.protocol.repository_root,
            expected_head=source.protocol.evaluator_head_sha,
            proposed_approver="approver-c",
            proposed_approved_at_utc=terminal_at_text,
            attempt_base=tmp_path / "local-app-data",
        )

    assert not (source.paths.attempt_root / "prepare-approval-request-reserved.json").exists()
    assert not source.paths.approval_request_root.exists()


@pytest.mark.parametrize("race_point", ("in-reservation", "end-verifier"))
def test_approval_registry_mutation_after_reservation_cannot_complete_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    race_point: str,
) -> None:
    source, registry_path, registry_payload, registry_sidecar_payload = (
        _prepare_approval_registry_race_harness(tmp_path, monkeypatch)
    )
    registry_sidecar = registry_path.with_suffix(registry_path.suffix + ".sha256")
    mutated_registry: Mapping[str, object] = {
        "activation_allowed": False,
        "entries": [
            {
                "approval_id": "foreign-post-reservation-approval",
                "status": "foreign",
            }
        ],
        "promotion_allowed": False,
        "schema": "inventory-positive-v3-independent-validation-approval-registry-v1",
    }
    mutated_payload = _canonical_bytes(mutated_registry)
    mutated_sidecar_payload = f"{_sha256(mutated_payload)}  {registry_path.name}\n".encode("ascii")
    mutation_done = False
    verification_calls = 0

    def mutate_registry() -> None:
        nonlocal mutation_done
        _atomic_replace_bytes(registry_path, mutated_payload)
        _atomic_replace_bytes(registry_sidecar, mutated_sidecar_payload)
        mutation_done = True

    def verify_pristine_registry(
        _protocol: v2.ProtocolV2LockBinding,
        *,
        access_hook: v2.AccessHook | None = None,
    ) -> bytes:
        nonlocal verification_calls
        del access_hook
        verification_calls += 1
        if race_point == "in-reservation" and verification_calls == 1:
            mutate_registry()
        if (
            registry_path.read_bytes() != registry_payload
            or registry_sidecar.read_bytes() != registry_sidecar_payload
        ):
            raise v2.InventoryV3ProtocolV2Error("source approval registry differs")
        return registry_payload

    monkeypatch.setattr(
        v2,
        "_verify_approval_registry_absent",
        verify_pristine_registry,
    )
    real_writer = v2._write_canonical_exclusive

    def mutate_before_request_publication(
        path: Path,
        value: Mapping[str, object],
    ) -> str:
        if (
            race_point == "end-verifier"
            and path.name == "approval-request.json"
            and not mutation_done
        ):
            mutate_registry()
        return real_writer(path, value)

    monkeypatch.setattr(
        v2,
        "_write_canonical_exclusive",
        mutate_before_request_publication,
    )

    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="source approval registry differs",
    ):
        v2.prepare_approval_request(
            source.protocol.repository_root,
            expected_head=source.protocol.evaluator_head_sha,
            proposed_approver="approver-c",
            proposed_approved_at_utc=(_current_approval_time_after_evaluator_terminal(source)),
            attempt_base=tmp_path / "local-app-data",
        )

    assert mutation_done is True
    assert verification_calls == (1 if race_point == "in-reservation" else 2)
    assert (source.paths.attempt_root / "prepare-approval-request-reserved.json").is_file()
    _assert_approval_integrity_failure(source)


@pytest.mark.parametrize(
    ("release_seam", "minimum_full_reads"),
    (
        (v2._load_acquisition, 1),
        (v2._load_review_intake, 1),
        (v2.publish_reviewed_package, 1),
        (v2.evaluate_locked_protocol_v2, 1),
        (v2.prepare_approval_request, 2),
    ),
)
def test_every_release_full_read_seam_binds_the_prereserved_tree_sha(
    release_seam: object,
    minimum_full_reads: int,
) -> None:
    source = textwrap.dedent(inspect.getsource(release_seam))
    parsed = ast.parse(source)
    calls = [
        node
        for node in ast.walk(parsed)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_read_verified_tree"
    ]
    assert len(calls) >= minimum_full_reads
    for call in calls:
        expected_keyword = next(
            (keyword for keyword in call.keywords if keyword.arg == "expected_tree_sha256"),
            None,
        )
        assert expected_keyword is not None
        assert not (
            isinstance(expected_keyword.value, ast.Constant)
            and expected_keyword.value.value is None
        )


@pytest.mark.skipif(os.name != "nt", reason="requires the actual Windows envelope")
def test_actual_repository_and_local_app_data_fit_legacy_windows_budget() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    local_app_data_value = os.environ.get("LOCALAPPDATA")
    assert local_app_data_value is not None
    paths = v2.ProtocolV2Paths.for_authorization(
        repository_root,
        _AUTHORIZATION_ID,
        _PROTOCOL_LOCK_SHA256,
        attempt_base=Path(local_app_data_value),
    )

    v2._assert_windows_legacy_path_budget(paths)
    files, directories = _fixed_budget_envelope(paths)

    assert max(_utf16_units(path) for path in files) <= 259
    assert max(_utf16_units(path) for path in directories) <= 247


@pytest.mark.parametrize("long_anchor", ("repository", "attempt-base"))
def test_over_budget_anchor_is_rejected_before_any_protocol_write(
    tmp_path: Path,
    long_anchor: str,
) -> None:
    long_path = tmp_path / ("x" * 100) / ("y" * 100) / ("z" * 100)
    if long_anchor == "repository":
        paths = _exact_unresolved_paths(long_path, tmp_path / "attempt-base")
    else:
        paths = _exact_unresolved_paths(tmp_path / "repository", long_path)
    output_roots = (
        paths.source_campaign_root,
        paths.workspace_root,
        paths.result_root,
        paths.attempt_root,
    )
    assert all(not path.exists() for path in output_roots)

    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="Windows legacy path budget",
    ):
        v2._assert_disjoint_paths(paths)

    assert all(not path.exists() for path in output_roots)
