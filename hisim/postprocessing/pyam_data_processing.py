"""Data Processing and Plotting for Scenario Comparison with Pyam."""


import glob
import datetime
import os
from typing import Dict, Any, Tuple, Optional, List
import string
import copy
import numpy as np
import pyam
import pandas as pd
import matplotlib.pyplot as plt
import plotly
from html2image import Html2Image
from ordered_set import OrderedSet
import seaborn as sns

from hisim.postprocessing.pyam_data_collection import (
    PyamDataTypeEnum,
    PyamDataProcessingModeEnum,
)
from hisim.postprocessing.chartbase import ChartFontsAndSize
from hisim import log

# TODO: debugging needed


class PyAmChartGenerator:

    """PyamChartGenerator class."""

    def __init__(
        self,
        simulation_duration_to_check: str,
        data_processing_mode: Any,
        time_resolution_of_data_set: Any,
        variables_to_check: Optional[List[str]] = None,
        # list_of_scenarios_to_check: Optional[List[str]] = None,
        dict_of_scenarios_to_check: Optional[Dict[str, List[str]]] = None,
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

        elif (
            data_processing_mode
            == PyamDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_DELTA_T_IN_HP_CONTROLLER
        ):
            data_path_strip = "data_with_different_delta_Ts"
            result_path_strip = "results_different_delta_Ts"

        elif (
            data_processing_mode
            == PyamDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_HOT_WATER_STORAGE_SIZES
        ):
            data_path_strip = "data_with_different_hot_water_storage_size_in_liters"
            result_path_strip = "results_different_hot_water_storage_size_in_liters"

        elif (
            data_processing_mode
            == PyamDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_SHARE_OF_MAXIMUM_PV
        ):
            data_path_strip = "data_with_different_share_of_maximum_pv_powers"
            result_path_strip = "results_different_share_of_maximum_pv_powers"

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

        if variables_to_check != [] and variables_to_check is not None:
            # read data, sort data according to scenarios if wanted, and create pandas dataframe
            (
                pandas_dataframe,
                key_for_scenario_one,
                key_for_current_scenario,
                variables_to_check,
            ) = self.get_dataframe_and_create_pandas_dataframe_for_all_data(
                folder_path=self.folder_path,
                time_resolution_of_data_set=time_resolution_of_data_set,
                dict_of_scenarios_to_check=dict_of_scenarios_to_check,
                variables_to_check=variables_to_check,
            )
            log.information("key for scneario one " + key_for_scenario_one)
            log.information("key for current scenario " + key_for_current_scenario)

            try:

                self.make_plots_with_specific_kind_of_data(
                    time_resolution_of_data_set=time_resolution_of_data_set,
                    pyam_dataframe=pandas_dataframe,
                    simulation_duration_key=simulation_duration_to_check,
                    variables_to_check=variables_to_check,
                )
            except Exception:
                log.information("Something went wrong while plotting.")
        else:
            log.information(
                "Variable list for data is not given and will not be plotted or anaylzed."
            )

    def get_dataframe_and_create_pandas_dataframe_for_all_data(
        self,
        folder_path: str,
        time_resolution_of_data_set: Any,
        # list_of_scenarios_to_check: Optional[List[str]],
        dict_of_scenarios_to_check: Optional[Dict[str, List[str]]],
        variables_to_check: List[str],
    ) -> Tuple[pd.DataFrame, str, str, List[str]]:
        """Get csv data and create pyam dataframes."""

        if time_resolution_of_data_set == PyamDataTypeEnum.HOURLY:
            kind_of_data_set = "hourly"
        elif time_resolution_of_data_set == PyamDataTypeEnum.YEARLY:
            kind_of_data_set = "yearly"
        elif time_resolution_of_data_set == PyamDataTypeEnum.DAILY:
            kind_of_data_set = "daily"
        elif time_resolution_of_data_set == PyamDataTypeEnum.MONTHLY:
            kind_of_data_set = "monthly"
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
        key_for_scenario_one = ""
        key_for_current_scenario = ""

        # filter scenarios
        # if (
        #     list_of_scenarios_to_check is not None
        #     and list_of_scenarios_to_check != []
        # ):

        #     file_df = self.check_if_scenario_exists_and_filter_dataframe_for_scenarios(
        #         data_frame=file_df,
        #         list_of_scenarios_to_check=list_of_scenarios_to_check,
        #     )

        # make rel electricity calculation before sorting and renaming

        if "ElectricityMeter|Electricity|ElectricityToOrFromGrid" in variables_to_check:

            file_df = self.calculate_relative_electricity_demand(dataframe=file_df)
            # file_df = file_df.reset_index()
            variables_to_check.append("Relative Electricity Demand")

        if dict_of_scenarios_to_check is not None and dict_of_scenarios_to_check != {}:

            (
                file_df,
                key_for_scenario_one,
                key_for_current_scenario,
            ) = self.check_if_scenario_exists_and_filter_dataframe_for_scenarios_dict(
                data_frame=file_df,
                # list_of_scenarios_to_check=list_of_scenarios_to_check,
                dict_of_scenarios_to_check=dict_of_scenarios_to_check,
            )

        return (
            file_df,
            key_for_scenario_one,
            key_for_current_scenario,
            variables_to_check,
        )

    def make_plots_with_specific_kind_of_data(
        self,
        time_resolution_of_data_set: Any,
        pyam_dataframe: pd.DataFrame,
        simulation_duration_key: str,
        variables_to_check: List[str],
    ) -> None:
        """Make plots for different kind of data."""

        log.information(f"Simulation duration: {simulation_duration_key} days.")

        if pyam_dataframe.empty:
            raise ValueError("Pyam dataframe is empty.")

        sub_results_folder = f"simulation_duration_of_{simulation_duration_key}_days"
        sub_sub_results_folder = (
            f"pyam_results_{time_resolution_of_data_set.value}_{self.datetime_string}"
        )

        self.path_for_plots = os.path.join(
            self.result_folder, sub_results_folder, sub_sub_results_folder
        )

        for variable_to_check in variables_to_check:
            log.information("Check variable " + str(variable_to_check))

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

            # # filter data according to variable
            # filtered_data = self.filter_pyam_dataframe(
            #     pyam_dataframe=pyam_dataframe,
            #     filter_model=None,
            #     filter_scenario=None,
            #     filter_region=None,
            #     filter_variables=variable_to_check,
            #     filter_unit=None,
            #     filter_year=None,
            # )
            filtered_data = self.filter_pandas_dataframe(
                dataframe=pyam_dataframe, variable_to_check=variable_to_check
            )

            # determine whether you want to compare one variable for different scenarios or different variables for one scenario
            # comparion_mode = self.decide_for_scenario_or_variable_comparison(
            #     filtered_data=filtered_data
            # )

            if time_resolution_of_data_set == PyamDataTypeEnum.YEARLY:
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

                # try:
                self.make_box_plot_for_pandas_dataframe(
                    filtered_data=filtered_data, title=self.path_addition,
                )
                # self.make_pie_plot_for_pyam_dataframe(
                #     filtered_data=filtered_data,
                #     comparison_mode=comparion_mode,
                #     title=self.path_addition,
                # )

                self.make_bar_plot_for_pandas_dataframe(
                    filtered_data=filtered_data, title=self.path_addition,
                )

                # if (
                #     variable_to_check
                #     == "ElectricityMeter|Electricity|ElectricityToOrFromGrid"
                # ):

                #     filtered_data = self.calculate_relative_electricity_demand(
                #         dataframe=filtered_data
                #     )
                #     scenario_set = []

                #     for scenario in filtered_data.scenario.values:

                #         share_of_pv_power = filtered_data.loc[
                #             filtered_data.scenario == scenario
                #         ].share_of_maximum_pv_power.values[-1]
                #         scenario_for_boxplot = (
                #             f"{scenario}_pv_share_{share_of_pv_power}"
                #         )
                #         scenario_set.append(scenario_for_boxplot)
                #     scenario_set = list(OrderedSet(scenario_set))

                #     self.path_addition = filtered_data.variable.values[0]
                #     self.plot_path_complete = os.path.join(
                #         self.path_for_plots, self.path_addition
                #     )
                #     if os.path.exists(self.plot_path_complete) is False:
                #         os.makedirs(self.plot_path_complete)

                #     self.make_box_plot_for_pandas_dataframe(
                #         filtered_data=filtered_data,
                #         title=self.path_addition,
                #         scenario_set=scenario_set,
                #     )
                #     self.make_bar_plot_for_pandas_dataframe(
                #         filtered_data=filtered_data,
                #         title=self.path_addition,
                #         alternative_bar_labels=scenario_set,
                #     )

                # except Exception:
                #     log.information(f"{variable_to_check} could not be plotted.")

                # self.make_scatter_plot_for_pyam_dataframe(
                #     pyam_dataframe=pyam_dataframe,
                #     filter_model=None,
                #     filter_scenario=None,
                #     filter_variables=None,
                #     title="HP vs Outside Temperatures",
                #     filter_region=None,
                #     filter_unit=None,
                #     filter_year=None,
                #     x_data_variable="Weather|Temperature|DailyAverageOutsideTemperatures",
                #     y_data_variable="HeatPumpHPLib|Heating|ThermalOutputPower",
                # )

            elif time_resolution_of_data_set in (
                PyamDataTypeEnum.HOURLY,
                PyamDataTypeEnum.DAILY,
                PyamDataTypeEnum.MONTHLY,
            ):

                if time_resolution_of_data_set == PyamDataTypeEnum.HOURLY:
                    kind_of_data_set = "hourly"
                    line_plot_marker_size = 2
                elif time_resolution_of_data_set == PyamDataTypeEnum.DAILY:
                    kind_of_data_set = "daily"
                    line_plot_marker_size = 3
                elif time_resolution_of_data_set == PyamDataTypeEnum.MONTHLY:
                    kind_of_data_set = "monthly"
                    line_plot_marker_size = 5

                log.information(
                    f"{kind_of_data_set} Data Processing for Simulation Duration of {simulation_duration_key} Days:"
                )
                # get statistical data
                self.get_statistics_of_data_and_write_to_excel(
                    filtered_data=filtered_data,
                    path_to_save=self.plot_path_complete,
                    kind_of_data_set=kind_of_data_set,
                )

                try:
                    self.make_line_plot_for_pandas_dataframe(
                        filtered_data=filtered_data,
                        title=self.path_addition,
                        line_plot_marker_size=line_plot_marker_size,
                    )

                # so far only working when scenarios_to_check are set
                # self.make_line_plot_with_filling_for_pyam_dataframe(
                #     filtered_data=filtered_data,
                #     comparison_mode=comparion_mode,
                #     title=self.path_addition,
                # )

                except Exception:
                    log.information(f"{variable_to_check} could not be plotted.")

            else:
                raise ValueError(
                    "This kind of data was not found in the pyamdatacollectorenum class."
                )

    def make_line_plot_for_pandas_dataframe(
        self, filtered_data: pd.DataFrame, title: str, line_plot_marker_size: int
    ) -> None:
        """Make line plot."""
        log.information("Make line plot with data.")

        fig, a_x = plt.subplots(
            figsize=self.hisim_chartbase.figsize, dpi=self.hisim_chartbase.dpi
        )
        x_data = list(OrderedSet(list(filtered_data.time)))

        year = str(filtered_data.time.values[0]).split("-")[0]

        x_data_transformed = np.asarray(x_data, dtype="datetime64[D]")

        for scenario in list(OrderedSet(list(filtered_data.scenario))):
            filtered_data_per_scenario = filtered_data.loc[
                filtered_data["scenario"] == scenario
            ]
            mean_values_aggregated_according_to_scenarios = []
            for time_value in x_data:

                mean_value_per_scenario_per_timestep = np.mean(
                    filtered_data_per_scenario.loc[
                        filtered_data_per_scenario["time"] == time_value
                    ]["value"]
                )

                mean_values_aggregated_according_to_scenarios.append(
                    mean_value_per_scenario_per_timestep
                )

            y_data = mean_values_aggregated_according_to_scenarios

            plt.plot(
                x_data_transformed,
                y_data,
                "-o",
                markersize=line_plot_marker_size,
                label=scenario,
            )

        y_tick_labels, unit, y_tick_locations = self.set_axis_scale(
            a_x, x_or_y="y", unit=filtered_data.unit.values[0]
        )
        plt.yticks(
            ticks=y_tick_locations,
            labels=y_tick_labels,
            fontsize=self.hisim_chartbase.fontsize_ticks,
        )

        plt.ylabel(
            ylabel=f"{unit}", fontsize=self.hisim_chartbase.fontsize_label,
        )
        plt.xlabel(
            xlabel=year, fontsize=self.hisim_chartbase.fontsize_label,
        )
        plt.title(label=title, fontsize=self.hisim_chartbase.fontsize_title)
        plt.tick_params(labelsize=self.hisim_chartbase.fontsize_ticks)
        a_x.tick_params(axis="x", labelrotation=45)
        plt.legend(bbox_to_anchor=(1, 1), loc="upper left")

        fig.savefig(
            os.path.join(self.plot_path_complete, "line_plot.png"), bbox_inches="tight"
        )
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

        y_tick_labels, unit, y_tick_locations = self.set_axis_scale(
            a_x, x_or_y="y", unit=filtered_data.unit[0]
        )
        plt.yticks(
            ticks=y_tick_locations,
            labels=y_tick_labels,
            fontsize=self.hisim_chartbase.fontsize_ticks,
        )
        plt.ylabel(
            ylabel=f"{unit}", fontsize=self.hisim_chartbase.fontsize_label,
        )
        plt.xlabel(
            xlabel="Time", fontsize=self.hisim_chartbase.fontsize_label,
        )
        plt.title(label=title, fontsize=self.hisim_chartbase.fontsize_title)
        plt.tick_params(labelsize=self.hisim_chartbase.fontsize_ticks)

        fig.savefig(os.path.join(self.plot_path_complete, "line_plot_with_filling.png"))
        plt.close()

    def make_bar_plot_for_pandas_dataframe(
        self,
        filtered_data: pd.DataFrame,
        title: str,
        alternative_bar_labels: Optional[List[str]] = None,
    ) -> None:
        """Make bar plot."""
        log.information("Make bar plot.")

        fig, a_x = plt.subplots(
            figsize=self.hisim_chartbase.figsize, dpi=self.hisim_chartbase.dpi
        )

        y_data = []
        bar_labels = []

        # for scenario in filtered_data.scenario:

        #     filtered_data_per_scenario = filtered_data.loc[
        #         filtered_data["scenario"] == scenario
        #     ]
        #     value = float(filtered_data_per_scenario["value"])

        #     y_data.append(value)
        #     bar_labels.append(scenario)

        for scenario in list(OrderedSet(list(filtered_data.scenario))):
            filtered_data_per_scenario = filtered_data.loc[
                filtered_data["scenario"] == scenario
            ]

            mean_value_per_scenario = np.mean(filtered_data_per_scenario.value.values)

            y_data.append(mean_value_per_scenario)
            bar_labels.append(scenario)

        # choose bar labels
        if alternative_bar_labels is not None:
            bar_labels = alternative_bar_labels

        x_data = np.arange(0, len(y_data) * 2, step=2)

        cmap = plt.get_cmap("viridis")
        colors = [cmap(i) for i in np.linspace(0, 1, len(x_data))]
        a_x.bar(x_data, y_data, label=bar_labels, color=colors)

        y_tick_labels, unit, y_tick_locations = self.set_axis_scale(
            a_x, x_or_y="y", unit=filtered_data.unit.values[0],
        )
        plt.yticks(
            ticks=y_tick_locations,
            labels=y_tick_labels,
            fontsize=self.hisim_chartbase.fontsize_ticks,
        )
        plt.ylabel(
            ylabel=f"{unit}", fontsize=self.hisim_chartbase.fontsize_label,
        )
        plt.xlabel(
            xlabel=filtered_data.year.values[0],
            fontsize=self.hisim_chartbase.fontsize_label,
        )
        plt.title(label=title, fontsize=self.hisim_chartbase.fontsize_title)
        plt.tick_params(labelsize=self.hisim_chartbase.fontsize_ticks)

        plt.legend(bbox_to_anchor=(1, 1), loc="upper left")

        a_x.xaxis.set_tick_params(labelbottom=False)
        a_x.set_xticks([])
        plt.tight_layout()
        fig.savefig(os.path.join(self.plot_path_complete, "bar_plot.png"))
        plt.close()

    def make_box_plot_for_pandas_dataframe(
        self,
        filtered_data: pd.DataFrame,
        title: str,
        scenario_set: Optional[List[str]] = None,
    ) -> None:
        """Make box plot."""
        log.information("Make box plot.")

        fig, a_x = plt.subplots(
            figsize=self.hisim_chartbase.figsize, dpi=self.hisim_chartbase.dpi
        )
        if scenario_set is None:
            scenario_set = list(OrderedSet(filtered_data.scenario))

        sns.boxplot(data=filtered_data, x="scenario", y="value")  #
        y_tick_labels, unit, y_tick_locations = self.set_axis_scale(
            a_x, x_or_y="y", unit=filtered_data.unit.values[0]
        )
        plt.yticks(
            ticks=y_tick_locations,
            labels=y_tick_labels,
            fontsize=self.hisim_chartbase.fontsize_ticks,
        )
        plt.ylabel(
            ylabel=f"{unit}", fontsize=self.hisim_chartbase.fontsize_label,
        )
        plt.xlabel(
            xlabel=filtered_data.year.values[0],
            fontsize=self.hisim_chartbase.fontsize_label,
        )
        plt.title(label=title, fontsize=self.hisim_chartbase.fontsize_title)
        plt.tick_params(labelsize=self.hisim_chartbase.fontsize_ticks)
        a_x.xaxis.set_tick_params(labelbottom=False)
        a_x.set_xticks([])
        plt.legend(scenario_set, bbox_to_anchor=(1, 1), loc="upper left")
        fig.savefig(
            os.path.join(self.plot_path_complete, "box_plot.png"), bbox_inches="tight"
        )
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
            unit,  # pylint: disable=unused-variable
            y_tick_locations,
        ) = self.set_axis_scale(a_x, x_or_y="y", unit=filtered_data.unit[0])

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

        y_tick_labels, unit, y_tick_locations = self.set_axis_scale(
            a_x, x_or_y="y", unit=filtered_data.unit[0]
        )
        plt.yticks(
            ticks=y_tick_locations,
            labels=y_tick_labels,
            fontsize=self.hisim_chartbase.fontsize_ticks,
        )
        plt.ylabel(
            ylabel=f"{unit}", fontsize=self.hisim_chartbase.fontsize_label,
        )
        plt.xlabel(
            xlabel=filtered_data.time_col.capitalize(),
            fontsize=self.hisim_chartbase.fontsize_label,
        )
        plt.title(label=title, fontsize=self.hisim_chartbase.fontsize_title)
        plt.tick_params(labelsize=self.hisim_chartbase.fontsize_ticks)

        fig.subplots_adjust(right=0.55)
        fig.savefig(os.path.join(self.plot_path_complete, "stack_plot.png"))

    def set_axis_scale(
        self, a_x: Any, x_or_y: Any, unit: Any
    ) -> Tuple[float, str, Any]:
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

        new_tick_values = tick_values
        scale = ""

        if unit not in ["-", "%"]:

            # if k already in unit, remove k first and then scale
            if unit in ["kg", "kWh", "kg/s", "kW"]:
                tick_values = tick_values * 1e3
                unit = unit.strip("k")

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
        unit = f"{scale}{unit}"

        return tick_labels, unit, tick_locations

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

    def filter_pandas_dataframe(
        self, dataframe: pd.DataFrame, variable_to_check: str
    ) -> pd.DataFrame:
        """Filter pandas dataframe according to variable."""

        return dataframe.loc[dataframe["variable"] == variable_to_check]

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
        self, filtered_data: pd.DataFrame, path_to_save: str, kind_of_data_set: str,
    ) -> None:
        """Use pandas describe method to get statistical values of certain data."""
        # create a excel writer object
        with pd.ExcelWriter(  # pylint: disable=abstract-class-instantiated
            path=os.path.join(path_to_save, f"{kind_of_data_set}_statistics.xlsx"),
            mode="w",
        ) as writer:

            filtered_data.to_excel(excel_writer=writer, sheet_name="filtered data")
            statistical_data = filtered_data.describe()

            statistical_data.to_excel(excel_writer=writer, sheet_name="statistics")

    def check_if_scenario_exists_and_filter_dataframe_for_scenarios(
        self,
        data_frame: pd.DataFrame,
        dict_of_scenarios_to_check: Dict[
            str, List[str]
        ],  # list_of_scenarios_to_check: List[str]
    ) -> pd.DataFrame:
        """Check if scenario exists and filter dataframe for scenario."""
        for (
            list_of_scenarios_to_check,
        ) in dict_of_scenarios_to_check.values():
            aggregated_scenario_dict: Dict = {
                key: [] for key in list_of_scenarios_to_check
            }

            for given_scenario in data_frame["scenario"]:
                # string comparison

                for scenario_to_check in list_of_scenarios_to_check:
                    if (
                        scenario_to_check in given_scenario
                        and given_scenario
                        not in aggregated_scenario_dict[scenario_to_check]
                    ):
                        aggregated_scenario_dict[scenario_to_check].append(
                            given_scenario
                        )
            # raise error if dict is empty
            for (
                key_scenario_to_check,
                given_scenario,
            ) in aggregated_scenario_dict.items():
                if given_scenario == []:
                    raise ValueError(
                        f"Scenarios containing {key_scenario_to_check} were not found in the pyam dataframe."
                    )

            concat_df = pd.DataFrame()
            # only take rows from dataframe which are in selected scenarios
            for (
                key_scenario_to_check,
                given_scenario,
            ) in aggregated_scenario_dict.items():

                df_filtered_for_specific_scenarios = data_frame.loc[
                    data_frame["scenario"].isin(given_scenario)
                ]
                df_filtered_for_specific_scenarios["scenario"] = [
                    key_scenario_to_check
                ] * len(df_filtered_for_specific_scenarios["scenario"])
                concat_df = pd.concat([concat_df, df_filtered_for_specific_scenarios])
                concat_df["scenario_0"] = data_frame["scenario"]

        return concat_df

    def check_if_scenario_exists_and_filter_dataframe_for_scenarios_dict(
        self,
        data_frame: pd.DataFrame,
        dict_of_scenarios_to_check: Dict[
            str, List[str]
        ],  # list_of_scenarios_to_check: List[str]
    ) -> Tuple[pd.DataFrame, str, str]:
        """Check if scenario exists and filter dataframe for scenario."""

        concat_df = data_frame  # copy.deepcopy(data_frame)
        filter_level_index = 0
        for (
            scenario_to_check_key,
            list_of_scenarios_to_check,
        ) in dict_of_scenarios_to_check.items():

            concat_df = self.check_for_one_scenario(
                dataframe=concat_df,
                list_of_scenarios_to_check=list_of_scenarios_to_check,
                column_name_to_check=scenario_to_check_key,
                filter_level_index=filter_level_index,
            )

            filter_level_index = filter_level_index + 1
        key_for_scenario_one = ""
        key_for_current_scenario = ""
        # rename scenario with all scenario filter levels
        for index in concat_df.index:
            # if even more filter levels need to add condition!
            if filter_level_index == 2:
                current_scenario_value = concat_df["scenario"][index]
                scenario_value_one = concat_df["scenario_1"][index]
                # scenario zero is original scenario that will be overwritten
                key_for_scenario_one = list(dict_of_scenarios_to_check.keys())[0]
                key_for_current_scenario = list(dict_of_scenarios_to_check.keys())[1]
                # concat_df.iloc[index, concat_df.columns.get_loc("scenario")] = f"{scenario_value_one}_{current_scenario_value}"
                concat_df.loc[
                    index, "scenario"
                ] = f"{scenario_value_one}_{current_scenario_value}"
            elif filter_level_index == 1:
                key_for_scenario_one = list(dict_of_scenarios_to_check.keys())[0]
                key_for_current_scenario = ""
        return concat_df, key_for_scenario_one, key_for_current_scenario

    def check_for_one_scenario(
        self,
        dataframe: pd.DataFrame,
        list_of_scenarios_to_check: List,
        column_name_to_check: str,
        filter_level_index: int,
    ) -> pd.DataFrame:
        """Check for one scenario."""

        aggregated_scenario_dict: Dict = {key: [] for key in list_of_scenarios_to_check}
        for scenario_to_check in list_of_scenarios_to_check:
            for value in dataframe[column_name_to_check].values:
                if (
                    isinstance(scenario_to_check, str)
                    and scenario_to_check in value
                    and value not in aggregated_scenario_dict[scenario_to_check]
                ):
                    aggregated_scenario_dict[scenario_to_check].append(value)
                elif (
                    isinstance(scenario_to_check, (float, int))
                    and scenario_to_check == value
                    and value not in aggregated_scenario_dict[scenario_to_check]
                ):

                    aggregated_scenario_dict[scenario_to_check].append(value)

        concat_df = pd.DataFrame()
        # new_df = copy.deepcopy(dataframe)
        # only take rows from dataframe which are in selected scenarios
        for (
            key_scenario_to_check,
            given_list_of_values,
        ) in aggregated_scenario_dict.items():

            df_filtered_for_specific_scenarios = dataframe.loc[
                dataframe[column_name_to_check].isin(given_list_of_values)
            ]

            # df_filtered_for_specific_scenarios.loc[df_filtered_for_specific_scenarios["scenario"]] = [
            #     key_scenario_to_check
            # ] * len(df_filtered_for_specific_scenarios["scenario"])
            df_filtered_for_specific_scenarios.loc[
                :, "scenario"
            ] = key_scenario_to_check

            concat_df = pd.concat(
                [concat_df, df_filtered_for_specific_scenarios], ignore_index=True
            )
            concat_df[f"scenario_{filter_level_index}"] = dataframe.loc[:, "scenario"]

            del df_filtered_for_specific_scenarios

        return concat_df

    def calculate_relative_electricity_demand(
        self, dataframe: pd.DataFrame
    ) -> pd.DataFrame:
        """Calculate relative electricity demand."""

        # look for ElectricityMeter|Electricity|ElectrcityToOrFromGrid output
        if (
            "ElectricityMeter|Electricity|ElectricityToOrFromGrid"
            not in dataframe.variable.values
        ):
            raise ValueError(
                "ElectricityMeter|Electricity|ElectricityToOrFromGrid was not found in variables."
            )

        # filter again just to be shure
        filtered_data = dataframe.loc[
            dataframe.variable == "ElectricityMeter|Electricity|ElectricityToOrFromGrid"
        ]

        if "share_of_maximum_pv_power" not in filtered_data.columns:
            raise ValueError(
                "share_of_maximum_pv_power was not found in dataframe columns"
            )
        # sort df accrofing to share of pv
        filtered_data = filtered_data.sort_values("share_of_maximum_pv_power")

        # # iterate over all new scenarios
        # for scenario in filtered_data.scenario.values:

        # iterate over all building codes
        for building_code in filtered_data.building_code.values:
            # data for this building code

            df_for_one_building_code = filtered_data.loc[
                filtered_data.building_code == building_code
            ]

            # get reference value (when share of pv power is zero)
            for (
                share_of_maximum_pv_power
            ) in df_for_one_building_code.share_of_maximum_pv_power.values:

                if share_of_maximum_pv_power == 0.0:

                    df_for_one_scenario_and_for_one_share = df_for_one_building_code.loc[
                        df_for_one_building_code.share_of_maximum_pv_power
                        == share_of_maximum_pv_power
                    ]

                    # df_demand_values = df_for_one_scenario_for_zero_pv_share.loc[df_for_one_scenario_for_zero_pv_share.value < 0]
                    # reference_value_for_electricity_demand = np.mean(
                    #     df_for_one_scenario_for_zero_pv_share.value.values
                    # )
                    reference_value_for_electricity_demand = (
                        df_for_one_scenario_and_for_one_share.value.values
                    )
                    value_for_electricity_demand = 0

                    # new_df_only_with_relative_electricity_demand = copy.deepcopy(df_for_one_scenario_and_for_one_share)
                    # new_df_only_with_relative_electricity_demand["variable"] = ["Relative Electricity Demand"]
                    # new_df_only_with_relative_electricity_demand["unit"] = ["%"]
                    # new_df_only_with_relative_electricity_demand["value"] = 1

                elif share_of_maximum_pv_power != 0.0:

                    df_for_one_scenario_and_for_one_share = df_for_one_building_code.loc[
                        df_for_one_building_code.share_of_maximum_pv_power
                        == share_of_maximum_pv_power
                    ]

                    # df_demand_values = df_for_one_scenario_for_nonzero_pv_share.loc[df_for_one_scenario_for_nonzero_pv_share.value < 0]
                    value_for_electricity_demand = (
                        df_for_one_scenario_and_for_one_share.value.values
                    )

                # calculate reference electricity demand for each scenario and share of pv power
                relative_electricity_demand = (
                    1
                    - (
                        (
                            reference_value_for_electricity_demand
                            - value_for_electricity_demand
                        )
                        / reference_value_for_electricity_demand
                    )
                ) * 100

                # make little df with new variable and value

                new_df_only_with_relative_electricity_demand = copy.deepcopy(
                    df_for_one_scenario_and_for_one_share
                )
                #
                new_df_only_with_relative_electricity_demand.loc[
                    :, "variable"
                ] = "Relative Electricity Demand"
                new_df_only_with_relative_electricity_demand.loc[:, "unit"] = "%"
                new_df_only_with_relative_electricity_demand.loc[
                    :, "value"
                ] = relative_electricity_demand

                del df_for_one_scenario_and_for_one_share

                dataframe = pd.concat(
                    [dataframe, new_df_only_with_relative_electricity_demand]
                )

                del dataframe["Unnamed: 0"]
                del new_df_only_with_relative_electricity_demand

        # write everything in df with new column and return df
        # new_df_only_with_relative_electricity_demand = copy.deepcopy(filtered_data)
        # new_df_only_with_relative_electricity_demand["variable"] = [
        #     "Relative Electricity Demand"
        # ] * len(filtered_data.variable.values)
        # new_df_only_with_relative_electricity_demand["unit"] = ["%"] * len(
        #     filtered_data.variable.values
        # )
        # new_df_only_with_relative_electricity_demand[
        #     "value"
        # ] = list_with_relative_electricity_demands

        return dataframe


