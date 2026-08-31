from __future__ import annotations

import hashlib
import inspect
import json
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from mining_automation.capture.windows import (
    CapturedPixels,
    WindowInfo,
    WindowsCaptureBackend,
)
from mining_automation.capture.windows.testing import FakeWin32Api
from mining_automation.validation import inventory_v3_capture as capture
from mining_automation.validation import inventory_v3_capture_cli as capture_cli

_ROOT = Path(__file__).resolve().parents[1]
_CAPTURE_LAUNCHER = _ROOT / "tools" / "capture_inventory_v3_independent.py"
_HWND = 3107


def _binding() -> capture._ProtocolBinding:
    return capture._ProtocolBinding(
        execution_head_sha="c" * 40,
        execution_head_committed_at_utc="2098-12-31T23:59:30Z",
        lock_commit_sha="b" * 40,
        lock_committed_at_utc="2098-12-31T23:59:00Z",
        lock_sha256="d" * 64,
        capture_build_sha="a" * 40,
        capture_configuration_id=(
            "inventory-positive-v3-independent-passive-natural-fill-v1"
        ),
    )


def _authorization() -> capture._LiveAuthorizationBinding:
    return capture._LiveAuthorizationBinding(
        authorization_id="2" * 64,
        git_commit_sha="e" * 40,
        git_committed_at_utc="2098-12-31T23:59:45Z",
        git_blob="f" * 40,
    )


def _inputs() -> capture.PassiveInventoryV3CaptureInputs:
    return capture.PassiveInventoryV3CaptureInputs(
        operator="operator-a",
        runelite_build="runelite-build-a",
        client_mode="fixed",
        theme="dark",
        renderer="gpu",
    )


def _frame_payload(*, width: int = 1005, height: int = 1078) -> bytes:
    payload = bytearray(width * height * 4)
    if width == 1005 and height == 1078:
        for row in range(248):
            for column in range(158):
                offset = ((569 + row) * width + 567 + column) * 4
                payload[offset : offset + 4] = bytes(
                    (column % 256, row % 256, (column + row) % 256, 255)
                )
    return bytes(payload)


def _expected_region(payload: bytes) -> bytes:
    row_bytes = 158 * 4
    return b"".join(
        payload[
            ((569 + row) * 1005 + 567) * 4 :
            ((569 + row) * 1005 + 567) * 4 + row_bytes
        ]
        for row in range(248)
    )


def _backend_factory(
    payload: bytes,
    *,
    width: int = 1005,
    height: int = 1078,
) -> tuple[Callable[[], WindowsCaptureBackend], FakeWin32Api]:
    api = FakeWin32Api(
        windows=[
            WindowInfo(
                hwnd=_HWND,
                title="RuneLite - private title",
                class_name="SunAwtFrame",
                is_visible=True,
                is_minimized=False,
                client_width=width,
                client_height=height,
            )
        ],
        captures={
            _HWND: CapturedPixels(
                payload=payload,
                width=width,
                height=height,
            )
        },
        dpi_by_hwnd={_HWND: 144},
    )

    def factory() -> WindowsCaptureBackend:
        return WindowsCaptureBackend(win32_api=api)

    return factory, api


def _timestamps() -> Iterator[str]:
    for second in range(60):
        yield f"2099-01-01T00:00:{second:02d}.000000Z"


def _patch_eligible_runtime(
    monkeypatch: pytest.MonkeyPatch,
    backend_factory: Callable[[], WindowsCaptureBackend],
    output_root: Path,
) -> list[str]:
    binding = _binding()
    authorization = _authorization()
    times = _timestamps()
    prepared: list[str] = []
    monkeypatch.setattr(capture, "_verify_capture_repository", lambda _root: binding)
    monkeypatch.setattr(
        capture,
        "_verify_live_capture_authorization",
        lambda _root, _binding: authorization,
    )
    monkeypatch.setattr(capture, "_new_source_owned_backend", backend_factory)
    monkeypatch.setattr(capture, "_approved_output_root", lambda _root: output_root)
    monkeypatch.setattr(
        capture,
        "_approved_host_reservation_root",
        lambda: output_root.parent / "host-reservations",
    )
    monkeypatch.setattr(capture, "_require_isolated_mode", lambda: None)
    monkeypatch.setattr(
        capture,
        "_acknowledge_stage",
        lambda stage, _index, _total, _path: prepared.append(stage),
    )
    monkeypatch.setattr(capture, "_utc_timestamp", lambda: next(times))
    monkeypatch.setattr(capture.platform, "platform", lambda: "Windows-test")
    return prepared


