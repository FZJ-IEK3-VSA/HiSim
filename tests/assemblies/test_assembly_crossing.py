"""Circuit ends, carrier needs and fact ports (``assemblies_spec.md`` §3.2, §3.3, §5, §6, §11.1, lean v1 §13.1).

A circuit end binds the one other end of its circuit in scope and lowers, at every member carrying
its placeholder, to a bare name of each member of the other end. A carrier need needs the one
provider of its carrier: for a fuel the provider's meter takes a bare name of the consumer, which
its dynamic default connections expand, and the wiring checks each consuming output's carrier and
that the meter feeds exactly the named outputs; electricity writes no wire. A fact need lowers to a
``sizing_sources`` line naming its provider, ``many: true`` to the list of every provider. Every
refusal is checked by its code and the names in it.

The circuits, carriers and the many fact run on the real library: ``heating/gas_condensing_boiler``,
``dhw/indirect_cylinder``, ``supply/gas_connection``, ``pv/array``, ``storage/battery`` and the gas
composed file. Inline assemblies of mock classes show what the real library cannot: a scalar fact
need, a member at a circuit end without default connections, a burner with an output its need leaves
out, and the meter classes whose declarations a carrier need contradicts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar, Dict, Iterator, Tuple

import pytest
import yaml

from hisim.cli import main
from hisim.cli_exit import ExitCodes
from hisim.energy_system.assemblies.binding import Owner, PortBinder
from hisim.energy_system.assemblies.record import ImportRecord
from hisim.energy_system.errors import (
    EnergySystemAssemblyError,
    EnergySystemCatalogueError,
    EnergySystemError,
    EnergySystemWiringError,
)
from hisim.energy_system.executor import run_energy_system
from hisim.energy_system.imports_model import BindingVerbs, Port, PortKind, PortState, Selection
from hisim.energy_system.model import AggregatorFeed, DefaultInputs, EnergySystemFile, SourceReference
from hisim.energy_system.wiring_checks import ConsumingOutput
from hisim.postprocessing.kpi_computation.kpi_address import KpiFinder
from tests.assemblies.helpers import (
    EMPTY_CONTRACT,
    MOCKS,
    OCCUPANCY,
    WEATHER,
    Library,
    Real,
    build_text,
    expand_text,
    imports,
    site,
)
from tests.assemblies.mock_components import MEASURED


class Gas:
    """The real imports of a gas-heated house, and the inline mock assemblies of the refusals the real ones cannot show.

    Example: ``Real.heating_site() + imports(Gas.CONNECTION, Gas.BOILER, Gas.CYLINDER)`` is the gas house's
    heating as the composed file imports it, without the electricity side.
    """

    CONNECTION: ClassVar[str] = "gas: {assembly: supply/gas_connection}"
    BOILER: ClassVar[str] = "boiler: {assembly: heating/gas_condensing_boiler}"
    CYLINDER: ClassVar[str] = "cylinder: {assembly: dhw/indirect_cylinder}"

    #: Inline mock assemblies by library path: a cylinder at the dhw circuit's end and a natural-gas supply.
    MOCK_ASSEMBLIES: ClassVar[Dict[str, str]] = {
        "test/cylinder": f"""\
            schema_version: 4
            kind: assembly
            name: test/cylinder
            components:
              Cylinder: {{class: {MOCKS}.MockCylinder, preset: standard, inputs: [{{$port: demand}}, {{$port: dhw}}]}}
            interface:
              needs:
                demand: {{into: [Cylinder], partner: MockOccupancy}}
                dhw: {{circuit: dhw, member: Cylinder}}
            {EMPTY_CONTRACT}""",
        "test/gas": f"""\
            schema_version: 4
            kind: assembly
            name: test/gas
            components:
              Meter: {{class: {MOCKS}.MockGasMeter, preset: standard, inputs: [{{$port: connection}}]}}
            interface:
              provides:
                connection: {{carrier: natural_gas, meter: Meter}}
            {EMPTY_CONTRACT}""",
    }

    @classmethod
    def mock_library(cls, directory: Path) -> Library:
        """A temporary library holding the inline mock cylinder and gas supply."""
        library = Library(directory)
        for path, text in cls.MOCK_ASSEMBLIES.items():
            library.add(path, text)
        return library


def refusal(text: str, library: Library = None) -> str:  # type: ignore[assignment]
    """The message an expansion refuses a file with."""
    with pytest.raises(EnergySystemAssemblyError) as raised:
        expand_text(text, library.resolver() if library is not None else None)
    return str(raised.value)


def build_refusal(text: str, directory: Path, library: Library = None) -> str:  # type: ignore[assignment]
    """The message a build refuses a file with whose expansion succeeds."""
    resolver = library.resolver() if library is not None else None
    expand_text(text, resolver)
    with pytest.raises(EnergySystemCatalogueError) as raised:
        build_text(text, directory, resolver)
    return str(raised.value)


def gas_heating() -> Tuple[EnergySystemFile, ImportRecord]:
    """The real boiler, cylinder and gas connection on the heating site, expanded."""
    return expand_text(Real.heating_site() + imports(Gas.CONNECTION, Gas.BOILER, Gas.CYLINDER))


def ports(record: ImportRecord, key: str) -> dict:
    """The port records of one import without instances, by port name."""
    return {item.port: item for item in record.instance(key).ports}


# ------------------------------------------------------------------------------------------ circuits


@pytest.mark.assemblies
def test_a_circuit_lowers_to_a_bare_name_of_the_other_end_at_each_end_and_records_both_ends() -> None:
    """Catches a circuit wired one way only, or recorded at one end only."""
    model, record = gas_heating()

    assert model.components["boiler-Boiler"].inputs[1] == DefaultInputs(source="cylinder-DHWStorage")
    assert model.components["cylinder-DHWStorage"].inputs == (
        DefaultInputs(source="UTSPConnector"),
        DefaultInputs(source="boiler-Boiler"),
    )
    boiler, cylinder = ports(record, "boiler")["dhw"], ports(record, "cylinder")["circuit"]
    assert (boiler.decision, boiler.verb, boiler.partner) == ("bound", "default", "cylinder.circuit")
    assert (cylinder.decision, cylinder.partner) == ("bound", "boiler.dhw")
    assert cylinder.verb == "joined by boiler.dhw"
    assert (
        boiler.lowered_to
        == cylinder.lowered_to
        == (
            "boiler-Boiler.inputs: cylinder-DHWStorage",
            "cylinder-DHWStorage.inputs: boiler-Boiler",
        )
    )


@pytest.mark.assemblies
def test_a_required_circuit_end_without_the_other_end_is_refused() -> None:
    """Catches a cylinder left without its boiler."""
    message = refusal(site(Real.WEATHER, Real.OCCUPANCY) + imports(Gas.CYLINDER))
    assert message.startswith("EF-7A at import 'cylinder'")
    assert "an end of the circuit dhw" in message


@pytest.mark.assemblies
def test_two_other_ends_need_a_verb_and_bind_decides() -> None:
    """Catches a circuit end silently picking one of two boilers.

    Each boiler is also an end of a space_heating circuit, so each has a heat distribution of its own.
    """
    distributions = (
        Real.HEAT_DISTRIBUTION.replace("ports:", "bind: {space_heating: one}, ports:"),
        Real.HEAT_DISTRIBUTION.replace("HeatDistributionSystem:", "Second:").replace(
            "ports:", "bind: {space_heating: two}, ports:"
        ),
    )
    house = site(Real.WEATHER, Real.OCCUPANCY, Real.HEAT_DISTRIBUTION_CONTROLLER, *distributions)
    boilers = ("one: {assembly: heating/gas_condensing_boiler}", "two: {assembly: heating/gas_condensing_boiler}")
    message = refusal(house + imports(Gas.CONNECTION, Gas.CYLINDER, *boilers))
    assert message.startswith("EF-7B at import 'cylinder'") and "one.dhw, two.dhw" in message
    assert "`bind: {circuit: one.dhw}`" in message

    model, _record = expand_text(
        house
        + imports(
            Gas.CONNECTION,
            "cylinder: {assembly: dhw/indirect_cylinder, bind: {circuit: two}}",
            "tank: {assembly: dhw/indirect_cylinder, bind: {circuit: one.dhw}}",
            "one: {assembly: heating/gas_condensing_boiler, bind: {dhw_temperature: tank}}",
            "two: {assembly: heating/gas_condensing_boiler, bind: {dhw_temperature: cylinder}}",
        )
    )
    assert model.components["cylinder-DHWStorage"].inputs[1] == DefaultInputs(source="two-Boiler")
    assert model.components["one-Boiler"].inputs[1] == DefaultInputs(source="tank-DHWStorage")


@pytest.mark.assemblies
def test_a_joined_circuit_end_bound_to_an_undeclared_name_is_refused_by_name() -> None:
    """Catches a joined end whose verb names a partner the file does not declare escaping as a KeyError."""
    message = refusal(
        Real.heating_site()
        + imports(Gas.BOILER, Gas.CONNECTION, "cylinder: {assembly: dhw/indirect_cylinder, bind: {circuit: wrong}}")
    )
    assert message.startswith("EF-7D at import 'cylinder'")
    assert "the circuit end 'circuit' is bound to 'wrong' with 'bind:'" in message
    assert "declares no import or component 'wrong'" in message


@pytest.mark.assemblies
def test_binding_an_end_of_another_circuit_is_refused(tmp_path: Path) -> None:
    """Catches a dhw cylinder bound to a space_heating end: the circuit name is the medium."""
    library = Library(tmp_path)
    library.add(
        "test/floor",
        f"""\
        schema_version: 4
        kind: assembly
        name: test/floor
        components:
          Floor: {{class: {MOCKS}.MockCylinder, preset: standard, inputs: [{{$port: space_heating}}]}}
        interface:
          needs:
            space_heating: {{circuit: space_heating, member: Floor}}
        {EMPTY_CONTRACT}""",
    )
    text = Real.heating_site() + imports(
        Gas.BOILER,
        Gas.CONNECTION,
        "cylinder: {assembly: dhw/indirect_cylinder, bind: {circuit: floor}}",
        "floor: {assembly: test/floor, none: [space_heating]}",
    )
    message = refusal(text, library)
    assert message.startswith("EF-7K at import 'cylinder'")
    assert "the circuit space_heating ('space_heating')" in message


@pytest.mark.assemblies
def test_a_member_at_an_end_that_reads_nothing_of_the_other_is_refused_by_the_wiring(tmp_path: Path) -> None:
    """D25: a member that neither owns nor reads a circuit output is a bare name without default connections.

    Inline mocks: no real class lacks the default connections a circuit end lowers to.
    """
    library = Library(tmp_path)
    library.add(
        "test/boiler",
        f"""\
        schema_version: 4
        kind: assembly
        name: test/boiler
        components:
          Boiler: {{class: {MOCKS}.MockBoiler, preset: condensing, inputs: [{{$port: dhw}}]}}
        interface:
          provides:
            dhw: {{circuit: dhw, member: Boiler}}
        {EMPTY_CONTRACT}""",
    )
    library.add(
        "test/bare_end",
        f"""\
        schema_version: 4
        kind: assembly
        name: test/bare_end
        components:
          Device: {{class: {MOCKS}.MockBareDevice, preset: standard, inputs: [{{$port: dhw}}]}}
        interface:
          needs:
            dhw: {{circuit: dhw, member: Device}}
        {EMPTY_CONTRACT}""",
    )
    text = site(WEATHER, OCCUPANCY) + imports("boiler: {assembly: test/boiler}", "end: {assembly: test/bare_end}")
    message = build_refusal(text, tmp_path / "results", library)
    assert message.startswith("EF-23 at components.boiler-Boiler.inputs: the bare item 'end-Device'")
    assert "[source: boiler-Boiler (import boiler" in message


@pytest.mark.assemblies
def test_a_site_entry_is_a_circuit_end_of_its_own() -> None:
    """Catches a circuit that only imports can end."""
    cylinder = (
        "Cylinder: {class: hisim.components.simple_water_storage.SimpleDHWStorage, preset: standard, "
        "inputs: [UTSPConnector, {$port: dhw}], ports: {dhw: {circuit: dhw}}}"
    )
    model, record = expand_text(Real.heating_site(cylinder) + imports(Gas.CONNECTION, Gas.BOILER))
    assert model.components["Cylinder"].inputs[1] == DefaultInputs(source="boiler-Boiler")
    assert model.components["boiler-Boiler"].inputs[1] == DefaultInputs(source="Cylinder")
    assert record.site_ports["Cylinder"][0].partner == "boiler.dhw"


# ------------------------------------------------------------------------------------------ carriers


def gas_feed(source: str, output: str) -> AggregatorFeed:
    """A gas meter's declared feed for one consuming output, written explicitly (D30)."""
    return AggregatorFeed(source=source, output=output, tags=("GAS_CONSUMPTION_UNCONTROLLED",), weight=MEASURED)


