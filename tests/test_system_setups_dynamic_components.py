"""Test for system setup dynamic component."""

import os
import pytest

from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils


@pytest.mark.extendedbase
@utils.measure_execution_time
def test_dynamic_components_system_setup() -> None:
    """Test dynamic components system setup."""

    dynamic_components_setup_path = "../system_setups/dynamic_components.py"
    mysimpar = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    hisim_main.main(dynamic_components_setup_path, mysimpar)
    log.information(os.getcwd())
