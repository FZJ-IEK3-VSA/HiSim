"""Test for simple system setups."""

import pytest

from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import utils


@pytest.mark.system_setups
@utils.measure_execution_time
def test_first_system_setup():
    """Performes a simple test for the first system setup."""
    path = "../system_setups/simple_system_setup_one.py"

    mysimpar = SimulationParameters.one_day_only_with_only_plots(
        year=2021, seconds_per_timestep=60
    )
    hisim_main.main(path, mysimpar)


@pytest.mark.system_setups
@utils.measure_execution_time
def test_second_system_setup():
    """Test second system setup."""
    path = "../system_setups/simple_system_setup_two.py"

    mysimpar = SimulationParameters.one_day_only_with_only_plots(
        year=2021, seconds_per_timestep=60
    )
    hisim_main.main(path, mysimpar)
