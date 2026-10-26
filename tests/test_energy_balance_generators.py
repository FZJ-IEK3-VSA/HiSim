"""The heat generators state their energy flows and close their balance (hisim-energy-sankeys-9uoo.4).

Every run checks the declared balances and fails when one does not close, so a run that finishes is one whose
generators close; ``EXPORT_ENERGY_BALANCE`` writes the report the tests read. Every check but the fuel-carrier
table's (``base``) runs a whole household, so those run in the ``extendedbase2`` shard: a winter day at 3600 s of
the gas, heat-pump, district-heating and electric-heating households for the ports, the first three weeks of January
of the solar-thermal one, in which its pump runs, the second January week at 900 s of each recorded generator
household, and the same week of the gas household for the boiler's combustion loss. Each generator declares what it
takes (fuel, electricity, district heat, ambient heat, solar), the heat it gives per use (space heating, hot water)
and what it loses; the Sankey links its fuel and electricity to the meter that reads them, which the carrier's supply
node or the grid feeds (owner decision Q-J; never to a controller, which is exempt), and the generator to its
storages, its loss nodes and the environment. The heat a generator gives to a storage or a distribution system is
the heat of its water circuit, a fluid loop both members tag on their channels (hydronic stage E,
hisim-hydronics-fxix.6): the buffer and the hot-water tank are declared nodes, and the distribution system without a
buffer (district heating) is undeclared until hisim-pnw7.
"""

import json
from pathlib import Path
from typing import Any, Dict, Sequence, Set, Tuple

import pytest

from hisim import loadtypes as lt
from hisim.energy_port import EnergyPort

ROOT = Path(__file__).resolve().parents[1]

#: Per setup: the generator, the carriers it takes in, the carriers it gives out, and links its Sankey must show.
#: The fuel and the electricity come through the meter that reads them: the electricity meter reads every electricity
#: port by its role (spec §4.3, §4.4, §5.2); a loss passes its own loss node on its way outdoors.
GENERATORS: Dict[str, Tuple[str, Set[str], Set[str], Set[Tuple[str, str, str]]]] = {
    "household_gas_building_sizer": (
        "CondensingGasBoiler",
        {"natural_gas"},
        {"space_heating_heat", "domestic_hot_water_heat"},
        {
            ("natural_gas", "natural gas supply", "GasMeter"),
            ("natural_gas", "GasMeter", "CondensingGasBoiler"),
            ("natural_gas", "CondensingGasBoiler", "CondensingGasBoiler · CombustionHeatLoss"),
            ("natural_gas", "CondensingGasBoiler · CombustionHeatLoss", "outdoors"),
            ("space_heating_heat", "CondensingGasBoiler", "SimpleHotWaterStorage"),
            ("domestic_hot_water_heat", "CondensingGasBoiler", "DHWStorage"),
        },
    ),
    "household_heatpump_building_sizer": (
        "MoreAdvancedHeatPumpHPLib",
        {"electricity", "ambient_heat"},
        {"space_heating_heat", "domestic_hot_water_heat"},
        {
            ("ambient_heat", "ambient heat source", "MoreAdvancedHeatPumpHPLib"),
            ("electricity", "ElectricityMeter", "MoreAdvancedHeatPumpHPLib"),
            ("electricity", "grid import", "ElectricityMeter"),
            ("space_heating_heat", "MoreAdvancedHeatPumpHPLib", "SimpleHotWaterStorage"),
            ("domestic_hot_water_heat", "MoreAdvancedHeatPumpHPLib", "DHWStorage"),
        },
    ),
    "household_district_heating_building_sizer": (
        "DistrictHeating",
        {"district_heat"},
        {"space_heating_heat", "domestic_hot_water_heat"},
        {
            ("district_heat", "district heat supply", "FuelMeter"),
            ("district_heat", "FuelMeter", "DistrictHeating"),
            ("domestic_hot_water_heat", "DistrictHeating", "DHWStorage"),
            ("space_heating_heat", "DistrictHeating", "undeclared: HeatDistributionSystem"),
        },
    ),
    "household_electric_heating_building_sizer": (
        "ElectricHeating",
        {"electricity"},
        {"space_heating_heat", "domestic_hot_water_heat"},
        {
            ("electricity", "ElectricityMeter", "ElectricHeating"),
            ("space_heating_heat", "ElectricHeating", "Building"),
            ("domestic_hot_water_heat", "ElectricHeating", "DHWStorage"),
        },
    ),
    "household_gas_solar_thermal_building_sizer": (
        "SolarThermalSystem",
        # the second January week is too dull for the pump to run: the collector loses all its solar
        {"solar"},
        set(),
        {
            ("solar", "sun", "SolarThermalSystem"),
            ("solar", "SolarThermalSystem", "SolarThermalSystem · CollectorHeatLoss"),
            ("solar", "SolarThermalSystem · CollectorHeatLoss", "outdoors"),
        },
    ),
}


