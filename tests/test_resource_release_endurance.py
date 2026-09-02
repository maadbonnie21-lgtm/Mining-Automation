from __future__ import annotations

import ast
import hashlib
import inspect
import json
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

import pytest

import mining_automation.perception as perception
from mining_automation.perception import resource_release_campaign as campaign
from mining_automation.perception import resource_release_decision as release_decision
from mining_automation.perception import resource_release_endurance as endurance
from mining_automation.perception import resource_release_receipt as release_receipt
from mining_automation.perception import resource_replay_promotion as replay_promotion
from mining_automation.perception.resource_release_endurance import (
    RESOURCE_RELEASE_ENDURANCE_SCHEMA_VERSION,
    RESOURCE_RELEASE_ENDURANCE_STATUS,
    ResourceReleaseChainExpectation,
    ResourceReleaseEnduranceError,
    verify_resource_release_endurance_report,
    write_resource_release_endurance_report,
)

_ROOT = Path(__file__).resolve().parents[1]
_SESSION_ID = "resource-release-20260902T010203Z-0123456789abcdef"
_HEAD_SHA = "1" * 40
_PACKAGE_SHA = "a" * 64
_PROPOSAL_SHA = "b" * 64
_RELEASE_SUMMARY_SHA = "c" * 64
_COMPLETION_SEAL_SHA = "d" * 64
_EXPECTED_REPORT_SHA = "d9cc41796116b10683f8a3725e2eb017c72eb9d3610168272fcc801eb4350f90"


@dataclass(frozen=True, slots=True)
class _Chain:
    package: Path
    followup: Path
    proposal: Path | None
    decision: Path
    expectation: ResourceReleaseChainExpectation


def _write_hashed_json(path: Path, value: dict[str, object]) -> str:
    payload = campaign._canonical_json_bytes(value)
    digest = hashlib.sha256(payload).hexdigest()
    path.write_bytes(payload)
    path.with_name(f"{path.name}.sha256").write_text(
        f"{digest}\n", encoding="ascii"
    )
    return digest


