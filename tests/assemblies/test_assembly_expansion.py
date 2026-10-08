"""The expansion of imports: byte identity, idempotency, addresses, parameters, variants, the record."""

from __future__ import annotations

from pathlib import Path

import pytest

from hisim.config import AddressStep, ComponentID, DisplayConfig
from hisim.energy_system.assemblies.expansion import expand_imports
from hisim.energy_system.assemblies.resolver import AssemblyResolver
from hisim.energy_system.errors import EnergySystemAssemblyError
from hisim.energy_system.loader import dump_energy_system, parse_energy_system
from hisim.energy_system.model import DefaultInputs, ExplicitWire
from hisim.energy_system.source_lines import LineIndex
from hisim.postprocessing.kpi_computation.kpi_structure import KpiAddressStep, KpiSource
from tests.assemblies.helpers import EMS, MOCKS, OCCUPANCY, WEATHER, Library, Mocks, build_text, expand_text, site
from tests.assemblies.mock_components import MockPVSystemConfig

REPOSITORY = Path(__file__).resolve().parents[2]
HEATER = "heater: {assembly: mock/electric_heater"


def expand_house():
    """The mock house, expanded against the mock library."""
    model = parse_energy_system(Mocks.HOUSE)
    lines = LineIndex.from_text(Mocks.HOUSE.read_text(encoding="utf-8"), Mocks.HOUSE.name)
    return expand_imports(model, AssemblyResolver([Mocks.LIBRARY]), lines=lines)


#: Every committed energy-system file that imports nothing; a composed file is checked by its gate
#: (``tests/assemblies/test_heatpump_twin_gate.py``).
FLAT_FILES = [
    path
    for path in sorted((REPOSITORY / "energy_systems").rglob("*.energy_system.yaml"))
    if not parse_energy_system(path).imports
]


@pytest.mark.assemblies
@pytest.mark.parametrize("path", FLAT_FILES, ids=lambda path: path.name)
def test_every_committed_file_expands_to_itself(path: Path) -> None:
    """Catches the expansion touching a file that imports nothing: it must be the very same object."""
    model = parse_energy_system(path)
    expanded, record = expand_imports(model, AssemblyResolver(()))
    assert expanded is model and record.is_empty
    assert dump_energy_system(expanded) == dump_energy_system(parse_energy_system(path))


@pytest.mark.assemblies
def test_the_expansion_is_idempotent() -> None:
    """Catches a second expansion changing the flat file the first one produced."""
    flat, _record = expand_house()
    again, record = expand_imports(flat, AssemblyResolver([Mocks.LIBRARY]))
    assert again is flat and record.is_empty and flat.schema_version == 3


@pytest.mark.assemblies
def test_site_entries_come_first_then_each_import_in_file_order() -> None:
    """Catches an evaluation order other than file order for a file without ``order:``, or a record not stating it."""
    flat, record = expand_house()
    assert list(flat.components) == [
        "Weather",
        "Occupancy",
        "Ems",
        "Monitor",
        "pv-east-PVSystem",
        "pv-west-PVSystem",
        "tank-Tank",
        "heater-Heater",
        "heater-Controller",
    ]
    assert record.sequence == tuple(flat.components) == tuple(record.to_document()["sequence"])


TANK_AND_HEATER = "tank: {assembly: mock/hot_water_tank{tank}}\nheater: {assembly: mock/electric_heater{heater}}"


def ordered(weather: str = "", occupancy: str = "", tank: str = "", heater: str = "") -> str:
    """A file of Weather, Occupancy, a tank and a heater, each with the given ``order:`` suffix."""
    return site(
        WEATHER.replace("}", weather + "}"),
        OCCUPANCY.replace("}", occupancy + "}"),
        imports=TANK_AND_HEATER.replace("{tank}", tank).replace("{heater}", heater),
    )


