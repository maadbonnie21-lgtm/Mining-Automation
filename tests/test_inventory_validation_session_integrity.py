from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mining_automation.capture.testing import ManualClock
from mining_automation.capture.windows import CapturedPixels, WindowInfo, WindowsCaptureBackend
from mining_automation.capture.windows.testing import FakeWin32Api
from mining_automation.perception.inventory import (
    InventoryValidationSessionError,
    run_inventory_validation_session,
)
from mining_automation.perception.inventory.live_validation import (
    InventoryValidationCase,
    InventoryValidationProvenance,
    run_inventory_live_validation,
)

_WINDOW_HANDLE = 412
_WIDTH = 12
_HEIGHT = 10
_PAYLOAD = bytes((20, 30, 40, 255)) * (_WIDTH * _HEIGHT)


def _utc() -> datetime:
    return datetime(2026, 8, 23, 18, 0, tzinfo=UTC)


def _provenance() -> InventoryValidationProvenance:
    return InventoryValidationProvenance(capture_build="issue-23-integrity")


def _backend() -> WindowsCaptureBackend:
    api = FakeWin32Api(
        windows=[
            WindowInfo(
                hwnd=_WINDOW_HANDLE,
                title="RuneLite - integrity test",
                class_name="SunAwtFrame",
                is_visible=True,
                is_minimized=False,
                client_width=_WIDTH,
                client_height=_HEIGHT,
            )
        ],
        captures={
            _WINDOW_HANDLE: CapturedPixels(
                payload=_PAYLOAD,
                width=_WIDTH,
                height=_HEIGHT,
            )
        },
        dpi_by_hwnd={_WINDOW_HANDLE: 96},
    )
    return WindowsCaptureBackend(win32_api=api)


def _completed_session(tmp_path: Path):  # type: ignore[no-untyped-def]
    return run_inventory_validation_session(
        backend_factory=_backend,
        output_root=tmp_path / "sessions",
        provenance=_provenance(),
        cases=(InventoryValidationCase.EMPTY_REFERENCE,),
        capture_clock=ManualClock(1.0),
        utc_clock=_utc,
    )


def test_resume_rejects_manifest_metadata_tampering(tmp_path: Path) -> None:
    report = _completed_session(tmp_path)
    payload = json.loads(report.report_path.read_text(encoding="utf-8"))
    payload["cases"][0]["capture"]["report_sha256"] = "0" * 64
    report.report_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(InventoryValidationSessionError, match="metadata disagrees"):
        run_inventory_validation_session(
            backend_factory=_backend,
            output_root=report.session_directory.parent,
            provenance=_provenance(),
            cases=(InventoryValidationCase.EMPTY_REFERENCE,),
            resume_directory=report.session_directory,
            utc_clock=_utc,
        )


def test_resume_rejects_extra_completed_capture_for_finished_case(tmp_path: Path) -> None:
    report = _completed_session(tmp_path)
    run_inventory_live_validation(
        backend=_backend(),
        case=InventoryValidationCase.EMPTY_REFERENCE,
        output_root=report.session_directory / "captures",
        provenance=_provenance(),
        capture_clock=ManualClock(2.0),
        utc_clock=_utc,
    )

    with pytest.raises(InventoryValidationSessionError, match="unreferenced completed"):
        run_inventory_validation_session(
            backend_factory=_backend,
            output_root=report.session_directory.parent,
            provenance=_provenance(),
            cases=(InventoryValidationCase.EMPTY_REFERENCE,),
            resume_directory=report.session_directory,
            utc_clock=_utc,
        )
