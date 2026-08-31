from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from mining_automation.perception.inventory import (
    InventorySanitizedReplayError,
    replay_inventory_sanitized_fixture,
)

_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "perception"
    / "inventory-live-candidate-safety-v1"
)


def _fixture_copy(tmp_path: Path) -> Path:
    target = tmp_path / "fixture"
    shutil.copytree(_FIXTURE, target)
    return target


def _rewrite_manifest(
    fixture: Path,
    mutate: Callable[[dict[str, object]], None],
) -> None:
    manifest_path = fixture / "manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    mutate(raw)
    content = (
        json.dumps(
            raw,
            allow_nan=False,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    content_bytes = content.encode("utf-8")
    manifest_path.write_bytes(content_bytes)
    digest = hashlib.sha256(content_bytes).hexdigest()
    (fixture / "manifest.json.sha256").write_bytes(
        f"{digest}  manifest.json\n".encode()
    )


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("candidate_kind", "other-candidate", "candidate kind"),
        ("candidate_schema_version", 2, "candidate schema"),
        ("candidate_schema_version", True, "positive integer"),
        ("review_status", "approved", "awaiting release approval"),
        ("activation_allowed", True, "cannot allow activation"),
    ],
)
def test_replay_rejects_rewritten_candidate_contract(
    tmp_path: Path,
    field: str,
    value: object,
    match: str,
) -> None:
    fixture = _fixture_copy(tmp_path)

    def mutate(raw: dict[str, object]) -> None:
        candidate = raw["candidate"]
        assert isinstance(candidate, dict)
        candidate[field] = value

    _rewrite_manifest(fixture, mutate)

    with pytest.raises(InventorySanitizedReplayError, match=match):
        replay_inventory_sanitized_fixture(fixture)


def test_replay_recomputes_profile_identity_from_reference_and_geometry(
    tmp_path: Path,
) -> None:
    fixture = _fixture_copy(tmp_path)

    def mutate(raw: dict[str, object]) -> None:
        candidate = raw["candidate"]
        assert isinstance(candidate, dict)
        profile = candidate["profile"]
        assert isinstance(profile, dict)
        profile["profile_id"] = "candidate-live-inventory-0000000000000000"

    _rewrite_manifest(fixture, mutate)

    with pytest.raises(InventorySanitizedReplayError, match="profile identity"):
        replay_inventory_sanitized_fixture(fixture)


def test_replay_recomputes_reference_region_hash_from_fixture_bytes(
    tmp_path: Path,
) -> None:
    fixture = _fixture_copy(tmp_path)

    def mutate(raw: dict[str, object]) -> None:
        candidate = raw["candidate"]
        assert isinstance(candidate, dict)
        evidence = candidate["evidence"]
        assert isinstance(evidence, dict)
        evidence["reference_region_sha256"] = "0" * 64

    _rewrite_manifest(fixture, mutate)

    with pytest.raises(InventorySanitizedReplayError, match="reference-region SHA-256"):
        replay_inventory_sanitized_fixture(fixture)


def test_replay_binds_reference_identity_and_full_payload_provenance(
    tmp_path: Path,
) -> None:
    fixture = _fixture_copy(tmp_path)

    def change_capture(raw: dict[str, object]) -> None:
        candidate = raw["candidate"]
        assert isinstance(candidate, dict)
        evidence = candidate["evidence"]
        assert isinstance(evidence, dict)
        evidence["reference_capture_id"] = "another-capture"

    _rewrite_manifest(fixture, change_capture)
    with pytest.raises(InventorySanitizedReplayError, match="reference identity"):
        replay_inventory_sanitized_fixture(fixture)

    fixture = _fixture_copy(tmp_path / "payload")

    def change_payload(raw: dict[str, object]) -> None:
        candidate = raw["candidate"]
        assert isinstance(candidate, dict)
        evidence = candidate["evidence"]
        assert isinstance(evidence, dict)
        evidence["reference_payload_sha256"] = "0" * 64

    _rewrite_manifest(fixture, change_payload)
    with pytest.raises(InventorySanitizedReplayError, match="payload identity"):
        replay_inventory_sanitized_fixture(fixture)


def test_replay_rejects_malformed_provenance_and_case_truth_identity(
    tmp_path: Path,
) -> None:
    fixture = _fixture_copy(tmp_path)

    def malformed_hash(raw: dict[str, object]) -> None:
        candidate = raw["candidate"]
        assert isinstance(candidate, dict)
        evidence = candidate["evidence"]
        assert isinstance(evidence, dict)
        evidence["package_manifest_sha256"] = "not-a-sha256"

    _rewrite_manifest(fixture, malformed_hash)
    with pytest.raises(InventorySanitizedReplayError, match="lowercase hexadecimal"):
        replay_inventory_sanitized_fixture(fixture)

    fixture = _fixture_copy(tmp_path / "identity")

    def changed_truth(raw: dict[str, object]) -> None:
        cases = raw["cases"]
        assert isinstance(cases, list)
        first = cases[0]
        assert isinstance(first, dict)
        truth = first["review_truth"]
        assert isinstance(truth, dict)
        truth["capture_id"] = "another-capture"

    _rewrite_manifest(fixture, changed_truth)
    with pytest.raises(InventorySanitizedReplayError, match="review truth identity"):
        replay_inventory_sanitized_fixture(fixture)
