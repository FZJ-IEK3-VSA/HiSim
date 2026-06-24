""" Main module for HiSim: Starts the Simulator. """
# clean
import os
import warnings
import importlib
from pathlib import Path
import sys
from datetime import datetime
from typing import Optional, Any, cast
import argparse
from pydantic import TypeAdapter
from dotenv import load_dotenv

try:
    from hisim.json_executor import setup_components_and_connections
    from hisim.postprocessingoptions import PostProcessingOptions
    import hisim.simulator as sim
    from hisim import log
    from hisim.simulationparameters import SimulationParameters
    from hisim.sim_repository_singleton import SingletonSimRepository, SingletonDictKeyEnum
except ModuleNotFoundError:
    raise ModuleNotFoundError(
        "Could not import HiSim modules. "
        "It may not be installed in the current Python environment.\n\n"
        "If you already installed HiSim locally with 'pip install -e .', "
        "make sure you are using the same virtual environment/interpreter.\n\n"
        "If you recently updated the repository via 'git pull', new dependencies "
        "may have been added. Try re-running 'pip install -e .' from the HiSim "
        "root directory to install any missing packages."
    ) from None

load_dotenv()

__authors__ = "Valentin Janser"
__credits__ = ["Noah Pflugradt", "Katharina Rieck"]
__maintainer__ = "Valentin Janser"
__email__ = "v.janser@fz-juelich.de"


def is_hisim_root(path: Path) -> bool:
    """Check if given path is HiSim root directory."""
    return (path / "setup.py").exists() and (path / "hisim").is_dir()


def get_description_from_py(path_obj: Path) -> str:
    """Extract brief description from the first line of the system setup python file."""
    with path_obj.open("r", encoding="utf-8") as file:
        first_line = file.readline().strip()

    desc = first_line
    for quote_type in ['"""', "'''"]:
        if first_line.startswith(quote_type):
            desc = first_line.replace(quote_type, "").strip()
            break
    return desc


def initialize_from_python(
    path_to_module: str,
    my_simulation_parameters: Optional[SimulationParameters] = None,
    my_module_config: Optional[str] = None,
) -> sim.Simulator:
    """Initialize the simulator based on a Python household configuration and optional simulation parameters and module config."""

    function_in_module = "setup_function"

    # Normalize module path and resolve absolute path
    path_obj = Path(path_to_module).with_suffix(".py").resolve()

    # Get module name (filename without suffix)
    module_filename = path_obj.stem

    # Add parent directory to PYTHONPATH
    module_dir = path_obj.parent
    for parent in path_obj.parents:
        if parent.exists():
            sys.path.append(str(parent))
            if is_hisim_root(parent):
                break
        else:
            raise ValueError(f"Directory of module does not exist: {module_dir}")

    # Final check and import
    if not path_obj.is_file():
        raise ValueError(f"Python script {module_filename}.py could not be found at {path_obj}")

    SingletonSimRepository().set_entry(
        key=SingletonDictKeyEnum.DESCRIPTION, entry=f"{get_description_from_py(path_obj)}",
    )

    # Make setup function executable
    targetmodule = importlib.import_module(module_filename)

    # Initialize simulator based on setup function
    my_sim: sim.Simulator = sim.Simulator(
        module_directory=str(module_dir),
        module_filename=module_filename,
        setup_function=function_in_module,
        my_simulation_parameters=my_simulation_parameters,
        my_module_config=my_module_config,
    )

    # Build method
    model_init_method = getattr(targetmodule, function_in_module)

    # Pass setup function to simulator
    model_init_method(my_sim, my_simulation_parameters)

    return my_sim


def load_json_file(path_str: str) -> dict[str, Any]:
    """Load a JSON file and return it as a Python dict."""
    # This function was created with the help of ChatGPT

    path = Path(path_str).expanduser().resolve()

    try:
        with path.open("r", encoding="utf-8") as f:
            json_dict = TypeAdapter(dict[str, Any])
            data = json_dict.validate_json(f.read())
            return cast(dict[str, Any], data)
    except Exception as e:
        raise ValueError(f"Invalid JSON in file {path}: {e}") from e


