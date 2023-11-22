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


# @pytest.mark.system_setups
@pytest.mark.utsp
@utils.measure_execution_time
def test_basic_household():
    """Single day."""

    config_filename = "household_4_advanced_hp_ev_pv_config.json"
    if Path(config_filename).is_file():
        os.remove(config_filename)

    path = "../system_setups/household_4a_with_car_priority_advanced_hp_ev_pv.py"

    mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
    mysimpar.post_processing_options.append(PostProcessingOptions.MAKE_NETWORK_CHARTS)
    hisim_main.main(path, mysimpar)
    log.information(os.getcwd())