@pytest.mark.assemblies
def test_a_fuel_need_lands_as_the_meters_declared_feed_written_explicitly(tmp_path: Path) -> None:
    """Catches a burner whose gas no meter observes, or a meter fed the consumer's bare name (D30)."""
    model, record = gas_heating()

    assert model.components["gas-GasMeter"].inputs == ()
    assert record.consuming == [
        ConsumingOutput("boiler-Boiler", "EnergyDemandSh", "natural_gas", "gas-GasMeter"),
        ConsumingOutput("boiler-Boiler", "EnergyDemandDhw", "natural_gas", "gas-GasMeter"),
    ]
    assert record.selection.landings == record.consuming
    fuel = ports(record, "boiler")["fuel"]
    assert (fuel.decision, fuel.partner) == ("bound", "gas.connection (meter gas-GasMeter)")
    assert fuel.lowered_to == (
        "gas-GasMeter.inputs: boiler-Boiler.EnergyDemandSh (its declared feed)",
        "gas-GasMeter.inputs: boiler-Boiler.EnergyDemandDhw (its declared feed)",
    )
    assert ports(record, "gas")["connection"].partner == "boiler.fuel"
    built = build_text(Real.GAS_HOUSE.read_text(encoding="utf-8"), tmp_path)
    assert built.model.components["gas-GasMeter"].inputs == (
        gas_feed("heating-Boiler", "EnergyDemandSh"),
        gas_feed("heating-Boiler", "EnergyDemandDhw"),
    )


