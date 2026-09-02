"""Inventory V3 independent-validation protocol V2 orchestration.

V2 is a versioned release protocol around the single frozen V3 evaluator.  It
does not implement detector behavior.  Its responsibilities are repository and
authorization preflight, deterministic acquisition finalization, independent
review intake, closed package ownership, one-shot result publication, and
privacy-safe failure state.

The module intentionally lives below ``validation/``.  Every v1 ``src/`` and
``tools/`` blob locked by commit 32764bfd remains byte-for-byte untouched and
continues to be the only evaluator/capture implementation used by this line.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Final

from .package_tree import (
    PackageTreeError,
    PackageTreeSnapshot,
    enumerate_package_tree,
    verify_package_tree,
)

FROZEN_V3_HEAD: Final[str] = "5975532b472a74d93f010e04ca44b2efa2a3ffd7"
PROTOCOL_V1_SOURCE_HEAD: Final[str] = "b3b141e0d9ca15d729eaa98c795f6c855bff68cf"
PROTOCOL_V1_LOCK_HEAD: Final[str] = "32764bfd82afb46d4e99292bab7d162be536e2d7"
PROTOCOL_V1_LOCK_SHA256: Final[str] = (
    "64ab45f8b0294f733c4517ad46ebb01e722f3fbf3d14d52feb79649b5a3649f1"
)
PROTOCOL_V1_PREREGISTRATION_SHA256: Final[str] = (
    "47db5a775095b7828e1c10d19949519002d5c7540eaf8d3c18e0eb3154bd9130"
)
PROTOCOL_V2_ID: Final[str] = "inventory-positive-v3-independent-validation"
PROTOCOL_V2_VERSION: Final[str] = "2.0.0"
PROTOCOL_V2_LOCK_SCHEMA: Final[str] = (
    "inventory-positive-v3-independent-validation-protocol-lock-v2"
)
PROTOCOL_V2_PREREGISTRATION_SCHEMA: Final[str] = (
    "inventory-positive-v3-independent-validation-preregistration-v3"
)
PROTOCOL_V2_PREREGISTRATION_SHA256: Final[str] = (
    "debecab3c90b71dbb7746c0fbe40abdb2212651ed495358a4c10ce712971d509"
)
CAPTURE_CONFIGURATION_ID: Final[str] = "inventory-positive-v3-independent-passive-natural-fill-v1"
LIVE_AUTHORIZATION_SCHEMA: Final[str] = (
    "inventory-positive-v3-independent-live-campaign-authorization-registry-v1"
)
LIVE_AUTHORIZATION_STATUS: Final[str] = "authorized-for-passive-independent-validation-capture"
REPOSITORY_ID: Final[str] = "maadbonnie21-lgtm/Mining-Automation"

SUPPORTED_FRAME_WIDTH: Final[int] = 1005
SUPPORTED_FRAME_HEIGHT: Final[int] = 1078
SUPPORTED_PIXEL_FORMAT: Final[str] = "bgra8888"
SUPPORTED_PROFILE_ID: Final[str] = "candidate-live-inventory-348867800b28a54e"
SUPPORTED_REGION: Final[tuple[int, int, int, int]] = (567, 569, 158, 248)
FULL_FRAME_SIZE: Final[int] = SUPPORTED_FRAME_WIDTH * SUPPORTED_FRAME_HEIGHT * 4
REGION_SIZE: Final[int] = SUPPORTED_REGION[2] * SUPPORTED_REGION[3] * 4

REQUIRED_STAGES: Final[tuple[str, ...]] = (
    "empty",
    "early-partial",
    "mid-partial",
    "near-full",
    "full",
    "wrong-tab",
    "row-obstruction",
)

_V2_DIRECTORY: Final[PurePosixPath] = PurePosixPath("validation/inventory_v3_protocol_v2")
_V2_LOCK_PATH: Final[PurePosixPath] = _V2_DIRECTORY / "protocol-lock.json"
_V2_LOCK_SIDECAR_PATH: Final[PurePosixPath] = _V2_DIRECTORY / "protocol-lock.json.sha256"
_V2_PREREGISTRATION_PATH: Final[PurePosixPath] = _V2_DIRECTORY / "preregistration.json"
_V2_PREREGISTRATION_SIDECAR_PATH: Final[PurePosixPath] = (
    _V2_DIRECTORY / "preregistration.json.sha256"
)
_V1_LOCK_PATH: Final[PurePosixPath] = PurePosixPath(
    "validation/inventory-positive-v3/protocol-lock.json"
)
_V1_LOCK_SIDECAR_PATH: Final[PurePosixPath] = PurePosixPath(
    "validation/inventory-positive-v3/protocol-lock.sha256"
)
_LIVE_AUTHORIZATION_PATH: Final[PurePosixPath] = PurePosixPath(
    "validation/inventory-positive-v3/live-campaign-authorizations.json"
)
_V2_LIVE_AUTHORIZATION_PATH: Final[PurePosixPath] = (
    _V2_DIRECTORY / "live-campaign-authorizations.json"
)
_V2_LIVE_AUTHORIZATION_SIDECAR_PATH: Final[PurePosixPath] = (
    _V2_DIRECTORY / "live-campaign-authorizations.json.sha256"
)
_V2_LIVE_AUTHORIZATION_SCHEMA: Final[str] = (
    "inventory-positive-v3-independent-protocol-v2-live-authorization-registry-v1"
)
_V2_LIVE_AUTHORIZATION_STATUS: Final[str] = (
    "authorized-for-protocol-v2-passive-independent-validation"
)
_V2_EMPTY_LIVE_AUTHORIZATION_SHA256: Final[str] = (
    "a1ff94c3b4060eeb967c5a4a2756c2d7370f6b66a2d6f5f2472ade2b27568a40"
)
_APPROVAL_REGISTRY_PATH: Final[PurePosixPath] = PurePosixPath(
    "validation/inventory-positive-v3/approved-campaigns.json"
)
_APPROVAL_REGISTRY_SIDECAR_PATH: Final[PurePosixPath] = PurePosixPath(
    "validation/inventory-positive-v3/approved-campaigns.json.sha256"
)
_DEVELOPMENT_MANIFEST_PATH: Final[PurePosixPath] = PurePosixPath(
    "tests/fixtures/perception/inventory-live-candidate-safety-bb0d0e3f7ff1c73b/manifest.json"
)
_DEVELOPMENT_MANIFEST_SIDECAR_PATH: Final[PurePosixPath] = PurePosixPath(
    "tests/fixtures/perception/inventory-live-candidate-safety-bb0d0e3f7ff1c73b/"
    "manifest.json.sha256"
)
_DEVELOPMENT_METADATA_PATHS: Final[tuple[str, ...]] = (
    _DEVELOPMENT_MANIFEST_PATH.as_posix(),
    _DEVELOPMENT_MANIFEST_SIDECAR_PATH.as_posix(),
)
_DEVELOPMENT_DATASET_ID: Final[str] = "inventory-live-candidate-safety-bb0d0e3f7ff1c73b"
_DEVELOPMENT_MANIFEST_SHA256: Final[str] = (
    "2e518ce81dd291f8b7d055afad9ddc12acbc66e0e967845f8f2e548fe1644479"
)
_DEVELOPMENT_CASE_IDS_SHA256: Final[str] = (
    "d91e1b01617669d0bf44c6d0c8645070994528471c02b773310b422b812ff05a"
)
_DEVELOPMENT_SESSION_IDS_SHA256: Final[str] = (
    "d75ad12a7d412e244b7349a8663ce7236d5cfc0bdd7e95461e76b1ca12623701"
)
_DEVELOPMENT_CAPTURE_IDS_SHA256: Final[str] = (
    "728a350c0af47cc660591b2ab713218352d19d78054288181eabdab00721a313"
)
_SOURCE_OUTPUT_ROOT: Final[PurePosixPath] = PurePosixPath(
    "diagnostics/inventory-positive-v3-independent-source"
)
_V2_WORKSPACE_ROOT: Final[PurePosixPath] = PurePosixPath("diagnostics/iv3v2")
_RESULT_OUTPUT_ROOT: Final[PurePosixPath] = PurePosixPath("diagnostics/iv3v2r")
_WORKSPACE_ACQUISITION_DIR: Final[str] = "a"
_WORKSPACE_REVIEW_INTAKE_DIR: Final[str] = "ri"
_WORKSPACE_REVIEWED_PACKAGE_DIR: Final[str] = "rp"
_WORKSPACE_APPROVAL_REQUEST_DIR: Final[str] = "ar"
_WINDOWS_LEGACY_MAX_PATH_CHARS: Final[int] = 259
_WINDOWS_LEGACY_MAX_DIRECTORY_CHARS: Final[int] = 247

_V2_SOURCE_PATHS: Final[tuple[str, ...]] = (
    "diagnostics/iv3v2/.gitignore",
    "diagnostics/iv3v2r/.gitignore",
    "validation/inventory_v3_protocol_v2/__init__.py",
    "validation/inventory_v3_protocol_v2/cli.py",
    "validation/inventory_v3_protocol_v2/launcher.py",
    "validation/inventory_v3_protocol_v2/package_tree.py",
    "validation/inventory_v3_protocol_v2/privacy.py",
    "validation/inventory_v3_protocol_v2/producer.py",
    "validation/inventory_v3_protocol_v2/protocol.py",
    "validation/inventory_v3_protocol_v2/preregistration.json",
    "validation/inventory_v3_protocol_v2/preregistration.json.sha256",
    "docs/INVENTORY_VALIDATION_PROTOCOL_V2.md",
)
_V2_TEST_PATHS: Final[tuple[str, ...]] = (
    "tests/test_inventory_v3_protocol_v2_bridge.py",
    "tests/test_inventory_v3_protocol_v2_cli.py",
    "tests/test_inventory_v3_protocol_v2_lock_shadows.py",
    "tests/test_inventory_v3_protocol_v2_package_tree.py",
    "tests/test_inventory_v3_protocol_v2_privacy.py",
    "tests/test_inventory_v3_protocol_v2_producer.py",
    "tests/test_inventory_v3_protocol_v2_protocol.py",
    "tests/test_inventory_v3_protocol_v2_transactions.py",
)
_P2_CHANGED_PATHS: Final[tuple[str, ...]] = tuple(
    sorted(
        {
            *_V2_SOURCE_PATHS,
            *_V2_TEST_PATHS,
            _V2_LIVE_AUTHORIZATION_PATH.as_posix(),
            _V2_LIVE_AUTHORIZATION_SIDECAR_PATH.as_posix(),
        }
    )
)

_SOURCE_SESSION_SCHEMA: Final[str] = "inventory-positive-v3-independent-source-session-v2"
_SOURCE_CAPTURE_SCHEMA: Final[str] = "inventory-positive-v3-independent-source-capture-v2"
_SOURCE_COMPLETION_SCHEMA: Final[str] = (
    "inventory-positive-v3-independent-source-completion-seal-v1"
)
_CAPTURE_PROGRESS_SCHEMA: Final[str] = (
    "inventory-positive-v3-independent-passive-capture-progress-v1"
)
_OWNED_FRAME_SCHEMA: Final[str] = "inventory-positive-v3-independent-owned-frame-v1"
_CAMPAIGN_MANIFEST_SCHEMA: Final[str] = "inventory-positive-v3-independent-validation-dataset-v2"
_REVIEW_SCHEMA: Final[str] = "inventory-positive-v3-independent-validation-review-v1"
_VALIDATION_PACKAGE_SCHEMA: Final[str] = "inventory-positive-v3-independent-validation-package-v2"
_PRODUCER_ATTESTATION_SCHEMA: Final[str] = (
    "inventory-positive-v3-independent-producer-attestation-v2"
)
_ACQUISITION_SCHEMA: Final[str] = "inventory-positive-v3-independent-finalized-acquisition-v2"
_REVIEW_INTAKE_SCHEMA: Final[str] = "inventory-positive-v3-independent-review-intake-v2"
_REVIEW_SUBMISSION_SCHEMA: Final[str] = "inventory-positive-v3-independent-review-submission-v2"
_REVIEWED_PACKAGE_SCHEMA: Final[str] = "inventory-positive-v3-independent-reviewed-package-v2"
_APPROVAL_REQUEST_SCHEMA: Final[str] = "inventory-positive-v3-independent-approval-request-v2"
_ATTEMPT_SCHEMA: Final[str] = "inventory-positive-v3-independent-attempt-v2"
_SOURCE_IDENTITY_BRIDGE: Final[str] = "frozen-capture-lf-to-frozen-evaluator-data-bytes-v1"
_PRODUCER_ATTESTATION_NAME: Final[str] = "protocol-v2-producer-attestation.json"
_SESSION_REPORT_NAME: Final[str] = "source-session-report.json"
_COMPLETION_SEAL_NAME: Final[str] = "source-completion-seal.json"
_EVALUATOR_SESSION_NAME: Final[str] = "frozen-v1-evaluator-source-session.json"
_EVALUATOR_SEAL_NAME: Final[str] = "frozen-v1-evaluator-completion-seal.json"
_CAPTURE_PROGRESS_NAME: Final[str] = "capture-progress.json"
_CAMPAIGN_MANIFEST_NAME: Final[str] = "campaign-manifest.json"
_REVIEW_TEMPLATE_NAME: Final[str] = "reviewer-template.json"
_REVIEW_SUBMISSION_NAME: Final[str] = "reviewer-submission.json"
_REVIEWER_TRUTH_NAME: Final[str] = "reviewer-truth.json"
_VALIDATION_PACKAGE_NAME: Final[str] = "validation-package.json"
_PACKAGE_TREE_NAME: Final[str] = "package-tree.json"

_ATTEMPT_OPERATIONS: Final[tuple[str, ...]] = (
    "capture-passive-campaign",
    "finalize-acquisition",
    "prepare-review-intake",
    "record-review-submission",
    "publish-reviewed-package",
    "evaluate-locked-candidate",
    "prepare-approval-request",
)
_ATTEMPT_SUCCESS_CONTRACTS: Final[Mapping[str, str]] = {
    "capture-passive-campaign": "PASSIVE_CAPTURE_COMPLETE_UNREVIEWED",
    "finalize-acquisition": "ACQUISITION_FINALIZED",
    "prepare-review-intake": "REVIEW_INTAKE_PREPARED",
    "record-review-submission": "REVIEW_SUBMISSION_RECORDED",
    "publish-reviewed-package": "REVIEWED_PACKAGE_FINALIZED",
    "evaluate-locked-candidate": "CONFORMANCE_PASSED_APPROVAL_REQUIRED",
    "prepare-approval-request": "SOURCE_APPROVAL_REQUEST_PREPARED_NOT_APPROVED",
}
_ATTEMPT_FAILURE_CONTRACTS: Final[Mapping[str, str]] = {
    "capture-passive-campaign": "CAMPAIGN_TERMINAL_FAILURE",
    "finalize-acquisition": "CASE_EVIDENCE_INELIGIBLE",
    "prepare-review-intake": "CASE_EVIDENCE_INELIGIBLE",
    "record-review-submission": "CASE_EVIDENCE_INELIGIBLE",
    "publish-reviewed-package": "CASE_EVIDENCE_INELIGIBLE",
    "evaluate-locked-candidate": "ATTEMPT_INTEGRITY_FAILURE",
    "prepare-approval-request": "ATTEMPT_INTEGRITY_FAILURE",
}

AccessHook = Callable[[str, str, Path | None], None]
ReviewerTruthProvider = Callable[[Mapping[str, object]], Mapping[str, object]]


class InventoryV3ProtocolV2Error(RuntimeError):
    """Protocol V2 rejected a release-critical operation."""


@dataclass(frozen=True, slots=True)
class GitBlobBinding:
    """One source path pinned to an exact Git blob."""

    path: str
    git_blob: str

    def to_dict(self) -> dict[str, str]:
        return {"git_blob": self.git_blob, "path": self.path}


@dataclass(frozen=True, slots=True)
class ProtocolV2LockBinding:
    """Verified P2/L2 history and source closure."""

    repository_root: Path
    evaluator_head_sha: str
    source_commit_sha: str
    lock_commit_sha: str
    lock_sha256: str
    locked_git_blobs: tuple[GitBlobBinding, ...]


@dataclass(frozen=True, slots=True)
class LiveAuthorizationBinding:
    """The sole source-owned authorization accepted by V2."""

    authorization_id: str
    git_commit_sha: str
    legacy_registry_git_blob: str
    protocol_v2_registry_git_blob: str
    committed_at_utc: str
    opaque_receipt_id: str


@dataclass(frozen=True, slots=True)
class ProtocolV2Paths:
    """Fixed roots for one authorization; callers cannot choose result paths."""

    repository_root: Path
    authorization_id: str
    source_campaign_root: Path
    workspace_root: Path
    acquisition_root: Path
    review_intake_root: Path
    reviewed_package_root: Path
    approval_request_root: Path
    result_root: Path
    attempt_root: Path
    attempt_base_root: Path | None = None

    @classmethod
    def for_authorization(
        cls,
        repository_root: Path,
        authorization_id: str,
        protocol_lock_sha256: str,
        *,
        attempt_base: Path | None = None,
    ) -> ProtocolV2Paths:
        root = repository_root.resolve(strict=True)
        _require_lower_hex(authorization_id, 64, "authorization_id")
        _require_lower_hex(protocol_lock_sha256, 64, "protocol_lock_sha256")
        source_root = root.joinpath(*_SOURCE_OUTPUT_ROOT.parts)
        result_root = root.joinpath(*_RESULT_OUTPUT_ROOT.parts)
        workspace = root.joinpath(*_V2_WORKSPACE_ROOT.parts) / authorization_id
        if attempt_base is None:
            attempt_base = _producer_user_local_app_data()
        attempt_identity = hashlib.sha256(
            f"{protocol_lock_sha256}:{authorization_id}".encode("ascii")
        ).hexdigest()
        attempts = (
            attempt_base.resolve(strict=False) / "Mining-Automation" / "iv3v2" / attempt_identity
        )
        return cls(
            repository_root=root,
            authorization_id=authorization_id,
            source_campaign_root=source_root / authorization_id,
            workspace_root=workspace,
            acquisition_root=workspace / _WORKSPACE_ACQUISITION_DIR,
            review_intake_root=workspace / _WORKSPACE_REVIEW_INTAKE_DIR,
            reviewed_package_root=workspace / _WORKSPACE_REVIEWED_PACKAGE_DIR,
            approval_request_root=workspace / _WORKSPACE_APPROVAL_REQUEST_DIR,
            result_root=result_root / authorization_id,
            attempt_root=attempts,
            attempt_base_root=attempt_base.resolve(strict=False),
        )


@dataclass(frozen=True, slots=True)
class SourceMetadataBinding:
    """Metadata-only source proof completed before any validation pixel read."""

    paths: ProtocolV2Paths
    protocol: ProtocolV2LockBinding
    authorization: LiveAuthorizationBinding
    session: Mapping[str, object]
    session_payload: bytes
    completion_seal: Mapping[str, object]
    completion_payload: bytes
    producer_attestation: Mapping[str, object]
    capture_reports: tuple[Mapping[str, object], ...]
    owned_frame_reports: tuple[Mapping[str, object], ...]
    source_files: tuple[str, ...]
    source_metadata_snapshot: tuple[tuple[str, tuple[int, ...]], ...] = ()


@dataclass(frozen=True, slots=True)
class FinalizedAcquisition:
    """One immutable source-owned acquisition package."""

    root: Path
    campaign_id: str
    dataset_id: str
    campaign_manifest_sha256: str
    package_tree_sha256: str


@dataclass(frozen=True, slots=True)
class ReviewerIntake:
    """Fixed independent-review intake created from one acquisition."""

    root: Path
    campaign_manifest_sha256: str
    reviewer_template_sha256: str


@dataclass(frozen=True, slots=True)
class ReviewedPackage:
    """Immutable evaluator-ready package after independent truth ingestion."""

    root: Path
    campaign_id: str
    dataset_id: str
    operator: str
    reviewer: str
    package_sha256: str
    campaign_manifest_sha256: str
    reviewer_truth_sha256: str
    package_tree_sha256: str


@dataclass(frozen=True, slots=True)
class TerminalEvaluation:
    """One no-replacement evaluator attempt and release interpretation."""

    root: Path
    terminal_status: str
    detector_conformance_passed: bool
    approval_required: bool
    frozen_evaluator_report_sha256: str
    result_record_sha256: str
    result_tree_sha256: str


def _emit(hook: AccessHook | None, phase: str, kind: str, path: Path | None = None) -> None:
    if hook is not None:
        hook(phase, kind, path)


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


def _canonical_data_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _git(root: Path, *arguments: str, allow_failure: bool = False) -> str:
    completed = subprocess.run(
        ("git", "-C", str(root), *arguments),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 and not allow_failure:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise InventoryV3ProtocolV2Error(f"Git command failed: {detail}")
    return completed.stdout.strip()


def _git_bytes(root: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ("git", "-C", str(root), *arguments),
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).decode("utf-8", errors="replace").strip()
        raise InventoryV3ProtocolV2Error(f"Git command failed: {detail}")
    return completed.stdout


def _require_lower_hex(value: object, length: int, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != length
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise InventoryV3ProtocolV2Error(
            f"{label} must be {length} lowercase hexadecimal characters"
        )
    return value


def _require_text(value: Mapping[str, object], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise InventoryV3ProtocolV2Error(f"{key} must be non-empty text")
    return result


def _require_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise InventoryV3ProtocolV2Error(f"{label} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise InventoryV3ProtocolV2Error(f"{label} keys must be text")
    return value


def _require_list(value: Mapping[str, object], key: str) -> list[object]:
    result = value.get(key)
    if not isinstance(result, list):
        raise InventoryV3ProtocolV2Error(f"{key} must be a list")
    return result


def _require_exact_keys(value: Mapping[str, object], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise InventoryV3ProtocolV2Error(
            f"{label} keys differ: missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def _parse_utc(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise InventoryV3ProtocolV2Error(f"{label} must be canonical UTC text")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise InventoryV3ProtocolV2Error(f"{label} is not a UTC timestamp") from exc
    if parsed.tzinfo != UTC:
        raise InventoryV3ProtocolV2Error(f"{label} must be UTC")
    return parsed


def _parse_git_time(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise InventoryV3ProtocolV2Error(f"{label} is not an ISO Git time") from exc
    if parsed.tzinfo is None:
        raise InventoryV3ProtocolV2Error(f"{label} lacks a timezone")
    return parsed.astimezone(UTC)


def _format_utc(value: datetime) -> str:
    normalized = value.astimezone(UTC)
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _read_canonical_json(
    path: Path,
    *,
    schema: str | None,
    label: str,
    require_sidecar: bool = True,
) -> tuple[Mapping[str, object], bytes]:
    _assert_plain_file(path, label)
    payload = path.read_bytes()
    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InventoryV3ProtocolV2Error(f"{label} is not canonical JSON") from exc
    value = _require_mapping(decoded, label)
    if _canonical_bytes(value) != payload:
        raise InventoryV3ProtocolV2Error(f"{label} is not canonical JSON")
    if schema is not None and value.get("schema") != schema:
        raise InventoryV3ProtocolV2Error(f"{label} schema differs")
    if require_sidecar:
        sidecar = path.with_suffix(path.suffix + ".sha256")
        _assert_plain_file(sidecar, f"{label} sidecar")
        expected = f"{_sha256(payload)}  {path.name}\n".encode("ascii")
        if sidecar.read_bytes() != expected:
            raise InventoryV3ProtocolV2Error(f"{label} sidecar mismatch")
    return value, payload


def _write_canonical_exclusive(path: Path, value: Mapping[str, object]) -> str:
    payload = _canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_plain_directory(path.parent, "output parent")
    digest = _sha256(payload)
    sidecar = path.with_suffix(path.suffix + ".sha256")
    sidecar_payload = f"{digest}  {path.name}\n".encode("ascii")
    owned: list[tuple[Path, tuple[int, int]]] = []
    descriptors: list[int] = []
    completed_successfully = False
    try:
        primary_fd, primary_identity = _create_owned_file(path, payload)
        descriptors.append(primary_fd)
        owned.append((path, primary_identity))
        sidecar_fd, sidecar_identity = _create_owned_file(sidecar, sidecar_payload)
        descriptors.append(sidecar_fd)
        owned.append((sidecar, sidecar_identity))
        if _read_owned_file(path, primary_identity) != payload:
            raise InventoryV3ProtocolV2Error(f"output readback mismatch: {path}")
        if _read_owned_file(sidecar, sidecar_identity) != sidecar_payload:
            raise InventoryV3ProtocolV2Error(f"output sidecar readback mismatch: {sidecar}")
        completed_successfully = True
    except FileExistsError as exc:
        occupied = Path(exc.filename) if exc.filename else path
        raise InventoryV3ProtocolV2Error(f"output already exists: {occupied}") from exc
    finally:
        for descriptor in descriptors:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if not completed_successfully:
            for owned_path, identity in reversed(owned):
                _unlink_owned_file(owned_path, identity)
    return digest


def _create_owned_file(path: Path, payload: bytes) -> tuple[int, tuple[int, int]]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    identity: tuple[int, int] | None = None
    try:
        opened = os.fstat(descriptor)
        _require_plain_single_link(opened, f"new output {path}")
        identity = (int(opened.st_dev), int(opened.st_ino))
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("short output write")
            offset += written
        os.fsync(descriptor)
        after = os.fstat(descriptor)
        _require_plain_single_link(after, f"new output {path}")
        if (int(after.st_dev), int(after.st_ino)) != identity or after.st_size != len(payload):
            raise InventoryV3ProtocolV2Error(f"output identity changed: {path}")
        return descriptor, identity
    except Exception:
        os.close(descriptor)
        if identity is not None:
            _unlink_owned_file(path, identity)
        raise


def _read_owned_file(path: Path, identity: tuple[int, int]) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        _require_plain_single_link(opened, f"owned output {path}")
        if (int(opened.st_dev), int(opened.st_ino)) != identity:
            raise InventoryV3ProtocolV2Error(f"output was replaced: {path}")
        blocks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            blocks.append(block)
        after = os.fstat(descriptor)
        if _stable_stat_fingerprint(opened) != _stable_stat_fingerprint(after):
            raise InventoryV3ProtocolV2Error(f"output changed during readback: {path}")
        return b"".join(blocks)
    finally:
        os.close(descriptor)


def _copy_file_exclusive(source: Path, destination: Path, label: str) -> None:
    _assert_plain_file(source, label)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _assert_plain_directory(destination.parent, "copy destination parent")
    payload = source.read_bytes()
    descriptor, identity = _create_owned_file(destination, payload)
    try:
        os.close(descriptor)
        if _read_owned_file(destination, identity) != payload:
            raise InventoryV3ProtocolV2Error(
                f"exclusive evidence copy readback differs: {destination}"
            )
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        _unlink_owned_file(destination, identity)
        raise


def _unlink_owned_file(path: Path, identity: tuple[int, int]) -> None:
    try:
        current = path.lstat()
        if (
            stat.S_ISREG(current.st_mode)
            and not _is_reparse(current)
            and (int(current.st_dev), int(current.st_ino)) == identity
        ):
            path.unlink()
    except OSError:
        return


def _assert_plain_file(path: Path, label: str) -> None:
    try:
        absolute = path.absolute()
        resolved = path.resolve(strict=True)
        info = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise InventoryV3ProtocolV2Error(f"{label} is unavailable") from exc
    if path.is_symlink() or resolved != absolute or not stat.S_ISREG(info.st_mode):
        raise InventoryV3ProtocolV2Error(f"{label} is redirected or not regular")
    attributes = getattr(info, "st_file_attributes", 0)
    if attributes & 0x400:
        raise InventoryV3ProtocolV2Error(f"{label} is a reparse point")
    if info.st_nlink != 1:
        raise InventoryV3ProtocolV2Error(f"{label} is hardlink-aliased")


def _is_reparse(value: os.stat_result) -> bool:
    return stat.S_ISLNK(value.st_mode) or bool(int(getattr(value, "st_file_attributes", 0)) & 0x400)


def _require_plain_single_link(value: os.stat_result, label: str) -> None:
    if _is_reparse(value) or not stat.S_ISREG(value.st_mode):
        raise InventoryV3ProtocolV2Error(f"{label} is redirected or not regular")
    if value.st_nlink != 1:
        raise InventoryV3ProtocolV2Error(f"{label} is hardlink-aliased")


def _assert_plain_directory(path: Path, label: str) -> None:
    try:
        absolute = path.absolute()
        resolved = path.resolve(strict=True)
        info = path.lstat()
    except OSError as exc:
        raise InventoryV3ProtocolV2Error(f"{label} is unavailable") from exc
    if resolved != absolute or _is_reparse(info) or not stat.S_ISDIR(info.st_mode):
        raise InventoryV3ProtocolV2Error(f"{label} is redirected or not a directory")


def _stable_changed_ns(value: os.stat_result) -> int:
    if os.name == "nt":
        return int(getattr(value, "st_birthtime_ns", value.st_ctime_ns))
    return int(value.st_ctime_ns)


def _stable_stat_fingerprint(value: os.stat_result) -> tuple[int, ...]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_mode),
        int(value.st_nlink),
        int(value.st_size),
        int(value.st_mtime_ns),
        _stable_changed_ns(value),
        int(getattr(value, "st_file_attributes", 0)),
    )


def _owned_path(root: Path, relative: str, label: str) -> Path:
    pure = PurePosixPath(relative)
    if (
        not relative
        or pure.is_absolute()
        or ".." in pure.parts
        or "." in pure.parts
        or "\\" in relative
        or ":" in relative
    ):
        raise InventoryV3ProtocolV2Error(f"{label} path escapes its root")
    result = root.joinpath(*pure.parts)
    root_absolute = root.absolute()
    try:
        if os.path.commonpath((str(root_absolute), str(result.absolute()))) != str(root_absolute):
            raise InventoryV3ProtocolV2Error(f"{label} path escapes its root")
    except ValueError as exc:
        raise InventoryV3ProtocolV2Error(f"{label} path escapes its root") from exc
    return result


def _path_is_occupied(path: Path, label: str) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise InventoryV3ProtocolV2Error(f"{label} occupancy is unreadable") from exc
    return True


def _require_precapture_state_unoccupied(
    paths: ProtocolV2Paths,
    *,
    include_attempt_root: bool,
) -> None:
    """Revalidate every one-shot namespace immediately before live capture."""

    attempt_anchor = paths.attempt_base_root or paths.attempt_root.parent
    legacy_reservation = (
        attempt_anchor
        / "Mining-Automation"
        / "inventory-positive-v3-independent-reservations"
        / f"{PROTOCOL_V1_LOCK_SHA256}.json"
    )
    if _path_is_occupied(legacy_reservation, "pre-capture legacy reservation"):
        raise InventoryV3ProtocolV2Error(
            "pre-capture reservation or fixed output path is already occupied"
        )
    _assert_plain_descendant_path(
        attempt_anchor,
        legacy_reservation,
        "legacy Windows-user reservation",
    )
    candidates = [
        paths.source_campaign_root,
        paths.workspace_root,
        paths.result_root,
    ]
    if include_attempt_root:
        candidates.append(paths.attempt_root)
    if any(_path_is_occupied(path, "pre-capture one-shot path") for path in candidates):
        raise InventoryV3ProtocolV2Error(
            "pre-capture reservation or fixed output path is already occupied"
        )


def _producer_user_local_app_data() -> Path:
    if sys.platform != "win32":
        raise InventoryV3ProtocolV2Error(
            "production V2 attempts require native Windows LocalAppData"
        )
    import ctypes  # imported only on the supported producer platform

    buffer = ctypes.create_unicode_buffer(32768)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    result = shell32.SHGetFolderPathW(None, 0x001C, None, 0, buffer)
    if result != 0 or not buffer.value:
        raise InventoryV3ProtocolV2Error("Windows LocalAppData is unavailable")
    return Path(buffer.value)


def _verify_base_repository_state(
    repository_root: Path,
    expected_head: str,
    *,
    source_mode: bool,
) -> tuple[Path, str]:
    _require_lower_hex(expected_head, 40, "expected_head")
    root = repository_root.resolve(strict=True)
    actual_root = Path(_git(root, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if actual_root != root:
        raise InventoryV3ProtocolV2Error("repository root differs from Git root")
    actual_head = _git(root, "rev-parse", "HEAD")
    if actual_head != expected_head:
        raise InventoryV3ProtocolV2Error(
            f"Git HEAD mismatch: expected {expected_head}, got {actual_head}"
        )
    if _git(root, "status", "--porcelain=v1"):
        raise InventoryV3ProtocolV2Error("clean exact-head worktree is required")
    if _git(root, "rev-parse", "--is-shallow-repository") != "false":
        raise InventoryV3ProtocolV2Error("full Git history is required")
    if _git(root, "replace", "-l"):
        raise InventoryV3ProtocolV2Error("Git replace refs are forbidden")
    grafts_text = _git(root, "rev-parse", "--git-path", "info/grafts")
    grafts = Path(grafts_text)
    if not grafts.is_absolute():
        grafts = root / grafts
    if grafts.is_file() and grafts.read_bytes().strip():
        raise InventoryV3ProtocolV2Error("Git grafts are forbidden")
    for identity in (FROZEN_V3_HEAD, PROTOCOL_V1_SOURCE_HEAD, PROTOCOL_V1_LOCK_HEAD):
        if _git(root, "cat-file", "-t", identity) != "commit":
            raise InventoryV3ProtocolV2Error(f"required frozen commit missing: {identity}")
    if _git(root, "show", "-s", "--format=%P", PROTOCOL_V1_LOCK_HEAD) != (PROTOCOL_V1_SOURCE_HEAD):
        raise InventoryV3ProtocolV2Error("frozen v1 P -> L direct ancestry changed")
    ancestor = subprocess.run(
        (
            "git",
            "-C",
            str(root),
            "merge-base",
            "--is-ancestor",
            FROZEN_V3_HEAD,
            PROTOCOL_V1_SOURCE_HEAD,
        ),
        check=False,
        capture_output=True,
    )
    if ancestor.returncode != 0:
        raise InventoryV3ProtocolV2Error("frozen V3 is not an ancestor of v1 P")
    if source_mode:
        parent = _git(root, "show", "-s", "--format=%P", actual_head)
        if parent != PROTOCOL_V1_LOCK_HEAD:
            raise InventoryV3ProtocolV2Error("P2 must be a direct child of exact v1 L")
    return root, actual_head


def _verify_exact_p2_delta(root: Path, source_head: str) -> None:
    """Require P2 to introduce exactly the reviewed V2 source/test surface."""

    raw = _git(
        root,
        "diff-tree",
        "--no-commit-id",
        "--name-status",
        "--no-renames",
        "-r",
        source_head,
    )
    entries: list[tuple[str, str]] = []
    for line in raw.splitlines():
        fields = line.split("\t")
        if len(fields) != 2:
            raise InventoryV3ProtocolV2Error("P2 changed-path evidence is malformed")
        entries.append((fields[0], fields[1]))
    expected = {("A", path) for path in _P2_CHANGED_PATHS}
    if set(entries) != expected or len(entries) != len(expected):
        raise InventoryV3ProtocolV2Error(
            "P2 must add exactly the fixed Protocol V2 source/test allowlist"
        )


def _frozen_v1_locked_blob_map(root: Path) -> dict[str, str]:
    payload = _git_bytes(
        root,
        "show",
        f"{PROTOCOL_V1_LOCK_HEAD}:{_V1_LOCK_PATH.as_posix()}",
    )
    if _sha256(payload) != PROTOCOL_V1_LOCK_SHA256:
        raise InventoryV3ProtocolV2Error("frozen v1 protocol lock SHA-256 differs")
    expected_sidecar = (f"{PROTOCOL_V1_LOCK_SHA256}  {_V1_LOCK_PATH.name}\n").encode("ascii")
    if (
        _git_bytes(
            root,
            "show",
            f"{PROTOCOL_V1_LOCK_HEAD}:{_V1_LOCK_SIDECAR_PATH.as_posix()}",
        )
        != expected_sidecar
    ):
        raise InventoryV3ProtocolV2Error("frozen v1 protocol lock sidecar differs")
    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InventoryV3ProtocolV2Error("frozen v1 lock is not JSON") from exc
    lock = _require_mapping(decoded, "frozen v1 lock")
    protocol = _require_mapping(lock.get("protocol"), "frozen v1 protocol")
    capture = _require_mapping(lock.get("approved_passive_capture"), "frozen v1 capture")
    result: dict[str, str] = {}
    for raw in (
        *_require_list(protocol, "locked_git_blobs"),
        *_require_list(capture, "source_git_blobs"),
    ):
        entry = _require_mapping(raw, "frozen v1 locked blob")
        _require_exact_keys(entry, {"git_blob", "path"}, "frozen v1 locked blob")
        path = _require_text(entry, "path")
        blob = _require_lower_hex(entry.get("git_blob"), 40, f"frozen blob for {path}")
        previous = result.get(path)
        if previous is not None and previous != blob:
            raise InventoryV3ProtocolV2Error(f"frozen v1 lock conflicts for path: {path}")
        result[path] = blob
    for frozen_path in (_V1_LOCK_PATH, _V1_LOCK_SIDECAR_PATH):
        result[frozen_path.as_posix()] = _git(
            root,
            "rev-parse",
            f"{PROTOCOL_V1_LOCK_HEAD}:{frozen_path.as_posix()}",
        )
    return result


def build_protocol_v2_lock(
    repository_root: Path,
    *,
    expected_source_head: str,
) -> Mapping[str, object]:
    """Build the deterministic P2 lock payload; caller writes only in L2."""

    root, source_head = _verify_base_repository_state(
        repository_root,
        expected_source_head,
        source_mode=True,
    )
    _verify_exact_p2_delta(root, source_head)
    prereg_path = root.joinpath(*_V2_PREREGISTRATION_PATH.parts)
    prereg_payload = prereg_path.read_bytes()
    if _sha256(prereg_payload) != PROTOCOL_V2_PREREGISTRATION_SHA256:
        raise InventoryV3ProtocolV2Error("V2 preregistration SHA-256 differs")
    expected_prereg_sidecar = (
        f"{PROTOCOL_V2_PREREGISTRATION_SHA256}  {_V2_PREREGISTRATION_PATH.name}\n"
    ).encode("ascii")
    prereg_sidecar_path = root.joinpath(*_V2_PREREGISTRATION_SIDECAR_PATH.parts)
    if prereg_sidecar_path.read_bytes() != expected_prereg_sidecar:
        raise InventoryV3ProtocolV2Error("V2 preregistration sidecar differs")
    frozen_v1_blobs = _frozen_v1_locked_blob_map(root)
    paths = set(_V2_SOURCE_PATHS)
    paths.update(frozen_v1_blobs)
    paths.update(_DEVELOPMENT_METADATA_PATHS)
    bindings: list[GitBlobBinding] = []
    for path in sorted(paths):
        blob = _git(root, "rev-parse", f"{source_head}:{path}")
        _require_lower_hex(blob, 40, f"Git blob for {path}")
        frozen_blob = frozen_v1_blobs.get(path)
        if frozen_blob is not None and blob != frozen_blob:
            raise InventoryV3ProtocolV2Error(f"P2 changes frozen v1 evaluator/capture blob: {path}")
        if root.joinpath(*PurePosixPath(path).parts).read_bytes() != _git_bytes(
            root, "show", f"{source_head}:{path}"
        ):
            raise InventoryV3ProtocolV2Error(f"worktree differs from P2: {path}")
        bindings.append(GitBlobBinding(path=path, git_blob=blob))
    v2_authorization_path = _V2_LIVE_AUTHORIZATION_PATH.as_posix()
    v2_authorization_sidecar_path = _V2_LIVE_AUTHORIZATION_SIDECAR_PATH.as_posix()
    legacy_authorization_path = _LIVE_AUTHORIZATION_PATH.as_posix()
    initial_legacy_authorization = _git_bytes(
        root, "show", f"{source_head}:{legacy_authorization_path}"
    )
    frozen_legacy_authorization = _git_bytes(
        root, "show", f"{PROTOCOL_V1_LOCK_HEAD}:{legacy_authorization_path}"
    )
    if initial_legacy_authorization != frozen_legacy_authorization:
        raise InventoryV3ProtocolV2Error(
            "P2 changes the frozen empty legacy live authorization registry"
        )
    if _git(
        root,
        "log",
        "--full-history",
        "--format=%H",
        f"{PROTOCOL_V1_LOCK_HEAD}..{source_head}",
        "--",
        legacy_authorization_path,
    ):
        raise InventoryV3ProtocolV2Error("legacy live authorization registry was touched before P2")
    initial_authorization = _git_bytes(root, "show", f"{source_head}:{v2_authorization_path}")
    if _sha256(initial_authorization) != _V2_EMPTY_LIVE_AUTHORIZATION_SHA256:
        raise InventoryV3ProtocolV2Error("P2 V2 live authorization is not empty")
    return {
        "activation_allowed": False,
        "approved_passive_capture": {
            "build_sha": PROTOCOL_V1_SOURCE_HEAD,
            "capture_configuration_id": CAPTURE_CONFIGURATION_ID,
            "legacy_live_authorization_initial_git_blob": _git(
                root,
                "rev-parse",
                f"{PROTOCOL_V1_LOCK_HEAD}:{legacy_authorization_path}",
            ),
            "legacy_live_authorization_initial_sha256": _sha256(frozen_legacy_authorization),
            "live_authorization_path": _LIVE_AUTHORIZATION_PATH.as_posix(),
            "protocol_v2_live_authorization_path": (_V2_LIVE_AUTHORIZATION_PATH.as_posix()),
            "protocol_v2_live_authorization_initial_git_blob": _git(
                root, "rev-parse", f"{source_head}:{v2_authorization_path}"
            ),
            "protocol_v2_live_authorization_initial_sha256": (_V2_EMPTY_LIVE_AUTHORIZATION_SHA256),
            "protocol_v2_live_authorization_sidecar_initial_git_blob": _git(
                root, "rev-parse", f"{source_head}:{v2_authorization_sidecar_path}"
            ),
            "reservation_scope": "windows-user-local-not-host-global",
        },
        "frozen_candidate_head_sha": FROZEN_V3_HEAD,
        "live_validation_authorized": False,
        "predecessor": {
            "protocol_lock_git_commit_sha": PROTOCOL_V1_LOCK_HEAD,
            "protocol_lock_sha256": PROTOCOL_V1_LOCK_SHA256,
            "protocol_source_git_commit_sha": PROTOCOL_V1_SOURCE_HEAD,
        },
        "preregistration_sha256": PROTOCOL_V2_PREREGISTRATION_SHA256,
        "protocol": {
            "id": PROTOCOL_V2_ID,
            "locked_git_blobs": [entry.to_dict() for entry in bindings],
            "source_commit_sha": source_head,
            "version": PROTOCOL_V2_VERSION,
        },
        "schema": PROTOCOL_V2_LOCK_SCHEMA,
    }


def verify_protocol_v2_repository(
    repository_root: Path,
    *,
    expected_head: str,
) -> ProtocolV2LockBinding:
    """Verify exact F -> P -> L -> P2 -> L2 history and V2 source closure."""

    root, head = _verify_base_repository_state(
        repository_root,
        expected_head,
        source_mode=False,
    )
    lock_path = root.joinpath(*_V2_LOCK_PATH.parts)
    lock, lock_payload = _read_canonical_json(
        lock_path,
        schema=PROTOCOL_V2_LOCK_SCHEMA,
        label="V2 protocol lock",
    )
    _require_exact_keys(
        lock,
        {
            "activation_allowed",
            "approved_passive_capture",
            "frozen_candidate_head_sha",
            "live_validation_authorized",
            "predecessor",
            "preregistration_sha256",
            "protocol",
            "schema",
        },
        "V2 protocol lock",
    )
    if (
        lock.get("activation_allowed") is not False
        or lock.get("live_validation_authorized") is not False
        or lock.get("frozen_candidate_head_sha") != FROZEN_V3_HEAD
        or lock.get("preregistration_sha256") != PROTOCOL_V2_PREREGISTRATION_SHA256
    ):
        raise InventoryV3ProtocolV2Error("V2 lock authority or candidate changed")
    predecessor = _require_mapping(lock.get("predecessor"), "V2 predecessor")
    _require_exact_keys(
        predecessor,
        {
            "protocol_lock_git_commit_sha",
            "protocol_lock_sha256",
            "protocol_source_git_commit_sha",
        },
        "V2 predecessor",
    )
    if predecessor != {
        "protocol_lock_git_commit_sha": PROTOCOL_V1_LOCK_HEAD,
        "protocol_lock_sha256": PROTOCOL_V1_LOCK_SHA256,
        "protocol_source_git_commit_sha": PROTOCOL_V1_SOURCE_HEAD,
    }:
        raise InventoryV3ProtocolV2Error("V2 predecessor identity changed")
    approved_capture = _require_mapping(
        lock.get("approved_passive_capture"), "V2 approved passive capture"
    )
    expected_capture_keys = {
        "build_sha",
        "capture_configuration_id",
        "legacy_live_authorization_initial_git_blob",
        "legacy_live_authorization_initial_sha256",
        "live_authorization_path",
        "protocol_v2_live_authorization_initial_git_blob",
        "protocol_v2_live_authorization_initial_sha256",
        "protocol_v2_live_authorization_path",
        "protocol_v2_live_authorization_sidecar_initial_git_blob",
        "reservation_scope",
    }
    _require_exact_keys(approved_capture, expected_capture_keys, "V2 approved passive capture")
    if (
        approved_capture.get("build_sha") != PROTOCOL_V1_SOURCE_HEAD
        or approved_capture.get("capture_configuration_id") != CAPTURE_CONFIGURATION_ID
        or approved_capture.get("live_authorization_path") != _LIVE_AUTHORIZATION_PATH.as_posix()
        or approved_capture.get("protocol_v2_live_authorization_path")
        != _V2_LIVE_AUTHORIZATION_PATH.as_posix()
        or approved_capture.get("protocol_v2_live_authorization_initial_sha256")
        != _V2_EMPTY_LIVE_AUTHORIZATION_SHA256
        or approved_capture.get("reservation_scope") != "windows-user-local-not-host-global"
    ):
        raise InventoryV3ProtocolV2Error("V2 passive-capture binding changed")
    protocol = _require_mapping(lock.get("protocol"), "V2 protocol")
    _require_exact_keys(
        protocol,
        {"id", "locked_git_blobs", "source_commit_sha", "version"},
        "V2 protocol",
    )
    if protocol.get("id") != PROTOCOL_V2_ID or protocol.get("version") != (PROTOCOL_V2_VERSION):
        raise InventoryV3ProtocolV2Error("V2 protocol identity changed")
    source_head = _require_lower_hex(protocol.get("source_commit_sha"), 40, "P2 source commit")
    if _git(root, "show", "-s", "--format=%P", source_head) != PROTOCOL_V1_LOCK_HEAD:
        raise InventoryV3ProtocolV2Error("P2 is not a direct child of exact v1 L")
    _verify_exact_p2_delta(root, source_head)
    prereg_payload = _git_bytes(
        root,
        "show",
        f"{source_head}:{_V2_PREREGISTRATION_PATH.as_posix()}",
    )
    expected_prereg_sidecar = (
        f"{PROTOCOL_V2_PREREGISTRATION_SHA256}  {_V2_PREREGISTRATION_PATH.name}\n"
    ).encode("ascii")
    prereg_sidecar_payload = _git_bytes(
        root,
        "show",
        f"{source_head}:{_V2_PREREGISTRATION_SIDECAR_PATH.as_posix()}",
    )
    if (
        _sha256(prereg_payload) != PROTOCOL_V2_PREREGISTRATION_SHA256
        or prereg_sidecar_payload != expected_prereg_sidecar
        or root.joinpath(*_V2_PREREGISTRATION_PATH.parts).read_bytes() != prereg_payload
        or root.joinpath(*_V2_PREREGISTRATION_SIDECAR_PATH.parts).read_bytes()
        != expected_prereg_sidecar
    ):
        raise InventoryV3ProtocolV2Error("V2 preregistration or its exact sidecar differs")
    legacy_initial_path = _LIVE_AUTHORIZATION_PATH.as_posix()
    frozen_legacy_payload = _git_bytes(
        root,
        "show",
        f"{PROTOCOL_V1_LOCK_HEAD}:{legacy_initial_path}",
    )
    if (
        approved_capture.get("legacy_live_authorization_initial_git_blob")
        != _git(
            root,
            "rev-parse",
            f"{PROTOCOL_V1_LOCK_HEAD}:{legacy_initial_path}",
        )
        or approved_capture.get("legacy_live_authorization_initial_sha256")
        != _sha256(frozen_legacy_payload)
        or _git_bytes(root, "show", f"{source_head}:{legacy_initial_path}") != frozen_legacy_payload
        or _git(
            root,
            "log",
            "--full-history",
            "--format=%H",
            f"{PROTOCOL_V1_LOCK_HEAD}..{source_head}",
            "--",
            legacy_initial_path,
        )
    ):
        raise InventoryV3ProtocolV2Error("legacy live authorization registry changed before P2")
    initial_auth_path = _V2_LIVE_AUTHORIZATION_PATH.as_posix()
    initial_sidecar_path = _V2_LIVE_AUTHORIZATION_SIDECAR_PATH.as_posix()
    initial_auth_payload = _git_bytes(root, "show", f"{source_head}:{initial_auth_path}")
    if _sha256(initial_auth_payload) != _V2_EMPTY_LIVE_AUTHORIZATION_SHA256:
        raise InventoryV3ProtocolV2Error("P2 V2 live authorization was not empty")
    expected_initial_sidecar = (
        f"{_V2_EMPTY_LIVE_AUTHORIZATION_SHA256}  {_V2_LIVE_AUTHORIZATION_PATH.name}\n"
    ).encode("ascii")
    if _git_bytes(root, "show", f"{source_head}:{initial_sidecar_path}") != (
        expected_initial_sidecar
    ):
        raise InventoryV3ProtocolV2Error("P2 V2 authorization sidecar differs")
    if approved_capture.get("protocol_v2_live_authorization_initial_git_blob") != _git(
        root, "rev-parse", f"{source_head}:{initial_auth_path}"
    ):
        raise InventoryV3ProtocolV2Error("V2 initial authorization blob differs")
    if approved_capture.get("protocol_v2_live_authorization_sidecar_initial_git_blob") != _git(
        root, "rev-parse", f"{source_head}:{initial_sidecar_path}"
    ):
        raise InventoryV3ProtocolV2Error("V2 initial authorization sidecar blob differs")
    lock_commits = _git(
        root,
        "log",
        "--full-history",
        "--diff-filter=A",
        "--format=%H",
        head,
        "--",
        _V2_LOCK_PATH.as_posix(),
    ).splitlines()
    if len(lock_commits) != 1:
        raise InventoryV3ProtocolV2Error("V2 lock must have one unique introduction")
    lock_commit = lock_commits[0]
    if _git(root, "show", "-s", "--format=%P", lock_commit) != source_head:
        raise InventoryV3ProtocolV2Error("L2 must be a direct lock-only child of P2")
    changed = set(
        _git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", lock_commit).splitlines()
    )
    if changed != {_V2_LOCK_PATH.as_posix(), _V2_LOCK_SIDECAR_PATH.as_posix()}:
        raise InventoryV3ProtocolV2Error("L2 is not an exact two-file lock-only commit")
    for lock_relative in (
        _V2_LOCK_PATH.as_posix(),
        _V2_LOCK_SIDECAR_PATH.as_posix(),
    ):
        current_path = root.joinpath(*PurePosixPath(lock_relative).parts)
        if current_path.read_bytes() != _git_bytes(root, "show", f"{lock_commit}:{lock_relative}"):
            raise InventoryV3ProtocolV2Error(
                f"current V2 lock artifact differs from exact L2: {lock_relative}"
            )
        if _git(root, "rev-parse", f"{head}:{lock_relative}") != _git(
            root, "rev-parse", f"{lock_commit}:{lock_relative}"
        ):
            raise InventoryV3ProtocolV2Error(
                f"current V2 lock blob differs from exact L2: {lock_relative}"
            )
        if _git(
            root,
            "log",
            "--full-history",
            "--format=%H",
            f"{lock_commit}..{head}",
            "--",
            lock_relative,
        ):
            raise InventoryV3ProtocolV2Error(f"V2 lock artifact changed after L2: {lock_relative}")
    source_time = _parse_git_time(
        _git(root, "show", "-s", "--format=%cI", source_head), "P2 Git time"
    )
    lock_time = _parse_git_time(
        _git(root, "show", "-s", "--format=%cI", lock_commit), "L2 Git time"
    )
    if lock_time <= source_time:
        raise InventoryV3ProtocolV2Error("L2 Git time must be later than P2")
    raw_entries = _require_list(protocol, "locked_git_blobs")
    bindings: list[GitBlobBinding] = []
    seen: set[str] = set()
    for raw in raw_entries:
        item = _require_mapping(raw, "V2 locked blob")
        _require_exact_keys(item, {"git_blob", "path"}, "V2 locked blob")
        path = _require_text(item, "path")
        blob = _require_lower_hex(item.get("git_blob"), 40, f"blob for {path}")
        if path in seen:
            raise InventoryV3ProtocolV2Error(f"duplicate V2 locked path: {path}")
        seen.add(path)
        if _git(root, "rev-parse", f"{source_head}:{path}") != blob:
            raise InventoryV3ProtocolV2Error(f"P2 blob differs from V2 lock: {path}")
        if _git(root, "rev-parse", f"{head}:{path}") != blob:
            raise InventoryV3ProtocolV2Error(f"current blob differs from V2 lock: {path}")
        if _git(
            root,
            "log",
            "--full-history",
            "--format=%H",
            f"{source_head}..{head}",
            "--",
            path,
        ):
            raise InventoryV3ProtocolV2Error(f"V2 locked path changed after P2: {path}")
        bindings.append(GitBlobBinding(path=path, git_blob=blob))
    if not set(_V2_SOURCE_PATHS).issubset(seen):
        raise InventoryV3ProtocolV2Error("V2 lock omits coordinator source")
    if not set(_DEVELOPMENT_METADATA_PATHS).issubset(seen):
        raise InventoryV3ProtocolV2Error("V2 lock omits frozen development identity metadata")
    frozen_v1_blobs = _frozen_v1_locked_blob_map(root)
    locked_by_path = {binding.path: binding.git_blob for binding in bindings}
    for path, frozen_blob in frozen_v1_blobs.items():
        if locked_by_path.get(path) != frozen_blob:
            raise InventoryV3ProtocolV2Error(
                f"V2 lock changes or omits frozen v1 evaluator/capture blob: {path}"
            )
    post_source_executable = _git(
        root,
        "log",
        "--full-history",
        "--format=%H",
        f"{source_head}..{head}",
        "--",
        "src/mining_automation",
        "tools",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "sitecustomize.py",
        "usercustomize.py",
    )
    if post_source_executable:
        raise InventoryV3ProtocolV2Error("executable source changed after P2")
    return ProtocolV2LockBinding(
        repository_root=root,
        evaluator_head_sha=head,
        source_commit_sha=source_head,
        lock_commit_sha=lock_commit,
        lock_sha256=_sha256(lock_payload),
        locked_git_blobs=tuple(bindings),
    )


def verify_live_authorization(
    protocol: ProtocolV2LockBinding,
    *,
    access_hook: AccessHook | None = None,
) -> LiveAuthorizationBinding:
    """Verify the single atomic v1+V2 source authorization commit."""

    root = protocol.repository_root
    head = protocol.evaluator_head_sha
    _emit(access_hook, "preflight", "live_authorization_metadata", None)
    legacy_path = root.joinpath(*_LIVE_AUTHORIZATION_PATH.parts)
    legacy, legacy_payload = _read_canonical_json(
        legacy_path,
        schema=LIVE_AUTHORIZATION_SCHEMA,
        label="legacy live authorization registry",
        require_sidecar=False,
    )
    _require_exact_keys(
        legacy,
        {"activation_allowed", "authorizations", "schema"},
        "legacy live authorization registry",
    )
    if legacy.get("activation_allowed") is not False:
        raise InventoryV3ProtocolV2Error("legacy authorization grants activation")
    legacy_entries = _require_list(legacy, "authorizations")
    if len(legacy_entries) != 1:
        raise InventoryV3ProtocolV2Error(
            "LIVE INVENTORY CAMPAIGN NOT YET AUTHORIZED: one legacy entry required"
        )
    legacy_entry = _require_mapping(legacy_entries[0], "legacy authorization")
    legacy_expected = {
        "capture_build_sha": PROTOCOL_V1_SOURCE_HEAD,
        "capture_configuration_id": CAPTURE_CONFIGURATION_ID,
        "protocol_lock_git_commit_sha": PROTOCOL_V1_LOCK_HEAD,
        "protocol_lock_sha256": PROTOCOL_V1_LOCK_SHA256,
        "status": LIVE_AUTHORIZATION_STATUS,
    }
    _require_exact_keys(
        legacy_entry, set(legacy_expected) | {"authorization_id"}, "legacy authorization"
    )
    authorization_id = _require_lower_hex(
        legacy_entry.get("authorization_id"), 64, "authorization_id"
    )
    if any(legacy_entry.get(key) != value for key, value in legacy_expected.items()):
        raise InventoryV3ProtocolV2Error("legacy authorization binding differs")

    v2_path = root.joinpath(*_V2_LIVE_AUTHORIZATION_PATH.parts)
    v2_registry, v2_payload = _read_canonical_json(
        v2_path,
        schema=_V2_LIVE_AUTHORIZATION_SCHEMA,
        label="V2 live authorization registry",
    )
    _require_exact_keys(
        v2_registry,
        {"activation_allowed", "authorizations", "schema"},
        "V2 live authorization registry",
    )
    if v2_registry.get("activation_allowed") is not False:
        raise InventoryV3ProtocolV2Error("V2 authorization grants activation")
    v2_entries = _require_list(v2_registry, "authorizations")
    if len(v2_entries) != 1:
        raise InventoryV3ProtocolV2Error(
            "LIVE INVENTORY CAMPAIGN NOT YET AUTHORIZED: one V2 entry required"
        )
    v2_entry = _require_mapping(v2_entries[0], "V2 authorization")
    v2_expected = {
        "authorization_id": authorization_id,
        "capture_build_sha": PROTOCOL_V1_SOURCE_HEAD,
        "capture_configuration_id": CAPTURE_CONFIGURATION_ID,
        "frozen_candidate_head_sha": FROZEN_V3_HEAD,
        "predecessor_protocol_lock_git_commit_sha": PROTOCOL_V1_LOCK_HEAD,
        "predecessor_protocol_lock_sha256": PROTOCOL_V1_LOCK_SHA256,
        "protocol_lock_git_commit_sha": protocol.lock_commit_sha,
        "protocol_lock_sha256": protocol.lock_sha256,
        "protocol_source_git_commit_sha": protocol.source_commit_sha,
        "status": _V2_LIVE_AUTHORIZATION_STATUS,
    }
    _require_exact_keys(v2_entry, set(v2_expected) | {"opaque_receipt_id"}, "V2 authorization")
    if any(v2_entry.get(key) != value for key, value in v2_expected.items()):
        raise InventoryV3ProtocolV2Error("V2 authorization binding differs")
    receipt_value = _require_text(v2_entry, "opaque_receipt_id")
    from .privacy import PreissuedOpaqueReceipt

    receipt = PreissuedOpaqueReceipt(receipt_value)
    expected_authorization_id = _sha256(
        _canonical_data_bytes(
            {
                "capture_build_sha": PROTOCOL_V1_SOURCE_HEAD,
                "capture_configuration_id": CAPTURE_CONFIGURATION_ID,
                "case_sequence": list(REQUIRED_STAGES),
                "frozen_candidate_head_sha": FROZEN_V3_HEAD,
                "opaque_receipt_id": receipt.value,
                "protocol_lock_git_commit_sha": protocol.lock_commit_sha,
                "protocol_lock_sha256": protocol.lock_sha256,
                "protocol_source_git_commit_sha": protocol.source_commit_sha,
            }
        )
    )
    if authorization_id != expected_authorization_id:
        raise InventoryV3ProtocolV2Error(
            "live authorization ID is not the content-bound protocol identity"
        )

    legacy_relative = _LIVE_AUTHORIZATION_PATH.as_posix()
    v2_relative = _V2_LIVE_AUTHORIZATION_PATH.as_posix()
    v2_sidecar_relative = _V2_LIVE_AUTHORIZATION_SIDECAR_PATH.as_posix()
    if _git_bytes(root, "show", f"{head}:{legacy_relative}") != legacy_payload:
        raise InventoryV3ProtocolV2Error("legacy authorization is not committed at HEAD")
    if _git_bytes(root, "show", f"{head}:{v2_relative}") != v2_payload:
        raise InventoryV3ProtocolV2Error("V2 authorization is not committed at HEAD")
    sidecar_payload = v2_path.with_suffix(v2_path.suffix + ".sha256").read_bytes()
    if _git_bytes(root, "show", f"{head}:{v2_sidecar_relative}") != sidecar_payload:
        raise InventoryV3ProtocolV2Error("V2 authorization sidecar is not committed")
    legacy_touches = _git(
        root,
        "log",
        "--full-history",
        "--format=%H",
        f"{PROTOCOL_V1_LOCK_HEAD}..{head}",
        "--",
        legacy_relative,
    ).splitlines()
    if len(legacy_touches) != 1:
        raise InventoryV3ProtocolV2Error(
            "legacy authorization requires exactly one change after frozen v1 L"
        )
    touches: list[list[str]] = [legacy_touches]
    for relative in (v2_relative, v2_sidecar_relative):
        entries = _git(
            root,
            "log",
            "--full-history",
            "--format=%H",
            f"{protocol.lock_commit_sha}..{head}",
            "--",
            relative,
        ).splitlines()
        if len(entries) != 1:
            raise InventoryV3ProtocolV2Error("authorization files require one post-L2 Git change")
        touches.append(entries)
    authorization_commit = legacy_touches[0]
    if any(items[0] != authorization_commit for items in touches[1:]):
        raise InventoryV3ProtocolV2Error("authorization files were not committed atomically")
    changed = set(
        _git(
            root,
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            authorization_commit,
        ).splitlines()
    )
    if changed != {legacy_relative, v2_relative, v2_sidecar_relative}:
        raise InventoryV3ProtocolV2Error(
            "live authorization commit is not an exact three-file source action"
        )
    if _git(root, "show", "-s", "--format=%P", authorization_commit) != (protocol.lock_commit_sha):
        raise InventoryV3ProtocolV2Error(
            "live authorization must be one direct non-merge child of exact L2"
        )
    if head != authorization_commit:
        raise InventoryV3ProtocolV2Error(
            "live execution HEAD must be the exact authorization commit"
        )
    if (
        subprocess.run(
            (
                "git",
                "-C",
                str(root),
                "merge-base",
                "--is-ancestor",
                protocol.lock_commit_sha,
                authorization_commit,
            ),
            check=False,
            capture_output=True,
        ).returncode
        != 0
    ):
        raise InventoryV3ProtocolV2Error("live authorization predates L2")
    authorization_time = _parse_git_time(
        _git(root, "show", "-s", "--format=%cI", authorization_commit),
        "authorization Git time",
    )
    lock_time = _parse_git_time(
        _git(root, "show", "-s", "--format=%cI", protocol.lock_commit_sha),
        "L2 Git time",
    )
    head_time = _parse_git_time(
        _git(root, "show", "-s", "--format=%cI", head), "execution HEAD Git time"
    )
    if authorization_time <= lock_time or authorization_time > head_time:
        raise InventoryV3ProtocolV2Error("live authorization chronology differs")
    return LiveAuthorizationBinding(
        authorization_id=authorization_id,
        git_commit_sha=authorization_commit,
        legacy_registry_git_blob=_git(root, "rev-parse", f"{head}:{legacy_relative}"),
        protocol_v2_registry_git_blob=_git(root, "rev-parse", f"{head}:{v2_relative}"),
        committed_at_utc=_format_utc(authorization_time),
        opaque_receipt_id=receipt.value,
    )


def build_live_authorization_proposal(
    repository_root: Path,
    *,
    expected_lock_head: str,
    opaque_receipt_id: str,
    attempt_base: Path | None = None,
) -> Mapping[str, object]:
    """Return the exact three-file future source action without writing it."""

    protocol = verify_protocol_v2_repository(repository_root, expected_head=expected_lock_head)
    if protocol.evaluator_head_sha != protocol.lock_commit_sha:
        raise InventoryV3ProtocolV2Error("live authorization proposal requires exact clean L2 HEAD")
    from .privacy import PreissuedOpaqueReceipt

    receipt = PreissuedOpaqueReceipt(opaque_receipt_id)
    legacy_path = protocol.repository_root.joinpath(*_LIVE_AUTHORIZATION_PATH.parts)
    legacy, _ = _read_canonical_json(
        legacy_path,
        schema=LIVE_AUTHORIZATION_SCHEMA,
        label="legacy live authorization registry",
        require_sidecar=False,
    )
    v2_path = protocol.repository_root.joinpath(*_V2_LIVE_AUTHORIZATION_PATH.parts)
    v2, v2_payload = _read_canonical_json(
        v2_path,
        schema=_V2_LIVE_AUTHORIZATION_SCHEMA,
        label="V2 live authorization registry",
    )
    if (
        _require_list(legacy, "authorizations")
        or _require_list(v2, "authorizations")
        or _sha256(v2_payload) != _V2_EMPTY_LIVE_AUTHORIZATION_SHA256
    ):
        raise InventoryV3ProtocolV2Error("live authorization registries are not empty")
    identity = {
        "capture_build_sha": PROTOCOL_V1_SOURCE_HEAD,
        "capture_configuration_id": CAPTURE_CONFIGURATION_ID,
        "case_sequence": list(REQUIRED_STAGES),
        "frozen_candidate_head_sha": FROZEN_V3_HEAD,
        "opaque_receipt_id": receipt.value,
        "protocol_lock_git_commit_sha": protocol.lock_commit_sha,
        "protocol_lock_sha256": protocol.lock_sha256,
        "protocol_source_git_commit_sha": protocol.source_commit_sha,
    }
    authorization_id = _sha256(_canonical_data_bytes(identity))
    if attempt_base is None:
        attempt_base = _producer_user_local_app_data()
    paths = ProtocolV2Paths.for_authorization(
        protocol.repository_root,
        authorization_id,
        protocol.lock_sha256,
        attempt_base=attempt_base,
    )
    _assert_disjoint_paths(paths)
    legacy_reservation = (
        attempt_base.resolve(strict=False)
        / "Mining-Automation"
        / "inventory-positive-v3-independent-reservations"
        / f"{PROTOCOL_V1_LOCK_SHA256}.json"
    )
    _assert_plain_descendant_path(
        paths.attempt_base_root or legacy_reservation.parent.parent,
        legacy_reservation,
        "legacy Windows-user reservation",
    )
    occupied_paths = [
        path
        for path in (
            legacy_reservation,
            paths.source_campaign_root,
            paths.workspace_root,
            paths.result_root,
            paths.attempt_root,
        )
        if _path_is_occupied(path, "fixed one-shot path")
    ]
    if occupied_paths:
        raise InventoryV3ProtocolV2Error(
            "one-shot reservation or fixed output path is already occupied"
        )
    approval_payload = _verify_approval_registry_absent(protocol)
    from .producer import observe_windows_identity

    producer_identity_obtainable = observe_windows_identity() is not None
    if not producer_identity_obtainable:
        raise InventoryV3ProtocolV2Error("OS-observed Windows producer identity is unavailable")
    legacy_document = {
        "activation_allowed": False,
        "authorizations": [
            {
                "authorization_id": authorization_id,
                "capture_build_sha": PROTOCOL_V1_SOURCE_HEAD,
                "capture_configuration_id": CAPTURE_CONFIGURATION_ID,
                "protocol_lock_git_commit_sha": PROTOCOL_V1_LOCK_HEAD,
                "protocol_lock_sha256": PROTOCOL_V1_LOCK_SHA256,
                "status": LIVE_AUTHORIZATION_STATUS,
            }
        ],
        "schema": LIVE_AUTHORIZATION_SCHEMA,
    }
    v2_document = {
        "activation_allowed": False,
        "authorizations": [
            {
                "authorization_id": authorization_id,
                "capture_build_sha": PROTOCOL_V1_SOURCE_HEAD,
                "capture_configuration_id": CAPTURE_CONFIGURATION_ID,
                "frozen_candidate_head_sha": FROZEN_V3_HEAD,
                "opaque_receipt_id": receipt.value,
                "predecessor_protocol_lock_git_commit_sha": PROTOCOL_V1_LOCK_HEAD,
                "predecessor_protocol_lock_sha256": PROTOCOL_V1_LOCK_SHA256,
                "protocol_lock_git_commit_sha": protocol.lock_commit_sha,
                "protocol_lock_sha256": protocol.lock_sha256,
                "protocol_source_git_commit_sha": protocol.source_commit_sha,
                "status": _V2_LIVE_AUTHORIZATION_STATUS,
            }
        ],
        "schema": _V2_LIVE_AUTHORIZATION_SCHEMA,
    }
    legacy_authorized_payload = _canonical_bytes(legacy_document)
    v2_authorized_payload = _canonical_bytes(v2_document)
    v2_sha = _sha256(v2_authorized_payload)
    return {
        "activation_allowed": False,
        "authorization_id": authorization_id,
        "files": [
            {
                "content": legacy_document,
                "path": _LIVE_AUTHORIZATION_PATH.as_posix(),
                "sha256": _sha256(legacy_authorized_payload),
            },
            {
                "content": v2_document,
                "path": _V2_LIVE_AUTHORIZATION_PATH.as_posix(),
                "sha256": v2_sha,
            },
            {
                "content_ascii": (f"{v2_sha}  {_V2_LIVE_AUTHORIZATION_PATH.name}\n"),
                "path": _V2_LIVE_AUTHORIZATION_SIDECAR_PATH.as_posix(),
                "sha256": _sha256(
                    f"{v2_sha}  {_V2_LIVE_AUTHORIZATION_PATH.name}\n".encode("ascii")
                ),
            },
        ],
        "promotion_allowed": False,
        "readiness": {
            "approval_registry_sha256": _sha256(approval_payload),
            "fixed_output_paths_unoccupied": True,
            "legacy_user_reservation_unoccupied": True,
            "producer_identity_obtainable": True,
            "reservation_scope": "windows-user-local-not-host-global",
        },
        "schema": "inventory-positive-v3-protocol-v2-live-authorization-proposal-v1",
        "source_commit_required": True,
        "source_registry_modified": False,
        "status": "proposal-only-not-authorized",
    }


def _verify_approval_registry_absent(
    protocol: ProtocolV2LockBinding,
    *,
    access_hook: AccessHook | None = None,
) -> bytes:
    _emit(access_hook, "preflight", "source_approval_absence_metadata", None)
    root = protocol.repository_root
    path = root.joinpath(*_APPROVAL_REGISTRY_PATH.parts)
    registry, payload = _read_canonical_json(
        path,
        schema="inventory-positive-v3-independent-validation-approval-registry-v1",
        label="source approval registry",
    )
    _require_exact_keys(
        registry,
        {"activation_allowed", "entries", "promotion_allowed", "schema"},
        "source approval registry",
    )
    if (
        registry.get("activation_allowed") is not False
        or registry.get("promotion_allowed") is not False
        or _require_list(registry, "entries")
    ):
        raise InventoryV3ProtocolV2Error(
            "source approval must remain exactly absent before terminal conformance"
        )
    sidecar_path = root.joinpath(*_APPROVAL_REGISTRY_SIDECAR_PATH.parts)
    for relative, current in (
        (_APPROVAL_REGISTRY_PATH.as_posix(), payload),
        (_APPROVAL_REGISTRY_SIDECAR_PATH.as_posix(), sidecar_path.read_bytes()),
    ):
        if current != _git_bytes(root, "show", f"{protocol.lock_commit_sha}:{relative}"):
            raise InventoryV3ProtocolV2Error(
                "source approval registry differs from its exact L2 baseline"
            )
        if _git(
            root,
            "log",
            "--full-history",
            "--format=%H",
            f"{protocol.lock_commit_sha}..{protocol.evaluator_head_sha}",
            "--",
            relative,
        ):
            raise InventoryV3ProtocolV2Error(
                "source approval registry was touched before terminal conformance"
            )
    return payload


def _frozen_development_identity_sets(
    protocol: ProtocolV2LockBinding,
) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """Read only locked development metadata needed for independence checks."""

    root = protocol.repository_root
    manifest_path = root.joinpath(*_DEVELOPMENT_MANIFEST_PATH.parts)
    sidecar_path = root.joinpath(*_DEVELOPMENT_MANIFEST_SIDECAR_PATH.parts)
    _assert_plain_file(manifest_path, "frozen development manifest")
    _assert_plain_file(sidecar_path, "frozen development manifest sidecar")
    payload = manifest_path.read_bytes()
    expected_sidecar = (
        f"{_DEVELOPMENT_MANIFEST_SHA256}  {_DEVELOPMENT_MANIFEST_PATH.name}\n"
    ).encode("ascii")
    if (
        _sha256(payload) != _DEVELOPMENT_MANIFEST_SHA256
        or sidecar_path.read_bytes() != expected_sidecar
    ):
        raise InventoryV3ProtocolV2Error("frozen development identity metadata differs")
    try:
        manifest = _require_mapping(
            json.loads(payload),
            "frozen development manifest",
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InventoryV3ProtocolV2Error("frozen development identity metadata is invalid") from exc
    _require_exact_keys(
        manifest,
        {
            "activation_allowed",
            "candidate",
            "cases",
            "dataset_id",
            "fixture_kind",
            "frame_reconstruction",
            "generated",
            "schema_version",
            "warning",
        },
        "frozen development manifest",
    )
    cases = _require_list(manifest, "cases")
    if (
        manifest.get("activation_allowed") is not False
        or manifest.get("dataset_id") != _DEVELOPMENT_DATASET_ID
        or manifest.get("fixture_kind") != "inventory-sanitized-region-replay"
        or manifest.get("schema_version") != 2
        or len(cases) != 16
    ):
        raise InventoryV3ProtocolV2Error("frozen development identity metadata binding differs")
    case_ids: set[str] = set()
    session_ids: set[str] = set()
    capture_ids: set[str] = set()
    for index, raw in enumerate(cases, start=1):
        item = _require_mapping(raw, f"development case {index}")
        truth = _require_mapping(item.get("review_truth"), "development review truth")
        case_ids.add(_require_text(item, "case_id"))
        session_ids.add(_require_text(truth, "session_id"))
        capture_ids.add(_require_text(truth, "capture_id"))
    identity_digests = (
        _sha256(_canonical_data_bytes(sorted(case_ids))),
        _sha256(_canonical_data_bytes(sorted(session_ids))),
        _sha256(_canonical_data_bytes(sorted(capture_ids))),
    )
    if identity_digests != (
        _DEVELOPMENT_CASE_IDS_SHA256,
        _DEVELOPMENT_SESSION_IDS_SHA256,
        _DEVELOPMENT_CAPTURE_IDS_SHA256,
    ):
        raise InventoryV3ProtocolV2Error("frozen development identity sets differ")
    return frozenset(case_ids), frozenset(session_ids), frozenset(capture_ids)


def _require_source_development_identity_disjoint(
    protocol: ProtocolV2LockBinding,
    *,
    session_id: str,
    capture_ids: set[str],
) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    development = _frozen_development_identity_sets(protocol)
    case_ids = {f"{session_id}/{capture_id}" for capture_id in capture_ids}
    if (
        case_ids.intersection(development[0])
        or session_id in development[1]
        or capture_ids.intersection(development[2])
    ):
        raise InventoryV3ProtocolV2Error(
            "independent campaign reuses a frozen development identity"
        )
    return development


def _require_report_development_identity_disjoint(
    report: object,
    development: tuple[frozenset[str], frozenset[str], frozenset[str]],
) -> None:
    cases = getattr(report, "cases", None)
    if not isinstance(cases, tuple) or len(cases) != len(REQUIRED_STAGES):
        raise InventoryV3ProtocolV2Error("frozen evaluator report cases differ")
    for item in cases:
        case_id = getattr(item, "case_id", None)
        if not isinstance(case_id, str) or case_id.count("/") != 1:
            raise InventoryV3ProtocolV2Error("frozen evaluator report identity differs")
        session_id, capture_id = case_id.split("/", maxsplit=1)
        if (
            case_id in development[0]
            or session_id in development[1]
            or capture_id in development[2]
        ):
            raise InventoryV3ProtocolV2Error(
                "frozen evaluator report reuses a development identity"
            )


def _require_manifest_development_identity_disjoint(
    manifest: Mapping[str, object],
    development: tuple[frozenset[str], frozenset[str], frozenset[str]],
) -> None:
    session_id = _require_text(manifest, "session_id")
    cases = _require_list(manifest, "cases")
    if len(cases) != len(REQUIRED_STAGES):
        raise InventoryV3ProtocolV2Error("campaign manifest cases differ")
    case_ids: set[str] = set()
    capture_ids: set[str] = set()
    for index, raw in enumerate(cases, start=1):
        case = _require_mapping(raw, f"campaign case {index}")
        case_id = _require_text(case, "case_id")
        capture_id = _require_text(case, "capture_id")
        if (
            case.get("session_id") != session_id
            or case_id != f"{session_id}/{capture_id}"
            or case_id in case_ids
            or capture_id in capture_ids
        ):
            raise InventoryV3ProtocolV2Error("campaign manifest identity differs")
        case_ids.add(case_id)
        capture_ids.add(capture_id)
    if (
        case_ids.intersection(development[0])
        or session_id in development[1]
        or capture_ids.intersection(development[2])
    ):
        raise InventoryV3ProtocolV2Error(
            "campaign manifest reuses a frozen development identity"
        )


def _require_non_development_dataset_identity(dataset_id: object) -> None:
    if dataset_id == _DEVELOPMENT_DATASET_ID:
        raise InventoryV3ProtocolV2Error(
            "independent campaign reuses the frozen development dataset identity"
        )


def _require_frozen_capture_identity(
    capture_id: str,
    captured_at_utc: str,
    *,
    sequence_index: int,
    stage: str,
) -> datetime:
    captured_at = _parse_utc(captured_at_utc, f"capture {sequence_index} time")
    if captured_at_utc != _format_utc(captured_at):
        raise InventoryV3ProtocolV2Error("source capture timestamp is not canonical")
    expected_capture_id = (
        captured_at_utc.replace("-", "").replace(":", "") + f"-{sequence_index:03d}-{stage}"
    )
    if capture_id != expected_capture_id:
        raise InventoryV3ProtocolV2Error(
            "source capture identity differs from the locked passive capture formula"
        )
    return captured_at


def _require_source_capture_nested_shapes(
    full: Mapping[str, object],
    region: Mapping[str, object],
    owned_frame: Mapping[str, object],
    owned_window: Mapping[str, object],
    *,
    index: int,
) -> None:
    _require_exact_keys(
        full,
        {"height", "path", "pixel_format", "sha256", "size_bytes", "width"},
        f"source full frame {index}",
    )
    _require_exact_keys(
        region,
        {"path", "region", "sha256", "size_bytes"},
        f"source inventory region {index}",
    )
    _require_exact_keys(
        owned_frame,
        {"frame_id", "height", "path", "pixel_format", "sha256", "size_bytes", "width"},
        f"owned frame payload {index}",
    )
    _require_exact_keys(
        owned_window,
        {"class", "handle", "windows_dpi"},
        f"owned frame window {index}",
    )
    frame_id = owned_frame.get("frame_id")
    if not isinstance(frame_id, int) or isinstance(frame_id, bool) or frame_id <= 0:
        raise InventoryV3ProtocolV2Error("owned frame_id is not a positive integer")


def _content_bound_campaign_id(session_id: str) -> str:
    identity = {
        "preregistration_sha256": PROTOCOL_V1_PREREGISTRATION_SHA256,
        "session_id": session_id,
    }
    return "inventory-positive-v3-campaign-" + _sha256(_canonical_data_bytes(identity))[:24]


def _legacy_source_campaign_id(session_id: str) -> str:
    """Reproduce the frozen capture's trailing-LF source campaign identity."""

    identity = {
        "preregistration_sha256": PROTOCOL_V1_PREREGISTRATION_SHA256,
        "session_id": session_id,
    }
    return "inventory-positive-v3-campaign-" + _sha256(_canonical_bytes(identity))[:24]


