# Generic/Built-in
import numpy as np
import copy
import matplotlib
import seaborn
from math import pi
from typing import Union, List
from dataclasses import dataclass
from dataclasses_json import dataclass_json

# Owned
import hisim.component as cp
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim.components.loadprofilegenerator_connector import Occupancy
from hisim.components import controller_l1_generic_runtime
import hisim.log as log
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


@dataclass_json
@dataclass
class BoilerConfig:
    parameter_string: str
    volume : float
    surface : float
    u_value : float
    T_warmwater : float 
    T_drainwater : float 
    power : float 
    efficiency : float
    fuel : str

    def __init__( self,
                  my_simulation_parameters: SimulationParameters,
                  volume : float,
                  surface : float,
                  u_value : float,
                  T_warmwater : float, 
                  T_drainwater : float, 
                  power : float, 
                  efficiency : float,
                  fuel : str ) :
        self.parameter_string = my_simulation_parameters.get_unique_key()
        self.volume = volume
        self.surface = surface
        self.u = u_value
        self.T_warmwater = T_warmwater + 273.15
        self.T_drainwater = T_drainwater + 273.15
        self.power = power
        self.efficiency = efficiency
        self.fuel = fuel

class BoilerState:
    """
    This data class saves the state of the simulation results.
    """

    def __init__( self, timestep : int = -1, volume_in_l : float = 200, temperature_in_K : float = 273.15 + 50 ):
        """
        Parameters
        ----------
        timestep : int, optional
            Timestep of simulation. The default is 0. 
        volume_in_l : float
            Volume of boiler in liters.
        temperature_in_K : float
            Temperature of boiler in Kelvin.
        """
        self.timestep = timestep
        self.temperature_in_K = temperature_in_K
        self.volume_in_l = volume_in_l

    def clone(self):
        return BoilerState( self.timestep, self.volume_in_l, self.temperature_in_K)

    def energy_from_temperature( self ) -> float:
        "converts temperature of storage (K) into energy contained in storage (kJ)"
        #0.977 is the density of water in kg/l
        #4.182 is the specific heat of water in kJ / ( K * kg )
        return self.temperature_in_K * self.volume_in_l * 0.977 * 4.182 #energy given in kJ


    def set_temperature_from_energy( self, energy_in_kJ):
        "converts energy contained in storage (kJ) into temperature (K)"
        #0.977 is the density of water in kg/l
        #4.182 is the specific heat of water in kJ / ( K * kg )
        self.temperature_in_K = energy_in_kJ / ( self.volume_in_l * 0.977 * 4.182 ) #temperature given in K
    
