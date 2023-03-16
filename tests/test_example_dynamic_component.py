import os

from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim.postprocessingoptions import PostProcessingOptions
from hisim import utils
import pytest

@pytest.mark.base
@utils.measure_execution_time
def test_dynamic_components_example():
    path = "../examples/dynamic_components.py"
    func = "dynamic_components_demonstration"
    mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
    hisim_main.main(path, func,mysimpar )
    log.information(os.getcwd())
