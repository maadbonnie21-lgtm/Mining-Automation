from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from mining_automation.validation.runelite_prep import (
    EXPECTED_CLIENT_HEIGHT,
    EXPECTED_CLIENT_WIDTH,
    PREP_CONFIRMATION,
    PrepActionReceipt,
    PrepCameraStep,
    PrepMode,
    PrepOperationError,
    PrepPoseReferenceReceipt,
    PrepSceneObservation,
    PrepStopReason,
    PrepWindowIdentity,
    PrepWindowSnapshot,
    run_runelite_prep,
)
from mining_automation.validation.session_recovery import (
    DISCONNECTED_STAGE,
    PREAUTHENTICATED_STAGE,
    WELCOME_PLAY_STAGE,
)

GIT_SHA = "a" * 40
FRAME_SHA = "b" * 64
REF_SHA = "c" * 64


def _identity(*, process_id: int = 100) -> PrepWindowIdentity:
    return PrepWindowIdentity(process_id, 200, "SunAwtFrame", "RuneLite - Test")


def _window(**changes: object) -> PrepWindowSnapshot:
    base = PrepWindowSnapshot(
        hwnd=42,
        identity=_identity(),
        visible=True,
        minimized=False,
        foreground=True,
        client_width=EXPECTED_CLIENT_WIDTH,
        client_height=EXPECTED_CLIENT_HEIGHT,
        dpi=96,
    )
    return replace(base, **changes)


def _refs() -> tuple[PrepPoseReferenceReceipt, ...]:
    return tuple(
        PrepPoseReferenceReceipt(
            pose_id=f"pose-{index}",
            relative_path=f"diagnostics/pose-{index}.bgra",
            sha256=REF_SHA,
            byte_count=EXPECTED_CLIENT_WIDTH * EXPECTED_CLIENT_HEIGHT * 4,
            width=EXPECTED_CLIENT_WIDTH,
            height=EXPECTED_CLIENT_HEIGHT,
        )
        for index in range(3)
    )


def _observation(
    *,
    gameplay_ready: bool = True,
    inventory: int | None = 0,
    inventory_confidence: float = 1.0,
    resource_supported: bool = True,
    matched: int = 6,
    zones: tuple[str, ...] = ("north_west", "north_east", "south_west"),
    score: float | None = 0.0,
    session_recovery_ready: bool = False,
    session_recovery_stage: str | None = None,
) -> PrepSceneObservation:
    if session_recovery_ready and session_recovery_stage is None:
        session_recovery_stage = PREAUTHENTICATED_STAGE
    return PrepSceneObservation(
        frame_id=1,
        frame_sha256=FRAME_SHA,
        gameplay_ready=gameplay_ready,
        gameplay_reason="ready" if gameplay_ready else "chrome mismatch",
        inventory_occupied=inventory,
        inventory_confidence=inventory_confidence,
        inventory_unknown_reason=("inventory tooltip obstructed" if inventory is None else None),
        resource_supported=resource_supported,
        resource_view="supported" if resource_supported else "unsupported",
        accepted_pose_id="at_southwest" if resource_supported else None,
        software_registration_identity=None,
        matched_landmarks=matched,
        matched_zones=zones,
        landmark_distances=tuple(
            (f"landmark-{index}", 0.01 if index < matched else 0.5)
            for index in range(6)
        ),
        diagnostic_score=score,
        frame_path="diagnostics/fake.bgra",
        session_recovery_ready=session_recovery_ready,
        session_recovery_stage=session_recovery_stage,
    )


