from __future__ import annotations

import ast
import hashlib
import inspect
import json
import re
import sys
from dataclasses import fields
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from mining_automation.perception.inventory.positive_v2_calibration import (
    InventoryPositiveV2CalibrationError,
    compute_inventory_positive_v2_calibration_sha256,
)
from mining_automation.perception.inventory.sanitized_replay import (
    InventorySanitizedReplayError,
    replay_inventory_sanitized_fixture,
)
from validation.inventory_v3_protocol_v2.privacy import (
    FAILURE_PROJECTION_SCHEMA,
    FailureContractId,
    InventoryV3PrivacyProjectionError,
    PermanentFailureProjection,
    PreissuedOpaqueReceipt,
    build_permanent_failure_projection,
    load_permanent_failure_projection,
    parse_permanent_failure_projection,
)

_ROOT = Path(__file__).resolve().parents[1]
_RECEIPT = PreissuedOpaqueReceipt("123e4567-e89b-42d3-a456-426614174000")
_SHA256_TEXT = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")
_FORBIDDEN_FIELDS = (
    "pixels",
    "pixel_sha256",
    "reviewer_truth",
    "occupied_slots",
    "count",
    "confidence",
    "operator",
    "reviewer",
    "approver",
    "captured_at_utc",
    "path",
    "message",
    "reason",
    "note",
)


def _projection(
    contract_id: FailureContractId = FailureContractId.C1_EMPTY_ZERO_CONFORMANCE_FAILURE,
) -> PermanentFailureProjection:
    return build_permanent_failure_projection(_RECEIPT, contract_id)


def _write_as_fixture_manifest(directory: Path, payload: bytes) -> None:
    directory.mkdir()
    manifest = directory / "manifest.json"
    manifest.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    (directory / "manifest.json.sha256").write_text(
        f"{digest}  manifest.json\n",
        encoding="ascii",
    )


def test_failure_contract_vocabulary_and_projection_keys_match_preregistration() -> None:
    preregistration = json.loads(
        (_ROOT / "validation/inventory_v3_protocol_v2/preregistration.json").read_bytes()
    )

    assert {item.value for item in FailureContractId} == set(preregistration["failure_contracts"])
    assert set(_projection().to_dict()) == set(
        preregistration["privacy_projection"]["allowed_keys"]
    )
    assert FAILURE_PROJECTION_SCHEMA == preregistration["privacy_projection"]["schema"]


def test_protocol_v2_has_no_validation_to_training_calibration_or_prototype_import() -> None:
    protocol_root = _ROOT / "validation" / "inventory_v3_protocol_v2"
    perception_imports: set[str] = set()
    forbidden_fragments = ("calibration", "prototype", "sanitized_replay", "training")

    for path in sorted(protocol_root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported: tuple[str, ...]
            if isinstance(node, ast.Import):
                imported = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported = ((node.module or ""),)
            else:
                continue
            for module in imported:
                lowered = module.lower()
                assert not any(fragment in lowered for fragment in forbidden_fragments)
                if module.startswith("mining_automation.perception"):
                    perception_imports.add(module)

    assert perception_imports == {
        "mining_automation.perception.inventory.positive_v3_independent_validation"
    }


def test_projection_is_exact_canonical_terminal_and_nonactivating() -> None:
    projection = _projection()
    expected = {
        "activation_allowed": False,
        "contract_id": "C1_EMPTY_ZERO_CONFORMANCE_FAILURE",
        "opaque_receipt_id": _RECEIPT.value,
        "promotion_allowed": False,
        "retry_allowed": False,
        "schema": FAILURE_PROJECTION_SCHEMA,
        "terminal_status": "failed-permanent",
    }

    assert projection.to_dict() == expected
    assert projection.to_json() == (
        json.dumps(expected, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n"
    )
    assert projection.terminal_status == "failed-permanent"
    assert projection.retry_allowed is False
    assert projection.activation_allowed is False
    assert projection.promotion_allowed is False
    assert load_permanent_failure_projection(projection.to_json().encode("ascii")) == projection


@pytest.mark.parametrize("contract_id", tuple(FailureContractId))
def test_every_preregistered_failure_has_the_same_closed_privacy_shape(
    contract_id: FailureContractId,
) -> None:
    projection = _projection(contract_id)
    document = projection.to_dict()
    serialized = projection.to_json()

    assert set(document) == {
        "activation_allowed",
        "contract_id",
        "opaque_receipt_id",
        "promotion_allowed",
        "retry_allowed",
        "schema",
        "terminal_status",
    }
    assert document["contract_id"] == contract_id.value
    assert _SHA256_TEXT.search(serialized) is None
    assert all(name not in document for name in _FORBIDDEN_FIELDS)
    allowed_strings = {
        FAILURE_PROJECTION_SCHEMA,
        "failed-permanent",
        _RECEIPT.value,
        contract_id.value,
    }
    assert {value for value in document.values() if type(value) is str} == allowed_strings


def test_typed_builder_has_no_sensitive_input_channel_and_is_noninterfering() -> None:
    signature = inspect.signature(build_permanent_failure_projection)
    assert tuple(signature.parameters) == ("receipt", "contract_id")
    assert tuple(item.name for item in fields(PermanentFailureProjection)) == (
        "receipt",
        "contract_id",
    )

    private_variants = (
        {
            "pixels": b"private-pixels-a",
            "reviewer_truth": {"occupied_slots": 0},
            "actual": {"confidence": 0.0},
        },
        {
            "pixels": b"private-pixels-b",
            "reviewer_truth": {"occupied_slots": 28},
            "actual": {"confidence": 1.0},
        },
    )
    outputs = [
        _projection(FailureContractId.CAMPAIGN_TERMINAL_FAILURE).to_json()
        for _private_material in private_variants
    ]
    assert outputs[0] == outputs[1]

    with pytest.raises(TypeError):
        build_permanent_failure_projection(  # type: ignore[call-arg]
            _RECEIPT,
            FailureContractId.CAMPAIGN_TERMINAL_FAILURE,
            pixels=b"forbidden",
        )


@pytest.mark.parametrize("forbidden_field", _FORBIDDEN_FIELDS)
def test_parser_rejects_every_unlisted_sensitive_or_free_text_field(
    forbidden_field: str,
) -> None:
    document = _projection().to_dict()
    document[forbidden_field] = "private-sentinel"

    with pytest.raises(
        InventoryV3PrivacyProjectionError,
        match="closed privacy schema",
    ):
        parse_permanent_failure_projection(document)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("activation_allowed", True),
        ("promotion_allowed", True),
        ("retry_allowed", True),
        ("terminal_status", "retryable"),
        ("schema", "attacker-schema"),
        ("contract_id", "C8_UNREGISTERED"),
        ("opaque_receipt_id", "0" * 64),
        ("opaque_receipt_id", "123e4567-e89b-12d3-a456-426614174000"),
        ("opaque_receipt_id", "123E4567-E89B-42D3-A456-426614174000"),
    ),
)
def test_parser_rejects_mutable_flags_unregistered_contracts_and_nonopaque_receipts(
    field: str,
    value: object,
) -> None:
    document = _projection().to_dict()
    document[field] = value

    with pytest.raises(InventoryV3PrivacyProjectionError):
        parse_permanent_failure_projection(document)


