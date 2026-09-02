from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from time import sleep

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import mining_automation.perception.inventory.positive_classifier_v3 as candidate_runtime
import mining_automation.perception.inventory.positive_v3_independent_validation as frozen
import validation.inventory_v3_protocol_v2.protocol as protocol_v2
from mining_automation.validation import inventory_v3_capture as legacy_capture
from tests import test_inventory_v3_protocol_v2_bridge as bridge_support
from validation.inventory_v3_protocol_v2 import producer as producer_v2

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_FROZEN_L2 = "66c7e9536539979bc60e17f02f026eb64ebf0768"
_FROZEN_P2 = "0aa2647cd3382f217212377c7218848c3f322739"
_FROZEN_L2_SHA256 = "60ff2c511e46be3b87df4e0d9e4f705d897a4181f9152f2729ee90f6c45f8cf5"
_OPAQUE_RECEIPT_ID = "123e4567-e89b-42d3-a456-426614174000"
_AUTHORIZATION_PATHS = (
    protocol_v2._LIVE_AUTHORIZATION_PATH.as_posix(),
    protocol_v2._V2_LIVE_AUTHORIZATION_PATH.as_posix(),
    protocol_v2._V2_LIVE_AUTHORIZATION_SIDECAR_PATH.as_posix(),
)


