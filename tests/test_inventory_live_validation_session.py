from __future__ import annotations

import ast
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.capture.testing import ManualClock
from mining_automation.capture.windows import CapturedPixels, WindowInfo, WindowsCaptureBackend
from mining_automation.capture.windows.testing import FakeWin32Api
from mining_automation.perception.inventory import (
    DEFAULT_INVENTORY_VALIDATION_CASES,
    InventoryDetector,
    InventoryFrameProfile,
    InventoryGridLayout,
    InventoryValidationSessionError,
    InventoryValidationSessionPaused,
    Region,
    inventory_detector_from_profile,
    live_validation_session_cli,
    load_inventory_validation_session,
    run_inventory_validation_session,
)
from mining_automation.perception.inventory.live_validation import (
    InventoryValidationCase,
    InventoryValidationProvenance,
)

_ROOT = Path(__file__).resolve().parents[1]
_GITIGNORE = _ROOT / ".gitignore"
_TOOL = _ROOT / "tools" / "validate_inventory_session.py"
_WINDOW_HANDLE = 91
_LAYOUT = InventoryGridLayout(
    profile_id="session-test-profile",
    column_stride=32,
    row_stride=36,
)
_REGION = Region(2, 2, _LAYOUT.width, _LAYOUT.height)
_FRAME_WIDTH = _REGION.x + _REGION.width + 2
_FRAME_HEIGHT = _REGION.y + _REGION.height + 2
_PIXEL = bytes((22, 25, 29, 255))
_EMPTY_PAYLOAD = _PIXEL * (_FRAME_WIDTH * _FRAME_HEIGHT)


def _fixed_utc() -> datetime:
    return datetime(2026, 8, 23, 14, 15, 16, tzinfo=UTC)


def _window(*, width: int = _FRAME_WIDTH, height: int = _FRAME_HEIGHT) -> WindowInfo:
    return WindowInfo(
        hwnd=_WINDOW_HANDLE,
        title="RuneLite - private session title",
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
) -> tuple[WindowsCaptureBackend, FakeWin32Api]:
    api = FakeWin32Api(
        windows=[_window(width=width + 7, height=height + 9)],
        captures={
            _WINDOW_HANDLE: CapturedPixels(
                payload=payload,
                width=width,
                height=height,
            )
        },
        dpi_by_hwnd={_WINDOW_HANDLE: 144},
    )
    return WindowsCaptureBackend(win32_api=api), api


class _BackendFactory:
    def __init__(
        self,
        payloads: tuple[bytes, ...],
        *,
        geometries: tuple[tuple[int, int], ...] | None = None,
    ) -> None:
        self.payloads = payloads
        self.geometries = geometries
        self.calls = 0
        self.apis: list[FakeWin32Api] = []

    def __call__(self) -> WindowsCaptureBackend:
        index = self.calls
        self.calls += 1
        payload = self.payloads[min(index, len(self.payloads) - 1)]
        geometry = (
            (_FRAME_WIDTH, _FRAME_HEIGHT)
            if self.geometries is None
            else self.geometries[min(index, len(self.geometries) - 1)]
        )
        backend, api = _backend(payload=payload, width=geometry[0], height=geometry[1])
        self.apis.append(api)
        return backend


@pytest.fixture
def provenance() -> InventoryValidationProvenance:
    return InventoryValidationProvenance(
        capture_build="issue-23-test",
        runelite_build="synthetic",
        notes=("guided session regression",),
    )


def _detector() -> InventoryDetector:
    profile = InventoryFrameProfile(
        profile_id=_LAYOUT.profile_id,
        frame_width=_FRAME_WIDTH,
        frame_height=_FRAME_HEIGHT,
        region=_REGION,
        layout=_LAYOUT,
    )
    reference = Frame.from_raw(
        RawFrame(
            payload=_EMPTY_PAYLOAD,
            width=_FRAME_WIDTH,
            height=_FRAME_HEIGHT,
            pixel_format=PixelFormat.BGRA8888,
        ),
        frame_id=400,
        captured_monotonic_s=400.0,
    )
    return inventory_detector_from_profile(profile, reference)


