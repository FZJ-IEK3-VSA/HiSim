""" Tests for the basic household system setup. """
# clean
import pytest

from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import utils
from hisim.postprocessingoptions import PostProcessingOptions


@pytest.mark.system_setups
@utils.measure_execution_time
def test_basic_household_with_all_resultfiles() -> None:
    """One day with representative result-file generating options."""
    path = "../system_setups/basic_household.py"

    mysimpar = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60 * 15)
    mysimpar.post_processing_options.extend(
        [
            PostProcessingOptions.EXPORT_TO_CSV,
            PostProcessingOptions.EXPORT_TO_PKL,
            PostProcessingOptions.EXPORT_RESULTS_IN_ONE_FILE,
            PostProcessingOptions.EXPORT_MONTHLY_RESULTS,
            PostProcessingOptions.MAKE_NETWORK_CHARTS,
            PostProcessingOptions.GENERATE_PDF_REPORT,
            PostProcessingOptions.WRITE_COMPONENTS_TO_REPORT,
            PostProcessingOptions.WRITE_ALL_OUTPUTS_TO_REPORT,
            PostProcessingOptions.WRITE_NETWORK_CHARTS_TO_REPORT,
            PostProcessingOptions.INCLUDE_CONFIGS_IN_PDF_REPORT,
            PostProcessingOptions.COMPUTE_OPEX,
            PostProcessingOptions.COMPUTE_CAPEX,
            PostProcessingOptions.COMPUTE_KPIS,
            PostProcessingOptions.PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION,
            PostProcessingOptions.WRITE_COMPONENT_CONFIGS_TO_JSON,
            PostProcessingOptions.WRITE_CONFIGS_FOR_SCENARIO_EVALUATION_TO_JSON,
            PostProcessingOptions.WRITE_KPIS_TO_JSON_FOR_BUILDING_SIZER,
            PostProcessingOptions.WRITE_KPIS_TO_JSON,
        ]
    )

    hisim_main.main(path, mysimpar)
