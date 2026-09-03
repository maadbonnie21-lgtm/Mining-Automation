#!/usr/bin/env python3
"""Local validation harness for the Windows RuneLite capture backend.

Development-only. This is not the production application and is not imported
by it -- per ``tools/README.md`` it is a thin script over the real typed
capture stack (`CaptureSource` + `WindowsCaptureBackend`), not a
reimplementation of capture logic.

Run this on a real Windows machine with RuneLite open:

    python tools/windows_capture_check.py
    python tools/windows_capture_check.py --title "RuneLite" --frames 20 --interval 0.5
    python tools/windows_capture_check.py --save-frame diagnostics/last.bmp
    python tools/windows_capture_check.py --frames 3 --interval 0.25 \
        --require-width 1005 --require-height 1078 --require-dpi 96 \
        --require-all-successful

It reports the selected window's identity, capture dimensions and pixel
format, frame id/timestamp progression through the real `CaptureSource`, and
a clear typed failure when the target is minimized or unavailable. Optional
``--require-*`` arguments turn this into a strict read-only environment gate:
a capture can succeed while the command still exits nonzero because the
observed window is outside the requested envelope. Passing ``--save-frame``
writes the last successful capture as a BMP -- opt-in only, and only ever the
RuneLite client's own content area, never the desktop or any other window.

This script itself cannot run on a non-Windows machine: `WindowsCaptureBackend`
defaults to `RealWin32Api`, which raises a clear `RuntimeError` immediately on
construction if `sys.platform != "win32"`, rather than failing confusingly
partway through.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mining_automation.capture import (  # noqa: E402
    CaptureError,
    CaptureFailureThresholdExceeded,
    CaptureSource,
    CaptureUnavailableError,
    Frame,
)
from mining_automation.capture.windows import (  # noqa: E402
    DEFAULT_TITLE_SUBSTRING,
    WindowsCaptureBackend,
)
from mining_automation.capture.windows.bmp import write_bgra_bmp  # noqa: E402


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--title",
        default=DEFAULT_TITLE_SUBSTRING,
        help=f"case-insensitive window title substring (default: {DEFAULT_TITLE_SUBSTRING!r})",
    )
    parser.add_argument(
        "--frames",
        type=_positive_int,
        default=10,
        help="number of captures to attempt (default: 10)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="seconds to wait between captures (default: 1.0)",
    )
    parser.add_argument(
        "--save-frame",
        type=Path,
        default=None,
        metavar="PATH",
        help="save the last successful frame as a BMP to PATH (opt-in; not saved by default)",
    )
    parser.add_argument(
        "--require-width",
        type=_positive_int,
        default=None,
        metavar="PX",
        help="fail closed unless every captured frame has exactly this width",
    )
    parser.add_argument(
        "--require-height",
        type=_positive_int,
        default=None,
        metavar="PX",
        help="fail closed unless every captured frame has exactly this height",
    )
    parser.add_argument(
        "--require-dpi",
        type=_positive_int,
        default=None,
        metavar="DPI",
        help="fail closed unless Windows reports exactly this DPI for the selected window",
    )
    parser.add_argument(
        "--require-all-successful",
        action="store_true",
        help="fail closed unless every requested capture succeeds",
    )
    return parser.parse_args(argv)


def _report_frame(index: int, total: int, frame: Frame) -> None:
    print(
        f"[{index}/{total}] OK   "
        f"frame_id={frame.frame_id} "
        f"{frame.width}x{frame.height} {frame.pixel_format.name} "
        f"t={frame.captured_monotonic_s:.3f}s"
    )


def _report_failure(index: int, total: int, exc: CaptureError) -> None:
    kind = "unavailable" if isinstance(exc, CaptureUnavailableError) else type(exc).__name__
    print(f"[{index}/{total}] FAIL {kind}: {exc}")


def _frame_matches_requirements(frame: Frame, args: argparse.Namespace) -> bool:
    return not (
        (args.require_width is not None and frame.width != args.require_width)
        or (args.require_height is not None and frame.height != args.require_height)
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.interval < 0:
        print("--interval cannot be negative", file=sys.stderr)
        return 2

    try:
        backend = WindowsCaptureBackend(title_substring=args.title)
    except RuntimeError as exc:
        print(f"Cannot run this tool here: {exc}", file=sys.stderr)
        return 1
    source = CaptureSource(backend, max_consecutive_failures=args.frames + 1)

    print(f"Windows RuneLite capture validation -- matching title: {args.title!r}")
    print(f"Attempting {args.frames} capture(s), {args.interval}s apart.\n")

    try:
        source.open()
    except CaptureError as exc:
        print(f"FAILED to open capture backend: {exc}", file=sys.stderr)
        return 1

    successes = 0
    last_frame: Frame | None = None
    frame_requirement_failures: list[str] = []

    for i in range(1, args.frames + 1):
        try:
            frame = source.capture()
        except CaptureFailureThresholdExceeded as exc:
            _report_failure(i, args.frames, exc)
            print(
                "\nToo many consecutive failures; stopping early. "
                "Check that RuneLite is running, visible, and not minimized.",
                file=sys.stderr,
            )
            break
        except CaptureError as exc:
            _report_failure(i, args.frames, exc)
        else:
            successes += 1
            last_frame = frame
            _report_frame(i, args.frames, frame)
            if not _frame_matches_requirements(frame, args):
                expected = (
                    f"{args.require_width if args.require_width is not None else '*'}x"
                    f"{args.require_height if args.require_height is not None else '*'}"
                )
                frame_requirement_failures.append(
                    f"frame {frame.frame_id}: observed {frame.width}x{frame.height}, "
                    f"required {expected}"
                )

        if i < args.frames:
            time.sleep(args.interval)

    selected = backend.selected_window
    dpi = backend.current_dpi
    source.close()

    print(f"\n{successes}/{args.frames} successful capture(s).")
    if selected is not None:
        print(
            f"Selected window: hwnd={selected.hwnd} "
            f"title={selected.title!r} class={selected.class_name!r}"
        )
        print(f"Reported DPI: {dpi}")
    else:
        print("No window is currently selected.")

    requirements_ok = True
    if args.require_all_successful and successes != args.frames:
        requirements_ok = False
        print(
            f"REQUIREMENT FAILED: expected {args.frames}/{args.frames} successful captures, "
            f"observed {successes}/{args.frames}."
        )
    if frame_requirement_failures:
        requirements_ok = False
        for failure in frame_requirement_failures:
            print(f"REQUIREMENT FAILED: {failure}")
    if args.require_dpi is not None and dpi != args.require_dpi:
        requirements_ok = False
        print(f"REQUIREMENT FAILED: observed DPI {dpi!r}, required {args.require_dpi}.")

    if args.save_frame is not None:
        if last_frame is None:
            print("\n--save-frame requested but no frame was captured; nothing to save.")
        else:
            write_bgra_bmp(
                args.save_frame,
                width=last_frame.width,
                height=last_frame.height,
                bgra_payload=last_frame.payload,
            )
            print(f"\nSaved diagnostic frame to {args.save_frame}")

    passed = successes > 0 and requirements_ok
    if passed:
        print("\nREAD-ONLY CAPTURE PREFLIGHT: PASS")
    else:
        print("\nREAD-ONLY CAPTURE PREFLIGHT: STOP")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
