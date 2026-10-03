"""Circuit ends, carrier needs and fact ports (``assemblies_spec.md`` §3.2, §3.3, §5, §6, §11.1; hisim-lt0b.2).

A circuit port is one end of one hydronic circuit: it binds the one other end of the same circuit in
scope and lowers to each end's default connections for the circuit's three outputs. A carrier need
binds the one provider of its carrier: for a fuel the provider's meter observes the consuming
outputs through the default feeds its class declares, for electricity nothing is wired and only the
one grid connection is checked. A fact need lowers to a ``sizing_sources`` line naming the provider.
Every refusal is a named error of the ``EF-7x`` band (``EF-4B`` for an ambiguous fact, the sizing
engine's own) naming the instance, the port and every candidate, with the source map of the import
and a paste-ready ``bind:`` line where one exists.
"""

import re
from pathlib import Path
from typing import Tuple

import pytest

from hisim.config import ComponentID
from hisim.config.base import AddressStep
from hisim.energy_system.assemblies.expansion import check_consumer_carriers, expand_imports
from hisim.energy_system.assemblies.library import CheckStrength, check_assembly
from hisim.energy_system.assemblies.record import CarrierConsumer, CarrierRecord, ImportRecord
from hisim.energy_system.errors import EnergySystemAssemblyError
from hisim.energy_system.loader import parse_energy_system
from hisim.energy_system.model import AggregatorFeed, DefaultInputs, SourceReference
from hisim.energy_system.source_lines import LineIndex
from hisim.postprocessing.kpi_computation.kpi_structure import KpiSource
from hisim.config import DisplayConfig
from hisim.simulationparameters import SimulationParameters
from tests.assemblies.fixture_components import FakeBoiler, FakeBoilerConfig
from tests.assemblies.helpers import OCCUPANCY, WEATHER, Fixtures, Library, expand_text, fixture_resolver, site

#: The boiler house's three imports of the circuit and the carrier.
GAS = "  gas: {assembly: supply/gas_connection}\n"
BOILER = "  boiler: {assembly: heating/gas_boiler}\n"
CYLINDER = "  cylinder: {assembly: dhw/cylinder}\n"

#: The site of most systems here.
SITE = site(WEATHER, OCCUPANCY)


def refusal(text: str, library: Library = None) -> str:  # type: ignore[assignment]
    """Expands a file that must be refused and returns the message."""
    with pytest.raises(EnergySystemAssemblyError) as raised:
        expand_text(text, library.resolver() if library is not None else None)
    return str(raised.value)


def expand_boiler_house():
    """The boiler house fixture, expanded with its line index."""
    path = Fixtures.SYSTEMS / "boiler_house.energy_system.yaml"
    return expand_imports(
        parse_energy_system(path),
        fixture_resolver(),
        lines=LineIndex.from_text(path.read_text(encoding="utf-8"), path.name),
    )


# ------------------------------------------------------------------------------------------ circuits


@pytest.mark.base
def test_a_circuit_lowers_to_each_ends_default_connections_from_the_other_end() -> None:
    """The cylinder reads the boiler's mass flow and supply leg, the boiler the cylinder's return leg."""
    expanded, record = expand_boiler_house()

    assert expanded.components["cylinder-Cylinder"].inputs == (
        DefaultInputs(source="Occupancy"),
        DefaultInputs(source="boiler-Boiler"),
    )
    assert expanded.components["boiler-Boiler"].inputs == (DefaultInputs(source="cylinder-Cylinder"),)
    (circuit,) = record.circuit("dhw")
    ends = {end.owner: (end.port, end.members, end.owns) for end in circuit.ends}
    assert ends == {
        "cylinder": ("circuit", ("cylinder-Cylinder",), ("ReturnTemperatureDhw",)),
        "boiler": ("dhw", ("boiler-Boiler",), ("MassFlowDhw", "SupplyTemperatureDhw")),
    }
    assert circuit.verb == "default"
    cylinder_port = record.instance("cylinder").port("circuit")  # type: ignore[union-attr]
    boiler_port = record.instance("boiler").port("dhw")  # type: ignore[union-attr]
    assert cylinder_port is not None and (cylinder_port.state, cylinder_port.partner) == ("required", "boiler.dhw")
    assert boiler_port is not None and (boiler_port.state, boiler_port.partner) == ("optional", "cylinder.circuit")
    entry = record.source_map.of("boiler-Boiler", "inputs[0]")
    assert entry is not None and entry.note == "circuit dhw: port dhw bound to cylinder.circuit (default)"
    document = record.to_document()
    assert document["circuits"][0]["ends"][1]["owns"] == ["MassFlowDhw", "SupplyTemperatureDhw"]


