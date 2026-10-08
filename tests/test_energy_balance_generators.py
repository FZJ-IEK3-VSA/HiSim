"""The heat generators state their energy flows and close their balance (hisim-9uoo.4).

Every run checks the declared balances and fails when one does not close, so a run that finishes is one whose
generators close; ``EXPORT_ENERGY_BALANCE`` writes the report the tests read. A winter day at 3600 s of the gas
and the heat-pump household and the first half of January of the solar-thermal one, in which its pump runs, are
the fast ``base`` checks of the ports; the second January week at 900 s of each recorded generator household is
the ``system_setups`` run. Each generator declares what it takes (fuel, electricity,
district heat, ambient heat, solar), the heat it gives per use (space heating, hot water) and what it loses; the
Sankey links it to its meter, its storages and the environment. The storages and the heat distribution system are
undeclared until they declare their ports with the hydronic coupling (hisim-fxix.6).
"""

import json
from pathlib import Path
from typing import Any, Dict, Set, Tuple

import pytest

from hisim import loadtypes as lt
from hisim.energy_port import EnergyPort

ROOT = Path(__file__).resolve().parents[1]

#: Per setup: the generator, the carriers it takes in, the carriers it gives out, and links its Sankey must show.
GENERATORS: Dict[str, Tuple[str, Set[str], Set[str], Set[Tuple[str, str, str]]]] = {
    "household_gas_building_sizer": (
        "CondensingGasBoiler",
        {"natural_gas"},
        {"space_heating_heat", "domestic_hot_water_heat"},
        {
            ("natural_gas", "undeclared: GasMeter", "CondensingGasBoiler"),
            ("natural_gas", "CondensingGasBoiler", "outdoors"),
            ("space_heating_heat", "CondensingGasBoiler", "undeclared: SimpleHotWaterStorage"),
            ("domestic_hot_water_heat", "CondensingGasBoiler", "undeclared: DHWStorage"),
        },
    ),
    "household_heatpump_building_sizer": (
        "MoreAdvancedHeatPumpHPLib",
        {"electricity", "ambient_heat"},
        {"space_heating_heat", "domestic_hot_water_heat"},
        {
            ("ambient_heat", "ambient heat", "MoreAdvancedHeatPumpHPLib"),
            ("electricity", "undeclared: L2EMSElectricityController", "MoreAdvancedHeatPumpHPLib"),
            ("space_heating_heat", "MoreAdvancedHeatPumpHPLib", "undeclared: SimpleHotWaterStorage"),
            ("domestic_hot_water_heat", "MoreAdvancedHeatPumpHPLib", "undeclared: DHWStorage"),
        },
    ),
    "household_district_heating_building_sizer": (
        "DistrictHeating",
        {"district_heat"},
        {"space_heating_heat", "domestic_hot_water_heat"},
        {
            ("district_heat", "undeclared: FuelMeter", "DistrictHeating"),
            ("domestic_hot_water_heat", "DistrictHeating", "undeclared: DHWStorage"),
            ("space_heating_heat", "DistrictHeating", "undeclared: HeatDistributionSystem"),
        },
    ),
    "household_electric_heating_building_sizer": (
        "ElectricHeating",
        {"electricity"},
        {"space_heating_heat", "domestic_hot_water_heat"},
        {
            ("space_heating_heat", "ElectricHeating", "undeclared: Building"),
            ("domestic_hot_water_heat", "ElectricHeating", "undeclared: DHWStorage"),
        },
    ),
    "household_gas_solar_thermal_building_sizer": (
        "SolarThermalSystem",
        # the second January week is too dull for the pump to run: the collector loses all its solar
        {"solar"},
        set(),
        {
            ("solar", "solar", "SolarThermalSystem"),
            ("solar", "SolarThermalSystem", "outdoors"),
        },
    ),
}


