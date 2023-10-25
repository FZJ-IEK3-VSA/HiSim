"""Test system setup starter."""

import os
import time
import shutil
from pathlib import Path
import json
import pytest

from hisim.system_setup_starter import make_system_setup
from hisim.hisim_main import main


@pytest.mark.base
def test_system_setup_starter():
    """Run a simulation from JSON."""

    parameters_json = {
        "path_to_module": "../examples/household_1_advanced_hp_diesel_car.py",
        "function_in_module": "household_1_advanced_hp_diesel_car",
        "simulation_parameters": {
            "start_date": "2021-01-01T00:00:00",
            "end_date": "2021-01-02T00:00:00",
            "seconds_per_timestep": 900,
            "post_processing_options": [13, 19, 20, 22],
        },
        "building_config": {
            "name": "Building",
            "building_code": "DE.N.SFH.05.Gen.ReEx.001.002",
            "building_heat_capacity_class": "medium",
            "initial_internal_temperature_in_celsius": 23,
            "heating_reference_temperature_in_celsius": -14,
            "absolute_conditioned_floor_area_in_m2": 121.2,
            "total_base_area_in_m2": None,
            "number_of_apartments": 1,
            "predictive": False,
        },
        "system_setup_config": {
            # "some_subconf": {
            #     "some_attribute": "some_building"
            # },  # Raises AttributeError
            "building_type": "some_building",
            "hp_config": {"set_thermal_output_power_in_watt": 12000, "cost": 1000000},
        },
    }

    result_directory = "results1"
    if Path(result_directory).is_dir():
        shutil.rmtree(result_directory)
    Path(result_directory).mkdir(parents=True)
    (
        path_to_module,
        function_in_module,
        simulation_parameters,
        module_config_path,
    ) = make_system_setup(
        parameters_json=parameters_json,
        result_directory=result_directory,
    )
    main(
        path_to_module,
        function_in_module,
        simulation_parameters,
        module_config_path,
    )
    # Check results
    assert os.path.isfile(result_directory + "/finished.flag")
    assert os.path.isfile(result_directory + "/webtool_kpis.json")

    with open(module_config_path, "r", encoding="utf8") as f:
        created_module_config = json.load(f)
    assert (
        created_module_config["setup_config"]["hp_config"][
            "set_thermal_output_power_in_watt"
        ]
        == parameters_json["system_setup_config"]["hp_config"][  # type: ignore
            "set_thermal_output_power_in_watt"
        ]
    )
    assert (
        simulation_parameters.seconds_per_timestep  # type: ignore
        == parameters_json["simulation_parameters"][
            "seconds_per_timestep"
        ]  # type: ignore
    )
    # Remove result directory
    time.sleep(1)
    shutil.rmtree(result_directory)


@pytest.mark.base
def test_system_setup_starter_scaling():
    """Run a simulation from JSON."""

    parameters_json = {
        "path_to_module": "../examples/household_1_advanced_hp_diesel_car.py",
        "function_in_module": "household_1_advanced_hp_diesel_car",
        "simulation_parameters": {
            "start_date": "2021-01-01T00:00:00",
            "end_date": "2021-01-02T00:00:00",
            "seconds_per_timestep": 900,
            "post_processing_options": [13, 19, 20, 22],
        },
        "building_config": {
            "name": "Building",
            "building_code": "DE.N.SFH.05.Gen.ReEx.001.002",
            "building_heat_capacity_class": "medium",
            "initial_internal_temperature_in_celsius": 23,
            "heating_reference_temperature_in_celsius": -14,
            "absolute_conditioned_floor_area_in_m2": 121.2,
            "total_base_area_in_m2": None,
            "number_of_apartments": 1,
            "predictive": False,
        },
    }

    result_directory = "results2"
    if Path(result_directory).is_dir():
        shutil.rmtree(result_directory)
    Path(result_directory).mkdir(parents=True)
    (
        path_to_module,
        function_in_module,
        simulation_parameters,
        module_config_path,
    ) = make_system_setup(
        parameters_json=parameters_json,
        result_directory=result_directory,
    )
    main(
        path_to_module,
        function_in_module,
        simulation_parameters,
        module_config_path,
    )
    # Check results
    assert os.path.isfile(result_directory + "/finished.flag")
    assert os.path.isfile(result_directory + "/webtool_kpis.json")

    assert (
        simulation_parameters.seconds_per_timestep  # type: ignore
        == parameters_json["simulation_parameters"][
            "seconds_per_timestep"
        ]  # type: ignore
    )
    # Remove result directory
    time.sleep(1)
    shutil.rmtree(result_directory)