def initialize_from_json(
    scenario: str,
    simulation_parameters: str,
    path_to_module: str,
    delta: Optional[str],
) -> sim.Simulator:
    """Initialize the simulator based on JSON scenario and simulation parameters."""

    # Load JSON files
    scenario_data = load_json_file(scenario)
    SingletonSimRepository().set_entry(
        key=SingletonDictKeyEnum.DESCRIPTION, entry=f"{scenario_data.get('description', '')}",
    )
    # Missing in the following data: result_directory, surplus_control, cache_dir_path, multiple_buildings
    # -> Result Directory is set in prepare_simulation_directory function, called by run_all_timesteps
    # -> Cache Dir Path is filled by default in SimulationParameters
    # -> Surplus Control: see comment in hisim_convert_to_json.py
    sim_params_data = load_json_file(simulation_parameters)
    sim_params_data['multiple_buildings'] = scenario_data.get('multiple_buildings', False)
    sim_params_data['start_date'] = datetime.fromisoformat(sim_params_data['start_date'])
    sim_params_data['end_date'] = datetime.fromisoformat(sim_params_data['end_date'])
    sim_params_data['post_processing_options'] = [PostProcessingOptions[option] for option in sim_params_data.get('post_processing_options', [])]
    sim_params = SimulationParameters(**sim_params_data)
    sim_params.log_connections = True  # For easy post-processing (and debugging)

    # Normalize module path and resolve absolute path
    path_obj = Path(path_to_module).with_suffix(".json").resolve()
    # Get module name (filename without suffix)
    module_filename = path_obj.stem
    # Add parent directory to PYTHONPATH
    module_dir = path_obj.parent
    if module_dir.exists():
        sys.path.append(str(module_dir))
    else:
        raise ValueError(f"Directory of module does not exist: {module_dir}")

    my_sim = sim.Simulator(
        module_directory=module_dir,
        module_filename=module_filename,
        setup_function="setup_function",  # In JSON mode we do not use a setup function; but must not be None for post-processing
        my_module_config=None,
        my_simulation_parameters=sim_params,
    )  # type: ignore[no-any-return]

    my_sim = setup_components_and_connections(scenario_data, my_sim, sim_params)

    if delta:
        log.warning("====================================================================")
        log.warning("== The delta file is currently not supported and will be ignored. ==")
        log.warning("====================================================================")

    return my_sim  # type: ignore[no-any-return]


