"""Offline readiness manifest for the future passive resource campaign.

This module can prove that one exact source head is prepared for a later,
separately reviewed enable-only change.  It cannot authorize capture, open a
backend, create a campaign session, review evidence, issue a receipt, or grant
runtime authority.
"""

from __future__ import annotations

import ast
import hashlib
import re
import subprocess
from pathlib import Path
from typing import Final, cast

from . import resource_release_campaign as campaign
from .production_profiles import load_varrock_east_iron_profile

RESOURCE_RELEASE_LIVE_READINESS_SCHEMA_VERSION: Final[int] = 1
RESOURCE_RELEASE_LIVE_READINESS_STATUS: Final[str] = "PREPARED_NOT_AUTHORIZED"

_READINESS_ID: Final[str] = "resource-release-live-campaign-readiness-v1"
_CONFIGURATION_ID: Final[str] = (
    "resource-release-live-campaign-readiness:varrock-east-iron-v1@1.0.0"
)
_ACCEPTED_PARENT_HEAD_SHA: Final[str] = (
    "d34143f00835cdafc4ace2987b1b8202e7a0abfb"
)
_READINESS_BRANCH: Final[str] = "codex/a-resource-live-campaign-readiness"
_CAMPAIGN_SOURCE_PATH: Final[str] = (
    "src/mining_automation/perception/resource_release_campaign.py"
)
_PROFILE_SOURCE_PATH: Final[str] = (
    "src/mining_automation/perception/profiles/varrock_east_iron_v1.json"
)
_READINESS_SOURCE_PATH: Final[str] = (
    "src/mining_automation/perception/resource_release_live_readiness.py"
)
_CLI_SOURCE_PATH: Final[str] = (
    "src/mining_automation/perception/resource_release_campaign_cli.py"
)
_TOOL_SOURCE_PATH: Final[str] = "tools/resource_release_campaign.py"
_SOURCE_GATE_NAME: Final[str] = "LIVE_RESOURCE_CAMPAIGN_AUTHORIZED"
_EXPECTED_CAMPAIGN_SOURCE_SHA256: Final[str] = (
    "8e791f582af8ba3f5c19e60b951114b451d76fcd321cb6ff809f77bac828c83a"
)
_EXPECTED_PROFILE_SHA256: Final[str] = (
    "317bd4f7d3e239874317bb9379a92d2541abac194039b82f4b0c02cc99844989"
)
_EXPECTED_PLAN_SHA256: Final[str] = (
    "5bc2ac8f56c7424b8dd178325874727069649fc86105c4bc9f2bfe9f91cbe877"
)
_EXPECTED_RESOURCE_IDS: Final[tuple[str, ...]] = (
    "varrock-east-iron-northwest",
    "varrock-east-iron-southwest",
    "varrock-east-iron-center",
    "varrock-east-iron-northeast",
)
_SHA256_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}\Z")
_GIT_SHA_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{40}\Z")


class ResourceReleaseLiveReadinessError(campaign.CampaignIntegrityError):
    """The offline readiness contract or its immutable artifact was invalid."""


def _authority() -> dict[str, object]:
    return {
        "campaign_authorized": False,
        "approval_authority": False,
        "release_eligible": False,
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
        "metadata_only": True,
        "contains_live_pixels": False,
        "contains_private_full_frames": False,
        "contains_sanitized_pixels": False,
        "contains_session_identity": False,
        "contains_operator_identity": False,
        "contains_reviewer_identity": False,
    }


def _strict_repository(value: object) -> campaign.RepositoryProvenance:
    if type(value) is not dict or set(cast(dict[object, object], value)) != {
        "head_sha",
        "branch",
        "clean",
    }:
        raise ResourceReleaseLiveReadinessError(
            "readiness repository provenance fields changed"
        )
    raw = cast(dict[str, object], value)
    head = raw["head_sha"]
    branch = raw["branch"]
    clean = raw["clean"]
    if (
        type(head) is not str
        or not _GIT_SHA_PATTERN.fullmatch(head)
        or type(branch) is not str
        or not branch.strip()
        or type(clean) is not bool
        or clean is not True
    ):
        raise ResourceReleaseLiveReadinessError(
            "readiness repository provenance is invalid"
        )
    return campaign.RepositoryProvenance(head_sha=head, branch=branch, clean=clean)