def _content_bound_dataset_id(manifest: Mapping[str, object]) -> str:
    identity = copy.deepcopy(dict(manifest))
    identity.pop("dataset_id", None)
    return "inventory-positive-v3-independent-" + _sha256(_canonical_data_bytes(identity))[:24]


def _require_actor(value: Mapping[str, object], key: str) -> str:
    actor = _require_text(value, key)
    if actor != actor.strip() or len(actor) > 256:
        raise InventoryV3ProtocolV2Error(f"{key} is not a canonical actor identity")
    return actor


def _source_allowlist() -> dict[str, str]:
    roles = {
        _CAPTURE_PROGRESS_NAME: "source-progress-metadata",
        _SESSION_REPORT_NAME: "source-session-metadata",
        f"{_SESSION_REPORT_NAME}.sha256": "source-session-sidecar",
        _COMPLETION_SEAL_NAME: "source-completion-metadata",
        f"{_COMPLETION_SEAL_NAME}.sha256": "source-completion-sidecar",
        _PRODUCER_ATTESTATION_NAME: "producer-attestation-metadata",
        f"{_PRODUCER_ATTESTATION_NAME}.sha256": "producer-attestation-sidecar",
    }
    for index, stage in enumerate(REQUIRED_STAGES, start=1):
        prefix = f"captures/{index:03d}-{stage}"
        roles[f"{prefix}/full-frame.bgra"] = "private-full-frame-bgra"
        roles[f"{prefix}/inventory-region.bgra"] = "private-inventory-region-bgra"
        roles[f"{prefix}/owned-frame.json"] = "owned-frame-metadata"
        roles[f"{prefix}/owned-frame.json.sha256"] = "owned-frame-sidecar"
        roles[f"{prefix}/source-capture-report.json"] = "source-capture-metadata"
        roles[f"{prefix}/source-capture-report.json.sha256"] = "source-capture-sidecar"
    return roles


