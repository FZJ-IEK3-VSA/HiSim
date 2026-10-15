"""Charts for a single day."""

import datetime
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
        window_start: datetime.datetime,
        day_in_window: int = 0,
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
            window_start: The first moment of the run, at midnight; the row ``0`` of
                ``data_with_units`` belongs to it.
            day_in_window: Which day of the run to display, counted from 0 for the
                run's first day (default 0).
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

        self.window_start: datetime.datetime = window_start
        self.day_in_window: int = day_in_window
        displayed_date = self.date_of_day(window_start, day_in_window)
        self.data_with_units: pd.Series = data_with_units
        self.plot_title: str = ""
        # Named by the zero-based month and day of month of the displayed date: the first of January is m0_d0.
        self.filename: str = (
            f"{self.type.lower()}_m{displayed_date.month - 1}_d{displayed_date.day - 1}{self.figure_format}"
        )

        self.filepath: str = str(Path(self.directory_path) / self.filename)
        self.filepath2: str = str(Path(self.component_output_folder_path) / self.filename)

    @staticmethod
    def build_day_slice(
        data_with_units: "pd.Series",
        window_start: datetime.datetime,
        day_in_window: int,
        time_correction_factor_in_hours: float,
        label_months_lowercase: List[str],
        title: str,
    ) -> Tuple["pd.Series", str]:
        """Compute the slice of ``data_with_units`` for a single day of the run and the matching plot title.

        This is the pure, side-effect-free core of :meth:`get_day_data`. It does
        neither touch ``self`` nor the filesystem, so it can be unit-tested with a
        plain :class:`pandas.Series` and no ``Chart`` construction (whose parent
        ``Chart.__init__`` would otherwise wire up directory paths and the
        ``result_path_provider`` singleton).

        The rows of ``data_with_units`` are the timesteps of the run, the first one at
        ``window_start``, which is midnight; day ``d`` of the run is therefore the rows from
        ``d`` times the timesteps per day on. The title names the day by its calendar date.
        Example: hourly data of a run from 1 July 2021, day 2, gives rows 48 to 71 and the title
        ``"{title} July 3rd"``.

        Args:
            data_with_units: The full time-series to slice; its physical unit
                is carried separately by the caller (here via ``units``).
            window_start: The first moment of the run, at midnight.
            day_in_window: The day of the run to slice, counted from 0.
            time_correction_factor_in_hours: ``seconds_per_timestep / 3600``; 24 divided by it
                gives the number of timesteps per day.
            label_months_lowercase: The month labels, January first.
            title: The base plot title the date string is appended to.

        Returns:
            A ``(data_slice, plot_title)`` tuple, where ``data_slice`` is the
            sub-series covering the requested day with its own index (or the original
            ``data_with_units`` when the data ends before that day does) and ``plot_title``
            is the assembled title string ``"{title} {month} {day}{ordinal}"``.
        """
        timesteps_per_day = int(round(24 / time_correction_factor_in_hours))
        firstindex = day_in_window * timesteps_per_day
        lastindex = firstindex + timesteps_per_day
        date = ChartSingleDay.date_of_day(window_start, day_in_window)
        day_number = date.day
        if day_number in (1, 21, 31):
            ordinal = "st"
        elif day_number in (2, 22):
            ordinal = "nd"
        elif day_number in (3, 23):
            ordinal = "rd"
        else:
            ordinal = "th"
        plot_title = f"{title} {label_months_lowercase[date.month - 1]} {day_number}{ordinal}"

        if 0 < timesteps_per_day and lastindex <= len(data_with_units):
            return data_with_units.iloc[firstindex:lastindex], plot_title
        return data_with_units, plot_title

    def get_day_data(self) -> pd.Series:
        """Extract the sub-series for the configured day of the run.

        Delegates to :meth:`build_day_slice` using the instance's ``window_start``,
        ``day_in_window``, ``data_with_units``, ``time_correction_factor_in_hours``,
        ``label_months_lowercase``, and ``title``. As a side effect, sets ``self.plot_title`` to the
        assembled title string returned by ``build_day_slice``.

        Returns:
            The pandas Series slice covering the requested day, or the full
            ``self.data_with_units`` when the computed slice would exceed the available data.
        """
        data_with_units, self.plot_title = self.build_day_slice(
            data_with_units=self.data_with_units,
            window_start=self.window_start,
            day_in_window=self.day_in_window,
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
        time-of-day formatted x-axis under a title that names the day's date, and — when ``close`` is truthy — saves the
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
        plt.title(self.plot_title, fontsize=self.fontsize_title)
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
