""" HiSim converter from legacy python-based system setups to JSON-based configurations. """
# clean
import warnings
import importlib
from pathlib import Path
import sys
from datetime import datetime
from typing import Any, Callable, Optional, Tuple
# Third party imports
from dotenv import load_dotenv
# First party imports
import hisim.simulator as sim
from hisim import log
from hisim.simulationparameters import SimulationParameters
from hisim.json_generator import write_standalone_scenario_json, write_standalone_simulation_json


load_dotenv()


def get_description_from_py(setup_py_path: Path) -> str:
    """Extract a brief description from the first line of a system setup file.

    Reads only the first line of the given Python file, strips a leading
    triple-quote delimiter (triple double quotes or triple single quotes) if present, and returns
    the remaining text. For an empty file an empty string is returned.

    Args:
        setup_py_path: Path to the Python setup file to read.

    Returns:
        The first line of the file with any surrounding triple quotes removed
        and surrounding whitespace stripped; an empty string for an empty file.
    """

    with open(setup_py_path, 'r', encoding="utf-8") as f:
        first_line = f.readline().strip()

    desc = first_line
    for quote_type in ['"""', "'''"]:
        if first_line.startswith(quote_type):
            desc = first_line.replace(quote_type, '').strip()
            break

    return desc


def main(
    path_to_module: str,
    my_simulation_parameters: Optional[SimulationParameters] = None,
    my_module_config: Optional[str] = None,
    output_directory: Optional[str] = None,
) -> None:
    """Convert a Python-based system setup to JSON-based configuration files.

    Loads a Python module containing a `setup_function`, initializes a
    Simulator, and writes out `.simulation.json` and `.scenario.json` files
    describing the configuration. Does not run the simulation.

    Args:
        path_to_module: Path or module name of the Python setup file
            (without the `.py` extension).
        my_simulation_parameters: Optional SimulationParameters passed to the
            setup function.
        my_module_config: Optional module configuration string.
        output_directory: Optional directory for output JSON files.
            Defaults to the directory containing the module.

    Raises:
        ValueError: If the module directory or the Python file cannot be found.
    """
    # Suppress warnings (e.g., from pvlib)
    warnings.filterwarnings("ignore")

    # Logging simulation start
    function_in_module = "setup_function"
    log.information("#################################")
    log.information(f"Starting simulation of {path_to_module}")
    starttime = datetime.now()
    start_timestamp_str = starttime.strftime("%d-%b-%Y %H:%M:%S")
    log.information(f"Start @ {start_timestamp_str}")
    log.profile(f"{path_to_module} {function_in_module} Start @ {start_timestamp_str}")
    log.information("#################################")

    # Normalize module path and resolve absolute path
    setup_py_path = Path(path_to_module).with_suffix(".py").resolve()

    # Get module name (filename without suffix)
    module_filename = setup_py_path.stem

    # Add parent directory to PYTHONPATH
    module_dir = setup_py_path.parent
    output_dir = Path(output_directory).resolve() if output_directory is not None else module_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    for parent in setup_py_path.parents:
        if parent.exists():
            sys.path.append(str(parent))
        else:
            raise ValueError(f"Directory of module does not exist: {module_dir}")

    # Final check and import
    if not setup_py_path.is_file():
        raise ValueError(f"Python script {module_filename}.py could not be found at {setup_py_path}")

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
    setup_function_ref: Callable[..., Any] = getattr(targetmodule, function_in_module)
    # Call the setup function, passing the simulator as an argument
    setup_function_ref(my_sim, my_simulation_parameters)
    # Write the simulation parameters now, then alter them to include advanced logging
    write_standalone_simulation_json(my_sim, path=str(output_dir / f"{module_filename}.simulation.json"))

    my_simulation_parameters = my_sim.get_simulation_parameters()
    my_simulation_parameters.log_connections = True

    # Other name due to mypy no-redef
    sim_with_logging: sim.Simulator = sim.Simulator(
        module_directory=str(module_dir),
        module_filename=module_filename,
        setup_function=function_in_module,
        my_simulation_parameters=my_simulation_parameters,
        my_module_config=my_module_config,
    )
    setup_function_ref(sim_with_logging, my_simulation_parameters)

    if sim_with_logging.my_module_config is not None:
        log.warning(f"Module config is not None but not exported to JSON: {sim_with_logging.my_module_config}")

    # Do not run the simulation
    sim_with_logging.prepare_calculation()
    sim_with_logging.connect_all_components()

    desc = get_description_from_py(setup_py_path)

    write_standalone_scenario_json(
        module_filename=module_filename,
        my_sim=sim_with_logging,
        desc=desc,
        path=str(output_dir / f"{module_filename}.scenario.json"),
    )

    log.information("#################################")
    endtime = datetime.now()
    end_timestamp_str = endtime.strftime("%d-%b-%Y %H:%M:%S")
    log.information(f"finished @ {end_timestamp_str}")
    log.profile(f"finished @ {end_timestamp_str}")
    log.profile(f"duration: {(endtime - starttime).total_seconds()}")
    log.information("#################################")
    log.information("")

    log.logger.reset()


