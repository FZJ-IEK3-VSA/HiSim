""" Main postprocessing module that starts all other modules. """
# clean
import os
import sys
from typing import Any, Optional, List, Dict
from timeit import default_timer as timer

from hisim.components import building
from hisim.postprocessing import reportgenerator
from hisim.postprocessing import charts
from hisim import log
from hisim import utils
from hisim.postprocessingoptions import PostProcessingOptions
from hisim import loadtypes as lt
from hisim.postprocessing.chart_singleday import ChartSingleDay
from hisim.postprocessing.compute_kpis import compute_kpis
from hisim.postprocessing.generate_csv_for_housing_database import (
    generate_csv_for_database,
)
from hisim.postprocessing.system_chart import SystemChart
from hisim.component import ComponentOutput
from hisim.postprocessing.postprocessing_datatransfer import PostProcessingDataTransfer
from hisim.postprocessing.report_image_entries import ReportImageEntry


class PostProcessor:

    """Core Post processor class."""

    @utils.measure_execution_time
    def __init__(self):
        """Initializes the post processing."""
        self.dirname: str
        self.report_image_entries: List[ReportImageEntry] = []
        self.chapter_counter: int = 1
        self.figure_counter: int = 1

    def set_dir_results(self, dirname: Optional[str] = None) -> None:
        """Sets the results directory."""
        if dirname is None:
            raise ValueError("No results directory name was defined.")
        self.dirname = dirname

    @utils.measure_execution_time
    def plot_sankeys(self, ppdt: PostProcessingDataTransfer) -> None:
        """For plotting the sankeys."""
        for i_display_name in [
            name for name, display_name in lt.DisplayNames.__members__.items()
        ]:
            my_sankey = charts.SankeyHISIM(
                name=i_display_name,
                component_name=i_display_name,
                output_description=None,
                units=lt.Units.ANY,
                directorypath=ppdt.simulation_parameters.result_directory,
                time_correction_factor=ppdt.time_correction_factor,
            )
            my_sankey.plot(data=ppdt.all_outputs)
        if any(
            component_output.component_name == "HeatPump"
            for component_output in ppdt.all_outputs
        ):
            my_sankey = charts.SankeyHISIM(
                name="HeatPump",
                component_name="HeatPump",
                output_description=None,
                units=lt.Units.ANY,
                directorypath=ppdt.simulation_parameters.result_directory,
                time_correction_factor=ppdt.time_correction_factor,
            )
            my_sankey.plot_heat_pump(data=ppdt.all_outputs)
        if any(
            component_output.component_name == "Building"
            for component_output in ppdt.all_outputs
        ):
            my_sankey = charts.SankeyHISIM(
                name="Building",
                component_name="Building",
                output_description=None,
                units=lt.Units.ANY,
                directorypath=ppdt.simulation_parameters.result_directory,
                time_correction_factor=ppdt.time_correction_factor,
            )
            my_sankey.plot_building(data=ppdt.all_outputs)

    @utils.measure_execution_time
    @utils.measure_memory_leak
    def run(self, ppdt: PostProcessingDataTransfer) -> None:  # noqa: MC0001
        """Runs the main post processing."""
        # Define the directory name
        log.information("Main post processing function")

        # Check whether HiSim is running in a docker container
        docker_flag = os.getenv("HISIM_IN_DOCKER_CONTAINER", "false")
        if docker_flag.lower() in ("true", "yes", "y", "1"):
            # Charts etc. are not needed when executing HiSim in a container. Allow only csv files and KPI.
            allowed_options_for_docker = {
                PostProcessingOptions.EXPORT_TO_CSV,
                PostProcessingOptions.COMPUTE_AND_WRITE_KPIS_TO_REPORT,
            }
            # Of all specified options, select those that are allowed
            valid_options = list(
                set(ppdt.post_processing_options) & allowed_options_for_docker
            )
            if len(valid_options) < len(ppdt.post_processing_options):
                # At least one invalid option was set
                ppdt.post_processing_options = valid_options
                log.warning(
                    "Hisim is running in a docker container. Disabled invalid postprocessing options."
                )

        report = reportgenerator.ReportGenerator(
            dirpath=ppdt.simulation_parameters.result_directory
        )
        days = {"month": 0, "day": 0}
        # Make plots
        if PostProcessingOptions.PLOT_LINE in ppdt.post_processing_options:
            log.information("Making line plots.")
            start = timer()
            self.make_line_plots(ppdt)
            end = timer()
            duration = end - start
            log.information("Making line plots took " + f"{duration:1.2f}s.")
        if PostProcessingOptions.PLOT_CARPET in ppdt.post_processing_options:
            log.information("Making carpet plots.")
            start = timer()
            self.make_carpet_plots(ppdt)
            end = timer()
            duration = end - start
            log.information("Making carpet plots took " + f"{duration:1.2f}s.")
        if PostProcessingOptions.PLOT_SINGLE_DAYS in ppdt.post_processing_options:
            log.information("Making single day plots.")
            start = timer()
            self.make_single_day_plots(days, ppdt)
            end = timer()
            duration = end - start
            log.information("Making single day plots took " + f"{duration:1.2f}s.")
        if PostProcessingOptions.PLOT_BAR_CHARTS in ppdt.post_processing_options:
            log.information("Making bar charts.")
            start = timer()
            self.make_bar_charts(ppdt)
            end = timer()
            duration = end - start
            log.information("Making bar plots took " + f"{duration:1.2f}s.")
        # Plot sankey
        if PostProcessingOptions.PLOT_SANKEY in ppdt.post_processing_options:
            log.information("Making sankey plots.")
            start = timer()
            self.make_sankey_plots()
            end = timer()
            duration = end - start
            log.information("Making sankey plots took " + f"{duration:1.2f}s.")
        # Export all results to CSV
        if PostProcessingOptions.EXPORT_TO_CSV in ppdt.post_processing_options:
            log.information("Making CSV exports.")
            start = timer()
            self.make_csv_export(ppdt)
            end = timer()
            duration = end - start
            log.information("Making CSV export took " + f"{duration:1.2f}s.")
        if PostProcessingOptions.MAKE_NETWORK_CHARTS in ppdt.post_processing_options:
            log.information("Computing network charts.")
            start = timer()
            self.make_network_charts(ppdt)
            end = timer()
            duration = end - start
            log.information("Computing network charts took " + f"{duration:1.2f}s.")
        # Generate Pdf report
        if PostProcessingOptions.GENERATE_PDF_REPORT in ppdt.post_processing_options:
            log.information(
                "Making PDF report and writing simulation parameters to report."
            )
            start = timer()
            self.write_simulation_parameters_to_report(ppdt, report)
            end = timer()
            duration = end - start
            log.information(
                "Making PDF report and writing simulation parameters to report took "
                + f"{duration:1.2f}s."
            )
        if (
            PostProcessingOptions.WRITE_COMPONENTS_TO_REPORT
            in ppdt.post_processing_options
        ):
            log.information("Writing components to report.")
            start = timer()
            self.write_components_to_report(ppdt, report, self.report_image_entries)
            end = timer()
            duration = end - start
            log.information("Writing components to report took " + f"{duration:1.2f}s.")

        if (
            PostProcessingOptions.WRITE_ALL_OUTPUTS_TO_REPORT
            in ppdt.post_processing_options
        ):
            log.information("Writing all outputs to report.")
            start = timer()
            self.write_all_outputs_to_report(ppdt, report)
            end = timer()
            duration = end - start
            log.information(
                "Writing all outputs to report took " + f"{duration:1.2f}s."
            )
        if (
            PostProcessingOptions.WRITE_NETWORK_CHARTS_TO_REPORT
            in ppdt.post_processing_options
        ):
            log.information("Writing network charts to report.")
            start = timer()
            self.write_network_charts_to_report(ppdt, report)
            end = timer()
            duration = end - start
            log.information(
                "Writing network charts toreport took " + f"{duration:1.2f}s."
            )
        if (
            PostProcessingOptions.COMPUTE_AND_WRITE_KPIS_TO_REPORT
            in ppdt.post_processing_options
        ):
            log.information("Computing and writing KPIs to report.")
            start = timer()
            self.compute_and_write_kpis_to_report(ppdt, report)
            end = timer()
            duration = end - start
            log.information(
                "Computing and writing KPIs to report took " + f"{duration:1.2f}s."
            )
        if (
            PostProcessingOptions.GENERATE_CSV_FOR_HOUSING_DATA_BASE
            in ppdt.post_processing_options
        ):  
            building_data = []
            for elem in ppdt.wrapped_components:
                if isinstance(elem.my_component, building.Building):
                    building_data = elem.my_component.buildingdata
            if len(building_data) == 0:
                log.warning("Building needs to be defined to generate csv for housing data base.")
            else:
                log.information("Generating csv for housing data base. ")
                start = timer()
                generate_csv_for_database(
                    all_outputs=ppdt.all_outputs,
                    results=ppdt.results,
                    simulation_parameters=ppdt.simulation_parameters,
                    building_data=building_data
                )
                end = timer()
                duration = end - start
                log.information(
                    "Generating csv for housing data base took " + f"{duration:1.2f}s."
                )
                
        # only a single day has been calculated. This gets special charts for debugging.
        if (
            PostProcessingOptions.PLOT_SPECIAL_TESTING_SINGLE_DAY
            in ppdt.post_processing_options
            and len(ppdt.results) == 1440
        ):
            log.information(
                "Making special single day plots for a single day calculation for testing."
            )
            start = timer()
            self.make_special_one_day_debugging_plots(ppdt)
            end = timer()
            duration = end - start
            log.information(
                "Making special single day plots for a single day calculation for testing took "
                + f"{duration:1.2f}s."
            )

        # Open file explorer
        if (
            PostProcessingOptions.OPEN_DIRECTORY_IN_EXPLORER
            in ppdt.post_processing_options
        ):
            log.information("Opening the explorer.")
            self.open_dir_in_file_explorer(ppdt)
        log.information("Finished main post processing function.")

    def make_network_charts(self, ppdt: PostProcessingDataTransfer) -> None:
        """Generates the network charts that show the connection of the elements."""
        systemchart = SystemChart(ppdt)
        systemchart.make_chart()

    def make_special_one_day_debugging_plots(
        self, ppdt: PostProcessingDataTransfer
    ) -> None:
        """Makes special plots for debugging if only a single day was calculated."""
        for index, output in enumerate(ppdt.all_outputs):
            if output.full_name == "Dummy # Residence Temperature":
                my_days = ChartSingleDay(
                    output=output.full_name,
                    component_name=output.component_name,
                    units=output.unit,
                    directory_path=ppdt.simulation_parameters.result_directory,
                    time_correction_factor=ppdt.time_correction_factor,
                    data=ppdt.results.iloc[:, index],
                    day=0,
                    month=0,
                    output2=ppdt.results.iloc[:, 11],
                    output_description=output.output_description,
                )
            else:
                my_days = ChartSingleDay(
                    output=output.full_name,
                    component_name=output.component_name,
                    units=output.unit,
                    directory_path=ppdt.simulation_parameters.result_directory,
                    time_correction_factor=ppdt.time_correction_factor,
                    data=ppdt.results.iloc[:, index],
                    day=0,
                    month=0,
                    output_description=output.output_description,
                )
            my_entry = my_days.plot(close=True)
            self.report_image_entries.append(my_entry)

    def make_csv_export(self, ppdt: PostProcessingDataTransfer) -> None:
        """Exports all data to CSV."""
        log.information("Exporting to csv.")
        self.export_results_to_csv(ppdt)

    def make_sankey_plots(self) -> None:
        """Makes Sankey plots. Needs work."""
        log.information("Plotting sankeys.")
        # TODO:   self.plot_sankeys()

    def make_bar_charts(self, ppdt: PostProcessingDataTransfer) -> None:
        """Make bar charts."""
        for index, output in enumerate(ppdt.all_outputs):
            my_bar = charts.BarChart(
                output=output.full_name,
                component_name=output.component_name,
                units=output.unit,
                directory_path=os.path.join(
                    ppdt.simulation_parameters.result_directory
                ),
                time_correction_factor=ppdt.time_correction_factor,
                output_description=output.output_description,
            )
            my_entry = my_bar.plot(data=ppdt.results_monthly.iloc[:, index])
            self.report_image_entries.append(my_entry)

    def make_single_day_plots(
        self, days: Dict[str, int], ppdt: PostProcessingDataTransfer
    ) -> None:
        """Makes plots for selected days."""
        for index, output in enumerate(ppdt.all_outputs):
            my_days = ChartSingleDay(
                output=output.full_name,
                component_name=output.component_name,
                units=output.unit,
                directory_path=ppdt.simulation_parameters.result_directory,
                time_correction_factor=ppdt.time_correction_factor,
                day=days["day"],
                month=days["month"],
                data=ppdt.results.iloc[:, index],
                output_description=output.output_description,
            )
            my_entry = my_days.plot(close=True)
            self.report_image_entries.append(my_entry)

    def make_carpet_plots(self, ppdt: PostProcessingDataTransfer) -> None:
        """Make carpet plots."""
        for index, output in enumerate(ppdt.all_outputs):
            log.trace("Making carpet plots")
            my_carpet = charts.Carpet(
                output=output.full_name,
                component_name=output.component_name,
                units=output.unit,
                directory_path=ppdt.simulation_parameters.result_directory,
                time_correction_factor=ppdt.time_correction_factor,
                output_description=output.output_description,
            )

            my_entry = my_carpet.plot(
                xdims=int(
                    (
                        ppdt.simulation_parameters.end_date
                        - ppdt.simulation_parameters.start_date
                    ).days
                ),
                data=ppdt.results.iloc[:, index],
            )
            self.report_image_entries.append(my_entry)

    @utils.measure_memory_leak
    def make_line_plots(self, ppdt: PostProcessingDataTransfer) -> None:
        """Makes the line plots."""
        for index, output in enumerate(ppdt.all_outputs):
            my_line = charts.Line(
                output=output.full_name,
                component_name=output.component_name,
                units=output.unit,
                directory_path=ppdt.simulation_parameters.result_directory,
                time_correction_factor=ppdt.time_correction_factor,
                output_description=output.output_description,
            )
            my_entry = my_line.plot(data=ppdt.results.iloc[:, index], units=output.unit)
            self.report_image_entries.append(my_entry)
            del my_line

    @utils.measure_execution_time
    def export_results_to_csv(self, ppdt: PostProcessingDataTransfer) -> None:
        """Exports the results to a CSV file."""
        for column in ppdt.results:
            ppdt.results[column].to_csv(
                os.path.join(
                    ppdt.simulation_parameters.result_directory,
                    f"{column.split(' ', 3)[2]}_{column.split(' ', 3)[0]}.csv",
                ),
                sep=",",
                decimal=".",
            )
        for column in ppdt.results_monthly:
            csvfilename = os.path.join(
                ppdt.simulation_parameters.result_directory,
                f"{column.split(' ', 3)[2]}_{column.split(' ', 3)[0]}_monthly.csv",
            )
            header = [
                f"{column.split('[', 1)[0]} - monthly [" f"{column.split('[', 1)[1]}"
            ]
            ppdt.results_monthly[column].to_csv(
                csvfilename, sep=",", decimal=".", header=header
            )

    def write_simulation_parameters_to_report(
        self, ppdt: PostProcessingDataTransfer, report: reportgenerator.ReportGenerator
    ) -> None:
        """Write simulation parameters to report."""
        report.open()
        report.write_heading_with_style_heading_one(
            [str(self.chapter_counter) + ". Simulation Parameters"]
        )
        report.write_with_normal_alignment(
            [
                "The following information was used to configure the HiSim Building Simulation."
            ]
        )
        report.write_with_normal_alignment(
            ppdt.simulation_parameters.get_unique_key_as_list()
        )
        self.chapter_counter = self.chapter_counter + 1
        report.page_break()
        report.close()

    def write_components_to_report(
        self,
        ppdt: PostProcessingDataTransfer,
        report: reportgenerator.ReportGenerator,
        report_image_entries: List[ReportImageEntry],
    ) -> None:
        """Writes information about the components used in the simulation to the simulation report."""

        def write_image_entry_to_report_for_one_component(
            component: Any, report_image_entries_for_component: List[ReportImageEntry]
        ) -> None:
            """Write image entry to report for one component."""

            sorted_entries = sorted(
                report_image_entries_for_component, key=lambda x: x.output_type
            )
            output_explanations = []

            output_type_counter = 1
            report.add_spacer()
            report.write_heading_with_style_heading_one(
                [str(self.chapter_counter) + ". " + component]
            )
            if (
                PostProcessingOptions.INCLUDE_CONFIGS_IN_PDF_REPORT
                in ppdt.post_processing_options
            ):
                for wrapped_component in ppdt.wrapped_components:
                    if wrapped_component.my_component.component_name == component:
                        report.write_with_normal_alignment(
                            [
                                "The following information was used to configure the component."
                            ]
                        )
                        component_content = (
                            wrapped_component.my_component.write_to_report()
                        )
                        report.write_with_normal_alignment(component_content)

            if (
                PostProcessingOptions.INCLUDE_IMAGES_IN_PDF_REPORT
                in ppdt.post_processing_options
            ):
                for entry in sorted_entries:
                    # write output description only once for each output type
                    if entry.output_type not in output_explanations:
                        output_explanations.append(entry.output_type)
                        report.write_heading_with_style_heading_two(
                            [
                                str(self.chapter_counter)
                                + "."
                                + str(output_type_counter)
                                + " "
                                + entry.component_name
                                + " Output: "
                                + entry.output_type
                            ]
                        )
                        report.write_with_normal_alignment([entry.output_description])
                        output_type_counter = output_type_counter + 1
                    report.write_figures_to_report(entry.file_path)
                    report.write_with_center_alignment(
                        [
                            "Fig."
                            + str(self.figure_counter)
                            + ": "
                            + entry.component_name
                            + " "
                            + entry.output_type
                        ]
                    )
                    report.add_spacer()
                    self.figure_counter = self.figure_counter + 1
            report.page_break()
            self.chapter_counter = self.chapter_counter + 1

        report.open()
        # sort report image entries
        component_names = []
        for report_image_entry in report_image_entries:
            if report_image_entry.component_name not in component_names:
                component_names.append(report_image_entry.component_name)

        for component in component_names:
            output_types = []
            report_image_entries_for_component = []
            for report_image_entry in report_image_entries:
                if report_image_entry.component_name == component:
                    report_image_entries_for_component.append(report_image_entry)
                    if report_image_entry.output_type not in output_types:
                        output_types.append(report_image_entry.output_type)

            write_image_entry_to_report_for_one_component(
                component, report_image_entries_for_component
            )

        report.close()

    def write_all_outputs_to_report(
        self, ppdt: PostProcessingDataTransfer, report: reportgenerator.ReportGenerator
    ) -> None:
        """Write all outputs to report."""
        report.open()
        all_output_names: List[Optional[str]]
        all_output_names = []
        output: ComponentOutput
        for output in ppdt.all_outputs:
            all_output_names.append(output.full_name + " [" + output.unit + "]")
        report.write_heading_with_style_heading_one(
            [str(self.chapter_counter) + ". All Outputs"]
        )
        self.chapter_counter = self.chapter_counter + 1
        report.write_with_normal_alignment(all_output_names)
        report.page_break()
        report.close()

    def write_network_charts_to_report(
        self, ppdt: PostProcessingDataTransfer, report: reportgenerator.ReportGenerator
    ) -> None:
        """Write network charts to report."""
        report.open()
        report.write_heading_with_style_heading_one(
            [str(self.chapter_counter) + ". System Network Charts"]
        )
        report.write_figures_to_report_with_size_four_six(
            os.path.join(
                ppdt.simulation_parameters.result_directory, "System_no_Edge_labels.png"
            )
        )
        report.write_with_center_alignment(
            [
                "Fig."
                + str(self.figure_counter)
                + ": "
                + "System Chart of all components."
            ]
        )
        report.write_figures_to_report_with_size_seven_four(
            os.path.join(
                ppdt.simulation_parameters.result_directory,
                "System_with_Edge_labels.png",
            )
        )
        self.figure_counter = self.figure_counter + 1
        report.write_with_center_alignment(
            [
                "Fig."
                + str(self.figure_counter)
                + ": "
                + "System Chart of all components and all outputs."
            ]
        )
        self.figure_counter = self.figure_counter + 1
        self.chapter_counter = self.chapter_counter + 1
        report.page_break()
        report.close()

    def compute_and_write_kpis_to_report(
        self, ppdt: PostProcessingDataTransfer, report: reportgenerator.ReportGenerator
    ) -> None:
        """Computes KPI's and writes them to report and csv."""
        kpi_compute_return = compute_kpis(
            results=ppdt.results,
            all_outputs=ppdt.all_outputs,
            simulation_parameters=ppdt.simulation_parameters,
        )
        lines = kpi_compute_return
        report.open()
        report.write_heading_with_style_heading_one(
            [str(self.chapter_counter) + ". KPIs"]
        )
        report.write_with_normal_alignment(lines)
        self.chapter_counter = self.chapter_counter + 1
        report.close()

    def open_dir_in_file_explorer(self, ppdt: PostProcessingDataTransfer) -> None:
        """Opens files in given path.

        The keyword darwin is used for supporting macOS,
        xdg-open will be available on any unix client running X.
        """
        if sys.platform == "win32":
            os.startfile(
                os.path.realpath(ppdt.simulation_parameters.result_directory)
            )  # noqa: B606
        else:
            log.information("Not on Windows. Can't open explorer.")

    def export_sankeys(self):
        """Exports Sankeys plots.

        ToDo: implement
        """
        pass  # noqa: unnecessary-pass
