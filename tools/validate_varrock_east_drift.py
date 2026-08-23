#!/usr/bin/env python3
"""Validate the production detector against a local directory of real frames.

Development-only. Issue #18.

The 36 real camera-drift captures live only on the owner's machine under
``diagnostics/`` and are deliberately not committed. This command points the
unmodified production detector at such a directory and reports, per frame,
whether every profiled rock came back UNCERTAIN and whether any false
definitive target appeared.

It does not modify the detector, the profile, or any committed fixture, and it
writes nothing back into the repository.

Expected drift-set result: every frame UNCERTAIN for all four rocks, zero false
definitive targets.

    python tools/validate_varrock_east_drift.py --frames diagnostics/drift-frames
    python tools/validate_varrock_east_drift.py --frames <dir> --expect definitive
    python tools/validate_varrock_east_drift.py --frames <dir> --report out.json

``--expect uncertain`` (the default) is the drift gate. ``--expect definitive``
is the reacquisition gate: point it at frames captured from a restored
supported view, or after an ordinary RuneLite restart, and every frame must
come back definitive.

Raw frames are read as ``.raw`` (or ``.raw.gz``) BGRA payloads matching the
profile geometry, which is the format ``write_resource_fixture_draft`` already
produces, so captures taken with ``validate_varrock_east_live.py`` can be fed
straight in.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mining_automation.capture import Frame, RawFrame  # noqa: E402
from mining_automation.perception import (  # noqa: E402
    ResourceVisualState,
    build_varrock_east_iron_detector,
    load_varrock_east_iron_profile,
)

_DEFINITIVE = {ResourceVisualState.AVAILABLE.value, ResourceVisualState.DEPLETED.value}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--frames",
        type=Path,
        required=True,
        help="directory containing .raw or .raw.gz frames at the profile geometry",
    )
    parser.add_argument(
        "--expect",
        choices=("uncertain", "definitive"),
        default="uncertain",
        help=(
            "uncertain (default) = drift gate: every rock must be UNCERTAIN. "
            "definitive = reacquisition gate: every rock must be available/depleted."
        ),
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="optional path to write a JSON report",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="stop after this many frames (default: all)",
    )
    return parser


def _load_frame(path: Path, width: int, height: int, pixel_format: Any) -> Frame:
    payload = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
    expected = width * height * pixel_format.bytes_per_pixel
    if len(payload) != expected:
        raise ValueError(
            f"{path.name}: payload is {len(payload)} bytes, expected {expected} "
            f"for {width}x{height} {pixel_format.name}"
        )
    return Frame.from_raw(
        RawFrame(payload, width, height, pixel_format),
        frame_id=1,
        captured_monotonic_s=0.0,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)

    if not args.frames.is_dir():
        print(f"not a directory: {args.frames}", file=sys.stderr)
        return 2

    paths = sorted(
        path
        for path in args.frames.iterdir()
        if path.is_file() and (path.suffix == ".raw" or path.name.endswith(".raw.gz"))
    )
    if args.limit is not None:
        paths = paths[: args.limit]
    if not paths:
        print(f"no .raw or .raw.gz frames found in {args.frames}", file=sys.stderr)
        return 2

    profile = load_varrock_east_iron_profile()
    detector = build_varrock_east_iron_detector()
    expect_uncertain = args.expect == "uncertain"

    print(f"profile          : {profile.profile_id} (schema v3)")
    print(f"landmarks        : {len(profile.scene_landmarks)}, quorum "
          f"{profile.minimum_landmark_quorum}, zones {profile.minimum_landmark_zones}")
    print(f"expectation      : every rock {args.expect.upper()}")
    print(f"frames           : {len(paths)} from {args.frames}\n")

    results: list[dict[str, Any]] = []
    passed = 0
    false_definitive = 0

    for path in paths:
        try:
            frame = _load_frame(
                path, profile.frame_width, profile.frame_height, profile.pixel_format
            )
        except ValueError as exc:
            print(f"  SKIP {path.name}: {exc}")
            results.append({"frame": path.name, "ok": False, "error": str(exc)})
            continue

        observations = detector.detect(frame)
        states = {o.evidence["resource_id"]: o.evidence["state"] for o in observations}
        definitive = sorted(k for k, v in states.items() if v in _DEFINITIVE)
        ok = (not definitive) if expect_uncertain else (len(definitive) == len(states))
        if ok:
            passed += 1
        if expect_uncertain and definitive:
            false_definitive += len(definitive)

        reason = observations[0].evidence.get("reason", "")
        landmark_distances = observations[0].evidence.get("landmark_distances", {})
        matched = sum(
            1
            for item in profile.scene_landmarks
            if float(landmark_distances.get(item.landmark_id, 9.9)) <= item.maximum_distance
        )
        print(
            f"  {'OK  ' if ok else 'FAIL'} {path.name:44s} "
            f"landmarks {matched}/{len(profile.scene_landmarks)}  {reason}"
        )
        results.append(
            {
                "frame": path.name,
                "ok": ok,
                "states": states,
                "definitive_targets": definitive,
                "landmarks_matched": matched,
                "reason": reason,
                "landmark_distances": landmark_distances,
            }
        )

    total = len(results)
    print(f"\n{passed}/{total} frames met the expectation.")
    if expect_uncertain:
        print(f"false definitive targets: {false_definitive}")

    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(
                {
                    "profile_id": profile.profile_id,
                    "schema_version": 3,
                    "expectation": args.expect,
                    "frames_total": total,
                    "frames_passed": passed,
                    "false_definitive_targets": false_definitive,
                    "results": results,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"report written to {args.report}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
