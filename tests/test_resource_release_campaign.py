from __future__ import annotations

import gzip
import hashlib
import inspect
import json
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
    monkeypatch.setattr(campaign, "_profile_identity", lambda: identity)


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
            "frame": None,
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
            "landmarks": [],
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
    session = _capture_and_seal_small_campaign(monkeypatch, tmp_path)
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
    )
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


def test_exact_source_owned_fixture_closes_only_evidence_blockers(
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
    assert report["release_eligible"] is False


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
    assert campaign.verify_review_package(destination)["verified"] is True

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
        campaign.verify_review_package(destination)


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
    verified = campaign.verify_review_package(destination)
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
        campaign.verify_review_package(destination)

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
        campaign.verify_review_package(destination)


def test_failed_reviewed_case_remains_packaged_as_nonactivating_regression(
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
    assert campaign.verify_review_package(destination)["verified"] is True


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
) -> Path:
    session = _capture_and_seal_small_campaign(
        monkeypatch,
        tmp_path / "source",
        raw_factory=_compact_raw,
        environment_provider=_compact_environment,
    )
    monkeypatch.setattr(campaign, "VARROCK_EAST_IRON_FIXED_UI_REGIONS", ())
    _review_all(monkeypatch, session)
    destination = tmp_path / "public-review"
    campaign.export_review_package(
        session,
        destination,
        repository=_repository(),
        exported_at_utc=_CREATED + timedelta(minutes=4),
    )
    _enable_tiny_verifier_profile(monkeypatch)
    assert campaign.verify_review_package(destination)["verified"] is True
    return destination


def _export_withheld_public_package(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Path:
    session = _capture_and_seal_small_campaign(
        monkeypatch,
        tmp_path / "source",
        raw_factory=_compact_raw,
        environment_provider=_compact_environment,
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
    assert campaign.verify_review_package(destination)["verified"] is True
    return destination


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
        campaign.verify_review_package(destination)


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
        campaign.verify_review_package(destination)


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
        campaign.verify_review_package(destination)


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
        campaign.verify_review_package(destination)


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
