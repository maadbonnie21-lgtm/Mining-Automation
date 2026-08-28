#!/usr/bin/env python3
"""Audit Issue #31 pointer coordinates natively without sending any input.

The tool captures one RuneLite client frame, but it never moves the cursor,
clicks, scrolls, presses a key, or constructs ``WindowsCameraControl``.  It
records every coordinate space in the corrected origin-based mapping, its
exact reverse round trip, two comparison-only legacy candidates, native DPI
awareness/scale facts, and the final point's physical capture coordinate.
An optional BMP provides the non-input visual cross-check required before any
acceptance-grade camera trial.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import subprocess
import sys
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mining_automation.capture import CaptureError, CaptureSource, Frame  # noqa: E402
from mining_automation.capture.windows import (  # noqa: E402
    DEFAULT_TITLE_SUBSTRING,
    WindowsCaptureBackend,
)
from mining_automation.validation.camera_coordinates import (  # noqa: E402
    CameraCoordinateMapping,
)
from mining_automation.validation.camera_plan import (  # noqa: E402
    REVIEWED_CAMERA_WHEEL_POINT,
    REVIEWED_COMPASS_POINT,
)
from mining_automation.validation.windows_camera import RealWindowsCameraApi  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[1]
_POINTS = (
    ("compass", REVIEWED_COMPASS_POINT),
    ("wheel", REVIEWED_CAMERA_WHEEL_POINT),
)
_BMP_FILE_HEADER_SIZE = 14
_BMP_INFO_HEADER_SIZE = 40


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--title",
        default=DEFAULT_TITLE_SUBSTRING,
        help=f"case-insensitive window title substring (default: {DEFAULT_TITLE_SUBSTRING!r})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="write the no-input JSON report exclusively to this path",
    )
    parser.add_argument(
        "--save-capture",
        type=Path,
        help=(
            "optionally save the captured RuneLite client as a private BMP for "
            "visual point cross-checking"
        ),
    )
    parser.add_argument(
        "--save-annotated-capture",
        type=Path,
        help=(
            "optionally save the private PrintWindow logical raster with "
            "crosshairs at reviewed target-logical coordinates"
        ),
    )
    parser.add_argument(
        "--save-physical-screen-capture",
        type=Path,
        help=(
            "optionally save a no-input physical-screen capture of the exact "
            "foreground RuneLite client rectangle"
        ),
    )
    parser.add_argument(
        "--save-physical-screen-annotated-capture",
        type=Path,
        help=(
            "optionally save the physical RuneLite client screen capture with "
            "crosshairs at final mapped physical pointer points"
        ),
    )
    return parser


def _mapping_dict(mapping: CameraCoordinateMapping) -> dict[str, object]:
    return {
        "hwnd": mapping.hwnd,
        "logical_client": list(mapping.logical_client.pair),
        "physical_client_origin": list(mapping.physical_client_origin.pair),
        "target_logical_screen_origin": list(
            mapping.target_logical_screen_origin.pair
        ),
        "target_logical_screen": list(mapping.target_logical_screen.pair),
        "physical_screen": list(mapping.physical_screen.pair),
        "reverse_target_logical_screen": list(
            mapping.reverse_target_logical_screen.pair
        ),
        "reverse_logical_client": list(mapping.reverse_logical_client.pair),
        "exact_round_trip": mapping.exact_round_trip,
    }


def _require_mapping_origin(
    mapping: CameraCoordinateMapping,
    expected: tuple[int, int],
    *,
    phase: str,
) -> None:
    if mapping.physical_client_origin.pair != expected:
        raise RuntimeError(
            f"target physical client origin changed {phase}: "
            f"expected {expected}, got {mapping.physical_client_origin.pair}"
        )


def _require_owned_roots(
    roots: tuple[int | None, ...],
    hwnd: int,
    *,
    phase: str,
) -> None:
    if any(root != hwnd for root in roots):
        raise RuntimeError(
            "a reviewed pointer point was not owned by the target RuneLite "
            f"top-level window {phase}"
        )


def _neighborhood_sha256(
    frame: Frame,
    point: tuple[int, int],
    *,
    radius: int = 4,
) -> str | None:
    x, y = point
    if not (0 <= x < frame.width and 0 <= y < frame.height):
        return None
    bytes_per_pixel = frame.pixel_format.bytes_per_pixel
    rows = bytearray()
    for row_y in range(max(0, y - radius), min(frame.height, y + radius + 1)):
        start_x = max(0, x - radius)
        end_x = min(frame.width, x + radius + 1)
        start = (row_y * frame.width + start_x) * bytes_per_pixel
        end = (row_y * frame.width + end_x) * bytes_per_pixel
        rows.extend(frame.payload[start:end])
    return hashlib.sha256(rows).hexdigest()


def _annotated_payload(
    payload: bytes,
    *,
    width: int,
    height: int,
    points: tuple[tuple[str, tuple[int, int]], ...],
) -> bytes:
    """Mark named points on a detached four-channel BGRA payload."""

    if len(payload) != width * height * 4:
        raise ValueError("annotated mapping capture requires exact BGRA payload size")
    annotated = bytearray(payload)
    colors = {
        "compass": (255, 0, 255, 255),  # BGRA magenta
        "wheel": (255, 255, 0, 255),  # BGRA cyan
    }
    for label, (x, y) in points:
        color = colors[label]
        for delta in range(-8, 9):
            for point_x, point_y in ((x + delta, y), (x, y + delta)):
                if not (0 <= point_x < width and 0 <= point_y < height):
                    continue
                offset = (point_y * width + point_x) * 4
                annotated[offset : offset + 4] = bytes(color)
    return bytes(annotated)


def _annotated_capture_payload(frame: Frame) -> bytes:
    """Mark reviewed target-logical points on the PrintWindow logical raster."""

    if frame.pixel_format.bytes_per_pixel != 4:
        raise ValueError("annotated mapping capture requires a four-channel frame")
    return _annotated_payload(
        frame.payload,
        width=frame.width,
        height=frame.height,
        points=_POINTS,
    )


def _bgra_bmp_bytes(
    *,
    width: int,
    height: int,
    bgra_payload: bytes,
) -> bytes:
    expected = width * height * 4
    if len(bgra_payload) != expected:
        raise ValueError(
            f"payload size {len(bgra_payload)} != expected {expected} "
            f"for {width}x{height}"
        )
    file_header = struct.pack(
        "<2sIHHI",
        b"BM",
        _BMP_FILE_HEADER_SIZE + _BMP_INFO_HEADER_SIZE + len(bgra_payload),
        0,
        0,
        _BMP_FILE_HEADER_SIZE + _BMP_INFO_HEADER_SIZE,
    )
    info_header = struct.pack(
        "<IiiHHIIiiII",
        _BMP_INFO_HEADER_SIZE,
        width,
        -height,
        1,
        32,
        0,
        len(bgra_payload),
        0,
        0,
        0,
        0,
    )
    return file_header + info_header + bgra_payload


def _point_report(
    *,
    label: str,
    logical_client: tuple[int, int],
    mapping: CameraCoordinateMapping,
    physical_client: tuple[int, int],
    physical_capture_origin: tuple[int, int],
    physical_capture_size: tuple[int, int],
    comparison: dict[str, object],
    frame: Frame,
) -> dict[str, object]:
    physical_origin_subtraction = (
        mapping.physical_screen.x - mapping.physical_client_origin.x,
        mapping.physical_screen.y - mapping.physical_client_origin.y,
    )
    logical_capture_point = logical_client
    logical_in_capture = (
        0 <= logical_capture_point[0] < frame.width
        and 0 <= logical_capture_point[1] < frame.height
    )
    physical_client_delta = (
        physical_client[0] - physical_origin_subtraction[0],
        physical_client[1] - physical_origin_subtraction[1],
    )
    physical_overlay_point = (
        mapping.physical_screen.x - physical_capture_origin[0],
        mapping.physical_screen.y - physical_capture_origin[1],
    )
    physical_overlay_inside = (
        0 <= physical_overlay_point[0] < physical_capture_size[0]
        and 0 <= physical_overlay_point[1] < physical_capture_size[1]
    )
    return {
        "label": label,
        "reviewed_coordinate_space": "runelite_target_logical_client",
        "reviewed_logical_client": list(logical_client),
        "corrected_mapping": _mapping_dict(mapping),
        "comparison_only_candidates": comparison,
        "capture_cross_check": {
            "captured_frame_coordinate_space": "runelite_target_logical_client",
            "reviewed_logical_capture_point": list(logical_capture_point),
            "reverse_logical_client_bridge": list(
                mapping.reverse_logical_client.pair
            ),
            "logical_round_trip_agreement": (
                mapping.reverse_logical_client.pair == logical_capture_point
            ),
            "logical_point_inside_captured_frame": logical_in_capture,
            "pixel_neighborhood_radius": 4,
            "pixel_neighborhood_sha256": _neighborhood_sha256(
                frame,
                logical_capture_point,
            ),
            "windows_physical_client_cross_check": list(physical_client),
            "physical_screen_minus_origin_arithmetic_only": list(
                physical_origin_subtraction
            ),
            "windows_physical_client_exact_arithmetic_agreement": (
                physical_client == physical_origin_subtraction
            ),
            "windows_physical_client_rounding_delta": list(physical_client_delta),
            "windows_physical_client_note": (
                "ScreenToClient is the native physical-client cross-check; simple "
                "origin subtraction can differ by one device pixel under DPI rounding"
            ),
            "physical_client_is_capture_pixel_index": False,
            "visual_ui_match_claimed": False,
            "visual_review_required": True,
        },
        "physical_screen_cross_check": {
            "capture_coordinate_space": "physical_screen_client_crop",
            "capture_screen_origin": list(physical_capture_origin),
            "capture_size": list(physical_capture_size),
            "final_physical_screen_point": list(mapping.physical_screen.pair),
            "physical_overlay_pixel": list(physical_overlay_point),
            "physical_overlay_point_inside_capture": physical_overlay_inside,
            "physical_overlay_pixel_is_final_screen_minus_origin": True,
            "visual_ui_match_claimed": False,
            "visual_review_required": True,
        },
    }


def _git_state() -> tuple[str, bool]:
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=_REPO_ROOT,
        text=True,
    ).strip()
    status = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=_REPO_ROOT,
        text=True,
    )
    return head, not bool(status.strip())


def _write_exclusive(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        # A hard-link publish is atomic and refuses an existing final path.
        os.link(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _validate_output_paths(paths: tuple[Path | None, ...]) -> None:
    requested = tuple(path.resolve() for path in paths if path is not None)
    path_keys = tuple(os.path.normcase(str(path)) for path in requested)
    if len(set(path_keys)) != len(path_keys):
        raise ValueError("diagnostic output paths must resolve to distinct files")
    existing = next((path for path in requested if path.exists()), None)
    if existing is not None:
        raise FileExistsError(existing)


def _write_artifacts(
    images: tuple[tuple[Path, bytes], ...],
    report: tuple[Path, bytes] | None,
) -> None:
    """Publish every image first and the self-describing report last."""

    for path, payload in images:
        _write_exclusive(path, payload)
    if report is not None:
        _write_exclusive(*report)


def _report_bytes(report: dict[str, object]) -> bytes:
    return (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    command_args = list(sys.argv[1:] if argv is None else argv)
    args = _build_parser().parse_args(command_args)
    output_paths = (
        args.output,
        args.save_capture,
        args.save_annotated_capture,
        args.save_physical_screen_capture,
        args.save_physical_screen_annotated_capture,
    )
    try:
        _validate_output_paths(output_paths)
    except (FileExistsError, OSError, ValueError) as exc:
        print(
            f"Refusing unsafe diagnostic output targets: {exc}",
            file=sys.stderr,
        )
        return 2

    backend: WindowsCaptureBackend | None = None
    source: CaptureSource | None = None
    try:
        backend = WindowsCaptureBackend(title_substring=args.title)
        source = CaptureSource(backend, max_consecutive_failures=1)
        source.open()
        frame = source.capture()
        selected = backend.selected_window
        if selected is None:
            raise RuntimeError("capture succeeded without a selected RuneLite window")

        native = RealWindowsCameraApi()
        native.declare_dpi_awareness()
        dpi = native.dpi_environment(selected.hwnd)
        if not native.is_window(selected.hwnd):
            raise RuntimeError("target RuneLite window disappeared before physical capture")
        if native.foreground_window() != selected.hwnd:
            raise RuntimeError(
                "target RuneLite window must be foreground for physical UI cross-check"
            )
        physical_size_before = native.client_size(selected.hwnd)
        if physical_size_before != dpi.physical_client_size:
            raise RuntimeError(
                "target physical client geometry changed before screen capture"
            )
        physical_origin_before_mapping = native.pointer_mapping(selected.hwnd, 0, 0)
        if not physical_origin_before_mapping.exact_round_trip:
            raise RuntimeError("physical client origin did not round-trip exactly")
        physical_origin_before = (
            physical_origin_before_mapping.physical_client_origin.pair
        )
        if physical_origin_before_mapping.physical_screen.pair != physical_origin_before:
            raise RuntimeError(
                "round-tripped zero point disagreed with native physical client origin"
            )

        point_inputs: list[
            tuple[
                str,
                tuple[int, int],
                CameraCoordinateMapping,
                tuple[int, int],
                dict[str, object],
                int | None,
            ]
        ] = []
        for label, logical_client in _POINTS:
            mapping = native.pointer_mapping(
                selected.hwnd,
                logical_client[0],
                logical_client[1],
            )
            if not mapping.exact_round_trip:
                raise RuntimeError(
                    f"reviewed {label} point did not round-trip exactly"
                )
            _require_mapping_origin(
                mapping,
                physical_origin_before,
                phase=f"while mapping the reviewed {label} point",
            )
            physical_client = native.physical_screen_to_physical_client(
                selected.hwnd,
                mapping.physical_screen.x,
                mapping.physical_screen.y,
            )
            point_inputs.append(
                (
                    label,
                    logical_client,
                    mapping,
                    physical_client,
                    native.mapping_candidate_comparison(
                        selected.hwnd,
                        logical_client[0],
                        logical_client[1],
                    ),
                    native.root_window_at_point(*mapping.physical_screen.pair),
                )
            )

        roots_before = tuple(item[5] for item in point_inputs)
        _require_owned_roots(
            roots_before,
            selected.hwnd,
            phase="before physical capture",
        )
        if not native.is_window(selected.hwnd):
            raise RuntimeError("target RuneLite window disappeared before BitBlt")
        if native.foreground_window() != selected.hwnd:
            raise RuntimeError("target RuneLite window lost foreground before BitBlt")
        if native.client_size(selected.hwnd) != physical_size_before:
            raise RuntimeError("target physical client geometry changed before BitBlt")
        origin_at_capture = native.pointer_mapping(selected.hwnd, 0, 0)
        if not origin_at_capture.exact_round_trip:
            raise RuntimeError("physical client origin did not round-trip before BitBlt")
        if (
            origin_at_capture.physical_client_origin.pair != physical_origin_before
            or origin_at_capture.physical_screen.pair != physical_origin_before
        ):
            raise RuntimeError("target physical client origin changed before BitBlt")

        physical_payload = native.capture_physical_screen_rect(
            physical_origin_before[0],
            physical_origin_before[1],
            physical_size_before[0],
            physical_size_before[1],
        )

        if not native.is_window(selected.hwnd):
            raise RuntimeError("target RuneLite window disappeared during physical capture")
        physical_size_after = native.client_size(selected.hwnd)
        physical_origin_after_mapping = native.pointer_mapping(
            selected.hwnd,
            0,
            0,
        )
        if not physical_origin_after_mapping.exact_round_trip:
            raise RuntimeError(
                "physical client origin no longer round-tripped after capture"
            )
        physical_origin_after = physical_origin_after_mapping.physical_client_origin.pair
        if physical_origin_after_mapping.physical_screen.pair != physical_origin_after:
            raise RuntimeError(
                "round-tripped zero point no longer agreed with native client origin"
            )
        foreground_after = native.foreground_window()
        roots_after = tuple(
            native.root_window_at_point(*item[2].physical_screen.pair)
            for item in point_inputs
        )
        if physical_size_after != physical_size_before:
            raise RuntimeError(
                "target physical client geometry changed during screen capture"
            )
        if physical_origin_after != physical_origin_before:
            raise RuntimeError("target physical client origin changed during screen capture")
        if foreground_after != selected.hwnd:
            raise RuntimeError("target RuneLite window lost foreground during screen capture")
        _require_owned_roots(
            roots_after,
            selected.hwnd,
            phase="after physical capture",
        )

        physical_overlay_points = tuple(
            (
                item[0],
                (
                    item[2].physical_screen.x - physical_origin_before[0],
                    item[2].physical_screen.y - physical_origin_before[1],
                ),
            )
            for item in point_inputs
        )
        if any(
            not (
                0 <= point[0] < physical_size_before[0]
                and 0 <= point[1] < physical_size_before[1]
            )
            for _label, point in physical_overlay_points
        ):
            raise RuntimeError(
                "a final physical pointer point fell outside the captured "
                "RuneLite client rectangle"
            )
        physical_annotated_payload = _annotated_payload(
            physical_payload,
            width=physical_size_before[0],
            height=physical_size_before[1],
            points=physical_overlay_points,
        )
        point_reports = [
            _point_report(
                label=label,
                logical_client=logical_client,
                mapping=mapping,
                physical_client=physical_client,
                physical_capture_origin=physical_origin_before,
                physical_capture_size=physical_size_before,
                comparison=comparison,
                frame=frame,
            )
            for (
                label,
                logical_client,
                mapping,
                physical_client,
                comparison,
                _root_before,
            ) in point_inputs
        ]
        git_head, worktree_clean = _git_state()
        report: dict[str, object] = {
            "schema_version": 2,
            "diagnostic": "issue31-camera-pointer-mapping-no-input",
            "captured_at_utc": datetime.now(UTC).isoformat(),
            "command_argv": [sys.executable, str(Path(__file__).resolve()), *command_args],
            "git_head_sha": git_head,
            "worktree_clean": worktree_clean,
            "input_boundary": {
                "input_events_sent": 0,
                "cursor_moved": False,
                "mouse_buttons_sent": False,
                "wheel_events_sent": False,
                "key_events_sent": False,
            },
            "window": {
                "hwnd": selected.hwnd,
                "title": selected.title,
                "class_name": selected.class_name,
                "selected_geometry": [selected.client_width, selected.client_height],
                "native_physical_client_geometry": list(dpi.physical_client_size),
                "estimated_target_logical_client_geometry": list(
                    dpi.estimated_target_logical_client_size
                ),
            },
            "dpi": asdict(dpi),
            "capture": {
                "coordinate_space": "printwindow_target_logical_raster",
                "width": frame.width,
                "height": frame.height,
                "pixel_format": frame.pixel_format.value,
                "payload_sha256": hashlib.sha256(frame.payload).hexdigest(),
                "saved_bmp": (
                    str(args.save_capture.resolve())
                    if args.save_capture is not None
                    else None
                ),
                "saved_annotated_bmp": (
                    str(args.save_annotated_capture.resolve())
                    if args.save_annotated_capture is not None
                    else None
                ),
                "annotated_points": {
                    "compass_magenta": list(REVIEWED_COMPASS_POINT),
                    "wheel_cyan": list(REVIEWED_CAMERA_WHEEL_POINT),
                },
                "private_pixels_committable": False,
            },
            "physical_screen_capture": {
                "coordinate_space": "physical_screen_client_crop",
                "screen_origin": list(physical_origin_before),
                "width": physical_size_before[0],
                "height": physical_size_before[1],
                "payload_sha256": hashlib.sha256(physical_payload).hexdigest(),
                "annotated_payload_sha256": hashlib.sha256(
                    physical_annotated_payload
                ).hexdigest(),
                "saved_bmp": (
                    str(args.save_physical_screen_capture.resolve())
                    if args.save_physical_screen_capture is not None
                    else None
                ),
                "saved_annotated_bmp": (
                    str(args.save_physical_screen_annotated_capture.resolve())
                    if args.save_physical_screen_annotated_capture is not None
                    else None
                ),
                "annotated_points": {
                    f"{label}_{'magenta' if label == 'compass' else 'cyan'}": list(
                        point
                    )
                    for label, point in physical_overlay_points
                },
                "stability": {
                    "foreground_hwnd_before": selected.hwnd,
                    "foreground_hwnd_after": foreground_after,
                    "client_size_before": list(physical_size_before),
                    "client_size_after": list(physical_size_after),
                    "screen_origin_before": list(physical_origin_before),
                    "screen_origin_after": list(physical_origin_after),
                    "point_root_hwnds_before": list(roots_before),
                    "point_root_hwnds_after": list(roots_after),
                    "exactly_stable_and_target_owned": True,
                },
                "private_pixels_committable": False,
            },
            "coordinate_contract": {
                "production_sequence": [
                    "ClientToScreen(hwnd, physical_client_origin=(0,0))",
                    "PhysicalToLogicalPointForPerMonitorDPI(hwnd, physical_screen_origin)",
                    "add reviewed target-logical client delta",
                    "LogicalToPhysicalPointForPerMonitorDPI(hwnd, target_logical_screen)",
                    "PhysicalToLogicalPointForPerMonitorDPI(hwnd, physical_screen)",
                    "subtract target-logical screen origin",
                    "require exact target-logical client round trip",
                ],
                "caller_requirement": "per_monitor_aware",
                "production_uses_legacy_candidates": False,
            },
            "points": point_reports,
        }
        report_payload = _report_bytes(report)
        image_artifacts: list[tuple[Path, bytes]] = []
        if args.save_capture is not None:
            image_artifacts.append(
                (
                    args.save_capture,
                    _bgra_bmp_bytes(
                        width=frame.width,
                        height=frame.height,
                        bgra_payload=frame.payload,
                    ),
                )
            )
        if args.save_annotated_capture is not None:
            image_artifacts.append(
                (
                    args.save_annotated_capture,
                    _bgra_bmp_bytes(
                        width=frame.width,
                        height=frame.height,
                        bgra_payload=_annotated_capture_payload(frame),
                    ),
                )
            )
        if args.save_physical_screen_capture is not None:
            image_artifacts.append(
                (
                    args.save_physical_screen_capture,
                    _bgra_bmp_bytes(
                        width=physical_size_before[0],
                        height=physical_size_before[1],
                        bgra_payload=physical_payload,
                    ),
                )
            )
        if args.save_physical_screen_annotated_capture is not None:
            image_artifacts.append(
                (
                    args.save_physical_screen_annotated_capture,
                    _bgra_bmp_bytes(
                        width=physical_size_before[0],
                        height=physical_size_before[1],
                        bgra_payload=physical_annotated_payload,
                    ),
                )
            )
        report_artifact = (
            (args.output, report_payload) if args.output is not None else None
        )
        _write_artifacts(tuple(image_artifacts), report_artifact)
    except (
        CaptureError,
        OSError,
        RuntimeError,
        ValueError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"NO-INPUT mapping diagnostic failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if source is not None:
            source.close()
        elif backend is not None:
            backend.close()

    print(report_payload.decode("utf-8"), end="")
    print(f"Report SHA-256: {hashlib.sha256(report_payload).hexdigest()}")
    if args.output is not None:
        print(f"Report: {args.output}")
    if args.save_capture is not None:
        print(f"Private capture: {args.save_capture}")
    if args.save_annotated_capture is not None:
        print(f"Private logical annotated capture: {args.save_annotated_capture}")
    if args.save_physical_screen_capture is not None:
        print(f"Private physical screen capture: {args.save_physical_screen_capture}")
    if args.save_physical_screen_annotated_capture is not None:
        print(
            "Private physical annotated capture: "
            f"{args.save_physical_screen_annotated_capture}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
