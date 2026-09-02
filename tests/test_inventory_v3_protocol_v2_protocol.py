from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Callable, Iterator, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from shutil import copytree
from tempfile import TemporaryDirectory
from time import sleep
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from mining_automation.capture.windows import (
    CapturedPixels,
    WindowInfo,
    WindowsCaptureBackend,
)
from mining_automation.capture.windows.testing import FakeWin32Api
from mining_automation.validation import inventory_v3_capture as legacy_capture
from validation.inventory_v3_protocol_v2 import privacy, producer
from validation.inventory_v3_protocol_v2 import protocol as v2

_AUTHORIZATION_ID = "2" * 64
_OPAQUE_RECEIPT_ID = "123e4567-e89b-42d3-a456-426614174000"
_EXECUTION_HEAD = "c" * 40
_PROTOCOL_SOURCE_HEAD = "a" * 40
_PROTOCOL_LOCK_HEAD = "b" * 40
_PROTOCOL_LOCK_SHA256 = "d" * 64
_AUTHORIZATION_HEAD = "e" * 40
_LEGACY_AUTHORIZATION_BLOB = "f" * 40
_V2_AUTHORIZATION_BLOB = "1" * 40
_WINDOW_HANDLE = 3107
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_canonical(
    path: Path,
    value: Mapping[str, object],
    *,
    sidecar: bool = True,
) -> bytes:
    payload = _canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    if sidecar:
        path.with_suffix(path.suffix + ".sha256").write_bytes(
            f"{_sha256(payload)}  {path.name}\n".encode("ascii")
        )
    return payload


def _coherently_rebind_json_member(
    root: Path,
    relative: str,
    value: Mapping[str, object],
) -> str:
    member_path = root.joinpath(*relative.split("/"))
    payload = _write_canonical(member_path, value)
    sidecar_path = member_path.with_suffix(member_path.suffix + ".sha256")
    replacements = {
        relative: payload,
        f"{relative}.sha256": sidecar_path.read_bytes(),
    }
    tree_path = root / v2._PACKAGE_TREE_NAME
    tree = json.loads(tree_path.read_bytes())
    entries = tree.get("entries")
    assert isinstance(entries, list)
    rebound: set[str] = set()
    for raw in entries:
        assert isinstance(raw, dict)
        path = raw.get("path")
        if isinstance(path, str) and path in replacements:
            replacement = replacements[path]
            raw["sha256"] = _sha256(replacement)
            raw["size_bytes"] = len(replacement)
            rebound.add(path)
    assert rebound == set(replacements)
    tree_payload = _write_canonical(tree_path, tree)
    return _sha256(tree_payload)


def _materialize_frozen_development_metadata(root: Path) -> None:
    for relative in v2._DEVELOPMENT_METADATA_PATHS:
        payload = v2._git_bytes(_REPOSITORY_ROOT, "show", f"HEAD:{relative}")
        destination = root.joinpath(*relative.split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)


def _protocol(root: Path) -> v2.ProtocolV2LockBinding:
    return v2.ProtocolV2LockBinding(
        repository_root=root.resolve(strict=True),
        evaluator_head_sha=_EXECUTION_HEAD,
        source_commit_sha=_PROTOCOL_SOURCE_HEAD,
        lock_commit_sha=_PROTOCOL_LOCK_HEAD,
        lock_sha256=_PROTOCOL_LOCK_SHA256,
        locked_git_blobs=(),
    )


def _authorization() -> v2.LiveAuthorizationBinding:
    return v2.LiveAuthorizationBinding(
        authorization_id=_AUTHORIZATION_ID,
        git_commit_sha=_AUTHORIZATION_HEAD,
        legacy_registry_git_blob=_LEGACY_AUTHORIZATION_BLOB,
        protocol_v2_registry_git_blob=_V2_AUTHORIZATION_BLOB,
        committed_at_utc="2024-12-31T23:59:45.000000Z",
        opaque_receipt_id=_OPAQUE_RECEIPT_ID,
    )


def _source_binding(
    root: Path,
    attempt_base: Path,
    *,
    operator: str = "operator-a",
) -> v2.SourceMetadataBinding:
    protocol = _protocol(root)
    authorization = _authorization()
    paths = v2.ProtocolV2Paths(
        repository_root=root.resolve(strict=True),
        authorization_id=authorization.authorization_id,
        source_campaign_root=root / "source",
        workspace_root=root / "workspace",
        acquisition_root=root / "acquisition",
        review_intake_root=root / "review-intake",
        reviewed_package_root=root / "reviewed-package",
        approval_request_root=root / "approval-request",
        result_root=root / "result",
        attempt_root=attempt_base / "attempts",
    )
    return v2.SourceMetadataBinding(
        paths=paths,
        protocol=protocol,
        authorization=authorization,
        session={
            "campaign_id": "campaign-a",
            "operator": operator,
            "session_id": f"inventory-v3-independent-{_AUTHORIZATION_ID}",
        },
        session_payload=b"private source session\n",
        completion_seal={},
        completion_payload=b"private completion seal\n",
        producer_attestation={},
        capture_reports=(),
        owned_frame_reports=(),
        source_files=(),
        source_metadata_snapshot=(),
    )


def _timestamps() -> Iterator[str]:
    for second in range(60):
        yield f"2025-01-01T00:00:{second:02d}.000000Z"


def _legacy_binding() -> legacy_capture._ProtocolBinding:
    return legacy_capture._ProtocolBinding(
        execution_head_sha=_EXECUTION_HEAD,
        execution_head_committed_at_utc="2024-12-31T23:59:30Z",
        lock_commit_sha=v2.PROTOCOL_V1_LOCK_HEAD,
        lock_committed_at_utc="2024-12-31T23:59:00Z",
        lock_sha256=v2.PROTOCOL_V1_LOCK_SHA256,
        capture_build_sha=v2.PROTOCOL_V1_SOURCE_HEAD,
        capture_configuration_id=v2.CAPTURE_CONFIGURATION_ID,
    )


def _legacy_authorization() -> legacy_capture._LiveAuthorizationBinding:
    return legacy_capture._LiveAuthorizationBinding(
        authorization_id=_AUTHORIZATION_ID,
        git_commit_sha=_AUTHORIZATION_HEAD,
        git_committed_at_utc="2024-12-31T23:59:45Z",
        git_blob=_LEGACY_AUTHORIZATION_BLOB,
    )


def _unique_backend_factory() -> Callable[[], WindowsCaptureBackend]:
    sequence = iter(range(1, len(v2.REQUIRED_STAGES) + 1))

    def factory() -> WindowsCaptureBackend:
        marker = next(sequence)
        payload = bytes((marker,)) * v2.FULL_FRAME_SIZE
        api = FakeWin32Api(
            windows=[
                WindowInfo(
                    hwnd=_WINDOW_HANDLE,
                    title="RuneLite - synthetic private title",
                    class_name="SunAwtFrame",
                    is_visible=True,
                    is_minimized=False,
                    client_width=v2.SUPPORTED_FRAME_WIDTH,
                    client_height=v2.SUPPORTED_FRAME_HEIGHT,
                )
            ],
            captures={
                _WINDOW_HANDLE: CapturedPixels(
                    payload=payload,
                    width=v2.SUPPORTED_FRAME_WIDTH,
                    height=v2.SUPPORTED_FRAME_HEIGHT,
                )
            },
            dpi_by_hwnd={_WINDOW_HANDLE: 144},
        )
        return WindowsCaptureBackend(win32_api=api)

    return factory