def test_default_plan_and_capture_only_session_contract(
    tmp_path: Path,
    provenance: InventoryValidationProvenance,
) -> None:
    assert DEFAULT_INVENTORY_VALIDATION_CASES == (
        InventoryValidationCase.EMPTY_REFERENCE,
        InventoryValidationCase.EMPTY_VALIDATION,
        InventoryValidationCase.PARTIAL,
        InventoryValidationCase.FULL,
        InventoryValidationCase.WRONG_TAB,
        InventoryValidationCase.OBSTRUCTED,
    )
    factory = _BackendFactory((_EMPTY_PAYLOAD,))
    seen: list[InventoryValidationCase] = []

    report = run_inventory_validation_session(
        backend_factory=factory,
        output_root=tmp_path / "sessions",
        provenance=provenance,
        ready_callback=lambda case, *_: seen.append(case),
        capture_clock=ManualClock(8.0),
        utc_clock=_fixed_utc,
    )

    assert report.complete
    assert report.exit_code == 0
    assert seen == list(DEFAULT_INVENTORY_VALIDATION_CASES)
    assert factory.calls == 6
    assert all(api.capture_calls == [_WINDOW_HANDLE] for api in factory.apis)
    assert len(tuple((report.session_directory / "captures").glob("*/report.json"))) == 6
    manifest = json.loads(report.report_path.read_text(encoding="utf-8"))
    assert manifest["summary"]["capture_only_cases"] == 6
    assert manifest["summary"]["complete"] is True
    assert all(
        item["operator_case"]["truth_status"] == "operator-selected-unverified"
        for item in manifest["cases"]
    )
    assert "passed" not in report.to_json().lower()
    draft = json.loads(report.profile_review_draft_path.read_text(encoding="utf-8"))
    assert draft["activation_allowed"] is False
    assert draft["approval"]["status"] == "unreviewed"
    assert draft["inventory_profile"]["inventory_region"] is None
    assert draft["inventory_profile"]["layout"]["slot_size"] == 32


def test_unique_ownership_pause_and_resume_do_not_recapture_completed_cases(
    tmp_path: Path,
    provenance: InventoryValidationProvenance,
) -> None:
    initial_factory = _BackendFactory((_EMPTY_PAYLOAD,))

    def pause_on_third(
        case: InventoryValidationCase,
        order: int,
        total: int,
        session_directory: Path,
    ) -> None:
        del case, total, session_directory
        if order == 3:
            raise KeyboardInterrupt

    with pytest.raises(InventoryValidationSessionPaused) as raised:
        run_inventory_validation_session(
            backend_factory=initial_factory,
            output_root=tmp_path / "sessions",
            provenance=provenance,
            ready_callback=pause_on_third,
            capture_clock=ManualClock(2.0),
            utc_clock=_fixed_utc,
        )
    session_directory = raised.value.session_directory
    paused = load_inventory_validation_session(session_directory)
    assert [item.case for item in paused.captured_records] == [
        InventoryValidationCase.EMPTY_REFERENCE,
        InventoryValidationCase.EMPTY_VALIDATION,
    ]
    assert initial_factory.calls == 2

    resumed_factory = _BackendFactory((_EMPTY_PAYLOAD,))
    resumed = run_inventory_validation_session(
        backend_factory=resumed_factory,
        output_root=session_directory.parent,
        provenance=provenance,
        cases=tuple(item.case for item in paused.records),
        resume_directory=session_directory,
        capture_clock=ManualClock(3.0),
        utc_clock=_fixed_utc,
    )
    assert resumed.complete
    assert resumed_factory.calls == 4
    assert len(tuple((session_directory / "captures").glob("*/report.json"))) == 6

    second = run_inventory_validation_session(
        backend_factory=_BackendFactory((_EMPTY_PAYLOAD,)),
        output_root=tmp_path / "sessions",
        provenance=provenance,
        cases=(InventoryValidationCase.EMPTY_REFERENCE,),
        utc_clock=_fixed_utc,
    )
    assert second.session_directory != session_directory


def test_cross_case_review_flags_geometry_and_reference_separation(
    tmp_path: Path,
    provenance: InventoryValidationProvenance,
) -> None:
    second_width = _FRAME_WIDTH + 1
    second_payload = _PIXEL * (second_width * _FRAME_HEIGHT)
    report = run_inventory_validation_session(
        backend_factory=_BackendFactory(
            (_EMPTY_PAYLOAD, second_payload),
            geometries=((_FRAME_WIDTH, _FRAME_HEIGHT), (second_width, _FRAME_HEIGHT)),
        ),
        output_root=tmp_path / "sessions",
        provenance=provenance,
        cases=(
            InventoryValidationCase.EMPTY_REFERENCE,
            InventoryValidationCase.EMPTY_VALIDATION,
        ),
        utc_clock=_fixed_utc,
    )
    assert "captured cases use inconsistent frame geometry" in report.blocking_reasons()
    draft = json.loads(report.profile_review_draft_path.read_text(encoding="utf-8"))
    assert draft["frame"] is None

    identical = run_inventory_validation_session(
        backend_factory=_BackendFactory((_EMPTY_PAYLOAD,)),
        output_root=tmp_path / "identical",
        provenance=provenance,
        cases=(
            InventoryValidationCase.EMPTY_REFERENCE,
            InventoryValidationCase.EMPTY_VALIDATION,
        ),
        utc_clock=_fixed_utc,
    )
    assert any("byte-identical" in reason for reason in identical.blocking_reasons())
    assert identical.records[0].capture_id != identical.records[1].capture_id


