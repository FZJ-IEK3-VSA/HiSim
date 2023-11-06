# clean

""" Contains functions for initializing and connecting components.

The functions are all called in modular_household.
"""

import json
from os import listdir, path
from typing import Any, List, Optional, Tuple

import pandas as pd
from utspclient.helpers.lpgpythonbindings import JsonReference

import hisim.loadtypes as lt
from hisim import utils
from hisim.component import Component
from hisim.components import (advanced_battery_bslib,
                              advanced_ev_battery_bslib, building,
                              controller_l1_building_heating,
                              controller_l1_generic_ev_charge,
                              controller_l1_heatpump,
                              controller_l1_chp,
                              controller_l1_electrolyzer,
                              controller_l2_energy_management_system,
                              generic_car,
                              generic_chp, generic_electrolyzer,
                              generic_heat_pump_modular, generic_heat_source,
                              generic_hot_water_storage_modular,
                              generic_hydrogen_storage, generic_pv_system,
                              generic_smart_device,
                              loadprofilegenerator_connector, weather)
from hisim.components.configuration import HouseholdWarmWaterDemandConfig
from hisim.simulator import SimulationParameters


def get_heating_system_efficiency(
        heating_system_installed: lt.HeatingSystems, water_vs_heating: lt.InandOutputType
) -> float:
    """Reads in type of heating system and returns related efficiency values.

    :param heating_system_installed: type of installed heating system
    :type heating_system_installed: lt.HeatingSystems
    :param water_vs_heating: Heating vs. WaterHeating
    :type water_vs_heating: lt.InandOutputType
    :return: efficiency of the selected heater
    :rtype: float
    """

    efficiency_data = pd.read_csv(utils.HISIMPATH["heater_efficiencies"], encoding="utf-8", index_col=0).astype("float")

    return float(efficiency_data.loc[heating_system_installed.value][water_vs_heating.value])


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
            power=pv_peak_power, source_weight=count,
        )
    else:
        my_pv_system_config = generic_pv_system.PVSystem.get_default_config(
            source_weight=count,
        )
    my_pv_system_config.location = my_weather.weather_config.location
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
                config=generic_smart_device.SmartDeviceConfig(name="SmartDevice", identifier=device, source_weight=count, smart_devices_included=smart_devices_included),
                my_simulation_parameters=my_simulation_parameters,
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

    # create all cars
    my_cars: List[generic_car.Car] = []
    for _ in names:
        # decide if they are diesel driven or electricity driven and initialize config
        if ev_included:
            my_car_config = generic_car.CarConfig.get_default_ev_config()
        else:
            my_car_config = generic_car.CarConfig.get_default_diesel_config()
        # reset name and source weight
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
    controlable: bool,
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
    controlable: bool
        True if battery of electric vehicle is charged with surplus.

    """
    ev_capacities = []

    if mobility_set.Name is None:
        raise Exception("For EV configuration mobility set is obligatory.")

    if charging_station_set is not None:
        charging_power = float(
            (charging_station_set.Name or "").split("with ")[1].split(" kW")[0]
        )
    else:
        raise Exception("For EV configuration charging station set is obligatory.")

    for car in my_cars:
        car_battery_config = advanced_ev_battery_bslib.CarBatteryConfig.get_default_config()
        car_battery_config.source_weight = car.config.source_weight
        car_battery_config.p_inv_custom = charging_power * 1e3
        my_carbattery = advanced_ev_battery_bslib.CarBattery(
            my_simulation_parameters=my_simulation_parameters, config=car_battery_config
        )
        ev_capacities.append(car_battery_config.e_bat_custom)

        car_battery_controller_config = (
            controller_l1_generic_ev_charge.ChargingStationConfig.get_default_config(
                charging_station_set=charging_station_set
            )
        )
        car_battery_controller_config.source_weight = car.config.source_weight
        car_battery_controller_config.lower_threshold_charging_power = charging_power * 1e3 * 0.1  # 10 % of charging power for acceptable efficiencies
        if controlable:
            car_battery_controller_config.battery_set = (
                0.4  # lower threshold for soc of car battery in clever case
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

        if controlable:
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
                custom_battery_capacity_generic_in_kilowatt_hour=battery_capacity,
                custom_pv_inverter_power_generic_in_watt=battery_capacity * 0.5 * 1e3,
                source_weight=count,
                system_id='SG1',
                name='Battery',
                charge_in_kwh=0,
                discharge_in_kwh=0,
                co2_footprint=battery_capacity * 130.7,
                cost=battery_capacity * 535.81,
                lifetime=10,  # todo set correct values
                lifetime_in_cycles=5e3,  # todo set correct values
                maintenance_cost_as_percentage_of_investment=0.02,
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
    number_of_apartments: float
) -> Tuple[generic_hot_water_storage_modular.HotWaterStorage, int]:
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
    number_of_apartments: float
        from building component

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
    heater_config.efficiency = get_heating_system_efficiency(
        heating_system_installed=water_heating_system_installed, water_vs_heating=lt.InandOutputType.HEATING)
    heater_l1_config = controller_l1_heatpump.L1HeatPumpConfig.get_default_config_heat_source_controller_dhw(
        "DHW" + water_heating_system_installed.value
    )
    [heater_config.source_weight, heater_l1_config.source_weight] = [count] * 2
    count += 1
    boiler_config = (
        generic_hot_water_storage_modular.StorageConfig.get_scaled_config_for_boiler_to_number_of_apartments(number_of_apartments=number_of_apartments)
    )
    boiler_config.compute_default_cycle(temperature_difference_in_kelvin=heater_l1_config.t_max_heating_in_celsius - heater_l1_config.t_min_heating_in_celsius)

    heater_config.power_th = (
        my_occupancy.max_hot_water_demand
        * (4180 / 3600)
        * 0.5
        * (3600 / my_simulation_parameters.seconds_per_timestep)
        * (
            HouseholdWarmWaterDemandConfig.ww_temperature_demand
            - HouseholdWarmWaterDemandConfig.temperature_difference_hot
            - HouseholdWarmWaterDemandConfig.freshwater_temperature
        )
        * my_occupancy.scaling_factor_according_to_number_of_apartments
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
    return my_boiler, count


def configure_water_heating_electric(
    my_sim: Any,
    my_simulation_parameters: SimulationParameters,
    my_occupancy: loadprofilegenerator_connector.Occupancy,
    my_electricity_controller: controller_l2_energy_management_system.L2GenericEnergyManagementSystem,
    my_weather: weather.Weather,
    water_heating_system_installed: lt.HeatingSystems,
    controlable: bool,
    count: int,
    number_of_apartments: float
) -> Tuple[generic_hot_water_storage_modular.HotWaterStorage, int]:
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
    number_of_apartments: float
        from building component

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
            - HouseholdWarmWaterDemandConfig.temperature_difference_hot
            - HouseholdWarmWaterDemandConfig.freshwater_temperature
        )
        * my_occupancy.scaling_factor_according_to_number_of_apartments
    )
    boiler_config = (
        generic_hot_water_storage_modular.StorageConfig.get_scaled_config_for_boiler_to_number_of_apartments(number_of_apartments=number_of_apartments)
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
    return my_boiler, count


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
    heater_config.efficiency = get_heating_system_efficiency(
        heating_system_installed=heating_system_installed, water_vs_heating=lt.InandOutputType.HEATING)
    heater_l1_config = controller_l1_heatpump.L1HeatPumpConfig.get_default_config_heat_source_controller(
        heating_system_installed.value
    )

    # set power of heating system according to maximal power demand
    heater_config.power_th = my_building.my_building_information.max_thermal_building_demand_in_watt
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

    heatpump_config.power_th = my_building.my_building_information.max_thermal_building_demand_in_watt * heatpump_power
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

    heatpump_config.power_th = my_building.my_building_information.max_thermal_building_demand_in_watt * heatpump_power
    heatpump_l1_config.day_of_heating_season_end = heating_season[0] + 1
    heatpump_l1_config.day_of_heating_season_begin = heating_season[1] - 1
    [heatpump_config.source_weight, heatpump_l1_config.source_weight] = [count] * 2
    count += 1

    buffer_config = (
        generic_hot_water_storage_modular.StorageConfig.get_default_config_buffer(power=float(my_building.my_building_information.max_thermal_building_demand_in_watt))
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
    heater_config.power_th = my_building.my_building_information.max_thermal_building_demand_in_watt
    heater_config.efficiency = get_heating_system_efficiency(
        heating_system_installed=heating_system_installed, water_vs_heating=lt.InandOutputType.HEATING)
    heater_l1_config = controller_l1_heatpump.L1HeatPumpConfig.get_default_config_heat_source_controller_buffer(
        "Buffer" + heating_system_installed.value + "Controller"
    )

    heater_l1_config.day_of_heating_season_end = heating_season[0] + 1
    heater_l1_config.day_of_heating_season_begin = heating_season[1] - 1
    [heater_config.source_weight, heater_l1_config.source_weight] = [count] * 2
    count += 1

    buffer_config = (
        generic_hot_water_storage_modular.StorageConfig.get_default_config_buffer(power=float(my_building.my_building_information.max_thermal_building_demand_in_watt))
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


def configure_chp(my_sim: Any, my_simulation_parameters: SimulationParameters, my_building: building.Building,
                  my_boiler: generic_hot_water_storage_modular.HotWaterStorage,
                  my_electricity_controller: controller_l2_energy_management_system.L2GenericEnergyManagementSystem,
                  chp_power: float, controlable: bool, count: int, ) -> int:
    """Sets up natural gas CHP. It heats the DHW storage and the building in winter.

    :param my_sim: Simulation class.
    :type my_sim: Any
    :param my_simulation_parameters: Simulation parameters for HiSIM calculation.
    :type my_simulation_parameters: SimulationParameters
    :param my_building: Building of the HiSIM example.
    :type my_building: building.Building
    :param my_boiler: Hot water storage of the HiSIM example.
    :type my_boiler: generic_hot_water_storage_modular.HotWaterStorage
    :param my_electricity_controller: Energy Management System of the HiSIM example
    :type my_electricity_controller: controller_l2_energy_management_system.L2GenericEnergyManagementSystem
    :param chp_power: Power of the CHP in multiples of default (<=1).
    :type chp_power: float
    :param controlable: When True, surplus control of Energy Management System is activated.
    :type controlable: bool
    :param count: Number of component outputs relevant in the energy management system.
    :type count: int
    :return: New counter variable (+1).
    :rtype: int
    """

    # configure and add chp controller
    chp_controller_config = controller_l1_chp.L1CHPControllerConfig.get_default_config_chp()
    chp_controller_config.source_weight = count

    # size chp power to hot water storage size
    my_boiler.config.compute_default_cycle(
        temperature_difference_in_kelvin=chp_controller_config.t_max_dhw_in_celsius - chp_controller_config.t_min_dhw_in_celsius)
    chp_power = chp_power * (my_boiler.config.energy_full_cycle or 1) * 3.6e6 / chp_controller_config.min_operation_time_in_seconds or 1

    # configure and add chp
    chp_config = generic_chp.CHPConfig.get_default_config_chp(thermal_power=chp_power)
    chp_config.source_weight = count
    my_chp = generic_chp.SimpleCHP(
        my_simulation_parameters=my_simulation_parameters, config=chp_config
    )

    # add treshold electricity to chp controller and add it to simulation
    chp_controller_config.electricity_threshold = chp_config.p_el / 2
    my_chp_controller = controller_l1_chp.L1CHPController(
        my_simulation_parameters=my_simulation_parameters, config=chp_controller_config
    )
    my_chp_controller.connect_only_predefined_connections(my_boiler)
    my_chp_controller.connect_only_predefined_connections(my_building)
    my_sim.add_component(my_chp_controller)

    # connect chp with controller intputs and add it to simulation
    my_chp.connect_only_predefined_connections(my_chp_controller)
    my_sim.add_component(my_chp)

    # connect thermal power output of CHP
    my_boiler.connect_only_predefined_connections(my_chp)
    my_building.connect_input(input_fieldname=my_building.ThermalPowerCHP,
                              src_object_name=my_chp.component_name,
                              src_field_name=my_chp.ThermalPowerOutputBuilding,
                              )

    my_electricity_controller.add_component_input_and_connect(
        source_component_class=my_chp,
        source_component_output="ElectricityOutput",
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=[lt.ComponentType.CHP, lt.InandOutputType.ELECTRICITY_PRODUCTION],
        source_weight=my_chp.config.source_weight,
    )

    # connect to EMS electricity controller
    if controlable:
        ems_target_electricity = my_electricity_controller.add_component_output(
            source_output_name=lt.InandOutputType.ELECTRICITY_TARGET,
            source_tags=[
                lt.ComponentType.CHP,
                lt.InandOutputType.ELECTRICITY_TARGET,
            ],
            source_weight=my_chp.config.source_weight,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            output_description="Target electricity for CHP. ",
        )

        my_chp_controller.connect_dynamic_input(
            input_fieldname=my_chp_controller.ElectricityTarget,
            src_object=ems_target_electricity,
        )

    # counting variable
    count += 1

    return count


def configure_chp_with_buffer(
        my_sim: Any, my_simulation_parameters: SimulationParameters, my_buffer: generic_hot_water_storage_modular.HotWaterStorage,
        my_boiler: generic_hot_water_storage_modular.HotWaterStorage,
        my_electricity_controller: controller_l2_energy_management_system.L2GenericEnergyManagementSystem,
        chp_power: float, controlable: bool, count: int, ) -> int:
    """Sets up natural gas CHP. It heats the DHW storage and the buffer storage for heating.

    :param my_sim: Simulation class.
    :type my_sim: Any
    :param my_simulation_parameters: Simulation parameters for HiSIM calculation.
    :type my_simulation_parameters: SimulationParameters
    :param my_buffer: Buffer storage for heating of the HISIM example
    :type my_buffer: generic_hot_water_storage_modular.HotWaterStorage
    :param my_boiler: Hot water storage of the HiSIM example.
    :type my_boiler: generic_hot_water_storage_modular.HotWaterStorage
    :param my_electricity_controller: Energy Management System of the HiSIM example
    :type my_electricity_controller: controller_l2_energy_management_system.L2GenericEnergyManagementSystem
    :param chp_power: Power of the CHP in multiples of default (<=1)
    :type chp_power: float
    :param controlable: When True, surplus control of Energy Management System is activated.
    :type controlable: bool
    :param count: Number of component outputs relevant in the energy management system.
    :type count: int
    :return: New counter variable (+1).
    :rtype: int
    """

    # configure chp controller
    chp_controller_config = controller_l1_chp.L1CHPControllerConfig.get_default_config_chp_with_buffer()
    chp_controller_config.source_weight = count

    # size chp power to hot water storage size
    my_boiler.config.compute_default_cycle(
        temperature_difference_in_kelvin=chp_controller_config.t_max_dhw_in_celsius - chp_controller_config.t_min_dhw_in_celsius)
    chp_power = chp_power * (my_boiler.config.energy_full_cycle or 1) * 3.6e6 / chp_controller_config.min_operation_time_in_seconds

    # configure and add chp
    chp_config = generic_chp.CHPConfig.get_default_config_chp(thermal_power=chp_power)
    chp_config.source_weight = count
    my_chp = generic_chp.SimpleCHP(
        my_simulation_parameters=my_simulation_parameters, config=chp_config,
    )

    # add chop controller and adopt electricity threshold
    chp_controller_config.electricity_threshold = chp_config.p_el / 2
    my_chp_controller = controller_l1_chp.L1CHPController(
        my_simulation_parameters=my_simulation_parameters, config=chp_controller_config,
    )
    my_chp_controller.connect_only_predefined_connections(my_boiler)
    my_chp_controller.connect_input(
        input_fieldname=my_chp_controller.BuildingTemperature, src_object_name=my_buffer.component_name,
        src_field_name=my_buffer.TemperatureMean,
    )
    my_sim.add_component(my_chp_controller)

    # connect chp with controller intputs and add it to simulation
    my_chp.connect_only_predefined_connections(my_chp_controller)
    my_sim.add_component(my_chp)

    # connect power output of CHP
    my_boiler.connect_only_predefined_connections(my_chp)
    my_buffer.connect_input(
        input_fieldname=my_buffer.ThermalPowerCHP,
        src_object_name=my_chp.component_name,
        src_field_name=my_chp.ThermalPowerOutputBuilding,
    )

    my_electricity_controller.add_component_input_and_connect(
        source_component_class=my_chp,
        source_component_output="ElectricityOutput",
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=[lt.ComponentType.CHP, lt.InandOutputType.ELECTRICITY_PRODUCTION],
        source_weight=my_chp.config.source_weight,
    )

    # connect to EMS electricity controller
    if controlable:
        ems_target_electricity = my_electricity_controller.add_component_output(
            source_output_name=lt.InandOutputType.ELECTRICITY_TARGET,
            source_tags=[
                lt.ComponentType.CHP,
                lt.InandOutputType.ELECTRICITY_TARGET,
            ],
            source_weight=my_chp.config.source_weight,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            output_description="Target electricity for CHP. ",
        )

        my_chp_controller.connect_dynamic_input(
            input_fieldname=my_chp_controller.ElectricityTarget,
            src_object=ems_target_electricity,
        )

    # counting variable
    count += 1

    return count


def configure_electrolyzer_and_h2_storage(
        my_sim: Any, my_simulation_parameters: SimulationParameters, my_chp: generic_chp.SimpleCHP, my_chp_controller: controller_l1_chp.L1CHPController,
        my_electricity_controller: controller_l2_energy_management_system.L2GenericEnergyManagementSystem, electrolyzer_power: float,
        h2_storage_size: float, fuel_cell_power: float, count: int, ) -> int:
    """Configures electrolyzer and h2 storage with fuel cell already defined.

    (in configure_elctrolysis_h2storage_fuelcell_system (_with_buffer))

    :param my_sim: Simulation class.
    :type my_sim: Any
    :param my_simulation_parameters: Simulation parameters for HiSIM calculation.
    :type my_simulation_parameters: SimulationParameters
    :param my_chp: Fuel cell component of the HiSIM example
    :type my_chp: generic_chp.CHP
    :param my_chp_controller: Fuel cell controller component of the HiSIM example
    :type my_chp_controller: controller_l1_chp.L1CHPController
    :param my_electricity_controller: Energy management system component of the HiSIM example
    :type my_electricity_controller: controller_l2_energy_management_system.L2GenericEnergyManagementSystem
    :param electrolyzer_power: Power of the electrolyzer in Watt
    :type electrolyzer_power: float
    :param h2_storage_size: Size of the hydrogen storage in capacity for storing kg of hydrogen
    :type h2_storage_size: float
    :param fuel_cell_power: power of the configured fuel cell
    :type fuel_cell_power: float
    :param count: Number of component outputs relevant in the energy management system.
    :type count: int
    :return: New counter variable (+1).
    :rtype: int
    """

    # electrolyzer default configuration
    electrolyzer_config = generic_electrolyzer.GenericElectrolyzerConfig.get_default_config(p_el=electrolyzer_power)
    electrolyzer_config.source_weight = count

    # electrolyzer controller default configuration and counting variable
    electrolyzer_controller_config = controller_l1_electrolyzer.L1ElectrolyzerControllerConfig.get_default_config()
    electrolyzer_controller_config.source_weight = count
    electrolyzer_controller_config.p_min_electrolyzer = electrolyzer_config.min_power
    count += 1

    # electrolyzer
    my_electrolyzer = generic_electrolyzer.GenericElectrolyzer(
        my_simulation_parameters=my_simulation_parameters, config=electrolyzer_config
    )
    my_sim.add_component(my_electrolyzer)

    # run time controller of electrolyzer
    my_electrolyzer_controller = (
        controller_l1_electrolyzer.L1GenericElectrolyzerController(
            my_simulation_parameters=my_simulation_parameters,
            config=electrolyzer_controller_config,
        )
    )
    my_sim.add_component(my_electrolyzer_controller)
    my_electrolyzer.connect_only_predefined_connections(my_electrolyzer_controller)
    print(my_electrolyzer)

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
        source_weight=my_electrolyzer.config.source_weight,
    )
    electricity_to_electrolyzer_target = my_electricity_controller.add_component_output(
        source_output_name=lt.InandOutputType.ELECTRICITY_TARGET,
        source_tags=[
            lt.ComponentType.ELECTROLYZER,
            lt.InandOutputType.ELECTRICITY_TARGET,
        ],
        source_weight=my_electrolyzer.config.source_weight,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        output_description="Target electricity for electrolyzer. ",
    )
    my_electrolyzer_controller.connect_dynamic_input(
        input_fieldname=controller_l1_electrolyzer.L1GenericElectrolyzerController.ElectricityTarget,
        src_object=electricity_to_electrolyzer_target,
    )

    # hydrogen storage default configuration
    h2_storage_config = generic_hydrogen_storage.GenericHydrogenStorageConfig.get_default_config(
        capacity=h2_storage_size,
        max_charging_rate=electrolyzer_power / (3.6e3 * 3.939e4),
        max_discharging_rate=fuel_cell_power / (3.6e3 * 3.939e4),
        source_weight=count,
    )
    my_h2storage = generic_hydrogen_storage.GenericHydrogenStorage(
        my_simulation_parameters=my_simulation_parameters, config=h2_storage_config
    )
    my_h2storage.connect_only_predefined_connections(my_electrolyzer)
    my_h2storage.connect_only_predefined_connections(my_chp)
    my_sim.add_component(my_h2storage)

    my_electrolyzer_controller.connect_only_predefined_connections(my_h2storage)
    my_chp_controller.connect_only_predefined_connections(my_h2storage)

    return count


def configure_elctrolysis_h2storage_fuelcell_system(
        my_sim: Any, my_simulation_parameters: SimulationParameters, my_building: building.Building,
        my_boiler: generic_hot_water_storage_modular.HotWaterStorage, my_electricity_controller: controller_l2_energy_management_system.L2GenericEnergyManagementSystem,
        fuel_cell_power: float, h2_storage_size: float, electrolyzer_power: float, controlable: bool, count: int, ) -> int:
    """Sets electrolysis, H2-storage and chp system.

    :param my_sim: Simulation class.
    :type my_sim: Any
    :param my_simulation_parameters: Simulation parameters for HiSIM calculation.
    :type my_simulation_parameters: SimulationParameters
    :param my_building: Building component of the HiSIM example.
    :type my_building: building.Building
    :param my_boiler: Hot water storage (for drain hot water) component of the HiSIM example.
    :type my_boiler: generic_hot_water_storage_modular.HotWaterStorage
    :param my_electricity_controller:Energy Management System controller component of the HiSIM examples.
    :type my_electricity_controller: controller_l2_energy_management_system.L2GenericEnergyManagementSystem
    :param fuel_cell_power: Power of the fuel cell in Watt
    :type fuel_cell_power: float
    :param h2_storage_size: Size of the hydrogen storage in capacity for storing kg of hydrogen
    :type h2_storage_size: float
    :param electrolyzer_power: Power of the electrolyzer in Watt.
    :type electrolyzer_power: float
    :param controlable: Electricity based control of the fuel cell considered or not.
    :type controlable: bool
    :param count: Number of component outputs relevant in the energy management system.
    :type count: int
    """

    # configure and add chp controller
    chp_controller_config = controller_l1_chp.L1CHPControllerConfig.get_default_config_fuel_cell()
    chp_controller_config.source_weight = count

    # size chp power to hot water storage size
    my_boiler.config.compute_default_cycle(
        temperature_difference_in_kelvin=chp_controller_config.t_max_dhw_in_celsius - chp_controller_config.t_min_dhw_in_celsius)
    fuel_cell_power = fuel_cell_power * (my_boiler.config.energy_full_cycle or 1) * 3.6e6 / chp_controller_config.min_operation_time_in_seconds or 1

    # configure and add chp
    chp_config = generic_chp.CHPConfig.get_default_config_fuelcell(thermal_power=fuel_cell_power)
    chp_config.source_weight = count
    my_chp = generic_chp.SimpleCHP(
        my_simulation_parameters=my_simulation_parameters, config=chp_config
    )

    # add treshold electricity to chp controller and add it to simulation
    chp_controller_config.electricity_threshold = chp_config.p_el / 2
    my_chp_controller = controller_l1_chp.L1CHPController(
        my_simulation_parameters=my_simulation_parameters, config=chp_controller_config
    )
    my_chp_controller.connect_only_predefined_connections(my_boiler)
    my_chp_controller.connect_only_predefined_connections(my_building)
    my_sim.add_component(my_chp_controller)

    # connect chp with controller intputs and add it to simulation
    my_chp.connect_only_predefined_connections(my_chp_controller)
    my_sim.add_component(my_chp)

    # connect thermal power output of CHP
    my_boiler.connect_only_predefined_connections(my_chp)
    my_building.connect_input(
        input_fieldname=my_building.ThermalPowerCHP,
        src_object_name=my_chp.component_name,
        src_field_name=my_chp.ThermalPowerOutputBuilding,
    )

    my_electricity_controller.add_component_input_and_connect(
        source_component_class=my_chp,
        source_component_output="ElectricityOutput",
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=[lt.ComponentType.CHP, lt.InandOutputType.ELECTRICITY_PRODUCTION],
        source_weight=my_chp.config.source_weight,
    )

    # connect to EMS electricity controller
    if controlable:
        ems_target_electricity = my_electricity_controller.add_component_output(
            source_output_name=lt.InandOutputType.ELECTRICITY_TARGET,
            source_tags=[
                lt.ComponentType.CHP,
                lt.InandOutputType.ELECTRICITY_TARGET,
            ],
            source_weight=my_chp.config.source_weight,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            output_description="Target electricity for CHP. ",
        )

        my_chp_controller.connect_dynamic_input(
            input_fieldname=my_chp_controller.ElectricityTarget,
            src_object=ems_target_electricity,
        )

    # counting variable
    count += 1

    count = configure_electrolyzer_and_h2_storage(
        my_sim=my_sim, my_simulation_parameters=my_simulation_parameters, my_chp=my_chp, my_chp_controller=my_chp_controller,
        my_electricity_controller=my_electricity_controller, electrolyzer_power=electrolyzer_power, h2_storage_size=h2_storage_size,
        fuel_cell_power=fuel_cell_power, count=count)

    return count


def configure_elctrolysis_h2storage_fuelcell_system_with_buffer(
        my_sim: Any, my_simulation_parameters: SimulationParameters, my_buffer: generic_hot_water_storage_modular.HotWaterStorage,
        my_boiler: generic_hot_water_storage_modular.HotWaterStorage, my_electricity_controller: controller_l2_energy_management_system.L2GenericEnergyManagementSystem,
        fuel_cell_power: float, h2_storage_size: float, electrolyzer_power: float, controlable: bool, count: int, ) -> int:
    """Sets electrolysis, H2-storage and chp system.

    :param my_sim: Simulation class.
    :type my_sim: Any
    :param my_simulation_parameters: Simulation parameters for HiSIM calculation.
    :type my_simulation_parameters: SimulationParameters
    :param my_buffer: Buffer storage component of the HiSIM example
    :type my_buffer: generic_hot_water_storage_modular.HotWaterStorage
    :param my_boiler: Hot water storage (for drain hot water) component of the HiSIM example.
    :type my_boiler: generic_hot_water_storage_modular.HotWaterStorage
    :param my_electricity_controller:Energy Management System controller component of the HiSIM examples.
    :type my_electricity_controller: controller_l2_energy_management_system.L2GenericEnergyManagementSystem
    :param fuel_cell_power: Power of the fuel cell in Watt
    :type fuel_cell_power: float
    :param h2_storage_size: Size of the hydrogen storage in capacity for storing kg of hydrogen
    :type h2_storage_size: float
    :param electrolyzer_power: Power of the electrolyzer in Watt.
    :type electrolyzer_power: float
    :param controlable: Electricity based control of the fuel cell considered or not.
    :type controlable: bool
    :param count: Number of component outputs relevant in the energy management system.
    :type count: int
    """

    # configure and add chp controller
    chp_controller_config = controller_l1_chp.L1CHPControllerConfig.get_default_config_fuel_cell()
    chp_controller_config.source_weight = count

    # size chp power to hot water storage size
    my_boiler.config.compute_default_cycle(
        temperature_difference_in_kelvin=chp_controller_config.t_max_dhw_in_celsius - chp_controller_config.t_min_dhw_in_celsius)
    fuel_cell_power = fuel_cell_power * (my_boiler.config.energy_full_cycle or 1) * 3.6e6 / chp_controller_config.min_operation_time_in_seconds or 1

    # configure and add chp
    chp_config = generic_chp.CHPConfig.get_default_config_fuelcell(thermal_power=fuel_cell_power)
    chp_config.source_weight = count
    my_chp = generic_chp.SimpleCHP(
        my_simulation_parameters=my_simulation_parameters, config=chp_config
    )

    # add treshold electricity to chp controller and add it to simulation
    chp_controller_config.electricity_threshold = chp_config.p_el / 2
    my_chp_controller = controller_l1_chp.L1CHPController(
        my_simulation_parameters=my_simulation_parameters, config=chp_controller_config
    )
    my_chp_controller.connect_only_predefined_connections(my_boiler)
    my_chp_controller.connect_input(
        input_fieldname=my_chp_controller.BuildingTemperature, src_object_name=my_buffer.component_name, src_field_name=my_buffer.TemperatureMean,
    )
    my_sim.add_component(my_chp_controller)

    # connect chp with controller intputs and add it to simulation
    my_chp.connect_only_predefined_connections(my_chp_controller)
    my_sim.add_component(my_chp)

    # connect thermal power output of CHP
    my_boiler.connect_only_predefined_connections(my_chp)
    my_buffer.connect_input(input_fieldname=my_buffer.ThermalPowerCHP,
                            src_object_name=my_chp.component_name,
                            src_field_name=my_chp.ThermalPowerOutputBuilding,
                            )

    my_electricity_controller.add_component_input_and_connect(
        source_component_class=my_chp,
        source_component_output="ElectricityOutput",
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=[lt.ComponentType.CHP, lt.InandOutputType.ELECTRICITY_PRODUCTION],
        source_weight=my_chp.config.source_weight,
    )

    # connect to EMS electricity controller
    if controlable:
        ems_target_electricity = my_electricity_controller.add_component_output(
            source_output_name=lt.InandOutputType.ELECTRICITY_TARGET,
            source_tags=[
                lt.ComponentType.CHP,
                lt.InandOutputType.ELECTRICITY_TARGET,
            ],
            source_weight=my_chp.config.source_weight,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            output_description="Target electricity for CHP. ",
        )

        my_chp_controller.connect_dynamic_input(
            input_fieldname=my_chp_controller.ElectricityTarget,
            src_object=ems_target_electricity,
        )

    # counting variable
    count += 1

    count = configure_electrolyzer_and_h2_storage(
        my_sim=my_sim, my_simulation_parameters=my_simulation_parameters, my_chp=my_chp, my_chp_controller=my_chp_controller,
        my_electricity_controller=my_electricity_controller, electrolyzer_power=electrolyzer_power, h2_storage_size=h2_storage_size,
        fuel_cell_power=fuel_cell_power, count=count)

    return count