def _build_synthetic_source_campaign(
    root: Path,
    attempt_base: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[v2.ProtocolV2LockBinding, v2.LiveAuthorizationBinding]:
    _materialize_frozen_development_metadata(root)
    protocol = _protocol(root)
    authorization = _authorization()
    output_root = root.joinpath(*v2._SOURCE_OUTPUT_ROOT.parts)
    reservation_root = (
        attempt_base / "Mining-Automation" / "inventory-positive-v3-independent-reservations"
    )
    times = _timestamps()
    legacy_protocol = _legacy_binding()
    legacy_authorization = _legacy_authorization()
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
        _unique_backend_factory(),
    )
    monkeypatch.setattr(
        legacy_capture,
        "_approved_output_root",
        lambda _root: output_root,
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
    monkeypatch.setattr(legacy_capture, "_utc_timestamp", lambda: next(times))
    monkeypatch.setattr(
        legacy_capture.platform,
        "platform",
        lambda: "Windows-synthetic-test",
    )
    result = legacy_capture.run_passive_inventory_v3_capture_campaign(
        inputs=legacy_capture.PassiveInventoryV3CaptureInputs(
            operator="operator-a",
            runelite_build="operator-asserted-build",
            client_mode="fixed",
            theme="dark",
            renderer="gpu",
        ),
        repository_root=root,
    )
    session = json.loads(result.source_session_report_path.read_bytes())
    environment = session["capture_environment"]
    assert isinstance(environment, dict)
    attestation = producer.build_producer_provenance(
        collected_at_utc="2025-01-01T00:00:30.000000Z",
        protocol_lock_sha256=protocol.lock_sha256,
        live_authorization_id=authorization.authorization_id,
        opaque_receipt_id=authorization.opaque_receipt_id,
        capture_execution_head_sha=_EXECUTION_HEAD,
        session_id=result.session_id,
        source_session_report_sha256=result.source_session_report_sha256,
        source_completion_seal_sha256=result.source_completion_seal_sha256,
        legacy_user_reservation_sha256=result.host_reservation_sha256,
        observed_identity=producer.WindowsProducerIdentity(
            computer_name="SYNTHETIC-HOST",
            user_name="synthetic-user",
            session_id=7,
        ),
        observed_environment={
            "frame.height": v2.SUPPORTED_FRAME_HEIGHT,
            "frame.pixel_format": v2.SUPPORTED_PIXEL_FORMAT,
            "frame.profile_id": v2.SUPPORTED_PROFILE_ID,
            "frame.width": v2.SUPPORTED_FRAME_WIDTH,
            "window_class": environment["window_class"],
            "window_handle": environment["window_handle"],
            "windows_dpi": environment["windows_dpi"],
            "windows_scaling_percent": environment["windows_scaling_percent"],
            "windows_version": environment["windows_version"],
        },
        operator_asserted_environment={
            "client_mode": environment["client_mode"],
            "renderer": environment["renderer"],
            "runelite_build": environment["runelite_build"],
            "theme": environment["theme"],
        },
    )
    _write_canonical(
        result.campaign_directory / v2._PRODUCER_ATTESTATION_NAME,
        attestation.to_dict(),
    )
    return protocol, authorization


def _prepare_copy_integrity_campaign(
    root: Path,
    attempt_base: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[
    v2.ProtocolV2LockBinding,
    v2.LiveAuthorizationBinding,
    v2.SourceMetadataBinding,
]:
    protocol, authorization = _build_synthetic_source_campaign(
        root,
        attempt_base,
        monkeypatch,
    )
    monkeypatch.setattr(
        "validation.inventory_v3_protocol_v2.protocol.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(
        v2,
        "_verify_approval_registry_absent",
        lambda _protocol, *, access_hook=None: b"approval absent\n",
    )
    monkeypatch.setattr(
        v2,
        "_git",
        lambda _root, *args, **kwargs: "2024-12-31T23:59:30+00:00",
    )
    source = v2.preflight_source_metadata(
        protocol,
        authorization,
        attempt_base=attempt_base,
    )
    monkeypatch.setattr(
        v2,
        "verify_protocol_v2_repository",
        lambda _root, *, expected_head: protocol,
    )
    monkeypatch.setattr(
        v2,
        "verify_live_authorization",
        lambda _protocol, *, access_hook=None: authorization,
    )
    capture_operation = "capture-passive-campaign"
    v2._reserve_attempt(
        source.paths,
        protocol,
        capture_operation,
        {
            "capture_build_sha": v2.PROTOCOL_V1_SOURCE_HEAD,
            "capture_configuration_id": v2.CAPTURE_CONFIGURATION_ID,
            "legacy_live_authorization_git_commit_sha": authorization.git_commit_sha,
        },
    )
    v2._record_attempt_terminal(
        source.paths,
        protocol,
        capture_operation,
        status="passed-terminal",
        contract_id="PASSIVE_CAPTURE_COMPLETE_UNREVIEWED",
        output_sha256=_sha256(_canonical_bytes(source.producer_attestation)),
    )
    return protocol, authorization, source


def test_authorization_proposal_is_deterministic_nonwriting_source_action(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    temporary = TemporaryDirectory(prefix="v2-auth-")
    request.addfinalizer(temporary.cleanup)
    temporary_root = Path(temporary.name)
    root = temporary_root / "r"
    root.mkdir()
    attempt_base = temporary_root / "a"
    protocol = replace(
        _protocol(root),
        evaluator_head_sha=_PROTOCOL_LOCK_HEAD,
    )
    legacy_registry = {
        "activation_allowed": False,
        "authorizations": [],
        "schema": v2.LIVE_AUTHORIZATION_SCHEMA,
    }
    v2_registry = {
        "activation_allowed": False,
        "authorizations": [],
        "schema": v2._V2_LIVE_AUTHORIZATION_SCHEMA,
    }
    approval_registry = {
        "activation_allowed": False,
        "entries": [],
        "promotion_allowed": False,
        "schema": "inventory-positive-v3-independent-validation-approval-registry-v1",
    }
    _write_canonical(
        root.joinpath(*v2._LIVE_AUTHORIZATION_PATH.parts),
        legacy_registry,
        sidecar=False,
    )
    _write_canonical(
        root.joinpath(*v2._V2_LIVE_AUTHORIZATION_PATH.parts),
        v2_registry,
    )
    approval_payload = _write_canonical(
        root.joinpath(*v2._APPROVAL_REGISTRY_PATH.parts),
        approval_registry,
    )
    monkeypatch.setattr(
        v2,
        "verify_protocol_v2_repository",
        lambda _root, *, expected_head: protocol,
    )
    monkeypatch.setattr(
        producer,
        "observe_windows_identity",
        lambda: producer.WindowsProducerIdentity("HOST", "user", 1),
    )
    monkeypatch.setattr(
        v2,
        "_verify_approval_registry_absent",
        lambda _protocol: approval_payload,
    )
    before = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }

    first = v2.build_live_authorization_proposal(
        root,
        expected_lock_head=protocol.lock_commit_sha,
        opaque_receipt_id=_OPAQUE_RECEIPT_ID,
        attempt_base=attempt_base,
    )
    second = v2.build_live_authorization_proposal(
        root,
        expected_lock_head=protocol.lock_commit_sha,
        opaque_receipt_id=_OPAQUE_RECEIPT_ID,
        attempt_base=attempt_base,
    )

    after = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    assert first == second
    assert after == before
    assert not attempt_base.exists()
    assert first["activation_allowed"] is False
    assert first["promotion_allowed"] is False
    assert first["source_registry_modified"] is False
    assert first["status"] == "proposal-only-not-authorized"
    files = first["files"]
    assert isinstance(files, list)
    assert [item["path"] for item in files] == [
        v2._LIVE_AUTHORIZATION_PATH.as_posix(),
        v2._V2_LIVE_AUTHORIZATION_PATH.as_posix(),
        v2._V2_LIVE_AUTHORIZATION_SIDECAR_PATH.as_posix(),
    ]
    for item in files[:2]:
        assert isinstance(item, dict)
        assert item["sha256"] == _sha256(_canonical_bytes(item["content"]))
    sidecar = files[2]
    assert isinstance(sidecar, dict)
    assert sidecar["sha256"] == _sha256(str(sidecar["content_ascii"]).encode("ascii"))


@pytest.mark.parametrize(
    "registry_update",
    (
        {"activation_allowed": True},
        {"promotion_allowed": True},
        {"unexpected": False},
    ),
)
def test_authorization_proposal_rejects_nonabsent_approval_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry_update: Mapping[str, object],
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    protocol = replace(_protocol(root), evaluator_head_sha=_PROTOCOL_LOCK_HEAD)
    _write_canonical(
        root.joinpath(*v2._LIVE_AUTHORIZATION_PATH.parts),
        {
            "activation_allowed": False,
            "authorizations": [],
            "schema": v2.LIVE_AUTHORIZATION_SCHEMA,
        },
        sidecar=False,
    )
    _write_canonical(
        root.joinpath(*v2._V2_LIVE_AUTHORIZATION_PATH.parts),
        {
            "activation_allowed": False,
            "authorizations": [],
            "schema": v2._V2_LIVE_AUTHORIZATION_SCHEMA,
        },
    )
    approval_registry: dict[str, object] = {
        "activation_allowed": False,
        "entries": [],
        "promotion_allowed": False,
        "schema": "inventory-positive-v3-independent-validation-approval-registry-v1",
    }
    approval_registry.update(registry_update)
    _write_canonical(
        root.joinpath(*v2._APPROVAL_REGISTRY_PATH.parts),
        approval_registry,
    )
    monkeypatch.setattr(
        v2,
        "verify_protocol_v2_repository",
        lambda _root, *, expected_head: protocol,
    )

    with pytest.raises(v2.InventoryV3ProtocolV2Error):
        v2.build_live_authorization_proposal(
            root,
            expected_lock_head=protocol.lock_commit_sha,
            opaque_receipt_id=_OPAQUE_RECEIPT_ID,
            attempt_base=tmp_path / "attempt-base",
        )


def test_valid_source_metadata_preflight_opens_no_pixels_then_fixed_order_is_finalized(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    temporary = TemporaryDirectory(prefix="v2-")
    request.addfinalizer(temporary.cleanup)
    temporary_root = Path(temporary.name)
    root = temporary_root / "r"
    root.mkdir()
    attempt_base = temporary_root / "a"
    protocol, authorization = _build_synthetic_source_campaign(
        root,
        attempt_base,
        monkeypatch,
    )
    events: list[tuple[str, str]] = []
    pixel_reads: list[Path] = []
    original_read_bytes = Path.read_bytes

    def audited_read_bytes(path: Path) -> bytes:
        if path.suffix == ".bgra":
            pixel_reads.append(path)
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", audited_read_bytes)
    monkeypatch.setattr(
        "validation.inventory_v3_protocol_v2.protocol.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(
        v2,
        "_verify_approval_registry_absent",
        lambda _protocol, *, access_hook=None: b"approval absent\n",
    )
    monkeypatch.setattr(
        v2,
        "_git",
        lambda _root, *args, **kwargs: "2024-12-31T23:59:30+00:00",
    )

    source = v2.preflight_source_metadata(
        protocol,
        authorization,
        attempt_base=attempt_base,
        access_hook=lambda phase, kind, _path: events.append((phase, kind)),
    )

    assert len(source.capture_reports) == 7
    assert pixel_reads == []
    assert ("preflight", "development_identity_disjointness_metadata") in events
    assert events[-1] == ("preflight", "source_preflight_complete")
    assert all(phase not in {"sensitive", "review"} for phase, _kind in events)

    monkeypatch.setattr(
        v2,
        "verify_protocol_v2_repository",
        lambda _root, *, expected_head: protocol,
    )
    monkeypatch.setattr(
        v2,
        "verify_live_authorization",
        lambda _protocol, *, access_hook=None: authorization,
    )
    monkeypatch.setattr(
        v2,
        "_verify_successful_operation",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(v2, "_reserve_attempt", lambda *args, **kwargs: "reserved")
    monkeypatch.setattr(v2, "_record_attempt_terminal", lambda *args, **kwargs: None)
    events.clear()
    acquisition = v2.finalize_acquisition(
        root,
        expected_head=protocol.evaluator_head_sha,
        attempt_base=attempt_base,
        access_hook=lambda phase, kind, _path: events.append((phase, kind)),
    )
    manifest = json.loads((acquisition.root / v2._CAMPAIGN_MANIFEST_NAME).read_bytes())
    assert manifest["campaign_id"] == v2._content_bound_campaign_id(manifest["session_id"])
    assert [item["sequence_index"] for item in manifest["cases"]] == list(range(1, 8))
    assert [item["planned_stage_id"] for item in manifest["cases"]] == list(v2.REQUIRED_STAGES)
    assert [item["operator_stage_label"] for item in manifest["cases"]] == list(v2.REQUIRED_STAGES)
    assert manifest["selection_policy"] == (
        "all-owned-captures-in-source-order-no-drop-no-replacement"
    )
    assert events.index(("preflight", "source_preflight_complete")) < events.index(
        ("sensitive", "validation_pixels_opened")
    )

    monkeypatch.setattr(
        v2,
        "_verify_successful_operation",
        lambda *args, **kwargs: None,
    )
    intake = v2.prepare_reviewer_intake(
        root,
        expected_head=protocol.evaluator_head_sha,
        attempt_base=attempt_base,
    )
    template = json.loads((intake.root / "package" / v2._REVIEW_TEMPLATE_NAME).read_bytes())
    assert template["operator_labels_available"] is False
    assert template["reviewer_truth_prefilled"] is False
    assert template["truth_source_required"] == "independent-human-review"
    assert len(template["cases"]) == len(v2.REQUIRED_STAGES)
    forbidden = {
        "capture_id",
        "case_id",
        "operator",
        "operator_stage_label",
        "planned_stage_id",
        "session_id",
    }
    assert not v2._mapping_contains_any_key(template, forbidden)
    for item in template["cases"]:
        assert set(item) == {
            "frame_region",
            "full_frame",
            "review_case_id",
            "truth",
        }
        assert all(value is None for value in item["truth"].values())


def test_capture_progress_must_match_exact_frozen_success_before_pixels(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    temporary = TemporaryDirectory(prefix="v2-progress-exact-")
    request.addfinalizer(temporary.cleanup)
    temporary_root = Path(temporary.name)
    root = temporary_root / "r"
    root.mkdir()
    attempt_base = temporary_root / "a"
    protocol, authorization = _build_synthetic_source_campaign(
        root,
        attempt_base,
        monkeypatch,
    )
    source_root = root.joinpath(*v2._SOURCE_OUTPUT_ROOT.parts) / _AUTHORIZATION_ID
    progress_path = source_root / v2._CAPTURE_PROGRESS_NAME
    progress = json.loads(progress_path.read_bytes())
    assert len(progress) == 23
    progress["detector_executed"] = True
    _write_canonical(progress_path, progress, sidecar=False)
    monkeypatch.setattr(
        "validation.inventory_v3_protocol_v2.protocol.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(
        v2,
        "_verify_approval_registry_absent",
        lambda _protocol, *, access_hook=None: b"approval absent\n",
    )
    monkeypatch.setattr(
        v2,
        "_git",
        lambda _root, *args, **kwargs: "2024-12-31T23:59:30+00:00",
    )
    pixel_reads: list[Path] = []
    events: list[tuple[str, str]] = []
    original_read_bytes = Path.read_bytes

    def audited_read_bytes(path: Path) -> bytes:
        if path.suffix == ".bgra":
            pixel_reads.append(path)
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", audited_read_bytes)
    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="source progress does not bind completion",
    ):
        v2.preflight_source_metadata(
            protocol,
            authorization,
            attempt_base=attempt_base,
            access_hook=lambda phase, kind, _path: events.append((phase, kind)),
        )

    assert pixel_reads == []
    assert all(phase not in {"sensitive", "review"} for phase, _kind in events)


@pytest.mark.parametrize("mutation", ("missing", "duplicated", "reordered"))
def test_source_case_record_mutation_rejects_before_sensitive_reads_or_advancement(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    mutation: str,
) -> None:
    temporary = TemporaryDirectory(prefix="v2-source-case-attack-")
    request.addfinalizer(temporary.cleanup)
    temporary_root = Path(temporary.name)
    root = temporary_root / "r"
    root.mkdir()
    attempt_base = temporary_root / "a"
    protocol, authorization, source = _prepare_copy_integrity_campaign(
        root,
        attempt_base,
        monkeypatch,
    )
    session_path = source.paths.source_campaign_root / v2._SESSION_REPORT_NAME
    session = json.loads(session_path.read_bytes())
    captures = session["captures"]
    owned_attempts = session["owned_attempts"]
    assert isinstance(captures, list)
    assert isinstance(owned_attempts, list)
    assert len(captures) == len(owned_attempts) == len(v2.REQUIRED_STAGES)
    if mutation == "missing":
        captures.pop()
        owned_attempts.pop()
    elif mutation == "duplicated":
        captures[1] = captures[0]
        owned_attempts[1] = owned_attempts[0]
    else:
        captures[0], captures[1] = captures[1], captures[0]
        owned_attempts[0], owned_attempts[1] = owned_attempts[1], owned_attempts[0]
    _write_canonical(session_path, session)

    attempt_before = {
        path.relative_to(source.paths.attempt_root).as_posix(): path.read_bytes()
        for path in source.paths.attempt_root.rglob("*")
        if path.is_file()
    }
    events: list[tuple[str, str]] = []
    pixel_reads: list[Path] = []
    evaluator_called = False
    original_read_bytes = Path.read_bytes

    def audited_read_bytes(path: Path) -> bytes:
        if path.suffix == ".bgra":
            pixel_reads.append(path)
        return original_read_bytes(path)

    def forbidden_evaluator(*args: object, **kwargs: object) -> object:
        nonlocal evaluator_called
        del args, kwargs
        evaluator_called = True
        raise AssertionError("malformed source cases must not reach the evaluator")

    monkeypatch.setattr(Path, "read_bytes", audited_read_bytes)
    monkeypatch.setattr(
        "mining_automation.perception.inventory.positive_v3_independent_validation."
        "evaluate_frozen_v3_independent_validation",
        forbidden_evaluator,
    )

    with pytest.raises(v2.InventoryV3ProtocolV2Error):
        v2.preflight_source_metadata(
            protocol,
            authorization,
            attempt_base=attempt_base,
            access_hook=lambda phase, kind, _path: events.append((phase, kind)),
        )

    assert pixel_reads == []
    assert evaluator_called is False
    assert all(phase not in {"sensitive", "review"} for phase, _kind in events)
    assert {
        path.relative_to(source.paths.attempt_root).as_posix(): path.read_bytes()
        for path in source.paths.attempt_root.rglob("*")
        if path.is_file()
    } == attempt_before
    assert not source.paths.workspace_root.exists()
    assert not source.paths.result_root.exists()


def test_acquisition_nested_metadata_rejects_before_pixel_open(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    temporary = TemporaryDirectory(prefix="v2-acquisition-metadata-")
    request.addfinalizer(temporary.cleanup)
    temporary_root = Path(temporary.name)
    root = temporary_root / "r"
    root.mkdir()
    attempt_base = temporary_root / "a"
    protocol, _, source = _prepare_copy_integrity_campaign(
        root,
        attempt_base,
        monkeypatch,
    )
    acquisition = v2.finalize_acquisition(
        root,
        expected_head=protocol.evaluator_head_sha,
        attempt_base=attempt_base,
    )
    manifest_path = acquisition.root / v2._CAMPAIGN_MANIFEST_NAME
    manifest = json.loads(manifest_path.read_bytes())
    first_case = manifest["cases"][0]
    assert isinstance(first_case, dict)
    first_case["hint"] = "empty"
    manifest["dataset_id"] = v2._content_bound_dataset_id(manifest)
    _coherently_rebind_json_member(
        acquisition.root,
        v2._CAMPAIGN_MANIFEST_NAME,
        manifest,
    )
    acquisition_record_path = acquisition.root / "protocol-v2-acquisition.json"
    acquisition_record = json.loads(acquisition_record_path.read_bytes())
    acquisition_record["campaign_manifest_sha256"] = _sha256(
        _canonical_bytes(manifest)
    )
    acquisition_record["dataset_id"] = manifest["dataset_id"]
    _coherently_rebind_json_member(
        acquisition.root,
        "protocol-v2-acquisition.json",
        acquisition_record,
    )
    monkeypatch.setattr(v2, "_verify_successful_operation", lambda *args, **kwargs: None)
    pixel_reads: list[Path] = []
    events: list[tuple[str, str]] = []
    original_read_bytes = Path.read_bytes

    def audited_read_bytes(path: Path) -> bytes:
        if path.suffix == ".bgra":
            pixel_reads.append(path)
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", audited_read_bytes)
    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="campaign case 1 keys differ",
    ):
        v2._load_acquisition(
            source,
            access_hook=lambda phase, kind, _path: events.append((phase, kind)),
        )

    assert pixel_reads == []
    assert ("sensitive", "validation_pixels_opened") not in events


def test_post_finalization_acquisition_mutation_is_terminal_before_review(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    temporary = TemporaryDirectory(prefix="v2-post-finalization-attack-")
    request.addfinalizer(temporary.cleanup)
    temporary_root = Path(temporary.name)
    root = temporary_root / "r"
    root.mkdir()
    attempt_base = temporary_root / "a"
    protocol, _, source = _prepare_copy_integrity_campaign(
        root,
        attempt_base,
        monkeypatch,
    )
    acquisition = v2.finalize_acquisition(
        root,
        expected_head=protocol.evaluator_head_sha,
        attempt_base=attempt_base,
    )
    real_reserve = v2._reserve_attempt
    mutation_done = False

    def reserve_then_mutate(
        paths: v2.ProtocolV2Paths,
        binding_protocol: v2.ProtocolV2LockBinding,
        operation: str,
        binding: Mapping[str, object],
    ) -> str:
        nonlocal mutation_done
        digest = real_reserve(paths, binding_protocol, operation, binding)
        if operation == "prepare-review-intake":
            manifest_path = acquisition.root / v2._CAMPAIGN_MANIFEST_NAME
            manifest = json.loads(manifest_path.read_bytes())
            cases = manifest["cases"]
            assert isinstance(cases, list)
            first_case = cases[0]
            assert isinstance(first_case, dict)
            first_case["operator_stage_label"] = "foreign-post-finalization-label"
            _coherently_rebind_json_member(
                acquisition.root,
                v2._CAMPAIGN_MANIFEST_NAME,
                manifest,
            )
            mutation_done = True
        return digest

    monkeypatch.setattr(v2, "_reserve_attempt", reserve_then_mutate)
    events: list[tuple[str, str]] = []
    with pytest.raises(v2.InventoryV3ProtocolV2Error):
        v2.prepare_reviewer_intake(
            root,
            expected_head=protocol.evaluator_head_sha,
            attempt_base=attempt_base,
            access_hook=lambda phase, kind, _path: events.append((phase, kind)),
        )

    assert mutation_done is True
    operation = "prepare-review-intake"
    terminal = json.loads(
        (source.paths.attempt_root / f"{operation}-terminal.json").read_bytes()
    )
    assert terminal["status"] == "failed-terminal"
    assert terminal["contract_id"] == "CASE_EVIDENCE_INELIGIBLE"
    assert terminal["retry_allowed"] is False
    assert all(phase not in {"sensitive", "review"} for phase, _kind in events)
    assert not (
        source.paths.review_intake_root / "package" / v2._REVIEW_TEMPLATE_NAME
    ).exists()
    assert not (source.paths.review_intake_root / "submission").exists()
    assert not (
        source.paths.attempt_root / "record-review-submission-reserved.json"
    ).exists()

    provider_called = False

    def forbidden_provider(_template: Mapping[str, object]) -> Mapping[str, object]:
        nonlocal provider_called
        provider_called = True
        raise AssertionError("terminal acquisition mutation must block reviewer truth")

    with pytest.raises(v2.InventoryV3ProtocolV2Error):
        v2.record_reviewer_submission(
            root,
            expected_head=protocol.evaluator_head_sha,
            reviewer="reviewer-b",
            truth_provider=forbidden_provider,
            attempt_base=attempt_base,
        )
    assert provider_called is False
    assert not (
        source.paths.attempt_root / "record-review-submission-reserved.json"
    ).exists()


def test_reviewer_template_extra_hint_rejects_before_review_pixels(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    temporary = TemporaryDirectory(prefix="v2-template-exact-")
    request.addfinalizer(temporary.cleanup)
    temporary_root = Path(temporary.name)
    root = temporary_root / "r"
    root.mkdir()
    attempt_base = temporary_root / "a"
    protocol, _, source = _prepare_copy_integrity_campaign(
        root,
        attempt_base,
        monkeypatch,
    )
    acquisition = v2.finalize_acquisition(
        root,
        expected_head=protocol.evaluator_head_sha,
        attempt_base=attempt_base,
    )
    intake = v2.prepare_reviewer_intake(
        root,
        expected_head=protocol.evaluator_head_sha,
        attempt_base=attempt_base,
    )
    package_root = intake.root / "package"
    template_path = package_root / v2._REVIEW_TEMPLATE_NAME
    template = json.loads(template_path.read_bytes())
    template["hint"] = "empty"
    _coherently_rebind_json_member(
        package_root,
        v2._REVIEW_TEMPLATE_NAME,
        template,
    )
    monkeypatch.setattr(v2, "_verify_successful_operation", lambda *args, **kwargs: None)
    pixel_reads: list[Path] = []
    original_read_bytes = Path.read_bytes

    def audited_read_bytes(path: Path) -> bytes:
        if path.suffix == ".bgra":
            pixel_reads.append(path)
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", audited_read_bytes)
    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="independent reviewer template keys differ",
    ):
        v2._load_review_intake(
            source,
            expected_manifest_sha256=acquisition.campaign_manifest_sha256,
            expected_acquisition_tree_sha256=acquisition.package_tree_sha256,
            submission_state="absent",
        )

    assert pixel_reads == []


def test_development_capture_identity_rejects_before_pixels_or_review(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    temporary = TemporaryDirectory(prefix="v2-development-identity-")
    request.addfinalizer(temporary.cleanup)
    temporary_root = Path(temporary.name)
    root = temporary_root / "r"
    root.mkdir()
    attempt_base = temporary_root / "a"
    protocol, authorization = _build_synthetic_source_campaign(
        root,
        attempt_base,
        monkeypatch,
    )
    source_root = root.joinpath(*v2._SOURCE_OUTPUT_ROOT.parts) / _AUTHORIZATION_ID
    session = json.loads((source_root / v2._SESSION_REPORT_NAME).read_bytes())
    capture = session["captures"][0]
    capture_id = capture["capture_id"]
    assert isinstance(capture_id, str)
    monkeypatch.setattr(
        v2,
        "_frozen_development_identity_sets",
        lambda _protocol: (frozenset(), frozenset(), frozenset({capture_id})),
    )
    monkeypatch.setattr(
        "validation.inventory_v3_protocol_v2.protocol.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(
        v2,
        "_verify_approval_registry_absent",
        lambda _protocol, *, access_hook=None: b"approval absent\n",
    )
    monkeypatch.setattr(
        v2,
        "_git",
        lambda _root, *args, **kwargs: "2024-12-31T23:59:30+00:00",
    )
    events: list[tuple[str, str]] = []
    pixel_reads: list[Path] = []
    original_read_bytes = Path.read_bytes

    def audited_read_bytes(path: Path) -> bytes:
        if path.suffix == ".bgra":
            pixel_reads.append(path)
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", audited_read_bytes)

    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="reuses a frozen development identity",
    ):
        v2.preflight_source_metadata(
            protocol,
            authorization,
            attempt_base=attempt_base,
            access_hook=lambda phase, kind, _path: events.append((phase, kind)),
        )

    assert pixel_reads == []
    assert all(phase not in {"sensitive", "review"} for phase, _kind in events)


def test_development_dataset_identity_is_explicitly_rejected() -> None:
    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="frozen development dataset identity",
    ):
        v2._require_non_development_dataset_identity(v2._DEVELOPMENT_DATASET_ID)

    v2._require_non_development_dataset_identity("independent-dataset-a")


def test_source_capture_identity_requires_exact_locked_formula() -> None:
    captured_at = "2025-01-01T00:00:01.000000Z"
    capture_id = "20250101T000001.000000Z-001-empty"

    parsed = v2._require_frozen_capture_identity(
        capture_id,
        captured_at,
        sequence_index=1,
        stage="empty",
    )
    assert v2._format_utc(parsed) == captured_at

    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="locked passive capture formula",
    ):
        v2._require_frozen_capture_identity(
            "foreign/capture",
            captured_at,
            sequence_index=1,
            stage="empty",
        )
    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="timestamp is not canonical",
    ):
        v2._require_frozen_capture_identity(
            "20250101T000001Z-001-empty",
            "2025-01-01T00:00:01Z",
            sequence_index=1,
            stage="empty",
        )


def test_source_capture_nested_metadata_uses_exact_frozen_shapes() -> None:
    full: dict[str, object] = {
        "height": v2.SUPPORTED_FRAME_HEIGHT,
        "path": "captures/001-empty/full-frame.bgra",
        "pixel_format": v2.SUPPORTED_PIXEL_FORMAT,
        "sha256": "1" * 64,
        "size_bytes": v2.FULL_FRAME_SIZE,
        "width": v2.SUPPORTED_FRAME_WIDTH,
    }
    region: dict[str, object] = {
        "path": "captures/001-empty/inventory-region.bgra",
        "region": list(v2.SUPPORTED_REGION),
        "sha256": "2" * 64,
        "size_bytes": v2.REGION_SIZE,
    }
    owned_frame: dict[str, object] = {
        "frame_id": 1,
        **full,
    }
    owned_window: dict[str, object] = {
        "class": "SunAwtFrame",
        "handle": _WINDOW_HANDLE,
        "windows_dpi": 144,
    }

    v2._require_source_capture_nested_shapes(
        full,
        region,
        owned_frame,
        owned_window,
        index=1,
    )

    with pytest.raises(v2.InventoryV3ProtocolV2Error, match="keys differ"):
        v2._require_source_capture_nested_shapes(
            {**full, "foreign": "metadata"},
            region,
            owned_frame,
            owned_window,
            index=1,
        )
    with pytest.raises(v2.InventoryV3ProtocolV2Error, match="positive integer"):
        v2._require_source_capture_nested_shapes(
            full,
            region,
            {**owned_frame, "frame_id": 0},
            owned_window,
            index=1,
        )


def test_source_nested_shape_gate_runs_before_any_pixel_open(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    temporary = TemporaryDirectory(prefix="v2-source-shape-")
    request.addfinalizer(temporary.cleanup)
    temporary_root = Path(temporary.name)
    root = temporary_root / "r"
    root.mkdir()
    attempt_base = temporary_root / "a"
    protocol, authorization = _build_synthetic_source_campaign(
        root,
        attempt_base,
        monkeypatch,
    )
    monkeypatch.setattr(
        "validation.inventory_v3_protocol_v2.protocol.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(
        v2,
        "_verify_approval_registry_absent",
        lambda _protocol, *, access_hook=None: b"approval absent\n",
    )
    monkeypatch.setattr(
        v2,
        "_git",
        lambda _root, *args, **kwargs: "2024-12-31T23:59:30+00:00",
    )

    def reject_nested_shape(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise v2.InventoryV3ProtocolV2Error("synthetic nested source shape differs")

    monkeypatch.setattr(v2, "_require_source_capture_nested_shapes", reject_nested_shape)
    pixel_reads: list[Path] = []
    events: list[tuple[str, str]] = []
    original_read_bytes = Path.read_bytes

    def audited_read_bytes(path: Path) -> bytes:
        if path.suffix == ".bgra":
            pixel_reads.append(path)
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", audited_read_bytes)

    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="synthetic nested source shape differs",
    ):
        v2.preflight_source_metadata(
            protocol,
            authorization,
            attempt_base=attempt_base,
            access_hook=lambda phase, kind, _path: events.append((phase, kind)),
        )

    assert pixel_reads == []
    assert all(phase not in {"sensitive", "review"} for phase, _kind in events)


def test_runbook_viewer_ignores_undefined_gdi_alpha_byte() -> None:
    runbook = (_REPOSITORY_ROOT / "docs" / "INVENTORY_VALIDATION_PROTOCOL_V2.md").read_text(
        encoding="utf-8"
    )

    assert "[System.Windows.Media.PixelFormats]::Bgr32" in runbook
    assert "[System.Windows.Media.PixelFormats]::Bgra32" not in runbook
    assert "[System.IO.File]::ReadAllBytes" in runbook
    assert "fourth byte as unused" in runbook


def test_source_to_acquisition_copy_mutation_never_gets_success_terminal(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    temporary = TemporaryDirectory(prefix="v2-copy-source-")
    request.addfinalizer(temporary.cleanup)
    temporary_root = Path(temporary.name)
    root = temporary_root / "r"
    root.mkdir()
    attempt_base = temporary_root / "a"
    protocol, _, source = _prepare_copy_integrity_campaign(
        root,
        attempt_base,
        monkeypatch,
    )
    original_copy = v2._copy_file_exclusive
    copied_member_mutated = False

    def mutate_copied_member(
        source_path: Path,
        destination: Path,
        label: str,
    ) -> None:
        nonlocal copied_member_mutated
        original_copy(source_path, destination, label)
        if destination.name == v2._PRODUCER_ATTESTATION_NAME:
            destination.write_bytes(destination.read_bytes() + b"post-copy mutation\n")
            copied_member_mutated = True

    monkeypatch.setattr(v2, "_copy_file_exclusive", mutate_copied_member)

    with pytest.raises(v2.InventoryV3ProtocolV2Error):
        v2.finalize_acquisition(
            root,
            expected_head=protocol.evaluator_head_sha,
            attempt_base=attempt_base,
        )

    assert copied_member_mutated is True
    operation = "finalize-acquisition"
    terminal = json.loads((source.paths.attempt_root / f"{operation}-terminal.json").read_bytes())
    assert terminal["status"] == "failed-terminal"
    assert terminal["contract_id"] == "CASE_EVIDENCE_INELIGIBLE"
    assert terminal["status"] != "passed-terminal"


def test_acquisition_to_reviewed_copy_mutation_never_gets_success_terminal(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    temporary = TemporaryDirectory(prefix="v2-copy-reviewed-")
    request.addfinalizer(temporary.cleanup)
    temporary_root = Path(temporary.name)
    root = temporary_root / "r"
    root.mkdir()
    attempt_base = temporary_root / "a"
    protocol, _, source = _prepare_copy_integrity_campaign(
        root,
        attempt_base,
        monkeypatch,
    )
    v2.finalize_acquisition(
        root,
        expected_head=protocol.evaluator_head_sha,
        attempt_base=attempt_base,
    )
    v2.prepare_reviewer_intake(
        root,
        expected_head=protocol.evaluator_head_sha,
        attempt_base=attempt_base,
    )

    occupied_slots = (0, 7, 14, 27, 28, None, None)
    visibilities = (
        "inventory-visible",
        "inventory-visible",
        "inventory-visible",
        "inventory-visible",
        "inventory-visible",
        "wrong-tab-visible",
        "inventory-obstructed",
    )

    def review_provider(template: Mapping[str, object]) -> Mapping[str, object]:
        raw_cases = template.get("cases")
        assert isinstance(raw_cases, list)
        sleep(0.001)
        cases = []
        for index, (raw_case, occupied, visibility) in enumerate(
            zip(raw_cases, occupied_slots, visibilities, strict=True),
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
                        "ordinary_iron_only": index in range(2, 6),
                        "quantity_text_visible": False,
                        "review_note": None,
                        "selected_item_visible": False,
                        "visibility": visibility,
                    },
                }
            )
        return {
            "cases": cases,
            "reviewed_at_utc": v2._format_utc(datetime.now(UTC)),
            "reviewer": "reviewer-b",
        }

    v2.record_reviewer_submission(
        root,
        expected_head=protocol.evaluator_head_sha,
        reviewer="reviewer-b",
        truth_provider=review_provider,
        attempt_base=attempt_base,
    )
    original_copy = v2._copy_file_exclusive
    copied_member_mutated = False

    def mutate_copied_member(
        source_path: Path,
        destination: Path,
        label: str,
    ) -> None:
        nonlocal copied_member_mutated
        original_copy(source_path, destination, label)
        if destination.name == v2._PRODUCER_ATTESTATION_NAME:
            destination.write_bytes(destination.read_bytes() + b"post-copy mutation\n")
            copied_member_mutated = True

    monkeypatch.setattr(v2, "_copy_file_exclusive", mutate_copied_member)

    with pytest.raises(v2.InventoryV3ProtocolV2Error):
        v2.publish_reviewed_package(
            root,
            expected_head=protocol.evaluator_head_sha,
            attempt_base=attempt_base,
        )

    assert copied_member_mutated is True
    operation = "publish-reviewed-package"
    terminal = json.loads((source.paths.attempt_root / f"{operation}-terminal.json").read_bytes())
    assert terminal["status"] == "failed-terminal"
    assert terminal["contract_id"] == "CASE_EVIDENCE_INELIGIBLE"
    assert terminal["status"] != "passed-terminal"


def test_foreign_reviewed_package_transplant_rejects_before_evaluator_or_result(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    temporary = TemporaryDirectory(prefix="v2-foreign-reviewed-attack-")
    request.addfinalizer(temporary.cleanup)
    temporary_root = Path(temporary.name)
    root = temporary_root / "r"
    root.mkdir()
    attempt_base = temporary_root / "a"
    protocol, _, source = _prepare_copy_integrity_campaign(
        root,
        attempt_base,
        monkeypatch,
    )
    v2.finalize_acquisition(
        root,
        expected_head=protocol.evaluator_head_sha,
        attempt_base=attempt_base,
    )
    v2.prepare_reviewer_intake(
        root,
        expected_head=protocol.evaluator_head_sha,
        attempt_base=attempt_base,
    )
    occupied_slots = (0, 1, 5, 27, 28, None, None)
    visibilities = (
        "inventory-visible",
        "inventory-visible",
        "inventory-visible",
        "inventory-visible",
        "inventory-visible",
        "wrong-tab-visible",
        "inventory-obstructed",
    )

    def review_provider(template: Mapping[str, object]) -> Mapping[str, object]:
        raw_cases = template.get("cases")
        assert isinstance(raw_cases, list)
        sleep(0.001)
        cases = []
        for index, (raw_case, occupied, visibility) in enumerate(
            zip(raw_cases, occupied_slots, visibilities, strict=True),
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
                        "ordinary_iron_only": index in range(2, 6),
                        "quantity_text_visible": False,
                        "review_note": None,
                        "selected_item_visible": False,
                        "visibility": visibility,
                    },
                }
            )
        return {
            "cases": cases,
            "reviewed_at_utc": v2._format_utc(datetime.now(UTC)),
            "reviewer": "reviewer-b",
        }

    v2.record_reviewer_submission(
        root,
        expected_head=protocol.evaluator_head_sha,
        reviewer="reviewer-b",
        truth_provider=review_provider,
        attempt_base=attempt_base,
    )
    v2.publish_reviewed_package(
        root,
        expected_head=protocol.evaluator_head_sha,
        attempt_base=attempt_base,
    )
    original_tree_payload = (
        source.paths.reviewed_package_root / v2._PACKAGE_TREE_NAME
    ).read_bytes()
    foreign_root = temporary_root / "foreign-reviewed-package"
    copytree(source.paths.reviewed_package_root, foreign_root)
    foreign_record_path = foreign_root / "protocol-v2-reviewed-package.json"
    foreign_record = json.loads(foreign_record_path.read_bytes())
    foreign_record["authorization_id"] = "3" * 64
    foreign_tree_sha = _coherently_rebind_json_member(
        foreign_root,
        "protocol-v2-reviewed-package.json",
        foreign_record,
    )
    assert foreign_tree_sha != _sha256(original_tree_payload)
    foreign_snapshot = v2._read_verified_tree(
        foreign_root,
        v2._reviewed_package_roles(),
        expected_tree_sha256=foreign_tree_sha,
    )
    foreign_snapshot.recheck()
    copytree(foreign_root, source.paths.reviewed_package_root, dirs_exist_ok=True)

    evaluator_called = False

    def forbidden_evaluator(*args: object, **kwargs: object) -> object:
        nonlocal evaluator_called
        del args, kwargs
        evaluator_called = True
        raise AssertionError("foreign reviewed package must not reach the evaluator")

    monkeypatch.setattr(
        "mining_automation.perception.inventory.positive_v3_independent_validation."
        "evaluate_frozen_v3_independent_validation",
        forbidden_evaluator,
    )
    events: list[tuple[str, str]] = []
    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="publish-reviewed-package successful lineage record differs",
    ):
        v2.evaluate_locked_protocol_v2(
            root,
            expected_head=protocol.evaluator_head_sha,
            attempt_base=attempt_base,
            access_hook=lambda phase, kind, _path: events.append((phase, kind)),
        )

    assert evaluator_called is False
    assert all(phase != "sensitive" for phase, _kind in events)
    assert not (
        source.paths.attempt_root / "evaluate-locked-candidate-reserved.json"
    ).exists()
    assert not source.paths.result_root.exists()


def test_partial_or_foreign_source_tree_fails_before_any_pixel_open(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    temporary = TemporaryDirectory(prefix="v2-tree-")
    request.addfinalizer(temporary.cleanup)
    temporary_root = Path(temporary.name)
    root = temporary_root / "r"
    root.mkdir()
    protocol = _protocol(root)
    authorization = _authorization()
    paths = v2.ProtocolV2Paths.for_authorization(
        root,
        authorization.authorization_id,
        protocol.lock_sha256,
        attempt_base=temporary_root / "a",
    )
    paths.source_campaign_root.mkdir(parents=True)
    (paths.source_campaign_root / "foreign.bgra").write_bytes(b"private sentinel")
    opened_pixels: list[Path] = []
    events: list[tuple[str, str]] = []
    original_read_bytes = Path.read_bytes

    def audited_read_bytes(path: Path) -> bytes:
        if path.suffix == ".bgra":
            opened_pixels.append(path)
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", audited_read_bytes)
    monkeypatch.setattr(
        v2,
        "_verify_approval_registry_absent",
        lambda _protocol, *, access_hook=None: b"approval absent\n",
    )

    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="fixed allowlist",
    ):
        v2.preflight_source_metadata(
            protocol,
            authorization,
            attempt_base=temporary_root / "a",
            access_hook=lambda phase, kind, _path: events.append((phase, kind)),
        )

    assert opened_pixels == []
    assert events == [("preflight", "source_tree_metadata")]


def test_capture_environment_and_configuration_mismatch_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    protocol = _protocol(root)
    authorization = _authorization()
    environment: dict[str, object] = {
        "capture_build_sha": v2.PROTOCOL_V1_SOURCE_HEAD,
        "capture_configuration_id": v2.CAPTURE_CONFIGURATION_ID,
        "capture_execution_head_sha": protocol.evaluator_head_sha,
        "client_mode": "fixed",
        "frame": {
            "height": v2.SUPPORTED_FRAME_HEIGHT,
            "pixel_format": v2.SUPPORTED_PIXEL_FORMAT,
            "profile_id": v2.SUPPORTED_PROFILE_ID,
            "width": v2.SUPPORTED_FRAME_WIDTH,
        },
        "host_reservation_sha256": "8" * 64,
        "live_authorization_git_blob": authorization.legacy_registry_git_blob,
        "live_authorization_git_commit_sha": authorization.git_commit_sha,
        "live_authorization_id": authorization.authorization_id,
        "protocol_lock_git_commit_sha": v2.PROTOCOL_V1_LOCK_HEAD,
        "python_isolated_mode": True,
        "python_isolated_source_cache": True,
        "python_no_site_mode": True,
        "renderer": "gpu",
        "runelite_build": "operator-asserted-build",
        "theme": "dark",
        "window_class": "SunAwtFrame",
        "window_handle": _WINDOW_HANDLE,
        "windows_dpi": 144,
        "windows_scaling_percent": 150,
        "windows_version": "Windows-synthetic-test",
    }
    monkeypatch.setattr(
        "validation.inventory_v3_protocol_v2.protocol.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    v2._validate_capture_environment(environment, protocol, authorization)

    for key, foreign in (
        ("capture_build_sha", "0" * 40),
        ("capture_configuration_id", "foreign-selection-policy"),
        ("live_authorization_id", "9" * 64),
        ("protocol_lock_git_commit_sha", "0" * 40),
    ):
        rebound = dict(environment)
        rebound[key] = foreign
        with pytest.raises(
            v2.InventoryV3ProtocolV2Error,
            match="capture environment provenance differs",
        ):
            v2._validate_capture_environment(rebound, protocol, authorization)

    wrong_frame = dict(environment)
    frame_value = environment["frame"]
    assert isinstance(frame_value, dict)
    wrong_frame["frame"] = {**frame_value, "width": 1004}
    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="supported BGRA envelope",
    ):
        v2._validate_capture_environment(wrong_frame, protocol, authorization)


def test_release_critical_input_output_overlap_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    source = _source_binding(root, tmp_path / "local-app-data")
    overlapping = replace(
        source.paths,
        attempt_root=source.paths.source_campaign_root / "attempts",
    )

    with pytest.raises(v2.InventoryV3ProtocolV2Error, match="roots overlap"):
        v2._assert_disjoint_paths(overlapping)


def test_one_shot_attempt_reservation_and_failure_are_permanent(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    protocol = _protocol(root)
    paths = v2.ProtocolV2Paths(
        repository_root=root.resolve(strict=True),
        authorization_id=_AUTHORIZATION_ID,
        source_campaign_root=root / "source",
        workspace_root=root / "workspace",
        acquisition_root=root / "acquisition",
        review_intake_root=root / "review-intake",
        reviewed_package_root=root / "reviewed-package",
        approval_request_root=root / "approval-request",
        result_root=root / "result",
        attempt_root=tmp_path / "attempts",
    )
    operation = "capture-passive-campaign"
    v2._reserve_attempt(paths, protocol, operation, {"input": "frozen"})
    reserved_path = paths.attempt_root / f"{operation}-reserved.json"
    reserved_before = reserved_path.read_bytes()

    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="fixed allowlist|already exists",
    ):
        v2._reserve_attempt(paths, protocol, operation, {"input": "replacement"})

    assert reserved_path.read_bytes() == reserved_before
    v2._record_attempt_terminal(
        paths,
        protocol,
        operation,
        status="failed-terminal",
        contract_id="CAMPAIGN_TERMINAL_FAILURE",
        output_sha256="a" * 64,
    )
    terminal_path = paths.attempt_root / f"{operation}-terminal.json"
    terminal_before = terminal_path.read_bytes()
    terminal = json.loads(terminal_before)
    assert terminal["status"] == "failed-terminal"
    assert terminal["retry_allowed"] is False
    assert terminal["activation_allowed"] is False
    assert terminal["promotion_allowed"] is False

    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="fallback status is permanent ATTEMPT_INTEGRITY_FAILURE",
    ):
        v2._attempt_failed_best_effort(
            paths,
            protocol,
            _authorization(),
            operation,
            "CAMPAIGN_TERMINAL_FAILURE",
            error_type="SyntheticFailure",
        )
    assert terminal_path.read_bytes() == terminal_before


