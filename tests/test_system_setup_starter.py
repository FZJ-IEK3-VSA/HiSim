import os
import shutil
from hisim.system_setup_starter import make_system_setup
from hisim.hisim_main import main
from pathlib import Path

parameters_json = {
    "path_to_module": "examples/household_1_advanced_hp_diesel_car.py",
    "function_in_module": "household_1_advanced_hp_diesel_car",
    "config_class_name": "HouseholdAdvancedHPDieselCarConfig",
    "building_type": "some_building",
    # "some_subconf": {"some_attribute": "some_building"},  # Raises AttributeError
    "hp_config": {
        "set_thermal_output_power_in_watt": 12000,
    },
}


def test_system_setup_starter():
    # Run simulation from config_json
    result_directory: Path = Path("test_system_setup_starter/result")
    if result_directory.is_dir():
        shutil.rmtree(result_directory)
    result_directory.mkdir(parents=True)

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
    assert os.path.isfile(str(result_directory) + "/finished.flag")
    assert os.path.isfile(str(result_directory) + "/webtool_kpis.json")
    # Remove result directory
    shutil.rmtree(result_directory)
