"""End-to-end test: a real simulation with COMPUTE_LIFECYCLE_COSTS on (cost_spec.md §10).

Modeled on tests/test_electricity_meter.py. Verifies the parallel engine runs in shadow mode
next to the legacy COMPUTE_OPEX/COMPUTE_CAPEX path and writes only new files.
"""

# clean

import json
from pathlib import Path

import pytest

import hisim.simulator as sim
from hisim import loadtypes, utils
from hisim.components import (
    building,
    electricity_meter,
    generic_pv_system,
    idealized_electric_heater,
    loadprofilegenerator_utsp_connector,
    weather,
)
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.simulator import SimulationParameters

# PATH needed to build simulator, PATH is fake
PATH: str = "../system_setups/household_for_test_economics_bridge.py"


@utils.measure_execution_time
@pytest.mark.extendedbase
def test_lifecycle_cost_engine_runs_in_shadow_mode() -> None:
    """One-day simulation; the lifecycle engine writes its exports next to the legacy CSVs."""
    my_simulation_parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60 * 60)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_OPEX)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_CAPEX)
    # LIFECYCLE_COST_REPORT implies COMPUTE_LIFECYCLE_COSTS and adds the human-readable reports.
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.LIFECYCLE_COST_REPORT)

    path_to_be_added = str(Path(PATH).resolve().parent)
    my_sim: sim.Simulator = sim.Simulator(
        module_directory=path_to_be_added,
        my_simulation_parameters=my_simulation_parameters,
        module_filename="household_for_test_economics_bridge",
    )
    my_sim.set_simulation_parameters(my_simulation_parameters)

    my_weather = weather.Weather(
        config=weather.WeatherConfig.get_default(location_entry=weather.LocationEnum.AACHEN),
        my_simulation_parameters=my_simulation_parameters,
    )
    my_photovoltaic_system = generic_pv_system.PVSystem(
        config=generic_pv_system.PVSystemConfig.get_scaled_pv_system(
            share_of_maximum_pv_potential=1, rooftop_area_in_m2=120
        ),
        my_simulation_parameters=my_simulation_parameters,
    )
    my_building = building.Building(
        config=building.BuildingConfig.get_default_german_single_family_home(),
        my_simulation_parameters=my_simulation_parameters,
    )
    my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
        config=loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig.get_default_utsp_connector_config(),
        my_simulation_parameters=my_simulation_parameters,
    )
    my_electricity_meter = electricity_meter.ElectricityMeter(
        my_simulation_parameters=my_simulation_parameters,
        config=electricity_meter.ElectricityMeterConfig.get_electricity_meter_default_config(),
    )
    my_idealized_electric_heater = idealized_electric_heater.IdealizedElectricHeater(
        my_simulation_parameters=my_simulation_parameters,
        config=idealized_electric_heater.IdealizedHeaterConfig.get_default_config(),
    )

    my_photovoltaic_system.connect_only_predefined_connections(my_weather)
    my_building.connect_only_predefined_connections(my_weather, my_occupancy)
    my_building.connect_input(
        my_building.ThermalPowerDelivered,
        my_idealized_electric_heater.component_name,
        my_idealized_electric_heater.ThermalPowerDelivered,
    )
    my_idealized_electric_heater.connect_input(
        my_idealized_electric_heater.TheoreticalThermalBuildingDemand,
        my_building.component_name,
        my_building.TheoreticalThermalBuildingDemand,
    )
    my_electricity_meter.add_component_input_and_connect(
        source_object_name=my_photovoltaic_system.component_name,
        source_component_output=my_photovoltaic_system.ElectricityOutput,
        source_load_type=loadtypes.LoadTypes.ELECTRICITY,
        source_unit=loadtypes.Units.WATT,
        source_tags=[loadtypes.ComponentType.PV, loadtypes.InandOutputType.ELECTRICITY_PRODUCTION],
        source_weight=999,
    )
    my_electricity_meter.add_component_input_and_connect(
        source_object_name=my_occupancy.component_name,
        source_component_output=my_occupancy.ElectricalPowerConsumption,
        source_load_type=loadtypes.LoadTypes.ELECTRICITY,
        source_unit=loadtypes.Units.WATT,
        source_tags=[loadtypes.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
        source_weight=999,
    )

    my_sim.add_component(my_weather)
    my_sim.add_component(my_photovoltaic_system)
    my_sim.add_component(my_occupancy)
    my_sim.add_component(my_building)
    my_sim.add_component(my_idealized_electric_heater)
    my_sim.add_component(my_electricity_meter)

    my_sim.run_all_timesteps()

    result_directory = Path(my_sim._simulation_parameters.result_directory)  # pylint: disable=W0212

    # Legacy outputs are still written (the parallel engine never touches them).
    assert (result_directory / "investment_cost_co2_footprint.csv").is_file()
    assert (result_directory / "operational_costs_co2_footprint.csv").is_file()

    # The new engine wrote its exports (§10 rule 3).
    for file_name in (
        "lifecycle_costs.json",
        "component_costs.json",
        "component_costs.csv",
        "cash_flow_timeline.csv",
        "lifecycle_kpis.json",
        "economic_inputs.json",
        "cost_provenance.json",
        "cost_audit.csv",
        "cost_parity_report.csv",
        # LIFECYCLE_COST_REPORT outputs:
        "cost_summary.md",
        "lifecycle_report.html",
        "lifecycle_annual_cash_flows.png",
        "lifecycle_perspective_costs.png",
    ):
        assert (result_directory / file_name).is_file(), f"missing {file_name}"

    with open(result_directory / "lifecycle_costs.json", encoding="utf-8") as file:
        lifecycle = json.load(file)
    assert "greenfield_gross" in lifecycle
    result = lifecycle["greenfield_gross"]

    # Reconciliation invariant (§7.4): subject NPVs sum to the perspective total.
    def average_of(value):
        return value["avg"] if isinstance(value, dict) else value

    subject_sum = sum(average_of(value) for value in result["npv_by_component"].values())
    assert subject_sum == pytest.approx(average_of(result["total_npv_in_euro"]), rel=1e-6)

    # The PV system and the electricity carrier both appear as subjects.
    subjects = set(result["npv_by_component"].keys())
    assert any("PVSystem" in subject for subject in subjects)
    assert "ELECTRICITY" in subjects

    # The KPI file carries namespaced names with uncertainty bands (§7.3).
    with open(result_directory / "lifecycle_kpis.json", encoding="utf-8") as file:
        kpis = json.load(file)["Lifecycle costs"]
    assert any("Equivalent annual cost" in name for name in kpis)
