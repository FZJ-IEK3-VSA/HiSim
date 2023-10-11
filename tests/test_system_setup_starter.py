import os
import shutil
from hisim.system_setup_starter import make_and_execute_system_setup

config_json = {
    "path_to_module": "../examples/household_1_advanced_hp_diesel_car.py",
    "function_in_module": "household_1_advanced_hp_diesel_car",
    "cost_parameters": {"electricity_price": 0.2, "gas_price": 0.1},
    "building_type": "some_building",
    "number_of_people": 4,
    "heat_pump_power": 12300,
}


def test_system_setup_starter():
    # Run simulation from config_json
    result_directory = "test_system_setup_starter/result"
    make_and_execute_system_setup(
        parameters_json=config_json, result_directory=result_directory
    )
    # Check results
    assert os.path.isfile(result_directory + "/finished.flag")
    assert os.path.isfile(result_directory + "/webtool_kpis.json")
    # Remove result directory
    shutil.rmtree(result_directory)
