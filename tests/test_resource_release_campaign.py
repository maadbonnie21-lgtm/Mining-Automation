from __future__ import annotations

import gzip
import hashlib
import inspect
import json
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from mining_automation.capture import (
    CaptureSource,
    CaptureUnavailableError,
    Frame,
    PixelFormat,
    RawFrame,
)
from mining_automation.capture.testing import FakeCaptureBackend, ManualClock
from mining_automation.perception import resource_release_campaign as campaign
from mining_automation.perception import resource_release_campaign_cli as campaign_cli
from mining_automation.perception import resource_release_decision as release_decision
from mining_automation.perception import resource_replay_promotion as replay_promotion
from mining_automation.perception.resource import ResourceVisualState

_HEAD_SHA = "a" * 40
_BRANCH = "codex/a-resource-release-campaign"
_CREATED = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
_NONCE = "0" * 32
_AVAILABLE_FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "perception"
    / "varrock-east-iron-v1"
    / "frames"
    / "available-01.raw.gz"
)


def _repository(
    *,
    head_sha: str = _HEAD_SHA,
    branch: str = _BRANCH,
    clean: bool = True,
) -> campaign.RepositoryProvenance:
    return campaign.RepositoryProvenance(
        head_sha=head_sha,
        branch=branch,
        clean=clean,
    )


def _session(tmp_path: Path) -> Path:
    return campaign.create_campaign(
        tmp_path / "campaigns",
        operator_id="operator-a",
        repository=_repository(),
        created_at_utc=_CREATED,
        nonce=_NONCE,
    )


def _environment() -> campaign.CaptureEnvironment:
    return campaign.CaptureEnvironment(
        backend_name="windows-runelite",
        title_match="RuneLite",
        window_title="private-title RuneLite",
        window_class="SunAwtFrame",
        window_hwnd=1234,
        window_client_width=1,
        window_client_height=1,
        reported_dpi=96,
    )


def _small_raw(value: int = 0) -> RawFrame:
    return RawFrame(
        payload=bytes((value & 0xFF, 0, 0, 255)),
        width=1,
        height=1,
        pixel_format=PixelFormat.BGRA8888,
    )


def _compact_raw(value: int = 0) -> RawFrame:
    pixel = bytes((value & 0xFF, 0, 0, 255))
    return RawFrame(
        payload=pixel * (124 * 104),
        width=124,
        height=104,
        pixel_format=PixelFormat.BGRA8888,
    )


def _compact_environment() -> campaign.CaptureEnvironment:
    return replace(
        _environment(),
        window_client_width=124,
        window_client_height=104,
    )


def _reviewed_available_frame() -> Frame:
    with gzip.open(_AVAILABLE_FIXTURE, "rb") as source:
        payload = source.read()
    return Frame.from_raw(
        RawFrame(
            payload=payload,
            width=1005,
            height=1078,
            pixel_format=PixelFormat.BGRA8888,
        ),
        frame_id=1,
        captured_monotonic_s=1.0,
    )