def _repository_json(value: campaign.RepositoryProvenance) -> dict[str, object]:
    if type(value) is not campaign.RepositoryProvenance:
        raise TypeError("repository must be exact RepositoryProvenance")
    if (
        type(value.head_sha) is not str
        or not _GIT_SHA_PATTERN.fullmatch(value.head_sha)
        or type(value.branch) is not str
        or not value.branch.strip()
        or value.branch != _READINESS_BRANCH
        or type(value.clean) is not bool
        or value.clean is not True
    ):
        raise ResourceReleaseLiveReadinessError(
            "readiness preparation requires an exact clean Git worktree"
        )
    return {
        "head_sha": value.head_sha,
        "branch": value.branch,
        "clean": value.clean,
    }


def _resolved_external_artifact_path(
    path: Path, *, repository_root: Path
) -> Path:
    supplied = Path(path)
    path_without_drive = str(supplied)[len(supplied.drive) :]
    if ":" in path_without_drive:
        raise ResourceReleaseLiveReadinessError(
            "readiness artifact must not address an alternate data stream"
        )
    try:
        repository = Path(repository_root).resolve(strict=True)
        parent = supplied.parent.resolve(strict=True)
    except OSError as exc:
        raise ResourceReleaseLiveReadinessError(
            "readiness repository or output parent is unavailable"
        ) from exc
    if not repository.is_dir() or not parent.is_dir():
        raise ResourceReleaseLiveReadinessError(
            "readiness repository and output parent must be directories"
        )
    if Path(repository_root).is_symlink() or supplied.parent.is_symlink():
        raise ResourceReleaseLiveReadinessError(
            "readiness repository and output parent must not be symlinks"
        )
    resolved = parent / supplied.name
    try:
        resolved.relative_to(repository)
    except ValueError:
        pass
    else:
        raise ResourceReleaseLiveReadinessError(
            "readiness artifact must be outside the Git repository"
        )
    if resolved.is_symlink() or resolved.with_name(f"{resolved.name}.sha256").is_symlink():
        raise ResourceReleaseLiveReadinessError(
            "readiness artifact and sidecar must not be symlinks"
        )
    return resolved


def _require_same_repository(
    expected: campaign.RepositoryProvenance,
    actual: object,
    *,
    stage: str,
) -> campaign.RepositoryProvenance:
    if type(actual) is not campaign.RepositoryProvenance:
        raise TypeError("repository reader returned a foreign provenance value")
    expected_json = _repository_json(expected)
    actual_json = _repository_json(actual)
    if actual_json != expected_json:
        raise ResourceReleaseLiveReadinessError(
            f"readiness repository changed {stage}"
        )
    return campaign.RepositoryProvenance(
        head_sha=cast(str, actual_json["head_sha"]),
        branch=cast(str, actual_json["branch"]),
        clean=cast(bool, actual_json["clean"]),
    )


