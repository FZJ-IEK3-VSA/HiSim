"""Contains all the chart classes."""

import datetime
import enum
import gc
import math
from typing import Any, Callable, ClassVar, List, Tuple
import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib import dates as mdates
import numpy as np
import pandas as pd

from hisim import log
from hisim import loadtypes as lt
from hisim.postprocessing.chartbase import AxisUnit, Chart, ChartFontsAndSize
from hisim import utils
from hisim.postprocessing.report_image_entries import ReportImageEntry
from hisim.simulationparameters import FigureFormat

mpl.rcParams["agg.path.chunksize"] = 10000


class Carpet(Chart, ChartFontsAndSize):  # noqa: too-few-public-methods
    """Class for carpet plots."""

    def __init__(
        self,
        output: str,
        component_name: str,
        units: str,
        directory_path: str,
        output_description: str,
        figure_format: FigureFormat,
        path_checker: Callable[[str], None] | None = None,
    ) -> None:
        """Initializes a carpet plot."""
        super().__init__(
            output=output,
            component_name=component_name,
            chart_type="Carpet",
            units=units,
            directory_path=directory_path,
            output_description=output_description,
            figure_format=figure_format,
            path_checker=path_checker,
        )

    #: Below this many days the x axis names each day by its date; from it on, each month by its name.
    DAYS_FOR_MONTH_TICKS: ClassVar[int] = 28

    #: At most this many day ticks on the x axis of a window shorter than :attr:`DAYS_FOR_MONTH_TICKS`.
    MAXIMUM_DAY_TICKS: ClassVar[int] = 7

    @classmethod
    def x_axis_ticks(
        cls, window_start: datetime.datetime, number_of_days: int
    ) -> Tuple[List[float], List[str], str]:
        """Return where the x axis of a carpet over a run's window is labelled, with what, and its title.

        Column ``i`` of the carpet is the ``i``-th day of the window, counted from ``window_start``,
        and spans ``i`` to ``i + 1`` on the x axis. A window of at least
        :attr:`DAYS_FOR_MONTH_TICKS` days is labelled with the month names, each in the column of the
        15th of its month; a shorter one with the dates of up to :attr:`MAXIMUM_DAY_TICKS` of its
        days. Example: a full year from 1 January puts "JAN" at 14.5 and "DEC" at 348.5; the week
        from 1 July 2021 puts "01 Jul" at 0.5 and "07 Jul" at 6.5. The labels come from the window's
        own dates, so a window that starts in July is not labelled as if it started in January.

        Args:
            window_start: The first moment of the run, at midnight.
            number_of_days: The number of days, and carpet columns, in the window.

        Returns:
            The tick positions, their labels and the title of the x axis.
        """
        first_day = window_start.date()
        if number_of_days >= cls.DAYS_FOR_MONTH_TICKS:
            positions: List[float] = []
            labels: List[str] = []
            for month_index, label in enumerate(cls.months_abbrev_uppercase):
                column = (datetime.date(first_day.year, month_index + 1, 15) - first_day).days
                if 0 <= column < number_of_days:
                    positions.append(column + 0.5)
                    labels.append(label)
            return positions, labels, "Month of the year"
        step_in_days = max(1, math.ceil(number_of_days / cls.MAXIMUM_DAY_TICKS))
        columns = range(0, number_of_days, step_in_days)
        return (
            [column + 0.5 for column in columns],
            [cls.date_of_day(window_start, column).strftime("%d %b") for column in columns],
            "Day",
        )

    @staticmethod
    def y_axis_ticks(timesteps_per_day: int) -> Tuple[List[float], List[str]]:
        """Return where the time-of-day axis of a carpet is labelled, and with which hours.

        The carpet has one row per timestep of the day, ``timesteps_per_day`` rows from 0 at the bottom
        to ``timesteps_per_day`` at the top, and the bottom row is the day's last timestep. The axis is
        labelled every six hours, 24 at the bottom and 0 at the top, at the positions those hours have on
        it: hour ``h`` is at ``timesteps_per_day * (24 - h) / 24``. Example: hourly data puts "18" at 6.0;
        two-hour steps put it at 3.0; four-hour steps put "6" at 4.5, inside the row of 04:00 to 08:00,
        because that is where 06:00 lies.

        Every step that divides a day gets a carpet, up to one row per day at 24 h: the rows are fewer
        but each still covers the hours it simulated, and the columns still show one day after the
        other, so there is no step length above which the plot would say something wrong.

        Args:
            timesteps_per_day: The number of rows of the carpet, at least 1.

        Returns:
            The tick positions, bottom to top, and their labels.
        """
        quarters = range(0, 5)
        return (
            [timesteps_per_day * quarter / 4 for quarter in quarters],
            [str(24 - 6 * quarter) for quarter in quarters],
        )

    def plot(
        self, xdims: int, data_in_self_units: pd.Series, window_start: datetime.datetime
    ) -> ReportImageEntry | None:
        """Make a carpet plot: one column per day of the run, one row per timestep of the day.

        Each column starts at midnight, which the window contract of the simulation parameters
        guarantees for the run's first day and with it for every other.

        Args:
            xdims: Number of days (columns) the data is reshaped into.
            data_in_self_units: Physical simulation output to plot, with values
                expressed in the unit tracked by ``self.units`` (e.g. W, kWh,
                °C, kg/s). The unit is dynamic and may be rescaled during
                plotting.
            window_start: The first moment of the run, which names the days on the x axis.

        Returns:
            ReportImageEntry or None -- None when the data cannot be reshaped
            into entire days.
        """
        log.trace("starting carpet plots")
        self.ensure_output_dir()
        ydims = len(data_in_self_units) // xdims  # number of calculated timesteps per day

        try:
            reshaped_data_in_self_units = data_in_self_units.values.reshape(xdims, ydims)
        except ValueError:
            log.error("Carpet plot can only deal with data containing entire days")
            return None

        # The same unit as the line and single-day charts of this output show: a carpet of a power of
        # 1.2 kW used to stay in W, and one of 2 000 kWh used to read "kkWh".
        reshaped_data_in_self_units, self.units = self.rescale_y_axis(
            y_values=reshaped_data_in_self_units, units=self.declared_units
        )

        plot_data_in_self_units = np.flip(reshaped_data_in_self_units.transpose(), axis=0)

        fig = plt.figure(figsize=self.figsize, dpi=self.dpi)

        axis = fig.add_subplot(111)
        mycolors = "viridis"
        color_map = mpl.colormaps.get_cmap(mycolors)

        plot = axis.pcolormesh(plot_data_in_self_units, cmap=color_map)
        plt.colorbar(plot).set_label(self.axis_label(self.units), fontsize=self.fontsize_label)

        # The ticks used to be placed per whole hour of rows, which is 0 rows for a step above one hour and
        # made np.arange divide by zero; they are placed on the day's own time axis now.
        y_ticks, y_tick_labels = self.y_axis_ticks(ydims)
        axis.set_yticks(y_ticks)
        axis.set_yticklabels(y_tick_labels)

        x_ticks, x_tick_labels, x_axis_title = self.x_axis_ticks(window_start, xdims)
        axis.set_xticks(x_ticks)
        axis.set_xticklabels(x_tick_labels)

        # optimizing fonts
        fig.autofmt_xdate(rotation=45)
        # setting axis of the plot
        axis.set_ylabel("Time of day [h]", fontsize=self.fontsize_label)
        axis.set_xlabel(x_axis_title, fontsize=self.fontsize_label)
        plt.title(self.wrap_title(self.title), fontsize=self.fontsize_title)
        plt.xticks(fontsize=self.fontsize_ticks)
        plt.yticks(fontsize=self.fontsize_ticks)
        plt.tight_layout()
        log.trace("finished carpet plot: " + self.filepath)
        plt.savefig(self.filepath2)
        plt.close()
        return ReportImageEntry(
            category=None,
            output_description=self.output_description,
            component_output_folder_path=self.component_output_folder_path,
            file_path=self.filepath2,
            unit=self.units,
            component_name=self.component_name,
            output_type=self.output_type,
        )