def _followup_document(
    *,
    proposal_required: bool,
    environment_overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    environment: dict[str, object] = {
        "required_reported_dpi": 96,
        "observed_reported_dpis": [96],
        "all_cases_match_required_dpi": True,
        "required_frame": {
            "width": 1005,
            "height": 1078,
            "pixel_format": "bgra8888",
        },
        "all_cases_match_required_frame": True,
        "observed_client_geometries": [{"width": 1005, "height": 1078}],
        "observed_window_classes": ["SunAwtFrame"],
        "window_class_consistent": True,
        "observed_capture_backends": ["windows-runelite"],
        "observed_evidence_origins": ["source-owned-windows-runelite"],
        "all_cases_source_owned": True,
        "renderer_identity": {
            "observed": False,
            "status": "NOT_OBSERVED_BY_CAPTURE_BACKEND",
            "requires_external_review": True,
        },
        "unresolved_external_inputs": ["independent review required"],
    }
    if environment_overrides is not None:
        environment.update(environment_overrides)
    retained = ["north-west-depleted"] if proposal_required else []
    candidate_count = 1 if proposal_required else 0
    return {
        "schema_version": 1,
        "inputs_id": "resource-release-followup-inputs-v1",
        "configuration_id": "resource-release-followup:varrock-east-iron-v1@1.0.0",
        "source_snapshot": {
            "package_id": "resource-release-review-package-v1",
            "manifest_sha256": _PACKAGE_SHA,
            "release_summary_sha256": _RELEASE_SUMMARY_SHA,
            "campaign_id": "resource-release-campaign:varrock-east-iron-v1",
            "campaign_version": "1.1.0",
            "configuration_id": "resource-release-campaign:varrock-east-iron-v1@1.1.0",
            "session_id": _SESSION_ID,
            "exported_at_utc": "2026-09-02T01:02:03Z",
            "completion_seal_sha256": _COMPLETION_SEAL_SHA,
            "repository": {
                "head_sha": _HEAD_SHA,
                "branch": "codex/a-resource-release-endurance",
                "clean": True,
            },
            "profile": {},
            "capture_configuration": {},
        },
        "verification": {
            "verified": True,
            "expected_manifest_sha256_matched": True,
            "case_count": 15,
            "operator_labels_included": False,
            "operator_labels_are_reviewer_truth": False,
            "all_cases_explicitly_privacy_reviewed": True,
            "contains_private_full_frames": False,
        },
        "case_bindings": [],
        "c1_result": {"status": "OPEN", "blockers": []},
        "failure_promotion_inputs": {
            "status": "PENDING_EXTERNAL" if proposal_required else "NOT_REQUIRED",
            "target_dataset_id": "varrock-east-iron-v1",
            "candidate_count": candidate_count,
            "candidates": [],
            "nonrelease_evidence_count": 0,
            "nonrelease_evidence": [],
            "promotion_complete": False,
        },
        "c2_envelope_review_inputs": {
            "input_status": "verified-inputs-only-independent-review-required",
            "required_reported_dpi": environment["required_reported_dpi"],
            "reported_dpi_by_case": [],
            "observed_reported_dpis": environment["observed_reported_dpis"],
            "all_cases_match_required_dpi": environment[
                "all_cases_match_required_dpi"
            ],
            "required_frame": environment["required_frame"],
            "all_cases_match_required_frame": environment[
                "all_cases_match_required_frame"
            ],
            "observed_capture_backends": environment[
                "observed_capture_backends"
            ],
            "observed_evidence_origins": environment[
                "observed_evidence_origins"
            ],
            "observed_window_classes": environment["observed_window_classes"],
            "observed_client_geometries": environment[
                "observed_client_geometries"
            ],
            "window_class_consistent": environment["window_class_consistent"],
            "all_cases_source_owned": environment["all_cases_source_owned"],
            "reported_release_gate_categories": {},
            "reported_c2_category": {},
            "retained_failure_case_ids": retained,
            "source_owned_failure_case_ids": retained,
            "nonrelease_failure_case_ids": [],
            "renderer_identity": environment["renderer_identity"],
            "unresolved_external_inputs": environment[
                "unresolved_external_inputs"
            ],
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


def _chain(
    tmp_path: Path,
    *,
    proposal_required: bool = True,
    environment_overrides: dict[str, object] | None = None,
) -> _Chain:
    package = tmp_path / "review-package"
    package.mkdir(parents=True)
    followup = tmp_path / "followup.json"
    followup_sha = _write_hashed_json(
        followup,
        _followup_document(
            proposal_required=proposal_required,
            environment_overrides=environment_overrides,
        ),
    )
    proposal: Path | None = None
    proposal_sha: str | None = None
    if proposal_required:
        proposal = tmp_path / "proposal"
        proposal.mkdir()
        proposal_sha = _PROPOSAL_SHA
    decision = tmp_path / "decision.json"
    decision_sha = _write_hashed_json(decision, {"rooted": True})
    return _Chain(
        package=package,
        followup=followup,
        proposal=proposal,
        decision=decision,
        expectation=ResourceReleaseChainExpectation(
            session_id=_SESSION_ID,
            repository_head_sha=_HEAD_SHA,
            package_manifest_sha256=_PACKAGE_SHA,
            followup_sha256=followup_sha,
            proposal_manifest_sha256=proposal_sha,
            decision_sha256=decision_sha,
        ),
    )


def _install_public_chain(
    monkeypatch: pytest.MonkeyPatch,
    chain: _Chain,
    *,
    overrides: dict[str, dict[str, object]] | None = None,
    followup_failure_call: int | None = None,
) -> dict[str, int]:
    counts = {
        "package": 0,
        "followup": 0,
        "proposal": 0,
        "decision": 0,
        "receipt": 0,
    }
    mutations = {} if overrides is None else overrides
    proposal_count = 1 if chain.proposal is not None else 0

    def package_verifier(
        package_dir: Path,
        *,
        expected_manifest_sha256: str,
    ) -> dict[str, object]:
        counts["package"] += 1
        assert Path(package_dir) == chain.package
        if expected_manifest_sha256 != chain.expectation.package_manifest_sha256:
            raise campaign.CampaignIntegrityError("wrong retained package root")
        value: dict[str, object] = {
            "package": str(chain.package),
            "manifest_sha256": chain.expectation.package_manifest_sha256,
            "release_summary_sha256": _RELEASE_SUMMARY_SHA,
            "case_count": 15,
            "contains_private_full_frames": False,
            "activation_allowed": False,
            "verified": True,
        }
        value.update(mutations.get("package", {}))
        return value

    def followup_verifier(
        path: Path,
        *,
        expected_sha256: str,
    ) -> dict[str, object]:
        counts["followup"] += 1
        assert Path(path) == chain.followup
        if counts["followup"] == followup_failure_call:
            raise campaign.CampaignIntegrityError("injected retained follow-up failure")
        if expected_sha256 != chain.expectation.followup_sha256:
            raise campaign.CampaignIntegrityError("wrong retained follow-up root")
        value: dict[str, object] = {
            "inputs": str(chain.followup),
            "sha256": chain.expectation.followup_sha256,
            "source_manifest_sha256": chain.expectation.package_manifest_sha256,
            "case_count": 15,
            "failure_candidate_count": proposal_count,
            "release_eligible": False,
            "activation_allowed": False,
            "verified": True,
        }
        value.update(mutations.get("followup", {}))
        return value

    def proposal_verifier(
        proposal_dir: Path,
        *,
        expected_manifest_sha256: str,
    ) -> dict[str, object]:
        counts["proposal"] += 1
        assert chain.proposal is not None
        assert Path(proposal_dir) == chain.proposal
        if expected_manifest_sha256 != chain.expectation.proposal_manifest_sha256:
            raise campaign.CampaignIntegrityError("wrong retained proposal root")
        value: dict[str, object] = {
            "proposal_dir": str(chain.proposal),
            "manifest_sha256": chain.expectation.proposal_manifest_sha256,
            "proposal_count": proposal_count,
            "verified": True,
            "adopted": False,
            "promotion_allowed": False,
            "activation_allowed": False,
        }
        value.update(mutations.get("proposal", {}))
        return value

    def decision_verifier(
        path: Path,
        *,
        expected_sha256: str,
    ) -> dict[str, object]:
        counts["decision"] += 1
        assert Path(path) == chain.decision
        if expected_sha256 != chain.expectation.decision_sha256:
            raise campaign.CampaignIntegrityError("wrong retained decision root")
        value: dict[str, object] = {
            "path": str(chain.decision),
            "sha256": chain.expectation.decision_sha256,
            "followup_sha256": chain.expectation.followup_sha256,
            "proposal_manifest_sha256": chain.expectation.proposal_manifest_sha256,
            "unresolved_condition_count": 3,
            "packet_integrity_verified": True,
            "review_packet_prepared": True,
            "release_eligible": False,
            "activation_allowed": False,
        }
        value.update(mutations.get("decision", {}))
        return value

    def receipt_loader() -> object:
        counts["receipt"] += 1
        reason = "resource release gates remain open: " + ", ".join(
            gate.value for gate in release_receipt.ResourceReleaseGate
        )
        raise release_receipt.ResourceReleaseReceiptUnavailable(reason)

    monkeypatch.setattr(campaign, "verify_review_package", package_verifier)
    monkeypatch.setattr(
        campaign, "verify_release_followup_inputs", followup_verifier
    )
    monkeypatch.setattr(
        replay_promotion,
        "verify_replay_promotion_proposals",
        proposal_verifier,
    )
    monkeypatch.setattr(
        release_decision,
        "verify_resource_release_decision",
        decision_verifier,
    )
    monkeypatch.setattr(
        release_receipt,
        "load_source_owned_varrock_east_iron_release_receipt",
        receipt_loader,
    )
    return counts


def _write(
    chain: _Chain,
    output: Path,
) -> dict[str, object]:
    return write_resource_release_endurance_report(
        chain.package,
        chain.followup,
        chain.decision,
        output,
        proposal_dir=chain.proposal,
        expectation=chain.expectation,
    )


def _verify(
    chain: _Chain,
    output: Path,
    digest: str,
) -> dict[str, object]:
    return verify_resource_release_endurance_report(
        output,
        chain.package,
        chain.followup,
        chain.decision,
        proposal_dir=chain.proposal,
        expectation=chain.expectation,
        expected_report_sha256=digest,
    )


def test_fixed_three_round_report_is_deterministic_and_integrity_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    chain = _chain(tmp_path / "chain")
    counts = _install_public_chain(monkeypatch, chain)
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    first_result = _write(chain, first)
    second_result = _write(chain, second)

    assert counts == {
        "package": 6,
        "followup": 6,
        "proposal": 6,
        "decision": 6,
        "receipt": 6,
    }
    assert first.read_bytes() == second.read_bytes()
    assert first_result["sha256"] == second_result["sha256"]
    assert first_result["sha256"] == _EXPECTED_REPORT_SHA
    report = json.loads(first.read_text(encoding="utf-8"))
    assert report["schema_version"] == RESOURCE_RELEASE_ENDURANCE_SCHEMA_VERSION
    assert report["status"] == RESOURCE_RELEASE_ENDURANCE_STATUS
    assert report["verification"]["required_round_count"] == 3
    assert report["verification"]["completed_round_count"] == 3
    assert [item["round"] for item in report["verification"]["rounds"]] == [1, 2, 3]
    assert report["privacy"] == {
        "contains_live_pixels": False,
        "contains_private_full_frames": False,
        "contains_sanitized_pixels": False,
        "metadata_only_report": True,
        "source_artifacts_embedded": False,
    }
    assert report["fresh_session_recovery"] == {
        "approval_override_allowed": False,
        "automatic_retry_allowed": False,
        "expected_root_rebinding_allowed": False,
        "failed_chain_mutation_allowed": False,
        "failure_action": "RETAIN_CHAIN_AND_START_FRESH_SOURCE_OWNED_SESSION",
        "fallback_allowed": False,
        "new_exact_clean_head_binding_required": True,
        "new_session_id_required": True,
        "same_session_retry_allowed": False,
    }
    assert all(value is False for value in report["authority"].values())
    for round_result in report["verification"]["rounds"]:
        assert round_result["receipt_readiness"] == {
            "activation_allowed": False,
            "loader_result": None,
            "receipt_authority": False,
            "receipt_issued": False,
            "status": "ALL_SOURCE_RECEIPT_GATES_OPEN",
        }
    assert str(chain.package) not in first.read_text(encoding="utf-8")
    assert _verify(chain, first, cast(str, first_result["sha256"]))["verified"] is True
    assert counts == {
        "package": 9,
        "followup": 9,
        "proposal": 9,
        "decision": 9,
        "receipt": 9,
    }


def test_absent_proposal_is_atomic_and_never_invokes_proposal_verifier(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    chain = _chain(tmp_path / "chain", proposal_required=False)
    counts = _install_public_chain(monkeypatch, chain)
    output = tmp_path / "report.json"

    _write(chain, output)

    assert counts == {
        "package": 3,
        "followup": 3,
        "proposal": 0,
        "decision": 3,
        "receipt": 3,
    }
    report = json.loads(output.read_text(encoding="utf-8"))
    for round_result in report["verification"]["rounds"]:
        assert round_result["proposal"] == {
            "activation_allowed": False,
            "adopted": False,
            "integrity_verified": True,
            "manifest_sha256": None,
            "promotion_allowed": False,
            "proposal_count": 0,
            "required": False,
        }


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("session_id", "foreign-session"),
        ("repository_head_sha", "2" * 40),
        ("package_manifest_sha256", "2" * 64),
        ("followup_sha256", "3" * 64),
        ("proposal_manifest_sha256", "4" * 64),
        ("decision_sha256", "5" * 64),
    ],
)
def test_stale_mixed_or_replayed_expectation_root_fails_without_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    field: str,
    replacement: str,
) -> None:
    chain = _chain(tmp_path / "chain")
    _install_public_chain(monkeypatch, chain)
    expectation = replace(chain.expectation, **{field: replacement})
    output = tmp_path / "report.json"

    with pytest.raises(
        (ResourceReleaseEnduranceError, campaign.CampaignIntegrityError),
    ):
        write_resource_release_endurance_report(
            chain.package,
            chain.followup,
            chain.decision,
            output,
            proposal_dir=chain.proposal,
            expectation=expectation,
        )

    assert not output.exists()
    assert not output.with_name("report.json.sha256").exists()


def test_failed_round_is_not_retried_and_requires_fresh_session_recovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    chain = _chain(tmp_path / "chain")
    counts = _install_public_chain(
        monkeypatch,
        chain,
        followup_failure_call=2,
    )
    source_snapshot = {
        path: path.read_bytes()
        for path in (
            chain.followup,
            chain.followup.with_name("followup.json.sha256"),
            chain.decision,
            chain.decision.with_name("decision.json.sha256"),
        )
    }
    output = tmp_path / "report.json"

    with pytest.raises(ResourceReleaseEnduranceError, match="start a fresh"):
        _write(chain, output)

    assert counts == {
        "package": 2,
        "followup": 2,
        "proposal": 1,
        "decision": 1,
        "receipt": 1,
    }
    assert not output.exists()
    assert not output.with_name("report.json.sha256").exists()
    assert {path: path.read_bytes() for path in source_snapshot} == source_snapshot


@pytest.mark.parametrize(
    ("stage", "field"),
    [
        ("package", "activation_allowed"),
        ("followup", "release_eligible"),
        ("proposal", "promotion_allowed"),
        ("decision", "activation_allowed"),
    ],
)
def test_public_chain_authority_spoof_cannot_be_packaged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stage: str,
    field: str,
) -> None:
    chain = _chain(tmp_path / "chain")
    _install_public_chain(monkeypatch, chain, overrides={stage: {field: True}})

    with pytest.raises(ResourceReleaseEnduranceError):
        _write(chain, tmp_path / "report.json")