def _acquisition_roles() -> dict[str, str]:
    roles = _source_allowlist()
    roles.update(
        {
            _CAMPAIGN_MANIFEST_NAME: "campaign-manifest",
            f"{_CAMPAIGN_MANIFEST_NAME}.sha256": "campaign-manifest-sidecar",
            _EVALUATOR_SESSION_NAME: "frozen-v1-evaluator-source-session",
            f"{_EVALUATOR_SESSION_NAME}.sha256": ("frozen-v1-evaluator-source-session-sidecar"),
            _EVALUATOR_SEAL_NAME: "frozen-v1-evaluator-completion-seal",
            f"{_EVALUATOR_SEAL_NAME}.sha256": ("frozen-v1-evaluator-completion-seal-sidecar"),
            "protocol-v2-acquisition.json": "protocol-v2-acquisition-record",
            "protocol-v2-acquisition.json.sha256": "protocol-v2-acquisition-sidecar",
        }
    )
    return roles


def _review_intake_roles() -> dict[str, str]:
    roles = {
        _REVIEW_TEMPLATE_NAME: "independent-review-template",
        f"{_REVIEW_TEMPLATE_NAME}.sha256": "review-template-sidecar",
    }
    for index in range(1, len(REQUIRED_STAGES) + 1):
        roles[f"cases/{index:03d}/full-frame.bgra"] = "private-review-full-frame-bgra"
        roles[f"cases/{index:03d}/inventory-region.bgra"] = "private-review-inventory-region-bgra"
    return roles


def _review_submission_roles() -> dict[str, str]:
    return {
        _REVIEW_SUBMISSION_NAME: "independent-reviewer-submission",
        f"{_REVIEW_SUBMISSION_NAME}.sha256": "reviewer-submission-sidecar",
    }


def _review_root_allowlist(*, submission_complete: bool) -> dict[str, str]:
    roles = {f"package/{path}": role for path, role in _review_intake_roles().items()}
    roles[f"package/{_PACKAGE_TREE_NAME}"] = "reserved-package-tree"
    roles[f"package/{_PACKAGE_TREE_NAME}.sha256"] = "reserved-package-tree-sidecar"
    if submission_complete:
        roles.update(
            {f"submission/{path}": role for path, role in _review_submission_roles().items()}
        )
        roles[f"submission/{_PACKAGE_TREE_NAME}"] = "reserved-package-tree"
        roles[f"submission/{_PACKAGE_TREE_NAME}.sha256"] = "reserved-package-tree-sidecar"
    return roles


def _reviewed_package_roles() -> dict[str, str]:
    roles = _acquisition_roles()
    roles.update(
        {
            _REVIEWER_TRUTH_NAME: "independent-reviewer-truth",
            f"{_REVIEWER_TRUTH_NAME}.sha256": "reviewer-truth-sidecar",
            _VALIDATION_PACKAGE_NAME: "frozen-evaluator-input",
            f"{_VALIDATION_PACKAGE_NAME}.sha256": "validation-package-sidecar",
            "protocol-v2-reviewed-package.json": "protocol-v2-reviewed-record",
            "protocol-v2-reviewed-package.json.sha256": "protocol-v2-reviewed-sidecar",
        }
    )
    return roles


def _result_roles(*, conformance_passed: bool) -> dict[str, str]:
    roles = {
        "frozen-evaluator-private-report.json": "private-frozen-evaluator-report",
        "frozen-evaluator-private-report.json.sha256": "private-report-sidecar",
        "protocol-v2-terminal-result.json": "private-terminal-result",
        "protocol-v2-terminal-result.json.sha256": "terminal-result-sidecar",
    }
    if not conformance_passed:
        roles["public-failure-receipt.json"] = "public-failure-receipt"
        roles["public-failure-receipt.json.sha256"] = "public-failure-receipt-sidecar"
    return roles


def _approval_request_roles() -> dict[str, str]:
    return {
        "approval-request.json": "source-approval-request-only",
        "approval-request.json.sha256": "approval-request-sidecar",
    }


def _operation_failure_roles() -> dict[str, str]:
    return {
        "private-failure.json": "private-operation-failure-binding",
        "private-failure.json.sha256": "private-operation-failure-sidecar",
        "public-failure-receipt.json": "public-failure-receipt",
        "public-failure-receipt.json.sha256": "public-failure-receipt-sidecar",
    }


def _scan_metadata_only_closed_tree(
    root: Path,
    allowlist: Mapping[str, str],
    *,
    require_source_layout: bool = False,
    expected_empty_directories: tuple[str, ...] = (),
) -> tuple[tuple[str, tuple[int, ...]], ...]:
    """Check an exact physical tree without opening any file payload."""

    absolute_root = root.absolute()
    try:
        root_info = root.lstat()
    except OSError as exc:
        raise InventoryV3ProtocolV2Error("source campaign root is unavailable") from exc
    if (
        root.is_symlink()
        or not stat.S_ISDIR(root_info.st_mode)
        or int(getattr(root_info, "st_file_attributes", 0)) & 0x400
    ):
        raise InventoryV3ProtocolV2Error("source campaign root is redirected")
    actual_files: set[str] = set()
    actual_directories: set[str] = {""}
    physical: dict[tuple[int, int], str] = {}
    fingerprints: dict[str, tuple[int, ...]] = {"D:.": _stable_stat_fingerprint(root_info)}

    def visit(directory: Path, relative: str) -> None:
        try:
            directory_before = directory.lstat()
        except OSError as exc:
            raise InventoryV3ProtocolV2Error("source campaign directory is unavailable") from exc
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name.encode("utf-8"))
        except OSError as exc:
            raise InventoryV3ProtocolV2Error("source campaign tree is unreadable") from exc
        for item in entries:
            child_relative = item.name if not relative else f"{relative}/{item.name}"
            _owned_path(absolute_root, child_relative, "source campaign")
            try:
                info = Path(item.path).lstat()
            except OSError as exc:
                raise InventoryV3ProtocolV2Error(
                    f"source campaign entry is unavailable: {child_relative}"
                ) from exc
            attributes = int(getattr(info, "st_file_attributes", 0))
            if item.is_symlink() or attributes & 0x400:
                raise InventoryV3ProtocolV2Error(
                    f"source campaign entry is redirected: {child_relative}"
                )
            if stat.S_ISDIR(info.st_mode):
                actual_directories.add(child_relative)
                fingerprints[f"D:{child_relative}"] = _stable_stat_fingerprint(info)
                visit(Path(item.path), child_relative)
            elif stat.S_ISREG(info.st_mode):
                if info.st_nlink != 1:
                    raise InventoryV3ProtocolV2Error(
                        f"source campaign file is hardlink-aliased: {child_relative}"
                    )
                identity = (int(info.st_dev), int(info.st_ino))
                previous = physical.get(identity)
                if previous is not None:
                    raise InventoryV3ProtocolV2Error(
                        "source campaign paths share physical identity: "
                        f"{previous}, {child_relative}"
                    )
                physical[identity] = child_relative
                actual_files.add(child_relative)
                fingerprints[f"F:{child_relative}"] = _stable_stat_fingerprint(info)
            else:
                raise InventoryV3ProtocolV2Error(
                    f"source campaign entry is not regular: {child_relative}"
                )
        try:
            directory_after = directory.lstat()
        except OSError as exc:
            raise InventoryV3ProtocolV2Error(
                "source campaign directory changed during enumeration"
            ) from exc
        if _stable_stat_fingerprint(directory_before) != _stable_stat_fingerprint(directory_after):
            raise InventoryV3ProtocolV2Error("source campaign directory changed during enumeration")

    visit(absolute_root, "")
    expected_files = set(allowlist)
    expected_directories = {""}
    for relative in expected_files:
        parts = relative.split("/")
        for count in range(1, len(parts)):
            expected_directories.add("/".join(parts[:count]))
    expected_directories.update(expected_empty_directories)
    if actual_files != expected_files or actual_directories != expected_directories:
        raise InventoryV3ProtocolV2Error("source campaign tree differs from the fixed allowlist")
    if require_source_layout:
        for index, stage in enumerate(REQUIRED_STAGES, start=1):
            prefix = root / "captures" / f"{index:03d}-{stage}"
            if (prefix / "full-frame.bgra").lstat().st_size != FULL_FRAME_SIZE:
                raise InventoryV3ProtocolV2Error("source full-frame size differs")
            if (prefix / "inventory-region.bgra").lstat().st_size != REGION_SIZE:
                raise InventoryV3ProtocolV2Error("source inventory-region size differs")
    return tuple(sorted(fingerprints.items()))


