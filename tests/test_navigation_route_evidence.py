from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256

import pytest

import mining_automation.navigation.route_evidence as route_evidence_module
from mining_automation.capture.frame import PixelFormat
from mining_automation.contracts import FrameRef
from mining_automation.navigation.contracts import (
    Checkpoint,
    CheckpointDetection,
    CheckpointDetectorIdentity,
    CheckpointMatchKind,
    CheckpointProfileIdentity,
    CheckpointRole,
    RouteDirection,
    RouteEndpoint,
    RouteEndpointRole,
    RouteIdentity,
    RoutePlan,
    RouteStep,
    Sha256Digest,
)
from mining_automation.navigation.route_evidence import (
    SYNTHETIC_ROUTE_EVIDENCE_ROLE,
    FinalizedRouteEvidencePackage,
    OwnedRouteEvidenceCase,
    RouteEndpointVerification,
    RouteEvidenceAcquisitionBinding,
    RouteEvidenceArtifactRef,
    RouteEvidenceCampaignPlan,
    RouteEvidenceCaptureBuildIdentity,
    RouteEvidenceCaseRole,
    RouteEvidenceCaseSpec,
    RouteEvidenceCaseTruth,
    RouteEvidenceIntegrityError,
    RouteEvidenceLoadExpectation,
    RouteEvidenceOperatorIntent,
    RouteEvidenceReview,
    RouteEvidenceReviewDecision,
    RouteEvidenceVerificationReport,
    SyntheticRouteEvidenceDetectorReport,
    canonical_route_evidence_bytes,
    digest_route_plan,
    parse_synthetic_detector_report,
    verify_synthetic_route_evidence,
)


def _digest(label: str) -> Sha256Digest:
    return Sha256Digest.from_bytes(label.encode("ascii"))


def _route(direction: RouteDirection) -> RoutePlan:
    if direction is RouteDirection.MINE_TO_BANK:
        prefix = "synthetic-m2b"
        origin = RouteEndpoint("synthetic-mine", RouteEndpointRole.MINE)
        destination = RouteEndpoint("synthetic-bank", RouteEndpointRole.BANK)
    else:
        prefix = "synthetic-b2m"
        origin = RouteEndpoint("synthetic-bank", RouteEndpointRole.BANK)
        destination = RouteEndpoint("synthetic-mine", RouteEndpointRole.MINE)
    checkpoints = (
        Checkpoint(f"{prefix}-departure", CheckpointRole.DEPARTURE),
        Checkpoint(f"{prefix}-transit", CheckpointRole.TRANSIT),
        Checkpoint(f"{prefix}-arrival", CheckpointRole.ARRIVAL),
    )
    return RoutePlan(
        identity=RouteIdentity(f"{prefix}-route", "1.0.0-synthetic", direction),
        origin=origin,
        destination=destination,
        checkpoints=checkpoints,
        steps=(
            RouteStep(
                f"{prefix}-step-1",
                checkpoints[0].checkpoint_id,
                checkpoints[1].checkpoint_id,
            ),
            RouteStep(
                f"{prefix}-step-2",
                checkpoints[1].checkpoint_id,
                checkpoints[2].checkpoint_id,
            ),
        ),
    )


def _case_specs(route: RoutePlan) -> tuple[RouteEvidenceCaseSpec, ...]:
    checkpoint_ids = tuple(item.checkpoint_id for item in route.checkpoints)
    return (
        RouteEvidenceCaseSpec(
            1,
            "synthetic-departure-positive",
            RouteEvidenceCaseRole.CHECKPOINT_POSITIVE,
            checkpoint_ids[0],
        ),
        RouteEvidenceCaseSpec(
            2,
            "synthetic-transit-negative",
            RouteEvidenceCaseRole.CHECKPOINT_NEGATIVE,
            checkpoint_ids[1],
        ),
        RouteEvidenceCaseSpec(
            3,
            "synthetic-transit-positive",
            RouteEvidenceCaseRole.CHECKPOINT_POSITIVE,
            checkpoint_ids[1],
        ),
        RouteEvidenceCaseSpec(
            4,
            "synthetic-arrival-proof",
            RouteEvidenceCaseRole.ROUTE_ARRIVAL,
            checkpoint_ids[2],
        ),
    )


def _detection_for_spec(spec: RouteEvidenceCaseSpec) -> CheckpointDetection:
    if spec.role is RouteEvidenceCaseRole.CHECKPOINT_NEGATIVE:
        return CheckpointDetection(CheckpointMatchKind.UNKNOWN, (), 0.0)
    return CheckpointDetection(
        CheckpointMatchKind.MATCHED,
        (spec.checkpoint_id,),
        1.0,
    )


