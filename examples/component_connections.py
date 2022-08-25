""" Contains functions for initializing and connecting components.

The functions are all called in modular_household.
"""


from typing import List, Optional

import csv

import hisim.loadtypes as lt
from hisim.simulator import SimulationParameters
from hisim.components import generic_heat_pump_modular
from hisim.components import generic_heat_source
from hisim.components import controller_l1_generic_runtime
from hisim.components import controller_l2_generic_heat_clever_simple
from hisim.components import controller_l2_generic_heat_simple
from hisim.components import controller_l2_energy_management_system
from hisim.components import generic_hot_water_storage_modular
from hisim.components import loadprofilegenerator_connector
from hisim.components import weather
from hisim.components import building
from hisim.components import generic_pv_system
from hisim.components import generic_smart_device
from hisim.components import advanced_battery_bslib
from hisim.components import generic_CHP
from hisim.components import controller_l2_generic_chp
from hisim.components import generic_electrolyzer
from hisim.components import generic_hydrogen_storage
from hisim.components import controller_l3_smart_devices
from hisim import utils


def configure_pv_system(my_sim, my_simulation_parameters: SimulationParameters, my_weather: weather.Weather,
                        production: List, pv_peak_power: Optional[float], count: int):
    """ Sets PV System.

    Parameters
    ----------
    my_sim: str
        filename of orginal built example.
    my_simulation_parameters: SimulationParameters
        The simulation parameters.
    my_weather: Weather
        The initialized Weather component.
    production: List
        List of Components with Parameter Production.
    pv_peak_power: float or None
        The peak power of the PV panel. In case of None default is used.
    count: int
        Integer tracking component hierachy for EMS.

    """
    if pv_peak_power is not None:
        my_pv_system_config = generic_pv_system.PVSystem.get_default_config(power=pv_peak_power, source_weight=count)
    else:
        my_pv_system_config = generic_pv_system.PVSystem.get_default_config(source_weight=count)
    count += 1
    my_pv_system = generic_pv_system.PVSystem(
        my_simulation_parameters=my_simulation_parameters, my_simulation_repository=my_sim.simulation_repository,
        config=my_pv_system_config)
    my_pv_system.connect_only_predefined_connections(my_weather)
    my_sim.add_component(my_pv_system)
    production.append(my_pv_system)

    return production, count


def configure_smart_devices(my_sim, my_simulation_parameters: SimulationParameters, consumption: List, count: int):
    """ Sets smart devices without controllers.

    Parameters
    ----------
    my_sim: str
        filename of orginal built example.
    my_simulation_parameters: SimulationParameters
        The simulation parameters.
    consumption: list
        List of Components with Parameter Consumption.
    count: int
        Integer tracking component hierachy for EMS.

    """
    filepath = utils.HISIMPATH["smart_devices"]["device_collection"]
    device_collection = []

    with open(filepath, 'r') as file:
        i = 0
        formatreader = csv.reader(file, delimiter=';')
        for line in formatreader:
            if i > 1:
                device_collection.append(line[0])
            i += 1

    # create all smart devices
    my_smart_devices: List[generic_smart_device.SmartDevice] = []
    for device in device_collection:
        my_smart_devices.append(generic_smart_device.SmartDevice(
            identifier=device, source_weight=count, my_simulation_parameters=my_simulation_parameters))
        my_sim.add_component(my_smart_devices[-1])
        consumption.append(my_smart_devices[-1])
        count += 1

    return my_smart_devices, consumption, count


