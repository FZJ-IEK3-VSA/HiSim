""" Main postprocessing module that starts all other modules. """

import os
import sys
from typing import Any

from hisim.postprocessing import reportgenerator
from hisim.postprocessing import charts
from hisim import log
from hisim import utils
from hisim.postprocessingoptions import PostProcessingOptions
from hisim import loadtypes as lt
from hisim.postprocessing.chart_singleday import ChartSingleDay
from hisim.postprocessing.compute_KPIs import compute_KPIs
from hisim.simulationparameters import SimulationParameters
from hisim.component import ComponentOutput


class PostProcessingDataTransfer:  # noqa: too-few-public-methods

    """ Data class for transfering the result data to this class. """

    def __init__(self,
                 results: Any,
                 all_outputs: Any,
                 simulation_parameters: SimulationParameters,
                 wrapped_components: Any,
                 mode: Any,
                 setup_function: Any,
                 execution_time: Any,
                 results_monthly: Any,
                 ) -> None:
        """ Initializes the values. """
        # Johanna Ganglbauer: time correction factor is applied in postprocessing to sum over power values and convert them to energy
        self.time_correction_factor = simulation_parameters.seconds_per_timestep / 3600
        self.results = results
        self.all_outputs = all_outputs
        self.simulation_parameters = simulation_parameters
        self.wrapped_components = wrapped_components
        self.mode = mode
        self.setup_function = setup_function
        self.execution_time = execution_time
        self.results_monthly = results_monthly
        self.post_processing_options = simulation_parameters.post_processing_options
        log.information("Selected " + str(len(self.post_processing_options)) + " post processing options:")
        for option in self.post_processing_options:
            log.information("Selected post processing option: " + str(option))


