"""R2.3 full-campaign authorization and durable reservation regressions."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from mining_automation.validation import camera_bridge_authorization as authorization
from mining_automation.validation.camera_bridge_authorization import (
    CAMERA_BRIDGE_AUTHORIZATION_CAMPAIGN_ID,
    CAMERA_BRIDGE_AUTHORIZATION_VERSION,
    CameraBridgeAuthorizationConsumedError,
    CameraBridgeAuthorizationError,
    CameraBridgeAuthorizationEvidence,
    CameraBridgeCompletionEvidence,
    authenticate_camera_bridge_authorization,
    authenticate_camera_bridge_completion,
    camera_bridge_authorization_consumed,
    camera_bridge_authorization_sentinel_path,
    camera_bridge_completion_seal_path,
    canonical_camera_bridge_component_sha256,
    repository_common_git_dir,
    reserve_camera_bridge_authorization,
    seal_camera_bridge_completion,
)

_HEAD = "a" * 40


@pytest.fixture(autouse=True)
def host_authority_base(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Keep host-global tests deterministic without adding a product override."""

    # Keep the host root short enough that the fixed production namespace and
    # campaign filename remain below the legacy Win32 MAX_PATH boundary.
    root = tmp_path_factory.mktemp("h")
    monkeypatch.setattr(authorization, "_host_authority_base", lambda: root)
    return root


def _evidence(
    seed: str = "baseline",
    *,
    precursor_mode: str = "compass_click",
) -> CameraBridgeAuthorizationEvidence:
    def digest(label: str) -> str:
        return hashlib.sha256(f"{seed}:{label}".encode()).hexdigest()

    return CameraBridgeAuthorizationEvidence(
        r1_report_sha256=digest("r1"),
        r2_report_sha256=digest("r2"),
        precursor_mode=precursor_mode,
        precursor_commit_sha256=digest("precursor-commit"),
        target_hwnd=123,
        target_process_id=456,
        target_thread_id=789,
        target_class_name="SunAwtFrame",
        target_title_sha256=digest("title"),
    )


def _ordinary_repository(tmp_path: Path, name: str = "repository") -> Path:
    repository = tmp_path / name
    (repository / ".git").mkdir(parents=True)
    return repository


def _independent_repositories(tmp_path: Path) -> tuple[Path, Path]:
    return (
        _ordinary_repository(tmp_path, "independent-clone-a"),
        _ordinary_repository(tmp_path, "independent-clone-b"),
    )


def _completion(
    reservation_sha256: str,
    seed: str = "complete",
) -> CameraBridgeCompletionEvidence:
    def digest(label: str) -> str:
        return hashlib.sha256(f"{seed}:{label}".encode()).hexdigest()

    return CameraBridgeCompletionEvidence(
        authorization_sentinel_sha256=reservation_sha256,
        capture_report_sha256=digest("report"),
        ordered_campaign_receipt_sha256=digest("ordered-campaign-receipt"),
        stage_chain_sha256=digest("stages"),
        commit_sha256=digest("commit"),
        post_sha256=digest("post"),
        pointer_mapping_sha256=digest("pointer"),
        registrations_sha256=digest("registrations"),
        closure_sha256=digest("closure"),
    )


def _linked_worktree(tmp_path: Path, name: str) -> tuple[Path, Path]:
    common = tmp_path / "common.git"
    worktree_git = common / "worktrees" / name
    worktree_git.mkdir(parents=True, exist_ok=True)
    (worktree_git / "commondir").write_text("../..\n", encoding="utf-8")
    repository = tmp_path / name
    repository.mkdir()
    (repository / ".git").write_text(
        f"gitdir: {worktree_git}\n",
        encoding="utf-8",
    )
    return repository, common