def test_public_failure_integration_exposes_only_opaque_closed_projection(
    tmp_path: Path,
) -> None:
    result_root = tmp_path / "private-result"
    result_root.mkdir()

    v2._write_public_failure_receipt(
        result_root,
        _authorization(),
        privacy.FailureContractId.C6_WRONG_TAB_UNKNOWN_SAFETY_FAILURE.value,
    )

    document = json.loads((result_root / "public-failure-receipt.json").read_bytes())
    assert set(document) == {
        "activation_allowed",
        "contract_id",
        "opaque_receipt_id",
        "promotion_allowed",
        "retry_allowed",
        "schema",
        "terminal_status",
    }
    assert document["retry_allowed"] is False
    assert document["terminal_status"] == "failed-permanent"
    serialized = _canonical_bytes(document).decode("ascii")
    assert re.search(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", serialized) is None
    for forbidden in (
        "pixels",
        "reviewer_truth",
        "occupied_slots",
        "confidence",
        "operator",
        "reviewer",
        "approver",
        "path",
    ):
        assert forbidden not in document


@pytest.mark.parametrize(
    ("failed_stage", "expected_contract"),
    (
        ("empty", "C1_EMPTY_ZERO_CONFORMANCE_FAILURE"),
        ("early-partial", "C2_EARLY_PARTIAL_CONFORMANCE_FAILURE"),
        ("mid-partial", "C3_MID_PARTIAL_ORDER_CONFORMANCE_FAILURE"),
    ),
)
def test_c1_c2_c3_failure_contract_selection_is_preregistered_source_order(
    failed_stage: str,
    expected_contract: str,
) -> None:
    cases = [
        SimpleNamespace(
            planned_stage_id=stage,
            passed=(stage != failed_stage),
        )
        for stage in reversed(v2.REQUIRED_STAGES)
    ]

    assert v2._failure_contract_for_report(SimpleNamespace(cases=cases)) == (expected_contract)


class _StableSnapshot:
    def recheck(self) -> None:
        return None


def _patch_approval_pipeline(
    root: Path,
    attempt_base: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    operator: str = "operator-a",
    reviewer: str = "reviewer-b",
    successful_ledger: bool = False,
) -> tuple[v2.SourceMetadataBinding, bytes, bytes]:
    source = _source_binding(root, attempt_base, operator=operator)
    campaign_id = v2._content_bound_campaign_id(str(source.session["session_id"]))
    (
        _,
        evaluator_session_payload,
        _,
        evaluator_seal_payload,
    ) = v2._evaluator_compatible_source_documents(source, campaign_id)
    source.paths.workspace_root.mkdir(parents=True)
    reviewed_tree_payload = b"reviewed closed tree\n"
    result_tree_payload = b"result closed tree\n"
    reviewed_record: Mapping[str, object] = {
        "operator": operator,
        "reviewed_at_utc": "2025-01-01T00:45:00.000000Z",
        "reviewer": reviewer,
        "reviewer_truth_sha256": "4" * 64,
        "validation_package_sha256": "5" * 64,
    }
    reviewed_acquisition: Mapping[str, object] = {
        "original_source_completion_seal_sha256": _sha256(source.completion_payload),
        "original_source_session_report_sha256": _sha256(source.session_payload),
        "source_completion_seal_sha256": _sha256(evaluator_seal_payload),
        "source_identity_bridge": v2._SOURCE_IDENTITY_BRIDGE,
        "source_session_report_sha256": _sha256(evaluator_session_payload),
    }
    result_record: Mapping[str, object] = {
        "activation_allowed": False,
        "approval_required": True,
        "authorization_id": source.authorization.authorization_id,
        "campaign_id": campaign_id,
        "campaign_manifest_sha256": "6" * 64,
        "dataset_id": "dataset-a",
        "detector_conformance_passed": True,
        "evaluated_at_utc": "2025-01-01T01:00:00.000000Z",
        "frozen_evaluator_report_sha256": "7" * 64,
        "promotion_allowed": False,
        "protocol_lock_git_commit_sha": source.protocol.lock_commit_sha,
        "protocol_lock_sha256": source.protocol.lock_sha256,
        "retry_allowed": False,
        "reviewed_package_tree_sha256": _sha256(reviewed_tree_payload),
        "schema": "inventory-positive-v3-independent-terminal-result-v2",
        "terminal_status": "conformance-passed-source-approval-required",
        "validation_package_sha256": "5" * 64,
    }
    result_payload = _canonical_bytes(result_record)
    registry = {
        "activation_allowed": False,
        "entries": [],
        "promotion_allowed": False,
        "schema": "inventory-positive-v3-independent-validation-approval-registry-v1",
    }
    _write_canonical(root.joinpath(*v2._APPROVAL_REGISTRY_PATH.parts), registry)
    registry_payload = _canonical_bytes(registry)

    def verify_empty_registry(
        _protocol: v2.ProtocolV2LockBinding,
        *,
        access_hook: v2.AccessHook | None = None,
    ) -> bytes:
        del access_hook
        registry_path = root.joinpath(*v2._APPROVAL_REGISTRY_PATH.parts)
        sidecar_path = root.joinpath(*v2._APPROVAL_REGISTRY_SIDECAR_PATH.parts)
        expected_sidecar = (
            f"{_sha256(registry_payload)}  {v2._APPROVAL_REGISTRY_PATH.name}\n"
        ).encode("ascii")
        if (
            registry_path.read_bytes() != registry_payload
            or sidecar_path.read_bytes() != expected_sidecar
        ):
            raise v2.InventoryV3ProtocolV2Error("source approval registry differs")
        return registry_payload

    monkeypatch.setattr(
        v2,
        "verify_protocol_v2_repository",
        lambda _root, *, expected_head: source.protocol,
    )
    monkeypatch.setattr(
        v2,
        "verify_live_authorization",
        lambda _protocol, *, access_hook=None: source.authorization,
    )
    monkeypatch.setattr(v2, "preflight_source_metadata", lambda *args, **kwargs: source)
    monkeypatch.setattr(v2, "_recheck_source_metadata", lambda _source: None)
    monkeypatch.setattr(v2, "_verify_approval_registry_absent", verify_empty_registry)
    monkeypatch.setattr(
        v2,
        "_assert_workspace_children",
        lambda _paths, _expected: None,
    )
    monkeypatch.setattr(
        v2,
        "_preflight_review_pipeline_lineage",
        lambda _source, *, require_reviewed: {
            "acquisition_package_tree_sha256": "1" * 64,
            "review_intake_package_tree_sha256": "2" * 64,
            "reviewed_package_tree_sha256": _sha256(reviewed_tree_payload),
        },
    )
    monkeypatch.setattr(
        v2,
        "_preflight_tree_metadata_only",
        lambda path, _expected_roles: (
            ({}, reviewed_tree_payload)
            if path == source.paths.reviewed_package_root
            else ({}, result_tree_payload)
        ),
    )
    original_read_document = v2._read_canonical_json

    def read_document(path: Path, **_kwargs: object) -> tuple[Mapping[str, object], bytes]:
        if path.name == "protocol-v2-reviewed-package.json":
            return reviewed_record, _canonical_bytes(reviewed_record)
        if path.name == "protocol-v2-acquisition.json":
            return reviewed_acquisition, _canonical_bytes(reviewed_acquisition)
        if path.name == "protocol-v2-terminal-result.json":
            return result_record, result_payload
        raw_schema = _kwargs.get("schema")
        schema = raw_schema if isinstance(raw_schema, str) else None
        return original_read_document(
            path,
            schema=schema,
            label=str(_kwargs.get("label", path.name)),
            require_sidecar=bool(_kwargs.get("require_sidecar", True)),
        )

    monkeypatch.setattr(v2, "_read_canonical_json", read_document)
    monkeypatch.setattr(
        v2,
        "_read_verified_tree",
        lambda _root, _expected_roles, **_kwargs: _StableSnapshot(),
    )
    if successful_ledger:
        through_evaluator = v2._ATTEMPT_OPERATIONS.index("evaluate-locked-candidate")
        for index, operation in enumerate(
            v2._ATTEMPT_OPERATIONS[: through_evaluator + 1],
            start=1,
        ):
            binding: Mapping[str, object] = {}
            output_sha = f"{index:x}" * 64
            if operation == "evaluate-locked-candidate":
                binding = {
                    "campaign_manifest_sha256": "6" * 64,
                    "opaque_receipt_id": source.authorization.opaque_receipt_id,
                    "reviewed_package_tree_sha256": _sha256(reviewed_tree_payload),
                    "validation_package_sha256": "5" * 64,
                }
                output_sha = _sha256(result_tree_payload)
            v2._reserve_attempt(
                source.paths,
                source.protocol,
                operation,
                binding,
            )
            v2._record_attempt_terminal(
                source.paths,
                source.protocol,
                operation,
                status="passed-terminal",
                contract_id=v2._ATTEMPT_SUCCESS_CONTRACTS[operation],
                output_sha256=output_sha,
            )
    else:
        monkeypatch.setattr(
            v2,
            "_verify_successful_operation",
            lambda *args, **kwargs: datetime(2025, 1, 1, 1, 1, tzinfo=UTC),
        )
    monkeypatch.setattr(v2, "_reserve_attempt", lambda *args, **kwargs: "reserved")
    monkeypatch.setattr(v2, "_record_attempt_terminal", lambda *args, **kwargs: None)
    return source, reviewed_tree_payload, result_tree_payload


@pytest.mark.parametrize("conflicting_approver", ("operator-a", "reviewer-b"))
def test_approver_must_differ_from_operator_and_reviewer_before_any_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    conflicting_approver: str,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    source, _, _ = _patch_approval_pipeline(
        root,
        tmp_path / "local-app-data",
        monkeypatch,
        successful_ledger=True,
    )

    with pytest.raises(v2.InventoryV3ProtocolV2Error, match="pairwise distinct"):
        v2.prepare_approval_request(
            root,
            expected_head=source.protocol.evaluator_head_sha,
            proposed_approver=conflicting_approver,
            proposed_approved_at_utc="2025-01-01T02:00:00.000000Z",
            attempt_base=tmp_path / "local-app-data",
        )

    assert not source.paths.approval_request_root.exists()


def test_approval_request_never_mutates_source_approval_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    source, _, _ = _patch_approval_pipeline(
        root,
        tmp_path / "local-app-data",
        monkeypatch,
        successful_ledger=True,
    )
    registry_path = root.joinpath(*v2._APPROVAL_REGISTRY_PATH.parts)
    sidecar_path = registry_path.with_suffix(registry_path.suffix + ".sha256")
    registry_before = registry_path.read_bytes()
    sidecar_before = sidecar_path.read_bytes()

    result = v2.prepare_approval_request(
        root,
        expected_head=source.protocol.evaluator_head_sha,
        proposed_approver="approver-c",
        proposed_approved_at_utc=v2._format_utc(datetime.now(UTC)),
        attempt_base=tmp_path / "local-app-data",
    )

    assert registry_path.read_bytes() == registry_before
    assert sidecar_path.read_bytes() == sidecar_before
    request_path = Path(str(result["path"]))
    request = json.loads(request_path.read_bytes())
    assert request["approval_registry_modified"] is False
    assert request["status"] == "request-only-not-approved"
    assert request["activation_allowed"] is False
    assert request["promotion_allowed"] is False
    assert request["source_action_required"] is True
    proposed = request["proposed_approval"]
    assert set(proposed) == {
        "approval_id",
        "approved_at_utc",
        "approver",
        "campaign_id",
        "campaign_manifest_sha256",
        "dataset_id",
        "operator",
        "package_sha256",
        "reviewer",
        "reviewer_truth_sha256",
        "source_completion_seal_sha256",
        "source_session_report_sha256",
        "status",
    }
    assert proposed["approver"] == "approver-c"
    approval_identity = {key: value for key, value in proposed.items() if key != "approval_id"}
    assert proposed["approval_id"] == (
        "inventory-positive-v3-approval-"
        + _sha256(v2._canonical_data_bytes(approval_identity))[:24]
    )
    proposed_files = request["proposed_source_files"]
    assert [item["path"] for item in proposed_files] == [
        v2._APPROVAL_REGISTRY_PATH.as_posix(),
        v2._APPROVAL_REGISTRY_SIDECAR_PATH.as_posix(),
    ]
    proposed_registry_file, proposed_sidecar_file = proposed_files
    proposed_registry = proposed_registry_file["content"]
    assert proposed_registry["entries"] == [proposed]
    proposed_registry_payload = _canonical_bytes(proposed_registry)
    proposed_registry_sha = _sha256(proposed_registry_payload)
    assert proposed_registry_file["sha256"] == proposed_registry_sha
    assert result["proposed_registry_sha256"] == proposed_registry_sha
    expected_sidecar = f"{proposed_registry_sha}  {v2._APPROVAL_REGISTRY_PATH.name}\n"
    assert proposed_sidecar_file["content_ascii"] == expected_sidecar
    assert proposed_sidecar_file["sha256"] == _sha256(expected_sidecar.encode("ascii"))


def test_approval_request_rejects_future_chronology_before_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    source, _, _ = _patch_approval_pipeline(
        root,
        tmp_path / "local-app-data",
        monkeypatch,
        successful_ledger=True,
    )

    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="timestamp cannot be in the future",
    ):
        v2.prepare_approval_request(
            root,
            expected_head=source.protocol.evaluator_head_sha,
            proposed_approver="approver-c",
            proposed_approved_at_utc="2099-01-01T02:00:00.000000Z",
            attempt_base=tmp_path / "local-app-data",
        )

    assert not source.paths.approval_request_root.exists()


