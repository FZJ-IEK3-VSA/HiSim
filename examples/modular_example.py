"""Example sets up a modular household according to json input file."""

from typing import Optional, List, Any
from pathlib import Path
import os
import json
import hisim.log
import hisim.utils
import hisim.loadtypes as lt
import scipy.interpolate
from hisim.modular_household import component_connections
from hisim.modular_household.modular_household_results import ModularHouseholdResults
from hisim.simulationparameters import SystemConfig
from hisim.simulator import SimulationParameters
from hisim.postprocessingoptions import PostProcessingOptions

from hisim.components import loadprofilegenerator_connector
from hisim.components import generic_price_signal
from hisim.components import weather
from hisim.components import building
from hisim.components import controller_l2_energy_management_system


def modular_household_explicit(my_sim: Any, my_simulation_parameters: Optional[SimulationParameters] = None) -> None:

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
        my_simulation_parameters = SimulationParameters.full_year(year=year, seconds_per_timestep=seconds_per_timestep)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.PLOT_CARPET)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.PLOT_LINE)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.GENERATE_PDF_REPORT)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_KPI)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.MAKE_NETWORK_CHARTS)
        my_simulation_parameters.skip_finished_results = False

    # try to read the system config from file
    if Path(system_config_filename).is_file():
        with open(system_config_filename, encoding='utf8') as system_config_file:
            system_config = SystemConfig.from_json(system_config_file.read())  # type: ignore
        hisim.log.information(f"Read system config from {system_config_filename}")
        my_simulation_parameters.system_config = system_config

    else:
        my_simulation_parameters.reset_system_config(
            location=lt.Locations.AACHEN, occupancy_profile=lt.OccupancyProfiles.CH01, building_code=lt.BuildingCodes.DE_N_SFH_05_GEN_REEX_001_002,
            predictive=False, prediction_horizon=24 * 3600, pv_included=True, pv_peak_power=10e3, smart_devices_included=False,
            water_heating_system_installed=lt.HeatingSystems.HEAT_PUMP, heating_system_installed=lt.HeatingSystems.HEAT_PUMP, buffer_included=True,
            buffer_volume=500, battery_included=False, battery_capacity=10e3, chp_included=False, chp_power=10e3, h2_storage_size=100,
            electrolyzer_power=5e3, current_mobility=lt.Cars.NO_CAR, mobility_distance=lt.MobilityDistance.RURAL)

    my_sim.set_simulation_parameters(my_simulation_parameters)

    # get system configuration
    location = weather.LocationEnum[my_simulation_parameters.system_config.location.value]
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
    my_occupancy_config = loadprofilegenerator_connector.OccupancyConfig(profile_name=occupancy_profile.value, name='Occupancy')
    my_occupancy = loadprofilegenerator_connector.Occupancy(config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters)
    my_sim.add_component(my_occupancy)
    consumption.append(my_occupancy)

    # Build Weather
    my_weather_config = weather.WeatherConfig.get_default(location_entry=location)
    my_weather = weather.Weather(config=my_weather_config, my_simulation_parameters=my_simulation_parameters)
    my_sim.add_component(my_weather)

    # Build building
    my_building_config = building.BuildingConfig.get_default_german_single_family_home()
    my_building_config.building_code = building_code.value
    my_building = building.Building(config=my_building_config, my_simulation_parameters=my_simulation_parameters)
    my_building.connect_only_predefined_connections(my_weather, my_occupancy)
    my_sim.add_component(my_building)

    # add price signal

    if my_simulation_parameters.system_config.predictive:
        my_price_signal = generic_price_signal.PriceSignal(my_simulation_parameters=my_simulation_parameters)
        my_sim.add_component(my_price_signal)

    economic_parameters = json.load(open('..\hisim\modular_household\EconomicParameters.json'))

    """PV"""
    if pv_included:
        production, count = component_connections.configure_pv_system(
            my_sim=my_sim, my_simulation_parameters=my_simulation_parameters, my_weather=my_weather, production=production,
            pv_peak_power=pv_peak_power, count=count)
        production, count = component_connections.configure_pv_system(
            my_sim=my_sim, my_simulation_parameters=my_simulation_parameters, my_weather=my_weather, production=production,
            pv_peak_power=pv_peak_power, count=count)
         
        if economic_parameters["pv_bought"]==True:
            ccpv = json.load(open('..\hisim\modular_household\ComponentCostPV.json'))       
            pv_cost = scipy.interpolate.interp1d(ccpv["capacity_for_cost"], ccpv["cost_per_capacity"])
            pv_cost=pv_cost(pv_peak_power)
            #print("Interpolierter Preis für Kapazität:", pv_cost(pv_peak_power))
        else:
            pv_cost = 0
            
    """SMART DEVICES"""

    if smart_devices_included:
        my_smart_devices, count = component_connections.configure_smart_devices(
            my_sim=my_sim, my_simulation_parameters=my_simulation_parameters, count=count)
    if economic_parameters["smart_devices_bought"]==True:
        ccsd = json.load(open('..\hisim\modular_household\ComponentCostSmartDevices.json'))
        smart_devices_cost = ccsd["smart_devices_cost"]
    else:
        smart_devices_cost = 0

    """SURPLUS CONTROLLER"""
    if battery_included or chp_included or smart_devices_included \
            or heating_system_installed in [lt.HeatingSystems.HEAT_PUMP, lt.HeatingSystems.ELECTRIC_HEATING] \
            or water_heating_system_installed in [lt.HeatingSystems.HEAT_PUMP, lt.HeatingSystems.ELECTRIC_HEATING]:
        my_electricity_controller = controller_l2_energy_management_system.L2GenericEnergyManagementSystem(
            my_simulation_parameters=my_simulation_parameters)

        my_electricity_controller.add_component_inputs_and_connect(source_component_classes=consumption,
                                                                   outputstring='ElectricityOutput',
                                                                   source_load_type=lt.LoadTypes.ELECTRICITY,
                                                                   source_unit=lt.Units.WATT,
                                                                   source_tags=[lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED],
                                                                   source_weight=999)
        my_electricity_controller.add_component_inputs_and_connect(source_component_classes=production,
                                                                   outputstring='ElectricityOutput',
                                                                   source_load_type=lt.LoadTypes.ELECTRICITY,
                                                                   source_unit=lt.Units.WATT,
                                                                   source_tags=[lt.InandOutputType.ELECTRICITY_PRODUCTION],
                                                                   source_weight=999)
    """SMART CONTROLLER FOR SMART DEVICES"""
    # use predictive controller if smart devices are included and do not use it if it is false
    if smart_devices_included:
        component_connections.configure_smart_controller_for_smart_devices(my_electricity_controller=my_electricity_controller, my_smart_devices=my_smart_devices)

    """WATERHEATING"""
    #    count = component_connections.configure_water_heating(
    #       my_sim=my_sim, my_simulation_parameters=my_simulation_parameters, my_occupancy=my_occupancy,
    #      my_electricity_controller=my_electricity_controller, my_weather=my_weather,
    #     water_heating_system_installed=water_heating_system_installed, count=count)

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
        #EconomicParameters.battery_bought abfragen, ob Batterie bereits vorhanden ist
        ccb = json.load(open('..\hisim\modular_household\ComponentCostBattery.json'))
        print("Battery capacity", battery_capacity)
        
        if economic_parameters["battery_bought"]==True:
            battery_cost = scipy.interpolate.interp1d(ccb["capacity_cost"], ccb["cost"])
            print("Interpolierter Preis für Kapazität von 1120:", battery_cost(battery_capacity))
        else:
            print("Battery was alerady in the house")
            battery_cost = 0
            
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
    
    investment_cost=battery_cost_interp(battery_capacity)
    co2_cost=1000    #CO2 von Herstellung der Komponenten plus CO2 für den Stromverbrauch der Komponenten
    injection=1000
    autarky_rate=1000
    self_consumption_rate=1000
    
    modular_household_results=ModularHouseholdResults(
        investment_cost=investment_cost,
        co2_cost=co2_cost,
        injection=injection,
        autarky_rate=autarky_rate,
        self_consumption_rate=self_consumption_rate,
        terminationflag=lt.Termination
        )
