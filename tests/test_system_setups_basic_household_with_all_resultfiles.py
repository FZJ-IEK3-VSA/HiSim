""" Tests for the basic household system setup. """
# clean
import os
import pytest

from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils
from hisim.postprocessingoptions import PostProcessingOptions


@pytest.mark.system_setups
@utils.measure_execution_time
def test_basic_household_with_all_resultfiles():
    """One day with all options."""
    path = "../system_setups/basic_household.py"

    mysimpar = SimulationParameters.one_day_only(
        year=2021, seconds_per_timestep=60
    )
    mysimpar.post_processing_options.append(PostProcessingOptions.GENERATE_CSV_FOR_HOUSING_DATA_BASE)


    hisim_main.main(path, mysimpar)
    log.information(os.getcwd())