@pytest.mark.parametrize(
    ("stage", "field"),
    [
        ("package", "manifest_sha256"),
        ("followup", "sha256"),
        ("proposal", "manifest_sha256"),
        ("decision", "sha256"),
    ],
)
def test_verifier_root_string_subclass_cannot_spoof_equality(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stage: str,
    field: str,
) -> None:
    class LyingSha(str):
        def __eq__(self, other: object) -> bool:
            return True

    chain = _chain(tmp_path / "chain")
    _install_public_chain(
        monkeypatch,
        chain,
        overrides={stage: {field: LyingSha("9" * 64)}},
    )

    with pytest.raises(ResourceReleaseEnduranceError, match="start a fresh"):
        _write(chain, tmp_path / "report.json")


@pytest.mark.parametrize(
    "behavior",
    [
        "return-none",
        "return-object",
        "foreign-exception",
        "subclass-exception",
        "wrong-unavailable-reason",
    ],
)
def test_receipt_readiness_requires_exact_all_gates_open_loader_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    behavior: str,
) -> None:
    chain = _chain(tmp_path / "chain")
    _install_public_chain(monkeypatch, chain)
    expected_reason = "resource release gates remain open: " + ", ".join(
        gate.value for gate in release_receipt.ResourceReleaseGate
    )

    class SpoofedUnavailable(release_receipt.ResourceReleaseReceiptUnavailable):
        pass

    def hostile_loader() -> object:
        if behavior == "return-none":
            return None
        if behavior == "return-object":
            return object()
        if behavior == "foreign-exception":
            raise RuntimeError(expected_reason)
        if behavior == "subclass-exception":
            raise SpoofedUnavailable(expected_reason)
        raise release_receipt.ResourceReleaseReceiptUnavailable("wrong reason")

    monkeypatch.setattr(
        release_receipt,
        "load_source_owned_varrock_east_iron_release_receipt",
        hostile_loader,
    )
    output = tmp_path / "report.json"

    with pytest.raises(ResourceReleaseEnduranceError, match="start a fresh"):
        _write(chain, output)

    assert not output.exists()
    assert not output.with_name("report.json.sha256").exists()


