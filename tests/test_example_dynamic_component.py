"""Test for example dynamic component."""

import os
import pytest

from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils


@pytest.mark.base
@utils.measure_execution_time
def test_dynamic_components_example():
    """Test dynamic components example."""

    path = "../examples/dynamic_components.py"
    func = "dynamic_components_demonstration"
    mysimpar = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    hisim_main.main(path, func, mysimpar)
    log.information(os.getcwd())
