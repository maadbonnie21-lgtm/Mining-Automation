#!/usr/bin/env python3
"""Capture and summarize one live Varrock East iron validation frame.

Development-only. This tool deliberately keeps the production detector
unchanged. It captures through the production Windows backend, writes the same
unreviewed fixture draft used by the normal resource-fixture workflow, runs the
production detector, measures every configured scene anchor, optionally
compares the frame with the local calibration reference, and writes one JSON
report. The reference comparison is diagnostic evidence, not a release verdict.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mining_automation.capture import CaptureError, CaptureSource, Frame, PixelFormat  # noqa: E402
from mining_automation.capture.windows import (  # noqa: E402
    DEFAULT_TITLE_SUBSTRING,
    WindowsCaptureBackend,
)
from mining_automation.controlled_mining_runner import (  # noqa: E402
    DryRunWin32MiningInputDevice,
)
from mining_automation.perception import (  # noqa: E402
    build_varrock_east_iron_detector,
    load_varrock_east_iron_profile,
    measure_region_mean_rgb,
    write_resource_fixture_draft,
)

_DIAGNOSTIC_ZONES: dict[str, tuple[int, int, int, int]] = {
    "NW": (120, 180, 410, 450),
    "NE": (410, 180, 700, 450),
    "SW": (120, 450, 410, 720),
    "SE": (410, 450, 700, 720),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("diagnostics/varrock-east-iron"),
        help="diagnostic dataset root (default: diagnostics/varrock-east-iron)",
    )
    parser.add_argument(
        "--dataset-id",
        default="varrock-east-iron-v1",
        help="stable dataset identifier (default: varrock-east-iron-v1)",
    )
    parser.add_argument(
        "--case-id",
        help="case identifier; default is a UTC timestamped live-validation id",
    )
    parser.add_argument(
        "--location-id",
        default="varrock-east-mine",
        help="location identifier (default: varrock-east-mine)",
    )
    parser.add_argument(
        "--title",
        default=DEFAULT_TITLE_SUBSTRING,
        help=f"RuneLite title substring (default: {DEFAULT_TITLE_SUBSTRING!r})",
    )
    parser.add_argument(
        "--reference",
        type=Path,
        help=(
            "calibration raw frame; default is "
            "<output>/frames/available-01.raw when present"
        ),
    )
    parser.add_argument(
        "--notes",
        default="One-command live Varrock East resource validation.",
        help="notes stored with the unreviewed capture draft",
    )
    return parser


def _default_case_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"live-validation-{stamp}"


def _capture_frame(args: argparse.Namespace, case_id: str) -> tuple[Frame, dict[str, str]]:
    try:
        backend = WindowsCaptureBackend(title_substring=args.title)
    except RuntimeError as exc:
        raise RuntimeError(f"cannot run live validation here: {exc}") from exc

    window_probe = DryRunWin32MiningInputDevice()
    before = window_probe.verify_target_window(args.title)
    source = CaptureSource(backend, max_consecutive_failures=2)
    try:
        source.open()
        frame = source.capture()
        selected = backend.selected_window
        if selected is None or selected.hwnd != before.hwnd:
            raise RuntimeError(
                "capture backend selected a different RuneLite HWND: "
                f"expected {before.hwnd}, got {None if selected is None else selected.hwnd}"
            )
        after = window_probe.verify_target_window(args.title)
        if after != before:
            raise RuntimeError(
                "RuneLite HWND/window state changed during read-only capture: "
                f"before={before!r}, after={after!r}"
            )
        provenance = {
            "capture_backend": backend.name,
            "capture_frame_id": str(frame.frame_id),
            "capture_title_match": args.title,
            "validation_tool": "validate_varrock_east_live.py",
        }
        if selected is not None:
            provenance.update(
                {
                    "window_title": selected.title,
                    "window_class": selected.class_name,
                    "window_hwnd": str(selected.hwnd),
                }
            )
        if backend.current_dpi is not None:
            provenance["reported_dpi"] = str(backend.current_dpi)

        paths = write_resource_fixture_draft(
            frame,
            args.output,
            dataset_id=args.dataset_id,
            case_id=case_id,
            location_id=args.location_id,
            tags=("real", "live-validation"),
            provenance=provenance,
            notes=args.notes,
        )
    finally:
        source.close()

    written = {
        "raw": str(paths.frame),
        "preview": str(paths.preview),
        "draft": str(paths.draft),
    }
    return frame, written


def _observation_report(frame: Frame) -> list[dict[str, Any]]:
    detector = build_varrock_east_iron_detector()
    observations = detector.detect(frame)
    report: list[dict[str, Any]] = []
    for observation in observations:
        report.append(
            {
                "resource_id": observation.evidence.get("resource_id"),
                "kind": observation.kind,
                "confidence": float(observation.confidence),
                "reason": observation.evidence.get("reason"),
            }
        )
    return report


def _anchor_report(frame: Frame) -> tuple[list[dict[str, Any]], float]:
    profile = load_varrock_east_iron_profile()
    rows: list[dict[str, Any]] = []
    for anchor in profile.anchors:
        actual = measure_region_mean_rgb(
            frame,
            anchor.region,
            sample_step=profile.sample_step,
        )
        rows.append(
            {
                "anchor_id": anchor.anchor_id,
                "region": list(anchor.region),
                "expected_mean_rgb": [float(value) for value in anchor.signature.mean_rgb],
                "actual_mean_rgb": [float(value) for value in actual],
                "similarity": float(anchor.signature.similarity(actual)),
            }
        )
    return rows, float(profile.minimum_anchor_confidence)


def _region_metrics(
    reference: bytes,
    current: bytes,
    *,
    width: int,
    region: tuple[int, int, int, int],
) -> dict[str, float]:
    x1, y1, x2, y2 = region
    pixel_errors: list[float] = []
    rgb_error_sum = 0
    counts = {2: 0, 5: 0, 10: 0, 20: 0}

    for y in range(y1, y2):
        row_offset = y * width * 4
        for x in range(x1, x2):
            index = row_offset + x * 4
            # BGRA8888: compare B, G, R and deliberately ignore alpha.
            error_sum = (
                abs(reference[index] - current[index])
                + abs(reference[index + 1] - current[index + 1])
                + abs(reference[index + 2] - current[index + 2])
            )
            rgb_error_sum += error_sum
            pixel_error = error_sum / 3.0
            pixel_errors.append(pixel_error)
            for threshold in counts:
                if pixel_error <= threshold:
                    counts[threshold] += 1

    pixels = len(pixel_errors)
    if pixels == 0:
        raise ValueError(f"diagnostic region is empty: {region}")

    return {
        "rgb_mae": rgb_error_sum / (pixels * 3.0),
        "median_pixel_mae": float(statistics.median(pixel_errors)),
        "percent_pixel_mae_le_2": counts[2] * 100.0 / pixels,
        "percent_pixel_mae_le_5": counts[5] * 100.0 / pixels,
        "percent_pixel_mae_le_10": counts[10] * 100.0 / pixels,
        "percent_pixel_mae_le_20": counts[20] * 100.0 / pixels,
    }


def _reference_report(frame: Frame, reference_path: Path) -> dict[str, Any]:
    if not reference_path.exists():
        return {
            "available": False,
            "reference": str(reference_path),
            "reason": "reference raw frame does not exist",
        }
    if frame.pixel_format is not PixelFormat.BGRA8888:
        return {
            "available": False,
            "reference": str(reference_path),
            "reason": f"unsupported live pixel format: {frame.pixel_format.value}",
        }

    reference = reference_path.read_bytes()
    expected_bytes = frame.width * frame.height * 4
    if len(reference) != expected_bytes:
        return {
            "available": False,
            "reference": str(reference_path),
            "reason": (
                f"reference byte count {len(reference)} does not match live frame "
                f"byte count {expected_bytes}"
            ),
        }

    for name, (x1, y1, x2, y2) in _DIAGNOSTIC_ZONES.items():
        if x1 < 0 or y1 < 0 or x2 > frame.width or y2 > frame.height:
            return {
                "available": False,
                "reference": str(reference_path),
                "reason": f"diagnostic zone {name} is outside the live frame geometry",
            }

    zones = {
        name: _region_metrics(
            reference,
            frame.payload,
            width=frame.width,
            region=region,
        )
        for name, region in _DIAGNOSTIC_ZONES.items()
    }
    return {
        "available": True,
        "reference": str(reference_path),
        "note": "same-coordinate diagnostic only; not a production release threshold",
        "zones": zones,
    }


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"validation report already exists: {path}")
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _print_summary(report: dict[str, Any]) -> None:
    print(f"\nLIVE VALIDATION: {report['case_id']}")
    print(f"Capture: {report['frame']['width']}x{report['frame']['height']} "
          f"{report['frame']['pixel_format']}")

    print("\nProduction detector")
    for item in report["observations"]:
        confidence = float(item["confidence"])
        print(
            f"  {item['resource_id']} -> {item['kind']} "
            f"| confidence {confidence:.4f} | reason {item['reason']}"
        )

    floor = float(report["minimum_anchor_confidence"])
    print(f"\nScene anchors (required floor {floor:.4f})")
    for item in report["anchors"]:
        similarity = float(item["similarity"])
        print(f"  {item['anchor_id']}: {similarity:.4f}")

    reference = report["reference_comparison"]
    if not reference["available"]:
        print(f"\nReference comparison: unavailable ({reference['reason']})")
    else:
        print("\nSame-coordinate reference comparison")
        for name, metrics in reference["zones"].items():
            print(
                f"  {name}: RGB MAE {metrics['rgb_mae']:.2f}, "
                f"median {metrics['median_pixel_mae']:.2f}, "
                f"<=10 {metrics['percent_pixel_mae_le_10']:.2f}%"
            )

    print(f"\nRaw:     {report['files']['raw']}")
    print(f"Preview: {report['files']['preview']}")
    print(f"Draft:   {report['files']['draft']}")
    print(f"Report:  {report['files']['report']}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    case_id = args.case_id or _default_case_id()
    report_path = args.output / "reports" / f"{case_id}.json"
    if report_path.exists():
        print(f"Refusing to overwrite existing report: {report_path}", file=sys.stderr)
        return 2

    try:
        frame, files = _capture_frame(args, case_id)
        reference_path = args.reference or args.output / "frames" / "available-01.raw"
        observations = _observation_report(frame)
        anchors, anchor_floor = _anchor_report(frame)
        reference_comparison = _reference_report(frame, reference_path)

        report: dict[str, Any] = {
            "schema_version": 1,
            "case_id": case_id,
            "captured_at_utc": datetime.now(UTC).isoformat(),
            "frame": {
                "frame_id": frame.frame_id,
                "width": frame.width,
                "height": frame.height,
                "pixel_format": frame.pixel_format.value,
            },
            "observations": observations,
            "minimum_anchor_confidence": anchor_floor,
            "anchors": anchors,
            "reference_comparison": reference_comparison,
            "files": {**files, "report": str(report_path)},
        }
        _write_report(report_path, report)
    except (CaptureError, OSError, RuntimeError, ValueError) as exc:
        print(f"Live validation failed: {exc}", file=sys.stderr)
        return 1

    _print_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
