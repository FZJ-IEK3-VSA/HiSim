"""Data Collection for Scenario Comparison with Pyam."""
# clean
import glob
import time
import os
from typing import Dict, Any
import json
import enum
import pyam
import pandas as pd


class PyamDataCollector:

    """PyamDataCollector class which collects and concatenate the pyam data from the examples/results."""

    def __init__(self) -> None:
        """Initialize the class."""

        self.result_folder = "..\\..\\examples\\results\\"
        self.pyam_data_folder = (
            "..\\..\\examples\\results_for_scenario_comparison\\data\\"
        )
        dict_of_yearly_csv_data, dict_of_hourly_csv_data = self.import_data_from_file(
            folder_path=self.result_folder
        )
        self.read_csv_and_generate_pyam_dataframe(
            dict_of_csv_to_read=dict_of_yearly_csv_data,
            kind_of_data=PyamDataCollectorEnum.YEARLY,
        )
        self.read_csv_and_generate_pyam_dataframe(
            dict_of_csv_to_read=dict_of_hourly_csv_data,
            kind_of_data=PyamDataCollectorEnum.HOURLY,
        )

    def import_data_from_file(self, folder_path: str) -> tuple[Dict, Dict]:
        """Import data from result files."""
        # get csv files
        dict_of_yearly_csv_data_for_different_simulation_duration: Dict = {}
        dict_of_hourly_csv_data_for_different_simulation_duration: Dict = {}
        yearly_data = []
        hourly_data = []
        simulation_durations = []

        for folder in glob.glob(folder_path + "**\\pyam_data\\"):

            for file in os.listdir(folder):
                # get yearly data
                if "yearly_results" in file and file.endswith(".csv"):

                    yearly_data.append(folder + file)
                if "hourly_results" in file and file.endswith(".csv"):
                    hourly_data.append(folder + file)

                # get simulation durations
                if ".json" in file:
                    with open(folder + "\\" + file, "r", encoding="utf-8") as openfile:
                        json_file = json.load(openfile)
                        simulation_duration = json_file["duration in days"]
                        simulation_durations.append(simulation_duration)

        # get a list of all simulation durations that exist and use them as key for the data dictionaries
        simulation_durations = list(set(simulation_durations))
        for simulation_duration in simulation_durations:
            dict_of_yearly_csv_data_for_different_simulation_duration[
                f"{simulation_duration}"
            ] = []
            dict_of_hourly_csv_data_for_different_simulation_duration[
                f"{simulation_duration}"
            ] = []

        yearly_data_csv_data = []
        yearly_data_set = []
        # prevent that csv data exists more than 1 time in the list
        for path in yearly_data:

            if path.split("\\")[-1] not in yearly_data_csv_data:
                yearly_data_csv_data.append(path.split("\\")[-1])
                yearly_data_set.append(path)

        hourly_data_csv_data = []
        hourly_data_set = []
        for path in hourly_data:

            if path.split("\\")[-1] not in hourly_data_csv_data:
                hourly_data_csv_data.append(path.split("\\")[-1])
                hourly_data_set.append(path)

        # order files according to their simualtion durations
        for file in yearly_data_set:

            parent_folder = os.path.abspath(os.path.join(file, os.pardir))
            for file1 in os.listdir(parent_folder):
                if ".json" in file1:
                    with open(parent_folder + "\\" + file1, "r", encoding="utf-8") as openfile:
                        json_file = json.load(openfile)
                        simulation_duration = json_file["duration in days"]
                        if simulation_duration in simulation_durations:
                            dict_of_yearly_csv_data_for_different_simulation_duration[
                                f"{simulation_duration}"
                            ].append(file)

        for file in hourly_data_set:
            parent_folder = os.path.abspath(os.path.join(file, os.pardir))
            for file1 in os.listdir(parent_folder):
                if ".json" in file1:
                    with open(parent_folder + "\\" + file1, "r", encoding="utf-8") as openfile:
                        json_file = json.load(openfile)
                        simulation_duration = json_file["duration in days"]
                        if simulation_duration in simulation_durations:
                            dict_of_hourly_csv_data_for_different_simulation_duration[
                                f"{simulation_duration}"
                            ].append(file)

        return (
            dict_of_yearly_csv_data_for_different_simulation_duration,
            dict_of_hourly_csv_data_for_different_simulation_duration,
        )

    def read_csv_and_generate_pyam_dataframe(
        self, dict_of_csv_to_read: Dict[str, list[str]], kind_of_data: Any
    ) -> None:
        """Read the csv files and generate the pyam dataframe for different simulation durations."""

        for simulation_duration_key, csv_data_list in dict_of_csv_to_read.items():
            appended_dataframe = pd.DataFrame()
            for csv_file in csv_data_list:
                dataframe = pd.read_csv(csv_file)
                appended_dataframe = pd.concat([appended_dataframe, dataframe])

            df_pyam_for_one_simulation_duration = pyam.IamDataFrame(appended_dataframe)

            if kind_of_data == PyamDataCollectorEnum.HOURLY:
                kind_of_data_set = "hourly"
            elif kind_of_data == PyamDataCollectorEnum.YEARLY:
                kind_of_data_set = "yearly"
            else:
                raise ValueError(
                    "This kind of data was not found in the pyamdatacollectorenum class."
                )

            if os.path.exists(
                self.pyam_data_folder
                + f"simulation_duration_of_{simulation_duration_key}_days"
            ):
                print("Saving pyam dataframe in Hisim/examples/results_for_scenario_comparison/data folder")
                df_pyam_for_one_simulation_duration.to_csv(
                    self.pyam_data_folder
                    + f"simulation_duration_of_{simulation_duration_key}_days"
                    + f"\\pyam_dataframe_for_{simulation_duration_key}_days_"
                    + kind_of_data_set
                    + "_data.csv"
                )
            else:
                os.makedirs(
                    self.pyam_data_folder
                    + f"simulation_duration_of_{simulation_duration_key}_days"
                )
                print("Saving pyam dataframe in Hisim/examples/results_for_scenario_comparison/data folder")
                df_pyam_for_one_simulation_duration.to_csv(
                    self.pyam_data_folder
                    + f"simulation_duration_of_{simulation_duration_key}_days"
                    + f"\\pyam_dataframe_for_{simulation_duration_key}_days_"
                    + kind_of_data_set
                    + "_data.csv"
                )


class PyamDataCollectorEnum(enum.Enum):

    """PyamDataCollectorEnum class.

    Here it is defined what kind of data you want to collect.
    """

    HOURLY = "hourly"
    YEARLY = "yearly"


def main():
    """Main function to execute the pyam data collection."""
    PyamDataCollector()


if __name__ == "__main__":
    start_time = time.time()
    main()
    print(f"---{time.time() - start_time} seconds ___")
