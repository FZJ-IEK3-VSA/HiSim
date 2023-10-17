import os
import shutil
from hisim.system_setup_starter import make_system_setup
from hisim.hisim_main import main
from pathlib import Path

parameters_json = {
    "path_to_module": "examples/household_1_advanced_hp_diesel_car.py",
    "function_in_module": "household_1_advanced_hp_diesel_car",
    "config_class_name": "HouseholdAdvancedHPDieselCarConfig",
    "simulation_parameters": {
        "start_date": "2021-01-01T00:00:00",
        "end_date": "2021-01-02T00:00:00",
        "seconds_per_timestep": 60,
        "post_processing_options": [13, 19, 20, 22],
        "result_directory": "tests/test_system_setup_starter/result",
    },
    "system_setup_config": {
        # "some_subconf": {"some_attribute": "some_building"},  # Raises AttributeError
        "building_type": "some_building",
        "hp_config": {
            "set_thermal_output_power_in_watt": 12000,
        },
    },
}

# parameters_json_modular_example = {
#     "path_to_module": "examples/modular_example.py",
#     "function_in_module": "modular_household_explicit",
#     "config_class_name": "ModularHouseholdConfig",
#     "system_setup_config": {
#         "system_config_": {
#             "pv_included": False,
#             "pv_peak_power": 10000.0,
#             "smart_devices_included": False,
#             "buffer_included": False,
#             "buffer_volume": 1.0,
#             "battery_included": False,
#             "battery_capacity": 10.0,
#             "heatpump_included": False,
#             "heatpump_power": 1.0,
#             "chp_included": False,
#             "chp_power": 12,
#             "h2_storage_size": 100,
#             "electrolyzer_power": 5000.0,
#             "ev_included": True,
#             "charging_station": {
#                 "Name": "Charging At Home with 03.7 kW",
#                 "Guid": {"StrVal": "38e3a15d-d6f5-4f51-a16a-da287d14608f"},
#             },
#         },
#         "archetype_config_": {
#             "occupancy_profile_utsp": None,
#             "occupancy_profile": "AVG",
#             "building_code": "ES.ME.TH.03.Gen.ReEx.001.003",
#             "absolute_conditioned_floor_area": None,
#             "water_heating_system_installed": "DistrictHeating",
#             "heating_system_installed": "DistrictHeating",
#             "mobility_set": None,
#             "mobility_distance": None,
#             "url": "http://134.94.131.167:443/api/v1/profilerequest",
#             "api_key": "limited_OXT60O84N9ITLO1CM9CJ1V393QFKOKCN",
#         },
#     },
# }


def test_system_setup_starter():
    # Run simulation from config_json
    result_directory: Path = "tests/test_system_setup_starter/result"
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
    assert os.path.isfile(str(result_directory) + "/finished.flag")
    assert os.path.isfile(str(result_directory) + "/webtool_kpis.json")
    # Remove result directory
    shutil.rmtree(result_directory)
