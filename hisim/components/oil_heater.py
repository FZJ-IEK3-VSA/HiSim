
# Import packages from standard library or the environment e.g. pandas, numpy etc.
from dataclasses import dataclass

# Import modules from HiSim
from hisim import component as cp
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim.components.building import Building
from hisim.components.weather import Weather
from hisim import log
__authors__ = "Johanna Ganglbauer - johanna.ganglbauer@4wardenergy.at"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"


@dataclass
class OilheaterState:
    """
    This data class saves the state of the simulation results.
    """
    
    def __init__( self, time_on : int = 0, time_off : int = 0, full_medium_off :   int = 0 ):
        """
        Parameters
        ----------
        time_on : int, optional
            Timesteps the Oilheater has been switched on. The default is 0.
        time_off : int, optional
            Timesteps the Oilheater has been switched off. The default is 0.
        full_medium_off : int, optional
            Control State: 2 is switched on, 1 is switched on medium level and 0 is turned off. The default is 0.
        """
        
        self.time_on = time_on
        self.time_off = time_off
        self.full_medium_off = full_medium_off

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
        if stateC == 2:
            self.time_on = self.time_on + seconds_per_timestep
            self.time_off = 0
            self.full_medium_off = 2
            
        elif stateC == 1:
            self.time_on = self.time_on + seconds_per_timestep
            self.time_off = 0
            self.full_medium_off = 1
            
        else:
            self.time_off = self.time_off + seconds_per_timestep
            self.time_on = 0
            self.full_medium_off = 0
            
    def clone( self ):
        return OilheaterState( self.time_on, self.time_off, self.full_medium_off )

