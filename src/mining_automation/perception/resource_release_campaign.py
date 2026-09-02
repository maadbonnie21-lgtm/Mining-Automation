"""Passive, provenance-bound resource release evidence campaign.

This development-only module owns the fixed PR #39 resource evidence plan.  It
does not move the camera, manipulate RuneLite, authorize interaction, or alter
production detector policy.  Operator staging is retained as an explicitly
unverified assertion; only a later, immutable reviewer record supplies truth.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import math
import os
import platform
import re
import struct
import subprocess
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from importlib.resources import files
from pathlib import Path
from typing import Final, cast

from ..capture import CaptureSource, Frame, PixelFormat, RawFrame
from ..capture.windows import DEFAULT_TITLE_SUBSTRING, WindowsCaptureBackend
from ..contracts import Observation, ResourceState
from .production_profiles import (
    VARROCK_EAST_IRON_DETECTOR_ID,
    VARROCK_EAST_IRON_DETECTOR_VERSION,
    VARROCK_EAST_IRON_FIXED_UI_REGIONS,
    VARROCK_EAST_IRON_PROFILE_ID,
    VARROCK_EAST_IRON_RESOURCE_IDS,
    load_varrock_east_iron_profile,
)
from .production_resource_pipeline import evaluate_varrock_east_iron_frame
from .resource import RESOURCE_PROFILE_SCHEMA_VERSION, ResourceVisualState
from .scene_landmarks import evaluate_scene

__all__ = [
    "CAMPAIGN_PLAN",
    "LIVE_RESOURCE_CAMPAIGN_AUTHORIZED",
    "RESOURCE_RELEASE_CAMPAIGN_ID",
    "RESOURCE_RELEASE_CAMPAIGN_SCHEMA_VERSION",
    "RESOURCE_RELEASE_CAMPAIGN_VERSION",
    "CampaignCase",
    "CampaignError",
    "CampaignIntegrityError",
    "CampaignStatus",
    "CaptureEnvironment",
    "NodeCyclePhase",
    "RepositoryProvenance",
    "ReviewDecision",
    "ReviewMeaning",
    "create_campaign",
    "evaluate_release",
    "export_review_package",
    "load_campaign_status",
    "load_review_decision",
    "prepare_case_review",
    "prepare_release_followup_inputs",
    "read_repository_provenance",
    "review_template_for_case",
    "review_decision_from_json",
    "record_case_review",
    "seal_campaign",
    "verify_review_package",
    "verify_release_followup_inputs",
    "write_release_summary",
]

RESOURCE_RELEASE_CAMPAIGN_SCHEMA_VERSION: Final[int] = 1
RESOURCE_RELEASE_CAMPAIGN_VERSION: Final[str] = "1.1.0"
RESOURCE_RELEASE_CAMPAIGN_ID: Final[str] = "varrock-east-iron-release-v1"
RESOURCE_RELEASE_CONFIGURATION_ID: Final[str] = (
    "resource-release-campaign:varrock-east-iron-v1@1.1.0"
)
RESOURCE_RELEASE_PRIVACY_MASK_ID: Final[str] = (
    "varrock-east-fixed-ui-opaque-v1"
)
_LIVE_CAPTURE_BACKEND_NAME: Final[str] = "windows-runelite"
_LIVE_CAPTURE_TITLE_MATCH: Final[str] = "RuneLite"
_SOURCE_OWNED_EVIDENCE_ORIGIN: Final[str] = "source-owned-windows-runelite"
_INJECTED_EVIDENCE_ORIGIN: Final[str] = "test-injected-non-release"
_REQUIRED_REPORTED_DPI: Final[int] = 96
_FOLLOWUP_ARTIFACT_ID: Final[str] = "resource-release-followup-inputs-v1"
_FOLLOWUP_CONFIGURATION_ID: Final[str] = (
    "resource-release-followup:varrock-east-iron-v1@1.0.0"
)
_FOLLOWUP_REGRESSION_DATASET_ID: Final[str] = (
    "varrock-east-iron-release-regressions-v1"
)
_SOURCE_OWNED_CAPTURE_CAPABILITY: Final[object] = object()
_INJECTED_CAPTURE_CAPABILITY: Final[object] = object()

# Source-owned live gate.  This PR prepares the campaign but does not authorize
# RuneLite capture.  A later, separately reviewed enable-only change is needed.
LIVE_RESOURCE_CAMPAIGN_AUTHORIZED: Final[bool] = False

_SHA256_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}\Z")
_GIT_SHA_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{40}\Z")
_MAX_PUBLIC_JSON_BYTES: Final[int] = 4 * 1024 * 1024
_MAX_FOLLOWUP_JSON_BYTES: Final[int] = 16 * 1024 * 1024
_IDENTIFIER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z"
)


def _valid_capture_origin_pair(backend_name: object, evidence_origin: object) -> bool:
    return (
        isinstance(backend_name, str)
        and bool(backend_name.strip())
        and evidence_origin
        in {_SOURCE_OWNED_EVIDENCE_ORIGIN, _INJECTED_EVIDENCE_ORIGIN}
        and (
            evidence_origin != _SOURCE_OWNED_EVIDENCE_ORIGIN
            or backend_name == _LIVE_CAPTURE_BACKEND_NAME
        )
    )


def _is_strict_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class CampaignError(RuntimeError):
    """Base failure for the passive resource release campaign."""


class CampaignIntegrityError(CampaignError):
    """Stored campaign evidence failed strict provenance or hash validation."""


class ReviewMeaning(StrEnum):
    """Independent reviewer meaning; never inferred from an operator label."""

    SUPPORTED_STARTUP = "supported-startup"
    PROFILED_NODE_STATE = "profiled-node-state"
    PROFILED_OBSTRUCTION = "profiled-obstruction"
    UNSUPPORTED_LOCATION = "unsupported-location"
    NEIGHBORING_COPPER = "neighboring-copper"
    NEIGHBORING_TIN = "neighboring-tin"
    TERRAIN_CLUTTER = "terrain-clutter"
    UNREVIEWABLE_PIXELS_WITHHELD = "unreviewable-pixels-withheld"


class NodeCyclePhase(StrEnum):
    """Reviewer-confirmed static phase within one ordered node cycle."""

    INITIAL_AVAILABLE = "initial-available"
    DEPLETED = "depleted"
    RESPAWN = "respawn"


@dataclass(frozen=True, slots=True)
class CampaignCase:
    """One fixed, manually staged, single-observation campaign step."""

    ordinal: int
    case_id: str
    blocker_id: str
    operator_prompt: str
    review_meaning: ReviewMeaning
    focal_resource_id: str | None = None
    requested_focal_state: ResourceVisualState | None = None
    requested_node_phase: NodeCyclePhase | None = None

    def __post_init__(self) -> None:
        if self.ordinal <= 0:
            raise ValueError("campaign case ordinal must be positive")
        for value, name in (
            (self.case_id, "case_id"),
            (self.blocker_id, "blocker_id"),
            (self.operator_prompt, "operator_prompt"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if not _IDENTIFIER_PATTERN.fullmatch(self.case_id):
            raise ValueError("case_id is not portable")
        if not _IDENTIFIER_PATTERN.fullmatch(self.blocker_id):
            raise ValueError("blocker_id is not portable")
        if not isinstance(self.review_meaning, ReviewMeaning):
            raise ValueError("review_meaning must be ReviewMeaning")
        if self.focal_resource_id is not None and self.focal_resource_id not in (
            VARROCK_EAST_IRON_RESOURCE_IDS
        ):
            raise ValueError("focal_resource_id is not a packaged resource")
        if self.requested_focal_state is not None and not isinstance(
            self.requested_focal_state, ResourceVisualState
        ):
            raise ValueError("requested_focal_state must be ResourceVisualState")
        if not isinstance(self.requested_node_phase, NodeCyclePhase | None):
            raise ValueError("requested_node_phase must be NodeCyclePhase or None")
        if len(
            {
                self.focal_resource_id is None,
                self.requested_focal_state is None,
                self.requested_node_phase is None,
            }
        ) != 1:
            raise ValueError("focal resource, state, and phase must be set together")


_NORTHWEST: Final[str] = VARROCK_EAST_IRON_RESOURCE_IDS[0]
_CENTER: Final[str] = VARROCK_EAST_IRON_RESOURCE_IDS[2]
_NORTHEAST: Final[str] = VARROCK_EAST_IRON_RESOURCE_IDS[3]

CAMPAIGN_PLAN: Final[tuple[CampaignCase, ...]] = (
    CampaignCase(
        1,
        "supported-startup-positive",
        "fresh-supported-startup",
        "Manually stage the exact reviewed supported Varrock East view; do not move it automatically.",
        ReviewMeaning.SUPPORTED_STARTUP,
    ),
    CampaignCase(
        2,
        "northwest-available",
        "northwest-cycle",
        "Manually show the north-west profiled iron node available.",
        ReviewMeaning.PROFILED_NODE_STATE,
        _NORTHWEST,
        ResourceVisualState.AVAILABLE,
        NodeCyclePhase.INITIAL_AVAILABLE,
    ),
    CampaignCase(
        3,
        "northwest-depleted",
        "northwest-cycle",
        "Manually mine the north-west node and stage its genuinely depleted presentation.",
        ReviewMeaning.PROFILED_NODE_STATE,
        _NORTHWEST,
        ResourceVisualState.DEPLETED,
        NodeCyclePhase.DEPLETED,
    ),
    CampaignCase(
        4,
        "northwest-respawn",
        "northwest-cycle",
        "Wait for and manually confirm the north-west node's genuine respawn presentation.",
        ReviewMeaning.PROFILED_NODE_STATE,
        _NORTHWEST,
        ResourceVisualState.AVAILABLE,
        NodeCyclePhase.RESPAWN,
    ),
    CampaignCase(
        5,
        "center-available",
        "center-cycle",
        "Manually show the center profiled iron node available.",
        ReviewMeaning.PROFILED_NODE_STATE,
        _CENTER,
        ResourceVisualState.AVAILABLE,
        NodeCyclePhase.INITIAL_AVAILABLE,
    ),
    CampaignCase(
        6,
        "center-depleted",
        "center-cycle",
        "Manually mine the center node and stage its genuinely depleted presentation.",
        ReviewMeaning.PROFILED_NODE_STATE,
        _CENTER,
        ResourceVisualState.DEPLETED,
        NodeCyclePhase.DEPLETED,
    ),
    CampaignCase(
        7,
        "center-respawn",
        "center-cycle",
        "Wait for and manually confirm the center node's genuine respawn presentation.",
        ReviewMeaning.PROFILED_NODE_STATE,
        _CENTER,
        ResourceVisualState.AVAILABLE,
        NodeCyclePhase.RESPAWN,
    ),
    CampaignCase(
        8,
        "northeast-available",
        "northeast-cycle",
        "Manually show the north-east profiled iron node available.",
        ReviewMeaning.PROFILED_NODE_STATE,
        _NORTHEAST,
        ResourceVisualState.AVAILABLE,
        NodeCyclePhase.INITIAL_AVAILABLE,
    ),
    CampaignCase(
        9,
        "northeast-depleted",
        "northeast-cycle",
        "Manually mine the north-east node and stage its genuinely depleted presentation.",
        ReviewMeaning.PROFILED_NODE_STATE,
        _NORTHEAST,
        ResourceVisualState.DEPLETED,
        NodeCyclePhase.DEPLETED,
    ),
    CampaignCase(
        10,
        "northeast-respawn",
        "northeast-cycle",
        "Wait for and manually confirm the north-east node's genuine respawn presentation.",
        ReviewMeaning.PROFILED_NODE_STATE,
        _NORTHEAST,
        ResourceVisualState.AVAILABLE,
        NodeCyclePhase.RESPAWN,
    ),
    CampaignCase(
        11,
        "profiled-obstruction",
        "profiled-obstruction",
        "Manually stage one genuine obstruction over a profiled candidate sample or world landmark.",
        ReviewMeaning.PROFILED_OBSTRUCTION,
    ),
    CampaignCase(
        12,
        "unsupported-location",
        "unsupported-location",
        "Manually stage a genuinely unsupported location at the exact client geometry.",
        ReviewMeaning.UNSUPPORTED_LOCATION,
    ),
    CampaignCase(
        13,
        "neighboring-copper",
        "neighboring-copper",
        "Manually stage the neighboring copper negative without changing detector policy.",
        ReviewMeaning.NEIGHBORING_COPPER,
    ),
    CampaignCase(
        14,
        "neighboring-tin",
        "neighboring-tin",
        "Manually stage the neighboring tin negative without changing detector policy.",
        ReviewMeaning.NEIGHBORING_TIN,
    ),
    CampaignCase(
        15,
        "terrain-clutter",
        "terrain-clutter",
        "Manually stage the terrain-clutter negative without changing detector policy.",
        ReviewMeaning.TERRAIN_CLUTTER,
    ),
)

_RESOURCE_BLOCKER_ORDER: Final[tuple[str, ...]] = (
    "fresh-supported-startup",
    "northwest-cycle",
    "center-cycle",
    "northeast-cycle",
    "profiled-obstruction",
    "unsupported-location",
    "neighboring-copper",
    "neighboring-tin",
    "terrain-clutter",
)
_FINAL_REVIEW_BLOCKER_ID: Final[str] = (
    "final-constrained-v1-operating-envelope-review"
)
_FINAL_REVIEW_REASON: Final[str] = (
    "final envelope review, failure replay promotion, and source release record "
    "cannot be self-approved by this harness"
)
_C2_GATE_ORDER: Final[tuple[str, ...]] = (
    "retained-failure-permanent-replay-promotion",
    _FINAL_REVIEW_BLOCKER_ID,
    "source-owned-constrained-v1-resource-release-promotion-record",
)
_C2_GATE_REASONS: Final[tuple[str, ...]] = (
    "each retained failure requires separate permanent replay promotion",
    "candidate DPI and exact client/renderer/profile envelope require lead review",
    "the source-owned release/promotion record requires separate approval",
)


@dataclass(frozen=True, slots=True)
class RepositoryProvenance:
    """Exact clean source identity bound to one campaign."""

    head_sha: str
    branch: str
    clean: bool

    def __post_init__(self) -> None:
        if not _GIT_SHA_PATTERN.fullmatch(self.head_sha):
            raise ValueError("head_sha must be a lowercase 40-character Git SHA")
        if not isinstance(self.branch, str) or not self.branch.strip():
            raise ValueError("branch must be a non-empty string")
        if not isinstance(self.clean, bool):
            raise ValueError("clean must be a boolean")


@dataclass(frozen=True, slots=True)
class CaptureEnvironment:
    """Observable private environment provenance for one passive capture."""

    backend_name: str
    title_match: str
    window_title: str | None = None
    window_class: str | None = None
    window_hwnd: int | None = None
    window_client_width: int | None = None
    window_client_height: int | None = None
    reported_dpi: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.backend_name, str) or not self.backend_name.strip():
            raise ValueError("backend_name must be a non-empty string")
        if not isinstance(self.title_match, str) or not self.title_match.strip():
            raise ValueError("title_match must be a non-empty string")
        for value, name in (
            (self.window_title, "window_title"),
            (self.window_class, "window_class"),
        ):
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{name} must be a string or None")
        for integer_value, name in (
            (self.window_hwnd, "window_hwnd"),
            (self.window_client_width, "window_client_width"),
            (self.window_client_height, "window_client_height"),
            (self.reported_dpi, "reported_dpi"),
        ):
            if integer_value is not None and (
                not isinstance(integer_value, int)
                or isinstance(integer_value, bool)
                or integer_value <= 0
            ):
                raise ValueError(f"{name} must be a positive integer or None")


@dataclass(frozen=True, slots=True)
class CampaignStatus:
    """Verified resumable state for a campaign directory."""

    session_id: str
    captured_case_ids: tuple[str, ...]
    next_case: CampaignCase | None
    next_case_no_frame_failures: int
    sealed: bool
    prepared_case_ids: tuple[str, ...]
    reviewed_case_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _VerifiedCaseSnapshot:
    """Exact validated bytes and manifest binding for one public case."""

    ordinal: int
    case_id: str
    case_review_sha256: str
    case_review_json: bytes
    sanitized_raw_gzip_path: str | None
    sanitized_raw_gzip_sha256: str | None
    sanitized_raw_gzip_bytes: bytes | None
    sanitized_decompressed_sha256: str | None


@dataclass(frozen=True, slots=True)
class _VerifiedReviewPackageSnapshot:
    """Validated package values consumed without reopening mutable JSON files."""

    package_dir: Path
    manifest_sha256: str
    release_summary_sha256: str
    manifest_json: bytes
    release_summary_json: bytes
    cases: tuple[_VerifiedCaseSnapshot, ...]


@dataclass(frozen=True, slots=True)
class _VerifiedFollowupSnapshot:
    """Validated follow-up bytes consumed without reopening mutable inputs."""

    path: Path
    sha256: str
    inputs_json: bytes
    inputs: dict[str, object]


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    """Explicit reviewer truth supplied independently from operator staging."""

    case_id: str
    reviewer_id: str
    reviewed_at_utc: datetime
    meaning: ReviewMeaning
    resource_truth: tuple[tuple[str, ResourceVisualState], ...]
    review_artifact_sha256: str
    privacy_review_confirmed: bool
    focal_resource_id: str | None = None
    node_phase: NodeCyclePhase | None = None
    obstruction_target_kind: str | None = None
    obstruction_target_id: str | None = None
    subject_region: tuple[int, int, int, int] | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if not _IDENTIFIER_PATTERN.fullmatch(self.case_id):
            raise ValueError("review case_id is not portable")
        if not _IDENTIFIER_PATTERN.fullmatch(self.reviewer_id):
            raise ValueError("reviewer_id is not portable")
        _require_utc(self.reviewed_at_utc, "reviewed_at_utc")
        if not isinstance(self.meaning, ReviewMeaning):
            raise ValueError("meaning must be ReviewMeaning")
        if not isinstance(self.privacy_review_confirmed, bool):
            raise ValueError("privacy_review_confirmed must be a boolean")
        if not isinstance(self.resource_truth, tuple):
            raise ValueError("resource_truth must be a tuple")
        if any(not isinstance(item, tuple) or len(item) != 2 for item in self.resource_truth):
            raise ValueError("resource_truth entries must be two-item tuples")
        ids = tuple(item[0] for item in self.resource_truth)
        if ids != VARROCK_EAST_IRON_RESOURCE_IDS:
            raise ValueError("resource_truth must cover all four resource IDs in order")
        if any(not isinstance(item[1], ResourceVisualState) for item in self.resource_truth):
            raise ValueError("resource_truth states must be ResourceVisualState")
        if not _SHA256_PATTERN.fullmatch(self.review_artifact_sha256):
            raise ValueError("review_artifact_sha256 must be a lowercase SHA-256")
        if self.focal_resource_id is not None and self.focal_resource_id not in (
            VARROCK_EAST_IRON_RESOURCE_IDS
        ):
            raise ValueError("review focal_resource_id is not a packaged resource")
        if not isinstance(self.node_phase, NodeCyclePhase | None):
            raise ValueError("review node_phase must be NodeCyclePhase or None")
        if (self.focal_resource_id is None) != (self.node_phase is None):
            raise ValueError("review focal resource and node phase must be set together")
        if (self.obstruction_target_kind is None) != (
            self.obstruction_target_id is None
        ):
            raise ValueError("obstruction target kind and id must be set together")
        if self.obstruction_target_kind is not None and self.obstruction_target_kind not in {
            "resource",
            "landmark",
        }:
            raise ValueError("obstruction_target_kind must be resource or landmark")
        if self.subject_region is not None:
            if (
                not isinstance(self.subject_region, tuple)
                or len(self.subject_region) != 4
                or any(
                    not isinstance(value, int) or isinstance(value, bool)
                    for value in self.subject_region
                )
            ):
                raise ValueError("subject_region must be a four-integer tuple")
            x, y, width, height = self.subject_region
            if x < 0 or y < 0 or width < 4 or height < 4:
                raise ValueError(
                    "subject_region must have non-negative origin and be at least 4x4"
                )
            profile = load_varrock_east_iron_profile()
            if x + width > profile.frame_width or y + height > profile.frame_height:
                raise ValueError("subject_region must stay inside reviewed frame geometry")
        if not isinstance(self.notes, str):
            raise ValueError("review notes must be a string")


def _require_utc(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    offset = value.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ValueError(f"{field_name} must use UTC")


def _utc_text(value: datetime) -> str:
    _require_utc(value, "datetime")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_utc(value: object, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise CampaignIntegrityError(f"{field_name} must be an ISO-8601 UTC string")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise CampaignIntegrityError(f"{field_name} is not valid ISO-8601") from exc
    _require_utc(parsed, field_name)
    return parsed


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_json_bytes(payload: Mapping[str, object]) -> bytes:
    try:
        text = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise CampaignError(f"campaign JSON contains an unsupported value: {exc}") from exc
    return (text + "\n").encode("utf-8")


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise CampaignIntegrityError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise CampaignIntegrityError(f"non-finite JSON number is forbidden: {value}")


def _strict_json_bytes(payload: bytes, *, label: str) -> dict[str, object]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CampaignIntegrityError(f"{label} is not UTF-8") from exc
    try:
        decoded: object = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
        )
    except json.JSONDecodeError as exc:
        raise CampaignIntegrityError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(decoded, dict):
        raise CampaignIntegrityError(f"{label} root must be an object")
    return cast(dict[str, object], decoded)


_OwnedFileIdentity = tuple[int, int, int, int, int]


def _identity_from_stat(value: os.stat_result) -> _OwnedFileIdentity | None:
    if value.st_ino <= 0:
        return None
    return (
        value.st_dev,
        value.st_ino,
        value.st_ctime_ns,
        value.st_mtime_ns,
        value.st_size,
    )


def _unlink_if_owned(path: Path, identity: _OwnedFileIdentity | None) -> None:
    if not _path_is_owned(path, identity):
        return

    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _path_is_owned(path: Path, identity: _OwnedFileIdentity | None) -> bool:
    if identity is None or path.is_symlink():
        return False
    try:
        current = _identity_from_stat(path.stat(follow_symlinks=False))
    except OSError:
        return False
    return current == identity


def _exclusive_write(path: Path, payload: bytes) -> _OwnedFileIdentity | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    identity: _OwnedFileIdentity | None = None
    try:
        with path.open("xb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
            # Record identity only after the final write.  Inode/device alone
            # are insufficient because unlink/recreate can immediately reuse
            # an inode; cleanup must never remove that concurrent winner.
            identity = _identity_from_stat(os.fstat(output.fileno()))
    except Exception:
        _unlink_if_owned(path, identity)
        raise
    return identity


def _artifact_sidecar(path: Path) -> Path:
    return path.with_name(f"{path.name}.sha256")


def _write_hashed_artifact(path: Path, payload: bytes) -> str:
    digest = _sha256(payload)
    sidecar = _artifact_sidecar(path)
    identity = _exclusive_write(path, payload)
    try:
        sidecar_payload = f"{digest}\n".encode("ascii")
        sidecar_identity = _exclusive_write(sidecar, sidecar_payload)
    except Exception:
        _unlink_if_owned(path, identity)
        raise
    try:
        publication_valid = (
            _path_is_owned(path, identity)
            and _path_is_owned(sidecar, sidecar_identity)
            and path.read_bytes() == payload
            and sidecar.read_bytes() == sidecar_payload
        )
    except OSError:
        publication_valid = False
    if not publication_valid:
        _unlink_if_owned(sidecar, sidecar_identity)
        _unlink_if_owned(path, identity)
        raise CampaignIntegrityError(
            f"campaign artifact changed during exclusive publication: {path}"
        )
    return digest


def _verify_hashed_artifact(
    path: Path,
    *,
    expected: str | None = None,
    maximum_bytes: int | None = None,
) -> tuple[bytes, str]:
    sidecar = _artifact_sidecar(path)
    if path.is_symlink() or sidecar.is_symlink():
        raise CampaignIntegrityError(f"campaign artifacts must not be symlinks: {path}")
    try:
        with path.open("rb") as source:
            payload = (
                source.read()
                if maximum_bytes is None
                else source.read(maximum_bytes + 1)
            )
        with sidecar.open("rb") as source:
            sidecar_payload = source.read(67)
    except OSError as exc:
        raise CampaignIntegrityError(f"missing campaign artifact: {path}") from exc
    if maximum_bytes is not None and len(payload) > maximum_bytes:
        raise CampaignIntegrityError(
            f"campaign artifact exceeds {maximum_bytes} bytes: {path}"
        )
    if len(sidecar_payload) not in {65, 66}:
        raise CampaignIntegrityError(f"SHA-256 sidecar size is malformed: {path}")
    try:
        sidecar_text = sidecar_payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise CampaignIntegrityError(f"SHA-256 sidecar is not ASCII: {path}") from exc
    digest = _sha256(payload)
    if sidecar_text not in {f"{digest}\n", f"{digest}\r\n"}:
        raise CampaignIntegrityError(f"SHA-256 sidecar mismatch: {path}")
    if expected is not None and digest != expected:
        raise CampaignIntegrityError(f"stored SHA-256 mismatch: {path}")
    return payload, digest


def _profile_identity() -> dict[str, object]:
    profile = load_varrock_east_iron_profile()
    profile_bytes = files("mining_automation.perception").joinpath(
        "profiles/varrock_east_iron_v1.json"
    ).read_bytes()
    return {
        "detector_id": VARROCK_EAST_IRON_DETECTOR_ID,
        "detector_version": VARROCK_EAST_IRON_DETECTOR_VERSION,
        "profile_id": VARROCK_EAST_IRON_PROFILE_ID,
        "profile_schema_version": RESOURCE_PROFILE_SCHEMA_VERSION,
        "profile_sha256": _sha256(profile_bytes),
        "location_id": profile.location_id,
        "frame_width": profile.frame_width,
        "frame_height": profile.frame_height,
        "pixel_format": profile.pixel_format.value,
        "resource_ids": list(VARROCK_EAST_IRON_RESOURCE_IDS),
        "landmark_quorum": profile.minimum_landmark_quorum,
        "landmark_zones": profile.minimum_landmark_zones,
    }


def _capture_configuration(
    *, live_source_authorized: bool = LIVE_RESOURCE_CAMPAIGN_AUTHORIZED
) -> dict[str, object]:
    return {
        "capture_backend": _LIVE_CAPTURE_BACKEND_NAME,
        "title_match": _LIVE_CAPTURE_TITLE_MATCH,
        "retry_attempts": 0,
        "automatic_camera_control": False,
        "automatic_camera_recovery": False,
        "input_allowed": False,
        "one_capture_per_observation": True,
        "required_evidence_origin": _SOURCE_OWNED_EVIDENCE_ORIGIN,
        "required_reported_dpi": _REQUIRED_REPORTED_DPI,
        "reported_dpi_requirement_status": "required-candidate-pending-fresh-review",
        # The default is frozen when this module is imported. Unit tests may
        # open the runtime gate around an injected source without forging the
        # source-owned build configuration bound into immutable evidence.
        "live_source_authorized": live_source_authorized,
    }


def _validate_capture_configuration(value: object) -> None:
    expected = _capture_configuration()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise CampaignIntegrityError("capture configuration fields changed")
    if (
        value.get("capture_backend") != _LIVE_CAPTURE_BACKEND_NAME
        or value.get("title_match") != _LIVE_CAPTURE_TITLE_MATCH
        or value.get("required_evidence_origin") != _SOURCE_OWNED_EVIDENCE_ORIGIN
        or not _is_strict_int(value.get("required_reported_dpi"))
        or value.get("required_reported_dpi") != _REQUIRED_REPORTED_DPI
        or value.get("reported_dpi_requirement_status")
        != "required-candidate-pending-fresh-review"
        or not _is_strict_int(value.get("retry_attempts"))
        or value.get("retry_attempts") != 0
    ):
        raise CampaignIntegrityError("capture configuration identity changed")
    boolean_fields = {
        "automatic_camera_control",
        "automatic_camera_recovery",
        "input_allowed",
        "one_capture_per_observation",
        "live_source_authorized",
    }
    if any(not isinstance(value.get(field), bool) for field in boolean_fields):
        raise CampaignIntegrityError("capture configuration boolean types changed")
    if value != expected:
        raise CampaignIntegrityError("source-owned capture configuration changed")


def _case_json(case: CampaignCase) -> dict[str, object]:
    return {
        "ordinal": case.ordinal,
        "case_id": case.case_id,
        "blocker_id": case.blocker_id,
        "operator_prompt": case.operator_prompt,
        "operator_label_role": "unverified-staging-only",
        "required_review_meaning": case.review_meaning.value,
        "focal_resource_id": case.focal_resource_id,
        "requested_focal_state": (
            None if case.requested_focal_state is None else case.requested_focal_state.value
        ),
        "requested_node_phase": (
            None if case.requested_node_phase is None else case.requested_node_phase.value
        ),
    }


def _plan_json() -> list[dict[str, object]]:
    return [_case_json(case) for case in CAMPAIGN_PLAN]


def _session_path(session_dir: Path) -> Path:
    return Path(session_dir) / "session.json"


def read_repository_provenance(repository: Path) -> RepositoryProvenance:
    """Read exact Git identity without modifying the repository."""

    repository = Path(repository)

    def run(*arguments: str) -> str:
        try:
            result = subprocess.run(
                ["git", *arguments],
                cwd=repository,
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise CampaignError(f"could not read Git provenance: {exc}") from exc
        return result.stdout.strip()

    return RepositoryProvenance(
        head_sha=run("rev-parse", "HEAD"),
        branch=run("rev-parse", "--abbrev-ref", "HEAD"),
        clean=not bool(run("status", "--porcelain=v1")),
    )


def create_campaign(
    root: Path,
    *,
    operator_id: str,
    repository: RepositoryProvenance,
    created_at_utc: datetime | None = None,
    nonce: str | None = None,
) -> Path:
    """Create one uniquely owned immutable campaign session."""

    if not _IDENTIFIER_PATTERN.fullmatch(operator_id):
        raise ValueError("operator_id must be a portable non-empty identifier")
    if not isinstance(repository, RepositoryProvenance):
        raise TypeError("repository must be RepositoryProvenance")
    if not repository.clean:
        raise CampaignError("campaign creation requires an exact clean Git worktree")
    created = datetime.now(UTC) if created_at_utc is None else created_at_utc
    _require_utc(created, "created_at_utc")
    token = uuid.uuid4().hex if nonce is None else nonce
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        raise ValueError("nonce must be 32 lowercase hexadecimal characters")
    stamp = created.strftime("%Y%m%dT%H%M%S.%fZ")
    session_id = f"resource-release-{stamp}-{token[:12]}"
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    session_dir = root / session_id
    session_dir.mkdir(exist_ok=False)
    session_token = _sha256(
        f"{session_id}\0{token}\0{repository.head_sha}\0{operator_id}".encode()
    )
    plan = _plan_json()
    session: dict[str, object] = {
        "schema_version": RESOURCE_RELEASE_CAMPAIGN_SCHEMA_VERSION,
        "campaign_id": RESOURCE_RELEASE_CAMPAIGN_ID,
        "campaign_version": RESOURCE_RELEASE_CAMPAIGN_VERSION,
        "configuration_id": RESOURCE_RELEASE_CONFIGURATION_ID,
        "session_id": session_id,
        "session_token": session_token,
        "created_at_utc": _utc_text(created),
        "operator": {
            "operator_id": operator_id,
            "role": "operator-staging-not-reviewer-truth",
        },
        "repository": {
            "head_sha": repository.head_sha,
            "branch": repository.branch,
            "clean": repository.clean,
        },
        "profile": _profile_identity(),
        "capture_configuration": _capture_configuration(),
        "host": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
        },
        "plan_sha256": _sha256(_canonical_json_bytes({"cases": plan})),
        "cases": plan,
    }
    try:
        _write_hashed_artifact(_session_path(session_dir), _canonical_json_bytes(session))
    except Exception:
        # The directory was exclusively created by this invocation and contains
        # no evidence if session publication failed.
        for child in session_dir.iterdir():
            child.unlink(missing_ok=True)
        session_dir.rmdir()
        raise
    return session_dir


def _require_exact_keys(
    payload: Mapping[str, object], expected: set[str], *, label: str
) -> None:
    if set(payload) != expected:
        raise CampaignIntegrityError(
            f"{label} fields mismatch; missing={sorted(expected - set(payload))}, "
            f"unknown={sorted(set(payload) - expected)}"
        )


def _load_session(session_dir: Path) -> dict[str, object]:
    session_dir = Path(session_dir)
    if session_dir.is_symlink():
        raise CampaignIntegrityError("campaign session directory must not be a symlink")
    allowed_root_names = {
        "session.json",
        "session.json.sha256",
        "private",
        "review",
        "completion-seal.json",
        "completion-seal.json.sha256",
    }
    try:
        foreign_root_names = {
            item.name for item in session_dir.iterdir() if item.name not in allowed_root_names
        }
    except OSError as exc:
        raise CampaignIntegrityError("campaign session directory is unavailable") from exc
    if foreign_root_names:
        raise CampaignIntegrityError(
            f"foreign campaign session artifacts: {sorted(foreign_root_names)}"
        )
    payload, _ = _verify_hashed_artifact(_session_path(session_dir))
    session = _strict_json_bytes(payload, label="campaign session")
    _require_exact_keys(
        session,
        {
            "schema_version",
            "campaign_id",
            "campaign_version",
            "configuration_id",
            "session_id",
            "session_token",
            "created_at_utc",
            "operator",
            "repository",
            "profile",
            "capture_configuration",
            "host",
            "plan_sha256",
            "cases",
        },
        label="campaign session",
    )
    if (
        not isinstance(session["schema_version"], int)
        or isinstance(session["schema_version"], bool)
        or session["schema_version"] != RESOURCE_RELEASE_CAMPAIGN_SCHEMA_VERSION
    ):
        raise CampaignIntegrityError("campaign schema version mismatch")
    if session["campaign_id"] != RESOURCE_RELEASE_CAMPAIGN_ID:
        raise CampaignIntegrityError("campaign identity mismatch")
    if session["campaign_version"] != RESOURCE_RELEASE_CAMPAIGN_VERSION:
        raise CampaignIntegrityError("campaign version mismatch")
    if session["configuration_id"] != RESOURCE_RELEASE_CONFIGURATION_ID:
        raise CampaignIntegrityError("campaign configuration mismatch")
    expected_cases = _plan_json()
    if session["cases"] != expected_cases:
        raise CampaignIntegrityError("campaign plan/order was modified")
    if not isinstance(session["cases"], list) or any(
        not isinstance(item, dict)
        or not isinstance(item.get("ordinal"), int)
        or isinstance(item.get("ordinal"), bool)
        for item in session["cases"]
    ):
        raise CampaignIntegrityError("campaign plan scalar types were modified")
    if session["plan_sha256"] != _sha256(
        _canonical_json_bytes({"cases": expected_cases})
    ):
        raise CampaignIntegrityError("campaign plan hash mismatch")
    if session["profile"] != _profile_identity():
        raise CampaignIntegrityError("detector/profile/schema/location identity changed")
    operator = session.get("operator")
    if not isinstance(operator, dict):
        raise CampaignIntegrityError("session operator identity is malformed")
    _require_exact_keys(operator, {"operator_id", "role"}, label="session operator")
    operator_id = operator.get("operator_id")
    if (
        not isinstance(operator_id, str)
        or not _IDENTIFIER_PATTERN.fullmatch(operator_id)
        or operator.get("role") != "operator-staging-not-reviewer-truth"
    ):
        raise CampaignIntegrityError("session operator identity/role changed")
    repository = session.get("repository")
    if not isinstance(repository, dict):
        raise CampaignIntegrityError("session repository provenance is malformed")
    _require_exact_keys(
        repository,
        {"head_sha", "branch", "clean"},
        label="session repository provenance",
    )
    try:
        RepositoryProvenance(
            head_sha=cast(str, repository.get("head_sha")),
            branch=cast(str, repository.get("branch")),
            clean=cast(bool, repository.get("clean")),
        )
    except (TypeError, ValueError) as exc:
        raise CampaignIntegrityError(
            f"session repository provenance is invalid: {exc}"
        ) from exc
    if repository.get("clean") is not True:
        raise CampaignIntegrityError("campaign repository provenance must be clean")
    _validate_capture_configuration(session.get("capture_configuration"))
    host = session.get("host")
    if not isinstance(host, dict):
        raise CampaignIntegrityError("session host provenance is malformed")
    _require_exact_keys(host, {"platform", "python"}, label="session host provenance")
    if any(
        not isinstance(host.get(field), str) or not cast(str, host[field]).strip()
        for field in ("platform", "python")
    ):
        raise CampaignIntegrityError("session host provenance is invalid")
    session_id = session["session_id"]
    if not isinstance(session_id, str) or session_dir.name != session_id:
        raise CampaignIntegrityError("session directory does not own this session")
    session_token = session["session_token"]
    if not isinstance(session_token, str) or not _SHA256_PATTERN.fullmatch(session_token):
        raise CampaignIntegrityError("session token is malformed")
    _parse_utc(session["created_at_utc"], "created_at_utc")
    return session


def _require_real_subdirectory(
    session_dir: Path, path: Path, *, label: str
) -> None:
    session_dir = Path(session_dir)
    path = Path(path)
    try:
        relative = path.relative_to(session_dir)
    except ValueError as exc:  # pragma: no cover - every caller uses fixed paths
        raise CampaignIntegrityError(f"{label} escaped the campaign session") from exc
    current = session_dir
    for component in relative.parts:
        current = current / component
        if current.exists() and current.is_symlink():
            raise CampaignIntegrityError(f"{label} contains a symbolic-link directory")
    if path.exists() and not path.is_dir():
        raise CampaignIntegrityError(f"{label} is not a directory")


def _case_stem(case: CampaignCase) -> str:
    return f"{case.ordinal:03d}-{case.case_id}"


def _case_dir(session_dir: Path, case: CampaignCase) -> Path:
    return Path(session_dir) / "private" / "captures" / _case_stem(case)


def _capture_record_path(session_dir: Path, case: CampaignCase) -> Path:
    return _case_dir(session_dir, case) / "capture-report.json"


def _capture_failure_paths(session_dir: Path, case: CampaignCase) -> tuple[Path, ...]:
    return tuple(sorted(_case_dir(session_dir, case).glob("capture-failure-*.json")))


def _owned_frame_path(session_dir: Path, case: CampaignCase) -> Path:
    return _case_dir(session_dir, case) / "owned-frame.json"


def _capture_reservation_path(session_dir: Path, case: CampaignCase) -> Path:
    return _case_dir(session_dir, case) / "capture-reservation.lock"


def _clear_owned_capture_reservation(path: Path, *, suppress_errors: bool) -> None:
    try:
        path.unlink()
    except OSError as exc:
        if not suppress_errors:
            raise CampaignIntegrityError(
                "owned capture reservation could not be released; session is fail-closed"
            ) from exc


def _verify_repository_binding(
    session: Mapping[str, object], repository: RepositoryProvenance
) -> None:
    if not repository.clean:
        raise CampaignIntegrityError("campaign operation requires a clean Git worktree")
    expected = session.get("repository")
    actual = {
        "head_sha": repository.head_sha,
        "branch": repository.branch,
        "clean": repository.clean,
    }
    if expected != actual:
        raise CampaignIntegrityError("campaign Git head/branch provenance changed")


def _frame_json(frame: Frame) -> dict[str, object]:
    return {
        "frame_id": frame.frame_id,
        "captured_monotonic_s": frame.captured_monotonic_s,
        "width": frame.width,
        "height": frame.height,
        "pixel_format": frame.pixel_format.value,
    }


def _environment_json(environment: CaptureEnvironment) -> dict[str, object]:
    return {
        "backend_name": environment.backend_name,
        "title_match": environment.title_match,
        "window_title": environment.window_title,
        "window_class": environment.window_class,
        "window_hwnd": environment.window_hwnd,
        "window_client_width": environment.window_client_width,
        "window_client_height": environment.window_client_height,
        "reported_dpi": environment.reported_dpi,
    }


def _validated_frame_scalars(
    value: object, *, label: str
) -> tuple[int, float, int, int, PixelFormat]:
    required = {
        "frame_id",
        "captured_monotonic_s",
        "width",
        "height",
        "pixel_format",
    }
    if not isinstance(value, dict):
        raise CampaignIntegrityError(f"{label} metadata is malformed")
    _require_exact_keys(value, required, label=f"{label} metadata")
    frame_id = value.get("frame_id")
    timestamp = value.get("captured_monotonic_s")
    width = value.get("width")
    height = value.get("height")
    pixel_format = value.get("pixel_format")
    if (
        not isinstance(frame_id, int)
        or isinstance(frame_id, bool)
        or frame_id <= 0
        or not isinstance(timestamp, int | float)
        or isinstance(timestamp, bool)
        or not math.isfinite(timestamp)
        or timestamp < 0
        or not isinstance(width, int)
        or isinstance(width, bool)
        or width <= 0
        or not isinstance(height, int)
        or isinstance(height, bool)
        or height <= 0
        or not isinstance(pixel_format, str)
    ):
        raise CampaignIntegrityError(f"{label} metadata has invalid scalar values")
    try:
        parsed_format = PixelFormat(pixel_format)
    except ValueError as exc:
        raise CampaignIntegrityError(f"{label} pixel format is invalid: {exc}") from exc
    return frame_id, float(timestamp), width, height, parsed_format


def _frame_from_metadata(
    value: object, payload: bytes, *, label: str
) -> Frame:
    frame_id, timestamp, width, height, pixel_format = _validated_frame_scalars(
        value, label=label
    )
    try:
        return Frame.from_raw(
            RawFrame(
                payload=payload,
                width=width,
                height=height,
                pixel_format=pixel_format,
            ),
            frame_id=frame_id,
            captured_monotonic_s=timestamp,
        )
    except (TypeError, ValueError) as exc:
        raise CampaignIntegrityError(f"{label} is invalid: {exc}") from exc


def _validated_capture_environment(
    value: object,
    *,
    frame: Frame,
    source_backend_name: str,
    evidence_origin: str,
) -> CaptureEnvironment:
    required = {
        "backend_name",
        "title_match",
        "window_title",
        "window_class",
        "window_hwnd",
        "window_client_width",
        "window_client_height",
        "reported_dpi",
    }
    if not isinstance(value, dict):
        raise CampaignIntegrityError("capture environment is malformed")
    _require_exact_keys(value, required, label="capture environment")
    try:
        environment = CaptureEnvironment(
            backend_name=cast(str, value.get("backend_name")),
            title_match=cast(str, value.get("title_match")),
            window_title=cast(str | None, value.get("window_title")),
            window_class=cast(str | None, value.get("window_class")),
            window_hwnd=cast(int | None, value.get("window_hwnd")),
            window_client_width=cast(int | None, value.get("window_client_width")),
            window_client_height=cast(int | None, value.get("window_client_height")),
            reported_dpi=cast(int | None, value.get("reported_dpi")),
        )
    except (TypeError, ValueError) as exc:
        raise CampaignIntegrityError(f"capture environment is invalid: {exc}") from exc
    if environment.backend_name != source_backend_name:
        raise CampaignIntegrityError("capture environment backend disagrees with source")
    if (
        evidence_origin == _SOURCE_OWNED_EVIDENCE_ORIGIN
        and source_backend_name != _LIVE_CAPTURE_BACKEND_NAME
    ):
        raise CampaignIntegrityError("source-owned capture backend identity changed")
    if environment.title_match != _LIVE_CAPTURE_TITLE_MATCH:
        raise CampaignIntegrityError("capture source/backend/title binding changed")
    if (
        environment.window_title is None
        or environment.window_class is None
        or environment.window_hwnd is None
        or environment.window_client_width is None
        or environment.window_client_height is None
    ):
        raise CampaignIntegrityError(
            "successful live capture lacks required native-window provenance"
        )
    if environment.title_match.casefold() not in environment.window_title.casefold():
        raise CampaignIntegrityError("captured window title does not match RuneLite")
    if (
        environment.window_client_width != frame.width
        or environment.window_client_height != frame.height
    ):
        raise CampaignIntegrityError(
            "capture environment geometry disagrees with the owned frame"
        )
    return environment


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, str | bool | int | float):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_json_safe(item) for item in value]
    raise CampaignError(f"production evidence is not JSON-serializable: {type(value).__name__}")


def _observation_json(observation: Observation) -> dict[str, object]:
    return {
        "kind": observation.kind,
        "detector_version": observation.detector_version,
        "frame": {
            "frame_id": observation.frame.frame_id,
            "captured_monotonic_s": observation.frame.captured_monotonic_s,
            "width": observation.frame.width,
            "height": observation.frame.height,
        },
        "confidence": observation.confidence,
        "evidence": _json_safe(observation.evidence),
    }


def _resource_json(resource: ResourceState) -> dict[str, object]:
    return {
        "resource_id": resource.resource_id,
        "resource_type": resource.resource_type,
        "available": resource.available,
        "confidence": resource.confidence,
        "interaction_region": (
            None if resource.interaction_region is None else list(resource.interaction_region)
        ),
    }


def _scene_json(frame: Frame) -> dict[str, object]:
    profile = load_varrock_east_iron_profile()
    if (
        frame.width != profile.frame_width
        or frame.height != profile.frame_height
        or frame.pixel_format is not profile.pixel_format
    ):
        return {
            "validated": False,
            "reason": "frame_geometry_or_pixel_format_mismatch",
            "matched_count": 0,
            "required_quorum": profile.minimum_landmark_quorum,
            "matched_zones": [],
            "required_zones": profile.minimum_landmark_zones,
            "landmarks": [],
            "authority": "read-only-summary-never-overrides-production",
        }
    verdict = evaluate_scene(
        frame,
        profile.scene_landmarks,
        required_quorum=profile.minimum_landmark_quorum,
        required_zones=profile.minimum_landmark_zones,
        frame_width=profile.frame_width,
        frame_height=profile.frame_height,
    )
    return {
        "validated": verdict.validated,
        "reason": verdict.detail,
        "matched_count": verdict.matched_count,
        "required_quorum": profile.minimum_landmark_quorum,
        "matched_zones": sorted(zone.value for zone in verdict.matched_zones),
        "required_zones": profile.minimum_landmark_zones,
        "landmarks": [
            {
                "landmark_id": match.landmark_id,
                "zone": match.zone.value,
                "distance": match.distance,
                "threshold": next(
                    landmark.maximum_distance
                    for landmark in profile.scene_landmarks
                    if landmark.landmark_id == match.landmark_id
                ),
                "matched": match.matched,
            }
            for match in verdict.matches
        ],
        "authority": "read-only-summary-never-overrides-production",
    }


def _production_json(frame: Frame) -> dict[str, object]:
    evaluation = evaluate_varrock_east_iron_frame(frame)
    observations = evaluation.observations
    trust = evaluation.trust
    resources = tuple(trust.resources)
    definitive_ids = [
        resource.resource_id for resource in resources if resource.available is not None
    ]
    actionable_ids = [resource.resource_id for resource in trust.actionable_targets]
    any_uncertain = any(resource.available is None for resource in resources)
    return {
        "status": "completed",
        "detector_id": VARROCK_EAST_IRON_DETECTOR_ID,
        "detector_version": VARROCK_EAST_IRON_DETECTOR_VERSION,
        "observations": [_observation_json(item) for item in observations],
        "trust": {
            "accepted": trust.accepted,
            "reason": trust.reason,
            "frame": None if trust.frame is None else _frame_json(frame),
            "resources": [_resource_json(item) for item in resources],
            "definitive_target_ids": definitive_ids,
            "production_actionable_target_ids": actionable_ids,
            "production_interaction_regions": {
                resource.resource_id: (
                    None
                    if resource.interaction_region is None
                    else list(resource.interaction_region)
                )
                for resource in resources
            },
        },
        "scene": _scene_json(frame),
        "passive_campaign_authorized_target_ids": [],
        "stop_required": (not trust.accepted) or any_uncertain,
        "input_authority": False,
    }


def _frame_to_bgra(frame: Frame) -> bytes:
    if frame.pixel_format is PixelFormat.BGRA8888:
        return frame.payload
    source = memoryview(frame.payload).cast("B")
    output = bytearray(frame.width * frame.height * 4)
    source_bpp = frame.pixel_format.bytes_per_pixel
    offset = 0
    for source_offset in range(0, len(source), source_bpp):
        if frame.pixel_format is PixelFormat.RGBA8888:
            red, green, blue, alpha = source[source_offset : source_offset + 4]
        elif frame.pixel_format is PixelFormat.RGB888:
            red, green, blue = source[source_offset : source_offset + 3]
            alpha = 255
        elif frame.pixel_format is PixelFormat.BGR888:
            blue, green, red = source[source_offset : source_offset + 3]
            alpha = 255
        elif frame.pixel_format is PixelFormat.GRAY8:
            red = green = blue = source[source_offset]
            alpha = 255
        else:  # pragma: no cover - PixelFormat is exhaustive
            raise CampaignError(f"unsupported preview format: {frame.pixel_format}")
        output[offset : offset + 4] = bytes((blue, green, red, alpha))
        offset += 4
    return bytes(output)


def _bmp_payload(frame: Frame) -> bytes:
    payload = _frame_to_bgra(frame)
    file_header_size = 14
    info_header_size = 40
    file_header = struct.pack(
        "<2sIHHI",
        b"BM",
        file_header_size + info_header_size + len(payload),
        0,
        0,
        file_header_size + info_header_size,
    )
    info_header = struct.pack(
        "<IiiHHIIiiII",
        info_header_size,
        frame.width,
        -frame.height,
        1,
        32,
        0,
        len(payload),
        0,
        0,
        0,
        0,
    )
    return file_header + info_header + payload


def _operator_id(session: Mapping[str, object]) -> str:
    operator = session.get("operator")
    if not isinstance(operator, dict) or set(operator) != {"operator_id", "role"}:
        raise CampaignIntegrityError("session operator identity is malformed")
    operator_id = operator.get("operator_id")
    if not isinstance(operator_id, str):
        raise CampaignIntegrityError("session operator id is malformed")
    return operator_id


def _preserve_capture_failure(
    session_dir: Path,
    session: Mapping[str, object],
    case: CampaignCase,
    *,
    captured_at_utc: datetime,
    stage: str,
    error: Exception,
    capture_source_backend_name: str,
    evidence_origin: str,
    raw_sha256: str | None = None,
    production_evaluation_count: int = 0,
) -> bool:
    prior = _capture_failure_paths(session_dir, case)
    attempt = len(prior) + 1
    failure_path = _case_dir(session_dir, case) / f"capture-failure-{attempt:03d}.json"
    failure: dict[str, object] = {
        "schema_version": RESOURCE_RELEASE_CAMPAIGN_SCHEMA_VERSION,
        "campaign_id": RESOURCE_RELEASE_CAMPAIGN_ID,
        "session_id": session["session_id"],
        "session_token": session["session_token"],
        "ordinal": case.ordinal,
        "case_id": case.case_id,
        "attempt": attempt,
        "attempted_at_utc": _utc_text(captured_at_utc),
        "stage": stage,
        "error_type": type(error).__name__,
        "error": str(error),
        "capture_source_backend_name": capture_source_backend_name,
        "evidence_origin": evidence_origin,
        "captured_raw_sha256": raw_sha256,
        "production_evaluation_count": production_evaluation_count,
        "automatic_retry_count": 0,
        "input_events": [],
        "terminal_for_session": stage != "capture-no-frame",
        "activation_allowed": False,
        "input_authority": False,
    }
    try:
        _write_hashed_artifact(failure_path, _canonical_json_bytes(failure))
    except (OSError, CampaignError):
        # Preserve the original capture/provenance error. The exclusive case
        # directory still prevents the failed observation from being hidden by
        # a recapture even if the supplemental failure report cannot publish.
        return False
    return True


def _load_capture_failures(
    session_dir: Path,
    session: Mapping[str, object],
    case: CampaignCase,
) -> tuple[dict[str, object], ...]:
    failures: list[dict[str, object]] = []
    required = {
        "schema_version",
        "campaign_id",
        "session_id",
        "session_token",
        "ordinal",
        "case_id",
        "attempt",
        "attempted_at_utc",
        "stage",
        "error_type",
        "error",
        "capture_source_backend_name",
        "evidence_origin",
        "captured_raw_sha256",
        "production_evaluation_count",
        "automatic_retry_count",
        "input_events",
        "terminal_for_session",
        "activation_allowed",
        "input_authority",
    }
    previous_attempt_time = _parse_utc(session.get("created_at_utc"), "created_at_utc")
    for expected_attempt, path in enumerate(
        _capture_failure_paths(session_dir, case), start=1
    ):
        if path.name != f"capture-failure-{expected_attempt:03d}.json":
            raise CampaignIntegrityError("capture failure filename/order is malformed")
        payload, _ = _verify_hashed_artifact(path)
        failure = _strict_json_bytes(payload, label=f"capture failure {case.case_id}")
        if set(failure) != required:
            raise CampaignIntegrityError("capture failure fields are malformed")
        if any(
            not _is_strict_int(failure.get(field))
            for field in (
                "schema_version",
                "ordinal",
                "attempt",
                "production_evaluation_count",
                "automatic_retry_count",
            )
        ):
            raise CampaignIntegrityError("capture failure integer scalar types changed")
        stage = failure.get("stage")
        if stage not in {
            "capture-no-frame",
            "post-capture-evidence-publication",
            "owned-frame-publication",
            "capture-report-publication",
        }:
            raise CampaignIntegrityError("capture failure stage is invalid")
        if (
            failure.get("schema_version") != RESOURCE_RELEASE_CAMPAIGN_SCHEMA_VERSION
            or failure.get("campaign_id") != RESOURCE_RELEASE_CAMPAIGN_ID
            or failure.get("session_id") != session.get("session_id")
            or failure.get("session_token") != session.get("session_token")
            or failure.get("case_id") != case.case_id
            or failure.get("ordinal") != case.ordinal
            or failure.get("attempt") != expected_attempt
            or failure.get("evidence_origin") not in {
                _SOURCE_OWNED_EVIDENCE_ORIGIN,
                _INJECTED_EVIDENCE_ORIGIN,
            }
            or failure.get("production_evaluation_count")
            != (1 if stage == "capture-report-publication" else 0)
            or failure.get("automatic_retry_count") != 0
            or failure.get("input_events") != []
            or failure.get("activation_allowed") is not False
            or failure.get("input_authority") is not False
            or failure.get("terminal_for_session") is not (stage != "capture-no-frame")
        ):
            raise CampaignIntegrityError("capture failure identity/policy is malformed")
        if any(
            not isinstance(failure.get(field), str)
            or not cast(str, failure[field]).strip()
            for field in ("error_type", "error")
        ):
            raise CampaignIntegrityError("capture failure error provenance is malformed")
        failure_backend = failure.get("capture_source_backend_name")
        if not _valid_capture_origin_pair(
            failure_backend, failure.get("evidence_origin")
        ):
            raise CampaignIntegrityError("capture failure source provenance is malformed")
        attempt_time = _parse_utc(failure.get("attempted_at_utc"), "attempted_at_utc")
        if attempt_time < previous_attempt_time:
            raise CampaignIntegrityError("capture failure chronology is stale or reordered")
        previous_attempt_time = attempt_time
        raw_sha = failure.get("captured_raw_sha256")
        if raw_sha is not None and not (
            isinstance(raw_sha, str) and _SHA256_PATTERN.fullmatch(raw_sha)
        ):
            raise CampaignIntegrityError("capture failure raw hash is malformed")
        if stage == "capture-no-frame" and raw_sha is not None:
            raise CampaignIntegrityError("no-frame failure cannot bind captured pixels")
        if stage in {"owned-frame-publication", "capture-report-publication"} and raw_sha is None:
            raise CampaignIntegrityError("post-frame failure must bind captured pixels")
        failures.append(failure)
    return tuple(failures)


def _capture_case(
    session_dir: Path,
    session: Mapping[str, object],
    case: CampaignCase,
    source: CaptureSource,
    environment_provider: Callable[[], CaptureEnvironment],
    captured_at_utc: datetime,
    evidence_origin: str,
) -> dict[str, object]:
    if evidence_origin not in {
        _SOURCE_OWNED_EVIDENCE_ORIGIN,
        _INJECTED_EVIDENCE_ORIGIN,
    }:
        raise CampaignIntegrityError("capture evidence origin is invalid")
    source_backend_name = source.backend_name
    if not isinstance(source_backend_name, str) or not source_backend_name.strip():
        raise CampaignIntegrityError(
            "capture source backend identity is missing"
        )
    if (
        evidence_origin == _SOURCE_OWNED_EVIDENCE_ORIGIN
        and source_backend_name != _LIVE_CAPTURE_BACKEND_NAME
    ):
        raise CampaignIntegrityError(
            "source-owned release evidence requires the windows-runelite backend"
        )
    case_dir = _case_dir(session_dir, case)
    prior_failures: tuple[dict[str, object], ...] = ()
    if case_dir.exists():
        prior_failures = _load_capture_failures(session_dir, session, case)
        if not prior_failures or any(
            item["stage"] != "capture-no-frame" for item in prior_failures
        ):
            raise CampaignIntegrityError("existing case directory is not retryable")
    else:
        case_dir.mkdir(parents=True, exist_ok=False)
    reservation_path = _capture_reservation_path(session_dir, case)
    try:
        _exclusive_write(
            reservation_path,
            _canonical_json_bytes(
                {
                    "session_id": session["session_id"],
                    "case_id": case.case_id,
                    "captured_at_utc": _utc_text(captured_at_utc),
                    "reservation_nonce": uuid.uuid4().hex,
                }
            ),
        )
    except FileExistsError as exc:
        raise CampaignIntegrityError(
            "capture case is already reserved by another invocation; zero pixels captured"
        ) from exc
    raw_path = case_dir / "frame.raw"
    preview_path = case_dir / "private-preview.bmp"
    try:
        frame = source.capture()
    except Exception as exc:
        failure_preserved = _preserve_capture_failure(
            session_dir,
            session,
            case,
            captured_at_utc=captured_at_utc,
            stage="capture-no-frame",
            error=exc,
            capture_source_backend_name=source_backend_name,
            evidence_origin=evidence_origin,
        )
        if failure_preserved:
            _clear_owned_capture_reservation(
                reservation_path, suppress_errors=True
            )
        raise
    # Retain an owned successful frame before any supplemental provenance or
    # detector step can fail. A partial case remains visible and deliberately
    # blocks recapture instead of silently replacing the observation.
    raw_sha: str | None = None
    try:
        raw_sha = _write_hashed_artifact(raw_path, frame.payload)
        preview_sha = _write_hashed_artifact(preview_path, _bmp_payload(frame))
        environment = environment_provider()
        if not isinstance(environment, CaptureEnvironment):
            raise TypeError("environment_provider must return CaptureEnvironment")
        _validated_capture_environment(
            _environment_json(environment),
            frame=frame,
            source_backend_name=source_backend_name,
            evidence_origin=evidence_origin,
        )
    except Exception as exc:
        failure_preserved = _preserve_capture_failure(
            session_dir,
            session,
            case,
            captured_at_utc=captured_at_utc,
            stage="post-capture-evidence-publication",
            error=exc,
            capture_source_backend_name=source_backend_name,
            evidence_origin=evidence_origin,
            raw_sha256=raw_sha,
        )
        if failure_preserved:
            _clear_owned_capture_reservation(
                reservation_path, suppress_errors=True
            )
        raise
    owned: dict[str, object] = {
        "schema_version": RESOURCE_RELEASE_CAMPAIGN_SCHEMA_VERSION,
        "campaign_id": RESOURCE_RELEASE_CAMPAIGN_ID,
        "session_id": session["session_id"],
        "session_token": session["session_token"],
        "ordinal": case.ordinal,
        "case_id": case.case_id,
        "captured_at_utc": _utc_text(captured_at_utc),
        "operator_stage": _case_json(case),
        "frame": _frame_json(frame),
        "raw": {
            "path": f"private/captures/{_case_stem(case)}/frame.raw",
            "sha256": raw_sha,
            "size_bytes": len(frame.payload),
        },
        "private_preview": {
            "path": f"private/captures/{_case_stem(case)}/private-preview.bmp",
            "sha256": preview_sha,
            "privacy_status": "private-unreviewed-do-not-commit",
        },
        "capture_source_backend_name": source_backend_name,
        "evidence_origin": evidence_origin,
        "capture_environment": _environment_json(environment),
    }
    try:
        owned_sha = _write_hashed_artifact(
            _owned_frame_path(session_dir, case), _canonical_json_bytes(owned)
        )
    except Exception as exc:
        failure_preserved = _preserve_capture_failure(
            session_dir,
            session,
            case,
            captured_at_utc=captured_at_utc,
            stage="owned-frame-publication",
            error=exc,
            capture_source_backend_name=source_backend_name,
            evidence_origin=evidence_origin,
            raw_sha256=raw_sha,
        )
        if failure_preserved:
            _clear_owned_capture_reservation(
                reservation_path, suppress_errors=True
            )
        raise
    try:
        production = _production_json(frame)
    except Exception as exc:
        production = _detector_error(exc)
    record: dict[str, object] = {
        "schema_version": RESOURCE_RELEASE_CAMPAIGN_SCHEMA_VERSION,
        "campaign_id": RESOURCE_RELEASE_CAMPAIGN_ID,
        "campaign_version": RESOURCE_RELEASE_CAMPAIGN_VERSION,
        "configuration_id": RESOURCE_RELEASE_CONFIGURATION_ID,
        "session_id": session["session_id"],
        "session_token": session["session_token"],
        "ordinal": case.ordinal,
        "case_id": case.case_id,
        "blocker_id": case.blocker_id,
        "captured_at_utc": _utc_text(captured_at_utc),
        "operator_stage": _case_json(case),
        "operator_stage_is_reviewer_truth": False,
        "owned_frame_sha256": owned_sha,
        "repository": session["repository"],
        "profile": session["profile"],
        "capture_configuration": session["capture_configuration"],
        "capture_source_backend_name": source_backend_name,
        "evidence_origin": evidence_origin,
        "frame": _frame_json(frame),
        "raw": owned["raw"],
        "private_preview": owned["private_preview"],
        "capture_environment": owned["capture_environment"],
        "production": production,
        "capture_count": 1,
        "capture_attempt_count": len(prior_failures) + 1,
        "prior_no_frame_failure_count": len(prior_failures),
        "prior_no_frame_failure_report_sha256s": [
            _verify_hashed_artifact(path)[1]
            for path in _capture_failure_paths(session_dir, case)
        ],
        "prior_no_frame_failure_provenance": [
            {
                "report_sha256": _verify_hashed_artifact(path)[1],
                "capture_source_backend_name": failure[
                    "capture_source_backend_name"
                ],
                "evidence_origin": failure["evidence_origin"],
            }
            for failure, path in zip(
                prior_failures,
                _capture_failure_paths(session_dir, case),
                strict=True,
            )
        ],
        "automatic_retry_count": 0,
        "input_events": [],
    }
    try:
        _write_hashed_artifact(
            _capture_record_path(session_dir, case), _canonical_json_bytes(record)
        )
    except Exception as exc:
        failure_preserved = _preserve_capture_failure(
            session_dir,
            session,
            case,
            captured_at_utc=captured_at_utc,
            stage="capture-report-publication",
            error=exc,
            capture_source_backend_name=source_backend_name,
            evidence_origin=evidence_origin,
            raw_sha256=raw_sha,
            production_evaluation_count=1,
        )
        if failure_preserved:
            _clear_owned_capture_reservation(
                reservation_path, suppress_errors=True
            )
        raise
    _clear_owned_capture_reservation(reservation_path, suppress_errors=False)
    return record


def _load_record(
    session_dir: Path, session: Mapping[str, object], case: CampaignCase
) -> tuple[dict[str, object], str]:
    record_path = _capture_record_path(session_dir, case)
    payload, digest = _verify_hashed_artifact(record_path)
    record = _strict_json_bytes(payload, label=f"capture report {case.case_id}")
    required = {
        "schema_version",
        "campaign_id",
        "campaign_version",
        "configuration_id",
        "session_id",
        "session_token",
        "ordinal",
        "case_id",
        "blocker_id",
        "captured_at_utc",
        "operator_stage",
        "operator_stage_is_reviewer_truth",
        "owned_frame_sha256",
        "repository",
        "profile",
        "capture_configuration",
        "capture_source_backend_name",
        "evidence_origin",
        "frame",
        "raw",
        "private_preview",
        "capture_environment",
        "production",
        "capture_count",
        "capture_attempt_count",
        "prior_no_frame_failure_count",
        "prior_no_frame_failure_report_sha256s",
        "prior_no_frame_failure_provenance",
        "automatic_retry_count",
        "input_events",
    }
    _require_exact_keys(record, required, label=f"capture report {case.case_id}")
    if any(
        not isinstance(record.get(field), int)
        or isinstance(record.get(field), bool)
        for field in (
            "schema_version",
            "ordinal",
            "capture_count",
            "capture_attempt_count",
            "prior_no_frame_failure_count",
            "automatic_retry_count",
        )
    ):
        raise CampaignIntegrityError("capture report integer scalar types changed")
    for key, expected in (
        ("schema_version", RESOURCE_RELEASE_CAMPAIGN_SCHEMA_VERSION),
        ("campaign_id", RESOURCE_RELEASE_CAMPAIGN_ID),
        ("campaign_version", RESOURCE_RELEASE_CAMPAIGN_VERSION),
        ("configuration_id", RESOURCE_RELEASE_CONFIGURATION_ID),
        ("session_id", session["session_id"]),
        ("session_token", session["session_token"]),
        ("ordinal", case.ordinal),
        ("case_id", case.case_id),
        ("blocker_id", case.blocker_id),
        ("operator_stage", _case_json(case)),
        ("operator_stage_is_reviewer_truth", False),
        ("repository", session["repository"]),
        ("profile", session["profile"]),
        ("capture_configuration", session["capture_configuration"]),
        ("capture_count", 1),
        ("automatic_retry_count", 0),
        ("input_events", []),
    ):
        if record.get(key) != expected:
            raise CampaignIntegrityError(
                f"capture report {case.case_id} has foreign or stale {key}"
            )
    failures = _load_capture_failures(session_dir, session, case)
    if any(item["stage"] != "capture-no-frame" for item in failures):
        raise CampaignIntegrityError("capture report follows a terminal failure")
    failure_hashes = [
        _verify_hashed_artifact(path)[1]
        for path in _capture_failure_paths(session_dir, case)
    ]
    failure_provenance = [
        {
            "report_sha256": digest,
            "capture_source_backend_name": failure["capture_source_backend_name"],
            "evidence_origin": failure["evidence_origin"],
        }
        for failure, digest in zip(failures, failure_hashes, strict=True)
    ]
    if (
        record.get("capture_attempt_count") != len(failures) + 1
        or record.get("prior_no_frame_failure_count") != len(failures)
        or record.get("prior_no_frame_failure_report_sha256s") != failure_hashes
        or record.get("prior_no_frame_failure_provenance") != failure_provenance
    ):
        raise CampaignIntegrityError("capture attempt history was replaced")
    captured = _parse_utc(record["captured_at_utc"], "captured_at_utc")
    if captured < _parse_utc(session["created_at_utc"], "created_at_utc"):
        raise CampaignIntegrityError(f"capture {case.case_id} predates the campaign")
    if failures:
        latest_failure = _parse_utc(
            failures[-1]["attempted_at_utc"], "attempted_at_utc"
        )
        if captured < latest_failure:
            raise CampaignIntegrityError("successful capture predates prior no-frame failure")
    raw = record.get("raw")
    preview = record.get("private_preview")
    if not isinstance(raw, dict) or set(raw) != {"path", "sha256", "size_bytes"}:
        raise CampaignIntegrityError(f"capture {case.case_id} raw metadata is malformed")
    if not isinstance(preview, dict) or set(preview) != {
        "path",
        "sha256",
        "privacy_status",
    }:
        raise CampaignIntegrityError(f"capture {case.case_id} preview metadata is malformed")
    expected_raw_path = f"private/captures/{_case_stem(case)}/frame.raw"
    expected_preview_path = (
        f"private/captures/{_case_stem(case)}/private-preview.bmp"
    )
    if raw.get("path") != expected_raw_path or preview.get("path") != expected_preview_path:
        raise CampaignIntegrityError(f"capture {case.case_id} contains a foreign path")
    raw_sha = raw.get("sha256")
    preview_sha = preview.get("sha256")
    if not isinstance(raw_sha, str) or not isinstance(preview_sha, str):
        raise CampaignIntegrityError(f"capture {case.case_id} hashes are malformed")
    if (
        not _SHA256_PATTERN.fullmatch(raw_sha)
        or not _SHA256_PATTERN.fullmatch(preview_sha)
        or preview.get("privacy_status") != "private-unreviewed-do-not-commit"
    ):
        raise CampaignIntegrityError(
            f"capture {case.case_id} hash/privacy metadata is malformed"
        )
    raw_payload, _ = _verify_hashed_artifact(session_dir / expected_raw_path, expected=raw_sha)
    preview_payload, _ = _verify_hashed_artifact(
        session_dir / expected_preview_path, expected=preview_sha
    )
    if raw.get("size_bytes") != len(raw_payload):
        raise CampaignIntegrityError(f"capture {case.case_id} raw size was replaced")
    frame = _frame_from_metadata(
        record.get("frame"), raw_payload, label=f"capture {case.case_id} frame"
    )
    source_backend_name = record.get("capture_source_backend_name")
    if not isinstance(source_backend_name, str):
        raise CampaignIntegrityError("capture source backend binding is malformed")
    evidence_origin = record.get("evidence_origin")
    if evidence_origin not in {
        _SOURCE_OWNED_EVIDENCE_ORIGIN,
        _INJECTED_EVIDENCE_ORIGIN,
    }:
        raise CampaignIntegrityError("capture evidence origin is malformed")
    _validated_capture_environment(
        record.get("capture_environment"),
        frame=frame,
        source_backend_name=source_backend_name,
        evidence_origin=evidence_origin,
    )
    if preview_payload != _bmp_payload(frame):
        raise CampaignIntegrityError(f"capture {case.case_id} private preview changed")
    try:
        replay_production: object = _production_json(frame)
    except Exception as exc:
        replay_production = _detector_error(exc)
    if not _production_equivalent(record.get("production"), replay_production):
        raise CampaignIntegrityError(
            f"capture {case.case_id} production output is not a raw-frame replay"
        )
    owned_sha = record.get("owned_frame_sha256")
    if not isinstance(owned_sha, str):
        raise CampaignIntegrityError(f"capture {case.case_id} owned-frame hash is malformed")
    owned_payload, _ = _verify_hashed_artifact(
        _owned_frame_path(session_dir, case), expected=owned_sha
    )
    owned = _strict_json_bytes(owned_payload, label=f"owned frame {case.case_id}")
    _require_exact_keys(
        owned,
        {
            "schema_version",
            "campaign_id",
            "session_id",
            "session_token",
            "ordinal",
            "case_id",
            "captured_at_utc",
            "operator_stage",
            "frame",
            "raw",
            "private_preview",
            "capture_source_backend_name",
            "evidence_origin",
            "capture_environment",
        },
        label=f"owned frame {case.case_id}",
    )
    if (
        owned.get("schema_version") != RESOURCE_RELEASE_CAMPAIGN_SCHEMA_VERSION
        or owned.get("campaign_id") != RESOURCE_RELEASE_CAMPAIGN_ID
        or not isinstance(owned.get("schema_version"), int)
        or isinstance(owned.get("schema_version"), bool)
        or not isinstance(owned.get("ordinal"), int)
        or isinstance(owned.get("ordinal"), bool)
    ):
        raise CampaignIntegrityError(f"owned frame {case.case_id} identity changed")
    for key in (
        "session_id",
        "session_token",
        "ordinal",
        "case_id",
        "captured_at_utc",
        "operator_stage",
        "frame",
        "raw",
        "private_preview",
        "capture_source_backend_name",
        "evidence_origin",
        "capture_environment",
    ):
        if owned.get(key) != record.get(key):
            raise CampaignIntegrityError(
                f"owned frame and report disagree for {case.case_id}: {key}"
            )
    return record, digest


def _captured_prefix(
    session_dir: Path, session: Mapping[str, object]
) -> list[tuple[CampaignCase, dict[str, object], str]]:
    captures_root = Path(session_dir) / "private" / "captures"
    _require_real_subdirectory(session_dir, captures_root, label="private capture root")
    known_names = {_case_stem(case) for case in CAMPAIGN_PLAN}
    if captures_root.exists():
        extras = [path.name for path in captures_root.iterdir() if path.name not in known_names]
        if extras:
            raise CampaignIntegrityError(
                f"foreign or duplicate capture directories present: {sorted(extras)}"
            )
    captured: list[tuple[CampaignCase, dict[str, object], str]] = []
    missing_seen = False
    previous_time: datetime | None = None
    for case in CAMPAIGN_PLAN:
        case_dir = _case_dir(session_dir, case)
        if not case_dir.exists():
            missing_seen = True
            continue
        _require_real_subdirectory(
            session_dir, case_dir, label=f"private capture case {case.case_id}"
        )
        if missing_seen:
            raise CampaignIntegrityError("capture evidence is out of fixed campaign order")
        if not _capture_record_path(session_dir, case).exists():
            if _capture_reservation_path(session_dir, case).exists():
                raise CampaignIntegrityError(
                    f"capture {case.case_id} is already reserved by another invocation; "
                    "zero additional pixels were captured"
                )
            failures = _load_capture_failures(session_dir, session, case)
            allowed_failure_files = {
                item.name
                for path in _capture_failure_paths(session_dir, case)
                for item in (path, _artifact_sidecar(path))
            }
            foreign = sorted(
                path.name for path in case_dir.iterdir() if path.name not in allowed_failure_files
            )
            if any(item["terminal_for_session"] is True for item in failures) or foreign:
                raise CampaignIntegrityError(
                    f"terminal capture failure is preserved for {case.case_id}; "
                    "start a new uniquely owned session rather than retrying"
                )
            if failures:
                # A later explicit capture-next invocation may retry only a
                # no-frame acquisition. Each failed invocation remains hashed;
                # no production frame or detector evaluation is repeated.
                missing_seen = True
                continue
            raise CampaignIntegrityError(
                f"partial capture evidence is preserved for {case.case_id}; refusing recapture"
            )
        failures = _load_capture_failures(session_dir, session, case)
        if any(item["terminal_for_session"] is True for item in failures):
            raise CampaignIntegrityError(
                f"terminal post-capture failure is preserved for {case.case_id}"
            )
        record, digest = _load_record(session_dir, session, case)
        allowed_case_files = {
            "frame.raw",
            "frame.raw.sha256",
            "private-preview.bmp",
            "private-preview.bmp.sha256",
            "owned-frame.json",
            "owned-frame.json.sha256",
            "capture-report.json",
            "capture-report.json.sha256",
            *(
                item.name
                for path in _capture_failure_paths(session_dir, case)
                for item in (path, _artifact_sidecar(path))
            ),
        }
        actual_case_files = {path.name for path in case_dir.iterdir()}
        if actual_case_files != allowed_case_files:
            raise CampaignIntegrityError(
                f"completed capture {case.case_id} contains foreign/partial artifacts"
            )
        captured_time = _parse_utc(record["captured_at_utc"], "captured_at_utc")
        if previous_time is not None and captured_time < previous_time:
            raise CampaignIntegrityError("capture UTC chronology is stale or reordered")
        previous_time = captured_time
        captured.append((case, record, digest))
    return captured


def load_campaign_status(
    session_dir: Path, *, repository: RepositoryProvenance | None = None
) -> CampaignStatus:
    """Verify all stored evidence and return the resumable campaign status."""

    session = _load_session(session_dir)
    if repository is not None:
        _verify_repository_binding(session, repository)
    captured = _captured_prefix(Path(session_dir), session)
    next_case = CAMPAIGN_PLAN[len(captured)] if len(captured) < len(CAMPAIGN_PLAN) else None
    no_frame_failures = (
        0
        if next_case is None or not _case_dir(Path(session_dir), next_case).exists()
        else len(_load_capture_failures(Path(session_dir), session, next_case))
    )
    sealed = (Path(session_dir) / "completion-seal.json").exists()
    if sealed:
        _verify_completion_seal(Path(session_dir), session, captured)
    prepared: tuple[str, ...] = ()
    reviewed: tuple[str, ...] = ()
    if (Path(session_dir) / "review").exists():
        if not sealed:
            raise CampaignIntegrityError("review evidence exists before campaign seal")
        prepared, reviewed = _review_inventory(Path(session_dir), session, captured)
    return CampaignStatus(
        session_id=cast(str, session["session_id"]),
        captured_case_ids=tuple(item[0].case_id for item in captured),
        next_case=next_case,
        next_case_no_frame_failures=no_frame_failures,
        sealed=sealed,
        prepared_case_ids=prepared,
        reviewed_case_ids=reviewed,
    )


def _capture_next_with_source(
    session_dir: Path,
    source: CaptureSource,
    *,
    repository: RepositoryProvenance,
    environment_provider: Callable[[], CaptureEnvironment],
    provenance_capability: object,
    expected_case_id: str | None,
    captured_at_utc: datetime | None = None,
) -> dict[str, object]:
    if not isinstance(source, CaptureSource):
        raise TypeError("source must be CaptureSource")
    if provenance_capability is _SOURCE_OWNED_CAPTURE_CAPABILITY:
        evidence_origin = _SOURCE_OWNED_EVIDENCE_ORIGIN
    elif provenance_capability is _INJECTED_CAPTURE_CAPABILITY:
        evidence_origin = _INJECTED_EVIDENCE_ORIGIN
    else:
        raise CampaignIntegrityError(
            "capture provenance requires a source-owned boundary capability"
        )
    session_dir = Path(session_dir)
    session = _load_session(session_dir)
    _verify_repository_binding(session, repository)
    if (session_dir / "completion-seal.json").exists():
        raise CampaignIntegrityError("sealed campaigns cannot capture more evidence")
    captured = _captured_prefix(session_dir, session)
    if len(captured) == len(CAMPAIGN_PLAN):
        raise CampaignError("campaign capture plan is already complete")
    case = CAMPAIGN_PLAN[len(captured)]
    if expected_case_id is not None and case.case_id != expected_case_id:
        raise CampaignIntegrityError(
            "staged-case acknowledgment is stale; zero pixels were captured"
        )
    captured_at = datetime.now(UTC) if captured_at_utc is None else captured_at_utc
    _require_utc(captured_at, "captured_at_utc")
    return _capture_case(
        session_dir,
        session,
        case,
        source,
        environment_provider,
        captured_at,
        evidence_origin,
    )


def capture_next_case(
    session_dir: Path,
    source: CaptureSource,
    *,
    repository: RepositoryProvenance,
    environment_provider: Callable[[], CaptureEnvironment],
    captured_at_utc: datetime | None = None,
) -> dict[str, object]:
    """Exercise one case with an injected source; evidence cannot close blockers.

    The source-owned CLI never uses this seam. It exists so deterministic tests
    can cover the ledger without a Windows display, and is permanently stamped
    ``test-injected-non-release``.
    """

    if not LIVE_RESOURCE_CAMPAIGN_AUTHORIZED:
        raise CampaignError(
            "LIVE RESOURCE CAMPAIGN NOT YET AUTHORIZED; source-owned live gate is false"
        )
    return _capture_next_with_source(
        session_dir,
        source,
        repository=repository,
        environment_provider=environment_provider,
        provenance_capability=_INJECTED_CAPTURE_CAPABILITY,
        expected_case_id=None,
        captured_at_utc=captured_at_utc,
    )


def _windows_capture_environment(
    backend: WindowsCaptureBackend,
) -> CaptureEnvironment:
    selected = backend.selected_window
    dpi: int | None = None
    try:
        dpi = backend.current_dpi
    except (OSError, RuntimeError):
        pass
    return CaptureEnvironment(
        backend_name=backend.name,
        title_match=DEFAULT_TITLE_SUBSTRING,
        window_title=None if selected is None else selected.title,
        window_class=None if selected is None else selected.class_name,
        window_hwnd=None if selected is None else selected.hwnd,
        window_client_width=None if selected is None else selected.client_width,
        window_client_height=None if selected is None else selected.client_height,
        reported_dpi=dpi,
    )


def _capture_next_windows_case(
    session_dir: Path,
    *,
    repository_root: Path,
    repository: RepositoryProvenance,
    expected_case_id: str,
    captured_at_utc: datetime | None = None,
) -> dict[str, object]:
    """Capture one real case through the fixed source-owned Windows boundary."""

    if not LIVE_RESOURCE_CAMPAIGN_AUTHORIZED:
        raise CampaignError(
            "LIVE RESOURCE CAMPAIGN NOT YET AUTHORIZED; no backend was opened"
        )
    repository_root = Path(repository_root).resolve()
    private_root = (
        repository_root / "diagnostics" / "resource-release-campaigns"
    ).resolve()
    resolved_session = Path(session_dir).resolve()
    try:
        resolved_session.relative_to(private_root)
    except ValueError as exc:
        raise CampaignIntegrityError(
            "source-owned live capture requires the Git-ignored private campaign root"
        ) from exc
    ignored = subprocess.run(
        ["git", "check-ignore", "--quiet", str(resolved_session)],
        cwd=repository_root,
        check=False,
    )
    if ignored.returncode != 0:
        raise CampaignIntegrityError(
            "source-owned live campaign path is not protected by Git ignore"
        )
    status = load_campaign_status(resolved_session, repository=repository)
    if status.next_case is None:
        raise CampaignError("campaign has no remaining capture case")
    if status.next_case.case_id != expected_case_id:
        raise CampaignIntegrityError(
            "staged-case acknowledgment is stale; no backend was opened"
        )
    backend = WindowsCaptureBackend(title_substring=DEFAULT_TITLE_SUBSTRING)
    with CaptureSource(backend, retry_attempts=0) as source:
        return _capture_next_with_source(
            resolved_session,
            source,
            repository=repository,
            environment_provider=lambda: _windows_capture_environment(backend),
            provenance_capability=_SOURCE_OWNED_CAPTURE_CAPABILITY,
            expected_case_id=expected_case_id,
            captured_at_utc=captured_at_utc,
        )


def seal_campaign(
    session_dir: Path,
    *,
    repository: RepositoryProvenance,
    sealed_at_utc: datetime | None = None,
) -> dict[str, object]:
    """Seal the exact complete capture set; detector failures remain evidence."""

    session_dir = Path(session_dir)
    session = _load_session(session_dir)
    _verify_repository_binding(session, repository)
    captured = _captured_prefix(session_dir, session)
    if len(captured) != len(CAMPAIGN_PLAN):
        raise CampaignError(
            f"cannot seal incomplete campaign: {len(captured)}/{len(CAMPAIGN_PLAN)}"
        )
    seal_path = session_dir / "completion-seal.json"
    if seal_path.exists():
        return _verify_completion_seal(session_dir, session, captured)
    sealed_at = datetime.now(UTC) if sealed_at_utc is None else sealed_at_utc
    _require_utc(sealed_at, "sealed_at_utc")
    latest_capture = _parse_utc(captured[-1][1]["captured_at_utc"], "captured_at_utc")
    if sealed_at < latest_capture:
        raise CampaignError("completion seal cannot predate the final capture")
    session_sha = _verify_hashed_artifact(_session_path(session_dir))[1]
    seal: dict[str, object] = {
        "schema_version": RESOURCE_RELEASE_CAMPAIGN_SCHEMA_VERSION,
        "campaign_id": RESOURCE_RELEASE_CAMPAIGN_ID,
        "session_id": session["session_id"],
        "session_token": session["session_token"],
        "sealed_at_utc": _utc_text(sealed_at),
        "session_sha256": session_sha,
        "ordered_capture_reports": [
            {
                "ordinal": case.ordinal,
                "case_id": case.case_id,
                "capture_report_sha256": digest,
                "raw_sha256": cast(dict[str, object], record["raw"])["sha256"],
                "evidence_origin": record["evidence_origin"],
            }
            for case, record, digest in captured
        ],
        "capture_count": len(captured),
        "automatic_retries": 0,
        "operator_labels_are_reviewer_truth": False,
        "activation_allowed": False,
    }
    _write_hashed_artifact(seal_path, _canonical_json_bytes(seal))
    return seal


def _verify_completion_seal(
    session_dir: Path,
    session: Mapping[str, object],
    captured: Sequence[tuple[CampaignCase, dict[str, object], str]],
) -> dict[str, object]:
    if len(captured) != len(CAMPAIGN_PLAN):
        raise CampaignIntegrityError("completion seal cannot bind an incomplete capture prefix")
    payload, _ = _verify_hashed_artifact(Path(session_dir) / "completion-seal.json")
    seal = _strict_json_bytes(payload, label="completion seal")
    if set(seal) != {
        "schema_version",
        "campaign_id",
        "session_id",
        "session_token",
        "sealed_at_utc",
        "session_sha256",
        "ordered_capture_reports",
        "capture_count",
        "automatic_retries",
        "operator_labels_are_reviewer_truth",
        "activation_allowed",
    }:
        raise CampaignIntegrityError("completion seal fields are malformed")
    if any(
        not _is_strict_int(seal.get(field))
        for field in ("schema_version", "capture_count", "automatic_retries")
    ):
        raise CampaignIntegrityError("completion seal integer scalar types changed")
    if (
        seal.get("schema_version") != RESOURCE_RELEASE_CAMPAIGN_SCHEMA_VERSION
        or seal.get("campaign_id") != RESOURCE_RELEASE_CAMPAIGN_ID
    ):
        raise CampaignIntegrityError("completion seal campaign identity changed")
    if seal.get("session_id") != session.get("session_id") or seal.get(
        "session_token"
    ) != session.get("session_token"):
        raise CampaignIntegrityError("completion seal belongs to a foreign session")
    expected_reports = [
        {
            "ordinal": case.ordinal,
            "case_id": case.case_id,
            "capture_report_sha256": digest,
            "raw_sha256": cast(dict[str, object], record["raw"])["sha256"],
            "evidence_origin": record["evidence_origin"],
        }
        for case, record, digest in captured
    ]
    if seal.get("ordered_capture_reports") != expected_reports:
        raise CampaignIntegrityError("completion seal no longer matches capture evidence")
    if seal.get("capture_count") != len(CAMPAIGN_PLAN):
        raise CampaignIntegrityError("completion seal does not cover the fixed plan")
    if (
        seal.get("automatic_retries") != 0
        or seal.get("operator_labels_are_reviewer_truth") is not False
        or seal.get("activation_allowed") is not False
    ):
        raise CampaignIntegrityError("completion seal weakened passive campaign policy")
    if seal.get("session_sha256") != _verify_hashed_artifact(
        _session_path(session_dir)
    )[1]:
        raise CampaignIntegrityError("completion seal no longer matches the session")
    sealed_at = _parse_utc(seal.get("sealed_at_utc"), "sealed_at_utc")
    latest_capture = _parse_utc(captured[-1][1]["captured_at_utc"], "captured_at_utc")
    if sealed_at < latest_capture:
        raise CampaignIntegrityError("completion seal predates final capture")
    return seal


def _review_dir(session_dir: Path, case: CampaignCase) -> Path:
    return Path(session_dir) / "review" / _case_stem(case)


def _review_truth_path(session_dir: Path, case: CampaignCase) -> Path:
    return _review_dir(session_dir, case) / "reviewer-truth.json"


def _review_preparation_path(session_dir: Path, case: CampaignCase) -> Path:
    return _review_dir(session_dir, case) / "review-preparation.json"


def _frame_from_record(session_dir: Path, record: Mapping[str, object]) -> Frame:
    frame_raw = record.get("frame")
    raw_raw = record.get("raw")
    if not isinstance(raw_raw, dict) or set(raw_raw) != {
        "path",
        "sha256",
        "size_bytes",
    }:
        raise CampaignIntegrityError("capture raw metadata is malformed")
    raw_path = raw_raw.get("path")
    raw_sha = raw_raw.get("sha256")
    if not isinstance(raw_path, str) or not isinstance(raw_sha, str):
        raise CampaignIntegrityError("capture raw path/hash is malformed")
    payload, _ = _verify_hashed_artifact(Path(session_dir) / raw_path, expected=raw_sha)
    return _frame_from_metadata(frame_raw, payload, label="capture frame")


def _sanitize_bgra_for_review(frame: Frame) -> bytes:
    profile = load_varrock_east_iron_profile()
    if (
        frame.width != profile.frame_width
        or frame.height != profile.frame_height
        or frame.pixel_format is not PixelFormat.BGRA8888
    ):
        raise CampaignIntegrityError(
            "privacy-safe review requires exact 1005x1078 BGRA production geometry"
        )
    payload = bytearray(frame.payload)
    black = b"\x00\x00\x00\xff"
    for x, y, width, height in VARROCK_EAST_IRON_FIXED_UI_REGIONS:
        for row in range(y, y + height):
            start = (row * frame.width + x) * 4
            payload[start : start + width * 4] = black * width
    return bytes(payload)


def _has_reviewable_geometry(frame: Frame) -> bool:
    profile = load_varrock_east_iron_profile()
    return (
        frame.width == profile.frame_width
        and frame.height == profile.frame_height
        and frame.pixel_format is PixelFormat.BGRA8888
    )


def _review_decision_json(decision: ReviewDecision) -> dict[str, object]:
    return {
        "case_id": decision.case_id,
        "reviewer_id": decision.reviewer_id,
        "reviewed_at_utc": _utc_text(decision.reviewed_at_utc),
        "meaning": decision.meaning.value,
        "resource_truth": [
            {"resource_id": resource_id, "state": state.value}
            for resource_id, state in decision.resource_truth
        ],
        "review_artifact_sha256": decision.review_artifact_sha256,
        "privacy_review_confirmed": decision.privacy_review_confirmed,
        "focal_resource_id": decision.focal_resource_id,
        "node_phase": None if decision.node_phase is None else decision.node_phase.value,
        "obstruction_target": (
            None
            if decision.obstruction_target_kind is None
            else {
                "kind": decision.obstruction_target_kind,
                "target_id": decision.obstruction_target_id,
            }
        ),
        "subject_region": (
            None if decision.subject_region is None else list(decision.subject_region)
        ),
        "notes": decision.notes,
    }


def review_decision_from_json(value: Mapping[str, object]) -> ReviewDecision:
    """Parse a strict reviewer-authored decision without consulting operator labels."""

    required = {
        "case_id",
        "reviewer_id",
        "reviewed_at_utc",
        "meaning",
        "resource_truth",
        "review_artifact_sha256",
        "privacy_review_confirmed",
        "focal_resource_id",
        "node_phase",
        "obstruction_target",
        "subject_region",
        "notes",
    }
    if set(value) != required:
        raise CampaignIntegrityError("review decision fields are malformed")
    resource_truth_raw = value.get("resource_truth")
    if not isinstance(resource_truth_raw, list):
        raise CampaignIntegrityError("review resource_truth must be a list")
    resource_truth: list[tuple[str, ResourceVisualState]] = []
    for item in resource_truth_raw:
        if not isinstance(item, dict) or set(item) != {"resource_id", "state"}:
            raise CampaignIntegrityError("review resource truth item is malformed")
        resource_id = item.get("resource_id")
        state = item.get("state")
        if not isinstance(resource_id, str) or not isinstance(state, str):
            raise CampaignIntegrityError("review resource truth values are malformed")
        try:
            resource_truth.append((resource_id, ResourceVisualState(state)))
        except ValueError as exc:
            raise CampaignIntegrityError("review resource truth state is invalid") from exc
    obstruction = value.get("obstruction_target")
    obstruction_kind: str | None = None
    obstruction_id: str | None = None
    if obstruction is not None:
        if not isinstance(obstruction, dict) or set(obstruction) != {"kind", "target_id"}:
            raise CampaignIntegrityError("review obstruction target is malformed")
        obstruction_kind = obstruction.get("kind")
        obstruction_id = obstruction.get("target_id")
        if not isinstance(obstruction_kind, str) or not isinstance(obstruction_id, str):
            raise CampaignIntegrityError("review obstruction target values are malformed")
    subject_raw = value.get("subject_region")
    subject_region: tuple[int, int, int, int] | None = None
    if subject_raw is not None:
        if (
            not isinstance(subject_raw, list)
            or len(subject_raw) != 4
            or any(not isinstance(item, int) or isinstance(item, bool) for item in subject_raw)
        ):
            raise CampaignIntegrityError("review subject region is malformed")
        subject_region = (
            subject_raw[0],
            subject_raw[1],
            subject_raw[2],
            subject_raw[3],
        )
    case_id = value.get("case_id")
    reviewer_id = value.get("reviewer_id")
    meaning_raw = value.get("meaning")
    privacy_confirmed = value.get("privacy_review_confirmed")
    review_artifact_sha256 = value.get("review_artifact_sha256")
    notes = value.get("notes")
    focal_resource_id = value.get("focal_resource_id")
    node_phase_raw = value.get("node_phase")
    if (
        not isinstance(case_id, str)
        or not isinstance(reviewer_id, str)
        or not isinstance(meaning_raw, str)
        or not isinstance(review_artifact_sha256, str)
        or not isinstance(privacy_confirmed, bool)
        or not isinstance(notes, str)
        or (focal_resource_id is not None and not isinstance(focal_resource_id, str))
        or (node_phase_raw is not None and not isinstance(node_phase_raw, str))
    ):
        raise CampaignIntegrityError("review decision scalar values are malformed")
    try:
        meaning = ReviewMeaning(meaning_raw)
        node_phase = None if node_phase_raw is None else NodeCyclePhase(node_phase_raw)
        return ReviewDecision(
            case_id=case_id,
            reviewer_id=reviewer_id,
            reviewed_at_utc=_parse_utc(value.get("reviewed_at_utc"), "reviewed_at_utc"),
            meaning=meaning,
            resource_truth=tuple(resource_truth),
            review_artifact_sha256=review_artifact_sha256,
            privacy_review_confirmed=privacy_confirmed,
            focal_resource_id=focal_resource_id,
            node_phase=node_phase,
            obstruction_target_kind=obstruction_kind,
            obstruction_target_id=obstruction_id,
            subject_region=subject_region,
            notes=notes,
        )
    except (TypeError, ValueError) as exc:
        raise CampaignIntegrityError(f"review decision is invalid: {exc}") from exc


def load_review_decision(path: Path) -> ReviewDecision:
    """Load one strict reviewer-authored decision file."""

    try:
        payload = Path(path).read_bytes()
    except OSError as exc:
        raise CampaignError(f"could not read review decision {path}: {exc}") from exc
    return review_decision_from_json(
        _strict_json_bytes(payload, label=f"review decision {path}")
    )


def _validate_review_decision(
    decision: ReviewDecision,
    *,
    case: CampaignCase,
    operator_id: str,
    sealed_at: datetime,
    pixels_withheld: bool,
) -> None:
    if decision.case_id != case.case_id:
        raise CampaignError("review decision does not match the selected case")
    if decision.reviewer_id == operator_id:
        raise CampaignError("reviewer must be independent from the campaign operator")
    if decision.reviewed_at_utc < sealed_at:
        raise CampaignError("review truth cannot predate campaign finalization")
    if not decision.privacy_review_confirmed:
        raise CampaignError("explicit privacy review confirmation is required")
    if pixels_withheld:
        if (
            decision.meaning is not ReviewMeaning.UNREVIEWABLE_PIXELS_WITHHELD
            or any(
                state is not ResourceVisualState.UNCERTAIN
                for _, state in decision.resource_truth
            )
            or decision.focal_resource_id is not None
            or decision.node_phase is not None
            or decision.obstruction_target_kind is not None
            or decision.subject_region is not None
        ):
            raise CampaignError(
                "withheld pixels require an explicit unreviewable disposition and all-UNCERTAIN truth"
            )
    elif decision.meaning is ReviewMeaning.UNREVIEWABLE_PIXELS_WITHHELD:
        raise CampaignError(
            "reviewable pixels cannot use the unreviewable-withheld disposition"
        )
    if decision.meaning is ReviewMeaning.PROFILED_NODE_STATE:
        if decision.focal_resource_id is None or decision.node_phase is None:
            raise CampaignError(
                "node-cycle truth requires explicit focal resource and phase"
            )
    elif decision.focal_resource_id is not None or decision.node_phase is not None:
        raise CampaignError("only node-cycle truth may contain focal resource/phase")
    if decision.meaning is ReviewMeaning.PROFILED_OBSTRUCTION:
        if decision.obstruction_target_kind is None:
            raise CampaignError("obstruction truth requires an explicit target")
        profile = load_varrock_east_iron_profile()
        if decision.obstruction_target_kind == "resource":
            if decision.obstruction_target_id not in VARROCK_EAST_IRON_RESOURCE_IDS:
                raise CampaignError("obstruction resource is not a packaged candidate")
            states = dict(decision.resource_truth)
            if states[decision.obstruction_target_id] is not (
                ResourceVisualState.UNCERTAIN
            ):
                raise CampaignError(
                    "an obstructed candidate must have explicit UNCERTAIN reviewer truth"
                )
        else:
            landmark_ids = {item.landmark_id for item in profile.scene_landmarks}
            if decision.obstruction_target_id not in landmark_ids:
                raise CampaignError("obstruction landmark is not a packaged world landmark")
    elif decision.obstruction_target_kind is not None:
        raise CampaignError("only obstruction truth may contain an obstruction target")
    negative_subject_meanings = {
        ReviewMeaning.NEIGHBORING_COPPER,
        ReviewMeaning.NEIGHBORING_TIN,
        ReviewMeaning.TERRAIN_CLUTTER,
    }
    if decision.meaning in negative_subject_meanings:
        if decision.subject_region is None:
            raise CampaignError(
                "copper, tin, and terrain-clutter truth require an explicit reviewed subject region"
            )
        profile = load_varrock_east_iron_profile()
        if any(
            _regions_overlap(decision.subject_region, fixed_ui)
            for fixed_ui in VARROCK_EAST_IRON_FIXED_UI_REGIONS
        ):
            raise CampaignError(
                "negative subject region must be wholly outside fixed UI/privacy masks"
            )
        if any(
            _regions_overlap(decision.subject_region, candidate.region)
            for candidate in profile.candidates
        ):
            raise CampaignError(
                "negative subject region must be distinct from profiled iron candidates"
            )
    elif decision.subject_region is not None:
        raise CampaignError(
            "only copper, tin, and terrain-clutter truth may contain a subject region"
        )


def _production_equivalent(first: object, second: object) -> bool:
    return _canonical_json_bytes({"production": first}) == _canonical_json_bytes(
        {"production": second}
    )


def _production_authority_projection(value: object) -> object:
    """Project production output to fields that can grant/deny resource authority."""

    if not isinstance(value, dict):
        return value
    trust = value.get("trust")
    scene = value.get("scene")
    projected_trust: object = trust
    if isinstance(trust, dict):
        resources = trust.get("resources")
        projected_resources: object = resources
        if isinstance(resources, list):
            projected_resources = [
                {
                    "resource_id": item.get("resource_id"),
                    "resource_type": item.get("resource_type"),
                    "available": item.get("available"),
                    "interaction_region": item.get("interaction_region"),
                }
                if isinstance(item, dict)
                else item
                for item in resources
            ]
        projected_trust = {
            "accepted": trust.get("accepted"),
            "reason": trust.get("reason"),
            "frame": trust.get("frame"),
            "resources": projected_resources,
            "definitive_target_ids": trust.get("definitive_target_ids"),
            "production_actionable_target_ids": trust.get(
                "production_actionable_target_ids"
            ),
            "production_interaction_regions": trust.get(
                "production_interaction_regions"
            ),
        }
    projected_scene: object = scene
    if isinstance(scene, dict):
        projected_scene = {
            "validated": scene.get("validated"),
            "matched_count": scene.get("matched_count"),
            "required_quorum": scene.get("required_quorum"),
            "matched_zones": scene.get("matched_zones"),
            "required_zones": scene.get("required_zones"),
        }
    return {
        "status": value.get("status"),
        "detector_id": value.get("detector_id"),
        "detector_version": value.get("detector_version"),
        "trust": projected_trust,
        "scene": projected_scene,
        "passive_campaign_authorized_target_ids": value.get(
            "passive_campaign_authorized_target_ids"
        ),
        "stop_required": value.get("stop_required"),
        "input_authority": value.get("input_authority"),
    }


def _production_authority_equivalent(first: object, second: object) -> bool:
    return _canonical_json_bytes(
        {"production_authority": _production_authority_projection(first)}
    ) == _canonical_json_bytes(
        {"production_authority": _production_authority_projection(second)}
    )


def _detector_error(exc: Exception) -> dict[str, object]:
    return {
        "status": "detector-error",
        "detector_id": VARROCK_EAST_IRON_DETECTOR_ID,
        "detector_version": VARROCK_EAST_IRON_DETECTOR_VERSION,
        "error_type": type(exc).__name__,
        "error": str(exc),
        "observations": [],
        "trust": {
            "accepted": False,
            "reason": "detector_evaluation_failed",
            "frame": None,
            "resources": [],
            "definitive_target_ids": [],
            "production_actionable_target_ids": [],
            "production_interaction_regions": {},
        },
        "scene": {
            "validated": False,
            "reason": "detector_evaluation_failed",
            "matched_count": 0,
            "required_quorum": 5,
            "matched_zones": [],
            "required_zones": 3,
            "landmarks": [],
            "authority": "read-only-summary-never-overrides-production",
        },
        "passive_campaign_authorized_target_ids": [],
        "stop_required": True,
        "input_authority": False,
    }


def _bounded_gzip_decompress(payload: bytes, *, expected_size: int, label: str) -> bytes:
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(payload), mode="rb") as stream:
            result = stream.read(expected_size + 1)
    except (OSError, EOFError) as exc:
        raise CampaignIntegrityError(f"{label} gzip is corrupt") from exc
    if len(result) != expected_size:
        raise CampaignIntegrityError(
            f"{label} decompressed size is {len(result)}, expected {expected_size}"
        )
    return result


def _deterministic_gzip(payload: bytes) -> bytes:
    output = io.BytesIO()
    with gzip.GzipFile(
        fileobj=output,
        mode="wb",
        filename="",
        compresslevel=9,
        mtime=0,
    ) as stream:
        stream.write(payload)
    return output.getvalue()


def prepare_case_review(
    session_dir: Path,
    case_id: str,
    *,
    repository: RepositoryProvenance,
    prepared_at_utc: datetime | None = None,
) -> dict[str, object]:
    """Create deterministic sanitized artifacts before reviewer truth is authored."""

    session_dir = Path(session_dir)
    session = _load_session(session_dir)
    _verify_repository_binding(session, repository)
    captured = _captured_prefix(session_dir, session)
    if len(captured) != len(CAMPAIGN_PLAN):
        raise CampaignError("review preparation requires the full sealed campaign")
    seal = _verify_completion_seal(session_dir, session, captured)
    _review_inventory(session_dir, session, captured)
    try:
        case = next(item for item in CAMPAIGN_PLAN if item.case_id == case_id)
    except StopIteration as exc:
        raise CampaignError("review preparation contains a foreign case") from exc
    prepared_at = datetime.now(UTC) if prepared_at_utc is None else prepared_at_utc
    _require_utc(prepared_at, "prepared_at_utc")
    sealed_at = _parse_utc(seal.get("sealed_at_utc"), "sealed_at_utc")
    if prepared_at < sealed_at:
        raise CampaignError("review preparation cannot predate campaign finalization")
    review_dir = _review_dir(session_dir, case)
    if review_dir.exists():
        raise CampaignIntegrityError(
            f"review preparation already exists for {case.case_id}; refusing overwrite"
        )
    review_dir.mkdir(parents=True, exist_ok=False)
    record, capture_report_sha = _load_record(session_dir, session, case)
    frame = _frame_from_record(session_dir, record)
    reviewable_geometry = _has_reviewable_geometry(frame)
    sanitized_payload = _sanitize_bgra_for_review(frame) if reviewable_geometry else None
    sanitized_frame = (
        None
        if sanitized_payload is None
        else Frame.from_raw(
            RawFrame(
                payload=sanitized_payload,
                width=frame.width,
                height=frame.height,
                pixel_format=frame.pixel_format,
            ),
            frame_id=frame.frame_id,
            captured_monotonic_s=frame.captured_monotonic_s,
        )
    )
    created: list[Path] = []
    try:
        gzip_meta: dict[str, object] | None = None
        preview_meta: dict[str, object] | None = None
        if sanitized_payload is not None and sanitized_frame is not None:
            gzip_path = review_dir / "sanitized-frame.raw.gz"
            gzip_sha = _write_hashed_artifact(
                gzip_path, _deterministic_gzip(sanitized_payload)
            )
            created.extend((gzip_path, _artifact_sidecar(gzip_path)))
            preview_path = review_dir / "sanitized-preview.bmp"
            preview_sha = _write_hashed_artifact(
                preview_path, _bmp_payload(sanitized_frame)
            )
            created.extend((preview_path, _artifact_sidecar(preview_path)))
            gzip_meta = {
                "path": f"review/{_case_stem(case)}/sanitized-frame.raw.gz",
                "file_sha256": gzip_sha,
                "decompressed_sha256": _sha256(sanitized_payload),
            }
            preview_meta = {
                "path": f"review/{_case_stem(case)}/sanitized-preview.bmp",
                "sha256": preview_sha,
            }
        try:
            replay_production: object = _production_json(frame)
        except Exception as exc:
            replay_production = _detector_error(exc)
        if sanitized_frame is None:
            sanitized_production: object = None
        else:
            try:
                sanitized_production = _production_json(sanitized_frame)
            except Exception as exc:
                sanitized_production = _detector_error(exc)
        raw_meta = cast(dict[str, object], record["raw"])
        preparation: dict[str, object] = {
            "schema_version": RESOURCE_RELEASE_CAMPAIGN_SCHEMA_VERSION,
            "campaign_id": RESOURCE_RELEASE_CAMPAIGN_ID,
            "campaign_version": RESOURCE_RELEASE_CAMPAIGN_VERSION,
            "session_id": session["session_id"],
            "session_token": session["session_token"],
            "ordinal": case.ordinal,
            "case_id": case.case_id,
            "blocker_id": case.blocker_id,
            "prepared_at_utc": _utc_text(prepared_at),
            "operator_stage": _case_json(case),
            "operator_stage_is_reviewer_truth": False,
            "source_bindings": {
                "completion_seal_sha256": _verify_hashed_artifact(
                    session_dir / "completion-seal.json"
                )[1],
                "capture_report_sha256": capture_report_sha,
                "private_raw_sha256": raw_meta["sha256"],
                "evidence_origin": record["evidence_origin"],
            },
            "privacy_artifacts": {
                "mode": (
                    "sanitized-frame"
                    if reviewable_geometry
                    else "pixels-withheld-unsupported-geometry"
                ),
                "sanitized_raw_gzip": gzip_meta,
                "sanitized_preview": preview_meta,
                "mask": (
                    RESOURCE_RELEASE_PRIVACY_MASK_ID if reviewable_geometry else None
                ),
                "full_geometry_preserved": reviewable_geometry,
            },
            "replay_check": {
                "capture_report_matches_replay": _production_equivalent(
                    record["production"], replay_production
                ),
                "sanitization_preserves_authoritative_production": (
                    sanitized_production is not None
                    and _production_authority_equivalent(
                        replay_production, sanitized_production
                    )
                ),
                "sanitization_production_exact": (
                    sanitized_production is not None
                    and _production_equivalent(replay_production, sanitized_production)
                ),
                "replay_production": replay_production,
                "sanitized_production": sanitized_production,
            },
            "privacy_reviewed": False,
            "activation_allowed": False,
            "promotion_allowed": False,
            "input_authority": False,
        }
        preparation_path = _review_preparation_path(session_dir, case)
        preparation_sha = _write_hashed_artifact(
            preparation_path, _canonical_json_bytes(preparation)
        )
        created.extend((preparation_path, _artifact_sidecar(preparation_path)))
    except Exception:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        if review_dir.exists() and not any(review_dir.iterdir()):
            review_dir.rmdir()
        raise
    return {
        "case_id": case.case_id,
        "review_artifact_sha256": preparation_sha,
        "privacy_artifacts": preparation["privacy_artifacts"],
        "privacy_reviewed": False,
        "activation_allowed": False,
    }


def _load_review_preparation(
    session_dir: Path,
    session: Mapping[str, object],
    case: CampaignCase,
    record: Mapping[str, object],
    capture_report_sha: str,
) -> tuple[dict[str, object], str]:
    payload, preparation_sha = _verify_hashed_artifact(
        _review_preparation_path(session_dir, case)
    )
    preparation = _strict_json_bytes(
        payload, label=f"review preparation {case.case_id}"
    )
    required = {
        "schema_version",
        "campaign_id",
        "campaign_version",
        "session_id",
        "session_token",
        "ordinal",
        "case_id",
        "blocker_id",
        "prepared_at_utc",
        "operator_stage",
        "operator_stage_is_reviewer_truth",
        "source_bindings",
        "privacy_artifacts",
        "replay_check",
        "privacy_reviewed",
        "activation_allowed",
        "promotion_allowed",
        "input_authority",
    }
    if set(preparation) != required:
        raise CampaignIntegrityError(f"review preparation {case.case_id} fields changed")
    if any(
        not _is_strict_int(preparation.get(field))
        for field in ("schema_version", "ordinal")
    ):
        raise CampaignIntegrityError("review preparation integer scalar types changed")
    if (
        preparation.get("schema_version") != RESOURCE_RELEASE_CAMPAIGN_SCHEMA_VERSION
        or preparation.get("campaign_id") != RESOURCE_RELEASE_CAMPAIGN_ID
        or preparation.get("campaign_version") != RESOURCE_RELEASE_CAMPAIGN_VERSION
        or preparation.get("session_id") != session.get("session_id")
        or preparation.get("session_token") != session.get("session_token")
        or preparation.get("ordinal") != case.ordinal
        or preparation.get("case_id") != case.case_id
        or preparation.get("blocker_id") != case.blocker_id
        or preparation.get("operator_stage") != _case_json(case)
        or preparation.get("operator_stage_is_reviewer_truth") is not False
    ):
        raise CampaignIntegrityError(f"review preparation {case.case_id} identity changed")
    if (
        preparation.get("privacy_reviewed") is not False
        or preparation.get("activation_allowed") is not False
        or preparation.get("promotion_allowed") is not False
        or preparation.get("input_authority") is not False
    ):
        raise CampaignIntegrityError("review preparation weakened deny-only policy")
    _parse_utc(preparation.get("prepared_at_utc"), "prepared_at_utc")
    bindings = preparation.get("source_bindings")
    raw_meta = record.get("raw")
    if not isinstance(bindings, dict) or set(bindings) != {
        "completion_seal_sha256",
        "capture_report_sha256",
        "private_raw_sha256",
        "evidence_origin",
    }:
        raise CampaignIntegrityError(f"review preparation {case.case_id} bindings malformed")
    if not isinstance(raw_meta, dict):
        raise CampaignIntegrityError(f"capture {case.case_id} raw metadata is malformed")
    if (
        bindings.get("capture_report_sha256") != capture_report_sha
        or bindings.get("private_raw_sha256") != raw_meta.get("sha256")
        or bindings.get("evidence_origin") != record.get("evidence_origin")
        or bindings.get("completion_seal_sha256")
        != _verify_hashed_artifact(session_dir / "completion-seal.json")[1]
    ):
        raise CampaignIntegrityError(
            f"review preparation {case.case_id} was rebound to replaced evidence"
        )
    artifacts = preparation.get("privacy_artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != {
        "mode",
        "sanitized_raw_gzip",
        "sanitized_preview",
        "mask",
        "full_geometry_preserved",
    }:
        raise CampaignIntegrityError(f"review preparation {case.case_id} artifacts malformed")
    mode = artifacts.get("mode")
    gzip_meta = artifacts.get("sanitized_raw_gzip")
    preview_meta = artifacts.get("sanitized_preview")
    frame = _frame_from_record(session_dir, record)
    sanitized_frame: Frame | None = None
    if mode == "sanitized-frame":
        if (
            artifacts.get("mask") != RESOURCE_RELEASE_PRIVACY_MASK_ID
            or artifacts.get("full_geometry_preserved") is not True
            or not _has_reviewable_geometry(frame)
        ):
            raise CampaignIntegrityError("review privacy mask identity changed")
        if not isinstance(gzip_meta, dict) or set(gzip_meta) != {
            "path",
            "file_sha256",
            "decompressed_sha256",
        }:
            raise CampaignIntegrityError("review sanitized gzip metadata is malformed")
        if not isinstance(preview_meta, dict) or set(preview_meta) != {"path", "sha256"}:
            raise CampaignIntegrityError("review sanitized preview metadata is malformed")
        expected_gzip_path = f"review/{_case_stem(case)}/sanitized-frame.raw.gz"
        expected_preview_path = f"review/{_case_stem(case)}/sanitized-preview.bmp"
        if (
            gzip_meta.get("path") != expected_gzip_path
            or preview_meta.get("path") != expected_preview_path
        ):
            raise CampaignIntegrityError(f"review {case.case_id} contains a foreign path")
        gzip_sha = gzip_meta.get("file_sha256")
        preview_sha = preview_meta.get("sha256")
        if not isinstance(gzip_sha, str) or not isinstance(preview_sha, str):
            raise CampaignIntegrityError("review artifact hashes are malformed")
        compressed, _ = _verify_hashed_artifact(
            session_dir / expected_gzip_path, expected=gzip_sha
        )
        preview_payload, _ = _verify_hashed_artifact(
            session_dir / expected_preview_path, expected=preview_sha
        )
        expected_size = frame.width * frame.height * frame.pixel_format.bytes_per_pixel
        sanitized = _bounded_gzip_decompress(
            compressed,
            expected_size=expected_size,
            label=f"review {case.case_id}",
        )
        expected_sanitized = _sanitize_bgra_for_review(frame)
        if (
            sanitized != expected_sanitized
            or gzip_meta.get("decompressed_sha256") != _sha256(expected_sanitized)
        ):
            raise CampaignIntegrityError(f"review {case.case_id} sanitization was replaced")
        sanitized_frame = Frame.from_raw(
            RawFrame(
                payload=sanitized,
                width=frame.width,
                height=frame.height,
                pixel_format=frame.pixel_format,
            ),
            frame_id=frame.frame_id,
            captured_monotonic_s=frame.captured_monotonic_s,
        )
        if preview_payload != _bmp_payload(sanitized_frame):
            raise CampaignIntegrityError("review sanitized preview was replaced")
    elif mode == "pixels-withheld-unsupported-geometry":
        if (
            _has_reviewable_geometry(frame)
            or gzip_meta is not None
            or preview_meta is not None
            or artifacts.get("mask") is not None
            or artifacts.get("full_geometry_preserved") is not False
        ):
            raise CampaignIntegrityError("withheld-pixel review artifact is malformed")
    else:
        raise CampaignIntegrityError("review privacy artifact mode is unknown")
    try:
        replay_production: object = _production_json(frame)
    except Exception as exc:
        replay_production = _detector_error(exc)
    if sanitized_frame is None:
        sanitized_production: object = None
    else:
        try:
            sanitized_production = _production_json(sanitized_frame)
        except Exception as exc:
            sanitized_production = _detector_error(exc)
    expected_replay = {
        "capture_report_matches_replay": _production_equivalent(
            record["production"], replay_production
        ),
        "sanitization_preserves_authoritative_production": (
            sanitized_production is not None
            and _production_authority_equivalent(
                replay_production, sanitized_production
            )
        ),
        "sanitization_production_exact": (
            sanitized_production is not None
            and _production_equivalent(replay_production, sanitized_production)
        ),
        "replay_production": replay_production,
        "sanitized_production": sanitized_production,
    }
    if preparation.get("replay_check") != expected_replay:
        raise CampaignIntegrityError("review replay evidence was replaced")
    return preparation, preparation_sha


def record_case_review(
    session_dir: Path,
    decision: ReviewDecision,
    *,
    repository: RepositoryProvenance,
    recorded_at_utc: datetime | None = None,
) -> dict[str, object]:
    """Bind independent truth to exact artifacts the reviewer already inspected."""

    if not isinstance(decision, ReviewDecision):
        raise TypeError("decision must be ReviewDecision")
    session_dir = Path(session_dir)
    session = _load_session(session_dir)
    _verify_repository_binding(session, repository)
    captured = _captured_prefix(session_dir, session)
    if len(captured) != len(CAMPAIGN_PLAN):
        raise CampaignError("review is unavailable until the full campaign is sealed")
    seal = _verify_completion_seal(session_dir, session, captured)
    _review_inventory(session_dir, session, captured)
    try:
        case = next(item for item in CAMPAIGN_PLAN if item.case_id == decision.case_id)
    except StopIteration as exc:
        raise CampaignError("review decision contains a foreign case") from exc
    record_by_id = {item.case_id: (record, digest) for item, record, digest in captured}
    record, capture_report_sha = record_by_id[case.case_id]
    preparation, preparation_sha = _load_review_preparation(
        session_dir, session, case, record, capture_report_sha
    )
    if decision.review_artifact_sha256 != preparation_sha:
        raise CampaignError("review decision does not bind the inspected artifact manifest")
    sealed_at = _parse_utc(seal.get("sealed_at_utc"), "sealed_at_utc")
    _validate_review_decision(
        decision,
        case=case,
        operator_id=_operator_id(session),
        sealed_at=sealed_at,
        pixels_withheld=cast(dict[str, object], preparation["privacy_artifacts"]).get(
            "mode"
        )
        == "pixels-withheld-unsupported-geometry",
    )
    recorded_at = datetime.now(UTC) if recorded_at_utc is None else recorded_at_utc
    _require_utc(recorded_at, "recorded_at_utc")
    prepared_at = _parse_utc(preparation.get("prepared_at_utc"), "prepared_at_utc")
    if decision.reviewed_at_utc < prepared_at:
        raise CampaignError("reviewer decision cannot predate inspected artifact preparation")
    if recorded_at < decision.reviewed_at_utc:
        raise CampaignError("review record cannot predate reviewer decision")
    truth_path = _review_truth_path(session_dir, case)
    if truth_path.exists() or _artifact_sidecar(truth_path).exists():
        raise CampaignIntegrityError(
            f"review truth already exists for {case.case_id}; refusing overwrite"
        )
    truth_states = dict(decision.resource_truth)
    truth: dict[str, object] = {
        "schema_version": RESOURCE_RELEASE_CAMPAIGN_SCHEMA_VERSION,
        "campaign_id": RESOURCE_RELEASE_CAMPAIGN_ID,
        "campaign_version": RESOURCE_RELEASE_CAMPAIGN_VERSION,
        "session_id": session["session_id"],
        "session_token": session["session_token"],
        "ordinal": case.ordinal,
        "case_id": case.case_id,
        "blocker_id": case.blocker_id,
        "recorded_at_utc": _utc_text(recorded_at),
        "operator_stage": _case_json(case),
        "operator_stage_is_reviewer_truth": False,
        "review_artifact_sha256": preparation_sha,
        "review": _review_decision_json(decision),
        "source_bindings": preparation["source_bindings"],
        "privacy_artifacts": preparation["privacy_artifacts"],
        "truth_definitive_target_ids": [
            resource_id
            for resource_id in VARROCK_EAST_IRON_RESOURCE_IDS
            if truth_states[resource_id] is not ResourceVisualState.UNCERTAIN
        ],
        "truth_actionable_target_ids": [
            resource_id
            for resource_id in VARROCK_EAST_IRON_RESOURCE_IDS
            if truth_states[resource_id] is ResourceVisualState.AVAILABLE
        ],
        "replay_check": preparation["replay_check"],
        "privacy_reviewed": True,
        "activation_allowed": False,
        "promotion_allowed": False,
        "input_authority": False,
    }
    _write_hashed_artifact(truth_path, _canonical_json_bytes(truth))
    return truth


def _load_review(
    session_dir: Path,
    session: Mapping[str, object],
    case: CampaignCase,
    record: Mapping[str, object],
    capture_report_sha: str,
) -> tuple[dict[str, object], str]:
    preparation, preparation_sha = _load_review_preparation(
        session_dir, session, case, record, capture_report_sha
    )
    payload, truth_sha = _verify_hashed_artifact(_review_truth_path(session_dir, case))
    truth = _strict_json_bytes(payload, label=f"review truth {case.case_id}")
    required = {
        "schema_version",
        "campaign_id",
        "campaign_version",
        "session_id",
        "session_token",
        "ordinal",
        "case_id",
        "blocker_id",
        "recorded_at_utc",
        "operator_stage",
        "operator_stage_is_reviewer_truth",
        "review_artifact_sha256",
        "review",
        "source_bindings",
        "privacy_artifacts",
        "truth_definitive_target_ids",
        "truth_actionable_target_ids",
        "replay_check",
        "privacy_reviewed",
        "activation_allowed",
        "promotion_allowed",
        "input_authority",
    }
    if set(truth) != required:
        raise CampaignIntegrityError(f"review truth {case.case_id} fields changed")
    if any(
        not _is_strict_int(truth.get(field))
        for field in ("schema_version", "ordinal")
    ):
        raise CampaignIntegrityError("review truth integer scalar types changed")
    if (
        truth.get("schema_version") != RESOURCE_RELEASE_CAMPAIGN_SCHEMA_VERSION
        or truth.get("campaign_id") != RESOURCE_RELEASE_CAMPAIGN_ID
        or truth.get("campaign_version") != RESOURCE_RELEASE_CAMPAIGN_VERSION
        or truth.get("session_id") != session.get("session_id")
        or truth.get("session_token") != session.get("session_token")
        or truth.get("ordinal") != case.ordinal
        or truth.get("case_id") != case.case_id
        or truth.get("blocker_id") != case.blocker_id
        or truth.get("operator_stage") != _case_json(case)
        or truth.get("operator_stage_is_reviewer_truth") is not False
        or truth.get("review_artifact_sha256") != preparation_sha
    ):
        raise CampaignIntegrityError(f"review truth {case.case_id} identity changed")
    if (
        truth.get("source_bindings") != preparation.get("source_bindings")
        or truth.get("privacy_artifacts") != preparation.get("privacy_artifacts")
        or truth.get("replay_check") != preparation.get("replay_check")
    ):
        raise CampaignIntegrityError(f"review truth {case.case_id} artifact binding changed")
    if (
        truth.get("privacy_reviewed") is not True
        or truth.get("activation_allowed") is not False
        or truth.get("promotion_allowed") is not False
        or truth.get("input_authority") is not False
    ):
        raise CampaignIntegrityError("review truth weakened deny-only policy")
    review = truth.get("review")
    if not isinstance(review, dict):
        raise CampaignIntegrityError(f"review {case.case_id} decision is malformed")
    decision = review_decision_from_json(review)
    if decision.review_artifact_sha256 != preparation_sha:
        raise CampaignIntegrityError("review decision artifact binding changed")
    sealed_at = _parse_utc(
        _verify_completion_seal(
            session_dir, session, _captured_prefix(session_dir, session)
        ).get("sealed_at_utc"),
        "sealed_at_utc",
    )
    _validate_review_decision(
        decision,
        case=case,
        operator_id=_operator_id(session),
        sealed_at=sealed_at,
        pixels_withheld=cast(dict[str, object], preparation["privacy_artifacts"]).get(
            "mode"
        )
        == "pixels-withheld-unsupported-geometry",
    )
    recorded_at = _parse_utc(truth.get("recorded_at_utc"), "recorded_at_utc")
    prepared_at = _parse_utc(preparation.get("prepared_at_utc"), "prepared_at_utc")
    if (
        decision.reviewed_at_utc < prepared_at
        or recorded_at < decision.reviewed_at_utc
    ):
        raise CampaignIntegrityError("review chronology is stale or reordered")
    truth_states = dict(decision.resource_truth)
    expected_definitive = [
        resource_id
        for resource_id in VARROCK_EAST_IRON_RESOURCE_IDS
        if truth_states[resource_id] is not ResourceVisualState.UNCERTAIN
    ]
    expected_actionable = [
        resource_id
        for resource_id in VARROCK_EAST_IRON_RESOURCE_IDS
        if truth_states[resource_id] is ResourceVisualState.AVAILABLE
    ]
    if (
        truth.get("truth_definitive_target_ids") != expected_definitive
        or truth.get("truth_actionable_target_ids") != expected_actionable
        or _review_decision_json(decision) != review
    ):
        raise CampaignIntegrityError("review truth derived fields changed")
    return truth, truth_sha


def _review_inventory(
    session_dir: Path,
    session: Mapping[str, object],
    captured: Sequence[tuple[CampaignCase, dict[str, object], str]],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    review_root = Path(session_dir) / "review"
    if not review_root.exists():
        return (), ()
    _require_real_subdirectory(session_dir, review_root, label="review root")
    known = {_case_stem(case): case for case in CAMPAIGN_PLAN}
    extras = [path.name for path in review_root.iterdir() if path.name not in known]
    if extras:
        raise CampaignIntegrityError(
            f"foreign or duplicate review directories present: {sorted(extras)}"
        )
    captured_by_id = {case.case_id: (record, digest) for case, record, digest in captured}
    prepared_ids: list[str] = []
    reviewed_ids: list[str] = []
    for case in CAMPAIGN_PLAN:
        review_dir = _review_dir(session_dir, case)
        if not review_dir.exists():
            continue
        _require_real_subdirectory(
            session_dir, review_dir, label=f"review case {case.case_id}"
        )
        if not review_dir.is_dir() or not _review_preparation_path(
            session_dir, case
        ).exists():
            raise CampaignIntegrityError(f"partial review evidence for {case.case_id}")
        record, capture_sha = captured_by_id[case.case_id]
        preparation, _ = _load_review_preparation(
            session_dir, session, case, record, capture_sha
        )
        artifacts = cast(dict[str, object], preparation["privacy_artifacts"])
        allowed = {
            "review-preparation.json",
            "review-preparation.json.sha256",
        }
        if artifacts.get("mode") == "sanitized-frame":
            allowed.update(
                {
                    "sanitized-frame.raw.gz",
                    "sanitized-frame.raw.gz.sha256",
                    "sanitized-preview.bmp",
                    "sanitized-preview.bmp.sha256",
                }
            )
        truth_path = _review_truth_path(session_dir, case)
        if truth_path.exists():
            allowed.update({"reviewer-truth.json", "reviewer-truth.json.sha256"})
            _load_review(session_dir, session, case, record, capture_sha)
            reviewed_ids.append(case.case_id)
        elif _artifact_sidecar(truth_path).exists():
            raise CampaignIntegrityError(f"partial review truth for {case.case_id}")
        foreign = sorted(path.name for path in review_dir.iterdir() if path.name not in allowed)
        if foreign:
            raise CampaignIntegrityError(
                f"foreign review artifacts for {case.case_id}: {foreign}"
            )
        prepared_ids.append(case.case_id)
    return tuple(prepared_ids), tuple(reviewed_ids)


def review_template_for_case(
    session_dir: Path,
    case_id: str,
    *,
    repository: RepositoryProvenance,
) -> dict[str, object]:
    """Build a blank truth template bound only to exact reviewed artifacts."""

    session_dir = Path(session_dir)
    session = _load_session(session_dir)
    _verify_repository_binding(session, repository)
    captured = _captured_prefix(session_dir, session)
    if len(captured) != len(CAMPAIGN_PLAN):
        raise CampaignError("review template requires the full sealed campaign")
    _verify_completion_seal(session_dir, session, captured)
    try:
        case = next(item for item in CAMPAIGN_PLAN if item.case_id == case_id)
    except StopIteration as exc:
        raise CampaignError("review template contains a foreign case") from exc
    captured_by_id = {item.case_id: (record, digest) for item, record, digest in captured}
    record, capture_sha = captured_by_id[case.case_id]
    _, preparation_sha = _load_review_preparation(
        session_dir, session, case, record, capture_sha
    )
    return {
        "case_id": case.case_id,
        "reviewer_id": "",
        "reviewed_at_utc": "",
        "meaning": "",
        "resource_truth": [
            {"resource_id": resource_id, "state": ""}
            for resource_id in VARROCK_EAST_IRON_RESOURCE_IDS
        ],
        "review_artifact_sha256": preparation_sha,
        "privacy_review_confirmed": False,
        "focal_resource_id": None,
        "node_phase": None,
        "obstruction_target": None,
        "subject_region": None,
        "notes": "",
    }


def _truth_state_vector(truth: Mapping[str, object]) -> tuple[ResourceVisualState, ...]:
    review = truth.get("review")
    if not isinstance(review, dict):
        raise CampaignIntegrityError("review decision is malformed")
    raw_truth = review.get("resource_truth")
    if not isinstance(raw_truth, list) or len(raw_truth) != len(
        VARROCK_EAST_IRON_RESOURCE_IDS
    ):
        raise CampaignIntegrityError("review truth does not cover all resources")
    states: list[ResourceVisualState] = []
    for expected_id, item in zip(VARROCK_EAST_IRON_RESOURCE_IDS, raw_truth, strict=True):
        if not isinstance(item, dict) or set(item) != {"resource_id", "state"}:
            raise CampaignIntegrityError("review resource truth item is malformed")
        if item.get("resource_id") != expected_id:
            raise CampaignIntegrityError("review resource truth order/identity changed")
        try:
            states.append(ResourceVisualState(cast(str, item.get("state"))))
        except ValueError as exc:
            raise CampaignIntegrityError("review resource truth state is invalid") from exc
    return tuple(states)


def _production_state_vector(
    production: Mapping[str, object],
) -> tuple[ResourceVisualState, ...] | None:
    if production.get("status") != "completed":
        return None
    trust = production.get("trust")
    if not isinstance(trust, dict) or trust.get("accepted") is not True:
        return None
    resources = trust.get("resources")
    if not isinstance(resources, list) or len(resources) != len(
        VARROCK_EAST_IRON_RESOURCE_IDS
    ):
        return None
    states: list[ResourceVisualState] = []
    for expected_id, item in zip(VARROCK_EAST_IRON_RESOURCE_IDS, resources, strict=True):
        if not isinstance(item, dict) or item.get("resource_id") != expected_id:
            return None
        available = item.get("available")
        if available is True:
            states.append(ResourceVisualState.AVAILABLE)
        elif available is False:
            states.append(ResourceVisualState.DEPLETED)
        elif available is None:
            states.append(ResourceVisualState.UNCERTAIN)
        else:
            return None
    return tuple(states)


def _regions_overlap(
    first: tuple[int, int, int, int], second: tuple[int, int, int, int]
) -> bool:
    first_x, first_y, first_width, first_height = first
    second_x, second_y, second_width, second_height = second
    return (
        first_x < second_x + second_width
        and second_x < first_x + first_width
        and first_y < second_y + second_height
        and second_y < first_y + first_height
    )


def _case_release_result(
    session_dir: Path,
    case: CampaignCase,
    record: Mapping[str, object],
    truth: Mapping[str, object],
) -> dict[str, object]:
    reasons: list[str] = []
    evidence_origin = record.get("evidence_origin")
    if evidence_origin != _SOURCE_OWNED_EVIDENCE_ORIGIN:
        reasons.append("non_source_owned_capture_evidence")
    prior_failure_provenance = record.get("prior_no_frame_failure_provenance")
    if not isinstance(prior_failure_provenance, list) or any(
        not isinstance(item, dict)
        or item.get("evidence_origin") != _SOURCE_OWNED_EVIDENCE_ORIGIN
        or item.get("capture_source_backend_name") != _LIVE_CAPTURE_BACKEND_NAME
        for item in prior_failure_provenance
    ):
        reasons.append("mixed_or_malformed_prior_capture_origin")
    environment = record.get("capture_environment")
    if not isinstance(environment, dict):
        raise CampaignIntegrityError(
            f"capture {case.case_id} environment provenance is malformed"
        )
    reported_dpi = environment.get("reported_dpi")
    if reported_dpi is None:
        reasons.append("required_release_envelope_reported_dpi_missing")
    elif reported_dpi != _REQUIRED_REPORTED_DPI:
        reasons.append("required_release_envelope_reported_dpi_not_96")
    review = truth.get("review")
    if not isinstance(review, dict):
        raise CampaignIntegrityError(f"review {case.case_id} is malformed")
    if review.get("meaning") != case.review_meaning.value:
        reasons.append("reviewer_did_not_confirm_requested_case_meaning")
    truth_states = _truth_state_vector(truth)
    production = record.get("production")
    if not isinstance(production, dict):
        raise CampaignIntegrityError(f"capture {case.case_id} production output is malformed")
    production_states = _production_state_vector(production)
    if production_states is None:
        reasons.append("production_ensemble_not_complete_or_trusted")
    elif production_states != truth_states:
        reasons.append("production_resource_state_vector_disagrees_with_reviewer_truth")
    frame = _frame_from_record(session_dir, record)
    reviewable_geometry = _has_reviewable_geometry(frame)
    if not reviewable_geometry:
        reasons.append("wrong_frame_geometry_or_pixel_format")
    try:
        replay_production: object = _production_json(frame)
    except Exception as exc:
        replay_production = _detector_error(exc)
    if not _production_equivalent(production, replay_production):
        reasons.append("stored_production_report_does_not_match_exact_replay")
    replay_check = truth.get("replay_check")
    if not isinstance(replay_check, dict) or replay_check.get(
        "capture_report_matches_replay"
    ) is not True:
        reasons.append("review_capture_replay_binding_failed")
    if reviewable_geometry and (
        not isinstance(replay_check, dict)
        or replay_check.get("sanitization_preserves_authoritative_production") is not True
    ):
        reasons.append("privacy_sanitization_changed_production_authority")
    scene = production.get("scene")
    scene_validated = isinstance(scene, dict) and scene.get("validated") is True
    truth_definitive = [
        resource_id
        for resource_id, state in zip(
            VARROCK_EAST_IRON_RESOURCE_IDS, truth_states, strict=True
        )
        if state is not ResourceVisualState.UNCERTAIN
    ]
    truth_actionable = [
        resource_id
        for resource_id, state in zip(
            VARROCK_EAST_IRON_RESOURCE_IDS, truth_states, strict=True
        )
        if state is ResourceVisualState.AVAILABLE
    ]
    trust = production.get("trust")
    if not isinstance(trust, dict):
        reasons.append("production_trust_payload_missing")
    else:
        if trust.get("definitive_target_ids") != truth_definitive:
            reasons.append("definitive_target_ids_disagree_with_reviewer_truth")
        if trust.get("production_actionable_target_ids") != truth_actionable:
            reasons.append("actionable_target_ids_disagree_with_reviewer_truth")
        regions = trust.get("production_interaction_regions")
        profile = load_varrock_east_iron_profile()
        expected_regions = {
            candidate.resource_id: (
                list(candidate.region)
                if truth_states[index] is ResourceVisualState.AVAILABLE
                else None
            )
            for index, candidate in enumerate(profile.candidates)
        }
        if regions != expected_regions:
            reasons.append("interaction_regions_disagree_with_reviewer_truth")
    if case.review_meaning is ReviewMeaning.SUPPORTED_STARTUP:
        if not scene_validated:
            reasons.append("fresh_startup_scene_not_production_validated")
        if ResourceVisualState.UNCERTAIN in truth_states:
            reasons.append("fresh_startup_contains_uncertain_resource_truth")
    elif case.review_meaning is ReviewMeaning.PROFILED_NODE_STATE:
        if not scene_validated:
            reasons.append("node_cycle_scene_not_production_validated")
        focal_index = VARROCK_EAST_IRON_RESOURCE_IDS.index(
            cast(str, case.focal_resource_id)
        )
        if truth_states[focal_index] is not case.requested_focal_state:
            reasons.append("reviewer_truth_does_not_confirm_requested_cycle_state")
        if review.get("focal_resource_id") != case.focal_resource_id:
            reasons.append("reviewer_truth_does_not_confirm_cycle_focal_resource")
        expected_phase = (
            None
            if case.requested_node_phase is None
            else case.requested_node_phase.value
        )
        if review.get("node_phase") != expected_phase:
            reasons.append("reviewer_truth_does_not_confirm_requested_cycle_phase")
    elif case.review_meaning is ReviewMeaning.UNSUPPORTED_LOCATION:
        if any(state is not ResourceVisualState.UNCERTAIN for state in truth_states):
            reasons.append("unsupported_location_truth_is_not_all_uncertain")
        if truth_definitive or truth_actionable:
            reasons.append("unsupported_location_exposes_false_iron_target")
        if scene_validated:
            reasons.append("unsupported_location_unexpectedly_validated_supported_scene")
    elif case.review_meaning in {
        ReviewMeaning.NEIGHBORING_COPPER,
        ReviewMeaning.NEIGHBORING_TIN,
        ReviewMeaning.TERRAIN_CLUTTER,
    }:
        subject_raw = review.get("subject_region")
        if (
            not isinstance(subject_raw, list)
            or len(subject_raw) != 4
            or any(not isinstance(value, int) for value in subject_raw)
        ):
            reasons.append("reviewed_negative_subject_region_missing")
        elif isinstance(trust, dict):
            regions = trust.get("production_interaction_regions")
            if not isinstance(regions, dict):
                reasons.append("negative_case_interaction_regions_missing")
            else:
                subject = (
                    cast(int, subject_raw[0]),
                    cast(int, subject_raw[1]),
                    cast(int, subject_raw[2]),
                    cast(int, subject_raw[3]),
                )
                for value in regions.values():
                    if value is None:
                        continue
                    if (
                        not isinstance(value, list)
                        or len(value) != 4
                        or any(not isinstance(item, int) for item in value)
                    ):
                        reasons.append("negative_case_interaction_region_malformed")
                        break
                    interaction = (
                        cast(int, value[0]),
                        cast(int, value[1]),
                        cast(int, value[2]),
                        cast(int, value[3]),
                    )
                    if _regions_overlap(subject, interaction):
                        reasons.append("negative_subject_overlaps_false_iron_target")
                        break
        if not scene_validated:
            if any(state is not ResourceVisualState.UNCERTAIN for state in truth_states):
                reasons.append("unsupported_negative_view_is_not_fail_closed")
            if truth_definitive or truth_actionable:
                reasons.append("unsupported_negative_view_exposes_target")
    elif case.review_meaning is ReviewMeaning.PROFILED_OBSTRUCTION:
        obstruction = review.get("obstruction_target")
        if not isinstance(obstruction, dict):
            reasons.append("obstruction_target_truth_missing")
        elif obstruction.get("kind") == "landmark":
            target_id = obstruction.get("target_id")
            landmark_results = scene.get("landmarks") if isinstance(scene, dict) else None
            target_result = None
            if isinstance(landmark_results, list):
                target_result = next(
                    (
                        item
                        for item in landmark_results
                        if isinstance(item, dict)
                        and item.get("landmark_id") == target_id
                    ),
                    None,
                )
            if not isinstance(target_result, dict):
                reasons.append("obstructed_landmark_result_missing")
            elif target_result.get("matched") is not False:
                reasons.append("reviewed_landmark_obstruction_did_not_fail_target")
    passed = not reasons
    privacy_artifacts = truth.get("privacy_artifacts")
    replay_candidate: object = None
    if isinstance(privacy_artifacts, dict):
        replay_candidate = privacy_artifacts.get("sanitized_raw_gzip")
    return {
        "ordinal": case.ordinal,
        "case_id": case.case_id,
        "blocker_id": case.blocker_id,
        "evidence_origin": evidence_origin,
        "passed": passed,
        "reasons": reasons,
        "production_scene_validated": scene_validated,
        "reviewed_state_vector": [state.value for state in truth_states],
        "production_state_vector": (
            None
            if production_states is None
            else [state.value for state in production_states]
        ),
        "reported_dpi": reported_dpi,
        "required_reported_dpi": _REQUIRED_REPORTED_DPI,
        "replay_regression_candidate": replay_candidate,
        "permanent_evidence_required": not passed,
        "policy_change_allowed_from_failure": False,
    }


def _release_gate_categories(
    blockers: Sequence[Mapping[str, object]],
    case_results: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    c1 = [
        item
        for item in blockers
        if item.get("blocker_id") in _RESOURCE_BLOCKER_ORDER
    ]
    c1_closed = len(c1) == len(_RESOURCE_BLOCKER_ORDER) and all(
        item.get("status") == "CLOSED" for item in c1
    )
    retained_failure_case_ids = [
        cast(str, item["case_id"])
        for item in case_results
        if item.get("passed") is False and "evidence_origin" in item
    ]
    return {
        "c1_fresh_empirical_evidence": {
            "status": "CLOSED" if c1_closed else "OPEN",
            "blocker_ids": list(_RESOURCE_BLOCKER_ORDER),
        },
        "c2_evidence_contingent_source_review": {
            "status": "OPEN",
            "gates": [
                {
                    "gate_id": gate_id,
                    "status": (
                        "OPEN"
                        if gate_id != _C2_GATE_ORDER[0]
                        or retained_failure_case_ids
                        else "CLOSED"
                    ),
                    "reason": reason,
                    "case_ids": (
                        retained_failure_case_ids
                        if gate_id == _C2_GATE_ORDER[0]
                        else []
                    ),
                }
                for gate_id, reason in zip(
                    _C2_GATE_ORDER, _C2_GATE_REASONS, strict=True
                )
            ],
        },
    }


def evaluate_release(
    session_dir: Path,
    *,
    repository: RepositoryProvenance,
    evaluated_at_utc: datetime | None = None,
) -> dict[str, object]:
    """Recompute exact PASS/FAIL results and the PR #39 blocker ledger."""

    session_dir = Path(session_dir)
    session = _load_session(session_dir)
    _verify_repository_binding(session, repository)
    captured = _captured_prefix(session_dir, session)
    if len(captured) != len(CAMPAIGN_PLAN):
        raise CampaignError("release evaluation requires the complete fixed campaign")
    seal = _verify_completion_seal(session_dir, session, captured)
    _review_inventory(session_dir, session, captured)
    evaluated = datetime.now(UTC) if evaluated_at_utc is None else evaluated_at_utc
    _require_utc(evaluated, "evaluated_at_utc")
    if evaluated < _parse_utc(seal.get("sealed_at_utc"), "sealed_at_utc"):
        raise CampaignError("release evaluation cannot predate campaign finalization")
    case_results: list[dict[str, object]] = []
    review_hashes: list[dict[str, object]] = []
    captured_by_id = {case.case_id: (record, digest) for case, record, digest in captured}
    for case in CAMPAIGN_PLAN:
        record, capture_report_sha = captured_by_id[case.case_id]
        truth_path = _review_truth_path(session_dir, case)
        if not truth_path.exists():
            case_results.append(
                {
                    "ordinal": case.ordinal,
                    "case_id": case.case_id,
                    "blocker_id": case.blocker_id,
                    "passed": False,
                    "reasons": ["independent_reviewer_truth_missing"],
                    "permanent_evidence_required": True,
                    "policy_change_allowed_from_failure": False,
                }
            )
            continue
        truth, truth_sha = _load_review(
            session_dir,
            session,
            case,
            record,
            capture_report_sha,
        )
        review_value = truth.get("review")
        if not isinstance(review_value, dict):
            raise CampaignIntegrityError("review chronology payload is malformed")
        decision = review_decision_from_json(review_value)
        recorded_at = _parse_utc(truth.get("recorded_at_utc"), "recorded_at_utc")
        if evaluated < decision.reviewed_at_utc or evaluated < recorded_at:
            raise CampaignError("release evaluation predates reviewer truth")
        review_hashes.append({"case_id": case.case_id, "review_sha256": truth_sha})
        case_results.append(_case_release_result(session_dir, case, record, truth))
    blockers: list[dict[str, object]] = []
    for blocker_id in _RESOURCE_BLOCKER_ORDER:
        members = [item for item in case_results if item["blocker_id"] == blocker_id]
        closed = bool(members) and all(item["passed"] is True for item in members)
        blockers.append(
            {
                "blocker_id": blocker_id,
                "status": "CLOSED" if closed else "STILL_OPEN",
                "case_ids": [cast(str, item["case_id"]) for item in members],
                "reasons": [
                    reason
                    for item in members
                    if item["passed"] is not True
                    for reason in cast(list[str], item["reasons"])
                ],
            }
        )
    blockers.append(
        {
            "blocker_id": _FINAL_REVIEW_BLOCKER_ID,
            "status": "STILL_OPEN",
            "case_ids": [],
            "reasons": [
                _FINAL_REVIEW_REASON
            ],
        }
    )
    closed_ids = [
        item["blocker_id"] for item in blockers if item["status"] == "CLOSED"
    ]
    still_open_ids = [
        item["blocker_id"] for item in blockers if item["status"] == "STILL_OPEN"
    ]
    return {
        "schema_version": RESOURCE_RELEASE_CAMPAIGN_SCHEMA_VERSION,
        "report_id": "resource-release-campaign-report-v1",
        "campaign_id": RESOURCE_RELEASE_CAMPAIGN_ID,
        "campaign_version": RESOURCE_RELEASE_CAMPAIGN_VERSION,
        "session_id": session["session_id"],
        "evaluated_at_utc": _utc_text(evaluated),
        "repository": session["repository"],
        "profile": session["profile"],
        "capture_configuration": session["capture_configuration"],
        "completion_seal_sha256": _verify_hashed_artifact(
            session_dir / "completion-seal.json"
        )[1],
        "review_hashes": review_hashes,
        "case_results": case_results,
        "blockers": blockers,
        "closed_blockers": closed_ids,
        "still_open_blockers": still_open_ids,
        "release_gate_categories": _release_gate_categories(blockers, case_results),
        "release_eligible": False,
        "activation_allowed": False,
        "promotion_allowed": False,
        "input_authority": False,
        "detector_policy_changed": False,
        "live_resource_campaign_authorized": cast(
            dict[str, object], session["capture_configuration"]
        )["live_source_authorized"],
    }


