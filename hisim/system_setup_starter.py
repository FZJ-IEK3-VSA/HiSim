"""Build and simulate a system setup for a specific system setup that is defined in a JSON file.

Result files are stored in `/results`.
See `tests/test_system_setup_starter.py` for a system setup.

Run `hisim/system_setup_starter.py <json-file>` to start a simulation.
Required fields in the JSON file are: `path_to_module`, `function_in_module` and
`simulation_parameters`. SimulationParameters from the system_setups is not used. Instead, the
parameters from the JSON are set.

Optional field: `building_config`
The values from `building_config` replace single values of the system setup's building object. When
present, it is used to scale the default configuration with `get_scaled_default()`.

Optional field: `system_setup_config`
The values from `system_setup_config` overwrite specific values of the configuration object.
Arguments that are not present keep the (scaled) default value.
"""

from __future__ import annotations

import json
import datetime
from pathlib import Path
from typing import Any
from copy import deepcopy

from hisim import log
from hisim.hisim_main import main
from hisim.utils import set_attributes_of_dataclass_from_dict
from hisim.simulator import SimulationParameters


def make_system_setup(
    parameters_json: dict[str, Any] | list[Any], result_directory: str
) -> tuple[str, SimulationParameters, str]:
    """Read setup parameters from JSON and build a system setup.

    Parses the JSON configuration, creates a SimulationParameters object,
    and returns the module path and config paths needed to run the simulation.
    The actual simulation is performed by `hisim.hisim_main.main()`.

    Args:
        parameters_json: Configuration dict (or list, currently unsupported) containing
            path_to_module, simulation_parameters, and optional building/system_setup configs.
        result_directory: Directory path where result files and config JSONs will be written.

    Returns:
        A tuple of (path_to_module, simulation_parameters, module_config_path) where:
            - path_to_module: Module path string for the setup function to load.
            - simulation_parameters: Configured SimulationParameters object.
            - module_config_path: Path to the written module_config.json file.

    Raises:
        NotImplementedError: If parameters_json is a list (multi-setup not yet supported).
        AttributeError: If parameters_json contains unrecognized top-level keys.
    """
    if isinstance(parameters_json, list):
        raise NotImplementedError("System Setup Starter can only handle one setup at a time for now.")

    _parameters_json = deepcopy(parameters_json)
    Path(result_directory).mkdir(parents=True, exist_ok=True)  # pylint: disable=unexpected-keyword-arg
    path_to_module = _parameters_json.pop("path_to_module")

    simulation_parameters_dict = _parameters_json.pop("simulation_parameters")
    module_config_path = str(Path(result_directory).joinpath("module_config.json"))
    simulation_parameters_path = str(Path(result_directory).joinpath("simulation_parameters.json"))
    options = _parameters_json.pop("options", {})
    building_config = _parameters_json.pop("building_config", {})
    system_setup_config = _parameters_json.pop("system_setup_config", {})
    module_config_dict = {
        "options": options,
        "building_config": building_config,
        "system_setup_config": system_setup_config,
    }

    if _parameters_json:
        raise AttributeError(f"There are unused attributes ({_parameters_json.keys()}) in parameters JSON.")

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
        simulation_parameters,
        module_config_path,
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) == 1:
        PARAMETERS_JSON_FILE: str = "input/request.json"
        RESULT_DIRECTORY: str = "results"
        if not Path(PARAMETERS_JSON_FILE).is_file():
            log.information("Please specify an input JSON file or place it in `input/request.json`.")
            sys.exit(1)
    elif len(sys.argv) == 2:
        PARAMETERS_JSON_FILE: str = sys.argv[1]
        RESULT_DIRECTORY: str = "results"
    elif len(sys.argv) == 3:
        PARAMETERS_JSON_FILE: str = sys.argv[1]
        RESULT_DIRECTORY: str = sys.argv[2]
    else:
        log.information("HiSim from JSON received too many arguments.")
        sys.exit(1)

    log.information(f"Reading parameters from {PARAMETERS_JSON_FILE}.")
    with open(PARAMETERS_JSON_FILE, "r", encoding="utf8") as file:
        my_parameters_json: dict[str, Any] | list[Any] = json.load(file)

    (
        my_path_to_module,
        my_simulation_parameters,
        my_module_config,
    ) = make_system_setup(parameters_json=my_parameters_json, result_directory=RESULT_DIRECTORY)

    main(
        my_path_to_module,
        my_simulation_parameters,
        my_module_config,
    )