class FakePrepBackend:
    def __init__(
        self,
        *,
        window: PrepWindowSnapshot | None = None,
        observations: list[PrepSceneObservation] | None = None,
    ) -> None:
        self.window = window or _window()
        self.observations = observations or [_observation()]
        self.observe_index = 0
        self.action_calls: list[str] = []
        self.camera_calls: list[PrepCameraStep] = []
        self.cleanup_calls = 0
        self.cleanup_released = False
        self.missing_refs = False
        self.partial_camera = False
        self.camera_error: PrepOperationError | None = None
        self.foreground_loss_after_camera = False
        self.identity_change_after_focus = False
        self.partial_recovery = False
        self.recovery_calls = 0
        self.recovery_stages: list[str] = []

    @property
    def setup_event_count(self) -> int:
        return sum(1 for action in self.action_calls if action != "observe")

    def snapshot(self) -> PrepWindowSnapshot:
        return self.window

    def verify_pose_references(self) -> tuple[PrepPoseReferenceReceipt, ...]:
        if self.missing_refs:
            raise PrepOperationError(
                PrepStopReason.POSE_REFERENCES_INVALID,
                "required local pose reference is missing",
            )
        return _refs()

    def restore_window(self) -> PrepActionReceipt:
        self.action_calls.append("restore")
        self.window = replace(self.window, minimized=False, visible=True)
        return PrepActionReceipt("restore_window", 1, 1)

    def resize_client(self, width: int, height: int) -> PrepActionReceipt:
        self.action_calls.append("resize")
        self.window = replace(self.window, client_width=width, client_height=height)
        return PrepActionReceipt("resize_client_area", 1, 1)

    def focus_window(self) -> PrepActionReceipt:
        self.action_calls.append("focus")
        identity = self.window.identity
        if self.identity_change_after_focus:
            identity = _identity(process_id=999)
        self.window = replace(self.window, foreground=True, identity=identity)
        return PrepActionReceipt("foreground_window", 1, 1)

    def neutralize_cursor(self) -> PrepActionReceipt:
        self.action_calls.append("neutral")
        # Simulate already-neutral/idempotent cursor state: no OS event required.
        return PrepActionReceipt("neutral_cursor", 0, 0)

    def recover_session(self, stage: str) -> PrepActionReceipt:
        self.recovery_calls += 1
        self.recovery_stages.append(stage)
        self.action_calls.append("recover_session")
        if stage == DISCONNECTED_STAGE:
            action = "disconnected_ok_click"
        elif stage == PREAUTHENTICATED_STAGE:
            action = "play_now_click"
        else:
            action = "welcome_play_click"
        return PrepActionReceipt(action, 2, 1 if self.partial_recovery else 2)

    def observe(self) -> PrepSceneObservation:
        self.action_calls.append("observe")
        index = min(self.observe_index, len(self.observations) - 1)
        self.observe_index += 1
        return self.observations[index]

    def camera_action(self, step: PrepCameraStep) -> tuple[PrepActionReceipt, ...]:
        self.camera_calls.append(step)
        self.action_calls.append(f"camera:{step.value}")
        if self.camera_error is not None:
            raise self.camera_error
        if self.foreground_loss_after_camera:
            self.window = replace(self.window, foreground=False)
        if self.partial_camera:
            return (PrepActionReceipt(step.value, 1, 0),)
        return (PrepActionReceipt(step.value, 1, 1),)

    def cleanup(self) -> tuple[PrepActionReceipt, ...]:
        self.cleanup_calls += 1
        self.cleanup_released = True
        return ()


def _run(
    backend: FakePrepBackend,
    *,
    mode: PrepMode = PrepMode.APPLY,
    confirm: str | None = PREP_CONFIRMATION,
    camera_steps: tuple[PrepCameraStep, ...] = (
        PrepCameraStep.PITCH_DOWN_100MS,
        PrepCameraStep.PITCH_UP_50MS,
    ),
):
    return run_runelite_prep(
        backend,
        mode=mode,
        git_sha=GIT_SHA,
        prep_session_id="prep-test",
        confirm=confirm,
        camera_steps=camera_steps,
        session_recovery_sleeper=lambda _: None,
    )


def test_correct_read_only_state_is_ready_with_zero_setup_input() -> None:
    backend = FakePrepBackend()
    result = _run(backend, mode=PrepMode.READ_ONLY, confirm=None)
    assert result.ready_for_mining is True
    assert result.stop_reason is PrepStopReason.NONE
    assert backend.action_calls == ["observe"]
    assert backend.camera_calls == []