def test_reviewer_must_differ_from_operator_before_submission_is_written(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    source = _source_binding(root, tmp_path / "local-app-data")
    monkeypatch.setattr(
        v2,
        "verify_protocol_v2_repository",
        lambda _root, *, expected_head: source.protocol,
    )
    monkeypatch.setattr(
        v2,
        "verify_live_authorization",
        lambda _protocol, *, access_hook=None: source.authorization,
    )
    monkeypatch.setattr(v2, "preflight_source_metadata", lambda *args, **kwargs: source)
    monkeypatch.setattr(v2, "_recheck_source_metadata", lambda _source: None)
    monkeypatch.setattr(
        v2,
        "_assert_workspace_children",
        lambda _paths, _expected: None,
    )
    monkeypatch.setattr(
        v2,
        "_read_canonical_json",
        lambda *args, **kwargs: ({"finalized_at_utc": "2025-01-01T00:30:00Z"}, b"metadata\n"),
    )
    snapshot = _StableSnapshot()
    monkeypatch.setattr(
        v2,
        "_load_acquisition",
        lambda *args, **kwargs: ({}, {}, "3" * 64, "4" * 64, snapshot),
    )
    monkeypatch.setattr(
        v2,
        "_load_review_intake",
        lambda *args, **kwargs: ({"cases": []}, "5" * 64, snapshot),
    )
    monkeypatch.setattr(v2, "_reserve_attempt", lambda *args, **kwargs: "reserved")
    monkeypatch.setattr(v2, "_attempt_failed_best_effort", lambda *args, **kwargs: None)
    provider_called = False

    def forbidden_provider(_template: Mapping[str, object]) -> Mapping[str, object]:
        nonlocal provider_called
        provider_called = True
        raise AssertionError("truth provider must not run for a conflicted reviewer")

    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="independent reviewer must differ from operator",
    ):
        v2.record_reviewer_submission(
            root,
            expected_head=source.protocol.evaluator_head_sha,
            reviewer="operator-a",
            truth_provider=forbidden_provider,
            attempt_base=tmp_path / "local-app-data",
        )

    submission_root = source.paths.review_intake_root / "submission"
    assert provider_called is False
    assert not submission_root.exists()


