"""  Household system setup with gas heater. """
# Todo: clean code

from typing import Optional, Any
from pathlib import Path

from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_utsp_connector
from hisim.components import weather
from hisim.components import generic_gas_heater
from hisim.components import controller_l1_generic_gas_heater
from hisim.components import heat_distribution_system
from hisim.components import building
from hisim.components import simple_hot_water_storage
from hisim import log
from obsolete.household_with_heatpump_and_pv import HouseholdPVConfig

__authors__ = "Vitor Hugo Bellotto Zago, Noah Pflugradt"
__copyright__ = "Copyright 2022, FZJ-IEK-3"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "1.0"
__maintainer__ = "Noah Pflugradt"
__status__ = "development"


def setup_function(
    my_sim: Any, my_simulation_parameters: Optional[SimulationParameters] = None
) -> None:  # noqa: too-many-statements
    """Basic household system setup.

    This setup function emulates a household with some basic components. Here the residents have their
    electricity and heating needs covered by a generic gas heater.

    - Simulation Parameters
    - Components
        - Occupancy (Residents' Demands)
        - Weather
        - Building
        - Gas Heater
        - Gas Heater Controller
        - Heat Distribution System
        - Heat Distribution System Controller
        - Simple Hot Water Storage
    """

    config_filename = "pv_hp_config.json"

    my_config: HouseholdPVConfig
    if Path(config_filename).is_file():
        with open(config_filename, encoding="utf8") as system_config_file:
            my_config = HouseholdPVConfig.from_json(system_config_file.read())  # type: ignore
        log.information(f"Read system config from {config_filename}")
    else:
        my_config = HouseholdPVConfig.get_default()

    # =================================================================================================================================
    # Set System Parameters

    # Set Simulation Parameters
    year = 2021
    seconds_per_timestep = 60

    # Set Occupancy
    data_acquisition_mode = my_config.data_acquisition_mode
    household = my_config.household_type
    energy_intensity = my_config.energy_intensity
    result_path = my_config.result_path
    travel_route_set = my_config.travel_route_set
    transportation_device_set = my_config.transportation_device_set
    charging_station_set = my_config.charging_station_set

    # =================================================================================================================================
    # Build Components

    # Build Simulation Parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year_all_options(
            year=year, seconds_per_timestep=seconds_per_timestep
        )
    my_sim.set_simulation_parameters(my_simulation_parameters)

    # Build Occupancy
    my_occupancy_config = loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig(
        data_acquisition_mode=data_acquisition_mode,
        household=household,
        energy_intensity=energy_intensity,
        result_dir_path=result_path,
        travel_route_set=travel_route_set,
        transportation_device_set=transportation_device_set,
        charging_station_set=charging_station_set,
        name="UTSPConnector",
        consumption=0,
        profile_with_washing_machine_and_dishwasher=True,
        predictive_control=False,
        predictive=False
    )
    my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
        config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters
    )

    # Build Weather
    my_weather = weather.Weather(
        config=weather.WeatherConfig.get_default(weather.LocationEnum.AACHEN),
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Building
    my_building_config = building.BuildingConfig.get_default_german_single_family_home()
    my_building_information = building.BuildingInformation(config=my_building_config)
    my_building = building.Building(
        config=my_building_config, my_simulation_parameters=my_simulation_parameters
    )
    # Build Heat Distribution Controller
    my_heat_distribution_controller_config = heat_distribution_system.HeatDistributionControllerConfig.get_default_heat_distribution_controller_config(
        set_heating_temperature_for_building_in_celsius=my_building_information.set_heating_temperature_for_building_in_celsius,
        set_cooling_temperature_for_building_in_celsius=my_building_information.set_cooling_temperature_for_building_in_celsius,
        heating_load_of_building_in_watt=my_building_information.max_thermal_building_demand_in_watt,
    )

    my_heat_distribution_controller = (
        heat_distribution_system.HeatDistributionController(
            my_simulation_parameters=my_simulation_parameters,
            config=my_heat_distribution_controller_config,
        )
    )
    my_hds_controller_information = (
        heat_distribution_system.HeatDistributionControllerInformation(
            config=my_heat_distribution_controller_config
        )
    )
    # Build Gas Heater Controller
    my_gasheater_controller_config = (
        controller_l1_generic_gas_heater.GenericGasHeaterControllerL1Config.get_default_generic_gas_heater_controller_config()
    )
    my_gasheater_controller = (
        controller_l1_generic_gas_heater.GenericGasHeaterControllerL1(
            my_simulation_parameters=my_simulation_parameters,
            config=my_gasheater_controller_config,
        )
    )

    # Build Gasheater
    my_gasheater = generic_gas_heater.GasHeater(
        config=generic_gas_heater.GenericGasHeaterConfig.get_default_gasheater_config(),
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Distribution System
    hds_config = heat_distribution_system.HeatDistributionConfig.get_default_heatdistributionsystem_config(
        temperature_difference_between_flow_and_return_in_celsius=my_hds_controller_information.temperature_difference_between_flow_and_return_in_celsius,
        water_mass_flow_rate_in_kg_per_second=my_hds_controller_information.water_mass_flow_rate_in_kp_per_second,
    )
    my_heat_distribution = heat_distribution_system.HeatDistribution(
        my_simulation_parameters=my_simulation_parameters, config=hds_config
    )

    # Build Heat Water Storage
    my_simple_heat_water_storage_config = simple_hot_water_storage.SimpleHotWaterStorageConfig.get_default_simplehotwaterstorage_config(
        water_mass_flow_rate_from_hds_in_kg_per_second=my_hds_controller_information.water_mass_flow_rate_in_kp_per_second
    )
    my_simple_hot_water_storage = simple_hot_water_storage.SimpleHotWaterStorage(
        config=my_simple_heat_water_storage_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # =================================================================================================================================
    # Add Components to Simulation Parameters
    my_sim.add_component(my_occupancy)
    my_sim.add_component(my_weather)
    my_sim.add_component(my_building, connect_automatically=True)
    my_sim.add_component(my_gasheater, connect_automatically=True)
    my_sim.add_component(my_gasheater_controller, connect_automatically=True)
    my_sim.add_component(my_heat_distribution, connect_automatically=True)
    my_sim.add_component(my_heat_distribution_controller, connect_automatically=True)
    my_sim.add_component(my_simple_hot_water_storage, connect_automatically=True)
