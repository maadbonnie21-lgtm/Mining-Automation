from __future__ import annotations

import ast
import hashlib
import inspect
import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

import mining_automation.perception as perception
from mining_automation.perception import resource_release_campaign as campaign
from mining_automation.perception import resource_release_campaign_cli as campaign_cli
from mining_automation.perception import resource_release_live_readiness as readiness
from mining_automation.perception.resource_release_live_readiness import (
    RESOURCE_RELEASE_LIVE_READINESS_STATUS,
    ResourceReleaseLiveReadinessError,
    prepare_resource_release_live_readiness,
    verify_resource_release_live_readiness,
)

_HEAD = "8" * 40
_OTHER_HEAD = "9" * 40
_ACCEPTED_PARENT = "d34143f00835cdafc4ace2987b1b8202e7a0abfb"
_ROOT = Path(__file__).resolve().parents[1]
_REAL_GIT_BLOB_BINDING = readiness._git_blob_binding
_REAL_SOURCE_GATE_BINDING = readiness._source_gate_binding
_REAL_CAPTURE_CONFIGURATION = campaign._capture_configuration
_A11_ENABLED_HEAD_TEST_MIGRATION = True


@pytest.fixture(autouse=True)
def _fixed_test_lineage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(campaign, "LIVE_RESOURCE_CAMPAIGN_AUTHORIZED", False)
    monkeypatch.setattr(
        campaign,
        "_capture_configuration",
        lambda *, live_source_authorized=False: _REAL_CAPTURE_CONFIGURATION(
            live_source_authorized=False
        ),
    )
    enabled_sha = hashlib.sha256(
        Path(campaign.__file__).resolve(strict=True).read_bytes()
    ).hexdigest()
    monkeypatch.setattr(readiness, "_EXPECTED_CAMPAIGN_SOURCE_SHA256", enabled_sha)
    monkeypatch.setattr(
        readiness,
        "_read_head_with_parents",
        lambda root: (_HEAD, _ACCEPTED_PARENT),
    )

    def fake_blob_binding(root: Path, relative_path: str) -> dict[str, object]:
        payload = (Path(root) / relative_path).read_bytes()
        return {
            "path": relative_path,
            "git_blob_sha": "a" * 40,
            "sha256": hashlib.sha256(payload).hexdigest(),
        }

    monkeypatch.setattr(readiness, "_git_blob_binding", fake_blob_binding)


    def frozen_source_gate_binding(root: Path) -> dict[str, object]:
        del root
        return {
            "source_path": readiness._CAMPAIGN_SOURCE_PATH,
            "source_sha256": readiness._EXPECTED_CAMPAIGN_SOURCE_SHA256,
            "git_blob_sha": "a" * 40,
            "gate_name": readiness._SOURCE_GATE_NAME,
            "gate_value": False,
            "assignment_form": "single_literal_false",
        }

    monkeypatch.setattr(
        readiness, "_source_gate_binding", frozen_source_gate_binding
    )


def _repository(
    *, head_sha: str = _HEAD, branch: str = "codex/a-resource-live-campaign-readiness"
) -> campaign.RepositoryProvenance:
    return campaign.RepositoryProvenance(
        head_sha=head_sha,
        branch=branch,
        clean=True,
    )


def _repository_root(tmp_path: Path) -> Path:
    del tmp_path
    return _ROOT


