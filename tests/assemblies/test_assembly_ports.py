"""Binding ports: the load-time refusals and the wiring refusals of lowered items (``assemblies_spec.md`` §3.1–§3.3).

Every refusal is a named error of the ``EF-7x`` band whose message names the import (and instance),
the port and carries the source map of the import. What the files decide is refused by the
expansion, naming the port's partner classes and every candidate and ending in a paste-ready
``bind:`` line where one exists. What only the constructed components say — a member's default
connections from its partner's class, a wired input or output, a provided output — the wiring stage
refuses like any connection, and the refusal names the port the item came from.
"""

import re
from pathlib import Path

import jsonschema
import pytest
import yaml

from hisim.energy_system.assemblies.record import LoweredKind
from hisim.energy_system.errors import EnergySystemAssemblyError, EnergySystemFormatError, EnergySystemWiringError
from hisim.energy_system.imports_model import PortState
from hisim.energy_system.model import DefaultInputs, ExplicitWire
from hisim.energy_system.schema_export import build_structural_schema
from tests.assemblies.helpers import EMS, OCCUPANCY, WEATHER, Library, Mocks, build_text, expand_text, site

#: A second weather station, for an ambiguous partner.
WEATHER_2 = WEATHER.replace("Weather:", "Weather2:")

#: An import of one PV instance, the subject of most refusals here.
PV_EAST = "imports:\n  pv:\n    assembly: mock/pv_array\n    instances: {east: {}}\n"


def refusal(text: str, library: Library = None) -> str:  # type: ignore[assignment]
    """Expands a file that must be refused and returns the message."""
    with pytest.raises(EnergySystemAssemblyError) as raised:
        expand_text(text, library.resolver() if library is not None else None)
    return str(raised.value)


def build_refusal(text: str, result_directory: Path, library: Library = None) -> str:  # type: ignore[assignment]
    """Builds a file whose expansion succeeds and whose wiring refuses a lowered item; the message."""
    expand_text(text, library.resolver() if library is not None else None)
    with pytest.raises(EnergySystemAssemblyError) as raised:
        build_text(text, result_directory, library.resolver() if library is not None else None)
    return str(raised.value)


@pytest.mark.base
def test_a_required_port_without_a_partner_is_refused() -> None:
    """EF-7A: names the instance, the port, its partner class, no candidates, a bind line, the source map."""
    message = refusal(site(OCCUPANCY) + PV_EAST)

    assert message.startswith("EF-7A at import pv[east]: required port 'weather' (partner MockWeather) has no partner")
    assert "candidates: none" in message
    assert re.search(r"\(import pv\[east\], inline.energy_system.yaml:\d+ → mock/pv_array.assembly.yaml:\d+\)", message)
    assert "add to the import 'pv' one of `bind: {weather: <partner>}`" in message


@pytest.mark.base
def test_a_port_with_several_candidates_and_no_verb_is_refused() -> None:
    """EF-7B: every candidate listed, one paste-ready line each."""
    message = refusal(site(WEATHER, WEATHER_2) + PV_EAST)

    assert message.startswith(
        "EF-7B at import pv[east]: port 'weather' (partner MockWeather) has 2 candidates Weather, Weather2"
    )
    assert "`bind: {weather: Weather}`, `bind: {weather: Weather2}`" in message


@pytest.mark.base
def test_declining_a_required_port_is_refused() -> None:
    """EF-7C: ``none:`` on a required port."""
    message = refusal(site(WEATHER) + PV_EAST + "    none: [weather]\n")

    assert message.startswith("EF-7C at import pv[east]: port 'weather' (partner MockWeather) is required")
    assert "candidates: Weather" in message
    assert "`bind: {weather: Weather}`" in message


@pytest.mark.base
def test_binding_an_absent_partner_is_refused() -> None:
    """EF-7D: the partner a ``bind:`` names does not exist; the real candidate is offered."""
    message = refusal(site(WEATHER) + PV_EAST + "    bind: {weather: Sky}\n")

    assert message.startswith("EF-7D at import pv[east]: port 'weather' (partner MockWeather) is bound to 'Sky'")
    assert "'Sky' names no component and no import of the file" in message
    assert "candidates: Weather" in message and "`bind: {weather: Weather}`" in message


