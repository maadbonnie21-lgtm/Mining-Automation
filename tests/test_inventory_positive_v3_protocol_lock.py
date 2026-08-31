from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

import mining_automation.perception.inventory.positive_v3_independent_validation as validation
from mining_automation.perception.inventory.positive_v3_independent_validation import (
    InventoryPositiveV3IndependentValidationError,
)
from mining_automation.validation import inventory_v3_capture as capture

_ROOT = Path(__file__).resolve().parents[1]
_EVALUATOR = Path(
    "src/mining_automation/perception/inventory/positive_v3_independent_validation.py"
)
_CAPTURE = Path("src/mining_automation/validation/inventory_v3_capture.py")
_LOCK = Path("validation/inventory-positive-v3/protocol-lock.json")
_LOCK_SIDECAR = Path("validation/inventory-positive-v3/protocol-lock.sha256")
_LIVE_AUTHORIZATION = Path("validation/inventory-positive-v3/live-campaign-authorizations.json")


def _git(root: Path, *arguments: str, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        ("git", "-C", str(root), *arguments),
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return completed.stdout.strip()


def _git_bytes(root: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ("git", "-C", str(root), *arguments),
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise AssertionError(
            (completed.stderr or completed.stdout).decode(
                "utf-8",
                errors="replace",
            )
        )
    return completed.stdout


def _clone(tmp_path: Path) -> Path:
    clone = tmp_path / "protocol-repository"
    completed = subprocess.run(
        (
            "git",
            "-c",
            "core.autocrlf=false",
            "clone",
            "--no-checkout",
            "--no-local",
            str(_ROOT),
            str(clone),
        ),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    _git(clone, "config", "core.autocrlf", "false")
    _git(clone, "config", "user.name", "Protocol Test")
    _git(clone, "config", "user.email", "protocol-test@example.invalid")
    _git(clone, "checkout", "--detach", _git(_ROOT, "rev-parse", "HEAD"))
    return clone


def _commit_paths(
    clone: Path,
    payloads: dict[Path, bytes],
    message: str,
    *,
    committed_at: str = "2099-01-01T00:00:00Z",
) -> str:
    for path, payload in payloads.items():
        target = clone / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    _git(clone, "add", "--", *(path.as_posix() for path in payloads))
    commit_env = os.environ.copy()
    commit_env.update(
        {
            "GIT_AUTHOR_DATE": committed_at,
            "GIT_COMMITTER_DATE": committed_at,
        }
    )
    _git(clone, "commit", "-m", message, env=commit_env)
    return _git(clone, "rev-parse", "HEAD")


def _commit(clone: Path, path: Path, payload: bytes, message: str) -> str:
    return _commit_paths(clone, {path: payload}, message)


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _canonical_lock_commit(clone: Path) -> str:
    additions = _git(
        clone,
        "log",
        "--full-history",
        "--diff-filter=A",
        "--format=%H",
        "--reverse",
        "--",
        _LOCK.as_posix(),
    ).splitlines()
    assert len(additions) == 1
    return additions[0]


def _protocol_source_commit(clone: Path) -> str:
    return _git(clone, "rev-parse", f"{_canonical_lock_commit(clone)}^")


def _write_alternate_lock(
    clone: Path,
    mutate: Callable[[dict[str, object], str], None] | None = None,
    *,
    committed_at: str = "2099-01-01T00:00:00Z",
    extra_payloads: dict[Path, bytes] | None = None,
) -> str:
    lock_commit = _canonical_lock_commit(clone)
    source_commit = _protocol_source_commit(clone)
    decoded = json.loads(_git_bytes(clone, "show", f"{lock_commit}:{_LOCK.as_posix()}"))
    assert isinstance(decoded, dict)
    if mutate is not None:
        mutate(decoded, source_commit)
    payload = _canonical_bytes(decoded)
    digest = hashlib.sha256(payload).hexdigest()
    sidecar = f"{digest}  {_LOCK.name}\n".encode("ascii")
    _git(clone, "checkout", "--detach", source_commit)
    return _commit_paths(
        clone,
        {
            _LOCK: payload,
            _LOCK_SIDECAR: sidecar,
            **(extra_payloads or {}),
        },
        "alternate protocol lock",
        committed_at=committed_at,
    )


def _lock_for_authorization(clone: Path) -> tuple[str, dict[str, object]]:
    lock_commit = _canonical_lock_commit(clone)
    payload = _git_bytes(clone, "show", f"{lock_commit}:{_LOCK.as_posix()}")
    decoded = json.loads(payload)
    assert isinstance(decoded, dict)
    decoded["_test_lock_commit_sha"] = lock_commit
    decoded["_test_lock_sha256"] = hashlib.sha256(payload).hexdigest()
    return lock_commit, decoded


def _authorization_payload(
    lock: dict[str, object],
    *,
    authorization_id: str = "a" * 64,
    mutate: Callable[[dict[str, object]], None] | None = None,
) -> bytes:
    capture_binding = lock["approved_passive_capture"]
    assert isinstance(capture_binding, dict)
    entry: dict[str, object] = {
        "authorization_id": authorization_id,
        "capture_build_sha": capture_binding["build_sha"],
        "capture_configuration_id": capture_binding["capture_configuration_id"],
        "protocol_lock_git_commit_sha": lock["_test_lock_commit_sha"],
        "protocol_lock_sha256": lock["_test_lock_sha256"],
        "status": "authorized-for-passive-independent-validation-capture",
    }
    if mutate is not None:
        mutate(entry)
    return _canonical_bytes(
        {
            "activation_allowed": False,
            "authorizations": [entry],
            "schema": ("inventory-positive-v3-independent-live-campaign-authorization-registry-v1"),
        }
    )


def _patch_runtime_paths(
    clone: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(validation, "__file__", str(clone / _EVALUATOR))
    monkeypatch.setattr(capture, "__file__", str(clone / _CAPTURE))


def _assert_both_protocol_verifiers_reject(
    clone: Path,
    head: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_runtime_paths(clone, monkeypatch)
    with pytest.raises(InventoryPositiveV3IndependentValidationError):
        validation._verify_repository_state(clone, head)
    with pytest.raises(capture.PassiveInventoryV3CaptureError):
        capture._verify_capture_repository(clone)


LockedMutation = Callable[[bytes], bytes]


def test_capture_and_evaluator_lock_contracts_are_exactly_aligned() -> None:
    assert validation._PROTOCOL_LOCKED_PATHS == capture._PROTOCOL_LOCKED_PATHS
    assert validation._APPROVED_CAPTURE_SOURCE_PATHS == capture._CAPTURE_SOURCE_PATHS
    assert capture.INDEPENDENT_CAPTURE_STAGES == (
        validation._POSITIVE_STAGES + validation._NEGATIVE_STAGES
    )
    expected_policy = validation._ApprovedPassiveCaptureBinding(
        build_sha="a" * 40,
        capture_configuration_id=("inventory-positive-v3-independent-passive-natural-fill-v1"),
        source_git_blobs=(),
    ).to_dict()["policy"]
    assert expected_policy == {
        "all_owned_captures_retained": True,
        "detector_controls_capture_selection": False,
        "detector_controls_inclusion": False,
        "detector_controls_retry": False,
        "detector_controls_stage_advancement": False,
        "input_automation_allowed": False,
        "inventory_region": [567, 569, 158, 248],
        "pixel_materialization": "fixed-bgra-row-slice-only",
        "pixel_value_transformation_allowed": False,
    }


@pytest.mark.parametrize(
    ("path", "mutate"),
    [
        (
            _EVALUATOR,
            lambda payload: payload.replace(
                b"return self.detector_conformance_passed and self.approval is not None",
                b"return True",
            ),
        ),
        (
            _EVALUATOR,
            lambda payload: payload.replace(
                b'"approved-for-independent-validation-conformance"',
                b'"approval-semantics-mutated"',
                1,
            ),
        ),
        (
            _EVALUATOR,
            lambda payload: payload.replace(
                b'"row-obstruction",\n)',
                b'"row-obstruction-mutated",\n)',
                1,
            ),
        ),
        (
            Path("validation/inventory-positive-v3/preregistration.json"),
            lambda payload: payload + b" ",
        ),
        (
            Path(
                "src/mining_automation/perception/inventory/"
                "positive_v3_independent_validation_cli.py"
            ),
            lambda payload: payload + b"\n# mutated CLI semantics\n",
        ),
        (
            Path("tools/inventory_v3_independent_validation.py"),
            lambda payload: payload + b"\n# mutated official entry point\n",
        ),
        (
            Path("validation/inventory-positive-v3/protocol-lock.json"),
            lambda payload: payload + b" ",
        ),
        (
            Path("validation/inventory-positive-v3/protocol-lock.sha256"),
            lambda payload: payload + b" ",
        ),
    ],
)
def test_exact_mutated_descendant_head_cannot_change_locked_protocol(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: Path,
    mutate: LockedMutation,
) -> None:
    clone = _clone(tmp_path)
    original = (clone / path).read_bytes()
    mutated = mutate(original)
    assert mutated != original
    head = _commit(clone, path, mutated, "mutate locked protocol")
    monkeypatch.setattr(validation, "__file__", str(clone / _EVALUATOR))

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="protocol|locked|Git blob",
    ):
        validation._verify_repository_state(clone, head)


def test_public_evaluator_rejects_protocol_mutation_before_opening_pixels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clone = _clone(tmp_path)
    original = (clone / _EVALUATOR).read_bytes()
    head = _commit(
        clone,
        _EVALUATOR,
        original + b"\n# post-lock evaluator mutation\n",
        "mutate evaluator before package read",
    )
    monkeypatch.setattr(validation, "__file__", str(clone / _EVALUATOR))

    def package_load_is_forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("validation pixels were opened before protocol proof")

    monkeypatch.setattr(
        validation,
        "_load_independent_validation_dataset",
        package_load_is_forbidden,
    )
    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="Git blob changed|changed after lock",
    ):
        validation.evaluate_frozen_v3_independent_validation(
            tmp_path / "untrusted-pixels",
            repository_root=clone,
            evaluator_git_head_sha=head,
        )


def test_public_loader_rejects_protocol_mutation_before_opening_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clone = _clone(tmp_path)
    original = (clone / _EVALUATOR).read_bytes()
    head = _commit(
        clone,
        _EVALUATOR,
        original + b"\n# post-lock loader mutation\n",
        "mutate loader before package read",
    )
    monkeypatch.setattr(validation, "__file__", str(clone / _EVALUATOR))

    def package_read_is_forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("validation package opened before protocol proof")

    monkeypatch.setattr(
        validation,
        "_read_canonical_document",
        package_read_is_forbidden,
    )
    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="Git blob changed|changed after lock",
    ):
        validation.load_independent_validation_dataset(
            tmp_path / "untrusted-package",
            repository_root=clone,
            evaluator_git_head_sha=head,
        )


def test_change_then_revert_still_invalidates_locked_protocol_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clone = _clone(tmp_path)
    original = (clone / _EVALUATOR).read_bytes()
    _commit(clone, _EVALUATOR, original + b"\n# temporary mutation\n", "mutate")
    head = _commit(clone, _EVALUATOR, original, "revert bytes")
    monkeypatch.setattr(validation, "__file__", str(clone / _EVALUATOR))

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="changed after lock",
    ):
        validation._verify_repository_state(clone, head)


def test_capture_materializer_change_after_lock_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clone = _clone(tmp_path)
    path = Path("src/mining_automation/validation/inventory_v3_capture.py")
    head = _commit(
        clone,
        path,
        (clone / path).read_bytes() + b"\n# crop semantics changed\n",
        "mutate approved capture materializer",
    )
    monkeypatch.setattr(validation, "__file__", str(clone / _EVALUATOR))

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="capture|protocol-bound|Git blob",
    ):
        validation._verify_repository_state(clone, head)


