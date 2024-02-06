"""Result Data Processing and Plotting for Scenario Comparison."""


import glob
import os
from typing import Dict, Any, Tuple, Optional, List
import pandas as pd

from hisim.postprocessing.scenario_evaluation.result_data_collection import (
    ResultDataTypeEnum,
)
from hisim import log


class ScenarioDataProcessing:

    """ScenarioDataProcessing class."""

    @staticmethod
    def get_dataframe_and_create_pandas_dataframe_for_all_data(
        data_folder_path: str,
        time_resolution_of_data_set: Any,
        dict_of_scenarios_to_check: Optional[Dict[str, List[str]]],
        variables_to_check: List[str],
    ) -> Tuple[pd.DataFrame, str, str, List[str]]:
        """Get csv data and create dataframes with the filtered and procesed scenario data."""

        if time_resolution_of_data_set == ResultDataTypeEnum.HOURLY:
            kind_of_data_set = "hourly"
        elif time_resolution_of_data_set == ResultDataTypeEnum.YEARLY:
            kind_of_data_set = "yearly"
        elif time_resolution_of_data_set == ResultDataTypeEnum.DAILY:
            kind_of_data_set = "daily"
        elif time_resolution_of_data_set == ResultDataTypeEnum.MONTHLY:
            kind_of_data_set = "monthly"
        else:
            raise ValueError("This kind of data was not found in the datacollectorenum class.")
        log.information(f"Read csv files and create one big dataframe for {kind_of_data_set} data.")

        csv_data_file_path = os.path.join(data_folder_path, "**", f"*{kind_of_data_set}*.csv")
        list_of_possible_data_csv_files = glob.glob(csv_data_file_path)
        file: str = ""
        if not list_of_possible_data_csv_files:
            raise FileExistsError(f"No csv file could be found in this path {csv_data_file_path}.")
        if len(list_of_possible_data_csv_files) > 1:
            raise ValueError(f"The csv file path {csv_data_file_path} should not contain more than one csv file.")

        file = list_of_possible_data_csv_files[0]

        file_df = pd.read_csv(filepath_or_buffer=file)

        # if scenario values are no strings, transform them
        file_df["scenario"] = file_df["scenario"].transform(str)
        key_for_scenario_one = ""
        key_for_current_scenario = ""

        if dict_of_scenarios_to_check is not None and dict_of_scenarios_to_check != {}:
            (
                file_df,
                key_for_scenario_one,
                key_for_current_scenario,
            ) = ScenarioDataProcessing.check_if_scenario_exists_and_filter_dataframe_for_scenarios_dict(
                data_frame=file_df,
                dict_of_scenarios_to_check=dict_of_scenarios_to_check,
            )

        return (
            file_df,
            key_for_scenario_one,
            key_for_current_scenario,
            variables_to_check,
        )

    @staticmethod
    def filter_pandas_dataframe(dataframe: pd.DataFrame, variable_to_check: str) -> pd.DataFrame:
        """Filter pandas dataframe according to variable."""
        filtered_dataframe = dataframe.loc[dataframe["variable"] == variable_to_check]
        if filtered_dataframe.empty:
            print(f"The dataframe contains the following variables: {set(list(dataframe.variable))}")
            # raise ValueError(
            print(
                f"The filtered dataframe is empty. The dataframe did not contain the variable {variable_to_check}. Check the list above."
            )
        return filtered_dataframe

    @staticmethod
    def get_statistics_of_data_and_write_to_excel(
        filtered_data: pd.DataFrame,
        path_to_save: str,
        kind_of_data_set: str,
    ) -> None:
        """Use pandas describe method to get statistical values of certain data."""
        # create a excel writer object
        with pd.ExcelWriter(  # pylint: disable=abstract-class-instantiated
            path=os.path.join(path_to_save, f"{kind_of_data_set}_statistics.xlsx"),
            mode="w",
        ) as writer:
            filtered_data.to_excel(excel_writer=writer, sheet_name="filtered data")
            statistical_data = filtered_data.describe()

            statistical_data.to_excel(excel_writer=writer, sheet_name="statistics")

    @staticmethod
    def check_if_scenario_exists_and_filter_dataframe_for_scenarios(
        data_frame: pd.DataFrame,
        dict_of_scenarios_to_check: Dict[str, List[str]],
    ) -> pd.DataFrame:
        """Check if scenario exists and filter dataframe for scenario."""
        for (list_of_scenarios_to_check,) in dict_of_scenarios_to_check.values():
            aggregated_scenario_dict: Dict = {key: [] for key in list_of_scenarios_to_check}

            for given_scenario in data_frame["scenario"]:
                # string comparison

                for scenario_to_check in list_of_scenarios_to_check:
                    if (
                        scenario_to_check in given_scenario
                        and given_scenario not in aggregated_scenario_dict[scenario_to_check]
                    ):
                        aggregated_scenario_dict[scenario_to_check].append(given_scenario)
            # raise error if dict is empty
            for (
                key_scenario_to_check,
                given_scenario,
            ) in aggregated_scenario_dict.items():
                if given_scenario == []:
                    raise ValueError(f"Scenarios containing {key_scenario_to_check} were not found in the dataframe.")

            concat_df = pd.DataFrame()
            # only take rows from dataframe which are in selected scenarios
            for (
                key_scenario_to_check,
                given_scenario,
            ) in aggregated_scenario_dict.items():
                df_filtered_for_specific_scenarios = data_frame.loc[data_frame["scenario"].isin(given_scenario)]
                df_filtered_for_specific_scenarios["scenario"] = [key_scenario_to_check] * len(
                    df_filtered_for_specific_scenarios["scenario"]
                )
                concat_df = pd.concat([concat_df, df_filtered_for_specific_scenarios])
                concat_df["scenario_0"] = data_frame["scenario"]

        return concat_df

    @staticmethod
    def aggregate_all_values_for_one_scenario(
        dataframe: pd.DataFrame,
        list_of_scenarios_to_check: List,
        column_name_to_check: str,
        # filter_level_index: int,
    ) -> pd.DataFrame:
        """Check for one scenario."""

        aggregated_scenario_dict: Dict = {key: [] for key in list_of_scenarios_to_check}

        for scenario_to_check in list_of_scenarios_to_check:
            print("scenario to check", scenario_to_check)
            for value in dataframe[column_name_to_check].values:
                if (
                    isinstance(scenario_to_check, str)
                    and scenario_to_check in value
                    and value not in aggregated_scenario_dict[scenario_to_check]
                ):
                    aggregated_scenario_dict[scenario_to_check].append(value)
                elif (
                    isinstance(scenario_to_check, (float, int))
                    and scenario_to_check == value
                    and value not in aggregated_scenario_dict[scenario_to_check]
                ):
                    aggregated_scenario_dict[scenario_to_check].append(value)

        concat_df = pd.DataFrame()
        # only take rows from dataframe which are in selected scenarios
        for (
            key_scenario_to_check,
            given_list_of_values,
        ) in aggregated_scenario_dict.items():
            df_filtered_for_specific_scenarios = dataframe.loc[
                dataframe[column_name_to_check].isin(given_list_of_values)
            ]

            df_filtered_for_specific_scenarios.loc[:, "scenario"] = key_scenario_to_check

            concat_df = pd.concat([concat_df, df_filtered_for_specific_scenarios], ignore_index=True)

            # concat_df[f"scenario_{filter_level_index}"] = dataframe.loc[:, "scenario"]

            del df_filtered_for_specific_scenarios

        return concat_df

    @staticmethod
    def check_if_scenario_exists_and_filter_dataframe_for_scenarios_dict(
        data_frame: pd.DataFrame,
        dict_of_scenarios_to_check: Dict[str, List[str]],
    ) -> Tuple[pd.DataFrame, str, str]:
        """Check if scenario exists and filter dataframe for scenario."""

        concat_df = data_frame
        filter_level_index = 0
        for (
            scenario_to_check_key,
            list_of_scenarios_to_check,
        ) in dict_of_scenarios_to_check.items():
            concat_df = ScenarioDataProcessing.aggregate_all_values_for_one_scenario(
                dataframe=concat_df,
                list_of_scenarios_to_check=list_of_scenarios_to_check,
                column_name_to_check=scenario_to_check_key,
                # filter_level_index=filter_level_index,
            )

            filter_level_index = filter_level_index + 1
        key_for_scenario_one = ""
        key_for_current_scenario = ""
        # rename scenario with all scenario filter levels
        for index in concat_df.index:
            # if even more filter levels need to add condition!
            if filter_level_index == 2:
                current_scenario_value = concat_df["scenario"][index]
                scenario_value_one = concat_df["scenario_1"][index]
                # scenario zero is original scenario that will be overwritten
                key_for_scenario_one = list(dict_of_scenarios_to_check.keys())[0]
                key_for_current_scenario = list(dict_of_scenarios_to_check.keys())[1]
                # concat_df.iloc[index, concat_df.columns.get_loc("scenario")] = f"{scenario_value_one}_{current_scenario_value}"
                concat_df.loc[index, "scenario"] = f"{scenario_value_one}_{current_scenario_value}"
            elif filter_level_index == 1:
                key_for_scenario_one = list(dict_of_scenarios_to_check.keys())[0]
                key_for_current_scenario = ""
        return concat_df, key_for_scenario_one, key_for_current_scenario


