# -*- coding: utf-8 -*-

# Generic/Built-in
import numpy as np

# Owned
import hisim.utils as utils
from hisim import component as cp
from hisim.loadtypes import LoadTypes, Units
from hisim.simulationparameters import SimulationParameters
from hisim.components import controller_l3_generic_heatpump_modular
from hisim.components.generic_dhw_boiler import Boiler
from hisim import log

__authors__ = "edited Johanna Ganglbauer"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"

class L2_ControllerState:
    """
    This data class saves the state of the heat pump.
    """

    def __init__( self, timestep_actual : int = -1, state : int = 0, compulsory : int = 0, count : int = 0 ):
        self.timestep_actual = timestep_actual
        self.state = state
        self.compulsory = compulsory
        self.count = count
        
    def clone( self ):
        return L2_ControllerState( timestep_actual = self.timestep_actual, state = self.state, compulsory = self.compulsory, count = self.count )
    
    def is_first_iteration( self, timestep ):
        if self.timestep_actual + 1 == timestep:
            self.timestep_actual += 1
            self.compulsory = 0
            self.count = 0
            return True
        else:
            return False
        
    def is_compulsory( self ):
        if self.count <= 1:
            self.compulsory = 0
        else:
            self.compulsory = 1

    def activate( self ):
        self.state = 1
        self.compulsory = 1
        self.count += 1
        
    def deactivate( self ):
        self.state = 0
        self.compulsory = 1
        self.count += 1

class L2_Controller( cp.Component ):
    
    """ L2 heat pump controller. Processes signals ensuring comfort temperature of building

    Parameters
    --------------
    T_min: float, optional
        Minimum temperature of water in boiler, in °C. The default is 45 °C.
    T_max: float, optional
        Maximum temperature of water in boiler, in °C. The default is 60 °C.
    T_tolerance : float, optional
        Temperature difference the boiler may go below or exceed the hysteresis, because of recommendations from L3. The default is 10 °C.
    """
    # Inputs
    ReferenceTemperature = "ReferenceTemperature"
    l3_DeviceSignal = "l3_DeviceSignal"

    # Outputs
    l2_DeviceSignal = "l2_DeviceSignal"
    
    # #Forecasts
    # HeatPumpLoadForecast = "HeatPumpLoadForecast"

    # Similar components to connect to:
    # 1. Building
    # 2. HeatPump
    
    @utils.measure_execution_time
    def __init__( self, 
                  my_simulation_parameters : SimulationParameters,
                  T_min : float = 45.0,
                  T_max : float = 60.0,
                  T_tolerance : float   = 10.0 ):
        super().__init__( "L2_Controller", my_simulation_parameters = my_simulation_parameters )
        self.build( T_min, T_max, T_tolerance )

        #Component Inputs
        self.ReferenceTemperatureC: cp.ComponentInput = self.add_input(     self.ComponentName,
                                                                            self.ReferenceTemperature,
                                                                            LoadTypes.Temperature,
                                                                            Units.Celsius,
                                                                            mandatory = True )
        self.add_default_connections( Boiler, self.get_boiler_default_connections( ) )
        
        self.l3_DeviceSignalC: cp.ComponentInput = self.add_input(  self.ComponentName,
                                                                    self.l3_DeviceSignal,
                                                                    LoadTypes.OnOff,
                                                                    Units.binary,
                                                                    mandatory = False )
        
        #Component outputs
        self.l2_DeviceSignalC: cp.ComponentOutput = self.add_output( self.ComponentName,
                                                                     self.l2_DeviceSignal,
                                                                     LoadTypes.OnOff,
                                                                     Units.binary )
        
    def get_boiler_default_connections( self ):
        log.information("setting boiler default connections in L2 Controller")
        connections = [ ]
        boiler_classname = Boiler.get_classname( )
        connections.append( cp.ComponentConnection( L2_Controller.ReferenceTemperature, boiler_classname, Boiler.TemperatureMean ) )
        return connections

    def build( self, T_min, T_max, T_tolerance ):
        
        self.T_min = T_min
        self.T_max = T_max
        self.T_tolerance = T_tolerance
        self.state = L2_ControllerState( )
        self.previous_state = L2_ControllerState( )

    def i_save_state(self):
        self.previous_state = self.state.clone( )

    def i_restore_state(self):
        self.state = self.previous_state.clone( )

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues,  force_convergence: bool):
        # check demand, and change state of self.has_heating_demand, and self._has_cooling_demand
        if force_convergence:
            T_control = stsv.get_input_value( self.ReferenceTemperatureC )
            if T_control < ( self.T_max + self.T_min ) / 2 :
                stsv.set_output_value( self.l2_DeviceSignalC, 1 )
            else:
                stsv.set_output_value( self.l2_DeviceSignalC, 0 )
        
        #get temperature of building
        T_control = stsv.get_input_value( self.ReferenceTemperatureC )

        #get l3 recommendation if available
        l3state = 0
        if self.l3_DeviceSignalC.SourceOutput is not None:
            l3state = stsv.get_input_value( self.l3_DeviceSignalC )
        
            #reset temperature limits if recommended from l3
            if l3state == 1 :
                T_max = self.T_max + self.T_tolerance
                T_min = self.T_min
                self.state.is_compulsory( )
                self.previous_state.is_compulsory( )
            elif l3state == 0:
                T_max = self.T_max
                T_min = self.T_min - self.T_tolerance
                self.state.is_compulsory( )
                self.previous_state.is_compulsory( )
        
        else:
            T_max = self.T_max
            T_min = self.T_min

        #check if it is the first iteration and reset compulsory and timestep_of_last_activation in state and previous_state
        if self.state.is_first_iteration( timestep ):
            self.previous_state.is_first_iteration( timestep )
        
        #check out
        if T_control > T_max:
            #stop heating if temperature exceeds upper limit
            self.state.deactivate( )
            self.previous_state.deactivate( )

        elif T_control < T_min:
            #start heating if temperature goes below lower limit
            self.state.activate( )
            self.previous_state.activate( )
        else:
            if self.state.compulsory == 1:
                #use previous state if it compulsory
                pass
            elif self.l3_DeviceSignalC.SourceOutput is not None:
                #use recommendation from l3 if available and not compulsory
                self.state.state = l3state
            else:
                #use revious state if l3 was not available
                self.state = self.previous_state.clone( )

        stsv.set_output_value( self.l2_DeviceSignalC, self.state.state )

    def prin1t_outpu1t(self, t_m, state):
        log.information("==========================================")
        log.information("T m: {}".format(t_m))
        log.information("State: {}".format(state))