import os

from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim.postprocessingoptions import PostProcessingOptions
from hisim import utils
import pytest

@pytest.mark.examples
@utils.measure_execution_time
def test_first_example():
    """ Performes a simple test for the first example. """
    path = "../examples/simple_examples.py"
    func = "first_example"
    mysimpar = SimulationParameters.one_day_only_with_only_plots(year=2019, seconds_per_timestep=60)
    hisim_main.main(path, func, mysimpar)

@pytest.mark.examples
@utils.measure_execution_time
def test_second_example():
    path = "../examples/simple_examples.py"
    func = "second_example"
    mysimpar = SimulationParameters.one_day_only_with_only_plots(year=2019, seconds_per_timestep=60)
    hisim_main.main(path, func, mysimpar)
