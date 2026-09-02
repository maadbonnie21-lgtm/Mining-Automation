"""Deterministic integrity-only endurance packaging for resource release evidence.

This module composes the existing resource review-package, follow-up,
replay-proposal, and release-decision verifiers.  It does not interpret pixels,
approve an envelope, issue a release receipt, or grant any runtime authority.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from . import resource_release_campaign as campaign
from . import resource_release_decision as release_decision
from . import resource_release_receipt as release_receipt
from . import resource_replay_promotion as replay_promotion

__all__ = [
    "RESOURCE_RELEASE_ENDURANCE_SCHEMA_VERSION",
    "RESOURCE_RELEASE_ENDURANCE_STATUS",
    "ResourceReleaseChainExpectation",
    "ResourceReleaseEnduranceError",
    "verify_resource_release_endurance_report",
    "write_resource_release_endurance_report",
]

RESOURCE_RELEASE_ENDURANCE_SCHEMA_VERSION: Final[int] = 1
RESOURCE_RELEASE_ENDURANCE_STATUS: Final[str] = "INTEGRITY_ONLY_NO_AUTHORITY"

_REPORT_ID: Final[str] = "resource-release-fault-endurance-report-v1"
_CONFIGURATION_ID: Final[str] = (
    "resource-release-fault-endurance:varrock-east-iron-v1@1.0.0"
)
_MAX_REPORT_BYTES: Final[int] = 1024 * 1024
_SHA256_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}\Z")
_GIT_SHA_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{40}\Z")
_SESSION_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z"
)
_EXPECTED_RECEIPT_UNAVAILABLE_REASON: Final[str] = (
    "resource release gates remain open: "
    + ", ".join(gate.value for gate in release_receipt.ResourceReleaseGate)
)


class ResourceReleaseEnduranceError(campaign.CampaignIntegrityError):
    """Raised when the fixed integrity campaign cannot complete exactly."""


@dataclass(frozen=True, slots=True)
class ResourceReleaseChainExpectation:
    """Roots retained independently from the release artifact chain.

    ``proposal_manifest_sha256`` is ``None`` only when the rooted follow-up
    proves that no replay-proposal directory is required.
    """

    session_id: str
    repository_head_sha: str
    package_manifest_sha256: str
    followup_sha256: str
    proposal_manifest_sha256: str | None
    decision_sha256: str

    def __post_init__(self) -> None:
        if (
            type(self.session_id) is not str
            or not _SESSION_ID_PATTERN.fullmatch(self.session_id)
        ):
            raise ValueError("session_id must be one exact retained campaign identifier")
        if (
            type(self.repository_head_sha) is not str
            or not _GIT_SHA_PATTERN.fullmatch(self.repository_head_sha)
        ):
            raise ValueError("repository_head_sha must be a lowercase 40-character Git ID")
        for name in (
            "package_manifest_sha256",
            "followup_sha256",
            "decision_sha256",
        ):
            value = getattr(self, name)
            if type(value) is not str or not _SHA256_PATTERN.fullmatch(value):
                raise ValueError(f"{name} must be a lowercase SHA-256")
        proposal = self.proposal_manifest_sha256
        if proposal is not None and (
            type(proposal) is not str or not _SHA256_PATTERN.fullmatch(proposal)
        ):
            raise ValueError(
                "proposal_manifest_sha256 must be a lowercase SHA-256 or None"
            )


def _authority() -> dict[str, object]:
    return {
        "approval_authority": False,
        "release_eligible": False,
        "promotion_allowed": False,
        "receipt_authority": False,
        "activation_allowed": False,
        "world_state_authority": False,
        "controller_authority": False,
        "mining_authority": False,
        "banking_authority": False,
        "navigation_authority": False,
        "input_authority": False,
        "click_authority": False,
    }


def _privacy() -> dict[str, object]:
    return {
        "metadata_only_report": True,
        "contains_live_pixels": False,
        "contains_private_full_frames": False,
        "contains_sanitized_pixels": False,
        "source_artifacts_embedded": False,
    }


def _fresh_session_recovery() -> dict[str, object]:
    return {
        "failure_action": "RETAIN_CHAIN_AND_START_FRESH_SOURCE_OWNED_SESSION",
        "failed_chain_mutation_allowed": False,
        "same_session_retry_allowed": False,
        "automatic_retry_allowed": False,
        "fallback_allowed": False,
        "expected_root_rebinding_allowed": False,
        "approval_override_allowed": False,
        "new_session_id_required": True,
        "new_exact_clean_head_binding_required": True,
    }


def _expectation_json(value: ResourceReleaseChainExpectation) -> dict[str, object]:
    return {
        "session_id": value.session_id,
        "repository_head_sha": value.repository_head_sha,
        "package_manifest_sha256": value.package_manifest_sha256,
        "followup_sha256": value.followup_sha256,
        "proposal_manifest_sha256": value.proposal_manifest_sha256,
        "decision_sha256": value.decision_sha256,
    }


def _require_expectation(value: object) -> ResourceReleaseChainExpectation:
    if type(value) is not ResourceReleaseChainExpectation:
        raise TypeError("expectation must be exact ResourceReleaseChainExpectation")
    # Work only from an internal validated copy.  A caller retaining the public
    # value cannot rebind roots between endurance rounds with object.__setattr__.
    return ResourceReleaseChainExpectation(
        session_id=value.session_id,
        repository_head_sha=value.repository_head_sha,
        package_manifest_sha256=value.package_manifest_sha256,
        followup_sha256=value.followup_sha256,
        proposal_manifest_sha256=value.proposal_manifest_sha256,
        decision_sha256=value.decision_sha256,
    )


def _strict_mapping(
    value: object,
    fields: set[str],
    *,
    label: str,
) -> dict[str, object]:
    if type(value) is not dict or set(cast(dict[object, object], value)) != fields:
        raise ResourceReleaseEnduranceError(f"{label} fields changed")
    return cast(dict[str, object], value)


def _strict_count(value: object, *, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ResourceReleaseEnduranceError(f"{label} must be a non-negative integer")
    return value


def _strict_sha256(value: object, *, label: str) -> str:
    if type(value) is not str or not _SHA256_PATTERN.fullmatch(value):
        raise ResourceReleaseEnduranceError(
            f"{label} must be an exact lowercase SHA-256"
        )
    return value


def _strict_string_list(value: object, *, label: str) -> list[str]:
    if type(value) is not list or any(
        type(item) is not str for item in cast(list[object], value)
    ):
        raise ResourceReleaseEnduranceError(f"{label} must be a plain string list")
    return cast(list[str], value).copy()


def _artifact_sidecar(path: Path) -> Path:
    return path.with_name(f"{path.name}.sha256")


def _exclusive_write_report(path: Path, payload: bytes) -> str:
    """Use the ownership-safe, manifest-last hashed artifact publisher."""

    return campaign._write_hashed_artifact(path, payload)


def _require_non_ads_path(path: Path, *, label: str) -> Path:
    supplied = Path(path)
    # On Windows, ``name:stream`` addresses an NTFS alternate data stream on
    # the named source file rather than an independent artifact.  A report and
    # sidecar must never be attachable to, or loaded from, a rooted source that
    # way. The drive prefix is the only permitted colon.
    path_without_drive = str(supplied)[len(supplied.drive) :]
    if ":" in path_without_drive:
        raise ResourceReleaseEnduranceError(
            f"resource endurance {label} must not address an alternate data stream"
        )
    return supplied


def _resolved_output(path: Path) -> Path:
    supplied = _require_non_ads_path(path, label="report path")
    try:
        parent = supplied.parent.resolve(strict=True)
    except OSError as exc:
        raise ResourceReleaseEnduranceError(
            "resource endurance report parent directory is unavailable"
        ) from exc
    if not parent.is_dir() or parent.is_symlink():
        raise ResourceReleaseEnduranceError(
            "resource endurance report parent must be a real directory"
        )
    return parent / supplied.name


@dataclass(frozen=True, slots=True)
class _ResolvedChainPaths:
    package_dir: Path
    followup_path: Path
    proposal_dir: Path | None
    decision_path: Path


def _resolve_chain_paths(
    *,
    package_dir: Path,
    followup_path: Path,
    proposal_dir: Path | None,
    decision_path: Path,
) -> _ResolvedChainPaths:
    package_path = _require_non_ads_path(package_dir, label="package path")
    followup_path_value = _require_non_ads_path(
        followup_path, label="follow-up path"
    )
    decision_path_value = _require_non_ads_path(
        decision_path, label="decision path"
    )
    proposal_path = (
        None
        if proposal_dir is None
        else _require_non_ads_path(proposal_dir, label="proposal path")
    )
    for label, source_path in (
        ("package", package_path),
        ("follow-up", followup_path_value),
        ("proposal", proposal_path),
        ("decision", decision_path_value),
    ):
        if source_path is not None and source_path.is_symlink():
            raise ResourceReleaseEnduranceError(
                f"resource endurance {label} source must not be a symlink"
            )
    try:
        return _ResolvedChainPaths(
            package_dir=package_path.resolve(strict=True),
            followup_path=followup_path_value.resolve(strict=True),
            proposal_dir=(
                None if proposal_path is None else proposal_path.resolve(strict=True)
            ),
            decision_path=decision_path_value.resolve(strict=True),
        )
    except OSError as exc:
        raise ResourceReleaseEnduranceError(
            "resource endurance source artifact is unavailable"
        ) from exc


def _reject_report_source_overlap(
    report_path: Path,
    *,
    sources: _ResolvedChainPaths,
) -> Path:
    report = _resolved_output(report_path)
    report_sidecar = _artifact_sidecar(report)
    source_files = {
        sources.followup_path,
        _artifact_sidecar(sources.followup_path),
        sources.decision_path,
        _artifact_sidecar(sources.decision_path),
    }
    if report in source_files or report_sidecar in source_files:
        raise ResourceReleaseEnduranceError(
            "resource endurance report must not replace a source artifact"
        )
    for root in (sources.package_dir, sources.proposal_dir):
        if root is not None and (
            report.is_relative_to(root) or report_sidecar.is_relative_to(root)
        ):
            raise ResourceReleaseEnduranceError(
                "resource endurance report must be outside source directories"
            )
    return report


def _validate_path_pair(
    proposal_dir: Path | None,
    expectation: ResourceReleaseChainExpectation,
) -> None:
    expected_proposal = expectation.proposal_manifest_sha256
    if (proposal_dir is None) != (expected_proposal is None):
        raise ResourceReleaseEnduranceError(
            "proposal directory and independently retained proposal root must be "
            "present or absent together"
        )


def _load_followup_projection(
    path: Path,
    *,
    expectation: ResourceReleaseChainExpectation,
) -> dict[str, object]:
    payload, digest = campaign._verify_hashed_artifact(
        Path(path),
        expected=expectation.followup_sha256,
        maximum_bytes=campaign._MAX_FOLLOWUP_JSON_BYTES,
    )
    inputs = campaign._strict_json_bytes(
        payload, label="resource endurance rooted follow-up"
    )
    if payload != campaign._canonical_json_bytes(inputs):
        raise ResourceReleaseEnduranceError(
            "resource endurance follow-up is not canonical JSON"
        )
    source = _strict_mapping(
        inputs.get("source_snapshot"),
        {
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
        },
        label="resource endurance follow-up source",
    )
    repository = _strict_mapping(
        source.get("repository"),
        {"head_sha", "branch", "clean"},
        label="resource endurance repository",
    )
    verification = _strict_mapping(
        inputs.get("verification"),
        {
            "verified",
            "expected_manifest_sha256_matched",
            "case_count",
            "operator_labels_included",
            "operator_labels_are_reviewer_truth",
            "all_cases_explicitly_privacy_reviewed",
            "contains_private_full_frames",
        },
        label="resource endurance follow-up verification",
    )
    failures = _strict_mapping(
        inputs.get("failure_promotion_inputs"),
        {
            "status",
            "target_dataset_id",
            "candidate_count",
            "candidates",
            "nonrelease_evidence_count",
            "nonrelease_evidence",
            "promotion_complete",
        },
        label="resource endurance retained failures",
    )
    envelope = _strict_mapping(
        inputs.get("c2_envelope_review_inputs"),
        {
            "input_status",
            "required_reported_dpi",
            "reported_dpi_by_case",
            "observed_reported_dpis",
            "all_cases_match_required_dpi",
            "required_frame",
            "all_cases_match_required_frame",
            "observed_capture_backends",
            "observed_evidence_origins",
            "observed_window_classes",
            "observed_client_geometries",
            "window_class_consistent",
            "all_cases_source_owned",
            "reported_release_gate_categories",
            "reported_c2_category",
            "retained_failure_case_ids",
            "source_owned_failure_case_ids",
            "nonrelease_failure_case_ids",
            "renderer_identity",
            "unresolved_external_inputs",
            "envelope_approved",
        },
        label="resource endurance envelope",
    )
    c1 = _strict_mapping(
        inputs.get("c1_result"),
        {"status", "blockers"},
        label="resource endurance C1 result",
    )
    authority = _strict_mapping(
        inputs.get("authority"),
        {
            "approval_authority",
            "release_eligible",
            "activation_allowed",
            "promotion_allowed",
            "input_authority",
        },
        label="resource endurance follow-up authority",
    )
    required_frame = _strict_mapping(
        envelope.get("required_frame"),
        {"width", "height", "pixel_format"},
        label="resource endurance required frame",
    )
    renderer_identity = _strict_mapping(
        envelope.get("renderer_identity"),
        {"observed", "status", "requires_external_review"},
        label="resource endurance renderer identity",
    )
    if (
        renderer_identity.get("observed") is not False
        or type(renderer_identity.get("status")) is not str
        or not cast(str, renderer_identity["status"])
        or renderer_identity.get("requires_external_review") is not True
    ):
        raise ResourceReleaseEnduranceError(
            "resource endurance renderer identity changed"
        )
    unresolved_external_inputs = _strict_string_list(
        envelope.get("unresolved_external_inputs"),
        label="resource endurance unresolved external inputs",
    )
    if (
        digest != expectation.followup_sha256
        or type(source.get("session_id")) is not str
        or source["session_id"] != expectation.session_id
        or type(source.get("manifest_sha256")) is not str
        or source["manifest_sha256"] != expectation.package_manifest_sha256
        or type(repository.get("head_sha")) is not str
        or repository["head_sha"] != expectation.repository_head_sha
        or repository.get("clean") is not True
        or verification.get("verified") is not True
        or verification.get("expected_manifest_sha256_matched") is not True
        or verification.get("operator_labels_included") is not False
        or verification.get("operator_labels_are_reviewer_truth") is not False
        or verification.get("all_cases_explicitly_privacy_reviewed") is not True
        or verification.get("contains_private_full_frames") is not False
        or failures.get("promotion_complete") is not False
        or envelope.get("envelope_approved") is not False
        or authority
        != {
            "approval_authority": False,
            "release_eligible": False,
            "activation_allowed": False,
            "promotion_allowed": False,
            "input_authority": False,
        }
    ):
        raise ResourceReleaseEnduranceError(
            "resource endurance follow-up identity or authority changed"
        )
    case_count = _strict_count(
        verification.get("case_count"), label="follow-up case count"
    )
    candidate_count = _strict_count(
        failures.get("candidate_count"), label="retained candidate count"
    )
    nonrelease_count = _strict_count(
        failures.get("nonrelease_evidence_count"),
        label="nonrelease evidence count",
    )
    retained = _strict_string_list(
        envelope.get("retained_failure_case_ids"),
        label="retained failure case IDs",
    )
    source_owned = _strict_string_list(
        envelope.get("source_owned_failure_case_ids"),
        label="source-owned failure case IDs",
    )
    nonrelease = _strict_string_list(
        envelope.get("nonrelease_failure_case_ids"),
        label="nonrelease failure case IDs",
    )
    source_manifest_sha256 = _strict_sha256(
        source.get("manifest_sha256"),
        label="resource endurance source manifest root",
    )
    release_summary_sha256 = _strict_sha256(
        source.get("release_summary_sha256"),
        label="resource endurance release summary root",
    )
    completion_seal_sha256 = _strict_sha256(
        source.get("completion_seal_sha256"),
        label="resource endurance completion seal root",
    )
    for name in (
        "observed_reported_dpis",
        "observed_client_geometries",
        "observed_capture_backends",
        "observed_evidence_origins",
        "observed_window_classes",
    ):
        if type(envelope.get(name)) is not list:
            raise ResourceReleaseEnduranceError(
                f"resource endurance {name} must remain a list"
            )
    return {
        "session_id": source["session_id"],
        "repository_head_sha": repository["head_sha"],
        "package_manifest_sha256": source_manifest_sha256,
        "release_summary_sha256": release_summary_sha256,
        "completion_seal_sha256": completion_seal_sha256,
        "case_count": case_count,
        "c1_status": c1["status"],
        "retained_failures": {
            "status": failures["status"],
            "candidate_count": candidate_count,
            "nonrelease_evidence_count": nonrelease_count,
            "retained_failure_case_ids": retained,
            "source_owned_failure_case_ids": source_owned,
            "nonrelease_failure_case_ids": nonrelease,
            "promotion_complete": False,
        },
        "environment": {
            "required_reported_dpi": envelope["required_reported_dpi"],
            "observed_reported_dpis": cast(list[object], envelope["observed_reported_dpis"]).copy(),
            "all_cases_match_required_dpi": envelope[
                "all_cases_match_required_dpi"
            ],
            "required_frame": required_frame.copy(),
            "all_cases_match_required_frame": envelope[
                "all_cases_match_required_frame"
            ],
            "observed_client_geometries": cast(
                list[object], envelope["observed_client_geometries"]
            ).copy(),
            "observed_window_classes": cast(
                list[object], envelope["observed_window_classes"]
            ).copy(),
            "window_class_consistent": envelope["window_class_consistent"],
            "observed_capture_backends": cast(
                list[object], envelope["observed_capture_backends"]
            ).copy(),
            "observed_evidence_origins": cast(
                list[object], envelope["observed_evidence_origins"]
            ).copy(),
            "all_cases_source_owned": envelope["all_cases_source_owned"],
            "renderer_identity": renderer_identity.copy(),
            "unresolved_external_inputs": unresolved_external_inputs,
            "envelope_approved": False,
        },
    }


def _verify_package_result(
    value: object,
    *,
    expectation: ResourceReleaseChainExpectation,
) -> dict[str, object]:
    result = _strict_mapping(
        value,
        {
            "package",
            "manifest_sha256",
            "release_summary_sha256",
            "case_count",
            "contains_private_full_frames",
            "activation_allowed",
            "verified",
        },
        label="resource endurance package verifier result",
    )
    manifest_sha256 = _strict_sha256(
        result.get("manifest_sha256"), label="package verifier manifest root"
    )
    release_summary_sha256 = _strict_sha256(
        result.get("release_summary_sha256"),
        label="package verifier release summary root",
    )
    if (
        manifest_sha256 != expectation.package_manifest_sha256
        or result.get("verified") is not True
        or result.get("contains_private_full_frames") is not False
        or result.get("activation_allowed") is not False
    ):
        raise ResourceReleaseEnduranceError(
            "resource endurance package verifier projection changed"
        )
    return {
        "manifest_sha256": manifest_sha256,
        "release_summary_sha256": release_summary_sha256,
        "case_count": _strict_count(
            result.get("case_count"), label="package case count"
        ),
        "integrity_verified": True,
        "contains_private_full_frames": False,
        "activation_allowed": False,
    }


def _verify_followup_result(
    value: object,
    *,
    expectation: ResourceReleaseChainExpectation,
) -> dict[str, object]:
    result = _strict_mapping(
        value,
        {
            "inputs",
            "sha256",
            "source_manifest_sha256",
            "case_count",
            "failure_candidate_count",
            "release_eligible",
            "activation_allowed",
            "verified",
        },
        label="resource endurance follow-up verifier result",
    )
    followup_sha256 = _strict_sha256(
        result.get("sha256"), label="follow-up verifier root"
    )
    source_manifest_sha256 = _strict_sha256(
        result.get("source_manifest_sha256"),
        label="follow-up verifier source manifest root",
    )
    if (
        followup_sha256 != expectation.followup_sha256
        or source_manifest_sha256 != expectation.package_manifest_sha256
        or result.get("verified") is not True
        or result.get("release_eligible") is not False
        or result.get("activation_allowed") is not False
    ):
        raise ResourceReleaseEnduranceError(
            "resource endurance follow-up verifier projection changed"
        )
    return {
        "sha256": followup_sha256,
        "source_manifest_sha256": source_manifest_sha256,
        "case_count": _strict_count(
            result.get("case_count"), label="follow-up verifier case count"
        ),
        "failure_candidate_count": _strict_count(
            result.get("failure_candidate_count"),
            label="follow-up failure candidate count",
        ),
        "integrity_verified": True,
        "release_eligible": False,
        "activation_allowed": False,
    }


def _verify_proposal_result(
    value: object,
    *,
    expectation: ResourceReleaseChainExpectation,
) -> dict[str, object]:
    result = _strict_mapping(
        value,
        {
            "proposal_dir",
            "manifest_sha256",
            "proposal_count",
            "verified",
            "adopted",
            "promotion_allowed",
            "activation_allowed",
        },
        label="resource endurance proposal verifier result",
    )
    manifest_sha256 = _strict_sha256(
        result.get("manifest_sha256"), label="proposal verifier manifest root"
    )
    if (
        manifest_sha256 != expectation.proposal_manifest_sha256
        or result.get("verified") is not True
        or result.get("adopted") is not False
        or result.get("promotion_allowed") is not False
        or result.get("activation_allowed") is not False
    ):
        raise ResourceReleaseEnduranceError(
            "resource endurance proposal verifier projection changed"
        )
    return {
        "required": True,
        "manifest_sha256": manifest_sha256,
        "proposal_count": _strict_count(
            result.get("proposal_count"), label="proposal count"
        ),
        "integrity_verified": True,
        "adopted": False,
        "promotion_allowed": False,
        "activation_allowed": False,
    }


def _verify_decision_result(
    value: object,
    *,
    expectation: ResourceReleaseChainExpectation,
) -> dict[str, object]:
    result = _strict_mapping(
        value,
        {
            "path",
            "sha256",
            "followup_sha256",
            "proposal_manifest_sha256",
            "unresolved_condition_count",
            "packet_integrity_verified",
            "review_packet_prepared",
            "release_eligible",
            "activation_allowed",
        },
        label="resource endurance decision verifier result",
    )
    decision_sha256 = _strict_sha256(
        result.get("sha256"), label="decision verifier root"
    )
    followup_sha256 = _strict_sha256(
        result.get("followup_sha256"), label="decision verifier follow-up root"
    )
    proposal_value = result.get("proposal_manifest_sha256")
    proposal_sha256 = (
        None
        if proposal_value is None
        else _strict_sha256(
            proposal_value, label="decision verifier proposal manifest root"
        )
    )
    if (
        decision_sha256 != expectation.decision_sha256
        or followup_sha256 != expectation.followup_sha256
        or proposal_sha256 != expectation.proposal_manifest_sha256
        or result.get("packet_integrity_verified") is not True
        or result.get("review_packet_prepared") is not True
        or result.get("release_eligible") is not False
        or result.get("activation_allowed") is not False
    ):
        raise ResourceReleaseEnduranceError(
            "resource endurance decision verifier projection changed"
        )
    return {
        "sha256": decision_sha256,
        "followup_sha256": followup_sha256,
        "proposal_manifest_sha256": proposal_sha256,
        "unresolved_condition_count": _strict_count(
            result.get("unresolved_condition_count"),
            label="decision unresolved condition count",
        ),
        "integrity_verified": True,
        "review_packet_prepared": True,
        "release_eligible": False,
        "activation_allowed": False,
    }


def _verify_receipt_readiness() -> dict[str, object]:
    """Require the frozen A5 loader's exact all-gates-open result."""

    try:
        returned = (
            release_receipt.load_source_owned_varrock_east_iron_release_receipt()
        )
    except release_receipt.ResourceReleaseReceiptUnavailable as exc:
        if (
            type(exc) is not release_receipt.ResourceReleaseReceiptUnavailable
            or str(exc) != _EXPECTED_RECEIPT_UNAVAILABLE_REASON
        ):
            raise ResourceReleaseEnduranceError(
                "resource release receipt-readiness result changed"
            ) from exc
    else:
        del returned
        raise ResourceReleaseEnduranceError(
            "resource release receipt loader returned while A5 gates must remain open"
        )
    return {
        "status": "ALL_SOURCE_RECEIPT_GATES_OPEN",
        "loader_result": None,
        "receipt_issued": False,
        "receipt_authority": False,
        "activation_allowed": False,
    }