def _validate_release_report(report: Mapping[str, object]) -> None:
    if not isinstance(report, Mapping):
        raise TypeError("report must be a mapping")
    required = {
        "schema_version",
        "report_id",
        "campaign_id",
        "campaign_version",
        "session_id",
        "evaluated_at_utc",
        "repository",
        "profile",
        "capture_configuration",
        "completion_seal_sha256",
        "review_hashes",
        "case_results",
        "blockers",
        "closed_blockers",
        "still_open_blockers",
        "release_gate_categories",
        "release_eligible",
        "activation_allowed",
        "promotion_allowed",
        "input_authority",
        "detector_policy_changed",
        "live_resource_campaign_authorized",
    }
    if set(report) != required:
        raise CampaignError("resource campaign report fields are malformed")
    if (
        not _is_strict_int(report.get("schema_version"))
        or report.get("schema_version") != RESOURCE_RELEASE_CAMPAIGN_SCHEMA_VERSION
        or report.get("report_id") != "resource-release-campaign-report-v1"
        or report.get("campaign_id") != RESOURCE_RELEASE_CAMPAIGN_ID
        or report.get("campaign_version") != RESOURCE_RELEASE_CAMPAIGN_VERSION
    ):
        raise CampaignError("resource campaign report identity changed")
    if (
        report.get("activation_allowed") is not False
        or report.get("promotion_allowed") is not False
        or report.get("input_authority") is not False
        or report.get("detector_policy_changed") is not False
    ):
        raise CampaignError("resource campaign reports must remain nonactivating")
    session_id = report.get("session_id")
    if not isinstance(session_id, str) or not _IDENTIFIER_PATTERN.fullmatch(session_id):
        raise CampaignError("resource campaign report session identity is malformed")
    _parse_utc(report.get("evaluated_at_utc"), "evaluated_at_utc")
    repository = report.get("repository")
    if not isinstance(repository, dict) or set(repository) != {
        "head_sha",
        "branch",
        "clean",
    }:
        raise CampaignError("resource campaign repository provenance is malformed")
    try:
        RepositoryProvenance(
            head_sha=cast(str, repository.get("head_sha")),
            branch=cast(str, repository.get("branch")),
            clean=cast(bool, repository.get("clean")),
        )
    except (TypeError, ValueError) as exc:
        raise CampaignError(f"resource campaign repository is invalid: {exc}") from exc
    if (
        repository.get("clean") is not True
        or report.get("profile") != _profile_identity()
    ):
        raise CampaignError("resource campaign source/profile provenance changed")
    _validate_capture_configuration(report.get("capture_configuration"))
    seal_sha = report.get("completion_seal_sha256")
    if not isinstance(seal_sha, str) or not _SHA256_PATTERN.fullmatch(seal_sha):
        raise CampaignError("resource campaign completion seal hash is malformed")

    case_results = report.get("case_results")
    if not isinstance(case_results, list) or len(case_results) != len(CAMPAIGN_PLAN):
        raise CampaignError("resource campaign case results are malformed")
    full_case_fields = {
        "ordinal",
        "case_id",
        "blocker_id",
        "evidence_origin",
        "passed",
        "reasons",
        "production_scene_validated",
        "reviewed_state_vector",
        "production_state_vector",
        "reported_dpi",
        "required_reported_dpi",
        "replay_regression_candidate",
        "permanent_evidence_required",
        "policy_change_allowed_from_failure",
    }
    missing_review_fields = {
        "ordinal",
        "case_id",
        "blocker_id",
        "passed",
        "reasons",
        "permanent_evidence_required",
        "policy_change_allowed_from_failure",
    }
    reviewed_case_ids: list[str] = []
    for case, result_raw in zip(CAMPAIGN_PLAN, case_results, strict=True):
        if not isinstance(result_raw, dict):
            raise CampaignError("resource campaign case result is not an object")
        if (
            not isinstance(result_raw.get("ordinal"), int)
            or isinstance(result_raw.get("ordinal"), bool)
            or result_raw.get("ordinal") != case.ordinal
            or result_raw.get("case_id") != case.case_id
            or result_raw.get("blocker_id") != case.blocker_id
        ):
            raise CampaignError("resource campaign case result order/identity changed")
        reasons = result_raw.get("reasons")
        if not isinstance(reasons, list) or any(
            not isinstance(reason, str) or not reason for reason in reasons
        ):
            raise CampaignError("resource campaign case reasons are malformed")
        passed = result_raw.get("passed")
        if (
            not isinstance(passed, bool)
            or result_raw.get("permanent_evidence_required") is not (not passed)
            or result_raw.get("policy_change_allowed_from_failure") is not False
            or (passed and reasons)
            or (not passed and not reasons)
        ):
            raise CampaignError("resource campaign case verdict is inconsistent")
        if set(result_raw) == missing_review_fields:
            if reasons != ["independent_reviewer_truth_missing"]:
                raise CampaignError("missing-review case result was altered")
            continue
        if set(result_raw) != full_case_fields:
            raise CampaignError("resource campaign full case fields are malformed")
        reviewed_case_ids.append(case.case_id)
        origin = result_raw.get("evidence_origin")
        if origin not in {
            _SOURCE_OWNED_EVIDENCE_ORIGIN,
            _INJECTED_EVIDENCE_ORIGIN,
        }:
            raise CampaignError("resource campaign evidence origin is malformed")
        if origin != _SOURCE_OWNED_EVIDENCE_ORIGIN and (
            passed or "non_source_owned_capture_evidence" not in reasons
        ):
            raise CampaignError("injected evidence was promoted as real")
        reported_dpi = result_raw.get("reported_dpi")
        required_reported_dpi = result_raw.get("required_reported_dpi")
        if (
            (reported_dpi is not None and (
                not _is_strict_int(reported_dpi) or cast(int, reported_dpi) <= 0
            ))
            or required_reported_dpi != _REQUIRED_REPORTED_DPI
            or not _is_strict_int(required_reported_dpi)
        ):
            raise CampaignError("resource campaign DPI evidence is malformed")
        missing_reason = "required_release_envelope_reported_dpi_missing"
        mismatch_reason = "required_release_envelope_reported_dpi_not_96"
        expected_dpi_reason = (
            missing_reason
            if reported_dpi is None
            else mismatch_reason
            if reported_dpi != _REQUIRED_REPORTED_DPI
            else None
        )
        if (
            (expected_dpi_reason is not None and expected_dpi_reason not in reasons)
            or (expected_dpi_reason is None and (
                missing_reason in reasons or mismatch_reason in reasons
            ))
            or (expected_dpi_reason is not None and passed)
        ):
            raise CampaignError("resource campaign DPI eligibility is inconsistent")
        if not isinstance(result_raw.get("production_scene_validated"), bool):
            raise CampaignError("resource campaign scene verdict is malformed")
        reviewed_states = result_raw.get("reviewed_state_vector")
        production_states = result_raw.get("production_state_vector")
        allowed_states = {item.value for item in ResourceVisualState}
        if (
            not isinstance(reviewed_states, list)
            or len(reviewed_states) != len(VARROCK_EAST_IRON_RESOURCE_IDS)
            or any(state not in allowed_states for state in reviewed_states)
            or (
                production_states is not None
                and (
                    not isinstance(production_states, list)
                    or len(production_states) != len(VARROCK_EAST_IRON_RESOURCE_IDS)
                    or any(state not in allowed_states for state in production_states)
                )
            )
        ):
            raise CampaignError("resource campaign state vector is malformed")
        replay = result_raw.get("replay_regression_candidate")
        if replay is not None:
            if not isinstance(replay, dict) or set(replay) not in (
                {"path", "file_sha256", "decompressed_sha256"},
                {"path", "sha256"},
            ):
                raise CampaignError("resource campaign replay binding is malformed")
            if not isinstance(replay.get("path"), str):
                raise CampaignError("resource campaign replay path is malformed")
            for key in set(replay) - {"path"}:
                value = replay.get(key)
                if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
                    raise CampaignError("resource campaign replay hash is malformed")

    review_hashes = report.get("review_hashes")
    if not isinstance(review_hashes, list) or len(review_hashes) != len(
        reviewed_case_ids
    ):
        raise CampaignError("resource campaign review hashes are malformed")
    for case_id, binding in zip(reviewed_case_ids, review_hashes, strict=True):
        if not isinstance(binding, dict) or set(binding) != {
            "case_id",
            "review_sha256",
        }:
            raise CampaignError("resource campaign review hash binding is malformed")
        review_sha = binding.get("review_sha256")
        if (
            binding.get("case_id") != case_id
            or not isinstance(review_sha, str)
            or not _SHA256_PATTERN.fullmatch(review_sha)
        ):
            raise CampaignError("resource campaign review hash identity changed")

    blockers = report.get("blockers")
    expected_blocker_ids = (*_RESOURCE_BLOCKER_ORDER, _FINAL_REVIEW_BLOCKER_ID)
    if not isinstance(blockers, list) or len(blockers) != len(expected_blocker_ids):
        raise CampaignError("resource campaign blocker ledger is malformed")
    expected_blockers: list[dict[str, object]] = []
    for blocker_id in _RESOURCE_BLOCKER_ORDER:
        members = [
            item for item in cast(list[dict[str, object]], case_results)
            if item["blocker_id"] == blocker_id
        ]
        closed = bool(members) and all(item["passed"] is True for item in members)
        expected_blockers.append(
            {
                "blocker_id": blocker_id,
                "status": "CLOSED" if closed else "STILL_OPEN",
                "case_ids": [item["case_id"] for item in members],
                "reasons": [
                    reason
                    for item in members
                    if item["passed"] is not True
                    for reason in cast(list[str], item["reasons"])
                ],
            }
        )
    expected_blockers.append(
        {
            "blocker_id": _FINAL_REVIEW_BLOCKER_ID,
            "status": "STILL_OPEN",
            "case_ids": [],
            "reasons": [_FINAL_REVIEW_REASON],
        }
    )
    if blockers != expected_blockers:
        raise CampaignError("resource campaign blocker ledger was not derived from cases")
    expected_categories = _release_gate_categories(
        expected_blockers, cast(list[dict[str, object]], case_results)
    )
    closed_ids_derived = [
        item["blocker_id"] for item in expected_blockers if item["status"] == "CLOSED"
    ]
    still_open = [
        item["blocker_id"]
        for item in expected_blockers
        if item["status"] == "STILL_OPEN"
    ]
    if (
        report.get("closed_blockers") != closed_ids_derived
        or report.get("still_open_blockers") != still_open
        or report.get("release_gate_categories") != expected_categories
        or _FINAL_REVIEW_BLOCKER_ID not in still_open
        or report.get("release_eligible") is not False
        or not isinstance(report.get("live_resource_campaign_authorized"), bool)
    ):
        raise CampaignError("resource campaign release eligibility is inconsistent")