def run_household(
    directory: Path,
    setup: str = "household_gas_building_sizer",
    start: str = "2021-01-10",
    end: str = "2021-01-17",
    seconds_per_timestep: int = 900,
) -> Path:
    """Run a recorded household offline from ``start`` to ``end`` (midnight to midnight) and return its results.

    The defaults are the second January week of 2021 at 900 s.
    """
    from hisim.energy_system.executor import run_energy_system  # pylint: disable=import-outside-toplevel

    twin = (ROOT / "energy_systems" / f"{setup}.energy_system.yaml").read_text(encoding="utf-8")
    energy_system = directory / "household.energy_system.yaml"
    energy_system.write_text(twin.replace("USE_LOCAL_LPG", "USE_PREDEFINED_PROFILE"), encoding="utf-8")
    parameters = directory / "period.simulation.yaml"
    parameters.write_text(
        f"start_date: '{start}T00:00:00'\n"
        f"end_date: '{end}T00:00:00'\n"
        f"seconds_per_timestep: {seconds_per_timestep}\n"
        "country: DE\n"
        "logging_level: 3\n"
        "post_processing_options: [EXPORT_ENERGY_BALANCE]\n",
        encoding="utf-8",
    )
    results = directory / "results"
    run_energy_system(energy_system, parameters, result_directory=str(results))
    return results


def balance_entry(results: Path, component: str) -> Dict[str, Any]:
    """One component's entry of the run's balance report."""
    report: Dict[str, Any] = json.loads((results / "balance_report.json").read_text(encoding="utf-8"))
    assert report["verdict"] == "closes"
    entries: Dict[str, Dict[str, Any]] = {entry["component"]: entry for entry in report["components"]}
    return entries[component]


def port_roles(entry: Dict[str, Any]) -> Dict[str, Tuple[str, str]]:
    """A component's declared ports as ``output -> (role, carrier)``."""
    return {port["output"]: (port["role"], port["carrier"]) for port in entry["ports"]}


def sankey_links(results: Path) -> Set[Tuple[str, str, str]]:
    """Every link of every carrier's diagram as (carrier, source, target)."""
    diagrams: Dict[str, Any] = json.loads((results / "energy_sankeys" / "energy_sankey.json").read_text("utf-8"))
    links = set()
    for carrier, diagram in diagrams.items():
        if carrier == "overall":
            continue
        names = [node["name"] for node in diagram["nodes"]]
        links |= {(carrier, names[link["source"]], names[link["target"]]) for link in diagram["links"]}
    return links


#: Per fast setup: the generator and its ports as ``output -> (role, carrier)``.
FAST_PORTS: Dict[str, Tuple[str, Dict[str, Tuple[str, str]]]] = {
    "household_gas_building_sizer": (
        "CondensingGasBoiler",
        {
            "EnergyDemandSh": ("in", "natural_gas"),
            "EnergyDemandDhw": ("in", "natural_gas"),
            "ThermalOutputEnergySh": ("out", "space_heating_heat"),
            "ThermalOutputEnergyDhw": ("out", "domestic_hot_water_heat"),
            "CombustionHeatLoss": ("loss", "natural_gas"),
        },
    ),
    "household_heatpump_building_sizer": (
        "MoreAdvancedHeatPumpHPLib",
        {
            "ElectricalInputPowerSH": ("in", "electricity"),
            "ElectricalInputPowerDHW": ("in", "electricity"),
            "ElectricalInputPowerForCooling": ("in", "electricity"),
            "ElectricalInputPowerBrinePump": ("in", "electricity"),
            "ThermalPowerInputFromEnvironment": ("in", "ambient_heat"),
            "ThermalPowerDeliveredForSpaceHeating": ("out", "space_heating_heat"),
            "ThermalPowerDrawnForCooling": ("in", "cooling"),
            "ThermalOutputPowerDHW": ("out", "domestic_hot_water_heat"),
        },
    ),
}


@pytest.mark.base
@pytest.mark.parametrize("setup", sorted(FAST_PORTS))
def test_a_winter_day_runs_and_the_generator_declares_its_ports(setup: str, tmp_path: Path) -> None:
    """One winter day at 3600 s: the run finishing proves the generator closes; its ports are as declared."""
    generator, expected_ports = FAST_PORTS[setup]
    results = run_household(tmp_path, setup, start="2021-01-11", end="2021-01-12", seconds_per_timestep=3600)

    entry = balance_entry(results, generator)
    assert entry["verdict"] == "closes" and entry["worst_step"] is None
    assert port_roles(entry) == expected_ports
    assert entry["in_kwh"] > 1.0


