from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

import mining_automation.perception.inventory.live_validation_session as live_validation_session
from mining_automation.capture.testing import ManualClock
from mining_automation.capture.windows import CapturedPixels, WindowInfo, WindowsCaptureBackend
from mining_automation.capture.windows.testing import FakeWin32Api
from mining_automation.perception.inventory import (
    InventoryValidationSessionError,
    InventoryValidationSessionPaused,
    InventoryValidationSessionStatus,
    load_inventory_validation_session,
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


def _paused_session(
    tmp_path: Path,
    *,
    cases: tuple[InventoryValidationCase, ...],
) -> Path:
    def pause_immediately(*_: object) -> None:
        raise KeyboardInterrupt

    with pytest.raises(InventoryValidationSessionPaused) as raised:
        run_inventory_validation_session(
            backend_factory=_backend,
            output_root=tmp_path / "sessions",
            provenance=_provenance(),
            cases=cases,
            ready_callback=pause_immediately,
            utc_clock=_utc,
        )
    return raised.value.session_directory


def _set_case_status(
    session_directory: Path,
    *,
    order: int,
    status: InventoryValidationSessionStatus,
) -> None:
    report_path = session_directory / "session-report.json"
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["cases"][order - 1]["status"] = status.value
    report_path.write_text(json.dumps(payload), encoding="utf-8")


def _forbidden_backend() -> WindowsCaptureBackend:
    raise AssertionError("resume must not recapture while orphan evidence is unresolved")


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


def test_resume_rejects_foreign_provenance_orphan_without_recapture(
    tmp_path: Path,
) -> None:
    session_directory = _paused_session(
        tmp_path,
        cases=(InventoryValidationCase.EMPTY_REFERENCE,),
    )
    _set_case_status(
        session_directory,
        order=1,
        status=InventoryValidationSessionStatus.CAPTURING,
    )
    foreign = run_inventory_live_validation(
        backend=_backend(),
        case=InventoryValidationCase.EMPTY_REFERENCE,
        output_root=session_directory / "captures",
        provenance=InventoryValidationProvenance(capture_build="foreign-build"),
        capture_clock=ManualClock(2.0),
        utc_clock=_utc,
    )
    report_before = foreign.report_path.read_bytes()

    with pytest.raises(
        InventoryValidationSessionError,
        match="capture provenance differs from the durable session provenance",
    ):
        run_inventory_validation_session(
            backend_factory=_forbidden_backend,
            output_root=session_directory.parent,
            provenance=_provenance(),
            cases=(InventoryValidationCase.EMPTY_REFERENCE,),
            resume_directory=session_directory,
            utc_clock=_utc,
        )

    assert foreign.report_path.read_bytes() == report_before
    paused = load_inventory_validation_session(session_directory)
    assert paused.records[0].status is InventoryValidationSessionStatus.CAPTURING
    assert not paused.captured_records


def test_resume_rejects_foreign_provenance_in_referenced_capture(
    tmp_path: Path,
) -> None:
    report = _completed_session(tmp_path)
    relative_report_path = report.captured_records[0].report_path
    assert relative_report_path is not None
    capture_report_path = report.session_directory / relative_report_path
    capture_report = json.loads(capture_report_path.read_text(encoding="utf-8"))
    capture_report["provenance"]["capture_build"] = "foreign-build"
    capture_report_path.write_text(json.dumps(capture_report), encoding="utf-8")

    with pytest.raises(
        InventoryValidationSessionError,
        match="capture provenance differs from the durable session provenance",
    ):
        run_inventory_validation_session(
            backend_factory=_forbidden_backend,
            output_root=report.session_directory.parent,
            provenance=_provenance(),
            cases=(InventoryValidationCase.EMPTY_REFERENCE,),
            resume_directory=report.session_directory,
            utc_clock=_utc,
        )


def test_resume_rejects_later_pending_orphan_without_overwrite_or_recapture(
    tmp_path: Path,
) -> None:
    cases = (
        InventoryValidationCase.EMPTY_REFERENCE,
        InventoryValidationCase.PARTIAL,
    )
    session_directory = _paused_session(tmp_path, cases=cases)
    later = run_inventory_live_validation(
        backend=_backend(),
        case=InventoryValidationCase.PARTIAL,
        output_root=session_directory / "captures",
        provenance=_provenance(),
        capture_clock=ManualClock(2.0),
        utc_clock=_utc,
    )
    report_before = later.report_path.read_bytes()

    with pytest.raises(
        InventoryValidationSessionError,
        match="unreferenced completed capture evidence.*durably CAPTURING",
    ):
        run_inventory_validation_session(
            backend_factory=_forbidden_backend,
            output_root=session_directory.parent,
            provenance=_provenance(),
            cases=cases,
            resume_directory=session_directory,
            utc_clock=_utc,
        )

    assert later.report_path.read_bytes() == report_before
    paused = load_inventory_validation_session(session_directory)
    assert all(
        record.status is InventoryValidationSessionStatus.PENDING
        for record in paused.records
    )


def test_resume_rejects_later_orphan_while_current_case_is_capturing(
    tmp_path: Path,
) -> None:
    cases = (
        InventoryValidationCase.EMPTY_REFERENCE,
        InventoryValidationCase.PARTIAL,
    )
    session_directory = _paused_session(tmp_path, cases=cases)
    _set_case_status(
        session_directory,
        order=1,
        status=InventoryValidationSessionStatus.CAPTURING,
    )
    later = run_inventory_live_validation(
        backend=_backend(),
        case=InventoryValidationCase.PARTIAL,
        output_root=session_directory / "captures",
        provenance=_provenance(),
        capture_clock=ManualClock(2.0),
        utc_clock=_utc,
    )
    report_before = later.report_path.read_bytes()

    with pytest.raises(
        InventoryValidationSessionError,
        match="does not match the current CAPTURING case",
    ):
        run_inventory_validation_session(
            backend_factory=_forbidden_backend,
            output_root=session_directory.parent,
            provenance=_provenance(),
            cases=cases,
            resume_directory=session_directory,
            utc_clock=_utc,
        )

    assert later.report_path.read_bytes() == report_before
    paused = load_inventory_validation_session(session_directory)
    assert paused.records[0].status is InventoryValidationSessionStatus.CAPTURING
    assert paused.records[1].status is InventoryValidationSessionStatus.PENDING


def test_resume_adopts_only_current_capturing_crash_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_capture = live_validation_session.run_inventory_live_validation

    def capture_then_interrupt(**kwargs: object) -> None:
        real_capture(**kwargs)  # type: ignore[arg-type]
        raise KeyboardInterrupt

    monkeypatch.setattr(
        live_validation_session,
        "run_inventory_live_validation",
        capture_then_interrupt,
    )
    with pytest.raises(InventoryValidationSessionPaused) as raised:
        run_inventory_validation_session(
            backend_factory=_backend,
            output_root=tmp_path / "sessions",
            provenance=_provenance(),
            cases=(InventoryValidationCase.EMPTY_REFERENCE,),
            capture_clock=ManualClock(3.0),
            utc_clock=_utc,
        )
    session_directory = raised.value.session_directory
    paused = load_inventory_validation_session(session_directory)
    assert paused.records[0].status is InventoryValidationSessionStatus.CAPTURING
    orphan_report = next((session_directory / "captures").glob("*/report.json"))
    orphan_before = orphan_report.read_bytes()

    monkeypatch.setattr(
        live_validation_session,
        "run_inventory_live_validation",
        real_capture,
    )
    resumed = run_inventory_validation_session(
        backend_factory=_forbidden_backend,
        output_root=session_directory.parent,
        provenance=_provenance(),
        cases=(InventoryValidationCase.EMPTY_REFERENCE,),
        resume_directory=session_directory,
        utc_clock=_utc,
    )

    assert resumed.complete
    assert resumed.records[0].capture_id == orphan_report.parent.name
    assert orphan_report.read_bytes() == orphan_before
    assert len(tuple((session_directory / "captures").glob("*/report.json"))) == 1


def test_resume_rejects_multiple_current_case_orphans_without_recapture(
    tmp_path: Path,
) -> None:
    session_directory = _paused_session(
        tmp_path,
        cases=(InventoryValidationCase.EMPTY_REFERENCE,),
    )
    _set_case_status(
        session_directory,
        order=1,
        status=InventoryValidationSessionStatus.CAPTURING,
    )
    first = run_inventory_live_validation(
        backend=_backend(),
        case=InventoryValidationCase.EMPTY_REFERENCE,
        output_root=session_directory / "captures",
        provenance=_provenance(),
        capture_clock=ManualClock(4.0),
        utc_clock=_utc,
    )
    second = run_inventory_live_validation(
        backend=_backend(),
        case=InventoryValidationCase.EMPTY_REFERENCE,
        output_root=session_directory / "captures",
        provenance=_provenance(),
        capture_clock=ManualClock(5.0),
        utc_clock=_utc,
    )
    evidence_before = {
        first.report_path: first.report_path.read_bytes(),
        second.report_path: second.report_path.read_bytes(),
    }

    with pytest.raises(
        InventoryValidationSessionError,
        match="multiple unassigned captures exist",
    ):
        run_inventory_validation_session(
            backend_factory=_forbidden_backend,
            output_root=session_directory.parent,
            provenance=_provenance(),
            cases=(InventoryValidationCase.EMPTY_REFERENCE,),
            resume_directory=session_directory,
            utc_clock=_utc,
        )

    assert {
        path: path.read_bytes() for path in evidence_before
    } == evidence_before
    paused = load_inventory_validation_session(session_directory)
    assert paused.records[0].status is InventoryValidationSessionStatus.CAPTURING
    assert not paused.captured_records
    assert len(tuple((session_directory / "captures").glob("*/report.json"))) == 2


def test_resume_retries_current_capturing_case_once_when_no_evidence_exists(
    tmp_path: Path,
) -> None:
    session_directory = _paused_session(
        tmp_path,
        cases=(InventoryValidationCase.EMPTY_REFERENCE,),
    )
    _set_case_status(
        session_directory,
        order=1,
        status=InventoryValidationSessionStatus.CAPTURING,
    )
    backend_calls = 0

    def counting_backend() -> WindowsCaptureBackend:
        nonlocal backend_calls
        backend_calls += 1
        return _backend()

    resumed = run_inventory_validation_session(
        backend_factory=counting_backend,
        output_root=session_directory.parent,
        provenance=_provenance(),
        cases=(InventoryValidationCase.EMPTY_REFERENCE,),
        resume_directory=session_directory,
        capture_clock=ManualClock(6.0),
        utc_clock=_utc,
    )

    assert resumed.complete
    assert resumed.records[0].status is InventoryValidationSessionStatus.CAPTURED
    assert backend_calls == 1
    assert len(tuple((session_directory / "captures").glob("*/report.json"))) == 1


def test_resume_rejects_invalid_status_order_before_capture(tmp_path: Path) -> None:
    cases = (
        InventoryValidationCase.EMPTY_REFERENCE,
        InventoryValidationCase.PARTIAL,
    )
    session_directory = _paused_session(tmp_path, cases=cases)
    _set_case_status(
        session_directory,
        order=2,
        status=InventoryValidationSessionStatus.CAPTURING,
    )

    with pytest.raises(ValueError, match="session status order"):
        run_inventory_validation_session(
            backend_factory=_forbidden_backend,
            output_root=session_directory.parent,
            provenance=_provenance(),
            cases=cases,
            resume_directory=session_directory,
            utc_clock=_utc,
        )
