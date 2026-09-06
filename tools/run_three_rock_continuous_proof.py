#!/usr/bin/env python3
"""Mine northwest, southwest, and center once in one fail-closed live run."""

from __future__ import annotations

import hashlib
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from run_proven_mining_loop import mine_hover_signature

from mining_automation.capture import CaptureSource, Frame, PixelFormat, RawFrame
from mining_automation.capture.windows import WindowsCaptureBackend
from mining_automation.controlled_mining_runner import (
    CANONICAL_RESOURCE_RELEASE,
    ProductionMiningPerceptionEvaluator,
    RealWin32MiningInputDevice,
)
from mining_automation.mining_slice import (
    MiningOnlyPhase,
    PerceptionEpoch,
    ResourcePerceptionEnvelope,
    ResourceViewState,
    assemble_atomic_mining_world_state,
    begin_mining_only_session,
)
from mining_automation.perception.production_profiles import (
    VARROCK_EAST_IRON_DETECTOR_VERSION,
    load_varrock_east_iron_profile,
)
from mining_automation.perception.resource import (
    ProfiledResourceDetector,
    ResourceDetectorProfile,
    RockCandidateProfile,
    resource_state_from_observation,
)
from mining_automation.perception.scene_landmarks import (
    MacroZone,
    SceneLandmarkProfile,
    describe_region,
    evaluate_scene,
    macro_zone_for_region,
)
from mining_automation.validation.windows_camera import RealWindowsCameraApi

HWND = 3736178
WIDTH = 1005
HEIGHT = 1078
STARTING_INVENTORY = 7
NEUTRAL_POINT = (100, 100)
MAX_PASSIVE_CAPTURES = 30
OUTPUT = Path("diagnostics/three-rock-continuous-final7-20260903")

ROCK_IDS = (
    "varrock-east-iron-northwest",
    "varrock-east-iron-southwest",
    "varrock-east-iron-center",
)

# These are position-specific detector regions, not an action sequence. A pose
# is accepted only by its independently frozen 5/6, three-zone landmark gate.
START_POSE_LANDMARK_REGIONS = (
    ("north-west-a", (80, 200, 48, 48), MacroZone.NORTH_WEST),
    ("north-west-b", (300, 300, 48, 48), MacroZone.NORTH_WEST),
    ("north-east-a", (600, 250, 48, 48), MacroZone.NORTH_EAST),
    ("north-east-b", (680, 350, 48, 48), MacroZone.NORTH_EAST),
    ("south-west-a", (200, 620, 48, 48), MacroZone.SOUTH_WEST),
    ("south-west-b", (300, 700, 48, 48), MacroZone.SOUTH_WEST),
)

POSES = {
    "at_start": {
        "reference": Path("diagnostics/current-start-pose-20260905/start-clean.bgra"),
        "optional_local": True,
        "landmark_regions": START_POSE_LANDMARK_REGIONS,
        "regions": ((260, 380, 20, 20), (340, 380, 20, 20), (535, 300, 20, 20)),
        "available_overrides": {
            "varrock-east-iron-northwest": (94.448, 64.433, 49.39),
            "varrock-east-iron-southwest": (89.425, 61.22, 47.343),
            "varrock-east-iron-center": (92.93, 64.733, 49.153),
        },
    },
    "at_northwest": {
        "reference": Path("diagnostics/different-rock-ore3-20260903/ore-01-clean.bgra"),
        "regions": ((370, 540, 20, 20), (440, 545, 20, 20), (370, 640, 20, 20)),
        "available_overrides": {
            "varrock-east-iron-southwest": (92.68, 65.61, 49.03),
            "varrock-east-iron-center": (88.56, 68.155, 42.655),
        },
    },
    "at_southwest": {
        "reference": Path("diagnostics/third-rock-ore4-20260903/ore-01-clean.bgra"),
        # 2026-09-06 0->28 evidence + live hover proof: all three adjacent
        # brown surfaces are exact "Mine Iron rocks" targets in this pose.
        # Move only the center 20x20 sample onto its clean interior; thresholds
        # and the 5/6 three-zone scene gate remain unchanged.
        "regions": ((270, 550, 20, 20), (393, 546, 20, 20), (262, 636, 20, 20)),
        "available_overrides": {
            "varrock-east-iron-northwest": (91.66, 61.52, 48.492),
            "varrock-east-iron-southwest": (88.8, 62.53, 45.25),
            "varrock-east-iron-center": (80.695, 59.623, 40.002),
        },
    },
    "at_center": {
        "reference": Path("diagnostics/third-rock-ore4-20260903/ore-04-reacquired.bgra"),
        "regions": ((450, 380, 20, 20), (530, 380, 20, 20), (450, 440, 20, 20)),
        "available_overrides": {
            "varrock-east-iron-northwest": (96.032, 64.537, 50.727),
            "varrock-east-iron-southwest": (88.382, 59.908, 46.73),
        },
    },
}