def _prepare(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[Path, str]:
    monkeypatch.setattr(
        campaign, "read_repository_provenance", lambda root: _repository()
    )
    repository_root = _repository_root(tmp_path)
    output = tmp_path / "resource-live-readiness.json"
    result = prepare_resource_release_live_readiness(
        output,
        repository_root=repository_root,
    )
    return output, cast(str, result["sha256"])


def _rewrite_hashed(path: Path, value: dict[str, object]) -> str:
    payload = campaign._canonical_json_bytes(value)
    digest = hashlib.sha256(payload).hexdigest()
    path.write_bytes(payload)
    path.with_name(f"{path.name}.sha256").write_text(
        f"{digest}\n", encoding="ascii"
    )
    return digest


def test_readiness_manifest_freezes_exact_campaign_envelope_and_no_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output, digest = _prepare(monkeypatch, tmp_path)

    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["status"] == "PREPARED_NOT_AUTHORIZED"
    assert manifest["accepted_parent_lineage_head_sha"] == (
        _ACCEPTED_PARENT
    )
    assert manifest["source_lineage"] == {
        "readiness_head_sha": _HEAD,
        "direct_parent_head_sha": _ACCEPTED_PARENT,
        "single_parent_commit": True,
        "relationship": "DIRECT_CHILD_OF_ACCEPTED_A7",
        "verified_from_git": True,
    }
    assert manifest["readiness_repository"] == {
        "head_sha": _HEAD,
        "branch": "codex/a-resource-live-campaign-readiness",
        "clean": True,
    }
    assert manifest["source_gate"]["gate_value"] is False
    assert manifest["source_gate"]["assignment_form"] == "single_literal_false"
    assert len(manifest["source_gate"]["source_sha256"]) == 64
    assert set(manifest["source_bindings"]) == {
        "profile",
        "readiness_implementation",
        "campaign_cli",
        "tool_entrypoint",
    }
    assert all(
        len(binding["git_blob_sha"]) == 40
        and len(binding["sha256"]) == 64
        for binding in manifest["source_bindings"].values()
    )

    production = manifest["production_contract"]
    profile = production["profile"]
    assert profile["detector_id"] == "profiled-resource:varrock-east-iron-v1"
    assert profile["detector_version"] == "2.1.0"
    assert profile["profile_id"] == "varrock-east-iron-v1"
    assert profile["profile_schema_version"] == 3
    assert profile["location_id"] == "varrock-east-mine"
    assert profile["resource_ids"] == list(campaign.VARROCK_EAST_IRON_RESOURCE_IDS)
    assert production["landmark_maximum_distance"] == 0.12
    assert production["world_landmark_count"] == 6
    assert production["landmark_quorum"] == 5
    assert production["landmark_zones_required"] == 3
    assert len(production["macro_zones"]) == 3
    assert production["unsupported_or_uncertain_policy"] == "zero_targets_and_stop"

    campaign_contract = manifest["campaign_contract"]
    assert campaign_contract["case_count"] == 15
    assert [item["case_id"] for item in campaign_contract["cases"]] == [
        case.case_id for case in campaign.CAMPAIGN_PLAN
    ]
    assert [item["ordinal"] for item in campaign_contract["cases"]] == list(
        range(1, 16)
    )
    assert all(
        item["operator_label_role"] == "unverified-staging-only"
        for item in campaign_contract["cases"]
    )
    capture = campaign_contract["capture_configuration"]
    assert capture["capture_backend"] == "windows-runelite"
    assert capture["required_evidence_origin"] == "source-owned-windows-runelite"
    assert capture["required_reported_dpi"] == 96
    assert capture["live_source_authorized"] is False

    identity = manifest["identity_review_contract"]
    assert identity["required_frame"] == {
        "width": 1005,
        "height": 1078,
        "pixel_format": "bgra8888",
    }
    assert identity["required_reported_dpi"] == 96
    assert identity["renderer_identity"] == {
        "observed": False,
        "value": None,
        "status": "UNOBSERVED_PENDING_EXTERNAL_REVIEW",
        "operator_may_assert": False,
        "required_before_final_envelope_release": True,
    }
    assert identity["client_window_identity"]["operator_may_assert"] is False

    execution = manifest["execution_contract"]
    assert execution["one_passive_observation_per_case"] is True
    assert execution["automatic_retry_count"] == 0
    assert execution["case_selection_allowed"] is False
    assert execution["automatic_camera_control"] is False
    assert execution["automatic_camera_recovery"] is False
    assert execution["input_allowed"] is False
    assert execution["retained_failures_remain_failures"] is True

    review = manifest["review_and_release_contract"]
    assert review["operator_must_differ_from_reviewer"] is True
    assert review["operator_labels_are_reviewer_truth"] is False
    assert review["negative_operator_labels_can_promote_truth"] is False
    assert review["c1_completion_self_closes_c2"] is False
    assert review["final_envelope_review_separate"] is True
    assert review["source_release_decision_separate"] is True
    assert set(review["post_campaign_roots"]) == {
        "c1_completion_seal_sha256",
        "c1_review_package_manifest_sha256",
        "c1_release_summary_sha256",
        "c1_followup_sha256",
        "c2_replay_proposal_manifest_sha256",
        "c2_permanent_replay_adoption_root",
        "c2_final_envelope_decision_sha256",
        "c2_source_release_record_root",
    }

    checklist = manifest["future_enable_only_checklist"]
    assert checklist["accepted_readiness_head_sha"] == _HEAD
    assert checklist["future_execution_head_sha"] is None
    assert checklist["future_head_must_be_direct_child"] is True
    assert checklist["single_source_change_only"] is True
    assert checklist["exact_enabled_head_ci_required"] is True
    assert checklist["explicit_lead_exact_head_authorization_required"] is True
    assert checklist["this_manifest_self_authorizes"] is False
    assert (
        checklist["source_flip_alone_satisfies_external_lead_authorization"]
        is False
    )
    assert checklist["lead_authorization_enforcement"] == (
        "external_release_control_not_runtime_state"
    )
    assert all(value is False for value in manifest["authority"].values())
    assert manifest["privacy"]["contains_live_pixels"] is False
    assert output.with_name(f"{output.name}.sha256").read_text(
        encoding="ascii"
    ) == f"{digest}\n"


def test_readiness_is_deterministic_and_verifies_only_with_external_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        campaign, "read_repository_provenance", lambda root: _repository()
    )
    repository_root = _repository_root(tmp_path)
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first_result = prepare_resource_release_live_readiness(
        first, repository_root=repository_root
    )
    second_result = prepare_resource_release_live_readiness(
        second, repository_root=repository_root
    )
    assert first.read_bytes() == second.read_bytes()
    assert first_result["sha256"] == second_result["sha256"]

    verified = verify_resource_release_live_readiness(
        first,
        expected_sha256=cast(str, first_result["sha256"]),
        repository_root=repository_root,
    )
    assert verified == {
        "verified": True,
        "status": RESOURCE_RELEASE_LIVE_READINESS_STATUS,
        "sha256": first_result["sha256"],
        "readiness_head_sha": _HEAD,
        "live_resource_campaign_authorized": False,
        "input_authority": False,
    }

    with pytest.raises(ValueError, match="exact lowercase SHA-256"):
        verify_resource_release_live_readiness(
            first,
            expected_sha256="not-a-digest",
            repository_root=repository_root,
        )
    with pytest.raises(campaign.CampaignIntegrityError, match="stored SHA-256"):
        verify_resource_release_live_readiness(
            first,
            expected_sha256="f" * 64,
            repository_root=repository_root,
        )


