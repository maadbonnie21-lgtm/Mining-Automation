from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.perception import (
    WideLandmarkSearch,
    WideRegistrationDiagnosis,
    WideSceneRegistrationAnalysis,
    load_varrock_east_iron_profile,
    varrock_east_iron_scene_excluded_regions,
)
from mining_automation.perception.scene_landmarks import MacroZone
from mining_automation.validation import camera_guidance_v2 as guidance_v2_module
from mining_automation.validation.camera_guidance import (
    CAMERA_GUIDANCE_ID,
    CAMERA_GUIDANCE_VERSION,
    CameraGuidanceAxis,
    CameraGuidanceDirection,
    CameraGuidanceDisposition,
    CameraGuidanceReason,
    CameraSimilarityFit,
    WorldCameraGuidance,
)
from mining_automation.validation.camera_guidance_v2 import (
    CAMERA_GUIDANCE_V2_DRAG_PULSE_PIXELS,
    CameraGuidanceV2Disposition,
    CameraGuidanceV2Reason,
    CameraGuidanceV2Session,
    CameraPrimitiveAxis,
    build_camera_guidance_v2_plan,
    build_camera_guidance_v2_probe,
    select_camera_guidance_v2,
)
from mining_automation.validation.camera_plan import (
    EXPECTED_CLIENT_HEIGHT,
    EXPECTED_CLIENT_WIDTH,
    REVIEWED_CAMERA_WHEEL_POINT,
    REVIEWED_COMPASS_POINT,
    CameraActionReceipt,
    CameraDragAxis,
    CameraInputOperation,
    CameraInputReceipt,
    CameraMiddleDrag,
    CameraPlan,
    CameraPlanReceipt,
    CameraPreflightReceipt,
    CameraWheel,
    CompassClick,
)
from mining_automation.validation.client_readiness import GAMEPLAY_CHROME_POLICIES


def _frame(*, frame_id: int = 1, value: int = 0) -> Frame:
    payload = bytes([value]) * (EXPECTED_CLIENT_WIDTH * EXPECTED_CLIENT_HEIGHT * 4)
    return Frame.from_raw(
        RawFrame(
            payload,
            EXPECTED_CLIENT_WIDTH,
            EXPECTED_CLIENT_HEIGHT,
            PixelFormat.BGRA8888,
        ),
        frame_id=frame_id,
        captured_monotonic_s=float(frame_id),
    )


def _trusted_exclusions() -> tuple[tuple[int, int, int, int], ...]:
    profile = load_varrock_east_iron_profile()
    return tuple(
        dict.fromkeys(
            (
                *varrock_east_iron_scene_excluded_regions(profile),
                *(policy.region for policy in GAMEPLAY_CHROME_POLICIES),
            )
        )
    )


def _analysis(*, matched_count: int) -> WideSceneRegistrationAnalysis:
    zones = (
        MacroZone.NORTH_WEST,
        MacroZone.NORTH_WEST,
        MacroZone.SOUTH_WEST,
        MacroZone.SOUTH_WEST,
        MacroZone.NORTH_EAST,
        MacroZone.NORTH_EAST,
    )
    return WideSceneRegistrationAnalysis(
        landmarks=tuple(
            WideLandmarkSearch(
                landmark_id=f"landmark-{index}",
                offset_x=0,
                offset_y=0,
                distance=0.01 if index < matched_count else 0.5,
                maximum_distance=0.12,
                matched=index < matched_count,
                zone=zone,
                searched_offsets=1,
            )
            for index, zone in enumerate(zones)
        ),
        best_shared=None,
        diagnosis=(
            WideRegistrationDiagnosis.CAMERA_TRANSFORM_NOT_TRANSLATION
            if matched_count >= 5
            else WideRegistrationDiagnosis.INSUFFICIENT_REGISTRATION_EVIDENCE
        ),
        detail="deterministic V2 test evidence",
        search_radius=96,
        coarse_step=4,
        refinement_radius=3,
    )


