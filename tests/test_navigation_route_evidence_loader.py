from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

import mining_automation.navigation.route_evidence_loader as loader_module
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
    FinalizedRouteEvidencePackage,
    OwnedRouteEvidenceCase,
    RouteEvidenceAcquisitionBinding,
    RouteEvidenceArtifactRef,
    RouteEvidenceCampaignPlan,
    RouteEvidenceCaptureBuildIdentity,
    RouteEvidenceCaseRole,
    RouteEvidenceCaseSpec,
    RouteEvidenceCaseTruth,
    RouteEvidenceIntegrityError,
    RouteEvidenceOperatorIntent,
    RouteEvidenceReview,
    RouteEvidenceReviewDecision,
    SyntheticRouteEvidenceDetectorReport,
    canonical_route_evidence_bytes,
    parse_synthetic_detector_report,
    route_evidence_sha256,
    verify_synthetic_route_evidence,
)
from mining_automation.navigation.route_evidence_loader import (
    FINALIZED_PACKAGE_FILENAME,
    INDEPENDENT_REVIEW_FILENAME,
    RouteEvidenceFilesystemExpectation,
    load_and_verify_synthetic_route_evidence,
)


def _digest(label: str) -> Sha256Digest:
    return Sha256Digest.from_bytes(label.encode("ascii"))


def _route(direction: RouteDirection) -> RoutePlan:
    if direction is RouteDirection.MINE_TO_BANK:
        prefix = "synthetic-loader-m2b"
        origin = RouteEndpoint("synthetic-mine", RouteEndpointRole.MINE)
        destination = RouteEndpoint("synthetic-bank", RouteEndpointRole.BANK)
    else:
        prefix = "synthetic-loader-b2m"
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


def _detection(spec: RouteEvidenceCaseSpec) -> CheckpointDetection:
    if spec.role is RouteEvidenceCaseRole.CHECKPOINT_NEGATIVE:
        return CheckpointDetection(CheckpointMatchKind.UNKNOWN, (), 0.0)
    return CheckpointDetection(CheckpointMatchKind.MATCHED, (spec.checkpoint_id,), 1.0)


