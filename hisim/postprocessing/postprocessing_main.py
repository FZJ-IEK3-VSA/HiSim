""" Main postprocessing module that starts all other modules. """
# clean
import os
import sys
import copy
from typing import Any, Optional, List, Dict, Tuple
from timeit import default_timer as timer
import string

import pandas as pd

from hisim.components import building
from hisim.components import loadprofilegenerator_connector
from hisim.postprocessing import reportgenerator
from hisim.postprocessing import charts
from hisim import log
from hisim import utils
from hisim.postprocessingoptions import PostProcessingOptions
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
from hisim.json_generator import JsonConfigurationGenerator
from hisim.postprocessing.webtool_kpi_entries import WebtoolKpiEntries


class PostProcessor:

    """Core Post processor class."""

    @utils.measure_execution_time
    def __init__(self):
        """Initializes the post processing."""
        self.dirname: str
        self.chapter_counter: int = 1
        self.figure_counter: int = 1
        self.pyam_data_folder: str = ""
        self.model: str = "HiSim"
        self.scenario: str = ""
        self.region: str = ""
        self.year: int = 2021

    def set_dir_results(self, dirname: Optional[str] = None) -> None:
        """Sets the results directory."""
        if dirname is None:
            raise ValueError("No results directory name was defined.")
        self.dirname = dirname

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
                PostProcessingOptions.COMPUTE_OPEX,
                PostProcessingOptions.COMPUTE_CAPEX,
                PostProcessingOptions.MAKE_RESULT_JSON_WITH_KPI_FOR_WEBTOOL,
                PostProcessingOptions.WRITE_COMPONENT_CONFIGS_TO_JSON,
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

        # make monthly bar plots only if simulation duration approximately a year
        if (
            PostProcessingOptions.PLOT_MONTHLY_BAR_CHARTS
            in ppdt.post_processing_options
            and ppdt.simulation_parameters.duration.days >= 360
        ):
            log.information("Making monthly bar charts.")
            start = timer()
            self.make_monthly_bar_charts(
                ppdt, report_image_entries=report_image_entries
            )
            end = timer()
            duration = end - start
            log.information("Making monthly bar plots took " + f"{duration:1.2f}s.")

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
            building_data = pd.DataFrame()
            occupancy_config = None
            for elem in ppdt.wrapped_components:
                if isinstance(elem.my_component, building.Building):
                    building_data = elem.my_component.my_building_information.buildingdata
                elif isinstance(
                    elem.my_component, loadprofilegenerator_connector.Occupancy
                ):
                    occupancy_config = elem.my_component.occupancy_config
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

        # Prepare webtool results
        if (
            PostProcessingOptions.MAKE_RESULT_JSON_WITH_KPI_FOR_WEBTOOL
            in ppdt.post_processing_options
        ):
            log.information("Make kpi json file for webtool.")
            self.write_kpis_for_webtool_to_json_file(ppdt)

        if (
            PostProcessingOptions.WRITE_COMPONENT_CONFIGS_TO_JSON
            in ppdt.post_processing_options
        ):
            log.information("Writing component configurations to JSON file.")
            self.write_component_configurations_to_json(ppdt)

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

    def make_monthly_bar_charts(
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
            my_entry = my_line.plot(data=ppdt.results.iloc[:, index])
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
        self.write_new_chapter_with_table_to_report(
            report=report,
            table_as_list_of_list=kpi_compute_return,
            headline=". KPIs",
            comment=["Here a comment on calculation of numbers will follow"],
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
        self.write_new_chapter_with_table_to_report(
            report=report,
            table_as_list_of_list=opex_compute_return,
            headline=". Operational Costs and Emissions for simulated period",
            comment=[
                "\n",
                "Comments:",
                "Operational Costs are the sum of fuel costs and maintenance costs for the devices, calculated for the simulated period.",
                "Emissions are fuel emissions emitted during simulad period.",
                "Consumption for Diesel_Car in l, for EV in kWh.",
            ],
        )

    def compute_and_write_capex_costs_to_report(
        self, ppdt: PostProcessingDataTransfer, report: reportgenerator.ReportGenerator
    ) -> None:
        """Computes CAPEX costs and CO2-emissions for production of devices and writes them to report and csv."""
        capex_compute_return = capex_calculation(
            components=ppdt.wrapped_components,
            simulation_parameters=ppdt.simulation_parameters,
        )
        self.write_new_chapter_with_table_to_report(
            report=report,
            table_as_list_of_list=capex_compute_return,
            headline=". Investment Cost and CO2-Emissions of devices for simulated period",
            comment=[
                "Values for Battery are calculated with lifetime in cycles instead of lifetime in years"
            ],
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

    def write_new_chapter_with_table_to_report(
        self,
        report: reportgenerator.ReportGenerator,
        table_as_list_of_list: List,
        headline: str,
        comment: List,
    ) -> None:
        """Write new chapter with headline and a table to report."""
        report.open()
        report.write_heading_with_style_heading_one(
            [str(self.chapter_counter) + headline]
        )
        report.write_tables_to_report(table_as_list_of_list)
        report.write_with_normal_alignment(comment)
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

        # create pyam data foler
        self.pyam_data_folder = os.path.join(
            ppdt.simulation_parameters.result_directory, "pyam_data"
        )
        if os.path.exists(self.pyam_data_folder) is False:
            os.makedirs(self.pyam_data_folder)
        else:
            log.information(
                "This pyam_data path exists already: " + self.pyam_data_folder
            )

        # --------------------------------------------------------------------------------------------------------------------------------------------------------------
        # make dictionaries with pyam data structure for hourly and yearly data
        simple_dict_hourly_data: Dict = {
            "model": [],
            "scenario": [],
            "region": [],
            "variable": [],
            "unit": [],
            "time": [],
            "value": [],
        }
        simple_dict_daily_data: Dict = copy.deepcopy(simple_dict_hourly_data)
        simple_dict_monthly_data: Dict = copy.deepcopy(simple_dict_hourly_data)

        simple_dict_cumulative_data: Dict = {
            "model": [],
            "scenario": [],
            "region": [],
            "variable": [],
            "unit": [],
            "year": [],
            "value": [],
        }
        # set pyam model name
        self.model = "".join(["HiSim_", ppdt.module_filename])

        # set pyam scenario name
        if SingletonSimRepository().exist_entry(
            key=SingletonDictKeyEnum.RESULT_SCENARIO_NAME
        ):
            self.scenario = SingletonSimRepository().get_entry(
                key=SingletonDictKeyEnum.RESULT_SCENARIO_NAME
            )
        else:
            self.scenario = ppdt.setup_function

        # set pyam region
        if SingletonSimRepository().exist_entry(key=SingletonDictKeyEnum.LOCATION):
            self.region = SingletonSimRepository().get_entry(
                key=SingletonDictKeyEnum.LOCATION
            )
        else:
            self.region = ""

        # set pyam year or timeseries
        self.year = ppdt.simulation_parameters.year
        timeseries_hourly = ppdt.results_hourly.index
        log.information(str(timeseries_hourly))
        timeseries_daily = ppdt.results_daily.index
        timeseries_monthly = ppdt.results_monthly.index

        if (
            PostProcessingOptions.COMPUTE_AND_WRITE_KPIS_TO_REPORT
            in ppdt.post_processing_options
        ):
            self.write_kpis_in_pyam_dict(
                ppdt=ppdt, simple_dict_cumulative_data=simple_dict_cumulative_data
            )

        # got through all components and read output values, variables and units
        # for hourly data
        dataframe_hourly_data = self.iterate_over_results_and_add_values_to_dict(
            results_df=ppdt.results_hourly,
            dict_to_check=simple_dict_hourly_data,
            timeseries=timeseries_hourly,
        )
        self.write_filename_and_save_to_csv(
            dataframe=dataframe_hourly_data,
            folder=self.pyam_data_folder,
            module_filename=ppdt.module_filename,
            simulation_duration=ppdt.simulation_parameters.duration.days,
            simulation_year=ppdt.simulation_parameters.year,
            region=self.region,
            time_resolution_of_data="hourly",
        )
        # for daily data
        dataframe_daily_data = self.iterate_over_results_and_add_values_to_dict(
            results_df=ppdt.results_daily,
            dict_to_check=simple_dict_daily_data,
            timeseries=timeseries_daily,
        )
        self.write_filename_and_save_to_csv(
            dataframe=dataframe_daily_data,
            folder=self.pyam_data_folder,
            module_filename=ppdt.module_filename,
            simulation_duration=ppdt.simulation_parameters.duration.days,
            simulation_year=ppdt.simulation_parameters.year,
            region=self.region,
            time_resolution_of_data="daily",
        )
        # for monthly data
        dataframe_monthly_data = self.iterate_over_results_and_add_values_to_dict(
            results_df=ppdt.results_monthly,
            dict_to_check=simple_dict_monthly_data,
            timeseries=timeseries_monthly,
        )
        self.write_filename_and_save_to_csv(
            dataframe=dataframe_monthly_data,
            folder=self.pyam_data_folder,
            module_filename=ppdt.module_filename,
            simulation_duration=ppdt.simulation_parameters.duration.days,
            simulation_year=ppdt.simulation_parameters.year,
            region=self.region,
            time_resolution_of_data="monthly",
        )

        # got through all components and read output values, variables and units for simple_dict_cumulative_data
        for column in ppdt.results_cumulative:
            value = ppdt.results_cumulative[column].values[0]

            (
                variable_name,
                unit,
            ) = self.get_variable_name_and_unit_from_ppdt_results_column(
                column=str(column)
            )

            simple_dict_cumulative_data["model"].append(self.model)
            simple_dict_cumulative_data["scenario"].append(self.scenario)
            simple_dict_cumulative_data["region"].append(self.region)
            simple_dict_cumulative_data["variable"].append(variable_name)
            simple_dict_cumulative_data["unit"].append(unit)
            simple_dict_cumulative_data["year"].append(self.year)
            simple_dict_cumulative_data["value"].append(value)

        # create dataframe
        simple_df_yearly_data = pd.DataFrame(simple_dict_cumulative_data)
        self.write_filename_and_save_to_csv(
            dataframe=simple_df_yearly_data,
            folder=self.pyam_data_folder,
            module_filename=ppdt.module_filename,
            time_resolution_of_data="yearly",
            simulation_duration=ppdt.simulation_parameters.duration.days,
            simulation_year=ppdt.simulation_parameters.year,
            region=self.region,
        )

        # --------------------------------------------------------------------------------------------------------------------------------------------------------------
        # create dictionary with all import pyam information
        data_information_dict = {
            "model": self.model,
            "scenario": self.scenario,
            "region": self.region,
            "year": self.year,
            "duration in days": ppdt.simulation_parameters.duration.days,
        }

        # write json config with all component configs, module config, pyam information dict and simulation parameters
        json_generator_config = JsonConfigurationGenerator(name=f"{self.scenario}")
        json_generator_config.set_simulation_parameters(
            my_simulation_parameters=ppdt.simulation_parameters
        )
        if ppdt.my_module_config_path is not None:
            json_generator_config.set_module_config(
                my_module_config_path=ppdt.my_module_config_path
            )
        json_generator_config.set_pyam_data_information_dict(
            pyam_data_information_dict=data_information_dict
        )
        for component in ppdt.wrapped_components:
            json_generator_config.add_component(config=component.my_component.config)

        # save the json config
        json_generator_config.save_to_json(
            filename=os.path.join(
                self.pyam_data_folder, "data_information_for_pyam.json"
            )
        )

    def write_component_configurations_to_json(self, ppdt: PostProcessingDataTransfer) -> None:
        """Collect all component configurations and write into JSON file in result directory."""
        json_generator_config = JsonConfigurationGenerator(name="my_system")
        for component in ppdt.wrapped_components:
            json_generator_config.add_component(config=component.my_component.config)
        json_generator_config.save_to_json(
            filename=os.path.join(
                ppdt.simulation_parameters.result_directory, "component_configurations.json"
            )
        )


    def write_kpis_in_pyam_dict(
        self,
        ppdt: PostProcessingDataTransfer,
        simple_dict_cumulative_data: Dict[str, Any],
    ) -> None:
        """Write kpis in pyam dictionary."""

        kpi_compute_return = compute_kpis(
            components=ppdt.wrapped_components,
            results=ppdt.results,
            all_outputs=ppdt.all_outputs,
            simulation_parameters=ppdt.simulation_parameters,
        )

        for i in kpi_compute_return:
            if "---" not in i:
                variable_name = "".join(x for x in i[0] if x != ":")
                variable_value = "".join(x for x in i[1] if x in string.digits)
                variable_unit = i[2]

                simple_dict_cumulative_data["model"].append(self.model)
                simple_dict_cumulative_data["scenario"].append(self.scenario)
                simple_dict_cumulative_data["region"].append(self.region)
                simple_dict_cumulative_data["variable"].append(variable_name)
                simple_dict_cumulative_data["unit"].append(variable_unit)
                simple_dict_cumulative_data["year"].append(self.year)
                simple_dict_cumulative_data["value"].append(variable_value)

    def get_variable_name_and_unit_from_ppdt_results_column(
        self, column: str
    ) -> Tuple[str, str]:
        """Get variable name and unit for pyam dictionary."""

        column_splitted = str(
            "".join(
                [
                    x
                    for x in column
                    if x
                    in string.ascii_letters + "'- " + string.digits + "_" + "°" + "/"
                ]
            )
        ).split(sep=" ")

        variable_name = "".join(
            [column_splitted[0], "|", column_splitted[3], "|", column_splitted[2]]
        )

        unit = column_splitted[5]

        return variable_name, unit

    def iterate_over_results_and_add_values_to_dict(
        self, results_df: pd.DataFrame, dict_to_check: Dict[str, Any], timeseries: Any
    ) -> pd.DataFrame:
        """Iterate over results and add values to dict, write to dataframe and save as csv."""

        for column in results_df:
            for index, timestep in enumerate(timeseries):
                # values = ppdt.results_hourly[column].values
                values = results_df[column].values

                (
                    variable_name,
                    unit,
                ) = self.get_variable_name_and_unit_from_ppdt_results_column(
                    column=str(column)
                )

                dict_to_check["model"].append(self.model)
                dict_to_check["scenario"].append(self.scenario)
                dict_to_check["region"].append(self.region)
                dict_to_check["variable"].append(variable_name)
                dict_to_check["unit"].append(unit)
                dict_to_check["time"].append(timestep)
                dict_to_check["value"].append(values[index])

        dataframe_from_dict = pd.DataFrame(dict_to_check)

        return dataframe_from_dict

    def write_filename_and_save_to_csv(
        self,
        dataframe: pd.DataFrame,
        folder: str,
        module_filename: str,
        time_resolution_of_data: str,
        simulation_duration: int,
        simulation_year: int,
        region: str,
    ) -> None:
        """Write file to csv."""

        filename = os.path.join(
            folder,
            f"{module_filename}_{time_resolution_of_data}_results_for_{simulation_duration}_days_in_year_{simulation_year}_in_{region}.csv",
        )

        dataframe.to_csv(
            path_or_buf=filename,
            index=None,
        )  # type: ignore

    def write_kpis_for_webtool_to_json_file(
        self, ppdt: PostProcessingDataTransfer
    ) -> None:
        """Collect important KPIs and write into json for webtool."""

        # check if important options were set
        if all(
            option in ppdt.post_processing_options
            for option in [
                PostProcessingOptions.COMPUTE_AND_WRITE_KPIS_TO_REPORT,
                PostProcessingOptions.COMPUTE_CAPEX,
                PostProcessingOptions.COMPUTE_OPEX,
            ]
        ):
            # calculate KPIs
            kpi_compute_return = compute_kpis(
                components=ppdt.wrapped_components,
                results=ppdt.results,
                all_outputs=ppdt.all_outputs,
                simulation_parameters=ppdt.simulation_parameters,
            )
            dict_with_important_kpi = self.get_dict_from_kpi_lists(
                value_list=kpi_compute_return
            )

            # caculate capex
            capex_compute_return = capex_calculation(
                components=ppdt.wrapped_components,
                simulation_parameters=ppdt.simulation_parameters,
            )

            dict_with_important_capex = self.get_dict_from_opex_capex_lists(
                value_list=capex_compute_return
            )

            # caculate opex
            opex_compute_return = opex_calculation(
                components=ppdt.wrapped_components,
                all_outputs=ppdt.all_outputs,
                postprocessing_results=ppdt.results,
                simulation_parameters=ppdt.simulation_parameters,
            )

            dict_with_important_opex = self.get_dict_from_opex_capex_lists(
                value_list=opex_compute_return
            )

            # initialize webtool kpi entries dataclass
            webtool_kpi_dataclass = WebtoolKpiEntries(
                kpi_dict=dict_with_important_kpi,
                capex_dict=dict_with_important_capex,
                opex_dict=dict_with_important_opex,
            )

            # save dict as json file in results folder
            json_file = webtool_kpi_dataclass.to_json(indent=4)
            with open(
                os.path.join(
                    ppdt.simulation_parameters.result_directory, "webtool_kpis.json"
                ),
                "w",
                encoding="utf-8",
            ) as file:
                file.write(json_file)

        else:
            raise ValueError(
                "Some PostProcessingOptions are not set."
                "Please check if PostProcessingOptions.COMPUTE_AND_WRITE_KPIS_TO_REPORT, PostProcessingOptions.COMPUTE_CAPEX,"
                "PostProcessingOptions.COMPUTE_OPEX are set in your example."
            )

    def get_dict_from_kpi_lists(self, value_list: List[str]) -> Dict[str, Any]:
        """Get dict with values for webtool from kpis lists."""

        dict_with_values = {}

        for kpi_value_unit in value_list:
            if "---" not in kpi_value_unit:
                variable_name = "".join(x for x in kpi_value_unit[0] if x != ":")
                variable_value = "".join(
                    x for x in kpi_value_unit[1] if x in string.digits
                )
                variable_unit = kpi_value_unit[2]

                dict_with_values.update(
                    {f"{variable_name} [{variable_unit}] ": variable_value}
                )

        return dict_with_values

    def get_dict_from_opex_capex_lists(self, value_list: List[str]) -> Dict[str, Any]:
        """Get dict with values for webtool from opex capex lists."""

        dict_with_cost_values = {}
        dict_with_emission_values = {}
        dict_with_lifetime_values = {}

        total_dict = {}

        name_one = value_list[0]

        for value_unit in value_list:
            if "---" not in value_unit:
                variable_name = "".join(x for x in value_unit[0] if x != ":")
                variable_value_investment = value_unit[1]
                variable_value_emissions = value_unit[2]
                variable_value_lifetime = value_unit[3]

                dict_with_cost_values.update(
                    {f"{variable_name} [{name_one[1]}] ": variable_value_investment}
                )
                dict_with_emission_values.update(
                    {f"{variable_name} [{name_one[2]}] ": variable_value_emissions}
                )
                dict_with_lifetime_values.update(
                    {f"{variable_name} [{name_one[3]}] ": variable_value_lifetime}
                )

                total_dict.update(
                    {
                        "column 1": dict_with_cost_values,
                        "column 2": dict_with_emission_values,
                        "column 3": dict_with_lifetime_values,
                    }
                )

        return total_dict
