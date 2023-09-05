"""Data Processing and Plotting for Scenario Comparison with Pyam."""


import glob
import time
import datetime
import os
from typing import Dict, Any, Tuple, Optional, List
import re
import enum
import numpy as np
import pyam
import pandas as pd
import matplotlib.pyplot as plt
import plotly
import string
from html2image import Html2Image
from hisim.postprocessing.pyam_data_collection import (
    PyamDataTypeEnum,
    PyamDataProcessingModeEnum,
)
from hisim.postprocessing.chartbase import ChartFontsAndSize
from hisim import log

from ordered_set import OrderedSet


class PyAmChartGenerator:

    """PyamChartGenerator class."""

    def __init__(
        self,
        simulation_duration_to_check: str,
        data_processing_mode: Any,
        analyze_yearly_or_hourly_data: Any,
        aggregate_data: bool = False,
        variables_to_check_for_hourly_data: Optional[List[str]] = None,
        variables_to_check_for_yearly_data: Optional[List[str]] = None,
        list_of_scenarios_to_check: Optional[List[str]] = None,
    ) -> None:
        """Initialize the class."""

        self.datetime_string = datetime.datetime.now().strftime("%Y%m%d_%H%M")

        if data_processing_mode == PyamDataProcessingModeEnum.PROCESS_ALL_DATA:

            data_path_strip = "data_with_all_parameters"
            result_path_strip = "results_for_all_parameters"

        elif (
            data_processing_mode
            == PyamDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_BUILDING_CODES
        ):
            data_path_strip = "data_with_different_building_codes"
            result_path_strip = "results_different_building_codes"

        elif (
            data_processing_mode
            == PyamDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_BUILDING_SIZES
        ):
            data_path_strip = "data_with_different_total_base_area_in_m2s"
            result_path_strip = "results_different_total_base_area_in_m2s"

        elif (
            data_processing_mode
            == PyamDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_PV_POWERS
        ):
            data_path_strip = "data_with_different_pv_powers"
            result_path_strip = "results_different_pv_powers"

        elif (
            data_processing_mode
            == PyamDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_PV_SIZES
        ):  # TODO: this is not implemented in generic pv system config
            data_path_strip = "data_with_different_pv_sizes"
            result_path_strip = "results_different_pv_sizes"

        elif (
            data_processing_mode
            == PyamDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_PV_AZIMUTH_ANGLES
        ):
            data_path_strip = "data_with_different_pv_azimuths"
            result_path_strip = "results_different_pv_azimuths"

        elif (
            data_processing_mode
            == PyamDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_PV_TILT_ANGLES
        ):
            data_path_strip = "data_with_different_pv_tilts"
            result_path_strip = "results_different_pv_tilts"

        else:
            raise ValueError("PyamDataProcessingMode not known.")

        self.folder_path = os.path.join(
            os.pardir,
            os.pardir,
            "examples",
            "results_for_scenario_comparison",
            "data",
            data_path_strip,
        )
        self.result_folder = os.path.join(
            os.pardir,
            os.pardir,
            "examples",
            "results_for_scenario_comparison",
            "results",
            result_path_strip,
        )
        log.information(f"Data folder path: {self.folder_path}")
        self.hisim_chartbase = ChartFontsAndSize()
        self.hisim_chartbase.figsize = (10, 8)

        pyam_dataframe = self.get_dataframe_and_create_pyam_dataframe_for_all_data(
            folder_path=self.folder_path,
            analyze_yearly_or_hourly_data=analyze_yearly_or_hourly_data,
            list_of_scenarios_to_check=list_of_scenarios_to_check,
            aggregate_data=aggregate_data,
        )
        

        if analyze_yearly_or_hourly_data == PyamDataTypeEnum.YEARLY:

            if (
                variables_to_check_for_yearly_data != []
                and variables_to_check_for_yearly_data is not None
            ):
                self.make_plots_with_specific_kind_of_data(
                    analyze_yearly_or_hourly_data=analyze_yearly_or_hourly_data,
                    pyam_dataframe=pyam_dataframe,
                    simulation_duration_key=simulation_duration_to_check,
                    variables_to_check=variables_to_check_for_yearly_data,
                )
            else:
                log.information(
                    "Variable list for yearly data is not given and will not be plotted or anaylzed."
                )

        elif analyze_yearly_or_hourly_data == PyamDataTypeEnum.HOURLY:

            if (
                variables_to_check_for_hourly_data != []
                and variables_to_check_for_hourly_data is not None
            ):

                self.make_plots_with_specific_kind_of_data(
                    analyze_yearly_or_hourly_data=analyze_yearly_or_hourly_data,
                    pyam_dataframe=pyam_dataframe,
                    simulation_duration_key=simulation_duration_to_check,
                    variables_to_check=variables_to_check_for_hourly_data,
                )
            else:
                log.information(
                    "Variable list for hourly data is not given and will not be plotted or anaylzed."
                )

        else:
            raise ValueError(
                "analyze_yearly_or_hourly_data variable is not set or has incompatible value."
            )

    def get_dataframe_and_create_pyam_dataframe_for_all_data(
        self,
        folder_path: str,
        analyze_yearly_or_hourly_data: Any,
        aggregate_data: bool,
        list_of_scenarios_to_check: Optional[List[str]],
    ) -> pyam.IamDataFrame:
        """Get csv data and create pyam dataframes."""

        if analyze_yearly_or_hourly_data == PyamDataTypeEnum.YEARLY:
            kind_of_data_set = "yearly"
        elif analyze_yearly_or_hourly_data == PyamDataTypeEnum.HOURLY:
            kind_of_data_set = "hourly"
        else:
            raise ValueError(
                "This kind of data was not found in the pyamdaacollectorenum class."
            )
        log.information(
            f"Read csv files and create one big pyam dataframe for {kind_of_data_set} data."
        )

        for file in glob.glob(
            os.path.join(folder_path, "**", f"*{kind_of_data_set}*.csv")
        ):

            file_df = pd.read_csv(filepath_or_buffer=file)
            

            # if scenario values are no strings, transform them
            file_df["scenario"] = file_df["scenario"].transform(str)

            # filter scenarios
            if (
                list_of_scenarios_to_check is not None
                and list_of_scenarios_to_check != []
            ):
                file_df = self.check_if_scenario_exists_and_filter_dataframe_for_scenarios(
                        data_frame=file_df,
                        list_of_scenarios_to_check=list_of_scenarios_to_check,
                        aggregate_data=aggregate_data)

            # create pyam dataframe
            pyam_dataframe = pyam.IamDataFrame(file_df)

            return pyam_dataframe

    def make_plots_with_specific_kind_of_data(
        self,
        analyze_yearly_or_hourly_data: Any,
        pyam_dataframe: pyam.IamDataFrame,
        simulation_duration_key: str,
        variables_to_check: List[str],
    ) -> None:
        """Make plots for different kind of data."""

        log.information(f"Simulation duration: {simulation_duration_key} days.")

        if pyam_dataframe.empty:
            raise ValueError("Pyam dataframe is empty.")

        sub_results_folder = f"simulation_duration_of_{simulation_duration_key}_days"
        sub_sub_results_folder = f"pyam_results_{self.datetime_string}"

        self.path_for_plots = os.path.join(
            self.result_folder, sub_results_folder, sub_sub_results_folder
        )

        for variable_to_check in variables_to_check:
            print("Check variable ", variable_to_check)

            # prepare path for plots
            self.path_addition = "".join(
                [
                    x
                    for x in variable_to_check
                    if x in string.ascii_letters or x.isspace() or x == "2"
                ]
            )

            self.plot_path_complete = os.path.join(
                self.path_for_plots, self.path_addition
            )
            if os.path.exists(self.plot_path_complete) is False:
                os.makedirs(self.plot_path_complete)

            filtered_data = self.filter_pyam_dataframe(
                pyam_dataframe=pyam_dataframe,
                filter_model=None,
                filter_scenario=None,
                filter_region=None,
                filter_variables=variable_to_check,
                filter_unit=None,
                filter_year=None,
            )

            log.information("Pyam dataframe scenarios " + str(filtered_data.scenario))

            # determine whether you want to compare one variable for different scenarios or different variables for one scenario
            comparion_mode = (
                "scenario"  # self.decide_for_scenario_or_variable_comparison(
            )
            #     filtered_data=filtered_data
            # )

            if analyze_yearly_or_hourly_data == PyamDataTypeEnum.YEARLY:
                kind_of_data_set = "yearly"
                log.information(
                    f"Yearly Data Processing for Simulation Duration of {simulation_duration_key} Days:"
                )
                # get statistical data
                self.get_statistics_of_data_and_write_to_excel(
                    filtered_data=filtered_data,
                    path_to_save=self.plot_path_complete,
                    kind_of_data_set=kind_of_data_set,
                )

                # self.make_bar_plot_for_pyam_dataframe(
                #     filtered_data=filtered_data,
                #     comparison_mode=comparion_mode,
                #     title=self.path_addition,
                # )

                self.make_box_plot_for_pyam_dataframe(
                    filtered_data=filtered_data,
                    comparison_mode=comparion_mode,
                    title=self.path_addition,
                )
                self.make_pie_plot_for_pyam_dataframe(
                    filtered_data=filtered_data,
                    comparison_mode=comparion_mode,
                    title=self.path_addition,
                )
                self.make_scatter_plot_for_pyam_dataframe(
                    pyam_dataframe=pyam_dataframe,
                    filter_model=None,
                    filter_scenario=None,
                    filter_variables=None,
                    title="HP vs Outside Temperatures",
                    filter_region=None,
                    filter_unit=None,
                    filter_year=None,
                    x_data_variable="Weather|Temperature|DailyAverageOutsideTemperatures",
                    y_data_variable="HeatPumpHPLib|Heating|ThermalOutputPower",
                )

                # self.make_stack_plot_for_pyam_dataframe(pyam_dataframe=pyam_dataframe, filter_model=None, filter_scenario=None,
                # filter_variables="EMS|ElectricityToOrFromGrid|-",
                # title="Electricity to or from Grid", filter_region=None, filter_unit=None, filter_year=None)
                # self.make_sankey_plot_for_pyam_dataframe(
                #     pyam_dataframe=pyam_dataframe, filter_model=None, filter_scenario="2227458627882477145", filter_variables="*|*|ElectricityOutput",
                # filter_region=None, filter_unit=None, filter_year=None
                # )

            elif analyze_yearly_or_hourly_data == PyamDataTypeEnum.HOURLY:
                kind_of_data_set = "hourly"
                log.information(
                    f"Hourly Data Processing for Simulation Duration of {simulation_duration_key} Days:"
                )
                # get statistical data
                self.get_statistics_of_data_and_write_to_excel(
                    filtered_data=filtered_data,
                    path_to_save=self.plot_path_complete,
                    kind_of_data_set=kind_of_data_set,
                )

                self.make_line_plot_for_pyam_dataframe(
                    filtered_data=filtered_data,
                    comparison_mode=comparion_mode,
                    title=self.path_addition,
                )
                self.make_line_plot_with_filling_for_pyam_dataframe(
                    filtered_data=filtered_data,
                    comparison_mode=comparion_mode,
                    title=self.path_addition,
                )

            else:
                raise ValueError(
                    "This kind of data was not found in the pyamdatacollectorenum class."
                )

    def make_line_plot_for_pyam_dataframe(
        self,
        filtered_data: pyam.IamDataFrame,
        comparison_mode: str,
        title: str,
    ) -> None:
        """Make line plot."""
        log.information("Make line plot with hourly data.")

        fig, a_x = plt.subplots(
            figsize=self.hisim_chartbase.figsize, dpi=self.hisim_chartbase.dpi
        )

        filtered_data.plot.line(
            ax=a_x,
            color=comparison_mode,
            title=title,
        )

        y_tick_labels, scale, y_tick_locations = self.set_axis_scale(a_x, x_or_y="y")
        plt.yticks(
            ticks=y_tick_locations,
            labels=y_tick_labels,
            fontsize=self.hisim_chartbase.fontsize_ticks,
        )
        plt.ylabel(
            ylabel=f"{scale}{filtered_data.unit[0]}",
            fontsize=self.hisim_chartbase.fontsize_label,
        )
        plt.xlabel(
            xlabel="Time",
            fontsize=self.hisim_chartbase.fontsize_label,
        )
        plt.title(label=title, fontsize=self.hisim_chartbase.fontsize_title)
        plt.tick_params(labelsize=self.hisim_chartbase.fontsize_ticks)

        fig.savefig(os.path.join(self.plot_path_complete, "line_plot.png"))
        plt.close()

    def make_line_plot_with_filling_for_pyam_dataframe(
        self,
        filtered_data: pyam.IamDataFrame,
        comparison_mode: str,
        title: str,
    ) -> None:
        """Make line plot with filling."""
        log.information("Make line plot with filling.")

        fig, a_x = plt.subplots(
            figsize=self.hisim_chartbase.figsize, dpi=self.hisim_chartbase.dpi
        )

        filtered_data.plot(
            ax=a_x,
            color=comparison_mode,
            title=title,
            fill_between=True,
        )

        y_tick_labels, scale, y_tick_locations = self.set_axis_scale(a_x, x_or_y="y")
        plt.yticks(
            ticks=y_tick_locations,
            labels=y_tick_labels,
            fontsize=self.hisim_chartbase.fontsize_ticks,
        )
        plt.ylabel(
            ylabel=f"{scale}{filtered_data.unit[0]}",
            fontsize=self.hisim_chartbase.fontsize_label,
        )
        plt.xlabel(
            xlabel="Time",
            fontsize=self.hisim_chartbase.fontsize_label,
        )
        plt.title(label=title, fontsize=self.hisim_chartbase.fontsize_title)
        plt.tick_params(labelsize=self.hisim_chartbase.fontsize_ticks)

        fig.savefig(os.path.join(self.plot_path_complete, "line_plot_with_filling.png"))
        plt.close()

    def make_bar_plot_for_pyam_dataframe(
        self,
        filtered_data: pyam.IamDataFrame,
        comparison_mode: str,
        title: str,
    ) -> None:
        """Make bar plot."""
        log.information("Make bar plot.")

        fig, a_x = plt.subplots(
            figsize=self.hisim_chartbase.figsize, dpi=self.hisim_chartbase.dpi
        )
        filtered_data.plot.bar(ax=a_x, stacked=True, bars=comparison_mode)

        y_tick_labels, scale, y_tick_locations = self.set_axis_scale(a_x, x_or_y="y")
        plt.yticks(
            ticks=y_tick_locations,
            labels=y_tick_labels,
            fontsize=self.hisim_chartbase.fontsize_ticks,
        )
        plt.ylabel(
            ylabel=f"{scale}{filtered_data.unit[0]}",
            fontsize=self.hisim_chartbase.fontsize_label,
        )
        plt.xlabel(
            xlabel=filtered_data.time_col.capitalize(),
            fontsize=self.hisim_chartbase.fontsize_label,
        )
        plt.title(label=title, fontsize=self.hisim_chartbase.fontsize_title)
        plt.tick_params(labelsize=self.hisim_chartbase.fontsize_ticks)

        plt.legend(loc=1)
        plt.tight_layout()
        a_x.tick_params(axis="x", rotation=45)
        fig.savefig(os.path.join(self.plot_path_complete, "bar_plot.png"))
        plt.close()

    def make_box_plot_for_pyam_dataframe(
        self,
        filtered_data: pyam.IamDataFrame,
        comparison_mode: str,
        title: str,
    ) -> None:
        """Make box plot."""
        log.information("Make box plot.")

        fig, a_x = plt.subplots(
            figsize=self.hisim_chartbase.figsize, dpi=self.hisim_chartbase.dpi
        )

        filtered_data.plot.box(
            ax=a_x,
            by="scenario",
            x="year",
            title=title,
            legend=True,  # comparison_mode
        )

        y_tick_labels, scale, y_tick_locations = self.set_axis_scale(a_x, x_or_y="y")
        plt.yticks(
            ticks=y_tick_locations,
            labels=y_tick_labels,
            fontsize=self.hisim_chartbase.fontsize_ticks,
        )
        plt.ylabel(
            ylabel=f"{scale}{filtered_data.unit[0]}",
            fontsize=self.hisim_chartbase.fontsize_label,
        )
        plt.xlabel(
            xlabel=filtered_data.time_col.capitalize(),
            fontsize=self.hisim_chartbase.fontsize_label,
        )
        plt.title(label=title, fontsize=self.hisim_chartbase.fontsize_title)
        plt.tick_params(labelsize=self.hisim_chartbase.fontsize_ticks)
        plt.tight_layout()
        plt.legend(loc=1)
        fig.savefig(os.path.join(self.plot_path_complete, "box_plot.png"))
        plt.close()

    def make_pie_plot_for_pyam_dataframe(
        self,
        filtered_data: pyam.IamDataFrame,
        comparison_mode: str,
        title: str,
    ) -> None:
        """Make pie plot."""
        log.information("Make pie plot.")

        fig, a_x = plt.subplots(
            figsize=self.hisim_chartbase.figsize, dpi=self.hisim_chartbase.dpi
        )
        filtered_data.plot.pie(
            ax=a_x,
            value="value",
            category=comparison_mode,
            title=title,
            legend=True,
            labels=None,
        )

        plt.title(label=title, fontsize=self.hisim_chartbase.fontsize_title)
        fig.subplots_adjust(right=0.75, left=0.3)
        plt.tight_layout()

        fig.savefig(os.path.join(self.plot_path_complete, "pie_plot.png"))
        plt.close()

    def make_scatter_plot_for_pyam_dataframe(
        self,
        pyam_dataframe: pyam.IamDataFrame,
        filter_model: Optional[str],
        filter_scenario: Optional[str],
        filter_variables: Optional[str],
        filter_region: Optional[str],
        filter_unit: Optional[str],
        filter_year: Optional[str],
        title: str,
        x_data_variable: str,
        y_data_variable: str,
    ) -> None:
        """Make scatter plot."""
        log.information("Make scatter plot.")

        filtered_data = self.filter_pyam_dataframe(
            pyam_dataframe=pyam_dataframe,
            filter_model=filter_model,
            filter_scenario=filter_scenario,
            filter_region=filter_region,
            filter_variables=filter_variables,
            filter_unit=filter_unit,
            filter_year=filter_year,
        )

        x_data = x_data_variable
        y_data = y_data_variable
        fig, a_x = plt.subplots(
            figsize=self.hisim_chartbase.figsize, dpi=self.hisim_chartbase.dpi
        )
        filtered_data.plot.scatter(
            ax=a_x,
            x=x_data,
            y=y_data,
        )

        (
            y_tick_labels,
            scale,
            y_tick_locations,
        ) = self.set_axis_scale(  # pylint: disable=unused-variable
            a_x, x_or_y="y"
        )

        plt.yticks(
            ticks=y_tick_locations,
            labels=y_tick_labels,
            fontsize=self.hisim_chartbase.fontsize_ticks,
        )
        plt.ylabel(
            ylabel=y_data.rsplit("|", maxsplit=1)[-1],  # + f" [{scale}°C]",
            fontsize=self.hisim_chartbase.fontsize_label,
        )
        plt.xlabel(
            xlabel=x_data.rsplit("|", maxsplit=1)[-1],  # + f" [{scale}°C]",
            fontsize=self.hisim_chartbase.fontsize_label,
        )
        plt.title(label=title, fontsize=self.hisim_chartbase.fontsize_title)
        plt.tick_params(labelsize=self.hisim_chartbase.fontsize_ticks)

        fig.savefig(os.path.join(self.plot_path_complete, "scatter_plot.png"))
        plt.close()

    def make_sankey_plot_for_pyam_dataframe(
        self,
        pyam_dataframe: pyam.IamDataFrame,
        filter_model: Optional[str],
        filter_scenario: Optional[str],
        filter_variables: Optional[str],
        filter_region: Optional[str],
        filter_unit: Optional[str],
        filter_year: Optional[str],
    ) -> None:
        """Make sankey plot."""
        log.information("Make sankey plot.")

        filtered_data = self.filter_pyam_dataframe(
            pyam_dataframe=pyam_dataframe,
            filter_model=filter_model,
            filter_scenario=filter_scenario,
            filter_region=filter_region,
            filter_variables=filter_variables,
            filter_unit=filter_unit,
            filter_year=filter_year,
        )

        sankey_mapping = {
            "ElectrcityGridBaseLoad|Electricity|ElectricityOutput": (
                "PV",
                "Occupancy",
            ),
            "PVSystemw-|Electricity|ElectricityOutput": ("PV", "Grid"),
            "Occupancy|Electricity|ElectricityOutput": ("Grid", "Occupancy"),
        }
        fig = filtered_data.plot.sankey(mapping=sankey_mapping)

        # save figure as html first
        plotly.offline.plot(
            fig,
            filename=os.path.join(self.plot_path_complete, "sankey_plot.html"),
            auto_open=False,
        )

        # convert html file to png
        hti = Html2Image()
        with open(
            os.path.join(self.plot_path_complete, "sankey_plot.html"),
            encoding="utf8",
        ) as file:
            hti.screenshot(
                file.read(),
                save_as="sankey_plot.png",
            )

        # change directory of sankey output file
        try:
            os.rename(
                "sankey_plot.png",
                os.path.join(self.plot_path_complete, "sankey_plot.png"),
            )
        except Exception as exc:
            raise Exception("Cannot save current sankey. Try again.") from exc

    def make_stack_plot_for_pyam_dataframe(
        self,
        filtered_data: pyam.IamDataFrame,
        # comparison_mode: str,
        title: str,
    ) -> None:
        """Make stack plot."""
        log.information("Make stack plot.")

        fig, a_x = plt.subplots(
            figsize=self.hisim_chartbase.figsize, dpi=self.hisim_chartbase.dpi
        )
        filtered_data.plot.stack(titel=title)

        y_tick_labels, scale, y_tick_locations = self.set_axis_scale(a_x, x_or_y="y")
        plt.yticks(
            ticks=y_tick_locations,
            labels=y_tick_labels,
            fontsize=self.hisim_chartbase.fontsize_ticks,
        )
        plt.ylabel(
            ylabel=f"{scale}{filtered_data.unit[0]}",
            fontsize=self.hisim_chartbase.fontsize_label,
        )
        plt.xlabel(
            xlabel=filtered_data.time_col.capitalize(),
            fontsize=self.hisim_chartbase.fontsize_label,
        )
        plt.title(label=title, fontsize=self.hisim_chartbase.fontsize_title)
        plt.tick_params(labelsize=self.hisim_chartbase.fontsize_ticks)

        fig.subplots_adjust(right=0.55)
        fig.savefig(os.path.join(self.plot_path_complete, "stack_plot.png"))

        plt.close()

    def set_axis_scale(self, a_x: Any, x_or_y: Any) -> Tuple[float, str, Any]:
        """Get axis and unit and scale it properly."""

        if x_or_y == "x":
            tick_values = a_x.get_xticks()
        elif x_or_y == "y":
            tick_values = a_x.get_yticks()
        else:
            raise ValueError("x_or_y must be either 'x' or 'y'")

        max_ticks = max(tick_values)
        min_ticks = min(tick_values)

        max_scale = max(abs(max_ticks), abs(min_ticks))

        if max_scale >= 1e12:
            new_tick_values = tick_values * 1e-12
            scale = "T"
        elif 1e9 <= max_scale < 1e12:
            new_tick_values = tick_values * 1e-9
            scale = "G"
        elif 1e6 <= max_scale < 1e9:
            new_tick_values = tick_values * 1e-6
            scale = "M"
        elif 1e3 <= max_scale < 1e6:
            new_tick_values = tick_values * 1e-3
            scale = "k"
        elif -1e3 <= max_scale < 1e3:
            new_tick_values = tick_values
            scale = ""

        tick_locations = tick_values
        tick_labels = np.round(new_tick_values, 1)

        return tick_labels, scale, tick_locations

    def filter_pyam_dataframe(
        self,
        pyam_dataframe: pyam.IamDataFrame,
        filter_model: Optional[str],
        filter_scenario: Optional[str],
        filter_variables: Optional[str],
        filter_region: Optional[str],
        filter_unit: Optional[str],
        filter_year: Optional[str],
    ) -> pyam.IamDataFrame:
        """Filter the pyam dataframe for the plots.

        If the value is None, it will be ignored.
        """

        filtered_data = pyam_dataframe

        if filter_model is not None:
            if filter_model not in filtered_data.model:
                raise ValueError(
                    f"Model {filter_model} not found in the pyam dataframe."
                )
            filtered_data = filtered_data.filter(model=filter_model)

        if filter_scenario is not None:
            if filter_scenario not in filtered_data.scenario:
                raise ValueError(
                    f"Scenario {filter_scenario} not found in the pyam dataframe."
                )
            filtered_data = filtered_data.filter(scenario=filter_scenario)

        if filter_variables is not None:
            # if filter_variables not in filtered_data.variable:
            #     raise ValueError(f"Variable {filter_variables} not found in the pyam dataframe.")
            filtered_data = filtered_data.filter(variable=filter_variables)
        if filter_region is not None:
            if filter_region not in filtered_data.region:
                raise ValueError(
                    f"Region {filter_region} not found in the pyam dataframe."
                )
            filtered_data = filtered_data.filter(region=filter_region)
        if filter_unit is not None:
            if filter_unit not in filtered_data.unit:
                raise ValueError(f"Unit {filter_unit} not found in the pyam dataframe.")
            filtered_data = filtered_data.filter(unit=filter_unit)
        if filter_year is not None:
            if (
                filtered_data.time_domain == "year"
                and filter_year not in filtered_data["year"]
            ):
                raise ValueError(f"Year {filter_year} not found in the pyam dataframe.")
            filtered_data = filtered_data.filter(year=filter_year)

        return filtered_data

    def decide_for_scenario_or_variable_comparison(
        self, filtered_data: pyam.IamDataFrame
    ) -> str:
        """Decide for each plot what will be compared, different scenarios or different variales."""

        if len(filtered_data.scenario) == 1 and len(filtered_data.variable) > 1:
            comparison_mode = "variable"
        elif len(filtered_data.scenario) > 1 and len(filtered_data.variable) == 1:
            comparison_mode = "scenario"
        else:
            raise ValueError(
                f"No comparison mode could be determined. There are {len(filtered_data.scenario)} scenarios and {len(filtered_data.variable)} variables filtered."
            )

        return comparison_mode

    def get_statistics_of_data_and_write_to_excel(
        self, filtered_data: pyam.IamDataFrame, path_to_save: str, kind_of_data_set: str
    ) -> None:
        """Use pandas describe method to get statistical values of certain data."""
        # create a excel writer object
        with pd.ExcelWriter(
            path=os.path.join(path_to_save, f"{kind_of_data_set}_statistics.xlsx"),
            mode="w",
        ) as writer:
            filtered_data.data.to_excel(excel_writer=writer, sheet_name="filtered data")
            statistical_data = filtered_data.data.describe()

            statistical_data.to_excel(excel_writer=writer, sheet_name="statistics")

    def check_if_scenario_exists_and_filter_dataframe_for_scenarios(
        self, data_frame: pd.DataFrame, list_of_scenarios_to_check: List[str], aggregate_data: bool
    ) -> pd.DataFrame:

        aggregated_scenario_dict: Dict = {key: [] for key in list_of_scenarios_to_check}

        for given_scenario in data_frame["scenario"]:
            # string comparison

            for scenario_to_check in list_of_scenarios_to_check:
                if (
                    scenario_to_check in given_scenario
                    and given_scenario
                    not in aggregated_scenario_dict[scenario_to_check]
                ):
                    aggregated_scenario_dict[scenario_to_check].append(given_scenario)
        # raise error if dict is empty
        for key_scenario_to_check, given_scenario in aggregated_scenario_dict.items():
            if given_scenario == []:
                raise ValueError(
                    f"Scenarios containing {key_scenario_to_check} were not found in the pyam dataframe."
                )

        concat_df = pd.DataFrame()
        # only take rows from dataframe which are in selected scenarios
        for key_scenario_to_check, given_scenario in aggregated_scenario_dict.items():

            df_filtered_for_specific_scenarios = data_frame.loc[
                data_frame["scenario"].isin(given_scenario)
            ]
            df_filtered_for_specific_scenarios["scenario"] = [key_scenario_to_check]* len(df_filtered_for_specific_scenarios["scenario"])
            concat_df = pd.concat([concat_df, df_filtered_for_specific_scenarios])

        if aggregate_data is True:
            concat_df = self.aggregate_dataframe_according_to_scenario_categories(data_frame=concat_df, list_of_scenarios_to_check=list_of_scenarios_to_check)
        return concat_df

    def aggregate_dataframe_according_to_scenario_categories(
        self, data_frame: pd.DataFrame, list_of_scenarios_to_check: List[str]
    ) -> pd.DataFrame:
        
        log.information(f"Data will be aggregated according to specified scenarios {list_of_scenarios_to_check}.")
        existing_variables = list(OrderedSet(data_frame["variable"]))

        new_dict: Dict = {key: [] for key in data_frame.columns}
        for scenario_to_check in list_of_scenarios_to_check:
            
            splitted_df_according_to_scenario = data_frame.loc[data_frame["scenario"]==scenario_to_check]
            
            for variable in existing_variables:
                all_examples_of_one_variable = splitted_df_according_to_scenario.loc[splitted_df_according_to_scenario["variable"]==variable]

                unit = list(all_examples_of_one_variable["unit"])[0]

                
                for index, time in enumerate(list(OrderedSet(splitted_df_according_to_scenario["time"]))):

                    values_for_this_time_step = list(all_examples_of_one_variable["value"].loc[all_examples_of_one_variable["time"]==time])
                    if unit in ["%", "-", "W", "kg/s", "m/s", "°C"]:
                        aggregated_value = np.mean(values_for_this_time_step)
                    else:
                        aggregated_value = np.sum(values_for_this_time_step)


                    for column in all_examples_of_one_variable.columns:
                        if column == "value":
                            new_dict[column].append(aggregated_value)
                        else:

                            new_dict[column].append(list(all_examples_of_one_variable[column])[index])

        new_df = pd.DataFrame(new_dict)
        return new_df

        # variables: Dict = {key: {} for key in data_frame["variable"]}
        # # order according to variables
        # for variable in data_frame["variable"]:
        #     for column in data_frame.columns:
        #         entries = list(
        #             data_frame.loc[data_frame["variable"] == variable][column]
        #         )
        #         variables[variable].update({column: entries})
        # print("variables", variables)
        # # now aggregate data
        # for variable_key, variable_dict in variables.items():
        #     # avoid that values duplicate
        #     dict_keys_that_contain_values = []
        #     for key in variable_dict.keys():
        #         variable_dict[key] = list(set(variable_dict[key]))
        #         # get key which contains the values of data (for yearly data the key is the year, for hourly data the key is the datetime)
        #         if not key.isalpha():
        #             dict_keys_that_contain_values.append(key)

        #     # iterate over all years or datetime keys
        #     for dict_key_with_values in dict_keys_that_contain_values:
        #         # if more than value exist, take mean or sum of these values
        #         if len(variable_dict[dict_key_with_values]) > 1:
        #             # take mean value for these units
        #             if variable_dict["unit"] in ["%", "-", "W", "kg/s", "m/s", "°C"]:
        #                 variable_dict[dict_key_with_values] = np.mean(
        #                     variable_dict[dict_key_with_values]
        #                 )
        #             # and take sum for these units
        #             else:
        #                 variable_dict[dict_key_with_values] = np.sum(
        #                     variable_dict[dict_key_with_values]
        #                 )

        # # make new df with aggregated data for specific scenarios that were given
        # new_dict: Dict = {key: [] for key in data_frame}
        # for dictio in variables.values():

        #     dictio["scenario"] = scenario_to_check

        #     for key, value in dictio.items():
        #         if isinstance(value, list):
        #             value = value[0]
        #         new_dict[key].append(value)

        # new_df = pd.DataFrame(new_dict)
        # return new_df


