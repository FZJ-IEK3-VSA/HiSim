"""Tests for the basic household system setup."""

# clean
import os
import pytest

from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils
from hisim.sim_repository_singleton import SingletonMeta

@pytest.fixture(autouse=True)
def reset_singletons():
    """This function resets the Singleton SimRepo which is needed for github pytest workflows."""
    SingletonMeta._instances.clear()  # pylint: disable=protected-access

@pytest.mark.system_setups
@utils.measure_execution_time
def test_basic_household():
    """Single day."""
    path = "../system_setups/basic_household.py"
    mysimpar = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    hisim_main.main(path, mysimpar)
    log.information(os.getcwd())
