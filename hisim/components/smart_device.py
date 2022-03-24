# Generic/Built-in
import pandas as pd
import json
import numpy as np
import math as ma
from typing import Optional

# Owned
from hisim import component as cp
from hisim import loadtypes as lt
from hisim import utils
from hisim.components import occupancy
from hisim.components import pvs
from hisim.components import price_signal
from hisim.components import predictive_controller
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
    """Component representing smart appliance
    
    Parameters:
    -----------
    actual power : float, optional
        Power of smart appliance at given timestep
    time_to_go : integer, optional
        Duration of the power profile, which follows for the nex time steps
    state : integer, optional
        State of the smart appliance:
            -2... needs to be switched off
            -1... is switched off but can be turned on
             1... is running but can be switched off
             2... needs to be turned on
    position : integer, optional
        Index of demand profile relevent for the given timestep
    """
    
    def __init__( self, actual_power : float = 0, time_to_go : int = -1, state : int = -1, position : int = 0, dictionary_set : bool = False ):
        self.actual_power = actual_power
        self.time_to_go = time_to_go
        self.state = state
        self.position = position
        self.dictionary_set = dictionary_set
        
    def clone( self ):
        return SmartDeviceState( self.actual_power, self.time_to_go, self.state, self.position ) 
        
    def run( self, electricity_profile : list ):
        
        #device activation
        if self.time_to_go == -1:
            self.time_to_go = len( electricity_profile )
            self.state = 2
         
        #device is running    
        self.actual_power = electricity_profile[ - self.time_to_go ]
        self.time_to_go = self.time_to_go - 1
        
        #device deactivation
        if self.time_to_go == -1:
            self.state = -1
            self.position = self.position + 1
            self.dictionary_set = False
    
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
    
    #input
    SmartApplianceSignal = "SmartApplianceSignal"
       
    # Outputs
    ElectricityOutput = "ElectricityOutput"
    SmartApplianceState = "SmartApplianceState"
    
    #Forecasts
    ShiftableLoadForecast = "ShiftableLoadForecast"

    # Similar components to connect to:
    # None

    def __init__( self,
                  my_simulation_parameters: SimulationParameters ):
        super().__init__ ( name = "SmartDevice", my_simulation_parameters = my_simulation_parameters )

        self.build( seconds_per_timestep = my_simulation_parameters.seconds_per_timestep )
        
        #Input if smart controller
        self.SmartApplianceSignalC: cp.ComponentInput = self.add_input( self.ComponentName,
                                                                        self.SmartApplianceSignal,
                                                                        lt.LoadTypes.Any,
                                                                        lt.Units.Any,
                                                                        mandatory = False )
        
        #mandatory Output
        self.electricity_outputC: cp.ComponentOutput = self.add_output( self.ComponentName,
                                                                        self.ElectricityOutput,
                                                                        lt.LoadTypes.Electricity,
                                                                        lt.Units.Watt )
        
        #Output if Smart Controller
        if self.predictive == True:
            self.SmartApplianceStateC: cp.ComponentOutput = self.add_output( self.ComponentName,
                                                                             self.SmartApplianceState,
                                                                             lt.LoadTypes.Any,
                                                                             lt.Units.Any )
            self.add_default_connections( predictive_controller.PredictiveController, self.get_predictive_controller_default_connections( ) )
        
        
    def get_predictive_controller_default_connections( self ):
        print("setting smart device default connections")
        connections = [ ]
        predictive_controller_classname = predictive_controller.PredictiveController.get_classname( )
        connections.append( cp.ComponentConnection( SmartDevice.SmartApplianceSignal, predictive_controller_classname, 
                                                    predictive_controller.PredictiveController.SmartApplianceSignal ) )
        return connections

    def i_save_state( self ):
        self.previous_state : SmartDeviceState = self.state.clone( )

    def i_restore_state(self):
        self.state : SmartDeviceState = self.previous_state.clone( )

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues,  force_conversion: bool ):
        
        #initialize power
        self.state.actual_power = 0
        
        #check out hard conditions
        if self.state.time_to_go < 0:
            if timestep < self.earliest_start[ self.state.position ]: #needs to be switched off
                self.state.state = - 2
            elif timestep == self.latest_start[ self.state.position ]: #needs to be activated
                self.state.state = 2
            else: #free for activation
                self.state.state = -1
                if self.predictive == True and self.state.dictionary_set == False:
                    self.simulation_repository.set_entry( self.ShiftableLoadForecast, self.electricity_profile[ self.state.position ] )    
                    self.state.dictionary_set = True
        
        if self.predictive == True:
            #pass conditions to smart controller
            stsv.set_output_value( self.SmartApplianceStateC, self.state.state )
            devicesignal = stsv.get_input_value( self.SmartApplianceSignalC )
            
            if self.state.state == -1 and devicesignal == 1:
                self.state.run( self.electricity_profile[ self.state.position ] )
        
        #device actions based on controller signal
        if self.state.state == 2:
            self.state.run( self.electricity_profile[ self.state.position ] )

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
            # else:
            #     z = z - 1
            #duration.append( z ) 
            electricity_profile.append( elem_el )
            
        self.earliest_start = earliest_start
        self.latest_start = latest_start
        #self.duration = duration
        self.electricity_profile = electricity_profile
        self.device_names = device_names
        self.state = SmartDeviceState( )
        self.previous_state = SmartDeviceState( )
        self.predictive = self.my_simulation_parameters.system_config.predictive

    def write_to_report(self):
        lines = []
        for elem in self.device_names:
            lines.append("DeviceName: {}".format( self.ComponentName ) )
        return lines
        