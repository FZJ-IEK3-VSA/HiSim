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
    """ Single day. """
    path = "../examples/basic_household.py"
    func = "basic_household_explicit"
    mysimpar = SimulationParameters.one_day_only_with_all_options(year=2019, seconds_per_timestep=60)
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
def test_modular_household_configurations():
    """ Tests the modular example. """
    path = "../examples/modular_example.py"
    func = "modular_household_explicit"
    mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60 * 15)
    hisim_main.main(path, func, mysimpar)


@utils.measure_execution_time
def test_household_with_heatpump_and_pv():
    """ Single day. """
    path = "../examples/household_with_heatpump_and_pv.py"
    func = "household_pv_hp"
    mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
    hisim_main.main(path, func, mysimpar)
    log.information(os.getcwd())


@utils.measure_execution_time
def test_household_with_gas_heater():
    """ Single day. """
    path = "../examples/household_with_gas_heater.py"
    func = "household_gas_heater"
    mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
    hisim_main.main(path, func, mysimpar)
    log.information(os.getcwd())
