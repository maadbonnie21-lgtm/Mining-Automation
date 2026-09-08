"""Development continuation of an exact stopped test; not an uninterrupted full pass."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--prior", type=Path, required=True)
p.add_argument("--sha", required=True)
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()


def git(*args):
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


assert git("rev-parse", "HEAD") == a.sha and not git(
    "status", "--porcelain", "--untracked-files=no"
)
prior = json.loads(a.prior.read_text(encoding="utf-8"))
assert prior["status"] == "STOP" and prior["stop_reason"] == "RuntimeError:repeated_no_progress"
assert (
    prior["evidence_origin"] == "standalone_program" and not prior["operator_chose_walking_clicks"]
)
assert prior["start_window"] == prior["end_window"]
git("merge-base", "--is-ancestor", prior["git_sha"], a.sha)
for rel in [
    "tools/continue_route_test.py",
    "src/mining_automation/navigation/runtime.py",
    "src/mining_automation/navigation/windows.py",
    "src/mining_automation/navigation/visual_route.py",
]:
    git("ls-files", "--error-unmatch", rel)

# Frozen-source and evidence checks intentionally precede these imports.
from mining_automation.navigation.runtime import RouteLimits, run_route  # noqa: E402
from mining_automation.navigation.visual_route import VisualRoute  # noqa: E402
from mining_automation.navigation.windows import NativeRouteBackend  # noqa: E402

route = VisualRoute(ROOT / "src/mining_automation/navigation/profiles/varrock_east/route.json")
index = len(prior["completed_checkpoints"])
assert 0 < index < len(route.waypoints) - 1
assert prior["completed_checkpoints"] == [w.name for w in route.waypoints[:index]]
observations = [e for e in prior["events"] if e["kind"] == "observation"]
assert observations[-1]["waypoint"] == route.waypoints[index].name
route.waypoints = route.waypoints[index:]
result = {"status": "STOP", "success": False, "stop_reason": "not_started"}
try:
    backend = NativeRouteBackend(
        prior["start_window"]["hwnd"],
        a.output,
        expected_title=prior["expected_title"],
        focus_existing=True,
        stop_file=a.output / "STOP",
    )
    assert json.loads(json.dumps(backend.initial)) == prior["end_window"], (
        "window_changed_since_prior_run"
    )
    frame = backend.capture("resume-location-proof")
    geometry, registration = route.observe(frame.image, route.waypoints[0])
    assert route.verify_health(frame.image, geometry), "health_unproven"
    assert registration.distance <= route.waypoints[0].tolerance, (
        "not_at_exact_expected_resume_checkpoint"
    )
    result = asdict(
        run_route(backend, route, limits=RouteLimits(start_tolerance=route.waypoints[0].tolerance))
    )
except Exception as exc:
    result.update(status="STOP", success=False, stop_reason=f"{type(exc).__name__}:{exc}")
result.update(
    git_sha=a.sha,
    evidence_origin="standalone_program_continuation",
    operator_chose_walking_clicks=False,
    uninterrupted_full_route_pass=False,
    prior_result=str(a.prior.resolve()),
    prior_result_sha256=hashlib.sha256(a.prior.read_bytes()).hexdigest(),
    resumed_checkpoint=route.waypoints[0].name,
)
a.output.mkdir(parents=True, exist_ok=True)
(a.output / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(
    json.dumps(
        {
            k: result.get(k)
            for k in [
                "status",
                "success",
                "stop_reason",
                "click_count",
                "completed_checkpoints",
                "resumed_checkpoint",
            ]
        },
        indent=2,
    ),
    flush=True,
)
raise SystemExit(0 if result.get("success") else 2)
