from __future__ import annotations

import ast
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.capture.testing import ManualClock
from mining_automation.capture.windows import CapturedPixels, WindowInfo, WindowsCaptureBackend
from mining_automation.capture.windows.testing import FakeWin32Api
from mining_automation.perception.inventory import (
    InventoryDetector,
    InventoryFrameProfile,
    InventoryGridLayout,
    extract_capture_bmp,
    inventory_detector_from_profile,
    live_validation,
    live_validation_cli,
)
from mining_automation.perception.inventory.live_validation import (
    InventoryValidationCase,
    InventoryValidationProvenance,
    run_inventory_live_validation,
)
from mining_automation.perception.inventory.localization import InventoryLocalization

_ROOT = Path(__file__).resolve().parents[1]
_TOOL = _ROOT / "tools" / "validate_inventory_live.py"
_WINDOW_HANDLE = 73
_LAYOUT = InventoryGridLayout(
    profile_id="synthetic-live-validation",
    column_stride=36,
    row_stride=36,
)
_REGION = _LAYOUT.region_at(2, 2)
_FRAME_WIDTH = _REGION.x + _REGION.width + 2
_FRAME_HEIGHT = _REGION.y + _REGION.height + 2
_PIXEL = bytes((24, 27, 31, 255))
_EMPTY_PAYLOAD = _PIXEL * (_FRAME_WIDTH * _FRAME_HEIGHT)


def _fixed_utc() -> datetime:
    return datetime(2026, 8, 23, 12, 34, 56, tzinfo=UTC)


def _window(*, width: int = _FRAME_WIDTH, height: int = _FRAME_HEIGHT) -> WindowInfo:
    return WindowInfo(
        hwnd=_WINDOW_HANDLE,
        title="RuneLite - private title",
        class_name="SunAwtFrame",
        is_visible=True,
        is_minimized=False,
        client_width=width,
        client_height=height,
    )


def _backend(
    *,
    payload: bytes = _EMPTY_PAYLOAD,
    width: int = _FRAME_WIDTH,
    height: int = _FRAME_HEIGHT,
    api: FakeWin32Api | None = None,
) -> tuple[WindowsCaptureBackend, FakeWin32Api]:
    selected_api = api or FakeWin32Api(
        windows=[_window(width=width + 11, height=height + 17)],
        captures={
            _WINDOW_HANDLE: CapturedPixels(
                payload=payload,
                width=width,
                height=height,
            )
        },
        dpi_by_hwnd={_WINDOW_HANDLE: 144},
    )
    return WindowsCaptureBackend(win32_api=selected_api), selected_api


def _reference_frame(payload: bytes = _EMPTY_PAYLOAD) -> Frame:
    return Frame.from_raw(
        RawFrame(
            payload=payload,
            width=_FRAME_WIDTH,
            height=_FRAME_HEIGHT,
            pixel_format=PixelFormat.BGRA8888,
        ),
        frame_id=900,
        captured_monotonic_s=900.0,
    )


def _profile() -> InventoryFrameProfile:
    return InventoryFrameProfile(
        profile_id=_LAYOUT.profile_id,
        frame_width=_FRAME_WIDTH,
        frame_height=_FRAME_HEIGHT,
        region=_REGION,
        layout=_LAYOUT,
    )


def _detector() -> InventoryDetector:
    return inventory_detector_from_profile(_profile(), _reference_frame())


def _run(
    tmp_path: Path,
    *,
    case: InventoryValidationCase = InventoryValidationCase.EMPTY_REFERENCE,
    detector: InventoryDetector | None = None,
    backend: WindowsCaptureBackend | None = None,
):
    selected_backend = backend or _backend()[0]
    return run_inventory_live_validation(
        backend=selected_backend,
        case=case,
        output_root=tmp_path / "diagnostics" / "inventory-live",
        provenance=InventoryValidationProvenance(
            capture_build="issue-19-test",
            runelite_build="synthetic-only",
            notes=("deterministic test capture",),
        ),
        detector=detector,
        capture_clock=ManualClock(12.5),
        utc_clock=_fixed_utc,
    )


