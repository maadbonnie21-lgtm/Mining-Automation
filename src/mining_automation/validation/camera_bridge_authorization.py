"""Source-owned one-shot authorization for the Issue #31 R2 bridge sample.

This module owns only durable authorization accounting.  It has no capture or
input dependency and cannot grant camera authority by itself.  The live
composition root must still retain its source-literal input gate and all of its
existing provenance, readiness, focus, geometry, pointer, lease, and freshness
checks.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from mining_automation.validation.camera_bridge_capture import (
    CAMERA_BRIDGE_CAPTURE_HOLD_SECONDS,
    CAMERA_BRIDGE_CAPTURE_ID,
    CAMERA_BRIDGE_CAPTURE_MAXIMUM_PHYSICAL_PRIMITIVES,
)
from mining_automation.validation.camera_bridge_planner import (
    FROZEN_ENDPOINT_OBJECTIVE_ID,
    FROZEN_ENDPOINT_SOURCE_SHA256,
)
from mining_automation.validation.camera_plan import (
    EXPECTED_CLIENT_HEIGHT,
    EXPECTED_CLIENT_WIDTH,
    REVIEWED_CAMERA_WHEEL_POINT,
)

CAMERA_BRIDGE_AUTHORIZATION_ID: Final[str] = (
    "issue31-r2-one-shot-bridge-authorization"
)
CAMERA_BRIDGE_AUTHORIZATION_VERSION: Final[str] = "2.2.0"
CAMERA_BRIDGE_AUTHORIZATION_CAMPAIGN_ID: Final[str] = (
    "issue31-r2-north-up-p610-y043-reset-right-0043-v1"
)

_AUTHORIZATION_SCHEMA_VERSION: Final[int] = 1
_AUTHORIZATION_STATE: Final[str] = "consumed_at_final_pre_input_seam"
_AUTHORIZATION_ACTION_FAMILY: Final[str] = "north-up-p610-y043-reset"
_AUTHORIZATION_KEY: Final[str] = "right"
_AUTHORIZATION_TARGET_TITLE_SUBSTRING: Final[str] = "runelite"
_AUTHORIZATION_CAMERA_ADAPTER: Final[str] = (
    "mining_automation.validation.windows_camera.WindowsCameraControl"
)
_AUTHORIZATION_INPUT_LEASE: Final[str] = (
    "mining_automation.validation.camera_input_lease.WindowsCameraInputLease"
)
_AUTHORIZATION_NAMESPACE: Final[Path] = Path(
    "mining-automation-authorizations"
) / "issue31-camera-bridge"
_AUTHORIZATION_SENTINEL_NAME: Final[str] = (
    f"{CAMERA_BRIDGE_AUTHORIZATION_CAMPAIGN_ID}.consumed.json"
)
_COMPLETION_SEAL_NAME: Final[str] = (
    f"{CAMERA_BRIDGE_AUTHORIZATION_CAMPAIGN_ID}.completed.json"
)
_COMPLETION_PENDING_NAME: Final[str] = (
    f"{CAMERA_BRIDGE_AUTHORIZATION_CAMPAIGN_ID}.completion-pending.json"
)
_SHA256_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")
_HEAD_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{40}")


class CameraBridgeAuthorizationError(RuntimeError):
    """Raised when the fixed authorization namespace cannot be authenticated."""


class CameraBridgeAuthorizationConsumedError(CameraBridgeAuthorizationError):
    """Raised whenever the one-shot campaign sentinel already exists."""


@dataclass(frozen=True, slots=True)
class CameraBridgeAuthorizationEvidence:
    """Authenticated dynamic evidence recorded by, but not authorizing, R2.2."""

    r1_report_sha256: str
    r2_report_sha256: str
    north_report_sha256: str
    north_post_sha256: str
    commit_sha256: str
    target_hwnd: int
    target_process_id: int
    target_thread_id: int
    target_class_name: str
    target_title_sha256: str

    def __post_init__(self) -> None:
        for field_name in (
            "r1_report_sha256",
            "r2_report_sha256",
            "north_report_sha256",
            "north_post_sha256",
            "commit_sha256",
            "target_title_sha256",
        ):
            if _SHA256_PATTERN.fullmatch(getattr(self, field_name)) is None:
                raise CameraBridgeAuthorizationError(
                    f"{field_name} must be a lowercase SHA-256"
                )
        for field_name in ("target_hwnd", "target_process_id", "target_thread_id"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or value <= 0:
                raise CameraBridgeAuthorizationError(f"{field_name} must be positive")
        if (
            not self.target_class_name
            or self.target_class_name != self.target_class_name.strip()
            or any(ord(character) < 32 for character in self.target_class_name)
        ):
            raise CameraBridgeAuthorizationError(
                "target_class_name must be non-empty printable text"
            )

    def as_dict(self) -> dict[str, object]:
        return {
            "r1_report_sha256": self.r1_report_sha256,
            "r2_report_sha256": self.r2_report_sha256,
            "north_report_sha256": self.north_report_sha256,
            "north_post_sha256": self.north_post_sha256,
            "commit_sha256": self.commit_sha256,
            "target_hwnd": self.target_hwnd,
            "target_process_id": self.target_process_id,
            "target_thread_id": self.target_thread_id,
            "target_class_name": self.target_class_name,
            "target_title_sha256": self.target_title_sha256,
        }


@dataclass(frozen=True, slots=True)
class CameraBridgeAuthorizationReservation:
    """Authenticated receipt for the permanently consumed campaign slot."""

    git_head_sha: str
    common_git_dir: Path
    sentinel_path: Path
    sentinel_sha256: str
    evidence: CameraBridgeAuthorizationEvidence

    def as_dict(self) -> dict[str, object]:
        """Return canonical report evidence without exposing an absolute path."""

        relative_path = self.sentinel_path.relative_to(self.common_git_dir)
        return {
            **_authorization_payload(self.git_head_sha, self.evidence),
            "sentinel_relative_to_common_git_dir": relative_path.as_posix(),
            "sentinel_sha256": self.sentinel_sha256,
            "source_owned_namespace": True,
            "caller_can_select_campaign": False,
            "caller_can_select_action_or_target": False,
            "alternate_output_or_case_prefix_can_bypass": False,
            "second_invocation_can_send_input": False,
        }


@dataclass(frozen=True, slots=True)
class CameraBridgeCompletionEvidence:
    """Hashes binding the complete post-input capture transaction."""

    authorization_sentinel_sha256: str
    capture_report_sha256: str
    receipt_sha256: str
    stage_chain_sha256: str
    commit_sha256: str
    post_sha256: str
    pointer_mapping_sha256: str
    registrations_sha256: str
    closure_sha256: str

    def __post_init__(self) -> None:
        for field_name in (
            "authorization_sentinel_sha256",
            "capture_report_sha256",
            "receipt_sha256",
            "stage_chain_sha256",
            "commit_sha256",
            "post_sha256",
            "pointer_mapping_sha256",
            "registrations_sha256",
            "closure_sha256",
        ):
            if _SHA256_PATTERN.fullmatch(getattr(self, field_name)) is None:
                raise CameraBridgeAuthorizationError(
                    f"{field_name} must be a lowercase SHA-256"
                )

    def as_dict(self) -> dict[str, str]:
        return {
            "authorization_sentinel_sha256": self.authorization_sentinel_sha256,
            "capture_report_sha256": self.capture_report_sha256,
            "receipt_sha256": self.receipt_sha256,
            "stage_chain_sha256": self.stage_chain_sha256,
            "commit_sha256": self.commit_sha256,
            "post_sha256": self.post_sha256,
            "pointer_mapping_sha256": self.pointer_mapping_sha256,
            "registrations_sha256": self.registrations_sha256,
            "closure_sha256": self.closure_sha256,
        }


@dataclass(frozen=True, slots=True)
class CameraBridgeCompletionSeal:
    """Authenticated receipt for a fully sealed bridge capture report."""

    git_head_sha: str
    common_git_dir: Path
    seal_path: Path
    seal_sha256: str
    evidence: CameraBridgeCompletionEvidence

    def as_dict(self) -> dict[str, object]:
        relative_path = self.seal_path.relative_to(self.common_git_dir)
        return {
            **_completion_payload(self.git_head_sha, self.evidence),
            "seal_relative_to_common_git_dir": relative_path.as_posix(),
            "seal_sha256": self.seal_sha256,
            "source_owned_namespace": True,
        }


def _git_directory_from_repository_root(repository_root: Path) -> Path:
    """Resolve the worktree Git directory without consulting Git/environment."""

    dot_git = repository_root.resolve() / ".git"
    if dot_git.is_dir():
        return dot_git.resolve()
    if not dot_git.is_file():
        raise CameraBridgeAuthorizationError(
            f"repository has no .git directory or worktree pointer: {repository_root}"
        )
    try:
        raw = dot_git.read_text(encoding="utf-8")
    except OSError as exc:
        raise CameraBridgeAuthorizationError(
            f"cannot read worktree Git pointer: {dot_git}"
        ) from exc
    line = raw.strip()
    if raw.count("\n") > 1 or not line.startswith("gitdir: "):
        raise CameraBridgeAuthorizationError("malformed worktree Git pointer")
    referenced = line.removeprefix("gitdir: ")
    if not referenced or "\x00" in referenced:
        raise CameraBridgeAuthorizationError("malformed worktree Git directory")
    candidate = Path(referenced)
    if not candidate.is_absolute():
        candidate = dot_git.parent / candidate
    resolved = candidate.resolve()
    if not resolved.is_dir():
        raise CameraBridgeAuthorizationError(
            f"worktree Git directory does not exist: {resolved}"
        )
    return resolved


def repository_worktree_git_dir(repository_root: Path) -> Path:
    """Return the physical worktree Git directory without Git/env indirection."""

    return _git_directory_from_repository_root(repository_root)


def repository_common_git_dir(repository_root: Path) -> Path:
    """Return the physical common Git directory, immune to Git env overrides."""

    worktree_git_dir = _git_directory_from_repository_root(repository_root)
    common_pointer = worktree_git_dir / "commondir"
    if not common_pointer.exists():
        return worktree_git_dir
    if not common_pointer.is_file():
        raise CameraBridgeAuthorizationError("Git commondir pointer is not a file")
    try:
        raw = common_pointer.read_text(encoding="utf-8")
    except OSError as exc:
        raise CameraBridgeAuthorizationError(
            f"cannot read Git commondir pointer: {common_pointer}"
        ) from exc
    referenced = raw.strip()
    if raw.count("\n") > 1 or not referenced or "\x00" in referenced:
        raise CameraBridgeAuthorizationError("malformed Git commondir pointer")
    candidate = Path(referenced)
    if not candidate.is_absolute():
        candidate = worktree_git_dir / candidate
    resolved = candidate.resolve()
    if not resolved.is_dir():
        raise CameraBridgeAuthorizationError(
            f"common Git directory does not exist: {resolved}"
        )
    return resolved


def camera_bridge_authorization_sentinel_path(repository_root: Path) -> Path:
    """Return the sole source-owned sentinel path for this campaign."""

    common_git_dir = repository_common_git_dir(repository_root)
    return _authorization_sentinel_from_common_git_dir(common_git_dir)


def _authorization_sentinel_from_common_git_dir(common_git_dir: Path) -> Path:
    return common_git_dir / _AUTHORIZATION_NAMESPACE / _AUTHORIZATION_SENTINEL_NAME


def camera_bridge_completion_seal_path(repository_root: Path) -> Path:
    """Return the sole source-owned completion-seal path for this campaign."""

    common_git_dir = repository_common_git_dir(repository_root)
    return _completion_seal_from_common_git_dir(common_git_dir)


def _completion_seal_from_common_git_dir(common_git_dir: Path) -> Path:
    return common_git_dir / _AUTHORIZATION_NAMESPACE / _COMPLETION_SEAL_NAME


def _completion_pending_from_common_git_dir(common_git_dir: Path) -> Path:
    return common_git_dir / _AUTHORIZATION_NAMESPACE / _COMPLETION_PENDING_NAME


def camera_bridge_authorization_consumed(repository_root: Path) -> bool:
    """Return whether any reservation or completion artifact consumes R2.2."""

    common_git_dir = repository_common_git_dir(repository_root)
    return os.path.lexists(
        _authorization_sentinel_from_common_git_dir(common_git_dir)
    ) or os.path.lexists(
        _completion_pending_from_common_git_dir(common_git_dir)
    ) or os.path.lexists(_completion_seal_from_common_git_dir(common_git_dir))


def _authorization_payload(
    git_head_sha: str,
    evidence: CameraBridgeAuthorizationEvidence,
) -> dict[str, object]:
    if _HEAD_PATTERN.fullmatch(git_head_sha) is None:
        raise CameraBridgeAuthorizationError(
            "bridge authorization requires an exact lowercase Git head"
        )
    return {
        "schema_version": _AUTHORIZATION_SCHEMA_VERSION,
        "authorization_id": CAMERA_BRIDGE_AUTHORIZATION_ID,
        "authorization_version": CAMERA_BRIDGE_AUTHORIZATION_VERSION,
        "campaign_id": CAMERA_BRIDGE_AUTHORIZATION_CAMPAIGN_ID,
        "state": _AUTHORIZATION_STATE,
        "authorization_authority": "source_literal_gate_only",
        "source_gate_enabled_at_consumption": True,
        "git_head_sha": git_head_sha,
        "objective_id": FROZEN_ENDPOINT_OBJECTIVE_ID,
        "required_source_sha256": FROZEN_ENDPOINT_SOURCE_SHA256,
        "action_id": CAMERA_BRIDGE_CAPTURE_ID,
        "action_family": _AUTHORIZATION_ACTION_FAMILY,
        "key": _AUTHORIZATION_KEY,
        "hold_seconds": CAMERA_BRIDGE_CAPTURE_HOLD_SECONDS,
        "maximum_physical_primitives": (
            CAMERA_BRIDGE_CAPTURE_MAXIMUM_PHYSICAL_PRIMITIVES
        ),
        "target_policy": {
            "camera_adapter": _AUTHORIZATION_CAMERA_ADAPTER,
            "client_height": EXPECTED_CLIENT_HEIGHT,
            "client_width": EXPECTED_CLIENT_WIDTH,
            "input_lease": _AUTHORIZATION_INPUT_LEASE,
            "reviewed_pointer_logical_client": list(
                REVIEWED_CAMERA_WHEEL_POINT
            ),
            "title_substring": _AUTHORIZATION_TARGET_TITLE_SUBSTRING,
        },
        "authenticated_evidence_not_authority": evidence.as_dict(),
        "owner": "Mining-Automation Issue #31 R2 bridge launcher",
    }


def _authorization_bytes(
    git_head_sha: str,
    evidence: CameraBridgeAuthorizationEvidence,
) -> bytes:
    return (
        json.dumps(
            _authorization_payload(git_head_sha, evidence),
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def canonical_camera_bridge_component_sha256(value: object) -> str:
    """Hash one canonical report component for the completion seal."""

    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CameraBridgeAuthorizationError(
            "completion evidence component is not canonical JSON"
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def _completion_payload(
    git_head_sha: str,
    evidence: CameraBridgeCompletionEvidence,
) -> dict[str, object]:
    if _HEAD_PATTERN.fullmatch(git_head_sha) is None:
        raise CameraBridgeAuthorizationError(
            "bridge completion requires an exact lowercase Git head"
        )
    return {
        "schema_version": _AUTHORIZATION_SCHEMA_VERSION,
        "authorization_id": CAMERA_BRIDGE_AUTHORIZATION_ID,
        "authorization_version": CAMERA_BRIDGE_AUTHORIZATION_VERSION,
        "campaign_id": CAMERA_BRIDGE_AUTHORIZATION_CAMPAIGN_ID,
        "state": "complete_post_input_transaction_sealed",
        "git_head_sha": git_head_sha,
        "objective_id": FROZEN_ENDPOINT_OBJECTIVE_ID,
        "required_source_sha256": FROZEN_ENDPOINT_SOURCE_SHA256,
        "action_id": CAMERA_BRIDGE_CAPTURE_ID,
        "action_family": _AUTHORIZATION_ACTION_FAMILY,
        "key": _AUTHORIZATION_KEY,
        "hold_seconds": CAMERA_BRIDGE_CAPTURE_HOLD_SECONDS,
        "maximum_physical_primitives": (
            CAMERA_BRIDGE_CAPTURE_MAXIMUM_PHYSICAL_PRIMITIVES
        ),
        "completion_evidence": evidence.as_dict(),
        "reservation_without_this_seal_is_not_an_action_transition": True,
        "owner": "Mining-Automation Issue #31 R2 bridge launcher",
    }


def _completion_bytes(
    git_head_sha: str,
    evidence: CameraBridgeCompletionEvidence,
) -> bytes:
    return (
        json.dumps(
            _completion_payload(git_head_sha, evidence),
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def reserve_camera_bridge_authorization(
    repository_root: Path,
    *,
    git_head_sha: str,
    source_gate_enabled: bool,
    evidence: CameraBridgeAuthorizationEvidence,
) -> CameraBridgeAuthorizationReservation:
    """Atomically and permanently consume the fixed campaign before live setup.

    The sentinel is never removed here.  A partial write, process interruption,
    capture failure, or unknown physical outcome therefore consumes this code
    campaign and requires a separately reviewed source change/campaign.
    """

    if source_gate_enabled is not True:
        raise CameraBridgeAuthorizationError(
            "source-literal bridge input gate is disabled"
        )
    payload = _authorization_bytes(git_head_sha, evidence)
    common_git_dir = repository_common_git_dir(repository_root)
    sentinel_path = _authorization_sentinel_from_common_git_dir(common_git_dir)
    completion_path = _completion_seal_from_common_git_dir(common_git_dir)
    pending_path = _completion_pending_from_common_git_dir(common_git_dir)
    if os.path.lexists(completion_path) or os.path.lexists(pending_path):
        raise CameraBridgeAuthorizationConsumedError(
            "the source-owned R2 bridge campaign completion already exists"
        )
    namespace = sentinel_path.parent
    try:
        namespace.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CameraBridgeAuthorizationError(
            f"cannot create bridge authorization namespace: {namespace}"
        ) from exc
    resolved_namespace = namespace.resolve()
    try:
        resolved_namespace.relative_to(common_git_dir.resolve())
    except ValueError as exc:
        raise CameraBridgeAuthorizationError(
            "bridge authorization namespace escaped the common Git directory"
        ) from exc
    try:
        with sentinel_path.open("xb") as sentinel:
            if sentinel.write(payload) != len(payload):
                raise OSError("short bridge authorization sentinel write")
            sentinel.flush()
            os.fsync(sentinel.fileno())
    except FileExistsError as exc:
        raise CameraBridgeAuthorizationConsumedError(
            "the source-owned R2 bridge campaign has already been consumed"
        ) from exc
    digest = hashlib.sha256(payload).hexdigest()
    return CameraBridgeAuthorizationReservation(
        git_head_sha=git_head_sha,
        common_git_dir=common_git_dir,
        sentinel_path=sentinel_path,
        sentinel_sha256=digest,
        evidence=evidence,
    )


def authenticate_camera_bridge_authorization(
    repository_root: Path,
    *,
    git_head_sha: str,
    expected_sentinel_sha256: str,
    evidence: CameraBridgeAuthorizationEvidence,
) -> CameraBridgeAuthorizationReservation:
    """Authenticate the fixed sentinel for read-only post-capture analysis."""

    if _SHA256_PATTERN.fullmatch(expected_sentinel_sha256) is None:
        raise CameraBridgeAuthorizationError(
            "expected authorization sentinel SHA-256 is malformed"
        )
    expected = _authorization_bytes(git_head_sha, evidence)
    common_git_dir = repository_common_git_dir(repository_root)
    sentinel_path = _authorization_sentinel_from_common_git_dir(common_git_dir)
    try:
        observed = sentinel_path.read_bytes()
    except OSError as exc:
        raise CameraBridgeAuthorizationError(
            f"cannot read source-owned bridge authorization sentinel: {sentinel_path}"
        ) from exc
    if observed != expected:
        raise CameraBridgeAuthorizationError(
            "bridge authorization sentinel is partial, stale, or tampered"
        )
    observed_sha256 = hashlib.sha256(observed).hexdigest()
    if observed_sha256 != expected_sentinel_sha256:
        raise CameraBridgeAuthorizationError(
            "bridge authorization sentinel SHA-256 mismatch"
        )
    return CameraBridgeAuthorizationReservation(
        git_head_sha=git_head_sha,
        common_git_dir=common_git_dir,
        sentinel_path=sentinel_path,
        sentinel_sha256=observed_sha256,
        evidence=evidence,
    )


def seal_camera_bridge_completion(
    repository_root: Path,
    *,
    git_head_sha: str,
    reservation: CameraBridgeAuthorizationReservation,
    evidence: CameraBridgeCompletionEvidence,
) -> CameraBridgeCompletionSeal:
    """Atomically seal a completely serialized post-input transaction.

    The seal is never removed.  Any partial write or interruption permanently
    prevents this fixed campaign from yielding an authenticated transition.
    """

    common_git_dir = repository_common_git_dir(repository_root)
    if common_git_dir != reservation.common_git_dir:
        raise CameraBridgeAuthorizationError(
            "authorization common Git directory changed before completion"
        )
    authenticated = authenticate_camera_bridge_authorization(
        repository_root,
        git_head_sha=git_head_sha,
        expected_sentinel_sha256=reservation.sentinel_sha256,
        evidence=reservation.evidence,
    )
    if authenticated.sentinel_path != reservation.sentinel_path:
        raise CameraBridgeAuthorizationError(
            "authorization sentinel path changed before completion"
        )
    if evidence.authorization_sentinel_sha256 != reservation.sentinel_sha256:
        raise CameraBridgeAuthorizationError(
            "completion evidence does not bind the authorization reservation"
        )
    payload = _completion_bytes(git_head_sha, evidence)
    digest = hashlib.sha256(payload).hexdigest()
    seal_path = _completion_seal_from_common_git_dir(common_git_dir)
    pending_path = _completion_pending_from_common_git_dir(common_git_dir)
    try:
        with pending_path.open("xb") as pending:
            if pending.write(payload) != len(payload):
                raise OSError("short bridge completion seal write")
            pending.flush()
            os.fsync(pending.fileno())
    except FileExistsError as exc:
        raise CameraBridgeAuthorizationConsumedError(
            "the source-owned R2 bridge completion attempt already exists"
        ) from exc
    try:
        os.link(pending_path, seal_path)
    except FileExistsError as exc:
        raise CameraBridgeAuthorizationConsumedError(
            "the source-owned R2 bridge completion seal already exists"
        ) from exc
    return CameraBridgeCompletionSeal(
        git_head_sha=git_head_sha,
        common_git_dir=common_git_dir,
        seal_path=seal_path,
        seal_sha256=digest,
        evidence=evidence,
    )


def authenticate_camera_bridge_completion(
    repository_root: Path,
    *,
    git_head_sha: str,
    expected_seal_sha256: str,
    evidence: CameraBridgeCompletionEvidence,
) -> CameraBridgeCompletionSeal:
    """Authenticate the immutable completion seal during offline ingestion."""

    if _SHA256_PATTERN.fullmatch(expected_seal_sha256) is None:
        raise CameraBridgeAuthorizationError(
            "expected bridge completion seal SHA-256 is malformed"
        )
    expected = _completion_bytes(git_head_sha, evidence)
    common_git_dir = repository_common_git_dir(repository_root)
    seal_path = _completion_seal_from_common_git_dir(common_git_dir)
    try:
        observed = seal_path.read_bytes()
    except OSError as exc:
        raise CameraBridgeAuthorizationError(
            f"cannot read source-owned bridge completion seal: {seal_path}"
        ) from exc
    if observed != expected:
        raise CameraBridgeAuthorizationError(
            "bridge completion seal is partial, stale, or tampered"
        )
    observed_sha256 = hashlib.sha256(observed).hexdigest()
    if observed_sha256 != expected_seal_sha256:
        raise CameraBridgeAuthorizationError(
            "bridge completion seal SHA-256 mismatch"
        )
    return CameraBridgeCompletionSeal(
        git_head_sha=git_head_sha,
        common_git_dir=common_git_dir,
        seal_path=seal_path,
        seal_sha256=observed_sha256,
        evidence=evidence,
    )