@pytest.mark.assemblies
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            ordered(occupancy=", order: 2", heater=", order: 1"),
            ["heater-Heater", "heater-Controller", "Occupancy", "Weather", "tank-Tank"],
        ),
        (  # the twin's interleaving: an import placed between two site entries
            ordered(weather=", order: 10", occupancy=", order: 30", tank=", order: 20"),
            ["Weather", "tank-Tank", "Occupancy", "heater-Heater", "heater-Controller"],
        ),
        (ordered(), ["Weather", "Occupancy", "tank-Tank", "heater-Heater", "heater-Controller"]),
    ],
    ids=["ordered-first", "interleaved", "file-order"],
)
def test_order_sorts_the_entries_carrying_it_then_the_others_follow_in_file_order(text: str, expected: list) -> None:
    """Catches ``order:`` ignored, splitting an import's block, or reordering the entries without it."""
    flat, record = expand_text(text)
    assert list(flat.components) == expected and list(record.sequence) == expected
    assert all(entry.order is None for entry in flat.components.values())


@pytest.mark.assemblies
def test_an_import_with_instances_is_one_block_in_written_order() -> None:
    """Catches ``order:`` on an import separating its instances or reversing them."""
    text = site(
        WEATHER.replace("}", ", order: 2}"),
        imports="pv: {assembly: mock/pv_array, order: 1, instances: {west: {azimuth_in_degree: 270}, east: {}}}",
    )
    assert list(expand_text(text)[0].components) == ["pv-west-PVSystem", "pv-east-PVSystem", "Weather"]


@pytest.mark.assemblies
def test_a_repeated_order_number_is_refused() -> None:
    """Catches two entries claiming one position, which would leave their sequence to chance."""
    with pytest.raises(EnergySystemAssemblyError, match="EF-7P") as refusal:
        expand_text(ordered(occupancy=", order: 1", heater=", order: 1"))
    assert "component 'Occupancy'" in str(refusal.value) and "import 'heater'" in str(refusal.value)


@pytest.mark.assemblies
def test_two_instances_get_one_step_addresses_and_their_kpi_sources_carry_them() -> None:
    """Catches two arrays sharing a name, or a KPI source losing the instance it came from."""
    flat, record = expand_house()
    east, west = record.addresses["pv-east-PVSystem"], record.addresses["pv-west-PVSystem"]
    assert east == ComponentID("PVSystem", path=(AddressStep("pv", "east"),), assembly="mock/pv_array")
    assert (east.key, west.key) == ("pv-east-PVSystem", "pv-west-PVSystem")
    assert east.display_name == "PV array, east, azimuth 90"
    source = KpiSource.for_component(east, DisplayConfig())
    assert source.path == (KpiAddressStep(import_key="pv", instance="east"),)
    assert (source.import_key, source.instance, source.member, source.assembly) == (
        "pv",
        "east",
        "PVSystem",
        "mock/pv_array",
    )
    assert source.display_name == "PV array, east, azimuth 90"
    assert flat.addresses == record.addresses


@pytest.mark.assemblies
def test_parameters_are_substituted_and_the_lowered_items_land_at_their_placeholders() -> None:
    """Catches a parameter not reaching its field, or a lowered item landing elsewhere than its placeholder."""
    flat, _record = expand_house()
    assert flat.components["pv-west-PVSystem"].config["azimuth"] == 270
    assert flat.components["pv-west-PVSystem"].config["power_in_watt"] == 3000
    assert flat.components["tank-Tank"].config["volume_in_liter"] == 200
    assert flat.components["Monitor"].inputs == (DefaultInputs(source="tank-Tank"), DefaultInputs(source="Ems"))
    assert flat.components["tank-Tank"].inputs == (
        DefaultInputs(source="Occupancy"),
        ExplicitWire(source="heater-Heater", input="ThermalPower", output="ThermalPower"),
    )
    assert flat.components["heater-Controller"].inputs == (
        DefaultInputs(source="tank-Tank"),
        DefaultInputs(source="Ems"),
    )
    assert flat.components["heater-Controller"].preset == "standard"


@pytest.mark.assemblies
def test_a_parameter_at_its_presets_value_auto_or_none_writes_no_config_line() -> None:
    """Catches the expansion writing a line the twin has not (G9, D28): a value the preset gives, AUTO or none."""
    imports = "pv: {assembly: mock/pv_array}\nbattery: {assembly: mock/battery}"
    flat, _record = expand_text(site(WEATHER, imports=imports))
    # azimuth 180 and tilt 30 are the rooftop preset's; the share resolves to none; power 5000 is an override.
    assert flat.components["pv-PVSystem"].config == {"power_in_watt": 5000}
    # The capacity resolves to AUTO, which leaves the field with its law.
    assert "capacity_in_kwh" not in flat.components["battery-Battery"].config


