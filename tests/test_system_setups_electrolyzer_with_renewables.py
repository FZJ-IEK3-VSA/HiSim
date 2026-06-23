""" Tests for the electrolyzer with renewables system setup. """

import os
import pytest

from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils


@pytest.mark.system_setups
@utils.measure_execution_time
def test_electrolyzer_with_renewables() -> None:
    """Test the electrolyzer with renewables system setup for a single day.
    
    Runs the system setup defined in ../system_setups/electrolyzer_with_renewables.py
    using one-day simulation parameters (year=2021, 60 seconds per timestep) and
    verifies that the simulation completes without errors.
    """
    path = "../system_setups/electrolyzer_with_renewables.py"
    mysimpar = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    hisim_main.main(path, mysimpar)
    log.information(os.getcwd())
