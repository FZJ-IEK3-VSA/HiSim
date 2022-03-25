# Generic/Built-in
import numpy as np
import copy
import matplotlib
import seaborn
from math import pi
from typing import Union, List

# Owned
import hisim.component as cp
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim.components.occupancy import Occupancy
from hisim.components import predictive_controller
import hisim.log as log
seaborn.set(style='ticks')
font = {'family' : 'normal',
        'size'   : 24}

matplotlib.rc('font', **font)

__authors__ = "Johanna Ganglbauer - johanna.ganglbauer@4wardenergy.at"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"

class BoilerState:
    """
    This data class saves the state of the simulation results.
    """

    def __init__( self, volume_in_l:float, temperature_in_K:float ):
        """
        Parameters
        ----------
        volume : float
            Volume of boiler in liters.
        """
        self.temperature_in_K: float = temperature_in_K
        self.volume_in_l = volume_in_l

    def clone(self):
        return BoilerState(self.volume_in_l, self.temperature_in_K)


    def energy_from_temperature( self ) -> float:
        "converts temperature of storage (K) into energy contained in storage (kJ)"
        #0.977 is the density of water in kg/l
        #4.182 is the specific heat of water in kJ / ( K * kg )
        return self.temperature_in_K * self.volume_in_l * 0.977 * 4.182 #energy given in kJ


    def set_temperature_from_energy( self, energy_in_kJ):
     #   "converts energy contained in storage (kJ) into temperature (K)"
        #0.977 is the density of water in kg/l
        #4.182 is the specific heat of water in kJ / ( K * kg )
        self.temperature_in_K = energy_in_kJ / ( self.volume_in_l * 0.977 * 4.182 ) #temperature given in K
        
    