def test_coordinated_rewrite_cannot_change_status_even_with_new_expected_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output, original_digest = _prepare(monkeypatch, tmp_path)
    repository_root = _repository_root(tmp_path)
    manifest = cast(dict[str, object], json.loads(output.read_text(encoding="utf-8")))
    manifest["status"] = "AUTHORIZED"
    changed_digest = _rewrite_hashed(output, manifest)

    with pytest.raises(campaign.CampaignIntegrityError, match="stored SHA-256"):
        verify_resource_release_live_readiness(
            output,
            expected_sha256=original_digest,
            repository_root=repository_root,
        )
    with pytest.raises(ResourceReleaseLiveReadinessError, match="projection changed"):
        verify_resource_release_live_readiness(
            output,
            expected_sha256=changed_digest,
            repository_root=repository_root,
        )


def test_replacement_during_verification_is_caught_by_final_recheck(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output, digest = _prepare(monkeypatch, tmp_path)
    real_verify = campaign._verify_hashed_artifact
    calls = 0

    def replace_after_first_read(
        path: Path,
        *,
        expected: str | None = None,
        maximum_bytes: int | None = None,
    ) -> tuple[bytes, str]:
        nonlocal calls
        result = real_verify(path, expected=expected, maximum_bytes=maximum_bytes)
        calls += 1
        if calls == 1:
            replacement = result[0] + b" \n"
            replacement_digest = hashlib.sha256(replacement).hexdigest()
            path.write_bytes(replacement)
            path.with_name(f"{path.name}.sha256").write_text(
                f"{replacement_digest}\n", encoding="ascii"
            )
        return result

    monkeypatch.setattr(campaign, "_verify_hashed_artifact", replace_after_first_read)
    with pytest.raises(campaign.CampaignIntegrityError, match="stored SHA-256"):
        verify_resource_release_live_readiness(
            output,
            expected_sha256=digest,
            repository_root=_repository_root(tmp_path),
        )
    assert calls == 1


def test_stale_dirty_or_foreign_repository_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output, digest = _prepare(monkeypatch, tmp_path)
    repository_root = _repository_root(tmp_path)
    monkeypatch.setattr(
        campaign,
        "read_repository_provenance",
        lambda root: _repository(head_sha=_OTHER_HEAD),
    )
    with pytest.raises(ResourceReleaseLiveReadinessError, match="exact manifest"):
        verify_resource_release_live_readiness(
            output,
            expected_sha256=digest,
            repository_root=repository_root,
        )

    monkeypatch.setattr(
        campaign,
        "read_repository_provenance",
        lambda root: campaign.RepositoryProvenance(
            head_sha=_HEAD,
            branch="codex/a-resource-live-campaign-readiness",
            clean=False,
        ),
    )
    with pytest.raises(ResourceReleaseLiveReadinessError, match="clean Git"):
        prepare_resource_release_live_readiness(
            tmp_path / "dirty.json", repository_root=repository_root
        )


def test_runtime_or_source_gate_change_refuses_without_publication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        campaign, "read_repository_provenance", lambda root: _repository()
    )
    repository_root = _repository_root(tmp_path)
    monkeypatch.setattr(
        readiness, "_source_gate_binding", _REAL_SOURCE_GATE_BINDING
    )
    runtime_output = tmp_path / "runtime.json"
    monkeypatch.setattr(campaign, "LIVE_RESOURCE_CAMPAIGN_AUTHORIZED", True)
    with pytest.raises(ResourceReleaseLiveReadinessError, match="already enabled"):
        prepare_resource_release_live_readiness(
            runtime_output, repository_root=repository_root
        )
    assert not runtime_output.exists()
    assert not runtime_output.with_name(f"{runtime_output.name}.sha256").exists()

    monkeypatch.setattr(campaign, "LIVE_RESOURCE_CAMPAIGN_AUTHORIZED", False)
    true_tree = ast.parse(
        "LIVE_RESOURCE_CAMPAIGN_AUTHORIZED: bool = True\n"
    )
    monkeypatch.setattr(readiness.ast, "parse", lambda *args, **kwargs: true_tree)
    source_output = tmp_path / "source.json"
    with pytest.raises(ResourceReleaseLiveReadinessError, match="literal False"):
        prepare_resource_release_live_readiness(
            source_output, repository_root=repository_root
        )
    assert not source_output.exists()



