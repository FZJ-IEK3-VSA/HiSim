""" Main module for HiSim: Starts the Simulator. """
# clean
import warnings
import importlib
import os
import sys
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv

import hisim.simulator as sim
from hisim import log
from hisim.simulationparameters import SimulationParameters

load_dotenv()


def main(
    path_to_module: str,
    my_simulation_parameters: Optional[SimulationParameters] = None,
    my_module_config_path: Optional[str] = None,
) -> None:
    """Core function."""
    # filter warnings due to pvlib, pvlib generates warnings during simulation within pvlib package
    warnings.filterwarnings("ignore")
    # before starting, delete old logging files if path and logging files exist
    logging_default_path = log.LOGGING_DEFAULT_PATH
    if os.path.exists(logging_default_path) and os.listdir(logging_default_path) != []:
        for file in os.listdir(logging_default_path):
            if os.path.exists(os.path.join(logging_default_path, file)):
                try:
                    os.remove(os.path.join(logging_default_path, file))
                except Exception:
                    log.information(
                        "Logging default file could not be removed. This can occur when more than one simulation run simultaneously."
                    )

    function_in_module = "setup_function"
    log.information("#################################")
    log.information("starting simulation of " + path_to_module)
    starttime = datetime.now()
    starting_date_time_str = starttime.strftime("%d-%b-%Y %H:%M:%S")
    log.information("Start @ " + starting_date_time_str + " ")
    log.profile(path_to_module + " " + function_in_module + "Start @ " + starting_date_time_str)
    log.information("#################################")
    normalized_path = os.path.normpath(path_to_module)
    path_in_list = normalized_path.split(os.sep)
    module_filename = path_in_list[-1]
    if len(path_in_list) >= 1:
        path_to_be_added = os.path.join(os.getcwd(), *path_in_list[:-1])
        if os.path.isdir(path_to_be_added):
            #  Add current path to PYTHONPATH
            sys.path.append(path_to_be_added)
        else:
            raise ValueError(
                f"Directory location of module location is nonexistent!\nDirectory entered: {path_to_be_added}"
            )
    suffix = module_filename[-3:]
    if suffix != ".py":
        module_full_filename = f"{module_filename}.py"
    else:
        module_full_filename = module_filename
        module_filename = module_filename[:-3]
    filepath = os.path.join(path_to_be_added, module_full_filename)
    if os.path.isfile(filepath):
        # Get setup function to executable
        targetmodule = importlib.import_module(module_filename)
    else:
        raise ValueError(f"Python script {module_filename}.py could not be found")

    # Create a Simulator object based on setup function
    my_sim: sim.Simulator = sim.Simulator(
        module_directory=path_to_be_added,
        module_filename=module_filename,
        setup_function=function_in_module,
        my_simulation_parameters=my_simulation_parameters,
        my_module_config_path=my_module_config_path,
    )

    # Build method
    model_init_method = getattr(targetmodule, function_in_module)

    # Pass setup function to simulator
    model_init_method(my_sim, my_simulation_parameters)

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

    # At the end put new logging files into result directory
    my_sim.put_log_files_into_result_path()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        log.information("HiSim needs at least one argument.")
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
            my_module_config_path=MODULE_CONFIG,
        )
