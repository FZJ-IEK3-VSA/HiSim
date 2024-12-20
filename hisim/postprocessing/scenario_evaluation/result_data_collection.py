"""Data Collection for Scenario Comparison."""
# clean
import glob
import datetime
import os
from typing import Dict, Any, Optional, List, Tuple
import json
import shutil
import re
from collections import defaultdict
import pandas as pd
from ordered_set import OrderedSet
from hisim import log
from hisim.postprocessing.scenario_evaluation.result_data_processing import (
    ResultDataProcessingModeEnum,
    ResultDataTypeEnum,
    DataFormatEnum,
)


class ResultDataCollection:

    """ResultDataCollection class which collects and concatenate the result data from the system_setups/results."""

    def __init__(
        self,
        scenario_analysis_config_name: str,
        data_format_type: str,
        data_processing_mode: Any,
        simulation_duration_to_check: str,
        time_resolution_of_data_set: Any,
        folder_from_which_data_will_be_collected: str = os.path.join(
            os.pardir, os.pardir, os.pardir, "system_setups", "results"
        ),
        path_to_default_config: Optional[str] = None,
    ) -> None:
        """Initialize the class."""
        result_folder = folder_from_which_data_will_be_collected
        self.result_data_folder = os.path.join(
            os.getcwd(), os.pardir, os.pardir, os.pardir, "system_setups", "scenario_comparison", "data",
        )
        if not os.path.exists(self.result_data_folder):
            os.makedirs(self.result_data_folder)

        # in each system_setups/results folder should be one system setup that was executed with the default config
        self.path_of_scenario_data_executed_with_default_config: str = ""
        self.data_format_type: str = data_format_type
        self.scenario_analysis_config_name: str = scenario_analysis_config_name

        log.information(f"Checking results from folder: {result_folder}")

        list_with_result_data_folders = self.get_only_useful_data(result_path=result_folder)

        if data_processing_mode == ResultDataProcessingModeEnum.PROCESS_ALL_DATA.name:
            parameter_key = None

        elif data_processing_mode == ResultDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_BUILDING_CODES.name:
            parameter_key = "building_code"

        else:
            raise ValueError("Analysis mode is not part of the ResultDataProcessingModeEnum class.")

        log.information(f"Data Collection Mode is {data_processing_mode}")

        print("parameter key ", parameter_key)
        print("##################")

        if path_to_default_config is None:
            list_with_parameter_key_values = None
            list_with_csv_files = list_with_result_data_folders
            list_with_module_config_dicts = None

        else:
            # path to default config is given (which means there should be also a module config dict in the json file in the result folder which has read the config)

            default_config_dict = self.get_default_config(path_to_default_config=path_to_default_config)

            (
                list_with_csv_files,
                list_with_parameter_key_values,
                list_with_module_config_dicts,
                list_building_set_heating_temperature_in_celsius,
                list_building_set_cooling_temperature_in_celsius,
                list_building_min_indoor_temperature_in_celsius,
                list_building_max_indoor_temperature_in_celsius,
                list_building_diff_min_indoor_and_set_heating_temperature_in_celsius,
                list_building_diff_max_indoor_and_set_cooling_temperature_in_celsius,
                list_building_temp_deviation_below_set_heating_in_celsius_hour,
                list_building_temp_deviation_above_set_cooling_in_celsius_hour,
            ) = self.go_through_all_result_data_folders_and_collect_file_paths_according_to_parameters(
                list_with_result_data_folders=list_with_result_data_folders,
                default_config_dict=default_config_dict,
                parameter_key=parameter_key,
            )

        if not list_with_csv_files:
            raise ValueError("list_with_csv_files is empty")

        all_csv_files = self.import_data_from_file(
            paths_to_check=list_with_csv_files, analyze_yearly_or_hourly_data=time_resolution_of_data_set,
        )

        dict_of_csv_data = self.make_dictionaries_with_simulation_duration_keys(
            simulation_duration_to_check=simulation_duration_to_check, all_csv_files=all_csv_files,
        )

        (
            self.filepath_of_aggregated_dataframe,
            dict_with_all_data,
        ) = self.alternative_read_csv_and_generate_pandas_dataframe(
            dict_of_csv_to_read=dict_of_csv_data,
            time_resolution_of_data_set=time_resolution_of_data_set,
            rename_scenario=True,
            parameter_key=parameter_key,
            list_with_parameter_key_values=list_with_parameter_key_values,
            list_with_module_config_dicts=list_with_module_config_dicts,
        )

        self.generate_pandas_dataframe_with_building_temperatures(
            dict_with_all_data=dict_with_all_data,
            list_building_set_heating_temperature_in_celsius=list_building_set_heating_temperature_in_celsius,
            list_building_set_cooling_temperature_in_celsius=list_building_set_cooling_temperature_in_celsius,
            list_building_min_indoor_temperature_in_celsius=list_building_min_indoor_temperature_in_celsius,
            list_building_max_indoor_temperature_in_celsius=list_building_max_indoor_temperature_in_celsius,
            list_building_diff_min_indoor_and_set_heating_temperature_in_celsius=list_building_diff_min_indoor_and_set_heating_temperature_in_celsius,
            list_building_diff_max_indoor_and_set_cooling_temperature_in_celsius=list_building_diff_max_indoor_and_set_cooling_temperature_in_celsius,
            list_building_temp_deviation_below_set_heating_in_celsius_hour=list_building_temp_deviation_below_set_heating_in_celsius_hour,
            list_building_temp_deviation_above_set_cooling_in_celsius_hour=list_building_temp_deviation_above_set_cooling_in_celsius_hour,
        )

        print("\n")

    def get_only_useful_data(self, result_path: str) -> List[str]:
        """Go through all result folders and filter only useful data and write unuseful data into txt file."""

        # go through result path and if the dirs do not contain finished.flag ask for deletion
        self.clean_result_directory_from_unfinished_results(result_path=result_path)

        # get result folders with result data folder
        list_with_all_paths_to_check = self.get_list_of_all_relevant_folders_or_files(
            result_path=result_path, folder_or_filename="result_data_for_scenario_evaluation"
        )
        print(
            "len of list with all paths to containing result data ", len(list_with_all_paths_to_check),
        )
        if len(list_with_all_paths_to_check) == 0:
            raise ValueError(
                "Result paths for scenario evaluation could not be found. Please check your result folder paths."
            )
        if len(list_with_all_paths_to_check) < 20:
            print(
                "list with all paths to containing result data ", list_with_all_paths_to_check,
            )
        # check if duplicates are existing and ask for deletion
        list_with_result_data_folders = self.go_through_all_scenario_data_folders_and_check_if_module_configs_are_double_somewhere(
            # list_of_result_folder_paths_to_check=list_with_all_paths_to_check_after_filtering
            list_of_result_folder_paths_to_check=list_with_all_paths_to_check
        )

        print(
            "len of list with all paths after double checking for duplicates ", len(list_with_result_data_folders),
        )
        return list_with_result_data_folders

    def clean_result_directory_from_unfinished_results(self, result_path: str) -> None:
        """When a result folder does not contain the finished_flag, it will be removed from the system_setups/result folder."""
        list_of_unfinished_folders = []
        file_name = os.path.join(self.result_data_folder, "failed_simualtions.txt")
        mode = "a" if os.path.exists(file_name) else "w"
        with open(file_name, mode, encoding="utf-8",) as file:
            file.write(str(datetime.datetime.now()) + "\n")
            file.write("Failed simulations found in the following folders: \n")
            list_with_all_potential_finsihed_flag_files = self.get_list_of_all_relevant_folders_or_files(
                result_path=result_path, folder_or_filename="finished.flag"
            )
            for filename in list_with_all_potential_finsihed_flag_files:
                if not os.path.exists(filename):
                    file.write(os.path.join(filename) + "\n")
                    list_of_unfinished_folders.append(filename)

            file.write(
                f"Total number of failed simulations in path {result_path}: {len(list_of_unfinished_folders)}"
                + "\n"
                + "\n"
            )

        print(
            f"The following result folders do not contain the finished flag: {list_of_unfinished_folders} Number: {len(list_of_unfinished_folders)}. "
        )

        # if list of unfinished folders is not empty
        if list_of_unfinished_folders:
            answer = input("Do you want to delete them?")
            if answer.upper() in ["Y", "YES"]:
                for filename in list_of_unfinished_folders:
                    shutil.rmtree(os.path.join(result_path, filename))
                print("All folders with failed simulations deleted.")
            elif answer.upper() in ["N", "NO"]:
                print("The folders won't be deleted.")
            else:
                print("The answer must be yes or no.")

    def get_indoor_air_temperatures_of_building(
        self,
        folder: str,
        list_building_set_heating_temperature_in_celsius: List,
        list_building_min_indoor_temperature_in_celsius: List,
        list_building_diff_min_indoor_and_set_heating_temperature_in_celsius: List,
        list_building_set_cooling_temperature_in_celsius: List,
        list_building_max_indoor_temperature_in_celsius: List,
        list_building_diff_max_indoor_and_set_cooling_temperature_in_celsius: List,
        list_building_temp_deviation_below_set_heating_in_celsius_hour: List,
        list_building_temp_deviation_above_set_cooling_in_celsius_hour: List,
    ) -> Tuple[List, List, List, List, List, List, List, List]:
        """Get indoor air temperatures of building."""
        scenario_data_information_new_version = os.path.join(folder, "data_for_scenario_evaluation.json")
        scenario_data_information_old_version = os.path.join(folder, "data_information_for_scenario_evaluation.json")
        main_folder = os.path.normpath(folder + os.sep + os.pardir)
        all_kpis_json_file = os.path.join(main_folder, "all_kpis.json")

        # get set temperatures used in the simulation
        if os.path.exists(scenario_data_information_new_version):
            with open(scenario_data_information_new_version, "r", encoding="utf-8") as data_info_file:
                try:
                    simulation_configuration_data = json.load(data_info_file)
                except Exception as exc:
                    content = data_info_file.read()
                    if content.strip() == "":
                        raise ValueError(
                            "The json file is empty. Maybe run the simulation again. "
                            f"The concerned folder is {folder}"
                        ) from exc
                component_entries = simulation_configuration_data["componentEntries"]
                for component in component_entries:
                    if "Building" in component["componentName"]:
                        set_heating_temperature = float(
                            component["configuration"].get("set_heating_temperature_in_celsius")
                        )
                        set_cooling_temperature = float(
                            component["configuration"].get("set_cooling_temperature_in_celsius")
                        )
                        break
        elif os.path.exists(scenario_data_information_old_version):
            with open(scenario_data_information_old_version, "r", encoding="utf-8") as data_info_file:
                try:
                    simulation_configuration_data = json.load(data_info_file)
                except Exception as exc:
                    content = data_info_file.read()
                    if content.strip() == "":
                        raise ValueError(
                            "The json file is empty. Maybe run the simulation again. "
                            f"The concerned folder is {folder}"
                        ) from exc
                component_entries = simulation_configuration_data["componentEntries"]
                for component in component_entries:
                    if "Building" in component["componentName"]:
                        set_heating_temperature = float(
                            component["configuration"].get("set_heating_temperature_in_celsius")
                        )
                        set_cooling_temperature = float(
                            component["configuration"].get("set_cooling_temperature_in_celsius")
                        )
                        break
        else:
            raise FileNotFoundError(
                f"Neither the file {scenario_data_information_new_version} nor the file {scenario_data_information_old_version} could not be found. "
            )

        # open the webtool kpis and check if building got too hot or too cold
        if os.path.exists(all_kpis_json_file):
            with open(all_kpis_json_file, "r", encoding="utf-8") as kpi_file:
                # try two methods because older and newer data have different formats
                try:
                    kpi_data = json.load(kpi_file)["BUI1"]
                except Exception:
                    # Reset file pointer to the beginning
                    kpi_file.seek(0)
                    contents = kpi_file.read()
                    if not contents.strip():
                        print(f"Raw contents:\n{repr(contents)}")
                    try:
                        kpi_data = json.loads(contents)
                    except json.JSONDecodeError as err:
                        print("Invalid JSON syntax:", err)

                # check if min and max temperatures are too low or too high
                min_temperature = float(
                    kpi_data["Building"]["Minimum building indoor air temperature reached"].get("value")
                )
                max_temperature = float(
                    kpi_data["Building"]["Maximum building indoor air temperature reached"].get("value")
                )
                temp_deviation_below_set = kpi_data["Building"][
                    f"Temperature deviation of building indoor air temperature being below set temperature {set_heating_temperature} Celsius"
                ].get("value")
                temp_deviation_above_set = kpi_data["Building"][
                    f"Temperature deviation of building indoor air temperature being above set temperature {set_cooling_temperature} Celsius"
                ].get("value")
                # append all to lists
                list_building_set_heating_temperature_in_celsius.append(set_heating_temperature)
                list_building_min_indoor_temperature_in_celsius.append(min_temperature)
                list_building_diff_min_indoor_and_set_heating_temperature_in_celsius.append(
                    set_heating_temperature - min_temperature
                )
                list_building_set_cooling_temperature_in_celsius.append(set_cooling_temperature)
                list_building_max_indoor_temperature_in_celsius.append(max_temperature)
                list_building_diff_max_indoor_and_set_cooling_temperature_in_celsius.append(
                    max_temperature - set_cooling_temperature
                )
                list_building_temp_deviation_below_set_heating_in_celsius_hour.append(temp_deviation_below_set)
                list_building_temp_deviation_above_set_cooling_in_celsius_hour.append(temp_deviation_above_set)
                # list_building_result_path.append(folder)
                return (
                    list_building_set_heating_temperature_in_celsius,
                    list_building_min_indoor_temperature_in_celsius,
                    list_building_diff_min_indoor_and_set_heating_temperature_in_celsius,
                    list_building_set_cooling_temperature_in_celsius,
                    list_building_max_indoor_temperature_in_celsius,
                    list_building_diff_max_indoor_and_set_cooling_temperature_in_celsius,
                    list_building_temp_deviation_below_set_heating_in_celsius_hour,
                    list_building_temp_deviation_above_set_cooling_in_celsius_hour,
                )

    def get_list_of_all_relevant_folders_or_files(self, result_path: str, folder_or_filename: str) -> List[str]:
        """Get a list of all folders or files which you want to analyze."""

        # choose which path to check
        path_to_check = os.path.join(result_path, "**", folder_or_filename)

        list_of_paths_first_order = list(glob.glob(path_to_check))

        # if in these paths no result data folder can be found check in subfolders for it
        path_to_check = os.path.join(result_path, "**", "**", folder_or_filename)  # type: ignore
        list_of_paths_second_order = list(glob.glob(path_to_check))

        path_to_check = os.path.join(result_path, "**", "**", "**", folder_or_filename)  # type: ignore
        list_of_paths_third_order = list(glob.glob(path_to_check))

        list_with_all_paths_to_check = (
            list_of_paths_first_order + list_of_paths_second_order + list_of_paths_third_order
        )

        return list_with_all_paths_to_check

    def import_data_from_file(self, paths_to_check: List[str], analyze_yearly_or_hourly_data: str) -> List:
        """Import data from result files."""
        log.information("Importing result_data_for_scenario_evaluation from csv files.")

        all_csv_files = []

        if analyze_yearly_or_hourly_data == ResultDataTypeEnum.HOURLY.name:
            kind_of_data_set = "hourly"
        elif analyze_yearly_or_hourly_data == ResultDataTypeEnum.YEARLY.name:
            kind_of_data_set = "yearly"
        elif analyze_yearly_or_hourly_data == ResultDataTypeEnum.DAILY.name:
            kind_of_data_set = "daily"
        elif analyze_yearly_or_hourly_data == ResultDataTypeEnum.MONTHLY.name:
            kind_of_data_set = "monthly"
        else:
            raise ValueError("analyze_yearly_or_hourly_data was not found in the datacollectorenum class.")

        for folder in paths_to_check:  # type: ignore
            for file in os.listdir(folder):  # type: ignore
                # get yearly or hourly data
                if kind_of_data_set in file and file.endswith(".csv"):
                    all_csv_files.append(os.path.join(folder, file))  # type: ignore

        return all_csv_files

    def make_dictionaries_with_simulation_duration_keys(
        self, simulation_duration_to_check: str, all_csv_files: List[str],
    ) -> Dict:
        """Make dictionaries containing csv files of hourly or yearly data and according to the simulation duration of the data."""

        dict_of_csv_data: Dict[str, Any] = {}

        dict_of_csv_data[f"{simulation_duration_to_check}"] = []

        # open file config and check if they have wanted simulation duration
        for file in all_csv_files:
            parent_folder = os.path.abspath(os.path.join(file, os.pardir))  # type: ignore
            for file1 in os.listdir(parent_folder):
                if ".json" in file1:
                    with open(os.path.join(parent_folder, file1), "r", encoding="utf-8") as openfile:
                        json_file = json.load(openfile)
                        simulation_duration = json_file["scenarioDataInformation"].get("duration in days")
                        if int(simulation_duration_to_check) == int(simulation_duration):
                            dict_of_csv_data[f"{simulation_duration}"].append(file)
                        else:
                            raise ValueError(
                                f"The simulation_duration_to_check of {simulation_duration_to_check} is different,"
                                f"to the simulation duration of {simulation_duration} found in the scenario data information json in the result folders."
                            )

        # raise error if dict is empty
        if bool(dict_of_csv_data) is False:
            raise ValueError(
                "The dictionary is empty. Maybe no data was collected. Please check your parameters again."
            )

        return dict_of_csv_data

    def generate_pandas_dataframe_with_building_temperatures(
        self,
        dict_with_all_data: Dict,
        list_building_set_heating_temperature_in_celsius: List,
        list_building_min_indoor_temperature_in_celsius: List,
        list_building_diff_min_indoor_and_set_heating_temperature_in_celsius: List,
        list_building_set_cooling_temperature_in_celsius: List,
        list_building_max_indoor_temperature_in_celsius: List,
        list_building_diff_max_indoor_and_set_cooling_temperature_in_celsius: List,
        list_building_temp_deviation_below_set_heating_in_celsius_hour: List,
        list_building_temp_deviation_above_set_cooling_in_celsius_hour: List,
    ) -> None:
        """Generate the result dataframe with building temperatures."""
        dict_with_no_duplicates = dict.fromkeys(dict_with_all_data, {})

        # get rows with unique house indices
        list_with_unique_house_indices = []
        for house_index in list(OrderedSet(dict_with_all_data["Index"]["Index"])):

            row_of_this_house_index = dict_with_all_data["Index"]["Index"].index(house_index)
            list_with_unique_house_indices.append(row_of_this_house_index)

        for key_1, dict_1 in dict_with_all_data.items():
            new_dict_1 = {}
            for key_2, value_list_2 in dict_1.items():
                value_list_with_unique_index = [
                    value_list_2[unique_index] for unique_index in list_with_unique_house_indices
                ]
                new_dict_1.update({key_2: value_list_with_unique_index})
            # add key, value pairs to dict_with_no_dulicates
            dict_with_no_duplicates[key_1] = new_dict_1

        if len(dict_with_no_duplicates["Index"]["Index"]) != len(list_building_set_heating_temperature_in_celsius):
            raise ValueError(
                "Dict with all data and temperature lists have differernt length: "
                + str(len(dict_with_no_duplicates["Index"]["Index"]))
                + "vs"
                + str(len(list_building_set_heating_temperature_in_celsius))
            )

        # Initialize dictionaries to hold data
        dict_with_input_data = {key: dict_with_no_duplicates[key] for key in ["Index", "Input"]}
        dict_with_temperature_data: Dict[str, defaultdict] = {
            "Output": defaultdict(list),
        }

        for house_index, set_heating_temperature in enumerate(list_building_set_heating_temperature_in_celsius):

            # Add outputs to dict
            dict_with_temperature_data["Output"]["building set heating temperature [°C]"].append(
                set_heating_temperature
            )
            dict_with_temperature_data["Output"]["building min indoor temerature [°C]"].append(
                list_building_min_indoor_temperature_in_celsius[house_index]
            )
            dict_with_temperature_data["Output"][
                "difference between set heating and min indoor temperature [°C]"
            ].append(list_building_diff_min_indoor_and_set_heating_temperature_in_celsius[house_index])
            dict_with_temperature_data["Output"]["temperature deviation below set heating temperature [°Ch]"].append(
                list_building_temp_deviation_below_set_heating_in_celsius_hour[house_index]
            )
            dict_with_temperature_data["Output"]["building set cooling temperature [°C]"].append(
                list_building_set_cooling_temperature_in_celsius[house_index]
            )
            dict_with_temperature_data["Output"]["building max indoor temperature [°C]"].append(
                list_building_max_indoor_temperature_in_celsius[house_index]
            )
            dict_with_temperature_data["Output"][
                "difference between set cooling and max indoor temperature [°C]"
            ].append(list_building_diff_max_indoor_and_set_cooling_temperature_in_celsius[house_index])
            dict_with_temperature_data["Output"]["temperature deviation above set cooling temperature [°Ch]"].append(
                list_building_temp_deviation_above_set_cooling_in_celsius_hour[house_index]
            )

        # merge the two dictionaries
        dict_with_input_data.update(dict_with_temperature_data)

        # create multiindex columns
        multi_index_columns = pd.MultiIndex.from_tuples(
            [(key1, key2) for key1, v1_dict in dict_with_input_data.items() for key2 in v1_dict.keys()],
            names=["first", "second"],
        )
        # add everything to the dataframe
        appended_dataframe = pd.DataFrame(
            {
                (key1, key2): value_list2
                for key1, v1_dict in dict_with_input_data.items()
                for key2, value_list2 in v1_dict.items()
            },
            columns=multi_index_columns,
        )

        appended_dataframe.to_csv(os.path.join(self.result_data_folder, "building_indoor_temperature_analysis.csv",))

        del appended_dataframe
        del dict_with_all_data
        del dict_with_input_data
        del dict_with_temperature_data

    def alternative_read_csv_and_generate_pandas_dataframe(
        self,
        dict_of_csv_to_read: Dict[str, list[str]],
        time_resolution_of_data_set: Any,
        rename_scenario: bool = False,
        parameter_key: Optional[str] = None,
        list_with_parameter_key_values: Optional[List[Any]] = None,
        list_with_module_config_dicts: Optional[List[Any]] = None,
    ) -> Tuple[str, Dict]:
        """Read the csv files and generate the result dataframe."""
        log.information(f"Read csv files and generate result dataframes for {time_resolution_of_data_set}.")

        if not dict_of_csv_to_read:
            raise ValueError("The input dictionary dict_of_csv_to_read is empty.")

        simulation_duration_key = list(dict_of_csv_to_read.keys())[0]
        csv_data_list = dict_of_csv_to_read[simulation_duration_key]

        if not csv_data_list:
            raise ValueError("csv_data_list is empty.")

        # Initialize dictionaries to hold data
        dict_with_all_data: Dict[str, defaultdict] = {
            "Index": defaultdict(list),
            "Input": defaultdict(list),
            "Output": defaultdict(list),
        }

        for house_index, csv_file in enumerate(csv_data_list):
            log.information(f"Reading data from house number {house_index}")
            dataframe = pd.read_csv(csv_file)
            set_of_variables = dataframe["variable"].unique()

            for variable in set_of_variables:
                filtered_df = dataframe[dataframe["variable"] == variable]

                if time_resolution_of_data_set != ResultDataTypeEnum.YEARLY.name and "time" in dataframe.columns:
                    # Process time series data
                    time_values = filtered_df["time"]
                    filtered_df = filtered_df[~time_values.str.isalnum()]
                    # if in timeseries data there are also yearly data contained (like KPIs) which cause an empty filtered_df, the variable should be skipped
                    if filtered_df.empty:
                        continue
                    for time_value, value in zip(filtered_df["time"], filtered_df["value"]):
                        dict_with_all_data["Output"][time_value].append(value)
                elif time_resolution_of_data_set == ResultDataTypeEnum.YEARLY.name:
                    # Process yearly data
                    dict_with_all_data["Output"][str(dataframe["year"].iloc[0])].append(filtered_df["value"].iloc[0])

                original_scenario_name = str(filtered_df["scenario"].iloc[0])
                if not original_scenario_name:
                    raise ValueError(
                        "The scenario variable of the current dataframe is empty. Please set a scenario name for your simulations."
                    )

                try:
                    hash_number = re.findall(r"-?\d+", original_scenario_name)[-1]
                except IndexError:
                    hash_number = 1
                    rename_scenario = False

                # Add input and metadata values to the dictionary
                dict_with_all_data["Input"]["model"].append(filtered_df["model"].iloc[0])
                dict_with_all_data["Input"]["region"].append(filtered_df["region"].iloc[0])
                dict_with_all_data["Input"]["hash"].append(hash_number)

                # Add outputs to dict
                dict_with_all_data["Output"]["variable"].append(variable)
                dict_with_all_data["Output"]["unit"].append(filtered_df["unit"].tolist()[0])

                if list_with_module_config_dicts is not None:
                    module_config_dict = list_with_module_config_dicts[house_index]
                    for key, value in module_config_dict.items():
                        if not isinstance(value, Dict):
                            dict_with_all_data["Input"][key].append(value)
                        else:
                            for key_2, value_2 in value.items():
                                dict_with_all_data["Input"][key_2].append(value_2)

                dict_with_all_data["Index"]["Index"].append(house_index)

                if rename_scenario and parameter_key and list_with_parameter_key_values:
                    value = list_with_parameter_key_values[house_index]
                    final_scenario_name = f"{value if isinstance(value, str) else round(value, 1)}"
                else:
                    final_scenario_name = original_scenario_name

                dict_with_all_data["Input"]["scenario"].append(final_scenario_name)

        if not dict_with_all_data:
            raise ValueError("The dict_with_all_data is empty")

        # create multiindex columns
        multi_index_columns = pd.MultiIndex.from_tuples(
            [(key1, key2) for key1, v1_dict in dict_with_all_data.items() for key2 in v1_dict.keys()],
            names=["first", "second"],
        )
        # add everything to the dataframe
        appended_dataframe = pd.DataFrame(
            {
                (key1, key2): value_list2
                for key1, v1_dict in dict_with_all_data.items()
                for key2, value_list2 in v1_dict.items()
            },
            columns=multi_index_columns,
        )

        if self.data_format_type == DataFormatEnum.CSV.name:
            # create filename
            filename = self.store_scenario_data_with_the_right_name_and_in_the_right_path(
                result_data_folder=self.result_data_folder,
                simulation_duration_key=simulation_duration_key,
                time_resolution_of_data_set=time_resolution_of_data_set,
                parameter_key=parameter_key,
                data_format_type="csv",
                scenario_analysis_config_name=self.scenario_analysis_config_name,
            )
            # save file compressed
            # appended_dataframe.to_csv(f"{filename}.gz", compression="gzip")
            appended_dataframe.to_csv(filename)

        elif self.data_format_type == DataFormatEnum.XLSX.name:
            # create filename
            filename = self.store_scenario_data_with_the_right_name_and_in_the_right_path(
                result_data_folder=self.result_data_folder,
                simulation_duration_key=simulation_duration_key,
                time_resolution_of_data_set=time_resolution_of_data_set,
                parameter_key=parameter_key,
                data_format_type="xlsx",
                scenario_analysis_config_name=self.scenario_analysis_config_name,
            )
            # save file (use zip64 for handling large excel files)
            with pd.ExcelWriter(filename, engine="xlsxwriter") as writer:  # pylint: disable=abstract-class-instantiated
                workbook = writer.book
                workbook.use_zip64()
                appended_dataframe.to_excel(writer, sheet_name="Sheet1")

        else:
            raise ValueError(f"Only data format types xlsx or csv are implemented. Here it is {self.data_format_type}.")

        log.information(f"Saving result dataframe here: {filename}")

        del appended_dataframe
        # del dict_with_all_data
        return filename, dict_with_all_data

    def store_scenario_data_with_the_right_name_and_in_the_right_path(
        self,
        result_data_folder: str,
        simulation_duration_key: str,
        time_resolution_of_data_set: str,
        data_format_type: str,
        scenario_analysis_config_name: str,
        parameter_key: Optional[str] = None,
    ) -> str:
        """Store csv files in the result data folder with the right filename and path."""

        if time_resolution_of_data_set == ResultDataTypeEnum.HOURLY.name:
            kind_of_data_set = "hourly"
        elif time_resolution_of_data_set == ResultDataTypeEnum.YEARLY.name:
            kind_of_data_set = "yearly"
        elif time_resolution_of_data_set == ResultDataTypeEnum.DAILY.name:
            kind_of_data_set = "daily"
        elif time_resolution_of_data_set == ResultDataTypeEnum.MONTHLY.name:
            kind_of_data_set = "monthly"
        else:
            raise ValueError("This kind of data was not found in the datacollectorenum class.")

        if parameter_key is not None:
            path_for_file = os.path.join(
                result_data_folder, f"data_different_{parameter_key}s", f"{simulation_duration_key}_days",
            )
        else:
            path_for_file = os.path.join(result_data_folder, "data_all_parameters", f"{simulation_duration_key}_days",)
        if os.path.exists(path_for_file) is False:
            os.makedirs(path_for_file)

        filename = os.path.join(
            path_for_file, f"result_df_{kind_of_data_set}_{scenario_analysis_config_name}.{data_format_type}",
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

    def read_scenario_data_json_config_and_compare_to_default_config(
        self,
        default_config_dict: Dict[str, Any],
        path_to_scenario_data_folder: str,
        list_with_csv_files: List[Any],
        list_with_parameter_key_values: List[Any],
        list_with_module_configs: List[Any],
        parameter_key: str,
    ) -> tuple[List[Any], List[Any], List[Any]]:
        """Read json config in result_data_for_scenario_evaluation folder and compare with default config."""

        for file in os.listdir(path_to_scenario_data_folder):
            if ".json" in file:
                with open(os.path.join(path_to_scenario_data_folder, file), "r", encoding="utf-8") as openfile:  # type: ignore
                    config_dict = json.load(openfile)
                    my_module_config_dict = config_dict["myModuleConfig"]
                    scenario_name = config_dict["systemName"]

                    # for paper: reference scenario without use of pv should have a share of maximum pv power of 0
                    if "ref_" in scenario_name:
                        try:
                            my_module_config_dict["share_of_maximum_pv_power"] = 0
                        except Exception as ecx:
                            raise KeyError(
                                "The key share of maximum pv power does not exist in the module dict. Unable this function if it not needed."
                            ) from ecx

        # check if module config and default config have any keys in common
        if len(set(default_config_dict).intersection(my_module_config_dict)) == 0:
            raise KeyError(
                f"The module config of the folder {path_to_scenario_data_folder} should contain the keys of the default config,",
                "otherwise their values cannot be compared.",
            )
        # check if there is a module config which is equal to default config

        if all(item in my_module_config_dict.items() for item in default_config_dict.items()):
            self.path_of_scenario_data_executed_with_default_config = path_to_scenario_data_folder

        # for each parameter different than the default config parameter, get the respective path to the folder
        # and also create a dict with the parameter, value pairs

        list_with_csv_files.append(path_to_scenario_data_folder)
        list_with_parameter_key_values.append(my_module_config_dict[parameter_key])

        list_with_module_configs.append(my_module_config_dict)

        # add to each item in the dict also the default system setup if the default system setup exists

        if self.path_of_scenario_data_executed_with_default_config != "":
            list_with_csv_files.append(self.path_of_scenario_data_executed_with_default_config)
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
        path_to_scenario_data_folder: str,
        list_with_module_configs: List[Any],
        list_with_csv_files: List[Any],
    ) -> Tuple[List, List]:
        """Read module config if possible and write to dataframe."""

        for file in os.listdir(path_to_scenario_data_folder):
            if ".json" in file:
                with open(os.path.join(path_to_scenario_data_folder, file), "r", encoding="utf-8") as openfile:  # type: ignore
                    config_dict = json.load(openfile)
                    my_module_config_dict = config_dict["myModuleConfig"]

        # check if module config and default config have any keys in common
        if len(set(default_config_dict).intersection(my_module_config_dict)) == 0:
            raise KeyError(
                f"The module config of the folder {path_to_scenario_data_folder} should contain the keys of the default config,",
                "otherwise their values cannot be compared.",
            )

        list_with_module_configs.append(my_module_config_dict)
        list_with_csv_files.append(path_to_scenario_data_folder)

        return (
            list_with_module_configs,
            list_with_csv_files,
        )

    def go_through_all_result_data_folders_and_collect_file_paths_according_to_parameters(
        self,
        list_with_result_data_folders: List[str],
        default_config_dict: Dict[str, Any],
        parameter_key: Optional[str],
    ) -> tuple[List[Any], List[Any], List[Any]]:
        """Order result files according to different parameters."""

        list_with_module_configs: List = []
        list_with_csv_files: List = []
        list_with_parameter_key_values: List = []
        list_building_set_heating_temperature_in_celsius = []
        list_building_set_cooling_temperature_in_celsius = []
        list_building_min_indoor_temperature_in_celsius = []
        list_building_max_indoor_temperature_in_celsius = []
        list_building_diff_min_indoor_and_set_heating_temperature_in_celsius = []
        list_building_diff_max_indoor_and_set_cooling_temperature_in_celsius = []
        list_building_temp_deviation_below_set_heating_in_celsius_hour = []
        list_building_temp_deviation_above_set_cooling_in_celsius_hour = []

        for folder in list_with_result_data_folders:  # type: ignore
            if parameter_key is None:
                (
                    list_with_module_configs,
                    list_with_csv_files,
                ) = self.read_module_config_if_exist_and_write_in_dataframe(
                    default_config_dict=default_config_dict,
                    path_to_scenario_data_folder=folder,
                    list_with_module_configs=list_with_module_configs,
                    list_with_csv_files=list_with_csv_files,
                )
                list_with_parameter_key_values = []

            else:
                (
                    list_with_csv_files,
                    list_with_parameter_key_values,
                    list_with_module_configs,
                ) = self.read_scenario_data_json_config_and_compare_to_default_config(
                    default_config_dict=default_config_dict,
                    path_to_scenario_data_folder=folder,
                    list_with_csv_files=list_with_csv_files,
                    list_with_parameter_key_values=list_with_parameter_key_values,
                    list_with_module_configs=list_with_module_configs,
                    parameter_key=parameter_key,
                )
            (
                list_building_set_heating_temperature_in_celsius,
                list_building_set_cooling_temperature_in_celsius,
                list_building_min_indoor_temperature_in_celsius,
                list_building_max_indoor_temperature_in_celsius,
                list_building_diff_min_indoor_and_set_heating_temperature_in_celsius,
                list_building_diff_max_indoor_and_set_cooling_temperature_in_celsius,
                list_building_temp_deviation_below_set_heating_in_celsius_hour,
                list_building_temp_deviation_above_set_cooling_in_celsius_hour,
            ) = self.get_indoor_air_temperatures_of_building(
                folder=folder,
                list_building_set_heating_temperature_in_celsius=list_building_set_heating_temperature_in_celsius,
                list_building_min_indoor_temperature_in_celsius=list_building_min_indoor_temperature_in_celsius,
                list_building_diff_min_indoor_and_set_heating_temperature_in_celsius=list_building_diff_min_indoor_and_set_heating_temperature_in_celsius,
                list_building_set_cooling_temperature_in_celsius=list_building_set_cooling_temperature_in_celsius,
                list_building_max_indoor_temperature_in_celsius=list_building_max_indoor_temperature_in_celsius,
                list_building_diff_max_indoor_and_set_cooling_temperature_in_celsius=list_building_diff_max_indoor_and_set_cooling_temperature_in_celsius,
                list_building_temp_deviation_below_set_heating_in_celsius_hour=list_building_temp_deviation_below_set_heating_in_celsius_hour,
                list_building_temp_deviation_above_set_cooling_in_celsius_hour=list_building_temp_deviation_above_set_cooling_in_celsius_hour,
            )

        return (
            list_with_csv_files,
            list_with_parameter_key_values,
            list_with_module_configs,
            list_building_set_heating_temperature_in_celsius,
            list_building_set_cooling_temperature_in_celsius,
            list_building_min_indoor_temperature_in_celsius,
            list_building_max_indoor_temperature_in_celsius,
            list_building_diff_min_indoor_and_set_heating_temperature_in_celsius,
            list_building_diff_max_indoor_and_set_cooling_temperature_in_celsius,
            list_building_temp_deviation_below_set_heating_in_celsius_hour,
            list_building_temp_deviation_above_set_cooling_in_celsius_hour,
        )

    def check_for_duplicates_in_dict(self, dictionary_to_check: Dict[str, Any], key: str) -> List:
        """Check for duplicates and return index of where the duplicates are found."""

        indices_of_duplicates = [
            index for index, value in enumerate(dictionary_to_check[key]) if value in dictionary_to_check[key][:index]
        ]

        return indices_of_duplicates

    def go_through_all_scenario_data_folders_and_check_if_module_configs_are_double_somewhere(
        self, list_of_result_folder_paths_to_check: List[str]
    ) -> List[Any]:
        """Go through all result folders and remove the system_setups that are duplicated."""
        if list_of_result_folder_paths_to_check == []:
            raise ValueError(
                "No HiSim results could be found in the results folder. Please check if you are collecting results from the correct folder."
            )

        list_of_all_module_configs = []
        list_of_result_folders_which_have_only_unique_configs = []
        for folder in list_of_result_folder_paths_to_check:
            for file in os.listdir(folder):
                if ".json" in file:
                    filename = os.path.join(folder, file)
                    with open(filename, "r", encoding="utf-8") as openfile:  # type: ignore
                        config_dict = json.load(openfile)
                        try:
                            my_module_config_dict = config_dict["myModuleConfig"]
                            my_module_config_dict.update(
                                {"duration in days": config_dict["scenarioDataInformation"].get("duration in days")}
                            )
                            my_module_config_dict.update({"model": config_dict["scenarioDataInformation"].get("model")})
                            my_module_config_dict.update(
                                {"model": config_dict["scenarioDataInformation"].get("scenario")}
                            )
                        except Exception as exc:
                            raise KeyError(
                                f"The file {filename} does not contain any key called myModuleConfig."
                            ) from exc
                        # prevent to add modules with same module config and same simulation duration twice
                        if my_module_config_dict not in list_of_all_module_configs:
                            list_of_all_module_configs.append(my_module_config_dict)
                            list_of_result_folders_which_have_only_unique_configs.append(os.path.join(folder))

            # get folders with duplicates
            list_with_duplicates = []
            if folder not in list_of_result_folders_which_have_only_unique_configs:
                whole_parent_folder = os.path.abspath(os.path.join(folder, os.pardir))
                list_with_duplicates.append(whole_parent_folder)

        print(
            f"The following folders seem to be duplicated: {list_with_duplicates}. Number: {len(list_with_duplicates)}."
        )
        # if list is not empty
        if list_with_duplicates:
            answer = input("Do you want to delete the duplicated folders?")
            if answer.upper() in ["Y", "YES"]:
                for folder in list_with_duplicates:
                    shutil.rmtree(folder, ignore_errors=True)
                print("All folders with duplicated results are deleted.")
            elif answer.upper() in ["N", "NO"]:
                print("These folders won't be deleted.")
            else:
                print("The answer must be yes or no.")

        return list_of_result_folders_which_have_only_unique_configs
