""" Charts for a single day. """
# clean
import os
from typing import Any
import matplotlib.pyplot as plt
from matplotlib.dates import DateFormatter

from hisim.postprocessing.chartbase import Chart, ChartFontsAndSize
from hisim.postprocessing.report_image_entries import ReportImageEntry


class ChartSingleDay(Chart, ChartFontsAndSize):

    """For making visualisations for a single day."""

    def __init__(
        self,
        output: Any,
        component_name: str,
        units: Any,
        directory_path: str,
        time_correction_factor: float,
        output_description: str,
        data: Any,
        day: Any = 0,
        month: Any = 0,
        output2: Any = None,
        figure_format: Any = None,
    ):
        """Initializes the class."""
        if output2 is not None:
            super().__init__(
                output=output,
                component_name=component_name,
                chart_type="days",
                units=units,
                directory_path=directory_path,
                time_correction_factor=time_correction_factor,
                output_description=output_description,
                output2=output2,
                figure_format=figure_format,
            )
        else:
            super().__init__(
                output=output,
                component_name=component_name,
                chart_type="days",
                units=units,
                directory_path=directory_path,
                time_correction_factor=time_correction_factor,
                output_description=output_description,
                figure_format=figure_format,
            )
        self.axis: plt.axis
        self.ax2: plt.axis
        self.line2: plt.axis
        self.month = month
        self.day = day
        self.data = data
        self.plot_title: str
        self.filename = (
            f"{self.type.lower()}_{self.output.split(' # ', 2)[1]}_{self.output.split(' # ', 2)[0]}_m"
            f"{self.month}_d{self.day}{self.figure_format}"
        )

        self.filepath = os.path.join(self.directory_path, self.filename)
        self.filepath2 = os.path.join(self.component_output_folder_path, self.filename)

    def get_day_data(self):
        """Extracts data for a single day."""
        firstindex = (
            (self.month * 30 + self.day) * 24 * int(1 / self.time_correction_factor)
        )
        lastindex = firstindex + 24 * int(1 / self.time_correction_factor)
        day_number = self.day + 1
        if day_number == 1:
            ordinal = "st"
        elif day_number == 2:
            ordinal = "nd"
        elif day_number == 3:
            ordinal = "rd"
        else:
            ordinal = "th"
        date = f"{self.label_months_lowercase[self.month]} {day_number}{ordinal}"
        self.plot_title = f"{self.title} {date}"

        if abs(lastindex - firstindex) < len(self.data):
            data = self.data[firstindex:lastindex]
            data_index = data.index[firstindex:lastindex]
            data.index = data_index
            return data
        return self.data

    def __add__(self, other):
        """Adds another chart to this one."""
        my_double: ChartSingleDay = ChartSingleDay(
            self.output,
            self.component_name,
            self.ylabel,
            self.directory_path,
            self.time_correction_factor,
            self.day,
            self.month,
            self.data,
            self.output_description,
            self.figure_format,
        )
        my_double.filename = (
            f"{self.type.lower()}_{self.output.split(' # ', 2)[1]}_{self.output.split(' # ', 2)[0]}"
            f"_AND_{other.output.split(' # ', 2)[1]}_{other.output.split(' # ', 2)[0]}_m{self.month}_d{self.day}{self.figure_format}"
        )
        my_double.filepath = os.path.join(self.directory_path, my_double.filename)
        my_double.plot(close=False)

        #  twin object for two different y-axis on the sample plot
        my_double.ax2 = my_double.axis.twinx()
        #  make a plot with different y-axis using second axis object
        my_double.line2 = my_double.ax2.plot(
            self.data.index, other.data, label=other.property, linewidth=5
        )
        my_double.ax2.set_ylabel(f"{other.ylabel}")
        my_double.ax2.xaxis.set_major_formatter(DateFormatter("%H:%M"))
        return my_double

    def close(self):
        """Closes a chart and saves."""
        plt.xticks(fontsize=self.fontsize_ticks)
        plt.yticks(fontsize=self.fontsize_ticks)
        self.axis.xaxis.set_major_formatter(DateFormatter("%H:%M"))
        self.axis.set_ylabel(f"[{self.ylabel}]", fontsize=self.fontsize_label)
        if hasattr(self, "line2"):
            self.ax2.xaxis.set_major_formatter(DateFormatter("%H:%M"))
        # plt.savefig(self.filepath)
        plt.savefig(self.filepath2)
        plt.close()

    def plot(self, close: Any) -> ReportImageEntry:
        """Plots a chart."""
        single_day_data = self.get_day_data()
        plt.rcParams["font.size"] = "30"
        plt.rcParams["agg.path.chunksize"] = 10000
        _fig, self.axis = plt.subplots(figsize=self.figsize, dpi=self.dpi)
        plt.xticks(fontsize=self.fontsize_ticks)
        plt.yticks(fontsize=self.fontsize_ticks)
        if abs(max(single_day_data)) > 1.5e3:
            single_day_data = single_day_data * 1e-3
            self.ylabel = f"k{self.ylabel}"
        plt.title(self.title, fontsize=self.fontsize_title)
        plt.plot(
            single_day_data.index,
            single_day_data,
            color="green",
            linewidth=1.0,
            label=self.property,
        )
        plt.grid(True)
        self.axis.set_ylabel(self.ylabel)
        plt.xlabel("Time [hours]", fontsize=self.fontsize_label)
        plt.ylabel(self.ylabel, fontsize=self.fontsize_label)
        plt.tight_layout()
        self.axis.xaxis.set_major_formatter(DateFormatter("%H:%M"))
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
