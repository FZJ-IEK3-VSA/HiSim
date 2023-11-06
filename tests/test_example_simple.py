"""Test for simple examples."""

import pytest

from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import utils


@pytest.mark.examples
@utils.measure_execution_time
def test_first_example():
    """Performes a simple test for the first example."""
    path = "../examples/simple_examples.py"
    func = "first_example"
    mysimpar = SimulationParameters.one_day_only_with_only_plots(
        year=2021, seconds_per_timestep=60
    )
    hisim_main.main(path, func, mysimpar)


@pytest.mark.examples
@utils.measure_execution_time
def test_second_example():
    """Test second example."""
    path = "../examples/simple_examples.py"
    func = "second_example"
    mysimpar = SimulationParameters.one_day_only_with_only_plots(
        year=2021, seconds_per_timestep=60
    )
    hisim_main.main(path, func, mysimpar)
