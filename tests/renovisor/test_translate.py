"""T-XLATE and T-DET: the file the translator writes, and that it writes the same one twice.

For every heat generator the dumped file loads back through ``load_energy_system``, the
generator entry is the twin's, and the diff against the twin carries nothing the rule forbids.
Every target of §3 of the contract receives its value, asserted on the model rather than on the
text. The report accounts for every leaf of the request exactly once. And two runs of the same
request produce identical bytes, whatever order the request's keys arrived in.

Decision D-D is under test here as much as the code is: the base files are the recorded grouped
twins, "no photovoltaics" is a zero-power pin on the array every twin carries, and a house
without a battery selects ``metered_directly`` rather than keeping the twin's default energy
management system, whose battery would size itself from an array producing nothing.
"""

import copy
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import pytest

from hisim.energy_system.loader import load_energy_system
from hisim.renovisor.apply import apply
from hisim.renovisor.constants import BuildingDefaults, DesignTemperatures, RoofDefaults, StorageDefaults
from hisim.renovisor.contract import ContractFiles
from hisim.renovisor.report import MappingReport
from hisim.renovisor.request import Request
from hisim.renovisor.translate import BaseFiles, Targets, TranslatedSystem, Translator
from hisim.renovisor.vocabulary import HeatGenerator, ReportStatus
from hisim.renovisor.whitelist import Whitelist

BASE_FILES = Path(__file__).resolve().parents[2] / "energy_systems"


def translate(document: Mapping[str, Any]) -> TranslatedSystem:
    """Validate, apply and translate one request document."""
    whitelist = Whitelist.load()
    request = Request.parse(copy.deepcopy(dict(document)))
    applied = apply(request.document["house"], request.measures, whitelist)
    return Translator(BASE_FILES, whitelist).translate(request, applied)


def baseline(**house: Any) -> Dict[str, Any]:
    """Return the mockup with an empty package and the given house paths overwritten."""
    document = copy.deepcopy(ContractFiles.request_mockup())
    document["measures"] = []
    for path, value in house.items():
        node: Any = document["house"]
        parts = path.split("__")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        if value is None:
            node.pop(parts[-1], None)
        else:
            node[parts[-1]] = value
    return document


def config_of(system: TranslatedSystem, component: str) -> Mapping[str, Any]:
    """Return one component's config out of the selected world of a translated model."""
    entry = _entry(system, component)
    return dict(entry.config) if entry is not None else {}


def constructor_of(system: TranslatedSystem, component: str) -> Optional[Mapping[str, Any]]:
    """Return one component's constructor arguments, or ``None`` when it has no constructor."""
    entry = _entry(system, component)
    call = getattr(entry, "constructor", None) if entry is not None else None
    return dict(call.arguments) if call is not None else None


def _entry(system: TranslatedSystem, component: str) -> Any:
    """Return one component entry from the top level, an enabled group or the selected option."""
    model = system.model
    if component in model.components:
        return model.components[component]
    for group in model.groups.values():
        if group.enabled and component in group.components:
            return group.components[component]
    for variant in model.variants.values():
        option = variant.options.get(variant.selected)
        if option is not None and component in option.components:
            return option.components[component]
    return None