def _build_package(
    direction: RouteDirection = RouteDirection.MINE_TO_BANK,
    *,
    configuration_label: str | None = None,
) -> tuple[
    FinalizedRouteEvidencePackage,
    RouteEvidenceReview,
    dict[str, bytes],
]:
    route = _route(direction)
    plan = RouteEvidenceCampaignPlan(
        campaign_id=f"synthetic-loader-{direction.value}-campaign",
        route_plan=route,
        detector=CheckpointDetectorIdentity("synthetic-loader-detector", "0.0.0"),
        profile=CheckpointProfileIdentity(
            "synthetic-loader-profile",
            "0.0.0",
            _digest(f"{direction.value}-profile"),
        ),
        capture_source_id=f"synthetic-loader-{direction.value}-source",
        capture_session_id=f"synthetic-loader-{direction.value}-session",
        capture_build=RouteEvidenceCaptureBuildIdentity(
            "synthetic-loader-capture",
            "0.0.0",
            _digest("synthetic-loader-capture-build"),
        ),
        frame_width=2,
        frame_height=1,
        pixel_format=PixelFormat.BGRA8888,
        capture_configuration_sha256=_digest(
            configuration_label
            if configuration_label is not None
            else f"{direction.value}-configuration"
        ),
        capture_environment_sha256=_digest(f"{direction.value}-environment"),
        support_envelope_sha256=_digest(f"{direction.value}-support-envelope"),
        operator_id="synthetic-loader-operator",
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
        capture_id = f"synthetic-loader-capture-{spec.ordinal}"
        acquisition = RouteEvidenceAcquisitionBinding(
            campaign_plan_sha256=plan.content_sha256,
            capture_source_identity_sha256=plan.capture_source_identity_sha256,
            capture_session_id=plan.capture_session_id,
            request_id=f"synthetic-loader-request-{spec.ordinal}",
            sequence_index=spec.ordinal,
            case_id=spec.case_id,
            capture_id=capture_id,
            operator_id=plan.operator_id,
            acknowledged_monotonic_s=float(spec.ordinal) - 0.5,
            expires_monotonic_s=float(spec.ordinal) + 29.5,
            frame_captured_monotonic_s=frame_ref.captured_monotonic_s,
            recorded_monotonic_s=float(spec.ordinal) + 0.1,
            previous_acquisition_sha256=previous_acquisition_sha256,
        )
        report = SyntheticRouteEvidenceDetectorReport(
            campaign_id=plan.campaign_id,
            campaign_plan_sha256=plan.content_sha256,
            route=plan.route,
            route_plan_sha256=plan.route_plan_sha256,
            sequence_index=spec.ordinal,
            case_id=spec.case_id,
            capture_id=capture_id,
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
            detection=_detection(spec),
        )
        frame_path = f"cases/{spec.ordinal:02d}-{spec.case_id}/frame.bgra"
        report_path = f"cases/{spec.ordinal:02d}-{spec.case_id}/detector-report.json"
        artifacts[frame_path] = frame_payload
        artifacts[report_path] = report.canonical_bytes
        owned_cases.append(
            OwnedRouteEvidenceCase(
                campaign_id=plan.campaign_id,
                campaign_plan_sha256=plan.content_sha256,
                route=plan.route,
                route_plan_sha256=plan.route_plan_sha256,
                sequence_index=spec.ordinal,
                case_id=spec.case_id,
                capture_id=capture_id,
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
                    len(report.canonical_bytes),
                    report.content_sha256,
                ),
            )
        )
        previous_acquisition_sha256 = owned_cases[-1].content_sha256
    package = FinalizedRouteEvidencePackage(
        campaign_plan=plan,
        cases=tuple(owned_cases),
        finalized_at_utc="2026-09-01T00:00:10Z",
        finalized_monotonic_s=5.0,
    )
    review = RouteEvidenceReview(
        finalized_package_sha256=package.content_sha256,
        campaign_id=plan.campaign_id,
        route=plan.route,
        route_plan_sha256=plan.route_plan_sha256,
        reviewer_id="synthetic-loader-independent-reviewer",
        reviewed_at_utc="2026-09-01T00:00:11Z",
        cases=tuple(
            RouteEvidenceCaseTruth(
                case_id=spec.case_id,
                frame_sha256=owned.frame_artifact.sha256,
                detector_report_sha256=owned.detector_report_artifact.sha256,
                decision=RouteEvidenceReviewDecision.APPROVED,
                detection=_detection(spec),
            )
            for spec, owned in zip(plan.cases, package.cases, strict=True)
        ),
    )
    return package, review, artifacts


def _expectation(
    package: FinalizedRouteEvidencePackage,
    review: RouteEvidenceReview,
) -> RouteEvidenceFilesystemExpectation:
    plan = package.campaign_plan
    return RouteEvidenceFilesystemExpectation(
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
        independent_review_sha256=review.content_sha256,
        reviewer_id=review.reviewer_id,
    )


def _write_package(
    root: Path,
    package: FinalizedRouteEvidencePackage,
    review: RouteEvidenceReview,
    artifacts: dict[str, bytes],
) -> None:
    root.mkdir()
    (root / FINALIZED_PACKAGE_FILENAME).write_bytes(
        canonical_route_evidence_bytes(package.to_json_value())
    )
    (root / INDEPENDENT_REVIEW_FILENAME).write_bytes(
        canonical_route_evidence_bytes(review.to_json_value())
    )
    for relative, payload in artifacts.items():
        path = root.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


@pytest.mark.parametrize("direction", tuple(RouteDirection))
def test_loader_accepts_exact_canonical_packages_for_both_directions(
    tmp_path: Path,
    direction: RouteDirection,
) -> None:
    package, review, artifacts = _build_package(direction)
    root = tmp_path / direction.value
    _write_package(root, package, review, artifacts)

    report = load_and_verify_synthetic_route_evidence(
        root,
        _expectation(package, review),
    )

    assert report.evidence_conformance_passed is True
    assert report.route == package.route
    assert report.endpoint.route_arrival_verified is True
    assert report.endpoint.supported_mining_view_proven is False
    assert report.endpoint.bank_interface_open_proven is False
    assert report.real_release_role_satisfied is False
    assert report.live_navigation_enabled is False
    assert report.activation_allowed is False
    assert report.input_authority is False


