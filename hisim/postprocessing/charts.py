""" Contains all the chart classes. """
# clean
import gc
from typing import Any
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np

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
        output: Any,
        component_name: str,
        units: Any,
        directory_path: str,
        time_correction_factor: float,
        output_description: str,
        figure_format: FigureFormat,
    ) -> None:
        """Initalizes a carpot plot."""
        super().__init__(
            output=output,
            component_name=component_name,
            chart_type="Carpet",
            units=units,
            directory_path=directory_path,
            time_correction_factor=time_correction_factor,
            output_description=output_description,
            figure_format=figure_format,
        )

    def plot(self, xdims: int, data: Any) -> ReportImageEntry:
        """Makes a carpet plot."""
        log.trace("starting carpet plots")
        ydims = int(len(data) / xdims)  # number of calculated timesteps per day
        y_steps_per_hour = int(ydims / 24)

        try:
            database = data.values.reshape(xdims, ydims)
        except ValueError:
            log.error("Carpet plot can only deal with data containing entire days")

        if np.max(np.abs(data.values)) > 1.5e3:
            database = database * 1e-3
            self.units = f"k{self.units}"

        plot_data = np.flip(database.transpose(), axis=0)

        fig = plt.figure(figsize=self.figsize, dpi=self.dpi)

        axis = fig.add_subplot(111)
        mycolors = "viridis"
        color_map = plt.cm.get_cmap(mycolors)

        plot = axis.pcolormesh(plot_data, cmap=color_map)
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
        output: Any,
        component_name: str,
        units: Any,
        directory_path: str,
        time_correction_factor: float,
        output_description: str,
        figure_format: FigureFormat,
    ):
        """Initializes a line chart."""
        if output_description is None:
            raise ValueError("Output description was None for component " + component_name)

        super().__init__(
            output=output,
            component_name=component_name,
            chart_type="Line",
            units=units,
            directory_path=directory_path,
            time_correction_factor=time_correction_factor,
            output_description=output_description,
            figure_format=figure_format,
        )

    @utils.measure_memory_leak
    def plot(self, data: Any) -> ReportImageEntry:
        """Makes a line plot."""

        mpl.use("Agg")

        _fig, axis = plt.subplots(figsize=self.figsize, dpi=self.dpi)
        x_zero = data.index
        plt.xticks(fontsize=self.fontsize_ticks, rotation=20)
        plt.yticks(fontsize=self.fontsize_ticks)

        # Rescale values in case they are too high
        data, self.units = self.rescale_y_axis(y_values=data, units=self.units)

        plt.plot(x_zero, data, color="green", linewidth=1.0)
        plt.ylabel(f"[{self.units}]", fontsize=self.fontsize_label)
        plt.xlabel("Time", fontsize=self.fontsize_label)
        plt.grid()
        plt.title(self.title, fontsize=self.fontsize_title)
        axis.set_xlim(xmin=x_zero[0])
        plt.tight_layout()
        # plt.savefig(self.filepath)
        plt.savefig(self.filepath2)
        plt.cla()
        plt.clf()
        plt.close("all")
        del x_zero
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

    original_pv_sol = [
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

    def __init__(
        self,
        output: Any,
        component_name: str,
        units: Any,
        directory_path: str,
        time_correction_factor: float,
        output_description: str,
        figure_format: FigureFormat,
    ):
        """Initializes the classes."""
        super().__init__(
            output=output,
            component_name=component_name,
            chart_type="Bar",
            units=units,
            directory_path=directory_path,
            time_correction_factor=time_correction_factor,
            output_description=output_description,
            figure_format=figure_format,
        )
        self.filename = f"monthly_{self.output}{self.figure_format}"

    def plot(self, data: Any) -> ReportImageEntry:
        """Plots the bar chart."""
        width = 0.35
        # Specify the values of blue bars (height)

        # Position of bars on x-axis
        ind = np.arange(12)

        # Width of a bar
        width = 0.4

        # Rescale values in case they are too high
        data, self.units = self.rescale_y_axis(y_values=data, units=self.units)

        plt.subplots(figsize=self.figsize, dpi=self.dpi)
        plt.bar(ind, data, width)
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
