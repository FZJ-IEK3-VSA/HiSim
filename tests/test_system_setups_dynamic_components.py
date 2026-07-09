"""Test for system setup dynamic component."""

import os
import pytest

from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import utils
from hisim.result_path_provider import ResultPathProviderSingleton


@pytest.mark.extendedbase
@utils.measure_execution_time
def test_dynamic_components_system_setup() -> None:
    """Test dynamic components system setup.

    Runs the dynamic-components setup for a single day and verifies that the
    simulation runs to completion and writes its outputs to the result directory
    selected by the ResultPathProvider.
    """

    dynamic_components_setup_path = "../system_setups/dynamic_components.py"
    dynamic_components_setup_path = "../system_setups/dynamic_components.py"
    mysimpar = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    hisim_main.main(dynamic_components_setup_path, mysimpar)

    # hisim_main.main runs the full simulation. The simulator writes its outputs into
    # the directory provided by the ResultPathProvider (see Simulator.prepare_simulation_directory).
    # Assert that a result directory was actually created and that the run produced
    # outputs there, instead of only checking that nothing raised.
    result_directory = ResultPathProviderSingleton().get_result_directory_name()
    assert result_directory is not None, (
        "ResultPathProvider did not provide a result directory after the simulation."
    )
    assert os.path.isdir(result_directory), (
        f"Result directory was not created: {result_directory}"
    )
    assert os.listdir(result_directory), (
        f"Result directory is empty: {result_directory}"
    )
    # `finished.flag` is written at the very end of a successful simulation run
    # (see Simulator.run_all_timesteps), so it is a reliable signal that the
    # dynamic-components setup ran to completion.
    finished_flag = os.path.join(result_directory, "finished.flag")
    assert os.path.isfile(finished_flag), (
        f"Simulation did not write the completion flag: {finished_flag}"
    )
