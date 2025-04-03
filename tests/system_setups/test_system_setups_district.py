""" Tests for the cluster system setups.

These system setups can only be tested on cluster because so far they need access to a certain cluster directory.
"""
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
def test_district():
    """Single day."""
    path = "../system_setups/district_system_setup/simple_district.py"

    my_simulation_parameters = SimulationParameters.one_week_only(year=2021, seconds_per_timestep=60)

    my_simulation_parameters.multiple_buildings = True

    my_simulation_parameters.post_processing_options.append(
        PostProcessingOptions.PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION
    )

    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_KPIS)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.WRITE_KPIS_TO_JSON)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.OPEN_DIRECTORY_IN_EXPLORER)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.MAKE_NETWORK_CHARTS)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.WRITE_COMPONENT_CONFIGS_TO_JSON)

    hisim_main.main(path, my_simulation_parameters)
    log.information(os.getcwd())