def test_rooted_environment_drift_and_retained_failure_remain_visible_but_powerless(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    chain = _chain(
        tmp_path / "chain",
        environment_overrides={
            "observed_reported_dpis": [96, 144],
            "all_cases_match_required_dpi": False,
            "all_cases_match_required_frame": False,
            "observed_client_geometries": [
                {"width": 1004, "height": 1078},
                {"width": 1005, "height": 1078},
            ],
            "observed_window_classes": ["ForeignWindow", "SunAwtFrame"],
            "window_class_consistent": False,
            "observed_capture_backends": [
                "foreign-backend",
                "windows-runelite",
            ],
            "unresolved_external_inputs": [
                "renderer review unresolved",
                "release record unresolved",
            ],
        },
    )
    _install_public_chain(monkeypatch, chain)
    output = tmp_path / "report.json"

    _write(chain, output)

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "INTEGRITY_ONLY_NO_AUTHORITY"
    assert report["retained_failures"]["retained_failure_case_ids"] == [
        "north-west-depleted"
    ]
    assert report["retained_failures"]["promotion_complete"] is False
    assert report["environment"]["all_cases_match_required_dpi"] is False
    assert report["environment"]["all_cases_match_required_frame"] is False
    assert report["environment"]["window_class_consistent"] is False
    assert report["environment"]["observed_capture_backends"] == [
        "foreign-backend",
        "windows-runelite",
    ]
    assert report["environment"]["all_cases_source_owned"] is True
    assert report["environment"]["renderer_identity"] == {
        "observed": False,
        "requires_external_review": True,
        "status": "NOT_OBSERVED_BY_CAPTURE_BACKEND",
    }
    assert report["environment"]["unresolved_external_inputs"] == [
        "renderer review unresolved",
        "release record unresolved",
    ]
    assert report["environment"]["envelope_approved"] is False
    assert all(value is False for value in report["authority"].values())


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        (
            "renderer_identity",
            {
                "observed": True,
                "status": "OBSERVED_BUT_NOT_REVIEWED",
                "requires_external_review": True,
            },
        ),
        ("unresolved_external_inputs", "not-a-list"),
        ("unresolved_external_inputs", ["valid", 1]),
    ],
)
def test_invalid_renderer_or_unresolved_projection_requires_fresh_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    field: str,
    replacement: object,
) -> None:
    chain = _chain(
        tmp_path / "chain",
        environment_overrides={field: replacement},
    )
    _install_public_chain(monkeypatch, chain)
    output = tmp_path / "report.json"

    with pytest.raises(ResourceReleaseEnduranceError, match="start a fresh"):
        _write(chain, output)

    assert not output.exists()
    assert not output.with_name("report.json.sha256").exists()


