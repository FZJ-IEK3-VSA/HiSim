""" Tests for the basic household system setup. """
# clean
import os
from pathlib import Path
import pytest

from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils


# @pytest.mark.system_setups
@pytest.mark.utsp
@utils.measure_execution_time
def test_basic_household():
    """Single day."""

    config_filename = "household_1e_advanced_hp_hp_storage_config.json"
    if Path(config_filename).is_file():
        os.remove(config_filename)

    path = "../system_setups/household_1e_more_advanced_dhw_hp_storage.py"
    mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
    hisim_main.main(path, mysimpar)
    log.information(os.getcwd())