@pytest.mark.base
def test_a_required_circuit_end_without_the_other_end_is_refused() -> None:
    """EF-7A: names the circuit, no candidates, the source map."""
    message = refusal(SITE + "imports:\n" + CYLINDER)

    assert message.startswith(
        "EF-7A at import cylinder: required port 'circuit' (circuit dhw) has no other end of the circuit dhw"
    )
    assert "candidates: none" in message
    assert re.search(r"\(import cylinder, inline.energy_system.yaml:\d+ → dhw/cylinder.assembly.yaml:\d+\)", message)
    assert "MassFlowDhw, SupplyTemperatureDhw, ReturnTemperatureDhw" in message


@pytest.mark.base
def test_two_ends_for_one_circuit_need_a_verb_and_bind_decides() -> None:
    """EF-7B lists both ends with a paste-ready line each; ``bind:`` picks one."""
    boilers = "  boiler:\n    assembly: heating/gas_boiler\n    instances: {a: {}, b: {}}\n"
    message = refusal(SITE + "imports:\n" + GAS + boilers + CYLINDER)

    assert message.startswith(
        "EF-7B at import cylinder: port 'circuit' (circuit dhw) has 2 candidates boiler[a].dhw, boiler[b].dhw"
    )
    assert "`bind: {circuit: boiler.a.dhw}`, `bind: {circuit: boiler.b.dhw}`" in message

    expanded, record = expand_text(
        SITE + "imports:\n" + GAS + boilers + CYLINDER.replace("}", ", bind: {circuit: boiler.b}}")
    )
    assert expanded.components["cylinder-Cylinder"].inputs[1] == DefaultInputs(source="boiler-b-Boiler")
    assert record.instance("boiler[a]").port("dhw").partner == "not bound: no candidate"  # type: ignore[union-attr]


@pytest.mark.base
def test_binding_a_circuit_of_another_medium_is_refused(tmp_path: Path) -> None:
    """EF-7N: a solar end is no dhw end; the outputs of both circuits are named."""
    library = Library(tmp_path)
    library.add(
        "solar/collector",
        """
        schema_version: 4
        kind: assembly
        name: solar/collector
        components:
          Collector:
            class: tests.assemblies.fixture_components.FakeCollector
            preset: standard
            inputs: [{$port: loop}]
        interface:
          provides:
            loop: {circuit: solar, member: Collector}
        """,
    )

    message = refusal(
        SITE + "imports:\n  solar: {assembly: solar/collector}\n" + CYLINDER.replace("}", ", bind: {circuit: solar}}"),
        library,
    )

    assert message.startswith(
        "EF-7N at import cylinder: circuit port 'circuit' (circuit dhw) is bound to 'solar', whose circuit ends are "
        "of other circuits: loop (solar)"
    )
    message = refusal(
        SITE
        + "imports:\n  solar: {assembly: solar/collector}\n"
        + CYLINDER.replace("}", ", bind: {circuit: solar.loop}}"),
        library,
    )
    assert "an end of the circuit solar; a circuit binds only an end of its own medium" in message
    assert "MassFlowSolar, SupplyTemperatureSolar, ReturnTemperatureSolar are not" in message


@pytest.mark.base
def test_two_ends_that_both_own_an_output_are_refused_naming_it() -> None:
    """EF-7N: two boilers bound to each other both own the mass flow."""
    boilers = (
        "  first: {assembly: heating/gas_boiler, bind: {dhw: second.dhw}}\n"
        "  second: {assembly: heating/gas_boiler}\n"
    )
    message = refusal(SITE + "imports:\n" + GAS + boilers)

    assert message.startswith(
        "EF-7N at import first: the circuit dhw between first.dhw and second.dhw needs exactly one owner of the "
        "output MassFlowDhw, but first-Boiler and second-Boiler both declare it"
    )


@pytest.mark.base
def test_two_ends_of_which_neither_owns_an_output_are_refused_naming_it() -> None:
    """EF-7N: two cylinders own the return leg twice and the mass flow not at all."""
    cylinders = (
        "  first: {assembly: dhw/cylinder, bind: {circuit: second.circuit}}\n  second: {assembly: dhw/cylinder}\n"
    )
    message = refusal(SITE + "imports:\n" + cylinders)

    assert message.startswith(
        "EF-7N at import first: the circuit dhw between first.circuit and second.circuit needs exactly one owner of "
        "the output MassFlowDhw, but no member of either end declares it"
    )


