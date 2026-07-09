"""Test for the basic_household_only_heating system setup.

This module contains a single integration test that loads the
basic_household_only_heating.py setup and verifies it runs without
errors for a one-day simulation and produces result files.
"""

import os
import pytest
from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils


@pytest.mark.system_setups
@utils.measure_execution_time
def test_basic_household_only_heating() -> None:
    """Run the basic household only heating system setup for one day.

    Loads the system setup from ../system_setups/basic_household_only_heating.py
    and executes a one-day simulation with 60-second timesteps to verify
    the setup initializes and runs without errors and writes its result
    artefacts to the configured result directory.

    ``hisim_main.main`` returns the directory the simulator actually wrote to,
    which ``Simulator.prepare_simulation_directory`` records on the
    ``SimulationParameters`` instance (configuring the
    ``ResultPathProviderSingleton`` as a side effect when no directory was
    pre-set). Asserting on that returned value keeps the test coupled to the
    value the function under test produces, rather than to the singleton's
    mutable global state.

    ``Simulator.run_all_timesteps`` only writes the ``finished.flag`` marker
    after every timestep and the post-processing have completed successfully,
    so checking for it (and a non-empty result directory) distinguishes a real
    run from a silent no-op.
    """
    path = "../system_setups/basic_household_only_heating.py"

    sim_params = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    result_directory = hisim_main.main(path, sim_params)
    log.information(os.getcwd())

    # The run must have produced outputs. finished.flag is written by
    # Simulator.run_all_timesteps once the simulation and post-processing have
    # finished, so its presence confirms a completed (not no-op) run.
    assert result_directory, "no result directory was configured for the run"
    assert os.path.isdir(result_directory), f"result directory does not exist: {result_directory}"
    assert os.listdir(result_directory), f"result directory is empty: {result_directory}"
    assert os.path.isfile(os.path.join(result_directory, "finished.flag")), (
        f"finished.flag not found in result directory: {result_directory}"
    )