def test_loader_rejects_old_or_foreign_caller_expectation(tmp_path: Path) -> None:
    old_package, old_review, _ = _build_package(configuration_label="old-configuration")
    package, review, artifacts = _build_package(configuration_label="new-configuration")
    root = tmp_path / "package"
    _write_package(root, package, review, artifacts)

    with pytest.raises(RouteEvidenceIntegrityError, match="expectation"):
        load_and_verify_synthetic_route_evidence(root, _expectation(old_package, old_review))

    foreign = replace(
        _expectation(package, review),
        finalized_package_sha256=_digest("foreign-package"),
    )
    with pytest.raises(RouteEvidenceIntegrityError, match="expectation"):
        load_and_verify_synthetic_route_evidence(root, foreign)


@pytest.mark.parametrize(
    "pin",
    [
        "acquisition-head",
        "capture-build",
        "frame-width",
        "frame-height",
        "pixel-format",
    ],
)
def test_loader_fails_closed_when_caller_identity_pins_drift(
    tmp_path: Path,
    pin: str,
) -> None:
    package, review, artifacts = _build_package()
    root = tmp_path / "package"
    _write_package(root, package, review, artifacts)
    expected = _expectation(package, review)
    if pin == "acquisition-head":
        foreign = replace(expected, acquisition_head_sha256=_digest("foreign-head"))
    elif pin == "capture-build":
        foreign = replace(
            expected,
            capture_build=replace(
                expected.capture_build,
                content_sha256=_digest("foreign-capture-build"),
            ),
        )
    elif pin == "frame-width":
        foreign = replace(expected, frame_width=expected.frame_width + 1)
    elif pin == "frame-height":
        foreign = replace(expected, frame_height=expected.frame_height + 1)
    else:
        foreign = replace(expected, pixel_format=PixelFormat.RGBA8888)

    with pytest.raises(RouteEvidenceIntegrityError, match="expectation"):
        load_and_verify_synthetic_route_evidence(root, foreign)


def test_loader_pins_independent_review_digest_and_reviewer(tmp_path: Path) -> None:
    package, review, artifacts = _build_package()
    root = tmp_path / "package"
    _write_package(root, package, review, artifacts)
    expected = _expectation(package, review)
    replaced_review = replace(review, reviewer_id="foreign-independent-reviewer")
    (root / INDEPENDENT_REVIEW_FILENAME).write_bytes(
        canonical_route_evidence_bytes(replaced_review.to_json_value())
    )

    with pytest.raises(RouteEvidenceIntegrityError, match="review digest"):
        load_and_verify_synthetic_route_evidence(root, expected)


def test_loader_rejects_detector_report_rebound_to_another_case(tmp_path: Path) -> None:
    package, review, artifacts = _build_package()
    owned = package.cases[-1]
    report_path = owned.detector_report_artifact.relative_path
    rebound_case_id = package.cases[0].case_id
    rebound_acquisition = replace(owned.acquisition, case_id=rebound_case_id)
    rebound_report = replace(
        parse_synthetic_detector_report(artifacts[report_path]),
        case_id=rebound_case_id,
        acquisition=rebound_acquisition,
    )
    rebound_payload = rebound_report.canonical_bytes
    changed_owned = replace(
        owned,
        detector_report_artifact=RouteEvidenceArtifactRef(
            report_path,
            len(rebound_payload),
            rebound_report.content_sha256,
        ),
    )
    changed_package = FinalizedRouteEvidencePackage(
        campaign_plan=package.campaign_plan,
        cases=(*package.cases[:-1], changed_owned),
        finalized_at_utc=package.finalized_at_utc,
        finalized_monotonic_s=package.finalized_monotonic_s,
    )
    changed_truth = replace(
        review.cases[-1],
        detector_report_sha256=rebound_report.content_sha256,
    )
    changed_review = replace(
        review,
        finalized_package_sha256=changed_package.content_sha256,
        cases=(*review.cases[:-1], changed_truth),
    )
    changed_artifacts = dict(artifacts)
    changed_artifacts[report_path] = rebound_payload
    root = tmp_path / "package"
    _write_package(root, changed_package, changed_review, changed_artifacts)

    with pytest.raises(RouteEvidenceIntegrityError, match="identity differs|rebound"):
        load_and_verify_synthetic_route_evidence(
            root,
            _expectation(changed_package, changed_review),
        )


