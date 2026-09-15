"""Contains all the chart classes."""

# clean
import gc
from typing import Callable, ClassVar
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from hisim import log
from hisim.postprocessing.chartbase import Chart, ChartFontsAndSize
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
        time_correction_factor_in_hours: float,
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
            time_correction_factor_in_hours=time_correction_factor_in_hours,
            output_description=output_description,
            figure_format=figure_format,
            path_checker=path_checker,
        )

    def plot(self, xdims: int, data_in_self_units: pd.Series) -> ReportImageEntry | None:
        """Make a carpet plot.

        Args:
            xdims: Number of days (columns) the data is reshaped into.
            data_in_self_units: Physical simulation output to plot, with values
                expressed in the unit tracked by ``self.units`` (e.g. W, kWh,
                °C, kg/s). The unit is dynamic and may be rescaled during
                plotting.

        Returns:
            ReportImageEntry or None -- None when the data cannot be reshaped
            into entire days.
        """
        log.trace("starting carpet plots")
        self.ensure_output_dir()
        ydims = len(data_in_self_units) // xdims  # number of calculated timesteps per day
        y_steps_per_hour = ydims // 24

        try:
            reshaped_data_in_self_units = data_in_self_units.values.reshape(xdims, ydims)
        except ValueError:
            log.error("Carpet plot can only deal with data containing entire days")
            return None

        if np.max(np.abs(data_in_self_units.values)) > 1.5e3:
            reshaped_data_in_self_units = reshaped_data_in_self_units * 1e-3
            self.units = f"k{self.units}"

        plot_data_in_self_units = np.flip(reshaped_data_in_self_units.transpose(), axis=0)

        fig = plt.figure(figsize=self.figsize, dpi=self.dpi)

        axis = fig.add_subplot(111)
        mycolors = "viridis"
        color_map = mpl.colormaps.get_cmap(mycolors)

        plot = axis.pcolormesh(plot_data_in_self_units, cmap=color_map)
        plt.colorbar(plot).set_label(self.units, fontsize=self.fontsize_label)

        y_ticks = np.arange(0, 25 * y_steps_per_hour, 6 * y_steps_per_hour).tolist()
        axis.set_yticks(y_ticks)
        y_ticks_labels = np.flip(list(range(0, 25, 6)), axis=0)
        axis.set_yticklabels([str(i) for i in y_ticks_labels])

        if xdims == 365:
            x_ticks = np.arange(15, 346, 30).tolist()
            axis.set_xticks(x_ticks)
            axis.set_xticklabels([str(i) for i in self.months_abbrev_uppercase])

        # optimizing fonts
        fig.autofmt_xdate(rotation=45)
        # setting axis of the plot
        axis.set_ylabel("Time of day [h]", fontsize=self.fontsize_label)
        axis.set_xlabel("Month of the year", fontsize=self.fontsize_label)
        plt.title(self.title, fontsize=self.fontsize_title)
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
        time_correction_factor_in_hours: float,
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
            time_correction_factor_in_hours=time_correction_factor_in_hours,
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
        plt.xticks(fontsize=self.fontsize_ticks, rotation=20)
        plt.yticks(fontsize=self.fontsize_ticks)

        # Rescale values in case they are too high
        data_in_self_units, self.units = self.rescale_y_axis(y_values=data_in_self_units, units=self.units)

        plt.plot(time_index, data_in_self_units, color="green", linewidth=1.0)
        plt.ylabel(f"[{self.units}]", fontsize=self.fontsize_label)
        plt.xlabel("Time", fontsize=self.fontsize_label)
        plt.grid()
        plt.title(self.title, fontsize=self.fontsize_title)
        axis.set_xlim(xmin=time_index[0])
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


class BarChart(Chart, ChartFontsAndSize):  # noqa: too-few-public-methods
    """Makes Bar charts."""

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
        time_correction_factor_in_hours: float,
        output_description: str,
        figure_format: FigureFormat,
        path_checker: Callable[[str], None] | None = None,
    ) -> None:
        """Initializes the classes."""
        super().__init__(
            output=output,
            component_name=component_name,
            chart_type="Bar",
            units=units,
            directory_path=directory_path,
            time_correction_factor_in_hours=time_correction_factor_in_hours,
            output_description=output_description,
            figure_format=figure_format,
            path_checker=path_checker,
        )
        self.filename = f"monthly_{self.output}{self.figure_format}"

    def plot(self, data_in_self_units: pd.Series) -> ReportImageEntry:
        """Plot the bar chart.

        Args:
            data_in_self_units: Physical simulation output to plot, with values
                expressed in the unit tracked by ``self.units`` (e.g. W, kWh,
                °C, kg/s). The unit is dynamic and may be rescaled during
                plotting.
        """
        # Specify the values of blue bars (height)
        self.ensure_output_dir()

        # Position of bars on x-axis
        ind = np.arange(12)

        # Width of a bar
        width = 0.4

        # Rescale values in case they are too high
        data_in_self_units, self.units = self.rescale_y_axis(y_values=data_in_self_units, units=self.units)

        plt.subplots(figsize=self.figsize, dpi=self.dpi)
        plt.bar(ind, data_in_self_units, width)
        plt.xticks(
            ticks=ind,
            labels=[str(i) for i in self.months_abbrev_uppercase],
            fontsize=self.fontsize_ticks,
        )

        plt.yticks(fontsize=self.fontsize_ticks)
        plt.title(f"{self.title} Monthly", fontsize=self.fontsize_title)
        plt.grid()
        plt.tight_layout()
        plt.ylabel(f"[{self.units}]", fontsize=self.fontsize_label)

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
