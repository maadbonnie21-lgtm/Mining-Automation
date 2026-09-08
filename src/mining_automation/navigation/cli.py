"""Development route entrypoint. Preview is the default; live is explicit."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import subprocess
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile", type=Path, default=Path(__file__).parent / "profiles/varrock_east/route.json"
    )
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--hwnd", type=int)
    parser.add_argument("--title", help="Exact already-authenticated RuneLite account window title")
    parser.add_argument("--authorize-execution-sha")
    parser.add_argument("--confirm", default="")
    parser.add_argument(
        "--focus-existing",
        action="store_true",
        help="Request foreground once, never restore/resize",
    )
    parser.add_argument(
        "--stop-after",
        type=int,
        help="Stop after N verified transitions, not full-route acceptance",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if not args.live:
        from .visual_route import VisualRoute

        route = VisualRoute(args.profile)
        print(
            json.dumps(
                {
                    "status": "PREVIEW_ONLY",
                    "route": route.config["route_id"],
                    "support_status": route.config["status"],
                    "checkpoints": [w.name for w in route.waypoints],
                    "game_input": False,
                },
                indent=2,
            )
        )
        return 0
    if not args.hwnd or not args.title or args.confirm != "RUN_MINE_TO_BANK_NO_RESIZE":
        parser.error("Live requires exact --hwnd, --title and --confirm RUN_MINE_TO_BANK_NO_RESIZE")
    root = Path(__file__).resolve().parents[3]
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not args.authorize_execution_sha or args.authorize_execution_sha != head or status:
        parser.error("Live requires the exact clean tracked checkout SHA; no force or bypass")
    output = args.output or root / "outputs" / (
        "program-route-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    )
    if output.exists():
        parser.error("Evidence output already exists; use a new unique directory")
    config = json.loads(args.profile.read_text(encoding="utf-8"))
    tracked_files = [
        Path(__file__).resolve(),
        args.profile.resolve(),
        (args.profile.resolve().parent / config["assets"]).resolve(),
    ]
    tracked_files.extend(
        (Path(__file__).parent / name).resolve()
        for name in ("__init__.py", "visual_route.py", "runtime.py", "windows.py")
    )
    if config.get("start_connectors"):
        tracked_files.extend(
            (
                root / "tools" / "run_three_rock_continuous_proof.py",
                root / "tools" / "run_proven_mining_loop.py",
                root / "src" / "mining_automation" / "controlled_mining_runner.py",
                root / "src" / "mining_automation" / "mining_slice.py",
            )
        )
    for file in tracked_files:
        if not file.is_relative_to(root):
            parser.error("Live source and profile must belong to the exact reviewed checkout")
        subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "--error-unmatch",
                file.relative_to(root).as_posix(),
            ],
            check=True,
            capture_output=True,
        )
    from .visual_route import VisualRoute

    route = VisualRoute(args.profile)
    from .runtime import run_route
    from .windows import NativeRouteBackend

    try:
        backend = NativeRouteBackend(
            args.hwnd,
            output,
            expected_title=args.title,
            focus_existing=args.focus_existing,
            stop_file=output / "STOP",
        )
        if route.start_connectors:
            tools_path = root / "tools"
            sys.path.insert(0, str(tools_path))
            from run_three_rock_continuous_proof import (  # type: ignore[import-not-found]
                build_pose_detectors,
                evaluate_resource,
            )

            from ..controlled_mining_runner import ProductionMiningPerceptionEvaluator

            backend.prepare_start_connector_authority(
                pose_detectors=build_pose_detectors(),
                resource_evaluator=evaluate_resource,
                inventory_evaluator=ProductionMiningPerceptionEvaluator(),
            )
        result = run_route(backend, route, stop_after=args.stop_after)
        payload = asdict(result)
    except (Exception, KeyboardInterrupt) as exc:
        payload = {"status": "STOP", "success": False, "stop_reason": f"{type(exc).__name__}:{exc}"}
    payload.update(
        {
            "git_sha": head,
            "expected_title": args.title,
            "python_executable": sys.executable,
            "dependency_versions": {
                name: importlib.metadata.version(name)
                for name in ("numpy", "opencv-python-headless")
            },
            "route_id": route.config["route_id"],
            "evidence_origin": "standalone_program",
            "operator_chose_walking_clicks": False,
            "profile_status": route.config["status"],
        }
    )
    output.mkdir(parents=True, exist_ok=True)
    (output / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": payload["status"],
                "success": payload["success"],
                "stop_reason": payload["stop_reason"],
                "evidence": str(output / "result.json"),
            },
            indent=2,
        )
    )
    return 0 if payload["status"] in ("PASS", "STAGE_PASS") else 2


if __name__ == "__main__":
    raise SystemExit(main())
