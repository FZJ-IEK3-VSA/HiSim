import importlib
import sys
from datetime import datetime
from hisim import log
import hisim.simulator as sim
import os
#from hisim.postprocessing import postprocessing_main as pp

def main(path_to_module: str, function_in_module: str, my_simulation_parameters = None):
    log.information("#################################")
    log.information("starting simulation of " + path_to_module  + " " + function_in_module)
    starttime = datetime.now()
    d4 = starttime.strftime("%d-%b-%Y %H:%M:%S")
    log.information("Start @ " + d4 + " "  )
    log.profile(path_to_module  + " " + function_in_module + "Start @ " + d4 )
    log.information("#################################")
    normalized_path = os.path.normpath(path_to_module)
    path_in_list = normalized_path.split(os.sep)
    module_filename = path_in_list[-1]
    if len(path_in_list) >= 1:
        path_to_be_added = os.path.join(os.getcwd(), *path_in_list[:-1])
        if os.path.isdir(path_to_be_added):
            # Add current path to PYTHONPATH
            sys.path.append(path_to_be_added)
            #for dirs in os.walk(path_to_be_added):
            #    sys.path.append(dirs)
        else:
            raise ValueError("Directory location of module location is nonexistent!\nDirectory entered: " + path_to_be_added)
    suffix =module_filename[-3:]
    if suffix != ".py":
        module_fullfilename = "{}.py".format(module_filename)
    else:
        module_fullfilename = module_filename
        module_filename = module_filename[:-3]
    filepath = os.path.join(path_to_be_added, module_fullfilename)
    if os.path.isfile(filepath):
        # Get setup function to executable
        targetmodule = importlib.import_module(module_filename)
    else:
        raise ValueError("Python script {}.py could not be found".format(module_filename))

    # Create a Simulator object based on setup function
    my_sim: sim.Simulator = sim.Simulator(module_directory=path_to_be_added,
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
    d4 = endtime.strftime("%d-%b-%Y %H:%M:%S")
    log.information("finished @ " + d4)
    log.profile("finished @ " + d4)
    log.profile("duration: " + str((endtime-starttime).total_seconds()))
    log.information("#################################")
    log.information("")

if __name__ == "__main__":
    #logging.basicConfig(level=logging.DEBUG)
    if len(sys.argv) < 3:
        log.information("HiSim needs two arguments")
        quit()
    filename = sys.argv[1]
    functionname = sys.argv[2]
    log.information("calling " + functionname + " from " + filename)
    main(filename, functionname)

