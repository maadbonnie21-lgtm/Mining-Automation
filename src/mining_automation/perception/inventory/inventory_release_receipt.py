"""Source-owned inventory release receipt boundary for the first mining slice.

No receipt is currently issued. Every live Protocol V2, review, approval,
production-binding, and cross-perception gate remains open. Runtime data cannot
close those gates: the public loader takes no arguments, and only one later,
reviewed source change can install the exact immutable singleton.

The receipt carries release-lineage metadata only. It contains no pixels,
InventoryState, WorldState, interaction region, target, or action authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from enum import StrEnum
from typing import Final, NoReturn, SupportsIndex, cast, final

__all__ = [
    "INVENTORY_RELEASE_RECEIPT_SCHEMA_VERSION",
    "InventoryReleaseGate",
    "InventoryReleaseReceipt",
    "InventoryReleaseReceiptUnavailable",
    "load_source_owned_inventory_release_receipt",
    "require_source_owned_inventory_release_receipt",
]

INVENTORY_RELEASE_RECEIPT_SCHEMA_VERSION: Final[int] = 1

_RECEIPT_ID: Final[str] = "inventory-release-receipt:inventory-positive-v3@1.0.0"
_RELEASE_RECORD_ID: Final[str] = "inventory-release-record:inventory-positive-v3@1.0.0"
_SOURCE_OWNER: Final[str] = "mining-automation-perception-inventory"
_EXPECTED_PROTOCOL_V2_LOCK_COMMIT_SHA: Final[str] = (
    "66c7e9536539979bc60e17f02f026eb64ebf0768"
)
_EXPECTED_PROTOCOL_V2_LOCK_SHA256: Final[str] = (
    "60ff2c511e46be3b87df4e0d9e4f705d897a4181f9152f2729ee90f6c45f8cf5"
)
_EXPECTED_C3_REHEARSAL_HEAD_SHA: Final[str] = (
    "76d47af4213a9990054b3beb5ccb0285e3138b79"
)
_EXPECTED_C4_PREPARATION_HEAD_SHA: Final[str] = (
    "74e2becd41af6b63b230ff11b07536d5da61aa80"
)
_EXPECTED_CAPACITY: Final[int] = 28
_EXPECTED_PUBLICATION_FLOOR: Final[float] = 0.8
_SHA256_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}\Z")
_GIT_SHA_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{40}\Z")


class InventoryReleaseGate(StrEnum):
    """Source-owned prerequisites that must all close before issuance."""

    LIVE_PROTOCOL_V2_AUTHORIZATION = "live_protocol_v2_authorization"
    LIVE_PROTOCOL_V2_CAMPAIGN_EXECUTION = "live_protocol_v2_campaign_execution"
    FINALIZED_CAMPAIGN_PACKAGE = "finalized_campaign_package"
    INDEPENDENT_REVIEWER_TRUTH = "independent_reviewer_truth"
    TERMINAL_CONFORMANCE_PASS = "terminal_conformance_pass"
    SOURCE_APPROVAL = "source_approval"
    PRODUCTION_IDENTITY_APPROVAL = "production_identity_approval"
    PRODUCTION_BINDING = "production_binding"
    RESOURCE_PERCEPTION_RELEASE = "resource_perception_release"


_REQUIRED_INVENTORY_RELEASE_GATES: Final[tuple[InventoryReleaseGate, ...]] = tuple(
    InventoryReleaseGate
)
_OPEN_INVENTORY_RELEASE_GATES: Final[tuple[InventoryReleaseGate, ...]] = (
    _REQUIRED_INVENTORY_RELEASE_GATES
)
_RECEIPT_ISSUANCE_ALLOWED: Final[bool] = False
_SOURCE_OWNED_RELEASE_RECORD: Final[dict[str, object] | None] = None
_APPROVED_RELEASE_RECORD_SHA256: Final[str | None] = None
_RECEIPT_ISSUER: Final[object] = object()

_STAGE_ORDER: Final[tuple[str, ...]] = (
    "authorization",
    "campaign",
    "review",
    "terminal_evaluation",
    "source_approval",
    "production_identity_approval",
    "production_binding",
)
_STAGE_STATUS: Final[dict[str, str]] = {
    "authorization": "AUTHORIZED",
    "campaign": "FINALIZED",
    "review": "ACCEPTED",
    "terminal_evaluation": "CONFORMANCE_PASSED_SOURCE_APPROVAL_REQUIRED",
    "source_approval": "APPROVED",
    "production_identity_approval": "APPROVED",
    "production_binding": "BOUND",
}
_STAGE_SHA_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "authorization": (),
    "campaign": (
        "campaign_manifest_sha256",
        "acquisition_record_sha256",
        "acquisition_package_tree_sha256",
        "completion_seal_sha256",
        "session_report_sha256",
        "capture_environment_sha256",
        "producer_identity_sha256",
    ),
    "review": (
        "reviewed_package_tree_sha256",
        "reviewer_truth_sha256",
        "validation_package_sha256",
        "review_submission_sha256",
    ),
    "terminal_evaluation": (
        "terminal_result_sha256",
        "result_package_tree_sha256",
        "frozen_evaluator_report_sha256",
    ),
    "source_approval": (
        "approval_request_sha256",
        "approval_registry_sha256",
    ),
    "production_identity_approval": (
        "identity_proposal_sha256",
        "record_sha256",
        "record_sidecar_sha256",
    ),
    "production_binding": (
        "record_sha256",
        "record_sidecar_sha256",
        "build_identity_sha256",
        "capture_environment_identity_sha256",
        "detector_identity_sha256",
        "inventory_configuration_identity_sha256",
        "observation_adapter_identity_sha256",
        "profile_identity_sha256",
    ),
}
_STAGE_GIT_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "authorization": (
        "legacy_registry_git_blob",
        "protocol_v2_registry_git_blob",
        "protocol_v2_registry_sidecar_git_blob",
    ),
    "campaign": (),
    "review": (),
    "terminal_evaluation": (),
    "source_approval": (
        "approval_registry_git_blob",
        "approval_registry_sidecar_git_blob",
    ),
    "production_identity_approval": (
        "record_git_blob",
        "record_sidecar_git_blob",
    ),
    "production_binding": (
        "record_git_blob",
        "record_sidecar_git_blob",
        "build_git_commit_sha",
    ),
}
_STAGE_TEXT_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "authorization": ("opaque_receipt_id",),
    "campaign": (),
    "review": (),
    "terminal_evaluation": ("outcome",),
    "source_approval": ("approval_id",),
    "production_identity_approval": ("approval_id",),
    "production_binding": (
        "binding_id",
        "source_approval_id",
        "production_identity_approval_id",
    ),
}
_STAGE_BOOL_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "authorization": (),
    "campaign": (),
    "review": (),
    "terminal_evaluation": ("one_shot_terminal", "retry_allowed"),
    "source_approval": (),
    "production_identity_approval": (),
    "production_binding": (),
}


class InventoryReleaseReceiptUnavailable(RuntimeError):
    """Raised while an inventory release prerequisite remains unresolved."""


def _strict_mapping(
    value: object,
    fields: set[str],
    *,
    label: str,
) -> dict[str, object]:
    if type(value) is not dict or set(cast(dict[object, object], value)) != fields:
        raise InventoryReleaseReceiptUnavailable(f"{label} fields changed")
    return cast(dict[str, object], value)


def _strict_nonempty_string(value: object, *, label: str) -> str:
    if type(value) is not str or not value.strip():
        raise InventoryReleaseReceiptUnavailable(f"{label} must be a plain nonempty string")
    return value


def _strict_exact_string(value: object, expected: str, *, label: str) -> str:
    if type(value) is not str or value != expected:
        raise InventoryReleaseReceiptUnavailable(f"{label} changed")
    return value


def _strict_sha256(value: object, *, label: str) -> str:
    if type(value) is not str or not _SHA256_PATTERN.fullmatch(value):
        raise InventoryReleaseReceiptUnavailable(f"{label} must be a lowercase SHA-256")
    return value


def _strict_git_sha(value: object, *, label: str) -> str:
    if type(value) is not str or not _GIT_SHA_PATTERN.fullmatch(value):
        raise InventoryReleaseReceiptUnavailable(
            f"{label} must be a lowercase 40-character Git object ID"
        )
    return value


def _strict_bool(value: object, expected: bool, *, label: str) -> bool:
    if type(value) is not bool or value is not expected:
        raise InventoryReleaseReceiptUnavailable(f"{label} changed")
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
        raise InventoryReleaseReceiptUnavailable(
            "source-owned inventory release record is not canonical JSON"
        ) from exc


def _mapping_sha256(value: dict[str, object]) -> str:
    return hashlib.sha256(_canonical_record_bytes(value)).hexdigest()


def _validate_root(section: dict[str, object], root_field: str, label: str) -> str:
    expected = _strict_sha256(section[root_field], label=f"{label} root")
    projection = {key: value for key, value in section.items() if key != root_field}
    if _mapping_sha256(projection) != expected:
        raise InventoryReleaseReceiptUnavailable(
            f"{label} root does not bind its exact components"
        )
    return expected


def _same(value: object, expected: str, *, label: str) -> str:
    actual = _strict_nonempty_string(value, label=label)
    if actual != expected:
        raise InventoryReleaseReceiptUnavailable(f"{label} crossed release lineages")
    return actual


def _validate_stage(
    name: str,
    raw_stage: object,
    identifiers: tuple[str, str, str, str] | None,
) -> tuple[dict[str, object], tuple[str, str, str, str], str]:
    evidence_fields = (
        set(_STAGE_SHA_FIELDS[name])
        | set(_STAGE_GIT_FIELDS[name])
        | set(_STAGE_TEXT_FIELDS[name])
        | set(_STAGE_BOOL_FIELDS[name])
    )
    stage = _strict_mapping(
        raw_stage,
        {
            "status",
            "authorization_id",
            "campaign_id",
            "dataset_id",
            "session_id",
            "actor_id",
            "git_commit_sha",
            "evidence",
            "stage_root_sha256",
        },
        label=f"{name} stage",
    )
    _strict_exact_string(stage["status"], _STAGE_STATUS[name], label=f"{name} status")
    current_identifiers = (
        _strict_nonempty_string(
            stage["authorization_id"], label=f"{name} authorization_id"
        ),
        _strict_nonempty_string(stage["campaign_id"], label=f"{name} campaign_id"),
        _strict_nonempty_string(stage["dataset_id"], label=f"{name} dataset_id"),
        _strict_nonempty_string(stage["session_id"], label=f"{name} session_id"),
    )
    if identifiers is not None and current_identifiers != identifiers:
        raise InventoryReleaseReceiptUnavailable(f"{name} stage crossed release lineages")
    _strict_nonempty_string(stage["actor_id"], label=f"{name} actor ID")
    _strict_git_sha(stage["git_commit_sha"], label=f"{name} Git commit")
    evidence = _strict_mapping(stage["evidence"], evidence_fields, label=f"{name} evidence")
    for field in _STAGE_SHA_FIELDS[name]:
        _strict_sha256(evidence[field], label=f"{name} evidence {field}")
    for field in _STAGE_GIT_FIELDS[name]:
        _strict_git_sha(evidence[field], label=f"{name} evidence {field}")
    for field in _STAGE_TEXT_FIELDS[name]:
        _strict_nonempty_string(evidence[field], label=f"{name} evidence {field}")
    for field in _STAGE_BOOL_FIELDS[name]:
        if type(evidence[field]) is not bool:
            raise InventoryReleaseReceiptUnavailable(
                f"{name} evidence {field} must be an exact boolean"
            )
    stage_root = _validate_root(stage, "stage_root_sha256", f"{name} stage")
    return stage, current_identifiers, stage_root


def _validate_source_owned_record(record: object) -> dict[str, object]:
    root = _strict_mapping(
        record,
        {
            "schema_version",
            "receipt_id",
            "release_record_id",
            "source_owner",
            "protocol_lineage",
            "inventory_contract",
            "stages",
            "resource_release",
            "final_decision",
        },
        label="inventory release record",
    )
    if type(root["schema_version"]) is not int or root["schema_version"] != 1:
        raise InventoryReleaseReceiptUnavailable("inventory release record schema changed")
    _strict_exact_string(root["receipt_id"], _RECEIPT_ID, label="receipt ID")
    _strict_exact_string(root["release_record_id"], _RELEASE_RECORD_ID, label="record ID")
    _strict_exact_string(root["source_owner"], _SOURCE_OWNER, label="source owner")

    lineage = _strict_mapping(
        root["protocol_lineage"],
        {
            "protocol_v2_lock_commit_sha",
            "protocol_v2_lock_sha256",
            "c3_rehearsal_head_sha",
            "c4_preparation_head_sha",
            "live_authorization_parent_sha",
            "live_authorization_commit_sha",
            "capture_execution_head_sha",
            "lineage_root_sha256",
        },
        label="protocol lineage",
    )
    lock_commit = _strict_exact_string(
        lineage["protocol_v2_lock_commit_sha"],
        _EXPECTED_PROTOCOL_V2_LOCK_COMMIT_SHA,
        label="Protocol V2 lock commit",
    )
    _strict_exact_string(
        lineage["protocol_v2_lock_sha256"],
        _EXPECTED_PROTOCOL_V2_LOCK_SHA256,
        label="Protocol V2 lock digest",
    )
    _strict_exact_string(
        lineage["c3_rehearsal_head_sha"],
        _EXPECTED_C3_REHEARSAL_HEAD_SHA,
        label="C3 rehearsal head",
    )
    _strict_exact_string(
        lineage["c4_preparation_head_sha"],
        _EXPECTED_C4_PREPARATION_HEAD_SHA,
        label="C4 preparation head",
    )
    _strict_exact_string(
        lineage["live_authorization_parent_sha"],
        lock_commit,
        label="live authorization parent",
    )
    authorization_commit = _strict_git_sha(
        lineage["live_authorization_commit_sha"], label="live authorization commit"
    )
    _same(
        lineage["capture_execution_head_sha"],
        authorization_commit,
        label="capture execution head",
    )
    if authorization_commit in {
        _EXPECTED_PROTOCOL_V2_LOCK_COMMIT_SHA,
        _EXPECTED_C3_REHEARSAL_HEAD_SHA,
        _EXPECTED_C4_PREPARATION_HEAD_SHA,
    }:
        raise InventoryReleaseReceiptUnavailable(
            "live authorization commit is not a future source-owned commit"
        )
    lineage_root = _validate_root(lineage, "lineage_root_sha256", "protocol lineage")

    contract = _strict_mapping(
        root["inventory_contract"],
        {
            "capacity",
            "publication_floor",
            "wrong_tab_outcome",
            "row_obstruction_outcome",
            "unknown_occupied_slots",
            "unknown_reason_preserved",
            "unknown_grants_action_authority",
            "unknown_grants_bank_transition_authority",
            "input_automation_allowed",
            "contract_root_sha256",
        },
        label="inventory contract",
    )
    if (
        type(contract["capacity"]) is not int
        or contract["capacity"] != _EXPECTED_CAPACITY
        or type(contract["publication_floor"]) is not float
        or contract["publication_floor"] != _EXPECTED_PUBLICATION_FLOOR
        or contract["unknown_occupied_slots"] is not None
    ):
        raise InventoryReleaseReceiptUnavailable("inventory production contract changed")
    _strict_exact_string(contract["wrong_tab_outcome"], "UNKNOWN", label="wrong-tab outcome")
    _strict_exact_string(
        contract["row_obstruction_outcome"], "UNKNOWN", label="row-obstruction outcome"
    )
    _strict_bool(contract["unknown_reason_preserved"], True, label="UNKNOWN reason retention")
    _strict_bool(
        contract["unknown_grants_action_authority"], False, label="UNKNOWN action authority"
    )
    _strict_bool(
        contract["unknown_grants_bank_transition_authority"],
        False,
        label="UNKNOWN bank-transition authority",
    )
    _strict_bool(contract["input_automation_allowed"], False, label="input authority")
    contract_root = _validate_root(contract, "contract_root_sha256", "inventory contract")

    stages = _strict_mapping(root["stages"], set(_STAGE_ORDER), label="release stages")
    identifiers: tuple[str, str, str, str] | None = None
    stage_roots: dict[str, str] = {}
    stage_values: dict[str, dict[str, object]] = {}
    for name in _STAGE_ORDER:
        stage, identifiers, stage_root = _validate_stage(name, stages[name], identifiers)
        stage_values[name] = stage
        stage_roots[name] = stage_root
    assert identifiers is not None

    authorization = stage_values["authorization"]
    campaign = stage_values["campaign"]
    review = stage_values["review"]
    evaluation = stage_values["terminal_evaluation"]
    source_approval = stage_values["source_approval"]
    identity_approval = stage_values["production_identity_approval"]
    binding = stage_values["production_binding"]

    _same(
        authorization["git_commit_sha"],
        authorization_commit,
        label="authorization stage commit",
    )
    _same(campaign["git_commit_sha"], authorization_commit, label="campaign execution head")
    actors = tuple(
        _strict_nonempty_string(stage_values[name]["actor_id"], label=f"{name} actor")
        for name in ("campaign", "review", "source_approval")
    )
    if len(set(actors)) != 3:
        raise InventoryReleaseReceiptUnavailable(
            "operator, reviewer, and source approver must be pairwise distinct"
        )
    evaluation_evidence = cast(dict[str, object], evaluation["evidence"])
    _strict_exact_string(
        evaluation_evidence["outcome"], "PASS", label="terminal evaluator outcome"
    )
    _strict_bool(
        evaluation_evidence["one_shot_terminal"], True, label="terminal one-shot policy"
    )
    _strict_bool(
        evaluation_evidence["retry_allowed"], False, label="terminal retry policy"
    )
    source_evidence = cast(dict[str, object], source_approval["evidence"])
    identity_evidence = cast(dict[str, object], identity_approval["evidence"])
    binding_evidence = cast(dict[str, object], binding["evidence"])
    _same(
        binding_evidence["source_approval_id"],
        _strict_nonempty_string(source_evidence["approval_id"], label="source approval ID"),
        label="production binding source approval ID",
    )
    _same(
        binding_evidence["production_identity_approval_id"],
        _strict_nonempty_string(
            identity_evidence["approval_id"], label="production identity approval ID"
        ),
        label="production binding identity approval ID",
    )
    future_commits = tuple(
        _strict_git_sha(stage_values[name]["git_commit_sha"], label=f"{name} commit")
        for name in (
            "authorization",
            "source_approval",
            "production_identity_approval",
            "production_binding",
        )
    )
    if len(set(future_commits)) != len(future_commits):
        raise InventoryReleaseReceiptUnavailable(
            "authorization and approval/binding source commits must be distinct"
        )

    resource_release = _strict_mapping(
        root["resource_release"],
        {
            "status",
            "receipt_id",
            "release_record_sha256",
            "source_commit_sha",
            "source_binding_root_sha256",
            "resource_release_root_sha256",
        },
        label="resource perception release",
    )
    _strict_exact_string(
        resource_release["status"], "RELEASED", label="resource release status"
    )
    _strict_nonempty_string(resource_release["receipt_id"], label="resource receipt ID")
    resource_record_root = _strict_sha256(
        resource_release["release_record_sha256"], label="resource release record"
    )
    _strict_git_sha(resource_release["source_commit_sha"], label="resource source commit")
    _strict_sha256(
        resource_release["source_binding_root_sha256"], label="resource source binding"
    )
    resource_root = _validate_root(
        resource_release, "resource_release_root_sha256", "resource perception release"
    )

    decision = _strict_mapping(
        root["final_decision"],
        {
            "status",
            "release_eligible",
            "activation_allowed",
            "world_state_authority",
            "controller_authority",
            "input_authority",
            "unresolved_condition_ids",
            "lead_approval_root_sha256",
            "protocol_lineage_root_sha256",
            "inventory_contract_root_sha256",
            "stage_roots",
            "resource_release_root_sha256",
            "decision_root_sha256",
        },
        label="final inventory release decision",
    )
    _strict_exact_string(decision["status"], "GRANTED", label="final decision status")
    _strict_bool(decision["release_eligible"], True, label="release eligibility")
    for field in (
        "activation_allowed",
        "world_state_authority",
        "controller_authority",
        "input_authority",
    ):
        _strict_bool(decision[field], False, label=f"final decision {field}")
    if type(decision["unresolved_condition_ids"]) is not list or cast(
        list[object], decision["unresolved_condition_ids"]
    ):
        raise InventoryReleaseReceiptUnavailable("final decision retains unresolved conditions")
    lead_root = _strict_sha256(
        decision["lead_approval_root_sha256"], label="lead approval root"
    )
    _same(
        decision["protocol_lineage_root_sha256"],
        lineage_root,
        label="final protocol lineage root",
    )
    _same(
        decision["inventory_contract_root_sha256"],
        contract_root,
        label="final inventory contract root",
    )
    decision_stage_roots = _strict_mapping(
        decision["stage_roots"], set(_STAGE_ORDER), label="final stage roots"
    )
    for name, expected in stage_roots.items():
        _same(decision_stage_roots[name], expected, label=f"final {name} root")
    _same(
        decision["resource_release_root_sha256"],
        resource_root,
        label="final resource release root",
    )
    _validate_root(decision, "decision_root_sha256", "final inventory release decision")
    if resource_record_root == lead_root:
        raise InventoryReleaseReceiptUnavailable(
            "resource release record and inventory lead approval roots collided"
        )
    return root


@final
class InventoryReleaseReceipt(tuple[object, ...]):
    """Immutable source receipt with no public construction or action surface."""

    __slots__ = ()

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise TypeError(
            "InventoryReleaseReceipt has no public constructor; use the source-owned loader"
        )

    def __init_subclass__(cls) -> None:
        raise TypeError("InventoryReleaseReceipt is sealed")

    def __copy__(self) -> InventoryReleaseReceipt:
        return self

    def __deepcopy__(self, memo: object) -> InventoryReleaseReceipt:
        del memo
        return self

    def __reduce__(self) -> NoReturn:
        raise TypeError("InventoryReleaseReceipt cannot be serialized")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        del protocol
        raise TypeError("InventoryReleaseReceipt cannot be serialized")

    def __repr__(self) -> str:
        return (
            "InventoryReleaseReceipt("
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
    def protocol_v2_lock_commit_sha(self) -> str:
        return cast(str, self[3])

    @property
    def live_authorization_commit_sha(self) -> str:
        return cast(str, self[4])

    @property
    def authorization_id(self) -> str:
        return cast(str, self[5])

    @property
    def campaign_id(self) -> str:
        return cast(str, self[6])

    @property
    def dataset_id(self) -> str:
        return cast(str, self[7])

    @property
    def session_id(self) -> str:
        return cast(str, self[8])

    @property
    def inventory_contract_root_sha256(self) -> str:
        return cast(str, self[9])

    @property
    def stage_roots(self) -> tuple[tuple[str, str], ...]:
        return cast(tuple[tuple[str, str], ...], self[10])

    @property
    def resource_release_root_sha256(self) -> str:
        return cast(str, self[11])

    @property
    def final_decision_root_sha256(self) -> str:
        return cast(str, self[12])

    @property
    def schema_version(self) -> int:
        return cast(int, self[13])


def _build_receipt(record: dict[str, object], record_sha256: str) -> InventoryReleaseReceipt:
    lineage = cast(dict[str, object], record["protocol_lineage"])
    contract = cast(dict[str, object], record["inventory_contract"])
    stages = cast(dict[str, object], record["stages"])
    authorization = cast(dict[str, object], stages["authorization"])
    campaign = cast(dict[str, object], stages["campaign"])
    resource = cast(dict[str, object], record["resource_release"])
    decision = cast(dict[str, object], record["final_decision"])
    roots = tuple(
        (name, cast(str, cast(dict[str, object], stages[name])["stage_root_sha256"]))
        for name in _STAGE_ORDER
    )
    values = (
        record["receipt_id"],
        record["release_record_id"],
        record_sha256,
        lineage["protocol_v2_lock_commit_sha"],
        lineage["live_authorization_commit_sha"],
        authorization["authorization_id"],
        campaign["campaign_id"],
        campaign["dataset_id"],
        campaign["session_id"],
        contract["contract_root_sha256"],
        roots,
        resource["resource_release_root_sha256"],
        decision["decision_root_sha256"],
        INVENTORY_RELEASE_RECEIPT_SCHEMA_VERSION,
        _RECEIPT_ISSUER,
    )
    return tuple.__new__(InventoryReleaseReceipt, values)


def _configured_source_receipt() -> InventoryReleaseReceipt | None:
    if (
        _OPEN_INVENTORY_RELEASE_GATES == _REQUIRED_INVENTORY_RELEASE_GATES
        and _RECEIPT_ISSUANCE_ALLOWED is False
        and _SOURCE_OWNED_RELEASE_RECORD is None
        and _APPROVED_RELEASE_RECORD_SHA256 is None
    ):
        return None
    if type(_OPEN_INVENTORY_RELEASE_GATES) is not tuple or any(
        type(gate) is not InventoryReleaseGate for gate in _OPEN_INVENTORY_RELEASE_GATES
    ):
        raise InventoryReleaseReceiptUnavailable(
            "source-owned open inventory release gates changed type"
        )
    if _OPEN_INVENTORY_RELEASE_GATES:
        raise InventoryReleaseReceiptUnavailable(
            "inventory release gates remain open: "
            + ", ".join(gate.value for gate in _OPEN_INVENTORY_RELEASE_GATES)
        )
    if _RECEIPT_ISSUANCE_ALLOWED is not True:
        raise InventoryReleaseReceiptUnavailable(
            "source-owned inventory receipt issuance is disabled"
        )
    if _SOURCE_OWNED_RELEASE_RECORD is None:
        raise InventoryReleaseReceiptUnavailable(
            "no source-owned granted inventory release record is packaged"
        )
    expected_digest = _strict_sha256(
        _APPROVED_RELEASE_RECORD_SHA256,
        label="approved inventory release record digest",
    )
    if type(_SOURCE_OWNED_RELEASE_RECORD) is not dict:
        raise InventoryReleaseReceiptUnavailable(
            "source-owned inventory release record type changed"
        )
    payload = _canonical_record_bytes(_SOURCE_OWNED_RELEASE_RECORD)
    actual_digest = hashlib.sha256(payload).hexdigest()
    if actual_digest != expected_digest:
        raise InventoryReleaseReceiptUnavailable(
            "source-owned inventory release record digest changed"
        )
    try:
        decoded: object = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise InventoryReleaseReceiptUnavailable(
            "source-owned inventory release record snapshot is invalid"
        ) from exc
    validated = _validate_source_owned_record(decoded)
    if _canonical_record_bytes(validated) != payload:
        raise InventoryReleaseReceiptUnavailable(
            "source-owned inventory release record snapshot changed"
        )
    return _build_receipt(validated, actual_digest)


_SOURCE_OWNED_RECEIPT_SINGLETON: Final[InventoryReleaseReceipt | None] = (
    _configured_source_receipt()
)


def _receipt_accessors(
    configured: InventoryReleaseReceipt | None,
    open_gates: tuple[InventoryReleaseGate, ...],
) -> tuple[
    Callable[[], InventoryReleaseReceipt],
    Callable[[object], InventoryReleaseReceipt],
]:
    expected = configured
    expected_projection = None if expected is None else tuple(expected)
    unavailable_reason = (
        "inventory release gates remain open: "
        + ", ".join(gate.value for gate in open_gates)
        if open_gates
        else "no source-owned granted inventory release receipt is packaged"
    )

    def load_source_owned_inventory_release_receipt() -> InventoryReleaseReceipt:
        """Load the exact packaged singleton; caller input cannot select it."""

        if expected is None or expected_projection is None:
            raise InventoryReleaseReceiptUnavailable(unavailable_reason)
        if type(expected) is not InventoryReleaseReceipt or tuple(expected) != expected_projection:
            raise InventoryReleaseReceiptUnavailable(
                "import-time source-owned inventory receipt changed"
            )
        return expected

    def require_source_owned_inventory_release_receipt(
        value: object,
    ) -> InventoryReleaseReceipt:
        """Require identity with the packaged singleton for the future Issue #14 seam."""

        loaded = load_source_owned_inventory_release_receipt()
        if type(value) is not InventoryReleaseReceipt or value is not loaded:
            raise InventoryReleaseReceiptUnavailable(
                "inventory release receipt is not the source-owned singleton"
            )
        return loaded

    return (
        load_source_owned_inventory_release_receipt,
        require_source_owned_inventory_release_receipt,
    )


(
    load_source_owned_inventory_release_receipt,
    require_source_owned_inventory_release_receipt,
) = _receipt_accessors(
    _SOURCE_OWNED_RECEIPT_SINGLETON,
    _OPEN_INVENTORY_RELEASE_GATES,
)
