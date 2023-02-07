"""Default Connections Module."""
# clean

from typing import Optional, Any
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_connector
from hisim.components import weather
from hisim.components import generic_pv_system
from hisim.components import building
from hisim.components import generic_heat_pump
from hisim.components import sumbuilder


def basic_household_with_default_connections(
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

    # delete all files in cache:
    # dir = '..//hisim//inputs//cache'
    # for file in os.listdir( dir ):
    #   os.remove( os.path.join( dir, file ) )

    # ==== System Parameters ====

    # Set simulation parameters
    year = 2021
    seconds_per_timestep = 60

    # Set weather
    location = "Aachen"

    # Set photovoltaic system
    time = 2019
    power = 10e3
    load_module_data = False
    module_name = "Hanwha_HSL60P6_PA_4_250T__2013_"
    integrate_inverter = True
    inverter_name = "ABB__MICRO_0_25_I_OUTD_US_208_208V__CEC_2014_"
    name = "PVSystem"
    azimuth = 180
    tilt = 30
    source_weight = 0

    # Set occupancy
    # occupancy_profile = "CH01"

    # Set building
    building_code = "DE.N.SFH.05.Gen.ReEx.001.002"
    building_heat_capacity_class = "medium"
    initial_internal_temperature_in_celsius = 23
    heating_reference_temperature_in_celsius = -14
    absolute_conditioned_floor_area_in_m2 = 121.2
    total_base_area_in_m2 = None

    # Set heat pump controller
    temperature_air_heating_in_celsius = 16.0
    temperature_air_cooling_in_celsius = 24.0
    offset = 0.5
    hp_mode = 2

    # ==== Build Components ====

    # Build system parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year_all_options(
            year=year, seconds_per_timestep=seconds_per_timestep
        )
    my_sim.set_simulation_parameters(my_simulation_parameters)
    # Build occupancy
    my_occupancy_config = loadprofilegenerator_connector.OccupancyConfig(
        profile_name="CH01", name="Occupancy1"
    )
    my_occupancy = loadprofilegenerator_connector.Occupancy(
        config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters
    )
    my_sim.add_component(my_occupancy)

    # Build Weather
    my_weather_config = weather.WeatherConfig.get_default(
        location_entry=weather.LocationEnum.Aachen
    )
    my_weather = weather.Weather(
        config=my_weather_config, my_simulation_parameters=my_simulation_parameters
    )
    my_sim.add_component(my_weather)

    # Build PV
    my_photovoltaic_system_config = generic_pv_system.PVSystemConfig(
        time=time,
        location=location,
        power=power,
        load_module_data=load_module_data,
        module_name=module_name,
        integrate_inverter=integrate_inverter,
        tilt=tilt,
        azimuth=azimuth,
        inverter_name=inverter_name,
        source_weight=source_weight,
        name=name,
    )
    my_photovoltaic_system = generic_pv_system.PVSystem(
        config=my_photovoltaic_system_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    my_sim.add_component(my_photovoltaic_system)
    my_photovoltaic_system.connect_only_predefined_connections(my_weather)
    # Build Building
    my_building_config = building.BuildingConfig(
        building_code=building_code,
        building_heat_capacity_class=building_heat_capacity_class,
        initial_internal_temperature_in_celsius=initial_internal_temperature_in_celsius,
        heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
        absolute_conditioned_floor_area_in_m2=absolute_conditioned_floor_area_in_m2,
        total_base_area_in_m2=total_base_area_in_m2,
        name="Building",
    )

    my_base_electricity_load_profile = sumbuilder.ElectricityGrid(
        config=sumbuilder.ElectricityGridConfig.get_default_electricity_grid(),
        my_simulation_parameters=my_simulation_parameters,
    )
    my_sim.add_component(my_base_electricity_load_profile)

    my_building = building.Building(
        config=my_building_config, my_simulation_parameters=my_simulation_parameters
    )
    my_building.connect_only_predefined_connections(my_weather, my_occupancy)
    my_sim.add_component(my_building)

    my_heat_pump_controller = generic_heat_pump.GenericHeatPumpController(
        config=generic_heat_pump.GenericHeatPumpControllerConfig(name="GenericHeatPumpController", temperature_air_heating_in_celsius=temperature_air_heating_in_celsius,
        temperature_air_cooling_in_celsius=temperature_air_cooling_in_celsius,
        offset=offset,
        mode=hp_mode),
        my_simulation_parameters=my_simulation_parameters,
    )
    my_heat_pump_controller.connect_only_predefined_connections(my_building)

    # depending on previous loads, hard to define default connections
    my_heat_pump_controller.connect_input(
        my_heat_pump_controller.ElectricityInput,
        my_base_electricity_load_profile.component_name,
        my_base_electricity_load_profile.ElectricityOutput,
    )
    my_sim.add_component(my_heat_pump_controller)

    my_heat_pump = generic_heat_pump.GenericHeatPump(
        config=generic_heat_pump.GenericHeatPumpConfig.get_default_generic_heat_pump_config(),
        my_simulation_parameters=my_simulation_parameters,
    )
    my_heat_pump.connect_only_predefined_connections(
        my_weather, my_heat_pump_controller
    )

    my_sim.add_component(my_heat_pump)

    # depending on type of heating device, hard to define default connections
    my_building.connect_input(
        my_building.ThermalPowerDelivered,
        my_heat_pump.component_name,
        my_heat_pump.ThermalPowerDelivered,
    )
