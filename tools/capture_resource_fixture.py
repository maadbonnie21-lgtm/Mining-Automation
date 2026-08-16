#!/usr/bin/env python3
"""Record deliberate RuneLite frames as unreviewed resource-fixture drafts.

Development-only.  This tool never labels a rock and never overwrites an
existing case.  It captures through the production Windows backend, writes the
owned raw frame bytes, and creates a BMP preview plus JSON draft for later
human review.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mining_automation.capture import CaptureError, CaptureSource  # noqa: E402
from mining_automation.capture.windows import (  # noqa: E402
    DEFAULT_TITLE_SUBSTRING,
    WindowsCaptureBackend,
)
from mining_automation.perception.resource_fixtures import (  # noqa: E402
    write_resource_fixture_draft,
)


def _positive_int(value: str) -> int:
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return result


def _non_negative_float(value: str) -> float:
    result = float(value)
    if result < 0:
        raise argparse.ArgumentTypeError("value must be non-negative")
    return result


def _parse_key_value(value: str) -> tuple[str, str]:
    key, separator, item = value.partition("=")
    if not separator or not key.strip() or not item.strip():
        raise argparse.ArgumentTypeError("provenance must be KEY=VALUE")
    return key.strip(), item.strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--output", type=Path, required=True, help="dataset root directory")
    parser.add_argument("--dataset-id", required=True, help="stable replay dataset identifier")
    parser.add_argument("--case-id", required=True, help="base case identifier")
    parser.add_argument(
        "--location-id",
        default="varrock-east-mine",
        help="supported location identifier (default: varrock-east-mine)",
    )
    parser.add_argument(
        "--title",
        default=DEFAULT_TITLE_SUBSTRING,
        help=f"RuneLite title substring (default: {DEFAULT_TITLE_SUBSTRING!r})",
    )
    parser.add_argument(
        "--frames",
        type=_positive_int,
        default=1,
        help="number of deliberate captures (default: 1)",
    )
    parser.add_argument(
        "--interval",
        type=_non_negative_float,
        default=1.0,
        help="seconds between captures (default: 1.0)",
    )
    parser.add_argument("--tag", action="append", default=[], help="repeatable fixture tag")
    parser.add_argument(
        "--provenance",
        action="append",
        default=[],
        type=_parse_key_value,
        metavar="KEY=VALUE",
        help="repeatable provenance field",
    )
    parser.add_argument("--notes", default="", help="review notes stored with every draft")
    return parser


def _case_id(base: str, index: int, total: int) -> str:
    return base if total == 1 else f"{base}-{index:03d}"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    try:
        backend = WindowsCaptureBackend(title_substring=args.title)
    except RuntimeError as exc:
        print(f"Cannot run resource capture here: {exc}", file=sys.stderr)
        return 2

    source = CaptureSource(backend, max_consecutive_failures=args.frames + 1)
    try:
        source.open()
    except CaptureError as exc:
        print(f"Could not open RuneLite capture: {exc}", file=sys.stderr)
        return 1

    failures = 0
    try:
        for index in range(1, args.frames + 1):
            case_id = _case_id(args.case_id, index, args.frames)
            try:
                frame = source.capture()
                selected = backend.selected_window
                provenance = dict(args.provenance)
                provenance.update(
                    {
                        "capture_backend": backend.name,
                        "capture_frame_id": str(frame.frame_id),
                        "capture_title_match": args.title,
                    }
                )
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
                    tags=tuple(args.tag),
                    provenance=provenance,
                    notes=args.notes,
                )
            except (CaptureError, OSError, ValueError) as exc:
                failures += 1
                print(f"[{index}/{args.frames}] FAILED {case_id}: {exc}", file=sys.stderr)
            else:
                print(
                    f"[{index}/{args.frames}] CAPTURED {case_id}: "
                    f"{frame.width}x{frame.height} {frame.pixel_format.value}"
                )
                print(f"  raw:     {paths.frame}")
                print(f"  preview: {paths.preview}")
                print(f"  draft:   {paths.draft}")
            if index < args.frames and args.interval:
                time.sleep(args.interval)
    finally:
        try:
            source.close()
        except CaptureError as exc:
            failures += 1
            print(f"Capture backend close failed: {exc}", file=sys.stderr)

    if failures:
        print(f"\nCompleted with {failures} failure(s); no failed case was labeled.")
        return 1
    print("\nAll captures written as unreviewed drafts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