def _build_package(
    direction: RouteDirection = RouteDirection.MINE_TO_BANK,
    *,
    capture_configuration_label: str | None = None,
) -> tuple[
    FinalizedRouteEvidencePackage,
    RouteEvidenceReview,
    dict[str, bytes],
]:
    route = _route(direction)
    plan = RouteEvidenceCampaignPlan(
        campaign_id=f"synthetic-{direction.value}-campaign",
        route_plan=route,
        detector=CheckpointDetectorIdentity("synthetic-checkpoint-detector", "0.0.0"),
        profile=CheckpointProfileIdentity(
            "synthetic-checkpoint-profile",
            "0.0.0",
            _digest(f"{direction.value}-profile"),
        ),
        capture_source_id=f"synthetic-{direction.value}-source",
        capture_session_id=f"synthetic-{direction.value}-session",
        capture_build=RouteEvidenceCaptureBuildIdentity(
            "synthetic-passive-capture",
            "0.0.0",
            _digest("synthetic-passive-capture-build"),
        ),
        frame_width=2,
        frame_height=1,
        pixel_format=PixelFormat.BGRA8888,
        capture_configuration_sha256=_digest(
            capture_configuration_label
            if capture_configuration_label is not None
            else f"{direction.value}-capture-configuration"
        ),
        capture_environment_sha256=_digest(f"{direction.value}-environment"),
        support_envelope_sha256=_digest(f"{direction.value}-support-envelope"),
        operator_id="synthetic-operator",
        created_at_utc="2026-09-01T00:00:00Z",
        cases=_case_specs(route),
    )
    artifacts: dict[str, bytes] = {}
    owned_cases: list[OwnedRouteEvidenceCase] = []
    previous_acquisition_sha256 = plan.content_sha256
    for spec in plan.cases:
        frame_payload = bytes([spec.ordinal]) * 8
        frame_ref = FrameRef(spec.ordinal, float(spec.ordinal), 2, 1)
        frame_sha256 = Sha256Digest.from_bytes(frame_payload)
        detection = _detection_for_spec(spec)
        acquisition = RouteEvidenceAcquisitionBinding(
            campaign_plan_sha256=plan.content_sha256,
            capture_source_identity_sha256=plan.capture_source_identity_sha256,
            capture_session_id=plan.capture_session_id,
            request_id=f"synthetic-request-{spec.ordinal}",
            sequence_index=spec.ordinal,
            case_id=spec.case_id,
            capture_id=f"synthetic-capture-{spec.ordinal}",
            operator_id=plan.operator_id,
            acknowledged_monotonic_s=float(spec.ordinal) - 0.5,
            expires_monotonic_s=float(spec.ordinal) + 29.5,
            frame_captured_monotonic_s=float(spec.ordinal),
            recorded_monotonic_s=float(spec.ordinal) + 0.25,
            previous_acquisition_sha256=previous_acquisition_sha256,
        )
        detector_report = SyntheticRouteEvidenceDetectorReport(
            campaign_id=plan.campaign_id,
            campaign_plan_sha256=plan.content_sha256,
            route=plan.route,
            route_plan_sha256=plan.route_plan_sha256,
            sequence_index=spec.ordinal,
            case_id=spec.case_id,
            capture_id=f"synthetic-capture-{spec.ordinal}",
            acquisition=acquisition,
            detector=plan.detector,
            profile=plan.profile,
            capture_source_id=plan.capture_source_id,
            capture_session_id=plan.capture_session_id,
            capture_build=plan.capture_build,
            capture_configuration_sha256=plan.capture_configuration_sha256,
            capture_environment_sha256=plan.capture_environment_sha256,
            support_envelope_sha256=plan.support_envelope_sha256,
            frame_ref=frame_ref,
            pixel_format=PixelFormat.BGRA8888,
            frame_sha256=frame_sha256,
            detection=detection,
        )
        report_payload = detector_report.canonical_bytes
        frame_path = f"cases/{spec.ordinal:02d}-{spec.case_id}/frame.bgra"
        report_path = f"cases/{spec.ordinal:02d}-{spec.case_id}/detector-report.json"
        artifacts[frame_path] = frame_payload
        artifacts[report_path] = report_payload
        owned_cases.append(
            OwnedRouteEvidenceCase(
                campaign_id=plan.campaign_id,
                campaign_plan_sha256=plan.content_sha256,
                route=plan.route,
                route_plan_sha256=plan.route_plan_sha256,
                sequence_index=spec.ordinal,
                case_id=spec.case_id,
                capture_id=f"synthetic-capture-{spec.ordinal}",
                operator_id=plan.operator_id,
                operator_intent=RouteEvidenceOperatorIntent(
                    spec.case_id,
                    spec.role,
                    spec.checkpoint_id,
                ),
                acquisition=acquisition,
                detector=plan.detector,
                profile=plan.profile,
                capture_source_id=plan.capture_source_id,
                capture_session_id=plan.capture_session_id,
                capture_build=plan.capture_build,
                capture_configuration_sha256=plan.capture_configuration_sha256,
                capture_environment_sha256=plan.capture_environment_sha256,
                support_envelope_sha256=plan.support_envelope_sha256,
                captured_at_utc=f"2026-09-01T00:00:0{spec.ordinal}Z",
                frame_ref=frame_ref,
                pixel_format=PixelFormat.BGRA8888,
                frame_artifact=RouteEvidenceArtifactRef(
                    frame_path,
                    len(frame_payload),
                    frame_sha256,
                ),
                detector_report_artifact=RouteEvidenceArtifactRef(
                    report_path,
                    len(report_payload),
                    Sha256Digest.from_bytes(report_payload),
                ),
            )
        )
        previous_acquisition_sha256 = owned_cases[-1].content_sha256
    package = FinalizedRouteEvidencePackage(
        campaign_plan=plan,
        cases=tuple(owned_cases),
        finalized_at_utc="2026-09-01T00:00:10Z",
        finalized_monotonic_s=10.0,
    )
    truths: list[RouteEvidenceCaseTruth] = []
    for spec, owned in zip(plan.cases, package.cases, strict=True):
        detection = _detection_for_spec(spec)
        truths.append(
            RouteEvidenceCaseTruth(
                case_id=spec.case_id,
                frame_sha256=owned.frame_artifact.sha256,
                detector_report_sha256=owned.detector_report_artifact.sha256,
                decision=RouteEvidenceReviewDecision.APPROVED,
                detection=detection,
            )
        )
    review = RouteEvidenceReview(
        finalized_package_sha256=package.content_sha256,
        campaign_id=plan.campaign_id,
        route=plan.route,
        route_plan_sha256=plan.route_plan_sha256,
        reviewer_id="synthetic-independent-reviewer",
        reviewed_at_utc="2026-09-01T00:00:11Z",
        cases=tuple(truths),
    )
    return package, review, artifacts


def _expectation(
    package: FinalizedRouteEvidencePackage,
) -> RouteEvidenceLoadExpectation:
    plan = package.campaign_plan
    return RouteEvidenceLoadExpectation(
        finalized_package_sha256=package.content_sha256,
        acquisition_head_sha256=package.acquisition_head_sha256,
        campaign_id=plan.campaign_id,
        route=plan.route,
        direction=plan.route.direction,
        route_plan_sha256=plan.route_plan_sha256,
        detector=plan.detector,
        profile=plan.profile,
        capture_source_id=plan.capture_source_id,
        capture_session_id=plan.capture_session_id,
        capture_build=plan.capture_build,
        frame_width=plan.frame_width,
        frame_height=plan.frame_height,
        pixel_format=plan.pixel_format,
        capture_configuration_sha256=plan.capture_configuration_sha256,
        capture_environment_sha256=plan.capture_environment_sha256,
        support_envelope_sha256=plan.support_envelope_sha256,
    )