def _read_head_with_parents(repository_root: Path) -> tuple[str, ...]:
    try:
        result = subprocess.run(
            ["git", "rev-list", "--parents", "-n", "1", "HEAD"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ResourceReleaseLiveReadinessError(
            "could not verify the accepted A7-to-A8 source lineage"
        ) from exc
    values = tuple(result.stdout.strip().split())
    if not values or any(not _GIT_SHA_PATTERN.fullmatch(value) for value in values):
        raise ResourceReleaseLiveReadinessError(
            "A8 Git parent projection is malformed"
        )
    return values


def _git_blob_binding(
    repository_root: Path, relative_path: str
) -> dict[str, object]:
    if type(relative_path) is not str or relative_path not in {
        _CAMPAIGN_SOURCE_PATH,
        _PROFILE_SOURCE_PATH,
        _READINESS_SOURCE_PATH,
        _CLI_SOURCE_PATH,
        _TOOL_SOURCE_PATH,
    }:
        raise ResourceReleaseLiveReadinessError(
            "readiness source binding path is not source-owned"
        )
    repository = Path(repository_root).resolve(strict=True)
    working_path = repository / relative_path
    try:
        resolved_working = working_path.resolve(strict=True)
        resolved_working.relative_to(repository)
        if working_path.is_symlink():
            raise ResourceReleaseLiveReadinessError(
                "readiness source binding must not be a symlink"
            )
        blob_id_result = subprocess.run(
            ["git", "rev-parse", f"HEAD:{relative_path}"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        )
        blob_result = subprocess.run(
            ["git", "show", f"HEAD:{relative_path}"],
            cwd=repository,
            check=True,
            capture_output=True,
        )
        working_blob_result = subprocess.run(
            [
                "git",
                "hash-object",
                "--path",
                relative_path,
                str(resolved_working),
            ],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        )
        working_bytes = resolved_working.read_bytes()
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        raise ResourceReleaseLiveReadinessError(
            f"could not bind source file to Git HEAD: {relative_path}"
        ) from exc
    blob_id = blob_id_result.stdout.strip()
    working_blob_id = working_blob_result.stdout.strip()
    head_bytes = blob_result.stdout
    calculated_blob_id = hashlib.sha1(
        f"blob {len(head_bytes)}\0".encode("ascii") + head_bytes,
        usedforsecurity=False,
    ).hexdigest()
    if (
        not _GIT_SHA_PATTERN.fullmatch(blob_id)
        or blob_id != calculated_blob_id
        or working_blob_id != blob_id
    ):
        raise ResourceReleaseLiveReadinessError(
            f"working source does not equal the exact Git HEAD blob: {relative_path}"
        )
    return {
        "path": relative_path,
        "git_blob_sha": blob_id,
        "sha256": hashlib.sha256(working_bytes).hexdigest(),
    }


def _source_lineage(
    repository: campaign.RepositoryProvenance, *, repository_root: Path
) -> dict[str, object]:
    values = _read_head_with_parents(repository_root)
    if len(values) != 2 or values != (
        repository.head_sha,
        _ACCEPTED_PARENT_HEAD_SHA,
    ):
        raise ResourceReleaseLiveReadinessError(
            "readiness HEAD must be one non-merge direct child of exact accepted A7"
        )
    return {
        "readiness_head_sha": repository.head_sha,
        "direct_parent_head_sha": _ACCEPTED_PARENT_HEAD_SHA,
        "single_parent_commit": True,
        "relationship": "DIRECT_CHILD_OF_ACCEPTED_A7",
        "verified_from_git": True,
    }


def _source_gate_binding(repository_root: Path) -> dict[str, object]:
    """Prove both runtime and source text retain one literal-false gate."""

    if campaign.LIVE_RESOURCE_CAMPAIGN_AUTHORIZED is not False:
        raise ResourceReleaseLiveReadinessError(
            "live resource source gate must remain literal false"
        )
    source_binding = _git_blob_binding(repository_root, _CAMPAIGN_SOURCE_PATH)
    if source_binding["sha256"] != _EXPECTED_CAMPAIGN_SOURCE_SHA256:
        raise ResourceReleaseLiveReadinessError(
            "source-owned campaign implementation changed from accepted A7"
        )
    imported_source = Path(campaign.__file__)
    expected_source = Path(repository_root) / _CAMPAIGN_SOURCE_PATH
    try:
        source_path = imported_source.resolve(strict=True)
        resolved_expected = expected_source.resolve(strict=True)
        if (
            source_path != resolved_expected
            or imported_source.is_symlink()
            or expected_source.is_symlink()
        ):
            raise ResourceReleaseLiveReadinessError(
                "campaign module was not imported from the exact repository source"
            )
        source_bytes = source_path.read_bytes()
        tree = ast.parse(source_bytes, filename=str(source_path))
    except (OSError, SyntaxError, ValueError) as exc:
        raise ResourceReleaseLiveReadinessError(
            "could not verify the source-owned live gate"
        ) from exc

    assignments: list[ast.expr] = []
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == _SOURCE_GATE_NAME and node.value is not None:
                assignments.append(node.value)
        elif isinstance(node, ast.Assign):
            if any(
                isinstance(target, ast.Name) and target.id == _SOURCE_GATE_NAME
                for target in node.targets
            ):
                assignments.append(node.value)
    if (
        len(assignments) != 1
        or not isinstance(assignments[0], ast.Constant)
        or assignments[0].value is not False
    ):
        raise ResourceReleaseLiveReadinessError(
            "live resource source gate is not one literal False assignment"
        )
    return {
        "source_path": _CAMPAIGN_SOURCE_PATH,
        "source_sha256": source_binding["sha256"],
        "git_blob_sha": source_binding["git_blob_sha"],
        "gate_name": _SOURCE_GATE_NAME,
        "gate_value": False,
        "assignment_form": "single_literal_false",
    }


def _production_contract() -> dict[str, object]:
    profile = load_varrock_east_iron_profile()
    thresholds = sorted({item.maximum_distance for item in profile.scene_landmarks})
    zones = sorted(
        {
            item.zone(profile.frame_width, profile.frame_height).value
            for item in profile.scene_landmarks
        }
    )
    if (
        thresholds != [0.12]
        or len(profile.scene_landmarks) != 6
        or profile.minimum_landmark_quorum != 5
        or profile.minimum_landmark_zones != 3
        or len(zones) != 3
        or profile.frame_width != 1005
        or profile.frame_height != 1078
        or profile.pixel_format.value != "bgra8888"
    ):
        raise ResourceReleaseLiveReadinessError(
            "packaged constrained-v1 production policy changed"
        )
    profile_identity = campaign._profile_identity()
    expected_identity: dict[str, object] = {
        "detector_id": "profiled-resource:varrock-east-iron-v1",
        "detector_version": "2.1.0",
        "profile_id": "varrock-east-iron-v1",
        "profile_schema_version": 3,
        "profile_sha256": _EXPECTED_PROFILE_SHA256,
        "location_id": "varrock-east-mine",
        "frame_width": 1005,
        "frame_height": 1078,
        "pixel_format": "bgra8888",
        "resource_ids": list(_EXPECTED_RESOURCE_IDS),
        "landmark_quorum": 5,
        "landmark_zones": 3,
    }
    if type(profile_identity) is not dict or profile_identity != expected_identity:
        raise ResourceReleaseLiveReadinessError(
            "detector/profile/schema/location identity changed from accepted A7"
        )
    return {
        "profile": profile_identity,
        "world_landmark_count": len(profile.scene_landmarks),
        "landmark_maximum_distance": thresholds[0],
        "landmark_quorum": profile.minimum_landmark_quorum,
        "landmark_zones_required": profile.minimum_landmark_zones,
        "macro_zones": zones,
        "scene_identity_authority": "unchanged_production_detector_only",
        "candidate_pixels_establish_scene_identity": False,
        "fixed_ui_establishes_scene_identity": False,
        "unsupported_or_uncertain_policy": "zero_targets_and_stop",
        "automatic_camera_recovery": False,
    }


def _campaign_contract() -> dict[str, object]:
    plan = campaign._plan_json()
    plan_sha256 = hashlib.sha256(
        campaign._canonical_json_bytes({"cases": plan})
    ).hexdigest()
    if (
        type(plan) is not list
        or len(plan) != 15
        or [item.get("ordinal") for item in plan] != list(range(1, 16))
        or len({cast(str, item.get("case_id")) for item in plan}) != 15
        or plan_sha256 != _EXPECTED_PLAN_SHA256
    ):
        raise ResourceReleaseLiveReadinessError(
            "fixed resource release campaign plan changed"
        )
    capture_configuration = campaign._capture_configuration()
    expected_capture_configuration: dict[str, object] = {
        "capture_backend": "windows-runelite",
        "title_match": "RuneLite",
        "retry_attempts": 0,
        "automatic_camera_control": False,
        "automatic_camera_recovery": False,
        "input_allowed": False,
        "one_capture_per_observation": True,
        "required_evidence_origin": "source-owned-windows-runelite",
        "required_reported_dpi": 96,
        "reported_dpi_requirement_status": (
            "required-candidate-pending-fresh-review"
        ),
        "live_source_authorized": False,
    }
    if (
        type(capture_configuration) is not dict
        or capture_configuration != expected_capture_configuration
    ):
        raise ResourceReleaseLiveReadinessError(
            "campaign capture configuration changed from accepted A7"
        )
    return {
        "campaign_id": campaign.RESOURCE_RELEASE_CAMPAIGN_ID,
        "campaign_version": campaign.RESOURCE_RELEASE_CAMPAIGN_VERSION,
        "campaign_schema_version": campaign.RESOURCE_RELEASE_CAMPAIGN_SCHEMA_VERSION,
        "configuration_id": campaign.RESOURCE_RELEASE_CONFIGURATION_ID,
        "case_count": 15,
        "plan_sha256": plan_sha256,
        "cases": plan,
        "capture_configuration": capture_configuration,
    }


def _execution_contract() -> dict[str, object]:
    return {
        "fixed_order_required": True,
        "case_selection_allowed": False,
        "staged_case_acknowledgment_only": True,
        "one_passive_observation_per_case": True,
        "one_production_capture_per_observation": True,
        "one_production_evaluation_per_observation": True,
        "automatic_retry_count": 0,
        "detector_controlled_retry_allowed": False,
        "detector_controlled_selection_allowed": False,
        "automatic_camera_control": False,
        "automatic_camera_recovery": False,
        "input_allowed": False,
        "retained_failures_remain_failures": True,
        "unsupported_scene_retry_allowed": False,
        "unsupported_or_uncertain_result": "zero_targets_and_stop",
    }


def _identity_review_contract() -> dict[str, object]:
    return {
        "required_frame": {
            "width": 1005,
            "height": 1078,
            "pixel_format": "bgra8888",
        },
        "required_reported_dpi": 96,
        "capture_backend": "windows-runelite",
        "evidence_origin": "source-owned-windows-runelite",
        "title_match": "RuneLite",
        "capture_build_binding": "exact_clean_repository_head",
        "capture_configuration_binding": (
            "resource-release-campaign:varrock-east-iron-v1@1.1.0"
        ),
        "per_observation_environment_fields": [
            "backend_name",
            "title_match",
            "window_title_present",
            "window_class",
            "window_client_width",
            "window_client_height",
            "reported_dpi",
        ],
        "client_window_identity": {
            "observed": False,
            "status": "PENDING_SOURCE_CAPTURE_AND_INDEPENDENT_REVIEW",
            "operator_may_assert": False,
            "must_be_consistent_across_all_cases": True,
        },
        "renderer_identity": {
            "observed": False,
            "value": None,
            "status": "UNOBSERVED_PENDING_EXTERNAL_REVIEW",
            "operator_may_assert": False,
            "required_before_final_envelope_release": True,
        },
    }


def _review_and_release_contract() -> dict[str, object]:
    return {
        "operator_role": "unverified_staging_only",
        "reviewer_role": "independent_truth",
        "operator_must_differ_from_reviewer": True,
        "operator_labels_are_reviewer_truth": False,
        "negative_operator_labels_can_promote_truth": False,
        "reviewer_truth_required_for_all_15_cases": True,
        "privacy_review_required_before_public_artifacts": True,
        "failure_action": "retain_as_permanent_evidence",
        "failure_policy_change_allowed": False,
        "post_campaign_roots": {
            "c1_completion_seal_sha256": "REQUIRED_EXTERNAL_ROOT",
            "c1_review_package_manifest_sha256": "REQUIRED_EXTERNAL_ROOT",
            "c1_release_summary_sha256": "REQUIRED_EXTERNAL_ROOT",
            "c1_followup_sha256": "REQUIRED_EXTERNAL_ROOT",
            "c2_replay_proposal_manifest_sha256": (
                "CONDITIONALLY_REQUIRED_EXTERNAL_ROOT"
            ),
            "c2_permanent_replay_adoption_root": "SEPARATE_SOURCE_REVIEW_REQUIRED",
            "c2_final_envelope_decision_sha256": "SEPARATE_EXTERNAL_ROOT_REQUIRED",
            "c2_source_release_record_root": "SEPARATE_SOURCE_CHANGE_REQUIRED",
        },
        "c1_completion_self_closes_c2": False,
        "final_envelope_review_separate": True,
        "source_release_decision_separate": True,
        "receipt_issuance_separate": True,
    }


def _enable_only_checklist(readiness_head: str) -> dict[str, object]:
    return {
        "status": "FUTURE_ENABLE_ONLY_CHANGE_NOT_AUTHORIZED",
        "accepted_readiness_head_sha": readiness_head,
        "future_execution_head_sha": None,
        "future_head_must_be_direct_child": True,
        "future_parent_must_equal_accepted_readiness_head": True,
        "single_source_change_only": True,
        "only_permitted_source_change": (
            "LIVE_RESOURCE_CAMPAIGN_AUTHORIZED: False -> True"
        ),
        "policy_or_refactor_change_allowed": False,
        "exact_enabled_head_ci_required": True,
        "explicit_lead_exact_head_authorization_required": True,
        "readiness_manifest_sha256_must_be_retained_externally": True,
        "readiness_session_reuse_allowed": False,
        "new_session_on_authorized_exact_head_required": True,
        "this_manifest_self_authorizes": False,
        "source_flip_alone_satisfies_external_lead_authorization": False,
        "lead_authorization_enforcement": (
            "external_release_control_not_runtime_state"
        ),
    }


def _manifest(
    repository: campaign.RepositoryProvenance, *, repository_root: Path
) -> dict[str, object]:
    repository_json = _repository_json(repository)
    source_lineage = _source_lineage(repository, repository_root=repository_root)
    source_gate = _source_gate_binding(repository_root)
    profile_binding = _git_blob_binding(repository_root, _PROFILE_SOURCE_PATH)
    readiness_binding = _git_blob_binding(repository_root, _READINESS_SOURCE_PATH)
    cli_binding = _git_blob_binding(repository_root, _CLI_SOURCE_PATH)
    tool_binding = _git_blob_binding(repository_root, _TOOL_SOURCE_PATH)
    if profile_binding["sha256"] != _EXPECTED_PROFILE_SHA256:
        raise ResourceReleaseLiveReadinessError(
            "packaged profile bytes changed from accepted A7"
        )
    return {
        "schema_version": RESOURCE_RELEASE_LIVE_READINESS_SCHEMA_VERSION,
        "readiness_id": _READINESS_ID,
        "configuration_id": _CONFIGURATION_ID,
        "status": RESOURCE_RELEASE_LIVE_READINESS_STATUS,
        "accepted_parent_lineage_head_sha": _ACCEPTED_PARENT_HEAD_SHA,
        "source_lineage": source_lineage,
        "readiness_repository": repository_json,
        "source_gate": source_gate,
        "source_bindings": {
            "profile": profile_binding,
            "readiness_implementation": readiness_binding,
            "campaign_cli": cli_binding,
            "tool_entrypoint": tool_binding,
        },
        "production_contract": _production_contract(),
        "campaign_contract": _campaign_contract(),
        "execution_contract": _execution_contract(),
        "identity_review_contract": _identity_review_contract(),
        "review_and_release_contract": _review_and_release_contract(),
        "future_enable_only_checklist": _enable_only_checklist(repository.head_sha),
        "privacy": _privacy(),
        "authority": _authority(),
    }


def prepare_resource_release_live_readiness(
    output_path: Path,
    *,
    repository_root: Path,
) -> dict[str, object]:
    """Exclusively publish one deterministic, non-authorizing readiness manifest."""

    if campaign.LIVE_RESOURCE_CAMPAIGN_AUTHORIZED is not False:
        raise ResourceReleaseLiveReadinessError(
            "live resource campaign is already enabled; preparation refused"
        )
    repository_root = Path(repository_root)
    output = _resolved_external_artifact_path(
        Path(output_path), repository_root=repository_root
    )
    repository = campaign.read_repository_provenance(repository_root)
    if type(repository) is not campaign.RepositoryProvenance:
        raise TypeError("repository reader returned a foreign provenance value")
    manifest = _manifest(repository, repository_root=repository_root)
    payload = campaign._canonical_json_bytes(manifest)
    before_publication = _require_same_repository(
        repository,
        campaign.read_repository_provenance(repository_root),
        stage="before publication",
    )
    if (
        campaign._canonical_json_bytes(
            _manifest(before_publication, repository_root=repository_root)
        )
        != payload
    ):
        raise ResourceReleaseLiveReadinessError(
            "readiness source projection changed before publication"
        )
    digest = campaign._write_hashed_artifact(output, payload)
    after_publication = _require_same_repository(
        repository,
        campaign.read_repository_provenance(repository_root),
        stage="during publication",
    )
    if (
        campaign._canonical_json_bytes(
            _manifest(after_publication, repository_root=repository_root)
        )
        != payload
    ):
        raise ResourceReleaseLiveReadinessError(
            "readiness source projection changed during publication"
        )
    return {
        "status": RESOURCE_RELEASE_LIVE_READINESS_STATUS,
        "path": str(output),
        "sha256": digest,
        "live_resource_campaign_authorized": False,
        "input_authority": False,
    }


def verify_resource_release_live_readiness(
    manifest_path: Path,
    *,
    expected_sha256: str,
    repository_root: Path,
) -> dict[str, object]:
    """Verify readiness against an externally retained digest and exact source."""

    if type(expected_sha256) is not str or not _SHA256_PATTERN.fullmatch(
        expected_sha256
    ):
        raise ValueError("expected_sha256 must be an exact lowercase SHA-256")
    if campaign.LIVE_RESOURCE_CAMPAIGN_AUTHORIZED is not False:
        raise ResourceReleaseLiveReadinessError(
            "live resource source gate no longer matches readiness"
        )
    repository_root = Path(repository_root)
    manifest = _resolved_external_artifact_path(
        Path(manifest_path), repository_root=repository_root
    )
    payload, digest = campaign._verify_hashed_artifact(
        manifest,
        expected=expected_sha256,
        maximum_bytes=campaign._MAX_PUBLIC_JSON_BYTES,
    )
    stored = campaign._strict_json_bytes(
        payload, label="resource live readiness manifest"
    )
    stored_repository = _strict_repository(stored.get("readiness_repository"))
    current_repository = campaign.read_repository_provenance(repository_root)
    current_repository = _require_same_repository(
        stored_repository,
        current_repository,
        stage="against the exact manifest binding",
    )
    expected = _manifest(current_repository, repository_root=repository_root)
    expected_payload = campaign._canonical_json_bytes(expected)
    if payload != expected_payload or stored != expected:
        raise ResourceReleaseLiveReadinessError(
            "resource live readiness manifest projection changed"
        )
    after_verification = _require_same_repository(
        current_repository,
        campaign.read_repository_provenance(repository_root),
        stage="during verification",
    )
    if (
        campaign._canonical_json_bytes(
            _manifest(after_verification, repository_root=repository_root)
        )
        != payload
    ):
        raise ResourceReleaseLiveReadinessError(
            "readiness source projection changed during verification"
        )
    final_payload, final_digest = campaign._verify_hashed_artifact(
        manifest,
        expected=expected_sha256,
        maximum_bytes=campaign._MAX_PUBLIC_JSON_BYTES,
    )
    if final_payload != payload or final_digest != digest:
        raise ResourceReleaseLiveReadinessError(
            "readiness artifact changed during verification"
        )
    return {
        "verified": True,
        "status": RESOURCE_RELEASE_LIVE_READINESS_STATUS,
        "sha256": digest,
        "readiness_head_sha": current_repository.head_sha,
        "live_resource_campaign_authorized": False,
        "input_authority": False,
    }
