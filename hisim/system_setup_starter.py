"""
Read setup parameters from a JSON file and build a system setup for a specific example that is
defined in the JSON file. The setup is simulated and result files are stored in `/results`.

Run `hisim/hisim_from_json.py <json-file>` to start a simulation or run `hisim/hisim_from_json.py`
to read the JSON from `input/request.json`.
"""

import importlib
from typing import Union
import json
from hisim import log
from hisim.simulator import SimulationParameters
from hisim.hisim_main import main
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.utils import rgetattr, rsetattr
from pathlib import Path


def read_parameters_json(parameters_json_path) -> Union[dict, list]:
    with open(parameters_json_path, "r") as c:
        parameters_json: Union[dict, list] = json.load(c)
    return parameters_json


def make_system_setup(
    parameters_json: Union[dict, list],
    result_directory: str = "result",
) -> None:
    """
    Read setup parameters from JSON and build a system setup for a specific example.
    The setup is simulated and result files are stored in `result_directory`.
    """
    if isinstance(parameters_json, list):
        raise NotImplementedError(
            "System Setup Starter can only handle one setup at a time for now."
        )

    result_directory: Path = Path(result_directory)
    module_config_path = result_directory.joinpath("module_config.json")
    path_to_module = parameters_json.pop("path_to_module")
    setup_module_name = path_to_module.replace(".py", "").replace("/", ".")
    config_class_name = parameters_json.pop("config_class_name")
    function_in_module = parameters_json.pop("function_in_module")

    # Import modules for the specified system setup
    setup_module = importlib.import_module(setup_module_name)
    config_class = getattr(setup_module, config_class_name)

    setup_config: config_class = config_class.get_default()

    def set_values(setup_config, parameter_dict, nested=[]):
        for key, value in parameter_dict.items():
            if isinstance(value, dict):
                set_values(setup_config, value, nested + [key])
            else:
                attribute = ".".join(nested + [key])
                if rgetattr(setup_config, attribute, False):
                    rsetattr(setup_config, attribute, value)
                else:
                    raise AttributeError(
                        f"Attribute `{attribute}` from JSON cannot be found in `{setup_config.__class__.__name__}`."
                    )

    set_values(setup_config, parameters_json)

    # Save to file
    with open(module_config_path, "w") as out_file:
        out_file.write(setup_config.to_json())

    # Set simulation parameters
    simulation_parameters = set_simulation_parameters(result_directory)

    return (
        str(path_to_module),
        function_in_module,
        simulation_parameters,
        str(module_config_path),
    )


def set_simulation_parameters(result_directory):
    simulation_parameters = SimulationParameters.one_day_only(
        year=2021,
        seconds_per_timestep=60,
    )
    simulation_parameters.result_directory = str(result_directory)
    simulation_parameters.post_processing_options.append(
        PostProcessingOptions.COMPUTE_AND_WRITE_KPIS_TO_REPORT
    )
    simulation_parameters.post_processing_options.append(
        PostProcessingOptions.COMPUTE_CAPEX
    )
    simulation_parameters.post_processing_options.append(
        PostProcessingOptions.COMPUTE_OPEX
    )
    simulation_parameters.post_processing_options.append(
        PostProcessingOptions.MAKE_RESULT_JSON_WITH_KPI_FOR_WEBTOOL
    )
    return simulation_parameters


if __name__ == "__main__":
    import sys

    if len(sys.argv) == 1:
        parameters_json_path = Path("input/request.json")
        if not parameters_json_path.is_file():
            log.information(
                "Please specify an input JSON file or place it in `input/request.json`."
            )
            sys.exit(1)
    elif len(sys.argv) == 2:
        parameters_json_path = sys.argv[1]
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
    ) = make_system_setup(parameters_json=parameters_json)

    main(
        path_to_module,
        function_in_module,
        simulation_parameters,
        module_config_path,
    )
