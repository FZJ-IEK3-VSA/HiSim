"""Test for system setup modular household."""

import os

import pytest

from hisim import utils
from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters


@pytest.mark.system_setups
@utils.measure_execution_time
def test_modular_household_configurations_default() -> None:
    """Tests the modular households.

    Runs a one-day simulation of the modular household setup and verifies that
    the simulation actually produced its expected output artifacts in the result
    directory, rather than only checking that the call did not raise.
    """
    path = "../system_setups/modular_example.py"

    simulation_parameters = SimulationParameters.one_day_only(
        year=2021, seconds_per_timestep=60 * 15
    )
    hisim_main.main(path, simulation_parameters)

    # hisim_main.main runs the full simulation and writes its artifacts into a
    # result directory. The simulator populates ``result_directory`` on the
    # (mutable) SimulationParameters object while preparing the run directory
    # (see Simulator.prepare_simulation_directory), so it is available for
    # inspection after the run.
    result_directory = simulation_parameters.result_directory
    assert result_directory, "Simulation did not set a result directory."
    assert os.path.isdir(result_directory), (
        f"Result directory does not exist: {result_directory!r}"
    )

    # A finished simulation always writes a 'finished.flag' marker file into its
    # result directory (see Simulator.run_all_timesteps).
    finished_flag = os.path.join(result_directory, "finished.flag")
    assert os.path.isfile(finished_flag), (
        f"finished.flag not found in result directory: {result_directory!r}"
    )

    # The result directory must contain non-empty output artifacts (e.g. the log
    # files written by the logger and the finished marker), not just be an empty
    # folder that was created at startup.
    produced_files = [
        os.path.join(directory, filename)
        for directory, _, filenames in os.walk(result_directory)
        for filename in filenames
    ]
    assert produced_files, "No result files were produced in the result directory."
