"""Test for electricity meter."""

# clean

import os
import json
from typing import Optional
import pytest
# import numpy as np
import hisim.simulator as sim
from hisim.simulator import SimulationParameters
from hisim.components import hot_water_storage_modular, loadprofilegenerator_utsp_connector
from hisim.components import weather
from hisim.components import (
    building,
    electricity_meter,
    heating_meter,
    more_advanced_heat_pump_hplib,
    simple_water_storage,
    heat_distribution_system,
)
from hisim import utils

from hisim.postprocessingoptions import PostProcessingOptions
from hisim import log
from hisim.components import pv_system


# PATH and FUNC needed to build simulator, PATH is fake
PATH = "../system_setups/household_for_test_gas_meter.py"


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
        my_simulation_parameters = SimulationParameters.one_day_only(
            year=year, seconds_per_timestep=seconds_per_timestep
        )

        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.EXPORT_TO_CSV)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_KPIS)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.WRITE_KPIS_TO_JSON)
        my_simulation_parameters.logging_level = 3

    # this part is copied from hisim_main
    # Build Simulator
    normalized_path = os.path.normpath(PATH)
    path_in_list = normalized_path.split(os.sep)
    if len(path_in_list) >= 1:
        path_to_be_added = os.path.join(os.getcwd(), *path_in_list[:-1])

    my_sim: sim.Simulator = sim.Simulator(
        module_directory=path_to_be_added,
        my_simulation_parameters=my_simulation_parameters,
        module_filename="household_for_test_heating_meter",
    )
    my_sim.set_simulation_parameters(my_simulation_parameters)
    # Set some parameters
    heating_reference_temperature_in_celsius: float = -7.0

    # Build Weather
    my_weather_config = weather.WeatherConfig.get_default(location_entry=weather.LocationEnum.AACHEN)
    my_weather = weather.Weather(config=my_weather_config, my_simulation_parameters=my_simulation_parameters)

    # Build PV
    my_photovoltaic_system_config = pv_system.PVSystemConfig.get_scaled_pv_system(
        share_of_maximum_pv_potential=1, rooftop_area_in_m2=120
    )
    my_photovoltaic_system = pv_system.PVSystem(
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
    my_simple_heat_water_storage_config = simple_water_storage.SimpleHotWaterStorageConfig.get_scaled_hot_water_storage(
        max_thermal_power_in_watt_of_heating_system=my_building_information.max_thermal_building_demand_in_watt,
        temperature_difference_between_flow_and_return_in_celsius=my_hds_controller_information.temperature_difference_between_flow_and_return_in_celsius,
        sizing_option=simple_water_storage.HotWaterStorageSizingEnum.SIZE_ACCORDING_TO_HEAT_PUMP,
    )
    my_simple_hot_water_storage = simple_water_storage.SimpleHotWaterStorage(
        config=my_simple_heat_water_storage_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Pump Controller for space heating
    my_heatpump_controller_sh_config = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig.get_default_space_heating_controller_config(
        heat_distribution_system_type=my_hds_controller_information.heat_distribution_system_type
    )

    my_heatpump_controller_space_heating = (
        more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibControllerSpaceHeating(
            config=my_heatpump_controller_sh_config, my_simulation_parameters=my_simulation_parameters
        )
    )

    # Build Heat Pump Controller for dhw
    my_heatpump_controller_dhw_config = (
        more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibControllerDHWConfig.get_default_dhw_controller_config()
    )

    my_heatpump_controller_dhw = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibControllerDHW(
        config=my_heatpump_controller_dhw_config, my_simulation_parameters=my_simulation_parameters
    )

    # Build Heat Pump
    my_heatpump_config = (
        more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibConfig.get_default_generic_advanced_hp_lib()
    )
    my_heatpump_config.with_domestic_hot_water_preparation = True

    my_heatpump = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLib(
        config=my_heatpump_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build DHW Storage

    my_dhw_storage_config = hot_water_storage_modular.StorageConfig.get_default_config_for_boiler()

    my_dhw_storage = hot_water_storage_modular.HotWaterStorage(
        config=my_dhw_storage_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Electricity Meter
    my_electricity_meter = electricity_meter.ElectricityMeter(
        my_simulation_parameters=my_simulation_parameters,
        config=electricity_meter.ElectricityMeterConfig.get_electricity_meter_default_config(),
    )

    # Build Gas Meter
    my_heating_meter = heating_meter.HeatingMeter(
        my_simulation_parameters=my_simulation_parameters,
        config=heating_meter.HeatingMeterConfig.get_heating_meter_default_config(),
    )

    # =========================================================================================================================================================
    # Connect some components

    my_heatpump.connect_input(
        my_heatpump.TemperatureInputPrimary,
        my_weather.component_name,
        my_weather.DailyAverageOutsideTemperatures,
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
    my_sim.add_component(my_heatpump_controller_dhw, connect_automatically=True)
    my_sim.add_component(my_heatpump_controller_space_heating, connect_automatically=True)
    my_sim.add_component(my_heatpump, connect_automatically=True)
    my_sim.add_component(my_dhw_storage, connect_automatically=True)
    my_sim.add_component(my_electricity_meter, connect_automatically=True)
    my_sim.add_component(my_heating_meter, connect_automatically=True)

    my_sim.run_all_timesteps()

    # =========================================================================================================================================================
    # Compare with kpi computation results

    # read kpi data
    with open(
        os.path.join(my_sim._simulation_parameters.result_directory, "all_kpis.json"),  # pylint: disable=W0212
        "r",
        encoding="utf-8",
    ) as file:
        jsondata = json.load(file)

    jsondata = jsondata["BUI1"]

    heat_consumption_in_kilowatt_hour = jsondata["Heating Meter"]["Total heat consumption from grid"].get("value")

    heat_consumption_for_space_heating_in_kilowatt_hour = jsondata["Heat Distribution System"][
        "Thermal output energy of heat distribution system"
    ].get("value")
    # heat_consumption_for_domestic_hot_water_in_kilowatt_hour = jsondata["Residents"][
    #     "Residents' total thermal dhw consumption"
    # ].get("value")

    opex_costs_for_heat_in_euro = jsondata["Heating Meter"]["Opex costs of heat consumption from grid"].get("value")

    co2_footprint_due_to_heat_use_in_kg = jsondata["Heating Meter"][
        "CO2 footprint of heat consumption from grid"
    ].get("value")

    log.information(
        "Heat consumption for space heating [kWh] " + str(heat_consumption_for_space_heating_in_kilowatt_hour)
    )
    # log.information(
    #     "Heat consumption for domestic hot water [kWh] " + str(heat_consumption_for_domestic_hot_water_in_kilowatt_hour)
    # )
    log.information("Total heat consumption measured by heating meter [kWh] " + str(heat_consumption_in_kilowatt_hour))
    log.information("Opex costs for total gas consumption [€] " + str(opex_costs_for_heat_in_euro))
    log.information("CO2 footprint for total heat consumption [kg] " + str(co2_footprint_due_to_heat_use_in_kg))

    # test and compare with relative error of 5%
    # np.testing.assert_allclose(
    #     heat_consumption_in_kilowatt_hour,
    #     heat_consumption_for_space_heating_in_kilowatt_hour + heat_consumption_for_domestic_hot_water_in_kilowatt_hour,
    #     rtol=0.05,
    # )
