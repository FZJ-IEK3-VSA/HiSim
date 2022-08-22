""" Contains functions for initializing and connecting components.

The functions are all called in modular_household.
"""


from typing import List

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


def configure_water_heating(
        my_sim, my_simulation_parameters: SimulationParameters,
        my_occupancy: loadprofilegenerator_connector.Occupancy,
        my_electricity_controller: controller_l2_energy_management_system.ControllerElectricityGeneric,
        my_weather: weather.Weather, water_heating_system_installed: str,
        consumption: List, count: int):
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
    consumption: List
        List of Components with Parameter Consumption.
    count: int
        Integer tracking component hierachy for EMS.

    """
    boiler_config = generic_hot_water_storage_modular.HotWaterStorage.get_default_config_boiler()
    if water_heating_system_installed == 'HeatPump':
        waterheater_config = generic_heat_pump_modular.HeatPump.get_default_config_waterheating()
        waterheater_l1_config = controller_l1_generic_runtime.L1_Controller.get_default_config_heatpump()
        waterheater_l2_config = controller_l2_generic_heat_clever_simple.L2_Controller.get_default_config_waterheating()
    elif water_heating_system_installed == 'ElectricHeating':
        waterheater_config = generic_heat_pump_modular.HeatPump.get_default_config_waterheating_electric()
        waterheater_l1_config = controller_l1_generic_runtime.L1_Controller.get_default_config()
        waterheater_l2_config = controller_l2_generic_heat_clever_simple.L2_Controller.get_default_config_waterheating()
    elif water_heating_system_installed in ['GasHeating', 'OilHeating', 'DistrictHeating']:
        waterheater_config = generic_heat_source.HeatSource.get_default_config_waterheating()
        waterheater_l1_config = controller_l1_generic_runtime.L1_Controller.get_default_config()
        waterheater_l2_config = controller_l2_generic_heat_simple.L2_Controller.get_default_config_waterheating()
        if water_heating_system_installed == 'GasHeating':
            waterheater_config.fuel = lt.LoadTypes.GAS
        elif water_heating_system_installed == 'OilHeating':
            waterheater_config.fuel = lt.LoadTypes.OIL
        elif water_heating_system_installed == 'DistrictHeating':
            waterheater_config.fuel = lt.LoadTypes.DISTRICTHEATING
    [waterheater_config.source_weight, boiler_config.source_weight, waterheater_l1_config.source_weight,
     waterheater_l2_config.source_weight] = [count] * 4
    count += 1

    waterheater_config.power_th = my_occupancy.max_hot_water_demand * 0.5 *\
        (boiler_config.warm_water_temperature - boiler_config.drain_water_temperature) * 0.977 * 4.182 / 3.6
    if water_heating_system_installed == 'HeatPump':
        waterheater_l2_config.P_threshold = waterheater_config.power_th / 3
    elif water_heating_system_installed == 'ElectricHeating':
        waterheater_l2_config.P_threshold = waterheater_config.power_th

    my_boiler = generic_hot_water_storage_modular.HotWaterStorage(my_simulation_parameters=my_simulation_parameters, config=boiler_config)
    my_boiler.connect_only_predefined_connections(my_occupancy)
    my_sim.add_component(my_boiler)

    if water_heating_system_installed in ['HeatPump', 'ElectricHeating']:
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

    if water_heating_system_installed in ['HeatPump', 'ElectricHeating']:
        my_waterheater = generic_heat_pump_modular.HeatPump(config=waterheater_config, my_simulation_parameters=my_simulation_parameters)
        my_waterheater_controller_l2.connect_only_predefined_connections(my_waterheater_controller_l1)
        my_waterheater.connect_only_predefined_connections(my_weather)
    else:
        my_waterheater = generic_heat_source.HeatSource(config=waterheater_config, my_simulation_parameters=my_simulation_parameters)
    my_waterheater.connect_only_predefined_connections(my_waterheater_controller_l1)
    my_sim.add_component(my_waterheater)
    my_boiler.connect_only_predefined_connections(my_waterheater)

    if water_heating_system_installed in ['HeatPump', 'ElectricHeating']:
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
        consumption.append(my_waterheater)
    return consumption, count


def configure_heating(my_sim, my_simulation_parameters: SimulationParameters,
                      my_building: building.Building,
                      my_electricity_controller: controller_l2_energy_management_system.ControllerElectricityGeneric,
                      my_weather: weather.Weather, heating_system_installed: str, consumption: List, count: int):
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
    consumption: List
        List of Components with Parameter Consumption.
    count: int
        Integer tracking component hierachy for EMS.

    """
    if heating_system_installed == 'HeatPump':
        heater_config = generic_heat_pump_modular.HeatPump.get_default_config_heating()
        l1_config = controller_l1_generic_runtime.L1_Controller.get_default_config_heatpump()
        l2_config = controller_l2_generic_heat_clever_simple.L2_Controller.get_default_config_heating()
    elif heating_system_installed == 'ElectricHeating':
        heater_config = generic_heat_pump_modular.HeatPump.get_default_config_heating_electric()
        l1_config = controller_l1_generic_runtime.L1_Controller.get_default_config()
        l2_config = controller_l2_generic_heat_clever_simple.L2_Controller.get_default_config_heating()
    elif heating_system_installed in ['GasHeating', 'OilHeating', 'DistrictHeating']:
        heater_config = generic_heat_source.HeatSource.get_default_config_heating()
        l1_config = controller_l1_generic_runtime.L1_Controller.get_default_config()
        l2_config = controller_l2_generic_heat_simple.L2_Controller.get_default_config_heating()
        if heating_system_installed == 'GasHeating':
            heater_config.fuel = lt.LoadTypes.GAS
        elif heating_system_installed == 'OilHeating':
            heater_config.fuel = lt.LoadTypes.OIL
        elif heating_system_installed == 'DistrictHeating':
            heater_config.fuel = lt.LoadTypes.DISTRICTHEATING
    [heater_config.source_weight, l1_config.source_weight, l2_config.source_weight] = [count] * 3
    count += 1

    heater_config.power_th = my_building.max_thermal_building_demand
    if heating_system_installed == 'HeatPump':
        l2_config.P_threshold = heater_config.power_th / 3
    elif heating_system_installed == 'ElectricHeating':
        l2_config.P_threshold = heater_config.power_th

    if heating_system_installed in ['HeatPump', 'ElectricHeating']:
        my_heater_controller_l2 = controller_l2_generic_heat_clever_simple.L2_Controller(my_simulation_parameters=my_simulation_parameters,
                                                                                         config=l2_config)
    else:
        my_heater_controller_l2 = controller_l2_generic_heat_simple.L2_Controller(my_simulation_parameters=my_simulation_parameters,
                                                                                  config=l2_config)
    my_heater_controller_l2.connect_only_predefined_connections(my_building)
    my_sim.add_component(my_heater_controller_l2)

    my_heater_controller_l1 = controller_l1_generic_runtime.L1_Controller(my_simulation_parameters=my_simulation_parameters,
                                                                          config=l1_config)
    my_heater_controller_l1.connect_only_predefined_connections(my_heater_controller_l2)
    my_sim.add_component(my_heater_controller_l1)
    my_heater_controller_l2.connect_only_predefined_connections(my_heater_controller_l1)

    if heating_system_installed in ['HeatPump', 'ElectricHeating']:
        my_heater = generic_heat_pump_modular.HeatPump(config=heater_config, my_simulation_parameters=my_simulation_parameters)
        my_heater.connect_only_predefined_connections(my_weather)
    else:
        my_heater = generic_heat_source.HeatSource(config=heater_config, my_simulation_parameters=my_simulation_parameters)
    my_heater.connect_only_predefined_connections(my_heater_controller_l1)
    my_sim.add_component(my_heater)

    if heating_system_installed in ['HeatPump', 'ElectricHeating']:
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
        consumption.append(my_heater)

    return my_heater, consumption, count
