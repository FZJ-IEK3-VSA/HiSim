""" Tests for the basic household system setup. """
# clean
import os
from pathlib import Path
import pytest

from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.result_path_provider import ResultPathProviderSingleton


# @pytest.mark.system_setups
@pytest.mark.utsp
@utils.measure_execution_time
def test_basic_household() -> None:
    """Test the household_1_advanced_hp_diesel_car system setup for a single day.

    Runs a one-day simulation of the advanced heat pump + diesel car household
    configuration and verifies that the simulation completes without error and
    actually produces tangible output in the results directory.

    ``hisim_main.main`` returns ``None`` and only raises on failure, so the implicit
    "does not crash" contract is not enough on its own: a run that silently produces
    no artifacts would still pass. The simulator records the result directory it used
    on the :class:`ResultPathProviderSingleton` and writes a ``finished.flag`` marker
    at the end of post-processing, so we assert on both to catch silent no-ops.
    """

    config_filename = "household_1_advanced_hp_diesel_car_config.json"
    Path(config_filename).unlink(missing_ok=True)

    path = "../system_setups/household_1_advanced_hp_diesel_car.py"
    mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
    mysimpar.post_processing_options.append(PostProcessingOptions.MAKE_NETWORK_CHARTS)
    hisim_main.main(path, mysimpar)
    log.information(os.getcwd())

    # hisim_main.main returns None, so confirm the run produced tangible output
    # rather than silently no-op'ing. The simulator stores the result directory it
    # used on the ResultPathProviderSingleton; a completed run leaves a non-empty
    # directory containing the "finished.flag" written at the end of post-processing.
    results_dir = ResultPathProviderSingleton().get_result_directory_name()
    assert results_dir is not None, "ResultPathProvider did not report a result directory."
    results_path = Path(results_dir)
    assert results_path.is_dir(), f"Results directory was not created: {results_path}"
    assert any(results_path.iterdir()), f"Results directory is empty: {results_path}"
    assert (results_path / "finished.flag").is_file(), (
        f"Simulation did not write its completion marker (finished.flag) in {results_path}."
    )