@pytest.mark.assemblies
def test_an_override_writes_its_line_and_the_record_keeps_every_parameter() -> None:
    """Catches G9 dropping an override, or the import record losing the values the expansion did not write."""
    flat, record = expand_house()
    assert flat.components["pv-east-PVSystem"].config == {"azimuth": 90, "power_in_watt": 5000}
    assert flat.components["pv-west-PVSystem"].config == {"azimuth": 270, "power_in_watt": 3000}
    assert flat.components["tank-Tank"].config == {"volume_in_liter": 200}
    east = record.instance("pv", "east")
    assert east.parameters_resolved == {
        "azimuth_in_degree": 90,
        "tilt_in_degree": 30,
        "power_in_watt": 5000,
        "share_of_roof": None,
        "facing": "east",
    }


@pytest.mark.assemblies
def test_a_member_without_a_preset_compares_with_the_field_default(tmp_path: Path) -> None:
    """Catches G9 comparing a member configured by its own block with anything but its class's field default."""
    library = Library(tmp_path)
    library.add(
        "mock/plain_tank",
        f"""
        schema_version: 4
        kind: assembly
        name: mock/plain_tank
        description: A tank configured by its own block.
        parameters:
          volume_in_liter:
            {{type: float, unit: LITER, default: 150, range: {{min: 50, max: 500}}, description: Volume.}}
        components:
          Tank:
            class: {MOCKS}.MockTank
            config: {{volume_in_liter: {{$param: volume_in_liter}}}}
        tests:
          bounds: [{{output: Tank.WaterTemperature, unit: CELSIUS, min: 0, max: 100}}]
          monotone: [{{parameter: volume_in_liter, kpi: Standby heat losses, member: Tank, direction: increasing}}]
        """,
    )
    flat, _record = expand_text(site(WEATHER, imports="tank: {assembly: mock/plain_tank}"), library.resolver())
    assert flat.components["tank-Tank"].config == {}
    flat, _record = expand_text(
        site(WEATHER, imports="tank: {assembly: mock/plain_tank, parameters: {volume_in_liter: 200}}"),
        library.resolver(),
    )
    assert flat.components["tank-Tank"].config == {"volume_in_liter": 200}


@pytest.mark.assemblies
def test_a_member_configured_by_a_named_constructor_always_writes_its_fed_fields(tmp_path: Path) -> None:
    """Pins G9's one exception: a constructor member's ``$param`` field is written even at the field's default.

    A named constructor computes its configuration from its arguments, so the expansion has no origin to
    compare with before the build; it writes every fed field that carries a value. Here
    ``predictive_control: false`` equals both the field default and what ``for_location`` gives, and is
    written all the same. Comparing with the field default instead would drop a value the constructor
    may set otherwise.
    """
    library = Library(tmp_path)
    library.add(
        "mock/built_weather",
        """
        schema_version: 4
        kind: assembly
        name: mock/built_weather
        description: A weather configured by its named constructor.
        parameters:
          predictive:
            {type: bool, default: false, description: Whether the weather offers predictions.}
        components:
          Weather:
            class: hisim.components.weather.Weather
            constructor:
              for_location: {location: AACHEN, data_source: DWD_TRY, heating_reference_temperature_in_celsius: -7.0}
            config: {predictive_control: {$param: predictive}}
        tests: {bounds: [], monotone: []}
        """,
    )
    flat, _record = expand_text(site(imports="weather: {assembly: mock/built_weather}"), library.resolver())
    assert flat.components["weather-Weather"].config == {"predictive_control": False}