def _verify_round(
    ordinal: int,
    *,
    package_dir: Path,
    followup_path: Path,
    proposal_dir: Path | None,
    decision_path: Path,
    expectation: ResourceReleaseChainExpectation,
) -> dict[str, object]:
    package = _verify_package_result(
        campaign.verify_review_package(
            package_dir,
            expected_manifest_sha256=expectation.package_manifest_sha256,
        ),
        expectation=expectation,
    )
    followup = _verify_followup_result(
        campaign.verify_release_followup_inputs(
            followup_path,
            expected_sha256=expectation.followup_sha256,
        ),
        expectation=expectation,
    )
    if proposal_dir is None:
        proposal: dict[str, object] = {
            "required": False,
            "manifest_sha256": None,
            "proposal_count": 0,
            "integrity_verified": True,
            "adopted": False,
            "promotion_allowed": False,
            "activation_allowed": False,
        }
    else:
        proposal = _verify_proposal_result(
            replay_promotion.verify_replay_promotion_proposals(
                proposal_dir,
                expected_manifest_sha256=cast(
                    str, expectation.proposal_manifest_sha256
                ),
            ),
            expectation=expectation,
        )
    decision = _verify_decision_result(
        release_decision.verify_resource_release_decision(
            decision_path,
            expected_sha256=expectation.decision_sha256,
        ),
        expectation=expectation,
    )
    receipt_readiness = _verify_receipt_readiness()
    source = _load_followup_projection(
        followup_path,
        expectation=expectation,
    )
    if (
        package["case_count"] != followup["case_count"]
        or source["case_count"] != package["case_count"]
        or package["release_summary_sha256"]
        != source["release_summary_sha256"]
        or followup["failure_candidate_count"]
        != cast(dict[str, object], source["retained_failures"])[
            "candidate_count"
        ]
    ):
        raise ResourceReleaseEnduranceError(
            "resource endurance chain counts or roots changed"
        )
    return {
        "round": ordinal,
        "package": package,
        "followup": followup,
        "proposal": proposal,
        "decision": decision,
        "receipt_readiness": receipt_readiness,
        "source": source,
        "chain_consistent": True,
    }