def _fit(
    *,
    scale: float = 1.0,
    rotation_degrees: float = 0.0,
    centre_shift_x: float = 0.0,
    centre_shift_y: float = 0.0,
    rms_residual_px: float = 1.0,
    maximum_residual_px: float = 2.0,
) -> CameraSimilarityFit:
    analysis = _analysis(matched_count=5)
    return CameraSimilarityFit(
        scale=scale,
        rotation_degrees=rotation_degrees,
        centre_shift_x=centre_shift_x,
        centre_shift_y=centre_shift_y,
        rms_residual_px=rms_residual_px,
        maximum_residual_px=maximum_residual_px,
        landmark_count=analysis.matched_count,
        matched_zones=analysis.matched_zones,
    )


def _base_refusal(
    reason: CameraGuidanceReason = CameraGuidanceReason.INSUFFICIENT_DISTRIBUTED_LANDMARKS,
    *,
    fit: CameraSimilarityFit | None = None,
    excluded_regions: tuple[tuple[int, int, int, int], ...] | None = None,
) -> WorldCameraGuidance:
    return WorldCameraGuidance(
        selector_id=CAMERA_GUIDANCE_ID,
        selector_version=CAMERA_GUIDANCE_VERSION,
        disposition=CameraGuidanceDisposition.INSUFFICIENT_GUIDANCE,
        reason=reason,
        detail="refused by trusted v1 evidence",
        axis=None,
        direction=None,
        fit=fit,
        analysis=(
            None
            if reason is CameraGuidanceReason.UNSUPPORTED_FRAME
            else _analysis(matched_count=5 if fit is not None else 3)
        ),
        excluded_regions=excluded_regions or _trusted_exclusions(),
    )


def _actionable_zoom() -> WorldCameraGuidance:
    fit = _fit(scale=1.02)
    return WorldCameraGuidance(
        selector_id=CAMERA_GUIDANCE_ID,
        selector_version=CAMERA_GUIDANCE_VERSION,
        disposition=CameraGuidanceDisposition.ACTIONABLE,
        reason=CameraGuidanceReason.ZOOM_SCALE_HIGH,
        detail="one reviewed negative detent",
        axis=CameraGuidanceAxis.ZOOM,
        direction=CameraGuidanceDirection.NEGATIVE,
        fit=fit,
        analysis=_analysis(matched_count=5),
        excluded_regions=_trusted_exclusions(),
    )


def _bind(
    monkeypatch: pytest.MonkeyPatch,
    value: WorldCameraGuidance,
) -> None:
    monkeypatch.setattr(
        guidance_v2_module,
        "evaluate_varrock_east_camera_guidance",
        lambda _frame: value,
    )


def _receipt(plan: CameraPlan) -> CameraPlanReceipt:
    action = plan.actions[0]
    return CameraPlanReceipt(
        plan=plan,
        preflight=CameraPreflightReceipt(
            focused=True,
            client_width=EXPECTED_CLIENT_WIDTH,
            client_height=EXPECTED_CLIENT_HEIGHT,
        ),
        action_receipts=(
            CameraActionReceipt(
                action_index=0,
                action=action,
                input_receipts=(
                    CameraInputReceipt(CameraInputOperation.COMPASS_CLICK, 1, 1),
                ),
            ),
        ),
    )


def test_north_bootstrap_is_reserved_once_and_completed_only_by_exact_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = _frame()
    _bind(monkeypatch, _base_refusal())
    session = CameraGuidanceV2Session()

    first = select_camera_guidance_v2(frame, session=session)
    second = select_camera_guidance_v2(frame, session=session)

    assert first.disposition is CameraGuidanceV2Disposition.ACTIONABLE_BOOTSTRAP
    assert first.reason is CameraGuidanceV2Reason.DETERMINISTIC_NORTH_BOOTSTRAP
    assert first.axis is CameraPrimitiveAxis.HEADING
    assert first.direction is None
    assert session.north_bootstrap_reserved
    assert not session.heading_normalized
    assert second.disposition is CameraGuidanceV2Disposition.INSUFFICIENT_GUIDANCE
    assert second.reason is CameraGuidanceV2Reason.NORTH_BOOTSTRAP_RESERVED
    plan = session.build_reserved_plan(first, frame, index=1)
    assert plan.actions == (CompassClick(*REVIEWED_COMPASS_POINT),)
    assert session.build_reserved_plan(first, frame, index=1) is plan

    receipt = _receipt(plan)
    session.record_north_receipt(first, frame, receipt)
    assert session.heading_normalized
    assert session.north_receipt is receipt
    after = select_camera_guidance_v2(frame, session=session)
    assert after.disposition is CameraGuidanceV2Disposition.INSUFFICIENT_GUIDANCE
    assert after.reason is CameraGuidanceV2Reason.INSUFFICIENT_DISTRIBUTED_EVIDENCE
    with pytest.raises(ValueError, match="not awaiting"):
        session.record_north_receipt(first, frame, receipt)