def test_campaign_path_is_source_owned_and_shared_across_worktrees(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    host_authority_base: Path,
) -> None:
    first, common = _linked_worktree(tmp_path, "first")
    second, _ = _linked_worktree(tmp_path, "second")
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "forged-git-dir"))
    monkeypatch.setenv("GIT_COMMON_DIR", str(tmp_path / "forged-common-dir"))

    first_path = camera_bridge_authorization_sentinel_path(first)
    second_path = camera_bridge_authorization_sentinel_path(second)

    assert repository_common_git_dir(first) == common.resolve()
    assert first_path == second_path
    assert first_path.is_relative_to(host_authority_base)
    assert first_path.name == f"{CAMERA_BRIDGE_AUTHORIZATION_CAMPAIGN_ID}.consumed.json"
    assert str(tmp_path / "forged-git-dir") not in str(first_path)


def test_campaign_path_is_shared_across_independent_clones(
    tmp_path: Path,
    host_authority_base: Path,
) -> None:
    first, second = _independent_repositories(tmp_path)

    first_path = camera_bridge_authorization_sentinel_path(first)
    second_path = camera_bridge_authorization_sentinel_path(second)

    assert repository_common_git_dir(first) != repository_common_git_dir(second)
    assert first_path == second_path
    assert first_path.is_relative_to(host_authority_base)
    assert not first_path.is_relative_to(first)
    assert not first_path.is_relative_to(second)
    assert first_path.name == f"{CAMERA_BRIDGE_AUTHORIZATION_CAMPAIGN_ID}.consumed.json"


def test_host_authority_path_cannot_be_redirected_by_environment_or_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    host_authority_base: Path,
) -> None:
    first, second = _independent_repositories(tmp_path)
    expected = camera_bridge_authorization_sentinel_path(first)
    hostile_root = tmp_path / "hostile"
    hostile_root.mkdir()
    for name in (
        "APPDATA",
        "GIT_COMMON_DIR",
        "GIT_DIR",
        "GIT_WORK_TREE",
        "HOME",
        "LOCALAPPDATA",
        "PATH",
        "PROGRAMDATA",
        "PYTHONPATH",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "XDG_CONFIG_HOME",
        "XDG_STATE_HOME",
    ):
        monkeypatch.setenv(name, str(hostile_root / name.lower()))
    alternate_cwd = tmp_path / "alternate-cwd"
    alternate_cwd.mkdir()
    monkeypatch.chdir(alternate_cwd)

    assert camera_bridge_authorization_sentinel_path(second) == expected
    assert expected.is_relative_to(host_authority_base)
    assert not expected.is_relative_to(hostile_root)
    assert list(hostile_root.rglob("*.consumed.json")) == []


def test_disabled_source_gate_cannot_create_reservation(tmp_path: Path) -> None:
    repository = _ordinary_repository(tmp_path)

    with pytest.raises(CameraBridgeAuthorizationError, match="gate is disabled"):
        reserve_camera_bridge_authorization(
            repository,
            git_head_sha=_HEAD,
            source_gate_enabled=False,
            evidence=_evidence(),
        )

    assert not camera_bridge_authorization_consumed(repository)


@pytest.mark.parametrize("precursor_mode", ["compass_click", "zero_click"])
def test_precursor_mode_is_exact_and_authenticated(
    tmp_path: Path,
    precursor_mode: str,
) -> None:
    repository = _ordinary_repository(tmp_path)
    evidence = _evidence(precursor_mode=precursor_mode)
    reservation = reserve_camera_bridge_authorization(
        repository,
        git_head_sha=_HEAD,
        source_gate_enabled=True,
        evidence=evidence,
    )
    authenticated = authenticate_camera_bridge_authorization(
        repository,
        git_head_sha=_HEAD,
        expected_sentinel_sha256=reservation.sentinel_sha256,
        evidence=evidence,
    )

    assert evidence.precursor_mode == precursor_mode
    assert evidence.as_dict()["precursor_mode"] == precursor_mode
    assert authenticated.evidence.precursor_mode == precursor_mode


@pytest.mark.parametrize(
    "precursor_mode",
    ["", "CompassClick", "north_report", "direct_registration", "zero-click"],
)
def test_invalid_precursor_mode_is_rejected(precursor_mode: str) -> None:
    with pytest.raises(CameraBridgeAuthorizationError, match="precursor_mode"):
        _evidence(precursor_mode=precursor_mode)