def run_household(
    directory: Path,
    setup: str = "household_gas_building_sizer",
    start: str = "2021-01-10",
    end: str = "2021-01-17",
    seconds_per_timestep: int = 900,
    predefined_profile: bool = True,
    post_processing_options: Sequence[str] = ("EXPORT_ENERGY_BALANCE",),
) -> Path:
    """Run a recorded household from ``start`` to ``end`` (midnight to midnight) and return its results.

    The defaults are the second January week of 2021 at 900 s, offline, writing the balance report and the Sankeys
    only: the occupancy reads a predefined profile instead of running the LoadProfileGenerator. A household with a car
    keeps its local LoadProfileGenerator run (``predefined_profile=False``), because only that run publishes the car's
    driving profile.
    """
    from hisim.energy_system.executor import run_energy_system  # pylint: disable=import-outside-toplevel

    twin = (ROOT / "energy_systems" / f"{setup}.energy_system.yaml").read_text(encoding="utf-8")
    energy_system = directory / "household.energy_system.yaml"
    if predefined_profile:
        twin = twin.replace("USE_LOCAL_LPG", "USE_PREDEFINED_PROFILE")
    energy_system.write_text(twin, encoding="utf-8")
    parameters = directory / "period.simulation.yaml"
    parameters.write_text(
        f"start_date: '{start}T00:00:00'\n"
        f"end_date: '{end}T00:00:00'\n"
        f"seconds_per_timestep: {seconds_per_timestep}\n"
        "country: DE\n"
        "logging_level: 3\n"
        f"post_processing_options: [{', '.join(post_processing_options)}]\n",
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
    """Every link of every carrier's view of the Sankey graph as (carrier, source, target)."""
    diagrams: Dict[str, Any] = json.loads((results / "energy_sankeys" / "energy_sankey.json").read_text("utf-8"))
    links = set()
    for carrier, diagram in diagrams.items():
        if carrier == "overall":
            continue
        names = [node["name"] for node in diagram["nodes"]]
        links |= {(carrier, names[link["source"]], names[link["target"]]) for link in diagram["links"]}
    return links


#: Per fast setup: the generator and its ports as ``output -> (role, carrier)``; a circuit is named by its mass flow.
FAST_PORTS: Dict[str, Tuple[str, Dict[str, Tuple[str, str]]]] = {
    "household_gas_building_sizer": (
        "CondensingGasBoiler",
        {
            "EnergyDemandSh": ("consumption", "natural_gas"),
            "EnergyDemandDhw": ("consumption", "natural_gas"),
            "circuit CondensingGasBoiler.WaterOutputMassFlowSh": ("production", "space_heating_heat"),
            "circuit CondensingGasBoiler.WaterOutputMassFlowDhw": ("production", "domestic_hot_water_heat"),
            "CombustionHeatLoss": ("loss", "natural_gas"),
        },
    ),
    "household_heatpump_building_sizer": (
        "MoreAdvancedHeatPumpHPLib",
        {
            "ElectricalInputPowerSH": ("consumption", "electricity"),
            "ElectricalInputPowerDHW": ("consumption", "electricity"),
            "ElectricalInputPowerForCooling": ("consumption", "electricity"),
            "ElectricalInputPowerBrinePump": ("consumption", "electricity"),
            "ThermalPowerInputFromEnvironment": ("consumption", "ambient_heat"),
            "circuit MoreAdvancedHeatPumpHPLib.MassFlowOutputSH": ("production", "space_heating_heat"),
            "circuit MoreAdvancedHeatPumpHPLib.MassFlowOutputDHW": ("production", "domestic_hot_water_heat"),
        },
    ),
    # The district heat comes in on the two metered energy outputs, not on DistrictHeatDrawn, their unread sum. The
    # space-heating circuit runs at the distribution system's flow, which the substation reads.
    "household_district_heating_building_sizer": (
        "DistrictHeating",
        {
            "ThermalOutputShEnergy": ("consumption", "district_heat"),
            "ThermalOutputDhwEnergy": ("consumption", "district_heat"),
            "circuit HeatDistributionSystem.WaterMassFlowHDS": ("production", "space_heating_heat"),
            "circuit DistrictHeating.WaterOutputDhwMassFlowRate": ("production", "domestic_hot_water_heat"),
        },
    ),
    "household_electric_heating_building_sizer": (
        "ElectricHeating",
        {
            "ElectricOutputShPower": ("consumption", "electricity"),
            "ElectricOutputDhwPower": ("consumption", "electricity"),
            "ThermalOutputShPower": ("production", "space_heating_heat"),
            "circuit ElectricHeating.WaterOutputDhwMassFlowRate": ("production", "domestic_hot_water_heat"),
        },
    ),
}


@pytest.mark.extendedbase2
@pytest.mark.parametrize("setup", sorted(FAST_PORTS))
def test_a_winter_day_runs_and_the_generator_declares_its_ports(setup: str, tmp_path: Path) -> None:
    """One winter day at 3600 s: the run finishing proves the generator closes; its ports are as declared."""
    generator, expected_ports = FAST_PORTS[setup]
    results = run_household(tmp_path, setup, start="2021-01-11", end="2021-01-12", seconds_per_timestep=3600)

    entry = balance_entry(results, generator)
    assert entry["verdict"] == "closes" and entry["worst_step"] is None
    assert port_roles(entry) == expected_ports
    assert entry["consumption_kwh"] > 1.0


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


@pytest.mark.extendedbase2
@pytest.mark.parametrize("setup", sorted(GENERATORS))
def test_the_generator_closes_and_links_its_carriers(setup: str, tmp_path: Path) -> None:
    """The run passes; the generator closes, takes and gives its carriers, and is linked.

    It closes to a millionth of what it takes in: its heat is its circuits' heat, derived from the converged flow
    and temperatures both ends read, while the generator booked its fuel or electricity on its last pass, which may
    have read a return temperature the other end moved by less than the simulator's convergence tolerance.
    """
    generator, carriers_in, carriers_out, expected_links = GENERATORS[setup]
    results = run_household(tmp_path, setup)

    entry = balance_entry(results, generator)
    assert entry["verdict"] == "closes"
    assert abs(entry["residual_kwh"]) <= 1e-6 * entry["consumption_kwh"]
    assert entry["consumption_kwh"] > 1.0
    for carrier in carriers_in:
        assert entry["carriers"][carrier]["consumption_kwh"] > 0.0, carrier
    for carrier in carriers_out:
        assert entry["carriers"][carrier]["production_kwh"] > 0.0, carrier
    links = sankey_links(results)
    assert expected_links <= links
    report: Dict[str, Any] = json.loads((results / "balance_report.json").read_text(encoding="utf-8"))
    exempt = {item["component"] for item in report["exempt"]}
    assert exempt, "the controllers of the household are exempt"
    drawn = {node for _, source, target in links for node in (source, target)}
    assert not {name for name in drawn if name.removeprefix("undeclared: ") in exempt}
    if setup == "household_gas_building_sizer":
        # the boiler is the gas meter's one consumer and declares its fuel: the meter publishes what it reads
        meters = {meter["meter"]: meter for meter in report["meters"]}
        assert meters["GasMeter"]["verdict"] == "agrees", meters["GasMeter"]


@pytest.mark.extendedbase2
def test_a_solar_collector_whose_pump_runs_delivers_heat_and_closes(tmp_path: Path) -> None:
    """The pump runs on 20 January: the collector delivers hot-water heat and its pump's electricity is a loss.

    Not a summer day: a window that does not start on 1 January still reads the weather from 1 January
    (hisim-midyear-windows-9g2), so a June day is a January day in disguise and its pump stands. The first three weeks of January
    at 900 s hold the year's first day on which the pump delivers more than 0.5 kWh (20 January; the pump stands
    whenever the boiler has charged the tank above 60 °C).
    """
    results = run_household(
        tmp_path, "household_gas_solar_thermal_building_sizer", start="2021-01-01", end="2021-01-21"
    )
    entry = balance_entry(results, "SolarThermalSystem")
    assert entry["verdict"] == "closes" and abs(entry["residual_kwh"]) <= 1e-9
    carriers = entry["carriers"]
    heat_out = carriers["domestic_hot_water_heat"]["production_kwh"]
    assert heat_out > 0.5
    assert carriers["solar"]["consumption_kwh"] == pytest.approx(heat_out + carriers["solar"]["loss_kwh"], rel=1e-9)
    assert carriers["electricity"]["consumption_kwh"] > 0.0
    assert carriers["electricity"]["loss_kwh"] == pytest.approx(carriers["electricity"]["consumption_kwh"])
    links = sankey_links(results)
    assert ("domestic_hot_water_heat", "SolarThermalSystem") in {(carrier, source) for carrier, source, _ in links}
    # the meter reads the pump's re-pointed electricity port directly, by its role (spec §5.2)
    assert ("electricity", "ElectricityMeter", "SolarThermalSystem") in links


@pytest.mark.extendedbase2
def test_a_boiler_loses_what_its_efficiency_does_not_convert(tmp_path: Path) -> None:
    """The combustion loss is the billed fuel times one minus the efficiency: 2 to 13 % of the gas in a week.

    The current condensing gas curve runs between 0.877 (full load, 60 °C return) and 0.978 (30 % load, 30 °C
    return) on the gross calorific value, so no step loses more than 12.3 % or less than 2.2 % of its gas.
    """
    results = run_household(tmp_path)
    boiler = balance_entry(results, "CondensingGasBoiler")
    gas = boiler["carriers"]["natural_gas"]
    assert 0.02 * gas["consumption_kwh"] < gas["loss_kwh"] < 0.13 * gas["consumption_kwh"]
    # the heat out is its circuits' heat from the converged values, within the convergence tolerance of its booking
    assert boiler["production_kwh"] == pytest.approx(gas["consumption_kwh"] - gas["loss_kwh"], rel=1e-9)
