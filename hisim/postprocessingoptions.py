""" For setting the post processing options. """
# clean
from enum import IntEnum


class PostProcessingOptions(IntEnum):

    """Enum class for enabling / disabling parts of the post processing."""

    PLOT_LINE = 1
    PLOT_CARPET = 2
    PLOT_SANKEY = 3
    PLOT_SINGLE_DAYS = 4
    PLOT_BAR_CHARTS = 5
    OPEN_DIRECTORY_IN_EXPLORER = 6
    EXPORT_TO_CSV = 7
    GENERATE_PDF_REPORT = 8
    WRITE_KPI_TO_REPORT = 9
    WRITE_ALL_OUTPUTS_TO_REPORT = 10
    MAKE_NETWORK_CHARTS = 11
    PLOT_SPECIAL_TESTING_SINGLE_DAY = 12
    GENERATE_CSV_FOR_HOUSING_DATA_BASE = 13
    INCLUDE_CONFIGS_IN_PDF_REPORT = 14
    INCLUDE_IMAGES_IN_PDF_REPORT = 15
