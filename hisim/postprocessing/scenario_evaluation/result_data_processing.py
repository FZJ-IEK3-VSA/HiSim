"""Result Data Processing and Plotting for Scenario Comparison."""


import glob
import os
from typing import Dict, Any, Tuple, Optional, List
import pandas as pd
from ordered_set import OrderedSet
from hisim.postprocessing.scenario_evaluation.result_data_collection import ResultDataTypeEnum
from hisim import log


class ScenarioDataProcessing:

    """ScenarioDataProcessing class."""

    @staticmethod
    def get_dataframe_and_create_pandas_dataframe_for_all_data(
        data_folder_path: str,
        time_resolution_of_data_set: Any,
        dict_of_scenarios_to_check: Optional[Dict[str, List[str]]],
        variables_to_check: List[str],
        xlsx_or_csv: str = "csv",
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
        log.information(f"Read csv or excel files and create one big dataframe for {kind_of_data_set} data.")

        data_file_path = os.path.join(data_folder_path, f"*{kind_of_data_set}*.{xlsx_or_csv}")
        list_of_possible_data_files = glob.glob(data_file_path)
        file: str = ""
        if not list_of_possible_data_files:
            raise FileExistsError(f"No file could be found in this path {data_file_path}.")
        if len(list_of_possible_data_files) > 1:
            raise ValueError(f"The file path {data_file_path} should not contain more than one file.")

        file = list_of_possible_data_files[0]
        if xlsx_or_csv == "csv":
            file_df = pd.read_csv(filepath_or_buffer=file, sep=",")
        else:
            file_df = pd.read_excel(file, header=[0, 1], index_col=0, sheet_name="Sheet1")

        # if scenario values are no strings, transform them
        # file_df["scenario"] = file_df["scenario"].transform(str)
        key_for_scenario_one = ""
        key_for_current_scenario = ""

        if dict_of_scenarios_to_check is not None and dict_of_scenarios_to_check != {}:
            (
                file_df,
                key_for_scenario_one,
                key_for_current_scenario,
            ) = ScenarioDataProcessing.check_if_scenario_exists_and_filter_dataframe_for_scenarios_dict(
                data_frame=file_df, dict_of_scenarios_to_check=dict_of_scenarios_to_check,
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

        filtered_dataframe = dataframe.loc[dataframe[("Output", "variable")] == variable_to_check]
        if filtered_dataframe.empty:
            print("The dataframe contains the following variables: ", set(list(dataframe[("Output", "variable")])))
            # raise ValueError(
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
            filtered_data.to_excel(excel_writer=writer, sheet_name="filtered data")
            statistical_data = filtered_data.describe()

            statistical_data.to_excel(excel_writer=writer, sheet_name="statistics")
            # write also x and y data which is plotted
            x_and_y_plot_data.to_excel(excel_writer=writer, sheet_name="plotted mean data")

    @staticmethod
    def aggregate_all_values_for_one_scenario(
        dataframe: pd.DataFrame, list_of_scenarios_to_check: List, column_name_to_check: str,
    ) -> pd.DataFrame:
        """Check for one scenario."""

        # Create a filtered DataFrame for each scenario and store in a list
        filtered_dfs = []
        # Iterate through each scenario in the list
        for scenario_to_check in list_of_scenarios_to_check:
            print("scenario to check", scenario_to_check)

            if isinstance(scenario_to_check, str):
                # Filter DataFrame rows where the specified column contains the scenario string
                filtered_df = dataframe[
                    dataframe[("Input", column_name_to_check)].str.contains(scenario_to_check, na=False)
                ]
            else:
                # Filter DataFrame rows where the specified column matches the scenario value (for numeric scenarios)
                filtered_df = dataframe[dataframe[("Input", column_name_to_check)] == scenario_to_check]

            # Add the filtered DataFrame to the list
            filtered_dfs.append(filtered_df)

        # Concatenate all filtered DataFrames
        concat_df = pd.concat(filtered_dfs, ignore_index=True)

        return concat_df

    @staticmethod
    def check_if_scenario_exists_and_filter_dataframe_for_scenarios_dict(
        data_frame: pd.DataFrame, dict_of_scenarios_to_check: Dict[str, List[str]],
    ) -> Tuple[pd.DataFrame, str, str]:
        """Check if scenario exists and filter dataframe for scenario."""

        # Assuming ScenarioDataProcessing.aggregate_all_values_for_one_scenario is optimized
        concat_df = data_frame
        filter_level_index = 0
        # Iterate over the dictionary of scenarios
        for scenario_to_check_key, list_of_scenarios_to_check in dict_of_scenarios_to_check.items():
            print("scenario to check key", scenario_to_check_key)
            concat_df = ScenarioDataProcessing.aggregate_all_values_for_one_scenario(
                dataframe=concat_df,
                list_of_scenarios_to_check=list_of_scenarios_to_check,
                column_name_to_check=scenario_to_check_key,
            )
            filter_level_index += 1

        # Extract scenario keys
        keys_of_scenarios = list(dict_of_scenarios_to_check.keys())
        # filter index is 1
        key_for_scenario_one = keys_of_scenarios[0] if keys_of_scenarios else ""
        # filter index is 2
        key_for_current_scenario = keys_of_scenarios[1] if len(keys_of_scenarios) > 1 else ""

        # Vectorized renaming of the scenario column
        if filter_level_index == 2:
            concat_df["scenario"] = concat_df.apply(lambda row: f"{row['scenario_1']}_{row['scenario']}", axis=1)

        return concat_df, key_for_scenario_one, key_for_current_scenario

    @staticmethod
    def take_mean_values_of_scenarios(
        filtered_data: pd.DataFrame, time_resolution_of_data_set: ResultDataTypeEnum
    ) -> Tuple[pd.DataFrame, List[str]]:
        """Get mean values of scenarios."""

        # Dictionary to store the x and y data for plotting
        dict_with_x_and_y_data: Dict[str, List] = {}

        # Get output keys where output values are stored, excluding 'variable' and 'unit'
        output_keys = [key for key in filtered_data["Output"].columns if key not in ("variable", "unit")]

        # The x-axis data is the time or category we are averaging over
        x_data = output_keys

        # Determine if we should take mean values over time
        if time_resolution_of_data_set == ResultDataTypeEnum.YEARLY and len(x_data) == 1:
            take_mean_time_values_bool = False
        elif time_resolution_of_data_set != ResultDataTypeEnum.YEARLY and len(x_data) > 1:
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
            self.flow_and_return_temperatures,
        ) = self.get_variables_to_check()
        (
            self.building_type,
            self.building_refurbishment_state,
            self.building_age,
            self.building_type_and_age,
            self.pv_share,
        ) = self.get_scenarios_to_check()

    def get_variables_to_check(self):
        """Get specific variables to check for the scenario evaluation."""

        # system_setups for variables to check (check names of your variables before your evaluation, if they are correct)
        # kpi data has no time series, so only choose when you analyze yearly data
        kpi_data = [
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

        electricity_data = [
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

        flow_and_return_temperatures = [
            "AdvancedHeatPumpHPLib|Heating|TemperatureOutput",
            "SimpleHotWaterStorage|Water|WaterTemperatureToHeatGenerator",
            "SimpleHotWaterStorage|Water|WaterTemperatureToHeatDistribution",
            "HeatDistributionSystem|Water|WaterTemperatureOutput",
            "Weather|Temperature|DailyAverageOutsideTemperatures",
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
            "AdvancedHeatPumpHPLib|Electricity|ElectricalInputPowerForHeating",
            "AdvancedHeatPumpHPLib|Electricity|ElectricalInputPowerForCooling",
            "Building|Temperature|TemperatureIndoorAir",
            "AdvancedHeatPumpHPLib|Any|COP",
            "Weather|Temperature|DailyAverageOutsideTemperatures",
            "HeatDistributionController|Temperature|HeatingFlowTemperature",
        ]

        return (
            kpi_data,
            electricity_data,
            occuancy_consumption,
            heating_demand,
            variables_for_debugging_purposes,
            flow_and_return_temperatures,
        )

    def get_scenarios_to_check(self):
        """Get scenarios to check for scenario evaluation."""

        (
            building_type,
            building_refurbishment_state,
            building_age,
            building_type_and_age,
        ) = self.get_building_properties_to_check()

        pv_share = self.get_pv_properties_to_check()

        return building_type, building_refurbishment_state, building_age, building_type_and_age, pv_share

    def get_building_properties_to_check(self):
        """Get building properties."""

        # system_setups for scenarios to filter
        building_type = [
            "SFH",
            "TH",
            "MFH",
            "AB",
        ]

        building_type_and_age = [
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

        return building_type, building_refurbishment_state, building_age, building_type_and_age

    def get_pv_properties_to_check(self):
        """Get pv properties."""

        # system_setups for scenarios to filter
        pv_share = [0, 0.25, 0.5, 1]

        return pv_share
