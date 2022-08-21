# """ Module to run a simulation from a json file instead of a python file. """
#
# import hisim.simulator as sim
#
# __authors__ = "Vitor Hugo Bellotto Zago"
# __copyright__ = "Copyright 2021, the House Infrastructure Project"
# __credits__ = ["Noah Pflugradt"]
# __license__ = "MIT"
# __version__ = "0.1"
# __maintainer__ = "Vitor Hugo Bellotto Zago"
# __email__ = "vitor.zago@rwth-aachen.de"
# __status__ = "development"
#
# # IMPORT ALL COMPONENT CLASSES DYNAMICALLY
# # DIRTY CODE. GIVE ME BETTER SUGGESTIONS
#
# # iterate through the modules in the current package
# from json_executor import JsonExecutor
#
#
#
# def basic_household_implicit(my_sim: sim.Simulator) -> None:
#     my_setup_function = JsonExecutor()
#     my_setup_function.build(my_sim)
