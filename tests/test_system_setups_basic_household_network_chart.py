""" Tests for the basic household system setup. """
# clean
from pathlib import Path

import pytest

from hisim import hisim_main
from hisim import utils
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.simulationparameters import SimulationParameters


@pytest.mark.system_setups
@utils.measure_execution_time
def test_basic_household_network_chart() -> None:
    """Verify that MAKE_NETWORK_CHARTS produces network chart files.

    Runs a one-day simulation of the basic household setup with only
    PostProcessingOptions.MAKE_NETWORK_CHARTS enabled and asserts that the
    expected Graphviz-rendered system network chart PNGs are written to the
    result directory.  Without these assertions the test would only check that
    nothing raised.
    """
    path = "../system_setups/basic_household.py"
    simulation_parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    simulation_parameters.post_processing_options.append(PostProcessingOptions.MAKE_NETWORK_CHARTS)
    result_directory = hisim_main.main(path, simulation_parameters)

    # hisim_main.main returns the absolute path of the directory the simulation
    # wrote its results to -- the authoritative location, preferred over
    # re-querying the ResultPathProviderSingleton.
    results_dir = Path(result_directory)
    assert results_dir.is_dir(), f"result directory does not exist: {results_dir}"

    # MAKE_NETWORK_CHARTS renders one PNG per chart variant via Graphviz.  The
    # "and_results" variant additionally requires cumulative results (only
    # computed when a monthly/cumulative post-processing option is also enabled),
    # so with MAKE_NETWORK_CHARTS alone the three baseline charts are the
    # expected output.
    for prefix in (
        "System_no_Edge_with_class_labels",
        "System_no_Edge_labels",
        "System_with_Edge_labels",
    ):
        matches = list(results_dir.glob(f"{prefix}*"))
        assert matches, (
            f"No network chart file matching '{prefix}*' was generated in {results_dir}"
        )
