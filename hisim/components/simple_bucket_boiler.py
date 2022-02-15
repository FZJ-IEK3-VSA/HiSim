# Generic/Built-in
import numpy as np
import copy
import matplotlib
import seaborn
from math import pi

# Owned
import hisim.component as cp
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim.components.occupancy import Occupancy

seaborn.set(style='ticks')
font = {'family' : 'normal',
        'size'   : 24}

matplotlib.rc('font', **font)

__authors__ = "Johanna Ganglbauer - johanna.ganglbauer@4wardenergy.at"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"

class BoilerState:
    """
    This data class saves the state of the simulation results.
    """

    def __init__( self, volume_in_l:float, temperature_in_K:float ):
        """
        Parameters
        ----------
        volume : float
            Volume of boiler in liters.
        """
        self.temperature_in_K: float = temperature_in_K
        self.volume_in_l = volume_in_l

    def clone(self):
        return BoilerState(self.volume_in_l, self.temperature_in_K)


    def energy_from_temperature( self ) -> float:
        "converts temperature of storage (K) into energy contained in storage (kJ)"
        #0.977 is the density of water in kg/l
        #4.182 is the specific heat of water in kJ / ( K * kg )
        return self.temperature_in_K * self.volume_in_l * 0.977 * 4.182 #energy given in kJ


    def set_temperature_from_energy( self, energy_in_kJ):
     #   "converts energy contained in storage (kJ) into temperature (K)"
        #0.977 is the density of water in kg/l
        #4.182 is the specific heat of water in kJ / ( K * kg )
        self.temperature_in_K = energy_in_kJ / ( self.volume_in_l * 0.977 * 4.182 ) #temperature given in K
        
    