def write_json_for_initialized_simulator(
    path_to_module: str,
    my_sim: sim.Simulator,
    output_directory: Optional[str] = None,
) -> Tuple[Path, Path]:
    """Write JSON configuration files for an already-initialized simulator.

    Writes ``<module>.simulation.json`` and ``<module>.scenario.json`` for the
    given simulator, using the description extracted from the first line of the
    setup file. The simulator's ``log_connections`` flag is temporarily
    disabled while writing the simulation JSON and restored afterwards.

    Args:
        path_to_module: Path or module name of the Python setup file (without
            the ``.py`` extension); used to derive the module name and to read
            the description.
        my_sim: An initialized ``Simulator`` whose components are connected in
            order to serialize the scenario.
        output_directory: Optional directory for the output JSON files.
            Defaults to the directory containing the setup file.

    Returns:
        A ``(scenario_path, simulation_parameters_path)`` tuple giving the
        paths of the written scenario and simulation JSON files, in that order.
    """
    setup_py_path = Path(path_to_module).with_suffix(".py").resolve()
    module_filename = setup_py_path.stem
    output_dir = Path(output_directory).resolve() if output_directory is not None else setup_py_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    simulation_parameters_path = output_dir / f"{module_filename}.simulation.json"
    scenario_path = output_dir / f"{module_filename}.scenario.json"

    my_simulation_parameters = my_sim.get_simulation_parameters()
    original_log_connections = my_simulation_parameters.log_connections
    try:
        my_simulation_parameters.log_connections = False
        write_standalone_simulation_json(my_sim, path=str(simulation_parameters_path))
    finally:
        my_simulation_parameters.log_connections = original_log_connections

    if my_sim.my_module_config is not None:
        log.warning(f"Module config is not None but not exported to JSON: {my_sim.my_module_config}")

    my_sim.prepare_calculation()
    my_sim.connect_all_components()

    desc = get_description_from_py(setup_py_path)
    write_standalone_scenario_json(
        module_filename=module_filename,
        my_sim=my_sim,
        desc=desc,
        path=str(scenario_path),
    )
    return scenario_path, simulation_parameters_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        log.information("HiSim converter needs at least one argument.")
        sys.exit(1)
    FILE_NAME: str = sys.argv[1]
    FUNCTION_NAME: str = "setup_function"
    if len(sys.argv) == 2:
        log.information(f"calling {FUNCTION_NAME} from {FILE_NAME}")
        main(path_to_module=FILE_NAME)
    if len(sys.argv) == 3:
        MODULE_CONFIG: str = sys.argv[2]
        log.information(f"calling {FUNCTION_NAME} from {FILE_NAME} with module config {MODULE_CONFIG}")
        main(
            path_to_module=FILE_NAME,
            my_module_config=MODULE_CONFIG,
        )
