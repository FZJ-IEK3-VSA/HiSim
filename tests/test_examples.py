""" Tests for the basic household example. """
# clean
import os

from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim.postprocessingoptions import PostProcessingOptions
from hisim import utils


@utils.measure_execution_time
def test_basic_household():
    """ Single day with all options. """
    path = "../examples/basic_household.py"
    func = "basic_household_explicit"
    mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
    hisim_main.main(path, func, mysimpar)
    log.information(os.getcwd())


@utils.measure_execution_time
def test_basic_household_network_chart():
    """ Makes only the network charts. """
    path = "../examples/basic_household.py"
    func = "basic_household_explicit"
    mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
    mysimpar.post_processing_options.append(PostProcessingOptions.MAKE_NETWORK_CHARTS)
    hisim_main.main(path, func, mysimpar)
    log.information(os.getcwd())


@utils.measure_execution_time
def test_basic_household_with_all_resultfiles():
    """ One day with all options. """
    path = "../examples/basic_household.py"
    func = "basic_household_explicit"
    mysimpar = SimulationParameters.one_day_only_with_all_options(year=2019, seconds_per_timestep=60)
    for option in PostProcessingOptions:
        mysimpar.post_processing_options.append(option)
    hisim_main.main(path, func, mysimpar)
    log.information(os.getcwd())


@utils.measure_execution_time
def test_modular_household_configurations():
    """ Tests the modular households. """
    path = "../examples/modular_household.py"
    func = "modular_household_explicit"
    mysimpar = SimulationParameters.one_day_only_with_all_options(year=2019, seconds_per_timestep=60 * 15)
    hisim_main.main(path, func, mysimpar)
