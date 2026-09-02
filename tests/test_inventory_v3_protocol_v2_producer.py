from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_PRODUCER_PATH = _ROOT / "validation" / "inventory_v3_protocol_v2" / "producer.py"
_LOCK_SHA = "6" * 64
_LIVE_AUTHORIZATION_ID = "a" * 64
_OPAQUE_RECEIPT_ID = "123e4567-e89b-42d3-a456-426614174000"
_CAPTURE_EXECUTION_HEAD_SHA = "c" * 40
_SESSION_ID = f"inventory-v3-independent-{_LIVE_AUTHORIZATION_ID}"
_SOURCE_SESSION_REPORT_SHA256 = "d" * 64
_SOURCE_COMPLETION_SEAL_SHA256 = "e" * 64
_LEGACY_USER_RESERVATION_SHA256 = "f" * 64
_COLLECTED_AT = "2099-01-01T00:00:00.000000Z"


def _load_producer() -> ModuleType:
    name = "inventory_v3_protocol_v2_producer_test"
    spec = importlib.util.spec_from_file_location(name, _PRODUCER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


producer = _load_producer()


def _identity(
    *,
    computer_name: str = "CAPTURE-HOST",
    user_name: str = "capture-user",
    session_id: int = 7,
):  # type: ignore[no-untyped-def]
    return producer.WindowsProducerIdentity(
        computer_name=computer_name,
        user_name=user_name,
        session_id=session_id,
    )


def _observed() -> dict[str, str | int | bool]:
    return {
        "frame.height": 1078,
        "frame.pixel_format": "bgra8888",
        "frame.profile_id": "candidate-live-inventory-348867800b28a54e",
        "frame.width": 1005,
        "window_class": "SunAwtFrame",
        "window_handle": 3107,
        "windows_dpi": 144,
        "windows_scaling_percent": 150,
        "windows_version": "Windows-test",
    }


def _asserted() -> dict[str, str | int | bool]:
    return {
        "client_mode": "fixed",
        "renderer": "gpu",
        "runelite_build": "operator-reported-build",
        "theme": "dark",
    }


def _record(
    *,
    observed: dict[str, str | int | bool] | None = None,
    asserted: dict[str, str | int | bool] | None = None,
    capture_execution_head_sha: str = _CAPTURE_EXECUTION_HEAD_SHA,
    session_id: str = _SESSION_ID,
    source_session_report_sha256: str = _SOURCE_SESSION_REPORT_SHA256,
    source_completion_seal_sha256: str = _SOURCE_COMPLETION_SEAL_SHA256,
    legacy_user_reservation_sha256: str = _LEGACY_USER_RESERVATION_SHA256,
):  # type: ignore[no-untyped-def]
    return producer.build_producer_provenance(
        collected_at_utc=_COLLECTED_AT,
        protocol_lock_sha256=_LOCK_SHA,
        live_authorization_id=_LIVE_AUTHORIZATION_ID,
        opaque_receipt_id=_OPAQUE_RECEIPT_ID,
        capture_execution_head_sha=capture_execution_head_sha,
        session_id=session_id,
        source_session_report_sha256=source_session_report_sha256,
        source_completion_seal_sha256=source_completion_seal_sha256,
        legacy_user_reservation_sha256=legacy_user_reservation_sha256,
        observed_identity=_identity(),
        observed_environment=_observed() if observed is None else observed,
        operator_asserted_environment=_asserted() if asserted is None else asserted,
    )


def test_module_has_no_pixel_capture_perception_or_reviewer_dependency() -> None:
    tree = ast.parse(_PRODUCER_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    called_attributes: set[str] = set()
    called_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            called_attributes.add(node.func.attr)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called_names.add(node.func.id)

    assert imported <= {
        "__future__",
        "collections.abc",
        "ctypes",
        "dataclasses",
        "datetime",
        "hashlib",
        "json",
        "os",
        "sys",
        "typing",
        "uuid",
    }
    assert "open" not in called_names
    assert called_attributes.isdisjoint(
        {
            "capture_client_area",
            "grab",
            "read_bytes",
            "analyze",
            "load_reviewer_truth",
            "read_reviewer_truth",
        }
    )


def test_windows_identity_is_os_observed_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(producer.sys, "platform", "win32")
    monkeypatch.setattr(
        producer,
        "_windows_computer_name",
        lambda: calls.append("computer") or "OBSERVED-HOST",
    )
    monkeypatch.setattr(
        producer,
        "_windows_user_name",
        lambda: calls.append("user") or "observed-user",
    )
    monkeypatch.setattr(
        producer,
        "_windows_session_id",
        lambda: calls.append("session") or 11,
    )

    identity = producer.observe_windows_identity()

    assert identity == producer.WindowsProducerIdentity(
        computer_name="OBSERVED-HOST",
        user_name="observed-user",
        session_id=11,
    )
    assert calls == ["computer", "user", "session"]


def test_non_windows_identity_does_not_fall_back_to_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(producer.sys, "platform", "linux")
    monkeypatch.setenv("COMPUTERNAME", "untrusted-host")
    monkeypatch.setenv("USERNAME", "untrusted-user")

    assert producer.observe_windows_identity() is None


def test_missing_os_observed_identity_fails_closed() -> None:
    with pytest.raises(
        producer.ProducerProvenanceError,
        match="OS-observed Windows producer identity is unavailable",
    ):
        producer.build_producer_provenance(
            collected_at_utc=_COLLECTED_AT,
            protocol_lock_sha256=_LOCK_SHA,
            live_authorization_id=_LIVE_AUTHORIZATION_ID,
            opaque_receipt_id=_OPAQUE_RECEIPT_ID,
            capture_execution_head_sha=_CAPTURE_EXECUTION_HEAD_SHA,
            session_id=_SESSION_ID,
            source_session_report_sha256=_SOURCE_SESSION_REPORT_SHA256,
            source_completion_seal_sha256=_SOURCE_COMPLETION_SEAL_SHA256,
            legacy_user_reservation_sha256=(_LEGACY_USER_RESERVATION_SHA256),
            observed_identity=None,
            observed_environment=_observed(),
            operator_asserted_environment=_asserted(),
        )


def test_operator_assertions_cannot_substitute_for_required_observation() -> None:
    observed = _observed()
    asserted = _asserted()
    asserted["window_handle"] = observed.pop("window_handle")

    with pytest.raises(
        producer.ProducerProvenanceError,
        match="missing required observed environment provenance: window_handle",
    ):
        _record(observed=observed, asserted=asserted)


@pytest.mark.parametrize(
    "required_field",
    producer.REQUIRED_OBSERVED_ENVIRONMENT_FIELDS,
)
def test_every_required_observed_field_fails_closed_when_missing(
    required_field: str,
) -> None:
    observed = _observed()
    observed.pop(required_field)

    with pytest.raises(
        producer.ProducerProvenanceError,
        match="missing required observed environment provenance",
    ):
        _record(observed=observed)


def test_asserted_environment_is_explicit_and_never_grants_authority() -> None:
    decoded = _record().to_dict()
    environment = decoded["environment"]
    assert isinstance(environment, dict)
    asserted = environment["operator_asserted"]
    assert isinstance(asserted, list)

    assert decoded["activation_allowed"] is False
    assert decoded["promotion_allowed"] is False
    assert decoded["support_authority_granted"] is False
    assert environment["assertions_grant_support_authority"] is False
    assert {item["provenance"] for item in asserted} == {"operator-asserted"}
    assert all(item["grants_support_authority"] is False for item in asserted)


@pytest.mark.parametrize(
    "required_field",
    producer.REQUIRED_OPERATOR_ASSERTED_ENVIRONMENT_FIELDS,
)
def test_every_required_operator_assertion_is_retained_for_audit(
    required_field: str,
) -> None:
    asserted = _asserted()
    asserted.pop(required_field)

    with pytest.raises(
        producer.ProducerProvenanceError,
        match="operator-asserted environment must contain exactly",
    ):
        _record(asserted=asserted)


@pytest.mark.parametrize("bad_value", ["", " padded ", False, 1])
def test_required_operator_assertions_are_nonempty_text(
    bad_value: str | int | bool,
) -> None:
    asserted = _asserted()
    asserted["client_mode"] = bad_value

    with pytest.raises(producer.ProducerProvenanceError, match="client_mode"):
        _record(asserted=asserted)


def test_unregistered_operator_assertions_are_rejected() -> None:
    asserted = _asserted()
    asserted["operator_note"] = "not preregistered"

    with pytest.raises(
        producer.ProducerProvenanceError,
        match="unexpected operator_note",
    ):
        _record(asserted=asserted)


def test_preissued_authorization_and_receipt_are_bound_but_not_granted() -> None:
    decoded = _record().to_dict()
    binding = decoded["authorization_binding"]
    assert isinstance(binding, dict)

    assert decoded["schema"] == ("inventory-positive-v3-independent-producer-attestation-v2")
    assert binding == {
        "live_authorization_id": _LIVE_AUTHORIZATION_ID,
        "opaque_receipt_id": _OPAQUE_RECEIPT_ID,
        "producer_grants_authorization": False,
    }
    assert decoded["capture_execution_head_sha"] == _CAPTURE_EXECUTION_HEAD_SHA
    assert decoded["session_id"] == _SESSION_ID
    assert decoded["source_session_report_sha256"] == (_SOURCE_SESSION_REPORT_SHA256)
    assert decoded["source_completion_seal_sha256"] == (_SOURCE_COMPLETION_SEAL_SHA256)
    assert decoded["legacy_user_reservation_sha256"] == (_LEGACY_USER_RESERVATION_SHA256)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "capture_execution_head_sha",
            "C" * 40,
            "capture_execution_head_sha",
        ),
        (
            "session_id",
            f"inventory-v3-independent-{'b' * 64}",
            "session_id",
        ),
        (
            "source_session_report_sha256",
            "short",
            "source_session_report_sha256",
        ),
        (
            "source_completion_seal_sha256",
            "E" * 64,
            "source_completion_seal_sha256",
        ),
        (
            "legacy_user_reservation_sha256",
            "F" * 64,
            "legacy_user_reservation_sha256",
        ),
    ],
)
def test_post_capture_bindings_reject_noncanonical_or_foreign_values(
    field: str,
    value: str,
    message: str,
) -> None:
    with pytest.raises(producer.ProducerProvenanceError, match=message):
        _record(**{field: value})


