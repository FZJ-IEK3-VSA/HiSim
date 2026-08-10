"""Module containing PostProcessingOptions enum for configuring post-processing features in HiSim.

This module provides the PostProcessingOptions IntEnum class which defines various options
for enabling or disabling specific post-processing features such as plotting, exporting,
report generation, and KPI calculations.
"""
from enum import IntEnum, unique


@unique
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
    INCLUDE_CONFIGS_IN_PDF_REPORT = 14
    INCLUDE_IMAGES_IN_PDF_REPORT = 15
    PROVIDE_DETAILED_ITERATION_LOGGING = 16
    COMPUTE_OPEX = 17
    COMPUTE_CAPEX = 18
    COMPUTE_KPIS = 19
    PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION = 20
    WRITE_COMPONENT_CONFIGS_TO_JSON = 21
    WRITE_KPIS_TO_JSON_FOR_BUILDING_SIZER = 22
    WRITE_KPIS_TO_JSON = 23
    EXPORT_TO_PKL = 24
    WRITE_CONFIGS_FOR_SCENARIO_EVALUATION_TO_JSON = 25
    EXPORT_MONTHLY_RESULTS = 26
    EXPORT_RESULTS_IN_ONE_FILE = 27
