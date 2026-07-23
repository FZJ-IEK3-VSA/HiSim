""" Tests for the household with gas heater. """
from __future__ import annotations

from typing import Any
import os
import shutil
from pathlib import Path
import json
import pytest

from hisim import utils, hisim_main, log
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.system_setup_starter import make_system_setup
from hisim.hisim_main import main
from tests.testing_utils import TestingUtils

MY_PATH_TO_MODULE: str = "../system_setups/household_gas_heater.py"
MY_SIMULATION_PARAMETERS: dict[str, Any] = {
    "start_date": "2021-01-01T00:00:00",
    "end_date": "2021-01-02T00:00:00",
    "seconds_per_timestep": 900,
    "post_processing_options": [9, 18, 19, 20, 22],
    "log_connections": True,
}


def create_results_directory(result_directory: str | Path) -> None:
    """Create a clean result directory.

    If the directory already exists it is recursively removed first, then
    (re)created along with any missing parents.

    Args:
        result_directory: Path to the directory to (re)create.
    """
    if Path(result_directory).is_dir():
        shutil.rmtree(result_directory)
    Path(result_directory).mkdir(parents=True, exist_ok=True)


def run_system(config_json: dict[str, Any], result_directory: str | Path) -> None:
    """Build and execute a system setup from a config dict.

    Uses `make_system_setup` to materialise the module path, simulation
    parameters, and module config from `config_json`, then invokes
    `hisim_main.main` to run the simulation, writing outputs into
    `result_directory`.

    Args:
        config_json: Configuration mapping with at least `path_to_module`
            and `simulation_parameters` keys.
        result_directory: Directory where simulation results are written.
    """
    (
        path_to_module,
        simulation_parameters,
        module_config_path,
    ) = make_system_setup(
        parameters_json=config_json,
        result_directory=str(result_directory),
    )
    main(
        path_to_module,
        simulation_parameters,
        module_config_path,
    )


def remove_results_directory(result_directory: str | Path) -> None:
    """Recursively remove the result directory.

    Args:
        result_directory: Path to the directory to delete.
    """
    shutil.rmtree(result_directory)


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_gas_heater_main() -> None:
    """Execute setup with default values with hisim main."""

    path = "../system_setups/household_gas_heater.py"
    simulation_parameters = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
    simulation_parameters.post_processing_options.append(PostProcessingOptions.MAKE_NETWORK_CHARTS)

    # Route results into a clean, test-scoped directory (mirroring the sibling tests below)
    # so the run's completion can be verified instead of only checking that nothing raised.
    result_directory = TestingUtils.get_result_directory()
    create_results_directory(result_directory)
    simulation_parameters.result_directory = result_directory

    hisim_main.main(path, simulation_parameters)
    log.information(os.getcwd())

    # Check if calculation has finished without errors.
    assert Path(result_directory).joinpath("finished.flag").is_file()

    remove_results_directory(result_directory)


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_gas_heater_system_setup_starter_default() -> None:
    """Execute setup with hisim system setup starter."""
    my_config_json = {"path_to_module": MY_PATH_TO_MODULE, "simulation_parameters": MY_SIMULATION_PARAMETERS}

    result_directory = TestingUtils.get_result_directory()
    create_results_directory(result_directory)
    run_system(my_config_json, result_directory)

    #  Check if calculation has finished without errors.
    assert Path(result_directory).joinpath("finished.flag").is_file()

    remove_results_directory(result_directory)


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_gas_heater_system_setup_starter_pv() -> None:
    """Run the gas-heater household via the system setup starter with PV enabled and verify PV wiring.

    Builds the household with the ``photovoltaic`` option enabled, executes the
    simulation, and asserts that a ``PVSystem``→``ElectricityMeter`` connection
    is present in the produced ``component_connections.json``.
    """
    my_config_json = {
        "path_to_module": MY_PATH_TO_MODULE,
        "simulation_parameters": MY_SIMULATION_PARAMETERS,
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
        {"From": {"Component": "PVSystem", "Field": "ElectricityOutput"}, "To":
            {"Component": "ElectricityMeter", "Field": f"Input_PVSystem_ElectricityOutput_{i}"}}
        for i in range(3)
    ]
    assert any(any(connection == pv_con_dict for connection in connections_list) for pv_con_dict in pv_con_dicts)

    remove_results_directory(result_directory)