@pytest.mark.base
def test_an_optional_port_with_a_candidate_and_no_verb_is_refused() -> None:
    """EF-7E (D8 i): the intent must be written; all three verbs are offered."""
    message = refusal(site(WEATHER, OCCUPANCY, EMS) + "imports:\n  dhw: {assembly: mock/storage_water_heater}\n")

    assert message.startswith(
        "EF-7E at import dhw: optional port 'ems_modifier' (partner MockEms) has 1 candidate Ems and no verb"
    )
    assert "`optional-bind: {ems_modifier: Ems}`, `bind: {ems_modifier: Ems}`, `none: [ems_modifier]`" in message


@pytest.mark.base
def test_a_verb_on_an_inactive_port_is_refused() -> None:
    """EF-7F: without a thermostat the modifier port is inactive, and binding it is a mistake."""
    message = refusal(
        site(WEATHER, EMS)
        + "imports:\n  heater: {assembly: mock/electric_heater, parameters: {with_thermostat: false}, "
        "bind: {ems_modifier: Ems}}\n"
    )

    assert message.startswith("EF-7F at import heater: port 'ems_modifier' is inactive with these parameters")
    assert "the import 'heater' writes 'bind' for it" in message


@pytest.mark.base
def test_an_inner_port_neither_bound_nor_re_exported_is_refused(tmp_path: Path) -> None:
    """EF-7G (§2.5): the tank's heat need has no partner inside and no re-export."""
    library = Library(tmp_path)
    library.add(
        "broken/open_tank",
        """
        schema_version: 4
        kind: assembly
        name: broken/open_tank
        imports:
          tank: {assembly: mock/hot_water_tank}
        interface:
          needs:
            hot_water_demand: {from: tank.hot_water_demand}
        """,
        contract=True,
    )

    message = refusal(site(WEATHER, OCCUPANCY) + "imports:\n  dhw: {assembly: broken/open_tank}\n", library)

    assert message.startswith(
        "EF-7G at import dhw → tank: required port 'heat' (partner MockHeater) of the inner import is neither bound "
        "nor re-exported inside 'broken/open_tank.assembly.yaml'; candidates: none"
    )
    assert "`heat: {from: tank.heat}`" in message
    assert (
        "a line on the inner import 'tank' in broken/open_tank.assembly.yaml one of `bind: {heat: <partner>}`"
        in message
    )


@pytest.mark.base
def test_binding_a_partner_of_another_class_is_refused() -> None:
    """EF-7H: the occupancy is no weather station."""
    message = refusal(site(WEATHER, OCCUPANCY) + PV_EAST + "    bind: {weather: Occupancy}\n")

    assert message.startswith(
        "EF-7H at import pv[east]: port 'weather' is bound to Occupancy (MockOccupancy), which is not of its partner "
        "class MockWeather"
    )
    assert re.search(r"\(import pv\[east\], inline.energy_system.yaml:\d+ → mock/pv_array.assembly.yaml:\d+\)", message)
    assert "Candidates: Weather; add to the import 'pv' one of `bind: {weather: Weather}`" in message


@pytest.mark.base
def test_binding_a_provider_the_member_declares_no_default_connections_from_is_refused(tmp_path: Path) -> None:
    """EF-7H from the wiring: the constructed tank declares no default connections from a PV system."""
    message = build_refusal(
        site(WEATHER, OCCUPANCY)
        + "imports:\n  pv: {assembly: mock/pv_array}\n"
        + "  tank: {assembly: mock/hot_water_tank, bind: {heat: pv.production}}\n",
        tmp_path,
    )

    assert message.startswith(
        "EF-7H at import tank: port 'heat' is bound to pv-PVSystem (MockPVSystem) by bind and lowers to the bare "
        "name 'pv-PVSystem' in tank-Tank (MockTank) (import tank, inline.energy_system.yaml:"
    )
    assert re.search(r"\(import tank, inline.energy_system.yaml:\d+ → mock/hot_water_tank.assembly.yaml:\d+\)", message)
    assert "the wiring refuses it: the bare item 'pv-PVSystem' of 'tank-Tank' cannot be expanded" in message
    assert "Valid source classes: MockHeater, MockOccupancy." in message
    assert message.endswith(
        "Add the default connection from MockPVSystem to MockTank, or bind the port to a partner of a class it "
        "declares default connections from."
    )


#: An assembly whose member declares no default connections at all.
BARE_DEVICE = """
    schema_version: 4
    kind: assembly
    name: broken/bare
    components:
      Device:
        class: tests.assemblies.mock_components.MockBareDevice
        preset: standard
        inputs: [{$port: weather}]
    interface:
      needs:
        weather: {into: [Device], partner: MockWeather}
    """