class Boiler( cp.Component ):
    """
    Simple boiler implementation: energy bucket model, extract energy, add energy and convert back to temperatere
    
    Parameters
    ----------
    volume : float, optional
        Volume of storage in liters. The default is 200 l.
    surface : float, optional
        Surface of storage in square meters. The default is 1.2 m**3
    u_value : float, optional
        u-value of stroage in W m**(-2) K**(-1). The default is 0.36 W / (m*m*K)
    T_warmwater : float, optional
        Set temperature of hot water used by residents in °C. The default is 50 °C.
    T_drainwater : float, optional
        Temperature of cold water from pipe in °C. The default is 10 °C.
    power : float, optional
        Power of the heating rod/ hydrogen oven in W. The default is 2400.
    efficiency : float, optional
        Efficiency of the heating rod / hydrogen oven. The default is 1.
    fuel : str, optional
        Fuel of the boiler. Either "electricity" or "hydrogen". The default is "electricity".
    name : str, optional
        Name of boiler within simulation. The default is 'Boiler'.
    source_weight : int, optional
        Weight of component, relevant if there is more than one boiler, defines hierachy in control. The default is 1.
    """
    # Inputs
    l1_DeviceSignal = "l1_DeviceSignal"
    l1_RunTimeSignal = 'l1_RunTimeSignal'
    WaterConsumption = "WaterConsumption"

    # obligatory Outputs
    TemperatureMean = "TemperatureMean"

    #availble Outputs
    ElectricityOutput = "ElectricityOutput"
    HydrogenOutput = "HydrogenOutput"
    
    def __init__( self, my_simulation_parameters: SimulationParameters, 
                        volume : float = 200, 
                        surface : float = 1.2,
                        u_value : float = 0.36,
                        T_warmwater : float = 50,
                        T_drainwater : float = 10,
                        power : float = 2400,
                        efficiency : float = 1,
                        fuel : str = 'electricity',
                        source_weight : int = 1,
                        name : str = 'Boiler' ) :

        super().__init__( name + str( source_weight ), my_simulation_parameters = my_simulation_parameters )
        
        self.config = BoilerConfig( my_simulation_parameters = my_simulation_parameters,
                                    volume = volume,
                                    surface = surface,
                                    u_value = u_value,
                                    T_warmwater = T_warmwater, 
                                    T_drainwater = T_drainwater, 
                                    power = power, 
                                    efficiency = efficiency,
                                    fuel = fuel )
        self.build( source_weight )
        
        #inputs
        self.WaterConsumptionC : cp.ComponentInput = self.add_input( self.ComponentName,
                                                            self.WaterConsumption,
                                                            lt.LoadTypes.WarmWater,
                                                            lt.Units.Liter, 
                                                            mandatory = True )
        
        self.l1_DeviceSignalC : cp.ComponentInput = self.add_input( self.ComponentName,
                                                                    self.l1_DeviceSignal,
                                                                    lt.LoadTypes.OnOff,
                                                                    lt.Units.binary,
                                                                    mandatory = True )
        self.l1_RunTimeSignalC : cp.ComponentInput = self.add_input( self.ComponentName,
                                                                    self.l1_RunTimeSignal,
                                                                    lt.LoadTypes.Any,
                                                                    lt.Units.Any,
                                                                    mandatory = False )
        
        #Outputs
        self.TemperatureMeanC : cp.ComponentOutput = self.add_output( self.ComponentName,
                                                                         self.TemperatureMean,
                                                                         lt.LoadTypes.Temperature,
                                                                         lt.Units.Celsius )
        
        if self.config.fuel == 'electricity':
            self.electricity_output_c = self.add_output( self.ComponentName,
                                                         self.ElectricityOutput,
                                                         lt.LoadTypes.Electricity,
                                                         lt.Units.Watt )
        
        elif self.config.fuel == 'hydrogen':
            self.hydrogen_output_c = self.add_output( self.ComponentName,
                                                      self.HydrogenOutput,
                                                      lt.LoadTypes.Hydrogen,
                                                      lt.Units.kg_per_sec )
        else:
            raise Exception(" The fuel ", str( self.config.fuel ), " is not available. Choose either 'electricity' or 'hydrogen'. " )
            
        self.add_default_connections( Occupancy, self.get_occupancy_default_connections( ) )
        self.add_default_connections( controller_l1_generic_runtime.L1_Controller, self.get_l1_controller_default_connections( ) )
        
    def get_occupancy_default_connections( self ):
        log.information("setting occupancy default connections in dhw boiler" )
        connections = [ ]
        occupancy_classname = Occupancy.get_classname( )
        connections.append( cp.ComponentConnection( Boiler.WaterConsumption, occupancy_classname, Occupancy.WaterConsumption ) )
        return connections
    
    def get_l1_controller_default_connections( self ):
        log.information( "setting l1 default connections in dhw boiler"  )
        connections = [ ]
        controller_classname = controller_l1_generic_runtime.L1_Controller.get_classname( )
        connections.append( cp.ComponentConnection( Boiler.l1_DeviceSignal, controller_classname, controller_l1_generic_runtime.L1_Controller.l1_DeviceSignal ) )
        connections.append( cp.ComponentConnection( Boiler.l1_RunTimeSignal, controller_classname, controller_l1_generic_runtime.L1_Controller.l1_RunTimeSignal ) )
        return connections
    
    def build( self, source_weight ):
        
        self.source_weight = source_weight
        
        #initialize Boiler State
        self.state = BoilerState( volume_in_l = self.config.volume, temperature_in_K = 273 + 50 )
        self.previous_state = self.state.clone()
            
        self.write_to_report( )

    def write_to_report(self):
        lines = []
        lines.append("Name: {}".format("electric Boiler"))
        lines.append("Power: {:4.0f} kW".format( ( self.config.power ) * 1E-3 ) )
        lines.append( "Volume: {:4.0f} l".format( self.config.volume ) )
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
        signal = stsv.get_input_value( self.l1_DeviceSignalC )

        #constant heat loss of heat storage with the assumption that environment has 20°C = 293 K -> based on energy balance in kJ
        #heat loss due to hot water consumption -> base on energy balance in kJ
        #heat gain due to electrical/hydrogen heating of boiler -> based on energy balance in kJ
        #0.977 density of water in kg/l
        #4.182 specific heat of water in kJ K^(-1) kg^(-1)
        #1e-3 conversion J to kJ
        energy = self.state.energy_from_temperature()
        new_energy = energy - ( self.state.temperature_in_K - 293 ) * self.config.surface * self.config.u * self.my_simulation_parameters.seconds_per_timestep * 1e-3 \
                                              - WW_consumption * ( self.config.T_warmwater - self.config.T_drainwater ) * 0.977 * 4.182 \
                                              + signal * self.config.power * self.config.efficiency * self.my_simulation_parameters.seconds_per_timestep * 1e-3
                                              
        #convert new energy to new temperature
        self.state.set_temperature_from_energy( new_energy )
        
        #save outputs
        stsv.set_output_value( self.TemperatureMeanC, self.state.temperature_in_K - 273.15 )
        
        if self.config.fuel == 'electricity':
            stsv.set_output_value( self.electricity_output_c, self.config.power * signal )
              
            #put forecast into dictionary
            if self.my_simulation_parameters.system_config.predictive:
                #only in first timestep
                if self.state.timestep + 1 == timestep:
                    self.state.timestep += 1
                    self.previous_state.timestep += 1
                    runtime = stsv.get_input_value( self.l1_RunTimeSignalC )
                    self.simulation_repository.set_dynamic_entry( component_type = lt.ComponentType.Boiler, source_weight = self.source_weight, entry = [ self.config.power ] * runtime )
        else:
            #heat of combustion hydrogen: 141.8 MJ / kg; conversion W = J/s to kg / s
            stsv.set_output_value( self.hydrogen_output_c, ( self.config.power * signal / 1.418 ) * 1e-8 )

