"""Build and simulate a system setup for a specific system setup that is defined in a JSON file.

Result files are stored in `/results`.
See `tests/test_system_setup_starter.py` for an system setup.

Run `hisim/system_setup_starter.py <json-file>` to start a simulation.
Required fields in the JSON file are: `path_to_module`, `function_in_module` and
`simulation_parameters`. SimulationParameters from the system_setups is not used. Instead the
parameters from the JSON are set.

Optional field: `building_config`
The values from `building_config` replace single values of the system setup's building object. When
present, it is used to scale the default configuration with `get_scaled_default()`.

Optional field: `system_setup_config`
The values from `system_setup_config` overwrite specific values of the configuration object.
Arguments that are not present keep the (scaled) default value.
"""
# clean

import json
import datetime
from pathlib import Path
from typing import Union, Tuple, Optional
from copy import deepcopy

from hisim import log
from hisim.hisim_main import main
from hisim.utils import set_attributes_of_dataclass_from_dict
from hisim.simulator import SimulationParameters

# System setups need to use `create_configuration()` and their config class needs to implement
# `get_default()` to run with the system setup starter.
SUPPORTED_MODULES = [
    "system_setups.modular_example",
    "system_setups.household_1_advanced_hp_diesel_car",
]


def make_system_setup(
    parameters_json: Union[dict, list], result_directory: str
) -> Tuple[str, Optional[SimulationParameters], str]: # str
    """Read setup parameters from JSON and build a system setup for a specific system setup.

    The setup is simulated and result files are stored in `result_directory`.
    """
    if isinstance(parameters_json, list):
        raise NotImplementedError(
            "System Setup Starter can only handle one setup at a time for now."
        )

    _parameters_json = deepcopy(parameters_json)
    Path(result_directory).mkdir(parents=True, exist_ok=True)  # pylint: disable=unexpected-keyword-arg
    path_to_module = _parameters_json.pop("path_to_module")
    setup_module_name = "system_setups." + path_to_module.split("/")[-1].replace(".py", "")
    if setup_module_name not in SUPPORTED_MODULES:
        raise NotImplementedError(
            f"System setup starter can only be used with one of {', '.join(SUPPORTED_MODULES)}"
        )
    # function_in_module = _parameters_json.pop("function_in_module")
    simulation_parameters_dict = _parameters_json.pop("simulation_parameters")
    module_config_path = str(Path(result_directory).joinpath("module_config.json"))
    simulation_parameters_path = str(Path(result_directory).joinpath("simulation_parameters.json"))
    building_config = _parameters_json.pop("building_config", {})
    system_setup_config = _parameters_json.pop("system_setup_config", {})
    module_config_dict = {"building_config": building_config, "system_setup_config": system_setup_config}

    if _parameters_json:
        raise AttributeError(
            f"There are unused attributes ({_parameters_json.keys()}) in parameters JSON."
        )

    # Set custom simulation parameters
    simulation_parameters = SimulationParameters(
        start_date=datetime.datetime.fromisoformat(simulation_parameters_dict.pop("start_date")),
        end_date=datetime.datetime.fromisoformat(simulation_parameters_dict.pop("end_date")),
        seconds_per_timestep=simulation_parameters_dict.pop("seconds_per_timestep"),
        result_directory=result_directory,
    )
    set_attributes_of_dataclass_from_dict(simulation_parameters, simulation_parameters_dict)

    with open(simulation_parameters_path, "w", encoding="utf8") as out_file:
        out_file.write(simulation_parameters.to_json())  # ignore: type

    with open(module_config_path, "w", encoding="utf8") as out_file:
        out_file.write(json.dumps(module_config_dict))  # ignore: type

    return (
        path_to_module,
        # function_in_module,
        simulation_parameters,
        module_config_path,
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) == 1:
        PARAMETERS_JSON_FILE = "input/request.json"
        RESULT_DIRECTORY = "results"
        if not Path(PARAMETERS_JSON_FILE).is_file():
            log.information(
                "Please specify an input JSON file or place it in `input/request.json`."
            )
            sys.exit(1)
    elif len(sys.argv) == 2:
        PARAMETERS_JSON_FILE = sys.argv[1]
        RESULT_DIRECTORY = "results"
    elif len(sys.argv) == 3:
        PARAMETERS_JSON_FILE = sys.argv[1]
        RESULT_DIRECTORY = sys.argv[2]
    else:
        log.information("HiSim from JSON received too many arguments.")
        sys.exit(1)

    log.information(f"Reading parameters from {PARAMETERS_JSON_FILE}.")
    with open(PARAMETERS_JSON_FILE, "r", encoding="utf8") as file:
        my_parameters_json: Union[dict, list] = json.load(file)

    (
        my_path_to_module,
        my_function_in_module,
        my_simulation_parameters,
        my_module_config_path,
    ) = make_system_setup(
        parameters_json=my_parameters_json, result_directory=RESULT_DIRECTORY
    )

    main(
        my_path_to_module,
        my_function_in_module,
        my_simulation_parameters,
        my_module_config_path,
    )
