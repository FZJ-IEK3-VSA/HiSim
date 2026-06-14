""" Tests for the basic household system setup with simulation params and without. """
# clean
import os
from pathlib import Path
import pytest

from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils

# Get HiSim root directory
hisim_root = Path(__file__).resolve().parent.parent


@pytest.mark.extendedbase
@utils.measure_execution_time
def test_basic_household_with_simu_params():
    """Single day."""
    path = str(hisim_root / "system_setups/basic_household.py")
    mysimpar = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60 * 60)
    hisim_main.main(path, mysimpar)
    log.information(os.getcwd())


@pytest.mark.extendedbase
@utils.measure_execution_time
def test_basic_household_without_simu_params():
    """No simulation params given. HiSim is often called this way."""
    path = str(hisim_root / "system_setups/basic_household.py")
    mysimpar = None
    hisim_main.main(path, mysimpar)
    log.information(os.getcwd())