def test_a11_enabled_head_changes_only_live_gate_and_preserves_safety(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(campaign, "LIVE_RESOURCE_CAMPAIGN_AUTHORIZED", True)
    source_path = Path(campaign.__file__).resolve(strict=True)
    current_source = source_path.read_text(encoding="utf-8")
    old_gate = "LIVE_RESOURCE_CAMPAIGN_AUTHORIZED: Final[bool] = False"
    new_gate = "LIVE_RESOURCE_CAMPAIGN_AUTHORIZED: Final[bool] = True"
    assert current_source.count(new_gate) == 1
    assert old_gate not in current_source
    assert campaign.LIVE_RESOURCE_CAMPAIGN_AUTHORIZED is True

    profile = readiness.load_varrock_east_iron_profile()
    assert {item.maximum_distance for item in profile.scene_landmarks} == {0.12}
    assert len(profile.scene_landmarks) == 6
    assert profile.minimum_landmark_quorum == 5
    assert profile.minimum_landmark_zones == 3
    assert len(
        {
            item.zone(profile.frame_width, profile.frame_height).value
            for item in profile.scene_landmarks
        }
    ) == 3
    assert profile.frame_width == 1005
    assert profile.frame_height == 1078
    assert profile.pixel_format.value == "bgra8888"
    assert campaign._REQUIRED_REPORTED_DPI == 96
    assert len(campaign.CAMPAIGN_PLAN) == 15
    config = _REAL_CAPTURE_CONFIGURATION(live_source_authorized=True)
    assert config["live_source_authorized"] is True
    assert config["retry_attempts"] == 0
    assert config["automatic_camera_control"] is False
    assert config["automatic_camera_recovery"] is False
    assert config["input_allowed"] is False
    assert all(value is False for value in readiness._authority().values())

def test_preparation_never_constructs_session_or_capture_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        campaign,
        "WindowsCaptureBackend",
        lambda *args, **kwargs: calls.append("backend"),
    )
    monkeypatch.setattr(
        campaign,
        "create_campaign",
        lambda *args, **kwargs: calls.append("session"),
    )
    _prepare(monkeypatch, tmp_path)
    assert calls == []


