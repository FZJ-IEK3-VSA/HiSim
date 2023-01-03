"""Test for heat demand calculation in the building module.

The aim is to compare the calculated heat demand in the building module with the heat demand given by TABULA.
"""
# clean
import os
from typing import Optional
import numpy as np

# from hisim import hisim_main
import hisim.simulator as sim
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_connector
from hisim.components import weather
from hisim.components import generic_pv_system
from hisim.components import building
from hisim.components import generic_heat_pump
from hisim.components import sumbuilder

__authors__ = "Vitor Hugo Bellotto Zago, Noah Pflugradt"
__copyright__ = "Copyright 2022, FZJ-IEK-3"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "1.0"
__maintainer__ = "Noah Pflugradt"
__status__ = "development"


# @utils.measure_execution_time
# def test_basic_household():
#     """ Single day. """
#     path = "../examples/household_with_fake_heater.py"
#     func = "household_fake_heating"
#     mysimpar = SimulationParameters.full_year_all_options(year=2019, seconds_per_timestep=60)
#     hisim_main.main(path, func, mysimpar)
#     log.information(os.getcwd())
#     log.information("after simulation run:")


# @utils.measure_execution_time
# def test_basic_household():
#     """ Single day. """
PATH = "../examples/household_for_test_building_heat_demand.py"
FUNC = "house_with_pv_and_hp_for_heating_test"
#     mysimpar = SimulationParameters.full_year(year=2019, seconds_per_timestep=60)
#     hisim_main.main(path, func, mysimpar)
#     log.information(os.getcwd())


