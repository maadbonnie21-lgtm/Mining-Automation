from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.capture.testing import ManualClock
from mining_automation.capture.windows import CapturedPixels, WindowInfo, WindowsCaptureBackend
from mining_automation.capture.windows.testing import FakeWin32Api
from mining_automation.perception.inventory import (
    InventoryCaseReview,
    InventoryEvidenceVisibility,
    InventoryReviewDecision,
    InventoryReviewGateError,
    InventoryReviewPackage,
    InventoryReviewPackageCase,
    InventoryReviewRecord,
    InventoryReviewSourceSession,
    InventorySanitizedReplayError,
    InventoryValidationSplit,
    load_inventory_review_package,
    load_inventory_review_record,
    prepare_inventory_review_package,
    replay_inventory_sanitized_fixture,
    run_inventory_review_replay_gate,
    run_inventory_validation_session,
)
from mining_automation.perception.inventory.geometry import (
    INVENTORY_CAPACITY,
    InventoryGridLayout,
    Region,
)
from mining_automation.perception.inventory.live_validation import (
    InventoryValidationCase,
    InventoryValidationProvenance,
)
from mining_automation.perception.inventory.live_validation_session import (
    InventoryValidationSessionReport,
)
from mining_automation.perception.inventory.review_gate import (
    _derive_unique_inventory_lattice,
    _remaining_release_gaps,
)

_HEAD_SHA = "a" * 40
_WINDOW_HANDLE = 731
_FRAME_WIDTH = 360
_FRAME_HEIGHT = 430
_BACKGROUND_PIXEL = bytes((18, 24, 31, 255))
_ITEM_PIXEL = bytes((190, 125, 58, 255))
_PRIVATE_TEXT = b"PRIVATE-PLAYER-NAME"
_LAYOUT = InventoryGridLayout(
    profile_id="synthetic-reviewed-layout",
    column_stride=36,
    row_stride=36,
)
_INVENTORY_REGION = _LAYOUT.region_at(140, 130)


def _fixed_utc() -> datetime:
    return datetime(2026, 8, 30, 20, 15, 0, tzinfo=UTC)


def _payload(*, occupied_slots: int) -> bytes:
    payload = bytearray(_BACKGROUND_PIXEL * (_FRAME_WIDTH * _FRAME_HEIGHT))
    payload[: len(_PRIVATE_TEXT)] = _PRIVATE_TEXT
    for index in range(occupied_slots):
        slot = _LAYOUT.slot_region(_INVENTORY_REGION, index)
        for y in range(slot.y, slot.y + slot.height):
            start = (y * _FRAME_WIDTH + slot.x) * 4
            payload[start : start + slot.width * 4] = _ITEM_PIXEL * slot.width
    return bytes(payload)


_EMPTY_PAYLOAD = _payload(occupied_slots=0)
_FULL_PAYLOAD = _payload(occupied_slots=INVENTORY_CAPACITY)


class _BackendFactory:
    def __init__(self, payloads: tuple[bytes, ...]) -> None:
        self._payloads = payloads
        self.calls = 0

    def __call__(self) -> WindowsCaptureBackend:
        payload = self._payloads[self.calls]
        self.calls += 1
        api = FakeWin32Api(
            windows=[
                WindowInfo(
                    hwnd=_WINDOW_HANDLE,
                    title="RuneLite - PRIVATE PLAYER NAME",
                    class_name="SunAwtFrame",
                    is_visible=True,
                    is_minimized=False,
                    client_width=_FRAME_WIDTH + 7,
                    client_height=_FRAME_HEIGHT + 9,
                )
            ],
            captures={
                _WINDOW_HANDLE: CapturedPixels(
                    payload=payload,
                    width=_FRAME_WIDTH,
                    height=_FRAME_HEIGHT,
                )
            },
            dpi_by_hwnd={_WINDOW_HANDLE: 144},
        )
        return WindowsCaptureBackend(win32_api=api)


def _session(tmp_path: Path) -> InventoryValidationSessionReport:
    factory = _BackendFactory((_EMPTY_PAYLOAD, _FULL_PAYLOAD))
    report = run_inventory_validation_session(
        backend_factory=factory,
        output_root=tmp_path / "sessions",
        provenance=InventoryValidationProvenance(
            capture_build="review-gate-test",
            runelite_build="synthetic-runelite",
            notes=("PRIVATE SESSION NOTE MUST NOT ENTER REVIEW PACKAGE",),
        ),
        cases=(
            InventoryValidationCase.PARTIAL,
            InventoryValidationCase.FULL,
        ),
        capture_clock=ManualClock(30.0),
        utc_clock=_fixed_utc,
    )
    assert factory.calls == 2
    assert report.complete
    return report


