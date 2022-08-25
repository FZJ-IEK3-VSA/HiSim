""" Charts for a single day. """

import os
import matplotlib.pyplot as plt
from matplotlib.dates import DateFormatter

from hisim.postprocessing.chartbase import Chart


class ChartSingleDay(Chart):

    """ For making visualisations for a single day. """

    def __init__(self, output,  units, directorypath, time_correction_factor, day=None, month=None, output2=None):
        """ Initializes the class. """
        if output2 is not None:
            super().__init__(output,  "days", units, directorypath, time_correction_factor, output2)
        else:
            super().__init__(output,  "days", units, directorypath, time_correction_factor)
        self.axis: plt.axis
        self.ax2: plt.axis
        self.line2: plt.axis
        self.month = month
        self.day = day
        self.filename = f"{self.type.lower()}_{self.output.split(' # ', 2)[1]}_{self.output.split(' # ', 2)[0]}_m" \
                        f"{self.month}_d{self.day}.png"
        self.filepath = os.path.join(self.directorypath, self.filename)


    def get_day_data(self, data):
        """ Extracts data for a single day. """
        firstindex = (self.month * 30 + self.day) * 24 * int(1 / self.time_correction_factor)
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

        if abs(lastindex - firstindex) < len(data):
            data = data[firstindex:lastindex]
            data_index = data.index[firstindex:lastindex]
            data = data
            data.index = data_index

    def __add__(self, other, data):
        """ Adds another chart to this one. """
        my_double: ChartSingleDay = ChartSingleDay(self.output,
                                                   self.ylabel,
                                                   self.directorypath,
                                                   self.time_correction_factor,
                                                   self.day,
                                                   self.month)
        my_double.filename = f"{self.type.lower()}_{self.output.split(' # ', 2)[1]}_{self.output.split(' # ', 2)[0]}" \
                             f"_AND_{other.output.split(' # ', 2)[1]}_{other.output.split(' # ', 2)[0]}_m{self.month}_d{self.day}.png"
        my_double.filepath = os.path.join(self.directorypath, my_double.filename)
        my_double.plot(close=False, data=data )

        #  twin object for two different y-axis on the sample plot
        my_double.ax2 = my_double.axis.twinx()
        #  make a plot with different y-axis using second axis object
        my_double.line2 = my_double.ax2.plot(data.index, other.data, label=other.property, linewidth=5)
        my_double.ax2.set_ylabel(f"{other.property} [{other.ylabel}]", fontsize=18)
        #  seaborn.despine(ax=my_double.ax2, offset=0)  # the important part here
        my_double.ax2.xaxis.set_major_formatter(DateFormatter("%H:%M"))
        return my_double

    def close(self):
        """ Closes a chart and saves. """
        plt.xticks(fontsize=18)
        plt.yticks(fontsize=18)
        #  if hasattr(self,"line2"):
        #    lines = self.line1 + self.line2
        #    labs = [l.get_label() for l in lines]
        #    self.ax.legend(lines, labs, loc=0)
        self.axis.xaxis.set_major_formatter(DateFormatter("%H:%M"))
        self.axis.set_ylabel(f"{self.property} [{self.ylabel}]", fontsize=18)
        if hasattr(self, "line2"):
            self.ax2.xaxis.set_major_formatter(DateFormatter("%H:%M"))
        #  plt.savefig(self.filepath, bbox_inches='tight')
        plt.savefig(self.filepath)
        plt.close()

    def plot(self, data, close):
        """ Plots a chart. """
        self.get_day_data(data)
        plt.xticks(fontsize=18)
        plt.yticks(fontsize=18)
        plt.rcParams['font.size'] = '18'
        plt.rcParams['agg.path.chunksize'] = 10000
        _fig, axis = plt.subplots(figsize=(13, 9))
        plt.xticks(fontsize=18)
        plt.yticks(fontsize=18)
        #  plt.plot(self.data.index[firstindex:lastindex], self.data[firstindex:lastindex], color="green", linewidth=5.0)
        if abs(max(data)) > 1.5E3:
            data = data * 1E-3
            self.ylabel = f"k{self.ylabel}"
        #  self.line1 =
        plt.plot(data.index, data, color="green", linewidth=5.0, label=self.property)
        plt.grid(True)
        #  plt.xticks(fontsize=18)
        #  plt.yticks(fontsize=18)
        self.axis.set_ylabel(self.ylabel)
        plt.xlabel("Time [hours]", fontsize=18)  # fontsize=18)
        #  plt.title("{}".format(self.plot_title), fontsize=20)
        self.axis.xaxis.set_major_formatter(DateFormatter("%H:%M"))
        if close:
            self.close()
