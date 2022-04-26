# -*- coding: utf-8 -*-

# Generic/Built-in
import numpy as np

# Owned
import hisim.utils as utils
from hisim import component as cp
from hisim.loadtypes import LoadTypes, Units
from hisim.simulationparameters import SimulationParameters
from hisim.components.building import Building
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

    def __init__( self, timestep_actual : int = -1, state : int = 0, mandatory : int = 0 ):
        self.timestep_actual = timestep_actual
        self.state = state
        self.mandatory = mandatory
        
    def clone( self ):
        return L2_ControllerState( timestep_actual = self.timestep_actual, state = self.state, mandatory = self.mandatory )
    
    def is_first_iteration( self, timestep ):
        if self.timestep_actual + 1 == timestep:
            self.timestep_actual += 1
            self.mandatory = 0
    
    def activate( self ):
        self.state = 1
        self.mandatory = 1
        
    def deactivate( self ):
        self.state = 0
        self.mandatory = 1

class L2_Controller( cp.Component ):
    
    """ L2 heat pump controller. Processes signals ensuring comfort temperature of building

    Parameters
    --------------
    T_min_heating: float, optional
        Minimum comfortable temperature for residents during heating period, in °C. The default is 19 °C.
    T_max_heating: float, optional
        Minimum comfortable temperature for residents during heating period, in °C. The default is 23 °C.
    T_min_cooling: float, optional
        Minimum comfortable temperature for residents during cooling period, in °C. The default is 23 °C.
    T_max_cooling: float, optional
        Minimum comfortable temperature for residents during cooling period, in °C. The default is 26 °C.
    heating_season_begin : int, optional
        Day( julian day, number of day in year ), when heating season starts - and cooling season ends. The default is 270.
    heating_season_end : int, optional
        Day( julian day, number of day in year ), when heating season ends - and cooling season starts. The default is 150
    """
    # Inputs
    ResidenceTemperature = "ResidenceTemperature"

    # Outputs
    l2_HeatPumpSignal = "l2_HeatPumpSignal"
    l2_HeatPumpCompulsory = "l2_HeatPumpCompulsory"
    
    # #Forecasts
    # HeatPumpLoadForecast = "HeatPumpLoadForecast"

    # Similar components to connect to:
    # 1. Building
    # 2. HeatPump
    
    @utils.measure_execution_time
    def __init__( self, 
                  my_simulation_parameters : SimulationParameters,
                  T_min_heating : float = 20.0,
                  T_max_heating : float = 22.0,
                  T_min_cooling : float = 23.0,
                  T_max_cooling : float = 25.0,
                  heating_season_begin : int = 270,
                  heating_season_end : int = 150 ):
        super().__init__( "L2_Controller", my_simulation_parameters = my_simulation_parameters )
        self.build( T_min_heating, T_max_heating, T_min_cooling, T_max_cooling, heating_season_begin, heating_season_end )

        self.ResidenceTemperatureC: cp.ComponentInput = self.add_input(     self.ComponentName,
                                                                            self.ResidenceTemperature,
                                                                            LoadTypes.Temperature,
                                                                            Units.Celsius,
                                                                            mandatory = True )
        self.add_default_connections( Building, self.get_building_default_connections( ) )
        
        #Component outputs
        self.l2_HeatPumpSignalC: cp.ComponentOutput = self.add_output( self.ComponentName,
                                                                       self.l2_HeatPumpSignal,
                                                                       LoadTypes.OnOff,
                                                                       Units.binary )
        self.l2_HeatPumpCompulsoryC: cp.ComponentOutput = self.add_output( self.ComponentName,
                                                                           self.l2_HeatPumpCompulsory,
                                                                           LoadTypes.Compulsory,
                                                                           Units.binary )
        
    def get_building_default_connections( self ):
        log.information("setting building default connections in L2 Controller")
        connections = [ ]
        building_classname = Building.get_classname( )
        connections.append( cp.ComponentConnection( L2_Controller.ResidenceTemperature, building_classname, Building.TemperatureMean ) )
        return connections

    def build( self, T_min_heating, T_max_heating, T_min_cooling, T_max_cooling, heating_season_begin, heating_season_end ):
        
        self.T_min_heating = T_min_heating
        self.T_max_heating = T_max_heating
        self.T_min_cooling = T_min_cooling
        self.T_max_cooling = T_max_cooling
        self.heating_season_begin = heating_season_begin * 24 * 3600 / self.my_simulation_parameters.seconds_per_timestep
        self.heating_season_end = heating_season_end * 24 * 3600 / self.my_simulation_parameters.seconds_per_timestep
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
            pass
        
        T_control = stsv.get_input_value( self.ResidenceTemperatureC )
        
        self.state.is_first_iteration( timestep )
        self.previous_state.is_first_iteration( timestep )
        
        #check out during cooling season
        if timestep < self.heating_season_begin and timestep > self.heating_season_end:
            if T_control > self.T_max_cooling:
                #start cooling if temperature exceeds upper limit
                self.state.activate( )
                self.previous_state.activate( )

            elif T_control < self.T_min_cooling:
                #stop cooling if temperature goes below lower limit
                self.state.deactivate( )
                self.previous_state.deactivate( )
            else:
                if self.previous_state.mandatory == 1:
                    self.state = self.previous_state.clone( )
                    
        #check out during heating season
        else:
            if T_control > self.T_max_heating:
                #stop heating if temperature exceeds upper limit
                self.state.deactivate( )
                self.previous_state.deactivate( )

            elif T_control < self.T_min_heating:
                #start heating if temperature goes below lower limit
                self.state.activate( )
                self.previous_state.activate( )
                if self.previous_state.mandatory == 1:
                    self.state = self.previous_state.clone( )
        
        stsv.set_output_value( self.l2_HeatPumpSignalC, self.state.state )
        stsv.set_output_value( self.l2_HeatPumpCompulsoryC, self.state.mandatory )

    def prin1t_outpu1t(self, t_m, state):
        log.information("==========================================")
        log.information("T m: {}".format(t_m))
        log.information("State: {}".format(state))