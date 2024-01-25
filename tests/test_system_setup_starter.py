"""Test system setup starter."""

import os
import shutil
import time
from pathlib import Path

import pytest

from hisim.hisim_main import main
from hisim.system_setup_starter import make_system_setup


@pytest.mark.utsp
def test_system_setup_starter():
    """Run a simulation from JSON."""

    parameters_json = {
        "path_to_module": "../system_setups/household_1_advanced_hp_diesel_car.py",
        "simulation_parameters": {
            "start_date": "2021-01-01T00:00:00",
            "end_date": "2021-01-02T00:00:00",
            "seconds_per_timestep": 900,
            "post_processing_options": [18, 19, 20, 22],
        },
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
            "enable_opening_windows": False,
        },
        "system_setup_config": {
            # "some_subconf": {
            #     "some_attribute": "some_building"
            # },  # Raises AttributeError
            "building_type": "some_building",
            "hp_config": {"cost": 1000000},
        },
    }

    result_directory = "test_system_setup_starter_results"
    if Path(result_directory).is_dir():
        shutil.rmtree(result_directory)
    Path(result_directory).mkdir(parents=True)
    (
        path_to_module,
        simulation_parameters,
        module_config_path,
    ) = make_system_setup(
        parameters_json=parameters_json,
        result_directory=result_directory,
    )
    main(
        path_to_module,
        simulation_parameters,
        module_config_path,
    )
    # Check results
    assert os.path.isfile(result_directory + "/finished.flag")
    assert os.path.isfile(result_directory + "/results_for_webtool.json")

    assert (
        simulation_parameters.seconds_per_timestep  # type: ignore
        == parameters_json["simulation_parameters"]["seconds_per_timestep"]  # type: ignore
    )

    # Check if the costs of the heat pump have been adapted.
    # TODO: Rewrite to the new data path
    # with open(result_directory + "/results_for_webtool.json", "r", encoding="utf8") as file:
    #     webtool_kpis = json.load(file)
    # assert webtool_kpis["capexDict"]["column 1"]["HeatPumpHPLib [Investment in EUR] "] == 273.97
    # Remove result directory
    time.sleep(1)
    shutil.rmtree(result_directory)


@pytest.mark.utsp
def test_system_setup_starter_scaling():
    """Run a simulation from JSON."""

    parameters_json = {
        "path_to_module": "../system_setups/household_1_advanced_hp_diesel_car.py",
        "simulation_parameters": {
            "start_date": "2021-01-01T00:00:00",
            "end_date": "2021-01-02T00:00:00",
            "seconds_per_timestep": 900,
            "post_processing_options": [18, 19, 20, 22],
        },
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
            "enable_opening_windows": False,
        },
    }

    result_directory = "test_system_setup_starter_scaling_results"
    if Path(result_directory).is_dir():
        shutil.rmtree(result_directory)
    Path(result_directory).mkdir(parents=True)
    (
        path_to_module,
        simulation_parameters,
        module_config_path,
    ) = make_system_setup(
        parameters_json=parameters_json,
        result_directory=result_directory,
    )
    main(
        path_to_module,
        simulation_parameters,
        module_config_path,
    )
    # Check results
    assert os.path.isfile(result_directory + "/finished.flag")
    assert os.path.isfile(result_directory + "/results_for_webtool.json")

    assert (
        simulation_parameters.seconds_per_timestep  # type: ignore
        == parameters_json["simulation_parameters"]["seconds_per_timestep"]  # type: ignore
    )

    # TODO: Rewrite to the new data path.
    # with open(result_directory + "/results_for_webtool.json", "r", encoding="utf8") as file:
    #     webtool_kpis = json.load(file)
    # assert webtool_kpis["capexDict"]["column 1"]["HeatPumpHPLib [Investment in EUR] "] == 3.32

    # Remove result directory
    time.sleep(1)
    shutil.rmtree(result_directory)
