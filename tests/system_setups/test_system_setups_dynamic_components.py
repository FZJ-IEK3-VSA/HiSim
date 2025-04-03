"""Test for system setup dynamic component."""

import os
import pytest

from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils


@pytest.mark.base
@utils.measure_execution_time
def test_dynamic_components_system_setup():
    """Test dynamic components system setup."""

    path = "../system_setups/dynamic_components.py"
    mysimpar = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    hisim_main.main(path, mysimpar)
    log.information(os.getcwd())
