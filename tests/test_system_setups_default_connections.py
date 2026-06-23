"""Test for system setup default connections."""

import os
import pytest
from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils


@pytest.mark.system_setups
@utils.measure_execution_time
def test_basic_household_with_default_connections() -> None:
    """Test that the basic household system setup runs with default component connections.
    
    Verifies that the system setup defined in `../system_setups/default_connections.py`
    executes successfully using default connection configurations for a one-day simulation
    with 60-second timesteps.
    """
    path = "../system_setups/default_connections.py"

    mysimpar = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    hisim_main.main(path, mysimpar)
    log.information(os.getcwd())
