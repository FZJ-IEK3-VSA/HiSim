""" Contains all the chart classes. """
# clean
import gc
from typing import Any
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np

from hisim import log
from hisim.postprocessing.chartbase import Chart
from hisim import utils
mpl.rcParams['agg.path.chunksize'] = 10000


class Carpet(Chart):  # noqa: too-few-public-methods

    """ Class for carpet plots. """

    def __init__(self, output: Any, units: Any, directorypath: str, time_correction_factor: float) -> None:
        """ Initalizes a carpot plot. """
        super().__init__(output=output,
                         chart_type="Carpet",
                         units=units,
                         directory_path=directorypath,
                         time_correction_factor=time_correction_factor)

    def plot(self, xdims: int, data: Any) -> None:
        """ Makes a carpet plot. """
        log.trace("starting carpet plots")
        ydims = int(len(data) / xdims)  # number of calculated timesteps per day
        y_steps_per_hour = int(ydims / 24)
        try:
            database = data.values.reshape(xdims, ydims)
        except ValueError:
            log.error("Carpet plot can only deal with data containing entire days")
            return
        if np.max(np.abs(data.values)) > 1.5E3:
            database = database * 1E-3
            self.units = f"k{self.units}"
        plot_data = np.flip(database.transpose(), axis=0)

        fig = plt.figure(figsize=(10, 5), dpi=500)

        axis = fig.add_subplot(111)
        mycolors = 'viridis'
        color_map = plt.cm.get_cmap(mycolors)

        plot = axis.pcolormesh(plot_data, cmap=color_map)
        plt.colorbar(plot).set_label(self.units)

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
        axis.set_ylabel('Daytime [h]')
        axis.set_xlabel('Month of the year')

        log.trace("finished carpet plot: " + self.filepath)
        plt.savefig(self.filepath, bbox_inches='tight')
        plt.close()


class Line(Chart):  # noqa: too-few-public-methods

    """ Makes a line chart. """

    # @utils.measure_memory_leak
    def __init__(self, output, units, directorypath, time_correction_factor):
        """ Initializes a line chart. """
        super().__init__(output, "line", units, directorypath, time_correction_factor)

    @utils.measure_memory_leak
    def plot(self, data, units):
        """ Makes a line plot. """
        all_font_size = 40
        size_1 = 20
        size_2 = 18
        mpl.use('Agg')

        ylabel = units
        _fig, axis = plt.subplots(figsize=(size_1, size_2))
        x_zero = data.index
        plt.xticks(fontsize=35, rotation=20)
        plt.yticks(fontsize=35)

        # Rescale values in case they are too high
        if max(abs(data)) > 1.5E3 and units != "-":
            data = data * 1E-3
            units = f"k{units}"

        plt.plot(x_zero, data, color="green", linewidth=6.0)
        plt.ylabel(ylabel, fontsize=all_font_size)
        plt.ylabel(f"[{units}]", fontsize=40)
        plt.xlabel("Time", fontsize=all_font_size)
        plt.grid()
        axis.set_xlim(xmin=x_zero[0])
        plt.savefig(self.filepath)
        plt.cla()
        plt.clf()
        plt.close("all")
        del x_zero
        gc.collect(2)


class BarChart(Chart):  # noqa: too-few-public-methods

    """ Makes Bar charts. """

    original = [385.66, 484.01, 981.05, 1096.7, 1157, 1299.9, 1415.3, 1266.1, 1075.8, 714.44, 422.51, 366.83]

    def __init__(self, output, units, dirpath, time_correction_factor):
        """ Initializes the classes. """
        super().__init__(output, "Bar", units, dirpath, time_correction_factor)
        self.filename = f"monthly_{self.output}.png"

    def plot(self, data):
        """ Plots the bar chart. """
        width = 0.35
        # Specify the values of blue bars (height)

        # Position of bars on x-axis
        ind = np.arange(12)

        # Figure size

        # Width of a bar
        width = 0.4

        plt.subplots()
        plt.bar(ind, data * 1E-3, width, label="HiSim")
        plt.bar(ind + width, self.original, width, label="PVSOL")

        plt.xticks(ind + width / 2)

        plt.title(f"{self.title} Monthly")
        plt.grid()
        plt.tight_layout()
        plt.ylabel(self.units)
        plt.legend(loc='best')
        plt.savefig(self.filepath, bbox_inches='tight')
        plt.close()