def _verify(
    package: FinalizedRouteEvidencePackage,
    review: RouteEvidenceReview,
    artifacts: dict[str, bytes],
) -> RouteEvidenceVerificationReport:
    return verify_synthetic_route_evidence(
        package,
        review,
        artifacts,
        _expectation(package),
    )


def _with_detector_report(
    package: FinalizedRouteEvidencePackage,
    review: RouteEvidenceReview,
    artifacts: dict[str, bytes],
    case_index: int,
    detector_report: SyntheticRouteEvidenceDetectorReport,
) -> tuple[
    FinalizedRouteEvidencePackage,
    RouteEvidenceReview,
    dict[str, bytes],
]:
    payload = detector_report.canonical_bytes
    changed_artifacts = dict(artifacts)
    owned_cases = list(package.cases[:case_index])
    for index in range(case_index, len(package.cases)):
        original_owned = package.cases[index]
        if index == case_index:
            report_payload = payload
            acquisition = original_owned.acquisition
        else:
            acquisition = replace(
                original_owned.acquisition,
                previous_acquisition_sha256=owned_cases[-1].content_sha256,
            )
            original_report_payload = artifacts[
                original_owned.detector_report_artifact.relative_path
            ]
            original_report = parse_synthetic_detector_report(original_report_payload)
            report_payload = replace(
                original_report,
                acquisition=acquisition,
            ).canonical_bytes
        changed_owned = replace(
            original_owned,
            acquisition=acquisition,
            detector_report_artifact=replace(
                original_owned.detector_report_artifact,
                size_bytes=len(report_payload),
                sha256=Sha256Digest.from_bytes(report_payload),
            ),
        )
        owned_cases.append(changed_owned)
        changed_artifacts[original_owned.detector_report_artifact.relative_path] = report_payload
    changed_package = replace(package, cases=tuple(owned_cases))
    truths = list(review.cases)
    for index in range(case_index, len(truths)):
        truths[index] = replace(
            truths[index],
            detector_report_sha256=owned_cases[index].detector_report_artifact.sha256,
        )
    changed_review = replace(
        review,
        finalized_package_sha256=changed_package.content_sha256,
        cases=tuple(truths),
    )
    return changed_package, changed_review, changed_artifacts


@pytest.mark.parametrize("direction", tuple(RouteDirection))
def test_synthetic_direction_package_passes_without_release_or_input(
    direction: RouteDirection,
) -> None:
    package, review, artifacts = _build_package(direction)

    report = _verify(package, review, artifacts)

    assert report.evidence_conformance_passed is True
    assert report.failure_reasons == ()
    assert report.route.direction is direction
    assert report.evidence_role == SYNTHETIC_ROUTE_EVIDENCE_ROLE
    assert report.endpoint.route_arrival_verified is True
    assert report.endpoint.supported_mining_view_proven is False
    assert report.endpoint.bank_interface_open_proven is False
    assert report.real_release_role_satisfied is False
    assert report.live_navigation_enabled is False
    assert report.activation_allowed is False
    assert report.input_authority is False


def test_canonical_digest_is_compact_sorted_ascii_with_one_lf() -> None:
    payload = canonical_route_evidence_bytes({"unicode": "café", "a": 1})

    assert payload == b'{"a":1,"unicode":"caf\\u00e9"}\n'
    assert Sha256Digest.from_bytes(payload).value == sha256(payload).hexdigest()
    with pytest.raises(ValueError, match="numbers must be finite"):
        canonical_route_evidence_bytes({"bad": float("nan")})


def test_canonical_evidence_rejects_surrogate_alias_but_accepts_scalar_unicode() -> None:
    scalar = canonical_route_evidence_bytes({"label": "\U0001f600"})
    assert b"\\ud83d\\ude00" in scalar
    with pytest.raises(ValueError, match="surrogate"):
        canonical_route_evidence_bytes({"label": "\ud83d\ude00"})


def test_route_evidence_rejects_primitive_subclass_identity_and_lineage_spoofs() -> None:
    class AdversarialStr(str):
        def __eq__(self, other: object) -> bool:
            del other
            return True

        def __ne__(self, other: object) -> bool:
            del other
            return False

        def endswith(
            self,
            suffix: str | tuple[str, ...],
            start: int | None = None,
            end: int | None = None,
        ) -> bool:
            del suffix, start, end
            return True

        __hash__ = str.__hash__

    class AdversarialInt(int):
        def __le__(self, other: object) -> bool:
            del other
            return False

    package, _, _ = _build_package()
    plan = package.campaign_plan
    with pytest.raises(ValueError, match="operator_id"):
        replace(plan, operator_id=AdversarialStr("foreign-operator"))
    with pytest.raises(ValueError, match="ordinal"):
        replace(plan.cases[0], ordinal=AdversarialInt(-99))
    with pytest.raises(ValueError, match="UTC"):
        replace(plan, created_at_utc=AdversarialStr("not-a-utc-timestamp"))
    with pytest.raises(TypeError, match="non-canonical JSON value"):
        canonical_route_evidence_bytes({"operator_id": AdversarialStr("foreign-operator")})


def test_acquisition_binding_requires_fixed_timeout_and_exact_float_times() -> None:
    package, _, _ = _build_package()
    acquisition = package.cases[0].acquisition
    with pytest.raises(ValueError, match="fixed passive capture timeout"):
        replace(acquisition, expires_monotonic_s=acquisition.expires_monotonic_s + 1.0)
    with pytest.raises(ValueError, match="exact finite"):
        replace(acquisition, recorded_monotonic_s=2**53 + 1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="exact finite"):
        replace(acquisition, acknowledged_monotonic_s=-0.0)
    large_acknowledgement = float(2**57)
    rounded_expiry = large_acknowledgement + 30.0
    with pytest.raises(ValueError, match="fixed passive capture timeout"):
        replace(
            acquisition,
            acknowledged_monotonic_s=large_acknowledgement,
            expires_monotonic_s=rounded_expiry,
            frame_captured_monotonic_s=rounded_expiry,
            recorded_monotonic_s=rounded_expiry,
        )


