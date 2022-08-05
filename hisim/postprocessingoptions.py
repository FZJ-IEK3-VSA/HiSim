from enum import IntEnum

class PostProcessingOptions(IntEnum):
    """
    Enum class for enabling / disabling parts of the post processing
    """
    PlotLine = 1
    PlotCarpet = 2
    PlotSankey = 3
    PlotDay = 4
    PlotBar = 5
    OpenDirectory = 6
    ExportToCSV = 7
    ComputeKPI = 8
