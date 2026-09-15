"""The five checks of ``measures_v2_requirements.md`` §7.5, plus the per-measure expectations.

These are the tests that turn drift into a failing build: a catalogue revision that adds a measure,
an option value nothing handles, a material spelling with no entry, or an inventory path the
contract does not know. The fifth check — every binding resolves in every base file — belongs to
step 5; what is checkable here is that every base file the selection table names exists.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from hisim.renovisor.base_files import BaseFiles
from hisim.renovisor.catalogue import (
    Catalogue,
    CatalogueSpellings,
    MeasureSpec,
    OptionSpec,
    OptionValueType,
)
from hisim.renovisor.effects import Effects, NoEffect, Refusal
from hisim.renovisor.envelope import RegulatoryTargets
from hisim.renovisor.inventory import Inventory, InventoryPathCheck
from hisim.renovisor.materials import InsulationMaterials
from hisim.renovisor.options import Options
from hisim.renovisor.reasons import ReasonCode, ValidationError
from hisim.renovisor.registry import MeasureRegistry
from hisim.renovisor.report import MappingReport
from hisim.renovisor.vocabulary import ThermalElement

pytestmark = pytest.mark.base

EXAMPLE_INVENTORY_PATH = Path(__file__).resolve().parent / "renovisor" / "example_inventory_ie_1988_detached.json"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

#: A usable value for each integer option the catalogue leaves open, so check 2 can call every
#: measure. The numbers are inside every range the measures impose; they are test inputs, not
#: defaults, and nothing in the library reads them.
SAMPLE_INTEGERS: Dict[str, int] = {
    "thickness_in_mm": 100,
    "power_in_watt": 3500,
    "size_in_percent_of_roof_area": 50,
    "days_to_cover": 2,
    "number": 1,
    "new_room_temperature": 21,
}


class FixedUValues:
    """A U-value source with one number for every element, so no TABULA row is needed."""

    def u_value(self, element: ThermalElement) -> float:
        """Return a fixed pre-measure U-value."""
        del element
        return 2.0

    def source_of(self, element: ThermalElement) -> str:
        """Return a fixed phrase naming this stub as the source."""
        del element
        return "a fixed test source"


@pytest.fixture(name="catalogue", scope="module")
def fixture_catalogue() -> Catalogue:
    """The vendored catalogue."""
    return Catalogue.load()


@pytest.fixture(name="materials", scope="module")
def fixture_materials() -> InsulationMaterials:
    """The committed material table."""
    return InsulationMaterials.load()


@pytest.fixture(name="targets", scope="module")
def fixture_targets() -> RegulatoryTargets:
    """The committed Irish target table."""
    return RegulatoryTargets.load()


@pytest.fixture(name="inventory", scope="module")
def fixture_inventory() -> Inventory:
    """The example Irish 1988 detached house, read only in these tests."""
    return Inventory.from_dict(json.loads(EXAMPLE_INVENTORY_PATH.read_text(encoding="utf-8")))


def run_measure(
    spec: MeasureSpec,
    supplied: Dict[str, Any],
    materials: InsulationMaterials,
    targets: RegulatoryTargets,
    inventory: Inventory,
) -> Effects:
    """Run one registry function over one set of option values and return its effects."""
    effects = Effects(materials, FixedUValues(), targets)
    options = Options(spec, supplied, MappingReport(), "package.measures[0]")
    MeasureRegistry.function_for(spec.measure_id)(options, inventory, effects)
    return effects


def option_cases(spec: MeasureSpec) -> List[Dict[str, Any]]:
    """Return one set of option values per value of the measure's first enumerated option.

    Measures with no enumerated option get a single case; measures with one get a case per value,
    which is what check 2 needs — every catalogue value has to be accepted by its function.
    """
    base: Dict[str, Any] = {}
    varied: Tuple[str, Tuple[Any, ...]] = ("", ())
    for option in spec.options:
        if option.values and not varied[0]:
            varied = (option.option_id, tuple(sample_values(option)))
            continue
        if option.value_type is OptionValueType.INTEGER:
            base[option.option_id] = SAMPLE_INTEGERS[option.option_id]
        elif option.value_type is OptionValueType.BOOLEAN:
            base[option.option_id] = True
    if not varied[0]:
        return [base]
    return [{**base, varied[0]: value} for value in varied[1]]


def sample_values(option: OptionSpec) -> List[Any]:
    """Return the catalogue's values for one option, as the JSON types a request would send."""
    if option.value_type is OptionValueType.INTEGER:
        return [int(value) for value in option.values]
    return list(option.values)