class FilterClass:

    """Class for setting filters on the data for processing."""

    def __init__(self):
        """Initialize the class."""

        (
            self.kpi_data,
            self.electricity_data,
            self.occuancy_consumption,
            self.heating_demand,
        ) = self.get_variables_to_check()
        (
            self.building_type,
            self.building_refurbishment_state,
            self.building_age,
            self.pv_share,
        ) = self.get_scenarios_to_check()

    def get_variables_to_check(self):
        """Get specific variables to check for the scenario evaluation."""

        # examples for variables to check (check names of your variables before your evaluation, if they are correct)
        # kpi data has no time series, so only choose when you analyze yearly data
        kpi_data = [
            "Consumption",
            # "Production",
            # "Self-consumption",
            # "Injection",
            # "Self-consumption rate",
            # "Cost for energy use",
            # "CO2 emitted due energy use",
            # "Battery losses",
            # "Autarky rate",
            # "Annual investment cost for equipment (old version)",
            # "Annual CO2 Footprint for equipment (old version)",
            # "Investment cost for equipment per simulated period",
            # "CO2 footprint for equipment per simulated period",
            # "System operational Cost for simulated period",
            # "System operational Emissions for simulated period",
            "Total costs for simulated period",
            "Total emissions for simulated period",
            "Temperature deviation of building indoor air temperature being below set temperature 19 °C",
            "Minimum building indoor air temperature reached",
            "Temperature deviation of building indoor air temperature being above set temperature 24 °C",
            "Maximum building indoor air temperature reached",
            "Number of heat pump cycles",
        ]

        electricity_data = [
            # "L2EMSElectricityController|Electricity|ElectricityToOrFromGrid",
            # "PVSystem_w0|Electricity|ElectricityOutput", # check if pv was used or not
            # "ElectricityMeter|Electricity|ElectricityToGrid",
            "ElectricityMeter|Electricity|ElectricityFromGrid",
            # "ElectricityMeter|Electricity|ElectricityAvailable",
            # "ElectricityMeter|Electricity|ElectricityConsumption",
            # "ElectricityMeter|Electricity|ElectricityProduction"
        ]

        occuancy_consumption = [
            "Occupancy|Electricity|ElectricityOutput",
            "Occupancy|WarmWater|WaterConsumption",
        ]

        heating_demand = [
            "AdvancedHeatPumpHPLib|Heating|ThermalOutputPower",
            # "HeatDistributionSystem|Heating|ThermalOutputPower",
            # "Building|Heating|TheoreticalThermalBuildingDemand",
            "Building|Temperature|TemperatureIndoorAir",
        ]

        return kpi_data, electricity_data, occuancy_consumption, heating_demand

    def get_scenarios_to_check(self):
        """Get scenarios to check for scenario evaluation."""

        (
            building_type,
            building_refurbishment_state,
            building_age,
        ) = self.get_building_properties_to_check()

        pv_share = self.get_pv_properties_to_check()

        return building_type, building_refurbishment_state, building_age, pv_share

    def get_building_properties_to_check(self):
        """Get building properties."""

        # examples for scenarios to filter
        building_type = [
            "DE.N.SFH",
            "DE.N.TH",
            "DE.N.MFH",
            "DE.N.AB",
        ]

        building_refurbishment_state = [
            "001.001",
            "001.002",
            "001.003",
        ]

        building_age = [
            "01.Gen",
            "02.Gen",
            "03.Gen",
            "04.Gen",
            "05.Gen",
            "06.Gen",
            "07.Gen",
            "08.Gen",
            "09.Gen",
            "10.Gen",
            "11.Gen",
            "12.Gen",
        ]

        return building_type, building_refurbishment_state, building_age

    def get_pv_properties_to_check(self):
        """Get pv properties."""

        # examples for scenarios to filter
        pv_share = [0, 0.25, 0.5, 1]

        return pv_share
