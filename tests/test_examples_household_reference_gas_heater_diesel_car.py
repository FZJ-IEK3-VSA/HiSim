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
def test_basic_household():
    """ Single day. """
    path = "../examples/household_reference_gas_heater_diesel_car.py"
    func = "household_reference_gas_heater_diesel_car"
    mysimpar = SimulationParameters.one_day_only_with_only_plots(year=2019, seconds_per_timestep=60)
    hisim_main.main(path, func, mysimpar)
    log.information(os.getcwd())
