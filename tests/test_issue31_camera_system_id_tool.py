from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from mining_automation.validation.camera_system_id import (
    CameraSystemIdAxis,
    CameraSystemIdAxisResult,
    CameraSystemIdResult,
)


def _load_tool() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "tools" / "validate_varrock_east_camera.py"
    spec = importlib.util.spec_from_file_location("validate_varrock_east_camera_sid", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def tool() -> ModuleType:
    return _load_tool()


class _Backend:
    constructed = 0

    def __init__(self, **_kwargs: object) -> None:
        type(self).constructed += 1
        self.selected_window = SimpleNamespace(
            hwnd=123,
            class_name="SunAwtFrame",
            title="RuneLite - Chief Luma",
        )


class _Source:
    last: _Source | None = None

    def __init__(self, _backend: object, **_kwargs: object) -> None:
        type(self).last = self
        self.opened = False
        self.closed = False

    def open(self) -> None:
        self.opened = True

    def capture(self) -> object:
        return object()

    def close(self) -> None:
        self.closed = True
        _Lease.events.append("capture_cleanup")


class _Control:
    last: _Control | None = None

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        type(self).last = self
        self.released = False

    def release_all_held_keys(self) -> None:
        self.released = True
        _Lease.events.append("input_cleanup")


class _Lease:
    events: list[str] = []

    def __init__(self) -> None:
        self.acquired = False

    def __enter__(self) -> _Lease:
        self.acquired = True
        type(self).events.append("lease_acquired")
        return self

    def __exit__(self, *_args: object) -> None:
        self.acquired = False
        type(self).events.append("lease_released")


def _inconclusive_result() -> CameraSystemIdResult:
    horizontal = CameraSystemIdAxisResult(
        axis=CameraSystemIdAxis.HORIZONTAL,
        baseline_one=None,
        baseline_two=None,
        baseline_guard=None,
        positive_step=None,
        return_step=None,
        comparison=None,
        detail="test stopped before input",
    )
    return CameraSystemIdResult(
        horizontal=horizontal,
        vertical=None,
        conclusion=None,
        detail="test inconclusive",
    )


@pytest.mark.parametrize(
    "override",
    [
        ["--allow-dirty"],
        ["--dry-run"],
        ["--title", "forged"],
        ["--settle", "0.01"],
        ["--axis", "vertical"],
        ["--direction", "negative"],
        ["--pixels", "8"],
        ["--yaw-drag-pixels", "4"],
        ["--pitch-drag-pixels", "4"],
    ],
)
def test_fixed_system_id_parser_rejects_every_control_override(
    tool: ModuleType,
    override: list[str],
) -> None:
    _Backend.constructed = 0
    with pytest.raises(SystemExit) as raised:
        tool.main(
            [
                "fixed-aba-probe-v2",
                "--case-prefix",
                "parser-refusal",
                *override,
            ]
        )
    assert raised.value.code == 2
    assert _Backend.constructed == 0


def test_fixed_system_id_lease_spans_cleanup_and_canonical_publication(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "private"
    _Backend.constructed = 0
    _Source.last = None
    _Control.last = None
    _Lease.events = []
    monkeypatch.setattr(tool, "_resolve_private_output_root", lambda _path: output)
    monkeypatch.setattr(tool, "_git_state", lambda: ("a" * 40, True))
    monkeypatch.setattr(tool, "WindowsCaptureBackend", _Backend)
    monkeypatch.setattr(tool, "CaptureSource", _Source)
    monkeypatch.setattr(tool, "WindowsCameraControl", _Control)
    monkeypatch.setattr(
        tool,
        "_EXPECTED_WINDOWS_CAMERA_ADAPTER",
        f"{_Control.__module__}.{_Control.__qualname__}",
    )
    monkeypatch.setattr(tool, "WindowsCameraInputLease", _Lease)
    def run(*_args: object, **_kwargs: object) -> CameraSystemIdResult:
        _Lease.events.append("runner")
        return _inconclusive_result()

    def revalidate(_result: CameraSystemIdResult) -> None:
        _Lease.events.append("revalidate")

    original_write = tool.write_camera_validation_report

    def write(*args: object, **kwargs: object) -> object:
        _Lease.events.append("publish")
        return original_write(*args, **kwargs)

    monkeypatch.setattr(tool, "run_fixed_camera_system_identification", run)
    monkeypatch.setattr(tool, "_require_fixed_system_id_result_identities", revalidate)
    monkeypatch.setattr(tool, "write_camera_validation_report", write)
    command = [
        "fixed-aba-probe-v2",
        "--output",
        str(output),
        "--case-prefix",
        "system-id-integration",
    ]

    assert tool.main(command) == 1

    assert _Lease.events == [
        "lease_acquired",
        "runner",
        "input_cleanup",
        "capture_cleanup",
        "revalidate",
        "publish",
        "lease_released",
    ]
    assert _Source.last is not None and _Source.last.closed
    assert _Control.last is not None and _Control.last.released
    report = output / "reports" / "system-id-integration.camera.json"
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["provenance"]["git_head_sha"] == "a" * 40
    assert payload["provenance"]["plan_id"] == (
        "issue31-fixed-camera-system-identification"
    )
    assert payload["provenance"]["command_argv"][-2:] == [
        "--case-prefix",
        "system-id-integration",
    ]
    assert payload["evidence"]["pointer_mapping"]["adapter_identity"] == (
        f"{_Control.__module__}.{_Control.__qualname__}"
    )
    assert payload["evidence"]["fixed_policy"] == {
        "caller_selectable_axis": False,
        "caller_selectable_coordinate": False,
        "caller_selectable_direction": False,
        "caller_selectable_magnitude": False,
        "drag_point": [200, 600],
        "logical_pixels": 4,
        "maximum_physical_primitives": 4,
        "order": [
            "horizontal_positive",
            "horizontal_return",
            "vertical_positive_if_horizontal_usable",
            "vertical_return_if_horizontal_usable",
        ],
        "post_action_settle_s": 1.0,
    }
    digest = report.with_name(f"{report.name}.sha256").read_text().strip()
    assert len(digest) == 64
    assert digest == hashlib.sha256(report.read_bytes()).hexdigest()


def test_dirty_head_stops_before_lease_capture_or_input(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "private"
    _Backend.constructed = 0
    _Lease.events = []
    monkeypatch.setattr(tool, "_resolve_private_output_root", lambda _path: output)
    monkeypatch.setattr(tool, "_git_state", lambda: ("a" * 40, False))
    monkeypatch.setattr(tool, "WindowsCaptureBackend", _Backend)
    monkeypatch.setattr(tool, "WindowsCameraInputLease", _Lease)

    result = tool.main(
        [
            "fixed-aba-probe-v2",
            "--output",
            str(output),
            "--case-prefix",
            "dirty-refusal",
        ]
    )

    assert result == 2
    assert _Backend.constructed == 0
    assert _Lease.events == []


def test_canonical_revalidation_rejects_guidance_not_bound_to_exact_payload(
    tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = SimpleNamespace(frame_id=1, captured_monotonic_s=1.0)
    readiness = SimpleNamespace(safe_to_attempt_camera_input=False)
    guidance = SimpleNamespace(
        selector_id=tool.CAMERA_GUIDANCE_ID,
        selector_version=tool.CAMERA_GUIDANCE_VERSION,
        analysis=object(),
        can_accept=False,
        can_validate_scene=False,
        can_expose_resources=False,
    )
    evidence = SimpleNamespace(
        artifact=SimpleNamespace(raw_sha256="a" * 64),
        readiness=readiness,
        production=None,
    )
    observation = SimpleNamespace(
        frame=frame,
        evidence=evidence,
        guidance=guidance,
    )
    axis = SimpleNamespace(
        axis=CameraSystemIdAxis.HORIZONTAL,
        baseline_one=observation,
        baseline_two=None,
        positive_step=None,
        return_step=None,
    )
    result = SimpleNamespace(horizontal=axis, vertical=None)
    monkeypatch.setattr(tool, "evaluate_client_input_readiness", lambda _frame: readiness)
    monkeypatch.setattr(
        tool,
        "evaluate_varrock_east_camera",
        lambda _frame: pytest.fail("production must not run when readiness vetoes"),
    )
    monkeypatch.setattr(
        tool,
        "evaluate_varrock_east_camera_guidance",
        lambda _frame: SimpleNamespace(selector_id="different"),
    )

    with pytest.raises(RuntimeError, match="guidance does not bind"):
        tool._require_fixed_system_id_result_identities(result)


def test_lease_release_failure_retracts_only_this_invocations_report(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "private"
    unrelated = output / "reports" / "unrelated.camera.json"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("keep", encoding="utf-8")

    class FailingReleaseLease(_Lease):
        def __exit__(self, *_args: object) -> None:
            type(self).events.append("lease_release_failed")
            raise tool.CameraInputLeaseError("release failed")

    FailingReleaseLease.events = []
    monkeypatch.setattr(tool, "_resolve_private_output_root", lambda _path: output)
    monkeypatch.setattr(tool, "_git_state", lambda: ("a" * 40, True))
    monkeypatch.setattr(tool, "WindowsCaptureBackend", _Backend)
    monkeypatch.setattr(tool, "CaptureSource", _Source)
    monkeypatch.setattr(tool, "WindowsCameraControl", _Control)
    monkeypatch.setattr(
        tool,
        "_EXPECTED_WINDOWS_CAMERA_ADAPTER",
        f"{_Control.__module__}.{_Control.__qualname__}",
    )
    monkeypatch.setattr(tool, "WindowsCameraInputLease", FailingReleaseLease)
    monkeypatch.setattr(
        tool,
        "run_fixed_camera_system_identification",
        lambda *_args, **_kwargs: _inconclusive_result(),
    )
    monkeypatch.setattr(
        tool,
        "_require_fixed_system_id_result_identities",
        lambda _result: None,
    )

    result = tool.main(
        [
            "fixed-aba-probe-v2",
            "--output",
            str(output),
            "--case-prefix",
            "release-failure",
        ]
    )

    report = output / "reports" / "release-failure.camera.json"
    assert result == 2
    assert not report.exists()
    assert not report.with_name(f"{report.name}.sha256").exists()
    assert unrelated.read_text(encoding="utf-8") == "keep"
