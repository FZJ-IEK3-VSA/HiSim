""" Tests for the basic household system setup. """
# clean
import os
import pytest

from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils


@pytest.mark.system_setups
@utils.measure_execution_time
def test_basic_household_with_all_resultfiles():
    """One day with all options."""
    path = "../system_setups/basic_household.py"
    func = "setup_function"
    mysimpar = SimulationParameters.one_day_only_with_only_plots(
        year=2021, seconds_per_timestep=60
    )
    hisim_main.main(path, func, mysimpar)
    log.information(os.getcwd())