def test_reservation_is_canonical_and_authenticates_exact_evidence(
    tmp_path: Path,
) -> None:
    repository = _ordinary_repository(tmp_path)
    evidence = _evidence()

    reservation = reserve_camera_bridge_authorization(
        repository,
        git_head_sha=_HEAD,
        source_gate_enabled=True,
        evidence=evidence,
    )
    authenticated = authenticate_camera_bridge_authorization(
        repository,
        git_head_sha=_HEAD,
        expected_sentinel_sha256=reservation.sentinel_sha256,
        evidence=evidence,
    )

    assert authenticated.as_dict() == reservation.as_dict()
    report = reservation.as_dict()
    assert report["schema_version"] == 3
    assert reservation.as_dict()["authorization_version"] == (
        CAMERA_BRIDGE_AUTHORIZATION_VERSION
    )
    assert CAMERA_BRIDGE_AUTHORIZATION_VERSION == "2.3.0"
    assert CAMERA_BRIDGE_AUTHORIZATION_CAMPAIGN_ID == (
        "issue31-r2-3-full-campaign-north-right-0043-v1"
    )
    assert reservation.as_dict()["authority_scope"] == (
        "persistent_per_user_host_global"
    )
    assert reservation.as_dict()["independent_repository_clone_can_bypass"] is False
    assert "sentinel_relative_to_host_authority_root" in reservation.as_dict()
    assert "sentinel_relative_to_common_git_dir" not in reservation.as_dict()
    assert reservation.as_dict()["state"] == (
        "consumed_before_first_possible_physical_primitive"
    )
    assert reservation.as_dict()["maximum_physical_primitives"] == 2
    assert reservation.as_dict()["campaign_reservation_id"] == (
        reservation.sentinel_sha256
    )
    assert reservation.as_dict()["sentinel_sha256"] == reservation.sentinel_sha256
    assert reservation.as_dict()["ordered_primitive_policy"] == [
        {
            "ordinal": 0,
            "stage": "north_precursor",
            "kind": "compass_click",
            "logical_client_point": [608, 49],
            "zero_click_requires_exact_frozen_north_pixels": True,
        },
        {
            "ordinal": 1,
            "stage": "bridge",
            "kind": "key_hold",
            "key": "right",
            "hold_seconds": 0.043,
        },
    ]
    for field_name in (
        "caller_can_select_campaign",
        "caller_can_select_primitive_order",
        "caller_can_select_compass_coordinate",
        "caller_can_select_key_or_duration",
        "caller_can_select_action_or_target",
        "caller_can_select_physical_budget",
        "caller_can_select_source_gate",
    ):
        assert reservation.as_dict()[field_name] is False
    assert reservation.as_dict()["target_policy"] == {
        "camera_adapter": (
            "mining_automation.validation.windows_camera.WindowsCameraControl"
        ),
        "client_height": 1078,
        "client_width": 1005,
        "input_lease": (
            "mining_automation.validation.camera_input_lease."
            "WindowsCameraInputLease"
        ),
        "reviewed_pointer_logical_client": [400, 50],
        "reviewed_compass_logical_client": [608, 49],
        "title_substring": "runelite",
    }
    assert reservation.evidence.as_dict() == {
        "r1_report_sha256": evidence.r1_report_sha256,
        "r2_report_sha256": evidence.r2_report_sha256,
        "precursor_mode": "compass_click",
        "precursor_commit_sha256": evidence.precursor_commit_sha256,
        "target_hwnd": 123,
        "target_process_id": 456,
        "target_thread_id": 789,
        "target_class_name": "SunAwtFrame",
        "target_title_sha256": evidence.target_title_sha256,
    }


def test_sequential_independent_clone_and_linked_worktree_reservations_are_refused(
    tmp_path: Path,
) -> None:
    first, second = _independent_repositories(tmp_path)
    linked, _ = _linked_worktree(tmp_path, "linked")
    reserve_camera_bridge_authorization(
        first,
        git_head_sha=_HEAD,
        source_gate_enabled=True,
        evidence=_evidence(),
    )

    for repository in (first, second, linked):
        with pytest.raises(
            CameraBridgeAuthorizationConsumedError,
            match="already been consumed",
        ):
            reserve_camera_bridge_authorization(
                repository,
                git_head_sha="b" * 40,
                source_gate_enabled=True,
                evidence=_evidence("alternate"),
            )


