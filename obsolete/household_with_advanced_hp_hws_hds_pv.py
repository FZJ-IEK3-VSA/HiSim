"""  Basic household new system setup. """

# clean

from typing import Optional, Any
from hisim.simulator import SimulationParameters

from hisim.components import weather
from hisim.components import generic_pv_system
from hisim.components import building
from hisim.components import advanced_heat_pump_hplib
from hisim.components import electricity_meter
from hisim.components import simple_hot_water_storage
from hisim.components import heat_distribution_system
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

    # Set Heat Pump Controller
    hp_controller_mode = (
        2  # mode 1 for on/off and mode 2 for heating/cooling/off (regulated)
    )
    set_heating_threshold_outside_temperature_for_heat_pump_in_celsius = 16.0
    set_cooling_threshold_outside_temperature_for_heat_pump_in_celsius = 20.0
    temperature_offset_for_state_conditions_in_celsius = 5.0

    # Set Heat Pump
    group_id: int = 1  # outdoor/air heat pump (choose 1 for regulated or 4 for on/off)
    heating_reference_temperature_in_celsius: float = -7  # t_in
    flow_temperature_in_celsius = 21  # t_out_val

    # Set Heat Distribution Controller
    hds_controller_name = "HeatDistributionSystemController"
    set_heating_threshold_outside_temperature_for_heat_distribution_system_in_celsius = (
        16.0
    )

    heating_system = heat_distribution_system.HeatDistributionSystemType.FLOORHEATING

    # =================================================================================================================================
    # Build Components

    # Build Simulation Parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year_with_only_plots(
            year=year, seconds_per_timestep=seconds_per_timestep
        )

    my_sim.set_simulation_parameters(my_simulation_parameters)

    # Build Building
    my_building_config = building.BuildingConfig.get_default_german_single_family_home(
        heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
    )
    my_building_information = building.BuildingInformation(config=my_building_config)
    my_building = building.Building(
        config=my_building_config, my_simulation_parameters=my_simulation_parameters
    )
    # Build Occupancy
    my_occupancy_config = loadprofilegenerator_connector.OccupancyConfig.get_scaled_chr01_according_to_number_of_apartments(
        number_of_apartments=my_building_information.number_of_apartments
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
        generic_pv_system.PVSystemConfig.get_scaled_pv_system(
            rooftop_area_in_m2=my_building_information.scaled_rooftop_area_in_m2
        )
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

    # Build Heat Distribution Controller
    my_heat_distribution_controller = heat_distribution_system.HeatDistributionController(
        my_simulation_parameters=my_simulation_parameters,
        config=heat_distribution_system.HeatDistributionControllerConfig(
            name=hds_controller_name,
            set_heating_threshold_outside_temperature_in_celsius=set_heating_threshold_outside_temperature_for_heat_distribution_system_in_celsius,
            set_heating_temperature_for_building_in_celsius=my_building_information.set_heating_temperature_for_building_in_celsius,
            set_cooling_temperature_for_building_in_celsius=my_building_information.set_cooling_temperature_for_building_in_celsius,
            heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
            heating_system=heating_system,
            heating_load_of_building_in_watt=my_building_information.max_thermal_building_demand_in_watt,
        ),
    )

    my_hds_controller_information = (
        heat_distribution_system.HeatDistributionControllerInformation(
            config=my_heat_distribution_controller.hsd_controller_config
        )
    )
    # Build Heat Pump Controller
    my_heat_pump_controller = advanced_heat_pump_hplib.HeatPumpHplibController(
        config=advanced_heat_pump_hplib.HeatPumpHplibControllerL1Config(
            name="HeatPumpHplibController",
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
        heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius
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
        heating_system_name=my_heat_pump.component_name,
        temperature_difference_between_flow_and_return_in_celsius=my_hds_controller_information.temperature_difference_between_flow_and_return_in_celsius,
        water_mass_flow_rate_from_hds_in_kg_per_second=my_hds_controller_information.water_mass_flow_rate_in_kp_per_second,
    )
    my_simple_hot_water_storage = simple_hot_water_storage.SimpleHotWaterStorage(
        config=my_simple_heat_water_storage_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # =================================================================================================================================
    # Add Components to Simulation Parameters

    my_sim.add_component(my_occupancy)
    my_sim.add_component(my_weather)
    my_sim.add_component(my_photovoltaic_system, connect_automatically=True)
    my_sim.add_component(my_electricity_meter, connect_automatically=True)
    my_sim.add_component(my_building, connect_automatically=True)
    my_sim.add_component(my_heat_distribution_controller, connect_automatically=True)
    my_sim.add_component(my_heat_distribution_system, connect_automatically=True)
    my_sim.add_component(my_simple_hot_water_storage, connect_automatically=True)
    my_sim.add_component(my_heat_pump_controller, connect_automatically=True)
    my_sim.add_component(my_heat_pump, connect_automatically=True)