def test_docs_only_descendant_keeps_locked_protocol_evaluable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clone = _clone(tmp_path)
    head = _commit(
        clone,
        Path("docs/protocol-lock-descendant-test.md"),
        b"Non-protocol documentation only.\n",
        "add non-protocol docs",
    )
    _patch_runtime_paths(clone, monkeypatch)

    assert validation._verify_repository_state(clone, head) == clone.resolve()
    assert capture._verify_capture_repository(clone).execution_head_sha == head


def test_approval_registry_only_descendant_keeps_protocol_lock_verifiable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clone = _clone(tmp_path)
    path = Path("validation/inventory-positive-v3/approved-campaigns.json")
    original = (clone / path).read_bytes()
    head = _commit(
        clone, path, original.replace(b'"entries":[]', b'"entries":[] '), "approval registry review"
    )
    monkeypatch.setattr(validation, "__file__", str(clone / _EVALUATOR))

    assert validation._verify_repository_state(clone, head) == clone.resolve()


def test_shallow_history_is_rejected_before_protocol_evaluation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clone = _clone(tmp_path)
    head = _git(clone, "rev-parse", "HEAD")
    (clone / ".git" / "shallow").write_text(head + "\n", encoding="ascii")
    monkeypatch.setattr(validation, "__file__", str(clone / _EVALUATOR))

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="full Git history",
    ):
        validation._verify_repository_state(clone, head)