LANDMARK_REGIONS = (
    ("north-west-a", (80, 200, 48, 48), MacroZone.NORTH_WEST),
    ("north-west-b", (300, 300, 48, 48), MacroZone.NORTH_WEST),
    ("north-east-a", (520, 300, 48, 48), MacroZone.NORTH_EAST),
    ("north-east-b", (680, 350, 48, 48), MacroZone.NORTH_EAST),
    ("south-west-a", (200, 620, 48, 48), MacroZone.SOUTH_WEST),
    ("south-west-b", (200, 700, 48, 48), MacroZone.SOUTH_WEST),
)


def registered_landmark_region_preserves_zone(
    landmark: SceneLandmarkProfile,
    region: tuple[int, int, int, int],
) -> bool:
    """Reject affine registrations that move a frozen landmark across macro zones."""

    x, y, width, height = region
    return (
        x >= 0
        and y >= 34
        and x + width <= 767
        and y + height <= 850
        and macro_zone_for_region(region, WIDTH, HEIGHT) is landmark.macro_zone
    )


def frame_from_path(path: Path, frame_id: int) -> Frame:
    return Frame.from_raw(
        RawFrame(path.read_bytes(), WIDTH, HEIGHT, PixelFormat.BGRA8888),
        frame_id=frame_id,
        captured_monotonic_s=0.0,
    )


def make_epoch(frame: Frame, sequence: int, cycle: str) -> PerceptionEpoch:
    return PerceptionEpoch(
        capture_source_id="windows-runelite",
        capture_session_id="three-rock-continuous",
        cycle_id=cycle,
        cycle_sequence=sequence,
        frame_id=frame.frame_id,
        captured_monotonic_s=frame.captured_monotonic_s,
        frame_width=frame.width,
        frame_height=frame.height,
        frame_payload_sha256=hashlib.sha256(frame.payload).hexdigest(),
        pixel_format="bgra8888",
    )