def test_wrong_receipt_cannot_complete_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = _frame()
    _bind(monkeypatch, _base_refusal())
    session = CameraGuidanceV2Session()
    guidance = select_camera_guidance_v2(frame, session=session)
    wrong_plan = CameraPlan(
        "wrong",
        (CompassClick(*REVIEWED_COMPASS_POINT),),
    )

    with pytest.raises(ValueError, match="exact reserved plan"):
        session.record_north_receipt(guidance, frame, _receipt(wrong_plan))
    assert not session.heading_normalized


def test_reviewed_v1_zoom_maps_to_exactly_one_detent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = _frame()
    _bind(monkeypatch, _actionable_zoom())
    result = select_camera_guidance_v2(frame, session=CameraGuidanceV2Session())

    assert result.disposition is CameraGuidanceV2Disposition.ACTIONABLE_CORRECTION
    assert result.reason is CameraGuidanceV2Reason.REVIEWED_ZOOM_SIGN
    assert result.axis is CameraPrimitiveAxis.ZOOM
    assert result.direction is CameraGuidanceDirection.NEGATIVE
    plan = build_camera_guidance_v2_plan(result, frame=frame, index=3)
    assert plan.actions == (CameraWheel(*REVIEWED_CAMERA_WHEEL_POINT, -1),)


@pytest.mark.parametrize(
    ("fit", "axis", "drag_axis"),
    [
        (_fit(rotation_degrees=2.0), CameraPrimitiveAxis.YAW, CameraDragAxis.HORIZONTAL),
        (_fit(centre_shift_y=32.0), CameraPrimitiveAxis.PITCH, CameraDragAxis.VERTICAL),
    ],
)
def test_dominant_uncalibrated_axis_permits_only_one_explicit_probe(
    monkeypatch: pytest.MonkeyPatch,
    fit: CameraSimilarityFit,
    axis: CameraPrimitiveAxis,
    drag_axis: CameraDragAxis,
) -> None:
    frame = _frame()
    _bind(
        monkeypatch,
        _base_refusal(CameraGuidanceReason.UNCALIBRATED_AXIS, fit=fit),
    )
    result = select_camera_guidance_v2(frame, session=CameraGuidanceV2Session())

    assert result.disposition is CameraGuidanceV2Disposition.CALIBRATION_REQUIRED
    assert result.reason is CameraGuidanceV2Reason.SIGNED_EFFECT_REQUIRED
    assert result.axis is axis
    assert result.direction is None
    with pytest.raises(ValueError, match="not an actionable correction"):
        build_camera_guidance_v2_plan(result, frame=frame, index=1)
    probe = build_camera_guidance_v2_probe(
        result,
        frame=frame,
        direction=CameraGuidanceDirection.POSITIVE,
        index=1,
    )
    assert probe.actions == (
        CameraMiddleDrag(drag_axis, CAMERA_GUIDANCE_V2_DRAG_PULSE_PIXELS),
    )


