import os

from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils
import pytest

@pytest.mark.examples
@utils.measure_execution_time
def test_basic_household_with_default_connections():
    path = "../examples/default_connections.py"
    func = "basic_household_with_default_connections"
    mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
    hisim_main.main(path, func, mysimpar)
    log.information(os.getcwd())
