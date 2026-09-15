"""Tests of writing a dwelling's values into a recorded energy-system file.

Three things are pinned here, and they are the three ways this step can go wrong. The **swaps**
must replace the recorded preset by the constructor that takes this dwelling's identifier and drop
the recorded keys the constructor now supplies -- the pinned ``weather_identity`` above all, which
would otherwise leave a building in Dublin keyed on Aachen's climate. The **coverage** must be
total: every inventory leaf the bindings send to a config field has to arrive there, or be
accounted for as ignored or refused. And the **diff rule** must actually refuse a forged change,
since it is the only mechanical guard requirement R4 has.
"""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List

import pytest

from hisim.energy_system.loader import load_energy_system
from hisim.energy_system.model import DefaultInputs
from hisim.renovisor.application import ApplicationResult, PackageApplication
from hisim.renovisor.bindings import BindingKind, Bindings
from hisim.renovisor.catalogue import Catalogue
from hisim.renovisor.inventory import Inventory
from hisim.renovisor.laws import StaticDemandEstimator
from hisim.renovisor.materials import InsulationMaterials
from hisim.renovisor.parametriser import (
    ConstructorSwaps,
    EditKind,
    ParametrisedSystem,
    Parametriser,
    ParametriserError,
    StaleLeaves,
)
from hisim.renovisor.reasons import ReasonCode, RefusalError
from hisim.renovisor.registry import MeasureRegistry
from hisim.renovisor.report import ReportStatus

pytestmark = pytest.mark.base

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
BASE_FILES = REPOSITORY_ROOT / "energy_systems"
EXAMPLE_INVENTORY_PATH = Path(__file__).resolve().parent / "renovisor" / "example_inventory_ie_1988_detached.json"
EXAMPLE_PACKAGE_PATH = Path(__file__).resolve().parent / "renovisor" / "example_package_gas_to_heat_pump.json"


def example_inventory() -> Inventory:
    """Return the example Irish 1988 detached house, loaded fresh."""
    return Inventory.from_dict(json.loads(EXAMPLE_INVENTORY_PATH.read_text(encoding="utf-8")))


def example_package() -> List[Dict[str, Any]]:
    """Return the committed gas-to-heat-pump package's measure list."""
    document = json.loads(EXAMPLE_PACKAGE_PATH.read_text(encoding="utf-8"))
    measures: List[Dict[str, Any]] = list(document["measures"])
    return measures


def apply(inventory: Inventory, package: List[Dict[str, Any]]) -> ApplicationResult:
    """Apply one package to one inventory with the committed catalogue and material table."""
    application = PackageApplication(Catalogue.load(), MeasureRegistry(), InsulationMaterials.load())
    return application.apply(inventory, package)


def parametrise(result: ApplicationResult) -> ParametrisedSystem:
    """Parametrise one application result with three stated demands, so no profile is loaded."""
    estimator = StaticDemandEstimator(12.0, 25.0, 0.0, generator=result.base_file_key.generator)
    return Parametriser(BASE_FILES).parametrise(result, estimator)


@pytest.fixture(name="result", scope="module")
def fixture_result() -> ApplicationResult:
    """The committed example package applied to the committed example dwelling."""
    return apply(example_inventory(), example_package())


@pytest.fixture(name="system", scope="module")
def fixture_system(result: ApplicationResult) -> ParametrisedSystem:
    """That result written into its base file."""
    return parametrise(result)


def test_the_building_is_built_from_its_tabula_code(system: ParametrisedSystem) -> None:
    """The recorded preset is one German house; this one is an Irish archetype by code."""
    entry = system.model.components["Building"]

    assert entry.preset is None
    assert entry.constructor is not None
    assert entry.constructor.name == "for_tabula_code"
    assert entry.constructor.arguments["building_code"].startswith("IE.N.SFH.")
    assert entry.constructor.arguments["absolute_conditioned_floor_area_in_m2"] == 157
    assert entry.constructor.arguments["building_heat_capacity_class"] == "medium"


def test_the_recorded_weather_identity_is_removed(system: ParametrisedSystem) -> None:
    """It is a sized field, so a recorded value pins it -- and it names Aachen.

    Verified against the configure stage in the step-5 review: a building keeping the recorded
    ``weather_identity`` resolves to Aachen's identity even with the weather set to Dublin, while
    one without it resolves to Dublin's. The law can only recompute a field the file leaves open.
    """
    entry = system.model.components["Building"]

    assert "weather_identity" not in entry.config
    assert "weather_identity" in load_energy_system(
        BASE_FILES / system.base_file_name
    ).components["Building"].config