def configure_smart_controller_for_smart_devices(my_sim, my_simulation_parameters: SimulationParameters,
                                                 my_smart_devices: List[generic_smart_device.SmartDevice]):
    """ Sets l3 controller for smart devices.

    Parameters
    ----------
    my_sim: str
        filename of orginal built example.
    my_simulation_parameters: SimulationParameters
        The simulation parameters.
    my_smart_devices: List[SmartDevice]
        List of initilized smart devices.

    """
    # construct predictive controller
    my_controller_l3 = controller_l3_smart_devices.L3_Controller(my_simulation_parameters=my_simulation_parameters)

    for elem in my_smart_devices:
        l3_activation_signal = my_controller_l3.add_component_output(
            source_output_name=lt.InandOutputType.RECOMMENDED_ACTIVATION,
            source_tags=[lt.ComponentType.SMART_DEVICE, lt.InandOutputType.RECOMMENDED_ACTIVATION],
            source_weight=elem.source_weight, source_load_type=lt.LoadTypes.ACTIVATION, source_unit=lt.Units.TIMESTEPS)
        elem.connect_dynamic_input(input_fieldname=generic_smart_device.SmartDevice.l3_DeviceActivation, src_object=l3_activation_signal)

        # elem.connect_dynamic_input( in)
        my_controller_l3.add_component_input_and_connect(
            source_component_class=elem, source_component_output=elem.LastActivation, source_load_type=lt.LoadTypes.ACTIVATION,
            source_unit=lt.Units.TIMESTEPS,
            source_tags=[lt.ComponentType.SMART_DEVICE, lt.InandOutputType.LAST_ACTIVATION], source_weight=elem.source_weight)
        my_controller_l3.add_component_input_and_connect(
            source_component_class=elem, source_component_output=elem.EarliestActivation, source_load_type=lt.LoadTypes.ACTIVATION,
            source_unit=lt.Units.TIMESTEPS,
            source_tags=[lt.ComponentType.SMART_DEVICE, lt.InandOutputType.EARLIEST_ACTIVATION], source_weight=elem.source_weight)
        my_controller_l3.add_component_input_and_connect(
            source_component_class=elem, source_component_output=elem.LatestActivation, source_load_type=lt.LoadTypes.ACTIVATION,
            source_unit=lt.Units.TIMESTEPS,
            source_tags=[lt.ComponentType.SMART_DEVICE, lt.InandOutputType.LATEST_ACTIVATION], source_weight=elem.source_weight)
    my_sim.add_component(my_controller_l3)


def configure_battery(my_sim, my_simulation_parameters: SimulationParameters,
                      my_electricity_controller: controller_l2_energy_management_system.ControllerElectricityGeneric,
                      battery_capacity: Optional[float], count: int):
    """ Sets advanced battery system with surplus controller.

    Parameters
    ----------
    my_sim: str
        filename of orginal built example.
    my_simulation_parameters: SimulationParameters
        The simulation parameters.
    my_electricity_controller: ControllerElectricityGeneric
        The initialized electricity controller.
    battery_capacity: float or None
        Capacity of the battery in Wh. In case of None default is used.
    count: int
        Integer tracking component hierachy for EMS.

    """
    if battery_capacity is not None:
        my_advanced_battery_config = advanced_battery_bslib.Battery.get_default_config(
            e_bat_custom=battery_capacity, p_inv_custom=battery_capacity * 0.5, source_weight=count)
    else:
        my_advanced_battery_config = advanced_battery_bslib.Battery.get_default_config(source_weight=count)
    count += 1
    my_advanced_battery = advanced_battery_bslib.Battery(my_simulation_parameters=my_simulation_parameters, config=my_advanced_battery_config)

    my_electricity_controller.add_component_input_and_connect(
        source_component_class=my_advanced_battery, source_component_output=my_advanced_battery.AcBatteryPower,
        source_load_type=lt.LoadTypes.ELECTRICITY, source_unit=lt.Units.WATT,
        source_tags=[lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_REAL],
        source_weight=my_advanced_battery.source_weight)

    electricity_to_or_from_battery_target = my_electricity_controller.add_component_output(
        source_output_name=lt.InandOutputType.ELECTRICITY_TARGET, source_tags=[lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_TARGET],
        source_weight=my_advanced_battery.source_weight, source_load_type=lt.LoadTypes.ELECTRICITY, source_unit=lt.Units.WATT)

    my_advanced_battery.connect_dynamic_input(
        input_fieldname=advanced_battery_bslib.Battery.LoadingPowerInput, src_object=electricity_to_or_from_battery_target)
    my_sim.add_component(my_advanced_battery)

    return count