@pytest.mark.base
def test_a_member_without_default_connections_from_its_partner_is_refused_once_constructed(tmp_path: Path) -> None:
    """EF-7H: the expansion binds by class name; the wiring finds no default connections on the constructed device."""
    library = Library(tmp_path)
    library.add("broken/bare", BARE_DEVICE, contract=True)

    message = build_refusal(site(WEATHER) + "imports:\n  device: {assembly: broken/bare}\n", tmp_path / "r", library)

    assert message.startswith(
        "EF-7H at import device: port 'weather' is bound to Weather (MockWeather) by default and lowers to the bare "
        "name 'Weather' in device-Device (MockBareDevice)"
    )
    assert re.search(r"\(import device, inline.energy_system.yaml:\d+ → broken/bare.assembly.yaml:\d+\)", message)
    assert "'device-Device' (tests.assemblies.mock_components.MockBareDevice) declares no default connections for " \
        "the class 'MockWeather'" in message
    assert "Add the default connection from MockWeather to MockBareDevice" in message


@pytest.mark.base
def test_a_verb_naming_a_port_the_assembly_does_not_have_is_refused() -> None:
    """EF-7M: the port list is offered."""
    message = refusal(site(WEATHER) + PV_EAST + "    bind: {wether: Weather}\n")

    assert message.startswith("EF-7M at imports.pv: a verb names the port 'wether'")
    assert "Did you mean: weather?" in message


@pytest.mark.base
def test_an_optional_bind_to_an_absent_partner_is_recorded_not_bound() -> None:
    """``optional-bind:`` never fails: the record says why the port stayed unbound."""
    _expanded, record = expand_text(
        site(WEATHER, OCCUPANCY)
        + "imports:\n  dhw: {assembly: mock/storage_water_heater, optional-bind: {ems_modifier: control}}\n"
    )

    port = record.instance("dhw").port("ems_modifier")  # type: ignore[union-attr]
    assert port is not None
    assert port.state == PortState.OPTIONAL and port.verb == "optional-bind"
    assert port.partner == "not bound: partner absent ('control' names no component and no import of the file)"


@pytest.mark.base
def test_explicit_wires_lower_to_wires_and_their_inputs_are_checked(tmp_path: Path) -> None:
    """A port's ``wires:`` lowers to explicit wires; the wiring refuses one naming no input, naming the port."""
    library = Library(tmp_path)
    wired = """
        schema_version: 4
        kind: assembly
        name: wired/pv
        components:
          PVSystem:
            class: tests.assemblies.mock_components.MockPVSystem
            preset: rooftop
            inputs: [{{$port: weather, wires: {{{target}: TemperatureOutside}}}}]
        interface:
          needs:
            weather: {{into: [PVSystem], partner: MockWeather}}
        """
    library.add("wired/pv", wired.format(target="TemperatureOutside"), contract=True)
    library.add(
        "wired/broken", wired.format(target="Irradiance").replace("wired/pv", "wired/broken"), contract=True
    )

    expanded, _record = expand_text(site(WEATHER) + "imports:\n  pv: {assembly: wired/pv}\n", library.resolver())
    assert expanded.components["pv-PVSystem"].inputs == (
        ExplicitWire(source="Weather", input="TemperatureOutside", output="TemperatureOutside"),
    )

    message = build_refusal(site(WEATHER) + "imports:\n  pv: {assembly: wired/broken}\n", tmp_path / "r", library)
    assert message.startswith(
        "EF-7J at import pv: port 'weather' is bound to Weather (MockWeather) by default and lowers to the wire "
        "'Irradiance' from 'TemperatureOutside' in pv-PVSystem (MockPVSystem)"
    )
    assert "names the input 'Irradiance', which 'pv-PVSystem'" in message
    assert re.search(r"\(import pv, inline.energy_system.yaml:\d+ → wired/broken.assembly.yaml:\d+\)", message)
    assert "Valid inputs: TemperatureOutside." in message


