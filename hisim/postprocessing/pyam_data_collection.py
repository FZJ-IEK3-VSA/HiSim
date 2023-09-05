"""Data Collection for Scenario Comparison with Pyam."""
# clean
import glob
import time
import os
from typing import Dict, Any, Optional, List
import json
import enum
from collections import defaultdict
import shutil
import pyam
import pandas as pd
import re

from hisim import log


class PyamDataCollector:

    """PyamDataCollector class which collects and concatenate the pyam data from the examples/results."""

    def __init__(
        self,
        data_processing_mode: Any,
        simulation_duration_to_check: str,
        analyze_yearly_or_hourly_data: Any,
        folder_from_which_data_will_be_collected: str = os.path.join(
            os.pardir, os.pardir, "examples", "results"
        ),
        path_to_default_config: Optional[str] = None,
        
        
    ) -> None:
        """Initialize the class."""
        result_folder = folder_from_which_data_will_be_collected
        self.pyam_data_folder = os.path.join(
            os.pardir, os.pardir, "examples", "results_for_scenario_comparison", "data"
        )

        # in each examples/results folder should be one example that was executed with the default config
        self.path_of_pyam_results_executed_with_default_config: str

        log.information(f"Checking results from folder: {result_folder}")

        self.clean_result_directory_from_unfinished_results(result_path=result_folder)

        list_with_pyam_data_folders = self.get_list_of_all_relevant_pyam_data_folders(
            result_path=result_folder
        )
        
        if (
            data_processing_mode
            == PyamDataProcessingModeEnum.PROCESS_ALL_DATA
        ):

            log.information(f"Data Collection Mode is {data_processing_mode}")
            
            parameter_key=None

        elif (
            data_processing_mode
            == PyamDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_BUILDING_SIZES
        ):
            parameter_key = "total_base_area_in_m2"
        
        elif (
            data_processing_mode
            == PyamDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_BUILDING_CODES
        ):
            parameter_key = "building_code"
            
        elif (
            data_processing_mode
            == PyamDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_PV_POWERS
        ):
            parameter_key = "pv_power"
            
            
        elif (
            data_processing_mode
            == PyamDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_PV_AZIMUTH_ANGLES
        ):
            parameter_key = "pv_azimuth"
            
            
        elif (
            data_processing_mode
            == PyamDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_PV_TILT_ANGLES
        ):
            parameter_key = "pv_tilt"


        else:
            raise ValueError(
                "Analysis mode is not part of the PyamDataProcessingModeEnum class."
            )
            
        log.information(f"Data Collection Mode is {data_processing_mode}")

        print("parameter key ", parameter_key)
        print("##################")
        if parameter_key is None:
            list_with_parameter_key_values = None
            path_to_check = list_with_pyam_data_folders
            
        elif parameter_key is not None and path_to_default_config is not None:
            
            default_config_dict = self.get_default_config(
            path_to_default_config=path_to_default_config
            )

            (
                dict_with_csv_files_for_each_parameter,
                dict_with_parameter_key_values,
            ) = self.go_through_all_pyam_data_folders_and_collect_file_paths_according_to_parameters(
                list_with_pyam_data_folders=list_with_pyam_data_folders,
                default_config_dict=default_config_dict,
            )
        
            if parameter_key in dict_with_csv_files_for_each_parameter and parameter_key in dict_with_parameter_key_values:
                list_with_parameter_key_values=dict_with_parameter_key_values[parameter_key]
                path_to_check = dict_with_csv_files_for_each_parameter[
                    parameter_key
                ]
            
            else:
                raise KeyError(f"The parameter key {parameter_key} was not found in the dictionary dict_with_csv_files_for_each_parameter or dict_with_parameter_key_values.")

        (
            all_simulation_durations,
            #all_hourly_csv_files,
            all_yearly_csv_files,
        ) = self.import_data_from_file(
            paths_to_check=path_to_check,
            simulation_duration_to_check=simulation_duration_to_check,
            analyze_yearly_or_hourly_data=analyze_yearly_or_hourly_data,
        )
        print(all_yearly_csv_files, all_simulation_durations)
        
        # (
        #         dict_of_yearly_csv_data,
        #         dict_of_hourly_csv_data,
        #     ) = self.make_dictionaries_with_simulation_duration_keys(
        #         simulation_durations=all_simulation_durations,
        #         hourly_data=all_hourly_csv_files,
        #         yearly_data=all_yearly_csv_files,
        #     )
        # if analyze_yearly_or_hourly_data == PyamDataTypeEnum.YEARLY:
        #     self.read_csv_and_generate_pyam_dataframe(
        #         dict_of_csv_to_read=dict_of_yearly_csv_data,
        #         kind_of_data=PyamDataTypeEnum.YEARLY,
        #         rename_scenario=True,
        #         parameter_key=parameter_key,
        #         list_with_parameter_key_values=list_with_parameter_key_values
        #     )
        # elif analyze_yearly_or_hourly_data == PyamDataTypeEnum.HOURLY:
        #     self.read_csv_and_generate_pyam_dataframe(
        #         dict_of_csv_to_read=dict_of_hourly_csv_data,
        #         kind_of_data=PyamDataTypeEnum.HOURLY,
        #         rename_scenario=True,
        #         parameter_key=parameter_key,
        #         list_with_parameter_key_values=list_with_parameter_key_values
        #     )
        # else:
        #     raise ValueError("analyze_yearly_or_hourly_data variable is not set or has incompatible value.")
        # print("\n")

    def clean_result_directory_from_unfinished_results(
        self, result_path: str
    ) -> None:  # TODO: add functionality
        """When a result folder does not contain the finished_flag, it will be removed from the examples/result folder."""
        pass

    def get_list_of_all_relevant_pyam_data_folders(self, result_path: str) -> List[str]:
        """Get a list of all pyam data folders which you want to analyze."""

        # choose which path to check
        path_to_check = os.path.join(result_path, "**", "pyam_data")
        list_of_paths = list(glob.glob(path_to_check))
        # if in these paths no pyam data folder can be found check in subfolders for it
        if len(list_of_paths) == 0:
            path_to_check = os.path.join(result_path, "**", "**", "pyam_data")  # type: ignore

        list_with_all_paths_to_check = glob.glob(path_to_check)
        list_with_no_duplicates = self.go_through_all_pyam_data_folders_and_check_if_module_configs_are_double_somewhere(
            list_of_pyam_folder_paths_to_check=list_with_all_paths_to_check
        )

        return list_with_no_duplicates

    def import_data_from_file(
        self, paths_to_check: List[str], simulation_duration_to_check: str, analyze_yearly_or_hourly_data: Any
    ) -> tuple[List, List, List]:
        """Import data from result files."""
        log.information("Importing pyam_data from csv files.")

        all_yearly_csv_files = []
        all_hourly_csv_files = []
        all_simulation_durations = []
        
        if analyze_yearly_or_hourly_data == PyamDataTypeEnum.HOURLY:
            kind_of_data_set = "hourly"
        elif analyze_yearly_or_hourly_data == PyamDataTypeEnum.YEARLY:
            kind_of_data_set = "yearly"
        else:
            raise ValueError(
                "analyze_yearly_or_hourly_data was not found in the pyamdatacollectorenum class."
            )

        for folder in paths_to_check:  # type: ignore

            for file in os.listdir(folder):  # type: ignore
                # get yearly data
                if kind_of_data_set in file and file.endswith(".csv"):
                    all_yearly_csv_files.append(os.path.join(folder, file))  # type: ignore

                # if "hourly_results" in file and file.endswith(".csv"):
                #     all_hourly_csv_files.append(os.path.join(folder, file))  # type: ignore

                # get simulation durations
                if ".json" in file:
                    with open(os.path.join(folder, file), "r", encoding="utf-8") as openfile:  # type: ignore
                        json_file = json.load(openfile)
                        simulation_duration = json_file["pyamDataInformation"].get(
                            "duration in days"
                        )
                        if simulation_duration_to_check == simulation_duration:
                            all_simulation_durations.append(simulation_duration)

        all_simulation_durations = list(set(all_simulation_durations))

        return all_simulation_durations, all_yearly_csv_files #all_hourly_csv_files, 

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

    def rename_scenario_name_of_dataframe_with_parameter_key_and_value(
        self,
        dataframe: pd.DataFrame,
        parameter_key: str,
        list_with_parameter_values: List[Any],
        index: int,
    ) -> Any:
        """Rename the scenario of the given dataframe adding parameter key and value."""
        value = list_with_parameter_values[index]
        if not isinstance(value, str):
            value = round(value, 1)
        dataframe["scenario"] = f"{parameter_key}_{value}"
        return dataframe["scenario"]

    def rename_scenario_name_of_dataframe_with_index(
        self, dataframe: pd.DataFrame, index: int
    ) -> Any:
        """Rename the scenario of the given dataframe adding an index."""
        dataframe["scenario"] = dataframe["scenario"] + f"_{index}"
        return dataframe["scenario"]

    def read_csv_and_generate_pyam_dataframe(
        self,
        dict_of_csv_to_read: Dict[str, list[str]],
        kind_of_data: Any,
        rename_scenario: bool = False,
        parameter_key: Optional[str] = None,
        list_with_parameter_key_values: Optional[List[Any]] = None,
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

                # add hash colum to dataframe so hash does not get lost when scenario is renamed
                hash_number = re.findall(r"\-?\d+", dataframe["scenario"][0])[-1]
                dataframe["hash"] = [hash_number] * len(dataframe["scenario"])

                if rename_scenario is True:
                    if (
                        parameter_key is not None
                        and list_with_parameter_key_values is not None
                    ):
                        # rename scenario adding paramter key, value pair
                        dataframe[
                            "scenario"
                        ] = self.rename_scenario_name_of_dataframe_with_parameter_key_and_value(
                            dataframe=dataframe,
                            parameter_key=parameter_key,
                            list_with_parameter_values=list_with_parameter_key_values,
                            index=index,
                        )
                    else:
                        # rename scenario adding an index
                        dataframe[
                            "scenario"
                        ] = self.rename_scenario_name_of_dataframe_with_index(
                            dataframe=dataframe, index=index
                        )

                appended_dataframe = pd.concat([appended_dataframe, dataframe])

                index = index + 1

            # df_pyam_for_one_simulation_duration = pyam.IamDataFrame(appended_dataframe)
            # convert unit "Watt" to "Watthour" because it makes plots more readable later, conversion factor is 1/3600s
            # df_pyam_for_one_simulation_duration = df_pyam_for_one_simulation_duration.convert_unit(
            #     current="W", to="Wh", factor=1 / 3600, inplace=False
            # )

            filename = self.store_pyam_data_with_the_right_name_and_in_the_right_path(
                pyam_data_folder=self.pyam_data_folder,
                simulation_duration_key=simulation_duration_key,
                kind_of_data=kind_of_data,
                parameter_key=parameter_key,
            )
            #df_pyam_for_one_simulation_duration.to_csv(filename)
            appended_dataframe.to_csv(filename)

    def store_pyam_data_with_the_right_name_and_in_the_right_path(
        self,
        pyam_data_folder: str,
        simulation_duration_key: str,
        kind_of_data: Any,
        parameter_key: Optional[str] = None,
    ) -> str:
        """Store csv files in the pyam data folder with the right filename and path."""

        if kind_of_data == PyamDataTypeEnum.HOURLY:
            kind_of_data_set = "hourly"
        elif kind_of_data == PyamDataTypeEnum.YEARLY:
            kind_of_data_set = "yearly"
        else:
            raise ValueError(
                "This kind of data was not found in the pyamdatacollectorenum class."
            )

        if parameter_key is not None:
            path_for_file = os.path.join(
                pyam_data_folder,
                f"data_with_different_{parameter_key}s",
                f"simulation_duration_of_{simulation_duration_key}_days",
            )
        else:
            path_for_file = os.path.join(
                pyam_data_folder,
                "data_with_all_parameters",
                f"simulation_duration_of_{simulation_duration_key}_days",
            )
        if os.path.exists(path_for_file) is False:
            os.makedirs(path_for_file)
        log.information(f"Saving pyam dataframe in {path_for_file} folder")

        filename = os.path.join(
            path_for_file,
            f"pyam_dataframe_for_{simulation_duration_key}_days_{kind_of_data_set}_data.csv",
        )

        return filename

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

    def go_through_all_pyam_data_folders_and_collect_file_paths_according_to_parameters(
        self,
        list_with_pyam_data_folders: List[str],
        default_config_dict: Dict[str, Any],
    ) -> tuple[Dict, Dict]:
        """Order result files according to different parameters."""

        dict_with_csv_files_for_each_parameter: Dict = defaultdict(list)
        dict_with_parameter_key_values: Dict = defaultdict(list)
        dict_with_opex_and_capex_costs: Dict = defaultdict(list)

        for folder in list_with_pyam_data_folders:  # type: ignore

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

    def check_for_duplicates_in_dict(
        self, dictionary_to_check: Dict[str, Any], key: str
    ) -> List:
        """Check for duplicates and return index of where the duplicates are found."""

        indices_of_duplicates = [
            index
            for index, value in enumerate(dictionary_to_check[key])
            if value in dictionary_to_check[key][:index]
        ]

        return indices_of_duplicates

    def go_through_all_pyam_data_folders_and_check_if_module_configs_are_double_somewhere(
        self, list_of_pyam_folder_paths_to_check: List[str]
    ) -> List[Any]:
        """Go through all pyam folders and remove the examples that are duplicated."""

        list_of_all_module_configs = []
        list_of_pyam_folders_which_have_only_unique_configs = []
        for folder in list_of_pyam_folder_paths_to_check:
            for file in os.listdir(folder):
                if ".json" in file:
                    with open(os.path.join(folder, file), "r", encoding="utf-8") as openfile:  # type: ignore
                        config_dict = json.load(openfile)
                        my_module_config_dict = config_dict["myModuleConfig"]
                        my_module_config_dict.update(
                            {
                                "duration in days": config_dict[
                                    "pyamDataInformation"
                                ].get("duration in days")
                            }
                        )

                        # prevent to add modules with same module config and same simulation duration twice
                        if my_module_config_dict not in list_of_all_module_configs:
                            list_of_all_module_configs.append(my_module_config_dict)
                            list_of_pyam_folders_which_have_only_unique_configs.append(
                                os.path.join(folder)
                            )

            # delete folders which have doubled results from examples/results directory
            if folder not in list_of_pyam_folders_which_have_only_unique_configs:
                # remove whole result folder from result directory
                whole_parent_folder = os.path.abspath(os.path.join(folder, os.pardir))
                log.information(
                    f"The result folder {whole_parent_folder} will be removed because these results are already existing."
                )
                shutil.rmtree(whole_parent_folder, ignore_errors=True)

        return list_of_pyam_folders_which_have_only_unique_configs


class PyamDataTypeEnum(enum.Enum):

    """PyamDataTypeEnum class.

    Here it is defined what kind of data you want to collect.
    """

    HOURLY = "hourly"
    YEARLY = "yearly"


class PyamDataProcessingModeEnum(enum.Enum):

    """PyamDataProcessingModeEnum class.

    Here it is defined what kind of data processing you want to make.
    """

    PROCESS_ALL_DATA = 1
    PROCESS_FOR_DIFFERENT_BUILDING_CODES = 2
    PROCESS_FOR_DIFFERENT_BUILDING_SIZES = 3
    PROCESS_FOR_DIFFERENT_PV_POWERS = 4
    PROCESS_FOR_DIFFERENT_PV_SIZES = 5
    PROCESS_FOR_DIFFERENT_PV_AZIMUTH_ANGLES = 6
    PROCESS_FOR_DIFFERENT_PV_TILT_ANGLES = 7

def main():
    """Main function to execute the pyam data collection."""

    PyamDataCollector(
            data_processing_mode=PyamDataProcessingModeEnum.PROCESS_ALL_DATA,
            folder_from_which_data_will_be_collected=os.path.join(
            os.pardir, os.pardir, "examples", "results"
            ),
            path_to_default_config="please insert path to your default module config",
            analyze_yearly_or_hourly_data=PyamDataTypeEnum.YEARLY,
        )


if __name__ == "__main__":
    start_time = time.time()
    main()
    print(f"---{time.time() - start_time} seconds ___")