def test_synthetic_detector_report_round_trips_exact_canonical_contract() -> None:
    package, _, artifacts = _build_package()
    owned = package.cases[0]
    payload = artifacts[owned.detector_report_artifact.relative_path]

    report = parse_synthetic_detector_report(payload)

    assert report.canonical_bytes == payload
    assert report.campaign_plan_sha256 == package.campaign_plan.content_sha256
    assert report.route == package.route
    assert report.detector == package.campaign_plan.detector
    assert report.profile == package.campaign_plan.profile
    assert report.frame_ref == owned.frame_ref
    assert report.frame_sha256 == owned.frame_artifact.sha256
    assert report.detector_output_is_reviewer_truth is False
    assert report.activation_allowed is False
    assert report.input_authority is False


def test_evidence_boundaries_reject_integer_float_aliases_before_serialization() -> None:
    package, review, artifacts = _build_package()
    owned = package.cases[0]
    report = parse_synthetic_detector_report(
        artifacts[owned.detector_report_artifact.relative_path]
    )
    integer_frame_time = FrameRef(
        report.frame_ref.frame_id,
        int(report.frame_ref.captured_monotonic_s),
        report.frame_ref.width,
        report.frame_ref.height,
    )
    integer_confidence = replace(report.detection)
    object.__setattr__(integer_confidence, "confidence", 1)

    with pytest.raises(ValueError, match="exact finite"):
        replace(report, frame_ref=integer_frame_time)
    with pytest.raises(ValueError, match="exact finite"):
        replace(owned, frame_ref=integer_frame_time)
    with pytest.raises(ValueError, match="exact finite"):
        replace(report, detection=integer_confidence)
    with pytest.raises(ValueError, match="exact finite"):
        replace(review.cases[0], detection=integer_confidence)


def test_evidence_boundaries_reject_nonportable_embedded_identities() -> None:
    package, review, artifacts = _build_package()
    plan = package.campaign_plan
    owned = package.cases[0]
    report = parse_synthetic_detector_report(
        artifacts[owned.detector_report_artifact.relative_path]
    )
    unicode_detector = replace(plan.detector, detector_id="d\u00e9tecteur")
    unicode_profile = replace(plan.profile, profile_id="profil\u00e9")
    unicode_route = replace(plan.route, route_id="rout\u00e9")

    for field_name, foreign_value in (
        ("detector", unicode_detector),
        ("profile", unicode_profile),
    ):
        with pytest.raises(ValueError, match="portable"):
            replace(plan, **{field_name: foreign_value})
        with pytest.raises(ValueError, match="portable"):
            replace(report, **{field_name: foreign_value})
        with pytest.raises(ValueError, match="portable"):
            replace(owned, **{field_name: foreign_value})
        with pytest.raises(ValueError, match="portable"):
            replace(_expectation(package), **{field_name: foreign_value})

    with pytest.raises(ValueError, match="portable"):
        replace(plan, route_plan=replace(plan.route_plan, identity=unicode_route))
    with pytest.raises(ValueError, match="portable"):
        replace(report, route=unicode_route)
    with pytest.raises(ValueError, match="portable"):
        replace(owned, route=unicode_route)
    with pytest.raises(ValueError, match="portable"):
        replace(_expectation(package), route=unicode_route)
    with pytest.raises(ValueError, match="portable"):
        replace(review, route=unicode_route)


@pytest.mark.parametrize(
    "mutation",
    (
        "duplicate-key",
        "missing-field",
        "unknown-field",
        "noncanonical",
        "authority",
        "old-schema",
        "integer-confidence",
        "integer-frame-time",
        "unrepresentable-number",
    ),
)
def test_detector_report_parser_rejects_nonexact_or_noncanonical_json(
    mutation: str,
) -> None:
    package, _, artifacts = _build_package()
    path = package.cases[0].detector_report_artifact.relative_path
    payload = artifacts[path]
    if mutation == "duplicate-key":
        changed = payload.replace(
            b"{",
            b'{"activation_allowed":false,',
            1,
        )
    elif mutation == "noncanonical":
        changed = payload[:-1] + b" \n"
    elif mutation == "authority":
        changed = payload.replace(
            b'"activation_allowed":false',
            b'"activation_allowed":true',
            1,
        )
    elif mutation == "old-schema":
        changed = payload.replace(
            b"fixed-route-evidence-synthetic-detector-report-v2",
            b"fixed-route-evidence-synthetic-detector-report-v1",
            1,
        )
    else:
        decoded: dict[str, object] = json.loads(payload)
        if mutation == "missing-field":
            del decoded["capture_session_id"]
        elif mutation == "unrepresentable-number":
            frame = decoded["frame"]
            assert isinstance(frame, dict)
            frame["captured_monotonic_s"] = 10**400
        elif mutation == "integer-frame-time":
            frame = decoded["frame"]
            assert isinstance(frame, dict)
            frame["captured_monotonic_s"] = 1
        elif mutation == "integer-confidence":
            detection = decoded["detection"]
            assert isinstance(detection, dict)
            detection["confidence"] = 1
        else:
            decoded["foreign"] = False
        changed = canonical_route_evidence_bytes(decoded)

    with pytest.raises(RouteEvidenceIntegrityError, match="detector report"):
        parse_synthetic_detector_report(changed)


def test_verifier_requires_an_explicit_caller_load_expectation() -> None:
    package, review, artifacts = _build_package()

    with pytest.raises(TypeError):
        verify_synthetic_route_evidence(  # type: ignore[call-arg]
            package,
            review,
            artifacts,
        )


