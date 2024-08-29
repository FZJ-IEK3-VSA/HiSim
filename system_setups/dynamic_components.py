"""Dynamic Components Module."""

# clean

from typing import Optional, Any

# import hisim.components.random_numbers
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_utsp_connector
from hisim.components import advanced_battery_bslib
from hisim.components import weather

# from hisim.components import generic_gas_heater
from hisim.components import controller_l2_energy_management_system as cl2
from hisim.components import generic_pv_system

# from hisim.components import building
from hisim.components import advanced_fuel_cell

# from hisim.components.random_numbers import RandomNumbers
# from hisim.components.example_transformer import ExampleTransformer
from hisim import loadtypes as lt

# from hisim import component as cp
# import numpy as np
# import os
# from hisim import utils


def setup_function(my_sim: Any, my_simulation_parameters: Optional[SimulationParameters] = None) -> None:
    """Dynamic Components Demonstration.

    In this system setup a generic controller is added. The generic controller
    makes it possible to add component generically.
    Here two fuel_cell/chp_systems and two batteries
    are added.
    """
    year = 2018
    seconds_per_timestep = 60 * 15

    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year_all_options(
            year=year, seconds_per_timestep=seconds_per_timestep
        )
    # my_simulation_parameters = SimulationParameters.january_only(year=year, seconds_per_timestep=seconds_per_timestep)
    # my_simulation_parameters.enable_all_options( )

    my_sim.set_simulation_parameters(my_simulation_parameters)

    my_advanced_battery_config_1 = advanced_battery_bslib.BatteryConfig.get_default_config()
    my_advanced_battery_config_1.name = "Battery1"
    my_advanced_battery_config_1.system_id = "SG1"
    my_advanced_battery_config_1.custom_battery_capacity_generic_in_kilowatt_hour = 10.0
    my_advanced_battery_config_1.custom_pv_inverter_power_generic_in_watt = 5.0
    my_advanced_battery_config_1.source_weight = 1

    my_advanced_battery_config_2 = advanced_battery_bslib.BatteryConfig.get_default_config()
    my_advanced_battery_config_2.name = "Battery2"
    my_advanced_battery_config_2.system_id = "SG1"
    my_advanced_battery_config_2.custom_battery_capacity_generic_in_kilowatt_hour = 5.0
    my_advanced_battery_config_2.custom_pv_inverter_power_generic_in_watt = 2.5
    my_advanced_battery_config_2.source_weight = 2

    my_advanced_battery_1 = advanced_battery_bslib.Battery(
        my_simulation_parameters=my_simulation_parameters,
        config=my_advanced_battery_config_1,
    )
    my_advanced_battery_2 = advanced_battery_bslib.Battery(
        my_simulation_parameters=my_simulation_parameters,
        config=my_advanced_battery_config_2,
    )

    my_advanced_fuel_cell_config_1 = advanced_fuel_cell.CHPConfig.get_default_config()
    my_advanced_fuel_cell_config_2 = advanced_fuel_cell.CHPConfig.get_default_config()
    my_advanced_fuel_cell_config_1.name = "CHP1"
    my_advanced_fuel_cell_config_2.name = "CHP2"

    my_advanced_fuel_cell_1 = advanced_fuel_cell.CHP(
        my_simulation_parameters=my_simulation_parameters,
        config=my_advanced_fuel_cell_config_1,
    )
    my_advanced_fuel_cell_2 = advanced_fuel_cell.CHP(
        my_simulation_parameters=my_simulation_parameters,
        config=my_advanced_fuel_cell_config_2,
    )
    my_cl2_config = cl2.EMSConfig.get_default_config_ems()
    my_cl2 = cl2.L2GenericEnergyManagementSystem(
        my_simulation_parameters=my_simulation_parameters, config=my_cl2_config
    )

    # Build Occupancy
    my_occupancy_config = loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig.get_default_utsp_connector_config()
    my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
        config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters
    )

    my_weather_config = weather.WeatherConfig.get_default(location_entry=weather.LocationEnum.AACHEN)
    my_weather = weather.Weather(config=my_weather_config, my_simulation_parameters=my_simulation_parameters)

    my_photovoltaic_system_config = generic_pv_system.PVSystemConfig.get_default_pv_system()
    my_photovoltaic_system = generic_pv_system.PVSystem(
        my_simulation_parameters=my_simulation_parameters,
        config=my_photovoltaic_system_config,
    )
    my_photovoltaic_system.connect_only_predefined_connections(my_weather)

    my_cl2.add_component_inputs_and_connect(
        source_component_classes=[my_occupancy],
        source_component_field_name="ElectricityOutput",
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=[lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
        source_weight=999,
    )
    my_cl2.add_component_inputs_and_connect(
        source_component_classes=[my_photovoltaic_system],
        source_component_field_name="ElectricityOutput",
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=[lt.InandOutputType.ELECTRICITY_PRODUCTION],
        source_weight=999,
    )
    my_cl2.add_component_input_and_connect(
        source_object_name=my_advanced_battery_1.component_name,
        source_component_output=my_advanced_battery_1.AcBatteryPowerUsed,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=[lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED],
        source_weight=1,
    )
    my_cl2.add_component_input_and_connect(
        source_object_name=my_advanced_battery_2.component_name,
        source_component_output=my_advanced_battery_2.AcBatteryPowerUsed,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=[lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED],
        source_weight=2,
    )

    electricity_to_or_from_battery_target_1 = my_cl2.add_component_output(
        source_output_name=lt.InandOutputType.ELECTRICITY_TARGET,
        source_tags=[lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_TARGET],
        source_weight=my_advanced_battery_1.source_weight,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        output_description="Target electricity for battery controller (I). ",
    )
    electricity_to_or_from_battery_target_2 = my_cl2.add_component_output(
        source_output_name=lt.InandOutputType.ELECTRICITY_TARGET,
        source_tags=[lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_TARGET],
        source_weight=my_advanced_battery_2.source_weight,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        output_description="Target electricity for battery controller (II). ",
    )

    my_advanced_battery_1.connect_dynamic_input(
        input_fieldname=advanced_battery_bslib.Battery.LoadingPowerInput,
        src_object=electricity_to_or_from_battery_target_1,
    )
    my_advanced_battery_2.connect_dynamic_input(
        input_fieldname=advanced_battery_bslib.Battery.LoadingPowerInput,
        src_object=electricity_to_or_from_battery_target_2,
    )

    my_cl2.add_component_input_and_connect(
        source_object_name=my_advanced_fuel_cell_1.component_name,
        source_component_output=my_advanced_fuel_cell_1.ElectricityOutput,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=[lt.ComponentType.FUEL_CELL, lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED],
        source_weight=3,
    )
    my_cl2.add_component_input_and_connect(
        source_object_name=my_advanced_fuel_cell_2.component_name,
        source_component_output=my_advanced_fuel_cell_2.ElectricityOutput,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=[lt.ComponentType.FUEL_CELL, lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED],
        source_weight=4,
    )

    electricity_from_fuel_cell_target_1 = my_cl2.add_component_output(
        source_output_name=lt.InandOutputType.ELECTRICITY_TARGET,
        source_tags=[lt.ComponentType.FUEL_CELL, lt.InandOutputType.ELECTRICITY_TARGET],
        source_weight=3,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        output_description="Target electricity for fuel cell (I). ",
    )
    electricity_from_fuel_cell_target_2 = my_cl2.add_component_output(
        source_output_name=lt.InandOutputType.ELECTRICITY_TARGET,
        source_tags=[lt.ComponentType.FUEL_CELL, lt.InandOutputType.ELECTRICITY_TARGET],
        source_weight=4,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        output_description="Target electricity for fuel cell (II). ",
    )

    my_advanced_fuel_cell_1.connect_dynamic_input(
        input_fieldname=advanced_fuel_cell.CHP.ElectricityFromCHPTarget,
        src_object=electricity_from_fuel_cell_target_1,
    )
    my_advanced_fuel_cell_2.connect_dynamic_input(
        input_fieldname=advanced_fuel_cell.CHP.ElectricityFromCHPTarget,
        src_object=electricity_from_fuel_cell_target_2,
    )

    my_sim.add_component(my_advanced_battery_1)
    my_sim.add_component(my_advanced_battery_2)
    my_sim.add_component(my_advanced_fuel_cell_1)
    my_sim.add_component(my_advanced_fuel_cell_2)
    my_sim.add_component(my_cl2)
    my_sim.add_component(my_weather)
    my_sim.add_component(my_occupancy)
    my_sim.add_component(my_photovoltaic_system)
