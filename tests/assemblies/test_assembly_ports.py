"""Binding the ports (§3.1, §3.3): the verbs, the default rule, and every expansion-time refusal."""

from __future__ import annotations

from typing import Tuple

import pytest

from hisim.energy_system.assemblies.binding import Owner, PortBinder
from hisim.energy_system.assemblies.record import ImportRecord
from hisim.energy_system.errors import EnergySystemAssemblyError
from hisim.energy_system.imports_model import BindingVerbs, Port, PortKind, PortState
from hisim.energy_system.model import DefaultInputs, ExplicitWire
from tests.assemblies.helpers import EMS, OCCUPANCY, WEATHER, Mocks, Real, expand_text, site

TANK = "tank: {assembly: mock/wired_tank"
HEATER = "heater: {assembly: mock/variant_heater"
MONITOR = f"""
Monitor:
  class: {Mocks.CLASSES}.MockBareDevice
  preset: standard
  inputs: [{{$port: pv}}]
  ports:
    pv: {{partner: PVSystem, wires: {{TemperatureOutside: ElectricityOutput}}}}
"""
PV_PAIR = "pv: {assembly: pv/array, instances: {east: {azimuth_in_degree: 90}, west: {azimuth_in_degree: 270}}"


def switched(verb: str = "optional-bind", enabled: bool = True) -> str:
    """A heater whose ``ems_modifier`` a verb names ``Ems``, which sits in the group ``control``."""
    imports = f"{TANK}}}\n{HEATER}, {verb}: {{ems_modifier: Ems}}}}"
    group = f"groups:\n  control:\n    enabled: {str(enabled).lower()}\n    components: {{{EMS}}}\n"
    return site(OCCUPANCY, imports=imports) + group


def refused(text: str, code: str, *names: str) -> str:
    """Expands a file that must be refused with ``code`` and a message naming every one of ``names``."""
    with pytest.raises(EnergySystemAssemblyError, match=code) as refusal:
        expand_text(text)
    message = str(refusal.value)
    for name in names:
        assert name in message, f"{name!r} is not named in: {message}"
    return message


def ports_of(text: str, import_key: str) -> dict:
    """The port records of one import (without instances) of an expanded file."""
    return {port.port: port for port in expand_text(text)[1].instance(import_key).ports}


@pytest.mark.assemblies
def test_a_required_port_without_a_partner_is_refused() -> None:
    """Catches a tank expanded with nobody drawing hot water from it."""
    refused(
        site(WEATHER, imports=f"{TANK}}}\n{HEATER}}}"), "EF-7A", "hot_water_demand", "MockOccupancy", "import 'tank'"
    )


@pytest.mark.assemblies
def test_a_required_port_with_several_candidates_and_no_verb_is_refused() -> None:
    """Catches the default rule picking one of two partners."""
    second = OCCUPANCY.replace("Occupancy:", "Guests:", 1)
    message = refused(site(OCCUPANCY, second, imports=f"{TANK}}}\n{HEATER}}}"), "EF-7B", "Occupancy", "Guests")
    assert "`bind: {hot_water_demand: Occupancy}`" in message


@pytest.mark.assemblies
def test_declining_a_required_port_is_refused() -> None:
    """Catches ``none:`` switching off a port the assembly cannot work without."""
    refused(site(OCCUPANCY, imports=f"{TANK}, none: [hot_water_demand]}}\n{HEATER}}}"), "EF-7C", "hot_water_demand")


@pytest.mark.assemblies
def test_a_bind_to_an_absent_partner_is_refused() -> None:
    """Catches ``bind:`` naming a component the file does not have."""
    refused(
        site(OCCUPANCY, imports=f"{TANK}, bind: {{hot_water_demand: Nobody}}}}\n{HEATER}}}"),
        "EF-7D",
        "Nobody",
        "Occupancy",
    )


@pytest.mark.assemblies
def test_a_bind_to_an_instance_an_import_does_not_have_is_refused_whatever_the_verb() -> None:
    """Catches a typo in an instance being taken for an absent partner by ``optional-bind:``."""
    monitor = MONITOR.replace("ports:", "optional-bind: {pv: pv.south}\n  ports:").replace(
        "ElectricityOutput}}", "ElectricityOutput}, optional: true}"
    )
    refused(
        site(monitor, Real.WEATHER, imports=PV_PAIR + "}"),
        "EF-7D",
        "pv.south",
        "east",
        "west",
    )


@pytest.mark.assemblies
def test_an_optional_bind_on_a_required_port_is_refused_with_the_bind_line() -> None:
    """Catches ``optional-bind:`` leaving a required port silently unbound when its partner is absent."""
    message = refused(
        site(OCCUPANCY, imports=f"{TANK}}}\n{HEATER}, optional-bind: {{tank_temperature: tank}}}}"),
        "EF-7C",
        "tank_temperature",
        "import 'heater'",
        "optional-bind: {tank_temperature: tank}",
    )
    assert "`bind: {tank_temperature: tank}`" in message


