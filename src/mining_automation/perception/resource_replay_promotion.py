"""Prepare retained resource failures for later replay-regression review.

This module deliberately stops before adoption.  It copies only the exact
privacy-safe bytes of source-owned retained failures selected by an externally
rooted follow-up artifact and emits deterministic fixture/evaluator proposals.
It cannot modify a replay dataset, approve perception, or grant input authority.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Final, cast

from . import resource_release_campaign as campaign
from .production_profiles import load_varrock_east_iron_profile

__all__ = [
    "prepare_replay_promotion_proposals",
    "verify_replay_promotion_proposals",
]

_PREPARATION_ID: Final[str] = "resource-release-replay-promotion-preparation-v1"
_CONFIGURATION_ID: Final[str] = (
    "resource-release-replay-preparation:varrock-east-iron-v1@1.0.0"
)
_FOLLOWUP_ID: Final[str] = "resource-release-followup-inputs-v1"
_TARGET_DATASET_ID: Final[str] = "varrock-east-iron-release-regressions-v1"
_SOURCE_ORIGIN: Final[str] = "source-owned-windows-runelite"
_MANIFEST_NAME: Final[str] = "proposal-manifest.json"
_EMBEDDED_FOLLOWUP_PATH: Final[str] = "source/followup-inputs.json"
_MAX_JSON_BYTES: Final[int] = 16 * 1024 * 1024
_MAX_GZIP_OVERHEAD: Final[int] = 4096


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _strict_mapping(value: object, fields: set[str], *, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise campaign.CampaignIntegrityError(f"{label} fields changed")
    return cast(dict[str, object], value)


def _portable_path(value: object, *, label: str) -> str:
    if not isinstance(value, str):
        raise campaign.CampaignIntegrityError(f"{label} must be a path string")
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise campaign.CampaignIntegrityError(f"{label} is not a portable relative path")
    return value


def _proposal_case_id(session_id: str, case_id: str, decompressed_sha256: str) -> str:
    identity = f"{session_id}\0{case_id}\0{decompressed_sha256}".encode()
    return f"release-failure-{case_id}-{_sha256(identity)[:16]}"


def _expected_resources(
    reviewer_truth: Mapping[str, object],
) -> tuple[list[dict[str, object]], bool]:
    profile = load_varrock_east_iron_profile()
    regions = {candidate.resource_id: candidate.region for candidate in profile.candidates}
    truth = reviewer_truth.get("resource_truth")
    if not isinstance(truth, list):
        raise campaign.CampaignIntegrityError("proposal reviewer resource truth changed")
    expected: list[dict[str, object]] = []
    reviewed_states: list[str] = []
    for item in truth:
        item = _strict_mapping(item, {"resource_id", "state"}, label="resource truth")
        resource_id = item.get("resource_id")
        state = item.get("state")
        if (
            not isinstance(resource_id, str)
            or resource_id not in regions
            or not isinstance(state, str)
            or state not in {"available", "depleted", "uncertain"}
        ):
            raise campaign.CampaignIntegrityError("proposal resource truth is malformed")
        reviewed_states.append(state)
    scene_validated = any(state != "uncertain" for state in reviewed_states)
    for item in truth:
        item = cast(dict[str, object], item)
        resource_id = cast(str, item["resource_id"])
        state = cast(str, item["state"])
        available: bool | None
        interaction_region: list[int] | None
        if not scene_validated or state == "uncertain":
            available = None
            interaction_region = None
        elif state == "available":
            available = True
            interaction_region = list(regions[resource_id])
        else:
            available = False
            interaction_region = None
        expected.append(
            {
                "resource_id": resource_id,
                "state": state,
                "available": available,
                "interaction_region": interaction_region,
            }
        )
    return expected, scene_validated


def _authority() -> dict[str, object]:
    return {
        "proposal_only": True,
        "adopted": False,
        "permanent_regression": False,
        "approval_authority": False,
        "promotion_allowed": False,
        "release_eligible": False,
        "activation_allowed": False,
        "input_authority": False,
    }


def _policy_lock(profile: Mapping[str, object]) -> dict[str, object]:
    required = {
        "detector_id",
        "detector_version",
        "profile_id",
        "profile_schema_version",
        "profile_sha256",
        "location_id",
        "frame_width",
        "frame_height",
        "pixel_format",
        "resource_ids",
        "landmark_quorum",
        "landmark_zones",
    }
    if set(profile) != required:
        raise campaign.CampaignIntegrityError("proposal profile identity fields changed")
    return {
        "detector_id": profile["detector_id"],
        "detector_version": profile["detector_version"],
        "profile_id": profile["profile_id"],
        "profile_schema_version": profile["profile_schema_version"],
        "profile_sha256": profile["profile_sha256"],
        "location_id": profile["location_id"],
        "landmark_quorum": profile["landmark_quorum"],
        "landmark_zones": profile["landmark_zones"],
        "threshold_change_requested": False,
        "quorum_change_requested": False,
        "zone_change_requested": False,
        "scene_authority_change_requested": False,
    }


def _proposal_json(
    *,
    candidate: Mapping[str, object],
    binding: Mapping[str, object],
    source: Mapping[str, object],
    copied_path: str,
    gzip_sha256: str,
    decompressed_sha256: str,
) -> dict[str, object]:
    reviewer_truth = cast(dict[str, object], binding["reviewer_truth"])
    expected_resources, scene_validated = _expected_resources(reviewer_truth)
    frame = cast(dict[str, object], cast(dict[str, object], binding["capture"])["frame"])
    profile = cast(dict[str, object], source["profile"])
    expected_definitive = [
        item["resource_id"]
        for item in expected_resources
        if scene_validated and item["available"] is not None
    ]
    expected_actionable = [
        item["resource_id"]
        for item in expected_resources
        if scene_validated and item["available"] is True
    ]
    return {
        "schema_version": 1,
        "proposal_id": _PREPARATION_ID,
        "ordinal": candidate["ordinal"],
        "case_id": candidate["case_id"],
        "blocker_id": candidate["blocker_id"],
        "fixture_input": {
            "status": "PREPARED_NOT_ADOPTED",
            "target_dataset_id": candidate["target_dataset_id"],
            "proposed_case_id": _proposal_case_id(
                cast(str, source["session_id"]),
                cast(str, candidate["case_id"]),
                decompressed_sha256,
            ),
            "frame": {
                "path": copied_path,
                "width": frame["width"],
                "height": frame["height"],
                "pixel_format": frame["pixel_format"],
                "gzip_sha256": gzip_sha256,
                "decompressed_sha256": decompressed_sha256,
            },
            "privacy": {
                "source_mode": "sanitized-frame",
                "contains_private_full_frame": False,
                "privacy_review_confirmed": True,
            },
        },
        "evaluator_input": {
            "evaluator_kind": "varrock-east-resource-production-state-v1",
            "detector_id": profile["detector_id"],
            "detector_version": profile["detector_version"],
            "profile_id": profile["profile_id"],
            "profile_sha256": profile["profile_sha256"],
            "profile_schema_version": profile["profile_schema_version"],
            "location_id": profile["location_id"],
            "reviewer_meaning": reviewer_truth["meaning"],
            "expected_scene_validated": scene_validated,
            "expected_resources": expected_resources,
            "expected_definitive_target_ids": expected_definitive,
            "expected_actionable_target_ids": expected_actionable,
            "current_production_snapshot": binding["production_snapshot"],
            "current_release_reasons": candidate["release_reasons"],
            "confidence_bounds_proposed": False,
        },
        "source_bindings": {
            "followup_case_review_sha256": candidate["case_review_sha256"],
            "case_hashes": binding["hashes"],
            "completion_seal_sha256": source["completion_seal_sha256"],
            "reviewer_truth": reviewer_truth,
            "capture_frame": frame,
            "release_result": binding["release_result"],
            "evidence_origin": _SOURCE_ORIGIN,
        },
        "policy_lock": _policy_lock(profile),
        "authority": _authority(),
    }


def _selected_candidates(inputs: Mapping[str, object]) -> tuple[list[dict[str, object]], list[str]]:
    promotion = _strict_mapping(
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
        label="follow-up failure promotion",
    )
    candidates = promotion.get("candidates")
    if not isinstance(candidates, list):
        raise campaign.CampaignIntegrityError("follow-up failure candidates changed")
    selected: list[dict[str, object]] = []
    metadata_only: list[str] = []
    for raw in candidates:
        if not isinstance(raw, dict):
            raise campaign.CampaignIntegrityError("follow-up failure candidate changed")
        disposition = raw.get("disposition")
        if disposition == "METADATA_ONLY_NO_PIXELS":
            if raw.get("replay_candidate") is not False:
                raise campaign.CampaignIntegrityError("metadata-only candidate gained pixels")
            metadata_only.append(cast(str, raw["case_id"]))
            continue
        if (
            disposition != "REPLAY_CANDIDATE"
            or raw.get("replay_candidate") is not True
            or raw.get("source_owned_release_evidence") is not True
            or raw.get("promotion_complete") is not False
            or raw.get("policy_change_allowed_from_failure") is not False
            or not isinstance(raw.get("sanitized_raw_gzip"), dict)
        ):
            raise campaign.CampaignIntegrityError("non-source replay candidate was selected")
        selected.append(cast(dict[str, object], raw))
    return selected, metadata_only


def _load_snapshots(
    followup_path: Path,
    package_dir: Path,
    *,
    expected_followup_sha256: str,
    expected_package_manifest_sha256: str,
) -> tuple[
    campaign._VerifiedFollowupSnapshot,
    campaign._VerifiedReviewPackageSnapshot,
    dict[str, object],
]:
    followup = campaign._load_verified_followup_snapshot(
        followup_path, expected_sha256=expected_followup_sha256
    )
    inputs = followup.inputs
    source = cast(dict[str, object], inputs["source_snapshot"])
    if (
        inputs.get("inputs_id") != _FOLLOWUP_ID
        or source.get("manifest_sha256") != expected_package_manifest_sha256
    ):
        raise campaign.CampaignIntegrityError("independent follow-up/package roots disagree")
    package = campaign._load_verified_review_package_snapshot(
        package_dir, expected_manifest_sha256=expected_package_manifest_sha256
    )
    expected_inputs = campaign._followup_inputs_from_verified_snapshot(package)
    if (
        inputs != expected_inputs
        or followup.inputs_json != campaign._canonical_json_bytes(expected_inputs)
    ):
        raise campaign.CampaignIntegrityError(
            "follow-up inputs are not the exact projection of the rooted package"
        )
    return followup, package, inputs


def _remove_owned_tree(root: Path, owned: Sequence[Path]) -> None:
    for path in reversed(owned):
        path.unlink(missing_ok=True)
    directories = sorted(
        (path for path in root.rglob("*") if path.is_dir()),
        key=lambda item: len(item.parts),
        reverse=True,
    ) if root.exists() else []
    for directory in directories:
        try:
            directory.rmdir()
        except OSError:
            pass
    try:
        root.rmdir()
    except OSError:
        pass


def prepare_replay_promotion_proposals(
    followup_path: Path,
    package_dir: Path,
    output_dir: Path,
    *,
    expected_followup_sha256: str,
    expected_package_manifest_sha256: str,
) -> dict[str, object]:
    """Write proposal-only inputs for exact source-owned retained failures."""

    followup, package, inputs = _load_snapshots(
        followup_path,
        package_dir,
        expected_followup_sha256=expected_followup_sha256,
        expected_package_manifest_sha256=expected_package_manifest_sha256,
    )
    selected, metadata_only = _selected_candidates(inputs)
    envelope = cast(dict[str, object], inputs["c2_envelope_review_inputs"])
    retained_failures = envelope["retained_failure_case_ids"]
    if retained_failures == []:
        raise campaign.CampaignError(
            "no retained failures require replay-promotion preparation"
        )
    output = Path(output_dir).resolve(strict=False)
    source_paths = {
        Path(followup_path).resolve(strict=True),
        Path(package_dir).resolve(strict=True),
    }
    if any(output == source or output in source.parents or source in output.parents for source in source_paths):
        raise campaign.CampaignError("proposal output must be separate from rooted sources")
    try:
        output.mkdir()
    except FileExistsError:
        raise FileExistsError(f"proposal output already exists: {output}") from None
    owned: list[Path] = []
    try:
        embedded_followup = output / _EMBEDDED_FOLLOWUP_PATH
        embedded_followup_sha = campaign._write_hashed_artifact(
            embedded_followup, followup.inputs_json
        )
        owned.extend(
            (embedded_followup, campaign._artifact_sidecar(embedded_followup))
        )
        if embedded_followup_sha != followup.sha256:
            raise campaign.CampaignIntegrityError(
                "embedded follow-up differs from the externally rooted input"
            )
        promotion = cast(dict[str, object], inputs["failure_promotion_inputs"])
        nonrelease = cast(list[dict[str, object]], promotion["nonrelease_evidence"])
        bindings = cast(list[dict[str, object]], inputs["case_bindings"])
        binding_by_id = {cast(str, item["case_id"]): item for item in bindings}
        package_by_id = {item.case_id: item for item in package.cases}
        proposals: list[dict[str, object]] = []
        for candidate in selected:
            case_id = cast(str, candidate["case_id"])
            binding = binding_by_id.get(case_id)
            case_snapshot = package_by_id.get(case_id)
            if binding is None or case_snapshot is None:
                raise campaign.CampaignIntegrityError("candidate case is absent from rooted package")
            release = cast(dict[str, object], binding["release_result"])
            origin = cast(dict[str, object], binding["capture_origin"])
            raw = cast(dict[str, object], candidate["sanitized_raw_gzip"])
            if (
                release.get("passed") is not False
                or release.get("permanent_evidence_required") is not True
                or origin.get("evidence_origin") != _SOURCE_ORIGIN
                or candidate.get("case_review_sha256") != case_snapshot.case_review_sha256
                or raw.get("path") != case_snapshot.sanitized_raw_gzip_path
                or raw.get("sha256") != case_snapshot.sanitized_raw_gzip_sha256
                or raw.get("decompressed_sha256")
                != case_snapshot.sanitized_decompressed_sha256
                or case_snapshot.sanitized_raw_gzip_bytes is None
                or case_snapshot.sanitized_decompressed_sha256 is None
            ):
                raise campaign.CampaignIntegrityError("candidate source/review/pixel binding changed")
            ordinal = cast(int, candidate["ordinal"])
            stem = f"{ordinal:02d}-{case_id}"
            gzip_rel = f"cases/{stem}/sanitized-frame.raw.gz"
            gzip_path = output / gzip_rel
            gzip_sha = campaign._write_hashed_artifact(
                gzip_path, case_snapshot.sanitized_raw_gzip_bytes
            )
            owned.extend((gzip_path, campaign._artifact_sidecar(gzip_path)))
            if gzip_sha != case_snapshot.sanitized_raw_gzip_sha256:
                raise campaign.CampaignIntegrityError("copied proposal gzip hash changed")
            proposal = _proposal_json(
                candidate=candidate,
                binding=binding,
                source=cast(dict[str, object], inputs["source_snapshot"]),
                copied_path=gzip_rel,
                gzip_sha256=gzip_sha,
                decompressed_sha256=case_snapshot.sanitized_decompressed_sha256,
            )
            proposal_rel = f"cases/{stem}/proposal.json"
            proposal_path = output / proposal_rel
            proposal_sha = campaign._write_hashed_artifact(
                proposal_path, campaign._canonical_json_bytes(proposal)
            )
            owned.extend((proposal_path, campaign._artifact_sidecar(proposal_path)))
            proposals.append(
                {
                    "ordinal": ordinal,
                    "case_id": case_id,
                    "proposal_path": proposal_rel,
                    "proposal_sha256": proposal_sha,
                    "gzip_path": gzip_rel,
                    "gzip_sha256": gzip_sha,
                    "decompressed_sha256": case_snapshot.sanitized_decompressed_sha256,
                }
            )
        source = cast(dict[str, object], inputs["source_snapshot"])
        manifest = {
            "schema_version": 1,
            "preparation_id": _PREPARATION_ID,
            "configuration_id": _CONFIGURATION_ID,
            "source": {
                "followup_inputs_id": inputs["inputs_id"],
                "followup_configuration_id": inputs["configuration_id"],
                "followup_path": _EMBEDDED_FOLLOWUP_PATH,
                "followup_sha256": followup.sha256,
                "package_manifest_sha256": package.manifest_sha256,
                "release_summary_sha256": source["release_summary_sha256"],
                "completion_seal_sha256": source["completion_seal_sha256"],
                "campaign_id": source["campaign_id"],
                "campaign_version": source["campaign_version"],
                "session_id": source["session_id"],
                "repository": source["repository"],
                "profile": source["profile"],
            },
            "selection": {
                "authority": "derived-only-from-externally-rooted-followup",
                "retained_failure_case_ids": cast(
                    dict[str, object], inputs["c2_envelope_review_inputs"]
                )["retained_failure_case_ids"],
                "preparable_case_ids": [item["case_id"] for item in proposals],
                "metadata_only_case_ids": metadata_only,
                "excluded_nonrelease_case_ids": [
                    item["case_id"] for item in nonrelease
                ],
                "caller_selected_case_ids": [],
            },
            "proposals": proposals,
            "policy_lock": _policy_lock(cast(dict[str, object], source["profile"])),
            "authority": _authority(),
            "manifest_written_last": True,
        }
        manifest_path = output / _MANIFEST_NAME
        manifest_sha = campaign._write_hashed_artifact(
            manifest_path, campaign._canonical_json_bytes(manifest)
        )
        owned.extend((manifest_path, campaign._artifact_sidecar(manifest_path)))
    except Exception:
        _remove_owned_tree(output, owned)
        raise
    return {
        "proposal_dir": str(output),
        "manifest_sha256": manifest_sha,
        "proposal_count": len(proposals),
        "metadata_only_count": len(metadata_only),
        "adopted": False,
        "promotion_allowed": False,
        "activation_allowed": False,
    }


def verify_replay_promotion_proposals(
    proposal_dir: Path,
    *,
    expected_manifest_sha256: str,
) -> dict[str, object]:
    """Strictly verify a proposal directory against an externally retained root."""

    supplied_root = Path(proposal_dir)
    if supplied_root.is_symlink():
        raise campaign.CampaignIntegrityError(
            "proposal root must be a real directory and cannot be a symlink"
        )
    root = supplied_root.resolve(strict=True)
    if not root.is_dir() or root.is_symlink():
        raise campaign.CampaignIntegrityError("proposal root must be a real directory")
    manifest_payload, manifest_sha = campaign._verify_hashed_artifact(
        root / _MANIFEST_NAME,
        expected=expected_manifest_sha256,
        maximum_bytes=_MAX_JSON_BYTES,
    )
    manifest = campaign._strict_json_bytes(manifest_payload, label="proposal manifest")
    _strict_mapping(
        manifest,
        {
            "schema_version",
            "preparation_id",
            "configuration_id",
            "source",
            "selection",
            "proposals",
            "policy_lock",
            "authority",
            "manifest_written_last",
        },
        label="proposal manifest",
    )
    if (
        not campaign._is_strict_int(manifest.get("schema_version"))
        or manifest.get("schema_version") != 1
        or manifest.get("preparation_id") != _PREPARATION_ID
        or manifest.get("configuration_id") != _CONFIGURATION_ID
        or manifest.get("authority") != _authority()
        or manifest.get("manifest_written_last") is not True
    ):
        raise campaign.CampaignIntegrityError("proposal identity/authority changed")
    source = _strict_mapping(
        manifest.get("source"),
        {
            "followup_inputs_id",
            "followup_configuration_id",
            "followup_path",
            "followup_sha256",
            "package_manifest_sha256",
            "release_summary_sha256",
            "completion_seal_sha256",
            "campaign_id",
            "campaign_version",
            "session_id",
            "repository",
            "profile",
        },
        label="proposal source",
    )
    source_profile = source.get("profile")
    repository = source.get("repository")
    hash_fields = (
        "followup_sha256",
        "package_manifest_sha256",
        "release_summary_sha256",
        "completion_seal_sha256",
    )
    if (
        source.get("followup_inputs_id") != _FOLLOWUP_ID
        or source.get("followup_configuration_id")
        != campaign._FOLLOWUP_CONFIGURATION_ID
        or source.get("campaign_id") != campaign.RESOURCE_RELEASE_CAMPAIGN_ID
        or source.get("campaign_version") != campaign.RESOURCE_RELEASE_CAMPAIGN_VERSION
        or any(
            not isinstance(source.get(name), str)
            or not campaign._SHA256_PATTERN.fullmatch(cast(str, source[name]))
            for name in hash_fields
        )
        or not isinstance(source.get("session_id"), str)
        or not campaign._IDENTIFIER_PATTERN.fullmatch(cast(str, source["session_id"]))
        or source_profile != campaign._profile_identity()
        or not isinstance(repository, dict)
        or set(repository) != {"head_sha", "branch", "clean"}
        or not isinstance(repository.get("head_sha"), str)
        or not campaign._GIT_SHA_PATTERN.fullmatch(cast(str, repository["head_sha"]))
        or not isinstance(repository.get("branch"), str)
        or not cast(str, repository["branch"]).strip()
        or repository.get("clean") is not True
    ):
        raise campaign.CampaignIntegrityError("proposal follow-up identity changed")
    followup_rel = _portable_path(
        source.get("followup_path"), label="embedded follow-up path"
    )
    if followup_rel != _EMBEDDED_FOLLOWUP_PATH:
        raise campaign.CampaignIntegrityError("embedded follow-up path changed")
    followup_resolved = (root / followup_rel).resolve()
    if not followup_resolved.is_relative_to(root):
        raise campaign.CampaignIntegrityError("embedded follow-up escapes proposal root")
    followup_payload, embedded_followup_sha = campaign._verify_hashed_artifact(
        root / followup_rel,
        expected=cast(str, source["followup_sha256"]),
        maximum_bytes=_MAX_JSON_BYTES,
    )
    followup_inputs = campaign._strict_json_bytes(
        followup_payload, label="embedded follow-up inputs"
    )
    campaign._validate_release_followup_inputs(followup_inputs)
    if followup_payload != campaign._canonical_json_bytes(followup_inputs):
        raise campaign.CampaignIntegrityError("embedded follow-up is not canonical JSON")
    followup_source = cast(dict[str, object], followup_inputs["source_snapshot"])
    if (
        embedded_followup_sha != source["followup_sha256"]
        or followup_inputs["inputs_id"] != source["followup_inputs_id"]
        or followup_inputs["configuration_id"]
        != source["followup_configuration_id"]
        or followup_source["manifest_sha256"]
        != source["package_manifest_sha256"]
        or followup_source["release_summary_sha256"]
        != source["release_summary_sha256"]
        or followup_source["completion_seal_sha256"]
        != source["completion_seal_sha256"]
        or followup_source["campaign_id"] != source["campaign_id"]
        or followup_source["campaign_version"] != source["campaign_version"]
        or followup_source["session_id"] != source["session_id"]
        or followup_source["repository"] != source["repository"]
        or followup_source["profile"] != source["profile"]
    ):
        raise campaign.CampaignIntegrityError(
            "embedded follow-up/source manifest binding changed"
        )
    if manifest.get("policy_lock") != _policy_lock(cast(dict[str, object], source_profile)):
        raise campaign.CampaignIntegrityError("proposal policy lock changed")
    proposals = manifest.get("proposals")
    if not isinstance(proposals, list):
        raise campaign.CampaignIntegrityError("proposal list changed")
    actual_files = {
        _MANIFEST_NAME,
        f"{_MANIFEST_NAME}.sha256",
        followup_rel,
        f"{followup_rel}.sha256",
    }
    expected_directories = {"source"}
    rooted_candidates, rooted_metadata_only = _selected_candidates(followup_inputs)
    rooted_candidate_by_id = {
        cast(str, item["case_id"]): item for item in rooted_candidates
    }
    rooted_bindings = cast(list[dict[str, object]], followup_inputs["case_bindings"])
    rooted_binding_by_id = {
        cast(str, item["case_id"]): item for item in rooted_bindings
    }
    case_ids: list[str] = []
    prior_ordinal = 0
    for entry_raw in proposals:
        entry = _strict_mapping(
            entry_raw,
            {
                "ordinal",
                "case_id",
                "proposal_path",
                "proposal_sha256",
                "gzip_path",
                "gzip_sha256",
                "decompressed_sha256",
            },
            label="proposal entry",
        )
        proposal_rel = _portable_path(entry["proposal_path"], label="proposal path")
        gzip_rel = _portable_path(entry["gzip_path"], label="proposal gzip path")
        ordinal = entry.get("ordinal")
        case_id = entry.get("case_id")
        if (
            not isinstance(ordinal, int)
            or isinstance(ordinal, bool)
            or ordinal <= prior_ordinal
            or not isinstance(case_id, str)
            or not case_id
            or case_id in case_ids
            or proposal_rel != f"cases/{ordinal:02d}-{case_id}/proposal.json"
            or gzip_rel != f"cases/{ordinal:02d}-{case_id}/sanitized-frame.raw.gz"
        ):
            raise campaign.CampaignIntegrityError("proposal order/path identity changed")
        plan_case = next(
            (item for item in campaign.CAMPAIGN_PLAN if item.case_id == case_id), None
        )
        if plan_case is None or plan_case.ordinal != ordinal:
            raise campaign.CampaignIntegrityError("proposal campaign identity changed")
        prior_ordinal = ordinal
        proposal_resolved = (root / proposal_rel).resolve()
        gzip_resolved = (root / gzip_rel).resolve()
        if not proposal_resolved.is_relative_to(root) or not gzip_resolved.is_relative_to(root):
            raise campaign.CampaignIntegrityError("proposal artifact escapes proposal root")
        proposal_payload, proposal_sha = campaign._verify_hashed_artifact(
            root / proposal_rel,
            expected=cast(str, entry["proposal_sha256"]),
            maximum_bytes=_MAX_JSON_BYTES,
        )
        gzip_payload, gzip_sha = campaign._verify_hashed_artifact(
            root / gzip_rel,
            expected=cast(str, entry["gzip_sha256"]),
            maximum_bytes=(1005 * 1078 * 4) + _MAX_GZIP_OVERHEAD,
        )
        if proposal_sha != entry["proposal_sha256"] or gzip_sha != entry["gzip_sha256"]:
            raise campaign.CampaignIntegrityError("proposal entry hash changed")
        proposal = campaign._strict_json_bytes(proposal_payload, label="case proposal")
        _strict_mapping(
            proposal,
            {
                "schema_version",
                "proposal_id",
                "ordinal",
                "case_id",
                "blocker_id",
                "fixture_input",
                "evaluator_input",
                "source_bindings",
                "policy_lock",
                "authority",
            },
            label="case proposal",
        )
        if (
            not campaign._is_strict_int(proposal.get("schema_version"))
            or proposal.get("schema_version") != 1
            or proposal.get("proposal_id") != _PREPARATION_ID
            or proposal.get("case_id") != entry["case_id"]
            or proposal.get("ordinal") != entry["ordinal"]
            or proposal.get("blocker_id") != plan_case.blocker_id
            or proposal.get("authority") != _authority()
            or proposal.get("policy_lock") != manifest["policy_lock"]
        ):
            raise campaign.CampaignIntegrityError("proposal identity/authority changed")
        fixture = _strict_mapping(
            proposal.get("fixture_input"),
            {"status", "target_dataset_id", "proposed_case_id", "frame", "privacy"},
            label="fixture proposal",
        )
        frame = _strict_mapping(
            fixture.get("frame"),
            {
                "path",
                "width",
                "height",
                "pixel_format",
                "gzip_sha256",
                "decompressed_sha256",
            },
            label="fixture frame",
        )
        if (
            fixture.get("status") != "PREPARED_NOT_ADOPTED"
            or fixture.get("target_dataset_id") != _TARGET_DATASET_ID
            or fixture.get("proposed_case_id")
            != _proposal_case_id(
                cast(str, source["session_id"]),
                case_id,
                cast(str, entry["decompressed_sha256"]),
            )
            or frame.get("path") != gzip_rel
            or frame.get("gzip_sha256") != gzip_sha
            or frame.get("decompressed_sha256") != entry["decompressed_sha256"]
            or fixture.get("privacy")
            != {
                "source_mode": "sanitized-frame",
                "contains_private_full_frame": False,
                "privacy_review_confirmed": True,
            }
        ):
            raise campaign.CampaignIntegrityError("case fixture proposal changed")
        width = frame.get("width")
        height = frame.get("height")
        source_profile = cast(dict[str, object], source_profile)
        runtime_profile = load_varrock_east_iron_profile()
        if (
            width != runtime_profile.frame_width
            or height != runtime_profile.frame_height
            or frame.get("pixel_format") != runtime_profile.pixel_format.value
        ):
            raise campaign.CampaignIntegrityError("proposal frame geometry changed")
        assert isinstance(width, int)
        assert isinstance(height, int)
        pixels = campaign._bounded_gzip_decompress(
            gzip_payload,
            expected_size=width * height * 4,
            label=f"proposal {entry['case_id']}",
        )
        if (
            gzip_payload != campaign._deterministic_gzip(pixels)
            or _sha256(pixels) != entry["decompressed_sha256"]
        ):
            raise campaign.CampaignIntegrityError("proposal gzip content changed")
        rooted_candidate = rooted_candidate_by_id.get(case_id)
        rooted_binding = rooted_binding_by_id.get(case_id)
        if rooted_candidate is None or rooted_binding is None:
            raise campaign.CampaignIntegrityError(
                "proposal case is not selected by the embedded follow-up"
            )
        rooted_candidate_raw = rooted_candidate.get("sanitized_raw_gzip")
        rooted_artifacts = rooted_binding.get("sanitized_artifacts")
        rooted_binding_raw = (
            rooted_artifacts.get("raw_gzip")
            if isinstance(rooted_artifacts, dict)
            else None
        )
        raw_fields = {"path", "sha256", "decompressed_sha256"}
        if (
            not isinstance(rooted_candidate_raw, dict)
            or set(rooted_candidate_raw) != raw_fields
            or not isinstance(rooted_binding_raw, dict)
            or set(rooted_binding_raw) != raw_fields
            or rooted_candidate_raw != rooted_binding_raw
            or rooted_candidate_raw.get("sha256") != gzip_sha
            or rooted_candidate_raw.get("decompressed_sha256")
            != entry.get("decompressed_sha256")
            or rooted_candidate_raw.get("decompressed_sha256") != _sha256(pixels)
        ):
            raise campaign.CampaignIntegrityError(
                "proposal rooted compressed/decompressed hash chain changed"
            )
        expected_proposal = _proposal_json(
            candidate=rooted_candidate,
            binding=rooted_binding,
            source=followup_source,
            copied_path=gzip_rel,
            gzip_sha256=gzip_sha,
            decompressed_sha256=entry["decompressed_sha256"],
        )
        if proposal != expected_proposal:
            raise campaign.CampaignIntegrityError(
                "proposal source binding/evaluator changed"
            )
        source_bindings = _strict_mapping(
            proposal.get("source_bindings"),
            {
                "followup_case_review_sha256",
                "case_hashes",
                "completion_seal_sha256",
                "reviewer_truth",
                "capture_frame",
                "release_result",
                "evidence_origin",
            },
            label="proposal source bindings",
        )
        if (
            source_bindings.get("completion_seal_sha256")
            != source.get("completion_seal_sha256")
            or source_bindings.get("evidence_origin") != _SOURCE_ORIGIN
        ):
            raise campaign.CampaignIntegrityError("proposal source binding changed")
        reviewer_truth = source_bindings.get("reviewer_truth")
        if not isinstance(reviewer_truth, dict):
            raise campaign.CampaignIntegrityError("proposal reviewer truth changed")
        case_hashes = source_bindings.get("case_hashes")
        capture_frame = source_bindings.get("capture_frame")
        release_result = source_bindings.get("release_result")
        if (
            not isinstance(case_hashes, dict)
            or not isinstance(capture_frame, dict)
            or not isinstance(release_result, dict)
            or source_bindings.get("followup_case_review_sha256")
            != case_hashes.get("case_review_sha256")
            or release_result.get("case_id") != case_id
            or release_result.get("passed") is not False
            or release_result.get("permanent_evidence_required") is not True
            or release_result.get("policy_change_allowed_from_failure") is not False
            or release_result.get("evidence_origin") != _SOURCE_ORIGIN
        ):
            raise campaign.CampaignIntegrityError("proposal source evidence changed")
        case_hash_field_names = {
            "case_review_sha256",
            "capture_report_sha256",
            "review_preparation_sha256",
            "review_truth_sha256",
            "private_raw_sha256",
            "sanitized_raw_gzip_sha256",
            "sanitized_preview_sha256",
        }
        release_fields = {
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
        capture_fields = {
            "frame_id",
            "captured_monotonic_s",
            "width",
            "height",
            "pixel_format",
        }
        reasons = release_result.get("reasons")
        replay_candidate = release_result.get("replay_regression_candidate")
        if (
            set(case_hashes) != case_hash_field_names
            or set(release_result) != release_fields
            or set(capture_frame) != capture_fields
            or any(
                not isinstance(case_hashes.get(name), str)
                or not campaign._SHA256_PATTERN.fullmatch(
                    cast(str, case_hashes[name])
                )
                for name in case_hash_field_names
            )
            or case_hashes.get("sanitized_raw_gzip_sha256") != gzip_sha
            or gzip_sha != entry.get("gzip_sha256")
            or frame.get("gzip_sha256") != gzip_sha
            or not isinstance(reasons, list)
            or any(not isinstance(reason, str) or not reason for reason in reasons)
            or release_result.get("ordinal") != ordinal
            or release_result.get("blocker_id") != plan_case.blocker_id
            or not isinstance(replay_candidate, dict)
            or set(replay_candidate) != {"path", "sha256"}
            or replay_candidate.get("sha256") != gzip_sha
            or not isinstance(replay_candidate.get("path"), str)
            or capture_frame.get("width") != frame.get("width")
            or capture_frame.get("height") != frame.get("height")
            or capture_frame.get("pixel_format") != frame.get("pixel_format")
        ):
            raise campaign.CampaignIntegrityError(
                "proposal case/hash/release identity changed"
            )
        campaign._validated_frame_scalars(capture_frame, label="proposal capture frame")
        artifacts = {"mode": "sanitized-frame"}
        decision = campaign._followup_review_decision(
            plan_case, reviewer_truth, case_hashes, artifacts
        )
        evaluator = _strict_mapping(
            proposal.get("evaluator_input"),
            {
                "evaluator_kind",
                "detector_id",
                "detector_version",
                "profile_id",
                "profile_sha256",
                "profile_schema_version",
                "location_id",
                "reviewer_meaning",
                "expected_scene_validated",
                "expected_resources",
                "expected_definitive_target_ids",
                "expected_actionable_target_ids",
                "current_production_snapshot",
                "current_release_reasons",
                "confidence_bounds_proposed",
            },
            label="evaluator proposal",
        )
        expected_resources, expected_scene = _expected_resources(reviewer_truth)
        expected_definitive = [
            item["resource_id"]
            for item in expected_resources
            if expected_scene and item["available"] is not None
        ]
        expected_actionable = [
            item["resource_id"]
            for item in expected_resources
            if expected_scene and item["available"] is True
        ]
        if (
            evaluator.get("evaluator_kind")
            != "varrock-east-resource-production-state-v1"
            or evaluator.get("detector_id") != source_profile.get("detector_id")
            or evaluator.get("detector_version") != source_profile.get("detector_version")
            or evaluator.get("profile_id") != source_profile.get("profile_id")
            or evaluator.get("profile_sha256") != source_profile.get("profile_sha256")
            or evaluator.get("profile_schema_version")
            != source_profile.get("profile_schema_version")
            or evaluator.get("location_id") != source_profile.get("location_id")
            or evaluator.get("reviewer_meaning") != reviewer_truth.get("meaning")
            or evaluator.get("expected_scene_validated") is not expected_scene
            or evaluator.get("expected_resources") != expected_resources
            or evaluator.get("expected_definitive_target_ids") != expected_definitive
            or evaluator.get("expected_actionable_target_ids") != expected_actionable
            or not isinstance(evaluator.get("current_production_snapshot"), dict)
            or evaluator.get("current_release_reasons") != reasons
            or evaluator.get("confidence_bounds_proposed") is not False
        ):
            raise campaign.CampaignIntegrityError(
                "proposal source binding/evaluator changed"
            )
        public_frame = campaign._public_frame(capture_frame, pixels)
        campaign._verify_opaque_fixed_ui(public_frame)
        try:
            replayed: object = campaign._production_json(public_frame)
        except Exception as exc:
            replayed = campaign._detector_error(exc)
        if not campaign._production_equivalent(
            evaluator["current_production_snapshot"],
            campaign._public_production(replayed),
        ):
            raise campaign.CampaignIntegrityError(
                "proposal production snapshot is not an exact sanitized replay"
            )
        campaign._validate_followup_production_snapshot(
            plan_case,
            evaluator["current_production_snapshot"],
            capture_frame=capture_frame,
            decision=decision,
            release_result=release_result,
            artifacts=artifacts,
        )
        case_ids.append(case_id)
        actual_files.update(
            {proposal_rel, f"{proposal_rel}.sha256", gzip_rel, f"{gzip_rel}.sha256"}
        )
        expected_directories.update(
            {"cases", f"cases/{ordinal:02d}-{case_id}"}
        )
    selection = _strict_mapping(
        manifest.get("selection"),
        {
            "authority",
            "retained_failure_case_ids",
            "preparable_case_ids",
            "metadata_only_case_ids",
            "excluded_nonrelease_case_ids",
            "caller_selected_case_ids",
        },
        label="proposal selection",
    )
    metadata_only = selection.get("metadata_only_case_ids")
    excluded = selection.get("excluded_nonrelease_case_ids")
    retained = selection.get("retained_failure_case_ids")
    rooted_promotion = cast(
        dict[str, object], followup_inputs["failure_promotion_inputs"]
    )
    rooted_nonrelease = cast(
        list[dict[str, object]], rooted_promotion["nonrelease_evidence"]
    )
    rooted_excluded = [item["case_id"] for item in rooted_nonrelease]
    rooted_retained = cast(
        dict[str, object], followup_inputs["c2_envelope_review_inputs"]
    )["retained_failure_case_ids"]
    if (
        selection.get("authority") != "derived-only-from-externally-rooted-followup"
        or selection.get("preparable_case_ids") != case_ids
        or not isinstance(metadata_only, list)
        or not isinstance(excluded, list)
        or not isinstance(retained, list)
        or any(not isinstance(item, str) for item in (*metadata_only, *excluded, *retained))
        or len(set((*case_ids, *metadata_only, *excluded)))
        != len(case_ids) + len(metadata_only) + len(excluded)
        or retained
        != rooted_retained
        or case_ids != [item["case_id"] for item in rooted_candidates]
        or metadata_only != rooted_metadata_only
        or excluded != rooted_excluded
        or selection.get("caller_selected_case_ids") != []
    ):
        raise campaign.CampaignIntegrityError("proposal selection changed")
    observed: set[str] = set()
    observed_directories: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise campaign.CampaignIntegrityError("proposal directory contains symlinks")
        if path.is_file():
            observed.add(path.relative_to(root).as_posix())
        elif path.is_dir():
            observed_directories.add(path.relative_to(root).as_posix())
    if observed != actual_files or observed_directories != expected_directories:
        raise campaign.CampaignIntegrityError("proposal directory contains missing/foreign files")
    return {
        "proposal_dir": str(root),
        "manifest_sha256": manifest_sha,
        "proposal_count": len(proposals),
        "verified": True,
        "adopted": False,
        "promotion_allowed": False,
        "activation_allowed": False,
    }
