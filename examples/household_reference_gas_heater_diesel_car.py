""" Single Household with gas heater and diesel Car as reference case"""
# Todo clean
from typing import Optional, Any
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_connector
from hisim.components import weather
from hisim.components import building
from hisim.components import sumbuilder
from hisim.components import controller_l1_heat_old
from hisim.components import generic_heat_water_storage
from hisim.components import heat_distribution_system
from hisim.components import generic_gas_heater
from hisim.components import simple_hot_water_storage

__authors__ = "Markus Blasberg"
__copyright__ = "Copyright 2021-2023, FZJ-IEK-3"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "1.0"
__maintainer__ = "Noah Pflugradt"
__status__ = "development"


def household_reference_gas_heater_diesel_car(
    my_sim: Any, my_simulation_parameters: Optional[SimulationParameters] = None
) -> None:
    """Gas heater + buffer storage.

    This setup function emulates an household including
    the basic components. Here the residents have their
    heating needs covered by a gas heater and a heating
    water storage. Mobility is covered by a diesel-driven car

    - Simulation Parameters
    - Components
        - Weather
        - building
        - Occupancy (Residents' Demands)
        -
        - GasHeater
        - HeatingStorage
        - HeatDistributionSystem
        - DHW (extra boiler/Heatpump)
        - Car (Diesel)
    """

    # =================================================================================================================================
    # Set System Parameters

    # Set simulation parameters
    year = 2021
    seconds_per_timestep = 60 * 15

    # Set Heat Distribution System  #Todo copied from advanced example
    hds_name = "HeatDistributionSystem"
    heating_system = heat_distribution_system.HeatingSystemType.FLOORHEATING

    # Set Heat Distribution Controller
    hds_controller_name = "HeatDistributionSystemController"
    set_heating_threshold_outside_temperature_for_heat_distribution_system_in_celsius = (
        None
    )
    set_temperature_for_building_in_celsius = 20.0
    set_cooling_threshold_water_temperature_in_celsius = 17.0

    heating_reference_temperature_in_celsius: float = -7  # t_in #Todo copied from HeatPump Parameters in advanced example

    # Set Simple Heat Water Storage #Todo copied from advanced example
    hws_name = "SimpleHeatWaterStorage"
    volume_heating_water_storage_in_liter = 500
    temperature_loss_in_celsius_per_hour = 0.21
    # =================================================================================================================================
    # Build Components

    # Build system parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year_all_options(
            year=year, seconds_per_timestep=seconds_per_timestep
        )
    my_sim.set_simulation_parameters(my_simulation_parameters)

    #Todo: change config with systemConfigBase.json for all components similar to modular_example

    # Build Building
    my_building = building.Building(
        config=building.BuildingConfig.get_default_german_single_family_home(),
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build occupancy
    my_occupancy = loadprofilegenerator_connector.Occupancy(
        config=loadprofilegenerator_connector.OccupancyConfig.get_default_CHS01(),
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Weather
    my_weather = weather.Weather(
        config=weather.WeatherConfig.get_default(weather.LocationEnum.Aachen),
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Base Electricity Load Profile #Todo copied from advanced example
    my_base_electricity_load_profile = sumbuilder.ElectricityGrid(
        config=sumbuilder.ElectricityGridConfig(
            name="ElectrcityGrid_BaseLoad",
            grid=[my_occupancy], #, "Subtract", my_photovoltaic_system],
            signal=None,
        ),
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Gas Heater
    my_gas_heater = generic_gas_heater.GasHeater(
        config=generic_gas_heater.GenericGasHeaterConfig.get_default_gasheater_config(),
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Water Storage #Todo copied from advanced example
    my_simple_heat_water_storage_config = (
        simple_hot_water_storage.SimpleHotWaterStorageConfig(
            name=hws_name,
            volume_heating_water_storage_in_liter=volume_heating_water_storage_in_liter,
            temperature_loss_in_celsius_per_hour=temperature_loss_in_celsius_per_hour,
        )
    )
    my_simple_hot_water_storage = simple_hot_water_storage.SimpleHotWaterStorage(
        config=my_simple_heat_water_storage_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    my_simple_hot_water_storage_controller = (
        simple_hot_water_storage.SimpleHotWaterStorageController(
            my_simulation_parameters=my_simulation_parameters
        )
    )

    # Build Heat Distribution Controller #Todo copied from advanced example
    my_heat_distribution_controller = heat_distribution_system.HeatDistributionController(
        my_simulation_parameters=my_simulation_parameters,
        config=heat_distribution_system.HeatDistributionControllerConfig(
            name=hds_controller_name,
            set_heating_threshold_outside_temperature_in_celsius=set_heating_threshold_outside_temperature_for_heat_distribution_system_in_celsius,
            set_temperature_for_building_in_celsius=set_temperature_for_building_in_celsius,
            heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
            heating_system=heating_system,
            set_cooling_threshold_water_temperature_in_celsius=set_cooling_threshold_water_temperature_in_celsius,
        ),
    )

    # Build Heat Distribution System #Todo copied from advanced example
    my_heat_distribution_system_config = (
        heat_distribution_system.HeatDistributionConfig(
            name=hds_name,
            heating_system=heating_system,
        )
    )
    my_heat_distribution_system = heat_distribution_system.HeatDistribution(
        config=my_heat_distribution_system_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build DHW

    # Build Diesel-Car

    # Build GasHeaterController

    # =================================================================================================================================
    # Connect Component Inputs with Outputs
    #



    # =================================================================================================================================
    # Add Components to Simulation Parameters

    # my_sim.add_component(my_building_controller)
    # my_sim.add_component(my_controller_heat)
    my_sim.add_component(my_simple_hot_water_storage_controller)

    my_sim.add_component(my_simple_hot_water_storage)
    my_sim.add_component(my_gas_heater)
    my_sim.add_component(my_building)
    my_sim.add_component(my_weather)
    my_sim.add_component(my_occupancy)