def _single_case_session(
    tmp_path: Path,
    *,
    name: str,
    case: InventoryValidationCase,
    payload: bytes,
    renderer: str,
    utc_second: int,
) -> InventoryValidationSessionReport:
    factory = _BackendFactory((payload,))
    report = run_inventory_validation_session(
        backend_factory=factory,
        output_root=tmp_path / name,
        provenance=InventoryValidationProvenance(
            capture_build=_HEAD_SHA,
            runelite_build="synthetic-runelite",
            windows_scaling_percent=150,
            client_mode="resizable",
            runelite_theme="default-dark",
            renderer=renderer,
            capture_configuration_id="synthetic-window-capture-v1",
        ),
        cases=(case,),
        capture_clock=ManualClock(30.0),
        utc_clock=lambda: datetime(
            2026,
            8,
            30,
            20,
            15,
            utc_second,
            tzinfo=UTC,
        ),
    )
    assert factory.calls == 1
    assert report.complete
    return report


def _prepare(
    tmp_path: Path,
    report: InventoryValidationSessionReport,
    *,
    name: str = "review-package",
):
    return prepare_inventory_review_package(
        (report.session_directory,),
        tmp_path / name,
        generator_head_sha=_HEAD_SHA,
    )


def _review_record(package_directory: Path) -> InventoryReviewRecord:
    package = load_inventory_review_package(package_directory)
    reference_case, full_case = package.cases
    manifest_sha = hashlib.sha256(package.manifest_path.read_bytes()).hexdigest()
    return InventoryReviewRecord(
        package_manifest_sha256=manifest_sha,
        reviewer="independent-test-reviewer",
        reviewed_at_utc="2026-08-30T20:20:00Z",
        cases=(
            InventoryCaseReview(
                session_id=reference_case.session_id,
                capture_id=reference_case.capture_id,
                panel_raw_sha256=reference_case.panel_raw_sha256,
                decision=InventoryReviewDecision.APPROVED,
                validation_split=InventoryValidationSplit.REFERENCE,
                visibility=InventoryEvidenceVisibility.INVENTORY,
                occupied_slots=0,
                operator_intent_confirmed=True,
                hover_visible=False,
                selected_item_visible=False,
                drag_visible=False,
                quantity_text_visible=False,
                geometry_source=False,
                artwork_tags=(),
            ),
            InventoryCaseReview(
                session_id=full_case.session_id,
                capture_id=full_case.capture_id,
                panel_raw_sha256=full_case.panel_raw_sha256,
                decision=InventoryReviewDecision.APPROVED,
                validation_split=InventoryValidationSplit.CALIBRATION,
                visibility=InventoryEvidenceVisibility.INVENTORY,
                occupied_slots=INVENTORY_CAPACITY,
                operator_intent_confirmed=True,
                hover_visible=False,
                selected_item_visible=False,
                drag_visible=False,
                quantity_text_visible=False,
                geometry_source=True,
                artwork_tags=("synthetic-item",),
            ),
        ),
    )


def _write_review(path: Path, record: InventoryReviewRecord) -> Path:
    path.write_text(record.to_json(), encoding="utf-8")
    return path


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _rewrite_package_manifest(
    package_directory: Path,
    mutate: Callable[[dict[str, object]], None],
) -> InventoryReviewPackage:
    manifest_path = package_directory / "review-package.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    mutate(raw)
    manifest = _canonical_json_bytes(raw)
    manifest_path.write_bytes(manifest)
    digest = hashlib.sha256(manifest).hexdigest()
    (package_directory / "review-package.sha256").write_text(
        f"{digest}  review-package.json\n",
        encoding="utf-8",
    )
    return load_inventory_review_package(package_directory)


def _review_for_package(package: InventoryReviewPackage) -> InventoryReviewRecord:
    manifest_sha = hashlib.sha256(package.manifest_path.read_bytes()).hexdigest()
    reviews: list[InventoryCaseReview] = []
    for index, case in enumerate(package.cases):
        is_reference = index == 0
        reviews.append(
            InventoryCaseReview(
                session_id=case.session_id,
                capture_id=case.capture_id,
                panel_raw_sha256=case.panel_raw_sha256,
                decision=InventoryReviewDecision.APPROVED,
                validation_split=(
                    InventoryValidationSplit.REFERENCE
                    if is_reference
                    else InventoryValidationSplit.CALIBRATION
                ),
                visibility=InventoryEvidenceVisibility.INVENTORY,
                occupied_slots=0 if is_reference else INVENTORY_CAPACITY,
                operator_intent_confirmed=True,
                hover_visible=False,
                selected_item_visible=False,
                drag_visible=False,
                quantity_text_visible=False,
                geometry_source=not is_reference,
                artwork_tags=() if is_reference else ("synthetic-item",),
            )
        )
    return InventoryReviewRecord(
        package_manifest_sha256=manifest_sha,
        reviewer="independent-test-reviewer",
        reviewed_at_utc="2026-08-30T20:20:00Z",
        cases=tuple(reviews),
    )


