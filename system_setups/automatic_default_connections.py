"""Household with automatic default connections."""

# clean

from typing import Optional, Any
from hisim.simulator import SimulationParameters

from hisim.components import (
    building,
    generic_pv_system,
    weather,
    advanced_heat_pump_hplib,
    loadprofilegenerator_utsp_connector,
    electricity_meter,
    simple_water_storage,
    heat_distribution_system,
    generic_heat_pump_modular,
    controller_l1_heatpump,
    generic_hot_water_storage_modular,
)
from hisim.units import Quantity, Celsius, Watt


def setup_function(my_sim: Any, my_simulation_parameters: Optional[SimulationParameters] = None) -> Any:
    """The setup function emulates an household including the basic components.

    Here the residents have their electricity and heating needs covered
    by the photovoltaic system and the heat pump.

    - Simulation Parameters
    - Components
        - Occupancy (Residents' Demands)
        - Weather
        - Photovoltaic System
        - Building
        - Heat Pump
        - Heat Pump Controller
        - Heat Distribution System
        - Heat Distribution Controller
        - Heat Water Storage

    """

    # ==== System Parameters ====

    # Set simulation parameters
    year = 2021
    seconds_per_timestep = 60

    # ==== Build Components ====

    # Build system parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year_all_options(
            year=year, seconds_per_timestep=seconds_per_timestep
        )
    my_sim.set_simulation_parameters(my_simulation_parameters)

    # =================================================================================================================================
    # Set Fix System Parameters

    # Set Heat Pump
    heating_reference_temperature_in_celsius: float = -7  # t_in #TODO: get real heating ref temps according to location

    # =================================================================================================================================
    # Build Basic Components

    # Build Building
    my_building_config = building.BuildingConfig.get_default_german_single_family_home(
        heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius
    )

    my_building_information = building.BuildingInformation(config=my_building_config)
    my_building = building.Building(config=my_building_config, my_simulation_parameters=my_simulation_parameters)

    # Build Occupancy
    my_occupancy_config = loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig.get_default_utsp_connector_config()
    my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
        config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters
    )

    # Build Weather
    my_weather_config = weather.WeatherConfig.get_default(location_entry=weather.LocationEnum.AACHEN)

    my_weather = weather.Weather(config=my_weather_config, my_simulation_parameters=my_simulation_parameters)
    # Build PV
    my_photovoltaic_system_config = generic_pv_system.PVSystemConfig.get_scaled_pv_system(
        rooftop_area_in_m2=my_building_information.roof_area_in_m2
    )
    my_photovoltaic_system = generic_pv_system.PVSystem(
        config=my_photovoltaic_system_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    # =================================================================================================================================
    # Build Energy System Components

    # Build Heat Distribution Controller
    my_heat_distribution_controller_config = heat_distribution_system.HeatDistributionControllerConfig.get_default_heat_distribution_controller_config(
        set_cooling_temperature_for_building_in_celsius=my_building_information.set_cooling_temperature_for_building_in_celsius,
        set_heating_temperature_for_building_in_celsius=my_building_information.set_heating_temperature_for_building_in_celsius,
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

    # Build Heat Pump Controller
    my_heat_pump_controller = advanced_heat_pump_hplib.HeatPumpHplibController(
        config=advanced_heat_pump_hplib.HeatPumpHplibControllerL1Config.get_default_generic_heat_pump_controller_config(
            heat_distribution_system_type=my_hds_controller_information.heat_distribution_system_type
        ),
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Pump
    my_heat_pump_config = advanced_heat_pump_hplib.HeatPumpHplibConfig.get_scaled_advanced_hp_lib(
        heating_load_of_building_in_watt=Quantity(my_building_information.max_thermal_building_demand_in_watt, Watt),
        heating_reference_temperature_in_celsius=Quantity(heating_reference_temperature_in_celsius, Celsius),
    )

    my_heat_pump = advanced_heat_pump_hplib.HeatPumpHplib(
        config=my_heat_pump_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Distribution System
    my_heat_distribution_system_config = (
        heat_distribution_system.HeatDistributionConfig.get_default_heatdistributionsystem_config(
            water_mass_flow_rate_in_kg_per_second=my_hds_controller_information.water_mass_flow_rate_in_kp_per_second,
            absolute_conditioned_floor_area_in_m2=my_building_information.scaled_conditioned_floor_area_in_m2,
            heating_system=my_hds_controller_information.hds_controller_config.heating_system,
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

    # Build DHW (this is taken from household_3_advanced_hp_diesel-car_pv_battery.py)
    my_dhw_heatpump_config = generic_heat_pump_modular.HeatPumpConfig.get_scaled_waterheating_to_number_of_apartments(
        number_of_apartments=my_building_information.number_of_apartments
    )

    my_dhw_heatpump_controller_config = (
        controller_l1_heatpump.L1HeatPumpConfig.get_default_config_heat_source_controller_dhw(
            name="DHWHeatpumpController"
        )
    )

    my_dhw_storage_config = (
        generic_hot_water_storage_modular.StorageConfig.get_scaled_config_for_boiler_to_number_of_apartments(
            number_of_apartments=my_building_information.number_of_apartments
        )
    )
    my_dhw_storage_config.compute_default_cycle(
        temperature_difference_in_kelvin=my_dhw_heatpump_controller_config.t_max_heating_in_celsius
        - my_dhw_heatpump_controller_config.t_min_heating_in_celsius
    )

    my_domnestic_hot_water_storage = generic_hot_water_storage_modular.HotWaterStorage(
        my_simulation_parameters=my_simulation_parameters, config=my_dhw_storage_config
    )

    my_domnestic_hot_water_heatpump_controller = controller_l1_heatpump.L1HeatPumpController(
        my_simulation_parameters=my_simulation_parameters,
        config=my_dhw_heatpump_controller_config,
    )

    my_domnestic_hot_water_heatpump = generic_heat_pump_modular.ModularHeatPump(
        config=my_dhw_heatpump_config, my_simulation_parameters=my_simulation_parameters
    )

    # Build Electricity Meter
    my_electricity_meter = electricity_meter.ElectricityMeter(
        my_simulation_parameters=my_simulation_parameters,
        config=electricity_meter.ElectricityMeterConfig.get_electricity_meter_default_config(),
    )

    # =================================================================================================================================
    # Add Components to Simulation Parameters
    my_sim.add_component(my_occupancy)
    my_sim.add_component(my_weather)
    my_sim.add_component(my_photovoltaic_system, connect_automatically=True)
    my_sim.add_component(my_building, connect_automatically=True)
    my_sim.add_component(my_heat_pump, connect_automatically=True)
    my_sim.add_component(my_heat_pump_controller, connect_automatically=True)
    my_sim.add_component(my_heat_distribution_system, connect_automatically=True)
    my_sim.add_component(my_heat_distribution_controller, connect_automatically=True)
    my_sim.add_component(my_simple_hot_water_storage, connect_automatically=True)
    my_sim.add_component(my_domnestic_hot_water_storage, connect_automatically=True)
    my_sim.add_component(my_domnestic_hot_water_heatpump_controller, connect_automatically=True)
    my_sim.add_component(my_domnestic_hot_water_heatpump, connect_automatically=True)
    my_sim.add_component(my_electricity_meter, connect_automatically=True)
