import os
import pytest
from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils

@pytest.mark.examples
@utils.measure_execution_time
def test_basic_household_with_default_connections():
    path = "../examples/basic_household_only_heating.py"
    func = "basic_household_only_heating"
    mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
    hisim_main.main(path, func,mysimpar )
    log.information(os.getcwd())