@pytest.mark.base
class TestEveryGenerator:
    """Seventeen generators, nine twins, and a file that loads for every one of them."""

    @pytest.mark.parametrize("generator", list(HeatGenerator))
    def test_the_file_loads_back_and_names_the_twins_generator(self, generator: HeatGenerator) -> None:
        """The self-check of §5.1 step 3, and the generator entry of §5.2."""
        system = translate(baseline(heating__type_of_system=generator.value))

        assert load_energy_system(system.yaml_text) is not None
        assert system.base_file_name == BaseFiles.select(generator, with_solar_thermal=False)
        assert _entry(system, BaseFiles.generator_component(system.base_file_name)) is not None

    @pytest.mark.parametrize("generator", list(HeatGenerator))
    def test_the_file_carries_no_placeholder_and_changes_nothing_forbidden(
        self, generator: HeatGenerator
    ) -> None:
        """The diff rule runs inside ``translate``; this is the claim it makes, written down."""
        system = translate(baseline(heating__type_of_system=generator.value))

        assert "RENOVISOR_PLACEHOLDER" not in system.yaml_text
        assert system.model.name.startswith("renovisor_")

    @pytest.mark.parametrize(
        "generator, boiler_type",
        [
            (HeatGenerator.CONVENTIONAL_GAS_HEATING, "CONVENTIONAL"),
            (HeatGenerator.CONDENSING_OIL_HEATING, "CONDENSING"),
            (HeatGenerator.CONDENSING_GAS_HEATING, None),
            (HeatGenerator.CONVENTIONAL_OIL_HEATING, None),
        ],
    )
    def test_a_twin_whose_boiler_is_of_the_other_kind_is_re_typed(
        self, generator: HeatGenerator, boiler_type: Optional[str]
    ) -> None:
        """One config override turns the gas twin's condensing boiler into a conventional one."""
        system = translate(baseline(heating__type_of_system=generator.value))

        config = config_of(system, BaseFiles.generator_component(system.base_file_name))
        assert config.get(Targets.BOILER_TYPE) == boiler_type

    def test_a_solar_thermal_house_runs_the_twin_that_wires_a_collector(self) -> None:
        """Only gas and the heat pump have one, which is why the others are on the list."""
        system = translate(
            baseline(
                heating__type_of_system="condensing_gas_heating",
                solar_thermal_system={"supplies": "dhw_only"},
            )
        )

        assert "solar_thermal" in system.base_file_name
        assert _entry(system, Targets.SOLAR_THERMAL) is not None


