"""Result Data Processing and Plotting for Scenario Comparison."""


import os
from enum import Enum
from dataclasses import dataclass
from typing import Dict, Tuple, Optional, List
import pandas as pd
from ordered_set import OrderedSet
from hisim import log


class ScenarioDataProcessing:

    """ScenarioDataProcessing class."""

    @staticmethod
    def get_dataframe_and_create_pandas_dataframe_for_all_data(
        filepath_of_aggregated_dataframe: str,
        dict_of_scenarios_to_check: Optional[Dict[str, List[str]]],
        variables_to_check: List[str],
        data_format_type: str,
    ) -> Tuple[pd.DataFrame, List[str]]:
        """Get csv data and create dataframes with the filtered and procesed scenario data."""

        if not os.path.isfile(filepath_of_aggregated_dataframe):
            raise FileExistsError(f"The file {filepath_of_aggregated_dataframe} could not be found.")
        log.information(f"Read aggregated dataframe with all HiSim results from {filepath_of_aggregated_dataframe}.")

        if data_format_type == DataFormatEnum.CSV.name:
            file_df = pd.read_csv(filepath_or_buffer=filepath_of_aggregated_dataframe, header=[0, 1])
        elif data_format_type == DataFormatEnum.XLSX.name:
            file_df = pd.read_excel(filepath_of_aggregated_dataframe, header=[0, 1], index_col=0, sheet_name="Sheet1")
        else:
            raise ValueError(f"Only data format types xlsx or csv are implemented. Here it is {data_format_type}.")

        if dict_of_scenarios_to_check is not None and dict_of_scenarios_to_check != {}:

            file_df = ScenarioDataProcessing.check_if_scenario_exists_and_filter_dataframe_for_scenarios_dict(
                data_frame=file_df, dict_of_scenarios_to_check=dict_of_scenarios_to_check,
            )

        return (
            file_df,
            variables_to_check,
        )

    @staticmethod
    def filter_pandas_dataframe_according_to_output_variable(
        dataframe: pd.DataFrame, variable_to_check: str
    ) -> pd.DataFrame:
        """Filter pandas dataframe according to variable."""

        filtered_dataframe = dataframe.loc[dataframe[("Output", "variable")] == variable_to_check]
        if filtered_dataframe.empty:
            print("The dataframe contains the following variables: ", set(list(dataframe[("Output", "variable")])))
            print(
                f"The filtered dataframe is empty. The dataframe did not contain the variable {variable_to_check}. Check the list above."
            )
        return filtered_dataframe

    @staticmethod
    def get_statistics_of_data_and_write_to_excel(
        filtered_data: pd.DataFrame, x_and_y_plot_data: pd.DataFrame, path_to_save: str, kind_of_data_set: str,
    ) -> None:
        """Use pandas describe method to get statistical values of certain data."""

        # create a excel writer object
        with pd.ExcelWriter(  # pylint: disable=abstract-class-instantiated
            path=os.path.join(path_to_save, f"{kind_of_data_set}_stats.xlsx"), mode="w",
        ) as writer:
            # statistical data
            statistical_data = filtered_data.describe()
            statistical_data.to_excel(excel_writer=writer, sheet_name="statistics")
            # write also x and y data which is plotted
            x_and_y_plot_data.to_excel(excel_writer=writer, sheet_name="plotted mean data")

    @staticmethod
    def aggregate_all_values_for_one_scenario_and_rename(
        dataframe: pd.DataFrame, list_of_scenarios_to_check: List, column_name_to_check: str,
    ) -> pd.DataFrame:
        """Check for one scenario."""

        # Create a filtered DataFrame for each scenario and store in a list
        filtered_dfs = []
        # Iterate through each scenario in the list
        for scenario_to_check in list_of_scenarios_to_check:
            log.information("Filtering dataframe for scenario: " + scenario_to_check)

            if isinstance(scenario_to_check, str):
                # Filter DataFrame rows where the specified column contains the scenario string
                filtered_df = dataframe[
                    dataframe[("Input", column_name_to_check)].str.contains(scenario_to_check, na=False)
                ]
            else:
                # Filter DataFrame rows where the specified column matches the scenario value (for numeric scenarios)
                filtered_df = dataframe[dataframe[("Input", column_name_to_check)] == scenario_to_check]

            # Rename scenarios according to scenario_to_check
            filtered_df[("Input", "scenario")] = scenario_to_check

            # Add the filtered DataFrame to the list
            filtered_dfs.append(filtered_df)

        # Concatenate all filtered DataFrames
        concat_df = pd.concat(filtered_dfs, ignore_index=True)

        return concat_df

    @staticmethod
    def check_if_scenario_exists_and_filter_dataframe_for_scenarios_dict(
        data_frame: pd.DataFrame, dict_of_scenarios_to_check: Dict[str, List[str]],
    ) -> pd.DataFrame:
        """Check if scenario exists and filter dataframe for scenario."""

        concat_df = data_frame
        filter_level_index = 0
        # Iterate over the dictionary of scenarios
        for scenario_to_check_key, list_of_scenarios_to_check in dict_of_scenarios_to_check.items():
            log.information(
                f"Scenarios to check are {list_of_scenarios_to_check} in Input column {scenario_to_check_key}."
            )
            concat_df = ScenarioDataProcessing.aggregate_all_values_for_one_scenario_and_rename(
                dataframe=concat_df,
                list_of_scenarios_to_check=list_of_scenarios_to_check,
                column_name_to_check=scenario_to_check_key,
            )
            filter_level_index += 1

        if filter_level_index > 1:
            raise ValueError(
                f"Filter level index is {filter_level_index} but should be 1 at max."
                " This means your dict_of_scenarios_to_check should only have one scenario_key to filter."
            )

        return concat_df

    @staticmethod
    def take_mean_values_of_scenarios(
        filtered_data: pd.DataFrame, time_resolution_of_data_set: str
    ) -> Tuple[pd.DataFrame, List[str]]:
        """Get mean values of scenarios."""

        # Dictionary to store the x and y data for plotting
        dict_with_x_and_y_data: Dict[str, List] = {}

        # Get output keys where output values are stored, excluding 'variable' and 'unit'
        output_keys = [key for key in filtered_data["Output"].columns if key not in ("variable", "unit")]

        # The x-axis data is the time or category we are averaging over
        x_data = output_keys

        # Determine if we should take mean values over time
        if len(x_data) == 0:
            raise ValueError(
                f"x data length is 0. This means no keys for output values could be found in the columns of filtered data: {filtered_data.columns}"
            )

        if time_resolution_of_data_set == ResultDataTypeEnum.YEARLY.name and len(x_data) == 1:
            take_mean_time_values_bool = False
        elif time_resolution_of_data_set != ResultDataTypeEnum.YEARLY.name and len(x_data) > 1:
            take_mean_time_values_bool = True
        else:
            raise ValueError("Invalid time resolution and x_data length combination. Check your data and parameters.")

        # Add x_data as a column to the dictionary
        dict_with_x_and_y_data["time"] = x_data

        # Iterate over each unique scenario in the filtered data
        for scenario in OrderedSet(filtered_data[("Input", "scenario")]):
            filtered_data_per_scenario = filtered_data[filtered_data[("Input", "scenario")] == scenario]

            if take_mean_time_values_bool:
                # Compute mean values for each time step
                mean_values = filtered_data_per_scenario["Output"].loc[:, x_data].mean().values
                dict_with_x_and_y_data[f"{scenario}"] = mean_values
            else:
                # Compute the mean value for yearly data
                mean_value = filtered_data_per_scenario[("Output", x_data[0])].mean()
                dict_with_x_and_y_data[f"{scenario}"] = [mean_value]

        # Convert the dictionary to a DataFrame for plotting
        x_and_y_plot_data = pd.DataFrame(dict_with_x_and_y_data)

        return x_and_y_plot_data, output_keys