def test_case_labels_are_closed_and_every_label_remains_unverified(tmp_path: Path) -> None:
    assert {case.value for case in InventoryValidationCase} == {
        "empty-reference",
        "empty-validation",
        "partial",
        "full",
        "wrong-tab",
        "obstructed",
        "hover-drag",
        "quantity-text",
    }

    for case in InventoryValidationCase:
        report = _run(tmp_path, case=case)
        payload = report.as_dict()
        assert payload["operator_case"] == {
            "label": case.value,
            "truth_status": "operator-selected-unverified",
        }
        assert payload["review_status"] == "unreviewed"
        assert "passed" not in report.to_json()


@pytest.mark.parametrize(
    "invalid",
    ["", "FULL", " full", "full ", "../full", "empty/reference", "CON"],
)
def test_invalid_case_labels_are_rejected_before_capture_or_filesystem_write(
    tmp_path: Path,
    invalid: str,
) -> None:
    backend, api = _backend()
    output_root = tmp_path / "must-not-exist"

    with pytest.raises(TypeError, match="InventoryValidationCase"):
        run_inventory_live_validation(
            backend=backend,
            case=invalid,  # type: ignore[arg-type]
            output_root=output_root,
            provenance=InventoryValidationProvenance(),
        )

    assert api.capture_calls == []
    assert not output_root.exists()


def test_unique_directories_are_atomic_and_preserve_existing_evidence(tmp_path: Path) -> None:
    output_root = tmp_path / "diagnostics" / "inventory-live"
    occupied_name = "20260823T123456.000000Z-empty-reference"
    occupied = output_root / occupied_name
    occupied.mkdir(parents=True)
    sentinel = occupied / "frame.bgra"
    sentinel.write_bytes(b"existing evidence")

    def capture_one(_: int) -> Path:
        return _run(tmp_path).run_directory

    with ThreadPoolExecutor(max_workers=4) as executor:
        directories = tuple(executor.map(capture_one, range(6)))

    assert len(set(directories)) == 6
    assert occupied not in directories
    assert all(path.parent == output_root for path in directories)
    assert sentinel.read_bytes() == b"existing evidence"


def test_unusable_output_root_is_rejected_before_capture(tmp_path: Path) -> None:
    output_root = tmp_path / "not-a-directory"
    output_root.write_bytes(b"preserve me")
    backend, api = _backend()

    with pytest.raises(FileExistsError):
        run_inventory_live_validation(
            backend=backend,
            case=InventoryValidationCase.EMPTY_REFERENCE,
            output_root=output_root,
            provenance=InventoryValidationProvenance(),
            capture_clock=ManualClock(12.5),
            utc_clock=_fixed_utc,
        )

    assert api.capture_calls == []
    assert output_root.read_bytes() == b"preserve me"


def test_capture_only_writes_owned_raw_bmp_draft_and_deterministic_report(
    tmp_path: Path,
) -> None:
    backend, api = _backend()

    report = _run(tmp_path, backend=backend)

    assert api.capture_calls == [_WINDOW_HANDLE]
    assert backend.selected_window is None
    assert report.detector.mode == "capture-only"
    assert report.detector.status == "profile-not-configured"
    assert report.detector.occupied_slots is None
    assert report.detector.confidence is None
    assert report.detector.profile_id is None
    assert report.detector.configuration_id is None
    assert report.exit_code == 0
    assert report.frame.width == _FRAME_WIDTH
    assert report.frame.height == _FRAME_HEIGHT
    assert report.as_dict()["capture"]["width"] == _FRAME_WIDTH  # type: ignore[index]
    assert (report.run_directory / "frame.bgra").read_bytes() == _EMPTY_PAYLOAD
    decoded = extract_capture_bmp((report.run_directory / "frame.bmp").read_bytes())
    assert (decoded.width, decoded.height, decoded.payload) == (
        _FRAME_WIDTH,
        _FRAME_HEIGHT,
        _EMPTY_PAYLOAD,
    )

    draft = json.loads((report.run_directory / "replay-case.draft.json").read_text())
    assert draft["review_status"] == "unreviewed"
    assert "expected_observations" not in draft
    serialized = report.to_json()
    assert report.report_path.read_text(encoding="utf-8") == serialized
    assert serialized.endswith("\n")
    assert str(tmp_path) not in serialized
    assert list(json.loads(serialized)) == [
        "artifacts",
        "capture",
        "capture_id",
        "created_at_utc",
        "detector",
        "operator_case",
        "provenance",
        "report_kind",
        "review_status",
        "schema_version",
    ]
    artifacts = json.loads(serialized)["artifacts"]
    assert artifacts["raw"]["path"] == "frame.bgra"
    assert artifacts["bmp"]["path"] == "frame.bmp"
    assert artifacts["draft"]["path"] == "replay-case.draft.json"
    assert artifacts["raw"]["sha256"] == hashlib.sha256(_EMPTY_PAYLOAD).hexdigest()
    assert report.as_dict()["capture"]["reported_dpi"] == 144  # type: ignore[index]

    equivalent = _run(tmp_path, backend=_backend()[0])
    normalized = replace(
        equivalent,
        run_directory=report.run_directory,
        capture_id=report.capture_id,
    )
    assert normalized is not report
    assert normalized.to_json() == serialized


