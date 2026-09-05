"""Charts for a single day."""

# clean
from typing import Callable, List, Tuple
from pathlib import Path
import matplotlib.pyplot as plt
from matplotlib.axis import Axis
from matplotlib.dates import DateFormatter
import pandas as pd

from hisim.postprocessing.chartbase import Chart, ChartFontsAndSize
from hisim.postprocessing.report_image_entries import ReportImageEntry


class ChartSingleDay(Chart, ChartFontsAndSize):
    """For making visualisations for a single day."""

    def __init__(
        self,
        output: str,
        component_name: str,
        units: str,
        directory_path: str,
        time_correction_factor_in_hours: float,
        output_description: str,
        data_with_units: pd.Series,
        day: int = 0,
        month: int = 0,
        output2_with_units: pd.Series | None = None,
        figure_format: str | None = None,
        path_checker: Callable[[str], None] | None = None,
    ) -> None:
        """Initializes a single-day chart.

        Args:
            output: The primary output data to plot.
            component_name: Name of the component producing the data.
            units: Units of the output values.
            directory_path: Directory path for saving the chart.
            time_correction_factor_in_hours: Factor for time step correction.
            output_description: Description of the output.
            data_with_units: Time-series data to visualize; its physical unit
                is carried separately by the ``units`` argument.
            day: Day of the month to display (default 0).
            month: Month of the year to display (default 0).
            output2_with_units: Optional secondary output data for comparison;
                its physical unit is carried separately by ``units``.
            figure_format: Format for the output figure (e.g., '.png').
            path_checker: Optional callable to validate path length; defaults to
                the global ``result_path_provider.check_path_length``.
        """
        if output2_with_units is not None:
            super().__init__(
                output=output,
                component_name=component_name,
                chart_type="days",
                units=units,
                directory_path=directory_path,
                time_correction_factor_in_hours=time_correction_factor_in_hours,
                output_description=output_description,
                output2=output2_with_units,
                figure_format=figure_format,
                path_checker=path_checker,
            )
        else:
            super().__init__(
                output=output,
                component_name=component_name,
                chart_type="days",
                units=units,
                directory_path=directory_path,
                time_correction_factor_in_hours=time_correction_factor_in_hours,
                output_description=output_description,
                figure_format=figure_format,
                path_checker=path_checker,
            )

        self.month: int = month
        self.day: int = day
        self.data_with_units: pd.Series = data_with_units
        self.plot_title: str = ""
        self.filename: str = f"{self.type.lower()}_m" f"{self.month}_d{self.day}{self.figure_format}"

        self.filepath: str = str(Path(self.directory_path) / self.filename)
        self.filepath2: str = str(Path(self.component_output_folder_path) / self.filename)

    @staticmethod
    def build_day_slice(
        data_with_units: "pd.Series",
        month: int,
        day: int,
        time_correction_factor_in_hours: float,
        label_months_lowercase: List[str],
        title: str,
    ) -> Tuple["pd.Series", str]:
        """Compute the slice of ``data_with_units`` for a single day and the matching plot title.

        This is the pure, side-effect-free core of :meth:`get_day_data`. It does
        neither touch ``self`` nor the filesystem, so it can be unit-tested with a
        plain :class:`pandas.Series` and no ``Chart`` construction (whose parent
        ``Chart.__init__`` would otherwise wire up directory paths and the
        ``result_path_provider`` singleton).

        Args:
            data_with_units: The full time-series to slice; its physical unit
                is carried separately by the caller (here via ``units``).
            month: Zero-based month index (0 = January, ... 11 = December).
            day: Zero-based day-of-month index within that month (0 = 1st).
            time_correction_factor_in_hours: ``seconds_per_timestep / 3600``; its reciprocal
                gives the number of timesteps per hour.
            label_months_lowercase: The month labels indexed by ``month``.
            title: The base plot title the date string is appended to.

        Returns:
            A ``(data_slice, plot_title)`` tuple, where ``data_slice`` is the
            sub-series covering the requested day (or the original ``data_with_units`` when
            the computed slice would exceed the available data) and ``plot_title``
            is the assembled title string ``"{title} {month} {day}{ordinal}"``.
        """
        timesteps_per_hour = int(1 / time_correction_factor_in_hours)
        firstindex = (month * 30 + day) * 24 * timesteps_per_hour
        lastindex = firstindex + 24 * timesteps_per_hour
        day_number = day + 1
        if day_number == 1:
            ordinal = "st"
        elif day_number == 2:
            ordinal = "nd"
        elif day_number == 3:
            ordinal = "rd"
        else:
            ordinal = "th"
        date = f"{label_months_lowercase[month]} {day_number}{ordinal}"
        plot_title = f"{title} {date}"

        if abs(lastindex - firstindex) < len(data_with_units):
            data_slice = data_with_units[firstindex:lastindex]
            data_index = data_slice.index[firstindex:lastindex]
            data_slice.index = data_index
            return data_slice, plot_title
        return data_with_units, plot_title

    def get_day_data(self) -> pd.Series:
        """Extract the sub-series for the configured month and day.

        Delegates to :meth:`build_day_slice` using the instance's ``month``,
        ``day``, ``data_with_units``, ``time_correction_factor_in_hours``, ``label_months_lowercase``,
        and ``title``. As a side effect, sets ``self.plot_title`` to the
        assembled title string returned by ``build_day_slice``.

        Returns:
            The pandas Series slice covering the requested day, or the full
            ``self.data_with_units`` when the computed slice would exceed the available data.
        """
        data_with_units, self.plot_title = self.build_day_slice(
            data_with_units=self.data_with_units,
            month=self.month,
            day=self.day,
            time_correction_factor_in_hours=self.time_correction_factor_in_hours,
            label_months_lowercase=self.label_months_lowercase,
            title=self.title,
        )
        return data_with_units

    def close(self) -> None:
        """Save the current matplotlib figure to disk and close it.

        Writes the active figure to ``self.filepath2`` (the component output
        folder path) and then closes the figure via ``plt.close()``.
        """

        plt.savefig(self.filepath2)
        plt.close()

    def plot(self, close: bool) -> ReportImageEntry:
        """Plot the single-day time series and optionally save the figure.

        Rescales the y-axis units, draws the day's data as a green line with a
        time-of-day formatted x-axis, and — when ``close`` is truthy — saves the
        figure to ``self.filepath2`` and closes it.

        Args:
            close: If truthy, persist the figure via :meth:`close` after drawing.

        Returns:
            A :class:`ReportImageEntry` describing the generated image, including
            the component name, output description, (possibly rescaled) units,
            output type, and the file path the figure was written to.
        """
        self.ensure_output_dir()
        single_day_data_with_units = self.get_day_data()
        plt.rcParams["font.size"] = "30"
        plt.rcParams["agg.path.chunksize"] = 10000
        _fig, a_x = plt.subplots(figsize=self.figsize, dpi=self.dpi)
        plt.xticks(fontsize=self.fontsize_ticks)
        plt.yticks(fontsize=self.fontsize_ticks)

        single_day_data_with_units, self.units = self.rescale_y_axis(y_values=single_day_data_with_units, units=self.units)
        plt.title(self.title, fontsize=self.fontsize_title)
        plt.plot(
            single_day_data_with_units.index,
            single_day_data_with_units,
            color="green",
            linewidth=1.0,
            label=self.property,
        )
        plt.grid(True)
        plt.xlabel("Time [hours]", fontsize=self.fontsize_label)
        plt.ylabel(f"[{self.units}]", fontsize=self.fontsize_label)
        plt.tight_layout()
        Axis.set_major_formatter(a_x.xaxis, DateFormatter("%H:%M"))
        if close:
            self.close()
        return ReportImageEntry(
            category=None,
            output_description=self.output_description,
            component_output_folder_path=self.component_output_folder_path,
            file_path=self.filepath2,
            unit=self.units,
            component_name=self.component_name,
            output_type=self.output_type,
        )