def _assert_sidecar(path: Path) -> None:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert path.with_suffix(path.suffix + ".sha256").read_text(
        encoding="ascii"
    ) == f"{digest}  {path.name}\n"


def _committed_launcher_repository(tmp_path: Path) -> Path:
    root = tmp_path / "launcher-repository"
    ignored = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
    for directory in ("src", "tools", "validation"):
        shutil.copytree(_ROOT / directory, root / directory, ignore=ignored)
    shutil.copy2(_ROOT / ".gitattributes", root / ".gitattributes")
    shutil.copy2(_ROOT / ".gitignore", root / ".gitignore")
    for arguments in (
        ("init", "--quiet"),
        ("config", "core.autocrlf", "false"),
        ("config", "core.longpaths", "true"),
        ("config", "user.name", "Launcher Test"),
        ("config", "user.email", "launcher-test@example.invalid"),
        ("add", "."),
        ("commit", "--quiet", "-m", "freeze launcher source"),
    ):
        subprocess.run(
            ("git", "-C", str(root), *arguments),
            check=True,
            capture_output=True,
            text=True,
        )
    return root


def test_cli_exposes_no_capture_identity_detector_stage_retry_or_title_override() -> None:
    destinations = {action.dest for action in capture_cli.build_parser()._actions}
    assert destinations == {
        "client_mode",
        "help",
        "operator",
        "renderer",
        "runelite_build",
        "theme",
    }
    assert tuple(
        inspect.signature(capture.run_passive_inventory_v3_capture_campaign).parameters
    ) == ("inputs", "repository_root")


