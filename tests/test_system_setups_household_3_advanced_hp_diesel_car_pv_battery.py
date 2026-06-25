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
def test_basic_household():
    """Test the advanced household setup with heat pump, diesel car, PV, and battery for a single day.
    
    Runs a one-day simulation using the household_3_advanced_hp_diesel_car_pv_battery
    configuration and generates network charts as post-processing output.
    """

    Runs a one-day simulation using the household_3_advanced_hp_diesel_car_pv_battery
    configuration and generates network charts as post-processing output. The simulator
    writes a ``finished.flag`` marker into the result directory once post-processing
    completes; asserting on that marker turns this from a "does not crash" smoke test
    into one that verifies the run actually finished.
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

    # The simulator writes "finished.flag" after post-processing completes; its presence
    # confirms the simulation ran to completion instead of merely not crashing.
    assert Path(result_directory).joinpath("finished.flag").is_file(), (
        "Simulation did not produce a 'finished.flag' marker in the result directory; "
        "the household_3_advanced_hp_diesel_car_pv_battery setup likely raised before "
        "completing post-processing."
    )

    shutil.rmtree(result_directory, ignore_errors=True)
