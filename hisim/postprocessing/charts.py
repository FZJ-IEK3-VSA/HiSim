import logging
import os
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
#from matplotlib.sankey import Sankey
import matplotlib as mpl
from matplotlib.dates import DateFormatter
import matplotlib
import seaborn
import numpy as np
import pandas as pd
import re
import warnings
warnings.filterwarnings("ignore")
mpl.rcParams['agg.path.chunksize'] = 10000

class Chart:
    """
    Parent class for plots to be exported.
    """
    months_abbrev_uppercase = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEZ']
    label_months_lowercase = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']

    def __init__(self, output, data, type, units, directorypath, time_correction_factor, output2=None):
        self.output = output
        self.data = data
        self.type = type
        if hasattr(units, "value"):
            self.units = units.value
            self.ylabel = units.value
        else:
            self.units = units
            self.ylabel = units
        self.time_correction_factor = time_correction_factor

        self.title = ""
        matches = re.finditer('.+?(?:(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|$|#)', self.output)
        matches = [m.group(0) for m in matches]
        pass_sign = False
        property = ""
        object = ""
        for m in matches:
            if pass_sign:
                property = "{} {}".format(property,m)
            else:
                object = "{}{}".format(object,m)

            if m.find("#"):
                pass_sign = True

            if len(self.title) == 0:
                self.title = m
            else:
                self.title = "{} {}".format(self.title, m)
        self.directorypath = directorypath

        self.object_name = " "
        self.property = property
        if output2 is not None:
            self.output2 = output2
            self.filename = "{}_{}_{}double.png".format(self.type.lower(),
                                                        self.output.split(' # ', 2)[1],
                                                        self.output.split(' # ', 2)[0])
        else:
            self.filename = "{}_{}_{}.png".format(self.type.lower(),
                                                  self.output.split(' # ', 2)[1],
                                                  self.output.split(' # ', 2)[0])
        self.filepath = os.path.join(self.directorypath, self.filename)

class Carpet(Chart):
    def __init__(self, output, data, units, directorypath, time_correction_factor):
        super().__init__(output=output,
                         data=data,
                         type="Carpet",
                         units=units,
                         directorypath=directorypath,
                         time_correction_factor=time_correction_factor)
    def plot(self):
        if(len(self.data.index) != 365*24):
            logging.error("Carpet plot can only deal with data for 365 days in 1h resolution")
            return
        xdims = 365 #number of days
        ydims = int( len( self.data ) / 365 ) #number of calculated timesteps per day
        y_steps_per_hour = int( ydims / 24 )
        database = self.data.values.reshape( xdims, ydims )
        if np.max(np.abs(self.data.values)) > 1.5E3:
            database = database * 1E-3
            self.units = "k{}".format(self.units)
        plot_data = np.flip(database.transpose(), axis=0)
        # plot_data = database

        # sns.set(font_scale=float(1.5))
        fig = plt.figure(figsize=(10, 5), dpi=500)

        ax = fig.add_subplot(111)
        mycolors = 'viridis'
        cm = plt.cm.get_cmap(mycolors)

        plot = ax.pcolormesh(plot_data, cmap=cm)
        plt.colorbar(plot).set_label(self.units)


        y_ticks = np.arange(0, 25 * y_steps_per_hour, 6 * y_steps_per_hour ).tolist()
        # y_ticks = np.arange(0, 25 * 60, 6 * 60).tolist()
        ax.set_yticks(y_ticks)
        y_ticks_labels = np.flip(list(range(0, 25, 6)), axis=0)
        ax.set_yticklabels([str(i) for i in y_ticks_labels])

        x_ticks = np.arange(15, 346, 30).tolist()
        ax.set_xticks(x_ticks)
        ax.set_xticklabels([str(i) for i in self.months_abbrev_uppercase])

        # optimizing fonts
        fig.autofmt_xdate(rotation=45)
        # setting axis of the plot
        ax.set_ylabel('Daytime [h]')
        ax.set_xlabel('Month of the year')
        #plt.title(self.title)
        # ax.set_ylabel('Daytime [h]', fontdict={'size': 14})
        # ax.set_xlabel('Month of the year', fontsize=14)

        # plt.show()

        plt.savefig(self.filepath, bbox_inches='tight')
        plt.close()