def test_local_git_replacement_ref_is_rejected_by_both_verifiers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clone = _clone(tmp_path)
    head = _git(clone, "rev-parse", "HEAD")
    commit_env = os.environ.copy()
    commit_env.update(
        {
            "GIT_AUTHOR_DATE": "2099-01-01T00:00:00Z",
            "GIT_COMMITTER_DATE": "2099-01-01T00:00:00Z",
        }
    )
    replacement = _git(
        clone,
        "commit-tree",
        _git(clone, "rev-parse", "HEAD^{tree}"),
        "-p",
        _git(clone, "rev-parse", "HEAD^"),
        "-m",
        "identity-preserving local replacement",
        env=commit_env,
    )
    _git(clone, "replace", head, replacement)
    assert _git(clone, "status", "--porcelain=v1") == ""
    _patch_runtime_paths(clone, monkeypatch)

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="replacement refs",
    ):
        validation._verify_repository_state(clone, head)
    with pytest.raises(
        capture.PassiveInventoryV3CaptureError,
        match="replacement refs",
    ):
        capture._verify_capture_repository(clone)


def test_nonempty_legacy_git_graft_is_rejected_by_both_verifiers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clone = _clone(tmp_path)
    head = _git(clone, "rev-parse", "HEAD")
    grafts_path = Path(_git(clone, "rev-parse", "--git-path", "info/grafts"))
    if not grafts_path.is_absolute():
        grafts_path = clone / grafts_path
    grafts_path.parent.mkdir(parents=True, exist_ok=True)
    grafts_path.write_text(
        f"{head} {_git(clone, 'rev-parse', 'HEAD^')}\n",
        encoding="ascii",
    )
    assert _git(clone, "status", "--porcelain=v1") == ""
    _patch_runtime_paths(clone, monkeypatch)

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="legacy Git grafts",
    ):
        validation._verify_repository_state(clone, head)
    with pytest.raises(
        capture.PassiveInventoryV3CaptureError,
        match="legacy Git grafts",
    ):
        capture._verify_capture_repository(clone)