def test_wrong_client_height_is_not_ready_in_read_only_mode() -> None:
    backend = FakePrepBackend(window=_window(client_height=687))
    result = _run(backend, mode=PrepMode.READ_ONLY, confirm=None)
    assert result.ready_for_mining is False
    assert result.stop_reason is PrepStopReason.CLIENT_GEOMETRY_MISMATCH
    assert "observe" not in backend.action_calls


def test_apply_corrects_simulated_client_geometry() -> None:
    backend = FakePrepBackend(window=_window(client_height=687))
    result = _run(backend)
    assert result.ready_for_mining is True
    assert "resize" in backend.action_calls
    assert result.final_window is not None
    assert (result.final_window.client_width, result.final_window.client_height) == (1005, 1078)


def test_minimized_target_is_never_silently_ready() -> None:
    read_only = FakePrepBackend(window=_window(minimized=True, client_width=0, client_height=0))
    result = _run(read_only, mode=PrepMode.READ_ONLY, confirm=None)
    assert result.stop_reason is PrepStopReason.WINDOW_MINIMIZED

    apply = FakePrepBackend(window=_window(minimized=True, client_width=1005, client_height=1078))
    applied = _run(apply)
    assert applied.ready_for_mining is True
    assert "restore" in apply.action_calls


def test_non_96_dpi_stops_before_any_setup_input() -> None:
    backend = FakePrepBackend(window=_window(dpi=120))
    result = _run(backend)
    assert result.stop_reason is PrepStopReason.DPI_MISMATCH
    assert backend.action_calls == []


def test_reused_hwnd_identity_stops() -> None:
    backend = FakePrepBackend(window=_window(foreground=False))
    backend.identity_change_after_focus = True
    result = _run(backend)
    assert result.ready_for_mining is False
    assert result.stop_reason is PrepStopReason.WINDOW_IDENTITY_CHANGED


def test_missing_local_pose_reference_stops() -> None:
    backend = FakePrepBackend()
    backend.missing_refs = True
    result = _run(backend)
    assert result.stop_reason is PrepStopReason.POSE_REFERENCES_INVALID
    assert backend.action_calls == []


def test_gameplay_chrome_mismatch_stops_before_camera_input() -> None:
    backend = FakePrepBackend(observations=[_observation(gameplay_ready=False, resource_supported=False, matched=0, zones=())])
    result = _run(backend)
    assert result.stop_reason is PrepStopReason.GAMEPLAY_CHROME_MISMATCH
    assert backend.camera_calls == []
    assert backend.recovery_calls == 0


def test_pre_authenticated_session_recovers_once_then_reobserves_cleanly() -> None:
    login = _observation(
        gameplay_ready=False, inventory=None, resource_supported=False, matched=0, zones=(),
        session_recovery_ready=True,
    )
    transition = _observation(
        gameplay_ready=True, inventory=0, resource_supported=False, matched=0, zones=(),
    )
    ready = _observation(gameplay_ready=True, inventory=0, resource_supported=True)
    backend = FakePrepBackend(observations=[login, transition, ready])
    result = _run(backend)
    assert result.ready_for_mining is True
    assert backend.recovery_calls == 1
    assert backend.camera_calls == []
    assert backend.action_calls.count("recover_session") == 1
    assert backend.action_calls.count("neutral") == 3


def test_two_stage_session_recovery_enters_gameplay_without_repeating_stage() -> None:
    login = _observation(
        gameplay_ready=False, inventory=None, resource_supported=False, matched=0, zones=(),
        session_recovery_ready=True, session_recovery_stage=PREAUTHENTICATED_STAGE,
    )
    welcome = _observation(
        gameplay_ready=False, inventory=None, resource_supported=False, matched=0, zones=(),
        session_recovery_ready=True, session_recovery_stage=WELCOME_PLAY_STAGE,
    )
    gameplay_probe = _observation(
        gameplay_ready=True, inventory=0, resource_supported=False, matched=0, zones=(),
    )
    ready = _observation(gameplay_ready=True, inventory=0, resource_supported=True)
    backend = FakePrepBackend(observations=[login, welcome, gameplay_probe, ready])
    result = _run(backend)
    assert result.ready_for_mining is True
    assert backend.recovery_calls == 2
    assert backend.recovery_stages == [PREAUTHENTICATED_STAGE, WELCOME_PLAY_STAGE]
    assert backend.camera_calls == []