class Line(Chart, ChartFontsAndSize):  # noqa: too-few-public-methods
    """Makes a line chart."""

    # @utils.measure_memory_leak
    def __init__(
        self,
        output: str,
        component_name: str,
        units: str,
        directory_path: str,
        output_description: str,
        figure_format: FigureFormat,
        path_checker: Callable[[str], None] | None = None,
    ) -> None:
        """Initializes a line chart."""
        if output_description is None:
            raise ValueError("Output description was None for component " + component_name)

        super().__init__(
            output=output,
            component_name=component_name,
            chart_type="Line",
            units=units,
            directory_path=directory_path,
            output_description=output_description,
            figure_format=figure_format,
            path_checker=path_checker,
        )

    @utils.measure_memory_leak
    def plot(self, data_in_self_units: pd.Series) -> ReportImageEntry:
        """Make a line plot.

        Args:
            data_in_self_units: Physical simulation output to plot, with values
                expressed in the unit tracked by ``self.units`` (e.g. W, kWh,
                °C, kg/s). The unit is dynamic and may be rescaled during
                plotting. The series index is a ``DatetimeIndex`` used as the
                time axis.
        """

        mpl.use("Agg")
        self.ensure_output_dir()

        _fig, axis = plt.subplots(figsize=self.figsize, dpi=self.dpi)
        time_index = data_in_self_units.index
        plt.yticks(fontsize=self.fontsize_ticks)

        # Rescale values in case they are too high
        data_in_self_units, self.units = self.rescale_y_axis(y_values=data_in_self_units, units=self.declared_units)

        plt.plot(time_index, data_in_self_units, color="green", linewidth=1.0)
        plt.ylabel(self.axis_label(self.units), fontsize=self.fontsize_label)
        plt.xlabel("Time", fontsize=self.fontsize_label)
        plt.grid()
        plt.title(self.wrap_title(self.title), fontsize=self.fontsize_title)
        if isinstance(time_index, pd.DatetimeIndex):
            # Month names under a year, hours under a date: the concise formatter names each tick once
            # and puts the year (or the date) beside the axis, where "2021-01" ... "2021-12" overlapped.
            locator = mdates.AutoDateLocator()
            axis.xaxis.set_major_locator(locator)
            axis.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
        plt.xticks(fontsize=self.fontsize_ticks)
        # Both ends at the data: a margin after the last timestep showed an empty strip and a tick of the next year.
        if len(time_index) > 1:
            axis.set_xlim(xmin=time_index[0], xmax=time_index[-1])
        plt.tight_layout()
        # plt.savefig(self.filepath)
        plt.savefig(self.filepath2)
        plt.cla()
        plt.clf()
        plt.close("all")
        # Keep this explicit full GC pass in place: post-processing creates a
        # large number of charts in rapid succession, and past experience
        # showed memory usage ballooning without it. The per-call overhead is
        # intentional and outweighed by avoiding unbounded growth across the
        # hundreds-to-thousands of plots produced in a parametric study.
        del time_index
        gc.collect(2)
        return ReportImageEntry(
            category=None,
            output_description=self.output_description,
            component_output_folder_path=self.component_output_folder_path,
            file_path=self.filepath2,
            unit=self.units,
            component_name=self.component_name,
            output_type=self.output_type,
        )