@pytest.mark.assemblies
def test_the_import_record_states_what_each_instance_was_given_and_how_each_port_was_decided() -> None:
    """Catches an import record that cannot say which assembly ran, with what, bound to what."""
    _flat, record = expand_house()
    west = record.instance("pv", "west")
    assert west.assembly == "mock/pv_array" and len(west.sha256) == 64
    assert west.parameters_given == {"azimuth_in_degree": 270, "facing": "west", "power_in_watt": 3000}
    assert west.parameters_resolved["tilt_in_degree"] == 30 and west.parameters_resolved["share_of_roof"] is None
    assert west.reserved == {"installation_year": 2026}
    heater = record.instance("heater")
    assert heater.variants == {"thermostat": "fitted"}
    assert heater.reserved["installation_year"] == 2025 and heater.reserved["quote"]["per"] == "unit"
    ports = {port.port: port for port in heater.ports}
    assert (ports["ems_modifier"].decision, ports["ems_modifier"].verb, ports["ems_modifier"].partner) == (
        "bound",
        "optional-bind",
        "Ems",
    )
    assert (ports["tank_temperature"].verb, ports["tank_temperature"].partner) == ("default", "tank-Tank")
    tank = {port.port: port for port in record.instance("tank").ports}
    assert tank["heat"].lowered_to == ("tank-Tank.inputs: {input: ThermalPower, from: heater-Heater.ThermalPower}",)
    assert record.site_ports["Monitor"][0].partner == "tank-Tank"
    document = record.to_document()
    assert document["addresses"]["pv-east-PVSystem"] == {
        "path": [{"import": "pv", "instance": "east"}],
        "member": "PVSystem",
        "assembly": "mock/pv_array",
        "display_name": "PV array, east, azimuth 90",
    }


@pytest.mark.assemblies
def test_the_source_map_names_the_import_the_instance_and_both_files_lines() -> None:
    """Catches a produced item without a source-map entry, or an entry pointing at the wrong line."""
    _flat, record = expand_house()
    entries = record.source_map.entries
    component = entries[("pv-west-PVSystem", "component")]
    assert (component.import_key, component.instance, component.member) == ("pv", "west", "PVSystem")
    assert [location.text for location in component.chain] == [
        "house.energy_system.yaml:32",
        "mock/pv_array.assembly.yaml:24",
    ]
    lowered = entries[("pv-west-PVSystem", "inputs[0]")]
    assert lowered.note == "port weather bound to Weather (default)"
    assert lowered.chain[-1].text == "mock/pv_array.assembly.yaml:34"
    assert entries[("Monitor", "inputs[0]")].chain[-1].text == "house.energy_system.yaml:22"
    assert ("pv-west-PVSystem", "config.azimuth") in entries


@pytest.mark.assemblies
def test_a_variant_without_the_member_drops_it_its_inputs_and_its_ports() -> None:
    """Catches a member of an unselected option surviving, or its ports demanding a partner."""
    flat, record = expand_text(
        site(
            WEATHER, OCCUPANCY, imports="heater: {assembly: mock/electric_heater, parameters: {with_thermostat: false}}"
        )
    )
    assert list(flat.components) == ["Weather", "Occupancy", "heater-Heater"]
    assert flat.components["heater-Heater"].inputs == ()
    states = {port.port: port.decision for port in record.instance("heater").ports}
    assert states["tank_temperature"] == "inactive" and states["ems_modifier"] == "inactive"
    assert record.instance("heater").variants == {"thermostat": "none"}


@pytest.mark.assemblies
@pytest.mark.parametrize(
    ("parameters", "code", "names"),
    [
        ("{volume: 3}", "EF-76", ("volume", "volume_in_liter")),
        ("{volume_in_liter: big}", "EF-76", ("volume_in_liter", "'big' is not a number")),
        ("{volume_in_liter: 900}", "EF-76", ("volume_in_liter", "outside the range")),
    ],
)
def test_a_parameter_that_does_not_fit_is_refused(parameters: str, code: str, names: tuple) -> None:
    """Catches an unknown parameter, a wrong type or a value outside the range passing into a member."""
    with pytest.raises(EnergySystemAssemblyError, match=code) as refusal:
        expand_text(
            site(WEATHER, OCCUPANCY, imports=f"tank: {{assembly: mock/hot_water_tank, parameters: {parameters}}}")
        )
    for name in names + ("import 'tank'",):
        assert name in str(refusal.value)