def test_house_with_pv_and_hp_for_heating_test(
    my_simulation_parameters: Optional[SimulationParameters] = None,
) -> None:  # noqa: too-many-statements
    """Test for heating energy demand.

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
    """

    # =========================================================================================================================================================
    # System Parameters

    # Set Simulation Parameters
    year = 2021
    seconds_per_timestep = 60

    # Set Weather
    location = "Aachen"

    # Set Photovoltaic System
    time = 2019
    power = 10e3
    load_module_data = False
    module_name = "Hanwha_HSL60P6_PA_4_250T__2013_"
    integrate_inverter = True
    inverter_name = "ABB__MICRO_0_25_I_OUTD_US_208_208V__CEC_2014_"
    name = "PVSystem"
    azimuth = 180
    tilt = 30
    source_weight = -1

    # Set Occupancy
    occupancy_profile = "CH01"

    # Set Building
    building_code = "DE.N.SFH.05.Gen.ReEx.001.002"
    building_heat_capacity_class = "medium"
    initial_temperature_in_celsius = 23
    heating_reference_temperature_in_celsius = -14
    absolute_conditioned_floor_area_in_m2 = 121.2
    total_base_area_in_m2 = None

    # Set Heat Pump Controller
    temperature_air_heating_in_celsius = 18
    temperature_air_cooling_in_celsius = 21
    offset = 0.5
    hp_mode = 2

    # Set Heat Pump
    hp_manufacturer = "Viessmann Werke GmbH & Co KG"
    hp_name = "Vitocal 300-A AWO-AC 301.B07"
    hp_min_operation_time = 60
    hp_min_idle_time = 15

    # =========================================================================================================================================================
    # Build Components

    # Build Simulation Parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year_all_options(
            year=year, seconds_per_timestep=seconds_per_timestep
        )
    # this part is copied from hisim_main
    # Build Simulator
    normalized_path = os.path.normpath(PATH)
    path_in_list = normalized_path.split(os.sep)
    if len(path_in_list) >= 1:
        path_to_be_added = os.path.join(os.getcwd(), *path_in_list[:-1])

    my_sim: sim.Simulator = sim.Simulator(
        module_directory=path_to_be_added,
        setup_function=FUNC,
        my_simulation_parameters=my_simulation_parameters,
    )
    my_sim.set_simulation_parameters(my_simulation_parameters)

    # Build Occupancy
    my_occupancy_config = loadprofilegenerator_connector.OccupancyConfig(
        profile_name=occupancy_profile, name="Occupancy"
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

    # Build Building
    my_building_config = building.BuildingConfig(
        building_code=building_code,
        building_heat_capacity_class=building_heat_capacity_class,
        initial_internal_temperature_in_celsius=initial_temperature_in_celsius,
        heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
        name="Building1",
        absolute_conditioned_floor_area_in_m2=absolute_conditioned_floor_area_in_m2,
        total_base_area_in_m2=total_base_area_in_m2,
    )
    my_building = building.Building(
        config=my_building_config, my_simulation_parameters=my_simulation_parameters
    )

    # Build Electricity Grid
    my_base_electricity_load_profile = sumbuilder.ElectricityGrid(
        name="BaseLoad",
        grid=[my_occupancy, "Subtract", my_photovoltaic_system],
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Pump
    my_heat_pump = generic_heat_pump.GenericHeatPump(
        manufacturer=hp_manufacturer,
        name=hp_name,
        min_operation_time=hp_min_operation_time,
        min_idle_time=hp_min_idle_time,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Pump Controller
    my_heat_pump_controller = generic_heat_pump.HeatPumpController(
        temperature_air_heating_in_celsius=temperature_air_heating_in_celsius,
        temperature_air_cooling_in_celsius=temperature_air_cooling_in_celsius,
        offset=offset,
        mode=hp_mode,
        my_simulation_parameters=my_simulation_parameters,
    )
    # =========================================================================================================================================================
    # Connect Components

    # PV System
    my_photovoltaic_system.connect_input(
        my_photovoltaic_system.TemperatureOutside,
        my_weather.component_name,
        my_weather.TemperatureOutside,
    )
    my_photovoltaic_system.connect_input(
        my_photovoltaic_system.DirectNormalIrradiance,
        my_weather.component_name,
        my_weather.DirectNormalIrradiance,
    )
    my_photovoltaic_system.connect_input(
        my_photovoltaic_system.DirectNormalIrradianceExtra,
        my_weather.component_name,
        my_weather.DirectNormalIrradianceExtra,
    )
    my_photovoltaic_system.connect_input(
        my_photovoltaic_system.DiffuseHorizontalIrradiance,
        my_weather.component_name,
        my_weather.DiffuseHorizontalIrradiance,
    )
    my_photovoltaic_system.connect_input(
        my_photovoltaic_system.GlobalHorizontalIrradiance,
        my_weather.component_name,
        my_weather.GlobalHorizontalIrradiance,
    )
    my_photovoltaic_system.connect_input(
        my_photovoltaic_system.Azimuth, my_weather.component_name, my_weather.Azimuth
    )
    my_photovoltaic_system.connect_input(
        my_photovoltaic_system.ApparentZenith,
        my_weather.component_name,
        my_weather.ApparentZenith,
    )
    my_photovoltaic_system.connect_input(
        my_photovoltaic_system.WindSpeed,
        my_weather.component_name,
        my_weather.WindSpeed,
    )

    # Building
    my_building.connect_input(
        my_building.Altitude, my_weather.component_name, my_weather.Altitude
    )
    my_building.connect_input(
        my_building.Azimuth, my_weather.component_name, my_weather.Azimuth
    )
    my_building.connect_input(
        my_building.DirectNormalIrradiance,
        my_weather.component_name,
        my_weather.DirectNormalIrradiance,
    )
    my_building.connect_input(
        my_building.DiffuseHorizontalIrradiance,
        my_weather.component_name,
        my_weather.DiffuseHorizontalIrradiance,
    )
    my_building.connect_input(
        my_building.GlobalHorizontalIrradiance,
        my_weather.component_name,
        my_weather.GlobalHorizontalIrradiance,
    )
    my_building.connect_input(
        my_building.DirectNormalIrradianceExtra,
        my_weather.component_name,
        my_weather.DirectNormalIrradianceExtra,
    )
    my_building.connect_input(
        my_building.ApparentZenith, my_weather.component_name, my_weather.ApparentZenith
    )
    my_building.connect_input(
        my_building.TemperatureOutside,
        my_weather.component_name,
        my_weather.TemperatureOutside,
    )
    my_building.connect_input(
        my_building.HeatingByResidents,
        my_occupancy.component_name,
        my_occupancy.HeatingByResidents,
    )
    my_building.connect_input(
        my_building.ThermalEnergyDelivered,
        my_heat_pump.component_name,
        my_heat_pump.ThermalEnergyDelivered,
    )

    # Heat Pump
    my_heat_pump.connect_input(
        my_heat_pump.State,
        my_heat_pump_controller.component_name,
        my_heat_pump_controller.State,
    )
    my_heat_pump.connect_input(
        my_heat_pump.TemperatureOutside,
        my_weather.component_name,
        my_weather.TemperatureOutside,
    )

    # Heat Pump Controller
    my_heat_pump_controller.connect_input(
        my_heat_pump_controller.TemperatureMean,
        my_building.component_name,
        my_building.TemperatureMean,
    )
    my_heat_pump_controller.connect_input(
        my_heat_pump_controller.ElectricityInput,
        my_base_electricity_load_profile.component_name,
        my_base_electricity_load_profile.ElectricityOutput,
    )

    # =========================================================================================================================================================
    # Add Components to Simulator and run all timesteps

    my_sim.add_component(my_weather)
    my_sim.add_component(my_occupancy)
    my_sim.add_component(my_building)
    my_sim.add_component(my_photovoltaic_system)
    my_sim.add_component(my_base_electricity_load_profile)
    my_sim.add_component(my_heat_pump)
    my_sim.add_component(my_heat_pump_controller)

    my_sim.run_all_timesteps()

    # =========================================================================================================================================================
    # Test annual floor related heating demand

    tabula_conditioned_floor_area_in_m2 = my_building.buildingdata["A_C_Ref"].values[0]
    energy_need_for_heating_given_by_tabula_in_kilowatt_hour_per_year_per_m2 = (
        my_building.buildingdata["q_h_nd"].values[0]
    )
    energy_need_for_heating_from_heat_pump_in_kilowatt_hour_per_year_per_m2 = (
        my_heat_pump.heating_energy_in_watt_hour
        / (1000 * tabula_conditioned_floor_area_in_m2)
    )
    # test whether tabula energy demand for heating is equal to energy demand for heating generated from heat pump with a tolerance of 10%
    np.testing.assert_allclose(
        energy_need_for_heating_given_by_tabula_in_kilowatt_hour_per_year_per_m2,
        energy_need_for_heating_from_heat_pump_in_kilowatt_hour_per_year_per_m2,
        rtol=0.1,
    )
