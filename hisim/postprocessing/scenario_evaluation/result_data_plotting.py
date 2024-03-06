"""Data Processing and Plotting for Scenario Comparison."""


import datetime
import os
from typing import Dict, Any, Tuple, Optional, List, Union
import string
import warnings
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors

# import plotly
# from html2image import Html2Image
from ordered_set import OrderedSet
import seaborn as sns

from hisim.postprocessing.scenario_evaluation.result_data_collection import (
    ResultDataTypeEnum,
    ResultDataProcessingModeEnum,
)
from hisim.postprocessing.scenario_evaluation.result_data_processing import ScenarioDataProcessing
from hisim.postprocessing.chartbase import ChartFontsAndSize
from hisim import log


class ScenarioChartGeneration:

    """ScenarioChartGeneration class."""

    def __init__(
        self,
        simulation_duration_to_check: str,
        data_processing_mode: Any,
        time_resolution_of_data_set: Any,
        dict_with_extra_information_for_specific_plot: Dict[str, Dict],
        variables_to_check: Optional[List[str]] = None,
        dict_of_scenarios_to_check: Optional[Dict[str, List[str]]] = None,
    ) -> None:
        """Initialize the class."""

        warnings.filterwarnings("ignore")

        self.datetime_string = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        self.show_plot_legend: bool = True
        self.data_processing_mode = data_processing_mode

        if self.data_processing_mode == ResultDataProcessingModeEnum.PROCESS_ALL_DATA:
            data_path_strip = "data_with_all_parameters"
            result_path_strip = "results_for_all_parameters"
            self.show_plot_legend = False

        elif self.data_processing_mode == ResultDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_BUILDING_CODES:
            data_path_strip = "data_with_different_building_codes"
            result_path_strip = "results_different_building_codes"

        elif self.data_processing_mode == ResultDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_BUILDING_SIZES:
            data_path_strip = "data_with_different_conditioned_floor_area_in_m2s"
            result_path_strip = "results_different_conditioned_floor_area_in_m2s"

        elif self.data_processing_mode == ResultDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_PV_AZIMUTH_ANGLES:
            data_path_strip = "data_with_different_pv_azimuths"
            result_path_strip = "results_different_pv_azimuths"

        elif self.data_processing_mode == ResultDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_PV_TILT_ANGLES:
            data_path_strip = "data_with_different_pv_tilts"
            result_path_strip = "results_different_pv_tilts"

        elif self.data_processing_mode == ResultDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_SHARE_OF_MAXIMUM_PV:
            data_path_strip = "data_with_different_share_of_maximum_pv_powers"
            result_path_strip = "results_different_share_of_maximum_pv_powers"

        elif self.data_processing_mode == ResultDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_NUMBER_OF_DWELLINGS:
            data_path_strip = "data_with_different_number_of_dwellings_per_buildings"
            result_path_strip = "results_different_number_of_dwellings_per_buildings"

        else:
            raise ValueError("DataProcessingMode not known.")

        self.data_folder_path = os.path.join(
            os.getcwd(),
            os.pardir,
            os.pardir,
            os.pardir,
            "system_setups",
            "results_for_scenario_comparison",
            "data",
            data_path_strip,
        )

        self.result_folder = os.path.join(
            os.getcwd(),
            os.pardir,
            os.pardir,
            os.pardir,
            "system_setups",
            "results_for_scenario_comparison",
            "results",
            result_path_strip,
        )

        self.hisim_chartbase = ChartFontsAndSize()
        self.hisim_chartbase.figsize = (10, 6)
        self.hisim_chartbase.dpi = 100

        if variables_to_check != [] and variables_to_check is not None:
            # read data, sort data according to scenarios if wanted, and create pandas dataframe
            (
                pandas_dataframe,
                key_for_scenario_one,
                key_for_current_scenario,
                variables_to_check,
            ) = ScenarioDataProcessing.get_dataframe_and_create_pandas_dataframe_for_all_data(
                data_folder_path=self.data_folder_path,
                time_resolution_of_data_set=time_resolution_of_data_set,
                dict_of_scenarios_to_check=dict_of_scenarios_to_check,
                variables_to_check=variables_to_check,
            )

            log.information("key for scenario one " + key_for_scenario_one)
            log.information("key for current scenario " + key_for_current_scenario)

            self.make_plots_with_specific_kind_of_data(
                time_resolution_of_data_set=time_resolution_of_data_set,
                pandas_dataframe=pandas_dataframe,
                simulation_duration_key=simulation_duration_to_check,
                variables_to_check=variables_to_check,
                dict_with_extra_information_for_specific_plot=dict_with_extra_information_for_specific_plot,
            )

        else:
            log.information("Variable list for data is not given and will not be plotted or anaylzed.")

    def make_plots_with_specific_kind_of_data(
        self,
        time_resolution_of_data_set: Any,
        pandas_dataframe: pd.DataFrame,
        simulation_duration_key: str,
        variables_to_check: List[str],
        dict_with_extra_information_for_specific_plot: Dict[str, Dict],
    ) -> None:
        """Make plots for different kind of data."""

        log.information(f"Simulation duration: {simulation_duration_key} days.")

        if pandas_dataframe.empty:
            raise ValueError("Dataframe is empty.")

        sub_results_folder = f"simulation_duration_of_{simulation_duration_key}_days"
        sub_sub_results_folder = f"scenario_comparison_{time_resolution_of_data_set.value}_{self.datetime_string}"

        self.path_for_plots = os.path.join(self.result_folder, sub_results_folder, sub_sub_results_folder)

        for variable_to_check in variables_to_check:
            log.information("Check variable " + str(variable_to_check))

            # prepare path for plots
            self.path_addition = "".join(
                [x for x in variable_to_check if x in string.ascii_letters or x.isspace() or x == "2"]
            )

            self.plot_path_complete = os.path.join(self.path_for_plots, self.path_addition)
            if os.path.exists(self.plot_path_complete) is False:
                os.makedirs(self.plot_path_complete)

            # filter the dataframe according to variable
            filtered_data = ScenarioDataProcessing.filter_pandas_dataframe(
                dataframe=pandas_dataframe, variable_to_check=variable_to_check
            )
            # get unit of variable
            try:
                unit = str(filtered_data.unit.values[0])
                unit = str(filtered_data.unit.values[0])
            except Exception:
                if "Temperature deviation" in variable_to_check:
                    unit = "°C*h"
                else:
                    unit = "-"

            if time_resolution_of_data_set == ResultDataTypeEnum.YEARLY:
                kind_of_data_set = "yearly"

                # get statistical data
                ScenarioDataProcessing.get_statistics_of_data_and_write_to_excel(
                    filtered_data=filtered_data,
                    path_to_save=self.plot_path_complete,
                    kind_of_data_set=kind_of_data_set,
                )

                # try:
                self.make_box_plot_for_pandas_dataframe(
                    filtered_data=filtered_data, title=self.path_addition,
                )
                # except Exception:
                #     log.information(f"{variable_to_check} could not be plotted as box plot.")

                # try:
                self.make_bar_plot_for_pandas_dataframe(
                    filtered_data=filtered_data, title=self.path_addition, unit=unit
                )
                # except Exception:
                #     log.information(f"{variable_to_check} could not be plotted as bar plot.")

                try:
                    x_data_variable = dict_with_extra_information_for_specific_plot["scatter"]["x_data_variable"]
                    self.make_scatter_plot_for_pandas_dataframe_for_yearly_data(
                        full_pandas_dataframe=pandas_dataframe,
                        filtered_data=filtered_data,
                        y_data_variable=self.path_addition,
                        x_data_variable=x_data_variable,
                    )
                except Exception:
                    log.information(f"{variable_to_check} could not be plotted as scatter plot.")

                try:
                    self.make_histogram_plot_for_pandas_dataframe(
                        filtered_data=filtered_data, title=self.path_addition, unit=unit
                    )
                except Exception:
                    log.information(f"{variable_to_check} could not be plotted as histogram.")

                if variable_to_check in [
                    dict_with_extra_information_for_specific_plot["stacked_bar"]["y1_data_variable"],
                    dict_with_extra_information_for_specific_plot["stacked_bar"]["y2_data_variable"],
                ]:
                    y1_data_variable = dict_with_extra_information_for_specific_plot["stacked_bar"]["y1_data_variable"]
                    y2_data_variable = dict_with_extra_information_for_specific_plot["stacked_bar"]["y2_data_variable"]
                    use_y1_as_bottom_for_y2 = dict_with_extra_information_for_specific_plot["stacked_bar"][
                        "use_y1_as_bottom_for_y2"
                    ]
                    sort_according_to_y1_or_y2_data = dict_with_extra_information_for_specific_plot["stacked_bar"][
                        "sort_according_to_y1_or_y2_data"
                    ]
                    self.make_stacked_bar_plot_for_pandas_dataframe(
                        full_pandas_dataframe=pandas_dataframe,
                        y1_data_variable=y1_data_variable,
                        y2_data_variable=y2_data_variable,
                        use_y1_as_bottom_for_y2=use_y1_as_bottom_for_y2,
                        sort_according_to_y1_or_y2_data=sort_according_to_y1_or_y2_data,
                    )

                if variable_to_check in [
                    dict_with_extra_information_for_specific_plot["stacked_bar"]["y1_data_variable"],
                    dict_with_extra_information_for_specific_plot["stacked_bar"]["y2_data_variable"],
                ]:
                    y1_data_variable = dict_with_extra_information_for_specific_plot["stacked_bar"]["y1_data_variable"]
                    y2_data_variable = dict_with_extra_information_for_specific_plot["stacked_bar"]["y2_data_variable"]
                    use_y1_as_bottom_for_y2 = dict_with_extra_information_for_specific_plot["stacked_bar"][
                        "use_y1_as_bottom_for_y2"
                    ]
                    sort_according_to_y1_or_y2_data = dict_with_extra_information_for_specific_plot["stacked_bar"][
                        "sort_according_to_y1_or_y2_data"
                    ]
                    self.make_stacked_bar_plot_for_pandas_dataframe(
                        full_pandas_dataframe=pandas_dataframe,
                        y1_data_variable=y1_data_variable,
                        y2_data_variable=y2_data_variable,
                        use_y1_as_bottom_for_y2=use_y1_as_bottom_for_y2,
                        sort_according_to_y1_or_y2_data=sort_according_to_y1_or_y2_data,
                    )

            elif time_resolution_of_data_set in (
                ResultDataTypeEnum.HOURLY,
                ResultDataTypeEnum.DAILY,
                ResultDataTypeEnum.MONTHLY,
            ):
                if time_resolution_of_data_set == ResultDataTypeEnum.HOURLY:
                    kind_of_data_set = "hourly"
                    line_plot_marker_size = 2
                elif time_resolution_of_data_set == ResultDataTypeEnum.DAILY:
                    kind_of_data_set = "daily"
                    line_plot_marker_size = 3
                elif time_resolution_of_data_set == ResultDataTypeEnum.MONTHLY:
                    kind_of_data_set = "monthly"
                    line_plot_marker_size = 5

                # get statistical data
                ScenarioDataProcessing.get_statistics_of_data_and_write_to_excel(
                    filtered_data=filtered_data,
                    path_to_save=self.plot_path_complete,
                    kind_of_data_set=kind_of_data_set,
                )

                # try:
                self.make_line_plot_for_pandas_dataframe(
                    filtered_data=filtered_data, title=self.path_addition, line_plot_marker_size=line_plot_marker_size,
                )
                # except Exception:
                #     log.information(f"{variable_to_check} could not be plotted as line plot.")
                try:
                    self.make_box_plot_for_pandas_dataframe(filtered_data=filtered_data, title=self.path_addition)

                except Exception:
                    log.information(f"{variable_to_check} could not be plotted as box plot.")

                try:
                    x_data_variable = dict_with_extra_information_for_specific_plot["scatter"]["x_data_variable"]
                    self.make_line_scatter_plot_for_pandas_dataframe(
                        full_pandas_dataframe=pandas_dataframe,
                        filtered_data=filtered_data,
                        y_data_variable=self.path_addition,
                        x_data_variable=x_data_variable,
                        line_plot_marker_size=line_plot_marker_size,
                    )
                except Exception:
                    log.information(f"{variable_to_check} could not be plotted as line scatter plot.")

                try:
                    x_data_variable = dict_with_extra_information_for_specific_plot["scatter"]["x_data_variable"]
                    self.make_line_scatter_plot_for_pandas_dataframe(
                        full_pandas_dataframe=pandas_dataframe,
                        filtered_data=filtered_data,
                        y_data_variable=self.path_addition,
                        x_data_variable=x_data_variable,
                        line_plot_marker_size=line_plot_marker_size,
                    )
                except Exception:
                    log.information(f"{variable_to_check} could not be plotted as line scatter plot.")

            else:
                raise ValueError("This kind of data was not found in the datacollectorenum class.")

    def make_line_plot_for_pandas_dataframe(
        self, filtered_data: pd.DataFrame, title: str, line_plot_marker_size: int
    ) -> None:
        """Make line plot."""
        log.information("Make line plot with data.")

        fig, a_x = plt.subplots(figsize=self.hisim_chartbase.figsize, dpi=self.hisim_chartbase.dpi)
        x_data = list(OrderedSet(list(filtered_data.time)))
        if filtered_data.time.values[0] is not None:
            year = filtered_data.time.values[0].split("-")[0]
        else:
            raise ValueError("year could not be determined because time value of filtered data was None.")

        x_data_transformed = np.asarray(x_data, dtype="datetime64[D]")
        color, edgecolor = self.set_plot_colors_according_to_data_processing_mode(
            number_of_scenarios=len(list(OrderedSet(list(filtered_data.scenario)))),
            data_processing_mode=self.data_processing_mode,
        )
        del edgecolor
        y_data = []
        for index, scenario in enumerate(list(OrderedSet(list(filtered_data.scenario)))):
            filtered_data_per_scenario = filtered_data.loc[filtered_data["scenario"] == scenario]
            mean_values_aggregated_according_to_scenarios = []
            for time_value in x_data:
                mean_value_per_scenario_per_timestep = np.mean(
                    filtered_data_per_scenario.loc[filtered_data_per_scenario["time"] == time_value]["value"]
                )

                mean_values_aggregated_according_to_scenarios.append(mean_value_per_scenario_per_timestep)

            y_data = mean_values_aggregated_according_to_scenarios

            plt.plot(
                x_data_transformed, y_data, "-o", markersize=line_plot_marker_size, label=scenario, color=color[index]
            )

        self.set_ticks_labels_legend_and_save_fig(
            fig=fig,
            a_x=a_x,
            x_axis_label=year,
            y_axis_unit=filtered_data.unit.values[0],
            show_legend=self.show_plot_legend,
            title=title,
            plot_type_name="line_plot",
            rotate_x_ticks=True,
        )
        self.set_ticks_labels_legend_and_save_fig(
            fig=fig,
            a_x=a_x,
            x_axis_label=year,
            y_axis_unit=filtered_data.unit.values[0],
            show_legend=self.show_plot_legend,
            title=title,
            plot_type_name="line_plot",
            rotate_x_ticks=True,
        )

    def make_bar_plot_for_pandas_dataframe(
        self, filtered_data: pd.DataFrame, title: str, unit: str, alternative_bar_labels: Optional[List[str]] = None,
    ) -> None:
        """Make bar plot."""
        log.information("Make bar plot.")

        fig, a_x = plt.subplots(figsize=self.hisim_chartbase.figsize, dpi=self.hisim_chartbase.dpi)

        y_data = []
        bar_labels = []
        x_data: Any

        for scenario in list(OrderedSet(list(filtered_data.scenario))):
            filtered_data_per_scenario = filtered_data.loc[filtered_data["scenario"] == scenario]

            mean_value_per_scenario = np.mean(filtered_data_per_scenario.value.values)

            y_data.append(mean_value_per_scenario)
            bar_labels.append(scenario)

        # choose bar labels
        if alternative_bar_labels is not None:
            bar_labels = alternative_bar_labels

        # sort y_data and labels
        y_data_sorted, bar_labels_sorted = self.sort_y_values_according_to_data_processing_mode(
            data_processing_mode=self.data_processing_mode, zip_list_one=y_data, zip_list_two=bar_labels
        )
        # if no scenarios chosen, make artificial x ticks
        if self.data_processing_mode == ResultDataProcessingModeEnum.PROCESS_ALL_DATA:
            x_data = np.arange(0, len(y_data) * 2, step=2)
            rotate_x_ticks = False
            x_axis_label = ""
            show_x_ticks = False
        # otherwise choose scenarios as x-ticks
        else:
            x_data = bar_labels_sorted
            rotate_x_ticks = True
            x_axis_label = ""
            show_x_ticks = True

        color, edgecolor = self.set_plot_colors_according_to_data_processing_mode(
            number_of_scenarios=len(bar_labels), data_processing_mode=self.data_processing_mode
        )
        a_x.bar(x_data, y_data_sorted, label=bar_labels_sorted, color=color, edgecolor=edgecolor)

        self.set_ticks_labels_legend_and_save_fig(
            fig=fig,
            a_x=a_x,
            title=title,
            show_legend=False,
            plot_type_name="bar_plot",
            y_axis_unit=unit,
            x_axis_label=x_axis_label,
            show_x_ticks=show_x_ticks,
            rotate_x_ticks=rotate_x_ticks,
            x_ticks=np.arange(len(bar_labels)),
            x_tick_labels=bar_labels_sorted,
        )

    def make_box_plot_for_pandas_dataframe(
        self, filtered_data: pd.DataFrame, title: str, scenario_set: Optional[List[str]] = None,
    ) -> None:
        """Make box plot."""
        log.information("Make box plot.")

        fig, a_x = plt.subplots(figsize=self.hisim_chartbase.figsize, dpi=self.hisim_chartbase.dpi)
        if scenario_set is None:
            scenario_set = list(OrderedSet(filtered_data.scenario))

        sns.boxplot(data=filtered_data, x="scenario", y="value", palette="Spectral")

        self.set_ticks_labels_legend_and_save_fig(
            fig=fig,
            a_x=a_x,
            x_axis_label="",
            y_axis_unit=filtered_data.unit.values[0],
            show_legend=False,
            legend_labels=scenario_set,
            title=title,
            plot_type_name="box_plot",
            show_x_ticks=True,
            x_ticks=np.arange(len(scenario_set)),
            x_tick_labels=scenario_set,
            rotate_x_ticks=True,
        )

    def make_histogram_plot_for_pandas_dataframe(
        self, filtered_data: pd.DataFrame, title: str, unit: str, scenario_set: Optional[List[str]] = None,
    ) -> None:
        """Make histogram plot."""
        log.information("Make histogram plot.")

        fig, a_x = plt.subplots(  # pylint: disable=unused-variable
            figsize=self.hisim_chartbase.figsize, dpi=self.hisim_chartbase.dpi
        )
        if scenario_set is None:
            scenario_set = list(OrderedSet(filtered_data.scenario))

        plt.hist(x=np.array(filtered_data.value.values), bins="auto")

        self.set_ticks_labels_legend_and_save_fig(
            fig=fig,
            a_x=a_x,
            x_axis_label=title,
            y_axis_label="Count",
            x_axis_unit=unit,
            show_legend=False,
            plot_type_name="histogram_plot",
        )

    def make_scatter_plot_for_pandas_dataframe_for_yearly_data(
        self,
        full_pandas_dataframe: pd.DataFrame,
        filtered_data: pd.DataFrame,
        y_data_variable: str,
        x_data_variable: str = "Specific heating demand according to TABULA",
    ) -> None:
        """Make scatter plot."""
        log.information("Make scatter plot with data.")

        fig, a_x = plt.subplots(figsize=self.hisim_chartbase.figsize, dpi=self.hisim_chartbase.dpi)

        # iterate over all scenarios
        x_data_mean_value_list_for_all_scenarios = []
        y_data_mean_value_list_for_all_scenarios = []
        for scenario in list(OrderedSet(list(full_pandas_dataframe.scenario))):
            full_data_per_scenario = full_pandas_dataframe.loc[full_pandas_dataframe["scenario"] == scenario]
            filtered_data_per_scenario = filtered_data.loc[filtered_data["scenario"] == scenario]

            # get x_data_list by filtering the df according to x_data_variable and then by taking values from "value" column
            x_data_list = list(
                full_data_per_scenario.loc[full_data_per_scenario["variable"] == x_data_variable]["value"].values
            )
            x_data_unit = full_data_per_scenario.loc[full_data_per_scenario["variable"] == x_data_variable][
                "unit"
            ].values[0]

            # if x_data_list has more than 1 value (because more values for this scenario exist), then take mean value
            if len(x_data_list) > 1:
                # for each scenario take the mean value
                x_data_mean_value_per_scenario = np.mean(x_data_list)
            elif len(x_data_list) == 1:
                x_data_mean_value_per_scenario = x_data_list[0]
            else:
                raise ValueError(
                    "The x_data_list is empty. Probably the full dataframe did not contain the x_data_variable in the column variable."
                )

            # append to x_data_mean_value_list
            x_data_mean_value_list_for_all_scenarios.append(x_data_mean_value_per_scenario)

            # get y values from filtered data per scenario (already filtered according to variable to check and scenario)
            y_data_list = list(filtered_data_per_scenario["value"].values)
            y_data_unit = filtered_data_per_scenario["unit"].values[0]
            # if y_data_list has more than 1 value (because more values for this scenario exist), then take mean value
            if len(y_data_list) > 1:
                # for each scenario take the mean value
                y_data_mean_value_per_scenario = np.mean(y_data_list)
            elif len(y_data_list) == 1:
                y_data_mean_value_per_scenario = y_data_list[0]
            else:
                raise ValueError(
                    "The y_data_list is empty. Something went wrong with the filtering in the functions before."
                )

            # append to y_data_mean_value_list
            y_data_mean_value_list_for_all_scenarios.append(y_data_mean_value_per_scenario)

        # identify marker size accroding to data length
        scatter_plot_marker_size = self.get_scatter_marker_size(
            data_length=len(x_data_mean_value_list_for_all_scenarios)
        )

        # make scatter plot
        plt.scatter(
            x_data_mean_value_list_for_all_scenarios,
            y_data_mean_value_list_for_all_scenarios,
            s=scatter_plot_marker_size,
        )

        self.set_ticks_labels_legend_and_save_fig(
            fig=fig,
            a_x=a_x,
            x_axis_label=x_data_variable,
            y_axis_label=y_data_variable,
            x_axis_unit=x_data_unit,
            y_axis_unit=y_data_unit,
            show_legend=self.show_plot_legend,
            plot_type_name="scatter_plot",
        )

    def make_line_scatter_plot_for_pandas_dataframe(
        self,
        full_pandas_dataframe: pd.DataFrame,
        filtered_data: pd.DataFrame,
        line_plot_marker_size: int,
        y_data_variable: str,
        x_data_variable: str = "Specific heating demand according to TABULA",
    ) -> None:
        """Make line scatter plot."""
        log.information("Make line scatter plot.")

        fig, a_x = plt.subplots(figsize=self.hisim_chartbase.figsize, dpi=self.hisim_chartbase.dpi)

        # iterate over all scenarios
        for scenario in list(OrderedSet(list(full_pandas_dataframe.scenario))):
            full_data_per_scenario = full_pandas_dataframe.loc[full_pandas_dataframe["scenario"] == scenario]
            filtered_data_per_scenario = filtered_data.loc[filtered_data["scenario"] == scenario]

            # get x_data_list by filtering the df according to x_data_variable and then by taking values from "value" column
            x_data_list = list(
                full_data_per_scenario.loc[full_data_per_scenario["variable"] == x_data_variable]["value"].values
            )
            x_data_unit = full_data_per_scenario.loc[full_data_per_scenario["variable"] == x_data_variable][
                "unit"
            ].values[0]

            # get y values from filtered data per scenario (already filtered according to variable to check and scenario)
            y_data_list = list(filtered_data_per_scenario["value"].values)
            y_data_unit = filtered_data_per_scenario["unit"].values[0]

            # make scatter plot
            plt.plot(x_data_list, y_data_list, "-o", markersize=line_plot_marker_size)

        self.set_ticks_labels_legend_and_save_fig(
            fig=fig,
            a_x=a_x,
            x_axis_label=x_data_variable,
            y_axis_label=y_data_variable,
            x_axis_unit=x_data_unit,
            y_axis_unit=y_data_unit,
            show_legend=self.show_plot_legend,
            plot_type_name="line_scatter_plot",
        )

    def make_stacked_bar_plot_for_pandas_dataframe(
        self,
        full_pandas_dataframe: pd.DataFrame,
        y1_data_variable: str,
        y2_data_variable: str,
        use_y1_as_bottom_for_y2: Optional[bool] = True,
        sort_according_to_y1_or_y2_data: Optional[str] = None,
    ) -> None:
        """Make stacked bar plot."""
        log.information("Make stacked bar plot.")

        fig, a_x = plt.subplots(figsize=self.hisim_chartbase.figsize, dpi=self.hisim_chartbase.dpi)

        # iterate over all scenarios
        y1_data_mean_value_list_for_all_scenarios = []
        y2_data_mean_value_list_for_all_scenarios = []

        for scenario in list(OrderedSet(list(full_pandas_dataframe.scenario))):
            full_data_per_scenario = full_pandas_dataframe.loc[full_pandas_dataframe["scenario"] == scenario]

            # get y1_data_list by filtering the df according to y1_data_variable and then by taking values from "value" column
            y1_data_list = list(
                full_data_per_scenario.loc[full_data_per_scenario["variable"] == y1_data_variable]["value"].values
            )
            y1_data_unit = full_data_per_scenario.loc[full_data_per_scenario["variable"] == y1_data_variable][
                "unit"
            ].values[0]
            # get y2_data_list by filtering the df according to y2_data_variable and then by taking values from "value" column
            y2_data_list = list(
                full_data_per_scenario.loc[full_data_per_scenario["variable"] == y2_data_variable]["value"].values
            )
            y2_data_unit = full_data_per_scenario.loc[full_data_per_scenario["variable"] == y2_data_variable][
                "unit"
            ].values[0]
            if y1_data_unit != y2_data_unit:
                raise ValueError("The units of y1 and y2 data variables must be the same for the stacked bar plot.")

            # if y1_data_list has more than 1 value (because more values for this scenario exist), then take mean value
            if len(y1_data_list) > 1:
                # for each scenario take the mean value
                y1_data_mean_value_per_scenario = np.mean(y1_data_list)
            elif len(y1_data_list) == 1:
                y1_data_mean_value_per_scenario = y1_data_list[0]
            else:
                raise ValueError(
                    "The y1_data_list is empty. Probably the full dataframe did not contain the y1_data_variable in the column variable."
                )
            # if y2_data_list has more than 1 value (because more values for this scenario exist), then take mean value
            if len(y2_data_list) > 1:
                # for each scenario take the mean value
                y2_data_mean_value_per_scenario = np.mean(y2_data_list)
            elif len(y2_data_list) == 1:
                y2_data_mean_value_per_scenario = y2_data_list[0]
            else:
                raise ValueError(
                    "The y2_data_list is empty. Probably the full dataframe did not contain the y2_data_variable in the column variable."
                )

            # append to y1_data_mean_value_list
            y1_data_mean_value_list_for_all_scenarios.append(y1_data_mean_value_per_scenario)

            # append to y2_data_mean_value_list
            y2_data_mean_value_list_for_all_scenarios.append(y2_data_mean_value_per_scenario)

        x_data = np.arange(0, len(y1_data_mean_value_list_for_all_scenarios) * 2, step=2)
        # x_data = list(OrderedSet(list(full_pandas_dataframe.scenario)))

        # sort values if demanded
        if sort_according_to_y1_or_y2_data == "y1":
            (
                y1_data_mean_value_list_for_all_scenarios_sorted,
                y2_data_mean_value_list_for_all_scenarios_sorted,
            ) = self.sort_y_values_according_to_data_processing_mode(
                data_processing_mode=self.data_processing_mode,
                zip_list_one=y1_data_mean_value_list_for_all_scenarios,
                zip_list_two=y2_data_mean_value_list_for_all_scenarios,
            )

        elif sort_according_to_y1_or_y2_data == "y2":
            (
                y2_data_mean_value_list_for_all_scenarios_sorted,
                y1_data_mean_value_list_for_all_scenarios_sorted,
            ) = self.sort_y_values_according_to_data_processing_mode(
                data_processing_mode=self.data_processing_mode,
                zip_list_one=y2_data_mean_value_list_for_all_scenarios,
                zip_list_two=y1_data_mean_value_list_for_all_scenarios,
            )
        else:
            y1_data_mean_value_list_for_all_scenarios_sorted = y1_data_mean_value_list_for_all_scenarios
            y2_data_mean_value_list_for_all_scenarios_sorted = y2_data_mean_value_list_for_all_scenarios

        a_x.bar(x_data, y1_data_mean_value_list_for_all_scenarios_sorted, color="r")
        if use_y1_as_bottom_for_y2 is True:
            a_x.bar(
                x_data,
                y2_data_mean_value_list_for_all_scenarios_sorted,
                bottom=y1_data_mean_value_list_for_all_scenarios_sorted,
                color="b",
            )
        else:
            a_x.bar(x_data, y2_data_mean_value_list_for_all_scenarios_sorted, color="b")

        self.set_ticks_labels_legend_and_save_fig(
            fig=fig,
            a_x=a_x,
            show_legend=True,
            plot_type_name="stacked_bar_plot",
            y_axis_unit=y1_data_unit,
            x_axis_label=full_pandas_dataframe.year.values[0],
            show_x_ticks=False,
            legend_labels=[y1_data_variable, y2_data_variable],
        )

    def get_scatter_marker_size(self, data_length: int) -> int:
        """Get scatter marker size."""
        if data_length < 10:
            scatter_plot_marker_size = 20
        elif 10 < data_length < 50:
            scatter_plot_marker_size = 16
        elif 50 < data_length < 100:
            scatter_plot_marker_size = 8
        elif 100 < data_length < 300:
            scatter_plot_marker_size = 6
        elif 300 < data_length < 500:
            scatter_plot_marker_size = 4
        elif 500 < data_length < 1000:
            scatter_plot_marker_size = 2
        else:
            scatter_plot_marker_size = 1
        return scatter_plot_marker_size

    def set_axis_scale(self, a_x: Any, x_or_y: Any, unit: Any) -> Tuple[float, str, Any]:
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

        if unit not in ["-", "%", "m2"]:
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

        # if k already in unit, remove k and replace with "M"
        if unit in ["kkWh", "kkW"]:
            unit = "M" + unit[2:]
        elif unit in ["kkg", "kkg/s"]:
            unit = "t" + unit[3:]

        return tick_labels, unit, tick_locations

    def set_ticks_labels_legend_and_save_fig(
        self,
        fig: Any,
        a_x: Any,
        show_legend: bool,
        plot_type_name: str,
        y_axis_label: str = "",
        y_axis_unit: str = "",
        x_axis_label: str = "",
        x_axis_unit: str = "",
        title: str = "",
        legend_labels: Optional[Any] = None,
        x_ticks: Any = None,
        x_tick_labels: Any = None,
        show_x_ticks: bool = True,
        rotate_x_ticks: bool = False,
    ) -> None:
        """Set ticks, labels and legend for plot and save."""

        # y-ticks
        y_tick_labels, y_axis_unit, y_tick_locations = self.set_axis_scale(a_x=a_x, x_or_y="y", unit=y_axis_unit)
        plt.yticks(
            ticks=y_tick_locations, labels=y_tick_labels, fontsize=self.hisim_chartbase.fontsize_ticks,
        )

        # y-label
        if y_axis_label != "" and y_axis_unit != "":
            plt.ylabel(
                ylabel=f"{y_axis_label} \n [{y_axis_unit}]", fontsize=self.hisim_chartbase.fontsize_label,
            )
        elif y_axis_label != "" and y_axis_unit == "":
            plt.ylabel(
                ylabel=f"{y_axis_label}", fontsize=self.hisim_chartbase.fontsize_label,
            )
        else:
            plt.ylabel(
                ylabel=f"[{y_axis_unit}]", fontsize=self.hisim_chartbase.fontsize_label,
            )

        # x-label
        if x_axis_label != "" and x_axis_unit != "":
            plt.xlabel(
                xlabel=f"{x_axis_label} \n [{x_axis_unit}]", fontsize=self.hisim_chartbase.fontsize_label,
            )
        elif x_axis_label != "" and x_axis_unit == "":
            plt.xlabel(
                xlabel=f"{x_axis_label}", fontsize=self.hisim_chartbase.fontsize_label,
            )
        elif x_axis_label == "" and x_axis_unit != "":
            plt.xlabel(
                xlabel=f"[{x_axis_unit}]", fontsize=self.hisim_chartbase.fontsize_label,
            )
        else:
            pass

        # title
        if title != "":
            plt.title(label=title, fontsize=self.hisim_chartbase.fontsize_title)

        # x-ticks
        if rotate_x_ticks:
            if x_ticks is None and x_tick_labels is None:
                a_x.tick_params(axis="x", labelrotation=45)
            else:
                a_x.set_xticks(x_ticks, x_tick_labels, rotation=45, ha="right", rotation_mode="anchor")

        if show_x_ticks:
            pass
        else:
            a_x.xaxis.set_tick_params(labelbottom=False)
            a_x.set_xticks([])

        plt.tick_params(labelsize=self.hisim_chartbase.fontsize_ticks)

        # legend
        if show_legend:
            if legend_labels is None:
                plt.legend(bbox_to_anchor=(1, 1), loc="upper left")
            else:
                plt.legend(legend_labels,)

        # save and close
        fig.savefig(
            os.path.join(self.plot_path_complete, f"{plot_type_name}.png"), bbox_inches="tight",
        )
        plt.close()

    def set_plot_colors_according_to_data_processing_mode(
        self, data_processing_mode: ResultDataProcessingModeEnum, number_of_scenarios: int
    ) -> Tuple[List[str], Optional[str]]:
        """Set plot colors according to data processing mode."""
        # color_palette = list(mcolors.TABLEAU_COLORS.values())
        color_palette = sns.color_palette("Spectral", n_colors=number_of_scenarios)  #

        color: List[str] = []
        edgecolor: Optional[str] = None
        if data_processing_mode == ResultDataProcessingModeEnum.PROCESS_ALL_DATA:
            for i in range(0, number_of_scenarios):
                color.append("blue")
        else:
            for i in range(0, number_of_scenarios):
                color.append(color_palette[i])
            edgecolor = "black"
        return color, edgecolor

    def sort_y_values_according_to_data_processing_mode(
        self, data_processing_mode: ResultDataProcessingModeEnum, zip_list_one: List, zip_list_two: List
    ) -> Tuple[List, List]:
        """Decide whether to sort y values or not."""
        # if all data is processed and no scenario is chosen, the y values for plots should be sorted
        if data_processing_mode == ResultDataProcessingModeEnum.PROCESS_ALL_DATA:
            sorted_zip_lists = sorted(zip(zip_list_one, zip_list_two), reverse=True)
            list_one_sorted = [y1 for y1, y2 in sorted_zip_lists]
            list_two_sorted = [y2 for y1, y2 in sorted_zip_lists]
            return list_one_sorted, list_two_sorted
        # otherwise the order of the scenarios should be maintained
        return zip_list_one, zip_list_two

    # def make_sankey_plot_for_pyam_dataframe(
    #     self,
    #     pyam_dataframe: pyam.IamDataFrame,
    #     filter_model: Optional[str],
    #     filter_scenario: Optional[str],
    #     filter_variables: Optional[str],
    #     filter_region: Optional[str],
    #     filter_unit: Optional[str],
    #     filter_year: Optional[str],
    # ) -> None:
    #     """Make sankey plot."""
    #     log.information("Make sankey plot.")

    #     filtered_data = self.filter_pyam_dataframe(
    #         pyam_dataframe=pyam_dataframe,
    #         filter_model=filter_model,
    #         filter_scenario=filter_scenario,
    #         filter_region=filter_region,
    #         filter_variables=filter_variables,
    #         filter_unit=filter_unit,
    #         filter_year=filter_year,
    #     )

    #     sankey_mapping = {
    #         "ElectrcityGridBaseLoad|Electricity|ElectricityOutput": (
    #             "PV",
    #             "Occupancy",
    #         ),
    #         "PVSystemw-|Electricity|ElectricityOutput": ("PV", "Grid"),
    #         "Occupancy|Electricity|ElectricityOutput": ("Grid", "Occupancy"),
    #     }
    #     fig = filtered_data.plot.sankey(mapping=sankey_mapping)

    #     # save figure as html first
    #     plotly.offline.plot(
    #         fig,
    #         filename=os.path.join(self.plot_path_complete, "sankey_plot.html"),
    #         auto_open=False,
    #     )

    #     # convert html file to png
    #     hti = Html2Image()
    #     with open(
    #         os.path.join(self.plot_path_complete, "sankey_plot.html"), encoding="utf8",
    #     ) as file:
    #         hti.screenshot(
    #             file.read(), save_as="sankey_plot.png",
    #         )

    #     # change directory of sankey output file
    #     try:
    #         os.rename(
    #             "sankey_plot.png",
    #             os.path.join(self.plot_path_complete, "sankey_plot.png"),
    #         )
    #     except Exception as exc:
    #         raise Exception("Cannot save current sankey. Try again.") from exc
