# import os
#
# from hisim import hisim_main
# from hisim.simulationparameters import SimulationParameters
# from hisim import log
# from hisim.postprocessingoptions import PostProcessingOptions
# #import .../examples/basic_household_cfg_automator  as cat
# import matplotlib.pyplot as plt
# from hisim import utils
# import pathlib
# import sys
# import os
#
# @utils.measure_execution_time
# def test_cfg_automator():
#     directory = pathlib.Path(__file__).parent.parent.resolve()
#     mypath = os.path.join(directory, "examples")
#     print (mypath)
#     # setting path
#     sys.path.append(mypath)
#     import basic_household_cfg_automator as cat
#     cat.generate_json_for_cfg_automator()
#     path = "../examples/basic_household_cfg_automator.py"
#     func = "basic_household_implicit"
#     mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
#     for option in PostProcessingOptions:
#         mysimpar.post_processing_options.append(option)
#     hisim_main.main(path, func, mysimpar)
#     log.information(os.getcwd())