def test_the_weather_is_the_station_of_the_dwellings_country(system: ParametrisedSystem) -> None:
    """The recorded complete Aachen configuration becomes one catalogue station by name."""
    entry = system.model.components["Weather"]

    assert entry.constructor is not None
    assert entry.constructor.name == "for_location"
    assert entry.constructor.arguments == {"location": "IE"}
    assert "source_path" not in entry.config
    assert "data_source" not in entry.config
    assert "location" not in entry.config


def test_the_photovoltaic_array_is_labelled_with_the_same_country(system: ParametrisedSystem) -> None:
    """One inventory leaf reaches a constructor and a plain field; both have to move."""
    assert system.model.components["PVSystem"].config["location"] == "IE"


def test_the_occupancy_is_the_matched_household_in_the_recorded_mode(system: ParametrisedSystem) -> None:
    """Decision Q20: the household changes, the local LoadProfileGenerator mode does not."""
    entry = system.model.components["UTSPConnector"]

    assert entry.constructor is not None
    assert entry.constructor.name == "for_household"
    assert entry.constructor.arguments["household"]["Name"].startswith("CHR")
    assert entry.constructor.arguments["data_acquisition_mode"] == "USE_LOCAL_LPG"
    assert entry.config["data_acquisition_mode"] == "USE_LOCAL_LPG"


def test_every_config_override_leaf_present_lands_on_its_target(
    result: ApplicationResult, system: ParametrisedSystem
) -> None:
    """Requirement R7 in its strongest form: no value the bindings route anywhere is dropped."""
    components = system.model.all_components()
    consumed = set(ConstructorSwaps.consumed_paths())
    checked = 0
    for path in result.inventory.leaf_paths():
        value = result.inventory.get(path)
        if value is None or path in consumed or path in result.pending_laws:
            continue
        binding = Bindings.resolve(path)
        if binding.kind is not BindingKind.CONFIG_OVERRIDE:
            continue
        if StaleLeaves.reason_for(path, result.inventory, generator_changed=True) is not None:
            continue
        for target in binding.targets:
            if target.component_key not in components:
                continue
            assert components[target.component_key].config[target.field_or_argument] == value, path
            checked += 1
    assert checked > 10, "the coverage check found almost nothing to check"


def test_the_resolved_law_lands_on_the_battery(system: ParametrisedSystem) -> None:
    """Decision Q12: two days of 12 + 25 + 0 kWh is what the recorded battery is given."""
    battery = system.model.variants["electricity_management"].options["ems_with_battery"].components["Battery"]

    assert battery.config["custom_battery_capacity_generic_in_kilowatt_hour"] == pytest.approx(
        2.0 * (12.0 + 25.0)
    )


def test_the_document_carries_nothing_that_varies_per_request(system: ParametrisedSystem) -> None:
    """Requirement R10: no timestamp, no hash, no job id in the name or the description."""
    assert system.model.name == "renovisor household_heatpump_building_sizer.grouped"
    assert system.model.description is not None
    assert "renovisor" in system.model.description.lower() or "RenoVisor" in system.model.description


def test_the_dump_is_byte_identical_across_two_runs() -> None:
    """Requirement R10 and acceptance criterion AC5: the same request produces the same file."""
    first = parametrise(apply(example_inventory(), example_package()))
    second = parametrise(apply(example_inventory(), example_package()))

    assert first.yaml_text == second.yaml_text


def test_the_edits_name_what_asked_for_each_of_them(system: ParametrisedSystem) -> None:
    """The trace page and the report both read the edits, so each carries its own provenance."""
    assert system.edits_of(EditKind.CONSTRUCTOR_SWAP)
    assert system.edits_of(EditKind.CONFIG_VALUE)
    assert system.edits_of(EditKind.VARIANT_SELECTION)
    for edit in system.edits:
        assert edit.source, edit.location
        assert edit.note, edit.location


def test_the_diff_rule_accepts_what_the_parametriser_produced(system: ParametrisedSystem) -> None:
    """The check runs on every parametrisation; this asserts it is not vacuously true."""
    system.assert_only_permitted_edits(load_energy_system(BASE_FILES / system.base_file_name))


def test_the_diff_rule_rejects_a_forged_input(system: ParametrisedSystem) -> None:
    """Requirement R4: authoring wiring is the one thing the parametriser must never do."""
    base = load_energy_system(BASE_FILES / system.base_file_name)
    entry = system.model.components["PVSystem"]
    forged_entry = entry.model_copy(
        update={"inputs": entry.inputs + (DefaultInputs(source="Building"),)}
    )
    components = dict(system.model.components)
    components["PVSystem"] = forged_entry
    forged = replace(system, model=system.model.model_copy(update={"components": components}))

    with pytest.raises(ParametriserError) as error:
        forged.assert_only_permitted_edits(base)

    assert "PVSystem.inputs" in str(error.value)