def build_pose_detectors() -> dict[str, ProfiledResourceDetector]:
    base = load_varrock_east_iron_profile()
    base_candidates = {item.resource_id: item for item in base.candidates}
    detectors: dict[str, ProfiledResourceDetector] = {}
    for index, (pose_name, config) in enumerate(POSES.items(), start=1):
        reference_path = config["reference"]
        if config.get("optional_local") and not reference_path.is_file():
            continue
        reference = frame_from_path(reference_path, index)
        landmark_regions = config.get("landmark_regions", LANDMARK_REGIONS)
        landmarks = tuple(
            SceneLandmarkProfile(
                landmark_id=f"{pose_name}-{landmark_id}",
                region=region,
                reference_descriptor=describe_region(reference, region, grid=4),
                maximum_distance=0.12,
                grid=4,
                macro_zone=zone,
            )
            for landmark_id, region, zone in landmark_regions
        )
        candidates = tuple(
            RockCandidateProfile(
                resource_id=resource_id,
                region=region,
                available_signature=(
                    type(base_candidates[resource_id].available_signature)(
                        tuple(config.get("available_overrides", {}).get(resource_id)),
                        base_candidates[resource_id].available_signature.max_distance,
                    )
                    if resource_id in config.get("available_overrides", {})
                    else base_candidates[resource_id].available_signature
                ),
                depleted_signature=base_candidates[resource_id].depleted_signature,
                minimum_similarity=base_candidates[resource_id].minimum_similarity,
                minimum_margin=base_candidates[resource_id].minimum_margin,
                occlusion_grid_columns=base_candidates[resource_id].occlusion_grid_columns,
                occlusion_grid_rows=base_candidates[resource_id].occlusion_grid_rows,
                minimum_occlusion_agreement=base_candidates[resource_id].minimum_occlusion_agreement,
            )
            for resource_id, region in zip(ROCK_IDS, config["regions"], strict=True)
        )
        profile = ResourceDetectorProfile(
            profile_id=f"varrock-east-iron-{pose_name}",
            location_id=base.location_id,
            ore_label=base.ore_label,
            frame_width=base.frame_width,
            frame_height=base.frame_height,
            pixel_format=base.pixel_format,
            anchors=base.anchors,
            candidates=candidates,
            minimum_scene_confidence=base.minimum_scene_confidence,
            minimum_anchor_confidence=base.minimum_anchor_confidence,
            sample_step=base.sample_step,
            scene_landmarks=landmarks,
            minimum_landmark_quorum=5,
            minimum_landmark_zones=3,
            schema_version=base.schema_version,
        )
        detectors[pose_name] = ProfiledResourceDetector(
            profile, version=VARROCK_EAST_IRON_DETECTOR_VERSION
        )
    return detectors


def _fast_descriptor(integral, region):
    x, y, width, height = region
    cell_width = width // 4
    cell_height = height // 4
    cells = []
    for row in range(4):
        for column in range(4):
            x1 = x + column * cell_width
            y1 = y + row * cell_height
            x2 = x1 + cell_width
            y2 = y1 + cell_height
            total = integral[y2, x2] - integral[y1, x2] - integral[y2, x1] + integral[y1, x1]
            cells.append(float(total) / (cell_width * cell_height))
    values = np.asarray(cells)
    values -= values.mean()
    scale = np.max(np.abs(values))
    if scale <= 1e-9:
        return np.zeros(16)
    return values / scale