@pytest.mark.parametrize(
    ("authorization_id", "receipt_id", "message"),
    [
        ("not-a-sha", _OPAQUE_RECEIPT_ID, "live_authorization_id"),
        (
            _LIVE_AUTHORIZATION_ID,
            "123e4567-e89b-12d3-a456-426614174000",
            "opaque_receipt_id",
        ),
        (
            _LIVE_AUTHORIZATION_ID,
            _OPAQUE_RECEIPT_ID.upper(),
            "opaque_receipt_id",
        ),
    ],
)
def test_authorization_binding_rejects_noncanonical_values(
    authorization_id: str,
    receipt_id: str,
    message: str,
) -> None:
    with pytest.raises(producer.ProducerProvenanceError, match=message):
        producer.build_producer_provenance(
            collected_at_utc=_COLLECTED_AT,
            protocol_lock_sha256=_LOCK_SHA,
            live_authorization_id=authorization_id,
            opaque_receipt_id=receipt_id,
            capture_execution_head_sha=_CAPTURE_EXECUTION_HEAD_SHA,
            session_id=(
                f"inventory-v3-independent-{authorization_id}"
                if len(authorization_id) == 64
                else _SESSION_ID
            ),
            source_session_report_sha256=_SOURCE_SESSION_REPORT_SHA256,
            source_completion_seal_sha256=_SOURCE_COMPLETION_SEAL_SHA256,
            legacy_user_reservation_sha256=(_LEGACY_USER_RESERVATION_SHA256),
            observed_identity=_identity(),
            observed_environment=_observed(),
            operator_asserted_environment=_asserted(),
        )


