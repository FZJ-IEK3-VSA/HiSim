"""Example sets up a modular household according to json input file."""

from typing import Optional, List

import component_connections
import hisim.log
import hisim.loadtypes as lt
from hisim.simulationparameters import SystemConfig
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_connector
from hisim.components import generic_price_signal
from hisim.components import weather
from hisim.components import building
from hisim.components import controller_l2_energy_management_system


def modular_household_explicit(my_sim, my_simulation_parameters: Optional[SimulationParameters] = None):
    """Setup function emulates an household including the basic components.

    The configuration of the household is read in via the json input file "system_config.json".
    """

    # Set simulation parameters
    year = 2018
    seconds_per_timestep = 60 * 15

    # path of system config file
    system_config_filename = "system_config.json"

    count = 1  # initialize source_weight with one
    production: List = []  # initialize list of components involved in production
    consumption: List = []  # initialize list of components involved in consumption
    heater: List = []  # initialize list of components used for heating

    # Build system parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.january_only(year=year, seconds_per_timestep=seconds_per_timestep)
        my_simulation_parameters.enable_all_options()

    # try to read the system config from file
    with open(system_config_filename) as system_config_file:
        system_config = SystemConfig.from_json(system_config_file.read())  # type: ignore
    if not system_config:
        my_simulation_parameters.reset_system_config(
            location=lt.Locations.AACHEN, occupancy_profile=lt.OccupancyProfiles.CH01, building_code=lt.BuildingCodes.DE_N_SFH_05_GEN_REEX_001_002,
            predictive=True, prediction_horizon=24 * 3600, pv_included=True, pv_peak_power=10e3, smart_devices_included=True,
            water_heating_system_installed=lt.HeatingSystems.HEAT_PUMP, heating_system_installed=lt.HeatingSystems.HEAT_PUMP, buffer_included=True,
            buffer_volume=500, battery_included=True, battery_capacity=10e3, chp_included=True, chp_power=10e3, h2_storage_size=100,
            electrolyzer_power=5e3, current_mobility=lt.Cars.NO_CAR, mobility_distance=lt.MobilityDistance.RURAL)
    else:
        hisim.log.information(f"Read system config from {system_config_filename}")
        my_simulation_parameters.system_config = system_config

    my_sim.set_simulation_parameters(my_simulation_parameters)

    # get system configuration
    location = my_simulation_parameters.system_config.location
    occupancy_profile = my_simulation_parameters.system_config.occupancy_profile
    building_code = my_simulation_parameters.system_config.building_code
    pv_included = my_simulation_parameters.system_config.pv_included  # True or False
    if pv_included:
        pv_peak_power = my_simulation_parameters.system_config.pv_peak_power
    smart_devices_included = my_simulation_parameters.system_config.smart_devices_included  # True or False
    water_heating_system_installed = my_simulation_parameters.system_config.water_heating_system_installed  # Electricity, Hydrogen or False
    heating_system_installed = my_simulation_parameters.system_config.heating_system_installed
    buffer_included = my_simulation_parameters.system_config.buffer_included
    if buffer_included:
        buffer_volume = my_simulation_parameters.system_config.buffer_volume
    battery_included = my_simulation_parameters.system_config.battery_included
    if battery_included:
        battery_capacity = my_simulation_parameters.system_config.battery_capacity
    chp_included = my_simulation_parameters.system_config.chp_included
    if chp_included:
        chp_power = my_simulation_parameters.system_config.chp_power
        h2_storage_size = my_simulation_parameters.system_config.h2_storage_size
        electrolyzer_power = my_simulation_parameters.system_config.electrolyzer_power

    """BASICS"""
    # Build occupancy
    my_occupancy_config = loadprofilegenerator_connector.OccupancyConfig(profile_name=occupancy_profile.value)
    my_occupancy = loadprofilegenerator_connector.Occupancy(config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters)
    my_sim.add_component(my_occupancy)
    consumption.append(my_occupancy)

    # Build Weather
    my_weather_config = weather.WeatherConfig(location=location.value)
    my_weather = weather.Weather(config=my_weather_config, my_simulation_parameters=my_simulation_parameters,
                                 my_simulation_repository=my_sim.simulation_repository)
    my_sim.add_component(my_weather)

    # Build building
    my_building_config = building.Building.get_default_config()
    my_building_config.building_code = building_code.value
    my_building = building.Building(config=my_building_config, my_simulation_parameters=my_simulation_parameters)
    my_building.connect_only_predefined_connections(my_weather, my_occupancy)
    my_sim.add_component(my_building)

    # add price signal
    my_price_signal = generic_price_signal.PriceSignal(my_simulation_parameters=my_simulation_parameters)
    my_sim.add_component(my_price_signal)

    """PV"""
    if pv_included:
        production, count = component_connections.configure_pv_system(
            my_sim=my_sim, my_simulation_parameters=my_simulation_parameters, my_weather=my_weather, production=production,
            pv_peak_power=pv_peak_power, count=count)
        production, count = component_connections.configure_pv_system(
            my_sim=my_sim, my_simulation_parameters=my_simulation_parameters, my_weather=my_weather, production=production,
            pv_peak_power=pv_peak_power, count=count)

    """SMART DEVICES"""
    my_smart_devices, consumption, count = component_connections.configure_smart_devices(
        my_sim=my_sim, my_simulation_parameters=my_simulation_parameters,
        consumption=consumption, count=count)

    """SURPLUS CONTROLLER"""
    if battery_included or chp_included or heating_system_installed in [lt.HeatingSystems.HEAT_PUMP, lt.HeatingSystems.ELECTRIC_HEATING] \
            or water_heating_system_installed in [lt.HeatingSystems.HEAT_PUMP, lt.HeatingSystems.ELECTRIC_HEATING]:
        my_electricity_controller = controller_l2_energy_management_system.ControllerElectricityGeneric(
            my_simulation_parameters=my_simulation_parameters)

        my_electricity_controller.add_component_inputs_and_connect(source_component_classes=consumption,
                                                                   outputstring='ElectricityOutput',
                                                                   source_load_type=lt.LoadTypes.ELECTRICITY,
                                                                   source_unit=lt.Units.WATT,
                                                                   source_tags=[lt.InandOutputType.CONSUMPTION],
                                                                   source_weight=999)
        my_electricity_controller.add_component_inputs_and_connect(source_component_classes=production,
                                                                   outputstring='ElectricityOutput',
                                                                   source_load_type=lt.LoadTypes.ELECTRICITY,
                                                                   source_unit=lt.Units.WATT,
                                                                   source_tags=[lt.InandOutputType.PRODUCTION],
                                                                   source_weight=999)

    """WATERHEATING"""
    count = component_connections.configure_water_heating(
        my_sim=my_sim, my_simulation_parameters=my_simulation_parameters, my_occupancy=my_occupancy,
        my_electricity_controller=my_electricity_controller, my_weather=my_weather,
        water_heating_system_installed=water_heating_system_installed, count=count)

    """HEATING"""
    if buffer_included:
        my_heater, my_buffer, count = component_connections.configure_heating_with_buffer(
            my_sim=my_sim, my_simulation_parameters=my_simulation_parameters, my_building=my_building,
            my_electricity_controller=my_electricity_controller, my_weather=my_weather, heating_system_installed=heating_system_installed,
            buffer_volume=buffer_volume, count=count)
    else:
        my_heater, count = component_connections.configure_heating(
            my_sim=my_sim, my_simulation_parameters=my_simulation_parameters, my_building=my_building,
            my_electricity_controller=my_electricity_controller, my_weather=my_weather, heating_system_installed=heating_system_installed,
            count=count)
    heater.append(my_heater)

    """BATTERY"""
    if battery_included:
        count = component_connections.configure_battery(
            my_sim=my_sim, my_simulation_parameters=my_simulation_parameters, my_electricity_controller=my_electricity_controller,
            battery_capacity=battery_capacity, count=count)

    """CHP + H2 STORAGE + ELECTROLYSIS"""
    if chp_included:
        my_chp, count = component_connections.configure_elctrolysis_h2storage_chp_system(
            my_sim=my_sim, my_simulation_parameters=my_simulation_parameters, my_building=my_building,
            my_electricity_controller=my_electricity_controller, chp_power=chp_power, h2_storage_size=h2_storage_size,
            electrolyzer_power=electrolyzer_power, count=count)
        heater.append(my_chp)

        if buffer_included:
            my_buffer.add_component_inputs_and_connect(source_component_classes=heater, outputstring='ThermalPowerDelivered',
                                                       source_load_type=lt.LoadTypes.HEATING, source_unit=lt.Units.WATT,
                                                       source_tags=[lt.InandOutputType.HEAT_TO_BUFFER], source_weight=999)
        else:
            my_building.add_component_inputs_and_connect(source_component_classes=heater, outputstring='ThermalPowerDelivered',
                                                         source_load_type=lt.LoadTypes.HEATING, source_unit=lt.Units.WATT,
                                                         source_tags=[lt.InandOutputType.HEAT_TO_BUILDING], source_weight=999)

    if battery_included or chp_included or heating_system_installed in [lt.HeatingSystems.HEAT_PUMP, lt.HeatingSystems.ELECTRIC_HEATING] \
            or water_heating_system_installed in [lt.HeatingSystems.HEAT_PUMP, lt.HeatingSystems.ELECTRIC_HEATING]:
        my_sim.add_component(my_electricity_controller)

    """PREDICTIVE CONTROLLER FOR SMART DEVICES"""
    # use predictive controller if smart devices are included and do not use it if it is false
    if smart_devices_included:
        my_simulation_parameters.system_config.predictive = True
        component_connections.configure_smart_controller_for_smart_devices(
            my_sim=my_sim, my_simulation_parameters=my_simulation_parameters, my_smart_devices=my_smart_devices)
    else:
        my_simulation_parameters.system_config.predictive = False