def test_competing_axes_refuse_without_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = _frame()
    _bind(
        monkeypatch,
        _base_refusal(
            CameraGuidanceReason.AMBIGUOUS_AXIS,
            fit=_fit(rotation_degrees=1.0, centre_shift_y=12.0),
        ),
    )
    result = select_camera_guidance_v2(frame, session=CameraGuidanceV2Session())

    assert result.disposition is CameraGuidanceV2Disposition.INSUFFICIENT_GUIDANCE
    assert result.reason is CameraGuidanceV2Reason.AMBIGUOUS_AXIS
    assert result.axis is None
    assert result.direction is None


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        (CameraGuidanceReason.WITHIN_DEADBAND, CameraGuidanceV2Reason.WITHIN_DEADBAND),
        (CameraGuidanceReason.AMBIGUOUS_AXIS, CameraGuidanceV2Reason.AMBIGUOUS_AXIS),
    ],
)
def test_fit_bearing_v1_refusal_reason_cannot_be_widened_to_a_probe(
    monkeypatch: pytest.MonkeyPatch,
    reason: CameraGuidanceReason,
    expected: CameraGuidanceV2Reason,
) -> None:
    frame = _frame()
    _bind(monkeypatch, _base_refusal(reason, fit=_fit(centre_shift_y=48.0)))

    result = select_camera_guidance_v2(frame, session=CameraGuidanceV2Session())

    assert result.disposition is CameraGuidanceV2Disposition.INSUFFICIENT_GUIDANCE
    assert result.reason is expected
    assert result.axis is None


@pytest.mark.parametrize(
    "reason",
    [CameraGuidanceReason.UNSUPPORTED_FRAME, CameraGuidanceReason.INCOHERENT_TRANSFORM],
)
def test_unsupported_or_incoherent_base_never_bootstraps(
    monkeypatch: pytest.MonkeyPatch,
    reason: CameraGuidanceReason,
) -> None:
    frame = _frame()
    _bind(monkeypatch, _base_refusal(reason))
    result = select_camera_guidance_v2(frame, session=CameraGuidanceV2Session())

    assert result.disposition is CameraGuidanceV2Disposition.INSUFFICIENT_GUIDANCE
    assert result.reason is CameraGuidanceV2Reason.UNSUPPORTED_OR_INCOHERENT
    assert result.axis is None


def test_v2_rejects_nonprofile_selector_and_incomplete_exclusions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = _frame()
    wrong_id = replace(_base_refusal(), selector_id="forged")
    _bind(monkeypatch, wrong_id)
    with pytest.raises(ValueError, match="profile-bound v1 selector"):
        select_camera_guidance_v2(frame, session=CameraGuidanceV2Session())

    _bind(monkeypatch, _base_refusal(excluded_regions=((0, 0, 1, 1),)))
    with pytest.raises(ValueError, match="every centralized"):
        select_camera_guidance_v2(frame, session=CameraGuidanceV2Session())


def test_action_token_cannot_be_forged_with_dataclass_replace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = _frame()
    _bind(monkeypatch, _base_refusal())
    genuine = select_camera_guidance_v2(frame, session=CameraGuidanceV2Session())

    with pytest.raises(ValueError, match="exact reviewed v1 zoom sign"):
        replace(
            genuine,
            disposition=CameraGuidanceV2Disposition.ACTIONABLE_CORRECTION,
            reason=CameraGuidanceV2Reason.REVIEWED_ZOOM_SIGN,
            axis=CameraPrimitiveAxis.ZOOM,
            direction=CameraGuidanceDirection.POSITIVE,
        )


def test_plan_builder_revalidates_exact_frame_identity_hash_and_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = _frame(frame_id=1, value=0)
    _bind(monkeypatch, _actionable_zoom())
    result = select_camera_guidance_v2(frame, session=CameraGuidanceV2Session())

    for other in (
        _frame(frame_id=2, value=0),
        _frame(frame_id=1, value=1),
    ):
        with pytest.raises(ValueError, match="exact decision frame"):
            build_camera_guidance_v2_plan(result, frame=other, index=1)


def test_bootstrap_receipt_and_token_are_exactly_session_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = _frame()
    _bind(monkeypatch, _base_refusal())
    first_session = CameraGuidanceV2Session()
    second_session = CameraGuidanceV2Session()
    first = select_camera_guidance_v2(frame, session=first_session)
    second = select_camera_guidance_v2(frame, session=second_session)
    first_plan = first_session.build_reserved_plan(first, frame, index=1)
    second_plan = second_session.build_reserved_plan(second, frame, index=1)

    assert first_plan == second_plan and first_plan is not second_plan
    with pytest.raises(ValueError, match="exact reserved plan"):
        second_session.record_north_receipt(second, frame, _receipt(first_plan))
    with pytest.raises(ValueError, match="exact token"):
        second_session.build_reserved_plan(first, frame, index=1)
    with pytest.raises(TypeError, match="dataclass"):
        replace(first_session)