def _validate_capture_environment(
    environment: Mapping[str, object],
    protocol: ProtocolV2LockBinding,
    authorization: LiveAuthorizationBinding,
) -> None:
    expected_keys = {
        "capture_build_sha",
        "capture_configuration_id",
        "capture_execution_head_sha",
        "client_mode",
        "frame",
        "host_reservation_sha256",
        "live_authorization_git_blob",
        "live_authorization_git_commit_sha",
        "live_authorization_id",
        "protocol_lock_git_commit_sha",
        "python_isolated_mode",
        "python_isolated_source_cache",
        "python_no_site_mode",
        "renderer",
        "runelite_build",
        "theme",
        "window_class",
        "window_handle",
        "windows_dpi",
        "windows_scaling_percent",
        "windows_version",
    }
    _require_exact_keys(environment, expected_keys, "capture environment")
    frame = _require_mapping(environment.get("frame"), "capture frame")
    _require_exact_keys(frame, {"height", "pixel_format", "profile_id", "width"}, "capture frame")
    if frame != {
        "height": SUPPORTED_FRAME_HEIGHT,
        "pixel_format": SUPPORTED_PIXEL_FORMAT,
        "profile_id": SUPPORTED_PROFILE_ID,
        "width": SUPPORTED_FRAME_WIDTH,
    }:
        raise InventoryV3ProtocolV2Error("capture frame differs from supported BGRA envelope")
    execution_head = _require_lower_hex(
        environment.get("capture_execution_head_sha"), 40, "capture execution HEAD"
    )
    if (
        subprocess.run(
            (
                "git",
                "-C",
                str(protocol.repository_root),
                "merge-base",
                "--is-ancestor",
                authorization.git_commit_sha,
                execution_head,
            ),
            check=False,
            capture_output=True,
        ).returncode
        != 0
    ):
        raise InventoryV3ProtocolV2Error("capture execution HEAD predates authorization")
    if (
        subprocess.run(
            (
                "git",
                "-C",
                str(protocol.repository_root),
                "merge-base",
                "--is-ancestor",
                execution_head,
                protocol.evaluator_head_sha,
            ),
            check=False,
            capture_output=True,
        ).returncode
        != 0
    ):
        raise InventoryV3ProtocolV2Error("capture execution HEAD is foreign to evaluator HEAD")
    if (
        environment.get("capture_build_sha") != PROTOCOL_V1_SOURCE_HEAD
        or environment.get("capture_configuration_id") != CAPTURE_CONFIGURATION_ID
        or environment.get("protocol_lock_git_commit_sha") != PROTOCOL_V1_LOCK_HEAD
        or environment.get("live_authorization_id") != authorization.authorization_id
        or environment.get("live_authorization_git_commit_sha") != authorization.git_commit_sha
        or environment.get("live_authorization_git_blob") != authorization.legacy_registry_git_blob
        or environment.get("python_isolated_mode") is not True
        or environment.get("python_isolated_source_cache") is not True
        or environment.get("python_no_site_mode") is not True
    ):
        raise InventoryV3ProtocolV2Error("capture environment provenance differs")
    for key in (
        "client_mode",
        "renderer",
        "runelite_build",
        "theme",
        "window_class",
        "windows_version",
    ):
        _require_text(environment, key)
    for key in ("window_handle", "windows_dpi", "windows_scaling_percent"):
        value = environment.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise InventoryV3ProtocolV2Error(f"capture environment {key} is invalid")


def _verify_legacy_user_reservation(
    binding: SourceMetadataBinding,
    *,
    attempt_base: Path | None,
) -> None:
    environment = _require_mapping(
        binding.session.get("capture_environment"), "capture environment"
    )
    if attempt_base is None:
        attempt_base = _producer_user_local_app_data()
    reservation_path = (
        attempt_base.resolve(strict=False)
        / "Mining-Automation"
        / "inventory-positive-v3-independent-reservations"
        / f"{PROTOCOL_V1_LOCK_SHA256}.json"
    )
    _assert_plain_file(reservation_path, "legacy Windows-user reservation")
    expected = {
        "authorization_id": binding.authorization.authorization_id,
        "capture_build_sha": PROTOCOL_V1_SOURCE_HEAD,
        "capture_configuration_id": CAPTURE_CONFIGURATION_ID,
        "live_authorization_git_commit_sha": binding.authorization.git_commit_sha,
        "protocol_lock_git_commit_sha": PROTOCOL_V1_LOCK_HEAD,
        "protocol_lock_sha256": PROTOCOL_V1_LOCK_SHA256,
        "repository": REPOSITORY_ID,
        "schema": "inventory-positive-v3-independent-host-reservation-v1",
        "status": "reserved-and-irrevocably-consumed",
    }
    payload = reservation_path.read_bytes()
    if payload != _canonical_bytes(expected):
        raise InventoryV3ProtocolV2Error("legacy Windows-user reservation differs")
    if environment.get("host_reservation_sha256") != _sha256(payload):
        raise InventoryV3ProtocolV2Error("source session reservation hash differs")


def preflight_source_metadata(
    protocol: ProtocolV2LockBinding,
    authorization: LiveAuthorizationBinding,
    *,
    attempt_base: Path | None = None,
    access_hook: AccessHook | None = None,
) -> SourceMetadataBinding:
    """Verify source metadata and provenance before any validation pixel read."""

    _verify_approval_registry_absent(protocol, access_hook=access_hook)
    _emit(access_hook, "preflight", "source_tree_metadata", None)
    paths = ProtocolV2Paths.for_authorization(
        protocol.repository_root,
        authorization.authorization_id,
        protocol.lock_sha256,
        attempt_base=attempt_base,
    )
    _assert_disjoint_paths(paths)
    source_root = paths.source_campaign_root
    source_metadata_snapshot = _scan_metadata_only_closed_tree(
        source_root, _source_allowlist(), require_source_layout=True
    )
    _emit(access_hook, "preflight", "source_session_metadata", source_root / _SESSION_REPORT_NAME)
    session, session_payload = _read_canonical_json(
        source_root / _SESSION_REPORT_NAME,
        schema=_SOURCE_SESSION_SCHEMA,
        label="source session report",
    )
    _require_exact_keys(
        session,
        {
            "activation_allowed",
            "all_owned_captures_included",
            "campaign_id",
            "capture_environment",
            "captures",
            "completed_at_utc",
            "operator",
            "owned_attempts",
            "schema",
            "session_id",
            "started_at_utc",
        },
        "source session report",
    )
    session_id = f"inventory-v3-independent-{authorization.authorization_id}"
    if (
        session.get("activation_allowed") is not False
        or session.get("all_owned_captures_included") is not True
        or session.get("session_id") != session_id
        or session.get("campaign_id") != _legacy_source_campaign_id(session_id)
    ):
        raise InventoryV3ProtocolV2Error("source session identity or authority differs")
    _require_actor(session, "operator")
    started = _parse_utc(session.get("started_at_utc"), "source start")
    completed = _parse_utc(session.get("completed_at_utc"), "source completion")
    if completed <= started:
        raise InventoryV3ProtocolV2Error("source session chronology differs")
    environment = _require_mapping(session.get("capture_environment"), "capture environment")
    _validate_capture_environment(environment, protocol, authorization)
    execution_head = _require_lower_hex(
        environment.get("capture_execution_head_sha"), 40, "capture execution HEAD"
    )
    chronology_floor = max(
        _parse_utc(authorization.committed_at_utc, "authorization commit time"),
        _parse_git_time(
            _git(
                protocol.repository_root,
                "show",
                "-s",
                "--format=%cI",
                protocol.lock_commit_sha,
            ),
            "L2 Git time",
        ),
        _parse_git_time(
            _git(
                protocol.repository_root,
                "show",
                "-s",
                "--format=%cI",
                execution_head,
            ),
            "capture execution HEAD Git time",
        ),
    )
    if started <= chronology_floor:
        raise InventoryV3ProtocolV2Error(
            "source session predates authorization, L2, or capture execution HEAD"
        )
    raw_captures = _require_list(session, "captures")
    raw_attempts = _require_list(session, "owned_attempts")
    if len(raw_captures) != len(REQUIRED_STAGES) or len(raw_attempts) != len(raw_captures):
        raise InventoryV3ProtocolV2Error(
            "source session does not contain exactly seven owned captures"
        )
    reports: list[Mapping[str, object]] = []
    owned_reports: list[Mapping[str, object]] = []
    capture_ids: set[str] = set()
    full_hashes: set[str] = set()
    region_hashes: set[str] = set()
    previous_time = started
    for index, (stage, raw_capture, raw_attempt) in enumerate(
        zip(REQUIRED_STAGES, raw_captures, raw_attempts, strict=True), start=1
    ):
        capture = _require_mapping(raw_capture, f"source capture {index}")
        attempt = _require_mapping(raw_attempt, f"owned attempt {index}")
        _require_exact_keys(
            capture,
            {
                "capture_id",
                "captured_at_utc",
                "capture_report",
                "planned_stage_id",
                "sequence_index",
            },
            f"source capture {index}",
        )
        _require_exact_keys(
            attempt,
            {
                "capture_id",
                "full_frame_attempt",
                "owned_frame_report",
                "planned_stage_id",
                "sequence_index",
                "status",
            },
            f"owned attempt {index}",
        )
        capture_id = _require_text(capture, "capture_id")
        captured_at_text = _require_text(capture, "captured_at_utc")
        captured_at = _require_frozen_capture_identity(
            capture_id,
            captured_at_text,
            sequence_index=index,
            stage=stage,
        )
        if captured_at <= previous_time or captured_at >= completed:
            raise InventoryV3ProtocolV2Error("source capture chronology differs")
        previous_time = captured_at
        if (
            capture.get("planned_stage_id") != stage
            or capture.get("sequence_index") != index
            or attempt.get("capture_id") != capture_id
            or attempt.get("planned_stage_id") != stage
            or attempt.get("sequence_index") != index
            or attempt.get("status") != "owned-frame-finalized"
            or capture_id in capture_ids
        ):
            raise InventoryV3ProtocolV2Error("source case order, identity, or ownership differs")
        capture_ids.add(capture_id)
        prefix = f"captures/{index:03d}-{stage}"
        capture_ref = _require_mapping(capture.get("capture_report"), "capture report ref")
        owned_ref = _require_mapping(attempt.get("owned_frame_report"), "owned frame ref")
        full_attempt = _require_mapping(attempt.get("full_frame_attempt"), "full frame attempt")
        _require_exact_keys(capture_ref, {"path", "sha256"}, "capture report ref")
        _require_exact_keys(owned_ref, {"path", "sha256"}, "owned frame ref")
        _require_exact_keys(full_attempt, {"path", "sha256", "size_bytes"}, "full frame attempt")
        expected_capture_report = f"{prefix}/source-capture-report.json"
        expected_owned_report = f"{prefix}/owned-frame.json"
        expected_full = f"{prefix}/full-frame.bgra"
        if (
            capture_ref.get("path") != expected_capture_report
            or owned_ref.get("path") != expected_owned_report
            or full_attempt.get("path") != expected_full
            or full_attempt.get("size_bytes") != FULL_FRAME_SIZE
        ):
            raise InventoryV3ProtocolV2Error("source-owned path binding differs")
        report, report_payload = _read_canonical_json(
            _owned_path(source_root, expected_capture_report, "capture report"),
            schema=_SOURCE_CAPTURE_SCHEMA,
            label=f"source capture report {index}",
        )
        owned, owned_payload = _read_canonical_json(
            _owned_path(source_root, expected_owned_report, "owned frame report"),
            schema=_OWNED_FRAME_SCHEMA,
            label=f"owned frame report {index}",
        )
        if capture_ref.get("sha256") != _sha256(report_payload) or owned_ref.get(
            "sha256"
        ) != _sha256(owned_payload):
            raise InventoryV3ProtocolV2Error("source metadata reference hash differs")
        _require_exact_keys(
            report,
            {
                "activation_allowed",
                "capture_environment",
                "capture_id",
                "capture_policy",
                "captured_at_utc",
                "full_frame",
                "inventory_region",
                "schema",
                "session_id",
            },
            f"capture report {index}",
        )
        if (
            report.get("activation_allowed") is not False
            or report.get("capture_environment") != environment
            or report.get("capture_id") != capture_id
            or report.get("captured_at_utc") != capture.get("captured_at_utc")
            or report.get("session_id") != session_id
        ):
            raise InventoryV3ProtocolV2Error("source capture report is rebound")
        policy = _require_mapping(report.get("capture_policy"), "capture policy")
        if policy != {
            "backend_attempts": 1,
            "detector_executed": False,
            "input_automation_allowed": False,
            "pixel_materialization": "fixed-bgra-row-slice-only",
        }:
            raise InventoryV3ProtocolV2Error("passive capture policy differs")
        full = _require_mapping(report.get("full_frame"), "full frame metadata")
        region = _require_mapping(report.get("inventory_region"), "region metadata")
        expected_region = f"{prefix}/inventory-region.bgra"
        if (
            full.get("path") != expected_full
            or full.get("size_bytes") != FULL_FRAME_SIZE
            or full.get("width") != SUPPORTED_FRAME_WIDTH
            or full.get("height") != SUPPORTED_FRAME_HEIGHT
            or full.get("pixel_format") != SUPPORTED_PIXEL_FORMAT
            or region.get("path") != expected_region
            or region.get("size_bytes") != REGION_SIZE
            or region.get("region") != list(SUPPORTED_REGION)
        ):
            raise InventoryV3ProtocolV2Error("capture geometry or fixed paths differ")
        full_sha = _require_lower_hex(full.get("sha256"), 64, "full frame SHA-256")
        region_sha = _require_lower_hex(region.get("sha256"), 64, "region SHA-256")
        if (
            full_attempt.get("sha256") != full_sha
            or full_sha in full_hashes
            or region_sha in region_hashes
        ):
            raise InventoryV3ProtocolV2Error("source frame hashes are duplicate or rebound")
        full_hashes.add(full_sha)
        region_hashes.add(region_sha)
        _require_exact_keys(
            owned,
            {
                "capture_id",
                "captured_at_utc",
                "frame",
                "planned_stage_id",
                "schema",
                "sequence_index",
                "session_id",
                "status",
                "window",
            },
            f"owned frame {index}",
        )
        owned_frame = _require_mapping(owned.get("frame"), "owned frame payload")
        owned_window = _require_mapping(owned.get("window"), "owned frame window")
        _require_source_capture_nested_shapes(
            full,
            region,
            owned_frame,
            owned_window,
            index=index,
        )
        if (
            owned.get("capture_id") != capture_id
            or owned.get("captured_at_utc") != capture.get("captured_at_utc")
            or owned.get("planned_stage_id") != stage
            or owned.get("sequence_index") != index
            or owned.get("session_id") != session_id
            or owned.get("status") != "captured-unreviewed"
            or owned_frame.get("path") != expected_full
            or owned_frame.get("sha256") != full_sha
            or owned_frame.get("size_bytes") != FULL_FRAME_SIZE
            or owned_frame.get("width") != SUPPORTED_FRAME_WIDTH
            or owned_frame.get("height") != SUPPORTED_FRAME_HEIGHT
            or owned_frame.get("pixel_format") != SUPPORTED_PIXEL_FORMAT
            or owned_window.get("class") != environment.get("window_class")
            or owned_window.get("handle") != environment.get("window_handle")
            or owned_window.get("windows_dpi") != environment.get("windows_dpi")
        ):
            raise InventoryV3ProtocolV2Error("owned-frame provenance differs")
        reports.append(report)
        owned_reports.append(owned)

    _require_source_development_identity_disjoint(
        protocol,
        session_id=session_id,
        capture_ids=capture_ids,
    )
    _emit(access_hook, "preflight", "development_identity_disjointness_metadata", None)
    _emit(
        access_hook, "preflight", "source_completion_metadata", source_root / _COMPLETION_SEAL_NAME
    )
    seal, seal_payload = _read_canonical_json(
        source_root / _COMPLETION_SEAL_NAME,
        schema=_SOURCE_COMPLETION_SCHEMA,
        label="source completion seal",
    )
    expected_seal = {
        "activation_allowed": False,
        "authorization_id": authorization.authorization_id,
        "campaign_id": session.get("campaign_id"),
        "capture_count": len(REQUIRED_STAGES),
        "capture_execution_head_sha": environment.get("capture_execution_head_sha"),
        "completed_at_utc": session.get("completed_at_utc"),
        "host_reservation_sha256": environment.get("host_reservation_sha256"),
        "live_authorization_git_commit_sha": authorization.git_commit_sha,
        "protocol_lock_git_commit_sha": PROTOCOL_V1_LOCK_HEAD,
        "schema": _SOURCE_COMPLETION_SCHEMA,
        "session_id": session_id,
        "source_session_report_sha256": _sha256(session_payload),
        "status": "complete-not-reviewed",
    }
    if seal != expected_seal:
        raise InventoryV3ProtocolV2Error("source completion seal differs")
    progress, _ = _read_canonical_json(
        source_root / _CAPTURE_PROGRESS_NAME,
        schema=_CAPTURE_PROGRESS_SCHEMA,
        label="source capture progress",
        require_sidecar=False,
    )
    expected_progress = {
        "activation_allowed": False,
        "campaign_id": session.get("campaign_id"),
        "capture_build_sha": environment.get("capture_build_sha"),
        "capture_configuration_id": environment.get("capture_configuration_id"),
        "capture_execution_head_sha": environment.get("capture_execution_head_sha"),
        "captures": raw_captures,
        "detector_executed": False,
        "failure": None,
        "host_reservation_sha256": environment.get("host_reservation_sha256"),
        "live_authorization_id": authorization.authorization_id,
        "live_authorization_git_blob": environment.get("live_authorization_git_blob"),
        "live_authorization_git_commit_sha": authorization.git_commit_sha,
        "owned_attempts": raw_attempts,
        "operator": session.get("operator"),
        "planned_stages": list(REQUIRED_STAGES),
        "preregistration_sha256": PROTOCOL_V1_PREREGISTRATION_SHA256,
        "protocol_lock_git_commit_sha": PROTOCOL_V1_LOCK_HEAD,
        "schema": _CAPTURE_PROGRESS_SCHEMA,
        "session_id": session_id,
        "source_completion_seal_sha256": _sha256(seal_payload),
        "source_session_report_sha256": _sha256(session_payload),
        "started_at_utc": session.get("started_at_utc"),
        "status": "ready-to-seal",
    }
    if progress != expected_progress:
        raise InventoryV3ProtocolV2Error("source progress does not bind completion")
    _emit(
        access_hook,
        "preflight",
        "producer_attestation_metadata",
        source_root / _PRODUCER_ATTESTATION_NAME,
    )
    producer, _ = _read_canonical_json(
        source_root / _PRODUCER_ATTESTATION_NAME,
        schema=_PRODUCER_ATTESTATION_SCHEMA,
        label="producer attestation",
    )
    _require_exact_keys(
        producer,
        {
            "activation_allowed",
            "authorization_binding",
            "capture_execution_head_sha",
            "collected_at_utc",
            "environment",
            "legacy_user_reservation_sha256",
            "producer_identity",
            "promotion_allowed",
            "protocol_lock_sha256",
            "reservation",
            "schema",
            "session_id",
            "source_completion_seal_sha256",
            "source_session_report_sha256",
            "support_authority_granted",
        },
        "producer attestation",
    )
    if (
        producer.get("activation_allowed") is not False
        or producer.get("promotion_allowed") is not False
        or producer.get("support_authority_granted") is not False
        or producer.get("protocol_lock_sha256") != protocol.lock_sha256
        or producer.get("capture_execution_head_sha")
        != environment.get("capture_execution_head_sha")
        or producer.get("session_id") != session_id
        or producer.get("source_session_report_sha256") != _sha256(session_payload)
        or producer.get("source_completion_seal_sha256") != _sha256(seal_payload)
        or producer.get("legacy_user_reservation_sha256")
        != environment.get("host_reservation_sha256")
    ):
        raise InventoryV3ProtocolV2Error("producer attestation binding differs")
    auth_binding = _require_mapping(
        producer.get("authorization_binding"), "producer authorization binding"
    )
    if auth_binding != {
        "live_authorization_id": authorization.authorization_id,
        "opaque_receipt_id": authorization.opaque_receipt_id,
        "producer_grants_authorization": False,
    }:
        raise InventoryV3ProtocolV2Error("producer authorization binding differs")
    reservation = _require_mapping(producer.get("reservation"), "producer reservation")
    if reservation.get("scope") != "windows-user-local-not-host-global":
        raise InventoryV3ProtocolV2Error("producer reservation overclaims its scope")
    producer_environment = _require_mapping(producer.get("environment"), "producer environment")
    _require_exact_keys(
        producer_environment,
        {
            "assertions_grant_support_authority",
            "observed",
            "operator_asserted",
            "required_observed_fields",
            "required_operator_asserted_fields",
        },
        "producer environment",
    )
    if producer_environment.get("assertions_grant_support_authority") is not False:
        raise InventoryV3ProtocolV2Error("producer assertions grant support authority")
    observed = _require_list(producer_environment, "observed")
    asserted = _require_list(producer_environment, "operator_asserted")
    expected_observed = {
        "frame.height": SUPPORTED_FRAME_HEIGHT,
        "frame.pixel_format": SUPPORTED_PIXEL_FORMAT,
        "frame.profile_id": SUPPORTED_PROFILE_ID,
        "frame.width": SUPPORTED_FRAME_WIDTH,
        "window_class": environment.get("window_class"),
        "window_handle": environment.get("window_handle"),
        "windows_dpi": environment.get("windows_dpi"),
        "windows_scaling_percent": environment.get("windows_scaling_percent"),
        "windows_version": environment.get("windows_version"),
    }
    expected_asserted = {
        "client_mode": environment.get("client_mode"),
        "renderer": environment.get("renderer"),
        "runelite_build": environment.get("runelite_build"),
        "theme": environment.get("theme"),
    }
    if producer_environment.get("required_observed_fields") != [
        "frame.height",
        "frame.pixel_format",
        "frame.profile_id",
        "frame.width",
        "window_class",
        "window_handle",
        "windows_dpi",
    ]:
        raise InventoryV3ProtocolV2Error("required observed provenance fields differ")
    if producer_environment.get("required_operator_asserted_fields") != sorted(expected_asserted):
        raise InventoryV3ProtocolV2Error("required asserted provenance fields differ")
    parsed_observed: dict[str, object] = {}
    for raw in observed:
        item = _require_mapping(raw, "observed producer field")
        if set(item) != {"name", "provenance", "value"} or item.get("provenance") != "observed":
            raise InventoryV3ProtocolV2Error("observed producer field differs")
        parsed_observed[_require_text(item, "name")] = item.get("value")
    parsed_asserted: dict[str, object] = {}
    for raw in asserted:
        item = _require_mapping(raw, "asserted producer field")
        if (
            set(item) != {"grants_support_authority", "name", "provenance", "value"}
            or item.get("provenance") != "operator-asserted"
            or item.get("grants_support_authority") is not False
        ):
            raise InventoryV3ProtocolV2Error("asserted producer field grants authority")
        parsed_asserted[_require_text(item, "name")] = item.get("value")
    if parsed_observed != expected_observed or parsed_asserted != expected_asserted:
        raise InventoryV3ProtocolV2Error("producer environment provenance differs")
    identity = _require_mapping(producer.get("producer_identity"), "producer identity")
    _require_exact_keys(
        identity,
        {"computer_name", "observation_source", "schema", "session_id", "user_name"},
        "producer identity",
    )
    if identity.get("observation_source") != "windows-api":
        raise InventoryV3ProtocolV2Error("producer identity was not OS-observed")
    computer_name = _require_text(identity, "computer_name")
    user_name = _require_text(identity, "user_name")
    producer_session_id = identity.get("session_id")
    if (
        not isinstance(producer_session_id, int)
        or isinstance(producer_session_id, bool)
        or producer_session_id < 0
    ):
        raise InventoryV3ProtocolV2Error("producer Windows session identity differs")
    from .producer import WindowsProducerIdentity, windows_user_reservation_name

    producer_identity = WindowsProducerIdentity(
        computer_name=computer_name,
        user_name=user_name,
        session_id=producer_session_id,
    )
    if reservation != {
        "name": windows_user_reservation_name(producer_identity, protocol.lock_sha256),
        "scope": "windows-user-local-not-host-global",
    }:
        raise InventoryV3ProtocolV2Error("producer reservation identity differs")
    if _parse_utc(producer.get("collected_at_utc"), "producer collection") <= completed:
        raise InventoryV3ProtocolV2Error("producer attestation predates source completion")
    binding = SourceMetadataBinding(
        paths=paths,
        protocol=protocol,
        authorization=authorization,
        session=session,
        session_payload=session_payload,
        completion_seal=seal,
        completion_payload=seal_payload,
        producer_attestation=producer,
        capture_reports=tuple(reports),
        owned_frame_reports=tuple(owned_reports),
        source_files=tuple(sorted(_source_allowlist())),
        source_metadata_snapshot=source_metadata_snapshot,
    )
    _verify_legacy_user_reservation(binding, attempt_base=attempt_base)
    _emit(access_hook, "preflight", "source_preflight_complete", None)
    return binding


def _recheck_source_metadata(binding: SourceMetadataBinding) -> None:
    current = _scan_metadata_only_closed_tree(
        binding.paths.source_campaign_root,
        _source_allowlist(),
        require_source_layout=True,
    )
    if current != binding.source_metadata_snapshot:
        raise InventoryV3ProtocolV2Error("source metadata changed after preflight")


def _assert_disjoint_paths(paths: ProtocolV2Paths) -> None:
    _assert_windows_legacy_path_budget(paths)
    repository_descendants = (
        paths.source_campaign_root,
        paths.workspace_root,
        paths.acquisition_root,
        paths.review_intake_root,
        paths.reviewed_package_root,
        paths.approval_request_root,
        paths.result_root,
    )
    for candidate in repository_descendants:
        _assert_plain_descendant_path(paths.repository_root, candidate, "repository output root")
    attempt_anchor = paths.attempt_base_root or paths.attempt_root.parent
    _assert_plain_descendant_path(attempt_anchor, paths.attempt_root, "attempt output root")
    roots = (
        paths.source_campaign_root,
        paths.acquisition_root,
        paths.review_intake_root,
        paths.reviewed_package_root,
        paths.approval_request_root,
        paths.result_root,
        paths.attempt_root,
    )
    normalized = [Path(os.path.abspath(item)).resolve(strict=False) for item in roots]
    for index, first in enumerate(normalized):
        for second in normalized[index + 1 :]:
            if os.name == "nt" and str(first).casefold() == str(second).casefold():
                raise InventoryV3ProtocolV2Error(
                    f"release-critical roots alias by Windows spelling: {first}, {second}"
                )
            try:
                common = Path(os.path.commonpath((str(first), str(second))))
            except ValueError as exc:
                raise InventoryV3ProtocolV2Error("package roots are incomparable") from exc
            if common in (first, second):
                raise InventoryV3ProtocolV2Error(
                    f"release-critical roots overlap: {first}, {second}"
                )


def _assert_windows_legacy_path_budget(paths: ProtocolV2Paths) -> None:
    """Require every fixed live path to work with long-path support disabled."""

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

    def add_tree(root: Path, roles: Mapping[str, str], *, packaged: bool) -> None:
        directories.add(root)
        for relative in roles:
            add_file(root.joinpath(*PurePosixPath(relative).parts), boundary=root)
        if packaged:
            add_file(root / _PACKAGE_TREE_NAME, boundary=root)
            add_file(root / f"{_PACKAGE_TREE_NAME}.sha256", boundary=root)

    add_tree(paths.source_campaign_root, _source_allowlist(), packaged=False)
    add_tree(paths.acquisition_root, _acquisition_roles(), packaged=True)
    add_tree(
        paths.review_intake_root / "package",
        _review_intake_roles(),
        packaged=True,
    )
    add_tree(
        paths.review_intake_root / "submission",
        _review_submission_roles(),
        packaged=True,
    )
    add_tree(paths.reviewed_package_root, _reviewed_package_roles(), packaged=True)
    result_roles = _result_roles(conformance_passed=True)
    result_roles.update(_result_roles(conformance_passed=False))
    add_tree(paths.result_root, result_roles, packaged=True)
    add_tree(paths.approval_request_root, _approval_request_roles(), packaged=True)
    for operation in _ATTEMPT_OPERATIONS:
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
            _operation_failure_roles(),
            packaged=True,
        )
    attempt_anchor = paths.attempt_base_root or paths.attempt_root.parent
    legacy_reservation_root = (
        attempt_anchor / "Mining-Automation" / "inventory-positive-v3-independent-reservations"
    )
    add_file(
        legacy_reservation_root / f"{PROTOCOL_V1_LOCK_SHA256}.json",
        boundary=legacy_reservation_root,
    )

    for directory in directories:
        absolute = str(Path(os.path.abspath(directory)))
        units = len(absolute.encode("utf-16-le")) // 2
        if units > _WINDOWS_LEGACY_MAX_DIRECTORY_CHARS:
            raise InventoryV3ProtocolV2Error(
                "fixed protocol directory exceeds the deterministic Windows "
                f"legacy path budget: {directory}"
            )
    for file_path in files:
        absolute = str(Path(os.path.abspath(file_path)))
        units = len(absolute.encode("utf-16-le")) // 2
        if units > _WINDOWS_LEGACY_MAX_PATH_CHARS:
            raise InventoryV3ProtocolV2Error(
                "fixed protocol file exceeds the deterministic Windows legacy "
                f"path budget: {file_path}"
            )


