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
from tests.assemblies.helpers import EMS, OCCUPANCY, WEATHER, Mocks, expand_text, site

REPOSITORY = Path(__file__).resolve().parents[2]


def expand_house():
    """The mock house, expanded against the mock library."""
    model = parse_energy_system(Mocks.HOUSE)
    lines = LineIndex.from_text(Mocks.HOUSE.read_text(encoding="utf-8"), Mocks.HOUSE.name)
    return expand_imports(model, AssemblyResolver([Mocks.LIBRARY]), lines=lines)


@pytest.mark.base
@pytest.mark.parametrize(
    "path", sorted((REPOSITORY / "energy_systems").rglob("*.energy_system.yaml")), ids=lambda path: path.name
)
def test_every_committed_file_expands_to_itself(path: Path) -> None:
    """Catches the expansion touching a file that imports nothing: it must be the very same object."""
    model = parse_energy_system(path)
    expanded, record = expand_imports(model, AssemblyResolver(()))
    assert expanded is model and record.is_empty
    assert dump_energy_system(expanded) == dump_energy_system(parse_energy_system(path))


@pytest.mark.base
def test_the_expansion_is_idempotent() -> None:
    """Catches a second expansion changing the flat file the first one produced."""
    flat, _record = expand_house()
    again, record = expand_imports(flat, AssemblyResolver([Mocks.LIBRARY]))
    assert again is flat and record.is_empty and flat.schema_version == 3


@pytest.mark.base
def test_site_entries_come_first_then_each_import_in_file_order() -> None:
    """Catches an evaluation order other than file order (no ``order:`` in v1)."""
    flat, _record = expand_house()
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


@pytest.mark.base
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


@pytest.mark.base
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


@pytest.mark.base
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


@pytest.mark.base
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


@pytest.mark.base
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


@pytest.mark.base
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


@pytest.mark.base
def test_an_enum_value_outside_its_values_is_refused() -> None:
    """Catches an enum parameter taking a value its declaration does not allow."""
    with pytest.raises(EnergySystemAssemblyError, match="EF-76") as refusal:
        expand_text(site(WEATHER, imports="pv: {assembly: mock/pv_array, parameters: {facing: north}}"))
    assert "facing" in str(refusal.value) and "'north'" in str(refusal.value)


@pytest.mark.base
@pytest.mark.parametrize("parameters", ["{share_of_roof: 0.5}", "{power_in_watt: none}"])
def test_an_exactly_one_of_constraint_is_checked_on_the_resolved_parameters(parameters: str) -> None:
    """Catches two alternatives both stated, or neither, reaching the component."""
    with pytest.raises(EnergySystemAssemblyError, match="EF-77") as refusal:
        expand_text(site(WEATHER, imports=f"pv: {{assembly: mock/pv_array, parameters: {parameters}}}"))
    assert "power_in_watt, share_of_roof" in str(refusal.value)


@pytest.mark.base
def test_an_import_named_like_a_component_is_refused() -> None:
    """Catches a verb's partner reference that could mean a component or an import."""
    with pytest.raises(EnergySystemAssemblyError, match="EF-52"):
        expand_text(site(WEATHER, EMS, imports="Ems: {assembly: mock/pv_array}"))
