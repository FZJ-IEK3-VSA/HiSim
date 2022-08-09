
""" For setting the post processing options. """

from enum import IntEnum


class PostProcessingOptions(IntEnum):

    """ Enum class for enabling / disabling parts of the post processing. """

    PLOT_LINE = 1
    PLOT_CARPET = 2
    PLOT_SANKEY = 3
    PLOT_SINGLE_DAYS = 4
    PLOT_BAR_CHARTS = 5
    OPEN_DIRECTORY_IN_EXPLORER = 6
    EXPORT_TO_CSV = 7
    COMPUTE_KPI = 8
    GENERATE_PDF_REPORT = 8