#: A dhw end of one fake class, inline.
DHW_END = """
schema_version: 4
kind: assembly
name: odd/{name}
components:
  End:
    class: tests.assemblies.fixture_components.{cls}
    preset: standard
{inputs}interface:
  needs:
    circuit: {{circuit: dhw, member: End}}
"""


@pytest.mark.base
def test_an_output_no_member_of_the_other_end_reads_is_refused(tmp_path: Path) -> None:
    """EF-7N: the boiler owns the mass flow, the other end reads nothing."""
    library = Library(tmp_path)
    library.add("odd/return_only", DHW_END.format(name="return_only", cls="FakeDhwReturn", inputs=""))

    message = refusal(SITE + "imports:\n" + GAS + BOILER + "  end: {assembly: odd/return_only}\n", library)

    assert message.startswith(
        "EF-7N at import end: the circuit dhw: boiler-Boiler at boiler.dhw owns the output MassFlowDhw, but no member "
        "of end.circuit (end-End) reads it"
    )


@pytest.mark.base
def test_a_reader_without_default_connections_from_the_owner_is_refused(tmp_path: Path) -> None:
    """EF-7H: the sink reads the boiler's outputs but declares no default connections from FakeBoiler."""
    library = Library(tmp_path)
    library.add(
        "odd/sink",
        DHW_END.format(name="sink", cls="FakeDhwSink", inputs="    inputs: [{$port: circuit}]\n"),
    )

    message = refusal(SITE + "imports:\n" + GAS + BOILER + "  end: {assembly: odd/sink}\n", library)

    assert message.startswith(
        "EF-7H at import end: the circuit dhw: end-End (FakeDhwSink) reads MassFlowDhw from boiler-Boiler, but "
        "declares no default connections from FakeBoiler"
    )


@pytest.mark.base
def test_an_optional_circuit_end_with_a_candidate_and_no_verb_is_refused() -> None:
    """EF-7E (D8 i): two optional ends; the intent must be written."""
    cylinder = f"""
    Cylinder:
      class: {Fixtures.FAKES}.FakeCylinder
      preset: standard
      inputs:
        - Occupancy
        - {{$port: dhw}}
      ports:
        dhw: {{circuit: dhw, optional: true}}
    """
    message = refusal(site(WEATHER, OCCUPANCY, cylinder) + "imports:\n" + GAS + BOILER)

    assert message.startswith(
        "EF-7E at component Cylinder: optional port 'dhw' (circuit dhw) has 1 candidate boiler.dhw and no verb"
    )
    assert "`optional-bind: {dhw: boiler.dhw}`, `bind: {dhw: boiler.dhw}`, `none: [dhw]`" in message


@pytest.mark.base
def test_a_circuit_end_reading_without_a_placeholder_fails_the_library_check(tmp_path: Path) -> None:
    """The contract check: a member reading the circuit's outputs needs the port's placeholder."""
    library = Library(tmp_path)
    library.add(
        "broken/cylinder",
        (Fixtures.LIBRARY / "dhw" / "cylinder.assembly.yaml")
        .read_text(encoding="utf-8")
        .replace("name: dhw/cylinder", "name: broken/cylinder")
        .replace("      - {$port: circuit}\n", ""),
    )
    resolver = library.resolver()

    problems = check_assembly(resolver.resolve("broken/cylinder", "test"), resolver, CheckStrength.LIBRARY)
    assert any(
        "'Cylinder' reads MassFlowDhw, SupplyTemperatureDhw through the circuit port 'circuit' but carries no "
        "'{$port: circuit}' placeholder" in problem
        for problem in problems
    ), problems


@pytest.mark.base
def test_a_site_entry_is_a_circuit_end_of_its_own() -> None:
    """A site component's circuit port binds an import's end, alike both ways."""
    cylinder = f"""
    Cylinder:
      class: {Fixtures.FAKES}.FakeCylinder
      preset: standard
      inputs:
        - Occupancy
        - {{$port: dhw}}
      ports:
        dhw: {{circuit: dhw}}
    """
    expanded, record = expand_text(site(WEATHER, OCCUPANCY, cylinder) + "imports:\n" + GAS + BOILER)

    assert expanded.components["Cylinder"].inputs == (
        DefaultInputs(source="Occupancy"),
        DefaultInputs(source="boiler-Boiler"),
    )
    assert expanded.components["boiler-Boiler"].inputs == (DefaultInputs(source="Cylinder"),)
    assert [(name, port.partner) for name, port in record.site_ports] == [("Cylinder", "boiler.dhw")]