@pytest.mark.base
class TestEveryTargetReceivesItsValue:
    """§3's "Target" column, asserted on the model rather than on the text."""

    def test_the_building_is_swapped_onto_its_tabula_constructor(self) -> None:
        """A TABULA code is an identifier, not a variant, so the preset cannot express it."""
        system = translate(baseline())

        arguments = constructor_of(system, Targets.BUILDING)
        assert arguments is not None
        assert arguments["building_code"].startswith("IE.N.SFH.")
        assert arguments["absolute_conditioned_floor_area_in_m2"] == 140
        assert arguments["number_of_apartments"] == BuildingDefaults.NUMBER_OF_APARTMENTS
        assert "heating_reference_temperature_in_celsius" not in arguments

    def test_the_weather_carries_the_reviewed_design_temperature(self) -> None:
        """The weather owns the design condition (HiSim #771).

        It is the per-country reviewed constant, not TABULA's degree-day base of 12 °C.
        """
        system = translate(baseline())

        arguments = constructor_of(system, Targets.WEATHER)
        assert arguments is not None
        assert arguments["heating_reference_temperature_in_celsius"] == DesignTemperatures.BY_COUNTRY["IE"]

    def test_the_recorded_weather_identity_is_removed(self) -> None:
        """Finding F1: a recorded value on a sized field pins it, and Aachen is not Dublin."""
        system = translate(baseline())

        assert "weather_identity" not in config_of(system, Targets.BUILDING)

    def test_the_weather_is_the_dwellings_own_country(self) -> None:
        """One station per country, chosen by its constructor rather than by a recorded path."""
        system = translate(baseline())

        assert constructor_of(system, Targets.WEATHER) == {
            "location": "IE",
            "heating_reference_temperature_in_celsius": DesignTemperatures.BY_COUNTRY["IE"],
        }
        assert "source_path" not in config_of(system, Targets.WEATHER)

    def test_the_occupancy_is_the_one_profile_the_image_ships(self) -> None:
        """Decision D-C, written explicitly so that no fallback chain is ever entered."""
        arguments = constructor_of(translate(baseline()), Targets.OCCUPANCY)

        assert arguments is not None
        assert arguments["data_acquisition_mode"] == "USE_PREDEFINED_PROFILE"
        assert arguments["household"]["Name"] == "CHR01 Couple both at Work"
        assert arguments["household"]["Guid"]["StrVal"]

    def test_every_element_u_value_lands_on_the_building(self) -> None:
        """Five elements, five config fields, each written verbatim from the request."""
        system = translate(baseline())

        config = config_of(system, Targets.BUILDING)
        assert config["facade_u_value_in_watt_per_m2_per_kelvin"] == 1.78
        assert config["roof_u_value_in_watt_per_m2_per_kelvin"] == 0.4
        assert config["floor_u_value_in_watt_per_m2_per_kelvin"] == 0.7
        assert config["window_u_value_in_watt_per_m2_per_kelvin"] == 3.7
        assert config["door_u_value_in_watt_per_m2_per_kelvin"] == 3.0

    def test_a_stated_area_pins_the_element_and_an_absent_one_does_not(self) -> None:
        """An absent area keeps the archetype's own, scaled to the conditioned floor area."""
        system = translate(baseline(building__facade={"u_value_in_watt_per_m2_per_kelvin": 1.1, "area_in_m2": 173}))

        config = config_of(system, Targets.BUILDING)
        assert config["facade_area_in_m2"] == 173
        assert "roof_area_in_m2" not in config

    def test_the_emitters_reach_the_heat_distribution_controller(self) -> None:
        """The request's word becomes the HiSim member's name, which the controller reads."""
        system = translate(baseline(heat_distribution={"type_of_system": "surface_heating"}))

        assert config_of(system, Targets.HEAT_DISTRIBUTION_CONTROLLER)[Targets.HEATING_SYSTEM] == "FLOORHEATING"

    def test_a_stated_room_set_point_reaches_the_building(self) -> None:
        """It propagates to the heat distribution controller as a fact, which is why it is written."""
        system = translate(baseline(building__set_heating_temperature_in_celsius=23))

        assert config_of(system, Targets.BUILDING)[Targets.SET_HEATING_TEMPERATURE] == 23

    def test_a_tank_insulation_measure_halves_the_storages_loss(self) -> None:
        """Half the class default, which is an approximation standing in for the pipes too."""
        document = copy.deepcopy(ContractFiles.request_mockup())
        document["measures"] = [{"id": "hot_water_tank_and_pipe_insulation"}]

        system = translate(document)

        config = config_of(system, Targets.DHW_STORAGE)
        assert config[Targets.STORAGE_HEAT_TRANSFER] == StorageDefaults.TANK_INSULATED_HEAT_TRANSFER


@pytest.mark.base
class TestTheSwitchesOfDecisionDD:
    """No ``pv`` group and no third EMS option: the twins as they stand, and what that means."""

    def test_a_house_without_photovoltaics_pins_the_array_to_zero(self) -> None:
        """No twin has a pv group, so absence is a zero-power array rather than a missing one."""
        system = translate(baseline(pv_system=None))

        assert config_of(system, Targets.PV)[Targets.POWER_IN_WATT] == 0
        line = system.report.line("house.pv_system")
        assert line is not None and line.status is ReportStatus.DEFAULTED

    def test_a_share_of_the_roof_leaves_the_power_to_hisim(self) -> None:
        """The array then moves with the roof, which is what the share is for."""
        system = translate(baseline(pv_system={"size_in_percent_of_roof_area": 60}))

        config = config_of(system, Targets.PV)
        assert config[Targets.SHARE_OF_ROOF] == pytest.approx(0.6)
        assert Targets.POWER_IN_WATT not in config

    def test_the_array_is_oriented_from_the_roof_when_the_request_says_nothing(self) -> None:
        """A pitched roof is 30 degrees and due south, both reported as defaults."""
        system = translate(baseline(pv_system={"size_in_percent_of_roof_area": 60}))

        config = config_of(system, Targets.PV)
        assert config[Targets.AZIMUTH] == RoofDefaults.AZIMUTH
        assert config[Targets.TILT] == RoofDefaults.TILT_BY_ROOF_SHAPE["pitched"]

    def test_a_house_without_a_battery_is_metered_directly(self) -> None:
        """The fix of c02bc801: the twins' default battery sizes itself from a zero-power array."""
        system = translate(baseline(battery=None))

        assert system.model.variants[Targets.ELECTRICITY_MANAGEMENT].selected == Targets.METERED_DIRECTLY

    def test_a_house_with_a_battery_selects_the_energy_management_system(self) -> None:
        """Capacity and inverter are pinned together, because the class's C-rate ties them."""
        system = translate(baseline(battery={"custom_battery_capacity_generic_in_kilowatt_hour": 8}))

        assert system.model.variants[Targets.ELECTRICITY_MANAGEMENT].selected == Targets.WITH_BATTERY
        config = config_of(system, Targets.BATTERY)
        assert config[Targets.BATTERY_CAPACITY] == 8
        assert config[Targets.BATTERY_INVERTER] == pytest.approx(8 * 500.0)


