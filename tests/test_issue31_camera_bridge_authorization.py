"""R2.2 one-shot authorization and durable reservation regressions."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from mining_automation.validation import camera_bridge_authorization as authorization
from mining_automation.validation.camera_bridge_authorization import (
    CAMERA_BRIDGE_AUTHORIZATION_CAMPAIGN_ID,
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


def _evidence(seed: str = "baseline") -> CameraBridgeAuthorizationEvidence:
    def digest(label: str) -> str:
        return hashlib.sha256(f"{seed}:{label}".encode()).hexdigest()

    return CameraBridgeAuthorizationEvidence(
        r1_report_sha256=digest("r1"),
        r2_report_sha256=digest("r2"),
        north_report_sha256=digest("north-report"),
        north_post_sha256=digest("north-post"),
        commit_sha256=digest("commit"),
        target_hwnd=123,
        target_process_id=456,
        target_thread_id=789,
        target_class_name="SunAwtFrame",
        target_title_sha256=digest("title"),
    )


def _ordinary_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    (repository / ".git").mkdir(parents=True)
    return repository


def _completion(
    reservation_sha256: str,
    seed: str = "complete",
) -> CameraBridgeCompletionEvidence:
    def digest(label: str) -> str:
        return hashlib.sha256(f"{seed}:{label}".encode()).hexdigest()

    return CameraBridgeCompletionEvidence(
        authorization_sentinel_sha256=reservation_sha256,
        capture_report_sha256=digest("report"),
        receipt_sha256=digest("receipt"),
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
) -> None:
    first, common = _linked_worktree(tmp_path, "first")
    second, _ = _linked_worktree(tmp_path, "second")
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "forged-git-dir"))
    monkeypatch.setenv("GIT_COMMON_DIR", str(tmp_path / "forged-common-dir"))

    first_path = camera_bridge_authorization_sentinel_path(first)
    second_path = camera_bridge_authorization_sentinel_path(second)

    assert repository_common_git_dir(first) == common.resolve()
    assert first_path == second_path
    assert first_path.name == f"{CAMERA_BRIDGE_AUTHORIZATION_CAMPAIGN_ID}.consumed.json"
    assert str(tmp_path / "forged-git-dir") not in str(first_path)


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
    assert reservation.as_dict()["state"] == "consumed_at_final_pre_input_seam"
    assert reservation.as_dict()["maximum_physical_primitives"] == 1
    assert reservation.as_dict()["hold_seconds"] == 0.043
    assert reservation.as_dict()["key"] == "right"
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
        "title_substring": "runelite",
    }


def test_sequential_and_alternate_worktree_second_reservations_are_refused(
    tmp_path: Path,
) -> None:
    first, _ = _linked_worktree(tmp_path, "first")
    second, _ = _linked_worktree(tmp_path, "second")
    reserve_camera_bridge_authorization(
        first,
        git_head_sha=_HEAD,
        source_gate_enabled=True,
        evidence=_evidence(),
    )

    for repository in (first, second):
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
    repository = _ordinary_repository(tmp_path)
    sentinel = camera_bridge_authorization_sentinel_path(repository)
    sentinel.parent.mkdir(parents=True)
    sentinel.write_bytes(contents)

    with pytest.raises(CameraBridgeAuthorizationConsumedError):
        reserve_camera_bridge_authorization(
            repository,
            git_head_sha=_HEAD,
            source_gate_enabled=True,
            evidence=_evidence(),
        )
    with pytest.raises(CameraBridgeAuthorizationError, match="partial, stale, or tampered"):
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
    repository = _ordinary_repository(tmp_path)

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("simulated durable-write interruption")

    monkeypatch.setattr(authorization.os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="simulated durable-write interruption"):
        reserve_camera_bridge_authorization(
            repository,
            git_head_sha=_HEAD,
            source_gate_enabled=True,
            evidence=_evidence(),
        )

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
    repository = _ordinary_repository(tmp_path)

    def attempt() -> str:
        try:
            reserve_camera_bridge_authorization(
                repository,
                git_head_sha=_HEAD,
                source_gate_enabled=True,
                evidence=_evidence(),
            )
        except CameraBridgeAuthorizationConsumedError:
            return "consumed"
        return "reserved"

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(lambda _index: attempt(), range(8)))

    assert outcomes.count("reserved") == 1
    assert outcomes.count("consumed") == 7


def test_separate_processes_share_one_reservation_and_restart_stays_consumed(
    tmp_path: Path,
) -> None:
    repository = _ordinary_repository(tmp_path)
    script = """
import hashlib
import sys
from pathlib import Path
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
    north_report_sha256=digest("north-report"),
    north_post_sha256=digest("north-post"),
    commit_sha256=digest("commit"),
    target_hwnd=123,
    target_process_id=456,
    target_thread_id=789,
    target_class_name="SunAwtFrame",
    target_title_sha256=digest("title"),
)
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
    print("reserved", flush=True)
