# -*- coding: utf-8 -*-

# Owned
import hisim.utils as utils
from hisim import component as cp
from hisim.loadtypes import LoadTypes, Units
from hisim.simulationparameters import SimulationParameters
from hisim.components import controller_l2_generic_heatpump_modular
from hisim import log

from dataclasses import dataclass
from dataclasses_json import dataclass_json

__authors__ = "edited Johanna Ganglbauer"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"

@dataclass_json
@dataclass
class L1Config:
    """
    L1 Config
    """
    name: str
    source_weight: int
    min_operation_time : int      
    min_idle_time : int

    def __init__( self,
                  name : str,
                  source_weight : int,
                  min_operation_time : int,
                  min_idle_time : int ):
        self.name = name
        self.source_weight = source_weight
        self.min_operation_time = min_operation_time
        self.min_idle_time = min_idle_time

class L1_ControllerState:
    """
    This data class saves the state of the controller.
    """

    def __init__( self, timestep_actual : int = -1, state : int = 0, timestep_of_last_action : int = 0 ):
        self.timestep_actual = timestep_actual
        self.state = state
        self.timestep_of_last_action = timestep_of_last_action
        
    def clone( self ):
        return L1_ControllerState( timestep_actual = self.timestep_actual, state = self.state, timestep_of_last_action = self.timestep_of_last_action )
    
    def is_first_iteration( self, timestep ):
        if self.timestep_actual + 1 == timestep:
            self.timestep_actual += 1
            return True
        else:
            return False
        
    def activation( self, timestep ):
        self.state = 1
        self.timestep_of_last_action = timestep
        
    def deactivation( self, timestep ):
        self.state = 0
        self.timestep_of_last_action = timestep 

class L1_Controller( cp.Component ):
    
    """
    L1 Heat Pump Controller. It takes care of the operation of the heat pump only in terms of running times.

    Parameters
    --------------
    min_running_time: int, optional
        Minimal running time of device, in seconds. The default is 3600 seconds.
    min_idle_time : int, optional
        Minimal off time of device, in seconds. The default is 900 seconds.
    heating_season_begin : int, optional
        Day( julian day, number of day in year ), when heating season starts - and cooling season ends. The default is 270.
    heating_season_end : int, optional
        Day( julian day, number of day in year ), when heating season ends - and cooling season starts. The default is 150
    source_weight : int, optional
        Weight of component, relevant if there is more than one component of same type, defines hierachy in control. The default is 1.
    component type : str, optional
        Name of component to be controlled
    """
    # Inputs
    l2_DeviceSignal = "l2_DeviceSignal"

    # Outputs
    l1_DeviceSignal = "l1_DeviceSignal"
    l1_RunTimeSignal = "l1_RunTimeSignal"

    # Similar components to connect to:
    # 1. Building
    @utils.measure_execution_time
    def __init__( self, my_simulation_parameters : SimulationParameters, config = L1Config ):
        
        super().__init__( config.name + str( config.source_weight ), my_simulation_parameters = my_simulation_parameters )
        self.build( config )
        
        #add inputs
        self.l2_DeviceSignalC: cp.ComponentInput = self.add_input( self.ComponentName,
                                                                   self.l2_DeviceSignal,
                                                                   LoadTypes.OnOff,
                                                                   Units.binary,
                                                                   mandatory = True )
        self.add_default_connections( controller_l2_generic_heatpump_modular.L2_Controller, self.get_l2_controller_default_connections( ) )
        
        
        #add outputs
        self.l1_DeviceSignalC: cp.ComponentOutput = self.add_output(    self.ComponentName,
                                                                        self.l1_DeviceSignal,
                                                                        LoadTypes.OnOff,
                                                                        Units.binary )
        if self.my_simulation_parameters.system_config.predictive == True:
            self.l1_RunTimeSignalC: cp.ComponentOutput = self.add_output(   self.ComponentName,
                                                                            self.l1_RunTimeSignal,
                                                                            LoadTypes.Any,
                                                                            Units.Any )
        
    def get_l2_controller_default_connections( self ):
        log.information("setting l2 default connections in l1")
        connections = [ ]
        controller_classname = controller_l2_generic_heatpump_modular.L2_Controller.get_classname( )
        connections.append( cp.ComponentConnection( L1_Controller.l2_DeviceSignal, controller_classname,controller_l2_generic_heatpump_modular.L2_Controller.l2_DeviceSignal ) )
        return connections
    
    @staticmethod
    def get_default_config():
        config = L1Config( name = 'L1Controller',
                           source_weight =  1,
                           min_operation_time = 3600,
                           min_idle_time = 900 ) 
        return config

    def build( self, config ):
        self.name = config.name
        self.source_weight = config.source_weight
        self.on_time = int( config.min_operation_time / self.my_simulation_parameters.seconds_per_timestep )
        self.off_time = int( config.min_idle_time / self.my_simulation_parameters.seconds_per_timestep )
        
        self.state0 = L1_ControllerState( )
        self.state = L1_ControllerState( )
        self.previous_state = L1_ControllerState( )

    def i_save_state(self):
        self.previous_state = self.state.clone( )

    def i_restore_state(self):
        self.state = self.previous_state.clone( )

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues,  force_convergence: bool):
        # check demand, and change state of self.has_heating_demand, and self._has_cooling_demand
        if force_convergence:
            pass
        
        l2_devicesignal = stsv.get_input_value( self.l2_DeviceSignalC )
        
        #save reference state state0 in first iteration
        if self.state.is_first_iteration( timestep ):
            self.state0 = self.state.clone( )
            
            if self.my_simulation_parameters.system_config.predictive == True:
                if self.state0.state == 1:
                    runtime = max( 1, self.on_time - timestep + self.state0.timestep_of_last_action )
                else:
                    runtime = self.on_time
                stsv.set_output_value( self.l1_RunTimeSignalC, runtime )
        
        #return device on if minimum operation time is not fulfilled and device was on in previous state
        if ( self.state0.state == 1 and self.state0.timestep_of_last_action + self.on_time >= timestep ):
            self.state.state = 1
        #return device off if minimum idle time is not fulfilled and device was off in previous state
        elif ( self.state0.state == 0 and self.state0.timestep_of_last_action + self.off_time >= timestep ):
            self.state.state = 0
        #check signal from l2 and turn on or off if it is necesary
        else:
            if l2_devicesignal == 0 and self.state0.state == 1:
                self.state.deactivation( timestep )
            elif l2_devicesignal == 1 and self.state0.state == 0:
                self.state.activation( timestep )
        
        stsv.set_output_value( self.l1_DeviceSignalC, self.state.state )

    def prin1t_outpu1t(self, t_m, state):
        log.information("==========================================")
        log.information("T m: {}".format(t_m))
        log.information("State: {}".format(state))