def test_check_one_bijection_with_the_catalogue(catalogue: Catalogue) -> None:
    """Check 1: every catalogue measure has a function and every function a catalogue measure."""
    assert set(catalogue.measure_ids()) == set(MeasureRegistry.BY_ID)
    assert len(MeasureRegistry.BY_ID) == 33


def test_check_two_every_option_value_is_handled(
    catalogue: Catalogue, materials: InsulationMaterials, targets: RegulatoryTargets, inventory: Inventory
) -> None:
    """Check 2: every value the catalogue lists is accepted, with the Experts options defaulted."""
    for spec in catalogue.measures():
        for supplied in option_cases(spec):
            effects = run_measure(spec, supplied, materials, targets, inventory)
            assert effects.all(), f"{spec.measure_id} with {supplied} produced no effect at all"


def test_check_two_experts_options_may_be_omitted(
    catalogue: Catalogue, materials: InsulationMaterials, targets: RegulatoryTargets, inventory: Inventory
) -> None:
    """Every Experts option has a default, so a request that omits all of them still works."""
    for spec in catalogue.measures():
        supplied = {
            option.option_id: (
                SAMPLE_INTEGERS[option.option_id]
                if option.value_type is OptionValueType.INTEGER and not option.values
                else sample_values(option)[0]
            )
            for option in spec.options
            if option.access_level.value == "EVERYONE"
        }
        effects = run_measure(spec, supplied, materials, targets, inventory)
        assert effects.all(), f"{spec.measure_id} needs an Experts option it has no default for"


def test_check_two_an_unknown_value_is_a_validation_error(
    catalogue: Catalogue, materials: InsulationMaterials, targets: RegulatoryTargets, inventory: Inventory
) -> None:
    """A value the catalogue does not list is rejected, not passed through."""
    for spec in catalogue.measures():
        enumerated = [option for option in spec.options if option.values]
        if not enumerated:
            continue
        option = enumerated[0]
        supplied: Dict[str, Any] = {option.option_id: "NOT_A_CATALOGUE_VALUE"}
        with pytest.raises(ValidationError) as error:
            run_measure(spec, supplied, materials, targets, inventory)
        assert error.value.reason in (
            ReasonCode.UNKNOWN_OPTION_VALUE,
            ReasonCode.OPTION_TYPE_MISMATCH,
        ), f"{spec.measure_id} accepted a made-up value"


def test_check_three_every_material_value_resolves_or_is_marked(
    catalogue: Catalogue, materials: InsulationMaterials
) -> None:
    """Check 3: each material value is either a real asp_id or explicitly unresolved."""
    for spec in catalogue.measures():
        for option in spec.options:
            if option.option_id != "material":
                continue
            for value in option.values:
                if CatalogueSpellings.is_unresolved(spec.measure_id, "material", value):
                    assert not materials.contains(value), (
                        f"{spec.measure_id}.material '{value}' is marked unresolved but has a database row"
                    )
                else:
                    assert materials.contains(value), (
                        f"{spec.measure_id}.material '{value}' is neither a database row nor "
                        "marked unresolved"
                    )


def test_check_three_every_material_value_has_a_table_entry(catalogue: Catalogue) -> None:
    """Every material the catalogue offers is answered for, one way or the other."""
    for spec in catalogue.measures():
        for option in spec.options:
            if option.option_id != "material":
                continue
            entries = CatalogueSpellings.BY_OPTION.get((spec.measure_id, "material"), {})
            assert len(entries) == len(option.display_values), (
                f"{spec.measure_id}.material has {len(option.display_values)} catalogue values and "
                f"{len(entries)} spelling-table entries"
            )