def configure_water_heating(
        my_sim, my_simulation_parameters: SimulationParameters,
        my_occupancy: loadprofilegenerator_connector.Occupancy,
        my_electricity_controller: controller_l2_energy_management_system.ControllerElectricityGeneric,
        my_weather: weather.Weather, water_heating_system_installed: lt.HeatingSystems,
        count: int):
    """ Sets Boiler with Heater, L1 Controller and L2 Controller for Water Heating System.

    Parameters
    ----------
    my_sim: str
        filename of orginal built example.
    my_simulation_parameters: SimulationParameters
        The simulation parameters.
    my_occupancy: Occupancy
        The initialized occupancy component.
    my_electricity_controller: ControllerElectricityGeneric
        The initialized electricity controller.
    my_weather: Weather
        The initialized Weather component.
    water_heating_system_installed: str
        Type of installed WaterHeatingSystem
    count: int
        Integer tracking component hierachy for EMS.

    """
    boiler_config = generic_hot_water_storage_modular.HotWaterStorage.get_default_config_boiler()
    if water_heating_system_installed == lt.HeatingSystems.HEAT_PUMP:
        waterheater_config = generic_heat_pump_modular.HeatPump.get_default_config_waterheating()
        waterheater_l1_config = controller_l1_generic_runtime.L1_Controller.get_default_config_heatpump()
        waterheater_l2_config = controller_l2_generic_heat_clever_simple.L2_Controller.get_default_config_waterheating()
    elif water_heating_system_installed == lt.HeatingSystems.ELECTRIC_HEATING:
        waterheater_config = generic_heat_pump_modular.HeatPump.get_default_config_waterheating_electric()
        waterheater_l1_config = controller_l1_generic_runtime.L1_Controller.get_default_config()
        waterheater_l2_config = controller_l2_generic_heat_clever_simple.L2_Controller.get_default_config_waterheating()
    elif water_heating_system_installed in [lt.HeatingSystems.GAS_HEATING, lt.HeatingSystems.OIL_HEATING, lt.HeatingSystems.DISTRICT_HEATING]:
        waterheater_config = generic_heat_source.HeatSource.get_default_config_waterheating()
        waterheater_l1_config = controller_l1_generic_runtime.L1_Controller.get_default_config()
        waterheater_l2_config = controller_l2_generic_heat_simple.L2_Controller.get_default_config_waterheating()
        if water_heating_system_installed == lt.HeatingSystems.GAS_HEATING:
            waterheater_config.fuel = lt.LoadTypes.GAS
        elif water_heating_system_installed == lt.HeatingSystems.OIL_HEATING:
            waterheater_config.fuel = lt.LoadTypes.OIL
        elif water_heating_system_installed == lt.HeatingSystems.DISTRICT_HEATING:
            waterheater_config.fuel = lt.LoadTypes.DISTRICTHEATING
    [waterheater_config.source_weight, boiler_config.source_weight, waterheater_l1_config.source_weight,
     waterheater_l2_config.source_weight] = [count] * 4
    count += 1

    waterheater_config.power_th = my_occupancy.max_hot_water_demand * 0.5 *\
        (boiler_config.warm_water_temperature - boiler_config.drain_water_temperature) * 0.977 * 4.182 / 3.6
    if water_heating_system_installed == lt.HeatingSystems.HEAT_PUMP:
        waterheater_l2_config.P_threshold = waterheater_config.power_th / 3
    elif water_heating_system_installed == lt.HeatingSystems.ELECTRIC_HEATING:
        waterheater_l2_config.P_threshold = waterheater_config.power_th

    my_boiler = generic_hot_water_storage_modular.HotWaterStorage(my_simulation_parameters=my_simulation_parameters, config=boiler_config)
    my_boiler.connect_only_predefined_connections(my_occupancy)
    my_sim.add_component(my_boiler)

    if water_heating_system_installed in [lt.HeatingSystems.HEAT_PUMP, lt.HeatingSystems.ELECTRIC_HEATING]:
        my_waterheater_controller_l2 = controller_l2_generic_heat_clever_simple.L2_Controller(my_simulation_parameters=my_simulation_parameters,
                                                                                              config=waterheater_l2_config)
    else:
        my_waterheater_controller_l2 = controller_l2_generic_heat_simple.L2_Controller(my_simulation_parameters=my_simulation_parameters,
                                                                                       config=waterheater_l2_config)
    my_waterheater_controller_l2.connect_only_predefined_connections(my_boiler)
    my_sim.add_component(my_waterheater_controller_l2)

    my_waterheater_controller_l1 = controller_l1_generic_runtime.L1_Controller(my_simulation_parameters=my_simulation_parameters,
                                                                               config=waterheater_l1_config)
    my_waterheater_controller_l1.connect_only_predefined_connections(my_waterheater_controller_l2)
    my_sim.add_component(my_waterheater_controller_l1)

    if water_heating_system_installed in [lt.HeatingSystems.HEAT_PUMP, lt.HeatingSystems.ELECTRIC_HEATING]:
        my_waterheater = generic_heat_pump_modular.HeatPump(config=waterheater_config, my_simulation_parameters=my_simulation_parameters)
        my_waterheater_controller_l2.connect_only_predefined_connections(my_waterheater_controller_l1)
        my_waterheater.connect_only_predefined_connections(my_weather)
    else:
        my_waterheater = generic_heat_source.HeatSource(config=waterheater_config, my_simulation_parameters=my_simulation_parameters)
    my_waterheater.connect_only_predefined_connections(my_waterheater_controller_l1)
    my_sim.add_component(my_waterheater)
    my_boiler.connect_only_predefined_connections(my_waterheater)

    if water_heating_system_installed in [lt.HeatingSystems.HEAT_PUMP, lt.HeatingSystems.ELECTRIC_HEATING]:
        my_electricity_controller.add_component_input_and_connect(source_component_class=my_waterheater,
                                                                  source_component_output=my_waterheater.ElectricityOutput,
                                                                  source_load_type=lt.LoadTypes.ELECTRICITY,
                                                                  source_unit=lt.Units.WATT,
                                                                  source_tags=[lt.ComponentType.HEAT_PUMP, lt.InandOutputType.ELECTRICITY_REAL],
                                                                  source_weight=my_waterheater.source_weight)

        electricity_to_waterheater = my_electricity_controller.add_component_output(
            source_output_name=lt.InandOutputType.ELECTRICITY_TARGET, source_tags=[lt.ComponentType.HEAT_PUMP, lt.InandOutputType.ELECTRICITY_TARGET],
            source_weight=my_waterheater.source_weight, source_load_type=lt.LoadTypes.ELECTRICITY, source_unit=lt.Units.WATT)

        my_waterheater_controller_l2.connect_dynamic_input(input_fieldname=controller_l2_generic_heat_clever_simple.L2_Controller.ElectricityTarget,
                                                           src_object=electricity_to_waterheater)
    return count