@pytest.mark.parametrize(
    "python_flags",
    [(), ("-I",), ("-S",)],
    ids=("neither", "missing-no-site", "missing-isolation"),
)
def test_official_capture_launcher_rejects_missing_isolation_guards(
    python_flags: tuple[str, ...],
) -> None:
    rejected = subprocess.run(
        (sys.executable, *python_flags, str(_CAPTURE_LAUNCHER), "--help"),
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode == 2
    assert "requires the locked Python -I -S launcher" in rejected.stderr


def test_official_capture_launcher_has_fixed_entrypoint(tmp_path: Path) -> None:
    repository = _committed_launcher_repository(tmp_path)
    launcher = repository / "tools" / _CAPTURE_LAUNCHER.name
    isolated = subprocess.run(
        (sys.executable, "-I", "-S", str(launcher), "--help"),
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )
    assert isolated.returncode == 0
    assert "--operator" in isolated.stdout
    assert "--runelite-build" in isolated.stdout


def test_capture_cli_rejects_isolated_no_site_without_source_cache() -> None:
    source_root = json.dumps(str(_ROOT / "src"))
    code = (
        f"import sys; sys.path.insert(0, {source_root}); "
        "from mining_automation.validation.inventory_v3_capture_cli "
        "import main; raise SystemExit(main(['--help']))"
    )
    rejected = subprocess.run(
        (sys.executable, "-I", "-S", "-c", code),
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode == 2
    assert "isolated source cache" in rejected.stderr


def test_repository_failure_happens_before_backend_or_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def backend_factory() -> WindowsCaptureBackend:
        nonlocal calls
        calls += 1
        return _backend_factory(_frame_payload())[0]()

    def reject(_root: Path) -> capture._ProtocolBinding:
        raise capture.PassiveInventoryV3CaptureError("unverified protocol")

    monkeypatch.setattr(capture, "_verify_capture_repository", reject)
    monkeypatch.setattr(capture, "_require_isolated_mode", lambda: None)
    monkeypatch.setattr(capture, "_new_source_owned_backend", backend_factory)
    output = tmp_path / "evidence"
    monkeypatch.setattr(capture, "_approved_output_root", lambda _root: output)
    with pytest.raises(capture.PassiveInventoryV3CaptureError, match="unverified"):
        capture.run_passive_inventory_v3_capture_campaign(
            inputs=_inputs(),
            repository_root=_ROOT,
        )
    assert calls == 0
    assert not output.exists()


def test_private_capture_core_cannot_bypass_isolated_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def repository(_root: Path) -> capture._ProtocolBinding:
        nonlocal calls
        calls += 1
        return _binding()

    monkeypatch.setattr(capture, "_verify_capture_repository", repository)
    with pytest.raises(
        capture.PassiveInventoryV3CaptureError,
        match="requires the locked Python -I -S launcher",
    ):
        capture._run_passive_inventory_v3_capture_campaign(
            inputs=_inputs(),
            repository_root=_ROOT,
            progress_context=[],
        )
    assert calls == 0


def test_private_capture_core_rejects_wrong_main_before_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_calls = 0
    backend_calls = 0

    def repository(_root: Path) -> capture._ProtocolBinding:
        nonlocal repository_calls
        repository_calls += 1
        return _binding()

    def backend() -> WindowsCaptureBackend:
        nonlocal backend_calls
        backend_calls += 1
        return _backend_factory(_frame_payload())[0]()

    launcher = _ROOT / "tools" / "capture_inventory_v3_independent.py"
    fake_sys = SimpleNamespace(
        argv=[str(launcher)],
        flags=SimpleNamespace(isolated=1, no_site=1),
        modules={"__main__": SimpleNamespace(__file__=str(_ROOT / "wrong-main.py"))},
        pycache_prefix=str(_ROOT / "private-source-cache"),
    )
    monkeypatch.setattr(capture, "sys", fake_sys)
    monkeypatch.setattr(capture, "_verify_capture_repository", repository)
    monkeypatch.setattr(capture, "_new_source_owned_backend", backend)

    with pytest.raises(
        capture.PassiveInventoryV3CaptureError,
        match="fixed source-owned capture launcher",
    ):
        capture._run_passive_inventory_v3_capture_campaign(
            inputs=_inputs(),
            repository_root=_ROOT,
            progress_context=[],
        )

    assert repository_calls == 0
    assert backend_calls == 0


def test_empty_live_authorization_blocks_backend_and_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    monkeypatch.setattr(
        capture,
        "_verify_capture_repository",
        lambda _root: _binding(),
    )
    monkeypatch.setattr(capture, "_require_isolated_mode", lambda: None)

    def reject(
        _root: Path,
        _binding: capture._ProtocolBinding,
    ) -> capture._LiveAuthorizationBinding:
        raise capture.PassiveInventoryV3CaptureError(
            "LIVE VALIDATION NOT YET AUTHORIZED"
        )

    def backend_factory() -> WindowsCaptureBackend:
        nonlocal calls
        calls += 1
        return _backend_factory(_frame_payload())[0]()

    monkeypatch.setattr(capture, "_verify_live_capture_authorization", reject)
    monkeypatch.setattr(capture, "_new_source_owned_backend", backend_factory)
    output = tmp_path / "evidence"
    monkeypatch.setattr(capture, "_approved_output_root", lambda _root: output)
    with pytest.raises(
        capture.PassiveInventoryV3CaptureError,
        match="LIVE VALIDATION NOT YET AUTHORIZED",
    ):
        capture.run_passive_inventory_v3_capture_campaign(
            inputs=_inputs(),
            repository_root=_ROOT,
        )
    assert calls == 0
    assert not output.exists()


def test_capture_build_has_canonical_empty_prelock_authorization() -> None:
    protocol = capture._verify_capture_repository(_ROOT)
    payload = subprocess.run(
        (
            "git",
            "-C",
            str(_ROOT),
            "show",
            f"{protocol.capture_build_sha}:validation/inventory-positive-v3/"
            "live-campaign-authorizations.json",
        ),
        check=True,
        capture_output=True,
    ).stdout
    assert payload == (
        b'{"activation_allowed":false,"authorizations":[],"schema":'
        b'"inventory-positive-v3-independent-live-campaign-authorization-'
        b'registry-v1"}\n'
    )


def test_private_default_capture_root_is_ignored_by_git() -> None:
    completed = subprocess.run(
        (
            "git",
            "-C",
            str(_ROOT),
            "check-ignore",
            "diagnostics/inventory-positive-v3-independent-source/example.raw",
        ),
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0


def test_fixed_campaign_is_capture_only_complete_and_byte_preserving(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert capture.INDEPENDENT_CAPTURE_STAGES == (
        "empty",
        "early-partial",
        "mid-partial",
        "near-full",
        "full",
        "wrong-tab",
        "row-obstruction",
    )
    payload = _frame_payload()
    backend_factory, api = _backend_factory(payload)
    prepared = _patch_eligible_runtime(
        monkeypatch,
        backend_factory,
        tmp_path / "evidence",
    )

    result = capture.run_passive_inventory_v3_capture_campaign(
        inputs=_inputs(),
        repository_root=_ROOT,
    )

    assert prepared == list(capture.INDEPENDENT_CAPTURE_STAGES)
    assert result.capture_count == 7
    assert result.capture_build_sha == "a" * 40
    assert result.capture_execution_head_sha == "c" * 40
    assert result.live_authorization_git_commit_sha == "e" * 40
    assert result.live_authorization_id == "2" * 64
    assert len(result.host_reservation_sha256) == 64
    assert api.capture_calls == [_HWND] * 7
    _assert_sidecar(result.source_session_report_path)
    _assert_sidecar(result.source_completion_seal_path)
    session = json.loads(result.source_session_report_path.read_text(encoding="utf-8"))
    assert session["all_owned_captures_included"] is True
    assert session["capture_environment"]["python_isolated_mode"] is True
    assert session["capture_environment"]["python_no_site_mode"] is True
    assert session["capture_environment"]["python_isolated_source_cache"] is True
    assert len(session["owned_attempts"]) == 7
    assert [item["capture_id"] for item in session["owned_attempts"]] == [
        item["capture_id"] for item in session["captures"]
    ]
    assert [item["planned_stage_id"] for item in session["captures"]] == list(
        capture.INDEPENDENT_CAPTURE_STAGES
    )
    assert [item["sequence_index"] for item in session["captures"]] == list(
        range(1, 8)
    )
    for item in session["captures"]:
        report_path = result.campaign_directory / item["capture_report"]["path"]
        _assert_sidecar(report_path)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        full = (result.campaign_directory / report["full_frame"]["path"]).read_bytes()
        region = (
            result.campaign_directory / report["inventory_region"]["path"]
        ).read_bytes()
        assert full == payload
        assert region == _expected_region(payload)
        assert region[:4] == bytes((0, 0, 0, 255))
        assert region[-4:] == bytes((157, 247, 148, 255))
    progress = json.loads(
        (result.campaign_directory / "capture-progress.json").read_text(
            encoding="utf-8"
        )
    )
    assert progress["status"] == "ready-to-seal"
    assert progress["source_completion_seal_sha256"] == (
        result.source_completion_seal_sha256
    )
    assert len(progress["owned_attempts"]) == 7
    assert progress["detector_executed"] is False
    assert progress["activation_allowed"] is False
    seal = json.loads(result.source_completion_seal_path.read_text(encoding="utf-8"))
    assert seal["authorization_id"] == "2" * 64
    assert seal["host_reservation_sha256"] == result.host_reservation_sha256
    assert seal["capture_count"] == 7
    assert seal["source_session_report_sha256"] == (
        result.source_session_report_sha256
    )


def test_wrong_geometry_retains_successful_owned_frame_before_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _frame_payload(width=1004)
    backend_factory, _api = _backend_factory(payload, width=1004)
    output = tmp_path / "evidence"
    _patch_eligible_runtime(monkeypatch, backend_factory, output)

    with pytest.raises(
        capture.PassiveInventoryV3CaptureError,
        match="geometry differs",
    ):
        capture.run_passive_inventory_v3_capture_campaign(
            inputs=_inputs(),
            repository_root=_ROOT,
        )

    campaigns = list(output.iterdir())
    assert len(campaigns) == 1
    attempt = campaigns[0] / "captures" / "001-empty"
    assert (attempt / "full-frame.bgra").read_bytes() == payload
    _assert_sidecar(attempt / "owned-frame.json")
    progress = json.loads((campaigns[0] / "capture-progress.json").read_text())
    assert progress["status"] == "failed-retained"
    assert progress["failure"]["error_type"] == "PassiveInventoryV3CaptureError"
    assert len(progress["owned_attempts"]) == 1
    assert progress["captures"] == []
    terminal = campaigns[0] / "campaign-terminal-failure.json"
    _assert_sidecar(terminal)


def test_owned_report_failure_keeps_raw_attempt_in_terminal_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _frame_payload()
    backend_factory, _api = _backend_factory(payload)
    output = tmp_path / "evidence"
    _patch_eligible_runtime(monkeypatch, backend_factory, output)
    original = capture._write_canonical_with_sidecar_exclusive

    def fail_owned_report(path: Path, document: bytes) -> None:
        if path.name == "owned-frame.json":
            raise OSError("forced owned-report failure")
        original(path, document)

    monkeypatch.setattr(
        capture,
        "_write_canonical_with_sidecar_exclusive",
        fail_owned_report,
    )
    with pytest.raises(OSError, match="forced owned-report failure"):
        capture.run_passive_inventory_v3_capture_campaign(
            inputs=_inputs(),
            repository_root=_ROOT,
        )

    campaign = next(output.iterdir())
    terminal = json.loads(
        (campaign / "campaign-terminal-failure.json").read_text(encoding="utf-8")
    )
    attempt = terminal["owned_attempts"][0]
    assert attempt["status"] == "raw-retained"
    assert attempt["owned_frame_report"] is None
    raw = campaign / attempt["full_frame_attempt"]["path"]
    assert raw.read_bytes() == payload
    assert hashlib.sha256(payload).hexdigest() == attempt["full_frame_attempt"][
        "sha256"
    ]


def test_completion_seal_is_the_last_fallible_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_factory, _api = _backend_factory(_frame_payload())
    _patch_eligible_runtime(monkeypatch, backend_factory, tmp_path / "evidence")
    sealed = False
    original_write = capture._write_canonical_with_sidecar_exclusive
    original_progress = capture._write_progress
    original_repository = capture._verify_capture_repository
    original_authorization = capture._verify_live_capture_authorization

    def write(path: Path, payload: bytes) -> None:
        nonlocal sealed
        assert not sealed
        original_write(path, payload)
        if path.name == "source-completion-seal.json":
            sealed = True

    def progress(*args: object, **kwargs: object) -> None:
        assert not sealed
        original_progress(*args, **kwargs)  # type: ignore[arg-type]

    def repository(root: Path) -> capture._ProtocolBinding:
        assert not sealed
        return original_repository(root)

    def authorization(
        root: Path,
        binding: capture._ProtocolBinding,
    ) -> capture._LiveAuthorizationBinding:
        assert not sealed
        return original_authorization(root, binding)

    monkeypatch.setattr(capture, "_write_canonical_with_sidecar_exclusive", write)
    monkeypatch.setattr(capture, "_write_progress", progress)
    monkeypatch.setattr(capture, "_verify_capture_repository", repository)
    monkeypatch.setattr(capture, "_verify_live_capture_authorization", authorization)

    result = capture.run_passive_inventory_v3_capture_campaign(
        inputs=_inputs(),
        repository_root=_ROOT,
    )
    assert sealed
    assert capture._completion_commit_exists(result.campaign_directory)
    assert not (result.campaign_directory / "campaign-terminal-failure.json").exists()


def test_final_provenance_failure_prevents_completion_seal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_factory, _api = _backend_factory(_frame_payload())
    output = tmp_path / "evidence"
    _patch_eligible_runtime(monkeypatch, backend_factory, output)
    ready_to_seal = False
    original_progress = capture._write_progress
    original_authorization = capture._verify_live_capture_authorization

    def progress(*args: object, **kwargs: object) -> None:
        nonlocal ready_to_seal
        original_progress(*args, **kwargs)  # type: ignore[arg-type]
        if kwargs.get("status") == "ready-to-seal":
            ready_to_seal = True

    def authorization(
        root: Path,
        binding: capture._ProtocolBinding,
    ) -> capture._LiveAuthorizationBinding:
        if ready_to_seal:
            raise capture.PassiveInventoryV3CaptureError(
                "forced final authorization race"
            )
        return original_authorization(root, binding)

    monkeypatch.setattr(capture, "_write_progress", progress)
    monkeypatch.setattr(capture, "_verify_live_capture_authorization", authorization)
    with pytest.raises(
        capture.PassiveInventoryV3CaptureError,
        match="forced final authorization race",
    ):
        capture.run_passive_inventory_v3_capture_campaign(
            inputs=_inputs(),
            repository_root=_ROOT,
        )

    campaign = next(output.iterdir())
    assert not (campaign / "source-completion-seal.json").exists()
    terminal = campaign / "campaign-terminal-failure.json"
    _assert_sidecar(terminal)


def test_one_protocol_lock_can_reserve_only_one_host_campaign(
    tmp_path: Path,
) -> None:
    authorization = _authorization()
    reservation_root = tmp_path / "host-reservations"
    output_root = tmp_path / "evidence"
    first, session_id, reservation_sha = capture._allocate_campaign_directory(
        output_root,
        reservation_root=reservation_root,
        authorization=authorization,
        protocol=_binding(),
    )

    assert first == output_root / authorization.authorization_id
    assert session_id == f"inventory-v3-independent-{authorization.authorization_id}"
    assert len(reservation_sha) == 64
    first.rmdir()
    with pytest.raises(
        capture.PassiveInventoryV3CaptureError,
        match="already reserved or consumed on this host",
    ):
        capture._allocate_campaign_directory(
            output_root,
            reservation_root=reservation_root,
            authorization=authorization,
            protocol=_binding(),
        )
    with pytest.raises(
        capture.PassiveInventoryV3CaptureError,
        match="already reserved or consumed on this host",
    ):
        capture._allocate_campaign_directory(
            tmp_path / "second-worktree-evidence",
            reservation_root=reservation_root,
            authorization=replace(authorization, authorization_id="4" * 64),
            protocol=_binding(),
        )


def test_host_reservation_root_ignores_profile_environment_rebinding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_app_data = tmp_path / "os-known-local-app-data"
    local_app_data.mkdir()

    class Shell32:
        @staticmethod
        def SHGetFolderPathW(
            _owner: object,
            _folder: int,
            _token: object,
            _flags: int,
            output: object,
        ) -> int:
            output.value = str(local_app_data)  # type: ignore[attr-defined]
            return 0

    monkeypatch.setattr(capture.sys, "platform", "win32")
    monkeypatch.setattr(
        capture.ctypes,
        "WinDLL",
        lambda *_args, **_kwargs: Shell32(),
        raising=False,
    )
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "profile-a"))
    first = capture._approved_host_reservation_root()
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "profile-b"))
    monkeypatch.setenv("HOME", str(tmp_path / "home-b"))
    second = capture._approved_host_reservation_root()

    assert first == second
    assert first == (
        local_app_data
        / "Mining-Automation"
        / "inventory-positive-v3-independent-reservations"
    )