def _enable_tiny_verifier_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the frozen manifest identity while replaying tiny test frames."""

    identity = campaign._profile_identity()
    original = campaign.load_varrock_east_iron_profile()
    values = {item.name: getattr(original, item.name) for item in fields(original)}
    values["frame_width"] = 124
    values["frame_height"] = 104
    tiny_profile = SimpleNamespace(**values)
    monkeypatch.setattr(campaign, "load_varrock_east_iron_profile", lambda: tiny_profile)
    monkeypatch.setattr(
        replay_promotion,
        "load_varrock_east_iron_profile",
        lambda: tiny_profile,
    )
    monkeypatch.setattr(campaign, "_profile_identity", lambda: identity)
    monkeypatch.setattr(
        replay_promotion,
        "load_varrock_east_iron_profile",
        lambda: tiny_profile,
    )


def _canonical_json_bytes(value: Mapping[str, object]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _rewrite_hashed_json(
    path: Path,
    mutate: Callable[[dict[str, object]], None],
) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    mutate(cast(dict[str, object], payload))
    encoded = _canonical_json_bytes(cast(dict[str, object], payload))
    digest = hashlib.sha256(encoded).hexdigest()
    path.write_bytes(encoded)
    path.with_name(f"{path.name}.sha256").write_text(
        f"{digest}\n",
        encoding="ascii",
    )
    return digest


def _current_manifest_sha256(package: Path) -> str:
    digest = (package / "manifest.json.sha256").read_text(encoding="ascii").strip()
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    return digest


def _verify_review_package(package: Path) -> dict[str, object]:
    return campaign.verify_review_package(
        package,
        expected_manifest_sha256=_current_manifest_sha256(package),
    )


def _case_dir(session: Path, ordinal: int, case_id: str) -> Path:
    return session / "private" / "captures" / f"{ordinal:03d}-{case_id}"


def _capture_next_injected(
    session: Path,
    source: CaptureSource,
    *,
    captured_at_utc: datetime,
    environment_provider: Callable[[], campaign.CaptureEnvironment] = _environment,
) -> dict[str, object]:
    """Capture test evidence without forging the source-owned live provenance."""

    return campaign.capture_next_case(
        session,
        source,
        repository=_repository(),
        environment_provider=environment_provider,
        captured_at_utc=captured_at_utc,
    )


def _capture_one(
    monkeypatch: pytest.MonkeyPatch,
    session: Path,
    raw: RawFrame,
    *,
    captured_at: datetime = _CREATED + timedelta(seconds=1),
) -> tuple[dict[str, object], FakeCaptureBackend]:
    monkeypatch.setattr(campaign, "LIVE_RESOURCE_CAMPAIGN_AUTHORIZED", True)
    backend = FakeCaptureBackend([raw], name="windows-runelite")
    with CaptureSource(backend, clock=ManualClock(1.0)) as source:
        record = _capture_next_injected(
            session,
            source,
            captured_at_utc=captured_at,
        )
    return record, backend


def _truth_states_for(case: campaign.CampaignCase) -> tuple[ResourceVisualState, ...]:
    states = [ResourceVisualState.AVAILABLE] * len(
        campaign.VARROCK_EAST_IRON_RESOURCE_IDS
    )
    if case.review_meaning in {
        campaign.ReviewMeaning.UNSUPPORTED_LOCATION,
        campaign.ReviewMeaning.NEIGHBORING_COPPER,
        campaign.ReviewMeaning.NEIGHBORING_TIN,
        campaign.ReviewMeaning.TERRAIN_CLUTTER,
    }:
        states = [ResourceVisualState.UNCERTAIN] * len(states)
    elif case.review_meaning is campaign.ReviewMeaning.PROFILED_NODE_STATE:
        assert case.focal_resource_id is not None
        assert case.requested_focal_state is not None
        index = campaign.VARROCK_EAST_IRON_RESOURCE_IDS.index(case.focal_resource_id)
        states[index] = case.requested_focal_state
    elif case.review_meaning is campaign.ReviewMeaning.PROFILED_OBSTRUCTION:
        states[0] = ResourceVisualState.UNCERTAIN
    return tuple(states)


def _fake_production(frame: campaign.Frame) -> dict[str, object]:
    case = campaign.CAMPAIGN_PLAN[frame.frame_id - 1]
    states = _truth_states_for(case)
    profile = campaign.load_varrock_east_iron_profile()
    resources: list[dict[str, object]] = []
    definitive: list[str] = []
    actionable: list[str] = []
    regions: dict[str, object] = {}
    for candidate, state in zip(profile.candidates, states, strict=True):
        available: bool | None
        if state is ResourceVisualState.AVAILABLE:
            available = True
            definitive.append(candidate.resource_id)
            actionable.append(candidate.resource_id)
            interaction: object = list(candidate.region)
        elif state is ResourceVisualState.DEPLETED:
            available = False
            definitive.append(candidate.resource_id)
            interaction = None
        else:
            available = None
            interaction = None
        regions[candidate.resource_id] = interaction
        resources.append(
            {
                "resource_id": candidate.resource_id,
                "resource_type": "iron",
                "available": available,
                "confidence": 1.0,
                "interaction_region": interaction,
            }
        )
    negative = case.review_meaning in {
        campaign.ReviewMeaning.UNSUPPORTED_LOCATION,
        campaign.ReviewMeaning.NEIGHBORING_COPPER,
        campaign.ReviewMeaning.NEIGHBORING_TIN,
        campaign.ReviewMeaning.TERRAIN_CLUTTER,
    }
    return {
        "status": "completed",
        "detector_id": campaign.VARROCK_EAST_IRON_DETECTOR_ID,
        "detector_version": campaign.VARROCK_EAST_IRON_DETECTOR_VERSION,
        "observations": [],
        "trust": {
            "accepted": True,
            "reason": "trusted_complete_production_ensemble",
            "frame": {
                "frame_id": frame.frame_id,
                "captured_monotonic_s": frame.captured_monotonic_s,
                "width": frame.width,
                "height": frame.height,
                "pixel_format": frame.pixel_format.value,
            },
            "resources": resources,
            "definitive_target_ids": definitive,
            "production_actionable_target_ids": actionable,
            "production_interaction_regions": regions,
        },
        "scene": {
            "validated": not negative,
            "reason": "synthetic-test-scene-summary",
            "matched_count": 6 if not negative else 0,
            "required_quorum": 5,
            "matched_zones": (
                ["north_east", "north_west", "south_west"] if not negative else []
            ),
            "required_zones": 3,
            "landmarks": [
                {
                    "landmark_id": landmark.landmark_id,
                    "zone": landmark.macro_zone.value,
                    "distance": (
                        0.0 if not negative else landmark.maximum_distance + 0.01
                    ),
                    "threshold": landmark.maximum_distance,
                    "matched": not negative,
                }
                for landmark in profile.scene_landmarks
            ],
            "authority": "read-only-summary-never-overrides-production",
        },
        "passive_campaign_authorized_target_ids": [],
        "stop_required": any(state is ResourceVisualState.UNCERTAIN for state in states),
        "input_authority": False,
    }


def _capture_and_seal_small_campaign(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    production: Callable[[Frame], dict[str, object]] = _fake_production,
    source_owned: bool = False,
    raw_factory: Callable[[int], RawFrame] = _small_raw,
    environment_provider: Callable[[], campaign.CaptureEnvironment] = _environment,
) -> Path:
    session = _session(tmp_path)
    monkeypatch.setattr(campaign, "LIVE_RESOURCE_CAMPAIGN_AUTHORIZED", True)
    monkeypatch.setattr(campaign, "_production_json", production)
    frames = [raw_factory(index) for index in range(1, len(campaign.CAMPAIGN_PLAN) + 1)]
    backend = FakeCaptureBackend(frames, name="windows-runelite")
    clock = ManualClock(1.0)
    with CaptureSource(backend, clock=clock) as source:
        for index, _case in enumerate(campaign.CAMPAIGN_PLAN, start=1):
            captured_at = _CREATED + timedelta(seconds=index)
            if source_owned:
                campaign._capture_next_with_source(
                    session,
                    source,
                    repository=_repository(),
                    environment_provider=environment_provider,
                    provenance_capability=campaign._SOURCE_OWNED_CAPTURE_CAPABILITY,
                    expected_case_id=_case.case_id,
                    captured_at_utc=captured_at,
                )
            else:
                _capture_next_injected(
                    session,
                    source,
                    captured_at_utc=captured_at,
                    environment_provider=environment_provider,
                )
            clock.advance(1.0)
    assert backend.grab_calls == len(campaign.CAMPAIGN_PLAN)
    campaign.seal_campaign(
        session,
        repository=_repository(),
        sealed_at_utc=_CREATED + timedelta(minutes=1),
    )
    return session


def _decision_for(
    case: campaign.CampaignCase,
    *,
    reviewer_id: str = "reviewer-b",
    meaning: campaign.ReviewMeaning | None = None,
    states: tuple[ResourceVisualState, ...] | None = None,
    review_artifact_sha256: str = "b" * 64,
) -> campaign.ReviewDecision:
    kwargs: dict[str, object] = {
        "case_id": case.case_id,
        "reviewer_id": reviewer_id,
        "reviewed_at_utc": _CREATED + timedelta(minutes=2),
        "meaning": case.review_meaning if meaning is None else meaning,
        "resource_truth": tuple(
            zip(
                campaign.VARROCK_EAST_IRON_RESOURCE_IDS,
                _truth_states_for(case) if states is None else states,
                strict=True,
            )
        ),
        "review_artifact_sha256": review_artifact_sha256,
        "privacy_review_confirmed": True,
        "focal_resource_id": case.focal_resource_id,
        "node_phase": case.requested_node_phase,
        "obstruction_target_kind": (
            "resource"
            if case.review_meaning is campaign.ReviewMeaning.PROFILED_OBSTRUCTION
            else None
        ),
        "obstruction_target_id": (
            campaign.VARROCK_EAST_IRON_RESOURCE_IDS[0]
            if case.review_meaning is campaign.ReviewMeaning.PROFILED_OBSTRUCTION
            else None
        ),
    }
    field_names = {item.name for item in fields(campaign.ReviewDecision)}
    if "subject_region" in field_names:
        kwargs["subject_region"] = (
            (120, 100, 4, 4)
            if case.review_meaning
            in {
                campaign.ReviewMeaning.NEIGHBORING_COPPER,
                campaign.ReviewMeaning.NEIGHBORING_TIN,
                campaign.ReviewMeaning.TERRAIN_CLUTTER,
            }
            else None
        )
    return campaign.ReviewDecision(**kwargs)  # type: ignore[arg-type]


def _prepare_case_review(
    session: Path,
    case: campaign.CampaignCase,
    *,
    prepared_at_utc: datetime = _CREATED + timedelta(minutes=1, seconds=10),
) -> str:
    prepared = campaign.prepare_case_review(
        session,
        case.case_id,
        repository=_repository(),
        prepared_at_utc=prepared_at_utc,
    )
    digest = prepared["review_artifact_sha256"]
    assert isinstance(digest, str)
    return digest


def _review_all(
    monkeypatch: pytest.MonkeyPatch,
    session: Path,
    *,
    override: Mapping[str, campaign.ReviewDecision] | None = None,
) -> None:
    monkeypatch.setattr(campaign, "_sanitize_bgra_for_review", lambda frame: frame.payload)
    monkeypatch.setattr(campaign, "_has_reviewable_geometry", lambda frame: True)
    for case in campaign.CAMPAIGN_PLAN:
        artifact_sha = _prepare_case_review(session, case)
        decision = (override or {}).get(case.case_id, _decision_for(case))
        campaign.record_case_review(
            session,
            replace(decision, review_artifact_sha256=artifact_sha),
            repository=_repository(),
            recorded_at_utc=_CREATED + timedelta(minutes=2, seconds=1),
        )


def test_campaign_plan_is_the_exact_fixed_pr39_order() -> None:
    assert tuple(case.ordinal for case in campaign.CAMPAIGN_PLAN) == tuple(range(1, 16))
    assert tuple(case.case_id for case in campaign.CAMPAIGN_PLAN) == (
        "supported-startup-positive",
        "northwest-available",
        "northwest-depleted",
        "northwest-respawn",
        "center-available",
        "center-depleted",
        "center-respawn",
        "northeast-available",
        "northeast-depleted",
        "northeast-respawn",
        "profiled-obstruction",
        "unsupported-location",
        "neighboring-copper",
        "neighboring-tin",
        "terrain-clutter",
    )


def test_each_node_cycle_freezes_initial_depleted_respawn_and_focal_truth() -> None:
    expected_phases = (
        campaign.NodeCyclePhase.INITIAL_AVAILABLE,
        campaign.NodeCyclePhase.DEPLETED,
        campaign.NodeCyclePhase.RESPAWN,
    )
    for cycle_start in (1, 4, 7):
        cases = campaign.CAMPAIGN_PLAN[cycle_start : cycle_start + 3]
        focal_ids = {case.focal_resource_id for case in cases}
        assert len(focal_ids) == 1
        assert tuple(case.requested_node_phase for case in cases) == expected_phases
        assert tuple(case.requested_focal_state for case in cases) == (
            ResourceVisualState.AVAILABLE,
            ResourceVisualState.DEPLETED,
            ResourceVisualState.AVAILABLE,
        )
        for case in cases:
            decision = _decision_for(case)
            assert decision.focal_resource_id == case.focal_resource_id
            assert decision.node_phase == case.requested_node_phase
    assert len({case.case_id for case in campaign.CAMPAIGN_PLAN}) == 15
    assert tuple(inspect.signature(campaign.capture_next_case).parameters) == (
        "session_dir",
        "source",
        "repository",
        "environment_provider",
        "captured_at_utc",
    )


def test_capture_configuration_source_owns_pending_dpi_requirement(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    payload = json.loads((session / "session.json").read_text(encoding="utf-8"))

    assert campaign.RESOURCE_RELEASE_CAMPAIGN_VERSION == "1.1.0"
    assert campaign.RESOURCE_RELEASE_CONFIGURATION_ID == (
        "resource-release-campaign:varrock-east-iron-v1@1.1.0"
    )
    assert payload["campaign_version"] == "1.1.0"
    assert payload["configuration_id"] == campaign.RESOURCE_RELEASE_CONFIGURATION_ID
    configuration = payload["capture_configuration"]
    assert configuration["required_reported_dpi"] == 96
    assert configuration["reported_dpi_requirement_status"] == (
        "required-candidate-pending-fresh-review"
    )
    assert configuration["live_source_authorized"] is False


def test_campaign_directory_is_exclusively_owned_and_collision_preserves_winner(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    session_before = (session / "session.json").read_bytes()

    with pytest.raises(FileExistsError):
        campaign.create_campaign(
            session.parent,
            operator_id="operator-a",
            repository=_repository(),
            created_at_utc=_CREATED,
            nonce=_NONCE,
        )

    assert (session / "session.json").read_bytes() == session_before
    status = campaign.load_campaign_status(session, repository=_repository())
    assert status.captured_case_ids == ()
    assert status.next_case == campaign.CAMPAIGN_PLAN[0]


def test_live_source_gate_blocks_before_capture_or_case_directory(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    backend = FakeCaptureBackend([_small_raw()], name="windows-runelite")
    source = CaptureSource(backend, clock=ManualClock())

    with pytest.raises(campaign.CampaignError, match="NOT YET AUTHORIZED"):
        campaign.capture_next_case(
            session,
            source,
            repository=_repository(),
            environment_provider=_environment,
        )

    assert backend.open_calls == 0
    assert backend.grab_calls == 0
    assert not (session / "private").exists()


def test_one_unsupported_capture_never_hunts_for_the_queued_next_frame(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    monkeypatch.setattr(campaign, "LIVE_RESOURCE_CAMPAIGN_AUTHORIZED", True)
    first = _small_raw(0)
    second = _small_raw(255)
    backend = FakeCaptureBackend([first, second], name="windows-runelite")
    with CaptureSource(backend, clock=ManualClock()) as source:
        record = _capture_next_injected(
            session,
            source,
            captured_at_utc=_CREATED + timedelta(seconds=1),
        )

    production = cast(dict[str, object], record["production"])
    trust = cast(dict[str, object], production["trust"])
    assert backend.grab_calls == 1
    assert record["capture_count"] == 1
    assert record["automatic_retry_count"] == 0
    assert trust["accepted"] is False
    assert trust["reason"] == "frame_geometry_mismatch"
    assert trust["definitive_target_ids"] == []
    assert trust["production_actionable_target_ids"] == []
    assert production["stop_required"] is True


def test_no_frame_failure_is_hashed_and_only_a_later_explicit_call_may_succeed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    monkeypatch.setattr(campaign, "LIVE_RESOURCE_CAMPAIGN_AUTHORIZED", True)
    backend = FakeCaptureBackend(
        [CaptureUnavailableError("surface lost"), _small_raw(1)],
        name="windows-runelite",
    )
    with CaptureSource(backend, retry_attempts=0) as source:
        with pytest.raises(CaptureUnavailableError, match="surface lost"):
            _capture_next_injected(
                session,
                source,
                captured_at_utc=_CREATED + timedelta(seconds=1),
            )
        status = campaign.load_campaign_status(session, repository=_repository())
        assert status.captured_case_ids == ()
        assert status.next_case_no_frame_failures == 1
        failure = (
            _case_dir(session, 1, "supported-startup-positive")
            / "capture-failure-001.json"
        )
        failure_sha = hashlib.sha256(failure.read_bytes()).hexdigest()
        assert failure.with_name(f"{failure.name}.sha256").read_text(
            encoding="ascii"
        ) == f"{failure_sha}\n"

        record = _capture_next_injected(
            session,
            source,
            captured_at_utc=_CREATED + timedelta(seconds=2),
        )

    assert backend.grab_calls == 2
    assert record["capture_attempt_count"] == 2
    assert record["prior_no_frame_failure_count"] == 1
    assert record["prior_no_frame_failure_report_sha256s"] == [failure_sha]
    assert record["automatic_retry_count"] == 0


def test_concurrent_explicit_capture_invocations_reserve_before_second_grab(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    monkeypatch.setattr(campaign, "LIVE_RESOURCE_CAMPAIGN_AUTHORIZED", True)
    first_backend = FakeCaptureBackend([_small_raw(1)], name="injected-first")
    second_backend = FakeCaptureBackend([_small_raw(2)], name="injected-second")
    entered_grab = threading.Event()
    release_grab = threading.Event()
    original_grab = first_backend.grab

    def blocking_grab() -> RawFrame:
        entered_grab.set()
        assert release_grab.wait(timeout=10)
        return original_grab()

    monkeypatch.setattr(first_backend, "grab", blocking_grab)
    first_errors: list[BaseException] = []
    with (
        CaptureSource(first_backend) as first_source,
        CaptureSource(second_backend) as second_source,
    ):

        def first_invocation() -> None:
            try:
                _capture_next_injected(
                    session,
                    first_source,
                    captured_at_utc=_CREATED + timedelta(seconds=1),
                    environment_provider=lambda: replace(
                        _environment(), backend_name="injected-first"
                    ),
                )
            except BaseException as exc:  # pragma: no cover - asserted below
                first_errors.append(exc)

        worker = threading.Thread(target=first_invocation)
        worker.start()
        assert entered_grab.wait(timeout=10)
        with pytest.raises(
            campaign.CampaignIntegrityError,
            match="already reserved|terminal capture failure",
        ):
            _capture_next_injected(
                session,
                second_source,
                captured_at_utc=_CREATED + timedelta(seconds=2),
            )
        release_grab.set()
        worker.join(timeout=10)

    assert not worker.is_alive()
    assert first_errors == []
    assert first_backend.grab_calls == 1
    assert second_backend.grab_calls == 0


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: cast(list[object], payload["cases"]).reverse(),
        lambda payload: cast(list[object], payload["cases"]).__setitem__(
            1, cast(list[object], payload["cases"])[0]
        ),
    ],
    ids=("reordered", "duplicate"),
)
def test_rebound_session_plan_tampering_is_rejected_before_capture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutate: Callable[[dict[str, object]], None],
) -> None:
    session = _session(tmp_path)
    _rewrite_hashed_json(session / "session.json", mutate)
    monkeypatch.setattr(campaign, "LIVE_RESOURCE_CAMPAIGN_AUTHORIZED", True)
    backend = FakeCaptureBackend([_small_raw()], name="windows-runelite")
    with CaptureSource(backend) as source:
        with pytest.raises(campaign.CampaignIntegrityError, match="plan/order"):
            campaign.capture_next_case(
                session,
                source,
                repository=_repository(),
                environment_provider=_environment,
            )
    assert backend.grab_calls == 0


def test_duplicate_json_key_is_rejected_even_with_a_matching_file_sidecar(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    path = session / "session.json"
    payload = path.read_bytes().replace(
        b'{\n  "campaign_id"',
        b'{\n  "schema_version": 1,\n  "campaign_id"',
        1,
    )
    path.write_bytes(payload)
    path.with_name("session.json.sha256").write_text(
        f"{hashlib.sha256(payload).hexdigest()}\n",
        encoding="ascii",
    )

    with pytest.raises(campaign.CampaignIntegrityError, match="duplicate JSON key"):
        campaign.load_campaign_status(session, repository=_repository())


def test_foreign_repository_binding_is_rejected_before_capture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    monkeypatch.setattr(campaign, "LIVE_RESOURCE_CAMPAIGN_AUTHORIZED", True)
    backend = FakeCaptureBackend([_small_raw()], name="windows-runelite")
    with CaptureSource(backend) as source:
        with pytest.raises(campaign.CampaignIntegrityError, match="Git head/branch"):
            campaign.capture_next_case(
                session,
                source,
                repository=_repository(head_sha="b" * 40),
                environment_provider=_environment,
            )
    assert backend.grab_calls == 0


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("detector_version", "foreign-version"),
        ("profile_id", "foreign-profile"),
        ("profile_schema_version", 2),
        ("location_id", "foreign-location"),
    ),
)
def test_wrong_detector_profile_schema_or_location_identity_fails_before_capture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    field: str,
    replacement: object,
) -> None:
    session = _session(tmp_path)

    def mutate(payload: dict[str, object]) -> None:
        profile = cast(dict[str, object], payload["profile"])
        profile[field] = replacement

    _rewrite_hashed_json(session / "session.json", mutate)
    monkeypatch.setattr(campaign, "LIVE_RESOURCE_CAMPAIGN_AUTHORIZED", True)
    backend = FakeCaptureBackend([_small_raw()], name="windows-runelite")
    with CaptureSource(backend) as source:
        with pytest.raises(campaign.CampaignIntegrityError, match="identity changed"):
            campaign.capture_next_case(
                session,
                source,
                repository=_repository(),
                environment_provider=_environment,
            )
    assert backend.grab_calls == 0


def test_foreign_duplicate_or_out_of_order_capture_directory_blocks_resume(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    foreign = session / "private" / "captures" / "999-foreign"
    foreign.mkdir(parents=True)

    with pytest.raises(campaign.CampaignIntegrityError, match="foreign or duplicate"):
        campaign.load_campaign_status(session, repository=_repository())


def test_replaced_raw_payload_is_detected_even_when_its_sidecar_is_rebound(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    _capture_one(monkeypatch, session, _small_raw(1))
    raw = _case_dir(session, 1, "supported-startup-positive") / "frame.raw"
    replaced = _small_raw(2).payload
    raw.write_bytes(replaced)
    raw.with_name("frame.raw.sha256").write_text(
        f"{hashlib.sha256(replaced).hexdigest()}\n",
        encoding="ascii",
    )

    with pytest.raises(campaign.CampaignIntegrityError, match="stored SHA-256 mismatch"):
        campaign.load_campaign_status(session, repository=_repository())


def test_stale_capture_chronology_is_rejected_after_full_hash_rebinding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    _capture_one(monkeypatch, session, _small_raw(1))
    case_dir = _case_dir(session, 1, "supported-startup-positive")
    stale = _CREATED - timedelta(seconds=1)
    stale_text = stale.isoformat().replace("+00:00", "Z")
    owned_path = case_dir / "owned-frame.json"
    owned_sha = _rewrite_hashed_json(
        owned_path,
        lambda payload: payload.__setitem__("captured_at_utc", stale_text),
    )

    def rewrite_report(payload: dict[str, object]) -> None:
        payload["captured_at_utc"] = stale_text
        payload["owned_frame_sha256"] = owned_sha

    _rewrite_hashed_json(case_dir / "capture-report.json", rewrite_report)

    with pytest.raises(campaign.CampaignIntegrityError, match="predates"):
        campaign.load_campaign_status(session, repository=_repository())


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (
            lambda payload: payload.__setitem__("capture_count", True),
            "integer scalar types changed",
        ),
        (
            lambda payload: cast(dict[str, object], payload["frame"]).__setitem__(
                "captured_monotonic_s", float("nan")
            ),
            "non-finite JSON number",
        ),
    ),
    ids=("bool-for-int", "nan"),
)
def test_rebound_bool_for_int_and_nan_artifacts_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutate: Callable[[dict[str, object]], None],
    message: str,
) -> None:
    session = _session(tmp_path)
    _capture_one(monkeypatch, session, _small_raw(1))
    report = _case_dir(session, 1, "supported-startup-positive") / "capture-report.json"
    _rewrite_hashed_json(report, mutate)

    with pytest.raises(campaign.CampaignIntegrityError, match=message):
        campaign.load_campaign_status(session, repository=_repository())


def test_review_requires_sealed_complete_campaign_and_independent_reviewer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    decision = _decision_for(campaign.CAMPAIGN_PLAN[0])
    with pytest.raises(campaign.CampaignError, match="full campaign is sealed"):
        campaign.record_case_review(
            session,
            decision,
            repository=_repository(),
        )

    complete = _capture_and_seal_small_campaign(monkeypatch, tmp_path / "complete")
    monkeypatch.setattr(campaign, "_sanitize_bgra_for_review", lambda frame: frame.payload)
    monkeypatch.setattr(campaign, "_has_reviewable_geometry", lambda frame: True)
    first = campaign.CAMPAIGN_PLAN[0]
    artifact_sha = _prepare_case_review(complete, first)
    with pytest.raises(campaign.CampaignError, match="reviewer must be independent"):
        campaign.record_case_review(
            complete,
            _decision_for(
                first,
                reviewer_id="operator-a",
                review_artifact_sha256=artifact_sha,
            ),
            repository=_repository(),
        )

    with pytest.raises(campaign.CampaignError, match="cannot predate"):
        campaign.record_case_review(
            complete,
            replace(
                _decision_for(first, review_artifact_sha256=artifact_sha),
                reviewed_at_utc=_CREATED + timedelta(seconds=30),
            ),
            repository=_repository(),
        )


def test_negative_subject_truth_is_explicit_and_forbidden_on_other_cases(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = _capture_and_seal_small_campaign(monkeypatch, tmp_path)
    monkeypatch.setattr(campaign, "_sanitize_bgra_for_review", lambda frame: frame.payload)
    monkeypatch.setattr(campaign, "_has_reviewable_geometry", lambda frame: True)
    copper = next(
        case
        for case in campaign.CAMPAIGN_PLAN
        if case.review_meaning is campaign.ReviewMeaning.NEIGHBORING_COPPER
    )
    copper_sha = _prepare_case_review(session, copper)
    missing_subject = replace(
        _decision_for(copper, review_artifact_sha256=copper_sha),
        subject_region=None,
    )
    with pytest.raises(campaign.CampaignError, match="explicit reviewed subject region"):
        campaign.record_case_review(
            session,
            missing_subject,
            repository=_repository(),
        )

    startup = campaign.CAMPAIGN_PLAN[0]
    startup_sha = _prepare_case_review(session, startup)
    foreign_subject = replace(
        _decision_for(startup, review_artifact_sha256=startup_sha),
        subject_region=(0, 0, 4, 4),
    )
    with pytest.raises(campaign.CampaignError, match="only copper, tin, and terrain"):
        campaign.record_case_review(
            session,
            foreign_subject,
            repository=_repository(),
        )


def test_review_must_bind_prepared_artifact_and_postdate_preparation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = _capture_and_seal_small_campaign(monkeypatch, tmp_path)
    case = campaign.CAMPAIGN_PLAN[0]
    monkeypatch.setattr(campaign, "_has_reviewable_geometry", lambda frame: True)
    monkeypatch.setattr(campaign, "_sanitize_bgra_for_review", lambda frame: frame.payload)
    artifact_sha = _prepare_case_review(
        session,
        case,
        prepared_at_utc=_CREATED + timedelta(minutes=1, seconds=30),
    )

    with pytest.raises(campaign.CampaignError, match="inspected artifact manifest"):
        campaign.record_case_review(
            session,
            _decision_for(case, review_artifact_sha256="d" * 64),
            repository=_repository(),
        )
    with pytest.raises(campaign.CampaignError, match="predate inspected artifact"):
        campaign.record_case_review(
            session,
            replace(
                _decision_for(case, review_artifact_sha256=artifact_sha),
                reviewed_at_utc=_CREATED + timedelta(minutes=1, seconds=20),
            ),
            repository=_repository(),
        )


def test_unreviewable_withheld_disposition_is_explicit_all_unknown_and_one_way() -> None:
    case = campaign.CAMPAIGN_PLAN[0]
    unreviewable = _decision_for(
        case,
        meaning=campaign.ReviewMeaning.UNREVIEWABLE_PIXELS_WITHHELD,
        states=(ResourceVisualState.UNCERTAIN,) * 4,
    )
    campaign._validate_review_decision(
        unreviewable,
        case=case,
        operator_id="operator-a",
        sealed_at=_CREATED + timedelta(minutes=1),
        pixels_withheld=True,
    )
    with pytest.raises(campaign.CampaignError, match="reviewable pixels"):
        campaign._validate_review_decision(
            unreviewable,
            case=case,
            operator_id="operator-a",
            sealed_at=_CREATED + timedelta(minutes=1),
            pixels_withheld=False,
        )
    with pytest.raises(campaign.CampaignError, match="explicit unreviewable"):
        campaign._validate_review_decision(
            _decision_for(case),
            case=case,
            operator_id="operator-a",
            sealed_at=_CREATED + timedelta(minutes=1),
            pixels_withheld=True,
        )


def test_rebound_preview_is_recomputed_and_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = _capture_and_seal_small_campaign(monkeypatch, tmp_path)
    monkeypatch.setattr(campaign, "_has_reviewable_geometry", lambda frame: True)
    monkeypatch.setattr(campaign, "_sanitize_bgra_for_review", lambda frame: frame.payload)
    case = campaign.CAMPAIGN_PLAN[0]
    _prepare_case_review(session, case)
    preview = session / "review" / f"001-{case.case_id}" / "sanitized-preview.bmp"
    replacement = preview.read_bytes() + b"recomputed-attacker-preview"
    preview.write_bytes(replacement)
    preview.with_name(f"{preview.name}.sha256").write_text(
        f"{hashlib.sha256(replacement).hexdigest()}\n",
        encoding="ascii",
    )
    replacement_sha = hashlib.sha256(replacement).hexdigest()
    preparation = preview.parent / "review-preparation.json"

    def rebind_preview(payload: dict[str, object]) -> None:
        privacy = cast(dict[str, object], payload["privacy_artifacts"])
        preview_meta = cast(dict[str, object], privacy["sanitized_preview"])
        preview_meta["sha256"] = replacement_sha

    _rewrite_hashed_json(preparation, rebind_preview)

    with pytest.raises(campaign.CampaignIntegrityError, match="preview was replaced"):
        campaign.load_campaign_status(session, repository=_repository())


def test_negative_subject_rejects_fixed_ui_and_candidate_regions() -> None:
    case = next(
        item
        for item in campaign.CAMPAIGN_PLAN
        if item.review_meaning is campaign.ReviewMeaning.NEIGHBORING_COPPER
    )
    profile = campaign.load_varrock_east_iron_profile()
    regions = (
        campaign.VARROCK_EAST_IRON_FIXED_UI_REGIONS[0],
        profile.candidates[0].region,
    )
    messages = ("fixed UI", "profiled iron candidate")
    for region, message in zip(regions, messages, strict=True):
        with pytest.raises(campaign.CampaignError, match=message):
            campaign._validate_review_decision(
                replace(_decision_for(case), subject_region=region),
                case=case,
                operator_id="operator-a",
                sealed_at=_CREATED + timedelta(minutes=1),
                pixels_withheld=False,
            )


def test_review_directories_are_strictly_owned_and_foreign_entries_fail(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = _capture_and_seal_small_campaign(monkeypatch, tmp_path)
    (session / "review" / "999-foreign-case").mkdir(parents=True)

    with pytest.raises(campaign.CampaignIntegrityError, match="foreign or duplicate"):
        campaign.load_campaign_status(session, repository=_repository())


def test_reviewer_meaning_is_never_inferred_from_operator_stage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = _capture_and_seal_small_campaign(
        monkeypatch,
        tmp_path,
        source_owned=True,
    )
    first = campaign.CAMPAIGN_PLAN[0]
    wrong = _decision_for(
        first,
        meaning=campaign.ReviewMeaning.UNSUPPORTED_LOCATION,
        states=(ResourceVisualState.UNCERTAIN,) * 4,
    )
    _review_all(monkeypatch, session, override={first.case_id: wrong})

    report = campaign.evaluate_release(
        session,
        repository=_repository(),
        evaluated_at_utc=_CREATED + timedelta(minutes=3),
    )
    first_result = cast(list[dict[str, object]], report["case_results"])[0]
    assert first_result["passed"] is False
    assert "reviewer_did_not_confirm_requested_case_meaning" in first_result["reasons"]
    assert "fresh-supported-startup" in report["still_open_blockers"]
    assert report["release_gate_categories"]["c1_fresh_empirical_evidence"][
        "status"
    ] == "OPEN"
    assert report["release_gate_categories"]["c2_evidence_contingent_source_review"][
        "status"
    ] == "OPEN"
    failure_promotion = report["release_gate_categories"][
        "c2_evidence_contingent_source_review"
    ]["gates"][0]
    assert failure_promotion["status"] == "OPEN"
    assert failure_promotion["case_ids"] == [first.case_id]
    assert report["release_eligible"] is False


def test_rebound_review_artifact_is_rejected_against_owned_frame(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = _capture_and_seal_small_campaign(monkeypatch, tmp_path)
    monkeypatch.setattr(campaign, "_sanitize_bgra_for_review", lambda frame: frame.payload)
    monkeypatch.setattr(campaign, "_has_reviewable_geometry", lambda frame: True)
    first = campaign.CAMPAIGN_PLAN[0]
    artifact_sha = _prepare_case_review(session, first)
    campaign.record_case_review(
        session,
        _decision_for(first, review_artifact_sha256=artifact_sha),
        repository=_repository(),
        recorded_at_utc=_CREATED + timedelta(minutes=2, seconds=1),
    )
    review_dir = session / "review" / f"001-{first.case_id}"
    artifact = review_dir / "sanitized-frame.raw.gz"
    replacement = artifact.read_bytes() + b"rebound"
    artifact.write_bytes(replacement)
    artifact.with_name(f"{artifact.name}.sha256").write_text(
        f"{hashlib.sha256(replacement).hexdigest()}\n",
        encoding="ascii",
    )

    with pytest.raises(campaign.CampaignIntegrityError, match="stored SHA-256 mismatch"):
        campaign.evaluate_release(
            session,
            repository=_repository(),
            evaluated_at_utc=_CREATED + timedelta(minutes=3),
        )


def test_operator_label_promotion_in_reviewer_truth_is_detected_after_rebinding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = _capture_and_seal_small_campaign(monkeypatch, tmp_path)
    monkeypatch.setattr(campaign, "_sanitize_bgra_for_review", lambda frame: frame.payload)
    monkeypatch.setattr(campaign, "_has_reviewable_geometry", lambda frame: True)
    first = campaign.CAMPAIGN_PLAN[0]
    artifact_sha = _prepare_case_review(session, first)
    campaign.record_case_review(
        session,
        _decision_for(first, review_artifact_sha256=artifact_sha),
        repository=_repository(),
        recorded_at_utc=_CREATED + timedelta(minutes=2, seconds=1),
    )
    pre_tamper = campaign.evaluate_release(
        session,
        repository=_repository(),
        evaluated_at_utc=_CREATED + timedelta(minutes=3),
    )
    assert pre_tamper["release_gate_categories"]["c1_fresh_empirical_evidence"][
        "status"
    ] == "OPEN"
    assert pre_tamper["release_gate_categories"][
        "c2_evidence_contingent_source_review"
    ]["status"] == "OPEN"
    assert pre_tamper["release_eligible"] is False
    truth = session / "review" / f"001-{first.case_id}" / "reviewer-truth.json"
    _rewrite_hashed_json(
        truth,
        lambda payload: payload.__setitem__("operator_stage_is_reviewer_truth", True),
    )

    with pytest.raises(campaign.CampaignIntegrityError, match="identity changed"):
        campaign.evaluate_release(
            session,
            repository=_repository(),
            evaluated_at_utc=_CREATED + timedelta(minutes=3),
        )


def test_release_ledger_keeps_missing_reviews_and_final_lead_gate_open(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = _capture_and_seal_small_campaign(monkeypatch, tmp_path)

    report = campaign.evaluate_release(
        session,
        repository=_repository(),
        evaluated_at_utc=_CREATED + timedelta(minutes=3),
    )

    assert report["closed_blockers"] == []
    assert report["still_open_blockers"] == [
        "fresh-supported-startup",
        "northwest-cycle",
        "center-cycle",
        "northeast-cycle",
        "profiled-obstruction",
        "unsupported-location",
        "neighboring-copper",
        "neighboring-tin",
        "terrain-clutter",
        "final-constrained-v1-operating-envelope-review",
    ]
    assert report["release_eligible"] is False
    assert report["activation_allowed"] is False
    assert report["promotion_allowed"] is False
    assert report["input_authority"] is False
    assert all(
        item["reasons"] == ["independent_reviewer_truth_missing"]
        for item in cast(list[dict[str, object]], report["case_results"])
    )


def test_complete_correct_injected_campaign_can_never_close_real_blockers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = _capture_and_seal_small_campaign(monkeypatch, tmp_path)
    _review_all(monkeypatch, session)

    report = campaign.evaluate_release(
        session,
        repository=_repository(),
        evaluated_at_utc=_CREATED + timedelta(minutes=3),
    )

    assert report["closed_blockers"] == []
    assert report["still_open_blockers"] == [
        "fresh-supported-startup",
        "northwest-cycle",
        "center-cycle",
        "northeast-cycle",
        "profiled-obstruction",
        "unsupported-location",
        "neighboring-copper",
        "neighboring-tin",
        "terrain-clutter",
        "final-constrained-v1-operating-envelope-review",
    ]
    assert report["release_eligible"] is False
    assert all(
        item["passed"] is False
        and "non_source_owned_capture_evidence" in item["reasons"]
        for item in cast(list[dict[str, object]], report["case_results"])
    )


def test_exact_source_owned_dpi_96_campaign_closes_c1_but_keeps_c2_open(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = _capture_and_seal_small_campaign(
        monkeypatch,
        tmp_path,
        source_owned=True,
    )
    _review_all(monkeypatch, session)

    report = campaign.evaluate_release(
        session,
        repository=_repository(),
        evaluated_at_utc=_CREATED + timedelta(minutes=3),
    )
    assert report["closed_blockers"] == [
        "fresh-supported-startup",
        "northwest-cycle",
        "center-cycle",
        "northeast-cycle",
        "profiled-obstruction",
        "unsupported-location",
        "neighboring-copper",
        "neighboring-tin",
        "terrain-clutter",
    ]
    assert report["still_open_blockers"] == [
        "final-constrained-v1-operating-envelope-review"
    ]
    categories = report["release_gate_categories"]
    assert categories["c1_fresh_empirical_evidence"] == {
        "status": "CLOSED",
        "blocker_ids": list(campaign._RESOURCE_BLOCKER_ORDER),
    }
    c2 = categories["c2_evidence_contingent_source_review"]
    assert c2["status"] == "OPEN"
    assert [gate["gate_id"] for gate in c2["gates"]] == list(
        campaign._C2_GATE_ORDER
    )
    assert [gate["status"] for gate in c2["gates"]] == [
        "CLOSED",
        "OPEN",
        "OPEN",
    ]
    assert [gate["reason"] for gate in c2["gates"]] == list(
        campaign._C2_GATE_REASONS
    )
    assert report["release_eligible"] is False


@pytest.mark.parametrize(
    ("first_dpi", "expected_reason"),
    (
        (None, "required_release_envelope_reported_dpi_missing"),
        (120, "required_release_envelope_reported_dpi_not_96"),
        (144, "required_release_envelope_reported_dpi_not_96"),
    ),
    ids=("missing", "120", "144"),
)
def test_first_case_dpi_failure_is_preserved_and_keeps_affected_c1_blocker_open(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    first_dpi: int | None,
    expected_reason: str,
) -> None:
    environment_calls = 0

    def staged_environment() -> campaign.CaptureEnvironment:
        nonlocal environment_calls
        dpi = first_dpi if environment_calls == 0 else 96
        environment_calls += 1
        return replace(_compact_environment(), reported_dpi=dpi)

    session = _capture_and_seal_small_campaign(
        monkeypatch,
        tmp_path / "source",
        source_owned=True,
        raw_factory=_compact_raw,
        environment_provider=staged_environment,
    )
    monkeypatch.setattr(campaign, "VARROCK_EAST_IRON_FIXED_UI_REGIONS", ())
    _review_all(monkeypatch, session)
    report = campaign.evaluate_release(
        session,
        repository=_repository(),
        evaluated_at_utc=_CREATED + timedelta(minutes=3),
    )
    results = cast(list[dict[str, object]], report["case_results"])
    assert results[0]["reported_dpi"] == first_dpi
    assert results[0]["required_reported_dpi"] == 96
    assert results[0]["reasons"] == [expected_reason]
    assert all(item["passed"] is True for item in results[1:])
    assert report["still_open_blockers"] == [
        "fresh-supported-startup",
        "final-constrained-v1-operating-envelope-review",
    ]
    assert report["release_gate_categories"]["c1_fresh_empirical_evidence"][
        "status"
    ] == "OPEN"
    assert report["release_gate_categories"]["c2_evidence_contingent_source_review"][
        "status"
    ] == "OPEN"
    assert [
        gate["status"]
        for gate in report["release_gate_categories"][
            "c2_evidence_contingent_source_review"
        ]["gates"]
    ] == ["OPEN", "OPEN", "OPEN"]
    assert report["release_eligible"] is False

    destination = tmp_path / "dpi-package"
    campaign.export_review_package(
        session,
        destination,
        repository=_repository(),
        exported_at_utc=_CREATED + timedelta(minutes=4),
    )
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    first_case_path = destination / manifest["cases"][0]["case_review_path"]
    public_case = json.loads(first_case_path.read_text(encoding="utf-8"))
    public_release = json.loads(
        (destination / "release-summary.json").read_text(encoding="utf-8")
    )
    assert public_case["capture_environment"]["reported_dpi"] == first_dpi
    assert public_case["release_result"]["reported_dpi"] == first_dpi
    assert public_case["release_result"]["reasons"] == [expected_reason]
    assert public_release["case_results"][0]["reported_dpi"] == first_dpi
    _enable_tiny_verifier_profile(monkeypatch)
    assert _verify_review_package(destination)["verified"] is True

    if first_dpi == 120:
        original_manifest_sha = _current_manifest_sha256(destination)

        def coordinate_case_rebind(payload: dict[str, object]) -> None:
            environment = cast(dict[str, object], payload["capture_environment"])
            environment["reported_dpi"] = 144
            release_result = cast(dict[str, object], payload["release_result"])
            release_result["reported_dpi"] = 144

        coordinated_case_sha = _rewrite_hashed_json(
            first_case_path, coordinate_case_rebind
        )

        release_path = destination / "release-summary.json"

        def coordinate_release_rebind(payload: dict[str, object]) -> None:
            case_results = cast(list[dict[str, object]], payload["case_results"])
            case_results[0]["reported_dpi"] = 144

        coordinated_release_sha = _rewrite_hashed_json(
            release_path, coordinate_release_rebind
        )

        def coordinate_manifest_rebind(payload: dict[str, object]) -> None:
            cases = cast(list[dict[str, object]], payload["cases"])
            cases[0]["case_review_sha256"] = coordinated_case_sha
            release_summary = cast(dict[str, object], payload["release_summary"])
            release_summary["sha256"] = coordinated_release_sha

        _rewrite_hashed_json(manifest_path, coordinate_manifest_rebind)
        with pytest.raises(
            campaign.CampaignIntegrityError,
            match="independently retained SHA-256",
        ):
            campaign.verify_review_package(
                destination,
                expected_manifest_sha256=original_manifest_sha,
            )

    def rebind_dpi(payload: dict[str, object]) -> None:
        environment = cast(dict[str, object], payload["capture_environment"])
        environment["reported_dpi"] = 96

    rebound_case_sha = _rewrite_hashed_json(first_case_path, rebind_dpi)

    def rebind_manifest(payload: dict[str, object]) -> None:
        cases = cast(list[dict[str, object]], payload["cases"])
        cases[0]["case_review_sha256"] = rebound_case_sha

    _rewrite_hashed_json(manifest_path, rebind_manifest)
    with pytest.raises(campaign.CampaignIntegrityError, match="DPI evidence was rebound"):
        _verify_review_package(destination)


def test_source_owned_origin_rejects_foreign_backend_before_grab(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    backend = FakeCaptureBackend([_small_raw()], name="foreign-backend")
    with CaptureSource(backend) as source:
        with pytest.raises(campaign.CampaignIntegrityError, match="windows-runelite"):
            campaign._capture_next_with_source(
                session,
                source,
                repository=_repository(),
                environment_provider=_environment,
                provenance_capability=campaign._SOURCE_OWNED_CAPTURE_CAPABILITY,
                expected_case_id="supported-startup-positive",
                captured_at_utc=_CREATED + timedelta(seconds=1),
            )
    assert backend.grab_calls == 0


def test_injected_failure_then_source_owned_success_stays_open_and_exports_origin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    monkeypatch.setattr(campaign, "LIVE_RESOURCE_CAMPAIGN_AUTHORIZED", True)
    monkeypatch.setattr(campaign, "_production_json", _fake_production)
    failed_backend = FakeCaptureBackend(
        [CaptureUnavailableError("no frame")],
        name="injected-failure",
    )
    with CaptureSource(failed_backend, retry_attempts=0) as failed_source:
        with pytest.raises(CaptureUnavailableError, match="no frame"):
            _capture_next_injected(
                session,
                failed_source,
                captured_at_utc=_CREATED + timedelta(seconds=1),
            )

    frames = [_small_raw(index) for index in range(1, len(campaign.CAMPAIGN_PLAN) + 1)]
    source_owned_backend = FakeCaptureBackend(frames, name="windows-runelite")
    clock = ManualClock(1.0)
    with CaptureSource(source_owned_backend, clock=clock) as source:
        for index, case in enumerate(campaign.CAMPAIGN_PLAN, start=1):
            campaign._capture_next_with_source(
                session,
                source,
                repository=_repository(),
                environment_provider=_environment,
                provenance_capability=campaign._SOURCE_OWNED_CAPTURE_CAPABILITY,
                expected_case_id=case.case_id,
                captured_at_utc=_CREATED + timedelta(seconds=index + 1),
            )
            clock.advance(1.0)
    campaign.seal_campaign(
        session,
        repository=_repository(),
        sealed_at_utc=_CREATED + timedelta(minutes=1),
    )
    _review_all(monkeypatch, session)

    report = campaign.evaluate_release(
        session,
        repository=_repository(),
        evaluated_at_utc=_CREATED + timedelta(minutes=3),
    )
    first = cast(list[dict[str, object]], report["case_results"])[0]
    assert first["passed"] is False
    assert "mixed_or_malformed_prior_capture_origin" in first["reasons"]
    assert "fresh-supported-startup" in report["still_open_blockers"]
    assert report["release_gate_categories"]["c1_fresh_empirical_evidence"][
        "status"
    ] == "OPEN"
    assert report["release_gate_categories"]["c2_evidence_contingent_source_review"][
        "status"
    ] == "OPEN"
    assert report["release_eligible"] is False

    destination = tmp_path / "mixed-origin-package"
    campaign.export_review_package(
        session,
        destination,
        repository=_repository(),
        exported_at_utc=_CREATED + timedelta(minutes=4),
    )
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    public_case = json.loads(
        (destination / manifest["cases"][0]["case_review_path"]).read_text(
            encoding="utf-8"
        )
    )
    policy = public_case["capture_policy_evidence"]
    assert policy["evidence_origin"] == campaign._SOURCE_OWNED_EVIDENCE_ORIGIN
    assert policy["prior_no_frame_failure_count"] == 1
    prior = policy["prior_no_frame_failure_provenance"]
    assert isinstance(prior, list) and len(prior) == 1
    assert prior[0]["capture_source_backend_name"] == "injected-failure"
    assert prior[0]["evidence_origin"] == campaign._INJECTED_EVIDENCE_ORIGIN
    assert prior[0]["report_sha256"] == policy[
        "prior_no_frame_failure_report_sha256s"
    ][0]
    assert public_case["release_result"]["passed"] is False


def test_metadata_only_wrong_geometry_review_exports_but_keeps_blockers_open(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = _capture_and_seal_small_campaign(
        monkeypatch,
        tmp_path / "source",
        raw_factory=_compact_raw,
        environment_provider=_compact_environment,
    )
    for case in campaign.CAMPAIGN_PLAN:
        artifact_sha = _prepare_case_review(session, case)
        unreviewable = replace(
            _decision_for(
                case,
                meaning=campaign.ReviewMeaning.UNREVIEWABLE_PIXELS_WITHHELD,
                states=(ResourceVisualState.UNCERTAIN,) * 4,
                review_artifact_sha256=artifact_sha,
            ),
            focal_resource_id=None,
            node_phase=None,
            obstruction_target_kind=None,
            obstruction_target_id=None,
            subject_region=None,
        )
        campaign.record_case_review(
            session,
            unreviewable,
            repository=_repository(),
            recorded_at_utc=_CREATED + timedelta(minutes=2, seconds=1),
        )

    report = campaign.evaluate_release(
        session,
        repository=_repository(),
        evaluated_at_utc=_CREATED + timedelta(minutes=3),
    )
    assert report["closed_blockers"] == []
    assert all(
        "wrong_frame_geometry_or_pixel_format" in item["reasons"]
        for item in cast(list[dict[str, object]], report["case_results"])
    )

    destination = tmp_path / "metadata-only-package"
    campaign.export_review_package(
        session,
        destination,
        repository=_repository(),
        exported_at_utc=_CREATED + timedelta(minutes=4),
    )
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    public_release = json.loads(
        (destination / "release-summary.json").read_text(encoding="utf-8")
    )
    assert public_release["closed_blockers"] == []
    assert all(
        item["sanitized_raw_gzip_sha256"] is None
        and item["sanitized_preview_sha256"] is None
        for item in manifest["cases"]
    )
    assert _verify_review_package(destination)["verified"] is True

    first_case = destination / manifest["cases"][0]["case_review_path"]

    def forge_supported_geometry(payload: dict[str, object]) -> None:
        frame = cast(dict[str, object], payload["frame"])
        frame["width"] = 1005
        frame["height"] = 1078
        environment = cast(dict[str, object], payload["capture_environment"])
        environment["window_client_width"] = 1005
        environment["window_client_height"] = 1078

    rebound_case_sha = _rewrite_hashed_json(first_case, forge_supported_geometry)

    def rebind_manifest(payload: dict[str, object]) -> None:
        cases = cast(list[dict[str, object]], payload["cases"])
        cases[0]["case_review_sha256"] = rebound_case_sha

    _rewrite_hashed_json(destination / "manifest.json", rebind_manifest)
    with pytest.raises(campaign.CampaignIntegrityError, match="withheld"):
        _verify_review_package(destination)


def test_landmark_obstruction_truth_does_not_pass_when_landmark_still_matches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    landmark_id = campaign.load_varrock_east_iron_profile().scene_landmarks[0].landmark_id

    def still_matched(frame: Frame) -> dict[str, object]:
        value = _fake_production(frame)
        scene = cast(dict[str, object], value["scene"])
        scene["landmarks"] = [{"landmark_id": landmark_id, "matched": True}]
        return value

    session = _capture_and_seal_small_campaign(
        monkeypatch,
        tmp_path,
        production=still_matched,
    )
    obstruction = next(
        case
        for case in campaign.CAMPAIGN_PLAN
        if case.review_meaning is campaign.ReviewMeaning.PROFILED_OBSTRUCTION
    )
    decision = replace(
        _decision_for(obstruction),
        obstruction_target_kind="landmark",
        obstruction_target_id=landmark_id,
    )
    _review_all(monkeypatch, session, override={obstruction.case_id: decision})

    report = campaign.evaluate_release(
        session,
        repository=_repository(),
        evaluated_at_utc=_CREATED + timedelta(minutes=3),
    )
    result = next(
        item
        for item in cast(list[dict[str, object]], report["case_results"])
        if item["case_id"] == obstruction.case_id
    )
    assert result["passed"] is False
    assert "reviewed_landmark_obstruction_did_not_fail_target" in result["reasons"]


def test_release_summary_is_immutable_and_refuses_activation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "release-summary.json"
    session = _capture_and_seal_small_campaign(monkeypatch, tmp_path / "source")
    report = campaign.evaluate_release(
        session,
        repository=_repository(),
        evaluated_at_utc=_CREATED + timedelta(minutes=3),
    )
    digest = campaign.write_release_summary(path, report)

    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(FileExistsError):
        campaign.write_release_summary(path, report)
    with pytest.raises(campaign.CampaignError, match="nonactivating"):
        campaign.write_release_summary(
            tmp_path / "must-not-exist.json",
            {**report, "activation_allowed": True},
        )


def test_review_export_requires_truth_for_every_case_before_creating_destination(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = _capture_and_seal_small_campaign(monkeypatch, tmp_path)
    destination = tmp_path / "must-not-exist"

    with pytest.raises(campaign.CampaignError, match="truth for all 15 cases"):
        campaign.export_review_package(
            session,
            destination,
            repository=_repository(),
            exported_at_utc=_CREATED + timedelta(minutes=3),
        )

    assert not destination.exists()


def test_real_available_review_sanitization_preserves_production_authority() -> None:
    frame = _reviewed_available_frame()
    sanitized_payload = campaign._sanitize_bgra_for_review(frame)
    sanitized = Frame.from_raw(
        RawFrame(
            payload=sanitized_payload,
            width=frame.width,
            height=frame.height,
            pixel_format=frame.pixel_format,
        ),
        frame_id=frame.frame_id,
        captured_monotonic_s=frame.captured_monotonic_s,
    )

    assert sanitized_payload != frame.payload
    raw_production = campaign._production_json(frame)
    sanitized_production = campaign._production_json(sanitized)
    assert campaign._production_authority_equivalent(
        raw_production, sanitized_production
    )
    assert not campaign._production_equivalent(raw_production, sanitized_production)
    raw_trust = cast(dict[str, object], raw_production["trust"])
    sanitized_trust = cast(dict[str, object], sanitized_production["trust"])
    assert sanitized_production["scene"] == raw_production["scene"]
    assert sanitized_trust["resources"] == raw_trust["resources"]
    assert (
        sanitized_trust["definitive_target_ids"]
        == raw_trust["definitive_target_ids"]
    )
    assert (
        sanitized_trust["production_actionable_target_ids"]
        == raw_trust["production_actionable_target_ids"]
    )
    assert (
        sanitized_trust["production_interaction_regions"]
        == raw_trust["production_interaction_regions"]
    )

    for mask_x, mask_y, mask_width, mask_height in (
        campaign.VARROCK_EAST_IRON_FIXED_UI_REGIONS
    ):
        for pixel_y in range(mask_y, mask_y + mask_height):
            for pixel_x in range(mask_x, mask_x + mask_width):
                offset = (pixel_y * frame.width + pixel_x) * 4
                assert sanitized_payload[offset : offset + 4] == b"\x00\x00\x00\xff"


def test_review_export_is_manifest_last_redacted_hashed_and_exclusive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = _capture_and_seal_small_campaign(
        monkeypatch,
        tmp_path / "source",
        raw_factory=_compact_raw,
        environment_provider=_compact_environment,
    )
    monkeypatch.setattr(campaign, "VARROCK_EAST_IRON_FIXED_UI_REGIONS", ())
    _review_all(monkeypatch, session)
    destination = tmp_path / "public-review"
    real_write = campaign._write_hashed_artifact
    publication_order: list[Path] = []

    def recording_write(path: Path, payload: bytes) -> str:
        digest = real_write(path, payload)
        if path.is_relative_to(destination):
            publication_order.append(path)
        return digest

    monkeypatch.setattr(campaign, "_write_hashed_artifact", recording_write)
    result = campaign.export_review_package(
        session,
        destination,
        repository=_repository(),
        exported_at_utc=_CREATED + timedelta(minutes=3),
    )

    assert publication_order[-1] == destination / "manifest.json"
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert result == {
        "package": str(destination),
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "release_summary_sha256": manifest["release_summary"]["sha256"],
        "case_count": 15,
        "contains_private_full_frames": False,
        "activation_allowed": False,
    }
    assert manifest["manifest_written_last"] is True
    assert manifest["contains_private_full_frames"] is False
    assert manifest["activation_allowed"] is False
    assert manifest["promotion_allowed"] is False
    assert manifest["input_authority"] is False
    assert len(manifest["cases"]) == 15

    expected_files = {
        "manifest.json",
        "manifest.json.sha256",
        "release-summary.json",
        "release-summary.json.sha256",
    }
    for item in manifest["cases"]:
        case_review = destination / item["case_review_path"]
        case_directory = case_review.parent
        raw_gzip = case_directory / "sanitized-frame.raw.gz"
        preview = case_directory / "sanitized-preview.bmp"
        for artifact, expected_sha in (
            (case_review, item["case_review_sha256"]),
            (raw_gzip, item["sanitized_raw_gzip_sha256"]),
            (preview, item["sanitized_preview_sha256"]),
        ):
            actual_sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
            assert actual_sha == expected_sha
            assert artifact.with_name(f"{artifact.name}.sha256").read_text(
                encoding="ascii"
            ) == f"{actual_sha}\n"
            expected_files.add(artifact.relative_to(destination).as_posix())
            expected_files.add(
                artifact.with_name(f"{artifact.name}.sha256")
                .relative_to(destination)
                .as_posix()
            )
        public_case = json.loads(case_review.read_text(encoding="utf-8"))
        public_environment = public_case["capture_environment"]
        assert public_environment["window_title_redacted"] is True
        assert "window_title" not in public_environment
        assert public_case["contains_private_full_frame"] is False
        assert public_case["activation_allowed"] is False
        assert public_case["input_authority"] is False

    actual_files = {
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*")
        if path.is_file()
    }
    assert actual_files == expected_files
    assert all(b"private-title" not in path.read_bytes() for path in destination.rglob("*") if path.is_file())

    _enable_tiny_verifier_profile(monkeypatch)
    verified = _verify_review_package(destination)
    assert verified["verified"] is True
    assert verified["manifest_sha256"] == result["manifest_sha256"]
    assert verified["activation_allowed"] is False

    before = {path: path.read_bytes() for path in destination.rglob("*") if path.is_file()}
    with pytest.raises(FileExistsError):
        campaign.export_review_package(
            session,
            destination,
            repository=_repository(),
            exported_at_utc=_CREATED + timedelta(minutes=4),
        )
    assert {path: path.read_bytes() for path in before} == before

    first_case_path = destination / manifest["cases"][0]["case_review_path"]

    def weaken_nested_production(payload: dict[str, object]) -> None:
        production = cast(dict[str, object], payload["production"])
        trust = cast(dict[str, object], production["trust"])
        trust["accepted"] = False

    rebound_case_sha = _rewrite_hashed_json(first_case_path, weaken_nested_production)

    def rebind_manifest(payload: dict[str, object]) -> None:
        cases = cast(list[dict[str, object]], payload["cases"])
        cases[0]["case_review_sha256"] = rebound_case_sha

    _rewrite_hashed_json(destination / "manifest.json", rebind_manifest)
    with pytest.raises(campaign.CampaignIntegrityError, match="exact public replay"):
        _verify_review_package(destination)

    for path, payload in before.items():
        path.write_bytes(payload)
    first_meta = manifest["cases"][0]
    first_case_path = destination / first_meta["case_review_path"]
    oversized_pixels = b"\x00\x00\x00\xff\x00"
    oversized_compressed = gzip.compress(oversized_pixels, compresslevel=9, mtime=0)
    raw_gzip = first_case_path.parent / "sanitized-frame.raw.gz"
    raw_gzip.write_bytes(oversized_compressed)
    oversized_file_sha = hashlib.sha256(oversized_compressed).hexdigest()
    raw_gzip.with_name(f"{raw_gzip.name}.sha256").write_text(
        f"{oversized_file_sha}\n",
        encoding="ascii",
    )

    def rebind_oversized_case(payload: dict[str, object]) -> None:
        artifacts = cast(dict[str, object], payload["sanitized_artifacts"])
        raw_meta = cast(dict[str, object], artifacts["raw_gzip"])
        raw_meta["sha256"] = oversized_file_sha
        raw_meta["decompressed_sha256"] = hashlib.sha256(oversized_pixels).hexdigest()

    oversized_case_sha = _rewrite_hashed_json(
        first_case_path,
        rebind_oversized_case,
    )

    def rebind_oversized_manifest(payload: dict[str, object]) -> None:
        cases = cast(list[dict[str, object]], payload["cases"])
        cases[0]["case_review_sha256"] = oversized_case_sha
        cases[0]["sanitized_raw_gzip_sha256"] = oversized_file_sha

    _rewrite_hashed_json(
        destination / "manifest.json",
        rebind_oversized_manifest,
    )
    with pytest.raises(campaign.CampaignIntegrityError, match="decompressed size"):
        _verify_review_package(destination)


def test_failed_reviewed_case_remains_packaged_as_nonactivating_regression_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = _capture_and_seal_small_campaign(monkeypatch, tmp_path / "source")
    first = campaign.CAMPAIGN_PLAN[0]
    wrong = _decision_for(
        first,
        meaning=campaign.ReviewMeaning.UNSUPPORTED_LOCATION,
        states=(ResourceVisualState.UNCERTAIN,) * 4,
    )
    _review_all(monkeypatch, session, override={first.case_id: wrong})
    destination = tmp_path / "failed-review-package"

    campaign.export_review_package(
        session,
        destination,
        repository=_repository(),
        exported_at_utc=_CREATED + timedelta(minutes=3),
    )

    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    first_entry = manifest["cases"][0]
    public_case = json.loads(
        (destination / first_entry["case_review_path"]).read_text(encoding="utf-8")
    )
    release_result = public_case["release_result"]
    assert manifest["case_count"] == 15
    assert release_result["passed"] is False
    assert release_result["permanent_evidence_required"] is True
    assert release_result["policy_change_allowed_from_failure"] is False
    assert "reviewer_did_not_confirm_requested_case_meaning" in release_result["reasons"]
    assert public_case["activation_allowed"] is False
    assert public_case["input_authority"] is False
    assert (public_case["sanitized_artifacts"]["raw_gzip"]["path"]).endswith(
        "sanitized-frame.raw.gz"
    )


def test_detector_error_remains_reviewable_packaged_and_privately_redacted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    private_error = "private-runtime-detail-must-not-leak"

    def fail_first(frame: Frame) -> dict[str, object]:
        if frame.frame_id == 1:
            raise RuntimeError(private_error)
        return _fake_production(frame)

    session = _capture_and_seal_small_campaign(
        monkeypatch,
        tmp_path / "source",
        production=fail_first,
        raw_factory=_compact_raw,
        environment_provider=_compact_environment,
    )
    status = campaign.load_campaign_status(session, repository=_repository())
    assert status.sealed is True
    assert len(status.captured_case_ids) == len(campaign.CAMPAIGN_PLAN)

    monkeypatch.setattr(campaign, "VARROCK_EAST_IRON_FIXED_UI_REGIONS", ())
    _review_all(monkeypatch, session)
    report = campaign.evaluate_release(
        session,
        repository=_repository(),
        evaluated_at_utc=_CREATED + timedelta(minutes=3),
    )
    first_result = cast(list[dict[str, object]], report["case_results"])[0]
    assert first_result["passed"] is False
    assert first_result["permanent_evidence_required"] is True
    assert "production_ensemble_not_complete_or_trusted" in first_result["reasons"]

    destination = tmp_path / "detector-error-package"
    campaign.export_review_package(
        session,
        destination,
        repository=_repository(),
        exported_at_utc=_CREATED + timedelta(minutes=4),
    )
    assert all(
        private_error.encode() not in path.read_bytes()
        for path in destination.rglob("*")
        if path.is_file()
    )
    _enable_tiny_verifier_profile(monkeypatch)
    assert _verify_review_package(destination)["verified"] is True


def test_review_template_is_blank_and_never_copies_operator_meaning_or_truth(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = _capture_and_seal_small_campaign(monkeypatch, tmp_path)
    output = tmp_path / "review-template.json"
    case = campaign.CAMPAIGN_PLAN[2]
    artifact_sha = _prepare_case_review(session, case)
    monkeypatch.setattr(
        campaign_cli, "_require_private_session", lambda path, root: session
    )
    monkeypatch.setattr(
        campaign_cli, "read_repository_provenance", lambda root: _repository()
    )

    assert campaign_cli.main(
        [
            "review-template",
            "--session",
            str(session),
            "--case-id",
            case.case_id,
            "--output",
            str(output),
        ],
        repository_root=Path.cwd(),
    ) == 0

    template = json.loads(output.read_text(encoding="utf-8"))
    assert template["case_id"] == case.case_id
    assert template["reviewer_id"] == ""
    assert template["reviewed_at_utc"] == ""
    assert template["meaning"] == ""
    assert template["review_artifact_sha256"] == artifact_sha
    assert template["privacy_review_confirmed"] is False
    assert template["focal_resource_id"] is None
    assert template["node_phase"] is None
    assert template["obstruction_target"] is None
    assert template["subject_region"] is None
    assert all(item["state"] == "" for item in template["resource_truth"])
    assert case.operator_prompt not in output.read_text(encoding="utf-8")
    assert case.review_meaning.value not in output.read_text(encoding="utf-8")


def test_cli_prepare_review_dispatches_only_for_owned_private_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = _capture_and_seal_small_campaign(monkeypatch, tmp_path)
    case = campaign.CAMPAIGN_PLAN[0]
    monkeypatch.setattr(
        campaign_cli, "_require_private_session", lambda path, root: session
    )
    monkeypatch.setattr(
        campaign_cli, "read_repository_provenance", lambda root: _repository()
    )

    assert campaign_cli.main(
        ["prepare-review", "--session", str(session), "--case-id", case.case_id],
        repository_root=tmp_path,
    ) == 0
    assert campaign.load_campaign_status(
        session, repository=_repository()
    ).prepared_case_ids == (case.case_id,)


def test_cli_rejects_session_outside_source_owned_private_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    private_root = tmp_path / "repository" / "diagnostics" / "resource-campaigns"
    outside = tmp_path / "foreign-session"
    monkeypatch.setattr(
        campaign_cli, "_private_campaign_root", lambda repository: private_root
    )

    with pytest.raises(campaign.CampaignError, match="outside"):
        campaign_cli._require_private_session(outside, tmp_path / "repository")


def test_cli_capture_requires_exact_next_case_ack_before_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    backend_constructed = False

    def forbidden_backend(*args: object, **kwargs: object) -> None:
        nonlocal backend_constructed
        backend_constructed = True
        raise AssertionError("wrong acknowledgment must stop before Windows capture")

    monkeypatch.setattr(campaign_cli, "LIVE_RESOURCE_CAMPAIGN_AUTHORIZED", True)
    monkeypatch.setattr(
        campaign_cli, "read_repository_provenance", lambda root: _repository()
    )
    monkeypatch.setattr(campaign_cli, "_capture_next_windows_case", forbidden_backend)

    with pytest.raises(campaign.CampaignError, match="acknowledgment"):
        campaign_cli._capture(
            session,
            tmp_path,
            confirmed_case_id="northwest-available",
        )
    assert backend_constructed is False


def test_source_owned_windows_wrapper_rejects_nonprivate_root_before_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = _session(tmp_path / "outside")
    backend_constructed = False

    def forbidden_backend(*args: object, **kwargs: object) -> None:
        nonlocal backend_constructed
        backend_constructed = True
        raise AssertionError("private-root rejection must precede backend open")

    monkeypatch.setattr(campaign, "LIVE_RESOURCE_CAMPAIGN_AUTHORIZED", True)
    monkeypatch.setattr(campaign, "WindowsCaptureBackend", forbidden_backend)
    with pytest.raises(campaign.CampaignIntegrityError, match="Git-ignored private"):
        campaign._capture_next_windows_case(
            session,
            repository_root=tmp_path / "repository",
            repository=_repository(),
            expected_case_id="supported-startup-positive",
        )
    assert backend_constructed is False


def test_source_owned_windows_wrapper_rejects_stale_ack_before_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repository"
    private_root = repository_root / "diagnostics" / "resource-release-campaigns"
    session = campaign.create_campaign(
        private_root,
        operator_id="operator-a",
        repository=_repository(),
        created_at_utc=_CREATED,
        nonce=_NONCE,
    )
    backend_constructed = False

    def forbidden_backend(*args: object, **kwargs: object) -> None:
        nonlocal backend_constructed
        backend_constructed = True
        raise AssertionError("stale acknowledgment must precede backend open")

    monkeypatch.setattr(campaign, "LIVE_RESOURCE_CAMPAIGN_AUTHORIZED", True)
    monkeypatch.setattr(campaign, "WindowsCaptureBackend", forbidden_backend)
    monkeypatch.setattr(
        campaign.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    with pytest.raises(campaign.CampaignIntegrityError, match="acknowledgment is stale"):
        campaign._capture_next_windows_case(
            session,
            repository_root=repository_root,
            repository=_repository(),
            expected_case_id="northwest-available",
        )
    assert backend_constructed is False


def test_capture_cli_requires_explicit_staged_case_acknowledgment() -> None:
    with pytest.raises(SystemExit):
        campaign_cli.build_parser().parse_args(
            ["capture-next", "--session", "owned-session"]
        )


def test_verify_export_cli_requires_independently_retained_manifest_hash() -> None:
    parser = campaign_cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["verify-export", "--package", "review-package"])

    parsed = parser.parse_args(
        [
            "verify-export",
            "--package",
            "review-package",
            "--expected-manifest-sha256",
            "a" * 64,
        ]
    )
    assert parsed.expected_manifest_sha256 == "a" * 64


def test_prepare_followup_cli_has_only_verified_package_and_output_inputs() -> None:
    parser = campaign_cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "prepare-followup",
                "--package",
                "review-package",
                "--output",
                "followup.json",
            ]
        )

    parsed = parser.parse_args(
        [
            "prepare-followup",
            "--package",
            "review-package",
            "--expected-manifest-sha256",
            "a" * 64,
            "--output",
            "followup.json",
        ]
    )
    assert vars(parsed) == {
        "command": "prepare-followup",
        "package": Path("review-package"),
        "expected_manifest_sha256": "a" * 64,
        "output": Path("followup.json"),
    }
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "prepare-followup",
                "--package",
                "review-package",
                "--expected-manifest-sha256",
                "a" * 64,
                "--output",
                "followup.json",
                "--approve",
                "true",
            ]
        )

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "verify-followup",
                "--inputs",
                "followup.json",
            ]
        )
    verified = parser.parse_args(
        [
            "verify-followup",
            "--inputs",
            "followup.json",
            "--expected-sha256",
            "b" * 64,
        ]
    )
    assert vars(verified) == {
        "command": "verify-followup",
        "inputs": Path("followup.json"),
        "expected_sha256": "b" * 64,
    }
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "verify-followup",
                "--inputs",
                "followup.json",
                "--expected-sha256",
                "b" * 64,
                "--approve",
                "true",
            ]
        )


def test_cli_capture_gate_fails_before_windows_backend_construction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    backend_constructed = False

    def forbidden_backend(*args: object, **kwargs: object) -> None:
        nonlocal backend_constructed
        backend_constructed = True
        raise AssertionError("disabled live gate must stop before Windows capture")

    monkeypatch.setattr(campaign_cli, "_capture_next_windows_case", forbidden_backend)
    monkeypatch.setattr(
        campaign_cli, "_require_private_session", lambda path, root: Path(path)
    )

    assert campaign_cli.main(
        [
            "capture-next",
            "--session",
            str(tmp_path / "not-opened"),
            "--confirm-staged-case",
            "supported-startup-positive",
        ],
        repository_root=tmp_path,
    ) == 1
    assert backend_constructed is False


@pytest.mark.parametrize(
    ("option", "value"),
    (
        ("--title", "attacker-window"),
        ("--case-id", "attacker-case"),
        ("--detector", "attacker-detector"),
        ("--retry", "9"),
        ("--camera", "recover"),
    ),
)
def test_capture_cli_exposes_no_identity_retry_or_camera_overrides(
    option: str,
    value: str,
) -> None:
    parser = campaign_cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "capture-next",
                "--session",
                "owned-session",
                "--confirm-staged-case",
                "supported-startup-positive",
                option,
                value,
            ]
        )


def _export_compact_public_package(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    source_owned: bool = False,
    environment_provider: Callable[[], campaign.CaptureEnvironment] = (
        _compact_environment
    ),
    raw_factory: Callable[[int], RawFrame] = _compact_raw,
    review_override: Mapping[str, campaign.ReviewDecision] | None = None,
) -> Path:
    session = _capture_and_seal_small_campaign(
        monkeypatch,
        tmp_path / "source",
        source_owned=source_owned,
        raw_factory=raw_factory,
        environment_provider=environment_provider,
    )
    monkeypatch.setattr(campaign, "VARROCK_EAST_IRON_FIXED_UI_REGIONS", ())
    _review_all(monkeypatch, session, override=review_override)
    destination = tmp_path / "public-review"
    campaign.export_review_package(
        session,
        destination,
        repository=_repository(),
        exported_at_utc=_CREATED + timedelta(minutes=4),
    )
    _enable_tiny_verifier_profile(monkeypatch)
    assert _verify_review_package(destination)["verified"] is True
    return destination


def _export_withheld_public_package(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    source_owned: bool = False,
    environment_provider: Callable[[], campaign.CaptureEnvironment] = (
        _compact_environment
    ),
    raw_factory: Callable[[int], RawFrame] = _compact_raw,
) -> Path:
    session = _capture_and_seal_small_campaign(
        monkeypatch,
        tmp_path / "source",
        source_owned=source_owned,
        raw_factory=raw_factory,
        environment_provider=environment_provider,
    )
    for case in campaign.CAMPAIGN_PLAN:
        artifact_sha = _prepare_case_review(session, case)
        decision = replace(
            _decision_for(
                case,
                meaning=campaign.ReviewMeaning.UNREVIEWABLE_PIXELS_WITHHELD,
                states=(ResourceVisualState.UNCERTAIN,) * 4,
                review_artifact_sha256=artifact_sha,
            ),
            focal_resource_id=None,
            node_phase=None,
            obstruction_target_kind=None,
            obstruction_target_id=None,
            subject_region=None,
        )
        campaign.record_case_review(
            session,
            decision,
            repository=_repository(),
            recorded_at_utc=_CREATED + timedelta(minutes=2, seconds=1),
        )
    destination = tmp_path / "withheld-public-review"
    campaign.export_review_package(
        session,
        destination,
        repository=_repository(),
        exported_at_utc=_CREATED + timedelta(minutes=4),
    )
    assert _verify_review_package(destination)["verified"] is True
    return destination


def _load_followup(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    assert path.with_name(f"{path.name}.sha256").read_text(
        encoding="ascii"
    ) == f"{digest}\n"
    decoded = json.loads(payload)
    assert isinstance(decoded, dict)
    return cast(dict[str, object], decoded)


def test_prepare_followup_all_pass_is_deterministic_hashed_and_nonactivating(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    package = _export_compact_public_package(
        monkeypatch,
        tmp_path / "package-source",
        source_owned=True,
    )
    manifest_sha = _current_manifest_sha256(package)
    first_output = tmp_path / "followup-a.json"
    second_output = tmp_path / "followup-b.json"

    first_result = campaign.prepare_release_followup_inputs(
        package,
        first_output,
        expected_manifest_sha256=manifest_sha,
    )
    second_result = campaign.prepare_release_followup_inputs(
        package,
        second_output,
        expected_manifest_sha256=manifest_sha,
    )

    assert first_output.read_bytes() == second_output.read_bytes()
    assert first_result["sha256"] == second_result["sha256"]
    followup = _load_followup(first_output)
    assert followup["inputs_id"] == "resource-release-followup-inputs-v1"
    assert followup["source_snapshot"]["manifest_sha256"] == manifest_sha
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    assert followup["source_snapshot"]["release_summary_sha256"] == manifest[
        "release_summary"
    ]["sha256"]
    assert [item["case_id"] for item in followup["case_bindings"]] == [
        case.case_id for case in campaign.CAMPAIGN_PLAN
    ]
    assert [item["hashes"]["case_review_sha256"] for item in followup["case_bindings"]] == [
        item["case_review_sha256"] for item in manifest["cases"]
    ]
    assert followup["verification"] == {
        "verified": True,
        "expected_manifest_sha256_matched": True,
        "case_count": 15,
        "operator_labels_included": False,
        "operator_labels_are_reviewer_truth": False,
        "all_cases_explicitly_privacy_reviewed": True,
        "contains_private_full_frames": False,
    }
    assert followup["c1_result"]["status"] == "CLOSED"
    assert followup["failure_promotion_inputs"] == {
        "status": "NOT_REQUIRED",
        "target_dataset_id": "varrock-east-iron-release-regressions-v1",
        "candidate_count": 0,
        "candidates": [],
        "nonrelease_evidence_count": 0,
        "nonrelease_evidence": [],
        "promotion_complete": False,
    }
    assert [
        gate["status"]
        for gate in followup["c2_envelope_review_inputs"][
            "reported_c2_category"
        ]["gates"]
    ] == ["CLOSED", "OPEN", "OPEN"]
    assert followup["c2_envelope_review_inputs"]["envelope_approved"] is False
    assert followup["authority"] == {
        "approval_authority": False,
        "release_eligible": False,
        "activation_allowed": False,
        "promotion_allowed": False,
        "input_authority": False,
    }
    output_bytes = first_output.read_bytes()
    assert b"private-title" not in output_bytes
    assert b"operator_prompt" not in output_bytes
    assert all(
        case.operator_prompt.encode() not in output_bytes
        for case in campaign.CAMPAIGN_PLAN
    )
    verified = campaign.verify_release_followup_inputs(
        first_output,
        expected_sha256=cast(str, first_result["sha256"]),
    )
    assert verified["verified"] is True
    assert verified["case_count"] == 15
    assert verified["failure_candidate_count"] == 0

    retained_second_sha = cast(str, second_result["sha256"])

    def forge_authority(payload: dict[str, object]) -> None:
        authority = cast(dict[str, object], payload["authority"])
        authority["release_eligible"] = True

    forged_sha = _rewrite_hashed_json(second_output, forge_authority)
    with pytest.raises(campaign.CampaignIntegrityError, match="stored SHA-256"):
        campaign.verify_release_followup_inputs(
            second_output,
            expected_sha256=retained_second_sha,
        )
    with pytest.raises(campaign.CampaignIntegrityError, match="deny-only"):
        campaign.verify_release_followup_inputs(
            second_output,
            expected_sha256=forged_sha,
        )

    production_output = tmp_path / "followup-production-tamper.json"
    production_result = campaign.prepare_release_followup_inputs(
        package,
        production_output,
        expected_manifest_sha256=manifest_sha,
    )
    retained_production_sha = cast(str, production_result["sha256"])

    def forge_nested_production_authority(payload: dict[str, object]) -> None:
        bindings = cast(list[dict[str, object]], payload["case_bindings"])
        production = cast(dict[str, object], bindings[0]["production_snapshot"])
        production["input_authority"] = True

    forged_production_sha = _rewrite_hashed_json(
        production_output,
        forge_nested_production_authority,
    )
    with pytest.raises(campaign.CampaignIntegrityError, match="stored SHA-256"):
        campaign.verify_release_followup_inputs(
            production_output,
            expected_sha256=retained_production_sha,
        )
    with pytest.raises(
        campaign.CampaignIntegrityError,
        match="production identity/authority",
    ):
        campaign.verify_release_followup_inputs(
            production_output,
            expected_sha256=forged_production_sha,
        )

    wrong_dpi_output = tmp_path / "followup-wrong-dpi-tamper.json"
    campaign.prepare_release_followup_inputs(
        package,
        wrong_dpi_output,
        expected_manifest_sha256=manifest_sha,
    )

    def forge_pass_at_wrong_dpi(payload: dict[str, object]) -> None:
        bindings = cast(list[dict[str, object]], payload["case_bindings"])
        capture = cast(dict[str, object], bindings[0]["capture"])
        environment = cast(dict[str, object], capture["environment"])
        environment["reported_dpi"] = 120
        result = cast(dict[str, object], bindings[0]["release_result"])
        result["reported_dpi"] = 120

    forged_wrong_dpi_sha = _rewrite_hashed_json(
        wrong_dpi_output,
        forge_pass_at_wrong_dpi,
    )
    with pytest.raises(
        campaign.CampaignIntegrityError,
        match="source/DPI/geometry/reviewer prerequisites",
    ):
        campaign.verify_release_followup_inputs(
            wrong_dpi_output,
            expected_sha256=forged_wrong_dpi_sha,
        )

    wrong_origin_output = tmp_path / "followup-wrong-origin-tamper.json"
    campaign.prepare_release_followup_inputs(
        package,
        wrong_origin_output,
        expected_manifest_sha256=manifest_sha,
    )

    def forge_pass_from_injected_origin(payload: dict[str, object]) -> None:
        bindings = cast(list[dict[str, object]], payload["case_bindings"])
        origin = cast(dict[str, object], bindings[0]["capture_origin"])
        origin["evidence_origin"] = "test-injected-non-release"
        result = cast(dict[str, object], bindings[0]["release_result"])
        result["evidence_origin"] = "test-injected-non-release"

    forged_wrong_origin_sha = _rewrite_hashed_json(
        wrong_origin_output,
        forge_pass_from_injected_origin,
    )
    with pytest.raises(
        campaign.CampaignIntegrityError,
        match="source/DPI/geometry/reviewer prerequisites",
    ):
        campaign.verify_release_followup_inputs(
            wrong_origin_output,
            expected_sha256=forged_wrong_origin_sha,
        )

    contradictory_scene_output = tmp_path / "followup-scene-tamper.json"
    campaign.prepare_release_followup_inputs(
        package,
        contradictory_scene_output,
        expected_manifest_sha256=manifest_sha,
    )

    def forge_contradictory_scene(payload: dict[str, object]) -> None:
        bindings = cast(list[dict[str, object]], payload["case_bindings"])
        production = cast(dict[str, object], bindings[0]["production_snapshot"])
        scene = cast(dict[str, object], production["scene"])
        landmarks = cast(list[dict[str, object]], scene["landmarks"])
        for landmark in landmarks:
            landmark["distance"] = cast(float, landmark["threshold"]) + 0.01
            landmark["matched"] = False

    forged_scene_sha = _rewrite_hashed_json(
        contradictory_scene_output,
        forge_contradictory_scene,
    )
    with pytest.raises(
        campaign.CampaignIntegrityError,
        match="scene summary contradicts landmarks",
    ):
        campaign.verify_release_followup_inputs(
            contradictory_scene_output,
            expected_sha256=forged_scene_sha,
        )

    negative_overlap_output = tmp_path / "followup-negative-overlap-tamper.json"
    campaign.prepare_release_followup_inputs(
        package,
        negative_overlap_output,
        expected_manifest_sha256=manifest_sha,
    )

    def forge_negative_subject_target_overlap(payload: dict[str, object]) -> None:
        bindings = cast(list[dict[str, object]], payload["case_bindings"])
        binding = next(
            item
            for item in bindings
            if cast(dict[str, object], item["reviewer_truth"])["meaning"]
            == "neighboring-copper"
        )
        truth = cast(dict[str, object], binding["reviewer_truth"])
        subject = cast(list[int], truth["subject_region"])
        production = cast(dict[str, object], binding["production_snapshot"])
        trust = cast(dict[str, object], production["trust"])
        resources = cast(list[dict[str, object]], trust["resources"])
        resources[0]["interaction_region"] = subject
        regions = cast(dict[str, object], trust["production_interaction_regions"])
        regions[campaign.VARROCK_EAST_IRON_RESOURCE_IDS[0]] = subject

    forged_negative_overlap_sha = _rewrite_hashed_json(
        negative_overlap_output,
        forge_negative_subject_target_overlap,
    )
    with pytest.raises(
        campaign.CampaignIntegrityError,
        match="production interaction region changed",
    ):
        campaign.verify_release_followup_inputs(
            negative_overlap_output,
            expected_sha256=forged_negative_overlap_sha,
        )

    original_output = first_output.read_bytes()
    original_sidecar = first_output.with_name(
        f"{first_output.name}.sha256"
    ).read_bytes()
    with pytest.raises(FileExistsError):
        campaign.prepare_release_followup_inputs(
            package,
            first_output,
            expected_manifest_sha256=manifest_sha,
        )
    assert first_output.read_bytes() == original_output
    assert first_output.with_name(f"{first_output.name}.sha256").read_bytes() == (
        original_sidecar
    )

    snapshot = campaign._load_verified_review_package_snapshot(
        package,
        expected_manifest_sha256=manifest_sha,
    )

    def frozen_snapshot(
        package_dir: Path,
        *,
        expected_manifest_sha256: str,
    ) -> object:
        del package_dir, expected_manifest_sha256
        return snapshot

    monkeypatch.setattr(
        campaign,
        "_load_verified_review_package_snapshot",
        frozen_snapshot,
    )
    concurrent_output = tmp_path / "concurrent-followup.json"
    barrier = threading.Barrier(3)
    successes: list[dict[str, object]] = []
    failures: list[BaseException] = []

    def writer() -> None:
        barrier.wait()
        try:
            successes.append(
                campaign.prepare_release_followup_inputs(
                    package,
                    concurrent_output,
                    expected_manifest_sha256=manifest_sha,
                )
            )
        except BaseException as exc:  # noqa: BLE001 - adversarial writer result
            failures.append(exc)

    threads = [threading.Thread(target=writer) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], FileExistsError)
    assert _load_followup(concurrent_output) == followup


def test_prepare_followup_retains_exact_failure_as_unapproved_replay_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first = campaign.CAMPAIGN_PLAN[0]
    wrong = _decision_for(
        first,
        meaning=campaign.ReviewMeaning.UNSUPPORTED_LOCATION,
        states=(ResourceVisualState.UNCERTAIN,) * 4,
    )
    package = _export_compact_public_package(
        monkeypatch,
        tmp_path / "package-source",
        source_owned=True,
        review_override={first.case_id: wrong},
    )
    output = tmp_path / "followup.json"
    campaign.prepare_release_followup_inputs(
        package,
        output,
        expected_manifest_sha256=_current_manifest_sha256(package),
    )
    followup = _load_followup(output)
    promotion = followup["failure_promotion_inputs"]
    assert promotion["status"] == "PENDING_EXTERNAL"
    assert promotion["candidate_count"] == 1
    candidate = promotion["candidates"][0]
    assert candidate["case_id"] == first.case_id
    assert candidate["disposition"] == "REPLAY_CANDIDATE"
    assert candidate["replay_candidate"] is True
    assert candidate["source_owned_release_evidence"] is True
    assert candidate["promotion_complete"] is False
    assert candidate["policy_change_allowed_from_failure"] is False
    assert candidate["reviewer_truth"]["meaning"] == "unsupported-location"
    assert candidate["sanitized_raw_gzip"]["path"].endswith(
        "sanitized-frame.raw.gz"
    )
    assert "reviewer_did_not_confirm_requested_case_meaning" in candidate[
        "release_reasons"
    ]
    assert followup["c1_result"]["status"] == "OPEN"
    assert followup["c2_envelope_review_inputs"][
        "retained_failure_case_ids"
    ] == [first.case_id]
    assert followup["authority"]["release_eligible"] is False


def test_prepare_followup_withheld_failures_are_metadata_only_not_promoted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    package = _export_withheld_public_package(
        monkeypatch,
        tmp_path / "source",
        source_owned=True,
    )
    output = tmp_path / "withheld-followup.json"
    campaign.prepare_release_followup_inputs(
        package,
        output,
        expected_manifest_sha256=_current_manifest_sha256(package),
    )
    followup = _load_followup(output)
    promotion = followup["failure_promotion_inputs"]
    assert promotion["status"] == "PENDING_EXTERNAL"
    assert promotion["candidate_count"] == len(campaign.CAMPAIGN_PLAN)
    assert all(
        candidate["disposition"] == "METADATA_ONLY_NO_PIXELS"
        and candidate["replay_candidate"] is False
        and candidate["sanitized_raw_gzip"] is None
        and candidate["promotion_complete"] is False
        for candidate in promotion["candidates"]
    )
    assert followup["c2_envelope_review_inputs"]["all_cases_source_owned"] is True
    assert followup["authority"]["promotion_allowed"] is False


def test_prepare_followup_injected_failures_are_never_release_candidates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    package = _export_compact_public_package(monkeypatch, tmp_path / "source")
    output = tmp_path / "injected-followup.json"
    result = campaign.prepare_release_followup_inputs(
        package,
        output,
        expected_manifest_sha256=_current_manifest_sha256(package),
    )

    followup = _load_followup(output)
    promotion = followup["failure_promotion_inputs"]
    assert promotion["status"] == "BLOCKED_NON_RELEASE_EVIDENCE"
    assert promotion["candidate_count"] == 0
    assert promotion["candidates"] == []
    assert promotion["nonrelease_evidence_count"] == len(campaign.CAMPAIGN_PLAN)
    assert all(
        item["disposition"] == "NON_RELEASE_TEST_EVIDENCE"
        and item["source_owned_release_evidence"] is False
        and item["promotion_complete"] is False
        and "target_dataset_id" not in item
        and "sanitized_raw_gzip" not in item
        for item in promotion["nonrelease_evidence"]
    )
    assert followup["c2_envelope_review_inputs"]["all_cases_source_owned"] is False
    assert followup["c2_envelope_review_inputs"][
        "source_owned_failure_case_ids"
    ] == []
    assert followup["c2_envelope_review_inputs"][
        "nonrelease_failure_case_ids"
    ] == [case.case_id for case in campaign.CAMPAIGN_PLAN]
    assert followup["authority"]["promotion_allowed"] is False
    verified = campaign.verify_release_followup_inputs(
        output,
        expected_sha256=cast(str, result["sha256"]),
    )
    assert verified["failure_candidate_count"] == 0


def test_prepare_followup_preserves_dpi_window_facts_without_approving_envelope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = 0

    def staged_environment() -> campaign.CaptureEnvironment:
        nonlocal calls
        dpi_values: tuple[int | None, ...] = (None, 120, 144, 96)
        dpi = dpi_values[calls] if calls < len(dpi_values) else 96
        window_class = "AltRuneLite" if calls == 1 else "SunAwtFrame"
        calls += 1
        return replace(
            _compact_environment(),
            reported_dpi=dpi,
            window_class=window_class,
        )

    package = _export_compact_public_package(
        monkeypatch,
        tmp_path / "package-source",
        source_owned=True,
        environment_provider=staged_environment,
    )
    output = tmp_path / "followup.json"
    campaign.prepare_release_followup_inputs(
        package,
        output,
        expected_manifest_sha256=_current_manifest_sha256(package),
    )
    envelope = _load_followup(output)["c2_envelope_review_inputs"]
    assert envelope["observed_reported_dpis"] == [None, 120, 144, 96]
    assert envelope["observed_window_classes"] == ["AltRuneLite", "SunAwtFrame"]
    assert envelope["window_class_consistent"] is False
    assert envelope["all_cases_match_required_dpi"] is False
    assert envelope["all_cases_match_required_frame"] is False
    assert envelope["renderer_identity"] == {
        "observed": False,
        "status": "NOT_OBSERVED_BY_CAPTURE_BACKEND",
        "requires_external_review": True,
    }
    assert envelope["envelope_approved"] is False
    assert envelope["retained_failure_case_ids"] == [
        case.case_id for case in campaign.CAMPAIGN_PLAN[:3]
    ]


def _replay_promotion_inputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    source_owned: bool = True,
    pixels_withheld: bool = False,
) -> tuple[Path, Path, str, str]:
    if pixels_withheld:
        package = _export_withheld_public_package(
            monkeypatch,
            tmp_path / "package-source",
            source_owned=source_owned,
        )
    else:
        first = campaign.CAMPAIGN_PLAN[0]
        wrong = _decision_for(
            first,
            meaning=campaign.ReviewMeaning.UNSUPPORTED_LOCATION,
            states=(ResourceVisualState.UNCERTAIN,) * 4,
        )
        package = _export_compact_public_package(
            monkeypatch,
            tmp_path / "package-source",
            source_owned=source_owned,
            review_override={first.case_id: wrong},
        )
    package_sha = _current_manifest_sha256(package)
    followup = tmp_path / "followup.json"
    result = campaign.prepare_release_followup_inputs(
        package,
        followup,
        expected_manifest_sha256=package_sha,
    )
    return package, followup, package_sha, cast(str, result["sha256"])


def test_replay_promotion_prepares_exact_source_owned_retained_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    package, followup, package_sha, followup_sha = _replay_promotion_inputs(
        monkeypatch, tmp_path
    )
    output = tmp_path / "proposals"
    result = replay_promotion.prepare_replay_promotion_proposals(
        followup,
        package,
        output,
        expected_followup_sha256=followup_sha,
        expected_package_manifest_sha256=package_sha,
    )

    assert result["proposal_count"] == 1
    assert result["metadata_only_count"] == 0
    assert result["adopted"] is False
    assert result["promotion_allowed"] is False
    assert result["activation_allowed"] is False
    manifest = json.loads((output / "proposal-manifest.json").read_text("utf-8"))
    assert manifest["preparation_id"] == (
        "resource-release-replay-promotion-preparation-v1"
    )
    assert manifest["source"]["followup_sha256"] == followup_sha
    assert manifest["source"]["package_manifest_sha256"] == package_sha
    assert manifest["selection"]["caller_selected_case_ids"] == []
    assert manifest["selection"]["preparable_case_ids"] == [
        campaign.CAMPAIGN_PLAN[0].case_id
    ]
    assert manifest["authority"] == {
        "proposal_only": True,
        "adopted": False,
        "permanent_regression": False,
        "approval_authority": False,
        "promotion_allowed": False,
        "release_eligible": False,
        "activation_allowed": False,
        "input_authority": False,
    }
    entry = manifest["proposals"][0]
    copied = output / entry["gzip_path"]
    package_manifest = json.loads((package / "manifest.json").read_text("utf-8"))
    source_case = package / package_manifest["cases"][0]["case_review_path"]
    source_gzip = source_case.parent / "sanitized-frame.raw.gz"
    assert copied.read_bytes() == source_gzip.read_bytes()
    assert hashlib.sha256(copied.read_bytes()).hexdigest() == entry["gzip_sha256"]
    assert all(
        b"private-title" not in path.read_bytes()
        for path in output.rglob("*")
        if path.is_file()
    )
    verified = replay_promotion.verify_replay_promotion_proposals(
        output,
        expected_manifest_sha256=cast(str, result["manifest_sha256"]),
    )
    assert verified["verified"] is True
    assert verified["proposal_count"] == 1
    assert verified["adopted"] is False


def test_replay_promotion_all_pass_creates_no_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    package = _export_compact_public_package(
        monkeypatch,
        tmp_path / "package-source",
        source_owned=True,
    )
    package_sha = _current_manifest_sha256(package)
    followup = tmp_path / "followup.json"
    followup_result = campaign.prepare_release_followup_inputs(
        package,
        followup,
        expected_manifest_sha256=package_sha,
    )
    output = tmp_path / "must-not-exist"

    with pytest.raises(campaign.CampaignError, match="retained failure"):
        replay_promotion.prepare_replay_promotion_proposals(
            followup,
            package,
            output,
            expected_followup_sha256=cast(str, followup_result["sha256"]),
            expected_package_manifest_sha256=package_sha,
        )
    assert not output.exists()


@pytest.mark.parametrize("pixels_withheld", (False, True))
def test_replay_promotion_excludes_injected_and_metadata_only_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    pixels_withheld: bool,
) -> None:
    package, followup, package_sha, followup_sha = _replay_promotion_inputs(
        monkeypatch,
        tmp_path,
        source_owned=pixels_withheld,
        pixels_withheld=pixels_withheld,
    )
    output = tmp_path / "proposals"
    result = replay_promotion.prepare_replay_promotion_proposals(
        followup,
        package,
        output,
        expected_followup_sha256=followup_sha,
        expected_package_manifest_sha256=package_sha,
    )
    assert result["proposal_count"] == 0
    assert result["metadata_only_count"] == (
        len(campaign.CAMPAIGN_PLAN) if pixels_withheld else 0
    )
    manifest = json.loads((output / "proposal-manifest.json").read_text("utf-8"))
    assert manifest["proposals"] == []
    assert manifest["selection"]["preparable_case_ids"] == []
    assert manifest["authority"]["promotion_allowed"] is False


def test_replay_promotion_requires_both_independently_retained_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    package, followup, package_sha, followup_sha = _replay_promotion_inputs(
        monkeypatch, tmp_path
    )
    for name, expected_followup, expected_package in (
        ("followup", "f" * 64, package_sha),
        ("package", followup_sha, "e" * 64),
    ):
        output = tmp_path / f"reject-{name}"
        with pytest.raises(campaign.CampaignIntegrityError):
            replay_promotion.prepare_replay_promotion_proposals(
                followup,
                package,
                output,
                expected_followup_sha256=expected_followup,
                expected_package_manifest_sha256=expected_package,
            )
        assert not output.exists()


def test_replay_promotion_output_must_not_overlap_either_rooted_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    package, followup, package_sha, followup_sha = _replay_promotion_inputs(
        monkeypatch, tmp_path
    )
    for output in (package / "proposals", followup.parent):
        with pytest.raises(campaign.CampaignError, match="separate"):
            replay_promotion.prepare_replay_promotion_proposals(
                followup,
                package,
                output,
                expected_followup_sha256=followup_sha,
                expected_package_manifest_sha256=package_sha,
            )


@pytest.mark.parametrize("tamper", ("authority", "candidate"))
def test_rehashed_replay_proposal_cannot_gain_authority_or_replace_truth(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tamper: str,
) -> None:
    package, followup, package_sha, followup_sha = _replay_promotion_inputs(
        monkeypatch, tmp_path
    )
    output = tmp_path / "proposals"
    replay_promotion.prepare_replay_promotion_proposals(
        followup,
        package,
        output,
        expected_followup_sha256=followup_sha,
        expected_package_manifest_sha256=package_sha,
    )
    manifest_path = output / "proposal-manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    proposal_path = output / manifest["proposals"][0]["proposal_path"]

    def mutate(payload: dict[str, object]) -> None:
        if tamper == "authority":
            authority = cast(dict[str, object], payload["authority"])
            authority["promotion_allowed"] = True
        else:
            evaluator = cast(dict[str, object], payload["evaluator_input"])
            evaluator["reviewer_meaning"] = "supported-startup"

    proposal_sha = _rewrite_hashed_json(proposal_path, mutate)

    def rebind_manifest(payload: dict[str, object]) -> None:
        proposals = cast(list[dict[str, object]], payload["proposals"])
        proposals[0]["proposal_sha256"] = proposal_sha

    manifest_sha = _rewrite_hashed_json(manifest_path, rebind_manifest)
    expected = "identity/authority" if tamper == "authority" else "source binding"
    with pytest.raises(campaign.CampaignIntegrityError, match=expected):
        replay_promotion.verify_replay_promotion_proposals(
            output,
            expected_manifest_sha256=manifest_sha,
        )


def test_concurrent_replay_promotion_writers_preserve_exact_winner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    package, followup, package_sha, followup_sha = _replay_promotion_inputs(
        monkeypatch, tmp_path
    )
    output = tmp_path / "proposals"
    barrier = threading.Barrier(3)
    successes: list[dict[str, object]] = []
    failures: list[BaseException] = []

    def writer() -> None:
        barrier.wait()
        try:
            successes.append(
                replay_promotion.prepare_replay_promotion_proposals(
                    followup,
                    package,
                    output,
                    expected_followup_sha256=followup_sha,
                    expected_package_manifest_sha256=package_sha,
                )
            )
        except BaseException as exc:  # noqa: BLE001 - adversarial writer result
            failures.append(exc)

    threads = [threading.Thread(target=writer) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], FileExistsError)
    replay_promotion.verify_replay_promotion_proposals(
        output,
        expected_manifest_sha256=cast(str, successes[0]["manifest_sha256"]),
    )


def _prepared_replay_proposals(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Path, Path, Path, dict[str, object]]:
    package, followup, package_sha, followup_sha = _replay_promotion_inputs(
        monkeypatch, tmp_path
    )
    output = tmp_path / "proposals"
    result = replay_promotion.prepare_replay_promotion_proposals(
        followup,
        package,
        output,
        expected_followup_sha256=followup_sha,
        expected_package_manifest_sha256=package_sha,
    )
    return package, followup, output, result


def test_replay_promotion_uses_verified_snapshots_after_both_sources_change(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    package, followup, package_sha, followup_sha = _replay_promotion_inputs(
        monkeypatch, tmp_path
    )
    package_manifest = json.loads((package / "manifest.json").read_text("utf-8"))
    source_case = package / package_manifest["cases"][0]["case_review_path"]
    source_gzip = source_case.parent / "sanitized-frame.raw.gz"
    rooted_gzip = source_gzip.read_bytes()
    real_loader = replay_promotion._load_snapshots

    def load_then_mutate_sources(
        followup_path: Path,
        package_dir: Path,
        *,
        expected_followup_sha256: str,
        expected_package_manifest_sha256: str,
    ) -> object:
        snapshots = real_loader(
            followup_path,
            package_dir,
            expected_followup_sha256=expected_followup_sha256,
            expected_package_manifest_sha256=expected_package_manifest_sha256,
        )
        followup.write_text("changed after verified snapshot\n", encoding="utf-8")
        source_gzip.write_bytes(b"changed after verified snapshot")
        return snapshots

    monkeypatch.setattr(replay_promotion, "_load_snapshots", load_then_mutate_sources)
    output = tmp_path / "proposals"
    result = replay_promotion.prepare_replay_promotion_proposals(
        followup,
        package,
        output,
        expected_followup_sha256=followup_sha,
        expected_package_manifest_sha256=package_sha,
    )
    manifest = json.loads((output / "proposal-manifest.json").read_text("utf-8"))
    copied = output / manifest["proposals"][0]["gzip_path"]
    assert copied.read_bytes() == rooted_gzip
    replay_promotion.verify_replay_promotion_proposals(
        output,
        expected_manifest_sha256=cast(str, result["manifest_sha256"]),
    )


@pytest.mark.parametrize("mutation", ("noncanonical", "corrupt", "oversized"))
def test_replay_proposal_rejects_noncanonical_corrupt_or_oversized_gzip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: str,
) -> None:
    _, _, output, _ = _prepared_replay_proposals(monkeypatch, tmp_path)
    manifest_path = output / "proposal-manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    entry = manifest["proposals"][0]
    gzip_path = output / entry["gzip_path"]
    if mutation == "noncanonical":
        pixels = gzip.decompress(gzip_path.read_bytes())
        replacement = gzip.compress(pixels, compresslevel=1, mtime=1)
    elif mutation == "corrupt":
        replacement = b"not-a-gzip-stream"
    else:
        replacement = b"x" * ((1005 * 1078 * 4) + 4097)
    gzip_path.write_bytes(replacement)
    gzip_sha = hashlib.sha256(replacement).hexdigest()
    gzip_path.with_name(f"{gzip_path.name}.sha256").write_text(
        f"{gzip_sha}\n", encoding="ascii"
    )
    proposal_path = output / entry["proposal_path"]

    def rebind_proposal(payload: dict[str, object]) -> None:
        fixture = cast(dict[str, object], payload["fixture_input"])
        frame = cast(dict[str, object], fixture["frame"])
        frame["gzip_sha256"] = gzip_sha

    proposal_sha = _rewrite_hashed_json(proposal_path, rebind_proposal)

    def rebind_manifest(payload: dict[str, object]) -> None:
        proposal = cast(list[dict[str, object]], payload["proposals"])[0]
        proposal["gzip_sha256"] = gzip_sha
        proposal["proposal_sha256"] = proposal_sha

    manifest_sha = _rewrite_hashed_json(manifest_path, rebind_manifest)
    with pytest.raises(campaign.CampaignIntegrityError):
        replay_promotion.verify_replay_promotion_proposals(
            output, expected_manifest_sha256=manifest_sha
        )


@pytest.mark.parametrize("mutation", ("duplicate", "omitted"))
def test_replay_proposal_manifest_rejects_duplicate_or_omitted_entry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: str,
) -> None:
    _, _, output, _ = _prepared_replay_proposals(monkeypatch, tmp_path)
    manifest_path = output / "proposal-manifest.json"

    def mutate(payload: dict[str, object]) -> None:
        proposals = cast(list[dict[str, object]], payload["proposals"])
        if mutation == "duplicate":
            proposals.append(dict(proposals[0]))
        else:
            proposals.clear()

    manifest_sha = _rewrite_hashed_json(manifest_path, mutate)
    with pytest.raises(campaign.CampaignIntegrityError):
        replay_promotion.verify_replay_promotion_proposals(
            output, expected_manifest_sha256=manifest_sha
        )


def test_replay_proposal_manifest_rejects_reordered_entries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cases = campaign.CAMPAIGN_PLAN[:2]
    overrides = {
        case.case_id: replace(
            _decision_for(
                case,
                meaning=campaign.ReviewMeaning.UNSUPPORTED_LOCATION,
                states=(ResourceVisualState.UNCERTAIN,) * 4,
            ),
            focal_resource_id=None,
            node_phase=None,
        )
        for case in cases
    }
    package = _export_compact_public_package(
        monkeypatch,
        tmp_path / "package-source",
        source_owned=True,
        review_override=overrides,
    )
    package_sha = _current_manifest_sha256(package)
    followup = tmp_path / "followup.json"
    followup_result = campaign.prepare_release_followup_inputs(
        package, followup, expected_manifest_sha256=package_sha
    )
    output = tmp_path / "proposals"
    replay_promotion.prepare_replay_promotion_proposals(
        followup,
        package,
        output,
        expected_followup_sha256=cast(str, followup_result["sha256"]),
        expected_package_manifest_sha256=package_sha,
    )
    manifest_path = output / "proposal-manifest.json"

    def reverse(payload: dict[str, object]) -> None:
        proposals = cast(list[dict[str, object]], payload["proposals"])
        proposals.reverse()

    manifest_sha = _rewrite_hashed_json(manifest_path, reverse)
    with pytest.raises(campaign.CampaignIntegrityError):
        replay_promotion.verify_replay_promotion_proposals(
            output, expected_manifest_sha256=manifest_sha
        )


@pytest.mark.parametrize("foreign_kind", ("file", "symlink", "sidecar"))
def test_replay_proposal_rejects_foreign_symlink_or_mutated_sidecar(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    foreign_kind: str,
) -> None:
    _, _, output, result = _prepared_replay_proposals(monkeypatch, tmp_path)
    if foreign_kind == "file":
        (output / "foreign.txt").write_text("foreign\n", encoding="utf-8")
    elif foreign_kind == "symlink":
        link = output / "foreign-link"
        link.write_text("simulated-link\n", encoding="utf-8")
        real_is_symlink = Path.is_symlink
        monkeypatch.setattr(
            Path,
            "is_symlink",
            lambda path: path == link or real_is_symlink(path),
        )
    else:
        (output / "proposal-manifest.json.sha256").write_text(
            f"{'0' * 64}\n", encoding="ascii"
        )
    with pytest.raises(campaign.CampaignIntegrityError):
        replay_promotion.verify_replay_promotion_proposals(
            output,
            expected_manifest_sha256=cast(str, result["manifest_sha256"]),
        )


@pytest.mark.parametrize("mutation", ("raw-hash", "reason", "review-hash"))
def test_rehashed_replay_proposal_rejects_candidate_binding_substitution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: str,
) -> None:
    _, _, output, _ = _prepared_replay_proposals(monkeypatch, tmp_path)
    manifest_path = output / "proposal-manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    entry = manifest["proposals"][0]
    proposal_path = output / entry["proposal_path"]

    def mutate(payload: dict[str, object]) -> None:
        if mutation == "reason":
            evaluator = cast(dict[str, object], payload["evaluator_input"])
            evaluator["current_release_reasons"] = ["replacement"]
            return
        bindings = cast(dict[str, object], payload["source_bindings"])
        if mutation == "review-hash":
            bindings["followup_case_review_sha256"] = "d" * 64
        else:
            hashes = cast(dict[str, object], bindings["case_hashes"])
            hashes["sanitized_raw_gzip_sha256"] = "d" * 64

    proposal_sha = _rewrite_hashed_json(proposal_path, mutate)

    def rebind(payload: dict[str, object]) -> None:
        proposal = cast(list[dict[str, object]], payload["proposals"])[0]
        proposal["proposal_sha256"] = proposal_sha

    manifest_sha = _rewrite_hashed_json(manifest_path, rebind)
    with pytest.raises(campaign.CampaignIntegrityError, match="source binding"):
        replay_promotion.verify_replay_promotion_proposals(
            output, expected_manifest_sha256=manifest_sha
        )


def test_replay_proposal_cli_has_no_selection_adoption_or_policy_overrides() -> None:
    parser = campaign_cli.build_parser()
    forbidden = {
        "--case-id",
        "--select",
        "--adopt",
        "--approve",
        "--promotion-allowed",
        "--threshold",
        "--quorum",
        "--zones",
        "--detector",
        "--profile",
    }
    actions = next(
        action for action in parser._actions if action.dest == "command"
    ).choices["prepare-replay-proposals"]._actions
    option_strings = {option for action in actions for option in action.option_strings}
    assert forbidden.isdisjoint(option_strings)


@pytest.mark.parametrize(
    "projection",
    ("repository", "reviewer-timestamp", "gzip-hash-chain"),
)
def test_valid_followup_root_must_still_match_full_rooted_package_projection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    projection: str,
) -> None:
    package, followup, package_sha, _ = _replay_promotion_inputs(
        monkeypatch, tmp_path
    )

    def forge(payload: dict[str, object]) -> None:
        source = cast(dict[str, object], payload["source_snapshot"])
        bindings = cast(list[dict[str, object]], payload["case_bindings"])
        promotion = cast(dict[str, object], payload["failure_promotion_inputs"])
        candidates = cast(list[dict[str, object]], promotion["candidates"])
        if projection == "repository":
            repository = cast(dict[str, object], source["repository"])
            repository["branch"] = "forged-but-valid-branch"
        elif projection == "reviewer-timestamp":
            truth = cast(dict[str, object], bindings[0]["reviewer_truth"])
            candidate_truth = cast(dict[str, object], candidates[0]["reviewer_truth"])
            truth["reviewed_at_utc"] = "2026-08-31T12:02:02Z"
            candidate_truth["reviewed_at_utc"] = "2026-08-31T12:02:02Z"
        else:
            hashes = cast(dict[str, object], bindings[0]["hashes"])
            artifacts = cast(dict[str, object], bindings[0]["sanitized_artifacts"])
            raw = cast(dict[str, object], artifacts["raw_gzip"])
            release = cast(dict[str, object], bindings[0]["release_result"])
            replay_candidate = cast(
                dict[str, object], release["replay_regression_candidate"]
            )
            candidate_raw = cast(dict[str, object], candidates[0]["sanitized_raw_gzip"])
            hashes["sanitized_raw_gzip_sha256"] = "d" * 64
            raw["sha256"] = "d" * 64
            replay_candidate["sha256"] = "d" * 64
            candidate_raw["sha256"] = "d" * 64

    forged_followup_sha = _rewrite_hashed_json(followup, forge)
    output = tmp_path / "must-not-exist"
    with pytest.raises(campaign.CampaignIntegrityError, match="exact projection"):
        replay_promotion.prepare_replay_promotion_proposals(
            followup,
            package,
            output,
            expected_followup_sha256=forged_followup_sha,
            expected_package_manifest_sha256=package_sha,
        )
    assert not output.exists()


def test_rehashed_embedded_followup_rejects_nested_foreign_release_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, _, output, _ = _prepared_replay_proposals(monkeypatch, tmp_path)
    manifest_path = output / "proposal-manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    embedded = output / manifest["source"]["followup_path"]

    def add_authority(payload: dict[str, object]) -> None:
        bindings = cast(list[dict[str, object]], payload["case_bindings"])
        result = cast(dict[str, object], bindings[0]["release_result"])
        result["input_authority"] = True

    embedded_sha = _rewrite_hashed_json(embedded, add_authority)

    def rebind_manifest(payload: dict[str, object]) -> None:
        source = cast(dict[str, object], payload["source"])
        source["followup_sha256"] = embedded_sha

    manifest_sha = _rewrite_hashed_json(manifest_path, rebind_manifest)
    with pytest.raises(campaign.CampaignIntegrityError):
        replay_promotion.verify_replay_promotion_proposals(
            output, expected_manifest_sha256=manifest_sha
        )


def test_rehashed_selection_cannot_relabel_passing_case_as_preparable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, _, output, _ = _prepared_replay_proposals(monkeypatch, tmp_path)
    manifest_path = output / "proposal-manifest.json"

    def relabel(payload: dict[str, object]) -> None:
        selection = cast(dict[str, object], payload["selection"])
        passing_case = campaign.CAMPAIGN_PLAN[1].case_id
        preparable = cast(list[str], selection["preparable_case_ids"])
        preparable.append(passing_case)

    manifest_sha = _rewrite_hashed_json(manifest_path, relabel)
    with pytest.raises(campaign.CampaignIntegrityError, match="selection"):
        replay_promotion.verify_replay_promotion_proposals(
            output, expected_manifest_sha256=manifest_sha
        )


def test_replay_verifier_requires_fixed_ui_opacity_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, _, output, result = _prepared_replay_proposals(monkeypatch, tmp_path)
    calls = 0

    def reject_nonopaque(_frame: Frame) -> None:
        nonlocal calls
        calls += 1
        raise campaign.CampaignIntegrityError("fixed UI pixels are not opaque")

    monkeypatch.setattr(campaign, "_verify_opaque_fixed_ui", reject_nonopaque)
    with pytest.raises(campaign.CampaignIntegrityError, match="fixed UI pixels"):
        replay_promotion.verify_replay_promotion_proposals(
            output,
            expected_manifest_sha256=cast(str, result["manifest_sha256"]),
        )
    assert calls == 1


def test_replay_verifier_rejects_proposal_root_symlink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, _, output, result = _prepared_replay_proposals(monkeypatch, tmp_path)
    real_is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda path: path == output or real_is_symlink(path),
    )
    with pytest.raises(campaign.CampaignIntegrityError, match="real directory"):
        replay_promotion.verify_replay_promotion_proposals(
            output,
            expected_manifest_sha256=cast(str, result["manifest_sha256"]),
        )


def test_rehashed_proposal_rejects_boolean_schema_version(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, _, output, _ = _prepared_replay_proposals(monkeypatch, tmp_path)
    manifest_path = output / "proposal-manifest.json"
    manifest_sha = _rewrite_hashed_json(
        manifest_path,
        lambda payload: payload.__setitem__("schema_version", True),
    )
    with pytest.raises(campaign.CampaignIntegrityError, match="identity"):
        replay_promotion.verify_replay_promotion_proposals(
            output, expected_manifest_sha256=manifest_sha
        )


def test_replay_proposal_outputs_are_byte_identical_and_do_not_mutate_dataset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    package, followup, package_sha, followup_sha = _replay_promotion_inputs(
        monkeypatch, tmp_path
    )
    dataset = tmp_path / "varrock-east-iron-release-regressions-v1"
    dataset.mkdir()
    sentinel = dataset / "owned-existing-fixture"
    sentinel.write_bytes(b"must remain exact")
    outputs = (tmp_path / "proposals-a", tmp_path / "proposals-b")
    results = [
        replay_promotion.prepare_replay_promotion_proposals(
            followup,
            package,
            output,
            expected_followup_sha256=followup_sha,
            expected_package_manifest_sha256=package_sha,
        )
        for output in outputs
    ]
    first_files = {
        path.relative_to(outputs[0]).as_posix(): path.read_bytes()
        for path in outputs[0].rglob("*")
        if path.is_file()
    }
    second_files = {
        path.relative_to(outputs[1]).as_posix(): path.read_bytes()
        for path in outputs[1].rglob("*")
        if path.is_file()
    }
    assert first_files == second_files
    assert results[0]["manifest_sha256"] == results[1]["manifest_sha256"]
    assert sentinel.read_bytes() == b"must remain exact"
    assert list(dataset.iterdir()) == [sentinel]


def test_rehashed_embedded_followup_cannot_rebind_decompressed_pixel_chain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, _, output, _ = _prepared_replay_proposals(monkeypatch, tmp_path)
    manifest_path = output / "proposal-manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    embedded = output / manifest["source"]["followup_path"]

    def replace_decompressed_hash(payload: dict[str, object]) -> None:
        bindings = cast(list[dict[str, object]], payload["case_bindings"])
        artifacts = cast(dict[str, object], bindings[0]["sanitized_artifacts"])
        binding_raw = cast(dict[str, object], artifacts["raw_gzip"])
        promotion = cast(dict[str, object], payload["failure_promotion_inputs"])
        candidates = cast(list[dict[str, object]], promotion["candidates"])
        candidate_raw = cast(dict[str, object], candidates[0]["sanitized_raw_gzip"])
        binding_raw["decompressed_sha256"] = "d" * 64
        candidate_raw["decompressed_sha256"] = "d" * 64

    embedded_sha = _rewrite_hashed_json(embedded, replace_decompressed_hash)

    def rebind_manifest(payload: dict[str, object]) -> None:
        source = cast(dict[str, object], payload["source"])
        source["followup_sha256"] = embedded_sha

    manifest_sha = _rewrite_hashed_json(manifest_path, rebind_manifest)
    with pytest.raises(
        campaign.CampaignIntegrityError,
        match="compressed/decompressed|pixel chain|source binding",
    ):
        replay_promotion.verify_replay_promotion_proposals(
            output,
            expected_manifest_sha256=manifest_sha,
        )


def test_prepare_followup_requires_retained_root_and_uses_verified_snapshot_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    package = _export_compact_public_package(monkeypatch, tmp_path / "source")
    manifest_sha = _current_manifest_sha256(package)
    rejected_output = tmp_path / "rejected.json"
    with pytest.raises(
        campaign.CampaignIntegrityError,
        match="independently retained SHA-256",
    ):
        campaign.prepare_release_followup_inputs(
            package,
            rejected_output,
            expected_manifest_sha256="0" * 64,
        )
    assert not rejected_output.exists()
    assert not rejected_output.with_name(f"{rejected_output.name}.sha256").exists()

    inside_output = package / "followup.json"
    with pytest.raises(campaign.CampaignError, match="outside the verified package"):
        campaign.prepare_release_followup_inputs(
            package,
            inside_output,
            expected_manifest_sha256=manifest_sha,
        )
    assert not inside_output.exists()
    assert not inside_output.with_name(f"{inside_output.name}.sha256").exists()

    original_loader = campaign._load_verified_review_package_snapshot

    def load_then_replace_source(
        package_dir: Path,
        *,
        expected_manifest_sha256: str,
    ) -> object:
        snapshot = original_loader(
            package_dir,
            expected_manifest_sha256=expected_manifest_sha256,
        )
        (package / "manifest.json").write_text("replaced after snapshot\n")
        return snapshot

    monkeypatch.setattr(
        campaign,
        "_load_verified_review_package_snapshot",
        load_then_replace_source,
    )
    output = tmp_path / "snapshot-followup.json"
    campaign.prepare_release_followup_inputs(
        package,
        output,
        expected_manifest_sha256=manifest_sha,
    )
    followup = _load_followup(output)
    assert followup["source_snapshot"]["manifest_sha256"] == manifest_sha
    assert followup["verification"]["case_count"] == 15


def _first_public_case_path(destination: Path) -> Path:
    manifest = json.loads(
        (destination / "manifest.json").read_text(encoding="utf-8")
    )
    return destination / manifest["cases"][0]["case_review_path"]


def _rebind_first_public_case(destination: Path, case_sha256: str) -> None:
    def rebind_manifest(payload: dict[str, object]) -> None:
        cases = cast(list[dict[str, object]], payload["cases"])
        cases[0]["case_review_sha256"] = case_sha256

    _rewrite_hashed_json(destination / "manifest.json", rebind_manifest)


def test_public_withheld_authority_tampering_rejects_after_full_hash_rebind(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    destination = _export_withheld_public_package(monkeypatch, tmp_path)
    case_path = _first_public_case_path(destination)

    def promote_withheld_authority(payload: dict[str, object]) -> None:
        production = cast(dict[str, object], payload["production"])
        authority = cast(dict[str, object], production["production_authority"])
        trust = cast(dict[str, object], authority["trust"])
        trust["accepted"] = True

    rebound_case_sha = _rewrite_hashed_json(case_path, promote_withheld_authority)
    _rebind_first_public_case(destination, rebound_case_sha)

    with pytest.raises(campaign.CampaignIntegrityError, match="withheld-pixel"):
        _verify_review_package(destination)


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("window_client_width", True),
        ("window_client_height", False),
        ("window_class", "   "),
    ),
)
def test_public_environment_rejects_boolean_dimensions_and_blank_class(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    field: str,
    replacement: object,
) -> None:
    destination = _export_compact_public_package(monkeypatch, tmp_path)
    case_path = _first_public_case_path(destination)

    def corrupt_environment(payload: dict[str, object]) -> None:
        environment = cast(dict[str, object], payload["capture_environment"])
        environment[field] = replacement

    rebound_case_sha = _rewrite_hashed_json(case_path, corrupt_environment)
    _rebind_first_public_case(destination, rebound_case_sha)

    with pytest.raises(
        campaign.CampaignIntegrityError,
        match="public environment provenance",
    ):
        _verify_review_package(destination)


def test_public_review_preparation_source_binding_rebind_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    destination = _export_compact_public_package(monkeypatch, tmp_path)
    case_path = _first_public_case_path(destination)

    def replace_preparation_binding(payload: dict[str, object]) -> None:
        bindings = cast(dict[str, object], payload["source_bindings"])
        bindings["review_preparation_sha256"] = "c" * 64

    rebound_case_sha = _rewrite_hashed_json(case_path, replace_preparation_binding)
    _rebind_first_public_case(destination, rebound_case_sha)

    with pytest.raises(campaign.CampaignIntegrityError, match="source bindings"):
        _verify_review_package(destination)


@pytest.mark.parametrize("oversized_part", ("artifact", "sidecar"))
def test_oversized_public_artifact_and_sidecar_are_bounded_and_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    oversized_part: str,
) -> None:
    destination = _export_compact_public_package(monkeypatch, tmp_path)
    case_path = _first_public_case_path(destination)
    oversized = b"x" * (campaign._MAX_PUBLIC_JSON_BYTES + 1)

    if oversized_part == "artifact":
        digest = hashlib.sha256(oversized).hexdigest()
        case_path.write_bytes(oversized)
        case_path.with_name(f"{case_path.name}.sha256").write_text(
            f"{digest}\n",
            encoding="ascii",
        )
        _rebind_first_public_case(destination, digest)
        expected = "campaign artifact exceeds"
    else:
        case_path.with_name(f"{case_path.name}.sha256").write_bytes(oversized)
        expected = "SHA-256 sidecar"

    with pytest.raises(campaign.CampaignIntegrityError, match=expected):
        _verify_review_package(destination)


def test_invalid_generic_provenance_capability_rejects_before_capture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    backend = FakeCaptureBackend([_small_raw()], name="windows-runelite")
    environment_calls = 0

    def environment_provider() -> campaign.CaptureEnvironment:
        nonlocal environment_calls
        environment_calls += 1
        return _environment()

    monkeypatch.setattr(campaign, "LIVE_RESOURCE_CAMPAIGN_AUTHORIZED", True)
    with CaptureSource(backend, clock=ManualClock(1.0)) as source:
        with pytest.raises(
            campaign.CampaignIntegrityError,
            match="source-owned boundary capability",
        ):
            campaign._capture_next_with_source(
                session,
                source,
                repository=_repository(),
                environment_provider=environment_provider,
                provenance_capability=object(),
                expected_case_id=campaign.CAMPAIGN_PLAN[0].case_id,
                captured_at_utc=_CREATED + timedelta(seconds=1),
            )

    assert backend.grab_calls == 0
    assert environment_calls == 0


def test_deterministic_gzip_has_frozen_canonical_bytes() -> None:
    payload = b"resource-release-canonical-gzip-v1\x00\xff" * 3
    expected = bytes.fromhex(
        "1f8b08000000000002ff2b4a2dce2f2d4a4ed52d4acd494d2c4ed54d4ecccbcf"
        "cb4c4eccd14dafca2cd02d3364f85f44253500f5e512726c000000"
    )

    first = campaign._deterministic_gzip(payload)
    second = campaign._deterministic_gzip(payload)

    assert first == second == expected
    assert first[4:8] == b"\x00\x00\x00\x00"
    assert first[9] == 0xFF
    assert gzip.decompress(first) == payload
    assert hashlib.sha256(first).hexdigest() == (
        "8e231acbb12962830a43f4463ee9ac73af67e9f0a73a6d20cb6b7ca0bb53054e"
    )


def _resource_release_decision_inputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    source_owned: bool = True,
) -> tuple[Path, Path, Path, str, str, str]:
    package, followup, package_sha, followup_sha = _replay_promotion_inputs(
        monkeypatch,
        tmp_path,
        source_owned=source_owned,
    )
    proposal = tmp_path / "replay-proposals"
    proposal_result = replay_promotion.prepare_replay_promotion_proposals(
        followup,
        package,
        proposal,
        expected_followup_sha256=followup_sha,
        expected_package_manifest_sha256=package_sha,
    )
    return (
        package,
        followup,
        proposal,
        package_sha,
        followup_sha,
        cast(str, proposal_result["manifest_sha256"]),
    )


def _copy_hashed_artifact(source: Path, destination: Path) -> None:
    destination.write_bytes(source.read_bytes())
    destination.with_name(f"{destination.name}.sha256").write_bytes(
        source.with_name(f"{source.name}.sha256").read_bytes()
    )


def test_release_decision_binds_all_roots_and_stays_proposal_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (
        package,
        followup,
        proposal,
        package_sha,
        followup_sha,
        proposal_sha,
    ) = _resource_release_decision_inputs(monkeypatch, tmp_path)
    output = tmp_path / "release-decision.json"
    result = release_decision.prepare_resource_release_decision(
        followup,
        package,
        output,
        expected_followup_sha256=followup_sha,
        expected_package_manifest_sha256=package_sha,
        proposal_dir=proposal,
        expected_proposal_manifest_sha256=proposal_sha,
    )

    assert result["review_packet_prepared"] is True
    assert result["review_package_manifest_sha256"] == package_sha
    assert result["followup_sha256"] == followup_sha
    assert result["proposal_manifest_sha256"] == proposal_sha
    assert result["release_eligible"] is False
    artifact = _load_followup(output)
    assert artifact["source_evidence"]["review_package_manifest_sha256"] == (
        package_sha
    )
    checks = artifact["input_checks"]
    assert checks["accepted_a1_packaging_checkpoint"] == {
        "status": "ACCEPTED_OFFLINE_NONACTIVATING",
        "pull_request": 49,
        "head_sha": "86090c93046ce584652f11fce1c49d59b5988754",
    }
    assert checks["permanent_replay_adoption_status"] == (
        "PROPOSALS_ONLY_NOT_ADOPTED"
    )
    envelope = artifact["candidate_envelope"]
    assert envelope["renderer"] == {
        "identity": None,
        "review_status": "PENDING_EXTERNAL_RENDERER_REVIEW",
        "capture_backend_observed_identity": False,
        "caller_may_assert_identity": False,
    }
    assert envelope["approved"] is False
    record = artifact["proposed_source_release_record"]
    assert record["status"] == "PROPOSED_NOT_GRANTED"
    assert record["replay_promotion"]["permanent_regression"] is False
    assert record["source_binding_plan"]["binding_complete"] is False
    assert record["source_binding_plan"]["provided_git_bindings"] == []
    assert "promoted_failure_payload_blobs" in record["source_binding_plan"][
        "required_exact_git_bindings"
    ]
    assert record["authority"] == artifact["authority"]
    assert all(value is False for key, value in artifact["authority"].items() if key != "proposal_only")
    assert artifact["authority"]["proposal_only"] is True
    condition_ids = [
        item["condition_id"] for item in artifact["unresolved_conditions"]
    ]
    assert "external-release-evidence-boundary-acceptance" not in condition_ids
    assert "permanent-replay-source-adoption" in condition_ids
    assert "exact-client-renderer-profile-envelope-review" in condition_ids
    assert "final-lead-release-decision" in condition_ids

    verified = release_decision.verify_resource_release_decision(
        output,
        expected_sha256=cast(str, result["sha256"]),
    )
    assert verified["packet_integrity_verified"] is True
    assert verified["review_packet_prepared"] is True
    assert verified["release_eligible"] is False
    assert verified["activation_allowed"] is False


def test_release_decision_all_pass_has_no_replay_adoption_blocker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    package = _export_compact_public_package(
        monkeypatch,
        tmp_path / "package-source",
        source_owned=True,
    )
    package_sha = _current_manifest_sha256(package)
    followup = tmp_path / "followup.json"
    followup_result = campaign.prepare_release_followup_inputs(
        package,
        followup,
        expected_manifest_sha256=package_sha,
    )
    followup_sha = cast(str, followup_result["sha256"])
    first = tmp_path / "decision-a.json"
    second = tmp_path / "decision-b.json"
    first_result = release_decision.prepare_resource_release_decision(
        followup,
        package,
        first,
        expected_followup_sha256=followup_sha,
        expected_package_manifest_sha256=package_sha,
    )
    second_result = release_decision.prepare_resource_release_decision(
        followup,
        package,
        second,
        expected_followup_sha256=followup_sha,
        expected_package_manifest_sha256=package_sha,
    )

    assert first.read_bytes() == second.read_bytes()
    assert first_result["sha256"] == second_result["sha256"]
    artifact = _load_followup(first)
    assert artifact["input_checks"]["c1_reported_closed"] is True
    assert artifact["input_checks"]["permanent_replay_adoption_status"] == (
        "NOT_REQUIRED"
    )
    assert artifact["source_evidence"]["replay_proposal_manifest"] is None
    assert artifact["proposed_source_release_record"]["replay_promotion"] == {
        "status": "NOT_REQUIRED",
        "retained_failure_case_ids": [],
        "source_owned_failure_case_ids": [],
        "nonrelease_failure_case_ids": [],
        "preparable_case_ids": [],
        "metadata_only_case_ids": [],
        "excluded_nonrelease_case_ids": [],
        "preparable_status": "NOT_PRESENT",
        "metadata_only_status": "NOT_PRESENT",
        "nonrelease_status": "NOT_PRESENT",
        "proposal_manifest_sha256": None,
        "adopted_fixture_git_blobs": [],
        "permanent_regression": False,
    }
    required_bindings = artifact["proposed_source_release_record"][
        "source_binding_plan"
    ]["required_exact_git_bindings"]
    assert "promoted_failure_payload_blobs" not in required_bindings
    assert "promoted_failure_evaluator_test_blobs" not in required_bindings
    assert "permanent-replay-source-adoption" not in {
        item["condition_id"] for item in artifact["unresolved_conditions"]
    }
    assert artifact["authority"]["release_eligible"] is False

    with pytest.raises(campaign.CampaignIntegrityError):
        release_decision.prepare_resource_release_decision(
            followup,
            package,
            tmp_path / "wrong-package-root.json",
            expected_followup_sha256=followup_sha,
            expected_package_manifest_sha256="f" * 64,
        )


def test_release_decision_accepts_exact_nonrelease_exclusion_partition(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (
        package,
        followup,
        proposal,
        package_sha,
        followup_sha,
        proposal_sha,
    ) = _resource_release_decision_inputs(
        monkeypatch,
        tmp_path,
        source_owned=False,
    )
    output = tmp_path / "nonrelease-decision.json"
    result = release_decision.prepare_resource_release_decision(
        followup,
        package,
        output,
        expected_followup_sha256=followup_sha,
        expected_package_manifest_sha256=package_sha,
        proposal_dir=proposal,
        expected_proposal_manifest_sha256=proposal_sha,
    )
    artifact = _load_followup(output)
    embedded = artifact["source_evidence"]["replay_proposal_manifest"]
    selection = embedded["selection"]
    assert selection["preparable_case_ids"] == []
    assert selection["metadata_only_case_ids"] == []
    assert selection["excluded_nonrelease_case_ids"] == [
        case.case_id for case in campaign.CAMPAIGN_PLAN
    ]
    assert artifact["input_checks"]["all_cases_source_owned"] is False
    envelope = artifact["candidate_envelope"]
    assert envelope["qualifying_evidence_complete"] is False
    assert envelope["candidate_reported_dpi"] is None
    assert envelope["candidate_client_geometry"] is None
    assert envelope["candidate_window_class"] is None
    assert envelope["candidate_capture_backend"] is None
    assert artifact["input_checks"]["permanent_replay_adoption_status"] == (
        "NOT_REQUIRED"
    )
    assert artifact["input_checks"]["metadata_only_replay_status"] == "NOT_PRESENT"
    assert artifact["input_checks"]["nonrelease_evidence_status"] == (
        "EXCLUDED_FROM_RELEASE"
    )
    assert "permanent-replay-source-adoption" not in {
        item["condition_id"] for item in artifact["unresolved_conditions"]
    }
    assert release_decision.verify_resource_release_decision(
        output,
        expected_sha256=cast(str, result["sha256"]),
    )["packet_integrity_verified"] is True


def test_release_decision_preserves_nonqualifying_envelope_facts_without_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = 0

    def staged_raw(value: int) -> RawFrame:
        width = 125 if value == 3 else 124
        pixel = bytes((value & 0xFF, 0, 0, 255))
        return RawFrame(
            payload=pixel * (width * 104),
            width=width,
            height=104,
            pixel_format=PixelFormat.BGRA8888,
        )

    def staged_environment() -> campaign.CaptureEnvironment:
        nonlocal calls
        index = calls
        calls += 1
        environment = _compact_environment()
        if index == 0:
            return replace(environment, reported_dpi=None)
        if index == 1:
            return replace(
                environment,
                reported_dpi=120,
                window_class="AltRuneLite",
            )
        if index == 2:
            return replace(environment, window_client_width=125)
        return environment

    package = _export_withheld_public_package(
        monkeypatch,
        tmp_path / "package-source",
        source_owned=True,
        environment_provider=staged_environment,
        raw_factory=staged_raw,
    )
    package_sha = _current_manifest_sha256(package)
    followup = tmp_path / "followup.json"
    followup_result = campaign.prepare_release_followup_inputs(
        package,
        followup,
        expected_manifest_sha256=package_sha,
    )
    proposal = tmp_path / "replay-proposals"
    proposal_result = replay_promotion.prepare_replay_promotion_proposals(
        followup,
        package,
        proposal,
        expected_followup_sha256=cast(str, followup_result["sha256"]),
        expected_package_manifest_sha256=package_sha,
    )
    output = tmp_path / "release-readiness.json"
    result = release_decision.prepare_resource_release_decision(
        followup,
        package,
        output,
        expected_followup_sha256=cast(str, followup_result["sha256"]),
        expected_package_manifest_sha256=package_sha,
        proposal_dir=proposal,
        expected_proposal_manifest_sha256=cast(
            str, proposal_result["manifest_sha256"]
        ),
    )

    artifact = _load_followup(output)
    envelope = artifact["candidate_envelope"]
    assert envelope["observed_reported_dpis"] == [None, 120, 96]
    assert envelope["observed_client_geometries"] == [
        {"width": 124, "height": 104},
        {"width": 125, "height": 104},
    ]
    assert envelope["observed_window_classes"] == [
        "AltRuneLite",
        "SunAwtFrame",
    ]
    assert envelope["qualifying_evidence_complete"] is False
    assert envelope["candidate_reported_dpi"] is None
    assert envelope["candidate_client_geometry"] is None
    assert envelope["candidate_window_class"] is None
    assert envelope["candidate_capture_backend"] is None
    condition_ids = {
        item["condition_id"] for item in artifact["unresolved_conditions"]
    }
    assert {
        "reported-dpi-96",
        "window-class-consistency",
        "exact-client-geometry",
    } <= condition_ids
    assert artifact["authority"]["release_eligible"] is False
    assert artifact["authority"]["activation_allowed"] is False
    assert release_decision.verify_resource_release_decision(
        output,
        expected_sha256=cast(str, result["sha256"]),
    )["packet_integrity_verified"] is True


def test_release_decision_keeps_metadata_only_failures_unpreparable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    package, followup, package_sha, followup_sha = _replay_promotion_inputs(
        monkeypatch,
        tmp_path,
        source_owned=True,
        pixels_withheld=True,
    )
    proposal = tmp_path / "metadata-only-proposals"
    proposal_result = replay_promotion.prepare_replay_promotion_proposals(
        followup,
        package,
        proposal,
        expected_followup_sha256=followup_sha,
        expected_package_manifest_sha256=package_sha,
    )
    output = tmp_path / "metadata-only-decision.json"
    result = release_decision.prepare_resource_release_decision(
        followup,
        package,
        output,
        expected_followup_sha256=followup_sha,
        expected_package_manifest_sha256=package_sha,
        proposal_dir=proposal,
        expected_proposal_manifest_sha256=cast(
            str, proposal_result["manifest_sha256"]
        ),
    )
    artifact = _load_followup(output)
    checks = artifact["input_checks"]
    assert checks["permanent_replay_adoption_status"] == "NOT_REQUIRED"
    assert checks["metadata_only_replay_status"] == "UNPREPARABLE_NO_PIXELS"
    assert checks["nonrelease_evidence_status"] == "NOT_PRESENT"
    replay = artifact["proposed_source_release_record"]["replay_promotion"]
    assert replay["preparable_case_ids"] == []
    assert replay["metadata_only_case_ids"] == [
        case.case_id for case in campaign.CAMPAIGN_PLAN
    ]
    assert replay["preparable_status"] == "NOT_PRESENT"
    assert replay["metadata_only_status"] == "UNPREPARABLE_NO_PIXELS"
    assert "promoted_failure_payload_blobs" not in artifact[
        "proposed_source_release_record"
    ]["source_binding_plan"]["required_exact_git_bindings"]
    condition_ids = {
        item["condition_id"] for item in artifact["unresolved_conditions"]
    }
    assert "metadata-only-retained-failure-resolution" in condition_ids
    assert "permanent-replay-source-adoption" not in condition_ids
    assert release_decision.verify_resource_release_decision(
        output,
        expected_sha256=cast(str, result["sha256"]),
    )["packet_integrity_verified"] is True


def test_release_decision_rejects_rehashed_authority_and_review_tampering(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (
        package,
        followup,
        proposal,
        package_sha,
        followup_sha,
        proposal_sha,
    ) = _resource_release_decision_inputs(monkeypatch, tmp_path)
    source = tmp_path / "decision.json"
    release_decision.prepare_resource_release_decision(
        followup,
        package,
        source,
        expected_followup_sha256=followup_sha,
        expected_package_manifest_sha256=package_sha,
        proposal_dir=proposal,
        expected_proposal_manifest_sha256=proposal_sha,
    )

    def grant_authority(payload: dict[str, object]) -> None:
        cast(dict[str, object], payload["authority"])["release_eligible"] = True

    def infer_renderer(payload: dict[str, object]) -> None:
        envelope = cast(dict[str, object], payload["candidate_envelope"])
        renderer = cast(dict[str, object], envelope["renderer"])
        renderer["identity"] = "inferred-opengl"
        renderer["review_status"] = "APPROVED"

    def remove_condition(payload: dict[str, object]) -> None:
        conditions = cast(list[dict[str, object]], payload["unresolved_conditions"])
        conditions.pop()

    def forge_git_binding(payload: dict[str, object]) -> None:
        record = cast(dict[str, object], payload["proposed_source_release_record"])
        plan = cast(dict[str, object], record["source_binding_plan"])
        plan["status"] = "COMPLETE"
        plan["provided_git_bindings"] = ["forged"]
        plan["binding_complete"] = True

    def adopt_embedded_proposal(payload: dict[str, object]) -> None:
        evidence = cast(dict[str, object], payload["source_evidence"])
        manifest = cast(dict[str, object], evidence["replay_proposal_manifest"])
        authority = cast(dict[str, object], manifest["authority"])
        authority["adopted"] = True
        rebind_embedded_proposal(payload)

    def rebind_embedded_proposal(payload: dict[str, object]) -> None:
        evidence = cast(dict[str, object], payload["source_evidence"])
        manifest = cast(dict[str, object], evidence["replay_proposal_manifest"])
        digest = hashlib.sha256(_canonical_json_bytes(manifest)).hexdigest()
        evidence["replay_proposal_manifest_sha256"] = digest
        record = cast(dict[str, object], payload["proposed_source_release_record"])
        lineage = cast(dict[str, object], record["lineage"])
        lineage["replay_proposal_manifest_sha256"] = digest
        promotion = cast(dict[str, object], record["replay_promotion"])
        promotion["proposal_manifest_sha256"] = digest

    def rebind_embedded_followup_and_proposal(
        payload: dict[str, object],
    ) -> None:
        evidence = cast(dict[str, object], payload["source_evidence"])
        followup_inputs = cast(dict[str, object], evidence["followup_inputs"])
        digest = hashlib.sha256(_canonical_json_bytes(followup_inputs)).hexdigest()
        evidence["followup_sha256"] = digest
        manifest = cast(dict[str, object], evidence["replay_proposal_manifest"])
        manifest_source = cast(dict[str, object], manifest["source"])
        manifest_source["followup_sha256"] = digest
        record = cast(dict[str, object], payload["proposed_source_release_record"])
        lineage = cast(dict[str, object], record["lineage"])
        lineage["followup_sha256"] = digest
        rebind_embedded_proposal(payload)

    def replace_proposal_path(payload: dict[str, object]) -> None:
        evidence = cast(dict[str, object], payload["source_evidence"])
        manifest = cast(dict[str, object], evidence["replay_proposal_manifest"])
        entries = cast(list[dict[str, object]], manifest["proposals"])
        entries[0]["proposal_path"] = "cases/01-forged/proposal.json"
        rebind_embedded_proposal(payload)

    def replace_proposal_hash(payload: dict[str, object]) -> None:
        evidence = cast(dict[str, object], payload["source_evidence"])
        manifest = cast(dict[str, object], evidence["replay_proposal_manifest"])
        entries = cast(list[dict[str, object]], manifest["proposals"])
        entries[0]["proposal_sha256"] = "0" * 64
        rebind_embedded_proposal(payload)

    def replace_proposal_ordinal(payload: dict[str, object]) -> None:
        evidence = cast(dict[str, object], payload["source_evidence"])
        manifest = cast(dict[str, object], evidence["replay_proposal_manifest"])
        entries = cast(list[dict[str, object]], manifest["proposals"])
        entries[0]["ordinal"] = 99
        rebind_embedded_proposal(payload)

    def duplicate_proposal(payload: dict[str, object]) -> None:
        evidence = cast(dict[str, object], payload["source_evidence"])
        manifest = cast(dict[str, object], evidence["replay_proposal_manifest"])
        entries = cast(list[dict[str, object]], manifest["proposals"])
        entries.append(dict(entries[0]))
        rebind_embedded_proposal(payload)

    def remove_decompressed_binding(payload: dict[str, object]) -> None:
        evidence = cast(dict[str, object], payload["source_evidence"])
        followup_inputs = cast(dict[str, object], evidence["followup_inputs"])
        promotion = cast(
            dict[str, object], followup_inputs["failure_promotion_inputs"]
        )
        candidates = cast(list[dict[str, object]], promotion["candidates"])
        candidate_raw = cast(
            dict[str, object], candidates[0]["sanitized_raw_gzip"]
        )
        candidate_raw.pop("decompressed_sha256")
        bindings = cast(list[dict[str, object]], followup_inputs["case_bindings"])
        artifacts = cast(dict[str, object], bindings[0]["sanitized_artifacts"])
        binding_raw = cast(dict[str, object], artifacts["raw_gzip"])
        binding_raw.pop("decompressed_sha256")
        rebind_embedded_followup_and_proposal(payload)

    def replace_source_raw_path(payload: dict[str, object]) -> None:
        evidence = cast(dict[str, object], payload["source_evidence"])
        followup_inputs = cast(dict[str, object], evidence["followup_inputs"])
        promotion = cast(
            dict[str, object], followup_inputs["failure_promotion_inputs"]
        )
        candidates = cast(list[dict[str, object]], promotion["candidates"])
        candidate_raw = cast(
            dict[str, object], candidates[0]["sanitized_raw_gzip"]
        )
        candidate_raw["path"] = "../foreign.raw.gz"
        bindings = cast(list[dict[str, object]], followup_inputs["case_bindings"])
        artifacts = cast(dict[str, object], bindings[0]["sanitized_artifacts"])
        binding_raw = cast(dict[str, object], artifacts["raw_gzip"])
        binding_raw["path"] = "../foreign.raw.gz"
        rebind_embedded_followup_and_proposal(payload)

    def boolean_schema(payload: dict[str, object]) -> None:
        payload["schema_version"] = True

    def replace_embedded_profile_field(
        payload: dict[str, object],
        field: str,
        replacement: object,
    ) -> None:
        evidence = cast(dict[str, object], payload["source_evidence"])
        followup_inputs = cast(dict[str, object], evidence["followup_inputs"])
        source_snapshot = cast(
            dict[str, object], followup_inputs["source_snapshot"]
        )
        profile = cast(dict[str, object], source_snapshot["profile"])
        profile[field] = replacement
        rebind_embedded_followup_and_proposal(payload)

    def replace_detector_identity(payload: dict[str, object]) -> None:
        replace_embedded_profile_field(
            payload,
            "detector_version",
            "foreign-version",
        )

    def replace_profile_identity(payload: dict[str, object]) -> None:
        replace_embedded_profile_field(payload, "profile_id", "foreign-profile")

    def replace_profile_schema(payload: dict[str, object]) -> None:
        replace_embedded_profile_field(payload, "profile_schema_version", 2)

    def replace_location_identity(payload: dict[str, object]) -> None:
        replace_embedded_profile_field(payload, "location_id", "foreign-location")

    for name, mutation in (
        ("authority", grant_authority),
        ("renderer", infer_renderer),
        ("condition", remove_condition),
        ("git-binding", forge_git_binding),
        ("proposal-adoption", adopt_embedded_proposal),
        ("proposal-path", replace_proposal_path),
        ("proposal-hash", replace_proposal_hash),
        ("proposal-ordinal", replace_proposal_ordinal),
        ("proposal-duplicate", duplicate_proposal),
        ("missing-decompressed-binding", remove_decompressed_binding),
        ("foreign-source-raw-path", replace_source_raw_path),
        ("boolean-schema", boolean_schema),
        ("detector-identity", replace_detector_identity),
        ("profile-identity", replace_profile_identity),
        ("profile-schema", replace_profile_schema),
        ("location-identity", replace_location_identity),
    ):
        forged = tmp_path / f"forged-{name}.json"
        _copy_hashed_artifact(source, forged)
        forged_sha = _rewrite_hashed_json(forged, mutation)
        with pytest.raises(campaign.CampaignIntegrityError):
            release_decision.verify_resource_release_decision(
                forged,
                expected_sha256=forged_sha,
            )

    forged_followup = tmp_path / "forged-followup.json"
    _copy_hashed_artifact(followup, forged_followup)

    def replace_capture_branch(payload: dict[str, object]) -> None:
        snapshot = cast(dict[str, object], payload["source_snapshot"])
        repository = cast(dict[str, object], snapshot["repository"])
        repository["branch"] = "forged/other-session"

    forged_followup_sha = _rewrite_hashed_json(
        forged_followup,
        replace_capture_branch,
    )
    with pytest.raises(
        campaign.CampaignIntegrityError,
        match="rooted follow-up does not match",
    ):
        release_decision.prepare_resource_release_decision(
            forged_followup,
            package,
            tmp_path / "cross-root.json",
            expected_followup_sha256=forged_followup_sha,
            expected_package_manifest_sha256=package_sha,
            proposal_dir=proposal,
            expected_proposal_manifest_sha256=proposal_sha,
        )


def test_release_decision_requires_conditional_proposal_and_separate_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (
        package,
        followup,
        proposal,
        package_sha,
        followup_sha,
        proposal_sha,
    ) = _resource_release_decision_inputs(monkeypatch, tmp_path)
    with pytest.raises(campaign.CampaignIntegrityError, match="retained failures"):
        release_decision.prepare_resource_release_decision(
            followup,
            package,
            tmp_path / "missing-proposal.json",
            expected_followup_sha256=followup_sha,
            expected_package_manifest_sha256=package_sha,
        )
    with pytest.raises(campaign.CampaignError, match="supplied together"):
        release_decision.prepare_resource_release_decision(
            followup,
            package,
            tmp_path / "half-proposal.json",
            expected_followup_sha256=followup_sha,
            expected_package_manifest_sha256=package_sha,
            proposal_dir=proposal,
        )
    for output in (
        followup,
        package / "decision.json",
        proposal / "decision.json",
    ):
        with pytest.raises(campaign.CampaignError, match="separate"):
            release_decision.prepare_resource_release_decision(
                followup,
                package,
                output,
                expected_followup_sha256=followup_sha,
                expected_package_manifest_sha256=package_sha,
                proposal_dir=proposal,
                expected_proposal_manifest_sha256=proposal_sha,
            )


def test_release_decision_cli_exposes_no_approval_or_envelope_overrides() -> None:
    parser = campaign_cli.build_parser()
    prepare = next(
        action
        for action in parser._actions
        if isinstance(action, campaign_cli.argparse._SubParsersAction)
    ).choices["prepare-release-decision-readiness"]
    destinations = {action.dest for action in prepare._actions}
    assert destinations == {
        "help",
        "followup",
        "expected_followup_sha256",
        "package",
        "expected_package_manifest_sha256",
        "proposal",
        "expected_proposal_manifest_sha256",
        "output",
    }
    assert not destinations & {
        "approve",
        "renderer",
        "dpi",
        "geometry",
        "detector",
        "profile",
        "release_eligible",
        "activation_allowed",
    }


def test_release_decision_uses_frozen_snapshots_and_preserves_concurrent_winner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (
        package,
        followup,
        proposal,
        package_sha,
        followup_sha,
        proposal_sha,
    ) = _resource_release_decision_inputs(monkeypatch, tmp_path)
    followup_snapshot = campaign._load_verified_followup_snapshot(
        followup,
        expected_sha256=followup_sha,
    )
    package_snapshot = campaign._load_verified_review_package_snapshot(
        package,
        expected_manifest_sha256=package_sha,
    )
    proposal_snapshot = release_decision._load_proposal_snapshot(
        proposal,
        expected_manifest_sha256=proposal_sha,
        followup=followup_snapshot.inputs,
        expected_followup_sha256=followup_sha,
    )
    mutated = False

    def frozen_followup(
        path: Path,
        *,
        expected_sha256: str,
    ) -> object:
        nonlocal mutated
        del path, expected_sha256
        if not mutated:
            mutated = True
            followup.write_text("replaced after snapshot", encoding="utf-8")
            (package / "manifest.json").write_text(
                "replaced after snapshot",
                encoding="utf-8",
            )
        return followup_snapshot

    def frozen_package(
        path: Path,
        *,
        expected_manifest_sha256: str,
    ) -> object:
        del path, expected_manifest_sha256
        return package_snapshot

    def frozen_proposal(
        proposal_dir: Path | None,
        *,
        expected_manifest_sha256: str | None,
        followup: Mapping[str, object],
        expected_followup_sha256: str,
    ) -> object:
        del (
            proposal_dir,
            expected_manifest_sha256,
            followup,
            expected_followup_sha256,
        )
        return proposal_snapshot

    monkeypatch.setattr(
        release_decision.campaign,
        "_load_verified_followup_snapshot",
        frozen_followup,
    )
    monkeypatch.setattr(
        release_decision.campaign,
        "_load_verified_review_package_snapshot",
        frozen_package,
    )
    monkeypatch.setattr(
        release_decision,
        "_load_proposal_snapshot",
        frozen_proposal,
    )
    output = tmp_path / "concurrent-decision.json"
    barrier = threading.Barrier(3)
    successes: list[dict[str, object]] = []
    failures: list[BaseException] = []

    def writer() -> None:
        barrier.wait()
        try:
            successes.append(
                release_decision.prepare_resource_release_decision(
                    followup,
                    package,
                    output,
                    expected_followup_sha256=followup_sha,
                    expected_package_manifest_sha256=package_sha,
                    proposal_dir=proposal,
                    expected_proposal_manifest_sha256=proposal_sha,
                )
            )
        except BaseException as exc:  # noqa: BLE001 - adversarial writer result
            failures.append(exc)

    threads = [threading.Thread(target=writer) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    assert mutated is True
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], FileExistsError)
    winning_bytes = output.read_bytes()
    winning_sidecar = output.with_name(f"{output.name}.sha256").read_bytes()
    with pytest.raises(FileExistsError):
        release_decision.prepare_resource_release_decision(
            followup,
            package,
            output,
            expected_followup_sha256=followup_sha,
            expected_package_manifest_sha256=package_sha,
            proposal_dir=proposal,
            expected_proposal_manifest_sha256=proposal_sha,
        )
    assert output.read_bytes() == winning_bytes
    assert output.with_name(f"{output.name}.sha256").read_bytes() == winning_sidecar
    assert release_decision.verify_resource_release_decision(
        output,
        expected_sha256=cast(str, successes[0]["sha256"]),
    )["packet_integrity_verified"] is True


def test_hashed_artifact_sidecar_failure_never_unlinks_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "owned.json"
    replacement = b"concurrent-winner-must-survive"
    real_exclusive_write = campaign._exclusive_write
    identity_calls = 0

    def distinct_identity(value: object) -> tuple[int, int, int, int, int]:
        nonlocal identity_calls
        del value
        identity_calls += 1
        return (1, identity_calls, identity_calls, identity_calls, identity_calls)

    def replace_before_sidecar(
        path: Path,
        payload: bytes,
    ) -> tuple[int, int] | None:
        if path == output:
            return real_exclusive_write(path, payload)
        output.unlink()
        output.write_bytes(replacement)
        raise FileExistsError("concurrent sidecar winner")

    monkeypatch.setattr(campaign, "_identity_from_stat", distinct_identity)
    monkeypatch.setattr(campaign, "_exclusive_write", replace_before_sidecar)
    with pytest.raises(FileExistsError, match="concurrent sidecar winner"):
        campaign._write_hashed_artifact(output, b"this invocation owned these bytes")

    assert identity_calls == 2
    assert output.read_bytes() == replacement
    assert not output.with_name(f"{output.name}.sha256").exists()


def test_hashed_artifact_success_path_detects_and_preserves_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "owned.json"
    replacement = b"replacement-before-successful-sidecar"
    real_exclusive_write = campaign._exclusive_write

    def replace_then_write_sidecar(
        path: Path,
        payload: bytes,
    ) -> tuple[int, int] | None:
        if path == output:
            return real_exclusive_write(path, payload)
        output.unlink()
        output.write_bytes(replacement)
        return real_exclusive_write(path, payload)

    monkeypatch.setattr(campaign, "_exclusive_write", replace_then_write_sidecar)
    with pytest.raises(
        campaign.CampaignIntegrityError,
        match="changed during exclusive publication",
    ):
        campaign._write_hashed_artifact(output, b"owned-publication")

    assert output.read_bytes() == replacement
    assert not output.with_name(f"{output.name}.sha256").exists()


def test_hashed_artifact_verifier_rejects_trailing_sidecar_bytes(
    tmp_path: Path,
) -> None:
    output = tmp_path / "artifact.json"
    digest = campaign._write_hashed_artifact(output, b"{}\n")
    sidecar = output.with_name(f"{output.name}.sha256")
    sidecar.write_bytes(f"{digest}\r\nforeign".encode("ascii"))

    with pytest.raises(campaign.CampaignIntegrityError, match="sidecar size"):
        campaign._verify_hashed_artifact(output, expected=digest)
