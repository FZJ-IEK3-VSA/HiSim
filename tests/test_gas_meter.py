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
    controller_l1_heatpump,
    generic_hot_water_storage_modular,
    simple_hot_water_storage,
    heat_distribution_system,
    generic_pv_system,
)
from hisim import utils, loadtypes

from hisim.postprocessingoptions import PostProcessingOptions
from hisim import log


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
        my_simulation_parameters = SimulationParameters.full_year(year=year, seconds_per_timestep=seconds_per_timestep)

        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.EXPORT_TO_CSV)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_KPIS)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.WRITE_KPIS_TO_JSON)
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
        module_filename="household_for_test_gas_meter",
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

    # Build Gas heater For Space Heating
    my_gas_heater_config = generic_gas_heater.GenericGasHeaterConfig.get_scaled_gasheater_config(
        heating_load_of_building_in_watt=my_building_information.max_thermal_building_demand_in_watt
    )
    my_gas_heater = generic_gas_heater.GasHeater(
        config=my_gas_heater_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    # Build Gas Heater for DHW
    my_gas_heater_for_dhw_config = generic_heat_source.HeatSourceConfig.get_default_config_waterheating_with_gas(
        max_warm_water_demand_in_liter=my_occupancy.max_hot_water_demand,
        scaling_factor_according_to_number_of_apartments=my_occupancy.scaling_factor_according_to_number_of_apartments,
        seconds_per_timestep=seconds_per_timestep,
    )

    my_gas_heater_controller_l1_config = (
        controller_l1_heatpump.L1HeatPumpConfig.get_default_config_heat_source_controller_dhw(
            "DHW" + loadtypes.HeatingSystems.GAS_HEATING.value
        )
    )

    my_boiler_config = (
        generic_hot_water_storage_modular.StorageConfig.get_scaled_config_for_boiler_to_number_of_apartments(
            number_of_apartments=my_building_information.number_of_apartments
        )
    )
    my_boiler_config.compute_default_cycle(
        temperature_difference_in_kelvin=my_gas_heater_controller_l1_config.t_max_heating_in_celsius
        - my_gas_heater_controller_l1_config.t_min_heating_in_celsius
    )

    my_boiler_for_dhw = generic_hot_water_storage_modular.HotWaterStorage(
        my_simulation_parameters=my_simulation_parameters, config=my_boiler_config
    )

    my_heater_controller_l1_for_dhw = controller_l1_heatpump.L1HeatPumpController(
        my_simulation_parameters=my_simulation_parameters, config=my_gas_heater_controller_l1_config
    )

    my_gas_heater_for_dhw = generic_heat_source.HeatSource(
        config=my_gas_heater_for_dhw_config, my_simulation_parameters=my_simulation_parameters
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
    my_sim.add_component(my_gas_heater_for_dhw, connect_automatically=True)
    my_sim.add_component(my_boiler_for_dhw, connect_automatically=True)
    my_sim.add_component(my_heater_controller_l1_for_dhw, connect_automatically=True)

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

    gas_consumption_in_kilowatt_hour = jsondata["Gas Meter"]["Total gas demand from grid"].get("value")
    gas_consumption_for_space_heating_in_kilowatt_hour = jsondata["Gas Heater For Space Heating"][
        "Gas consumption for space heating"
    ].get("value")
    gas_consumption_for_domestic_hot_water_in_kilowatt_hour = jsondata["Gas Heater For Domestic Hot Water"][
        "Gas consumption for domestic hot water"
    ].get("value")

    opex_costs_for_gas_in_euro = jsondata["Gas Meter"]["Opex costs of gas consumption from grid"].get("value")

    co2_footprint_due_to_gas_use_in_kg = jsondata["Gas Meter"]["CO2 footprint of gas consumption from grid"].get("value")

    log.information(
        "Gas consumption for space heating [kWh] " + str(gas_consumption_for_space_heating_in_kilowatt_hour)
    )
    log.information(
        "Gas consumption for domestic hot water [kWh] " + str(gas_consumption_for_domestic_hot_water_in_kilowatt_hour)
    )
    log.information("Total gas consumption measured by gas meter [kWh] " + str(gas_consumption_in_kilowatt_hour))
    log.information("Opex costs for total gas consumption [€] " + str(opex_costs_for_gas_in_euro))
    log.information("CO2 footprint for total gas consumption [kg] " + str(co2_footprint_due_to_gas_use_in_kg))

    # test and compare with relative error of 5%
    np.testing.assert_allclose(
        gas_consumption_in_kilowatt_hour,
        gas_consumption_for_domestic_hot_water_in_kilowatt_hour + gas_consumption_for_space_heating_in_kilowatt_hour,
        rtol=0.05,
    )
