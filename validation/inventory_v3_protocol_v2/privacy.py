"""Privacy-safe permanent-failure projection for Inventory V3 protocol V2.

The projection is deliberately incapable of carrying validation pixels, content
digests, reviewer truth, model output, actor identity, chronology, paths, or
free-form text.  A trusted ledger must issue the opaque receipt before any
sensitive validation material is opened; this module never generates receipts.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

__all__ = [
    "FAILURE_PROJECTION_SCHEMA",
    "FailureContractId",
    "InventoryV3PrivacyProjectionError",
    "PermanentFailureProjection",
    "PreissuedOpaqueReceipt",
    "build_permanent_failure_projection",
    "load_permanent_failure_projection",
    "parse_permanent_failure_projection",
]


FAILURE_PROJECTION_SCHEMA: Final[str] = (
    "inventory-positive-v3-private-failure-regression-receipt-v1"
)
_TERMINAL_STATUS: Final[str] = "failed-permanent"
_EXACT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "activation_allowed",
        "contract_id",
        "opaque_receipt_id",
        "promotion_allowed",
        "retry_allowed",
        "schema",
        "terminal_status",
    }
)
_MAX_SERIALIZED_BYTES: Final[int] = 1024
_CANONICAL_UUID_V4 = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)


class InventoryV3PrivacyProjectionError(ValueError):
    """A permanent-failure projection violated its closed privacy schema."""


class FailureContractId(StrEnum):
    """The complete preregistered Inventory V3 protocol-V2 failure vocabulary."""

    C1_EMPTY_ZERO_CONFORMANCE_FAILURE = "C1_EMPTY_ZERO_CONFORMANCE_FAILURE"
    C2_EARLY_PARTIAL_CONFORMANCE_FAILURE = "C2_EARLY_PARTIAL_CONFORMANCE_FAILURE"
    C3_MID_PARTIAL_ORDER_CONFORMANCE_FAILURE = "C3_MID_PARTIAL_ORDER_CONFORMANCE_FAILURE"
    C4_NEAR_FULL_BOUND_CONFORMANCE_FAILURE = "C4_NEAR_FULL_BOUND_CONFORMANCE_FAILURE"
    C5_FULL_28_CONFORMANCE_FAILURE = "C5_FULL_28_CONFORMANCE_FAILURE"
    C6_WRONG_TAB_UNKNOWN_SAFETY_FAILURE = "C6_WRONG_TAB_UNKNOWN_SAFETY_FAILURE"
    C7_ROW_OBSTRUCTION_UNKNOWN_SAFETY_FAILURE = "C7_ROW_OBSTRUCTION_UNKNOWN_SAFETY_FAILURE"
    CASE_EVIDENCE_INELIGIBLE = "CASE_EVIDENCE_INELIGIBLE"
    ATTEMPT_INTEGRITY_FAILURE = "ATTEMPT_INTEGRITY_FAILURE"
    CAMPAIGN_TERMINAL_FAILURE = "CAMPAIGN_TERMINAL_FAILURE"


@dataclass(frozen=True, slots=True)
class PreissuedOpaqueReceipt:
    """A trusted-ledger UUIDv4 issued before sensitive validation reads.

    UUIDv4 is required because it carries neither time nor content-derived
    identity.  Construction validates representation only; provenance remains
    the responsibility of the protocol-V2 authorization ledger.
    """

    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str or _CANONICAL_UUID_V4.fullmatch(self.value) is None:
            raise InventoryV3PrivacyProjectionError(
                "opaque receipt must be a canonical lowercase UUIDv4"
            )
        parsed = uuid.UUID(self.value)
        if parsed.version != 4 or parsed.variant != uuid.RFC_4122 or str(parsed) != self.value:
            raise InventoryV3PrivacyProjectionError(
                "opaque receipt must be a canonical lowercase UUIDv4"
            )


@dataclass(frozen=True, slots=True)
class PermanentFailureProjection:
    """One typed, terminal, non-retriable failure-regression receipt."""

    receipt: PreissuedOpaqueReceipt
    contract_id: FailureContractId

    def __post_init__(self) -> None:
        if type(self.receipt) is not PreissuedOpaqueReceipt:
            raise TypeError("receipt must be PreissuedOpaqueReceipt")
        if type(self.contract_id) is not FailureContractId:
            raise TypeError("contract_id must be FailureContractId")

    @property
    def terminal_status(self) -> str:
        return _TERMINAL_STATUS

    @property
    def retry_allowed(self) -> bool:
        return False

    @property
    def activation_allowed(self) -> bool:
        return False

    @property
    def promotion_allowed(self) -> bool:
        return False

    def to_dict(self) -> dict[str, object]:
        """Return the exact closed public representation."""

        return {
            "activation_allowed": False,
            "contract_id": self.contract_id.value,
            "opaque_receipt_id": self.receipt.value,
            "promotion_allowed": False,
            "retry_allowed": False,
            "schema": FAILURE_PROJECTION_SCHEMA,
            "terminal_status": _TERMINAL_STATUS,
        }

    def to_json(self) -> str:
        """Return canonical ASCII JSON with its required final newline."""

        return _canonical_bytes(self.to_dict()).decode("ascii")


def build_permanent_failure_projection(
    receipt: PreissuedOpaqueReceipt,
    contract_id: FailureContractId,
) -> PermanentFailureProjection:
    """Build a projection from the only two permitted variable inputs."""

    return PermanentFailureProjection(receipt=receipt, contract_id=contract_id)


def parse_permanent_failure_projection(
    value: object,
) -> PermanentFailureProjection:
    """Parse an in-memory projection while rejecting every unlisted field."""

    if type(value) is not dict:
        raise InventoryV3PrivacyProjectionError("failure projection must be a JSON object")
    document = value
    if set(document) != _EXACT_KEYS:
        raise InventoryV3PrivacyProjectionError(
            "failure projection fields differ from the closed privacy schema"
        )
    _require_exact_value(document, "schema", FAILURE_PROJECTION_SCHEMA)
    _require_exact_value(document, "terminal_status", _TERMINAL_STATUS)
    for key in ("retry_allowed", "activation_allowed", "promotion_allowed"):
        _require_exact_value(document, key, False)

    receipt_value = document["opaque_receipt_id"]
    if type(receipt_value) is not str:
        raise InventoryV3PrivacyProjectionError("opaque_receipt_id must be text")
    contract_value = document["contract_id"]
    if type(contract_value) is not str:
        raise InventoryV3PrivacyProjectionError("contract_id must be text")
    try:
        contract_id = FailureContractId(contract_value)
    except ValueError as exc:
        raise InventoryV3PrivacyProjectionError(
            "contract_id is not a preregistered failure contract"
        ) from exc
    return PermanentFailureProjection(
        receipt=PreissuedOpaqueReceipt(receipt_value),
        contract_id=contract_id,
    )


def load_permanent_failure_projection(payload: bytes) -> PermanentFailureProjection:
    """Load one canonical projection without permitting duplicate JSON keys."""

    if type(payload) is not bytes:
        raise TypeError("payload must be bytes")
    if not payload or len(payload) > _MAX_SERIALIZED_BYTES:
        raise InventoryV3PrivacyProjectionError("failure projection has an invalid serialized size")
    try:
        decoded = json.loads(payload.decode("ascii"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InventoryV3PrivacyProjectionError(
            "failure projection is not canonical ASCII JSON"
        ) from exc
    projection = parse_permanent_failure_projection(decoded)
    if payload != _canonical_bytes(projection.to_dict()):
        raise InventoryV3PrivacyProjectionError("failure projection is not canonical ASCII JSON")
    return projection


def _require_exact_value(document: dict[str, object], key: str, expected: object) -> None:
    value = document[key]
    if type(value) is not type(expected) or value != expected:
        raise InventoryV3PrivacyProjectionError(
            f"failure projection {key} differs from its terminal constant"
        )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise InventoryV3PrivacyProjectionError(
                "failure projection contains a duplicate JSON key"
            )
        result[key] = value
    return result


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        + b"\n"
    )
