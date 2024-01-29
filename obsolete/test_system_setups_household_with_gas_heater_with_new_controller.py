""" Tests for the basic household system setup. """
# clean
import os
import pytest

from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils


# @pytest.mark.system_setups
@pytest.mark.utsp
@utils.measure_execution_time
def test_household_with_gas_heater_with_controller():
    """Single day."""
    path = "../system_setups/household_with_gas_heater_with_new_controller.py"

    mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
    hisim_main.main(path, mysimpar)
    log.information(os.getcwd())