def _assert_plain_descendant_path(anchor: Path, target: Path, label: str) -> None:
    anchor_absolute = Path(os.path.abspath(anchor))
    target_absolute = Path(os.path.abspath(target))
    try:
        common = Path(os.path.commonpath((str(anchor_absolute), str(target_absolute))))
    except ValueError as exc:
        raise InventoryV3ProtocolV2Error(f"{label} escapes its trust anchor") from exc
    if common != anchor_absolute or target_absolute == anchor_absolute:
        raise InventoryV3ProtocolV2Error(f"{label} escapes its trust anchor")
    current = target_absolute
    while current != anchor_absolute:
        try:
            info = current.lstat()
        except FileNotFoundError:
            current = current.parent
            continue
        except OSError as exc:
            raise InventoryV3ProtocolV2Error(f"{label} ancestor is unavailable") from exc
        if _is_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise InventoryV3ProtocolV2Error(
                f"{label} contains a redirected or non-directory ancestor"
            )
        current = current.parent


def _assert_workspace_children(paths: ProtocolV2Paths, expected: set[str]) -> None:
    _assert_plain_directory(paths.workspace_root, "protocol V2 workspace")
    actual: set[str] = set()
    try:
        entries = list(os.scandir(paths.workspace_root))
    except OSError as exc:
        raise InventoryV3ProtocolV2Error("protocol V2 workspace is unreadable") from exc
    for entry in entries:
        info = Path(entry.path).lstat()
        if _is_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise InventoryV3ProtocolV2Error(
                "protocol V2 workspace contains a foreign file or redirected directory"
            )
        actual.add(entry.name)
    if actual != expected:
        raise InventoryV3ProtocolV2Error(
            "protocol V2 workspace directories differ from the fixed lifecycle state"
        )


def _reserve_attempt(
    paths: ProtocolV2Paths,
    protocol: ProtocolV2LockBinding,
    operation: str,
    binding: Mapping[str, object],
) -> str:
    try:
        operation_index = _ATTEMPT_OPERATIONS.index(operation)
    except ValueError as exc:
        raise InventoryV3ProtocolV2Error(
            "attempt operation is outside the fixed lifecycle"
        ) from exc
    if operation_index == 0:
        paths.attempt_root.parent.mkdir(parents=True, exist_ok=True)
        _assert_plain_directory(
            paths.attempt_root.parent,
            "protocol V2 attempt parent",
        )
        try:
            paths.attempt_root.mkdir(exist_ok=False)
        except FileExistsError as exc:
            raise InventoryV3ProtocolV2Error("protocol V2 attempt root already exists") from exc
    _assert_plain_directory(paths.attempt_root, "protocol V2 attempt root")
    prior_roles: dict[str, str] = {}
    for prior in _ATTEMPT_OPERATIONS[:operation_index]:
        prior_roles.update(
            {
                f"{prior}-reserved.json": "prior-attempt-reservation",
                f"{prior}-reserved.json.sha256": "prior-attempt-reservation-sidecar",
                f"{prior}-terminal.json": "prior-attempt-terminal",
                f"{prior}-terminal.json.sha256": "prior-attempt-terminal-sidecar",
            }
        )
    _scan_metadata_only_closed_tree(paths.attempt_root, prior_roles)
    last_terminal_at: datetime | None = None
    for prior in _ATTEMPT_OPERATIONS[:operation_index]:
        prior_terminal_at = _verify_prior_operation_passed(paths, protocol, prior)
        if last_terminal_at is not None and prior_terminal_at <= last_terminal_at:
            raise InventoryV3ProtocolV2Error(
                "attempt lifecycle chronology is not strictly increasing"
            )
        last_terminal_at = prior_terminal_at
    reserved_at_value = datetime.now(UTC)
    if last_terminal_at is not None and reserved_at_value <= last_terminal_at:
        raise InventoryV3ProtocolV2Error(
            "attempt reservation does not follow the prior terminal stage"
        )
    reserved_at = _format_utc(reserved_at_value)
    reservation = {
        "activation_allowed": False,
        "authorization_id": paths.authorization_id,
        "binding": dict(binding),
        "fallback_contract_id": "ATTEMPT_INTEGRITY_FAILURE",
        "fallback_terminal_status": "failed-terminal-if-no-completion-record",
        "operation": operation,
        "protocol_lock_git_commit_sha": protocol.lock_commit_sha,
        "protocol_lock_sha256": protocol.lock_sha256,
        "protocol_source_git_commit_sha": protocol.source_commit_sha,
        "reserved_at_utc": reserved_at,
        "retry_allowed": False,
        "schema": _ATTEMPT_SCHEMA,
        "status": "reserved-irrevocably",
    }
    return _write_canonical_exclusive(
        paths.attempt_root / f"{operation}-reserved.json", reservation
    )


def _verify_prior_operation_passed(
    paths: ProtocolV2Paths,
    protocol: ProtocolV2LockBinding,
    operation: str,
) -> datetime:
    reservation, reservation_payload = _read_canonical_json(
        paths.attempt_root / f"{operation}-reserved.json",
        schema=_ATTEMPT_SCHEMA,
        label=f"{operation} prior reservation",
    )
    terminal, _ = _read_canonical_json(
        paths.attempt_root / f"{operation}-terminal.json",
        schema=_ATTEMPT_SCHEMA,
        label=f"{operation} prior terminal",
    )
    _require_exact_keys(
        reservation,
        {
            "activation_allowed",
            "authorization_id",
            "binding",
            "fallback_contract_id",
            "fallback_terminal_status",
            "operation",
            "protocol_lock_git_commit_sha",
            "protocol_lock_sha256",
            "protocol_source_git_commit_sha",
            "reserved_at_utc",
            "retry_allowed",
            "schema",
            "status",
        },
        f"{operation} prior reservation",
    )
    _require_mapping(reservation.get("binding"), f"{operation} prior binding")
    _require_exact_keys(
        terminal,
        {
            "activation_allowed",
            "authorization_id",
            "contract_id",
            "operation",
            "output_sha256",
            "promotion_allowed",
            "protocol_lock_git_commit_sha",
            "protocol_lock_sha256",
            "reservation_sha256",
            "retry_allowed",
            "schema",
            "status",
            "terminal_at_utc",
        },
        f"{operation} prior terminal",
    )
    _require_lower_hex(terminal.get("output_sha256"), 64, f"{operation} prior output SHA")
    if (
        reservation.get("activation_allowed") is not False
        or reservation.get("authorization_id") != paths.authorization_id
        or reservation.get("fallback_contract_id") != "ATTEMPT_INTEGRITY_FAILURE"
        or reservation.get("fallback_terminal_status") != "failed-terminal-if-no-completion-record"
        or reservation.get("operation") != operation
        or reservation.get("protocol_lock_git_commit_sha") != protocol.lock_commit_sha
        or reservation.get("protocol_lock_sha256") != protocol.lock_sha256
        or reservation.get("protocol_source_git_commit_sha") != protocol.source_commit_sha
        or reservation.get("retry_allowed") is not False
        or reservation.get("status") != "reserved-irrevocably"
        or terminal.get("activation_allowed") is not False
        or terminal.get("authorization_id") != paths.authorization_id
        or terminal.get("contract_id") != _ATTEMPT_SUCCESS_CONTRACTS[operation]
        or terminal.get("operation") != operation
        or terminal.get("promotion_allowed") is not False
        or terminal.get("protocol_lock_git_commit_sha") != protocol.lock_commit_sha
        or terminal.get("protocol_lock_sha256") != protocol.lock_sha256
        or terminal.get("reservation_sha256") != _sha256(reservation_payload)
        or terminal.get("retry_allowed") is not False
        or terminal.get("status") != "passed-terminal"
    ):
        raise InventoryV3ProtocolV2Error(
            f"{operation} is not a closed successful prior lifecycle stage"
        )
    reserved_at = _parse_utc(
        reservation.get("reserved_at_utc"), f"{operation} prior reservation time"
    )
    terminal_at = _parse_utc(terminal.get("terminal_at_utc"), f"{operation} prior terminal time")
    if terminal_at <= reserved_at:
        raise InventoryV3ProtocolV2Error("prior attempt chronology differs")
    return terminal_at


def _record_attempt_terminal(
    paths: ProtocolV2Paths,
    protocol: ProtocolV2LockBinding,
    operation: str,
    *,
    status: str,
    contract_id: str,
    output_sha256: str | None,
) -> None:
    if status not in {"passed-terminal", "failed-terminal"}:
        raise InventoryV3ProtocolV2Error("attempt terminal status is invalid")
    if operation not in _ATTEMPT_SUCCESS_CONTRACTS:
        raise InventoryV3ProtocolV2Error("attempt operation is outside the lifecycle")
    if status == "passed-terminal" and (contract_id != _ATTEMPT_SUCCESS_CONTRACTS[operation]):
        raise InventoryV3ProtocolV2Error("attempt success contract differs")
    if status == "failed-terminal":
        from .privacy import FailureContractId

        try:
            FailureContractId(contract_id)
        except ValueError as exc:
            raise InventoryV3ProtocolV2Error(
                "attempt failure contract is not preregistered"
            ) from exc
        if (
            operation != "evaluate-locked-candidate"
            and contract_id != _ATTEMPT_FAILURE_CONTRACTS[operation]
        ):
            raise InventoryV3ProtocolV2Error("attempt failure contract differs")
    _require_lower_hex(output_sha256, 64, "attempt terminal output SHA")
    reservation_path = paths.attempt_root / f"{operation}-reserved.json"
    reservation, reservation_payload = _read_canonical_json(
        reservation_path,
        schema=_ATTEMPT_SCHEMA,
        label=f"{operation} reservation",
    )
    if (
        reservation.get("authorization_id") != paths.authorization_id
        or reservation.get("operation") != operation
        or reservation.get("protocol_lock_git_commit_sha") != protocol.lock_commit_sha
        or reservation.get("protocol_lock_sha256") != protocol.lock_sha256
        or reservation.get("protocol_source_git_commit_sha") != protocol.source_commit_sha
        or reservation.get("status") != "reserved-irrevocably"
        or reservation.get("retry_allowed") is not False
    ):
        raise InventoryV3ProtocolV2Error("attempt reservation binding differs")
    terminal_at_value = datetime.now(UTC)
    if terminal_at_value <= _parse_utc(
        reservation.get("reserved_at_utc"), "attempt reservation time"
    ):
        raise InventoryV3ProtocolV2Error("attempt terminal does not follow its reservation")
    terminal = {
        "activation_allowed": False,
        "authorization_id": paths.authorization_id,
        "contract_id": contract_id,
        "operation": operation,
        "output_sha256": output_sha256,
        "promotion_allowed": False,
        "protocol_lock_git_commit_sha": protocol.lock_commit_sha,
        "protocol_lock_sha256": protocol.lock_sha256,
        "reservation_sha256": _sha256(reservation_payload),
        "retry_allowed": False,
        "schema": _ATTEMPT_SCHEMA,
        "status": status,
        "terminal_at_utc": _format_utc(terminal_at_value),
    }
    _write_canonical_exclusive(paths.attempt_root / f"{operation}-terminal.json", terminal)


def _finalize_operation_failure(
    paths: ProtocolV2Paths,
    protocol: ProtocolV2LockBinding,
    authorization: LiveAuthorizationBinding,
    operation: str,
    contract_id: str,
    *,
    error_type: str,
) -> str:
    if (
        not error_type
        or len(error_type) > 128
        or any(not (character.isalnum() or character == "_") for character in error_type)
    ):
        raise InventoryV3ProtocolV2Error("operation failure type is not canonical")
    reservation, reservation_payload = _read_canonical_json(
        paths.attempt_root / f"{operation}-reserved.json",
        schema=_ATTEMPT_SCHEMA,
        label=f"{operation} failed reservation",
    )
    if (
        reservation.get("authorization_id") != authorization.authorization_id
        or reservation.get("operation") != operation
        or reservation.get("protocol_lock_git_commit_sha") != protocol.lock_commit_sha
        or reservation.get("protocol_lock_sha256") != protocol.lock_sha256
        or reservation.get("protocol_source_git_commit_sha") != protocol.source_commit_sha
    ):
        raise InventoryV3ProtocolV2Error("failed operation reservation binding differs")
    failure_root = paths.attempt_root / f"{operation}-failure"
    failure_root.mkdir(exist_ok=False)
    private_record = {
        "activation_allowed": False,
        "authorization_id": authorization.authorization_id,
        "contract_id": contract_id,
        "error_type": error_type,
        "opaque_receipt_id": authorization.opaque_receipt_id,
        "operation": operation,
        "promotion_allowed": False,
        "protocol_lock_git_commit_sha": protocol.lock_commit_sha,
        "protocol_lock_sha256": protocol.lock_sha256,
        "protocol_source_git_commit_sha": protocol.source_commit_sha,
        "reservation_sha256": _sha256(reservation_payload),
        "retry_allowed": False,
        "schema": "inventory-positive-v3-independent-operation-failure-v1",
        "terminal_status": "failed-terminal-permanent",
    }
    _write_canonical_exclusive(failure_root / "private-failure.json", private_record)
    _write_public_failure_receipt(failure_root, authorization, contract_id)
    tree_sha, _ = _write_tree_document(failure_root, _operation_failure_roles())
    return tree_sha


def _attempt_failed_best_effort(
    paths: ProtocolV2Paths,
    protocol: ProtocolV2LockBinding,
    authorization: LiveAuthorizationBinding,
    operation: str,
    contract_id: str,
    *,
    error_type: str,
) -> None:
    try:
        failure_tree_sha = _finalize_operation_failure(
            paths,
            protocol,
            authorization,
            operation,
            contract_id,
            error_type=error_type,
        )
        _record_attempt_terminal(
            paths,
            protocol,
            operation,
            status="failed-terminal",
            contract_id=contract_id,
            output_sha256=failure_tree_sha,
        )
    except Exception as exc:
        raise InventoryV3ProtocolV2Error(
            f"{operation} failed with {error_type} and closed terminal failure "
            "publication also failed; the irrevocable reservation's fallback "
            "status is permanent ATTEMPT_INTEGRITY_FAILURE"
        ) from exc


def _verify_successful_operation(
    paths: ProtocolV2Paths,
    protocol: ProtocolV2LockBinding,
    operation: str,
    *,
    expected_binding: Mapping[str, object],
    expected_contract_id: str,
    expected_output_sha256: str,
) -> datetime:
    reservation, reservation_payload = _read_canonical_json(
        paths.attempt_root / f"{operation}-reserved.json",
        schema=_ATTEMPT_SCHEMA,
        label=f"{operation} reservation",
    )
    terminal, _ = _read_canonical_json(
        paths.attempt_root / f"{operation}-terminal.json",
        schema=_ATTEMPT_SCHEMA,
        label=f"{operation} terminal record",
    )
    _require_exact_keys(
        reservation,
        {
            "activation_allowed",
            "authorization_id",
            "binding",
            "fallback_contract_id",
            "fallback_terminal_status",
            "operation",
            "protocol_lock_git_commit_sha",
            "protocol_lock_sha256",
            "protocol_source_git_commit_sha",
            "reserved_at_utc",
            "retry_allowed",
            "schema",
            "status",
        },
        f"{operation} reservation",
    )
    _require_exact_keys(
        terminal,
        {
            "activation_allowed",
            "authorization_id",
            "contract_id",
            "operation",
            "output_sha256",
            "promotion_allowed",
            "protocol_lock_git_commit_sha",
            "protocol_lock_sha256",
            "reservation_sha256",
            "retry_allowed",
            "schema",
            "status",
            "terminal_at_utc",
        },
        f"{operation} terminal record",
    )
    if (
        reservation.get("activation_allowed") is not False
        or reservation.get("authorization_id") != paths.authorization_id
        or reservation.get("binding") != dict(expected_binding)
        or reservation.get("fallback_contract_id") != "ATTEMPT_INTEGRITY_FAILURE"
        or reservation.get("fallback_terminal_status") != "failed-terminal-if-no-completion-record"
        or reservation.get("operation") != operation
        or reservation.get("protocol_lock_git_commit_sha") != protocol.lock_commit_sha
        or reservation.get("protocol_lock_sha256") != protocol.lock_sha256
        or reservation.get("protocol_source_git_commit_sha") != protocol.source_commit_sha
        or reservation.get("retry_allowed") is not False
        or reservation.get("status") != "reserved-irrevocably"
        or terminal.get("activation_allowed") is not False
        or terminal.get("promotion_allowed") is not False
        or terminal.get("authorization_id") != paths.authorization_id
        or terminal.get("contract_id") != expected_contract_id
        or terminal.get("operation") != operation
        or terminal.get("output_sha256") != expected_output_sha256
        or terminal.get("protocol_lock_git_commit_sha") != protocol.lock_commit_sha
        or terminal.get("protocol_lock_sha256") != protocol.lock_sha256
        or terminal.get("reservation_sha256") != _sha256(reservation_payload)
        or terminal.get("retry_allowed") is not False
        or terminal.get("status") != "passed-terminal"
    ):
        raise InventoryV3ProtocolV2Error(f"{operation} successful lineage record differs")
    terminal_at = _parse_utc(terminal.get("terminal_at_utc"), "attempt terminal time")
    if terminal_at <= _parse_utc(reservation.get("reserved_at_utc"), "attempt reservation time"):
        raise InventoryV3ProtocolV2Error("attempt terminal chronology differs")
    return terminal_at


def _write_tree_document(root: Path, roles: Mapping[str, str]) -> tuple[str, PackageTreeSnapshot]:
    try:
        snapshot = enumerate_package_tree(root, roles)
    except PackageTreeError as exc:
        raise InventoryV3ProtocolV2Error("package tree finalization failed") from exc
    tree_digest = _write_canonical_exclusive(root / _PACKAGE_TREE_NAME, snapshot.to_document())
    tree, _ = _read_canonical_json(
        root / _PACKAGE_TREE_NAME,
        schema="inventory-positive-v3-independent-package-tree-v1",
        label="package tree",
    )
    try:
        verified = verify_package_tree(
            root,
            tree,
            reserved_paths=(_PACKAGE_TREE_NAME, f"{_PACKAGE_TREE_NAME}.sha256"),
        )
    except PackageTreeError as exc:
        raise InventoryV3ProtocolV2Error("published package tree differs") from exc
    return tree_digest, verified


def _require_copied_snapshot_entries(
    source: PackageTreeSnapshot,
    destination: PackageTreeSnapshot,
    *,
    label: str,
) -> None:
    """Prove every source entry was published unchanged at the same role/path."""

    destination_entries = {entry.path: entry for entry in destination.entries}
    for source_entry in source.entries:
        destination_entry = destination_entries.get(source_entry.path)
        if (
            destination_entry is None
            or destination_entry.role != source_entry.role
            or destination_entry.size_bytes != source_entry.size_bytes
            or destination_entry.sha256 != source_entry.sha256
        ):
            raise InventoryV3ProtocolV2Error(f"{label} differs from its source snapshot")


def _producer_environment_value(value: object, label: str) -> str | int | bool:
    if isinstance(value, str):
        if not value.strip() or value != value.strip():
            raise InventoryV3ProtocolV2Error(f"{label} is not canonical text")
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    raise InventoryV3ProtocolV2Error(f"{label} is not a producer environment value")


def run_passive_capture_protocol_v2(
    repository_root: Path,
    *,
    expected_head: str,
    operator: str,
    runelite_build: str,
    client_mode: str,
    theme: str,
    renderer: str,
    attempt_base: Path | None = None,
) -> Path:
    """Invoke only the frozen passive launcher, then bind producer provenance.

    This function is a future authorized entry point.  Calling it without the
    exact atomic source authorization fails before the frozen capture process
    starts.  It never performs detector-driven selection or game input.
    """

    protocol = verify_protocol_v2_repository(repository_root, expected_head=expected_head)
    authorization = verify_live_authorization(protocol)
    paths = ProtocolV2Paths.for_authorization(
        protocol.repository_root,
        authorization.authorization_id,
        protocol.lock_sha256,
        attempt_base=attempt_base,
    )
    _assert_disjoint_paths(paths)
    actor_container: Mapping[str, object] = {"operator": operator}
    _require_actor(actor_container, "operator")
    assertions: dict[str, str] = {
        "client_mode": client_mode,
        "renderer": renderer,
        "runelite_build": runelite_build,
        "theme": theme,
    }
    for key, value in assertions.items():
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise InventoryV3ProtocolV2Error(
                f"capture assertion {key} must be canonical non-empty text"
            )
    from .producer import build_producer_provenance, observe_windows_identity

    observed_identity = observe_windows_identity()
    if observed_identity is None:
        raise InventoryV3ProtocolV2Error("OS-observed Windows producer identity is unavailable")
    operation = "capture-passive-campaign"
    _require_precapture_state_unoccupied(paths, include_attempt_root=True)
    _reserve_attempt(
        paths,
        protocol,
        operation,
        {
            "capture_build_sha": PROTOCOL_V1_SOURCE_HEAD,
            "capture_configuration_id": CAPTURE_CONFIGURATION_ID,
            "legacy_live_authorization_git_commit_sha": (authorization.git_commit_sha),
        },
    )
    try:
        _require_precapture_state_unoccupied(paths, include_attempt_root=False)
        launcher = protocol.repository_root / "tools" / "capture_inventory_v3_independent.py"
        _assert_plain_file(launcher, "frozen passive capture launcher")
        command = (
            sys.executable,
            "-I",
            "-S",
            str(launcher),
            "--operator",
            operator,
            "--runelite-build",
            runelite_build,
            "--client-mode",
            client_mode,
            "--theme",
            theme,
            "--renderer",
            renderer,
        )
        completed = subprocess.run(
            command,
            cwd=protocol.repository_root,
            check=False,
        )
        if completed.returncode != 0:
            raise InventoryV3ProtocolV2Error(
                f"frozen passive capture exited {completed.returncode}"
            )
        session, session_payload = _read_canonical_json(
            paths.source_campaign_root / _SESSION_REPORT_NAME,
            schema=_SOURCE_SESSION_SCHEMA,
            label="completed source session",
        )
        seal, seal_payload = _read_canonical_json(
            paths.source_campaign_root / _COMPLETION_SEAL_NAME,
            schema=_SOURCE_COMPLETION_SCHEMA,
            label="completed source seal",
        )
        environment = _require_mapping(session.get("capture_environment"), "capture environment")
        if (
            seal.get("status") != "complete-not-reviewed"
            or seal.get("authorization_id") != authorization.authorization_id
            or seal.get("source_session_report_sha256") != _sha256(session_payload)
        ):
            raise InventoryV3ProtocolV2Error("frozen capture completion is unbound")
        frame = _require_mapping(environment.get("frame"), "capture frame")
        observed_environment: dict[str, str | int | bool] = {
            "frame.height": _producer_environment_value(frame.get("height"), "frame.height"),
            "frame.pixel_format": _producer_environment_value(
                frame.get("pixel_format"), "frame.pixel_format"
            ),
            "frame.profile_id": _producer_environment_value(
                frame.get("profile_id"), "frame.profile_id"
            ),
            "frame.width": _producer_environment_value(frame.get("width"), "frame.width"),
            "window_class": _producer_environment_value(
                environment.get("window_class"), "window_class"
            ),
            "window_handle": _producer_environment_value(
                environment.get("window_handle"), "window_handle"
            ),
            "windows_dpi": _producer_environment_value(
                environment.get("windows_dpi"), "windows_dpi"
            ),
            "windows_scaling_percent": _producer_environment_value(
                environment.get("windows_scaling_percent"),
                "windows_scaling_percent",
            ),
            "windows_version": _producer_environment_value(
                environment.get("windows_version"), "windows_version"
            ),
        }
        provenance = build_producer_provenance(
            collected_at_utc=_format_utc(datetime.now(UTC)),
            protocol_lock_sha256=protocol.lock_sha256,
            live_authorization_id=authorization.authorization_id,
            opaque_receipt_id=authorization.opaque_receipt_id,
            capture_execution_head_sha=_require_lower_hex(
                environment.get("capture_execution_head_sha"),
                40,
                "capture execution HEAD",
            ),
            session_id=_require_text(session, "session_id"),
            source_session_report_sha256=_sha256(session_payload),
            source_completion_seal_sha256=_sha256(seal_payload),
            legacy_user_reservation_sha256=_require_lower_hex(
                environment.get("host_reservation_sha256"),
                64,
                "legacy reservation SHA-256",
            ),
            observed_identity=observed_identity,
            observed_environment=observed_environment,
            operator_asserted_environment=assertions,
        )
        attestation_path = paths.source_campaign_root / _PRODUCER_ATTESTATION_NAME
        attestation_sha = _write_canonical_exclusive(attestation_path, provenance.to_dict())
        preflight_source_metadata(
            protocol,
            authorization,
            attempt_base=attempt_base,
        )
        _record_attempt_terminal(
            paths,
            protocol,
            operation,
            status="passed-terminal",
            contract_id="PASSIVE_CAPTURE_COMPLETE_UNREVIEWED",
            output_sha256=attestation_sha,
        )
        return attestation_path
    except BaseException as exc:
        _attempt_failed_best_effort(
            paths,
            protocol,
            authorization,
            operation,
            "CAMPAIGN_TERMINAL_FAILURE",
            error_type=type(exc).__name__,
        )
        raise


def _read_verified_tree(
    root: Path,
    expected_roles: Mapping[str, str],
    *,
    expected_tree_sha256: str | None = None,
) -> PackageTreeSnapshot:
    tree, tree_payload = _read_canonical_json(
        root / _PACKAGE_TREE_NAME,
        schema="inventory-positive-v3-independent-package-tree-v1",
        label="package tree",
    )
    if expected_tree_sha256 is not None and _sha256(tree_payload) != _require_lower_hex(
        expected_tree_sha256, 64, "expected package tree SHA-256"
    ):
        raise InventoryV3ProtocolV2Error("package tree changed after metadata reservation")
    if _tree_roles_from_document(tree) != dict(expected_roles):
        raise InventoryV3ProtocolV2Error("package tree roles differ from fixed protocol")
    try:
        return verify_package_tree(
            root,
            tree,
            reserved_paths=(_PACKAGE_TREE_NAME, f"{_PACKAGE_TREE_NAME}.sha256"),
        )
    except PackageTreeError as exc:
        raise InventoryV3ProtocolV2Error("closed package tree differs") from exc


def _tree_entries_from_document(
    document: Mapping[str, object],
) -> dict[str, tuple[str, str, int]]:
    if set(document) != {"entries", "schema"}:
        raise InventoryV3ProtocolV2Error("package tree metadata keys differ")
    raw_entries = _require_list(document, "entries")
    entries: dict[str, tuple[str, str, int]] = {}
    for index, raw in enumerate(raw_entries, start=1):
        item = _require_mapping(raw, f"package tree entry {index}")
        _require_exact_keys(
            item,
            {"path", "role", "sha256", "size_bytes"},
            f"package tree entry {index}",
        )
        path = _require_text(item, "path")
        role = _require_text(item, "role")
        sha256 = _require_lower_hex(item.get("sha256"), 64, f"package tree entry {index} SHA")
        size = item.get("size_bytes")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise InventoryV3ProtocolV2Error("package tree entry size differs")
        if path in entries:
            raise InventoryV3ProtocolV2Error("package tree repeats a path")
        entries[path] = (role, sha256, size)
    return entries


def _tree_roles_from_document(document: Mapping[str, object]) -> dict[str, str]:
    return {
        path: entry[0]
        for path, entry in _tree_entries_from_document(document).items()
    }


def _read_tree_bound_payload(
    root: Path,
    entries: Mapping[str, tuple[str, str, int]],
    relative: str,
    label: str,
) -> bytes:
    entry = entries.get(relative)
    if entry is None:
        raise InventoryV3ProtocolV2Error(f"{label} is absent from package tree")
    path = _owned_path(root, relative, label)
    _assert_plain_file(path, label)
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise InventoryV3ProtocolV2Error(f"{label} is unreadable") from exc
    if _sha256(payload) != entry[1] or len(payload) != entry[2]:
        raise InventoryV3ProtocolV2Error(f"{label} differs from package tree metadata")
    return payload


def _require_tree_bound_payload(
    entries: Mapping[str, tuple[str, str, int]],
    relative: str,
    payload: bytes,
    label: str,
) -> None:
    entry = entries.get(relative)
    if entry is None or _sha256(payload) != entry[1] or len(payload) != entry[2]:
        raise InventoryV3ProtocolV2Error(f"{label} differs from package tree metadata")


def _require_tree_entry_subset_equal(
    original: Mapping[str, object],
    copied: Mapping[str, object],
    expected_roles: Mapping[str, str],
    label: str,
) -> None:
    original_entries = _tree_entries_from_document(original)
    copied_entries = _tree_entries_from_document(copied)
    for relative, role in expected_roles.items():
        original_entry = original_entries.get(relative)
        copied_entry = copied_entries.get(relative)
        if (
            original_entry is None
            or copied_entry is None
            or original_entry[0] != role
            or copied_entry != original_entry
        ):
            raise InventoryV3ProtocolV2Error(f"{label} differs")


