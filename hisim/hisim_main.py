""" Main module for HiSim: Starts the Simulator. """
# clean
import warnings
import importlib
from pathlib import Path
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
    my_module_config: Optional[str] = None,
) -> None:
    """Core function."""
    # Suppress warnings (e.g., from pvlib)
    warnings.filterwarnings("ignore")

    # Delete old log files
    logging_default_path = Path(log.LOGGING_DEFAULT_PATH)
    if logging_default_path.exists() and logging_default_path.is_dir():
        for file in logging_default_path.iterdir():
            try:
                file.unlink()
            except Exception:
                log.information("Logging default file could not be removed. This can occur when more than one simulation run simultaneously.")

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
            my_module_config=MODULE_CONFIG,
        )