def configure_heating(my_sim, my_simulation_parameters: SimulationParameters,
                      my_building: building.Building,
                      my_electricity_controller: controller_l2_energy_management_system.ControllerElectricityGeneric,
                      my_weather: weather.Weather, heating_system_installed: str, count: int):
    """ Sets Heater, L1 Controller and L2 Controller for Heating System.

    Parameters
    ----------
    my_sim: str
        filename of orginal built example.
    my_simulation_parameters: SimulationParameters
        The simulation parameters.
    my_building: Building
        The initialized building component.
    my_electricity_controller: ControllerElectricityGeneric
        The initialized electricity controller.
    my_weather: Weather
        The initialized Weather component.
    heating_system_installed: str
        Type of installed HeatingSystem
    count: int
        Integer tracking component hierachy for EMS.

    """
    if heating_system_installed == lt.HeatingSystems.HEAT_PUMP:
        heater_config = generic_heat_pump_modular.HeatPump.get_default_config_heating()
        heater_l1_config = controller_l1_generic_runtime.L1_Controller.get_default_config_heatpump()
        heater_l2_config = controller_l2_generic_heat_clever_simple.L2_Controller.get_default_config_heating()
    elif heating_system_installed == lt.HeatingSystems.ELECTRIC_HEATING:
        heater_config = generic_heat_pump_modular.HeatPump.get_default_config_heating_electric()
        heater_l1_config = controller_l1_generic_runtime.L1_Controller.get_default_config()
        heater_l2_config = controller_l2_generic_heat_clever_simple.L2_Controller.get_default_config_heating_electric()
    elif heating_system_installed in [lt.HeatingSystems.GAS_HEATING, lt.HeatingSystems.OIL_HEATING, lt.HeatingSystems.DISTRICT_HEATING]:
        heater_config = generic_heat_source.HeatSource.get_default_config_heating()
        heater_l1_config = controller_l1_generic_runtime.L1_Controller.get_default_config()
        heater_l2_config = controller_l2_generic_heat_simple.L2_Controller.get_default_config_heating()
        if heating_system_installed == lt.HeatingSystems.GAS_HEATING:
            heater_config.fuel = lt.LoadTypes.GAS
        elif heating_system_installed == lt.HeatingSystems.OIL_HEATING:
            heater_config.fuel = lt.LoadTypes.OIL
        elif heating_system_installed == lt.HeatingSystems.DISTRICT_HEATING:
            heater_config.fuel = lt.LoadTypes.DISTRICTHEATING
    [heater_config.source_weight, heater_l1_config.source_weight, heater_l2_config.source_weight] = [count] * 3
    count += 1

    heater_config.power_th = my_building.max_thermal_building_demand
    if heating_system_installed == lt.HeatingSystems.HEAT_PUMP:
        heater_l2_config.P_threshold = heater_config.power_th / 3
        heater_l2_config.cooling_considered = True
    elif heating_system_installed == lt.HeatingSystems.ELECTRIC_HEATING:
        heater_l2_config.P_threshold = heater_config.power_th

    if heating_system_installed in [lt.HeatingSystems.HEAT_PUMP, lt.HeatingSystems.ELECTRIC_HEATING]:
        my_heater_controller_l2 = controller_l2_generic_heat_clever_simple.L2_Controller(my_simulation_parameters=my_simulation_parameters,
                                                                                         config=heater_l2_config)
    else:
        my_heater_controller_l2 = controller_l2_generic_heat_simple.L2_Controller(my_simulation_parameters=my_simulation_parameters,
                                                                                  config=heater_l2_config)
    my_heater_controller_l2.connect_only_predefined_connections(my_building)
    my_sim.add_component(my_heater_controller_l2)

    my_heater_controller_l1 = controller_l1_generic_runtime.L1_Controller(my_simulation_parameters=my_simulation_parameters,
                                                                          config=heater_l1_config)
    my_heater_controller_l1.connect_only_predefined_connections(my_heater_controller_l2)
    my_sim.add_component(my_heater_controller_l1)
    my_heater_controller_l2.connect_only_predefined_connections(my_heater_controller_l1)

    if heating_system_installed in [lt.HeatingSystems.HEAT_PUMP, lt.HeatingSystems.ELECTRIC_HEATING]:
        my_heater = generic_heat_pump_modular.HeatPump(config=heater_config, my_simulation_parameters=my_simulation_parameters)
        my_heater.connect_only_predefined_connections(my_weather)
    else:
        my_heater = generic_heat_source.HeatSource(config=heater_config, my_simulation_parameters=my_simulation_parameters)
    my_heater.connect_only_predefined_connections(my_heater_controller_l1)
    my_sim.add_component(my_heater)

    if heating_system_installed in [lt.HeatingSystems.HEAT_PUMP, lt.HeatingSystems.ELECTRIC_HEATING]:
        my_electricity_controller.add_component_input_and_connect(source_component_class=my_heater,
                                                                  source_component_output=my_heater.ElectricityOutput,
                                                                  source_load_type=lt.LoadTypes.ELECTRICITY, source_unit=lt.Units.WATT,
                                                                  source_tags=[lt.ComponentType.HEAT_PUMP, lt.InandOutputType.ELECTRICITY_REAL],
                                                                  source_weight=my_heater.source_weight)

        electricity_to_heatpump = my_electricity_controller.add_component_output(
            source_output_name=lt.InandOutputType.ELECTRICITY_TARGET, source_tags=[lt.ComponentType.HEAT_PUMP, lt.InandOutputType.ELECTRICITY_TARGET],
            source_weight=my_heater.source_weight, source_load_type=lt.LoadTypes.ELECTRICITY, source_unit=lt.Units.WATT)

        my_heater_controller_l2.connect_dynamic_input(input_fieldname=controller_l2_generic_heat_clever_simple.L2_Controller.ElectricityTarget,
                                                      src_object=electricity_to_heatpump)

    return my_heater, count