@pytest.mark.assemblies
def test_two_named_outputs_land_as_two_feeds_and_an_unnamed_one_is_not_metered(tmp_path: Path) -> None:
    """Catches a meter that meters every output its class declares instead of the ones the carrier need names.

    Inline mocks: the meter's class declares a feed for an output the burner's need leaves out, which no
    real burner has.
    """
    library = Gas.mock_library(tmp_path)
    library.add(
        "test/combi",
        f"""\
        schema_version: 4
        kind: assembly
        name: test/combi
        components:
          Burner: {{class: {MOCKS}.MockCombiBurner, preset: standard}}
        interface:
          needs:
            fuel: {{carrier: natural_gas, outputs: [Burner.FuelSh, Burner.FuelDhw]}}
        {EMPTY_CONTRACT}""",
    )
    text = site(WEATHER, OCCUPANCY) + imports("gas: {assembly: test/gas}", "combi: {assembly: test/combi}")
    built = build_text(text, tmp_path / "results", library.resolver())
    assert built.model.components["gas-Meter"].inputs == (
        gas_feed("combi-Burner", "FuelSh"),
        gas_feed("combi-Burner", "FuelDhw"),
    )


@pytest.mark.assemblies
def test_a_fuel_need_without_a_provider_two_providers_and_an_idle_provider_are_refused() -> None:
    """Catches a carrier without exactly one provider, and a provider nothing consumes from."""
    message = refusal(Real.heating_site() + imports(Gas.BOILER, Gas.CYLINDER))
    assert message.startswith("EF-7L at import 'boiler'") and "no provider of natural_gas" in message
    second = "gas2: {assembly: supply/gas_connection}"
    message = refusal(Real.heating_site() + imports(Gas.CONNECTION, second, Gas.BOILER, Gas.CYLINDER))
    assert message.startswith("EF-7L") and "natural_gas has 2 providers, gas.connection, gas2.connection" in message
    message = refusal(site(Real.WEATHER, Real.OCCUPANCY) + imports(Gas.CONNECTION))
    assert message.startswith("EF-7L at import 'gas'") and "has no bound consumer" in message


