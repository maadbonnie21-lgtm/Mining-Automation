from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from time import sleep
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import mining_automation.perception.inventory.positive_v3_independent_validation as frozen
import validation.inventory_v3_protocol_v2.protocol as protocol_v2
from mining_automation.capture.windows import CapturedPixels, WindowInfo, WindowsCaptureBackend
from mining_automation.capture.windows.testing import FakeWin32Api
from mining_automation.validation import inventory_v3_capture as legacy_capture
from tests import test_inventory_positive_v3_independent_validation as v1_support
from tests import test_inventory_v3_protocol_v2_protocol as v2_support
from validation.inventory_v3_protocol_v2 import producer as producer_v2

_DERIVED_SESSION = "frozen-v1-evaluator-source-session.json"
_DERIVED_SEAL = "frozen-v1-evaluator-completion-seal.json"
_ORIGINAL_SESSION = "source-session-report.json"
_ORIGINAL_SEAL = "source-completion-seal.json"
_PASS_COUNTS = (0, 1, 5, 27, 28, None, None)
_VISIBILITIES = (
    "inventory-visible",
    "inventory-visible",
    "inventory-visible",
    "inventory-visible",
    "inventory-visible",
    "wrong-tab-visible",
    "inventory-obstructed",
)


@dataclass(frozen=True, slots=True)
class _BridgePackage:
    root: Path
    source_session: Mapping[str, object]
    source_session_payload: bytes
    source_seal: Mapping[str, object]
    source_seal_payload: bytes
    derived_session: Mapping[str, object]
    derived_session_payload: bytes
    derived_seal: Mapping[str, object]
    derived_seal_payload: bytes
    evidence_snapshot: Mapping[str, bytes]


