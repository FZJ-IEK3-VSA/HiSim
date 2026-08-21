"""Test for electricity meter."""

# clean

import json
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import pytest

import hisim.simulator as sim
from hisim.components import loadprofilegenerator_utsp_connector
from hisim.components import weather
from hisim.components import (
    building,
    electricity_meter,
    generic_pv_system,
    idealized_electric_heater,
)
from hisim import utils, loadtypes
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.simulator import SimulationParameters
from hisim import log


# PATH and FUNC needed to build simulator, PATH is fake
PATH: str = "../system_setups/household_for_test_electricity_meter.py"


def _build_system(
    my_simulation_parameters: SimulationParameters,
) -> tuple[sim.Simulator, electricity_meter.ElectricityMeter, pd.DataFrame]:
    """Construct, wire, register, and run the electricity-meter test system.

    Owns component construction, predefined and manual connections, component
    registration on the simulator, and the full timestep simulation.
    """
    # this part is copied from hisim_main
    # Build Simulator
    path_to_be_added = str(Path(PATH).resolve().parent)

    my_sim: sim.Simulator = sim.Simulator(
        module_directory=path_to_be_added,
        my_simulation_parameters=my_simulation_parameters,
        module_filename="household_for_test_electricity_meter",
    )
    my_sim.set_simulation_parameters(my_simulation_parameters)

    # Build Weather
    my_weather_config = weather.WeatherConfig.get_default(
        location_entry=weather.LocationEnum.AACHEN
    )
    my_weather = weather.Weather(
        config=my_weather_config, my_simulation_parameters=my_simulation_parameters
    )
    # Build PV
    my_photovoltaic_system_config = (
        generic_pv_system.PVSystemConfig.get_scaled_pv_system(share_of_maximum_pv_potential=1, rooftop_area_in_m2=120)
    )

    my_photovoltaic_system = generic_pv_system.PVSystem(
        config=my_photovoltaic_system_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    # Build Building
    my_building_config = building.BuildingConfig.presets.german_single_family_home
    my_building = building.Building(
        config=my_building_config, my_simulation_parameters=my_simulation_parameters
    )
    # Occupancy
    my_occupancy_config = loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig.get_default_utsp_connector_config()
    my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
        config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters
    )

    # Build Electricity Meter
    my_electricity_meter = electricity_meter.ElectricityMeter(
        my_simulation_parameters=my_simulation_parameters,
        config=electricity_meter.ElectricityMeterConfig.get_electricity_meter_default_config(),
    )

    # Build Fake Heater
    my_idealized_electric_heater = idealized_electric_heater.IdealizedElectricHeater(
        my_simulation_parameters=my_simulation_parameters,
        config=idealized_electric_heater.IdealizedHeaterConfig.get_default_config(),
    )

    # =========================================================================================================================================================
    # Connect Components

    # PV System
    my_photovoltaic_system.connect_only_predefined_connections(my_weather)

    # Building
    my_building.connect_only_predefined_connections(my_weather, my_occupancy)
    my_building.connect_input(
        my_building.ThermalPowerDelivered,
        my_idealized_electric_heater.component_name,
        my_idealized_electric_heater.ThermalPowerDelivered,
    )

    # Idealized Heater
    my_idealized_electric_heater.connect_input(
        my_idealized_electric_heater.TheoreticalThermalBuildingDemand,
        my_building.component_name,
        my_building.TheoreticalThermalBuildingDemand,
    )

    # Electricity Grid

    my_electricity_meter.add_component_input_and_connect(
        source_object_name=my_photovoltaic_system.component_name,
        source_component_output=my_photovoltaic_system.ElectricityOutput,
        source_load_type=loadtypes.LoadTypes.ELECTRICITY,
        source_unit=loadtypes.Units.WATT,
        source_tags=[
            loadtypes.ComponentType.PV,
            loadtypes.InandOutputType.ELECTRICITY_PRODUCTION,
        ],
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

    # =========================================================================================================================================================
    # Add Components to Simulator and run all timesteps

    my_sim.add_component(my_weather)
    my_sim.add_component(my_photovoltaic_system)
    my_sim.add_component(my_occupancy)
    my_sim.add_component(my_building)

    my_sim.add_component(my_idealized_electric_heater)
    my_sim.add_component(my_electricity_meter)

    my_sim.run_all_timesteps()

    return my_sim, my_electricity_meter, my_sim.results_data_frame


def _load_kpi_summary(result_directory: str) -> dict:
    """Load the ``BUI1`` KPI summary dict from the simulation result directory."""
    with open(str(Path(result_directory) / "all_kpis.json"), "r", encoding="utf-8") as file:
        jsondata = json.load(file)

    return jsondata["BUI1"]  # type: ignore[no-any-return]


def _assert_kpis_match_simulation(kpis: dict, results_df: pd.DataFrame) -> None:
    """Compare KPI JSON values against the electricity-meter simulation results.

    Converts the simulation results from Wh to kWh and checks them against the
    KPI summary with a relative tolerance of 10%.
    """
    cumulative_consumption_kpi_in_kilowatt_hour = kpis["General"]["Total electricity consumption"].get("value")
    cumulative_production_kpi_in_kilowatt_hour = kpis["General"]["Total electricity production"].get("value")
    electricity_from_grid_kpi_in_kilowatt_hour = kpis["Electricity Meter"]["Total energy from grid"].get("value")

    # simulation results from grid energy balancer (last entry)
    simulation_results_electricity_meter_cumulative_production_in_watt_hour = (
        results_df["ElectricityMeter - CumulativeProduction [Electricity - Wh]"].iloc[-1]
    )
    simulation_results_electricity_meter_cumulative_consumption_in_watt_hour = (
        results_df["ElectricityMeter - CumulativeConsumption [Electricity - Wh]"].iloc[-1]
    )
    simulation_results_electricity_from_grid_in_watt_hour = (
        results_df["ElectricityMeter - ElectricityFromGrid [Electricity - Wh]"]
    )
    simulation_results_electricity_consumption_in_watt_hour = (
        results_df["ElectricityMeter - ElectricityConsumption [Electricity - Wh]"]
    )
    sum_electricity_from_grid_in_kilowatt_hour = sum(simulation_results_electricity_from_grid_in_watt_hour) / 1000
    sum_electricity_consumption_in_kilowatt_hour = sum(simulation_results_electricity_consumption_in_watt_hour) / 1000

    log.information(f"kpi cumulative production [kWh] {cumulative_production_kpi_in_kilowatt_hour}")
    log.information(f"kpi cumulative consumption [kWh] {cumulative_consumption_kpi_in_kilowatt_hour}")
    log.information(f"kpi energy from grid [kWh] {electricity_from_grid_kpi_in_kilowatt_hour}")
    log.information(
        "ElectricityMeter cumulative production [kWh] "
        f"{simulation_results_electricity_meter_cumulative_production_in_watt_hour * 1e-3}"
    )
    log.information(
        "ElectricityMeter cumulative consumption [kWh] "
        f"{simulation_results_electricity_meter_cumulative_consumption_in_watt_hour * 1e-3}"
    )
    log.information(f"ElectricityMeter energy from grid [kWh] {sum_electricity_from_grid_in_kilowatt_hour}")
    log.information(f"ElectricityMeter consumption [kWh] {sum_electricity_consumption_in_kilowatt_hour}")

    # test and compare with relative error of 10%
    np.testing.assert_allclose(
        cumulative_production_kpi_in_kilowatt_hour,
        simulation_results_electricity_meter_cumulative_production_in_watt_hour * 1e-3,
        rtol=0.1,
    )

    np.testing.assert_allclose(
        cumulative_consumption_kpi_in_kilowatt_hour,
        simulation_results_electricity_meter_cumulative_consumption_in_watt_hour * 1e-3,
        rtol=0.1,
    )

    np.testing.assert_allclose(
        electricity_from_grid_kpi_in_kilowatt_hour,
        sum_electricity_from_grid_in_kilowatt_hour,
        rtol=0.1,
    )


@utils.measure_execution_time
@pytest.mark.extendedbase
def test_house(
    my_simulation_parameters: Optional[SimulationParameters] = None,
) -> None:
    """The test should check if a normal simulation works with the electricity grid implementation."""

    # =========================================================================================================================================================
    # System Parameters

    # Set Simulation Parameters
    year = 2021
    seconds_per_timestep = 60 * 60

    # =========================================================================================================================================================
    # Build Components

    # Build Simulation Parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.one_day_only(
            year=year, seconds_per_timestep=seconds_per_timestep
        )

        my_simulation_parameters.post_processing_options.append(
            PostProcessingOptions.EXPORT_TO_CSV
        )
        my_simulation_parameters.post_processing_options.append(
            PostProcessingOptions.COMPUTE_KPIS
        )
        my_simulation_parameters.post_processing_options.append(
            PostProcessingOptions.WRITE_KPIS_TO_JSON
        )

    my_sim, _, results_df = _build_system(my_simulation_parameters)

    kpis = _load_kpi_summary(my_sim._simulation_parameters.result_directory)  # pylint: disable=W0212

    _assert_kpis_match_simulation(kpis, results_df)
