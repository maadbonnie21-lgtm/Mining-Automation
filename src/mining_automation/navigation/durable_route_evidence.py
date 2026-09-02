"""Append-only filesystem transactions for offline synthetic route evidence.

Acquisition and independent review intentionally use different roots and
different mutable capabilities.  A transaction can only create a fresh root;
it cannot resume, adopt, overwrite, retry, reverse, or remove evidence.  The
terminal manifest is written last, and strict read-only intake remains the
authority boundary.  The pathname-based writer requires a trusted, non-hostile
dedicated parent namespace and is not eligible to acquire future real release
evidence without a separately reviewed handle-anchored writer.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from threading import RLock
from types import MappingProxyType
from typing import Final, Literal, NoReturn, Protocol, SupportsIndex

from .checkpoint_evidence import CheckpointDetector
from .contracts import (
    CheckpointDetection,
    CheckpointDetectorIdentity,
    CheckpointProfileIdentity,
    RouteIdentity,
    Sha256Digest,
)
from .passive_campaign import (
    PassiveCampaignFailureReason,
    PassiveCampaignFinalizationError,
    PassiveCampaignPhase,
    PassiveCampaignProgress,
    PassiveCampaignSequencer,
    PassiveCaptureRequest,
    PassiveCaptureSource,
    PassiveMonotonicClock,
)
from .route_evidence import (
    SYNTHETIC_ROUTE_EVIDENCE_ROLE,
    FinalizedRouteEvidencePackage,
    OwnedRouteEvidenceCase,
    RouteEvidenceCampaignPlan,
    RouteEvidenceCaptureBuildIdentity,
    RouteEvidenceCaseTruth,
    RouteEvidenceIntegrityError,
    RouteEvidenceLoadExpectation,
    RouteEvidenceReview,
    RouteEvidenceVerificationReport,
    canonical_route_evidence_bytes,
    verify_synthetic_route_evidence,
)
from .route_evidence_loader import (
    FINALIZED_PACKAGE_FILENAME,
    INDEPENDENT_REVIEW_FILENAME,
    RouteEvidenceFilesystemExpectation,
    _assert_exact_tree,
    _cross_handle_identity,
    _directory_signature_identity,
    _exact_keys,
    _FileSnapshot,
    _identifier,
    _lstat,
    _parse_package,
    _parse_review,
    _read_owned_file,
    _reject_duplicate_artifact_content,
    _root_path,
    _stable_tree_identity,
    _stat_signature,
    _strict_canonical_object,
    _string,
    _TreeEntry,
    _validate_detector_reports,
)

__all__ = [
    "ACQUISITION_FINALIZATION_FILENAME",
    "ACQUISITION_PLAN_FILENAME",
    "ACQUISITION_STOP_FILENAME",
    "DURABLE_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE",
    "DURABLE_WRITER_NAMESPACE_CONTRACT",
    "REVIEW_FINALIZATION_FILENAME",
    "REVIEW_PLAN_FILENAME",
    "REVIEW_STOP_FILENAME",
    "DurableAcquisitionFilesystemExpectation",
    "DurableAcquisitionPhase",
    "DurableAcquisitionReceipt",
    "DurableAcquisitionTransaction",
    "DurableEvidenceCollisionError",
    "DurableEvidenceError",
    "DurableEvidenceStateError",
    "DurableReviewPhase",
    "DurableReviewReceipt",
    "DurableReviewTransaction",
    "DurableRouteEvidenceFilesystemExpectation",
    "VerifiedDurableAcquisition",
    "begin_durable_acquisition",
    "begin_durable_review",
    "load_and_verify_durable_synthetic_route_evidence",
    "load_durable_acquisition",
]

ACQUISITION_PLAN_FILENAME: Final[str] = "campaign-plan.json"
ACQUISITION_FINALIZATION_FILENAME: Final[str] = "acquisition-finalization.json"
ACQUISITION_STOP_FILENAME: Final[str] = "acquisition-stop.json"
REVIEW_PLAN_FILENAME: Final[str] = "review-plan.json"
REVIEW_FINALIZATION_FILENAME: Final[str] = "review-finalization.json"
REVIEW_STOP_FILENAME: Final[str] = "review-stop.json"
DURABLE_WRITER_NAMESPACE_CONTRACT: Final[str] = "trusted_non_hostile_dedicated_parent_namespace_v1"
DURABLE_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE: Final[Literal[False]] = False

_ACQUISITION_REQUEST_SCHEMA: Final[str] = "fixed-route-durable-capture-request-v1"
_ACQUISITION_CASE_SCHEMA: Final[str] = "fixed-route-durable-owned-case-v1"
_ACQUISITION_FINALIZATION_SCHEMA: Final[str] = "fixed-route-durable-acquisition-finalization-v1"
_ACQUISITION_STOP_SCHEMA: Final[str] = "fixed-route-durable-acquisition-stop-v1"
_REVIEW_PLAN_SCHEMA: Final[str] = "fixed-route-durable-review-plan-v2"
_REVIEW_TRUTH_SCHEMA: Final[str] = "fixed-route-durable-review-truth-v1"
_REVIEW_FINALIZATION_SCHEMA: Final[str] = "fixed-route-durable-review-finalization-v1"
_REVIEW_STOP_SCHEMA: Final[str] = "fixed-route-durable-review-stop-v1"
_PHYSICAL_IDENTITY_SCHEMA: Final[str] = "fixed-route-durable-physical-tree-identity-v2"
_MAX_MANIFEST_BYTES: Final[int] = 16 * 1024 * 1024
_MAX_ARTIFACT_BYTES: Final[int] = 512 * 1024 * 1024
_FACTORY_TOKEN: Final[object] = object()


class DurableEvidenceError(RuntimeError):
    """A durable transaction could not preserve its fail-closed contract."""


class DurableEvidenceCollisionError(DurableEvidenceError):
    """A transaction id or immutable path was already claimed."""


class DurableEvidenceStateError(DurableEvidenceError):
    """An operation was attempted after the durable head stopped or finalized."""


class _DurableNamespace(Protocol):
    """Navigation-private append-only namespace used by the transaction state machines."""

    @property
    def root(self) -> Path: ...

    @property
    def root_identity(self) -> tuple[int, ...]: ...

    def mkdir(self, relative_path: str) -> None: ...

    def mkdir_child(self, parent: str, child: str) -> str: ...

    def write(
        self,
        relative_path: str,
        payload: bytes,
        *,
        max_bytes: int = _MAX_MANIFEST_BYTES,
    ) -> Sha256Digest: ...

    def read_owned(self, relative_path: str, max_bytes: int) -> bytes: ...

    def assert_exact_tree(self, expected_files: set[str]) -> None: ...

    def physical_identity_sha256(self, expected_files: set[str]) -> Sha256Digest: ...

    def close(self) -> None: ...


_NamespaceFactory = Callable[[Path], _DurableNamespace]


class DurableAcquisitionPhase(StrEnum):
    READY_FOR_REQUEST = "ready_for_request"
    AWAITING_CAPTURE = "awaiting_capture"
    COMPLETE = "complete"
    FINALIZED = "finalized"
    STOPPED = "stopped"


class DurableReviewPhase(StrEnum):
    READY_FOR_TRUTH = "ready_for_truth"
    COMPLETE = "complete"
    FINALIZED = "finalized"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class DurableAcquisitionFilesystemExpectation(RouteEvidenceLoadExpectation):
    """Caller-owned pins for one finalized acquisition root."""

    acquisition_journal_head_sha256: Sha256Digest
    acquisition_finalization_sha256: Sha256Digest
    acquisition_physical_identity_sha256: Sha256Digest

    def __post_init__(self) -> None:
        super(DurableAcquisitionFilesystemExpectation, self).__post_init__()
        if not isinstance(self.acquisition_journal_head_sha256, Sha256Digest):
            raise ValueError("durable acquisition journal head must be Sha256Digest")
        if not isinstance(self.acquisition_finalization_sha256, Sha256Digest):
            raise ValueError("durable acquisition finalization must be Sha256Digest")
        if not isinstance(self.acquisition_physical_identity_sha256, Sha256Digest):
            raise ValueError("durable acquisition physical identity must be Sha256Digest")


@dataclass(frozen=True, slots=True)
class DurableRouteEvidenceFilesystemExpectation(RouteEvidenceFilesystemExpectation):
    """External pins for separate finalized acquisition and review roots."""

    acquisition_journal_head_sha256: Sha256Digest
    acquisition_finalization_sha256: Sha256Digest
    review_id: str
    review_plan_sha256: Sha256Digest
    review_journal_head_sha256: Sha256Digest
    review_finalization_sha256: Sha256Digest
    acquisition_physical_identity_sha256: Sha256Digest
    review_physical_identity_sha256: Sha256Digest

    def __post_init__(self) -> None:
        super(DurableRouteEvidenceFilesystemExpectation, self).__post_init__()
        for value, name in (
            (self.acquisition_journal_head_sha256, "acquisition journal head"),
            (self.acquisition_finalization_sha256, "acquisition finalization"),
            (self.review_plan_sha256, "review plan"),
            (self.review_journal_head_sha256, "review journal head"),
            (self.review_finalization_sha256, "review finalization"),
            (self.acquisition_physical_identity_sha256, "acquisition physical identity"),
            (self.review_physical_identity_sha256, "review physical identity"),
        ):
            if not isinstance(value, Sha256Digest):
                raise ValueError(f"durable expectation {name} must be Sha256Digest")
        _identifier(self.review_id, "durable expectation review_id")


@dataclass(frozen=True, slots=True)
class _AcquisitionFilesystemIdentity:
    root_identity: tuple[int, ...]
    tree_identity: tuple[tuple[str, bool, tuple[int, ...]], ...]


@dataclass(frozen=True, slots=True)
class VerifiedDurableAcquisition:
    """Detached data returned by strict acquisition intake; never input authority."""

    package: FinalizedRouteEvidencePackage
    artifacts: Mapping[str, bytes]
    expectation: DurableAcquisitionFilesystemExpectation
    filesystem_identity: _AcquisitionFilesystemIdentity
    activation_allowed: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        if type(self.package) is not FinalizedRouteEvidencePackage:
            raise ValueError("verified acquisition requires an exact package")
        if type(self.expectation) is not DurableAcquisitionFilesystemExpectation:
            raise ValueError("verified acquisition requires exact caller pins")
        if type(self.filesystem_identity) is not _AcquisitionFilesystemIdentity:
            raise ValueError("verified acquisition requires an exact filesystem identity")
        if not isinstance(self.artifacts, Mapping):
            raise ValueError("verified acquisition artifacts must be a mapping")


@dataclass(frozen=True, slots=True)
class DurableAcquisitionReceipt:
    """Writer result; callers must persist and independently supply its pins."""

    expectation: DurableAcquisitionFilesystemExpectation
    activation_allowed: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)


@dataclass(frozen=True, slots=True)
class DurableReviewReceipt:
    """Digest receipt for one separately finalized review transaction."""

    review_id: str
    reviewer_id: str
    independent_review_sha256: Sha256Digest
    review_plan_sha256: Sha256Digest
    review_journal_head_sha256: Sha256Digest
    review_finalization_sha256: Sha256Digest
    review_physical_identity_sha256: Sha256Digest
    report: RouteEvidenceVerificationReport
    activation_allowed: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        _identifier(self.review_id, "review receipt review_id")
        _identifier(self.reviewer_id, "review receipt reviewer_id")
        if any(
            not isinstance(value, Sha256Digest)
            for value in (
                self.independent_review_sha256,
                self.review_plan_sha256,
                self.review_journal_head_sha256,
                self.review_finalization_sha256,
                self.review_physical_identity_sha256,
            )
        ):
            raise ValueError("review receipt digests must be Sha256Digest")
        if not isinstance(self.report, RouteEvidenceVerificationReport):
            raise ValueError("review receipt report has the wrong type")


@dataclass(frozen=True, slots=True)
class _OwnedDirectory:
    relative_path: str
    identity: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _OwnedFile:
    relative_path: str
    signature: tuple[int, ...]


def _writer_directory_identity(signature: tuple[int, ...]) -> tuple[int, ...]:
    """Ignore metadata that append-only child creation legitimately changes."""

    return (
        signature[0],
        signature[1],
        signature[2],
        signature[7],
        signature[8],
    )


def _physical_object_identity(
    signature: tuple[int, ...],
    *,
    is_directory: bool,
) -> tuple[int, ...]:
    """Stable physical fields captured only after the append-only tree is complete."""

    stable = (
        signature[0],
        signature[1],
        signature[2],
        signature[3],
        signature[7],
        signature[8],
    )
    if is_directory:
        return stable
    # POSIX filesystems may immediately reuse an unlinked file's inode.  A
    # completed regular file's status-change epoch distinguishes that new
    # object without depending on asynchronously updated directory times.
    return (*stable, signature[6])


def _physical_identity_sha256(
    root_signature: tuple[int, ...],
    tree: Mapping[str, _TreeEntry],
) -> Sha256Digest:
    payload = canonical_route_evidence_bytes(
        {
            "entries": [
                {
                    "is_directory": entry.is_directory,
                    "physical_identity": list(
                        _physical_object_identity(
                            entry.signature,
                            is_directory=entry.is_directory,
                        )
                    ),
                    "relative_path": relative,
                }
                for relative, entry in sorted(tree.items())
            ],
            "root_physical_identity": list(
                _physical_object_identity(root_signature, is_directory=True)
            ),
            "schema": _PHYSICAL_IDENTITY_SCHEMA,
        }
    )
    return Sha256Digest.from_bytes(payload)


def _absolute_path_once(value: str | os.PathLike[str], field_name: str) -> Path:
    raw = os.fspath(value)
    if type(raw) is not str:
        raise TypeError(f"{field_name} must resolve to a text filesystem path")
    return Path(raw).absolute()


def _assert_existing_directory_chain(directory: Path, field_name: str) -> Path:
    chain: list[Path] = []
    current = directory
    while True:
        chain.append(current)
        if current.parent == current:
            break
        current = current.parent
    for component in reversed(chain):
        component_stat = _lstat(component, f"{field_name} component")
        if not stat.S_ISDIR(component_stat.st_mode):
            raise DurableEvidenceError(f"{field_name} component is not a directory")
    try:
        return directory.resolve(strict=True)
    except OSError as exc:
        raise DurableEvidenceError(f"cannot resolve {field_name}: {exc}") from exc


def _paths_overlap(first: Path, second: Path) -> bool:
    try:
        first.relative_to(second)
        return True
    except ValueError:
        pass
    try:
        second.relative_to(first)
        return True
    except ValueError:
        return False


def _intake_root(path: Path, field_name: str) -> tuple[Path, tuple[int, ...]]:
    try:
        resolved_parent = _assert_existing_directory_chain(path.parent, f"{field_name} parent")
    except DurableEvidenceError as exc:
        raise RouteEvidenceIntegrityError(f"invalid {field_name} ancestry: {exc}") from exc
    root, signature = _root_path(path)
    try:
        resolved_root = root.resolve(strict=True)
    except OSError as exc:
        raise RouteEvidenceIntegrityError(f"cannot resolve {field_name}: {exc}") from exc
    if resolved_root.parent != resolved_parent:
        raise RouteEvidenceIntegrityError(f"{field_name} escaped its bound parent")
    return root, signature


class _ExclusiveNamespace:
    """Fresh pathname namespace beneath a trusted, non-hostile dedicated parent."""

    __slots__ = ("_directories", "_files", "_root", "_root_identity")

    def __init__(self, root: str | os.PathLike[str]) -> None:
        path = _absolute_path_once(root, "durable transaction root")
        resolved_parent = _assert_existing_directory_chain(
            path.parent,
            "durable transaction parent",
        )
        try:
            path.mkdir(exist_ok=False)
        except FileExistsError as exc:
            raise DurableEvidenceCollisionError(
                "durable transaction root was already claimed"
            ) from exc
        except OSError as exc:
            raise DurableEvidenceError(f"cannot reserve durable transaction root: {exc}") from exc
        root_path, signature = _root_path(path)
        if root_path.resolve(strict=True).parent != resolved_parent:
            raise DurableEvidenceError("durable transaction root escaped its bound parent")
        self._root = root_path
        self._root_identity = _writer_directory_identity(signature)
        self._directories: dict[str, _OwnedDirectory] = {}
        self._files: dict[str, _OwnedFile] = {}

    @property
    def root(self) -> Path:
        return self._root

    @property
    def root_identity(self) -> tuple[int, ...]:
        """Physical identity bound into the terminal manifest before its write."""

        return self._root_identity

    def _assert_root(self) -> None:
        current = _lstat(self._root, "durable transaction root")
        if _writer_directory_identity(_stat_signature(current)) != self._root_identity:
            raise DurableEvidenceError("durable transaction root was replaced")
        for relative, owned in self._directories.items():
            current_directory = _lstat(
                self._root / PurePosixPath(relative),
                f"owned durable directory {relative!r}",
            )
            if not stat.S_ISDIR(current_directory.st_mode) or (
                _writer_directory_identity(_stat_signature(current_directory)) != owned.identity
            ):
                raise DurableEvidenceError(f"owned durable directory was replaced: {relative}")
        for relative, owned_file in self._files.items():
            current_file = _lstat(
                self._root / PurePosixPath(relative),
                f"owned durable file {relative!r}",
            )
            if (
                not stat.S_ISREG(current_file.st_mode)
                or current_file.st_nlink != 1
                or _stat_signature(current_file) != owned_file.signature
            ):
                raise DurableEvidenceError(f"owned durable file was replaced: {relative}")

    def mkdir(self, relative_path: str) -> None:
        self._assert_root()
        pure = PurePosixPath(relative_path)
        if pure.is_absolute() or len(pure.parts) != 1 or pure.as_posix() != relative_path:
            raise DurableEvidenceError("durable directories require one portable component")
        path = self._root / relative_path
        try:
            path.mkdir(exist_ok=False)
        except FileExistsError as exc:
            raise DurableEvidenceCollisionError(
                f"durable directory was already claimed: {relative_path}"
            ) from exc
        except OSError as exc:
            raise DurableEvidenceError(
                f"cannot create durable directory {relative_path}: {exc}"
            ) from exc
        signature = _lstat(path, f"new durable directory {relative_path!r}")
        if not stat.S_ISDIR(signature.st_mode):
            raise DurableEvidenceError("new durable directory changed type")
        self._directories[relative_path] = _OwnedDirectory(
            relative_path,
            _writer_directory_identity(_stat_signature(signature)),
        )
        self._assert_root()

    def mkdir_child(self, parent: str, child: str) -> str:
        self._assert_root()
        if parent not in self._directories:
            raise DurableEvidenceError("durable child parent is not invocation-owned")
        pure = PurePosixPath(child)
        if pure.is_absolute() or len(pure.parts) != 1 or pure.as_posix() != child:
            raise DurableEvidenceError("durable child requires one portable component")
        relative = f"{parent}/{child}"
        path = self._root / PurePosixPath(relative)
        try:
            path.mkdir(exist_ok=False)
        except FileExistsError as exc:
            raise DurableEvidenceCollisionError(
                f"durable directory was already claimed: {relative}"
            ) from exc
        except OSError as exc:
            raise DurableEvidenceError(
                f"cannot create durable directory {relative}: {exc}"
            ) from exc
        signature = _lstat(path, f"new durable directory {relative!r}")
        if not stat.S_ISDIR(signature.st_mode):
            raise DurableEvidenceError("new durable child directory changed type")
        self._directories[relative] = _OwnedDirectory(
            relative,
            _writer_directory_identity(_stat_signature(signature)),
        )
        self._assert_root()
        return relative

    def write(
        self,
        relative_path: str,
        payload: bytes,
        *,
        max_bytes: int = _MAX_MANIFEST_BYTES,
    ) -> Sha256Digest:
        if type(payload) is not bytes:
            raise TypeError("durable payload must be exact immutable bytes")
        if type(max_bytes) is not int or max_bytes < 1:
            raise ValueError("durable write limit must be a positive exact integer")
        if len(payload) > max_bytes:
            raise DurableEvidenceError(f"durable payload exceeds its write limit: {relative_path}")
        self._assert_root()
        pure = PurePosixPath(relative_path)
        if pure.is_absolute() or pure.as_posix() != relative_path or not pure.parts:
            raise DurableEvidenceError("durable file path must be safe relative POSIX syntax")
        parent = pure.parent.as_posix()
        if parent != "." and parent not in self._directories:
            raise DurableEvidenceError("durable file parent is not invocation-owned")
        path = self._root / pure
        try:
            handle = path.open("xb+", buffering=0)
        except FileExistsError as exc:
            raise DurableEvidenceCollisionError(
                f"immutable durable file was already claimed: {relative_path}"
            ) from exc
        except OSError as exc:
            raise DurableEvidenceError(
                f"cannot create durable file {relative_path}: {exc}"
            ) from exc
        try:
            view = memoryview(payload)
            offset = 0
            while offset < len(view):
                written = handle.write(view[offset:])
                if written is None or written <= 0:
                    raise DurableEvidenceError(f"short durable write: {relative_path}")
                offset += written
            handle.flush()
            os.fsync(handle.fileno())
            opened = os.fstat(handle.fileno())
            handle.seek(0)
            readback = handle.read(len(payload) + 1)
            after_open = os.fstat(handle.fileno())
        except BaseException:
            handle.close()
            raise
        handle.close()
        after_path = _lstat(path, f"new durable file {relative_path!r}")
        if (
            not stat.S_ISREG(after_path.st_mode)
            or after_path.st_nlink != 1
            or _stat_signature(opened) != _stat_signature(after_open)
            or _cross_handle_identity(opened) != _cross_handle_identity(after_path)
            or readback != payload
        ):
            raise DurableEvidenceError(
                f"durable file changed during exclusive write: {relative_path}"
            )
        self._files[relative_path] = _OwnedFile(
            relative_path,
            _stat_signature(after_path),
        )
        self._assert_root()
        return Sha256Digest.from_bytes(payload)

    def read_owned(self, relative_path: str, max_bytes: int) -> bytes:
        """Read one invocation-owned file while retaining the pathname contract."""

        self._assert_root()
        snapshot = _read_owned_file(self._root, relative_path, max_bytes)
        self._assert_root()
        return snapshot.payload

    def assert_exact_tree(self, expected_files: set[str]) -> None:
        """Require the exact invocation-owned prefix before terminal publication."""

        self._assert_root()
        _assert_exact_tree(self._root, expected_files)
        self._assert_root()

    def physical_identity_sha256(self, expected_files: set[str]) -> Sha256Digest:
        """Pin the complete post-terminal physical tree for strict later intake."""

        self._assert_root()
        initial_tree = _assert_exact_tree(self._root, expected_files)
        initial_root = _stat_signature(_lstat(self._root, "durable transaction root"))
        initial = _physical_identity_sha256(initial_root, initial_tree)
        final_tree = _assert_exact_tree(self._root, expected_files)
        final_root = _stat_signature(_lstat(self._root, "durable transaction root"))
        if _physical_identity_sha256(final_root, final_tree) != initial:
            raise DurableEvidenceError("durable physical identity changed while it was pinned")
        self._assert_root()
        return initial

    def close(self) -> None:
        """The pathname writer owns no retained operating-system handles."""


def _canonical(value: Mapping[str, object]) -> bytes:
    return canonical_route_evidence_bytes(value)


def _utc(value: object, field_name: str) -> datetime:
    if type(value) is not str or not value.endswith("Z"):
        raise ValueError(f"{field_name} must be an ISO-8601 UTC string ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{field_name} must be valid ISO-8601 UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError(f"{field_name} must use UTC")
    return parsed


def _identity_json(plan: RouteEvidenceCampaignPlan) -> dict[str, object]:
    return {
        "campaign_id": plan.campaign_id,
        "campaign_plan_sha256": plan.content_sha256.value,
        "capture_source_identity_sha256": plan.capture_source_identity_sha256.value,
        "direction": plan.route.direction.value,
        "route_id": plan.route.route_id,
        "route_plan_sha256": plan.route_plan_sha256.value,
        "route_version": plan.route.version,
    }


def _fixed_false() -> dict[str, object]:
    return {
        "activation_allowed": False,
        "evidence_role": SYNTHETIC_ROUTE_EVIDENCE_ROLE,
        "input_authority": False,
        "live_navigation_enabled": False,
    }


def _request_json(
    plan: RouteEvidenceCampaignPlan,
    request: PassiveCaptureRequest,
    previous_journal_sha256: Sha256Digest,
) -> dict[str, object]:
    return {
        **_fixed_false(),
        **_identity_json(plan),
        "acknowledged_monotonic_s": request.acknowledged_monotonic_s,
        "camera_automation_enabled": False,
        "capture_session_id": request.capture_session_id,
        "case_id": request.case_id,
        "checkpoint_truth_asserted": False,
        "expires_monotonic_s": request.expires_monotonic_s,
        "keyboard_input_enabled": False,
        "mouse_input_enabled": False,
        "navigation_automation_enabled": False,
        "operator_acknowledgement_is_reviewer_truth": False,
        "operator_id": request.operator_id,
        "previous_journal_sha256": previous_journal_sha256.value,
        "request_id": request.request_id,
        "schema": _ACQUISITION_REQUEST_SCHEMA,
        "sequence_index": request.sequence_index,
    }


def _request_json_from_owned(
    plan: RouteEvidenceCampaignPlan,
    owned: OwnedRouteEvidenceCase,
    previous_journal_sha256: Sha256Digest,
) -> dict[str, object]:
    acquisition = owned.acquisition
    return {
        **_fixed_false(),
        **_identity_json(plan),
        "acknowledged_monotonic_s": acquisition.acknowledged_monotonic_s,
        "camera_automation_enabled": False,
        "capture_session_id": acquisition.capture_session_id,
        "case_id": acquisition.case_id,
        "checkpoint_truth_asserted": False,
        "expires_monotonic_s": acquisition.expires_monotonic_s,
        "keyboard_input_enabled": False,
        "mouse_input_enabled": False,
        "navigation_automation_enabled": False,
        "operator_acknowledgement_is_reviewer_truth": False,
        "operator_id": acquisition.operator_id,
        "previous_journal_sha256": previous_journal_sha256.value,
        "request_id": acquisition.request_id,
        "schema": _ACQUISITION_REQUEST_SCHEMA,
        "sequence_index": acquisition.sequence_index,
    }


def _case_record_json(
    plan: RouteEvidenceCampaignPlan,
    owned: OwnedRouteEvidenceCase,
    request_record_sha256: Sha256Digest,
    previous_journal_sha256: Sha256Digest,
) -> dict[str, object]:
    return {
        **_fixed_false(),
        **_identity_json(plan),
        "case_id": owned.case_id,
        "owned_case": owned.to_json_value(),
        "previous_journal_sha256": previous_journal_sha256.value,
        "request_record_sha256": request_record_sha256.value,
        "schema": _ACQUISITION_CASE_SCHEMA,
        "sequence_index": owned.sequence_index,
    }


def _acquisition_finalization_json(
    package: FinalizedRouteEvidencePackage,
    journal_head_sha256: Sha256Digest,
    case_record_sha256s: tuple[Sha256Digest, ...],
    transaction_root_identity: tuple[int, ...],
) -> dict[str, object]:
    return {
        **_fixed_false(),
        **_identity_json(package.campaign_plan),
        "acquisition_head_sha256": package.acquisition_head_sha256.value,
        "case_record_sha256s": [item.value for item in case_record_sha256s],
        "finalized_package_filename": FINALIZED_PACKAGE_FILENAME,
        "finalized_package_sha256": package.content_sha256.value,
        "journal_head_sha256": journal_head_sha256.value,
        "schema": _ACQUISITION_FINALIZATION_SCHEMA,
        "status": "finalized",
        "transaction_root_identity": list(transaction_root_identity),
    }


def _acquisition_stop_json(
    plan: RouteEvidenceCampaignPlan,
    *,
    reason: str,
    journal_head_sha256: Sha256Digest,
    stopped_monotonic_s: float,
) -> dict[str, object]:
    return {
        **_fixed_false(),
        **_identity_json(plan),
        "journal_head_sha256": journal_head_sha256.value,
        "no_retry_in_same_transaction": True,
        "reason": reason,
        "review_eligible": False,
        "schema": _ACQUISITION_STOP_SCHEMA,
        "status": "stopped",
        "stopped_monotonic_s": stopped_monotonic_s,
    }


def _review_plan_json(
    package: FinalizedRouteEvidencePackage,
    acquisition: DurableAcquisitionFilesystemExpectation,
    *,
    review_id: str,
    reviewer_id: str,
    started_at_utc: str,
) -> dict[str, object]:
    return {
        **_fixed_false(),
        **_identity_json(package.campaign_plan),
        "acquisition_finalization_sha256": acquisition.acquisition_finalization_sha256.value,
        "acquisition_journal_head_sha256": acquisition.acquisition_journal_head_sha256.value,
        "acquisition_physical_identity_sha256": (
            acquisition.acquisition_physical_identity_sha256.value
        ),
        "case_bindings": [
            {
                "case_id": owned.case_id,
                "detector_report_sha256": owned.detector_report_artifact.sha256.value,
                "frame_sha256": owned.frame_artifact.sha256.value,
                "sequence_index": owned.sequence_index,
            }
            for owned in package.cases
        ],
        "finalized_package_sha256": package.content_sha256.value,
        "operator_id": package.campaign_plan.operator_id,
        "review_id": review_id,
        "reviewer_id": reviewer_id,
        "schema": _REVIEW_PLAN_SCHEMA,
        "started_at_utc": started_at_utc,
        "truth_source": "independent-human-review",
    }


def _truth_record_json(
    package: FinalizedRouteEvidencePackage,
    truth: RouteEvidenceCaseTruth,
    *,
    ordinal: int,
    review_id: str,
    review_plan_sha256: Sha256Digest,
    previous_journal_sha256: Sha256Digest,
    recorded_at_utc: str,
) -> dict[str, object]:
    return {
        **_fixed_false(),
        **_identity_json(package.campaign_plan),
        "case_truth": truth.to_json_value(),
        "finalized_package_sha256": package.content_sha256.value,
        "ordinal": ordinal,
        "previous_journal_sha256": previous_journal_sha256.value,
        "recorded_at_utc": recorded_at_utc,
        "review_id": review_id,
        "review_plan_sha256": review_plan_sha256.value,
        "schema": _REVIEW_TRUTH_SCHEMA,
    }


def _review_finalization_json(
    package: FinalizedRouteEvidencePackage,
    review: RouteEvidenceReview,
    *,
    review_id: str,
    review_plan_sha256: Sha256Digest,
    journal_head_sha256: Sha256Digest,
    transaction_root_identity: tuple[int, ...],
) -> dict[str, object]:
    return {
        **_fixed_false(),
        **_identity_json(package.campaign_plan),
        "finalized_package_sha256": package.content_sha256.value,
        "independent_review_filename": INDEPENDENT_REVIEW_FILENAME,
        "independent_review_sha256": review.content_sha256.value,
        "journal_head_sha256": journal_head_sha256.value,
        "review_id": review_id,
        "review_plan_sha256": review_plan_sha256.value,
        "schema": _REVIEW_FINALIZATION_SCHEMA,
        "status": "finalized",
        "transaction_root_identity": list(transaction_root_identity),
    }


def _review_stop_json(
    package: FinalizedRouteEvidencePackage,
    *,
    review_id: str,
    review_plan_sha256: Sha256Digest,
    journal_head_sha256: Sha256Digest,
    reason: str,
    stopped_at_utc: str,
) -> dict[str, object]:
    return {
        **_fixed_false(),
        **_identity_json(package.campaign_plan),
        "finalized_package_sha256": package.content_sha256.value,
        "journal_head_sha256": journal_head_sha256.value,
        "no_retry_in_same_transaction": True,
        "reason": reason,
        "review_eligible": False,
        "review_id": review_id,
        "review_plan_sha256": review_plan_sha256.value,
        "schema": _REVIEW_STOP_SCHEMA,
        "status": "stopped",
        "stopped_at_utc": stopped_at_utc,
    }


def _request_path(owned: OwnedRouteEvidenceCase) -> str:
    return f"audit/{owned.sequence_index:03d}-{owned.case_id}-request.json"


def _case_record_path(owned: OwnedRouteEvidenceCase) -> str:
    return f"audit/{owned.sequence_index:03d}-{owned.case_id}-owned.json"


def _truth_path(owned: OwnedRouteEvidenceCase) -> str:
    return f"truth/{owned.sequence_index:03d}-{owned.case_id}.json"


def _snapshot_truth(value: RouteEvidenceCaseTruth) -> RouteEvidenceCaseTruth:
    if type(value) is not RouteEvidenceCaseTruth:
        raise ValueError("review truth must have the exact contract type")
    detection = value.detection
    if type(detection) is not CheckpointDetection:
        raise ValueError("review truth detection must have the exact contract type")
    return RouteEvidenceCaseTruth(
        case_id=value.case_id,
        frame_sha256=Sha256Digest(value.frame_sha256.value),
        detector_report_sha256=Sha256Digest(value.detector_report_sha256.value),
        decision=value.decision,
        detection=CheckpointDetection(
            detection.match,
            tuple(detection.candidate_checkpoint_ids),
            detection.confidence,
        ),
    )


def _acquisition_expectation_from_package(
    package: FinalizedRouteEvidencePackage,
    *,
    journal_head_sha256: Sha256Digest,
    finalization_sha256: Sha256Digest,
    physical_identity_sha256: Sha256Digest,
) -> DurableAcquisitionFilesystemExpectation:
    plan = package.campaign_plan
    return DurableAcquisitionFilesystemExpectation(
        finalized_package_sha256=package.content_sha256,
        acquisition_head_sha256=package.acquisition_head_sha256,
        campaign_id=plan.campaign_id,
        route=RouteIdentity(plan.route.route_id, plan.route.version, plan.route.direction),
        direction=plan.route.direction,
        route_plan_sha256=plan.route_plan_sha256,
        detector=CheckpointDetectorIdentity(plan.detector.detector_id, plan.detector.version),
        profile=CheckpointProfileIdentity(
            plan.profile.profile_id,
            plan.profile.version,
            Sha256Digest(plan.profile.content_sha256.value),
        ),
        capture_source_id=plan.capture_source_id,
        capture_session_id=plan.capture_session_id,
        capture_build=RouteEvidenceCaptureBuildIdentity(
            plan.capture_build.build_id,
            plan.capture_build.version,
            Sha256Digest(plan.capture_build.content_sha256.value),
        ),
        frame_width=plan.frame_width,
        frame_height=plan.frame_height,
        pixel_format=plan.pixel_format,
        capture_configuration_sha256=Sha256Digest(plan.capture_configuration_sha256.value),
        capture_environment_sha256=Sha256Digest(plan.capture_environment_sha256.value),
        support_envelope_sha256=Sha256Digest(plan.support_envelope_sha256.value),
        acquisition_journal_head_sha256=journal_head_sha256,
        acquisition_finalization_sha256=finalization_sha256,
        acquisition_physical_identity_sha256=physical_identity_sha256,
    )


def _snapshot_acquisition_expectation(
    value: DurableAcquisitionFilesystemExpectation,
) -> DurableAcquisitionFilesystemExpectation:
    if type(value) is not DurableAcquisitionFilesystemExpectation:
        raise TypeError("expectation must be DurableAcquisitionFilesystemExpectation")
    return DurableAcquisitionFilesystemExpectation(
        finalized_package_sha256=Sha256Digest(value.finalized_package_sha256.value),
        acquisition_head_sha256=Sha256Digest(value.acquisition_head_sha256.value),
        campaign_id=value.campaign_id,
        route=RouteIdentity(value.route.route_id, value.route.version, value.route.direction),
        direction=value.direction,
        route_plan_sha256=Sha256Digest(value.route_plan_sha256.value),
        detector=CheckpointDetectorIdentity(value.detector.detector_id, value.detector.version),
        profile=CheckpointProfileIdentity(
            value.profile.profile_id,
            value.profile.version,
            Sha256Digest(value.profile.content_sha256.value),
        ),
        capture_source_id=value.capture_source_id,
        capture_session_id=value.capture_session_id,
        capture_build=RouteEvidenceCaptureBuildIdentity(
            value.capture_build.build_id,
            value.capture_build.version,
            Sha256Digest(value.capture_build.content_sha256.value),
        ),
        frame_width=value.frame_width,
        frame_height=value.frame_height,
        pixel_format=value.pixel_format,
        capture_configuration_sha256=Sha256Digest(value.capture_configuration_sha256.value),
        capture_environment_sha256=Sha256Digest(value.capture_environment_sha256.value),
        support_envelope_sha256=Sha256Digest(value.support_envelope_sha256.value),
        acquisition_journal_head_sha256=Sha256Digest(value.acquisition_journal_head_sha256.value),
        acquisition_finalization_sha256=Sha256Digest(value.acquisition_finalization_sha256.value),
        acquisition_physical_identity_sha256=Sha256Digest(
            value.acquisition_physical_identity_sha256.value
        ),
    )


def _snapshot_full_expectation(
    value: DurableRouteEvidenceFilesystemExpectation,
) -> DurableRouteEvidenceFilesystemExpectation:
    if type(value) is not DurableRouteEvidenceFilesystemExpectation:
        raise TypeError("expectation must be DurableRouteEvidenceFilesystemExpectation")
    acquisition = DurableAcquisitionFilesystemExpectation(
        finalized_package_sha256=value.finalized_package_sha256,
        acquisition_head_sha256=value.acquisition_head_sha256,
        campaign_id=value.campaign_id,
        route=value.route,
        direction=value.direction,
        route_plan_sha256=value.route_plan_sha256,
        detector=value.detector,
        profile=value.profile,
        capture_source_id=value.capture_source_id,
        capture_session_id=value.capture_session_id,
        capture_build=value.capture_build,
        frame_width=value.frame_width,
        frame_height=value.frame_height,
        pixel_format=value.pixel_format,
        capture_configuration_sha256=value.capture_configuration_sha256,
        capture_environment_sha256=value.capture_environment_sha256,
        support_envelope_sha256=value.support_envelope_sha256,
        acquisition_journal_head_sha256=value.acquisition_journal_head_sha256,
        acquisition_finalization_sha256=value.acquisition_finalization_sha256,
        acquisition_physical_identity_sha256=value.acquisition_physical_identity_sha256,
    )
    owned = _snapshot_acquisition_expectation(acquisition)
    return DurableRouteEvidenceFilesystemExpectation(
        finalized_package_sha256=owned.finalized_package_sha256,
        acquisition_head_sha256=owned.acquisition_head_sha256,
        campaign_id=owned.campaign_id,
        route=owned.route,
        direction=owned.direction,
        route_plan_sha256=owned.route_plan_sha256,
        detector=owned.detector,
        profile=owned.profile,
        capture_source_id=owned.capture_source_id,
        capture_session_id=owned.capture_session_id,
        capture_build=owned.capture_build,
        frame_width=owned.frame_width,
        frame_height=owned.frame_height,
        pixel_format=owned.pixel_format,
        capture_configuration_sha256=owned.capture_configuration_sha256,
        capture_environment_sha256=owned.capture_environment_sha256,
        support_envelope_sha256=owned.support_envelope_sha256,
        independent_review_sha256=Sha256Digest(value.independent_review_sha256.value),
        reviewer_id=value.reviewer_id,
        acquisition_journal_head_sha256=owned.acquisition_journal_head_sha256,
        acquisition_finalization_sha256=owned.acquisition_finalization_sha256,
        acquisition_physical_identity_sha256=owned.acquisition_physical_identity_sha256,
        review_id=value.review_id,
        review_plan_sha256=Sha256Digest(value.review_plan_sha256.value),
        review_journal_head_sha256=Sha256Digest(value.review_journal_head_sha256.value),
        review_finalization_sha256=Sha256Digest(value.review_finalization_sha256.value),
        review_physical_identity_sha256=Sha256Digest(value.review_physical_identity_sha256.value),
    )


def _assert_stable_intake(
    root: Path,
    root_signature: tuple[int, ...],
    initial_tree: Mapping[str, _TreeEntry],
    snapshots: Mapping[str, _FileSnapshot],
    size_limits: Mapping[str, int],
) -> None:
    final_tree = _assert_exact_tree(root, set(size_limits))
    if _stable_tree_identity(final_tree) != _stable_tree_identity(initial_tree):
        raise RouteEvidenceIntegrityError("durable evidence tree changed during verification")
    for relative, untyped_before in snapshots.items():
        before = untyped_before
        after = _read_owned_file(root, relative, size_limits[relative])
        if after != before:
            raise RouteEvidenceIntegrityError(
                f"durable evidence file changed during final verification: {relative}"
            )
    final_root = _lstat(root, "durable evidence root")
    if _directory_signature_identity(_stat_signature(final_root)) != (
        _directory_signature_identity(root_signature)
    ):
        raise RouteEvidenceIntegrityError("durable evidence root changed during verification")


def _acquisition_filesystem_identity(
    root_signature: tuple[int, ...],
    tree: Mapping[str, _TreeEntry],
) -> _AcquisitionFilesystemIdentity:
    return _AcquisitionFilesystemIdentity(
        root_identity=_directory_signature_identity(root_signature),
        tree_identity=tuple(
            (
                relative,
                entry.is_directory,
                (
                    _directory_signature_identity(entry.signature)
                    if entry.is_directory
                    else entry.signature
                ),
            )
            for relative, entry in sorted(tree.items())
        ),
    )


def load_durable_acquisition(
    root: str | os.PathLike[str],
    expectation: DurableAcquisitionFilesystemExpectation,
) -> VerifiedDurableAcquisition:
    """Strictly load one finalized acquisition root without reviewer truth."""

    owned_expectation = _snapshot_acquisition_expectation(expectation)
    root_argument = _absolute_path_once(root, "durable acquisition root")
    root_path, root_signature = _intake_root(root_argument, "durable acquisition root")
    initial_plan = _read_owned_file(root_path, ACQUISITION_PLAN_FILENAME, _MAX_MANIFEST_BYTES)
    initial_finalization = _read_owned_file(
        root_path, ACQUISITION_FINALIZATION_FILENAME, _MAX_MANIFEST_BYTES
    )
    initial_finalization_object = _strict_canonical_object(
        initial_finalization.payload,
        "durable acquisition finalization",
    )
    transaction_root_identity = _writer_directory_identity(root_signature)
    if initial_finalization_object.get("transaction_root_identity") != list(
        transaction_root_identity
    ):
        raise RouteEvidenceIntegrityError("durable acquisition transaction root identity differs")
    initial_package = _read_owned_file(root_path, FINALIZED_PACKAGE_FILENAME, _MAX_MANIFEST_BYTES)
    package = _parse_package(initial_package.payload)
    plan_payload = _canonical(package.campaign_plan.to_json_value())
    if initial_plan.payload != plan_payload:
        raise RouteEvidenceIntegrityError("durable campaign plan differs from finalized package")
    if package.content_sha256 != owned_expectation.finalized_package_sha256:
        raise RouteEvidenceIntegrityError("durable package digest differs from expectation")

    artifact_paths = {
        artifact.relative_path
        for owned in package.cases
        for artifact in (owned.frame_artifact, owned.detector_report_artifact)
    }
    request_paths = {_request_path(owned) for owned in package.cases}
    case_record_paths = {_case_record_path(owned) for owned in package.cases}
    fixed_paths = {
        ACQUISITION_PLAN_FILENAME,
        ACQUISITION_FINALIZATION_FILENAME,
        FINALIZED_PACKAGE_FILENAME,
    }
    expected_files = fixed_paths | artifact_paths | request_paths | case_record_paths
    size_limits = {relative: _MAX_MANIFEST_BYTES for relative in expected_files}
    for owned in package.cases:
        for artifact in (owned.frame_artifact, owned.detector_report_artifact):
            if artifact.size_bytes > _MAX_ARTIFACT_BYTES:
                raise RouteEvidenceIntegrityError(
                    f"durable artifact exceeds loader limit: {artifact.relative_path}"
                )
            size_limits[artifact.relative_path] = artifact.size_bytes
    initial_tree = _assert_exact_tree(root_path, expected_files)
    if _physical_identity_sha256(root_signature, initial_tree) != (
        owned_expectation.acquisition_physical_identity_sha256
    ):
        raise RouteEvidenceIntegrityError("durable acquisition physical identity differs")
    snapshots = {
        relative: _read_owned_file(root_path, relative, size_limits[relative])
        for relative in sorted(expected_files)
    }
    if snapshots[ACQUISITION_PLAN_FILENAME].payload != initial_plan.payload:
        raise RouteEvidenceIntegrityError("durable campaign plan changed during intake")
    if snapshots[ACQUISITION_FINALIZATION_FILENAME].payload != initial_finalization.payload:
        raise RouteEvidenceIntegrityError("durable acquisition finalization changed during intake")
    if snapshots[FINALIZED_PACKAGE_FILENAME].payload != initial_package.payload:
        raise RouteEvidenceIntegrityError("durable finalized package changed during intake")

    artifacts = {relative: snapshots[relative].payload for relative in artifact_paths}
    _reject_duplicate_artifact_content(package)
    _validate_detector_reports(package, artifacts)
    journal_head = package.campaign_plan.content_sha256
    case_record_sha256s: list[Sha256Digest] = []
    for owned in package.cases:
        request_payload = _canonical(
            _request_json_from_owned(package.campaign_plan, owned, journal_head)
        )
        if snapshots[_request_path(owned)].payload != request_payload:
            raise RouteEvidenceIntegrityError(f"durable request journal differs: {owned.case_id}")
        request_sha = Sha256Digest.from_bytes(request_payload)
        case_payload = _canonical(
            _case_record_json(package.campaign_plan, owned, request_sha, request_sha)
        )
        if snapshots[_case_record_path(owned)].payload != case_payload:
            raise RouteEvidenceIntegrityError(
                f"durable owned-case journal differs: {owned.case_id}"
            )
        journal_head = Sha256Digest.from_bytes(case_payload)
        case_record_sha256s.append(journal_head)
    finalization_payload = _canonical(
        _acquisition_finalization_json(
            package,
            journal_head,
            tuple(case_record_sha256s),
            transaction_root_identity,
        )
    )
    if initial_finalization.payload != finalization_payload:
        raise RouteEvidenceIntegrityError("durable acquisition finalization lineage differs")
    if journal_head != owned_expectation.acquisition_journal_head_sha256:
        raise RouteEvidenceIntegrityError("durable acquisition journal head differs")
    if Sha256Digest.from_bytes(finalization_payload) != (
        owned_expectation.acquisition_finalization_sha256
    ):
        raise RouteEvidenceIntegrityError("durable acquisition finalization digest differs")
    _require_package_expectation(package, owned_expectation)
    _assert_stable_intake(root_path, root_signature, initial_tree, snapshots, size_limits)
    return VerifiedDurableAcquisition(
        package=package,
        artifacts=MappingProxyType(dict(artifacts)),
        expectation=owned_expectation,
        filesystem_identity=_acquisition_filesystem_identity(root_signature, initial_tree),
    )


def _require_package_expectation(
    package: FinalizedRouteEvidencePackage,
    expectation: RouteEvidenceLoadExpectation,
) -> None:
    plan = package.campaign_plan
    if (
        package.content_sha256 != expectation.finalized_package_sha256
        or package.acquisition_head_sha256 != expectation.acquisition_head_sha256
        or plan.campaign_id != expectation.campaign_id
        or package.route != expectation.route
        or package.route.direction is not expectation.direction
        or plan.route_plan_sha256 != expectation.route_plan_sha256
        or plan.detector != expectation.detector
        or plan.profile != expectation.profile
        or plan.capture_source_id != expectation.capture_source_id
        or plan.capture_session_id != expectation.capture_session_id
        or plan.capture_build != expectation.capture_build
        or plan.frame_width != expectation.frame_width
        or plan.frame_height != expectation.frame_height
        or plan.pixel_format is not expectation.pixel_format
        or plan.capture_configuration_sha256 != expectation.capture_configuration_sha256
        or plan.capture_environment_sha256 != expectation.capture_environment_sha256
        or plan.support_envelope_sha256 != expectation.support_envelope_sha256
    ):
        raise RouteEvidenceIntegrityError(
            "durable acquisition differs from caller-owned identity pins"
        )


class DurableAcquisitionTransaction:
    """Single-use source-owning acquisition writer with no resume surface."""

    __slots__ = (
        "_case_record_sha256s",
        "_journal_head",
        "_namespace",
        "_phase",
        "_sequencer",
        "_transition_lock",
    )

    def __init__(
        self,
        namespace: _DurableNamespace,
        sequencer: PassiveCampaignSequencer,
        *,
        _factory_token: object | None = None,
    ) -> None:
        if _factory_token is not _FACTORY_TOKEN:
            raise ValueError("durable acquisition transactions require their factory")
        self._namespace = namespace
        self._sequencer = sequencer
        self._journal_head = sequencer.progress.plan.content_sha256
        self._case_record_sha256s: list[Sha256Digest] = []
        self._phase = DurableAcquisitionPhase.READY_FOR_REQUEST
        self._transition_lock = RLock()

    @property
    def phase(self) -> DurableAcquisitionPhase:
        with self._transition_lock:
            return self._phase

    @property
    def progress(self) -> PassiveCampaignProgress:
        with self._transition_lock:
            return self._sequencer.progress

    def __copy__(self) -> NoReturn:
        raise TypeError("durable acquisition transactions cannot be copied")

    def __deepcopy__(self, memo: object) -> NoReturn:
        del memo
        raise TypeError("durable acquisition transactions cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("durable acquisition transactions cannot be pickled")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        del protocol
        raise TypeError("durable acquisition transactions cannot be pickled")

    def _write_stop(self, reason: str, progress: PassiveCampaignProgress) -> None:
        if self._phase is DurableAcquisitionPhase.STOPPED:
            return
        self._phase = DurableAcquisitionPhase.STOPPED
        try:
            payload = _canonical(
                _acquisition_stop_json(
                    progress.plan,
                    reason=reason,
                    journal_head_sha256=self._journal_head,
                    stopped_monotonic_s=progress.last_event_monotonic_s,
                )
            )
            self._namespace.write(ACQUISITION_STOP_FILENAME, payload)
        except BaseException:
            # The existing partial prefix is itself non-reviewable audit evidence.
            pass
        finally:
            try:
                self._namespace.close()
            except BaseException:
                pass

    def _require_open(self) -> None:
        if self._phase in {DurableAcquisitionPhase.FINALIZED, DurableAcquisitionPhase.STOPPED}:
            raise DurableEvidenceStateError("durable acquisition is terminal")

    def request_capture(
        self,
        *,
        request_id: str,
        operator_id: str,
        acknowledged_monotonic_s: float,
    ) -> PassiveCampaignProgress:
        with self._transition_lock:
            self._require_open()
            try:
                progress = self._sequencer.request_capture(
                    request_id=request_id,
                    operator_id=operator_id,
                    acknowledged_monotonic_s=acknowledged_monotonic_s,
                )
                if progress.phase is PassiveCampaignPhase.STOPPED:
                    reason = (
                        progress.failure.reason.value if progress.failure else "passive-stopped"
                    )
                    self._write_stop(reason, progress)
                    return progress
                if progress.pending_request is None:
                    raise DurableEvidenceError("passive sequencer omitted its pending request")
                payload = _canonical(
                    _request_json(progress.plan, progress.pending_request, self._journal_head)
                )
                self._namespace.write(
                    f"audit/{progress.pending_request.sequence_index:03d}-"
                    f"{progress.pending_request.case_id}-request.json",
                    payload,
                )
                self._journal_head = Sha256Digest.from_bytes(payload)
                self._phase = DurableAcquisitionPhase.AWAITING_CAPTURE
                return progress
            except BaseException:
                progress = self._sequencer.fail(
                    PassiveCampaignFailureReason.INTERRUPTED,
                    evaluated_monotonic_s=self._sequencer.progress.last_event_monotonic_s,
                )
                self._write_stop("durable-request-persistence-failed", progress)
                raise

    def capture(self) -> PassiveCampaignProgress:
        with self._transition_lock:
            self._require_open()
            try:
                progress = self._sequencer.capture()
                if progress.phase is PassiveCampaignPhase.STOPPED:
                    reason = (
                        progress.failure.reason.value if progress.failure else "passive-stopped"
                    )
                    self._write_stop(reason, progress)
                    return progress
                if not progress.captures:
                    raise DurableEvidenceError("passive sequencer returned no owned capture")
                capture = progress.captures[-1]
                owned = capture.owned_case
                case_directory = f"{owned.sequence_index:03d}-{owned.case_id}"
                self._namespace.mkdir_child("cases", case_directory)
                self._namespace.write(
                    owned.frame_artifact.relative_path,
                    capture.frame_payload,
                    max_bytes=_MAX_ARTIFACT_BYTES,
                )
                self._namespace.write(
                    owned.detector_report_artifact.relative_path,
                    capture.detector_report_payload,
                    max_bytes=_MAX_ARTIFACT_BYTES,
                )
                request_sha = self._journal_head
                case_payload = _canonical(
                    _case_record_json(progress.plan, owned, request_sha, request_sha)
                )
                self._namespace.write(_case_record_path(owned), case_payload)
                self._journal_head = Sha256Digest.from_bytes(case_payload)
                self._case_record_sha256s.append(self._journal_head)
                self._phase = (
                    DurableAcquisitionPhase.COMPLETE
                    if progress.phase is PassiveCampaignPhase.COMPLETE
                    else DurableAcquisitionPhase.READY_FOR_REQUEST
                )
                return progress
            except BaseException:
                progress = self._sequencer.fail(
                    PassiveCampaignFailureReason.INTERRUPTED,
                    evaluated_monotonic_s=self._sequencer.progress.last_event_monotonic_s,
                )
                self._write_stop("durable-capture-persistence-failed", progress)
                raise

    def fail(
        self,
        reason: PassiveCampaignFailureReason,
        *,
        evaluated_monotonic_s: float,
    ) -> PassiveCampaignProgress:
        with self._transition_lock:
            self._require_open()
            progress = self._sequencer.fail(
                reason,
                evaluated_monotonic_s=evaluated_monotonic_s,
            )
            self._write_stop(reason.value, progress)
            return progress

    def finalize(self, *, finalized_at_utc: str) -> DurableAcquisitionReceipt:
        with self._transition_lock:
            self._require_open()
            if self._phase is not DurableAcquisitionPhase.COMPLETE:
                progress = self._sequencer.fail(
                    PassiveCampaignFailureReason.FINALIZATION_FAILED,
                    evaluated_monotonic_s=self._sequencer.progress.last_event_monotonic_s,
                )
                self._write_stop("durable-finalization-before-complete", progress)
                raise DurableEvidenceStateError("only a complete durable acquisition can finalize")
            terminal_manifest_started = False
            try:
                finalization = self._sequencer.finalize(finalized_at_utc=finalized_at_utc)
                package = finalization.package
                for relative, payload in finalization.artifact_payloads:
                    persisted = self._namespace.read_owned(relative, len(payload))
                    if persisted != payload:
                        raise DurableEvidenceError(
                            f"persisted artifact differs before finalization: {relative}"
                        )
                finalization_payload = _canonical(
                    _acquisition_finalization_json(
                        package,
                        self._journal_head,
                        tuple(self._case_record_sha256s),
                        self._namespace.root_identity,
                    )
                )
                finalization_sha = Sha256Digest.from_bytes(finalization_payload)
                package_payload = _canonical(package.to_json_value())
                self._namespace.write(FINALIZED_PACKAGE_FILENAME, package_payload)
                expected_before_terminal_manifest = {
                    ACQUISITION_PLAN_FILENAME,
                    FINALIZED_PACKAGE_FILENAME,
                    *(
                        path
                        for owned in package.cases
                        for path in (
                            _request_path(owned),
                            _case_record_path(owned),
                            owned.frame_artifact.relative_path,
                            owned.detector_report_artifact.relative_path,
                        )
                    ),
                }
                self._namespace.assert_exact_tree(expected_before_terminal_manifest)
                terminal_manifest_started = True
                self._namespace.write(
                    ACQUISITION_FINALIZATION_FILENAME,
                    finalization_payload,
                )
                finalized_files = {
                    ACQUISITION_FINALIZATION_FILENAME,
                    *expected_before_terminal_manifest,
                }
                self._namespace.assert_exact_tree(finalized_files)
                physical_identity_sha256 = self._namespace.physical_identity_sha256(finalized_files)
                expectation = _acquisition_expectation_from_package(
                    package,
                    journal_head_sha256=self._journal_head,
                    finalization_sha256=finalization_sha,
                    physical_identity_sha256=physical_identity_sha256,
                )
                receipt = DurableAcquisitionReceipt(expectation)
                self._namespace.close()
                self._phase = DurableAcquisitionPhase.FINALIZED
                return receipt
            except PassiveCampaignFinalizationError:
                self._write_stop(
                    "passive-finalization-failed",
                    self._sequencer.progress,
                )
                raise
            except BaseException:
                if terminal_manifest_started:
                    self._phase = DurableAcquisitionPhase.STOPPED
                    try:
                        self._namespace.close()
                    except BaseException:
                        pass
                else:
                    self._write_stop(
                        "durable-finalization-persistence-failed",
                        self._sequencer.progress,
                    )
                raise


def begin_durable_acquisition(
    root: str | os.PathLike[str],
    plan: RouteEvidenceCampaignPlan,
    source: PassiveCaptureSource,
    detector: CheckpointDetector,
    clock: PassiveMonotonicClock,
    *,
    started_monotonic_s: float,
) -> DurableAcquisitionTransaction:
    """Reserve a fresh root beneath a trusted, non-hostile dedicated parent."""

    root_path = _absolute_path_once(root, "durable acquisition root")
    return _begin_durable_acquisition_with_namespace_factory(
        root_path,
        plan,
        source,
        detector,
        clock,
        started_monotonic_s=started_monotonic_s,
        namespace_factory=_ExclusiveNamespace,
    )


def _begin_durable_acquisition_with_namespace_factory(
    root_path: Path,
    plan: RouteEvidenceCampaignPlan,
    source: PassiveCaptureSource,
    detector: CheckpointDetector,
    clock: PassiveMonotonicClock,
    *,
    started_monotonic_s: float,
    namespace_factory: _NamespaceFactory,
) -> DurableAcquisitionTransaction:
    """Initialize one transaction with a navigation-owned namespace implementation."""

    namespace = namespace_factory(root_path)
    try:
        namespace.mkdir("audit")
        namespace.mkdir("cases")
        sequencer = PassiveCampaignSequencer(
            plan,
            source,
            detector,
            clock,
            started_monotonic_s=started_monotonic_s,
        )
        plan_snapshot = sequencer.progress.plan
        namespace.write(
            ACQUISITION_PLAN_FILENAME,
            _canonical(plan_snapshot.to_json_value()),
        )
    except BaseException:
        # Keep the invocation-owned prefix as non-reviewable audit evidence.
        try:
            namespace.close()
        except BaseException:
            pass
        raise
    return DurableAcquisitionTransaction(
        namespace,
        sequencer,
        _factory_token=_FACTORY_TOKEN,
    )


class DurableReviewTransaction:
    """Separate reviewer-only writer; it has no capture or navigation capability."""

    __slots__ = (
        "_acquisition_expectation",
        "_acquisition_filesystem_identity",
        "_acquisition_root",
        "_journal_head",
        "_last_recorded_at",
        "_namespace",
        "_package",
        "_phase",
        "_review_id",
        "_review_plan_sha256",
        "_reviewer_id",
        "_started_at",
        "_transition_lock",
        "_truths",
    )

    def __init__(
        self,
        namespace: _DurableNamespace,
        acquisition_root: Path,
        acquisition: VerifiedDurableAcquisition,
        *,
        review_id: str,
        reviewer_id: str,
        started_at_utc: str,
        review_plan_sha256: Sha256Digest,
        _factory_token: object | None = None,
    ) -> None:
        if _factory_token is not _FACTORY_TOKEN:
            raise ValueError("durable review transactions require their factory")
        self._namespace = namespace
        self._acquisition_root = acquisition_root
        self._acquisition_expectation = acquisition.expectation
        self._acquisition_filesystem_identity = acquisition.filesystem_identity
        self._package = acquisition.package
        self._review_id = review_id
        self._reviewer_id = reviewer_id
        self._started_at = _utc(started_at_utc, "review start")
        self._last_recorded_at = self._started_at
        self._review_plan_sha256 = review_plan_sha256
        self._journal_head = review_plan_sha256
        self._truths: list[RouteEvidenceCaseTruth] = []
        self._phase = DurableReviewPhase.READY_FOR_TRUTH
        self._transition_lock = RLock()

    @property
    def phase(self) -> DurableReviewPhase:
        with self._transition_lock:
            return self._phase

    def __copy__(self) -> NoReturn:
        raise TypeError("durable review transactions cannot be copied")

    def __deepcopy__(self, memo: object) -> NoReturn:
        del memo
        raise TypeError("durable review transactions cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("durable review transactions cannot be pickled")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        del protocol
        raise TypeError("durable review transactions cannot be pickled")

    def _require_open(self) -> None:
        if self._phase in {DurableReviewPhase.FINALIZED, DurableReviewPhase.STOPPED}:
            raise DurableEvidenceStateError("durable review is terminal")

    def _write_stop(self, reason: str, stopped_at_utc: str) -> None:
        if self._phase is DurableReviewPhase.STOPPED:
            return
        self._phase = DurableReviewPhase.STOPPED
        try:
            _utc(stopped_at_utc, "review stop time")
            payload = _canonical(
                _review_stop_json(
                    self._package,
                    review_id=self._review_id,
                    review_plan_sha256=self._review_plan_sha256,
                    journal_head_sha256=self._journal_head,
                    reason=reason,
                    stopped_at_utc=stopped_at_utc,
                )
            )
            self._namespace.write(REVIEW_STOP_FILENAME, payload)
        except BaseException:
            pass
        finally:
            try:
                self._namespace.close()
            except BaseException:
                pass

    def record_case_truth(
        self,
        truth: RouteEvidenceCaseTruth,
        *,
        recorded_at_utc: str,
    ) -> DurableReviewPhase:
        with self._transition_lock:
            self._require_open()
            try:
                timestamp = _utc(recorded_at_utc, "review truth recorded_at_utc")
                if self._phase is not DurableReviewPhase.READY_FOR_TRUTH:
                    raise DurableEvidenceStateError("review truth is not expected")
                if timestamp <= self._last_recorded_at:
                    raise ValueError("review truth chronology must be strictly increasing")
                snapshot = _snapshot_truth(truth)
                owned = self._package.cases[len(self._truths)]
                if (
                    snapshot.case_id != owned.case_id
                    or snapshot.frame_sha256 != owned.frame_artifact.sha256
                    or snapshot.detector_report_sha256 != owned.detector_report_artifact.sha256
                ):
                    raise ValueError("review truth does not bind the exact next case artifacts")
                route_checkpoint_ids = {
                    item.checkpoint_id
                    for item in self._package.campaign_plan.route_plan.checkpoints
                }
                if any(
                    candidate not in route_checkpoint_ids
                    for candidate in snapshot.detection.candidate_checkpoint_ids
                ):
                    raise ValueError("review truth names a foreign route checkpoint")
                payload = _canonical(
                    _truth_record_json(
                        self._package,
                        snapshot,
                        ordinal=owned.sequence_index,
                        review_id=self._review_id,
                        review_plan_sha256=self._review_plan_sha256,
                        previous_journal_sha256=self._journal_head,
                        recorded_at_utc=recorded_at_utc,
                    )
                )
                self._namespace.write(_truth_path(owned), payload)
                self._journal_head = Sha256Digest.from_bytes(payload)
                self._truths.append(snapshot)
                self._last_recorded_at = timestamp
                if len(self._truths) == len(self._package.cases):
                    self._phase = DurableReviewPhase.COMPLETE
                return self._phase
            except BaseException:
                self._write_stop("review-truth-persistence-or-contract-failed", recorded_at_utc)
                raise

    def stop(self, *, reason: str, stopped_at_utc: str) -> None:
        with self._transition_lock:
            self._require_open()
            try:
                _identifier(reason, "review stop reason")
                timestamp = _utc(stopped_at_utc, "review stop time")
                if timestamp <= self._last_recorded_at:
                    raise ValueError("review stop must follow the last review event")
                self._write_stop(reason, stopped_at_utc)
            except BaseException:
                self._write_stop("invalid-review-stop", stopped_at_utc)
                raise

    def finalize(self, *, reviewed_at_utc: str) -> DurableReviewReceipt:
        with self._transition_lock:
            self._require_open()
            try:
                reviewed_at = _utc(reviewed_at_utc, "review finalization time")
            except BaseException:
                self._write_stop("invalid-review-finalization-time", reviewed_at_utc)
                raise
            if self._phase is not DurableReviewPhase.COMPLETE:
                self._write_stop("review-finalization-before-complete", reviewed_at_utc)
                raise DurableEvidenceStateError("only a complete durable review can finalize")
            if reviewed_at <= self._last_recorded_at:
                self._write_stop("review-finalization-chronology-failed", reviewed_at_utc)
                raise ValueError("review finalization must follow the last truth record")
            terminal_manifest_started = False
            try:
                acquisition = load_durable_acquisition(
                    self._acquisition_root,
                    self._acquisition_expectation,
                )
                if acquisition.package.content_sha256 != self._package.content_sha256:
                    raise RouteEvidenceIntegrityError(
                        "acquisition changed after review transaction began"
                    )
                if acquisition.filesystem_identity != self._acquisition_filesystem_identity:
                    raise RouteEvidenceIntegrityError(
                        "acquisition filesystem identity changed after review began"
                    )
                review = RouteEvidenceReview(
                    finalized_package_sha256=self._package.content_sha256,
                    campaign_id=self._package.campaign_plan.campaign_id,
                    route=self._package.route,
                    route_plan_sha256=self._package.campaign_plan.route_plan_sha256,
                    reviewer_id=self._reviewer_id,
                    reviewed_at_utc=reviewed_at_utc,
                    cases=tuple(self._truths),
                )
                report = verify_synthetic_route_evidence(
                    self._package,
                    review,
                    acquisition.artifacts,
                    self._acquisition_expectation,
                )
                finalization_payload = _canonical(
                    _review_finalization_json(
                        self._package,
                        review,
                        review_id=self._review_id,
                        review_plan_sha256=self._review_plan_sha256,
                        journal_head_sha256=self._journal_head,
                        transaction_root_identity=self._namespace.root_identity,
                    )
                )
                finalization_sha = Sha256Digest.from_bytes(finalization_payload)
                review_payload = _canonical(review.to_json_value())
                self._namespace.write(INDEPENDENT_REVIEW_FILENAME, review_payload)
                expected_before_terminal_manifest = {
                    REVIEW_PLAN_FILENAME,
                    INDEPENDENT_REVIEW_FILENAME,
                    *(_truth_path(owned) for owned in self._package.cases),
                }
                self._namespace.assert_exact_tree(expected_before_terminal_manifest)
                terminal_manifest_started = True
                self._namespace.write(
                    REVIEW_FINALIZATION_FILENAME,
                    finalization_payload,
                )
                finalized_files = {
                    REVIEW_FINALIZATION_FILENAME,
                    *expected_before_terminal_manifest,
                }
                self._namespace.assert_exact_tree(finalized_files)
                physical_identity_sha256 = self._namespace.physical_identity_sha256(finalized_files)
                receipt = DurableReviewReceipt(
                    review_id=self._review_id,
                    reviewer_id=self._reviewer_id,
                    independent_review_sha256=review.content_sha256,
                    review_plan_sha256=self._review_plan_sha256,
                    review_journal_head_sha256=self._journal_head,
                    review_finalization_sha256=finalization_sha,
                    review_physical_identity_sha256=physical_identity_sha256,
                    report=report,
                )
                self._namespace.close()
                self._phase = DurableReviewPhase.FINALIZED
                return receipt
            except BaseException:
                if terminal_manifest_started:
                    self._phase = DurableReviewPhase.STOPPED
                    try:
                        self._namespace.close()
                    except BaseException:
                        pass
                else:
                    self._write_stop(
                        "review-finalization-persistence-failed",
                        reviewed_at_utc,
                    )
                raise


def begin_durable_review(
    root: str | os.PathLike[str],
    acquisition_root: str | os.PathLike[str],
    acquisition_expectation: DurableAcquisitionFilesystemExpectation,
    *,
    review_id: str,
    reviewer_id: str,
    started_at_utc: str,
) -> DurableReviewTransaction:
    """Reserve a fresh trusted-parent review root after strict acquisition intake."""

    review_path = _absolute_path_once(root, "durable review root")
    return _begin_durable_review_with_namespace_factory(
        review_path,
        acquisition_root,
        acquisition_expectation,
        review_id=review_id,
        reviewer_id=reviewer_id,
        started_at_utc=started_at_utc,
        namespace_factory=_ExclusiveNamespace,
    )


def _begin_durable_review_with_namespace_factory(
    review_path: Path,
    acquisition_root: str | os.PathLike[str],
    acquisition_expectation: DurableAcquisitionFilesystemExpectation,
    *,
    review_id: str,
    reviewer_id: str,
    started_at_utc: str,
    namespace_factory: _NamespaceFactory,
) -> DurableReviewTransaction:
    """Initialize one independent review with a navigation-owned namespace."""

    _identifier(review_id, "review_id")
    _identifier(reviewer_id, "reviewer_id")
    started_at = _utc(started_at_utc, "review started_at_utc")
    acquisition_path = _absolute_path_once(acquisition_root, "durable acquisition root")
    acquisition = load_durable_acquisition(acquisition_path, acquisition_expectation)
    package = acquisition.package
    if reviewer_id.casefold() == package.campaign_plan.operator_id.casefold():
        raise RouteEvidenceIntegrityError("independent reviewer must differ from operator")
    if started_at <= _utc(package.finalized_at_utc, "package finalized_at_utc"):
        raise RouteEvidenceIntegrityError("review must begin after package finalization")
    acquisition_resolved = acquisition_path.resolve(strict=True)
    review_parent_resolved = _assert_existing_directory_chain(
        review_path.parent,
        "durable review parent",
    )
    prospective_review = review_parent_resolved / review_path.name
    if _paths_overlap(acquisition_resolved, prospective_review):
        raise RouteEvidenceIntegrityError(
            "acquisition and review transaction roots must be physically disjoint"
        )
    plan_payload = _canonical(
        _review_plan_json(
            package,
            acquisition.expectation,
            review_id=review_id,
            reviewer_id=reviewer_id,
            started_at_utc=started_at_utc,
        )
    )
    namespace = namespace_factory(review_path)
    try:
        namespace.mkdir("truth")
        plan_sha = namespace.write(REVIEW_PLAN_FILENAME, plan_payload)
    except BaseException:
        try:
            namespace.close()
        except BaseException:
            pass
        raise
    return DurableReviewTransaction(
        namespace,
        acquisition_path,
        acquisition,
        review_id=review_id,
        reviewer_id=reviewer_id,
        started_at_utc=started_at_utc,
        review_plan_sha256=plan_sha,
        _factory_token=_FACTORY_TOKEN,
    )


def load_and_verify_durable_synthetic_route_evidence(
    acquisition_root: str | os.PathLike[str],
    review_root: str | os.PathLike[str],
    expectation: DurableRouteEvidenceFilesystemExpectation,
) -> RouteEvidenceVerificationReport:
    """Verify exact separate roots without granting release or input authority."""

    acquisition_path = _absolute_path_once(acquisition_root, "durable acquisition root")
    review_path = _absolute_path_once(review_root, "durable review root")
    owned_expectation = _snapshot_full_expectation(expectation)
    acquisition_expectation = DurableAcquisitionFilesystemExpectation(
        finalized_package_sha256=owned_expectation.finalized_package_sha256,
        acquisition_head_sha256=owned_expectation.acquisition_head_sha256,
        campaign_id=owned_expectation.campaign_id,
        route=owned_expectation.route,
        direction=owned_expectation.direction,
        route_plan_sha256=owned_expectation.route_plan_sha256,
        detector=owned_expectation.detector,
        profile=owned_expectation.profile,
        capture_source_id=owned_expectation.capture_source_id,
        capture_session_id=owned_expectation.capture_session_id,
        capture_build=owned_expectation.capture_build,
        frame_width=owned_expectation.frame_width,
        frame_height=owned_expectation.frame_height,
        pixel_format=owned_expectation.pixel_format,
        capture_configuration_sha256=owned_expectation.capture_configuration_sha256,
        capture_environment_sha256=owned_expectation.capture_environment_sha256,
        support_envelope_sha256=owned_expectation.support_envelope_sha256,
        acquisition_journal_head_sha256=(owned_expectation.acquisition_journal_head_sha256),
        acquisition_finalization_sha256=(owned_expectation.acquisition_finalization_sha256),
        acquisition_physical_identity_sha256=(
            owned_expectation.acquisition_physical_identity_sha256
        ),
    )
    acquisition = load_durable_acquisition(acquisition_path, acquisition_expectation)
    package = acquisition.package

    acquisition_resolved = acquisition_path.resolve(strict=True)
    review_resolved = review_path.resolve(strict=True)
    if _paths_overlap(acquisition_resolved, review_resolved):
        raise RouteEvidenceIntegrityError(
            "acquisition and review transaction roots must be physically disjoint"
        )
    root_path, root_signature = _intake_root(review_path, "durable review root")
    initial_plan = _read_owned_file(root_path, REVIEW_PLAN_FILENAME, _MAX_MANIFEST_BYTES)
    initial_finalization = _read_owned_file(
        root_path, REVIEW_FINALIZATION_FILENAME, _MAX_MANIFEST_BYTES
    )
    initial_finalization_object = _strict_canonical_object(
        initial_finalization.payload,
        "durable review finalization",
    )
    transaction_root_identity = _writer_directory_identity(root_signature)
    if initial_finalization_object.get("transaction_root_identity") != list(
        transaction_root_identity
    ):
        raise RouteEvidenceIntegrityError("durable review transaction root identity differs")
    initial_review = _read_owned_file(root_path, INDEPENDENT_REVIEW_FILENAME, _MAX_MANIFEST_BYTES)
    review = _parse_review(initial_review.payload)
    if review.content_sha256 != owned_expectation.independent_review_sha256:
        raise RouteEvidenceIntegrityError("durable independent review digest differs")
    if review.reviewer_id != owned_expectation.reviewer_id:
        raise RouteEvidenceIntegrityError("durable independent reviewer differs")
    if tuple(item.case_id for item in review.cases) != tuple(
        item.case_id for item in package.cases
    ):
        raise RouteEvidenceIntegrityError(
            "durable review truth coverage/order differs from acquisition"
        )

    plan_object = _strict_canonical_object(initial_plan.payload, "durable review plan")
    _exact_keys(
        plan_object,
        set(
            _review_plan_json(
                package,
                acquisition.expectation,
                review_id=owned_expectation.review_id,
                reviewer_id=owned_expectation.reviewer_id,
                started_at_utc="2000-01-01T00:00:00Z",
            )
        ),
        "durable review plan",
    )
    started_at_utc = _string(plan_object["started_at_utc"], "review plan started_at_utc")
    expected_plan = _canonical(
        _review_plan_json(
            package,
            acquisition.expectation,
            review_id=owned_expectation.review_id,
            reviewer_id=owned_expectation.reviewer_id,
            started_at_utc=started_at_utc,
        )
    )
    if initial_plan.payload != expected_plan:
        raise RouteEvidenceIntegrityError("durable review plan binding differs")
    plan_sha = Sha256Digest.from_bytes(expected_plan)
    if plan_sha != owned_expectation.review_plan_sha256:
        raise RouteEvidenceIntegrityError("durable review plan digest differs")
    started_at = _utc(started_at_utc, "review plan started_at_utc")
    if started_at <= _utc(package.finalized_at_utc, "package finalized_at_utc"):
        raise RouteEvidenceIntegrityError("durable review began before acquisition finalization")

    truth_paths = {_truth_path(owned) for owned in package.cases}
    expected_files = {
        REVIEW_PLAN_FILENAME,
        REVIEW_FINALIZATION_FILENAME,
        INDEPENDENT_REVIEW_FILENAME,
        *truth_paths,
    }
    size_limits = {relative: _MAX_MANIFEST_BYTES for relative in expected_files}
    initial_tree = _assert_exact_tree(root_path, expected_files)
    if _physical_identity_sha256(root_signature, initial_tree) != (
        owned_expectation.review_physical_identity_sha256
    ):
        raise RouteEvidenceIntegrityError("durable review physical identity differs")
    snapshots = {
        relative: _read_owned_file(root_path, relative, size_limits[relative])
        for relative in sorted(expected_files)
    }
    if snapshots[REVIEW_PLAN_FILENAME].payload != initial_plan.payload:
        raise RouteEvidenceIntegrityError("durable review plan changed during intake")
    if snapshots[REVIEW_FINALIZATION_FILENAME].payload != initial_finalization.payload:
        raise RouteEvidenceIntegrityError("durable review finalization changed during intake")
    if snapshots[INDEPENDENT_REVIEW_FILENAME].payload != initial_review.payload:
        raise RouteEvidenceIntegrityError("durable independent review changed during intake")

    journal_head = plan_sha
    previous_time = started_at
    for owned, truth in zip(package.cases, review.cases, strict=True):
        path = _truth_path(owned)
        truth_object = _strict_canonical_object(
            snapshots[path].payload,
            f"durable review truth {owned.case_id}",
        )
        expected_keys = set(
            _truth_record_json(
                package,
                truth,
                ordinal=owned.sequence_index,
                review_id=owned_expectation.review_id,
                review_plan_sha256=plan_sha,
                previous_journal_sha256=journal_head,
                recorded_at_utc="2000-01-01T00:00:00Z",
            )
        )
        _exact_keys(truth_object, expected_keys, f"durable review truth {owned.case_id}")
        recorded_at_utc = _string(
            truth_object["recorded_at_utc"],
            f"durable review truth time {owned.case_id}",
        )
        recorded_at = _utc(recorded_at_utc, "review truth recorded_at_utc")
        if recorded_at <= previous_time:
            raise RouteEvidenceIntegrityError("durable review truth chronology is not strict")
        expected_truth = _canonical(
            _truth_record_json(
                package,
                truth,
                ordinal=owned.sequence_index,
                review_id=owned_expectation.review_id,
                review_plan_sha256=plan_sha,
                previous_journal_sha256=journal_head,
                recorded_at_utc=recorded_at_utc,
            )
        )
        if snapshots[path].payload != expected_truth:
            raise RouteEvidenceIntegrityError(
                f"durable review truth lineage differs: {owned.case_id}"
            )
        journal_head = Sha256Digest.from_bytes(expected_truth)
        previous_time = recorded_at
    if journal_head != owned_expectation.review_journal_head_sha256:
        raise RouteEvidenceIntegrityError("durable review journal head differs")
    if _utc(review.reviewed_at_utc, "review reviewed_at_utc") <= previous_time:
        raise RouteEvidenceIntegrityError("review finalization does not follow truth chronology")
    expected_finalization = _canonical(
        _review_finalization_json(
            package,
            review,
            review_id=owned_expectation.review_id,
            review_plan_sha256=plan_sha,
            journal_head_sha256=journal_head,
            transaction_root_identity=transaction_root_identity,
        )
    )
    if initial_finalization.payload != expected_finalization:
        raise RouteEvidenceIntegrityError("durable review finalization lineage differs")
    if Sha256Digest.from_bytes(expected_finalization) != (
        owned_expectation.review_finalization_sha256
    ):
        raise RouteEvidenceIntegrityError("durable review finalization digest differs")
    report = verify_synthetic_route_evidence(
        package,
        review,
        acquisition.artifacts,
        owned_expectation,
    )
    _assert_stable_intake(root_path, root_signature, initial_tree, snapshots, size_limits)
    # Re-intake acquisition after review verification to close cross-root TOCTOU.
    final_acquisition = load_durable_acquisition(acquisition_path, acquisition_expectation)
    if final_acquisition.filesystem_identity != acquisition.filesystem_identity:
        raise RouteEvidenceIntegrityError(
            "acquisition filesystem identity changed during durable review verification"
        )
    _assert_stable_intake(root_path, root_signature, initial_tree, snapshots, size_limits)
    return report