class Boiler( cp.Component ):
    """Implementation of a simple Boilder model with constant heat loss.
    The boiler is modelled as an energy bucket.
    
    Parameters
    ----------
    definition : string or list
        String selects predefined boiler types - currently availble option is '0815-boiler' or 'hydrogen-boiler'. 
        Alternatively, list defines boiler by the following parameters:
        [ volume in [ liters ], 
         surface in [ square metres ], 
         u-value in [ W / ( m^2 K ) ], 
         temperature of hot water leaving the hot water storage in [ Kelvin ],
         temperature of cold water to be heated in boiler in [ Kelvin ],
         power of boiler in [ Watt ],
         efficiency [ p. u. ]
         ]
    fuel : string
        either "electricity" or "hydrogen"
    """
    # Inputs
    BoilerControllerState = "BoilerControllerState" 
    WaterConsumption = "WaterConsumption"

    # obligatory Outputs
    StorageTemperature = "StorageTemperature"

    #availble Outputs
    ElectricityOutput = "ElectricityOutput"
    HydrogenOutput = "HydrogenOutput"
    
    def __init__( self, my_simulation_parameters: SimulationParameters, definition : Union[ str, List ] = '0815-boiler', fuel : str = 'electricity' ):

        super().__init__( "Boiler", my_simulation_parameters = my_simulation_parameters )
        self.build( definition, fuel )
        
        #initialize Boiler State
        self.state = BoilerState( volume_in_l = self.volume, temperature_in_K = 273 + 50 )
        self.previous_state = self.state.clone()
        
        #inputs
        self.WaterConsumptionC : cp.ComponentInput = self.add_input( self.ComponentName,
                                                           self.WaterConsumption,
                                                           lt.LoadTypes.WarmWater,
                                                           lt.Units.Liter, 
                                                           mandatory = True )
        
        self.BoilerControllerStateC : cp.ComponentInput = self.add_input( self.ComponentName,
                                                                          self.BoilerControllerState,
                                                                          lt.LoadTypes.Any,
                                                                          lt.Units.Any,
                                                                          mandatory = True )
        
        #Outputs
        self.StorageTemperatureC : cp.ComponentOutput = self.add_output( self.ComponentName,
                                                              self.StorageTemperature,
                                                              lt.LoadTypes.Temperature,
                                                              lt.Units.Kelvin )
        
        if self.fuel == 'electricity':
            self.electricity_output_c = self.add_output( self.ComponentName,
                                                        self.ElectricityOutput,
                                                        lt.LoadTypes.Electricity,
                                                        lt.Units.Watt )
        
        elif self.fuel == 'hydrogen':
            self.hydrogen_output_c = self.add_output( self.ComponentName,
                                                      self.HydrogenOutput,
                                                      lt.LoadTypes.Hydrogen,
                                                      lt.Units.kg_per_sec )
        else:
            raise Exception(" The fuel ", str( self.fuel ), " is not available. Choose either 'electricity' or 'hydrogen'. " )
            
        self.add_default_connections( Occupancy, self.get_occupancy_default_connections( ) )
        self.add_default_connections( BoilerController, self.get_controller_default_connections( ) )
        
    def get_occupancy_default_connections( self ):
        log.information("setting occupancy default connections")
        connections = [ ]
        occupancy_classname = Occupancy.get_classname( )
        connections.append( cp.ComponentConnection( Boiler.WaterConsumption, occupancy_classname, Occupancy.WaterConsumption ) )
        return connections
    
    def get_controller_default_connections( self ):
        log.information("setting controller default connections")
        connections = [ ]
        controller_classname = BoilerController.get_classname( )
        connections.append( cp.ComponentConnection( Boiler.BoilerControllerState, controller_classname, BoilerController.BoilerControllerState ) )
        return connections
    
    def build( self, definition, fuel ):
        if type( definition ) == str:
            if definition == '0815-boiler':
                self.volume = 200 #l
                self.surface = 1.2 #m^3
                self.u = 0.36 #W m^(-2) K^(-1)
                self.T_warmwater = 50 + 273 #K
                self.T_drainwater = 10 + 273 #K
                self.P_on = 2400 #W
                self.efficiency = 1.0
                
            elif definition == 'hydrogen-boiler':
                self.volume = 200 #l
                self.surface = 1.2 #m^3
                self.u = 0.36 #W m^(-2) K^(-1)
                self.T_warmwater = 50 + 273 #K
                self.T_drainwater = 10 + 273 #K
                self.P_on = 2400 #W
                self.efficiency = 0.9
        
        else:
            self.volume, self.surface, self.u, self.T_warmwater, self.T_drainwater, self.P_on, self.efficiency = definition
            
        self.fuel = fuel    
            
        self.write_to_report( )

    def write_to_report(self):
        lines = []
        lines.append("Name: {}".format("electric Boiler"))
        lines.append("Power: {:4.0f} kW".format( ( self.P_on ) * 1E-3 ) )
        lines.append( "Volume: {:4.0f} l".format( self.volume ) )
        return lines

    def i_save_state( self ):
        self.previous_state = self.state.clone()

    def i_restore_state( self ):
        self.state = self.previous_state.clone()

    def i_doublecheck( self, timestep: int, stsv: cp.SingleTimeStepValues ):
        pass

    def i_simulate( self, timestep: int, stsv: cp.SingleTimeStepValues,  force_convergence: bool ):
        
        # Retrieves inputs
        WW_consumption = stsv.get_input_value( self.WaterConsumptionC )
        signal = stsv.get_input_value( self.BoilerControllerStateC )
        
        if signal > 0:
            signal = 1
        else:
            signal = 0

        #constant heat loss of heat storage with the assumption that environment has 20°C = 293 K -> based on energy balance in kJ
        #heat loss due to hot water consumption -> base on energy balance in kJ
        #heat gain due to electrical/hydrogen heating of boiler -> based on energy balance in kJ
        #0.977 density of water in kg/l
        #4.182 specific heat of water in kJ K^(-1) kg^(-1)
        #1e-3 conversion J to kJ
        energy = self.state.energy_from_temperature()
        new_energy = energy - ( self.state.temperature_in_K - 293 ) * self.surface * self.u * self.my_simulation_parameters.seconds_per_timestep * 1e-3 \
                                              - WW_consumption * ( self.T_warmwater - self.T_drainwater ) * 0.977 * 4.182 \
                                              + signal * self.P_on * self.efficiency * self.my_simulation_parameters.seconds_per_timestep * 1e-3
                                              
        #convert new energy to new temperature
        self.state.set_temperature_from_energy( new_energy )
        
        #save outputs
        stsv.set_output_value( self.StorageTemperatureC, self.state.temperature_in_K )
        
        if self.fuel == 'electricity':
             stsv.set_output_value( self.electricity_output_c, self.P_on * signal )
        else:
            #heat of combustion hydrogen: 141.8 MJ / kg; conversion W = J/s to kg / s
             stsv.set_output_value( self.hydrogen_output_c, ( self.P_on * signal / 1.418 ) * 1e-8 )

class ControllerState:
    """
    This data class saves the state of the controller.
    """

    def __init__( self, state : int = 0, time_to_go : int = -1 ):
        self.state = state
        self.time_to_go = time_to_go
        
    def clone( self ):
        return ControllerState( state = self.state, time_to_go = self.time_to_go )