def _frame(payload: bytes, *, width: int, height: int, frame_id: int) -> Frame:
    return Frame.from_raw(
        RawFrame(
            payload=payload,
            width=width,
            height=height,
            pixel_format=PixelFormat.BGRA8888,
        ),
        frame_id=frame_id,
        captured_monotonic_s=float(frame_id),
    )


def _two_lattice_payload() -> tuple[bytes, bytes, Region, Region]:
    width = 680
    height = 430
    first = _LAYOUT.region_at(40, 100)
    second = _LAYOUT.region_at(400, 100)
    reference = _BACKGROUND_PIXEL * (width * height)
    full = bytearray(reference)
    for region in (first, second):
        for index in range(INVENTORY_CAPACITY):
            slot = _LAYOUT.slot_region(region, index)
            for y in range(slot.y, slot.y + slot.height):
                start = (y * width + slot.x) * 4
                full[start : start + slot.width * 4] = _ITEM_PIXEL * slot.width
    return reference, bytes(full), first, second


def test_review_package_is_deterministic_and_excludes_private_source_material(
    tmp_path: Path,
) -> None:
    report = _session(tmp_path)

    first = _prepare(tmp_path, report, name="package-a")
    second = _prepare(tmp_path, report, name="package-b")

    first_files = {
        path.relative_to(first.package_directory).as_posix(): path.read_bytes()
        for path in first.package_directory.rglob("*")
        if path.is_file()
    }
    second_files = {
        path.relative_to(second.package_directory).as_posix(): path.read_bytes()
        for path in second.package_directory.rglob("*")
        if path.is_file()
    }
    assert first_files == second_files
    assert first.review_region == Region(72, 70, 288, 360)
    assert all(_PRIVATE_TEXT not in content for content in first_files.values())
    assert all(b"PRIVATE PLAYER NAME" not in content for content in first_files.values())
    assert all(b"PRIVATE SESSION NOTE" not in content for content in first_files.values())
    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    assert manifest["privacy"] == {
        "free_form_notes_included": False,
        "full_frames_included": False,
        "review_region": [72, 70, 288, 360],
        "window_titles_included": False,
    }


def test_blank_review_template_never_promotes_operator_labels_to_truth(
    tmp_path: Path,
) -> None:
    package = _prepare(tmp_path, _session(tmp_path))

    manifest = json.loads(package.manifest_path.read_text(encoding="utf-8"))
    template = json.loads(package.template_path.read_text(encoding="utf-8"))

    assert [
        item["operator_selection"] for item in manifest["cases"]
    ] == [
        {"label": "partial", "truth_status": "operator-selected-unverified"},
        {"label": "full", "truth_status": "operator-selected-unverified"},
    ]
    assert "operator_selection" not in json.dumps(template["cases"])
    assert all(
        item["decision"] is None
        and item["validation_split"] is None
        and item["visibility"] is None
        and item["occupied_slots"] is None
        and item["hover_visible"] is None
        for item in template["cases"]
    )
    assert "No field is populated from an operator label" in template["warning"]


def test_schema_v1_review_record_loads_with_hover_truth_defaulted_false(
    tmp_path: Path,
) -> None:
    package = _prepare(tmp_path, _session(tmp_path))
    raw = json.loads(_review_record(package.package_directory).to_json())
    raw["schema_version"] = 1
    cases = raw["cases"]
    assert isinstance(cases, list)
    for case in cases:
        assert isinstance(case, dict)
        case.pop("hover_visible")
    path = tmp_path / "schema-v1-review.json"
    path.write_bytes(_canonical_json_bytes(raw))

    loaded = load_inventory_review_record(path, package)

    assert all(case.hover_visible is False for case in loaded.cases)