class MonthlyAggregation(str, enum.Enum):
    """How the twelve values of a monthly bar chart were formed from the run's time steps.

    The simulator aggregates every output into months by its unit (``Simulator.get_std_results``):
    a rate or a state such as W, °C or a fraction is averaged over the month, everything else, such
    as Wh, is summed. The bar chart names which of the two its bars are, since a monthly mean power
    in kW and a monthly energy in kWh look alike on an axis that names only the unit. Example: PV
    electricity in W is drawn as "Power, monthly mean [kW]", PV energy in Wh as "Energy, monthly
    sum [MWh]".
    """

    MEAN = "mean"
    SUM = "sum"

    @classmethod
    def of_unit(cls, unit: Any) -> "MonthlyAggregation":
        """Return how the simulator aggregates an output of this unit into months.

        Args:
            unit: The output's unit, a ``loadtypes.Units`` member or its string value.

        Returns:
            ``MEAN`` for the units in ``loadtypes.UNITS_USING_MEAN_AGGREGATION``, ``SUM`` for all others.
        """
        unit_value = unit.value if hasattr(unit, "value") else unit
        mean_unit_values = {mean_unit.value for mean_unit in lt.UNITS_USING_MEAN_AGGREGATION}
        return cls.MEAN if unit_value in mean_unit_values else cls.SUM