@pytest.mark.base
def test_a_wire_naming_an_output_the_partner_does_not_have_is_refused_once_constructed(tmp_path: Path) -> None:
    """EF-7J: the wiring looks the wired output up on the constructed partner."""
    library = Library(tmp_path)
    library.add(
        "wired/sky",
        """
        schema_version: 4
        kind: assembly
        name: wired/sky
        components:
          PVSystem:
            class: tests.assemblies.mock_components.MockPVSystem
            preset: rooftop
            inputs: [{$port: weather, wires: {TemperatureOutside: Temperature}}]
        interface:
          needs:
            weather: {into: [PVSystem], partner: MockWeather}
        """,
        contract=True,
    )

    message = build_refusal(site(WEATHER) + "imports:\n  pv: {assembly: wired/sky}\n", tmp_path / "r", library)

    assert message.startswith(
        "EF-7J at import pv: port 'weather' is bound to Weather (MockWeather) by default and lowers to the wire "
        "'TemperatureOutside' from 'Temperature' in pv-PVSystem (MockPVSystem)"
    )
    assert "names the output 'Temperature', which 'Weather' (tests.assemblies.mock_components.MockWeather)" in message
    assert "Did you mean: TemperatureOutside?" in message


@pytest.mark.base
def test_any_other_wiring_refusal_of_a_lowered_item_keeps_its_id_and_names_the_port(tmp_path: Path) -> None:
    """EF-30 stays EF-30: the wiring's load-type check refuses a lowered wire and the message names the port."""
    library = Library(tmp_path)
    library.add(
        "wired/mixed",
        """
        schema_version: 4
        kind: assembly
        name: wired/mixed
        components:
          PVSystem:
            class: tests.assemblies.mock_components.MockPVSystem
            preset: rooftop
            inputs: [{$port: climate, wires: {TemperatureOutside: ElectricityConsumption}}]
        interface:
          needs:
            climate: {into: [PVSystem], partner: MockOccupancy}
        """,
        contract=True,
    )

    with pytest.raises(EnergySystemWiringError) as raised:
        build_text(site(OCCUPANCY) + "imports:\n  pv: {assembly: wired/mixed}\n", tmp_path / "r", library.resolver())

    message = str(raised.value)
    assert message.startswith(
        "EF-30 at import pv: port 'climate' is bound to Occupancy (MockOccupancy) by default and lowers to the wire "
        "'TemperatureOutside' from 'ElectricityConsumption' in pv-PVSystem (MockPVSystem)"
    )
    assert re.search(r"\(import pv, inline.energy_system.yaml:\d+ → wired/mixed.assembly.yaml:\d+\)", message)
    assert "the wiring refuses it: load type mismatch" in message


@pytest.mark.base
def test_a_provided_output_the_member_does_not_have_is_refused_once_constructed(tmp_path: Path) -> None:
    """EF-7J: nothing reads the provided output, and the wiring looks it up on the constructed member all the same."""
    library = Library(tmp_path)
    library.add(
        "broken/provides",
        """
        schema_version: 4
        kind: assembly
        name: broken/provides
        components:
          Heater:
            class: tests.assemblies.mock_components.MockHeater
            preset: standard
        interface:
          provides:
            heat: {output: Heater.NoSuchOutput}
        """,
        contract=True,
    )

    text = site(WEATHER) + "imports:\n  heater: {assembly: broken/provides}\n"
    message = build_refusal(text, tmp_path / "r", library)

    assert message.startswith(
        "EF-7J at import heater: port 'heat' provides the output 'NoSuchOutput' of heater-Heater (MockHeater) "
        "(import heater, inline.energy_system.yaml:"
    )
    assert "the wiring refuses it: the output 'NoSuchOutput' is declared on 'heater-Heater'" in message
    assert "Valid outputs: ElectricityInput, ThermalPower." in message


@pytest.mark.base
def test_the_port_provenance_table_lists_every_lowered_item_and_a_fitting_system_builds(tmp_path: Path) -> None:
    """The table holds each bare name, wire and provided output with its origin; the house's members all fit."""
    _expanded, record = expand_text(Mocks.HOUSE.read_text(encoding="utf-8"))

    weather = next(
        entry for entry in record.port_provenance if entry.member == "pv-east-PVSystem" and entry.port == "weather"
    )
    assert (weather.kind, weather.owner, weather.import_path, weather.port, weather.verb) == (
        LoweredKind.DEFAULT,
        "import pv[east]",
        "pv[east]",
        "weather",
        "default",
    )
    assert (weather.partner, weather.partner_class) == ("Weather", f"{Mocks.MOCKS}.MockWeather")
    assert weather.member_class == f"{Mocks.MOCKS}.MockPVSystem"
    assert weather.chain[-1].startswith("mock/pv_array.assembly.yaml:")
    assert {entry.kind for entry in record.port_provenance} >= {LoweredKind.DEFAULT, LoweredKind.PROVIDED}
    assert record.to_document()["port_provenance"][0] == record.port_provenance[0].to_document()

    built = build_text(Mocks.HOUSE.read_text(encoding="utf-8"), tmp_path)
    assert built.imports.port_provenance == record.port_provenance


