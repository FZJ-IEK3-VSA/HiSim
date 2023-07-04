"""Test for grid energy balancer."""

# clean

import os
from typing import Optional
import pytest
import hisim.simulator as sim
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_connector
from hisim.components import weather
from hisim.components import building, grid_energy_balancer, generic_pv_system, idealized_electric_heater
from hisim import utils, loadtypes


# PATH and FUNC needed to build simulator, PATH is fake
PATH = "../examples/household_for_test_grid_energy_balancer.py"
FUNC = "test_house"


@utils.measure_execution_time
@pytest.mark.base
def test_house(
    my_simulation_parameters: Optional[SimulationParameters] = None,
) -> None:  # noqa: too-many-statements
    """The test should check if a normal simulation works with the grid energy balancer implementation.

    """

    # =========================================================================================================================================================
    # System Parameters

    # Set Simulation Parameters
    year = 2021
    seconds_per_timestep = 60 * 15

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
        module_filename= "household_for_test_grid_energy_balancer.py"
    )
    my_sim.set_simulation_parameters(my_simulation_parameters)

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
    
    # Build Grid Energy Balancer
    
    my_electricity_grid = grid_energy_balancer.GridEnergyBalancer(
                                            my_simulation_parameters=my_simulation_parameters,
                                            config=grid_energy_balancer.GridEnergyBalancerConfig.get_GridEnergyBalancer_default_config())

    # Build Fake Heater
    my_idealized_electric_heater = idealized_electric_heater.IdealizedElectricHeater(
        my_simulation_parameters=my_simulation_parameters,
        config=idealized_electric_heater.IdealizedHeaterConfig.get_default_config(),
    )

    # =========================================================================================================================================================
    # Connect Components

    # PV System
    my_photovoltaic_system.connect_only_predefined_connections(my_weather)

    # Building
    my_building.connect_only_predefined_connections(my_weather, my_occupancy)
    my_building.connect_input(
        my_building.ThermalPowerDelivered,
        my_idealized_electric_heater.component_name,
        my_idealized_electric_heater.ThermalPowerDelivered,
    )

    # Idealized Heater
    my_idealized_electric_heater.connect_input(
        my_idealized_electric_heater.TheoreticalThermalBuildingDemand,
        my_building.component_name,
        my_building.TheoreticalThermalBuildingDemand,
    )
    
    # Electricity Grid
    
    my_electricity_grid.add_component_input_and_connect(
        source_component_class=my_photovoltaic_system,
        source_component_output=my_photovoltaic_system.ElectricityOutput,
        source_load_type=loadtypes.LoadTypes.ELECTRICITY,
        source_unit=loadtypes.Units.WATT,
        source_tags=[loadtypes.ComponentType.PV, loadtypes.InandOutputType.ELECTRICITY_PRODUCTION],
        source_weight=999,
    )
    
    my_electricity_grid.add_component_input_and_connect(
        source_component_class=my_occupancy,
        source_component_output=my_occupancy.ElectricityOutput,
        source_load_type=loadtypes.LoadTypes.ELECTRICITY,
        source_unit=loadtypes.Units.WATT,
        source_tags=[loadtypes.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
        source_weight=999,
    )
    

    # =========================================================================================================================================================
    # Add Components to Simulator and run all timesteps

    my_sim.add_component(my_weather)
    my_sim.add_component(my_photovoltaic_system)
    my_sim.add_component(my_occupancy)
    my_sim.add_component(my_building)
    
    my_sim.add_component(my_idealized_electric_heater)
    my_sim.add_component(my_electricity_grid)

    my_sim.run_all_timesteps()

