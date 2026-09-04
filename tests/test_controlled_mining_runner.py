"""Offline / synthetic integration tests for the controlled mining runner.

These tests execute without touching live Win32 mouse/keyboard inputs or requiring
a live RuneLite window.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.contracts import InventoryState, ResourceState
from mining_automation.controlled_mining_runner import (
    CANONICAL_INVENTORY_RELEASE,
    CANONICAL_RESOURCE_RELEASE,
    EXPECTED_CLIENT_HEIGHT,
    EXPECTED_CLIENT_WIDTH,
    ProductionMiningPerceptionEvaluator,
    SyntheticMiningInputDevice,
    SyntheticMiningPerceptionEvaluator,
    execute_one_controlled_attempt,
)
from mining_automation.mining_slice import (
    INVENTORY_CAPACITY,
    InventoryPerceptionEnvelope,
    MiningOnlyStopReason,
    MiningProgressKind,
    PerceptionEpoch,
    ResourcePerceptionEnvelope,
    ResourceViewState,
)


class MockCaptureSource:
    """Mock capture source that returns pre-configured frames."""

    def __init__(self, frames: list[Frame]) -> None:
        self._frames = list(frames)
        self._index = 0

    def capture(self) -> Frame:
        if self._index >= len(self._frames):
            raise IndexError("MockCaptureSource exhausted")
        frame = self._frames[self._index]
        self._index += 1
        return frame


def _make_frame(frame_id: int, captured_s: float, payload_tag: str = "tag") -> Frame:
    # 1005x1078 BGRA frame
    # Create distinct bytes using payload_tag
    tag_bytes = payload_tag.encode("ascii")
    payload = tag_bytes.ljust(1005 * 1078 * 4, b"\x00")
    raw = RawFrame(
        payload=payload,
        width=EXPECTED_CLIENT_WIDTH,
        height=EXPECTED_CLIENT_HEIGHT,
        pixel_format=PixelFormat.BGRA8888,
    )
    return Frame.from_raw(raw, frame_id=frame_id, captured_monotonic_s=captured_s)


def _make_resource_envelope(
    resources: tuple[ResourceState, ...],
    view: ResourceViewState = ResourceViewState.SUPPORTED,
) -> ResourcePerceptionEnvelope:
    dummy_epoch = PerceptionEpoch(
        capture_source_id="windows-runelite",
        capture_session_id="dummy",
        cycle_id="dummy",
        cycle_sequence=1,
        frame_id=1,
        captured_monotonic_s=1.0,
        frame_width=EXPECTED_CLIENT_WIDTH,
        frame_height=EXPECTED_CLIENT_HEIGHT,
        frame_payload_sha256="0" * 64,
    )
    return ResourcePerceptionEnvelope(
        epoch=dummy_epoch,
        release=CANONICAL_RESOURCE_RELEASE,
        view=view,
        resources=resources,
    )


def _make_inventory_envelope(
    occupied_slots: int | None,
    confidence: float = 0.95,
    unknown_reason: str | None = None,
) -> InventoryPerceptionEnvelope:
    dummy_epoch = PerceptionEpoch(
        capture_source_id="windows-runelite",
        capture_session_id="dummy",
        cycle_id="dummy",
        cycle_sequence=1,
        frame_id=1,
        captured_monotonic_s=1.0,
        frame_width=EXPECTED_CLIENT_WIDTH,
        frame_height=EXPECTED_CLIENT_HEIGHT,
        frame_payload_sha256="0" * 64,
    )
    return InventoryPerceptionEnvelope(
        epoch=dummy_epoch,
        release=CANONICAL_INVENTORY_RELEASE,
        inventory=InventoryState(
            occupied_slots=occupied_slots,
            capacity=INVENTORY_CAPACITY,
            confidence=confidence,
        ),
        unknown_reason=unknown_reason,
    )


STANDARD_RESOURCES = (
    ResourceState("varrock-east-iron-nw", "iron", True, 0.92, (100, 200, 40, 40)),
    ResourceState("varrock-east-iron-sw", "iron", True, 0.95, (100, 300, 40, 40)),
    ResourceState("varrock-east-iron-center", "iron", False, 0.88, None),
    ResourceState("varrock-east-iron-ne", "iron", False, 0.90, None),
)


def test_runner_selects_first_available_target_and_dispatches_one_click(tmp_path: Path) -> None:
    t0 = time.monotonic()
    f1 = _make_frame(1, t0, "frame1")
    f2 = _make_frame(2, t0 + 0.1, "frame2")
    capture = MockCaptureSource([f1, f2])

    pre_res = _make_resource_envelope(STANDARD_RESOURCES)
    pre_inv = _make_inventory_envelope(5)

    # Post state: nw rock depleted
    post_resources = (
        ResourceState("varrock-east-iron-nw", "iron", False, 0.92, None),
        ResourceState("varrock-east-iron-sw", "iron", True, 0.95, (100, 300, 40, 40)),
        ResourceState("varrock-east-iron-center", "iron", False, 0.88, None),
        ResourceState("varrock-east-iron-ne", "iron", False, 0.90, None),
    )
    post_res = _make_resource_envelope(post_resources)
    post_inv = _make_inventory_envelope(5)

    evaluator = SyntheticMiningPerceptionEvaluator([
        (pre_res, pre_inv),
        (post_res, post_inv),
    ])
    input_device = SyntheticMiningInputDevice()

    outcome = execute_one_controlled_attempt(
        capture_source=capture,
        evaluator=evaluator,
        input_device=input_device,
        evidence_dir=tmp_path,
        post_attempt_delay_s=0.0,
    )

    # Verifications
    assert outcome.success is True
    assert outcome.progress_kind is MiningProgressKind.RESOURCE_DEPLETED
    assert outcome.stop_reason is MiningOnlyStopReason.NONE
    assert outcome.proposal is not None
    assert outcome.proposal.target_id == "varrock-east-iron-nw"
    assert outcome.proposal.target_region == (100, 200, 40, 40)
    assert len(input_device.dispatch_calls) == 1
    assert input_device.dispatch_calls[0][1] == (100, 200, 40, 40)

    # Evidence written
    assert outcome.evidence_path is not None
    evidence_file = Path(outcome.evidence_path)
    assert evidence_file.exists()
    evidence_data = json.loads(evidence_file.read_text(encoding="utf-8"))
    assert evidence_data["summary"]["success"] is True
    assert evidence_data["summary"]["proposal"]["target_id"] == "varrock-east-iron-nw"


def test_runner_verifies_inventory_increment(tmp_path: Path) -> None:
    t0 = time.monotonic()
    f1 = _make_frame(1, t0, "frame1")
    f2 = _make_frame(2, t0 + 0.1, "frame2")
    capture = MockCaptureSource([f1, f2])

    pre_res = _make_resource_envelope(STANDARD_RESOURCES)
    pre_inv = _make_inventory_envelope(5)

    # Post state: nw rock still available, but inventory incremented to 6
    post_res = _make_resource_envelope(STANDARD_RESOURCES)
    post_inv = _make_inventory_envelope(6)

    evaluator = SyntheticMiningPerceptionEvaluator([
        (pre_res, pre_inv),
        (post_res, post_inv),
    ])
    input_device = SyntheticMiningInputDevice()

    outcome = execute_one_controlled_attempt(
        capture_source=capture,
        evaluator=evaluator,
        input_device=input_device,
        evidence_dir=tmp_path,
        post_attempt_delay_s=0.0,
    )

    assert outcome.success is True
    assert outcome.progress_kind is MiningProgressKind.INVENTORY_INCREMENTED
    assert outcome.stop_reason is MiningOnlyStopReason.NONE


def test_runner_verifies_both_depletion_and_inventory_increment(tmp_path: Path) -> None:
    t0 = time.monotonic()
    f1 = _make_frame(1, t0, "frame1")
    f2 = _make_frame(2, t0 + 0.1, "frame2")
    capture = MockCaptureSource([f1, f2])

    pre_res = _make_resource_envelope(STANDARD_RESOURCES)
    pre_inv = _make_inventory_envelope(5)

    # Post state: nw rock depleted AND inventory incremented
    post_resources = (
        ResourceState("varrock-east-iron-nw", "iron", False, 0.92, None),
        ResourceState("varrock-east-iron-sw", "iron", True, 0.95, (100, 300, 40, 40)),
        ResourceState("varrock-east-iron-center", "iron", False, 0.88, None),
        ResourceState("varrock-east-iron-ne", "iron", False, 0.90, None),
    )
    post_res = _make_resource_envelope(post_resources)
    post_inv = _make_inventory_envelope(6)

    evaluator = SyntheticMiningPerceptionEvaluator([
        (pre_res, pre_inv),
        (post_res, post_inv),
    ])
    input_device = SyntheticMiningInputDevice()

    outcome = execute_one_controlled_attempt(
        capture_source=capture,
        evaluator=evaluator,
        input_device=input_device,
        evidence_dir=tmp_path,
        post_attempt_delay_s=0.0,
    )

    assert outcome.success is True
    assert outcome.progress_kind is MiningProgressKind.RESOURCE_DEPLETED_AND_INVENTORY_INCREMENTED
    assert outcome.stop_reason is MiningOnlyStopReason.NONE


def test_runner_stops_on_no_observed_progress(tmp_path: Path) -> None:
    t0 = time.monotonic()
    f1 = _make_frame(1, t0, "frame1")
    f2 = _make_frame(2, t0 + 0.1, "frame2")
    capture = MockCaptureSource([f1, f2])

    pre_res = _make_resource_envelope(STANDARD_RESOURCES)
    pre_inv = _make_inventory_envelope(5)

    # Post state: completely unchanged (no depletion, no inventory change)
    post_res = _make_resource_envelope(STANDARD_RESOURCES)
    post_inv = _make_inventory_envelope(5)

    evaluator = SyntheticMiningPerceptionEvaluator([
        (pre_res, pre_inv),
        (post_res, post_inv),
    ])
    input_device = SyntheticMiningInputDevice()

    outcome = execute_one_controlled_attempt(
        capture_source=capture,
        evaluator=evaluator,
        input_device=input_device,
        evidence_dir=tmp_path,
        post_attempt_delay_s=0.0,
    )

    # Must STOP and report failure; click dispatch is NOT success!
    assert outcome.success is False
    assert outcome.stop_reason is MiningOnlyStopReason.NO_OBSERVED_PROGRESS
    assert outcome.progress_kind is MiningProgressKind.NONE
    assert len(input_device.dispatch_calls) == 1  # Exactly one click, NO automatic retries!


def test_runner_stops_on_stale_frame(tmp_path: Path) -> None:
    t0 = time.monotonic()
    f1 = _make_frame(2, t0, "frame1")
    f2 = _make_frame(2, t0, "frame2")  # Same frame_id! Stale!
    capture = MockCaptureSource([f1, f2])

    pre_res = _make_resource_envelope(STANDARD_RESOURCES)
    pre_inv = _make_inventory_envelope(5)
    post_res = _make_resource_envelope(STANDARD_RESOURCES)
    post_inv = _make_inventory_envelope(6)

    evaluator = SyntheticMiningPerceptionEvaluator([
        (pre_res, pre_inv),
        (post_res, post_inv),
    ])
    input_device = SyntheticMiningInputDevice()

    outcome = execute_one_controlled_attempt(
        capture_source=capture,
        evaluator=evaluator,
        input_device=input_device,
        evidence_dir=tmp_path,
        post_attempt_delay_s=0.0,
    )

    assert outcome.success is False
    assert outcome.stop_reason is MiningOnlyStopReason.NEWER_OBSERVATION_REQUIRED


def test_runner_stops_on_full_inventory(tmp_path: Path) -> None:
    t0 = time.monotonic()
    f1 = _make_frame(1, t0, "frame1")
    capture = MockCaptureSource([f1])

    pre_res = _make_resource_envelope(STANDARD_RESOURCES)
    pre_inv = _make_inventory_envelope(28)  # Full!

    evaluator = SyntheticMiningPerceptionEvaluator([(pre_res, pre_inv)])
    input_device = SyntheticMiningInputDevice()

    outcome = execute_one_controlled_attempt(
        capture_source=capture,
        evaluator=evaluator,
        input_device=input_device,
        evidence_dir=tmp_path,
        post_attempt_delay_s=0.0,
    )

    assert outcome.success is False
    assert outcome.stop_reason is MiningOnlyStopReason.INVENTORY_FULL
    assert len(input_device.dispatch_calls) == 0  # No click sent when full!


def test_runner_stops_on_unknown_inventory(tmp_path: Path) -> None:
    t0 = time.monotonic()
    f1 = _make_frame(1, t0, "frame1")
    capture = MockCaptureSource([f1])

    pre_res = _make_resource_envelope(STANDARD_RESOURCES)
    pre_inv = _make_inventory_envelope(None, unknown_reason="tab_not_open")  # Unknown!

    evaluator = SyntheticMiningPerceptionEvaluator([(pre_res, pre_inv)])
    input_device = SyntheticMiningInputDevice()

    outcome = execute_one_controlled_attempt(
        capture_source=capture,
        evaluator=evaluator,
        input_device=input_device,
        evidence_dir=tmp_path,
        post_attempt_delay_s=0.0,
    )

    assert outcome.success is False
    assert outcome.stop_reason is MiningOnlyStopReason.INVENTORY_UNKNOWN
    assert len(input_device.dispatch_calls) == 0  # No click sent when inventory unknown!


def test_runner_stops_on_target_window_verification_failure(tmp_path: Path) -> None:
    t0 = time.monotonic()
    f1 = _make_frame(1, t0, "frame1")
    capture = MockCaptureSource([f1])
    evaluator = SyntheticMiningPerceptionEvaluator([])

    # Input device configured to fail window verification (e.g. wrong client, wrong resolution)
    input_device = SyntheticMiningInputDevice(should_fail_verification=True)

    outcome = execute_one_controlled_attempt(
        capture_source=capture,
        evaluator=evaluator,
        input_device=input_device,
        evidence_dir=tmp_path,
        post_attempt_delay_s=0.0,
    )

    assert outcome.success is False
    assert outcome.stop_reason is MiningOnlyStopReason.PUBLICATION_BLOCKED
    assert "verification failed" in outcome.detail
    assert len(input_device.dispatch_calls) == 0


def test_runner_stops_on_input_dispatch_failure(tmp_path: Path) -> None:
    t0 = time.monotonic()
    f1 = _make_frame(1, t0, "frame1")
    capture = MockCaptureSource([f1])

    pre_res = _make_resource_envelope(STANDARD_RESOURCES)
    pre_inv = _make_inventory_envelope(5)

    evaluator = SyntheticMiningPerceptionEvaluator([(pre_res, pre_inv)])
    input_device = SyntheticMiningInputDevice(should_fail_dispatch=True)

    outcome = execute_one_controlled_attempt(
        capture_source=capture,
        evaluator=evaluator,
        input_device=input_device,
        evidence_dir=tmp_path,
        post_attempt_delay_s=0.0,
    )

    assert outcome.success is False
    assert outcome.stop_reason is MiningOnlyStopReason.ATTEMPT_RECEIPT_INVALID
    assert "Input dispatch failed" in outcome.detail


def test_packaged_inventory_reference_is_known_empty() -> None:
    from mining_automation.perception.inventory.geometry import Region
    from mining_automation.perception.inventory.positive_classifier_v3 import (
        SUPPORTED_REGION,
    )

    region_path = (
        Path(__file__).parents[1]
        / "src"
        / "mining_automation"
        / "perception"
        / "inventory"
        / "profiles"
        / "varrock_east_empty_inventory_v3.bgra"
    )
    region = Region(*SUPPORTED_REGION)
    region_payload = region_path.read_bytes()
    payload = bytearray(EXPECTED_CLIENT_WIDTH * EXPECTED_CLIENT_HEIGHT * 4)
    source_stride = region.width * 4
    target_stride = EXPECTED_CLIENT_WIDTH * 4
    for row in range(region.height):
        source_start = row * source_stride
        target_start = (region.y + row) * target_stride + region.x * 4
        payload[target_start : target_start + source_stride] = region_payload[
            source_start : source_start + source_stride
        ]
    frame = Frame.from_raw(
        RawFrame(
            bytes(payload),
            EXPECTED_CLIENT_WIDTH,
            EXPECTED_CLIENT_HEIGHT,
            PixelFormat.BGRA8888,
        ),
        frame_id=1,
        captured_monotonic_s=1.0,
    )

    state, reason = ProductionMiningPerceptionEvaluator()._evaluate_packaged_inventory(
        frame
    )

    assert state.occupied_slots == 0
    assert state.confidence == 1.0
    assert reason is None


def test_runner_rejects_capture_from_different_hwnd(tmp_path: Path) -> None:
    t0 = time.monotonic()
    capture = MockCaptureSource([_make_frame(1, t0, "frame1")])
    input_device = SyntheticMiningInputDevice()

    outcome = execute_one_controlled_attempt(
        capture_source=capture,
        evaluator=SyntheticMiningPerceptionEvaluator([]),
        input_device=input_device,
        evidence_dir=tmp_path,
        post_attempt_delay_s=0.0,
        capture_hwnd_supplier=lambda: input_device.target_window.hwnd + 1,
    )

    assert outcome.success is False
    assert outcome.stop_reason is MiningOnlyStopReason.PUBLICATION_BLOCKED
    assert "capture HWND" in outcome.detail
    assert input_device.dispatch_calls == []
