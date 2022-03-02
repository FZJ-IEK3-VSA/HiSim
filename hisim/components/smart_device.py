# Generic/Built-in
import pandas as pd
import json
import numpy as np
import math as ma

# Owned
from hisim import component as cp
from hisim import loadtypes as lt
from hisim import utils
from hisim.simulationparameters import SimulationParameters
from hisim.components.configuration import HouseholdWarmWaterDemandConfig
from hisim.components.configuration import PhysicsConfig

__authors__ = "Johanna Ganglbauer"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"

class SmartDeviceState:
    
    def __init__( self, actual_power : float = 0, time_to_go : int = -1, state : int = -1 ):
        self.actual_power = actual_power
        self.time_to_go = time_to_go
        self.state = state
        
    def clone( self ):
        return SmartDeviceState( self.actual_power, self.state ) 
    
    def activate( self, time_to_go : int ):
        self.time_to_go = time_to_go
        self.state = 1
        
    def run( self, electricity_profile : list ):
        if self.state == 1:
            self.actual_power = electricity_profile[ - self.time_to_go ]
            self.time_to_go = self.time_to_go - 1
        else:
            self.actual_power = 0
        
    def deactivate( self ):
        self.time_to_go = -1
        self.state = -1
    
class SmartDevice( cp.Component ):
    """
    Class component that provides availablity and profiles of flexible smart devices like shiftable (in time) washing machines and dishwashers. 
    Data provided or based on LPG exports.

    Parameters
    -----------------------------------------------
    profile: string
        profile code corresponded to the family or residents configuration

    ComponentInputs:
    -----------------------------------------------
       None

    ComponentOutputs:
    -----------------------------------------------
       Load Profile of Smart Device : kWh
       State of Smart Device : Any
    """

    # Outputs
    ElectricityOutput = "ElectricityOutput"
    
    #input
    State = "State"

    # Similar components to connect to:
    # None

    def __init__( self,
                  my_simulation_parameters: SimulationParameters ):
        super().__init__ ( name = "SmartDevice", my_simulation_parameters = my_simulation_parameters )

        self.build( seconds_per_timestep = my_simulation_parameters.seconds_per_timestep )

        self.electricity_outputC: cp.ComponentOutput = self.add_output( self.ComponentName,
                                                                        self.ElectricityOutput,
                                                                        lt.LoadTypes.Electricity,
                                                                        lt.Units.Watt )

    def i_save_state(self):
        pass

    def i_restore_state(self):
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues,  force_conversion: bool ):
        
        if self.state.state == - 1:
            if self.earliest_start[ 0 ] <= timestep and self.latest_start[ 0 ] > timestep:
                pass
                #case for smart controller
            elif self.latest_start[ 0 ] == timestep:
                print( timestep, self.state.state, self.state.time_to_go, self.state.actual_power , 'actually happened - 0' )
                self.activate( )
                print( timestep, self.state.state, self.state.time_to_go, self.state.actual_power, self.electricity_profile[ 0 ] , 'actually happened - 1' )

        self.run( )
        stsv.set_output_value( self.electricity_outputC, self.state.actual_power )
            
        
    def build( self, seconds_per_timestep : int = 60 ):

        #load smart device profile
        smart_device_profile = [ ]
        filepath = utils.HISIMPATH[ "smart_devices" ] 
        f = open( filepath )
        smart_device_profile = json.load( f )
        f.close()
        
        if not smart_device_profile:
            raise NameError( 'LPG data for smart appliances is missing or located missleadingly' )
        
        #initializing relevant data
        earliest_start, latest_start, duration, electricity_profile, device_names = [ ], [ ], [ ], [ ], [ ]
        
        minutes_per_timestep = seconds_per_timestep / 60
        
        if not minutes_per_timestep.is_integer( ):
            raise TypeError( 'Up to now smart appliances have only been implemented for timeresolutions corresponding to multiples of one minute' )
        minutes_per_timestep = int( minutes_per_timestep )
        
        #reading in data from json file and adopting to given time resolution
        for sample in smart_device_profile :
            device_name = str( sample[ 'Device' ][ 'Name' ] )
            if device_name not in device_names:
                device_names.append( device_name )
            #earliest start in given time resolution -> integer value
            x = sample[ 'EarliestStart' ][ 'ExternalStep' ] 
            # timestep (in minutes) the profile is shifted in the first step of the external time resolution
            offset = minutes_per_timestep -  x % minutes_per_timestep
            #earliest start in given time resolution -> float value
            x = x / minutes_per_timestep
            #latest start in given time resolution 
            y = sample[ 'LatestStart' ][ 'ExternalStep' ] /  minutes_per_timestep 
            #number of timesteps in given time resolution -> integer value
            z = ma.ceil( x + sample[ 'TotalDuration' ] / minutes_per_timestep ) - ma.floor( x )
            #earliest and latest start in new time resolution -> integer value
            earliest_start.append( ma.floor( x ) ) 
            latest_start.append( ma.ceil( y ) )
        
            #get shiftable load profile    
            el = sample[ 'Profiles' ][ 2 ][ 'TimeOffsetInSteps' ] * [ 0 ] + sample[ 'Profiles' ][ 2 ][ 'Values' ]
        
            #average profiles given in 1 minute resolution to given time resolution
            elem_el = [ ]
            #append first timestep which may not fill  the entire 15 minutes
            elem_el.append( sum( el[ : offset ] ) / offset )
            
            for i in range( z - 2 ):
                elem_el.append( sum( el[ offset + minutes_per_timestep * i : offset + ( i + 1 ) * minutes_per_timestep ] ) / minutes_per_timestep )
            
            last = el[ offset + ( i + 1 ) * minutes_per_timestep : ]
            if offset != minutes_per_timestep:
                elem_el.append( sum( last ) / ( minutes_per_timestep - offset ) )
            else:
                z = z - 1
            duration.append( z ) 
            electricity_profile.append( elem_el )
            
        self.earliest_start = earliest_start
        self.latest_start = latest_start
        self.duration = duration
        self.electricity_profile = electricity_profile
        self.device_names = device_names
        self.state = SmartDeviceState( )
        
    def activate( self ):
        self.state.activate( self.duration[ 0 ] )
        if len( self.duration ) > 1:
            #print( len( self.duration ) )
            self.duration.pop( 0 )
            #print( len( self.duration ) )
            self.earliest_start.pop( 0 )
            self.latest_start.pop( 0 )
        
    def run( self ):
        self.state.run( self.electricity_profile[ 0 ] )
        if self.state.time_to_go == 0:
            if len( self.electricity_profile ) > 1:
                #print( len( self.electricity_profile ) )
                self.electricity_profile.pop( 0 )
                #print( len( self.electricity_profile ) )
            self.state.deactivate( )

    def write_to_report(self):
        lines = []
        for elem in self.device_names:
            lines.append("DeviceName: {}".format( self.ComponentName ) )
        return lines
    