def test_terminal_record_survives_a_stuck_progress_temporary(
    tmp_path: Path,
) -> None:
    context = capture._CampaignProgressContext(
        campaign_directory=tmp_path,
        campaign_id="campaign-a",
        session_id="session-a",
        started_at_utc="2099-01-01T00:00:00Z",
        captures=[],
        owned_attempts=[],
        protocol=_binding(),
        authorization=_authorization(),
        host_reservation_sha256="3" * 64,
        inputs=_inputs(),
    )
    stuck = tmp_path / ".capture-progress.json.new"
    stuck.write_bytes(b"incomplete-owned-progress")

    capture._record_terminal_failure(context, RuntimeError("forced failure"))

    assert stuck.read_bytes() == b"incomplete-owned-progress"
    terminal = tmp_path / "campaign-terminal-failure.json"
    _assert_sidecar(terminal)
    decoded = json.loads(terminal.read_text(encoding="utf-8"))
    assert decoded["status"] == "failed-retained"
    assert decoded["failure"]["message"] == "forced failure"


def test_capture_inputs_reject_surrounding_whitespace() -> None:
    with pytest.raises(ValueError, match="surrounding whitespace"):
        capture.PassiveInventoryV3CaptureInputs(
            operator=" operator-a",
            runelite_build="runelite-build-a",
            client_mode="fixed",
            theme="dark",
            renderer="gpu",
        )