def test_policy_drift_and_plan_drift_are_rejected_before_publication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        campaign, "read_repository_provenance", lambda root: _repository()
    )
    repository_root = _repository_root(tmp_path)
    profile = readiness.load_varrock_east_iron_profile()
    wrong_profile = replace(profile, minimum_landmark_quorum=4)
    monkeypatch.setattr(readiness, "load_varrock_east_iron_profile", lambda: wrong_profile)
    profile_output = tmp_path / "profile.json"
    with pytest.raises(ResourceReleaseLiveReadinessError, match="production policy"):
        prepare_resource_release_live_readiness(
            profile_output, repository_root=repository_root
        )
    assert not profile_output.exists()

    monkeypatch.setattr(readiness, "load_varrock_east_iron_profile", lambda: profile)
    original_plan = campaign._plan_json
    monkeypatch.setattr(campaign, "_plan_json", lambda: original_plan()[:-1])
    plan_output = tmp_path / "plan.json"
    with pytest.raises(ResourceReleaseLiveReadinessError, match="plan changed"):
        prepare_resource_release_live_readiness(
            plan_output, repository_root=repository_root
        )
    assert not plan_output.exists()


@pytest.mark.parametrize(
    ("case_index", "field", "value"),
    (
        (0, "case_id", "replacement-startup"),
        (0, "operator_prompt", "changed operator staging text"),
        (1, "required_review_meaning", "neighboring-copper"),
        (1, "requested_focal_state", "DEPLETED"),
    ),
)
def test_any_fixed_case_semantic_change_breaks_frozen_plan_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case_index: int,
    field: str,
    value: object,
) -> None:
    monkeypatch.setattr(
        campaign, "read_repository_provenance", lambda root: _repository()
    )
    plan = json.loads(json.dumps(campaign._plan_json()))
    plan[case_index][field] = value
    monkeypatch.setattr(campaign, "_plan_json", lambda: plan)
    output = tmp_path / f"wrong-plan-{field}.json"
    with pytest.raises(ResourceReleaseLiveReadinessError, match="plan changed"):
        prepare_resource_release_live_readiness(
            output, repository_root=_repository_root(tmp_path)
        )
    assert not output.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("retry_attempts", 1),
        ("automatic_camera_control", True),
        ("automatic_camera_recovery", True),
        ("input_allowed", True),
        ("capture_backend", "foreign-backend"),
        ("required_evidence_origin", "test-injected-non-release"),
        ("required_reported_dpi", 120),
    ),
)
def test_capture_configuration_drift_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    monkeypatch.setattr(
        campaign, "read_repository_provenance", lambda root: _repository()
    )
    original = campaign._capture_configuration()
    changed = {**original, field: value}
    monkeypatch.setattr(campaign, "_capture_configuration", lambda: changed)
    output = tmp_path / f"wrong-capture-{field}.json"
    with pytest.raises(ResourceReleaseLiveReadinessError, match="configuration"):
        prepare_resource_release_live_readiness(
            output, repository_root=_repository_root(tmp_path)
        )
    assert not output.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("detector_id", "foreign-detector"),
        ("detector_version", "0.0.0"),
        ("profile_id", "foreign-profile"),
        ("profile_schema_version", 2),
        ("profile_sha256", "f" * 64),
        ("location_id", "foreign-location"),
        ("resource_ids", ["foreign-resource"] * 4),
    ),
)
def test_detector_profile_identity_drift_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    monkeypatch.setattr(
        campaign, "read_repository_provenance", lambda root: _repository()
    )
    original = campaign._profile_identity()
    changed = {**original, field: value}
    monkeypatch.setattr(campaign, "_profile_identity", lambda: changed)
    output = tmp_path / f"wrong-profile-{field}.json"
    with pytest.raises(ResourceReleaseLiveReadinessError, match="identity"):
        prepare_resource_release_live_readiness(
            output, repository_root=_repository_root(tmp_path)
        )
    assert not output.exists()