def test_observed_and_asserted_names_cannot_overlap() -> None:
    asserted = _asserted()
    asserted["windows_dpi"] = 144

    with pytest.raises(
        producer.ProducerProvenanceError,
        match="both observed and operator-asserted: windows_dpi",
    ):
        _record(asserted=asserted)


def test_reservation_name_is_user_local_stable_across_sessions() -> None:
    first = producer.windows_user_reservation_name(
        _identity(session_id=1),
        _LOCK_SHA,
    )
    later_session = producer.windows_user_reservation_name(
        _identity(session_id=99),
        _LOCK_SHA,
    )
    other_user = producer.windows_user_reservation_name(
        _identity(user_name="other-user", session_id=1),
        _LOCK_SHA,
    )

    assert first == later_session
    assert first != other_user
    assert first.startswith(f"{_LOCK_SHA}.")
    assert first.endswith(".json")
    reservation = _record().to_dict()["reservation"]
    assert isinstance(reservation, dict)
    assert reservation["scope"] == "windows-user-local-not-host-global"


def test_producer_record_is_canonical_and_input_order_independent() -> None:
    observed = _observed()
    asserted = _asserted()
    reverse_observed = dict(reversed(tuple(observed.items())))
    reverse_asserted = dict(reversed(tuple(asserted.items())))

    first = _record(observed=observed, asserted=asserted)
    second = _record(observed=reverse_observed, asserted=reverse_asserted)
    payload = first.to_canonical_json()

    assert payload == second.to_canonical_json()
    assert payload.endswith("\n")
    assert json.loads(payload) == first.to_dict()
    assert first.sha256 == second.sha256
    assert len(first.sha256) == 64


def test_record_rejects_noncanonical_identity_and_timestamp() -> None:
    with pytest.raises(producer.ProducerProvenanceError, match="computer_name"):
        _identity(computer_name=" padded ")
    with pytest.raises(producer.ProducerProvenanceError, match="UTC Z suffix"):
        producer.build_producer_provenance(
            collected_at_utc="2099-01-01T00:00:00+00:00",
            protocol_lock_sha256=_LOCK_SHA,
            live_authorization_id=_LIVE_AUTHORIZATION_ID,
            opaque_receipt_id=_OPAQUE_RECEIPT_ID,
            capture_execution_head_sha=_CAPTURE_EXECUTION_HEAD_SHA,
            session_id=_SESSION_ID,
            source_session_report_sha256=_SOURCE_SESSION_REPORT_SHA256,
            source_completion_seal_sha256=_SOURCE_COMPLETION_SEAL_SHA256,
            legacy_user_reservation_sha256=(_LEGACY_USER_RESERVATION_SHA256),
            observed_identity=_identity(),
            observed_environment=_observed(),
            operator_asserted_environment=_asserted(),
        )