@pytest.mark.assemblies
def test_no_verb_binds_a_carrier_need() -> None:
    """Catches a verb on a carrier need being accepted: there is one provider per carrier."""
    message = refusal(
        Real.heating_site()
        + imports(
            Gas.CONNECTION, "boiler: {assembly: heating/gas_condensing_boiler, bind: {fuel: gas}}", Gas.CYLINDER
        )
    )
    assert message.startswith("EF-7G at import 'boiler'") and "'fuel', which is a carrier port" in message


@pytest.mark.assemblies
def test_site_entries_provide_and_consume_carriers_alike() -> None:
    """Catches a carrier that only assemblies can provide or consume."""
    meter = (
        "Meter: {class: hisim.components.gas_meter.GasMeter, preset: standard, inputs: [{$port: gas}], "
        "ports: {gas: {carrier: natural_gas}}}"
    )
    model, record = expand_text(Real.heating_site(meter) + imports(Gas.BOILER, Gas.CYLINDER))
    assert model.components["Meter"].inputs == ()
    assert record.selection.landings == [
        ConsumingOutput("boiler-Boiler", "EnergyDemandSh", "natural_gas", "Meter"),
        ConsumingOutput("boiler-Boiler", "EnergyDemandDhw", "natural_gas", "Meter"),
    ]
    assert record.site_ports["Meter"][0].partner == "boiler.fuel"


@pytest.mark.assemblies
@pytest.mark.parametrize(
    ("port", "code", "fragment"),
    [
        ("{carrier: natural_gas}", "EF-70", "names its 'meter:'"),
        ("{carrier: electricity, meter: Meter}", "EF-70", "electricity has no link"),
        ("{carrier: town_gas, meter: Meter}", "EF-70", "'town_gas', which is no energy carrier"),
    ],
)
def test_the_reader_refuses_a_provision_of_the_wrong_shape(tmp_path: Path, port: str, code: str, fragment: str) -> None:
    """A fuel provision names its meter, an electricity provision none, a carrier is an energy carrier."""
    library = Library(tmp_path)
    library.add(
        "test/supply",
        f"""\
        schema_version: 4
        kind: assembly
        name: test/supply
        components:
          Meter: {{class: {MOCKS}.MockGasMeter, preset: standard, inputs: [{{$port: connection}}]}}
        interface:
          provides:
            connection: {port}
        {EMPTY_CONTRACT}""",
    )
    with pytest.raises(EnergySystemError) as raised:
        expand_text(site(WEATHER, OCCUPANCY) + imports("supply: {assembly: test/supply}"), library.resolver())
    assert str(raised.value).startswith(code) and fragment in str(raised.value)


