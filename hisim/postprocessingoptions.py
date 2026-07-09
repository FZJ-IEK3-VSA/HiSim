"""Module containing PostProcessingOptions enum for configuring post-processing features in HiSim.

This module provides the PostProcessingOptions IntEnum class which defines various options
for enabling or disabling specific post-processing features such as plotting, exporting,
report generation, and KPI calculations.
"""
# clean
from enum import IntEnum


class PostProcessingOptions(IntEnum):

    """Enum class for enabling / disabling parts of the post processing."""

    PLOT_LINE = 1
    PLOT_CARPET = 2
    PLOT_SANKEY = 3
    PLOT_SINGLE_DAYS = 4
    PLOT_MONTHLY_BAR_CHARTS = 5
    OPEN_DIRECTORY_IN_EXPLORER = 6
    EXPORT_TO_CSV = 7
    MAKE_NETWORK_CHARTS = 8
    GENERATE_PDF_REPORT = 9
    WRITE_COMPONENTS_TO_REPORT = 10
    WRITE_ALL_OUTPUTS_TO_REPORT = 11
    WRITE_NETWORK_CHARTS_TO_REPORT = 12
    PLOT_SPECIAL_TESTING_SINGLE_DAY = 13
    GENERATE_CSV_FOR_HOUSING_DATA_BASE = 14
    INCLUDE_CONFIGS_IN_PDF_REPORT = 15
    INCLUDE_IMAGES_IN_PDF_REPORT = 16
    PROVIDE_DETAILED_ITERATION_LOGGING = 17
    COMPUTE_OPEX = 18
    COMPUTE_CAPEX = 19
    COMPUTE_KPIS = 20
    PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION = 21
    WRITE_COMPONENT_CONFIGS_TO_JSON = 22
    WRITE_KPIS_TO_JSON_FOR_BUILDING_SIZER = 23
    WRITE_KPIS_TO_JSON = 24
    EXPORT_TO_PKL = 25
    WRITE_CONFIGS_FOR_SCENARIO_EVALUATION_TO_JSON = 26
    EXPORT_MONTHLY_RESULTS = 27
    EXPORT_RESULTS_IN_ONE_FILE = 38
    # Runs the parallel lifecycle cost engine (cost_spec.md). Opt-in and side-effect free:
    # only writes new files; all legacy outputs stay identical.
    COMPUTE_LIFECYCLE_COSTS = 39
    # Additionally writes the human-readable lifecycle reports: cost_summary.md,
    # lifecycle_report.html (plausibility panel + charts along the calculation chain) and
    # matplotlib PNGs. Implies COMPUTE_LIFECYCLE_COSTS.
    LIFECYCLE_COST_REPORT = 40
