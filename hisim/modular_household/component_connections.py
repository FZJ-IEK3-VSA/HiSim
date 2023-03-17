# clean

""" Contains functions for initializing and connecting components.

The functions are all called in modular_household.
"""

from typing import List, Optional, Tuple, Any
from os import listdir, path
import json

from utspclient.helpers.lpgpythonbindings import JsonReference

import hisim.loadtypes as lt
from hisim.component import Component
from hisim.simulator import SimulationParameters
from hisim.components import generic_heat_pump_modular
from hisim.components import generic_heat_source
from hisim.components import controller_l1_building_heating
from hisim.components import controller_l1_heatpump
from hisim.components import controller_l2_generic_heat_simple
from hisim.components import controller_l2_energy_management_system
from hisim.components import generic_hot_water_storage_modular
from hisim.components import loadprofilegenerator_connector
from hisim.components import weather
from hisim.components import building
from hisim.components import generic_pv_system
from hisim.components import generic_smart_device
from hisim.components import generic_car
from hisim.components import controller_l1_generic_ev_charge
from hisim.components import advanced_battery_bslib
from hisim.components import advanced_ev_battery_bslib
from hisim.components import generic_CHP
from hisim.components import generic_electrolyzer
from hisim.components import generic_hydrogen_storage
from hisim.components.configuration import HouseholdWarmWaterDemandConfig
from hisim import utils