@pytest.mark.assemblies
@pytest.mark.parametrize(
    ("outputs", "carrier", "meter", "code", "fragment"),
    [
        (
            "[Boiler.Nope]",
            "natural_gas",
            "MockGasMeter",
            "EF-21",
            "'boiler-Boiler.Nope', which MockBoiler does not have",
        ),
        (
            "[Boiler.FuelUse]",
            "heating_oil",
            "MockGasMeter",
            "EF-7M",
            "consumed as heating_oil, but its energy port carries natural_gas",
        ),
        (
            "[Boiler.FuelUse, Boiler.FlueLoss]",
            "natural_gas",
            "MockGasMeter",
            "EF-7N",
            "names the consuming output 'boiler-Boiler.FlueLoss', but the meter 'supply-Meter' (MockGasMeter) declares "
            "no feed for it; of MockBoiler it declares feeds for FuelUse",
        ),
        (
            "[Boiler.FuelUse]",
            "natural_gas",
            "MockDoubleGasMeter",
            "EF-7N",
            "names the consuming output 'boiler-Boiler.FuelUse', but the meter 'supply-Meter' (MockDoubleGasMeter) "
            "declares 2 feeds for it, so the one it lands as is not determined",
        ),
    ],
)
def test_the_wiring_checks_what_a_carrier_need_states_and_no_wire_shows(
    tmp_path: Path, outputs: str, carrier: str, meter: str, code: str, fragment: str
) -> None:
    """A consuming output exists, carries the need's carrier, and is one its meter declares exactly one feed for.

    Inline mocks: each case is a need that contradicts its member's or its meter's class, which no real
    assembly does.
    """
    library = Gas.mock_library(tmp_path)
    library.add(
        "test/burner",
        f"""\
        schema_version: 4
        kind: assembly
        name: test/burner
        components:
          Boiler: {{class: {MOCKS}.MockBoiler, preset: condensing, inputs: [{{$port: dhw}}]}}
        interface:
          needs:
            fuel: {{carrier: {carrier}, outputs: {outputs}}}
            dhw: {{circuit: dhw, member: Boiler}}
        {EMPTY_CONTRACT}""",
    )
    library.add(
        "test/supply",
        f"""\
        schema_version: 4
        kind: assembly
        name: test/supply
        components:
          Meter: {{class: {MOCKS}.{meter}, preset: standard, inputs: [{{$port: connection}}]}}
        interface:
          provides:
            connection: {{carrier: {carrier}, meter: Meter}}
        {EMPTY_CONTRACT}""",
    )
    text = site(WEATHER, OCCUPANCY) + imports(
        "supply: {assembly: test/supply}", "boiler: {assembly: test/burner}", "cylinder: {assembly: test/cylinder}"
    )
    message = build_refusal(text, tmp_path / "results", library)
    assert message.startswith(code) and fragment in message
    assert "[source: " in message


@pytest.mark.assemblies
def test_an_electricity_need_checks_the_one_provider_and_writes_no_wire(tmp_path: Path) -> None:
    """Catches an electricity need lowering a wire, or passing without a grid connection."""
    library = Library(tmp_path)
    library.add(
        "test/plug",
        f"""\
        schema_version: 4
        kind: assembly
        name: test/plug
        components:
          Heater: {{class: {MOCKS}.MockHeater, preset: standard}}
        interface:
          needs:
            power: {{carrier: electricity, outputs: [Heater.ElectricityInput]}}
        {EMPTY_CONTRACT}""",
    )
    house = site(WEATHER, OCCUPANCY)
    message = refusal(house + imports("plug: {assembly: test/plug}"), library)
    assert message.startswith("EF-7L at import 'plug'") and "no provider of electricity" in message
    model, record = expand_text(
        house + imports("plug: {assembly: test/plug}", "grid: {assembly: supply/electricity_grid}"), library.resolver()
    )
    assert model.components["plug-Heater"].inputs == ()
    assert ports(record, "plug")["power"].partner == "grid.connection (no link)"
    assert record.consuming == [ConsumingOutput("plug-Heater", "ElectricityInput", "electricity", None)]


# --------------------------------------------------------------------------------------------- facts


