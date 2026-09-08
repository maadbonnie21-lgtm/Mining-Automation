"""Fresh endpoint policy, with fake navigation/images; never a live acceptance receipt."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from mining_automation import beta_home
from mining_automation.navigation.visual_route import MinimapGeometry, Registration


def fixture(tmp_path, monkeypatch, distances=(0.5, 0.5, 0.5)):
    profile = tmp_path / "src/mining_automation/navigation/profiles/varrock_east/route.json"
    profile.parent.mkdir(parents=True)
    profile.write_text("{}")
    (profile.parent / "terrain.npz").write_bytes(b"fake")
    observations = list(distances)
    state = dict(
        empty=28,
        unknown=0,
        bank_open=False,
        tabs=1.0,
        healthy=True,
        clock=100.0,
        captures=0,
        clicks=[],
        changed=False,
    )
    window = {
        "identity": {"title": "RuneLite - test"},
        "dpi_environment": {"effective_mapping_scale_x": 1.0, "effective_mapping_scale_y": 1.0},
    }
    geometry = MinimapGeometry(800.0, 100.0, 80.0, 0.0)

    class Route:
        waypoints = [SimpleNamespace(image_key="canonical_mine")]

        def __init__(self, path):
            pass

        def observe(self, image, mine):
            distance = observations.pop(0) if len(observations) > 1 else observations[0]
            reg = Registration((95.5 + distance, 95.5), distance, 30, 0.9, 0.1, 1.0, 1.0, 0.0)
            return geometry, reg

        def verify_health(self, image, geometry):
            return state["healthy"]

    class Vision:
        def inventory(self, image):
            return dict(empty_count=state["empty"], unknown_count=state["unknown"])

        def match(self, *args):
            return SimpleNamespace(score=state["tabs"])

        def bank_controls(self, image):
            return object() if state["bank_open"] else None

    class Native:
        hwnd = 42
        output = tmp_path / "output"
        initial = window
        frame_id = 0

        def capture(self, label):
            self.frame_id += 1
            state["captures"] += 1
            return SimpleNamespace(
                frame_id=self.frame_id,
                captured_monotonic_s=state["clock"],
                image=np.zeros((862, 804, 3), dtype=np.uint8),
                evidence_path=f"fake-{self.frame_id}.png",
            )

        def click(self, frame, point, geometry):
            state["clicks"].append((frame.frame_id, point))

        def snapshot(self):
            return {} if state["changed"] else window

    def wait(seconds):
        state["clock"] += seconds

    policy = SimpleNamespace(check=lambda: None, wait=wait)
    monkeypatch.setattr(beta_home, "VisualRoute", Route)
    monkeypatch.setattr(beta_home, "BankVision", Vision)
    monkeypatch.setattr(beta_home.time, "monotonic", lambda: state["clock"])
    monkeypatch.setattr(beta_home.cv2, "resize", lambda image, *args, **kw: image)
    return Native(), policy, state


def test_canonical_home_requires_three_new_stationary_empty_closed_observations(
    tmp_path, monkeypatch
):
    native, policy, state = fixture(tmp_path, monkeypatch)
    result = beta_home.verify_home(native, policy, tmp_path, fresh_rocks=False)
    assert state["captures"] == 3 and not state["clicks"]
    assert result["fresh"] and result["stationary_verified"]
    assert result["canonical_distance"] <= 1
    assert result["final_frame"]["frame_id"] == 3
    assert len(result["profile_sha256"]) == 64


@pytest.mark.parametrize(
    "changes",
    [
        dict(empty=27),
        dict(unknown=1),
        dict(bank_open=True),
        dict(tabs=0.8),
        dict(tabs=float("nan")),
        dict(healthy=False),
    ],
)
def test_endpoint_unknowns_never_authorize_normalization(tmp_path, monkeypatch, changes):
    native, policy, state = fixture(tmp_path, monkeypatch, distances=(2.0, 2.0, 2.0))
    state.update(changes)
    with pytest.raises(RuntimeError):
        beta_home.verify_home(native, policy, tmp_path, fresh_rocks=False)
    assert not state["clicks"]


@pytest.mark.parametrize("distance", [4.1, 11.18, float("inf"), float("nan")])
def test_endpoint_adapter_does_not_replace_full_inventory_departure_repair(
    tmp_path, monkeypatch, distance
):
    native, policy, state = fixture(tmp_path, monkeypatch, distances=(distance,))
    with pytest.raises(RuntimeError, match="outside_reviewed"):
        beta_home.verify_home(native, policy, tmp_path, fresh_rocks=False)
    assert not state["clicks"]


def test_exact_finish_uses_one_fresh_click_only_inside_empty_return_envelope(tmp_path, monkeypatch):
    native, policy, state = fixture(tmp_path, monkeypatch, distances=(2, 2, 2, 0.3, 0.3, 0.3))
    result = beta_home.verify_home(native, policy, tmp_path, fresh_rocks=False)
    assert len(state["clicks"]) == 1 and state["clicks"][0][0] == 3
    assert result["normalization_clicks"] == 1 and result["canonical_distance"] <= 1


def test_failed_normalization_stops_instead_of_clicking_again(tmp_path, monkeypatch):
    native, policy, state = fixture(tmp_path, monkeypatch, distances=(2,))
    with pytest.raises(RuntimeError, match="no_blind_retry"):
        beta_home.verify_home(native, policy, tmp_path, fresh_rocks=False)
    assert len(state["clicks"]) == 1


def test_final_window_change_is_not_verified_home(tmp_path, monkeypatch):
    native, policy, state = fixture(tmp_path, monkeypatch)
    state["changed"] = True
    result = beta_home.verify_home(native, policy, tmp_path, fresh_rocks=False)
    assert result["window_unchanged"] is False
    from mining_automation.beta_session import require_home

    with pytest.raises(RuntimeError):
        require_home(result)