def test_loader_rejects_duplicate_json_key(tmp_path: Path) -> None:
    package, review, artifacts = _build_package()
    root = tmp_path / "package"
    _write_package(root, package, review, artifacts)
    path = root / FINALIZED_PACKAGE_FILENAME
    payload = path.read_bytes()
    path.write_bytes(
        payload.replace(
            b'{"activation_allowed":false,',
            b'{"activation_allowed":false,"activation_allowed":false,',
            1,
        )
    )

    with pytest.raises(RouteEvidenceIntegrityError, match="duplicate JSON key"):
        load_and_verify_synthetic_route_evidence(root, _expectation(package, review))


@pytest.mark.parametrize("mutation", ["noncanonical", "unknown", "old-schema"])
def test_loader_rejects_noncanonical_or_unknown_manifest_fields(
    tmp_path: Path,
    mutation: str,
) -> None:
    package, review, artifacts = _build_package()
    root = tmp_path / "package"
    _write_package(root, package, review, artifacts)
    path = root / FINALIZED_PACKAGE_FILENAME
    value = json.loads(path.read_text(encoding="ascii"))
    if mutation == "noncanonical":
        path.write_text(json.dumps(value, indent=2), encoding="ascii", newline="\n")
        expected = "not canonical"
    elif mutation == "unknown":
        value["unknown"] = False
        path.write_bytes(canonical_route_evidence_bytes(value))
        expected = "unknown"
    else:
        value["schema"] = "fixed-route-evidence-finalized-package-v1"
        path.write_bytes(canonical_route_evidence_bytes(value))
        expected = "schema"

    with pytest.raises(RouteEvidenceIntegrityError, match=expected):
        load_and_verify_synthetic_route_evidence(root, _expectation(package, review))


@pytest.mark.parametrize("manifest_kind", ("owned-frame", "review-truth"))
def test_loader_rejects_integer_aliases_for_exact_float_fields(
    tmp_path: Path,
    manifest_kind: str,
) -> None:
    package, review, artifacts = _build_package()
    root = tmp_path / "package"
    _write_package(root, package, review, artifacts)
    if manifest_kind == "owned-frame":
        path = root / FINALIZED_PACKAGE_FILENAME
        value = json.loads(path.read_text(encoding="ascii"))
        value["cases"][0]["case"]["frame"]["captured_monotonic_s"] = 1
    else:
        path = root / INDEPENDENT_REVIEW_FILENAME
        value = json.loads(path.read_text(encoding="ascii"))
        value["cases"][0]["reviewed_detection"]["confidence"] = 1
    path.write_bytes(canonical_route_evidence_bytes(value))

    with pytest.raises(RouteEvidenceIntegrityError, match="typed canonical round-trip"):
        load_and_verify_synthetic_route_evidence(root, _expectation(package, review))


@pytest.mark.parametrize(
    "identity",
    ("detector", "profile", "route", "review-route"),
)
def test_loader_rejects_nonportable_unicode_identity_fields(
    tmp_path: Path,
    identity: str,
) -> None:
    package, review, artifacts = _build_package()
    root = tmp_path / "package"
    _write_package(root, package, review, artifacts)
    if identity == "review-route":
        path = root / INDEPENDENT_REVIEW_FILENAME
        value = json.loads(path.read_text(encoding="ascii"))
        value["route"]["route_id"] = "rout\u00e9"
    else:
        path = root / FINALIZED_PACKAGE_FILENAME
        value = json.loads(path.read_text(encoding="ascii"))
        campaign = value["campaign_plan"]
        if identity == "detector":
            campaign["checkpoint_detector"]["detector_id"] = "d\u00e9tecteur"
        elif identity == "profile":
            campaign["checkpoint_profile"]["profile_id"] = "profil\u00e9"
        else:
            campaign["route_plan"]["identity"]["route_id"] = "rout\u00e9"
    path.write_bytes(canonical_route_evidence_bytes(value))

    with pytest.raises(RouteEvidenceIntegrityError, match="portable identifier"):
        load_and_verify_synthetic_route_evidence(root, _expectation(package, review))