class ScalarBattery:
    """An inline battery whose fact need reads one array's peak power: a scalar fact need.

    The real battery sums every array (``many: true``); a single-provider scalar read uses the engine's
    bare-fact rule there (D29), so a scalar fact need is a shape only a mock shows.
    """

    TEXT: ClassVar[str] = f"""\
        schema_version: 4
        kind: assembly
        name: test/scalar_battery
        components:
          Battery: {{class: {MOCKS}.MockBattery, preset: sized_to_pv}}
        interface:
          needs:
            pv_power: {{fact: pv_peak_power_in_watt, into: [Battery]}}
        {EMPTY_CONTRACT}"""

    @classmethod
    def library(cls, directory: Path) -> Library:
        """A temporary library holding the scalar battery."""
        library = Library(directory)
        library.add("test/scalar_battery", cls.TEXT)
        return library


@pytest.mark.assemblies
def test_a_fact_need_lowers_to_a_sizing_line_naming_its_provider_bound_by_a_verb(tmp_path: Path) -> None:
    """Catches a battery that cannot be pointed at one of two arrays."""
    library = ScalarBattery.library(tmp_path)
    model, record = expand_text(
        site(Real.WEATHER)
        + imports(
            "pv: {assembly: pv/array, instances: {east: {}, west: {}}}",
            "battery: {assembly: test/scalar_battery, bind: {pv_power: pv.east}}",
        ),
        library.resolver(),
    )
    assert model.components["battery-Battery"].sizing_sources == {
        "pv_peak_power_in_watt": SourceReference(component="pv-east-PVSystem", fact="pv_peak_power_in_watt")
    }
    pv_power = ports(record, "battery")["pv_power"]
    assert (pv_power.verb, pv_power.partner) == ("bind", "pv-east-PVSystem")
    assert record.source_map.entries[("battery-Battery", "sizing_sources.pv_peak_power_in_watt")].note == (
        "port pv_power bound (bind)"
    )


@pytest.mark.assemblies
def test_a_scalar_fact_need_with_two_providers_needs_a_verb_and_a_site_provider_counts(tmp_path: Path) -> None:
    """Catches a fact need silently picking one of two arrays; a site array is a provider by its class."""
    library = ScalarBattery.library(tmp_path)
    pv = "pv: {assembly: pv/array, instances: {east: {}, west: {}}}"
    message = refusal(site(Real.WEATHER) + imports(pv, "battery: {assembly: test/scalar_battery}"), library)
    assert message.startswith("EF-7B at import 'battery'")
    assert "pv-east-PVSystem, pv-west-PVSystem" in message and "`bind: {pv_power: pv.east}`" in message
    model, _record = expand_text(
        site(Real.WEATHER, Real.ROOF) + imports("battery: {assembly: test/scalar_battery}"), library.resolver()
    )
    assert model.components["battery-Battery"].sizing_sources["pv_peak_power_in_watt"] == SourceReference(
        component="Roof", fact="pv_peak_power_in_watt"
    )


@pytest.mark.assemblies
def test_a_many_fact_need_lowers_the_list_of_every_provider_in_written_order_and_takes_no_verb() -> None:
    """Site entries first, then the imports and instances as written; a verb cannot choose among them."""
    model, record = expand_text(
        site(Real.WEATHER, Real.ROOF)
        + imports("pv: {assembly: pv/array, instances: {west: {}, east: {}}}", "battery: {assembly: storage/battery}")
    )
    listed = model.components["battery-Battery"].sizing_sources["pv_peak_power_in_watt"]
    assert isinstance(listed, tuple) and [item.component for item in listed] == [
        "Roof",
        "pv-west-PVSystem",
        "pv-east-PVSystem",
    ]
    assert ports(record, "battery")["pv_peak_power"].partner == "Roof, pv-west-PVSystem, pv-east-PVSystem"
    message = refusal(
        site(Real.WEATHER, Real.ROOF) + imports("battery: {assembly: storage/battery, bind: {pv_peak_power: Roof}}")
    )
    assert message.startswith("EF-7G") and "a many fact need bind every provider in scope" in message
    message = refusal(site(Real.WEATHER) + imports("battery: {assembly: storage/battery}"))
    assert message.startswith("EF-7A at import 'battery'") and "pv_peak_power_in_watt" in message


@pytest.mark.assemblies
def test_a_list_into_a_one_provider_law_and_one_provider_into_a_sum_are_refused_by_name(tmp_path: Path) -> None:
    """The sizing engine refuses a line whose shape is not the cardinality of the law reading it (EF-4E)."""
    library = Library(tmp_path)
    for name, (member_class, preset, many) in {
        "test/listed": ("MockBattery", "sized_to_pv", "true"),
        "test/single": ("MockArrayBattery", "sized_to_all_arrays", "false"),
    }.items():
        library.add(
            name,
            f"""\
            schema_version: 4
            kind: assembly
            name: {name}
            components:
              Battery: {{class: {MOCKS}.{member_class}, preset: {preset}}}
            interface:
              needs:
                pv_power: {{fact: pv_peak_power_in_watt, many: {many}, into: [Battery]}}
            {EMPTY_CONTRACT}""",
        )
    for name, fragment in (("listed", "expected one reference"), ("single", "expected a list of reference")):
        message = build_refusal(
            site(Real.WEATHER) + imports(Real.PV, f"battery: {{assembly: test/{name}}}"), tmp_path / name, library
        )
        assert message.startswith("EF-4E") and fragment in message