class OilHeater( cp.Component ):
    """
    Oil heater implementation. Oil heater heats up oil transmittant with electricity ( efficiency = 1 ).
    The oil heater can be switched on with maximum power, with medium power, or switched off - there are three power levels.
    """
    
    # Inputs
    StateC =                     "StateC" #0 if switched off, 1 if switched on with medium power, 2 if switched on with maximum power
    
    # Outputs
    ThermalEnergyDelivered =    "ThermalEnergyDelivered"
    ElectricityOutput =         "ElectricityOutput" #this definition is useful in sumbuilder, but of corse it is electricity input needed
    NumberOfCycles =            "NumberOfCycles"

    def __init__( self, my_simulation_parameters: SimulationParameters , max_power: int, min_off_time : int, min_on_time : int,  ):
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
        
        super( ).__init__( name = 'OilHeater', my_simulation_parameters=my_simulation_parameters )
        
        #introduce parameters of oil heater
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
        self.add_default_connections( OilHeaterController, self.get_controller_default_connections( ) )
        
    def get_controller_default_connections( self ):
        log.information("setting oil heater default connections")
        connections = [ ]
        controller_classname = OilHeaterController.get_classname( )
        connections.append( cp.ComponentConnection( OilHeater.StateC, controller_classname, OilHeaterController.StateC ) )
        return connections

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
        self.previous_state = self.state.clone( )

    def i_restore_state(self):
        self.state = self.previous_state.clone( )

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues ):
        pass
    
    def i_simulate( self, timestep: int, stsv: cp.SingleTimeStepValues,force_convergence: bool ):
        """
        Performs the simulation of the oil heater.
        """ 
        
        # Load control signal stateC value ( 1 means on 0 means off )
        stateC = stsv.get_input_value( self.stateC )
        
        # Overwrite control signal stateC to realize minimum time on or time off
        if self.state.full_medium_off == 2 and self.state.time_on < self.min_on_time * 60 :
            stateC = 2
        if self.state.full_medium_off == 1 and self.state.time_on < self.min_on_time * 60 :
            stateC = 1
        if self.state.full_medium_off == 0 and self.state.time_off < self.min_off_time * 60:
            stateC = 0
            
        #update state class accordingly
        self.state.update_state( stateC = stateC , seconds_per_timestep = self.my_simulation_parameters.seconds_per_timestep )
        
        # write values for output time series
        stsv.set_output_value( self.thermal_energy_delivered, self.max_power * self.state.full_medium_off / 2 )
        stsv.set_output_value( self.electricity_output, self.max_power * self.state.full_medium_off / 2 )
        
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
    TemperatureOutside = "Temperature Outside"

    # Outputs
    StateC = "StateC"

    # Similar components to connect to:
    # 1. Building

    def __init__( self,
                  my_simulation_parameters: SimulationParameters,
                  t_air_heating: float = 20.0,
                  offset: float = 2.0 ):
        
        super().__init__( "OilheaterController", my_simulation_parameters=my_simulation_parameters )
        
        self.build( t_air_heating = t_air_heating,
                    offset = offset )

        #inputs
        self.t_mC: cp.ComponentInput = self.add_input( self.ComponentName,
                                                    self.TemperatureMean,
                                                    lt.LoadTypes.Temperature,
                                                    lt.Units.Celsius,
                                                    True )
        self.t_outC: cp.ComponentInput = self.add_input(self.ComponentName,
                                                     self.TemperatureOutside,
                                                     lt.LoadTypes.Any,
                                                     lt.Units.Celsius,
                                                     True) 
        #outputs
        self.stateC:cp.ComponentOutput = self.add_output( self.ComponentName,
                                      self.StateC,
                                      lt.LoadTypes.Any,
                                      lt.Units.Any)
        
        self.add_default_connections( Weather, self.get_weather_default_connections( ) )
        self.add_default_connections( Building, self.get_building_default_connections( ) )
        
    def get_weather_default_connections( self ):
        log.information("setting weather default connections in OilHeaterController")
        connections = [ ]
        weather_classname = Weather.get_classname( )
        connections.append( cp.ComponentConnection( OilHeaterController.TemperatureOutside, weather_classname, Weather.TemperatureOutside ) )
        return connections
    
    def get_building_default_connections( self ):
        log.information("setting building default connections in OilHeaterController")
        connections = [ ]
        building_classname = Building.get_classname( )
        connections.append( cp.ComponentConnection( OilHeaterController.TemperatureMean, building_classname, Building.TemperatureMean ) )
        return connections

    def build( self, t_air_heating, offset ):
        
        #initialize control mode
        self.full_medium_off = 0
        self.previous_full_medium_off = 0

        # Configuration
        self.t_set_heating = t_air_heating
        self.offset = offset

    def i_save_state(self):
        self.previous_full_medium_off = self.full_medium_off

    def i_restore_state(self):
        self.full_medium_off = self.previous_full_medium_off

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues,  force_convergence: bool  ):
        
        # check demand, and change state of self.has_heating_demand, and self._has_cooling_demand
        if force_convergence:
            stateC = 0
            pass
        else:
            # Retrieves inputs
            t_m_old = stsv.get_input_value( self.t_mC )
            T_out =   stsv.get_input_value( self.t_outC )
            T_diff = t_m_old - T_out
    
            minimum_heating_set_temp = self.t_set_heating - self.offset
            heating_set_temp = self.t_set_heating
            maximum_heating_set_temp = self.t_set_heating + self.offset
            
            #below comfortband heating goes to maximum
            if t_m_old < minimum_heating_set_temp:
                stateC = 2
                
            #in lower half of comfort band 
            elif t_m_old >= minimum_heating_set_temp and t_m_old < heating_set_temp:
                #heating goes to maximum on cold days
                if T_diff >= 20:
                    stateC = 2
                #heating is turned on medium on medium days
                elif T_diff < 20 and T_diff >= 10:
                    stateC = 1
                #heating is not touched on warm days
                else:
                    stateC = 0
                    
            #in upper half of comfort band
            elif t_m_old >= heating_set_temp and t_m_old < maximum_heating_set_temp:
                if T_diff >= 20:
                    stateC = 1
                else:
                    stateC = 0
            
            #room temperature exceeds comfort band
            else:
                stateC = 0

        #log.information(state)
        stsv.set_output_value(self.stateC, stateC )

    def prin1t_output(self, t_m, state):
        log.information("==========================================")
        log.information("T m: {}".format(t_m))
        log.information("State: {}".format(state))