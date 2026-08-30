"""Source-owned one-shot authorization for the Issue #31 R2 bridge sample.

This module owns only durable authorization accounting.  It has no capture or
input dependency and cannot grant camera authority by itself.  The live
composition root must still retain its source-literal input gate and all of its
existing provenance, readiness, focus, geometry, pointer, lease, and freshness
checks.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, cast

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
CAMERA_BRIDGE_AUTHORIZATION_VERSION: Final[str] = "2.2.1"
CAMERA_BRIDGE_AUTHORIZATION_CAMPAIGN_ID: Final[str] = (
    "issue31-r2-north-up-p610-y043-reset-right-0043-v1"
)
CAMERA_BRIDGE_AUTHORIZATION_REPOSITORY_ID: Final[str] = (
    "maadbonnie21-lgtm/Mining-Automation"
)

_AUTHORIZATION_SCHEMA_VERSION: Final[int] = 2
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
_HOST_AUTHORIZATION_NAMESPACE: Final[Path] = (
    Path("Mining-Automation")
    / "host-authorizations"
    / "maadbonnie21-lgtm-Mining-Automation"
    / "issue31-camera-bridge"
)
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
_HOST_AUTHORITY_PROVIDER_ID: Final[str] = "windows-known-folder-localappdata-v1"
_LOCAL_APP_DATA_FOLDER_ID: Final[uuid.UUID] = uuid.UUID(
    "f1b32785-6fba-4fcf-9d55-7b8e7f157091"
)


class CameraBridgeAuthorizationError(RuntimeError):
    """Raised when the fixed authorization namespace cannot be authenticated."""


class CameraBridgeAuthorizationConsumedError(CameraBridgeAuthorizationError):
    """Raised whenever the one-shot campaign sentinel already exists."""


class _WindowsGuid(ctypes.Structure):
    """ctypes representation of a Windows KNOWNFOLDERID."""

    _fields_ = (
        ("data1", ctypes.c_uint32),
        ("data2", ctypes.c_uint16),
        ("data3", ctypes.c_uint16),
        ("data4", ctypes.c_ubyte * 8),
    )

    @classmethod
    def from_uuid(cls, value: uuid.UUID) -> _WindowsGuid:
        fields = value.fields
        node = int(fields[5]).to_bytes(6, "big")
        return cls(
            fields[0],
            fields[1],
            fields[2],
            (ctypes.c_ubyte * 8)(fields[3], fields[4], *node),
        )


@dataclass(frozen=True, slots=True)
class _HostAuthorizationStore:
    """Fixed host-global paths for the one source-owned campaign."""

    root: Path
    namespace: Path
    namespace_identity: tuple[int, int] | None
    sentinel_path: Path
    completion_pending_path: Path
    completion_seal_path: Path


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
    host_authority_root: Path
    sentinel_path: Path
    sentinel_sha256: str
    evidence: CameraBridgeAuthorizationEvidence
    authority_namespace_identity: tuple[int, int] = (0, 0)

    def as_dict(self) -> dict[str, object]:
        """Return canonical report evidence without exposing an absolute path."""

        relative_path = self.sentinel_path.relative_to(self.host_authority_root)
        return {
            **_authorization_payload(self.git_head_sha, self.evidence),
            "sentinel_relative_to_host_authority_root": relative_path.as_posix(),
            "sentinel_sha256": self.sentinel_sha256,
            "source_owned_namespace": True,
            "persistent_per_user_host_global_authority": True,
            "independent_repository_clone_can_bypass": False,
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
    host_authority_root: Path
    seal_path: Path
    seal_sha256: str
    evidence: CameraBridgeCompletionEvidence
    authority_namespace_identity: tuple[int, int] = (0, 0)

    def as_dict(self) -> dict[str, object]:
        relative_path = self.seal_path.relative_to(self.host_authority_root)
        return {
            **_completion_payload(self.git_head_sha, self.evidence),
            "seal_relative_to_host_authority_root": relative_path.as_posix(),
            "seal_sha256": self.seal_sha256,
            "source_owned_namespace": True,
            "persistent_per_user_host_global_authority": True,
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


def _host_authority_base() -> Path:
    """Resolve per-user LocalAppData through the Windows Known Folder API."""

    if os.name != "nt":
        raise CameraBridgeAuthorizationError(
            "host-global bridge authorization requires Windows LocalAppData"
        )
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:  # pragma: no cover - defensive Windows runtime guard
        raise CameraBridgeAuthorizationError("Windows DLL loading is unavailable")
    shell32 = cast(Any, win_dll("shell32", use_last_error=True))
    ole32 = cast(Any, win_dll("ole32", use_last_error=True))
    known_folder = shell32.SHGetKnownFolderPath
    known_folder.argtypes = (
        ctypes.POINTER(_WindowsGuid),
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    )
    known_folder.restype = ctypes.c_long
    free_memory = ole32.CoTaskMemFree
    free_memory.argtypes = (ctypes.c_void_p,)
    free_memory.restype = None
    folder_id = _WindowsGuid.from_uuid(_LOCAL_APP_DATA_FOLDER_ID)
    raw_path = ctypes.c_void_p()
    result = cast(
        int,
        known_folder(
            ctypes.byref(folder_id),
            0,
            None,
            ctypes.byref(raw_path),
        ),
    )
    if result != 0 or raw_path.value is None:
        raise CameraBridgeAuthorizationError(
            "SHGetKnownFolderPath(FOLDERID_LocalAppData) failed "
            f"with HRESULT 0x{result & 0xFFFFFFFF:08x}"
        )
    try:
        value = ctypes.wstring_at(raw_path.value)
    finally:
        free_memory(raw_path)
    if not value or "\x00" in value:
        raise CameraBridgeAuthorizationError(
            "Windows LocalAppData Known Folder returned an invalid path"
        )
    return Path(value)


def _is_link_or_reparse(path: Path) -> bool:
    """Return whether an existing path can redirect fixed namespace lookup."""

    metadata = os.lstat(path)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
    attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)


def _path_exists_no_follow(path: Path, *, label: str) -> bool:
    """Return false only for a proven absence; every ambiguity fails closed."""

    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise CameraBridgeAuthorizationError(f"cannot inspect {label}: {path}") from exc
    return True


def _namespace_identity(namespace: Path) -> tuple[int, int]:
    """Return a stable directory identity after rejecting path redirects."""

    try:
        metadata = os.lstat(namespace)
    except OSError as exc:
        raise CameraBridgeAuthorizationError(
            f"cannot inspect host authorization namespace: {namespace}"
        ) from exc
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
    attributes = getattr(metadata, "st_file_attributes", 0)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or bool(attributes & reparse_flag)
        or not stat.S_ISDIR(metadata.st_mode)
    ):
        raise CameraBridgeAuthorizationError(
            "host authorization namespace is not a fixed directory"
        )
    return metadata.st_dev, metadata.st_ino


def _validate_windows_host_volume(root: Path) -> None:
    """Require a fixed local volume with hard-link support on Windows."""

    if os.name != "nt":
        return
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:  # pragma: no cover - defensive Windows runtime guard
        raise CameraBridgeAuthorizationError("Windows DLL loading is unavailable")
    kernel32 = cast(Any, win_dll("kernel32", use_last_error=True))
    volume_path = ctypes.create_unicode_buffer(32768)
    get_volume_path = kernel32.GetVolumePathNameW
    get_volume_path.argtypes = (
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_uint32,
    )
    get_volume_path.restype = ctypes.c_int
    if not get_volume_path(str(root), volume_path, len(volume_path)):
        raise CameraBridgeAuthorizationError(
            "cannot resolve host authorization volume"
        )
    get_drive_type = kernel32.GetDriveTypeW
    get_drive_type.argtypes = (ctypes.c_wchar_p,)
    get_drive_type.restype = ctypes.c_uint32
    if cast(int, get_drive_type(volume_path.value)) != 3:
        raise CameraBridgeAuthorizationError(
            "host authorization volume must be a fixed local drive"
        )
    filesystem_flags = ctypes.c_uint32()
    get_volume_information = kernel32.GetVolumeInformationW
    get_volume_information.argtypes = (
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_wchar_p,
        ctypes.c_uint32,
    )
    get_volume_information.restype = ctypes.c_int
    if not get_volume_information(
        volume_path.value,
        None,
        0,
        None,
        None,
        ctypes.byref(filesystem_flags),
        None,
        0,
    ):
        raise CameraBridgeAuthorizationError(
            "cannot inspect host authorization filesystem capabilities"
        )
    if not filesystem_flags.value & 0x00400000:
        raise CameraBridgeAuthorizationError(
            "host authorization filesystem does not support hard links"
        )


def _resolved_host_authority_root() -> Path:
    """Resolve the source-owned host trust anchor without environment fallback."""

    candidate = _host_authority_base()
    if not candidate.is_absolute():
        raise CameraBridgeAuthorizationError(
            "host authorization Known Folder path is not absolute"
        )
    if os.name == "nt" and str(candidate).startswith(("\\\\", "//")):
        raise CameraBridgeAuthorizationError(
            "host authorization Known Folder may not be a network path"
        )
    cursor = Path(candidate.anchor)
    for component in candidate.parts[1:]:
        cursor /= component
        if not _path_exists_no_follow(
            cursor,
            label="host authorization Known Folder component",
        ):
            raise CameraBridgeAuthorizationError(
                f"host authorization Known Folder component is missing: {cursor}"
            )
        try:
            if _is_link_or_reparse(cursor) or not cursor.is_dir():
                raise CameraBridgeAuthorizationError(
                    "host authorization Known Folder contains a redirect or "
                    f"non-directory component: {cursor}"
                )
        except OSError as exc:
            raise CameraBridgeAuthorizationError(
                f"cannot inspect host authorization Known Folder: {cursor}"
            ) from exc
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise CameraBridgeAuthorizationError(
            f"cannot resolve host authorization root: {candidate}"
        ) from exc
    if not resolved.is_dir():
        raise CameraBridgeAuthorizationError(
            f"host authorization root is not a directory: {resolved}"
        )
    _validate_windows_host_volume(resolved)
    return resolved


def _host_authorization_store(
    repository_root: Path,
    *,
    create_namespace: bool,
) -> _HostAuthorizationStore:
    """Resolve the same fixed host store from every valid repository clone."""

    repository_worktree_git_dir(repository_root)
    root = _resolved_host_authority_root()
    cursor = root
    namespace_missing = False
    for component in _HOST_AUTHORIZATION_NAMESPACE.parts:
        cursor = cursor / component
        if namespace_missing:
            if create_namespace:
                try:
                    cursor.mkdir()
                except FileExistsError:
                    pass
            else:
                continue
        if not _path_exists_no_follow(
            cursor,
            label="host authorization namespace component",
        ):
            namespace_missing = True
            if not create_namespace:
                continue
            try:
                cursor.mkdir()
            except FileExistsError:
                pass
        if not _path_exists_no_follow(
            cursor,
            label="host authorization namespace component",
        ):
            raise CameraBridgeAuthorizationError(
                f"cannot create host authorization namespace: {cursor}"
            )
        try:
            if _is_link_or_reparse(cursor) or not cursor.is_dir():
                raise CameraBridgeAuthorizationError(
                    "host authorization namespace contains a redirect or "
                    f"non-directory component: {cursor}"
                )
        except OSError as exc:
            raise CameraBridgeAuthorizationError(
                f"cannot inspect host authorization namespace: {cursor}"
            ) from exc
    namespace = root / _HOST_AUTHORIZATION_NAMESPACE
    try:
        resolved_namespace = namespace.resolve(strict=create_namespace)
        resolved_namespace.relative_to(root)
    except (OSError, ValueError) as exc:
        raise CameraBridgeAuthorizationError(
            "host authorization namespace escaped its Known Folder root"
        ) from exc
    namespace_identity = (
        _namespace_identity(namespace)
        if _path_exists_no_follow(
            namespace,
            label="host authorization namespace",
        )
        else None
    )
    return _HostAuthorizationStore(
        root=root,
        namespace=namespace,
        namespace_identity=namespace_identity,
        sentinel_path=namespace / _AUTHORIZATION_SENTINEL_NAME,
        completion_pending_path=namespace / _COMPLETION_PENDING_NAME,
        completion_seal_path=namespace / _COMPLETION_SEAL_NAME,
    )


def camera_bridge_authorization_sentinel_path(repository_root: Path) -> Path:
    """Return the per-user host-global sentinel path for this campaign."""

    return _host_authorization_store(
        repository_root,
        create_namespace=False,
    ).sentinel_path


def camera_bridge_completion_seal_path(repository_root: Path) -> Path:
    """Return the per-user host-global completion-seal path."""

    return _host_authorization_store(
        repository_root,
        create_namespace=False,
    ).completion_seal_path


def _completion_pending_path(repository_root: Path) -> Path:
    return _host_authorization_store(
        repository_root,
        create_namespace=False,
    ).completion_pending_path


def camera_bridge_authorization_consumed(repository_root: Path) -> bool:
    """Return whether any host-global artifact consumes the R2.2 campaign."""

    store = _host_authorization_store(
        repository_root,
        create_namespace=False,
    )
    return any(
        _path_exists_no_follow(path, label="host authorization artifact")
        for path in (
            store.sentinel_path,
            store.completion_pending_path,
            store.completion_seal_path,
        )
    )


def _read_regular_artifact(path: Path, *, label: str) -> bytes:
    """Read one fixed artifact without accepting links or non-regular files."""

    try:
        before = os.lstat(path)
    except OSError as exc:
        raise CameraBridgeAuthorizationError(f"cannot inspect {label}: {path}") from exc
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
    attributes = getattr(before, "st_file_attributes", 0)
    if (
        stat.S_ISLNK(before.st_mode)
        or bool(attributes & reparse_flag)
        or not stat.S_ISREG(before.st_mode)
    ):
        raise CameraBridgeAuthorizationError(
            f"{label} is not a fixed regular file: {path}"
        )
    try:
        with path.open("rb") as artifact:
            opened = os.fstat(artifact.fileno())
            if (
                opened.st_dev != before.st_dev
                or opened.st_ino != before.st_ino
                or not stat.S_ISREG(opened.st_mode)
            ):
                raise CameraBridgeAuthorizationError(
                    f"{label} changed while it was opened"
                )
            observed = artifact.read()
    except OSError as exc:
        raise CameraBridgeAuthorizationError(f"cannot read {label}: {path}") from exc
    try:
        after = os.lstat(path)
    except OSError as exc:
        raise CameraBridgeAuthorizationError(
            f"cannot re-inspect {label}: {path}"
        ) from exc
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
    attributes = getattr(after, "st_file_attributes", 0)
    if (
        after.st_dev != opened.st_dev
        or after.st_ino != opened.st_ino
        or stat.S_ISLNK(after.st_mode)
        or bool(attributes & reparse_flag)
        or not stat.S_ISREG(after.st_mode)
    ):
        raise CameraBridgeAuthorizationError(f"{label} changed while it was read")
    return observed


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
        "repository_id": CAMERA_BRIDGE_AUTHORIZATION_REPOSITORY_ID,
        "authority_scope": "persistent_per_user_host_global",
        "authority_provider_id": _HOST_AUTHORITY_PROVIDER_ID,
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
        "repository_id": CAMERA_BRIDGE_AUTHORIZATION_REPOSITORY_ID,
        "authority_scope": "persistent_per_user_host_global",
        "authority_provider_id": _HOST_AUTHORITY_PROVIDER_ID,
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
    store = _host_authorization_store(
        repository_root,
        create_namespace=True,
    )
    if store.namespace_identity is None:  # pragma: no cover - defensive
        raise CameraBridgeAuthorizationError(
            "host authorization namespace has no stable identity"
        )
    if _path_exists_no_follow(
        store.completion_seal_path,
        label="host-global bridge completion seal",
    ) or _path_exists_no_follow(
        store.completion_pending_path,
        label="host-global bridge completion pending witness",
    ):
        raise CameraBridgeAuthorizationConsumedError(
            "the source-owned R2 bridge campaign completion already exists"
        )
    try:
        with store.sentinel_path.open("xb") as sentinel:
            if sentinel.write(payload) != len(payload):
                raise OSError("short bridge authorization sentinel write")
            sentinel.flush()
            os.fsync(sentinel.fileno())
    except FileExistsError as exc:
        raise CameraBridgeAuthorizationConsumedError(
            "the source-owned R2 bridge campaign has already been consumed"
        ) from exc
    if _namespace_identity(store.namespace) != store.namespace_identity:
        raise CameraBridgeAuthorizationError(
            "host authorization namespace changed during reservation"
        )
    digest = hashlib.sha256(payload).hexdigest()
    return CameraBridgeAuthorizationReservation(
        git_head_sha=git_head_sha,
        host_authority_root=store.root,
        sentinel_path=store.sentinel_path,
        sentinel_sha256=digest,
        evidence=evidence,
        authority_namespace_identity=store.namespace_identity,
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
    store = _host_authorization_store(
        repository_root,
        create_namespace=False,
    )
    if store.namespace_identity is None:
        raise CameraBridgeAuthorizationError(
            "host authorization namespace is missing"
        )
    observed = _read_regular_artifact(
        store.sentinel_path,
        label="source-owned host-global bridge authorization sentinel",
    )
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
        host_authority_root=store.root,
        sentinel_path=store.sentinel_path,
        sentinel_sha256=observed_sha256,
        evidence=evidence,
        authority_namespace_identity=store.namespace_identity,
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

    store = _host_authorization_store(
        repository_root,
        create_namespace=False,
    )
    if store.root != reservation.host_authority_root:
        raise CameraBridgeAuthorizationError(
            "host authorization root changed before completion"
        )
    if store.namespace_identity != reservation.authority_namespace_identity:
        raise CameraBridgeAuthorizationError(
            "host authorization namespace identity changed before completion"
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
    try:
        with store.completion_pending_path.open("xb") as pending:
            if pending.write(payload) != len(payload):
                raise OSError("short bridge completion seal write")
            pending.flush()
            os.fsync(pending.fileno())
    except FileExistsError as exc:
        raise CameraBridgeAuthorizationConsumedError(
            "the source-owned R2 bridge completion attempt already exists"
        ) from exc
    if _namespace_identity(store.namespace) != store.namespace_identity:
        raise CameraBridgeAuthorizationError(
            "host authorization namespace changed during completion write"
        )
    try:
        os.link(
            store.completion_pending_path,
            store.completion_seal_path,
            follow_symlinks=False,
        )
    except FileExistsError as exc:
        raise CameraBridgeAuthorizationConsumedError(
            "the source-owned R2 bridge completion seal already exists"
        ) from exc
    if _namespace_identity(store.namespace) != store.namespace_identity:
        raise CameraBridgeAuthorizationError(
            "host authorization namespace changed during completion seal"
        )
    return CameraBridgeCompletionSeal(
        git_head_sha=git_head_sha,
        host_authority_root=store.root,
        seal_path=store.completion_seal_path,
        seal_sha256=digest,
        evidence=evidence,
        authority_namespace_identity=store.namespace_identity,
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
    store = _host_authorization_store(
        repository_root,
        create_namespace=False,
    )
    if store.namespace_identity is None:
        raise CameraBridgeAuthorizationError(
            "host authorization namespace is missing"
        )
    observed = _read_regular_artifact(
        store.completion_seal_path,
        label="source-owned host-global bridge completion seal",
    )
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
        host_authority_root=store.root,
        seal_path=store.completion_seal_path,
        seal_sha256=observed_sha256,
        evidence=evidence,
        authority_namespace_identity=store.namespace_identity,
    )