CONTROL = """
schema_version: 4
kind: assembly
name: control/ems
components:
  EMS:
    class: tests.assemblies.mock_components.MockEms
    preset: standard
"""

#: A site thermostat with a port an import may bind.
SITE_CONTROLLER = f"""
Thermostat:
  class: {Mocks.MOCKS}.MockController
  preset: standard
  inputs:
    - {{$port: modifier}}
  optional-bind: {{modifier: control}}
  ports:
    modifier: {{partner: MockEms, optional: true}}
"""


@pytest.mark.base
def test_a_site_entry_binds_an_import_member_through_its_port(tmp_path: Path) -> None:
    """The verbs work alike on a site entry: ``optional-bind:`` binds when the import exists, else stays inert."""
    library = Library(tmp_path)
    library.add("control/ems", CONTROL, contract=True)

    expanded, record = expand_text(
        site(SITE_CONTROLLER) + "imports:\n  control: {assembly: control/ems}\n", library.resolver()
    )
    assert expanded.components["Thermostat"].inputs == (DefaultInputs(source="control-EMS"),)
    assert expanded.components["Thermostat"].ports == {} and expanded.components["Thermostat"].placeholders == ()
    assert [(name, port.state, port.partner) for name, port in record.site_ports] == [
        ("Thermostat", PortState.OPTIONAL, "control-EMS")
    ]
    entry = record.source_map.of("Thermostat", "inputs[0]")
    assert entry is not None and entry.note == "port modifier bound to control-EMS (optional-bind)"

    alone, alone_record = expand_text(
        site(SITE_CONTROLLER, WEATHER) + "imports:\n  pv: {assembly: mock/pv_array}\n", library.resolver()
    )
    assert alone.components["Thermostat"].inputs == ()
    assert alone_record.site_ports[0][1].partner.startswith("not bound: partner absent")


@pytest.mark.base
def test_a_site_port_needs_its_placeholder_and_a_placeholder_needs_its_port() -> None:
    """EF-7J: the contract check of a site entry."""
    without_placeholder = SITE_CONTROLLER.replace("  inputs:\n    - {$port: modifier}\n", "")
    message = refusal(site(without_placeholder, WEATHER) + "imports:\n  pv: {assembly: mock/pv_array}\n")
    assert message.startswith(
        "EF-7J at components.Thermostat.ports.modifier: the port 'modifier' of 'Thermostat' has no"
    )

    undeclared = SITE_CONTROLLER.replace("{$port: modifier}", "{$port: modifyer}")
    message = refusal(site(undeclared, WEATHER) + "imports:\n  pv: {assembly: mock/pv_array}\n")
    assert "EF-7J at components.Thermostat" in message


@pytest.mark.base
def test_a_site_entry_port_cannot_depend_on_parameters() -> None:
    """A site entry has no parameters, so ``required_when``/``active_when`` on its port are refused (EF-70)."""
    conditional = SITE_CONTROLLER.replace("optional: true}", "optional: true, active_when: {season: [winter]}}")
    text = site(conditional, WEATHER) + "imports:\n  pv: {assembly: mock/pv_array}\n"

    with pytest.raises(EnergySystemFormatError, match="EF-70 at inline.energy_system.yaml.components.Thermostat.ports."
                       "modifier.active_when: the port 'modifier' of the site entry 'Thermostat' writes 'active_when'"):
        expand_text(text)
    assert not jsonschema.Draft202012Validator(build_structural_schema()).is_valid(yaml.safe_load(text))


#: A tank fed by a heater imported beside it; the verb names one of the heater's provided outputs.
TANK_BOUND_TO = (
    site(WEATHER, OCCUPANCY)
    + "imports:\n"
    + "  tank: {{assembly: {tank}, bind: {{heat: backup.{output}}}}}\n"
    + "  backup: {{assembly: mock/electric_heater, parameters: {{with_thermostat: false}}}}\n"
)


