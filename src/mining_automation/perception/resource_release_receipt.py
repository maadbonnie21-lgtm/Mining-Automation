"""Source-owned resource release receipt boundary for constrained v1.

No receipt is currently issued.  The real campaign and every C2 source/review
gate remain open, the source-owned issuance switch is false, and no approved
record is packaged.  Runtime data cannot close those gates: the public loader
takes no arguments and only a later reviewed source change can install the one
exact immutable receipt.

The receipt is release-lineage metadata only.  It carries no observations,
regions, targets, frame state, inventory state, or action authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from enum import StrEnum
from typing import Final, NoReturn, SupportsIndex, cast, final

from ..capture.frame import PixelFormat
from .production_profiles import (
    VARROCK_EAST_IRON_DETECTOR_ID,
    VARROCK_EAST_IRON_DETECTOR_VERSION,
    VARROCK_EAST_IRON_PROFILE_ID,
    VARROCK_EAST_IRON_RESOURCE_IDS,
    load_varrock_east_iron_profile,
)
from .resource import RESOURCE_PROFILE_SCHEMA_VERSION

__all__ = [
    "RESOURCE_RELEASE_RECEIPT_SCHEMA_VERSION",
    "ResourceReleaseGate",
    "ResourceReleaseReceipt",
    "ResourceReleaseReceiptUnavailable",
    "load_source_owned_varrock_east_iron_release_receipt",
    "require_source_owned_varrock_east_iron_release_receipt",
]

RESOURCE_RELEASE_RECEIPT_SCHEMA_VERSION: Final[int] = 1

_RECEIPT_ID: Final[str] = (
    "resource-release-receipt:varrock-east-iron-v1@1.0.0"
)
_RELEASE_RECORD_ID: Final[str] = (
    "resource-release-record:varrock-east-iron-v1@1.0.0"
)
_SOURCE_OWNER: Final[str] = "mining-automation-perception"
_EXPECTED_LOCATION_ID: Final[str] = "varrock-east-mine"
_EXPECTED_CAPTURE_BACKEND: Final[str] = "windows-runelite"
_EXPECTED_CAPTURE_CONFIGURATION_ID: Final[str] = (
    "resource-release-campaign:varrock-east-iron-v1@1.1.0"
)
_EXPECTED_WIDTH: Final[int] = 1005
_EXPECTED_HEIGHT: Final[int] = 1078
_EXPECTED_REPORTED_DPI: Final[int] = 96
_EXPECTED_STOP_POLICY: Final[str] = "zero_targets_and_stop"
_SHA256_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}\Z")
_GIT_SHA_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{40}\Z")


class ResourceReleaseGate(StrEnum):
    """Every source-owned gate required before a receipt may exist."""

    C1_SOURCE_OWNED_CAMPAIGN = "c1_source_owned_campaign"
    C1_INDEPENDENT_REVIEWER_TRUTH = "c1_independent_reviewer_truth"
    C1_PRODUCTION_CONFORMANCE = "c1_production_conformance"
    C2_PERMANENT_REPLAY_ADOPTION = "c2_permanent_replay_adoption"
    C2_EXACT_ENVELOPE_REVIEW = "c2_exact_envelope_review"
    C2_EXACT_SOURCE_BINDINGS = "c2_exact_source_bindings"
    C2_FINAL_SOURCE_RELEASE_GRANT = "c2_final_source_release_grant"


_REQUIRED_RESOURCE_RELEASE_GATES: Final[tuple[ResourceReleaseGate, ...]] = tuple(
    ResourceReleaseGate
)

# This is the only current source configuration.  It deliberately cannot issue
# a receipt.  A future change must close every gate, enable issuance, package a
# canonical granted record, bind its independently reviewed digest, and receive
# review as one atomic source diff.
_OPEN_RESOURCE_RELEASE_GATES: Final[tuple[ResourceReleaseGate, ...]] = (
    _REQUIRED_RESOURCE_RELEASE_GATES
)
_RECEIPT_ISSUANCE_ALLOWED: Final[bool] = False
_SOURCE_OWNED_RELEASE_RECORD: Final[dict[str, object] | None] = None
_APPROVED_RECEIPT_RECORD_SHA256: Final[str | None] = None
_RECEIPT_ISSUER: Final[object] = object()


class ResourceReleaseReceiptUnavailable(RuntimeError):
    """Raised while any source-owned resource release gate remains open."""


def _strict_mapping(
    value: object,
    fields: set[str],
    *,
    label: str,
) -> dict[str, object]:
    if type(value) is not dict or set(cast(dict[object, object], value)) != fields:
        raise ResourceReleaseReceiptUnavailable(f"{label} fields changed")
    return cast(dict[str, object], value)


def _strict_nonempty_string(value: object, *, label: str) -> str:
    if type(value) is not str or not value.strip():
        raise ResourceReleaseReceiptUnavailable(f"{label} must be a plain string")
    return value


def _strict_exact_string(value: object, expected: str, *, label: str) -> str:
    if type(value) is not str or value != expected:
        raise ResourceReleaseReceiptUnavailable(f"{label} changed")
    return value


def _strict_sha256(value: object, *, label: str) -> str:
    if type(value) is not str or not _SHA256_PATTERN.fullmatch(value):
        raise ResourceReleaseReceiptUnavailable(
            f"{label} must be a lowercase SHA-256"
        )
    return value


def _strict_git_sha(value: object, *, label: str) -> str:
    if type(value) is not str or not _GIT_SHA_PATTERN.fullmatch(value):
        raise ResourceReleaseReceiptUnavailable(
            f"{label} must be a lowercase 40-character Git object ID"
        )
    return value


def _canonical_record_bytes(record: dict[str, object]) -> bytes:
    try:
        return json.dumps(
            record,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ResourceReleaseReceiptUnavailable(
            "source-owned resource release record is not canonical JSON"
        ) from exc


def _mapping_sha256(value: dict[str, object]) -> str:
    return hashlib.sha256(_canonical_record_bytes(value)).hexdigest()


def _validate_source_owned_record(record: object) -> dict[str, object]:
    root = _strict_mapping(
        record,
        {
            "schema_version",
            "receipt_id",
            "release_record_id",
            "source_owner",
            "detector",
            "c1_evidence",
            "retained_failure_replay",
            "approved_envelope",
            "source_bindings",
            "final_decision",
        },
        label="resource release record",
    )
    if type(root["schema_version"]) is not int or root["schema_version"] != 1:
        raise ResourceReleaseReceiptUnavailable(
            "resource release record schema changed"
        )
    _strict_exact_string(root["receipt_id"], _RECEIPT_ID, label="receipt ID")
    _strict_exact_string(
        root["release_record_id"],
        _RELEASE_RECORD_ID,
        label="release record ID",
    )
    _strict_exact_string(root["source_owner"], _SOURCE_OWNER, label="source owner")

    detector = _strict_mapping(
        root["detector"],
        {
            "detector_id",
            "detector_version",
            "profile_id",
            "profile_schema_version",
            "location_id",
            "resource_ids",
        },
        label="detector binding",
    )
    _strict_exact_string(
        detector["detector_id"],
        VARROCK_EAST_IRON_DETECTOR_ID,
        label="detector ID",
    )
    _strict_exact_string(
        detector["detector_version"],
        VARROCK_EAST_IRON_DETECTOR_VERSION,
        label="detector version",
    )
    _strict_exact_string(
        detector["profile_id"],
        VARROCK_EAST_IRON_PROFILE_ID,
        label="profile ID",
    )
    _strict_exact_string(
        detector["location_id"],
        _EXPECTED_LOCATION_ID,
        label="location ID",
    )
    if (
        type(detector["profile_schema_version"]) is not int
        or detector["profile_schema_version"] != RESOURCE_PROFILE_SCHEMA_VERSION
        or type(detector["resource_ids"]) is not list
        or any(
            type(resource_id) is not str
            for resource_id in cast(list[object], detector["resource_ids"])
        )
        or tuple(cast(list[object], detector["resource_ids"]))
        != VARROCK_EAST_IRON_RESOURCE_IDS
    ):
        raise ResourceReleaseReceiptUnavailable(
            "resource release detector/profile identity changed"
        )

    c1 = _strict_mapping(
        root["c1_evidence"],
        {
            "status",
            "source_owned_campaign",
            "independent_reviewer_truth",
            "production_conformance_passed",
            "review_package_manifest_sha256",
            "release_summary_sha256",
            "completion_seal_sha256",
            "followup_sha256",
        },
        label="C1 evidence binding",
    )
    _strict_exact_string(c1["status"], "CLOSED", label="C1 status")
    if (
        c1["source_owned_campaign"] is not True
        or c1["independent_reviewer_truth"] is not True
        or c1["production_conformance_passed"] is not True
    ):
        raise ResourceReleaseReceiptUnavailable("C1 resource evidence is not closed")
    for name in (
        "review_package_manifest_sha256",
        "release_summary_sha256",
        "completion_seal_sha256",
        "followup_sha256",
    ):
        _strict_sha256(c1[name], label=f"C1 {name}")

    replay = _strict_mapping(
        root["retained_failure_replay"],
        {
            "status",
            "permanent_adoption_complete",
            "unresolved_case_ids",
            "adoption_manifest_sha256",
            "promoted_fixture_tree_sha",
            "evaluator_test_tree_sha",
            "permanent_replay_root_sha256",
        },
        label="retained-failure replay binding",
    )
    _strict_exact_string(replay["status"], "CLOSED", label="replay status")
    if (
        replay["permanent_adoption_complete"] is not True
        or type(replay["unresolved_case_ids"]) is not list
        or cast(list[object], replay["unresolved_case_ids"])
    ):
        raise ResourceReleaseReceiptUnavailable(
            "retained-failure replay adoption is incomplete"
        )
    replay_components: dict[str, object] = {
        "adoption_manifest_sha256": _strict_sha256(
            replay["adoption_manifest_sha256"],
            label="replay adoption manifest",
        ),
        "promoted_fixture_tree_sha": _strict_git_sha(
            replay["promoted_fixture_tree_sha"],
            label="promoted fixture tree",
        ),
        "evaluator_test_tree_sha": _strict_git_sha(
            replay["evaluator_test_tree_sha"],
            label="replay evaluator test tree",
        ),
    }
    replay_root = _strict_sha256(
        replay["permanent_replay_root_sha256"],
        label="permanent replay root",
    )
    if replay_root != _mapping_sha256(replay_components):
        raise ResourceReleaseReceiptUnavailable(
            "permanent replay root does not bind its exact components"
        )

    envelope = _strict_mapping(
        root["approved_envelope"],
        {
            "status",
            "envelope_root_sha256",
            "frame_width",
            "frame_height",
            "pixel_format",
            "reported_dpi",
            "window_class",
            "capture_backend",
            "capture_configuration_id",
            "renderer_id",
            "automatic_camera_recovery_allowed",
            "unsupported_or_uncertain_view_policy",
        },
        label="approved envelope",
    )
    profile = load_varrock_east_iron_profile()
    _strict_exact_string(envelope["status"], "APPROVED", label="envelope status")
    _strict_exact_string(
        envelope["pixel_format"],
        PixelFormat.BGRA8888.value,
        label="envelope pixel format",
    )
    _strict_exact_string(
        envelope["capture_backend"],
        _EXPECTED_CAPTURE_BACKEND,
        label="capture backend",
    )
    _strict_exact_string(
        envelope["capture_configuration_id"],
        _EXPECTED_CAPTURE_CONFIGURATION_ID,
        label="capture configuration ID",
    )
    _strict_exact_string(
        envelope["unsupported_or_uncertain_view_policy"],
        _EXPECTED_STOP_POLICY,
        label="unsupported-view policy",
    )
    if (
        type(envelope["frame_width"]) is not int
        or envelope["frame_width"] != _EXPECTED_WIDTH == profile.frame_width
        or type(envelope["frame_height"]) is not int
        or envelope["frame_height"] != _EXPECTED_HEIGHT == profile.frame_height
        or envelope["pixel_format"] != profile.pixel_format.value
        or type(envelope["reported_dpi"]) is not int
        or envelope["reported_dpi"] != _EXPECTED_REPORTED_DPI
        or envelope["automatic_camera_recovery_allowed"] is not False
    ):
        raise ResourceReleaseReceiptUnavailable(
            "approved constrained-v1 envelope changed"
        )
    window_class = _strict_nonempty_string(
        envelope["window_class"], label="window class"
    )
    renderer_id = _strict_nonempty_string(envelope["renderer_id"], label="renderer ID")
    envelope_components = {
        key: envelope[key]
        for key in (
            "status",
            "frame_width",
            "frame_height",
            "pixel_format",
            "reported_dpi",
            "capture_backend",
            "capture_configuration_id",
            "automatic_camera_recovery_allowed",
            "unsupported_or_uncertain_view_policy",
        )
    }
    envelope_components["window_class"] = window_class
    envelope_components["renderer_id"] = renderer_id
    envelope_root = _strict_sha256(
        envelope["envelope_root_sha256"], label="envelope root"
    )
    if envelope_root != _mapping_sha256(envelope_components):
        raise ResourceReleaseReceiptUnavailable(
            "approved envelope root does not bind its exact components"
        )

    bindings = _strict_mapping(
        root["source_bindings"],
        {
            "status",
            "source_commit_sha",
            "source_tree_sha",
            "source_binding_root_sha256",
            "detector_source_blob_sha",
            "packaged_profile_blob_sha",
            "reviewed_dataset_manifest_blob_sha",
        },
        label="source bindings",
    )
    _strict_exact_string(
        bindings["status"], "COMPLETE", label="source binding status"
    )
    for name in (
        "source_commit_sha",
        "source_tree_sha",
        "detector_source_blob_sha",
        "packaged_profile_blob_sha",
        "reviewed_dataset_manifest_blob_sha",
    ):
        _strict_git_sha(bindings[name], label=f"source binding {name}")
    source_binding_root = _strict_sha256(
        bindings["source_binding_root_sha256"],
        label="source binding root",
    )
    source_binding_components = {
        key: bindings[key]
        for key in (
            "status",
            "source_commit_sha",
            "source_tree_sha",
            "detector_source_blob_sha",
            "packaged_profile_blob_sha",
            "reviewed_dataset_manifest_blob_sha",
        )
    }
    if source_binding_root != _mapping_sha256(source_binding_components):
        raise ResourceReleaseReceiptUnavailable(
            "source binding root does not bind its exact Git components"
        )

    decision = _strict_mapping(
        root["final_decision"],
        {
            "status",
            "release_eligible",
            "activation_allowed",
            "unresolved_condition_ids",
            "decision_root_sha256",
            "lead_approval_root_sha256",
        },
        label="final resource release decision",
    )
    _strict_exact_string(
        decision["status"], "GRANTED", label="source release decision status"
    )
    if (
        decision["release_eligible"] is not True
        or decision["activation_allowed"] is not False
        or type(decision["unresolved_condition_ids"]) is not list
        or cast(list[object], decision["unresolved_condition_ids"])
    ):
        raise ResourceReleaseReceiptUnavailable(
            "final source release decision is not a non-activating grant"
        )
    _strict_sha256(decision["decision_root_sha256"], label="decision root")
    _strict_sha256(
        decision["lead_approval_root_sha256"],
        label="lead approval root",
    )
    return root


@final
class ResourceReleaseReceipt(tuple[object, ...]):
    """Immutable nominal source receipt with no public constructor."""

    __slots__ = ()

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise TypeError(
            "ResourceReleaseReceipt has no public constructor; use the "
            "source-owned no-argument loader"
        )

    def __init_subclass__(cls) -> None:
        raise TypeError("ResourceReleaseReceipt is sealed")

    def __copy__(self) -> ResourceReleaseReceipt:
        return self

    def __deepcopy__(self, memo: object) -> ResourceReleaseReceipt:
        del memo
        return self

    def __reduce__(self) -> NoReturn:
        raise TypeError("ResourceReleaseReceipt cannot be serialized")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        del protocol
        raise TypeError("ResourceReleaseReceipt cannot be serialized")

    def __repr__(self) -> str:
        return (
            "ResourceReleaseReceipt("
            f"receipt_id={self.receipt_id!r}, "
            f"release_record_sha256={self.release_record_sha256!r})"
        )

    @property
    def receipt_id(self) -> str:
        return cast(str, self[0])

    @property
    def release_record_id(self) -> str:
        return cast(str, self[1])

    @property
    def release_record_sha256(self) -> str:
        return cast(str, self[2])

    @property
    def detector_id(self) -> str:
        return cast(str, self[3])

    @property
    def detector_version(self) -> str:
        return cast(str, self[4])

    @property
    def profile_id(self) -> str:
        return cast(str, self[5])

    @property
    def profile_schema_version(self) -> int:
        return cast(int, self[6])

    @property
    def location_id(self) -> str:
        return cast(str, self[7])

    @property
    def resource_ids(self) -> tuple[str, ...]:
        return cast(tuple[str, ...], self[8])

    @property
    def approved_envelope_root_sha256(self) -> str:
        return cast(str, self[9])

    @property
    def c1_review_package_manifest_sha256(self) -> str:
        return cast(str, self[10])

    @property
    def c1_release_summary_sha256(self) -> str:
        return cast(str, self[11])

    @property
    def c1_completion_seal_sha256(self) -> str:
        return cast(str, self[12])

    @property
    def c1_followup_sha256(self) -> str:
        return cast(str, self[13])

    @property
    def permanent_replay_root_sha256(self) -> str:
        return cast(str, self[14])

    @property
    def source_binding_root_sha256(self) -> str:
        return cast(str, self[15])

    @property
    def source_commit_sha(self) -> str:
        return cast(str, self[16])

    @property
    def final_decision_root_sha256(self) -> str:
        return cast(str, self[17])

    @property
    def lead_approval_root_sha256(self) -> str:
        return cast(str, self[18])

    @property
    def schema_version(self) -> int:
        return cast(int, self[19])


def _build_receipt(record: dict[str, object], record_sha256: str) -> ResourceReleaseReceipt:
    detector = cast(dict[str, object], record["detector"])
    c1 = cast(dict[str, object], record["c1_evidence"])
    replay = cast(dict[str, object], record["retained_failure_replay"])
    envelope = cast(dict[str, object], record["approved_envelope"])
    bindings = cast(dict[str, object], record["source_bindings"])
    decision = cast(dict[str, object], record["final_decision"])
    values = (
        record["receipt_id"],
        record["release_record_id"],
        record_sha256,
        detector["detector_id"],
        detector["detector_version"],
        detector["profile_id"],
        detector["profile_schema_version"],
        detector["location_id"],
        tuple(cast(list[str], detector["resource_ids"])),
        envelope["envelope_root_sha256"],
        c1["review_package_manifest_sha256"],
        c1["release_summary_sha256"],
        c1["completion_seal_sha256"],
        c1["followup_sha256"],
        replay["permanent_replay_root_sha256"],
        bindings["source_binding_root_sha256"],
        bindings["source_commit_sha"],
        decision["decision_root_sha256"],
        decision["lead_approval_root_sha256"],
        RESOURCE_RELEASE_RECEIPT_SCHEMA_VERSION,
        _RECEIPT_ISSUER,
    )
    return tuple.__new__(ResourceReleaseReceipt, values)


def _configured_source_receipt() -> ResourceReleaseReceipt | None:
    if (
        _OPEN_RESOURCE_RELEASE_GATES == _REQUIRED_RESOURCE_RELEASE_GATES
        and _RECEIPT_ISSUANCE_ALLOWED is False
        and _SOURCE_OWNED_RELEASE_RECORD is None
        and _APPROVED_RECEIPT_RECORD_SHA256 is None
    ):
        return None
    if type(_OPEN_RESOURCE_RELEASE_GATES) is not tuple or any(
        type(gate) is not ResourceReleaseGate
        for gate in _OPEN_RESOURCE_RELEASE_GATES
    ):
        raise ResourceReleaseReceiptUnavailable(
            "source-owned open resource release gates changed type"
        )
    if _OPEN_RESOURCE_RELEASE_GATES:
        raise ResourceReleaseReceiptUnavailable(
            "resource release gates remain open: "
            + ", ".join(gate.value for gate in _OPEN_RESOURCE_RELEASE_GATES)
        )
    if _RECEIPT_ISSUANCE_ALLOWED is not True:
        raise ResourceReleaseReceiptUnavailable(
            "source-owned resource receipt issuance is disabled"
        )
    if _SOURCE_OWNED_RELEASE_RECORD is None:
        raise ResourceReleaseReceiptUnavailable(
            "no source-owned granted resource release record is packaged"
        )
    expected_digest = _strict_sha256(
        _APPROVED_RECEIPT_RECORD_SHA256,
        label="approved resource release record digest",
    )
    if type(_SOURCE_OWNED_RELEASE_RECORD) is not dict:
        raise ResourceReleaseReceiptUnavailable(
            "source-owned resource release record type changed"
        )
    # Serialize once, then parse a private plain snapshot. Validation, hashing,
    # and receipt construction never reread the mutable source mapping.
    payload = _canonical_record_bytes(_SOURCE_OWNED_RELEASE_RECORD)
    actual_digest = hashlib.sha256(payload).hexdigest()
    if actual_digest != expected_digest:
        raise ResourceReleaseReceiptUnavailable(
            "source-owned resource release record digest changed"
        )
    try:
        decoded: object = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ResourceReleaseReceiptUnavailable(
            "source-owned resource release record snapshot is invalid"
        ) from exc
    record = _validate_source_owned_record(decoded)
    if _canonical_record_bytes(record) != payload:
        raise ResourceReleaseReceiptUnavailable(
            "source-owned resource release record snapshot changed"
        )
    return _build_receipt(record, actual_digest)


# Import-time construction is intentional: changing runtime data or monkeypatching
# a gate cannot create a new capability.  A valid singleton can appear only after
# an atomic reviewed source change followed by a fresh process import.
_SOURCE_OWNED_RECEIPT_SINGLETON: Final[ResourceReleaseReceipt | None] = (
    _configured_source_receipt()
)


def _receipt_accessors(
    configured: ResourceReleaseReceipt | None,
    open_gates: tuple[ResourceReleaseGate, ...],
) -> tuple[
    Callable[[], ResourceReleaseReceipt],
    Callable[[object], ResourceReleaseReceipt],
]:
    expected = configured
    expected_projection = None if expected is None else tuple(expected)
    unavailable_reason = (
        "resource release gates remain open: "
        + ", ".join(gate.value for gate in open_gates)
        if open_gates
        else "no source-owned granted resource release receipt is packaged"
    )

    def load_source_owned_varrock_east_iron_release_receipt(
    ) -> ResourceReleaseReceipt:
        """Load the one exact packaged receipt; no caller input can select it."""

        if expected is None or expected_projection is None:
            raise ResourceReleaseReceiptUnavailable(unavailable_reason)
        if (
            type(expected) is not ResourceReleaseReceipt
            or tuple(expected) != expected_projection
        ):
            raise ResourceReleaseReceiptUnavailable(
                "import-time source-owned resource receipt changed"
            )
        return expected

    def require_source_owned_varrock_east_iron_release_receipt(
        value: object,
    ) -> ResourceReleaseReceipt:
        """Require identity with the packaged singleton for Issue #14."""

        loaded = load_source_owned_varrock_east_iron_release_receipt()
        if type(value) is not ResourceReleaseReceipt or value is not loaded:
            raise ResourceReleaseReceiptUnavailable(
                "resource release receipt is not the source-owned singleton"
            )
        return loaded

    return (
        load_source_owned_varrock_east_iron_release_receipt,
        require_source_owned_varrock_east_iron_release_receipt,
    )


(
    load_source_owned_varrock_east_iron_release_receipt,
    require_source_owned_varrock_east_iron_release_receipt,
) = _receipt_accessors(
    _SOURCE_OWNED_RECEIPT_SINGLETON,
    _OPEN_RESOURCE_RELEASE_GATES,
)
