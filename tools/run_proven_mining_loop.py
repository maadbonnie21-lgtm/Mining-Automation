#!/usr/bin/env python3
"""Run the proven mining-only loop from the current stable RuneLite view."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mining_automation.capture import CaptureSource
from mining_automation.capture.windows import WindowsCaptureBackend
from mining_automation.controlled_mining_runner import (
    RealWin32MiningInputDevice,
    ProductionMiningPerceptionEvaluator,
)
from mining_automation.mining_slice import (
    INVENTORY_CAPACITY,
    MiningOnlyPhase,
    PerceptionEpoch,
    ResourceViewState,
    assemble_atomic_mining_world_state,
    begin_mining_only_session,
)
from mining_automation.validation.windows_camera import RealWindowsCameraApi


HWND = 3736178
NEUTRAL_POINT = (100, 100)
OUTPUT = Path(os.environ.get(
    "MINING_LOOP_OUTPUT", "diagnostics/proven-mining-ore2-20260903"
))
MAX_PASSIVE_CAPTURES = 30
MISSION_TARGET_OCCUPIED = int(os.environ.get("MINING_TARGET_OCCUPIED", "2"))
TEST_EXCLUDED_TARGET_IDS = frozenset(
    item
    for item in os.environ.get("MINING_TEST_EXCLUDE_TARGET_ID", "").split(",")
    if item
)


def epoch(frame, *, cycle: str, sequence: int) -> PerceptionEpoch:
    return PerceptionEpoch(
        capture_source_id="windows-runelite",
        capture_session_id="proven-mining-loop",
        cycle_id=cycle,
        cycle_sequence=sequence,
        frame_id=frame.frame_id,
        captured_monotonic_s=frame.captured_monotonic_s,
        frame_width=frame.width,
        frame_height=frame.height,
        frame_payload_sha256=hashlib.sha256(frame.payload).hexdigest(),
        pixel_format="bgra8888",
    )


def mine_hover_signature(payload: bytes, width: int) -> dict[str, object]:
    cyan: list[tuple[int, int]] = []
    white_prefix: list[tuple[int, int]] = []
    for y in range(25, 60):
        for x in range(250):
            offset = (y * width + x) * 4
            blue, green, red = payload[offset : offset + 3]
            if blue - red > 50 and green - red > 50 and green > 100:
                cyan.append((x, y - 25))
            if (
                x < 42
                and min(blue, green, red) > 150
                and max(blue, green, red) - min(blue, green, red) < 35
            ):
                white_prefix.append((x, y - 25))

    cyan_bbox = None if not cyan else [
        min(x for x, _ in cyan), min(y for _, y in cyan),
        max(x for x, _ in cyan), max(y for _, y in cyan),
    ]
    white_bbox = None if not white_prefix else [
        min(x for x, _ in white_prefix), min(y for _, y in white_prefix),
        max(x for x, _ in white_prefix), max(y for _, y in white_prefix),
    ]
    proven = (
        160 <= len(cyan) <= 230
        and cyan_bbox is not None
        and 38 <= cyan_bbox[0] <= 45
        and 105 <= cyan_bbox[2] <= 115
        and 90 <= len(white_prefix) <= 140
        and white_bbox is not None
        and 5 <= white_bbox[0] <= 10
        and 32 <= white_bbox[2] <= 38
    )
    return {
        "proven_mine_iron_rocks": proven,
        "cyan_pixels": len(cyan),
        "cyan_bbox": cyan_bbox,
        "white_prefix_pixels": len(white_prefix),
        "white_prefix_bbox": white_bbox,
    }


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    api = RealWindowsCameraApi()
    backend = WindowsCaptureBackend(title_substring="RuneLite")
    source = CaptureSource(backend, max_consecutive_failures=2)
    events: list[dict[str, object]] = []
    source.open()
    try:
        iteration = 0
        while True:
            iteration += 1
            input_device = RealWin32MiningInputDevice()
            window = input_device.verify_target_window()
            if window.hwnd != HWND or api.foreground_window() != HWND:
                return stop(events, "foreground_or_window_unknown")

            neutral = api.pointer_mapping(HWND, *NEUTRAL_POINT).physical_screen.pair
            if api.root_window_at_point(*neutral) != HWND or not api.move_cursor(*neutral):
                return stop(events, "neutral_cursor_move_failed")
            time.sleep(1.0)
            if api.foreground_window() != HWND:
                return stop(events, "foreground_lost_before_clean_capture")

            evaluator = ProductionMiningPerceptionEvaluator()
            clean = source.capture()
            clean_path = OUTPUT / f"ore-{iteration:02d}-clean.bgra"
            clean_path.write_bytes(clean.payload)
            clean_epoch = epoch(clean, cycle=f"ore-{iteration}-clean", sequence=iteration * 100)
            resource, inventory = evaluator.evaluate(clean, clean_epoch)
            if TEST_EXCLUDED_TARGET_IDS:
                resource = type(resource)(
                    epoch=resource.epoch,
                    release=resource.release,
                    view=resource.view,
                    resources=tuple(
                        item
                        for item in resource.resources
                        if item.resource_id not in TEST_EXCLUDED_TARGET_IDS
                    ),
                )
            now = max(time.monotonic(), clean.captured_monotonic_s)
            state = assemble_atomic_mining_world_state(
                resource=resource,
                inventory=inventory,
                evaluated_monotonic_s=now,
            )
            decision = begin_mining_only_session(
                session_id=f"proven-mining-loop-{iteration}",
                state=state,
                now_monotonic_s=now,
            )
            proposal = decision.proposal
            clean_event: dict[str, object] = {
                "iteration": iteration,
                "kind": "clean_reacquisition",
                "frame": str(clean_path),
                "resource_view": resource.view.value,
                "inventory_occupied": inventory.inventory.occupied_slots,
                "inventory_confidence": inventory.inventory.confidence,
                "phase": decision.session.phase.value,
                "stop_reason": decision.stop_reason.value,
                "test_excluded_target_ids": sorted(TEST_EXCLUDED_TARGET_IDS),
            }
            events.append(clean_event)
            persist(events)
            occupied = inventory.inventory.occupied_slots
            if occupied == INVENTORY_CAPACITY:
                return stop(events, "inventory_full", success=True)
            if occupied == MISSION_TARGET_OCCUPIED:
                return stop(
                    events,
                    f"ore_{MISSION_TARGET_OCCUPIED}_already_verified",
                    success=True,
                )
            if proposal is None or decision.session.phase is not MiningOnlyPhase.READY:
                return stop(events, "clean_state_uncertain")

            rx, ry, rw, rh = proposal.target_region
            target_point = (rx + rw // 2, ry + rh // 2)
            mapping = api.pointer_mapping(HWND, *target_point)
            screen_point = mapping.physical_screen.pair
            if (
                api.foreground_window() != HWND
                or api.root_window_at_point(*screen_point) != HWND
                or not api.move_cursor(*screen_point)
            ):
                return stop(events, "target_cursor_move_failed")
            time.sleep(0.7)
            hover = source.capture()
            hover_path = OUTPUT / f"ore-{iteration:02d}-hover.bgra"
            hover_path.write_bytes(hover.payload)
            hover_proof = mine_hover_signature(hover.payload, hover.width)
            events.append({
                "iteration": iteration,
                "kind": "hover_proof",
                "target_id": proposal.target_id,
                "target_region": list(proposal.target_region),
                "client_point": list(target_point),
                "screen_point": list(screen_point),
                "frame": str(hover_path),
                **hover_proof,
            })
            persist(events)
            if not hover_proof["proven_mine_iron_rocks"]:
                return stop(events, "mine_hover_unproven")

            window = input_device.verify_target_window()
            if (
                window.hwnd != HWND
                or api.foreground_window() != HWND
                or api.cursor_position() != screen_point
                or api.root_window_at_point(*screen_point) != HWND
            ):
                return stop(events, "pre_click_safety_changed")

            receipt = input_device.dispatch_one_click(
                HWND, proposal.target_region, proposal
            )
            events.append({
                "iteration": iteration,
                "kind": "single_click",
                "receipt": {
                    "dispatch_id": receipt.dispatch_id,
                    "click_count": receipt.click_dispatch_count,
                    "dispatched_monotonic_s": receipt.dispatched_monotonic_s,
                },
                "audit": input_device.last_dispatch_audit,
            })
            persist(events)

            progressed = False
            for passive_index in range(1, MAX_PASSIVE_CAPTURES + 1):
                time.sleep(1.0)
                frame = source.capture()
                frame_path = OUTPUT / (
                    f"ore-{iteration:02d}-passive-{passive_index:02d}.bgra"
                )
                frame_path.write_bytes(frame.payload)
                post_epoch = epoch(
                    frame,
                    cycle=f"ore-{iteration}-passive-{passive_index}",
                    sequence=iteration * 100 + passive_index,
                )
                post_resource, post_inventory = evaluator.evaluate(frame, post_epoch)
                post_occupied = post_inventory.inventory.occupied_slots
                selected_available = None
                if post_resource.view is ResourceViewState.SUPPORTED:
                    selected_available = next(
                        (
                            item.available
                            for item in post_resource.resources
                            if item.resource_id == proposal.target_id
                        ),
                        None,
                    )
                event = {
                    "iteration": iteration,
                    "kind": "passive_verification",
                    "index": passive_index,
                    "frame": str(frame_path),
                    "inventory_occupied": post_occupied,
                    "inventory_confidence": post_inventory.inventory.confidence,
                    "inventory_unknown_reason": post_inventory.unknown_reason,
                    "resource_view": post_resource.view.value,
                    "selected_available": selected_available,
                }
                events.append(event)
                persist(events)
                print(
                    f"ore {iteration} passive {passive_index}: "
                    f"inventory={post_occupied} resource={post_resource.view.value}",
                    flush=True,
                )
                if post_occupied is None:
                    return stop(events, "inventory_unknown_during_verification")
                if post_occupied == proposal.inventory_occupied_before + 1:
                    progressed = True
                    print(f"ORE {post_occupied} VERIFIED", flush=True)
                    break
                if post_occupied != proposal.inventory_occupied_before:
                    return stop(events, "unexpected_inventory_delta")
            if not progressed:
                return stop(events, "no_progress_after_proven_click")
            if post_occupied == MISSION_TARGET_OCCUPIED:
                neutral = api.pointer_mapping(HWND, *NEUTRAL_POINT).physical_screen.pair
                if api.root_window_at_point(*neutral) != HWND or not api.move_cursor(*neutral):
                    return stop(
                        events,
                        f"ore_{MISSION_TARGET_OCCUPIED}_verified_reacquisition_cursor_failed",
                    )
                time.sleep(1.0)
                reacquired = source.capture()
                reacquired_path = OUTPUT / (
                    f"ore-{MISSION_TARGET_OCCUPIED:02d}-reacquired.bgra"
                )
                reacquired_path.write_bytes(reacquired.payload)
                reacquire_evaluator = ProductionMiningPerceptionEvaluator()
                reacquire_resource, reacquire_inventory = reacquire_evaluator.evaluate(
                    reacquired,
                    epoch(
                        reacquired,
                        cycle=f"ore-{MISSION_TARGET_OCCUPIED}-reacquired",
                        sequence=9999,
                    ),
                )
                events.append({
                    "iteration": iteration,
                    "kind": "post_ore_reacquisition",
                    "frame": str(reacquired_path),
                    "resource_view": reacquire_resource.view.value,
                    "inventory_occupied": reacquire_inventory.inventory.occupied_slots,
                    "inventory_confidence": reacquire_inventory.inventory.confidence,
                    "inventory_unknown_reason": reacquire_inventory.unknown_reason,
                })
                persist(events)
                return stop(
                    events,
                    f"ore_{MISSION_TARGET_OCCUPIED}_verified",
                    success=True,
                )
    finally:
        source.close()


def persist(events: list[dict[str, object]]) -> None:
    (OUTPUT / "loop-evidence.json").write_text(
        json.dumps({"events": events}, indent=2), encoding="utf-8"
    )


def stop(
    events: list[dict[str, object]], reason: str, *, success: bool = False
) -> int:
    payload = {"success": success, "stop_reason": reason, "events": events}
    (OUTPUT / "result.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    print(f"STOP: {reason}", flush=True)
    return 0 if success else 2


if __name__ == "__main__":
    raise SystemExit(main())
