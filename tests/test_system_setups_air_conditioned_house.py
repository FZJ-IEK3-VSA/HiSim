"""Test for smart residential cooling."""

# clean

import os
import pytest
from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils


@pytest.mark.mpc
@utils.measure_execution_time
def test_household_with_air_conditioner_and_controller_mpc():
    """The test should check if a normal simulation works with the smart cooling implementation."""

    path = "../system_setups/air_conditioned_house_a_with_mpc_controller.py"

    mysimpar = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)

    mysimpar.mpc_battery_capacity = 5e3
    mysimpar.pv_included = True
    mysimpar.pv_peak_power = 4e3
    mysimpar.smart_devices_included = True
    mysimpar.battery_included = True

    hisim_main.main(path, mysimpar)

    log.information(os.getcwd())


if __name__ == "__main__":
    test_household_with_air_conditioner_and_controller_mpc()