def run_simulation(my_sim: sim.Simulator, path_to_module: Optional[str]) -> None:
    """Runs the simulation (for both Python-based and JSON-based executions)."""

    # If debugging is needed, this may be used to print components and their inputs/outputs
    for comp in my_sim.wrapped_components:
        log.debug(f"Component {comp.my_component.component_name} has inputs "
                  f"{[input.full_name for input in comp.component_inputs]} and"
                  f" outputs {[output.full_name for output in comp.component_outputs]}")

    log.information("#################################")
    log.information(f"Starting simulation of {path_to_module}")
    starttime = datetime.now()
    starting_date_time_str = starttime.strftime("%d-%b-%Y %H:%M:%S")
    log.information(f"Start @ {starting_date_time_str}")
    log.information("#################################")

    # Perform simulation throughout the defined timeline
    my_sim.run_all_timesteps()

    log.information("#################################")
    endtime = datetime.now()
    starting_date_time_str = endtime.strftime("%d-%b-%Y %H:%M:%S")
    log.information("finished @ " + starting_date_time_str)
    log.profile("finished @ " + starting_date_time_str)
    log.profile("duration: " + str((endtime - starttime).total_seconds()))
    log.information("#################################")
    log.information("")

    log.logger.reset()


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for HiSim execution."""

    # Adapted from README from https://github.com/FZJ-IEK3-VSA/HiSim
    description = (
        "ETHOS.HiSim --- Household Infrastructure and Building Simulator\n\n"
        "ETHOS.HiSim allows simulating and analyzing household scenarios "
        "and building systems, integrating load profiles generation of "
        "electricity consumption/generation, heating demand, "
        "and smart strategies of modern components, such as heat pump, "
        "battery, electric vehicle or thermal energy storage."
    )

    parser = argparse.ArgumentParser(
        prog="python hisim_main.py",
        description=description,
        formatter_class=argparse.RawTextHelpFormatter,
    )

    parser.add_argument(
        "inputs",
        nargs="+",
        help=(
            "JSON mode:\n"
            "  <scenario_params.json> <simulation_params.json> [scenario_delta.json]\n\n"
            "Legacy Python mode:\n"
            "  <module.py> [module_config]"
        ),
    )

    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> dict[str, Optional[str]]:
    """Check the provided command-line arguments for validity and determine execution mode."""

    inputs = args.inputs

    if inputs[0].endswith(".py"):
        if len(inputs) > 2:
            raise ValueError(
                "The legancy Python mode accepts at most 2 arguments:\n"
                "  <module.py> [module_config]"
            )

        module_file = inputs[0]
        module_config = inputs[1] if len(inputs) == 2 else None

        if not os.path.isfile(module_file):
            raise FileNotFoundError(f"Python module not found: {module_file}")

        return {
            "mode": "python",
            "module_file": module_file,
            "module_config": module_config,
        }

    if inputs[0].endswith(".json"):
        if len(inputs) < 2:
            raise ValueError(
                "The JSON mode requires at least 2 files:\n"
                "  <scenario.json> <simulation_params.json> [delta.json]"
            )
        if len(inputs) > 3:
            raise ValueError(
                "The JSON mode accepts at most 3 files:\n"
                "  <scenario.json> <simulation_params.json> [delta.json]"
            )

        for f in inputs:
            if not f.endswith(".json"):
                raise ValueError(f"Invalid file in JSON mode (must be .json): {f}")
            if not os.path.isfile(f):
                raise FileNotFoundError(f"File not found: {f}")

        return {
            "mode": "json",
            "scenario": inputs[0],
            "simulation": inputs[1],
            "delta": inputs[2] if len(inputs) == 3 else None,
        }

    raise ValueError(
        "First argument must be either:\n"
        "  - a Python file (*.py) for legacy Python mode, or\n"
        "  - a JSON file (*.json) for JSON mode"
    )


def get_required_config_value(config: dict[str, Optional[str]], key: str) -> str:
    """Return a required command-line config value."""
    value = config[key]
    if value is None:
        raise ValueError(f"Missing required command-line argument: {key}")
    return value


def main_cli() -> None:
    """Main function for command-line execution of HiSim, supporting both Python-based and JSON-based scenarios."""

    args = parse_args()
    config = validate_args(args)

    # Suppress warnings (e.g., from pvlib)
    warnings.filterwarnings("ignore")

    my_sim: sim.Simulator
    ptm: str
    # Dispatching logic
    if config["mode"] == "python":
        module_file = get_required_config_value(config, "module_file")
        print(f"Calling setup_function from {module_file}")
        my_sim = initialize_from_python(
            path_to_module=module_file,
            my_module_config=config["module_config"],
        )
        ptm = module_file

    elif config["mode"] == "json":
        scenario = get_required_config_value(config, "scenario")
        simulation = get_required_config_value(config, "simulation")
        print(
            f"Running simulation of scenario {scenario} with simulation parameters {simulation}"
            + (f" and delta {config['delta']}" if config["delta"] else "")
        )
        my_sim = initialize_from_json(
            scenario=scenario,
            simulation_parameters=simulation,
            path_to_module=scenario,
            delta=config["delta"],
        )
        ptm = scenario

    run_simulation(my_sim, path_to_module=ptm)


def main(path_to_module: str, my_simulation_parameters: Optional[SimulationParameters] = None, my_module_config: Optional[str] = None) -> None:
    """Main function only needed for legacy functionality, such as system setup tests."""

    my_sim = initialize_from_python(
        path_to_module=path_to_module,
        my_simulation_parameters=my_simulation_parameters,
        my_module_config=my_module_config,
    )
    run_simulation(my_sim, path_to_module=path_to_module)


if __name__ == "__main__":
    main_cli()
