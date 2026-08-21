"""Test for heat demand calculation in the building module.

The aim is to compare the calculated heat demand in the building module with the heat demand given by TABULA.
"""

# clean
import os
from typing import NamedTuple, Optional, Tuple
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
from hisim.config import ComponentID


# PATH and FUNC needed to build simulator, PATH is fake
PATH: str = "../system_setups/household_for_test_building_u_values.py"


class BuildingUValueParams(NamedTuple):
    """Optional envelope U-value overrides forwarded to the building configuration."""

    u_value_facade_in_watt_per_m2_per_kelvin: Optional[float]
    u_value_roof_in_watt_per_m2_per_kelvin: Optional[float]
    u_value_window_in_watt_per_m2_per_kelvin: Optional[float]
    u_value_door_in_watt_per_m2_per_kelvin: Optional[float]


class BuildingUValueResults(NamedTuple):
    """U-values and thermal demand extracted from a simulated building."""

    u_value_wall: float
    u_value_window: float
    u_value_door: float
    u_value_roof: float
    total_heat_conductance_transmission: float
    max_thermal_building_demand_in_watt: float


# TABULA reference values for the default German single-family home
# (building code DE.N.SFH.05.Gen.ReEx.001.002, heat-capacity class "medium").
# The U-values come directly from the TABULA dataset (U_Actual_*_1 columns of
# episcope-tabula.csv); total_heat_conductance_transmission and
# max_thermal_building_demand_in_watt are derived from the TABULA envelope data
# with the default building configuration.  Hardcoding these lets the first
# simulation run be validated against the TABULA reference instead of against a
# second self-referential run, so a regression in the TABULA defaults would be
# caught rather than masked.
EXPECTED_U_VALUE_WALL: float = 0.234  # U_Actual_Wall_1   [W/(m²·K)]
EXPECTED_U_VALUE_WINDOW: float = 1.3  # U_Actual_Window_1 [W/(m²·K)]
EXPECTED_U_VALUE_DOOR: float = 1.3  # U_Actual_Door_1   [W/(m²·K)]
EXPECTED_U_VALUE_ROOF: float = 0.41  # U_Actual_Roof_1   [W/(m²·K)]
EXPECTED_TOTAL_HEAT_CONDUCTANCE_TRANSMISSION: float = 206.4898  # [W/K]
EXPECTED_MAX_THERMAL_BUILDING_DEMAND_IN_WATT: float = 7780.7522  # [W]


def _build_components(
    my_simulation_parameters: SimulationParameters,
    u_value_params: BuildingUValueParams,
) -> Tuple[
    building.Building,
    weather.Weather,
    loadprofilegenerator_utsp_connector.UtspLpgConnector,
    idealized_electric_heater.IdealizedElectricHeater,
]:
    """Construct the Building, Weather, Occupancy, and IdealizedElectricHeater components.

    Encapsulates all ``BuildingConfig``, ``WeatherConfig``, ``UtspLpgConnectorConfig``,
    and ``IdealizedHeaterConfig`` construction so the orchestrator only wires and runs
    the assembled components.
    """
    set_heating_temperature_for_building_in_celsius = 19.5
    set_cooling_temperature_for_building_in_celsius = 20.5

    # Build Building
    my_building_config = building.BuildingConfig.presets.german_single_family_home
    my_building_config.facade_u_value_in_watt_per_m2_per_kelvin = u_value_params.u_value_facade_in_watt_per_m2_per_kelvin
    my_building_config.roof_u_value_in_watt_per_m2_per_kelvin = u_value_params.u_value_roof_in_watt_per_m2_per_kelvin
    my_building_config.window_u_value_in_watt_per_m2_per_kelvin = u_value_params.u_value_window_in_watt_per_m2_per_kelvin
    my_building_config.door_u_value_in_watt_per_m2_per_kelvin = u_value_params.u_value_door_in_watt_per_m2_per_kelvin
    my_building_config.set_cooling_temperature_in_celsius = set_cooling_temperature_for_building_in_celsius
    my_building_config.set_heating_temperature_in_celsius = set_heating_temperature_for_building_in_celsius
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
        component_id=ComponentID(name="IdealizedElectricHeater"),
        set_heating_temperature_for_building_in_celsius=set_heating_temperature_for_building_in_celsius,
        set_cooling_temperature_for_building_in_celsius=set_cooling_temperature_for_building_in_celsius,
    )
    # Build Fake Heater
    my_idealized_electric_heater = idealized_electric_heater.IdealizedElectricHeater(
        my_simulation_parameters=my_simulation_parameters,
        config=my_idealized_electric_heater_config,
    )
    return my_building, my_weather, my_occupancy, my_idealized_electric_heater


