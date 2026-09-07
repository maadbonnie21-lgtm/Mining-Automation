"""Observed-location navigation setup for a repeat test; no product return-loop claim."""

# ruff: noqa: E402
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
assert prior["status"] == "PASS" and prior["success"] and not prior["operator_chose_walking_clicks"]
assert prior["completed_checkpoints"][-1] == "bank_interior_endpoint"
assert prior["start_window"] == prior["end_window"]
git("merge-base", "--is-ancestor", prior["git_sha"], a.sha)
git("ls-files", "--error-unmatch", "tools/reset_route_test.py")
from mining_automation.navigation.runtime import RouteLimits, run_route
from mining_automation.navigation.visual_route import VisualRoute, crop_minimap
from mining_automation.navigation.windows import NativeRouteBackend

route = VisualRoute(ROOT / "src/mining_automation/navigation/profiles/varrock_east/route.json")
result = {"status": "STOP", "success": False, "stop_reason": "not_started"}
try:
    backend = NativeRouteBackend(
        prior["start_window"]["hwnd"],
        a.output,
        expected_title=prior["start_window"]["identity"]["title"],
        focus_existing=True,
        stop_file=a.output / "STOP",
    )
    assert json.loads(json.dumps(backend.initial)) == prior["end_window"], (
        "window_changed_since_prior_run"
    )
    frame = backend.capture("reset-bank-start-proof")
    geometry, registration = route.observe(frame.image, route.waypoints[-1])
    assert route.verify_health(frame.image, geometry) and registration.distance <= 4, (
        "bank_start_unproven"
    )
    assert route.verify_endpoint(frame.image, geometry)["accepted"], "counter_start_unproven"
    mine = route.waypoints[0]

    def mine_arrival(image, geometry):
        reg = route.references[mine.image_key].register(crop_minimap(image, geometry))
        return {
            "accepted": reg.distance <= mine.tolerance,
            "mine_distance": reg.distance,
            "authority": "setup_only",
        }

    route.waypoints = list(reversed(route.waypoints))
    route.verify_endpoint = mine_arrival
    result = asdict(run_route(backend, route, limits=RouteLimits(start_tolerance=4.0)))
    if result["success"]:
        result.update(status="SETUP_READY_AT_MINE", stop_reason="test_setup_mine_arrival_verified")
except Exception as exc:
    result.update(status="STOP", success=False, stop_reason=f"{type(exc).__name__}:{exc}")
result.update(
    git_sha=a.sha,
    evidence_origin="program_owned_test_reset",
    operator_chose_walking_clicks=False,
    production_return_route_acceptance=False,
    prior_result=str(a.prior.resolve()),
    prior_result_sha256=hashlib.sha256(a.prior.read_bytes()).hexdigest(),
)
a.output.mkdir(parents=True, exist_ok=True)
(a.output / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(
    json.dumps(
        {
            k: result.get(k)
            for k in ["status", "success", "stop_reason", "click_count", "completed_checkpoints"]
        },
        indent=2,
    ),
    flush=True,
)
raise SystemExit(0 if result.get("success") else 2)
