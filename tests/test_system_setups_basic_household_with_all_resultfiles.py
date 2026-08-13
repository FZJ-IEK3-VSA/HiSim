""" Tests for the basic household system setup. """
# clean
import os
from pathlib import Path

import pytest

from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import utils
from hisim.postprocessingoptions import PostProcessingOptions


@pytest.mark.system_setups
@utils.measure_execution_time
def test_basic_household_with_all_resultfiles() -> None:
    """One day with representative result-file generating options.

    Runs a one-day simulation of the basic household setup with a broad set of
    post-processing options enabled (CSV/PKL export, PDF report, JSON exports
    for the webtool / KPIs / configs, scenario-evaluation output) and verifies
    that the enabled options actually produce their expected result files.
    Without these assertions the test would only check that nothing raised.
    """
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

    # main() returns the absolute path of the directory the simulation wrote
    # its results to -- recorded on SimulationParameters.result_directory by
    # Simulator.prepare_simulation_directory() during the run. Depending on this
    # return value (the contract exposed by the system under test) instead of
    # re-querying the mutable ResultPathProviderSingleton keeps the test coupled
    # to main's interface rather than to shared global state.
    result_directory = hisim_main.main(path, mysimpar)
    assert result_directory, f"main() returned an empty result directory for {path}"
    results_dir = Path(result_directory)
    assert results_dir.is_dir(), f"result directory does not exist: {results_dir}"
    assert os.listdir(result_directory), f"result directory is empty: {results_dir}"

    # finished.flag is written by Simulator.run_all_timesteps once the simulation
    # and post-processing have finished, so its presence confirms a completed run.
    assert (results_dir / "finished.flag").is_file(), (
        f"finished.flag not found in result directory: {results_dir}"
    )

    # --- CSV exports --------------------------------------------------------
    # EXPORT_TO_CSV + EXPORT_RESULTS_IN_ONE_FILE produce a single all_results.csv.
    assert any(results_dir.rglob("*.csv")), "no CSV result files produced"
    assert (results_dir / "all_results.csv").is_file(), (
        f"all_results.csv not found in result directory: {results_dir}"
    )

    # --- PKL exports --------------------------------------------------------
    # EXPORT_TO_PKL + EXPORT_RESULTS_IN_ONE_FILE produce a single all_results.pkl.
    assert any(results_dir.rglob("*.pkl")), "no PKL result files produced"
    assert (results_dir / "all_results.pkl").is_file(), (
        f"all_results.pkl not found in result directory: {results_dir}"
    )

    # --- PDF report ---------------------------------------------------------
    # GENERATE_PDF_REPORT produces report.pdf in the result directory.
    assert any(results_dir.rglob("*.pdf")), "no PDF report produced"
    assert (results_dir / "report.pdf").is_file(), (
        f"report.pdf not found in result directory: {results_dir}"
    )

    # --- JSON outputs -------------------------------------------------------
    # WRITE_COMPONENT_CONFIGS_TO_JSON    -> simulation.json, scenario.json
    # WRITE_KPIS_TO_JSON                 -> all_kpis.json
    # WRITE_KPIS_TO_JSON_FOR_BUILDING_SIZER -> <building>_kpi_config_for_building_sizer.json
    assert any(results_dir.rglob("*.json")), "no JSON result files produced"

    assert (results_dir / "simulation.json").is_file(), (
        f"simulation.json not found in result directory: {results_dir}"
    )
    assert (results_dir / "scenario.json").is_file(), (
        f"scenario.json not found in result directory: {results_dir}"
    )
    assert (results_dir / "all_kpis.json").is_file(), (
        f"all_kpis.json not found in result directory: {results_dir}"
    )
    # The building-sizer KPI config filename includes the building name, so use a glob.
    assert any(results_dir.glob("*_kpi_config_for_building_sizer.json")), (
        f"no *_kpi_config_for_building_sizer.json found in result directory: {results_dir}"
    )

    # --- Scenario-evaluation output -----------------------------------------
    # PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION creates a sub-directory with
    # resolution CSVs (hourly/daily/monthly/yearly) and, together with
    # WRITE_CONFIGS_FOR_SCENARIO_EVALUATION_TO_JSON, also writes simulation.json
    # and scenario.json into it.
    scenario_eval_dir = results_dir / "result_data_for_scenario_evaluation"
    assert scenario_eval_dir.is_dir(), (
        f"scenario-evaluation output directory was not created at {scenario_eval_dir}"
    )
    assert any(scenario_eval_dir.rglob("*.csv")), "no CSV files in scenario-evaluation output directory"
    assert (scenario_eval_dir / "simulation.json").is_file(), (
        f"simulation.json not found in scenario-evaluation directory: {scenario_eval_dir}"
    )
    assert (scenario_eval_dir / "scenario.json").is_file(), (
        f"scenario.json not found in scenario-evaluation directory: {scenario_eval_dir}"
    )