def test_review_record_schema_version_rejects_boolean_alias(tmp_path: Path) -> None:
    package = _prepare(tmp_path, _session(tmp_path))
    raw = json.loads(_review_record(package.package_directory).to_json())
    raw["schema_version"] = True
    path = tmp_path / "boolean-schema-version-review.json"
    path.write_bytes(_canonical_json_bytes(raw))

    with pytest.raises(
        InventoryReviewGateError,
        match="unsupported review record schema version",
    ):
        load_inventory_review_record(path, package)


def test_package_loader_detects_artifact_and_manifest_tampering(tmp_path: Path) -> None:
    report = _session(tmp_path)
    artifact_package = _prepare(tmp_path, report, name="artifact-tamper")
    artifact_path = artifact_package.package_directory / artifact_package.cases[0].panel_raw_path
    original = artifact_path.read_bytes()
    artifact_path.write_bytes(bytes((original[0] ^ 0xFF,)) + original[1:])

    with pytest.raises(InventoryReviewGateError, match="panel BGRA SHA-256 mismatch"):
        load_inventory_review_package(artifact_package.package_directory)

    manifest_package = _prepare(tmp_path, report, name="manifest-tamper")
    manifest_package.manifest_path.write_bytes(
        manifest_package.manifest_path.read_bytes() + b" "
    )
    with pytest.raises(InventoryReviewGateError, match="sidecar mismatch"):
        load_inventory_review_package(manifest_package.package_directory)


