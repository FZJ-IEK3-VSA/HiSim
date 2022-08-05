import os
import pandas as pd
import sys
import inspect
import subprocess
#import tkinter as tk
import tkinter.filedialog as filedialog
from enum import Enum

#currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
#parentdir = os.path.dirname(currentdir)
#sys.path.insert(0, parentdir)

# Owned
import hisim.log as log
from hisim import utils
from hisim.utils import PostProcessingOptions
from hisim import loadtypes as lt
import pickle
#from . import charts
#from . import report
import hisim.postprocessing.charts as charts
from hisim.postprocessing.chart_singleday import ChartSingleDay
import hisim.postprocessing.report as report
from hisim import component
from hisim.simulationparameters import SimulationParameters
import warnings

#import cfg_automator

class PostProcessingDataTransfer:
    def __init__(self,
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
        # Johanna Ganglbauer: time correction factor is applied in postprocessing to sum over power values and convert them to energy
        self.time_correction_factor =  simulation_parameters.seconds_per_timestep / 3600
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

#    def open_latest_pickle(self):
 #       sim_pickle, dirpath, dirname = self.get_lastest_pickle()
  #      self.dirname = dirname
   #     #utils.del_file_type(dirname, ".png")
    #    self.get_pickle_attributes(sim_pickle)

    def set_dir_results(self, dirname=None):
        if dirname is None:
            dirname = filedialog.askdirectory(initialdir=utils.HISIMPATH["results"])
        self.dirname = dirname

    #def open_pickle(self, dirname):
#        sim_pickle, dirpath = utils.open_pickle(dirname)
#        utils.del_file_type(dirname, ".png")
#        self.get_pickle_attributes(sim_pickle)

 #   def get_lastest_pickle(self):
  #      stored_results_list = os.listdir(self.resultsdir)
   #     execution_dates = []
    #    for index, result_dir in enumerate(stored_results_list):
     #       temp = result_dir.split("_")
      #      execution_dates.append("{}_{}".format(temp[-2], temp[-1]))

       # dir_index = execution_dates.index(max(execution_dates))
        #latest_dir = stored_results_list[dir_index]
        #latest_dir_path = os.path.join(self.resultsdir, latest_dir)
        #for file in os.listdir(latest_dir_path):
         #   if file.endswith(".pkl"):
          #      pickle_file = file
           #     break

        #filepath = os.path.join(latest_dir_path, pickle_file)
        #with open(filepath, 'rb') as input:
#            extracted_pickle = pickle.load(input)
 #       return extracted_pickle, latest_dir_path, latest_dir

    # def get_pickle_attributes(self, sim_pickle):
    #     ppdt: PostProcessingDataTransfer = sim_pickle
    #     self.dirpath = ppdt.directory_path
    #     #self.dirpath = sim_pickle["directory_path"]

    #     self.report.executation_time = ppdt.execution_time #  sim_pickle["execution_time"]
    #     self.time_correction_factor = ppdt.time_correction_factor # sim_pickle["time_correction_factor"]
    #     self.all_outputs = ppdt.all_outputs#  sim_pickle["all_outputs"]
    #     self.results_m = ppdt.results_m # sim_pickle["results_m"]
    #     self.WrappedComponents = ppdt.wrapped_components # sim_pickle["wrapped_components"]

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
        ##
        warnings.filterwarnings("ignore")
#        if self.dirname is None:
 #           self.open_latest_pickle()
  #      else:
   #         self.open_pickle(self.dirname)

        days={"month":0,
              "day":0}
        #if len(self.results) == 60 * 24 * 365:
        for index, output in enumerate(self.ppdt.all_outputs):
            if PostProcessingOptions.PlotLine in  self.ppdt.postProcessingOptions:
                my_line = charts.Line(output=output.FullName,
                               data=self.ppdt.results.iloc[:, index],
                               units=output.Unit,
                               directorypath=self.ppdt.directory_path,
                               time_correction_factor=self.ppdt .time_correction_factor)
                my_line.plot()
            if PostProcessingOptions.PlotCarpet in self.ppdt.postProcessingOptions:
                #log.information("Making carpet plots")
                my_carpet = charts.Carpet(output=output.FullName,
                                   data=self.ppdt.results.iloc[:, index],
                                   units=output.Unit,
                                   directorypath=self.ppdt.directory_path,
                                   time_correction_factor=self.ppdt.time_correction_factor)
                my_carpet.plot( xdims = int( ( self.ppdt.simulation_parameters.end_date - self.ppdt.simulation_parameters.start_date ).days ) )

            if PostProcessingOptions.PlotDay in self.ppdt.postProcessingOptions:
                    my_days = ChartSingleDay(output=output.FullName,
                                   data=self.ppdt.results.iloc[:, index],
                                   units=output.Unit,
                                   directorypath=self.ppdt.directory_path,
                                   time_correction_factor=self.ppdt.time_correction_factor,
                                   day=days["day"],
                                   month=days["month"])
                    my_days.plot()
            if PostProcessingOptions.PlotBar in self.ppdt.postProcessingOptions:
                my_bar = charts.Bar(output=output.FullName,
                             data=self.ppdt.results_monthly.iloc[:, index],
                             units=output.Unit,
                             dirpath=self.ppdt.directory_path,
                             time_correction_factor=self.ppdt.time_correction_factor)
                my_bar.plot()


        # Plot sankey
        if PostProcessingOptions.PlotSankey in self.ppdt.postProcessingOptions:
            log.information("plotting sankeys")
        #    self.plot_sankeys()
        else:
            for option in self.ppdt.postProcessingOptions:
                log.information("in sankey: selected: " + str(option))
            log.information("not plotting sankeys")

        # Export all results to CSV
        if PostProcessingOptions.ExportToCSV in self.ppdt.postProcessingOptions:
            log.information("exporting to csv")
            self.export_results_to_csv()
        else:
            log.information("not exporting to CSV")
            
         # Export all results to CSV
        if PostProcessingOptions.ComputeKPI in self.ppdt.postProcessingOptions:
            log.information("Computing KPIs")
            self.compute_KPIs( )
        else:
            log.information("not exporting to CSV")


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
                #my_days.close()

        # Open file explorer
        if PostProcessingOptions.OpenDirectory in self.ppdt.postProcessingOptions:
            self.open_dir_in_file_explorer()

    @utils.measure_execution_time
    def export_results_to_csv(self):
        for column in self.ppdt.results:
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
        #sum consumption and production of individual components
        self.ppdt.results[ 'consumption' ] = 0
        self.ppdt.results[ 'production' ] = 0
        self.ppdt.results[ 'storage' ] = 0
        for index, output in enumerate(self.ppdt.all_outputs):
            if 'ElectricityOutput' in output.FullName:
                if ( 'PVSystem' in output.FullName ) or ( 'CHP' in output.FullName ) :
                    self.ppdt.results[ 'production' ] = self.ppdt.results[ 'production' ] + self.ppdt.results.iloc[:, index]
                else:
                    self.ppdt.results[ 'consumption' ] = self.ppdt.results[ 'consumption' ] + self.ppdt.results.iloc[:, index]
            elif 'AcBatteryPower' in output.FullName:
                self.ppdt.results[ 'storage' ] = self.ppdt.results[ 'storage' ] + self.ppdt.results.iloc[:, index]
            else:
                continue
            
        #initilize lines for report
        lines = [ ]
         
        #sum over time and write to report
        consumption_sum = self.ppdt.results[ 'consumption' ].sum( ) * self.ppdt.simulation_parameters.seconds_per_timestep / 3.6e6
        lines.append( "Consumption: {:4.0f} kWh".format( consumption_sum ) )
        
        production_sum = self.ppdt.results[ 'production' ].sum( ) * self.ppdt.simulation_parameters.seconds_per_timestep / 3.6e6
        lines.append( "Production: {:4.0f} kWh".format( production_sum ) )
        
        if production_sum > 0:
            #evaluate injection, sum over time and wite to 
            injection = ( self.ppdt.results[ 'production' ] - self.ppdt.results[ 'storage' ] - self.ppdt.results[ 'consumption' ] ) 
            injection_sum = injection[ injection > 0 ].sum( ) * self.ppdt.simulation_parameters.seconds_per_timestep / 3.6e6
            lines.append( "Injection: {:4.0f} kWh".format( injection_sum ) )
            
            batterylosses = self.ppdt.results[ 'storage' ].sum( ) * self.ppdt.simulation_parameters.seconds_per_timestep / 3.6e6
            print( batterylosses )
            
            #evaluate self consumption rate and autarky rate:
            lines.append( "Autarky Rate: {:3.1f} %".format( 100 * ( production_sum - injection_sum - batterylosses ) / consumption_sum ) )
            lines.append( "Self Consumption Rate: {:3.1f} %".format( 100 * ( production_sum - injection_sum ) / production_sum ) )
            
            #evaluate electricity price
            if 'PriceSignal - PricePurchase [Price - Cents per kWh]' in self.ppdt.results:
                price = - ( ( injection[ injection < 0 ] * self.ppdt.results[ 'PriceSignal - PricePurchase [Price - Cents per kWh]' ][ injection < 0 ] ).sum( ) \
                        + ( injection[ injection > 0 ] * self.ppdt.results[ 'PriceSignal - PriceInjection [Price - Cents per kWh]' ][ injection > 0 ]).sum( ) ) \
                        * self.ppdt.simulation_parameters.seconds_per_timestep / 3.6e6
                        
                lines.append( "Price paid for electricity: {:3.0f} EUR".format( price *1e-2 ) )
        
        else:
            if 'PriceSignal - PricePurchase [Price - Cents per kWh]' in self.ppdt.results:
                price = ( self.ppdt.results[ 'consumption' ] * self.ppdt.results[ 'PriceSignal - PricePurchase [Price - Cents per kWh]' ] ).sum( ) \
                    * self.ppdt.simulation_parameters.seconds_per_timestep / 3.6e6
                lines.append( "Price paid for electricity: {:3.0f} EUR".format( price *1e-2 ) )
            
        self.write_to_report( lines )

    #
    # def cal_pos_sim(self):
    #     self.write_components_to_report()
    #
    #     total_electricity_consumed = None
    #     total_electricity_not_covered = None
    #     heat_pump_heating = None
    #     heat_pump_cooling = 0.0
    #     building_area = None
    #     solar_gain_through_windows = None
    #     internal_gains = None
    #
    #     for index, entry in enumerate(self.ppdt.wrapped_components):
    #         if entry.MyComponent.ComponentName == "Building":
    #             building_area = entry.MyComponent.A_f
    #
    #     for index, entry in enumerate(self.ppdt.all_outputs):
    #         if entry.ObjectName == "ElectricityGrid_Consumed":
    #             total_electricity_consumed = sum(entry.Results)* self.ppdt.time_correction_factor
    #         if entry.ObjectName == "ElectricityGrid_NotConveredConsumed":
    #             total_electricity_not_covered = sum(entry.Results)* self.ppdt.time_correction_factor
    #         if entry.ObjectName == "HeatPump" and entry.FieldName == "Heating":
    #             heat_pump_heating = sum(entry.Results)* self.ppdt.time_correction_factor
    #         if entry.ObjectName == "HeatPump" and entry.FieldName == "Cooling":
    #             heat_pump_cooling = abs(sum(entry.Results))* self.ppdt.time_correction_factor
    #         if entry.ObjectName == "HeatPump" and entry.FieldName == "ElectricityOutput":
    #             heat_pump_electricity_output = abs(sum(entry.Results)) * self.ppdt.time_correction_factor
    #         if entry.ObjectName == "HeatPump" and entry.FieldName == "NumberOfCycles":
    #             heat_pump_number_of_cycles = abs(entry.Results[-1])
    #         if entry.ObjectName == "Building" and entry.FieldName == "SolarGainThroughWindows":
    #             solar_gain_through_windows = abs(sum(entry.Results))* self.ppdt.time_correction_factor
    #         if entry.ObjectName == "Occupancy" and entry.FieldName == "HeatingByResidents":
    #             internal_gains = abs(sum(entry.Results)*self.ppdt.time_correction_factor)
    #
    #     # Writes self-consumption and autarky
    #     if total_electricity_consumed is not None:
    #         if total_electricity_not_covered is not None:
    #             autarky = ( ( total_electricity_consumed - total_electricity_not_covered ) / total_electricity_consumed ) * 100
    #             text = ["Consumed: {:.0f} kWh".format(total_electricity_consumed * 1E-3)]
    #             self.write_to_report(text)
    #             text = ["Not Covered: {:.0f} kWh".format(total_electricity_not_covered * 1E-3)]
    #             self.write_to_report(text)
    #             text = ["Autarky: {:.3}%".format(autarky)]
    #             self.write_to_report(text)
    #
    #     # Writes performance of heat pump
    #     if heat_pump_heating is not None:
    #         self.write_to_report(["HeatPump - Absolute Heating Demand [kWh]: {:.0f}".format(1E-3*heat_pump_heating)])
    #         self.write_to_report(["HeatPump - Absolute Cooling Demand [kWh]: {:.0f}".format(1E-3*heat_pump_cooling)])
    #         self.write_to_report(["HeatPump - Electricity Output [kWh]: {:.0f}".format(1E-3*heat_pump_electricity_output)])
    #         self.write_to_report(["HeatPump - Number Of Cycles: {}".format(heat_pump_number_of_cycles)])
    #         self.write_to_report(["HeatPump - Overall Coefficient of Performance: {:.2f}".format( (heat_pump_heating+heat_pump_cooling)/heat_pump_electricity_output )])
    #         if building_area is not None:
    #             self.write_to_report(["HeatPump - Relative Heating Demand [kWh/m2]: {:.0f} ".format(1E-3*heat_pump_heating/building_area)])
    #
    #     # Writes building solar gains
    #     if solar_gain_through_windows is not None:
    #         self.write_to_report(["Absolute Solar Gains [kWh]: {:.0f}".format(1E-3*solar_gain_through_windows)])
    #         if building_area is not None:
    #             self.write_to_report(["Relative Solar Gains [Wh/m2]: {:.0f} ".format(1E-3*solar_gain_through_windows/building_area)])
    #
    #     # Writes building internal gains
    #     if internal_gains is not None:
    #         self.write_to_report(["Absolute Internal Gains [kWh]: {:.0f}".format(1E-3*internal_gains)])
    #         if building_area is not None:
    #             self.write_to_report(["Relative Internal Gains [kWh/m2]: {:.0f} ".format(1E-3*internal_gains/building_area)])

    def write_components_to_report(self):
        """
        Writes information about the components used in the simulation
        to the simulation report.
        """
        self.report.open()
        for wc in self.ppdt.wrapped_components:
            if hasattr(wc.my_component, "write_to_report"):
                component_content = wc.my_component.write_to_report()
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