def test_reviewer_timestamp_must_be_observed_during_collection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    source = _source_binding(root, tmp_path / "local-app-data")
    source.paths.review_intake_root.mkdir(parents=True)
    acquisition_tree_payload = b"acquisition tree\n"
    intake_tree_payload = b"intake tree\n"
    manifest = {
        "finalized_at_utc": "2025-01-01T00:30:00.000000Z",
        "schema": v2._CAMPAIGN_MANIFEST_SCHEMA,
    }
    manifest_payload = _canonical_bytes(manifest)
    monkeypatch.setattr(
        v2,
        "verify_protocol_v2_repository",
        lambda _root, *, expected_head: source.protocol,
    )
    monkeypatch.setattr(
        v2,
        "verify_live_authorization",
        lambda _protocol, *, access_hook=None: source.authorization,
    )
    monkeypatch.setattr(v2, "preflight_source_metadata", lambda *args, **kwargs: source)
    monkeypatch.setattr(v2, "_recheck_source_metadata", lambda _source: None)
    monkeypatch.setattr(v2, "_assert_workspace_children", lambda *args, **kwargs: None)
    monkeypatch.setattr(v2, "_verify_successful_operation", lambda *args, **kwargs: None)
    monkeypatch.setattr(v2, "_scan_metadata_only_closed_tree", lambda *args, **kwargs: ())
    monkeypatch.setattr(v2, "_reserve_attempt", lambda *args, **kwargs: "reserved")
    monkeypatch.setattr(v2, "_attempt_failed_best_effort", lambda *args, **kwargs: None)

    def preflight_tree(path: Path, _roles: Mapping[str, str]) -> tuple[Mapping[str, object], bytes]:
        payload = (
            intake_tree_payload
            if path == source.paths.review_intake_root / "package"
            else acquisition_tree_payload
        )
        return {}, payload

    monkeypatch.setattr(v2, "_preflight_tree_metadata_only", preflight_tree)
    monkeypatch.setattr(
        v2,
        "_read_canonical_json",
        lambda *args, **kwargs: (manifest, manifest_payload),
    )
    monkeypatch.setattr(
        v2,
        "_load_acquisition",
        lambda *args, **kwargs: (
            {},
            {},
            _sha256(manifest_payload),
            _sha256(acquisition_tree_payload),
            _StableSnapshot(),
        ),
    )
    monkeypatch.setattr(
        v2,
        "_load_review_intake",
        lambda *args, **kwargs: (
            {"cases": []},
            _sha256(intake_tree_payload),
            _StableSnapshot(),
        ),
    )

    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="observed during this reviewer collection",
    ):
        v2.record_reviewer_submission(
            root,
            expected_head=source.protocol.evaluator_head_sha,
            reviewer="reviewer-b",
            truth_provider=lambda _template: {
                "cases": [],
                "reviewed_at_utc": "2099-01-01T00:00:00.000000Z",
                "reviewer": "reviewer-b",
            },
            attempt_base=tmp_path / "local-app-data",
        )