def test_three_stage_disconnect_recovery_enters_gameplay_once_per_stage() -> None:
    disconnected = _observation(
        gameplay_ready=False, inventory=None, resource_supported=False, matched=0, zones=(),
        session_recovery_ready=True, session_recovery_stage=DISCONNECTED_STAGE,
    )
    login = _observation(
        gameplay_ready=False, inventory=None, resource_supported=False, matched=0, zones=(),
        session_recovery_ready=True, session_recovery_stage=PREAUTHENTICATED_STAGE,
    )
    welcome = _observation(
        gameplay_ready=False, inventory=None, resource_supported=False, matched=0, zones=(),
        session_recovery_ready=True, session_recovery_stage=WELCOME_PLAY_STAGE,
    )
    gameplay_probe = _observation(
        gameplay_ready=True, inventory=0, resource_supported=False, matched=0, zones=(),
    )
    ready = _observation(gameplay_ready=True, inventory=0, resource_supported=True)
    backend = FakePrepBackend(
        observations=[disconnected, login, welcome, gameplay_probe, ready]
    )
    result = _run(backend)
    assert result.ready_for_mining is True
    assert backend.recovery_calls == 3
    assert backend.recovery_stages == [
        DISCONNECTED_STAGE,
        PREAUTHENTICATED_STAGE,
        WELCOME_PLAY_STAGE,
    ]
    assert backend.camera_calls == []


def test_pre_authenticated_session_short_click_receipt_stops_without_retry() -> None:
    login = _observation(
        gameplay_ready=False, inventory=None, resource_supported=False, matched=0, zones=(),
        session_recovery_ready=True,
    )
    backend = FakePrepBackend(observations=[login])
    backend.partial_recovery = True
    result = _run(backend)
    assert result.stop_reason is PrepStopReason.SESSION_RECOVERY_FAILED
    assert backend.recovery_calls == 1
    assert backend.camera_calls == []


def test_pre_authenticated_session_never_clicks_twice_when_transition_stalls() -> None:
    login = _observation(
        gameplay_ready=False, inventory=None, resource_supported=False, matched=0, zones=(),
        session_recovery_ready=True,
    )
    backend = FakePrepBackend(observations=[login])
    result = _run(backend)
    assert result.stop_reason is PrepStopReason.SESSION_RECOVERY_FAILED
    assert backend.recovery_calls == 1
    assert backend.camera_calls == []


def test_already_supported_camera_sends_zero_camera_actions() -> None:
    backend = FakePrepBackend(observations=[_observation(resource_supported=True)])
    result = _run(backend)
    assert result.ready_for_mining is True
    assert backend.camera_calls == []


def test_short_camera_receipt_stops() -> None:
    backend = FakePrepBackend(observations=[_observation(resource_supported=False, matched=0, zones=())])
    backend.partial_camera = True
    result = _run(backend)
    assert result.stop_reason is PrepStopReason.CAMERA_RECEIPT_INCOMPLETE
    assert len(backend.camera_calls) == 1


def test_foreground_loss_during_camera_prep_stops() -> None:
    backend = FakePrepBackend(observations=[_observation(resource_supported=False, matched=0, zones=())])
    backend.foreground_loss_after_camera = True
    result = _run(backend)
    assert result.stop_reason is PrepStopReason.CAMERA_INPUT_REJECTED


def test_held_mouse_or_key_veto_before_camera_operation() -> None:
    backend = FakePrepBackend(observations=[_observation(resource_supported=False, matched=0, zones=())])
    backend.camera_error = PrepOperationError(
        PrepStopReason.INPUT_STATE_UNSAFE,
        "validator-controlled global key is already held",
    )
    result = _run(backend)
    assert result.stop_reason is PrepStopReason.INPUT_STATE_UNSAFE
    assert len(backend.camera_calls) == 1


