""" Tests for the basic household system setup. """
# clean
import os
from pathlib import Path

import pytest

from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils


@pytest.mark.system_setups
@utils.measure_execution_time
def test_basic_household() -> None:
    """Test basic household system setup for a single day simulation.

    Runs a simulation of the basic household configuration for one day
    using 60-second timesteps and verifies the simulation completes
    without errors by checking that the result directory was created and
    that the ``finished.flag`` marker file was written at the end of the
    run.
    """
    path = "../system_setups/basic_household.py"
    mysimpar = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    hisim_main.main(path, mysimpar)
    log.information(os.getcwd())

    # The simulator populates ``result_directory`` while preparing the run and
    # writes a ``finished.flag`` file once all timesteps and post-processing are
    # complete. Asserting on both confirms the run actually produced outputs
    # rather than merely failing to raise.
    results_dir = Path(mysimpar.result_directory)
    assert results_dir.is_dir(), f"Result directory was not created: {results_dir}"
    assert (results_dir / "finished.flag").is_file(), (
        f"finished.flag was not written to {results_dir}"
    )
