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
    """Test the household_1_advanced_hp_diesel_car system setup for a single day.

    Runs a one-day simulation of the advanced heat pump + diesel car household
    configuration and verifies that the simulation completes without error.
    """

    config_filename = "household_1_advanced_hp_diesel_car_config.json"
    Path(config_filename).unlink(missing_ok=True)

    path = "../system_setups/household_1_advanced_hp_diesel_car.py"
    mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
    mysimpar.post_processing_options.append(PostProcessingOptions.MAKE_NETWORK_CHARTS)
    hisim_main.main(path, mysimpar)
    log.information(os.getcwd())
