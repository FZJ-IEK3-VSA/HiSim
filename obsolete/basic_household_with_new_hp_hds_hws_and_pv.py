"""  Basic household new system setup. """

# clean

from typing import Optional, Any
from hisim.simulator import SimulationParameters

from hisim.components import weather
from hisim.components import generic_pv_system
from hisim.components import building

from hisim.components import electricity_meter
from hisim.components import simple_hot_water_storage
from hisim.components import heat_distribution_system
from hisim import postprocessingoptions
from hisim import loadtypes
import generic_heat_pump_for_house_with_hds
from obsolete import loadprofilegenerator_connector


__authors__ = "Katharina Rieck"
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
    my_building_information = building.BuildingInformation(config=my_building_config)

    # Build Occupancy
    my_occupancy_config = (
        loadprofilegenerator_connector.OccupancyConfig.get_default_chr01_couple_both_at_work()
    )

    my_occupancy = loadprofilegenerator_connector.Occupancy(
        config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters
    )

    # Build Weather
    my_weather_config = weather.WeatherConfig.get_default(
        location_entry=weather.LocationEnum.AACHEN
    )
    my_weather = weather.Weather(
        config=my_weather_config, my_simulation_parameters=my_simulation_parameters
    )

    # Build PV
    my_photovoltaic_system_config = (
        generic_pv_system.PVSystemConfig.get_default_pv_system()
    )

    my_photovoltaic_system = generic_pv_system.PVSystem(
        config=my_photovoltaic_system_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Electricity Meter
    my_electricity_meter = electricity_meter.ElectricityMeter(
        my_simulation_parameters=my_simulation_parameters,
        config=electricity_meter.ElectricityMeterConfig.get_electricity_meter_default_config(),
    )

    # Build Heat Pump Controller
    my_heat_pump_controller = generic_heat_pump_for_house_with_hds.HeatPumpControllerNew(
        config=generic_heat_pump_for_house_with_hds.HeatPumpControllerConfigNew.get_default_generic_heat_pump_controller_config(),
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Pump
    my_heat_pump = generic_heat_pump_for_house_with_hds.GenericHeatPumpNew(
        config=generic_heat_pump_for_house_with_hds.GenericHeatPumpConfigNew.get_default_generic_heat_pump_config(
            heating_load_of_building_in_watt=my_building.my_building_information.max_thermal_building_demand_in_watt
        ),
        my_simulation_parameters=my_simulation_parameters,
    )

    my_hds_controller_config = (
        heat_distribution_system.HeatDistributionControllerConfig.get_default_heat_distribution_controller_config(
            set_heating_temperature_for_building_in_celsius=my_building_information.set_heating_temperature_for_building_in_celsius,
            set_cooling_temperature_for_building_in_celsius=my_building_information.set_cooling_temperature_for_building_in_celsius,
            heating_load_of_building_in_watt=my_building_information.max_thermal_building_demand_in_watt,
        ),
    )
    # Build Heat Distribution Controller
    my_heat_distribution_controller = (
        heat_distribution_system.HeatDistributionController(
            my_simulation_parameters=my_simulation_parameters,
            config=my_hds_controller_config,
        )
    )
    my_hds_controller_information = (
        heat_distribution_system.HeatDistributionControllerInformation(
            config=my_hds_controller_config
        )
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
    my_simple_heat_water_storage_config = simple_hot_water_storage.SimpleHotWaterStorageConfig.get_default_simplehotwaterstorage_config(
        water_mass_flow_rate_from_hds_in_kg_per_second=my_hds_controller_information.water_mass_flow_rate_in_kp_per_second
    )

    my_simple_hot_water_storage = simple_hot_water_storage.SimpleHotWaterStorage(
        config=my_simple_heat_water_storage_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # =================================================================================================================================
    # Connect Component Inputs with Outputs

    my_photovoltaic_system.connect_only_predefined_connections(my_weather)

    # Electricity Grid
    my_electricity_meter.add_component_input_and_connect(
        source_object_name=my_photovoltaic_system.component_name,
        source_component_output=my_photovoltaic_system.ElectricityOutput,
        source_load_type=loadtypes.LoadTypes.ELECTRICITY,
        source_unit=loadtypes.Units.WATT,
        source_tags=[
            loadtypes.ComponentType.PV,
            loadtypes.InandOutputType.ELECTRICITY_PRODUCTION,
        ],
        source_weight=999,
    )

    my_electricity_meter.add_component_input_and_connect(
        source_object_name=my_occupancy.component_name,
        source_component_output=my_occupancy.ElectricityOutput,
        source_load_type=loadtypes.LoadTypes.ELECTRICITY,
        source_unit=loadtypes.Units.WATT,
        source_tags=[loadtypes.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
        source_weight=999,
    )

    my_electricity_meter.add_component_input_and_connect(
        source_object_name=my_heat_pump.component_name,
        source_component_output=my_heat_pump.ElectricityOutput,
        source_load_type=loadtypes.LoadTypes.ELECTRICITY,
        source_unit=loadtypes.Units.WATT,
        source_tags=[
            loadtypes.ComponentType.HEAT_PUMP,
            loadtypes.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED,
        ],
        source_weight=999,
    )
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
        my_electricity_meter.component_name,
        my_electricity_meter.ElectricityToOrFromGrid,
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
        my_simple_hot_water_storage.WaterTemperatureFromHeatDistribution,
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
    my_sim.add_component(my_electricity_meter)
    my_sim.add_component(my_simple_hot_water_storage)
    my_sim.add_component(my_heat_distribution_controller)
    my_sim.add_component(my_building)
    my_sim.add_component(my_heat_distribution_system)
    my_sim.add_component(my_heat_pump_controller)
    my_sim.add_component(my_heat_pump)