@pytest.mark.parametrize("contents", [b"", b"interrupted", b"{\"tampered\":true}\n"])
def test_precreated_partial_or_tampered_sentinel_is_permanently_consumed(
    tmp_path: Path,
    contents: bytes,
) -> None:
    first, second = _independent_repositories(tmp_path)
    sentinel = camera_bridge_authorization_sentinel_path(first)
    sentinel.parent.mkdir(parents=True)
    sentinel.write_bytes(contents)

    for repository in (first, second):
        assert camera_bridge_authorization_consumed(repository)
        with pytest.raises(CameraBridgeAuthorizationConsumedError):
            reserve_camera_bridge_authorization(
                repository,
                git_head_sha=_HEAD,
                source_gate_enabled=True,
                evidence=_evidence(),
            )
        with pytest.raises(
            CameraBridgeAuthorizationError,
            match="partial, stale, or tampered",
        ):
            authenticate_camera_bridge_authorization(
                repository,
                git_head_sha=_HEAD,
                expected_sentinel_sha256="0" * 64,
                evidence=_evidence(),
            )


def test_interruption_after_exclusive_create_leaves_campaign_consumed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = _independent_repositories(tmp_path)

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("simulated durable-write interruption")

    monkeypatch.setattr(authorization.os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="simulated durable-write interruption"):
        reserve_camera_bridge_authorization(
            first,
            git_head_sha=_HEAD,
            source_gate_enabled=True,
            evidence=_evidence(),
        )

    for repository in (first, second):
        assert camera_bridge_authorization_consumed(repository)
        with pytest.raises(CameraBridgeAuthorizationConsumedError):
            reserve_camera_bridge_authorization(
                repository,
                git_head_sha=_HEAD,
                source_gate_enabled=True,
                evidence=_evidence(),
            )


def test_concurrent_reservation_has_exactly_one_atomic_winner(
    tmp_path: Path,
) -> None:
    first, second = _independent_repositories(tmp_path)
    repositories = (first, second)

    def attempt(index: int) -> str:
        try:
            reserve_camera_bridge_authorization(
                repositories[index % len(repositories)],
                git_head_sha=_HEAD,
                source_gate_enabled=True,
                evidence=_evidence(),
            )
        except CameraBridgeAuthorizationConsumedError:
            return "consumed"
        return "reserved"

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(attempt, range(8)))

    assert outcomes.count("reserved") == 1
    assert outcomes.count("consumed") == 7