@pytest.mark.parametrize(
    "history",
    (
        (_HEAD, "7" * 40),
        (_OTHER_HEAD, _ACCEPTED_PARENT),
        (_HEAD, _ACCEPTED_PARENT, "7" * 40),
    ),
)
def test_readiness_requires_one_nonmerge_direct_child_of_exact_a7(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    history: tuple[str, ...],
) -> None:
    monkeypatch.setattr(
        campaign, "read_repository_provenance", lambda root: _repository()
    )
    monkeypatch.setattr(readiness, "_read_head_with_parents", lambda root: history)
    output = tmp_path / "wrong-lineage.json"
    with pytest.raises(ResourceReleaseLiveReadinessError, match="direct child"):
        prepare_resource_release_live_readiness(
            output, repository_root=_repository_root(tmp_path)
        )
    assert not output.exists()


def test_campaign_and_profile_working_bytes_equal_exact_head_blobs() -> None:
    campaign_binding = _REAL_GIT_BLOB_BINDING(_ROOT, readiness._CAMPAIGN_SOURCE_PATH)
    profile_binding = _REAL_GIT_BLOB_BINDING(_ROOT, readiness._PROFILE_SOURCE_PATH)
    tool_binding = _REAL_GIT_BLOB_BINDING(_ROOT, readiness._TOOL_SOURCE_PATH)
    assert campaign_binding == {
        "path": readiness._CAMPAIGN_SOURCE_PATH,
        "git_blob_sha": "b699c0086d5350c430ee84ca019b4c8089b0cda7",
        "sha256": (
            "7329f5ed63d7430fc2e8831749ecd5e96fc510fce0616d26cde18c86c827da3c"
        ),
    }
    assert profile_binding == {
        "path": readiness._PROFILE_SOURCE_PATH,
        "git_blob_sha": "2259232823f0887acda93991d8e98eb75af3af03",
        "sha256": (
            "317bd4f7d3e239874317bb9379a92d2541abac194039b82f4b0c02cc99844989"
        ),
    }
    assert tool_binding == {
        "path": readiness._TOOL_SOURCE_PATH,
        "git_blob_sha": "d928da237d2eec886522d0086e5183e0c550aa89",
        "sha256": (
            "dc56d81a19396fe1e2840d6d0ed26ba6f6c5d6d5b4c2aed2f069a3cd3f7db1df"
        ),
    }


