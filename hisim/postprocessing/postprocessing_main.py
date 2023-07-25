""" Main postprocessing module that starts all other modules. """
# clean
import os
import sys
from typing import Any, Optional, List, Dict
from timeit import default_timer as timer
import string
import json
import pandas as pd

from hisim.components import building
from hisim.components import loadprofilegenerator_connector
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
from hisim.postprocessing.opex_and_capex_cost_calculation import (
    opex_calculation,
    capex_calculation,
)
from hisim.postprocessing.system_chart import SystemChart
from hisim.component import ComponentOutput
from hisim.postprocessing.postprocessing_datatransfer import PostProcessingDataTransfer
from hisim.postprocessing.report_image_entries import ReportImageEntry, SystemChartEntry
from hisim.sim_repository_singleton import SingletonSimRepository, SingletonDictKeyEnum


class PostProcessor:

    """Core Post processor class."""

    @utils.measure_execution_time
    def __init__(self):
        """Initializes the post processing."""
        self.dirname: str
        # self.report_image_entries: List[ReportImageEntry] = []
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
                figure_format=ppdt.simulation_parameters.figure_format,
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
                figure_format=ppdt.simulation_parameters.figure_format,
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
                figure_format=ppdt.simulation_parameters.figure_format,
            )
            my_sankey.plot_building(data=ppdt.all_outputs)

    @utils.measure_execution_time
    @utils.measure_memory_leak
    def run(self, ppdt: PostProcessingDataTransfer) -> None:  # noqa: MC0001
        """Runs the main post processing."""
        # Define the directory name
        log.information("Main post processing function")
        report_image_entries: List[ReportImageEntry] = []
        # Check whether HiSim is running in a docker container
        docker_flag = os.getenv("HISIM_IN_DOCKER_CONTAINER", "false")
        if docker_flag.lower() in ("true", "yes", "y", "1"):
            # Charts etc. are not needed when executing HiSim in a container. Allow only csv files and KPI.
            allowed_options_for_docker = {
                PostProcessingOptions.EXPORT_TO_CSV,
                PostProcessingOptions.COMPUTE_AND_WRITE_KPIS_TO_REPORT,
                PostProcessingOptions.GENERATE_CSV_FOR_HOUSING_DATA_BASE,
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
        system_chart_entries: List[SystemChartEntry] = []
        # Make plots
        if PostProcessingOptions.PLOT_LINE in ppdt.post_processing_options:
            log.information("Making line plots.")
            start = timer()
            self.make_line_plots(ppdt, report_image_entries=report_image_entries)
            end = timer()
            duration = end - start
            log.information("Making line plots took " + f"{duration:1.2f}s.")
        if PostProcessingOptions.PLOT_CARPET in ppdt.post_processing_options:
            log.information("Making carpet plots.")
            start = timer()
            self.make_carpet_plots(ppdt, report_image_entries=report_image_entries)
            end = timer()
            duration = end - start
            log.information("Making carpet plots took " + f"{duration:1.2f}s.")
        if PostProcessingOptions.PLOT_SINGLE_DAYS in ppdt.post_processing_options:
            log.information("Making single day plots.")
            start = timer()
            self.make_single_day_plots(
                days, ppdt, report_image_entries=report_image_entries
            )
            end = timer()
            duration = end - start
            log.information("Making single day plots took " + f"{duration:1.2f}s.")
        if PostProcessingOptions.PLOT_BAR_CHARTS in ppdt.post_processing_options:
            log.information("Making bar charts.")
            start = timer()
            self.make_bar_charts(ppdt, report_image_entries=report_image_entries)
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
            system_chart_entries = self.make_network_charts(ppdt)
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
        if PostProcessingOptions.COMPUTE_OPEX in ppdt.post_processing_options:
            log.information(
                "Computing and writing operational costs and C02 emissions produced in operation to report."
            )
            start = timer()
            self.compute_and_write_opex_costs_to_report(ppdt, report)
            end = timer()
            duration = end - start
            log.information(
                "Computing and writing operational costs and C02 emissions produced in operation to report took "
                + f"{duration:1.2f}s."
            )
        if (
            PostProcessingOptions.WRITE_COMPONENTS_TO_REPORT
            in ppdt.post_processing_options
        ):
            log.information("Writing components to report.")
            start = timer()
            self.write_components_to_report(ppdt, report, report_image_entries)
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
            self.write_network_charts_to_report(
                ppdt, report, system_chart_entries=system_chart_entries
            )
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
        if PostProcessingOptions.COMPUTE_CAPEX in ppdt.post_processing_options:
            log.information(
                "Computing and writing investment costs and C02 emissions from production of devices to report."
            )
            start = timer()
            self.compute_and_write_capex_costs_to_report(ppdt, report)
            end = timer()
            duration = end - start
            log.information(
                "Computing and writing investment costs and C02 emissions from production of devices to report took "
                + f"{duration:1.2f}s."
            )
        if (
            PostProcessingOptions.GENERATE_CSV_FOR_HOUSING_DATA_BASE
            in ppdt.post_processing_options
        ):
            building_data = pd.DataFrame()
            occupancy_config = None
            for elem in ppdt.wrapped_components:
                if isinstance(elem.my_component, building.Building):
                    building_data = elem.my_component.buildingdata
                elif isinstance(
                    elem.my_component, loadprofilegenerator_connector.Occupancy
                ):
                    occupancy_config = elem.my_component.occupancyConfig
            if len(building_data) == 0:
                log.warning(
                    "Building needs to be defined to generate csv for housing data base."
                )
            else:
                log.information("Generating csv for housing data base. ")
                start = timer()
                generate_csv_for_database(
                    all_outputs=ppdt.all_outputs,
                    results=ppdt.results,
                    simulation_parameters=ppdt.simulation_parameters,
                    building_data=building_data,
                    occupancy_config=occupancy_config,
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
            self.make_special_one_day_debugging_plots(
                ppdt, report_image_entries=report_image_entries
            )
            end = timer()
            duration = end - start
            log.information(
                "Making special single day plots for a single day calculation for testing took "
                + f"{duration:1.2f}s."
            )

        # Write Outputs to pyam.IAMDataframe format for scenario evaluation
        if (
            PostProcessingOptions.PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION_WITH_PYAM
            in ppdt.post_processing_options
        ):
            log.information("Prepare results for scenario evaluation with pyam.")
            self.prepare_results_for_scenario_evaluation_with_pyam(ppdt)

        # Open file explorer
        if (
            PostProcessingOptions.OPEN_DIRECTORY_IN_EXPLORER
            in ppdt.post_processing_options
        ):
            log.information("Opening the explorer.")
            self.open_dir_in_file_explorer(ppdt)
        log.information("Finished main post processing function.")

    def make_network_charts(
        self, ppdt: PostProcessingDataTransfer
    ) -> List[SystemChartEntry]:
        """Generates the network charts that show the connection of the elements."""
        systemchart = SystemChart(ppdt)
        return systemchart.make_chart()

    def make_special_one_day_debugging_plots(
        self,
        ppdt: PostProcessingDataTransfer,
        report_image_entries: List[ReportImageEntry],
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
                    figure_format=ppdt.simulation_parameters.figure_format,
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
                    figure_format=ppdt.simulation_parameters.figure_format,
                )
            my_entry = my_days.plot(close=True)
            report_image_entries.append(my_entry)

    def make_csv_export(self, ppdt: PostProcessingDataTransfer) -> None:
        """Exports all data to CSV."""
        log.information("Exporting to csv.")
        self.export_results_to_csv(ppdt)

    def make_sankey_plots(self) -> None:
        """Makes Sankey plots. Needs work."""
        log.information("Plotting sankeys.")
        # TODO:   self.plot_sankeys()

    def make_bar_charts(
        self,
        ppdt: PostProcessingDataTransfer,
        report_image_entries: List[ReportImageEntry],
    ) -> None:
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
                figure_format=ppdt.simulation_parameters.figure_format,
            )
            my_entry = my_bar.plot(data=ppdt.results_monthly.iloc[:, index])
            report_image_entries.append(my_entry)

    def make_single_day_plots(
        self,
        days: Dict[str, int],
        ppdt: PostProcessingDataTransfer,
        report_image_entries: List[ReportImageEntry],
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
                figure_format=ppdt.simulation_parameters.figure_format,
            )
            my_entry = my_days.plot(close=True)
            report_image_entries.append(my_entry)

    def make_carpet_plots(
        self,
        ppdt: PostProcessingDataTransfer,
        report_image_entries: List[ReportImageEntry],
    ) -> None:
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
                figure_format=ppdt.simulation_parameters.figure_format,
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
            report_image_entries.append(my_entry)

    @utils.measure_memory_leak
    def make_line_plots(
        self,
        ppdt: PostProcessingDataTransfer,
        report_image_entries: List[ReportImageEntry],
    ) -> None:
        """Makes the line plots."""
        for index, output in enumerate(ppdt.all_outputs):
            if output.output_description is None:
                raise ValueError(
                    "Output description was missing for " + output.full_name
                )
            my_line = charts.Line(
                output=output.full_name,
                component_name=output.component_name,
                units=output.unit,
                directory_path=ppdt.simulation_parameters.result_directory,
                time_correction_factor=ppdt.time_correction_factor,
                output_description=output.output_description,
                figure_format=ppdt.simulation_parameters.figure_format,
            )
            my_entry = my_line.plot(data=ppdt.results.iloc[:, index], units=output.unit)
            report_image_entries.append(my_entry)
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
        lines = [
            "The following information was used to configure the HiSim Building Simulation."
        ]
        simulation_parameters_list = ppdt.simulation_parameters.get_unique_key_as_list()
        lines += simulation_parameters_list
        self.write_new_chapter_with_text_content_to_report(
            report=report,
            lines=lines,
            headline=". Simulation Parameters",
        )

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

            sorted_entries: List[ReportImageEntry] = sorted(
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
                entry: ReportImageEntry
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
                        if entry.output_description is None:
                            raise ValueError(
                                "Component had no description: "
                                + str(entry.component_name)
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
        all_output_names: List[Optional[str]]
        all_output_names = []
        output: ComponentOutput
        for output in ppdt.all_outputs:
            all_output_names.append(output.full_name + " [" + output.unit + "]")
        self.write_new_chapter_with_text_content_to_report(
            report=report,
            lines=all_output_names,
            headline=". All Outputs",
        )

    def write_network_charts_to_report(
        self,
        ppdt: PostProcessingDataTransfer,
        report: reportgenerator.ReportGenerator,
        system_chart_entries: List[SystemChartEntry],
    ) -> None:
        """Write network charts to report."""
        report.open()
        report.write_heading_with_style_heading_one(
            [str(self.chapter_counter) + ". System Network Charts"]
        )
        for entry in system_chart_entries:
            report.write_figures_to_report_with_size_four_six(
                os.path.join(ppdt.simulation_parameters.result_directory, entry.path)
            )
            report.write_with_center_alignment(
                ["Fig." + str(self.figure_counter) + ": " + entry.caption]
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
            components=ppdt.wrapped_components,
            results=ppdt.results,
            all_outputs=ppdt.all_outputs,
            simulation_parameters=ppdt.simulation_parameters,
        )
        self.write_new_chapter_with_text_content_to_report(
            report=report,
            lines=kpi_compute_return,
            headline=". KPIs"
        )

    def compute_and_write_opex_costs_to_report(
        self, ppdt: PostProcessingDataTransfer, report: reportgenerator.ReportGenerator
    ) -> None:
        """Computes OPEX costs and operational CO2-emissions and writes them to report and csv."""
        opex_compute_return = opex_calculation(
            components=ppdt.wrapped_components,
            all_outputs=ppdt.all_outputs,
            postprocessing_results=ppdt.results,
            simulation_parameters=ppdt.simulation_parameters,
        )
        self.write_new_chapter_with_text_content_to_report(
            report=report,
            lines=opex_compute_return,
            headline=". Costs and Emissions"
        )

    def compute_and_write_capex_costs_to_report(
        self, ppdt: PostProcessingDataTransfer, report: reportgenerator.ReportGenerator
    ) -> None:
        """Computes CAPEX costs and CO2-emissions for production of devices and writes them to report and csv."""
        capex_compute_return = capex_calculation(
            components=ppdt.wrapped_components,
            simulation_parameters=ppdt.simulation_parameters,
        )
        self.write_new_chapter_with_text_content_to_report(
            report=report,
            lines=capex_compute_return,
            headline=". Investment Cost and CO2-Emissions of devices",
        )

    def write_new_chapter_with_text_content_to_report(
        self, report: reportgenerator.ReportGenerator, lines: List, headline: str
    ) -> None:
        """Write new chapter with headline and some general information e.g. KPIs to report."""
        report.open()
        report.write_heading_with_style_heading_one(
            [str(self.chapter_counter) + headline]
        )
        report.write_with_normal_alignment(lines)
        self.chapter_counter = self.chapter_counter + 1
        report.page_break()
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

    def prepare_results_for_scenario_evaluation_with_pyam(
        self, ppdt: PostProcessingDataTransfer
    ) -> None:
        """Prepare the results for the scenario evaluation with pyam."""

        simple_dict_hourly_data: Dict = {
            "model": [],
            "scenario": [],
            "region": [],
            "variable": [],
            "unit": [],
            "time": [],
            "value": [],
        }
        simple_dict_cumulative_data: Dict = {
            "model": [],
            "scenario": [],
            "region": [],
            "variable": [],
            "unit": [],
            "year": [],
            "value": [],
        }
        model = "".join(["HiSim_", ppdt.module_filename])
        scenario = ppdt.setup_function
        region = SingletonSimRepository().get_entry(key=SingletonDictKeyEnum.LOCATION)
        year = ppdt.simulation_parameters.year
        timeseries = (
            ppdt.results_hourly.index
        )  # .strftime("%Y-%m-%d %H:%M:%S").to_list()

        for column in ppdt.results_hourly:
            for index, timestep in enumerate(timeseries):
                values = ppdt.results_hourly[column].values
                column_splitted = str(
                    "".join([x for x in column if x in string.ascii_letters + "'- "])
                ).split(sep=" ")
                variable = "".join(
                    [
                        column_splitted[0],
                        "|",
                        column_splitted[3],
                        "|",
                        column_splitted[2],
                    ]
                )
                unit = column_splitted[5]
                simple_dict_hourly_data["model"].append(model)
                simple_dict_hourly_data["scenario"].append(scenario)
                simple_dict_hourly_data["region"].append(region)
                simple_dict_hourly_data["variable"].append(variable)
                simple_dict_hourly_data["unit"].append(unit)
                simple_dict_hourly_data["time"].append(timestep)
                simple_dict_hourly_data["value"].append(values[index])

        for column in ppdt.results_cumulative:
            value = ppdt.results_cumulative[column].values[0]

            column_splitted = str(
                "".join([x for x in column if x in string.ascii_letters + "'- "])
            ).split(sep=" ")
            variable = "".join(
                [column_splitted[0], "|", column_splitted[3], "|", column_splitted[2]]
            )
            unit = column_splitted[5]
            simple_dict_cumulative_data["model"].append(model)
            simple_dict_cumulative_data["scenario"].append(scenario)
            simple_dict_cumulative_data["region"].append(region)
            simple_dict_cumulative_data["variable"].append(variable)
            simple_dict_cumulative_data["unit"].append(unit)
            simple_dict_cumulative_data["year"].append(year)
            simple_dict_cumulative_data["value"].append(value)

        # create dataframe
        simple_df_hourly_data = pd.DataFrame(simple_dict_hourly_data)
        simple_df_yearly_data = pd.DataFrame(simple_dict_cumulative_data)
        # write dictionary with all import parameters
        data_information_dict = {
            "model": model,
            "scenario": scenario,
            "region": region,
            "year": year,
            "duration in days": ppdt.simulation_parameters.duration.days,
        }
        component_counter = 0
        for component in ppdt.wrapped_components:
            try:
                # rename keys because some get overwritten if key name exists several times
                dict_config = component.my_component.config.to_dict()
                rename_dict_config = {}
                for key, value in dict_config.items():
                    rename_dict_config[
                        f"component {component_counter} {key}"
                    ] = dict_config[key]
                dict_config = rename_dict_config
                del rename_dict_config
            except BaseException as exc:
                raise ValueError(
                    "component.my_component.config.to_dict() does probably not work. "
                    "That might be because the config of the component does not inherit from Configbase. "
                    "Please change your config class according to the other component config classes with the configbase inheritance."
                ) from exc

            try:
                # try json dumping and if it works append data information dict
                component.my_component.config.to_json()
                data_information_dict.update(dict_config)
                component_counter = component_counter + 1

            except Exception as ex:
                # else try to convert data types so that json dumping works out
                for key, value in dict_config.items():
                    if not isinstance(value, (int, float, str, bool, type(None))):
                        if isinstance(value, list):
                            # transform list to string so it can be json serializable later
                            dict_config[key] = str(value).strip("[]")
                            # append data information dict
                            data_information_dict.update(dict_config)
                            component_counter = component_counter + 1
                        else:
                            raise ValueError(
                                "Value in config dict has a datatype that is not json serializable. Check the data type and try to transform it to a built-in data type."
                            ) from ex

        # pyam_data_folder = ppdt.simulation_parameters.result_directory + "\\pyam_data\\"
        pyam_data_folder = os.path.join(
            ppdt.simulation_parameters.result_directory, "pyam_data"
        )
        if os.path.exists(pyam_data_folder) is False:
            os.makedirs(pyam_data_folder)
        else:
            log.information("This pyam_data path exists already: " + pyam_data_folder)
        file_name_hourly = os.path.join(
            pyam_data_folder,
            f"{ppdt.module_filename}_hourly_results_for_{ppdt.simulation_parameters.duration.days}_days_in_year_{ppdt.simulation_parameters.year}_in_{region}.csv",
        )
        file_name_yearly = os.path.join(
            pyam_data_folder,
            f"{ppdt.module_filename}_yearly_results_for_{ppdt.simulation_parameters.duration.days}_days_in_year_{ppdt.simulation_parameters.year}_in_{region}.csv",
        )
        simple_df_hourly_data.to_csv(
            path_or_buf=file_name_hourly,
            index=None,
        )  # type: ignore
        simple_df_yearly_data.to_csv(path_or_buf=file_name_yearly, index=None)  # type: ignore

        # Serializing json
        json_object = json.dumps(data_information_dict, indent=4)
        # Writing to sample.json
        with open(
            os.path.join(pyam_data_folder, "data_information_for_pyam.json"),
            "w",
            encoding="utf-8",
        ) as outfile:
            outfile.write(json_object)