def _preflight_tree_metadata_only(
    root: Path, expected_roles: Mapping[str, str]
) -> tuple[Mapping[str, object], bytes]:
    tree, payload = _read_canonical_json(
        root / _PACKAGE_TREE_NAME,
        schema="inventory-positive-v3-independent-package-tree-v1",
        label="package tree metadata",
    )
    roles = _tree_roles_from_document(tree)
    if roles != dict(expected_roles):
        raise InventoryV3ProtocolV2Error("package tree roles differ from fixed protocol")
    allowlist = dict(expected_roles)
    allowlist[_PACKAGE_TREE_NAME] = "reserved-package-tree"
    allowlist[f"{_PACKAGE_TREE_NAME}.sha256"] = "reserved-package-tree-sidecar"
    _scan_metadata_only_closed_tree(root, allowlist)
    return tree, payload


def _preflight_review_pipeline_lineage(
    source: SourceMetadataBinding,
    *,
    require_reviewed: bool,
) -> dict[str, str]:
    _, acquisition_payload = _preflight_tree_metadata_only(
        source.paths.acquisition_root, _acquisition_roles()
    )
    acquisition_sha = _sha256(acquisition_payload)
    _verify_successful_operation(
        source.paths,
        source.protocol,
        "finalize-acquisition",
        expected_binding={
            "source_completion_seal_sha256": _sha256(source.completion_payload),
            "source_session_report_sha256": _sha256(source.session_payload),
        },
        expected_contract_id="ACQUISITION_FINALIZED",
        expected_output_sha256=acquisition_sha,
    )
    manifest, manifest_payload = _read_canonical_json(
        source.paths.acquisition_root / _CAMPAIGN_MANIFEST_NAME,
        schema=_CAMPAIGN_MANIFEST_SCHEMA,
        label="campaign manifest lineage metadata",
    )
    del manifest
    manifest_sha = _sha256(manifest_payload)
    _scan_metadata_only_closed_tree(
        source.paths.review_intake_root,
        _review_root_allowlist(submission_complete=True),
    )
    _, intake_payload = _preflight_tree_metadata_only(
        source.paths.review_intake_root / "package", _review_intake_roles()
    )
    intake_sha = _sha256(intake_payload)
    _verify_successful_operation(
        source.paths,
        source.protocol,
        "prepare-review-intake",
        expected_binding={"acquisition_package_tree_sha256": acquisition_sha},
        expected_contract_id="REVIEW_INTAKE_PREPARED",
        expected_output_sha256=intake_sha,
    )
    submission_tree, submission_payload = _preflight_tree_metadata_only(
        source.paths.review_intake_root / "submission",
        _review_submission_roles(),
    )
    submission_entries = _tree_entries_from_document(submission_tree)
    submission_entry = submission_entries.get(_REVIEW_SUBMISSION_NAME)
    if submission_entry is None:
        raise InventoryV3ProtocolV2Error("review submission is absent from package tree")
    submission_sidecar = _read_tree_bound_payload(
        source.paths.review_intake_root / "submission",
        submission_entries,
        f"{_REVIEW_SUBMISSION_NAME}.sha256",
        "review submission sidecar",
    )
    if submission_sidecar != (
        f"{submission_entry[1]}  {_REVIEW_SUBMISSION_NAME}\n".encode("ascii")
    ):
        raise InventoryV3ProtocolV2Error("review submission sidecar binding differs")
    submission_sha = _sha256(submission_payload)
    _verify_successful_operation(
        source.paths,
        source.protocol,
        "record-review-submission",
        expected_binding={
            "acquisition_package_tree_sha256": acquisition_sha,
            "campaign_manifest_sha256": manifest_sha,
            "review_intake_tree_sha256": intake_sha,
        },
        expected_contract_id="REVIEW_SUBMISSION_RECORDED",
        expected_output_sha256=submission_sha,
    )
    result = {
        "acquisition_package_tree_sha256": acquisition_sha,
        "campaign_manifest_sha256": manifest_sha,
        "review_intake_package_tree_sha256": intake_sha,
        "review_submission_sha256": submission_entry[1],
        "review_submission_package_tree_sha256": submission_sha,
    }
    if require_reviewed:
        _, reviewed_payload = _preflight_tree_metadata_only(
            source.paths.reviewed_package_root, _reviewed_package_roles()
        )
        reviewed_sha = _sha256(reviewed_payload)
        _verify_successful_operation(
            source.paths,
            source.protocol,
            "publish-reviewed-package",
            expected_binding={
                "acquisition_package_tree_sha256": acquisition_sha,
                "review_intake_package_tree_sha256": intake_sha,
                "review_submission_package_tree_sha256": submission_sha,
            },
            expected_contract_id="REVIEWED_PACKAGE_FINALIZED",
            expected_output_sha256=reviewed_sha,
        )
        result["reviewed_package_tree_sha256"] = reviewed_sha
    return result


def _evaluator_compatible_source_documents(
    source: SourceMetadataBinding,
    campaign_id: str,
) -> tuple[Mapping[str, object], bytes, Mapping[str, object], bytes]:
    """Bridge the two frozen components' incompatible campaign-ID encodings."""

    if campaign_id != _content_bound_campaign_id(_require_text(source.session, "session_id")):
        raise InventoryV3ProtocolV2Error("evaluator-compatible campaign identity differs")
    session = copy.deepcopy(dict(source.session))
    session["campaign_id"] = campaign_id
    session_payload = _canonical_bytes(session)
    seal = copy.deepcopy(dict(source.completion_seal))
    seal["campaign_id"] = campaign_id
    seal["source_session_report_sha256"] = _sha256(session_payload)
    seal_payload = _canonical_bytes(seal)
    return session, session_payload, seal, seal_payload


def finalize_acquisition(
    repository_root: Path,
    *,
    expected_head: str,
    attempt_base: Path | None = None,
    access_hook: AccessHook | None = None,
) -> FinalizedAcquisition:
    """Finalize all seven source-owned captures without caller-selected inputs."""

    protocol = verify_protocol_v2_repository(repository_root, expected_head=expected_head)
    authorization = verify_live_authorization(protocol, access_hook=access_hook)
    source = preflight_source_metadata(
        protocol,
        authorization,
        attempt_base=attempt_base,
        access_hook=access_hook,
    )
    _assert_disjoint_paths(source.paths)
    _verify_successful_operation(
        source.paths,
        protocol,
        "capture-passive-campaign",
        expected_binding={
            "capture_build_sha": PROTOCOL_V1_SOURCE_HEAD,
            "capture_configuration_id": CAPTURE_CONFIGURATION_ID,
            "legacy_live_authorization_git_commit_sha": authorization.git_commit_sha,
        },
        expected_contract_id="PASSIVE_CAPTURE_COMPLETE_UNREVIEWED",
        expected_output_sha256=_sha256(_canonical_bytes(source.producer_attestation)),
    )
    operation = "finalize-acquisition"
    _reserve_attempt(
        source.paths,
        protocol,
        operation,
        {
            "source_completion_seal_sha256": _sha256(source.completion_payload),
            "source_session_report_sha256": _sha256(source.session_payload),
        },
    )
    try:
        source.paths.workspace_root.mkdir(parents=True, exist_ok=False)
        source.paths.acquisition_root.mkdir(exist_ok=False)
        _recheck_source_metadata(source)
        _emit(
            access_hook, "sensitive", "validation_pixels_opened", source.paths.source_campaign_root
        )
        try:
            source_snapshot = enumerate_package_tree(
                source.paths.source_campaign_root, _source_allowlist()
            )
        except PackageTreeError as exc:
            raise InventoryV3ProtocolV2Error("source bytes changed after preflight") from exc
        source_entries = {entry.path: entry for entry in source_snapshot.entries}
        for index, (stage, report) in enumerate(
            zip(REQUIRED_STAGES, source.capture_reports, strict=True), start=1
        ):
            prefix = f"captures/{index:03d}-{stage}"
            full = _require_mapping(report.get("full_frame"), "full frame metadata")
            region = _require_mapping(report.get("inventory_region"), "region metadata")
            full_entry = source_entries[f"{prefix}/full-frame.bgra"]
            region_entry = source_entries[f"{prefix}/inventory-region.bgra"]
            if (
                full_entry.sha256 != full.get("sha256")
                or full_entry.size_bytes != full.get("size_bytes")
                or region_entry.sha256 != region.get("sha256")
                or region_entry.size_bytes != region.get("size_bytes")
            ):
                raise InventoryV3ProtocolV2Error(
                    "source validation bytes differ from preflight metadata"
                )
        session_id = _require_text(source.session, "session_id")
        source_campaign_id = _require_text(source.session, "campaign_id")
        campaign_id = _content_bound_campaign_id(session_id)
        (
            evaluator_session,
            evaluator_session_payload,
            evaluator_seal,
            evaluator_seal_payload,
        ) = _evaluator_compatible_source_documents(source, campaign_id)
        for relative in source.source_files:
            source_path = _owned_path(source.paths.source_campaign_root, relative, "source file")
            destination = _owned_path(source.paths.acquisition_root, relative, "acquisition file")
            _copy_file_exclusive(source_path, destination, "source campaign file")
        evaluator_session_sha = _write_canonical_exclusive(
            source.paths.acquisition_root / _EVALUATOR_SESSION_NAME,
            evaluator_session,
        )
        evaluator_seal_sha = _write_canonical_exclusive(
            source.paths.acquisition_root / _EVALUATOR_SEAL_NAME,
            evaluator_seal,
        )
        if evaluator_session_sha != _sha256(
            evaluator_session_payload
        ) or evaluator_seal_sha != _sha256(evaluator_seal_payload):
            raise InventoryV3ProtocolV2Error(
                "evaluator-compatible source identity publication differs"
            )
        source_snapshot.recheck()
        cases: list[dict[str, object]] = []
        captures = _require_list(source.session, "captures")
        for index, (stage, raw_capture, report) in enumerate(
            zip(REQUIRED_STAGES, captures, source.capture_reports, strict=True),
            start=1,
        ):
            capture = _require_mapping(raw_capture, f"source capture {index}")
            region = _require_mapping(report.get("inventory_region"), "region metadata")
            report_ref = _require_mapping(capture.get("capture_report"), "capture report ref")
            capture_id = _require_text(capture, "capture_id")
            cases.append(
                {
                    "capture_id": capture_id,
                    "captured_at_utc": capture.get("captured_at_utc"),
                    "case_id": f"{session_id}/{capture_id}",
                    "frame_region": {
                        "path": region.get("path"),
                        "sha256": region.get("sha256"),
                        "size_bytes": region.get("size_bytes"),
                    },
                    "operator_label_status": "operator-selected-unverified",
                    "operator_stage_label": stage,
                    "planned_stage_id": stage,
                    "sequence_index": index,
                    "session_id": session_id,
                    "source": {
                        "capture_report": {
                            "path": report_ref.get("path"),
                            "sha256": report_ref.get("sha256"),
                        }
                    },
                }
            )
        source_completed_at = _parse_utc(
            source.session.get("completed_at_utc"), "source completion"
        )
        finalized_at_value = datetime.now(UTC)
        if finalized_at_value <= source_completed_at:
            raise InventoryV3ProtocolV2Error(
                "acquisition finalization must occur after source completion"
            )
        finalized_at = _format_utc(finalized_at_value)
        manifest: dict[str, object] = {
            "activation_allowed": False,
            "all_owned_captures_included": True,
            "campaign_id": campaign_id,
            "campaign_status": "finalized",
            "candidate_head_sha": FROZEN_V3_HEAD,
            "capture_environment": copy.deepcopy(source.session["capture_environment"]),
            "cases": cases,
            "dataset_id": "pending-content-bound-dataset-id",
            "dataset_role": "independent-validation-only",
            "finalized_at_utc": finalized_at,
            "operator": source.session.get("operator"),
            "preregistration_sha256": PROTOCOL_V1_PREREGISTRATION_SHA256,
            "prior_campaigns": [],
            "prototype_eligible": False,
            "schema": _CAMPAIGN_MANIFEST_SCHEMA,
            "selection_policy": "all-owned-captures-in-source-order-no-drop-no-replacement",
            "session_id": session_id,
            "source_completion_seal": {
                "path": _EVALUATOR_SEAL_NAME,
                "sha256": evaluator_seal_sha,
            },
            "source_session_report": {
                "path": _EVALUATOR_SESSION_NAME,
                "sha256": evaluator_session_sha,
            },
            "training_allowed": False,
        }
        manifest["dataset_id"] = _content_bound_dataset_id(manifest)
        manifest_sha = _write_canonical_exclusive(
            source.paths.acquisition_root / _CAMPAIGN_MANIFEST_NAME, manifest
        )
        producer_payload = (source.paths.acquisition_root / _PRODUCER_ATTESTATION_NAME).read_bytes()
        acquisition_record = {
            "activation_allowed": False,
            "authorization_id": authorization.authorization_id,
            "campaign_id": campaign_id,
            "campaign_manifest_sha256": manifest_sha,
            "dataset_id": manifest["dataset_id"],
            "finalized_at_utc": finalized_at,
            "frozen_candidate_head_sha": FROZEN_V3_HEAD,
            "live_authorization_git_commit_sha": authorization.git_commit_sha,
            "opaque_receipt_id": authorization.opaque_receipt_id,
            "original_source_completion_seal_sha256": _sha256(source.completion_payload),
            "original_source_session_report_sha256": _sha256(source.session_payload),
            "producer_attestation_sha256": _sha256(producer_payload),
            "promotion_allowed": False,
            "protocol_lock_git_commit_sha": protocol.lock_commit_sha,
            "protocol_lock_sha256": protocol.lock_sha256,
            "protocol_source_git_commit_sha": protocol.source_commit_sha,
            "schema": _ACQUISITION_SCHEMA,
            "source_campaign_id": source_campaign_id,
            "source_completion_seal_sha256": evaluator_seal_sha,
            "source_identity_bridge": _SOURCE_IDENTITY_BRIDGE,
            "source_session_report_sha256": evaluator_session_sha,
            "status": "finalized-unreviewed",
            "training_allowed": False,
        }
        _write_canonical_exclusive(
            source.paths.acquisition_root / "protocol-v2-acquisition.json",
            acquisition_record,
        )
        acquisition_roles = _acquisition_roles()
        tree_sha, acquisition_snapshot = _write_tree_document(
            source.paths.acquisition_root, acquisition_roles
        )
        _require_copied_snapshot_entries(
            source_snapshot,
            acquisition_snapshot,
            label="finalized acquisition copy",
        )
        source_snapshot.recheck()
        acquisition_snapshot.recheck()
        _record_attempt_terminal(
            source.paths,
            protocol,
            operation,
            status="passed-terminal",
            contract_id="ACQUISITION_FINALIZED",
            output_sha256=tree_sha,
        )
        return FinalizedAcquisition(
            root=source.paths.acquisition_root,
            campaign_id=campaign_id,
            dataset_id=str(manifest["dataset_id"]),
            campaign_manifest_sha256=manifest_sha,
            package_tree_sha256=tree_sha,
        )
    except BaseException as exc:
        _attempt_failed_best_effort(
            source.paths,
            protocol,
            authorization,
            operation,
            "CASE_EVIDENCE_INELIGIBLE",
            error_type=type(exc).__name__,
        )
        raise


def _preflight_acquisition_semantics(
    source: SourceMetadataBinding,
    root: Path,
    tree_document: Mapping[str, object],
    tree_payload: bytes,
) -> tuple[Mapping[str, object], Mapping[str, object], bytes]:
    entries = _tree_entries_from_document(tree_document)
    acquisition_roles = _acquisition_roles()
    for relative, expected_role in acquisition_roles.items():
        entry = entries.get(relative)
        if entry is None or entry[0] != expected_role:
            raise InventoryV3ProtocolV2Error(
                "acquisition package tree roles differ from fixed protocol"
            )

    metadata_payloads: dict[str, bytes] = {}
    for relative, role in acquisition_roles.items():
        if role.endswith("-bgra"):
            continue
        metadata_payloads[relative] = _read_tree_bound_payload(
            root,
            entries,
            relative,
            f"acquisition metadata {relative}",
        )

    source_root = source.paths.source_campaign_root
    for relative, role in _source_allowlist().items():
        if role.endswith("-bgra"):
            continue
        source_path = _owned_path(source_root, relative, "source metadata copy")
        _assert_plain_file(source_path, "source metadata copy")
        try:
            source_payload = source_path.read_bytes()
        except OSError as exc:
            raise InventoryV3ProtocolV2Error("source metadata copy is unreadable") from exc
        if metadata_payloads[relative] != source_payload:
            raise InventoryV3ProtocolV2Error(
                "finalized acquisition metadata differs from its source copy"
            )

    manifest, manifest_payload = _read_canonical_json(
        root / _CAMPAIGN_MANIFEST_NAME,
        schema=_CAMPAIGN_MANIFEST_SCHEMA,
        label="campaign manifest",
    )
    record, record_payload = _read_canonical_json(
        root / "protocol-v2-acquisition.json",
        schema=_ACQUISITION_SCHEMA,
        label="V2 acquisition record",
    )
    _require_tree_bound_payload(
        entries,
        _CAMPAIGN_MANIFEST_NAME,
        manifest_payload,
        "campaign manifest",
    )
    _require_tree_bound_payload(
        entries,
        "protocol-v2-acquisition.json",
        record_payload,
        "V2 acquisition record",
    )
    _require_exact_keys(
        manifest,
        {
            "activation_allowed",
            "all_owned_captures_included",
            "campaign_id",
            "campaign_status",
            "candidate_head_sha",
            "capture_environment",
            "cases",
            "dataset_id",
            "dataset_role",
            "finalized_at_utc",
            "operator",
            "preregistration_sha256",
            "prior_campaigns",
            "prototype_eligible",
            "schema",
            "selection_policy",
            "session_id",
            "source_completion_seal",
            "source_session_report",
            "training_allowed",
        },
        "campaign manifest",
    )
    _require_exact_keys(
        record,
        {
            "activation_allowed",
            "authorization_id",
            "campaign_id",
            "campaign_manifest_sha256",
            "dataset_id",
            "finalized_at_utc",
            "frozen_candidate_head_sha",
            "live_authorization_git_commit_sha",
            "opaque_receipt_id",
            "original_source_completion_seal_sha256",
            "original_source_session_report_sha256",
            "producer_attestation_sha256",
            "promotion_allowed",
            "protocol_lock_git_commit_sha",
            "protocol_lock_sha256",
            "protocol_source_git_commit_sha",
            "schema",
            "source_campaign_id",
            "source_completion_seal_sha256",
            "source_identity_bridge",
            "source_session_report_sha256",
            "status",
            "training_allowed",
        },
        "V2 acquisition record",
    )

    manifest_sha = _sha256(manifest_payload)
    campaign_id = _require_text(manifest, "campaign_id")
    (
        expected_session,
        expected_session_payload,
        expected_seal,
        expected_seal_payload,
    ) = _evaluator_compatible_source_documents(source, campaign_id)
    actual_session, actual_session_payload = _read_canonical_json(
        root / _EVALUATOR_SESSION_NAME,
        schema=_SOURCE_SESSION_SCHEMA,
        label="evaluator-compatible source session",
    )
    actual_seal, actual_seal_payload = _read_canonical_json(
        root / _EVALUATOR_SEAL_NAME,
        schema=_SOURCE_COMPLETION_SCHEMA,
        label="evaluator-compatible source completion seal",
    )
    _require_tree_bound_payload(
        entries,
        _EVALUATOR_SESSION_NAME,
        actual_session_payload,
        "evaluator-compatible source session",
    )
    _require_tree_bound_payload(
        entries,
        _EVALUATOR_SEAL_NAME,
        actual_seal_payload,
        "evaluator-compatible source completion seal",
    )
    session_ref = _require_mapping(
        manifest.get("source_session_report"), "campaign source session ref"
    )
    seal_ref = _require_mapping(
        manifest.get("source_completion_seal"), "campaign source completion ref"
    )
    _require_exact_keys(session_ref, {"path", "sha256"}, "campaign source session ref")
    _require_exact_keys(seal_ref, {"path", "sha256"}, "campaign source completion ref")

    finalized_at_text = _require_text(manifest, "finalized_at_utc")
    finalized_at = _parse_utc(finalized_at_text, "acquisition finalization")
    if (
        finalized_at_text != _format_utc(finalized_at)
        or finalized_at
        <= _parse_utc(source.session.get("completed_at_utc"), "source completion")
        or finalized_at >= datetime.now(UTC)
    ):
        raise InventoryV3ProtocolV2Error("acquisition finalization chronology differs")

    if (
        manifest.get("activation_allowed") is not False
        or manifest.get("all_owned_captures_included") is not True
        or manifest.get("campaign_status") != "finalized"
        or manifest.get("candidate_head_sha") != FROZEN_V3_HEAD
        or manifest.get("capture_environment") != source.session.get("capture_environment")
        or manifest.get("preregistration_sha256")
        != PROTOCOL_V1_PREREGISTRATION_SHA256
        or manifest.get("dataset_role") != "independent-validation-only"
        or manifest.get("prior_campaigns") != []
        or manifest.get("prototype_eligible") is not False
        or manifest.get("training_allowed") is not False
        or manifest.get("selection_policy")
        != "all-owned-captures-in-source-order-no-drop-no-replacement"
        or manifest.get("campaign_id")
        != _content_bound_campaign_id(_require_text(source.session, "session_id"))
        or manifest.get("session_id") != source.session.get("session_id")
        or manifest.get("operator") != source.session.get("operator")
        or manifest.get("dataset_id") != _content_bound_dataset_id(manifest)
        or session_ref
        != {
            "path": _EVALUATOR_SESSION_NAME,
            "sha256": _sha256(expected_session_payload),
        }
        or seal_ref
        != {
            "path": _EVALUATOR_SEAL_NAME,
            "sha256": _sha256(expected_seal_payload),
        }
    ):
        raise InventoryV3ProtocolV2Error("campaign manifest differs from source")

    if (
        record.get("activation_allowed") is not False
        or record.get("promotion_allowed") is not False
        or record.get("training_allowed") is not False
        or record.get("status") != "finalized-unreviewed"
        or record.get("authorization_id") != source.authorization.authorization_id
        or record.get("protocol_source_git_commit_sha")
        != source.protocol.source_commit_sha
        or record.get("protocol_lock_git_commit_sha")
        != source.protocol.lock_commit_sha
        or record.get("protocol_lock_sha256") != source.protocol.lock_sha256
        or record.get("frozen_candidate_head_sha") != FROZEN_V3_HEAD
        or record.get("live_authorization_git_commit_sha")
        != source.authorization.git_commit_sha
        or record.get("campaign_manifest_sha256") != manifest_sha
        or record.get("original_source_session_report_sha256")
        != _sha256(source.session_payload)
        or record.get("original_source_completion_seal_sha256")
        != _sha256(source.completion_payload)
        or record.get("source_session_report_sha256")
        != _sha256(expected_session_payload)
        or record.get("source_completion_seal_sha256")
        != _sha256(expected_seal_payload)
        or record.get("source_identity_bridge") != _SOURCE_IDENTITY_BRIDGE
        or record.get("producer_attestation_sha256")
        != _sha256(metadata_payloads[_PRODUCER_ATTESTATION_NAME])
        or actual_session != expected_session
        or actual_session_payload != expected_session_payload
        or actual_seal != expected_seal
        or actual_seal_payload != expected_seal_payload
        or record.get("campaign_id") != manifest.get("campaign_id")
        or record.get("source_campaign_id") != source.session.get("campaign_id")
        or record.get("dataset_id") != manifest.get("dataset_id")
        or record.get("finalized_at_utc") != finalized_at_text
        or record.get("opaque_receipt_id")
        != source.authorization.opaque_receipt_id
    ):
        raise InventoryV3ProtocolV2Error("finalized acquisition record differs")

    cases = _require_list(manifest, "cases")
    captures = _require_list(source.session, "captures")
    if len(cases) != len(REQUIRED_STAGES):
        raise InventoryV3ProtocolV2Error("campaign manifest case count differs")
    for index, (stage, raw_case, raw_capture, expected_report) in enumerate(
        zip(
            REQUIRED_STAGES,
            cases,
            captures,
            source.capture_reports,
            strict=True,
        ),
        start=1,
    ):
        case = _require_mapping(raw_case, f"campaign case {index}")
        capture = _require_mapping(raw_capture, f"source capture {index}")
        _require_exact_keys(
            case,
            {
                "capture_id",
                "captured_at_utc",
                "case_id",
                "frame_region",
                "operator_label_status",
                "operator_stage_label",
                "planned_stage_id",
                "sequence_index",
                "session_id",
                "source",
            },
            f"campaign case {index}",
        )
        frame_region = _require_mapping(
            case.get("frame_region"), f"campaign frame region {index}"
        )
        case_source = _require_mapping(case.get("source"), f"campaign source {index}")
        capture_ref = _require_mapping(
            case_source.get("capture_report"), f"campaign capture report ref {index}"
        )
        _require_exact_keys(
            frame_region,
            {"path", "sha256", "size_bytes"},
            f"campaign frame region {index}",
        )
        _require_exact_keys(case_source, {"capture_report"}, f"campaign source {index}")
        _require_exact_keys(
            capture_ref,
            {"path", "sha256"},
            f"campaign capture report ref {index}",
        )
        expected_capture_ref = _require_mapping(
            capture.get("capture_report"), f"source capture report ref {index}"
        )
        expected_region = _require_mapping(
            expected_report.get("inventory_region"),
            f"source inventory region {index}",
        )
        capture_id = _require_text(capture, "capture_id")
        if (
            case.get("sequence_index") != index
            or case.get("planned_stage_id") != stage
            or case.get("capture_id") != capture_id
            or case.get("captured_at_utc") != capture.get("captured_at_utc")
            or case.get("case_id")
            != f"{source.session.get('session_id')}/{capture_id}"
            or case.get("session_id") != source.session.get("session_id")
            or case.get("operator_label_status")
            != "operator-selected-unverified"
            or case.get("operator_stage_label") != stage
            or frame_region
            != {
                "path": expected_region.get("path"),
                "sha256": expected_region.get("sha256"),
                "size_bytes": expected_region.get("size_bytes"),
            }
            or case_source != {"capture_report": dict(expected_capture_ref)}
        ):
            raise InventoryV3ProtocolV2Error("campaign manifest case order differs")

        report_relative = _require_text(capture_ref, "path")
        report, report_payload = _read_canonical_json(
            _owned_path(root, report_relative, "acquisition capture report"),
            schema=_SOURCE_CAPTURE_SCHEMA,
            label=f"acquisition capture report {index}",
        )
        _require_tree_bound_payload(
            entries,
            report_relative,
            report_payload,
            f"acquisition capture report {index}",
        )
        full = _require_mapping(report.get("full_frame"), f"acquisition full frame {index}")
        region = _require_mapping(
            report.get("inventory_region"), f"acquisition inventory region {index}"
        )
        full_relative = _require_text(full, "path")
        region_relative = _require_text(region, "path")
        full_entry = entries.get(full_relative)
        region_entry = entries.get(region_relative)
        if (
            report != expected_report
            or capture_ref != expected_capture_ref
            or capture_ref.get("sha256") != _sha256(report_payload)
            or full_entry is None
            or full_entry[1] != full.get("sha256")
            or full_entry[2] != full.get("size_bytes")
            or region_entry is None
            or region_entry[1] != region.get("sha256")
            or region_entry[2] != region.get("size_bytes")
        ):
            raise InventoryV3ProtocolV2Error(
                "acquisition case evidence differs from declared package tree"
            )

    current_tree, current_tree_payload = _read_canonical_json(
        root / _PACKAGE_TREE_NAME,
        schema="inventory-positive-v3-independent-package-tree-v1",
        label="acquisition package tree metadata",
    )
    if current_tree != tree_document or current_tree_payload != tree_payload:
        raise InventoryV3ProtocolV2Error(
            "acquisition tree changed after metadata preflight"
        )
    return manifest, record, manifest_payload


def _load_acquisition(
    source: SourceMetadataBinding,
    *,
    access_hook: AccessHook | None,
) -> tuple[Mapping[str, object], Mapping[str, object], str, str, PackageTreeSnapshot]:
    root = source.paths.acquisition_root
    tree_document, preflight_tree_payload = _preflight_tree_metadata_only(
        root, _acquisition_roles()
    )
    tree_sha = _sha256(preflight_tree_payload)
    _verify_successful_operation(
        source.paths,
        source.protocol,
        "finalize-acquisition",
        expected_binding={
            "source_completion_seal_sha256": _sha256(source.completion_payload),
            "source_session_report_sha256": _sha256(source.session_payload),
        },
        expected_contract_id="ACQUISITION_FINALIZED",
        expected_output_sha256=tree_sha,
    )
    manifest, record, manifest_payload = _preflight_acquisition_semantics(
        source,
        root,
        tree_document,
        preflight_tree_payload,
    )
    _emit(access_hook, "sensitive", "validation_pixels_opened", root)
    snapshot = _read_verified_tree(
        root,
        _acquisition_roles(),
        expected_tree_sha256=tree_sha,
    )
    return manifest, record, _sha256(manifest_payload), tree_sha, snapshot

def _review_case_id(campaign_manifest_sha256: str, sequence_index: int) -> str:
    identity = {
        "campaign_manifest_sha256": campaign_manifest_sha256,
        "sequence_index": sequence_index,
    }
    return "review-case-" + _sha256(_canonical_data_bytes(identity))[:24]