class ResultDataTypeEnum(Enum):

    """ResultDataTypeEnum class.

    Here it is defined what kind of data you want to collect.
    """

    HOURLY = "hourly"  # hourly not working yet
    DAILY = "daily"
    MONTHLY = "monthly"
    YEARLY = "yearly"


class ResultDataProcessingModeEnum(Enum):

    """ResultDataProcessingModeEnum class.

    Here it is defined what kind of data processing you want to make.
    """

    PROCESS_ALL_DATA = 1
    PROCESS_FOR_DIFFERENT_BUILDING_CODES = 2


class DataFormatEnum(Enum):
    """Class for choosign data format."""

    XLSX = 1
    CSV = 2


@dataclass
class DataInfo:

    """Data info class is an object for storing data in lists of strings."""

    descriptions: List[str]


class OutputVariableEnumClass(Enum):

    """Output variable enum class is for determining variables which will be checked and plotted."""

    KPI_DATA = DataInfo(
        descriptions=[
            "Total electricity consumption",
            "PV production",
            # "Ratio between PV production and total consumption",
            # "Self-consumption",
            # "Self-consumption rate",
            # "Autarky rate",
            "Total energy from grid",
            "Total energy to grid",
            # "Relative electricity demand from grid",
            # "Self-consumption rate according to solar htw berlin",
            "Autarky rate according to solar htw berlin",
            "Investment costs for equipment per simulated period",
            "CO2 footprint for equipment per simulated period",
            "System operational costs for simulated period",
            "System operational emissions for simulated period",
            "Total costs for simulated period",
            "Total CO2 emissions for simulated period",
            # "Temperature deviation of building indoor air temperature being below set temperature 20.0 Celsius",
            # "Minimum building indoor air temperature reached",
            # "Temperature deviation of building indoor air temperature being above set temperature 25.0 Celsius",
            # "Maximum building indoor air temperature reached",
            # "Building heating load",
            "Conditioned floor area",
            # "Rooftop area",
            # "Specific heating load",
            "Specific heating demand according to TABULA",
            # "Thermal output energy of heat distribution system",
            # "Number of SH heat pump cycles",
            # "Seasonal performance factor of SH heat pump",
            # "Seasonal energy efficiency ratio of SH heat pump",
            # "Heating hours of SH heat pump",
            # "Cooling hours of SH heat pump",
            # "Max flow temperature of SH heat pump",
            # "Max return temperature of SH heat pump",
            # "Max temperature difference of SH heat pump",
            # "Min flow temperature of SH heat pump",
            # "Min return temperature of SH heat pump",
            # "Min temperature difference of SH heat pump",
            "Mean flow temperature of SH heat pump",
            "Mean return temperature of SH heat pump",
            # "Heating output energy of SH heat pump",
            # "Cooling output energy of SH heat pump",
            # "Specific heating energy of SH heat pump",
            # "Electrical input energy for heating of SH heat pump",
            # "Electrical input energy for cooling of SH heat pump",
            "Total electrical input energy of SH heat pump",
            "Space heating heat pump electricity from grid",
            # "Relative electricity demand of SH heat pump",
            "DHW heat pump total electricity consumption",
            "Domestic hot water heat pump electricity from grid",
            # "Heating output energy of DHW heat pump",
            # "Relative electricity demand of DHW heat pump",
            "Residents' total electricity consumption",
            "Residents' electricity consumption from grid",
            # "Relative electricity demand of residents",
            # "Battery charging energy",
            # "Battery discharging energy",
            # "Battery losses",
        ]
    )
    ELECTRICITY_DATA = DataInfo(
        descriptions=[
            # "ElectricityMeter|Electricity|ElectricityToGrid",
            # "ElectricityMeter|Electricity|ElectricityFromGrid",
            "ElectricityMeter|Electricity|ElectricityToAndFromGrid",
            # "ElectricityMeter|Electricity|ElectricityConsumption",
            "UTSPConnector|Electricity|ElectricityOutput",
            "PVSystem_w0|Electricity|ElectricityOutput",
            "AdvancedHeatPumpHPLib|Electricity|ElectricalInputPower",
            "DHWHeatPump_w1|Electricity|ElectricityOutput",
            "ElectricCar_1_w1|Electricity|ElectricityOutput",
            "ElectricCar_2_w1|Electricity|ElectricityOutput",
            "ElectricCar_3_w1|Electricity|ElectricityOutput",
            "ElectricCar_4_w1|Electricity|ElectricityOutput",
            "ElectricCar_5_w1|Electricity|ElectricityOutput",
            "ElectricCar_6_w1|Electricity|ElectricityOutput",
            "ElectricCar_7_w1|Electricity|ElectricityOutput",
            "ElectricCar_8_w1|Electricity|ElectricityOutput",
            "ElectricCar_9_w1|Electricity|ElectricityOutput",
            "ElectricCar_10_w1|Electricity|ElectricityOutput",
            "ElectricCar_11_w1|Electricity|ElectricityOutput",
            "ElectricCar_12_w1|Electricity|ElectricityOutput",
        ]
    )
    FLOW_AND_RETURN_TEMPERTAURES = DataInfo(
        descriptions=[
            "AdvancedHeatPumpHPLib|Heating|TemperatureOutput",
            "SimpleHotWaterStorage|Water|WaterTemperatureToHeatGenerator",
            "SimpleHotWaterStorage|Water|WaterTemperatureToHeatDistribution",
            "HeatDistributionSystem|Water|WaterTemperatureOutput",
            "Weather|Temperature|DailyAverageOutsideTemperatures",
        ]
    )
    OCCUPANCY_CONSUMPTION = DataInfo(
        descriptions=["Occupancy|Electricity|ElectricityOutput", "Occupancy|WarmWater|WaterConsumption"]
    )
    HEATING_DEMAND = DataInfo(
        descriptions=["AdvancedHeatPumpHPLib|Heating|ThermalOutputPower", "Building|Temperature|TemperatureIndoorAir"]
    )
    VARIABLES_FOR_DEBUGGING_PURPOSES = DataInfo(
        descriptions=[
            "AdvancedHeatPumpHPLib|Heating|ThermalOutputPower",
            "AdvancedHeatPumpHPLib|Electricity|ElectricalInputPowerForHeating",
            "AdvancedHeatPumpHPLib|Electricity|ElectricalInputPowerForCooling",
            "Building|Temperature|TemperatureIndoorAir",
            "AdvancedHeatPumpHPLib|Any|COP",
            "Weather|Temperature|DailyAverageOutsideTemperatures",
            "HeatDistributionController|Temperature|HeatingFlowTemperature",
        ]
    )