@pytest.mark.assemblies
def test_a_three_part_verb_target_on_an_import_without_instances_is_refused() -> None:
    """Catches ``import.port.extra`` binding to the port and silently dropping the rest."""
    refused(
        site(OCCUPANCY, imports=f"{TANK}, bind: {{heat: heater.heat.extra}}}}\n{HEATER}}}"),
        "EF-7D",
        "heater.heat.extra",
        "names more than an instance and a port",
    )


@pytest.mark.assemblies
def test_a_provided_output_whose_member_is_absent_is_refused_by_name() -> None:
    """Catches the binder raising a KeyError for a provided output whose member the selection left out."""
    need = Port(name="heat", section="needs", kind=PortKind.NEED, into=("Tank",), partner=("Gen",), wires={"P": "P"})
    provided = Port(name="power", section="provides", kind=PortKind.PROVIDED, output="Gen.P")
    states = {"power": PortState.PROVIDED}
    target = Owner("gen", "import 'gen'", "the import 'gen'", BindingVerbs(), {}, {"power": provided}, states)
    owner = Owner("tank", "import 'tank'", "the import 'tank'", BindingVerbs(), {}, {"heat": need}, {})
    binder = PortBinder({}, {"gen": [target], "tank": [owner]}, ImportRecord())
    with pytest.raises(EnergySystemAssemblyError, match="EF-7D") as refusal:
        binder.provided(owner, need, target, "power", "gen.power")
    for name in ("gen.power", "'Gen'", "import 'gen'"):
        assert name in str(refusal.value)


@pytest.mark.assemblies
def test_an_optional_port_with_a_candidate_and_no_verb_is_refused() -> None:
    """Catches adding an energy manager silently rewiring a heater that did not ask for it."""
    message = refused(site(OCCUPANCY, EMS, imports=f"{TANK}}}\n{HEATER}}}"), "EF-7E", "ems_modifier", "Ems")
    assert "`optional-bind: {ems_modifier: Ems}`" in message and "`none: [ems_modifier]`" in message


@pytest.mark.assemblies
def test_a_verb_on_an_inactive_port_is_refused() -> None:
    """Catches a verb for a port the parameters switch off."""
    text = site(
        OCCUPANCY,
        EMS,
        imports=f"{HEATER}, parameters: {{with_thermostat: false}}, optional-bind: {{ems_modifier: Ems}}}}",
    )
    refused(text, "EF-7F", "ems_modifier", "inactive")


@pytest.mark.assemblies
@pytest.mark.parametrize("port", ["nothing", "temperature"])
def test_a_verb_naming_no_need_is_refused(port: str) -> None:
    """Catches a verb naming a port the import does not have, or binding a provided port from its own side."""
    refused(
        site(OCCUPANCY, imports=f"{TANK}, bind: {{{port}: Occupancy}}}}\n{HEATER}}}"), "EF-7G", port, "hot_water_demand"
    )


@pytest.mark.assemblies
def test_a_need_bound_to_a_provided_output_its_wires_do_not_read_is_refused() -> None:
    """Catches a binding to an output that the lowered wires never read: a silent lie."""
    text = site(OCCUPANCY, imports=f"{TANK}, bind: {{heat: heater.electricity}}}}\n{HEATER}}}")
    refused(text, "EF-7H", "heater.electricity", "Heater.ElectricityInput", "ThermalPower")


@pytest.mark.assemblies
def test_a_need_without_wires_bound_to_a_provided_output_is_refused() -> None:
    """Catches a provided output named where only default connections are lowered, which name no output."""
    text = site(OCCUPANCY, imports=f"{TANK}}}\n{HEATER}, bind: {{tank_temperature: tank.temperature}}}}")
    refused(text, "EF-7H", "tank.temperature", "default connections")


@pytest.mark.assemblies
def test_a_bind_to_a_partner_of_another_class_is_refused() -> None:
    """Catches a need bound to a component whose class its default connections do not come from."""
    refused(
        site(WEATHER, OCCUPANCY, imports=f"{TANK}, bind: {{hot_water_demand: Weather}}}}\n{HEATER}}}"),
        "EF-7J",
        "MockWeather",
        "MockOccupancy",
    )


@pytest.mark.assemblies
def test_a_bind_to_an_import_with_several_matching_instances_is_refused() -> None:
    """Catches a bind to ``pv`` choosing one of its two arrays."""
    message = refused(
        site(MONITOR.replace("ports:", "bind: {pv: pv}\n  ports:"), Real.WEATHER, imports=PV_PAIR + "}"),
        "EF-7B",
        "pv-east-PVSystem",
        "pv-west-PVSystem",
    )
    assert "pv.<port>" in message


