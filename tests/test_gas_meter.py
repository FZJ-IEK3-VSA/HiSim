"""Test for electricity meter."""

# clean

import os
import json
from typing import Optional
import pytest
import numpy as np
import hisim.simulator as sim
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_utsp_connector
from hisim.components import weather
from hisim.components import (
    building,
    electricity_meter,
    gas_meter,
    generic_gas_heater,
    controller_l1_generic_gas_heater,
    generic_heat_source,
    simple_hot_water_storage,
    heat_distribution_system,
    generic_pv_system,
)
from hisim import utils, loadtypes

from hisim.postprocessingoptions import PostProcessingOptions
from hisim import log


# PATH and FUNC needed to build simulator, PATH is fake
PATH = "../system_setups/household_for_test_electricity_meter.py"


@utils.measure_execution_time
@pytest.mark.base
def test_house(
    my_simulation_parameters: Optional[SimulationParameters] = None,
) -> None:  # noqa: too-many-statements
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
        my_simulation_parameters = SimulationParameters.full_year(year=year, seconds_per_timestep=seconds_per_timestep)

        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.EXPORT_TO_CSV)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_OPEX)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_KPIS_AND_WRITE_TO_REPORT)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.WRITE_ALL_KPIS_TO_JSON)
        my_simulation_parameters.logging_level = 4

    # this part is copied from hisim_main
    # Build Simulator
    normalized_path = os.path.normpath(PATH)
    path_in_list = normalized_path.split(os.sep)
    if len(path_in_list) >= 1:
        path_to_be_added = os.path.join(os.getcwd(), *path_in_list[:-1])

    my_sim: sim.Simulator = sim.Simulator(
        module_directory=path_to_be_added,
        my_simulation_parameters=my_simulation_parameters,
        module_filename="household_for_test_electricity_meter",
    )
    my_sim.set_simulation_parameters(my_simulation_parameters)
    # Set some parameters
    heating_reference_temperature_in_celsius: float = -7.0

    # Build Weather
    my_weather_config = weather.WeatherConfig.get_default(location_entry=weather.LocationEnum.AACHEN)
    my_weather = weather.Weather(config=my_weather_config, my_simulation_parameters=my_simulation_parameters)

    # Build PV
    my_photovoltaic_system_config = generic_pv_system.PVSystemConfig.get_scaled_pv_system(
        share_of_maximum_pv_potential=1, rooftop_area_in_m2=120
    )
    my_photovoltaic_system = generic_pv_system.PVSystem(
        config=my_photovoltaic_system_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Building
    my_building_config = building.BuildingConfig.get_default_german_single_family_home(
        heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius
    )
    my_building = building.Building(config=my_building_config, my_simulation_parameters=my_simulation_parameters)
    my_building_information = building.BuildingInformation(config=my_building_config)

    # Occupancy
    my_occupancy_config = loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig.get_default_utsp_connector_config()
    my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
        config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters
    )

    # Build Heat Distribution Controller
    my_heat_distribution_controller_config = heat_distribution_system.HeatDistributionControllerConfig.get_default_heat_distribution_controller_config(
        set_heating_temperature_for_building_in_celsius=my_building_information.set_heating_temperature_for_building_in_celsius,
        set_cooling_temperature_for_building_in_celsius=my_building_information.set_cooling_temperature_for_building_in_celsius,
        heating_load_of_building_in_watt=my_building_information.max_thermal_building_demand_in_watt,
        heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
    )

    my_heat_distribution_controller = heat_distribution_system.HeatDistributionController(
        my_simulation_parameters=my_simulation_parameters,
        config=my_heat_distribution_controller_config,
    )
    my_hds_controller_information = heat_distribution_system.HeatDistributionControllerInformation(
        config=my_heat_distribution_controller_config
    )
    # Build Heat Distribution System
    my_heat_distribution_system_config = (
        heat_distribution_system.HeatDistributionConfig.get_default_heatdistributionsystem_config(
            water_mass_flow_rate_in_kg_per_second=my_hds_controller_information.water_mass_flow_rate_in_kp_per_second,
            absolute_conditioned_floor_area_in_m2=my_building_information.scaled_conditioned_floor_area_in_m2,
        )
    )
    my_heat_distribution_system = heat_distribution_system.HeatDistribution(
        config=my_heat_distribution_system_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Water Storage
    my_simple_heat_water_storage_config = simple_hot_water_storage.SimpleHotWaterStorageConfig.get_scaled_hot_water_storage(
        max_thermal_power_in_watt_of_heating_system=my_building_information.max_thermal_building_demand_in_watt,
        temperature_difference_between_flow_and_return_in_celsius=my_hds_controller_information.temperature_difference_between_flow_and_return_in_celsius,
        sizing_option=simple_hot_water_storage.HotWaterStorageSizingEnum.SIZE_ACCORDING_TO_HEAT_PUMP,
    )
    my_simple_hot_water_storage = simple_hot_water_storage.SimpleHotWaterStorage(
        config=my_simple_heat_water_storage_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Gas Heater Controller
    my_gas_heater_controller_config = controller_l1_generic_gas_heater.GenericGasHeaterControllerL1Config.get_scaled_generic_gas_heater_controller_config(
        heating_load_of_building_in_watt=my_building_information.max_thermal_building_demand_in_watt
    )
    my_gas_heater_controller = controller_l1_generic_gas_heater.GenericGasHeaterControllerL1(
        my_simulation_parameters=my_simulation_parameters,
        config=my_gas_heater_controller_config,
    )

    # Build Gas heater
    my_gas_heater_config = generic_gas_heater.GenericGasHeaterConfig.get_scaled_gasheater_config(
        heating_load_of_building_in_watt=my_building_information.max_thermal_building_demand_in_watt
    )
    my_gas_heater = generic_gas_heater.GasHeater(
        config=my_gas_heater_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Electricity Meter
    my_electricity_meter = electricity_meter.ElectricityMeter(
        my_simulation_parameters=my_simulation_parameters,
        config=electricity_meter.ElectricityMeterConfig.get_electricity_meter_default_config(),
    )

    # Build Gas Meter
    my_gas_meter = gas_meter.GasMeter(
        my_simulation_parameters=my_simulation_parameters,
        config=gas_meter.GasMeterConfig.get_gas_meter_default_config(),
    )

    # =========================================================================================================================================================
    # Add Components to Simulator and run all timesteps

    my_sim.add_component(my_weather)
    my_sim.add_component(my_photovoltaic_system, connect_automatically=True)
    my_sim.add_component(my_occupancy)
    my_sim.add_component(my_building, connect_automatically=True)
    my_sim.add_component(my_heat_distribution_controller, connect_automatically=True)
    my_sim.add_component(my_heat_distribution_system, connect_automatically=True)
    my_sim.add_component(my_simple_hot_water_storage, connect_automatically=True)
    my_sim.add_component(my_gas_heater_controller, connect_automatically=True)
    my_sim.add_component(my_gas_heater, connect_automatically=True)

    my_sim.add_component(my_electricity_meter, connect_automatically=True)
    my_sim.add_component(my_gas_meter, connect_automatically=True)

    my_sim.run_all_timesteps()

    # =========================================================================================================================================================
    # Compare with kpi computation results

    # # read kpi data
    # with open(os.path.join(my_sim._simulation_parameters.result_directory, "all_kpis.json"), "r", encoding="utf-8") as file:  # pylint: disable=W0212
    #     jsondata = json.load(file)

    # cumulative_consumption_kpi_in_kilowatt_hour = jsondata["General"]["Total electricity consumption"].get("value")

    # cumulative_production_kpi_in_kilowatt_hour = jsondata["General"]["Total electricity production"].get("value")

    # electricity_from_grid_kpi_in_kilowatt_hour = jsondata["General"]["Total energy from grid"].get("value")

    # # simualtion results from grid energy balancer (last entry)
    # simulation_results_electricity_meter_cumulative_production_in_watt_hour = (
    #     my_sim.results_data_frame[
    #         "ElectricityMeter - CumulativeProduction [Electricity - Wh]"
    #     ][-1]
    # )
    # simulation_results_electricity_meter_cumulative_consumption_in_watt_hour = (
    #     my_sim.results_data_frame[
    #         "ElectricityMeter - CumulativeConsumption [Electricity - Wh]"
    #     ][-1]
    # )
    # simulation_results_electricity_from_grid_in_watt_hour = (
    #     my_sim.results_data_frame[
    #         "ElectricityMeter - ElectricityFromGrid [Electricity - Wh]"
    #     ]
    # )
    # simulation_results_electricity_consumption_in_watt_hour = (
    #     my_sim.results_data_frame[
    #         "ElectricityMeter - ElectricityConsumption [Electricity - Wh]"
    #     ]
    # )
    # sum_electricity_from_grid_in_kilowatt_hour = sum(simulation_results_electricity_from_grid_in_watt_hour) / 1000
    # sum_electricity_consumption_in_kilowatt_hour = sum(simulation_results_electricity_consumption_in_watt_hour) / 1000

    # log.information(
    #     "kpi cumulative production [kWh] "
    #     + str(cumulative_production_kpi_in_kilowatt_hour)
    # )
    # log.information(
    #     "kpi cumulative consumption [kWh] "
    #     + str(cumulative_consumption_kpi_in_kilowatt_hour)
    # )
    # log.information(
    #     "kpi energy from grid [kWh] "
    #     + str(electricity_from_grid_kpi_in_kilowatt_hour)
    # )
    # log.information(
    #     "ElectricityMeter cumulative production [kWh] "
    #     + str(
    #         simulation_results_electricity_meter_cumulative_production_in_watt_hour
    #         * 1e-3
    #     )
    # )
    # log.information(
    #     "ElectricityMeter cumulative consumption [kWh] "
    #     + str(
    #         simulation_results_electricity_meter_cumulative_consumption_in_watt_hour
    #         * 1e-3
    #     )
    # )
    # log.information(
    #     "ElectricityMeter energy from grid [kWh] "
    #     + str(
    #         sum_electricity_from_grid_in_kilowatt_hour
    #     )
    # )
    # log.information(
    #     "ElectricityMeter consumption [kWh] "
    #     + str(
    #         sum_electricity_consumption_in_kilowatt_hour
    #     )
    # )

    # # test and compare with relative error of 10%
    # np.testing.assert_allclose(
    #     cumulative_production_kpi_in_kilowatt_hour,
    #     simulation_results_electricity_meter_cumulative_production_in_watt_hour * 1e-3,
    #     rtol=0.1,
    # )

    # np.testing.assert_allclose(
    #     cumulative_consumption_kpi_in_kilowatt_hour,
    #     simulation_results_electricity_meter_cumulative_consumption_in_watt_hour * 1e-3,
    #     rtol=0.1,
    # )

    # np.testing.assert_allclose(
    #     electricity_from_grid_kpi_in_kilowatt_hour,
    #     sum_electricity_from_grid_in_kilowatt_hour,
    #     rtol=0.1,
    # )
