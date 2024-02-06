"""  Basic household new system setup. """

# clean

from typing import Optional, Any, Union, List
import re
import os
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from utspclient.helpers.lpgdata import Households
from utspclient.helpers.lpgpythonbindings import JsonReference
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_utsp_connector
from hisim.components import weather
from hisim.components import building
from hisim.components import (
    advanced_heat_pump_hplib,
    simple_hot_water_storage,
    heat_distribution_system,
    generic_heat_pump_modular,
    generic_hot_water_storage_modular,
    controller_l1_heatpump,
    electricity_meter,
)
from hisim.component import ConfigBase
from hisim.result_path_provider import ResultPathProviderSingleton, SortingOptionEnum
from hisim.sim_repository_singleton import SingletonSimRepository, SingletonDictKeyEnum
from hisim.postprocessingoptions import PostProcessingOptions
from hisim import log

__authors__ = "Katharina Rieck"
__copyright__ = "Copyright 2022, FZJ-IEK-3"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "1.0"
__maintainer__ = "Noah Pflugradt"
__status__ = "development"


@dataclass_json
@dataclass
class BuildingPVWeatherConfig(ConfigBase):

    """Configuration for BuildingPv."""

    name: str
    pv_size: float
    pv_azimuth: float
    pv_tilt: float
    share_of_maximum_pv_power: float
    building_code: str
    conditioned_floor_area_in_m2: float
    number_of_dwellings_per_building: int
    lpg_households: List[str]

    @classmethod
    def get_default(cls):
        """Get default BuildingPVConfig."""

        return BuildingPVWeatherConfig(
            name="BuildingPVConfig",
            pv_size=5,
            pv_azimuth=180,
            pv_tilt=30,
            share_of_maximum_pv_power=1,
            building_code="DE.N.SFH.05.Gen.ReEx.001.002",
            conditioned_floor_area_in_m2=121.2,
            number_of_dwellings_per_building=1,
            lpg_households=["CHR01_Couple_both_at_Work"],
        )