@pytest.mark.assemblies
def test_two_fact_ports_lowering_one_fact_into_one_member_are_refused(tmp_path: Path) -> None:
    """Catches the second fact port silently overwriting the first's sizing_sources line."""
    library = Library(tmp_path)
    library.add(
        "test/twice",
        f"""\
        schema_version: 4
        kind: assembly
        name: test/twice
        components:
          Battery: {{class: {MOCKS}.MockBattery, preset: sized_to_pv}}
        interface:
          needs:
            size_one: {{fact: pv_peak_power_in_watt, into: [Battery]}}
            size_two: {{fact: pv_peak_power_in_watt, into: [Battery]}}
        {EMPTY_CONTRACT}""",
    )
    pv = "pv: {assembly: pv/array, instances: {east: {}, west: {}}}"
    battery = "battery: {assembly: test/twice, bind: {size_one: pv.east, size_two: pv.west}}"
    message = refusal(site(Real.WEATHER) + imports(pv, battery), library)
    assert message.startswith("EF-7J at import 'battery'")
    for name in ("'size_two'", "port size_one bound", "pv_peak_power_in_watt", "'battery-Battery'"):
        assert name in message, f"{name!r} is not in: {message}"


@pytest.mark.assemblies
def test_a_fuel_provision_whose_meter_is_absent_is_refused_by_name() -> None:
    """Catches the binder raising a KeyError for a fuel provision's meter the selection left out."""
    provision = Port(name="connection", section="provides", kind=PortKind.CARRIER, carrier="natural_gas", meter="Meter")
    need = Port(name="gas", section="needs", kind=PortKind.CARRIER, carrier="natural_gas", outputs=("Boiler.FuelUse",))
    provider = Owner(
        "gas",
        "import 'gas'",
        "the import 'gas'",
        BindingVerbs(),
        {},
        {"connection": provision},
        {"connection": PortState.PROVIDED},
    )
    consumer = Owner(
        "boiler",
        "import 'boiler'",
        "the import 'boiler'",
        BindingVerbs(),
        {},
        {"gas": need},
        {"gas": PortState.REQUIRED},
    )
    with pytest.raises(EnergySystemAssemblyError, match="EF-7J") as raised:
        PortBinder({}, {"gas": [provider], "boiler": [consumer]}, ImportRecord()).bind()
    message = str(raised.value)
    for name in ("import 'gas'", "'connection'", "'Meter'", "boiler.gas"):
        assert name in message, f"{name!r} is not in: {message}"


@pytest.mark.assemblies
def test_an_active_observer_port_whose_members_are_all_absent_is_refused_by_name() -> None:
    """Catches an observer port registering no observer, silently, when the selection left out its members."""
    port = Port(name="reading", section="observes", kind=PortKind.OBSERVER, into=("Meter",), selection=Selection())
    owner = Owner(
        "grid",
        "import 'grid'",
        "the import 'grid'",
        BindingVerbs(),
        {},
        {"reading": port},
        {"reading": PortState.REQUIRED},
    )
    with pytest.raises(EnergySystemAssemblyError, match="EF-7J") as raised:
        PortBinder({}, {"grid": [owner]}, ImportRecord()).bind()
    message = str(raised.value)
    for name in ("import 'grid'", "observer port 'reading'", "Meter"):
        assert name in message, f"{name!r} is not in: {message}"


@pytest.mark.assemblies
def test_a_provided_fact_outside_the_member_classes_contributions_fails_the_library_check(tmp_path: Path) -> None:
    """Catches a fact port promising a fact its member's class never computes."""
    library = Library(tmp_path)
    library.add(
        "test/liar",
        f"""\
        schema_version: 4
        kind: assembly
        name: test/liar
        components:
          Tank: {{class: {MOCKS}.MockTank, preset: standard}}
        interface:
          provides:
            peak: {{fact: pv_peak_power_in_watt, member: Tank}}
        {EMPTY_CONTRACT}""",
    )
    message = refusal(site(WEATHER, OCCUPANCY) + imports("liar: {assembly: test/liar}"), library)
    assert message.startswith("EF-75") and "MockTankConfig (member 'Tank') does not declare" in message


