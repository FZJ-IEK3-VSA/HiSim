"""Tests of applying a package to an inventory, end to end through the pure layers.

These are the cases a caller actually hits: a normal package that changes two U-values and the
base file, a package whose only measure has no model, a package that has to be refused, and the
four ways a package can be malformed. Together they pin the order of work — validate, copy, run,
compose, check, write, report — because getting that order wrong is what makes a translation layer
half-apply a bad request.
"""

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from hisim.renovisor.application import ApplicationResult, PackageApplication
from hisim.renovisor.catalogue import Catalogue
from hisim.renovisor.envelope import EnvelopePaths
from hisim.renovisor.inventory import Inventory
from hisim.renovisor.materials import InsulationMaterials
from hisim.renovisor.reasons import ReasonCode, RefusalError, ValidationError
from hisim.renovisor.registry import MeasureRegistry
from hisim.renovisor.report import ReportStatus
from hisim.renovisor.vocabulary import HeatGenerator, ThermalElement

pytestmark = pytest.mark.base

EXAMPLE_INVENTORY_PATH = Path(__file__).resolve().parent / "renovisor" / "example_inventory_ie_1988_detached.json"

RETROFIT_PACKAGE: List[Dict[str, Any]] = [
    {"measure_id": "EXTERNAL_INSULATION", "options": {"material": "polystyrene_eps_rigid_board"}},
    {"measure_id": "ROLLED_OUT_ATTIC_INSULATION", "options": {"material": "wood_fiber_rigid_board"}},
    {"measure_id": "HEATING_INSTALLATION", "options": {"type_of_system": "FLOORHEATING"}},
    {"measure_id": "CHANGE_ROOM_TEMPERATURE", "options": {"new_room_temperature": 21}},
    {"measure_id": "HEATING_SYSTEM", "options": {"type_of_system": "HEAT_PUMP"}},
]


@pytest.fixture(name="inventory")
def fixture_inventory() -> Inventory:
    """The example Irish 1988 detached house: gas boiler, radiators, no photovoltaics, no car."""
    return Inventory.from_dict(json.loads(EXAMPLE_INVENTORY_PATH.read_text(encoding="utf-8")))


@pytest.fixture(name="application", scope="module")
def fixture_application() -> PackageApplication:
    """The application over the committed catalogue and material table."""
    return PackageApplication(Catalogue.load(), MeasureRegistry(), InsulationMaterials.load())


@pytest.fixture(name="result")
def fixture_result(application: PackageApplication, inventory: Inventory) -> ApplicationResult:
    """The retrofit package applied to the example house."""
    return application.apply(inventory, RETROFIT_PACKAGE)


def test_the_two_insulated_elements_change_and_the_others_do_not(
    result: ApplicationResult, inventory: Inventory
) -> None:
    """Two measures, two composed U-values; the untouched elements keep what they had."""
    facade = result.inventory.get(EnvelopePaths.u_value_path(ThermalElement.FACADE))
    roof = result.inventory.get(EnvelopePaths.u_value_path(ThermalElement.ROOF))

    assert facade == pytest.approx(0.3252, abs=1e-4)
    assert roof == pytest.approx(0.1562, abs=1e-4)
    assert result.inventory.get(EnvelopePaths.u_value_path(ThermalElement.WINDOW)) == 4.8
    assert inventory.get(EnvelopePaths.u_value_path(ThermalElement.FACADE)) is None


def test_the_heat_pump_measure_changes_the_base_file(result: ApplicationResult) -> None:
    """The house starts on the gas file and ends on the heat-pump one."""
    assert result.base_file_key.generator is HeatGenerator.HEAT_PUMP
    assert result.base_file_key.solar_thermal is False
    assert result.base_file_key.cars == 0
    assert result.base_file_name == "household_heatpump_building_sizer.grouped.energy_system.yaml"


def test_the_non_envelope_fields_are_written(result: ApplicationResult) -> None:
    """A measure's inventory writes land in the post-measure copy in HiSim spelling."""
    assert result.inventory.get("building_config.general.set_heating_temperature_in_celsius") == 21
    assert result.inventory.get("energy_system_config.heating_system.system") == "HEAT_PUMP"
    assert result.inventory.get("energy_system_config.heating_system.heat_distribution_system") == "FLOORHEATING"


def test_the_report_has_one_line_per_measure(result: ApplicationResult) -> None:
    """Requirement R7: every measure of the package appears once, with its own measure id."""
    measure_lines = [line for line in result.report.to_list() if line["path"].startswith("package.measures[")]
    named = [line for line in measure_lines if "measure_id" in line]

    assert {line["measure_id"] for line in named} == {entry["measure_id"] for entry in RETROFIT_PACKAGE}
    assert len(named) == len(RETROFIT_PACKAGE)


def test_the_report_covers_every_leaf_of_the_post_measure_inventory(result: ApplicationResult) -> None:
    """Requirement R7: no field of the configuration is left unaccounted for."""
    covered = {line["path"] for line in result.report.to_list()}

    for leaf in result.inventory.leaf_paths():
        assert leaf in covered, f"{leaf} has no report line"


