"""Sankey plots in chart module."""

import matplotlib as mpl
import matplotlib.pyplot as plt
from timeit import default_timer as timer
from hisim.postprocessing.chartbase import Chart, ChartFontsAndSize
from hisim.components import building
from hisim.components import generic_heat_pump_modular
from hisim.components import generic_heat_pump
from hisim.components import advanced_heat_pump_hplib

from hisim import log
from hisim import utils
from hisim import loadtypes as lt
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.postprocessing.postprocessing_datatransfer import PostProcessingDataTransfer
from obsolete import loadprofilegenerator_connector


class SankeyHISIM(Chart, ChartFontsAndSize):

    """Class for sankey charts."""

    def __init__(
        self,
        name,
        component_name,
        units,
        directorypath,
        time_correction_factor,
        output_description,
        figure_format,
    ):
        """Initializes the Sankey chart."""
        super().__init__(
            output_description=output_description,
            output=name,
            component_name=component_name,
            chart_type="Sankey",
            units=units,
            directory_path=directorypath,
            time_correction_factor=time_correction_factor,
            figure_format=figure_format,
        )
        self.filename = f"{self.output}{self.figure_format}"

    def plot(self, data):
        """Executes the plot."""
        components = {}
        for _index, output_result in enumerate(data):
            if self.output == output_result.display_name:
                if output_result.sankey_flow_direction is True:
                    components[output_result.component_name] = round(
                        sum(output_result.Results) * 1e-3
                    )
                elif output_result.sankey_flow_direction is False:
                    components[output_result.component_name] = -round(
                        sum(output_result.Results) * 1e-3
                    )

        # if components:
        flows = []
        flows_labels = []
        for key in components.items():
            if isinstance(components[key], bool) is False:
                flows.append(components[key])
                flows_labels.append(f"{key} kWh")

        orientations = self.make_orientations(flows)

        pathlengths = 0.4
        fig = plt.figure(figsize=self.figsize, dpi=self.dpi)
        axis = fig.add_subplot(1, 1, 1, xticks=[], yticks=[])

        sankey = mpl.sankey.Sankey(
            ax=axis, scale=5e-5, offset=3e-1, head_angle=100, margin=0.4
        )
        sankey.add(
            flows=flows,
            labels=flows_labels,
            orientations=orientations,
            pathlengths=pathlengths,
        )
        sankey.finish()
        plt.title(self.title, fontsize=self.fontsize_title)
        plt.xticks(fontsize=self.fontsize_ticks)
        plt.yticks(fontisze=self.fontsize_ticks)
        plt.axis("off")
        # plt.savefig(self.filepath)
        plt.savefig(self.filepath2)
        plt.close()

    def make_orientations(self, flows):
        """Counts the orientations."""
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
        """Plots a sankey for the Heat Pump."""
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

        flows = [
            electricity_consumption * 1e-3,
            thermal_energy_extracted * 1e-3,
            -thermal_energy_delivered * 1e-3,
        ]
        flows = [round(i) for i in flows]
        flows_labels = [
            "Electricity\nConsumption",
            "Thermal Energy\nExtracted",
            "Thermal Energy\nDelivered",
        ]
        orientations = [1, -1, 0]
        pathlengths = 0.25

        fig = plt.figure(figsize=self.figsize, dpi=self.dpi)
        axis = fig.add_subplot(1, 1, 1, xticks=[], yticks=[])

        sankey = mpl.sankey.Sankey(
            ax=axis, scale=5e-5, offset=3e-1, head_angle=100, margin=0.4
        )
        sankey.add(
            flows=flows,
            labels=flows_labels,
            orientations=orientations,
            pathlengths=pathlengths,
        )
        sankey.finish()
        plt.title("Heap Pump Energy Equilibrium", fontsize=self.fontsize_title)
        plt.xticks(fontsize=self.fontsize_ticks)
        plt.yticks(fontisze=self.fontsize_ticks)
        plt.axis("off")
        # plt.savefig(self.filepath)
        plt.savefig(self.filepath2)
        plt.close()

    def plot_building(self, data):
        """Sankey of Thermal Equilibrium from Residence."""
        (
            heating_by_residents,
            internal_loss,
            solar_gain_through_windows,
            stored_energy_variation,
            thermal_energy_delivered,
            total_energy_to_residence,
        ) = self.calculate_metrics(data)

        # If the
        if abs(stored_energy_variation) < 100 * 1e3:
            flows = [
                heating_by_residents * 1e-3,
                solar_gain_through_windows * 1e-3,
                thermal_energy_delivered * 1e-3,
                -internal_loss * 1e-3,
            ]
            flows = [round(i) for i in flows]
            flows_labels = [
                "Heating\nby Residents",
                "Solar\nGain",
                "Heater",
                "Heat\nLoss",
            ]
            orientations = [1, 0, -1, 0]
        else:

            flows = [
                heating_by_residents * 1e-3,
                solar_gain_through_windows * 1e-3,
                thermal_energy_delivered * 1e-3,
                -stored_energy_variation * 1e-3,
                -internal_loss * 1e-3,
            ]
            flows = [round(i) for i in flows]
            flows_labels = [
                "Heating\nby Residents",
                "Solar\nGain",
                "Heater",
                "GainedEnergy",
                "Heat\nLoss",
            ]

            orientations = [1, 0, -1, -1, 1]
        pathlengths = 0.4

        fig = plt.figure(figsize=self.figsize, dpi=self.dpi)
        axis = fig.add_subplot(1, 1, 1, xticks=[], yticks=[])

        sankey = mpl.sankey.Sankey(
            ax=axis, scale=5e-5, offset=3e-1, head_angle=100, margin=0.4
        )
        sankey.add(
            flows=flows,
            labels=flows_labels,
            orientations=orientations,
            pathlengths=pathlengths,
        )
        sankey.finish()
        plt.title(
            "Residence Annual Thermal Equilibrium [kWh]", fontsize=self.fontsize_title
        )
        plt.axis("off")
        # plt.savefig(self.filepath)
        plt.savefig(self.filepath2)
        plt.close()

        flows = [
            heating_by_residents * 1e-3,
            solar_gain_through_windows * 1e-3,
            thermal_energy_delivered * 1e-3,
            stored_energy_variation * 1e-3,
            internal_loss * 1e-3,
            total_energy_to_residence * 1e-3,
        ]

    def calculate_metrics(self, data):
        """Calculates the metrics for the sankey chart."""
        heating_by_residents = 0
        total_energy_to_residence = 0
        solar_gain_through_windows = 0
        internal_loss = 0
        stored_energy_variation = 0
        thermal_energy_delivered = 0
        for _index, output_result in enumerate(data):
            if (
                output_result.component_name == "Occupancy"
                and output_result.display_name
                == loadprofilegenerator_connector.Occupancy.HeatingByResidents
            ):
                heating_by_residents = sum(output_result.Results)
            if output_result.component_name == "HeatPump" and (
                output_result.display_name
                in (
                    generic_heat_pump_modular.ModularHeatPump.ThermalPowerDelivered,
                    generic_heat_pump.GenericHeatPump.ThermalPowerDelivered,
                    advanced_heat_pump_hplib.HeatPumpHplib.ThermalOutputPower,
                )
            ):
                thermal_energy_delivered = sum(output_result.Results)
            if output_result.component_name == "Building":
                if (
                    output_result.display_name
                    == building.Building.TotalEnergyToResidence
                ):
                    total_energy_to_residence = sum(output_result.Results)
                if (
                    output_result.display_name
                    == building.Building.SolarGainThroughWindows
                ):
                    solar_gain_through_windows = sum(output_result.Results)
                if output_result.display_name == building.Building.InternalLoss:
                    internal_loss = sum(output_result.Results)
                if (
                    output_result.display_name
                    == building.Building.StoredEnergyVariation
                ):
                    stored_energy_variation = sum(output_result.Results)
        if (
            heating_by_residents == 0
            or total_energy_to_residence == 0
            or solar_gain_through_windows == 0
        ):
            raise ValueError("Sum of outputs has not been calculated.")
        return (
            heating_by_residents,
            internal_loss,
            solar_gain_through_windows,
            stored_energy_variation,
            thermal_energy_delivered,
            total_energy_to_residence,
        )


