"""Tests for household_5a: advanced HP, EV, PV, battery with car priority."""
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
    """Run a one-day simulation of the advanced HP/EV/PV/battery system with car priority."""

    config_filename = "household_5_advanced_hp_ev_pv_battery_config.json"
    if Path(config_filename).is_file():
        os.remove(config_filename)

    path = (
        "../system_setups/household_5a_with_car_priority_advanced_hp_ev_pv_battery.py"
    )

    mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
    mysimpar.post_processing_options.append(PostProcessingOptions.MAKE_NETWORK_CHARTS)
    # Use a deterministic result directory so the run's artifacts can be verified.
    mysimpar.result_directory = TestingUtils.get_result_directory()
    shutil.rmtree(mysimpar.result_directory, ignore_errors=True)
    hisim_main.main(path, mysimpar)

    # Verify the simulation ran to completion and produced its output artifacts.
    assert Path(mysimpar.result_directory).joinpath("finished.flag").is_file(), (
        "Simulation did not produce 'finished.flag' in the result directory; "
        "the one-day household_5a run did not complete successfully (post-processing unfinished)."
    )
