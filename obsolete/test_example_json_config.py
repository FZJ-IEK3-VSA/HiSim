# from hisim import hisim_main
# from hisim.simulationparameters import SimulationParameters
# from hisim import log
# from hisim import utils
# import pathlib
# import sys
# import os
#
# @utils.measure_execution_time
# def test_cfg_automator():
#     directory = pathlib.Path(__file__).parent.parent.resolve()
#     mypath = os.path.join(directory, "system_setups")
#     # setting path
#     sys.path.append(mypath)
#     import json_config_example as cat  # noqa
#     cat.generate_json_for_cfg_automator()
#     path = "../system_setups/json_config_example.py"
#     func = "execute_json_config_example"
#     mysimpar = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
#     mysimpar.enable_all_options()
#     hisim_main.main(path, func, mysimpar)
#     log.information(os.getcwd())