def write_release_summary(path: Path, report: Mapping[str, object]) -> str:
    """Publish one immutable canonical release summary and adjacent hash."""

    _validate_release_report(report)
    return _write_hashed_artifact(Path(path), _canonical_json_bytes(report))


def _public_environment(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise CampaignIntegrityError("capture environment is malformed")
    expected = {
        "backend_name",
        "title_match",
        "window_title",
        "window_class",
        "window_hwnd",
        "window_client_width",
        "window_client_height",
        "reported_dpi",
    }
    if set(value) != expected:
        raise CampaignIntegrityError("capture environment fields are malformed")
    window_title = value.get("window_title")
    if window_title is not None and not isinstance(window_title, str):
        raise CampaignIntegrityError("capture window title is malformed")
    return {
        "backend_name": value.get("backend_name"),
        "title_match": value.get("title_match"),
        "window_title_present": window_title is not None,
        "window_class": value.get("window_class"),
        "window_client_width": value.get("window_client_width"),
        "window_client_height": value.get("window_client_height"),
        "reported_dpi": value.get("reported_dpi"),
        "window_title_redacted": True,
        "native_window_handle_redacted": True,
    }


def _public_production(value: object) -> object:
    if not isinstance(value, dict) or value.get("status") != "detector-error":
        return value
    public = dict(value)
    public["error"] = "private runtime detail redacted"
    return public


def _withheld_public_authority() -> dict[str, object]:
    """Return the sole public authority shape for evidence whose pixels are withheld."""

    return {
        "status": "pixels-withheld-unsupported-geometry",
        "detector_id": VARROCK_EAST_IRON_DETECTOR_ID,
        "detector_version": VARROCK_EAST_IRON_DETECTOR_VERSION,
        "trust": {
            "accepted": False,
            "reason": "pixels_withheld_unsupported_geometry",
            "frame": None,
            "resources": [],
            "definitive_target_ids": [],
            "production_actionable_target_ids": [],
            "production_interaction_regions": {},
        },
        "scene": {
            "validated": False,
            "matched_count": 0,
            "required_quorum": 5,
            "matched_zones": [],
            "required_zones": 3,
        },
        "passive_campaign_authorized_target_ids": [],
        "stop_required": True,
        "input_authority": False,
    }


def _public_withheld_production(value: object) -> dict[str, object]:
    # The private capture report remains hash-bound to the exact production
    # output. The public package cannot expose or replay pixels for this case,
    # so it publishes only a canonical deny-only authority projection.
    _ = value
    return {
        "status": "pixels-withheld-unsupported-geometry",
        "production_authority": _withheld_public_authority(),
        "private_diagnostics_redacted": True,
        "input_authority": False,
    }


def _public_review(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise CampaignIntegrityError("review decision is malformed")
    decision = review_decision_from_json(value)
    public = _review_decision_json(decision)
    public.pop("reviewer_id")
    public.pop("notes")
    public["reviewer_role"] = "independent-reviewer-identity-redacted"
    public["reviewer_identity_redacted"] = True
    public["free_form_notes_redacted"] = True
    return public


def export_review_package(
    session_dir: Path,
    output_dir: Path,
    *,
    repository: RepositoryProvenance,
    exported_at_utc: datetime | None = None,
) -> dict[str, object]:
    """Export only reviewed, privacy-safe evidence with a manifest-last snapshot."""

    session_dir = Path(session_dir)
    output_dir = Path(output_dir)
    session = _load_session(session_dir)
    _verify_repository_binding(session, repository)
    captured = _captured_prefix(session_dir, session)
    if len(captured) != len(CAMPAIGN_PLAN):
        raise CampaignError("review export requires the complete fixed campaign")
    seal = _verify_completion_seal(session_dir, session, captured)
    exported = datetime.now(UTC) if exported_at_utc is None else exported_at_utc
    _require_utc(exported, "exported_at_utc")
    if exported < _parse_utc(seal.get("sealed_at_utc"), "sealed_at_utc"):
        raise CampaignError("review export cannot predate campaign finalization")
    release = evaluate_release(
        session_dir,
        repository=repository,
        evaluated_at_utc=exported,
    )
    if len(cast(list[object], release["review_hashes"])) != len(CAMPAIGN_PLAN):
        raise CampaignError("review export requires independent truth for all 15 cases")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(exist_ok=False)
    public_cases: list[dict[str, object]] = []
    captured_by_id = {case.case_id: (record, digest) for case, record, digest in captured}
    public_release = _strict_json_bytes(
        _canonical_json_bytes(release), label="public release snapshot"
    )
    release_by_id = {
        cast(str, item["case_id"]): item
        for item in cast(list[dict[str, object]], public_release["case_results"])
    }
    release_review_hashes = {
        cast(str, item["case_id"]): cast(str, item["review_sha256"])
        for item in cast(list[dict[str, object]], release["review_hashes"])
    }
    for case in CAMPAIGN_PLAN:
        record, capture_report_sha = captured_by_id[case.case_id]
        truth, truth_sha = _load_review(
            session_dir,
            session,
            case,
            record,
            capture_report_sha,
        )
        if release_review_hashes.get(case.case_id) != truth_sha:
            raise CampaignIntegrityError(
                "review truth changed during immutable package snapshot"
            )
        artifacts = cast(dict[str, object], truth["privacy_artifacts"])
        target_dir = output_dir / "cases" / _case_stem(case)
        target_dir.mkdir(parents=True, exist_ok=False)
        public_artifacts: dict[str, object]
        public_production: object
        source_gzip_sha: str | None = None
        source_preview_sha: str | None = None
        if artifacts.get("mode") == "sanitized-frame":
            gzip_meta = cast(dict[str, object], artifacts["sanitized_raw_gzip"])
            preview_meta = cast(dict[str, object], artifacts["sanitized_preview"])
            source_gzip = session_dir / cast(str, gzip_meta["path"])
            source_preview = session_dir / cast(str, preview_meta["path"])
            gzip_payload, source_gzip_sha = _verify_hashed_artifact(
                source_gzip, expected=cast(str, gzip_meta["file_sha256"])
            )
            preview_payload, source_preview_sha = _verify_hashed_artifact(
                source_preview, expected=cast(str, preview_meta["sha256"])
            )
            target_gzip = target_dir / "sanitized-frame.raw.gz"
            target_preview = target_dir / "sanitized-preview.bmp"
            if _write_hashed_artifact(target_gzip, gzip_payload) != source_gzip_sha:
                raise CampaignIntegrityError("sanitized gzip changed during review export")
            if _write_hashed_artifact(target_preview, preview_payload) != source_preview_sha:
                raise CampaignIntegrityError("sanitized preview changed during review export")
            public_artifacts = {
                "mode": "sanitized-frame",
                "raw_gzip": {
                    "path": f"cases/{_case_stem(case)}/sanitized-frame.raw.gz",
                    "sha256": source_gzip_sha,
                    "decompressed_sha256": gzip_meta["decompressed_sha256"],
                },
                "preview": {
                    "path": f"cases/{_case_stem(case)}/sanitized-preview.bmp",
                    "sha256": source_preview_sha,
                },
                "mask": artifacts["mask"],
                "full_geometry_preserved": True,
            }
            replay_check = truth.get("replay_check")
            if not isinstance(replay_check, dict):
                raise CampaignIntegrityError("review replay evidence is malformed")
            sanitized_production = replay_check.get("sanitized_production")
            if sanitized_production is None:
                raise CampaignIntegrityError(
                    "sanitized review case lacks replayable production evidence"
                )
            public_production = _public_production(sanitized_production)
            release_by_id[case.case_id]["replay_regression_candidate"] = {
                "path": f"cases/{_case_stem(case)}/sanitized-frame.raw.gz",
                "sha256": source_gzip_sha,
            }
        elif artifacts.get("mode") == "pixels-withheld-unsupported-geometry":
            public_artifacts = {
                "mode": "pixels-withheld-unsupported-geometry",
                "raw_gzip": None,
                "preview": None,
                "mask": None,
                "full_geometry_preserved": False,
                "pixels_withheld_reason": "unsupported_geometry_or_pixel_format",
            }
            public_production = _public_withheld_production(record["production"])
            release_by_id[case.case_id]["replay_regression_candidate"] = None
        else:  # _load_review already rejects this; retain fail-closed defense.
            raise CampaignIntegrityError("unknown privacy artifact mode during export")
        source_bindings = truth.get("source_bindings")
        review_preparation_sha = truth.get("review_artifact_sha256")
        if not isinstance(source_bindings, dict) or not isinstance(
            review_preparation_sha, str
        ):
            raise CampaignIntegrityError("review source bindings are malformed")
        public_source_bindings = dict(source_bindings)
        public_source_bindings["review_preparation_sha256"] = review_preparation_sha
        public_case: dict[str, object] = {
            "schema_version": RESOURCE_RELEASE_CAMPAIGN_SCHEMA_VERSION,
            "campaign_id": RESOURCE_RELEASE_CAMPAIGN_ID,
            "session_id": session["session_id"],
            "ordinal": case.ordinal,
            "case_id": case.case_id,
            "blocker_id": case.blocker_id,
            "operator_stage": record["operator_stage"],
            "operator_stage_is_reviewer_truth": False,
            "review": _public_review(truth["review"]),
            "frame": record["frame"],
            "captured_at_utc": record["captured_at_utc"],
            "capture_environment": _public_environment(record["capture_environment"]),
            "capture_policy_evidence": {
                "capture_source_backend_name": record["capture_source_backend_name"],
                "evidence_origin": record["evidence_origin"],
                "capture_count": record["capture_count"],
                "production_evaluation_count": 1,
                "capture_attempt_count": record["capture_attempt_count"],
                "prior_no_frame_failure_count": record[
                    "prior_no_frame_failure_count"
                ],
                "prior_no_frame_failure_report_sha256s": record[
                    "prior_no_frame_failure_report_sha256s"
                ],
                "prior_no_frame_failure_provenance": record[
                    "prior_no_frame_failure_provenance"
                ],
                "automatic_retry_count": record["automatic_retry_count"],
                "input_events": record["input_events"],
            },
            "source_bindings": public_source_bindings,
            "capture_report_sha256": capture_report_sha,
            "review_truth_sha256": truth_sha,
            "sanitized_artifacts": public_artifacts,
            "production": public_production,
            "release_result": release_by_id[case.case_id],
            "contains_private_full_frame": False,
            "activation_allowed": False,
            "promotion_allowed": False,
            "input_authority": False,
        }
        case_path = target_dir / "case-review.json"
        case_sha = _write_hashed_artifact(
            case_path, _canonical_json_bytes(public_case)
        )
        public_cases.append(
            {
                "ordinal": case.ordinal,
                "case_id": case.case_id,
                "case_review_path": f"cases/{_case_stem(case)}/case-review.json",
                "case_review_sha256": case_sha,
                "sanitized_raw_gzip_sha256": source_gzip_sha,
                "sanitized_preview_sha256": source_preview_sha,
            }
        )
    release_path = output_dir / "release-summary.json"
    release_sha = write_release_summary(release_path, public_release)
    manifest: dict[str, object] = {
        "schema_version": RESOURCE_RELEASE_CAMPAIGN_SCHEMA_VERSION,
        "package_id": "resource-release-review-package-v1",
        "campaign_id": RESOURCE_RELEASE_CAMPAIGN_ID,
        "campaign_version": RESOURCE_RELEASE_CAMPAIGN_VERSION,
        "configuration_id": RESOURCE_RELEASE_CONFIGURATION_ID,
        "session_id": session["session_id"],
        "exported_at_utc": _utc_text(exported),
        "repository": session["repository"],
        "profile": session["profile"],
        "capture_configuration": session["capture_configuration"],
        "completion_seal_sha256": _verify_hashed_artifact(
            session_dir / "completion-seal.json"
        )[1],
        "release_summary": {
            "path": "release-summary.json",
            "sha256": release_sha,
        },
        "cases": public_cases,
        "case_count": len(public_cases),
        "operator_labels_are_reviewer_truth": False,
        "all_cases_explicitly_privacy_reviewed": True,
        "contains_private_full_frames": False,
        "manifest_written_last": True,
        "activation_allowed": False,
        "promotion_allowed": False,
        "input_authority": False,
    }
    manifest_sha = _write_hashed_artifact(
        output_dir / "manifest.json", _canonical_json_bytes(manifest)
    )
    return {
        "package": str(output_dir),
        "manifest_sha256": manifest_sha,
        "release_summary_sha256": release_sha,
        "case_count": len(public_cases),
        "contains_private_full_frames": False,
        "activation_allowed": False,
    }


def _public_frame(value: object, payload: bytes) -> Frame:
    return _frame_from_metadata(value, payload, label="public review frame")


def _verify_opaque_fixed_ui(frame: Frame) -> None:
    if not _has_reviewable_geometry(frame):
        raise CampaignIntegrityError("sanitized public frame has unsupported geometry")
    black = b"\x00\x00\x00\xff"
    for x, y, width, height in VARROCK_EAST_IRON_FIXED_UI_REGIONS:
        for row in range(y, y + height):
            start = (row * frame.width + x) * 4
            region = frame.payload[start : start + width * 4]
            if region != black * width:
                raise CampaignIntegrityError("public review frame exposes fixed UI pixels")


def _verify_public_pass_claim(
    case: CampaignCase,
    *,
    decision: ReviewDecision,
    production: object,
    frame: Frame,
    release_result: Mapping[str, object],
) -> None:
    if release_result.get("passed") is not True:
        return
    if release_result.get("evidence_origin") != _SOURCE_OWNED_EVIDENCE_ORIGIN:
        raise CampaignIntegrityError("non-source-owned evidence claimed a public PASS")
    if not _has_reviewable_geometry(frame) or not isinstance(production, dict):
        raise CampaignIntegrityError("unsupported public geometry claimed a PASS")
    if decision.meaning is not case.review_meaning:
        raise CampaignIntegrityError("review meaning does not prove the fixed case")
    truth_states = tuple(state for _, state in decision.resource_truth)
    production_states = _production_state_vector(production)
    if production_states is None or production_states != truth_states:
        raise CampaignIntegrityError("public PASS state vector is not independently proven")
    scene = production.get("scene")
    scene_validated = isinstance(scene, dict) and scene.get("validated") is True
    if release_result.get("production_scene_validated") is not scene_validated:
        raise CampaignIntegrityError("public PASS scene result was rebound")
    if release_result.get("reviewed_state_vector") != [
        state.value for state in truth_states
    ] or release_result.get("production_state_vector") != [
        state.value for state in production_states
    ]:
        raise CampaignIntegrityError("public PASS state vectors were rebound")
    truth_definitive = [
        resource_id
        for resource_id, state in decision.resource_truth
        if state is not ResourceVisualState.UNCERTAIN
    ]
    truth_actionable = [
        resource_id
        for resource_id, state in decision.resource_truth
        if state is ResourceVisualState.AVAILABLE
    ]
    trust = production.get("trust")
    if not isinstance(trust, dict):
        raise CampaignIntegrityError("public PASS lacks production trust evidence")
    profile = load_varrock_east_iron_profile()
    expected_regions = {
        candidate.resource_id: (
            list(candidate.region)
            if truth_states[index] is ResourceVisualState.AVAILABLE
            else None
        )
        for index, candidate in enumerate(profile.candidates)
    }
    if (
        trust.get("definitive_target_ids") != truth_definitive
        or trust.get("production_actionable_target_ids") != truth_actionable
        or trust.get("production_interaction_regions") != expected_regions
    ):
        raise CampaignIntegrityError("public PASS target/region evidence is inconsistent")
    if case.review_meaning is ReviewMeaning.SUPPORTED_STARTUP:
        if not scene_validated or ResourceVisualState.UNCERTAIN in truth_states:
            raise CampaignIntegrityError("startup PASS is not a supported definitive view")
    elif case.review_meaning is ReviewMeaning.PROFILED_NODE_STATE:
        focal_id = case.focal_resource_id
        if (
            not scene_validated
            or decision.focal_resource_id != focal_id
            or decision.node_phase is not case.requested_node_phase
            or focal_id is None
            or truth_states[VARROCK_EAST_IRON_RESOURCE_IDS.index(focal_id)]
            is not case.requested_focal_state
        ):
            raise CampaignIntegrityError("node-cycle PASS is not the fixed reviewed phase")
    elif case.review_meaning is ReviewMeaning.UNSUPPORTED_LOCATION:
        if scene_validated or any(
            state is not ResourceVisualState.UNCERTAIN for state in truth_states
        ):
            raise CampaignIntegrityError("unsupported-location PASS is not fail-closed")
    elif case.review_meaning in {
        ReviewMeaning.NEIGHBORING_COPPER,
        ReviewMeaning.NEIGHBORING_TIN,
        ReviewMeaning.TERRAIN_CLUTTER,
    }:
        if decision.subject_region is None:
            raise CampaignIntegrityError("negative PASS lacks reviewed subject geometry")
        for region in expected_regions.values():
            if region is not None and _regions_overlap(
                decision.subject_region, cast(tuple[int, int, int, int], tuple(region))
            ):
                raise CampaignIntegrityError("negative subject overlaps an iron target")
        if not scene_validated and any(
            state is not ResourceVisualState.UNCERTAIN for state in truth_states
        ):
            raise CampaignIntegrityError("unsupported negative PASS is not fail-closed")
    elif case.review_meaning is ReviewMeaning.PROFILED_OBSTRUCTION:
        if decision.obstruction_target_kind == "landmark":
            landmarks = scene.get("landmarks") if isinstance(scene, dict) else None
            matched = None
            if isinstance(landmarks, list):
                matched = next(
                    (
                        item.get("matched")
                        for item in landmarks
                        if isinstance(item, dict)
                        and item.get("landmark_id") == decision.obstruction_target_id
                    ),
                    None,
                )
            if matched is not False:
                raise CampaignIntegrityError(
                    "landmark-obstruction PASS lacks a failed target landmark"
                )


def _load_verified_review_package_snapshot(
    package_dir: Path, *, expected_manifest_sha256: str
) -> _VerifiedReviewPackageSnapshot:
    """Verify and snapshot a package against an independently retained root."""

    package_dir = Path(package_dir)
    if (
        not isinstance(expected_manifest_sha256, str)
        or not _SHA256_PATTERN.fullmatch(expected_manifest_sha256)
    ):
        raise CampaignIntegrityError(
            "expected manifest SHA-256 must be 64 lowercase hexadecimal characters"
        )
    if not package_dir.is_dir() or package_dir.is_symlink():
        raise CampaignIntegrityError("review package must be a real directory")
    profile = load_varrock_east_iron_profile()
    expected_public_raw_bytes = profile.frame_width * profile.frame_height * 4
    manifest_payload, manifest_sha = _verify_hashed_artifact(
        package_dir / "manifest.json", maximum_bytes=_MAX_PUBLIC_JSON_BYTES
    )
    if manifest_sha != expected_manifest_sha256:
        raise CampaignIntegrityError(
            "review package manifest does not match independently retained SHA-256"
        )
    manifest = _strict_json_bytes(manifest_payload, label="review package manifest")
    required_manifest = {
        "schema_version",
        "package_id",
        "campaign_id",
        "campaign_version",
        "configuration_id",
        "session_id",
        "exported_at_utc",
        "repository",
        "profile",
        "capture_configuration",
        "completion_seal_sha256",
        "release_summary",
        "cases",
        "case_count",
        "operator_labels_are_reviewer_truth",
        "all_cases_explicitly_privacy_reviewed",
        "contains_private_full_frames",
        "manifest_written_last",
        "activation_allowed",
        "promotion_allowed",
        "input_authority",
    }
    if set(manifest) != required_manifest:
        raise CampaignIntegrityError("review package manifest fields changed")
    if (
        not _is_strict_int(manifest.get("schema_version"))
        or manifest.get("schema_version") != RESOURCE_RELEASE_CAMPAIGN_SCHEMA_VERSION
        or manifest.get("package_id") != "resource-release-review-package-v1"
        or manifest.get("campaign_id") != RESOURCE_RELEASE_CAMPAIGN_ID
        or manifest.get("campaign_version") != RESOURCE_RELEASE_CAMPAIGN_VERSION
        or manifest.get("configuration_id") != RESOURCE_RELEASE_CONFIGURATION_ID
        or manifest.get("case_count") != len(CAMPAIGN_PLAN)
        or manifest.get("operator_labels_are_reviewer_truth") is not False
        or manifest.get("all_cases_explicitly_privacy_reviewed") is not True
        or manifest.get("contains_private_full_frames") is not False
        or manifest.get("manifest_written_last") is not True
        or manifest.get("activation_allowed") is not False
        or manifest.get("promotion_allowed") is not False
        or manifest.get("input_authority") is not False
        or manifest.get("profile") != _profile_identity()
    ):
        raise CampaignIntegrityError("review package identity/policy changed")
    _validate_capture_configuration(manifest.get("capture_configuration"))
    exported_at = _parse_utc(manifest.get("exported_at_utc"), "exported_at_utc")
    release_meta = manifest.get("release_summary")
    if not isinstance(release_meta, dict) or set(release_meta) != {"path", "sha256"}:
        raise CampaignIntegrityError("public release-summary binding is malformed")
    if release_meta.get("path") != "release-summary.json":
        raise CampaignIntegrityError("public release-summary path changed")
    release_sha = release_meta.get("sha256")
    if not isinstance(release_sha, str):
        raise CampaignIntegrityError("public release-summary hash is malformed")
    release_payload, _ = _verify_hashed_artifact(
        package_dir / "release-summary.json",
        expected=release_sha,
        maximum_bytes=_MAX_PUBLIC_JSON_BYTES,
    )
    release = _strict_json_bytes(release_payload, label="public release summary")
    _validate_release_report(release)
    evaluated_at = _parse_utc(release.get("evaluated_at_utc"), "evaluated_at_utc")
    capture_configuration = manifest.get("capture_configuration")
    if (
        release.get("session_id") != manifest.get("session_id")
        or release.get("repository") != manifest.get("repository")
        or release.get("profile") != manifest.get("profile")
        or release.get("capture_configuration")
        != manifest.get("capture_configuration")
        or release.get("completion_seal_sha256")
        != manifest.get("completion_seal_sha256")
        or evaluated_at != exported_at
        or not isinstance(capture_configuration, dict)
        or release.get("live_resource_campaign_authorized")
        != capture_configuration.get("live_source_authorized")
    ):
        raise CampaignIntegrityError("public release summary was rebound from manifest")
    release_review_hashes = {
        cast(str, item["case_id"]): cast(str, item["review_sha256"])
        for item in cast(list[dict[str, object]], release["review_hashes"])
    }
    release_results = {
        cast(str, item["case_id"]): item
        for item in cast(list[dict[str, object]], release["case_results"])
    }
    cases = manifest.get("cases")
    if not isinstance(cases, list) or len(cases) != len(CAMPAIGN_PLAN):
        raise CampaignIntegrityError("review package case list is malformed")
    expected_files = {
        "manifest.json",
        "manifest.json.sha256",
        "release-summary.json",
        "release-summary.json.sha256",
    }
    verified_cases: list[_VerifiedCaseSnapshot] = []
    for case, case_meta in zip(CAMPAIGN_PLAN, cases, strict=True):
        if not isinstance(case_meta, dict) or set(case_meta) != {
            "ordinal",
            "case_id",
            "case_review_path",
            "case_review_sha256",
            "sanitized_raw_gzip_sha256",
            "sanitized_preview_sha256",
        }:
            raise CampaignIntegrityError("review package case manifest is malformed")
        stem = _case_stem(case)
        case_review_rel = f"cases/{stem}/case-review.json"
        if (
            not isinstance(case_meta.get("ordinal"), int)
            or isinstance(case_meta.get("ordinal"), bool)
            or case_meta.get("ordinal") != case.ordinal
            or case_meta.get("case_id") != case.case_id
            or case_meta.get("case_review_path") != case_review_rel
        ):
            raise CampaignIntegrityError("review package case order/identity changed")
        case_sha = case_meta.get("case_review_sha256")
        if not isinstance(case_sha, str):
            raise CampaignIntegrityError("public case-review hash is malformed")
        case_payload, _ = _verify_hashed_artifact(
            package_dir / case_review_rel,
            expected=case_sha,
            maximum_bytes=_MAX_PUBLIC_JSON_BYTES,
        )
        public_case = _strict_json_bytes(
            case_payload, label=f"public case review {case.case_id}"
        )
        required_case = {
            "schema_version",
            "campaign_id",
            "session_id",
            "ordinal",
            "case_id",
            "blocker_id",
            "operator_stage",
            "operator_stage_is_reviewer_truth",
            "review",
            "frame",
            "captured_at_utc",
            "capture_environment",
            "capture_policy_evidence",
            "source_bindings",
            "capture_report_sha256",
            "review_truth_sha256",
            "sanitized_artifacts",
            "production",
            "release_result",
            "contains_private_full_frame",
            "activation_allowed",
            "promotion_allowed",
            "input_authority",
        }
        if set(public_case) != required_case:
            raise CampaignIntegrityError("public case-review fields changed")
        if (
            public_case.get("schema_version") != RESOURCE_RELEASE_CAMPAIGN_SCHEMA_VERSION
            or not isinstance(public_case.get("schema_version"), int)
            or isinstance(public_case.get("schema_version"), bool)
            or public_case.get("campaign_id") != RESOURCE_RELEASE_CAMPAIGN_ID
            or public_case.get("session_id") != manifest.get("session_id")
            or not isinstance(public_case.get("ordinal"), int)
            or isinstance(public_case.get("ordinal"), bool)
            or public_case.get("ordinal") != case.ordinal
            or public_case.get("case_id") != case.case_id
            or public_case.get("blocker_id") != case.blocker_id
            or public_case.get("operator_stage") != _case_json(case)
            or public_case.get("operator_stage_is_reviewer_truth") is not False
            or public_case.get("contains_private_full_frame") is not False
            or public_case.get("activation_allowed") is not False
            or public_case.get("promotion_allowed") is not False
            or public_case.get("input_authority") is not False
            or public_case.get("release_result") != release_results.get(case.case_id)
        ):
            raise CampaignIntegrityError("public case-review identity/policy changed")
        captured_at = _parse_utc(public_case.get("captured_at_utc"), "captured_at_utc")
        review = public_case.get("review")
        public_review_fields = {
            "case_id",
            "reviewed_at_utc",
            "meaning",
            "resource_truth",
            "review_artifact_sha256",
            "privacy_review_confirmed",
            "focal_resource_id",
            "node_phase",
            "obstruction_target",
            "subject_region",
            "reviewer_role",
            "reviewer_identity_redacted",
            "free_form_notes_redacted",
        }
        if not isinstance(review, dict) or set(review) != public_review_fields:
            raise CampaignIntegrityError("public review fields/privacy changed")
        if (
            review.get("privacy_review_confirmed") is not True
            or review.get("reviewer_role")
            != "independent-reviewer-identity-redacted"
            or review.get("reviewer_identity_redacted") is not True
            or review.get("free_form_notes_redacted") is not True
        ):
            raise CampaignIntegrityError("public review privacy confirmation changed")
        private_shape = {
            key: value
            for key, value in review.items()
            if key
            not in {
                "reviewer_role",
                "reviewer_identity_redacted",
                "free_form_notes_redacted",
            }
        }
        private_shape["reviewer_id"] = "independent-reviewer-redacted"
        private_shape["notes"] = ""
        decision = review_decision_from_json(private_shape)
        if not (captured_at <= decision.reviewed_at_utc <= evaluated_at):
            raise CampaignIntegrityError("public capture/review chronology changed")
        decision_artifacts = public_case.get("sanitized_artifacts")
        if not isinstance(decision_artifacts, dict):
            raise CampaignIntegrityError("public sanitized artifact record is malformed")
        _validate_review_decision(
            decision,
            case=case,
            operator_id="operator-identity-redacted",
            sealed_at=decision.reviewed_at_utc,
            pixels_withheld=decision_artifacts.get("mode")
            == "pixels-withheld-unsupported-geometry",
        )
        review_truth_sha = public_case.get("review_truth_sha256")
        capture_report_sha = public_case.get("capture_report_sha256")
        if (
            not isinstance(review_truth_sha, str)
            or not _SHA256_PATTERN.fullmatch(review_truth_sha)
            or release_review_hashes.get(case.case_id) != review_truth_sha
            or not isinstance(capture_report_sha, str)
            or not _SHA256_PATTERN.fullmatch(capture_report_sha)
        ):
            raise CampaignIntegrityError("public review/capture hash binding changed")
        environment = public_case.get("capture_environment")
        environment_fields = {
            "backend_name",
            "title_match",
            "window_title_present",
            "window_class",
            "window_client_width",
            "window_client_height",
            "reported_dpi",
            "window_title_redacted",
            "native_window_handle_redacted",
        }
        if not isinstance(environment, dict) or set(environment) != environment_fields:
            raise CampaignIntegrityError("public environment fields/privacy changed")
        _, _, frame_width, frame_height, _ = _validated_frame_scalars(
            public_case.get("frame"), label="public review frame"
        )
        if (
            environment.get("backend_name") != _LIVE_CAPTURE_BACKEND_NAME
            or environment.get("title_match") != _LIVE_CAPTURE_TITLE_MATCH
            or environment.get("window_title_present") is not True
            or not isinstance(environment.get("window_class"), str)
            or not cast(str, environment["window_class"]).strip()
            or not _is_strict_int(environment.get("window_client_width"))
            or not _is_strict_int(environment.get("window_client_height"))
            or cast(int, environment["window_client_width"]) <= 0
            or cast(int, environment["window_client_height"]) <= 0
            or environment.get("window_client_width") != frame_width
            or environment.get("window_client_height") != frame_height
            or environment.get("window_title_redacted") is not True
            or environment.get("native_window_handle_redacted") is not True
            or (
                environment.get("reported_dpi") is not None
                and (
                    not isinstance(environment.get("reported_dpi"), int)
                    or isinstance(environment.get("reported_dpi"), bool)
                    or cast(int, environment["reported_dpi"]) <= 0
                )
            )
        ):
            raise CampaignIntegrityError("public environment provenance is malformed")
        policy = public_case.get("capture_policy_evidence")
        policy_fields = {
            "capture_source_backend_name",
            "evidence_origin",
            "capture_count",
            "production_evaluation_count",
            "capture_attempt_count",
            "prior_no_frame_failure_count",
            "prior_no_frame_failure_report_sha256s",
            "prior_no_frame_failure_provenance",
            "automatic_retry_count",
            "input_events",
        }
        if not isinstance(policy, dict) or set(policy) != policy_fields:
            raise CampaignIntegrityError("public capture policy evidence is malformed")
        prior_count = policy.get("prior_no_frame_failure_count")
        attempt_count = policy.get("capture_attempt_count")
        prior_hashes = policy.get("prior_no_frame_failure_report_sha256s")
        prior_provenance = policy.get("prior_no_frame_failure_provenance")
        capture_count = policy.get("capture_count")
        evaluation_count = policy.get("production_evaluation_count")
        automatic_retry_count = policy.get("automatic_retry_count")
        if (
            policy.get("capture_source_backend_name") != _LIVE_CAPTURE_BACKEND_NAME
            or policy.get("evidence_origin")
            not in {_SOURCE_OWNED_EVIDENCE_ORIGIN, _INJECTED_EVIDENCE_ORIGIN}
            or not isinstance(capture_count, int)
            or isinstance(capture_count, bool)
            or capture_count != 1
            or not isinstance(evaluation_count, int)
            or isinstance(evaluation_count, bool)
            or evaluation_count != 1
            or not isinstance(prior_count, int)
            or isinstance(prior_count, bool)
            or prior_count < 0
            or not isinstance(attempt_count, int)
            or isinstance(attempt_count, bool)
            or attempt_count != prior_count + 1
            or not isinstance(prior_hashes, list)
            or len(prior_hashes) != prior_count
            or any(
                not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value)
                for value in prior_hashes
            )
            or not isinstance(prior_provenance, list)
            or len(prior_provenance) != prior_count
            or any(
                not isinstance(item, dict)
                or set(item) != {
                    "report_sha256",
                    "capture_source_backend_name",
                    "evidence_origin",
                }
                or item.get("report_sha256") != prior_hashes[index]
                or not _valid_capture_origin_pair(
                    item.get("capture_source_backend_name"),
                    item.get("evidence_origin"),
                )
                for index, item in enumerate(prior_provenance)
            )
            or not isinstance(automatic_retry_count, int)
            or isinstance(automatic_retry_count, bool)
            or automatic_retry_count != 0
            or policy.get("input_events") != []
        ):
            raise CampaignIntegrityError("public no-retry/input provenance changed")
        bindings = public_case.get("source_bindings")
        if not isinstance(bindings, dict) or set(bindings) != {
            "completion_seal_sha256",
            "capture_report_sha256",
            "private_raw_sha256",
            "evidence_origin",
            "review_preparation_sha256",
        }:
            raise CampaignIntegrityError("public source bindings are malformed")
        private_raw_sha = bindings.get("private_raw_sha256")
        if (
            bindings.get("completion_seal_sha256")
            != manifest.get("completion_seal_sha256")
            or bindings.get("capture_report_sha256") != capture_report_sha
            or bindings.get("evidence_origin") != policy.get("evidence_origin")
            or bindings.get("review_preparation_sha256")
            != decision.review_artifact_sha256
            or not isinstance(private_raw_sha, str)
            or not _SHA256_PATTERN.fullmatch(private_raw_sha)
        ):
            raise CampaignIntegrityError("public source bindings were rebound")
        release_result_value = public_case.get("release_result")
        if not isinstance(release_result_value, dict) or (
            release_result_value.get("evidence_origin")
            != policy.get("evidence_origin")
        ):
            raise CampaignIntegrityError("public case evidence origins disagree")
        if (
            release_result_value.get("reported_dpi")
            != environment.get("reported_dpi")
            or release_result_value.get("required_reported_dpi")
            != _REQUIRED_REPORTED_DPI
        ):
            raise CampaignIntegrityError("public case DPI evidence was rebound")
        if release_result_value.get("passed") is True and any(
            not isinstance(item, dict)
            or item.get("evidence_origin") != _SOURCE_OWNED_EVIDENCE_ORIGIN
            or item.get("capture_source_backend_name") != _LIVE_CAPTURE_BACKEND_NAME
            for item in cast(list[object], prior_provenance)
        ):
            raise CampaignIntegrityError(
                "public PASS contains mixed prior capture origins"
            )
        sanitized_raw_gzip_path: str | None = None
        sanitized_raw_gzip_sha256: str | None = None
        sanitized_raw_gzip_bytes: bytes | None = None
        sanitized_decompressed_sha256: str | None = None
        artifacts = decision_artifacts
        expected_files.update({case_review_rel, f"{case_review_rel}.sha256"})
        mode = artifacts.get("mode")
        if mode == "sanitized-frame":
            raw_meta = artifacts.get("raw_gzip")
            preview_meta = artifacts.get("preview")
            if set(artifacts) != {
                "mode",
                "raw_gzip",
                "preview",
                "mask",
                "full_geometry_preserved",
            } or not isinstance(raw_meta, dict) or not isinstance(preview_meta, dict):
                raise CampaignIntegrityError("public sanitized files are malformed")
            if (
                set(raw_meta) != {"path", "sha256", "decompressed_sha256"}
                or set(preview_meta) != {"path", "sha256"}
                or artifacts.get("mask") != RESOURCE_RELEASE_PRIVACY_MASK_ID
                or artifacts.get("full_geometry_preserved") is not True
            ):
                raise CampaignIntegrityError("public sanitized metadata/policy changed")
            raw_rel = f"cases/{stem}/sanitized-frame.raw.gz"
            preview_rel = f"cases/{stem}/sanitized-preview.bmp"
            if raw_meta.get("path") != raw_rel or preview_meta.get("path") != preview_rel:
                raise CampaignIntegrityError("public sanitized path changed")
            raw_sha = raw_meta.get("sha256")
            preview_sha = preview_meta.get("sha256")
            if (
                not isinstance(raw_sha, str)
                or not isinstance(preview_sha, str)
                or not _SHA256_PATTERN.fullmatch(raw_sha)
                or not _SHA256_PATTERN.fullmatch(preview_sha)
                or not isinstance(raw_meta.get("decompressed_sha256"), str)
                or not _SHA256_PATTERN.fullmatch(
                    cast(str, raw_meta["decompressed_sha256"])
                )
                or case_meta.get("sanitized_raw_gzip_sha256") != raw_sha
                or case_meta.get("sanitized_preview_sha256") != preview_sha
            ):
                raise CampaignIntegrityError("public sanitized hash binding changed")
            compressed, _ = _verify_hashed_artifact(
                package_dir / raw_rel,
                expected=raw_sha,
                maximum_bytes=expected_public_raw_bytes + 4096,
            )
            frame_meta = public_case.get("frame")
            _, _, width, height, pixel_format = _validated_frame_scalars(
                frame_meta, label="public review frame"
            )
            if (
                width != profile.frame_width
                or height != profile.frame_height
                or pixel_format is not PixelFormat.BGRA8888
            ):
                raise CampaignIntegrityError("sanitized public frame geometry changed")
            pixels = _bounded_gzip_decompress(
                compressed,
                expected_size=expected_public_raw_bytes,
                label=f"public review {case.case_id}",
            )
            if compressed != _deterministic_gzip(pixels):
                raise CampaignIntegrityError(
                    "public sanitized frame does not use canonical gzip encoding"
                )
            if raw_meta.get("decompressed_sha256") != _sha256(pixels):
                raise CampaignIntegrityError("public decompressed hash changed")
            sanitized_raw_gzip_path = raw_rel
            sanitized_raw_gzip_sha256 = raw_sha
            sanitized_raw_gzip_bytes = compressed
            sanitized_decompressed_sha256 = cast(
                str, raw_meta["decompressed_sha256"]
            )
            frame = _public_frame(frame_meta, pixels)
            _verify_opaque_fixed_ui(frame)
            preview_payload, _ = _verify_hashed_artifact(
                package_dir / preview_rel,
                expected=preview_sha,
                maximum_bytes=54 + expected_public_raw_bytes,
            )
            if preview_payload != _bmp_payload(frame):
                raise CampaignIntegrityError("public sanitized preview changed")
            try:
                sanitized_production: object = _production_json(frame)
            except Exception as exc:
                sanitized_production = _detector_error(exc)
            if not _production_equivalent(
                public_case.get("production"),
                _public_production(sanitized_production),
            ):
                raise CampaignIntegrityError(
                    "public sanitized production is not an exact public replay"
                )
            release_result = cast(dict[str, object], public_case["release_result"])
            if release_result.get("replay_regression_candidate") != {
                "path": raw_rel,
                "sha256": raw_sha,
            }:
                raise CampaignIntegrityError("public replay candidate path/hash changed")
            _verify_public_pass_claim(
                case,
                decision=decision,
                production=public_case.get("production"),
                frame=frame,
                release_result=release_result,
            )
            expected_files.update(
                {
                    raw_rel,
                    f"{raw_rel}.sha256",
                    preview_rel,
                    f"{preview_rel}.sha256",
                }
            )
        elif mode == "pixels-withheld-unsupported-geometry":
            if set(artifacts) != {
                "mode",
                "raw_gzip",
                "preview",
                "mask",
                "full_geometry_preserved",
                "pixels_withheld_reason",
            }:
                raise CampaignIntegrityError("withheld-pixel metadata fields changed")
            frame_meta = public_case.get("frame")
            _, _, width, height, pixel_format = _validated_frame_scalars(
                frame_meta, label="withheld public frame"
            )
            if (
                artifacts.get("raw_gzip") is not None
                or artifacts.get("preview") is not None
                or artifacts.get("mask") is not None
                or artifacts.get("full_geometry_preserved") is not False
                or artifacts.get("pixels_withheld_reason")
                != "unsupported_geometry_or_pixel_format"
                or (
                    width == profile.frame_width
                    and height == profile.frame_height
                    and pixel_format is PixelFormat.BGRA8888
                )
                or case_meta.get("sanitized_raw_gzip_sha256") is not None
                or case_meta.get("sanitized_preview_sha256") is not None
                or cast(dict[str, object], public_case["release_result"]).get(
                    "replay_regression_candidate"
                )
                is not None
            ):
                raise CampaignIntegrityError("withheld-pixel public case exposed pixels")
            withheld_production = public_case.get("production")
            if (
                not isinstance(withheld_production, dict)
                or set(withheld_production) != {
                    "status",
                    "production_authority",
                    "private_diagnostics_redacted",
                    "input_authority",
                }
                or withheld_production.get("status")
                != "pixels-withheld-unsupported-geometry"
                or withheld_production.get("production_authority")
                != _withheld_public_authority()
                or withheld_production.get("private_diagnostics_redacted") is not True
                or withheld_production.get("input_authority") is not False
                or cast(dict[str, object], public_case["release_result"]).get("passed")
                is not False
                or "wrong_frame_geometry_or_pixel_format"
                not in cast(
                    list[str],
                    cast(dict[str, object], public_case["release_result"])["reasons"],
                )
            ):
                raise CampaignIntegrityError("withheld-pixel case was promoted or malformed")
        else:
            raise CampaignIntegrityError("public privacy artifact mode is unknown")
        verified_cases.append(
            _VerifiedCaseSnapshot(
                ordinal=case.ordinal,
                case_id=case.case_id,
                case_review_sha256=case_sha,
                case_review_json=case_payload,
                sanitized_raw_gzip_path=sanitized_raw_gzip_path,
                sanitized_raw_gzip_sha256=sanitized_raw_gzip_sha256,
                sanitized_raw_gzip_bytes=sanitized_raw_gzip_bytes,
                sanitized_decompressed_sha256=sanitized_decompressed_sha256,
            )
        )
    actual_files: set[str] = set()
    for path in package_dir.rglob("*"):
        if path.is_symlink():
            raise CampaignIntegrityError("review package cannot contain symbolic links")
        if path.is_file():
            actual_files.add(path.relative_to(package_dir).as_posix())
    if actual_files != expected_files:
        raise CampaignIntegrityError(
            f"review package contains missing/foreign files: "
            f"missing={sorted(expected_files - actual_files)}, "
            f"foreign={sorted(actual_files - expected_files)}"
        )
    return _VerifiedReviewPackageSnapshot(
        package_dir=package_dir,
        manifest_sha256=manifest_sha,
        release_summary_sha256=release_sha,
        manifest_json=manifest_payload,
        release_summary_json=release_payload,
        cases=tuple(verified_cases),
    )


def verify_review_package(
    package_dir: Path, *, expected_manifest_sha256: str
) -> dict[str, object]:
    """Verify a package against an independently retained manifest root hash."""

    snapshot = _load_verified_review_package_snapshot(
        package_dir,
        expected_manifest_sha256=expected_manifest_sha256,
    )
    return {
        "package": str(snapshot.package_dir),
        "manifest_sha256": snapshot.manifest_sha256,
        "release_summary_sha256": snapshot.release_summary_sha256,
        "case_count": len(snapshot.cases),
        "contains_private_full_frames": False,
        "activation_allowed": False,
        "verified": True,
    }


def _ordered_unique(values: Sequence[object]) -> list[object]:
    unique: list[object] = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return unique


def _followup_inputs_from_verified_snapshot(
    snapshot: _VerifiedReviewPackageSnapshot,
) -> dict[str, object]:
    """Project immutable verified bytes into nonauthoritative review inputs."""

    manifest = _strict_json_bytes(
        snapshot.manifest_json,
        label="verified follow-up source manifest",
    )
    release = _strict_json_bytes(
        snapshot.release_summary_json,
        label="verified follow-up release summary",
    )
    manifest_cases = cast(list[dict[str, object]], manifest["cases"])
    public_cases = tuple(
        _strict_json_bytes(
            case_snapshot.case_review_json,
            label=f"verified follow-up case {case_snapshot.case_id}",
        )
        for case_snapshot in snapshot.cases
    )
    case_bindings: list[dict[str, object]] = []
    failure_candidates: list[dict[str, object]] = []
    nonrelease_failures: list[dict[str, object]] = []
    retained_failure_case_ids: list[str] = []
    dpi_by_case: list[dict[str, object]] = []
    frame_matches: list[bool] = []
    source_owned_matches: list[bool] = []
    backends: list[object] = []
    origins: list[object] = []
    window_classes: list[object] = []
    client_geometries: list[tuple[int, int]] = []
    observed_dpis: list[object] = []
    profile = cast(dict[str, object], manifest["profile"])
    required_width = cast(int, profile["frame_width"])
    required_height = cast(int, profile["frame_height"])
    required_pixel_format = cast(str, profile["pixel_format"])
    required_configuration = cast(
        dict[str, object], manifest["capture_configuration"]
    )
    required_dpi = cast(int, required_configuration["required_reported_dpi"])

    for case, case_snapshot, case_meta, public_case in zip(
        CAMPAIGN_PLAN,
        snapshot.cases,
        manifest_cases,
        public_cases,
        strict=True,
    ):
        review = cast(dict[str, object], public_case["review"])
        source_bindings = cast(dict[str, object], public_case["source_bindings"])
        policy = cast(dict[str, object], public_case["capture_policy_evidence"])
        environment = cast(dict[str, object], public_case["capture_environment"])
        frame = cast(dict[str, object], public_case["frame"])
        artifacts = cast(dict[str, object], public_case["sanitized_artifacts"])
        release_result = cast(dict[str, object], public_case["release_result"])
        if artifacts.get("mode") == "pixels-withheld-unsupported-geometry":
            # The public package deliberately replaces private production with
            # one canonical fail-closed authority projection.  Its follow-up
            # must not carry a scene verdict or state vector that cannot be
            # replayed from public pixels.
            release_result = {
                **release_result,
                "production_scene_validated": False,
                "production_state_vector": None,
            }
        evidence_origin = policy["evidence_origin"]
        reported_dpi = environment["reported_dpi"]
        frame_width = cast(int, frame["width"])
        frame_height = cast(int, frame["height"])
        pixel_format = cast(str, frame["pixel_format"])
        client_width = cast(int, environment["window_client_width"])
        client_height = cast(int, environment["window_client_height"])
        source_owned = evidence_origin == _SOURCE_OWNED_EVIDENCE_ORIGIN
        frame_matches_requirement = (
            frame_width == required_width
            and frame_height == required_height
            and pixel_format == required_pixel_format
        )
        source_owned_matches.append(source_owned)
        frame_matches.append(frame_matches_requirement)
        backends.append(environment["backend_name"])
        origins.append(evidence_origin)
        window_classes.append(environment["window_class"])
        client_geometries.append((client_width, client_height))
        observed_dpis.append(reported_dpi)
        dpi_by_case.append(
            {
                "case_id": case.case_id,
                "reported_dpi": reported_dpi,
                "matches_requirement": reported_dpi == required_dpi,
            }
        )
        reviewer_truth = {
            "reviewed_at_utc": review["reviewed_at_utc"],
            "meaning": review["meaning"],
            "resource_truth": review["resource_truth"],
            "privacy_review_confirmed": review["privacy_review_confirmed"],
            "focal_resource_id": review["focal_resource_id"],
            "node_phase": review["node_phase"],
            "obstruction_target": review["obstruction_target"],
            "subject_region": review["subject_region"],
        }
        hashes = {
            "case_review_sha256": case_snapshot.case_review_sha256,
            "capture_report_sha256": public_case["capture_report_sha256"],
            "review_preparation_sha256": source_bindings[
                "review_preparation_sha256"
            ],
            "review_truth_sha256": public_case["review_truth_sha256"],
            "private_raw_sha256": source_bindings["private_raw_sha256"],
            "sanitized_raw_gzip_sha256": case_meta[
                "sanitized_raw_gzip_sha256"
            ],
            "sanitized_preview_sha256": case_meta[
                "sanitized_preview_sha256"
            ],
        }
        case_bindings.append(
            {
                "ordinal": case.ordinal,
                "case_id": case.case_id,
                "blocker_id": case.blocker_id,
                "hashes": hashes,
                "capture_origin": {
                    "capture_source_backend_name": policy[
                        "capture_source_backend_name"
                    ],
                    "evidence_origin": evidence_origin,
                    "capture_attempt_count": policy["capture_attempt_count"],
                    "prior_no_frame_failure_provenance": policy[
                        "prior_no_frame_failure_provenance"
                    ],
                },
                "capture": {
                    "captured_at_utc": public_case["captured_at_utc"],
                    "frame": frame,
                    "environment": environment,
                },
                "reviewer_truth": reviewer_truth,
                "release_result": release_result,
                "production_snapshot": public_case["production"],
                "sanitized_artifacts": artifacts,
            }
        )
        if (
            release_result["passed"] is False
            and release_result["permanent_evidence_required"] is True
        ):
            retained_failure_case_ids.append(case.case_id)
            has_replay_pixels = artifacts["mode"] == "sanitized-frame"
            raw_binding: object = None
            if has_replay_pixels:
                raw_binding = artifacts["raw_gzip"]
            if source_owned:
                failure_candidates.append(
                    {
                        "ordinal": case.ordinal,
                        "case_id": case.case_id,
                        "blocker_id": case.blocker_id,
                        "target_dataset_id": _FOLLOWUP_REGRESSION_DATASET_ID,
                        "disposition": (
                            "REPLAY_CANDIDATE"
                            if has_replay_pixels
                            else "METADATA_ONLY_NO_PIXELS"
                        ),
                        "replay_candidate": has_replay_pixels,
                        "source_owned_release_evidence": True,
                        "case_review_sha256": case_snapshot.case_review_sha256,
                        "reviewer_truth": reviewer_truth,
                        "release_reasons": release_result["reasons"],
                        "sanitized_raw_gzip": raw_binding,
                        "promotion_complete": False,
                        "policy_change_allowed_from_failure": False,
                    }
                )
            else:
                nonrelease_failures.append(
                    {
                        "ordinal": case.ordinal,
                        "case_id": case.case_id,
                        "blocker_id": case.blocker_id,
                        "disposition": (
                            "NON_RELEASE_TEST_EVIDENCE"
                            if has_replay_pixels
                            else "NON_RELEASE_TEST_EVIDENCE_NO_PIXELS"
                        ),
                        "source_owned_release_evidence": False,
                        "case_review_sha256": case_snapshot.case_review_sha256,
                        "release_reasons": release_result["reasons"],
                        "promotion_complete": False,
                        "policy_change_allowed_from_failure": False,
                    }
                )

    c1_blockers = [
        blocker
        for blocker in cast(list[dict[str, object]], release["blockers"])
        if blocker["blocker_id"] in _RESOURCE_BLOCKER_ORDER
    ]
    categories = cast(dict[str, object], release["release_gate_categories"])
    c1_category = cast(dict[str, object], categories["c1_fresh_empirical_evidence"])
    c2_category = cast(
        dict[str, object], categories["c2_evidence_contingent_source_review"]
    )
    unresolved_external_inputs = [
        "external B release-evidence-boundary acceptance",
        "independent exact client/renderer/profile/envelope review",
        "source-owned constrained-v1 resource release/promotion record",
    ]
    if retained_failure_case_ids:
        unresolved_external_inputs.insert(
            2, "retained-failure permanent replay promotion"
        )
    distinct_window_classes = sorted(
        cast(list[str], _ordered_unique(window_classes))
    )
    distinct_backends = sorted(cast(list[str], _ordered_unique(backends)))
    distinct_origins = sorted(cast(list[str], _ordered_unique(origins)))
    distinct_geometries = sorted(set(client_geometries))
    return {
        "schema_version": 1,
        "inputs_id": _FOLLOWUP_ARTIFACT_ID,
        "configuration_id": _FOLLOWUP_CONFIGURATION_ID,
        "source_snapshot": {
            "package_id": manifest["package_id"],
            "manifest_sha256": snapshot.manifest_sha256,
            "release_summary_sha256": snapshot.release_summary_sha256,
            "campaign_id": manifest["campaign_id"],
            "campaign_version": manifest["campaign_version"],
            "configuration_id": manifest["configuration_id"],
            "session_id": manifest["session_id"],
            "exported_at_utc": manifest["exported_at_utc"],
            "completion_seal_sha256": manifest["completion_seal_sha256"],
            "repository": manifest["repository"],
            "profile": profile,
            "capture_configuration": required_configuration,
        },
        "verification": {
            "verified": True,
            "expected_manifest_sha256_matched": True,
            "case_count": len(case_bindings),
            "operator_labels_included": False,
            "operator_labels_are_reviewer_truth": False,
            "all_cases_explicitly_privacy_reviewed": True,
            "contains_private_full_frames": False,
        },
        "case_bindings": case_bindings,
        "c1_result": {
            "status": c1_category["status"],
            "blockers": c1_blockers,
        },
        "failure_promotion_inputs": {
            "status": (
                "BLOCKED_NON_RELEASE_EVIDENCE"
                if nonrelease_failures
                else "PENDING_EXTERNAL"
                if failure_candidates
                else "NOT_REQUIRED"
            ),
            "target_dataset_id": _FOLLOWUP_REGRESSION_DATASET_ID,
            "candidate_count": len(failure_candidates),
            "candidates": failure_candidates,
            "nonrelease_evidence_count": len(nonrelease_failures),
            "nonrelease_evidence": nonrelease_failures,
            "promotion_complete": False,
        },
        "c2_envelope_review_inputs": {
            "input_status": "verified-inputs-only-independent-review-required",
            "required_reported_dpi": required_dpi,
            "reported_dpi_by_case": dpi_by_case,
            "observed_reported_dpis": _ordered_unique(observed_dpis),
            "all_cases_match_required_dpi": all(
                item["matches_requirement"] is True for item in dpi_by_case
            ),
            "required_frame": {
                "width": required_width,
                "height": required_height,
                "pixel_format": required_pixel_format,
            },
            "all_cases_match_required_frame": all(frame_matches),
            "observed_capture_backends": distinct_backends,
            "observed_evidence_origins": distinct_origins,
            "observed_window_classes": distinct_window_classes,
            "observed_client_geometries": [
                {"width": width, "height": height}
                for width, height in distinct_geometries
            ],
            "window_class_consistent": len(distinct_window_classes) == 1,
            "all_cases_source_owned": all(source_owned_matches),
            "reported_release_gate_categories": categories,
            "reported_c2_category": c2_category,
            "retained_failure_case_ids": retained_failure_case_ids,
            "source_owned_failure_case_ids": [
                item["case_id"] for item in failure_candidates
            ],
            "nonrelease_failure_case_ids": [
                item["case_id"] for item in nonrelease_failures
            ],
            "renderer_identity": {
                "observed": False,
                "status": "NOT_OBSERVED_BY_CAPTURE_BACKEND",
                "requires_external_review": True,
            },
            "unresolved_external_inputs": unresolved_external_inputs,
            "envelope_approved": False,
        },
        "authority": {
            "approval_authority": False,
            "release_eligible": False,
            "activation_allowed": False,
            "promotion_allowed": False,
            "input_authority": False,
        },
    }


def prepare_release_followup_inputs(
    package_dir: Path,
    output_path: Path,
    *,
    expected_manifest_sha256: str,
) -> dict[str, object]:
    """Write deterministic nonactivating replay-promotion and C2 review inputs."""

    snapshot = _load_verified_review_package_snapshot(
        package_dir,
        expected_manifest_sha256=expected_manifest_sha256,
    )
    inputs = _followup_inputs_from_verified_snapshot(snapshot)
    package_path = snapshot.package_dir.resolve(strict=True)
    output = Path(output_path).resolve(strict=False)
    sidecar = _artifact_sidecar(output).resolve(strict=False)
    if any(
        candidate == package_path
        or package_path in candidate.parents
        or candidate in package_path.parents
        for candidate in (output, sidecar)
    ):
        raise CampaignError(
            "follow-up output and sidecar must be outside the verified package"
        )
    digest = _write_hashed_artifact(
        output,
        _canonical_json_bytes(inputs),
    )
    failure_promotion = cast(dict[str, object], inputs["failure_promotion_inputs"])
    return {
        "output": str(output),
        "sha256": digest,
        "source_manifest_sha256": snapshot.manifest_sha256,
        "case_count": len(snapshot.cases),
        "failure_candidate_count": failure_promotion["candidate_count"],
        "release_eligible": False,
        "activation_allowed": False,
    }


def _followup_review_decision(
    case: CampaignCase,
    reviewer_truth: Mapping[str, object],
    hashes: Mapping[str, object],
    artifacts: Mapping[str, object],
) -> ReviewDecision:
    obstruction = reviewer_truth.get("obstruction_target")
    decision_json: dict[str, object] = {
        "case_id": case.case_id,
        "reviewer_id": "independent-reviewer-redacted",
        "reviewed_at_utc": reviewer_truth.get("reviewed_at_utc"),
        "meaning": reviewer_truth.get("meaning"),
        "resource_truth": reviewer_truth.get("resource_truth"),
        "review_artifact_sha256": hashes.get("review_preparation_sha256"),
        "privacy_review_confirmed": reviewer_truth.get(
            "privacy_review_confirmed"
        ),
        "focal_resource_id": reviewer_truth.get("focal_resource_id"),
        "node_phase": reviewer_truth.get("node_phase"),
        "obstruction_target": obstruction,
        "subject_region": reviewer_truth.get("subject_region"),
        "notes": "",
    }
    try:
        decision = review_decision_from_json(decision_json)
        _validate_review_decision(
            decision,
            case=case,
            operator_id="operator-identity-redacted",
            sealed_at=decision.reviewed_at_utc,
            pixels_withheld=artifacts.get("mode")
            == "pixels-withheld-unsupported-geometry",
        )
    except CampaignError as exc:
        raise CampaignIntegrityError(
            f"follow-up reviewer truth is invalid: {exc}"
        ) from exc
    expected_truth = {
        "reviewed_at_utc": _utc_text(decision.reviewed_at_utc),
        "meaning": decision.meaning.value,
        "resource_truth": [
            {"resource_id": resource_id, "state": state.value}
            for resource_id, state in decision.resource_truth
        ],
        "privacy_review_confirmed": decision.privacy_review_confirmed,
        "focal_resource_id": decision.focal_resource_id,
        "node_phase": (
            None if decision.node_phase is None else decision.node_phase.value
        ),
        "obstruction_target": (
            None
            if decision.obstruction_target_kind is None
            else {
                "kind": decision.obstruction_target_kind,
                "target_id": decision.obstruction_target_id,
            }
        ),
        "subject_region": (
            None
            if decision.subject_region is None
            else list(decision.subject_region)
        ),
    }
    if dict(reviewer_truth) != expected_truth:
        raise CampaignIntegrityError("follow-up reviewer truth projection changed")
    return decision


def _followup_finite_confidence(value: object) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0.0 <= value <= 1.0
    )