def test_tampered_and_rehashed_report_cannot_replace_expected_projection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    chain = _chain(tmp_path / "chain")
    _install_public_chain(monkeypatch, chain)
    output = tmp_path / "report.json"
    result = _write(chain, output)
    original_digest = cast(str, result["sha256"])
    report = json.loads(output.read_text(encoding="utf-8"))
    report["authority"]["release_eligible"] = True
    tampered_digest = _write_hashed_json(output, cast(dict[str, object], report))

    with pytest.raises(campaign.CampaignIntegrityError, match="stored SHA-256"):
        _verify(chain, output, original_digest)
    with pytest.raises(ResourceReleaseEnduranceError, match="no longer matches"):
        _verify(chain, output, tampered_digest)


def test_report_replacement_during_verification_is_detected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    chain = _chain(tmp_path / "chain")
    _install_public_chain(monkeypatch, chain)
    output = tmp_path / "report.json"
    result = _write(chain, output)
    retained_digest = cast(str, result["sha256"])
    real_build = endurance._build_report

    def replace_after_rebuild(
        *,
        package_dir: Path,
        followup_path: Path,
        proposal_dir: Path | None,
        decision_path: Path,
        expectation: ResourceReleaseChainExpectation,
    ) -> dict[str, object]:
        expected = real_build(
            package_dir=package_dir,
            followup_path=followup_path,
            proposal_dir=proposal_dir,
            decision_path=decision_path,
            expectation=expectation,
        )
        _write_hashed_json(output, {"replacement": True})
        return expected

    monkeypatch.setattr(endurance, "_build_report", replace_after_rebuild)

    with pytest.raises(campaign.CampaignIntegrityError, match="stored SHA-256"):
        _verify(chain, output, retained_digest)