def _build_report(
    *,
    package_dir: Path,
    followup_path: Path,
    proposal_dir: Path | None,
    decision_path: Path,
    expectation: ResourceReleaseChainExpectation,
) -> dict[str, object]:
    rounds: list[dict[str, object]] = []
    reference: dict[str, object] | None = None
    # Keep the fixed policy literal in the execution path.  It must not be
    # mutable through rebinding a module-level round-count constant.
    for ordinal in (1, 2, 3):
        try:
            current = _verify_round(
                ordinal,
                package_dir=package_dir,
                followup_path=followup_path,
                proposal_dir=proposal_dir,
                decision_path=decision_path,
                expectation=expectation,
            )
        except Exception as exc:
            raise ResourceReleaseEnduranceError(
                f"resource release integrity round {ordinal} failed; retain the "
                "entire chain unchanged and start a fresh source-owned session"
            ) from exc
        projection = {key: value for key, value in current.items() if key != "round"}
        if reference is None:
            reference = projection
        elif projection != reference:
            raise ResourceReleaseEnduranceError(
                f"resource release integrity round {ordinal} changed; retain the "
                "entire chain unchanged and start a fresh source-owned session"
            )
        rounds.append(current)
    assert reference is not None
    source = cast(dict[str, object], reference["source"])
    return {
        "schema_version": RESOURCE_RELEASE_ENDURANCE_SCHEMA_VERSION,
        "report_id": _REPORT_ID,
        "configuration_id": _CONFIGURATION_ID,
        "status": RESOURCE_RELEASE_ENDURANCE_STATUS,
        "expectation": _expectation_json(expectation),
        "verification": {
            "required_round_count": 3,
            "completed_round_count": len(rounds),
            "all_rounds_identical": True,
            "failed_round_retry_attempted": False,
            "fallback_used": False,
            "rounds": rounds,
        },
        "retained_failures": source["retained_failures"],
        "environment": source["environment"],
        "privacy": _privacy(),
        "fresh_session_recovery": _fresh_session_recovery(),
        "authority": _authority(),
    }


