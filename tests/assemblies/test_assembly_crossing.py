"""Circuit ends, carrier needs and fact ports (``assemblies_spec.md`` §3.2, §3.3, §5, §6, §11.1, lean v1 §13.1).

A circuit end binds the one other end of its circuit in scope and lowers, at every member carrying
its placeholder, to a bare name of each member of the other end. A carrier need needs the one
provider of its carrier: for a fuel the provider's meter takes a bare name of the consumer, which
its dynamic default connections expand, and the wiring checks each consuming output's carrier and
that the meter feeds exactly the named outputs; electricity writes no wire. A fact need lowers to a
``sizing_sources`` line naming its provider, ``many: true`` to the list of every provider. Every
refusal is checked by its code and the names in it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Tuple

import pytest
import yaml

from hisim.cli import main
from hisim.cli_exit import ExitCodes
from hisim.energy_system.assemblies.binding import Owner, PortBinder
from hisim.energy_system.assemblies.record import ImportRecord
from hisim.energy_system.assemblies.resolver import AssemblyResolver
from hisim.energy_system.errors import (
    EnergySystemAssemblyError,
    EnergySystemCatalogueError,
    EnergySystemError,
    EnergySystemWiringError,
)
from hisim.energy_system.executor import run_energy_system
from hisim.energy_system.imports_model import BindingVerbs, Port, PortKind, PortState, Selection
from hisim.energy_system.model import DefaultInputs, EnergySystemFile, SourceReference
from hisim.energy_system.wiring_checks import ConsumingOutput
from hisim.postprocessing.kpi_computation.kpi_address import KpiFinder
from tests.assemblies.helpers import (
    EMPTY_CONTRACT,
    MOCKS,
    OCCUPANCY,
    WEATHER,
    Library,
    Mocks,
    build_text,
    expand_text,
    site,
    system_text,
)

#: The site of most systems here.
SITE = site(WEATHER, OCCUPANCY)


def imports(*lines: str) -> str:
    """An ``imports:`` block of one-line imports."""
    return "imports:\n" + "".join(f"  {line}\n" for line in lines)


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


def boiler_house() -> Tuple[EnergySystemFile, ImportRecord]:
    """The committed boiler house, expanded."""
    return expand_text(system_text("boiler_house.energy_system.yaml"))


def ports(record: ImportRecord, key: str) -> dict:
    """The port records of one import without instances, by port name."""
    return {item.port: item for item in record.instance(key).ports}


# ------------------------------------------------------------------------------------------ circuits


@pytest.mark.base
def test_a_circuit_lowers_to_a_bare_name_of_the_other_end_at_each_end_and_records_both_ends() -> None:
    """Catches a circuit wired one way only, or recorded at one end only."""
    model, record = boiler_house()

    assert model.components["boiler-Boiler"].inputs == (DefaultInputs(source="cylinder-Cylinder"),)
    assert model.components["cylinder-Cylinder"].inputs == (
        DefaultInputs(source="Occupancy"),
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
            "boiler-Boiler.inputs: cylinder-Cylinder",
            "cylinder-Cylinder.inputs: boiler-Boiler",
        )
    )


@pytest.mark.base
def test_a_required_circuit_end_without_the_other_end_is_refused() -> None:
    """Catches a cylinder left without its boiler."""
    message = refusal(SITE + imports("cylinder: {assembly: mock/dhw_cylinder}"))
    assert message.startswith("EF-7A at import 'cylinder'")
    assert "an end of the circuit dhw" in message


@pytest.mark.base
def test_two_other_ends_need_a_verb_and_bind_decides() -> None:
    """Catches a circuit end silently picking one of two boilers."""
    gas = "gas: {assembly: mock/gas_connection}"
    boilers = ("one: {assembly: mock/gas_boiler}", "two: {assembly: mock/gas_boiler}")
    message = refusal(SITE + imports(gas, "cylinder: {assembly: mock/dhw_cylinder}", *boilers))
    assert message.startswith("EF-7B at import 'cylinder'") and "one.dhw, two.dhw" in message
    assert "`bind: {circuit: one.dhw}`" in message

    model, _record = expand_text(
        SITE
        + imports(
            gas,
            "cylinder: {assembly: mock/dhw_cylinder, bind: {circuit: two}}",
            "tank: {assembly: mock/dhw_cylinder, bind: {circuit: one.dhw}}",
            *boilers,
        )
    )
    assert model.components["cylinder-Cylinder"].inputs[1] == DefaultInputs(source="two-Boiler")
    assert model.components["one-Boiler"].inputs == (DefaultInputs(source="tank-Cylinder"),)


@pytest.mark.base
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
    text = SITE + imports(
        "boiler: {assembly: mock/gas_boiler}",
        "gas: {assembly: mock/gas_connection}",
        "cylinder: {assembly: mock/dhw_cylinder, bind: {circuit: floor}}",
        "floor: {assembly: test/floor, none: [space_heating]}",
    )
    message = refusal(text, library)
    assert message.startswith("EF-7K at import 'cylinder'")
    assert "the circuit space_heating ('space_heating')" in message


@pytest.mark.base
def test_a_member_at_an_end_that_reads_nothing_of_the_other_is_refused_by_the_wiring(tmp_path: Path) -> None:
    """D25: a member that neither owns nor reads a circuit output is a bare name without default connections."""
    library = Library(tmp_path)
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
    text = SITE + imports(
        "gas: {assembly: mock/gas_connection}", "boiler: {assembly: mock/gas_boiler}", "end: {assembly: test/bare_end}"
    )
    message = build_refusal(text, tmp_path / "results", library)
    assert message.startswith("EF-23 at components.boiler-Boiler.inputs: the bare item 'end-Device'")
    assert "[source: boiler-Boiler (import boiler" in message


@pytest.mark.base
def test_a_site_entry_is_a_circuit_end_of_its_own() -> None:
    """Catches a circuit that only imports can end."""
    tank = (
        f"Tank: {{class: {MOCKS}.MockCylinder, preset: standard, inputs: [Occupancy, {{$port: dhw}}], "
        "ports: {dhw: {circuit: dhw}}}"
    )
    model, record = expand_text(
        site(WEATHER, OCCUPANCY, tank)
        + imports("gas: {assembly: mock/gas_connection}", "boiler: {assembly: mock/gas_boiler}")
    )
    assert model.components["Tank"].inputs[1] == DefaultInputs(source="boiler-Boiler")
    assert model.components["boiler-Boiler"].inputs == (DefaultInputs(source="Tank"),)
    assert record.site_ports["Tank"][0].partner == "boiler.dhw"


# ------------------------------------------------------------------------------------------ carriers


@pytest.mark.base
def test_a_fuel_need_lowers_to_a_bare_name_in_the_providers_meter_and_hands_the_wiring_its_outputs() -> None:
    """Catches a burner whose gas no meter observes."""
    model, record = boiler_house()

    assert model.components["gas-Meter"].inputs == (DefaultInputs(source="boiler-Boiler"),)
    assert record.consuming == [ConsumingOutput("boiler-Boiler", "FuelUse", "natural_gas", "gas-Meter")]
    fuel = ports(record, "boiler")["fuel"]
    assert (fuel.decision, fuel.partner) == ("bound", "gas.connection (meter gas-Meter)")
    assert ports(record, "gas")["connection"].partner == "boiler.fuel"


@pytest.mark.base
def test_a_fuel_need_without_a_provider_two_providers_and_an_idle_provider_are_refused() -> None:
    """Catches a carrier without exactly one provider, and a provider nothing consumes from."""
    boiler, cylinder, gas = (
        "boiler: {assembly: mock/gas_boiler}",
        "cylinder: {assembly: mock/dhw_cylinder}",
        "gas: {assembly: mock/gas_connection}",
    )
    message = refusal(SITE + imports(boiler, cylinder))
    assert message.startswith("EF-7L at import 'boiler'") and "no provider of natural_gas" in message
    message = refusal(SITE + imports(gas, "gas2: {assembly: mock/gas_connection}", boiler, cylinder))
    assert message.startswith("EF-7L") and "natural_gas has 2 providers, gas.connection, gas2.connection" in message
    message = refusal(SITE + imports(gas))
    assert message.startswith("EF-7L at import 'gas'") and "has no bound consumer" in message


@pytest.mark.base
def test_no_verb_binds_a_carrier_need() -> None:
    """Catches a verb on a carrier need being accepted: there is one provider per carrier."""
    message = refusal(
        SITE
        + imports(
            "gas: {assembly: mock/gas_connection}",
            "boiler: {assembly: mock/gas_boiler, bind: {fuel: gas}}",
            "cylinder: {assembly: mock/dhw_cylinder}",
        )
    )
    assert message.startswith("EF-7G at import 'boiler'") and "'fuel', which is a carrier port" in message


@pytest.mark.base
def test_site_entries_provide_and_consume_carriers_alike() -> None:
    """Catches a carrier that only assemblies can provide or consume."""
    meter = (
        f"Meter: {{class: {MOCKS}.MockGasMeter, preset: standard, inputs: [{{$port: gas}}], "
        "ports: {gas: {carrier: natural_gas}}}"
    )
    model, record = expand_text(
        site(WEATHER, OCCUPANCY, meter)
        + imports("boiler: {assembly: mock/gas_boiler}", "cylinder: {assembly: mock/dhw_cylinder}")
    )
    assert model.components["Meter"].inputs == (DefaultInputs(source="boiler-Boiler"),)
    assert record.site_ports["Meter"][0].partner == "boiler.fuel"


@pytest.mark.base
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
        expand_text(SITE + imports("supply: {assembly: test/supply}"), library.resolver())
    assert str(raised.value).startswith(code) and fragment in str(raised.value)


@pytest.mark.base
@pytest.mark.parametrize(
    ("outputs", "carrier", "code", "fragment"),
    [
        ("[Boiler.Nope]", "natural_gas", "EF-21", "'boiler-Boiler.Nope', which MockBoiler does not have"),
        (
            "[Boiler.FuelUse]",
            "heating_oil",
            "EF-7M",
            "consumed as heating_oil, but its energy port carries natural_gas",
        ),
        ("[Boiler.FuelUse, Boiler.FlueLoss]", "natural_gas", "EF-7N", "feeds FuelUse of MockBoiler 'boiler-Boiler'"),
    ],
)
def test_the_wiring_checks_what_a_carrier_need_states_and_no_wire_shows(
    tmp_path: Path, outputs: str, carrier: str, code: str, fragment: str
) -> None:
    """A consuming output exists, carries the need's carrier, and is exactly what the meter feeds."""
    library = Library(tmp_path)
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
          Meter: {{class: {MOCKS}.MockGasMeter, preset: standard, inputs: [{{$port: connection}}]}}
        interface:
          provides:
            connection: {{carrier: {carrier}, meter: Meter}}
        {EMPTY_CONTRACT}""",
    )
    text = SITE + imports(
        "supply: {assembly: test/supply}", "boiler: {assembly: test/burner}", "cylinder: {assembly: mock/dhw_cylinder}"
    )
    message = build_refusal(text, tmp_path / "results", library)
    assert message.startswith(code) and fragment in message
    assert "[source: " in message


@pytest.mark.base
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
    message = refusal(SITE + imports("plug: {assembly: test/plug}"), library)
    assert message.startswith("EF-7L at import 'plug'") and "no provider of electricity" in message
    model, record = expand_text(
        SITE + imports("plug: {assembly: test/plug}", "grid: {assembly: mock/electricity_grid}"), library.resolver()
    )
    assert model.components["plug-Heater"].inputs == ()
    assert ports(record, "plug")["power"].partner == "grid.connection (no link)"
    assert record.consuming == [ConsumingOutput("plug-Heater", "ElectricityInput", "electricity", None)]


# --------------------------------------------------------------------------------------------- facts


@pytest.mark.base
def test_a_fact_need_lowers_to_a_sizing_line_naming_its_provider_bound_by_a_verb() -> None:
    """Catches a battery that cannot be pointed at one of two arrays."""
    model, record = boiler_house()
    assert model.components["battery-Battery"].sizing_sources == {
        "pv_peak_power_in_watt": SourceReference(component="pv-east-PVSystem", fact="pv_peak_power_in_watt")
    }
    pv_power = ports(record, "battery")["pv_power"]
    assert (pv_power.verb, pv_power.partner) == ("bind", "pv-east-PVSystem")
    assert record.source_map.entries[("battery-Battery", "sizing_sources.pv_peak_power_in_watt")].note == (
        "port pv_power bound (bind)"
    )


@pytest.mark.base
def test_a_scalar_fact_need_with_two_providers_needs_a_verb_and_a_site_provider_counts() -> None:
    """Catches a fact need silently picking one of two arrays; a site array is a provider by its class."""
    pv = "pv: {assembly: mock/pv_array, instances: {east: {}, west: {}}}"
    message = refusal(site(WEATHER) + imports(pv, "battery: {assembly: mock/battery}"))
    assert message.startswith("EF-7B at import 'battery'")
    assert "pv-east-PVSystem, pv-west-PVSystem" in message and "`bind: {pv_power: pv.east}`" in message
    roof = f"Roof: {{class: {MOCKS}.MockPVSystem, preset: rooftop, inputs: [Weather]}}"
    model, _record = expand_text(site(WEATHER, roof) + imports("battery: {assembly: mock/battery}"))
    assert model.components["battery-Battery"].sizing_sources["pv_peak_power_in_watt"] == SourceReference(
        component="Roof", fact="pv_peak_power_in_watt"
    )


@pytest.mark.base
def test_a_many_fact_need_lowers_the_list_of_every_provider_in_written_order_and_takes_no_verb() -> None:
    """Site entries first, then the imports and instances as written; a verb cannot choose among them."""
    roof = f"Roof: {{class: {MOCKS}.MockPVSystem, preset: rooftop, inputs: [Weather]}}"
    model, record = expand_text(
        site(WEATHER, roof)
        + imports(
            "pv: {assembly: mock/pv_array, instances: {west: {}, east: {}}}", "battery: {assembly: mock/array_battery}"
        )
    )
    listed = model.components["battery-Battery"].sizing_sources["pv_peak_power_in_watt"]
    assert isinstance(listed, tuple) and [item.component for item in listed] == [
        "Roof",
        "pv-west-PVSystem",
        "pv-east-PVSystem",
    ]
    assert ports(record, "battery")["pv_power"].partner == "Roof, pv-west-PVSystem, pv-east-PVSystem"
    message = refusal(site(WEATHER, roof) + imports("battery: {assembly: mock/array_battery, bind: {pv_power: Roof}}"))
    assert message.startswith("EF-7G") and "a many fact need bind every provider in scope" in message
    message = refusal(site(WEATHER) + imports("battery: {assembly: mock/array_battery}"))
    assert message.startswith("EF-7A at import 'battery'") and "pv_peak_power_in_watt" in message


@pytest.mark.base
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
    pv = "pv: {assembly: mock/pv_array}"
    for name, fragment in (("listed", "expected one reference"), ("single", "expected a list of reference")):
        message = build_refusal(
            site(WEATHER) + imports(pv, f"battery: {{assembly: test/{name}}}"), tmp_path / name, library
        )
        assert message.startswith("EF-4E") and fragment in message


@pytest.mark.base
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
    pv = "pv: {assembly: mock/pv_array, instances: {east: {}, west: {}}}"
    battery = "battery: {assembly: test/twice, bind: {size_one: pv.east, size_two: pv.west}}"
    message = refusal(site(WEATHER) + imports(pv, battery), library)
    assert message.startswith("EF-7J at import 'battery'")
    for name in ("'size_two'", "port size_one bound", "pv_peak_power_in_watt", "'battery-Battery'"):
        assert name in message, f"{name!r} is not in: {message}"


@pytest.mark.base
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


@pytest.mark.base
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


@pytest.mark.base
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
    message = refusal(SITE + imports("liar: {assembly: test/liar}"), library)
    assert message.startswith("EF-75") and "MockTankConfig (member 'Tank') does not declare" in message


# ------------------------------------------------------------------------------------------ one day


def run(system: str, directory: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Runs one committed mock system for a day with the energy balance, and returns its result directory."""
    monkeypatch.setenv(AssemblyResolver.ENVIRONMENT_VARIABLE, str(Mocks.LIBRARY))
    parameters = Mocks.ROOT / "one_day_balance.simulation.yaml"
    arguments = ["energy-system", "run", str(Mocks.ROOT / "systems" / system), str(parameters), "--result-dir"]
    assert main(arguments + [str(directory)]) == ExitCodes.OK
    return directory