def test_bounded_camera_search_exhaustion_stops() -> None:
    backend = FakePrepBackend(
        observations=[
            _observation(resource_supported=False, matched=0, zones=()),
            _observation(resource_supported=False, matched=1, zones=("north_west",)),
            _observation(resource_supported=False, matched=2, zones=("north_west", "north_east")),
        ]
    )
    result = _run(backend)
    assert result.stop_reason is PrepStopReason.CAMERA_SEARCH_EXHAUSTED
    assert len(backend.camera_calls) == 2


def test_improved_diagnostic_metric_cannot_turn_zero_of_six_into_ready() -> None:
    backend = FakePrepBackend(
        observations=[
            _observation(resource_supported=False, matched=0, zones=(), score=0.9),
            _observation(resource_supported=False, matched=0, zones=(), score=0.3),
            _observation(resource_supported=False, matched=0, zones=(), score=0.1),
        ]
    )
    result = _run(backend)
    assert result.ready_for_mining is False
    assert result.stop_reason is PrepStopReason.CAMERA_SEARCH_EXHAUSTED


@pytest.mark.parametrize(
    ("matched", "zones"),
    [
        (4, ("north_west", "north_east", "south_west")),
        (5, ("north_west", "north_east")),
    ],
)
def test_ready_requires_five_of_six_and_all_three_zones(
    matched: int,
    zones: tuple[str, ...],
) -> None:
    backend = FakePrepBackend(
        observations=[_observation(resource_supported=True, matched=matched, zones=zones)]
    )
    result = _run(backend, mode=PrepMode.READ_ONLY, confirm=None)
    assert result.ready_for_mining is False
    assert result.stop_reason is PrepStopReason.RESOURCE_SCENE_UNSUPPORTED


def test_inventory_unknown_or_tooltip_is_not_ready() -> None:
    backend = FakePrepBackend(observations=[_observation(inventory=None, inventory_confidence=0.0)])
    result = _run(backend)
    assert result.stop_reason is PrepStopReason.INVENTORY_UNKNOWN
    assert "tooltip" in result.detail


def test_inventory_below_floor_is_not_ready() -> None:
    backend = FakePrepBackend(observations=[_observation(inventory=3, inventory_confidence=0.79)])
    result = _run(backend)
    assert result.stop_reason is PrepStopReason.INVENTORY_CONFIDENCE_BELOW_FLOOR


def test_cleanup_runs_after_camera_exception() -> None:
    backend = FakePrepBackend(observations=[_observation(resource_supported=False, matched=0, zones=())])
    backend.camera_error = PrepOperationError(
        PrepStopReason.CAMERA_INPUT_REJECTED,
        "simulated OS exception after guarded operation",
    )
    result = _run(backend)
    assert result.ready_for_mining is False
    assert backend.cleanup_calls == 1
    assert backend.cleanup_released is True


def test_prep_source_has_no_mining_navigation_or_banking_actuation_path() -> None:
    root = Path(__file__).resolve().parents[1]
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            root / "src/mining_automation/validation/runelite_prep.py",
            root / "tools/runelite_prep.py",
        )
    )
    forbidden = (
        "dispatch_one_click",
        "RealWin32MiningInputDevice",
        "run_mining_to_full",
        "deposit_all",
        "navigate_to_bank",
        "navigate_to_mine",
    )
    assert not any(token in text for token in forbidden)


def test_apply_requires_exact_prep_confirmation() -> None:
    backend = FakePrepBackend()
    result = _run(backend, confirm="wrong-token")
    assert result.stop_reason is PrepStopReason.PREP_CONFIRMATION_REQUIRED
    assert backend.action_calls == []


def test_second_apply_run_when_already_ready_is_effectively_no_input() -> None:
    backend = FakePrepBackend()
    first = _run(backend)
    assert first.ready_for_mining is True
    first_input_events = sum(action.requested_events for action in first.actions)
    second = _run(backend)
    assert second.ready_for_mining is True
    second_input_events = sum(action.requested_events for action in second.actions)
    assert first_input_events == 0
    assert second_input_events == 0
    assert backend.camera_calls == []