def test_parser_rejects_missing_wrong_typed_or_non_object_documents() -> None:
    missing = _projection().to_dict()
    missing.pop("contract_id")
    with pytest.raises(InventoryV3PrivacyProjectionError, match="closed privacy schema"):
        parse_permanent_failure_projection(missing)

    wrong_type = _projection().to_dict()
    wrong_type["retry_allowed"] = 0
    with pytest.raises(InventoryV3PrivacyProjectionError, match="terminal constant"):
        parse_permanent_failure_projection(wrong_type)

    for value in (None, (), [], "projection"):
        with pytest.raises(InventoryV3PrivacyProjectionError, match="JSON object"):
            parse_permanent_failure_projection(value)


def test_typed_constructor_rejects_raw_strings() -> None:
    with pytest.raises(TypeError, match="PreissuedOpaqueReceipt"):
        PermanentFailureProjection(  # type: ignore[arg-type]
            receipt=_RECEIPT.value,
            contract_id=FailureContractId.ATTEMPT_INTEGRITY_FAILURE,
        )
    with pytest.raises(TypeError, match="FailureContractId"):
        PermanentFailureProjection(  # type: ignore[arg-type]
            receipt=_RECEIPT,
            contract_id=FailureContractId.ATTEMPT_INTEGRITY_FAILURE.value,
        )


@pytest.mark.parametrize(
    "payload",
    (
        b"",
        b"[]\n",
        b'{"contract_id":"C1_EMPTY_ZERO_CONFORMANCE_FAILURE",'
        b'"contract_id":"C2_EARLY_PARTIAL_CONFORMANCE_FAILURE"}\n',
        b'{"schema":"non-ascii-\xff"}\n',
        b"{}",
        b" " * 1025,
    ),
)
def test_loader_rejects_noncanonical_duplicate_nonascii_or_oversized_payloads(
    payload: bytes,
) -> None:
    with pytest.raises(InventoryV3PrivacyProjectionError):
        load_permanent_failure_projection(payload)


def test_projection_is_not_a_sanitized_replay_or_calibration_fixture(tmp_path: Path) -> None:
    payload = _projection().to_json().encode("ascii")
    replay_fixture = tmp_path / "replay"
    calibration_fixture = tmp_path / "calibration"
    _write_as_fixture_manifest(replay_fixture, payload)
    _write_as_fixture_manifest(calibration_fixture, payload)

    with pytest.raises(InventorySanitizedReplayError, match="unsupported sanitized fixture kind"):
        replay_inventory_sanitized_fixture(replay_fixture)
    with pytest.raises(
        InventoryPositiveV2CalibrationError,
        match="unsupported sanitized fixture kind",
    ):
        compute_inventory_positive_v2_calibration_sha256(calibration_fixture)