def test_separate_processes_share_one_reservation_and_restart_stays_consumed(
    tmp_path: Path,
    host_authority_base: Path,
) -> None:
    first, second = _independent_repositories(tmp_path)
    third = _ordinary_repository(tmp_path, "independent-clone-c")
    physical_boundary = tmp_path / "physical-boundary.marker"
    script = """
import hashlib
import sys
from pathlib import Path
from mining_automation.validation import camera_bridge_authorization as authorization
from mining_automation.validation.camera_bridge_authorization import (
    CameraBridgeAuthorizationConsumedError,
    CameraBridgeAuthorizationEvidence,
    reserve_camera_bridge_authorization,
)

def digest(label: str) -> str:
    return hashlib.sha256(f"subprocess:{label}".encode()).hexdigest()

evidence = CameraBridgeAuthorizationEvidence(
    r1_report_sha256=digest("r1"),
    r2_report_sha256=digest("r2"),
    precursor_mode="compass_click",
    precursor_commit_sha256=digest("precursor-commit"),
    target_hwnd=123,
    target_process_id=456,
    target_thread_id=789,
    target_class_name="SunAwtFrame",
    target_title_sha256=digest("title"),
)
authorization._host_authority_base = lambda: Path(sys.argv[2])
sys.stdin.readline()
try:
    reserve_camera_bridge_authorization(
        Path(sys.argv[1]),
        git_head_sha="a" * 40,
        source_gate_enabled=True,
        evidence=evidence,
    )
except CameraBridgeAuthorizationConsumedError:
    print("consumed", flush=True)
else:
    Path(sys.argv[3]).write_text("physical-boundary\\n", encoding="utf-8")
    print("reserved", flush=True)
"""

    def hostile_environment(label: str) -> dict[str, str]:
        environment = dict(os.environ)
        hostile_root = tmp_path / f"hostile-{label}"
        for name in (
            "APPDATA",
            "GIT_COMMON_DIR",
            "GIT_DIR",
            "GIT_WORK_TREE",
            "HOME",
            "LOCALAPPDATA",
            "PATH",
            "PROGRAMDATA",
            "PYTHONPATH",
            "TEMP",
            "TMP",
            "USERPROFILE",
            "XDG_STATE_HOME",
        ):
            environment[name] = str(hostile_root / name.lower())
        return environment

    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                script,
                str(repository),
                str(host_authority_base),
                str(physical_boundary),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=repository,
            env=hostile_environment(str(index)),
        )
        for index, repository in enumerate((first, second), start=1)
    ]
    for process in processes:
        assert process.stdin is not None
        process.stdin.write("start\n")
        process.stdin.flush()
    results: list[str] = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=15.0)
        assert process.returncode == 0, stderr
        results.append(stdout.strip())

    assert sorted(results) == ["consumed", "reserved"]
    assert physical_boundary.read_text(encoding="utf-8") == "physical-boundary\n"

    restarted = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(third),
            str(host_authority_base),
            str(physical_boundary),
        ],
        input="start\n",
        check=True,
        capture_output=True,
        text=True,
        timeout=15.0,
        cwd=third,
        env=hostile_environment("restart"),
    )
    assert restarted.stdout.strip() == "consumed"
    assert physical_boundary.read_text(encoding="utf-8") == "physical-boundary\n"


def test_authentication_rejects_changed_head_or_dynamic_evidence(
    tmp_path: Path,
) -> None:
    first, second = _independent_repositories(tmp_path)
    evidence = _evidence()
    reservation = reserve_camera_bridge_authorization(
        first,
        git_head_sha=_HEAD,
        source_gate_enabled=True,
        evidence=evidence,
    )

    for head, observed_evidence in (
        ("b" * 40, evidence),
        (_HEAD, _evidence("substituted-report")),
        (_HEAD, replace(evidence, precursor_mode="zero_click")),
        (_HEAD, replace(evidence, precursor_commit_sha256="f" * 64)),
    ):
        with pytest.raises(CameraBridgeAuthorizationError):
            authenticate_camera_bridge_authorization(
                second,
                git_head_sha=head,
                expected_sentinel_sha256=reservation.sentinel_sha256,
                evidence=observed_evidence,
            )


def test_completion_requires_and_authenticates_exact_full_transaction(
    tmp_path: Path,
) -> None:
    first, second = _independent_repositories(tmp_path)
    reservation = reserve_camera_bridge_authorization(
        first,
        git_head_sha=_HEAD,
        source_gate_enabled=True,
        evidence=_evidence(),
    )
    evidence = _completion(reservation.sentinel_sha256)

    completion = seal_camera_bridge_completion(
        second,
        git_head_sha=_HEAD,
        reservation=reservation,
        evidence=evidence,
    )
    authenticated = authenticate_camera_bridge_completion(
        first,
        git_head_sha=_HEAD,
        expected_seal_sha256=completion.seal_sha256,
        evidence=evidence,
    )

    assert completion.as_dict() == authenticated.as_dict()
    assert completion.as_dict()["schema_version"] == 3
    assert completion.as_dict()["authorization_version"] == (
        CAMERA_BRIDGE_AUTHORIZATION_VERSION
    )
    assert completion.as_dict()["authority_scope"] == (
        "persistent_per_user_host_global"
    )
    assert "seal_relative_to_host_authority_root" in completion.as_dict()
    assert "seal_relative_to_common_git_dir" not in completion.as_dict()
    assert completion.as_dict()["state"] == "complete_post_input_transaction_sealed"
    assert completion.as_dict()[
        "reservation_without_this_seal_is_not_an_action_transition"
    ] is True
    assert completion.as_dict()["maximum_physical_primitives"] == 2
    assert completion.as_dict()["ordered_primitive_policy"] == (
        reservation.as_dict()["ordered_primitive_policy"]
    )
    completion_evidence = completion.as_dict()["completion_evidence"]
    assert isinstance(completion_evidence, dict)
    assert completion_evidence["ordered_campaign_receipt_sha256"] == (
        evidence.ordered_campaign_receipt_sha256
    )
    assert "receipt_sha256" not in completion_evidence

    tampered_receipt = replace(
        evidence,
        ordered_campaign_receipt_sha256="f" * 64,
    )
    with pytest.raises(CameraBridgeAuthorizationError, match="partial, stale"):
        authenticate_camera_bridge_completion(
            second,
            git_head_sha=_HEAD,
            expected_seal_sha256=completion.seal_sha256,
            evidence=tampered_receipt,
        )


