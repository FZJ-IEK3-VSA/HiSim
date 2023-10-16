"""
Read setup parameters from a JSON file and build a system setup for a specific example that is
defined in the JSON file. The setup is simulated and result files are stored in `/results`.

Run `hisim/hisim_from_json.py <json-file>` to start a simulation or run `hisim/hisim_from_json.py`
to read the JSON from `input/request.json`.
"""

import importlib
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from typing import Optional, Union
import json
from hisim import log
from hisim.simulator import SimulationParameters
from hisim.hisim_main import main
from hisim.postprocessingoptions import PostProcessingOptions
from pathlib import Path


@dataclass_json
@dataclass
class CostParameters:
    electricity_price: float
    gas_price: float


@dataclass_json
@dataclass
class SystemSetupParameters:
    path_to_module: str
    function_in_module: str
    config_class_name: str
    simple_parameters: bool
    cost_parameters: CostParameters
    building_type: str
    number_of_people: int
    heat_pump_power: Optional[float] = None


def read_parameters_json(parameters_json_path):
    with open(parameters_json_path, "r") as c:
        parameters_json: Union[dict, list] = json.load(c)
    simple_parameters = True
    return parameters_json, simple_parameters


def make_system_setup(
    parameters_json: Union[dict, list],
    result_directory: str = "result",
    simplified: bool = True,
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
    path_to_module = parameters_json.get("path_to_module")
    setup_module_name = path_to_module.replace(".py", "").replace("/", ".")
    config_class_name = parameters_json.get("config_class_name")
    function_in_module = parameters_json.get("function_in_module")

    # Import modules for the specified system setup
    # this_module = importlib.import_module("examples.household_1_advanced_hp_diesel_car")
    # this_class = getattr(this_module, "HouseholdAdvancedHPDieselCarConfig")
    setup_module = importlib.import_module(setup_module_name)
    config_class = getattr(setup_module, config_class_name)

    setup_config: config_class = config_class.get_default()

    if simplified:
        # Set parameters manually.
        parameters: SystemSetupParameters = SystemSetupParameters.schema().load(  # type: ignore
            parameters_json, many=False
        )
        if (
            setup_module_name == "examples.household_1_advanced_hp_diesel_car"
            and config_class_name == "HouseholdAdvancedHPDieselCarConfig"
        ):
            setup_config.building_type = parameters.building_type
            _ = parameters.cost_parameters.electricity_price
            _ = parameters.cost_parameters.gas_price
            _ = parameters.number_of_people
            _ = parameters.heat_pump_power

        else:
            raise NotImplementedError(
                "System Setup Starter can only handle `examples.household_1_advanced_hp_diesel_car` now."
            )

    else:
        # Created with utils.create_config_json_template
        with open(
            "examples/household_1_advanced_hp_diesel_car_household_1_advanced_hp_diesel_car_configurable.json",
            "r",
        ) as o:
            template_json = json.load(o)

        def check_values(parameter_dict, template_dict):
            for key, value in parameter_dict.items():
                if isinstance(value, dict):
                    check_values(value, template_dict[key])
                else:
                    if template_dict[key]:
                        pass
                    else:
                        print(key)
                        print(value)
                        raise ValueError("Parameter cannot be set with JSON.")

        check_values(parameters_json, template_json)

        def set_values(setup_config, parameter_dict, nested=[]):
            for key, value in parameter_dict.items():
                if isinstance(value, dict):
                    set_values(setup_config, value, nested + [key])
                else:
                    setattr(setup_config, ".".join(nested + [key]), value)

        set_values(setup_config, parameters_json)

    # Save to file
    with open(module_config_path, "w", encoding="utf8") as out_file:
        json.dump(setup_config.to_json(), out_file)

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

    parameters_json, simplified = read_parameters_json(parameters_json_path)

    (
        path_to_module,
        function_in_module,
        simulation_parameters,
        module_config_path,
    ) = make_system_setup(parameters_json=parameters_json, simplified=simplified)

    main(
        path_to_module,
        function_in_module,
        simulation_parameters,
        module_config_path,
    )
