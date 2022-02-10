import os
import matplotlib.pyplot as plt
from matplotlib.dates import DateFormatter

from hisim.postprocessing.chartbase import Chart

class ChartSingleDay(Chart):
    def __init__(self, output, data, units, directorypath, time_correction_factor, day=None, month=None, output2=None):
        if output2 is not None:
            super().__init__(output, data, "days", units, directorypath, time_correction_factor, output2)
        else:
            super().__init__(output, data, "days", units, directorypath, time_correction_factor)
        self.ax2 : plt.axis
        self.line2: plt.axis
        self.month = month
        self.day = day
        self.filename = "{}_{}_{}_m{}_d{}.png".format(self.type.lower(),
                                                      self.output.split(' # ', 2)[1],
                                                      self.output.split(' # ', 2)[0],
                                                      self.month,
                                                      self.day)
        self.filepath = os.path.join(self.directorypath, self.filename)
        self.get_day_data()

    def get_day_data(self):
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
        date = "{} {}{}".format(self.label_months_lowercase[self.month], day_number, ordinal)
        self.plot_title = "{} {}".format(self.title, date)

        if abs(lastindex - firstindex) < len(self.data):
            data = self.data[firstindex:lastindex]
            data_index = self.data.index[firstindex:lastindex]
            self.data = data
            self.data.index = data_index



    def __add__(self, other):
        my_double:ChartSingleDay = ChartSingleDay(self.output,
                        self.data,
                        self.ylabel,
                        self.directorypath,
                        self.time_correction_factor,
                        self.day,
                        self.month)
        my_double.filename = "{}_{}_{}_AND_{}_{}_m{}_d{}.png".format(self.type.lower(),
                                                                     self.output.split(' # ', 2)[1],
                                                                     self.output.split(' # ', 2)[0],
                                                                     other.output.split(' # ', 2)[1],
                                                                     other.output.split(' # ', 2)[0],
                                                                     self.month,
                                                                     self.day)
        my_double.filepath = os.path.join(self.directorypath, my_double.filename)
        my_double.plot(close=False)

        # twin object for two different y-axis on the sample plot
        my_double.ax2 = my_double.ax.twinx()
        # make a plot with different y-axis using second axis object
        my_double.line2 = my_double.ax2.plot(my_double.data.index, other.data, label=other.property, linewidth=5)
        my_double.ax2.set_ylabel("{} [{}]".format(other.property,other.ylabel),fontsize=18)
        #seaborn.despine(ax=my_double.ax2, offset=0)  # the important part here
        my_double.ax2.xaxis.set_major_formatter(DateFormatter("%H:%M"))
        return my_double

    def close(self):
        plt.xticks(fontsize=18)
        plt.yticks(fontsize=18)
        #if hasattr(self,"line2"):
        #    lines = self.line1 + self.line2
        #    labs = [l.get_label() for l in lines]
        #    self.ax.legend(lines, labs, loc=0)
        self.ax.xaxis.set_major_formatter(DateFormatter("%H:%M"))
        self.ax.set_ylabel("{} [{}]".format(self.property,self.ylabel),fontsize=18)
        if hasattr(self,"line2"):
            self.ax2.xaxis.set_major_formatter(DateFormatter("%H:%M"))
        #plt.savefig(self.filepath, bbox_inches='tight')
        plt.savefig(self.filepath)
        plt.close()

    def plot(self, close=True):
        plt.xticks(fontsize=18)
        plt.yticks(fontsize=18)
        plt.rcParams['font.size'] = '18'
        plt.rcParams['agg.path.chunksize'] = 10000
        self.fig, self.ax = plt.subplots(figsize=(13, 9))
        plt.xticks(fontsize=18)
        plt.yticks(fontsize=18)
        #plt.plot(self.data.index[firstindex:lastindex], self.data[firstindex:lastindex], color="green", linewidth=5.0)
        if abs(max(self.data)) > 1.5E3:
            self.data = self.data * 1E-3
            self.ylabel = "k{}".format(self.ylabel)
        self.line1 = plt.plot(self.data.index, self.data, color="green", linewidth=5.0, label=self.property)
        plt.grid(True)
        #plt.xticks(fontsize=18)
        #plt.yticks(fontsize=18)
        self.ax.set_ylabel(self.ylabel)
        plt.xlabel("Time [hours]", fontsize=18)  # fontsize=18)
        #plt.title("{}".format(self.plot_title), fontsize=20)
        self.ax.xaxis.set_major_formatter(DateFormatter("%H:%M"))
        if close:
            self.close()