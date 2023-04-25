"""Scenario Comparison with Pyam."""
import pandas as pd
import glob
import os
import re
import string
from pathlib import Path
import pyam
import enum
from hisim.postprocessing.chartbase import Chart
import matplotlib.pyplot as plt

class PyAmChartGenerator():#Chart):
    
    # def __init__(self, output, component_name, output_description, chart_type, units, directory_path, time_correction_factor, output2=None, figure_format=None):
    #     super().__init__(output, component_name, output_description, chart_type, units, directory_path, time_correction_factor, output2, figure_format)
    def __init__(self) -> None:
        self.folder_path = "..\\..\\examples\\results_for_scenario_comparison\\**\\*ElectricityOutput*"
        self.result_folder = "..\\..\\examples\\results_for_scenario_comparison\\results"
        #self.df_thermal_power_delivered = pd.read_csv(self.folder_path + "/ThermalPowerDelivered_HeatPump.csv")
        list_of_csv_data_for_one_variable = self.import_data_from_file(folder_path=self.folder_path)
        pyam_dataframe = self.read_csv_and_generate_pyam_dataframe(list_of_csv_to_read=list_of_csv_data_for_one_variable)
        self.make_line_plot_for_pyam_dataframe(dataframe=pyam_dataframe)
        #self.make_bar_plot_for_pyam_dataframe(dataframe=pyam_dataframe)
        #self.make_box_plot_for_pyam_dataframe(dataframe=pyam_dataframe)
        self.make_stack_plot_for_pyam_dataframe(dataframe=pyam_dataframe)
        self.make_pie_plot_for_pyam_dataframe(dataframe=pyam_dataframe)
        self.make_scatter_plot_for_pyam_dataframe(dataframe=pyam_dataframe)
        self.make_sankey_plot_for_pyam_dataframe(dataframe=pyam_dataframe)
        
        
        
    def import_data_from_file(self, folder_path: str):
        # get results csv from all folders for one variable
        list_of_timeseries_of_one_variable = []
        for file in glob.glob(folder_path, recursive=True):
            #print(file)
            if file.endswith(".csv") and "monthly" not in file:
                list_of_timeseries_of_one_variable.append(file)
        return list_of_timeseries_of_one_variable
    
    def read_csv_and_generate_pyam_dataframe(self, list_of_csv_to_read: list[str]):
        
        simple_dict = {"model": [], "scenario": [], "region":[], "variable": [], "unit": []}
        for csv_file in list_of_csv_to_read:
            scenario = csv_file.split(sep="\\")[-2]
            dataframe = pd.read_csv(csv_file)
            timeseries = dataframe.values[:,0]
            values = dataframe.values[:,1]
            column_name = str(''.join([x for x in dataframe.columns[1] if x in string.ascii_letters + '\'- '])).split(sep=' ')
            variable_name = column_name[2]
            subcategory_name = column_name[0]
            loadtype = column_name[3]
            unit = column_name[5]
            simple_dict["model"].append("HiSim")
            simple_dict["scenario"].append(scenario)
            simple_dict["region"].append("Aachen")
            simple_dict["variable"].append(variable_name + "|" + subcategory_name)
            simple_dict["unit"].append(unit)
            for index, timestep in enumerate(timeseries):
                simple_dict[f"{timestep}"]=values[index]
        
        simple_df = pd.DataFrame(simple_dict)   
        df_pyam = pyam.IamDataFrame(data=simple_df)
        return df_pyam
        
    def make_line_plot_for_pyam_dataframe(self, dataframe: pyam.IamDataFrame):
        data = dataframe
        fig, ax = plt.subplots(figsize=(12,10))
        
        data.plot.line(ax=ax, color="scenario", title="Electricity Output [W]")
        fig.show()
        fig.savefig(self.result_folder + "\\line_plot.png")


    def make_bar_plot_for_pyam_dataframe(self, dataframe: pyam.IamDataFrame):
        data = dataframe
        fig, ax = plt.subplots(figsize=(12,10))
        
        data.plot.bar(ax=ax, color="scenario", title="Electricity Output [W]")
        fig.show()
        fig.savefig(self.result_folder + "\\bar_plot.png")
        
        
    def make_stack_plot_for_pyam_dataframe(self, dataframe: pyam.IamDataFrame):
        data = dataframe
        fig, ax = plt.subplots(figsize=(12,10))
        
        data.plot.stack(ax=ax, color="scenario", title="Electricity Output [W]")
        fig.show()
        fig.savefig(self.result_folder + "\\stack_plot.png")

    def make_box_plot_for_pyam_dataframe(self, dataframe: pyam.IamDataFrame):
        data = dataframe
        fig, ax = plt.subplots(figsize=(12,10))
        
        data.plot.box(ax=ax, color="scenario", title="Electricity Output [W]")
        fig.show()
        fig.savefig(self.result_folder + "\\box_plot.png")

    def make_pie_plot_for_pyam_dataframe(self, dataframe: pyam.IamDataFrame):
        data = dataframe
        fig, ax = plt.subplots(figsize=(12,10))
        
        data.plot.pie(ax=ax, color="scenario", title="Electricity Output [W]")
        fig.show()
        fig.savefig(self.result_folder + "\\pie_plot.png")

    def make_scatter_plot_for_pyam_dataframe(self, dataframe: pyam.IamDataFrame):
        data = dataframe
        fig, ax = plt.subplots(figsize=(12,10))
        
        data.plot.scatter(ax=ax, color="scenario", title="Electricity Output [W]")
        fig.show()
        fig.savefig(self.result_folder + "\\scatter_plot.png")

    def make_sankey_plot_for_pyam_dataframe(self, dataframe: pyam.IamDataFrame):
        data = dataframe
        fig, ax = plt.subplots(figsize=(12,10))
        
        data.plot.sankey(ax=ax, color="scenario", title="Electricity Output [W]")
        fig.show()
        fig.savefig(self.result_folder + "\\sankey_plot.png")







class VariableEnum(enum.Enum):
    
    ThermalPowerDelivered = 1
    ElectricityOutput = 2       

def main():
    pyamgenerator = PyAmChartGenerator()
    #print(pyamgenerator.df_thermal_power_delivered)
    
if __name__=="__main__":
    main()
    
    
    