# ------------------------------------------------------------------------------------------ carriers


@pytest.mark.base
def test_a_fuel_need_lowers_to_the_meters_declared_feed() -> None:
    """The meter observes the boiler's fuel with the tags and the weight its class declares."""
    expanded, record = expand_boiler_house()

    assert expanded.components["gas-Meter"].inputs == (
        AggregatorFeed(source="boiler-Boiler", output="FuelUse", tags=("GAS_CONSUMPTION_UNCONTROLLED",), weight=999),
    )
    (provider,) = record.carrier("natural_gas")
    assert (provider.provider, provider.port, provider.meter) == ("gas", "connection", "gas-Meter")
    assert [(consumer.owner, consumer.port, consumer.outputs) for consumer in provider.consumers] == [
        ("boiler", "fuel", ("boiler-Boiler.FuelUse",))
    ]
    fuel = record.instance("boiler").port("fuel")  # type: ignore[union-attr]
    assert fuel is not None and fuel.partner == "gas.connection (meter gas-Meter)"
    assert record.instance("gas").port("connection").partner == "boiler.fuel"  # type: ignore[union-attr]


@pytest.mark.base
def test_a_fuel_need_without_a_provider_is_refused_naming_the_supply_assembly() -> None:
    """EF-7P: the format never adds a provider (§5.2); the message says which import adds it."""
    message = refusal(SITE + "imports:\n" + BOILER + CYLINDER)

    assert message.startswith(
        "EF-7P at import boiler: required carrier need 'fuel' has no provider of natural_gas in the file; "
        "candidates: none"
    )
    assert re.search(
        r"\(import boiler, inline.energy_system.yaml:\d+ → heating/gas_boiler.assembly.yaml:\d+\)", message
    )
    assert "add the import of `supply/gas_connection`" in message


@pytest.mark.base
def test_two_providers_of_a_fuel_need_a_verb_and_bind_decides() -> None:
    """EF-7P lists both providers with a paste-ready line each; ``bind:`` picks one."""
    two = GAS + GAS.replace("gas:", "spare:")
    message = refusal(SITE + "imports:\n" + two + BOILER + CYLINDER)

    assert message.startswith(
        "EF-7P at import boiler: port 'fuel' (carrier natural_gas) has 2 candidates gas.connection, spare.connection"
    )
    assert "`bind: {fuel: gas}`, `bind: {fuel: spare}`" in message

    with pytest.raises(EnergySystemAssemblyError, match="EF-7P .*the provider of natural_gas gas.connection has no "):
        expand_text(SITE + "imports:\n" + two + BOILER.replace("}", ", bind: {fuel: spare}}") + CYLINDER)


@pytest.mark.base
def test_an_idle_fuel_provider_is_refused() -> None:
    """EF-7P (§5.2): a connection no consumer is bound to."""
    message = refusal(SITE + "imports:\n" + GAS)

    assert message.startswith("EF-7P at import gas: the provider of natural_gas gas.connection has no bound consumer")


@pytest.mark.base
def test_site_entries_need_and_provide_carriers_alike() -> None:
    """A site boiler's need binds an imported connection; a site meter provides gas to an imported boiler."""
    boiler = f"""
    Boiler:
      class: {Fixtures.FAKES}.FakeBoiler
      preset: condensing
      ports:
        fuel: {{carrier: natural_gas, outputs: [FuelUse]}}
    """
    expanded, record = expand_text(site(WEATHER, boiler) + "imports:\n" + GAS)
    assert expanded.components["gas-Meter"].inputs == (
        AggregatorFeed(source="Boiler", output="FuelUse", tags=("GAS_CONSUMPTION_UNCONTROLLED",), weight=999),
    )
    assert record.site_ports[0][1].partner == "gas.connection (meter gas-Meter)"

    meter = f"""
    Meter:
      class: {Fixtures.FAKES}.FakeGasMeter
      preset: standard
      inputs: [{{$port: gas}}]
      ports:
        gas: {{carrier: natural_gas}}
    """
    expanded, record = expand_text(site(WEATHER, OCCUPANCY, meter) + "imports:\n" + BOILER + CYLINDER)
    assert expanded.components["Meter"].inputs == (
        AggregatorFeed(source="boiler-Boiler", output="FuelUse", tags=("GAS_CONSUMPTION_UNCONTROLLED",), weight=999),
    )
    (provider,) = record.carrier("natural_gas")
    assert (provider.provider, provider.port, provider.meter) == ("Meter", "gas", "Meter")


