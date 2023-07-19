"""Data Collection for Scenario Comparison with Pyam."""
# clean
import glob
import time
import os
from typing import Dict, Any, Optional, List
import json
import enum
import pyam
import pandas as pd
from collections import defaultdict
from hisim import log


class PyamDataCollector:

    """PyamDataCollector class which collects and concatenate the pyam data from the examples/results."""

    def __init__(
        self, analysis_mode: Any, path_to_default_config: Optional[str] = None
    ) -> None:
        """Initialize the class."""
        self.result_folder = os.path.join(os.pardir, os.pardir, "examples", "results")
        self.pyam_data_folder = os.path.join(
            os.pardir, os.pardir, "examples", "results_for_scenario_comparison", "data"
        )
        log.information(f"Getting results from folder: {self.result_folder}")

        if analysis_mode == PyamDataAnalysisEnum.SENSITIVITY_ANALYSIS:

            default_config_dict = self.get_default_config(
                path_to_default_config=path_to_default_config
            )

            dict_with_csv_files_for_each_parameter = self.go_through_all_result_folders_and_store_file_paths_according_to_parameters(
                result_path=self.result_folder, default_config_dict=default_config_dict
            )
            print(dict_with_csv_files_for_each_parameter)
            # all_simulation_durations, all_hourly_csv_files, all_yearly_csv_files = self.import_data_from_file(
            #     folder_path=self.result_folder
            # )
            # dict_of_yearly_csv_data, dict_of_hourly_csv_data = self.make_dictionaries_with_simulation_duration_keys(simulation_durations=all_simulation_durations, hourly_data=all_hourly_csv_files, yearly_data=all_yearly_csv_files)
            # self.read_csv_and_generate_pyam_dataframe(
            #     dict_of_csv_to_read=dict_of_yearly_csv_data,
            #     kind_of_data=PyamDataCollectorEnum.YEARLY,
            # )
            # self.read_csv_and_generate_pyam_dataframe(
            #     dict_of_csv_to_read=dict_of_hourly_csv_data,
            #     kind_of_data=PyamDataCollectorEnum.HOURLY,
            # )

        elif analysis_mode == PyamDataAnalysisEnum.RANDOM:

            (
                all_simulation_durations,
                all_hourly_csv_files,
                all_yearly_csv_files,
            ) = self.import_data_from_file(folder_path=self.result_folder)
            (
                dict_of_yearly_csv_data,
                dict_of_hourly_csv_data,
            ) = self.make_dictionaries_with_simulation_duration_keys(
                simulation_durations=all_simulation_durations,
                hourly_data=all_hourly_csv_files,
                yearly_data=all_yearly_csv_files,
            )
            self.read_csv_and_generate_pyam_dataframe(
                dict_of_csv_to_read=dict_of_yearly_csv_data,
                kind_of_data=PyamDataCollectorEnum.YEARLY,
            )
            self.read_csv_and_generate_pyam_dataframe(
                dict_of_csv_to_read=dict_of_hourly_csv_data,
                kind_of_data=PyamDataCollectorEnum.HOURLY,
            )

        else:
            raise ValueError(
                "Analysis mode is not part of the PyamDataAnalysorEnum class."
            )

    def import_data_from_file(self, folder_path: str) -> tuple[List, List, List]:
        """Import data from result files."""
        log.information("Importing pyam_data from csv files.")
        # get csv files
        dict_of_yearly_csv_data_for_different_simulation_duration: Dict = {}
        dict_of_hourly_csv_data_for_different_simulation_duration: Dict = {}
        all_yearly_csv_files = []
        all_hourly_csv_files = []
        all_simulation_durations = []

        # choose which path to check
        path_to_check = os.path.join(folder_path, "**", "pyam_data")
        list_of_paths = list(glob.glob(path_to_check))
        # if in these paths no pyam data folder can be found check in subfolders for it
        if len(list_of_paths) == 0:
            path_to_check = os.path.join(folder_path, "**", "**", "pyam_data")  # type: ignore

        for folder in glob.glob(path_to_check):  # type: ignore

            for file in os.listdir(folder):  # type: ignore
                # get yearly data
                if "yearly_results" in file and file.endswith(".csv"):
                    all_yearly_csv_files.append(os.path.join(folder, file))  # type: ignore

                if "hourly_results" in file and file.endswith(".csv"):
                    all_hourly_csv_files.append(os.path.join(folder, file))  # type: ignore

                # get simulation durations
                if ".json" in file:
                    with open(os.path.join(folder, file), "r", encoding="utf-8") as openfile:  # type: ignore
                        json_file = json.load(openfile)
                        simulation_duration = json_file["duration in days"]
                        all_simulation_durations.append(simulation_duration)

        all_simulation_durations = list(set(all_simulation_durations))
        return all_simulation_durations, all_hourly_csv_files, all_yearly_csv_files

        def make_dictionaries_with_simulation_duration_keys(
            self,
            simulation_durations: List[int],
            hourly_data: List[str],
            yearly_data: List[str],
        ) -> tuple[Dict, Dict]:
            # get a list of all simulation durations that exist and use them as key for the data dictionaries

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
                print("path", path)
                if path.split("\\")[-1] not in hourly_data_csv_data:
                    hourly_data_csv_data.append(path.split("\\")[-1])
                    hourly_data_set.append(path)

            # order files according to their simualtion durations
            for file in yearly_data_set:

                parent_folder = os.path.abspath(os.path.join(file, os.pardir))  # type: ignore
                for file1 in os.listdir(parent_folder):
                    if ".json" in file1:
                        with open(
                            os.path.join(parent_folder, file1), "r", encoding="utf-8"
                        ) as openfile:
                            json_file = json.load(openfile)
                            simulation_duration = json_file["duration in days"]
                            if simulation_duration in simulation_durations:
                                dict_of_yearly_csv_data_for_different_simulation_duration[
                                    f"{simulation_duration}"
                                ].append(
                                    file
                                )

            for file in hourly_data_set:

                parent_folder = os.path.abspath(os.path.join(file, os.pardir))  # type: ignore
                for file1 in os.listdir(parent_folder):
                    if ".json" in file1:
                        with open(
                            os.path.join(parent_folder, file1), "r", encoding="utf-8"
                        ) as openfile:
                            json_file = json.load(openfile)
                            simulation_duration = json_file["duration in days"]
                            if simulation_duration in simulation_durations:
                                dict_of_hourly_csv_data_for_different_simulation_duration[
                                    f"{simulation_duration}"
                                ].append(
                                    file
                                )
            return (
                dict_of_yearly_csv_data_for_different_simulation_duration,
                dict_of_hourly_csv_data_for_different_simulation_duration,
            )

    def read_csv_and_generate_pyam_dataframe(
        self, dict_of_csv_to_read: Dict[str, list[str]], kind_of_data: Any
    ) -> None:
        """Read the csv files and generate the pyam dataframe for different simulation durations."""
        log.information(
            f"Read csv files and generate pyam dataframes for {kind_of_data}."
        )
        if bool(dict_of_csv_to_read) is False:
            raise ValueError("The passed dictionary is empty.")

        for simulation_duration_key, csv_data_list in dict_of_csv_to_read.items():
            appended_dataframe = pd.DataFrame()
            for csv_file in csv_data_list:
                dataframe = pd.read_csv(csv_file)
                appended_dataframe = pd.concat([appended_dataframe, dataframe])
            # transform scenario values to str values
            appended_dataframe["scenario"] = appended_dataframe["scenario"].transform(
                lambda x: str(x)
            )

            df_pyam_for_one_simulation_duration = pyam.IamDataFrame(appended_dataframe)
            # convert unit "Watt" to "Watthour" because it makes plots more readable later, conversion factor is 1/3600s
            df_pyam_for_one_simulation_duration = (
                df_pyam_for_one_simulation_duration.convert_unit(
                    current="W", to="Wh", factor=1 / 3600, inplace=False
                )
            )

            if kind_of_data == PyamDataCollectorEnum.HOURLY:
                kind_of_data_set = "hourly"
            elif kind_of_data == PyamDataCollectorEnum.YEARLY:
                kind_of_data_set = "yearly"
            else:
                raise ValueError(
                    "This kind of data was not found in the pyamdatacollectorenum class."
                )

            if (
                os.path.exists(
                    os.path.join(
                        self.pyam_data_folder,
                        f"simulation_duration_of_{simulation_duration_key}_days",
                    )
                )
                is False
            ):
                os.makedirs(
                    os.path.join(
                        self.pyam_data_folder,
                        f"simulation_duration_of_{simulation_duration_key}_days",
                    )
                )
            log.information(
                "Saving pyam dataframe in Hisim/examples/results_for_scenario_comparison/data folder"
            )
            df_pyam_for_one_simulation_duration.to_csv(
                os.path.join(
                    self.pyam_data_folder,
                    f"simulation_duration_of_{simulation_duration_key}_days",
                    f"pyam_dataframe_for_{simulation_duration_key}_days_{kind_of_data_set}_data.csv",
                )
            )

    def get_default_config(self, path_to_default_config: str):
        """Get default config."""

        if ".json" in path_to_default_config:
            with open(path_to_default_config, "r", encoding="utf-8") as openfile:  # type: ignore
                default_config_dict = json.load(openfile)
                print("default config ", default_config_dict)
        else:
            raise ValueError("The default config is not in .json format.")

        return default_config_dict

    def read_pyam_data_json_config_and_compare_to_default_config(
        self,
        default_config_dict: Dict[str, Any],
        path_to_pyam_data_folder: str,
        dict_with_csv_files_for_each_parameter: Dict[str, List[str]],
    ):
        """Read json config in pyam_data folder and compare with default config."""

        for file in os.listdir(path_to_pyam_data_folder):

            if ".json" in file:
                with open(os.path.join(path_to_pyam_data_folder, file), "r", encoding="utf-8") as openfile:  # type: ignore
                    config_dict = json.load(openfile)

        if len(set(default_config_dict).intersection(config_dict)) == 0:
            raise KeyError(
                "The config should contain the keys of the default config, otherwise their values cannot be compared."
            )
        # elif all(item in config_dict.items() for item in default_config_dict.items()):
        #     print("path ", path_to_pyam_data_folder)
        #     print(config_dict)
        del config_dict["name"]
        del default_config_dict["name"]
        if all(
            (k in config_dict and config_dict[k] == v)
            for k, v in default_config_dict.items()
        ):
            print(path_to_pyam_data_folder)
            print(config_dict)

        if "-5982918960945138887" in path_to_pyam_data_folder:
            print(path_to_pyam_data_folder)
            for k in default_config_dict:
                print(config_dict[k])

        for default_key, default_value in default_config_dict.items():

            if config_dict[default_key] != default_value:

                dict_with_csv_files_for_each_parameter[default_key] += [
                    path_to_pyam_data_folder
                ]

        return dict_with_csv_files_for_each_parameter

    def go_through_all_result_folders_and_store_file_paths_according_to_parameters(
        self, result_path: str, default_config_dict: Dict[str, Any]
    ):
        """Order result files according to different parameters."""
        # choose which path to check
        path_to_check = os.path.join(result_path, "**", "pyam_data")
        list_of_paths = list(glob.glob(path_to_check))
        # if in these paths no pyam data folder can be found check in subfolders for it
        if len(list_of_paths) == 0:
            path_to_check = os.path.join(result_path, "**", "**", "pyam_data")  # type: ignore
        dict_with_csv_files_for_each_parameter = defaultdict(list)
        for folder in glob.glob(path_to_check):  # type: ignore

            dict_with_csv_files_for_each_parameter = self.read_pyam_data_json_config_and_compare_to_default_config(
                default_config_dict=default_config_dict,
                path_to_pyam_data_folder=folder,
                dict_with_csv_files_for_each_parameter=dict_with_csv_files_for_each_parameter,
            )

        # remove name key from dict because this variable is different for all the configs
        del dict_with_csv_files_for_each_parameter["name"]
        return dict_with_csv_files_for_each_parameter


class PyamDataCollectorEnum(enum.Enum):

    """PyamDataCollectorEnum class.

    Here it is defined what kind of data you want to collect.
    """

    HOURLY = "hourly"
    YEARLY = "yearly"


class PyamDataAnalysisEnum(enum.Enum):

    """PyamDataAnalysisEnum class.

    Here it is defined what kind of analysis you want to make.
    """

    SENSITIVITY_ANALYSIS = 1
    RANDOM = 2


def main():
    """Main function to execute the pyam data collection."""
    PyamDataCollector()


if __name__ == "__main__":
    start_time = time.time()
    main()
    print(f"---{time.time() - start_time} seconds ___")
