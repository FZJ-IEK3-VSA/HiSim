from hisim import component as cp
from hisim import loadtypes as lt
from hisim.components.configuration import PhysicsConfig
from hisim import utils
#from math import pi
#from math import floor
from hisim.simulationparameters import SimulationParameters
from hisim.components import controller_l2_generic_chp
import hisim.log as log
import pandas as pd
import os
from dataclasses import dataclass
from dataclasses_json import dataclass_json

import math

__authors__ = "Frank Burkrad, Maximilian Hillen,"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = ""
__version__ = ""
__maintainer__ = "Johanna Ganglbauer"
__email__ = "johanna.ganglbauer@4wardenergy.at"
__status__ = "development"

@dataclass_json
@dataclass
class GCHPConfig:
    """
    GCHP Config
    """
    name: str
    source_weight: int
    p_el : float       
    p_th : float
    p_fuel : float

    def __init__( self,
                  name : str,
                  source_weight : int,
                  p_el : float,
                  p_th : float,
                  p_fuel : float ):
        self.name = name
        self.source_weight = source_weight
        self.p_el = p_el
        self.p_th = p_th
        self.p_fuel = p_fuel
            
class CHPState:
    """
    This data class saves the state of the CHP.
    """

    def __init__( self, state : int = 0 ):
        self.state = state
        
    def clone( self ):
        return CHPState( state = self.state )
            
class GCHP( cp.Component ):
    """
    Simulates CHP operation with constant electical and thermal power as well as constant fuel consumption.
    """
    # Inputs
    l1_DeviceSignal = "l1_DeviceSignal"

    # Outputs
    ThermalEnergyDelivered = "ThermalEnergyDelivered"
    ElectricityOutput = "ElectricityOutput"
    FuelDelivered = "FuelDelivered"
    
    
    def __init__( self, my_simulation_parameters: SimulationParameters, config: GCHPConfig ):
        super().__init__( name = config.name + str( config.source_weight ), my_simulation_parameters=my_simulation_parameters )
        self.build( config )

        #Inputs
        self.l1_DeviceSignalC: cp.ComponentInput = self.add_input( self.ComponentName, 
                                                                self.l1_DeviceSignal, 
                                                                lt.LoadTypes.OnOff, 
                                                                lt.Units.binary,
                                                                mandatory = True )
        
        #Component outputs
        self.ThermalEnergyDeliveredC: cp.ComponentOutput = self.add_output( self.ComponentName,
                                                                            self.ThermalEnergyDelivered,
                                                                            lt.LoadTypes.Heating,
                                                                            lt.Units.Watt )
        self.ElectricityOutputC: cp.ComponentOutput = self.add_output( self.ComponentName,
                                                                       self.ElectricityOutput,
                                                                       lt.LoadTypes.Electricity,
                                                                       lt.Units.Watt )
        self.FuelDeliveredC: cp.ComponentOutput = self.add_output( self.ComponentName,
                                                                   self.FuelDelivered,
                                                                   lt.LoadTypes.Hydrogen,
                                                                   lt.Units.kg_per_sec )
        self.add_default_connections( L1_Controller, self.get_l1_controller_default_connections( ) )

    @staticmethod
    def get_default_config():
        config=GCHPConfig( name = 'CHP',
                          source_weight =  1,
                          p_el = 2000,
                          p_th = 3000,
                          p_fuel = 6000 ) 
        return config
    
    def build( self, config ):
        self.state = CHPState( )
        self.previous_state = CHPState( )
        self.source_weight = config.source_weight
        self.p_th = config.p_th
        self.p_el = config.p_el
        self.p_fuel = config.p_fuel * 1e-8 / 1.41 #converted to kg / s
    
    def i_save_state(self):
        self.previous_state = self.state.clone( )

    def i_restore_state(self):
        self.state = self.previous_state.clone( )

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
         pass

    def i_simulate( self, timestep: int, stsv: cp.SingleTimeStepValues,  force_convergence: bool ):

        # Inputs
        self.state.state = stsv.get_input_value( self.l1_DeviceSignalC )
       
        # Outputs
        stsv.set_output_value( self.ThermalEnergyDeliveredC, self.state.state * self.p_th )
        stsv.set_output_value( self.ElectricityOutputC, self.state.state * self.p_el )
        
        #heat of combustion hydrogen: 141.8 MJ / kg; conversion W = J/s to kg / s
        stsv.set_output_value( self.FuelDeliveredC, self.state.state * self.p_fuel )
        
    def get_l1_controller_default_connections( self ):
        log.information("setting l1 default connections in generic CHP" )
        connections = [ ]
        controller_classname = L1_Controller.get_classname( )
        connections.append( cp.ComponentConnection( GCHP.l1_DeviceSignal, controller_classname, L1_Controller.l1_DeviceSignal ) )
        return connections
        
