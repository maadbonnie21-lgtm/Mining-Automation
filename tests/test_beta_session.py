from __future__ import annotations

import json
from dataclasses import replace

import pytest

from mining_automation.beta_session import (
    BetaSession,
    RunBreakRow,
    SessionControls,
    SessionSettings,
    SessionUnproven,
    require_home,
)


def home():
    return dict(
        mine_arrival_verified=True,
        inventory_empty_verified=True,
        bank_closed_verified=True,
        window_unchanged=True,
        stationary_verified=True,
        fresh=True,
        fresh_rocks_verified=True,
    )


class Clock:
    def __init__(self):
        self.now = 0.0
        self.on_sleep = lambda: None

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        self.on_sleep()


class Backend:
    def __init__(self, control, clock):
        self.control, self.clock = control, clock
        self.calls = []
        self.on_phase = lambda phase: None
        self.on_home = lambda: None
        self.cycles = 0
        self.logins = 0
        self.bad_logout = False

    def verify_home(self, *, fresh_rocks):
        self.calls.append(("home", fresh_rocks))
        self.on_home()
        return home()

    def cycle(self, number, heartbeat):
        self.cycles += 1
        for phase in ("mine", "outbound", "bank", "return"):
            self.calls.append(phase)
            self.on_phase(phase)
            heartbeat(phase)
            self.clock.now += 1
        return dict(success=True, deposited_ore=28, cycle=number)

    def logout(self):
        self.calls.append("logout")
        self.clock.now += 3
        return dict(logout_verified=not self.bad_logout, deliberate=True)

    def login(self):
        self.control.check(authentication=True)
        self.calls.append("login")
        self.logins += 1
        if self.logins == 2:
            raise SessionUnproven("challenge")
        return dict(login_verified=True)

    def cancel(self):
        self.calls.append("cancel")


def make(tmp_path, settings=None):
    clock, control = Clock(), SessionControls()
    backend = Backend(control, clock)
    session = BetaSession(
        settings or SessionSettings(mode="finite", cycles=1),
        control,
        backend,
        tmp_path / "run",
        sha="a" * 40,
        clock=clock,
        sleep=clock.sleep,
    )
    return session, control, backend, clock


@pytest.mark.parametrize("mode", ["finite", "continuous"])
def test_unlimited_has_no_old_five_cycle_cap(tmp_path, mode):
    session, control, backend, clock = make(tmp_path, SessionSettings(mode=mode, cycles=8))

    def stop(phase):
        if backend.cycles == 8 and phase == "mine" and mode == "continuous":
            control.request_stop()

    backend.on_phase = stop
    result = session.run()
    assert result["state"] == "STOPPED"
    assert result["cycles_completed"] == 8
    assert result["ore_deposited"] == 224
    assert result["home_proof"]["mine_arrival_verified"]


@pytest.mark.parametrize("phase", ["mine", "outbound", "bank", "return"])
def test_normal_stop_finishes_current_cycle_only(tmp_path, phase):
    session, control, backend, clock = make(tmp_path, SessionSettings())
    backend.on_phase = lambda p: control.request_stop() if p == phase else None
    result = session.run()
    assert result["state"] == "STOPPED"
    assert backend.cycles == 1
    assert backend.calls.count("return") == 1
    assert "cancel" not in backend.calls
    assert result["normal_stop_requested"]


@pytest.mark.parametrize("phase", ["mine", "outbound", "bank", "return"])
def test_emergency_does_not_finish_or_login(tmp_path, phase):
    session, control, backend, clock = make(tmp_path)
    backend.on_phase = lambda p: control.request_emergency() if p == phase else None
    result = session.run()
    assert result["state"] == "EMERGENCY_STOPPED"
    assert not result["success"]
    assert result["cycles_completed"] == 0
    assert backend.calls[-1] == "cancel"
    assert backend.logins == 0
    assert "return_not_completed" in result["reason"]


def test_break_countdown_starts_after_slow_return_and_logout(tmp_path):
    settings = SessionSettings(mode="routine", routine=(RunBreakRow(1, 2),), repeat=False)
    session, control, backend, clock = make(tmp_path, settings)
    result = session.run()
    assert result["break_started_monotonic"] == 7  # Four phases then actual logout.
    assert clock.now >= 9
    assert backend.logins == 0
    assert result["state"] == "STOPPED"
    assert result["logout_proof"]["logout_verified"]


def test_stop_in_break_cancels_login(tmp_path):
    session, control, backend, clock = make(
        tmp_path, SessionSettings(mode="routine", routine=(RunBreakRow(1, 10),))
    )
    clock.on_sleep = control.request_stop
    result = session.run()
    assert backend.logins == 0
    assert result["state"] == "STOPPED"
    assert result["break_remaining_s"] > 0
    assert result["login_permitted"] is False


def test_unverified_logout_never_starts_break(tmp_path):
    session, control, backend, clock = make(
        tmp_path, SessionSettings(mode="routine", routine=(RunBreakRow(1, 10),))
    )
    backend.bad_logout = True
    result = session.run()
    assert result["state"] == "ERROR"
    assert result["break_started_monotonic"] is None
    assert backend.logins == 0


def test_repeat_requires_login_then_fresh_home_and_rocks(tmp_path):
    session, control, backend, clock = make(
        tmp_path, SessionSettings(mode="routine", routine=(RunBreakRow(1, 1),))
    )
    result = session.run()
    login_index = backend.calls.index("login")
    assert backend.calls[login_index + 1] == ("home", True)
    assert result["state"] == "ERROR"  # The second login injects a challenge.
    assert result["interrupted"] is True
    assert backend.logins == 2
    assert backend.calls[-1] == "cancel"


def test_failure_cannot_publish_clean_stopped(tmp_path):
    session, control, backend, clock = make(tmp_path)
    backend.on_home = lambda: (_ for _ in ()).throw(SessionUnproven("position_unknown"))
    result = session.run()
    assert result["state"] == "ERROR"
    assert result["cycles_completed"] == 0
    assert backend.cycles == 0
    saved = json.loads((tmp_path / "run/result.json").read_text())
    assert saved["reason"] == result["reason"]
    assert saved["success"] is False


@pytest.mark.parametrize("field", list(home()))
def test_home_requires_all_endpoint_proofs(field):
    proof = home()
    proof[field] = False
    if field == "fresh_rocks_verified":
        require_home(proof)  # Rocks are additionally required at start/resume only.
    else:
        with pytest.raises(SessionUnproven):
            require_home(proof)


@pytest.mark.parametrize("value", [-1, 0, float("nan"), float("inf"), True])
def test_invalid_durations(value):
    with pytest.raises(ValueError):
        RunBreakRow(value, 1)


def test_saved_settings_are_immutable_and_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    original = SessionSettings(mode="routine", routine=(RunBreakRow(9, 2), RunBreakRow(3, 1)))
    original.save(path)
    assert SessionSettings.load(path) == original
    replace(original, mode="continuous").save(path)
    assert original.mode == "routine"


def test_stop_before_start_does_not_mine(tmp_path):
    session, control, backend, clock = make(tmp_path)
    control.request_stop()
    result = session.run()
    assert result["success"]
    assert backend.cycles == 0
