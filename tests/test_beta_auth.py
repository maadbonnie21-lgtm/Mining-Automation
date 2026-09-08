"""No-input authentication gates; synthetic data is never a live profile."""

from __future__ import annotations

import hashlib
import json
import subprocess
from types import SimpleNamespace

import pytest

from mining_automation import beta_auth
from mining_automation.beta_auth import LOGOUT_PROFILE, load_logout_profile


def profile(tmp_path, monkeypatch, **changes):
    raw = bytes(1005 * 1078 * 4)
    source = tmp_path / "diagnostics/fixture.bgra"
    source.parent.mkdir()
    source.write_bytes(raw)
    anchors = [
        dict(region=[10, 10, 8, 8], sha256=hashlib.sha256(bytes(8 * 8 * 4)).hexdigest()),
        dict(region=[100, 100, 9, 9], sha256=hashlib.sha256(bytes(9 * 9 * 4)).hexdigest()),
    ]
    value = dict(
        version=1,
        live_reviewed=True,
        review_issue=94,
        review_comment_id=123,
        steps=[
            dict(
                action="confirm_logout",
                point=[30, 30],
                click_region=[20, 20, 20, 20],
                reference_path="diagnostics/fixture.bgra",
                reference_sha256=hashlib.sha256(raw).hexdigest(),
                anchors=anchors,
            )
        ],
    )
    value.update(changes)
    path = tmp_path / LOGOUT_PROFILE
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout=path.read_bytes())
    )
    return value, path


def test_missing_logout_profile_never_constructs_capture(tmp_path, monkeypatch):
    monkeypatch.setattr(
        beta_auth,
        "AuthReader",
        lambda *args: pytest.fail("No capture or input permitted without logout source evidence"),
    )
    with pytest.raises(RuntimeError, match="fixture_missing"):
        beta_auth.logout(None, tmp_path)


def test_untracked_profile_is_not_input_authority(tmp_path, monkeypatch):
    value, path = profile(tmp_path, monkeypatch)
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=1, stdout=b"")
    )
    with pytest.raises(RuntimeError, match="frozen_committed"):
        load_logout_profile(tmp_path)


def test_dirty_profile_is_not_authority(tmp_path, monkeypatch):
    value, path = profile(tmp_path, monkeypatch)
    committed = path.read_bytes()
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout=committed)
    )
    path.write_bytes(committed + b" ")
    with pytest.raises(RuntimeError, match="frozen_committed"):
        load_logout_profile(tmp_path)


@pytest.mark.parametrize(
    "changes",
    [
        dict(live_reviewed=False),
        dict(review_issue=119),
        dict(review_comment_id=0),
        dict(version=2),
        dict(steps=[]),
    ],
)
def test_profile_requires_reviewed_action_and_provenance(tmp_path, monkeypatch, changes):
    profile(tmp_path, monkeypatch, **changes)
    with pytest.raises((ValueError, RuntimeError)):
        load_logout_profile(tmp_path)


@pytest.mark.parametrize(
    "fault", ["hash", "anchor", "duplicate_anchor", "point", "region", "reference"]
)
def test_profile_is_bound_to_exact_source_pixels_and_inward_control(tmp_path, monkeypatch, fault):
    value, path = profile(tmp_path, monkeypatch)
    step = value["steps"][0]
    if fault == "hash":
        step["reference_sha256"] = "f" * 64
    elif fault == "anchor":
        step["anchors"][0]["sha256"] = "f" * 64
    elif fault == "duplicate_anchor":
        step["anchors"][1] = step["anchors"][0]
    elif fault == "point":
        step["point"] = [20, 20]
    elif fault == "region":
        step["click_region"] = [20, 20, 2000, 2000]
    else:
        step["reference_path"] = "../other-checkout/fixture.bgra"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError):
        load_logout_profile(tmp_path)


def test_valid_synthetic_profile_load_is_not_a_live_acceptance_claim(tmp_path, monkeypatch):
    value, path = profile(tmp_path, monkeypatch)
    assert load_logout_profile(tmp_path) == value["steps"]
    assert not (tmp_path / "outputs").exists()


def test_unknown_auth_screen_never_clicks(tmp_path):
    reader = beta_auth.AuthReader.__new__(beta_auth.AuthReader)
    reader.read = lambda: object()
    reader.policy = SimpleNamespace(wait=lambda seconds: None)
    with pytest.raises(RuntimeError, match="unknown_or_challenge_screen"):
        reader.wait_for(lambda frame: False, timeout_s=0)


def test_login_stops_on_unknown_first_stage_without_click(tmp_path, monkeypatch):
    calls = []

    class Reader:
        def __init__(self, policy):
            pass

        def wait_for(self, matcher):
            raise RuntimeError("challenge")

        def click(self, *args):
            calls.append("click")

        def close(self):
            calls.append("close")

    monkeypatch.setattr(beta_auth, "AuthReader", Reader)
    with pytest.raises(RuntimeError, match="challenge"):
        beta_auth.login(None, tmp_path)
    assert calls == ["close"]
