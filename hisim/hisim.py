import logging
import importlib
import sys

import simulator as sim
import os
import postprocessing.postprocessing as pp

def main(path_to_module: str, function_in_module: str):

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
            raise ValueError("Directory location of module location is nonexistent!\nDirectory entered: {}".format(path_to_be_added))
    filepath = os.path.join(path_to_be_added, "{}.py".format(module_filename))
    if os.path.isfile(filepath):
        # Get setup function to executable
        targetmodule = importlib.import_module(module_filename)
    else:
        raise ValueError("Python script {}.py could not be found".format(module_filename))

    # Create a Simulator object based on setup function
    my_sim: sim.Simulator = sim.Simulator(module_directory=path_to_be_added,
                                          setup_function=function_in_module)

    # Build method
    model_init_method = getattr(targetmodule, function_in_module)

    # Pass setup function to simulator
    model_init_method(my_sim)

    # Perform simulation throughout the defined timeline
    my_sim.run_all_timesteps()

    # Perform postprocessing
    my_post_processor = pp.PostProcessor(resultsdir=my_sim.resultsdir)
    my_post_processor.run()

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("need two arguments")
        quit()
    filename = sys.argv[1]
    functionname = sys.argv[2]
    print("calling " + functionname + " from " + filename)
    main(filename, functionname)