@pytest.mark.parametrize(
    ("field_name", "foreign_value"),
    (
        ("finalized_package_sha256", _digest("foreign-package")),
        ("acquisition_head_sha256", _digest("foreign-acquisition-head")),
        ("campaign_id", "foreign-campaign"),
        (
            "route",
            RouteIdentity(
                "foreign-route",
                "1.0.0-synthetic",
                RouteDirection.MINE_TO_BANK,
            ),
        ),
        ("route_plan_sha256", _digest("foreign-route-plan")),
        (
            "detector",
            CheckpointDetectorIdentity("foreign-detector", "0.0.0"),
        ),
        (
            "profile",
            CheckpointProfileIdentity(
                "foreign-profile",
                "0.0.0",
                _digest("foreign-profile"),
            ),
        ),
        ("capture_source_id", "foreign-source"),
        ("capture_session_id", "foreign-session"),
        (
            "capture_build",
            RouteEvidenceCaptureBuildIdentity(
                "foreign-capture-build",
                "0.0.1",
                _digest("foreign-capture-build"),
            ),
        ),
        ("frame_width", 3),
        ("frame_height", 2),
        ("pixel_format", PixelFormat.RGBA8888),
        ("capture_configuration_sha256", _digest("foreign-configuration")),
        ("capture_environment_sha256", _digest("foreign-environment")),
        ("support_envelope_sha256", _digest("foreign-support-envelope")),
    ),
)
def test_load_expectation_pins_every_package_authority_field(
    field_name: str,
    foreign_value: object,
) -> None:
    package, review, artifacts = _build_package()
    expectation = replace(
        _expectation(package),
        **{field_name: foreign_value},  # type: ignore[arg-type]
    )

    with pytest.raises(RouteEvidenceIntegrityError, match="load expectation"):
        verify_synthetic_route_evidence(package, review, artifacts, expectation)


def test_load_expectation_pins_direction_independently_of_route_text() -> None:
    package, review, artifacts = _build_package()
    opposite_route = _route(RouteDirection.BANK_TO_MINE).identity
    foreign = replace(
        _expectation(package),
        route=opposite_route,
        direction=RouteDirection.BANK_TO_MINE,
    )

    with pytest.raises(RouteEvidenceIntegrityError, match="load expectation"):
        verify_synthetic_route_evidence(package, review, artifacts, foreign)


def test_original_expectation_rejects_uniformly_recomputed_stale_graph() -> None:
    current, _, _ = _build_package(capture_configuration_label="current-config")
    stale, stale_review, stale_artifacts = _build_package(
        capture_configuration_label="stale-but-internally-consistent-config"
    )

    with pytest.raises(RouteEvidenceIntegrityError, match="load expectation"):
        verify_synthetic_route_evidence(
            stale,
            stale_review,
            stale_artifacts,
            _expectation(current),
        )


def test_direction_plans_and_packages_have_distinct_digests() -> None:
    mine_to_bank, _, _ = _build_package(RouteDirection.MINE_TO_BANK)
    bank_to_mine, _, _ = _build_package(RouteDirection.BANK_TO_MINE)

    assert digest_route_plan(mine_to_bank.campaign_plan.route_plan) != digest_route_plan(
        bank_to_mine.campaign_plan.route_plan
    )
    assert mine_to_bank.content_sha256 != bank_to_mine.content_sha256


def test_opposite_direction_review_cannot_be_rebound_to_package() -> None:
    mine_to_bank, _, artifacts = _build_package(RouteDirection.MINE_TO_BANK)
    _, bank_to_mine_review, _ = _build_package(RouteDirection.BANK_TO_MINE)
    forged = replace(
        bank_to_mine_review,
        finalized_package_sha256=mine_to_bank.content_sha256,
    )

    with pytest.raises(RouteEvidenceIntegrityError, match="foreign.*direction"):
        _verify(mine_to_bank, forged, artifacts)


def test_operator_intent_is_permanently_unverified_and_not_truth() -> None:
    package, _, _ = _build_package()
    intent = package.cases[0].operator_intent

    assert intent.status == "operator-intent-unverified"
    assert intent.operator_intent_is_reviewer_truth is False
    with pytest.raises(FrozenInstanceError):
        intent.operator_intent_is_reviewer_truth = True  # type: ignore[misc,assignment]


def test_operator_cannot_independently_review_own_package() -> None:
    package, review, artifacts = _build_package()
    self_review = replace(review, reviewer_id=package.campaign_plan.operator_id)

    with pytest.raises(RouteEvidenceIntegrityError, match="differ from.*operator"):
        _verify(package, self_review, artifacts)

    case_alias = replace(
        review,
        reviewer_id=package.campaign_plan.operator_id.upper(),
    )
    with pytest.raises(RouteEvidenceIntegrityError, match="differ from.*operator"):
        _verify(package, case_alias, artifacts)


def test_review_must_bind_exact_finalized_package_hash() -> None:
    package, review, artifacts = _build_package()
    foreign = replace(review, finalized_package_sha256=_digest("other-package"))

    with pytest.raises(RouteEvidenceIntegrityError, match="finalized package hash"):
        _verify(package, foreign, artifacts)


@pytest.mark.parametrize("mutation", ["missing", "foreign", "replaced"])
def test_missing_foreign_and_replaced_owned_artifacts_are_rejected(mutation: str) -> None:
    package, review, original = _build_package()
    artifacts = dict(original)
    path = package.cases[0].frame_artifact.relative_path
    if mutation == "missing":
        del artifacts[path]
        expected = "missing="
    elif mutation == "foreign":
        artifacts["cases/foreign.bin"] = b"foreign"
        expected = "foreign="
    else:
        artifacts[path] = b"x" * len(artifacts[path])
        expected = "digest changed"

    with pytest.raises(RouteEvidenceIntegrityError, match=expected):
        _verify(package, review, artifacts)


def test_review_truth_must_bind_owned_frame_and_report_hashes() -> None:
    package, review, artifacts = _build_package()
    forged_case = replace(review.cases[0], frame_sha256=_digest("foreign-frame"))
    forged_review = replace(review, cases=(forged_case, *review.cases[1:]))

    with pytest.raises(RouteEvidenceIntegrityError, match="foreign or replaced"):
        _verify(package, forged_review, artifacts)


