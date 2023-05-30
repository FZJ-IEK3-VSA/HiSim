"""Data Processing and Plotting for Scenario Comparison with Pyam."""


import glob
import time
import datetime
import os
from typing import Dict, Any
import re
import pyam
import pandas as pd
import matplotlib.pyplot as plt
import plotly
from pyam_data_collection import PyamDataCollectorEnum
from html2image import Html2Image


class PyAmChartGenerator:

    """PyamChartGenerator class."""

    def __init__(self) -> None:
        """Initialize the class."""

        self.folder_path = "..\\..\\examples\\results_for_scenario_comparison\\data\\"
        self.result_folder = (
            "..\\..\\examples\\results_for_scenario_comparison\\results\\"
        )

        dict_of_yearly_pyam_dataframes_for_different_simulation_durations = (
            self.get_dataframe_and_create_pyam_dataframe(
                folder_path=self.folder_path, kind_of_data=PyamDataCollectorEnum.YEARLY
            )
        )
        dict_of_hourly_pyam_dataframes_for_different_simulation_durations = (
            self.get_dataframe_and_create_pyam_dataframe(
                folder_path=self.folder_path, kind_of_data=PyamDataCollectorEnum.HOURLY
            )
        )

        self.make_plots_with_specific_kind_of_data(
            kind_of_data=PyamDataCollectorEnum.YEARLY,
            dict_of_data=dict_of_yearly_pyam_dataframes_for_different_simulation_durations,
        )
        self.make_plots_with_specific_kind_of_data(
            kind_of_data=PyamDataCollectorEnum.HOURLY,
            dict_of_data=dict_of_hourly_pyam_dataframes_for_different_simulation_durations,
        )

    def get_dataframe_and_create_pyam_dataframe(
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

        dict_of_different_pyam_dataframes_of_different_simulation_parameters = {}
        # make dictionary with different simulation durations as keys
        for file in glob.glob(folder_path + f"**\\*{kind_of_data_set}*.csv"):
            file_df = pd.read_csv(filepath_or_buffer=file)
            pyam_dataframe = pyam.IamDataFrame(file_df)
            simulation_duration = re.findall(string=file, pattern=r"\d+")[0]
            dict_of_different_pyam_dataframes_of_different_simulation_parameters[
                f"{simulation_duration}"
            ] = pyam_dataframe

        return dict_of_different_pyam_dataframes_of_different_simulation_parameters

    def make_plots_with_specific_kind_of_data(
        self, kind_of_data: Any, dict_of_data: Dict[str, pyam.IamDataFrame]
    ) -> None:
        """Make plots for different kind of data."""

        for simulation_duration_key, pyam_dataframe in dict_of_data.items():

            self.sub_results_folder = (
                f"simulation_duration_of_{simulation_duration_key}_days\\"
            )
            self.sub_sub_results_folder = (
                f"pyam_results_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}"
            )

            if kind_of_data == PyamDataCollectorEnum.HOURLY:
                self.make_line_plot_for_pyam_dataframe(
                    pyam_dataframe=pyam_dataframe,
                )
                self.make_line_plot_with_filling_for_pyam_dataframe(
                    pyam_dataframe=pyam_dataframe,
                )
            elif kind_of_data == PyamDataCollectorEnum.YEARLY:
                self.make_bar_plot_for_pyam_dataframe(
                    pyam_dataframe=pyam_dataframe,
                )
                self.make_box_plot_for_pyam_dataframe(
                    pyam_dataframe=pyam_dataframe,
                )
                self.make_pie_plot_for_pyam_dataframe(
                    pyam_dataframe=pyam_dataframe,
                )
                self.make_scatter_plot_for_pyam_dataframe(
                    pyam_dataframe=pyam_dataframe,
                )
                # self.make_stack_plot_for_pyam_dataframe(pyam_dataframe=pyam_dataframe)
                self.make_sankey_plot_for_pyam_dataframe(
                    pyam_dataframe=pyam_dataframe,
                )
            else:
                raise ValueError(
                    "This kind of data was not found in the pyamdaacollectorenum class."
                )

    def make_line_plot_for_pyam_dataframe(
        self, pyam_dataframe: pyam.IamDataFrame
    ) -> None:
        """Make line plot."""
        print("Make line plot with hourly data.")

        data = pyam_dataframe
        # model = "HiSim_basic_household"
        # scenario="basic_household_explicit"
        filtered_data = data.filter(
            variable="Building|Heating|TheoreticalThermalBuildingDemand"
        )
        fig, a_x = plt.subplots(figsize=(12, 10))

        filtered_data.plot.line(
            ax=a_x,
            color="scenario",
            title="Building Theoretical Thermal Building Demand",
        )
        if os.path.exists(
            self.result_folder + self.sub_results_folder + self.sub_sub_results_folder
        ):
            fig.savefig(
                self.result_folder
                + self.sub_results_folder
                + self.sub_sub_results_folder
                + "\\line_plot.png"
            )
        else:
            os.makedirs(
                self.result_folder
                + self.sub_results_folder
                + self.sub_sub_results_folder
            )
            fig.savefig(
                self.result_folder
                + self.sub_results_folder
                + self.sub_sub_results_folder
                + "\\line_plot.png"
            )

    def make_line_plot_with_filling_for_pyam_dataframe(
        self, pyam_dataframe: pyam.IamDataFrame
    ) -> None:
        """Make line plot with filling."""
        print("Make line plot with filling.")

        data = pyam_dataframe
        model = "HiSim_basic_household"
        scenario = "basic_household_explicit"
        filtered_data = data.filter(
            model=model, scenario=scenario, variable="Building|Heating|*"
        )
        fig, a_x = plt.subplots(figsize=(12, 10))

        filtered_data.plot(
            ax=a_x,
            color="variable",
            title="Building Heating Outputs",
            fill_between=True,
        )
        if os.path.exists(
            self.result_folder + self.sub_results_folder + self.sub_sub_results_folder
        ):
            fig.savefig(
                self.result_folder
                + self.sub_results_folder
                + self.sub_sub_results_folder
                + "\\line_plot_with_filling.png"
            )
        else:
            os.makedirs(
                self.result_folder
                + self.sub_results_folder
                + self.sub_sub_results_folder
            )
            fig.savefig(
                self.result_folder
                + self.sub_results_folder
                + self.sub_sub_results_folder
                + "\\line_plot_with_filling.png"
            )

    def make_bar_plot_for_pyam_dataframe(
        self, pyam_dataframe: pyam.IamDataFrame
    ) -> None:
        """Make bar plot."""
        print("Make bar plot.")

        data = pyam_dataframe
        model = "HiSim_basic_household"
        scenario = "basic_household_explicit"
        filtered_data = data.filter(
            model=model, scenario=scenario, variable="Building|Heating|*"
        )
        fig, a_x = plt.subplots(figsize=(12, 10))

        filtered_data.plot.bar(ax=a_x, stacked=True)
        plt.legend(loc=1)
        plt.tight_layout()
        a_x.tick_params(axis="x", rotation=45)
        if os.path.exists(
            self.result_folder + self.sub_results_folder + self.sub_sub_results_folder
        ):
            fig.savefig(
                self.result_folder
                + self.sub_results_folder
                + self.sub_sub_results_folder
                + "\\bar_plot.png"
            )
        else:
            os.makedirs(
                self.result_folder
                + self.sub_results_folder
                + self.sub_sub_results_folder
            )
            fig.savefig(
                self.result_folder
                + self.sub_results_folder
                + self.sub_sub_results_folder
                + "\\bar_plot.png"
            )

    def make_stack_plot_for_pyam_dataframe(
        self, pyam_dataframe: pyam.IamDataFrame
    ) -> None:
        """Make stack plot."""

        data = pyam_dataframe
        model = "HiSim_basic_household"
        scenario = "basic_household_explicit"
        filtered_data = data.filter(
            model=model,
            scenario=scenario,
            variable="Building|Heating|*",
            region="Aachen",
        )
        print(filtered_data.timeseries())
        fig = plt.subplots(figsize=(12, 10))

        filtered_data.plot.stack(titel=scenario)
        fig.subplots_adjust(right=0.55)
        if os.path.exists(
            self.result_folder + self.sub_results_folder + self.sub_sub_results_folder
        ):
            fig.savefig(
                self.result_folder
                + self.sub_results_folder
                + self.sub_sub_results_folder
                + "\\stack_plot.png"
            )
        else:
            os.makedirs(
                self.result_folder
                + self.sub_results_folder
                + self.sub_sub_results_folder
            )
            fig.savefig(
                self.result_folder
                + self.sub_results_folder
                + self.sub_sub_results_folder
                + "\\stack_plot.png"
            )

    def make_box_plot_for_pyam_dataframe(
        self, pyam_dataframe: pyam.IamDataFrame
    ) -> None:
        """Make box plot."""
        print("Make box plot.")

        data = pyam_dataframe
        # model = "HiSim_basic_household"
        # scenario="basic_household_explicit"
        filtered_data = data.filter(
            variable="Building|Heating|TheoreticalThermalBuildingDemand"
        )
        fig, a_x = plt.subplots(figsize=(12, 10))

        filtered_data.plot.box(
            ax=a_x,
            by="variable",
            legend=True,
            x="year",
            title="Theoretical Building Demand for all scenarios",
        )
        plt.tight_layout()
        plt.legend(loc=1)
        if os.path.exists(
            self.result_folder + self.sub_results_folder + self.sub_sub_results_folder
        ):
            fig.savefig(
                self.result_folder
                + self.sub_results_folder
                + self.sub_sub_results_folder
                + "\\box_plot.png"
            )
        else:
            os.makedirs(
                self.result_folder
                + self.sub_results_folder
                + self.sub_sub_results_folder
            )
            fig.savefig(
                self.result_folder
                + self.sub_results_folder
                + self.sub_sub_results_folder
                + "\\box_plot.png"
            )

    def make_pie_plot_for_pyam_dataframe(
        self, pyam_dataframe: pyam.IamDataFrame
    ) -> None:
        """Make pie plot."""
        print("Make pie plot.")

        data = pyam_dataframe
        model = "HiSim_basic_household"
        scenario = "basic_household_explicit"
        filtered_data = data.filter(
            model=model, scenario=scenario, variable="Building|Heating|*"
        )
        fig, a_x = plt.subplots(figsize=(12, 10))

        filtered_data.plot.pie(
            ax=a_x,
            value="value",
            category="variable",
            title="Building Heating Outputs",
        )
        fig.subplots_adjust(right=0.75, left=0.3)
        if os.path.exists(
            self.result_folder + self.sub_results_folder + self.sub_sub_results_folder
        ):
            fig.savefig(
                self.result_folder
                + self.sub_results_folder
                + self.sub_sub_results_folder
                + "\\pie_plot.png"
            )
        else:
            os.makedirs(
                self.result_folder
                + self.sub_results_folder
                + self.sub_sub_results_folder
            )
            fig.savefig(
                self.result_folder
                + self.sub_results_folder
                + self.sub_sub_results_folder
                + "\\pie_plot.png"
            )

    def make_scatter_plot_for_pyam_dataframe(
        self, pyam_dataframe: pyam.IamDataFrame
    ) -> None:
        """Make scatter plot."""
        print("Make scatter plot.")

        data = pyam_dataframe
        filtered_data = data
        fig, a_x = plt.subplots(figsize=(12, 10))
        filtered_data.plot.scatter(
            ax=a_x,
            x="Weather|Temperature|DailyAverageOutsideTemperatures",
            y="Building|Temperature|TemperatureIndoorAir",
        )
        if os.path.exists(
            self.result_folder + self.sub_results_folder + self.sub_sub_results_folder
        ):
            fig.savefig(
                self.result_folder
                + self.sub_results_folder
                + self.sub_sub_results_folder
                + "\\scatter_plot.png"
            )
        else:
            os.makedirs(
                self.result_folder
                + self.sub_results_folder
                + self.sub_sub_results_folder
            )
            fig.savefig(
                self.result_folder
                + self.sub_results_folder
                + self.sub_sub_results_folder
                + "\\scatter_plot.png"
            )

    def make_sankey_plot_for_pyam_dataframe(
        self, pyam_dataframe: pyam.IamDataFrame
    ) -> None:
        """Make sankey plot."""
        print("Make sankey plot.")

        data = pyam_dataframe

        model = "HiSim_basic_household"
        scenario = "basic_household_explicit"
        region = "Aachen"
        unit = "W"
        filtered_data = data.filter(
            model=model, scenario=scenario, region=region, unit=unit, year=2021
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
            filename=self.result_folder
            + self.sub_results_folder
            + self.sub_sub_results_folder
            + "\\sankey_plot.html",
            auto_open=False,
        )

        # convert html file to png
        hti = Html2Image()
        with open(
            self.result_folder
            + self.sub_results_folder
            + self.sub_sub_results_folder
            + "\\sankey_plot.html",
            encoding="utf8",
        ) as file:
            hti.screenshot(
                file.read(),
                save_as="sankey_plot.png",
            )

        # change directory of sankey output file
        os.rename(
            "sankey_plot.png",
            self.result_folder
            + self.sub_results_folder
            + self.sub_sub_results_folder
            + "\\sankey_plot.png",
        )


def main():
    """Main function to execute the pyam data processing."""
    PyAmChartGenerator()


if __name__ == "__main__":
    start_time = time.time()
    main()
    print(f"---{time.time() - start_time} seconds ___")
