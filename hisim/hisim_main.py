""" Main module for HiSim: Starts the Simulator. """
# clean
import importlib
import sys
from datetime import datetime
import os
from typing import Optional
from hisim import log
import hisim.simulator as sim
from hisim.simulationparameters import SimulationParameters


def main(path_to_module: str, function_in_module: str, my_simulation_parameters: Optional[SimulationParameters] = None) -> None:
    """ Core function. """
    log.information("#################################")
    log.information("starting simulation of " + path_to_module + " " + function_in_module)
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
                f"Directory location of module location is nonexistent!\nDirectory entered: {path_to_be_added}")
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
    my_sim: sim.Simulator = sim.Simulator(module_directory=path_to_be_added,
                                          module_filename=module_filename,
                                          setup_function=function_in_module,
                                          my_simulation_parameters=my_simulation_parameters)

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
    if len(sys.argv) < 3:
        log.information("HiSim needs two arguments")
        sys.exit(1)
    FILE_NAME = sys.argv[1]
    FUNCTION_NAME = sys.argv[2]
    log.information("calling " + FUNCTION_NAME + " from " + FILE_NAME)
    main(FILE_NAME, FUNCTION_NAME)