"""

    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(repository)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _index in range(2)
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

    restarted = subprocess.run(
        [sys.executable, "-c", script, str(repository)],
        input="start\n",
        check=True,
        capture_output=True,
        text=True,
        timeout=15.0,
    )
    assert restarted.stdout.strip() == "consumed"


def test_authentication_rejects_changed_head_or_dynamic_evidence(
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

    for head, observed_evidence in (
        ("b" * 40, evidence),
        (_HEAD, _evidence("substituted-report")),
    ):
        with pytest.raises(CameraBridgeAuthorizationError):
            authenticate_camera_bridge_authorization(
                repository,
                git_head_sha=head,
                expected_sentinel_sha256=reservation.sentinel_sha256,
                evidence=observed_evidence,
            )


def test_completion_requires_and_authenticates_exact_full_transaction(
    tmp_path: Path,
) -> None:
    repository = _ordinary_repository(tmp_path)
    reservation = reserve_camera_bridge_authorization(
        repository,
        git_head_sha=_HEAD,
        source_gate_enabled=True,
        evidence=_evidence(),
    )
    evidence = _completion(reservation.sentinel_sha256)

    completion = seal_camera_bridge_completion(
        repository,
        git_head_sha=_HEAD,
        reservation=reservation,
        evidence=evidence,
    )
    authenticated = authenticate_camera_bridge_completion(
        repository,
        git_head_sha=_HEAD,
        expected_seal_sha256=completion.seal_sha256,
        evidence=evidence,
    )

    assert completion.as_dict() == authenticated.as_dict()
    assert completion.as_dict()["state"] == "complete_post_input_transaction_sealed"
    assert completion.as_dict()[
        "reservation_without_this_seal_is_not_an_action_transition"
    ] is True


def test_concurrent_completion_seal_has_one_immutable_winner(
    tmp_path: Path,
) -> None:
    repository = _ordinary_repository(tmp_path)
    reservation = reserve_camera_bridge_authorization(
        repository,
        git_head_sha=_HEAD,
        source_gate_enabled=True,
        evidence=_evidence(),
    )
    candidates = tuple(
        _completion(reservation.sentinel_sha256, f"candidate-{index}")
        for index in range(8)
    )

    def attempt(evidence: CameraBridgeCompletionEvidence) -> tuple[str, str]:
        try:
            completion = seal_camera_bridge_completion(
                repository,
                git_head_sha=_HEAD,
                reservation=reservation,
                evidence=evidence,
            )
        except CameraBridgeAuthorizationConsumedError:
            return "consumed", ""
        return "sealed", completion.seal_sha256

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(attempt, candidates))

    winners = [outcome for outcome in outcomes if outcome[0] == "sealed"]
    assert len(winners) == 1
    assert sum(outcome[0] == "consumed" for outcome in outcomes) == 7
    winner_index = outcomes.index(winners[0])
    winner_evidence = candidates[winner_index]
    authenticated = authenticate_camera_bridge_completion(
        repository,
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
                repository,
                git_head_sha=_HEAD,
                expected_seal_sha256=winners[0][1],
                evidence=evidence,
            )


def test_reservation_without_completion_cannot_authenticate_action_transition(
    tmp_path: Path,
) -> None:
    repository = _ordinary_repository(tmp_path)
    reservation = reserve_camera_bridge_authorization(
        repository,
        git_head_sha=_HEAD,
        source_gate_enabled=True,
        evidence=_evidence(),
    )

    assert not camera_bridge_completion_seal_path(repository).exists()
    with pytest.raises(CameraBridgeAuthorizationError, match="completion seal"):
        authenticate_camera_bridge_completion(
            repository,
            git_head_sha=_HEAD,
            expected_seal_sha256="0" * 64,
            evidence=_completion(reservation.sentinel_sha256),
        )


@pytest.mark.parametrize("contents", [b"", b"interrupted", b"{}\n"])
def test_partial_or_tampered_completion_is_permanently_fail_closed(
    tmp_path: Path,
    contents: bytes,
) -> None:
    repository = _ordinary_repository(tmp_path)
    completion_path = camera_bridge_completion_seal_path(repository)
    completion_path.parent.mkdir(parents=True)
    completion_path.write_bytes(contents)

    assert camera_bridge_authorization_consumed(repository)
    with pytest.raises(CameraBridgeAuthorizationConsumedError):
        reserve_camera_bridge_authorization(
            repository,
            git_head_sha=_HEAD,
            source_gate_enabled=True,
            evidence=_evidence(),
        )


def test_component_hash_is_canonical_and_rejects_non_json_values() -> None:
    assert canonical_camera_bridge_component_sha256(
        {"b": [2, 3], "a": 1}
    ) == canonical_camera_bridge_component_sha256({"a": 1, "b": [2, 3]})
    with pytest.raises(CameraBridgeAuthorizationError, match="canonical JSON"):
        canonical_camera_bridge_component_sha256({"bad": object()})