def _validate_followup_scene(value: object) -> bool:
    fields = {
        "validated",
        "reason",
        "matched_count",
        "required_quorum",
        "matched_zones",
        "required_zones",
        "landmarks",
        "authority",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise CampaignIntegrityError("follow-up production scene fields changed")
    profile = load_varrock_east_iron_profile()
    validated = value.get("validated")
    matched_count = value.get("matched_count")
    matched_zones = value.get("matched_zones")
    allowed_zones = {
        landmark.macro_zone.value for landmark in profile.scene_landmarks
    }
    if (
        not isinstance(validated, bool)
        or not isinstance(value.get("reason"), str)
        or not cast(str, value["reason"]).strip()
        or not _is_strict_int(matched_count)
        or not 0 <= cast(int, matched_count) <= len(profile.scene_landmarks)
        or value.get("required_quorum") != profile.minimum_landmark_quorum
        or not isinstance(matched_zones, list)
        or len(matched_zones) != len(set(cast(list[object], matched_zones)))
        or any(zone not in allowed_zones for zone in matched_zones)
        or value.get("required_zones") != profile.minimum_landmark_zones
        or value.get("authority")
        != "read-only-summary-never-overrides-production"
    ):
        raise CampaignIntegrityError("follow-up production scene policy changed")
    if validated and (
        cast(int, matched_count) < profile.minimum_landmark_quorum
        or len(matched_zones) < profile.minimum_landmark_zones
    ):
        raise CampaignIntegrityError("follow-up production scene verdict is incoherent")
    landmarks = value.get("landmarks")
    if not isinstance(landmarks, list) or len(landmarks) != len(
        profile.scene_landmarks
    ):
        raise CampaignIntegrityError("follow-up production landmarks changed")
    derived_zones: set[str] = set()
    derived_count = 0
    for expected, raw in zip(profile.scene_landmarks, landmarks, strict=True):
        if (
            not isinstance(raw, dict)
            or set(raw)
            != {"landmark_id", "zone", "distance", "threshold", "matched"}
            or raw.get("landmark_id") != expected.landmark_id
            or raw.get("zone") != expected.macro_zone.value
            or not isinstance(raw.get("distance"), int | float)
            or isinstance(raw.get("distance"), bool)
            or not math.isfinite(cast(float, raw["distance"]))
            or cast(float, raw["distance"]) < 0.0
            or raw.get("threshold") != expected.maximum_distance
            or not isinstance(raw.get("matched"), bool)
            or raw.get("matched")
            is not (cast(float, raw["distance"]) <= expected.maximum_distance)
        ):
            raise CampaignIntegrityError(
                "follow-up production landmark evidence changed"
            )
        if raw["matched"] is True:
            derived_count += 1
            derived_zones.add(expected.macro_zone.value)
    derived_validated = (
        derived_count >= profile.minimum_landmark_quorum
        and len(derived_zones) >= profile.minimum_landmark_zones
    )
    if (
        matched_count != derived_count
        or matched_zones != sorted(derived_zones)
        or validated is not derived_validated
    ):
        raise CampaignIntegrityError(
            "follow-up production scene summary contradicts landmarks"
        )
    return validated


def _validate_followup_observations(
    value: object, *, capture_frame: Mapping[str, object]
) -> None:
    if not isinstance(value, list):
        raise CampaignIntegrityError("follow-up production observations changed")
    expected_frame = {
        "frame_id": capture_frame["frame_id"],
        "captured_monotonic_s": capture_frame["captured_monotonic_s"],
        "width": capture_frame["width"],
        "height": capture_frame["height"],
    }
    for item in value:
        if (
            not isinstance(item, dict)
            or set(item)
            != {"kind", "detector_version", "frame", "confidence", "evidence"}
            or not isinstance(item.get("kind"), str)
            or not cast(str, item["kind"]).strip()
            or item.get("detector_version")
            != VARROCK_EAST_IRON_DETECTOR_VERSION
            or item.get("frame") != expected_frame
            or not _followup_finite_confidence(item.get("confidence"))
            or not isinstance(item.get("evidence"), dict)
        ):
            raise CampaignIntegrityError(
                "follow-up production observation evidence changed"
            )


def _validate_followup_production_snapshot(
    case: CampaignCase,
    production: object,
    *,
    capture_frame: Mapping[str, object],
    decision: ReviewDecision,
    release_result: Mapping[str, object],
    artifacts: Mapping[str, object],
) -> None:
    if artifacts.get("mode") == "pixels-withheld-unsupported-geometry":
        if production != _public_withheld_production(None):
            raise CampaignIntegrityError(
                "withheld follow-up production authority changed"
            )
        if (
            release_result.get("production_scene_validated") is not False
            or release_result.get("production_state_vector") is not None
        ):
            raise CampaignIntegrityError(
                "withheld follow-up production result was rebound"
            )
        return
    if not isinstance(production, dict):
        raise CampaignIntegrityError("follow-up production snapshot is malformed")
    common_fields = {
        "status",
        "detector_id",
        "detector_version",
        "observations",
        "trust",
        "scene",
        "passive_campaign_authorized_target_ids",
        "stop_required",
        "input_authority",
    }
    status = production.get("status")
    expected_fields = (
        common_fields | {"error_type", "error"}
        if status == "detector-error"
        else common_fields
    )
    if (
        set(production) != expected_fields
        or status not in {"completed", "detector-error"}
        or production.get("detector_id") != VARROCK_EAST_IRON_DETECTOR_ID
        or production.get("detector_version")
        != VARROCK_EAST_IRON_DETECTOR_VERSION
        or production.get("passive_campaign_authorized_target_ids") != []
        or production.get("input_authority") is not False
    ):
        raise CampaignIntegrityError("follow-up production identity/authority changed")
    if status == "detector-error":
        expected = _detector_error(RuntimeError("redacted"))
        expected["error_type"] = production.get("error_type")
        expected["error"] = "private runtime detail redacted"
        if (
            not isinstance(production.get("error_type"), str)
            or not cast(str, production["error_type"]).strip()
            or production != expected
            or release_result.get("production_scene_validated") is not False
            or release_result.get("production_state_vector") is not None
        ):
            raise CampaignIntegrityError(
                "follow-up detector-error production changed authority"
            )
        return
    _validate_followup_observations(
        production.get("observations"), capture_frame=capture_frame
    )
    scene_validated = _validate_followup_scene(production.get("scene"))
    trust = production.get("trust")
    trust_fields = {
        "accepted",
        "reason",
        "frame",
        "resources",
        "definitive_target_ids",
        "production_actionable_target_ids",
        "production_interaction_regions",
    }
    if (
        not isinstance(trust, dict)
        or set(trust) != trust_fields
        or not isinstance(trust.get("accepted"), bool)
        or not isinstance(trust.get("reason"), str)
        or not cast(str, trust["reason"]).strip()
    ):
        raise CampaignIntegrityError("follow-up production trust fields changed")
    accepted = cast(bool, trust["accepted"])
    resources = trust.get("resources")
    expected_frame = dict(capture_frame)
    if not accepted:
        if (
            trust.get("frame") is not None
            or resources != []
            or trust.get("definitive_target_ids") != []
            or trust.get("production_actionable_target_ids") != []
            or trust.get("production_interaction_regions") != {}
            or production.get("stop_required") is not True
        ):
            raise CampaignIntegrityError(
                "rejected follow-up production exposed resource authority"
            )
        production_states: list[str] | None = None
    else:
        if (
            trust.get("reason") != "trusted_complete_production_ensemble"
            or trust.get("frame") != expected_frame
            or not isinstance(resources, list)
            or len(resources) != len(VARROCK_EAST_IRON_RESOURCE_IDS)
        ):
            raise CampaignIntegrityError("accepted follow-up trust binding changed")
        profile = load_varrock_east_iron_profile()
        definitive: list[str] = []
        actionable: list[str] = []
        regions: dict[str, object] = {}
        production_states = []
        for resource_id, candidate, item in zip(
            VARROCK_EAST_IRON_RESOURCE_IDS,
            profile.candidates,
            resources,
            strict=True,
        ):
            if (
                not isinstance(item, dict)
                or set(item)
                != {
                    "resource_id",
                    "resource_type",
                    "available",
                    "confidence",
                    "interaction_region",
                }
                or item.get("resource_id") != resource_id
                or item.get("resource_type") != "iron"
                or item.get("available") not in {True, False, None}
                or not _followup_finite_confidence(item.get("confidence"))
            ):
                raise CampaignIntegrityError(
                    "follow-up production resource evidence changed"
                )
            available = item["available"]
            expected_region: object = (
                list(candidate.region) if available is True else None
            )
            if item.get("interaction_region") != expected_region:
                raise CampaignIntegrityError(
                    "follow-up production interaction region changed"
                )
            regions[resource_id] = expected_region
            if available is True:
                definitive.append(resource_id)
                actionable.append(resource_id)
                production_states.append(ResourceVisualState.AVAILABLE.value)
            elif available is False:
                definitive.append(resource_id)
                production_states.append(ResourceVisualState.DEPLETED.value)
            else:
                production_states.append(ResourceVisualState.UNCERTAIN.value)
        if (
            trust.get("definitive_target_ids") != definitive
            or trust.get("production_actionable_target_ids") != actionable
            or trust.get("production_interaction_regions") != regions
            or production.get("stop_required")
            is not (ResourceVisualState.UNCERTAIN.value in production_states)
            or (
                not scene_validated
                and any(
                    state != ResourceVisualState.UNCERTAIN.value
                    for state in production_states
                )
            )
        ):
            raise CampaignIntegrityError(
                "follow-up production target authority is incoherent"
            )
    reviewed_states = [state.value for _, state in decision.resource_truth]
    if (
        release_result.get("production_scene_validated") is not scene_validated
        or release_result.get("reviewed_state_vector") != reviewed_states
        or release_result.get("production_state_vector") != production_states
        or (
            release_result.get("passed") is True
            and production_states != reviewed_states
        )
    ):
        raise CampaignIntegrityError(
            "follow-up production/reviewer/release result was rebound"
        )


def _validate_followup_release_pass_claim(
    case: CampaignCase,
    release_result: Mapping[str, object],
    *,
    capture_origin: Mapping[str, object],
    capture_frame: Mapping[str, object],
    reported_dpi: object,
    decision: ReviewDecision,
    production: object,
    artifacts: Mapping[str, object],
) -> None:
    reasons = cast(list[str], release_result["reasons"])
    passed = cast(bool, release_result["passed"])
    if passed is not (not reasons):
        raise CampaignIntegrityError(
            "follow-up release PASS/reason predicate changed"
        )
    if not passed:
        return
    prior = capture_origin.get("prior_no_frame_failure_provenance")
    profile = load_varrock_east_iron_profile()
    if (
        capture_origin.get("capture_source_backend_name")
        != _LIVE_CAPTURE_BACKEND_NAME
        or capture_origin.get("evidence_origin")
        != _SOURCE_OWNED_EVIDENCE_ORIGIN
        or not isinstance(prior, list)
        or any(
            not isinstance(item, dict)
            or item.get("evidence_origin") != _SOURCE_OWNED_EVIDENCE_ORIGIN
            or item.get("capture_source_backend_name")
            != _LIVE_CAPTURE_BACKEND_NAME
            for item in prior
        )
        or reported_dpi != _REQUIRED_REPORTED_DPI
        or capture_frame.get("width") != profile.frame_width
        or capture_frame.get("height") != profile.frame_height
        or capture_frame.get("pixel_format") != PixelFormat.BGRA8888.value
        or artifacts.get("mode") != "sanitized-frame"
        or decision.meaning is not case.review_meaning
        or not isinstance(production, dict)
        or production.get("status") != "completed"
    ):
        raise CampaignIntegrityError(
            "follow-up PASS lacks source/DPI/geometry/reviewer prerequisites"
        )
    truth_states = tuple(state for _, state in decision.resource_truth)
    production_states = _production_state_vector(production)
    if production_states is None or production_states != truth_states:
        raise CampaignIntegrityError(
            "follow-up PASS state vector is not independently proven"
        )
    scene = production.get("scene")
    scene_validated = isinstance(scene, dict) and scene.get("validated") is True
    trust = production.get("trust")
    if not isinstance(trust, dict):
        raise CampaignIntegrityError("follow-up PASS lacks production trust")
    definitive = [
        resource_id
        for resource_id, state in decision.resource_truth
        if state is not ResourceVisualState.UNCERTAIN
    ]
    actionable = [
        resource_id
        for resource_id, state in decision.resource_truth
        if state is ResourceVisualState.AVAILABLE
    ]
    expected_regions = {
        candidate.resource_id: (
            list(candidate.region)
            if truth_states[index] is ResourceVisualState.AVAILABLE
            else None
        )
        for index, candidate in enumerate(profile.candidates)
    }
    if (
        trust.get("definitive_target_ids") != definitive
        or trust.get("production_actionable_target_ids") != actionable
        or trust.get("production_interaction_regions") != expected_regions
    ):
        raise CampaignIntegrityError(
            "follow-up PASS target/region evidence is inconsistent"
        )
    if case.review_meaning is ReviewMeaning.SUPPORTED_STARTUP:
        if not scene_validated or ResourceVisualState.UNCERTAIN in truth_states:
            raise CampaignIntegrityError(
                "follow-up startup PASS is not a supported definitive view"
            )
    elif case.review_meaning is ReviewMeaning.PROFILED_NODE_STATE:
        focal_id = case.focal_resource_id
        if (
            not scene_validated
            or decision.focal_resource_id != focal_id
            or decision.node_phase is not case.requested_node_phase
            or focal_id is None
            or truth_states[VARROCK_EAST_IRON_RESOURCE_IDS.index(focal_id)]
            is not case.requested_focal_state
        ):
            raise CampaignIntegrityError(
                "follow-up node-cycle PASS is not the fixed reviewed phase"
            )
    elif case.review_meaning is ReviewMeaning.UNSUPPORTED_LOCATION:
        if scene_validated or any(
            state is not ResourceVisualState.UNCERTAIN for state in truth_states
        ):
            raise CampaignIntegrityError(
                "follow-up unsupported-location PASS is not fail-closed"
            )
    elif case.review_meaning in {
        ReviewMeaning.NEIGHBORING_COPPER,
        ReviewMeaning.NEIGHBORING_TIN,
        ReviewMeaning.TERRAIN_CLUTTER,
    }:
        if decision.subject_region is None:
            raise CampaignIntegrityError(
                "follow-up negative PASS lacks reviewed subject geometry"
            )
        if any(
            region is not None
            and _regions_overlap(
                decision.subject_region,
                cast(tuple[int, int, int, int], tuple(region)),
            )
            for region in expected_regions.values()
        ):
            raise CampaignIntegrityError(
                "follow-up negative subject overlaps an iron target"
            )
        if not scene_validated and any(
            state is not ResourceVisualState.UNCERTAIN for state in truth_states
        ):
            raise CampaignIntegrityError(
                "follow-up unsupported negative PASS is not fail-closed"
            )
    elif case.review_meaning is ReviewMeaning.PROFILED_OBSTRUCTION:
        if decision.obstruction_target_kind == "landmark":
            landmarks = scene.get("landmarks") if isinstance(scene, dict) else None
            target = None
            if isinstance(landmarks, list):
                target = next(
                    (
                        item
                        for item in landmarks
                        if isinstance(item, dict)
                        and item.get("landmark_id")
                        == decision.obstruction_target_id
                    ),
                    None,
                )
            if not isinstance(target, dict) or target.get("matched") is not False:
                raise CampaignIntegrityError(
                    "follow-up landmark-obstruction PASS lacks failed target"
                )


def _validate_release_followup_inputs(value: Mapping[str, object]) -> None:
    required_top = {
        "schema_version",
        "inputs_id",
        "configuration_id",
        "source_snapshot",
        "verification",
        "case_bindings",
        "c1_result",
        "failure_promotion_inputs",
        "c2_envelope_review_inputs",
        "authority",
    }
    if set(value) != required_top or (
        not _is_strict_int(value.get("schema_version"))
        or value.get("schema_version") != 1
        or value.get("inputs_id") != _FOLLOWUP_ARTIFACT_ID
        or value.get("configuration_id") != _FOLLOWUP_CONFIGURATION_ID
    ):
        raise CampaignIntegrityError("follow-up input identity/schema changed")
    authority = value.get("authority")
    expected_authority = {
        "approval_authority": False,
        "release_eligible": False,
        "activation_allowed": False,
        "promotion_allowed": False,
        "input_authority": False,
    }
    if authority != expected_authority:
        raise CampaignIntegrityError("follow-up authority must remain deny-only")
    source = value.get("source_snapshot")
    source_fields = {
        "package_id",
        "manifest_sha256",
        "release_summary_sha256",
        "campaign_id",
        "campaign_version",
        "configuration_id",
        "session_id",
        "exported_at_utc",
        "completion_seal_sha256",
        "repository",
        "profile",
        "capture_configuration",
    }
    if not isinstance(source, dict) or set(source) != source_fields:
        raise CampaignIntegrityError("follow-up source snapshot fields changed")
    repository = source.get("repository")
    if (
        source.get("package_id") != "resource-release-review-package-v1"
        or source.get("campaign_id") != RESOURCE_RELEASE_CAMPAIGN_ID
        or source.get("campaign_version") != RESOURCE_RELEASE_CAMPAIGN_VERSION
        or source.get("configuration_id") != RESOURCE_RELEASE_CONFIGURATION_ID
        or not isinstance(source.get("session_id"), str)
        or not _IDENTIFIER_PATTERN.fullmatch(cast(str, source["session_id"]))
        or not isinstance(source.get("manifest_sha256"), str)
        or not _SHA256_PATTERN.fullmatch(cast(str, source["manifest_sha256"]))
        or not isinstance(source.get("release_summary_sha256"), str)
        or not _SHA256_PATTERN.fullmatch(
            cast(str, source["release_summary_sha256"])
        )
        or not isinstance(source.get("completion_seal_sha256"), str)
        or not _SHA256_PATTERN.fullmatch(
            cast(str, source["completion_seal_sha256"])
        )
        or source.get("profile") != _profile_identity()
        or not isinstance(repository, dict)
        or set(repository) != {"head_sha", "branch", "clean"}
        or not isinstance(repository.get("head_sha"), str)
        or not _GIT_SHA_PATTERN.fullmatch(cast(str, repository["head_sha"]))
        or not isinstance(repository.get("branch"), str)
        or not cast(str, repository["branch"]).strip()
        or repository.get("clean") is not True
    ):
        raise CampaignIntegrityError("follow-up source identity/provenance changed")
    _parse_utc(source.get("exported_at_utc"), "follow-up exported_at_utc")
    _validate_capture_configuration(source.get("capture_configuration"))
    verification = value.get("verification")
    if verification != {
        "verified": True,
        "expected_manifest_sha256_matched": True,
        "case_count": len(CAMPAIGN_PLAN),
        "operator_labels_included": False,
        "operator_labels_are_reviewer_truth": False,
        "all_cases_explicitly_privacy_reviewed": True,
        "contains_private_full_frames": False,
    }:
        raise CampaignIntegrityError("follow-up verification projection changed")
    bindings = value.get("case_bindings")
    if not isinstance(bindings, list) or len(bindings) != len(CAMPAIGN_PLAN):
        raise CampaignIntegrityError("follow-up case binding count changed")
    profile = cast(dict[str, object], source["profile"])
    required_width = cast(int, profile["frame_width"])
    required_height = cast(int, profile["frame_height"])
    required_pixel_format = cast(str, profile["pixel_format"])
    capture_configuration = cast(dict[str, object], source["capture_configuration"])
    required_dpi = cast(int, capture_configuration["required_reported_dpi"])
    release_result_fields = {
        "ordinal",
        "case_id",
        "blocker_id",
        "evidence_origin",
        "passed",
        "reasons",
        "production_scene_validated",
        "reviewed_state_vector",
        "production_state_vector",
        "reported_dpi",
        "required_reported_dpi",
        "replay_regression_candidate",
        "permanent_evidence_required",
        "policy_change_allowed_from_failure",
    }
    case_results: list[dict[str, object]] = []
    expected_candidates: list[dict[str, object]] = []
    expected_nonrelease: list[dict[str, object]] = []
    retained_failure_ids: list[str] = []
    dpi_by_case: list[dict[str, object]] = []
    observed_dpis: list[object] = []
    backends: list[object] = []
    origins: list[object] = []
    window_classes: list[object] = []
    client_geometries: list[tuple[int, int]] = []
    frame_matches: list[bool] = []
    source_matches: list[bool] = []
    for case, binding_raw in zip(CAMPAIGN_PLAN, bindings, strict=True):
        binding_fields = {
            "ordinal",
            "case_id",
            "blocker_id",
            "hashes",
            "capture_origin",
            "capture",
            "reviewer_truth",
            "release_result",
            "production_snapshot",
            "sanitized_artifacts",
        }
        if not isinstance(binding_raw, dict) or set(binding_raw) != binding_fields:
            raise CampaignIntegrityError("follow-up case binding fields changed")
        binding = cast(dict[str, object], binding_raw)
        if (
            binding.get("ordinal") != case.ordinal
            or binding.get("case_id") != case.case_id
            or binding.get("blocker_id") != case.blocker_id
        ):
            raise CampaignIntegrityError("follow-up case order/identity changed")
        hashes = binding.get("hashes")
        hash_fields = {
            "case_review_sha256",
            "capture_report_sha256",
            "review_preparation_sha256",
            "review_truth_sha256",
            "private_raw_sha256",
            "sanitized_raw_gzip_sha256",
            "sanitized_preview_sha256",
        }
        if not isinstance(hashes, dict) or set(hashes) != hash_fields:
            raise CampaignIntegrityError("follow-up case hash fields changed")
        for name in hash_fields - {
            "sanitized_raw_gzip_sha256",
            "sanitized_preview_sha256",
        }:
            item = hashes.get(name)
            if not isinstance(item, str) or not _SHA256_PATTERN.fullmatch(item):
                raise CampaignIntegrityError("follow-up case hash is malformed")
        origin = binding.get("capture_origin")
        if not isinstance(origin, dict) or set(origin) != {
            "capture_source_backend_name",
            "evidence_origin",
            "capture_attempt_count",
            "prior_no_frame_failure_provenance",
        }:
            raise CampaignIntegrityError("follow-up capture origin fields changed")
        if (
            not _valid_capture_origin_pair(
                origin.get("capture_source_backend_name"),
                origin.get("evidence_origin"),
            )
            or not _is_strict_int(origin.get("capture_attempt_count"))
            or cast(int, origin["capture_attempt_count"]) < 1
            or not isinstance(origin.get("prior_no_frame_failure_provenance"), list)
        ):
            raise CampaignIntegrityError("follow-up capture origin is malformed")
        capture = binding.get("capture")
        if not isinstance(capture, dict) or set(capture) != {
            "captured_at_utc",
            "frame",
            "environment",
        }:
            raise CampaignIntegrityError("follow-up capture fields changed")
        _parse_utc(capture.get("captured_at_utc"), "follow-up captured_at_utc")
        _, _, frame_width, frame_height, pixel_format = _validated_frame_scalars(
            capture.get("frame"), label="follow-up frame"
        )
        environment = capture.get("environment")
        environment_fields = {
            "backend_name",
            "title_match",
            "window_title_present",
            "window_class",
            "window_client_width",
            "window_client_height",
            "reported_dpi",
            "window_title_redacted",
            "native_window_handle_redacted",
        }
        if not isinstance(environment, dict) or set(environment) != environment_fields:
            raise CampaignIntegrityError("follow-up environment fields changed")
        client_width = environment.get("window_client_width")
        client_height = environment.get("window_client_height")
        reported_dpi = environment.get("reported_dpi")
        if (
            environment.get("backend_name") != _LIVE_CAPTURE_BACKEND_NAME
            or environment.get("title_match") != _LIVE_CAPTURE_TITLE_MATCH
            or environment.get("window_title_present") is not True
            or not isinstance(environment.get("window_class"), str)
            or not cast(str, environment["window_class"]).strip()
            or not _is_strict_int(client_width)
            or not _is_strict_int(client_height)
            or client_width != frame_width
            or client_height != frame_height
            or environment.get("window_title_redacted") is not True
            or environment.get("native_window_handle_redacted") is not True
            or (
                reported_dpi is not None
                and (not _is_strict_int(reported_dpi) or cast(int, reported_dpi) <= 0)
            )
        ):
            raise CampaignIntegrityError("follow-up environment provenance changed")
        reviewer_truth = binding.get("reviewer_truth")
        if not isinstance(reviewer_truth, dict) or set(reviewer_truth) != {
            "reviewed_at_utc",
            "meaning",
            "resource_truth",
            "privacy_review_confirmed",
            "focal_resource_id",
            "node_phase",
            "obstruction_target",
            "subject_region",
        } or reviewer_truth.get("privacy_review_confirmed") is not True:
            raise CampaignIntegrityError("follow-up reviewer truth changed")
        _parse_utc(
            reviewer_truth.get("reviewed_at_utc"), "follow-up reviewed_at_utc"
        )
        result = binding.get("release_result")
        if not isinstance(result, dict) or set(result) != release_result_fields:
            raise CampaignIntegrityError("follow-up release result fields changed")
        reasons = result.get("reasons")
        passed = result.get("passed")
        evidence_origin = origin["evidence_origin"]
        if (
            result.get("ordinal") != case.ordinal
            or result.get("case_id") != case.case_id
            or result.get("blocker_id") != case.blocker_id
            or result.get("evidence_origin") != evidence_origin
            or not isinstance(passed, bool)
            or not isinstance(reasons, list)
            or any(not isinstance(reason, str) for reason in reasons)
            or result.get("permanent_evidence_required") is not (not passed)
            or result.get("policy_change_allowed_from_failure") is not False
            or result.get("reported_dpi") != reported_dpi
            or result.get("required_reported_dpi") != required_dpi
        ):
            raise CampaignIntegrityError("follow-up release result was rebound")
        artifacts = binding.get("sanitized_artifacts")
        if not isinstance(artifacts, dict) or artifacts.get("mode") not in {
            "sanitized-frame",
            "pixels-withheld-unsupported-geometry",
        }:
            raise CampaignIntegrityError("follow-up sanitized artifact mode changed")
        raw_binding: object = None
        has_replay_pixels = artifacts["mode"] == "sanitized-frame"
        if has_replay_pixels:
            raw_binding = artifacts.get("raw_gzip")
            raw_sha = hashes.get("sanitized_raw_gzip_sha256")
            preview_sha = hashes.get("sanitized_preview_sha256")
            if (
                not isinstance(raw_binding, dict)
                or not isinstance(raw_sha, str)
                or not _SHA256_PATTERN.fullmatch(raw_sha)
                or not isinstance(preview_sha, str)
                or not _SHA256_PATTERN.fullmatch(preview_sha)
                or raw_binding.get("sha256") != raw_sha
            ):
                raise CampaignIntegrityError("follow-up replay artifact binding changed")
        elif (
            hashes.get("sanitized_raw_gzip_sha256") is not None
            or hashes.get("sanitized_preview_sha256") is not None
        ):
            raise CampaignIntegrityError("withheld follow-up case exposed pixel hashes")
        expected_replay_candidate: object = None
        if isinstance(raw_binding, dict):
            expected_replay_candidate = {
                "path": raw_binding["path"],
                "sha256": raw_binding["sha256"],
            }
        if result.get("replay_regression_candidate") != expected_replay_candidate:
            raise CampaignIntegrityError(
                "follow-up replay-regression candidate binding changed"
            )
        decision = _followup_review_decision(
            case,
            reviewer_truth,
            hashes,
            artifacts,
        )
        _validate_followup_production_snapshot(
            case,
            binding.get("production_snapshot"),
            capture_frame=cast(dict[str, object], capture["frame"]),
            decision=decision,
            release_result=result,
            artifacts=artifacts,
        )
        _validate_followup_release_pass_claim(
            case,
            result,
            capture_origin=origin,
            capture_frame=cast(dict[str, object], capture["frame"]),
            reported_dpi=reported_dpi,
            decision=decision,
            production=binding.get("production_snapshot"),
            artifacts=artifacts,
        )
        case_results.append(result)
        source_owned = evidence_origin == _SOURCE_OWNED_EVIDENCE_ORIGIN
        source_matches.append(source_owned)
        observed_dpis.append(reported_dpi)
        backends.append(environment["backend_name"])
        origins.append(evidence_origin)
        window_classes.append(environment["window_class"])
        client_geometries.append((cast(int, client_width), cast(int, client_height)))
        frame_matches.append(
            frame_width == required_width
            and frame_height == required_height
            and pixel_format.value == required_pixel_format
        )
        dpi_by_case.append(
            {
                "case_id": case.case_id,
                "reported_dpi": reported_dpi,
                "matches_requirement": reported_dpi == required_dpi,
            }
        )
        if passed is False:
            retained_failure_ids.append(case.case_id)
            if source_owned:
                expected_candidates.append(
                    {
                        "ordinal": case.ordinal,
                        "case_id": case.case_id,
                        "blocker_id": case.blocker_id,
                        "target_dataset_id": _FOLLOWUP_REGRESSION_DATASET_ID,
                        "disposition": (
                            "REPLAY_CANDIDATE"
                            if has_replay_pixels
                            else "METADATA_ONLY_NO_PIXELS"
                        ),
                        "replay_candidate": has_replay_pixels,
                        "source_owned_release_evidence": True,
                        "case_review_sha256": hashes["case_review_sha256"],
                        "reviewer_truth": reviewer_truth,
                        "release_reasons": reasons,
                        "sanitized_raw_gzip": raw_binding,
                        "promotion_complete": False,
                        "policy_change_allowed_from_failure": False,
                    }
                )
            else:
                expected_nonrelease.append(
                    {
                        "ordinal": case.ordinal,
                        "case_id": case.case_id,
                        "blocker_id": case.blocker_id,
                        "disposition": (
                            "NON_RELEASE_TEST_EVIDENCE"
                            if has_replay_pixels
                            else "NON_RELEASE_TEST_EVIDENCE_NO_PIXELS"
                        ),
                        "source_owned_release_evidence": False,
                        "case_review_sha256": hashes["case_review_sha256"],
                        "release_reasons": reasons,
                        "promotion_complete": False,
                        "policy_change_allowed_from_failure": False,
                    }
                )
    expected_c1: list[dict[str, object]] = []
    for blocker_id in _RESOURCE_BLOCKER_ORDER:
        members = [item for item in case_results if item["blocker_id"] == blocker_id]
        closed = bool(members) and all(item["passed"] is True for item in members)
        expected_c1.append(
            {
                "blocker_id": blocker_id,
                "status": "CLOSED" if closed else "STILL_OPEN",
                "case_ids": [item["case_id"] for item in members],
                "reasons": [
                    reason
                    for item in members
                    if item["passed"] is not True
                    for reason in cast(list[str], item["reasons"])
                ],
            }
        )
    expected_c1_status = (
        "CLOSED" if all(item["status"] == "CLOSED" for item in expected_c1) else "OPEN"
    )
    if value.get("c1_result") != {
        "status": expected_c1_status,
        "blockers": expected_c1,
    }:
        raise CampaignIntegrityError("follow-up C1 projection changed")
    expected_failure_status = (
        "BLOCKED_NON_RELEASE_EVIDENCE"
        if expected_nonrelease
        else "PENDING_EXTERNAL"
        if expected_candidates
        else "NOT_REQUIRED"
    )
    expected_failure_inputs = {
        "status": expected_failure_status,
        "target_dataset_id": _FOLLOWUP_REGRESSION_DATASET_ID,
        "candidate_count": len(expected_candidates),
        "candidates": expected_candidates,
        "nonrelease_evidence_count": len(expected_nonrelease),
        "nonrelease_evidence": expected_nonrelease,
        "promotion_complete": False,
    }
    if value.get("failure_promotion_inputs") != expected_failure_inputs:
        raise CampaignIntegrityError("follow-up failure-promotion projection changed")
    expected_categories = _release_gate_categories(
        [
            *expected_c1,
            {
                "blocker_id": _FINAL_REVIEW_BLOCKER_ID,
                "status": "STILL_OPEN",
                "case_ids": [],
                "reasons": [_FINAL_REVIEW_REASON],
            },
        ],
        case_results,
    )
    unresolved_external_inputs = [
        "external B release-evidence-boundary acceptance",
        "independent exact client/renderer/profile/envelope review",
        "source-owned constrained-v1 resource release/promotion record",
    ]
    if retained_failure_ids:
        unresolved_external_inputs.insert(
            2, "retained-failure permanent replay promotion"
        )
    distinct_window_classes = sorted(
        cast(list[str], _ordered_unique(window_classes))
    )
    expected_envelope = {
        "input_status": "verified-inputs-only-independent-review-required",
        "required_reported_dpi": required_dpi,
        "reported_dpi_by_case": dpi_by_case,
        "observed_reported_dpis": _ordered_unique(observed_dpis),
        "all_cases_match_required_dpi": all(
            item["matches_requirement"] is True for item in dpi_by_case
        ),
        "required_frame": {
            "width": required_width,
            "height": required_height,
            "pixel_format": required_pixel_format,
        },
        "all_cases_match_required_frame": all(frame_matches),
        "observed_capture_backends": sorted(
            cast(list[str], _ordered_unique(backends))
        ),
        "observed_evidence_origins": sorted(
            cast(list[str], _ordered_unique(origins))
        ),
        "observed_window_classes": distinct_window_classes,
        "observed_client_geometries": [
            {"width": width, "height": height}
            for width, height in sorted(set(client_geometries))
        ],
        "window_class_consistent": len(distinct_window_classes) == 1,
        "all_cases_source_owned": all(source_matches),
        "reported_release_gate_categories": expected_categories,
        "reported_c2_category": expected_categories[
            "c2_evidence_contingent_source_review"
        ],
        "retained_failure_case_ids": retained_failure_ids,
        "source_owned_failure_case_ids": [
            item["case_id"] for item in expected_candidates
        ],
        "nonrelease_failure_case_ids": [
            item["case_id"] for item in expected_nonrelease
        ],
        "renderer_identity": {
            "observed": False,
            "status": "NOT_OBSERVED_BY_CAPTURE_BACKEND",
            "requires_external_review": True,
        },
        "unresolved_external_inputs": unresolved_external_inputs,
        "envelope_approved": False,
    }
    if value.get("c2_envelope_review_inputs") != expected_envelope:
        raise CampaignIntegrityError("follow-up C2 envelope projection changed")


def _load_verified_followup_snapshot(
    path: Path, *, expected_sha256: str
) -> _VerifiedFollowupSnapshot:
    """Verify and snapshot follow-up inputs against an independent root."""

    path = Path(path)
    if not isinstance(expected_sha256, str) or not _SHA256_PATTERN.fullmatch(
        expected_sha256
    ):
        raise CampaignIntegrityError(
            "expected follow-up SHA-256 must be 64 lowercase hexadecimal characters"
        )
    payload, digest = _verify_hashed_artifact(
        path,
        expected=expected_sha256,
        maximum_bytes=_MAX_FOLLOWUP_JSON_BYTES,
    )
    inputs = _strict_json_bytes(payload, label="resource release follow-up inputs")
    _validate_release_followup_inputs(inputs)
    return _VerifiedFollowupSnapshot(
        path=path,
        sha256=digest,
        inputs_json=payload,
        inputs=inputs,
    )


def verify_release_followup_inputs(
    path: Path, *, expected_sha256: str
) -> dict[str, object]:
    """Verify immutable follow-up inputs against an independently retained root."""

    snapshot = _load_verified_followup_snapshot(path, expected_sha256=expected_sha256)
    inputs = snapshot.inputs
    candidates = cast(
        dict[str, object], inputs["failure_promotion_inputs"]
    )["candidate_count"]
    return {
        "inputs": str(snapshot.path),
        "sha256": snapshot.sha256,
        "source_manifest_sha256": cast(
            dict[str, object], inputs["source_snapshot"]
        )["manifest_sha256"],
        "case_count": len(cast(list[object], inputs["case_bindings"])),
        "failure_candidate_count": candidates,
        "release_eligible": False,
        "activation_allowed": False,
        "verified": True,
    }
