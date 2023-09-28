import os
import pandas as pd
import numpy as np
import sys
import subprocess
import tkinter.filedialog as filedialog

# Owned
import hisim.log as log
from hisim import utils
from hisim.utils import PostProcessingOptions
from hisim import loadtypes as lt
import hisim.postprocessing.charts as charts
from hisim.postprocessing.chart_singleday import ChartSingleDay
import hisim.postprocessing.report as report
from hisim.simulationparameters import SimulationParameters
import warnings

class PostProcessingDataTransfer:
    def __init__(self,
                  time_correction_factor,
                  directory_path,
                  results,
                  all_outputs,
                  simulation_parameters: SimulationParameters,
                  wrapped_components,
                  story,
                  mode,
                  setup_function,
                  execution_time,
                  results_monthly,
                  ):
        self.time_correction_factor =  time_correction_factor
        self.directory_path = directory_path
        self.results = results
        self.all_outputs = all_outputs
        self.simulation_parameters = simulation_parameters
        self.wrapped_components = wrapped_components
        self.story = story
        self.mode = mode
        self.setup_function = setup_function
        self.execution_time = execution_time
        self.results_monthly = results_monthly
        self.postProcessingOptions = simulation_parameters.post_processing_options
        log.information("selected " + str(len(self.postProcessingOptions)) + " options")
        for option in self.postProcessingOptions:
            log.information("selected: " + str(option))