class Line(Chart):
    def __init__(self, output, data , units, directorypath, time_correction_factor):
        super().__init__(output, data, "line", units, directorypath, time_correction_factor)

    def plot(self):
        all_font_size = 40
        size_1 = 20
        size_2 = 18

        font = {'family': 'normal',
                'weight': 'normal',
                'size': '{}'.format(all_font_size)}
        matplotlib.rc('font', **font)

        ylabel = self.units
        fig, ax = plt.subplots(figsize=(size_1, size_2))
        xo = self.data.index
        plt.xticks(fontsize=35, rotation=20)
        plt.yticks(fontsize=35)

        # Rescale values in case they are too high
        if max(abs(self.data)) > 1.5E3 and self.units != "-":
            self.data = self.data * 1E-3
            self.units = "k{}".format(self.units)

        plt.plot(xo, self.data, color="green", linewidth=6.0)
        plt.ylabel(ylabel, fontsize=all_font_size)
        plt.ylabel("[{}]".format(self.units), fontsize=40)
        plt.xlabel("Time", fontsize=all_font_size)
        plt.grid()
        ax.set_xlim(xmin=xo[0])
        plt.savefig(self.filepath)
        plt.close()

class Day(Chart):
    def __init__(self, output, data, units, directorypath, time_correction_factor, day=None, month=None, output2=None):
        if output2 is not None:
            super().__init__(output, data, "days", units, directorypath, time_correction_factor, output2)
        else:
            super().__init__(output, data, "days", units, directorypath, time_correction_factor)
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
        my_double = Day(self.output,
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

class Bar(Chart):
    original = [385.66, 484.01, 981.05, 1096.7, 1157, 1299.9, 1415.3, 1266.1, 1075.8, 714.44, 422.51, 366.83]

    def __init__(self, output, data , units, dirpath, time_correction_factor):
        super().__init__(output, data, "Bar", units, dirpath, time_correction_factor)
        self.filename = "montly_{}.png".format(self.output)

    def plot(self):
        width = 0.35
        # Specify the values of blue bars (height)

        # Position of bars on x-axis
        ind = np.arange(12)

        # Figure size

        # Width of a bar
        width = 0.4

        fig, ax = plt.subplots()
        plt.bar(ind, self.data * 1E-3, width, label="HiSim")
        plt.bar(ind+width, self.original, width, label="PVSOL")

        plt.xticks(ind + width / 2, self.months_short)

        plt.title("{} Monthly".format(self.title))
        plt.grid()
        # seaborn.despine(ax=ax, offset=0)  # the important part here
        # autolabel(rect)
        plt.tight_layout()
        plt.ylabel(self.units)
        plt.legend(loc='best')
        plt.savefig(self.filepath, bbox_inches='tight')
        plt.close()

class SankeyHISIM(Chart):
    def __init__(self,
                 name,
                 data,
                 units,
                 directorypath,
                 time_correction_factor):
        super().__init__(output=name,
                         data=data,
                         type="Sankey",
                         units=units,
                         directorypath=directorypath,
                         time_correction_factor=time_correction_factor)
        self.filename = "{}.png".format(self.output)

    def plot(self):
        components = {}
        common_unit = []
        for index, output_result in enumerate(self.data):
            if self.output == output_result.DisplayName:
                if output_result.SankeyFlowDirection is True:
                    components[output_result.ObjectName] = round(sum(output_result.Results) * 1E-3)
                elif output_result.SankeyFlowDirection is False:
                    components[output_result.ObjectName] = - round(sum(output_result.Results) * 1E-3)

        if components:
            flows = []
            flows_labels = []
            for key in components:
                if isinstance(components[key], bool) is False:
                    flows.append(components[key])
                    flows_labels.append("{} kWh".format(key))

            i_positive = 0
            i_negative = 0
            indices = [0, 1, -1]
            orientations = []
            for index, flow in enumerate(flows):
                if flow > 0:
                    orientations.append(indices[i_positive])
                    i_positive = i_positive + 1
                else:
                    orientations.append(indices[i_negative])
                    i_negative = i_negative + 1
                if i_positive >= 3:
                    i_positive = 0
                if i_negative >= 3:
                    i_negative = 0

            pathlengths = 0.4
            #plt.rcParams['font.size'] = 12
            fig = plt.figure(figsize=[10, 10])
            ax = fig.add_subplot(1, 1, 1, xticks=[], yticks=[])

            sankey = matplotlib.sankey.Sankey(ax=ax, scale=5E-5, offset=3E-1, head_angle=100, margin=0.4)
            sankey.add(flows=flows,
                       labels=flows_labels,
                       orientations=orientations,
                       pathlengths=pathlengths)
            sankey.finish()
            plt.title(self.title, fontsize=18)
            plt.axis("off")
            plt.savefig(self.filepath)
            plt.close()

    def plot_heat_pump(self):
        # Heat Pump
        electricity_consumption = 0

        for index, output_result in enumerate(self.data):
            if output_result.ObjectName == "HeatPump":
                if "ElectricityOutput" == output_result.DisplayName:
                    electricity_consumption = sum(output_result.Results)
                if "Heating" == output_result.DisplayName:
                    heating = sum(output_result.Results)
                if "Cooling" == output_result.DisplayName:
                    cooling = sum(output_result.Results)
        if cooling < 0:
            cooling = abs(cooling)
        thermal_energy_delivered = heating + cooling
        thermal_energy_extracted = thermal_energy_delivered - electricity_consumption


        flows = [electricity_consumption*1E-3,
                 thermal_energy_extracted*1E-3,
                 -thermal_energy_delivered*1E-3]
        flows = [round(i) for i in flows]
        flows_labels = ['Electricity\nConsumption',
                        'Thermal Energy\nExtracted',
                        'Thermal Energy\nDelivered']
        orientations = [1, -1, 0]
        pathlengths = 0.25


        #plt.rcParams['font.size'] = 12
        fig = plt.figure(figsize=[10,10])
        ax = fig.add_subplot(1, 1, 1, xticks=[], yticks=[])

        sankey = matplotlib.sankey.Sankey(ax=ax, scale=5E-5, offset=3E-1, head_angle=100, margin=0.4)
        sankey.add(flows=flows,
                   labels=flows_labels,
                   orientations= orientations,
                   pathlengths=pathlengths)
        sankey.finish()
        plt.title("Heap Pump Energy Equilibrium", fontsize=18)
        plt.axis("off")
        plt.savefig(self.filepath)
        plt.close()

    def plot_building(self):
        # Sankey of Thermal Equilibrium from Residence
        heating_by_residents = 0
        total_energy_to_residence = 0
        solar_gain_through_windows = 0

        for index, output_result in enumerate(self.data):
            if output_result.ObjectName == "Occupancy":
                if "HeatingByResidents" == output_result.DisplayName:
                    heating_by_residents = sum(output_result.Results)
            if output_result.ObjectName == "HeatPump":
                if "ThermalEnergyDelivered" == output_result.DisplayName:
                    thermal_energy_delivered = sum(output_result.Results)
            if output_result.ObjectName == "Building":
                if "TotalEnergyToResidence" == output_result.DisplayName:
                    total_energy_to_residence = sum(output_result.Results)
                if "SolarGainThroughWindows" == output_result.DisplayName:
                    solar_gain_through_windows = sum(output_result.Results)
                if "InternalLoss" == output_result.DisplayName:
                    internal_loss = sum(output_result.Results)
                if "StoredEnergyVariation" == output_result.DisplayName:
                    stored_energy_variation = sum(output_result.Results)
                if "OldStoredEnergy" == output_result.DisplayName:
                    stored_energy_initial = sum(output_result.Results)
                if "TemperatureMean" == output_result.DisplayName:
                    current_mean_temperature = sum(output_result.Results)

        if heating_by_residents == 0 or total_energy_to_residence == 0 or solar_gain_through_windows == 0:
            raise Exception("Sum of outputs has not been calculated.")

        # If the
        if abs(stored_energy_variation) < 100*1E3:
            flows = [heating_by_residents * 1E-3,
                     solar_gain_through_windows * 1E-3,
                     thermal_energy_delivered * 1E-3,
                     -internal_loss * 1E-3]
            flows = [round(i) for i in flows]
            flows_labels = ['Heating\nby Residents',
                            'Solar\nGain',
                            'Heater',
                            'Heat\nLoss']
            orientations = [1, 0, -1, 0]
        else:

            flows = [heating_by_residents*1E-3,
                     solar_gain_through_windows*1E-3,
                     thermal_energy_delivered*1E-3,
                     -stored_energy_variation*1E-3,
                     -internal_loss*1E-3
                     ]
            flows = [round(i) for i in flows]
            residence_flows = flows
            #if stored_energy_variation < 1000:
            #    stored_energy_variation = 0
            flows_labels = ['Heating\nby Residents',
                            'Solar\nGain',
                            'Heater',
                            'GainedEnergy',
                            'Heat\nLoss']

            orientations = [1, 0, -1, -1, 1]
        pathlengths = 0.4

        #plt.rcParams['font.size'] = 12
        fig = plt.figure(figsize=[10,10])
        ax = fig.add_subplot(1, 1, 1, xticks=[], yticks=[])

        sankey = matplotlib.sankey.Sankey(ax=ax, scale=5E-5, offset=3E-1, head_angle=100, margin=0.4)
        sankey.add(flows=flows,
                   labels=flows_labels,
                   orientations=orientations,
                   pathlengths=pathlengths)
        sankey.finish()
        plt.title("Residence Annual Thermal Equilibrium [kWh]", fontsize=18)
        plt.axis("off")
        plt.savefig(self.filepath)
        plt.close()

        flows = [heating_by_residents * 1E-3,
                 solar_gain_through_windows * 1E-3,
                 thermal_energy_delivered * 1E-3,
                 stored_energy_variation * 1E-3,
                 internal_loss * 1E-3,
                 total_energy_to_residence * 1E-3
                 ]
