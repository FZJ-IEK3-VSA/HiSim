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
def test_household_gas():
    """Single day."""
    path = "../system_setups/household_gas_building_sizer.py"

    # Set simu parameters for tests
    my_simulation_parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60 * 15)
    my_simulation_parameters.post_processing_options.append(
        PostProcessingOptions.PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION
    )
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_OPEX)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_CAPEX)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_KPIS)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.WRITE_KPIS_TO_JSON)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.OPEN_DIRECTORY_IN_EXPLORER)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.MAKE_NETWORK_CHARTS)

    hisim_main.main(path, my_simulation_parameters)
    log.information(os.getcwd())


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_oil():
    """Single day."""
    path = "../system_setups/household_oil_building_sizer.py"

    # Set simu parameters for tests
    my_simulation_parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60 * 15)
    my_simulation_parameters.post_processing_options.append(
        PostProcessingOptions.PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION
    )
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_OPEX)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_CAPEX)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_KPIS)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.WRITE_KPIS_TO_JSON)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.OPEN_DIRECTORY_IN_EXPLORER)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.MAKE_NETWORK_CHARTS)

    hisim_main.main(path, my_simulation_parameters)
    log.information(os.getcwd())


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_heatpump():
    """Single day."""
    path = "../system_setups/household_heatpump_building_sizer.py"

    # Set simu parameters for tests
    my_simulation_parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60 * 15)
    my_simulation_parameters.post_processing_options.append(
        PostProcessingOptions.PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION
    )
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_OPEX)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_CAPEX)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_KPIS)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.WRITE_KPIS_TO_JSON)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.OPEN_DIRECTORY_IN_EXPLORER)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.MAKE_NETWORK_CHARTS)

    hisim_main.main(path, my_simulation_parameters)
    log.information(os.getcwd())


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_pellet_heating():
    """Single day."""
    path = "../system_setups/household_pellets_building_sizer.py"

    # Set simu parameters for tests
    my_simulation_parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60 * 15)
    my_simulation_parameters.post_processing_options.append(
        PostProcessingOptions.PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION
    )
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_OPEX)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_CAPEX)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_KPIS)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.WRITE_KPIS_TO_JSON)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.OPEN_DIRECTORY_IN_EXPLORER)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.MAKE_NETWORK_CHARTS)

    hisim_main.main(path, my_simulation_parameters)
    log.information(os.getcwd())


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_district_heating():
    """Single day."""
    path = "../system_setups/household_district_heating_building_sizer.py"

    # Set simu parameters for tests
    my_simulation_parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60 * 15)
    my_simulation_parameters.post_processing_options.append(
        PostProcessingOptions.PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION
    )
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_OPEX)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_CAPEX)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_KPIS)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.WRITE_KPIS_TO_JSON)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.OPEN_DIRECTORY_IN_EXPLORER)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.MAKE_NETWORK_CHARTS)

    hisim_main.main(path, my_simulation_parameters)
    log.information(os.getcwd())