def test_live_authorization_must_be_direct_nonmerge_child_of_exact_l2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    protocol = _protocol(root)
    authorization_id = _sha256(
        v2._canonical_data_bytes(
            {
                "capture_build_sha": v2.PROTOCOL_V1_SOURCE_HEAD,
                "capture_configuration_id": v2.CAPTURE_CONFIGURATION_ID,
                "case_sequence": list(v2.REQUIRED_STAGES),
                "frozen_candidate_head_sha": v2.FROZEN_V3_HEAD,
                "opaque_receipt_id": _OPAQUE_RECEIPT_ID,
                "protocol_lock_git_commit_sha": protocol.lock_commit_sha,
                "protocol_lock_sha256": protocol.lock_sha256,
                "protocol_source_git_commit_sha": protocol.source_commit_sha,
            }
        )
    )
    legacy_registry = {
        "activation_allowed": False,
        "authorizations": [
            {
                "authorization_id": authorization_id,
                "capture_build_sha": v2.PROTOCOL_V1_SOURCE_HEAD,
                "capture_configuration_id": v2.CAPTURE_CONFIGURATION_ID,
                "protocol_lock_git_commit_sha": v2.PROTOCOL_V1_LOCK_HEAD,
                "protocol_lock_sha256": v2.PROTOCOL_V1_LOCK_SHA256,
                "status": v2.LIVE_AUTHORIZATION_STATUS,
            }
        ],
        "schema": v2.LIVE_AUTHORIZATION_SCHEMA,
    }
    v2_registry = {
        "activation_allowed": False,
        "authorizations": [
            {
                "authorization_id": authorization_id,
                "capture_build_sha": v2.PROTOCOL_V1_SOURCE_HEAD,
                "capture_configuration_id": v2.CAPTURE_CONFIGURATION_ID,
                "frozen_candidate_head_sha": v2.FROZEN_V3_HEAD,
                "opaque_receipt_id": _OPAQUE_RECEIPT_ID,
                "predecessor_protocol_lock_git_commit_sha": (v2.PROTOCOL_V1_LOCK_HEAD),
                "predecessor_protocol_lock_sha256": v2.PROTOCOL_V1_LOCK_SHA256,
                "protocol_lock_git_commit_sha": protocol.lock_commit_sha,
                "protocol_lock_sha256": protocol.lock_sha256,
                "protocol_source_git_commit_sha": protocol.source_commit_sha,
                "status": v2._V2_LIVE_AUTHORIZATION_STATUS,
            }
        ],
        "schema": v2._V2_LIVE_AUTHORIZATION_SCHEMA,
    }
    legacy_path = root.joinpath(*v2._LIVE_AUTHORIZATION_PATH.parts)
    v2_path = root.joinpath(*v2._V2_LIVE_AUTHORIZATION_PATH.parts)
    legacy_payload = _write_canonical(legacy_path, legacy_registry, sidecar=False)
    v2_payload = _write_canonical(v2_path, v2_registry)
    v2_sidecar_path = v2_path.with_suffix(v2_path.suffix + ".sha256")
    committed_payloads = {
        v2._LIVE_AUTHORIZATION_PATH.as_posix(): legacy_payload,
        v2._V2_LIVE_AUTHORIZATION_PATH.as_posix(): v2_payload,
        v2._V2_LIVE_AUTHORIZATION_SIDECAR_PATH.as_posix(): (v2_sidecar_path.read_bytes()),
    }

    def git_bytes(_root: Path, *args: str) -> bytes:
        assert args[0] == "show"
        _commit, relative = args[1].split(":", maxsplit=1)
        return committed_payloads[relative]

    def git(_root: Path, *args: str) -> str:
        if args[0] == "log":
            return _AUTHORIZATION_HEAD
        if args[0] == "diff-tree":
            return "\n".join(committed_payloads)
        if args[:3] == ("show", "-s", "--format=%P"):
            return "9" * 40
        raise AssertionError(f"unexpected synthetic Git query: {args!r}")

    monkeypatch.setattr(v2, "_git_bytes", git_bytes)
    monkeypatch.setattr(v2, "_git", git)

    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="direct non-merge child of exact L2",
    ):
        v2.verify_live_authorization(protocol)


