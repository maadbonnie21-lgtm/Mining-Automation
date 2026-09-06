from __future__ import annotations

from dataclasses import replace

from mining_automation.validation.runelite_prep import (
    EXPECTED_CLIENT_HEIGHT,
    EXPECTED_CLIENT_WIDTH,
    PREP_CONFIRMATION,
    PrepActionReceipt,
    PrepCameraStep,
    PrepMode,
    PrepPoseReferenceReceipt,
    PrepSceneObservation,
    PrepStopReason,
    PrepWindowIdentity,
    PrepWindowSnapshot,
    run_runelite_prep,
)

_GIT_SHA = "a" * 40
_SHA256 = "b" * 64


class _FocusShrinksClientBackend:
    """Model the observed Java/AWT focus transition that changed 1078 -> 687."""

    def __init__(self) -> None:
        self.window = PrepWindowSnapshot(
            hwnd=42,
            identity=PrepWindowIdentity(100, 200, "SunAwtFrame", "RuneLite - Test"),
            visible=True,
            minimized=False,
            foreground=False,
            client_width=EXPECTED_CLIENT_WIDTH,
            client_height=EXPECTED_CLIENT_HEIGHT,
            dpi=96,
        )
        self.actions: list[str] = []

    def snapshot(self) -> PrepWindowSnapshot:
        return self.window

    def verify_pose_references(self) -> tuple[PrepPoseReferenceReceipt, ...]:
        return tuple(
            PrepPoseReferenceReceipt(
                pose_id=f"pose-{index}",
                relative_path=f"diagnostics/pose-{index}.bgra",
                sha256=_SHA256,
                byte_count=EXPECTED_CLIENT_WIDTH * EXPECTED_CLIENT_HEIGHT * 4,
                width=EXPECTED_CLIENT_WIDTH,
                height=EXPECTED_CLIENT_HEIGHT,
            )
            for index in range(3)
        )

    def restore_window(self) -> PrepActionReceipt:
        raise AssertionError("restore must not be needed in this regression")

    def focus_window(self) -> PrepActionReceipt:
        self.actions.append("focus")
        self.window = replace(
            self.window,
            foreground=True,
            client_height=687,
        )
        return PrepActionReceipt("foreground_window", 1, 1)

    def resize_client(self, width: int, height: int) -> PrepActionReceipt:
        self.actions.append("resize")
        self.window = replace(
            self.window,
            client_width=width,
            client_height=height,
        )
        return PrepActionReceipt("resize_client_area", 1, 1)

    def neutralize_cursor(self) -> PrepActionReceipt:
        self.actions.append("neutral")
        return PrepActionReceipt("neutral_cursor", 0, 0)

    def recover_session(self) -> PrepActionReceipt:
        return PrepActionReceipt("play_now_click", 2, 2)

    def observe(self) -> PrepSceneObservation:
        self.actions.append("observe")
        return PrepSceneObservation(
            frame_id=len(self.actions),
            frame_sha256=_SHA256,
            gameplay_ready=True,
            gameplay_reason="ready",
            inventory_occupied=0,
            inventory_confidence=1.0,
            inventory_unknown_reason=None,
            resource_supported=True,
            resource_view="supported",
            accepted_pose_id="at_southwest",
            software_registration_identity=None,
            matched_landmarks=6,
            matched_zones=("north_west", "north_east", "south_west"),
            landmark_distances=tuple(
                (f"landmark-{index}", 0.01) for index in range(6)
            ),
        )

    def camera_action(self, step: PrepCameraStep) -> tuple[PrepActionReceipt, ...]:
        raise AssertionError(f"camera action must not run for supported scene: {step}")

    def cleanup(self) -> tuple[PrepActionReceipt, ...]:
        return ()


def test_focus_induced_1005x687_is_corrected_before_perception() -> None:
    backend = _FocusShrinksClientBackend()

    result = run_runelite_prep(
        backend,
        mode=PrepMode.APPLY,
        git_sha=_GIT_SHA,
        prep_session_id="focus-resize-regression",
        confirm=PREP_CONFIRMATION,
    )

    assert result.ready_for_mining is True
    assert result.stop_reason is PrepStopReason.NONE
    assert result.final_window is not None
    assert result.final_window.foreground is True
    assert result.final_window.dpi == 96
    assert (
        result.final_window.client_width,
        result.final_window.client_height,
    ) == (1005, 1078)
    assert backend.actions[:2] == ["focus", "resize"]
    assert backend.actions.index("resize") < backend.actions.index("observe")
