"""Test for the basic_household_only_heating system setup.

This module contains a single integration test that loads the
basic_household_only_heating.py setup and verifies it runs without
errors for a one-day simulation.
"""

import os
import pytest
from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils


@pytest.mark.system_setups
@utils.measure_execution_time
def test_basic_household_only_heating():
    """Run the basic household only heating system setup for one day.

    Loads the system setup from ../system_setups/basic_household_only_heating.py
    and executes a one-day simulation with 60-second timesteps to verify
    the setup initializes and runs without errors.
    """
    path = "../system_setups/basic_household_only_heating.py"

    mysimpar = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    hisim_main.main(path, mysimpar)
    log.information(os.getcwd())