@dataclass_json
@dataclass
class L1CHPConfig:
    """
    L1CHP Config
    """
    name : str
    source_weight : int
    min_operation_time : int
    min_idle_time : int
    min_h2_soc : float

    def __init__( self,
                  name : str,
                  source_weight : int,
                  min_operation_time : int,
                  min_idle_time : int,
                  min_h2_soc : float ):
        self.name = name
        self.source_weight = source_weight
        self.min_operation_time = min_operation_time
        self.min_idle_time = min_idle_time
        self.min_h2_SOC = min_h2_soc
        
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
    L1 CHP Controller. It takes care of the operation of the CHP only in terms of running times.

    Parameters
    --------------
    min_running_time: int, optional
        Minimal running time of device, in seconds. The default is 3600 seconds.
    min_idle_time : int, optional
        Minimal off time of device, in seconds. The default is 900 seconds.
    source_weight : int, optional
        Weight of component, relevant if there is more than one component of same type, defines hierachy in control. The default is 1.
    component type : str, optional
        Name of component to be controlled
    """
    # Inputs
    l2_DeviceSignal = "l2_DeviceSignal"
    ElectricityTarget = "ElectricityTarget"

    # Outputs
    l1_DeviceSignal = "l1_DeviceSignal"
    
    # Similar components to connect to:
    # 1. Building
    @utils.measure_execution_time
    def __init__( self, 
                  my_simulation_parameters : SimulationParameters,
                  config : L1CHPConfig ):
        
        super().__init__( name = config.name + str( config.source_weight ), 
                          my_simulation_parameters = my_simulation_parameters )
        
        self.build( config )
        
        #add inputs
        self.l2_DeviceSignalC: cp.ComponentInput = self.add_input( self.ComponentName,
                                                                   self.l2_DeviceSignal,
                                                                   lt.LoadTypes.OnOff,
                                                                   lt.Units.binary,
                                                                   mandatory = True )
        self.ElectricityTargetC : cp.ComponentInput = self.add_input( self.ComponentName,
                                                                      self.ElectricityTarget,
                                                                      lt.LoadTypes.Electricity,
                                                                      lt.Units.Watt,
                                                                      mandatory = True )
        
        # self.HydrogenSOC : cp.ComponentInput = self.add_input( self.ComponentName,
        #                                                        self.HydrogenSOC,
        #                                                        lt.LoadTypes.Hydrogen,
        #                                                        lt.Units.Percent,
        #                                                        mandatory = True )

        self.add_default_connections( controller_l2_generic_chp.L2_Controller, self.get_l2_controller_default_connections( ) )
        
        
        #add outputs
        self.l1_DeviceSignalC: cp.ComponentOutput = self.add_output(    self.ComponentName,
                                                                        self.l1_DeviceSignal,
                                                                        lt.LoadTypes.OnOff,
                                                                        lt.Units.binary )
        
    def get_l2_controller_default_connections( self ):
        log.information("setting l2 default connections in l1")
        connections = [ ]
        controller_classname = controller_l2_generic_chp.L2_Controller.get_classname( )
        connections.append( cp.ComponentConnection( L1_Controller.l2_DeviceSignal, controller_classname,controller_l2_generic_chp.L2_Controller.l2_DeviceSignal ) )
        return connections

    def build( self, config ):
        
        self.on_time = int( config.min_operation_time / self.my_simulation_parameters.seconds_per_timestep )
        self.off_time = int( config.min_idle_time / self.my_simulation_parameters.seconds_per_timestep )
        self.name = config.name
        self.source_weight = config.source_weight
        
        self.state0 = L1_ControllerState( )
        self.state = L1_ControllerState( )
        self.previous_state = L1_ControllerState( )

    def i_save_state(self):
        self.previous_state = self.state.clone( )

    def i_restore_state(self):
        self.state = self.previous_state.clone( )

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def i_simulate( self, timestep: int, stsv: cp.SingleTimeStepValues,  force_convergence: bool ):
        # check demand, and change state of self.has_heating_demand, and self._has_cooling_demand
        if force_convergence:
            pass
        
        l2_devicesignal = stsv.get_input_value( self.l2_DeviceSignalC )
        electricity_target = stsv.get_input_value( self.ElectricityTargetC )
        
        #save reference state state0 in first iteration
        if self.state.is_first_iteration( timestep ):
            self.state0 = self.state.clone( )
        
        #return device on if minimum operation time is not fulfilled and device was on in previous state
        if ( self.state0.state == 1 and self.state0.timestep_of_last_action + self.on_time >= timestep ):
            self.state.state = 1
        #return device off if minimum idle time is not fulfilled and device was off in previous state
        elif ( self.state0.state == 0 and self.state0.timestep_of_last_action + self.off_time >= timestep ):
            self.state.state = 0
        #check signal from l2 and turn on or off if it is necesary
        else:
            if ( l2_devicesignal == 0 or electricity_target <= 0 ) and self.state0.state == 1:
                self.state.deactivation( timestep )
            elif l2_devicesignal == 1 and electricity_target > 0 and self.state0.state == 0:
                self.state.activation( timestep )
        
        stsv.set_output_value( self.l1_DeviceSignalC, self.state.state )
        
    @staticmethod
    def get_default_config():
        config = L1CHPConfig( name = 'L1CHP',
                              source_weight =  1,
                              min_operation_time = 14400,
                              min_idle_time = 7200,
                              min_h2_soc = 10 )
        return config

    def prin1t_outpu1t(self, t_m, state):
        log.information("==========================================")
        log.information("T m: {}".format(t_m))
        log.information("State: {}".format(state))
        