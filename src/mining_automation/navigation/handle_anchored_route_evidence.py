"""Handle-anchored Windows writer for navigation route-evidence transactions.

The accepted pathname writer remains synthetic-only and requires a trusted
parent namespace.  This module supplies the separate native Windows boundary
whose fresh transaction root and every child are created relative to retained
directory handles. Unsupported platforms, missing APIs, and unsupported parent
storage fail before root reservation. A capability failure discovered only on
the atomically created root can leave that empty owned root, but occurs before
sequencer/source access or evidence bytes.

Writer eligibility is not route release authority.  The transactions produced
here still contain the current offline synthetic evidence role, no input
authority, and no activation capability.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final, Literal

from .checkpoint_evidence import CheckpointDetector
from .durable_route_evidence import (
    DurableAcquisitionFilesystemExpectation,
    DurableAcquisitionTransaction,
    DurableEvidenceError,
    DurableReviewTransaction,
    _absolute_path_once,
    _begin_durable_acquisition_with_namespace_factory,
    _begin_durable_review_with_namespace_factory,
    _NamespaceFactory,
)
from .passive_campaign import (
    PassiveCaptureSource,
    PassiveMonotonicClock,
)
from .route_evidence import RouteEvidenceCampaignPlan

__all__ = [
    "HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE",
    "HANDLE_ANCHORED_WRITER_NAMESPACE_CONTRACT",
    "HANDLE_ANCHORED_WRITER_PROCESS_INTEGRITY_REQUIRED",
    "HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM",
    "HandleAnchoredEvidenceCapabilityError",
    "begin_handle_anchored_acquisition",
    "begin_handle_anchored_review",
]

HANDLE_ANCHORED_WRITER_NAMESPACE_CONTRACT: Final[str] = (
    "windows_nt_handle_relative_no_follow_fresh_directory_v1"
)
HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM: Final[Literal["win32"]] = "win32"
HANDLE_ANCHORED_WRITER_PROCESS_INTEGRITY_REQUIRED: Final[Literal[True]] = True
HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE: Final[Literal[False]] = False


class HandleAnchoredEvidenceCapabilityError(DurableEvidenceError):
    """The host cannot prove fresh-directory ownership before evidence writes."""


def _namespace_factory() -> _NamespaceFactory:
    if sys.platform != HANDLE_ANCHORED_WRITER_SUPPORTED_PLATFORM:
        raise HandleAnchoredEvidenceCapabilityError(
            "atomic fresh-directory handle claim is unavailable on this platform"
        )
    try:
        from ._windows_handle_anchored import _WindowsHandleAnchoredNamespace
    except (AttributeError, ImportError, OSError) as exc:
        raise HandleAnchoredEvidenceCapabilityError(
            "Windows handle-anchored writer capabilities are unavailable"
        ) from exc
    return _WindowsHandleAnchoredNamespace


def begin_handle_anchored_acquisition(
    root: str | Path,
    plan: RouteEvidenceCampaignPlan,
    source: PassiveCaptureSource,
    detector: CheckpointDetector,
    clock: PassiveMonotonicClock,
    *,
    started_monotonic_s: float,
) -> DurableAcquisitionTransaction:
    """Begin one Windows handle-owned offline acquisition transaction."""

    factory = _namespace_factory()
    root_path = _absolute_path_once(root, "handle-anchored acquisition root")
    return _begin_durable_acquisition_with_namespace_factory(
        root_path,
        plan,
        source,
        detector,
        clock,
        started_monotonic_s=started_monotonic_s,
        namespace_factory=factory,
    )


def begin_handle_anchored_review(
    root: str | Path,
    acquisition_root: str | Path,
    acquisition_expectation: DurableAcquisitionFilesystemExpectation,
    *,
    review_id: str,
    reviewer_id: str,
    started_at_utc: str,
) -> DurableReviewTransaction:
    """Begin one independently rooted Windows handle-owned review transaction."""

    factory = _namespace_factory()
    review_path = _absolute_path_once(root, "handle-anchored review root")
    return _begin_durable_review_with_namespace_factory(
        review_path,
        acquisition_root,
        acquisition_expectation,
        review_id=review_id,
        reviewer_id=reviewer_id,
        started_at_utc=started_at_utc,
        namespace_factory=factory,
    )