def test_loader_rejects_declared_acquisition_head_mismatch(tmp_path: Path) -> None:
    package, review, artifacts = _build_package()
    root = tmp_path / "package"
    _write_package(root, package, review, artifacts)
    path = root / FINALIZED_PACKAGE_FILENAME
    value = json.loads(path.read_text(encoding="ascii"))
    value["acquisition_head_sha256"] = _digest("foreign-acquisition-head").value
    path.write_bytes(canonical_route_evidence_bytes(value))

    with pytest.raises(RouteEvidenceIntegrityError, match="acquisition-head"):
        load_and_verify_synthetic_route_evidence(root, _expectation(package, review))


@pytest.mark.parametrize(
    ("finalized_monotonic_s", "expected"),
    [
        (4.1, "finalization must follow"),
        (-1.0, "non-negative"),
        ("5.0", "JSON number"),
    ],
)
def test_loader_rejects_invalid_finalization_monotonic_time(
    tmp_path: Path,
    finalized_monotonic_s: object,
    expected: str,
) -> None:
    package, review, artifacts = _build_package()
    root = tmp_path / "package"
    _write_package(root, package, review, artifacts)
    path = root / FINALIZED_PACKAGE_FILENAME
    value = json.loads(path.read_text(encoding="ascii"))
    value["finalized_monotonic_s"] = finalized_monotonic_s
    path.write_bytes(canonical_route_evidence_bytes(value))

    with pytest.raises(RouteEvidenceIntegrityError, match=expected):
        load_and_verify_synthetic_route_evidence(root, _expectation(package, review))


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("duplicate-request", "request id"),
        ("backdated-acknowledgement", "source chronology"),
        ("source-identity", "foreign campaign provenance"),
        ("fixed-false", "fixed schema value"),
        ("old-acquisition-schema", "schema"),
    ],
)
def test_loader_rejects_owned_acquisition_transcript_mutations(
    tmp_path: Path,
    mutation: str,
    expected: str,
) -> None:
    package, review, artifacts = _build_package()
    root = tmp_path / "package"
    _write_package(root, package, review, artifacts)
    path = root / FINALIZED_PACKAGE_FILENAME
    value = json.loads(path.read_text(encoding="ascii"))
    first_acquisition = value["cases"][0]["case"]["acquisition"]
    prior_acquisition = value["cases"][-2]["case"]["acquisition"]
    final_entry = value["cases"][-1]
    final_case = final_entry["case"]
    acquisition = final_case["acquisition"]
    if mutation == "duplicate-request":
        acquisition["request_id"] = first_acquisition["request_id"]
    elif mutation == "backdated-acknowledgement":
        acquisition["acknowledged_monotonic_s"] = prior_acquisition["recorded_monotonic_s"]
        acquisition["expires_monotonic_s"] = acquisition["acknowledged_monotonic_s"] + 30.0
    elif mutation == "source-identity":
        acquisition["capture_source_identity_sha256"] = _digest("foreign-source-identity").value
    elif mutation == "fixed-false":
        acquisition["navigation_automation_enabled"] = True
    else:
        acquisition["schema"] = "fixed-route-evidence-acquisition-binding-v0"
    final_entry["case_record_sha256"] = route_evidence_sha256(final_case).value
    value["acquisition_head_sha256"] = final_entry["case_record_sha256"]
    path.write_bytes(canonical_route_evidence_bytes(value))

    with pytest.raises(RouteEvidenceIntegrityError, match=expected):
        load_and_verify_synthetic_route_evidence(root, _expectation(package, review))


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("request", "identity differs"),
        ("source-identity", "identity differs"),
        ("capture-build", "identity differs"),
        ("old-acquisition-schema", "acquisition binding"),
    ],
)
def test_loader_rejects_detector_report_acquisition_or_build_rebinding(
    tmp_path: Path,
    mutation: str,
    expected: str,
) -> None:
    package, review, artifacts = _build_package()
    owned = package.cases[-1]
    report_path = owned.detector_report_artifact.relative_path
    report_value = json.loads(artifacts[report_path].decode("ascii"))
    if mutation == "request":
        report_value["acquisition"]["request_id"] = "foreign-request"
    elif mutation == "source-identity":
        report_value["acquisition"]["capture_source_identity_sha256"] = _digest(
            "foreign-source-identity"
        ).value
    elif mutation == "capture-build":
        report_value["capture_build"]["content_sha256"] = _digest("foreign-capture-build").value
    else:
        report_value["acquisition"]["schema"] = "fixed-route-evidence-acquisition-binding-v0"
    rebound_payload = canonical_route_evidence_bytes(report_value)
    rebound_sha256 = Sha256Digest.from_bytes(rebound_payload)
    changed_owned = replace(
        owned,
        detector_report_artifact=RouteEvidenceArtifactRef(
            report_path,
            len(rebound_payload),
            rebound_sha256,
        ),
    )
    changed_package = FinalizedRouteEvidencePackage(
        campaign_plan=package.campaign_plan,
        cases=(*package.cases[:-1], changed_owned),
        finalized_at_utc=package.finalized_at_utc,
        finalized_monotonic_s=package.finalized_monotonic_s,
    )
    changed_truth = replace(
        review.cases[-1],
        detector_report_sha256=rebound_sha256,
    )
    changed_review = replace(
        review,
        finalized_package_sha256=changed_package.content_sha256,
        cases=(*review.cases[:-1], changed_truth),
    )
    changed_artifacts = dict(artifacts)
    changed_artifacts[report_path] = rebound_payload
    root = tmp_path / "package"
    _write_package(root, changed_package, changed_review, changed_artifacts)

    with pytest.raises(RouteEvidenceIntegrityError, match=expected):
        load_and_verify_synthetic_route_evidence(
            root,
            _expectation(changed_package, changed_review),
        )


