"""Locked passive capture path for a future Inventory V3 campaign.

This development-only path never runs a detector and never sends input. It
captures one owned frame after each operator acknowledgement, preserves every
successful capture, and materializes the fixed inventory rectangle as an exact
BGRA row slice. Running it is not authorized until a separate lead decision.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import platform
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Final, cast

from ..capture import CaptureSource, Frame, PixelFormat
from ..capture.windows import WindowsCaptureBackend
from ..capture.windows.win32_api import RealWin32Api

__all__ = [
    "INDEPENDENT_CAPTURE_STAGES",
    "PassiveInventoryV3CaptureError",
    "PassiveInventoryV3CaptureInputs",
    "PassiveInventoryV3CaptureResult",
    "run_passive_inventory_v3_capture_campaign",
]

INDEPENDENT_CAPTURE_STAGES: Final[tuple[str, ...]] = (
    "empty",
    "early-partial",
    "mid-partial",
    "near-full",
    "full",
    "wrong-tab",
    "row-obstruction",
)
_APPROVED_CONFIGURATION_ID: Final[str] = (
    "inventory-positive-v3-independent-passive-natural-fill-v1"
)
_FROZEN_CANDIDATE_HEAD_SHA: Final[str] = (
    "5975532b472a74d93f010e04ca44b2efa2a3ffd7"
)
_PREREGISTRATION_SHA256: Final[str] = (
    "47db5a775095b7828e1c10d19949519002d5c7540eaf8d3c18e0eb3154bd9130"
)
_PROTOCOL_LOCK_PATH: Final[PurePosixPath] = PurePosixPath(
    "validation/inventory-positive-v3/protocol-lock.json"
)
_PROTOCOL_LOCK_SCHEMA: Final[str] = (
    "inventory-positive-v3-independent-validation-protocol-lock-v1"
)
_LIVE_AUTHORIZATION_PATH: Final[PurePosixPath] = PurePosixPath(
    "validation/inventory-positive-v3/live-campaign-authorizations.json"
)
_LIVE_AUTHORIZATION_SCHEMA: Final[str] = (
    "inventory-positive-v3-independent-live-campaign-authorization-registry-v1"
)
_LIVE_AUTHORIZATION_STATUS: Final[str] = (
    "authorized-for-passive-independent-validation-capture"
)
_PROTOCOL_ID: Final[str] = "inventory-positive-v3-independent-validation"
_PROTOCOL_VERSION: Final[str] = "1.0.0"
_PROTOCOL_LOCKED_PATHS: Final[tuple[str, ...]] = (
    ".gitattributes",
    "src/mining_automation/__init__.py",
    "src/mining_automation/capture/windows/bmp.py",
    "src/mining_automation/perception/__init__.py",
    "src/mining_automation/perception/detector.py",
    "src/mining_automation/perception/errors.py",
    "src/mining_automation/perception/evaluation.py",
    "src/mining_automation/perception/inventory/__init__.py",
    "src/mining_automation/perception/inventory/adapter.py",
    "src/mining_automation/perception/inventory/classification.py",
    "src/mining_automation/perception/inventory/configuration.py",
    "src/mining_automation/perception/inventory/detector.py",
    "src/mining_automation/perception/inventory/fixture_preparation.py",
    "src/mining_automation/perception/inventory/geometry.py",
    "src/mining_automation/perception/inventory/live_validation.py",
    "src/mining_automation/perception/inventory/live_validation_session.py",
    "src/mining_automation/perception/inventory/localization.py",
    "src/mining_automation/perception/inventory/positive_classifier_v2.py",
    "src/mining_automation/perception/inventory/positive_classifier_v3.py",
    "src/mining_automation/perception/inventory/positive_v2_calibration.py",
    "src/mining_automation/perception/inventory/positive_v2_evaluation.py",
    "src/mining_automation/perception/inventory/positive_v3_independent_validation.py",
    "src/mining_automation/perception/inventory/positive_v3_independent_validation_cli.py",
    "src/mining_automation/perception/inventory/positive_v3_prototypes.py",
    "src/mining_automation/perception/inventory/review_gate.py",
    "src/mining_automation/perception/inventory/sanitized_replay.py",
    "src/mining_automation/perception/replay.py",
    "tools/inventory_v3_independent_validation.py",
    "validation/inventory-positive-v3/preregistration.json",
    "validation/inventory-positive-v3/preregistration.sha256",
)
_CAPTURE_SOURCE_PATHS: Final[tuple[str, ...]] = (
    ".gitattributes",
    ".gitignore",
    "src/mining_automation/__init__.py",
    "src/mining_automation/capture/__init__.py",
    "src/mining_automation/capture/backend.py",
    "src/mining_automation/capture/errors.py",
    "src/mining_automation/capture/frame.py",
    "src/mining_automation/capture/source.py",
    "src/mining_automation/capture/windows/__init__.py",
    "src/mining_automation/capture/windows/_win32_calls.py",
    "src/mining_automation/capture/windows/backend.py",
    "src/mining_automation/capture/windows/gdi_resources.py",
    "src/mining_automation/capture/windows/geometry.py",
    "src/mining_automation/capture/windows/win32_api.py",
    "src/mining_automation/capture/windows/window_selector.py",
    "src/mining_automation/contracts.py",
    "src/mining_automation/diagnostics.py",
    "src/mining_automation/validation/__init__.py",
    "src/mining_automation/validation/inventory_v3_capture.py",
    "src/mining_automation/validation/inventory_v3_capture_cli.py",
    "tools/capture_inventory_v3_independent.py",
)
_CAPTURE_LAUNCHER_PATH: Final[PurePosixPath] = PurePosixPath(
    "tools/capture_inventory_v3_independent.py"
)
_SOURCE_SESSION_SCHEMA: Final[str] = (
    "inventory-positive-v3-independent-source-session-v2"
)
_SOURCE_CAPTURE_SCHEMA: Final[str] = (
    "inventory-positive-v3-independent-source-capture-v2"
)
_PROGRESS_SCHEMA: Final[str] = (
    "inventory-positive-v3-independent-passive-capture-progress-v1"
)
_OWNED_FRAME_SCHEMA: Final[str] = (
    "inventory-positive-v3-independent-owned-frame-v1"
)
_COMPLETION_SEAL_SCHEMA: Final[str] = (
    "inventory-positive-v3-independent-source-completion-seal-v1"
)
_TERMINAL_FAILURE_SCHEMA: Final[str] = (
    "inventory-positive-v3-independent-source-terminal-failure-v1"
)
_SUPPORTED_FRAME_WIDTH: Final[int] = 1005
_SUPPORTED_FRAME_HEIGHT: Final[int] = 1078
_SUPPORTED_PIXEL_FORMAT: Final[str] = "bgra8888"
_SUPPORTED_PROFILE_ID: Final[str] = "candidate-live-inventory-348867800b28a54e"
_SUPPORTED_REGION: Final[tuple[int, int, int, int]] = (567, 569, 158, 248)
_FULL_FRAME_NAME: Final[str] = "full-frame.bgra"
_REGION_NAME: Final[str] = "inventory-region.bgra"
_CAPTURE_REPORT_NAME: Final[str] = "source-capture-report.json"
_SESSION_REPORT_NAME: Final[str] = "source-session-report.json"
_COMPLETION_SEAL_NAME: Final[str] = "source-completion-seal.json"
_TERMINAL_FAILURE_NAME: Final[str] = "campaign-terminal-failure.json"
_PROGRESS_NAME: Final[str] = "capture-progress.json"
_PRIVATE_OUTPUT_RELATIVE: Final[PurePosixPath] = PurePosixPath(
    "diagnostics/inventory-positive-v3-independent-source"
)
_HOST_RESERVATION_RELATIVE: Final[PurePosixPath] = PurePosixPath(
    "Mining-Automation/inventory-positive-v3-independent-reservations"
)
_HOST_RESERVATION_SCHEMA: Final[str] = (
    "inventory-positive-v3-independent-host-reservation-v1"
)
_REPOSITORY_ID: Final[str] = "maadbonnie21-lgtm/Mining-Automation"

class PassiveInventoryV3CaptureError(RuntimeError):
    """The passive campaign could not preserve its evidence contract."""


@dataclass(frozen=True, slots=True)
class PassiveInventoryV3CaptureInputs:
    """Operator/environment assertions that cannot redefine capture identity."""

    operator: str
    runelite_build: str
    client_mode: str
    theme: str
    renderer: str

    def __post_init__(self) -> None:
        for label, value in (
            ("operator", self.operator),
            ("runelite_build", self.runelite_build),
            ("client_mode", self.client_mode),
            ("theme", self.theme),
            ("renderer", self.renderer),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} must be non-empty text")
            if value != value.strip():
                raise ValueError(f"{label} cannot contain surrounding whitespace")


@dataclass(frozen=True, slots=True)
class PassiveInventoryV3CaptureResult:
    """Finalized source evidence; capture completion is never visual truth."""

    campaign_directory: Path
    campaign_id: str
    session_id: str
    source_session_report_path: Path
    source_session_report_sha256: str
    source_completion_seal_path: Path
    source_completion_seal_sha256: str
    capture_count: int
    protocol_lock_git_commit_sha: str
    capture_build_sha: str
    capture_configuration_id: str
    capture_execution_head_sha: str
    host_reservation_sha256: str
    live_authorization_id: str
    live_authorization_git_commit_sha: str


@dataclass(frozen=True, slots=True)
class _ProtocolBinding:
    execution_head_sha: str
    execution_head_committed_at_utc: str
    lock_commit_sha: str
    lock_committed_at_utc: str
    lock_sha256: str
    capture_build_sha: str
    capture_configuration_id: str


@dataclass(frozen=True, slots=True)
class _LiveAuthorizationBinding:
    authorization_id: str
    git_commit_sha: str
    git_committed_at_utc: str
    git_blob: str


@dataclass(frozen=True, slots=True)
class _OwnedFrameBinding:
    capture_id: str
    capture_directory: Path
    relative_root: Path
    full_frame_relative_path: str
    full_frame_sha256: str
    full_frame_size_bytes: int
    ownership_report_relative_path: str
    ownership_report_sha256: str


@dataclass(frozen=True, slots=True)
class _CampaignProgressContext:
    campaign_directory: Path
    campaign_id: str
    session_id: str
    started_at_utc: str
    captures: list[dict[str, object]]
    owned_attempts: list[dict[str, object]]
    protocol: _ProtocolBinding
    authorization: _LiveAuthorizationBinding
    host_reservation_sha256: str
    inputs: PassiveInventoryV3CaptureInputs


def run_passive_inventory_v3_capture_campaign(
    *,
    inputs: PassiveInventoryV3CaptureInputs,
    repository_root: Path,
) -> PassiveInventoryV3CaptureResult:
    """Capture the immutable seven-stage plan via the exact approved path.

    The eligible entry point deliberately exposes no backend, clock, callback,
    retry, stage, crop, or detector seam.  A separate Git-tracked authorization
    entry must exist before this function creates a directory or OS backend.
    """
    _require_isolated_mode()
    progress_context: list[_CampaignProgressContext] = []
    try:
        return _run_passive_inventory_v3_capture_campaign(
            inputs=inputs,
            repository_root=repository_root,
            progress_context=progress_context,
        )
    except BaseException as exc:
        if progress_context and not _completion_commit_exists(
            progress_context[0].campaign_directory
        ):
            _record_terminal_failure(progress_context[0], exc)
        raise


def _run_passive_inventory_v3_capture_campaign(
    *,
    inputs: PassiveInventoryV3CaptureInputs,
    repository_root: Path,
    progress_context: list[_CampaignProgressContext],
) -> PassiveInventoryV3CaptureResult:
    _require_isolated_mode()
    if not isinstance(repository_root, Path):
        raise TypeError("repository_root must be pathlib.Path")
    if not isinstance(inputs, PassiveInventoryV3CaptureInputs):
        raise TypeError("inputs must be PassiveInventoryV3CaptureInputs")
    protocol = _verify_capture_repository(repository_root)
    authorization = _verify_live_capture_authorization(repository_root, protocol)
    started_at = _utc_timestamp()
    _require_after_protocol_lock(started_at, protocol)
    _require_after_live_authorization(started_at, authorization)
    _require_after_execution_head(started_at, protocol)
    output_root = _approved_output_root(repository_root)
    reservation_root = _approved_host_reservation_root()
    campaign_directory, session_id, host_reservation_sha256 = (
        _allocate_campaign_directory(
            output_root,
            reservation_root=reservation_root,
            authorization=authorization,
            protocol=protocol,
        )
    )
    campaign_id = _content_bound_campaign_id(session_id)
    captures: list[dict[str, object]] = []
    owned_attempts: list[dict[str, object]] = []
    progress_context.append(
        _CampaignProgressContext(
            campaign_directory=campaign_directory,
            campaign_id=campaign_id,
            session_id=session_id,
            started_at_utc=started_at,
            captures=captures,
            owned_attempts=owned_attempts,
            protocol=protocol,
            authorization=authorization,
            host_reservation_sha256=host_reservation_sha256,
            inputs=inputs,
        )
    )
    fixed_environment: dict[str, object] | None = None
    last_capture_time = _parse_utc(started_at)
    _write_progress(
        campaign_directory,
        campaign_id=campaign_id,
        session_id=session_id,
        started_at_utc=started_at,
        status="capturing",
        captures=captures,
        owned_attempts=owned_attempts,
        protocol=protocol,
        authorization=authorization,
        host_reservation_sha256=host_reservation_sha256,
        inputs=inputs,
    )

    for sequence_index, stage in enumerate(INDEPENDENT_CAPTURE_STAGES, start=1):
        _acknowledge_stage(
            stage,
            sequence_index,
            len(INDEPENDENT_CAPTURE_STAGES),
            campaign_directory,
        )
        current_protocol = _verify_capture_repository(repository_root)
        current_authorization = _verify_live_capture_authorization(
            repository_root,
            current_protocol,
        )
        if (
            current_protocol != protocol
            or current_authorization != authorization
        ):
            raise PassiveInventoryV3CaptureError(
                "capture provenance changed during the source session"
            )
        backend = _new_source_owned_backend()
        if type(backend) is not WindowsCaptureBackend:
            raise PassiveInventoryV3CaptureError(
                "eligible capture requires the exact WindowsCaptureBackend"
            )
        with CaptureSource(
            backend,
            retry_attempts=0,
        ) as source:
            frame = source.capture()
            window = backend.selected_window
            if window is None:
                raise PassiveInventoryV3CaptureError(
                    "capture backend did not retain selected-window metadata"
                )
            dpi_error: Exception | None = None
            try:
                dpi = backend.current_dpi
            except Exception as exc:  # preserve pixels before failing provenance
                dpi = None
                dpi_error = exc
        captured_at = _utc_timestamp()
        owned_frame = _persist_owned_frame(
            campaign_directory,
            session_id=session_id,
            sequence_index=sequence_index,
            stage=stage,
            captured_at_utc=captured_at,
            frame=frame,
            window_handle=window.hwnd,
            window_class=window.class_name,
            windows_dpi=dpi,
            owned_attempts=owned_attempts,
        )
        _write_progress(
            campaign_directory,
            campaign_id=campaign_id,
            session_id=session_id,
            started_at_utc=started_at,
            status="captured-unchecked",
            captures=captures,
            owned_attempts=owned_attempts,
            protocol=protocol,
            authorization=authorization,
            host_reservation_sha256=host_reservation_sha256,
            inputs=inputs,
        )
        if dpi_error is not None:
            raise PassiveInventoryV3CaptureError(
                "Windows DPI lookup failed after an owned frame was retained"
            ) from dpi_error
        _require_after_protocol_lock(captured_at, protocol)
        _require_after_live_authorization(captured_at, authorization)
        _require_after_execution_head(captured_at, protocol)
        parsed_capture_time = _parse_utc(captured_at)
        if parsed_capture_time <= last_capture_time:
            raise PassiveInventoryV3CaptureError(
                "capture wall-clock timestamps must increase strictly"
            )
        last_capture_time = parsed_capture_time
        if frame.width != _SUPPORTED_FRAME_WIDTH or frame.height != (
            _SUPPORTED_FRAME_HEIGHT
        ):
            raise PassiveInventoryV3CaptureError(
                "capture geometry differs from the frozen 1005x1078 profile"
            )
        if frame.pixel_format is not PixelFormat.BGRA8888:
            raise PassiveInventoryV3CaptureError(
                "capture pixel format differs from frozen BGRA8888"
            )
        if not isinstance(dpi, int) or isinstance(dpi, bool) or dpi <= 0:
            raise PassiveInventoryV3CaptureError(
                "Windows DPI must be available for independent capture provenance"
            )
        environment = _capture_environment(
            protocol,
            authorization,
            inputs,
            host_reservation_sha256=host_reservation_sha256,
            window_handle=window.hwnd,
            window_class=window.class_name,
            dpi=dpi,
        )
        if fixed_environment is None:
            fixed_environment = environment
        elif environment != fixed_environment:
            raise PassiveInventoryV3CaptureError(
                "capture environment changed within the immutable source session"
            )
        capture = _persist_capture(
            owned_frame,
            session_id=session_id,
            sequence_index=sequence_index,
            stage=stage,
            captured_at_utc=captured_at,
            environment=environment,
            full_frame=frame.payload,
        )
        captures.append(capture)
        _write_progress(
            campaign_directory,
            campaign_id=campaign_id,
            session_id=session_id,
            started_at_utc=started_at,
            status="capturing",
            captures=captures,
            owned_attempts=owned_attempts,
            protocol=protocol,
            authorization=authorization,
            host_reservation_sha256=host_reservation_sha256,
            inputs=inputs,
        )
        if _verify_capture_repository(repository_root) != protocol:
            raise PassiveInventoryV3CaptureError(
                "capture provenance changed after frame preservation"
            )
        if _verify_live_capture_authorization(repository_root, protocol) != (
            authorization
        ):
            raise PassiveInventoryV3CaptureError(
                "live authorization changed after frame preservation"
            )

    if fixed_environment is None:
        raise PassiveInventoryV3CaptureError("campaign captured no frames")
    completed_at = _utc_timestamp()
    _require_after_protocol_lock(completed_at, protocol)
    _require_after_live_authorization(completed_at, authorization)
    _require_after_execution_head(completed_at, protocol)
    if _parse_utc(completed_at) <= last_capture_time:
        raise PassiveInventoryV3CaptureError(
            "campaign completion timestamp must follow every capture"
        )
    if _verify_capture_repository(repository_root) != protocol:
        raise PassiveInventoryV3CaptureError(
            "capture provenance changed before final publication"
        )
    if _verify_live_capture_authorization(repository_root, protocol) != authorization:
        raise PassiveInventoryV3CaptureError(
            "live authorization changed before final publication"
        )
    source_session = {
        "activation_allowed": False,
        "all_owned_captures_included": True,
        "campaign_id": campaign_id,
        "capture_environment": fixed_environment,
        "captures": captures,
        "completed_at_utc": completed_at,
        "operator": inputs.operator,
        "owned_attempts": owned_attempts,
        "schema": _SOURCE_SESSION_SCHEMA,
        "session_id": session_id,
        "started_at_utc": started_at,
    }
    report_path = campaign_directory / _SESSION_REPORT_NAME
    report_payload = _canonical_bytes(source_session)
    _write_canonical_with_sidecar_exclusive(report_path, report_payload)
    report_sha = _sha256(report_payload)
    if _verify_capture_repository(repository_root) != protocol:
        raise PassiveInventoryV3CaptureError(
            "capture provenance changed during final publication"
        )
    if _verify_live_capture_authorization(repository_root, protocol) != authorization:
        raise PassiveInventoryV3CaptureError(
            "live authorization changed during final publication"
        )
    completion_seal = {
        "activation_allowed": False,
        "authorization_id": authorization.authorization_id,
        "campaign_id": campaign_id,
        "capture_count": len(captures),
        "capture_execution_head_sha": protocol.execution_head_sha,
        "completed_at_utc": completed_at,
        "host_reservation_sha256": host_reservation_sha256,
        "live_authorization_git_commit_sha": authorization.git_commit_sha,
        "protocol_lock_git_commit_sha": protocol.lock_commit_sha,
        "schema": _COMPLETION_SEAL_SCHEMA,
        "session_id": session_id,
        "source_session_report_sha256": report_sha,
        "status": "complete-not-reviewed",
    }
    completion_path = campaign_directory / _COMPLETION_SEAL_NAME
    completion_payload = _canonical_bytes(completion_seal)
    completion_sha = _sha256(completion_payload)
    _write_progress(
        campaign_directory,
        campaign_id=campaign_id,
        session_id=session_id,
        started_at_utc=started_at,
        status="ready-to-seal",
        captures=captures,
        owned_attempts=owned_attempts,
        protocol=protocol,
        authorization=authorization,
        host_reservation_sha256=host_reservation_sha256,
        inputs=inputs,
        source_completion_seal_sha256=completion_sha,
        source_session_report_sha256=report_sha,
    )
    if _verify_capture_repository(repository_root) != protocol:
        raise PassiveInventoryV3CaptureError(
            "capture provenance changed before completion seal"
        )
    if _verify_live_capture_authorization(repository_root, protocol) != authorization:
        raise PassiveInventoryV3CaptureError(
            "live authorization changed before completion seal"
        )
    result = PassiveInventoryV3CaptureResult(
        campaign_directory=campaign_directory,
        campaign_id=campaign_id,
        session_id=session_id,
        source_session_report_path=report_path,
        source_session_report_sha256=report_sha,
        source_completion_seal_path=completion_path,
        source_completion_seal_sha256=completion_sha,
        capture_count=len(captures),
        protocol_lock_git_commit_sha=protocol.lock_commit_sha,
        capture_build_sha=protocol.capture_build_sha,
        capture_configuration_id=protocol.capture_configuration_id,
        capture_execution_head_sha=protocol.execution_head_sha,
        host_reservation_sha256=host_reservation_sha256,
        live_authorization_id=authorization.authorization_id,
        live_authorization_git_commit_sha=authorization.git_commit_sha,
    )
    # This canonical file plus sidecar is the final irreversible success
    # publication. No fallible check or write may follow it.
    _write_canonical_with_sidecar_exclusive(completion_path, completion_payload)
    return result


def _verify_capture_repository(repository_root: Path) -> _ProtocolBinding:
    root = repository_root.resolve(strict=True)
    actual_root = Path(_git(root, "rev-parse", "--show-toplevel")).resolve(
        strict=True
    )
    if actual_root != root:
        raise PassiveInventoryV3CaptureError(
            "repository_root is not the exact Git worktree root"
        )
    head = _git(root, "rev-parse", "HEAD")
    if _git(root, "status", "--porcelain=v1"):
        raise PassiveInventoryV3CaptureError(
            "worktree changes prevent source-owned capture provenance"
        )
    if _git(root, "rev-parse", "--is-shallow-repository") != "false":
        raise PassiveInventoryV3CaptureError(
            "full Git history is required for source-owned capture provenance"
        )
    if _git(root, "replace", "-l"):
        raise PassiveInventoryV3CaptureError(
            "Git replacement refs cannot establish capture provenance"
        )
    grafts = Path(_git(root, "rev-parse", "--git-path", "info/grafts"))
    if not grafts.is_absolute():
        grafts = root / grafts
    if grafts.is_file() and grafts.read_bytes().strip():
        raise PassiveInventoryV3CaptureError(
            "legacy Git grafts cannot establish capture provenance"
        )
    lock_path = root.joinpath(*_PROTOCOL_LOCK_PATH.parts)
    lock_payload = lock_path.read_bytes()
    try:
        decoded = json.loads(lock_payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PassiveInventoryV3CaptureError(
            "validation protocol lock is not canonical JSON"
        ) from exc
    if not isinstance(decoded, dict) or _canonical_bytes(decoded) != lock_payload:
        raise PassiveInventoryV3CaptureError(
            "validation protocol lock is not canonical JSON"
        )
    lock_digest = _sha256(lock_payload)
    sidecar = lock_path.with_suffix(".sha256").read_bytes()
    if sidecar != f"{lock_digest}  {lock_path.name}\n".encode("ascii"):
        raise PassiveInventoryV3CaptureError("validation protocol lock sidecar mismatch")
    lock_relative = _PROTOCOL_LOCK_PATH.as_posix()
    sidecar_relative = _PROTOCOL_LOCK_PATH.with_suffix(".sha256").as_posix()
    lock_commits = _git(
        root,
        "log",
        "--full-history",
        "--diff-filter=A",
        "--format=%H",
        "--reverse",
        "--",
        lock_relative,
    ).splitlines()
    sidecar_commits = _git(
        root,
        "log",
        "--full-history",
        "--diff-filter=A",
        "--format=%H",
        "--reverse",
        "--",
        sidecar_relative,
    ).splitlines()
    if len(lock_commits) != 1 or sidecar_commits != lock_commits:
        raise PassiveInventoryV3CaptureError(
            "validation protocol lock has ambiguous Git provenance"
        )
    lock_commit = lock_commits[0]
    if _git_bytes(
        root,
        "show",
        f"{lock_commit}:{lock_relative}",
    ) != lock_payload:
        raise PassiveInventoryV3CaptureError(
            "validation protocol lock changed after its Git introduction"
        )
    if _git_bytes(root, "show", f"{lock_commit}:{sidecar_relative}") != sidecar:
        raise PassiveInventoryV3CaptureError(
            "validation protocol lock sidecar changed after its Git introduction"
        )
    if _git_returncode(root, "merge-base", "--is-ancestor", lock_commit, head) != 0:
        raise PassiveInventoryV3CaptureError(
            "validation protocol lock is not an ancestor of capture HEAD"
        )
    for path in (lock_relative, sidecar_relative):
        _reject_post_lock_history(root, lock_commit, head, path)
    _require_exact_keys(
        decoded,
        {
            "activation_allowed",
            "approved_passive_capture",
            "frozen_candidate_head_sha",
            "live_validation_authorized",
            "preregistration_sha256",
            "protocol",
            "schema",
        },
        "validation protocol lock",
    )
    if (
        decoded.get("schema") != _PROTOCOL_LOCK_SCHEMA
        or decoded.get("activation_allowed") is not False
        or decoded.get("live_validation_authorized") is not False
        or decoded.get("frozen_candidate_head_sha") != _FROZEN_CANDIDATE_HEAD_SHA
        or decoded.get("preregistration_sha256") != _PREREGISTRATION_SHA256
    ):
        raise PassiveInventoryV3CaptureError(
            "validation protocol lock identity or authority changed"
        )
    protocol = _object(decoded, "protocol")
    capture = _object(decoded, "approved_passive_capture")
    _require_exact_keys(
        protocol,
        {"id", "locked_git_blobs", "source_commit_sha", "version"},
        "validation protocol",
    )
    if (
        protocol.get("id") != _PROTOCOL_ID
        or protocol.get("version") != _PROTOCOL_VERSION
    ):
        raise PassiveInventoryV3CaptureError("validation protocol identity changed")
    _require_exact_keys(
        capture,
        {"build_sha", "capture_configuration_id", "policy", "source_git_blobs"},
        "approved passive capture",
    )
    build_sha = _text(capture, "build_sha")
    configuration_id = _text(capture, "capture_configuration_id")
    if configuration_id != _APPROVED_CONFIGURATION_ID:
        raise PassiveInventoryV3CaptureError(
            "passive capture configuration identity changed"
        )
    if _git_returncode(
        root,
        "merge-base",
        "--is-ancestor",
        _FROZEN_CANDIDATE_HEAD_SHA,
        build_sha,
    ) != 0:
        raise PassiveInventoryV3CaptureError(
            "passive capture build is not descended from frozen V3"
        )
    parents = _git(root, "rev-list", "--parents", "-n", "1", lock_commit).split()
    if len(parents) != 2 or build_sha != parents[1] or (
        _text(protocol, "source_commit_sha") != build_sha
    ):
        raise PassiveInventoryV3CaptureError(
            "passive capture build is not the finalized pre-lock source commit"
        )
    _reject_post_build_executable_history(root, build_sha, head)
    lock_commit_paths = _git(
        root,
        "diff-tree",
        "--no-commit-id",
        "--name-only",
        "-r",
        lock_commit,
    ).splitlines()
    if tuple(lock_commit_paths) != (lock_relative, sidecar_relative):
        raise PassiveInventoryV3CaptureError(
            "protocol lock commit must add only the lock and its sidecar"
        )
    policy = _object(capture, "policy")
    expected_policy: dict[str, object] = {
        "all_owned_captures_retained": True,
        "detector_controls_capture_selection": False,
        "detector_controls_inclusion": False,
        "detector_controls_retry": False,
        "detector_controls_stage_advancement": False,
        "input_automation_allowed": False,
        "inventory_region": list(_SUPPORTED_REGION),
        "pixel_materialization": "fixed-bgra-row-slice-only",
        "pixel_value_transformation_allowed": False,
    }
    if dict(policy) != expected_policy:
        raise PassiveInventoryV3CaptureError(
            "approved passive capture policy is not the exact fixed capture-only policy"
        )
    protocol_entries = protocol.get("locked_git_blobs")
    capture_entries = capture.get("source_git_blobs")
    if not isinstance(protocol_entries, list) or not isinstance(
        capture_entries, list
    ):
        raise PassiveInventoryV3CaptureError("protocol Git blob sets must be lists")
    _verify_blob_entries(
        root,
        protocol_entries,
        expected_paths=_PROTOCOL_LOCKED_PATHS,
        source_commit=build_sha,
        lock_commit=lock_commit,
        head=head,
        label="validation protocol",
    )
    _verify_blob_entries(
        root,
        capture_entries,
        expected_paths=_CAPTURE_SOURCE_PATHS,
        source_commit=build_sha,
        lock_commit=lock_commit,
        head=head,
        label="approved passive capture",
    )
    expected_module = root / "src/mining_automation/validation/inventory_v3_capture.py"
    if Path(__file__).resolve(strict=True) != expected_module.resolve(strict=True):
        raise PassiveInventoryV3CaptureError(
            "passive capture runtime is not owned by the verified repository"
        )
    committed_at = _parse_utc(
        _git(root, "show", "-s", "--format=%cI", lock_commit)
    )
    source_committed_at = _parse_utc(
        _git(root, "show", "-s", "--format=%cI", build_sha)
    )
    if committed_at <= source_committed_at:
        raise PassiveInventoryV3CaptureError(
            "protocol lock Git time must be later than its source commit"
        )
    execution_head_committed_at = _parse_utc(
        _git(root, "show", "-s", "--format=%cI", head)
    )
    return _ProtocolBinding(
        execution_head_sha=head,
        execution_head_committed_at_utc=(
            execution_head_committed_at.isoformat(timespec="seconds").replace(
                "+00:00", "Z"
            )
        ),
        lock_commit_sha=lock_commit,
        lock_committed_at_utc=(
            committed_at.isoformat(timespec="seconds").replace("+00:00", "Z")
        ),
        lock_sha256=lock_digest,
        capture_build_sha=build_sha,
        capture_configuration_id=configuration_id,
    )


def _verify_live_capture_authorization(
    repository_root: Path,
    protocol: _ProtocolBinding,
) -> _LiveAuthorizationBinding:
    """Require a later source-owned authorization without mutating the lock."""

    root = repository_root.resolve(strict=True)
    path = root.joinpath(*_LIVE_AUTHORIZATION_PATH.parts)
    try:
        payload = path.read_bytes()
        decoded = json.loads(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PassiveInventoryV3CaptureError(
            "LIVE VALIDATION NOT YET AUTHORIZED: authorization registry unavailable"
        ) from exc
    if not isinstance(decoded, dict) or _canonical_bytes(decoded) != payload:
        raise PassiveInventoryV3CaptureError(
            "live campaign authorization registry is not canonical JSON"
        )
    _require_exact_keys(
        decoded,
        {"activation_allowed", "authorizations", "schema"},
        "live campaign authorization registry",
    )
    if (
        decoded.get("schema") != _LIVE_AUTHORIZATION_SCHEMA
        or decoded.get("activation_allowed") is not False
    ):
        raise PassiveInventoryV3CaptureError(
            "live campaign authorization registry identity changed"
        )
    entries = decoded.get("authorizations")
    if not isinstance(entries, list):
        raise PassiveInventoryV3CaptureError(
            "live campaign authorizations must be a list"
        )
    expected = {
        "capture_build_sha": protocol.capture_build_sha,
        "capture_configuration_id": protocol.capture_configuration_id,
        "protocol_lock_git_commit_sha": protocol.lock_commit_sha,
        "protocol_lock_sha256": protocol.lock_sha256,
        "status": _LIVE_AUTHORIZATION_STATUS,
    }
    if len(entries) != 1 or not isinstance(entries[0], Mapping):
        raise PassiveInventoryV3CaptureError(
            "LIVE VALIDATION NOT YET AUTHORIZED: exactly one source-owned "
            "authorization is required"
        )
    entry = entries[0]
    _require_exact_keys(
        entry,
        set(expected) | {"authorization_id"},
        "live campaign authorization 0",
    )
    authorization_id = _text(entry, "authorization_id")
    if len(authorization_id) != 64 or any(
        character not in "0123456789abcdef" for character in authorization_id
    ):
        raise PassiveInventoryV3CaptureError(
            "live campaign authorization_id must be 64 lowercase hex characters"
        )
    if any(entry.get(key) != value for key, value in expected.items()):
        raise PassiveInventoryV3CaptureError(
            "LIVE VALIDATION NOT YET AUTHORIZED: source-owned binding differs"
        )
    relative = _LIVE_AUTHORIZATION_PATH.as_posix()
    empty_registry = _canonical_bytes(
        {
            "activation_allowed": False,
            "authorizations": [],
            "schema": _LIVE_AUTHORIZATION_SCHEMA,
        }
    )
    if _git_bytes(
        root,
        "show",
        f"{protocol.capture_build_sha}:{relative}",
    ) != empty_registry:
        raise PassiveInventoryV3CaptureError(
            "pre-lock live campaign authorization registry was not empty"
        )
    committed_payload = _git_bytes(
        root,
        "show",
        f"{protocol.execution_head_sha}:{relative}",
    )
    if committed_payload != payload:
        raise PassiveInventoryV3CaptureError(
            "live campaign authorization is not committed at capture HEAD"
        )
    authorization_touches = _git(
        root,
        "log",
        "--full-history",
        "--format=%H",
        f"{protocol.lock_commit_sha}..{protocol.execution_head_sha}",
        "--",
        relative,
    ).splitlines()
    if len(authorization_touches) != 1:
        raise PassiveInventoryV3CaptureError(
            "live campaign authorization must have one post-lock Git change"
        )
    authorization_commit = authorization_touches[0]
    if _git_returncode(
        root,
        "merge-base",
        "--is-ancestor",
        protocol.lock_commit_sha,
        authorization_commit,
    ) != 0:
        raise PassiveInventoryV3CaptureError(
            "live campaign authorization predates the immutable protocol lock"
        )
    if _git_returncode(
        root,
        "merge-base",
        "--is-ancestor",
        authorization_commit,
        protocol.execution_head_sha,
    ) != 0:
        raise PassiveInventoryV3CaptureError(
            "live campaign authorization is not an ancestor of capture HEAD"
        )
    authorization_time = _parse_utc(
        _git(root, "show", "-s", "--format=%cI", authorization_commit)
    )
    if authorization_time <= _parse_utc(protocol.lock_committed_at_utc):
        raise PassiveInventoryV3CaptureError(
            "live campaign authorization must be committed after the protocol lock"
        )
    if _parse_utc(protocol.execution_head_committed_at_utc) < authorization_time:
        raise PassiveInventoryV3CaptureError(
            "capture execution HEAD Git time predates live authorization"
        )
    return _LiveAuthorizationBinding(
        authorization_id=authorization_id,
        git_commit_sha=authorization_commit,
        git_committed_at_utc=(
            authorization_time.isoformat(timespec="seconds").replace("+00:00", "Z")
        ),
        git_blob=_git(
            root,
            "rev-parse",
            f"{protocol.execution_head_sha}:{relative}",
        ),
    )


def _verify_blob_entries(
    root: Path,
    entries: Sequence[object],
    *,
    expected_paths: Sequence[str],
    source_commit: str,
    lock_commit: str,
    head: str,
    label: str,
) -> None:
    paths: list[str] = []
    for raw in entries:
        if not isinstance(raw, Mapping):
            raise PassiveInventoryV3CaptureError("Git blob entry must be an object")
        _require_exact_keys(raw, {"git_blob", "path"}, "Git blob entry")
        path = _text(raw, "path")
        blob = _text(raw, "git_blob")
        paths.append(path)
        if len(blob) != 40 or any(character not in "0123456789abcdef" for character in blob):
            raise PassiveInventoryV3CaptureError(f"{label} has an invalid Git blob")
        if (
            _git(root, "rev-parse", f"{source_commit}:{path}") != blob
            or _git(root, "rev-parse", f"{head}:{path}") != blob
        ):
            raise PassiveInventoryV3CaptureError(f"protocol-bound source changed: {path}")
        worktree_path = root.joinpath(*PurePosixPath(path).parts)
        if (
            not worktree_path.is_file()
            or worktree_path.is_symlink()
            or worktree_path.resolve(strict=True) != worktree_path.absolute()
            or worktree_path.read_bytes()
            != _git_bytes(root, "show", f"{head}:{path}")
        ):
            raise PassiveInventoryV3CaptureError(
                f"worktree bytes differ from protocol-bound Git source: {path}"
            )
        if _git(
            root,
            "log",
            "--full-history",
            "--format=%H",
            f"{lock_commit}..{head}",
            "--",
            path,
        ):
            raise PassiveInventoryV3CaptureError(
                f"protocol-bound source changed after lock: {path}"
            )
    if tuple(paths) != tuple(expected_paths):
        raise PassiveInventoryV3CaptureError(
            f"{label} does not bind the exact source-owned path set"
        )
    _reject_import_competitors(root, expected_paths)


def _reject_import_competitors(root: Path, paths: Sequence[str]) -> None:
    for path in paths:
        source = root.joinpath(*PurePosixPath(path).parts)
        if source.suffix != ".py":
            continue
        if source.name == "__init__.py":
            stem = source.parent.name
            parent = source.parent.parent
        else:
            stem = source.stem
            parent = source.parent
            if (parent / stem).exists():
                raise PassiveInventoryV3CaptureError(
                    f"competing import package exists beside locked source: {path}"
                )
        competitors = tuple(
            candidate
            for suffix in (".pyd", ".so", ".dll")
            for candidate in parent.glob(f"{stem}*{suffix}")
        )
        if competitors:
            raise PassiveInventoryV3CaptureError(
                f"competing native import exists beside locked source: {path}"
            )


def _reject_post_lock_history(
    root: Path,
    lock_commit: str,
    head: str,
    path: str,
) -> None:
    if _git(
        root,
        "log",
        "--full-history",
        "--format=%H",
        f"{lock_commit}..{head}",
        "--",
        path,
    ):
        raise PassiveInventoryV3CaptureError(
            f"protocol-bound source changed after lock: {path}"
        )


def _reject_post_build_executable_history(
    root: Path,
    build_sha: str,
    head: str,
) -> None:
    touched = _git(
        root,
        "log",
        "--full-history",
        "--format=%H",
        f"{build_sha}..{head}",
        "--",
        "src/mining_automation",
        "tools",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "sitecustomize.py",
        "usercustomize.py",
    )
    if touched:
        raise PassiveInventoryV3CaptureError(
            "source-owned executable paths changed after the approved capture build"
        )


def _persist_owned_frame(
    campaign_directory: Path,
    *,
    session_id: str,
    sequence_index: int,
    stage: str,
    captured_at_utc: str,
    frame: Frame,
    window_handle: int,
    window_class: str,
    windows_dpi: int | None,
    owned_attempts: list[dict[str, object]],
) -> _OwnedFrameBinding:
    """Persist every successful backend result before envelope decisions."""

    capture_id = (
        captured_at_utc.replace("-", "").replace(":", "")
        + f"-{sequence_index:03d}-{stage}"
    )
    relative_root = Path("captures") / f"{sequence_index:03d}-{stage}"
    capture_directory = campaign_directory / relative_root
    capture_directory.mkdir(parents=True, exist_ok=False)
    full_path = capture_directory / _FULL_FRAME_NAME
    full_relative = (relative_root / _FULL_FRAME_NAME).as_posix()
    full_sha = _sha256(frame.payload)
    attempt: dict[str, object] = {
        "capture_id": capture_id,
        "full_frame_attempt": {
            "path": full_relative,
            "sha256": full_sha,
            "size_bytes": len(frame.payload),
        },
        "owned_frame_report": None,
        "planned_stage_id": stage,
        "sequence_index": sequence_index,
        "status": "raw-write-attempted",
    }
    owned_attempts.append(attempt)
    _write_bytes_exclusive(full_path, frame.payload)
    attempt["status"] = "raw-retained"
    ownership = {
        "capture_id": capture_id,
        "captured_at_utc": captured_at_utc,
        "frame": {
            "frame_id": frame.frame_id,
            "height": frame.height,
            "path": full_relative,
            "pixel_format": frame.pixel_format.value,
            "sha256": full_sha,
            "size_bytes": len(frame.payload),
            "width": frame.width,
        },
        "planned_stage_id": stage,
        "schema": _OWNED_FRAME_SCHEMA,
        "sequence_index": sequence_index,
        "session_id": session_id,
        "status": "captured-unreviewed",
        "window": {
            "class": window_class,
            "handle": window_handle,
            "windows_dpi": windows_dpi,
        },
    }
    ownership_path = capture_directory / "owned-frame.json"
    ownership_payload = _canonical_bytes(ownership)
    _write_canonical_with_sidecar_exclusive(ownership_path, ownership_payload)
    attempt["owned_frame_report"] = {
        "path": (relative_root / ownership_path.name).as_posix(),
        "sha256": _sha256(ownership_payload),
    }
    attempt["status"] = "owned-frame-finalized"
    return _OwnedFrameBinding(
        capture_id=capture_id,
        capture_directory=capture_directory,
        relative_root=relative_root,
        full_frame_relative_path=full_relative,
        full_frame_sha256=full_sha,
        full_frame_size_bytes=len(frame.payload),
        ownership_report_relative_path=(relative_root / ownership_path.name).as_posix(),
        ownership_report_sha256=_sha256(ownership_payload),
    )


def _persist_capture(
    owned_frame: _OwnedFrameBinding,
    *,
    session_id: str,
    sequence_index: int,
    stage: str,
    captured_at_utc: str,
    environment: dict[str, object],
    full_frame: bytes,
) -> dict[str, object]:
    region = _fixed_region(full_frame)
    _write_bytes_exclusive(owned_frame.capture_directory / _REGION_NAME, region)
    region_relative = (owned_frame.relative_root / _REGION_NAME).as_posix()
    report = {
        "activation_allowed": False,
        "capture_environment": environment,
        "capture_id": owned_frame.capture_id,
        "capture_policy": {
            "backend_attempts": 1,
            "detector_executed": False,
            "input_automation_allowed": False,
            "pixel_materialization": "fixed-bgra-row-slice-only",
        },
        "captured_at_utc": captured_at_utc,
        "full_frame": {
            "height": _SUPPORTED_FRAME_HEIGHT,
            "path": owned_frame.full_frame_relative_path,
            "pixel_format": _SUPPORTED_PIXEL_FORMAT,
            "sha256": owned_frame.full_frame_sha256,
            "size_bytes": owned_frame.full_frame_size_bytes,
            "width": _SUPPORTED_FRAME_WIDTH,
        },
        "inventory_region": {
            "path": region_relative,
            "region": list(_SUPPORTED_REGION),
            "sha256": _sha256(region),
            "size_bytes": len(region),
        },
        "schema": _SOURCE_CAPTURE_SCHEMA,
        "session_id": session_id,
    }
    report_relative = (
        owned_frame.relative_root / _CAPTURE_REPORT_NAME
    ).as_posix()
    report_payload = _canonical_bytes(report)
    _write_canonical_with_sidecar_exclusive(
        owned_frame.capture_directory / _CAPTURE_REPORT_NAME,
        report_payload,
    )
    return {
        "capture_id": owned_frame.capture_id,
        "captured_at_utc": captured_at_utc,
        "capture_report": {
            "path": report_relative,
            "sha256": _sha256(report_payload),
        },
        "planned_stage_id": stage,
        "sequence_index": sequence_index,
    }


def _capture_environment(
    protocol: _ProtocolBinding,
    authorization: _LiveAuthorizationBinding,
    inputs: PassiveInventoryV3CaptureInputs,
    *,
    host_reservation_sha256: str,
    window_handle: int,
    window_class: str,
    dpi: int,
) -> dict[str, object]:
    if not window_class.strip():
        raise PassiveInventoryV3CaptureError("window class must be non-empty")
    return {
        "capture_build_sha": protocol.capture_build_sha,
        "capture_configuration_id": protocol.capture_configuration_id,
        "capture_execution_head_sha": protocol.execution_head_sha,
        "client_mode": inputs.client_mode,
        "frame": {
            "height": _SUPPORTED_FRAME_HEIGHT,
            "pixel_format": _SUPPORTED_PIXEL_FORMAT,
            "profile_id": _SUPPORTED_PROFILE_ID,
            "width": _SUPPORTED_FRAME_WIDTH,
        },
        "host_reservation_sha256": host_reservation_sha256,
        "live_authorization_id": authorization.authorization_id,
        "live_authorization_git_blob": authorization.git_blob,
        "live_authorization_git_commit_sha": authorization.git_commit_sha,
        "protocol_lock_git_commit_sha": protocol.lock_commit_sha,
        "python_isolated_mode": True,
        "python_isolated_source_cache": True,
        "python_no_site_mode": True,
        "renderer": inputs.renderer,
        "runelite_build": inputs.runelite_build,
        "theme": inputs.theme,
        "window_class": window_class,
        "window_handle": window_handle,
        "windows_dpi": dpi,
        "windows_scaling_percent": round(dpi * 100 / 96),
        "windows_version": platform.platform(),
    }


def _record_terminal_failure(
    context: _CampaignProgressContext,
    error: BaseException,
) -> None:
    message = str(error).strip() or "campaign aborted"
    failed_at_utc = _utc_timestamp()
    failure = {
        "error_type": type(error).__name__,
        "failed_at_utc": failed_at_utc,
        "message": message,
    }
    terminal = {
        "activation_allowed": False,
        "campaign_id": context.campaign_id,
        "captures": context.captures,
        "detector_executed": False,
        "failure": failure,
        "owned_attempts": context.owned_attempts,
        "schema": _TERMINAL_FAILURE_SCHEMA,
        "session_id": context.session_id,
        "status": "failed-retained",
    }
    try:
        _write_canonical_with_sidecar_exclusive(
            context.campaign_directory / _TERMINAL_FAILURE_NAME,
            _canonical_bytes(terminal),
        )
    except Exception:
        pass
    try:
        _write_progress(
            context.campaign_directory,
            campaign_id=context.campaign_id,
            session_id=context.session_id,
            started_at_utc=context.started_at_utc,
            status="failed-retained",
            captures=context.captures,
            owned_attempts=context.owned_attempts,
            protocol=context.protocol,
            authorization=context.authorization,
            host_reservation_sha256=context.host_reservation_sha256,
            inputs=context.inputs,
            failure=failure,
        )
    except Exception:
        # Preserve the triggering exception. An already-owned partial progress
        # write is never deleted or replaced merely to improve failure metadata.
        return


def _completion_commit_exists(campaign_directory: Path) -> bool:
    """Return true only for the fully published final success commit point."""

    path = campaign_directory / _COMPLETION_SEAL_NAME
    sidecar = path.with_suffix(path.suffix + ".sha256")
    try:
        payload = path.read_bytes()
        decoded = json.loads(payload)
        expected_sidecar = f"{_sha256(payload)}  {path.name}\n".encode("ascii")
        return (
            isinstance(decoded, dict)
            and _canonical_bytes(decoded) == payload
            and decoded.get("schema") == _COMPLETION_SEAL_SCHEMA
            and decoded.get("status") == "complete-not-reviewed"
            and sidecar.read_bytes() == expected_sidecar
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False


def _write_progress(
    campaign_directory: Path,
    *,
    campaign_id: str,
    session_id: str,
    started_at_utc: str,
    status: str,
    captures: list[dict[str, object]],
    owned_attempts: list[dict[str, object]],
    protocol: _ProtocolBinding,
    authorization: _LiveAuthorizationBinding,
    host_reservation_sha256: str,
    inputs: PassiveInventoryV3CaptureInputs,
    source_completion_seal_sha256: str | None = None,
    source_session_report_sha256: str | None = None,
    failure: Mapping[str, object] | None = None,
) -> None:
    progress = {
        "activation_allowed": False,
        "campaign_id": campaign_id,
        "capture_build_sha": protocol.capture_build_sha,
        "capture_configuration_id": protocol.capture_configuration_id,
        "capture_execution_head_sha": protocol.execution_head_sha,
        "captures": captures,
        "detector_executed": False,
        "failure": failure,
        "host_reservation_sha256": host_reservation_sha256,
        "live_authorization_id": authorization.authorization_id,
        "live_authorization_git_blob": authorization.git_blob,
        "live_authorization_git_commit_sha": authorization.git_commit_sha,
        "owned_attempts": owned_attempts,
        "operator": inputs.operator,
        "planned_stages": list(INDEPENDENT_CAPTURE_STAGES),
        "preregistration_sha256": _PREREGISTRATION_SHA256,
        "protocol_lock_git_commit_sha": protocol.lock_commit_sha,
        "schema": _PROGRESS_SCHEMA,
        "session_id": session_id,
        "source_completion_seal_sha256": source_completion_seal_sha256,
        "source_session_report_sha256": source_session_report_sha256,
        "started_at_utc": started_at_utc,
        "status": status,
    }
    path = campaign_directory / _PROGRESS_NAME
    temporary = campaign_directory / f".{_PROGRESS_NAME}.new"
    if temporary.exists():
        raise PassiveInventoryV3CaptureError(
            "ambiguous prior progress write blocks campaign continuation"
        )
    _write_bytes_exclusive(temporary, _canonical_bytes(progress))
    temporary.replace(path)


def _fixed_region(payload: bytes) -> bytes:
    expected = _SUPPORTED_FRAME_WIDTH * _SUPPORTED_FRAME_HEIGHT * 4
    if len(payload) != expected:
        raise PassiveInventoryV3CaptureError(
            "full frame cannot be cropped outside frozen capture geometry"
        )
    x, y, width, height = _SUPPORTED_REGION
    source_stride = _SUPPORTED_FRAME_WIDTH * 4
    row_size = width * 4
    result = bytearray(row_size * height)
    for row in range(height):
        source_start = (y + row) * source_stride + x * 4
        destination_start = row * row_size
        result[destination_start : destination_start + row_size] = payload[
            source_start : source_start + row_size
        ]
    return bytes(result)


def _allocate_campaign_directory(
    output_root: Path,
    *,
    reservation_root: Path,
    authorization: _LiveAuthorizationBinding,
    protocol: _ProtocolBinding,
) -> tuple[Path, str, str]:
    reservation_root.mkdir(parents=True, exist_ok=True)
    reservation = {
        "authorization_id": authorization.authorization_id,
        "capture_build_sha": protocol.capture_build_sha,
        "capture_configuration_id": protocol.capture_configuration_id,
        "live_authorization_git_commit_sha": authorization.git_commit_sha,
        "protocol_lock_git_commit_sha": protocol.lock_commit_sha,
        "protocol_lock_sha256": protocol.lock_sha256,
        "repository": _REPOSITORY_ID,
        "schema": _HOST_RESERVATION_SCHEMA,
        "status": "reserved-and-irrevocably-consumed",
    }
    reservation_payload = _canonical_bytes(reservation)
    # Protocol v1 permits exactly one attempt under a lock, even if a later
    # branch tries to substitute a different authorization identifier.
    reservation_path = reservation_root / f"{protocol.lock_sha256}.json"
    try:
        _write_bytes_exclusive(reservation_path, reservation_payload)
    except PassiveInventoryV3CaptureError as exc:
        raise PassiveInventoryV3CaptureError(
            "source-owned live authorization was already reserved or consumed "
            "on this host"
        ) from exc
    output_root.mkdir(parents=True, exist_ok=True)
    candidate = output_root / authorization.authorization_id
    try:
        candidate.mkdir(exist_ok=False)
    except FileExistsError as exc:
        raise PassiveInventoryV3CaptureError(
            "source-owned live authorization was already reserved or consumed"
        ) from exc
    session_id = f"inventory-v3-independent-{authorization.authorization_id}"
    return candidate, session_id, _sha256(reservation_payload)


def _approved_output_root(repository_root: Path) -> Path:
    root = repository_root.resolve(strict=True)
    output = root.joinpath(*_PRIVATE_OUTPUT_RELATIVE.parts)
    diagnostics = output.parent
    if diagnostics.exists() and diagnostics.resolve(strict=True) != diagnostics:
        raise PassiveInventoryV3CaptureError(
            "private diagnostics root cannot be redirected through a link"
        )
    if output.exists() and output.resolve(strict=True) != output:
        raise PassiveInventoryV3CaptureError(
            "private campaign root cannot be redirected through a link"
        )
    return output


def _approved_host_reservation_root() -> Path:
    """Return the source-owned host-global reservation namespace.

    The path is deliberately outside any Git worktree so a second clone or
    worktree cannot reuse the same one-shot live authorization on this host.
    """

    if sys.platform != "win32":
        raise PassiveInventoryV3CaptureError(
            "eligible Inventory V3 capture requires native Windows"
        )
    buffer = ctypes.create_unicode_buffer(32768)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    sh_get_folder_path = cast(
        Callable[[object, int, object, int, object], int],
        shell32.SHGetFolderPathW,
    )
    # CSIDL_LOCAL_APPDATA / SHGFP_TYPE_CURRENT. This OS lookup is not affected
    # by USERPROFILE/HOME changes between invocations.
    result = sh_get_folder_path(None, 0x001C, None, 0, buffer)
    if result != 0 or not buffer.value:
        raise PassiveInventoryV3CaptureError(
            "Windows Local AppData location is unavailable for host reservation"
        )
    base = Path(buffer.value)
    result_path = base.joinpath(*_HOST_RESERVATION_RELATIVE.parts)
    for candidate in (base, result_path.parent, result_path):
        if candidate.exists() and (
            candidate.is_symlink()
            or candidate.resolve(strict=True) != candidate.absolute()
        ):
            raise PassiveInventoryV3CaptureError(
                "host reservation namespace cannot be redirected through a link"
            )
    return result_path


def _require_isolated_mode() -> None:
    if (
        not sys.flags.isolated
        or not sys.flags.no_site
        or sys.pycache_prefix is None
    ):
        raise PassiveInventoryV3CaptureError(
            "eligible Inventory V3 capture requires the locked Python -I -S "
            "launcher with an isolated source cache"
        )
    main_file = getattr(sys.modules.get("__main__"), "__file__", None)
    try:
        module_path = Path(__file__).resolve(strict=True)
        repository_root = module_path.parents[3]
        launcher = repository_root.joinpath(*_CAPTURE_LAUNCHER_PATH.parts).resolve(
            strict=True
        )
        argv_launcher = Path(sys.argv[0]).resolve(strict=True)
        main_launcher = (
            Path(main_file).resolve(strict=True)
            if isinstance(main_file, str)
            else None
        )
    except (IndexError, OSError, TypeError):
        launcher = None
        argv_launcher = None
        main_launcher = None
    if launcher is None or argv_launcher != launcher or main_launcher != launcher:
        raise PassiveInventoryV3CaptureError(
            "eligible Inventory V3 capture requires direct execution of the fixed "
            "source-owned capture launcher"
        )


def _content_bound_campaign_id(session_id: str) -> str:
    value = {
        "preregistration_sha256": _PREREGISTRATION_SHA256,
        "session_id": session_id,
    }
    return "inventory-positive-v3-campaign-" + _sha256(_canonical_bytes(value))[:24]


def _require_after_protocol_lock(timestamp: str, protocol: _ProtocolBinding) -> None:
    if _parse_utc(timestamp) <= _parse_utc(protocol.lock_committed_at_utc):
        raise PassiveInventoryV3CaptureError(
            "capture timestamp must follow the immutable protocol-lock commit"
        )


def _require_after_live_authorization(
    timestamp: str,
    authorization: _LiveAuthorizationBinding,
) -> None:
    if _parse_utc(timestamp) <= _parse_utc(authorization.git_committed_at_utc):
        raise PassiveInventoryV3CaptureError(
            "capture timestamp must follow the live-authorization Git commit"
        )


def _require_after_execution_head(
    timestamp: str,
    protocol: _ProtocolBinding,
) -> None:
    if _parse_utc(timestamp) <= _parse_utc(
        protocol.execution_head_committed_at_utc
    ):
        raise PassiveInventoryV3CaptureError(
            "capture timestamp must follow the exact execution HEAD commit"
        )


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PassiveInventoryV3CaptureError("invalid capture UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PassiveInventoryV3CaptureError("capture UTC timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")


def _write_bytes_exclusive(path: Path, payload: bytes) -> None:
    try:
        with path.open("xb", buffering=0) as handle:
            written = 0
            while written < len(payload):
                count = handle.write(payload[written:])
                if count is None or count <= 0:
                    raise PassiveInventoryV3CaptureError(
                        f"short write while preserving {path.name}"
                    )
                written += count
    except FileExistsError as exc:
        raise PassiveInventoryV3CaptureError(
            f"refusing to replace existing capture evidence: {path}"
        ) from exc


def _write_canonical_with_sidecar_exclusive(path: Path, payload: bytes) -> None:
    _write_bytes_exclusive(path, payload)
    digest = _sha256(payload)
    _write_bytes_exclusive(
        path.with_suffix(path.suffix + ".sha256"),
        f"{digest}  {path.name}\n".encode("ascii"),
    )


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(root), *arguments),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise PassiveInventoryV3CaptureError(f"Git command failed: {detail}")
    return completed.stdout.strip()


def _git_bytes(root: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ("git", "-C", str(root), *arguments),
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).decode(
            "utf-8", errors="replace"
        ).strip()
        raise PassiveInventoryV3CaptureError(f"Git command failed: {detail}")
    return completed.stdout


def _git_returncode(root: Path, *arguments: str) -> int:
    return subprocess.run(
        ("git", "-C", str(root), *arguments),
        check=False,
        capture_output=True,
    ).returncode


def _object(value: Mapping[str, object], key: str) -> Mapping[str, object]:
    result = value.get(key)
    if not isinstance(result, Mapping):
        raise PassiveInventoryV3CaptureError(f"{key} must be an object")
    return result


def _text(value: Mapping[str, object], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise PassiveInventoryV3CaptureError(f"{key} must be non-empty text")
    return result


def _require_exact_keys(
    value: Mapping[str, object], expected: set[str], label: str
) -> None:
    if set(value) != expected:
        raise PassiveInventoryV3CaptureError(
            f"{label} keys differ: expected={sorted(expected)}, actual={sorted(value)}"
        )


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _new_source_owned_backend() -> WindowsCaptureBackend:
    return WindowsCaptureBackend(RealWin32Api())


def _acknowledge_stage(stage: str, index: int, total: int, path: Path) -> None:
    print(f"\n[{index}/{total}] Prepare unverified stage: {stage}")
    print(f"Evidence directory: {path.resolve()}")
    input("Press Enter to capture exactly once, or Ctrl+C to abort: ")
