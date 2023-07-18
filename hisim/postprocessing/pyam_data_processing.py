"""Data Processing and Plotting for Scenario Comparison with Pyam."""


import glob
import time
import datetime
import os
from typing import Dict, Any, Tuple, Optional
import re
import numpy as np
import pyam
import pandas as pd
import matplotlib.pyplot as plt
import plotly
from html2image import Html2Image
from hisim.postprocessing.pyam_data_collection import PyamDataCollectorEnum
from hisim.postprocessing.chartbase import ChartFontsAndSize
from hisim import log


class PyAmChartGenerator:

    """PyamChartGenerator class."""

    def __init__(self, simulation_duration_to_check: str) -> None:
        """Initialize the class."""

        self.folder_path = os.path.join(
            os.pardir, os.pardir, "examples", "results_for_scenario_comparison", "data"
        )
        self.result_folder = os.path.join(
            os.pardir,
            os.pardir,
            "examples",
            "results_for_scenario_comparison",
            "results",
        )
        log.information(f"Data folder path: {self.folder_path}.")
        self.hisim_chartbase = ChartFontsAndSize()
        self.hisim_chartbase.figsize = (10, 8)

        dict_of_yearly_pyam_dataframes_for_different_simulation_durations = self.get_dataframe_and_create_pyam_dataframe_for_all_data(
            folder_path=self.folder_path, kind_of_data=PyamDataCollectorEnum.YEARLY
        )
        dict_of_hourly_pyam_dataframes_for_different_simulation_durations = self.get_dataframe_and_create_pyam_dataframe_for_all_data(
            folder_path=self.folder_path, kind_of_data=PyamDataCollectorEnum.HOURLY
        )

        self.make_plots_with_specific_kind_of_data(
            kind_of_data=PyamDataCollectorEnum.YEARLY,
            dict_of_data=dict_of_yearly_pyam_dataframes_for_different_simulation_durations,
            simulation_duration_key=simulation_duration_to_check,
        )
        self.make_plots_with_specific_kind_of_data(
            kind_of_data=PyamDataCollectorEnum.HOURLY,
            dict_of_data=dict_of_hourly_pyam_dataframes_for_different_simulation_durations,
            simulation_duration_key=simulation_duration_to_check,
        )

    def get_dataframe_and_create_pyam_dataframe_for_all_data(
        self, folder_path: str, kind_of_data: Any
    ) -> Dict:
        """Get csv data and create pyam dataframes."""

        if kind_of_data == PyamDataCollectorEnum.YEARLY:
            kind_of_data_set = "yearly"
        elif kind_of_data == PyamDataCollectorEnum.HOURLY:
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

            file_df["Scenario"] = file_df["Scenario"].transform(lambda x: str(x))
            pyam_dataframe = pyam.IamDataFrame(file_df)

            simulation_duration = re.findall(string=file, pattern=r"\d+")[0]
            dict_of_different_pyam_dataframes_of_different_simulation_parameters[
                f"{simulation_duration}"
            ] = pyam_dataframe

        return dict_of_different_pyam_dataframes_of_different_simulation_parameters

    def make_plots_with_specific_kind_of_data(
        self,
        kind_of_data: Any,
        dict_of_data: Dict[str, pyam.IamDataFrame],
        simulation_duration_key: str,
    ) -> None:
        """Make plots for different kind of data."""
        log.information(f"Simulation duration: {simulation_duration_key} days.")
        pyam_dataframe = dict_of_data[simulation_duration_key]
        if pyam_dataframe.empty:
            raise ValueError("Pyam dataframe is empty.")

        log.information("Pyam dataframe columns " + str(pyam_dataframe.dimensions))
        log.information("Pyam dataframe scenarios " + str(pyam_dataframe.scenario))
        # log.information("Pyam dataframe models " + str(pyam_dataframe.model))
        # log.information("Pyam dataframe variables " + str(pyam_dataframe.variable))
        # log.information("Pyam dataframe region " + str(pyam_dataframe.region))
        # log.information("Pyam dataframe time domain " + str(pyam_dataframe.time_domain))

        sub_results_folder = f"simulation_duration_of_{simulation_duration_key}_days"
        sub_sub_results_folder = (
            f"pyam_results_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}"
        )

        self.path_for_plots = os.path.join(
            self.result_folder, sub_results_folder, sub_sub_results_folder
        )
        if kind_of_data == PyamDataCollectorEnum.YEARLY:
            log.information(
                f"Yearly Data Processing for Simulation Duration of {simulation_duration_key} Days:"
            )
            self.make_bar_plot_for_pyam_dataframe(
                pyam_dataframe=pyam_dataframe,
                filter_model=None,
                filter_scenario="365d_60s_2227458627882477145",
                filter_variables="Building1|Heating|*",
                filter_region=None,
                filter_unit=None,
                filter_year=None,
                title="Heating",
            )

            self.make_box_plot_for_pyam_dataframe(
                pyam_dataframe=pyam_dataframe,
                filter_variables="Building1|Heating|TheoreticalThermalBuildingDemand",
                title="Building Theoretical Thermal Building Demand of All Scenarios",
                filter_model=None,
                filter_scenario=None,
                filter_region=None,
                filter_unit=None,
                filter_year=None,
            )
            self.make_pie_plot_for_pyam_dataframe(
                pyam_dataframe=pyam_dataframe,
                filter_model=None,
                filter_scenario=None,
                filter_variables="EMS|ElectricityToOrFromGrid|-",
                title="Electricity to or from Grid",
                filter_region=None,
                filter_unit=None,
                filter_year=None,
            )
            self.make_scatter_plot_for_pyam_dataframe(
                pyam_dataframe=pyam_dataframe,
                filter_model=None,
                filter_scenario=None,
                filter_variables=None,
                title="Temperatures",
                filter_region=None,
                filter_unit=None,
                filter_year=None,
                x_data_variable="Building1|Temperature|TemperatureIndoorAir",
                y_data_variable="Building1|Temperature|TemperatureMeanThermalMass",
            )

            # self.make_stack_plot_for_pyam_dataframe(pyam_dataframe=pyam_dataframe, filter_model=None, filter_scenario=None, filter_variables="EMS|ElectricityToOrFromGrid|-", title="Electricity to or from Grid", filter_region=None, filter_unit=None, filter_year=None)
            # self.make_sankey_plot_for_pyam_dataframe(
            #     pyam_dataframe=pyam_dataframe, filter_model=None, filter_scenario="2227458627882477145", filter_variables="*|*|ElectricityOutput", filter_region=None, filter_unit=None, filter_year=None
            # )
        elif kind_of_data == PyamDataCollectorEnum.HOURLY:
            log.information(
                f"Hourly Data Processing for Simulation Duration of {simulation_duration_key} Days:"
            )
            self.make_line_plot_for_pyam_dataframe(
                pyam_dataframe=pyam_dataframe,
                filter_variables="Building1|Heating|TheoreticalThermalBuildingDemand",
                title="Building Theoretical Thermal Building Demand",
                filter_model=None,
                filter_scenario=None,
                filter_region=None,
                filter_unit=None,
                filter_year=None,
            )
            self.make_line_plot_with_filling_for_pyam_dataframe(
                pyam_dataframe=pyam_dataframe,
                filter_model=None,
                filter_scenario=None,
                filter_variables="EMS|ElectricityToOrFromGrid|-",
                title="Electricity to or from Grid",
                filter_region=None,
                filter_unit=None,
                filter_year=None,
            )
        else:
            raise ValueError(
                "This kind of data was not found in the pyamdatacollectorenum class."
            )

    def make_line_plot_for_pyam_dataframe(
        self,
        pyam_dataframe: pyam.IamDataFrame,
        filter_model: Optional[str],
        filter_scenario: Optional[str],
        filter_variables: Optional[str],
        filter_region: Optional[str],
        filter_unit: Optional[str],
        filter_year: Optional[str],
        title: str,
    ) -> None:
        """Make line plot."""
        log.information("Make line plot with hourly data.")

        filtered_data = self.filter_pyam_dataframe(
            pyam_dataframe=pyam_dataframe,
            filter_model=filter_model,
            filter_scenario=filter_scenario,
            filter_region=filter_region,
            filter_variables=filter_variables,
            filter_unit=filter_unit,
            filter_year=filter_year,
        )

        comparion_mode = self.decide_for_scenario_or_variable_comparison(
            filtered_data=filtered_data
        )

        fig, a_x = plt.subplots(
            figsize=self.hisim_chartbase.figsize, dpi=self.hisim_chartbase.dpi
        )
        filtered_data.plot.line(
            ax=a_x, color=comparion_mode, title=title,
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

        if os.path.exists(self.path_for_plots) is False:
            os.makedirs(self.path_for_plots)
        fig.savefig(os.path.join(self.path_for_plots, "line_plot.png"))

    def make_line_plot_with_filling_for_pyam_dataframe(
        self,
        pyam_dataframe: pyam.IamDataFrame,
        filter_model: Optional[str],
        filter_scenario: Optional[str],
        filter_variables: Optional[str],
        filter_region: Optional[str],
        filter_unit: Optional[str],
        filter_year: Optional[str],
        title: str,
    ) -> None:
        """Make line plot with filling."""
        log.information("Make line plot with filling.")

        filtered_data = self.filter_pyam_dataframe(
            pyam_dataframe=pyam_dataframe,
            filter_model=filter_model,
            filter_scenario=filter_scenario,
            filter_region=filter_region,
            filter_variables=filter_variables,
            filter_unit=filter_unit,
            filter_year=filter_year,
        )
        comparion_mode = self.decide_for_scenario_or_variable_comparison(
            filtered_data=filtered_data
        )

        fig, a_x = plt.subplots(
            figsize=self.hisim_chartbase.figsize, dpi=self.hisim_chartbase.dpi
        )

        filtered_data.plot(
            ax=a_x, color=comparion_mode, title=title, fill_between=True,
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

        if os.path.exists(self.path_for_plots) is False:
            os.makedirs(self.path_for_plots)
        fig.savefig(os.path.join(self.path_for_plots, "line_plot_with_filling.png"))

    def make_bar_plot_for_pyam_dataframe(
        self,
        pyam_dataframe: pyam.IamDataFrame,
        filter_model: Optional[str],
        filter_scenario: Optional[str],
        filter_variables: Optional[str],
        filter_region: Optional[str],
        filter_unit: Optional[str],
        filter_year: Optional[str],
        title: str,
    ) -> None:
        """Make bar plot."""
        log.information("Make bar plot.")

        filtered_data = self.filter_pyam_dataframe(
            pyam_dataframe=pyam_dataframe,
            filter_model=filter_model,
            filter_scenario=filter_scenario,
            filter_region=filter_region,
            filter_variables=filter_variables,
            filter_unit=filter_unit,
            filter_year=filter_year,
        )
        comparion_mode = self.decide_for_scenario_or_variable_comparison(
            filtered_data=filtered_data
        )

        fig, a_x = plt.subplots(
            figsize=self.hisim_chartbase.figsize, dpi=self.hisim_chartbase.dpi
        )
        filtered_data.plot.bar(ax=a_x, stacked=True, bars=comparion_mode)

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
        if os.path.exists(self.path_for_plots) is False:
            os.makedirs(self.path_for_plots)
        fig.savefig(os.path.join(self.path_for_plots, "bar_plot.png"))

    def make_box_plot_for_pyam_dataframe(
        self,
        pyam_dataframe: pyam.IamDataFrame,
        filter_model: Optional[str],
        filter_scenario: Optional[str],
        filter_variables: Optional[str],
        filter_region: Optional[str],
        filter_unit: Optional[str],
        filter_year: Optional[str],
        title: str,
    ) -> None:
        """Make box plot."""
        log.information("Make box plot.")

        filtered_data = self.filter_pyam_dataframe(
            pyam_dataframe=pyam_dataframe,
            filter_model=filter_model,
            filter_scenario=filter_scenario,
            filter_region=filter_region,
            filter_variables=filter_variables,
            filter_unit=filter_unit,
            filter_year=filter_year,
        )
        # comparion_mode = self.decide_for_scenario_or_variable_comparison(filtered_data=filtered_data)
        # print("comparison mode", comparion_mode)
        fig, a_x = plt.subplots(
            figsize=self.hisim_chartbase.figsize, dpi=self.hisim_chartbase.dpi
        )

        filtered_data.plot.box(
            ax=a_x, by="variable", x="year", title=title, legend=True,
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
        if os.path.exists(self.path_for_plots) is False:
            os.makedirs(self.path_for_plots)
        fig.savefig(os.path.join(self.path_for_plots, "box_plot.png"))

    def make_pie_plot_for_pyam_dataframe(
        self,
        pyam_dataframe: pyam.IamDataFrame,
        filter_model: Optional[str],
        filter_scenario: Optional[str],
        filter_variables: Optional[str],
        filter_region: Optional[str],
        filter_unit: Optional[str],
        filter_year: Optional[str],
        title: Optional[str],
    ) -> None:
        """Make pie plot."""
        log.information("Make pie plot.")

        filtered_data = self.filter_pyam_dataframe(
            pyam_dataframe=pyam_dataframe,
            filter_model=filter_model,
            filter_scenario=filter_scenario,
            filter_region=filter_region,
            filter_variables=filter_variables,
            filter_unit=filter_unit,
            filter_year=filter_year,
        )
        comparion_mode = self.decide_for_scenario_or_variable_comparison(
            filtered_data=filtered_data
        )
        fig, a_x = plt.subplots(
            figsize=self.hisim_chartbase.figsize, dpi=self.hisim_chartbase.dpi
        )
        filtered_data.plot.pie(
            ax=a_x, value="value", category=comparion_mode, title=title,
        )

        plt.title(label=title, fontsize=self.hisim_chartbase.fontsize_title)
        fig.subplots_adjust(right=0.75, left=0.3)
        if os.path.exists(self.path_for_plots) is False:
            os.makedirs(self.path_for_plots)
        fig.savefig(os.path.join(self.path_for_plots, "pie_plot.png"))

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

        y_tick_labels, scale, y_tick_locations = self.set_axis_scale(a_x, x_or_y="y")
        # x_tick_labels, scale_x, x_tick_locations = self.set_axis_scale(a_x, x_or_y="x")
        plt.yticks(
            ticks=y_tick_locations,
            labels=y_tick_labels,
            fontsize=self.hisim_chartbase.fontsize_ticks,
        )
        plt.ylabel(
            # ylabel=y_data.split(sep="|")[-1] + f" [{scale}°C]",
            ylabel=y_data.rsplit("|", maxsplit=1)[-1] + f" [{scale}°C]",
            fontsize=self.hisim_chartbase.fontsize_label,
        )
        plt.xlabel(
            # xlabel=x_data.split(sep="|")[-1] + f" [{scale}°C]",
            xlabel=x_data.rsplit("|", maxsplit=1)[-1] + f" [{scale}°C]",
            fontsize=self.hisim_chartbase.fontsize_label,
        )
        plt.title(label=title, fontsize=self.hisim_chartbase.fontsize_title)
        plt.tick_params(labelsize=self.hisim_chartbase.fontsize_ticks)

        if os.path.exists(self.path_for_plots) is False:
            os.makedirs(self.path_for_plots)
        fig.savefig(os.path.join(self.path_for_plots, "scatter_plot.png"))

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
            filename=os.path.join(self.path_for_plots, "sankey_plot.html"),
            auto_open=False,
        )

        # convert html file to png
        hti = Html2Image()
        with open(
            os.path.join(self.path_for_plots, "sankey_plot.html"), encoding="utf8",
        ) as file:
            hti.screenshot(
                file.read(), save_as="sankey_plot.png",
            )

        # change directory of sankey output file
        try:
            os.rename(
                "sankey_plot.png", os.path.join(self.path_for_plots, "sankey_plot.png"),
            )
        except Exception as exc:
            raise Exception("Cannot save current sankey. Try again.") from exc

    def make_stack_plot_for_pyam_dataframe(
        self,
        pyam_dataframe: pyam.IamDataFrame,
        filter_model: Optional[str],
        filter_scenario: Optional[str],
        filter_variables: Optional[str],
        filter_region: Optional[str],
        filter_unit: Optional[str],
        filter_year: Optional[str],
        title: str,
    ) -> None:
        """Make stack plot."""
        log.information("Make stack plot.")

        filtered_data = self.filter_pyam_dataframe(
            pyam_dataframe=pyam_dataframe,
            filter_model=filter_model,
            filter_scenario=filter_scenario,
            filter_region=filter_region,
            filter_variables=filter_variables,
            filter_unit=filter_unit,
            filter_year=filter_year,
        )

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
        if os.path.exists(self.path_for_plots) is False:
            os.makedirs(self.path_for_plots)
        fig.savefig(os.path.join(self.path_for_plots, "stack_plot.png"))

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


def main():
    """Main function to execute the pyam data processing."""
    PyAmChartGenerator(simulation_duration_to_check=str(365))


if __name__ == "__main__":
    start_time = time.time()
    main()
    print(f"---{time.time() - start_time} seconds ___")