def test_a_defaulted_thickness_is_reported_with_its_rule(result: ApplicationResult) -> None:
    """Requirement M7 and decision Q11: the report names the target and the simplification."""
    line = next(
        item
        for item in result.report.to_list()
        if item["path"].endswith("[0].options.thickness_in_mm")
    )

    assert line["status"] == ReportStatus.DEFAULTED.value
    assert "target" in line["rule"]
    assert "no thermal-bridge surcharge" in line["rule"]


def test_the_post_measure_inventory_still_validates(result: ApplicationResult) -> None:
    """Acceptance criterion AC4.1: what the measures produced is still a valid inventory."""
    result.inventory.validate()


def test_a_measure_with_no_model_is_reported_not_dropped(
    application: PackageApplication, inventory: Inventory
) -> None:
    """Requirement M8: the package simulates and the report says the measure changed nothing."""
    result = application.apply(inventory, [{"measure_id": "INSTALL_NEW_LED_LIGHTS", "options": {}}])

    line = next(line for line in result.report.to_list() if line["path"] == "package.measures[0]")
    assert line["status"] == ReportStatus.NON_SIMULATION.value
    assert line["rule"] == ReasonCode.NO_APPLIANCE_SUBMODEL.value
    assert result.base_file_key.generator is HeatGenerator.GAS_HEATING


def test_a_blocked_material_refuses_the_whole_package(
    application: PackageApplication, inventory: Inventory
) -> None:
    """Decision Q13: no window rows in the database means no window replacement, and it is said."""
    with pytest.raises(RefusalError) as error:
        application.apply(inventory, [{"measure_id": "WINDOW_REPLACEMENT", "options": {"glazing_panes": 3}}])

    assert error.value.reason is ReasonCode.MATERIAL_NOT_IN_DATABASE
    assert error.value.refusals[0].measure_id == "WINDOW_REPLACEMENT"


def test_every_refusal_is_reported_at_once(application: PackageApplication, inventory: Inventory) -> None:
    """A caller with two problems learns both, rather than fixing one and resubmitting."""
    with pytest.raises(RefusalError) as error:
        application.apply(
            inventory,
            [
                {"measure_id": "WINDOW_REPLACEMENT", "options": {"glazing_panes": 3}},
                {"measure_id": "ADD_INTERNAL_DRY_LINING", "options": {}},
            ],
        )

    assert {item.reason for item in error.value.refusals} == {
        ReasonCode.MATERIAL_NOT_IN_DATABASE,
        ReasonCode.UNDEFINED_MEASURE_BUILDUP,
    }
    assert len(error.value.to_list()) == 2


def test_contradictory_floor_measures_refuse(application: PackageApplication, inventory: Inventory) -> None:
    """Decision Q3: a basement ceiling and a suspended floor are two different buildings."""
    with pytest.raises(RefusalError) as error:
        application.apply(
            inventory,
            [
                {"measure_id": "BASEMENT_CEILING_INSULATION", "options": {"material": "polystyrene_eps_rigid_board"}},
                {
                    "measure_id": "SUSPENDED_GROUND_FLOOR_INSULATION",
                    "options": {"material": "polystyrene_eps_rigid_board"},
                },
            ],
        )

    assert ReasonCode.CONTRADICTORY_MEASURES in {item.reason for item in error.value.refusals}


def test_a_measure_that_does_not_fit_the_building_refuses(
    application: PackageApplication, inventory: Inventory
) -> None:
    """Decision Q3: once the inventory states the wall construction, the fit is checked."""
    inventory.set(EnvelopePaths.WALL_CONSTRUCTION, "SOLID")

    with pytest.raises(RefusalError) as error:
        application.apply(inventory, [{"measure_id": "CAVITY_WALL_INSULATION", "options": {}}])

    assert error.value.reason is ReasonCode.MEASURE_DOES_NOT_FIT_BUILDING


def test_a_domestic_hot_water_heat_pump_on_a_boiler_house_refuses(
    application: PackageApplication, inventory: Inventory
) -> None:
    """The combination has no recorded file, which only the application can see."""
    with pytest.raises(RefusalError) as error:
        application.apply(inventory, [{"measure_id": "HOT_WATER_SYSTEM", "options": {"supply": "HEAT_PUMP"}}])

    assert error.value.reason is ReasonCode.NO_BASE_FILE_FOR_COMBINATION


def test_a_combination_without_a_recorded_file_refuses(
    application: PackageApplication, inventory: Inventory
) -> None:
    """Solar thermal on an oil boiler: a well-formed request with nowhere to run."""
    inventory.set("energy_system_config.heating_system.system", "OIL_HEATING")

    with pytest.raises(RefusalError) as error:
        application.apply(inventory, [{"measure_id": "SOLAR_THERMAL_SYSTEM", "options": {"supplies": "DHW_ONLY"}}])

    assert error.value.reason is ReasonCode.NO_BASE_FILE_FOR_COMBINATION