def test_concurrent_completion_seal_has_one_immutable_winner(
    tmp_path: Path,
) -> None:
    first, second = _independent_repositories(tmp_path)
    repositories = (first, second)
    reservation = reserve_camera_bridge_authorization(
        first,
        git_head_sha=_HEAD,
        source_gate_enabled=True,
        evidence=_evidence(),
    )
    candidates = tuple(
        _completion(reservation.sentinel_sha256, f"candidate-{index}")
        for index in range(8)
    )

    def attempt(
        indexed: tuple[int, CameraBridgeCompletionEvidence],
    ) -> tuple[str, str]:
        index, evidence = indexed
        try:
            completion = seal_camera_bridge_completion(
                repositories[index % len(repositories)],
                git_head_sha=_HEAD,
                reservation=reservation,
                evidence=evidence,
            )
        except CameraBridgeAuthorizationConsumedError:
            return "consumed", ""
        return "sealed", completion.seal_sha256

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(attempt, enumerate(candidates)))

    winners = [outcome for outcome in outcomes if outcome[0] == "sealed"]
    assert len(winners) == 1
    assert sum(outcome[0] == "consumed" for outcome in outcomes) == 7
    winner_index = outcomes.index(winners[0])
    winner_evidence = candidates[winner_index]
    authenticated = authenticate_camera_bridge_completion(
        second,
        git_head_sha=_HEAD,
        expected_seal_sha256=winners[0][1],
        evidence=winner_evidence,
    )
    assert authenticated.evidence == winner_evidence
    for index, evidence in enumerate(candidates):
        if index == winner_index:
            continue
        with pytest.raises(CameraBridgeAuthorizationError, match="partial, stale"):
            authenticate_camera_bridge_completion(
                first,
                git_head_sha=_HEAD,
                expected_seal_sha256=winners[0][1],
                evidence=evidence,
            )


def test_reservation_without_completion_cannot_authenticate_action_transition(
    tmp_path: Path,
) -> None:
    first, second = _independent_repositories(tmp_path)
    reservation = reserve_camera_bridge_authorization(
        first,
        git_head_sha=_HEAD,
        source_gate_enabled=True,
        evidence=_evidence(),
    )

    assert not camera_bridge_completion_seal_path(second).exists()
    with pytest.raises(CameraBridgeAuthorizationError, match="completion seal"):
        authenticate_camera_bridge_completion(
            second,
            git_head_sha=_HEAD,
            expected_seal_sha256="0" * 64,
            evidence=_completion(reservation.sentinel_sha256),
        )


@pytest.mark.parametrize("artifact", ["pending", "final"])
@pytest.mark.parametrize("contents", [b"", b"interrupted", b"{}\n"])
def test_partial_or_tampered_completion_is_permanently_fail_closed(
    tmp_path: Path,
    artifact: str,
    contents: bytes,
) -> None:
    first, second = _independent_repositories(tmp_path)
    completion_path = (
        authorization._completion_pending_path(first)
        if artifact == "pending"
        else camera_bridge_completion_seal_path(first)
    )
    completion_path.parent.mkdir(parents=True)
    completion_path.write_bytes(contents)

    for repository in (first, second):
        assert camera_bridge_authorization_consumed(repository)
        with pytest.raises(CameraBridgeAuthorizationConsumedError):
            reserve_camera_bridge_authorization(
                repository,
                git_head_sha=_HEAD,
                source_gate_enabled=True,
                evidence=_evidence(),
            )
        with pytest.raises(CameraBridgeAuthorizationError):
            authenticate_camera_bridge_completion(
                repository,
                git_head_sha=_HEAD,
                expected_seal_sha256="0" * 64,
                evidence=_completion("1" * 64),
            )