@pytest.mark.base
def test_both_ends_may_write_the_binding_but_must_agree() -> None:
    """Verbs on both ends naming each other bind once; naming a third end is refused (EF-7N)."""
    agreed = (
        "  heater: {assembly: heating/gas_boiler, bind: {dhw: cylinder}}\n"
        "  cylinder: {assembly: dhw/cylinder, bind: {circuit: heater.dhw}}\n"
    )
    expanded, _record = expand_text(SITE + "imports:\n" + GAS + agreed)
    assert expanded.components["heater-Boiler"].inputs == (DefaultInputs(source="cylinder-Cylinder"),)
    disagreeing = (
        "  cylinder: {assembly: dhw/cylinder, bind: {circuit: heater.dhw}}\n"
        "  heater: {assembly: heating/gas_boiler, bind: {dhw: spare}}\n"
        "  spare: {assembly: dhw/cylinder}\n"
    )
    message = refusal(SITE + "imports:\n" + GAS + disagreeing)
    assert message.startswith(
        "EF-7N at import heater: circuit port 'dhw' (circuit dhw) is bound to cylinder.circuit by that end's verb, "
        "yet the import 'heater' writes 'bind: spare' for it"
    )

    boilers = "  boiler:\n    assembly: heating/gas_boiler\n    instances: {a: {}, b: {}}\n"

    first = "  first: {assembly: dhw/cylinder, bind: {circuit: boiler.a.dhw}}\n"
    second = "  second: {assembly: dhw/cylinder, bind: {circuit: boiler.a.dhw}}\n"
    message = refusal(SITE + "imports:\n" + GAS + boilers + first + second)
    assert message.startswith(
        "EF-7N at import second: circuit port 'circuit' (circuit dhw) is bound to 'boiler.a.dhw', whose circuit "
        "already joins first.circuit; a circuit has exactly two ends"
    )


#: A supply of heating oil, metered by the gas meter class (only its carrier matters here).
OIL = """
schema_version: 4
kind: assembly
name: supply/oil
components:
  Meter:
    class: tests.assemblies.fixture_components.FakeGasMeter
    preset: standard
    inputs: [{$port: connection}]
interface:
  provides:
    connection: {carrier: heating_oil, meter: Meter}
"""


@pytest.mark.base
def test_binding_a_provider_of_another_carrier_is_refused(tmp_path: Path) -> None:
    """EF-7Q: an oil tank provides heating_oil, not natural_gas."""
    library = Library(tmp_path)
    library.add("supply/oil", OIL)

    message = refusal(
        SITE
        + "imports:\n  oil: {assembly: supply/oil}\n"
        + GAS
        + BOILER.replace("}", ", bind: {fuel: oil}}")
        + CYLINDER,
        library,
    )

    assert message.startswith(
        "EF-7Q at import boiler: carrier need 'fuel' (carrier natural_gas) is bound to 'oil', which provides "
        "heating_oil"
    )


@pytest.mark.base
def test_a_consuming_output_of_another_carrier_is_refused(tmp_path: Path) -> None:
    """EF-7Q at load time where the class states the carrier, and once built where it does not."""
    library = Library(tmp_path)
    library.add(
        "heating/oil_boiler",
        (Fixtures.LIBRARY / "heating" / "gas_boiler.assembly.yaml")
        .read_text(encoding="utf-8")
        .replace("name: heating/gas_boiler", "name: heating/oil_boiler")
        .replace("carrier: natural_gas", "carrier: heating_oil"),
    )
    resolver = library.resolver()
    problems = check_assembly(resolver.resolve("heating/oil_boiler", "test"), resolver, CheckStrength.EXPANSION)
    assert any(
        "the carrier need 'fuel' is of 'heating_oil', but 'Boiler.FuelUse' carries 'natural_gas'" in problem
        for problem in problems
    ), problems

    parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=900)
    boiler = FakeBoiler(parameters, FakeBoilerConfig.preset_condensing("Boiler"))
    record = ImportRecord(
        carriers=[
            CarrierRecord(
                carrier="heating_oil",
                provider="oil",
                port="connection",
                meter="oil-Meter",
                consumers=[CarrierConsumer("boiler", "fuel", ("Boiler.FuelUse",), "default", ())],
            )
        ]
    )
    with pytest.raises(EnergySystemAssemblyError, match="EF-7Q at components.Boiler: .*carries natural_gas"):
        check_consumer_carriers(record, [("Boiler", boiler)])


