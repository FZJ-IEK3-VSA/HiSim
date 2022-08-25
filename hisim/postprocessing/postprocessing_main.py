""" Main postprocessing module that starts all other modules. """

import os
import sys
from typing import Any

import pandas as pd

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
                 directory_path: str,
                 results: Any,
                 all_outputs: Any,
                 simulation_parameters: SimulationParameters,
                 wrapped_components: Any,
                 story: Any,
                 mode: Any,
                 setup_function: Any,
                 execution_time: Any,
                 results_monthly: Any,
                 ) -> None:
        """ Initializes the values. """
        # Johanna Ganglbauer: time correction factor is applied in postprocessing to sum over power values and convert them to energy
        self.time_correction_factor = simulation_parameters.seconds_per_timestep / 3600
        self.directory_path = directory_path
        self.results = results
        self.all_outputs = all_outputs
        self.simulation_parameters = simulation_parameters
        self.wrapped_components = wrapped_components
        self.story = story
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
    def __init__(self, ppdt: PostProcessingDataTransfer):
        """ Initializes the post processing. """

        self.result_m: Any
        self.dirname: str
        self.ppdt = ppdt
        self.report_m: Any
        if ppdt is None:
            raise Exception("PPDT was none")
        self.report = reportgenerator.ReportGenerator(dirpath=self.ppdt.directory_path)

    def set_dir_results(self, dirname):
        """ Sets the results directory. """
        if dirname is None:
            raise ValueError("No results directory name was defined.")
        self.dirname = dirname

    @utils.measure_execution_time
    def plot_sankeys(self):
        """ For plotting the sankeys. """
        for i_display_name in [name for name, display_name in lt.DisplayNames.__members__.items()]:
            my_sankey = charts.SankeyHISIM(name=i_display_name,
                                           data=self.ppdt.all_outputs,
                                           units=lt.Units.ANY,
                                           directorypath=self.ppdt.directory_path,
                                           time_correction_factor=self.ppdt.time_correction_factor)
            my_sankey.plot()
        if any(component_output.component_name == "HeatPump" for component_output in self.ppdt.all_outputs):
            my_sankey = charts.SankeyHISIM(name="HeatPump",
                                           data=self.ppdt.all_outputs,
                                           units=lt.Units.ANY,
                                           directorypath=self.ppdt.directory_path,
                                           time_correction_factor=self.ppdt.time_correction_factor)
            my_sankey.plot_heat_pump()
        if any(component_output.component_name == "Building" for component_output in self.ppdt.all_outputs):
            my_sankey = charts.SankeyHISIM(name="Building",
                                           data=self.ppdt.all_outputs,
                                           units=lt.Units.ANY,
                                           directorypath=self.ppdt.directory_path,
                                           time_correction_factor=self.ppdt.time_correction_factor)
            my_sankey.plot_building()

    @utils.measure_execution_time
    def run(self):  # noqa: MC0001
        """ Runs the main post processing. """
        # Define the directory name

        days = {"month": 0, "day": 0}
        if PostProcessingOptions.PLOT_LINE in self.ppdt.post_processing_options:
            self.make_line_plots()
        if PostProcessingOptions.PLOT_CARPET in self.ppdt.post_processing_options:
            self.make_carpet_plots()
        if PostProcessingOptions.PLOT_SINGLE_DAYS in self.ppdt.post_processing_options:
            self.make_single_day_plots(days)
        if PostProcessingOptions.PLOT_BAR_CHARTS in self.ppdt.post_processing_options:
            self.make_bar_charts()
        # Plot sankey
        if PostProcessingOptions.PLOT_SANKEY in self.ppdt.post_processing_options:
            self.make_sankey_plots()
        # Export all results to CSV
        if PostProcessingOptions.EXPORT_TO_CSV in self.ppdt.post_processing_options:
            self.make_csv_export()
        if PostProcessingOptions.GENERATE_PDF_REPORT in self.ppdt.post_processing_options:
            self.write_components_to_report()
        # Export all results to CSV
        if PostProcessingOptions.COMPUTE_KPI in self.ppdt.post_processing_options:
            log.information("Computing KPIs")
            self.compute_kpis()
        else:
            log.information("not exporting to CSV")

        # only a single day has been calculated. This gets special charts for debugging.
        if len(self.ppdt.results) == 1440:
            self.make_special_one_day_debugging_plots()

        # Open file explorer
        if PostProcessingOptions.OPEN_DIRECTORY_IN_EXPLORER in self.ppdt.post_processing_options:
            self.open_dir_in_file_explorer()

    def make_special_one_day_debugging_plots(self):
        """ Makes special plots for debugging if only a single day was calculated."""
        for index, output in enumerate(self.ppdt.all_outputs):
            if output.full_name == "Dummy # Residence Temperature":
                my_days = ChartSingleDay(output=output.full_name,
                                         data=self.ppdt.results.iloc[:, index],
                                         units=output.unit,
                                         directorypath=self.ppdt.directory_path,
                                         time_correction_factor=self.ppdt.time_correction_factor,
                                         day=0,
                                         month=0,
                                         output2=self.ppdt.results.iloc[:, 11])
            else:
                my_days = ChartSingleDay(output=output.full_name,
                                         data=self.ppdt.results.iloc[:, index],
                                         units=output.unit,
                                         directorypath=self.ppdt.directory_path,
                                         time_correction_factor=self.ppdt.time_correction_factor,
                                         day=0,
                                         month=0)
            my_days.plot()
            my_days.close()

    def make_csv_export(self):
        """ Exports all data to CSV. """
        log.information("exporting to csv")
        self.export_results_to_csv()

    def make_sankey_plots(self):
        """ Makes Sankey plots. Needs work. """
        log.information("plotting sankeys")
        #    self.plot_sankeys()

    def make_bar_charts(self):
        """ Make bar charts. """
        for index, output in enumerate(self.ppdt.all_outputs):
            my_bar = charts.Bar(output=output.full_name,
                                data=self.ppdt.results_monthly.iloc[:, index],
                                units=output.unit,
                                dirpath=self.ppdt.directory_path,
                                time_correction_factor=self.ppdt.time_correction_factor)
            my_bar.plot()

    def make_single_day_plots(self, days):
        """ Makes plots for selected days. """
        for index, output in enumerate(self.ppdt.all_outputs):
            my_days = ChartSingleDay(output=output.full_name,
                                     data=self.ppdt.results.iloc[:, index],
                                     units=output.unit,
                                     directorypath=self.ppdt.directory_path,
                                     time_correction_factor=self.ppdt.time_correction_factor,
                                     day=days["day"],
                                     month=days["month"])
            my_days.plot()

    def make_carpet_plots(self):
        """ Make carpet plots. """
        for index, output in enumerate(self.ppdt.all_outputs):
            # log.information("Making carpet plots")
            my_carpet = charts.Carpet(output=output.full_name,
                                      data=self.ppdt.results.iloc[:, index],
                                      units=output.unit,
                                      directorypath=self.ppdt.directory_path,
                                      time_correction_factor=self.ppdt.time_correction_factor)
            my_carpet.plot(xdims=int(
                (self.ppdt.simulation_parameters.end_date - self.ppdt.simulation_parameters.start_date).days))

    def make_line_plots(self):
        """ Makes the line plots."""
        for index, output in enumerate(self.ppdt.all_outputs):
            my_line = charts.Line(output=output.full_name,
                                  data=self.ppdt.results.iloc[:, index],
                                  units=output.unit,
                                  directorypath=self.ppdt.directory_path,
                                  time_correction_factor=self.ppdt.time_correction_factor)
            my_line.plot()

    @utils.measure_execution_time
    def export_results_to_csv(self):
        """ Exports the results to a CSV file. """
        for column in self.ppdt.results:
            self.ppdt.results[column].to_csv(os.path.join(self.ppdt.directory_path,
                                                          f"{column.split(' ', 3)[2]}_{column.split(' ', 3)[0]}.csv"), sep=",", decimal=".")
        for column in self.ppdt.results_monthly:
            csvfilename = os.path.join(self.ppdt.directory_path, f"{column.split(' ', 3)[2]}_{column.split(' ', 3)[0]}_monthly.csv")
            header = [f"{column.split('[', 1)[0]} - monthly ["f"{column.split('[', 1)[1]}"]
            self.ppdt.results_monthly[column].to_csv(csvfilename, sep=",", decimal=".", header=header)

    def write_to_report(self, text):
        """ Writes a single line to the report. """
        self.report.open()
        self.report.write(text)
        self.report.close()

    def compute_kpis(self):
        """ KPI Calculator function. """
        lines = compute_KPIs(results=self.ppdt.results, all_outputs=self.ppdt.all_outputs, simulation_parameters=self.ppdt.simulation_parameters)
        self.write_to_report(lines)
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

    def write_components_to_report(self):
        """ Writes information about the components used in the simulation to the simulation report. """
        self.report.open()
        for wrapped_component in self.ppdt.wrapped_components:
            # print( wc.my_component )
            # if hasattr(wc.my_component, "write_to_report"):
            component_content = wrapped_component.my_component.write_to_report()
            if isinstance(component_content, list) is False:
                component_content = [component_content]
            if isinstance(component_content, str) is True:
                component_content = [component_content]
            self.report.write(component_content)
        all_output_names = []
        output: ComponentOutput
        for output in self.ppdt.all_outputs:
            all_output_names.append(output.full_name + " [" + output.unit + "]")
        self.report.write(["### All Outputs"])
        self.report.write(all_output_names)
        #   def __init__(self, object_name: str, field_name: str, load_type: lt.LoadTypes, unit: lt.Units,
        #                  sankey_flow_direction: Optional[bool] = None):
        self.report.close()

    def open_dir_in_file_explorer(self):
        """ Opens files in given path.

        The keyword darwin is used for supporting macOS,
        xdg-open will be available on any unix client running X.
        """
        if sys.platform == "win32":
            os.startfile(os.path.realpath(self.ppdt.directory_path))  # noqa: B606
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

    def get_std_results(self):
        """ Reshapes the results for bar charts.

        ToDo: to be redefined and recoded in monthly bar plots in Bar Class
        """
        pd_timeline = pd.date_range(start=self.ppdt.simulation_parameters.start_date,
                                    end=self.ppdt.simulation_parameters.end_date,
                                    freq=f'{self.ppdt.simulation_parameters.seconds_per_timestep}S')[:-1]
        n_columns = self.ppdt.results.shape[1]
        my_data_frame = pd.DataFrame()
        for i_column in range(n_columns):
            temp_df = pd.DataFrame(self.ppdt.results.values[:, i_column], index=pd_timeline,
                                   columns=[self.ppdt.results.columns[i_column]])
            if 'Temperature' in self.ppdt.results.columns[i_column] or 'Percent' in self.ppdt.results.columns[i_column]:
                temp_df = temp_df.resample('H').interpolate(method='linear')
            else:
                temp_df = temp_df.resample('H').sum()
            my_data_frame[temp_df.columns[0]] = temp_df.values[:, 0]
            my_data_frame.index = temp_df.index

        self.ppdt.results.index = pd_timeline

        dfm = pd.DataFrame()
        for i_column in range(n_columns):
            temp_df = pd.DataFrame(self.ppdt.results.values[:, i_column], index=pd_timeline,
                                   columns=[self.ppdt.results.columns[i_column]])
            if 'Temperature' in self.ppdt.results.columns[i_column] or 'Percent' in self.ppdt.results.columns[i_column]:
                temp_df = temp_df.resample('M').interpolate(method='linear')
            else:
                temp_df = temp_df.resample('M').sum()
            dfm[temp_df.columns[0]] = temp_df.values[:, 0]
            dfm.index = temp_df.index

        self.result_m = dfm
