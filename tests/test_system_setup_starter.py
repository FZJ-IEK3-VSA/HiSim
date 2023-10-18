import os
import shutil
from hisim.system_setup_starter import make_system_setup
from hisim.hisim_main import main
from pathlib import Path
import pytest
import time

parameters_json = {
    "path_to_module": "examples/household_1_advanced_hp_diesel_car.py",
    "function_in_module": "household_1_advanced_hp_diesel_car",
    "config_class_name": "HouseholdAdvancedHPDieselCarConfig",
    "simulation_parameters": {
        "start_date": "2021-01-01T00:00:00",
        "end_date": "2021-01-02T00:00:00",
        "seconds_per_timestep": 60,
        "post_processing_options": [13, 19, 20, 22],
    },
    "system_setup_config": {
        # "some_subconf": {"some_attribute": "some_building"},  # Raises AttributeError
        "building_type": "some_building",
        "hp_config": {
            "set_thermal_output_power_in_watt": 12000,
        },
    },
}


@pytest.mark.base
def test_system_setup_starter():
    # Run simulation from config_json
    result_directory = "results"
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
        result_path=result_directory,
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
    # Remove result directory
    time.sleep(1)
    shutil.rmtree(result_directory)