@pytest.mark.parametrize(
    "relative_path",
    (readiness._CAMPAIGN_SOURCE_PATH, readiness._TOOL_SOURCE_PATH),
)
def test_head_blob_binding_detects_assume_unchanged_working_source(
    tmp_path: Path,
    relative_path: str,
) -> None:
    repository = tmp_path / "git-repository"
    source = repository / relative_path
    source.parent.mkdir(parents=True)
    source.write_bytes(b"LIVE_RESOURCE_CAMPAIGN_AUTHORIZED = False\n")
    commands = (
        ("init", "-q"),
        ("config", "user.email", "tests@example.invalid"),
        ("config", "user.name", "A8 tests"),
        ("config", "core.autocrlf", "false"),
        ("add", relative_path),
        ("commit", "-q", "-m", "fixture"),
    )
    for arguments in commands:
        subprocess.run(["git", *arguments], cwd=repository, check=True)
    assert _REAL_GIT_BLOB_BINDING(
        repository, relative_path
    )["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()

    subprocess.run(
        ["git", "update-index", "--assume-unchanged", relative_path],
        cwd=repository,
        check=True,
    )
    source.write_bytes(b"LIVE_RESOURCE_CAMPAIGN_AUTHORIZED = True\n")
    status = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout == ""
    with pytest.raises(ResourceReleaseLiveReadinessError, match="exact Git HEAD blob"):
        _REAL_GIT_BLOB_BINDING(repository, relative_path)


def test_existing_artifact_is_preserved_and_duplicate_json_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        campaign, "read_repository_provenance", lambda root: _repository()
    )
    repository_root = _repository_root(tmp_path)
    output = tmp_path / "existing.json"
    output.write_bytes(b"winner")
    with pytest.raises(FileExistsError):
        prepare_resource_release_live_readiness(output, repository_root=repository_root)
    assert output.read_bytes() == b"winner"
    assert not output.with_name(f"{output.name}.sha256").exists()

    duplicate = tmp_path / "duplicate.json"
    payload = b'{"readiness_repository":{},"readiness_repository":{}}\n'
    digest = hashlib.sha256(payload).hexdigest()
    duplicate.write_bytes(payload)
    duplicate.with_name(f"{duplicate.name}.sha256").write_text(
        f"{digest}\n", encoding="ascii"
    )
    with pytest.raises(campaign.CampaignIntegrityError, match="duplicate JSON key"):
        verify_resource_release_live_readiness(
            duplicate,
            expected_sha256=digest,
            repository_root=repository_root,
        )


def test_output_must_be_external_real_path_and_not_an_ads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository_root = _repository_root(tmp_path)
    monkeypatch.setattr(
        campaign, "read_repository_provenance", lambda root: _repository()
    )
    inside = repository_root / "readiness.json"
    with pytest.raises(ResourceReleaseLiveReadinessError, match="outside"):
        prepare_resource_release_live_readiness(
            inside, repository_root=repository_root
        )
    assert not inside.exists()

    ads = tmp_path / "readiness.json:alternate"
    with pytest.raises(ResourceReleaseLiveReadinessError, match="alternate data"):
        prepare_resource_release_live_readiness(ads, repository_root=repository_root)
    assert not ads.exists()


