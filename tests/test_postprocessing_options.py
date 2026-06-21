"""Integration tests for individual PostProcessingOptions."""
from __future__ import annotations

import inspect
import sys

import pytest

from hisim.postprocessingoptions import PostProcessingOptions
from tests.postprocessing_option_test_framework import PostProcessingOptionTestFramework


pytestmark = pytest.mark.postprocessingoptions


@pytest.fixture(scope="module")
def postprocessing_option_framework() -> PostProcessingOptionTestFramework:
    """Shared framework that keeps the individual option tests small."""
    return PostProcessingOptionTestFramework()


def test_postprocessing_option_plot_line(postprocessing_option_framework: PostProcessingOptionTestFramework) -> None:
    postprocessing_option_framework.run(PostProcessingOptions.PLOT_LINE, expected_files=["*/*/line.png"])


def test_postprocessing_option_plot_carpet(postprocessing_option_framework: PostProcessingOptionTestFramework) -> None:
    postprocessing_option_framework.run(PostProcessingOptions.PLOT_CARPET, expected_files=["*/*/carpet.png"])


def test_postprocessing_option_plot_sankey(postprocessing_option_framework: PostProcessingOptionTestFramework) -> None:
    postprocessing_option_framework.run(PostProcessingOptions.PLOT_SANKEY, expected_files=["hisim_simulation.log"])


