"""Data Collection for Scenario Comparison with Pyam."""
# clean
import glob
import time
import os
from typing import Dict, Any, Optional, List
import json
import enum
from collections import defaultdict
import pyam
import pandas as pd

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

        # in each examples/results folder should be one example that ran with the default config
        self.path_of_pyam_results_executed_with_default_config: str

        log.information(f"Getting results from folder: {self.result_folder}")

        if analysis_mode == PyamDataAnalysisEnum.SENSITIVITY_ANALYSIS:

            default_config_dict = self.get_default_config(
                path_to_default_config=path_to_default_config
            )

            (
                dict_with_csv_files_for_each_parameter,
                dict_with_parameter_key_values,
            ) = self.go_through_all_result_folders_and_store_file_paths_according_to_parameters(
                result_path=self.result_folder, default_config_dict=default_config_dict
            )

            for key in dict_with_csv_files_for_each_parameter.keys():
                print("parameter key ", key)
                print("##################")
                list_with_pyam_data_paths_for_one_parameter = (
                    dict_with_csv_files_for_each_parameter[key]
                )

                (
                    all_simulation_durations,
                    all_hourly_csv_files,
                    all_yearly_csv_files,
                ) = self.import_data_from_file(
                    paths_to_check=list_with_pyam_data_paths_for_one_parameter
                )

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
                    parameter_key=key,
                    list_with_parameter_key_values=dict_with_parameter_key_values[key],
                )
                self.read_csv_and_generate_pyam_dataframe(
                    dict_of_csv_to_read=dict_of_hourly_csv_data,
                    kind_of_data=PyamDataCollectorEnum.HOURLY,
                    parameter_key=key,
                    list_with_parameter_key_values=dict_with_parameter_key_values[key],
                )
                print("\n")

        # elif analysis_mode == PyamDataAnalysisEnum.RANDOM:

        #     (
        #         all_simulation_durations,
        #         all_hourly_csv_files,
        #         all_yearly_csv_files,
        #     ) = self.import_data_from_file(paths_to_check=self.result_folder)
        #     (
        #         dict_of_yearly_csv_data,
        #         dict_of_hourly_csv_data,
        #     ) = self.make_dictionaries_with_simulation_duration_keys(
        #         simulation_durations=all_simulation_durations,
        #         hourly_data=all_hourly_csv_files,
        #         yearly_data=all_yearly_csv_files,
        #     )
        #     self.read_csv_and_generate_pyam_dataframe(
        #         dict_of_csv_to_read=dict_of_yearly_csv_data,
        #         kind_of_data=PyamDataCollectorEnum.YEARLY,
        #     )
        #     self.read_csv_and_generate_pyam_dataframe(
        #         dict_of_csv_to_read=dict_of_hourly_csv_data,
        #         kind_of_data=PyamDataCollectorEnum.HOURLY,
        #     )

        # else:
        #     raise ValueError(
        #         "Analysis mode is not part of the PyamDataAnalysorEnum class."
        #     )

    def import_data_from_file(
        self, paths_to_check: List[str]
    ) -> tuple[List, List, List]:
        """Import data from result files."""
        log.information("Importing pyam_data from csv files.")

        all_yearly_csv_files = []
        all_hourly_csv_files = []
        all_simulation_durations = []

        # # choose which path to check
        # path_to_check = os.path.join(folder_path, "**", "pyam_data")
        # list_of_paths = list(glob.glob(path_to_check))
        # # if in these paths no pyam data folder can be found check in subfolders for it
        # if len(list_of_paths) == 0:
        #     path_to_check = os.path.join(folder_path, "**", "**", "pyam_data")  # type: ignore

        # for folder in glob.glob(path_to_check):  # type: ignore
        for folder in paths_to_check:  # type: ignore

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
                        simulation_duration = json_file["pyamDataInformation"].get(
                            "duration in days"
                        )
                        all_simulation_durations.append(simulation_duration)

        all_simulation_durations = list(set(all_simulation_durations))

        return all_simulation_durations, all_hourly_csv_files, all_yearly_csv_files

    def make_dictionaries_with_simulation_duration_keys(
        self,
        simulation_durations: List[int],
        hourly_data: List[str],
        yearly_data: List[str],
    ) -> tuple[Dict, Dict]:
        """Make dictionaries containing csv files of hourly and yearly data and sort them according to the simulation duration of the data."""

        dict_of_yearly_csv_data_for_different_simulation_duration: Dict[str, Any] = {}
        dict_of_hourly_csv_data_for_different_simulation_duration: Dict[str, Any] = {}

        # get a list of all simulation durations that exist and use them as key for the data dictionaries
        for simulation_duration in simulation_durations:
            dict_of_yearly_csv_data_for_different_simulation_duration[
                f"{simulation_duration}"
            ] = []
            dict_of_hourly_csv_data_for_different_simulation_duration[
                f"{simulation_duration}"
            ] = []

        # yearly_data_csv_data = []
        # yearly_data_set = []
        # # prevent that csv data exists more than 1 time in the list
        # print(yearly_data)
        # for path in yearly_data:

        #     if path.split("\\")[-1] not in yearly_data_csv_data:
        #         yearly_data_csv_data.append(path.split("\\")[-1])
        #         yearly_data_set.append(path)

        # hourly_data_csv_data = []
        # hourly_data_set = []
        # for path in hourly_data:

        #     if path.split("\\")[-1] not in hourly_data_csv_data:
        #         hourly_data_csv_data.append(path.split("\\")[-1])
        #         hourly_data_set.append(path)

        yearly_data_set = yearly_data
        hourly_data_set = hourly_data

        # order files according to their simualtion durations
        for file in yearly_data_set:

            parent_folder = os.path.abspath(os.path.join(file, os.pardir))  # type: ignore
            for file1 in os.listdir(parent_folder):
                if ".json" in file1:
                    with open(
                        os.path.join(parent_folder, file1), "r", encoding="utf-8"
                    ) as openfile:
                        json_file = json.load(openfile)
                        simulation_duration = json_file["pyamDataInformation"].get(
                            "duration in days"
                        )
                        if simulation_duration in simulation_durations:
                            dict_of_yearly_csv_data_for_different_simulation_duration[
                                f"{simulation_duration}"
                            ].append(file)

        for file in hourly_data_set:

            parent_folder = os.path.abspath(os.path.join(file, os.pardir))  # type: ignore
            for file1 in os.listdir(parent_folder):
                if ".json" in file1:
                    with open(
                        os.path.join(parent_folder, file1), "r", encoding="utf-8"
                    ) as openfile:
                        json_file = json.load(openfile)
                        simulation_duration = json_file["pyamDataInformation"].get(
                            "duration in days"
                        )
                        if simulation_duration in simulation_durations:
                            dict_of_hourly_csv_data_for_different_simulation_duration[
                                f"{simulation_duration}"
                            ].append(file)
        return (
            dict_of_yearly_csv_data_for_different_simulation_duration,
            dict_of_hourly_csv_data_for_different_simulation_duration,
        )

    def rename_scenario_name_of_dataframe(
        self,
        dataframe: pd.DataFrame,
        parameter_key: str = None,
        parameter_value: Any = None,
    ) -> pd.Series:
        """Rename the scenario of the given dataframe."""
        if None not in (parameter_key, parameter_value):
            dataframe["scenario"] = f"{parameter_key}_{parameter_value}"
        else:
            raise ValueError(
                "Parameter key and value not given. if you want to rename the scenario, feel free to implement other methods in this function."
            )
        return dataframe["scenario"]

    def read_csv_and_generate_pyam_dataframe(
        self,
        dict_of_csv_to_read: Dict[str, list[str]],
        kind_of_data: Any,
        parameter_key: str,
        list_with_parameter_key_values: List[Any],
    ) -> None:
        """Read the csv files and generate the pyam dataframe for different simulation durations."""
        log.information(
            f"Read csv files and generate pyam dataframes for {kind_of_data}."
        )
        if bool(dict_of_csv_to_read) is False:
            raise ValueError("The passed dictionary is empty.")

        for simulation_duration_key, csv_data_list in dict_of_csv_to_read.items():
            appended_dataframe = pd.DataFrame()
            index = 0
            for csv_file in csv_data_list:

                dataframe = pd.read_csv(csv_file)
                # rename scenario according to parameter key
                dataframe["scenario"] = self.rename_scenario_name_of_dataframe(
                    dataframe=dataframe,
                    parameter_key=parameter_key,
                    parameter_value=list_with_parameter_key_values[index],
                )  # f"{parameter_key}_{list_with_parameter_key_values[index]}"

                appended_dataframe = pd.concat([appended_dataframe, dataframe])

                index = index + 1
            # # transform scenario values to str values
            # appended_dataframe["scenario"] = appended_dataframe["scenario"].transform(
            #     lambda x: str(x)
            # )

            df_pyam_for_one_simulation_duration = pyam.IamDataFrame(appended_dataframe)
            # convert unit "Watt" to "Watthour" because it makes plots more readable later, conversion factor is 1/3600s
            # df_pyam_for_one_simulation_duration = df_pyam_for_one_simulation_duration.convert_unit(
            #     current="W", to="Wh", factor=1 / 3600, inplace=False
            # )

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
                        f"data_with_different_{parameter_key}s",
                        f"simulation_duration_of_{simulation_duration_key}_days",
                    )
                )
                is False
            ):
                os.makedirs(
                    os.path.join(
                        self.pyam_data_folder,
                        f"data_with_different_{parameter_key}s",
                        f"simulation_duration_of_{simulation_duration_key}_days",
                    )
                )
            log.information(
                f"Saving pyam dataframe in Hisim/examples/results_for_scenario_comparison/data/data_with_different_{parameter_key}s folder"
            )
            df_pyam_for_one_simulation_duration.to_csv(
                os.path.join(
                    self.pyam_data_folder,
                    f"data_with_different_{parameter_key}s",
                    f"simulation_duration_of_{simulation_duration_key}_days",
                    f"pyam_dataframe_for_{simulation_duration_key}_days_{kind_of_data_set}_data.csv",
                )
            )

    def get_default_config(self, path_to_default_config: Optional[str]) -> Any:
        """Get default config."""

        if path_to_default_config is not None and ".json" in path_to_default_config:
            with open(path_to_default_config, "r", encoding="utf-8") as openfile:  # type: ignore
                default_config_dict = json.load(openfile)

        else:
            raise ValueError("The default config is not in .json format.")

        return default_config_dict

    def read_pyam_data_json_config_and_compare_to_default_config(
        self,
        default_config_dict: Dict[str, Any],
        path_to_pyam_data_folder: str,
        dict_with_csv_files_for_each_parameter: Dict[str, List[str]],
        dict_with_parameter_key_values: Dict[str, Any],
    ) -> tuple[Dict, Dict]:
        """Read json config in pyam_data folder and compare with default config."""

        for file in os.listdir(path_to_pyam_data_folder):

            if ".json" in file:
                with open(os.path.join(path_to_pyam_data_folder, file), "r", encoding="utf-8") as openfile:  # type: ignore
                    config_dict = json.load(openfile)
                    my_module_config_dict = config_dict["myModuleConfig"]

        # check if module config and default config have any keys in common
        if len(set(default_config_dict).intersection(my_module_config_dict)) == 0:
            raise KeyError(
                "The module config should contain the keys of the default config, otherwise their values cannot be compared."
            )
        # check if there is a module config which is equal to default config
        if all(
            item in my_module_config_dict.items()
            for item in default_config_dict.items()
        ):
            self.path_of_pyam_results_executed_with_default_config = (
                path_to_pyam_data_folder
            )

        # for each parameter different than the default config parameter, get the respective path to the folder
        # and also create a dict with the parameter, value pairs

        for default_key, default_value in default_config_dict.items():

            if my_module_config_dict[default_key] != default_value:

                dict_with_csv_files_for_each_parameter[default_key] += [
                    path_to_pyam_data_folder
                ]
                dict_with_parameter_key_values[default_key] += [
                    my_module_config_dict[default_key]
                ]

        return dict_with_csv_files_for_each_parameter, dict_with_parameter_key_values

    def go_through_all_result_folders_and_store_file_paths_according_to_parameters(
        self, result_path: str, default_config_dict: Dict[str, Any]
    ) -> tuple[Dict, Dict]:
        """Order result files according to different parameters."""
        # choose which path to check
        path_to_check = os.path.join(result_path, "**", "pyam_data")
        list_of_paths = list(glob.glob(path_to_check))
        # if in these paths no pyam data folder can be found check in subfolders for it
        if len(list_of_paths) == 0:
            path_to_check = os.path.join(result_path, "**", "**", "pyam_data")  # type: ignore
        dict_with_csv_files_for_each_parameter: Dict = defaultdict(list)
        dict_with_parameter_key_values: Dict = defaultdict(list)
        for folder in glob.glob(path_to_check):  # type: ignore

            (
                dict_with_csv_files_for_each_parameter,
                dict_with_parameter_key_values,
            ) = self.read_pyam_data_json_config_and_compare_to_default_config(
                default_config_dict=default_config_dict,
                path_to_pyam_data_folder=folder,
                dict_with_csv_files_for_each_parameter=dict_with_csv_files_for_each_parameter,
                dict_with_parameter_key_values=dict_with_parameter_key_values,
            )

        # add to each item in the dict also the default example
        for key in dict_with_csv_files_for_each_parameter.keys():
            dict_with_csv_files_for_each_parameter[key].append(
                self.path_of_pyam_results_executed_with_default_config
            )
            dict_with_parameter_key_values[key].append(default_config_dict[key])

        return dict_with_csv_files_for_each_parameter, dict_with_parameter_key_values


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
    PyamDataCollector(analysis_mode=PyamDataAnalysisEnum.SENSITIVITY_ANALYSIS, path_to_default_config="please insert path to your default module config")


if __name__ == "__main__":
    start_time = time.time()
    main()
    print(f"---{time.time() - start_time} seconds ___")
