""" Tests for the basic household system setup. """
# clean
import os
import shutil
from pathlib import Path
import pytest

from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils
from hisim.postprocessingoptions import PostProcessingOptions
from tests.testing_utils import TestingUtils


# @pytest.mark.system_setups
@pytest.mark.utsp
@utils.measure_execution_time
def test_basic_household() -> None:
    """Test the advanced household setup with heat pump, diesel car, PV, and battery for a single day.

    Runs a one-day simulation using the household_3_advanced_hp_diesel_car_pv_battery
    configuration and generates network charts as post-processing output. The simulator
    writes a ``finished.flag`` marker into the result directory once post-processing
    completes; asserting on that marker and on the presence of concrete output files
    turns this from a "does not crash" smoke test into one that verifies the run
    actually finished and produced results.
    """

    path = "../system_setups/household_3_advanced_hp_diesel_car_pv_battery.py"
    simulation_parameters = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
    simulation_parameters.post_processing_options.append(PostProcessingOptions.MAKE_NETWORK_CHARTS)

    # Use a dedicated, clean result directory so the run's completion can be verified.
    # An explicit test_name avoids collisions with the many sibling utsp tests that
    # also define a function named ``test_basic_household``.
    result_directory = TestingUtils.get_result_directory(
        test_name="household_3_advanced_hp_diesel_car_pv_battery"
    )
    simulation_parameters.result_directory = result_directory
    shutil.rmtree(result_directory, ignore_errors=True)

    hisim_main.main(path, simulation_parameters)
    log.information(os.getcwd())

    # hisim_main.main returns None; the simulator writes concrete artifacts into the
    # result directory during the run. Asserting on the directory, the always-written
    # simulation log, and the ``finished.flag`` completion marker confirms the full
    # simulation pipeline ran to completion and produced output rather than merely
    # failing to raise.
    #
    # Note: CSV/JSON exports are gated behind post-processing options (EXPORT_TO_CSV,
    # WRITE_KPIS_TO_JSON, ...) that SimulationParameters.one_day_only does not enable,
    # so they cannot be asserted on here. The simulation log (hisim_simulation.log) is
    # always written by log.logger.setup at the start of run_all_timesteps, and
    # finished.flag is written at the very end after post-processing completes.
    results_path = Path(simulation_parameters.result_directory)
    assert results_path.is_dir(), f"Results directory was not created: {results_path}"
    assert (results_path / "hisim_simulation.log").is_file(), (
        f"hisim_simulation.log missing in results directory: {results_path}"
    )
    assert (results_path / "finished.flag").is_file(), (
        f"Simulation did not write its completion marker (finished.flag) in {results_path}."
    )

    shutil.rmtree(result_directory, ignore_errors=True)