def test_postprocessing_option_plot_single_days(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    postprocessing_option_framework.run(
        PostProcessingOptions.PLOT_SINGLE_DAYS,
        expected_files=["*/*/days_m0_d0.png"],
    )


def test_postprocessing_option_plot_monthly_bar_charts(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    postprocessing_option_framework.run(
        PostProcessingOptions.PLOT_MONTHLY_BAR_CHARTS,
        expected_files=["*/*/bar.png"],
    )


def test_postprocessing_option_open_directory_in_explorer(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    postprocessing_option_framework.run(
        PostProcessingOptions.OPEN_DIRECTORY_IN_EXPLORER,
        expected_files=["hisim_simulation.log"],
    )


def test_postprocessing_option_export_to_csv(postprocessing_option_framework: PostProcessingOptionTestFramework) -> None:
    postprocessing_option_framework.run(PostProcessingOptions.EXPORT_TO_CSV, expected_files=["*.csv"])


def test_postprocessing_option_make_network_charts(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    postprocessing_option_framework.run(
        PostProcessingOptions.MAKE_NETWORK_CHARTS,
        expected_files=["System_*.png"],
    )


def test_postprocessing_option_generate_pdf_report(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    postprocessing_option_framework.run(PostProcessingOptions.GENERATE_PDF_REPORT, expected_files=["report.pdf"])


def test_postprocessing_option_write_components_to_report(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    postprocessing_option_framework.run(
        PostProcessingOptions.WRITE_COMPONENTS_TO_REPORT,
        expected_files=["report.pdf"],
    )


def test_postprocessing_option_write_all_outputs_to_report(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    postprocessing_option_framework.run(
        PostProcessingOptions.WRITE_ALL_OUTPUTS_TO_REPORT,
        expected_files=["report.pdf"],
    )


def test_postprocessing_option_write_network_charts_to_report(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    postprocessing_option_framework.run(
        PostProcessingOptions.WRITE_NETWORK_CHARTS_TO_REPORT,
        expected_files=["System_*.png", "report.pdf"],
    )


def test_postprocessing_option_plot_special_testing_single_day(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    postprocessing_option_framework.run(
        PostProcessingOptions.PLOT_SPECIAL_TESTING_SINGLE_DAY,
        expected_files=["*/*/days_m0_d0.png"],
    )


def test_postprocessing_option_generate_csv_for_housing_data_base(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    postprocessing_option_framework.run(
        PostProcessingOptions.GENERATE_CSV_FOR_HOUSING_DATA_BASE,
        expected_files=[
            "csv_for_housing_data_base_annual_*.csv",
            "csv_for_housing_data_base_seasonal_*.csv",
        ],
    )


def test_postprocessing_option_include_configs_in_pdf_report(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    postprocessing_option_framework.run(
        PostProcessingOptions.INCLUDE_CONFIGS_IN_PDF_REPORT,
        expected_files=["report.pdf"],
    )


def test_postprocessing_option_include_images_in_pdf_report(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    postprocessing_option_framework.run(
        PostProcessingOptions.INCLUDE_IMAGES_IN_PDF_REPORT,
        expected_files=["*/*/line.png", "report.pdf"],
    )


def test_postprocessing_option_provide_detailed_iteration_logging(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    postprocessing_option_framework.run(
        PostProcessingOptions.PROVIDE_DETAILED_ITERATION_LOGGING,
        expected_files=["hisim_simulation.log"],
    )


def test_postprocessing_option_compute_opex(postprocessing_option_framework: PostProcessingOptionTestFramework) -> None:
    postprocessing_option_framework.run(
        PostProcessingOptions.COMPUTE_OPEX,
        expected_files=["operational_costs_co2_footprint.csv"],
    )


def test_postprocessing_option_compute_capex(postprocessing_option_framework: PostProcessingOptionTestFramework) -> None:
    postprocessing_option_framework.run(
        PostProcessingOptions.COMPUTE_CAPEX,
        expected_files=["investment_cost_co2_footprint.csv"],
    )


def test_postprocessing_option_compute_kpis(postprocessing_option_framework: PostProcessingOptionTestFramework) -> None:
    postprocessing_option_framework.run(PostProcessingOptions.COMPUTE_KPIS, expected_files=["hisim_simulation.log"])


def test_postprocessing_option_prepare_outputs_for_scenario_evaluation(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    postprocessing_option_framework.run(
        PostProcessingOptions.PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION,
        expected_files=[
            "result_data_for_scenario_evaluation/hourly_*_days.csv",
            "result_data_for_scenario_evaluation/daily_*_days.csv",
            "result_data_for_scenario_evaluation/monthly_*_days.csv",
            "result_data_for_scenario_evaluation/yearly_*_days.csv",
            "result_data_for_scenario_evaluation/scenario.json",
            "result_data_for_scenario_evaluation/simulation.json",
        ],
    )


def test_postprocessing_option_make_result_json_for_webtool(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    postprocessing_option_framework.run(
        PostProcessingOptions.MAKE_RESULT_JSON_FOR_WEBTOOL,
        expected_files=["results_for_webtool.json"],
    )


def test_postprocessing_option_write_component_configs_to_json(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    postprocessing_option_framework.run(
        PostProcessingOptions.WRITE_COMPONENT_CONFIGS_TO_JSON,
        expected_files=["scenario.json", "simulation.json"],
    )


def test_postprocessing_option_write_kpis_to_json_for_building_sizer(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    postprocessing_option_framework.run(
        PostProcessingOptions.WRITE_KPIS_TO_JSON_FOR_BUILDING_SIZER,
        expected_files=["*_kpi_config_for_building_sizer.json"],
    )


def test_postprocessing_option_write_kpis_to_json(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    postprocessing_option_framework.run(PostProcessingOptions.WRITE_KPIS_TO_JSON, expected_files=["all_kpis.json"])


def test_postprocessing_option_make_operation_results_for_webtool(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    postprocessing_option_framework.run(
        PostProcessingOptions.MAKE_OPERATION_RESULTS_FOR_WEBTOOL,
        expected_files=["results_daily_operation_for_webtool.json"],
    )


def test_postprocessing_option_export_to_pkl(postprocessing_option_framework: PostProcessingOptionTestFramework) -> None:
    postprocessing_option_framework.run(PostProcessingOptions.EXPORT_TO_PKL, expected_files=["*.pkl"])


def test_postprocessing_option_write_configs_for_scenario_evaluation_to_json(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    postprocessing_option_framework.run(
        PostProcessingOptions.WRITE_CONFIGS_FOR_SCENARIO_EVALUATION_TO_JSON,
        expected_files=["scenario.json", "simulation.json"],
    )


def test_postprocessing_option_export_monthly_results(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    postprocessing_option_framework.run(PostProcessingOptions.EXPORT_MONTHLY_RESULTS, expected_files=["*_monthly.csv"])


def test_postprocessing_option_export_results_in_one_file(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    postprocessing_option_framework.run(
        PostProcessingOptions.EXPORT_RESULTS_IN_ONE_FILE,
        expected_files=["all_results.csv"],
    )


def test_each_postprocessing_option_has_a_named_test() -> None:
    """Guard against adding enum values without adding a dedicated runtime-statistics test."""

    current_module = sys.modules[__name__]
    actual_test_names = {
        name
        for name, _function in inspect.getmembers(current_module, inspect.isfunction)
        if name.startswith("test_postprocessing_option_")
    }
    expected_test_names = {
        f"test_postprocessing_option_{postprocessing_option.name.lower()}"
        for postprocessing_option in PostProcessingOptions
    }

    assert actual_test_names == expected_test_names