def test_report_cannot_overlap_source_and_exclusive_write_preserves_existing_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    chain = _chain(tmp_path / "chain")
    counts = _install_public_chain(monkeypatch, chain)

    with pytest.raises(ResourceReleaseEnduranceError, match="outside source"):
        _write(chain, chain.package / "report.json")
    assert counts == {
        "package": 0,
        "followup": 0,
        "proposal": 0,
        "decision": 0,
        "receipt": 0,
    }

    existing = tmp_path / "existing.json"
    existing.write_bytes(b"winner")
    with pytest.raises(FileExistsError):
        _write(chain, existing)
    assert existing.read_bytes() == b"winner"
    assert not existing.with_name("existing.json.sha256").exists()

    partial = tmp_path / "partial.json"
    partial_sidecar = partial.with_name("partial.json.sha256")
    partial_sidecar.write_bytes(b"foreign-winner")
    with pytest.raises(FileExistsError):
        _write(chain, partial)
    assert not partial.exists()
    assert partial_sidecar.read_bytes() == b"foreign-winner"

    seed = tmp_path / "seed.json"
    digest = cast(str, _write(chain, seed)["sha256"])
    matching = tmp_path / "matching.json"
    matching_sidecar = matching.with_name("matching.json.sha256")
    matching_sidecar_payload = f"{digest}\n".encode("ascii")
    matching_sidecar.write_bytes(matching_sidecar_payload)

    with pytest.raises(FileExistsError):
        _write(chain, matching)

    assert not matching.exists()
    assert matching_sidecar.read_bytes() == matching_sidecar_payload
    with pytest.raises(campaign.CampaignIntegrityError, match="missing campaign"):
        _verify(chain, matching, digest)


