"""One composed fixture system through ``hisim energy-system run``, and what its results say.

The fixture house imports two PV instances, a hot-water package nested three deep and a backup
heater, and runs one day. The run must write the import record and the source maps into the
realized record, and its ``all_kpis.json`` must address each array by import and instance, so that
the finder returns exactly the two arrays for ``import_key="pv"`` (``roadmap/kpi_address_spec.md``,
``assemblies_spec.md`` §2.4). Re-running the realized record needs no assembly and reproduces it.
"""

import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Dict

import pytest
import yaml

from hisim.cli import main
from hisim.energy_system.assemblies.resolver import AssemblyResolver
from hisim.postprocessing.kpi_computation.kpi_address import KpiFinder
from tests.assemblies.helpers import Fixtures


@pytest.fixture(name="house_run", scope="module")
def fixture_house_run(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Runs the fixture house once for the module, through the console command."""
    result = tmp_path_factory.mktemp("house_run")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv(AssemblyResolver.ENVIRONMENT_VARIABLE, str(Fixtures.LIBRARY))
    try:
        code = main(
            ["energy-system", "run", str(Fixtures.HOUSE), str(Fixtures.PARAMETERS), "--result-dir", str(result)]
        )
    finally:
        monkeypatch.undo()
    assert code == 0
    return result


def realized(directory: Path) -> Dict[str, Any]:
    """The realized record a run wrote."""
    loaded: Dict[str, Any] = yaml.safe_load((directory / "realized.energy_system.yaml").read_text(encoding="utf-8"))
    return loaded


@pytest.mark.base
def test_the_run_writes_the_import_record_and_the_source_maps(house_run: Path) -> None:
    """The realized record is flat, names the members by address and carries both blocks."""
    record = realized(house_run)

    assert record["schema_version"] == 3
    assert "imports" not in record
    assert list(record["components"])[2:4] == ["pv-east-PVSystem", "pv-west-PVSystem"]
    metadata = record["metadata"]
    assert [instance["path"] for instance in metadata["imports"]["instances"]] == [
        "pv[east]",
        "pv[west]",
        "hot_water → heater → tank",
        "hot_water → heater",
        "hot_water",
        "backup",
    ]
    assert metadata["imports"]["addresses"]["backup-Heater"]["assembly"] == "generator/electric_heater"
    assert metadata["source_map"]["pv-east-PVSystem"]["inputs[0]"]["note"] == "port weather bound to Weather (default)"


@pytest.mark.base
def test_the_kpis_of_two_instances_carry_their_import_and_instance(house_run: Path) -> None:
    """``all_kpis.json`` keys each array by its address; the finder filters by import without splitting."""
    document = json.loads((house_run / "all_kpis.json").read_text(encoding="utf-8"))
    pv = document["BUI1"]["Rooftop PV"]

    east = pv["PV production (pv-east-PVSystem)"]["source"]
    west = pv["PV production (pv-west-PVSystem)"]["source"]
    assert (east["import"], east["instance"], east["member"], east["assembly"]) == (
        "pv",
        "east",
        "PVSystem",
        "pv/array",
    )
    assert (west["import"], west["instance"], west["name"]) == ("pv", "west", "pv-west-PVSystem")
    assert east["display_name"] == "PV array, east, azimuth 90"
    assert west["display_name"] == "PV array, west, azimuth 270"

    finder = KpiFinder(document)
    addresses = finder.addresses(import_key="pv")
    assert sorted(address.source.name for address in addresses if address.source is not None) == [
        "pv-east-PVSystem",
        "pv-west-PVSystem",
    ]
    assert len(addresses) == 2
    west_only = finder.addresses(import_key="pv", instance="west")
    assert [address.source.instance for address in west_only if address.source is not None] == ["west"]


@pytest.fixture(name="boiler_run", scope="module")
def fixture_boiler_run(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Runs the boiler house — a dhw circuit, a natural-gas carrier, a fact port — once for the module."""
    result = tmp_path_factory.mktemp("boiler_run")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv(AssemblyResolver.ENVIRONMENT_VARIABLE, str(Fixtures.LIBRARY))
    try:
        code = main(
            [
                "energy-system",
                "run",
                str(Fixtures.SYSTEMS / "boiler_house.energy_system.yaml"),
                str(Fixtures.ROOT / "one_day_balance.simulation.yaml"),
                "--result-dir",
                str(result),
            ]
        )
    finally:
        monkeypatch.undo()
    assert code == 0
    return result


@pytest.mark.base
def test_a_circuit_and_a_carrier_run_one_day_with_the_energy_balance_closed(boiler_run: Path) -> None:
    """The boiler's ports balance; the meter reads what the boiler burns; the battery is sized to one array."""
    balance = json.loads((boiler_run / "balance_report.json").read_text(encoding="utf-8"))
    assert balance["verdict"] == "closes"
    (boiler,) = [entry for entry in balance["components"] if entry["component"] == "boiler-Boiler"]
    assert boiler["verdict"] == "closes" and boiler["throughput_kwh"] > 0

    kpis = json.loads((boiler_run / "all_kpis.json").read_text(encoding="utf-8"))["BUI1"]
    metered = kpis["Gas Meter"]["Gas consumption (gas-Meter)"]
    burned = kpis["General"]["Boiler fuel (boiler-Boiler)"]
    assert metered["value"] == pytest.approx(burned["value"]) and burned["value"] > 0
    assert burned["source"]["display_name"] == "Gas boiler, 4000 W"
    assert kpis["General"]["Battery capacity (battery-Battery)"]["value"] == pytest.approx(5.0)

    imports = realized(boiler_run)["metadata"]["imports"]
    assert [circuit["circuit"] for circuit in imports["circuits"]] == ["dhw"]
    assert imports["carriers"][0]["consumers"][0]["outputs"] == ["boiler-Boiler.FuelUse"]
    assert imports["addresses"]["boiler-Boiler"]["display_name"] == "Gas boiler, 4000 W"


@pytest.mark.base
def test_the_boiler_houses_realized_record_re_runs_without_any_assembly(boiler_run: Path, tmp_path: Path) -> None:
    """The lowered feed, circuit wires and sizing line are plain items of the record."""
    code = main(
        [
            "energy-system",
            "run",
            str(boiler_run / "realized.energy_system.yaml"),
            str(Fixtures.ROOT / "one_day_balance.simulation.yaml"),
            "--rerun",
            "--result-dir",
            str(tmp_path),
        ]
    )

    assert code == 0


@pytest.mark.base
def test_re_running_the_realized_record_needs_no_assembly_and_reproduces_it(house_run: Path, tmp_path: Path) -> None:
    """The record's address table admits its expanded names; ``--rerun`` verifies the reproduction."""
    code = main(
        [
            "energy-system",
            "run",
            str(house_run / "realized.energy_system.yaml"),
            str(Fixtures.PARAMETERS),
            "--rerun",
            "--result-dir",
            str(tmp_path),
        ]
    )

    assert code == 0
    first, second = realized(house_run), realized(tmp_path)
    for record in (first, second):
        for key in ("source_energy_system", "source_simulation_parameters"):
            record["metadata"].pop(key)
    assert first == second


@pytest.mark.base
def test_describe_prints_an_assemblys_interface_parameters_and_test_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """``hisim energy-system describe <family>/<name>``: ports with partners and states, parameters, contract."""
    monkeypatch.setenv(AssemblyResolver.ENVIRONMENT_VARIABLE, str(Fixtures.LIBRARY))
    out = io.StringIO()
    with redirect_stdout(out):
        code = main(["energy-system", "describe", "generator/electric_heater"])
    text = " ".join(out.getvalue().split())

    assert code == 0
    for expected in (
        "generator/electric_heater — A fake electric heater and its thermostat.",
        "tank_temperature        need from FakeTank into Controller; required, active when with_thermostat in [True]",
        "ems_modifier            need from FakeEms into Controller; optional (bind:, optional-bind: or none:), active "
        "when",
        "heat                    provides Heater.ThermalPower; provided",
        "power_in_watt           float  WATT  range [500, 6000]  default 2000 — Rated power.",
        "control                 enum  values standard, eco  default 'standard' — The thermostat's preset.",
        "eco                     control='eco', set_temperature_in_celsius=40",
        "variant thermostat, selected by with_thermostat:",
        "fitted when [True]: Controller",
        "test contract: 2 bounds, 1 monotone, 0 expect",
        "monotone  power_in_watt rises: Heater energy of Heater increasing",
        "set_temperature_in_celsius  float  CELSIUS",
    ):
        assert " ".join(expected.split()) in text, expected


@pytest.mark.base
def test_describe_reads_an_assembly_file_by_its_path() -> None:
    """A ``*.assembly.yaml`` argument is read directly."""
    out = io.StringIO()
    with redirect_stdout(out):
        code = main(["energy-system", "describe", str(Fixtures.LIBRARY / "pv" / "array.assembly.yaml")])

    assert code == 0
    assert "bounds    PVSystem.ElectricityOutput [WATT]: 0 … 20000" in out.getvalue()
    assert "at_most_one_of: [power_in_watt, share_of_roof]" in out.getvalue()


@pytest.mark.base
def test_describe_prints_circuit_carrier_and_fact_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    """A provision with its meter, a carrier need with its outputs, a circuit end with its outputs, a fact need."""
    monkeypatch.setenv(AssemblyResolver.ENVIRONMENT_VARIABLE, str(Fixtures.LIBRARY))
    out = io.StringIO()
    with redirect_stdout(out):
        for assembly in ("supply/gas_connection", "heating/gas_boiler", "storage/battery", "pv/array"):
            assert main(["energy-system", "describe", assembly]) == 0
    text = " ".join(out.getvalue().split())

    for expected in (
        "connection provides carrier natural_gas, metered by Meter; provided",
        "fuel needs carrier natural_gas for Boiler.FuelUse; required",
        "dhw circuit dhw at Boiler (MassFlowDhw, SupplyTemperatureDhw, ReturnTemperatureDhw); optional",
        "pv_power needs fact pv_peak_power_in_watt into Battery; required",
        "peak_power provides fact pv_peak_power_in_watt from PVSystem; provided",
    ):
        assert expected in text, expected