def test_fixed_package_tree_roles_reject_self_described_extra_entry(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "package"
    package_root.mkdir()
    (package_root / "evidence.bin").write_bytes(b"frozen evidence")
    (package_root / "self-described-extra.bin").write_bytes(b"foreign evidence")
    v2._write_tree_document(
        package_root,
        {
            "evidence.bin": "frozen-evidence",
            "self-described-extra.bin": "self-described-extra",
        },
    )

    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="package tree roles differ from fixed protocol",
    ):
        v2._preflight_tree_metadata_only(
            package_root,
            {"evidence.bin": "frozen-evidence"},
        )


def test_reviewer_submission_requires_successful_prior_operation_ledgers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    source = _source_binding(root, tmp_path / "local-app-data")
    monkeypatch.setattr(
        v2,
        "verify_protocol_v2_repository",
        lambda _root, *, expected_head: source.protocol,
    )
    monkeypatch.setattr(
        v2,
        "verify_live_authorization",
        lambda _protocol, *, access_hook=None: source.authorization,
    )
    monkeypatch.setattr(v2, "preflight_source_metadata", lambda *args, **kwargs: source)
    monkeypatch.setattr(v2, "_recheck_source_metadata", lambda _source: None)
    monkeypatch.setattr(
        v2,
        "_assert_workspace_children",
        lambda _paths, _expected: None,
    )
    monkeypatch.setattr(
        v2,
        "_preflight_tree_metadata_only",
        lambda _root, _roles: ({}, b"closed acquisition tree\n"),
    )
    provider_called = False

    def forbidden_provider(_template: Mapping[str, object]) -> Mapping[str, object]:
        nonlocal provider_called
        provider_called = True
        raise AssertionError("review must not start without successful prior ledgers")

    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="finalize-acquisition reservation is unavailable",
    ):
        v2.record_reviewer_submission(
            root,
            expected_head=source.protocol.evaluator_head_sha,
            reviewer="reviewer-b",
            truth_provider=forbidden_provider,
            attempt_base=tmp_path / "local-app-data",
        )

    assert provider_called is False
    assert not (source.paths.review_intake_root / "submission").exists()


def test_result_root_collision_closes_integrity_failure_and_is_permanent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    source = _source_binding(root, tmp_path / "local-app-data")
    source.paths.result_root.mkdir()
    evaluator_index = v2._ATTEMPT_OPERATIONS.index("evaluate-locked-candidate")
    for index, operation in enumerate(
        v2._ATTEMPT_OPERATIONS[:evaluator_index],
        start=1,
    ):
        v2._reserve_attempt(source.paths, source.protocol, operation, {})
        v2._record_attempt_terminal(
            source.paths,
            source.protocol,
            operation,
            status="passed-terminal",
            contract_id=v2._ATTEMPT_SUCCESS_CONTRACTS[operation],
            output_sha256=f"{index:x}" * 64,
        )
    reviewed_tree_payload = b"closed reviewed package tree\n"
    reviewed_tree_sha = _sha256(reviewed_tree_payload)
    reviewed_tree_document: Mapping[str, object] = {
        "entries": [
            {
                "path": v2._REVIEWER_TRUTH_NAME,
                "role": "independent-reviewer-truth",
                "sha256": "4" * 64,
                "size_bytes": 1,
            }
        ],
        "schema": "inventory-positive-v3-independent-package-tree-v1",
    }
    manifest: Mapping[str, object] = {
        "campaign_id": "campaign-a",
        "dataset_id": "dataset-a",
        "finalized_at_utc": "2025-01-01T00:30:00.000000Z",
        "operator": "operator-a",
        "schema": v2._CAMPAIGN_MANIFEST_SCHEMA,
    }
    manifest_payload = _canonical_bytes(manifest)
    manifest_sha = _sha256(manifest_payload)
    reviewer_truth_sha = "4" * 64
    package: Mapping[str, object] = {
        "activation_allowed": False,
        "campaign_manifest": {
            "path": v2._CAMPAIGN_MANIFEST_NAME,
            "sha256": manifest_sha,
        },
        "dataset_role": "independent-validation-only",
        "preregistration_sha256": v2.PROTOCOL_V1_PREREGISTRATION_SHA256,
        "prototype_eligible": False,
        "reviewer_truth": {
            "path": v2._REVIEWER_TRUTH_NAME,
            "sha256": reviewer_truth_sha,
        },
        "schema": v2._VALIDATION_PACKAGE_SCHEMA,
        "training_allowed": False,
    }
    package_payload = _canonical_bytes(package)
    package_sha = _sha256(package_payload)
    reviewed_record: Mapping[str, object] = {
        "acquisition_package_tree_sha256": reviewed_tree_sha,
        "activation_allowed": False,
        "authorization_id": source.authorization.authorization_id,
        "campaign_id": "campaign-a",
        "campaign_manifest_sha256": manifest_sha,
        "dataset_id": "dataset-a",
        "operator": "operator-a",
        "promotion_allowed": False,
        "protocol_lock_git_commit_sha": source.protocol.lock_commit_sha,
        "protocol_lock_sha256": source.protocol.lock_sha256,
        "review_intake_tree_sha256": "2" * 64,
        "review_submission_sha256": "3" * 64,
        "reviewed_at_utc": "2025-01-01T00:45:00.000000Z",
        "reviewer": "reviewer-b",
        "reviewer_truth_sha256": reviewer_truth_sha,
        "schema": v2._REVIEWED_PACKAGE_SCHEMA,
        "status": "reviewed-evaluator-ready",
        "training_allowed": False,
        "validation_package_sha256": package_sha,
    }
    prior_lineage = {
        "acquisition_package_tree_sha256": reviewed_tree_sha,
        "campaign_manifest_sha256": manifest_sha,
        "review_intake_package_tree_sha256": "2" * 64,
        "review_submission_package_tree_sha256": "5" * 64,
        "review_submission_sha256": "3" * 64,
        "reviewed_package_tree_sha256": reviewed_tree_sha,
    }
    monkeypatch.setattr(
        v2,
        "verify_protocol_v2_repository",
        lambda _root, *, expected_head: source.protocol,
    )
    monkeypatch.setattr(
        v2,
        "verify_live_authorization",
        lambda _protocol, *, access_hook=None: source.authorization,
    )
    monkeypatch.setattr(v2, "preflight_source_metadata", lambda *args, **kwargs: source)
    monkeypatch.setattr(v2, "_recheck_source_metadata", lambda _source: None)
    monkeypatch.setattr(
        v2,
        "_assert_workspace_children",
        lambda _paths, _expected: None,
    )
    monkeypatch.setattr(
        v2,
        "_preflight_review_pipeline_lineage",
        lambda _source, *, require_reviewed: prior_lineage,
    )
    monkeypatch.setattr(
        v2,
        "_preflight_tree_metadata_only",
        lambda _root, _roles: (reviewed_tree_document, reviewed_tree_payload),
    )
    monkeypatch.setattr(
        v2,
        "_preflight_acquisition_semantics",
        lambda _source, _root, _tree, _payload: (manifest, {}, manifest_payload),
    )
    monkeypatch.setattr(
        v2,
        "_require_tree_entry_subset_equal",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        v2,
        "_require_tree_bound_payload",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        v2,
        "_read_tree_bound_payload",
        lambda _root, _entries, relative, _label: (
            f"{reviewer_truth_sha}  {v2._REVIEWER_TRUTH_NAME}\n".encode("ascii")
            if relative == f"{v2._REVIEWER_TRUTH_NAME}.sha256"
            else b""
        ),
    )
    monkeypatch.setattr(
        v2,
        "_frozen_development_identity_sets",
        lambda _protocol: (frozenset(), frozenset(), frozenset()),
    )
    monkeypatch.setattr(
        v2,
        "_require_manifest_development_identity_disjoint",
        lambda _manifest, _development: None,
    )
    original_read_document = v2._read_canonical_json

    def read_document(
        path: Path,
        *,
        schema: str | None,
        label: str,
        require_sidecar: bool = True,
    ) -> tuple[Mapping[str, object], bytes]:
        if path.name == v2._CAMPAIGN_MANIFEST_NAME:
            return manifest, manifest_payload
        if path.name == v2._VALIDATION_PACKAGE_NAME:
            return package, package_payload
        if path.name == "protocol-v2-reviewed-package.json":
            return reviewed_record, _canonical_bytes(reviewed_record)
        return original_read_document(
            path,
            schema=schema,
            label=label,
            require_sidecar=require_sidecar,
        )

    monkeypatch.setattr(v2, "_read_canonical_json", read_document)
    events: list[tuple[str, str]] = []

    with pytest.raises(FileExistsError):
        v2.evaluate_locked_protocol_v2(
            root,
            expected_head=source.protocol.evaluator_head_sha,
            attempt_base=tmp_path / "local-app-data",
            access_hook=lambda phase, kind, _path: events.append((phase, kind)),
        )

    assert all(phase != "sensitive" for phase, _kind in events)
    failure_root = source.paths.attempt_root / "evaluate-locked-candidate-failure"
    snapshot = v2._read_verified_tree(
        failure_root,
        v2._operation_failure_roles(),
    )
    snapshot.recheck()
    private_failure = json.loads((failure_root / "private-failure.json").read_bytes())
    assert private_failure["contract_id"] == "ATTEMPT_INTEGRITY_FAILURE"
    assert private_failure["error_type"] == "FileExistsError"
    assert private_failure["operation"] == "evaluate-locked-candidate"
    assert private_failure["terminal_status"] == "failed-terminal-permanent"
    assert private_failure["retry_allowed"] is False
    assert private_failure["activation_allowed"] is False
    assert private_failure["promotion_allowed"] is False
    public_failure = json.loads((failure_root / "public-failure-receipt.json").read_bytes())
    assert public_failure["contract_id"] == "ATTEMPT_INTEGRITY_FAILURE"
    assert public_failure["terminal_status"] == "failed-permanent"
    assert public_failure["retry_allowed"] is False
    operation = "evaluate-locked-candidate"
    terminal_path = source.paths.attempt_root / f"{operation}-terminal.json"
    terminal_before = terminal_path.read_bytes()
    terminal = json.loads(terminal_before)
    assert terminal["status"] == "failed-terminal"
    assert terminal["contract_id"] == "ATTEMPT_INTEGRITY_FAILURE"
    assert terminal["retry_allowed"] is False
    assert terminal["output_sha256"] == _sha256((failure_root / v2._PACKAGE_TREE_NAME).read_bytes())
    failure_before = {
        path.name: path.read_bytes() for path in failure_root.iterdir() if path.is_file()
    }

    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="fixed allowlist|output already exists",
    ):
        v2.evaluate_locked_protocol_v2(
            root,
            expected_head=source.protocol.evaluator_head_sha,
            attempt_base=tmp_path / "local-app-data",
        )

    assert terminal_path.read_bytes() == terminal_before
    assert {
        path.name: path.read_bytes() for path in failure_root.iterdir() if path.is_file()
    } == failure_before


