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

import importlib
from typing import Any, Union, Tuple, Optional
import json
from hisim import log
from hisim.simulator import SimulationParameters
from hisim.hisim_main import main
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.utils import rgetattr, rsetattr, rhasattr
from pathlib import Path
import datetime


def read_parameters_json(parameters_json_path: str) -> Union[dict, list]:
    with open(parameters_json_path, "r") as c:
        parameters_json: Union[dict, list] = json.load(c)
    return parameters_json


def set_values(setup_config, parameter_dict, nested=[]):
    for key, value in parameter_dict.items():
        if isinstance(value, dict):
            set_values(setup_config, value, nested + [key])
        else:
            attribute = ".".join(nested + [key])
            if rhasattr(setup_config, attribute):
                rsetattr(setup_config, attribute, value)
            else:
                raise AttributeError(
                    f"""Attribute `{attribute}` from JSON cannot be found
                    in `{setup_config.__class__.__name__}`."""
                )


def make_system_setup(
    parameters_json: Union[dict, list],
    result_path: str
) -> Tuple[str, str, Optional[SimulationParameters], str]:
    """
    Read setup parameters from JSON and build a system setup for a specific example.
    The setup is simulated and result files are stored in `result_directory`.
    """
    if isinstance(parameters_json, list):
        raise NotImplementedError(
            "System Setup Starter can only handle one setup at a time for now."
        )

    result_directory = result_path

    path_to_module = parameters_json.pop("path_to_module")
    setup_module_name = "examples." + path_to_module.split("/")[-1].replace(".py", "")
    config_class_name = parameters_json.pop("config_class_name")
    function_in_module = parameters_json.pop("function_in_module")
    simulation_parameters_dict = parameters_json.pop("simulation_parameters")
    module_config_path = str(Path(result_directory).joinpath("module_config.json"))
    simulation_parameters_path = str(
        Path(result_directory).joinpath("simulation_parameters.json")
    )

    setup_config_dict = parameters_json.pop("system_setup_config")
    if parameters_json:
        raise AttributeError("There are unused attributes in parameters JSON.")

    # Import modules for the specified system setup
    setup_module = importlib.import_module(setup_module_name)
    config_class = getattr(setup_module, config_class_name)

    setup_config: Any = config_class.get_default()
    set_values(setup_config, setup_config_dict)

    # Save to file
    with open(module_config_path, "w", encoding="utf8") as out_file:
        out_file.write(setup_config.to_json())  # ignore: type

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
        parameters_json_path = "input/request.json"
        result_path = "results"
        if not Path(parameters_json_path).is_file():
            log.information(
                "Please specify an input JSON file or place it in `input/request.json`."
            )
            sys.exit(1)
    elif len(sys.argv) == 2:
        parameters_json_path = sys.argv[1]
        result_path = "results"
    elif len(sys.argv) == 3:
        parameters_json_path = sys.argv[1]
        result_path = sys.argv[2]
    else:
        log.information("HiSim from JSON received too many arguments.")
        sys.exit(1)

    log.information(f"Reading parameters from {parameters_json_path}.")

    parameters_json = read_parameters_json(parameters_json_path)

    (
        path_to_module,
        function_in_module,
        simulation_parameters,
        module_config_path,
    ) = make_system_setup(parameters_json=parameters_json, result_path=result_path)

    main(
        path_to_module,
        function_in_module,
        simulation_parameters,
        module_config_path,
    )