@pytest.fixture
def c3_sandbox(request: pytest.FixtureRequest) -> Path:
    temporary = TemporaryDirectory(prefix="v2c3-")
    request.addfinalizer(temporary.cleanup)
    return Path(temporary.name)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _git(
    repository_root: Path,
    *arguments: str,
    committed_at: datetime | None = None,
) -> str:
    environment = os.environ.copy()
    if committed_at is not None:
        timestamp = committed_at.astimezone(UTC).isoformat(timespec="seconds")
        environment["GIT_AUTHOR_DATE"] = timestamp
        environment["GIT_COMMITTER_DATE"] = timestamp
    completed = subprocess.run(
        (
            "git",
            "-c",
            "core.autocrlf=false",
            "-C",
            str(repository_root),
            *arguments,
        ),
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return completed.stdout.strip()


def _clone_frozen_l2(tmp_path: Path) -> Path:
    clone = tmp_path / "r"
    _git(
        tmp_path,
        "clone",
        "--quiet",
        "--no-local",
        "--no-checkout",
        "--",
        str(_REPOSITORY_ROOT),
        str(clone),
    )
    for key, value in (
        ("core.autocrlf", "false"),
        ("commit.gpgSign", "false"),
        ("user.email", "protocol-v2-c3@example.invalid"),
        ("user.name", "Protocol V2 C3 Rehearsal"),
    ):
        _git(clone, "config", key, value)
    _git(clone, "checkout", "--quiet", "--detach", _FROZEN_L2)
    assert _git(clone, "rev-parse", "HEAD") == _FROZEN_L2
    assert _git(clone, "rev-parse", "HEAD^") == _FROZEN_P2
    assert _git(clone, "status", "--porcelain=v1") == ""
    return clone


def _file_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _approval_registry_snapshot(repository_root: Path) -> dict[str, bytes]:
    return {
        relative.as_posix(): repository_root.joinpath(*relative.parts).read_bytes()
        for relative in (
            protocol_v2._APPROVAL_REGISTRY_PATH,
            protocol_v2._APPROVAL_REGISTRY_SIDECAR_PATH,
        )
    }


def _authorization_registry_snapshot(repository_root: Path) -> dict[str, bytes]:
    return {
        relative.as_posix(): repository_root.joinpath(*relative.parts).read_bytes()
        for relative in (
            protocol_v2._LIVE_AUTHORIZATION_PATH,
            protocol_v2._V2_LIVE_AUTHORIZATION_PATH,
            protocol_v2._V2_LIVE_AUTHORIZATION_SIDECAR_PATH,
        )
    }


def _materialize_authorization_proposal(
    repository_root: Path,
    proposal: Mapping[str, object],
    *,
    committed_at: datetime,
) -> str:
    raw_files = proposal.get("files")
    assert isinstance(raw_files, list)
    assert [item.get("path") for item in raw_files if isinstance(item, dict)] == list(
        _AUTHORIZATION_PATHS
    )
    for raw in raw_files:
        assert isinstance(raw, dict)
        relative = raw.get("path")
        assert isinstance(relative, str)
        if "content" in raw:
            content = raw["content"]
            assert isinstance(content, Mapping)
            payload = protocol_v2._canonical_bytes(content)
        else:
            content_ascii = raw.get("content_ascii")
            assert isinstance(content_ascii, str)
            payload = content_ascii.encode("ascii")
        assert raw.get("sha256") == _sha256(payload)
        path = repository_root.joinpath(*relative.split("/"))
        path.write_bytes(payload)

    _git(repository_root, "add", "--", *_AUTHORIZATION_PATHS)
    assert _git(repository_root, "diff", "--cached", "--name-only").splitlines() == list(
        _AUTHORIZATION_PATHS
    )
    _git(
        repository_root,
        "commit",
        "--quiet",
        "-m",
        "test: synthetic protocol-v2 authorization",
        committed_at=committed_at,
    )
    authorization_head = _git(repository_root, "rev-parse", "HEAD")
    assert _git(repository_root, "show", "-s", "--format=%P", authorization_head) == _FROZEN_L2
    changed = _git(
        repository_root,
        "diff-tree",
        "--no-commit-id",
        "--name-only",
        "-r",
        authorization_head,
    ).splitlines()
    assert changed == list(_AUTHORIZATION_PATHS)
    assert _git(repository_root, "status", "--porcelain=v1") == ""
    return authorization_head


def _install_disposable_capture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    repository_root: Path,
    attempt_base: Path,
    paths: protocol_v2.ProtocolV2Paths,
    authorization_time: datetime,
) -> list[tuple[str, ...]]:
    capture_runtime = repository_root / "src/mining_automation/validation/inventory_v3_capture.py"
    evaluator_runtime = (
        repository_root
        / "src/mining_automation/perception/inventory/positive_v3_independent_validation.py"
    )
    candidate_runtime_path = (
        repository_root
        / "src/mining_automation/perception/inventory/positive_classifier_v3.py"
    )
    assert Path(legacy_capture.__file__).read_bytes() == capture_runtime.read_bytes()
    assert Path(frozen.__file__).read_bytes() == evaluator_runtime.read_bytes()
    outer_candidate_runtime = Path(candidate_runtime.__file__).resolve(strict=True)
    assert outer_candidate_runtime.read_bytes() == candidate_runtime_path.read_bytes()
    real_inspect = frozen.inspect
    monkeypatch.setattr(legacy_capture, "__file__", str(capture_runtime))
    monkeypatch.setattr(frozen, "__file__", str(evaluator_runtime))

    class CloneOwnedInspect:
        def getsourcefile(self, value: object) -> str | None:
            source = real_inspect.getsourcefile(value)
            if source is not None and Path(source).resolve(strict=True) == outer_candidate_runtime:
                return str(candidate_runtime_path)
            return source

        def __getattr__(self, name: str) -> object:
            return getattr(real_inspect, name)

    monkeypatch.setattr(frozen, "inspect", CloneOwnedInspect())

    reservation_root = (
        attempt_base / "Mining-Automation" / "inventory-positive-v3-independent-reservations"
    )
    monkeypatch.setattr(
        legacy_capture,
        "_new_source_owned_backend",
        bridge_support._conformance_backend_factory(),
    )
    monkeypatch.setattr(
        legacy_capture,
        "_approved_output_root",
        lambda _root: paths.source_campaign_root.parent,
    )
    monkeypatch.setattr(
        legacy_capture,
        "_approved_host_reservation_root",
        lambda: reservation_root,
    )
    monkeypatch.setattr(legacy_capture, "_require_isolated_mode", lambda: None)
    monkeypatch.setattr(
        legacy_capture,
        "_acknowledge_stage",
        lambda _stage, _index, _total, _path: None,
    )
    capture_times = iter(
        protocol_v2._format_utc(authorization_time + timedelta(seconds=offset))
        for offset in range(2, 20)
    )
    monkeypatch.setattr(legacy_capture, "_utc_timestamp", lambda: next(capture_times))
    monkeypatch.setattr(
        legacy_capture.platform,
        "platform",
        lambda: "Windows-synthetic-c3-rehearsal",
    )

    real_run = subprocess.run
    launcher = repository_root / "tools/capture_inventory_v3_independent.py"
    expected_command = (
        protocol_v2.sys.executable,
        "-I",
        "-S",
        str(launcher),
        "--operator",
        "operator-a",
        "--runelite-build",
        "operator-asserted-build",
        "--client-mode",
        "fixed",
        "--theme",
        "dark",
        "--renderer",
        "gpu",
    )
    launcher_calls: list[tuple[str, ...]] = []

    def run_disposable_launcher(
        command: object,
        *args: object,
        **kwargs: object,
    ) -> object:
        if isinstance(command, (tuple, list)) and tuple(command) == expected_command:
            assert kwargs == {"cwd": repository_root, "check": False}
            launcher_calls.append(expected_command)
            legacy_capture.run_passive_inventory_v3_capture_campaign(
                inputs=legacy_capture.PassiveInventoryV3CaptureInputs(
                    operator="operator-a",
                    runelite_build="operator-asserted-build",
                    client_mode="fixed",
                    theme="dark",
                    renderer="gpu",
                ),
                repository_root=repository_root,
            )
            return subprocess.CompletedProcess(expected_command, 0)
        return real_run(command, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(protocol_v2.subprocess, "run", run_disposable_launcher)
    return launcher_calls


def _reuse_exact_verified_bindings(
    monkeypatch: pytest.MonkeyPatch,
    *,
    repository_root: Path,
    authorization_head: str,
    protocol: protocol_v2.ProtocolV2LockBinding,
    authorization: protocol_v2.LiveAuthorizationBinding,
    legacy_protocol: legacy_capture._ProtocolBinding,
    legacy_authorization: legacy_capture._LiveAuthorizationBinding,
    frozen_lock: object,
    frozen_authorization: tuple[str, str, str],
) -> None:
    """Avoid repeating expensive Git closure after obtaining real bindings."""

    verified_root = repository_root.resolve(strict=True)

    def checked_protocol(
        root: Path,
        *,
        expected_head: str,
    ) -> protocol_v2.ProtocolV2LockBinding:
        assert root.resolve(strict=True) == verified_root
        assert expected_head == authorization_head
        assert _git(verified_root, "status", "--porcelain=v1") == ""
        return protocol

    def checked_authorization(
        candidate: protocol_v2.ProtocolV2LockBinding,
        *,
        access_hook: protocol_v2.AccessHook | None = None,
    ) -> protocol_v2.LiveAuthorizationBinding:
        del access_hook
        assert candidate == protocol
        return authorization

    def checked_legacy_protocol(root: Path) -> legacy_capture._ProtocolBinding:
        assert root.resolve(strict=True) == verified_root
        return legacy_protocol

    def checked_legacy_authorization(
        root: Path,
        candidate: legacy_capture._ProtocolBinding,
    ) -> legacy_capture._LiveAuthorizationBinding:
        assert root.resolve(strict=True) == verified_root
        assert candidate == legacy_protocol
        return legacy_authorization

    def checked_frozen_repository(root: Path, expected_head: str) -> Path:
        assert root.resolve(strict=True) == verified_root
        assert expected_head == authorization_head
        assert _git(verified_root, "status", "--porcelain=v1") == ""
        return verified_root

    def checked_frozen_lock(root: Path) -> object:
        assert root.resolve(strict=True) == verified_root
        return frozen_lock

    def checked_frozen_authorization(
        candidate: object,
        *,
        capture_execution_head_sha: str,
        authorization_git_commit_sha: str,
        authorization_git_blob: str,
    ) -> tuple[str, str, str]:
        assert candidate == frozen_lock
        assert capture_execution_head_sha == authorization_head
        assert authorization_git_commit_sha == authorization_head
        assert authorization_git_blob == legacy_authorization.git_blob
        return frozen_authorization

    monkeypatch.setattr(protocol_v2, "verify_protocol_v2_repository", checked_protocol)
    monkeypatch.setattr(protocol_v2, "verify_live_authorization", checked_authorization)
    monkeypatch.setattr(legacy_capture, "_verify_capture_repository", checked_legacy_protocol)
    monkeypatch.setattr(
        legacy_capture,
        "_verify_live_capture_authorization",
        checked_legacy_authorization,
    )
    monkeypatch.setattr(frozen, "_verify_repository_state", checked_frozen_repository)
    monkeypatch.setattr(frozen, "_current_validation_protocol_lock", checked_frozen_lock)
    monkeypatch.setattr(
        frozen,
        "_verify_capture_execution_authorization",
        checked_frozen_authorization,
    )


def test_exact_l2_pass_reaches_request_only_and_evaluator_replay_is_immutable(
    c3_sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = _clone_frozen_l2(c3_sandbox)
    attempt_base = c3_sandbox / "a"
    main_approval_before = _approval_registry_snapshot(_REPOSITORY_ROOT)
    clone_approval_before = _approval_registry_snapshot(repository_root)
    empty_authorization_before = _authorization_registry_snapshot(repository_root)

    producer_identity = producer_v2.WindowsProducerIdentity(
        computer_name="SYNTHETIC-C3-HOST",
        user_name="synthetic-c3-user",
        session_id=37,
    )
    monkeypatch.setattr(producer_v2, "observe_windows_identity", lambda: producer_identity)

    frozen_protocol = protocol_v2.verify_protocol_v2_repository(
        repository_root,
        expected_head=_FROZEN_L2,
    )
    assert frozen_protocol.source_commit_sha == _FROZEN_P2
    assert frozen_protocol.lock_commit_sha == _FROZEN_L2
    assert frozen_protocol.lock_sha256 == _FROZEN_L2_SHA256

    first_proposal = protocol_v2.build_live_authorization_proposal(
        repository_root,
        expected_lock_head=_FROZEN_L2,
        opaque_receipt_id=_OPAQUE_RECEIPT_ID,
        attempt_base=attempt_base,
    )
    second_proposal = protocol_v2.build_live_authorization_proposal(
        repository_root,
        expected_lock_head=_FROZEN_L2,
        opaque_receipt_id=_OPAQUE_RECEIPT_ID,
        attempt_base=attempt_base,
    )
    assert first_proposal == second_proposal
    assert first_proposal["status"] == "proposal-only-not-authorized"
    assert first_proposal["source_registry_modified"] is False
    assert first_proposal["activation_allowed"] is False
    assert first_proposal["promotion_allowed"] is False
    assert first_proposal["readiness"] == {
        "approval_registry_sha256": _sha256(
            clone_approval_before[protocol_v2._APPROVAL_REGISTRY_PATH.as_posix()]
        ),
        "fixed_output_paths_unoccupied": True,
        "legacy_user_reservation_unoccupied": True,
        "producer_identity_obtainable": True,
        "reservation_scope": "windows-user-local-not-host-global",
    }
    assert not attempt_base.exists()
    assert _authorization_registry_snapshot(repository_root) == empty_authorization_before
    assert _approval_registry_snapshot(repository_root) == clone_approval_before
    assert _git(repository_root, "status", "--porcelain=v1") == ""

    l2_time = datetime.fromisoformat(_git(repository_root, "show", "-s", "--format=%cI", _FROZEN_L2))
    authorization_time = l2_time.astimezone(UTC) + timedelta(seconds=1)
    assert authorization_time + timedelta(seconds=20) < datetime.now(UTC)
    authorization_head = _materialize_authorization_proposal(
        repository_root,
        first_proposal,
        committed_at=authorization_time,
    )

    protocol = protocol_v2.verify_protocol_v2_repository(
        repository_root,
        expected_head=authorization_head,
    )
    authorization = protocol_v2.verify_live_authorization(protocol)
    assert protocol.source_commit_sha == _FROZEN_P2
    assert protocol.lock_commit_sha == _FROZEN_L2
    assert protocol.lock_sha256 == _FROZEN_L2_SHA256
    assert protocol.evaluator_head_sha == authorization_head
    assert authorization.authorization_id == first_proposal["authorization_id"]
    assert authorization.git_commit_sha == authorization_head
    assert authorization.opaque_receipt_id == _OPAQUE_RECEIPT_ID
    authorized_registry_before = _authorization_registry_snapshot(repository_root)

    paths = protocol_v2.ProtocolV2Paths.for_authorization(
        repository_root,
        authorization.authorization_id,
        protocol.lock_sha256,
        attempt_base=attempt_base,
    )
    launcher_calls = _install_disposable_capture(
        monkeypatch,
        repository_root=repository_root,
        attempt_base=attempt_base,
        paths=paths,
        authorization_time=authorization_time,
    )

    legacy_protocol = legacy_capture._verify_capture_repository(repository_root)
    legacy_authorization = legacy_capture._verify_live_capture_authorization(
        repository_root,
        legacy_protocol,
    )
    assert legacy_protocol.execution_head_sha == authorization_head
    assert legacy_authorization.authorization_id == authorization.authorization_id
    assert legacy_authorization.git_commit_sha == authorization_head
    assert frozen._verify_repository_state(repository_root, authorization_head) == (
        repository_root.resolve(strict=True)
    )
    frozen_lock = frozen._current_validation_protocol_lock(repository_root)
    frozen_authorization = frozen._verify_capture_execution_authorization(
        frozen_lock,
        capture_execution_head_sha=authorization_head,
        authorization_git_commit_sha=authorization_head,
        authorization_git_blob=legacy_authorization.git_blob,
    )
    assert frozen_authorization[0] == authorization.authorization_id
    _reuse_exact_verified_bindings(
        monkeypatch,
        repository_root=repository_root,
        authorization_head=authorization_head,
        protocol=protocol,
        authorization=authorization,
        legacy_protocol=legacy_protocol,
        legacy_authorization=legacy_authorization,
        frozen_lock=frozen_lock,
        frozen_authorization=frozen_authorization,
    )

    attestation_path = protocol_v2.run_passive_capture_protocol_v2(
        repository_root,
        expected_head=authorization_head,
        operator="operator-a",
        runelite_build="operator-asserted-build",
        client_mode="fixed",
        theme="dark",
        renderer="gpu",
        attempt_base=attempt_base,
    )
    assert len(launcher_calls) == 1
    assert attestation_path == paths.source_campaign_root / protocol_v2._PRODUCER_ATTESTATION_NAME
    source_evidence = bridge_support._evidence_bytes(paths.source_campaign_root)
    assert len(tuple(paths.source_campaign_root.glob("captures/*/full-frame.bgra"))) == 7
    assert len(tuple(paths.source_campaign_root.glob("captures/*/inventory-region.bgra"))) == 7

    protocol_v2.finalize_acquisition(
        repository_root,
        expected_head=authorization_head,
        attempt_base=attempt_base,
    )
    protocol_v2.prepare_reviewer_intake(
        repository_root,
        expected_head=authorization_head,
        attempt_base=attempt_base,
    )
    protocol_v2.record_reviewer_submission(
        repository_root,
        expected_head=authorization_head,
        reviewer="reviewer-b",
        truth_provider=bridge_support._review_provider(bridge_support._PASS_COUNTS),
        attempt_base=attempt_base,
    )
    protocol_v2.publish_reviewed_package(
        repository_root,
        expected_head=authorization_head,
        attempt_base=attempt_base,
    )
    terminal = protocol_v2.evaluate_locked_protocol_v2(
        repository_root,
        expected_head=authorization_head,
        attempt_base=attempt_base,
    )

    assert terminal.detector_conformance_passed is True
    assert terminal.approval_required is True
    assert terminal.terminal_status == "conformance-passed-source-approval-required"
    result = bridge_support._read_mapping(paths.result_root / "protocol-v2-terminal-result.json")
    assert set(result) == {
        "activation_allowed",
        "approval_required",
        "authorization_id",
        "campaign_id",
        "campaign_manifest_sha256",
        "contract_id",
        "dataset_id",
        "detector_conformance_passed",
        "evaluated_at_utc",
        "frozen_candidate_head_sha",
        "frozen_evaluator_report_sha256",
        "opaque_receipt_id",
        "promotion_allowed",
        "protocol_lock_git_commit_sha",
        "protocol_lock_sha256",
        "protocol_source_git_commit_sha",
        "retry_allowed",
        "reviewed_package_tree_sha256",
        "schema",
        "terminal_status",
        "validation_package_sha256",
    }
    assert result["protocol_lock_git_commit_sha"] == _FROZEN_L2
    assert result["protocol_lock_sha256"] == _FROZEN_L2_SHA256
    assert result["protocol_source_git_commit_sha"] == _FROZEN_P2
    assert result["authorization_id"] == authorization.authorization_id
    assert result["contract_id"] == "CONFORMANCE_PASSED_APPROVAL_REQUIRED"
    assert result["retry_allowed"] is False
    assert result["activation_allowed"] is False
    assert result["promotion_allowed"] is False
    assert not (paths.result_root / "public-failure-receipt.json").exists()

    manifest = bridge_support._read_mapping(
        paths.reviewed_package_root / protocol_v2._CAMPAIGN_MANIFEST_NAME
    )
    cases = manifest["cases"]
    assert isinstance(cases, list)
    assert [case["planned_stage_id"] for case in cases] == list(protocol_v2.REQUIRED_STAGES)
    assert [case["sequence_index"] for case in cases] == list(range(1, 8))
    assert manifest["selection_policy"] == (
        "all-owned-captures-in-source-order-no-drop-no-replacement"
    )
    reviewer_truth = bridge_support._read_mapping(
        paths.reviewed_package_root / protocol_v2._REVIEWER_TRUTH_NAME
    )
    review_cases = reviewer_truth["cases"]
    assert isinstance(review_cases, list)
    assert [case["occupied_slots"] for case in review_cases] == list(
        bridge_support._PASS_COUNTS
    )
    assert [case["visibility"] for case in review_cases] == list(
        bridge_support._VISIBILITIES
    )
    assert bridge_support._evidence_bytes(paths.source_campaign_root) == source_evidence

    result_before_replay = _file_snapshot(paths.result_root)
    attempts_before_replay = _file_snapshot(paths.attempt_root)
    with pytest.raises(protocol_v2.InventoryV3ProtocolV2Error):
        protocol_v2.evaluate_locked_protocol_v2(
            repository_root,
            expected_head=authorization_head,
            attempt_base=attempt_base,
        )
    assert _file_snapshot(paths.result_root) == result_before_replay
    assert _file_snapshot(paths.attempt_root) == attempts_before_replay
    assert bridge_support._evidence_bytes(paths.source_campaign_root) == source_evidence

    sleep(0.002)
    proposal = protocol_v2.prepare_approval_request(
        repository_root,
        expected_head=authorization_head,
        proposed_approver="approver-c",
        proposed_approved_at_utc=protocol_v2._format_utc(datetime.now(UTC)),
        attempt_base=attempt_base,
    )
    request = bridge_support._read_mapping(Path(str(proposal["path"])))
    proposed_approval = request["proposed_approval"]
    assert isinstance(proposed_approval, dict)
    assert {
        proposed_approval["operator"],
        proposed_approval["reviewer"],
        proposed_approval["approver"],
    } == {"operator-a", "reviewer-b", "approver-c"}
    assert request["status"] == "request-only-not-approved"
    assert request["source_action_required"] is True
    assert request["approval_registry_modified"] is False
    assert request["activation_allowed"] is False
    assert request["promotion_allowed"] is False

    assert _authorization_registry_snapshot(repository_root) == authorized_registry_before
    assert _approval_registry_snapshot(repository_root) == clone_approval_before
    assert _approval_registry_snapshot(_REPOSITORY_ROOT) == main_approval_before
    assert _git(repository_root, "status", "--porcelain=v1") == ""
