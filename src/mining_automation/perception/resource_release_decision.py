"""Deny-only C2 resource release-decision preparation.

The artifact produced here is a source-review packet, not a release receipt.  It
binds externally rooted C1 and replay-proposal evidence, projects the narrow
candidate envelope, and enumerates every unresolved external decision.  No
input accepted by this module can approve an envelope, adopt a fixture, or
grant runtime authority.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final, cast

from . import resource_release_campaign as campaign
from . import resource_replay_promotion as replay_promotion

__all__ = [
    "prepare_resource_release_decision",
    "verify_resource_release_decision",
]

_DECISION_ID: Final[str] = "resource-release-decision-readiness-v1"
_CONFIGURATION_ID: Final[str] = (
    "resource-release-decision:varrock-east-iron-v1@1.0.0"
)
_PROPOSED_RECORD_ID: Final[str] = (
    "resource-release-record:varrock-east-iron-v1@1.0.0-proposal"
)
_PROPOSAL_PREPARATION_ID: Final[str] = (
    "resource-release-replay-promotion-preparation-v1"
)
_ACCEPTED_A1_PR: Final[int] = 49
_ACCEPTED_A1_HEAD: Final[str] = "86090c93046ce584652f11fce1c49d59b5988754"
_MAX_DECISION_BYTES: Final[int] = 64 * 1024 * 1024


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _strict_mapping(
    value: object,
    fields: set[str],
    *,
    label: str,
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise campaign.CampaignIntegrityError(f"{label} fields changed")
    return cast(dict[str, object], value)


def _authority() -> dict[str, object]:
    return {
        "proposal_only": True,
        "source_release_record_granted": False,
        "envelope_approved": False,
        "permanent_replay_adoption_allowed": False,
        "approval_authority": False,
        "release_eligible": False,
        "activation_allowed": False,
        "world_state_authority": False,
        "controller_authority": False,
        "input_authority": False,
    }


def _source_binding_plan(*, has_preparable_proposals: bool) -> dict[str, object]:
    required = [
        "detector_source_blob",
        "packaged_profile_blob",
        "existing_reviewed_resource_dataset_manifest_blob",
    ]
    if has_preparable_proposals:
        required.extend(
            (
                "promoted_failure_payload_blobs",
                "promoted_failure_evaluator_test_blobs",
            )
        )
    required.append("source_release_record_external_commit_or_tree_binding")
    return {
        "status": "PENDING_REVIEWED_SOURCE_COMMIT",
        "required_exact_git_bindings": required,
        "provided_git_bindings": [],
        "caller_may_supply_bindings": False,
        "binding_complete": False,
    }


def _proposal_authority() -> dict[str, object]:
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


def _validate_embedded_proposal_manifest(
    manifest: Mapping[str, object],
    *,
    followup: Mapping[str, object],
    expected_followup_sha256: str,
    expected_manifest_sha256: str,
) -> None:
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
        label="embedded replay proposal manifest",
    )
    if (
        not campaign._is_strict_int(manifest.get("schema_version"))
        or manifest.get("schema_version") != 1
        or manifest.get("preparation_id") != _PROPOSAL_PREPARATION_ID
        or manifest.get("configuration_id")
        != replay_promotion._CONFIGURATION_ID
        or manifest.get("authority") != _proposal_authority()
        or manifest.get("manifest_written_last") is not True
    ):
        raise campaign.CampaignIntegrityError(
            "embedded replay proposal identity/authority changed"
        )
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
        label="embedded replay proposal source",
    )
    followup_source = cast(dict[str, object], followup["source_snapshot"])
    source_evidence = cast(dict[str, object], followup["c2_envelope_review_inputs"])
    if (
        source.get("followup_inputs_id") != followup["inputs_id"]
        or source.get("followup_configuration_id") != followup["configuration_id"]
        or source.get("package_manifest_sha256")
        != followup_source["manifest_sha256"]
        or source.get("release_summary_sha256")
        != followup_source["release_summary_sha256"]
        or source.get("completion_seal_sha256")
        != followup_source["completion_seal_sha256"]
        or source.get("campaign_id") != followup_source["campaign_id"]
        or source.get("campaign_version") != followup_source["campaign_version"]
        or source.get("session_id") != followup_source["session_id"]
        or source.get("repository") != followup_source["repository"]
        or source.get("profile") != followup_source["profile"]
        or source.get("followup_path") != replay_promotion._EMBEDDED_FOLLOWUP_PATH
        or source.get("followup_sha256") != expected_followup_sha256
        or not campaign._SHA256_PATTERN.fullmatch(expected_followup_sha256)
        or not isinstance(expected_manifest_sha256, str)
        or not campaign._SHA256_PATTERN.fullmatch(expected_manifest_sha256)
    ):
        raise campaign.CampaignIntegrityError(
            "embedded replay proposal source binding changed"
        )
    if manifest.get("policy_lock") != replay_promotion._policy_lock(
        cast(dict[str, object], followup_source["profile"])
    ):
        raise campaign.CampaignIntegrityError(
            "embedded replay proposal policy lock changed"
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
        label="embedded replay proposal selection",
    )
    retained = cast(list[str], source_evidence["retained_failure_case_ids"])
    rooted_candidates, rooted_metadata_only = replay_promotion._selected_candidates(
        followup
    )
    rooted_preparable = [cast(str, item["case_id"]) for item in rooted_candidates]
    failure_inputs = cast(dict[str, object], followup["failure_promotion_inputs"])
    rooted_nonrelease = cast(
        list[dict[str, object]], failure_inputs["nonrelease_evidence"]
    )
    rooted_excluded = [cast(str, item["case_id"]) for item in rooted_nonrelease]
    preparable = selection.get("preparable_case_ids")
    metadata_only = selection.get("metadata_only_case_ids")
    excluded = selection.get("excluded_nonrelease_case_ids")
    proposal_entries = manifest.get("proposals")
    if (
        selection.get("authority")
        != "derived-only-from-externally-rooted-followup"
        or selection.get("retained_failure_case_ids") != retained
        or selection.get("caller_selected_case_ids") != []
        or not isinstance(preparable, list)
        or not isinstance(metadata_only, list)
        or not isinstance(excluded, list)
        or not isinstance(proposal_entries, list)
        or any(
            not isinstance(item, str) or not item
            for item in (*preparable, *metadata_only, *excluded)
        )
        or len(set((*preparable, *metadata_only, *excluded)))
        != len(preparable) + len(metadata_only) + len(excluded)
        or [
            entry.get("case_id") if isinstance(entry, dict) else None
            for entry in proposal_entries
        ]
        != preparable
        or preparable != rooted_preparable
        or metadata_only != rooted_metadata_only
        or excluded != rooted_excluded
        or set(retained) != set((*preparable, *metadata_only, *excluded))
    ):
        raise campaign.CampaignIntegrityError(
            "embedded replay proposal selection changed"
        )
    rooted_bindings = cast(list[dict[str, object]], followup["case_bindings"])
    binding_by_id = {
        cast(str, item["case_id"]): item for item in rooted_bindings
    }
    for rooted_candidate, entry in zip(
        rooted_candidates,
        proposal_entries,
        strict=True,
    ):
        exact = _strict_mapping(
            entry,
            {
                "ordinal",
                "case_id",
                "proposal_path",
                "proposal_sha256",
                "gzip_path",
                "gzip_sha256",
                "decompressed_sha256",
            },
            label="embedded replay proposal entry",
        )
        ordinal = rooted_candidate["ordinal"]
        case_id = rooted_candidate["case_id"]
        proposal_path = f"cases/{cast(int, ordinal):02d}-{case_id}/proposal.json"
        gzip_path = f"cases/{cast(int, ordinal):02d}-{case_id}/sanitized-frame.raw.gz"
        raw_binding = _strict_mapping(
            rooted_candidate.get("sanitized_raw_gzip"),
            {"path", "sha256", "decompressed_sha256"},
            label="embedded rooted replay gzip",
        )
        gzip_sha = raw_binding["sha256"]
        decompressed_sha = raw_binding["decompressed_sha256"]
        expected_source_path = (
            f"cases/{cast(int, ordinal):03d}-{case_id}/sanitized-frame.raw.gz"
        )
        binding = binding_by_id.get(cast(str, case_id))
        if binding is None:
            raise campaign.CampaignIntegrityError(
                "embedded replay proposal case binding changed"
            )
        binding_artifacts = cast(dict[str, object], binding["sanitized_artifacts"])
        binding_hashes = cast(dict[str, object], binding["hashes"])
        if (
            raw_binding.get("path") != expected_source_path
            or not isinstance(gzip_sha, str)
            or not campaign._SHA256_PATTERN.fullmatch(gzip_sha)
            or not isinstance(decompressed_sha, str)
            or not campaign._SHA256_PATTERN.fullmatch(decompressed_sha)
            or binding_artifacts.get("mode") != "sanitized-frame"
            or binding_artifacts.get("raw_gzip") != raw_binding
            or binding_hashes.get("sanitized_raw_gzip_sha256") != gzip_sha
        ):
            raise campaign.CampaignIntegrityError(
                "embedded rooted replay gzip binding changed"
            )
        expected_proposal = replay_promotion._proposal_json(
            candidate=rooted_candidate,
            binding=binding,
            source=followup_source,
            copied_path=gzip_path,
            gzip_sha256=gzip_sha,
            decompressed_sha256=decompressed_sha,
        )
        expected_proposal_sha = _sha256(
            campaign._canonical_json_bytes(expected_proposal)
        )
        if (
            not campaign._is_strict_int(exact.get("ordinal"))
            or exact.get("ordinal") != ordinal
            or exact.get("case_id") != case_id
            or exact.get("proposal_path") != proposal_path
            or exact.get("gzip_path") != gzip_path
            or exact.get("proposal_sha256") != expected_proposal_sha
            or exact.get("gzip_sha256") != gzip_sha
            or exact.get("decompressed_sha256") != decompressed_sha
            or any(
                not isinstance(exact.get(field), str)
                or not campaign._SHA256_PATTERN.fullmatch(cast(str, exact[field]))
                for field in (
                    "proposal_sha256",
                    "gzip_sha256",
                    "decompressed_sha256",
                )
            )
        ):
            raise campaign.CampaignIntegrityError(
                "embedded replay proposal entry identity changed"
            )


def _load_proposal_snapshot(
    proposal_dir: Path | None,
    *,
    expected_manifest_sha256: str | None,
    followup: Mapping[str, object],
    expected_followup_sha256: str,
) -> tuple[bytes | None, dict[str, object] | None, str | None]:
    retained = cast(
        list[str],
        cast(dict[str, object], followup["c2_envelope_review_inputs"])[
            "retained_failure_case_ids"
        ],
    )
    if (proposal_dir is None) != (expected_manifest_sha256 is None):
        raise campaign.CampaignError(
            "proposal directory and retained proposal root must be supplied together"
        )
    if proposal_dir is None:
        if retained:
            raise campaign.CampaignIntegrityError(
                "retained failures require an externally rooted replay proposal"
            )
        return None, None, None
    if not retained:
        raise campaign.CampaignIntegrityError(
            "a replay proposal cannot be attached when retained failures are absent"
        )
    assert expected_manifest_sha256 is not None
    verified = replay_promotion.verify_replay_promotion_proposals(
        proposal_dir,
        expected_manifest_sha256=expected_manifest_sha256,
    )
    root = Path(proposal_dir).resolve(strict=True)
    payload, digest = campaign._verify_hashed_artifact(
        root / replay_promotion._MANIFEST_NAME,
        expected=expected_manifest_sha256,
        maximum_bytes=campaign._MAX_FOLLOWUP_JSON_BYTES,
    )
    manifest = campaign._strict_json_bytes(
        payload, label="rooted replay proposal manifest"
    )
    if (
        verified.get("verified") is not True
        or verified.get("manifest_sha256") != digest
        or digest != expected_manifest_sha256
    ):
        raise campaign.CampaignIntegrityError(
            "replay proposal verification/root changed"
        )
    _validate_embedded_proposal_manifest(
        manifest,
        followup=followup,
        expected_followup_sha256=expected_followup_sha256,
        expected_manifest_sha256=digest,
    )
    return payload, manifest, digest


def _candidate_envelope(followup: Mapping[str, object]) -> dict[str, object]:
    source = cast(dict[str, object], followup["source_snapshot"])
    inputs = cast(dict[str, object], followup["c2_envelope_review_inputs"])
    required_frame = cast(dict[str, object], inputs["required_frame"])
    capture_configuration = cast(dict[str, object], source["capture_configuration"])
    c1 = cast(dict[str, object], followup["c1_result"])
    geometries = cast(list[dict[str, object]], inputs["observed_client_geometries"])
    window_classes = cast(list[str], inputs["observed_window_classes"])
    backends = cast(list[str], inputs["observed_capture_backends"])
    exact_geometry = {
        "width": required_frame["width"],
        "height": required_frame["height"],
    }
    qualifying_evidence = (
        c1["status"] == "CLOSED"
        and inputs["all_cases_source_owned"] is True
        and inputs["all_cases_match_required_frame"] is True
        and inputs["all_cases_match_required_dpi"] is True
        and geometries == [exact_geometry]
        and inputs["window_class_consistent"] is True
        and len(window_classes) == 1
        and backends == [capture_configuration["capture_backend"]]
    )
    candidate_geometry: object = geometries[0] if qualifying_evidence else None
    candidate_window_class: object = (
        window_classes[0] if qualifying_evidence else None
    )
    candidate_backend: object = backends[0] if qualifying_evidence else None
    return {
        "status": "CANDIDATE_ONLY_PENDING_INDEPENDENT_LEAD_REVIEW",
        "profile": source["profile"],
        "capture_configuration": capture_configuration,
        "required_frame": inputs["required_frame"],
        "required_reported_dpi": inputs["required_reported_dpi"],
        "observed_reported_dpis": inputs["observed_reported_dpis"],
        "observed_client_geometries": inputs["observed_client_geometries"],
        "observed_window_classes": inputs["observed_window_classes"],
        "observed_capture_backends": inputs["observed_capture_backends"],
        "candidate_reported_dpi": (
            inputs["required_reported_dpi"]
            if qualifying_evidence
            else None
        ),
        "candidate_client_geometry": candidate_geometry,
        "candidate_window_class": candidate_window_class,
        "candidate_capture_backend": candidate_backend,
        "observed_evidence_origins": inputs["observed_evidence_origins"],
        "qualifying_evidence_complete": qualifying_evidence,
        "renderer": {
            "identity": None,
            "review_status": "PENDING_EXTERNAL_RENDERER_REVIEW",
            "capture_backend_observed_identity": False,
            "caller_may_assert_identity": False,
        },
        "automatic_camera_recovery": False,
        "unsupported_or_uncertain_action": "STOP_WITH_ZERO_TARGETS",
        "approved": False,
    }


def _input_checks(
    followup: Mapping[str, object],
    *,
    proposal_manifest: Mapping[str, object] | None,
) -> dict[str, object]:
    c1 = cast(dict[str, object], followup["c1_result"])
    envelope = cast(dict[str, object], followup["c2_envelope_review_inputs"])
    retained = cast(list[str], envelope["retained_failure_case_ids"])
    source = cast(dict[str, object], followup["source_snapshot"])
    required_frame = cast(dict[str, object], envelope["required_frame"])
    exact_geometry = {
        "width": required_frame["width"],
        "height": required_frame["height"],
    }
    observed_geometries = cast(
        list[dict[str, object]], envelope["observed_client_geometries"]
    )
    capture_configuration = cast(dict[str, object], source["capture_configuration"])
    observed_backends = cast(list[str], envelope["observed_capture_backends"])
    selection = (
        None
        if proposal_manifest is None
        else cast(dict[str, object], proposal_manifest["selection"])
    )
    preparable = (
        [] if selection is None else cast(list[str], selection["preparable_case_ids"])
    )
    metadata_only = (
        []
        if selection is None
        else cast(list[str], selection["metadata_only_case_ids"])
    )
    excluded = (
        []
        if selection is None
        else cast(list[str], selection["excluded_nonrelease_case_ids"])
    )
    return {
        "accepted_a1_packaging_checkpoint": {
            "status": "ACCEPTED_OFFLINE_NONACTIVATING",
            "pull_request": _ACCEPTED_A1_PR,
            "head_sha": _ACCEPTED_A1_HEAD,
        },
        "c1_reported_closed": c1["status"] == "CLOSED",
        "all_cases_source_owned": envelope["all_cases_source_owned"],
        "all_cases_exact_frame": envelope["all_cases_match_required_frame"],
        "exact_client_geometry_consistent": observed_geometries == [exact_geometry],
        "expected_capture_backend_consistent": observed_backends
        == [capture_configuration["capture_backend"]],
        "all_cases_reported_dpi_96": (
            envelope["all_cases_match_required_dpi"] is True
            and envelope["required_reported_dpi"] == 96
        ),
        "window_class_consistent": envelope["window_class_consistent"],
        "replay_proposal_root_verified_when_required": (
            proposal_manifest is not None if retained else proposal_manifest is None
        ),
        "permanent_replay_adoption_status": (
            "PROPOSALS_ONLY_NOT_ADOPTED" if preparable else "NOT_REQUIRED"
        ),
        "metadata_only_replay_status": (
            "UNPREPARABLE_NO_PIXELS" if metadata_only else "NOT_PRESENT"
        ),
        "nonrelease_evidence_status": (
            "EXCLUDED_FROM_RELEASE" if excluded else "NOT_PRESENT"
        ),
        "reviewer_truth_separate_from_operator_labels": True,
        "privacy_safe_package_verified": True,
        "renderer_review_complete": False,
        "envelope_lead_approval_complete": False,
        "source_release_record_granted": False,
    }


def _unresolved_conditions(
    followup: Mapping[str, object],
    *,
    proposal_manifest: Mapping[str, object] | None,
) -> list[dict[str, object]]:
    checks = _input_checks(followup, proposal_manifest=proposal_manifest)
    selection = (
        None
        if proposal_manifest is None
        else cast(dict[str, object], proposal_manifest["selection"])
    )
    preparable = (
        [] if selection is None else cast(list[str], selection["preparable_case_ids"])
    )
    metadata_only = (
        []
        if selection is None
        else cast(list[str], selection["metadata_only_case_ids"])
    )
    conditions: list[dict[str, object]] = []

    def add(condition_id: str, reason: str) -> None:
        conditions.append(
            {
                "condition_id": condition_id,
                "status": "STILL_OPEN",
                "reason": reason,
                "caller_may_close": False,
            }
        )

    if checks["c1_reported_closed"] is not True:
        add("c1-fresh-evidence-completion", "the rooted C1 result is not CLOSED")
    if checks["all_cases_source_owned"] is not True:
        add("source-owned-evidence", "not every case is source-owned release evidence")
    if checks["all_cases_exact_frame"] is not True:
        add("exact-client-frame", "not every case has the required frame identity")
    if checks["exact_client_geometry_consistent"] is not True:
        add(
            "exact-client-geometry",
            "observed client geometries do not equal the one required frame geometry",
        )
    if checks["expected_capture_backend_consistent"] is not True:
        add(
            "expected-capture-backend",
            "observed capture backends do not equal the source-owned required backend",
        )
    if checks["all_cases_reported_dpi_96"] is not True:
        add("reported-dpi-96", "not every case reports the required DPI 96")
    if checks["window_class_consistent"] is not True:
        add("window-class-consistency", "observed window classes are not consistent")
    if preparable:
        add(
            "permanent-replay-source-adoption",
            "source-owned retained failures have proposal inputs only and no reviewed Git adoption",
        )
    if metadata_only:
        add(
            "metadata-only-retained-failure-resolution",
            "source-owned retained failures without public pixels are unpreparable and need independent disposition",
        )
    add(
        "exact-client-renderer-profile-envelope-review",
        "renderer identity and the complete candidate envelope require independent review",
    )
    add(
        "exact-source-git-blob-bindings",
        "the future source commit and every detector/profile/replay/record blob are unbound",
    )
    add(
        "final-lead-release-decision",
        "a separate lead decision must grant the source-owned release record",
    )
    return conditions


def _decision_artifact(
    *,
    followup: Mapping[str, object],
    followup_sha256: str,
    proposal_manifest: Mapping[str, object] | None,
    proposal_manifest_sha256: str | None,
) -> dict[str, object]:
    source = cast(dict[str, object], followup["source_snapshot"])
    envelope = cast(dict[str, object], followup["c2_envelope_review_inputs"])
    retained = cast(list[str], envelope["retained_failure_case_ids"])
    source_owned_failures = cast(
        list[str], envelope["source_owned_failure_case_ids"]
    )
    nonrelease_failures = cast(
        list[str], envelope["nonrelease_failure_case_ids"]
    )
    candidate_envelope = _candidate_envelope(followup)
    checks = _input_checks(followup, proposal_manifest=proposal_manifest)
    unresolved = _unresolved_conditions(
        followup, proposal_manifest=proposal_manifest
    )
    selection = (
        None
        if proposal_manifest is None
        else cast(dict[str, object], proposal_manifest["selection"])
    )
    preparable = (
        [] if selection is None else cast(list[str], selection["preparable_case_ids"])
    )
    metadata_only = (
        []
        if selection is None
        else cast(list[str], selection["metadata_only_case_ids"])
    )
    excluded = (
        []
        if selection is None
        else cast(list[str], selection["excluded_nonrelease_case_ids"])
    )
    proposal_status = "NOT_REQUIRED" if not retained else "PARTITIONED_REVIEW_INPUTS_ONLY"
    authority = _authority()
    return {
        "schema_version": 1,
        "decision_id": _DECISION_ID,
        "configuration_id": _CONFIGURATION_ID,
        "source_evidence": {
            "followup_sha256": followup_sha256,
            "review_package_manifest_sha256": source["manifest_sha256"],
            "followup_inputs": followup,
            "replay_proposal_manifest_sha256": proposal_manifest_sha256,
            "replay_proposal_manifest": proposal_manifest,
        },
        "input_checks": checks,
        "candidate_envelope": candidate_envelope,
        "unresolved_conditions": unresolved,
        "proposed_source_release_record": {
            "record_id": _PROPOSED_RECORD_ID,
            "status": "PROPOSED_NOT_GRANTED",
            "source_owner": "mining-automation-perception",
            "lineage": {
                "campaign_repository": source["repository"],
                "campaign_id": source["campaign_id"],
                "campaign_version": source["campaign_version"],
                "session_id": source["session_id"],
                "review_package_manifest_sha256": source["manifest_sha256"],
                "release_summary_sha256": source["release_summary_sha256"],
                "completion_seal_sha256": source["completion_seal_sha256"],
                "followup_sha256": followup_sha256,
                "replay_proposal_manifest_sha256": proposal_manifest_sha256,
            },
            "candidate_envelope": candidate_envelope,
            "replay_promotion": {
                "status": proposal_status,
                "retained_failure_case_ids": retained,
                "source_owned_failure_case_ids": source_owned_failures,
                "nonrelease_failure_case_ids": nonrelease_failures,
                "preparable_case_ids": preparable,
                "metadata_only_case_ids": metadata_only,
                "excluded_nonrelease_case_ids": excluded,
                "preparable_status": (
                    "PROPOSALS_ONLY_NOT_ADOPTED" if preparable else "NOT_PRESENT"
                ),
                "metadata_only_status": (
                    "UNPREPARABLE_NO_PIXELS" if metadata_only else "NOT_PRESENT"
                ),
                "nonrelease_status": (
                    "EXCLUDED_FROM_RELEASE" if excluded else "NOT_PRESENT"
                ),
                "proposal_manifest_sha256": proposal_manifest_sha256,
                "adopted_fixture_git_blobs": [],
                "permanent_regression": False,
            },
            "source_binding_plan": _source_binding_plan(
                has_preparable_proposals=bool(preparable)
            ),
            "unresolved_condition_ids": [
                item["condition_id"] for item in unresolved
            ],
            "release_decision": {
                "status": "NOT_GRANTED",
                "release_eligible": False,
                "activation_allowed": False,
                "reason": "separate-lead-review-and-source-grant-required",
            },
            "authority": authority,
        },
        "authority": authority,
    }


def _validate_decision_artifact(value: Mapping[str, object]) -> None:
    _strict_mapping(
        value,
        {
            "schema_version",
            "decision_id",
            "configuration_id",
            "source_evidence",
            "input_checks",
            "candidate_envelope",
            "unresolved_conditions",
            "proposed_source_release_record",
            "authority",
        },
        label="resource release decision",
    )
    if (
        not campaign._is_strict_int(value.get("schema_version"))
        or value.get("schema_version") != 1
        or value.get("decision_id") != _DECISION_ID
        or value.get("configuration_id") != _CONFIGURATION_ID
        or value.get("authority") != _authority()
    ):
        raise campaign.CampaignIntegrityError(
            "resource release decision identity/authority changed"
        )
    source_evidence = _strict_mapping(
        value.get("source_evidence"),
        {
            "followup_sha256",
            "review_package_manifest_sha256",
            "followup_inputs",
            "replay_proposal_manifest_sha256",
            "replay_proposal_manifest",
        },
        label="resource release decision source evidence",
    )
    followup_sha = source_evidence.get("followup_sha256")
    package_sha = source_evidence.get("review_package_manifest_sha256")
    followup = source_evidence.get("followup_inputs")
    if (
        not isinstance(followup_sha, str)
        or not campaign._SHA256_PATTERN.fullmatch(followup_sha)
        or not isinstance(package_sha, str)
        or not campaign._SHA256_PATTERN.fullmatch(package_sha)
        or not isinstance(followup, dict)
    ):
        raise campaign.CampaignIntegrityError(
            "resource release decision follow-up root changed"
        )
    campaign._validate_release_followup_inputs(followup)
    followup_source = cast(dict[str, object], followup["source_snapshot"])
    if followup_source.get("manifest_sha256") != package_sha:
        raise campaign.CampaignIntegrityError(
            "resource release decision package/follow-up root changed"
        )
    if _sha256(campaign._canonical_json_bytes(followup)) != followup_sha:
        raise campaign.CampaignIntegrityError(
            "resource release decision embedded follow-up hash changed"
        )
    proposal_sha = source_evidence.get("replay_proposal_manifest_sha256")
    proposal = source_evidence.get("replay_proposal_manifest")
    if (proposal_sha is None) != (proposal is None):
        raise campaign.CampaignIntegrityError(
            "resource release decision replay proposal root changed"
        )
    if proposal is not None:
        if (
            not isinstance(proposal_sha, str)
            or not campaign._SHA256_PATTERN.fullmatch(proposal_sha)
            or not isinstance(proposal, dict)
            or _sha256(campaign._canonical_json_bytes(proposal)) != proposal_sha
        ):
            raise campaign.CampaignIntegrityError(
                "resource release decision replay proposal hash changed"
            )
        _validate_embedded_proposal_manifest(
            proposal,
            followup=followup,
            expected_followup_sha256=followup_sha,
            expected_manifest_sha256=proposal_sha,
        )
    elif cast(dict[str, object], followup["c2_envelope_review_inputs"])[
        "retained_failure_case_ids"
    ]:
        raise campaign.CampaignIntegrityError(
            "resource release decision omitted required replay proposal"
        )
    expected = _decision_artifact(
        followup=followup,
        followup_sha256=followup_sha,
        proposal_manifest=cast(dict[str, object] | None, proposal),
        proposal_manifest_sha256=cast(str | None, proposal_sha),
    )
    if value != expected:
        raise campaign.CampaignIntegrityError(
            "resource release decision projection changed"
        )


def _overlaps(path: Path, sources: Sequence[Path]) -> bool:
    return any(
        path == source or path in source.parents or source in path.parents
        for source in sources
    )


def prepare_resource_release_decision(
    followup_path: Path,
    package_dir: Path,
    output_path: Path,
    *,
    expected_followup_sha256: str,
    expected_package_manifest_sha256: str,
    proposal_dir: Path | None = None,
    expected_proposal_manifest_sha256: str | None = None,
) -> dict[str, object]:
    """Prepare one externally rootable, permanently deny-only C2 review packet."""

    followup_snapshot = campaign._load_verified_followup_snapshot(
        followup_path,
        expected_sha256=expected_followup_sha256,
    )
    package_snapshot = campaign._load_verified_review_package_snapshot(
        package_dir,
        expected_manifest_sha256=expected_package_manifest_sha256,
    )
    expected_followup = campaign._followup_inputs_from_verified_snapshot(
        package_snapshot
    )
    if (
        package_snapshot.manifest_sha256 != expected_package_manifest_sha256
        or followup_snapshot.inputs != expected_followup
        or followup_snapshot.inputs_json
        != campaign._canonical_json_bytes(expected_followup)
    ):
        raise campaign.CampaignIntegrityError(
            "rooted follow-up does not match the rooted review package"
        )
    proposal_payload, proposal_manifest, proposal_sha = _load_proposal_snapshot(
        proposal_dir,
        expected_manifest_sha256=expected_proposal_manifest_sha256,
        followup=followup_snapshot.inputs,
        expected_followup_sha256=followup_snapshot.sha256,
    )
    if proposal_payload is not None and proposal_manifest is not None:
        if proposal_payload != campaign._canonical_json_bytes(proposal_manifest):
            raise campaign.CampaignIntegrityError(
                "rooted replay proposal manifest is not canonical JSON"
            )
    output = Path(output_path).resolve(strict=False)
    sources = [
        Path(followup_path).resolve(strict=True),
        package_snapshot.package_dir.resolve(strict=True),
    ]
    if proposal_dir is not None:
        sources.append(Path(proposal_dir).resolve(strict=True))
    if _overlaps(output, sources) or _overlaps(
        campaign._artifact_sidecar(output).resolve(strict=False), sources
    ):
        raise campaign.CampaignError(
            "release decision output must be separate from rooted sources"
        )
    artifact = _decision_artifact(
        followup=followup_snapshot.inputs,
        followup_sha256=followup_snapshot.sha256,
        proposal_manifest=proposal_manifest,
        proposal_manifest_sha256=proposal_sha,
    )
    digest = campaign._write_hashed_artifact(
        output,
        campaign._canonical_json_bytes(artifact),
    )
    unresolved = cast(list[dict[str, object]], artifact["unresolved_conditions"])
    return {
        "output": str(output),
        "sha256": digest,
        "followup_sha256": followup_snapshot.sha256,
        "review_package_manifest_sha256": package_snapshot.manifest_sha256,
        "proposal_manifest_sha256": proposal_sha,
        "unresolved_condition_count": len(unresolved),
        "review_packet_prepared": True,
        "release_eligible": False,
        "activation_allowed": False,
    }


def verify_resource_release_decision(
    path: Path,
    *,
    expected_sha256: str,
) -> dict[str, object]:
    """Verify one decision packet against an independently retained digest."""

    supplied = Path(path)
    if supplied.is_symlink():
        raise campaign.CampaignIntegrityError(
            "resource release decision path cannot be a symlink"
        )
    if (
        not isinstance(expected_sha256, str)
        or not campaign._SHA256_PATTERN.fullmatch(expected_sha256)
    ):
        raise campaign.CampaignIntegrityError(
            "expected decision SHA-256 must be 64 lowercase hexadecimal characters"
        )
    payload, digest = campaign._verify_hashed_artifact(
        supplied,
        expected=expected_sha256,
        maximum_bytes=_MAX_DECISION_BYTES,
    )
    artifact = campaign._strict_json_bytes(
        payload, label="resource release decision"
    )
    if payload != campaign._canonical_json_bytes(artifact):
        raise campaign.CampaignIntegrityError(
            "resource release decision is not canonical JSON"
        )
    _validate_decision_artifact(artifact)
    unresolved = cast(list[dict[str, object]], artifact["unresolved_conditions"])
    source_evidence = cast(dict[str, object], artifact["source_evidence"])
    return {
        "path": str(supplied.resolve(strict=True)),
        "sha256": digest,
        "followup_sha256": source_evidence["followup_sha256"],
        "proposal_manifest_sha256": source_evidence[
            "replay_proposal_manifest_sha256"
        ],
        "unresolved_condition_count": len(unresolved),
        "packet_integrity_verified": True,
        "review_packet_prepared": True,
        "release_eligible": False,
        "activation_allowed": False,
    }