def test_rebound_regenerated_panel_attack_is_rejected_against_owned_frame(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    package = _prepare(tmp_path, session)
    first = package.cases[0]
    raw_path = package.package_directory / first.panel_raw_path
    bmp_path = package.package_directory / first.panel_bmp_path
    raw = bytearray(raw_path.read_bytes())
    raw[0] ^= 0xFF
    raw_path.write_bytes(raw)
    bmp = bytearray(bmp_path.read_bytes())
    assert bmp[:2] == b"BM"
    bmp[54] = raw[0]
    bmp_path.write_bytes(bmp)

    def rebind_artifacts(manifest: dict[str, object]) -> None:
        cases = manifest["cases"]
        assert isinstance(cases, list)
        case = cases[0]
        assert isinstance(case, dict)
        artifacts = case["artifacts"]
        assert isinstance(artifacts, dict)
        panel_raw = artifacts["panel_bgra"]
        panel_bmp = artifacts["panel_bmp"]
        assert isinstance(panel_raw, dict)
        assert isinstance(panel_bmp, dict)
        panel_raw["sha256"] = hashlib.sha256(raw).hexdigest()
        panel_bmp["sha256"] = hashlib.sha256(bmp).hexdigest()

    rebound = _rewrite_package_manifest(
        package.package_directory,
        rebind_artifacts,
    )
    review_path = _write_review(
        tmp_path / "rebound-review.json",
        _review_for_package(rebound),
    )

    with pytest.raises(
        InventoryReviewGateError,
        match="review panel BGRA does not match the durable owned frame",
    ):
        run_inventory_review_replay_gate(
            (session.session_directory,),
            rebound.package_directory,
            review_path,
            tmp_path / "must-not-exist",
            expected_head_sha=_HEAD_SHA,
        )
    assert not (tmp_path / "must-not-exist").exists()


def test_rebound_bmp_raw_divergence_is_rejected_against_owned_frame(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    package = _prepare(tmp_path, session)
    first = package.cases[0]
    bmp_path = package.package_directory / first.panel_bmp_path
    bmp = bytearray(bmp_path.read_bytes())
    bmp[54] ^= 0xFF
    bmp_path.write_bytes(bmp)

    def rebind_bmp(manifest: dict[str, object]) -> None:
        cases = manifest["cases"]
        assert isinstance(cases, list)
        case = cases[0]
        assert isinstance(case, dict)
        artifacts = case["artifacts"]
        assert isinstance(artifacts, dict)
        panel_bmp = artifacts["panel_bmp"]
        assert isinstance(panel_bmp, dict)
        panel_bmp["sha256"] = hashlib.sha256(bmp).hexdigest()

    rebound = _rewrite_package_manifest(package.package_directory, rebind_bmp)
    review_path = _write_review(
        tmp_path / "bmp-divergence-review.json",
        _review_for_package(rebound),
    )

    with pytest.raises(
        InventoryReviewGateError,
        match="review panel BMP does not match the durable owned frame",
    ):
        run_inventory_review_replay_gate(
            (session.session_directory,),
            rebound.package_directory,
            review_path,
            tmp_path / "must-not-exist",
            expected_head_sha=_HEAD_SHA,
        )


@pytest.mark.parametrize(
    ("attack", "expected_error"),
    (
        ("omit", "review package omits durable source captures"),
        ("relabel", "case sequence/metadata differs from durable sessions"),
        ("frame-metadata", "case sequence/metadata differs from durable sessions"),
    ),
)
def test_rebound_package_sequence_and_metadata_attacks_are_rejected(
    tmp_path: Path,
    attack: str,
    expected_error: str,
) -> None:
    session = _session(tmp_path)
    package = _prepare(tmp_path, session)

    def mutate(manifest: dict[str, object]) -> None:
        cases = manifest["cases"]
        assert isinstance(cases, list)
        first = cases[0]
        assert isinstance(first, dict)
        if attack == "omit":
            cases.pop()
        elif attack == "relabel":
            operator = first["operator_selection"]
            assert isinstance(operator, dict)
            operator["label"] = "full"
        else:
            frame = first["frame"]
            assert isinstance(frame, dict)
            width = frame["width"]
            assert isinstance(width, int)
            frame["width"] = width + 1

    rebound = _rewrite_package_manifest(package.package_directory, mutate)
    review_path = _write_review(
        tmp_path / f"{attack}-review.json",
        _review_for_package(rebound),
    )

    with pytest.raises(InventoryReviewGateError, match=expected_error):
        run_inventory_review_replay_gate(
            (session.session_directory,),
            rebound.package_directory,
            review_path,
            tmp_path / f"must-not-exist-{attack}",
            expected_head_sha=_HEAD_SHA,
        )


def test_explicit_review_must_cover_exact_artifacts_and_obey_truth_contracts(
    tmp_path: Path,
) -> None:
    package = _prepare(tmp_path, _session(tmp_path))
    valid = _review_record(package.package_directory)
    reference = valid.cases[0]
    incomplete = InventoryReviewRecord(
        package_manifest_sha256=valid.package_manifest_sha256,
        reviewer=valid.reviewer,
        reviewed_at_utc=valid.reviewed_at_utc,
        cases=(reference,),
    )

    with pytest.raises(InventoryReviewGateError, match="cover every package case"):
        load_inventory_review_record(
            _write_review(tmp_path / "incomplete-review.json", incomplete),
            package,
        )

    wrong_hash = InventoryReviewRecord(
        package_manifest_sha256=valid.package_manifest_sha256,
        reviewer=valid.reviewer,
        reviewed_at_utc=valid.reviewed_at_utc,
        cases=(
            InventoryCaseReview(
                session_id=reference.session_id,
                capture_id=reference.capture_id,
                panel_raw_sha256="0" * 64,
                decision=reference.decision,
                validation_split=reference.validation_split,
                visibility=reference.visibility,
                occupied_slots=reference.occupied_slots,
                operator_intent_confirmed=reference.operator_intent_confirmed,
                hover_visible=reference.hover_visible,
                selected_item_visible=reference.selected_item_visible,
                drag_visible=reference.drag_visible,
                quantity_text_visible=reference.quantity_text_visible,
                geometry_source=reference.geometry_source,
                artwork_tags=reference.artwork_tags,
            ),
            valid.cases[1],
        ),
    )
    with pytest.raises(InventoryReviewGateError, match="exact artifact hashes"):
        load_inventory_review_record(
            _write_review(tmp_path / "wrong-hash-review.json", wrong_hash),
            package,
        )

    with pytest.raises(ValueError, match="wrong-tab/obstructed"):
        InventoryCaseReview(
            session_id="session",
            capture_id="capture",
            panel_raw_sha256="1" * 64,
            decision=InventoryReviewDecision.APPROVED,
            validation_split=InventoryValidationSplit.NEGATIVE,
            visibility=InventoryEvidenceVisibility.WRONG_TAB,
            occupied_slots=1,
            operator_intent_confirmed=True,
            hover_visible=False,
            selected_item_visible=False,
            drag_visible=False,
            quantity_text_visible=False,
            geometry_source=False,
            artwork_tags=(),
        )


@pytest.mark.parametrize(
    ("validation_split", "hover", "selected", "drag", "quantity"),
    (
        (InventoryValidationSplit.HELD_OUT, False, False, False, False),
        (InventoryValidationSplit.CALIBRATION, True, False, False, False),
        (InventoryValidationSplit.CALIBRATION, False, True, False, False),
        (InventoryValidationSplit.CALIBRATION, False, False, True, False),
        (InventoryValidationSplit.CALIBRATION, False, False, False, True),
    ),
)
def test_geometry_source_rejects_noncalibration_or_adversarial_evidence(
    validation_split: InventoryValidationSplit,
    hover: bool,
    selected: bool,
    drag: bool,
    quantity: bool,
) -> None:
    with pytest.raises(
        ValueError,
        match="geometry_source requires a clean approved calibration 28-slot inventory",
    ):
        InventoryCaseReview(
            session_id="session",
            capture_id="capture",
            panel_raw_sha256="1" * 64,
            decision=InventoryReviewDecision.APPROVED,
            validation_split=validation_split,
            visibility=InventoryEvidenceVisibility.INVENTORY,
            occupied_slots=INVENTORY_CAPACITY,
            operator_intent_confirmed=True,
            hover_visible=hover,
            selected_item_visible=selected,
            drag_visible=drag,
            quantity_text_visible=quantity,
            geometry_source=True,
            artwork_tags=("synthetic-item",),
        )


def test_reference_and_geometry_require_the_same_capture_environment(
    tmp_path: Path,
) -> None:
    reference_session = _single_case_session(
        tmp_path,
        name="reference-session",
        case=InventoryValidationCase.PARTIAL,
        payload=_EMPTY_PAYLOAD,
        renderer="gpu",
        utc_second=1,
    )
    geometry_session = _single_case_session(
        tmp_path,
        name="geometry-session",
        case=InventoryValidationCase.FULL,
        payload=_FULL_PAYLOAD,
        renderer="software",
        utc_second=2,
    )
    package = prepare_inventory_review_package(
        (reference_session.session_directory, geometry_session.session_directory),
        tmp_path / "environment-package",
        generator_head_sha=_HEAD_SHA,
    )
    review_path = _write_review(
        tmp_path / "environment-review.json",
        _review_for_package(package),
    )

    with pytest.raises(
        InventoryReviewGateError,
        match="reference and geometry evidence use different capture environments",
    ):
        run_inventory_review_replay_gate(
            (
                reference_session.session_directory,
                geometry_session.session_directory,
            ),
            package.package_directory,
            review_path,
            tmp_path / "must-not-exist",
            expected_head_sha=_HEAD_SHA,
        )
    assert not (tmp_path / "must-not-exist").exists()


def test_lattice_derivation_requires_one_unique_reviewed_four_by_seven_grid() -> None:
    region, column_stride, row_stride = _derive_unique_inventory_lattice(
        _frame(_EMPTY_PAYLOAD, width=_FRAME_WIDTH, height=_FRAME_HEIGHT, frame_id=1),
        _frame(_FULL_PAYLOAD, width=_FRAME_WIDTH, height=_FRAME_HEIGHT, frame_id=2),
    )
    assert (region, column_stride, row_stride) == (_INVENTORY_REGION, 36, 36)

    reference, full, first, second = _two_lattice_payload()
    assert first != second
    with pytest.raises(InventoryReviewGateError, match="exactly one 4x7 lattice"):
        _derive_unique_inventory_lattice(
            _frame(reference, width=680, height=430, frame_id=3),
            _frame(full, width=680, height=430, frame_id=4),
        )


def test_gate_uses_unchanged_detector_and_publishes_only_nonactivating_candidate(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    package = _prepare(tmp_path, session)
    review_path = _write_review(
        tmp_path / "review.json",
        _review_record(package.package_directory),
    )

    report = run_inventory_review_replay_gate(
        (session.session_directory,),
        package.package_directory,
        review_path,
        tmp_path / "replay-output",
        expected_head_sha=_HEAD_SHA,
        fixture_output_directory=tmp_path / "sanitized-fixture",
    )

    payload = report.payload
    candidate = payload["candidate"]
    assert isinstance(candidate, dict)
    assert candidate["activation_allowed"] is False
    assert candidate["review_status"] == "candidate-awaiting-release-approval"
    detector = candidate["detector"]
    assert isinstance(detector, dict)
    assert detector["detector_id"] == "inventory-baseline"
    assert detector["detector_version"] == "1.0.0"
    assert detector["minimum_slot_confidence"] == 0.8
    assert payload["release_gate_passed"] is False
    assert payload["remaining_release_gaps"]

    results = payload["results"]
    assert isinstance(results, list)
    assert [item["detector"]["occupied_slots"] for item in results] == [0, 28]
    assert [item["detector"]["label"] for item in results] == ["empty", "full"]
    assert all(item["review_agreement"] is True for item in results)
    assert all(item["sanitized_observation_equals_exact_owned"] is True for item in results)
    assert all(
        item["operator_selection"]["truth_status"]
        == "operator-selected-unverified"
        for item in results
    )

    candidate_file = json.loads(
        (report.report_directory / "candidate-profile.json").read_text(encoding="utf-8")
    )
    fixture = json.loads(
        (tmp_path / "sanitized-fixture" / "manifest.json").read_text(encoding="utf-8")
    )
    assert candidate_file == candidate
    assert fixture["activation_allowed"] is False
    assert fixture["candidate"] == candidate
    assert fixture["schema_version"] == 2
    assert fixture["generated"] == {"git_head_sha": _HEAD_SHA}
    assert fixture["dataset_id"].startswith("inventory-live-candidate-safety-")
    assert fixture["dataset_id"] != "inventory-live-candidate-safety-v1"
    assert [
        item["current_safety_expectation"]["occupied_slots"]
        for item in fixture["cases"]
    ] == [0, 28]
    assert all(
        (tmp_path / "sanitized-fixture" / item["frame_region"]["path"]).is_file()
        for item in fixture["cases"]
    )
    sanitized_report = replay_inventory_sanitized_fixture(
        tmp_path / "sanitized-fixture"
    )
    assert sanitized_report.passed
    assert sanitized_report.failed_case_ids == ()
    assert sanitized_report.detector_id == "inventory-baseline"
    assert sanitized_report.detector_version == "1.0.0"
    assert sanitized_report.fixture_schema_version == 2
    assert sanitized_report.generator_head_sha == _HEAD_SHA
    assert sanitized_report.dataset_id == fixture["dataset_id"]
    assert len(sanitized_report.cases) == 2

    fixture["dataset_id"] = "inventory-live-candidate-safety-0000000000000000"
    rewritten = _canonical_json_bytes(fixture)
    fixture_manifest = tmp_path / "sanitized-fixture" / "manifest.json"
    fixture_manifest.write_bytes(rewritten)
    (tmp_path / "sanitized-fixture" / "manifest.json.sha256").write_text(
        f"{hashlib.sha256(rewritten).hexdigest()}  manifest.json\n",
        encoding="utf-8",
    )
    with pytest.raises(InventorySanitizedReplayError, match="dataset identity"):
        replay_inventory_sanitized_fixture(tmp_path / "sanitized-fixture")


def test_release_gate_reports_every_required_semantic_evidence_gap(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    package = _prepare(tmp_path, session)
    review_path = _write_review(
        tmp_path / "semantic-gap-review.json",
        _review_record(package.package_directory),
    )

    report = run_inventory_review_replay_gate(
        (session.session_directory,),
        package.package_directory,
        review_path,
        tmp_path / "semantic-gap-output",
        expected_head_sha=_HEAD_SHA,
    )

    gaps = report.payload["remaining_release_gaps"]
    assert isinstance(gaps, list)
    assert {
        "no reviewed ordinary held-out partial inventory evidence",
        "no reviewed ordinary held-out full inventory evidence",
        "no reviewed wrong-tab negative evidence",
        "fewer than two distinct reviewed obstruction examples",
        "no reviewer-confirmed hover evidence",
        "no reviewer-confirmed held/drag evidence",
        "no reviewed quantity-text adversarial evidence",
    }.issubset(set(gaps))


def test_artwork_tag_ordering_cannot_game_diversity_release_gate(
    tmp_path: Path,
) -> None:
    session_id = "synthetic-reviewed-session"

    def package_case(
        order: int,
        capture_id: str,
        panel_sha: str,
    ) -> InventoryReviewPackageCase:
        return InventoryReviewPackageCase(
            order=order,
            session_id=session_id,
            capture_id=capture_id,
            operator_label="partial",
            source_report_path=f"captures/{capture_id}/report.json",
            source_report_sha256=f"{order + 10:064x}",
            source_payload_sha256=f"{order + 20:064x}",
            frame_width=_FRAME_WIDTH,
            frame_height=_FRAME_HEIGHT,
            pixel_format=PixelFormat.BGRA8888.value,
            reported_dpi=144,
            window_class="SunAwtFrame",
            panel_raw_path=f"panel-artifacts/{capture_id}.panel.bgra",
            panel_raw_sha256=panel_sha,
            panel_bmp_path=f"panel-artifacts/{capture_id}.panel.bmp",
            panel_bmp_sha256=f"{order + 30:064x}",
        )

    package_cases = tuple(
        package_case(index, capture_id, f"{index + 40:064x}")
        for index, capture_id in enumerate(
            ("reference", "geometry", "partial", "full"),
            start=1,
        )
    )
    package = InventoryReviewPackage(
        package_directory=tmp_path,
        generator_head_sha=_HEAD_SHA,
        source_sessions=(
            InventoryReviewSourceSession(
                session_id=session_id,
                session_report_sha256="1" * 64,
                capture_build=_HEAD_SHA,
                runelite_build="synthetic-runelite",
                windows_scaling_percent=150,
                client_mode="resizable",
                runelite_theme="default-dark",
                renderer="gpu",
                capture_configuration_id="synthetic-window-capture-v1",
            ),
        ),
        review_region=Region(72, 70, 288, 360),
        cases=package_cases,
    )

    reference = InventoryCaseReview(
        session_id=session_id,
        capture_id="reference",
        panel_raw_sha256=package_cases[0].panel_raw_sha256,
        decision=InventoryReviewDecision.APPROVED,
        validation_split=InventoryValidationSplit.REFERENCE,
        visibility=InventoryEvidenceVisibility.INVENTORY,
        occupied_slots=0,
        operator_intent_confirmed=True,
        hover_visible=False,
        selected_item_visible=False,
        drag_visible=False,
        quantity_text_visible=False,
        geometry_source=False,
        artwork_tags=(),
    )
    geometry = replace(
        reference,
        capture_id="geometry",
        panel_raw_sha256=package_cases[1].panel_raw_sha256,
        validation_split=InventoryValidationSplit.CALIBRATION,
        occupied_slots=INVENTORY_CAPACITY,
        geometry_source=True,
        artwork_tags=("wide-sprite", "ore"),
    )
    partial = replace(
        reference,
        capture_id="partial",
        panel_raw_sha256=package_cases[2].panel_raw_sha256,
        validation_split=InventoryValidationSplit.HELD_OUT,
        occupied_slots=7,
        artwork_tags=("ore", "wide-sprite"),
    )
    full = replace(
        reference,
        capture_id="full",
        panel_raw_sha256=package_cases[3].panel_raw_sha256,
        validation_split=InventoryValidationSplit.HELD_OUT,
        occupied_slots=INVENTORY_CAPACITY,
        artwork_tags=("wide-sprite", "ore"),
    )
    review = InventoryReviewRecord(
        package_manifest_sha256="2" * 64,
        reviewer="independent-test-reviewer",
        reviewed_at_utc="2026-08-30T20:20:00Z",
        cases=(reference, geometry, partial, full),
    )
    results = tuple(
        {
            "candidate_region_artifact": {"sha256": f"{index + 50:064x}"},
            "review": case.as_dict(),
        }
        for index, case in enumerate(review.cases)
    )

    gaps = _remaining_release_gaps(
        package,
        review,
        results,
        f"{50:064x}",
    )

    assert "insufficient byte-distinct varied-art partial/full evidence" in gaps


def test_sanitized_fixture_replay_rejects_payload_tampering(tmp_path: Path) -> None:
    session = _session(tmp_path)
    package = _prepare(tmp_path, session)
    review_path = _write_review(
        tmp_path / "review.json",
        _review_record(package.package_directory),
    )
    fixture_directory = tmp_path / "sanitized-fixture"
    run_inventory_review_replay_gate(
        (session.session_directory,),
        package.package_directory,
        review_path,
        tmp_path / "replay-output",
        expected_head_sha=_HEAD_SHA,
        fixture_output_directory=fixture_directory,
    )
    fixture = json.loads(
        (fixture_directory / "manifest.json").read_text(encoding="utf-8")
    )
    first_path = fixture_directory / fixture["cases"][0]["frame_region"]["path"]
    payload = first_path.read_bytes()
    first_path.write_bytes(bytes((payload[0] ^ 0xFF,)) + payload[1:])

    with pytest.raises(InventorySanitizedReplayError, match="SHA-256 mismatch"):
        replay_inventory_sanitized_fixture(fixture_directory)


def test_gate_rejects_head_or_reviewer_binding_mismatch_before_output(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    package = _prepare(tmp_path, session)
    review = _review_record(package.package_directory)
    review_path = _write_review(tmp_path / "review.json", review)

    with pytest.raises(InventoryReviewGateError, match="generator head"):
        run_inventory_review_replay_gate(
            (session.session_directory,),
            package.package_directory,
            review_path,
            tmp_path / "must-not-exist",
            expected_head_sha="b" * 40,
        )
    assert not (tmp_path / "must-not-exist").exists()

    raw = json.loads(review_path.read_text(encoding="utf-8"))
    raw["package_manifest_sha256"] = "f" * 64
    review_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(InventoryReviewGateError, match="another package manifest"):
        load_inventory_review_record(review_path, package)
