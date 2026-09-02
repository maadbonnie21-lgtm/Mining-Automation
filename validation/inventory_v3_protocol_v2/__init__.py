"""Versioned Inventory V3 independent-validation protocol coordinator.

This package contains release-protocol orchestration only.  The frozen V3
classifier and the single locked v1 evaluator remain the source of detector
behavior and conformance results.
"""

from .protocol import (
    InventoryV3ProtocolV2Error,
    ProtocolV2Paths,
    build_live_authorization_proposal,
    build_protocol_v2_lock,
    evaluate_locked_protocol_v2,
    finalize_acquisition,
    preflight_source_metadata,
    prepare_approval_request,
    prepare_reviewer_intake,
    publish_reviewed_package,
    record_reviewer_submission,
    run_passive_capture_protocol_v2,
    verify_live_authorization,
    verify_protocol_v2_repository,
)

__all__ = [
    "InventoryV3ProtocolV2Error",
    "ProtocolV2Paths",
    "build_live_authorization_proposal",
    "build_protocol_v2_lock",
    "evaluate_locked_protocol_v2",
    "finalize_acquisition",
    "prepare_approval_request",
    "prepare_reviewer_intake",
    "preflight_source_metadata",
    "publish_reviewed_package",
    "record_reviewer_submission",
    "run_passive_capture_protocol_v2",
    "verify_live_authorization",
    "verify_protocol_v2_repository",
]