@pytest.mark.base
def test_every_fuel_has_its_balance_carrier_and_the_table_is_read_only() -> None:
    """Each load type a generator burns maps to its carrier; anything else is refused; the table cannot change."""
    assert dict(EnergyPort.FUEL_CARRIERS) == {
        lt.LoadTypes.GAS: lt.EnergyBalanceCarrier.NATURAL_GAS,
        lt.LoadTypes.OIL: lt.EnergyBalanceCarrier.HEATING_OIL,
        lt.LoadTypes.PELLETS: lt.EnergyBalanceCarrier.PELLETS,
        lt.LoadTypes.WOOD_CHIPS: lt.EnergyBalanceCarrier.WOOD_CHIPS,
        lt.LoadTypes.GREEN_HYDROGEN: lt.EnergyBalanceCarrier.HYDROGEN,
        lt.LoadTypes.DIESEL: lt.EnergyBalanceCarrier.DIESEL,
        lt.LoadTypes.DISTRICTHEATING: lt.EnergyBalanceCarrier.DISTRICT_HEAT,
        lt.LoadTypes.ELECTRICITY: lt.EnergyBalanceCarrier.ELECTRICITY,
    }
    for load_type, carrier in EnergyPort.FUEL_CARRIERS.items():
        assert EnergyPort.carrier_of_fuel(load_type) is carrier
    with pytest.raises(ValueError, match="not a fuel with an energy-balance carrier"):
        EnergyPort.carrier_of_fuel(lt.LoadTypes.HEATING)
    with pytest.raises(TypeError):
        EnergyPort.FUEL_CARRIERS[lt.LoadTypes.HEATING] = lt.EnergyBalanceCarrier.SOLAR  # type: ignore[index]


@pytest.mark.system_setups
@pytest.mark.parametrize("setup", sorted(GENERATORS))
def test_the_generator_closes_and_links_its_carriers(setup: str, tmp_path: Path) -> None:
    """The run passes; the generator closes to 1e-9 kWh, takes and gives its carriers, and is linked."""
    generator, carriers_in, carriers_out, expected_links = GENERATORS[setup]
    results = run_household(tmp_path, setup)

    entry = balance_entry(results, generator)
    assert entry["verdict"] == "closes"
    assert abs(entry["residual_kwh"]) <= 1e-9
    assert entry["in_kwh"] > 1.0
    for carrier in carriers_in:
        assert entry["carriers"][carrier]["in_kwh"] > 0.0, carrier
    for carrier in carriers_out:
        assert entry["carriers"][carrier]["out_kwh"] > 0.0, carrier
    assert expected_links <= sankey_links(results)


@pytest.mark.base
def test_a_solar_collector_whose_pump_runs_delivers_heat_and_closes(tmp_path: Path) -> None:
    """The pump runs on 20 January: the collector delivers hot-water heat and its pump's electricity is a loss.

    Not a summer day: a window that does not start on 1 January still reads the weather from 1 January
    (hisim-9g2), so a June day is a January day in disguise and its pump stands. The first three weeks of January
    at 900 s hold the year's first day on which the pump delivers more than 0.5 kWh (20 January; the pump stands
    whenever the boiler has charged the tank above 60 °C).
    """
    results = run_household(
        tmp_path, "household_gas_solar_thermal_building_sizer", start="2021-01-01", end="2021-01-21"
    )
    entry = balance_entry(results, "SolarThermalSystem")
    assert entry["verdict"] == "closes" and abs(entry["residual_kwh"]) <= 1e-9
    carriers = entry["carriers"]
    heat_out = carriers["domestic_hot_water_heat"]["out_kwh"]
    assert heat_out > 0.5
    assert carriers["solar"]["in_kwh"] == pytest.approx(heat_out + carriers["solar"]["loss_kwh"], rel=1e-9)
    assert carriers["electricity"]["in_kwh"] > 0.0
    assert carriers["electricity"]["loss_kwh"] == pytest.approx(carriers["electricity"]["in_kwh"])
    assert ("domestic_hot_water_heat", "SolarThermalSystem") in {
        (carrier, source) for carrier, source, _ in sankey_links(results)
    }


@pytest.mark.system_setups
def test_a_boiler_loses_what_its_efficiency_does_not_convert(tmp_path: Path) -> None:
    """The combustion loss is the billed fuel times one minus the efficiency: 10 to 40 % of the gas in a week."""
    results = run_household(tmp_path)
    boiler = balance_entry(results, "CondensingGasBoiler")
    gas = boiler["carriers"]["natural_gas"]
    assert 0.1 * gas["in_kwh"] < gas["loss_kwh"] < 0.4 * gas["in_kwh"]
    assert boiler["out_kwh"] == pytest.approx(gas["in_kwh"] - gas["loss_kwh"], rel=1e-12)