def test_same_session_replace_forge_cannot_issue_another_bootstrap_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = _frame()
    _bind(monkeypatch, _base_refusal())
    session = CameraGuidanceV2Session()
    reserved = select_camera_guidance_v2(frame, session=session)
    refused = select_camera_guidance_v2(frame, session=session)
    forged = replace(
        refused,
        disposition=CameraGuidanceV2Disposition.ACTIONABLE_BOOTSTRAP,
        reason=CameraGuidanceV2Reason.DETERMINISTIC_NORTH_BOOTSTRAP,
        heading_was_normalized=False,
        axis=CameraPrimitiveAxis.HEADING,
        direction=None,
    )

    assert session.build_reserved_plan(reserved, frame, index=1) is session.north_plan
    with pytest.raises(ValueError, match="exact token"):
        session.build_reserved_plan(forged, frame, index=1)
    with pytest.raises(ValueError, match="session-owned"):
        build_camera_guidance_v2_plan(forged, frame=frame, index=1)


def test_incoherent_fit_cannot_be_replaced_into_a_probe_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = _frame()
    _bind(
        monkeypatch,
        _base_refusal(
            CameraGuidanceReason.INCOHERENT_TRANSFORM,
            fit=_fit(
                centre_shift_y=48.0,
                rms_residual_px=20.0,
                maximum_residual_px=30.0,
            ),
        ),
    )
    refusal = select_camera_guidance_v2(frame, session=CameraGuidanceV2Session())

    assert refusal.disposition is CameraGuidanceV2Disposition.INSUFFICIENT_GUIDANCE
    assert refusal.reason is CameraGuidanceV2Reason.UNSUPPORTED_OR_INCOHERENT
    with pytest.raises(ValueError, match="probe request"):
        replace(
            refusal,
            disposition=CameraGuidanceV2Disposition.CALIBRATION_REQUIRED,
            reason=CameraGuidanceV2Reason.SIGNED_EFFECT_REQUIRED,
            axis=CameraPrimitiveAxis.PITCH,
            direction=None,
        )


def test_evidence_types_and_no_authority_flags_are_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = _frame()
    _bind(monkeypatch, _base_refusal())
    result = select_camera_guidance_v2(frame, session=CameraGuidanceV2Session())

    assert not result.can_accept
    assert not result.can_validate_scene
    assert not result.can_expose_resources
    with pytest.raises(ValueError, match="transform_error is invalid"):
        replace(result, transform_error=cast(Any, "forged"))
    with pytest.raises(ValueError, match="direction is invalid"):
        replace(result, direction=cast(Any, "positive"))
    with pytest.raises(ValueError, match="captured Frame"):
        select_camera_guidance_v2(
            cast(Any, object()),
            session=CameraGuidanceV2Session(),
        )


def test_probe_and_plan_indices_are_hard_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = _frame()
    _bind(
        monkeypatch,
        _base_refusal(
            CameraGuidanceReason.UNCALIBRATED_AXIS,
            fit=_fit(centre_shift_y=32.0),
        ),
    )
    guidance = select_camera_guidance_v2(frame, session=CameraGuidanceV2Session())

    with pytest.raises(ValueError, match="positive integer"):
        build_camera_guidance_v2_probe(
            guidance,
            frame=frame,
            direction=CameraGuidanceDirection.POSITIVE,
            index=0,
        )
    _bind(monkeypatch, _base_refusal())
    with pytest.raises(ValueError, match="calibration probe"):
        build_camera_guidance_v2_probe(
            select_camera_guidance_v2(
                frame,
                session=CameraGuidanceV2Session(),
            ),
            frame=frame,
            direction=CameraGuidanceDirection.POSITIVE,
            index=1,
        )
