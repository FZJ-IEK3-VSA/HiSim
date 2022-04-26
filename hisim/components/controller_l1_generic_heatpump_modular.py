# -*- coding: utf-8 -*-

# Owned
import hisim.utils as utils
from hisim import component as cp
from hisim.loadtypes import LoadTypes, Units
from hisim.simulationparameters import SimulationParameters
import hisim.components.generic_heat_pump_modular as generic_hp
from hisim import log

__authors__ = "edited Johanna Ganglbauer"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"

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
    """
    # Inputs
    HeatPumpSignal = "HeatPumpSignal"
    HeatPumpPowerPotential = "HeatPumpPowerPotential"

    # Outputs
    l1_HeatPumpSignal = "l1_HeatPumpSignal"
    l1_HeatPumpCompulsory = "l1_HeatPumpCompulsory"
    
    # Forecasts
    HeatPumpLoadForecast = "HeatPumpLoadForecast"

    # Similar components to connect to:
    # 1. Building
    @utils.measure_execution_time
    def __init__( self, 
                  my_simulation_parameters : SimulationParameters,
                  min_operation_time : int = 3600,
                  min_idle_time : int = 900 ):
        
        super().__init__( "L1_Controller", my_simulation_parameters = my_simulation_parameters )
        self.build( min_operation_time, min_idle_time )
        
        #add inputs
        self.HeatPumpSignalC: cp.ComponentInput = self.add_input( self.ComponentName,
                                                                   self.HeatPumpSignal,
                                                                   LoadTypes.OnOff,
                                                                   Units.binary,
                                                                   mandatory = True )
        self.HeatPumpPowerPotentialC: cp.ComponentInput = self.add_input(  self.ComponentName,
                                                                           self.HeatPumpPowerPotential,
                                                                           LoadTypes.Electricity,
                                                                           Units.Watt,
                                                                           mandatory = False )
        self.add_default_connections( generic_hp.HeatPump, self.get_heatpump_default_connections( ) )
        
        
        #add outputs
        self.l1_HeatPumpSignalC: cp.ComponentOutput = self.add_output( self.ComponentName,
                                                                       self.l1_HeatPumpSignal,
                                                                       LoadTypes.OnOff,
                                                                       Units.binary )
        
        self.l1_HeatPumpCompulsoryC: cp.ComponentOutput = self.add_output( self.ComponentName,
                                                                           self.l1_HeatPumpCompulsory,
                                                                           LoadTypes.Compulsory,
                                                                           Units.binary )
        
    def get_heatpump_default_connections( self ):
        log.information("setting heat pump default connections in L1 controller")
        connections = [ ]
        heat_pump_classname = generic_hp.HeatPump.get_classname( )
        connections.append( cp.ComponentConnection( L1_Controller.HeatPumpSignal, heat_pump_classname, generic_hp.HeatPump.HeatPumpSignal ) )
        connections.append( cp.ComponentConnection( L1_Controller.HeatPumpPowerPotential, heat_pump_classname, generic_hp.HeatPump.HeatPumpPowerPotential ) )
        return connections

    def build( self, min_operation_time, min_idle_time ):
        
        self.on_time = int( min_operation_time / self.my_simulation_parameters.seconds_per_timestep )
        self.off_time = int( min_idle_time / self.my_simulation_parameters.seconds_per_timestep )
        
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
        
        devicesignal = stsv.get_input_value( self.HeatPumpSignalC )
        
        if self.state.is_first_iteration( timestep ):
            self.state0 = self.state.clone( )
            
            #put forecast into dictionary
            if self.my_simulation_parameters.system_config.predictive:
                P_on = stsv.get_input_value( self.HeatPumpPowerPotentialC )

                if self.state0.state > 0:
                    self.simulation_repository.set_entry( self.HeatPumpLoadForecast, [ P_on ] * max( 1, self.on_time + self.state0.timestep_of_last_action - timestep ) )
                else:
                    self.simulation_repository.set_entry( self.HeatPumpLoadForecast, [ P_on ] * self.on_time )
            
        if ( self.state0.state == 1 and self.state0.timestep_of_last_action + self.on_time >= timestep ) or \
            ( self.state0.state == 0 and self.state0.timestep_of_last_action + self.off_time >= timestep ):
            mandatory = 1
        else:
            mandatory = 0
            if devicesignal == 0 and self.state0.state == 1:
                self.state.deactivation( timestep )
            elif devicesignal == 1 and self.state0.state == 0:
                self.state.activation( timestep )
        
        stsv.set_output_value( self.l1_HeatPumpSignalC, self.state.state )
        stsv.set_output_value( self.l1_HeatPumpCompulsoryC, mandatory )

    def prin1t_outpu1t(self, t_m, state):
        log.information("==========================================")
        log.information("T m: {}".format(t_m))
        log.information("State: {}".format(state))