@pytest.mark.assemblies
@pytest.mark.parametrize(
    ("entry", "names"),
    [
        (
            f"Monitor: {{class: {Mocks.CLASSES}.MockBareDevice, preset: standard, inputs: [{{$port: pv}}]}}",
            ("pv", "Monitor"),
        ),
        (
            "Monitor: "
            f"{{class: {Mocks.CLASSES}.MockBareDevice, preset: standard, ports: {{pv: {{partner: PVSystem}}}}}}",
            ("{$port: pv}",),
        ),
    ],
)
def test_a_site_placeholder_and_its_port_belong_together(entry: str, names: Tuple[str, ...]) -> None:
    """Catches a site placeholder naming no port, and a site port with nowhere to land."""
    refused(site(entry, Real.WEATHER, imports=PV_PAIR + "}"), "EF-7J", *names)


@pytest.mark.assemblies
def test_a_site_port_binds_to_one_instance_and_lowers_to_its_wires() -> None:
    """Catches a bind to ``<import>.<instance>`` landing anywhere but its placeholder, or as a bare name."""
    flat, record = expand_text(
        site(MONITOR.replace("ports:", "bind: {pv: pv.west}\n  ports:"), Real.WEATHER, imports=PV_PAIR + "}")
    )
    assert flat.components["Monitor"].inputs == (
        ExplicitWire(source="pv-west-PVSystem", input="TemperatureOutside", output="ElectricityOutput"),
    )
    assert record.site_ports["Monitor"][0].verb == "bind"


@pytest.mark.assemblies
def test_the_optional_states_are_recorded_and_lower_nothing() -> None:
    """Catches an optional port lowered without a partner, or its decision not stated."""
    absent = ports_of(switched(enabled=False), "heater")
    assert absent["ems_modifier"].decision == "not bound: Ems disabled by group control"
    declined = ports_of(site(OCCUPANCY, EMS, imports=f"{TANK}}}\n{HEATER}, none: [ems_modifier]}}"), "heater")
    assert declined["ems_modifier"].decision == "declined" and not declined["ems_modifier"].lowered_to
    alone = ports_of(site(OCCUPANCY, imports=f"{TANK}}}\n{HEATER}}}"), "heater")
    assert alone["ems_modifier"].decision == "not bound: no candidate"
    flat, _record = expand_text(site(OCCUPANCY, imports=f"{TANK}}}\n{HEATER}}}"))
    assert flat.components["heater-Controller"].inputs == (DefaultInputs(source="tank-Tank"),)


@pytest.mark.assemblies
def test_a_port_never_binds_into_its_own_instance() -> None:
    """Catches the default rule binding an assembly to its own member."""
    text = site(WEATHER, OCCUPANCY, imports=f"{TANK}}}\n{HEATER}}}")
    flat, _record = expand_text(text)
    assert flat.components["tank-Tank"].inputs[1] == ExplicitWire(
        source="heater-Heater", input="ThermalPower", output="ThermalPower"
    )


@pytest.mark.assemblies
def test_an_optional_bind_to_a_live_declared_partner_binds_it() -> None:
    """Catches a partner in an enabled group being out of the binding's reach."""
    flat, record = expand_text(switched())
    port = {port.port: port for port in record.instance("heater").ports}["ems_modifier"]
    assert (port.decision, port.verb, port.partner) == ("bound", "optional-bind", "Ems")
    assert DefaultInputs(source="Ems") in flat.components["heater-Controller"].inputs


@pytest.mark.assemblies
def test_an_optional_bind_to_a_partner_an_unselected_option_holds_stays_unbound_with_the_reason() -> None:
    """Catches a switched-off partner failing the file, or its port's record not saying why it is unbound."""
    text = site(OCCUPANCY, imports=f"{TANK}}}\n{HEATER}, optional-bind: {{ems_modifier: Ems}}}}") + (
        "variants:\n  metering:\n    selected: bare\n    options:\n"
        f"      managed: {{components: {{{EMS}}}}}\n      bare: {{components: {{{WEATHER}}}}}\n"
    )
    flat, record = expand_text(text)
    port = {port.port: port for port in record.instance("heater").ports}["ems_modifier"]
    assert port.decision == "not bound: Ems disabled by variant metering (bare)"
    assert not port.lowered_to and flat.components["heater-Controller"].inputs == (DefaultInputs(source="tank-Tank"),)


@pytest.mark.assemblies
def test_a_bind_to_a_switched_off_partner_is_refused_with_the_reason() -> None:
    """Catches ``bind:`` accepting a partner a disabled group removes."""
    refused(switched("bind", enabled=False), "EF-7D", "Ems", "disabled by group control")


@pytest.mark.assemblies
@pytest.mark.parametrize("verb", ["optional-bind", "bind"])
def test_a_partner_the_file_does_not_declare_is_refused_whatever_the_verb(verb: str) -> None:
    """Catches a typo in a partner being taken for a house without that import."""
    text = site(OCCUPANCY, imports=f"{TANK}}}\n{HEATER}, {verb}: {{ems_modifier: control}}}}")
    message = refused(text, "EF-7D", "'control'", "declares no import or component")
    assert "Occupancy, heater, tank" in message