@pytest.mark.base
def test_the_boiler_house_runs_a_day_its_meter_reads_the_boilers_fuel_and_its_balance_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches a gas meter that reads something else than the gas burned, or a balance that does not close."""
    result = run("boiler_house.energy_system.yaml", tmp_path / "run", monkeypatch)
    kpis = KpiFinder(json.loads((result / "all_kpis.json").read_text(encoding="utf-8")))
    fuel = kpis.value(name="Boiler fuel", source="boiler-Boiler")
    assert fuel > 0 and kpis.value(name="Gas consumption", source="gas-Meter") == pytest.approx(fuel, rel=1e-12)
    assert json.loads((result / "balance_report.json").read_text(encoding="utf-8"))["verdict"] == "closes"


@pytest.mark.base
def test_the_record_carries_the_consuming_outputs_and_a_rerun_checks_them_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches a re-run that skips the carrier and meter-feed checks the expanding run made (EF-7M, EF-7N)."""
    result = run("boiler_house.energy_system.yaml", tmp_path / "run", monkeypatch)
    monkeypatch.delenv(AssemblyResolver.ENVIRONMENT_VARIABLE)
    record = yaml.safe_load((result / "realized.energy_system.yaml").read_text(encoding="utf-8"))
    consuming = record["metadata"]["imports"]["consuming"]
    assert consuming == [
        {"consumer": "boiler-Boiler", "output": "FuelUse", "carrier": "natural_gas", "meter": "gas-Meter"}
    ]
    parameters = result / "realized.simulation.yaml"
    run_energy_system(result / "realized.energy_system.yaml", parameters, str(tmp_path / "again"), rerun=True)
    consuming[0]["carrier"] = "heating_oil"
    edited = tmp_path / "edited.energy_system.yaml"
    edited.write_text(yaml.safe_dump(record, sort_keys=False), encoding="utf-8")
    with pytest.raises(EnergySystemWiringError, match="EF-7M .*consumed as heating_oil"):
        run_energy_system(edited, parameters, str(tmp_path / "edited"), rerun=True)