@pytest.mark.parametrize(
    "payload",
    [b"{\n", b'[{"schema":"not-an-object"}]\n', b'{"schema":NaN}\n'],
)
def test_loader_normalizes_malformed_manifest_failures(
    tmp_path: Path,
    payload: bytes,
) -> None:
    package, review, artifacts = _build_package()
    root = tmp_path / "package"
    _write_package(root, package, review, artifacts)
    (root / FINALIZED_PACKAGE_FILENAME).write_bytes(payload)

    with pytest.raises(RouteEvidenceIntegrityError):
        load_and_verify_synthetic_route_evidence(root, _expectation(package, review))


@pytest.mark.parametrize("mutation", ["missing", "foreign"])
def test_loader_rejects_missing_and_foreign_files(tmp_path: Path, mutation: str) -> None:
    package, review, artifacts = _build_package()
    root = tmp_path / "package"
    _write_package(root, package, review, artifacts)
    if mutation == "missing":
        relative = package.cases[0].frame_artifact.relative_path
        root.joinpath(*relative.split("/")).unlink()
        expected = "missing_files"
    else:
        (root / "foreign.bin").write_bytes(b"foreign")
        expected = "foreign_files"

    with pytest.raises(RouteEvidenceIntegrityError, match=expected):
        load_and_verify_synthetic_route_evidence(root, _expectation(package, review))


def test_loader_rehashes_and_restats_every_file_before_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package, review, artifacts = _build_package()
    root = tmp_path / "package"
    _write_package(root, package, review, artifacts)
    relative = package.cases[0].frame_artifact.relative_path
    target = root.joinpath(*relative.split("/"))

    def replacing_verifier(*args: object, **kwargs: object) -> object:
        report = verify_synthetic_route_evidence(*args, **kwargs)  # type: ignore[arg-type]
        target.write_bytes(b"x" * target.stat().st_size)
        return report

    monkeypatch.setattr(loader_module, "verify_synthetic_route_evidence", replacing_verifier)

    with pytest.raises(RouteEvidenceIntegrityError, match="changed during verification"):
        load_and_verify_synthetic_route_evidence(root, _expectation(package, review))


