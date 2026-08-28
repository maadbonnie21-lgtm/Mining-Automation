#!/usr/bin/env python3
"""Run wide, diagnostic-only Varrock East scene registration on one raw frame."""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path
from typing import Any

from mining_automation.capture import Frame, RawFrame
from mining_automation.perception import (
    DEFAULT_WIDE_REGISTRATION_RADIUS,
    ResourceDetectorProfile,
    WideSceneRegistrationAnalysis,
    analyze_wide_scene_registration,
    load_varrock_east_iron_profile,
    varrock_east_iron_scene_excluded_regions,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--frame",
        required=True,
        type=Path,
        help="1005x1078 BGRA8888 .raw or .raw.gz supported-view candidate",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="optional JSON report path; default is beside the input frame",
    )
    parser.add_argument(
        "--radius",
        type=int,
        default=DEFAULT_WIDE_REGISTRATION_RADIUS,
        help=(
            "diagnostic search radius in pixels "
            f"(default: {DEFAULT_WIDE_REGISTRATION_RADIUS})"
        ),
    )
    return parser


def _load_frame(path: Path, profile: ResourceDetectorProfile) -> Frame:
    if not path.is_file():
        raise ValueError(f"not a frame file: {path}")
    encoded = path.read_bytes()
    payload = gzip.decompress(encoded) if path.name.endswith(".raw.gz") else encoded
    expected = (
        profile.frame_width
        * profile.frame_height
        * profile.pixel_format.bytes_per_pixel
    )
    if len(payload) != expected:
        raise ValueError(
            f"{path.name}: payload is {len(payload)} bytes, expected {expected} "
            f"for {profile.frame_width}x{profile.frame_height} "
            f"{profile.pixel_format.value}"
        )
    return Frame.from_raw(
        RawFrame(
            payload,
            profile.frame_width,
            profile.frame_height,
            profile.pixel_format,
        ),
        frame_id=1,
        captured_monotonic_s=0.0,
    )


def _excluded_regions(
    profile: ResourceDetectorProfile,
) -> tuple[tuple[int, int, int, int], ...]:
    """Return candidates plus the shared reviewed fixed-UI exclusions."""

    return varrock_east_iron_scene_excluded_regions(profile)


def _analysis_dict(analysis: WideSceneRegistrationAnalysis) -> dict[str, Any]:
    shared = analysis.best_shared
    return {
        "diagnosis": {
            "code": analysis.diagnosis.value,
            "detail": analysis.detail,
        },
        "landmarks": [
            {
                "landmark_id": item.landmark_id,
                "zone": item.zone.value,
                "best_offset": [item.offset_x, item.offset_y],
                "distance": item.distance,
                "threshold": item.maximum_distance,
                "normalized_distance": item.normalized_distance,
                "matched": item.matched,
                "searched_offsets": item.searched_offsets,
            }
            for item in analysis.landmarks
        ],
        "matched_count": analysis.matched_count,
        "matched_zones": [zone.value for zone in analysis.matched_zones],
        "search": {
            "radius_pixels": analysis.search_radius,
            "coarse_step_pixels": analysis.coarse_step,
            "refinement_radius_pixels": analysis.refinement_radius,
        },
        "best_shared_offset": None
        if shared is None
        else {
            "offset": [shared.offset_x, shared.offset_y],
            "validated": shared.validated,
            "matched_count": shared.matched_count,
            "matched_zones": [zone.value for zone in shared.matched_zones],
            "required_quorum": shared.required_quorum,
            "required_zones": shared.required_zones,
            "valid_landmark_count": shared.valid_landmark_count,
            "normalized_distance_sum": shared.normalized_distance_sum,
        },
        "diagnostic_search_does_not_override_production": True,
    }


def _print_analysis(
    frame_path: Path,
    analysis: WideSceneRegistrationAnalysis,
) -> None:
    print("WIDE SCENE REGISTRATION -- DIAGNOSTIC ONLY")
    print(f"Frame: {frame_path}")
    print(
        f"Search: +/-{analysis.search_radius}px, coarse step {analysis.coarse_step}px, "
        f"refine +/-{analysis.refinement_radius}px"
    )
    print("\nlandmark                  zone         offset      distance  threshold  status")
    for item in analysis.landmarks:
        offset = f"({item.offset_x:+d},{item.offset_y:+d})"
        print(
            f"{item.landmark_id:25s} {item.zone.value:12s} {offset:11s} "
            f"{item.distance:8.6f}  {item.maximum_distance:9.6f}  "
            f"{'MATCH' if item.matched else 'FAIL'}"
        )
    zones = ", ".join(zone.value for zone in analysis.matched_zones) or "none"
    print(
        f"\nIndividual recoveries: {analysis.matched_count}/{len(analysis.landmarks)} "
        f"across {len(analysis.matched_zones)} zones [{zones}]"
    )
    if analysis.best_shared is None:
        print("Best shared offset: unavailable (insufficient matched landmarks)")
    else:
        shared = analysis.best_shared
        shared_zones = ", ".join(zone.value for zone in shared.matched_zones) or "none"
        print(
            f"Best shared offset: ({shared.offset_x:+d},{shared.offset_y:+d}) | "
            f"{shared.matched_count}/{len(analysis.landmarks)} landmarks | "
            f"zones [{shared_zones}] | "
            f"{'VALIDATED' if shared.validated else 'REJECTED'}"
        )
    print(f"\nWIDE DIAGNOSIS: {analysis.diagnosis.value}")
    print(f"EVIDENCE: {analysis.detail}")
    print("PRODUCTION DECISION: unchanged / fail-closed")


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    try:
        profile = load_varrock_east_iron_profile()
        frame = _load_frame(arguments.frame, profile)
        excluded_regions = _excluded_regions(profile)
        analysis = analyze_wide_scene_registration(
            frame,
            profile.scene_landmarks,
            required_quorum=profile.minimum_landmark_quorum,
            required_zones=profile.minimum_landmark_zones,
            frame_width=profile.frame_width,
            frame_height=profile.frame_height,
            search_radius=arguments.radius,
            excluded_regions=excluded_regions,
        )
        report_path = arguments.report or arguments.frame.with_name(
            f"{arguments.frame.stem}.wide-registration.json"
        )
        report = {
            "report_kind": "varrock-east-wide-scene-registration",
            "schema_version": 1,
            "frame": str(arguments.frame),
            "profile_id": profile.profile_id,
            "excluded_regions": [list(region) for region in excluded_regions],
            **_analysis_dict(analysis),
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (EOFError, OSError, TypeError, ValueError) as exc:
        print(f"wide registration error: {exc}", file=sys.stderr)
        return 2

    _print_analysis(arguments.frame, analysis)
    print(f"Report written: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
