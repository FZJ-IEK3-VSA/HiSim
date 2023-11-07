"""Test for example modular housheold."""

import pytest

from hisim import utils
from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters


@pytest.mark.system_setups
@utils.measure_execution_time
def test_modular_household_configurations_default():
    """Tests the modular households."""
    path = "../system_setups/modular_example.py"
    func = "modular_household_explicit"
    mysimpar = SimulationParameters.one_day_only(
        year=2021, seconds_per_timestep=60 * 15
    )
    hisim_main.main(path, func, mysimpar)