def setup_function(
    my_sim: Any, my_simulation_parameters: Optional[SimulationParameters] = None
) -> None:  # noqa: too-many-statements
    """Household system setup.

    This setup function emulates an household including the following components:

    - Simulation Parameters
    - Components
        - Occupancy (Residents' Demands)
        - Weather
        - Building
        - Heat Pump
        - Heat Pump Controller
        - Heat Distribution System
        - Heat Distribution Controller
        - Heat Water Storage
        - Domestic water heat pump
        - Electricity Meter
    """

    # =================================================================================================================================
    # Set System Parameters from Config

    # household-pv-config
    config_filename = my_sim.my_module_config_path

    my_config: BuildingPVWeatherConfig
    if isinstance(config_filename, str) and os.path.exists(config_filename.rstrip("\r")):
        with open(config_filename.rstrip("\r"), encoding="unicode_escape") as system_config_file:
            my_config = BuildingPVWeatherConfig.from_json(system_config_file.read())  # type: ignore

        log.information(f"Read system config from {config_filename}")
        log.information("Config values: " + f"{my_config.to_dict}" + "\n")
    else:
        my_config = BuildingPVWeatherConfig.get_default()
        log.information("No module config path from the simulator was given. Use default config.")

    # Set Simulation Parameters
    year = 2021
    seconds_per_timestep = 60

    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year(year=year, seconds_per_timestep=seconds_per_timestep)
        my_simulation_parameters.post_processing_options.append(
            PostProcessingOptions.PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION
        )
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_OPEX)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_CAPEX)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_KPIS_AND_WRITE_TO_REPORT)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.WRITE_ALL_KPIS_TO_JSON)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.OPEN_DIRECTORY_IN_EXPLORER)
    my_sim.set_simulation_parameters(my_simulation_parameters)

    # Set Building (scale building according to total base area and not absolute floor area)
    building_code = my_config.building_code
    total_base_area_in_m2 = None
    absolute_conditioned_floor_area_in_m2 = my_config.conditioned_floor_area_in_m2
    number_of_apartments = my_config.number_of_dwellings_per_building

    # Set occupancy
    cache_dir_path = "/fast/home/k-rieck/lpg-utsp-data"

    # get household attribute jsonreferences from list of strings
    lpg_households: Union[JsonReference, List[JsonReference]]

    if isinstance(my_config.lpg_households, List):
        if len(my_config.lpg_households) == 1:
            lpg_households = getattr(Households, my_config.lpg_households[0])
        elif len(my_config.lpg_households) > 1:
            lpg_households = []
            for household_string in my_config.lpg_households:
                if hasattr(Households, household_string):
                    lpg_household = getattr(Households, household_string)
                    lpg_households.append(lpg_household)
        else:
            raise ValueError("Config list with lpg household is empty.")

    else:
        raise TypeError(f"Type {type(my_config.lpg_households)} is incompatible. Should be List[str].")

    # =================================================================================================================================
    # Set Fix System Parameters

    # Set Heat Pump Controller
    hp_controller_mode = 2  # mode 1 for on/off and mode 2 for heating/cooling/off (regulated)
    set_heating_threshold_outside_temperature_for_heat_pump_in_celsius = 16.0
    set_cooling_threshold_outside_temperature_for_heat_pump_in_celsius = 20.0
    temperature_offset_for_state_conditions_in_celsius = 5.0

    # Set Heat Pump
    group_id: int = 1  # outdoor/air heat pump (choose 1 for regulated or 4 for on/off)
    heating_reference_temperature_in_celsius: float = -7  # t_in #TODO: get real heating ref temps according to location
    flow_temperature_in_celsius = 21  # t_out_val

    # =================================================================================================================================
    # Build Basic Components

    # Build Building
    my_building_config = building.BuildingConfig.get_default_german_single_family_home(
        heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
    )
    my_building_config.building_code = building_code
    my_building_config.total_base_area_in_m2 = total_base_area_in_m2
    my_building_config.absolute_conditioned_floor_area_in_m2 = absolute_conditioned_floor_area_in_m2

    my_building_config.number_of_apartments = number_of_apartments
    my_building_config.enable_opening_windows = True
    my_building_information = building.BuildingInformation(config=my_building_config)
    my_building = building.Building(config=my_building_config, my_simulation_parameters=my_simulation_parameters)

    # Build Occupancy
    my_occupancy_config = loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig.get_default_utsp_connector_config()
    my_occupancy_config.household = lpg_households
    my_occupancy_config.cache_dir_path = cache_dir_path

    my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
        config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters
    )

    # Build Weather
    my_weather_config = weather.WeatherConfig.get_default(location_entry=weather.LocationEnum.AACHEN)

    my_weather = weather.Weather(config=my_weather_config, my_simulation_parameters=my_simulation_parameters)

    # =================================================================================================================================
    # Build Energy System Components

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
    # Build Heat Pump Controller
    my_heat_pump_controller = advanced_heat_pump_hplib.HeatPumpHplibController(
        config=advanced_heat_pump_hplib.HeatPumpHplibControllerL1Config(
            name="HeatPumpController",
            mode=hp_controller_mode,
            set_heating_threshold_outside_temperature_in_celsius=set_heating_threshold_outside_temperature_for_heat_pump_in_celsius,
            set_cooling_threshold_outside_temperature_in_celsius=set_cooling_threshold_outside_temperature_for_heat_pump_in_celsius,
            temperature_offset_for_state_conditions_in_celsius=temperature_offset_for_state_conditions_in_celsius,
            heat_distribution_system_type=my_hds_controller_information.heat_distribution_system_type,
        ),
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Pump
    my_heat_pump_config = advanced_heat_pump_hplib.HeatPumpHplibConfig.get_scaled_advanced_hp_lib(
        heating_load_of_building_in_watt=my_building_information.max_thermal_building_demand_in_watt,
        heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
    )
    my_heat_pump_config.group_id = group_id
    my_heat_pump_config.flow_temperature_in_celsius = flow_temperature_in_celsius

    my_heat_pump = advanced_heat_pump_hplib.HeatPumpHplib(
        config=my_heat_pump_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Distribution System
    my_heat_distribution_system_config = heat_distribution_system.HeatDistributionConfig.get_default_heatdistributionsystem_config(
        temperature_difference_between_flow_and_return_in_celsius=my_hds_controller_information.temperature_difference_between_flow_and_return_in_celsius,
        water_mass_flow_rate_in_kg_per_second=my_hds_controller_information.water_mass_flow_rate_in_kp_per_second,
    )
    my_heat_distribution_system = heat_distribution_system.HeatDistribution(
        config=my_heat_distribution_system_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    # Build Heat Water Storage
    my_simple_heat_water_storage_config = simple_hot_water_storage.SimpleHotWaterStorageConfig.get_scaled_hot_water_storage(
        max_thermal_power_in_watt_of_heating_system=my_heat_pump_config.set_thermal_output_power_in_watt,
        temperature_difference_between_flow_and_return_in_celsius=my_hds_controller_information.temperature_difference_between_flow_and_return_in_celsius,
        heating_system_name=my_heat_pump.component_name,
        water_mass_flow_rate_from_hds_in_kg_per_second=my_hds_controller_information.water_mass_flow_rate_in_kp_per_second,
    )
    my_simple_hot_water_storage = simple_hot_water_storage.SimpleHotWaterStorage(
        config=my_simple_heat_water_storage_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build DHW (this is taken from household_3_advanced_hp_diesel-car_pv_battery.py)
    my_dhw_heatpump_config = generic_heat_pump_modular.HeatPumpConfig.get_scaled_waterheating_to_number_of_apartments(
        number_of_apartments=my_building_information.number_of_apartments,
        default_power_in_watt=6000,
    )

    my_dhw_heatpump_controller_config = (
        controller_l1_heatpump.L1HeatPumpConfig.get_default_config_heat_source_controller_dhw(
            name="DHWHeatpumpController"
        )
    )

    my_dhw_storage_config = (
        generic_hot_water_storage_modular.StorageConfig.get_scaled_config_for_boiler_to_number_of_apartments(
            number_of_apartments=my_building_information.number_of_apartments,
            default_volume_in_liter=450,
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

    # Set Results Path
    # if config_filename is given, get hash number and sampling mode for result path
    if config_filename is not None:
        config_filename_splitted = config_filename.split("/")
        hash_number = re.findall(r"\-?\d+", config_filename_splitted[-1])[0]
        sampling_mode = config_filename_splitted[-2]

        sorting_option = SortingOptionEnum.MASS_SIMULATION_WITH_HASH_ENUMERATION

        SingletonSimRepository().set_entry(
            key=SingletonDictKeyEnum.RESULT_SCENARIO_NAME,
            entry=f"ref_{hash_number}",
        )

    # if config_filename is not given, make result path with index enumeration
    else:
        hash_number = None
        sorting_option = SortingOptionEnum.MASS_SIMULATION_WITH_INDEX_ENUMERATION
        sampling_mode = None

    ResultPathProviderSingleton().set_important_result_path_information(
        module_directory=my_sim.module_directory,
        model_name=my_sim.module_filename,
        variant_name="ref_",
        hash_number=hash_number,
        sorting_option=sorting_option,
        sampling_mode=sampling_mode,
    )
