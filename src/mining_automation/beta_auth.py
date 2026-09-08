"""Reviewed-screen-only logout/login. Missing logout evidence denies all auth input."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any

from .beta_input import InputPolicy
from .beta_interaction import click_at_proven_point, require_interior
from .capture import CaptureSource
from .capture.windows.backend import WindowsCaptureBackend
from .validation.session_recovery import (
    PLAY_NOW_CLIENT_POINT,
    PREAUTHENTICATED_STAGE,
    WELCOME_PLAY_CLIENT_POINT,
    WELCOME_PLAY_STAGE,
    SessionScreenAnchor,
    SessionScreenFingerprint,
    matches_session_screen,
    session_recovery_stage,
)

LOGOUT_PROFILE = "src/mining_automation/beta_profiles/logout.json"


def load_logout_profile(root: Path) -> list[dict[str, Any]]:
    path = root / LOGOUT_PROFILE
    if not path.is_file():
        raise RuntimeError("logout_control_live_fixture_missing; no_logout_input_authorized")
    committed = subprocess.run(
        ["git", "-C", str(root), "show", f"HEAD:{LOGOUT_PROFILE}"], check=False, capture_output=True
    )
    if committed.returncode or committed.stdout.replace(
        b"\r\n", b"\n"
    ) != path.read_bytes().replace(b"\r\n", b"\n"):
        raise RuntimeError("logout_profile_not_identical_to_frozen_committed_build")
    value = json.loads(committed.stdout.decode("utf-8"))
    if (
        value.get("review_issue") != 94
        or type(value.get("review_comment_id")) is not int
        or value["review_comment_id"] <= 0
    ):
        raise RuntimeError("logout_profile_requires_explicit_live_review_provenance")
    if value.get("version") != 1 or value.get("live_reviewed") is not True:
        raise RuntimeError("logout_profile_not_live_reviewed")
    steps = value.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 2:
        raise ValueError("Logout requires one or two reviewed control steps")
    allowed = ("open_logout_tab", "confirm_logout")[-len(steps) :]
    for index, step in enumerate(steps):
        if step.get("action") != allowed[index] or len(step.get("anchors", [])) < 2:
            raise ValueError("Unreviewed logout action or insufficient independent screen anchors")
        require_interior(tuple(step["point"]), tuple(step["click_region"]))
        x, y, w, h = step["click_region"]
        if min(x, y) < 0 or min(w, h) <= 0 or x + w > 1005 or y + h > 1078:
            raise ValueError("Logout action region exceeds the reviewed client")
        reference = (root / step["reference_path"]).resolve()
        if (
            not reference.is_relative_to((root / "diagnostics").resolve())
            or not reference.is_file()
        ):
            raise ValueError(
                "Logout source fixture must be retained privately in checkout diagnostics"
            )
        data = reference.read_bytes()
        if (
            len(data) != 1005 * 1078 * 4
            or hashlib.sha256(data).hexdigest() != step["reference_sha256"]
        ):
            raise ValueError("Logout source fixture hash/geometry mismatch")
        for anchor in step["anchors"]:
            x, y, w, h = anchor["region"]
            if min(x, y) < 0 or min(w, h) <= 0 or x + w > 1005 or y + h > 1078:
                raise ValueError("Invalid logout fingerprint anchor")
            if any(type(value) is not int for value in (x, y, w, h)):
                raise ValueError("Logout anchors require integer coordinates")
            crop = b"".join(
                data[(row * 1005 + x) * 4 : (row * 1005 + x + w) * 4] for row in range(y, y + h)
            )
            if hashlib.sha256(crop).hexdigest() != anchor["sha256"]:
                raise ValueError(
                    "Logout fingerprint does not match the retained source observation"
                )
        if len({tuple(a["region"]) for a in step["anchors"]}) < 2:
            raise ValueError("Logout anchors must identify independent regions")
    return steps


def fingerprint(step: dict[str, Any]) -> SessionScreenFingerprint:
    return SessionScreenFingerprint(
        step["action"],
        tuple(
            SessionScreenAnchor(tuple(anchor["region"]), anchor["sha256"])
            for anchor in step["anchors"]
        ),
    )


class AuthReader:
    def __init__(self, policy: InputPolicy) -> None:
        self.policy = policy
        self.backend = WindowsCaptureBackend(title_substring=policy.initial["identity"]["title"])
        self.source = CaptureSource(self.backend, max_consecutive_failures=2)
        self.source.open()
        self.receipts: list[dict[str, Any]] = []

    def close(self) -> None:
        self.source.close()

    def read(self) -> Any:
        self.policy.check()
        frame = self.source.capture()
        if (
            self.backend.selected_window is None
            or self.backend.selected_window.hwnd != self.policy.hwnd
        ):
            raise RuntimeError("authentication_capture_changed_HWND")
        self.policy.check()
        self.receipts.append(
            dict(
                frame_id=frame.frame_id,
                captured_monotonic_s=frame.captured_monotonic_s,
                sha256=hashlib.sha256(frame.payload).hexdigest(),
                stage=session_recovery_stage(frame),
            )
        )
        # Only hashes/stages are retained; no credential or private auth screenshots.
        return frame

    def click(self, point: tuple[int, int], matcher: Any) -> None:
        first = self.read()
        if not matcher(first):
            raise RuntimeError("expected_authentication_control_unproven; zero_click")
        mapping = self.policy.api.pointer_mapping(self.policy.hwnd, *point)
        if not mapping.exact_round_trip:
            raise RuntimeError("authentication_coordinate_mapping_unproven")
        screen = mapping.physical_screen.pair
        self.policy.move(screen, deadline=first.captured_monotonic_s + 1)
        fresh = self.read()
        if not matcher(fresh):
            raise RuntimeError("authentication_control_changed_during_motion; zero_click")
        audit: dict[str, Any] = dict(action_point=list(point), frame_id=fresh.frame_id)
        self.receipts.append(audit)
        click_at_proven_point(
            self.policy.api,
            screen,
            hwnd=self.policy.hwnd,
            check=self.policy.check,
            wait=self.policy.wait,
            deadline=fresh.captured_monotonic_s + 1,
            receipt=audit,
        )

    def wait_for(self, matcher: Any, timeout_s: float = 20) -> Any:
        end = time.monotonic() + timeout_s
        while time.monotonic() < end:
            frame = self.read()
            if matcher(frame):
                return frame
            self.policy.wait(0.2)
        raise RuntimeError("unknown_or_challenge_screen; owner_attention_required; no_retry_click")


def logout(policy: InputPolicy, root: Path) -> dict[str, Any]:
    steps = load_logout_profile(
        root
    )  # Fail before opening/clicking anything when evidence is absent.
    reader = AuthReader(policy)
    try:
        for step in steps:
            fp = fingerprint(step)
            reader.wait_for(lambda frame, fp=fp: matches_session_screen(frame, fp), timeout_s=8)
            reader.click(
                tuple(step["point"]), lambda frame, fp=fp: matches_session_screen(frame, fp)
            )
        for _ in range(2):
            reader.wait_for(lambda frame: session_recovery_stage(frame) == PREAUTHENTICATED_STAGE)
            policy.wait(0.2)
        return dict(
            success=True,
            logout_verified=True,
            deliberate=True,
            logout_verified_monotonic_s=time.monotonic(),
            receipts=reader.receipts,
        )
    finally:
        reader.close()


def login(policy: InputPolicy, root: Path) -> dict[str, Any]:
    from .beta_home import verify_home

    reader = AuthReader(policy)
    try:
        for stage, point in (
            (PREAUTHENTICATED_STAGE, PLAY_NOW_CLIENT_POINT),
            (WELCOME_PLAY_STAGE, WELCOME_PLAY_CLIENT_POINT),
        ):
            reader.wait_for(lambda frame, stage=stage: session_recovery_stage(frame) == stage)
            reader.click(point, lambda frame, stage=stage: session_recovery_stage(frame) == stage)
        # No blind repeated Play clicks; unknown/connecting canvases get observations only.
        end = time.monotonic() + 20
        last_reason = "gameplay_not_observed"
        while time.monotonic() < end:
            policy.check()
            try:
                home = verify_home(
                    policy.native, policy, root, fresh_rocks=True, allow_normalize=False
                )
                return dict(
                    success=True, login_verified=True, home_proof=home, receipts=reader.receipts
                )
            except RuntimeError as exc:
                last_reason = str(exc)
                policy.check()
                policy.wait(0.2)
        raise RuntimeError(f"login_gameplay_reacquisition_unproven:{last_reason}")
    finally:
        reader.close()
