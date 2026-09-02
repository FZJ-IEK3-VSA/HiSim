"""Simple air-conditioner household building-sizer system setup.

This setup pairs a :class:`~hisim.components.building.Building` with
:class:`~hisim.components.weather.Weather`, a
:class:`~hisim.components.simple_air_conditioner.SimpleAirConditioner` and its
:class:`~hisim.components.simple_air_conditioner.SimpleAirConditionerController`.

It is derived from ``household_heatpump_building_sizer.py`` but stripped down to
weather + building + AC + controller only — no UTSP/occupancy, PV, battery, EMS,
heat pump, heat distribution, DHW storage, or electricity meter.
"""

# clean

import re
from typing import Optional, Any

from hisim.simulator import SimulationParameters
from hisim.components import weather
from hisim.components import building
from hisim.components import simple_air_conditioner
from hisim.result_path_provider import ResultPathProviderSingleton, SortingOptionEnum
from hisim.sim_repository_singleton import SingletonSimRepository, SingletonDictKeyEnum
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.building_sizer_utils.interface_configs.modular_household_config import (
    read_in_configs,
    ModularHouseholdConfig,
)
from hisim import log

__authors__ = "HiSim Project"
__copyright__ = "Copyright 2025, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "1.0"
__maintainer__ = "HiSim Project"
__email__ = "n.pflugradt@fz-juelich.de"
__status__ = "development"


def setup_function(
    my_sim: Any, my_simulation_parameters: Optional[SimulationParameters] = None
) -> None:
    """Build a household with a simple Carnot-efficiency air conditioner for cooling.

    Components:
        - Building (German single-family home, cooling setpoint 25 °C)
        - Weather
        - SimpleAirConditioner (Carnot-efficiency cooling model)
        - SimpleAirConditionerController (hysteresis on indoor temperature)
    """
    # =================================================================================================================================
    # Set System Parameters from Config
    config_filename = my_sim.my_module_config
    my_config = read_in_configs(my_sim.my_module_config)
    if my_config is None:
        my_config = ModularHouseholdConfig().get_default_config_for_household_heatpump()
        log.warning(
            f"Could not read the modular household config from path '{config_filename}'. "
            "Using the heatpump household default config instead."
        )
    assert my_config.archetype_config_ is not None
    arche_type_config_ = my_config.archetype_config_

    # Set Simulation Parameters
    if my_simulation_parameters is None:
        seconds_per_timestep = 60 * 15
        my_simulation_parameters = SimulationParameters.full_year(
            year=2021, seconds_per_timestep=seconds_per_timestep
        )
        my_simulation_parameters.post_processing_options.append(
            PostProcessingOptions.COMPUTE_OPEX
        )
        my_simulation_parameters.post_processing_options.append(
            PostProcessingOptions.COMPUTE_CAPEX
        )
        my_simulation_parameters.post_processing_options.append(
            PostProcessingOptions.COMPUTE_KPIS
        )
    my_sim.set_simulation_parameters(my_simulation_parameters)

    # =================================================================================================================================
    # Extract config values
    weather_location = arche_type_config_.weather_location
    building_code = arche_type_config_.building_code
    total_base_area_in_m2 = None
    absolute_conditioned_floor_area_in_m2 = arche_type_config_.conditioned_floor_area_in_m2
    number_of_apartments = arche_type_config_.number_of_dwellings_per_building

    # =================================================================================================================================
    # Build Building
    # The weather configuration is created before anything that depends on it, because the building and
    # the PV system record which weather they are computed against (their weather_identity) and that
    # has to be set before those components are built. The weather component itself is added below,
    # where it always was, so the order the simulator sees is unchanged.
    my_weather_config = weather.WeatherConfig.get_default(location_entry=weather_location)

    my_building_config = building.BuildingConfig.preset_standard("Building")
    my_building_config.set_heating_temperature_in_celsius = 20.0
    my_building_config.set_cooling_temperature_in_celsius = 25.0
    my_building_config.building_code = building_code
    my_building_config.total_base_area_in_m2 = total_base_area_in_m2
    my_building_config.absolute_conditioned_floor_area_in_m2 = (
        absolute_conditioned_floor_area_in_m2
    )
    my_building_config.number_of_apartments = number_of_apartments
    my_building_config.enable_opening_windows = True
    my_building_config.weather_identity = my_weather_config.identity()
    my_building = building.Building(
        config=my_building_config, my_simulation_parameters=my_simulation_parameters
    )

    # =================================================================================================================================
    # Build Weather
    my_weather = weather.Weather(
        config=my_weather_config, my_simulation_parameters=my_simulation_parameters
    )

    # =================================================================================================================================
    # Build Simple Air Conditioner
    my_air_conditioner_config = (
        simple_air_conditioner.SimpleAirConditionerConfig.get_default_simple_air_conditioner_config()
    )
    my_air_conditioner = simple_air_conditioner.SimpleAirConditioner(
        config=my_air_conditioner_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # =================================================================================================================================
    # Build Simple Air Conditioner Controller
    my_air_conditioner_controller_config = (
        simple_air_conditioner.SimpleAirConditionerControllerConfig.get_default_simple_air_conditioner_controller_config()
    )
    my_air_conditioner_controller = simple_air_conditioner.SimpleAirConditionerController(
        config=my_air_conditioner_controller_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # =================================================================================================================================
    # Wire and add components

    # Building: connect weather defaults, then add
    my_building.connect_only_predefined_connections(my_weather)
    my_sim.add_component(my_building, connect_automatically=True)

    # Weather: add (no auto-connect needed)
    my_sim.add_component(my_weather)

    # AC: add with auto-connect (connects to Weather, Building, Controller via default connections)
    my_sim.add_component(my_air_conditioner, connect_automatically=True)

    # Manual wiring: AC ThermalPowerDelivered → Building ThermalPowerDelivered (negative = cooling)
    my_building.connect_input(
        my_building.ThermalPowerDelivered,
        my_air_conditioner.component_name,
        my_air_conditioner.ThermalPowerDelivered,
    )

    # Controller: add with auto-connect (connects to Building via default connections)
    my_sim.add_component(my_air_conditioner_controller, connect_automatically=True)

    # =================================================================================================================================
    # Set Results Path
    # if config_filename is given, get hash number and sampling mode for result path
    if config_filename is not None:
        config_filename_splitted = config_filename.split("/")
        try:
            scenario_hash_string = re.findall(r"\-?\d+", config_filename_splitted[-1])[0]
            sorting_option = SortingOptionEnum.MASS_SIMULATION_WITH_HASH_ENUMERATION
        except (IndexError, ValueError):
            scenario_hash_string = "-"
            sorting_option = SortingOptionEnum.MASS_SIMULATION_WITH_INDEX_ENUMERATION
        try:
            further_result_folder_description = config_filename_splitted[-2]
        except IndexError:
            further_result_folder_description = "-"
    else:
        scenario_hash_string = "default_scenario"
        sorting_option = SortingOptionEnum.MASS_SIMULATION_WITH_INDEX_ENUMERATION
        further_result_folder_description = "default_config"

    SingletonSimRepository().set_entry(
        key=SingletonDictKeyEnum.RESULT_SCENARIO_NAME,
        entry=f"{scenario_hash_string}",
    )

    if my_simulation_parameters.result_directory == "":
        ResultPathProviderSingleton().set_important_result_path_information(
            module_directory=my_sim.module_directory,
            model_name=my_sim.module_filename,
            further_result_folder_description=further_result_folder_description,
            variant_name="_",
            scenario_hash_string=scenario_hash_string,
            sorting_option=sorting_option,
        )