def test_windows_alternate_data_stream_output_is_rejected_before_verification(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    chain = _chain(tmp_path / "chain")
    counts = _install_public_chain(monkeypatch, chain)
    alternate_stream = Path(f"{chain.followup}:endurance")

    with pytest.raises(ResourceReleaseEnduranceError, match="alternate data stream"):
        _write(chain, alternate_stream)

    assert counts == {
        "package": 0,
        "followup": 0,
        "proposal": 0,
        "decision": 0,
        "receipt": 0,
    }


@pytest.mark.parametrize("source_name", ["followup", "decision"])
def test_windows_alternate_data_stream_source_is_rejected_before_verification(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    source_name: str,
) -> None:
    chain = _chain(tmp_path / "chain")
    counts = _install_public_chain(monkeypatch, chain)
    followup = (
        Path(f"{chain.followup}:foreign")
        if source_name == "followup"
        else chain.followup
    )
    decision = (
        Path(f"{chain.decision}:foreign")
        if source_name == "decision"
        else chain.decision
    )

    with pytest.raises(ResourceReleaseEnduranceError, match="alternate data stream"):
        write_resource_release_endurance_report(
            chain.package,
            followup,
            decision,
            tmp_path / "report.json",
            proposal_dir=chain.proposal,
            expectation=chain.expectation,
        )

    assert counts == {
        "package": 0,
        "followup": 0,
        "proposal": 0,
        "decision": 0,
        "receipt": 0,
    }


@pytest.mark.parametrize(
    "source_name",
    ["package", "followup", "proposal", "decision"],
)
def test_direct_source_symlink_is_rejected_before_canonicalization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    source_name: str,
) -> None:
    chain = _chain(tmp_path / "chain")
    counts = _install_public_chain(monkeypatch, chain)
    selected = cast(Path, getattr(chain, source_name))
    real_is_symlink = Path.is_symlink

    def selected_path_is_symlink(path: Path) -> bool:
        if path == selected:
            return True
        return real_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", selected_path_is_symlink)

    with pytest.raises(ResourceReleaseEnduranceError, match="must not be a symlink"):
        _write(chain, tmp_path / "report.json")

    assert counts == {
        "package": 0,
        "followup": 0,
        "proposal": 0,
        "decision": 0,
        "receipt": 0,
    }


def test_public_verifier_failure_stops_before_report_and_does_not_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    chain = _chain(
        tmp_path / "chain",
        environment_overrides={
            "observed_capture_backends": ["foreign-backend"],
        },
    )
    counts = _install_public_chain(
        monkeypatch,
        chain,
        followup_failure_call=1,
    )
    output = tmp_path / "report.json"

    with pytest.raises(ResourceReleaseEnduranceError, match="start a fresh"):
        _write(chain, output)

    assert counts == {
        "package": 1,
        "followup": 1,
        "proposal": 0,
        "decision": 0,
        "receipt": 0,
    }
    assert not output.exists()


def test_two_concurrent_writers_leave_one_complete_winner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    chain = _chain(tmp_path / "chain")
    _install_public_chain(monkeypatch, chain)
    output = tmp_path / "report.json"
    barrier = threading.Barrier(3)
    successes: list[dict[str, object]] = []
    failures: list[BaseException] = []

    def writer() -> None:
        barrier.wait()
        try:
            successes.append(_write(chain, output))
        except BaseException as exc:  # noqa: BLE001 - adversarial writer capture
            failures.append(exc)

    threads = [threading.Thread(target=writer) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], FileExistsError)
    digest = cast(str, successes[0]["sha256"])
    assert output.with_name("report.json.sha256").read_text(encoding="ascii") == (
        f"{digest}\n"
    )
    assert _verify(chain, output, digest)["verified"] is True


def test_expectation_is_exact_and_proposal_path_is_atomic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class HostileString(str):
        pass

    with pytest.raises(ValueError, match="session_id"):
        ResourceReleaseChainExpectation(
            session_id=HostileString(_SESSION_ID),
            repository_head_sha=_HEAD_SHA,
            package_manifest_sha256=_PACKAGE_SHA,
            followup_sha256="2" * 64,
            proposal_manifest_sha256=None,
            decision_sha256="3" * 64,
        )

    chain = _chain(tmp_path / "chain")
    _install_public_chain(monkeypatch, chain)
    with pytest.raises(ResourceReleaseEnduranceError, match="present or absent"):
        write_resource_release_endurance_report(
            chain.package,
            chain.followup,
            chain.decision,
            tmp_path / "report.json",
            proposal_dir=None,
            expectation=chain.expectation,
        )

    class ForeignExpectation(ResourceReleaseChainExpectation):
        pass

    foreign = ForeignExpectation(
        session_id=chain.expectation.session_id,
        repository_head_sha=chain.expectation.repository_head_sha,
        package_manifest_sha256=chain.expectation.package_manifest_sha256,
        followup_sha256=chain.expectation.followup_sha256,
        proposal_manifest_sha256=chain.expectation.proposal_manifest_sha256,
        decision_sha256=chain.expectation.decision_sha256,
    )
    with pytest.raises(TypeError, match="exact ResourceReleaseChainExpectation"):
        write_resource_release_endurance_report(
            chain.package,
            chain.followup,
            chain.decision,
            tmp_path / "foreign.json",
            proposal_dir=chain.proposal,
            expectation=foreign,
        )