def test_verifier_rejects_every_detector_report_identity_rebinding() -> None:
    package, review, artifacts = _build_package()
    owned = package.cases[0]
    path = owned.detector_report_artifact.relative_path
    report = parse_synthetic_detector_report(artifacts[path])
    mutations: tuple[dict[str, object], ...] = (
        {"campaign_id": "foreign-campaign"},
        {"campaign_plan_sha256": _digest("foreign-campaign-plan")},
        {
            "route": RouteIdentity(
                "foreign-route",
                "1.0.0-synthetic",
                package.route.direction,
            )
        },
        {"route_plan_sha256": _digest("foreign-route-plan")},
        {"sequence_index": report.sequence_index + 10},
        {"case_id": "foreign-case"},
        {"capture_id": "foreign-capture"},
        {"detector": CheckpointDetectorIdentity("foreign-detector", "0.0.0")},
        {
            "profile": CheckpointProfileIdentity(
                "foreign-profile",
                "0.0.0",
                _digest("foreign-profile"),
            )
        },
        {"capture_source_id": "foreign-source"},
        {"capture_session_id": "foreign-session"},
        {
            "capture_build": RouteEvidenceCaptureBuildIdentity(
                "foreign-capture-build",
                "0.0.1",
                _digest("foreign-capture-build"),
            )
        },
        {"capture_configuration_sha256": _digest("foreign-configuration")},
        {"capture_environment_sha256": _digest("foreign-environment")},
        {"support_envelope_sha256": _digest("foreign-support-envelope")},
        {
            "frame_ref": FrameRef(
                99,
                report.frame_ref.captured_monotonic_s,
                2,
                1,
            )
        },
        {"pixel_format": PixelFormat.RGBA8888},
        {"frame_sha256": _digest("foreign-frame")},
    )

    for mutation in mutations:
        acquisition_mutation: dict[str, object] = {}
        for field_name in (
            "campaign_plan_sha256",
            "sequence_index",
            "case_id",
            "capture_id",
            "capture_session_id",
        ):
            if field_name in mutation:
                acquisition_mutation[field_name] = mutation[field_name]
        foreign_report = replace(
            report,
            acquisition=replace(report.acquisition, **acquisition_mutation),  # type: ignore[arg-type]
            **mutation,  # type: ignore[arg-type]
        )
        changed_package, changed_review, changed_artifacts = _with_detector_report(
            package,
            review,
            artifacts,
            0,
            foreign_report,
        )
        with pytest.raises(RouteEvidenceIntegrityError, match="rebound or mismatched"):
            _verify(changed_package, changed_review, changed_artifacts)


def test_detector_output_disagreement_is_a_retained_conformance_failure() -> None:
    package, review, artifacts = _build_package()
    owned = package.cases[0]
    path = owned.detector_report_artifact.relative_path
    detector_report = parse_synthetic_detector_report(artifacts[path])
    changed_report = replace(
        detector_report,
        detection=CheckpointDetection(
            CheckpointMatchKind.MATCHED,
            (package.campaign_plan.route_plan.checkpoints[1].checkpoint_id,),
            1.0,
        ),
    )
    changed_package, changed_review, changed_artifacts = _with_detector_report(
        package,
        review,
        artifacts,
        0,
        changed_report,
    )

    result = _verify(changed_package, changed_review, changed_artifacts)

    assert result.evidence_conformance_passed is False
    assert result.failure_reasons == (
        "synthetic-departure-positive:detector_output_disagrees_with_reviewer_truth",
    )
    assert result.endpoint.route_arrival_verified is False


@pytest.mark.parametrize("coverage", ["missing", "foreign", "reordered"])
def test_reviewer_truth_requires_exact_case_coverage_and_order(coverage: str) -> None:
    package, review, artifacts = _build_package()
    if coverage == "missing":
        cases = review.cases[:-1]
    elif coverage == "foreign":
        cases = (*review.cases, replace(review.cases[0], case_id="foreign-case"))
    else:
        cases = (review.cases[1], review.cases[0], *review.cases[2:])
    changed = replace(review, cases=cases)

    with pytest.raises(RouteEvidenceIntegrityError, match="coverage/order"):
        _verify(package, changed, artifacts)


def test_package_rejects_duplicate_capture_and_artifact_ownership() -> None:
    package, _, _ = _build_package()
    reused = replace(
        package.cases[1],
        capture_id=package.cases[0].capture_id,
        acquisition=replace(
            package.cases[1].acquisition,
            capture_id=package.cases[0].capture_id,
        ),
        frame_artifact=replace(
            package.cases[1].frame_artifact,
            relative_path=package.cases[0].frame_artifact.relative_path,
        ),
    )

    with pytest.raises(ValueError, match="capture id"):
        FinalizedRouteEvidencePackage(
            package.campaign_plan,
            (package.cases[0], reused, *package.cases[2:]),
            package.finalized_at_utc,
            package.finalized_monotonic_s,
        )


def test_package_rejects_artifact_path_reuse_even_with_distinct_capture_id() -> None:
    package, _, _ = _build_package()
    reused = replace(
        package.cases[1],
        frame_artifact=replace(
            package.cases[1].frame_artifact,
            relative_path=package.cases[0].frame_artifact.relative_path,
        ),
    )

    with pytest.raises(ValueError, match="artifact path"):
        FinalizedRouteEvidencePackage(
            package.campaign_plan,
            (package.cases[0], reused, *package.cases[2:]),
            package.finalized_at_utc,
            package.finalized_monotonic_s,
        )


def test_package_rejects_case_folded_artifact_path_alias() -> None:
    package, _, _ = _build_package()
    aliased = replace(
        package.cases[1],
        frame_artifact=replace(
            package.cases[1].frame_artifact,
            relative_path=package.cases[0].frame_artifact.relative_path.upper(),
        ),
    )

    with pytest.raises(ValueError, match="case-fold alias"):
        FinalizedRouteEvidencePackage(
            package.campaign_plan,
            (package.cases[0], aliased, *package.cases[2:]),
            package.finalized_at_utc,
            package.finalized_monotonic_s,
        )