class FilterClass:

    """Class for setting filters on the data for processing."""

    def __init__(self):
        """Initialize the class."""

        (
            self.kpi_data,
            self.electricity_data,
            self.occuancy_consumption,
            self.heating_demand,
            self.variables_for_debugging_purposes,
        ) = self.get_variables_to_check()
        (
            self.building_type,
            self.building_refurbishment_state,
            self.building_age,
            self.pv_share,
        ) = self.get_scenarios_to_check()

    def get_variables_to_check(self):
        """Get specific variables to check for the scenario evaluation."""

        # system_setups for variables to check (check names of your variables before your evaluation, if they are correct)
        # kpi data has no time series, so only choose when you analyze yearly data
        kpi_data = [
            "Production",
            "Consumption",
            "Ratio between energy production and consumption",
            "Injection",
            "Self-consumption",
            "Self-consumption rate",
            "Self-consumption rate according to mydualsun",
            "Autarky rate",
            "Total energy from grid",
            "Total energy to grid",
            "Relative electricity demand from grid",
            "Investment costs for equipment per simulated period",
            "CO2 footprint for equipment per simulated period",
            "System operational costs for simulated period",
            "System operational emissions for simulated period",
            "Total costs for simulated period",
            "Total CO2 emissions for simulated period",
            "Temperature deviation of building indoor air temperature being below set temperature 19.0 Celsius",
            "Minimum building indoor air temperature reached",
            "Temperature deviation of building indoor air temperature being above set temperature 24.0 Celsius",
            "Maximum building indoor air temperature reached",
            "Building heating load",
            "Specific heating load",
            "Specific heating demand according to TABULA",
            "Thermal output energy of heat distribution system",
            "Number of heat pump cycles",
            "Seasonal performance factor of heat pump",
            "Thermal output energy of heat pump",
            "Specific thermal output energy of heat pump",
            "Electrical input energy of heat pump"
        ]

        electricity_data = [
            "ElectricityMeter|Electricity|ElectricityToGrid",
            "ElectricityMeter|Electricity|ElectricityFromGrid",
            "ElectricityMeter|Electricity|ElectricityAvailable",
            # if you analyze a house with ems the production and consumption values of the electricity meter are not representative
            # use the ems production and consumption or the kpi values instead if needed
            # "ElectricityMeter|Electricity|ElectricityConsumption",
            # "ElectricityMeter|Electricity|ElectricityProduction",
        ]

        occuancy_consumption = [
            "Occupancy|Electricity|ElectricityOutput",
            "Occupancy|WarmWater|WaterConsumption",
        ]

        heating_demand = [
            "AdvancedHeatPumpHPLib|Heating|ThermalOutputPower",
            "Building|Temperature|TemperatureIndoorAir",
        ]
        variables_for_debugging_purposes = [
            "AdvancedHeatPumpHPLib|Heating|ThermalOutputPower",
            "Building|Temperature|TemperatureIndoorAir",
            "AdvancedHeatPumpHPLib|Any|COP",
            "Battery_w1|Any|StateOfCharge",
        ]

        return (
            kpi_data,
            electricity_data,
            occuancy_consumption,
            heating_demand,
            variables_for_debugging_purposes,
        )

    def get_scenarios_to_check(self):
        """Get scenarios to check for scenario evaluation."""

        (
            building_type,
            building_refurbishment_state,
            building_age,
        ) = self.get_building_properties_to_check()

        pv_share = self.get_pv_properties_to_check()

        return building_type, building_refurbishment_state, building_age, pv_share

    def get_building_properties_to_check(self):
        """Get building properties."""

        # system_setups for scenarios to filter
        building_type = [
            "DE.N.SFH",
            "DE.N.TH",
            "DE.N.MFH",
            "DE.N.AB",
        ]

        building_refurbishment_state = [
            "001.001",
            "001.002",
            "001.003",
        ]

        building_age = [
            "01.Gen",
            "02.Gen",
            "03.Gen",
            "04.Gen",
            "05.Gen",
            "06.Gen",
            "07.Gen",
            "08.Gen",
            "09.Gen",
            "10.Gen",
            "11.Gen",
            "12.Gen",
        ]

        return building_type, building_refurbishment_state, building_age

    def get_pv_properties_to_check(self):
        """Get pv properties."""

        # system_setups for scenarios to filter
        pv_share = [0, 0.25, 0.5, 1]

        return pv_share
