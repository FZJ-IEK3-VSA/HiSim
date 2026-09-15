"""Tests of the path-addressed inventory and its validation against the vendored contract.

Paths are the vocabulary the measure layer and the binding layer share, so reading, writing and
checking them has to be exact: a path that silently creates the wrong block, or a validation that
silently passes a wrong type, would surface as a wrong simulation rather than as an error.
"""

import json
from pathlib import Path

import pytest

from hisim.renovisor.inventory import (
    Inventory,
    InventoryPathCheck,
    InventorySchema,
    OpenApiSchemaAdapter,
    PendingContractPaths,
)
from hisim.renovisor.reasons import ReasonCode, ValidationError

pytestmark = pytest.mark.base

EXAMPLE_INVENTORY_PATH = Path(__file__).resolve().parent / "renovisor" / "example_inventory_ie_1988_detached.json"


@pytest.fixture(name="inventory")
def fixture_inventory() -> Inventory:
    """The example Irish 1988 detached house, fresh for each test."""
    return Inventory.from_dict(json.loads(EXAMPLE_INVENTORY_PATH.read_text(encoding="utf-8")))


def test_from_dict_deep_copies(inventory: Inventory) -> None:
    """Requirement R5: applying a package must not change the caller's own document."""
    original = json.loads(EXAMPLE_INVENTORY_PATH.read_text(encoding="utf-8"))
    copied = Inventory.from_dict(original)

    copied.set("building_config.general.construction_year", 1900)

    assert original["building_config"]["general"]["construction_year"] == 1988
    assert inventory.get("building_config.general.construction_year") == 1988


def test_get_reads_a_nested_path(inventory: Inventory) -> None:
    """A dotted path walks the nested objects the contract declares."""
    assert inventory.get("building_config.general.tabula_building_type") == "SFH"
    assert inventory.get("energy_system_config.heating_system.system") == "GAS_HEATING"


def test_get_returns_the_default_for_an_absent_or_null_path(inventory: Inventory) -> None:
    """A null field means 'not stated', which is the same answer as an absent one."""
    assert inventory.get("building_config.general.nothing_here", "fallback") == "fallback"
    assert inventory.get("occupancy_config.travel_route_set", "fallback") == "fallback"
    assert not inventory.has("occupancy_config.travel_route_set")
    assert inventory.has("occupancy_config.residents_count")


def test_set_creates_the_intermediate_blocks(inventory: Inventory) -> None:
    """A measure writing a pending path must not have to create its block first."""
    inventory.set("energy_system_config.ventilation_system.system", "MECHANICAL_EXTRACT")

    assert inventory.get("energy_system_config.ventilation_system.system") == "MECHANICAL_EXTRACT"


def test_set_refuses_to_write_through_a_non_object(inventory: Inventory) -> None:
    """Writing under a scalar would replace a value with a block; that is a schema violation."""
    with pytest.raises(ValidationError) as error:
        inventory.set("building_config.general.construction_year.month", 5)
    assert error.value.reason is ReasonCode.SCHEMA_VIOLATION


def test_the_example_inventory_validates(inventory: Inventory) -> None:
    """The example the other tests build on satisfies the vendored schema."""
    inventory.validate()


def test_validation_rejects_a_wrong_type(inventory: Inventory) -> None:
    """A string where the schema declares a number is caught and the path is named."""
    inventory.set("building_config.general.construction_year", "nineteen eighty-eight")

    with pytest.raises(ValidationError) as error:
        inventory.validate()
    assert error.value.reason is ReasonCode.SCHEMA_VIOLATION
    assert error.value.path == "building_config.general.construction_year"


def test_validation_rejects_a_value_outside_an_enum(inventory: Inventory) -> None:
    """The retrofit status is one of three values; a fourth is a violation."""
    inventory.set("building_config.general.retrofit_status", "gutted")

    with pytest.raises(ValidationError):
        inventory.validate()


def test_a_nullable_field_accepts_null_and_a_number(inventory: Inventory) -> None:
    """OpenAPI's nullable is rewritten into a nullable type, so both forms validate."""
    inventory.set("building_config.general.max_thermal_building_demand_in_watt", None)
    inventory.validate()

    inventory.set("building_config.general.max_thermal_building_demand_in_watt", 9000)
    inventory.validate()


def test_the_adapter_folds_nullable_into_the_type() -> None:
    """The rewrite is local and leaves everything else alone."""
    adapted = OpenApiSchemaAdapter.adapt(
        {"properties": {"a": {"type": "number", "nullable": True}, "b": {"type": "string", "examples": ["x"]}}}
    )

    assert adapted["properties"]["a"]["type"] == ["number", "null"]
    assert "nullable" not in adapted["properties"]["a"]
    assert adapted["properties"]["b"] == {"type": "string", "examples": ["x"]}


def test_the_schema_knows_the_paths_the_measures_write() -> None:
    """Check 4 of the requirements: a declared path is recognised as declared."""
    assert InventorySchema.declares("building_config.general.set_heating_temperature_in_celsius")
    assert InventorySchema.declares("energy_system_config.battery_storage.capacity_in_kwh")
    assert not InventorySchema.declares("energy_system_config.photovoltaics.share_of_maximum_pv_potential")


def test_every_pending_path_names_the_decision_that_adds_it() -> None:
    """The pending list is a to-do list for the contract PR, not a place to hide a typo."""
    assert PendingContractPaths.paths()
    for path, decision in PendingContractPaths.BY_PATH.items():
        assert not InventorySchema.declares(path), f"{path} is in the contract now; drop it from the list"
        assert decision


def test_the_path_check_accepts_declared_and_pending_paths() -> None:
    """A path is acceptable when the contract has it or the pending list names it."""
    assert InventoryPathCheck.unknown_in(
        (
            "building_config.general.set_heating_temperature_in_celsius",
            "energy_system_config.photovoltaics.share_of_maximum_pv_potential",
        )
    ) == ()
    assert InventoryPathCheck.unknown_in(("building_config.general.invented_field",)) == (
        "building_config.general.invented_field",
    )
    assert InventoryPathCheck.describe("building_config.general.floor_construction") == "pending, added by Q3"
    assert InventoryPathCheck.describe("building_config.general.invented_field") is None


def test_leaf_paths_cover_the_whole_document(inventory: Inventory) -> None:
    """The report's coverage rule needs every leaf, including the ones inside blocks."""
    leaves = inventory.leaf_paths()

    assert "location.country_code" in leaves
    assert "energy_system_config.heating_system.with_dhw_preparation" in leaves