@pytest.mark.base
def test_an_output_the_meter_declares_no_feed_from_is_refused(tmp_path: Path) -> None:
    """EF-7H: the gas meter declares a default feed from FakeBoiler.FuelUse only."""
    library = Library(tmp_path)
    library.add(
        "heating/flue",
        (Fixtures.LIBRARY / "heating" / "gas_boiler.assembly.yaml")
        .read_text(encoding="utf-8")
        .replace("name: heating/gas_boiler", "name: heating/flue")
        .replace("outputs: [Boiler.FuelUse]", "outputs: [Boiler.FlueLoss]"),
    )

    message = refusal(SITE + "imports:\n" + GAS + "  boiler: {assembly: heating/flue}\n" + CYLINDER, library)

    assert message.startswith(
        "EF-7H at import boiler: carrier need 'fuel' (carrier natural_gas) would feed boiler-Boiler.FlueLoss into the "
        "meter gas-Meter (FakeGasMeter), which declares no default feed from FakeBoiler.FlueLoss"
    )
    assert "FakeBoiler.FuelUse" in message


#: An electricity consumer and a grid connection, inline.
HEATER = """
schema_version: 4
kind: assembly
name: consumer/heater
components:
  Heater:
    class: tests.assemblies.fixture_components.FakeHeater
    preset: standard
interface:
  needs:
    electricity: {carrier: electricity, outputs: [Heater.ElectricityInput]}
"""
GRID = """
schema_version: 4
kind: assembly
name: supply/grid
interface:
  provides:
    connection: {carrier: electricity}
"""


def electricity_library(tmp_path: Path) -> Library:
    """A library with the heater and the grid."""
    library = Library(tmp_path)
    library.add("consumer/heater", HEATER)
    library.add("supply/grid", GRID)
    return library


@pytest.mark.base
def test_an_electricity_need_checks_the_one_grid_connection_and_writes_no_wire(tmp_path: Path) -> None:
    """No feed, no verb; the record says the provider and that there is no link."""
    expanded, record = expand_text(
        site(WEATHER) + "imports:\n  grid: {assembly: supply/grid}\n  heater: {assembly: consumer/heater}\n",
        electricity_library(tmp_path).resolver(),
    )

    assert expanded.components["heater-Heater"].inputs == ()
    (provider,) = record.carrier("electricity")
    assert provider.meter is None and provider.consumers[0].lowered_to == ()
    port = record.instance("heater").port("electricity")  # type: ignore[union-attr]
    assert port is not None and port.partner == "grid.connection (no link)"


@pytest.mark.base
def test_the_electricity_refusals(tmp_path: Path) -> None:
    """EF-7P: no grid connection, two of them, and a verb on an electricity need."""
    library = electricity_library(tmp_path)
    heater = "  heater: {assembly: consumer/heater}\n"

    message = refusal(site(WEATHER) + "imports:\n" + heater, library)
    assert message.startswith(
        "EF-7P at import heater: required carrier need 'electricity' has no provider of electricity in the file"
    )
    assert "Add the import of `supply/electricity_grid`" in message

    two = "  grid: {assembly: supply/grid}\n  second: {assembly: supply/grid}\n"
    message = refusal(site(WEATHER) + "imports:\n" + two + heater, library)
    assert "finds 2 electricity providers: grid.connection, second.connection; a system has exactly one grid" in message

    message = refusal(
        site(WEATHER) + "imports:\n  grid: {assembly: supply/grid}\n  heater: {assembly: consumer/heater, "
        "bind: {electricity: grid}}\n",
        library,
    )
    assert message.startswith(
        "EF-7P at import heater: carrier need 'electricity' (carrier electricity) carries the "
        "verb 'bind', but electricity has no link end"
    )