def test_reviewed_detector_keeps_identity_and_wrong_geometry_fails_closed(
    tmp_path: Path,
    provenance: InventoryValidationProvenance,
) -> None:
    detector = _detector()
    known = run_inventory_validation_session(
        backend_factory=_BackendFactory((_EMPTY_PAYLOAD,)),
        output_root=tmp_path / "known",
        provenance=provenance,
        cases=(InventoryValidationCase.EMPTY_VALIDATION,),
        detector=detector,
        utc_clock=_fixed_utc,
    ).records[0]
    assert known.detector_mode == "detector-run"
    assert known.detector_occupied_slots == 0
    assert known.detector_confidence is not None and known.detector_confidence > 0.0
    assert known.detector_profile_id == _LAYOUT.profile_id
    assert known.detector_configuration_id == detector.configuration_id

    width, height = 5, 3
    payload = bytes((1, 2, 3, 255)) * (width * height)
    unknown = run_inventory_validation_session(
        backend_factory=_BackendFactory((payload,), geometries=((width, height),)),
        output_root=tmp_path / "unknown",
        provenance=provenance,
        cases=(InventoryValidationCase.WRONG_TAB,),
        detector=detector,
        utc_clock=_fixed_utc,
    ).records[0]
    assert unknown.detector_occupied_slots is None
    assert unknown.detector_confidence == 0.0
    assert unknown.detector_reason is not None
    assert "inventory_region_not_localized" in unknown.detector_reason


def test_no_row_gutter_reviewed_detector_remains_fail_closed_unknown(
    tmp_path: Path,
    provenance: InventoryValidationProvenance,
) -> None:
    layout = InventoryGridLayout(
        profile_id="session-test-no-row-gutter",
        column_stride=32,
        row_stride=32,
    )
    region = Region(2, 2, layout.width, layout.height)
    frame_width = region.x + region.width + 2
    frame_height = region.y + region.height + 2
    payload = _PIXEL * (frame_width * frame_height)
    profile = InventoryFrameProfile(
        profile_id=layout.profile_id,
        frame_width=frame_width,
        frame_height=frame_height,
        region=region,
        layout=layout,
    )
    reference = Frame.from_raw(
        RawFrame(
            payload=payload,
            width=frame_width,
            height=frame_height,
            pixel_format=PixelFormat.BGRA8888,
        ),
        frame_id=401,
        captured_monotonic_s=401.0,
    )
    detector = inventory_detector_from_profile(profile, reference)

    record = run_inventory_validation_session(
        backend_factory=_BackendFactory(
            (payload,),
            geometries=((frame_width, frame_height),),
        ),
        output_root=tmp_path / "no-row-gutter",
        provenance=provenance,
        cases=(InventoryValidationCase.EMPTY_VALIDATION,),
        detector=detector,
        utc_clock=_fixed_utc,
    ).records[0]

    assert record.detector_mode == "detector-run"
    assert record.detector_occupied_slots is None
    assert record.detector_confidence == 0.0
    assert record.detector_reason == (
        "obstruction_guard_unavailable: localized layout has no horizontal "
        "row-gutter obstruction guard"
    )
    assert record.detector_profile_id == layout.profile_id
    assert record.detector_configuration_id == detector.configuration_id


def test_invalid_plan_and_partial_orphan_never_overwrite_evidence(
    tmp_path: Path,
    provenance: InventoryValidationProvenance,
) -> None:
    factory = _BackendFactory((_EMPTY_PAYLOAD,))
    with pytest.raises(ValueError, match="duplicates"):
        run_inventory_validation_session(
            backend_factory=factory,
            output_root=tmp_path / "invalid",
            provenance=provenance,
            cases=(InventoryValidationCase.FULL, InventoryValidationCase.FULL),
        )
    assert factory.calls == 0
    assert not (tmp_path / "invalid").exists()

    def pause_immediately(*_: object) -> None:
        raise KeyboardInterrupt

    with pytest.raises(InventoryValidationSessionPaused) as raised:
        run_inventory_validation_session(
            backend_factory=_BackendFactory((_EMPTY_PAYLOAD,)),
            output_root=tmp_path / "sessions",
            provenance=provenance,
            cases=(InventoryValidationCase.EMPTY_REFERENCE,),
            ready_callback=pause_immediately,
            utc_clock=_fixed_utc,
        )
    partial = raised.value.session_directory / "captures" / "partial-owned-evidence"
    partial.mkdir()
    sentinel = partial / "frame.bgra"
    sentinel.write_bytes(b"preserve")
    with pytest.raises(InventoryValidationSessionError, match="partial/uncommitted"):
        run_inventory_validation_session(
            backend_factory=_BackendFactory((_EMPTY_PAYLOAD,)),
            output_root=raised.value.session_directory.parent,
            provenance=provenance,
            cases=(InventoryValidationCase.EMPTY_REFERENCE,),
            resume_directory=raised.value.session_directory,
            utc_clock=_fixed_utc,
        )
    assert sentinel.read_bytes() == b"preserve"