def test_bmp_publication_refuses_racing_file_without_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_encoder = live_validation.write_bgra_bmp
    sentinel = b"racing evidence"

    def racing_encoder(
        path: Path,
        *,
        width: int,
        height: int,
        bgra_payload: bytes,
    ) -> None:
        (path.parent.parent / "frame.bmp").write_bytes(sentinel)
        original_encoder(
            path,
            width=width,
            height=height,
            bgra_payload=bgra_payload,
        )

    monkeypatch.setattr(live_validation, "write_bgra_bmp", racing_encoder)

    with pytest.raises(FileExistsError):
        _run(tmp_path)

    run_directories = tuple((tmp_path / "diagnostics" / "inventory-live").iterdir())
    assert len(run_directories) == 1
    assert (run_directories[0] / "frame.bmp").read_bytes() == sentinel


class _MutatingDpiApi(FakeWin32Api):
    def __init__(self, payload: bytearray) -> None:
        self.mutable_payload = payload
        super().__init__(
            windows=[_window()],
            captures={
                _WINDOW_HANDLE: CapturedPixels(
                    payload=cast(bytes, self.mutable_payload),
                    width=_FRAME_WIDTH,
                    height=_FRAME_HEIGHT,
                )
            },
        )

    def get_dpi_for_window(self, hwnd: int) -> int:
        assert hwnd == _WINDOW_HANDLE
        self.mutable_payload[:] = bytes([0xFF]) * len(self.mutable_payload)
        return 120