def test_merged_side_branch_change_then_revert_is_still_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clone = _clone(tmp_path)
    base = _git(clone, "rev-parse", "HEAD")
    _git(clone, "switch", "-c", "adversarial-side-branch")
    original = (clone / _EVALUATOR).read_bytes()
    _commit(clone, _EVALUATOR, original + b"\n# side mutation\n", "side mutation")
    _commit(clone, _EVALUATOR, original, "side byte revert")
    _git(clone, "checkout", "--detach", base)
    _git(clone, "merge", "--no-ff", "--no-edit", "adversarial-side-branch")
    head = _git(clone, "rev-parse", "HEAD")
    monkeypatch.setattr(validation, "__file__", str(clone / _EVALUATOR))

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="changed after lock",
    ):
        validation._verify_repository_state(clone, head)


def test_real_exact_head_mismatch_is_rejected_before_protocol_evaluation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clone = _clone(tmp_path)
    actual_head = _git(clone, "rev-parse", "HEAD")
    wrong_head = _git(clone, "rev-parse", "HEAD^")
    monkeypatch.setattr(validation, "__file__", str(clone / _EVALUATOR))

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="Git HEAD mismatch",
    ):
        validation._verify_repository_state(clone, wrong_head)
    assert _git(clone, "rev-parse", "HEAD") == actual_head


def test_tests_only_descendant_keeps_locked_protocol_evaluable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clone = _clone(tmp_path)
    head = _commit(
        clone,
        Path("tests/protocol_lock_descendant_test.py"),
        b"def test_non_protocol_descendant() -> None:\n    assert True\n",
        "add non-protocol test",
    )
    _patch_runtime_paths(clone, monkeypatch)

    assert validation._verify_repository_state(clone, head) == clone.resolve()
    assert capture._verify_capture_repository(clone).execution_head_sha == head


