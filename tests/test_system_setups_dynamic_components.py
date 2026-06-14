"""Test for system setup dynamic component."""

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
def test_dynamic_components_system_setup():
    """Test dynamic components system setup."""
    path = str(hisim_root / "system_setups/dynamic_components.py")
    mysimpar = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    hisim_main.main(path, mysimpar)
    log.information(os.getcwd())
