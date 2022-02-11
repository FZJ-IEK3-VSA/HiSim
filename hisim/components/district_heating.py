
# Import packages from standard library or the environment e.g. pandas, numpy etc.
from dataclasses import dataclass

# Import modules from HiSim
from hisim import component as cp
from hisim import loadtypes as lt
import copy as copy
from hisim.simulationparameters import SimulationParameters

__authors__ = "Johanna Ganglbauer - johanna.ganglbauer@4wardenergy.at"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"


@dataclass
class DistrictHeatingState:
    """
    This data class saves the state of the simulation results.
    """
    PowerDistrictHeating:   float = 0
    Iteration:              int = 0
    Timestep :              int = 0
            
    def update_state( self, power : float ):
        """
        Updates state of controller for district heating
        
        Parameters
        ----------
        power : float
            Power transfered from radiator to house.
        """
        
        self.PowerDistrictHeating = power
        self.Iteration += 1 
        
    def check_iteration( self, timestep ):
        #track number of iteration in each time step
        if self.Timestep == timestep:
            pass
        else:
            self.Timestep = timestep
            self.Iteration = 0
        

class DistrictHeating( cp.Component ):
    """
    District heating implementation. District Heating transmitts heat with given efficiency.
    District heating is controlled to reach the set point temperature in every time step up to a tolerance.
    This is realized by an iterative control adopting the power step with decreasing values.
    """
    
    # Inputs
    signal =                     "signal" #0 if switched off, 1 if switched on
    
    # Outputs
    ThermalEnergyDelivered =    "ThermalEnergyDelivered"
    PowerDistrictHeating =      "PowerDistrictHeating"

    def __init__( self,my_simulation_parameters: SimulationParameters , max_power: int, min_power : int, efficiency : float ):
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
        
        super( ).__init__( name = 'DistrictHeating', my_simulation_parameters=my_simulation_parameters )
        
        #introduce parameters of district heating
        self.build( max_power = max_power,
                    min_power = min_power,
                    efficiency = efficiency )
        
        #initialize state and previous state
        self.state =            DistrictHeatingState( )
        self.state_previous =   DistrictHeatingState( )
        
        # Inputs - Mandatories
        self.signalC : cp.ComponentInput = self.add_input(  self.ComponentName,
                                                            self.signal,
                                                            lt.LoadTypes.Heating,
                                                            lt.Units.Watt,
                                                            mandatory = True )
        
        # Outputs 
        self.thermal_energy_delivered : cp.ComponentOutput = self.add_output(   self.ComponentName,
                                                                                self.ThermalEnergyDelivered,
                                                                                lt.LoadTypes.Heating,
                                                                                lt.Units.Watt )
        
        self.PowerDistrictHeatingOutput: cp.ComponentOutput = self.add_output(    self.ComponentName,
                                                                            self.PowerDistrictHeating,
                                                                            lt.LoadTypes.Heating,
                                                                            lt.Units.Watt )

    def build( self, max_power: int, min_power: int, efficiency : float ):
        """
        Assigns parameters of oil heater to class, and writes them to the report
        """
        
        #Parameters:
        self.max_power =    max_power
        self.min_power =    min_power
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
        lines.append( "MaxPower: {:4.0f} kW".format( ( self.max_power ) * 1E-3 ) )
        lines.append( "MinPower: {:4.0f} kW".format( ( self.min_power ) * 1E-3 ) )
        lines.append( 'Efficiency : {:4.0f} %'.format( ( self.efficiency ) * 100 ) )
        return lines
    
    def i_save_state( self ):
        self.previous_state = copy.deepcopy( self.state )

    def i_restore_state(self):
        self.state = copy.deepcopy( self.previous_state )

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues ):
        pass
    
    def i_simulate( self, timestep: int, stsv: cp.SingleTimeStepValues,  force_convergence: bool ):
        """
        Performs the simulation of the district heating model.
        """ 
        
        # Load control signal signalC value
        signalC = stsv.get_input_value( self.signalC )
        
        # write values for output time series
        stsv.set_output_value( self.thermal_energy_delivered, signalC * self.efficiency )
        stsv.set_output_value( self.PowerDistrictHeatingOutput, signalC )
        