def test_package_rejects_duplicate_exact_frame_content() -> None:
    package, _, _ = _build_package()
    duplicate = replace(
        package.cases[1],
        frame_artifact=replace(
            package.cases[1].frame_artifact,
            sha256=package.cases[0].frame_artifact.sha256,
        ),
    )

    with pytest.raises(ValueError, match="exact frame content"):
        FinalizedRouteEvidencePackage(
            package.campaign_plan,
            (package.cases[0], duplicate, *package.cases[2:]),
            package.finalized_at_utc,
            package.finalized_monotonic_s,
        )


def test_package_rejects_duplicate_exact_detector_report_content() -> None:
    package, _, _ = _build_package()
    duplicate = replace(
        package.cases[1],
        detector_report_artifact=replace(
            package.cases[1].detector_report_artifact,
            size_bytes=package.cases[0].detector_report_artifact.size_bytes,
            sha256=package.cases[0].detector_report_artifact.sha256,
        ),
    )

    with pytest.raises(ValueError, match="exact detector report content"):
        FinalizedRouteEvidencePackage(
            package.campaign_plan,
            (package.cases[0], duplicate, *package.cases[2:]),
            package.finalized_at_utc,
            package.finalized_monotonic_s,
        )


@pytest.mark.parametrize(
    "unsafe_path",
    (
        "cases/C:/frame.bgra",
        "cases/CON/frame.bgra",
        "cases/aux.json",
        "cases/CON .json",
        "cases/COM\u00b9/frame.bgra",
        "cases/COM\u00b2/frame.bgra",
        "cases/COM\u00b3/frame.bgra",
        "cases/LPT\u00b9/frame.bgra",
        "cases/LPT\u00b2/frame.bgra",
        "cases/LPT\u00b3/frame.bgra",
        "cases/trailing./frame.bgra",
        "cases/trailing /frame.bgra",
    ),
)
def test_artifact_paths_reject_windows_aliases_and_unsafe_components(
    unsafe_path: str,
) -> None:
    with pytest.raises(ValueError, match="path component|device name"):
        RouteEvidenceArtifactRef(unsafe_path, 1, _digest("path"))


def test_package_rejects_missing_case_and_foreign_plan_provenance() -> None:
    package, _, _ = _build_package()
    with pytest.raises(ValueError, match="missing or foreign"):
        FinalizedRouteEvidencePackage(
            package.campaign_plan,
            package.cases[:-1],
            package.finalized_at_utc,
            package.finalized_monotonic_s,
        )

    foreign_digest = _digest("foreign-plan")
    foreign = replace(
        package.cases[0],
        campaign_plan_sha256=foreign_digest,
        acquisition=replace(
            package.cases[0].acquisition,
            campaign_plan_sha256=foreign_digest,
        ),
    )
    with pytest.raises(ValueError, match="foreign campaign provenance"):
        FinalizedRouteEvidencePackage(
            package.campaign_plan,
            (foreign, *package.cases[1:]),
            package.finalized_at_utc,
            package.finalized_monotonic_s,
        )


@pytest.mark.parametrize(
    ("field_name", "foreign_value"),
    (
        (
            "detector",
            CheckpointDetectorIdentity("stale-synthetic-detector", "0.0.1"),
        ),
        (
            "profile",
            CheckpointProfileIdentity(
                "stale-synthetic-profile",
                "0.0.1",
                _digest("stale-profile"),
            ),
        ),
        ("capture_source_id", "foreign-capture-source"),
        ("capture_session_id", "foreign-capture-session"),
        (
            "capture_build",
            RouteEvidenceCaptureBuildIdentity(
                "stale-capture-build",
                "0.0.1",
                _digest("stale-capture-build"),
            ),
        ),
        (
            "capture_configuration_sha256",
            _digest("stale-capture-configuration"),
        ),
        ("capture_environment_sha256", _digest("foreign-environment")),
        ("support_envelope_sha256", _digest("stale-support-envelope")),
    ),
)
def test_finalization_rejects_stale_or_mixed_source_session_and_configuration(
    field_name: str,
    foreign_value: object,
) -> None:
    package, _, _ = _build_package()
    acquisition = package.cases[1].acquisition
    if field_name == "capture_session_id":
        acquisition = replace(acquisition, capture_session_id=foreign_value)
    changed = replace(
        package.cases[1],
        acquisition=acquisition,
        **{field_name: foreign_value},  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="foreign campaign provenance"):
        FinalizedRouteEvidencePackage(
            package.campaign_plan,
            (package.cases[0], changed, *package.cases[2:]),
            package.finalized_at_utc,
            package.finalized_monotonic_s,
        )


@pytest.mark.parametrize(
    ("frame_ref", "pixel_format"),
    (
        (FrameRef(2, 2.0, 1, 2), PixelFormat.BGRA8888),
        (FrameRef(2, 2.0, 2, 1), PixelFormat.RGBA8888),
    ),
)
def test_finalization_rejects_mixed_campaign_geometry_or_pixel_format(
    frame_ref: FrameRef,
    pixel_format: PixelFormat,
) -> None:
    package, _, _ = _build_package()
    original = package.cases[1]
    changed = replace(original, frame_ref=frame_ref, pixel_format=pixel_format)

    with pytest.raises(ValueError, match="foreign campaign provenance"):
        FinalizedRouteEvidencePackage(
            package.campaign_plan,
            (package.cases[0], changed, *package.cases[2:]),
            package.finalized_at_utc,
            package.finalized_monotonic_s,
        )


def test_campaign_requires_explicit_terminal_arrival_and_checkpoint_coverage() -> None:
    package, _, _ = _build_package()
    plan = package.campaign_plan
    without_arrival = tuple(
        replace(item, role=RouteEvidenceCaseRole.CHECKPOINT_POSITIVE)
        if item.role is RouteEvidenceCaseRole.ROUTE_ARRIVAL
        else item
        for item in plan.cases
    )
    with pytest.raises(ValueError, match="exactly one explicit route-arrival"):
        replace(plan, cases=without_arrival)

    without_transit_positive = tuple(
        item for item in plan.cases if item.case_id != "synthetic-transit-positive"
    )
    without_transit_positive = tuple(
        replace(item, ordinal=index) for index, item in enumerate(without_transit_positive, start=1)
    )
    with pytest.raises(ValueError, match="every route checkpoint"):
        replace(plan, cases=without_transit_positive)