@pytest.mark.base
def test_the_two_array_house_sizes_its_battery_to_both_arrays_and_its_record_reruns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """5 kWp + 3 kWp give 8 kWh; the record writes the list, the audit one lookup per array, and re-runs."""
    result = run("two_array_house.energy_system.yaml", tmp_path / "run", monkeypatch)
    kpis = KpiFinder(json.loads((result / "all_kpis.json").read_text(encoding="utf-8")))
    assert kpis.value(name="Battery capacity", source="battery-Battery") == pytest.approx(8.0)
    record = yaml.safe_load((result / "realized.energy_system.yaml").read_text(encoding="utf-8"))
    assert record["components"]["battery-Battery"]["sizing_sources"]["pv_peak_power_in_watt"] == [
        "pv-east-PVSystem.pv_peak_power_in_watt",
        "pv-west-PVSystem.pv_peak_power_in_watt",
    ]
    audit = yaml.safe_load((result / "realized.audit.yaml").read_text(encoding="utf-8"))
    lookups = [item for item in audit["resolution"]["lookups"] if item["consumer"] == "battery-Battery"]
    assert [(item["source"], item["value"], item.get("many")) for item in lookups] == [
        ("pv-east-PVSystem", 5000.0, True),
        ("pv-west-PVSystem", 3000.0, True),
    ]
    monkeypatch.delenv(AssemblyResolver.ENVIRONMENT_VARIABLE)
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
