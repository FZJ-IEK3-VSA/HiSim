""" Tests for the household with gas heater. """
import json
import os
import subprocess
import shutil
from pathlib import Path
import pytest

from hisim import utils, hisim_main, log
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.system_setup_starter import  make_system_setup
from hisim.hisim_main import main

my_path_to_module = "../system_setups/household_gas_heater.py"
my_simulation_parameters = {
    "start_date": "2021-01-01T00:00:00",
    "end_date": "2021-01-02T00:00:00",
    "seconds_per_timestep": 900,
    "post_processing_options": [18, 19, 20, 22],
}
my_result_directory = "test_system_setups/results/test_household_gas_heater_with_system_setup_starter"


def create_results_directory(result_directory):
    if Path(result_directory).is_dir():
        shutil.rmtree(result_directory)
    Path(result_directory).mkdir(parents=True, exist_ok=True)


def run_system(config_json, result_directory):
    # subprocess.check_output(["python", "../hisim/system_setup_starter.py", config_json, result_directory])
    (path_to_module, simulation_parameters, module_config_path,) = make_system_setup(
        parameters_json=config_json,
        result_directory=result_directory,
    )
    main(
        path_to_module,
        simulation_parameters,
        module_config_path,
    )


def remove_results_directory(result_directory):
    shutil.rmtree(result_directory)


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_gas_heater_main():
    """Execute setup with default values with hisim main."""

    path = "../system_setups/household_gas_heater.py"
    my_simpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
    my_simpar.post_processing_options.append(PostProcessingOptions.MAKE_NETWORK_CHARTS)
    hisim_main.main(path, my_simpar)
    log.information(os.getcwd())


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_gas_heater_system_setup_starter_default():
    """Execute setup with hisim system setup starter."""
    my_config_json = {"path_to_module": my_path_to_module, "simulation_parameters": my_simulation_parameters}

    create_results_directory(my_result_directory)
    run_system(my_config_json, my_result_directory)
    assert Path(my_result_directory).joinpath("finished.flag").is_file()
    remove_results_directory(my_result_directory)


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_gas_heater_system_setup_starter_pv():
    """Execute setup with hisim system setup starter."""
    my_config_json = {
        "path_to_module": my_path_to_module,
        "simulation_parameters": my_simulation_parameters,
        "building_config": {
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
            "enable_opening_windows": False
        },
        "options": {"photovoltaic": True},
    }

    create_results_directory(my_result_directory)
    run_system(my_config_json, my_result_directory)
    assert Path(my_result_directory).joinpath("finished.flag").is_file()
    remove_results_directory(my_result_directory)