class Boiler( cp.Component ):
    """Implementation of a simple Boilder model with constant heat loss.
    The boiler is modelled as an energy bucket.
    
    Parameters
    ----------
    definition : string or list
        String selects predefined boiler types - currently availble option is '0815-boiler' or 'hydrogen-boiler'. 
        Alternatively, list defines boiler by the following parameters:
        [ volume in [ liters ], 
         surface in [ square metres ], 
         u-value in [ W / ( m^2 K ) ], 
         temperature of hot water leaving the hot water storage in [ Kelvin ],
         temperature of cold water to be heated in boiler in [ Kelvin ],
         power of poiler in [ Watt ],
         efficincy [ p. u. ]
         ]
    fuel : string
        either "electricity" or "hydrogen"
    """
    # Inputs
    State = "State" #0/1 meaining off/on
    WaterConsumption = "WaterConsumption" #l

    # obligatory Outputs
    StorageTemperature = "StorageTemperature"

    #availble Outputs
    ElectricityOutput = "ElectricityOutput"
    HydrogenOutput = "HydrogenOutput"
    
    def __init__( self,my_simulation_parameters: SimulationParameters,  definition = '0815-boiler', fuel = 'electricity' ):

        super().__init__( "Boiler", my_simulation_parameters=my_simulation_parameters )
        self.efficiency: float = 0
        self.build( definition, fuel )
        
        #initialize Boiler State
        self.state = BoilerState( volume_in_l = self.volume, temperature_in_K=273+50 )
        self.previous_state = self.state.clone()
        
        #inputs
        self.WaterConsumptionC : cp.ComponentInput = self.add_input( self.ComponentName,
                                                           self.WaterConsumption,
                                                           lt.LoadTypes.WarmWater,
                                                           lt.Units.Liter, 
                                                           mandatory = True )
        
        self.stateC : cp.ComponentInput = self.add_input( self.ComponentName,
                                                           self.State,
                                                           lt.LoadTypes.Any,
                                                           lt.Units.Any,
                                                           mandatory = True )
        
        #Outputs
        self.StorageTemperatureC : cp.ComponentOutput = self.add_output( self.ComponentName,
                                                              self.StorageTemperature,
                                                              lt.LoadTypes.Temperature,
                                                              lt.Units.Kelvin )
        if self.fuel == 'electricity':
            self.electricity_output_c = self.add_output( self.ComponentName,
                                                         self.ElectricityOutput,
                                                         lt.LoadTypes.Electricity,
                                                         lt.Units.Watt )
        elif self.fuel == 'hydrogen':
            self.hydrogen_output_c = self.add_output( self.ComponentName,
                                                      self.HydrogenOutput,
                                                      lt.LoadTypes.Hydrogen,
                                                      lt.Units.kg_per_sec )
        else:
            raise Exception(" The fuel ", str( self.fuel ), " is not available. Choose either 'electricity' or 'hydrogen'. " )
            
        self.add_default_connections( Occupancy, self.get_occupancy_default_connections( ) )
        self.add_default_connections( BoilerController, self.get_controller_default_connections( ) )
        
    def get_occupancy_default_connections( self ):
        print("setting occupancy default connections")
        connections = [ ]
        occupancy_classname = Occupancy.get_classname( )
        connections.append( cp.ComponentConnection( Boiler.WaterConsumption, occupancy_classname, Occupancy.WaterConsumption ) )
        return connections
    
    def get_controller_default_connections( self ):
        print("setting controller default connections")
        connections = [ ]
        controller_classname = BoilerController.get_classname( )
        connections.append( cp.ComponentConnection( Boiler.State, controller_classname, BoilerController.State ) )
        return connections
    
    def build( self, definition, fuel ):
        if type( definition ) == str:
            if definition == '0815-boiler':
                self.volume = 200 #l
                self.surface = 1.2 #m^3
                self.u = 0.36 #W m^(-2) K^(-1)
                self.T_warmwater = 50 + 273 #K
                self.T_drainwater = 10 + 273 #K
                self.P_on = 2400 #W
                self.efficiency = 1
                
            elif definition == 'hydrogen-boiler':
                self.volume = 200 #l
                self.surface = 1.2 #m^3
                self.u = 0.36 #W m^(-2) K^(-1)
                self.T_warmwater = 50 + 273 #K
                self.T_drainwater = 10 + 273 #K
                self.P_on = 2400 #W
                self.efficiency = 0.9
        
        else:
            self.volume, self.surface, self.u, self.T_warmwater, self.T_drainwater, self.P_on = definition
            
        self.fuel = fuel    
            
        self.write_to_report( )

    def write_to_report(self):
        lines = []
        lines.append("Name: {}".format("electric Boiler"))
        lines.append("Power: {:4.0f} kW".format( ( self.P_on ) * 1E-3 ) )
        lines.append( "Volume: {:4.0f} l".format( self.volume ) )
        return lines

    def i_save_state( self ):
        self.previous_state = self.state.clone()

    def i_restore_state( self ):
        self.state = self.previous_state.clone()

    def i_doublecheck( self, timestep: int, stsv: cp.SingleTimeStepValues ):
        pass

    def i_simulate( self, timestep: int, stsv: cp.SingleTimeStepValues,  force_convergence: bool ):
        
        # Retrieves inputs
        WW_consumption = stsv.get_input_value( self.WaterConsumptionC )
        signal = stsv.get_input_value( self.stateC )

        #constant heat loss of heat storage with the assumption that environment has 20°C = 293 K -> based on energy balance in kJ
        #heat loss due to hot water consumption -> base on energy balance in kJ
        #heat gain due to electrical/hydrogen heating of boiler -> based on energy balance in kJ
        #0.977 density of water in kg/l
        #4.182 specific heat of water in kJ K^(-1) kg^(-1)
        #1e-3 conversion J to kJ
        energy = self.state.energy_from_temperature()
        new_energy = energy - ( self.state.temperature_in_K - 293 ) * self.surface * self.u * self.my_simulation_parameters.seconds_per_timestep * 1e-3 \
                                              - WW_consumption * ( self.T_warmwater - self.T_drainwater ) * 0.977 * 4.182 \
                                              + signal * self.P_on * self.efficiency * self.my_simulation_parameters.seconds_per_timestep * 1e-3
                                              
        #convert new energy to new temperature
        self.state.set_temperature_from_energy( new_energy)
        
        #save outputs
        stsv.set_output_value( self.StorageTemperatureC, self.state.temperature_in_K )
        
        if self.fuel == 'electricity':
             stsv.set_output_value( self.electricity_output_c, self.P_on * signal )
        else:
            #heat of combustion hydrogen: 141.8 MJ / kg; conversion W = J/s to kg / s
             stsv.set_output_value( self.hydrogen_output_c, ( self.P_on * signal / 1.418 ) * 1e-8 )