def test_ready_independently_rechecks_landmark_distances_at_point_12() -> None:
    observation = replace(
        _observation(resource_supported=True, matched=5),
        landmark_distances=tuple(
            (f"landmark-{index}", 0.13 if index < 5 else 0.5)
            for index in range(6)
        ),
    )
    backend = FakePrepBackend(observations=[observation])
    result = _run(backend, mode=PrepMode.READ_ONLY, confirm=None)
    assert result.ready_for_mining is False
    assert result.stop_reason is PrepStopReason.RESOURCE_SCENE_UNSUPPORTED


def test_ready_rejects_three_wrong_zone_names_even_with_five_matches() -> None:
    backend = FakePrepBackend(
        observations=[
            _observation(
                resource_supported=True,
                matched=5,
                zones=("wrong_a", "wrong_b", "wrong_c"),
            )
        ]
    )
    result = _run(backend, mode=PrepMode.READ_ONLY, confirm=None)
    assert result.ready_for_mining is False
    assert result.stop_reason is PrepStopReason.RESOURCE_SCENE_UNSUPPORTED


def test_exact_five_of_six_distances_and_all_three_zones_can_pass() -> None:
    backend = FakePrepBackend(
        observations=[
            _observation(
                resource_supported=True,
                matched=5,
                zones=("north_west", "north_east", "south_west"),
            )
        ]
    )
    result = _run(backend, mode=PrepMode.READ_ONLY, confirm=None)
    assert result.ready_for_mining is True


def test_five_retained_inlier_distances_across_all_three_zones_can_pass() -> None:
    observation = replace(
        _observation(resource_supported=True, matched=5),
        landmark_distances=tuple(
            (f"landmark-{index}", 0.01) for index in range(5)
        ),
    )
    backend = FakePrepBackend(observations=[observation])
    result = _run(backend, mode=PrepMode.READ_ONLY, confirm=None)
    assert result.ready_for_mining is True
    assert result.stop_reason is PrepStopReason.NONE


def test_four_retained_distances_cannot_satisfy_five_of_six_quorum() -> None:
    observation = replace(
        _observation(resource_supported=True, matched=4),
        landmark_distances=tuple(
            (f"landmark-{index}", 0.01) for index in range(4)
        ),
    )
    backend = FakePrepBackend(observations=[observation])
    result = _run(backend, mode=PrepMode.READ_ONLY, confirm=None)
    assert result.ready_for_mining is False
    assert result.stop_reason is PrepStopReason.RESOURCE_SCENE_UNSUPPORTED


def test_default_prep_evidence_is_ignored_before_separate_miner_handoff() -> None:
    root = Path(__file__).resolve().parents[1]
    ignore = (root / ".gitignore").read_text(encoding="utf-8")
    assert "/diagnostics/runelite-prep-*/" in ignore

    tool = (root / "tools/runelite_prep.py").read_text(encoding="utf-8")
    clean_index = tool.index("checkout_clean = _checkout_clean()")
    mkdir_index = tool.index("output.mkdir(parents=True)")
    assert clean_index < mkdir_index
    assert "if mode is PrepMode.APPLY and not _checkout_clean()" not in tool
    assert "if result.ready_for_mining and not _checkout_clean():" in tool


def test_default_apply_unsupported_view_sends_zero_camera_input() -> None:
    backend = FakePrepBackend(
        observations=[_observation(resource_supported=False, matched=0, zones=())]
    )
    result = run_runelite_prep(
        backend,
        mode=PrepMode.APPLY,
        git_sha=GIT_SHA,
        prep_session_id="prep-default-no-camera",
        confirm=PREP_CONFIRMATION,
    )
    assert result.ready_for_mining is False
    assert result.stop_reason is PrepStopReason.RESOURCE_SCENE_UNSUPPORTED
    assert backend.camera_calls == []
    assert "no evidence-backed automatic camera" in result.detail