class PostProcessor:
    @utils.measure_execution_time
    def __init__(self, ppdt: PostProcessingDataTransfer):
        self.ppdt = ppdt
        if ppdt is None:
            raise Exception("PPDT was none")
        #self.resultsdir = resultsdir
        #self.all_outputs = all_outputs
        #self.time_correction_factor = time_correction_factor
        #self.dirname = None
        # self.flags = {"plot_line": plot_line,
        #               "plot_carpet": plot_carpet,
        #               "plot_sankey": plot_sankey,
        #               "plot_day": plot_day,
        #               "plot_bar": plot_bar,
        #               "open_dir": open_dir,
        #               "export_results_to_CSV": export_results_to_CSV}
        self.report = report.Report(setup_function=self.ppdt.setup_function, dirpath=self.ppdt.directory_path)
        #self.cal_pos_sim()

    def set_dir_results(self, dirname=None):
        if dirname is None:
            dirname = filedialog.askdirectory(initialdir=utils.HISIMPATH["results"])
        self.dirname = dirname

    @utils.measure_execution_time
    def plot_sankeys(self):
        for i_display_name in [name for name, display_name in lt.DisplayNames.__members__.items()]:
            my_sankey = charts.SankeyHISIM(name=i_display_name,
                                    data=self.ppdt.all_outputs,
                                    units=lt.Units.Any,
                                    directorypath=self.ppdt.directory_path,
                                    time_correction_factor=self.ppdt.time_correction_factor)
            my_sankey.plot()
        if any(component_output.ObjectName == "HeatPump" for component_output in self.ppdt.all_outputs):
            my_sankey = charts.SankeyHISIM(name="HeatPump",
                                    data=self.ppdt.all_outputs,
                                    units=lt.Units.Any,
                                    directorypath=self.ppdt.directory_path,
                                    time_correction_factor=self.ppdt.time_correction_factor)
            my_sankey.plot_heat_pump()
        if any(component_output.ObjectName == "Building" for component_output in self.ppdt.all_outputs):
            my_sankey = charts.SankeyHISIM(name="Building",
                                    data=self.ppdt.all_outputs,
                                    units=lt.Units.Any,
                                    directorypath=self.ppdt.directory_path,
                                    time_correction_factor=self.ppdt.time_correction_factor)
            my_sankey.plot_building()

    @utils.measure_execution_time
    def run(self):
        # Define the directory name
        warnings.filterwarnings("ignore")


        days={"month":0,
              "day":0}
        #if len(self.results) == 60 * 24 * 365:
        for index, output in enumerate(self.ppdt.all_outputs):
            if PostProcessingOptions.Plot_Line in  self.ppdt.postProcessingOptions:
                my_line = charts.Line(output=output.FullName,
                               data=self.ppdt.results.iloc[:, index],
                               units=output.Unit,
                               directorypath=self.ppdt.directory_path,
                               time_correction_factor=self.ppdt .time_correction_factor)
                my_line.plot()
            if PostProcessingOptions.Plot_Carpet in self.ppdt.postProcessingOptions:
                #log.information("Making carpet plots")
                my_carpet = charts.Carpet(output=output.FullName,
                                   data=self.ppdt.results.iloc[:, index],
                                   units=output.Unit,
                                   directorypath=self.ppdt.directory_path,
                                   time_correction_factor=self.ppdt.time_correction_factor)
                my_carpet.plot( xdims = int( ( self.ppdt.simulation_parameters.end_date - self.ppdt.simulation_parameters.start_date ).days ) )

            if PostProcessingOptions.Plot_Day in self.ppdt.postProcessingOptions:
                    my_days = ChartSingleDay(output=output.FullName,
                                   data=self.ppdt.results.iloc[:, index],
                                   units=output.Unit,
                                   directorypath=self.ppdt.directory_path,
                                   time_correction_factor=self.ppdt.time_correction_factor,
                                   day=days["day"],
                                   month=days["month"])
                    my_days.plot()
            if PostProcessingOptions.Plot_Bar in self.ppdt.postProcessingOptions:
                my_bar = charts.Bar(output=output.FullName,
                             data=self.ppdt.results_monthly.iloc[:, index],
                             units=output.Unit,
                             dirpath=self.ppdt.directory_path,
                             time_correction_factor=self.ppdt.time_correction_factor)
                my_bar.plot()


        # Plot sankey
        if PostProcessingOptions.Plot_Sankey in self.ppdt.postProcessingOptions:
            log.information("plotting sankeys")
        #    self.plot_sankeys()
        else:
            for option in self.ppdt.postProcessingOptions:
                log.information("in sankey: selected: " + str(option))
            log.information("not plotting sankeys")

        # Export all results to CSV
        if PostProcessingOptions.Export_To_CSV in self.ppdt.postProcessingOptions:
            log.information("exporting to csv")
            self.export_results_to_csv()
        else:
            log.information("not exporting to CSV")
            
         # Calculate KPIs and save as CSV
        if PostProcessingOptions.Compute_KPI in self.ppdt.postProcessingOptions:
            log.information("Computing KPIs")
            self.compute_KPIs( )
        else:
            log.information("not calculating KPIs")


        if len(self.ppdt.results) == 1440:
            for index, output in enumerate(self.ppdt.all_outputs):
                if output.FullName == "Dummy # Residence Temperature":
                    my_days = ChartSingleDay(output=output.FullName,
                                   data=self.ppdt.results.iloc[:, index],
                                   units=output.Unit,
                                   directorypath=self.ppdt.directory_path,
                                   time_correction_factor=self.ppdt.time_correction_factor,
                                   day=0,
                                   month=0,
                                   output2=self.ppdt.results.iloc[:, 11])
                else:
                    my_days = ChartSingleDay(output=output.FullName,
                                   data=self.ppdt   .results.iloc[:, index],
                                   units=output.Unit,
                                   directorypath=self.ppdt.directory_path,
                                   time_correction_factor=self.ppdt.time_correction_factor,
                                   day=0,
                                   month=0)
                my_days.plot()

        # Open file explorer
        if PostProcessingOptions.Open_Directory in self.ppdt.postProcessingOptions:
            self.open_dir_in_file_explorer()

    @utils.measure_execution_time
    def export_results_to_csv(self):
        for column in self.ppdt.results:
            print(column.split(' ',3))
            self.ppdt.results[column].to_csv(os.path.join(self.ppdt.directory_path,
                                                     "{}_{}.csv".format(column.split(' ', 3)[2],
                                                                        column.split(' ', 3)[0])),
                                        sep=",", decimal=".")
        for column in self.ppdt.results_monthly:
            self.ppdt.results_monthly[column].to_csv(os.path.join(self.ppdt.directory_path,
                                                       "{}_{}_monthly.csv".format(column.split(' ', 3)[2],
                                                                                  column.split(' ', 3)[0])),
                                          sep=",", decimal=".")

    def write_to_report(self, text):
        self.report.open()
        self.report.write(text)
        self.report.close()
        
    def compute_KPIs( self ):
        consumption_sum=0
        production_sum=0
        battery_discharge=0
        battery_charge=0
        pv_size=0
        chp_size=0
        #sum consumption and production of individual components
        for column in self.ppdt.results:
            if column.endswith('power_demand [Electricity - W]'):
                electric_loadprofile=(column[:-33])
                consumption_sum+=self.ppdt.results[column].mean()*8.76
            elif column.endswith('pv_power_production [Electricity - W]'):
                pv_size=float(column.split("'")[1])
                region=int(column.split("'")[3])
                type=column.split("'")[5]
                year=int(column.split("'")[7])
                production_sum+=(self.ppdt.results[column].mean()*8.76)
            elif column.endswith('chp_power_production [Electricity - W]'):
                chp_size=float(column.split("'")[1])
                region=int(column.split("'")[3])
                type=column.split("'")[5]
                year=int(column.split("'")[7])
                production_sum+=(self.ppdt.results[column].mean()*8.76)
            elif column.endswith('AcBatteryPower [Electricity - W]'):
                e_bat=float(column[:-36])
                battery_discharge+=np.minimum(0,self.ppdt.results[column].values).mean()*-8.76
                battery_charge+=np.maximum(0,self.ppdt.results[column].values).mean()*8.76
            elif column.endswith('ElectricityToOrFromGrid [Electricity - W]'):
                grid_supply = np.maximum(0,self.ppdt.results[column].values).mean()*8.76
                grid_feed = np.minimum(0,self.ppdt.results[column].values).mean()*-8.76

        autarkiegrad=(consumption_sum-grid_supply)/consumption_sum
        eigenverbrauch=(production_sum-grid_feed)/production_sum    
        #initilize lines for report
        lines = [ ]
        results_kpi=pd.DataFrame()
        results_kpi['electric_loadprofile']=[electric_loadprofile]
        results_kpi['weather_region']=[region]
        results_kpi['weather_type']=[type]
        results_kpi['weather_year']=[year]
        results_kpi['p_pv']=[pv_size]
        results_kpi['p_chp']=[chp_size]
        results_kpi['e_bat']=[e_bat]
        results_kpi['production_sum [kWh]']=[production_sum]
        results_kpi['consumption_sum [kWh]']=[consumption_sum]
        results_kpi['battery_discharge [kWh]']=[battery_discharge]
        results_kpi['battery_charge [kWh]']=[battery_charge]
        results_kpi['grid_feed [kWh]']=[grid_feed]
        results_kpi['grid_supply [kWh]']=[grid_supply]
        results_kpi['autarkiegrad']=[autarkiegrad]
        results_kpi['eigenverbrauch']=[eigenverbrauch]

        results_kpi.to_csv(os.path.join(self.ppdt.directory_path,"{}.csv".format('results_kpi')),index=False)


    def write_components_to_report(self):
        """
        Writes information about the components used in the simulation
        to the simulation report.
        """
        self.report.open()
        for wc in self.ppdt.wrapped_components:
            if hasattr(wc.MyComponent, "write_to_report"):
                component_content = wc.MyComponent.write_to_report()
                if isinstance(component_content, list) is False:
                    component_content = [component_content]
                self.report.write(component_content)
        self.report.close()

    def open_dir_in_file_explorer(self):
        """
        Opens files in given path.
        The keyword darwin is used for supporting macOS,
        xdg-open will be available on any unix client running X.

        """
        if sys.platform == "win32":
            os.startfile(os.path.realpath(self.ppdt.directory_path))
        else:
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.call([opener, os.path.realpath(self.ppdt.directory_path)])

    def export_sankeys(self):
        """
        Exports Sankeys plots.
        ToDo: To be substitute by SankeyHISIM and cal_pos_sim

        """

    def get_std_results(self):
        """:key
        ToDo: to be redefined and recoded in monthly bar plots in Bar Class


        """
        pd_timeline = pd.date_range(start=self.ppdt.simulation_parameters.start_date,
                                    end=self.ppdt.simulation_parameters.end_date,
                                    freq='{}S'.format(self.ppdt.simulation_parameters.seconds_per_timestep))[:-1]
        n_columns = self.ppdt.results.shape[1]
        df = pd.DataFrame()
        for i_column in range(n_columns):
            temp_df = pd.DataFrame(self.ppdt.results.values[:, i_column], index=pd_timeline, columns=[self.ppdt.results.columns[i_column]])
            if 'Temperature' in self.ppdt.results.columns[i_column] or 'Percent' in self.ppdt.results.columns[i_column]:
                temp_df = temp_df.resample('H').interpolate(method='linear')
            else:
                temp_df = temp_df.resample('H').sum()
            df[temp_df.columns[0]] = temp_df.values[:, 0]
            df.index = temp_df.index

        self.ppdt.results.index = pd_timeline
        #self.ppdt.results_std = df

        dfm = pd.DataFrame()
        for i_column in range(n_columns):
            temp_df = pd.DataFrame(self.ppdt.results.values[:, i_column], index=pd_timeline, columns=[self.ppdt.results.columns[i_column]])
            if 'Temperature' in self.ppdt.results.columns[i_column] or 'Percent' in self.ppdt.results.columns[i_column]:
                temp_df = temp_df.resample('M').interpolate(method='linear')
            else:
                temp_df = temp_df.resample('M').sum()
            dfm[temp_df.columns[0]] = temp_df.values[:, 0]
            dfm.index = temp_df.index

        self.result_m = dfm

# if __name__ == "__main__":
#     flags = {"plot_line": True,
#              "plot_carpet": False,
#              "plot_sankey": False,
#              "plot_day": False,
#              "plot_bar": False,
#              "open_dir": True,
#              "export_results_to_CSV": True}
#     my_post_processing = PostProcessor(**flags)
#     #my_post_processing.set_dir_results()
#     my_post_processing.run()
#     my_post_processing.open_dir_in_file_explorer()