# class ControllerState:
#     """
#     This data class saves the state of the controller.
#     """

#     def __init__( self, state : int = 0, timestep_of_last_action : int = -999 ):
#         self.state = state
#         self.timestep_of_last_action = timestep_of_last_action
        
#     def clone( self ):
#         return ControllerState( state = self.state, timestep_of_last_action = self.timestep_of_last_action )

# class BoilerController(cp.Component):
#     """
#     Boiler on/off controller. It activates boiler, when temperature falls below
#     certain threshold and deactivates it if temperature exceeds limit.

#     Parameters
#     --------------
#     t_water_min: float
#         Minimum temperature of water in hot water storage -> in Kelvin
#     t_water_max: float
#         Maximum temperature of water in hot water storage -> in Kelvin
#     P_on : float
#         Power of boiler -> in Watt
#     smart : bool
#         One if boiler enforces heating in case of surplus from PV and 0 if it does not react to the situaltion.
#     """
#     # Inputs
#     TemperatureMean = "Storage Temperature"
#     BoilerSignal = "BoilerSignal"

#     # Outputs
#     BoilerControllerState = "BoilerControllerState"
    
#     #Forecasts
#     BoilerLoadForecast = "BoilerLoadForecast"

    # # Similar components to connect to:
    # # 1. Building

    # def __init__( self, my_simulation_parameters: SimulationParameters,
    #                     t_water_min : float = 273.0 + 40.0,
    #                     t_water_max : float = 273.0 + 80.0,
    #                     P_on :        int =   2400,
    #                     on_time :   int = 2700,
    #                     off_time :  int = 1800 ):
        
    #     super().__init__( name="BoilerController", my_simulation_parameters = my_simulation_parameters )
        
    #     self.build( t_water_min = t_water_min,
    #                 t_water_max = t_water_max,
    #                 P_on = P_on,
    #                 on_time = on_time,
    #                 off_time = off_time )

    #     #input
    #     self.TemperatureMeanC: cp.ComponentInput = self.add_input( self.ComponentName,
    #                                                                   self.TemperatureMean,
    #                                                                   lt.LoadTypes.Temperature,
    #                                                                   lt.Units.Kelvin,
    #                                                                   mandatory = True )
    #     if self.my_simulation_parameters.system_config.predictive and self.my_simulation_parameters.system_config.boiler_included == 'electricity':
    #         self.BoilerSignalC: cp.ComponentInput = self.add_input( self.ComponentName,
    #                                                                 self.BoilerSignal,
    #                                                                 lt.LoadTypes.Any,
    #                                                                 lt.Units.Any,
    #                                                                 mandatory = False )
    #         self.add_default_connections( controller_l3_predictive.PredictiveController, self.get_predictive_controller_default_connections( ) )
        
        
    #     #output
    #     self.BoilerControllerStateC = self.add_output( self.ComponentName,
    #                                                     self.BoilerControllerState,
    #                                                     lt.LoadTypes.Any,
    #                                                     lt.Units.Any )
        
    #     self.add_default_connections( Boiler, self.get_boiler_default_connections( ) )
    
    # def get_boiler_default_connections( self ):
    #     log.information("setting boiler default connections")
    #     connections = [ ]
    #     boiler_classname = Boiler.get_classname( )
    #     connections.append( cp.ComponentConnection( BoilerController.TemperatureMean, boiler_classname, Boiler.TemperatureMean ) )
    #     return connections
    
    # def get_predictive_controller_default_connections( self ):
    #     log.information( "setting predictive controller default connections") 
    #     connections = [ ]
    #     predictive_controller_classname = controller_l3_predictive.PredictiveController.get_classname( )
    #     connections.append( cp.ComponentConnection( BoilerController.BoilerSignal, predictive_controller_classname, 
    #                                                 controller_l3_predictive.PredictiveController.BoilerSignal ) )
    #     return connections

    # def build( self, t_water_min, t_water_max, P_on, on_time, off_time ):
        
    #     # state
    #     self.state = ControllerState( )
    #     self.previous_state = ControllerState( )

    #     # Configuration
    #     self.t_water_min = t_water_min
    #     self.t_water_max = t_water_max
    #     self.P_on = P_on
    #     self.on_time = int( on_time / self.my_simulation_parameters.seconds_per_timestep )
    #     self.off_time = int( off_time / self.my_simulation_parameters.seconds_per_timestep )
        
    # def activation( self, timestep ):
    #     self.state.state = 2
    #     self.state.timestep_of_last_action = timestep
    #     #violently access previous timestep to avoid oscillation between 0 and 1 (decision is based on decision of previous time step)
    #     self.previous_state = self.state.clone( )

    # def deactivation( self, timestep ):
    #     self.state.state = -2
    #     self.state.timestep_of_last_action = timestep 
    #     #violently access previous timestep to avoid oscillation between 0 and 1 (decision is based on decision of previous time step)
    #     self.previous_state = self.state.clone( )

    # def i_save_state(self):
    #     self.previous_state = self.state.clone( )

    # def i_restore_state(self):
    #     self.state = self.previous_state.clone( )

    # def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
    #     pass

    # def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues,  force_convergence: bool):

    #     if force_convergence: # please stop oscillating!
    #         return
        
    #     if ( self.state.state == 2 and timestep < self.state.timestep_of_last_action + self.on_time ) or \
    #           ( self.state.state == -2 and timestep < self.state.timestep_of_last_action + self.off_time ):
    #         pass
    #     else:
    #         # Retrieves inputs
    #         T_control = stsv.get_input_value( self.TemperatureMeanC )
    
    #         #on off control based on temperature limits
    #         if T_control > self.t_water_max:
    #             #stop heating if temperature exceeds upper limit
    #             if self.state.state >= 0:
    #                 self.deactivation( timestep )

    #         elif T_control < self.t_water_min:
    #             #start heating if temperature goes below lower limit
    #             if self.state.state <= 0:
    #                 self.activation( timestep )
             
    #         #continue working if other is not defined    
    #         else:
    #             if self.state.state > 0:
    #                 self.state.state = 1
    #             if self.state.state < 0:
    #                 self.state.state = -1
        
    #         if self.my_simulation_parameters.system_config.predictive and self.my_simulation_parameters.system_config.boiler_included == 'electricity':
    #             #put forecast into dictionary
    #             if self.state.state > 0:
    #                 self.simulation_repository.set_entry( self.BoilerLoadForecast, [ self.P_on ] * max( 1, self.on_time - timestep + self.state.timestep_of_last_action ) )
    #             else:
    #                 self.simulation_repository.set_entry( self.BoilerLoadForecast, [ self.P_on ] * self.on_time )
                    
    #             #read in signal and modify state if recommended
    #             devicesignal = stsv.get_input_value( self.BoilerSignalC )
    #             if self.state.state == 1 and devicesignal == -1:
    #                 self.deactivation( timestep )
                    
    #             elif self.state.state == -1 and devicesignal == 1:
    #                 self.activation( timestep )
                 
    #     stsv.set_output_value( self.BoilerControllerStateC, self.state.state )     

    # # def log_output(self, t_m, state):
    # #     log.information("==========================================")
    # #     log.information("T m: {}".format(t_m))
    # #     log.information("State: {}".format(state))