class PostProcessor:

    """ Core Post processor class. """

    @utils.measure_execution_time
    def __init__(self):
        """ Initializes the post processing. """
        self.dirname: str

    def set_dir_results(self, dirname):
        """ Sets the results directory. """
        if dirname is None:
            raise ValueError("No results directory name was defined.")
        self.dirname = dirname

    @utils.measure_execution_time
    def plot_sankeys(self, ppdt: PostProcessingDataTransfer) -> None:
        """ For plotting the sankeys. """
        for i_display_name in [name for name, display_name in lt.DisplayNames.__members__.items()]:
            my_sankey = charts.SankeyHISIM(name=i_display_name,
                                           units=lt.Units.ANY,
                                           directorypath=ppdt.simulation_parameters.result_directory,
                                           time_correction_factor=ppdt.time_correction_factor)
            my_sankey.plot(data=ppdt.all_outputs)
        if any(component_output.component_name == "HeatPump" for component_output in ppdt.all_outputs):
            my_sankey = charts.SankeyHISIM(name="HeatPump",
                                           units=lt.Units.ANY,
                                           directorypath=ppdt.simulation_parameters.result_directory,
                                           time_correction_factor=ppdt.time_correction_factor)
            my_sankey.plot_heat_pump(data=ppdt.all_outputs)
        if any(component_output.component_name == "Building" for component_output in ppdt.all_outputs):
            my_sankey = charts.SankeyHISIM(name="Building",
                                           units=lt.Units.ANY,
                                           directorypath=ppdt.simulation_parameters.result_directory,
                                           time_correction_factor=ppdt.time_correction_factor)
            my_sankey.plot_building(data=ppdt.all_outputs)

    @utils.measure_execution_time
    @utils.measure_memory_leak
    def run(self, ppdt: PostProcessingDataTransfer) -> None:  # noqa: MC0001
        """ Runs the main post processing. """
        # Define the directory name
        log.information("Main post processing function")
        report = reportgenerator.ReportGenerator(dirpath=ppdt.simulation_parameters.result_directory)
        days = {"month": 0, "day": 0}
        if PostProcessingOptions.PLOT_LINE in ppdt.post_processing_options:
            log.information("Making line plots.")
            self.make_line_plots(ppdt)
        if PostProcessingOptions.PLOT_CARPET in ppdt.post_processing_options:
            log.information("Making carpet plots.")
            self.make_carpet_plots(ppdt)
        if PostProcessingOptions.PLOT_SINGLE_DAYS in ppdt.post_processing_options:
            log.information("Making single day plots.")
            self.make_single_day_plots(days, ppdt)
        if PostProcessingOptions.PLOT_BAR_CHARTS in ppdt.post_processing_options:
            log.information("Making bar charts.")
            self.make_bar_charts(ppdt)
        # Plot sankey
        if PostProcessingOptions.PLOT_SANKEY in ppdt.post_processing_options:
            log.information("Making sankey plots.")
            self.make_sankey_plots()
        # Export all results to CSV
        if PostProcessingOptions.EXPORT_TO_CSV in ppdt.post_processing_options:
            log.information("Making CSV exports.")
            self.make_csv_export(ppdt)
        if PostProcessingOptions.GENERATE_PDF_REPORT in ppdt.post_processing_options:
            log.information("Making PDF report.")
            self.write_components_to_report(ppdt, report)
        # Export all results to
        if PostProcessingOptions.COMPUTE_KPI in ppdt.post_processing_options:
            log.information("Computing KPIs for the report.")
            log.information("Computing KPIs")
            self.compute_kpis(ppdt, report)

        # only a single day has been calculated. This gets special charts for debugging.
        if len(ppdt.results) == 1440:
            log.information("Making sankey plots.")
            self.make_special_one_day_debugging_plots(ppdt)

        # Open file explorer
        if PostProcessingOptions.OPEN_DIRECTORY_IN_EXPLORER in ppdt.post_processing_options:
            log.information("opening the explorer.")
            self.open_dir_in_file_explorer(ppdt)
        log.information("Finished main post processing function")

    def make_special_one_day_debugging_plots(self, ppdt: PostProcessingDataTransfer) -> None:
        """ Makes special plots for debugging if only a single day was calculated."""
        for index, output in enumerate(ppdt.all_outputs):
            if output.full_name == "Dummy # Residence Temperature":
                my_days = ChartSingleDay(output=output.full_name,
                                         units=output.unit,
                                         directorypath=ppdt.simulation_parameters.result_directory,
                                         time_correction_factor=ppdt.time_correction_factor,
                                         data=ppdt.results.iloc[:, index],
                                         day=0,
                                         month=0,
                                         output2=ppdt.results.iloc[:, 11])
            else:
                my_days = ChartSingleDay(output=output.full_name,
                                         units=output.unit,
                                         directorypath=ppdt.simulation_parameters.result_directory,
                                         time_correction_factor=ppdt.time_correction_factor,
                                         data=ppdt.results.iloc[:, index],
                                         day=0,
                                         month=0)
            my_days.plot(close=True)

    def make_csv_export(self, ppdt: PostProcessingDataTransfer) -> None:
        """ Exports all data to CSV. """
        log.information("exporting to csv")
        self.export_results_to_csv(ppdt)

    def make_sankey_plots(self) -> None:
        """ Makes Sankey plots. Needs work. """
        log.information("plotting sankeys")
        #    self.plot_sankeys()

    def make_bar_charts(self, ppdt: PostProcessingDataTransfer) -> None:
        """ Make bar charts. """
        for index, output in enumerate(ppdt.all_outputs):
            my_bar = charts.BarChart(output=output.full_name,

                                     units=output.unit,
                                     dirpath=ppdt.simulation_parameters.result_directory,
                                     time_correction_factor=ppdt.time_correction_factor)
            my_bar.plot(data=ppdt.results_monthly.iloc[:, index])

    def make_single_day_plots(self, days: Any, ppdt: PostProcessingDataTransfer) -> None:
        """ Makes plots for selected days. """
        for index, output in enumerate(ppdt.all_outputs):
            my_days = ChartSingleDay(output=output.full_name,
                                     units=output.unit,
                                     directorypath=ppdt.simulation_parameters.result_directory,
                                     time_correction_factor=ppdt.time_correction_factor,
                                     day=days["day"],
                                     month=days["month"],
                                     data=ppdt.results.iloc[:, index])
            my_days.plot(close=True)

    def make_carpet_plots(self, ppdt: PostProcessingDataTransfer) -> None:
        """ Make carpet plots. """
        for index, output in enumerate(ppdt.all_outputs):
            # log.information("Making carpet plots")
            my_carpet = charts.Carpet(output=output.full_name,

                                      units=output.unit,
                                      directorypath=ppdt.simulation_parameters.result_directory,
                                      time_correction_factor=ppdt.time_correction_factor)
            my_carpet.plot(xdims=int(
                (ppdt.simulation_parameters.end_date - ppdt.simulation_parameters.start_date).days), data=ppdt.results.iloc[:, index])

    @utils.measure_memory_leak
    def make_line_plots(self, ppdt: PostProcessingDataTransfer) -> None:
        """ Makes the line plots."""
        for index, output in enumerate(ppdt.all_outputs):
            my_line = charts.Line(output=output.full_name,
                                  units=output.unit,
                                  directorypath=ppdt.simulation_parameters.result_directory,
                                  time_correction_factor=ppdt.time_correction_factor)
            my_line.plot(data=ppdt.results.iloc[:, index], units=output.unit)
            del my_line

    @utils.measure_execution_time
    def export_results_to_csv(self, ppdt: PostProcessingDataTransfer) -> None:
        """ Exports the results to a CSV file. """
        for column in ppdt.results:
            ppdt.results[column].to_csv(os.path.join(ppdt.simulation_parameters.result_directory,
                                                     f"{column.split(' ', 3)[2]}_{column.split(' ', 3)[0]}.csv"), sep=",", decimal=".")
        for column in ppdt.results_monthly:
            csvfilename = os.path.join(ppdt.simulation_parameters.result_directory,
                                       f"{column.split(' ', 3)[2]}_{column.split(' ', 3)[0]}_monthly.csv")
            header = [f"{column.split('[', 1)[0]} - monthly ["f"{column.split('[', 1)[1]}"]
            ppdt.results_monthly[column].to_csv(csvfilename, sep=",", decimal=".", header=header)

    def write_to_report(self, text: Any, report: reportgenerator.ReportGenerator) -> None:
        """ Writes a single line to the report. """
        report.open()
        report.write(text)
        report.close()

    def compute_kpis(self, ppdt: PostProcessingDataTransfer, report: reportgenerator.ReportGenerator) -> None:
        """ Computes KPI's and writes them to report. """
        lines = compute_KPIs(results=ppdt.results, all_outputs=ppdt.all_outputs, simulation_parameters=ppdt.simulation_parameters)
        self.write_to_report(text=lines, report=report)

    #
    # def cal_pos_sim(self):
    #     self.write_components_to_report()
    #
    #     total_electricity_consumed = None
    #     total_electricity_not_covered = None
    #     heat_pump_heating = None
    #     heat_pump_cooling = 0.0
    #     building_area = None
    #     solar_gain_through_windows = None
    #     internal_gains = None
    #
    #     for index, entry in enumerate(self.ppdt.wrapped_components):
    #         if entry.MyComponent.ComponentName == "Building":
    #             building_area = entry.MyComponent.A_f
    #
    #     for index, entry in enumerate(self.ppdt.all_outputs):
    #         if entry.ObjectName == "ElectricityGrid_Consumed":
    #             total_electricity_consumed = sum(entry.Results)* self.ppdt.time_correction_factor
    #         if entry.ObjectName == "ElectricityGrid_NotConveredConsumed":
    #             total_electricity_not_covered = sum(entry.Results)* self.ppdt.time_correction_factor
    #         if entry.ObjectName == "HeatPump" and entry.FieldName == "Heating":
    #             heat_pump_heating = sum(entry.Results)* self.ppdt.time_correction_factor
    #         if entry.ObjectName == "HeatPump" and entry.FieldName == "Cooling":
    #             heat_pump_cooling = abs(sum(entry.Results))* self.ppdt.time_correction_factor
    #         if entry.ObjectName == "HeatPump" and entry.FieldName == "ElectricityOutput":
    #             heat_pump_electricity_output = abs(sum(entry.Results)) * self.ppdt.time_correction_factor
    #         if entry.ObjectName == "HeatPump" and entry.FieldName == "NumberOfCycles":
    #             heat_pump_number_of_cycles = abs(entry.Results[-1])
    #         if entry.ObjectName == "Building" and entry.FieldName == "SolarGainThroughWindows":
    #             solar_gain_through_windows = abs(sum(entry.Results))* self.ppdt.time_correction_factor
    #         if entry.ObjectName == "Occupancy" and entry.FieldName == "HeatingByResidents":
    #             internal_gains = abs(sum(entry.Results)*self.ppdt.time_correction_factor)
    #
    #     # Writes self-consumption and autarky
    #     if total_electricity_consumed is not None:
    #         if total_electricity_not_covered is not None:
    #             autarky = ( ( total_electricity_consumed - total_electricity_not_covered ) / total_electricity_consumed ) * 100
    #             text = ["Consumed: {:.0f} kWh".format(total_electricity_consumed * 1E-3)]
    #             self.write_to_report(text)
    #             text = ["Not Covered: {:.0f} kWh".format(total_electricity_not_covered * 1E-3)]
    #             self.write_to_report(text)
    #             text = ["Autarky: {:.3}%".format(autarky)]
    #             self.write_to_report(text)
    #
    #     # Writes performance of heat pump
    #     if heat_pump_heating is not None:
    #         self.write_to_report(["HeatPump - Absolute Heating Demand [kWh]: {:.0f}".format(1E-3*heat_pump_heating)])
    #         self.write_to_report(["HeatPump - Absolute Cooling Demand [kWh]: {:.0f}".format(1E-3*heat_pump_cooling)])
    #         self.write_to_report(["HeatPump - Electricity Output [kWh]: {:.0f}".format(1E-3*heat_pump_electricity_output)])
    #         self.write_to_report(["HeatPump - Number Of Cycles: {}".format(heat_pump_number_of_cycles)])
    #         self.write_to_report(["HeatPump - Overall Coefficient of Performance: {:.2f}".format( (heat_pump_heating+heat_pump_cooling)
    #                                                                                               /heat_pump_electricity_output )])
    #         if building_area is not None:
    #             self.write_to_report(["HeatPump - Relative Heating Demand [kWh/m2]: {:.0f} ".format(1E-3*heat_pump_heating/building_area)])
    #
    #     # Writes building solar gains
    #     if solar_gain_through_windows is not None:
    #         self.write_to_report(["Absolute Solar Gains [kWh]: {:.0f}".format(1E-3*solar_gain_through_windows)])
    #         if building_area is not None:
    #             self.write_to_report(["Relative Solar Gains [Wh/m2]: {:.0f} ".format(1E-3*solar_gain_through_windows/building_area)])
    #
    #     # Writes building internal gains
    #     if internal_gains is not None:
    #         self.write_to_report(["Absolute Internal Gains [kWh]: {:.0f}".format(1E-3*internal_gains)])
    #         if building_area is not None:
    #             self.write_to_report(["Relative Internal Gains [kWh/m2]: {:.0f} ".format(1E-3*internal_gains/building_area)])

    def write_components_to_report(self, ppdt: PostProcessingDataTransfer, report: reportgenerator.ReportGenerator) -> None:
        """ Writes information about the components used in the simulation to the simulation report. """
        report.open()
        for wrapped_component in ppdt.wrapped_components:
            # print( wc.my_component )
            # if hasattr(wc.my_component, "write_to_report"):
            component_content = wrapped_component.my_component.write_to_report()
            if isinstance(component_content, list) is False:
                component_content = [component_content]
            if isinstance(component_content, str) is True:
                component_content = [component_content]
            report.write(component_content)
        all_output_names = []
        output: ComponentOutput
        for output in ppdt.all_outputs:
            all_output_names.append(output.full_name + " [" + output.unit + "]")
        report.write(["### All Outputs"])
        report.write(all_output_names)
        #   def __init__(self, object_name: str, field_name: str, load_type: lt.LoadTypes, unit: lt.Units,
        #                  sankey_flow_direction: Optional[bool] = None):
        report.close()

    def open_dir_in_file_explorer(self, ppdt: PostProcessingDataTransfer) -> None:
        """ Opens files in given path.

        The keyword darwin is used for supporting macOS,
        xdg-open will be available on any unix client running X.
        """
        if sys.platform == "win32":
            os.startfile(os.path.realpath(ppdt.simulation_parameters.result_directory))  # noqa: B606
        else:
            log.information("Not on Windows. Can't open explorer.")
        # else:
        #    opener = "open" if sys.platform == "darwin" else "xdg-open"
        #    subprocess.call([opener, os.path.realpath(self.ppdt.directory_path)])

    def export_sankeys(self):
        """ Exports Sankeys plots.

        ToDo: implement
        """
        pass  # noqa: unnecessary-pass

    # def get_std_results(self, ppdt: PostProcessingDataTransfer):
    #     """ Reshapes the results for bar charts.
    #
    #     ToDo: to be redefined and recoded in monthly bar plots in Bar Class
    #     """
    #     pd_timeline = pd.date_range(start=self.ppdt.simulation_parameters.start_date,
    #                                 end=self.ppdt.simulation_parameters.end_date,
    #                                 freq=f'{self.ppdt.simulation_parameters.seconds_per_timestep}S')[:-1]
    #     n_columns = ppdt.results.shape[1]
    #     my_data_frame = pd.DataFrame()
    #     for i_column in range(n_columns):
    #         temp_df = pd.DataFrame(self.ppdt.results.values[:, i_column], index=pd_timeline,
    #                                columns=[self.ppdt.results.columns[i_column]])
    #         if 'Temperature' in self.ppdt.results.columns[i_column] or 'Percent' in self.ppdt.results.columns[i_column]:
    #             temp_df = temp_df.resample('H').interpolate(method='linear')
    #         else:
    #             temp_df = temp_df.resample('H').sum()
    #         my_data_frame[temp_df.columns[0]] = temp_df.values[:, 0]
    #         my_data_frame.index = temp_df.index
    #
    #     ppdt.results.index = pd_timeline
    #
    #     dfm = pd.DataFrame()
    #     for i_column in range(n_columns):
    #         temp_df = pd.DataFrame(ppdt.results.values[:, i_column], index=pd_timeline,
    #                                columns=[ppdt.results.columns[i_column]])
    #         if 'Temperature' in ppdt.results.columns[i_column] or 'Percent' in ppdt.results.columns[i_column]:
    #             temp_df = temp_df.resample('M').interpolate(method='linear')
    #         else:
    #             temp_df = temp_df.resample('M').sum()
    #         dfm[temp_df.columns[0]] = temp_df.values[:, 0]
    #         dfm.index = temp_df.index
    #
    #     self.result_m = dfm
