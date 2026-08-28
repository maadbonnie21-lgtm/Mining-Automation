"""Issue #31 coordinate-space ordering and no-input diagnostic regressions."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.validation.camera_coordinates import (
    CameraCoordinateTransform,
    CoordinateMappingError,
    LogicalClientPoint,
    LogicalScreenPoint,
    PhysicalScreenPoint,
    map_logical_client_point,
    require_exact_round_trip,
)


class _AffineTransform(CameraCoordinateTransform):
    """Non-commutative 2x target transform with a translated client origin."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def physical_client_origin(self, hwnd: int) -> PhysicalScreenPoint:
        self.calls.append(("physical-client-origin", hwnd))
        return PhysicalScreenPoint(1200, 300)

    def physical_to_target_logical(
        self,
        hwnd: int,
        point: PhysicalScreenPoint,
    ) -> LogicalScreenPoint:
        self.calls.append(("physical-to-target-logical", hwnd, point.pair))
        return LogicalScreenPoint((point.x - 100) // 2, (point.y - 40) // 2)

    def target_logical_to_physical(
        self,
        hwnd: int,
        point: LogicalScreenPoint,
    ) -> PhysicalScreenPoint:
        self.calls.append(("target-logical-to-physical", hwnd, point.pair))
        return PhysicalScreenPoint(point.x * 2 + 100, point.y * 2 + 40)


class _LossyReverseTransform(_AffineTransform):
    def __init__(self) -> None:
        super().__init__()
        self._reverse_calls = 0

    def physical_to_target_logical(
        self,
        hwnd: int,
        point: PhysicalScreenPoint,
    ) -> LogicalScreenPoint:
        result = super().physical_to_target_logical(hwnd, point)
        self._reverse_calls += 1
        if self._reverse_calls == 2:
            return LogicalScreenPoint(result.x + 1, result.y)
        return result


def _load_mapping_tool() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "tools" / "diagnose_camera_pointer_mapping.py"
    spec = importlib.util.spec_from_file_location("diagnose_camera_pointer_mapping", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_origin_based_mapping_orders_spaces_and_round_trips_exactly() -> None:
    transform = _AffineTransform()

    mapping = map_logical_client_point(
        123,
        LogicalClientPoint(608, 49),
        transform,
    )

    assert mapping.physical_client_origin == PhysicalScreenPoint(1200, 300)
    assert mapping.target_logical_screen_origin == LogicalScreenPoint(550, 130)
    assert mapping.target_logical_screen == LogicalScreenPoint(1158, 179)
    assert mapping.physical_screen == PhysicalScreenPoint(2416, 398)
    assert mapping.reverse_target_logical_screen == LogicalScreenPoint(1158, 179)
    assert mapping.reverse_logical_client == LogicalClientPoint(608, 49)
    assert mapping.exact_round_trip
    assert require_exact_round_trip(mapping) == PhysicalScreenPoint(2416, 398)
    assert transform.calls == [
        ("physical-client-origin", 123),
        ("physical-to-target-logical", 123, (1200, 300)),
        ("target-logical-to-physical", 123, (1158, 179)),
        ("physical-to-target-logical", 123, (2416, 398)),
    ]


def test_wrong_l2p_then_client_to_screen_order_is_observably_different() -> None:
    transform = _AffineTransform()
    reviewed = LogicalClientPoint(608, 49)
    corrected = map_logical_client_point(123, reviewed, transform)

    # This models the audited bug: the client-relative input is incorrectly
    # treated as logical screen, then its physical-screen result is incorrectly
    # treated as a client-relative point by ClientToScreen.
    wrong_intermediate = transform.target_logical_to_physical(
        123,
        LogicalScreenPoint(reviewed.x, reviewed.y),
    )
    wrong_final = PhysicalScreenPoint(
        1200 + wrong_intermediate.x,
        300 + wrong_intermediate.y,
    )

    assert wrong_intermediate == PhysicalScreenPoint(1316, 138)
    assert wrong_final == PhysicalScreenPoint(2516, 438)
    assert wrong_final != corrected.physical_screen


def test_mapping_fails_closed_when_reverse_loses_one_logical_pixel() -> None:
    mapping = map_logical_client_point(
        123,
        LogicalClientPoint(400, 50),
        _LossyReverseTransform(),
    )

    assert not mapping.exact_round_trip
    assert mapping.reverse_logical_client == LogicalClientPoint(401, 50)
    with pytest.raises(CoordinateMappingError, match="did not round-trip"):
        require_exact_round_trip(mapping)


def test_no_input_report_cross_checks_physical_capture_coordinate() -> None:
    tool = _load_mapping_tool()
    mapping = map_logical_client_point(
        123,
        LogicalClientPoint(400, 50),
        _AffineTransform(),
    )
    frame = Frame.from_raw(
        RawFrame(
            payload=bytes(1005 * 1078 * 4),
            width=1005,
            height=1078,
            pixel_format=PixelFormat.BGRA8888,
        ),
        frame_id=1,
        captured_monotonic_s=1.0,
    )
    physical_client = (
        mapping.physical_screen.x - mapping.physical_client_origin.x,
        mapping.physical_screen.y - mapping.physical_client_origin.y,
    )

    report = tool._point_report(
        label="wheel",
        logical_client=(400, 50),
        mapping=mapping,
        physical_client=physical_client,
        physical_capture_origin=mapping.physical_client_origin.pair,
        physical_capture_size=(1005, 1078),
        comparison={"production_uses_comparison": False},
        frame=frame,
    )

    assert report["reviewed_coordinate_space"] == "runelite_target_logical_client"
    cross_check = report["capture_cross_check"]
    assert cross_check["reviewed_logical_capture_point"] == [400, 50]
    assert cross_check["reverse_logical_client_bridge"] == [400, 50]
    assert cross_check["logical_round_trip_agreement"] is True
    assert cross_check["logical_point_inside_captured_frame"] is True
    assert cross_check["windows_physical_client_exact_arithmetic_agreement"] is True
    assert cross_check["windows_physical_client_rounding_delta"] == [0, 0]
    assert cross_check["physical_client_is_capture_pixel_index"] is False
    assert cross_check["visual_ui_match_claimed"] is False
    assert cross_check["visual_review_required"] is True
    physical = report["physical_screen_cross_check"]
    assert physical["capture_screen_origin"] == [1200, 300]
    assert physical["final_physical_screen_point"] == [2000, 400]
    assert physical["physical_overlay_pixel"] == [800, 100]
    assert physical["physical_overlay_point_inside_capture"] is True


def test_annotated_capture_marks_only_a_copy_at_logical_points() -> None:
    tool = _load_mapping_tool()
    original = bytes(1005 * 1078 * 4)
    frame = Frame.from_raw(
        RawFrame(
            payload=original,
            width=1005,
            height=1078,
            pixel_format=PixelFormat.BGRA8888,
        ),
        frame_id=1,
        captured_monotonic_s=1.0,
    )

    annotated = tool._annotated_capture_payload(frame)

    compass_offset = (49 * frame.width + 608) * 4
    wheel_offset = (50 * frame.width + 400) * 4
    assert annotated[compass_offset : compass_offset + 4] == bytes((255, 0, 255, 255))
    assert annotated[wheel_offset : wheel_offset + 4] == bytes((255, 255, 0, 255))
    assert frame.payload == original


def test_physical_overlay_marks_final_screen_minus_capture_origin() -> None:
    tool = _load_mapping_tool()
    original = bytes(1005 * 1078 * 4)

    annotated = tool._annotated_payload(
        original,
        width=1005,
        height=1078,
        points=(("compass", (760, 61)), ("wheel", (500, 63))),
    )

    compass_offset = (61 * 1005 + 760) * 4
    wheel_offset = (63 * 1005 + 500) * 4
    assert annotated[compass_offset : compass_offset + 4] == bytes(
        (255, 0, 255, 255)
    )
    assert annotated[wheel_offset : wheel_offset + 4] == bytes(
        (255, 255, 0, 255)
    )
    assert original == bytes(len(original))


def test_mapping_diagnostic_rejects_aliases_and_exclusive_write_preserves(
    tmp_path: Path,
) -> None:
    tool = _load_mapping_tool()
    target = tmp_path / "evidence.bin"

    with pytest.raises(ValueError, match="distinct"):
        tool._validate_output_paths((target, tmp_path / "." / "evidence.bin"))

    tool._write_exclusive(target, b"first")
    with pytest.raises(FileExistsError):
        tool._write_exclusive(target, b"second")
    assert target.read_bytes() == b"first"


def test_mapping_diagnostic_writes_report_last_and_skips_it_after_image_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = _load_mapping_tool()
    image_one = tmp_path / "one.bmp"
    image_two = tmp_path / "two.bmp"
    report = tmp_path / "report.json"
    calls: list[Path] = []

    def fail_second(path: Path, _payload: bytes) -> None:
        calls.append(path)
        if path == image_two:
            raise OSError("image failed")

    monkeypatch.setattr(tool, "_write_exclusive", fail_second)
    with pytest.raises(OSError, match="image failed"):
        tool._write_artifacts(
            ((image_one, b"one"), (image_two, b"two")),
            (report, b"report"),
        )
    assert calls == [image_one, image_two]
    assert report not in calls

    calls.clear()
    monkeypatch.setattr(
        tool,
        "_write_exclusive",
        lambda path, _payload: calls.append(path),
    )
    tool._write_artifacts(((image_one, b"one"),), (report, b"report"))
    assert calls == [image_one, report]


def test_mapping_diagnostic_rejects_origin_change_during_point_mapping() -> None:
    tool = _load_mapping_tool()
    mapping = map_logical_client_point(
        123,
        LogicalClientPoint(608, 49),
        _AffineTransform(),
    )

    tool._require_mapping_origin(
        mapping,
        (1200, 300),
        phase="while mapping the reviewed compass point",
    )
    with pytest.raises(RuntimeError, match="origin changed.*expected.*got"):
        tool._require_mapping_origin(
            mapping,
            (1201, 300),
            phase="while mapping the reviewed compass point",
        )


def test_mapping_diagnostic_rejects_foreign_root_before_capture() -> None:
    tool = _load_mapping_tool()

    tool._require_owned_roots((123, 123), 123, phase="before physical capture")
    with pytest.raises(RuntimeError, match="not owned.*before physical capture"):
        tool._require_owned_roots((123, 999), 123, phase="before physical capture")
