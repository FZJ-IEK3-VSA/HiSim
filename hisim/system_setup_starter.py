"""
Build and simulate a system setup for a specific example that is defined in a JSON file.
Result files are stored in `/results`.
See `tests/test_system_setup_starter.py` for an example.

Run `hisim/system_setup_starter.py <json-file>` to start a simulation.
Required fields in the JSON file are: `path_to_module`, `function_in_module`, `config_class_name`
and `simulation_parameters`.
SimulationParameters from the examples is not used. Instead the parameters from the JSON are set.

Optional field: `system_setup_config`.
The values from `system_setup_config` replace single values of the example's configuration object.
"""

import json
import importlib
import datetime
from pathlib import Path
from typing import Any, Union, Tuple, Optional
from copy import deepcopy

from hisim import log
from hisim.hisim_main import main
from hisim.utils import rsetattr, rhasattr
from hisim.simulator import SimulationParameters

# Examples need to use `create_configuration()` and their config class needs to implement
# `get_default()` to run with the system setup starter.
SUPPORTED_MODULES = [
    "examples.modular_example",
    "examples.household_1_advanced_hp_diesel_car",
]


def read_parameters_json(parameters_json_path: str) -> Union[dict, list]:
    """Read the parameter JSON string from file."""
    with open(parameters_json_path, "r", encoding="utf8") as c:
        parameters_json: Union[dict, list] = json.load(c)
    return parameters_json


def set_values(setup_config, parameter_dict, nested=None):
    """Set values in config Dataclass from the parameter dictionnary."""
    for key, value in parameter_dict.items():
        if nested:
            path_list = nested + [key]
        else:
            path_list = [key]
        if isinstance(value, dict):
            set_values(setup_config, value, path_list)
        else:
            attribute = ".".join(path_list)
            if rhasattr(setup_config, attribute):
                rsetattr(setup_config, attribute, value)
            else:
                raise AttributeError(
                    f"""Attribute `{attribute}` from JSON cannot be found
                    in `{setup_config.__class__.__name__}`."""
                )


def make_system_setup(
    parameters_json: Union[dict, list], result_directory: str
) -> Tuple[str, str, Optional[SimulationParameters], str]:
    """
    Read setup parameters from JSON and build a system setup for a specific example.
    The setup is simulated and result files are stored in `result_directory`.
    """
    if isinstance(parameters_json, list):
        raise NotImplementedError(
            "System Setup Starter can only handle one setup at a time for now."
        )

    _parameters_json = deepcopy(parameters_json)
    Path(result_directory).mkdir(parents=True, exist_ok=True)
    path_to_module = _parameters_json.pop("path_to_module")
    setup_module_name = "examples." + path_to_module.split("/")[-1].replace(".py", "")
    if setup_module_name not in SUPPORTED_MODULES:
        raise NotImplementedError(
            f"System setup starter can only be used with one of {', '.join(SUPPORTED_MODULES)}"
        )
    config_class_name = _parameters_json.pop("config_class_name")
    function_in_module = _parameters_json.pop("function_in_module")
    simulation_parameters_dict = _parameters_json.pop("simulation_parameters")
    module_config_path = str(Path(result_directory).joinpath("module_config.json"))
    simulation_parameters_path = str(
        Path(result_directory).joinpath("simulation_parameters.json")
    )
    setup_config_dict = _parameters_json.pop("system_setup_config")
    if _parameters_json:
        raise AttributeError("There are unused attributes in parameters JSON.")

    # Import modules for the specified system setup
    setup_module = importlib.import_module(setup_module_name)
    config_class = getattr(setup_module, config_class_name)

    setup_config: Any = config_class.get_default()
    set_values(setup_config, setup_config_dict)

    # Set custom simulation parameters
    simulation_parameters = SimulationParameters(
        start_date=datetime.datetime.fromisoformat(
            simulation_parameters_dict.pop("start_date")
        ),
        end_date=datetime.datetime.fromisoformat(
            simulation_parameters_dict.pop("end_date")
        ),
        seconds_per_timestep=simulation_parameters_dict.pop("seconds_per_timestep"),
        result_directory=result_directory,
    )
    set_values(simulation_parameters, simulation_parameters_dict)

    with open(simulation_parameters_path, "w", encoding="utf8") as out_file:
        out_file.write(simulation_parameters.to_json())  # ignore: type

    with open(module_config_path, "w", encoding="utf8") as out_file:
        out_file.write(setup_config.to_json())  # ignore: type

    return (
        path_to_module,
        function_in_module,
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

    my_parameters_json = read_parameters_json(PARAMETERS_JSON_FILE)

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