def _connect_components(
    my_building: building.Building,
    my_weather: weather.Weather,
    my_occupancy: loadprofilegenerator_utsp_connector.UtspLpgConnector,
    my_idealized_electric_heater: idealized_electric_heater.IdealizedElectricHeater,
) -> None:
    """Wire the twelve ``connect_input`` calls between the four components."""
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


def _extract_building_results(my_building: building.Building) -> BuildingUValueResults:
    """Read the six U-value and thermal-demand fields from the building information."""
    return BuildingUValueResults(
        u_value_wall=my_building.my_building_information.u_value_wall,
        u_value_window=my_building.my_building_information.u_value_window,
        u_value_door=my_building.my_building_information.u_value_door,
        u_value_roof=my_building.my_building_information.u_value_roof,
        total_heat_conductance_transmission=my_building.my_building_information.total_heat_conductance_transmission,
        max_thermal_building_demand_in_watt=my_building.my_building_information.max_thermal_building_demand_in_watt,
    )


@pytest.mark.buildingtest
@utils.measure_execution_time
def test_house_with_idealized_electric_heater_for_testing_u_values(
    my_simulation_parameters: Optional[SimulationParameters] = None,
) -> None:  # noqa: too-many-statements
    """Test building U-values against TABULA reference values.

    This test runs the simulation twice:

    1. First run: all U-value overrides set to ``None`` so the building uses the
       TABULA defaults. The resulting U-values and thermal demand are compared
       against the hardcoded ``EXPECTED_*`` TABULA reference constants below, so a
       regression in the TABULA defaults is caught rather than masked.
    2. Second run: the roof U-value is explicitly overridden with the known TABULA
       reference value (``EXPECTED_U_VALUE_ROOF``); all other U-values remain
       ``None``. The outputs are compared against the first run to verify that an
       explicit override equal to the default reproduces the default behaviour
       (override consistency). The roof U-value is additionally checked against
       ``EXPECTED_U_VALUE_ROOF`` directly, rather than against the value fed in.

    Args:
        my_simulation_parameters: Optional simulation parameters to override defaults.

    Returns:
        None. Raises AssertionError if any U-value or thermal demand differs from
        its reference by more than 0.01.
    """

    # ------------------------------------------------------------------
    # Run 1: TABULA defaults (all overrides None).
    # Validate against hardcoded TABULA reference values.
    # ------------------------------------------------------------------
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

    log.information("----Run 1 vs TABULA reference----")
    assert abs(u_value_wall1_tabula - EXPECTED_U_VALUE_WALL) < 0.01, (
        f"u_value_wall1_tabula: {u_value_wall1_tabula} != {EXPECTED_U_VALUE_WALL}"
    )
    assert abs(u_value_window1_tabula - EXPECTED_U_VALUE_WINDOW) < 0.01, (
        f"u_value_window1_tabula: {u_value_window1_tabula} != {EXPECTED_U_VALUE_WINDOW}"
    )
    assert abs(u_value_door1_tabula - EXPECTED_U_VALUE_DOOR) < 0.01, (
        f"u_value_door1_tabula: {u_value_door1_tabula} != {EXPECTED_U_VALUE_DOOR}"
    )
    assert abs(u_value_roof1_tabula - EXPECTED_U_VALUE_ROOF) < 0.01, (
        f"u_value_roof1_tabula: {u_value_roof1_tabula} != {EXPECTED_U_VALUE_ROOF}"
    )
    assert (
        abs(total_heat_conductance_transmission_tabula - EXPECTED_TOTAL_HEAT_CONDUCTANCE_TRANSMISSION)
        < 0.01
    ), (
        f"total_heat_conductance_transmission_tabula: "
        f"{total_heat_conductance_transmission_tabula} != "
        f"{EXPECTED_TOTAL_HEAT_CONDUCTANCE_TRANSMISSION}"
    )
    assert (
        abs(max_thermal_building_demand_in_watt_tabula - EXPECTED_MAX_THERMAL_BUILDING_DEMAND_IN_WATT)
        < 0.01
    ), (
        f"max_thermal_building_demand_in_watt_tabula: "
        f"{max_thermal_building_demand_in_watt_tabula} != "
        f"{EXPECTED_MAX_THERMAL_BUILDING_DEMAND_IN_WATT}"
    )

    # ------------------------------------------------------------------
    # Run 2: explicit roof override equal to the TABULA reference value.
    # Verify the override reproduces the default results (override consistency).
    # ------------------------------------------------------------------
    print("__________")
    u_value_roof_in_watt_per_m2_per_kelvin = EXPECTED_U_VALUE_ROOF

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

    log.information("----Run 2 vs Run 1 (override consistency)----")
    log.information(f"u_value_wall1: {u_value_wall1} != {u_value_wall1_tabula}")
    assert abs(u_value_wall1 - u_value_wall1_tabula) < 0.01, f"u_value_wall1: {u_value_wall1} != {u_value_wall1_tabula}"
    log.information(f"u_value_window1: {u_value_window1} != {u_value_window1_tabula}")
    assert (
        abs(u_value_window1 - u_value_window1_tabula) < 0.01
    ), f"u_value_window1: {u_value_window1} != {u_value_window1_tabula}"
    log.information(f"u_value_door1: {u_value_door1} != {u_value_door1_tabula}")
    assert abs(u_value_door1 - u_value_door1_tabula) < 0.01, f"u_value_door1: {u_value_door1} != {u_value_door1_tabula}"
    log.information(f"u_value_roof1: {u_value_roof1} != {EXPECTED_U_VALUE_ROOF}")
    assert abs(u_value_roof1 - EXPECTED_U_VALUE_ROOF) < 0.01, f"u_value_roof1: {u_value_roof1} != {EXPECTED_U_VALUE_ROOF}"
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
    u_value_facade_in_watt_per_m2_per_kelvin: Optional[float] = None,
    u_value_roof_in_watt_per_m2_per_kelvin: Optional[float] = None,
    u_value_window_in_watt_per_m2_per_kelvin: Optional[float] = None,
    u_value_door_in_watt_per_m2_per_kelvin: Optional[float] = None,
) -> BuildingUValueResults:
    """Build and run a simulator to extract building U-values and thermal demand.

    Constructs a simulator with Building, Weather, Occupancy, and IdealizedElectricHeater
    components, runs all timesteps, and extracts U-values and thermal demand from
    my_building.my_building_information.

    Args:
        my_simulation_parameters: Optional simulation parameters to override defaults.
        u_value_facade_in_watt_per_m2_per_kelvin: Optional U-value for facade in W/(m²·K).
            If None, uses TABULA reference value.
        u_value_roof_in_watt_per_m2_per_kelvin: Optional U-value for roof in W/(m²·K).
            If None, uses TABULA reference value.
        u_value_window_in_watt_per_m2_per_kelvin: Optional U-value for window in W/(m²·K).
            If None, uses TABULA reference value.
        u_value_door_in_watt_per_m2_per_kelvin: Optional U-value for door in W/(m²·K).
            If None, uses TABULA reference value.

    Returns:
        BuildingUValueResults: A NamedTuple containing the wall, window, door, and roof
        U-values, the total heat conductance for transmission, and the maximum thermal
        building demand.
    """

    # =========================================================================================================================================================
    # System Parameters

    # Set Simulation Parameters
    year = 2021
    seconds_per_timestep = 60 * 60

    # =========================================================================================================================================================
    # Build Simulator

    # Build Simulation Parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year(year=year, seconds_per_timestep=seconds_per_timestep)

    # this part is copied from hisim_main
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

    u_value_params = BuildingUValueParams(
        u_value_facade_in_watt_per_m2_per_kelvin=u_value_facade_in_watt_per_m2_per_kelvin,
        u_value_roof_in_watt_per_m2_per_kelvin=u_value_roof_in_watt_per_m2_per_kelvin,
        u_value_window_in_watt_per_m2_per_kelvin=u_value_window_in_watt_per_m2_per_kelvin,
        u_value_door_in_watt_per_m2_per_kelvin=u_value_door_in_watt_per_m2_per_kelvin,
    )

    my_building, my_weather, my_occupancy, my_idealized_electric_heater = _build_components(
        my_simulation_parameters=my_simulation_parameters,
        u_value_params=u_value_params,
    )

    _connect_components(
        my_building=my_building,
        my_weather=my_weather,
        my_occupancy=my_occupancy,
        my_idealized_electric_heater=my_idealized_electric_heater,
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
    log.information(f"sum heating [W*timestep] {sum_heating_in_watt_timestep}")

    results = _extract_building_results(my_building)

    log.information(f"u_value_wall1: {results.u_value_wall}")
    log.information(f"u_value_window1: {results.u_value_window}")
    log.information(f"u_value_door1: {results.u_value_door}")
    log.information(f"u_value_roof1: {results.u_value_roof}")
    log.information("________")
    log.information(f"total_heat_conductance_transmission: {results.total_heat_conductance_transmission}")
    log.information(f"max_termal_building_demand: {results.max_thermal_building_demand_in_watt}")

    return results
