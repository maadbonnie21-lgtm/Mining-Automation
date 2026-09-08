"""Fresh empty/closed/stationary canonical mine receipt for beta completion.

This is not the mining-full departure repair. Any normalization is confined to
one fresh minimap click from the existing <=4-pixel EMPTY mine endpoint envelope.
"""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import cv2

from .bank_vision import BankVision
from .beta_input import InputPolicy
from .beta_mining import beta_mining_backend
from .mining_slice import WorldStatePublicationStatus
from .navigation.visual_route import VisualRoute

CANONICAL_FINISH_DISTANCE = 1.0
EXISTING_RETURN_ENVELOPE = 4.0


def verify_home(
    native: Any, policy: InputPolicy, root: Path, *, fresh_rocks: bool, allow_normalize: bool = True
) -> dict[str, Any]:
    profile = root / "src/mining_automation/navigation/profiles/varrock_east/route.json"
    route, vision = VisualRoute(profile), BankVision()
    mine = route.waypoints[0]
    previous = previous_geometry = None
    stable = exact = 0
    correction = False
    evidence: list[dict[str, Any]] = []
    started = time.monotonic()
    for _ in range(35):
        policy.check()
        frame = native.capture("beta-mine-endpoint")
        geometry, registration = route.observe(frame.image, mine)
        if not route.verify_health(frame.image, geometry):
            raise RuntimeError("healthy_mine_display_unproven")
        if (
            not math.isfinite(registration.distance)
            or registration.distance > EXISTING_RETURN_ENVELOPE
        ):
            raise RuntimeError("outside_reviewed_empty_mine_endpoint; no_normalization")
        dpi = native.initial["dpi_environment"]
        image = cv2.resize(
            frame.image,
            None,
            fx=1 / dpi["effective_mapping_scale_x"],
            fy=1 / dpi["effective_mapping_scale_y"],
            interpolation=cv2.INTER_AREA,
        )
        inventory = vision.inventory(image)
        tabs = vision.match(
            image, "normal_tabs", (450, image.shape[0] - 350, image.shape[1], image.shape[0] - 230)
        )
        if inventory["empty_count"] != 28 or inventory["unknown_count"] != 0:
            raise RuntimeError("home_requires_fresh_empty_inventory")
        if (
            vision.bank_controls(image) is not None
            or not math.isfinite(tabs.score)
            or tabs.score <= 0.82
        ):
            raise RuntimeError("home_requires_positive_bank_closed_proof")
        current_geometry = (geometry.x, geometry.y, geometry.radius)
        if previous_geometry is not None and math.dist(current_geometry, previous_geometry) > 2:
            raise RuntimeError("minimap_geometry_changed_at_home")
        stationary = previous is not None and math.dist(previous, registration.target) <= 1.3
        stable = stable + 1 if stationary else 0
        exact = (
            exact + 1 if stationary and registration.distance <= CANONICAL_FINISH_DISTANCE else 0
        )
        previous, previous_geometry = registration.target, current_geometry
        event = dict(
            frame_id=frame.frame_id,
            captured_monotonic_s=frame.captured_monotonic_s,
            frame_sha256=hashlib.sha256(frame.image.tobytes()).hexdigest(),
            image=frame.evidence_path,
            registration=asdict(registration),
            minimap=asdict(geometry),
            stationary=stationary,
            inventory_empty=28,
            bank_tabs_score=tabs.score,
        )
        evidence.append(event)
        if exact >= 2:
            break
        if allow_normalize and stable >= 2 and registration.distance > CANONICAL_FINISH_DISTANCE:
            if correction:
                raise RuntimeError("canonical_finish_correction_unproven; no_blind_retry")
            point = geometry.screen_point(registration.target)
            native.click(frame, point, geometry)
            correction = True
            stable = exact = 0
            previous = None
        if time.monotonic() - started > 25:
            raise RuntimeError("canonical_finish_timeout")
        policy.wait(0.35)
    else:
        raise RuntimeError("canonical_mine_start_unproven")
    rock_receipt = None
    if fresh_rocks:
        # Reuse the same retained-reference, all-zone, same-frame miner assembler.
        import run_mining_to_full_safe as safe

        from .perception.live_pose_references import verify_local_pose_references

        verify_local_pose_references(root)
        backend_type = beta_mining_backend(
            safe.SafeWindowsMiningToFullBackend, policy, varied_points=False
        )
        backend = backend_type(
            expected_hwnd=native.hwnd,
            output=native.output / f"fresh-rock-view-{native.frame_id:05d}",
            session_id="beta-home-reacquisition",
            title_substring=native.initial["identity"]["title"],
            neutral_settle_s=1.0,
            hover_settle_s=0.7,
            passive_interval_s=1.0,
        )
        try:
            backend.open()
            clean = backend.acquire_clean_observation(session_id=backend.session_id, iteration=1)
            if (
                clean.state.status is not WorldStatePublicationStatus.READY
                or clean.state.inventory.occupied_slots != 0
                or (clean.matched_landmarks or 0) < 5
                or len(clean.matched_zones) < 3
                or clean.state.selected_target is None
            ):
                raise RuntimeError("home_fresh_supported_iron_view_unproven")
            rock_receipt = dict(
                pose=clean.pose_id,
                landmarks=clean.matched_landmarks,
                zones=clean.matched_zones,
                source_frame=clean.frame_path,
            )
        finally:
            backend.close()
        # The final endpoint proof must be NEWER than rock reacquisition, not stale.
        final = verify_home(native, policy, root, fresh_rocks=False, allow_normalize=False)
        final["fresh_rocks_verified"] = True
        final["rock_reacquisition"] = rock_receipt
        final["normalization_clicks"] += int(correction)
        return final
    policy.check()
    if not 0 <= time.monotonic() - frame.captured_monotonic_s <= 4:
        raise RuntimeError("home_receipt_expired")
    return dict(
        success=True,
        mine_arrival_verified=True,
        inventory_empty_verified=True,
        bank_closed_verified=True,
        stationary_verified=True,
        fresh=True,
        window_unchanged=native.snapshot() == native.initial,
        fresh_rocks_verified=False,
        canonical_distance=registration.distance,
        acceptance_distance=CANONICAL_FINISH_DISTANCE,
        endpoint_reference=mine.image_key,
        profile_sha256=hashlib.sha256(profile.read_bytes()).hexdigest(),
        terrain_sha256=hashlib.sha256((profile.parent / "terrain.npz").read_bytes()).hexdigest(),
        final_frame=evidence[-1],
        observations=evidence[-3:],
        normalization_clicks=int(correction),
        start_window=native.initial,
        end_window=native.snapshot(),
    )
