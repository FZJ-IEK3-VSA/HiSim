"""  Basic household new example. """

# clean

from typing import Optional, Any
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_connector
from hisim.components import weather
from hisim.components import generic_pv_system
from hisim.components import building
from hisim.components import advanced_heat_pump_hplib
from hisim.components import electricity_meter
from hisim.components import simple_hot_water_storage
from hisim.components import heat_distribution_system
from hisim import loadtypes


__authors__ = "Katharina Rieck"
__copyright__ = "Copyright 2022, FZJ-IEK-3"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "1.0"
__maintainer__ = "Noah Pflugradt"
__status__ = "development"


def household_with_hds_and_advanced_hp(
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

    # Set Heat Pump Controller
    hp_controller_mode = (
        2  # mode 1 for on/off and mode 2 for heating/cooling/off (regulated)
    )
    set_heating_threshold_outside_temperature_for_heat_pump_in_celsius = 16.0
    set_cooling_threshold_outside_temperature_for_heat_pump_in_celsius = 20.0

    # Set Heat Pump
    model: str = "Generic"
    group_id: int = 1  # outdoor/air heat pump (choose 1 for regulated or 4 for on/off)
    heating_reference_temperature_in_celsius: float = -7  # t_in
    set_thermal_output_power_in_watt: float = 8000
    flow_temperature_in_celsius = 21  # t_out_val
    cycling_mode = True
    minimum_running_time_in_seconds = 600
    minimum_idle_time_in_seconds = 600
    hp_co2_footprint = set_thermal_output_power_in_watt * 1e-3 * 165.84
    hp_cost = set_thermal_output_power_in_watt * 1e-3 * 1513.74
    hp_lifetime = 10
    hp_maintenance_cost_as_percentage_of_investment = 0.025
    hp_consumption = 0

    # Set Heat Distribution Controller
    hds_controller_name = "HeatDistributionSystemController"
    set_heating_threshold_outside_temperature_for_heat_distribution_system_in_celsius = (
        16.0
    )
    set_heating_temperature_for_building_in_celsius = 19.0
    set_cooling_temperature_for_building_in_celsius = 24.0
    heating_system = heat_distribution_system.HeatingSystemType.FLOORHEATING

    # =================================================================================================================================
    # Build Components

    # Build Simulation Parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year_with_only_plots(
            year=year, seconds_per_timestep=seconds_per_timestep
        )

    my_sim.set_simulation_parameters(my_simulation_parameters)

    # Build Heat Distribution Controller
    my_heat_distribution_controller = heat_distribution_system.HeatDistributionController(
        my_simulation_parameters=my_simulation_parameters,
        config=heat_distribution_system.HeatDistributionControllerConfig(
            name=hds_controller_name,
            set_heating_threshold_outside_temperature_in_celsius=set_heating_threshold_outside_temperature_for_heat_distribution_system_in_celsius,
            set_heating_temperature_for_building_in_celsius=set_heating_temperature_for_building_in_celsius,
            set_cooling_temperature_for_building_in_celsius=set_cooling_temperature_for_building_in_celsius,
            heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
            heating_system=heating_system,
        ),
    )
    # Build Building
    my_building_config = building.BuildingConfig.get_default_german_single_family_home()
    my_building_config.heating_reference_temperature_in_celsius = (
        heating_reference_temperature_in_celsius
    )
    my_building_information = building.BuildingInformation(config=my_building_config)
    my_building = building.Building(
        config=my_building_config, my_simulation_parameters=my_simulation_parameters
    )
    # Build Occupancy
    my_occupancy_config = (
        loadprofilegenerator_connector.OccupancyConfig.get_scaled_CHS01_according_to_number_of_apartments(number_of_apartments=my_building_information.number_of_apartments)
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
        generic_pv_system.PVSystemConfig.get_scaled_PV_system(rooftop_area_in_m2=my_building_information.scaled_rooftop_area_in_m2)
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
    my_heat_pump_controller = advanced_heat_pump_hplib.HeatPumpHplibController(
        config=advanced_heat_pump_hplib.HeatPumpHplibControllerL1Config(
            name="HeatPumpHplibController",
            mode=hp_controller_mode,
            set_heating_threshold_outside_temperature_in_celsius=set_heating_threshold_outside_temperature_for_heat_pump_in_celsius,
            set_cooling_threshold_outside_temperature_in_celsius=set_cooling_threshold_outside_temperature_for_heat_pump_in_celsius,
        ),
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Pump
    my_heat_pump_config = advanced_heat_pump_hplib.HeatPumpHplibConfig.get_scaled_advanced_hp_lib(heating_load_of_building_in_watt=my_building_information.max_thermal_building_demand_in_watt)
    my_heat_pump_config.group_id = group_id
    my_heat_pump_config.flow_temperature_in_celsius = flow_temperature_in_celsius
    my_heat_pump_config.heating_reference_temperature_in_celsius = heating_reference_temperature_in_celsius
    
    my_heat_pump = advanced_heat_pump_hplib.HeatPumpHplib(
        config=my_heat_pump_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Distribution System
    my_heat_distribution_system_config = (
        heat_distribution_system.HeatDistributionConfig.get_default_heatdistributionsystem_config(heating_load_of_building_in_watt=my_building_information.max_thermal_building_demand_in_watt)
    )
    my_heat_distribution_system = heat_distribution_system.HeatDistribution(
        config=my_heat_distribution_system_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Water Storage
    my_simple_heat_water_storage_config = (
        simple_hot_water_storage.SimpleHotWaterStorageConfig.get_scaled_hot_water_storage(heating_load_of_building_in_watt=my_building_information.max_thermal_building_demand_in_watt)
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
        source_component_class=my_photovoltaic_system,
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
        source_component_class=my_occupancy,
        source_component_output=my_occupancy.ElectricityOutput,
        source_load_type=loadtypes.LoadTypes.ELECTRICITY,
        source_unit=loadtypes.Units.WATT,
        source_tags=[loadtypes.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
        source_weight=999,
    )

    my_electricity_meter.add_component_input_and_connect(
        source_component_class=my_heat_pump,
        source_component_output=my_heat_pump.ElectricalInputPower,
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

    my_heat_pump_controller.connect_only_predefined_connections(
        my_weather, my_simple_hot_water_storage, my_heat_distribution_controller
    )

    # -----------------------------------------------------------------------------------------------------------------

    my_heat_pump.connect_only_predefined_connections(
        my_heat_pump_controller, my_weather, my_simple_hot_water_storage
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
        my_heat_pump.TemperatureOutput,
    )
    my_simple_hot_water_storage.connect_input(
        my_simple_hot_water_storage.WaterMassFlowRateFromHeatGenerator,
        my_heat_pump.component_name,
        my_heat_pump.MassFlowOutput,
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
    my_sim.add_component(my_building)
    my_sim.add_component(my_heat_distribution_controller)
    my_sim.add_component(my_heat_distribution_system)
    my_sim.add_component(my_simple_hot_water_storage)
    my_sim.add_component(my_heat_pump_controller)
    my_sim.add_component(my_heat_pump)