def configure_heating_with_buffer(my_sim,
                                  my_simulation_parameters: SimulationParameters,
                                  my_building: building.Building,
                                  my_electricity_controller: controller_l2_energy_management_system.ControllerElectricityGeneric,
                                  my_weather: weather.Weather, heating_system_installed: str, buffer_volume: Optional[float], count: int):
    """ Sets Heater, L1 Controller and L2 Controller for Heating System.

    Parameters
    ----------
    my_sim: str
        filename of orginal built example.
    my_simulation_parameters: SimulationParameters
        The simulation parameters.
    my_building: Building
        The initialized building component.
    my_electricity_controller: ControllerElectricityGeneric
        The initialized electricity controller.
    my_weather: Weather
        The initialized Weather component.
    heating_system_installed: str
        Type of installed HeatingSystem.
    buffer_volume: float or None
        Volume of buffer storage in liters. In case of None default is used.
    count: int
        Integer tracking component hierachy for EMS.

    """
    if heating_system_installed == lt.HeatingSystems.HEAT_PUMP:
        heater_config = generic_heat_pump_modular.HeatPump.get_default_config_heating()
        heater_l1_config = controller_l1_generic_runtime.L1_Controller.get_default_config_heatpump()
        heater_l2_config = controller_l2_generic_heat_clever_simple.L2_Controller.get_default_config_buffer_heating()
    elif heating_system_installed == lt.HeatingSystems.ELECTRIC_HEATING:
        heater_config = generic_heat_pump_modular.HeatPump.get_default_config_heating_electric()
        heater_l1_config = controller_l1_generic_runtime.L1_Controller.get_default_config()
        heater_l2_config = controller_l2_generic_heat_clever_simple.L2_Controller.get_default_config_buffer_heating()
    elif heating_system_installed in [lt.HeatingSystems.GAS_HEATING, lt.HeatingSystems.OIL_HEATING, lt.HeatingSystems.DISTRICT_HEATING]:
        heater_config = generic_heat_source.HeatSource.get_default_config_heating()
        heater_l1_config = controller_l1_generic_runtime.L1_Controller.get_default_config()
        heater_l2_config = controller_l2_generic_heat_simple.L2_Controller.get_default_config_buffer_heating()
        if heating_system_installed == lt.HeatingSystems.GAS_HEATING:
            heater_config.fuel = lt.LoadTypes.GAS
        elif heating_system_installed == lt.HeatingSystems.OIL_HEATING:
            heater_config.fuel = lt.LoadTypes.OIL
        elif heating_system_installed == lt.HeatingSystems.DISTRICT_HEATING:
            heater_config.fuel = lt.LoadTypes.DISTRICTHEATING
    [heater_config.source_weight, heater_l1_config.source_weight, heater_l2_config.source_weight] = [count] * 3
    count += 1

    if buffer_volume is not None:
        buffer_config = generic_hot_water_storage_modular.HotWaterStorage.get_default_config_buffer(volume=buffer_volume)
    else:
        buffer_config = generic_hot_water_storage_modular.HotWaterStorage.get_default_config_buffer()
    buffer_config.power = float(my_building.max_thermal_building_demand)
    buffer_l1_config = controller_l1_generic_runtime.L1_Controller.get_default_config()
    buffer_l2_config = controller_l2_generic_heat_simple.L2_Controller.get_default_config_heating()
    [buffer_config.source_weight, buffer_l1_config.source_weight, buffer_l2_config.source_weight] = [count] * 3
    count += 1

    heater_config.power_th = my_building.max_thermal_building_demand
    if heating_system_installed == lt.HeatingSystems.HEAT_PUMP:
        heater_l2_config.P_threshold = heater_config.power_th / 3
        [heater_l2_config.cooling_considered, buffer_l2_config.cooling_considered] = [True] * 2
    elif heating_system_installed == lt.HeatingSystems.ELECTRIC_HEATING:
        heater_l2_config.P_threshold = heater_config.power_th

    my_buffer = generic_hot_water_storage_modular.HotWaterStorage(my_simulation_parameters=my_simulation_parameters, config=buffer_config)
    my_sim.add_component(my_buffer)

    if heating_system_installed in [lt.HeatingSystems.HEAT_PUMP, lt.HeatingSystems.ELECTRIC_HEATING]:
        my_heater_controller_l2 = controller_l2_generic_heat_clever_simple.L2_Controller(my_simulation_parameters=my_simulation_parameters,
                                                                                         config=heater_l2_config)
    else:
        my_heater_controller_l2 = controller_l2_generic_heat_simple.L2_Controller(my_simulation_parameters=my_simulation_parameters,
                                                                                  config=heater_l2_config)
    my_heater_controller_l2.connect_only_predefined_connections(my_buffer)
    my_sim.add_component(my_heater_controller_l2)

    my_heater_controller_l1 = controller_l1_generic_runtime.L1_Controller(my_simulation_parameters=my_simulation_parameters,
                                                                          config=heater_l1_config)
    my_heater_controller_l1.connect_only_predefined_connections(my_heater_controller_l2)
    my_sim.add_component(my_heater_controller_l1)
    my_heater_controller_l2.connect_only_predefined_connections(my_heater_controller_l1)

    if heating_system_installed in [lt.HeatingSystems.HEAT_PUMP, lt.HeatingSystems.ELECTRIC_HEATING]:
        my_heater = generic_heat_pump_modular.HeatPump(config=heater_config, my_simulation_parameters=my_simulation_parameters)
        my_heater.connect_only_predefined_connections(my_weather)
    else:
        my_heater = generic_heat_source.HeatSource(config=heater_config, my_simulation_parameters=my_simulation_parameters)
    my_heater.connect_only_predefined_connections(my_heater_controller_l1)
    my_sim.add_component(my_heater)

    if heating_system_installed in [lt.HeatingSystems.HEAT_PUMP, lt.HeatingSystems.ELECTRIC_HEATING]:
        my_electricity_controller.add_component_input_and_connect(source_component_class=my_heater,
                                                                  source_component_output=my_heater.ElectricityOutput,
                                                                  source_load_type=lt.LoadTypes.ELECTRICITY, source_unit=lt.Units.WATT,
                                                                  source_tags=[lt.ComponentType.HEAT_PUMP, lt.InandOutputType.ELECTRICITY_REAL],
                                                                  source_weight=my_heater.source_weight)

        electricity_to_heatpump = my_electricity_controller.add_component_output(
            source_output_name=lt.InandOutputType.ELECTRICITY_TARGET, source_tags=[lt.ComponentType.HEAT_PUMP, lt.InandOutputType.ELECTRICITY_TARGET],
            source_weight=my_heater.source_weight, source_load_type=lt.LoadTypes.ELECTRICITY, source_unit=lt.Units.WATT)

        my_heater_controller_l2.connect_dynamic_input(input_fieldname=controller_l2_generic_heat_clever_simple.L2_Controller.ElectricityTarget,
                                                      src_object=electricity_to_heatpump)

    my_buffer_controller_l2 = controller_l2_generic_heat_simple.L2_Controller(my_simulation_parameters=my_simulation_parameters,
                                                                              config=buffer_l2_config)
    my_buffer_controller_l2.connect_only_predefined_connections(my_building)
    my_sim.add_component(my_buffer_controller_l2)
    my_buffer_controller_l1 = controller_l1_generic_runtime.L1_Controller(my_simulation_parameters=my_simulation_parameters,
                                                                          config=buffer_l1_config)
    my_buffer_controller_l1.connect_only_predefined_connections(my_buffer_controller_l2)
    my_sim.add_component(my_buffer_controller_l1)

    my_buffer.connect_only_predefined_connections(my_buffer_controller_l1)

    my_building.add_component_input_and_connect(source_component_class=my_buffer, source_component_output=my_buffer.HeatToBuilding,
                                                source_load_type=lt.LoadTypes.HEATING, source_unit=lt.Units.WATT,
                                                source_tags=[lt.InandOutputType.HEAT_TO_BUILDING], source_weight=count - 1)

    return my_heater, my_buffer, count