def test_owned_frame_survives_backend_buffer_reuse_for_artifacts_and_detector(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mutable_payload = bytearray(_EMPTY_PAYLOAD)
    api = _MutatingDpiApi(mutable_payload)
    backend = WindowsCaptureBackend(win32_api=api)
    seen_frames: list[Frame] = []
    original_runner = live_validation.run_detector

    def observing_runner(*args: object, **kwargs: object):
        seen_frames.append(cast(Frame, args[1]))
        return original_runner(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(live_validation, "run_detector", observing_runner)

    report = _run(tmp_path, backend=backend, detector=_detector())

    assert mutable_payload != _EMPTY_PAYLOAD
    assert report.frame.payload == _EMPTY_PAYLOAD
    assert (report.run_directory / "frame.bgra").read_bytes() == _EMPTY_PAYLOAD
    decoded = extract_capture_bmp((report.run_directory / "frame.bmp").read_bytes())
    assert decoded.payload == _EMPTY_PAYLOAD
    assert len(seen_frames) == 1
    assert seen_frames[0] is report.frame
    assert report.detector.occupied_slots == 0
    assert report.detector.label == "empty"


@pytest.mark.parametrize(
    "case",
    [InventoryValidationCase.FULL, InventoryValidationCase.WRONG_TAB],
)
def test_operator_label_does_not_change_detector_result(
    tmp_path: Path,
    case: InventoryValidationCase,
) -> None:
    report = _run(tmp_path, case=case, detector=_detector())

    assert report.detector.mode == "detector-run"
    assert report.detector.status == "observation-recorded"
    assert report.detector.label == "empty"
    assert report.detector.occupied_slots == 0
    assert report.detector.confidence is not None
    assert report.detector.confidence > 0.0
    assert report.detector.profile_id == _LAYOUT.profile_id
    assert report.detector.configured_profile_id == _LAYOUT.profile_id
    assert report.detector.configuration_id == report.detector.configured_configuration_id
    assert report.detector.configuration_id == _detector().configuration_id
    assert report.exit_code == 0


def test_configured_detector_fails_closed_on_unsupported_geometry(tmp_path: Path) -> None:
    width, height = 4, 2
    payload = bytes((5, 6, 7, 255)) * (width * height)
    backend, _ = _backend(payload=payload, width=width, height=height)

    report = _run(tmp_path, backend=backend, detector=_detector())

    assert report.detector.mode == "detector-run"
    assert report.detector.status == "observation-recorded"
    assert report.detector.label == "unknown"
    assert report.detector.occupied_slots is None
    assert report.detector.confidence == 0.0
    assert report.detector.profile_id is None
    assert report.detector.configured_profile_id == _LAYOUT.profile_id
    assert report.detector.configuration_id == report.detector.configured_configuration_id
    assert report.detector.reason is not None
    assert "inventory_region_not_localized" in report.detector.reason
    assert "profile-not-configured" not in report.to_json()
    assert report.exit_code == 0


def test_configured_detector_preserves_obstruction_as_unknown_zero_confidence(
    tmp_path: Path,
) -> None:
    payload = bytearray(_EMPTY_PAYLOAD)
    for y in range(_REGION.y, _REGION.y + _REGION.height):
        for x in range(_REGION.x, _REGION.x + _REGION.width):
            offset = (y * _FRAME_WIDTH + x) * 4
            payload[offset : offset + 4] = bytes((235, 235, 235, 255))
    backend, _ = _backend(payload=bytes(payload))

    report = _run(
        tmp_path,
        case=InventoryValidationCase.OBSTRUCTED,
        backend=backend,
        detector=_detector(),
    )

    assert report.detector.status == "observation-recorded"
    assert report.detector.label == "unknown"
    assert report.detector.occupied_slots is None
    assert report.detector.confidence == 0.0
    assert report.detector.reason is not None
    assert report.detector.reason.startswith("inventory_obstructed:")
    assert "passed" not in report.to_json()


class _DpiFailureApi(FakeWin32Api):
    def get_dpi_for_window(self, hwnd: int) -> int:
        raise OSError(f"DPI unavailable for {hwnd}")


def test_unavailable_dpi_is_reported_without_discarding_capture(tmp_path: Path) -> None:
    api = _DpiFailureApi(
        windows=[_window()],
        captures={
            _WINDOW_HANDLE: CapturedPixels(
                payload=_EMPTY_PAYLOAD,
                width=_FRAME_WIDTH,
                height=_FRAME_HEIGHT,
            )
        },
    )

    report = _run(tmp_path, backend=WindowsCaptureBackend(win32_api=api))

    assert report.reported_dpi is None
    assert report.metadata_warnings
    assert "reported DPI unavailable" in report.metadata_warnings[0]
    assert (report.run_directory / "frame.bgra").exists()


class _ExplodingLocator:
    configuration_id = "synthetic-exploding-locator"

    def locate(self, frame: Frame) -> InventoryLocalization:
        raise RuntimeError(f"synthetic locator failure on {frame.frame_id}")


def test_detector_failure_is_distinct_from_capture_only_and_retains_evidence(
    tmp_path: Path,
) -> None:
    baseline = _detector()
    failing = InventoryDetector(
        locator=_ExplodingLocator(),
        classifier=baseline.classifier,
    )

    report = _run(tmp_path, detector=failing)

    assert report.detector.mode == "detector-run"
    assert report.detector.status == "detector-error"
    assert report.detector.error_type == "DetectorExecutionError"
    assert report.detector.configured_profile_id == _LAYOUT.profile_id
    assert report.detector.configured_configuration_id == failing.configuration_id
    assert report.exit_code == 1
    assert (report.run_directory / "frame.bgra").read_bytes() == _EMPTY_PAYLOAD
    assert report.report_path.exists()


def test_cli_defaults_to_capture_only_and_prints_no_false_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    backend, api = _backend()
    monkeypatch.setattr(live_validation_cli, "WindowsCaptureBackend", lambda **_: backend)

    exit_code = live_validation_cli.main(
        [
            "--case",
            "empty-reference",
            "--output-root",
            str(tmp_path / "live"),
            "--capture-build",
            "test-head",
        ]
    )

    output = capsys.readouterr()
    assert exit_code == 0
    assert api.capture_calls == [_WINDOW_HANDLE]
    assert "capture-only / profile-not-configured" in output.out
    assert "NOT VALIDATED" in output.out
    assert "PASS" not in output.out
    assert output.err == ""


def test_cli_wires_reviewed_inventory_detector_to_same_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    backend, api = _backend()
    detector = _detector()
    monkeypatch.setattr(live_validation_cli, "WindowsCaptureBackend", lambda **_: backend)
    monkeypatch.setattr(live_validation_cli, "load_detector", lambda _: detector)

    exit_code = live_validation_cli.main(
        [
            "--case",
            "empty-validation",
            "--output-root",
            str(tmp_path / "live"),
            "--reviewed-detector",
            "approved_inventory:build_detector",
        ]
    )

    output = capsys.readouterr()
    assert exit_code == 0
    assert api.capture_calls == [_WINDOW_HANDLE]
    assert "detector-run / observation-recorded" in output.out
    assert detector.configuration_id in output.out
    report_path = next((tmp_path / "live").glob("*/report.json"))
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["detector"]["configured_profile_id"] == _LAYOUT.profile_id


def test_cli_rejects_non_inventory_detector_before_backend_or_capture(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    constructed = False

    def forbidden_backend(**_: object) -> WindowsCaptureBackend:
        nonlocal constructed
        constructed = True
        return _backend()[0]

    monkeypatch.setattr(live_validation_cli, "WindowsCaptureBackend", forbidden_backend)
    monkeypatch.setattr(live_validation_cli, "load_detector", lambda _: object())

    exit_code = live_validation_cli.main(
        ["--case", "empty-reference", "--reviewed-detector", "wrong:value"]
    )

    output = capsys.readouterr()
    assert exit_code == 2
    assert not constructed
    assert "production InventoryDetector" in output.err


def test_cli_rejects_blank_window_title_before_backend_construction(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    constructed = False

    def forbidden_backend(**_: object) -> WindowsCaptureBackend:
        nonlocal constructed
        constructed = True
        return _backend()[0]

    monkeypatch.setattr(live_validation_cli, "WindowsCaptureBackend", forbidden_backend)

    exit_code = live_validation_cli.main(["--case", "empty-reference", "--title", " "])

    output = capsys.readouterr()
    assert exit_code == 2
    assert not constructed
    assert "--title must be a non-empty" in output.err


def test_cli_rejects_noncanonical_label_before_backend_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructed = False

    def forbidden_backend(**_: object) -> WindowsCaptureBackend:
        nonlocal constructed
        constructed = True
        return _backend()[0]

    monkeypatch.setattr(live_validation_cli, "WindowsCaptureBackend", forbidden_backend)
    with pytest.raises(SystemExit) as raised:
        live_validation_cli.main(["--case", "../full"])

    assert raised.value.code == 2
    assert not constructed


def test_tool_is_only_a_thin_package_delegate() -> None:
    source = _TOOL.read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert not any(isinstance(node, (ast.FunctionDef, ast.ClassDef)) for node in tree.body)
    assert "mining_automation.perception.inventory.live_validation_cli" in source
    assert "WindowsCaptureBackend" not in source
    assert "run_inventory_live_validation" not in source