def test_a_stage_zero_measure_is_a_validation_error(
    application: PackageApplication, inventory: Inventory
) -> None:
    """Decision Q4: the base state comes from the inventory alone, and saying otherwise fails hard."""
    with pytest.raises(ValidationError) as error:
        application.apply(
            inventory,
            [{"measure_id": "EXTERNAL_INSULATION", "options": {"material": "extruded_polystyrene_xps"}, "stage": 0}],
        )

    assert error.value.reason is ReasonCode.STAGE_ZERO_MEASURE


def test_an_unknown_key_in_a_package_entry_is_rejected(
    application: PackageApplication, inventory: Inventory
) -> None:
    """A package entry carries measure_id and options and nothing else (decision Q1)."""
    with pytest.raises(ValidationError) as error:
        application.apply(inventory, [{"measure_id": "OUTSIDE_SHADING", "options": {}, "priority": 2}])

    assert error.value.reason is ReasonCode.UNKNOWN_KEY_IN_MEASURE


def test_an_unknown_measure_is_rejected(application: PackageApplication, inventory: Inventory) -> None:
    """A measure the catalogue does not have names the path of the offending id."""
    with pytest.raises(ValidationError) as error:
        application.apply(inventory, [{"measure_id": "BUILD_A_WINDMILL", "options": {}}])

    assert error.value.reason is ReasonCode.UNKNOWN_MEASURE
    assert error.value.path == "package.measures[0].measure_id"


def test_the_same_measure_twice_is_rejected(application: PackageApplication, inventory: Inventory) -> None:
    """Challenge C5: measure kinds are unique per package; two layers are two measures."""
    with pytest.raises(ValidationError) as error:
        application.apply(
            inventory,
            [
                {"measure_id": "OUTSIDE_SHADING", "options": {}},
                {"measure_id": "OUTSIDE_SHADING", "options": {}},
            ],
        )

    assert error.value.reason is ReasonCode.DUPLICATE_MEASURE


def test_an_empty_package_leaves_the_inventory_alone(
    application: PackageApplication, inventory: Inventory
) -> None:
    """The base case: no measures, no changes, and the inventory's own base file."""
    result = application.apply(inventory, [])

    assert result.base_file_name == "household_gas_building_sizer.grouped.energy_system.yaml"
    assert result.u_values == {}
    assert result.inventory.to_dict() == inventory.to_dict()


def test_a_pending_law_is_reported_and_not_written(
    application: PackageApplication, inventory: Inventory
) -> None:
    """Decision Q12: the capacity is sized in step 5, so the field is reported as approximated."""
    result = application.apply(inventory, [{"measure_id": "BATTERY_SYSTEM", "options": {"days_to_cover": 2}}])

    path = "energy_system_config.battery_storage.capacity_in_kwh"
    assert path in result.pending_laws
    assert result.inventory.get(path, 0) == 0
    line = next(item for item in result.report.to_list() if item["path"] == path)
    assert line["status"] == ReportStatus.APPROXIMATED.value
    assert result.variant_selections == {"electricity_management": "ems_with_battery"}


def test_an_unknown_generator_in_the_inventory_is_a_schema_violation(
    application: PackageApplication, inventory: Inventory
) -> None:
    """The inventory's own generator has to be one HiSim knows before any measure runs."""
    inventory.set("energy_system_config.heating_system.system", "COAL_FIRE")

    with pytest.raises(ValidationError) as error:
        application.apply(inventory, [])

    assert error.value.reason is ReasonCode.SCHEMA_VIOLATION


def test_a_house_without_a_battery_runs_metered_directly(
    application: PackageApplication, inventory: Inventory
) -> None:
    """The base state decides the electricity-management variant when no measure does.

    The recorded base files default to ``ems_with_battery``, whose battery sizes itself from the
    PV array. A baseline without a battery must therefore select ``metered_directly``; keeping the
    default gave a zero-capacity battery on a house without PV, which the battery library cannot
    simulate. The example inventory states a battery block with capacity 0, which is no battery.
    """
    result = application.apply(inventory, [])
    assert result.variant_selections == {"electricity_management": "metered_directly"}
    line = next(entry for entry in result.report.to_list() if entry["path"].endswith("battery_storage.capacity_in_kwh"))
    assert line["status"] == ReportStatus.DEFAULTED.value


def test_a_house_with_a_battery_runs_the_ems_variant(application: PackageApplication, inventory: Inventory) -> None:
    """A stated battery capacity selects ``ems_with_battery`` without any measure."""
    with_battery = Inventory.from_dict(inventory.to_dict())
    with_battery.set("energy_system_config.battery_storage.capacity_in_kwh", 8.0)
    result = application.apply(with_battery, [])
    assert result.variant_selections == {"electricity_management": "ems_with_battery"}


def test_a_battery_measure_still_wins_over_the_base_state(
    application: PackageApplication, inventory: Inventory
) -> None:
    """The ``BATTERY_SYSTEM`` measure's own selection is not overwritten by the base-state rule."""
    result = application.apply(inventory, [{"measure_id": "BATTERY_SYSTEM", "options": {"days_to_cover": 2}}])
    assert result.variant_selections["electricity_management"] == "ems_with_battery"