@pytest.mark.base
def test_an_electricity_provision_names_no_meter_and_a_fuel_provision_names_one(tmp_path: Path) -> None:
    """The contract check of a carrier provision."""
    library = Library(tmp_path)
    library.add(
        "supply/metered_grid", OIL.replace("heating_oil", "electricity").replace("supply/oil", "supply/metered_grid")
    )
    library.add(
        "supply/unmetered_gas",
        GRID.replace("electricity", "natural_gas").replace("supply/grid", "supply/unmetered_gas"),
    )
    resolver = library.resolver()

    metered = check_assembly(resolver.resolve("supply/metered_grid", "test"), resolver, CheckStrength.EXPANSION)
    unmetered = check_assembly(resolver.resolve("supply/unmetered_gas", "test"), resolver, CheckStrength.EXPANSION)

    assert any("an electricity provision names no 'meter'" in problem for problem in metered), metered
    assert any("the provision of 'natural_gas' names no 'meter'" in problem for problem in unmetered), unmetered


# --------------------------------------------------------------------------------------------- facts


@pytest.mark.base
def test_a_fact_port_lowers_to_a_sizing_line_naming_the_provider() -> None:
    """The battery reads the east array's peak power; the source map says which port wrote the line."""
    expanded, record = expand_boiler_house()

    assert expanded.components["battery-Battery"].sizing_sources == {
        "pv_peak_power_in_watt": SourceReference(component="pv-east-PVSystem", fact="pv_peak_power_in_watt")
    }
    port = record.instance("battery").port("pv_power")  # type: ignore[union-attr]
    assert port is not None and (port.partner, port.verb) == ("pv-east-PVSystem", "bind")
    assert port.lowered_to == (
        "battery-Battery.sizing_sources.pv_peak_power_in_watt: pv-east-PVSystem.pv_peak_power_in_watt",
    )
    entry = record.source_map.of("battery-Battery", "sizing_sources.pv_peak_power_in_watt")
    assert entry is not None and entry.note == "port pv_power bound to pv-east-PVSystem (bind)"


@pytest.mark.base
def test_a_scalar_fact_with_two_providers_is_the_sizing_engines_ambiguity() -> None:
    """EF-4B, the engine's ambiguity, at load time: both arrays listed, a paste-ready line each."""
    pv = "  pv:\n    assembly: pv/array\n    instances: {east: {}, west: {}}\n"
    message = refusal(SITE + "imports:\n" + pv + "  battery: {assembly: storage/battery}\n")

    assert message.startswith(
        "EF-4B at import battery: port 'pv_power' (fact pv_peak_power_in_watt) has 2 candidates pv-east-PVSystem, "
        "pv-west-PVSystem and no verb"
    )
    assert "`bind: {pv_power: pv.east.peak_power}`, `bind: {pv_power: pv.west.peak_power}`" in message
    assert re.search(r"\(import battery, inline.energy_system.yaml:\d+ → storage/battery.assembly.yaml:\d+\)", message)


@pytest.mark.base
def test_a_fact_without_a_provider_and_a_bind_to_a_non_provider_are_refused() -> None:
    """EF-7R both: nobody contributes the fact, and the bound site entry does not."""
    battery = "  battery: {assembly: storage/battery}\n"
    message = refusal(SITE + "imports:\n" + battery)
    assert message.startswith(
        "EF-7R at import battery: required fact port 'pv_power' finds no provider of the fact pv_peak_power_in_watt"
    )

    message = refusal(SITE + "imports:\n" + battery.replace("}", ", bind: {pv_power: Weather}}"))
    assert message.startswith(
        "EF-7R at import battery: fact port 'pv_power' (fact pv_peak_power_in_watt) is bound to 'Weather' "
        "(FakeWeatherConfig), which does not contribute pv_peak_power_in_watt"
    )


@pytest.mark.base
def test_a_site_component_providing_the_fact_is_bound_by_the_default_rule() -> None:
    """A site array is the one provider in scope; the line names it by its own name."""
    roof = f"""
    Roof:
      class: {Fixtures.FAKES}.FakePVSystem
      preset: rooftop
      inputs: [Weather]
    """
    expanded, _record = expand_text(
        site(WEATHER, OCCUPANCY, roof) + "imports:\n  battery: {assembly: storage/battery}\n"
    )

    assert expanded.components["battery-Battery"].sizing_sources == {
        "pv_peak_power_in_watt": SourceReference(component="Roof", fact="pv_peak_power_in_watt")
    }