def configure_elctrolysis_h2storage_chp_system(my_sim, my_simulation_parameters: SimulationParameters, my_building: building.Building,
                                               my_electricity_controller: controller_l2_energy_management_system.ControllerElectricityGeneric,
                                               chp_power: Optional[float], h2_storage_size: Optional[float], electrolyzer_power: Optional[float],
                                               count: int):
    """ Sets electrolysis, H2-storage and chp system.

    Parameters
    ----------
    my_sim: str
        filename of orginal built example.
    my_simulation_parameters: SimulationParameters
        The simulation parameters.
    my_building: Building
        The initialized building component.
    my_electricity_controller: ControllerElectricityGeneric
        The initialized electricity controller.
    chp_power: float or None
        Maximum power (thermal+electrical+loss) of CHP in Watt.
    h2_storage_size: float or None
        Maximum capacity of hydrogen storage in kg hydrogen.
    electrolyzer_power: float or None
        Maximum power of electrolyzer in Watt.
    count: int
        Integer tracking component hierachy for EMS.

    """
    # Fuel Cell default configurations
    l2_config = controller_l2_generic_chp.L2_Controller.get_default_config()
    l2_config.source_weight = count
    l1_config = generic_CHP.L1_Controller.get_default_config()
    l1_config.source_weight = count
    if chp_power is not None:
        chp_config = generic_CHP.GCHPConfig(name='CHP', source_weight=count, p_el=0.3 * chp_power, p_th=0.5 * chp_power, p_fuel=chp_power)
    else:
        chp_config = generic_CHP.GCHP.get_default_config()
        chp_config.source_weight = count
    count += 1

    # fuel cell
    my_chp = generic_CHP.GCHP(my_simulation_parameters=my_simulation_parameters, config=chp_config)
    my_sim.add_component(my_chp)

    # heat controller of fuel cell
    my_chp_controller_l2 = controller_l2_generic_chp.L2_Controller(my_simulation_parameters=my_simulation_parameters, config=l2_config)
    my_chp_controller_l2.connect_only_predefined_connections(my_building)
    my_sim.add_component(my_chp_controller_l2)

    # run time controller of fuel cell
    my_chp_controller_l1 = generic_CHP.L1_Controller(my_simulation_parameters=my_simulation_parameters, config=l1_config)
    my_chp_controller_l1.connect_only_predefined_connections(my_chp_controller_l2)
    my_sim.add_component(my_chp_controller_l1)
    my_chp.connect_only_predefined_connections(my_chp_controller_l1)

    # electricity controller of fuel cell
    my_electricity_controller.add_component_input_and_connect(
        source_component_class=my_chp, source_component_output=my_chp.ElectricityOutput, source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT, source_tags=[lt.ComponentType.FUEL_CELL, lt.InandOutputType.ELECTRICITY_REAL], source_weight=my_chp.source_weight)
    electricity_from_fuelcell_target = my_electricity_controller.add_component_output(
        source_output_name=lt.InandOutputType.ELECTRICITY_TARGET, source_tags=[lt.ComponentType.FUEL_CELL, lt.InandOutputType.ELECTRICITY_TARGET],
        source_weight=my_chp.source_weight, source_load_type=lt.LoadTypes.ELECTRICITY, source_unit=lt.Units.WATT)
    my_chp_controller_l1.connect_dynamic_input(input_fieldname=generic_CHP.L1_Controller.ElectricityTarget,
                                               src_object=electricity_from_fuelcell_target)

    # electrolyzer default configuration
    l1_config = generic_electrolyzer.L1_Controller.get_default_config()
    l1_config.source_weight = count
    if electrolyzer_power is not None:
        electrolyzer_config = generic_electrolyzer.ElectrolyzerConfig(
            name='Electrolyzer', source_weight=count, min_power=0.5 * electrolyzer_power, max_power=electrolyzer_power,
            min_hydrogen_production_rate_hour=0.125 * electrolyzer_power, max_hydrogen_production_rate_hour=2 * electrolyzer_power)
    else:
        electrolyzer_config = generic_electrolyzer.Electrolyzer.get_default_config()
        electrolyzer_config.source_weight = count
    count += 1

    # electrolyzer
    my_electrolyzer = generic_electrolyzer.Electrolyzer(my_simulation_parameters=my_simulation_parameters, config=electrolyzer_config)
    my_sim.add_component(my_electrolyzer)

    # run time controller of electrolyzer
    my_electrolyzer_controller_l1 = generic_electrolyzer.L1_Controller(my_simulation_parameters=my_simulation_parameters, config=l1_config)
    my_sim.add_component(my_electrolyzer_controller_l1)
    my_electrolyzer.connect_only_predefined_connections(my_electrolyzer_controller_l1)

    # electricity controller of fuel cell
    my_electricity_controller.add_component_input_and_connect(
        source_component_class=my_electrolyzer, source_component_output=my_electrolyzer.ElectricityOutput, source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT, source_tags=[lt.ComponentType.ELECTROLYZER, lt.InandOutputType.ELECTRICITY_REAL],
        source_weight=my_electrolyzer.source_weight)
    electricity_to_electrolyzer_target = my_electricity_controller.add_component_output(
        source_output_name=lt.InandOutputType.ELECTRICITY_TARGET, source_tags=[lt.ComponentType.ELECTROLYZER, lt.InandOutputType.ELECTRICITY_TARGET],
        source_weight=my_electrolyzer.source_weight, source_load_type=lt.LoadTypes.ELECTRICITY, source_unit=lt.Units.WATT)
    my_electrolyzer_controller_l1.connect_dynamic_input(
        input_fieldname=generic_electrolyzer.L1_Controller.l2_ElectricityTarget, src_object=electricity_to_electrolyzer_target)

    if h2_storage_size is not None:
        h2_storage_config = generic_hydrogen_storage.HydrogenStorage.get_default_config(
            capacity=h2_storage_size, max_charging_rate=h2_storage_size * 1e-2, max_discharging_rate=h2_storage_size * 1e-2, source_weight=count)
    else:
        h2_storage_config = generic_hydrogen_storage.HydrogenStorage.get_default_config(source_weight=count)
    my_h2storage = generic_hydrogen_storage.HydrogenStorage(my_simulation_parameters=my_simulation_parameters, config=h2_storage_config)
    my_h2storage.connect_only_predefined_connections(my_electrolyzer)
    my_h2storage.connect_only_predefined_connections(my_chp)
    my_sim.add_component(my_h2storage)

    my_electrolyzer_controller_l1.connect_only_predefined_connections(my_h2storage)
    my_chp_controller_l1.connect_only_predefined_connections(my_h2storage)

    return my_chp, count
