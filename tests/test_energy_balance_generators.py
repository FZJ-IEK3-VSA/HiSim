"""The heat generators state their energy flows and close their balance (hisim-9uoo.4).

A winter week of each recorded generator household at 900 s, with ``EXPORT_ENERGY_BALANCE`` in strict mode: the
run fails if a declared balance does not close. Each generator declares what it takes (fuel, electricity,
district heat, ambient heat, solar), the heat it gives per use (space heating, hot water) and what it loses; the
Sankey links it to its meter, its storages and the environment. The storages and the heat distribution system are
undeclared until they declare their ports with the hydronic coupling (hisim-fxix.6).
"""

import json
from pathlib import Path
from typing import Any, Dict, Set, Tuple

import pytest

from hisim.postprocessing.energy_balance import MODE_VARIABLE

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
        # the first January week is too dull for the pump to run: the collector loses all its solar
        {"solar"},
        set(),
        {
            ("solar", "solar", "SolarThermalSystem"),
            ("solar", "SolarThermalSystem", "outdoors"),
        },
    ),
}


def run_winter_week(directory: Path, setup: str = "household_gas_building_sizer") -> Path:
    """Run a recorded household offline for the second January week of 2021 and return its result directory."""
    from hisim.energy_system.executor import run_energy_system  # pylint: disable=import-outside-toplevel

    twin = (ROOT / "energy_systems" / f"{setup}.energy_system.yaml").read_text(encoding="utf-8")
    energy_system = directory / "household.energy_system.yaml"
    energy_system.write_text(twin.replace("USE_LOCAL_LPG", "USE_PREDEFINED_PROFILE"), encoding="utf-8")
    parameters = directory / "winter_week.simulation.yaml"
    parameters.write_text(
        "start_date: '2021-01-10T00:00:00'\n"
        "end_date: '2021-01-17T00:00:00'\n"
        "seconds_per_timestep: 900\n"
        "country: DE\n"
        "logging_level: 3\n"
        "post_processing_options: [EXPORT_ENERGY_BALANCE]\n",
        encoding="utf-8",
    )
    results = directory / "results"
    run_energy_system(energy_system, parameters, result_directory=str(results))
    return results


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


@pytest.mark.system_setups
@pytest.mark.parametrize("setup", sorted(GENERATORS))
def test_the_generator_closes_and_links_its_carriers(setup: str, tmp_path: Path, monkeypatch: Any) -> None:
    """Strict mode passes; the generator closes to 1e-9 kWh, takes and gives its carriers, and is linked."""
    monkeypatch.setenv(MODE_VARIABLE, "strict")
    generator, carriers_in, carriers_out, expected_links = GENERATORS[setup]
    results = run_winter_week(tmp_path, setup)

    report = json.loads((results / "balance_report.json").read_text(encoding="utf-8"))
    assert report["verdict"] == "closes" and report["mode"] == "strict"
    entry = {component["component"]: component for component in report["components"]}[generator]
    assert entry["verdict"] == "closes"
    assert abs(entry["residual_kwh"]) <= 1e-9
    assert entry["in_kwh"] > 1.0
    for carrier in carriers_in:
        assert entry["carriers"][carrier]["in_kwh"] > 0.0, carrier
    for carrier in carriers_out:
        assert entry["carriers"][carrier]["out_kwh"] > 0.0, carrier
    assert expected_links <= sankey_links(results)


@pytest.mark.system_setups
def test_a_boiler_loses_what_its_efficiency_does_not_convert(tmp_path: Path) -> None:
    """The combustion loss is the billed fuel times one minus the efficiency: 10 to 40 % of the gas in a week."""
    results = run_winter_week(tmp_path)
    report = json.loads((results / "balance_report.json").read_text(encoding="utf-8"))
    boiler = {component["component"]: component for component in report["components"]}["CondensingGasBoiler"]
    gas = boiler["carriers"]["natural_gas"]
    assert 0.1 * gas["in_kwh"] < gas["loss_kwh"] < 0.4 * gas["in_kwh"]
    assert boiler["out_kwh"] == pytest.approx(gas["in_kwh"] - gas["loss_kwh"], rel=1e-12)