def _result(path: Path, digest: str) -> dict[str, object]:
    return {
        "report": str(Path(path).resolve(strict=True)),
        "sha256": digest,
        "status": RESOURCE_RELEASE_ENDURANCE_STATUS,
        "verification_rounds": 3,
        "release_eligible": False,
        "promotion_allowed": False,
        "receipt_authority": False,
        "activation_allowed": False,
        "world_state_authority": False,
        "controller_authority": False,
        "input_authority": False,
        "verified": True,
    }


def write_resource_release_endurance_report(
    package_dir: Path,
    followup_path: Path,
    decision_path: Path,
    output_path: Path,
    *,
    proposal_dir: Path | None,
    expectation: ResourceReleaseChainExpectation,
) -> dict[str, object]:
    """Run exactly three integrity rounds and exclusively publish one report."""

    exact_expectation = _require_expectation(expectation)
    _validate_path_pair(proposal_dir, exact_expectation)
    initial_sources = _resolve_chain_paths(
        package_dir=package_dir,
        followup_path=followup_path,
        proposal_dir=proposal_dir,
        decision_path=decision_path,
    )
    initial_output = _reject_report_source_overlap(
        output_path,
        sources=initial_sources,
    )
    report = _build_report(
        package_dir=initial_sources.package_dir,
        followup_path=initial_sources.followup_path,
        proposal_dir=initial_sources.proposal_dir,
        decision_path=initial_sources.decision_path,
        expectation=exact_expectation,
    )
    payload = campaign._canonical_json_bytes(report)
    if len(payload) > _MAX_REPORT_BYTES:
        raise ResourceReleaseEnduranceError("resource endurance report is oversized")
    final_sources = _resolve_chain_paths(
        package_dir=package_dir,
        followup_path=followup_path,
        proposal_dir=proposal_dir,
        decision_path=decision_path,
    )
    if final_sources != initial_sources:
        raise ResourceReleaseEnduranceError(
            "resource endurance source paths changed during verification"
        )
    resolved_output = _reject_report_source_overlap(
        output_path,
        sources=final_sources,
    )
    if resolved_output != initial_output:
        raise ResourceReleaseEnduranceError(
            "resource endurance report destination changed during verification"
        )
    digest = _exclusive_write_report(resolved_output, payload)
    return _result(resolved_output, digest)