def test_repository_toctou_stops_before_publication_or_returns_no_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository_root = _repository_root(tmp_path)
    stable = _repository()
    changed = _repository(head_sha=_OTHER_HEAD)
    before_calls = iter((stable, changed))
    monkeypatch.setattr(
        campaign, "read_repository_provenance", lambda root: next(before_calls)
    )
    before_output = tmp_path / "changed-before.json"
    with pytest.raises(ResourceReleaseLiveReadinessError, match="before publication"):
        prepare_resource_release_live_readiness(
            before_output, repository_root=repository_root
        )
    assert not before_output.exists()
    assert not before_output.with_name(f"{before_output.name}.sha256").exists()

    after_calls = iter((stable, stable, changed))
    monkeypatch.setattr(
        campaign, "read_repository_provenance", lambda root: next(after_calls)
    )
    after_output = tmp_path / "changed-during.json"
    with pytest.raises(ResourceReleaseLiveReadinessError, match="during publication"):
        prepare_resource_release_live_readiness(
            after_output, repository_root=repository_root
        )
    # The ownership-safe publisher completed before the repository changed.
    # No digest was returned, and a new external path is required; cleanup must
    # not race a concurrent replacement.
    assert after_output.exists()
    assert after_output.with_name(f"{after_output.name}.sha256").exists()


def test_false_gate_cli_start_creates_neither_session_nor_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(campaign_cli, "LIVE_RESOURCE_CAMPAIGN_AUTHORIZED", False)
    monkeypatch.setattr(
        campaign_cli,
        "read_repository_provenance",
        lambda root: calls.append("repository") or _repository(),
    )
    monkeypatch.setattr(
        campaign_cli,
        "create_campaign",
        lambda *args, **kwargs: calls.append("session") or tmp_path / "session",
    )
    monkeypatch.setattr(
        campaign,
        "WindowsCaptureBackend",
        lambda *args, **kwargs: calls.append("backend"),
    )

    assert campaign_cli.main(
        ["start", "--operator-id", "operator-a"], repository_root=tmp_path
    ) == 1
    assert calls == []
    assert list(tmp_path.iterdir()) == []


def test_cli_readiness_commands_are_thin_and_expected_root_is_mandatory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    prepared: list[tuple[Path, Path]] = []
    verified: list[tuple[Path, str, Path]] = []
    monkeypatch.setattr(
        campaign_cli,
        "prepare_resource_release_live_readiness",
        lambda output, *, repository_root: prepared.append(
            (output, repository_root)
        )
        or {"status": "PREPARED_NOT_AUTHORIZED"},
    )
    monkeypatch.setattr(
        campaign_cli,
        "verify_resource_release_live_readiness",
        lambda manifest, *, expected_sha256, repository_root: verified.append(
            (manifest, expected_sha256, repository_root)
        )
        or {"verified": True},
    )
    output = tmp_path / "manifest.json"
    assert campaign_cli.main(
        ["prepare-live-readiness", "--output", str(output)],
        repository_root=tmp_path,
    ) == 0
    assert prepared == [(output, tmp_path)]

    digest = "a" * 64
    assert campaign_cli.main(
        [
            "verify-live-readiness",
            "--manifest",
            str(output),
            "--expected-sha256",
            digest,
        ],
        repository_root=tmp_path,
    ) == 0
    assert verified == [(output, digest, tmp_path)]
    with pytest.raises(SystemExit):
        campaign_cli.build_parser().parse_args(
            ["verify-live-readiness", "--manifest", str(output)]
        )


def test_readiness_module_is_nonexported_and_has_no_authorization_seam() -> None:
    assert "resource_release_live_readiness" not in perception.__all__
    prepare_parameters = inspect.signature(
        prepare_resource_release_live_readiness
    ).parameters
    assert set(prepare_parameters) == {"output_path", "repository_root"}
    verify_parameters = inspect.signature(
        verify_resource_release_live_readiness
    ).parameters
    assert set(verify_parameters) == {
        "manifest_path",
        "expected_sha256",
        "repository_root",
    }
    source = inspect.getsource(readiness)
    assert "WindowsCaptureBackend" not in source
    assert "_capture_next_windows_case" not in source
    assert "create_campaign(" not in source
    assert "WorldState" not in source
