"""Test for heat demand calculation in the building module.

The aim is to compare the calculated heat demand in the building module with the heat demand given by TABULA.
"""

# clean
import os
from typing import Optional, Tuple
import pytest

# import numpy as np

import hisim.simulator as sim
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_utsp_connector
from hisim.components import weather
from hisim.components import building
from hisim.components import idealized_electric_heater
from hisim import log
from hisim import utils


# PATH and FUNC needed to build simulator, PATH is fake
PATH = "../system_setups/household_for_test_building_u_values.py"


@pytest.mark.buildingtest
@utils.measure_execution_time
def test_house_with_idealized_electric_heater_for_testing_u_values(
    my_simulation_parameters: Optional[SimulationParameters] = None,
) -> None:  # noqa: too-many-statements
    """Test for bulding with u values."""

    (
        u_value_wall1_tabula,
        u_value_window1_tabula,
        u_value_door1_tabula,
        u_value_roof1_tabula,
        total_heat_conductance_transmission_tabula,
        max_thermal_building_demand_in_watt_tabula,
    ) = house_with_idealized_electric_heater_for_testing_u_values(
        my_simulation_parameters=my_simulation_parameters,
        u_value_facade_in_watt_per_m2_per_kelvin=None,
        u_value_roof_in_watt_per_m2_per_kelvin=None,
        u_value_window_in_watt_per_m2_per_kelvin=None,
        u_value_door_in_watt_per_m2_per_kelvin=None,
    )

    print("__________")
    u_value_roof_in_watt_per_m2_per_kelvin = u_value_roof1_tabula

    (
        u_value_wall1,
        u_value_window1,
        u_value_door1,
        u_value_roof1,
        total_heat_conductance_transmission,
        max_thermal_building_demand_in_watt,
    ) = house_with_idealized_electric_heater_for_testing_u_values(
        my_simulation_parameters=my_simulation_parameters,
        u_value_facade_in_watt_per_m2_per_kelvin=None,
        u_value_roof_in_watt_per_m2_per_kelvin=u_value_roof_in_watt_per_m2_per_kelvin,
        u_value_window_in_watt_per_m2_per_kelvin=None,
        u_value_door_in_watt_per_m2_per_kelvin=None,
    )

    log.information("----Results----")
    log.information(f"u_value_wall1: {u_value_wall1} != {u_value_wall1_tabula}")
    assert abs(u_value_wall1 - u_value_wall1_tabula) < 0.01, f"u_value_wall1: {u_value_wall1} != {u_value_wall1_tabula}"
    log.information(f"u_value_window1: {u_value_window1} != {u_value_window1_tabula}")
    assert (
        abs(u_value_window1 - u_value_window1_tabula) < 0.01
    ), f"u_value_window1: {u_value_window1} != {u_value_window1_tabula}"
    log.information(f"u_value_door1: {u_value_door1} != {u_value_door1_tabula}")
    assert abs(u_value_door1 - u_value_door1_tabula) < 0.01, f"u_value_door1: {u_value_door1} != {u_value_door1_tabula}"
    log.information(f"u_value_roof1: {u_value_roof1} != {u_value_roof1_tabula}")
    assert abs(u_value_roof1 - u_value_roof1_tabula) < 0.01, f"u_value_roof1: {u_value_roof1} != {u_value_roof1_tabula}"
    log.information(
        f"total_heat_conductance_transmission: "
        f"{total_heat_conductance_transmission} != "
        f"{total_heat_conductance_transmission_tabula}"
    )
    assert (
        abs(
            total_heat_conductance_transmission
            - total_heat_conductance_transmission_tabula
        )
        < 0.01
    ), (f"total_heat_conductance_transmission: "
        f"{total_heat_conductance_transmission} != "
        f"{total_heat_conductance_transmission_tabula}")
    log.information(
        f"max_thermal_building_demand_in_watt: {max_thermal_building_demand_in_watt} != {max_thermal_building_demand_in_watt_tabula}"
    )
    assert (
        abs(max_thermal_building_demand_in_watt - max_thermal_building_demand_in_watt_tabula) < 0.01
    ), f"max_thermal_building_demand_in_watt: {max_thermal_building_demand_in_watt} != {max_thermal_building_demand_in_watt_tabula}"


