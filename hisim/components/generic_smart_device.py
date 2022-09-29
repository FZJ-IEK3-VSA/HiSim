# Generic/Built-in
import pandas as pd
import json
import numpy as np
import math as ma
from typing import Optional, List

# Owned
from hisim import component as cp
from hisim import loadtypes as lt
from hisim import utils
from hisim.components import controller_l3_smart_devices
from hisim.components import generic_pv_system
from hisim.components import generic_price_signal
#from hisim.components import controller_l3_predictive
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
    timestep_of_activation : int, optional
        Timestep, where the device was activated. The default is -999.
    time_to_go : integer, optional
        Duration of the power profile, which follows for the nex time steps. The default is 0.
    position : integer, optional
        Index of demand profile relevent for the given timestep. The default is 0.
    """
    
    def __init__( self, actual_power : float = 0, timestep_of_activation : int = -999, time_to_go : int = 0, position : int = 0 ):
        self.actual_power = actual_power
        self.timestep_of_activation = timestep_of_activation
        self.time_to_go = time_to_go
        self.position = position
        
    def clone( self ):
        return SmartDeviceState( self.actual_power, self.timestep_of_activation, self.time_to_go, self.position ) 
        
    def run( self, timestep : int, electricity_profile : List[float] )-> None:
        
        #device activation
        if timestep > self.timestep_of_activation + self.time_to_go:
            self.timestep_of_activation = timestep
            self.time_to_go = len( electricity_profile )
            self.actual_power = electricity_profile[ 0 ]
            
        if timestep < self.timestep_of_activation + self.time_to_go:
            #device is running    
            self.actual_power = electricity_profile[ timestep - self.timestep_of_activation ]
        
        #device deactivation
        if timestep == self.timestep_of_activation + self.time_to_go:
            self.position = self.position + 1
            self.time_to_go = 0
            self.actual_power = 0
    
class SmartDevice( cp.Component ):
    """
    Class component that provides availablity and profiles of flexible smart devices like shiftable (in time) washing machines and dishwashers. 
    Data provided or based on LPG exports.

    Parameters
    -----------------------------------------------
    profile: string
        profile code corresponded to the family or residents configuration
    """
      
    #optional Inputs
    l3_DeviceActivation = "l3_DeviceActivation"
    
    # Outputs
    #mandatory
    ElectricityOutput = "ElectricityOutput"
    
    #optional
    LastActivation = "LastActivation"
    EarliestActivation = "EarliestActivation"
    LatestActivation = "LatestActivation"

    def __init__( self,
                  identifier : str,
                  source_weight : int,
                  my_simulation_parameters: SimulationParameters):
        super().__init__ ( name = identifier.split(' ')[0] + identifier.split(' ')[1] + str( source_weight ), my_simulation_parameters = my_simulation_parameters )

        self.build( identifier = identifier, source_weight = source_weight, seconds_per_timestep = my_simulation_parameters.seconds_per_timestep )
        
        #mandatory Output
        self.ElectricityOutputC: cp.ComponentOutput = self.add_output(
            object_name=self.component_name, field_name=self.ElectricityOutput, load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT, postprocessing_flag=[lt.InandOutputType.CONSUMPTION])
        self.l3_DeviceActivationC: cp.ComponentInput = self.add_input(self.component_name,
                                                                      self.l3_DeviceActivation,
                                                                      lt.LoadTypes.ACTIVATION,
                                                                      lt.Units.TIMESTEPS,
                                                                      mandatory = False)
        
        if self.predictive:    
            self.LastActivationC: cp.ComponentOutput = self.add_output(object_name = self.component_name,
                                                                       field_name = self.LastActivation,
                                                                       load_type = lt.LoadTypes.ACTIVATION,
                                                                       unit = lt.Units.TIMESTEPS)
            self.EarliestActivationC: cp.ComponentOutput = self.add_output(object_name = self.component_name,
                                                                           field_name = self.EarliestActivation,
                                                                           load_type = lt.LoadTypes.ACTIVATION,
                                                                           unit = lt.Units.TIMESTEPS)
            self.LatestActivationC: cp.ComponentOutput = self.add_output(object_name = self.component_name,
                                                                         field_name = self.LatestActivation,
                                                                         load_type = lt.LoadTypes.ACTIVATION,
                                                                         unit = lt.Units.TIMESTEPS)
            
    def i_save_state( self ) -> None:
        self.previous_state : SmartDeviceState = self.state.clone( )

    def i_restore_state(self)  -> None:
        self.state : SmartDeviceState = self.previous_state.clone( )

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues)  -> None:
        pass
    def i_prepare_simulation(self) -> None:
        """ Prepares the simulation. """
        pass
    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues,  force_conversion: bool )  -> None:
        
        #initialize power
        self.state.actual_power = 0
        
        #set forecast in first timestep
        if timestep == 0 and self.predictive:
            self.simulation_repository.set_dynamic_entry(component_type = lt.ComponentType.SMART_DEVICE, source_weight = self.source_weight,
                                                         entry = [ [ ], self.electricity_profile[ 0 ] ])
        
        #if not already running: check if activation makes sense
        if timestep > self.state.timestep_of_activation + self.state.time_to_go:
            if timestep > self.earliest_start[ self.state.position ]: #needs to be switched off
                #initialize next activation
                activation:float = timestep + 10
                #when predictive read in best activation
                if self.predictive:
                    activation = stsv.get_input_value( self.l3_DeviceActivationC )
                #if last possible switch on force activation
                if timestep >= self.latest_start[ self.state.position ]: #needs to be activated
                    activation = timestep
                    
                if timestep == activation:
                    self.state.run( timestep, self.electricity_profile[ self.state.position ] )
                    if self.predictive == True:
                        if self.state.position < len( self.electricity_profile ) - 1:
                            self.simulation_repository.set_dynamic_entry(component_type = lt.ComponentType.SMART_DEVICE, source_weight = self.source_weight,
                                                                         entry = [ self.electricity_profile[ self.state.position ], self.electricity_profile[ self.state.position + 1 ] ])
                            
                        elif self.state.position == len( self.electricity_profile ) - 1:
                            self.simulation_repository.set_dynamic_entry(component_type = lt.ComponentType.SMART_DEVICE, source_weight = self.source_weight,
                                                                         entry = [ self.electricity_profile[ self.state.position ], [ ] ])
        
        #run device if it was already activated
        else:
            self.state.run( timestep, self.electricity_profile[ self.state.position ] )

        stsv.set_output_value( self.ElectricityOutputC, self.state.actual_power )
        
        if self.predictive == True:
            #pass conditions to smart controller
            stsv.set_output_value( self.LastActivationC, self.state.timestep_of_activation )
            stsv.set_output_value( self.EarliestActivationC, self.earliest_start[ self.state.position ] )
            stsv.set_output_value( self.LatestActivationC, self.latest_start[ self.state.position ] )
        
    def build( self, identifier: str, source_weight: int, seconds_per_timestep : int = 60 ) -> None:

        #load smart device profile
        smart_device_profile = [ ]
        filepath = utils.HISIMPATH[ "smart_devices" ][ "profile_data" ] 
        f = open( filepath )
        smart_device_profile = json.load( f )
        f.close()
        
        if not smart_device_profile:
            raise NameError( 'LPG data for smart appliances is missing or located missleadingly' )
        
        #initializing relevant data
        earliest_start, latest_start, electricity_profile = [ ], [ ], [ ]
        
        minutes_per_timestep = seconds_per_timestep / 60
        
        if not minutes_per_timestep.is_integer( ):
            raise TypeError( 'Up to now smart appliances have only been implemented for time resolutions corresponding to multiples of one minute' )
        minutes_per_timestep = int( minutes_per_timestep )
        
        #reading in data from json file and adopting to given time resolution
        for sample in smart_device_profile :
            device_name = str( sample[ 'Device' ][ 'Name' ] )
            if device_name == identifier:
                #earliest start in given time resolution -> integer value
                x = sample[ 'EarliestStart' ][ 'ExternalStep' ] 
                #skip if occurs in calibration days (negative sign )
                if x < 0:
                    continue
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
                electricity_profile.append( elem_el )
        
        self.source_weight = source_weight    
        self.earliest_start = earliest_start + [ self.my_simulation_parameters.timesteps ] #append value to continue simulation after last necesary run of flexible device at end of year
        self.latest_start = latest_start + [ self.my_simulation_parameters.timesteps + 999 ] #append value to continue simulation after last necesary run of smart device at end of year
        self.electricity_profile = electricity_profile
        self.state = SmartDeviceState( )
        self.previous_state = SmartDeviceState( )
        self.predictive = self.my_simulation_parameters.system_config.predictive

    def write_to_report(self) -> List[str]:
        lines: List[str] = []
        lines.append("DeviceName: {}".format(self.component_name))
        return lines
        