def test_check_four_every_written_path_is_known_to_the_contract(
    catalogue: Catalogue, materials: InsulationMaterials, targets: RegulatoryTargets, inventory: Inventory
) -> None:
    """Check 4: each path the registry writes is declared by the schema or listed as pending."""
    written: set = set()
    for spec in catalogue.measures():
        for supplied in option_cases(spec):
            effects = run_measure(spec, supplied, materials, targets, inventory)
            resolved = effects.resolve(inventory, FixedUValues())
            written |= set(resolved.writes) | set(resolved.pending_laws)
            written |= {Effects.u_value_path(element) for element in resolved.u_values}

    assert written, "the registry wrote nothing at all, so the check proves nothing"
    assert InventoryPathCheck.unknown_in(tuple(written)) == ()


def test_check_five_every_base_file_the_table_names_exists() -> None:
    """The recorded files the selection table points at are really in the repository."""
    directory = REPOSITORY_ROOT / BaseFiles.DIRECTORY
    for file_name in BaseFiles.file_names():
        assert (directory / file_name).is_file(), f"{file_name} is named by BaseFiles but not in {directory}"


def test_the_selection_table_covers_the_recorded_fleet() -> None:
    """Eleven recorded files, eleven entries: a file added without an entry is unreachable."""
    assert len(BaseFiles.BY_KEY) == 11
    assert len(BaseFiles.file_names()) == 11


@pytest.mark.parametrize(
    "measure_id,supplied,reason",
    [
        ("ADD_INTERNAL_DRY_LINING", {}, ReasonCode.UNDEFINED_MEASURE_BUILDUP),
        ("BASEMENT_INTERNAL_INSULATION", {}, ReasonCode.UNDEFINED_MEASURE_BUILDUP),
        ("TOP_FLOOR_CEILING_INSULATION", {}, ReasonCode.UNDEFINED_MEASURE_BUILDUP),
        ("WINDOW_REPLACEMENT", {"glazing_panes": 3}, ReasonCode.MATERIAL_NOT_IN_DATABASE),
        ("DOOR_REPLACEMENT", {"glazing_panes": 0}, ReasonCode.MATERIAL_NOT_IN_DATABASE),
        ("WARM_ROOF_INSULATION", {"material": "PIR"}, ReasonCode.MATERIAL_NOT_IN_DATABASE),
        ("AIR_CONDITIONERS", {"power_in_watt": 3500}, ReasonCode.NO_BASE_FILE_FOR_COMBINATION),
        (
            "HEATING_SYSTEM",
            {"type_of_system": "HYBRID_HEAT_PUMP"},
            ReasonCode.UNSUPPORTED_SYSTEM,
        ),
        ("HEATING_SYSTEM", {"type_of_system": "HVO_HEATING"}, ReasonCode.NO_BASE_FILE_FOR_COMBINATION),
        ("HOT_WATER_SYSTEM", {"supply": "DIRECT_ELECTRIC"}, ReasonCode.UNSUPPORTED_SYSTEM),
        (
            "SOLAR_THERMAL_SYSTEM",
            {"supplies": "SPACE_HEATING_ONLY"},
            ReasonCode.NO_BASE_FILE_FOR_COMBINATION,
        ),
        ("ELECTRIC_VEHICLE", {"number": 2}, ReasonCode.TOO_MANY_VEHICLES),
    ],
)
def test_the_measures_that_refuse(
    catalogue: Catalogue,
    materials: InsulationMaterials,
    targets: RegulatoryTargets,
    inventory: Inventory,
    measure_id: str,
    supplied: Dict[str, Any],
    reason: ReasonCode,
) -> None:
    """Each refusal is a stated decision, so each is pinned with the reason code it carries."""
    effects = run_measure(catalogue.by_id(measure_id), supplied, materials, targets, inventory)

    refusals = [effect for effect in effects.all() if isinstance(effect, Refusal)]
    assert [item.reason for item in refusals] == [reason]