def configure_pv_system(
    my_sim: Any,
    my_simulation_parameters: SimulationParameters,
    my_weather: weather.Weather,
    production: List,
    pv_peak_power: Optional[float],
    count: int,
) -> Tuple[List, int]:
    """Sets PV System.

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
        my_pv_system_config = generic_pv_system.PVSystem.get_default_config(
            power=pv_peak_power, source_weight=count
        )
    else:
        my_pv_system_config = generic_pv_system.PVSystem.get_default_config(
            source_weight=count
        )
    count += 1
    my_pv_system = generic_pv_system.PVSystem(
        my_simulation_parameters=my_simulation_parameters,
        config=my_pv_system_config,
    )
    my_pv_system.connect_only_predefined_connections(my_weather)
    my_sim.add_component(my_pv_system)
    production.append(my_pv_system)

    return production, count


def configure_smart_devices(
    my_sim: Any,
    my_simulation_parameters: SimulationParameters,
    count: int,
    smart_devices_included: bool,
) -> Tuple[List[generic_smart_device.SmartDevice], int]:
    """Sets smart devices without controllers.

    Parameters
    ----------
    my_sim: str
        filename of orginal built example.
    my_simulation_parameters: SimulationParameters
        The simulation parameters.
    smart_devices_included: bool
        True if smart devices (washing machine, dish washer, etc.) are actually smart or surplus controlled.
    count: int
        Integer tracking component hierachy for EMS.

    """
    filepath = path.join(utils.HISIMPATH["utsp_reports"], "FlexibilityEvents.HH1.json")
    device_collection = []
    with open(filepath, mode="r", encoding="utf-8") as jsonfile:
        strfile = json.load(jsonfile)

    for elem in strfile:
        if elem["Device"]["Name"] in device_collection:
            pass
        else:
            device_collection.append(elem["Device"]["Name"])

    # create all smart devices
    my_smart_devices: List[generic_smart_device.SmartDevice] = []
    for device in device_collection:
        my_smart_devices.append(
            generic_smart_device.SmartDevice(
                identifier=device,
                source_weight=count,
                my_simulation_parameters=my_simulation_parameters,
                smart_devices_included=smart_devices_included,
            )
        )
        my_sim.add_component(my_smart_devices[-1])
        count += 1

    return my_smart_devices, count


def configure_cars(
    my_sim: Any,
    my_simulation_parameters: SimulationParameters,
    count: int,
    ev_included: bool,
    occupancy_config: Any,
) -> Tuple[List[generic_car.Car], int]:
    """Sets smart devices without controllers.

    Parameters
    ----------
    my_sim: str
        filename of orginal built example.
    my_simulation_parameters: SimulationParameters
        The simulation parameters.
    count: int
        Integer tracking component hierachy for EMS.
    ev_included: bool
        True if Car is electric, False if it is diesel.
    occupancy_config: loadprofilegenerator_connector.OccupancyConfig
        Unique description of load profile generator call (mobility is related!)


    """
    # get names of all available cars
    filepaths = listdir(utils.HISIMPATH["utsp_results"])
    filepaths_location = [elem for elem in filepaths if "CarLocation." in elem]
    names = [elem.partition(",")[0].partition(".")[2] for elem in filepaths_location]

    # decide if they are diesel driven or electricity driven
    if ev_included:
        my_car_config = generic_car.CarConfig.get_default_ev_config()
    else:
        my_car_config = generic_car.CarConfig.get_default_diesel_config()

    # create all cars
    my_cars: List[generic_car.Car] = []
    for car in names:
        my_car_config.name = car
        my_car_config.source_weight = count
        my_cars.append(
            generic_car.Car(
                my_simulation_parameters=my_simulation_parameters,
                config=my_car_config,
                occupancy_config=occupancy_config,
            )
        )
        my_sim.add_component(my_cars[-1])
        count += 1

    return my_cars, count


def configure_ev_batteries(
    my_sim: Any,
    my_simulation_parameters: SimulationParameters,
    my_cars: List[generic_car.Car],
    charging_station_set: Optional[JsonReference],
    mobility_set: JsonReference,
    my_electricity_controller: controller_l2_energy_management_system.L2GenericEnergyManagementSystem,
    clever: bool,
) -> List:
    """Sets batteries and controllers of electric vehicles.

    Parameters
    ----------
    my_sim: str
        filename of orginal built example.
    my_simulation_parameters: SimulationParameters
        The simulation parameters.
    my_cars: List[Car]
        List of initilized cars.
    charging_station_set: ChargingStationSets
        Encoding of the charging station.
    mobility_set: TransportationDeviceSets
        Encoding of the available cars.
    my_electricity_controller: L2GenericEnergyManagementSystem
        The initialized electricity controller.
    clever: bool
        True if battery of electric vehicle is charged with surplus.

    """
    ev_capacities = []

    if mobility_set.Name is None:
        raise Exception("For EV configuration mobility set is obligatory.")
    mobility_speed = (
        mobility_set.Name.partition("and ")[2].partition(" ")[2].partition(" km/h")[0]
    )
    if mobility_speed == "30":
        car_battery_config = (
            advanced_ev_battery_bslib.CarBatteryConfig.get_default_config(
                e_bat_custom=30, p_inv_custom=5000, name="CarBattery"
            )
        )
        ev_capacities.append(30)
    elif mobility_speed == "60":
        car_battery_config = (
            advanced_ev_battery_bslib.CarBatteryConfig.get_default_config(
                e_bat_custom=50, p_inv_custom=11000, name="CarBattery"
            )
        )
        ev_capacities.append(50)
    if charging_station_set is None:
        raise Exception("For EV configuration charging station set is obligatory.")

    car_battery_controller_config = (
        controller_l1_generic_ev_charge.ChargingStationConfig.get_default_config(
            charging_station_set=charging_station_set
        )
    )

    if clever:
        car_battery_controller_config.battery_set = (
            0.4  # lower threshold for soc of car battery in clever case
        )

    for car in my_cars:
        car_battery_config.source_weight = car.source_weight
        car_battery_controller_config.source_weight = car.source_weight
        my_carbattery = advanced_ev_battery_bslib.CarBattery(
            my_simulation_parameters=my_simulation_parameters, config=car_battery_config
        )
        my_controller_carbattery = controller_l1_generic_ev_charge.L1Controller(
            my_simulation_parameters=my_simulation_parameters,
            config=car_battery_controller_config,
        )
        my_controller_carbattery.connect_only_predefined_connections(car)
        my_controller_carbattery.connect_only_predefined_connections(my_carbattery)
        my_carbattery.connect_only_predefined_connections(my_controller_carbattery)
        my_sim.add_component(my_carbattery)
        my_sim.add_component(my_controller_carbattery)

        if clever:
            my_electricity_controller.add_component_input_and_connect(
                source_component_class=my_carbattery,
                source_component_output=my_carbattery.AcBatteryPower,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[
                    lt.ComponentType.CAR_BATTERY,
                    lt.InandOutputType.ELECTRICITY_REAL,
                ],
                source_weight=my_carbattery.source_weight,
            )

            electricity_target = my_electricity_controller.add_component_output(
                source_output_name=lt.InandOutputType.ELECTRICITY_TARGET,
                source_tags=[
                    lt.ComponentType.CAR_BATTERY,
                    lt.InandOutputType.ELECTRICITY_TARGET,
                ],
                source_weight=my_controller_carbattery.source_weight,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                output_description="Target Electricity for EV Battery Controller. ",
            )

            my_controller_carbattery.connect_dynamic_input(
                input_fieldname=controller_l1_generic_ev_charge.L1Controller.ElectricityTarget,
                src_object=electricity_target,
            )

    return ev_capacities


def configure_smart_controller_for_smart_devices(
    my_electricity_controller: controller_l2_energy_management_system.L2GenericEnergyManagementSystem,
    my_smart_devices: List[generic_smart_device.SmartDevice],
) -> None:
    """Sets l3 controller for smart devices.

    Parameters
    ----------
    my_electricity_controller: L2GenericEnergyManagementSystem
        The initialized electricity controller.
    my_smart_devices: List[SmartDevice]
        List of initilized smart devices.

    """

    for elem in my_smart_devices:
        my_electricity_controller.add_component_input_and_connect(
            source_component_class=elem,
            source_component_output=elem.ElectricityOutput,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            source_tags=[
                lt.ComponentType.SMART_DEVICE,
                lt.InandOutputType.ELECTRICITY_REAL,
            ],
            source_weight=elem.source_weight,
        )

        electricity_to_smart_device = my_electricity_controller.add_component_output(
            source_output_name=lt.InandOutputType.ELECTRICITY_TARGET,
            source_tags=[
                lt.ComponentType.SMART_DEVICE,
                lt.InandOutputType.ELECTRICITY_TARGET,
            ],
            source_weight=elem.source_weight,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            output_description="Target electricity for Smart Device Controller. ",
        )

        elem.connect_dynamic_input(
            input_fieldname=generic_smart_device.SmartDevice.ElectricityTarget,
            src_object=electricity_to_smart_device,
        )


def configure_battery(
    my_sim: Any,
    my_simulation_parameters: SimulationParameters,
    my_electricity_controller: controller_l2_energy_management_system.L2GenericEnergyManagementSystem,
    battery_capacity: Optional[float],
    count: int,
) -> int:
    """Sets advanced battery system with surplus controller.

    Parameters
    ----------
    my_sim: str
        filename of orginal built example.
    my_simulation_parameters: SimulationParameters
        The simulation parameters.
    my_electricity_controller: L2GenericEnergyManagementSystem
        The initialized electricity controller.
    battery_capacity: float or None
        Capacity of the battery in Wh. In case of None default is used.
    count: int
        Integer tracking component hierachy for EMS.

    """
    if battery_capacity is not None:
        my_advanced_battery_config = (
            advanced_battery_bslib.BatteryConfig(
                e_bat_custom=battery_capacity,
                p_inv_custom=battery_capacity * 0.5 * 1e3,
                source_weight=count,
                system_id='SG1',
                name='Battery',
            )
        )
    else:
        my_advanced_battery_config = (
            advanced_battery_bslib.BatteryConfig.get_default_config()
        )
        my_advanced_battery_config.source_weight = count
    count += 1
    my_advanced_battery = advanced_battery_bslib.Battery(
        my_simulation_parameters=my_simulation_parameters,
        config=my_advanced_battery_config,
    )

    my_electricity_controller.add_component_input_and_connect(
        source_component_class=my_advanced_battery,
        source_component_output=my_advanced_battery.AcBatteryPower,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=[lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_REAL],
        source_weight=my_advanced_battery.source_weight,
    )

    electricity_to_or_from_battery_target = (
        my_electricity_controller.add_component_output(
            source_output_name=lt.InandOutputType.ELECTRICITY_TARGET,
            source_tags=[
                lt.ComponentType.BATTERY,
                lt.InandOutputType.ELECTRICITY_TARGET,
            ],
            source_weight=my_advanced_battery.source_weight,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            output_description="Target electricity for Battery Control. ",
        )
    )

    my_advanced_battery.connect_dynamic_input(
        input_fieldname=advanced_battery_bslib.Battery.LoadingPowerInput,
        src_object=electricity_to_or_from_battery_target,
    )
    my_sim.add_component(my_advanced_battery)

    return count


def configure_water_heating(
    my_sim: Any,
    my_simulation_parameters: SimulationParameters,
    my_occupancy: loadprofilegenerator_connector.Occupancy,
    water_heating_system_installed: lt.HeatingSystems,
    count: int,
) -> int:
    """Sets Boiler with Heater, L1 Controller and L2 Controller for Water Heating System.

    Parameters
    ----------
    my_sim: str
        filename of orginal built example.
    my_simulation_parameters: SimulationParameters
        The simulation parameters.
    my_occupancy: Occupancy
        The initialized occupancy component.
    water_heating_system_installed: str
        Type of installed WaterHeatingSystem
    count: int
        Integer tracking component hierachy for EMS.

    """
    fuel_translator = {
        lt.HeatingSystems.GAS_HEATING: lt.LoadTypes.GAS,
        lt.HeatingSystems.OIL_HEATING: lt.LoadTypes.OIL,
        lt.HeatingSystems.DISTRICT_HEATING: lt.LoadTypes.DISTRICTHEATING,
    }
    heater_config = (
        generic_heat_source.HeatSourceConfig.get_default_config_waterheating()
    )
    heater_config.fuel = fuel_translator[water_heating_system_installed]
    heater_l1_config = controller_l1_heatpump.L1HeatPumpConfig.get_default_config_heat_source_controller_dhw(
        "DHW" + water_heating_system_installed.value
    )
    [heater_config.source_weight, heater_l1_config.source_weight] = [count] * 2
    count += 1
    boiler_config = (
        generic_hot_water_storage_modular.StorageConfig.get_default_config_boiler()
    )
    boiler_config.compute_default_cycle(temperature_difference_in_kelvin=heater_l1_config.t_max_heating_in_celsius - heater_l1_config.t_min_heating_in_celsius)

    heater_config.power_th = (
        my_occupancy.max_hot_water_demand
        * (4180 / 3600)
        * 0.5
        * (3600 / my_simulation_parameters.seconds_per_timestep)
        * (
            HouseholdWarmWaterDemandConfig.ww_temperature_demand
            - HouseholdWarmWaterDemandConfig.freshwater_temperature
        )
    )

    my_boiler = generic_hot_water_storage_modular.HotWaterStorage(
        my_simulation_parameters=my_simulation_parameters, config=boiler_config
    )
    my_boiler.connect_only_predefined_connections(my_occupancy)
    my_sim.add_component(my_boiler)

    my_heater_controller_l1 = controller_l1_heatpump.L1HeatPumpController(
        my_simulation_parameters=my_simulation_parameters, config=heater_l1_config
    )
    my_heater_controller_l1.connect_only_predefined_connections(my_boiler)
    my_sim.add_component(my_heater_controller_l1)

    my_heater = generic_heat_source.HeatSource(
        config=heater_config, my_simulation_parameters=my_simulation_parameters
    )
    my_heater.connect_only_predefined_connections(my_heater_controller_l1)

    my_sim.add_component(my_heater)

    my_boiler.connect_only_predefined_connections(my_heater)
    return count


def configure_water_heating_electric(
    my_sim: Any,
    my_simulation_parameters: SimulationParameters,
    my_occupancy: loadprofilegenerator_connector.Occupancy,
    my_electricity_controller: controller_l2_energy_management_system.L2GenericEnergyManagementSystem,
    my_weather: weather.Weather,
    water_heating_system_installed: lt.HeatingSystems,
    controlable: bool,
    count: int,
) -> int:
    """Sets Boiler with Heater, L1 Controller and L2 Controller for Water Heating System.

    Parameters
    ----------
    my_sim: str
        filename of orginal built example.
    my_simulation_parameters: SimulationParameters
        The simulation parameters.
    my_occupancy: Occupancy
        The initialized occupancy component.
    my_electricity_controller: L2GenericEnergyManagementSystem
        The initialized electricity controller.
    my_weather: Weather
        The initialized Weather component.
    water_heating_system_installed: str
        Type of installed WaterHeatingSystem
    controlable: bool
        True if control of heating device is smart, False if not.
    count: int
        Integer tracking component hierachy for EMS.

    """
    if water_heating_system_installed == lt.HeatingSystems.HEAT_PUMP:
        heatpump_config = (
            generic_heat_pump_modular.HeatPumpConfig.get_default_config_waterheating()
        )
        heatpump_l1_config = controller_l1_heatpump.L1HeatPumpConfig.get_default_config_heat_source_controller_dhw(
            "DHWHeatPumpController"
        )
    elif water_heating_system_installed == lt.HeatingSystems.ELECTRIC_HEATING:
        heatpump_config = (
            generic_heat_pump_modular.HeatPumpConfig.get_default_config_waterheating_electric()
        )
        heatpump_l1_config = controller_l1_heatpump.L1HeatPumpConfig.get_default_config_heat_source_controller_dhw(
            "BoilerHeatingController"
        )

    [heatpump_config.source_weight, heatpump_l1_config.source_weight] = [count] * 2
    count += 1

    heatpump_config.power_th = (
        my_occupancy.max_hot_water_demand
        * (4180 / 3600)
        * 0.5
        * (3600 / my_simulation_parameters.seconds_per_timestep)
        * (
            HouseholdWarmWaterDemandConfig.ww_temperature_demand
            - HouseholdWarmWaterDemandConfig.freshwater_temperature
        )
    )
    boiler_config = (
        generic_hot_water_storage_modular.StorageConfig.get_default_config_boiler()
    )
    boiler_config.compute_default_cycle(temperature_difference_in_kelvin=heatpump_l1_config.t_max_heating_in_celsius - heatpump_l1_config.t_min_heating_in_celsius)

    my_boiler = generic_hot_water_storage_modular.HotWaterStorage(
        my_simulation_parameters=my_simulation_parameters, config=boiler_config
    )
    my_boiler.connect_only_predefined_connections(my_occupancy)
    my_sim.add_component(my_boiler)

    my_heatpump_controller_l1 = controller_l1_heatpump.L1HeatPumpController(
        my_simulation_parameters=my_simulation_parameters, config=heatpump_l1_config
    )
    my_heatpump_controller_l1.connect_only_predefined_connections(my_boiler)
    my_sim.add_component(my_heatpump_controller_l1)

    my_heatpump = generic_heat_pump_modular.ModularHeatPump(
        config=heatpump_config, my_simulation_parameters=my_simulation_parameters
    )
    my_heatpump.connect_only_predefined_connections(my_weather)
    my_heatpump.connect_only_predefined_connections(my_heatpump_controller_l1)
    my_sim.add_component(my_heatpump)
    my_boiler.connect_only_predefined_connections(my_heatpump)

    if controlable:
        my_heatpump_controller_l1.connect_input(
            my_heatpump_controller_l1.StorageTemperatureModifier,
            my_electricity_controller.component_name,
            my_electricity_controller.StorageTemperatureModifier,
        )
        my_electricity_controller.add_component_input_and_connect(
            source_component_class=my_heatpump,
            source_component_output=my_heatpump.ElectricityOutput,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            source_tags=[
                lt.ComponentType.HEAT_PUMP,
                lt.InandOutputType.ELECTRICITY_REAL,
            ],
            source_weight=my_heatpump.config.source_weight,
        )

        my_electricity_controller.add_component_output(
            source_output_name=lt.InandOutputType.ELECTRICITY_TARGET,
            source_tags=[
                lt.ComponentType.HEAT_PUMP,
                lt.InandOutputType.ELECTRICITY_TARGET,
            ],
            source_weight=my_heatpump.config.source_weight,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            output_description="Target electricity for heat pump.",
        )

    else:
        my_electricity_controller.add_component_input_and_connect(
            source_component_class=my_heatpump,
            source_component_output=my_heatpump.ElectricityOutput,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            source_tags=[lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
            source_weight=999,
        )
    return count


def configure_heating(
    my_sim: Any,
    my_simulation_parameters: SimulationParameters,
    my_building: building.Building,
    heating_system_installed: lt.HeatingSystems,
    heating_season: List[int],
    count: int,
) -> Tuple[Component, int]:
    """Sets Heater, L1 Controller and L2 Controller for Heating System.

    Parameters
    ----------
    my_sim: str
        filename of orginal built example.
    my_simulation_parameters: SimulationParameters
        The simulation parameters.
    my_building: Building
        The initialized building component.
    heating_system_installed: str
        Type of installed HeatingSystem
    heating_season: List[int]
        Contains first and last day of heating season.
    count: int
        Integer tracking component hierachy for EMS.

    """
    fuel_translator = {
        lt.HeatingSystems.GAS_HEATING: lt.LoadTypes.GAS,
        lt.HeatingSystems.OIL_HEATING: lt.LoadTypes.OIL,
        lt.HeatingSystems.DISTRICT_HEATING: lt.LoadTypes.DISTRICTHEATING,
    }
    heater_config = generic_heat_source.HeatSourceConfig.get_default_config_heating()
    heater_config.fuel = fuel_translator[heating_system_installed]
    heater_l1_config = controller_l1_heatpump.L1HeatPumpConfig.get_default_config_heat_source_controller(
        heating_system_installed.value
    )

    # set power of heating system according to maximal power demand
    heater_config.power_th = my_building.max_thermal_building_demand_in_watt
    heater_l1_config.day_of_heating_season_end = heating_season[0]
    heater_l1_config.day_of_heating_season_begin = heating_season[1]

    [heater_config.source_weight, heater_l1_config.source_weight] = [count] * 2
    count += 1

    my_heater_controller_l1 = controller_l1_heatpump.L1HeatPumpController(
        my_simulation_parameters=my_simulation_parameters, config=heater_l1_config
    )
    my_heater_controller_l1.connect_only_predefined_connections(my_building)
    my_sim.add_component(my_heater_controller_l1)

    my_heater = generic_heat_source.HeatSource(
        config=heater_config, my_simulation_parameters=my_simulation_parameters
    )
    my_heater.connect_only_predefined_connections(my_heater_controller_l1)
    my_sim.add_component(my_heater)

    my_building.connect_input(
        input_fieldname=my_building.ThermalPowerDelivered,
        src_object_name=my_heater.component_name,
        src_field_name=my_heater.ThermalPowerDelivered
    )

    return my_heater, count


def configure_heating_electric(
    my_sim: Any,
    my_simulation_parameters: SimulationParameters,
    my_building: building.Building,
    my_electricity_controller: controller_l2_energy_management_system.L2GenericEnergyManagementSystem,
    my_weather: weather.Weather,
    heating_system_installed: lt.HeatingSystems,
    heatpump_power: float,
    controlable: bool,
    heating_season: List[int],
    count: int,
) -> Tuple[Component, int]:
    """Sets Heater, L1 Controller and L2 Controller for Heating System.

    Parameters
    ----------
    my_sim: str
        filename of orginal built example.
    my_simulation_parameters: SimulationParameters
        The simulation parameters.
    my_building: Building
        The initialized building component.
    my_electricity_controller: L2GenericEnergyManagementSystem
        The initialized electricity controller.
    my_weather: Weather
        The initialized Weather component.
    heating_system_installed: str
        Type of installed HeatingSystem
    heatpump_power: float,
        Power of heat pump in multiples of default.
    controlable: bool
        True if control of heating device is smart, False if not.
    heating_season: List[int]
        Contains first and last day of heating season.
    count: int
        Integer tracking component hierachy for EMS.

    """
    if heating_system_installed == lt.HeatingSystems.HEAT_PUMP:
        heatpump_config = (
            generic_heat_pump_modular.HeatPumpConfig.get_default_config_heating()
        )
        heatpump_l1_config = controller_l1_heatpump.L1HeatPumpConfig.get_default_config_heat_source_controller(
            "HeatigHeatPumpController"
        )
    elif heating_system_installed == lt.HeatingSystems.ELECTRIC_HEATING:
        heatpump_config = (
            generic_heat_pump_modular.HeatPumpConfig.get_default_config_heating_electric()
        )
        heatpump_l1_config = controller_l1_heatpump.L1HeatPumpConfig.get_default_config_heat_source_controller(
            "ElectricHeatingController"
        )

    heatpump_config.power_th = my_building.max_thermal_building_demand_in_watt * heatpump_power
    heatpump_l1_config.day_of_heating_season_end = heating_season[0]
    heatpump_l1_config.day_of_heating_season_begin = heating_season[1]
    [heatpump_config.source_weight, heatpump_l1_config.source_weight] = [count] * 2
    count += 1

    my_heatpump_controller_l1 = controller_l1_heatpump.L1HeatPumpController(
        my_simulation_parameters=my_simulation_parameters, config=heatpump_l1_config
    )
    my_heatpump_controller_l1.connect_only_predefined_connections(my_building)
    my_sim.add_component(my_heatpump_controller_l1)

    my_heatpump = generic_heat_pump_modular.ModularHeatPump(
        config=heatpump_config, my_simulation_parameters=my_simulation_parameters
    )
    my_heatpump.connect_only_predefined_connections(my_weather)
    my_heatpump.connect_only_predefined_connections(my_heatpump_controller_l1)
    my_sim.add_component(my_heatpump)

    if controlable:
        my_heatpump_controller_l1.connect_input(
            my_heatpump_controller_l1.StorageTemperatureModifier,
            my_electricity_controller.component_name,
            my_electricity_controller.BuildingTemperatureModifier,
        )
        my_electricity_controller.add_component_input_and_connect(
            source_component_class=my_heatpump,
            source_component_output=my_heatpump.ElectricityOutput,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            source_tags=[
                lt.ComponentType.HEAT_PUMP,
                lt.InandOutputType.ELECTRICITY_REAL,
            ],
            source_weight=my_heatpump.config.source_weight,
        )

        my_electricity_controller.add_component_output(
            source_output_name=lt.InandOutputType.ELECTRICITY_TARGET,
            source_tags=[
                lt.ComponentType.HEAT_PUMP,
                lt.InandOutputType.ELECTRICITY_TARGET,
            ],
            source_weight=my_heatpump.config.source_weight,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            output_description="Target electricity for HeatingHeat Pump. ",
        )
    else:
        my_electricity_controller.add_component_input_and_connect(
            source_component_class=my_heatpump,
            source_component_output=my_heatpump.ElectricityOutput,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            source_tags=[lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
            source_weight=999,
        )

    my_building.connect_input(
        input_fieldname=my_building.ThermalPowerDelivered,
        src_object_name=my_heatpump.component_name,
        src_field_name=my_heatpump.ThermalPowerDelivered
    )

    return my_heatpump, count


def configure_heating_with_buffer_electric(
    my_sim: Any,
    my_simulation_parameters: SimulationParameters,
    my_building: building.Building,
    my_electricity_controller: controller_l2_energy_management_system.L2GenericEnergyManagementSystem,
    my_weather: weather.Weather,
    heating_system_installed: lt.HeatingSystems,
    heatpump_power: float,
    buffer_volume: float,
    controlable: bool,
    heating_season: List[int],
    count: int,
) -> Tuple:
    """Sets Heater, L1 Controller and L2 Controller for Heating System.

    Parameters
    ----------
    my_sim: str
        filename of orginal built example.
    my_simulation_parameters: SimulationParameters
        The simulation parameters.
    my_building: Building
        The initialized building component.
    my_electricity_controller: L2GenericEnergyManagementSystem
        The initialized electricity controller.
    my_weather: Weather
        The initialized Weather component.
    heating_system_installed: str
        Type of installed HeatingSystem.
    heatpump_power: float
        Power of heat pump in multiples of default.
    buffer_volume: float
        Volume of buffer storage in multiples of default.
    controlable: bool
        True if control of heating device is smart, False if not.
    heating_season: List[int]
        Contains first and last day of heating season.
    count: int
        Integer tracking component hierachy for EMS.

    """
    if heating_system_installed == lt.HeatingSystems.HEAT_PUMP:
        heatpump_config = (
            generic_heat_pump_modular.HeatPumpConfig.get_default_config_heating()
        )
        heatpump_l1_config = controller_l1_heatpump.L1HeatPumpConfig.get_default_config_heat_source_controller_buffer(
            "BufferHeatPumpController"
        )
    elif heating_system_installed == lt.HeatingSystems.ELECTRIC_HEATING:
        heatpump_config = (
            generic_heat_pump_modular.HeatPumpConfig.get_default_config_heating_electric()
        )
        heatpump_l1_config = controller_l1_heatpump.L1HeatPumpConfig.get_default_config_heat_source_controller_buffer(
            "BufferElectricHeatingController"
        )

    heatpump_config.power_th = my_building.max_thermal_building_demand_in_watt * heatpump_power
    heatpump_l1_config.day_of_heating_season_end = heating_season[0] + 1
    heatpump_l1_config.day_of_heating_season_begin = heating_season[1] - 1
    [heatpump_config.source_weight, heatpump_l1_config.source_weight] = [count] * 2
    count += 1

    buffer_config = (
        generic_hot_water_storage_modular.StorageConfig.get_default_config_buffer(power=float(my_building.max_thermal_building_demand_in_watt))
    )
    buffer_config.compute_default_volume(
        time_in_seconds=heatpump_l1_config.min_idle_time_in_seconds,
        temperature_difference_in_kelvin=heatpump_l1_config.t_max_heating_in_celsius - heatpump_l1_config.t_min_heating_in_celsius,
        multiplier=buffer_volume
    )
    buffer_config.compute_default_cycle(temperature_difference_in_kelvin=heatpump_l1_config.t_max_heating_in_celsius - heatpump_l1_config.t_min_heating_in_celsius)

    building_heating_controller_config = controller_l1_building_heating.L1BuildingHeatingConfig.get_default_config_heating(
        "buffer"
    )
    building_heating_controller_config.day_of_heating_season_end = heating_season[0]
    building_heating_controller_config.day_of_heating_season_begin = heating_season[1]
    building_heating_controller_config.t_buffer_activation_threshold_in_celsius = heatpump_l1_config.t_max_heating_in_celsius
    [buffer_config.source_weight, building_heating_controller_config.source_weight] = [
        count
    ] * 2
    count += 1

    my_buffer = generic_hot_water_storage_modular.HotWaterStorage(
        my_simulation_parameters=my_simulation_parameters, config=buffer_config
    )
    my_sim.add_component(my_buffer)

    my_heatpump_controller_l1 = controller_l1_heatpump.L1HeatPumpController(
        my_simulation_parameters=my_simulation_parameters, config=heatpump_l1_config
    )
    my_heatpump_controller_l1.connect_only_predefined_connections(my_buffer)
    my_sim.add_component(my_heatpump_controller_l1)
    my_heatpump = generic_heat_pump_modular.ModularHeatPump(
        config=heatpump_config, my_simulation_parameters=my_simulation_parameters
    )
    my_heatpump.connect_only_predefined_connections(my_weather)
    my_heatpump.connect_only_predefined_connections(my_heatpump_controller_l1)
    my_sim.add_component(my_heatpump)

    my_buffer_controller = controller_l1_building_heating.L1BuildingHeatController(
    my_simulation_parameters=my_simulation_parameters,
    config=building_heating_controller_config,
)
    my_buffer_controller.connect_only_predefined_connections(my_building)
    my_buffer_controller.connect_only_predefined_connections(my_buffer)
    my_sim.add_component(my_buffer_controller)

    if controlable:
        my_heatpump_controller_l1.connect_input(
            my_heatpump_controller_l1.StorageTemperatureModifier,
            my_electricity_controller.component_name,
            my_electricity_controller.StorageTemperatureModifier,
        )
        my_buffer_controller.connect_input(
            my_buffer_controller.BuildingTemperatureModifier,
            my_electricity_controller.component_name,
            my_electricity_controller.BuildingTemperatureModifier
        )

        my_electricity_controller.add_component_input_and_connect(
            source_component_class=my_heatpump,
            source_component_output=my_heatpump.ElectricityOutput,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            source_tags=[
                lt.ComponentType.HEAT_PUMP,
                lt.InandOutputType.ELECTRICITY_REAL,
            ],
            source_weight=my_heatpump.config.source_weight,
        )

        my_electricity_controller.add_component_output(
            source_output_name=lt.InandOutputType.ELECTRICITY_TARGET,
            source_tags=[
                lt.ComponentType.HEAT_PUMP,
                lt.InandOutputType.ELECTRICITY_TARGET,
            ],
            source_weight=my_heatpump.config.source_weight,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            output_description="Target electricity for HeatingHeat Pump. ",
        )

    else:
        my_electricity_controller.add_component_input_and_connect(
            source_component_class=my_heatpump,
            source_component_output=my_heatpump.ElectricityOutput,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            source_tags=[lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
            source_weight=999,
        )

    my_buffer_controller.connect_only_predefined_connections(my_electricity_controller)
    my_buffer.connect_only_predefined_connections(my_buffer_controller)
    my_buffer.connect_only_predefined_connections(my_heatpump)
    my_building.connect_input(
        input_fieldname=my_building.ThermalPowerDelivered,
        src_object_name=my_buffer.component_name,
        src_field_name=my_buffer.PowerFromHotWaterStorage
    )

    return my_heatpump, my_buffer, count


def configure_heating_with_buffer(
    my_sim: Any,
    my_simulation_parameters: SimulationParameters,
    my_building: building.Building,
    heating_system_installed: lt.HeatingSystems,
    buffer_volume: float,
    heating_season: List[int],
    count: int,
) -> Tuple:
    """Sets Heater, L1 Controller and L2 Controller for Heating System.

    Parameters
    ----------
    my_sim: str
        filename of orginal built example.
    my_simulation_parameters: SimulationParameters
        The simulation parameters.
    my_building: Building
        The initialized building component.
    my_weather: Weather
        The initialized Weather component.
    heating_system_installed: str
        Type of installed HeatingSystem.
    buffer_volume: float
        Volume of buffer storage in multiples of default. In case of None one is used.
    heating_season: List[int]
        Contains first and last day of heating season.
    count: int
        Integer tracking component hierachy for EMS.

    """
    fuel_translator = {
        lt.HeatingSystems.GAS_HEATING: lt.LoadTypes.GAS,
        lt.HeatingSystems.OIL_HEATING: lt.LoadTypes.OIL,
        lt.HeatingSystems.DISTRICT_HEATING: lt.LoadTypes.DISTRICTHEATING,
    }
    heater_config = generic_heat_source.HeatSourceConfig.get_default_config_heating()
    heater_config.fuel = fuel_translator[heating_system_installed]
    heater_config.power_th = my_building.max_thermal_building_demand_in_watt
    heater_l1_config = controller_l1_heatpump.L1HeatPumpConfig.get_default_config_heat_source_controller_buffer(
        "Buffer" + heating_system_installed.value + "Controller"
    )

    heater_l1_config.day_of_heating_season_end = heating_season[0] + 1
    heater_l1_config.day_of_heating_season_begin = heating_season[1] - 1
    [heater_config.source_weight, heater_l1_config.source_weight] = [count] * 2
    count += 1

    buffer_config = (
        generic_hot_water_storage_modular.StorageConfig.get_default_config_buffer(power=float(my_building.max_thermal_building_demand_in_watt))
    )
    buffer_config.compute_default_volume(
        time_in_seconds=heater_l1_config.min_idle_time_in_seconds,
        temperature_difference_in_kelvin=heater_l1_config.t_max_heating_in_celsius - heater_l1_config.t_min_heating_in_celsius,
        multiplier=buffer_volume
    )
    buffer_config.compute_default_cycle(temperature_difference_in_kelvin=heater_l1_config.t_max_heating_in_celsius - heater_l1_config.t_min_heating_in_celsius)

    building_heating_controller_config = controller_l1_building_heating.L1BuildingHeatingConfig.get_default_config_heating(
        "buffer"
    )
    building_heating_controller_config.day_of_heating_season_end = heating_season[0]
    building_heating_controller_config.day_of_heating_season_begin = heating_season[1] - 1
    building_heating_controller_config.t_buffer_activation_threshold_in_celsius = heater_l1_config.t_max_heating_in_celsius
    [buffer_config.source_weight, building_heating_controller_config.source_weight] = [
        count
    ] * 2
    count += 1

    my_buffer = generic_hot_water_storage_modular.HotWaterStorage(
        my_simulation_parameters=my_simulation_parameters, config=buffer_config
    )
    my_sim.add_component(my_buffer)
    my_heater_controller_l1 = controller_l1_heatpump.L1HeatPumpController(
        my_simulation_parameters=my_simulation_parameters, config=heater_l1_config
    )
    my_heater_controller_l1.connect_only_predefined_connections(my_buffer)
    my_sim.add_component(my_heater_controller_l1)

    my_heater = generic_heat_source.HeatSource(
        config=heater_config, my_simulation_parameters=my_simulation_parameters
    )
    my_heater.connect_only_predefined_connections(my_heater_controller_l1)
    my_sim.add_component(my_heater)

    my_buffer_controller = controller_l1_building_heating.L1BuildingHeatController(
        my_simulation_parameters=my_simulation_parameters,
        config=building_heating_controller_config,
    )
    my_buffer_controller.connect_only_predefined_connections(my_building)
    my_sim.add_component(my_buffer_controller)
    my_buffer.connect_only_predefined_connections(my_buffer_controller)
    my_buffer.connect_only_predefined_connections(my_heater)
    my_building.connect_input(
        input_fieldname=my_building.ThermalPowerDelivered,
        src_object_name=my_buffer.component_name,
        src_field_name=my_buffer.PowerFromHotWaterStorage
    )

    return my_heater, my_buffer, count


def configure_elctrolysis_h2storage_chp_system(
    my_sim: Any,
    my_simulation_parameters: SimulationParameters,
    my_building: building.Building,
    my_electricity_controller: controller_l2_energy_management_system.L2GenericEnergyManagementSystem,
    chp_power: Optional[float],
    h2_storage_size: Optional[float],
    electrolyzer_power: Optional[float],
    count: int,
) -> Tuple[generic_CHP.GCHP, int]:
    """Sets electrolysis, H2-storage and chp system.

    Parameters
    ----------
    my_sim: str
        filename of orginal built example.
    my_simulation_parameters: SimulationParameters
        The simulation parameters.
    my_building: Building
        The initialized building component.
    my_electricity_controller: L2GenericEnergyManagementSystem
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
    l2_config = controller_l2_generic_heat_simple.L2GenericHeatConfig.get_default_config_heating(
        "chp"
    )
    l2_config.source_weight = count
    l1_config_chp = generic_CHP.L1CHPConfig.get_default_config()
    l1_config_chp.source_weight = count
    if chp_power is not None:
        chp_config = generic_CHP.GCHPConfig(
            name="CHP",
            source_weight=count,
            p_el=0.3 * chp_power,
            p_th=0.5 * chp_power,
            p_fuel=chp_power,
        )
    else:
        chp_config = generic_CHP.GCHPConfig.get_default_config()
        chp_config.source_weight = count
    count += 1

    # fuel cell
    my_chp = generic_CHP.GCHP(
        my_simulation_parameters=my_simulation_parameters, config=chp_config
    )
    my_sim.add_component(my_chp)

    # heat controller of fuel cell
    my_chp_controller_l2 = controller_l2_generic_heat_simple.L2GenericHeatController(
        my_simulation_parameters=my_simulation_parameters, config=l2_config
    )
    my_chp_controller_l2.connect_only_predefined_connections(my_building)
    my_sim.add_component(my_chp_controller_l2)

    # run time controller of fuel cell
    my_chp_controller_l1 = generic_CHP.L1GenericCHPRuntimeController(
        my_simulation_parameters=my_simulation_parameters, config=l1_config_chp
    )
    my_chp_controller_l1.connect_only_predefined_connections(my_chp_controller_l2)
    my_sim.add_component(my_chp_controller_l1)
    my_chp.connect_only_predefined_connections(my_chp_controller_l1)

    # electricity controller of fuel cell
    my_electricity_controller.add_component_input_and_connect(
        source_component_class=my_chp,
        source_component_output=my_chp.ElectricityOutput,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=[lt.ComponentType.FUEL_CELL, lt.InandOutputType.ELECTRICITY_REAL],
        source_weight=my_chp.source_weight,
    )
    electricity_from_fuelcell_target = my_electricity_controller.add_component_output(
        source_output_name=lt.InandOutputType.ELECTRICITY_TARGET,
        source_tags=[lt.ComponentType.FUEL_CELL, lt.InandOutputType.ELECTRICITY_TARGET],
        source_weight=my_chp.source_weight,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        output_description="Target electricity for Fuel Cell. ",
    )
    my_chp_controller_l1.connect_dynamic_input(
        input_fieldname=generic_CHP.L1GenericCHPRuntimeController.ElectricityTarget,
        src_object=electricity_from_fuelcell_target,
    )

    # electrolyzer default configuration
    l1_config_electrolyzer = (
        generic_electrolyzer.L1ElectrolyzerConfig.get_default_config()
    )
    l1_config_electrolyzer.source_weight = count
    if electrolyzer_power is not None:
        electrolyzer_config = generic_electrolyzer.GenericElectrolyzerConfig(
            name="Electrolyzer",
            source_weight=count,
            min_power=0.5 * electrolyzer_power,
            max_power=electrolyzer_power,
            min_hydrogen_production_rate_hour=0.125 * electrolyzer_power,
            max_hydrogen_production_rate_hour=2 * electrolyzer_power,
        )
    else:
        electrolyzer_config = (
            generic_electrolyzer.GenericElectrolyzerConfig.get_default_config()
        )
        electrolyzer_config.source_weight = count
    count += 1

    # electrolyzer
    my_electrolyzer = generic_electrolyzer.GenericElectrolyzer(
        my_simulation_parameters=my_simulation_parameters, config=electrolyzer_config
    )
    my_sim.add_component(my_electrolyzer)

    # run time controller of electrolyzer
    my_electrolyzer_controller_l1 = (
        generic_electrolyzer.L1GenericElectrolyzerController(
            my_simulation_parameters=my_simulation_parameters,
            config=l1_config_electrolyzer,
        )
    )
    my_sim.add_component(my_electrolyzer_controller_l1)
    my_electrolyzer.connect_only_predefined_connections(my_electrolyzer_controller_l1)

    # electricity controller of fuel cell
    my_electricity_controller.add_component_input_and_connect(
        source_component_class=my_electrolyzer,
        source_component_output=my_electrolyzer.ElectricityOutput,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=[
            lt.ComponentType.ELECTROLYZER,
            lt.InandOutputType.ELECTRICITY_REAL,
        ],
        source_weight=my_electrolyzer.source_weight,
    )
    electricity_to_electrolyzer_target = my_electricity_controller.add_component_output(
        source_output_name=lt.InandOutputType.ELECTRICITY_TARGET,
        source_tags=[
            lt.ComponentType.ELECTROLYZER,
            lt.InandOutputType.ELECTRICITY_TARGET,
        ],
        source_weight=my_electrolyzer.source_weight,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        output_description="Target electricity for electrolyzer. ",
    )
    my_electrolyzer_controller_l1.connect_dynamic_input(
        input_fieldname=generic_electrolyzer.L1GenericElectrolyzerController.l2_ElectricityTarget,
        src_object=electricity_to_electrolyzer_target,
    )

    if h2_storage_size is not None:
        h2_storage_config = (
            generic_hydrogen_storage.GenericHydrogenStorageConfig.get_default_config(
                capacity=h2_storage_size,
                max_charging_rate=h2_storage_size * 1e-2,
                max_discharging_rate=h2_storage_size * 1e-2,
                source_weight=count,
            )
        )
    else:
        h2_storage_config = (
            generic_hydrogen_storage.GenericHydrogenStorageConfig.get_default_config(
                source_weight=count
            )
        )
    my_h2storage = generic_hydrogen_storage.GenericHydrogenStorage(
        my_simulation_parameters=my_simulation_parameters, config=h2_storage_config
    )
    my_h2storage.connect_only_predefined_connections(my_electrolyzer)
    my_h2storage.connect_only_predefined_connections(my_chp)
    my_sim.add_component(my_h2storage)

    my_electrolyzer_controller_l1.connect_only_predefined_connections(my_h2storage)
    my_chp_controller_l1.connect_only_predefined_connections(my_h2storage)

    return my_chp, count
