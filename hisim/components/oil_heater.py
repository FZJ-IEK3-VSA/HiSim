
# Import packages from standard library or the environment e.g. pandas, numpy etc.
from copy import deepcopy
from dataclasses import dataclass

# Import modules from HiSim
import component as cp
import loadtypes as lt
import copy as copy

__authors__ = "Tjarko Tjaden, Kai Rösken"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Johanna Ganglbauer"
__email__ = "johanna.ganglbauer@4wardenergy.at"
__status__ = "development"


@dataclass
class OilheaterState:
    """
    This data class saves the state of the simulation results.
    """
    time_on: int = 0
    time_off: int = 0
    on_off: int = 0

    def update_state( self, stateC, seconds_per_timestep ):
        """
        Updates state.
        
        Parameters
        ----------
        stateC : int
            Is 0 if Oilheater is switched off and 1 if Oilheater is switched on.
        seconds_per_timestep : int
            Seconds per iteration.
        """
        
        # on switch
        if stateC == 1:
            self.time_on = self.time_on + seconds_per_timestep
            self.time_off = 0
            self.on_off = 1
            
        else:
            self.time_off = self.time_off + seconds_per_timestep
            self.time_on = 0
            self.on_off = 0

class OilHeater( cp.Component ):
    """
    Oil heater implementation. Oil heater heats up oil transmittant with electricity ( efficiency = 1 ).
    The oil heater can be either switched on or switched off, there is no intermediate level.
    """
    
    # Inputs
    StateC =                     "StateC" #0 if switched off, 1 if switched on
    
    # Outputs
    ThermalEnergyDelivered =    "ThermalEnergyDelivered"
    ElectricityOutput =         "ElectricityOutput"
    NumberOfCycles =            "NumberOfCycles"

    def __init__( self, max_power: int, min_off_time : int, min_on_time : int ):
        """
        Parameters
        ----------
        max_power: int
            Power of oil heater when turned on.
        min_off_time : int
            Minimal time oil heater is switched off
        min_on_time : int
            Minimal time oil heater is switched on
        """
        
        super( ).__init__( name = 'OilHeater' )
        
        #introduce parameters of heat pump
        self.build( max_power = max_power,
                    min_off_time = min_off_time,
                    min_on_time = min_on_time )
        
        #initialize state and previous state
        self.state = OilheaterState( )
        self.state_previous = OilheaterState( )
        
        # Inputs - Mandatories
        self.stateC: cp.ComponentInput = self.add_input(    self.ComponentName,
                                                            self.StateC,
                                                            lt.LoadTypes.Any,
                                                            lt.Units.Any,
                                                            mandatory = True )
        
        # Outputs 
        self.thermal_energy_delivered : cp.ComponentOutput = self.add_output(   self.ComponentName,
                                                                                self.ThermalEnergyDelivered,
                                                                                lt.LoadTypes.Heating,
                                                                                lt.Units.Watt )
        
        self.electricity_output: cp.ComponentOutput = self.add_output(      self.ComponentName,
                                                                            self.ElectricityOutput,
                                                                            lt.LoadTypes.Electricity,
                                                                            lt.Units.Watt )

    def build( self, max_power: int, min_on_time : int, min_off_time : int ):
        """
        Assigns parameters of oil heater to class, and writes them to the report
        """
        
        #Parameters:
        self.max_power =    max_power
        self.min_on_time =  min_on_time
        self.min_off_time = min_off_time
        
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
        lines.append( "Name: {}".format( "Oil heater" ) )
        lines.append( "Power: {:4.0f} kW".format( ( self.max_power ) * 1E-3 ) )
        return lines
    
    def i_save_state( self ):
        self.previous_state = copy.deepcopy( self.state )

    def i_restore_state(self):
        self.state = copy.deepcopy( self.previous_state )

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues ):
        pass
    
    def i_simulate( self, timestep: int, stsv: cp.SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool ):
        """
        Performs the simulation of the heat pump model.
        """ 
        
        # Load control signal stateC value ( 1 means on 0 means off )
        stateC = stsv.get_input_value( self.stateC )
        
        # Overwrite control signal stateC to realize minimum time on or time off
        if self.state.on_off == 1 and self.state.time_on < self.min_on_time * 60 :
            stateC = 1
        if self.state.on_off == 0 and self.state.time_off < self.min_off_time * 60:
            stateC = 0
            if timestep < 100:
                print( self.state.on_off, self.state.time_off, self.min_off_time * 60 )
            
        #update state class accordingly
        self.state.update_state( stateC = stateC , seconds_per_timestep = seconds_per_timestep )
        
        # write values for output time series
        stsv.set_output_value( self.thermal_energy_delivered, self.max_power * self.state.on_off )
        stsv.set_output_value( self.electricity_output, self.max_power * self.state.on_off )
        
class OilHeaterController( cp.Component ):
    """
    Oilheater Controller. It takes data from other
    components and sends signal to the Oilheater for
    activation or deactivation.

    Parameters
    --------------
    t_air_heating: float
        Minimum comfortable temperature for residents
    offset: float
        Temperature offset to compensate the hysteresis
        correction for the building temperature change
    """
    
    # Inputs
    TemperatureMean = "Residence Temperature"
    ElectricityInput = "ElectricityInput"

    # Outputs
    StateC = "StateC"

    # Similar components to connect to:
    # 1. Building

    def __init__( self,
                 t_air_heating: float = 20.0,
                 offset: float = 2.0 ):
        
        super().__init__( "HeatPumpController" )
        
        self.build( t_air_heating = t_air_heating,
                    offset = offset )

        self.t_mC: cp.ComponentOutput = self.add_input(self.ComponentName,
                                                    self.TemperatureMean,
                                                    lt.LoadTypes.Temperature,
                                                    lt.Units.Celsius,
                                                    True)
        self.electricity_inputC = self.add_input(self.ComponentName,
                                                 self.ElectricityInput,
                                                 lt.LoadTypes.Electricity,
                                             lt.Units.Watt,
                                                 False)
        self.stateC = self.add_output(self.ComponentName,
                                      self.StateC,
                                      lt.LoadTypes.Any,
                                      lt.Units.Any)

    def build( self, t_air_heating, offset ):
        
        #initialize control mode
        self.on_off = 0
        self.previous_on_off = 0

        # Configuration
        self.t_set_heating = t_air_heating
        self.offset = offset

    def i_save_state(self):
        self.previous_on_off = self.on_off

    def i_restore_state(self):
        self.on_off = self.previous_on_off

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool  ):
        
        # check demand, and change state of self.has_heating_demand, and self._has_cooling_demand
        if force_convergence:
            stateC = 0
        else:
            # Retrieves inputs
            t_m_old = stsv.get_input_value( self.t_mC )
            electricity_input = stsv.get_input_value( self.electricity_inputC )
    
            minimum_heating_set_temp = self.t_set_heating - self.offset
            heating_set_temp = self.t_set_heating
        
            if t_m_old < minimum_heating_set_temp:
                stateC = 1
            elif t_m_old < heating_set_temp and electricity_input > 0:
                stateC = 1
            else:
                stateC = 0

        #print(state)
        stsv.set_output_value(self.stateC, stateC )

    def print_output(self, t_m, state):
        print("==========================================")
        print("T m: {}".format(t_m))
        print("State: {}".format(state))