"""Sankey Plot implemenetation in postprocessing main module."""


def make_sankey_plots() -> None:
    """Makes Sankey plots. Needs work."""
    log.information("Plotting sankeys.")
    # TODO:   self.plot_sankeys()


# Plot sankey
@utils.measure_execution_time
@utils.measure_memory_leak
def run(ppdt: PostProcessingDataTransfer) -> None:  # noqa: MC0001
    if PostProcessingOptions.PLOT_SANKEY in ppdt.post_processing_options:
        log.information("Making sankey plots.")
        start = timer()
        make_sankey_plots()
        end = timer()
        duration = end - start
        log.information("Making sankey plots took " + f"{duration:1.2f}s.")


@utils.measure_execution_time
def plot_sankeys(ppdt: PostProcessingDataTransfer) -> None:
    """For plotting the sankeys."""
    for i_display_name in [
        name for name, display_name in lt.DisplayNames.__members__.items()
    ]:
        my_sankey = SankeyHISIM(
            name=i_display_name,
            component_name=i_display_name,
            output_description=None,
            units=lt.Units.ANY,
            directorypath=ppdt.simulation_parameters.result_directory,
            time_correction_factor=ppdt.time_correction_factor,
            figure_format=ppdt.simulation_parameters.figure_format,
        )
        my_sankey.plot(data=ppdt.all_outputs)
    if any(
        component_output.component_name == "HeatPump"
        for component_output in ppdt.all_outputs
    ):
        my_sankey = SankeyHISIM(
            name="HeatPump",
            component_name="HeatPump",
            output_description=None,
            units=lt.Units.ANY,
            directorypath=ppdt.simulation_parameters.result_directory,
            time_correction_factor=ppdt.time_correction_factor,
            figure_format=ppdt.simulation_parameters.figure_format,
        )
        my_sankey.plot_heat_pump(data=ppdt.all_outputs)
    if any(
        component_output.component_name == "Building"
        for component_output in ppdt.all_outputs
    ):
        my_sankey = SankeyHISIM(
            name="Building",
            component_name="Building",
            output_description=None,
            units=lt.Units.ANY,
            directorypath=ppdt.simulation_parameters.result_directory,
            time_correction_factor=ppdt.time_correction_factor,
            figure_format=ppdt.simulation_parameters.figure_format,
        )
        my_sankey.plot_building(data=ppdt.all_outputs)
