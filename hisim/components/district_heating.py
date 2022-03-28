
# Import packages from standard library or the environment e.g. pandas, numpy etc.
from dataclasses import dataclass

# Import modules from HiSim
from hisim import component as cp
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim.components.building import Building
from hisim.components import predictive_controller
from hisim import log
__authors__ = "Johanna Ganglbauer - johanna.ganglbauer@4wardenergy.at"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"

class DistrictHeating( cp.Component ):
    """
    District heating implementation. District Heating transmitts heat with given efficiency.
    District heating is controlled with an on/off control oscillating within the comfort temperature band.
    """
    
    # Inputs
    DistrictHeatingControllerState = "DistrictHeatingControllerState"
    
    # Outputs
    ThermalEnergyDelivered =    "ThermalEnergyDelivered"
    PowerDistrictHeating =      "PowerDistrictHeating"

    def __init__( self, my_simulation_parameters: SimulationParameters , P_on : float, efficiency : float ):
        """
        Parameters
        ----------
        max_power : int
            Maximum power of district heating.
        min_power : int
            Minimal power of district heating
        efficiency : float
            Efficiency of heat transfer
        """
        
        super( ).__init__( name = 'DistrictHeating', my_simulation_parameters = my_simulation_parameters )
        
        #introduce parameters of district heating
        self.build( P_on = P_on, efficiency = efficiency )
        
        # Inputs - Mandatories
        self.DistrictHeatingControllerStateC : cp.ComponentInput = self.add_input(  self.ComponentName,
                                                                                    self.DistrictHeatingControllerState,
                                                                                    lt.LoadTypes.Any,
                                                                                    lt.Units.Any,
                                                                                    mandatory = True )
        
        # Outputs 
        self.thermal_energy_delivered : cp.ComponentOutput = self.add_output(   self.ComponentName,
                                                                                self.ThermalEnergyDelivered,
                                                                                lt.LoadTypes.Heating,
                                                                                lt.Units.Watt )
        
        self.PowerDistrictHeatingOutput: cp.ComponentOutput = self.add_output( self.ComponentName,
                                                                               self.PowerDistrictHeating,
                                                                               lt.LoadTypes.Heating,
                                                                               lt.Units.Watt )
        
        self.add_default_connections( DistrictHeatingController, self.get_controller_default_connections( ) )
        
    def get_controller_default_connections( self ):
        log.information("setting weather default connections")
        connections = [ ]
        controller_classname = DistrictHeatingController.get_classname( )
        connections.append( cp.ComponentConnection( DistrictHeating.DistrictHeatingControllerState, controller_classname, DistrictHeatingController.DistrictHeatingControllerState ) )
        return connections

    def build( self, P_on: float, efficiency : float ):
        """
        Assigns parameters of oil heater to class, and writes them to the report
        """
        
        #Parameters:
        self.P_on  =        P_on
        self.efficiency =   efficiency
        
        # Writes info to report
        self.write_to_report()

    def write_to_report( self ):
        """
        Returns
        -------
        lines : list of strings
            Text to enter report.
        """
        
        lines = []
        lines.append( "Name: {}".format( "District Heating" ) )
        lines.append( "Power: {:4.0f} kW".format( ( self.P_on ) * 1E-3 ) )
        lines.append( 'Efficiency : {:4.0f} %'.format( ( self.efficiency ) * 100 ) )
        return lines
    
    def i_save_state(self):
        pass

    def i_restore_state(self):
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues ):
        pass
    
    def i_simulate( self, timestep: int, stsv: cp.SingleTimeStepValues,  force_convergence: bool ):
        """
        Performs the simulation of the district heating model.
        """ 
        
        # Load control signal signalC value
        signal = stsv.get_input_value( self.DistrictHeatingControllerStateC )
        
        if signal > 0:
            signal = 1
        else:
            signal = 0
        
        # write values for output time series
        stsv.set_output_value( self.thermal_energy_delivered, signal * self.P_on * self.efficiency )
        stsv.set_output_value( self.PowerDistrictHeatingOutput, signal * self.P_on )
        
class ControllerState:
    """
    This data class saves the state of the controller.
    """

    def __init__( self, state : int = 0, timestep_of_last_action : int = -999 ):
        self.state = state
        self.timestep_of_last_action = timestep_of_last_action
        
    def clone( self ):
        return ControllerState( state = self.state, timestep_of_last_action = self.timestep_of_last_action )
        