def test_arbitrary_new_source_executable_after_lock_is_rejected_by_both_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clone = _clone(tmp_path)
    head = _commit(
        clone,
        Path("src/mining_automation/post_lock_executable.py"),
        b"PROTOCOL_BYPASS = True\n",
        "add post-lock executable",
    )

    _assert_both_protocol_verifiers_reject(clone, head, monkeypatch)


@pytest.mark.parametrize(
    "rebind",
    [
        "arbitrary-build",
        "stale-build",
        "foreign-build",
        "wrong-source-commit",
        "capture-configuration",
    ],
)
def test_alternate_lock_cannot_rebind_source_or_capture_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rebind: str,
) -> None:
    clone = _clone(tmp_path)
    source_commit = _protocol_source_commit(clone)
    stale_commit = _git(clone, "rev-parse", f"{source_commit}^")
    commit_env = os.environ.copy()
    commit_env.update(
        {
            "GIT_AUTHOR_DATE": "2098-01-01T00:00:00Z",
            "GIT_COMMITTER_DATE": "2098-01-01T00:00:00Z",
        }
    )
    foreign_commit = _git(
        clone,
        "commit-tree",
        _git(clone, "rev-parse", f"{source_commit}^{{tree}}"),
        "-m",
        "foreign capture build",
        env=commit_env,
    )

    def mutate(lock: dict[str, object], source: str) -> None:
        protocol = lock["protocol"]
        approved_capture = lock["approved_passive_capture"]
        assert isinstance(protocol, dict)
        assert isinstance(approved_capture, dict)
        if rebind == "arbitrary-build":
            approved_capture["build_sha"] = "0" * 40
        elif rebind == "stale-build":
            approved_capture["build_sha"] = stale_commit
        elif rebind == "foreign-build":
            approved_capture["build_sha"] = foreign_commit
        elif rebind == "wrong-source-commit":
            protocol["source_commit_sha"] = stale_commit
        else:
            assert rebind == "capture-configuration"
            approved_capture["capture_configuration_id"] = "attacker-rebound-capture-configuration"
        assert source == source_commit

    head = _write_alternate_lock(clone, mutate)
    _assert_both_protocol_verifiers_reject(clone, head, monkeypatch)


def test_protocol_lock_commit_cannot_smuggle_a_third_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clone = _clone(tmp_path)
    head = _write_alternate_lock(
        clone,
        extra_payloads={Path("docs/smuggled-with-protocol-lock.md"): b"third path\n"},
    )

    _assert_both_protocol_verifiers_reject(clone, head, monkeypatch)


def test_protocol_lock_commit_cannot_be_backdated_to_its_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clone = _clone(tmp_path)
    head = _write_alternate_lock(
        clone,
        committed_at="2000-01-01T00:00:00Z",
    )

    _assert_both_protocol_verifiers_reject(clone, head, monkeypatch)


def test_one_real_post_lock_authorization_is_accepted_by_both_verifiers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clone = _clone(tmp_path)
    lock_commit, lock = _lock_for_authorization(clone)
    _git(clone, "checkout", "--detach", lock_commit)
    head = _commit_paths(
        clone,
        {_LIVE_AUTHORIZATION: _authorization_payload(lock)},
        "authorize one passive campaign",
        committed_at="2099-01-02T00:00:00Z",
    )
    _patch_runtime_paths(clone, monkeypatch)

    capture_protocol = capture._verify_capture_repository(clone)
    capture_authorization = capture._verify_live_capture_authorization(
        clone,
        capture_protocol,
    )
    assert capture_authorization.git_commit_sha == head

    assert validation._verify_repository_state(clone, head) == clone.resolve()
    evaluator_protocol = validation._current_validation_protocol_lock(clone)
    authorization_id, _authorization_time, _execution_time = (
        validation._verify_capture_execution_authorization(
            evaluator_protocol,
            capture_execution_head_sha=head,
            authorization_git_commit_sha=head,
            authorization_git_blob=_git(
                clone,
                "rev-parse",
                f"{head}:{_LIVE_AUTHORIZATION.as_posix()}",
            ),
        )
    )
    assert authorization_id == "a" * 64


