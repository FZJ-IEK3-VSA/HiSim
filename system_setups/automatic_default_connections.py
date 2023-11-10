"""Household identical to default connections module but with automatic default connections."""
# clean

from typing import Optional, Any
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_connector
from hisim.components import weather
from hisim.components import generic_pv_system
from hisim.components import building
from hisim.components import generic_heat_pump
from hisim.components import electricity_meter
from hisim import loadtypes


def setup_function(
    my_sim: Any, my_simulation_parameters: Optional[SimulationParameters] = None
) -> Any:
    """The setup function emulates an household including the basic components.

    Here the residents have their electricity and heating needs covered
    by the photovoltaic system and the heat pump.

    - Simulation Parameters
    - Components
        - Occupancy (Residents' Demands)
        - Weather
        - Photovoltaic System
        - Building
        - Heat Pump

    """

    # ==== System Parameters ====

    # Set simulation parameters
    year = 2021
    seconds_per_timestep = 60

    # ==== Build Components ====

    # Build system parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year_all_options(
            year=year, seconds_per_timestep=seconds_per_timestep
        )
    my_sim.set_simulation_parameters(my_simulation_parameters)

    # Build Building
    my_building_config = building.BuildingConfig.get_default_german_single_family_home()
    my_building = building.Building(
        config=my_building_config, my_simulation_parameters=my_simulation_parameters
    )

    # Build occupancy
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

    my_heat_pump_controller = generic_heat_pump.GenericHeatPumpController(
        config=generic_heat_pump.GenericHeatPumpControllerConfig.get_default_generic_heat_pump_controller_config(),
        my_simulation_parameters=my_simulation_parameters,
    )

    # depending on previous loads, hard to define default connections
    my_heat_pump_controller.connect_input(
        my_heat_pump_controller.ElectricityInput,
        my_electricity_meter.component_name,
        my_electricity_meter.ElectricityAvailable,
    )

    my_heat_pump = generic_heat_pump.GenericHeatPump(
        config=generic_heat_pump.GenericHeatPumpConfig.get_default_generic_heat_pump_config(),
        my_simulation_parameters=my_simulation_parameters,
    )

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
        source_component_output=my_heat_pump.ElectricityOutput,
        source_load_type=loadtypes.LoadTypes.ELECTRICITY,
        source_unit=loadtypes.Units.WATT,
        source_tags=[
            loadtypes.ComponentType.HEAT_PUMP,
            loadtypes.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED,
        ],
        source_weight=999,
    )

    # depending on type of heating device, hard to define default connections
    my_building.connect_input(
        my_building.ThermalPowerDelivered,
        my_heat_pump.component_name,
        my_heat_pump.ThermalPowerDelivered,
    )

    my_sim.add_component(my_building, connect_automatically=True)
    my_sim.add_component(my_occupancy, connect_automatically=True)
    my_sim.add_component(my_weather, connect_automatically=True)
    my_sim.add_component(my_photovoltaic_system, connect_automatically=True)
    my_sim.add_component(my_heat_pump, connect_automatically=True)
    my_sim.add_component(my_heat_pump_controller, connect_automatically=True)
    my_sim.add_component(my_electricity_meter, connect_automatically=True)