def _read_mapping(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _changed_keys(before: Mapping[str, object], after: Mapping[str, object]) -> set[str]:
    return {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}


def _install_synthetic_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    lock = v1_support._synthetic_protocol_lock()
    monkeypatch.setattr(
        frozen,
        "_verify_repository_state",
        lambda root, _expected_head: root.resolve(strict=True),
    )
    monkeypatch.setattr(
        frozen,
        "_current_validation_protocol_lock",
        lambda _root: lock,
    )
    monkeypatch.setattr(
        frozen,
        "_verify_capture_execution_authorization",
        lambda *_args, **_kwargs: (
            "2" * 64,
            "2098-12-31T23:59:30Z",
            "2098-12-31T23:59:45Z",
        ),
    )


def _write_document(path: Path, value: object) -> tuple[bytes, str]:
    return v1_support._write_document(path, value)


def _rebind_outer_documents(root: Path) -> None:
    manifest = _read_mapping(root / "campaign-manifest.json")
    manifest["dataset_id"] = frozen._content_bound_dataset_id(manifest)
    _, manifest_sha = _write_document(root / "campaign-manifest.json", manifest)

    review = _read_mapping(root / "reviewer-truth.json")
    review["campaign_id"] = manifest["campaign_id"]
    review["campaign_manifest_sha256"] = manifest_sha
    review["dataset_id"] = manifest["dataset_id"]
    _, review_sha = _write_document(root / "reviewer-truth.json", review)

    package = _read_mapping(root / "validation-package.json")
    campaign_ref = package["campaign_manifest"]
    reviewer_ref = package["reviewer_truth"]
    assert isinstance(campaign_ref, dict)
    assert isinstance(reviewer_ref, dict)
    campaign_ref["sha256"] = manifest_sha
    reviewer_ref["sha256"] = review_sha
    _write_document(root / "validation-package.json", package)


def _evidence_bytes(root: Path) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if path.suffix == ".bgra" or relative.endswith(
            (
                "-capture-report.json",
                "-capture-report.json.sha256",
                "-owned-frame.json",
                "-owned-frame.json.sha256",
            )
        ):
            result[relative] = path.read_bytes()
    return result


def _build_bridged_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _BridgePackage:
    _install_synthetic_lock(monkeypatch)
    root = v1_support._build_synthetic_contract_package(tmp_path)
    evidence_snapshot = _evidence_bytes(root)

    existing_session_path = root / "source" / "session-report.json"
    existing_seal_path = root / "source" / "completion-seal.json"
    evaluator_session = _read_mapping(existing_session_path)
    evaluator_seal = _read_mapping(existing_seal_path)
    session_id = evaluator_session["session_id"]
    assert isinstance(session_id, str)

    evaluator_campaign_id = protocol_v2._content_bound_campaign_id(session_id)
    assert evaluator_campaign_id == frozen._content_bound_campaign_id(session_id)
    assert evaluator_session["campaign_id"] == evaluator_campaign_id

    source_session = copy.deepcopy(evaluator_session)
    source_campaign_id = protocol_v2._legacy_source_campaign_id(session_id)
    assert source_campaign_id != evaluator_campaign_id
    source_session["campaign_id"] = source_campaign_id
    source_session_payload, _ = _write_document(root / _ORIGINAL_SESSION, source_session)

    source_seal = copy.deepcopy(evaluator_seal)
    source_seal["campaign_id"] = source_campaign_id
    source_seal["source_session_report_sha256"] = _sha256(source_session_payload)
    source_seal_payload, _ = _write_document(root / _ORIGINAL_SEAL, source_seal)

    (
        derived_session,
        derived_session_payload,
        derived_seal,
        derived_seal_payload,
    ) = protocol_v2._evaluator_compatible_source_documents(
        SimpleNamespace(session=source_session, completion_seal=source_seal),
        evaluator_campaign_id,
    )
    assert derived_session == evaluator_session
    assert derived_seal == evaluator_seal
    derived_session_written, derived_session_sha = _write_document(
        root / _DERIVED_SESSION, derived_session
    )
    derived_seal_written, derived_seal_sha = _write_document(root / _DERIVED_SEAL, derived_seal)
    assert derived_session_written == derived_session_payload
    assert derived_seal_written == derived_seal_payload

    manifest = _read_mapping(root / "campaign-manifest.json")
    manifest["source_session_report"] = {
        "path": _DERIVED_SESSION,
        "sha256": derived_session_sha,
    }
    manifest["source_completion_seal"] = {
        "path": _DERIVED_SEAL,
        "sha256": derived_seal_sha,
    }
    _write_document(root / "campaign-manifest.json", manifest)
    _rebind_outer_documents(root)

    return _BridgePackage(
        root=root,
        source_session=source_session,
        source_session_payload=source_session_payload,
        source_seal=source_seal,
        source_seal_payload=source_seal_payload,
        derived_session=derived_session,
        derived_session_payload=derived_session_payload,
        derived_seal=derived_seal,
        derived_seal_payload=derived_seal_payload,
        evidence_snapshot=evidence_snapshot,
    )


def _load_with_frozen_v1(package: _BridgePackage) -> frozen.IndependentValidationDataset:
    return v1_support.load_independent_validation_dataset(package.root)


def _conformance_backend_factory() -> Callable[[], WindowsCaptureBackend]:
    frames = tuple(
        v1_support._full_frame_from_region(region)
        for region in v1_support._development_conformance_payloads()
    )
    sequence = iter(frames)

    def factory() -> WindowsCaptureBackend:
        payload = next(sequence)
        api = FakeWin32Api(
            windows=[
                WindowInfo(
                    hwnd=v2_support._WINDOW_HANDLE,
                    title="RuneLite - synthetic private title",
                    class_name="SunAwtFrame",
                    is_visible=True,
                    is_minimized=False,
                    client_width=protocol_v2.SUPPORTED_FRAME_WIDTH,
                    client_height=protocol_v2.SUPPORTED_FRAME_HEIGHT,
                )
            ],
            captures={
                v2_support._WINDOW_HANDLE: CapturedPixels(
                    payload=payload,
                    width=protocol_v2.SUPPORTED_FRAME_WIDTH,
                    height=protocol_v2.SUPPORTED_FRAME_HEIGHT,
                )
            },
            dpi_by_hwnd={v2_support._WINDOW_HANDLE: 144},
        )
        return WindowsCaptureBackend(win32_api=api)

    return factory


def _install_full_lifecycle_seams(
    sandbox_root: Path,
    attempt_base: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[
    protocol_v2.ProtocolV2LockBinding,
    protocol_v2.LiveAuthorizationBinding,
    protocol_v2.SourceMetadataBinding,
]:
    """Create disposable evidence while retaining both real evaluator layers."""

    synthetic_protocol = v2_support._protocol(sandbox_root)
    authorization = v2_support._authorization()
    protocol = replace(
        synthetic_protocol,
        repository_root=v1_support._ROOT.resolve(strict=True),
        evaluator_head_sha=v1_support._EVALUATOR_HEAD,
    )
    sandbox_paths = protocol_v2.ProtocolV2Paths.for_authorization(
        sandbox_root,
        authorization.authorization_id,
        protocol.lock_sha256,
        attempt_base=attempt_base,
    )
    paths = replace(
        sandbox_paths,
        repository_root=protocol.repository_root,
    )

    def fixed_paths(
        _cls: type[protocol_v2.ProtocolV2Paths],
        _repository_root: Path,
        _authorization_id: str,
        _protocol_lock_sha256: str,
        *,
        attempt_base: Path | None = None,
    ) -> protocol_v2.ProtocolV2Paths:
        del attempt_base
        return paths

    monkeypatch.setattr(
        protocol_v2.ProtocolV2Paths,
        "for_authorization",
        classmethod(fixed_paths),
    )
    monkeypatch.setattr(
        protocol_v2,
        "verify_protocol_v2_repository",
        lambda _root, *, expected_head: protocol,
    )
    monkeypatch.setattr(
        protocol_v2,
        "verify_live_authorization",
        lambda _protocol, *, access_hook=None: authorization,
    )
    approval_registry_path = protocol.repository_root.joinpath(
        *protocol_v2._APPROVAL_REGISTRY_PATH.parts
    )
    approval_registry_payload = approval_registry_path.read_bytes()
    monkeypatch.setattr(
        protocol_v2,
        "_verify_approval_registry_absent",
        lambda _protocol, *, access_hook=None: approval_registry_payload,
    )
    monkeypatch.setattr(
        protocol_v2,
        "_git",
        lambda _root, *args, **kwargs: "2024-12-31T23:59:30+00:00",
    )
    legacy_protocol = v2_support._legacy_binding()
    legacy_authorization = v2_support._legacy_authorization()
    reservation_root = (
        attempt_base / "Mining-Automation" / "inventory-positive-v3-independent-reservations"
    )
    monkeypatch.setattr(
        legacy_capture,
        "_verify_capture_repository",
        lambda _root: legacy_protocol,
    )
    monkeypatch.setattr(
        legacy_capture,
        "_verify_live_capture_authorization",
        lambda _root, _protocol: legacy_authorization,
    )
    monkeypatch.setattr(
        legacy_capture,
        "_new_source_owned_backend",
        _conformance_backend_factory(),
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
    capture_times = v2_support._timestamps()
    monkeypatch.setattr(legacy_capture, "_utc_timestamp", lambda: next(capture_times))
    monkeypatch.setattr(
        legacy_capture.platform,
        "platform",
        lambda: "Windows-synthetic-test",
    )
    monkeypatch.setattr(
        producer_v2,
        "observe_windows_identity",
        lambda: producer_v2.WindowsProducerIdentity(
            computer_name="SYNTHETIC-HOST",
            user_name="synthetic-user",
            session_id=7,
        ),
    )
    original_subprocess_run = subprocess.run
    launcher_path = protocol.repository_root / "tools" / "capture_inventory_v3_independent.py"

    def allow_synthetic_ancestry(
        command: object,
        *args: object,
        **kwargs: object,
    ) -> object:
        if isinstance(command, (tuple, list)) and str(launcher_path) in command:
            assert command == (
                protocol_v2.sys.executable,
                "-I",
                "-S",
                str(launcher_path),
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
            assert kwargs == {"cwd": protocol.repository_root, "check": False}
            legacy_capture.run_passive_inventory_v3_capture_campaign(
                inputs=legacy_capture.PassiveInventoryV3CaptureInputs(
                    operator="operator-a",
                    runelite_build="operator-asserted-build",
                    client_mode="fixed",
                    theme="dark",
                    renderer="gpu",
                ),
                repository_root=sandbox_root,
            )
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        if (
            isinstance(command, (tuple, list))
            and "merge-base" in command
            and "--is-ancestor" in command
        ):
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        return original_subprocess_run(command, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(protocol_v2.subprocess, "run", allow_synthetic_ancestry)

    base_frozen_lock = v1_support._synthetic_protocol_lock()
    frozen_capture = replace(
        base_frozen_lock.approved_passive_capture,
        build_sha=protocol_v2.PROTOCOL_V1_SOURCE_HEAD,
    )
    frozen_lock = replace(
        base_frozen_lock,
        lock_git_commit_sha=protocol_v2.PROTOCOL_V1_LOCK_HEAD,
        lock_git_committed_at_utc="2024-12-31T23:59:00Z",
        lock_sha256=protocol_v2.PROTOCOL_V1_LOCK_SHA256,
        protocol_source_commit_sha=protocol_v2.PROTOCOL_V1_SOURCE_HEAD,
        approved_passive_capture=frozen_capture,
    )
    monkeypatch.setattr(
        frozen,
        "_verify_repository_state",
        lambda root, _expected_head: root.resolve(strict=True),
    )
    monkeypatch.setattr(frozen, "_current_validation_protocol_lock", lambda _root: frozen_lock)
    monkeypatch.setattr(
        frozen,
        "_verify_capture_execution_authorization",
        lambda *_args, **_kwargs: (
            authorization.authorization_id,
            "2024-12-31T23:59:45Z",
            "2024-12-31T23:59:50Z",
        ),
    )

    attestation_path = protocol_v2.run_passive_capture_protocol_v2(
        protocol.repository_root,
        expected_head=protocol.evaluator_head_sha,
        operator="operator-a",
        runelite_build="operator-asserted-build",
        client_mode="fixed",
        theme="dark",
        renderer="gpu",
        attempt_base=attempt_base,
    )
    assert attestation_path == paths.source_campaign_root / protocol_v2._PRODUCER_ATTESTATION_NAME
    source = protocol_v2.preflight_source_metadata(
        protocol,
        authorization,
        attempt_base=attempt_base,
    )
    return protocol, authorization, source


def _review_provider(
    counts: tuple[int | None, ...],
) -> Callable[[Mapping[str, object]], Mapping[str, object]]:
    def provide(template: Mapping[str, object]) -> Mapping[str, object]:
        raw_cases = template.get("cases")
        assert isinstance(raw_cases, list)
        assert len(raw_cases) == len(counts) == len(_VISIBILITIES)
        sleep(0.002)
        cases: list[dict[str, object]] = []
        for index, (raw_case, occupied, visibility) in enumerate(
            zip(raw_cases, counts, _VISIBILITIES, strict=True),
            start=1,
        ):
            assert isinstance(raw_case, dict)
            cases.append(
                {
                    "review_case_id": raw_case["review_case_id"],
                    "truth": {
                        "decision": "approved",
                        "drag_visible": False,
                        "hover_visible": False,
                        "occupied_slots": occupied,
                        "ordinary_iron_only": index <= 5,
                        "quantity_text_visible": False,
                        "review_note": "synthetic full-lifecycle rehearsal",
                        "selected_item_visible": False,
                        "visibility": visibility,
                    },
                }
            )
        return {
            "cases": cases,
            "reviewed_at_utc": protocol_v2._format_utc(datetime.now(UTC)),
            "reviewer": "reviewer-b",
        }

    return provide


def _run_to_published_review(
    root: Path,
    attempt_base: Path,
    protocol: protocol_v2.ProtocolV2LockBinding,
    *,
    counts: tuple[int | None, ...],
) -> None:
    protocol_v2.finalize_acquisition(
        root,
        expected_head=protocol.evaluator_head_sha,
        attempt_base=attempt_base,
    )
    protocol_v2.prepare_reviewer_intake(
        root,
        expected_head=protocol.evaluator_head_sha,
        attempt_base=attempt_base,
    )
    protocol_v2.record_reviewer_submission(
        root,
        expected_head=protocol.evaluator_head_sha,
        reviewer="reviewer-b",
        truth_provider=_review_provider(counts),
        attempt_base=attempt_base,
    )
    protocol_v2.publish_reviewed_package(
        root,
        expected_head=protocol.evaluator_head_sha,
        attempt_base=attempt_base,
    )


def _run_to_terminal_evaluation(
    root: Path,
    attempt_base: Path,
    protocol: protocol_v2.ProtocolV2LockBinding,
    *,
    counts: tuple[int | None, ...],
) -> protocol_v2.TerminalEvaluation:
    _run_to_published_review(
        root,
        attempt_base,
        protocol,
        counts=counts,
    )
    sleep(0.002)
    return protocol_v2.evaluate_locked_protocol_v2(
        root,
        expected_head=protocol.evaluator_head_sha,
        attempt_base=attempt_base,
    )


def _coherently_rewrite_reviewed_truth(
    source: protocol_v2.SourceMetadataBinding,
    *,
    mutate_review: Callable[[dict[str, object]], None],
    reviewed_record_updates: Mapping[str, object] | None = None,
) -> None:
    reviewed_root = source.paths.reviewed_package_root
    review = _read_mapping(reviewed_root / protocol_v2._REVIEWER_TRUTH_NAME)
    mutate_review(review)
    _, review_sha = _write_document(
        reviewed_root / protocol_v2._REVIEWER_TRUTH_NAME,
        review,
    )

    package = _read_mapping(reviewed_root / protocol_v2._VALIDATION_PACKAGE_NAME)
    review_ref = package["reviewer_truth"]
    assert isinstance(review_ref, dict)
    review_ref["sha256"] = review_sha
    _, package_sha = _write_document(
        reviewed_root / protocol_v2._VALIDATION_PACKAGE_NAME,
        package,
    )

    reviewed_record_path = reviewed_root / "protocol-v2-reviewed-package.json"
    reviewed_record = _read_mapping(reviewed_record_path)
    reviewed_record["reviewer_truth_sha256"] = review_sha
    reviewed_record["validation_package_sha256"] = package_sha
    reviewed_record.update(reviewed_record_updates or {})
    _write_document(reviewed_record_path, reviewed_record)

    rebound_paths = {
        protocol_v2._REVIEWER_TRUTH_NAME,
        f"{protocol_v2._REVIEWER_TRUTH_NAME}.sha256",
        protocol_v2._VALIDATION_PACKAGE_NAME,
        f"{protocol_v2._VALIDATION_PACKAGE_NAME}.sha256",
        "protocol-v2-reviewed-package.json",
        "protocol-v2-reviewed-package.json.sha256",
    }
    tree_path = reviewed_root / protocol_v2._PACKAGE_TREE_NAME
    tree = _read_mapping(tree_path)
    entries = tree["entries"]
    assert isinstance(entries, list)
    rebound: set[str] = set()
    for raw in entries:
        assert isinstance(raw, dict)
        relative = raw.get("path")
        if not isinstance(relative, str) or relative not in rebound_paths:
            continue
        payload = reviewed_root.joinpath(*relative.split("/")).read_bytes()
        raw["sha256"] = _sha256(payload)
        raw["size_bytes"] = len(payload)
        rebound.add(relative)
    assert rebound == rebound_paths
    _, tree_sha = _write_document(tree_path, tree)

    terminal_path = (
        source.paths.attempt_root / "publish-reviewed-package-terminal.json"
    )
    terminal = _read_mapping(terminal_path)
    terminal["output_sha256"] = tree_sha
    _write_document(terminal_path, terminal)


def _coherently_replace_reviewed_truth_with_c2_pass(
    source: protocol_v2.SourceMetadataBinding,
) -> None:
    def replace_case_two(review: dict[str, object]) -> None:
        cases = review["cases"]
        assert isinstance(cases, list)
        case_two = cases[1]
        assert isinstance(case_two, dict)
        assert case_two["occupied_slots"] == 2
        case_two["occupied_slots"] = 1

    _coherently_rewrite_reviewed_truth(
        source,
        mutate_review=replace_case_two,
    )


def _coherently_rebind_reviewed_reviewer(
    source: protocol_v2.SourceMetadataBinding,
    *,
    replacement: str,
) -> None:
    def replace_reviewer(review: dict[str, object]) -> None:
        assert review["reviewer"] == "reviewer-b"
        review["reviewer"] = replacement

    _coherently_rewrite_reviewed_truth(
        source,
        mutate_review=replace_reviewer,
        reviewed_record_updates={"reviewer": replacement},
    )


def _assert_rewritten_review_projection_rejected(
    repository_root: Path,
    attempt_base: Path,
    protocol: protocol_v2.ProtocolV2LockBinding,
    source: protocol_v2.SourceMetadataBinding,
    monkeypatch: pytest.MonkeyPatch,
    *,
    submission_before: Mapping[str, bytes],
) -> None:
    evaluator_calls = 0

    def forbidden_evaluator(*args: object, **kwargs: object) -> object:
        nonlocal evaluator_calls
        del args, kwargs
        evaluator_calls += 1
        raise AssertionError("rewritten truth must not reach the frozen evaluator")

    monkeypatch.setattr(
        frozen,
        "evaluate_frozen_v3_independent_validation",
        forbidden_evaluator,
    )
    events: list[tuple[str, str]] = []
    sleep(0.002)
    with pytest.raises(
        protocol_v2.InventoryV3ProtocolV2Error,
        match="not the deterministic original submission projection",
    ):
        protocol_v2.evaluate_locked_protocol_v2(
            repository_root,
            expected_head=protocol.evaluator_head_sha,
            attempt_base=attempt_base,
            access_hook=lambda phase, kind, _path: events.append((phase, kind)),
        )

    submission_root = source.paths.review_intake_root / "submission"
    assert _snapshot_regular_files(submission_root) == submission_before
    assert evaluator_calls == 0
    assert events.count(("sensitive", "reviewer_truth_opened")) == 1
    assert ("sensitive", "validation_pixels_opened") not in events
    reservation = _read_mapping(
        source.paths.attempt_root / "evaluate-locked-candidate-reserved.json"
    )
    terminal = _read_mapping(
        source.paths.attempt_root / "evaluate-locked-candidate-terminal.json"
    )
    failure_root = source.paths.attempt_root / "evaluate-locked-candidate-failure"
    private_failure = _read_mapping(failure_root / "private-failure.json")
    public_failure = _read_mapping(failure_root / "public-failure-receipt.json")
    assert reservation["status"] == "reserved-irrevocably"
    assert terminal["status"] == "failed-terminal"
    assert terminal["contract_id"] == "ATTEMPT_INTEGRITY_FAILURE"
    assert terminal["retry_allowed"] is False
    assert private_failure["contract_id"] == "ATTEMPT_INTEGRITY_FAILURE"
    assert private_failure["terminal_status"] == "failed-terminal-permanent"
    assert public_failure["contract_id"] == "ATTEMPT_INTEGRITY_FAILURE"
    assert public_failure["terminal_status"] == "failed-permanent"
    assert _snapshot_regular_files(source.paths.result_root) == {}
    assert not source.paths.approval_request_root.exists()


def test_bridge_is_accepted_by_complete_frozen_v1_package_parser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _build_bridged_package(tmp_path, monkeypatch)

    source_session_before = (package.root / _ORIGINAL_SESSION).read_bytes()
    source_seal_before = (package.root / _ORIGINAL_SEAL).read_bytes()
    report = frozen.evaluate_frozen_v3_independent_validation(
        package.root,
        repository_root=v1_support._ROOT,
        evaluator_git_head_sha=v1_support._EVALUATOR_HEAD,
    )
    dataset = report.dataset

    assert len(dataset.cases) == 7
    assert len(report.cases) == 7
    assert report.candidate_identity_before == report.candidate_identity_after
    assert report.activation_allowed is False
    assert dataset.campaign_id == package.derived_session["campaign_id"]
    assert dataset.source_session_report_sha256 == _sha256(package.derived_session_payload)
    assert dataset.source_completion_seal_sha256 == _sha256(package.derived_seal_payload)
    assert _changed_keys(package.source_session, package.derived_session) == {"campaign_id"}
    assert _changed_keys(package.source_seal, package.derived_seal) == {
        "campaign_id",
        "source_session_report_sha256",
    }
    assert package.derived_seal["source_session_report_sha256"] == _sha256(
        package.derived_session_payload
    )

    manifest = _read_mapping(package.root / "campaign-manifest.json")
    assert manifest["source_session_report"] == {
        "path": _DERIVED_SESSION,
        "sha256": _sha256(package.derived_session_payload),
    }
    assert manifest["source_completion_seal"] == {
        "path": _DERIVED_SEAL,
        "sha256": _sha256(package.derived_seal_payload),
    }
    assert (package.root / _DERIVED_SESSION).read_bytes() == (package.derived_session_payload)
    assert (package.root / _DERIVED_SEAL).read_bytes() == package.derived_seal_payload

    assert source_session_before == package.source_session_payload
    assert source_seal_before == package.source_seal_payload
    assert (package.root / _ORIGINAL_SESSION).read_bytes() == source_session_before
    assert (package.root / _ORIGINAL_SEAL).read_bytes() == source_seal_before
    assert _evidence_bytes(package.root) == package.evidence_snapshot


def _rebind_session_reference(root: Path, digest: str) -> None:
    manifest = _read_mapping(root / "campaign-manifest.json")
    reference = manifest["source_session_report"]
    assert isinstance(reference, dict)
    reference["sha256"] = digest
    _write_document(root / "campaign-manifest.json", manifest)
    _rebind_outer_documents(root)


def _rebind_seal_reference(root: Path, digest: str) -> None:
    manifest = _read_mapping(root / "campaign-manifest.json")
    reference = manifest["source_completion_seal"]
    assert isinstance(reference, dict)
    reference["sha256"] = digest
    _write_document(root / "campaign-manifest.json", manifest)
    _rebind_outer_documents(root)


def _tamper_derived_session(root: Path) -> None:
    session = _read_mapping(root / _DERIVED_SESSION)
    session["operator"] = "tampered-operator"
    _, digest = _write_document(root / _DERIVED_SESSION, session)
    _rebind_session_reference(root, digest)


def _tamper_derived_seal(root: Path) -> None:
    seal = _read_mapping(root / _DERIVED_SEAL)
    seal["capture_count"] = 6
    _, digest = _write_document(root / _DERIVED_SEAL, seal)
    _rebind_seal_reference(root, digest)


def _tamper_derived_hash(root: Path) -> None:
    (root / f"{_DERIVED_SESSION}.sha256").write_text(
        f"{'0' * 64}  {_DERIVED_SESSION}\n",
        encoding="ascii",
        newline="\n",
    )


def _tamper_manifest_reference(root: Path) -> None:
    manifest = _read_mapping(root / "campaign-manifest.json")
    reference = manifest["source_session_report"]
    assert isinstance(reference, dict)
    reference["path"] = _ORIGINAL_SESSION
    reference["sha256"] = _sha256((root / _ORIGINAL_SESSION).read_bytes())
    _write_document(root / "campaign-manifest.json", manifest)
    _rebind_outer_documents(root)


@pytest.mark.parametrize(
    "tamper",
    [
        _tamper_derived_session,
        _tamper_derived_seal,
        _tamper_derived_hash,
        _tamper_manifest_reference,
    ],
    ids=["derived-session", "derived-seal", "derived-hash", "manifest-ref"],
)
def test_frozen_v1_parser_rejects_bridge_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper: Callable[[Path], None],
) -> None:
    package = _build_bridged_package(tmp_path, monkeypatch)
    original_session = (package.root / _ORIGINAL_SESSION).read_bytes()
    original_seal = (package.root / _ORIGINAL_SEAL).read_bytes()
    evidence = _evidence_bytes(package.root)

    tamper(package.root)

    with pytest.raises(frozen.InventoryPositiveV3IndependentValidationError):
        _load_with_frozen_v1(package)
    assert (package.root / _ORIGINAL_SESSION).read_bytes() == original_session
    assert (package.root / _ORIGINAL_SEAL).read_bytes() == original_seal
    assert _evidence_bytes(package.root) == evidence


def test_full_v2_synthetic_lifecycle_reaches_nonactivating_terminal_pass_and_proposal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = v1_support._ROOT.resolve(strict=True)
    registry_path = repository_root.joinpath(*protocol_v2._APPROVAL_REGISTRY_PATH.parts)
    registry_sidecar_path = repository_root.joinpath(
        *protocol_v2._APPROVAL_REGISTRY_SIDECAR_PATH.parts
    )
    registry_before = registry_path.read_bytes()
    registry_sidecar_before = registry_sidecar_path.read_bytes()

    with TemporaryDirectory(prefix="v2r-", dir=repository_root) as temporary:
        sandbox_root = Path(temporary)
        attempt_base = sandbox_root / "a"
        protocol, authorization, source = _install_full_lifecycle_seams(
            sandbox_root,
            attempt_base,
            monkeypatch,
        )
        development_case_ids, development_session_ids, development_capture_ids = (
            protocol_v2._frozen_development_identity_sets(protocol)
        )
        session_id = source.session["session_id"]
        assert isinstance(session_id, str)
        capture_ids = {str(item["capture_id"]) for item in source.capture_reports}
        source_case_ids = {f"{session_id}/{capture_id}" for capture_id in capture_ids}
        assert source_case_ids.isdisjoint(development_case_ids)
        assert session_id not in development_session_ids
        assert capture_ids.isdisjoint(development_capture_ids)
        capture_reservation = _read_mapping(
            source.paths.attempt_root / "capture-passive-campaign-reserved.json"
        )
        capture_terminal = _read_mapping(
            source.paths.attempt_root / "capture-passive-campaign-terminal.json"
        )
        assert capture_reservation["status"] == "reserved-irrevocably"
        assert capture_reservation["binding"] == {
            "capture_build_sha": protocol_v2.PROTOCOL_V1_SOURCE_HEAD,
            "capture_configuration_id": protocol_v2.CAPTURE_CONFIGURATION_ID,
            "legacy_live_authorization_git_commit_sha": authorization.git_commit_sha,
        }
        assert capture_terminal["status"] == "passed-terminal"
        assert capture_terminal["contract_id"] == "PASSIVE_CAPTURE_COMPLETE_UNREVIEWED"
        assert capture_terminal["output_sha256"] == _sha256(
            protocol_v2._canonical_bytes(source.producer_attestation)
        )

        terminal = _run_to_terminal_evaluation(
            repository_root,
            attempt_base,
            protocol,
            counts=_PASS_COUNTS,
        )

        manifest = _read_mapping(
            source.paths.reviewed_package_root / protocol_v2._CAMPAIGN_MANIFEST_NAME
        )
        cases = manifest["cases"]
        assert isinstance(cases, list)
        assert [item["planned_stage_id"] for item in cases] == list(protocol_v2.REQUIRED_STAGES)
        assert [item["sequence_index"] for item in cases] == list(range(1, 8))
        assert manifest["selection_policy"] == (
            "all-owned-captures-in-source-order-no-drop-no-replacement"
        )
        assert manifest["training_allowed"] is False
        assert manifest["prototype_eligible"] is False
        reviewed_validation_package = _read_mapping(
            source.paths.reviewed_package_root / protocol_v2._VALIDATION_PACKAGE_NAME
        )
        assert reviewed_validation_package["training_allowed"] is False
        assert reviewed_validation_package["prototype_eligible"] is False
        assert reviewed_validation_package["activation_allowed"] is False

        private_report = _read_mapping(terminal.root / "frozen-evaluator-private-report.json")
        report_cases = private_report["cases"]
        assert isinstance(report_cases, list)
        assert terminal.detector_conformance_passed is True
        assert terminal.approval_required is True
        assert terminal.terminal_status == "conformance-passed-source-approval-required"
        assert private_report["detector_conformance_passed"] is True
        assert private_report["validation_passed"] is False
        assert private_report["validation_status"] == "approval-required"
        assert private_report["approval"] is None
        assert private_report["activation_allowed"] is False
        assert private_report["promotion_allowed"] is False
        assert private_report["candidate_model"] == frozen.frozen_v3_model_binding().to_dict()
        assert (
            private_report["candidate_identity_before"]
            == private_report["candidate_identity_after"]
        )
        assert (
            private_report["analyzer_state_sha256_before"]
            == private_report["analyzer_state_sha256_after"]
        )
        assert private_report["contamination_firewall"] == {
            "candidate_identity_unchanged": True,
            "development_and_validation_dataset_paths_are_separate": True,
            "prototype_learning_allowed": False,
            "prototypes_added": 0,
            "training_allowed": False,
            "validation_case_export_to_model_allowed": False,
        }
        assert private_report["action_authority"] == {
            "banking_authority": False,
            "click_authority": False,
            "mining_authority": False,
            "reason": "validation_readiness_is_not_a_production_perception_snapshot",
            "target_ids": [],
        }
        assert any(item["byte_identical_to_development_payload"] for item in report_cases)
        assert {str(item["case_id"]) for item in report_cases}.isdisjoint(development_case_ids)

        terminal_record = _read_mapping(terminal.root / "protocol-v2-terminal-result.json")
        assert terminal_record["approval_required"] is True
        assert terminal_record["activation_allowed"] is False
        assert terminal_record["promotion_allowed"] is False
        sleep(0.002)
        proposal = protocol_v2.prepare_approval_request(
            repository_root,
            expected_head=protocol.evaluator_head_sha,
            proposed_approver="approver-c",
            proposed_approved_at_utc=protocol_v2._format_utc(datetime.now(UTC)),
            attempt_base=attempt_base,
        )
        request = _read_mapping(Path(str(proposal["path"])))
        proposed_approval = request["proposed_approval"]
        assert isinstance(proposed_approval, dict)
        approval_identity = {
            key: value for key, value in proposed_approval.items() if key != "approval_id"
        }
        assert proposed_approval["approval_id"] == (
            "inventory-positive-v3-approval-"
            + _sha256(protocol_v2._canonical_data_bytes(approval_identity))[:24]
        )
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
        assert authorization.authorization_id == terminal_record["authorization_id"]

    assert registry_path.read_bytes() == registry_before
    assert registry_sidecar_path.read_bytes() == registry_sidecar_before


def test_evaluator_rejects_coherent_truth_replacement_against_original_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = v1_support._ROOT.resolve(strict=True)
    with TemporaryDirectory(prefix="v2x-", dir=repository_root) as temporary:
        sandbox_root = Path(temporary)
        attempt_base = sandbox_root / "a"
        protocol, _, source = _install_full_lifecycle_seams(
            sandbox_root,
            attempt_base,
            monkeypatch,
        )
        _run_to_published_review(
            repository_root,
            attempt_base,
            protocol,
            counts=(0, 2, 5, 27, 28, None, None),
        )
        submission_before = _snapshot_regular_files(
            source.paths.review_intake_root / "submission"
        )
        _coherently_replace_reviewed_truth_with_c2_pass(source)
        _assert_rewritten_review_projection_rejected(
            repository_root,
            attempt_base,
            protocol,
            source,
            monkeypatch,
            submission_before=submission_before,
        )


def test_evaluator_rejects_coherent_reviewer_identity_rebinding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = v1_support._ROOT.resolve(strict=True)
    with TemporaryDirectory(prefix="v2i-", dir=repository_root) as temporary:
        sandbox_root = Path(temporary)
        attempt_base = sandbox_root / "a"
        protocol, _, source = _install_full_lifecycle_seams(
            sandbox_root,
            attempt_base,
            monkeypatch,
        )
        _run_to_published_review(
            repository_root,
            attempt_base,
            protocol,
            counts=_PASS_COUNTS,
        )
        submission_root = source.paths.review_intake_root / "submission"
        submission_before = _snapshot_regular_files(submission_root)
        submission = _read_mapping(submission_root / protocol_v2._REVIEW_SUBMISSION_NAME)
        assert submission["reviewer"] == "reviewer-b"

        _coherently_rebind_reviewed_reviewer(source, replacement="reviewer-c")
        reviewed_root = source.paths.reviewed_package_root
        review = _read_mapping(reviewed_root / protocol_v2._REVIEWER_TRUTH_NAME)
        reviewed_record = _read_mapping(
            reviewed_root / "protocol-v2-reviewed-package.json"
        )
        assert review["reviewer"] == reviewed_record["reviewer"] == "reviewer-c"
        rebound_snapshot = protocol_v2._read_verified_tree(
            reviewed_root,
            protocol_v2._reviewed_package_roles(),
        )
        rebound_snapshot.recheck()
        publish_terminal = _read_mapping(
            source.paths.attempt_root / "publish-reviewed-package-terminal.json"
        )
        assert publish_terminal["output_sha256"] == _sha256(
            (reviewed_root / protocol_v2._PACKAGE_TREE_NAME).read_bytes()
        )

        _assert_rewritten_review_projection_rejected(
            repository_root,
            attempt_base,
            protocol,
            source,
            monkeypatch,
            submission_before=submission_before,
        )


def test_full_v2_synthetic_conformance_failure_is_terminal_and_cannot_propose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = v1_support._ROOT.resolve(strict=True)
    registry_path = repository_root.joinpath(*protocol_v2._APPROVAL_REGISTRY_PATH.parts)
    registry_sidecar_path = repository_root.joinpath(
        *protocol_v2._APPROVAL_REGISTRY_SIDECAR_PATH.parts
    )
    registry_before = registry_path.read_bytes()
    registry_sidecar_before = registry_sidecar_path.read_bytes()

    with TemporaryDirectory(prefix="v2r-", dir=repository_root) as temporary:
        sandbox_root = Path(temporary)
        attempt_base = sandbox_root / "a"
        protocol, _, source = _install_full_lifecycle_seams(
            sandbox_root,
            attempt_base,
            monkeypatch,
        )
        terminal = _run_to_terminal_evaluation(
            repository_root,
            attempt_base,
            protocol,
            counts=(0, 2, 5, 27, 28, None, None),
        )

        assert terminal.detector_conformance_passed is False
        assert terminal.approval_required is False
        assert terminal.terminal_status == "conformance-failed-permanent"
        result = _read_mapping(terminal.root / "protocol-v2-terminal-result.json")
        assert result["detector_conformance_passed"] is False
        assert result["approval_required"] is False
        assert result["contract_id"] == "C2_EARLY_PARTIAL_CONFORMANCE_FAILURE"
        assert result["retry_allowed"] is False
        assert result["activation_allowed"] is False
        assert result["promotion_allowed"] is False
        private_report = _read_mapping(terminal.root / "frozen-evaluator-private-report.json")
        report_cases = private_report["cases"]
        assert isinstance(report_cases, list)
        failures = [item for item in report_cases if item["passed"] is not True]
        assert [item["planned_stage_id"] for item in failures] == ["early-partial"]
        assert all(
            item["passed"] is True
            for item in report_cases
            if item["planned_stage_id"] != "early-partial"
        )
        public_failure = _read_mapping(terminal.root / "public-failure-receipt.json")
        assert public_failure["contract_id"] == "C2_EARLY_PARTIAL_CONFORMANCE_FAILURE"
        assert public_failure["terminal_status"] == "failed-permanent"
        evaluator_terminal = _read_mapping(
            source.paths.attempt_root / "evaluate-locked-candidate-terminal.json"
        )
        assert evaluator_terminal["status"] == "failed-terminal"
        assert evaluator_terminal["contract_id"] == "C2_EARLY_PARTIAL_CONFORMANCE_FAILURE"
        assert evaluator_terminal["retry_allowed"] is False

        with pytest.raises(protocol_v2.InventoryV3ProtocolV2Error):
            protocol_v2.prepare_approval_request(
                repository_root,
                expected_head=protocol.evaluator_head_sha,
                proposed_approver="approver-c",
                proposed_approved_at_utc=protocol_v2._format_utc(datetime.now(UTC)),
                attempt_base=attempt_base,
            )
        assert not source.paths.approval_request_root.exists()

    assert registry_path.read_bytes() == registry_before
    assert registry_sidecar_path.read_bytes() == registry_sidecar_before


def _snapshot_regular_files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _snapshot_evaluator_ledger(source: protocol_v2.SourceMetadataBinding) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in sorted(source.paths.attempt_root.iterdir())
        if path.is_file() and path.name.startswith("evaluate-locked-candidate-")
    }


def test_terminal_pass_replay_never_reinvokes_evaluator_or_changes_first_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = v1_support._ROOT.resolve(strict=True)
    with TemporaryDirectory(prefix="v2p-", dir=repository_root) as temporary:
        sandbox_root = Path(temporary)
        attempt_base = sandbox_root / "a"
        protocol, _, source = _install_full_lifecycle_seams(
            sandbox_root,
            attempt_base,
            monkeypatch,
        )
        original_evaluator = frozen.evaluate_frozen_v3_independent_validation
        evaluator_calls = 0

        def counted_evaluator(*args: object, **kwargs: object) -> object:
            nonlocal evaluator_calls
            evaluator_calls += 1
            return original_evaluator(*args, **kwargs)

        monkeypatch.setattr(
            frozen,
            "evaluate_frozen_v3_independent_validation",
            counted_evaluator,
        )
        terminal = _run_to_terminal_evaluation(
            repository_root,
            attempt_base,
            protocol,
            counts=_PASS_COUNTS,
        )

        assert terminal.detector_conformance_passed is True
        assert evaluator_calls == 1
        result_before = _snapshot_regular_files(source.paths.result_root)
        ledger_before = _snapshot_evaluator_ledger(source)
        assert terminal.result_tree_sha256 == _sha256(
            result_before[protocol_v2._PACKAGE_TREE_NAME]
        )
        assert set(ledger_before) == {
            "evaluate-locked-candidate-reserved.json",
            "evaluate-locked-candidate-reserved.json.sha256",
            "evaluate-locked-candidate-terminal.json",
            "evaluate-locked-candidate-terminal.json.sha256",
        }

        with pytest.raises(protocol_v2.InventoryV3ProtocolV2Error):
            protocol_v2.evaluate_locked_protocol_v2(
                repository_root,
                expected_head=protocol.evaluator_head_sha,
                attempt_base=attempt_base,
            )

        assert evaluator_calls == 1
        assert _snapshot_regular_files(source.paths.result_root) == result_before
        assert _snapshot_evaluator_ledger(source) == ledger_before


def test_evaluator_exception_is_permanent_and_replay_preserves_failure_receipts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = v1_support._ROOT.resolve(strict=True)
    with TemporaryDirectory(prefix="v2e-", dir=repository_root) as temporary:
        sandbox_root = Path(temporary)
        attempt_base = sandbox_root / "a"
        protocol, _, source = _install_full_lifecycle_seams(
            sandbox_root,
            attempt_base,
            monkeypatch,
        )
        evaluator_calls = 0

        def exploding_evaluator(*args: object, **kwargs: object) -> object:
            nonlocal evaluator_calls
            del args, kwargs
            evaluator_calls += 1
            raise RuntimeError("synthetic frozen evaluator exception")

        monkeypatch.setattr(
            frozen,
            "evaluate_frozen_v3_independent_validation",
            exploding_evaluator,
        )

        with pytest.raises(RuntimeError, match="synthetic frozen evaluator exception"):
            _run_to_terminal_evaluation(
                repository_root,
                attempt_base,
                protocol,
                counts=_PASS_COUNTS,
            )

        assert evaluator_calls == 1
        failure_root = (
            source.paths.attempt_root / "evaluate-locked-candidate-failure"
        )
        private_failure = _read_mapping(failure_root / "private-failure.json")
        public_failure = _read_mapping(failure_root / "public-failure-receipt.json")
        evaluator_terminal = _read_mapping(
            source.paths.attempt_root / "evaluate-locked-candidate-terminal.json"
        )
        assert private_failure["contract_id"] == "ATTEMPT_INTEGRITY_FAILURE"
        assert private_failure["error_type"] == "RuntimeError"
        assert private_failure["terminal_status"] == "failed-terminal-permanent"
        assert private_failure["retry_allowed"] is False
        assert private_failure["activation_allowed"] is False
        assert private_failure["promotion_allowed"] is False
        assert public_failure["contract_id"] == "ATTEMPT_INTEGRITY_FAILURE"
        assert public_failure["terminal_status"] == "failed-permanent"
        assert public_failure["retry_allowed"] is False
        assert public_failure["activation_allowed"] is False
        assert public_failure["promotion_allowed"] is False
        assert evaluator_terminal["status"] == "failed-terminal"
        assert evaluator_terminal["contract_id"] == "ATTEMPT_INTEGRITY_FAILURE"
        assert evaluator_terminal["retry_allowed"] is False
        assert evaluator_terminal["output_sha256"] == _sha256(
            (failure_root / protocol_v2._PACKAGE_TREE_NAME).read_bytes()
        )
        assert not (
            source.paths.result_root / "protocol-v2-terminal-result.json"
        ).exists()
        assert not (
            source.paths.result_root / "frozen-evaluator-private-report.json"
        ).exists()
        assert not source.paths.approval_request_root.exists()

        result_before = _snapshot_regular_files(source.paths.result_root)
        failure_before = _snapshot_regular_files(failure_root)
        ledger_before = _snapshot_evaluator_ledger(source)
        with pytest.raises(protocol_v2.InventoryV3ProtocolV2Error):
            protocol_v2.evaluate_locked_protocol_v2(
                repository_root,
                expected_head=protocol.evaluator_head_sha,
                attempt_base=attempt_base,
            )

        assert evaluator_calls == 1
        assert _snapshot_regular_files(source.paths.result_root) == result_before
        assert _snapshot_regular_files(failure_root) == failure_before
        assert _snapshot_evaluator_ledger(source) == ledger_before
        assert not source.paths.approval_request_root.exists()


def test_post_pass_result_tree_rebind_cannot_produce_approval_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = v1_support._ROOT.resolve(strict=True)
    registry_path = repository_root.joinpath(*protocol_v2._APPROVAL_REGISTRY_PATH.parts)
    registry_sidecar_path = repository_root.joinpath(
        *protocol_v2._APPROVAL_REGISTRY_SIDECAR_PATH.parts
    )
    registry_before = registry_path.read_bytes()
    registry_sidecar_before = registry_sidecar_path.read_bytes()

    with TemporaryDirectory(prefix="v2b-", dir=repository_root) as temporary:
        sandbox_root = Path(temporary)
        attempt_base = sandbox_root / "a"
        protocol, _, source = _install_full_lifecycle_seams(
            sandbox_root,
            attempt_base,
            monkeypatch,
        )
        terminal = _run_to_terminal_evaluation(
            repository_root,
            attempt_base,
            protocol,
            counts=_PASS_COUNTS,
        )
        evaluator_terminal_path = (
            source.paths.attempt_root / "evaluate-locked-candidate-terminal.json"
        )
        evaluator_terminal_before = evaluator_terminal_path.read_bytes()
        evaluator_ledger_before = _snapshot_evaluator_ledger(source)
        evaluator_terminal = json.loads(evaluator_terminal_before)
        assert evaluator_terminal["output_sha256"] == terminal.result_tree_sha256

        result_root = source.paths.result_root
        result_path = result_root / "protocol-v2-terminal-result.json"
        result = _read_mapping(result_path)
        result["frozen_evaluator_report_sha256"] = "0" * 64
        _write_document(result_path, result)
        rebound_paths = {
            "protocol-v2-terminal-result.json",
            "protocol-v2-terminal-result.json.sha256",
        }
        tree_path = result_root / protocol_v2._PACKAGE_TREE_NAME
        tree = _read_mapping(tree_path)
        entries = tree["entries"]
        assert isinstance(entries, list)
        rebound: set[str] = set()
        for raw in entries:
            assert isinstance(raw, dict)
            relative = raw.get("path")
            if not isinstance(relative, str) or relative not in rebound_paths:
                continue
            payload = result_root.joinpath(*relative.split("/")).read_bytes()
            raw["sha256"] = _sha256(payload)
            raw["size_bytes"] = len(payload)
            rebound.add(relative)
        assert rebound == rebound_paths
        _, rebound_tree_sha = _write_document(tree_path, tree)
        assert rebound_tree_sha != terminal.result_tree_sha256
        rebound_snapshot = protocol_v2._read_verified_tree(
            result_root,
            protocol_v2._result_roles(conformance_passed=True),
        )
        rebound_snapshot.recheck()

        sleep(0.002)
        with pytest.raises(
            protocol_v2.InventoryV3ProtocolV2Error,
            match="evaluate-locked-candidate successful lineage record differs",
        ):
            protocol_v2.prepare_approval_request(
                repository_root,
                expected_head=protocol.evaluator_head_sha,
                proposed_approver="approver-c",
                proposed_approved_at_utc=protocol_v2._format_utc(datetime.now(UTC)),
                attempt_base=attempt_base,
            )

        assert evaluator_terminal_path.read_bytes() == evaluator_terminal_before
        assert _snapshot_evaluator_ledger(source) == evaluator_ledger_before
        assert not source.paths.approval_request_root.exists()
        assert not (
            source.paths.attempt_root / "prepare-approval-request-reserved.json"
        ).exists()

    assert registry_path.read_bytes() == registry_before
    assert registry_sidecar_path.read_bytes() == registry_sidecar_before