def test_backdated_post_lock_authorization_is_rejected_by_both_verifiers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clone = _clone(tmp_path)
    lock_commit, lock = _lock_for_authorization(clone)
    _git(clone, "checkout", "--detach", lock_commit)
    head = _commit_paths(
        clone,
        {_LIVE_AUTHORIZATION: _authorization_payload(lock)},
        "backdated authorization",
        committed_at="2000-01-01T00:00:00Z",
    )
    _patch_runtime_paths(clone, monkeypatch)

    capture_protocol = capture._verify_capture_repository(clone)
    with pytest.raises(
        capture.PassiveInventoryV3CaptureError,
        match="committed after the protocol lock",
    ):
        capture._verify_live_capture_authorization(clone, capture_protocol)

    validation._verify_repository_state(clone, head)
    evaluator_protocol = validation._current_validation_protocol_lock(clone)
    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="committed after the protocol lock",
    ):
        validation._verify_capture_execution_authorization(
            evaluator_protocol,
            capture_execution_head_sha=head,
            authorization_git_commit_sha=head,
            authorization_git_blob=_git(
                clone,
                "rev-parse",
                f"{head}:{_LIVE_AUTHORIZATION.as_posix()}",
            ),
        )


def test_capture_execution_commit_time_cannot_predate_authorization_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clone = _clone(tmp_path)
    lock_commit, lock = _lock_for_authorization(clone)
    _git(clone, "checkout", "--detach", lock_commit)
    authorization_commit = _commit_paths(
        clone,
        {_LIVE_AUTHORIZATION: _authorization_payload(lock)},
        "authorize one passive campaign",
        committed_at="2099-01-02T00:00:00Z",
    )
    capture_head = _commit_paths(
        clone,
        {Path("tests/backdated-capture-head.txt"): b"tests-only descendant\n"},
        "backdate capture execution head",
        committed_at="2099-01-01T00:00:00Z",
    )
    _patch_runtime_paths(clone, monkeypatch)

    capture_protocol = capture._verify_capture_repository(clone)
    with pytest.raises(
        capture.PassiveInventoryV3CaptureError,
        match="execution HEAD Git time predates live authorization",
    ):
        capture._verify_live_capture_authorization(clone, capture_protocol)

    validation._verify_repository_state(clone, capture_head)
    evaluator_protocol = validation._current_validation_protocol_lock(clone)
    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="execution HEAD Git time predates live authorization",
    ):
        validation._verify_capture_execution_authorization(
            evaluator_protocol,
            capture_execution_head_sha=capture_head,
            authorization_git_commit_sha=authorization_commit,
            authorization_git_blob=_git(
                clone,
                "rev-parse",
                f"{capture_head}:{_LIVE_AUTHORIZATION.as_posix()}",
            ),
        )


def test_two_authorization_registry_touches_are_rejected_even_if_final_is_valid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clone = _clone(tmp_path)
    lock_commit, lock = _lock_for_authorization(clone)
    _git(clone, "checkout", "--detach", lock_commit)
    _commit_paths(
        clone,
        {
            _LIVE_AUTHORIZATION: _authorization_payload(
                lock,
                authorization_id="b" * 64,
            )
        },
        "first authorization touch",
        committed_at="2099-01-01T00:00:00Z",
    )
    head = _commit_paths(
        clone,
        {_LIVE_AUTHORIZATION: _authorization_payload(lock)},
        "replace authorization",
        committed_at="2099-01-02T00:00:00Z",
    )
    _patch_runtime_paths(clone, monkeypatch)

    capture_protocol = capture._verify_capture_repository(clone)
    with pytest.raises(
        capture.PassiveInventoryV3CaptureError,
        match="one post-lock Git change",
    ):
        capture._verify_live_capture_authorization(clone, capture_protocol)

    validation._verify_repository_state(clone, head)
    evaluator_protocol = validation._current_validation_protocol_lock(clone)
    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="sole post-lock registry change",
    ):
        validation._verify_capture_execution_authorization(
            evaluator_protocol,
            capture_execution_head_sha=head,
            authorization_git_commit_sha=head,
            authorization_git_blob=_git(
                clone,
                "rev-parse",
                f"{head}:{_LIVE_AUTHORIZATION.as_posix()}",
            ),
        )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("capture_build_sha", "0" * 40),
        ("capture_configuration_id", "attacker-rebound-configuration"),
        ("protocol_lock_git_commit_sha", "1" * 40),
        ("protocol_lock_sha256", "2" * 64),
    ],
)
def test_live_authorization_cannot_rebind_protocol_or_capture_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    replacement: str,
) -> None:
    clone = _clone(tmp_path)
    lock_commit, lock = _lock_for_authorization(clone)

    def mutate(entry: dict[str, object]) -> None:
        entry[field] = replacement

    _git(clone, "checkout", "--detach", lock_commit)
    head = _commit_paths(
        clone,
        {
            _LIVE_AUTHORIZATION: _authorization_payload(
                lock,
                mutate=mutate,
            )
        },
        f"rebind authorization {field}",
        committed_at="2099-01-02T00:00:00Z",
    )
    _patch_runtime_paths(clone, monkeypatch)

    capture_protocol = capture._verify_capture_repository(clone)
    with pytest.raises(capture.PassiveInventoryV3CaptureError):
        capture._verify_live_capture_authorization(clone, capture_protocol)

    validation._verify_repository_state(clone, head)
    evaluator_protocol = validation._current_validation_protocol_lock(clone)
    with pytest.raises(InventoryPositiveV3IndependentValidationError):
        validation._verify_capture_execution_authorization(
            evaluator_protocol,
            capture_execution_head_sha=head,
            authorization_git_commit_sha=head,
            authorization_git_blob=_git(
                clone,
                "rev-parse",
                f"{head}:{_LIVE_AUTHORIZATION.as_posix()}",
            ),
        )


