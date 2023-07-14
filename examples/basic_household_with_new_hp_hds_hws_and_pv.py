"""  Basic household new example. """

# clean

from typing import Optional, Any
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_connector
from hisim.components import weather
from hisim.components import generic_pv_system
from hisim.components import building
from hisim.components import generic_heat_pump_for_house_with_hds
from hisim.components import sumbuilder
from hisim.components import simple_hot_water_storage
from hisim.components import heat_distribution_system
from hisim import postprocessingoptions

__authors__ = "Katharina Rieck"
__copyright__ = "Copyright 2022, FZJ-IEK-3"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "1.0"
__maintainer__ = "Noah Pflugradt"
__status__ = "development"


def household_with_hds(
    my_sim: Any, my_simulation_parameters: Optional[SimulationParameters] = None
) -> None:  # noqa: too-many-statements
    """Basic household example.

    This setup function emulates an household including the basic components. Here the residents have their
    electricity and heating needs covered by the photovoltaic system and the heat pump.

    - Simulation Parameters
    - Components
        - Occupancy (Residents' Demands)
        - Weather
        - Photovoltaic System
        - Building
        - Heat Pump
        - Heat Pump Controller
        - Heat Distribution System
        - Heat Distribution Controller
        - Heat Water Storage
    """

    # =================================================================================================================================
    # Set System Parameters

    # Set Simulation Parameters
    year = 2021
    seconds_per_timestep = 60

    # =================================================================================================================================
    # Build Components

    # Build Simulation Parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year(
            year=year, seconds_per_timestep=seconds_per_timestep
        )
    # my_simulation_parameters.post_processing_options.append(postprocessingoptions.PostProcessingOptions.PROVIDE_DETAILED_ITERATION_LOGGING)
    my_simulation_parameters.post_processing_options.append(
        postprocessingoptions.PostProcessingOptions.MAKE_NETWORK_CHARTS
    )
    my_simulation_parameters.post_processing_options.append(
        postprocessingoptions.PostProcessingOptions.WRITE_NETWORK_CHARTS_TO_REPORT
    )

    my_sim.set_simulation_parameters(my_simulation_parameters)

    # Build Building
    my_building_config = building.BuildingConfig.get_default_german_single_family_home()

    my_building = building.Building(
        config=my_building_config, my_simulation_parameters=my_simulation_parameters
    )
    # Build Occupancy
    my_occupancy_config = (
        loadprofilegenerator_connector.OccupancyConfig.get_default_CHS01()
    )

    my_occupancy = loadprofilegenerator_connector.Occupancy(
        config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters
    )

    # Build Weather
    my_weather_config = weather.WeatherConfig.get_default(
        location_entry=weather.LocationEnum.Aachen
    )
    my_weather = weather.Weather(
        config=my_weather_config, my_simulation_parameters=my_simulation_parameters
    )

    # Build PV
    my_photovoltaic_system_config = (
        generic_pv_system.PVSystemConfig.get_default_PV_system()
    )

    my_photovoltaic_system = generic_pv_system.PVSystem(
        config=my_photovoltaic_system_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Base Electricity Load Profile
    my_base_electricity_load_profile = sumbuilder.ElectricityGrid(
        config=sumbuilder.ElectricityGridConfig(
            name="ElectrcityGrid_BaseLoad",
            grid=[my_occupancy, "Subtract", my_photovoltaic_system],
            signal=None,
        ),
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Pump Controller
    my_heat_pump_controller = generic_heat_pump_for_house_with_hds.HeatPumpControllerNew(
        config=generic_heat_pump_for_house_with_hds.HeatPumpControllerConfigNew.get_default_generic_heat_pump_controller_config(),
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Pump
    my_heat_pump = generic_heat_pump_for_house_with_hds.GenericHeatPumpNew(
        config=generic_heat_pump_for_house_with_hds.GenericHeatPumpConfigNew.get_default_generic_heat_pump_config(),
        my_simulation_parameters=my_simulation_parameters,
    )
    # Build Heat Distribution Controller
    my_heat_distribution_controller = heat_distribution_system.HeatDistributionController(
        my_simulation_parameters=my_simulation_parameters,
        config=heat_distribution_system.HeatDistributionControllerConfig.get_default_heat_distribution_controller_config(),
    )

    # Build Heat Distribution System
    my_heat_distribution_system_config = (
        heat_distribution_system.HeatDistributionConfig.get_default_heatdistributionsystem_config()
    )

    my_heat_distribution_system = heat_distribution_system.HeatDistribution(
        config=my_heat_distribution_system_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Water Storage
    my_simple_heat_water_storage_config = (
        simple_hot_water_storage.SimpleHotWaterStorageConfig.get_default_simplehotwaterstorage_config()
    )

    my_simple_hot_water_storage = simple_hot_water_storage.SimpleHotWaterStorage(
        config=my_simple_heat_water_storage_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # =================================================================================================================================
    # Connect Component Inputs with Outputs

    my_photovoltaic_system.connect_only_predefined_connections(my_weather)
    # -----------------------------------------------------------------------------------------------------------------
    my_building.connect_only_predefined_connections(my_weather, my_occupancy)
    my_building.connect_input(
        my_building.ThermalPowerDelivered,
        my_heat_distribution_system.component_name,
        my_heat_distribution_system.ThermalPowerDelivered,
    )

    # -----------------------------------------------------------------------------------------------------------------
    my_heat_pump_controller.connect_input(
        my_heat_pump_controller.WaterTemperatureInputFromHeatWaterStorage,
        my_simple_hot_water_storage.component_name,
        my_simple_hot_water_storage.WaterTemperatureToHeatGenerator,
    )
    my_heat_pump_controller.connect_input(
        my_heat_pump_controller.ElectricityInput,
        my_base_electricity_load_profile.component_name,
        my_base_electricity_load_profile.ElectricityOutput,
    )
    # -----------------------------------------------------------------------------------------------------------------
    my_heat_pump.connect_only_predefined_connections(
        my_weather, my_heat_pump_controller
    )

    my_heat_pump.connect_input(
        my_heat_pump.WaterTemperatureInputFromHeatWaterStorage,
        my_simple_hot_water_storage.component_name,
        my_simple_hot_water_storage.WaterTemperatureToHeatGenerator,
    )

    # -----------------------------------------------------------------------------------------------------------------
    my_simple_hot_water_storage.connect_input(
        my_simple_hot_water_storage.WaterTemperatureFromHeatDistributionSystem,
        my_heat_distribution_system.component_name,
        my_heat_distribution_system.WaterTemperatureOutput,
    )
    my_simple_hot_water_storage.connect_input(
        my_simple_hot_water_storage.WaterTemperatureFromHeatGenerator,
        my_heat_pump.component_name,
        my_heat_pump.WaterTemperatureOutput,
    )

    # -----------------------------------------------------------------------------------------------------------------
    my_heat_distribution_controller.connect_only_predefined_connections(
        my_weather, my_building, my_simple_hot_water_storage
    )
    # -----------------------------------------------------------------------------------------------------------------
    my_heat_distribution_system.connect_only_predefined_connections(
        my_building, my_heat_distribution_controller, my_simple_hot_water_storage
    )

    # =================================================================================================================================
    # Add Components to Simulation Parameters

    my_sim.add_component(my_occupancy)
    my_sim.add_component(my_weather)
    my_sim.add_component(my_photovoltaic_system)
    my_sim.add_component(my_base_electricity_load_profile)
    my_sim.add_component(my_simple_hot_water_storage)
    my_sim.add_component(my_heat_distribution_controller)
    my_sim.add_component(my_building)
    my_sim.add_component(my_heat_distribution_system)
    my_sim.add_component(my_heat_pump_controller)
    my_sim.add_component(my_heat_pump)
