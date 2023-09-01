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
from hisim.postprocessing.pyam_data_collection import PyamDataTypeEnum, PyamDataProcessingModeEnum
from hisim.postprocessing.chartbase import ChartFontsAndSize
from hisim import log


class PyAmChartGenerator:

    """PyamChartGenerator class."""

    def __init__(
        self,
        simulation_duration_to_check: str,
        data_processing_mode: Any,
        analyze_yearly_or_hourly_data: Any = None,
        variables_to_check_for_hourly_data: Optional[List[str]] = None,
        variables_to_check_for_yearly_data: Optional[List[str]] = None,
        
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
            data_path_strip = "data_with_different_building_types"
            result_path_strip = "results_different_building_types"

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

        if analyze_yearly_or_hourly_data == PyamDataTypeEnum.YEARLY:

            dict_of_yearly_pyam_dataframes_for_different_simulation_durations = self.get_dataframe_and_create_pyam_dataframe_for_all_data(
                folder_path=self.folder_path, kind_of_data=PyamDataTypeEnum.YEARLY
            )

            if (
            variables_to_check_for_yearly_data != []
            and variables_to_check_for_yearly_data is not None
            ):
                self.make_plots_with_specific_kind_of_data(
                    kind_of_data=PyamDataTypeEnum.YEARLY,
                    dict_of_data=dict_of_yearly_pyam_dataframes_for_different_simulation_durations,
                    simulation_duration_key=simulation_duration_to_check,
                    variables_to_check=variables_to_check_for_yearly_data,
                )
            else:
                log.information(
                    "Variable list for yearly data is not given and will not be plotted or anaylzed."
                )

        elif analyze_yearly_or_hourly_data == PyamDataTypeEnum.HOURLY:

            dict_of_hourly_pyam_dataframes_for_different_simulation_durations = self.get_dataframe_and_create_pyam_dataframe_for_all_data(
                folder_path=self.folder_path, kind_of_data=PyamDataTypeEnum.HOURLY
            )
            if (
            variables_to_check_for_hourly_data != []
            and variables_to_check_for_hourly_data is not None
        ):
                self.make_plots_with_specific_kind_of_data(
                    kind_of_data=PyamDataTypeEnum.HOURLY,
                    dict_of_data=dict_of_hourly_pyam_dataframes_for_different_simulation_durations,
                    simulation_duration_key=simulation_duration_to_check,
                    variables_to_check=variables_to_check_for_hourly_data,
                )
            else:
                log.information(
                    "Variable list for hourly data is not given and will not be plotted or anaylzed."
                )
            
        else:
            raise ValueError("analyze_yearly_or_hourly_data variable is not set or has incompatible value.")




    def get_dataframe_and_create_pyam_dataframe_for_all_data(
        self, folder_path: str, kind_of_data: Any
    ) -> Dict:
        """Get csv data and create pyam dataframes."""

        if kind_of_data == PyamDataTypeEnum.YEARLY:
            kind_of_data_set = "yearly"
        elif kind_of_data == PyamDataTypeEnum.HOURLY:
            kind_of_data_set = "hourly"
        else:
            raise ValueError(
                "This kind of data was not found in the pyamdaacollectorenum class."
            )
        log.information(
            f"Read csv files and create one big pyam dataframe for {kind_of_data_set} data."
        )
        dict_of_different_pyam_dataframes_of_different_simulation_parameters = {}

        for file in glob.glob(
            os.path.join(folder_path, "**", f"*{kind_of_data_set}*.csv")
        ):

            file_df = pd.read_csv(filepath_or_buffer=file)

            # if scenario values are no strings, transform them
            file_df["Scenario"] = file_df["Scenario"].transform(str)

            # create pyam dataframe
            pyam_dataframe = pyam.IamDataFrame(file_df)

            simulation_duration = re.findall(string=file, pattern=r"\d+")[-1]
            dict_of_different_pyam_dataframes_of_different_simulation_parameters[
                f"{simulation_duration}"
            ] = pyam_dataframe

        if dict_of_different_pyam_dataframes_of_different_simulation_parameters == {}:
            raise ValueError(
                f"The dictionary is empty. Please check your filepath {folder_path} if there is the right csv file."
            )

        return dict_of_different_pyam_dataframes_of_different_simulation_parameters

    def make_plots_with_specific_kind_of_data(
        self,
        kind_of_data: Any,
        dict_of_data: Dict[str, pyam.IamDataFrame],
        simulation_duration_key: str,
        variables_to_check: List[str],
    ) -> None:
        """Make plots for different kind of data."""

        log.information(f"Simulation duration: {simulation_duration_key} days.")
        pyam_dataframe = dict_of_data[simulation_duration_key]

        if pyam_dataframe.empty:
            raise ValueError("Pyam dataframe is empty.")

        log.information("Pyam dataframe columns " + str(pyam_dataframe.dimensions))
        log.information("Pyam dataframe scenarios " + str(pyam_dataframe.scenario))
        # log.information("Pyam Variables " + str(pyam_dataframe.variable))

        sub_results_folder = f"simulation_duration_of_{simulation_duration_key}_days"
        sub_sub_results_folder = f"pyam_results_{self.datetime_string}"

        self.path_for_plots = os.path.join(
            self.result_folder, sub_results_folder, sub_sub_results_folder
        )

        for variable_to_check in variables_to_check:

            # prepare path for plots
            print(variable_to_check)
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

            # determine whether you want to compare one variable for different scenarios or different variables for one scenario
            comparion_mode = self.decide_for_scenario_or_variable_comparison(
                filtered_data=filtered_data
            )

            if kind_of_data == PyamDataTypeEnum.YEARLY:
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

                self.make_bar_plot_for_pyam_dataframe(
                    filtered_data=filtered_data,
                    comparison_mode=comparion_mode,
                    title=self.path_addition,
                )

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
            elif kind_of_data == PyamDataTypeEnum.HOURLY:
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
        self, filtered_data: pyam.IamDataFrame, comparison_mode: str, title: str,
    ) -> None:
        """Make line plot."""
        log.information("Make line plot with hourly data.")

        fig, a_x = plt.subplots(
            figsize=self.hisim_chartbase.figsize, dpi=self.hisim_chartbase.dpi
        )
        filtered_data.plot.line(
            ax=a_x, color=comparison_mode, title=title,
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
            xlabel="Time", fontsize=self.hisim_chartbase.fontsize_label,
        )
        plt.title(label=title, fontsize=self.hisim_chartbase.fontsize_title)
        plt.tick_params(labelsize=self.hisim_chartbase.fontsize_ticks)

        fig.savefig(os.path.join(self.plot_path_complete, "line_plot.png"))
        plt.close()

    def make_line_plot_with_filling_for_pyam_dataframe(
        self, filtered_data: pyam.IamDataFrame, comparison_mode: str, title: str,
    ) -> None:
        """Make line plot with filling."""
        log.information("Make line plot with filling.")

        fig, a_x = plt.subplots(
            figsize=self.hisim_chartbase.figsize, dpi=self.hisim_chartbase.dpi
        )

        filtered_data.plot(
            ax=a_x, color=comparison_mode, title=title, fill_between=True,
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
            xlabel="Time", fontsize=self.hisim_chartbase.fontsize_label,
        )
        plt.title(label=title, fontsize=self.hisim_chartbase.fontsize_title)
        plt.tick_params(labelsize=self.hisim_chartbase.fontsize_ticks)

        fig.savefig(os.path.join(self.plot_path_complete, "line_plot_with_filling.png"))
        plt.close()

    def make_bar_plot_for_pyam_dataframe(
        self, filtered_data: pyam.IamDataFrame, comparison_mode: str, title: str,
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
        self, filtered_data: pyam.IamDataFrame, comparison_mode: str, title: str,
    ) -> None:
        """Make box plot."""
        log.information("Make box plot.")

        fig, a_x = plt.subplots(
            figsize=self.hisim_chartbase.figsize, dpi=self.hisim_chartbase.dpi
        )

        filtered_data.plot.box(
            ax=a_x, by=comparison_mode, x="year", title=title, legend=True,
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
        self, filtered_data: pyam.IamDataFrame, comparison_mode: str, title: str,
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
            ax=a_x, x=x_data, y=y_data,
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
            os.path.join(self.plot_path_complete, "sankey_plot.html"), encoding="utf8",
        ) as file:
            hti.screenshot(
                file.read(), save_as="sankey_plot.png",
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




# examples for variables to check
# kpi data only in yearly format
kpi_data = [
    "Consumption",
    "Production",
    "Self-consumption",
    "Injection",
    "Self-consumption rate",
    "Cost for energy use",
    "CO2 emitted due energy use",
    "Battery losses",
    "Autarky rate",
    "Annual investment cost for equipment (old version)",
    "Annual CO2 Footprint for equipment (old version)",
    "Investment cost for equipment per simulated period",
    "CO2 footprint for equipment per simulated period",
    "System operational Cost for simulated period",
    "System operational Emissions for simulated period",
    "Total costs for simulated period",
    "Total emissions for simulated period",
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
