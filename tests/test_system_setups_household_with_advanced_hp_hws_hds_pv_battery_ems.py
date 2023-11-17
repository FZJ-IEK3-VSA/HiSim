""" Tests for the basic household system setup. """
# clean
import os
import pytest

from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_with_advanced_hp_hws_hds_pv_battery_ems():
    """Single day."""
    path = "../system_setups/household_with_advanced_hp_hws_hds_pv_battery_ems.py"

    mysimpar = SimulationParameters.one_day_only(
        year=2021, seconds_per_timestep=60
    )
    hisim_main.main(path, mysimpar)
    log.information(os.getcwd())