def prepare_reviewer_intake(
    repository_root: Path,
    *,
    expected_head: str,
    attempt_base: Path | None = None,
    access_hook: AccessHook | None = None,
) -> ReviewerIntake:
    """Create a blind, fixed-path reviewer intake only after acquisition."""

    protocol = verify_protocol_v2_repository(repository_root, expected_head=expected_head)
    authorization = verify_live_authorization(protocol, access_hook=access_hook)
    source = preflight_source_metadata(
        protocol,
        authorization,
        attempt_base=attempt_base,
        access_hook=access_hook,
    )
    _assert_disjoint_paths(source.paths)
    _recheck_source_metadata(source)
    _assert_workspace_children(source.paths, {_WORKSPACE_ACQUISITION_DIR})
    tree_document, tree_payload = _read_canonical_json(
        source.paths.acquisition_root / _PACKAGE_TREE_NAME,
        schema="inventory-positive-v3-independent-package-tree-v1",
        label="acquisition package tree metadata",
    )
    del tree_document
    acquisition_tree_sha = _sha256(tree_payload)
    _verify_successful_operation(
        source.paths,
        protocol,
        "finalize-acquisition",
        expected_binding={
            "source_completion_seal_sha256": _sha256(source.completion_payload),
            "source_session_report_sha256": _sha256(source.session_payload),
        },
        expected_contract_id="ACQUISITION_FINALIZED",
        expected_output_sha256=acquisition_tree_sha,
    )
    operation = "prepare-review-intake"
    _reserve_attempt(
        source.paths,
        protocol,
        operation,
        {"acquisition_package_tree_sha256": acquisition_tree_sha},
    )
    try:
        intake_root = source.paths.review_intake_root
        package_root = intake_root / "package"
        intake_root.mkdir(exist_ok=False)
        package_root.mkdir()
        manifest, _, manifest_sha, acquisition_tree_sha, acquisition_snapshot = _load_acquisition(
            source, access_hook=access_hook
        )
        cases = _require_list(manifest, "cases")
        template_cases: list[dict[str, object]] = []
        roles: dict[str, str] = {}
        for index, raw_case in enumerate(cases, start=1):
            case = _require_mapping(raw_case, f"campaign case {index}")
            frame_region = _require_mapping(case.get("frame_region"), "frame region")
            source_ref = _require_mapping(case.get("source"), "case source")
            capture_ref = _require_mapping(source_ref.get("capture_report"), "capture report ref")
            report, _ = _read_canonical_json(
                _owned_path(
                    source.paths.acquisition_root,
                    _require_text(capture_ref, "path"),
                    "acquisition capture report",
                ),
                schema=_SOURCE_CAPTURE_SCHEMA,
                label=f"acquisition capture report {index}",
            )
            full = _require_mapping(report.get("full_frame"), "full frame")
            full_relative = f"cases/{index:03d}/full-frame.bgra"
            region_relative = f"cases/{index:03d}/inventory-region.bgra"
            full_source = _owned_path(
                source.paths.acquisition_root,
                _require_text(full, "path"),
                "acquisition full frame",
            )
            region_source = _owned_path(
                source.paths.acquisition_root,
                _require_text(frame_region, "path"),
                "acquisition region",
            )
            full_destination = _owned_path(package_root, full_relative, "review full frame")
            region_destination = _owned_path(package_root, region_relative, "review region")
            full_destination.parent.mkdir(parents=True, exist_ok=True)
            _copy_file_exclusive(full_source, full_destination, "acquisition full frame")
            _copy_file_exclusive(
                region_source,
                region_destination,
                "acquisition inventory region",
            )
            roles[full_relative] = "private-review-full-frame-bgra"
            roles[region_relative] = "private-review-inventory-region-bgra"
            template_cases.append(
                {
                    "frame_region": {
                        "path": region_relative,
                        "sha256": frame_region.get("sha256"),
                        "size_bytes": frame_region.get("size_bytes"),
                    },
                    "full_frame": {
                        "path": full_relative,
                        "sha256": full.get("sha256"),
                        "size_bytes": full.get("size_bytes"),
                    },
                    "review_case_id": _review_case_id(manifest_sha, index),
                    "truth": {
                        "decision": None,
                        "drag_visible": None,
                        "hover_visible": None,
                        "occupied_slots": None,
                        "ordinary_iron_only": None,
                        "quantity_text_visible": None,
                        "review_note": None,
                        "selected_item_visible": None,
                        "visibility": None,
                    },
                }
            )
        acquisition_snapshot.recheck()
        template = {
            "acquisition_package_tree_sha256": acquisition_tree_sha,
            "activation_allowed": False,
            "campaign_manifest_sha256": manifest_sha,
            "cases": template_cases,
            "operator_labels_available": False,
            "promotion_allowed": False,
            "protocol_lock_git_commit_sha": protocol.lock_commit_sha,
            "protocol_lock_sha256": protocol.lock_sha256,
            "reviewer_truth_prefilled": False,
            "schema": _REVIEW_INTAKE_SCHEMA,
            "submission_path": f"submission/{_REVIEW_SUBMISSION_NAME}",
            "truth_source_required": "independent-human-review",
        }
        template_sha = _write_canonical_exclusive(package_root / _REVIEW_TEMPLATE_NAME, template)
        roles[_REVIEW_TEMPLATE_NAME] = "independent-review-template"
        roles[f"{_REVIEW_TEMPLATE_NAME}.sha256"] = "review-template-sidecar"
        intake_tree_sha, intake_snapshot = _write_tree_document(package_root, roles)
        acquisition_entries = {entry.path: entry for entry in acquisition_snapshot.entries}
        intake_entries = {entry.path: entry for entry in intake_snapshot.entries}
        for index, raw_case in enumerate(cases, start=1):
            case = _require_mapping(raw_case, f"campaign case {index}")
            frame_region = _require_mapping(case.get("frame_region"), "frame region")
            source_ref = _require_mapping(case.get("source"), "case source")
            capture_ref = _require_mapping(source_ref.get("capture_report"), "capture report ref")
            report, _ = _read_canonical_json(
                _owned_path(
                    source.paths.acquisition_root,
                    _require_text(capture_ref, "path"),
                    "acquisition capture report",
                ),
                schema=_SOURCE_CAPTURE_SCHEMA,
                label=f"acquisition capture report {index}",
            )
            full = _require_mapping(report.get("full_frame"), "full frame")
            pairs = (
                (
                    _require_text(full, "path"),
                    f"cases/{index:03d}/full-frame.bgra",
                ),
                (
                    _require_text(frame_region, "path"),
                    f"cases/{index:03d}/inventory-region.bgra",
                ),
            )
            for source_path, intake_path in pairs:
                source_entry = acquisition_entries.get(source_path)
                intake_entry = intake_entries.get(intake_path)
                if (
                    source_entry is None
                    or intake_entry is None
                    or source_entry.sha256 != intake_entry.sha256
                    or source_entry.size_bytes != intake_entry.size_bytes
                ):
                    raise InventoryV3ProtocolV2Error(
                        "review intake bytes differ from finalized acquisition"
                    )
        acquisition_snapshot.recheck()
        intake_snapshot.recheck()
        _scan_metadata_only_closed_tree(
            intake_root,
            _review_root_allowlist(submission_complete=False),
        )
        _record_attempt_terminal(
            source.paths,
            protocol,
            operation,
            status="passed-terminal",
            contract_id="REVIEW_INTAKE_PREPARED",
            output_sha256=intake_tree_sha,
        )
        return ReviewerIntake(
            root=intake_root,
            campaign_manifest_sha256=manifest_sha,
            reviewer_template_sha256=template_sha,
        )
    except BaseException as exc:
        _attempt_failed_best_effort(
            source.paths,
            protocol,
            authorization,
            operation,
            "CASE_EVIDENCE_INELIGIBLE",
            error_type=type(exc).__name__,
        )
        raise


def _mapping_contains_any_key(value: object, forbidden: set[str]) -> bool:
    if isinstance(value, Mapping):
        return bool(set(value) & forbidden) or any(
            _mapping_contains_any_key(item, forbidden) for item in value.values()
        )
    if isinstance(value, list):
        return any(_mapping_contains_any_key(item, forbidden) for item in value)
    return False


def _load_review_intake(
    source: SourceMetadataBinding,
    *,
    expected_manifest_sha256: str,
    expected_acquisition_tree_sha256: str,
    submission_state: str,
) -> tuple[Mapping[str, object], str, PackageTreeSnapshot]:
    if submission_state not in {"absent", "empty", "complete"}:
        raise InventoryV3ProtocolV2Error("review submission state differs")
    _scan_metadata_only_closed_tree(
        source.paths.review_intake_root,
        _review_root_allowlist(submission_complete=submission_state == "complete"),
        expected_empty_directories=("submission",) if submission_state == "empty" else (),
    )
    package_root = source.paths.review_intake_root / "package"
    preflight_tree, preflight_tree_payload = _preflight_tree_metadata_only(
        package_root, _review_intake_roles()
    )
    tree_entries = _tree_entries_from_document(preflight_tree)
    preflight_tree_sha = _sha256(preflight_tree_payload)
    _verify_successful_operation(
        source.paths,
        source.protocol,
        "prepare-review-intake",
        expected_binding={"acquisition_package_tree_sha256": expected_acquisition_tree_sha256},
        expected_contract_id="REVIEW_INTAKE_PREPARED",
        expected_output_sha256=preflight_tree_sha,
    )
    template, template_payload = _read_canonical_json(
        package_root / _REVIEW_TEMPLATE_NAME,
        schema=_REVIEW_INTAKE_SCHEMA,
        label="independent reviewer template",
    )
    _require_tree_bound_payload(
        tree_entries,
        _REVIEW_TEMPLATE_NAME,
        template_payload,
        "independent reviewer template",
    )
    _require_exact_keys(
        template,
        {
            "acquisition_package_tree_sha256",
            "activation_allowed",
            "campaign_manifest_sha256",
            "cases",
            "operator_labels_available",
            "promotion_allowed",
            "protocol_lock_git_commit_sha",
            "protocol_lock_sha256",
            "reviewer_truth_prefilled",
            "schema",
            "submission_path",
            "truth_source_required",
        },
        "independent reviewer template",
    )
    if (
        template.get("activation_allowed") is not False
        or template.get("promotion_allowed") is not False
        or template.get("operator_labels_available") is not False
        or template.get("reviewer_truth_prefilled") is not False
        or template.get("truth_source_required") != "independent-human-review"
        or template.get("submission_path") != f"submission/{_REVIEW_SUBMISSION_NAME}"
        or template.get("campaign_manifest_sha256") != expected_manifest_sha256
        or template.get("acquisition_package_tree_sha256") != expected_acquisition_tree_sha256
        or template.get("protocol_lock_git_commit_sha") != source.protocol.lock_commit_sha
        or template.get("protocol_lock_sha256") != source.protocol.lock_sha256
    ):
        raise InventoryV3ProtocolV2Error("reviewer template binding differs")
    if _mapping_contains_any_key(
        template,
        {
            "capture_id",
            "case_id",
            "operator",
            "operator_stage_label",
            "planned_stage_id",
            "session_id",
        },
    ):
        raise InventoryV3ProtocolV2Error("reviewer template leaks operator labels")
    raw_cases = _require_list(template, "cases")
    if len(raw_cases) != len(REQUIRED_STAGES):
        raise InventoryV3ProtocolV2Error("reviewer template case count differs")
    seen: set[str] = set()
    for index, raw in enumerate(raw_cases, start=1):
        item = _require_mapping(raw, f"review template case {index}")
        _require_exact_keys(
            item,
            {"frame_region", "full_frame", "review_case_id", "truth"},
            f"review template case {index}",
        )
        review_case_id = _require_text(item, "review_case_id")
        if (
            review_case_id != _review_case_id(expected_manifest_sha256, index)
            or review_case_id in seen
        ):
            raise InventoryV3ProtocolV2Error("review template identity differs")
        seen.add(review_case_id)
        for key, expected_path in (
            ("full_frame", f"cases/{index:03d}/full-frame.bgra"),
            ("frame_region", f"cases/{index:03d}/inventory-region.bgra"),
        ):
            frame = _require_mapping(item.get(key), f"review template {key}")
            entry = tree_entries.get(expected_path)
            if (
                set(frame) != {"path", "sha256", "size_bytes"}
                or frame.get("path") != expected_path
                or entry is None
                or frame.get("sha256") != entry[1]
                or frame.get("size_bytes") != entry[2]
            ):
                raise InventoryV3ProtocolV2Error(
                    "review template bytes differ from finalized intake tree"
                )
        truth = _require_mapping(item.get("truth"), "empty reviewer truth")
        expected_truth_keys = {
            "decision",
            "drag_visible",
            "hover_visible",
            "occupied_slots",
            "ordinary_iron_only",
            "quantity_text_visible",
            "review_note",
            "selected_item_visible",
            "visibility",
        }
        if set(truth) != expected_truth_keys or any(value is not None for value in truth.values()):
            raise InventoryV3ProtocolV2Error("review template prefills truth")
    snapshot = _read_verified_tree(
        package_root,
        _review_intake_roles(),
        expected_tree_sha256=preflight_tree_sha,
    )
    tree_sha = _sha256((package_root / _PACKAGE_TREE_NAME).read_bytes())
    if tree_sha != preflight_tree_sha:
        raise InventoryV3ProtocolV2Error("review intake tree changed after preflight")
    return template, tree_sha, snapshot


def _validate_reviewer_truth_fields(
    value: Mapping[str, object], *, label: str
) -> dict[str, object]:
    expected = {
        "decision",
        "drag_visible",
        "hover_visible",
        "occupied_slots",
        "ordinary_iron_only",
        "quantity_text_visible",
        "review_note",
        "selected_item_visible",
        "visibility",
    }
    _require_exact_keys(value, expected, label)
    decision = value.get("decision")
    visibility = value.get("visibility")
    if decision not in {"approved", "rejected"}:
        raise InventoryV3ProtocolV2Error(f"{label} decision differs")
    if visibility not in {
        "inventory-visible",
        "wrong-tab-visible",
        "inventory-obstructed",
    }:
        raise InventoryV3ProtocolV2Error(f"{label} visibility differs")
    occupied = value.get("occupied_slots")
    if occupied is not None and (
        not isinstance(occupied, int) or isinstance(occupied, bool) or not 0 <= occupied <= 28
    ):
        raise InventoryV3ProtocolV2Error(f"{label} occupied_slots differs")
    for key in (
        "drag_visible",
        "hover_visible",
        "ordinary_iron_only",
        "quantity_text_visible",
        "selected_item_visible",
    ):
        if not isinstance(value.get(key), bool):
            raise InventoryV3ProtocolV2Error(f"{label} {key} must be boolean")
    note = value.get("review_note")
    if note is not None and (not isinstance(note, str) or len(note) > 1000 or note != note.strip()):
        raise InventoryV3ProtocolV2Error(f"{label} review_note differs")
    return copy.deepcopy(dict(value))


def record_reviewer_submission(
    repository_root: Path,
    *,
    expected_head: str,
    reviewer: str,
    truth_provider: ReviewerTruthProvider,
    attempt_base: Path | None = None,
    access_hook: AccessHook | None = None,
) -> Path:
    """Preflight first, then collect one blinded reviewer submission."""

    if not callable(truth_provider):
        raise TypeError("truth_provider must be callable")
    protocol = verify_protocol_v2_repository(repository_root, expected_head=expected_head)
    authorization = verify_live_authorization(protocol, access_hook=access_hook)
    source = preflight_source_metadata(
        protocol,
        authorization,
        attempt_base=attempt_base,
        access_hook=access_hook,
    )
    _assert_disjoint_paths(source.paths)
    _recheck_source_metadata(source)
    _assert_workspace_children(
        source.paths,
        {_WORKSPACE_ACQUISITION_DIR, _WORKSPACE_REVIEW_INTAKE_DIR},
    )
    reviewer_container: Mapping[str, object] = {"reviewer": reviewer}
    reviewer_identity = _require_actor(reviewer_container, "reviewer")
    operator = _require_actor(source.session, "operator")
    if reviewer_identity == operator:
        raise InventoryV3ProtocolV2Error("independent reviewer must differ from operator")
    _, acquisition_tree_payload = _preflight_tree_metadata_only(
        source.paths.acquisition_root, _acquisition_roles()
    )
    acquisition_tree_sha = _sha256(acquisition_tree_payload)
    _verify_successful_operation(
        source.paths,
        protocol,
        "finalize-acquisition",
        expected_binding={
            "source_completion_seal_sha256": _sha256(source.completion_payload),
            "source_session_report_sha256": _sha256(source.session_payload),
        },
        expected_contract_id="ACQUISITION_FINALIZED",
        expected_output_sha256=acquisition_tree_sha,
    )
    manifest, manifest_payload = _read_canonical_json(
        source.paths.acquisition_root / _CAMPAIGN_MANIFEST_NAME,
        schema=_CAMPAIGN_MANIFEST_SCHEMA,
        label="campaign manifest metadata",
    )
    del manifest
    _scan_metadata_only_closed_tree(
        source.paths.review_intake_root,
        _review_root_allowlist(submission_complete=False),
    )
    _, intake_tree_payload = _preflight_tree_metadata_only(
        source.paths.review_intake_root / "package", _review_intake_roles()
    )
    intake_tree_sha = _sha256(intake_tree_payload)
    _verify_successful_operation(
        source.paths,
        protocol,
        "prepare-review-intake",
        expected_binding={"acquisition_package_tree_sha256": acquisition_tree_sha},
        expected_contract_id="REVIEW_INTAKE_PREPARED",
        expected_output_sha256=intake_tree_sha,
    )
    operation = "record-review-submission"
    _reserve_attempt(
        source.paths,
        protocol,
        operation,
        {
            "acquisition_package_tree_sha256": acquisition_tree_sha,
            "campaign_manifest_sha256": _sha256(manifest_payload),
            "review_intake_tree_sha256": intake_tree_sha,
        },
    )
    try:
        submission_root = source.paths.review_intake_root / "submission"
        submission_root.mkdir(exist_ok=False)
        _, _, _, acquisition_tree_sha, acquisition_snapshot = _load_acquisition(
            source, access_hook=access_hook
        )
        template, intake_tree_sha, intake_snapshot = _load_review_intake(
            source,
            expected_manifest_sha256=_sha256(manifest_payload),
            expected_acquisition_tree_sha256=acquisition_tree_sha,
            submission_state="empty",
        )
        collection_started_at = datetime.now(UTC)
        _emit(access_hook, "review", "reviewer_truth_collected", None)
        provided = _require_mapping(
            truth_provider(copy.deepcopy(template)), "reviewer truth provider result"
        )
        collection_completed_at = datetime.now(UTC)
        _require_exact_keys(
            provided,
            {"cases", "reviewed_at_utc", "reviewer"},
            "reviewer truth provider result",
        )
        provided_reviewer = _require_actor(provided, "reviewer")
        if provided_reviewer != reviewer_identity:
            raise InventoryV3ProtocolV2Error(
                "reviewer truth provider identity differs from preflight reviewer"
            )
        reviewed_at = _parse_utc(provided.get("reviewed_at_utc"), "reviewed_at_utc")
        if not collection_started_at < reviewed_at <= collection_completed_at:
            raise InventoryV3ProtocolV2Error(
                "review timestamp must be observed during this reviewer collection"
            )
        manifest_value, _ = _read_canonical_json(
            source.paths.acquisition_root / _CAMPAIGN_MANIFEST_NAME,
            schema=_CAMPAIGN_MANIFEST_SCHEMA,
            label="campaign manifest",
        )
        if reviewed_at <= _parse_utc(manifest_value.get("finalized_at_utc"), "finalized_at_utc"):
            raise InventoryV3ProtocolV2Error("review must follow acquisition finalization")
        provided_cases = _require_list(provided, "cases")
        template_cases = _require_list(template, "cases")
        if len(provided_cases) != len(template_cases):
            raise InventoryV3ProtocolV2Error("review submission case count differs")
        submission_cases: list[dict[str, object]] = []
        seen: set[str] = set()
        for index, (raw, raw_template) in enumerate(
            zip(provided_cases, template_cases, strict=True), start=1
        ):
            item = _require_mapping(raw, f"review submission case {index}")
            template_case = _require_mapping(raw_template, f"review template case {index}")
            _require_exact_keys(
                item,
                {"review_case_id", "truth"},
                f"review submission case {index}",
            )
            review_case_id = _require_text(item, "review_case_id")
            if review_case_id != template_case.get("review_case_id") or review_case_id in seen:
                raise InventoryV3ProtocolV2Error("review submission order differs")
            seen.add(review_case_id)
            truth = _validate_reviewer_truth_fields(
                _require_mapping(item.get("truth"), "review truth"),
                label=f"review truth {index}",
            )
            submission_cases.append({"review_case_id": review_case_id, "truth": truth})
        acquisition_snapshot.recheck()
        intake_snapshot.recheck()
        submission = {
            "acquisition_package_tree_sha256": acquisition_tree_sha,
            "activation_allowed": False,
            "campaign_manifest_sha256": _sha256(manifest_payload),
            "cases": submission_cases,
            "operator_labels_used_as_truth": False,
            "promotion_allowed": False,
            "review_intake_tree_sha256": intake_tree_sha,
            "reviewed_at_utc": provided.get("reviewed_at_utc"),
            "reviewer": reviewer_identity,
            "schema": _REVIEW_SUBMISSION_SCHEMA,
            "truth_source": "independent-human-review",
        }
        _write_canonical_exclusive(submission_root / _REVIEW_SUBMISSION_NAME, submission)
        submission_tree_sha, _ = _write_tree_document(
            submission_root,
            {
                _REVIEW_SUBMISSION_NAME: "independent-reviewer-submission",
                f"{_REVIEW_SUBMISSION_NAME}.sha256": "reviewer-submission-sidecar",
            },
        )
        _scan_metadata_only_closed_tree(
            source.paths.review_intake_root,
            _review_root_allowlist(submission_complete=True),
        )
        _record_attempt_terminal(
            source.paths,
            protocol,
            operation,
            status="passed-terminal",
            contract_id="REVIEW_SUBMISSION_RECORDED",
            output_sha256=submission_tree_sha,
        )
        return submission_root / _REVIEW_SUBMISSION_NAME
    except BaseException as exc:
        _attempt_failed_best_effort(
            source.paths,
            protocol,
            authorization,
            operation,
            "CASE_EVIDENCE_INELIGIBLE",
            error_type=type(exc).__name__,
        )
        raise


def _project_reviewer_truth(
    manifest: Mapping[str, object],
    manifest_sha256: str,
    submission: Mapping[str, object],
    *,
    expected_acquisition_tree_sha256: str,
    expected_review_intake_tree_sha256: str,
) -> dict[str, object]:
    _require_exact_keys(
        submission,
        {
            "acquisition_package_tree_sha256",
            "activation_allowed",
            "campaign_manifest_sha256",
            "cases",
            "operator_labels_used_as_truth",
            "promotion_allowed",
            "review_intake_tree_sha256",
            "reviewed_at_utc",
            "reviewer",
            "schema",
            "truth_source",
        },
        "reviewer submission",
    )
    if (
        submission.get("activation_allowed") is not False
        or submission.get("promotion_allowed") is not False
        or submission.get("operator_labels_used_as_truth") is not False
        or submission.get("truth_source") != "independent-human-review"
        or submission.get("acquisition_package_tree_sha256")
        != expected_acquisition_tree_sha256
        or submission.get("campaign_manifest_sha256") != manifest_sha256
        or submission.get("review_intake_tree_sha256")
        != expected_review_intake_tree_sha256
    ):
        raise InventoryV3ProtocolV2Error("reviewer submission binding differs")
    reviewer = _require_actor(submission, "reviewer")
    operator = _require_actor(manifest, "operator")
    if reviewer == operator:
        raise InventoryV3ProtocolV2Error("independent reviewer must differ from operator")
    reviewed_at_text = _require_text(submission, "reviewed_at_utc")
    reviewed_at = _parse_utc(reviewed_at_text, "reviewed_at_utc")
    if (
        reviewed_at_text != _format_utc(reviewed_at)
        or reviewed_at
        <= _parse_utc(manifest.get("finalized_at_utc"), "finalized_at_utc")
    ):
        raise InventoryV3ProtocolV2Error("review chronology differs")

    submitted_cases = _require_list(submission, "cases")
    manifest_cases = _require_list(manifest, "cases")
    if not (
        len(submitted_cases) == len(manifest_cases) == len(REQUIRED_STAGES)
    ):
        raise InventoryV3ProtocolV2Error("review case count differs")
    truths: list[dict[str, object]] = []
    seen: set[str] = set()
    for index, (raw_submission, raw_manifest) in enumerate(
        zip(submitted_cases, manifest_cases, strict=True),
        start=1,
    ):
        submitted = _require_mapping(raw_submission, f"review submission case {index}")
        manifest_case = _require_mapping(raw_manifest, f"manifest case {index}")
        _require_exact_keys(
            submitted,
            {"review_case_id", "truth"},
            f"review submission case {index}",
        )
        review_case_id = _require_text(submitted, "review_case_id")
        if (
            review_case_id != _review_case_id(manifest_sha256, index)
            or review_case_id in seen
        ):
            raise InventoryV3ProtocolV2Error("review submission order differs")
        seen.add(review_case_id)
        truth = _validate_reviewer_truth_fields(
            _require_mapping(submitted.get("truth"), "review truth"),
            label=f"review truth {index}",
        )
        frame_region = _require_mapping(
            manifest_case.get("frame_region"), "manifest frame region"
        )
        truths.append(
            {
                "case_id": manifest_case.get("case_id"),
                "decision": truth["decision"],
                "drag_visible": truth["drag_visible"],
                "frame_region_sha256": frame_region.get("sha256"),
                "hover_visible": truth["hover_visible"],
                "occupied_slots": truth["occupied_slots"],
                "ordinary_iron_only": truth["ordinary_iron_only"],
                "quantity_text_visible": truth["quantity_text_visible"],
                "review_note": truth["review_note"],
                "selected_item_visible": truth["selected_item_visible"],
                "visibility": truth["visibility"],
            }
        )
    return {
        "activation_allowed": False,
        "campaign_id": manifest.get("campaign_id"),
        "campaign_manifest_sha256": manifest_sha256,
        "cases": truths,
        "dataset_id": manifest.get("dataset_id"),
        "reviewed_at_utc": reviewed_at_text,
        "reviewer": reviewer,
        "schema": _REVIEW_SCHEMA,
        "truth_source": "independent-human-review",
    }


def publish_reviewed_package(
    repository_root: Path,
    *,
    expected_head: str,
    attempt_base: Path | None = None,
    access_hook: AccessHook | None = None,
) -> ReviewedPackage:
    """Ingest the fixed blinded submission into one evaluator-ready package."""

    protocol = verify_protocol_v2_repository(repository_root, expected_head=expected_head)
    authorization = verify_live_authorization(protocol, access_hook=access_hook)
    source = preflight_source_metadata(
        protocol,
        authorization,
        attempt_base=attempt_base,
        access_hook=access_hook,
    )
    _assert_disjoint_paths(source.paths)
    _recheck_source_metadata(source)
    _assert_workspace_children(
        source.paths,
        {_WORKSPACE_ACQUISITION_DIR, _WORKSPACE_REVIEW_INTAKE_DIR},
    )
    prior_lineage = _preflight_review_pipeline_lineage(source, require_reviewed=False)
    metadata_bindings = {
        key: prior_lineage[key]
        for key in (
            "acquisition_package_tree_sha256",
            "review_intake_package_tree_sha256",
            "review_submission_package_tree_sha256",
        )
    }
    operation = "publish-reviewed-package"
    _reserve_attempt(source.paths, protocol, operation, metadata_bindings)
    try:
        reviewed_root = source.paths.reviewed_package_root
        reviewed_root.mkdir(exist_ok=False)
        manifest, _, manifest_sha, acquisition_tree_sha, acquisition_snapshot = _load_acquisition(
            source, access_hook=access_hook
        )
        _template, intake_tree_sha, intake_snapshot = _load_review_intake(
            source,
            expected_manifest_sha256=manifest_sha,
            expected_acquisition_tree_sha256=acquisition_tree_sha,
            submission_state="complete",
        )
        submission_root = source.paths.review_intake_root / "submission"
        _emit(
            access_hook,
            "sensitive",
            "reviewer_truth_opened",
            submission_root / _REVIEW_SUBMISSION_NAME,
        )
        submission_snapshot = _read_verified_tree(
            submission_root,
            _review_submission_roles(),
            expected_tree_sha256=metadata_bindings["review_submission_package_tree_sha256"],
        )
        submission, submission_payload = _read_canonical_json(
            submission_root / _REVIEW_SUBMISSION_NAME,
            schema=_REVIEW_SUBMISSION_SCHEMA,
            label="reviewer submission",
        )
        review = _project_reviewer_truth(
            manifest,
            manifest_sha,
            submission,
            expected_acquisition_tree_sha256=acquisition_tree_sha,
            expected_review_intake_tree_sha256=intake_tree_sha,
        )
        reviewer = _require_actor(review, "reviewer")
        operator = _require_actor(manifest, "operator")
        acquisition_snapshot.recheck()
        intake_snapshot.recheck()
        submission_snapshot.recheck()
        acquisition_roles = {
            entry.path: entry.role for entry in acquisition_snapshot.entries
        }
        for relative in acquisition_roles:
            origin = _owned_path(
                source.paths.acquisition_root, relative, "acquisition package file"
            )
            destination = _owned_path(
                reviewed_root, relative, "reviewed package file"
            )
            _copy_file_exclusive(origin, destination, "acquisition package file")
        review_sha = _write_canonical_exclusive(reviewed_root / _REVIEWER_TRUTH_NAME, review)
        package = {
            "activation_allowed": False,
            "campaign_manifest": {
                "path": _CAMPAIGN_MANIFEST_NAME,
                "sha256": manifest_sha,
            },
            "dataset_role": "independent-validation-only",
            "preregistration_sha256": PROTOCOL_V1_PREREGISTRATION_SHA256,
            "prototype_eligible": False,
            "reviewer_truth": {
                "path": _REVIEWER_TRUTH_NAME,
                "sha256": review_sha,
            },
            "schema": _VALIDATION_PACKAGE_SCHEMA,
            "training_allowed": False,
        }
        package_sha = _write_canonical_exclusive(reviewed_root / _VALIDATION_PACKAGE_NAME, package)
        reviewed_record = {
            "acquisition_package_tree_sha256": acquisition_tree_sha,
            "activation_allowed": False,
            "authorization_id": authorization.authorization_id,
            "campaign_id": manifest.get("campaign_id"),
            "campaign_manifest_sha256": manifest_sha,
            "dataset_id": manifest.get("dataset_id"),
            "operator": operator,
            "promotion_allowed": False,
            "protocol_lock_git_commit_sha": protocol.lock_commit_sha,
            "protocol_lock_sha256": protocol.lock_sha256,
            "review_intake_tree_sha256": intake_tree_sha,
            "review_submission_sha256": _sha256(submission_payload),
            "reviewed_at_utc": submission.get("reviewed_at_utc"),
            "reviewer": reviewer,
            "reviewer_truth_sha256": review_sha,
            "schema": _REVIEWED_PACKAGE_SCHEMA,
            "status": "reviewed-evaluator-ready",
            "training_allowed": False,
            "validation_package_sha256": package_sha,
        }
        _write_canonical_exclusive(
            reviewed_root / "protocol-v2-reviewed-package.json", reviewed_record
        )
        reviewed_roles = _reviewed_package_roles()
        reviewed_tree_sha, reviewed_snapshot = _write_tree_document(reviewed_root, reviewed_roles)
        _require_copied_snapshot_entries(
            acquisition_snapshot,
            reviewed_snapshot,
            label="reviewed package acquisition copy",
        )
        acquisition_snapshot.recheck()
        intake_snapshot.recheck()
        submission_snapshot.recheck()
        reviewed_snapshot.recheck()
        _record_attempt_terminal(
            source.paths,
            protocol,
            operation,
            status="passed-terminal",
            contract_id="REVIEWED_PACKAGE_FINALIZED",
            output_sha256=reviewed_tree_sha,
        )
        return ReviewedPackage(
            root=reviewed_root,
            campaign_id=_require_text(manifest, "campaign_id"),
            dataset_id=_require_text(manifest, "dataset_id"),
            operator=operator,
            reviewer=reviewer,
            package_sha256=package_sha,
            campaign_manifest_sha256=manifest_sha,
            reviewer_truth_sha256=review_sha,
            package_tree_sha256=reviewed_tree_sha,
        )
    except BaseException as exc:
        _attempt_failed_best_effort(
            source.paths,
            protocol,
            authorization,
            operation,
            "CASE_EVIDENCE_INELIGIBLE",
            error_type=type(exc).__name__,
        )
        raise