def verify_resource_release_endurance_report(
    report_path: Path,
    package_dir: Path,
    followup_path: Path,
    decision_path: Path,
    *,
    proposal_dir: Path | None,
    expectation: ResourceReleaseChainExpectation,
    expected_report_sha256: str,
) -> dict[str, object]:
    """Verify the report root and rerun the exact fixed three-round chain."""

    exact_expectation = _require_expectation(expectation)
    if (
        type(expected_report_sha256) is not str
        or not _SHA256_PATTERN.fullmatch(expected_report_sha256)
    ):
        raise ValueError("expected_report_sha256 must be a lowercase SHA-256")
    _validate_path_pair(proposal_dir, exact_expectation)
    initial_sources = _resolve_chain_paths(
        package_dir=package_dir,
        followup_path=followup_path,
        proposal_dir=proposal_dir,
        decision_path=decision_path,
    )
    resolved_report = _reject_report_source_overlap(
        report_path,
        sources=initial_sources,
    )
    payload, digest = campaign._verify_hashed_artifact(
        resolved_report,
        expected=expected_report_sha256,
        maximum_bytes=_MAX_REPORT_BYTES,
    )
    stored = campaign._strict_json_bytes(
        payload, label="resource release endurance report"
    )
    if payload != campaign._canonical_json_bytes(stored):
        raise ResourceReleaseEnduranceError(
            "resource release endurance report is not canonical JSON"
        )
    expected = _build_report(
        package_dir=initial_sources.package_dir,
        followup_path=initial_sources.followup_path,
        proposal_dir=initial_sources.proposal_dir,
        decision_path=initial_sources.decision_path,
        expectation=exact_expectation,
    )
    if stored != expected:
        raise ResourceReleaseEnduranceError(
            "resource release endurance report no longer matches the rooted chain"
        )
    final_payload, final_digest = campaign._verify_hashed_artifact(
        resolved_report,
        expected=expected_report_sha256,
        maximum_bytes=_MAX_REPORT_BYTES,
    )
    if final_payload != payload or final_digest != digest:
        raise ResourceReleaseEnduranceError(
            "resource release endurance report changed during verification"
        )
    final_sources = _resolve_chain_paths(
        package_dir=package_dir,
        followup_path=followup_path,
        proposal_dir=proposal_dir,
        decision_path=decision_path,
    )
    if final_sources != initial_sources:
        raise ResourceReleaseEnduranceError(
            "resource endurance source paths changed during verification"
        )
    final_report = _reject_report_source_overlap(
        report_path,
        sources=final_sources,
    )
    if final_report != resolved_report:
        raise ResourceReleaseEnduranceError(
            "resource endurance report destination changed during verification"
        )
    return _result(final_report, digest)