def test_loader_rejects_symlink_or_reparse_file_without_platform_skip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package, review, artifacts = _build_package()
    root = tmp_path / "package"
    _write_package(root, package, review, artifacts)
    relative = package.cases[0].frame_artifact.relative_path
    target = root.joinpath(*relative.split("/"))
    original_payload = target.read_bytes()
    replacement = tmp_path / "replacement.bin"
    replacement.write_bytes(original_payload)
    target.unlink()
    try:
        target.symlink_to(replacement)
    except OSError:
        target.write_bytes(original_payload)
        original_lstat = loader_module._lstat

        def simulated_lstat(path: Path, context: str) -> os.stat_result:
            if path == target:
                raise RouteEvidenceIntegrityError(f"{context} is a symlink or reparse point")
            return original_lstat(path, context)

        monkeypatch.setattr(loader_module, "_lstat", simulated_lstat)

    with pytest.raises(RouteEvidenceIntegrityError, match="symlink or reparse"):
        load_and_verify_synthetic_route_evidence(root, _expectation(package, review))


def test_loader_rejects_hard_link_to_content_outside_the_evidence_tree(
    tmp_path: Path,
) -> None:
    package, review, artifacts = _build_package()
    root = tmp_path / "package"
    _write_package(root, package, review, artifacts)
    relative = package.cases[0].frame_artifact.relative_path
    target = root.joinpath(*relative.split("/"))
    outside = tmp_path / "outside-owned-frame.bgra"
    outside.write_bytes(target.read_bytes())
    target.unlink()
    os.link(outside, target)

    with pytest.raises(RouteEvidenceIntegrityError, match="external hard-link alias"):
        load_and_verify_synthetic_route_evidence(root, _expectation(package, review))


def test_loader_rejects_parent_escape_in_manifest(tmp_path: Path) -> None:
    package, review, artifacts = _build_package()
    root = tmp_path / "package"
    _write_package(root, package, review, artifacts)
    manifest_path = root / FINALIZED_PACKAGE_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="ascii"))
    manifest["cases"][0]["case"]["frame"]["artifact"]["path"] = "../escape.bgra"
    manifest_path.write_bytes(canonical_route_evidence_bytes(manifest))

    with pytest.raises(RouteEvidenceIntegrityError, match="safe relative"):
        load_and_verify_synthetic_route_evidence(root, _expectation(package, review))


def test_loader_rejects_case_fold_artifact_aliases(tmp_path: Path) -> None:
    package, review, artifacts = _build_package()
    first_path = package.cases[0].frame_artifact.relative_path
    root = tmp_path / "package"
    _write_package(root, package, review, artifacts)
    manifest_path = root / FINALIZED_PACKAGE_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="ascii"))
    second_case = manifest["cases"][1]["case"]
    second_case["frame"]["artifact"]["path"] = first_path.upper()
    manifest["cases"][1]["case_record_sha256"] = route_evidence_sha256(second_case).value
    manifest_path.write_bytes(canonical_route_evidence_bytes(manifest))

    with pytest.raises(RouteEvidenceIntegrityError, match="case-fold"):
        load_and_verify_synthetic_route_evidence(root, _expectation(package, review))


@pytest.mark.parametrize(
    ("unsafe_path", "expected"),
    (
        ("cases/file:stream", "Windows-unsafe"),
        ("cases/COM\u00b9/frame.bgra", "reserved Windows device"),
        ("cases/COM\u00b2/frame.bgra", "reserved Windows device"),
        ("cases/COM\u00b3/frame.bgra", "reserved Windows device"),
        ("cases/LPT\u00b9/frame.bgra", "reserved Windows device"),
        ("cases/LPT\u00b2/frame.bgra", "reserved Windows device"),
        ("cases/LPT\u00b3/frame.bgra", "reserved Windows device"),
    ),
)
def test_loader_rejects_windows_unsafe_artifact_path_in_manifest(
    tmp_path: Path,
    unsafe_path: str,
    expected: str,
) -> None:
    package, review, artifacts = _build_package()
    root = tmp_path / "package"
    _write_package(root, package, review, artifacts)
    manifest_path = root / FINALIZED_PACKAGE_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="ascii"))
    manifest["cases"][0]["case"]["frame"]["artifact"]["path"] = unsafe_path
    manifest_path.write_bytes(canonical_route_evidence_bytes(manifest))

    with pytest.raises(RouteEvidenceIntegrityError, match=expected):
        load_and_verify_synthetic_route_evidence(root, _expectation(package, review))