class SankeyHISIM(Chart):

    """ Class for sankey charts. """

    def __init__(self,
                 name,
                 units,
                 directorypath,
                 time_correction_factor):
        """ Initializes the Sankey chart. """
        super().__init__(output=name,
                         chart_type="Sankey",
                         units=units,
                         directory_path=directorypath,
                         time_correction_factor=time_correction_factor)
        self.filename = f"{self.output}.png"

    def plot(self, data):
        """ Executes the plot. """
        components = {}
        for _index, output_result in enumerate(data):
            if self.output == output_result.display_name:
                if output_result.sankey_flow_direction is True:
                    components[output_result.component_name] = round(sum(output_result.Results) * 1E-3)
                elif output_result.sankey_flow_direction is False:
                    components[output_result.component_name] = - round(sum(output_result.Results) * 1E-3)

        # if components:
        flows = []
        flows_labels = []
        for key in components.items():
            if isinstance(components[key], bool) is False:
                flows.append(components[key])
                flows_labels.append(f"{key} kWh")

        orientations = self.make_orientations(flows)

        pathlengths = 0.4
        fig = plt.figure(figsize=[10, 10])
        axis = fig.add_subplot(1, 1, 1, xticks=[], yticks=[])

        sankey = mpl.sankey.Sankey(ax=axis, scale=5E-5, offset=3E-1, head_angle=100, margin=0.4)
        sankey.add(flows=flows,
                   labels=flows_labels,
                   orientations=orientations,
                   pathlengths=pathlengths)
        sankey.finish()
        plt.title(self.title, fontsize=18)
        plt.axis("off")
        plt.savefig(self.filepath)
        plt.close()

    def make_orientations(self, flows):
        """ Counts the orientations. """
        i_positive = 0
        i_negative = 0
        indices = [0, 1, -1]
        orientations = []
        for _index, flow in enumerate(flows):
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
        return orientations

    def plot_heat_pump(self, data):
        """ Plots a sankey for the Heat Pump. """
        electricity_consumption = 0

        for _index, output_result in enumerate(data):
            if output_result.component_name == "HeatPump":
                if output_result.display_name == "ElectricityOutput":
                    electricity_consumption = sum(output_result.Results)
                if output_result.display_name == "Heating":
                    heating = sum(output_result.Results)
                if output_result.display_name == "Cooling":
                    cooling = sum(output_result.Results)
        if cooling < 0:
            cooling = abs(cooling)
        thermal_energy_delivered = heating + cooling
        thermal_energy_extracted = thermal_energy_delivered - electricity_consumption

        flows = [electricity_consumption * 1E-3,
                 thermal_energy_extracted * 1E-3,
                 -thermal_energy_delivered * 1E-3]
        flows = [round(i) for i in flows]
        flows_labels = ['Electricity\nConsumption',
                        'Thermal Energy\nExtracted',
                        'Thermal Energy\nDelivered']
        orientations = [1, -1, 0]
        pathlengths = 0.25

        fig = plt.figure(figsize=[10, 10])
        axis = fig.add_subplot(1, 1, 1, xticks=[], yticks=[])

        sankey = mpl.sankey.Sankey(ax=axis, scale=5E-5, offset=3E-1, head_angle=100, margin=0.4)
        sankey.add(flows=flows,
                   labels=flows_labels,
                   orientations=orientations,
                   pathlengths=pathlengths)
        sankey.finish()
        plt.title("Heap Pump Energy Equilibrium", fontsize=18)
        plt.axis("off")
        plt.savefig(self.filepath)
        plt.close()

    def plot_building(self, data):
        """ Sankey of Thermal Equilibrium from Residence. """
        heating_by_residents, internal_loss, solar_gain_through_windows, \
            stored_energy_variation, thermal_energy_delivered, total_energy_to_residence = self.calculate_metrics(data)

        # If the
        if abs(stored_energy_variation) < 100 * 1E3:
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

            flows = [heating_by_residents * 1E-3,
                     solar_gain_through_windows * 1E-3,
                     thermal_energy_delivered * 1E-3,
                     -stored_energy_variation * 1E-3,
                     -internal_loss * 1E-3
                     ]
            flows = [round(i) for i in flows]
            flows_labels = ['Heating\nby Residents',
                            'Solar\nGain',
                            'Heater',
                            'GainedEnergy',
                            'Heat\nLoss']

            orientations = [1, 0, -1, -1, 1]
        pathlengths = 0.4

        fig = plt.figure(figsize=[10, 10])
        axis = fig.add_subplot(1, 1, 1, xticks=[], yticks=[])

        sankey = mpl.sankey.Sankey(ax=axis, scale=5E-5, offset=3E-1, head_angle=100, margin=0.4)
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

    def calculate_metrics(self, data):
        """ Calculates the metrics for the sankey chart. """
        heating_by_residents = 0
        total_energy_to_residence = 0
        solar_gain_through_windows = 0
        for _index, output_result in enumerate(data):
            if output_result.component_name == "Occupancy" and output_result.display_name == "HeatingByResidents":
                heating_by_residents = sum(output_result.Results)
            if output_result.component_name == "HeatPump" and output_result.display_name == "ThermalEnergyDelivered":
                thermal_energy_delivered = sum(output_result.Results)
            if output_result.component_name == "Building":
                if output_result.display_name == "TotalEnergyToResidence":
                    total_energy_to_residence = sum(output_result.Results)
                if output_result.display_name == "SolarGainThroughWindows":
                    solar_gain_through_windows = sum(output_result.Results)
                if output_result.display_name == "InternalLoss":
                    internal_loss = sum(output_result.Results)
                if output_result.display_name == "StoredEnergyVariation":
                    stored_energy_variation = sum(output_result.Results)
        if heating_by_residents == 0 or total_energy_to_residence == 0 or solar_gain_through_windows == 0:
            raise Exception("Sum of outputs has not been calculated.")
        return heating_by_residents, internal_loss, solar_gain_through_windows, \
            stored_energy_variation, thermal_energy_delivered, total_energy_to_residence
