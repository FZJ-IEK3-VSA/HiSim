"""Binding ports and the load-time refusals of ``assemblies_spec.md`` §3.1–§3.3.

Every refusal is a named error of the ``EF-7x`` band whose message names the import (and instance),
the port, its partner classes and every candidate, carries the source map of the import, and ends
in a paste-ready ``bind:`` line where one exists. One test per refusal, each asserting exactly
those parts.
"""

import re
from pathlib import Path

import pytest

from hisim.energy_system.errors import EnergySystemAssemblyError
from hisim.energy_system.model import DefaultInputs, ExplicitWire
from tests.assemblies.helpers import EMS, OCCUPANCY, WEATHER, Library, Mocks, expand_text, site

#: A second weather station, for an ambiguous partner.
WEATHER_2 = WEATHER.replace("Weather:", "Weather2:")

#: An import of one PV instance, the subject of most refusals here.
PV_EAST = "imports:\n  pv:\n    assembly: mock/pv_array\n    instances: {east: {}}\n"


def refusal(text: str, library: Library = None) -> str:  # type: ignore[assignment]
    """Expands a file that must be refused and returns the message."""
    with pytest.raises(EnergySystemAssemblyError) as raised:
        expand_text(text, library.resolver() if library is not None else None)
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
    assert "Candidates: Weather; add to the import 'pv' one of `bind: {weather: Weather}`" in message


@pytest.mark.base
def test_binding_a_provider_the_member_declares_no_default_connections_from_is_refused() -> None:
    """EF-7H: the tank declares no default connections from a PV system, whichever port names it."""
    message = refusal(
        site(WEATHER, OCCUPANCY)
        + "imports:\n  pv: {assembly: mock/pv_array}\n"
        + "  tank: {assembly: mock/hot_water_tank, bind: {heat: pv.production}}\n"
    )

    assert message.startswith("EF-7H at import tank: port 'heat' is bound to pv-PVSystem (MockPVSystem)")
    assert "tank-Tank (MockTank) declares no default connections from MockPVSystem" in message
    assert "Valid classes it declares default connections from: MockHeater, MockOccupancy." in message
    assert "Candidates: none; add to the import 'tank' one of `bind: {heat: <partner>}`" in message


@pytest.mark.base
def test_a_member_class_without_an_interface_cannot_be_bound(tmp_path: Path) -> None:
    """EF-7H: a class that makes no class-level statement cannot be checked, so it is not bound."""
    library = Library(tmp_path)
    library.add(
        "broken/undeclared",
        """
        schema_version: 4
        kind: assembly
        name: broken/undeclared
        components:
          Device:
            class: tests.assemblies.mock_components.UndeclaredDevice
            preset: standard
            inputs: [{$port: weather}]
        interface:
          needs:
            weather: {into: [Device], partner: MockWeather}
        """,
    )

    message = refusal(site(WEATHER) + "imports:\n  device: {assembly: broken/undeclared}\n", library)

    assert message.startswith(
        "EF-7H at import device: port 'weather' would lower to device-Device's default connections"
    )
    assert "declares no CLASS_INTERFACE" in message
    assert "Candidates: Weather; add to the import 'device' one of `bind: {weather: Weather}`" in message


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
    assert port.state == "optional" and port.verb == "optional-bind"
    assert port.partner == "not bound: partner absent ('control' names no component and no import of the file)"


@pytest.mark.base
def test_explicit_wires_lower_to_wires_and_their_inputs_are_checked(tmp_path: Path) -> None:
    """A port's ``wires:`` lowers to explicit wires; a wire naming no input is a contract error."""
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
    library.add("wired/pv", wired.format(target="TemperatureOutside"))
    library.add("wired/broken", wired.format(target="Irradiance").replace("wired/pv", "wired/broken"))

    expanded, _record = expand_text(site(WEATHER) + "imports:\n  pv: {assembly: wired/pv}\n", library.resolver())
    assert expanded.components["pv-PVSystem"].inputs == (
        ExplicitWire(source="Weather", input="TemperatureOutside", output="TemperatureOutside"),
    )

    message = refusal(site(WEATHER) + "imports:\n  pv: {assembly: wired/broken}\n", library)
    assert message.startswith("EF-7J at import pv: port 'weather' wires 'Irradiance', which is no input of pv-PVSystem")


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
    library.add("control/ems", CONTROL)

    expanded, record = expand_text(
        site(SITE_CONTROLLER) + "imports:\n  control: {assembly: control/ems}\n", library.resolver()
    )
    assert expanded.components["Thermostat"].inputs == (DefaultInputs(source="control-EMS"),)
    assert expanded.components["Thermostat"].ports == {} and expanded.components["Thermostat"].placeholders == ()
    assert [(name, port.state, port.partner) for name, port in record.site_ports] == [
        ("Thermostat", "optional", "control-EMS")
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
