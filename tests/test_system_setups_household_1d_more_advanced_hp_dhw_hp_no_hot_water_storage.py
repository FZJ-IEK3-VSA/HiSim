"""Tests for the household_1d advanced heat-pump setup (DHW HP, no hot-water storage).

Runs a one-day simulation of the
`household_1d_more_advanced_hp_dhw_hp_no_hot_water_storage` system setup through
`hisim_main.main` and asserts that the simulator populates a result directory,
writes a `finished.flag` marker, and emits at least one CSV result file.
"""
# clean
import os
from pathlib import Path
import pytest

from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils
from hisim.postprocessingoptions import PostProcessingOptions


# @pytest.mark.system_setups
@pytest.mark.utsp
@utils.measure_execution_time
def test_basic_household() -> None:
    """Run a one-day simulation of the household_1d_more_advanced_hp_dhw_hp_no_hot_water_storage setup.

    Verifies that the simulation actually completes and writes its result artifacts,
    not just that ``hisim_main.main`` does not raise. The runner sets
    ``result_directory`` on the simulation parameters while preparing the simulation
    directory and writes a ``finished.flag`` marker file at the end of the run
    (after post-processing), so both are meaningful proof that the setup produced
    its expected output. The post-processor additionally exports the simulation
    results to CSV files, so confirming at least one ``.csv`` exists guards against
    a run that silently produces no data.
    """

    config_filename = "household_1d_more_advanced_hp_dhw_hp_no_hot_water_storage.py.json"
    if Path(config_filename).is_file():
        os.remove(config_filename)

    path = "../system_setups/household_1d_more_advanced_hp_dhw_hp_no_hot_water_storage.py"
    mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
    mysimpar.post_processing_options.append(PostProcessingOptions.MAKE_NETWORK_CHARTS)
    hisim_main.main(path, mysimpar)
    log.information(os.getcwd())

    # hisim_main.main runs the simulation on the same SimulationParameters instance,
    # so result_directory is populated by the simulator with the directory it wrote to.
    assert mysimpar.result_directory, "simulation did not set a result directory"
    result_directory = Path(mysimpar.result_directory)
    assert result_directory.is_dir(), (
        f"result directory was not created: {result_directory}"
    )
    assert (result_directory / "finished.flag").is_file(), (
        f"finished.flag was not written to the result directory: {result_directory}"
    )
    assert any(result_directory.glob("*.csv")), (
        f"no CSV result files were written to the result directory: {result_directory}"
    )
