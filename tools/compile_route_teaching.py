# ruff: noqa: E402
# Source-checkout bootstrap must precede package imports.
"""Derive observed-location goals from preserved, genuine teaching frames.

The compiler measures image geometry now; it never claims those measurements
were logged during the operator run. All original evidence stays untouched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if "--live" not in sys.argv:
    sys.path.insert(0, str(ROOT / ".route-deps"))

import cv2
import numpy as np

from mining_automation.navigation.visual_route import (
    MinimapLocator,
    TerrainReference,
    crop_minimap,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--seed-profile", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    data = json.loads(args.manifest.read_text(encoding="utf-8"))
    steps = data["steps"]
    if not steps or any(not s.get("before_image") or not s.get("after_image") for s in steps):
        raise ValueError("Original before/after evidence is required for each teaching action")
    seed = json.loads(args.seed_profile.read_text(encoding="utf-8"))
    with np.load(args.seed_profile.parent / seed["assets"], allow_pickle=False) as archive:
        assets = {"chrome": archive["chrome"].copy(), "chrome_mask": archive["chrome_mask"].copy()}
    locator = MinimapLocator(
        assets["chrome"],
        assets["chrome_mask"],
        tuple(seed["minimap_offset"]),
        seed["minimap_radius"],
    )
    # The next actual before-frame is the settled arrival evidence for the
    # preceding action. Do not confuse immediate motion feedback with arrival.
    paths = (
        [steps[0]["before_image"]]
        + [s["before_image"] for s in steps[1:]]
        + [steps[-1]["after_image"]]
    )
    names = ["mine_start"] + [s["expected_next_checkpoint"] for s in steps]
    if len(set(names)) != len(names):
        raise ValueError("Teaching checkpoint identifiers must be unique")
    reports = []
    waypoints = []
    previous = None
    for i, (name, path) in enumerate(zip(names, paths, strict=True)):
        image_path = Path(path)
        image = cv2.imread(str(image_path))
        if image is None:
            raise ValueError(f"Unreadable teaching image: {image_path.name}")
        geometry = locator.locate(image)
        minimap = crop_minimap(image, geometry)
        key = f"map_{i:02d}"
        assets[key] = minimap
        reports.append(
            {
                "name": name,
                "image": image_path.name,
                "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
                "derived_geometry": asdict(geometry),
                "measurement_origin": "DERIVED_AT_COMPILATION",
            }
        )
        if previous is not None:
            registration = TerrainReference(minimap).register(previous)
            if registration.distance > 72:
                raise ValueError(f"Recorded leg exceeds bounded reach: {name}")
            reports[-1]["previous_frame_registration"] = asdict(registration)
        previous = minimap
        waypoints.append({"name": name, "image_key": key, "tolerance": 3.0})
        if i == 0:
            # Static heart/prayer orb chrome is a separate gameplay gate.
            assets["gameplay_chrome"] = image[90:180, 731:772].copy()
            gameplay_box = [
                (v - (geometry.x if j % 2 == 0 else geometry.y)) / geometry.radius
                for j, v in enumerate((731, 90, 772, 180))
            ]
            # Reviewed 23-HP display in this one-account development envelope.
            x1, y1, x2, y2 = 704, 109, 731, 129
            assets["healthy_display"] = image[y1:y2, x1:x2].copy()
            hp_box = [
                (v - (geometry.x if j % 2 == 0 else geometry.y)) / geometry.radius
                for j, v in enumerate((x1, y1, x2, y2))
            ]
        if i == len(paths) - 1:
            # Counter-only crop: no chat, title, inventory, or player avatar.
            assets["counter"] = image[557:668, 55:430].copy()
            counter_radius = geometry.radius
    args.output.mkdir(parents=True, exist_ok=True)
    asset_path = args.output / "terrain.npz"
    np.savez_compressed(asset_path, **assets)
    config = {
        "schema_version": 1,
        "route_id": "varrock-east-mine-to-bank",
        "status": "development_candidate_not_live_verified",
        "source_manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
        "source_pass": data.get("historical_record_audit", {}).get("source_pass_local_interval"),
        "provenance": "Original operator frames; geometry derived at compilation, not at run time",
        "assets": "terrain.npz",
        "assets_sha256": hashlib.sha256(asset_path.read_bytes()).hexdigest(),
        "minimap_offset": seed["minimap_offset"],
        "minimap_radius": seed["minimap_radius"],
        "gameplay_chrome_box_relative": gameplay_box,
        "healthy_display_box_relative": hp_box,
        "healthy_display_reference": "23 HP",
        "counter_reference_radius": counter_radius,
        "waypoints": waypoints,
        "reference_frames": reports,
    }
    (args.output / "route.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "compiled_not_live_proven",
                "waypoints": len(waypoints),
                "profile": str(args.output / "route.json"),
                "registered_adjacent_pairs": len(reports) - 1,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