@pytest.mark.base
def test_a_need_bound_to_a_provided_output_its_default_connections_do_not_read_is_refused(tmp_path: Path) -> None:
    """EF-7J: ``bind: {heat: backup.electricity}`` lowers to the bare name, read by default as ThermalPower."""
    built = build_text(TANK_BOUND_TO.format(tank="mock/hot_water_tank", output="heat"), tmp_path / "heat")
    assert built.model.components["tank-Tank"].inputs[-1] == DefaultInputs(source="backup-Heater")

    message = build_refusal(TANK_BOUND_TO.format(tank="mock/hot_water_tank", output="electricity"), tmp_path / "r")

    assert message.startswith(
        "EF-7J at import tank: port 'heat' is bound to backup-Heater (MockHeater) by bind and lowers to the bare name "
        "'backup-Heater' in tank-Tank (MockTank)"
    )
    assert re.search(r"\(import tank, inline.energy_system.yaml:\d+ → mock/hot_water_tank.assembly.yaml:\d+\)", message)
    assert "bound to the provided output 'ElectricityInput', but the default connections of MockTank from MockHeater " \
        "read ThermalPower, not 'ElectricityInput'." in message


@pytest.mark.base
def test_a_need_bound_to_a_provided_output_its_wires_do_not_read_is_refused(tmp_path: Path) -> None:
    """EF-7J from the files: the port's wires name the outputs read, and none of them is the bound one."""
    library = Library(tmp_path)
    library.add(
        "wired/tank",
        """
        schema_version: 4
        kind: assembly
        name: wired/tank
        components:
          Tank:
            class: tests.assemblies.mock_components.MockTank
            preset: standard
            inputs: [{$port: heat, wires: {ThermalPower: ThermalPower}}]
        interface:
          needs:
            heat: {into: [Tank], partner: MockHeater}
        """,
        contract=True,
    )

    expanded, _record = expand_text(TANK_BOUND_TO.format(tank="wired/tank", output="heat"), library.resolver())
    assert expanded.components["tank-Tank"].inputs == (
        ExplicitWire(source="backup-Heater", input="ThermalPower", output="ThermalPower"),
    )
    message = refusal(TANK_BOUND_TO.format(tank="wired/tank", output="electricity"), library)
    assert message.startswith(
        "EF-7J at import tank: port 'heat' is bound to the provided output 'ElectricityInput' of backup-Heater, but "
        "its wires into tank-Tank read ThermalPower"
    )


#: An assembly re-exporting the weather need of a two-instance inner import.
ARRAYS = """
    schema_version: 4
    kind: assembly
    name: multi/arrays
    imports:
      arrays:
        assembly: mock/pv_array
        instances: {a: {azimuth_in_degree: 90}, b: {azimuth_in_degree: 270}}
    interface:
      needs:
    {needs}
    """


@pytest.mark.base
def test_a_re_export_of_a_multi_instance_inner_import_names_the_instance(tmp_path: Path) -> None:
    """Each instance has its own port: ``from: arrays[a].weather``; ``from: arrays.weather`` is refused."""
    library = Library(tmp_path)
    library.add(
        "multi/arrays",
        ARRAYS.replace(
            "{needs}", "      weather_a: {from: 'arrays[a].weather'}\n          weather_b: {from: 'arrays[b].weather'}"
        ),
        contract=True,
    )
    library.add(
        "multi/ambiguous",
        ARRAYS.replace("multi/arrays", "multi/ambiguous").replace("{needs}", "      weather: {from: arrays.weather}"),
        contract=True,
    )

    expanded, record = expand_text(site(WEATHER) + "imports:\n  roof: {assembly: multi/arrays}\n", library.resolver())
    assert expanded.components["roof-arrays-a-PVSystem"].inputs == (DefaultInputs(source="Weather"),)
    assert expanded.components["roof-arrays-b-PVSystem"].inputs == (DefaultInputs(source="Weather"),)
    inner = record.instance("roof → arrays[a]")
    assert inner is not None and inner.port("weather").partner == "as roof.weather_a"  # type: ignore[union-attr]

    message = refusal(site(WEATHER) + "imports:\n  roof: {assembly: multi/ambiguous}\n", library)
    assert "the port 'weather' re-exports 'arrays.weather' of the inner import 'arrays', which has the instances " \
        "a, b, each with its own 'weather'; re-export each by name: from: arrays[a].weather" in message
