""" HiSim converter from legacy python-based system setups to JSON-based configurations. """
# clean
import warnings
import importlib
from pathlib import Path
import sys
from datetime import datetime
from typing import Optional
# Third party imports
from dotenv import load_dotenv
# First party imports
import hisim.simulator as sim
from hisim import log
from hisim.simulationparameters import SimulationParameters
from hisim.json_generator import write_standalone_scenario_json, write_standalone_simulation_json


load_dotenv()


def get_description_from_py(path_obj: Path) -> str:
    """Extract brief description from the first line of the system setup python file."""

    with open(path_obj, 'r', encoding="utf-8") as f:
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
) -> None:
    """Core function."""
    # Suppress warnings (e.g., from pvlib)
    warnings.filterwarnings("ignore")

    # Logging simulation start
    function_in_module = "setup_function"
    log.information("#################################")
    log.information(f"Starting simulation of {path_to_module}")
    starttime = datetime.now()
    starting_date_time_str = starttime.strftime("%d-%b-%Y %H:%M:%S")
    log.information(f"Start @ {starting_date_time_str}")
    log.profile(f"{path_to_module} {function_in_module} Start @ {starting_date_time_str}")
    log.information("#################################")

    # Normalize module path and resolve absolute path
    path_obj = Path(path_to_module).with_suffix(".py").resolve()

    # Get module name (filename without suffix)
    module_filename = path_obj.stem

    # Add parent directory to PYTHONPATH
    module_dir = path_obj.parent
    if module_dir.exists():
        sys.path.append(str(module_dir))
    else:
        raise ValueError(f"Directory of module does not exist: {module_dir}")

    # Final check and import
    if not path_obj.is_file():
        raise ValueError(f"Python script {module_filename}.py could not be found at {path_obj}")

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
    # Write the simulation parameters now, then alter them to include advanced logging
    write_standalone_simulation_json(my_sim, path=f"{module_dir}/{module_filename}.simulation.json")

    my_simulation_parameters = my_sim.get_simulation_parameters()
    my_simulation_parameters.log_connections = True

    # Other name due to mypy no-redef
    my_sim2: sim.Simulator = sim.Simulator(
        module_directory=str(module_dir),
        module_filename=module_filename,
        setup_function=function_in_module,
        my_simulation_parameters=my_simulation_parameters,
        my_module_config=my_module_config,
    )
    model_init_method(my_sim2, my_simulation_parameters)

    if my_sim2.my_module_config is not None:
        log.warning(f"Module config is not None but not exported to JSON: {my_sim2.my_module_config}")
    # The config dictionary is already part of the components (under "configuration")
    # if len(my_sim2.config_dictionary) > 0:
    #     log.warning(f"Config dictionary is not empty but not exported to JSON: {my_sim2.config_dictionary}")

    # Do not run the simulation
    my_sim2.prepare_calculation()
    my_sim2.connect_all_components()

    desc = get_description_from_py(path_obj)

    write_standalone_scenario_json(module_filename=module_filename, my_sim=my_sim2, desc=desc, path=f"{module_dir}/{module_filename}.scenario.json")

    log.information("#################################")
    endtime = datetime.now()
    starting_date_time_str = endtime.strftime("%d-%b-%Y %H:%M:%S")
    log.information("finished @ " + starting_date_time_str)
    log.profile("finished @ " + starting_date_time_str)
    log.profile("duration: " + str((endtime - starttime).total_seconds()))
    log.information("#################################")
    log.information("")

    log.logger.reset()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        log.information("HiSim converter needs at least one argument.")
        sys.exit(1)
    FILE_NAME = sys.argv[1]
    FUNCTION_NAME = "setup_function"
    if len(sys.argv) == 2:
        log.information("calling " + FUNCTION_NAME + " from " + FILE_NAME)
        main(path_to_module=FILE_NAME)
    if len(sys.argv) == 3:
        MODULE_CONFIG = sys.argv[2]
        log.information("calling " + FUNCTION_NAME + " from " + FILE_NAME + " with module config " + MODULE_CONFIG)
        main(
            path_to_module=FILE_NAME,
            my_module_config=MODULE_CONFIG,
        )