@pytest.mark.parametrize(
    "measure_id,supplied,reason",
    [
        ("OUTSIDE_SHADING", {}, ReasonCode.NO_SHADING_MODEL),
        ("SHALLOW_AIR_TIGHTNESS_MEASURES", {}, ReasonCode.NO_INFILTRATION_MODEL),
        ("DIY_SEALING_OF_AIR_LEAKS", {}, ReasonCode.NO_INFILTRATION_MODEL),
        ("VENTILATION_SYSTEM", {"type_of_system": "MECHANICAL_EXTRACT"}, ReasonCode.NO_VENTILATION_MODEL),
        (
            "TEMPERATURE_CONTROL_SYSTEM",
            {"type_of_system": "SMART_HEATING_CONTROL_SYSTEM"},
            ReasonCode.NO_CONTROL_SCHEDULE_MODEL,
        ),
        ("REPLACE_WHITE_APPLIANCES", {}, ReasonCode.NO_APPLIANCE_SUBMODEL),
        ("INSTALL_NEW_LED_LIGHTS", {}, ReasonCode.NO_APPLIANCE_SUBMODEL),
        ("THERMOCOVER_FOR_THE_WINDOWS", {}, ReasonCode.NO_BEHAVIOUR_MODEL),
        ("OPTIMIZE_BEHAVIOUR_FOR_SELF_CONSUMPTION_OF_PV", {}, ReasonCode.NO_BEHAVIOUR_MODEL),
    ],
)
def test_the_measures_with_no_model(
    catalogue: Catalogue,
    materials: InsulationMaterials,
    targets: RegulatoryTargets,
    inventory: Inventory,
    measure_id: str,
    supplied: Dict[str, Any],
    reason: ReasonCode,
) -> None:
    """Requirement M8: nine measures are accepted, change nothing, and say so with a reason."""
    effects = run_measure(catalogue.by_id(measure_id), supplied, materials, targets, inventory)

    assert [effect.reason for effect in effects.all() if isinstance(effect, NoEffect)] == [reason]


def test_biomass_is_simulated_as_pellets(
    catalogue: Catalogue, materials: InsulationMaterials, targets: RegulatoryTargets, inventory: Inventory
) -> None:
    """Decision Q14: biomass runs the pellet file and the report says the substitution happened."""
    effects = Effects(materials, FixedUValues(), targets)
    report = MappingReport()
    options = Options(
        catalogue.by_id("HEATING_SYSTEM"),
        {"type_of_system": "BIOMASS_HEATING"},
        report,
        "package.measures[0]",
    )
    MeasureRegistry.heating_system(options, inventory, effects)

    resolved = effects.resolve(inventory, FixedUValues())
    assert resolved.base_file.generator.value == "PELLET_HEATING"
    assert resolved.writes["energy_system_config.heating_system.system"] == "PELLET_HEATING"
    line = next(item for item in report.to_list() if item["path"] == "package.measures[0]")
    assert line["status"] == "APPROXIMATED"
    assert "Q14" in line["rule"]


def test_photovoltaics_writes_a_share_not_a_percentage(
    catalogue: Catalogue, materials: InsulationMaterials, targets: RegulatoryTargets, inventory: Inventory
) -> None:
    """Requirement M5: the post-measure inventory is in its own units, so 40% becomes 0.4."""
    effects = run_measure(
        catalogue.by_id("PHOTOVOLTAIC_SYSTEM"),
        {"size_in_percent_of_roof_area": 40},
        materials,
        targets,
        inventory,
    )

    resolved = effects.resolve(inventory, FixedUValues())
    assert resolved.writes["energy_system_config.photovoltaics.share_of_maximum_pv_potential"] == pytest.approx(0.4)


def test_the_battery_selects_the_variant_and_defers_the_capacity(
    catalogue: Catalogue, materials: InsulationMaterials, targets: RegulatoryTargets, inventory: Inventory
) -> None:
    """Decision Q12: the capacity needs simulation inputs, so only the law is recorded now."""
    effects = run_measure(
        catalogue.by_id("BATTERY_SYSTEM"), {"days_to_cover": 2}, materials, targets, inventory
    )

    resolved = effects.resolve(inventory, FixedUValues())
    assert resolved.variant_selections == {"electricity_management": "ems_with_battery"}
    assert "energy_system_config.battery_storage.capacity_in_kwh" in resolved.pending_laws


def test_every_registry_function_names_its_decisions(catalogue: Catalogue) -> None:
    """Decision V2: the translation map reads the decision ids off these docstrings."""
    for measure_id in catalogue.measure_ids():
        docstring = MeasureRegistry.function_for(measure_id).__doc__ or ""
        lines = [line.strip() for line in docstring.strip().splitlines() if line.strip()]
        assert lines, f"{measure_id} has no docstring"
        assert lines[-1].startswith("Decisions:"), f"{measure_id} has no 'Decisions:' line"