def test_reported_authorization_commit_must_be_ancestor_of_capture_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clone = _clone(tmp_path)
    lock_commit, lock = _lock_for_authorization(clone)
    payload = _authorization_payload(lock)
    _git(clone, "checkout", "--detach", lock_commit)
    foreign_authorization = _commit_paths(
        clone,
        {_LIVE_AUTHORIZATION: payload},
        "authorization on foreign side",
        committed_at="2099-01-02T00:00:00Z",
    )
    _git(clone, "checkout", "--detach", lock_commit)
    capture_head = _commit_paths(
        clone,
        {_LIVE_AUTHORIZATION: payload},
        "authorization on capture branch",
        committed_at="2099-01-02T00:00:00Z",
    )
    _patch_runtime_paths(clone, monkeypatch)
    validation._verify_repository_state(clone, capture_head)
    evaluator_protocol = validation._current_validation_protocol_lock(clone)

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="not an ancestor of capture execution HEAD",
    ):
        validation._verify_capture_execution_authorization(
            evaluator_protocol,
            capture_execution_head_sha=capture_head,
            authorization_git_commit_sha=foreign_authorization,
            authorization_git_blob=_git(
                clone,
                "rev-parse",
                f"{capture_head}:{_LIVE_AUTHORIZATION.as_posix()}",
            ),
        )


@pytest.mark.parametrize(
    "path",
    [
        _EVALUATOR,
        _CAPTURE,
        Path("validation/inventory-positive-v3/preregistration.json"),
    ],
)
def test_assume_unchanged_cannot_hide_protocol_bound_worktree_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: Path,
) -> None:
    clone = _clone(tmp_path)
    head = _git(clone, "rev-parse", "HEAD")
    target = clone / path
    target.write_bytes(target.read_bytes() + b"\n")
    _git(clone, "update-index", "--assume-unchanged", "--", path.as_posix())
    assert _git(clone, "status", "--porcelain=v1") == ""

    _assert_both_protocol_verifiers_reject(clone, head, monkeypatch)


def test_assume_unchanged_cannot_rebind_approval_registry_from_expected_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clone = _clone(tmp_path)
    head = _git(clone, "rev-parse", "HEAD")
    registry_path = Path("validation/inventory-positive-v3/approved-campaigns.json")
    sidecar_path = registry_path.with_suffix(".json.sha256")
    registry = json.loads((clone / registry_path).read_bytes())
    assert isinstance(registry, dict)
    registry["entries"] = [{}]
    payload = _canonical_bytes(registry)
    digest = hashlib.sha256(payload).hexdigest()
    (clone / registry_path).write_bytes(payload)
    (clone / sidecar_path).write_bytes(f"{digest}  {registry_path.name}\n".encode("ascii"))
    _git(
        clone,
        "update-index",
        "--assume-unchanged",
        "--",
        registry_path.as_posix(),
        sidecar_path.as_posix(),
    )
    assert _git(clone, "status", "--porcelain=v1") == ""
    monkeypatch.setattr(validation, "__file__", str(clone / _EVALUATOR))

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="differs from exact evaluator HEAD",
    ):
        validation._read_approval_registry(clone, head)
