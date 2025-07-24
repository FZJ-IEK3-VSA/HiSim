"""Basic household system setup. Shows how to set up a standard system."""

# clean

from typing import Optional, Any
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_utsp_connector
from hisim.components import weather
from hisim.components import generic_pv_system
from hisim.components import building
from hisim.components import generic_heat_pump
from hisim.components import electricity_meter
from hisim import loadtypes


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

    This setup function emulates an household including the basic components. Here the residents have their
    electricity and heating needs covered by the photovoltaic system and the heat pump.

    - Simulation Parameters
    - Components
        - Occupancy (Residents' Demands)
        - Weather
        - Photovoltaic System
        - Building
        - Heat Pump
    """

    # =================================================================================================================================
    # Set System Parameters

    # Set Simulation Parameters
    year = 2021
    seconds_per_timestep = 60

    # Set Heat Pump Controller
    temperature_air_heating_in_celsius = 19.0
    temperature_air_cooling_in_celsius = 24.0
    offset = 0.5
    hp_mode = 2

    # =================================================================================================================================
    # Build Components

    # Build Simulation Parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year_with_only_plots(
            year=year, seconds_per_timestep=seconds_per_timestep
        )

    my_sim.set_simulation_parameters(my_simulation_parameters)

    # Build Building
    my_building_config = building.BuildingConfig.get_default_german_single_family_home()

    my_building = building.Building(config=my_building_config, my_simulation_parameters=my_simulation_parameters)
    # Build Occupancy
    my_occupancy_config = loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig.get_default_utsp_connector_config()
    my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
        config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters
    )

    # Build Weather
    my_weather_config = weather.WeatherConfig.get_default(location_entry=weather.LocationEnum.AACHEN)
    my_weather = weather.Weather(config=my_weather_config, my_simulation_parameters=my_simulation_parameters)

    # Build PV
    my_photovoltaic_system_config = generic_pv_system.PVSystemConfig.get_default_pv_system()

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
    my_heat_pump_controller = generic_heat_pump.GenericHeatPumpController(
        config=generic_heat_pump.GenericHeatPumpControllerConfig(
            building_name="BUI1",
            name="GenericHeatPumpController",
            temperature_air_heating_in_celsius=temperature_air_heating_in_celsius,
            temperature_air_cooling_in_celsius=temperature_air_cooling_in_celsius,
            offset=offset,
            mode=hp_mode,
        ),
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Pump
    my_heat_pump = generic_heat_pump.GenericHeatPump(
        config=generic_heat_pump.GenericHeatPumpConfig.get_default_generic_heat_pump_config(),
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
        source_component_output=my_occupancy.ElectricalPowerConsumption,
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

    my_building.connect_only_predefined_connections(my_weather, my_occupancy)

    my_building.connect_input(
        my_building.ThermalPowerDelivered,
        my_heat_pump.component_name,
        my_heat_pump.ThermalPowerDelivered,
    )

    my_heat_pump_controller.connect_only_predefined_connections(my_building)

    my_heat_pump_controller.connect_input(
        my_heat_pump_controller.ElectricityInput,
        my_electricity_meter.component_name,
        my_electricity_meter.ElectricityAvailable,
    )
    my_heat_pump.connect_only_predefined_connections(my_weather, my_heat_pump_controller)
    my_heat_pump.get_default_connections_heatpump_controller()
    # =================================================================================================================================
    # Add Components to Simulation Parameters

    my_sim.add_component(my_occupancy)
    my_sim.add_component(my_weather)
    my_sim.add_component(my_photovoltaic_system)
    my_sim.add_component(my_electricity_meter)
    my_sim.add_component(my_building)
    my_sim.add_component(my_heat_pump_controller)
    my_sim.add_component(my_heat_pump)
