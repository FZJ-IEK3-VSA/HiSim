"""Data Collection for Scenario Comparison with Pyam."""
# clean
import glob
import os
from typing import Dict, Any, Optional, List, Tuple
import json
import enum
import shutil
import re
import pandas as pd
import ordered_set


from hisim import log


class PyamDataCollector:

    """PyamDataCollector class which collects and concatenate the pyam data from the system_setups/results."""

    def __init__(
        self,
        data_processing_mode: Any,
        simulation_duration_to_check: str,
        time_resolution_of_data_set: Any,
        folder_from_which_data_will_be_collected: str = os.path.join(
            os.pardir, os.pardir, "system_setups", "results"
        ),
        path_to_default_config: Optional[str] = None,
    ) -> None:
        """Initialize the class."""
        result_folder = folder_from_which_data_will_be_collected
        self.pyam_data_folder = os.path.join(
            os.pardir, os.pardir, "system_setups", "results_for_scenario_comparison", "data"
        )

        # in each system_setups/results folder should be one system setup that was executed with the default config
        self.path_of_pyam_results_executed_with_default_config: str = ""

        log.information(f"Checking results from folder: {result_folder}")

        # self.clean_result_directory_from_unfinished_results(result_path=result_folder)

        list_with_pyam_data_folders = self.get_list_of_all_relevant_pyam_data_folders(
            result_path=result_folder
        )
        print("lsit with pyam folders", list_with_pyam_data_folders)

        if data_processing_mode == PyamDataProcessingModeEnum.PROCESS_ALL_DATA:

            parameter_key = None

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

        elif (
            data_processing_mode
            == PyamDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_DELTA_T_IN_HP_CONTROLLER
        ):
            parameter_key = "delta_T"

        elif (
            data_processing_mode
            == PyamDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_HOT_WATER_STORAGE_SIZES
        ):
            parameter_key = "hot_water_storage_size_in_liter"

        elif (
            data_processing_mode
            == PyamDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_SHARE_OF_MAXIMUM_PV
        ):
            parameter_key = "share_of_maximum_pv_power"

        else:
            raise ValueError(
                "Analysis mode is not part of the PyamDataProcessingModeEnum class."
            )

        log.information(f"Data Collection Mode is {data_processing_mode}")

        print("parameter key ", parameter_key)
        print("##################")

        if path_to_default_config is None:
            list_with_parameter_key_values = None
            list_with_csv_files = list_with_pyam_data_folders
            list_with_module_config_dicts = None

        else:
            # path to default config is given (which means there should be also a module config dict in the json file in the pyam folder which has read the config)

            default_config_dict = self.get_default_config(
                path_to_default_config=path_to_default_config
            )

            (
                list_with_csv_files,
                list_with_parameter_key_values,
                list_with_module_config_dicts,
            ) = self.go_through_all_pyam_data_folders_and_collect_file_paths_according_to_parameters(
                list_with_pyam_data_folders=list_with_pyam_data_folders,
                default_config_dict=default_config_dict,
                parameter_key=parameter_key,
            )

        all_csv_files = self.import_data_from_file(
            paths_to_check=list_with_csv_files,
            analyze_yearly_or_hourly_data=time_resolution_of_data_set,
        )

        dict_of_csv_data = self.make_dictionaries_with_simulation_duration_keys(
            simulation_duration_to_check=simulation_duration_to_check,
            all_csv_files=all_csv_files,
        )

        self.read_csv_and_generate_pandas_dataframe(
            dict_of_csv_to_read=dict_of_csv_data,
            time_resolution_of_data_set=time_resolution_of_data_set,
            rename_scenario=True,
            parameter_key=parameter_key,
            list_with_parameter_key_values=list_with_parameter_key_values,
            list_with_module_config_dicts=list_with_module_config_dicts,
        )

        print("\n")

    def clean_result_directory_from_unfinished_results(
        self, result_path: str
    ) -> None:  # TODO: add functionality
        """When a result folder does not contain the finished_flag, it will be removed from the system_setups/result folder."""
        pass

    def get_list_of_all_relevant_pyam_data_folders(self, result_path: str) -> List[str]:
        """Get a list of all pyam data folders which you want to analyze."""
        print("result path", result_path)
        # choose which path to check
        path_to_check = os.path.join(result_path, "**", "pyam_data")

        list_of_paths_first_order = list(glob.glob(path_to_check))

        # if in these paths no pyam data folder can be found check in subfolders for it
        path_to_check = os.path.join(result_path, "**", "**", "pyam_data")  # type: ignore
        list_of_paths_second_order = list(glob.glob(path_to_check))

        path_to_check = os.path.join(result_path, "**", "**", "**", "pyam_data")  # type: ignore
        list_of_paths_third_order = list(glob.glob(path_to_check))

        list_with_all_paths_to_check = (
            list_of_paths_first_order
            + list_of_paths_second_order
            + list_of_paths_third_order
        )
        print("list with all paths to check for pyam data", list_with_all_paths_to_check)
        list_with_no_duplicates = self.go_through_all_pyam_data_folders_and_check_if_module_configs_are_double_somewhere(
            list_of_pyam_folder_paths_to_check=list_with_all_paths_to_check
        )
        print("list with all paths to check for pyam data after double check", list_with_no_duplicates)

        return list_with_no_duplicates

    def import_data_from_file(
        self, paths_to_check: List[str], analyze_yearly_or_hourly_data: Any
    ) -> List:
        """Import data from result files."""
        log.information("Importing pyam_data from csv files.")

        all_csv_files = []

        if analyze_yearly_or_hourly_data == PyamDataTypeEnum.HOURLY:
            kind_of_data_set = "hourly"
        elif analyze_yearly_or_hourly_data == PyamDataTypeEnum.YEARLY:
            kind_of_data_set = "yearly"
        elif analyze_yearly_or_hourly_data == PyamDataTypeEnum.DAILY:
            kind_of_data_set = "daily"
        elif analyze_yearly_or_hourly_data == PyamDataTypeEnum.MONTHLY:
            kind_of_data_set = "monthly"
        else:
            raise ValueError(
                "analyze_yearly_or_hourly_data was not found in the pyamdatacollectorenum class."
            )

        for folder in paths_to_check:  # type: ignore

            for file in os.listdir(folder):  # type: ignore
                # get yearly or hourly data
                if kind_of_data_set in file and file.endswith(".csv"):
                    all_csv_files.append(os.path.join(folder, file))  # type: ignore

        return all_csv_files

    def make_dictionaries_with_simulation_duration_keys(
        self,
        simulation_duration_to_check: str,
        all_csv_files: List[str],
    ) -> Dict:
        """Make dictionaries containing csv files of hourly or yearly data and according to the simulation duration of the data."""

        dict_of_csv_data: Dict[str, Any] = {}

        dict_of_csv_data[f"{simulation_duration_to_check}"] = []

        # open file config and check if they have wanted simulation duration
        for file in all_csv_files:

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
                        if int(simulation_duration_to_check) == int(
                            simulation_duration
                        ):
                            dict_of_csv_data[f"{simulation_duration}"].append(file)

        # raise error if dict is empty
        if bool(dict_of_csv_data) is False:
            raise ValueError(
                "The dictionary is empty. Maybe no data was collected. Please check your parameters again."
            )

        return dict_of_csv_data

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

    def sort_dataframe_according_to_scenario_values(
        self, dataframe: pd.DataFrame
    ) -> pd.DataFrame:
        """Sort dataframe according to scenario values."""

        # get parameter key values from scenario name
        values = []
        for scenario in dataframe["scenario"]:
            scenario_name_splitted = scenario.split("_")
            try:
                number = float(scenario_name_splitted[-1])
                values.append(number)
            except Exception:
                return dataframe

        # order the values
        ordered_values = list(ordered_set.OrderedSet(sorted(values)))

        # sort the order of the dataframe according to order of parameter key values
        new_df = pd.DataFrame()
        for sorted_value in ordered_values:

            for scenario in list(set(dataframe["scenario"])):
                scenario_name_splitted = scenario.split("_")
                number = float(scenario_name_splitted[-1])

                if sorted_value == number:
                    df_1 = dataframe.loc[dataframe["scenario"] == scenario]
                    new_df = pd.concat([new_df, df_1])

        return new_df

    def read_csv_and_generate_pandas_dataframe(
        self,
        dict_of_csv_to_read: Dict[str, list[str]],
        time_resolution_of_data_set: Any,
        rename_scenario: bool = False,
        parameter_key: Optional[str] = None,
        list_with_parameter_key_values: Optional[List[Any]] = None,
        list_with_module_config_dicts: Optional[List[Any]] = None,
    ) -> None:
        """Read the csv files and generate the pyam dataframe."""
        log.information(
            f"Read csv files and generate pyam dataframes for {time_resolution_of_data_set}."
        )

        appended_dataframe = pd.DataFrame()
        index = 0
        simulation_duration_key = list(dict_of_csv_to_read.keys())[0]
        csv_data_list = dict_of_csv_to_read[simulation_duration_key]

        for csv_file in csv_data_list:

            dataframe = pd.read_csv(csv_file)

            # add hash colum to dataframe so hash does not get lost when scenario is renamed
            # TODO: make this optional in case hash does not exist in scenario name
            hash_number = re.findall(r"\-?\d+", dataframe["scenario"][0])[-1]
            dataframe["hash"] = [hash_number] * len(dataframe["scenario"])

            # write all values that were in module config dict in the dataframe, so that you can use these values later for sorting and searching
            if list_with_module_config_dicts is not None:
                module_config_dict = list_with_module_config_dicts[index]
                for (
                    module_config_key,
                    module_config_value,
                ) in module_config_dict.items():
                    dataframe[module_config_key] = [module_config_value] * len(
                        dataframe["scenario"]
                    )

            if rename_scenario is True:
                if (
                    parameter_key is not None
                    and list_with_parameter_key_values is not None
                    and list_with_parameter_key_values != []
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

        # sort dataframe
        appended_dataframe = self.sort_dataframe_according_to_scenario_values(
            dataframe=appended_dataframe
        )

        filename = self.store_pyam_data_with_the_right_name_and_in_the_right_path(
            pyam_data_folder=self.pyam_data_folder,
            simulation_duration_key=simulation_duration_key,
            time_resolution_of_data_set=time_resolution_of_data_set,
            parameter_key=parameter_key,
        )
        appended_dataframe.to_csv(filename)

    def store_pyam_data_with_the_right_name_and_in_the_right_path(
        self,
        pyam_data_folder: str,
        simulation_duration_key: str,
        time_resolution_of_data_set: Any,
        parameter_key: Optional[str] = None,
    ) -> str:
        """Store csv files in the pyam data folder with the right filename and path."""

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
        list_with_csv_files: List[Any],
        list_with_parameter_key_values: List[Any],
        list_with_module_configs: List[Any],
        parameter_key: str,
    ) -> tuple[List[Any], List[Any], List[Any]]:
        """Read json config in pyam_data folder and compare with default config."""

        for file in os.listdir(path_to_pyam_data_folder):

            if ".json" in file:
                with open(os.path.join(path_to_pyam_data_folder, file), "r", encoding="utf-8") as openfile:  # type: ignore
                    config_dict = json.load(openfile)
                    my_module_config_dict = config_dict["myModuleConfig"]

        # check if module config and default config have any keys in common
        if len(set(default_config_dict).intersection(my_module_config_dict)) == 0:
            raise KeyError(
                f"The module config of the folder {path_to_pyam_data_folder} should contain the keys of the default config, otherwise their values cannot be compared."
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

        if my_module_config_dict[parameter_key] != default_config_dict[parameter_key]:

            list_with_csv_files.append(path_to_pyam_data_folder)
            list_with_parameter_key_values.append(my_module_config_dict[parameter_key])

            list_with_module_configs.append(my_module_config_dict)

        # add to each item in the dict also the default system setup if the default system setup exists

        if self.path_of_pyam_results_executed_with_default_config != "":
            list_with_csv_files.append(
                self.path_of_pyam_results_executed_with_default_config
            )
            list_with_parameter_key_values.append(default_config_dict[parameter_key])

            list_with_module_configs.append(default_config_dict)

        return (
            list_with_csv_files,
            list_with_parameter_key_values,
            list_with_module_configs,
        )

    def read_module_config_if_exist_and_write_in_dataframe(
        self,
        default_config_dict: Dict[str, Any],
        path_to_pyam_data_folder: str,
        list_with_module_configs: List[Any],
        list_with_csv_files: List[Any],
    ) -> Tuple[List, List]:
        """Read module config if possible and write to dataframe."""

        for file in os.listdir(path_to_pyam_data_folder):

            if ".json" in file:
                with open(os.path.join(path_to_pyam_data_folder, file), "r", encoding="utf-8") as openfile:  # type: ignore
                    config_dict = json.load(openfile)
                    my_module_config_dict = config_dict["myModuleConfig"]

        # check if module config and default config have any keys in common
        if len(set(default_config_dict).intersection(my_module_config_dict)) == 0:
            raise KeyError(
                f"The module config of the folder {path_to_pyam_data_folder} should contain the keys of the default config, otherwise their values cannot be compared."
            )

        list_with_module_configs.append(my_module_config_dict)
        list_with_csv_files.append(path_to_pyam_data_folder)

        return (
            list_with_module_configs,
            list_with_csv_files,
        )

    def go_through_all_pyam_data_folders_and_collect_file_paths_according_to_parameters(
        self,
        list_with_pyam_data_folders: List[str],
        default_config_dict: Dict[str, Any],
        parameter_key: Optional[str],
    ) -> tuple[List[Any], List[Any], List[Any]]:
        """Order result files according to different parameters."""

        list_with_module_configs: List = []
        list_with_csv_files: List = []
        list_with_parameter_key_values: List = []

        for folder in list_with_pyam_data_folders:  # type: ignore

            if parameter_key is None:
                (
                    list_with_module_configs,
                    list_with_csv_files,
                ) = self.read_module_config_if_exist_and_write_in_dataframe(
                    default_config_dict=default_config_dict,
                    path_to_pyam_data_folder=folder,
                    list_with_module_configs=list_with_module_configs,
                    list_with_csv_files=list_with_csv_files,
                )
                list_with_parameter_key_values = []

            else:

                (
                    list_with_csv_files,
                    list_with_parameter_key_values,
                    list_with_module_configs,
                ) = self.read_pyam_data_json_config_and_compare_to_default_config(
                    default_config_dict=default_config_dict,
                    path_to_pyam_data_folder=folder,
                    list_with_csv_files=list_with_csv_files,
                    list_with_parameter_key_values=list_with_parameter_key_values,
                    list_with_module_configs=list_with_module_configs,
                    parameter_key=parameter_key,
                )

        return (
            list_with_csv_files,
            list_with_parameter_key_values,
            list_with_module_configs,
        )

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
        """Go through all pyam folders and remove the system_setups that are duplicated."""

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
                        my_module_config_dict.update(
                            {"model": config_dict["pyamDataInformation"].get("model")}
                        )
                        my_module_config_dict.update(
                            {"model": config_dict["pyamDataInformation"].get("scenario")}
                        )

                        # prevent to add modules with same module config and same simulation duration twice
                        if my_module_config_dict not in list_of_all_module_configs:
                            list_of_all_module_configs.append(my_module_config_dict)
                            list_of_pyam_folders_which_have_only_unique_configs.append(
                                os.path.join(folder)
                            )

            # delete folders which have doubled results from system_setups/results directory
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

    HOURLY = "hourly"  # hourly not working yet
    DAILY = "daily"
    MONTHLY = "monthly"
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
    PROCESS_FOR_DIFFERENT_DELTA_T_IN_HP_CONTROLLER = 8
    PROCESS_FOR_DIFFERENT_HOT_WATER_STORAGE_SIZES = 9
    PROCESS_FOR_DIFFERENT_SHARE_OF_MAXIMUM_PV = 10