class DistrictHeatingController( cp.Component ):
    """
    District Heating Controller. It takes power from the previous time step and adopts power to meet the heating needs.
    
    Parameters
    --------------
    T_min : float
        Lower comfort temperature of building, in °C. The default is 19 °C.
    T_max : float
        Upper comfort temperature of building, in °C. The default is 23 °C.    
    P_on : float, optional
        Power of heating when turned on, in W. The default is 600 W.
    on_time : int, optional
        Minimal running time of district heating system, in seconds. The default is 2700 s.
    off_time : int, optional
        Minimal off time of district heating, in seconds. The default is 120 s.
    heating_season_begin : int, optional
        Day( julian day, number of day in year ), when heating season starts. The default is 270.
    heating_season_end : int, optional
        Day( julian day, number of day in year ), when heating season ends. The default is 120
    """
    # Inputs
    TemperatureMean = "Residence Temperature"

    # Outputs
    DistrictHeatingControllerState = "DistrictHeatingControllerState"

    # Similar components to connect to:
    # 1. Building

    def __init__( self,
                  my_simulation_parameters: SimulationParameters,
                  T_min: float = 19.0,
                  T_max : float = 23.0,
                  P_on : float = 6000,
                  on_time : int = 2700,
                  off_time : int = 1800,
                  heating_season_begin : int = 270,
                  heating_season_end : int = 120 ):
        
        super().__init__( name = "DistrictHeatingController", my_simulation_parameters = my_simulation_parameters )
        
        self.build( T_min = T_min,
                    T_max = T_max,
                    P_on = P_on,
                    on_time = on_time,
                    off_time = off_time,
                    heating_season_begin = heating_season_begin,
                    heating_season_end = heating_season_end )

        #inputs
        self.t_mC: cp.ComponentInput = self.add_input( self.ComponentName,
                                                        self.TemperatureMean,
                                                        lt.LoadTypes.Temperature,
                                                        lt.Units.Celsius,
                                                        True )
        
        #outputs
        self.DistrictHeatingControllerStateC = self.add_output( self.ComponentName,
                                                                self.DistrictHeatingControllerState,
                                                                lt.LoadTypes.Any,
                                                                lt.Units.Any )
        
        self.add_default_connections( Building, self.get_building_default_connections( ) )
    
    def get_building_default_connections( self ):
        log.information("setting controller default connections")
        connections = [ ]
        building_classname = Building.get_classname( )
        connections.append( cp.ComponentConnection( DistrictHeatingController.TemperatureMean, building_classname, Building.TemperatureMean ) )
        return connections

    def build( self, T_min, T_max, P_on, on_time, off_time, heating_season_begin, heating_season_end ):
        self.T_min = T_min
        self.T_max = T_max
        self.P_on = P_on
        self.on_time = int( on_time / self.my_simulation_parameters.seconds_per_timestep )
        self.off_time = int( off_time / self.my_simulation_parameters.seconds_per_timestep )
        self.heating_season_begin = heating_season_begin * 24 * 3600 / self.my_simulation_parameters.seconds_per_timestep
        self.heating_season_end = heating_season_end * 24 * 3600 / self.my_simulation_parameters.seconds_per_timestep
        
        #initialize control mode
        self.state = ControllerState()
        self.state_previous = ControllerState( )
        
    def activation( self, timestep ):
        self.state.state = 2
        self.state.timestep_of_last_action = timestep
        #violently access previous timestep to avoid oscillation between 0 and 1 (decision is based on decision of previous time step)
        self.previous_state = self.state.clone( )

    def deactivation( self, timestep ):
        self.state.state = -2
        self.state.timestep_of_last_action = timestep 
        #violently access previous timestep to avoid oscillation between 0 and 1 (decision is based on decision of previous time step)
        self.previous_state = self.state.clone( )

    def i_save_state( self ):
        self.state = self.state_previous

    def i_restore_state( self ):
        self.state_previous = self.state

    def i_doublecheck( self, timestep: int, stsv: cp.SingleTimeStepValues ):
        pass

    def i_simulate( self, timestep: int, stsv: cp.SingleTimeStepValues,  force_convergence: bool  ):
        
        # check demand, and change state of self.has_heating_demand, and self._has_cooling_demand
        if force_convergence:
            pass
        
        if timestep < self.heating_season_begin and timestep > self.heating_season_end:
            self.state.state = -2
            
        elif ( self.state.state == 2 and timestep < self.state.timestep_of_last_action + self.on_time ) or \
             ( self.state.state == -2 and timestep < self.state.timestep_of_last_action + self.off_time ):
            pass
        else:
            # Retrieves inputs
            T_control = stsv.get_input_value( self.t_mC )
    
            #on off control based on temperature limits
            if T_control > self.T_max:
                #stop heating if temperature exceeds upper limit
                if self.state.state >= 0:
                    self.deactivation( timestep )

            elif T_control < self.T_min:
                #start heating if temperature goes below lower limit
                if self.state.state <= 0:
                    self.activation( timestep )
             
            #continue working if other is not defined    
            else:
                if self.state.state > 0:
                    self.state.state = 1
                if self.state.state < 0:
                    self.state.state = -1
        
        stsv.set_output_value( self.DistrictHeatingControllerStateC, self.state.state )

    #def prin1t_output(self, t_m, state):
    #    log.information("==========================================")
    #    log.information("T m: {}".format(t_m))
    #    log.information("State: {}".format(state))