@pytest.mark.assemblies
def test_an_enum_value_outside_its_values_is_refused() -> None:
    """Catches an enum parameter taking a value its declaration does not allow."""
    with pytest.raises(EnergySystemAssemblyError, match="EF-76") as refusal:
        expand_text(site(WEATHER, imports="pv: {assembly: mock/pv_array, parameters: {facing: north}}"))
    assert "facing" in str(refusal.value) and "'north'" in str(refusal.value)


@pytest.mark.assemblies
def test_an_exactly_one_of_constraint_left_with_no_member_stated_is_refused() -> None:
    """Catches the one written member set to none leaving the component with neither alternative."""
    with pytest.raises(EnergySystemAssemblyError, match="EF-77") as refusal:
        expand_text(site(WEATHER, imports="pv: {assembly: mock/pv_array, parameters: {power_in_watt: none}}"))
    message = str(refusal.value)
    for name in ("power_in_watt, share_of_roof", "0 are (none)", "import 'pv'"):
        assert name in message, f"{name!r} is not in: {message}"


@pytest.mark.assemblies
def test_stating_one_member_of_an_exactly_one_of_unstates_the_others_defaults(tmp_path: Path) -> None:
    """Catches the sibling's default (power 5000 W) still counting when the import writes only the share (D27)."""
    text = site(WEATHER, imports="pv: {assembly: mock/pv_array, parameters: {share_of_roof: 0.5}}")
    flat, record = expand_text(text)
    assert record.instance("pv").parameters_resolved["power_in_watt"] is None
    assert record.instance("pv").parameters_resolved["share_of_roof"] == 0.5
    config = flat.components["pv-PVSystem"].config
    # G9: the unstated power writes no line, so the preset's open power stays and the share is read.
    assert "power_in_watt" not in config and config["share_of_roof"] == 0.5
    built = dict(build_text(text, tmp_path).wired.components)["pv-PVSystem"].config
    assert built.power_in_watt is None
    assert built.peak_power_in_watt() == 0.5 * MockPVSystemConfig.ROOF_PEAK_POWER_IN_WATT


@pytest.mark.assemblies
def test_zero_is_a_stated_value_for_an_exactly_one_of_constraint() -> None:
    """Catches ``0 == False`` making a power of 0 count as unstated: alone it is the one stated value."""
    flat, record = expand_text(site(WEATHER, imports="pv: {assembly: mock/pv_array, parameters: {power_in_watt: 0}}"))
    assert record.instance("pv").parameters_resolved["power_in_watt"] == 0
    assert flat.components["pv-PVSystem"].config["power_in_watt"] == 0


@pytest.mark.assemblies
@pytest.mark.parametrize(
    "parameters", ["{power_in_watt: 0, share_of_roof: 0.5}", "{power_in_watt: 5000, share_of_roof: 0.5}"]
)
def test_an_import_writing_two_members_of_an_exactly_one_of_is_refused(parameters: str) -> None:
    """Catches two stated alternatives passing, where stating one unstates the other (0 is stated)."""
    with pytest.raises(EnergySystemAssemblyError, match="EF-77") as refusal:
        expand_text(site(WEATHER, imports=f"pv: {{assembly: mock/pv_array, parameters: {parameters}}}"))
    message = str(refusal.value)
    for name in ("states 2 of them (power_in_watt, share_of_roof)", "state one", "import 'pv'"):
        assert name in message, f"{name!r} is not in: {message}"


@pytest.mark.assemblies
def test_a_variant_selector_left_at_no_value_is_refused_by_name() -> None:
    """Catches an import writing ``none`` for a variant's selector crashing on an assertion."""
    with pytest.raises(EnergySystemAssemblyError, match="EF-76") as refusal:
        expand_text(site(WEATHER, OCCUPANCY, imports=f"{HEATER}, parameters: {{with_thermostat: none}}}}"))
    message = str(refusal.value)
    for name in ("with_thermostat", "thermostat", "None", "import 'heater'"):
        assert name in message, f"{name!r} is not in: {message}"


@pytest.mark.assemblies
def test_an_import_named_like_a_component_is_refused() -> None:
    """Catches a verb's partner reference that could mean a component or an import."""
    with pytest.raises(EnergySystemAssemblyError, match="EF-52"):
        expand_text(site(WEATHER, EMS, imports="Ems: {assembly: mock/pv_array}"))