def register_translation(frame: Frame, detector: ProfiledResourceDetector):
    pixels = np.frombuffer(frame.payload, dtype=np.uint8).reshape(HEIGHT, WIDTH, 4)
    luma = (
        0.299 * pixels[:, :, 2].astype(np.float64)
        + 0.587 * pixels[:, :, 1].astype(np.float64)
        + 0.114 * pixels[:, :, 0].astype(np.float64)
    )
    integral = np.pad(luma.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    profile = detector.profile

    recovered = []
    for landmark in profile.scene_landmarks:
        x, y, width, height = landmark.region
        scored = []
        for dy in range(-160, 161, 4):
            for dx in range(-160, 161, 4):
                shifted = (x + dx, y + dy, width, height)
                if (
                    shifted[0] < 0 or shifted[1] < 34
                    or shifted[0] + width > 767 or shifted[1] + height > 850
                    or macro_zone_for_region(shifted, WIDTH, HEIGHT) is not landmark.macro_zone
                ):
                    continue
                actual = _fast_descriptor(integral, shifted)
                distance = float(np.mean(np.abs(actual - np.asarray(landmark.reference_descriptor))))
                scored.append((distance, dx, dy))
        if not scored:
            return None
        scored.sort(key=lambda item: (item[0], abs(item[1]) + abs(item[2])))
        _, coarse_x, coarse_y = scored[0]
        refined = []
        for dy in range(coarse_y - 4, coarse_y + 5):
            for dx in range(coarse_x - 4, coarse_x + 5):
                shifted = (x + dx, y + dy, width, height)
                if (
                    shifted[0] < 0 or shifted[1] < 34
                    or shifted[0] + width > 767 or shifted[1] + height > 850
                    or macro_zone_for_region(shifted, WIDTH, HEIGHT) is not landmark.macro_zone
                ):
                    continue
                actual = _fast_descriptor(integral, shifted)
                distance = float(np.mean(np.abs(actual - np.asarray(landmark.reference_descriptor))))
                refined.append((distance, dx, dy))
        refined.sort(key=lambda item: (item[0], abs(item[1]) + abs(item[2])))
        distance, dx, dy = refined[0]
        recovered.append((landmark, distance, dx, dy))

    matched = [item for item in recovered if item[1] <= item[0].maximum_distance]
    if matched:
        median_dx = float(np.median([item[2] for item in matched]))
        median_dy = float(np.median([item[3] for item in matched]))
        matched = [
            item for item in matched
            if abs(item[2] - median_dx) <= 48 and abs(item[3] - median_dy) <= 48
        ]
    matched_zones = {item[0].macro_zone for item in matched}
    if len(matched) < 5 or len(matched_zones) < 3:
        return None

    source_centers = np.asarray([
        [item[0].region[0] + 24.0, item[0].region[1] + 24.0, 1.0]
        for item in matched
    ])
    target_centers = np.asarray([
        [
            item[0].region[0] + 24.0 + item[2],
            item[0].region[1] + 24.0 + item[3],
        ]
        for item in matched
    ])
    affine = np.linalg.lstsq(source_centers, target_centers, rcond=None)[0]

    matched_ids = {item[0].landmark_id for item in matched}
    shifted_landmarks_list = []
    for landmark, _, dx, dy in recovered:
        is_matched = landmark.landmark_id in matched_ids
        if is_matched:
            new_region = (landmark.region[0] + dx, landmark.region[1] + dy, 48, 48)
        else:
            mapped = np.asarray([
                landmark.region[0] + 24.0,
                landmark.region[1] + 24.0,
                1.0,
            ]) @ affine
            new_region = (
                int(round(mapped[0] - 24.0)),
                int(round(mapped[1] - 24.0)),
                48,
                48,
            )
        if not registered_landmark_region_preserves_zone(landmark, new_region):
            # A matched inlier must always preserve its frozen macro zone.
            # An already-unmatched landmark cannot veto a registration that
            # independently proved the frozen 5/6 quorum across all 3 zones.
            if is_matched:
                return None
            continue
        shifted_landmarks_list.append(replace(landmark, region=new_region))
    shifted_landmarks = tuple(shifted_landmarks_list)
    shifted_candidates_list = []
    for item in profile.candidates:
        x, y, width, height = item.region
        mapped = np.asarray([x + width / 2, y + height / 2, 1.0]) @ affine
        new_region = (
            int(round(mapped[0] - width / 2)),
            int(round(mapped[1] - height / 2)),
            width,
            height,
        )
        if (
            new_region[0] < 0 or new_region[1] < 34
            or new_region[0] + width > 767 or new_region[1] + height > 850
        ):
            return None
        shifted_candidates_list.append(replace(item, region=new_region))
    shifted_candidates = tuple(shifted_candidates_list)
    shifted_profile = replace(
        profile,
        profile_id=f"{profile.profile_id}-registered",
        scene_landmarks=shifted_landmarks,
        candidates=shifted_candidates,
    )
    return (
        ProfiledResourceDetector(shifted_profile, version=VARROCK_EAST_IRON_DETECTOR_VERSION),
        {
            "kind": "distributed_affine_registration",
            "matched": len(matched),
            "zones": sorted(zone.value for zone in matched_zones),
            "landmarks": {
                landmark.landmark_id: {
                    "distance": round(distance, 6),
                    "offset": [dx, dy],
                }
                for landmark, distance, dx, dy in recovered
            },
            "affine": affine.tolist(),
        },
    )


def _regions_overlap(first, second):
    ax, ay, aw, ah = first
    bx, by, bw, bh = second
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def _choose_consensus_available_registration(candidates):
    if len(candidates) < 2:
        return None
    common_ids = set.intersection(*(set(states) for _, _, _, states in candidates))
    available_ids = sorted(
        resource_id
        for resource_id in common_ids
        if all(states[resource_id].available is True for _, _, _, states in candidates)
    )
    if len(available_ids) != 1:
        return None
    resource_id = available_ids[0]
    states = [item[3][resource_id] for item in candidates]
    regions = [state.interaction_region for state in states]
    if any(region is None for region in regions):
        return None
    first_region = regions[0]
    assert first_region is not None
    if any(not _regions_overlap(first_region, region) for region in regions[1:] if region is not None):
        return None
    best_index = max(range(len(states)), key=lambda index: states[index].confidence)
    pose_name, detector, evidence, _ = candidates[best_index]
    selected_state = states[best_index]
    consensus_evidence = dict(evidence)
    consensus_evidence['kind'] = 'distributed_affine_consensus_registration'
    consensus_evidence['consensus_poses'] = [item[0] for item in candidates]
    consensus_evidence['consensus_resource_id'] = resource_id
    consensus_evidence['consensus_regions'] = [list(region) for region in regions if region is not None]
    return pose_name, detector, consensus_evidence, selected_state


def evaluate_resource(frame: Frame, epoch: PerceptionEpoch, detectors, excluded, active):
    passed = []
    diagnoses = {}
    exact_sources = []
    if active.get("detector") is not None:
        exact_sources.append((active["pose"], active["detector"]))
    exact_sources.extend(detectors.items())
    for pose_name, detector in exact_sources:
        profile = detector.profile
        verdict = evaluate_scene(
            frame,
            profile.scene_landmarks,
            required_quorum=5,
            required_zones=3,
            frame_width=WIDTH,
            frame_height=HEIGHT,
        )
        diagnoses[pose_name] = {
            "validated": verdict.validated,
            "matched": verdict.matched_count,
            "zones": [zone.value for zone in verdict.matched_zones],
            "distances": {match.landmark_id: round(match.distance, 6) for match in verdict.matches},
        }
        if verdict.validated:
            passed.append((pose_name, detector))
            if detector is active.get("detector"):
                break
    if len(passed) == 0:
        translated = []
        sources = []
        if active.get("detector") is not None:
            sources.append((active["pose"], active["detector"]))
        sources.extend(detectors.items())
        for pose_name, detector in sources:
            registration = register_translation(frame, detector)
            if registration is not None:
                registered_detector, registration_evidence = registration
                translated.append((pose_name, registered_detector, registration_evidence))
        if len(translated) == 1:
            pose_name, detector, registration_evidence = translated[0]
            passed.append((pose_name, detector))
            diagnoses["software_registration"] = registration_evidence
        elif len(translated) > 1:
            consensus_candidates = []
            for pose_name, detector, registration_evidence in translated:
                states = {
                    state.resource_id: state
                    for observation in detector.detect(frame)
                    if observation.evidence["resource_id"] not in excluded
                    for state in (resource_state_from_observation(observation),)
                }
                consensus_candidates.append(
                    (pose_name, detector, registration_evidence, states)
                )
            consensus = _choose_consensus_available_registration(consensus_candidates)
            if consensus is not None:
                pose_name, detector, registration_evidence, selected_state = consensus
                active["pose"] = pose_name
                active["detector"] = detector
                diagnoses["software_registration"] = registration_evidence
                return ResourcePerceptionEnvelope(
                    epoch=epoch,
                    release=CANONICAL_RESOURCE_RELEASE,
                    view=ResourceViewState.SUPPORTED,
                    resources=(selected_state,),
                ), pose_name, diagnoses
    if len(passed) != 1:
        return ResourcePerceptionEnvelope(
            epoch=epoch,
            release=CANONICAL_RESOURCE_RELEASE,
            view=ResourceViewState.UNSUPPORTED,
            resources=(),
        ), None, diagnoses
    pose_name, detector = passed[0]
    active["pose"] = pose_name
    active["detector"] = detector
    resources = tuple(
        resource_state_from_observation(observation)
        for observation in detector.detect(frame)
        if observation.evidence["resource_id"] not in excluded
    )
    return ResourcePerceptionEnvelope(
        epoch=epoch,
        release=CANONICAL_RESOURCE_RELEASE,
        view=ResourceViewState.SUPPORTED,
        resources=resources,
    ), pose_name, diagnoses


def persist(events, success=False, reason="running"):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    payload = {"success": success, "stop_reason": reason, "events": events}
    (OUTPUT / "result.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def stop(events, reason, success=False):
    persist(events, success, reason)
    print(f"STOP: {reason}", flush=True)
    return 0 if success else 2


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    detectors = build_pose_detectors()
    api = RealWindowsCameraApi()
    backend = WindowsCaptureBackend(title_substring="RuneLite")
    source = CaptureSource(backend, max_consecutive_failures=2)
    inventory_evaluator = ProductionMiningPerceptionEvaluator()
    events = []
    completed: list[str] = []
    click_count = 0
    active_registration = {"pose": None, "detector": None}
    source.open()
    try:
        for ore_index in range(1, 4):
            input_device = RealWin32MiningInputDevice()
            window = input_device.verify_target_window()
            if window.hwnd != HWND or api.foreground_window() != HWND:
                return stop(events, "window_or_foreground_changed")
            neutral_screen = api.pointer_mapping(HWND, *NEUTRAL_POINT).physical_screen.pair
            if api.root_window_at_point(*neutral_screen) != HWND or not api.move_cursor(*neutral_screen):
                return stop(events, "neutral_cursor_failed")
            time.sleep(1.0)
            clean = source.capture()
            clean_path = OUTPUT / f"ore-{ore_index:02d}-clean.bgra"
            clean_path.write_bytes(clean.payload)
            clean_epoch = make_epoch(clean, ore_index * 100, f"ore-{ore_index}-clean")
            resource, pose, diagnoses = evaluate_resource(
                clean, clean_epoch, detectors, frozenset(completed), active_registration
            )
            if "software_registration" in diagnoses:
                registration_diagnoses = diagnoses
                clean = source.capture()
                clean_path = OUTPUT / f"ore-{ore_index:02d}-clean-registered.bgra"
                clean_path.write_bytes(clean.payload)
                clean_epoch = make_epoch(
                    clean,
                    ore_index * 100 + 1,
                    f"ore-{ore_index}-clean-registered",
                )
                resource, pose, fresh_diagnoses = evaluate_resource(
                    clean,
                    clean_epoch,
                    detectors,
                    frozenset(completed),
                    active_registration,
                )
                diagnoses = {
                    "registration_capture": registration_diagnoses,
                    "fresh_registered_capture": fresh_diagnoses,
                }
            _, inventory = inventory_evaluator.evaluate(clean, clean_epoch)
            now = max(time.monotonic(), clean.captured_monotonic_s)
            state = assemble_atomic_mining_world_state(
                resource=resource, inventory=inventory, evaluated_monotonic_s=now
            )
            decision = begin_mining_only_session(
                session_id=f"three-rock-{ore_index}", state=state, now_monotonic_s=now
            )
            proposal = decision.proposal
            events.append({
                "kind": "clean_reacquisition",
                "ore_index": ore_index,
                "frame": str(clean_path),
                "pose": pose,
                "pose_diagnoses": diagnoses,
                "inventory": inventory.inventory.occupied_slots,
                "inventory_confidence": inventory.inventory.confidence,
                "resource_view": resource.view.value,
                "completed_rocks": list(completed),
                "phase": decision.session.phase.value,
            })
            persist(events)
            if ore_index == 1 and inventory.inventory.occupied_slots != STARTING_INVENTORY:
                return stop(events, "starting_inventory_not_expected")
            if proposal is None or decision.session.phase is not MiningOnlyPhase.READY:
                return stop(events, "resource_or_inventory_unknown")

            rx, ry, rw, rh = proposal.target_region
            client_point = (rx + rw // 2, ry + rh // 2)
            screen_point = api.pointer_mapping(HWND, *client_point).physical_screen.pair
            if api.root_window_at_point(*screen_point) != HWND or not api.move_cursor(*screen_point):
                return stop(events, "target_cursor_failed")
            time.sleep(0.7)
            hover = source.capture()
            hover_path = OUTPUT / f"ore-{ore_index:02d}-hover.bgra"
            hover_path.write_bytes(hover.payload)
            hover_proof = mine_hover_signature(hover.payload, hover.width)
            events.append({
                "kind": "hover_proof",
                "ore_index": ore_index,
                "target_id": proposal.target_id,
                "target_region": list(proposal.target_region),
                "client_point": list(client_point),
                "frame": str(hover_path),
                **hover_proof,
            })
            persist(events)
            if not hover_proof["proven_mine_iron_rocks"]:
                return stop(events, "mine_hover_unproven")

            if (
                api.foreground_window() != HWND
                or api.cursor_position() != screen_point
                or api.root_window_at_point(*screen_point) != HWND
            ):
                return stop(events, "pre_click_safety_changed")
            receipt = input_device.dispatch_one_click(HWND, proposal.target_region, proposal)
            click_count += 1
            events.append({
                "kind": "single_click",
                "ore_index": ore_index,
                "target_id": proposal.target_id,
                "click_count": click_count,
                "dispatch_id": receipt.dispatch_id,
                "audit": input_device.last_dispatch_audit,
            })
            persist(events)

            before = proposal.inventory_occupied_before
            verified = False
            for passive_index in range(1, MAX_PASSIVE_CAPTURES + 1):
                time.sleep(1.0)
                passive = source.capture()
                passive_path = OUTPUT / f"ore-{ore_index:02d}-passive-{passive_index:02d}.bgra"
                passive_path.write_bytes(passive.payload)
                passive_epoch = make_epoch(
                    passive, ore_index * 100 + passive_index,
                    f"ore-{ore_index}-passive-{passive_index}",
                )
                _, post_inventory = inventory_evaluator.evaluate(passive, passive_epoch)
                occupied = post_inventory.inventory.occupied_slots
                events.append({
                    "kind": "passive_verification",
                    "ore_index": ore_index,
                    "index": passive_index,
                    "frame": str(passive_path),
                    "inventory": occupied,
                    "inventory_confidence": post_inventory.inventory.confidence,
                })
                persist(events)
                if occupied is None:
                    return stop(events, "inventory_unknown_during_verification")
                if occupied == before + 1:
                    completed.append(proposal.target_id)
                    verified = True
                    break
                if occupied != before:
                    return stop(events, "unexpected_inventory_delta")
            if not verified:
                return stop(events, "no_progress_after_proven_click")

        if tuple(sorted(completed)) != tuple(sorted(ROCK_IDS)):
            return stop(events, "three_distinct_rocks_not_completed")

        neutral_screen = api.pointer_mapping(HWND, *NEUTRAL_POINT).physical_screen.pair
        if api.root_window_at_point(*neutral_screen) != HWND or not api.move_cursor(*neutral_screen):
            return stop(events, "final_neutral_cursor_failed")
        time.sleep(1.0)
        final_frame = source.capture()
        final_path = OUTPUT / "final-reacquired.bgra"
        final_path.write_bytes(final_frame.payload)
        final_epoch = make_epoch(final_frame, 9999, "final-reacquisition")
        final_resource, final_pose, final_diagnoses = evaluate_resource(
            final_frame, final_epoch, detectors, frozenset(), active_registration
        )
        _, final_inventory = inventory_evaluator.evaluate(final_frame, final_epoch)
        events.append({
            "kind": "final_reacquisition",
            "frame": str(final_path),
            "pose": final_pose,
            "pose_diagnoses": final_diagnoses,
            "resource_view": final_resource.view.value,
            "inventory": final_inventory.inventory.occupied_slots,
            "inventory_confidence": final_inventory.inventory.confidence,
            "rock_order": completed,
            "click_count": click_count,
        })
        if final_resource.view is not ResourceViewState.SUPPORTED:
            return stop(events, "final_resource_reacquisition_unknown")
        if final_inventory.inventory.occupied_slots != STARTING_INVENTORY + 3:
            return stop(events, "final_inventory_delta_not_3")
        return stop(events, "three_rock_continuous_proof_verified", success=True)
    finally:
        source.close()


if __name__ == "__main__":
    raise SystemExit(main())
