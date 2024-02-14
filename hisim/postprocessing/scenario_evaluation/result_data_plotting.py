"""Data Processing and Plotting for Scenario Comparison."""


import datetime
import os
from typing import Dict, Any, Tuple, Optional, List
import string
import warnings
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# import plotly
# from html2image import Html2Image
from ordered_set import OrderedSet
import seaborn as sns

from hisim.postprocessing.scenario_evaluation.result_data_collection import (
    ResultDataTypeEnum,
    ResultDataProcessingModeEnum,
)
from hisim.postprocessing.scenario_evaluation.result_data_processing import (
    ScenarioDataProcessing,
)
from hisim.postprocessing.chartbase import ChartFontsAndSize
from hisim import log


class ScenarioChartGeneration:

    """ScenarioChartGeneration class."""

    def __init__(
        self,
        simulation_duration_to_check: str,
        data_processing_mode: Any,
        time_resolution_of_data_set: Any,
        variables_to_check: Optional[List[str]] = None,
        dict_of_scenarios_to_check: Optional[Dict[str, List[str]]] = None,
    ) -> None:
        """Initialize the class."""

        warnings.filterwarnings("ignore")

        self.datetime_string = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        self.show_plot_legend: bool = True

        if data_processing_mode == ResultDataProcessingModeEnum.PROCESS_ALL_DATA:
            data_path_strip = "data_with_all_parameters"
            result_path_strip = "results_for_all_parameters"
            self.show_plot_legend = False

        elif data_processing_mode == ResultDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_BUILDING_CODES:
            data_path_strip = "data_with_different_building_codes"
            result_path_strip = "results_different_building_codes"

        elif data_processing_mode == ResultDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_BUILDING_SIZES:
            data_path_strip = "data_with_different_conditioned_floor_area_in_m2s"
            result_path_strip = "results_different_conditioned_floor_area_in_m2s"

        elif data_processing_mode == ResultDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_PV_AZIMUTH_ANGLES:
            data_path_strip = "data_with_different_pv_azimuths"
            result_path_strip = "results_different_pv_azimuths"

        elif data_processing_mode == ResultDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_PV_TILT_ANGLES:
            data_path_strip = "data_with_different_pv_tilts"
            result_path_strip = "results_different_pv_tilts"

        elif data_processing_mode == ResultDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_SHARE_OF_MAXIMUM_PV:
            data_path_strip = "data_with_different_share_of_maximum_pv_powers"
            result_path_strip = "results_different_share_of_maximum_pv_powers"

        elif data_processing_mode == ResultDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_NUMBER_OF_DWELLINGS:
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
            )

        else:
            log.information("Variable list for data is not given and will not be plotted or anaylzed.")

    def make_plots_with_specific_kind_of_data(
        self,
        time_resolution_of_data_set: Any,
        pandas_dataframe: pd.DataFrame,
        simulation_duration_key: str,
        variables_to_check: List[str],
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
                unit = filtered_data.unit.values[0]
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

                try:
                    self.make_box_plot_for_pandas_dataframe(
                        filtered_data=filtered_data,
                        title=self.path_addition,
                    )
                except Exception:
                    log.information(f"{variable_to_check} could not be plotted as box plot.")

                try:
                    self.make_bar_plot_for_pandas_dataframe(
                        filtered_data=filtered_data, title=self.path_addition, unit=unit
                    )
                except Exception:
                    log.information(f"{variable_to_check} could not be plotted as bar plot.")

                try:
                    self.make_scatter_plot_for_pandas_dataframe(
                        full_pandas_dataframe=pandas_dataframe,
                        filtered_data=filtered_data,
                        y_data_variable=self.path_addition,
                    )
                except Exception:
                    log.information(f"{variable_to_check} could not be plotted as scatter plot.")

                try:
                    self.make_histogram_plot_for_pandas_dataframe(
                        filtered_data=filtered_data, title=self.path_addition, unit=unit
                    )
                except Exception:
                    log.information(f"{variable_to_check} could not be plotted as histogram.")

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

                try:
                    self.make_line_plot_for_pandas_dataframe(
                        filtered_data=filtered_data,
                        title=self.path_addition,
                        line_plot_marker_size=line_plot_marker_size,
                    )
                except Exception:
                    log.information(f"{variable_to_check} could not be plotted as line plot.")
                try:
                    self.make_box_plot_for_pandas_dataframe(filtered_data=filtered_data, title=self.path_addition)

                except Exception:
                    log.information(f"{variable_to_check} could not be plotted as box plot.")

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

        for scenario in list(OrderedSet(list(filtered_data.scenario))):
            filtered_data_per_scenario = filtered_data.loc[filtered_data["scenario"] == scenario]
            mean_values_aggregated_according_to_scenarios = []
            for time_value in x_data:
                mean_value_per_scenario_per_timestep = np.mean(
                    filtered_data_per_scenario.loc[filtered_data_per_scenario["time"] == time_value]["value"]
                )

                mean_values_aggregated_according_to_scenarios.append(mean_value_per_scenario_per_timestep)

            y_data = mean_values_aggregated_according_to_scenarios

            plt.plot(
                x_data_transformed,
                y_data,
                "-o",
                markersize=line_plot_marker_size,
                label=scenario,
            )

        y_tick_labels, unit, y_tick_locations = self.set_axis_scale(a_x, x_or_y="y", unit=filtered_data.unit.values[0])
        plt.yticks(
            ticks=y_tick_locations,
            labels=y_tick_labels,
            fontsize=self.hisim_chartbase.fontsize_ticks,
        )

        plt.ylabel(
            ylabel=f"{unit}",
            fontsize=self.hisim_chartbase.fontsize_label,
        )
        plt.xlabel(
            xlabel=year,
            fontsize=self.hisim_chartbase.fontsize_label,
        )
        plt.title(label=title, fontsize=self.hisim_chartbase.fontsize_title)
        plt.tick_params(labelsize=self.hisim_chartbase.fontsize_ticks)
        a_x.tick_params(axis="x", labelrotation=45)
        if self.show_plot_legend:
            plt.legend(bbox_to_anchor=(1, 1), loc="upper left")

        fig.savefig(os.path.join(self.plot_path_complete, "line_plot.png"), bbox_inches="tight")
        plt.close()

    def make_bar_plot_for_pandas_dataframe(
        self,
        filtered_data: pd.DataFrame,
        title: str,
        unit: str,
        alternative_bar_labels: Optional[List[str]] = None,
    ) -> None:
        """Make bar plot."""
        log.information("Make bar plot.")

        fig, a_x = plt.subplots(figsize=self.hisim_chartbase.figsize, dpi=self.hisim_chartbase.dpi)

        y_data = []
        bar_labels = []

        for scenario in list(OrderedSet(list(filtered_data.scenario))):
            filtered_data_per_scenario = filtered_data.loc[filtered_data["scenario"] == scenario]

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
            a_x,
            x_or_y="y",
            unit=unit,
        )
        plt.yticks(
            ticks=y_tick_locations,
            labels=y_tick_labels,
            fontsize=self.hisim_chartbase.fontsize_ticks,
        )
        plt.ylabel(
            ylabel=f"{unit}",
            fontsize=self.hisim_chartbase.fontsize_label,
        )
        plt.xlabel(
            xlabel=filtered_data.year.values[0],
            fontsize=self.hisim_chartbase.fontsize_label,
        )
        plt.title(label=title, fontsize=self.hisim_chartbase.fontsize_title)
        plt.tick_params(labelsize=self.hisim_chartbase.fontsize_ticks)

        if self.show_plot_legend:
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

        fig, a_x = plt.subplots(figsize=self.hisim_chartbase.figsize, dpi=self.hisim_chartbase.dpi)
        if scenario_set is None:
            scenario_set = list(OrderedSet(filtered_data.scenario))

        sns.boxplot(data=filtered_data, x="scenario", y="value")

        y_tick_labels, unit, y_tick_locations = self.set_axis_scale(a_x, x_or_y="y", unit=filtered_data.unit.values[0])
        plt.yticks(
            ticks=y_tick_locations,
            labels=y_tick_labels,
            fontsize=self.hisim_chartbase.fontsize_ticks,
        )
        plt.ylabel(
            ylabel=f"{unit}",
            fontsize=self.hisim_chartbase.fontsize_label,
        )
        try:
            # this works for yearly data
            plt.xlabel(
                xlabel=filtered_data.year.values[0],
                fontsize=self.hisim_chartbase.fontsize_label,
            )
        except Exception:
            # take year from time colum
            year = filtered_data.time.values[0].split("-")[0]
            plt.xlabel(
                xlabel=year,
                fontsize=self.hisim_chartbase.fontsize_label,
            )
        plt.title(label=title, fontsize=self.hisim_chartbase.fontsize_title)
        plt.tick_params(labelsize=self.hisim_chartbase.fontsize_ticks)
        a_x.xaxis.set_tick_params(labelbottom=False)
        a_x.set_xticks([])
        if self.show_plot_legend:
            plt.legend(scenario_set, bbox_to_anchor=(1, 1), loc="upper left")

        fig.savefig(os.path.join(self.plot_path_complete, "box_plot.png"), bbox_inches="tight")
        plt.close()

    def make_histogram_plot_for_pandas_dataframe(
        self,
        filtered_data: pd.DataFrame,
        title: str,
        unit: str,
        scenario_set: Optional[List[str]] = None,
    ) -> None:
        """Make histogram plot."""
        log.information("Make histogram plot.")

        fig, a_x = plt.subplots(  # pylint: disable=unused-variable
            figsize=self.hisim_chartbase.figsize, dpi=self.hisim_chartbase.dpi
        )
        if scenario_set is None:
            scenario_set = list(OrderedSet(filtered_data.scenario))

        plt.hist(x=np.array(filtered_data.value.values), bins="auto")

        plt.ylabel(
            ylabel="Count",
            fontsize=self.hisim_chartbase.fontsize_label,
        )
        plt.xlabel(
            xlabel=f"{unit}",
            fontsize=self.hisim_chartbase.fontsize_label,
        )
        plt.title(label=title, fontsize=self.hisim_chartbase.fontsize_title)
        plt.tick_params(labelsize=self.hisim_chartbase.fontsize_ticks)

        fig.savefig(
            os.path.join(self.plot_path_complete, "histogram_plot.png"),
            bbox_inches="tight",
        )
        plt.close()

    def make_scatter_plot_for_pandas_dataframe(
        self,
        full_pandas_dataframe: pd.DataFrame,
        filtered_data: pd.DataFrame,
        y_data_variable: str,
        x_data_variable: str = "Ratio between energy production and consumption",
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
        data_length = len(x_data_mean_value_list_for_all_scenarios)
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

        # make scatter plot
        plt.scatter(
            x_data_mean_value_list_for_all_scenarios,
            y_data_mean_value_list_for_all_scenarios,
            s=scatter_plot_marker_size,
        )

        y_tick_labels, y_unit, y_tick_locations = self.set_axis_scale(a_x, x_or_y="y", unit=y_data_unit)
        plt.yticks(
            ticks=y_tick_locations,
            labels=y_tick_labels,
            fontsize=self.hisim_chartbase.fontsize_ticks,
        )

        plt.ylabel(
            ylabel=f"{y_data_variable} [{y_unit}]",
            fontsize=self.hisim_chartbase.fontsize_label,
        )
        plt.xlabel(
            xlabel=f"{x_data_variable} [{x_data_unit}]",
            fontsize=self.hisim_chartbase.fontsize_label,
        )

        plt.tick_params(labelsize=self.hisim_chartbase.fontsize_ticks)
        a_x.tick_params(axis="x", labelrotation=45)
        if self.show_plot_legend:
            plt.legend(bbox_to_anchor=(1, 1), loc="upper left")

        fig.savefig(
            os.path.join(self.plot_path_complete, "scatter_plot.png"),
            bbox_inches="tight",
        )
        plt.close()

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

        if unit not in ["-", "%"]:
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
