from __future__ import annotations

import inspect

import pytest

import mining_automation.app as app_module
import mining_automation.controller as controller_module
import mining_automation.perception.inventory as inventory_package
import mining_automation.perception.inventory.configuration as inventory_configuration
from mining_automation.contracts import Observation
from mining_automation.perception.inventory.adapter import (
    InventoryObservationError,
    inventory_state_from_observation,
)
from mining_automation.perception.inventory.classification import (
    InventorySlotClassifier,
)
from mining_automation.perception.inventory.detector import InventoryDetector
from mining_automation.perception.inventory.positive_classifier_v3 import (
    INVENTORY_POSITIVE_V3_VALIDATION_STATUS,
    InventoryPositiveV3DevelopmentAnalyzer,
    InventoryPositiveV3DevelopmentResult,
    inventory_positive_v3_model_configuration,
)


def test_v3_is_absent_from_production_package_and_factory_exports() -> None:
    exported = set(inventory_package.__all__)

    assert all("V3" not in name and "v3" not in name for name in exported)
    assert not hasattr(
        inventory_configuration,
        "inventory_positive_detector_v3_from_profile",
    )
    assert "positive_v3" not in inspect.getsource(
        inventory_configuration.inventory_detector_from_profile
    )


def test_v3_development_types_do_not_implement_production_protocols() -> None:
    assert not issubclass(InventoryPositiveV3DevelopmentAnalyzer, InventoryDetector)
    assert not issubclass(InventoryPositiveV3DevelopmentResult, Observation)
    assert not issubclass(
        InventoryPositiveV3DevelopmentAnalyzer,
        InventorySlotClassifier,
    )


def test_v3_development_result_cannot_enter_inventory_state_adapter() -> None:
    result = InventoryPositiveV3DevelopmentResult(
        configuration_id="inventory-positive-v3-development-test",
        occupied_slots=1,
        label="partial",
        confidence=1.0,
        reason=None,
        slots=(),
    )

    with pytest.raises(
        InventoryObservationError,
        match="observation must be Observation",
    ):
        inventory_state_from_observation(result)  # type: ignore[arg-type]


def test_v3_activation_and_validation_status_are_source_owned_fail_closed() -> None:
    configuration = inventory_positive_v3_model_configuration()

    assert configuration["activation_allowed"] is False
    assert (
        configuration["validation_status"]
        == INVENTORY_POSITIVE_V3_VALIDATION_STATUS
        == "independent-campaign-required"
    )
    evidence = configuration["development_evidence"]
    assert isinstance(evidence, dict)
    assert evidence["dataset_id"] == (
        "inventory-live-candidate-safety-bb0d0e3f7ff1c73b"
    )
    assert evidence["manifest_sha256"] == (
        "2e518ce81dd291f8b7d055afad9ddc12acbc66e0e967845f8f2e548fe1644479"
    )
    assert evidence["self_fit_only"] is True
    source_artifacts = configuration["prototype_source_artifacts"]
    assert isinstance(source_artifacts, list)
    assert len(source_artifacts) == 4


@pytest.mark.parametrize("module", [app_module, controller_module])
def test_application_and_controller_have_no_v3_activation_path(module: object) -> None:
    source = inspect.getsource(module)

    assert "positive_v3" not in source
    assert "InventoryPositiveV3" not in source