def test_interrupted_completion_pending_consumes_every_independent_clone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = _independent_repositories(tmp_path)
    reservation = reserve_camera_bridge_authorization(
        first,
        git_head_sha=_HEAD,
        source_gate_enabled=True,
        evidence=_evidence(),
    )
    evidence = _completion(reservation.sentinel_sha256)

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("simulated host-global completion interruption")

    monkeypatch.setattr(authorization.os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="host-global completion interruption"):
        seal_camera_bridge_completion(
            first,
            git_head_sha=_HEAD,
            reservation=reservation,
            evidence=evidence,
        )

    for repository in (first, second):
        assert authorization._completion_pending_path(repository).exists()
        assert not camera_bridge_completion_seal_path(repository).exists()
        assert camera_bridge_authorization_consumed(repository)
        with pytest.raises(CameraBridgeAuthorizationConsumedError):
            seal_camera_bridge_completion(
                repository,
                git_head_sha=_HEAD,
                reservation=reservation,
                evidence=evidence,
            )
        with pytest.raises(CameraBridgeAuthorizationError, match="completion seal"):
            authenticate_camera_bridge_completion(
                repository,
                git_head_sha=_HEAD,
                expected_seal_sha256="0" * 64,
                evidence=evidence,
            )


def test_broken_host_namespace_fails_closed_without_repository_fallback(
    tmp_path: Path,
    host_authority_base: Path,
) -> None:
    first, second = _independent_repositories(tmp_path)
    namespace_blocker = host_authority_base / "Mining-Automation"
    namespace_blocker.write_text("not a directory\n", encoding="utf-8")

    for repository in (first, second):
        with pytest.raises(
            CameraBridgeAuthorizationError,
            match="redirect or non-directory",
        ):
            camera_bridge_authorization_consumed(repository)
        with pytest.raises(
            CameraBridgeAuthorizationError,
            match="redirect or non-directory",
        ):
            reserve_camera_bridge_authorization(
                repository,
                git_head_sha=_HEAD,
                source_gate_enabled=True,
                evidence=_evidence(),
            )
        assert not (repository / ".git" / "mining-automation-authorizations").exists()


def test_redirected_known_folder_root_fails_closed_without_repository_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    host_authority_base: Path,
) -> None:
    first, second = _independent_repositories(tmp_path)
    real_redirect_check = authorization._is_link_or_reparse

    def redirected_root(path: Path) -> bool:
        return path == host_authority_base or real_redirect_check(path)

    monkeypatch.setattr(authorization, "_is_link_or_reparse", redirected_root)

    for repository in (first, second):
        with pytest.raises(
            CameraBridgeAuthorizationError,
            match="Known Folder contains a redirect",
        ):
            camera_bridge_authorization_consumed(repository)
        with pytest.raises(
            CameraBridgeAuthorizationError,
            match="Known Folder contains a redirect",
        ):
            reserve_camera_bridge_authorization(
                repository,
                git_head_sha=_HEAD,
                source_gate_enabled=True,
                evidence=_evidence(),
            )
        assert not (repository / ".git" / "mining-automation-authorizations").exists()


def test_component_hash_is_canonical_and_rejects_non_json_values() -> None:
    assert canonical_camera_bridge_component_sha256(
        {"b": [2, 3], "a": 1}
    ) == canonical_camera_bridge_component_sha256({"a": 1, "b": [2, 3]})
    with pytest.raises(CameraBridgeAuthorizationError, match="canonical JSON"):
        canonical_camera_bridge_component_sha256({"bad": object()})