def _patch_synthetic_locked_repository(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    omitted_path: str | None,
    touched_path: str | None,
) -> None:
    legacy_authorization_payload = _canonical_bytes(
        {
            "activation_allowed": False,
            "authorizations": [],
            "schema": v2.LIVE_AUTHORIZATION_SCHEMA,
        }
    )
    initial_authorization = {
        "activation_allowed": False,
        "authorizations": [],
        "schema": v2._V2_LIVE_AUTHORIZATION_SCHEMA,
    }
    authorization_path = root.joinpath(*v2._V2_LIVE_AUTHORIZATION_PATH.parts)
    authorization_payload = _write_canonical(authorization_path, initial_authorization)
    authorization_sidecar_payload = authorization_path.with_suffix(
        authorization_path.suffix + ".sha256"
    ).read_bytes()
    assert _sha256(authorization_payload) == v2._V2_EMPTY_LIVE_AUTHORIZATION_SHA256
    preregistration_source = Path(v2.__file__).with_name("preregistration.json")
    preregistration_payload = preregistration_source.read_bytes()
    preregistration_path = root.joinpath(*v2._V2_PREREGISTRATION_PATH.parts)
    preregistration_path.parent.mkdir(parents=True, exist_ok=True)
    preregistration_path.write_bytes(preregistration_payload)
    preregistration_sidecar_payload = (
        f"{v2.PROTOCOL_V2_PREREGISTRATION_SHA256}  {v2._V2_PREREGISTRATION_PATH.name}\n"
    ).encode("ascii")
    preregistration_path.with_suffix(".json.sha256").write_bytes(preregistration_sidecar_payload)
    coordinator_blob = "8" * 40
    frozen_v1_blobs = {
        v2._V1_LOCK_PATH.as_posix(): "a" * 40,
        v2._V1_LOCK_SIDECAR_PATH.as_posix(): "b" * 40,
    }
    locked_entries = [
        {"git_blob": coordinator_blob, "path": path}
        for path in v2._V2_SOURCE_PATHS
        if path != omitted_path
    ]
    locked_entries.extend(
        {"git_blob": blob, "path": path} for path, blob in frozen_v1_blobs.items()
    )
    lock_document = {
        "activation_allowed": False,
        "approved_passive_capture": {
            "build_sha": v2.PROTOCOL_V1_SOURCE_HEAD,
            "capture_configuration_id": v2.CAPTURE_CONFIGURATION_ID,
            "legacy_live_authorization_initial_git_blob": "3" * 40,
            "legacy_live_authorization_initial_sha256": _sha256(legacy_authorization_payload),
            "live_authorization_path": v2._LIVE_AUTHORIZATION_PATH.as_posix(),
            "protocol_v2_live_authorization_initial_git_blob": "6" * 40,
            "protocol_v2_live_authorization_initial_sha256": (
                v2._V2_EMPTY_LIVE_AUTHORIZATION_SHA256
            ),
            "protocol_v2_live_authorization_path": (v2._V2_LIVE_AUTHORIZATION_PATH.as_posix()),
            "protocol_v2_live_authorization_sidecar_initial_git_blob": "7" * 40,
            "reservation_scope": "windows-user-local-not-host-global",
        },
        "frozen_candidate_head_sha": v2.FROZEN_V3_HEAD,
        "live_validation_authorized": False,
        "predecessor": {
            "protocol_lock_git_commit_sha": v2.PROTOCOL_V1_LOCK_HEAD,
            "protocol_lock_sha256": v2.PROTOCOL_V1_LOCK_SHA256,
            "protocol_source_git_commit_sha": v2.PROTOCOL_V1_SOURCE_HEAD,
        },
        "preregistration_sha256": v2.PROTOCOL_V2_PREREGISTRATION_SHA256,
        "protocol": {
            "id": v2.PROTOCOL_V2_ID,
            "locked_git_blobs": locked_entries,
            "source_commit_sha": _PROTOCOL_SOURCE_HEAD,
            "version": v2.PROTOCOL_V2_VERSION,
        },
        "schema": v2.PROTOCOL_V2_LOCK_SCHEMA,
    }
    lock_path = root.joinpath(*v2._V2_LOCK_PATH.parts)
    lock_payload = _write_canonical(lock_path, lock_document)
    lock_sidecar_payload = lock_path.with_suffix(lock_path.suffix + ".sha256").read_bytes()
    lock_artifact_blobs = {
        v2._V2_LOCK_PATH.as_posix(): "4" * 40,
        v2._V2_LOCK_SIDECAR_PATH.as_posix(): "5" * 40,
    }
    committed_payloads = {
        (
            v2.PROTOCOL_V1_LOCK_HEAD,
            v2._LIVE_AUTHORIZATION_PATH.as_posix(),
        ): legacy_authorization_payload,
        (
            _PROTOCOL_SOURCE_HEAD,
            v2._LIVE_AUTHORIZATION_PATH.as_posix(),
        ): legacy_authorization_payload,
        (_PROTOCOL_SOURCE_HEAD, v2._V2_LIVE_AUTHORIZATION_PATH.as_posix()): (authorization_payload),
        (
            _PROTOCOL_SOURCE_HEAD,
            v2._V2_LIVE_AUTHORIZATION_SIDECAR_PATH.as_posix(),
        ): authorization_sidecar_payload,
        (
            _PROTOCOL_SOURCE_HEAD,
            v2._V2_PREREGISTRATION_PATH.as_posix(),
        ): preregistration_payload,
        (
            _PROTOCOL_SOURCE_HEAD,
            v2._V2_PREREGISTRATION_SIDECAR_PATH.as_posix(),
        ): preregistration_sidecar_payload,
        (_PROTOCOL_LOCK_HEAD, v2._V2_LOCK_PATH.as_posix()): lock_payload,
        (_PROTOCOL_LOCK_HEAD, v2._V2_LOCK_SIDECAR_PATH.as_posix()): (lock_sidecar_payload),
    }

    def git_bytes(_root: Path, *args: str) -> bytes:
        assert args[0] == "show"
        commit, relative = args[1].split(":", maxsplit=1)
        return committed_payloads[(commit, relative)]

    def git(_root: Path, *args: str) -> str:
        if args[:3] == ("show", "-s", "--format=%P"):
            commit = args[3]
            if commit == _PROTOCOL_SOURCE_HEAD:
                return v2.PROTOCOL_V1_LOCK_HEAD
            if commit == _PROTOCOL_LOCK_HEAD:
                return _PROTOCOL_SOURCE_HEAD
        if args[:3] == ("show", "-s", "--format=%cI"):
            commit = args[3]
            if commit == _PROTOCOL_SOURCE_HEAD:
                return "2025-01-01T00:00:00+00:00"
            if commit == _PROTOCOL_LOCK_HEAD:
                return "2025-01-01T00:00:01+00:00"
        if args[0] == "diff-tree":
            if args[-1] == _PROTOCOL_SOURCE_HEAD:
                return "\n".join(f"A\t{path}" for path in v2._P2_CHANGED_PATHS)
            return "\n".join(lock_artifact_blobs)
        if args[0] == "rev-parse":
            commit, relative = args[1].split(":", maxsplit=1)
            if commit == v2.PROTOCOL_V1_LOCK_HEAD and relative == (
                v2._LIVE_AUTHORIZATION_PATH.as_posix()
            ):
                return "3" * 40
            if commit == _PROTOCOL_SOURCE_HEAD and relative == (
                v2._V2_LIVE_AUTHORIZATION_PATH.as_posix()
            ):
                return "6" * 40
            if commit == _PROTOCOL_SOURCE_HEAD and relative == (
                v2._V2_LIVE_AUTHORIZATION_SIDECAR_PATH.as_posix()
            ):
                return "7" * 40
            if relative in lock_artifact_blobs and commit in {
                _PROTOCOL_LOCK_HEAD,
                _EXECUTION_HEAD,
            }:
                return lock_artifact_blobs[relative]
            if relative in v2._V2_SOURCE_PATHS and commit in {
                _PROTOCOL_SOURCE_HEAD,
                _EXECUTION_HEAD,
            }:
                return coordinator_blob
            if relative in frozen_v1_blobs and commit in {
                _PROTOCOL_SOURCE_HEAD,
                _EXECUTION_HEAD,
            }:
                return frozen_v1_blobs[relative]
        if args[0] == "log":
            if "--diff-filter=A" in args:
                return _PROTOCOL_LOCK_HEAD
            separator = args.index("--")
            queried_paths = set(args[separator + 1 :])
            if touched_path is not None and touched_path in queried_paths:
                return "9" * 40
            return ""
        raise AssertionError(f"unexpected synthetic Git query: {args!r}")

    monkeypatch.setattr(
        v2,
        "_verify_base_repository_state",
        lambda _root, expected_head, *, source_mode: (root.resolve(strict=True), _EXECUTION_HEAD),
    )
    monkeypatch.setattr(
        v2,
        "_frozen_v1_locked_blob_map",
        lambda _root: frozen_v1_blobs,
    )
    monkeypatch.setattr(v2, "_git_bytes", git_bytes)
    monkeypatch.setattr(v2, "_git", git)


@pytest.mark.parametrize(
    ("omitted_path", "touched_path", "expected_error"),
    (
        (
            None,
            v2._V2_LOCK_PATH.as_posix(),
            "V2 lock artifact changed after L2",
        ),
        (
            None,
            v2._V2_SOURCE_PATHS[0],
            "V2 locked path changed after P2",
        ),
        (
            v2._V2_SOURCE_PATHS[0],
            None,
            "V2 lock omits coordinator source",
        ),
        (
            None,
            v2._LIVE_AUTHORIZATION_PATH.as_posix(),
            "legacy live authorization registry changed before P2",
        ),
        (
            None,
            v2._V1_LOCK_PATH.as_posix(),
            "V2 locked path changed after P2",
        ),
    ),
)
def test_lock_rejects_modify_restore_history_and_omitted_coordinator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    omitted_path: str | None,
    touched_path: str | None,
    expected_error: str,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    _patch_synthetic_locked_repository(
        root,
        monkeypatch,
        omitted_path=omitted_path,
        touched_path=touched_path,
    )

    with pytest.raises(v2.InventoryV3ProtocolV2Error, match=expected_error):
        v2.verify_protocol_v2_repository(root, expected_head=_EXECUTION_HEAD)
