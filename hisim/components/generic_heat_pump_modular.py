# Generic/Built-in
import numpy as np
import copy
import matplotlib
import seaborn
from math import pi

# Owned
import hisim.utils as utils
from hisim import component as cp
from hisim.loadtypes import LoadTypes, Units
from hisim.simulationparameters import SimulationParameters
from hisim.components.weather import Weather
from hisim.components import controller_l1_generic_heatpump_modular
from hisim import log

seaborn.set(style='ticks')
font = {'family' : 'normal',
        'size'   : 24}

matplotlib.rc('font', **font)

__authors__ = "edited Johanna Ganglbauer"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"

class HeatPumpState:
    """
    This data class saves the state of the heat pump.
    """

    def __init__( self, state : int = 0, timestep : int = -1 ):
        self.state = state
        self.timestep = timestep
        
    def clone( self ):
        return HeatPumpState( state = self.state, timestep = self.timestep )

class HeatPump(cp.Component):
    """
    Heat pump implementation. It does support a
    refrigeration cycle.

    Parameters
    ----------
    manufacturer : str
        Heat pump manufacturer
    name : str
        Heat pump model
    heating_season_begin : int, optional
        Day( julian day, number of day in year ), when heating season starts - and cooling season ends. The default is 270.
    heating_season_end : int, optional
        Day( julian day, number of day in year ), when heating season ends - and cooling season starts. The default is 150
    """
    # Inputs
    TemperatureOutside = "TemperatureOutside"
    l1_DeviceSignal = "l1_DeviceSignal"
    l1_RunTimeSignal = 'l1_RunTimeSignal'

    # Outputs
    ThermalEnergyDelivered = "ThermalEnergyDelivered"
    ElectricityOutput = "ElectricityOutput"
    
    # Forecasts
    HeatPumpLoadForecast = "HeatPumpLoadForecast"

    # Similar components to connect to:
    # 1. HeatPump l1 controller
    # 2. HeatPump l2 controller
    # 3. HeatPump l3 controller ( optional )
    
    @utils.measure_execution_time
    def __init__( self,
                  my_simulation_parameters: SimulationParameters,
                  manufacturer : str = "Viessmann Werke GmbH & Co KG",
                  name : str ="Vitocal 300-A AWO-AC 301.B07",
                  heating_season_begin : int = 270,
                  heating_season_end : int = 150 ):
        super().__init__( "HeatPump", my_simulation_parameters = my_simulation_parameters )

        self.build( manufacturer, name, heating_season_begin, heating_season_end )

        # Inputs - Mandatories
        self.TemperatureOutsideC: cp.ComponentInput = self.add_input(   self.ComponentName,
                                                                        self.TemperatureOutside,
                                                                        LoadTypes.Any,
                                                                        Units.Celsius,
                                                                        mandatory = True )
        
        self.l1_DeviceSignalC: cp.ComponentInput = self.add_input(  self.ComponentName,
                                                                    self.l1_DeviceSignal,
                                                                    LoadTypes.OnOff,
                                                                    Units.binary,
                                                                    mandatory = True )
        self.l1_RunTimeSignalC: cp.ComponentInput = self.add_input( self.ComponentName,
                                                                    self.l1_RunTimeSignal,
                                                                    LoadTypes.Any,
                                                                    Units.Any,
                                                                    mandatory = False )
        
        #Outputs
        self.ThermalEnergyDeliveredC: cp.ComponentOutput = self.add_output(   self.ComponentName,
                                                                              self.ThermalEnergyDelivered,
                                                                              LoadTypes.Heating,
                                                                              Units.Watt )
        self.ElectricityOutputC: cp.ComponentOutput = self.add_output(  self.ComponentName,
                                                                        self.ElectricityOutput,
                                                                        LoadTypes.Electricity,
                                                                        Units.Watt )
            
        self.add_default_connections( Weather, self.get_weather_default_connections( ) )
        self.add_default_connections( controller_l1_generic_heatpump_modular.L1_Controller, self.get_l1_controller_default_connections( ) )
        
    def get_weather_default_connections( self ):
        log.information("setting weather default connections in HeatPump")
        connections = [ ]
        weather_classname = Weather.get_classname( )
        connections.append( cp.ComponentConnection( HeatPump.TemperatureOutside, weather_classname, Weather.TemperatureOutside ) )
        return connections
    
    def get_l1_controller_default_connections( self ):
        log.information("setting l1 default connections in HeatPump")
        connections = [ ]
        controller_classname = controller_l1_generic_heatpump_modular.L1_Controller.get_classname( )
        connections.append( cp.ComponentConnection( HeatPump.l1_DeviceSignal, controller_classname, controller_l1_generic_heatpump_modular.L1_Controller.l1_DeviceSignal ) )
        connections.append( cp.ComponentConnection( HeatPump.l1_RunTimeSignal, controller_classname, controller_l1_generic_heatpump_modular.L1_Controller.l1_RunTimeSignal ) )
        return connections

    def build( self, manufacturer, name, heating_season_begin, heating_season_end ):

        # Retrieves heat pump from database - BEGIN
        heat_pumps_database = utils.load_smart_appliance("Heat Pump")

        heat_pump_found = False
        for heat_pump in heat_pumps_database:
            if heat_pump["Manufacturer"] == manufacturer and heat_pump["Name"] == name:
                heat_pump_found = True
                break

        if heat_pump_found == False:
            raise Exception("Heat pump model not registered in the database")

        # Interpolates COP data from the database
        self.cop_ref = []
        self.t_out_ref = []
        for heat_pump_cops in heat_pump['COP']:
            self.t_out_ref.append(float([*heat_pump_cops][0][1:].split("/")[0]))
            self.cop_ref.append(float([*heat_pump_cops.values()][0]))
        self.cop_coef = np.polyfit(self.t_out_ref, self.cop_ref, 1)

        self.max_heating_power = heat_pump['Nominal Heating Power A2/35'] * 1E3
        
        self.heating_season_begin = heating_season_begin * 24 * 3600 / self.my_simulation_parameters.seconds_per_timestep
        self.heating_season_end = heating_season_end * 24 * 3600 / self.my_simulation_parameters.seconds_per_timestep
        
        self.state = HeatPumpState( )
        self.previous_state = HeatPumpState( )

        # Writes info to report
        self.write_to_report()

    def cal_cop( self, t_out ):
        return self.cop_coef[0] * t_out + self.cop_coef[1]

    def i_save_state(self):
        self.previous_state = self.state.clone( )

    def i_restore_state(self):
        self.state = self.previous_state.clone( )

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def write_to_report(self):
        lines = []
        lines.append("Name: {}".format("Heat Pump"))
        lines.append("Max power: {:4.0f} kW".format((self.max_heating_power)*1E-3))
        return lines

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues,  force_convergence: bool):
        
        # Inputs
        self.state.state = stsv.get_input_value( self.l1_DeviceSignalC )
        T_out = stsv.get_input_value( self.TemperatureOutsideC )
          
        #cop
        cop = self.cal_cop( T_out )
        
        # write values for output time series
        #cooling season
        if timestep < self.heating_season_begin and timestep > self.heating_season_end:
            stsv.set_output_value( self.ThermalEnergyDeliveredC, - self.state.state * self.max_heating_power )
        #heating season
        else:
            stsv.set_output_value( self.ThermalEnergyDeliveredC, self.state.state * self.max_heating_power )
        
        stsv.set_output_value( self.ElectricityOutputC, self.state.state * self.max_heating_power / cop )
        
        #put forecast into dictionary
        if self.my_simulation_parameters.system_config.predictive:
            #only in first timestep
            if self.state.timestep + 1 == timestep:
                self.state.timestep += 1
                self.previous_state.timestep += 1
                runtime = stsv.get_input_value( self.l1_RunTimeSignalC )
                self.simulation_repository.set_entry( self.HeatPumpLoadForecast, [ self.max_heating_power / cop ] * runtime )

    def prin1t_outpu1t(self, t_m, state):
        log.information("==========================================")
        log.information("T m: {}".format(t_m))
        log.information("State: {}".format(state))