def test_the_diff_rule_rejects_a_new_component(system: ParametrisedSystem) -> None:
    """Requirement R4: the set of components is the base file's reviewed content."""
    base = load_energy_system(BASE_FILES / system.base_file_name)
    components = dict(system.model.components)
    components["SecondPVSystem"] = components["PVSystem"].model_copy(update={"name": "SecondPVSystem"})
    forged = replace(system, model=system.model.model_copy(update={"components": components}))

    with pytest.raises(ParametriserError) as error:
        forged.assert_only_permitted_edits(base)

    assert "SecondPVSystem" in str(error.value)


def test_the_diff_rule_rejects_a_preset_swap_on_a_component_that_has_no_constructor(
    system: ParametrisedSystem,
) -> None:
    """Only the three identifier-parameterised components may lose their recorded preset."""
    base = load_energy_system(BASE_FILES / system.base_file_name)
    components = dict(system.model.components)
    components["DHWStorage"] = components["DHWStorage"].model_copy(update={"preset": None})
    forged = replace(system, model=system.model.model_copy(update={"components": components}))

    with pytest.raises(ParametriserError) as error:
        forged.assert_only_permitted_edits(base)

    assert "DHWStorage.preset" in str(error.value)


def electric_heating_inventory() -> Inventory:
    """Return the example dwelling with its gas boiler replaced by electric heating.

    The electric-heating base file is the one recorded file with no ``HeatDistributionController``
    and no ``SimpleHotWaterStorage``, which is what makes it the case that exercises both halves of
    the missing-component rule.
    """
    inventory = example_inventory()
    inventory.set("energy_system_config.heating_system.system", "ELECTRIC_HEATING")
    return inventory


def test_a_base_state_leaf_whose_component_is_missing_is_reported_ignored() -> None:
    """The dwelling has radiators and the electric file has no heating controller; that is a fact."""
    result = apply(electric_heating_inventory(), [])

    parametrise(result)

    lines = {line["path"]: line for line in result.report.to_list()}
    emitter = lines["energy_system_config.heating_system.heat_distribution_system"]
    assert emitter["status"] == ReportStatus.IGNORED.value
    assert "has no HeatDistributionController" in emitter["note"]
    storage = lines["energy_system_config.water_storage.hot_water_storage.volume_in_liters"]
    assert storage["status"] == ReportStatus.IGNORED.value
    assert "has no SimpleHotWaterStorage" in storage["note"]


def test_a_measure_whose_component_is_missing_is_refused() -> None:
    """A requested renovation the base file cannot carry fails hard rather than being ignored."""
    result = apply(
        electric_heating_inventory(),
        [{"measure_id": "HEATING_INSTALLATION", "options": {"type_of_system": "FLOORHEATING"}}],
    )

    with pytest.raises(RefusalError) as error:
        parametrise(result)

    refusal = error.value.refusals[0]
    assert refusal.reason is ReasonCode.NO_BASE_FILE_FOR_COMBINATION
    assert refusal.path == "energy_system_config.heating_system.heat_distribution_system"
    assert "has no HeatDistributionController" in refusal.detail


def test_a_country_with_no_weather_station_is_refused() -> None:
    """A new reason code, because it is the request that cannot be served, not a crash.

    ``XX`` is TABULA's generic European archetype, which exists in the typology table and has no
    weather station named after it -- so the building resolves and the weather is what cannot,
    which is exactly the condition this reason code is for.
    """
    inventory = example_inventory()
    inventory.set("location.country_code", "XX")
    result = apply(inventory, [])

    with pytest.raises(RefusalError) as error:
        parametrise(result)

    assert error.value.refusals[0].reason is ReasonCode.NO_WEATHER_FOR_COUNTRY
    assert error.value.refusals[0].path == "location.country_code"


def test_a_superseded_device_size_is_reported_rather_than_written(
    result: ApplicationResult, system: ParametrisedSystem
) -> None:
    """The old boiler's rating must not size the new heat pump, and the array's power not the array."""
    lines = {line["path"]: line for line in result.report.to_list()}
    generator = system.model.components["MoreAdvancedHeatPumpHPLib"]

    assert lines[StaleLeaves.GENERATOR_POWER]["status"] == ReportStatus.IGNORED.value
    assert lines[StaleLeaves.PV_POWER]["status"] == ReportStatus.IGNORED.value
    assert generator.config["set_thermal_output_power_in_watt"] != 18000
    assert "power_in_watt" not in system.model.components["PVSystem"].config
