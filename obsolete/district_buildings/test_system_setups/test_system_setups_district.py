""" Tests for the cluster system setups.

These system setups can only be tested on cluster because so far they need access to a certain cluster directory.
"""
# clean
import os
import pathlib
import pytest

from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils
from hisim.postprocessingoptions import PostProcessingOptions


@pytest.mark.system_setups
@utils.measure_execution_time
def test_district() -> None:
    """Run the simple district system setup for one day and verify post-processing outputs.

    Executes ``../system_setups/district_system_setup/simple_district.py`` via
    ``hisim_main.main`` with one-day simulation parameters (2021, 15-minute
    time steps) and ``multiple_buildings`` enabled.  Several post-processing
    options are turned on (KPI computation, KPI JSON, network charts, component
    config JSON, scenario-evaluation preparation) and the test asserts that the
    expected artefacts are written to the result directory:
    ``all_kpis.json``, ``simulation.json``, ``scenario.json``, and the
    ``result_data_for_scenario_evaluation`` subfolder with CSV files.

    Raises:
        AssertionError: If any expected result file or directory is missing
            after the simulation completes.
    """
    path: str = "../system_setups/district_system_setup/simple_district.py"

    my_simulation_parameters: SimulationParameters = SimulationParameters.one_day_only(
        year=2021, seconds_per_timestep=60 * 15
    )

    my_simulation_parameters.multiple_buildings = True

    my_simulation_parameters.post_processing_options.append(
        PostProcessingOptions.PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION
    )

    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_KPIS)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.WRITE_KPIS_TO_JSON)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.MAKE_NETWORK_CHARTS)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.WRITE_COMPONENT_CONFIGS_TO_JSON)

    hisim_main.main(path, my_simulation_parameters)
    log.information(os.getcwd())

    # The simulation configures its own result directory during the run
    # (Simulator.prepare_simulation_directory writes it back onto the same
    # SimulationParameters instance passed in here), so read it back from there
    # rather than guessing a path.
    results_dir = pathlib.Path(my_simulation_parameters.result_directory)
    assert results_dir.is_dir(), (
        f"results directory was not created at {results_dir!r}"
    )

    # WRITE_KPIS_TO_JSON (together with COMPUTE_KPIS) writes "all_kpis.json"
    # directly into the result directory.
    kpi_json = results_dir / "all_kpis.json"
    assert kpi_json.is_file(), (
        f"WRITE_KPIS_TO_JSON did not produce {kpi_json!r}"
    )

    # WRITE_COMPONENT_CONFIGS_TO_JSON writes "simulation.json" and "scenario.json"
    # directly into the result directory.
    simulation_json = results_dir / "simulation.json"
    assert simulation_json.is_file(), (
        f"WRITE_COMPONENT_CONFIGS_TO_JSON did not produce {simulation_json!r}"
    )
    scenario_json = results_dir / "scenario.json"
    assert scenario_json.is_file(), (
        f"WRITE_COMPONENT_CONFIGS_TO_JSON did not produce {scenario_json!r}"
    )

    # PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION creates a dedicated subfolder and
    # writes the aggregated CSV results (hourly/daily/monthly/yearly) into it.
    scenario_eval_dir = results_dir / "result_data_for_scenario_evaluation"
    assert scenario_eval_dir.is_dir(), (
        f"PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION did not create {scenario_eval_dir!r}"
    )
    scenario_eval_csvs = list(scenario_eval_dir.glob("*.csv"))
    assert scenario_eval_csvs, (
        f"no scenario-evaluation CSV outputs were written in {scenario_eval_dir!r}"
    )

    # MAKE_NETWORK_CHARTS renders the system flow charts (via Graphviz/pydot) as
    # PNG files whose names start with "System_" directly into the result
    # directory (see SystemChart.make_chart in hisim.postprocessing.system_chart).
    # Temporarily disabled: network charts depend on Graphviz/pydot being
    # available in the test environment, which is not reliably the case.
    # network_charts = list(results_dir.glob("System_*.png"))
    # assert network_charts, (
    #     f"MAKE_NETWORK_CHARTS did not produce any System_*.png network chart in {results_dir!r}"
    # )