class ScenarioAggregationEnumClass(Enum):

    """Scenario Aggregation Enum class is for deciding according to which scenarios the data will be aggregated."""

    BUILDING_TYPE = DataInfo(descriptions=["SFH", "TH", "MFH", "AB"])
    BUILDING_TYPE_AND_AGE = DataInfo(
        descriptions=[
            # "DE.N.TH.10",
            # "DE.N.TH.09",
            # "DE.N.TH.08",
            # "DE.N.TH.07",
            # "DE.N.TH.06",
            # "DE.N.TH.05",
            # "DE.N.TH.04",
            # "DE.N.TH.03",
            # "DE.N.TH.02",
            # "DE.N.SFH.10",
            # "DE.N.SFH.09",
            # "DE.N.SFH.08",
            # "DE.N.SFH.07",
            # "DE.N.SFH.06",
            # "DE.N.SFH.05",
            # "DE.N.SFH.04",
            # "DE.N.SFH.03",
            # "DE.N.SFH.02",
            # "DE.N.SFH.01",
            "DE.N.MFH.10",
            "DE.N.MFH.09",
            "DE.N.MFH.08",
            "DE.N.MFH.07",
            "DE.N.MFH.06",
            "DE.N.MFH.05",
            "DE.N.MFH.04",
            "DE.N.MFH.03",
            "DE.N.MFH.02",
            "DE.N.MFH.01",
            "DE.N.AB.06",
            "DE.N.AB.05",
            "DE.N.AB.04",
            "DE.N.AB.03",
            "DE.N.AB.02",
            "DE.East.MFH.05",
            "DE.East.MFH.04",
            "DE.East.AB.08",
            "DE.East.AB.07",
            "DE.East.AB.06",
        ]
    )
    BUILDING_REFURBISHMENT_STATE = DataInfo(descriptions=["001.001", "001.002", "001.003"])

    BUILDING_AGE = DataInfo(
        descriptions=[
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
    )

    def return_enum_values_as_dict(self) -> Dict[str, List[str]]:
        """Return enum values as dict."""
        dict_to_return: Dict[str, List[str]] = {"building_code": self.value.descriptions}
        return dict_to_return