# class SmartDeviceController(cp.Component):
#     """
#     Smart Controller. It takes data from other
#     components and sends signal to the smart device for
#     activation or deactivation.

#     Parameters
#     --------------
#     threshold_peak: float
#         Maximal peak allowed for switch on.
#     threshold_price: float
#         Maximum price allowed for switch on
#     prefered time: integer
#         Prefered time for on switch
#     """

#     # Outputs
#     State = "State"

#     # Similar components to connect to:
#     # 1. Building

#     def __init__(self, my_simulation_parameters: SimulationParameters,
#                  threshold_peak : float = 600,
#                  threshold_price : float = 100,
#                  prefered_time : int = 8 ):
#         super( ).__init__( "SmartDeviceController", my_simulation_parameters = my_simulation_parameters )
#         self.build( threshold_peak, threshold_price, prefered_time )

#         self.stateC: cp.ComponentOutput = self.add_output( self.ComponentName,
#                                                            self.State,
#                                                            LoadTypes.Any,
#                                                            Units.Any )

#     def build(self, threshold_peak : float, threshold_price : float, prefered_time : int ):
#         self.threshold_peak = threshold_peak
#         self.threshold_price = threshold_price
#         self.prefered_time = prefered_time

#     def i_save_state(self):
#         self.previous_heatpump_mode = self.controller_heatpumpmode

#     def i_restore_state(self):
#         self.controller_heatpumpmode = self.previous_heatpump_mode

#     def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
#         pass

#     def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues,  force_convergence: bool):
#         # check demand, and change state of self.has_heating_demand, and self._has_cooling_demand
#         if force_convergence:
#             pass
#         else:
#             # Retrieves inputs
#             t_m_old = stsv.get_input_value(self.t_mC)
#             electricity_input = stsv.get_input_value(self.electricity_inputC)

#             if self.mode == 1:
#                 self.conditions(t_m_old)
#             elif self.mode == 2:
#                 self.smart_conditions(t_m_old, electricity_input)

#         if self.controller_heatpumpmode == 'heating':
#             state = 1
#         if self.controller_heatpumpmode == 'cooling':
#             state = -1
#         if self.controller_heatpumpmode == 'off':
#             state = 0
#         stsv.set_output_value(self.stateC, state)