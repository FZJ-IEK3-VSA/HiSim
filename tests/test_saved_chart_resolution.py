"""Every output chart is saved at the one resolution of :class:`SavedChartResolution`, 300 dpi.

The line, carpet, single-day and monthly bar charts were saved at 600 dpi: in a full-year run
that made 143 MB of time-series images and a 201 MB PDF report. Each test here draws one chart
kind into the test's own directory and reads the written PNG back, so a chart class that sets its
own resolution, or a constant moved back to 600, fails here. The lifecycle cost charts are checked
where they are drawn, in ``tests/test_economics_report_plots.py`` (``_assert_is_a_real_png``).
"""

import datetime
from pathlib import Path
from typing import Any, Callable, Dict

import numpy as np
import pandas as pd
import pytest
from PIL import Image as PilImage

from hisim.postprocessing.chart_singleday import ChartSingleDay
from hisim.postprocessing.chartbase import ChartFontsAndSize, SavedChartResolution
from hisim.postprocessing.charts import BarChart, Carpet, Line
from hisim.simulationparameters import FigureFormat

pytestmark: pytest.MarkDecorator = pytest.mark.base


def _arguments(tmp_path: Path) -> Dict[str, Any]:
    """Return the constructor arguments every chart kind shares, writing below ``tmp_path``.

    Args:
        tmp_path: The test's own directory.

    Returns:
        The keyword arguments of a chart of the PV system's electricity output in W.
    """
    return {
        "output": "PVSystem # ElectricityOutput",
        "component_name": "PVSystem",
        "units": "W",
        "directory_path": str(tmp_path),
        "time_correction_factor_in_hours": 1.0,
        "output_description": "electricity output",
        "figure_format": FigureFormat.PNG,
        "path_checker": lambda **_: None,
    }


JANUARY_FIRST = datetime.datetime(2021, 1, 1)
TWO_DAYS_HOURLY = pd.date_range(JANUARY_FIRST, periods=48, freq="h")


def _draw_line(tmp_path: Path) -> str:
    """Draw a line chart of two days and return the path of its PNG."""
    chart = Line(**_arguments(tmp_path))
    chart.plot(data_in_self_units=pd.Series(np.linspace(0.0, 2500.0, 48), index=TWO_DAYS_HOURLY))
    return chart.filepath2


def _draw_carpet(tmp_path: Path) -> str:
    """Draw a carpet of two days of 24 values and return the path of its PNG."""
    chart = Carpet(**_arguments(tmp_path))
    chart.plot(xdims=2, data_in_self_units=pd.Series(np.linspace(0.0, 2500.0, 48)), window_start=JANUARY_FIRST)
    return chart.filepath2


def _draw_single_day(tmp_path: Path) -> str:
    """Draw the single-day chart of 1 January and return the path of its PNG."""
    chart = ChartSingleDay(
        **_arguments(tmp_path),
        data_with_units=pd.Series(np.linspace(0.0, 2500.0, 48), index=TWO_DAYS_HOURLY),
        window_start=JANUARY_FIRST,
        day_in_window=0,
    )
    chart.plot(close=True)
    return chart.filepath2


def _draw_monthly_bars(tmp_path: Path) -> str:
    """Draw a monthly bar chart of twelve values and return the path of its PNG."""
    chart = BarChart(**_arguments(tmp_path))
    chart.plot(pd.Series(np.linspace(1000.0, 4000.0, 12)))
    return chart.filepath2


def test_the_saved_chart_resolution_is_300_dpi() -> None:
    """300 dpi, the print resolution the owner chose; the output charts take theirs from it."""
    assert SavedChartResolution.DPI == 300
    assert ChartFontsAndSize.dpi == SavedChartResolution.DPI


@pytest.mark.parametrize(
    "draw",
    [_draw_line, _draw_carpet, _draw_single_day, _draw_monthly_bars],
    ids=["line", "carpet", "single_day", "monthly_bars"],
)
def test_every_output_chart_is_saved_at_the_chart_resolution(tmp_path: Path, draw: Callable[[Path], str]) -> None:
    """The PNG says 300 dpi and has 300 pixels per inch of the figure: 1800 x 1200 for 6 x 4 inches.

    The report places a figure at its size in inches (the PNG's dpi tells it), so the resolution
    changes the pixels only, not how large the chart and its text are in the report.
    """
    path = draw(tmp_path)
    with PilImage.open(path) as image:
        dots_per_inch = image.info["dpi"]
        size_in_pixels = image.size
    assert dots_per_inch == pytest.approx((SavedChartResolution.DPI, SavedChartResolution.DPI), abs=0.01)
    width_in_inches, height_in_inches = ChartFontsAndSize.figsize
    assert size_in_pixels == (width_in_inches * SavedChartResolution.DPI, height_in_inches * SavedChartResolution.DPI)