def test_campaign_rejects_arrival_first() -> None:
    package, _, _ = _build_package()
    plan = package.campaign_plan
    arrival_first = (plan.cases[-1], *plan.cases[:-1])
    arrival_first = tuple(
        replace(item, ordinal=index) for index, item in enumerate(arrival_first, start=1)
    )

    with pytest.raises(ValueError, match="first case.*departure"):
        replace(plan, cases=arrival_first)


def test_campaign_rejects_reordered_positive_checkpoint_subsequence() -> None:
    package, _, _ = _build_package()
    plan = package.campaign_plan
    checkpoints = tuple(item.checkpoint_id for item in plan.route_plan.checkpoints)
    reordered = (
        plan.cases[0],
        RouteEvidenceCaseSpec(
            2,
            "synthetic-arrival-too-early",
            RouteEvidenceCaseRole.CHECKPOINT_POSITIVE,
            checkpoints[2],
        ),
        replace(plan.cases[1], ordinal=3),
        replace(plan.cases[2], ordinal=4),
        replace(plan.cases[3], ordinal=5),
    )

    with pytest.raises(ValueError, match="nondecreasing route order"):
        replace(plan, cases=reordered)


def test_campaign_requires_terminal_arrival_to_be_final_case() -> None:
    package, _, _ = _build_package()
    plan = package.campaign_plan
    post_arrival_negative = RouteEvidenceCaseSpec(
        5,
        "synthetic-post-arrival-negative",
        RouteEvidenceCaseRole.CHECKPOINT_NEGATIVE,
        plan.route_plan.checkpoints[-1].checkpoint_id,
    )

    with pytest.raises(ValueError, match="route-arrival.*final"):
        replace(plan, cases=(*plan.cases, post_arrival_negative))


def test_negative_case_match_fails_conformance_and_endpoint_claim() -> None:
    package, review, artifacts = _build_package()
    negative = package.campaign_plan.cases[1]
    matched_negative = replace(
        review.cases[1],
        detection=CheckpointDetection(
            CheckpointMatchKind.MATCHED,
            (negative.checkpoint_id,),
            1.0,
        ),
    )
    changed = replace(
        review,
        cases=(review.cases[0], matched_negative, *review.cases[2:]),
    )

    report = _verify(package, changed, artifacts)

    assert report.evidence_conformance_passed is False
    assert report.failure_reasons == (
        "synthetic-transit-negative:negative_case_was_definitively_matched",
        "synthetic-transit-negative:detector_output_disagrees_with_reviewer_truth",
    )
    assert report.endpoint.route_arrival_verified is False
    assert report.real_release_role_satisfied is False


def test_unknown_arrival_never_becomes_route_arrival() -> None:
    package, review, artifacts = _build_package()
    unknown_arrival = replace(
        review.cases[-1],
        detection=CheckpointDetection(CheckpointMatchKind.UNKNOWN, (), 0.0),
    )
    changed = replace(review, cases=(*review.cases[:-1], unknown_arrival))

    report = _verify(package, changed, artifacts)

    assert report.evidence_conformance_passed is False
    assert report.endpoint.route_arrival_verified is False
    assert report.endpoint.supported_mining_view_proven is False
    assert report.endpoint.bank_interface_open_proven is False


def test_reviewer_rejection_is_retained_as_failure() -> None:
    package, review, artifacts = _build_package()
    rejected = replace(
        review.cases[0],
        decision=RouteEvidenceReviewDecision.REJECTED,
    )
    changed = replace(review, cases=(rejected, *review.cases[1:]))

    report = _verify(package, changed, artifacts)

    assert report.failure_reasons == ("synthetic-departure-positive:reviewer_rejected",)
    assert report.activation_allowed is False


def test_review_must_follow_finalization() -> None:
    package, review, artifacts = _build_package()
    early = replace(review, reviewed_at_utc=package.finalized_at_utc)

    with pytest.raises(RouteEvidenceIntegrityError, match="follow package finalization"):
        _verify(package, early, artifacts)


def test_endpoint_verification_cannot_be_caller_forged() -> None:
    package, review, _ = _build_package()

    with pytest.raises(RouteEvidenceIntegrityError, match="only be constructed"):
        RouteEndpointVerification(
            route=package.route,
            finalized_package_sha256=package.content_sha256,
            reviewer_truth_sha256=review.content_sha256,
            arrival_case_id="synthetic-arrival-proof",
            arrival_checkpoint_id=package.campaign_plan.route_plan.checkpoints[-1].checkpoint_id,
            route_arrival_verified=True,
        )


def test_verification_report_rejects_endpoint_hash_rebinding() -> None:
    package, review, artifacts = _build_package()
    report = _verify(package, review, artifacts)
    token = route_evidence_module._REPORT_FACTORY_TOKEN
    foreign_endpoints = (
        replace(
            report.endpoint,
            finalized_package_sha256=_digest("foreign-endpoint-package"),
            _factory_token=token,
        ),
        replace(
            report.endpoint,
            reviewer_truth_sha256=_digest("foreign-endpoint-review"),
            _factory_token=token,
        ),
    )

    for foreign_endpoint in foreign_endpoints:
        with pytest.raises(ValueError, match="endpoint package/review hashes"):
            replace(
                report,
                endpoint=foreign_endpoint,
                _factory_token=token,
            )


def test_foreign_review_checkpoint_truth_is_rejected() -> None:
    package, review, artifacts = _build_package()
    foreign_truth = replace(
        review.cases[0],
        detection=CheckpointDetection(
            CheckpointMatchKind.MATCHED,
            ("foreign-checkpoint",),
            1.0,
        ),
    )
    changed = replace(review, cases=(foreign_truth, *review.cases[1:]))

    with pytest.raises(RouteEvidenceIntegrityError, match="foreign route checkpoint"):
        _verify(package, changed, artifacts)
