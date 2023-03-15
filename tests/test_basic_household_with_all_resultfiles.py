""" Tests for the basic household example. """
# clean
import os

from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils
import pytest

@pytest.mark.base
@utils.measure_execution_time
def test_basic_household_with_all_resultfiles():
    """ One day with all options. """
    path = "../examples/basic_household.py"
    func = "basic_household_explicit"
    mysimpar = SimulationParameters.one_day_only_with_only_plots(year=2019, seconds_per_timestep=60)
    hisim_main.main(path, func, mysimpar)
    log.information(os.getcwd())