# examples for variables to check
# kpi data only in yearly format
kpi_data = [
    "Consumption",
    "Production",
    # "Self-consumption",
    # "Injection",
    "Self-consumption rate",
    # "Cost for energy use",
    # "CO2 emitted due energy use",
    # "Battery losses",
    "Autarky rate",
    "Annual investment cost for equipment (old version)",
    "Annual CO2 Footprint for equipment (old version)",
    # "Investment cost for equipment per simulated period",
    # "CO2 footprint for equipment per simulated period",
    # "System operational Cost for simulated period",
    # "System operational Emissions for simulated period",
    # "Total costs for simulated period",
    # "Total emissions for simulated period",
]

electricity_data = [
    "L2EMSElectricityController|Electricity|ElectricityToOrFromGrid",
    "PVSystem|Electricity|ElectricityOutput",
]

occuancy_consumption = [
    "Occupancy|Electricity|ElectricityOutput",
    "Occupancy|WarmWater|WaterConsumption",
]

heating_demand = [
    "HeatPumpHPLib|Heating|ThermalOutputPower",
    # "HeatDistributionSystem|Heating|ThermalOutputPower",
    "Building|Heating|TheoreticalThermalBuildingDemand",
]


def main():
    """Main function to execute the pyam data processing."""
    PyAmChartGenerator(
        simulation_duration_to_check=str(365),
        data_processing_mode=PyamDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_BUILDING_SIZES,
        # variables_to_check_for_hourly_data=heating_demand + electricity_data + occuancy_consumption,
        variables_to_check_for_yearly_data=kpi_data,
    )


if __name__ == "__main__":
    start_time = time.time()
    main()
    print(f"---{time.time() - start_time} seconds ___")
