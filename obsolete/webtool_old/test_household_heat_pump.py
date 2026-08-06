"""Integration tests for the household heat pump system setup.

Exercises ``system_setups/household_heat_pump.py`` through both the direct
:func:`hisim.hisim_main.main` entry point and the
:func:`hisim.system_setup_starter.make_system_setup` JSON-config pathway.
All tests are marked ``system_setups`` and are excluded from the fast ``base``
test gate.
"""

import copy
import json
import shutil
from pathlib import Path

import pytest

from hisim import hisim_main, log, utils
from hisim.hisim_main import main
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.simulationparameters import SimulationParameters
from hisim.system_setup_starter import make_system_setup
from tests.testing_utils import TestingUtils

MY_PATH_TO_MODULE = "../system_setups/household_heat_pump.py"
MY_SIMULATION_PARAMETERS = {
    "start_date": "2021-01-01T00:00:00",
    "end_date": "2021-01-02T00:00:00",
    "seconds_per_timestep": 900,
    "post_processing_options": [9, 18, 19, 20, 22],
    "log_connections": True,
}


def create_results_directory(result_directory):
    """Ensure a fresh, empty results directory exists at the given path.

    If a directory already exists at ``result_directory`` it is removed first
    (ignoring errors), then a new directory is created including any parent
    directories.

    Args:
        result_directory: Path to the desired results directory.
    """
    if Path(result_directory).is_dir():
        shutil.rmtree(result_directory, ignore_errors=True)
    Path(result_directory).mkdir(parents=True, exist_ok=True)


def run_system(config_json, result_directory):
    """Build and execute a household heat pump system setup from a JSON config.

    Translates ``config_json`` into a module path, simulation parameters, and
    module config via :func:`hisim.system_setup_starter.make_system_setup`,
    then runs the simulation with :func:`hisim.hisim_main.main`.

    Args:
        config_json: Configuration dictionary describing the system setup,
            including the module path and simulation parameters.
        result_directory: Directory where simulation results are written.
    """
    (
        path_to_module,
        simulation_parameters,
        module_config_path,
    ) = make_system_setup(
        parameters_json=config_json,
        result_directory=result_directory,
    )
    main(
        path_to_module,
        simulation_parameters,
        module_config_path,
    )


def remove_results_directory(result_directory):
    """Remove the results directory if it exists.

    Errors during removal are ignored.

    Args:
        result_directory: Path to the results directory to remove.
    """
    shutil.rmtree(result_directory, ignore_errors=True)


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_heat_pump_main():
    """Run the household heat pump setup with defaults via hisim_main.

    Executes a one-day simulation at 60-second resolution with network-chart
    post-processing enabled, then asserts that ``finished.flag`` was written to
    the result directory.
    """

    path = "../system_setups/household_heat_pump.py"
    my_simulation_parameters = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.MAKE_NETWORK_CHARTS)
    hisim_main.main(path, my_simulation_parameters)
    log.information(str(Path.cwd()))

    # Verify the simulation actually completed and produced its result artifacts.
    # hisim_main.main mutates result_directory in place on the same
    # SimulationParameters object, and writes finished.flag only on success.
    finished = Path(my_simulation_parameters.result_directory).joinpath("finished.flag")
    assert finished.is_file(), f"Simulation did not produce {finished}"


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_heat_pump_system_setup_starter_default():
    """Run the household heat pump setup via the system setup starter with defaults.

    Builds the system from a JSON config using default simulation parameters
    (one day, 900-second timesteps) and asserts that the simulation produced a
    ``finished.flag`` file in the result directory.
    """
    my_config_json = {"path_to_module": MY_PATH_TO_MODULE, "simulation_parameters": copy.deepcopy(MY_SIMULATION_PARAMETERS)}

    result_directory = TestingUtils.get_result_directory()
    create_results_directory(result_directory)
    run_system(my_config_json, result_directory)

    #  Check if calculation has finished without errors.
    assert Path(result_directory).joinpath("finished.flag").is_file()

    remove_results_directory(result_directory)


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_heat_pump_system_setup_starter_pv():
    """Run the household heat pump setup with photovoltaic enabled.

    Builds the system from a JSON config that enables the photovoltaic option and
    provides a detailed building configuration, then asserts that the PV system is
    connected to the electricity meter by inspecting ``component_connections.json``.
    """
    my_config_json = {
        "path_to_module": MY_PATH_TO_MODULE,
        "simulation_parameters": copy.deepcopy(MY_SIMULATION_PARAMETERS),
        "building_config": {
            "building_name": "BUI1",
            "name": "Building",
            "building_code": "DE.N.SFH.05.Gen.ReEx.001.002",
            "building_heat_capacity_class": "medium",
            "initial_internal_temperature_in_celsius": 23,
            "heating_reference_temperature_in_celsius": -7.0,
            "absolute_conditioned_floor_area_in_m2": 121.2,
            "total_base_area_in_m2": None,
            "number_of_apartments": 1,
            "predictive": False,
            "set_heating_temperature_in_celsius": 19.0,
            "set_cooling_temperature_in_celsius": 24.0,
            "enable_opening_windows": False,
            "max_thermal_building_demand_in_watt": None,
            "floor_u_value_in_watt_per_m2_per_kelvin": None,
            "floor_area_in_m2": None,
            "facade_u_value_in_watt_per_m2_per_kelvin": None,
            "facade_area_in_m2": None,
            "roof_u_value_in_watt_per_m2_per_kelvin": None,
            "roof_area_in_m2": None,
            "window_u_value_in_watt_per_m2_per_kelvin": None,
            "window_area_in_m2": None,
            "door_u_value_in_watt_per_m2_per_kelvin": None,
            "door_area_in_m2": None,
            "device_co2_footprint_in_kg": 1,
            "investment_costs_in_euro": 1,
            "maintenance_costs_in_euro_per_year": 0.01,
            "subsidy_as_percentage_of_investment_costs": 0,
            "lifetime_in_years": 1,
        },
        "options": {"photovoltaic": True},
    }

    result_directory = TestingUtils.get_result_directory()
    create_results_directory(result_directory)
    run_system(my_config_json, result_directory)

    # Check if PV has been build and is connected.
    with open(Path(result_directory).joinpath("component_connections.json"), mode="r", encoding="utf-8") as file:
        connections_list = json.load(file)

    # Automatic connections are created with an index, we check the first three indexes here, which should suffice to
    # find the component.
    pv_con_dicts = [
        {
            "From": {"Component": "PVSystem", "Field": "ElectricityOutput"},
            "To": {"Component": "ElectricityMeter", "Field": f"Input_PVSystem_ElectricityOutput_{i}"},
        }
        for i in range(3)
    ]
    assert any(any(connection == pv_con_dict for connection in connections_list) for pv_con_dict in pv_con_dicts)

    remove_results_directory(result_directory)
