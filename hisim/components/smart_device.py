# Generic/Built-in
import pandas as pd
import json
import numpy as np
import math as ma

# Owned
from hisim import component as cp
from hisim import loadtypes as lt
from hisim import utils
from hisim.components import occupancy
from hisim.components import price_signal
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
            2... to be activated
            1... active
            0... may be activated or not
            -1.. needs to be switched off
    """
    
    def __init__( self, actual_power : float = 0, time_to_go : int = -1, state : int = -1, position : int = 0 ):
        self.actual_power = actual_power
        self.time_to_go = time_to_go
        self.state = state
        self.position = position
        
    def clone( self ):
        return SmartDeviceState( self.actual_power, self.time_to_go, self.state, self.position ) 
    
    def activate( self, time_to_go : int ):
        self.time_to_go = time_to_go
        self.state = 1
        self.position = self.position + 1
        
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
    
    #input
    ControllerState = "ControllerState"
       
    # Outputs
    ElectricityOutput = "ElectricityOutput"
    DeviceState = "DeviceState"
    
    #Forecasts
    ShiftableLoadForecast = "ShiftableLoadForecast"

    # Similar components to connect to:
    # None

    def __init__( self,
                  my_simulation_parameters: SimulationParameters ):
        super().__init__ ( name = "SmartDevice", my_simulation_parameters = my_simulation_parameters )

        self.build( seconds_per_timestep = my_simulation_parameters.seconds_per_timestep )
        
        #Input
        self.ControllerStateC: cp.ComponentInput = self.add_input( self.ComponentName,
                                                                   self.ControllerState,
                                                                   lt.LoadTypes.Any,
                                                                   lt.Units.Any,
                                                                   mandatory = True )
        #Outputs
        self.electricity_outputC: cp.ComponentOutput = self.add_output( self.ComponentName,
                                                                        self.ElectricityOutput,
                                                                        lt.LoadTypes.Electricity,
                                                                        lt.Units.Watt )
        self.DeviceStateC: cp.ComponentOutput = self.add_output( self.ComponentName,
                                                                 self.DeviceState,
                                                                 lt.LoadTypes.Any,
                                                                 lt.Units.Any )

    def i_save_state( self ):
        self.previous_state : SmartDeviceState = self.state.clone( )

    def i_restore_state(self):
        self.state : SmartDeviceState = self.previous_state.clone( )

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues,  force_conversion: bool ):
        
        #check out hard conditions
        if self.state.state != 1:
            if timestep < self.earliest_start[ self.state.position ]: #needs to be switched off
                self.state.state = - 1
            elif timestep == self.latest_start[ self.state.position ]: #needs to be activated
                self.state.state = 2
            else: #free for activation
                self.state.state = 0
                self.simulation_repository.set_entry( self.ShiftableLoadForecast, self.electricity_profile[ self.state.position ] )
        if self.state.state == 2:
                print( timestep, self.state.state, self.earliest_start[ self.state.position ], self.latest_start[ self.state.position ] )
                
        if timestep >= 750 and timestep < 900:
            print( timestep, self.state.state, self.earliest_start[ self.state.position ], self.latest_start[ self.state.position ] )
                
            
        #pass conditions to smart controller
        stsv.set_output_value( self.DeviceStateC, self.state.state )
        self.state.state = stsv.get_input_value( self.ControllerStateC )
        
        #device actions based on controller signal
        if self.state.state == 2:
            self.state.activate( self.duration[ self.state.position ] )

        self.state.run( self.electricity_profile[ self.state.position - 1 ] )
        if self.state.time_to_go == 0:
            self.state.deactivate( )
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
        self.previous_state = SmartDeviceState( )

    def write_to_report(self):
        lines = []
        for elem in self.device_names:
            lines.append("DeviceName: {}".format( self.ComponentName ) )
        return lines
    
class SmartDeviceController(cp.Component):
    """
    Smart Controller. It takes data from other
    components and sends signal to the smart device for
    activation or deactivation.

    Parameters
    --------------
    threshold_peak: float
        Maximal peak allowed for switch on.
    threshold_price: float
        Maximum price allowed for switch on
    prefered time: integer
        Prefered time for on switch
    """

    # Outputs
    DeviceState = "DeviceState"
    ControllerState = "ControllerState"

    # Similar components to connect to:
    # 1. Building

    def __init__(self, my_simulation_parameters: SimulationParameters,
                  threshold_peak : float = 5000,
                  threshold_price : float = 25 ):
        super( ).__init__( "SmartDeviceController", my_simulation_parameters = my_simulation_parameters )
        self.build( threshold_peak, threshold_price )
        
        #Input
        self.DeviceStateC: cp.ComponentInput = self.add_input( self.ComponentName,
                                                               self.DeviceState,
                                                               lt.LoadTypes.Any,
                                                               lt.Units.Any,
                                                               mandatory = True )
        #Output
        self.ControllerStateC: cp.ComponentOutput = self.add_output( self.ComponentName,
                                                                     self.ControllerState,
                                                                     lt.LoadTypes.Any,
                                                                     lt.Units.Any )

    def build(self, threshold_peak : float, threshold_price : float, state : int = 0 ):
        self.threshold_peak = threshold_peak
        self.threshold_price = threshold_price
        self.state = state

    def i_save_state(self):
        self.previous_state = self.state

    def i_restore_state(self):
        self.state = self.previous_state

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues,  force_convergence: bool):
        #get state
        self.state = stsv.get_input_value( self.DeviceStateC )
        
        #see if device is controllable
        if self.state == 0:
            #get forecasts
            shiftableload = self.simulation_repository.get_entry( SmartDevice.ShiftableLoadForecast )
            steps = len( shiftableload )
            
            demandforecast = self.simulation_repository.get_entry( occupancy.Occupancy.Electricity_Demand_Forecast_24h )[ : steps ]
            priceinjectionforecast = self.simulation_repository.get_entry( price_signal.PriceSignal.Price_Injection_Forecast_24h )[ : steps ]
            pricepurchaseforecast = self.simulation_repository.get_entry( price_signal.PriceSignal.Price_Purchase_Forecast_24h )[ : steps ]
            
            #build total load
            potentialload = [ a + b for ( a, b ) in zip( demandforecast, shiftableload ) ]
            
            #calculate price
            price_with = [ a * b for ( a, b ) in zip( potentialload, pricepurchaseforecast ) if a > 0 ] \
                + [ a * b for ( a, b ) in zip( potentialload, priceinjectionforecast ) if a < 0 ]
            price_without = [ a * b for ( a, b ) in zip( demandforecast, pricepurchaseforecast ) if a > 0 ] \
                + [ a * b for ( a, b ) in zip( demandforecast, priceinjectionforecast ) if a < 0 ]
            price = sum( price_with ) - sum( price_without )
            
            #calculate peak
            peak = max( potentialload )
            
            #make decision based on thresholds
            if peak < self.threshold_peak and price < self.threshold_price:
                self.state = 2
            else:
                self.state = -1 

        stsv.set_output_value( self.ControllerStateC, self.state )
        