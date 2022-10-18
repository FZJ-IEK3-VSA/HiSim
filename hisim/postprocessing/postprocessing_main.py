""" Main postprocessing module that starts all other modules. """
# clean
import os
import csv
import sys
from typing import Any, Optional

from hisim.postprocessing import reportgenerator
from hisim.postprocessing import charts
from hisim import log
from hisim import utils
from hisim.postprocessingoptions import PostProcessingOptions
from hisim import loadtypes as lt
from hisim.postprocessing.chart_singleday import ChartSingleDay
from hisim.postprocessing.compute_kpis import compute_kpis
from hisim.postprocessing.system_chart import SystemChart
from hisim.component import ComponentOutput
from hisim.postprocessing.postprocessing_datatransfer import PostProcessingDataTransfer


class PostProcessor:

    """ Core Post processor class. """

    @utils.measure_execution_time
    def __init__(self):
        """ Initializes the post processing. """
        self.dirname: str

    def set_dir_results(self, dirname: Optional[str] = None) -> None:
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

        # Check whether HiSim is running in a docker container
        docker_flag = os.getenv("HISIM_IN_DOCKER_CONTAINER", "false")
        if docker_flag.lower() in ("true", "yes", "y", "1"):
            # Charts etc. are not needed when executing HiSim in a container. Allow only csv files and KPI.
            allowed_options_for_docker = {PostProcessingOptions.EXPORT_TO_CSV, PostProcessingOptions.COMPUTE_KPI}
            # Of all specified options, select those that are allowed
            valid_options = list(set(ppdt.post_processing_options) & allowed_options_for_docker)
            if len(valid_options) < len(ppdt.post_processing_options):
                # At least one invalid option was set
                ppdt.post_processing_options = valid_options
                log.warning("Hisim is running in a docker container. Disabled invalid postprocessing options.")

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
            log.information("Computing KPIs")
            self.compute_kpis(ppdt, report)
        if PostProcessingOptions.MAKE_NETWORK_CHARTS in ppdt.post_processing_options:
            log.information("Computing Network Charts")
            self.make_network_charts(ppdt)

        # only a single day has been calculated. This gets special charts for debugging.
        if PostProcessingOptions.PLOT_SPECIAL_TESTING_SINGLE_DAY in ppdt.post_processing_options and len(ppdt.results) == 1440:
            log.information("Making special single day plots for a single day calculation for testing.")
            self.make_special_one_day_debugging_plots(ppdt)

        # Open file explorer
        if PostProcessingOptions.OPEN_DIRECTORY_IN_EXPLORER in ppdt.post_processing_options:
            log.information("opening the explorer.")
            self.open_dir_in_file_explorer(ppdt)
        log.information("Finished main post processing function")

    def make_network_charts(self, ppdt: PostProcessingDataTransfer) -> None:
        """ Generates the network charts that show the connection of the elements. """
        systemchart = SystemChart(ppdt)
        systemchart.make_chart()

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
        # TODO:   self.plot_sankeys()

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
            log.trace("Making carpet plots")
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
        """ Computes KPI's and writes them to report and csv. """
        kpi_compute_return = compute_kpis(results=ppdt.results, all_outputs=ppdt.all_outputs, simulation_parameters=ppdt.simulation_parameters)
        lines = kpi_compute_return[0]
        self.write_to_report(text=lines, report=report)
        csvfilename = os.path.join(ppdt.simulation_parameters.result_directory, "KPIs.csv")
        kpis_list = kpi_compute_return[1]
        kpis_values_list = kpi_compute_return[2]
        with open(csvfilename, "w", encoding='utf8') as csvfile:
            writer = csv.writer(csvfile)
            for (kpis_list_elem, kpis_values_list_elem) in zip(kpis_list, kpis_values_list):
                writer.writerow([kpis_list_elem, kpis_values_list_elem])

    def write_components_to_report(self, ppdt: PostProcessingDataTransfer, report: reportgenerator.ReportGenerator) -> None:
        """ Writes information about the components used in the simulation to the simulation report. """
        report.open()
        for wrapped_component in ppdt.wrapped_components:
            if hasattr(wrapped_component.my_component, "write_to_report"):
                component_content = wrapped_component.my_component.write_to_report()
            else:
                raise ValueError("Component is missing write_to_report_function: " + wrapped_component.my_component.component_name)
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

    def export_sankeys(self):
        """ Exports Sankeys plots.

        ToDo: implement
        """
        pass  # noqa: unnecessary-pass
