"""Test for system setup automatic default connections."""

import os
from pathlib import Path

import pytest
from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils


@pytest.mark.system_setups
@utils.measure_execution_time
def test_basic_household_with_default_connections() -> None:
    """Test basic household with automatic default connections.

    Runs a one-day simulation of the automatic default connections household
    configuration and verifies that the simulation actually completed and wrote
    output artifacts, rather than merely failing to raise. The simulator
    populates ``result_directory`` while preparing the run and writes a
    ``finished.flag`` file once all timesteps and post-processing are complete,
    so asserting on both confirms the run produced a valid result.
    """
    path = "../system_setups/automatic_default_connections.py"

    simulation_parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    hisim_main.main(path, simulation_parameters)
    log.information(os.getcwd())

    # The simulator populates ``result_directory`` while preparing the run and
    # writes a ``finished.flag`` file once all timesteps and post-processing are
    # complete. Asserting on both confirms the run actually produced outputs
    # rather than merely failing to raise.
    results_dir = Path(simulation_parameters.result_directory)
    assert results_dir.is_dir(), f"Result directory was not created: {results_dir}"
    assert (results_dir / "finished.flag").is_file(), (
        f"finished.flag was not written to {results_dir}"
    )
    assert any(results_dir.iterdir()), f"Results directory is empty: {results_dir}"