def test_cli_runs_default_and_optional_cases_without_false_pass_language(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    apis: list[FakeWin32Api] = []

    def backend_constructor(**_: object) -> WindowsCaptureBackend:
        backend, api = _backend()
        apis.append(api)
        return backend

    monkeypatch.setattr(
        live_validation_session_cli,
        "WindowsCaptureBackend",
        backend_constructor,
    )
    prompts: list[str] = []
    exit_code = live_validation_session_cli.main(
        [
            "--output-root",
            str(tmp_path / "sessions"),
            "--capture-build",
            "session-cli-test",
            "--include-case",
            "hover-drag",
            "--include-case",
            "quantity-text",
        ],
        input_function=lambda prompt: prompts.append(prompt) or "",
    )
    output = capsys.readouterr()
    assert exit_code == 0
    assert len(apis) == 8
    assert len(prompts) == 8
    assert all(api.capture_calls == [_WINDOW_HANDLE] for api in apis)
    assert "COMPLETE -- REVIEW REQUIRED" in output.out
    assert "capture completion is not a detector pass" in output.out.lower()
    assert " PASS " not in output.out
    report_path = next((tmp_path / "sessions").glob("*/session-report.json"))
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert [item["operator_case"]["label"] for item in payload["cases"]][-2:] == [
        "hover-drag",
        "quantity-text",
    ]


def test_tool_is_thin_and_manifest_rejects_truth_promotion(
    tmp_path: Path,
    provenance: InventoryValidationProvenance,
) -> None:
    source = _TOOL.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert not any(isinstance(node, (ast.FunctionDef, ast.ClassDef)) for node in tree.body)
    assert "live_validation_session_cli" in source
    assert "WindowsCaptureBackend" not in source

    report = run_inventory_validation_session(
        backend_factory=_BackendFactory((_EMPTY_PAYLOAD,)),
        output_root=tmp_path / "sessions",
        provenance=provenance,
        cases=(InventoryValidationCase.EMPTY_REFERENCE,),
        utc_clock=_fixed_utc,
    )
    payload = json.loads(report.report_path.read_text(encoding="utf-8"))
    payload["cases"][0]["operator_case"]["truth_status"] = "verified"
    report.report_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(InventoryValidationSessionError, match="truth status"):
        load_inventory_validation_session(report.session_directory)


def test_default_inventory_evidence_roots_are_gitignored() -> None:
    ignored_roots = set(_GITIGNORE.read_text(encoding="utf-8").splitlines())
    assert "/diagnostics/inventory-live/" in ignored_roots
    assert "/diagnostics/inventory-validation-sessions/" in ignored_roots


def test_session_report_is_deterministic_for_equivalent_owned_evidence(
    tmp_path: Path,
    provenance: InventoryValidationProvenance,
) -> None:
    first = run_inventory_validation_session(
        backend_factory=_BackendFactory((_EMPTY_PAYLOAD,)),
        output_root=tmp_path / "first",
        provenance=provenance,
        cases=(InventoryValidationCase.EMPTY_REFERENCE,),
        capture_clock=ManualClock(11.0),
        utc_clock=_fixed_utc,
    )
    second = run_inventory_validation_session(
        backend_factory=_BackendFactory((_EMPTY_PAYLOAD,)),
        output_root=tmp_path / "second",
        provenance=provenance,
        cases=(InventoryValidationCase.EMPTY_REFERENCE,),
        capture_clock=ManualClock(11.0),
        utc_clock=_fixed_utc,
    )
    assert first.session_id == second.session_id
    assert first.to_json() == second.to_json()
    assert (
        first.profile_review_draft_path.read_text(encoding="utf-8")
        == second.profile_review_draft_path.read_text(encoding="utf-8")
    )
