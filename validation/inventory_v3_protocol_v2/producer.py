"""Truthful producer provenance primitives for Inventory V3 Protocol V2.

This module is deliberately metadata-only.  It does not import the capture,
perception, evaluator, or reviewer stacks and therefore cannot acquire or
inspect validation pixels or reviewer truth.

Protocol V2 distinguishes facts observed by the producer from statements made
by an operator.  Operator assertions are retained for audit, but they never
grant activation, promotion, or supported-environment authority.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal
from uuid import RFC_4122, UUID

PRODUCER_ATTESTATION_SCHEMA: Final[str] = (
    "inventory-positive-v3-independent-producer-attestation-v2"
)
WINDOWS_IDENTITY_SCHEMA: Final[str] = "inventory-positive-v3-protocol-v2-windows-identity-v1"
WINDOWS_USER_RESERVATION_SCOPE: Final[str] = "windows-user-local-not-host-global"

REQUIRED_OBSERVED_ENVIRONMENT_FIELDS: Final[tuple[str, ...]] = (
    "frame.height",
    "frame.pixel_format",
    "frame.profile_id",
    "frame.width",
    "window_class",
    "window_handle",
    "windows_dpi",
)
REQUIRED_OPERATOR_ASSERTED_ENVIRONMENT_FIELDS: Final[tuple[str, ...]] = (
    "client_mode",
    "renderer",
    "runelite_build",
    "theme",
)

type EnvironmentValue = str | int | bool
type ProvenanceKind = Literal["observed", "operator-asserted"]


class ProducerProvenanceError(ValueError):
    """Producer provenance is absent, ambiguous, or unsupported."""


@dataclass(frozen=True, slots=True)
class WindowsProducerIdentity:
    """Windows identity observed from OS APIs for the current process."""

    computer_name: str
    user_name: str
    session_id: int

    def __post_init__(self) -> None:
        _require_text(self.computer_name, "computer_name")
        _require_text(self.user_name, "user_name")
        if (
            not isinstance(self.session_id, int)
            or isinstance(self.session_id, bool)
            or self.session_id < 0
        ):
            raise ProducerProvenanceError("session_id must be a non-negative integer")

    def to_dict(self) -> dict[str, object]:
        return {
            "computer_name": self.computer_name,
            "observation_source": "windows-api",
            "schema": WINDOWS_IDENTITY_SCHEMA,
            "session_id": self.session_id,
            "user_name": self.user_name,
        }


@dataclass(frozen=True, slots=True)
class EnvironmentProvenanceField:
    """One environment value with an explicit evidence class."""

    name: str
    value: EnvironmentValue
    provenance: ProvenanceKind

    def __post_init__(self) -> None:
        _require_text(self.name, "environment field name")
        _require_environment_value(self.value, self.name)
        if self.provenance not in ("observed", "operator-asserted"):
            raise ProducerProvenanceError(
                f"unsupported provenance kind for {self.name}: {self.provenance!r}"
            )

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "name": self.name,
            "provenance": self.provenance,
            "value": self.value,
        }
        if self.provenance == "operator-asserted":
            result["grants_support_authority"] = False
        return result


@dataclass(frozen=True, slots=True)
class ProducerProvenanceRecord:
    """Canonical, non-activating producer identity and environment record."""

    collected_at_utc: str
    protocol_lock_sha256: str
    live_authorization_id: str
    opaque_receipt_id: str
    capture_execution_head_sha: str
    session_id: str
    source_session_report_sha256: str
    source_completion_seal_sha256: str
    legacy_user_reservation_sha256: str
    windows_identity: WindowsProducerIdentity
    reservation_name: str
    observed_environment: tuple[EnvironmentProvenanceField, ...]
    operator_asserted_environment: tuple[EnvironmentProvenanceField, ...]

    def __post_init__(self) -> None:
        _require_utc_timestamp(self.collected_at_utc)
        _require_sha256(self.protocol_lock_sha256, "protocol_lock_sha256")
        _require_sha256(self.live_authorization_id, "live_authorization_id")
        _require_uuid4(self.opaque_receipt_id, "opaque_receipt_id")
        _require_git_sha(self.capture_execution_head_sha, "capture_execution_head_sha")
        expected_session_id = f"inventory-v3-independent-{self.live_authorization_id}"
        if self.session_id != expected_session_id:
            raise ProducerProvenanceError(
                "session_id must be the frozen capture session bound to live_authorization_id"
            )
        _require_sha256(
            self.source_session_report_sha256,
            "source_session_report_sha256",
        )
        _require_sha256(
            self.source_completion_seal_sha256,
            "source_completion_seal_sha256",
        )
        _require_sha256(
            self.legacy_user_reservation_sha256,
            "legacy_user_reservation_sha256",
        )
        if not isinstance(self.windows_identity, WindowsProducerIdentity):
            raise ProducerProvenanceError(
                "windows_identity must be observed Windows producer identity"
            )
        expected_name = windows_user_reservation_name(
            self.windows_identity,
            self.protocol_lock_sha256,
        )
        if self.reservation_name != expected_name:
            raise ProducerProvenanceError(
                "reservation_name is not derived from the Windows user scope"
            )
        _validate_environment_group(
            self.observed_environment,
            expected_provenance="observed",
            label="observed_environment",
        )
        _validate_environment_group(
            self.operator_asserted_environment,
            expected_provenance="operator-asserted",
            label="operator_asserted_environment",
        )
        observed_names = {item.name for item in self.observed_environment}
        asserted_names = {item.name for item in self.operator_asserted_environment}
        overlap = observed_names & asserted_names
        if overlap:
            raise ProducerProvenanceError(
                "environment fields cannot be both observed and operator-asserted: "
                + ", ".join(sorted(overlap))
            )
        missing = set(REQUIRED_OBSERVED_ENVIRONMENT_FIELDS) - observed_names
        if missing:
            raise ProducerProvenanceError(
                "missing required observed environment provenance: " + ", ".join(sorted(missing))
            )
        required_asserted = set(REQUIRED_OPERATOR_ASSERTED_ENVIRONMENT_FIELDS)
        missing_asserted = required_asserted - asserted_names
        unexpected_asserted = asserted_names - required_asserted
        if missing_asserted or unexpected_asserted:
            details: list[str] = []
            if missing_asserted:
                details.append("missing " + ", ".join(sorted(missing_asserted)))
            if unexpected_asserted:
                details.append("unexpected " + ", ".join(sorted(unexpected_asserted)))
            raise ProducerProvenanceError(
                "operator-asserted environment must contain exactly the required "
                "audit fields: " + "; ".join(details)
            )
        for item in self.operator_asserted_environment:
            if not isinstance(item.value, str):
                raise ProducerProvenanceError(
                    f"operator-asserted {item.name} must be non-empty text"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "activation_allowed": False,
            "authorization_binding": {
                "live_authorization_id": self.live_authorization_id,
                "opaque_receipt_id": self.opaque_receipt_id,
                "producer_grants_authorization": False,
            },
            "capture_execution_head_sha": self.capture_execution_head_sha,
            "collected_at_utc": self.collected_at_utc,
            "environment": {
                "assertions_grant_support_authority": False,
                "observed": [item.to_dict() for item in self.observed_environment],
                "operator_asserted": [
                    item.to_dict() for item in self.operator_asserted_environment
                ],
                "required_observed_fields": list(REQUIRED_OBSERVED_ENVIRONMENT_FIELDS),
                "required_operator_asserted_fields": list(
                    REQUIRED_OPERATOR_ASSERTED_ENVIRONMENT_FIELDS
                ),
            },
            "legacy_user_reservation_sha256": (self.legacy_user_reservation_sha256),
            "producer_identity": self.windows_identity.to_dict(),
            "promotion_allowed": False,
            "protocol_lock_sha256": self.protocol_lock_sha256,
            "reservation": {
                "name": self.reservation_name,
                "scope": WINDOWS_USER_RESERVATION_SCOPE,
            },
            "schema": PRODUCER_ATTESTATION_SCHEMA,
            "session_id": self.session_id,
            "source_completion_seal_sha256": (self.source_completion_seal_sha256),
            "source_session_report_sha256": self.source_session_report_sha256,
            "support_authority_granted": False,
        }

    def to_canonical_json(self) -> str:
        return canonical_json_bytes(self.to_dict()).decode("utf-8")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(canonical_json_bytes(self.to_dict())).hexdigest()


def observe_windows_identity() -> WindowsProducerIdentity | None:
    """Observe current computer, user, and session through Windows APIs.

    ``None`` means the required Windows API provenance is unavailable.  The
    record builder treats that state as ineligible instead of falling back to
    environment variables such as ``COMPUTERNAME`` or ``USERNAME``.
    """

    if sys.platform != "win32":
        return None
    return WindowsProducerIdentity(
        computer_name=_windows_computer_name(),
        user_name=_windows_user_name(),
        session_id=_windows_session_id(),
    )


def build_producer_provenance(
    *,
    collected_at_utc: str,
    protocol_lock_sha256: str,
    live_authorization_id: str,
    opaque_receipt_id: str,
    capture_execution_head_sha: str,
    session_id: str,
    source_session_report_sha256: str,
    source_completion_seal_sha256: str,
    legacy_user_reservation_sha256: str,
    observed_identity: WindowsProducerIdentity | None,
    observed_environment: Mapping[str, EnvironmentValue],
    operator_asserted_environment: Mapping[str, EnvironmentValue],
) -> ProducerProvenanceRecord:
    """Build a fail-closed canonical producer provenance record."""

    if observed_identity is None:
        raise ProducerProvenanceError(
            "required OS-observed Windows producer identity is unavailable"
        )
    observed = _environment_fields(observed_environment, provenance="observed")
    asserted = _environment_fields(
        operator_asserted_environment,
        provenance="operator-asserted",
    )
    return ProducerProvenanceRecord(
        collected_at_utc=collected_at_utc,
        protocol_lock_sha256=protocol_lock_sha256,
        live_authorization_id=live_authorization_id,
        opaque_receipt_id=opaque_receipt_id,
        capture_execution_head_sha=capture_execution_head_sha,
        session_id=session_id,
        source_session_report_sha256=source_session_report_sha256,
        source_completion_seal_sha256=source_completion_seal_sha256,
        legacy_user_reservation_sha256=legacy_user_reservation_sha256,
        windows_identity=observed_identity,
        reservation_name=windows_user_reservation_name(
            observed_identity,
            protocol_lock_sha256,
        ),
        observed_environment=observed,
        operator_asserted_environment=asserted,
    )


def collect_producer_provenance(
    *,
    protocol_lock_sha256: str,
    live_authorization_id: str,
    opaque_receipt_id: str,
    capture_execution_head_sha: str,
    session_id: str,
    source_session_report_sha256: str,
    source_completion_seal_sha256: str,
    legacy_user_reservation_sha256: str,
    observed_environment: Mapping[str, EnvironmentValue],
    operator_asserted_environment: Mapping[str, EnvironmentValue],
) -> ProducerProvenanceRecord:
    """Observe the current Windows identity and build a provenance record."""

    return build_producer_provenance(
        collected_at_utc=_utc_timestamp(),
        protocol_lock_sha256=protocol_lock_sha256,
        live_authorization_id=live_authorization_id,
        opaque_receipt_id=opaque_receipt_id,
        capture_execution_head_sha=capture_execution_head_sha,
        session_id=session_id,
        source_session_report_sha256=source_session_report_sha256,
        source_completion_seal_sha256=source_completion_seal_sha256,
        legacy_user_reservation_sha256=legacy_user_reservation_sha256,
        observed_identity=observe_windows_identity(),
        observed_environment=observed_environment,
        operator_asserted_environment=operator_asserted_environment,
    )


def windows_user_reservation_name(
    identity: WindowsProducerIdentity,
    protocol_lock_sha256: str,
) -> str:
    """Return a lock-specific *per-Windows-user* reservation filename.

    The session ID is intentionally excluded, so logging out and back in does
    not create another reservation for the same local Windows user.  This is
    not, and must never be described as, a host-global reservation.
    """

    if not isinstance(identity, WindowsProducerIdentity):
        raise ProducerProvenanceError("identity must be WindowsProducerIdentity")
    _require_sha256(protocol_lock_sha256, "protocol_lock_sha256")
    user_scope = {
        "computer_name": identity.computer_name.casefold(),
        "scope": WINDOWS_USER_RESERVATION_SCOPE,
        "user_name": identity.user_name.casefold(),
    }
    user_digest = hashlib.sha256(canonical_json_bytes(user_scope)).hexdigest()
    return f"{protocol_lock_sha256}.{user_digest}.json"


def canonical_json_bytes(value: object) -> bytes:
    """Serialize one JSON-compatible value canonically with a trailing LF."""

    try:
        text = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ProducerProvenanceError(
            "producer provenance is not canonically JSON-serializable"
        ) from exc
    return (text + "\n").encode("utf-8")


def _environment_fields(
    values: Mapping[str, EnvironmentValue],
    *,
    provenance: ProvenanceKind,
) -> tuple[EnvironmentProvenanceField, ...]:
    if not isinstance(values, Mapping):
        raise ProducerProvenanceError("environment provenance must be a mapping")
    fields = tuple(
        EnvironmentProvenanceField(name=name, value=value, provenance=provenance)
        for name, value in sorted(values.items())
    )
    return fields


def _validate_environment_group(
    fields: tuple[EnvironmentProvenanceField, ...],
    *,
    expected_provenance: ProvenanceKind,
    label: str,
) -> None:
    if not isinstance(fields, tuple):
        raise ProducerProvenanceError(f"{label} must be an immutable tuple")
    names = tuple(item.name for item in fields)
    if names != tuple(sorted(names)) or len(names) != len(set(names)):
        raise ProducerProvenanceError(f"{label} must be uniquely name-sorted")
    if any(
        not isinstance(item, EnvironmentProvenanceField) or item.provenance != expected_provenance
        for item in fields
    ):
        raise ProducerProvenanceError(f"{label} contains a provenance mismatch")


def _require_environment_value(value: EnvironmentValue, label: str) -> None:
    if isinstance(value, str):
        _require_text(value, label)
        return
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        return
    raise ProducerProvenanceError(f"{label} must be a canonical string, integer, or boolean")


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ProducerProvenanceError(
            f"{label} must be non-empty text without surrounding whitespace"
        )
    return value


def _require_sha256(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ProducerProvenanceError(f"{label} must be 64 lowercase hexadecimal characters")
    return value


def _require_git_sha(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ProducerProvenanceError(f"{label} must be 40 lowercase hexadecimal characters")
    return value


def _require_uuid4(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ProducerProvenanceError(f"{label} must be a canonical lowercase RFC 4122 UUIDv4")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ProducerProvenanceError(
            f"{label} must be a canonical lowercase RFC 4122 UUIDv4"
        ) from exc
    if parsed.version != 4 or parsed.variant != RFC_4122 or str(parsed) != value:
        raise ProducerProvenanceError(f"{label} must be a canonical lowercase RFC 4122 UUIDv4")
    return value


def _require_utc_timestamp(value: object) -> str:
    text = _require_text(value, "collected_at_utc")
    if not text.endswith("Z"):
        raise ProducerProvenanceError("collected_at_utc must use the UTC Z suffix")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise ProducerProvenanceError("collected_at_utc is not ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ProducerProvenanceError("collected_at_utc must be timezone-aware UTC")
    return text


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _windows_computer_name() -> str:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    buffer = ctypes.create_unicode_buffer(256)
    size = ctypes.c_uint32(len(buffer))
    if not kernel32.GetComputerNameW(buffer, ctypes.byref(size)):
        raise ProducerProvenanceError(
            f"Windows computer name is unavailable (error {ctypes.get_last_error()})"  # type: ignore[attr-defined]
        )
    return _require_text(buffer.value, "Windows computer name")


def _windows_user_name() -> str:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]
    buffer = ctypes.create_unicode_buffer(1024)
    size = ctypes.c_uint32(len(buffer))
    if not advapi32.GetUserNameW(buffer, ctypes.byref(size)):
        raise ProducerProvenanceError(
            f"Windows user name is unavailable (error {ctypes.get_last_error()})"  # type: ignore[attr-defined]
        )
    return _require_text(buffer.value, "Windows user name")


def _windows_session_id() -> int:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    session_id = ctypes.c_uint32()
    if not kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session_id)):
        raise ProducerProvenanceError(
            f"Windows session ID is unavailable (error {ctypes.get_last_error()})"  # type: ignore[attr-defined]
        )
    return int(session_id.value)
