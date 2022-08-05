import os

from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
import shutil
import random
from hisim import log
from hisim.postprocessingoptions import PostProcessingOptions
import matplotlib.pyplot as plt
from hisim import utils

@utils.measure_execution_time
def test_basic_household():
  #  if os.path.isdir("../hisim/inputs/cache"):
   #     shutil.rmtree("../hisim/inputs/cache")
    path = "../examples/basic_household.py"
    func = "basic_household_explicit"
    mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
    hisim_main.main(path, func,mysimpar )
    log.information(os.getcwd())

@utils.measure_execution_time
def test_basic_household_with_default_connections():
  #  if os.path.isdir("../hisim/inputs/cache"):
   #     shutil.rmtree("../hisim/inputs/cache")
    path = "../examples/basic_household.py"
    func = "basic_household_with_default_connections"
    mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
    hisim_main.main(path, func,mysimpar )
    log.information(os.getcwd())

@utils.measure_execution_time
def test_basic_household_with_all_resultfiles():
   # if os.path.isdir("../hisim/inputs/cache"):
    #    shutil.rmtree("../hisim/inputs/cache")
    path = "../examples/basic_household.py"
    func = "basic_household_explicit"
    mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
    for option in PostProcessingOptions:
        mysimpar.post_processing_options.append(option)
    hisim_main.main(path, func,mysimpar )
    log.information(os.getcwd())

#
# def test_basic_household_with_all_resultfiles_full_year():
#     if os.path.isdir("../hisim/inputs/cache"):
#         shutil.rmtree("../hisim/inputs/cache")
#     path = "../examples/basic_household.py"
#     func = "basic_household_explicit"
#     mysimpar = SimulationParameters.full_year(year=2019, seconds_per_timestep=60)
#     for option in PostProcessingOptions:
#         mysimpar.post_processing_options.append(option)
#         log.information(option)
#     hisim_main.main(path, func,mysimpar)
#     log.information(os.getcwd())


# def test_basic_household_boiler():
#     path = "../examples/basic_household_boiler.py"
#     func = "basic_household_boiler_explicit"
#     mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
#     hisim_main.main(path, func, mysimpar)

# def test_basic_household_districtheating():
#     path = "../examples/basic_household_Districtheating.py"
#     func = "basic_household_Districtheating_explicit"
#     mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
#     hisim_main.main(path, func, mysimpar)

# def test_basic_household_oilheater():
#     path = "../examples/basic_household_Oilheater.py"
#     func = "basic_household_Oilheater_explicit"
#     mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
#     hisim_main.main(path, func, mysimpar)
@utils.measure_execution_time
def test_modular_household_configurations( ):
    path = "../examples/modular_household.py"
    func = "modular_household_explicit"
    mysimpar = SimulationParameters.one_day_only( year = 2019, seconds_per_timestep = 60 * 15 )
    hisim_main.main( path, func, mysimpar )
@utils.measure_execution_time
def test_first_example():
    path = "../examples/examples.py"
    func = "first_example"
    mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
    hisim_main.main(path, func, mysimpar)
@utils.measure_execution_time
def test_second_example():
    path = "../examples/examples.py"
    func = "second_example"
    mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
    hisim_main.main(path, func, mysimpar)