class BarChart(Chart, ChartFontsAndSize):  # noqa: too-few-public-methods
    """Makes the monthly bar chart of one output: twelve bars, January to December.

    The y axis names the quantity and whether a bar is the month's mean or its sum
    (:class:`MonthlyAggregation`), and an output that is zero in every month is drawn with a note
    saying so instead of an empty axis.
    """

    original_pv_sol_in_kwh: ClassVar[list[float]] = [
        385.66,
        484.01,
        981.05,
        1096.7,
        1157,
        1299.9,
        1415.3,
        1266.1,
        1075.8,
        714.44,
        422.51,
        366.83,
    ]
    """Reference monthly PV production values (in kWh) used as a baseline for comparison."""

    def __init__(
        self,
        output: str,
        component_name: str,
        units: str,
        directory_path: str,
        output_description: str,
        figure_format: FigureFormat,
        path_checker: Callable[[str], None] | None = None,
        aggregation: MonthlyAggregation | None = None,
    ) -> None:
        """Initialize the bar chart of one output.

        Args:
            output: The output's full name, ``"Component # Field"``.
            component_name: The component the output belongs to.
            units: The output's unit.
            directory_path: The result directory.
            output_description: The output's description, for the report.
            figure_format: The image format.
            path_checker: Validates path lengths; the result path provider's check by default.
            aggregation: Whether the monthly values are means or sums; derived from ``units`` as the
                simulator does when not given.
        """
        super().__init__(
            output=output,
            component_name=component_name,
            chart_type="Bar",
            units=units,
            directory_path=directory_path,
            output_description=output_description,
            figure_format=figure_format,
            path_checker=path_checker,
        )
        self.aggregation: MonthlyAggregation = (
            aggregation if aggregation is not None else MonthlyAggregation.of_unit(self.declared_units)
        )

    def y_axis_label(self, shown_units: str) -> str:
        """Return the y axis title: quantity, aggregation and unit, e.g. ``Energy, monthly sum [MWh]``.

        Args:
            shown_units: The unit the bars are drawn in, as :meth:`rescale_y_axis` returned it.

        Returns:
            The label.
        """
        quantity = AxisUnit.QUANTITIES.get(self.declared_units, "Value")
        return f"{quantity}, monthly {self.aggregation.value} [{shown_units}]"

    def plot(self, data_in_self_units: pd.Series) -> ReportImageEntry:
        """Plot the bar chart.

        Args:
            data_in_self_units: The twelve monthly values of the output, January to December,
                expressed in the unit tracked by ``self.units`` (e.g. W, kWh, °C). The unit may be
                rescaled with an SI prefix while plotting.

        Returns:
            The report entry of the written image.
        """
        self.ensure_output_dir()

        # Position of bars on x-axis
        ind = np.arange(12)

        # Width of a bar
        width = 0.4

        # Rescale values in case they are too high
        data_in_self_units, self.units = self.rescale_y_axis(y_values=data_in_self_units, units=self.declared_units)

        _fig, axis = plt.subplots(figsize=self.figsize, dpi=self.dpi)
        axis.set_axisbelow(True)
        axis.grid(axis="y")
        axis.bar(ind, data_in_self_units, width)
        axis.set_xticks(ind)
        axis.set_xticklabels([str(i) for i in self.months_abbrev_uppercase], fontsize=self.fontsize_ticks)
        axis.tick_params(axis="y", labelsize=self.fontsize_ticks)
        if not np.any(np.asarray(data_in_self_units, dtype=float)):
            axis.text(
                0.5,
                0.5,
                "All monthly values are 0",
                transform=axis.transAxes,
                ha="center",
                va="center",
                fontsize=self.fontsize_label,
            )
        axis.set_title(self.wrap_title(self.title), fontsize=self.fontsize_title)
        axis.set_ylabel(self.y_axis_label(self.units), fontsize=self.fontsize_label)
        plt.tight_layout()

        plt.savefig(self.filepath2)
        plt.close()
        return ReportImageEntry(
            category=None,
            output_description=self.output_description,
            component_output_folder_path=self.component_output_folder_path,
            file_path=self.filepath2,
            unit=self.units,
            component_name=self.component_name,
            output_type=self.output_type,
        )