class BoilerController(cp.Component):
    """
    Boiler on/off controller. It activates boiler, when temperature falls below
    certain threshold and deactivates it if temperature exceeds limit.

    Parameters
    --------------
    t_water_min: float
        Minimum temperature of water in hot water storage -> in Kelvin
    t_water_max: float
        Maximum temperature of water in hot water storage -> in Kelvin
    P_on : float
        Power of boiler -> in Watt
    smart : bool
        One if boiler enforces heating in case of surplus from PV and 0 if it does not react to the situaltion.
    """
    # Inputs
    StorageTemperature = "Storage Temperature"
    ElectricityInput = "ElectricityInput"

    # Outputs
    State = "State"

    # Similar components to connect to:
    # 1. Building

    def __init__(self,
                 my_simulation_parameters: SimulationParameters,
        t_water_min : float = 273.0 + 40.0,
                  t_water_max : float = 273.0 + 80.0,
                  P_on :        int =   2400,
                  smart :       int = 0 ):
        
        super().__init__( name="BoilerController", my_simulation_parameters=my_simulation_parameters )
        
        self.build( t_water_min = t_water_min,
                    t_water_max = t_water_max,
                    P_on = P_on,
                    smart = smart )

        #input
        self.StorageTemperatureC: cp.ComponentInput = self.add_input( self.ComponentName,
                                                           self.StorageTemperature,
                                                           lt.LoadTypes.Temperature,
                                                           lt.Units.Kelvin,
                                                           mandatory = True )
        
        if smart == 1:
            self.electricity_inputC: cp.ComponentInput = self.add_input( self.ComponentName,
                                                                        self.ElectricityInput,
                                                                        lt.LoadTypes.Electricity,
                                                                        lt.Units.Watt,
                                                                        mandatory = True )
                
        #output
        self.stateC = self.add_output( self.ComponentName,
                                       self.State,
                                       lt.LoadTypes.Any,
                                       lt.Units.Any )
        
        self.add_default_connections( Boiler, self.get_boiler_default_connections( ) )
    
    def get_boiler_default_connections( self ):
        print("setting boiler default connections")
        connections = [ ]
        boiler_classname = Boiler.get_classname( )
        connections.append( cp.ComponentConnection( BoilerController.StorageTemperature, boiler_classname, Boiler.StorageTemperature ) )
        return connections

    def build( self, t_water_min, t_water_max, P_on, smart ):
        
        # state
        self.signal = 0
        self.previous_signal = 0

        # Configuration
        self.t_water_min = t_water_min
        self.t_water_max = t_water_max
        self.P_on = P_on
        self.smart = smart


    def i_save_state(self):
        self.previous_signal = self.signal

    def i_restore_state(self):
        self.signal = self.previous_signal

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues,  force_convergence: bool):

        if force_convergence: # please stop oscillating!
            return


        # Retrieves inputs
        T_control = stsv.get_input_value( self.StorageTemperatureC )
        
        if self.smart == 1:
            E_control = stsv.get_input_value( self.electricity_inputC )
            #check if surplus energy is available and boiler still can be heated ( 10 % of the storage capacity are still empty )
            if E_control * ( -1 ) > self.P_on and T_control < self.t_water_max - ( self.t_water_max - self.t_water_min ) * 0.1:
                #switch on boiler if surplus is available
                self.signal = 1
                #violently access previous timestep to avoid oscillation between 0 and 1 ( decision is based on decision of previous time step )
                self.previous_signal = 1

        if T_control > self.t_water_max:
            #stop heating if temperature exceeds upper limit
            self.signal = 0
            #violently access previous timestep to avoid oscillation between 0 and 1 (decision is based on decision of previous time step)
            self.previous_signal = 0
        if T_control < self.t_water_min:
            #start heating if temperature goes below lower limit
            self.signal = 1
            #violently access previous timestep to avoid oscillation between 0 and 1 (decision is based on decision of previous time step)
            self.previous_signal = 1
            
        stsv.set_output_value( self.stateC, self.signal )

    # def print_output(self, t_m, state):
    #     print("==========================================")
    #     print("T m: {}".format(t_m))
    #     print("State: {}".format(state))