# ------------------------------------------------------------------------------------------ one day


@pytest.fixture(scope="module", name="gas_house_run")
def fixture_gas_house_run(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """One day of the gas composed file with the energy balance; its result directory, for the module."""
    directory = tmp_path_factory.mktemp("gas_house")
    arguments = [str(Real.GAS_HOUSE), str(Real.PARAMETERS), "--result-dir", str(directory)]
    assert main(["energy-system", "run", *arguments]) == ExitCodes.OK
    yield directory


@pytest.mark.assemblies
def test_the_gas_house_runs_a_day_its_meter_reads_the_boilers_fuel_and_its_balance_closes(gas_house_run: Path) -> None:
    """Catches a gas meter that reads something else than the gas burned, or a balance that does not close."""
    kpis = KpiFinder(json.loads((gas_house_run / "all_kpis.json").read_text(encoding="utf-8")))
    fuel = kpis.value(name="Total Gas consumption (energy)", source="heating-Boiler")
    metered = kpis.value(name="Total gas consumption", source="gas-GasMeter")
    assert fuel > 0 and metered == pytest.approx(fuel, rel=1e-12)
    assert json.loads((gas_house_run / "balance_report.json").read_text(encoding="utf-8"))["verdict"] == "closes"


@pytest.mark.assemblies
def test_the_record_carries_the_consuming_outputs_and_a_rerun_checks_them_again(
    gas_house_run: Path, tmp_path: Path
) -> None:
    """Catches a re-run that skips the carrier and meter-feed checks the expanding run made (EF-7M, EF-7N)."""
    record = yaml.safe_load((gas_house_run / "realized.energy_system.yaml").read_text(encoding="utf-8"))
    consuming = record["metadata"]["imports"]["consuming"]
    assert consuming == [
        {"consumer": "heating-Boiler", "output": output, "carrier": "natural_gas", "meter": "gas-GasMeter"}
        for output in ("EnergyDemandSh", "EnergyDemandDhw")
    ]
    parameters = gas_house_run / "realized.simulation.yaml"
    run_energy_system(gas_house_run / "realized.energy_system.yaml", parameters, str(tmp_path / "again"), rerun=True)
    consuming[0]["carrier"] = "heating_oil"
    edited = tmp_path / "edited.energy_system.yaml"
    edited.write_text(yaml.safe_dump(record, sort_keys=False), encoding="utf-8")
    with pytest.raises(EnergySystemWiringError, match="EF-7M .*consumed as heating_oil"):
        run_energy_system(edited, parameters, str(tmp_path / "edited"), rerun=True)


@pytest.mark.assemblies
def test_the_two_array_house_sizes_its_battery_to_both_arrays_and_its_record_reruns(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """5 kWp + 3 kWp give 8 kWh; the record writes the list, the audit one lookup per array, and re-runs."""
    arrays = (
        "pv: {assembly: pv/array, instances: {east: {azimuth_in_degree: 90, power_in_watt: 5000}, "
        "west: {azimuth_in_degree: 270, power_in_watt: 3000}}}"
    )
    house = tmp_path / "two_arrays.energy_system.yaml"
    house.write_text(
        site(Real.WEATHER, Real.OCCUPANCY)
        + imports(arrays, "battery: {assembly: storage/battery}", Real.CONTROL, Real.GRID_ON_BALANCE),
        encoding="utf-8",
    )
    result = tmp_path / "run"
    assert main(["energy-system", "run", str(house), str(Real.PARAMETERS), "--result-dir", str(result)]) == ExitCodes.OK
    record = yaml.safe_load((result / "realized.energy_system.yaml").read_text(encoding="utf-8"))
    battery = record["components"]["battery-Battery"]
    assert battery["config"]["custom_battery_capacity_generic_in_kilowatt_hour"] == pytest.approx(8.0)
    assert battery["sizing_sources"]["pv_peak_power_in_watt"] == [
        "pv-east-PVSystem.pv_peak_power_in_watt",
        "pv-west-PVSystem.pv_peak_power_in_watt",
    ]
    audit = yaml.safe_load((result / "realized.audit.yaml").read_text(encoding="utf-8"))
    lookups = [item for item in audit["resolution"]["lookups"] if item["consumer"] == "battery-Battery"]
    assert [(item["source"], item["value"], item.get("many")) for item in lookups] == [
        ("pv-east-PVSystem", 5000, True),
        ("pv-west-PVSystem", 3000, True),
    ]
    code = main(
        [
            "energy-system",
            "run",
            str(result / "realized.energy_system.yaml"),
            str(result / "realized.simulation.yaml"),
            "--result-dir",
            str(tmp_path / "again"),
            "--rerun",
        ]
    )
    capsys.readouterr()
    assert code == ExitCodes.OK