@pytest.mark.base
class TestTheReportAccountsForTheRequest:
    """Every leaf exactly once, every default with its value, every note from the list."""

    def test_the_report_covers_every_leaf_of_the_mockup(self) -> None:
        """The completeness check runs inside ``translate``; this is the claim written down."""
        document = copy.deepcopy(ContractFiles.request_mockup())

        system = translate(document)

        system.report.assert_complete(document)
        paths = {line.path for line in system.report.lines()}
        for leaf in MappingReport.request_leaves(document):
            assert any(leaf == path or leaf.startswith(f"{path}.") for path in paths), leaf

    def test_every_measure_appears_once_with_its_options(self) -> None:
        """Five measures in, five entries out, in package order."""
        document = copy.deepcopy(ContractFiles.request_mockup())

        system = translate(document)

        entries = system.report.to_json()["measures"]
        assert [entry["id"] for entry in entries] == [measure["id"] for measure in document["measures"]]

    def test_every_default_carries_the_value_it_applied(self) -> None:
        """A default without its value is a sentence nobody can check."""
        system = translate(baseline())

        for line in system.report.lines():
            if line.status is ReportStatus.DEFAULTED:
                assert line.value is not None, line.path

    def test_a_not_implemented_line_carries_its_entry_note_and_nothing_else(self) -> None:
        """Only the list may produce that status, and the note is the list's, verbatim."""
        system = translate(baseline())

        line = system.report.line("house.occupancy.number_of_residents")
        assert line is not None
        assert line.status is ReportStatus.NOT_IMPLEMENTED_YET
        assert line.note == (
            "Every household is simulated as a working couple (CHR01) until the "
            "LoadProfileGenerator is in the image."
        )

    def test_the_report_names_the_twin_and_the_file(self) -> None:
        """A stored report has to say what it was a report of."""
        system = translate(baseline())

        body = system.report.to_json()
        assert body["base_file"] == system.base_file_name
        assert body["energy_system_file"] == system.file_name
        assert body["translator"]["request_schema_version"] == 1


@pytest.mark.base
class TestDeterminism:
    """T-DET: the same request is the same bytes and the same file name."""

    def test_translating_twice_produces_identical_bytes(self) -> None:
        """Nothing in the file carries a clock, a hostname or an iteration order."""
        first = translate(baseline())
        second = translate(baseline())

        assert first.yaml_text == second.yaml_text
        assert first.file_name == second.file_name

    def test_key_order_changes_neither_the_bytes_nor_the_name(self) -> None:
        """The content hash is over the canonical body, so formatting cannot split one job in two."""
        import json

        document = baseline()
        reordered = json.loads(json.dumps(document, sort_keys=True))

        assert translate(document).yaml_text == translate(reordered).yaml_text

    def test_the_file_is_named_after_what_it_contains(self) -> None:
        """``renovisor_<hash>.energy_system.yaml``, with the same hash inside as a name."""
        system = translate(baseline())

        assert system.file_name.startswith("renovisor_")
        assert system.file_name.endswith(".energy_system.yaml")
        assert system.model.name == system.file_name[: -len(".energy_system.yaml")]