class DistrictHeatingController( cp.Component ):
    """
    District Heating Controller. It takes power from the previous time step and adopts power to meet the heating needs.
    
    Parameters
    --------------
    max_power : float
        maximal heating temperature
    min_power : float
        minimal heating temperature
    t_air_heating : float
        set temperature for heating
    tol : float, optional
        tolerance of set_temperature in °C. The default is 1e-3°C
        
    """
    # Inputs
    TemperatureMean = "Residence Temperature"

    # Outputs
    signal = "signal"

    # Similar components to connect to:
    # 1. Building

    def __init__( self,
                  my_simulation_parameters: SimulationParameters,
                  max_power : float = 6000,
                 min_power : float = 1000,
                 t_air_heating: float = 20.0,
                 tol : float = 1e-3 ):
        
        super().__init__( name="DistrictHeatingController", my_simulation_parameters=my_simulation_parameters )
        
        self.build( max_power = max_power,
                    min_power = min_power,
                    t_air_heating = t_air_heating,
                    tol = tol )

        #inputs
        self.t_mC: cp.ComponentInput = self.add_input( self.ComponentName,
                                                        self.TemperatureMean,
                                                        lt.LoadTypes.Temperature,
                                                        lt.Units.Celsius,
                                                        True )
        
        #outputs
        self.signalC = self.add_output( self.ComponentName,
                                        self.signal,
                                        lt.LoadTypes.Heating,
                                        lt.Units.Watt )

    def build( self, max_power, min_power, t_air_heating, tol ):
        self.max_power = max_power
        self.min_power = min_power
        self.t_air_heating = t_air_heating
        self.tol = tol
        
        #initialize control mode
        self.state = DistrictHeatingState()
        self.state_previous = DistrictHeatingState( )

    def i_save_state(self):
        self.state = self.state_previous

    def i_restore_state(self):
        self.state_previous = self.state

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues,  force_convergence: bool  ):
        
        # check demand, and change state of self.has_heating_demand, and self._has_cooling_demand
        if force_convergence:
            pass
        else:
            # Retrieves inputs
            t_m_old = stsv.get_input_value( self.t_mC )
            
            # set Iteration to zero if timestep is new
            self.state.check_iteration( timestep = timestep )
            
            #in first iteration heating is either max or off
            if self.state.Iteration == 0:
                #turn off heating when above set point
                if t_m_old > self.t_air_heating:
                    DistrictHeatingPower:float = 0
                #turn on heating with maximum if below set point
                else:
                    DistrictHeatingPower = self.max_power
                    
            #in second iteration heating is turned to min if max was to much
            elif self.state.Iteration == 1:
                #go to minimum of heating, if temperature is above set temperature and heating was maximal before
                if t_m_old > self.t_air_heating:
                    if self.state.PowerDistrictHeating == self.max_power:
                        DistrictHeatingPower = self.min_power
                    else:
                        DistrictHeatingPower = self.state.PowerDistrictHeating
                else:
                    DistrictHeatingPower = self.state.PowerDistrictHeating
            
            #in third iteration stay at 0 or max and balance in other cases
            elif self.state.Iteration == 2:
                #stay at 0, max or min if it is already in tolerance
                if self.state.PowerDistrictHeating in [ 0, self.max_power ] or ( t_m_old > ( self.t_air_heating - self.tol ) and t_m_old < ( self.t_air_heating + self.tol ) ):
                    DistrictHeatingPower = self.state.PowerDistrictHeating
                else:
                    #turn off if temperatue is above set value with minimal heating power
                    if t_m_old > self.t_air_heating:
                        DistrictHeatingPower = 0
                    #increase if temperatue is below set value with minimal heating power
                    else:
                        DistrictHeatingPower = self.state.PowerDistrictHeating + ( self.max_power - self.min_power ) / ( 2 * ( self.state.Iteration - 1 ) )
            else:
                #stay at 0, max if it was set before and stay at value if it is already in tolerance
                if self.state.PowerDistrictHeating in [ 0, self.max_power ] or ( t_m_old > ( self.t_air_heating - self.tol ) and t_m_old < ( self.t_air_heating + self.tol ) ):
                    DistrictHeatingPower = self.state.PowerDistrictHeating
                else:
                    #decrease if temperature is above set value with previous heating power
                    if t_m_old > self.t_air_heating:
                        DistrictHeatingPower = self.state.PowerDistrictHeating - ( self.max_power - self.min_power ) / ( 2 * ( self.state.Iteration - 1 ) )
                    #increase if temperature is below set value with previous heating power
                    else:
                        DistrictHeatingPower = self.state.PowerDistrictHeating + ( self.max_power - self.min_power ) / ( 2 * ( self.state.Iteration - 1 ) )

        self.state.update_state( power = DistrictHeatingPower )

        #print(state)
        stsv.set_output_value(self.signalC, DistrictHeatingPower )

    def print_output(self, t_m, state):
        print("==========================================")
        print("T m: {}".format(t_m))
        print("State: {}".format(state))