class BoilerController(cp.Component):
    """
    Boiler on/off controller. It activates boiler, when temperature falls below
    certain threshold and deactivates it if temperature exceeds limit.

    Parameters
    --------------
    t_water_min: float
        Minimum temperature of water in hot water storage -> in Kelvin
    t_water_max: float
        Maximum temperature of water in hot water storage -> in Kelvin
    P_on : float
        Power of boiler -> in Watt
    smart : bool
        One if boiler enforces heating in case of surplus from PV and 0 if it does not react to the situaltion.
    """
    # Inputs
    StorageTemperature = "Storage Temperature"
    BoilerSignal = "BoilerSignal"

    # Outputs
    BoilerControllerState = "BoilerControllerState"
    
    #Forecasts
    BoilerLoadForecast = "BoilerLoadForecast"

    # Similar components to connect to:
    # 1. Building

    def __init__( self, my_simulation_parameters: SimulationParameters,
                        t_water_min : float = 273.0 + 40.0,
                        t_water_max : float = 273.0 + 80.0,
                        P_on :        int =   2400,
                        on_time :   int = 2700,
                        off_time :  int = 1800 ):
        
        super().__init__( name="BoilerController", my_simulation_parameters = my_simulation_parameters )
        
        self.build( t_water_min = t_water_min,
                    t_water_max = t_water_max,
                    P_on = P_on,
                    on_time = on_time,
                    off_time = off_time )

        #input
        self.StorageTemperatureC: cp.ComponentInput = self.add_input( self.ComponentName,
                                                                      self.StorageTemperature,
                                                                      lt.LoadTypes.Temperature,
                                                                      lt.Units.Kelvin,
                                                                      mandatory = True )
        if self.my_simulation_parameters.system_config.predictive and self.my_simulation_parameters.system_config.boiler_included == 'electricity':
            self.BoilerSignalC: cp.ComponentInput = self.add_input( self.ComponentName,
                                                                    self.BoilerSignal,
                                                                    lt.LoadTypes.Any,
                                                                    lt.Units.Any,
                                                                    mandatory = False )
            self.add_default_connections( predictive_controller.PredictiveController, self.get_predictive_controller_default_connections( ) )
        
        
        #output
        self.BoilerControllerStateC = self.add_output( self.ComponentName,
                                                       self.BoilerControllerState,
                                                       lt.LoadTypes.Any,
                                                       lt.Units.Any )
        
        self.add_default_connections( Boiler, self.get_boiler_default_connections( ) )
    
    def get_boiler_default_connections( self ):
        log.information("setting boiler default connections")
        connections = [ ]
        boiler_classname = Boiler.get_classname( )
        connections.append( cp.ComponentConnection( BoilerController.StorageTemperature, boiler_classname, Boiler.StorageTemperature ) )
        return connections
    
    def get_predictive_controller_default_connections( self ):
        log.information( "setting predictive controller default connections") 
        connections = [ ]
        predictive_controller_classname = predictive_controller.PredictiveController.get_classname( )
        connections.append( cp.ComponentConnection( BoilerController.BoilerSignal, predictive_controller_classname, 
                                                    predictive_controller.PredictiveController.BoilerSignal ) )
        return connections

    def build( self, t_water_min, t_water_max, P_on, on_time, off_time ):
        
        # state
        self.state = ControllerState( )
        self.previous_state = ControllerState( )

        # Configuration
        self.t_water_min = t_water_min
        self.t_water_max = t_water_max
        self.P_on = P_on
        self.on_time = int( on_time / self.my_simulation_parameters.seconds_per_timestep )
        self.off_time = int( off_time / self.my_simulation_parameters.seconds_per_timestep )
        
    def activation( self ):
        self.state.state = 2
        self.state.time_to_go = self.on_time
        #violently access previous timestep to avoid oscillation between 0 and 1 (decision is based on decision of previous time step)
        self.previous_state = self.state.clone( )

    def deactivation( self ):
        self.state.state = -2
        self.state.time_to_go = self.off_time
        #violently access previous timestep to avoid oscillation between 0 and 1 (decision is based on decision of previous time step)
        self.previous_state = self.state.clone( )

    def i_save_state(self):
        self.previous_state = self.state.clone( )

    def i_restore_state(self):
        self.state = self.previous_state.clone( )

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues,  force_convergence: bool):

        if force_convergence: # please stop oscillating!
            return
            
        if self.state.time_to_go < 0:
            # Retrieves inputs
            T_control = stsv.get_input_value( self.StorageTemperatureC )
    
            #on off control based on temperature limits
            if T_control > self.t_water_max:
                #stop heating if temperature exceeds upper limit
                if self.state.state >= 0:
                    self.deactivation( )

            elif T_control < self.t_water_min:
                #start heating if temperature goes below lower limit
                if self.state.state <= 0:
                    self.activation( )
             
            #continue working if other is not defined    
            else:
                if self.state.state > 0:
                    self.state.state = 1
                if self.state.state < 0:
                    self.state.state = -1
        
            if self.my_simulation_parameters.system_config.predictive:
                #put forecast into dictionary
                if self.state.state > 0:
                    self.simulation_repository.set_entry( self.BoilerLoadForecast, [ self.P_on ] * max( 1, self.state.time_to_go + 1 ) )
                else:
                    self.simulation_repository.set_entry( self.BoilerLoadForecast, [ self.P_on ] * self.on_time )
                    
                #read in signal and modify state if recommended
                devicesignal = stsv.get_input_value( self.BoilerSignalC )
                if self.state.state == 1 and devicesignal == -1:
                    self.deactivation( )
                    
                elif self.state.state == -1 and devicesignal == 1:
                    self.activation( )
                    
            print( T_control ) 
            
        print( timestep, self.state.state, self.state.time_to_go )       
        self.state.time_to_go = self.state.time_to_go - 1
          
        
        stsv.set_output_value( self.BoilerControllerStateC, self.state.state )
            

    # def log_output(self, t_m, state):
    #     log.information("==========================================")
    #     log.information("T m: {}".format(t_m))
    #     log.information("State: {}".format(state))

