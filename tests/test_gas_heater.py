""" Tests for the basic household example. """
# clean
import os
import pytest
from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils


@pytest.mark.examples
@utils.measure_execution_time
def test_household_with_gas_heater():
    """ Single day. """
    path = "../examples/household_with_gas_heater.py"
    func = "household_gas_heater"
    mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
    hisim_main.main(path, func, mysimpar)
    log.information(os.getcwd())