@pytest.mark.base
def test_a_provided_fact_outside_the_classes_contributions_fails_the_contract(tmp_path: Path) -> None:
    """A fact port provides only what its member's class declares in SIZING_CONTRIBUTIONS."""
    library = Library(tmp_path)
    library.add(
        "broken/pv",
        (Fixtures.LIBRARY / "pv" / "array.assembly.yaml")
        .read_text(encoding="utf-8")
        .replace("name: pv/array", "name: broken/pv")
        .replace("{fact: pv_peak_power_in_watt, member: PVSystem}", "{fact: heating_load_in_watt, member: PVSystem}"),
    )
    resolver = library.resolver()

    problems = check_assembly(resolver.resolve("broken/pv", "test"), resolver, CheckStrength.EXPANSION)
    assert any(
        "the provided fact 'heating_load_in_watt' is not among the SIZING_CONTRIBUTIONS of FakePVSystemConfig"
        in problem
        for problem in problems
    ), problems


# ------------------------------------------------------------------------------------------- $switch


@pytest.mark.base
def test_a_switch_takes_the_case_its_parameter_or_variant_selects(tmp_path: Path) -> None:
    """``{$switch: …}`` by a parameter and by an internal variant, resolved at expansion."""
    _expanded, _record = expand_text(SITE + "imports:\n" + GAS + BOILER + CYLINDER)
    standard, _ = expand_text(
        SITE + "imports:\n" + GAS + BOILER.replace("}", ", parameters: {boiler_type: standard}}") + CYLINDER
    )
    condensing, _ = expand_text(SITE + "imports:\n" + GAS + BOILER + CYLINDER)
    assert standard.components["boiler-Boiler"].config["efficiency"] == 0.85
    assert condensing.components["boiler-Boiler"].config["efficiency"] == 0.95

    library = Library(tmp_path)
    library.add(
        "switch/heater",
        (Fixtures.LIBRARY / "generator" / "electric_heater.assembly.yaml")
        .read_text(encoding="utf-8")
        .replace("name: generator/electric_heater", "name: switch/heater")
        .replace(
            "      power_in_watt: {$param: power_in_watt}\n",
            "      power_in_watt: {$switch: thermostat, fitted: {$param: power_in_watt}, none: 1500}\n",
        ),
    )
    plain, _ = expand_text(
        site(WEATHER) + "imports:\n  heater: {assembly: switch/heater, parameters: {with_thermostat: false}}\n",
        library.resolver(),
    )
    assert plain.components["heater-Heater"].config["power_in_watt"] == 1500


@pytest.mark.base
def test_a_switch_must_name_one_selector_and_cover_its_values(tmp_path: Path) -> None:
    """The library check: an unknown selector, a missing case, an unknown case."""
    library = Library(tmp_path)
    base = (Fixtures.LIBRARY / "heating" / "gas_boiler.assembly.yaml").read_text(encoding="utf-8")
    cases = {
        "broken/selector": "{$switch: kind, condensing: 0.95, standard: 0.85}",
        "broken/cases": "{$switch: boiler_type, condensing: 0.95, old: 0.8}",
    }
    for name, switch in cases.items():
        library.add(
            name,
            base.replace("name: heating/gas_boiler", f"name: {name}").replace(
                "{$switch: boiler_type, condensing: 0.95, standard: 0.85}", switch
            ),
        )
    resolver = library.resolver()

    selector = check_assembly(resolver.resolve("broken/selector", "test"), resolver, CheckStrength.EXPANSION)
    cases_found = check_assembly(resolver.resolve("broken/cases", "test"), resolver, CheckStrength.EXPANSION)

    assert any("the switch on 'kind' names neither a parameter nor an internal variant" in p for p in selector)
    assert any("missing 'standard'; unknown 'old'" in problem for problem in cases_found), cases_found


# ------------------------------------------------------------------------------------- display names


@pytest.mark.base
def test_a_members_display_name_reaches_its_identity_and_its_kpi_source() -> None:
    """The rendered template rides on the member's ComponentID; site components keep their DisplayConfig."""
    expanded, _record = expand_boiler_house()

    identity = expanded.addresses["boiler-Boiler"]
    assert identity.display_name == "Gas boiler, 4000 W"
    assert KpiSource.for_component(identity, DisplayConfig("ignored")).display_name == "Gas boiler, 4000 W"
    assert expanded.addresses["cylinder-Cylinder"].display_name is None
    plain = ComponentID("Building")
    assert KpiSource.for_component(plain, DisplayConfig("Main building")).display_name == "Main building"
    assert KpiSource.for_component(plain, DisplayConfig()).display_name == "Building"
    member: Tuple[AddressStep, ...] = (AddressStep("pv", "east"),)
    assert ComponentID("PVSystem", path=member, display_name="x") == ComponentID("PVSystem", path=member)