def test_caller_cannot_rebind_expectation_between_rounds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    chain = _chain(tmp_path / "chain")
    _install_public_chain(monkeypatch, chain)
    supplied = replace(chain.expectation)
    package_verifier = campaign.verify_review_package
    calls = 0

    def mutate_caller_after_first_package(
        package_dir: Path,
        *,
        expected_manifest_sha256: str,
    ) -> dict[str, object]:
        nonlocal calls
        result = package_verifier(
            package_dir,
            expected_manifest_sha256=expected_manifest_sha256,
        )
        calls += 1
        if calls == 1:
            object.__setattr__(supplied, "package_manifest_sha256", "9" * 64)
        return result

    monkeypatch.setattr(
        campaign,
        "verify_review_package",
        mutate_caller_after_first_package,
    )
    output = tmp_path / "report.json"

    result = write_resource_release_endurance_report(
        chain.package,
        chain.followup,
        chain.decision,
        output,
        proposal_dir=chain.proposal,
        expectation=supplied,
    )

    assert calls == 3
    assert result["status"] == "INTEGRITY_ONLY_NO_AUTHORITY"
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["expectation"]["package_manifest_sha256"] == _PACKAGE_SHA


def test_relative_source_paths_are_snapshotted_and_cwd_rebinding_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    chain = _chain(tmp_path / "chain")
    counts = _install_public_chain(monkeypatch, chain)
    package_verifier = campaign.verify_review_package
    foreign_cwd = tmp_path / "foreign-cwd"
    foreign_cwd.mkdir()
    calls = 0

    def change_cwd_after_first_check(
        package_dir: Path,
        *,
        expected_manifest_sha256: str,
    ) -> dict[str, object]:
        nonlocal calls
        result = package_verifier(
            package_dir,
            expected_manifest_sha256=expected_manifest_sha256,
        )
        calls += 1
        if calls == 1:
            monkeypatch.chdir(foreign_cwd)
        return result

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        campaign,
        "verify_review_package",
        change_cwd_after_first_check,
    )

    with pytest.raises(ResourceReleaseEnduranceError, match="source artifact"):
        write_resource_release_endurance_report(
            Path("chain/review-package"),
            Path("chain/followup.json"),
            Path("chain/decision.json"),
            Path("report.json"),
            proposal_dir=Path("chain/proposal"),
            expectation=chain.expectation,
        )

    assert counts == {
        "package": 3,
        "followup": 3,
        "proposal": 3,
        "decision": 3,
        "receipt": 3,
    }
    assert not (tmp_path / "report.json").exists()
    assert not (foreign_cwd / "report.json").exists()


def test_fixed_round_count_cannot_be_rebound_through_module_global(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    chain = _chain(tmp_path / "chain")
    counts = _install_public_chain(monkeypatch, chain)
    monkeypatch.setattr(endurance, "_VERIFICATION_ROUNDS", 1, raising=False)
    output = tmp_path / "report.json"

    _write(chain, output)

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["verification"]["required_round_count"] == 3
    assert [item["round"] for item in report["verification"]["rounds"]] == [1, 2, 3]
    assert counts == {
        "package": 3,
        "followup": 3,
        "proposal": 3,
        "decision": 3,
        "receipt": 3,
    }


def test_surface_has_no_retry_fallback_approval_or_live_dependency() -> None:
    write_parameters = set(
        inspect.signature(write_resource_release_endurance_report).parameters
    )
    verify_parameters = set(
        inspect.signature(verify_resource_release_endurance_report).parameters
    )
    forbidden_parameters = {
        "rounds",
        "retries",
        "retry",
        "fallback",
        "approve",
        "approval",
        "activate",
        "authority",
    }
    assert write_parameters.isdisjoint(forbidden_parameters)
    assert verify_parameters.isdisjoint(forbidden_parameters)
    assert not hasattr(perception, "ResourceReleaseChainExpectation")

    path = (
        _ROOT
        / "src"
        / "mining_automation"
        / "perception"
        / "resource_release_endurance.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    imports.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    forbidden_dependencies = {
        "capture",
        "state",
        "controller",
        "navigation",
        "banking",
        "interaction",
        "application",
        "constrained_v1_same_cycle",
    }
    assert all(
        fragment not in imported
        for imported in imports
        for fragment in forbidden_dependencies
    )
    relative_import_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is None
        for alias in node.names
    }
    assert "resource_release_receipt" in relative_import_names