def house_with_idealized_electric_heater_for_testing_u_values(
    my_simulation_parameters: Optional[SimulationParameters] = None,
    u_value_facade_in_watt_per_m2_per_kelvin=None,
    u_value_roof_in_watt_per_m2_per_kelvin=None,
    u_value_window_in_watt_per_m2_per_kelvin=None,
    u_value_door_in_watt_per_m2_per_kelvin=None,
) -> Tuple[float, float, float, float, float, float]:  # noqa: too-many-statements
    """Test for u values."""

    # =========================================================================================================================================================
    # System Parameters

    # Set Simulation Parameters
    year = 2021
    seconds_per_timestep = 60 * 60

    # Set Fake Heater
    set_heating_temperature_for_building_in_celsius = 19.5
    set_cooling_temperature_for_building_in_celsius = 20.5

    # =========================================================================================================================================================
    # Build Components

    # Build Simulation Parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year(year=year, seconds_per_timestep=seconds_per_timestep)

    # this part is copied from hisim_main
    # Build Simulator
    normalized_path = os.path.normpath(PATH)
    path_in_list = normalized_path.split(os.sep)
    if len(path_in_list) >= 1:
        path_to_be_added = os.path.join(os.getcwd(), *path_in_list[:-1])

    my_sim: sim.Simulator = sim.Simulator(
        module_directory=path_to_be_added,
        my_simulation_parameters=my_simulation_parameters,
        module_filename="household_for_test_building_u_vbalues",
    )
    my_sim.set_simulation_parameters(my_simulation_parameters)

    # Build Building
    my_building_config = building.BuildingConfig.get_default_german_single_family_home(
        facade_u_value_in_watt_per_m2_per_kelvin=u_value_facade_in_watt_per_m2_per_kelvin,
        roof_u_value_in_watt_per_m2_per_kelvin=u_value_roof_in_watt_per_m2_per_kelvin,
        window_u_value_in_watt_per_m2_per_kelvin=u_value_window_in_watt_per_m2_per_kelvin,
        door_u_value_in_watt_per_m2_per_kelvin=u_value_door_in_watt_per_m2_per_kelvin,
        set_cooling_temperature_in_celsius=set_cooling_temperature_for_building_in_celsius,
        set_heating_temperature_in_celsius=set_heating_temperature_for_building_in_celsius,
    )
    my_building = building.Building(config=my_building_config, my_simulation_parameters=my_simulation_parameters)
    # Occupancy
    my_occupancy_config = loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig.get_default_utsp_connector_config()
    my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
        config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters
    )

    # Build Weather
    my_weather_config = weather.WeatherConfig.get_default(location_entry=weather.LocationEnum.AACHEN)
    my_weather = weather.Weather(config=my_weather_config, my_simulation_parameters=my_simulation_parameters)
    # Build Fake Heater Config
    my_idealized_electric_heater_config = idealized_electric_heater.IdealizedHeaterConfig(
        building_name="BUI1",
        name="IdealizedElectricHeater",
        set_heating_temperature_for_building_in_celsius=set_heating_temperature_for_building_in_celsius,
        set_cooling_temperature_for_building_in_celsius=set_cooling_temperature_for_building_in_celsius,
    )
    # Build Fake Heater
    my_idealized_electric_heater = idealized_electric_heater.IdealizedElectricHeater(
        my_simulation_parameters=my_simulation_parameters,
        config=my_idealized_electric_heater_config,
    )
    # =========================================================================================================================================================
    # Connect Components

    # Building
    my_building.connect_input(my_building.Altitude, my_weather.component_name, my_weather.Altitude)
    my_building.connect_input(my_building.Azimuth, my_weather.component_name, my_weather.Azimuth)
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
    my_building.connect_input(my_building.ApparentZenith, my_weather.component_name, my_weather.ApparentZenith)
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
        my_building.HeatingByDevices,
        my_occupancy.component_name,
        my_occupancy.HeatingByDevices,
    )
    my_building.connect_input(
        my_building.ThermalPowerDelivered,
        my_idealized_electric_heater.component_name,
        my_idealized_electric_heater.ThermalPowerDelivered,
    )

    # Fake Heater
    my_idealized_electric_heater.connect_input(
        my_idealized_electric_heater.TheoreticalThermalBuildingDemand,
        my_building.component_name,
        my_building.TheoreticalThermalBuildingDemand,
    )

    # =========================================================================================================================================================
    # Add Components to Simulator and run all timesteps

    my_sim.add_component(my_weather)
    my_sim.add_component(my_occupancy)
    my_sim.add_component(my_building)
    my_sim.add_component(my_idealized_electric_heater)

    my_sim.run_all_timesteps()

    # =========================================================================================================================================================
    # Calculate annual heat pump heating energy

    results_heating = my_sim.results_data_frame["IdealizedElectricHeater - HeatingPowerDelivered [Heating - W]"]

    sum_heating_in_watt_timestep = sum(results_heating)
    log.information("sum heating [W*timestep] " + str(sum_heating_in_watt_timestep))

    u_value_wall1 = my_building.my_building_information.buildingdata_ref["U_Actual_Wall_1"].values[0]

    u_value_window1 = my_building.my_building_information.buildingdata_ref["U_Actual_Window_1"].values[0]

    u_value_door1 = my_building.my_building_information.buildingdata_ref["U_Actual_Door_1"].values[0]

    u_value_roof1 = my_building.my_building_information.buildingdata_ref["U_Actual_Roof_1"].values[0]

    max_thermal_building_demand_in_watt = my_building.my_building_information.max_thermal_building_demand_in_watt
    total_heat_conductance_transmission = (
        my_building.my_building_information.total_heat_conductance_transmission
    )

    log.information("u_value_wall1: " + str(u_value_wall1))
    log.information("u_value_window1: " + str(u_value_window1))
    log.information("u_value_door1: " + str(u_value_door1))
    log.information("u_value_roof1: " + str(u_value_roof1))
    log.information("________")
    log.information(
        "total_heat_conductance_transmission: "
        + str(total_heat_conductance_transmission)
    )
    log.information("max_termal_building_demand: " + str(max_thermal_building_demand_in_watt))

    return (
        u_value_wall1,
        u_value_window1,
        u_value_door1,
        u_value_roof1,
        total_heat_conductance_transmission,
        max_thermal_building_demand_in_watt,
    )