def _failure_contract_for_report(report: object) -> str:
    cases = getattr(report, "cases", ())
    by_stage = {getattr(item, "planned_stage_id", ""): item for item in cases}
    contracts = {
        "empty": "C1_EMPTY_ZERO_CONFORMANCE_FAILURE",
        "early-partial": "C2_EARLY_PARTIAL_CONFORMANCE_FAILURE",
        "mid-partial": "C3_MID_PARTIAL_ORDER_CONFORMANCE_FAILURE",
        "near-full": "C4_NEAR_FULL_BOUND_CONFORMANCE_FAILURE",
        "full": "C5_FULL_28_CONFORMANCE_FAILURE",
        "wrong-tab": "C6_WRONG_TAB_UNKNOWN_SAFETY_FAILURE",
        "row-obstruction": "C7_ROW_OBSTRUCTION_UNKNOWN_SAFETY_FAILURE",
    }
    for stage in REQUIRED_STAGES:
        item = by_stage.get(stage)
        if item is not None and getattr(item, "passed", False) is not True:
            return contracts[stage]
    return "CAMPAIGN_TERMINAL_FAILURE"


def _write_public_failure_receipt(
    result_root: Path,
    authorization: LiveAuthorizationBinding,
    contract_id: str,
) -> str:
    from .privacy import (
        FailureContractId,
        PreissuedOpaqueReceipt,
        build_permanent_failure_projection,
    )

    projection = build_permanent_failure_projection(
        PreissuedOpaqueReceipt(authorization.opaque_receipt_id),
        FailureContractId(contract_id),
    )
    return _write_canonical_exclusive(
        result_root / "public-failure-receipt.json", projection.to_dict()
    )


def evaluate_locked_protocol_v2(
    repository_root: Path,
    *,
    expected_head: str,
    attempt_base: Path | None = None,
    access_hook: AccessHook | None = None,
) -> TerminalEvaluation:
    """Run the single frozen evaluator once after metadata-only preflight."""

    protocol = verify_protocol_v2_repository(repository_root, expected_head=expected_head)
    authorization = verify_live_authorization(protocol, access_hook=access_hook)
    source = preflight_source_metadata(
        protocol,
        authorization,
        attempt_base=attempt_base,
        access_hook=access_hook,
    )
    _assert_disjoint_paths(source.paths)
    _recheck_source_metadata(source)
    _assert_workspace_children(
        source.paths,
        {
            _WORKSPACE_ACQUISITION_DIR,
            _WORKSPACE_REVIEW_INTAKE_DIR,
            _WORKSPACE_REVIEWED_PACKAGE_DIR,
        },
    )
    reviewed_root = source.paths.reviewed_package_root
    prior_lineage = _preflight_review_pipeline_lineage(
        source, require_reviewed=True
    )

    acquisition_tree, acquisition_tree_payload = _preflight_tree_metadata_only(
        source.paths.acquisition_root, _acquisition_roles()
    )
    acquisition_tree_sha = _sha256(acquisition_tree_payload)
    if (
        acquisition_tree_sha
        != prior_lineage["acquisition_package_tree_sha256"]
    ):
        raise InventoryV3ProtocolV2Error("acquisition package lineage changed")
    (
        original_manifest,
        _original_record,
        original_manifest_payload,
    ) = _preflight_acquisition_semantics(
        source,
        source.paths.acquisition_root,
        acquisition_tree,
        acquisition_tree_payload,
    )

    _emit(
        access_hook,
        "preflight",
        "reviewed_package_tree_metadata",
        reviewed_root / _PACKAGE_TREE_NAME,
    )
    reviewed_tree, reviewed_tree_payload = _preflight_tree_metadata_only(
        reviewed_root, _reviewed_package_roles()
    )
    reviewed_tree_sha = _sha256(reviewed_tree_payload)
    if (
        prior_lineage.get("reviewed_package_tree_sha256")
        != reviewed_tree_sha
    ):
        raise InventoryV3ProtocolV2Error("reviewed package lineage changed")
    _require_tree_entry_subset_equal(
        acquisition_tree,
        reviewed_tree,
        _acquisition_roles(),
        "reviewed acquisition tree copy",
    )
    manifest, _reviewed_acquisition_record, manifest_payload = (
        _preflight_acquisition_semantics(
            source,
            reviewed_root,
            reviewed_tree,
            reviewed_tree_payload,
        )
    )
    manifest_sha = _sha256(manifest_payload)
    if (
        manifest_sha != prior_lineage["campaign_manifest_sha256"]
        or original_manifest_payload != manifest_payload
        or original_manifest != manifest
    ):
        raise InventoryV3ProtocolV2Error("reviewed campaign manifest lineage changed")

    reviewed_entries = _tree_entries_from_document(reviewed_tree)
    package, package_payload = _read_canonical_json(
        reviewed_root / _VALIDATION_PACKAGE_NAME,
        schema=_VALIDATION_PACKAGE_SCHEMA,
        label="validation package metadata",
    )
    reviewed_record, reviewed_record_payload = _read_canonical_json(
        reviewed_root / "protocol-v2-reviewed-package.json",
        schema=_REVIEWED_PACKAGE_SCHEMA,
        label="V2 reviewed package metadata",
    )
    _require_tree_bound_payload(
        reviewed_entries,
        _VALIDATION_PACKAGE_NAME,
        package_payload,
        "validation package metadata",
    )
    _require_tree_bound_payload(
        reviewed_entries,
        "protocol-v2-reviewed-package.json",
        reviewed_record_payload,
        "V2 reviewed package metadata",
    )
    for relative, label in (
        (
            f"{_VALIDATION_PACKAGE_NAME}.sha256",
            "validation package sidecar",
        ),
        (
            "protocol-v2-reviewed-package.json.sha256",
            "V2 reviewed package sidecar",
        ),
    ):
        _read_tree_bound_payload(
            reviewed_root,
            reviewed_entries,
            relative,
            label,
        )
    _require_exact_keys(
        package,
        {
            "activation_allowed",
            "campaign_manifest",
            "dataset_role",
            "preregistration_sha256",
            "prototype_eligible",
            "reviewer_truth",
            "schema",
            "training_allowed",
        },
        "validation package metadata",
    )
    _require_exact_keys(
        reviewed_record,
        {
            "acquisition_package_tree_sha256",
            "activation_allowed",
            "authorization_id",
            "campaign_id",
            "campaign_manifest_sha256",
            "dataset_id",
            "operator",
            "promotion_allowed",
            "protocol_lock_git_commit_sha",
            "protocol_lock_sha256",
            "review_intake_tree_sha256",
            "review_submission_sha256",
            "reviewed_at_utc",
            "reviewer",
            "reviewer_truth_sha256",
            "schema",
            "status",
            "training_allowed",
            "validation_package_sha256",
        },
        "V2 reviewed package metadata",
    )
    campaign_ref = _require_mapping(
        package.get("campaign_manifest"), "validation campaign ref"
    )
    review_ref = _require_mapping(
        package.get("reviewer_truth"), "validation review ref"
    )
    _require_exact_keys(
        campaign_ref, {"path", "sha256"}, "validation campaign ref"
    )
    _require_exact_keys(
        review_ref, {"path", "sha256"}, "validation review ref"
    )
    review_entry = reviewed_entries.get(_REVIEWER_TRUTH_NAME)
    if review_entry is None:
        raise InventoryV3ProtocolV2Error(
            "reviewer truth is absent from reviewed package tree"
        )
    review_sidecar = _read_tree_bound_payload(
        reviewed_root,
        reviewed_entries,
        f"{_REVIEWER_TRUTH_NAME}.sha256",
        "reviewer truth sidecar",
    )
    if review_sidecar != (
        f"{review_entry[1]}  {_REVIEWER_TRUTH_NAME}\n".encode("ascii")
    ):
        raise InventoryV3ProtocolV2Error("reviewer truth sidecar binding differs")

    package_sha = _sha256(package_payload)
    operator = _require_actor(reviewed_record, "operator")
    reviewer = _require_actor(reviewed_record, "reviewer")
    reviewed_at_text = _require_text(reviewed_record, "reviewed_at_utc")
    reviewed_at = _parse_utc(reviewed_at_text, "reviewed_at_utc")
    if (
        reviewed_at_text != _format_utc(reviewed_at)
        or reviewed_at
        <= _parse_utc(manifest.get("finalized_at_utc"), "finalized_at_utc")
        or reviewed_at >= datetime.now(UTC)
    ):
        raise InventoryV3ProtocolV2Error(
            "review chronology must precede evaluator invocation"
        )
    if (
        package.get("activation_allowed") is not False
        or package.get("training_allowed") is not False
        or package.get("prototype_eligible") is not False
        or package.get("dataset_role") != "independent-validation-only"
        or package.get("preregistration_sha256")
        != PROTOCOL_V1_PREREGISTRATION_SHA256
        or campaign_ref
        != {
            "path": _CAMPAIGN_MANIFEST_NAME,
            "sha256": manifest_sha,
        }
        or review_ref
        != {
            "path": _REVIEWER_TRUTH_NAME,
            "sha256": review_entry[1],
        }
        or reviewer == operator
        or operator != manifest.get("operator")
        or reviewed_record.get("activation_allowed") is not False
        or reviewed_record.get("promotion_allowed") is not False
        or reviewed_record.get("training_allowed") is not False
        or reviewed_record.get("status") != "reviewed-evaluator-ready"
        or reviewed_record.get("authorization_id")
        != authorization.authorization_id
        or reviewed_record.get("protocol_lock_git_commit_sha")
        != protocol.lock_commit_sha
        or reviewed_record.get("protocol_lock_sha256")
        != protocol.lock_sha256
        or reviewed_record.get("campaign_manifest_sha256") != manifest_sha
        or reviewed_record.get("validation_package_sha256") != package_sha
        or reviewed_record.get("reviewer_truth_sha256") != review_entry[1]
        or reviewed_record.get("review_submission_sha256")
        != prior_lineage["review_submission_sha256"]
        or reviewed_record.get("acquisition_package_tree_sha256")
        != acquisition_tree_sha
        or reviewed_record.get("review_intake_tree_sha256")
        != prior_lineage["review_intake_package_tree_sha256"]
        or reviewed_record.get("campaign_id") != manifest.get("campaign_id")
        or reviewed_record.get("dataset_id") != manifest.get("dataset_id")
    ):
        raise InventoryV3ProtocolV2Error(
            "evaluator package metadata binding differs"
        )

    _require_non_development_dataset_identity(manifest.get("dataset_id"))
    development_identities = _frozen_development_identity_sets(protocol)
    _require_manifest_development_identity_disjoint(
        manifest,
        development_identities,
    )
    _recheck_source_metadata(source)
    current_reviewed_tree, current_reviewed_tree_payload = (
        _preflight_tree_metadata_only(
            reviewed_root,
            _reviewed_package_roles(),
        )
    )
    if (
        current_reviewed_tree != reviewed_tree
        or current_reviewed_tree_payload != reviewed_tree_payload
    ):
        raise InventoryV3ProtocolV2Error(
            "reviewed package tree changed during evaluator preflight"
        )
    _emit(access_hook, "preflight", "evaluator_preflight_complete", None)
    operation = "evaluate-locked-candidate"
    _reserve_attempt(
        source.paths,
        protocol,
        operation,
        {
            "campaign_manifest_sha256": manifest_sha,
            "opaque_receipt_id": authorization.opaque_receipt_id,
            "reviewed_package_tree_sha256": reviewed_tree_sha,
            "validation_package_sha256": package_sha,
        },
    )
    try:
        source.paths.result_root.parent.mkdir(parents=True, exist_ok=True)
        source.paths.result_root.mkdir(exist_ok=False)
        _emit(
            access_hook,
            "sensitive",
            "reviewer_truth_opened",
            reviewed_root / _REVIEWER_TRUTH_NAME,
        )
        submission_root = source.paths.review_intake_root / "submission"
        submission_snapshot = _read_verified_tree(
            submission_root,
            _review_submission_roles(),
            expected_tree_sha256=prior_lineage[
                "review_submission_package_tree_sha256"
            ],
        )
        submission, submission_payload = _read_canonical_json(
            submission_root / _REVIEW_SUBMISSION_NAME,
            schema=_REVIEW_SUBMISSION_SCHEMA,
            label="original reviewer submission",
        )
        if (
            _sha256(submission_payload)
            != prior_lineage["review_submission_sha256"]
        ):
            raise InventoryV3ProtocolV2Error(
                "original reviewer submission lineage changed"
            )
        review, review_payload = _read_canonical_json(
            reviewed_root / _REVIEWER_TRUTH_NAME,
            schema=_REVIEW_SCHEMA,
            label="reviewed independent truth",
        )
        _require_tree_bound_payload(
            reviewed_entries,
            _REVIEWER_TRUTH_NAME,
            review_payload,
            "reviewed independent truth",
        )
        expected_review = _project_reviewer_truth(
            manifest,
            manifest_sha,
            submission,
            expected_acquisition_tree_sha256=acquisition_tree_sha,
            expected_review_intake_tree_sha256=prior_lineage[
                "review_intake_package_tree_sha256"
            ],
        )
        if (
            review != expected_review
            or review_payload != _canonical_bytes(expected_review)
            or reviewer != expected_review["reviewer"]
            or reviewed_at_text != expected_review["reviewed_at_utc"]
        ):
            raise InventoryV3ProtocolV2Error(
                "reviewed truth is not the deterministic original submission projection"
            )
        submission_snapshot.recheck()
        _emit(
            access_hook,
            "sensitive",
            "validation_pixels_opened",
            reviewed_root,
        )
        reviewed_snapshot = _read_verified_tree(
            reviewed_root,
            _reviewed_package_roles(),
            expected_tree_sha256=reviewed_tree_sha,
        )
        from mining_automation.perception.inventory.positive_v3_independent_validation import (
            evaluate_frozen_v3_independent_validation,
        )

        report = evaluate_frozen_v3_independent_validation(
            reviewed_root,
            repository_root=protocol.repository_root,
            evaluator_git_head_sha=protocol.evaluator_head_sha,
        )
        submission_snapshot.recheck()
        reviewed_snapshot.recheck()
        _require_report_development_identity_disjoint(
            report,
            development_identities,
        )
        if report.approval is not None:
            raise InventoryV3ProtocolV2Error(
                "source approval cannot precede the terminal V2 conformance attempt"
            )
        report_sha = _write_canonical_exclusive(
            source.paths.result_root / "frozen-evaluator-private-report.json",
            report.to_dict(),
        )
        conformance_passed = bool(report.detector_conformance_passed)
        contract_id = (
            "CONFORMANCE_PASSED_APPROVAL_REQUIRED"
            if conformance_passed
            else _failure_contract_for_report(report)
        )
        terminal_status = (
            "conformance-passed-source-approval-required"
            if conformance_passed
            else "conformance-failed-permanent"
        )
        evaluated_at_value = datetime.now(UTC)
        if evaluated_at_value <= reviewed_at:
            raise InventoryV3ProtocolV2Error("terminal evaluation must follow independent review")
        evaluated_at_utc = _format_utc(evaluated_at_value)
        result_record = {
            "activation_allowed": False,
            "approval_required": conformance_passed,
            "authorization_id": authorization.authorization_id,
            "campaign_id": manifest.get("campaign_id"),
            "campaign_manifest_sha256": manifest_sha,
            "contract_id": contract_id,
            "dataset_id": manifest.get("dataset_id"),
            "detector_conformance_passed": conformance_passed,
            "evaluated_at_utc": evaluated_at_utc,
            "frozen_candidate_head_sha": FROZEN_V3_HEAD,
            "frozen_evaluator_report_sha256": report_sha,
            "opaque_receipt_id": authorization.opaque_receipt_id,
            "promotion_allowed": False,
            "protocol_lock_git_commit_sha": protocol.lock_commit_sha,
            "protocol_lock_sha256": protocol.lock_sha256,
            "protocol_source_git_commit_sha": protocol.source_commit_sha,
            "retry_allowed": False,
            "reviewed_package_tree_sha256": reviewed_tree_sha,
            "schema": "inventory-positive-v3-independent-terminal-result-v2",
            "terminal_status": terminal_status,
            "validation_package_sha256": package_sha,
        }
        result_record_sha = _write_canonical_exclusive(
            source.paths.result_root / "protocol-v2-terminal-result.json",
            result_record,
        )
        result_roles = _result_roles(conformance_passed=conformance_passed)
        if not conformance_passed:
            _write_public_failure_receipt(source.paths.result_root, authorization, contract_id)
        result_tree_sha, _ = _write_tree_document(source.paths.result_root, result_roles)
        _record_attempt_terminal(
            source.paths,
            protocol,
            operation,
            status=("passed-terminal" if conformance_passed else "failed-terminal"),
            contract_id=contract_id,
            output_sha256=result_tree_sha,
        )
        return TerminalEvaluation(
            root=source.paths.result_root,
            terminal_status=terminal_status,
            detector_conformance_passed=conformance_passed,
            approval_required=conformance_passed,
            frozen_evaluator_report_sha256=report_sha,
            result_record_sha256=result_record_sha,
            result_tree_sha256=result_tree_sha,
        )
    except BaseException as exc:
        _attempt_failed_best_effort(
            source.paths,
            protocol,
            authorization,
            operation,
            "ATTEMPT_INTEGRITY_FAILURE",
            error_type=type(exc).__name__,
        )
        raise


def prepare_approval_request(
    repository_root: Path,
    *,
    expected_head: str,
    proposed_approver: str,
    proposed_approved_at_utc: str,
    attempt_base: Path | None = None,
    access_hook: AccessHook | None = None,
) -> Mapping[str, object]:
    """Generate a result-bound proposal without mutating any approval registry."""

    protocol = verify_protocol_v2_repository(repository_root, expected_head=expected_head)
    authorization = verify_live_authorization(protocol, access_hook=access_hook)
    source = preflight_source_metadata(
        protocol,
        authorization,
        attempt_base=attempt_base,
        access_hook=access_hook,
    )
    _assert_disjoint_paths(source.paths)
    _recheck_source_metadata(source)
    _assert_workspace_children(
        source.paths,
        {
            _WORKSPACE_ACQUISITION_DIR,
            _WORKSPACE_REVIEW_INTAKE_DIR,
            _WORKSPACE_REVIEWED_PACKAGE_DIR,
        },
    )
    prior_lineage = _preflight_review_pipeline_lineage(source, require_reviewed=True)
    _, reviewed_tree_payload = _preflight_tree_metadata_only(
        source.paths.reviewed_package_root, _reviewed_package_roles()
    )
    _, result_tree_payload = _preflight_tree_metadata_only(
        source.paths.result_root, _result_roles(conformance_passed=True)
    )
    reviewed_record, _ = _read_canonical_json(
        source.paths.reviewed_package_root / "protocol-v2-reviewed-package.json",
        schema=_REVIEWED_PACKAGE_SCHEMA,
        label="reviewed package record",
    )
    reviewed_acquisition, _ = _read_canonical_json(
        source.paths.reviewed_package_root / "protocol-v2-acquisition.json",
        schema=_ACQUISITION_SCHEMA,
        label="reviewed acquisition record",
    )
    result_record, result_record_payload = _read_canonical_json(
        source.paths.result_root / "protocol-v2-terminal-result.json",
        schema="inventory-positive-v3-independent-terminal-result-v2",
        label="terminal evaluator result",
    )
    approval_campaign_id = _require_text(result_record, "campaign_id")
    (
        _,
        expected_session_payload,
        _,
        expected_seal_payload,
    ) = _evaluator_compatible_source_documents(source, approval_campaign_id)
    if (
        result_record.get("detector_conformance_passed") is not True
        or result_record.get("terminal_status") != "conformance-passed-source-approval-required"
        or result_record.get("approval_required") is not True
        or result_record.get("retry_allowed") is not False
        or result_record.get("activation_allowed") is not False
        or result_record.get("promotion_allowed") is not False
        or result_record.get("authorization_id") != authorization.authorization_id
        or result_record.get("protocol_lock_git_commit_sha") != protocol.lock_commit_sha
        or result_record.get("protocol_lock_sha256") != protocol.lock_sha256
        or result_record.get("reviewed_package_tree_sha256") != _sha256(reviewed_tree_payload)
        or prior_lineage.get("reviewed_package_tree_sha256") != _sha256(reviewed_tree_payload)
        or reviewed_acquisition.get("source_identity_bridge") != _SOURCE_IDENTITY_BRIDGE
        or reviewed_acquisition.get("original_source_session_report_sha256")
        != _sha256(source.session_payload)
        or reviewed_acquisition.get("original_source_completion_seal_sha256")
        != _sha256(source.completion_payload)
        or reviewed_acquisition.get("source_session_report_sha256")
        != _sha256(expected_session_payload)
        or reviewed_acquisition.get("source_completion_seal_sha256")
        != _sha256(expected_seal_payload)
    ):
        raise InventoryV3ProtocolV2Error(
            "only a terminal conformance PASS may produce an approval request"
        )
    evaluator_terminal_at = _verify_successful_operation(
        source.paths,
        protocol,
        "evaluate-locked-candidate",
        expected_binding={
            "campaign_manifest_sha256": result_record.get("campaign_manifest_sha256"),
            "opaque_receipt_id": authorization.opaque_receipt_id,
            "reviewed_package_tree_sha256": _sha256(reviewed_tree_payload),
            "validation_package_sha256": result_record.get("validation_package_sha256"),
        },
        expected_contract_id="CONFORMANCE_PASSED_APPROVAL_REQUIRED",
        expected_output_sha256=_sha256(result_tree_payload),
    )
    operator = _require_actor(reviewed_record, "operator")
    reviewer = _require_actor(reviewed_record, "reviewer")
    approver_container: Mapping[str, object] = {"proposed_approver": proposed_approver}
    approver = _require_actor(approver_container, "proposed_approver")
    if len({operator, reviewer, approver}) != 3:
        raise InventoryV3ProtocolV2Error(
            "operator, reviewer, and approver must be pairwise distinct"
        )
    approved_at = _parse_utc(proposed_approved_at_utc, "proposed_approved_at_utc")
    evaluated_at = _parse_utc(result_record.get("evaluated_at_utc"), "evaluated_at_utc")
    reviewed_at = _parse_utc(reviewed_record.get("reviewed_at_utc"), "reviewed_at_utc")
    if approved_at <= max(evaluated_at, reviewed_at, evaluator_terminal_at):
        raise InventoryV3ProtocolV2Error(
            "source approval must follow independent review and terminal conformance"
        )
    if approved_at > datetime.now(UTC):
        raise InventoryV3ProtocolV2Error(
            "source approval proposal timestamp cannot be in the future"
        )
    operation = "prepare-approval-request"
    _reserve_attempt(
        source.paths,
        protocol,
        operation,
        {
            "result_package_tree_sha256": _sha256(result_tree_payload),
            "terminal_result_sha256": _sha256(result_record_payload),
        },
    )
    try:
        registry_before = _verify_approval_registry_absent(
            protocol,
            access_hook=access_hook,
        )
        registry_value = _require_mapping(
            json.loads(registry_before),
            "source approval registry",
        )
        entries = _require_list(registry_value, "entries")
        source.paths.approval_request_root.mkdir(exist_ok=False)
        reviewed_snapshot = _read_verified_tree(
            source.paths.reviewed_package_root,
            _reviewed_package_roles(),
            expected_tree_sha256=_sha256(reviewed_tree_payload),
        )
        result_snapshot = _read_verified_tree(
            source.paths.result_root,
            _result_roles(conformance_passed=True),
            expected_tree_sha256=_sha256(result_tree_payload),
        )
        proposed: dict[str, object] = {
            "approved_at_utc": proposed_approved_at_utc,
            "approver": approver,
            "campaign_id": result_record.get("campaign_id"),
            "campaign_manifest_sha256": result_record.get("campaign_manifest_sha256"),
            "dataset_id": result_record.get("dataset_id"),
            "operator": operator,
            "package_sha256": reviewed_record.get("validation_package_sha256"),
            "reviewer": reviewer,
            "reviewer_truth_sha256": reviewed_record.get("reviewer_truth_sha256"),
            "source_completion_seal_sha256": reviewed_acquisition.get(
                "source_completion_seal_sha256"
            ),
            "source_session_report_sha256": reviewed_acquisition.get(
                "source_session_report_sha256"
            ),
            "status": "approved-for-independent-validation-conformance",
        }
        approval_id = (
            "inventory-positive-v3-approval-" + _sha256(_canonical_data_bytes(proposed))[:24]
        )
        proposed["approval_id"] = approval_id
        proposed_registry = copy.deepcopy(dict(registry_value))
        proposed_registry["entries"] = [*entries, proposed]
        proposed_registry_payload = _canonical_bytes(proposed_registry)
        proposed_registry_sha = _sha256(proposed_registry_payload)
        proposed_sidecar_ascii = f"{proposed_registry_sha}  {_APPROVAL_REGISTRY_PATH.name}\n"
        request = {
            "activation_allowed": False,
            "approval_registry_modified": False,
            "promotion_allowed": False,
            "proposed_approval": proposed,
            "proposed_source_files": [
                {
                    "content": proposed_registry,
                    "path": _APPROVAL_REGISTRY_PATH.as_posix(),
                    "sha256": proposed_registry_sha,
                },
                {
                    "content_ascii": proposed_sidecar_ascii,
                    "path": _APPROVAL_REGISTRY_SIDECAR_PATH.as_posix(),
                    "sha256": _sha256(proposed_sidecar_ascii.encode("ascii")),
                },
            ],
            "result_binding": {
                "authorization_id": authorization.authorization_id,
                "frozen_evaluator_report_sha256": result_record.get(
                    "frozen_evaluator_report_sha256"
                ),
                "protocol_lock_git_commit_sha": protocol.lock_commit_sha,
                "protocol_lock_sha256": protocol.lock_sha256,
                "result_package_tree_sha256": _sha256(result_tree_payload),
                "reviewed_package_tree_sha256": _sha256(reviewed_tree_payload),
                "terminal_result_sha256": _sha256(result_record_payload),
            },
            "schema": _APPROVAL_REQUEST_SCHEMA,
            "source_action_required": True,
            "status": "request-only-not-approved",
        }
        request_sha = _write_canonical_exclusive(
            source.paths.approval_request_root / "approval-request.json", request
        )
        request_tree_sha, _ = _write_tree_document(
            source.paths.approval_request_root,
            _approval_request_roles(),
        )
        reviewed_snapshot.recheck()
        result_snapshot.recheck()
        registry_after = _verify_approval_registry_absent(
            protocol,
            access_hook=access_hook,
        )
        if registry_after != registry_before:
            raise InventoryV3ProtocolV2Error(
                "approval request tooling modified the source approval registry"
            )
        _record_attempt_terminal(
            source.paths,
            protocol,
            operation,
            status="passed-terminal",
            contract_id="SOURCE_APPROVAL_REQUEST_PREPARED_NOT_APPROVED",
            output_sha256=request_tree_sha,
        )
        return {
            "approval_id": approval_id,
            "approval_request_sha256": request_sha,
            "approval_request_tree_sha256": request_tree_sha,
            "proposed_registry_sha256": proposed_registry_sha,
            "path": str(source.paths.approval_request_root / "approval-request.json"),
        }
    except BaseException as exc:
        _attempt_failed_best_effort(
            source.paths,
            protocol,
            authorization,
            operation,
            "ATTEMPT_INTEGRITY_FAILURE",
            error_type=type(exc).__name__,
        )
        raise
